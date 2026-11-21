"""Stage standalone Hector LV2 releases without writing to firmware."""
import argparse
import fcntl
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import sys
import tempfile
import time
import zipfile
from contextlib import contextmanager

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import MIB, Problem, component
from module_restart import MARKER as RESTART_MARKER
from native_release import (ARCHIVE, SCOPE_KEY, SIGNED_ARCHIVE, STAGED_ARCHIVE, matches,
                            native_zip, signed_release, verified_release)
from publisher_release import PublisherPolicy
import native_module
import usb_release
from native_migration import MigrationPolicy, DEFAULT_POLICY as MIGRATION_POLICY

LIMIT = 16 * MIB
MAX_MODULES = 24
PREFIX = 'urn:hector-community:poly-view:'
SCHEMA = 'poly-view-hector-module/1'
DATA = Path('/mnt/hector-storage-manager/modules')
RUNTIME = Path('/run/hector-module-catalog')
COMMUNITY_CATALOG = Path(__file__).with_name('community-catalog.json')
NATIVE_BATCH_KEY = 'aetherscope-test13'
NATIVE_PROGRESS = Path('/run/hector-native-install-progress.json')
SAMPLE_BOX_LEGACY_URI = 'urn:beebo-hector-lab:sample-box'
SAMPLE_BOX_LEGACY_REVIEW = hashlib.sha256(SAMPLE_BOX_LEGACY_URI.encode()).hexdigest()
# Exact root-owned legacy inventories from our reviewed builds. This exception
# does not admit third-party USB modules or unsigned uploads into the manager.
OUR_LEGACY_EFFECTS = frozenset(('sample_box_legacy','cardinal_module','echo_field_xl','echo_field',
    'fourbyfour','fourbyfour_legacy','link_audio_receive','link_audio_send','ableton_link',
    'ableton_link_audio','mala','oscilloscope','poly_rhythm','poly_rhythm_next',
    'poly_rhythm_legacy','poly303','sample_box'))


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def write_file(path, data):
    with path.open('wb') as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish(path, data):
    with tempfile.TemporaryDirectory(dir=path.parent, prefix='.write-') as temp:
        new = Path(temp) / 'new'
        write_file(new, data)
        os.replace(new, path)
        sync_directory(path.parent)


def text_field(value, limit):
    return (isinstance(value, str) and 0 < len(value) <= limit and value == value.strip()
            and value.isprintable() and '|' not in value)


def check_manifest(module):
    if native_module.is_native(module):
        native_module.validate(module)
        return
    keys = {'schema', 'id', 'name', 'version', 'ports'}
    if not isinstance(module, dict) or set(module) != keys or module['schema'] != SCHEMA:
        raise Problem('Choose a Poly / View–compatible Hector release ZIP.')
    if not isinstance(module['id'], str) or not re.fullmatch('[a-z][a-z0-9-]{0,47}', module['id']):
        raise Problem('Invalid module ID.')
    if not text_field(module['name'], 80) or not text_field(module['version'], 32):
        raise Problem('Invalid module name or version.')
    ports = module['ports']
    if not isinstance(ports, list) or not 1 <= len(ports) <= 32:
        raise Problem('A module must describe 1–32 ports.')
    symbols = set()
    for port in ports:
        if not isinstance(port, dict) or set(port) - {'symbol', 'name', 'direction', 'type', 'range'}:
            raise Problem('Invalid module port.')
        symbol = port.get('symbol')
        if not isinstance(symbol, str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,31}', symbol) or symbol in symbols:
            raise Problem('Invalid or repeated port symbol.')
        symbols.add(symbol)
        if not text_field(port.get('name'), 64) or port.get('direction') not in ('input', 'output'):
            raise Problem('Invalid port label or direction.')
        kind = port.get('type')
        if kind == 'control' and port['direction'] == 'input':
            values = port.get('range')
            if (not isinstance(values, list) or len(values) != 3
                    or not all(type(v) in (int, float) and -1e9 <= v <= 1e9 and math.isfinite(v) for v in values)
                    or not values[1] <= values[0] <= values[2] or values[1] == values[2]):
                raise Problem('Control inputs need a valid [default, minimum, maximum] range.')
        elif kind not in ('audio', 'cv') or 'range' in port:
            raise Problem('Supported ports are audio, CV, and numeric control inputs.')


def read_release(raw):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            limits = {'module.json': 24 * 1024, 'module.so': LIMIT, 'LICENSE': 64 * 1024}
            names = [entry.filename for entry in entries]
            if (len(set(names)) != len(names) or not {'module.json', 'module.so'} <= set(names)
                    or any(name not in limits for name in names)):
                raise Problem('This ZIP is not a standalone Poly / View release. USB kits and source ZIPs use their original installation instructions.')
            for entry in entries:
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if (mode not in (0, stat.S_IFREG) or entry.orig_filename != entry.filename
                        or entry.flag_bits & 1 or not 0 < entry.file_size <= limits[entry.filename]
                        or entry.compress_type not in (zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED)):
                    raise Problem('The ZIP contains an unsupported file or exceeds the size limit.')
            module = json.loads(archive.read('module.json'))
            binary = archive.read('module.so')
    except (zipfile.BadZipFile, ValueError, RuntimeError, EOFError) as error:
        raise Problem('The release ZIP or its module manifest could not be read.') from error
    check_manifest(module)
    check_binary(module, binary)
    return module, binary


def check_binary(module, binary):
    if (len(binary) < 64 or binary[:7] != b'\x7fELF\x02\x01\x01'
            or binary[16:24] != b'\x03\x00\xb7\x00\x01\x00\x00\x00'):
        raise Problem('The module must be a Linux ARM64 shared library for Hector.')
    if b'\0lv2_descriptor\0' not in binary or module_uri(module).encode() + b'\0' not in binary:
        raise Problem('The module binary is missing its LV2 descriptor or the expected module URI.')


def port_identity(module):
    return [(port['symbol'], port['direction'], port['type']) for port in module['ports']]


def module_uri(module):
    return module['uri'] if native_module.is_native(module) else PREFIX + module['id']


def check_update(current, incoming):
    if (not incoming.get('publisher') or (current.get('publisher')
            and current['publisher'] != incoming['publisher'])):
        raise Problem('The update must be signed by the installed module’s verified developer.', 409)
    if current['version'] == incoming['version']:
        raise Problem('This module version is already installed.', 409)
    if module_uri(current) != module_uri(incoming):
        raise Problem('The update changes the module URI used by saved patches.', 409)
    if native_module.is_native(current) and (not native_module.is_native(incoming)
            or native_module.effect_name(current) != native_module.effect_name(incoming)):
        raise Problem('The update changes the native module picker ID.', 409)
    old_ports = port_identity(current)
    if port_identity(incoming)[:len(old_ports)] != old_ports:
        raise Problem('This update changes existing ports and could break saved patches.', 409)


def prototype(module):
    result = dict(display_name=module['name'], description=module['name'],
                  long_description='Community LV2 module · ' + module['version'],
                  category=0, tags=['ported', 'community'], controls={}, inputs={}, outputs={})
    for port in module['ports']:
        name, kind, symbol = port['name'], port['type'], port['symbol']
        if kind == 'control':
            result['controls'][symbol] = [name, *port['range']]
        else:
            result[port['direction'] + 's'][symbol] = [name, 'AudioPort' if kind == 'audio' else 'CVPort']
    result['num_cv_in'] = sum(port[1] == 'CVPort' for port in result['inputs'].values())
    return result


def turtle(module, filename):
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    fields = ['a lv2:Plugin', 'doap:name ' + quote(module['name']), 'lv2:binary <' + filename + '>']
    for index, port in enumerate(module['ports']):
        kind = {'audio': 'AudioPort', 'cv': 'CVPort', 'control': 'ControlPort'}[port['type']]
        lines = ['a lv2:' + port['direction'].title() + 'Port, lv2:' + kind,
                 'lv2:index ' + str(index), 'lv2:symbol ' + quote(port['symbol']), 'lv2:name ' + quote(port['name'])]
        if kind == 'ControlPort':
            lines.extend('lv2:' + name + ' ' + str(value)
                         for name, value in zip(('default', 'minimum', 'maximum'), port['range']))
        fields.append('lv2:port [ ' + ' ; '.join(lines) + ' ]')
    return '<' + PREFIX + module['id'] + '> ' + ' ;\n  '.join(fields) + ' .\n'


def valid_keys(keys):
    return (isinstance(keys, list) and 1 <= len(keys) <= MAX_MODULES
            and all(isinstance(key, str) and re.fullmatch('[a-f0-9]{64}', key) for key in keys)
            and len(set(keys)) == len(keys))


def activation_plan(state):
    return hashlib.sha256(canonical((sorted(state['target']), state.get('removal_reviews', {})))).hexdigest()


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


class InstallBatch:
    def __init__(self, root):
        self.path = root / 'install-batch.json'

    def read(self):
        try:
            return json.loads(self.path.read_bytes())
        except FileNotFoundError:
            return None

    def save(self, batch):
        if batch is None:
            self.path.unlink(missing_ok=True)
            sync_directory(self.path.parent)
        else:
            publish(self.path, canonical(batch))

    def view(self):
        batch = self.read()
        if not batch or batch['phase'] in ('installed', 'failed'):
            return batch
        if not RESTART_MARKER.exists():
            if batch['phase'] == 'checking' and time.time() - batch.get('requested', 0) <= 20:
                return batch
            return dict(batch, phase='failed', error='The installation was interrupted. Review the queue before trying again.')
        if batch['native'] and batch['phase'] in ('checking', 'installing', 'ready', 'restarting'):
            try:
                progress = json.loads(NATIVE_PROGRESS.read_bytes())
            except (OSError, ValueError):
                return batch
            if progress['batch'] == batch['id']:
                row = batch['modules'][-1]
                row.update({key: progress[key] for key in ('status', 'applied', 'total', 'percent', 'message') if key in progress})
        return batch

    def begin(self, state, keys, native, native_package=None):
        rows = [{key: state['queued'][selected][key] for key in ('name', 'version', 'binary_sha256')}
                | {'key': selected, 'status': 'queued'} for selected in keys]
        if native:
            details = native_package or {'name': 'AetherScope', 'version': 'test.13'}
            rows.append(details | dict(key=NATIVE_BATCH_KEY, status='queued'))
        batch = dict(id=secrets.token_hex(16), boot=boot_id(), phase='checking', requested=time.time(), native=native, modules=rows)
        self.save(batch)
        return batch

    def fail(self, batch, message):
        if batch and batch['phase'] not in ('installed', 'failed'):
            if batch['phase'] == 'installing' and batch['native']:
                batch['modules'][-1]['status'] = 'failed'
            batch.update(phase='failed', error=str(message)[:300])
            self.save(batch)


class ModuleManager:
    def __init__(self, root, runtime, preset_root, reserve, startup=None, *, community_preview=False,
                 community_catalog=COMMUNITY_CATALOG, native=False, publishers=None,
                 migration_policy=MIGRATION_POLICY, firmware_root=Path("/")):
        self.root = Path(root)
        self.runtime = Path(runtime)
        self.presets = Path(preset_root)
        self.startup = Path(startup) if startup else None
        self.reserve = reserve
        self.community_preview = community_preview
        self.native = native
        self.publishers = publishers
        self.batch = InstallBatch(self.root)
        self.firmware_root=Path(firmware_root)
        self.migrations = MigrationPolicy(migration_policy, firmware_root)
        try:
            self.community = json.loads(Path(community_catalog).read_bytes()) if community_catalog else {}
        except FileNotFoundError:
            self.community = {}

    @contextmanager
    def locked(self):
        self.root.mkdir(mode=0o700, exist_ok=True)
        with (self.root / 'lock').open('ab') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def state(self):
        try:
            return json.loads((self.root / 'state.json').read_bytes())
        except FileNotFoundError:
            return dict(queued={}, target={}, applied={})

    def save(self, state):
        publish(self.root / 'state.json', canonical(state))

    def collect(self, state):
        referenced = set().union(state['queued'], state['target'], state['applied'])
        for path in self.root.iterdir():
            if re.fullmatch(r'[a-f0-9]{64}\.so', path.name) and path.name[:-3] not in referenced:
                path.unlink()
            if re.fullmatch(r'[a-f0-9]{64}\.pkg', path.name) and path.name[:-4] not in referenced:
                if path.is_symlink():
                    path.unlink()
                else:
                    shutil.rmtree(path)

    def native_inventory(self):
        try:
            snapshot = json.loads((self.runtime / 'native-inventory.json').read_bytes())
            entries = snapshot.get('entries')
            if snapshot.get('schema') == 1 and isinstance(entries, list):
                return entries
        except (OSError, ValueError, AttributeError):
            pass
        return None

    def community_removal_preview(self, inventory):
        if not self.community_preview:
            return None
        for entry in inventory or []:
            if (entry.get('id'), entry.get('uri'), entry.get('origin')) == (
                    'sample_box_legacy', SAMPLE_BOX_LEGACY_URI, 'community'):
                return {'id': entry['id'], 'name': 'Sample Box Legacy', 'key': SAMPLE_BOX_LEGACY_REVIEW}
        return None

    def managed_community(self, package):
        return self.publishers is None or (bool(package.get('members'))
            and set(package['members']) <= OUR_LEGACY_EFFECTS)

    def community_listing(self, state, activation):
        pending = state.get('community_pending', {})
        return [dict(key=key, members=list(package['members']),
                     **({field:rule[field] for field in ('id','publisher')} |
                        {'version':rule['previous_version']} if (rule:=self.migrations.rules.get(key)) else
                        {'id':'oscilloscope','version':package.get('version'),'publisher':package.get('publisher')} if key==SCOPE_KEY else {}),
                     name=package['name'], scope=package['scope'], status='remove' if key in pending else 'installed')
                for key, package in self.community.items() if key in activation.get('community', []) and self.managed_community(package)]

    def listing(self, state):
        def summary(package, **extra):
            return {k: package[k] for k in ('id', 'name', 'version', 'file', 'size')} | extra
        try:
            activation = json.loads((self.runtime / 'activation.json').read_bytes())
            active = activation['active']
        except FileNotFoundError:
            activation, active = {}, state['applied']
        target = state['target']
        pending = set(active) != set(target)
        error = activation.get('error')
        if not pending or (activation.get('plan') and activation['plan'] != activation_plan(state)):
            error = None
        community_pending = state.get('community_pending', {})
        for key, attempt in activation.get('community_errors', {}).items():
            if community_pending.get(key, {}).get('review') == attempt['review']:
                error = error or attempt['error']
        inventory = self.native_inventory()
        if self.publishers is not None and inventory is not None:
            reviewed=set()
            for key in activation.get('community',[]):
                package=self.community.get(key)
                if not package or not self.managed_community(package):continue
                try:self.community_assets(package)
                except (OSError,Problem):continue
                reviewed.update((name,member['uri']) for name,member in package['members'].items())
            inventory=[row for row in inventory if row.get('origin') in ('stock','device')
                       or (row.get('id'),row.get('uri')) in reviewed]
        native_release = None
        if self.native:
            native_release = {'file': ARCHIVE, 'staged': (self.root / STAGED_ARCHIVE).is_file()}
            if state.get('native_queued') and (self.root / SIGNED_ARCHIVE).is_file():
                native_release.update(state['native_queued'], staged=True)
        return {
            'revision': hashlib.sha256(canonical(state)).hexdigest(),
            'ready': bool(activation),
            'activation_error': error,
            'restart_required': pending or bool(community_pending),
            'native_inventory': inventory,
            'community_removal': self.community_listing(state, activation),
            'community_removal_preview': self.community_removal_preview(inventory),
            'native_release': native_release,
            'install_batch': self.batch.view(),
            'queue': [summary(record, key=key, **({'previous_version': state['applied'][record['replaces']]['version']}
                          if record.get('replaces') in state['applied'] else
                          {'previous_version': record['community_previous_version']}
                          if record.get('community_upgrade') else {}))
                      for key, record in state['queued'].items()],
            'installed': [summary(record, key=key, status=('installed' if key in active and key in target
                          else 'install' if key in target else 'remove'))
                          for key, record in (active | target).items()],
        }

    def list(self):
        with self.locked():
            return self.listing(self.state())

    def upload(self, name, stream, length, *, offline=False):
        component(name)
        if not name.lower().endswith('.zip') or not 0 < length <= LIMIT:
            raise Problem('Choose a release ZIP of up to 16 MiB.')
        raw = stream.read(length)
        if len(raw) != length:
            raise Problem('The upload was interrupted.')
        package_key=hashlib.sha256(raw).hexdigest()
        if usb_release.wrapped(raw):
            if self.publishers is None:raise Problem('A verified publisher policy is required for this installer.')
            raw,_,_=usb_release.read(raw,self.publishers)
        if offline and os.geteuid()!=0:raise Problem('Independent USB installation requires the trusted root worker.')
        if self.native and matches(raw):
            verified_release(raw)
            with self.locked():
                if RESTART_MARKER.exists():
                    raise Problem('Hector is already restarting.', 409)
                if (self.root / SIGNED_ARCHIVE).is_file():
                    raise Problem('Discard the queued native package before uploading another.', 409)
                space = os.statvfs(self.root)
                if space.f_bavail * space.f_frsize < self.reserve + length + MIB:
                    raise Problem('There is too little free data space for this release.', 507)
                publish(self.root / STAGED_ARCHIVE, raw)
                return self.listing(self.state())
        if self.native and name == ARCHIVE:
            raise Problem('Choose the original, verified AetherScope test.13 USB ZIP.')
        if self.native and self.publishers is not None and native_zip(raw):
            manifest, _, _, publisher = signed_release(raw, self.publishers)
            return self.stage_native(name, raw, manifest, publisher)
        assets = None
        if self.publishers is not None and self.native_module_zip(raw):
            module, binary, proof, assets = native_module.read(raw, self.publishers)
        elif self.publishers is None:
            module, binary = read_release(raw)
            proof = {}
        else:
            module, binary, proof = self.publishers.read(raw)
            check_manifest(module)
            check_binary(module, binary)
        import system_support
        system_support.requirement(module,self.firmware_root,staging=offline,policy=self.publishers)
        key = package_key
        with self.locked():
            if RESTART_MARKER.exists() and not offline:
                raise Problem('Hector is already restarting.', 409)
            state = self.state()
            self.collect(state)
            existing = state['applied'] | state['queued'] | state['target']
            self.check_native_identity(module | proof, state, existing)
            matching = [previous for previous, record in existing.items() if module['id'] == record['id']]
            if len({record['id'] for record in existing.values()}) >= MAX_MODULES and not matching:
                raise Problem('The module library and queue can hold up to 24 packages.', 409)
            package_bytes = sum(len(content) for content in assets.values()) if assets is not None else len(binary)
            record = module | proof | {'file': name, 'size': package_bytes,
                                       'binary_sha256': hashlib.sha256(binary).hexdigest()}
            adoption = self.migrations.adoption(self, record, state) if native_module.is_native(module) else None
            if adoption:
                record['community_upgrade'] = adoption
                record['community_previous_version'] = self.migrations.rules[adoption]['previous_version']
            if matching:
                if (not proof or len(matching) != 1 or matching[0] not in state['applied']
                        or matching[0] not in state['target'] or matching[0] in state['queued']):
                    raise Problem('That module ID is already queued, installed, or waiting for removal.', 409)
                record['replaces'] = matching[0]
                check_update(state['applied'][matching[0]], record)
            space = os.statvfs(self.root)
            if space.f_bavail * space.f_frsize < self.reserve + package_bytes + MIB:
                raise Problem('There is too little free data space for this package.', 507)
            publish(self.root / (key + '.so'), binary)
            if assets is not None:
                self.publish_native_assets(key, assets)
                record['uris'] = [module['uri']]
            state['queued'][key] = record
            self.save(state)
            return self.listing(state)

    @staticmethod
    def native_module_zip(raw):
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                info = archive.getinfo('module.json')
                if info.file_size > MIB:
                    raise Problem('The module manifest exceeds its size limit.')
                return native_module.is_native(json.loads(archive.read(info)))
        except (KeyError, zipfile.BadZipFile, ValueError, TypeError):
            return False

    def check_native_identity(self, module, state, existing):
        if not native_module.is_native(module):
            return
        self.migrations.reserve(module)
        uri = module['uri']
        effect = native_module.effect_name(module)
        stock = json.loads(Path(__file__).with_name('stock407_modules.json').read_bytes())['modules']
        if uri in stock.values() or effect in stock:
            raise Problem('Stock modules are protected.', 409)
        reviewed={(name,member['uri']) for package in self.community.values() for name,member in package['members'].items()}
        for entry in self.native_inventory() or []:
            if entry.get('uri')!=uri and entry.get('id')!=effect:continue
            managed=any(record['id']==module['id'] and module_uri(record)==uri for record in existing.values())
            if not managed and (entry.get('id'),entry.get('uri')) not in reviewed:
                raise Problem('This existing module needs a reviewed compatibility profile before it can be updated.',409)
        for key, package in self.community.items():
            for name, member in package['members'].items():
                if uri == member['uri'] or effect == name:
                    if key not in state.get('community_removed', []) or uri != member['uri'] or effect != name:
                        if key not in self.migrations.rules:
                            raise Problem('Remove the older community version before migrating it to this package format.', 409)
                        self.migrations.validate(self, module, state, key)
        for record in existing.values():
            if record['id'] == module['id']:
                continue
            other_effect = native_module.effect_name(record)
            if uri == module_uri(record) or effect == other_effect:
                raise Problem('Another installed or queued module uses this URI or picker ID.', 409)

    def publish_native_assets(self, key, assets):
        destination = self.root / (key + '.pkg')
        if destination.exists():
            raise Problem('This release already has a staged native package. Discard it before uploading again.', 409)
        with tempfile.TemporaryDirectory(dir=self.root, prefix='.native-') as directory:
            package = Path(directory) / 'package'
            package.mkdir(mode=0o700)
            for name, raw in assets.items():
                if name == 'module.so':
                    continue
                target = package / name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                write_file(target, raw)
            for parent in sorted((path for path in package.rglob('*') if path.is_dir()), key=lambda path: len(path.parts), reverse=True):
                sync_directory(parent)
            sync_directory(package)
            os.replace(package, destination)
            sync_directory(self.root)

    def stage_native(self, name, raw, manifest, publisher):
        with self.locked():
            if RESTART_MARKER.exists():
                raise Problem('Hector is already restarting.', 409)
            state = self.state()
            installed = self.community.get(SCOPE_KEY, {})
            version = installed.get('version', '0.1.0-test.13')
            active = SCOPE_KEY in self.activation().get('community', [])
            if active and version == manifest['version']:
                raise Problem('This module version is already installed.', 409)
            if installed.get('publisher', publisher) != publisher:
                raise Problem('The update must be signed by the installed module’s verified developer.', 409)
            if (self.root / SIGNED_ARCHIVE).exists() and state.get('native_queued'):
                raise Problem('Discard the queued native package before uploading another.', 409)
            space = os.statvfs(self.root)
            if space.f_bavail * space.f_frsize < self.reserve + len(raw) + MIB:
                raise Problem('There is too little free data space for this release.', 507)
            publish(self.root / SIGNED_ARCHIVE, raw)
            state['native_queued'] = dict(file=name, name='AetherScope', version=manifest['version'],
                                          signed=True, sha256=hashlib.sha256(raw).hexdigest())
            if active:
                state['native_queued']['previous_version'] = version
            self.save(state)
            return self.listing(state)

    def activation(self):
        try:
            return json.loads((self.runtime / 'activation.json').read_bytes())
        except FileNotFoundError:
            return {}

    def discard_native(self, state):
        if state.pop('native_queued', None):
            (self.root / SIGNED_ARCHIVE).unlink(missing_ok=True)
            self.save(state)
        else:
            (self.root / STAGED_ARCHIVE).unlink(missing_ok=True)

    def references(self, modules, startup=False):
        uses = {key: [] for key in modules}
        if not uses:
            return uses
        paths = [('startup', self.startup)] if startup and self.startup and self.startup.exists() else []
        if not startup:
            paths = [(bank, path) for bank in ('beebo', 'digit')
                     for path in sorted((self.presets / bank).glob('*.ingen'))]
        namespaced = re.compile(rb'(?im)(?:@prefix|prefix)\s+([\w.-]*):\s*<([^>]+)>')
        identifiers = {}
        for key, module in modules.items():
            uris = module.get('uris') or ([module['uri']] if native_module.is_native(module) else None)
            if uris:
                compact = []
                for uri in uris:
                    split = max(uri.rfind('#'), uri.rfind('/'), uri.rfind(':')) + 1
                    compact.append((uri[:split].encode(), uri[split:].encode()))
                full = b'|'.join(re.escape(('<' + uri + '>').encode()) for uri in uris)
            else:
                identifier = module['id']
                compact = [(PREFIX.encode(), identifier.encode())]
                full = rb'(?:' + re.escape((PREFIX + identifier).encode()) + rb'|' + re.escape(
                    ('poly_view_' + identifier.replace('-', '_')).encode()) + rb')(?![\w-])'
            identifiers[key] = (compact, re.compile(full))
        for bank, path in paths:
            if path.is_symlink() or not path.is_dir():
                raise Problem('A preset could not be checked for references.', 409)
            found = set()
            for base, _, names in os.walk(path, followlinks=False):
                for name in names:
                    if not name.endswith('.ttl'):
                        continue
                    with os.fdopen(os.open(Path(base) / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as source:
                        info = os.fstat(source.fileno())
                        if not stat.S_ISREG(info.st_mode) or info.st_size > 8 * MIB:
                            raise Problem('A preset could not be checked for references.', 409)
                        raw = source.read(8 * MIB + 1)
                    prefixes = namespaced.findall(raw)
                    for key, (compact, full) in identifiers.items():
                        if full.search(raw) or any(re.search(
                            re.escape(prefix + b':' + identifier) + rb'(?![\w-])', raw
                        ) for prefix, base in prefixes for namespace, identifier in compact if base == namespace):
                            found.add(key)
            for key in found:
                uses[key].append(bank + '/' + path.name)
        return uses

    def checked_state(self, revision, action):
        if action not in ('discard','undo'):
            from system_readiness import require_mutation
            require_mutation()
        if RESTART_MARKER.exists():
            raise Problem('Hector is already restarting.', 409)
        state = self.state()
        current = self.listing(state)
        if current['revision'] != revision:
            raise Problem('The module library changed. Refresh and review the latest list.', 409)
        if action != 'discard' and not current['ready']:
            raise Problem('Restart Hector to finish enabling the module catalog.', 409)
        return state

    def start_batch(self, keys, native, revision, restarter):
        if (type(native) is not bool or not isinstance(keys, list) or (not keys and not native)
                or (keys and not valid_keys(keys))):
            raise Problem('Select the packages you want to install.')
        with self.locked():
            state = self.checked_state(revision, 'install')
            if self.listing(state)['restart_required']:
                raise Problem('Apply or undo pending module changes before starting an install.', 409)
            if not all(key in state['queued'] for key in keys):
                raise Problem('The selected packages changed. Refresh the queue.', 409)
            import system_support
            for key in keys:system_support.requirement(state['queued'][key],self.firmware_root,policy=self.publishers)
            staged = self.listing(state)['native_release'] if native else None
            if native and (not staged or not staged['staged']):
                raise Problem('Upload a verified native release first.', 409)
            previous = self.batch.read()
            if previous and self.batch.view()['phase'] not in ('installed', 'failed'):
                raise Problem('A module installation is already in progress.', 409)
            package = state.get('native_queued') if staged and staged.get('signed') else None
            batch = self.batch.begin(state, keys, native, package)
            try:
                restarter.batch_request(native)
            except Exception as error:
                if getattr(error, 'request_sent', False):
                    return self.batch.read()
                self.batch.save(previous)
                raise
            return batch

    def verify_publisher(self, record):
        if 'publisher_proof' not in record and self.publishers is None:
            return
        if self.publishers is None or 'publisher_proof' not in record:
            raise Problem('Upload a signed package from a verified developer.')
        if native_module.is_native(record):
            manifest = native_module.manifest(record)
            owner, files = self.publishers.verify_manifest(record['publisher_proof'], record['id'], native=True)
            if (files != record.get('native_files') or files.get('module.so') != record['binary_sha256']
                    or files.get('module.json') != hashlib.sha256(canonical(manifest)).hexdigest()
                    or owner != record.get('publisher')):
                raise Problem('The native package differs from the signed release.')
            return
        manifest = {name: record[name] for name in ('schema', 'id', 'name', 'version', 'ports')}
        checksum = hashlib.sha256(canonical(manifest)).hexdigest()
        owner, _ = self.publishers.verify(record['publisher_proof'], manifest['id'], checksum, record['binary_sha256'])
        if owner != record.get('publisher'):
            raise Problem('The module publisher changed after upload.')

    def batch_targets(self, state, batch):
        target = dict(state['target'])
        for row in batch['modules']:
            key = row['key']
            if key == NATIVE_BATCH_KEY:
                continue
            record = state['queued'].get(key) or target.get(key)
            if not record or record['binary_sha256'] != row['binary_sha256']:
                raise Problem('A package changed while Hector was restarting.')
            if key not in target:
                previous = record.get('replaces')
                matches = [other for other, installed in target.items() if installed['id'] == record['id']]
                if matches != ([previous] if previous else []):
                    raise Problem('The installed module changed. Upload the update again.', 409)
                if previous:
                    check_update(target.pop(previous), record)
            target[key] = record
        return target

    def finish_batch(self, batch, activation):
        if not batch or batch['phase'] != 'restarting' or batch['boot'] == boot_id():
            return
        active = activation['active']
        scope_active = SCOPE_KEY in activation['community']
        for row in batch['modules']:
            if row['key'] == NATIVE_BATCH_KEY:
                present = scope_active and (not row.get('signed') or row['version'] == self.community[SCOPE_KEY].get('version'))
            else:
                present = row['key'] in active
            row['status'] = 'installed' if present else 'failed'
            if present:
                row['percent'] = 100
        if all(row['status'] == 'installed' for row in batch['modules']):
            if batch['native'] and batch['modules'][-1].get('signed'):
                self.discard_native(self.state())
            batch['phase'] = 'installed'
            self.batch.save(batch)
        else:
            self.batch.fail(batch, activation.get('error') or 'Hector could not load every selected module.')

    def review_removal(self, keys, revision):
        if not valid_keys(keys):
            raise Problem('Select the modules you want to remove.')
        with self.locked():
            state = self.checked_state(revision, 'remove')
            if keys == [SAMPLE_BOX_LEGACY_REVIEW] and self.community_removal_preview(self.native_inventory()):
                references = self.references({SAMPLE_BOX_LEGACY_REVIEW: {'uris': [SAMPLE_BOX_LEGACY_URI]}})
                return {'preview_only': True, 'references': references}
            if len(keys) == 1 and keys[0] in self.community:
                return self.review_community(state, keys[0], revision)
            return self._removal_review(state['target'], keys, revision)

    def community_reference(self, key):
        return {'uris': [member['uri'] for member in self.community[key]['members'].values()]}

    def review_community(self, state, key, revision):
        available = self.listing(state)['community_removal']
        if not any(item['key'] == key and item['status'] == 'installed' for item in available):
            raise Problem('This community package is no longer available for removal.', 409)
        return self._removal_review({key: self.community_reference(key)}, [key], revision)

    def change_community(self, state, action, key, revision, review):
        pending = state.setdefault('community_pending', {})
        if action == 'undo' and key in pending:
            del pending[key]
        elif action == 'remove':
            latest = self.review_community(state, key, revision)
            if review != latest['review']:
                raise Problem('Review the current list of affected presets before confirming removal.', 409)
            pending[key] = {'review': review, 'references': latest['references'][key]}
        else:
            raise Problem('This community package has no matching change.', 409)
        if not pending:
            state.pop('community_pending', None)
        self.save(state)
        return self.listing(state)

    def _removal_review(self, target, keys, revision):
        if not all(key in target for key in keys):
            raise Problem('A selected module is no longer available for removal.', 409)
        references = self.references({key: target[key] for key in keys})
        token = hashlib.sha256(canonical({'revision': revision, 'references': references})).hexdigest()
        return {'review': token, 'references': references}

    def change(self, action, keys, revision, review=None):
        if not valid_keys(keys) or action not in ('install', 'remove', 'discard', 'undo'):
            raise Problem('Select the modules you want to change.')
        with self.locked():
            state = self.checked_state(revision, action)
            if len(keys) == 1 and keys[0] in self.community:
                return self.change_community(state, action, keys[0], revision, review)
            if action == 'undo':
                pending = state['target'].keys() ^ state['applied'].keys()
                if not set(keys) <= pending:
                    raise Problem('A selected module no longer has a pending change.', 409)
                for key in keys:
                    if key in state['target']:
                        state['queued'][key] = state['target'].pop(key)
                    else:
                        state['target'][key] = state['applied'][key]
                        state.get('removal_reviews', {}).pop(key, None)
                self.save(state)
                return self.listing(state)
            source = state['target'] if action == 'remove' else state['queued']
            if not all(key in source for key in keys):
                raise Problem('A selected module is no longer available for that action.', 409)
            if action == 'remove':
                latest = self._removal_review(source, keys, revision)
                if review != latest['review']:
                    raise Problem('Review the current list of affected presets before confirming removal.', 409)
                state.setdefault('removal_reviews', {}).update(latest['references'])
            if action == 'install' and any(source[key].get('replaces') for key in keys):
                raise Problem('Use the install batch to apply an update.')
            if action=='install':
                import system_support
                for key in keys:system_support.requirement(source[key],self.firmware_root,policy=self.publishers)
            for key in keys:
                record = source.pop(key)
                if action == 'install':
                    state['target'][key] = record
            self.save(state)
            self.collect(state)
            return self.listing(state)

    def community_activation(self, state):
        removed = set(state.get('community_removed', []))
        pending = state.get('community_pending', {})
        errors = {}
        requested = {key: self.community_reference(key) for key in pending if key in self.community}
        try:
            startup = self.references(requested, startup=True)
            presets = self.references(requested)
            scan_error = None
        except (OSError, Problem) as exc:
            scan_error = str(exc)
        for key, approved in pending.items():
            try:
                if key not in self.community:
                    raise Problem('The verified plan for this community package is unavailable.')
                if not self.managed_community(self.community[key]):
                    raise Problem('This third-party USB module is outside Poly / View management. Undo the pending change.')
                if scan_error:
                    raise Problem(scan_error)
                self.community_assets(self.community[key])
                if startup[key]:
                    raise Problem('Removal held because Hector’s startup graph uses the module.')
                if set(presets[key]) - set(approved['references']):
                    raise Problem('A saved preset now uses this module. Undo the removal and review it again.')
                removed.add(key)
            except (OSError, Problem) as exc:
                errors[key] = {'review': approved['review'], 'error': str(exc)}
        active = {key: package for key, package in self.community.items() if key not in removed}
        return active, removed, errors

    def community_asset_path(self, asset):
        if asset.get('owned'):
            component(asset['path'])
            return self.root / asset['path']
        return Path(asset['path'])

    def community_files(self, package):
        return package['files'] if 'files' in package else {kind: package[kind] for kind in ('binary', 'metadata')}

    def community_assets(self, package):
        paths = {}
        for kind, asset in self.community_files(package).items():
            path = self.community_asset_path(asset)
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != asset['sha256']:
                raise Problem('The community package files differ from the verified release.')
            paths[kind] = path
        return paths

    def publish_community(self, bundle, directory, rows, catalog, active):
        directory.mkdir()
        for key, package in active.items():
            assets = self.community_assets(package)
            if 'files' in package:
                destination = directory / key
                destination.mkdir()
                for filename, source in assets.items():
                    component(filename)
                    (destination / filename).symlink_to(source)
                write_file(destination / 'manifest.ttl', package['manifest'].encode())
                common_index=package['manifest']
                for filename in assets:
                    uri=(self.runtime/'community'/key/filename).as_uri()
                    common_index=common_index.replace('<'+filename+'>','<'+uri+'>')
                rows.append(common_index+'\n')
                continue
            stem = 'community-' + key[:16]
            (bundle / (stem + '.so')).symlink_to(assets['binary'])
            (bundle / (stem + '.ttl')).symlink_to(assets['metadata'])
            for name, member in package['members'].items():
                catalog[name] = member
                rows.append(f"<{member['uri']}> a lv2:Plugin ; lv2:binary <{stem}.so> ; rdfs:seeAlso <{stem}.ttl> .\n")

    def publish_native_modules(self, directory, catalog, active):
        for key, module in active.items():
            if not native_module.is_native(module):
                continue
            bundle = directory / (key + '.lv2')
            bundle.mkdir()
            (bundle / 'module.so').symlink_to(self.root / (key + '.so'))
            (bundle / 'module.ttl').symlink_to(self.root / (key + '.pkg/module.ttl'))
            manifest = ('@prefix lv2: <http://lv2plug.in/ns/lv2core#> .\n'
                        '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n'
                        '<' + module['uri'] + '> a lv2:Plugin ; lv2:binary <module.so> ; rdfs:seeAlso <module.ttl> .\n')
            write_file(bundle / 'manifest.ttl', manifest.encode())
            catalog[native_module.effect_name(module)] = dict(uri=module['uri'], managed_native=True,
                    prototype=native_module.prototype(module, self.root, key))

    def collect_community(self, removed):
        for key in removed & self.community.keys():
            for asset in self.community_files(self.community[key]).values():
                if asset.get('owned'):
                    path = self.community_asset_path(asset)
                    if not path.is_symlink() and path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == asset['sha256']:
                        path.unlink()
                        sync_directory(self.root)

    def activate(self):
        """Run once before Hector's native module host starts."""
        with self.locked():
            receipt = self.runtime / 'activation.json'
            batch = self.batch.read()
            if receipt.is_file():
                self.finish_batch(batch, json.loads(receipt.read_bytes()))
                return
            state = self.state()
            apply_batch = batch and batch['phase'] == 'restarting' and batch['boot'] != boot_id()
            target = state['target']
            active = target
            error = None
            try:
                if apply_batch:
                    active = self.batch_targets(state, batch)
                active_ids = {module['id'] for module in active.values()}
                removing = {key: module for key, module in state['applied'].items()
                            if key not in active and module['id'] not in active_ids}
                if any(self.references(removing, startup=True).values()):
                    raise Problem('Removal held because Hector’s startup graph uses the module.')
                approved = state.get('removal_reviews', {})
                for key, presets in self.references(removing).items():
                    new = set(presets) - set(approved.get(key, []))
                    if new:
                        name = min(new).split('/', 1)[1]
                        raise Problem('Removal held because ' + name + ' now uses the module. Undo the removal and review it again.')
                for key, module in active.items():
                    if key not in state['applied']:
                        self.verify_publisher(module)
                        import system_support
                        system_support.requirement(module,self.firmware_root,policy=self.publishers)
                    if native_module.is_native(module):
                        self.check_native_identity(module, state, active)
                        native_module.verify_files(self.root, key, module)
                    if hashlib.sha256((self.root / (key + '.so')).read_bytes()).hexdigest() != module['binary_sha256']:
                        raise Problem('A staged module failed its integrity check.')
            except (OSError, Problem) as exc:
                active = state['applied']
                error = str(exc)
            community, removed, community_errors = self.community_activation(state)
            migrations = self.migrations.applied(self, active, state)
            # Retire only the duplicate registration. Its original owned files
            # remain available; no saved-preset removal review is bypassed.
            community = {key: value for key, value in community.items() if key not in migrations}
            self.runtime.mkdir(mode=0o755, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=self.runtime, prefix='.catalog-') as temp:
                bundle = Path(temp) / 'lv2'
                bundle.mkdir(mode=0o755)
                rows = ['@prefix lv2: <http://lv2plug.in/ns/lv2core#> .\n@prefix doap: <http://usefulinc.com/ns/doap#> .\n'
                        '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n']
                catalog = {}
                for key, module in active.items():
                    if native_module.is_native(module):
                        continue
                    filename = key + '.so'
                    (bundle / filename).symlink_to(self.root / filename)
                    rows.append(turtle(module, filename))
                    effect = 'poly_view_' + module['id'].replace('-', '_')
                    catalog[effect] = {'uri': PREFIX + module['id'], 'prototype': prototype(module)}
                directories = Path(temp) / 'community'
                self.publish_community(bundle, directories, rows, catalog, community)
                self.publish_native_modules(directories, catalog, active)
                # Ingen scans the stable common .lv2 link. Include native
                # manifests there too, so each new package needs no root link
                # or global LV2_PATH change. Metadata resolves its module.so
                # relative to the published runtime bundle, as signed.
                for key,module in active.items():
                    if native_module.is_native(module):
                        binary=(self.root/(key+'.so')).as_uri()
                        metadata=(self.runtime/'community'/(key+'.lv2')/'module.ttl').as_uri()
                        rows.append('<'+module['uri']+'> a lv2:Plugin ; lv2:binary <'+binary+'> ; rdfs:seeAlso <'+metadata+'> .\n')
                write_file(bundle / 'manifest.ttl', ''.join(rows).encode())
                destination = self.runtime / 'lv2'
                if destination.exists():
                    shutil.rmtree(destination)
                os.replace(bundle, destination)
                destination = self.runtime / 'community'
                if destination.exists():
                    shutil.rmtree(destination)
                os.replace(directories, destination)
            publish(self.runtime / 'catalog.json', canonical(catalog))
            publish(self.runtime / 'native-editors.json', canonical({native_module.effect_name(module):
                    dict(root=str(self.root / (key + '.pkg/editor')), backend=module['editor'].get('backend'),
                         lifecycle=module.get('requirements',{}).get('native_editor_lifecycle'),
                         **({'support':module['support']} if module.get('support') else {}))
                    for key, module in active.items() if native_module.is_native(module)}))
            suppressed = sorted(name for key in (removed | migrations.keys()) & self.community.keys()
                                if 'files' in self.community[key] for name in self.community[key]['members'])
            publish(self.runtime / 'community-removed.json', canonical(suppressed))
            managed_changed = state['applied'] != active
            community_changed = set(state.get('community_removed', [])) != removed
            if managed_changed:
                state['applied'] = active
                state.pop('removal_reviews', None)
                if apply_batch and error is None:
                    state['target'] = active
                    for row in batch['modules']:
                        state['queued'].pop(row['key'], None)
            if community_changed:
                state['community_removed'] = sorted(removed)
                pending = {key: record for key, record in state.get('community_pending', {}).items()
                           if key not in removed}
                if pending:
                    state['community_pending'] = pending
                else:
                    state.pop('community_pending', None)
            migration_changed = state.get('community_migrations', {}) != migrations
            if migration_changed:
                state['community_migrations'] = migrations
            if managed_changed or community_changed or migration_changed:
                self.save(state)
            self.collect_community(removed)
            activation = dict(active=active, error=error, plan=activation_plan(state),
                              community=list(community), community_errors=community_errors)
            publish(receipt, canonical(activation))
            self.finish_batch(batch, activation)
            self.collect(state)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--activate', action='store_true', required=True)
    parser.parse_args()
    os.umask(0o077)
    ModuleManager(DATA, RUNTIME, '/mnt/presets', 32 * MIB, '/home/debian/start_up.ingen',
                  publishers=PublisherPolicy()).activate()
