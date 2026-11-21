"""Verify signed module packages against Poly / View's local publisher policy."""
import base64
import binascii
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import zipfile

from core import MIB, Problem, safe_parts


POLICY = Path(__file__).with_name('publishers.json')
MANIFEST = 'publisher.json'
SIGNATURE = 'publisher.sig'
SCHEMA = 'poly-view-publisher/1'
LIMITS = {'module.json': 24 * 1024, 'module.so': 8 * MIB, 'LICENSE': 64 * 1024}
ENVELOPE_LIMIT = 4096
NATIVE_ENVELOPE_LIMIT = 32768
ED25519_SPKI = bytes.fromhex('302a300506032b6570032100')


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def decode(text):
    return base64.b64decode(text, validate=True)


def archive_files(raw, limits, required):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            names = {entry.filename for entry in entries}
            if len(entries) != len(names) or not required <= names or names - limits.keys():
                raise Problem('This ZIP does not contain the expected release files.')
            files = {}
            for entry in entries:
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if (mode not in (0, stat.S_IFREG) or entry.orig_filename != entry.filename
                        or entry.flag_bits & 1 or not 0 < entry.file_size <= limits[entry.filename]
                        or entry.compress_type not in (zipfile.ZIP_DEFLATED, zipfile.ZIP_STORED)):
                    raise Problem('The release ZIP contains an unsupported file or exceeds the size limit.')
                files[entry.filename] = archive.read(entry)
            return files
    except (zipfile.BadZipFile, ValueError, RuntimeError, EOFError, OSError) as error:
        raise Problem('The release ZIP could not be read.') from error


def native_archive_files(raw):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > 128:
                raise Problem('The native package contains too many files.')
            names, limits, total = set(), {}, 0
            for entry in entries:
                safe_parts(entry.filename)
                mode = stat.S_IFMT(entry.external_attr >> 16)
                if (entry.filename in names or entry.orig_filename != entry.filename or entry.flag_bits & 1
                        or entry.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                        or mode not in (0, stat.S_IFDIR if entry.is_dir() else stat.S_IFREG)
                        or entry.file_size > 8 * 1024 * 1024 or (entry.is_dir() and entry.file_size)):
                    raise Problem('The native package contains an unsupported archive entry.')
                names.add(entry.filename)
                if not entry.is_dir():
                    limits[entry.filename] = entry
                    total += entry.file_size
            if total > 16 * 1024 * 1024:
                raise Problem('The native package exceeds the unpacked size limit.')
            if (MANIFEST in limits and limits[MANIFEST].file_size > NATIVE_ENVELOPE_LIMIT
                    or SIGNATURE in limits and limits[SIGNATURE].file_size != 64):
                raise Problem('Invalid publisher signature size.')
            return {name: archive.read(entry) for name, entry in limits.items()}
    except (zipfile.BadZipFile, ValueError, RuntimeError, EOFError, OSError) as error:
        raise Problem('The native release ZIP could not be read.') from error


def extract_proof(files):
    try:
        return {'manifest': base64.b64encode(files.pop(MANIFEST)).decode(),
                'signature': base64.b64encode(files.pop(SIGNATURE)).decode()}
    except KeyError as error:
        raise Problem('Upload a signed module ZIP from a verified developer.') from error


def verify_signature(public_key, message, signature):
    with tempfile.TemporaryDirectory(prefix='poly-view-signature-') as temp:
        directory = Path(temp)
        (directory / 'public.der').write_bytes(public_key)
        (directory / 'manifest').write_bytes(message)
        (directory / 'signature').write_bytes(signature)
        result = subprocess.run(
            ['/usr/bin/openssl', 'pkeyutl', '-verify', '-pubin', '-keyform', 'DER', '-inkey',
             'public.der', '-rawin', '-in', 'manifest', '-sigfile', 'signature'],
            cwd=directory, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=5, check=False)
        if result.returncode:
            raise Problem('The publisher signature did not verify.')


class PublisherPolicy:
    def __init__(self, path=POLICY):
        self.path = Path(path) if path else None

    def keys(self):
        if self.path is None:
            return {}
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as source:
                info = os.fstat(source.fileno())
                allowed_owner = (0,) if self.path == POLICY else (0, os.getuid())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid not in allowed_owner
                        or info.st_mode & 0o022 or not 0 < info.st_size <= 65536):
                    raise ValueError('Invalid publisher policy permissions.')
                policy = json.loads(source.read(65537))
            if policy.get('schema') != SCHEMA or not isinstance(policy.get('keys'), dict):
                raise ValueError('Invalid publisher policy.')
            return policy['keys']
        except (OSError, ValueError, AttributeError) as error:
            raise Problem('The verified publisher list is unavailable.', 503) from error

    def verify_manifest(self, proof, module_id, native=False,platform=False):
        try:
            raw = decode(proof['manifest'])
            signature = decode(proof['signature'])
            limit = 128*1024 if platform and module_id in ('network-connection-poly-view','poly-view-module-runtime') else NATIVE_ENVELOPE_LIMIT if native else ENVELOPE_LIMIT
            if not 0 < len(raw) <= limit or len(signature) != 64:
                raise ValueError('Invalid envelope size')
            manifest = json.loads(raw)
            if (set(manifest) != {'schema', 'key', 'module', 'target', 'files'}
                    or manifest['schema'] != SCHEMA or raw != canonical(manifest)
                    or not re.fullmatch('[a-f0-9]{64}', manifest['key'])):
                raise ValueError('Invalid envelope')
            if manifest['target'] != 'hector-407-aarch64' or manifest['module'] != module_id:
                raise Problem('The signed package targets a different module or firmware.')
            files = manifest['files']
            if (not isinstance(files, dict)
                    or any(not re.fullmatch('[a-f0-9]{64}', checksum) for checksum in files.values())):
                raise Problem('The module files differ from the signed release.')
            publisher = self.keys().get(manifest['key'])
            if publisher is None:
                raise Problem('This developer has not been verified for Poly / View.')
            public_key = decode(publisher['key'])
            allowed = publisher.get('native_modules', []) if native else publisher['modules']
            if (len(public_key) != len(ED25519_SPKI) + 32 or not public_key.startswith(ED25519_SPKI)
                    or sha256(public_key) != manifest['key'] or not isinstance(allowed, list)):
                raise Problem('The verified publisher list is unavailable.', 503)
            if '*' not in allowed and module_id not in allowed:
                raise Problem('This developer is not verified for this module.')
        except (ValueError, TypeError, KeyError, binascii.Error) as error:
            raise Problem('The publisher signature could not be read.') from error
        try:
            verify_signature(public_key, raw, signature)
        except (OSError, subprocess.SubprocessError) as error:
            raise Problem('The publisher signature could not be checked.', 503) from error
        return manifest['key'], files

    def verify(self, proof, module_id, module_checksum, binary_checksum):
        owner, files = self.verify_manifest(proof, module_id)
        if (files.keys() - LIMITS.keys() or files.get('module.json') != module_checksum
                or files.get('module.so') != binary_checksum):
            raise Problem('The module files differ from the signed release.')
        return owner, files

    def read(self, raw):
        limits = LIMITS | {MANIFEST: ENVELOPE_LIMIT, SIGNATURE: 64}
        files = archive_files(raw, limits, {'module.json', 'module.so', MANIFEST, SIGNATURE})
        proof = extract_proof(files)
        try:
            module = json.loads(files['module.json'])
            if not isinstance(module, dict) or files['module.json'] != canonical(module):
                raise ValueError('Invalid module manifest')
            owner, checksums = self.verify(proof, module['id'], sha256(files['module.json']), sha256(files['module.so']))
        except (TypeError, ValueError, KeyError) as error:
            raise Problem('The signed module manifest could not be read.') from error
        if checksums != {name: sha256(content) for name, content in files.items()}:
            raise Problem('The module files differ from the signed release.')
        return module, files['module.so'], {'publisher': owner, 'publisher_proof': proof}
