"""GPL-2.0-or-later. Data and recovery boundary for Hector Wi-Fi Setup."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import time
import uuid

SCHEMA = 'hector-wifi/1'
DATA = Path('/mnt/community-lab/wifi-manager')
RUNTIME = Path('/run/hector-wifi-manager')
NAMESPACE = 'hector-mala-trial'
MAC = re.compile(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}')
INTERFACE = re.compile(r'[a-zA-Z0-9_-]{1,15}')


class WifiError(Exception):
    """Only a fixed code crosses the privileged/UI boundary."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def mac(value):
    if not isinstance(value, str) or not MAC.fullmatch(value) or int(value[:2], 16) & 1 or value == '00:00:00:00:00:00':
        raise WifiError('invalid-adapter')
    return value


def ssid_bytes(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:[0-9a-f]{2}){1,32}', value):
        raise WifiError('invalid-network')
    return bytes.fromhex(value)


def label(value):
    # SSIDs are bytes, not code/markup; all QML consumers use PlainText.
    return ''.join(c if c.isprintable() else '\ufffd' for c in ssid_bytes(value).decode('utf-8', 'replace'))


def decode_ssid(value):
    """Decode wpa_supplicant's printf-escaped SSIDs without Unicode guessing."""
    result = bytearray()
    index = 0
    while index < len(value):
        if value[index] == '\\' and index + 1 < len(value):
            if value[index + 1] == 'x' and index + 3 < len(value) and re.fullmatch('[0-9a-fA-F]{2}', value[index + 2:index + 4]):
                result.append(int(value[index + 2:index + 4], 16)); index += 4; continue
            escapes = {'n': 10, 'r': 13, 't': 9, 'e': 27, '\\': 92, '"': 34}
            if value[index + 1] in escapes:
                result.append(escapes[value[index + 1]]); index += 2; continue
        result.extend(value[index].encode('utf-8')); index += 1
    return bytes(result)


def networks(raw):
    """One row per SSID/authentication family; keep compatible radio choices."""
    found = {}
    for row in raw.splitlines()[1:]:
        parts = row.split('\t', 4)
        if len(parts) != 5:
            continue
        address, frequency, signal, flags, name = parts
        try:
            mac(address)
            frequency, signal = int(frequency), int(signal)
            name = decode_ssid(name)
            if not 1 <= len(name) <= 32 or not 2300 <= frequency <= 7125:
                continue
        except (WifiError, ValueError):
            continue
        security = 'enterprise' if 'EAP' in flags else 'sae' if 'SAE' in flags else 'psk' if 'PSK' in flags else 'unsupported' if any(x in flags for x in ['WEP', 'OWE', 'WPA', 'RSN']) else 'open'
        modes = (['auto', 'psk', 'sae'] if 'SAE' in flags and 'PSK' in flags else
                 ['auto', 'sae'] if security == 'sae' else ['auto', 'psk'] if security == 'psk' else ['auto'])
        family = 'personal' if security in ['psk', 'sae'] else security
        key = (name.hex(), family)
        item = dict(id=name.hex() + ':' + security, ssid_hex=name.hex(), name=label(name.hex()),
                    bssid=address, frequency=frequency, signal=signal, security=security, modes=modes,
                    supported=security not in ['enterprise', 'unsupported'], access_points=1)
        radio = {k: item[k] for k in ['bssid', 'frequency', 'signal', 'security', 'modes', 'supported']}
        group = found.setdefault(key, dict(ssid_hex=name.hex(), name=label(name.hex()), radios={}))
        previous = group['radios'].get(address)
        if previous is None or radio['signal'] > previous['signal']:
            group['radios'][address] = radio
    rows = []
    for (ssid, family), group in found.items():
        radios = sorted(group['radios'].values(), key=lambda x: (-x['signal'], x['bssid']))
        strongest = radios[0]
        # Keep the best radio for every explicit mode, even when it is weaker
        # than the first few radios. Never offer a mode using an incompatible AP.
        retained = {r['bssid']: r for r in radios[:6]}
        for mode in ['psk', 'sae']:
            choice = next((r for r in radios if mode in r['modes']), None)
            if choice is not None: retained[choice['bssid']] = choice
        choices = sorted(retained.values(), key=lambda x: (-x['signal'], x['bssid']))
        modes = ['auto'] + [m for m in ['psk', 'sae'] if any(m in r['modes'] for r in choices)]
        rows.append(dict(strongest, id=ssid + ':' + family, ssid_hex=ssid, name=group['name'],
                         modes=modes, access_points=len(radios), radios=choices))
    # Keep the public RPC response bounded, including optional radio details.
    result, used = [], 2
    for row in sorted(rows, key=lambda x: (-x['signal'], x['name'], x['id']))[:128]:
        size = len(json.dumps(row, ensure_ascii=True).encode()) + 2
        if used + size > 96 * 1024: break
        result.append(row); used += size
    return result


def profile(adapter, network, password, mode='auto'):
    adapter = mac(adapter)
    ssid = ssid_bytes(network['ssid_hex'])
    if mode not in network['modes'] or not network['supported']:
        raise WifiError('unsupported-security')
    radios = network.get('radios', [network])
    compatible = [r for r in radios if r['supported'] and (mode == 'auto' or mode in r['modes'])]
    if not compatible:
        raise WifiError('unsupported-security')
    radio = min(compatible, key=lambda r: (-r['signal'], r['bssid'])) if 'radios' in network else compatible[0]
    address = mac(radio['bssid'])
    security = radio['security'] if mode == 'auto' else mode
    if security not in ['open', 'psk', 'sae']:
        raise WifiError('unsupported-security')
    if not isinstance(password, str) or any(ord(c) < 32 or ord(c) == 127 for c in password):
        raise WifiError('invalid-password')
    raw = password.encode('utf-8')
    if security != 'open' and not (1 if security == 'sae' else 8) <= len(raw) <= 63:
        raise WifiError('password-length')
    result = dict(schema=SCHEMA, adapter=adapter, ssid_hex=ssid.hex(), bssid=address, security=security)
    if security == 'psk':
        result['psk'] = hashlib.pbkdf2_hmac('sha1', raw, ssid, 4096, 32).hex()
    elif security == 'sae':
        result['sae_password'] = password  # Private root-owned remembered network; never an RPC reply.
    return validate_profile(result)


def validate_profile(value):
    if not isinstance(value, dict):
        raise WifiError('invalid-profile')
    expected = {'schema', 'adapter', 'ssid_hex', 'bssid', 'security'}
    security = value.get('security')
    expected |= {'psk'} if security == 'psk' else {'sae_password'} if security == 'sae' else set()
    if value.get('schema') != SCHEMA or set(value) != expected or security not in ['open', 'psk', 'sae']:
        raise WifiError('invalid-profile')
    mac(value['adapter']); mac(value['bssid']); ssid_bytes(value['ssid_hex'])
    if security == 'psk' and (not isinstance(value['psk'], str) or not re.fullmatch('[0-9a-f]{64}', value['psk'])):
        raise WifiError('invalid-profile')
    if security == 'sae' and (not isinstance(value['sae_password'], str)
            or not 1 <= len(value['sae_password'].encode()) <= 63
            or any(ord(c) < 32 or ord(c) == 127 for c in value['sae_password'])):
        raise WifiError('invalid-profile')
    return dict(value)


def public_profile(value):
    if value is None:
        return None
    validate_profile(value)
    return {k: value[k] for k in ['adapter', 'ssid_hex', 'bssid', 'security']}


def quoted(value):
    if '\0' in value or '\n' in value or '\r' in value:
        raise WifiError('invalid-password')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def atomic(path, value, uid=0):
    path = Path(path)
    protected_parents(path.parent, uid)
    if path.exists() or path.is_symlink():
        private_json(path, uid=uid)
    raw = json.dumps(value, ensure_ascii=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'
    if len(raw) > 65536:
        raise WifiError('state-too-large')
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(raw); file.flush(); os.fsync(file.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def private_json(path, default=None, uid=0):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return default
    with os.fdopen(fd, 'rb') as file:
        info = os.fstat(file.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 65536:
            raise WifiError('unprotected-state')
        return json.load(file)


def protected_parents(path, uid=0):
    for part in (path, *path.parents):
        info = part.lstat()
        # A private fixture beneath sticky /tmp is allowed only in unprivileged tests.
        if uid != 0 and part == Path('/tmp') and info.st_mode & stat.S_ISVTX:
            continue
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, uid) or info.st_mode & 0o022:
            raise WifiError('unprotected-state')


def private_directory(path, uid=0):
    path = Path(path)
    missing = []
    ancestor = path
    while not ancestor.exists() and not ancestor.is_symlink():
        missing.append(ancestor); ancestor = ancestor.parent
    protected_parents(ancestor, uid)  # Check before creating anything as root.
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            protected_parents(directory, uid)  # A concurrent service may create it first.
    protected_parents(path, uid)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
        raise WifiError('unprotected-state')


class Store:
    def __init__(self, directory=DATA, uid=0):
        self.directory, self.uid = directory, uid
        private_directory(directory, uid)

    def load(self):
        value = private_json(self.directory / 'settings.json', uid=self.uid)
        if value is None:
            return dict(schema=SCHEMA, adapter=None, current=None, previous=None, last_job=None)
        if set(value) != {'schema', 'adapter', 'current', 'previous', 'last_job'} or value['schema'] != SCHEMA:
            raise WifiError('invalid-state')
        if value['adapter'] is not None:
            mac(value['adapter'])
        for name in ['current', 'previous']:
            if value[name] is not None:
                validate_profile(value[name])
        return value

    def commit(self, adapter, value, remember=True, token=None):
        old = self.load()
        if value is not None:
            validate_profile(value)
        if remember:
            new = dict(schema=SCHEMA, adapter=mac(adapter), current=value,
                       previous=old['current'] if old['current'] != value else old['previous'], last_job=token)
            atomic(self.directory / 'settings.json', new, self.uid)
        return old


class Control:
    """A private pathname return socket works across network namespaces."""
    def __init__(self, interface, directory=RUNTIME / 'private', control_root=Path('/run/hector-wifi-trial/private/wpa-control'), uid=0):
        if not INTERFACE.fullmatch(interface):
            raise WifiError('invalid-adapter')
        target = control_root / interface
        info = target.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != uid:
            raise WifiError('wifi-unavailable')
        self.identity = (info.st_dev, info.st_ino)
        private_directory(directory, uid)
        self.path = directory / ('reply-' + uuid.uuid4().hex)
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.socket.settimeout(3)
        try:
            self.socket.bind(str(self.path)); os.chmod(self.path, 0o600)
            self.socket.connect(str(target))
        except Exception:
            self.close()
            raise

    def command(self, value):
        if len(value.encode()) > 2048 or '\n' in value or '\r' in value or '\0' in value:
            raise WifiError('invalid-request')
        self.socket.send(value.encode())
        result = self.socket.recv(65536)
        if len(result) >= 65536:
            raise WifiError('reply-too-large')
        return result.decode('utf-8', 'replace').strip()

    def ok(self, value):
        if self.command(value) != 'OK':
            raise WifiError('wifi-command-refused')

    def status(self):
        fields = dict(line.split('=', 1) for line in self.command('STATUS').splitlines() if '=' in line)
        return {k: fields[k] for k in ['wpa_state', 'id', 'bssid', 'freq', 'ssid', 'ip_address'] if k in fields}

    def close(self):
        self.socket.close(); self.path.unlink(missing_ok=True)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def connect_transaction(ops, value, remember, adapter_only=False):
    """A durable intent and independent restoration timer precede disruption."""
    old = ops.snapshot()
    job = ops.prepare(old, value, remember, adapter_only)
    armed = False
    committed = False
    try:
        armed = True; ops.arm(job)
        ops.apply(job)
        ops.wait_connected(job)
        ops.commit(job); committed = True
    except Exception as error:
        code = error.code if isinstance(error, WifiError) else 'connection-failed'
        ops.failure(job, code)
    finally:
        committed = committed or ops.committed(job)
        if not committed:
            try:
                ops.restore(job)
                if armed:
                    ops.disarm(job)
            except Exception:
                ops.recovery_needed(job)
        elif armed:
            try:
                ops.disarm(job)
            except Exception:
                pass  # A late timer checks the durable commit before restoring.
        ops.finish(job, committed)
    return committed
