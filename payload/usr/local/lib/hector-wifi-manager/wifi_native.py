"""GPL-2.0-or-later. Native AR9271 adapter behind the existing Wi-Fi screen API."""
import ipaddress
import json
from pathlib import Path
import re
import threading
import time
import uuid
from gi.repository import Gio, GLib

BASE = '/org/freedesktop/NetworkManager'
NM = 'org.freedesktop.NetworkManager'
DEVICE = NM+'.Device'
WIRELESS = DEVICE+'.Wireless'
CONNECTION = NM+'.Settings.Connection'
PREFIX = 'hector-ar9271-'


class Native:
    def __init__(self, core, inventory, saved, bus=None):
        self.c, self.inventory, self.saved = core, inventory, saved
        self.bus = bus or Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.busy, self.phase, self.error = False, 'ready', ''
        self.scanned = 0.
        self.networks, self.aps = [], {}
        self.created = set()
        self.restore_attempted = False

    def call(self, path, interface, method, parameters=None):
        return self.bus.call_sync(NM, path, interface, method, parameters, None,
                                 Gio.DBusCallFlags.NONE, 3500, None).unpack()

    def props(self, path, interface):
        return self.call(path, 'org.freedesktop.DBus.Properties', 'GetAll',
                         GLib.Variant('(s)', (interface,)))[0]

    def adapters(self):
        result = []
        for row in self.inventory():
            name = row['interface']
            if not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', name):
                continue
            p = Path('/sys/class/net')/name
            try:
                dev = (p/'device').resolve(strict=True)
                usb = next(q for q in dev.parents if (q/'idVendor').exists())
                identity = ((usb/'idVendor').read_text().strip(),
                            (usb/'idProduct').read_text().strip(),
                            (p/'device/driver').resolve(strict=True).name)
                if identity != ('0cf3', '9271', 'ath9k_htc'):
                    continue
            except (OSError, StopIteration):
                continue
            result.append(dict(row, name='AR9271 · 2.4 GHz', supported=True))
        return result

    def available(self):
        return bool(self.adapters()) or self.busy

    def device(self):
        rows = self.adapters()
        if len(rows) != 1:
            raise self.c.WifiError('unsupported-adapter')
        row = rows[0]
        path = self.call(BASE, NM, 'GetDeviceByIpIface',
                         GLib.Variant('(s)', (row['interface'],)))[0]
        return row, path

    def remembered(self):
        value = self.c.private_json(self.saved)
        if value is None:
            return None
        if set(value) != {'schema', 'profile'} or value['schema'] != 1:
            raise self.c.WifiError('invalid-state')
        return self.c.validate_profile(value['profile'])

    def scan_results(self, device):
        paths = self.call(device, WIRELESS, 'GetAllAccessPoints')[0]
        lines = ['bssid / frequency / signal level / flags / ssid']
        aps = {}
        for path in paths[:128]:
            a = self.props(path, NM+'.AccessPoint')
            if not 2400 <= a['Frequency'] < 2500:
                continue
            address = a['HwAddress'].lower()
            raw = bytes(a['Ssid'])
            if not raw or not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', address):
                continue
            # The first native-adapter flow supports WPA2 personal and open.
            # Mixed WPA2/WPA3 APs use their WPA2 path; SAE-only APs stay disabled.
            security = int(a['RsnFlags'])
            flags = '[WPA2-PSK-CCMP][ESS]' if security & 0x100 else (
                '[RSN-UNSUPPORTED][ESS]' if int(a['Flags']) & 1 else '[ESS]')
            signal = round(int(a['Strength'])/2-100)
            escaped = ''.join('\\x%02x' % n for n in raw)
            lines.append('%s\t%d\t%d\t%s\t%s' %
                         (address, a['Frequency'], signal, flags, escaped))
            aps[address] = path
        self.networks = self.c.networks('\n'.join(lines))
        self.aps = aps

    def status(self):
        rows = self.adapters()
        connection = {}
        if len(rows) == 1:
            row, device = self.device()
            d = self.props(device, DEVICE)
            connection = dict(adapter=row['mac'], wpa_state='DISCONNECTED')
            if self.scanned:
                self.scan_results(device)
            if int(d['State']) == 100:
                active = self.props(device, WIRELESS).get('ActiveAccessPoint')
                a = self.props(active, NM+'.AccessPoint') if active and active != '/' else {}
                ip4 = self.props(d['Ip4Config'], NM+'.IP4Config')
                addresses = [v['address'] for v in ip4.get('AddressData', [])
                             if ipaddress.IPv4Address(v['address']).is_private]
                connection.update(wpa_state='COMPLETED', ssid=bytes(a.get('Ssid', [])).decode('utf-8', 'replace'),
                                  bssid=a.get('HwAddress', '').lower(), freq=a.get('Frequency'),
                                  ip_address=addresses[0] if len(addresses) == 1 else '')
        remembered = self.remembered()
        return dict(online=True, native_nm=True, phase=self.phase, error=self.error,
                    busy=self.busy, adapters=rows, networks=self.networks,
                    scan_age=round(time.monotonic()-self.scanned) if self.scanned else None,
                    remembered=self.c.public_profile(remembered), connection=connection)

    def radio(self, enabled):
        current = self.props(BASE, NM)['WirelessEnabled']
        if bool(current) != enabled:
            self.call(BASE, 'org.freedesktop.DBus.Properties', 'Set',
                      GLib.Variant('(ssv)', (NM, 'WirelessEnabled', GLib.Variant('b', enabled))))
        if not enabled:
            self.restore_attempted = False
        elif not self.restore_attempted and not self.created:
            self.restore_attempted = True
            saved = self.remembered()
            if saved:
                row, _ = self.device()
                self.start(dict(saved, adapter=row['mac']), False)

    def dispatch(self, request):
        if not isinstance(request, dict):
            raise self.c.WifiError('invalid-request')
        action = request.get('action')
        if request == {'action': 'status'}:
            return self.status()
        if request == {'action': 'cancel'}:
            self.cancel.set()
            return dict(accepted=True)
        if set(request) == {'action', 'enabled'} and action == 'radio' and type(request['enabled']) is bool:
            if not self.busy:
                self.radio(request['enabled'])
            return dict(accepted=True)
        if self.busy:
            raise self.c.WifiError('busy')
        row, device = self.device()
        if request == {'action': 'scan'}:
            try:
                self.call(device, WIRELESS, 'RequestScan', GLib.Variant('(a{sv})', ({},)))
            except GLib.Error:
                pass  # A scan already in flight leaves its cached APs usable.
            self.scanned = time.monotonic()
            self.scan_results(device)
            return self.status()
        if action == 'adapter' and set(request) == {'action', 'adapter'}:
            if request['adapter'] != row['mac']:
                raise self.c.WifiError('unsupported-adapter')
            return dict(accepted=True)
        if (action != 'connect' or set(request) != {'action', 'adapter', 'network', 'password', 'mode', 'remember'}
                or type(request['remember']) is not bool or request['adapter'] != row['mac']):
            raise self.c.WifiError('invalid-request')
        network = next((n for n in self.networks if n['id'] == request['network']), None)
        if network is None:
            raise self.c.WifiError('scan-again')
        value = self.c.profile(row['mac'], network, request['password'], request['mode'])
        request['password'] = ''
        self.start(value, request['remember'])
        return dict(accepted=True)

    def start(self, value, remember):
        with self.lock:
            if self.busy:
                raise self.c.WifiError('busy')
            self.busy, self.phase, self.error = True, 'connecting', ''
            self.cancel.clear()
        threading.Thread(target=self.connect, args=(value, remember), daemon=True).start()

    def wait(self, device, active, seconds, cancellable=True):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            if cancellable and self.cancel.is_set():
                raise self.c.WifiError('cancelled')
            properties = self.props(device, DEVICE)
            state = int(properties['State'])
            if state == 100 and properties.get('ActiveConnection') == active:
                return
            if state == 120:
                break
            time.sleep(.4)
        raise self.c.WifiError('join-failed')

    def connect(self, value, remember):
        profile, active, old_connection = None, None, None
        try:
            row, device = self.device()
            assert row['mac'] == value['adapter']
            old_active = self.props(device, DEVICE).get('ActiveConnection', '/')
            if old_active != '/':
                old_connection = self.props(old_active, NM+'.Connection.Active').get('Connection')
            token = uuid.uuid4().hex
            v = GLib.Variant
            settings = {
                'connection': {'id': v('s', PREFIX+token), 'uuid': v('s', str(uuid.uuid4())),
                               'type': v('s', '802-11-wireless'), 'interface-name': v('s', row['interface']),
                               'autoconnect': v('b', True)},
                '802-11-wireless': {'ssid': v('ay', list(bytes.fromhex(value['ssid_hex']))),
                                     'mode': v('s', 'infrastructure'), 'band': v('s', 'bg')},
                'ipv4': {'method': v('s', 'auto'), 'never-default': v('b', False),
                         'ignore-auto-dns': v('b', False), 'route-metric': v('x', 700)},
                'ipv6': {'method': v('s', 'ignore')}}
            if value['security'] == 'psk':
                settings['802-11-wireless-security'] = {'key-mgmt': v('s', 'wpa-psk'),
                                                       'psk': v('s', value['psk']), 'proto': v('as', ['rsn'])}
            elif value['security'] != 'open':
                raise self.c.WifiError('unsupported-security')
            profile = self.call(BASE+'/Settings', NM+'.Settings', 'AddConnectionUnsaved',
                                v('(a{sa{sv}})', (settings,)))[0]
            self.created.add(profile)
            assert self.props(profile, CONNECTION)['Unsaved']
            specific = self.aps.get(value['bssid'], '/')
            active = self.call(BASE, NM, 'ActivateConnection', v('(ooo)', (profile, device, specific)))[0]
            self.wait(device, active, 35)
            # Only commit the private saved choice after association and DHCP.
            if remember:
                self.c.atomic(self.saved, dict(schema=1, profile=value))
            for previous in list(self.created-{profile}):
                try:
                    self.call(previous, CONNECTION, 'Delete')
                    self.created.discard(previous)
                except GLib.Error:
                    pass
            self.phase, self.error = 'connected', ''
        except Exception as error:
            self.phase, self.error = 'restoring', error.code if isinstance(error, self.c.WifiError) else 'join-failed'
            if active:
                try:
                    self.call(BASE, NM, 'DeactivateConnection', GLib.Variant('(o)', (active,)))
                except Exception:
                    pass
            if profile:
                try:
                    self.call(profile, CONNECTION, 'Delete')
                    self.created.discard(profile)
                except Exception:
                    pass
            if old_connection:
                try:
                    restored = self.call(BASE, NM, 'ActivateConnection', GLib.Variant('(ooo)', (old_connection, device, '/')))[0]
                    self.wait(device, restored, 25, cancellable=False)
                except Exception:
                    self.error = 'recovery-needed'
            self.phase = 'restored'
        finally:
            with self.lock:
                self.busy = False

    def restore_saved(self):
        def restore():
            for _ in range(30):
                if self.adapters():
                    try:
                        saved = self.remembered()
                        preference = self.c.private_json(Path('/mnt/community-lab/network-connection/private/wifi.json'))
                        enabled = preference is None or preference.get('enabled') is True
                        if saved and enabled and self.props(BASE, NM)['WirelessEnabled']:
                            row, _ = self.device()
                            saved = dict(saved, adapter=row['mac'])
                            self.restore_attempted = True
                            self.start(saved, False)
                    except Exception:
                        pass
                    return
                time.sleep(1)
        threading.Thread(target=restore, daemon=True).start()
