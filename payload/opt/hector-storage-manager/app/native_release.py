"""Admission for the reviewed AetherScope USB release."""
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

from core import Problem
from publisher_release import PublisherPolicy, extract_proof, native_archive_files

ARCHIVE = 'AetherScope-Hector-0.1.0-test.13-USB.zip'
ARCHIVE_SHA256 = '58457ec507a30189f5ccad5d2e0109cda48c7cc139e537cf329e0360ed38470e'
ARCHIVE_SIZE = 939548
URI = 'urn:beebo-hector-lab:oscilloscope'
SCOPE_KEY = hashlib.sha256(URI.encode()).hexdigest()
CATALOG = 'opt/hector-storage-manager/app/community-catalog.json'
POLICY = 'opt/hector-storage-manager/native/bridge.json'
STORE = Path('/mnt/community-lab/hector-aetherscope-browser-test13')
STAGED_ARCHIVE = 'native-aetherscope-test13.zip'
SIGNED_ARCHIVE = 'native-publisher.zip'
SIGNED_STORE = STORE.with_name('hector-aetherscope-publisher')
BUSY_MARKER = Path('/run/hector-module-restarting')
PACKAGE_PREFIX = 'aetherscope/package/'
NATIVE_ID = 'oscilloscope'
UI = 'home/debian/UI/'
SHARED_SCOPE_FILES = {CATALOG, UI + 'module_info.py', UI + 'qml/module_info.js'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def matches(raw):
    return len(raw) == ARCHIVE_SIZE and sha(raw) == ARCHIVE_SHA256


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def verified_release(raw):
    if not matches(raw):
        raise ValueError('Choose the original, verified AetherScope test.13 USB ZIP.')
    # ZIP members are read into memory. Only the named firmware and LV2 data
    # below go to the trusted transaction. USB scripts never enter the worker.
    prefix = 'aetherscope/package/'
    with zipfile.ZipFile(io.BytesIO(raw)) as package:
        manifest = json.loads(package.read(prefix + 'manifest.json'))
        validation = json.loads(package.read(prefix + 'validation.json'))
        if (manifest['version'] != '0.1.0-test.13'
                or validation['status'] != 'pass'
                or validation['manifest_sha256'] != sha(canonical(manifest))):
            raise ValueError('The AetherScope release verification failed.')
        payload = {}
        for row in manifest['files']:
            name = row['path']
            content = package.read(prefix + 'payload/' + name)
            if len(content) != row['size'] or sha(content) != row['sha256']:
                raise ValueError('AetherScope payload verification failed: ' + name)
            payload[name] = content
        if validation['tested_files'] != dict(manifest['guards'], **{name: sha(content)
                                                                  for name, content in payload.items()}):
            raise ValueError('The AetherScope payload differs from the tested release.')
        scope = manifest['managed_scope']
        assets = {}
        for field, checksum in (('binary', 'binary_sha256'), ('metadata', 'next_sha256')):
            name = scope[field]
            assets[name] = package.read(prefix + 'scope/' + name)
            if sha(assets[name]) != scope[checksum]:
                raise ValueError('AetherScope LV2 verification failed: ' + name)
        if scope['uri'] != URI:
            raise ValueError('Unexpected AetherScope plugin ID.')
    return manifest, payload, assets


def native_zip(raw):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as package:
            return PACKAGE_PREFIX + 'manifest.json' in package.namelist()
    except zipfile.BadZipFile:
        return False


def native_target(path):
    return path in SHARED_SCOPE_FILES or path in {UI + 'oscilloscope_backend.py', UI + 'oscilloscope_host.py'} or bool(
        re.fullmatch(UI + r'(?:qml/oscilloscope/[A-Za-z][A-Za-z0-9_]*\.qml|oscilloscope_data/controls\.json)', path))


def checksum(value):
    return isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value)


def signed_release(raw, policy=None):
    files = native_archive_files(raw)
    proof = extract_proof(files)
    publisher, signed = (policy or PublisherPolicy()).verify_manifest(proof, NATIVE_ID, native=True)
    if signed != {name: sha(content) for name, content in files.items()}:
        raise Problem('The native package differs from the signed release.')
    try:
        manifest = json.loads(files[PACKAGE_PREFIX + 'manifest.json'])
        validation = json.loads(files[PACKAGE_PREFIX + 'validation.json'])
        if ((manifest['schema'], manifest['hardware'], manifest['firmware'], manifest['architecture'])
                != ('four-by-four-hector/guarded/1', 'Hector', '407', 'aarch64')
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+-]{0,47}', manifest['version'])
                or validation['status'] != 'pass' or validation['manifest_sha256'] != sha(canonical(manifest))):
            raise ValueError('Unsupported firmware or missing release validation')
        payload = {}
        absent = []
        for row in manifest['files']:
            path = row['path']
            if not native_target(path) or path in payload:
                raise ValueError('Unexpected native module destination')
            prior = row['before_sha256']
            if prior is None and row['before_metadata'] is None and path not in SHARED_SCOPE_FILES:
                absent.append(path)
            elif not checksum(prior) or not isinstance(row['before_metadata'], dict):
                raise ValueError('Missing native update baseline')
            content = files[PACKAGE_PREFIX + 'payload/' + path]
            if len(content) != row['size'] or sha(content) != row['sha256']:
                raise ValueError('Native payload differs from the release manifest')
            payload[path] = content
        if (CATALOG not in payload or set(payload) & manifest['guards'].keys()
                or sorted(manifest['require_absent']) != sorted(absent)
                or set(manifest['guard_metadata']) != set(manifest['guards'])
                or validation['tested_files'] != dict(manifest['guards'], **{name: sha(content) for name, content in payload.items()})):
            raise ValueError('Native package validation is incomplete')
        info = manifest['managed_scope']
        assets = {}
        if info['uri'] != URI:
            raise ValueError('Unexpected native module URI')
        for label, extension, before, after in (
                ('binary', 'so', 'before_binary_sha256', 'binary_sha256'),
                ('metadata', 'ttl', 'before_sha256', 'next_sha256')):
            name = 'community-' + SCOPE_KEY[:16] + '-oscilloscope.' + extension
            if info[label] != name or not checksum(info[before]):
                raise ValueError('Unexpected native asset destination or missing baseline')
            content = files[PACKAGE_PREFIX + 'scope/' + name]
            if sha(content) != info[after]:
                raise ValueError('Native module asset differs from the manifest')
            assets[name] = content
        if assets[info['binary']][:7] != b'\x7fELF\x02\x01\x01' or assets[info['binary']][16:24] != b'\x03\x00\xb7\x00\x01\x00\x00\x00':
            raise ValueError('Native module is not an ARM64 shared library')
    except (AttributeError, KeyError, TypeError, ValueError) as error:
        raise Problem('Incompatible native module release. ' + str(error)) from error
    return manifest, payload, assets, publisher


def job_status(store=STORE, marker=BUSY_MARKER, runtime=Path('/run/hector-module-catalog')):
    if marker.exists():
        return {'state': 'working'}
    try:
        job = json.loads((store / 'browser-job.json').read_bytes())
    except FileNotFoundError:
        return {'state': 'ready'}
    token = job.get('token', '')
    if not re.fullmatch('[a-f0-9]{32}', token):
        return {'state': 'unavailable'}
    result = job_result(store, token)
    if result is None:
        return {'state': 'failed', 'message': job.get('error', 'The previous installation did not finish.')}
    status = result['status']
    if status == 'installed-reboot-required':
        try:
            active = json.loads((runtime / 'activation.json').read_bytes())
        except FileNotFoundError:
            return {'state': 'working'}
        if SCOPE_KEY in active.get('community', []):
            return {'state': 'installed', **({'installed_version': job['version']} if 'version' in job else {})}
        return {'state': 'removed'}
    if status == 'restored-reboot-required':
        return {'state': 'rolled-back'}
    if status == 'recovery-required':
        return {'state': 'recovery-required', 'message': 'The installer needs recovery before another change.'}
    return {'state': 'failed', 'message': job.get('error') or result.get('error') or 'The installer stopped.'}


def installed_job_status():
    if (SIGNED_STORE / 'browser-job.json').is_file():
        status = job_status(SIGNED_STORE)
        if status['state'] != 'rolled-back':
            return status
    return job_status()


def job_result(store, token):
    folder = store / 'transactions' / token
    try:
        return json.loads((folder / 'result.json').read_bytes())
    except FileNotFoundError:
        pass
    try:
        phase = json.loads((folder / 'journal.json').read_bytes())['content']['phase']
    except FileNotFoundError:
        return None
    if phase in ('publishing', 'committed', 'restoring'):
        return {'status': 'recovery-required'}
    return None
