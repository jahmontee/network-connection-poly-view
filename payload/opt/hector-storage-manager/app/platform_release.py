"""Signed platform data; browser uploads never supply executable installers."""
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import zipfile

from core import MIB, Problem, safe_parts
from publisher_release import PublisherPolicy, canonical, extract_proof

LEGACY_SCHEMA='poly-view-hector-platform/1'
SCHEMA='poly-view-hector-platform/2'
ID='network-connection-poly-view'
CORE_ID='poly-view-module-runtime'
OWNER='26e23e5cf387aed68bfd58bdf366f602760acadb072e7609224387e29f862a31'
LIMIT=16*MIB
VERSION=Path('/opt/hector-storage-manager/platform-version.json')
STAGED='platform-update.zip'
PROGRESS=Path('/run/hector-platform-progress.json')


def sha(raw):return hashlib.sha256(raw).hexdigest()


def archive_files(raw):
    if not 0<len(raw)<=LIMIT:raise Problem('Choose a platform ZIP of up to 16 MiB.',413)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries=archive.infolist();files={};total=0
            if len(entries)>512:raise Problem('The platform archive has too many files.')
            for entry in entries:
                safe_parts(entry.filename)
                if (entry.filename in files or entry.orig_filename!=entry.filename or entry.is_dir()
                        or stat.S_IFMT(entry.external_attr>>16) not in (0,stat.S_IFREG)
                        or entry.flag_bits&1 or entry.compress_type not in (0,8)
                        or not 0<entry.file_size<=8*MIB):
                    raise Problem('The platform archive contains an unsupported file.')
                total+=entry.file_size
                if total>32*MIB:raise Problem('The unpacked platform archive exceeds its limit.')
                files[entry.filename]=archive.read(entry)
            return files
    except (zipfile.BadZipFile,ValueError,RuntimeError,EOFError,OSError) as error:
        raise Problem('The platform ZIP could not be read.') from error


def is_platform(raw):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:return 'platform.json' in archive.namelist()
    except zipfile.BadZipFile:return False


def target(name):
    safe_parts(name)
    return (name.startswith(('opt/hector-storage-manager/','home/debian/UI/','usr/local/lib/hector-network-panel/',
                            'usr/local/lib/hector-ethernet-panel/','usr/local/lib/hector-wifi-manager/'))
            or bool(re.fullmatch(r'etc/systemd/system/hector-(?:storage|module|remote|platform|repository|network|ethernet|wifi)[A-Za-z0-9@_.-]*\.(?:service|socket)',name))
            or name in ('etc/tmpfiles.d/hector-module-catalog.conf',
                        'etc/systemd/system/multi-user.target.d/95-poly-view-platform.conf',
                        'etc/systemd/system/jack.service.d/95-poly-view-modules.conf',
                        'etc/systemd/system/lightdm.service.d/95-poly-view-modules.conf',
                        'usr/local/lib/poly-view-platform-recovery.py',
                        'usr/lib/firmware/ath9k_htc/htc_9271-1.4.0.fw',
                        'usr/share/doc/network-connection-poly-view/ATH9K-HTC-LICENSE.txt')
            or name=='usr/lib/lv2/hector-link-sync.lv2/manifest.ttl'
            or name in {'usr/local/lib/hector-link-sync/'+leaf for leaf in ('backend.py','client.py','helper','control')}
            or name in {'etc/systemd/system/hector-link-sync-'+kind+'.service' for kind in ('control','ethernet','wifi')}
            or bool(re.fullmatch(r'usr/lib/lv2/(?:mala|poly303)\.lv2/(?:mala\.so|mala\.ttl|poly303\.so|poly303\.ttl|manifest\.ttl|LICENSE|NOTICE|UPSTREAM-LICENSES)',name)))


def validate_manifest(manifest):
    if (manifest.get('schema')!='four-by-four-hector/guarded/1' or manifest.get('hardware')!='Hector'
            or manifest.get('firmware')!='407' or manifest.get('architecture')!='aarch64'
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.+-]{0,47}',manifest.get('version',''))):
        raise ValueError('Unsupported platform target')
    rows=manifest['files'];names=[row['path'] for row in rows]
    if not 0<len(rows)<=256 or len(names)!=len(set(names)) or any(not target(name) for name in names):
        raise ValueError('Unsupported platform destination')
    digest=lambda value:isinstance(value,str) and re.fullmatch('[a-f0-9]{64}',value)
    for row in rows:
        if (not digest(row['sha256']) or type(row['size']) is not int or not 0<row['size']<=8*MIB
                or row['before_sha256'] is not None and not digest(row['before_sha256'])):
            raise ValueError('Invalid platform file record')
        if row['path']=='usr/local/lib/poly-view-platform-recovery.py' and row['before_sha256'] is not None:
            raise ValueError('The fixed recovery entry cannot be replaced by an app upload')
    guards=manifest['guards']
    if not isinstance(guards,dict) or set(guards)&set(names):raise ValueError('Invalid platform guards')
    for name,value in guards.items():
        safe_parts(name)
        if not digest(value):raise ValueError('Invalid preserved checksum')
    if set(manifest['require_absent'])!={row['path'] for row in rows if row['before_sha256'] is None}:
        raise ValueError('Invalid new-file contract')
    if type(manifest['published_mtime_ns']) is not int or manifest['published_mtime_ns']<1:
        raise ValueError('Invalid publication timestamp')


def read(raw,policy=None,owner=OWNER):
    files=archive_files(raw);proof=extract_proof(files)
    try:module_id=json.loads(files['platform.json'])['id']
    except (KeyError,TypeError,ValueError) as error:raise Problem('Invalid platform manifest.') from error
    if module_id not in (ID,CORE_ID):raise Problem('Unsupported platform identity.')
    publisher,checksums=(policy or PublisherPolicy()).verify_manifest(proof,module_id,native=True,platform=True)
    if publisher!=owner:raise Problem('Only the Poly / View platform publisher can update this app.')
    if checksums!={name:sha(content) for name,content in files.items()}:
        raise Problem('The platform files differ from the signed release.')
    try:
        info=json.loads(files['platform.json'])
        if (set(info)!={'schema','id','name','version','target','profiles'} or info['schema'] not in (LEGACY_SCHEMA,SCHEMA)
                or info['id']!=module_id or info['target']!='hector-407-aarch64'
                or not isinstance(info['profiles'],list) or not 0<len(info['profiles'])<=(256 if info['schema']==SCHEMA else 128)):
            raise ValueError('Unsupported platform release')
        expected={'platform.json','debug.sh','poly-view/install.py','READ-ME-FIRST.txt'}
        for manifest in info['profiles']:
            validate_manifest(manifest)
            if manifest['version']!=info['version']:raise ValueError('Platform versions disagree')
            for row in manifest['files']:
                name='payload/'+row['sha256'];content=files[name];expected.add(name)
                if len(content)!=row['size'] or sha(content)!=row['sha256']:raise ValueError('Platform payload differs')
        if set(files)!=expected:raise ValueError('Unexpected platform release file')
    except (KeyError,TypeError,ValueError,AttributeError) as error:
        raise Problem('Incompatible platform package: '+str(error)) from error
    return info,files,proof


def select(info,files,root):
    """Choose an exact installed profile before any writes, without network."""
    from platform_transaction import file_hash,safe
    matches=[]
    for manifest in info['profiles']:
        expected=dict(manifest['guards'],**{row['path']:row['before_sha256'] for row in manifest['files']})
        if all(file_hash(safe(root,name))==value for name,value in expected.items()):matches.append(manifest)
    if len(matches)!=1:raise Problem('This release does not match the installed app. No files were changed.',409)
    manifest=json.loads(canonical(matches[0]));payload={row['path']:files['payload/'+row['sha256']] for row in manifest['files']}
    return manifest,payload
