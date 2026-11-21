"""Independent Ethernet utility. NetworkManager retains ownership of the NIC.

No Link API, namespaces, DSP, presets, firmware or existing service control.
Startup adopts the existing connection without a network write. Only a user's
saved On/Off choice authorizes subsequent targeted NetworkManager operations.
"""
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import threading
import time

SOCKET = '/run/hector-ethernet-panel/control.sock'
STATE = Path('/mnt/community-lab/ethernet-panel/private/preference.json')
DRIVERS = {'r8152', 'ax88179_178a', 'cdc_ncm', 'asix'}


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=18,
                            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
    if result.returncode:
        raise RuntimeError('network-operation-failed')
    return result.stdout.strip()


def text(path, default=''):
    try:
        return path.read_text().strip()
    except OSError:
        return default


def discover(root=Path('/sys/class/net'), run=command):
    addresses = json.loads(run(['/sbin/ip', '-j', 'address', 'show']))
    addresses = {r['ifname']: r for r in addresses}
    rows = []
    for interface in sorted(root.iterdir()):
        name = interface.name
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', name):
            continue
        if (interface / 'wireless').exists() or text(interface / 'type') != '1':
            continue
        device = (interface / 'device').resolve()
        driver = (device / 'driver').resolve().name
        if driver not in DRIVERS:
            continue
        usb = next((p for p in [device] + list(device.parents)
                    if (p / 'idVendor').is_file() and (p / 'idProduct').is_file()), None)
        if usb is None:
            continue
        raw = run(['/usr/bin/nmcli', '-g',
                   'GENERAL.STATE,GENERAL.NM-MANAGED,GENERAL.AUTOCONNECT',
                   'device', 'show', name]).splitlines()
        if len(raw) != 3:
            raise RuntimeError('network-status-unavailable')
        state = int(raw[0].split()[0])
        managed, autoconnect = raw[1] == 'yes', raw[2] == 'yes'
        carrier = text(interface / 'carrier') == '1'
        ips = [r['local'] for r in addresses.get(name, {}).get('addr_info', [])
               if r.get('family') == 'inet' and r.get('scope') == 'global']
        mac = text(interface / 'address').lower()
        usb_id = text(usb / 'idVendor') + ':' + text(usb / 'idProduct')
        label = 'TBI Pro hub · Realtek RTL8153' if usb_id == '0bda:8153' else text(usb / 'product', 'USB Ethernet')[:80]
        status = ('Not managed' if not managed else 'Connected' if state == 100 and ips
                  else 'Cable disconnected' if not carrier else 'Obtaining IP address' if state in range(40, 100)
                  else 'Ready to connect')
        rows.append(dict(interface=name, name=label, mac=mac, ip=', '.join(ips),
                         status=status, carrier=carrier, state=state, managed=managed,
                         autoconnect=autoconnect, driver=driver, usb_id=usb_id,
                         identity=str(device) + '|' + mac + '|' + usb_id))
    return rows


class Controller:
    def __init__(self, state=STATE, scan=discover, run=command):
        self.state_file, self.scan, self.run = state, scan, run
        self.lock = threading.Lock()
        self.enabled, self.owned = True, False
        if state.exists():
            preference = json.loads(state.read_text())
            if set(preference) != {'enabled'} or type(preference['enabled']) is not bool:
                raise RuntimeError('invalid-preference')
            self.enabled, self.owned = preference['enabled'], True
        self.data = dict(online=False, enabled=self.enabled, busy=False, adapters=[], error='')
        self.pending = False
        self.applying = False
        self.generation = 0
        self.attempts = {}

    def request(self, value):
        if value == {'action': 'status'}:
            with self.lock:
                return dict(ok=True, data=dict(self.data))
        if (not isinstance(value, dict) or set(value) != {'action', 'enabled'}
                or value['action'] != 'set' or type(value['enabled']) is not bool):
            return dict(ok=False, error='invalid-request')
        with self.lock:
            if self.data['busy'] or self.applying:
                return dict(ok=False, error='busy')
            self.state_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = self.state_file.with_suffix('.new')
            fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            try:
                with os.fdopen(fd, 'w') as stream:
                    json.dump({'enabled': value['enabled']}, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(str(temporary), str(self.state_file))
            finally:
                if temporary.exists():
                    temporary.unlink()
            self.enabled, self.owned, self.pending = value['enabled'], True, True
            self.generation += 1
            self.attempts.clear()
            self.data.update(enabled=self.enabled, busy=True, error='')
        return dict(ok=True, data={'accepted': True})

    def refresh(self):
        generation = None
        try:
            rows = self.scan()
            with self.lock:
                enabled, owned, pending = self.enabled, self.owned, self.pending
                generation = self.generation
                self.applying = owned
            if owned:
                rechecked = False
                for row in rows:
                    if not row['managed']:
                        continue
                    name = row['interface']
                    if enabled:
                        change = not row['autoconnect'] or (row['carrier'] and row['state'] == 30)
                    else:
                        change = row['autoconnect'] or 40 <= row['state'] <= 100
                    if not change:
                        continue
                    now = time.monotonic()
                    if not pending and now - self.attempts.get(row['identity'], -100) < 15:
                        continue
                    # Re-enumerate before any write; a replug must not redirect an action.
                    rechecked = True
                    current = next((r for r in self.scan() if r['identity'] == row['identity']
                                    and r['interface'] == name and r['managed']), None)
                    if current is None:
                        continue
                    self.attempts[row['identity']] = now
                    if enabled:
                        if not current['autoconnect']:
                            self.run(['/usr/bin/nmcli', '--wait', '10', 'device', 'set', name, 'autoconnect', 'yes'])
                        if current['carrier'] and current['state'] == 30:
                            self.run(['/usr/bin/nmcli', '--wait', '10', 'device', 'connect', name])
                    else:
                        # Device disconnect also blocks automatic reactivation.
                        if 40 <= current['state'] <= 100:
                            self.run(['/usr/bin/nmcli', '--wait', '10', 'device', 'disconnect', name])
                        elif current['autoconnect']:
                            self.run(['/usr/bin/nmcli', '--wait', '10', 'device', 'set', name, 'autoconnect', 'no'])
                if rechecked:
                    rows = self.scan()
            public = [{k: v for k, v in r.items() if k != 'identity'} for r in rows]
            if not enabled:
                for row in public:
                    if row['managed'] and row['state'] <= 30 and not row['autoconnect']:
                        row['status'] = 'Off'
            with self.lock:
                if generation == self.generation:
                    self.pending = False
                    self.data.update(online=True, busy=False, adapters=public, error='')
        except Exception:
            with self.lock:
                if generation is None or generation == self.generation:
                    self.pending = False
                    self.data.update(online=False, busy=False, error='Could not confirm the Ethernet state. Try again.')
        finally:
            with self.lock:
                self.applying = False


def main():
    controller = Controller()
    path = Path(SOCKET)
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    os.chown(str(path.parent), 0, 1000)
    os.chmod(str(path.parent), 0o750)
    os.chown(str(path), 0, 1000)
    os.chmod(str(path), 0o660)
    server.listen(4)
    server.settimeout(0.5)
    def worker():
        while True:
            controller.refresh()
            time.sleep(1.5)
    threading.Thread(target=worker, daemon=True).start()
    while True:
        try:
            peer, _ = server.accept()
        except socket.timeout:
            continue
        with peer:
            peer.settimeout(1)
            try:
                _, uid, _ = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid not in (0, 1000):
                    raise ValueError('unauthorized')
                raw = b''
                while b'\n' not in raw and len(raw) <= 1024:
                    chunk = peer.recv(1024)
                    if not chunk:
                        break
                    raw += chunk
                if len(raw) > 1024:
                    raise ValueError('oversize')
                reply = controller.request(json.loads(raw))
            except Exception:
                reply = dict(ok=False, error='service-unavailable')
            try:
                peer.sendall(json.dumps(reply).encode() + b'\n')
            except OSError:
                pass


if __name__ == '__main__':
    main()
