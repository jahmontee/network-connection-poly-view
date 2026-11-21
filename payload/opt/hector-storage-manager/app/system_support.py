# SPDX-License-Identifier: MIT
"""Trusted, separately owned flash repair. Uploaded archives are data only."""
import ast
import base64
import fcntl
import gzip
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parent))
from core import Problem
from publisher_release import PublisherPolicy,canonical,extract_proof,native_archive_files,sha256
import system_support_contract as contract
import system_support_transaction as tr

ID='hector-flash-rx-fix'
CAPABILITY='flash-rx-support-1'
PRESERVATION='preset-preservation-1'
PAIRED_VERSION='0.1.0-spi-rx.9'
SPI_SHA='f17184df4b6a8e4f75dcce5062e3bb43cadb0b325faa180c74c95a127cea869e'
FS_SHA='4e2b9264417a9859481c0b789d7c008a430c53b6d462c9f9b95654fe10fd8049'
FS_NOTE='040000001400000003000000474e5500596f746eb07055e6e5a23d013e0810da21437a5d'
OWNER='26e23e5cf387aed68bfd58bdf366f602760acadb072e7609224387e29f862a31'
BASE='usr/local/lib/hector-flash-rx-fix/'
DATA='mnt/community-lab/hector-flash-rx-fix'
BUNDLE='opt/hector-storage-manager/app/flash-repair.zip'
UNIT='etc/systemd/system/hector-platform-flash-bootstrap.service'
APP=Path(__file__).resolve().parent
LINK_IDS={'hector-link-sync','ableton-link-sync','ableton-link-audio-send','ableton-link-audio-receive'}

def version(value):
    match=re.fullmatch(r'(\d+)\.(\d+)\.(\d+)-spi-rx\.(\d+)',value)
    if not match:raise ValueError('Unrecognized flash repair version')
    return tuple(map(int,match.groups()))

def constants(raw):
    values={}
    for node in ast.parse(raw).body:
        if isinstance(node,ast.Assign):
            for name in node.targets:
                if isinstance(name,ast.Name) and name.id in ('VERSION','MODULE_SHA','CONFIG_SHA'):
                    values[name.id]=ast.literal_eval(node.value)
    if set(values)!={'VERSION','MODULE_SHA','CONFIG_SHA'}:raise ValueError('Incomplete signed flash repair identity')
    version(values['VERSION']);return values

def verify_payload(payload,policy):
    if set(payload) not in (set(contract.FILES),set(contract.LEGACY_FILES)):raise Problem('The flash repair file inventory is incompatible.')
    proof=json.loads(payload[BASE+'publisher-proof.json'])
    owner,hashes=policy.verify_manifest(proof,ID,native=True)
    if owner!=OWNER or hashes!={n:sha256(v) for n,v in payload.items() if n!=BASE+'publisher-proof.json'}:
        raise Problem('Only the platform publisher can supply the shared flash repair.')
    values=constants(payload[BASE+'boot.py'])
    if sha256(payload[BASE+'hector_sun6i_rx_fix.ko'])!=values['MODULE_SHA']:
        raise Problem('The flash driver differs from its signed boot contract.')
    if set(payload)==set(contract.FILES):
        if values['MODULE_SHA']!=SPI_SHA or sha256(payload[BASE+'jffs2-preserve-read-errors.ko'])!=FS_SHA:raise Problem('The paired storage correction differs from the reviewed kernel objects.')
    elif values['VERSION'] not in contract.LEGACY_VERSIONS:raise Problem('Unknown legacy storage correction.')
    return values

def read(raw,policy):
    if not 0<len(raw)<512*1024:raise Problem('The flash repair package exceeds its limit.')
    files=native_archive_files(raw);proof=extract_proof(files)
    owner,hashes=policy.verify_manifest(proof,ID,native=True)
    if owner!=OWNER or hashes!={n:sha256(v) for n,v in files.items()}:
        raise Problem('The shared flash repair did not verify with the platform publisher.')
    try:
        info=json.loads(files.pop('component.json'))
        if (set(info)!={'schema','id','version','api_version','kernel_release','config_sha256','module_sha256'}
                or info['schema']!='poly-view-system-component/1' or info['id']!=ID or info['api_version']!=1):
            raise ValueError('Unsupported component descriptor')
        values=verify_payload(files,policy)
        if (info['version']!=values['VERSION'] or info['module_sha256']!=values['MODULE_SHA']
                or info['config_sha256']!=values['CONFIG_SHA'] or info['kernel_release']!='5.4.2-rt5-001'):
            raise ValueError('Component identity differs')
    except (KeyError,TypeError,ValueError) as exc:raise Problem('Incompatible flash repair package.') from exc
    return info,files

def kernel_host(root,config_sha):
    """Immutable target checks available before preset flash is mounted."""
    root=Path(root)
    if (platform.machine() not in ('aarch64','arm64')
            or (root/'proc/sys/kernel/osrelease').read_text().strip()!='5.4.2-rt5-001'
            or sha256(gzip.decompress((root/'proc/config.gz').read_bytes()))!=config_sha
            or b'pine64,sopine\0' not in (root/'proc/device-tree/compatible').read_bytes()):
        raise Problem('This flash repair requires the reviewed Hector firmware407 kernel. No repair files changed.',409)


def host(root,config_sha):
    """Late install/mutation admission also requires the mounted Hector model."""
    root=Path(root);kernel_host(root,config_sha)
    if json.loads((root/'mnt/pedal_state/state.json').read_text()).get('model')!='hector':
        raise Problem('This flash repair requires the reviewed Hector firmware407 kernel. No repair files changed.',409)


def installed(root,policy,firmware=None,*,pre_nor=False):
    root=Path(root);firmware=Path(firmware) if firmware is not None else root
    names=[n for n in contract.FILES if os.path.lexists(tr.safe(firmware,n))]
    if not names:return None
    if len(names)==len(contract.FILES):inventory=contract.FILES
    elif set(names)==set(contract.LEGACY_FILES):inventory=contract.LEGACY_FILES
    else:raise Problem('A partial flash repair requires recovery before installation.',409)
    payload={n:tr.regular(tr.safe(firmware,n),256*1024) for n in inventory}
    values=verify_payload(payload,policy)
    folder=tr.safe(root,DATA);owner=0 if root==Path('/') else os.geteuid()
    st=folder.lstat()
    if st.st_uid!=owner or st.st_mode&0o077:raise Problem('Unprotected flash repair state.',409)
    state=json.loads(tr.regular(tr.safe(root,DATA+'/state.json'),4096))
    if state['version']!=values['VERSION'] or state['module_sha256']!=values['MODULE_SHA']:
        raise Problem('The protected flash repair state differs from its signed files.',409)
    # Early() has already authenticated the component's SD recovery intent
    # against this physical card/device. NOR model data is unavailable there.
    if pre_nor:kernel_host(root,values['CONFIG_SHA'])
    else:host(root,values['CONFIG_SHA'])
    return dict(version=values['VERSION'],enabled=state['enabled'],state=state,preset_preservation=set(payload)==set(contract.FILES))

def requirement(module,root,*,staging=False,policy=None):
    """Check dependencies on upload AND again at installation; never for removal."""
    root=Path(root);requested=module.get('requirements')
    if module.get('id') in LINK_IDS or (requested or {}).get('network_connection'):
        path=tr.safe(root,'opt/hector-storage-manager/platform-version.json')
        try:net=json.loads(tr.regular(path,4096))
        except FileNotFoundError:net={}
        if net.get('id')!='network-connection-poly-view':
            raise Problem('Install compatible Network Connection before installing this Link module.',409)
        for name in ('usr/local/lib/hector-network-panel/client.py','etc/systemd/system/hector-network-panel.service'):
            if not tr.safe(root,name).is_file():raise Problem('Network Connection is incomplete. Repair it before installing Link.',409)
    if requested and not staging:
        marker=tr.safe(root,'opt/hector-storage-manager/runtime-version.json')
        try:runtime=json.loads(tr.regular(marker,4096))
        except FileNotFoundError:runtime={}
        if requested.get('midi_bound_recall') and 'midi-bound-recall-1' not in runtime.get('capabilities',[]):
            raise Problem('Update Network Connection / Poly / View or use the complete USB installer to restore MIDI-mapped controls.',409)
        if requested.get('native_editor_lifecycle') and 'native-editor-lifecycle-1' not in runtime.get('capabilities',[]):
            raise Problem('Update Network Connection / Poly / View or use the complete USB installer to enable this module’s controls.',409)
        if requested.get('preset_preservation') and PRESERVATION not in runtime.get('capabilities',[]):raise Problem('Install the current Network Connection / Poly / View USB release before installing this module.',409)
        if requested.get('repair_readiness') and 'repair-readiness-1' not in runtime.get('capabilities',[]):
            raise Problem('Update Network Connection / Poly / View or use this complete USB installer before installing this module.',409)
        if CAPABILITY not in runtime.get('capabilities',[]):
            raise Problem('Update Network Connection or use this complete USB installer to enable shared flash repair support.',409)
        if not staging:
            path=tr.safe(root,'run/hector-system-support/status.json')
            try:
                st=path.lstat();value=json.loads(tr.regular(path,4096))
                if st.st_uid!=0 or st.st_mode&0o022:raise ValueError('Untrusted support status')
            except (OSError,ValueError):value={}
            boot=(root/'proc/sys/kernel/random/boot_id').read_text().strip()
            if value.get('boot')!=boot or not value.get('available'):
                raise Problem('The shared preset-flash repair needs attention before installing a module.',409)


def export_status(root=Path('/'),firmware=Path('/overlay/lower'),policy=None):
    if os.geteuid()!=0:raise ValueError('Only the trusted catalog may publish system support status')
    root,firmware=Path(root),Path(firmware)
    value=installed(root,policy or PublisherPolicy(),firmware=firmware)
    driver=root/'sys/devices/platform/soc/1c68000.spi/driver'
    note=root/'sys/module/jffs2/notes/.note.gnu.build-id'
    available=bool(value and value['enabled'] and value['preset_preservation'] and driver.resolve().name=='hector-sun6i-rx-fix' and note.is_file() and note.read_bytes().hex()==FS_NOTE)
    result=dict(schema=1,api_version=1,id=ID,boot=(root/'proc/sys/kernel/random/boot_id').read_text().strip(),
                available=available,preset_preservation=available,version=value['version'] if value else None)
    folder=tr.safe(root,'run/hector-system-support');folder.mkdir(mode=0o755,parents=True,exist_ok=True)
    if folder.lstat().st_uid!=0:raise ValueError('The public support directory is not root-owned')
    folder.chmod(0o755)
    path=folder/'status.json';tr.atomic(path,canonical(result));path.chmod(0o644)
    return result


def validate_runtime(info,files,policy):
    """Every new complete installer carries the same separately verified repair."""
    repair=None
    for manifest in info['profiles']:
        rows={r['path']:r for r in manifest['files']}
        hashes=dict(manifest['guards'],**{n:r['sha256'] for n,r in rows.items()})
        required=(BUNDLE,UNIT,'etc/systemd/system/hector-platform-flash-status.service',
            'opt/hector-storage-manager/app/system_support.py','opt/hector-storage-manager/app/system_support_contract.py',
            'opt/hector-storage-manager/app/system_support_transaction.py','opt/hector-storage-manager/runtime-version.json')
        if not all(n in hashes for n in required):raise ValueError('The runtime is missing shared flash repair support')
        runtime=json.loads(files['payload/'+hashes['opt/hector-storage-manager/runtime-version.json']])
        if runtime.get('id')!=__import__('platform_release').CORE_ID or not {CAPABILITY,PRESERVATION}<=set(runtime.get('capabilities',[])):raise ValueError('Runtime omits the shared repair capability')
        raw=files['payload/'+hashes[BUNDLE]]
        if repair is None:
            component,body=read(raw,policy)
            if component['version']!=PAIRED_VERSION or set(body)!=set(contract.FILES):raise ValueError('Runtime must include the reviewed paired storage correction')
            repair=raw
        elif repair!=raw:raise ValueError('Runtime profiles disagree about the shared repair')
    return repair

def load_upgrade_boot(payload,root,lower):
    """Only separately platform-verified system code; never a module USB script."""
    import types
    module=types.ModuleType('hector_verified_rx2_migration')
    module.__file__=str(lower/BASE/'boot.py')
    exec(compile(payload[BASE+'boot.py'],module.__file__,'exec'),module.__dict__)
    module.DATA=root/DATA;module.BASE=lower/BASE;module.RUN=root/'run/hector-flash-rx-fix'
    return module


def stage_upgrade(root,lower,policy,raw,checkpoint=lambda stage,name:None,bridge=None):
    info,payload=read(raw,policy)
    if info['version']!=PAIRED_VERSION:raise ValueError('Unsupported paired support successor')
    boot=load_upgrade_boot(payload,Path(root),Path(lower))
    return boot.storage_upgrade_support().stage(boot,Path(root),Path(lower),payload,checkpoint,bridge=bridge)


def committed_platform_before_upgrade(root,lower):
    """No repair migration may begin while app publication is incomplete."""
    import platform_transaction as pt
    data=root/'mnt/community-lab/hector-poly-view-platform'
    current=json.loads(pt.regular(data/'current.json'));token=current['id']
    if not re.fullmatch('[a-f0-9]{32}',token):raise ValueError('Invalid app transaction')
    job=json.loads(pt.regular(data/'jobs'/token/'job.json'))
    if job.get('module_only') or job.get('platform_id') not in ('network-connection-poly-view','poly-view-module-runtime'):
        raise ValueError('Support migration requires a committed compatible platform/runtime transaction')
    tx=pt.Transaction(lower,data/'transactions'/token,pt.regular(data/'key'));record=tx.load()
    identity_paths={'/sys/block/mmcblk0/device/cid','/pedal_state/hardware_info.json'}
    if set(job['manifest']['identity_files'])!=identity_paths:raise ValueError('Invalid app physical identity contract')
    identity=sha256(canonical({name:sha256((root/name.lstrip('/')).read_bytes()) for name in identity_paths}))
    if record['phase']!='committed' or record['identity']!=identity or record['package_digest']!=sha256(canonical(job['manifest'])):
        raise ValueError('App publication is incomplete; leave original support for independent app recovery')
    tx.verify_installed()
    # Bind the exact new signed archive/profile, not only mutable job metadata.
    import platform_release
    archive=pt.regular(data/'jobs'/token/'archive.zip',platform_release.LIMIT)
    info,files,_=platform_release.read(archive)
    if sha256(archive)!=job['key']:raise ValueError('Committed app archive differs')
    profile=json.loads(canonical(job['manifest']))
    for name in ('identity_files','baseline_boot','guard_metadata'):profile.pop(name)
    for row in profile['files']:row.pop('before_metadata',None)
    if sum(profile==p for p in info['profiles'])!=1:raise ValueError('App journal is not an exact signed profile')
    return token


def stage(root,lower,policy,raw,checkpoint=lambda stage,name:None,bridge=None):
    """Separate authenticated transaction. Caller owns the firmware mount window."""
    root,lower=Path(root),Path(lower);info,payload=read(raw,policy)
    data=tr.safe(root,DATA);current=None
    removed=False
    if info['version']==PAIRED_VERSION and (data/'removal-rx2/intent.json').exists():
        # A completed explicit removal retains its authenticated initial intent.
        # Revalidate its whole signed/HMAC/identity/guard lineage and a new stock
        # boot before treating that intent as retired. Status='removed' alone is
        # never sufficient; partial residue requires the independent recovery kit.
        if any(os.path.lexists(base/n) for base in (root,lower) for n in contract.FILES):
            raise ValueError('Complete authenticated support removal recovery before reinstalling')
        result=load_upgrade_boot(payload,root,lower).close_removal(root,lower)
        if result.get('status')!='stock-restored-verified':raise ValueError('Support removal did not close')
        removed=True
    if not removed and (data/'upgrade-storage-v1/active.json').exists():
        recovered=load_upgrade_boot(payload,root,lower).recover_storage_upgrade(root,lower,write_window=True)
        if recovered in ('restored-previous-support','previous-support-restored'):raise Problem('Previous storage support restored. Restart and review before retry.',409)
    if info['version']==PAIRED_VERSION and (data/'initial-intent.json').exists() and not removed and json.loads(tr.regular(data/'state.json',4096)).get('version')==PAIRED_VERSION:
        load_upgrade_boot(payload,root,lower).recover_initial(root,lower,write_window=True)
    if (all((lower/n).is_file() for n in contract.FILES) or all((lower/n).is_file() for n in contract.LEGACY_FILES)) and (data/'state.json').exists():
        state=json.loads(tr.regular(data/'state.json',4096))
        if state.get('status')!='staging':
            current=installed(root,policy,firmware=lower)
            if version(current['version'])<version(info['version']):return stage_upgrade(root,lower,policy,raw,checkpoint,bridge=bridge)
            return dict(action='reuse',version=current['version'],enabled=current['enabled'])
    if (data/'transaction/journal.json').exists() and not current:
        tx=tr.Transaction(lower,data/'transaction',tr.regular(data/'transaction-key'))
        record=tx.load()
        identity={n:sha256((root/p).read_bytes()) for n,p in [('card','sys/block/mmcblk0/device/cid'),('device','pedal_state/hardware_info.json')]}
        if (record['identity']!=identity or tuple(r['path'] for r in record['files'])!=contract.FILES
                or any(r['before_sha256'] is not None for r in record['files'])):
            raise ValueError('The support recovery journal belongs to another component/device')
        if record['phase'] not in ('committed','rolled-back'):tx.rollback()
        if record['phase']=='committed':
            tx.verify_installed()
            # A power loss between file commit and state publication is recoverable.
            state=json.loads(tr.regular(data/'state.json',4096))
            if state.get('status')=='staging':
                state.update(enabled=True,status='ready-for-first-boot');tr.atomic(data/'state.json',canonical(state))
    current=installed(root,policy,firmware=lower)
    if current:
        if version(current['version'])<version(info['version']):return stage_upgrade(root,lower,policy,raw,checkpoint,bridge=bridge)
        return dict(action='reuse',version=current['version'],enabled=current['enabled'])
    host(root,info['config_sha256'])
    if data.exists():
        archived=data.with_name(data.name+'-recovered-'+uuid.uuid4().hex)
        os.rename(data,archived);tr.sync_dir(data.parent)
    data.mkdir(mode=0o700,parents=True);data.chmod(0o700);(data/'transaction').mkdir(mode=0o700)
    tr.atomic(data/'transaction-key',os.urandom(32))
    initial=dict(schema=1,version=info['version'],module_sha256=info['module_sha256'],enabled=False,pending_boot=None,status='staging')
    tr.atomic(data/'state.json',canonical(initial))
    guards={n:tr.file_hash(tr.safe(lower,n)) for n in contract.GUARDS}
    manifest=dict(schema='four-by-four-hector/guarded/1',hardware='Hector',firmware='407',architecture='aarch64',
        version=info['version'],published_mtime_ns=1700000000000000000,require_absent=list(contract.FILES),guards=guards,
        guard_metadata={n:tr.metadata(tr.safe(lower,n)) for n in contract.GUARDS},
        files=[dict(path=n,before_sha256=None,sha256=sha256(payload[n]),size=len(payload[n])) for n in contract.FILES])
    tx=tr.Transaction(lower,data/'transaction',tr.regular(data/'transaction-key'))
    identity={n:sha256((root/p).read_bytes()) for n,p in [('card','sys/block/mmcblk0/device/cid'),('device','pedal_state/hardware_info.json')]}
    installation_record=dict(schema='hector-signed-system-component/1',id=ID,
        version=info['version'],api_version=1,publisher=OWNER,file_manifest=manifest,independent_of_networking=True,
        protected_system_component=True,ordered_ports=[],lv2_uri=None,transaction='transaction',publisher_proof=payload[BASE+'publisher-proof.json'].decode())
    if info['version']==PAIRED_VERSION:
        import hmac,hashlib
        intent=dict(schema=1,identity=identity,manifest=manifest,proof=base64.b64encode(payload[BASE+'publisher-proof.json']).decode(),initial_state=initial,installation_record=installation_record)
        key=tr.regular(data/'transaction-key')
        tr.atomic(data/'initial-intent.json',canonical(dict(content=intent,hmac=hmac.new(key,canonical(intent),hashlib.sha256).hexdigest())))
    tx.prepare(manifest,payload,identity,(root/'proc/sys/kernel/random/boot_id').read_text().strip())
    try:
        tx.apply(payload,checkpoint);tx.verify_installed()
        initial.update(enabled=True,status='ready-for-first-boot');tr.atomic(data/'state.json',canonical(initial))
        tr.atomic(data/'installation-record.json',canonical(dict(schema='hector-signed-system-component/1',id=ID,
            version=info['version'],api_version=1,publisher=OWNER,file_manifest=manifest,independent_of_networking=True,
            protected_system_component=True,ordered_ports=[],lv2_uri=None,transaction='transaction',
            publisher_proof=payload[BASE+'publisher-proof.json'].decode())))
    except Exception:
        tx.rollback();initial.update(enabled=False,status='deployment-restored');tr.atomic(data/'state.json',canonical(initial));raise
    return dict(action='installed',version=info['version'],files=len(payload),enabled=True)

def usb_device():
    """Admit the original sealed SD/overlay layout without touching NOR data."""
    import platform_engine
    probe=platform_engine.Device.__new__(platform_engine.Device)
    probe.root=Path('/');probe.lower=Path('/overlay/lower');probe.initial_mounts=None
    probe.check_mounts('ro')
    return probe


def initial_staging_ready(root,units,continuation_marker=None):
    """Read-only exception for authenticated, never-enabled initial publication.

    It permits only recovery inspection, not mutation readiness or presets.
    Every service writer must be absent/terminal before the mutable state read.
    """
    root=Path(root);data=root/DATA;intent_path=data/'initial-intent.json'
    marker=root/'run/hector-module-restarting'
    if marker.exists() and (continuation_marker is None or tr.regular(marker,512)!=continuation_marker):return False
    upgrade=data/'upgrade-storage-v1/active.json'
    if upgrade.exists():
        from system_readiness import settled
        if not settled(units):return False
        state_path=data/'state.json'
        if not state_path.is_file():return False
        raw=tr.regular(APP/'flash-repair.zip',512*1024);info,payload=read(raw,PublisherPolicy(APP/'publishers.json'))
        boot=load_upgrade_boot(payload,root,root/'overlay/lower');helper=boot.storage_upgrade_support();helper.configure(boot,root,root/'overlay/lower')
        engine=boot.recovery_transaction();key=helper.key_read(engine);p,f,i,tx=helper.load_current(engine,key)
        if p['phase']!='publishing':return False
        tx.admit_rollback()
        return True
    if not intent_path.exists():return False
    marker=root/'run/hector-module-restarting'
    if marker.exists() and (continuation_marker is None or tr.regular(marker,512)!=continuation_marker):return False
    for name,row in units.items():
        if row.get('LoadState')=='not-found':continue
        if (row.get('MainPID')!='0' or row.get('ControlPID')!='0' or row.get('Job') not in ('','0')
                or row.get('ActiveState') not in ('inactive','failed','active')
                or row.get('ActiveState')=='active' and row.get('SubState')!='exited'):
            return False
        if name=='hector-flash-rx-guard.service' and row.get('Result')!='success':return False
    if (root/'sys/devices/platform/soc/1c68000.spi/driver').resolve().name!='sun6i-spi':return False
    # A guard attempt must not be inferred disarmed from a missing sysfs attr.
    if (root/'run/hector-flash-rx-fix/guard-armed.json').exists():return False
    import platform_engine,hmac,hashlib
    platform_engine.Device.protected(data)
    if data.stat().st_mode&0o077:raise ValueError('Unprotected initial support store')
    keypath=data/'transaction-key';platform_engine.Device.protected(keypath)
    if keypath.stat().st_mode&0o077:raise ValueError('Unprotected initial support key')
    key=tr.regular(keypath,128)
    if len(key)!=32:raise ValueError('Invalid support key')
    envelope=json.loads(tr.regular(intent_path));intent=envelope['content']
    if not hmac.compare_digest(envelope['hmac'],hmac.new(key,canonical(intent),hashlib.sha256).hexdigest()):raise ValueError('Initial support intent authentication failed')
    manifest=intent['manifest'];contract.validate_manifest(manifest)
    if manifest.get('migration_from') or manifest['version']!=PAIRED_VERSION:return False
    proof_raw=base64.b64decode(intent['proof'],validate=True);proof=json.loads(proof_raw)
    owner,hashes=PublisherPolicy().verify_manifest(proof,ID,native=True)
    hashes=dict(hashes,**{BASE+'publisher-proof.json':sha256(proof_raw)})
    if owner!=OWNER or set(hashes)!=set(contract.FILES) or any(hashes[r['path']]!=r['sha256'] for r in manifest['files']):raise ValueError('Initial support proof differs')
    identity={n:sha256((root/p).read_bytes()) for n,p in [('card','sys/block/mmcblk0/device/cid'),('device','pedal_state/hardware_info.json')]}
    if intent['identity']!=identity:raise ValueError('Initial support belongs to another device')
    tx=tr.Transaction(root/'overlay/lower',data/'transaction',key)
    if (data/'transaction/journal.json').exists():
        record=tx.load()
        if (record['identity']!=identity or record['package_digest']!=sha256(canonical(manifest)) or tuple(r['path'] for r in record['files'])!=contract.FILES
                or any(r['before_sha256'] is not None or r['original'] is not None or r['sha256']!=hashes[r['path']] for r in record['files'])):
            raise ValueError('Initial support journal differs')
        tx.admit_rollback()
    elif any(os.path.lexists(root/'overlay/lower'/n) for n in contract.FILES):raise ValueError('Initial support files have no recovery journal')
    if marker.exists() and (continuation_marker is None or tr.regular(marker,512)!=continuation_marker):return False
    state=json.loads(tr.regular(data/'state.json',4096))
    if state!=intent['initial_state'] or state.get('enabled') is not False or state.get('pending_boot') is not None or state.get('status')!='staging':return False
    if marker.exists() and (continuation_marker is None or tr.regular(marker,512)!=continuation_marker):return False
    return True


def support_marker(expected=None):
    """Use the exclusion understood by both frozen and new managers."""
    marker=Path('/run/hector-module-restarting')
    if expected is not None:
        info=marker.lstat()
        if info.st_uid!=0 or info.st_mode&0o077 or tr.regular(marker,512)!=expected:
            raise ValueError('The trusted USB continuation does not own the restart marker')
        return marker,None
    value=('support-usb '+uuid.uuid4().hex+'\n').encode()
    fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(value);stream.flush();os.fsync(stream.fileno())
    return marker,value


def release_support_marker(marker,value):
    if value is not None and tr.regular(marker,512)==value:marker.unlink()


def bridge_for_release(raw,policy,lower=Path('/overlay/lower')):
    import platform_release,zlib
    from publisher_release import extract_proof
    lower=Path(lower)
    if not (lower/'etc/systemd/system/hector-platform-flash-bootstrap.service').is_file():return None
    info,files,proof=platform_release.read(raw,policy);manifest,payload=platform_release.select(info,files,lower)
    names=tuple('opt/hector-storage-manager/app/'+n for n in ('system_support.py','system_support_contract.py','system_readiness.py','flash-repair.zip'))
    rows={r['path']:r for r in manifest['files']}
    if not all(n in rows for n in names):return None
    if all(tr.file_hash(lower/n)==rows[n]['sha256'] for n in names):return None
    return dict(evidence=dict(proof=base64.b64encode(canonical(proof)).decode(),platform_z=base64.b64encode(zlib.compress(files['platform.json'],9)).decode(),profile_sha256=sha256(canonical(manifest))),
                payload={n:files['payload/'+rows[n]['sha256']] for n in names})


def usb_prepare_repair(raw,policy,source_sha256,*,continuation_marker=None,allow_legacy_upgrade=False,bridge=None):
    """Physical USB only: repair first, then resume the unchanged app transaction.

    This component writes its signed SD support files. It never reads,
    formats, unmounts or modifies NOR or hot-swaps a driver. A normal restart
    activates the independent signed boot service before NOR mounts. The next
    USB boot must observe healthy confirmation before reading saved presets.
    """
    if os.geteuid()!=0:raise ValueError('The physical USB root installer is required')
    if not re.fullmatch('[a-f0-9]{64}',source_sha256):raise ValueError('Invalid USB release identity')
    from system_readiness import wait
    readiness=wait(allow_absent=True,allow_legacy_upgrade=allow_legacy_upgrade,allow_initial_staging=True,continuation_marker=continuation_marker)
    info,payload=read(raw,policy);root=Path('/');lower=Path('/overlay/lower')
    host(root,info['config_sha256'])
    data=root/DATA;current_boot=(root/'proc/sys/kernel/random/boot_id').read_text().strip()
    state=None
    if data.exists():
        st=data.lstat()
        if data.is_symlink() or st.st_uid!=0 or st.st_mode&0o077:raise ValueError('Unprotected repair recovery store')
        try:state=json.loads(tr.regular(data/'state.json',4096))
        except FileNotFoundError:
            if (data/'transaction/journal.json').exists():raise
            # A cut before the initial state publication wrote no component
            # files. The stage helper archives this empty owned setup safely.
            state=None
    full=all((lower/n).is_file() and not (lower/n).is_symlink() for n in contract.LEGACY_FILES)
    current=installed(root,policy,firmware=lower) if full and (state or {}).get('status')!='staging' and readiness.get('status')!='initial-staging-recovery' else None
    if current and not current['enabled']:raise Problem('Storage support needs recovery before installation.',409)
    if current and version(current['version'])<version(info['version']):
        if current['version'] not in contract.LEGACY_VERSIONS:raise Problem('This repair upgrade needs a reviewed migration.',409)
        current=None
    if current:
        driver=(root/'sys/devices/platform/soc/1c68000.spi/driver').resolve().name
        accepted=current['state']
        if driver=='hector-sun6i-rx-fix' and accepted.get('confirmed_boot')==current_boot and not accepted.get('pending_boot'):
            return dict(reboot_requested=False,action='verified-active-repair-reused',version=current['version'])
        report=root/'run/hector-flash-rx-fix/status.json'
        if report.exists():
            status=json.loads(tr.regular(report,4096))
            if status.get('boot')==current_boot and status.get('status') in ('fallback-stock','reboot-to-stock'):
                raise Problem('Flash Repair did not activate safely. Review recovery before continuing.',409)
        # Original USB debug and normal UI/audio services start in parallel.
        # Wait for their independent boot confirmation BEFORE a module update
        # can pause consumers or request its own restart. Never retry activation.
        if accepted.get('pending_boot')==current_boot and driver=='hector-sun6i-rx-fix':
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                latest=json.loads(tr.regular(data/'state.json',4096))
                if not latest['enabled']:break
                if latest.get('confirmed_boot')==current_boot and not latest.get('pending_boot'):
                    checked=installed(root,policy,firmware=lower)
                    if checked['enabled'] and (root/'sys/devices/platform/soc/1c68000.spi/driver').resolve().name=='hector-sun6i-rx-fix':
                        return dict(reboot_requested=False,action='verified-active-repair-reused',version=checked['version'])
                    break
                time.sleep(.25)
        # Never convert a failed/unconfirmed boot into an automatic retry loop.
        if accepted.get('pending_boot'):
            raise Problem('Flash Repair has not confirmed. No module files changed; review recovery before continuing.',409)
        request=data/'usb-activation-request.json'
        if request.exists() and json.loads(tr.regular(request,4096)).get('boot')==current_boot:
            raise Problem('Flash Repair is prepared. Restart Hector to activate it before continuing.',409)
        if accepted.get('status')!='ready-for-first-boot':
            raise Problem('The installed repair is not confirmed for this boot. Review recovery before continuing.',409)
    else:
        # Recover only authenticated partial publications through the original
        # component transaction. It refuses unknown edited targets and preserves
        # its old recovery directory rather than deleting it.
        marker,owned=support_marker(continuation_marker)
        try:probe=usb_device()
        except Exception:
            release_support_marker(marker,owned);raise
        def command(args):return subprocess.run(args,check=True,capture_output=True,timeout=30)
        try:command(['/bin/mount','-o','remount,rw',str(lower)])
        except Exception:
            try:probe.check_mounts('ro');release_support_marker(marker,owned)
            except Exception:pass
            raise
        error=None
        try:
            probe.check_mounts('rw');stage(root,lower,policy,raw,bridge=bridge)
        except BaseException as exc:
            error=exc;raise
        finally:
            os.sync()
            try:
                command(['/bin/mount','-o','remount,ro',str(lower)]);probe.check_mounts('ro')
                if error is not None and not any(os.path.lexists(lower/n) for n in contract.FILES):
                    release_support_marker(marker,owned)
            except (subprocess.SubprocessError,ValueError) as exc:
                stderr=getattr(exc,'stderr',b'') or b''
                if isinstance(stderr,bytes):stderr=stderr.decode(errors='replace')
                if error is not None or 'busy' not in str(stderr).lower():raise
                # The known Btrfs/overlay EBUSY closeout is admitted only after
                # this separately authenticated support commit fully verifies.
                current=installed(root,policy,firmware=lower)
                if not current or not current['enabled']:raise
                tx=tr.Transaction(lower,data/'transaction',tr.regular(data/'transaction-key'))
                tx.load();tx.verify_installed()
                if tx.record['phase']!='committed':raise ValueError('Repair publication did not commit')
        current=installed(root,policy,firmware=lower)
        if not current or not current['enabled']:raise ValueError('Repair installation did not verify')
    request=dict(schema=1,boot=current_boot,release_sha256=source_sha256,version=current['version'],
                 status='repair-first-normal-restart-requested',app_files_written=False,preset_banks_read=False)
    tr.atomic(data/'usb-activation-request.json',canonical(request))
    os.sync()
    subprocess.run(['/bin/systemctl','--no-block','reboot'],check=True,capture_output=True,timeout=15)
    return dict(status='flash-repair-first-reboot-required',reboot_requested=True,device_written=True,
                version=current['version'],app_files_written=False,preset_banks_read=False)


def early():
    """Pre-NOR bootstrap; the signed component retains independent recovery."""
    root=Path('/');lower=Path('/overlay/lower');policy=PublisherPolicy(APP/'publishers.json')
    raw=tr.regular(APP/'flash-repair.zip',512*1024);info,payload=read(raw,policy)
    boot=load_upgrade_boot(payload,root,lower)
    if (root/DATA/'upgrade-rx6/active.json').exists():
        raise RuntimeError('Private lab support must be closed through its reviewed stock-reset workflow before this app is installed')
    if (root/DATA/'removal-rx2/intent.json').exists():
        boot.recover_removal(root,lower);return
    if (root/DATA/'upgrade-storage-v1/active.json').exists():
        result=boot.recover_storage_upgrade(root,lower)
        if result=='restored-previous-support':
            subprocess.run(['/bin/systemctl','--no-block','reboot'],check=True,capture_output=True,timeout=15)
            time.sleep(30);raise RuntimeError('Waiting for overlay recovery restart')
    if (root/DATA/'upgrade-storage-v1/active.json').exists() and result=='previous-support-restored':return
    if (root/DATA/'initial-intent.json').exists() and json.loads(tr.regular(root/DATA/'state.json',4096)).get('version')==info['version']:
        restored=boot.recover_initial(root,lower)
        if restored=='initial-restored-stock':return
    current=installed(root,policy,firmware=lower,pre_nor=True)
    if current and current['version']==info['version']:return
    if current and current.get('preset_preservation') and version(current['version'])>version(info['version']):return
    if current:committed_platform_before_upgrade(root,lower)
    if any(row.split()[4]=='/nor_flash' for row in (root/'proc/1/mountinfo').read_text().splitlines()):
        raise RuntimeError('Refusing storage publication after NOR mounted')
    subprocess.run(['/bin/mount','-o','remount,rw',str(lower)],check=True,capture_output=True,timeout=30)
    try:stage(root,lower,policy,raw)
    finally:
        os.sync()
        try:subprocess.run(['/bin/mount','-o','remount,ro',str(lower)],check=True,capture_output=True,timeout=30)
        except subprocess.CalledProcessError as exc:
            if 'busy' not in (exc.stderr or b'').decode(errors='replace').lower():raise
    # A fresh overlay is required for replaced/previously negative lower paths.
    subprocess.run(['/bin/systemctl','--no-block','reboot'],check=True,capture_output=True,timeout=15)
    time.sleep(30);raise RuntimeError('Waiting for storage publication restart')

if __name__=='__main__':
    if sys.argv[1:]==['status']:print(json.dumps(export_status()))
    elif not sys.argv[1:]:early()
    else:raise ValueError('Unknown system-support operation')
