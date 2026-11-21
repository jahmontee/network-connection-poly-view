"""Storage and complete Ingen bundles. Only explicit preset imports write data."""
import contextlib
import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import socket
import stat
import tempfile
import threading
import time
import uuid
import zipfile
from urllib.parse import unquote, urlsplit

MIB = 1024 * 1024
MAX_BYTES = 128 * MIB
MAX_UPLOAD = 16 * MIB
MAX_FILES = 8192
RESERVE = 32 * MIB
MANIFEST = 'hector-backup.json'


class Problem(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def component(value):
    if not isinstance(value, str) or not value or value in ('.', '..') or value.startswith('.') or any(ord(c) < 32 or c in '/\\\x7f' for c in value) or len(value.encode()) > 220:
        raise Problem('Use an ordinary preset name without slashes or hidden folders.')
    return value


def safe_parts(value):
    if not value or '\\' in value or '\x00' in value or value.startswith('/'):
        raise Problem('The ZIP contains an unsafe path.')
    parts = value.rstrip('/').split('/')
    if len(parts) > 24 or any(not p or p in ('.', '..') or any(ord(c) < 32 for c in p) or len(p.encode()) > 240 for p in parts):
        raise Problem('The ZIP contains an unsafe or overly long path.')
    return parts


@contextlib.contextmanager
def directory(path=None, parent=None, name=None):
    fd = os.open(str(path) if path is not None else name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


@contextlib.contextmanager
def regular(root_fd, parts):
    with contextlib.ExitStack() as stack:
        fd = root_fd
        for part in parts[:-1]:
            fd = stack.enter_context(directory(parent=fd, name=part))
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise Problem('A preset contains a link or special file; export stopped.')
            yield file_fd, info
        finally:
            os.close(file_fd)


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def revision(*files):
    return hashlib.sha256(repr([fingerprint(info) for info in files]).encode()).hexdigest()


def inventory(fd, prefix=()):
    rows = []
    with os.scandir(fd) as entries:
        names = sorted((e.name for e in entries))
    for name in names:
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        parts = prefix + (name,)
        if stat.S_ISDIR(info.st_mode):
            with directory(parent=fd, name=name) as child:
                rows.extend(inventory(child, parts))
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            rows.append((parts, fingerprint(info)))
        elif stat.S_ISLNK(info.st_mode):
            # Save the reference itself. Never open or follow the destination.
            rows.append((parts, ('symlink', info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns, os.readlink(name, dir_fd=fd))))
        else:
            raise Problem('A preset contains a link or special file; export stopped.')
        if len(rows) > MAX_FILES:
            raise Problem('The preset library contains too many files for one backup.')
    return rows


def rename_new(source, destination, parent_fd):
    """Linux atomic no-replace publish, including protection from an empty target."""
    libc = ctypes.CDLL(None, use_errno=True)
    operation = getattr(libc, 'renameat2', None)
    if operation is None:
        raise Problem('This system cannot safely publish a new preset.', 503)
    if operation(parent_fd, os.fsencode(source), parent_fd, os.fsencode(destination), 1):
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            raise Problem('That preset already exists. Existing presets are never overwritten.', 409)
        raise OSError(code, os.strerror(code))


class Library:
    def __init__(self, preset_root, scratch, data_root='/mnt', reserve=RESERVE):
        self.root = Path(preset_root).resolve(strict=True)
        self.scratch = Path(scratch)
        self.scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.data_root = Path(data_root)
        self.reserve = reserve
        self.lock = threading.Lock()
        self.scan_lock = threading.Lock()
        self.breakdown = {'state': 'idle', 'rows': []}

    def bank(self, bank):
        if bank not in ('beebo', 'digit'):
            raise Problem('Unknown preset bank.')
        return self.root / bank

    def list(self):
        rows = []
        for bank in ('beebo', 'digit'):
            if not self.bank(bank).exists():
                continue
            with directory(self.bank(bank)) as fd:
                for name in sorted(os.listdir(fd), key=str.casefold):
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if not name.startswith('.') and name.endswith('.ingen') and stat.S_ISDIR(info.st_mode):
                        try:
                            with directory(parent=fd,name=name) as preset:
                                main = os.stat('main.ttl', dir_fd=preset, follow_symlinks=False)
                        except FileNotFoundError:
                            main = info
                        rows.append({'bank': bank, 'name': name, 'title': name[:-6].replace('_', ' '),
                                     'modified': info.st_mtime, 'revision': revision(info,main)})
        return rows

    def delete(self, bank, name, expected):
        component(name)
        if not name.endswith('.ingen') or name in ('Empty.ingen','Default_Preset.ingen'):
            raise Problem('This preset is protected from deletion.', 409)
        with self.operation(), directory(self.bank(bank)) as fd:
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode):
                raise Problem('The selected preset no longer exists.', 404)
            with directory(parent=fd,name=name) as preset:
                try:
                    main = os.stat('main.ttl', dir_fd=preset, follow_symlinks=False)
                except FileNotFoundError:
                    main = info
            if expected != revision(info,main):
                raise Problem('This preset changed. Refresh and review it again.',409)
            set_list = self.data_root / 'pedal_state/beebo_preset_list.json'
            if set_list.exists():
                try:
                    with set_list.open() as handle:
                        saved = json.load(handle)
                    if not isinstance(saved,list) or not all(isinstance(item,str) for item in saved):
                        raise ValueError()
                    target = (self.bank(bank)/name).resolve()
                    if any(Path(unquote(urlsplit(item).path)).resolve()==target for item in saved):
                        raise Problem('This preset is in Hector’s set list. Remove it there before deleting it.',409)
                except (ValueError,OSError,RuntimeError):
                    raise Problem('Hector’s set list could not be checked. Try again shortly.',409)
            tombstone = '.hector-delete-' + uuid.uuid4().hex
            rename_new(name,tombstone,fd)
            os.fsync(fd)
            try:
                shutil.rmtree(tombstone,dir_fd=fd)
            except OSError:
                return {'removed':True,'cleanup_pending':True}
            os.fsync(fd)
            return {'removed':True,'cleanup_pending':False}

    def _available(self, root, required, reserve):
        v = os.statvfs(root)
        if v.f_flag & os.ST_RDONLY or v.f_bavail * v.f_frsize < required + reserve:
            raise Problem('There is not enough free space. This operation keeps {} MiB available.'.format(reserve // MIB), 507)

    @contextlib.contextmanager
    def operation(self):
        if not self.lock.acquire(False):
            raise Problem('Another transfer is in progress. Try again when it finishes.', 409)
        try:
            yield
        finally:
            self.lock.release()

    def archive(self, selected=None):
        """Prepare in RAM and verify before sending any bytes to the browser."""
        with self.operation():
            available = {(r['bank'], r['name']) for r in self.list()}
            chosen = sorted(available if selected is None else {tuple(selected)})
            if not chosen or any(pair not in available for pair in chosen):
                raise Problem('The selected preset no longer exists.', 404)
            plans = []
            total = 0
            count = 0
            for bank, name in chosen:
                component(name)
                with directory(self.bank(bank)) as bank_fd, directory(parent=bank_fd, name=name) as fd:
                    rows = inventory(fd)
                    if not any(parts == ('main.ttl',) for parts, _ in rows):
                        raise Problem('A selected preset has no main.ttl file.')
                    plans.append((bank, name, rows))
                    total += sum(len(os.fsencode(sig[-1])) if sig[0]=='symlink' else sig[2] for _, sig in rows)
                    count += len(rows)
            if total > MAX_BYTES or count > MAX_FILES:
                raise Problem('This backup exceeds the first version’s 128 MiB / 8,192 file limit. Download individual presets instead.', 413)
            self._available(self.scratch, total + count * 512 + MIB, 32 * MIB)
            out = tempfile.TemporaryFile(dir=self.scratch)
            manifest = {'format': 'hector-presets/1', 'created': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'scope': 'saved preset bundles and link references only; unsaved edits, settings and external asset contents are not included', 'files': {}, 'links': {}}
            try:
                os.ftruncate(out.fileno(), 0)
                with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_STORED) as z:
                    for bank, name, rows in plans:
                        with directory(self.bank(bank)) as bank_fd, directory(parent=bank_fd, name=name) as fd:
                            for parts, expected in rows:
                                archive_name = '/'.join((bank, name) + parts)
                                if expected[0]=='symlink':
                                    raw=os.fsencode(expected[-1])
                                    entry=zipfile.ZipInfo(archive_name)
                                    entry.create_system=3
                                    entry.external_attr=(stat.S_IFLNK|0o777)<<16
                                    z.writestr(entry,raw)
                                    manifest['links'][archive_name]=expected[-1]
                                    manifest['files'][archive_name]=hashlib.sha256(raw).hexdigest()
                                    continue
                                h = hashlib.sha256()
                                with regular(fd, parts) as (source, info):
                                    if fingerprint(info) != expected:
                                        raise Problem('A preset changed during backup. Finish saving on Hector, then retry.', 409)
                                    with z.open(archive_name, 'w', force_zip64=True) as target:
                                        while True:
                                            chunk = os.read(source, 65536)
                                            if not chunk:
                                                break
                                            target.write(chunk)
                                            h.update(chunk)
                                            time.sleep(0.001)
                                    if fingerprint(os.fstat(source)) != expected:
                                        raise Problem('A preset changed during backup. Finish saving on Hector, then retry.', 409)
                                manifest['files'][archive_name] = h.hexdigest()
                            if inventory(fd) != rows:
                                raise Problem('A preset changed during backup. Finish saving on Hector, then retry.', 409)
                    z.writestr(MANIFEST, json.dumps(manifest, indent=2))
                for bank, name, rows in plans:
                    with directory(self.bank(bank)) as bank_fd, directory(parent=bank_fd, name=name) as fd:
                        if inventory(fd) != rows:
                            raise Problem('A preset changed during backup. Finish saving on Hector, then retry.', 409)
                if selected is None and {(r['bank'], r['name']) for r in self.list()} != available:
                    raise Problem('The preset library changed during backup. Retry after saving.', 409)
                out.seek(0)
                return out
            except BaseException:
                out.close()
                raise

    def upload(self, source, bank='beebo'):
        """Import one complete bundle, staged outside the visible preset list."""
        self.bank(bank)
        with self.operation():
            try:
                z = zipfile.ZipFile(source)
            except (zipfile.BadZipFile, OSError):
                raise Problem('Choose a ZIP containing one complete .ingen preset folder.')
            with z:
                entries = z.infolist()
                if len(entries) > MAX_FILES:
                    raise Problem('The ZIP contains too many files.', 413)
                files = []
                links = []
                seen = set()
                bundle = None
                total = 0
                expected_hashes = None
                expected_links = {}
                manifests=[i for i in entries if i.filename==MANIFEST]
                if len(manifests)>1:
                    raise Problem('Invalid backup manifest.')
                if manifests:
                    item=manifests[0]
                    if item.file_size>2*MIB or item.flag_bits&1 or stat.S_IFMT(item.external_attr>>16) not in (0,stat.S_IFREG):
                        raise Problem('Invalid backup manifest.')
                    try:
                        meta=json.loads(z.read(item))
                        expected_hashes=meta['files'];expected_links=meta.get('links',{})
                        if meta['format']!='hector-presets/1' or not isinstance(expected_hashes,dict) or not isinstance(expected_links,dict):raise ValueError()
                    except (ValueError,KeyError,TypeError,zipfile.BadZipFile):
                        raise Problem('Invalid backup manifest.')
                for info in entries:
                    parts = safe_parts(info.filename)
                    mode = info.external_attr >> 16
                    kind = stat.S_IFMT(mode)
                    if info.flag_bits & 1 or kind not in (0, stat.S_IFREG, stat.S_IFDIR,stat.S_IFLNK) or (kind == stat.S_IFDIR) != info.is_dir() and kind not in (0,stat.S_IFLNK):
                        raise Problem('Encrypted ZIPs, links and special files are not supported.')
                    if info.filename == MANIFEST:
                        continue
                    original = '/'.join(parts)
                    if parts[0] in ('beebo', 'digit'):
                        if parts[0] != bank:
                            raise Problem('Choose the same bank as the downloaded preset.')
                        if len(parts) == 1 and info.is_dir():
                            continue
                        parts = parts[1:]
                    name = component(parts[0])
                    if not name.endswith('.ingen'):
                        raise Problem('The ZIP must contain a .ingen folder, including main.ttl.')
                    if bundle is not None and bundle != name:
                        raise Problem('Upload one preset at a time. Extract the library backup and ZIP the chosen .ingen folder.')
                    bundle = name
                    relative = tuple(parts[1:])
                    if relative in seen:
                        raise Problem('The ZIP contains duplicate paths.')
                    seen.add(relative)
                    if info.is_dir():
                        continue
                    if not relative or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                        raise Problem('Use a standard ZIP containing a complete preset folder.')
                    total += info.file_size
                    if total > MAX_BYTES or info.file_size > MAX_BYTES or info.file_size / max(info.compress_size, 1) > 250:
                        raise Problem('The ZIP expands beyond the import limit.', 413)
                    if kind==stat.S_IFLNK:
                        if info.file_size>2048 or original not in expected_links:
                            raise Problem('Only manifest-checked saved audio references are supported.')
                        try:target=z.read(info).decode('utf-8')
                        except (ValueError,UnicodeError,zipfile.BadZipFile):raise Problem('Invalid saved audio reference.')
                        if not target or '\x00' in target or target!=expected_links[original] or hashlib.sha256(target.encode()).hexdigest()!=expected_hashes.get(original):
                            raise Problem('The audio reference failed its integrity check.')
                        links.append((relative,original,target))
                    else:
                        files.append((info, relative, original))
                if not bundle or not any(parts == ('main.ttl',) for _, parts, _ in files):
                    raise Problem('The preset is missing main.ttl.')
                if expected_hashes is not None and set(expected_hashes) != {original for _, _, original in files}|{original for _,original,_ in links}:
                    raise Problem('The files do not match the backup manifest.')
                if set(expected_links)!={original for _,original,_ in links}:
                    raise Problem('The saved audio references do not match the manifest.')
                link_paths={parts for parts,_,_ in links}
                if any(parts[:i] in link_paths for parts in seen for i in range(1,len(parts))):
                    raise Problem('A ZIP path attempts to write through a link.')
                destination=self.bank(bank)/bundle
                ordinary_paths={parts for _,parts,_ in files}
                audio_roots={Path('/audio').resolve(),Path('/mnt/audio').resolve()}
                for parts,original,target in links:
                    resolved=Path(os.path.realpath(os.path.join(str(destination.joinpath(*parts[:-1])),target)))
                    internal=resolved.is_relative_to(destination) and tuple(resolved.relative_to(destination).parts) in ordinary_paths
                    audio=resolved.suffix.lower()=='.wav' and any(resolved.is_relative_to(root) and resolved!=root for root in audio_roots)
                    if not (internal or audio):
                        raise Problem('The ZIP has a link outside saved preset files or Hector’s shared audio folders.')
                required = total + len(entries) * 4096 + MIB
                self._available(self.bank(bank), required, self.reserve)
                stage = '.hector-upload-' + uuid.uuid4().hex
                with directory(self.bank(bank)) as fd:
                    if os.path.lexists(self.bank(bank) / bundle):
                        raise Problem('That preset already exists. Existing presets are never overwritten.', 409)
                    os.mkdir(stage, 0o700, dir_fd=fd)
                    staging = self.bank(bank) / stage
                    try:
                        for info, parts, original in files:
                            target = staging.joinpath(*parts)
                            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                            h = hashlib.sha256()
                            copied = 0
                            with z.open(info) as src, open(target, 'xb') as dst:
                                os.chmod(target, 0o644)
                                while True:
                                    chunk = src.read(65536)
                                    if not chunk:
                                        break
                                    copied += len(chunk)
                                    if copied > info.file_size:
                                        raise Problem('Invalid ZIP file length.')
                                    self._available(self.bank(bank), len(chunk) + MIB, self.reserve)
                                    dst.write(chunk)
                                    h.update(chunk)
                                    time.sleep(0.001)
                                dst.flush()
                                os.fsync(dst.fileno())
                            if copied != info.file_size or expected_hashes is not None and h.hexdigest() != expected_hashes[original]:
                                raise Problem('A file failed the backup integrity check.')
                        for parts,original,target in links:
                            link=staging.joinpath(*parts)
                            link.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
                            os.symlink(target,link)
                        for folder, _, _ in os.walk(staging, topdown=False):
                            with directory(folder) as staged_fd:
                                os.fsync(staged_fd)
                        os.chmod(staging, 0o755)
                        rename_new(stage, bundle, fd)
                        os.fsync(fd)
                    except (zipfile.BadZipFile, RuntimeError, EOFError) as exc:
                        raise Problem('The ZIP is damaged or incomplete.') from exc
                    finally:
                        if staging.exists():
                            shutil.rmtree(staging)
                return {'bank': bank, 'name': bundle, 'files': len(files), 'bytes': total}

    def scan(self):
        if not self.scan_lock.acquire(False):
            return self.breakdown
        self.breakdown = {'state': 'running', 'rows': []}

        def run():
            rows = []
            audio = {}
            try:
                deadline = time.monotonic() + 120
                root_device = self.data_root.stat().st_dev
                count = 0

                def walk(parent_fd, name, audio_root=False):
                    nonlocal count
                    info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                    count += 1
                    if count > 60000 or time.monotonic() > deadline:
                        raise Problem('Folder inspection reached its time or file limit. Retry when Hector is idle.')
                    if count % 64 == 0:
                        time.sleep(0.01)
                    if info.st_dev != root_device or stat.S_ISLNK(info.st_mode):
                        return 0
                    size = info.st_blocks * 512
                    if stat.S_ISDIR(info.st_mode):
                        with directory(parent=parent_fd, name=name) as child_fd, os.scandir(child_fd) as entries:
                            for entry in entries:
                                child_size = walk(child_fd, entry.name)
                                size += child_size
                                if audio_root and entry.name in ('reverbs', 'cabs', 'sample_box'):
                                    audio[entry.name] = child_size
                    return size

                with directory(self.data_root) as root_fd, os.scandir(root_fd) as entries:
                    for entry in entries:
                        if len(rows) >= 256:
                            raise Problem('Folder inspection reached the 256 top-level folder limit.')
                        if entry.is_symlink():
                            continue
                        rows.append({'name': entry.name, 'bytes': walk(root_fd, entry.name, entry.name=='audio')})
                self.breakdown = {'state': 'ready', 'rows': sorted(rows, key=lambda r: r['bytes'], reverse=True), 'audio': audio, 'updated': time.time(), 'note': 'Allocated folder sizes. Shared Btrfs extents may be counted more than once; totals can differ from filesystem usage.'}
            except (OSError, Problem) as exc:
                self.breakdown = {'state': 'error', 'rows': [], 'message': str(exc)}
            finally:
                self.scan_lock.release()

        threading.Thread(target=run, daemon=True).start()
        return self.breakdown


def storage():
    rows = []
    names = {'/mnt': ('Data storage', 'Presets, community installs, rollback copies and logs', 'data'), '/overlay/lower': ('System filesystem', 'Operating system and installed software', 'system'), '/nor_flash': ('Preset flash', 'Separate internal flash; presets may be redirected to the SD card', 'flash'), '/overlay': ('Temporary memory', 'RAM-backed working area; cleared at reboot', 'memory')}
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, right = line.split(' - ', 1)
        a, b = left.split(), right.split()
        target = a[4]
        if target not in names:
            continue
        name, description, kind = names[target]
        v = os.statvfs(target)
        # The service has read-only bind mounts for protection. Report the
        # backing filesystem mode, rather than that private service view.
        rows.append({'name': name, 'description': description, 'kind': kind, 'path': target, 'filesystem': b[0], 'total': v.f_blocks * v.f_frsize, 'used': (v.f_blocks - v.f_bfree) * v.f_frsize, 'reserved': (v.f_bfree-v.f_bavail)*v.f_frsize, 'available': v.f_bavail * v.f_frsize, 'readonly': 'ro' in b[2].split(',')})
    return rows


def card_geometry():
    root = Path('/sys/class/block')
    try:
        sizes = {name: int((root / name / 'size').read_text()) * 512 for name in ('mmcblk0', 'mmcblk0p1', 'mmcblk0p2', 'mmcblk0p3')}
        return {'card_bytes': sizes['mmcblk0'], 'system_partition_bytes': sizes['mmcblk0p2'], 'data_partition_bytes': sizes['mmcblk0p3'], 'unpartitioned_bytes': max(0, sizes['mmcblk0'] - sum(v for k, v in sizes.items() if k != 'mmcblk0'))}
    except (OSError, ValueError):
        return None


def inspector_request(path, action):
    if action not in ('status', 'scan'):
        raise Problem('Unknown inspection action.')
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(2)
            connection.connect(str(path))
            connection.sendall(json.dumps({'action': action}).encode()+b'\n')
            raw = b''
            while b'\n' not in raw:
                chunk = connection.recv(8192)
                if not chunk or len(raw)+len(chunk)>131072:
                    raise ValueError('Incomplete inspection response')
                raw += chunk
        return json.loads(raw.split(b'\n', 1)[0])
    except (OSError, ValueError):
        return {'state': 'error', 'rows': [], 'message': 'The read-only folder inspector is unavailable. Storage capacity and preset transfers are still available.'}
