# SPDX-License-Identifier: MIT
"""Explicitly authorized Link recovery update worker. No default action; no network client.

The filesystem transaction derives from Mala's MIT installer. Atomic lower-layer
publication requires an ordinary reboot before using the new overlay view.
"""
import argparse
import base64
import contextlib
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import stat
import struct
import subprocess
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from platform_transaction import (Transaction, atomic, canonical, digest, file_hash,
                         metadata, regular, safe, sync_dir, validate_manifest)

DATA = Path('/mnt/community-lab/hector-poly-view-platform')
CONSUMERS = ('hector-repository-fetch.socket', 'hector-remote-input.service', 'hector-remote-screen.service', 'hector-storage-manager-live.service', 'hector-storage-inspector-live.service', 'hector-storage-manager.service', 'hector-storage-inspector.service', 'lightdm.service', 'jack.service', 'pcm_power.service', 'hector-module-catalog.service',
    'hector-link-send-manager.service', 'hector-link-receive-manager.service',
    'hector-link-sync-control.service', 'hector-link-sync-ethernet.service',
    'hector-link-sync-wifi.service', 'hector-link.service',
    'hector-network-panel.service', 'hector-ethernet-panel.service')
IDENTITY_PATHS = {'/sys/block/mmcblk0/device/cid', '/pedal_state/hardware_info.json'}
PAUSE = b'[Unit]\nConditionPathExists=/run/hector-poly-view-platform-enable-consumers\n'


def command(args, timeout=45):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, timeout=timeout,
        env=dict(PATH='/usr/sbin:/usr/bin:/sbin:/bin', LC_ALL='C'))
    if p.returncode:
        raise RuntimeError('Command failed: ' + ' '.join(args[:3]) + ': ' + p.stderr[-1000:])
    return p.stdout


def load_package(folder):
    manifest = json.loads(regular(safe(folder, 'manifest.json')).decode())
    validate_manifest(manifest)
    payload = {r['path']: regular(safe(folder/'payload', r['path'])) for r in manifest['files']}
    for row in manifest['files']:
        if digest(payload[row['path']]) != row['sha256'] or len(payload[row['path']]) != row['size']:
            raise ValueError('Payload corrupt: '+row['path'])
    validation=json.loads(regular(folder/'validation.json'))
    if validation.get('status')!='pass' or validation.get('manifest_sha256')!=digest(canonical(manifest)):
        raise ValueError('This exact package has no passing bound validation')
    if validation['tested_files'] != dict(manifest['guards'], **{row['path']: row['sha256'] for row in manifest['files']}):
        raise ValueError('Package files differ from the tested prospective payload')
    return manifest, payload


def transport_playing(raw):
    if len(raw)<16 or struct.unpack_from('<Q',raw,0)[0]!=0x484543544c4e4b31:
        raise ValueError('Invalid Link session header')
    version=struct.unpack_from('<I',raw,8)[0]
    offset={2:88,4:112}.get(version)
    if offset is None or len(raw)<offset+8:raise ValueError('Unsupported Link session layout')
    return bool(struct.unpack_from('<Q',raw,offset)[0])


def authorize(value, action, manifest, identity, boot, token):
    expected = dict(action=action, manifest_sha256=digest(canonical(manifest)),
                    identity=identity, boot=boot, transaction=token)
    if (any(value.get(k) != v for k,v in expected.items())
            or any(value.get(k) is not True for k in ['owner_approved', 'transport_paused_confirmed', 'reboot_approved'])
            or not (value.get('saved_patch_confirmed') is True or value.get('unsaved_changes_discard_approved') is True)):
        raise ValueError('Explicit action/device/boot/package approval and saved patch or explicit discard authorization, plus pause, required')


class Device:
    def __init__(self, manifest):
        if os.geteuid() != 0 or platform.machine() not in ('aarch64','arm64'):
            raise ValueError('ARM64 Hector as root required')
        if os.stat('/proc/self/ns/mnt').st_ino != os.stat('/proc/1/ns/mnt').st_ino:
            raise ValueError('Enter the main mount namespace before running this worker')
        if b'pine64,sopine\0' not in Path('/proc/device-tree/compatible').read_bytes():
            raise ValueError('Unsupported Hector board')
        if json.loads(Path('/mnt/pedal_state/state.json').read_text()).get('model') != 'hector':
            raise ValueError('Hector model required')
        if set(manifest['identity_files']) != IDENTITY_PATHS:
            raise ValueError('Invalid identity contract')
        for name, expected in manifest['identity_files'].items():
            if digest(Path(name).read_bytes()) != expected:
                raise ValueError('Different physical Hector')
        self.identity = digest(canonical(manifest['identity_files']))
        self.boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        self.lower = Path('/overlay/lower')
        self.root = Path('/')
        self.data = DATA
        self.initial_mounts = None
        self.pause_files = []
        self.initial_services = {}
        self.check_mounts('ro')

    def check_mounts(self, mode):
        unescape=lambda s:re.sub(r'\\([0-7]{3})',lambda m:chr(int(m.group(1),8)),s)
        rows={}
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            left,right=line.split(' - ',1);a,b=left.split(),right.split()
            rows.setdefault(unescape(a[4]),[]).append(dict(id=a[0],device=a[2],root=unescape(a[3]),
                options=a[5].split(','),fs=b[0],source=unescape(b[1]),super_options=b[2].split(',')))
        selected={}
        for name in ['/','/overlay','/overlay/lower','/mnt']:
            fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            try:
                values=dict(l.split(':',1) for l in Path('/proc/self/fdinfo/'+str(fd)).read_text().splitlines())
                matches=[r for r in rows.get(name,[]) if r['id']==values['mnt_id'].strip()]
            finally:os.close(fd)
            if len(matches)!=1:raise ValueError('Unexpected mount view: '+name)
            selected[name]=matches[0]
        for path,source in [('/overlay/lower','/dev/mmcblk0p2'),('/mnt','/dev/mmcblk0p3')]:
            r=selected[path]
            if r['fs']!='btrfs' or r['source']!=source or r['root']!='/':
                raise ValueError('Unexpected persistent partition: '+path)
        if (mode not in selected['/overlay/lower']['options'] or 'rw' not in selected['/mnt']['options']
                or selected['/']['fs']!='overlay' or selected['/overlay']['fs']!='tmpfs'
                or not {'lowerdir=/overlay/lower','upperdir=/overlay/upper','workdir=/overlay/work'}.issubset(selected['/']['super_options'])):
            raise ValueError('Unexpected overlay layout or mount mode')
        identity={p:{k:r[k] for k in ['id','device','root','fs','source']} for p,r in selected.items()}
        if self.initial_mounts is None:self.initial_mounts=identity
        elif identity!=self.initial_mounts:raise ValueError('Mount identity changed')

    def overlay(self, manifest, visible=True):
        for name in set(manifest['guards'])|{r['path'] for r in manifest['files']}:
            upper=safe(self.root/'overlay/upper',name)
            if os.path.lexists(str(upper)):raise ValueError('Writable overlay shadows '+name)
            for parent in list(upper.parents):
                if parent==self.root/'overlay':break
                if parent.exists() and any(a.endswith(('overlay.opaque','overlay.redirect','overlay.metacopy'))
                                           for a in os.listxattr(str(parent))):
                    raise ValueError('Writable overlay directory needs review')
            if visible and file_hash(safe(self.root,name))!=file_hash(safe(self.lower,name)):
                raise ValueError('Visible/persistent mismatch: '+name)

    def guards(self, manifest):
        for name,expected in manifest['guards'].items():
            p=safe(self.lower,name)
            if file_hash(p)!=expected:raise ValueError('Preserved file changed: '+name)
            observed=metadata(p)
            if any(observed[k]!=manifest['guard_metadata'][name][k] for k in ['mode','uid','gid','mtime_ns','xattrs']):
                raise ValueError('Preserved metadata changed: '+name)

    def guard_metadata(self,manifest):
        for name,expected in manifest['guard_metadata'].items():
            observed=metadata(safe(self.lower,name))
            if any(observed[k]!=expected[k] for k in ['mode','uid','gid','mtime_ns','xattrs']):
                raise ValueError('Preserved metadata changed: '+name)

    def admit_environment(self,manifest,payload):
        if self.boot!=manifest['baseline_boot']:raise ValueError('Hector rebooted; refresh the install baseline')
        self.check_mounts('ro');self.guard_metadata(manifest)
        for p in [self.lower,Path('/mnt')]:
            if shutil.disk_usage(str(p)).free<32*1024*1024:raise ValueError('Need32 MiB free on both partitions')

    def baseline(self, manifest, payload):
        if self.boot!=manifest['baseline_boot']:raise ValueError('Hector rebooted; refresh the install baseline')
        self.check_mounts('ro');self.overlay(manifest);self.guards(manifest)
        Transaction(self.lower,Path('/unused'),b'').preflight(manifest,payload)
        for p in [self.lower,Path('/mnt')]:
            if shutil.disk_usage(str(p)).free<32*1024*1024:raise ValueError('Need 32 MiB free on both partitions')

    def presets(self):
        return {bank:self.preset_bank(bank) for bank in ("beebo","digit")}

    def preset_bank(self, bank):
        root=self.root/"mnt/presets"/bank
        result={}
        for directory,dirs,files in os.walk(str(root),followlinks=False):
            for name in dirs+files:
                p=Path(directory)/name;info=p.lstat();rel=str(p.relative_to(root))
                if stat.S_ISLNK(info.st_mode):
                    result[rel]=dict(symlink=os.readlink(str(p)))
                elif stat.S_ISREG(info.st_mode):
                    fd=os.open(str(p),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
                    with os.fdopen(fd,'rb') as stream:
                        initial=os.fstat(stream.fileno())
                        if not stat.S_ISREG(initial.st_mode):raise ValueError('Preset changed type')
                        import hashlib
                        value=hashlib.sha256()
                        while True:
                            raw=stream.read(256*1024)
                            if not raw:break
                            value.update(raw)
                        end=os.fstat(stream.fileno())
                    if (initial.st_ino,initial.st_size,initial.st_mtime_ns)!=(end.st_ino,end.st_size,end.st_mtime_ns):
                        raise ValueError('Saved preset changed while being read')
                    result[rel]=dict(sha256=value.hexdigest(),size=end.st_size,mode=stat.S_IMODE(end.st_mode),
                                     uid=end.st_uid,gid=end.st_gid,mtime_ns=end.st_mtime_ns)
                elif not stat.S_ISDIR(info.st_mode):
                    raise ValueError('Unexpected special file in presets')
        return result

    @staticmethod
    def protected(path):
        for p in [path]+list(path.parents):
            st=p.lstat()
            if stat.S_ISLNK(st.st_mode) or st.st_uid!=0 or st.st_mode&0o022:
                raise ValueError('Unprotected installer storage: '+str(p))

    @contextlib.contextmanager
    def storage(self, token):
        # Only called after authorization and firmware admission.
        parent=self.data.parent
        self.protected(parent)
        if not self.data.exists():self.data.mkdir(mode=0o700);sync_dir(parent)
        self.protected(self.data)
        lock=self.data/'lock';fd=os.open(str(lock),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:
            self.protected(lock)
            if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_nlink!=1:
                raise ValueError('Installer lock must be an unlinked regular file')
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            key=self.data/'key'
            if not key.exists():atomic(key,os.urandom(32));key.chmod(0o600);sync_dir(self.data)
            self.protected(key)
            if key.stat().st_mode&0o077:raise ValueError('Backup key permissions differ')
            txroot=self.data/'transactions'
            if not txroot.exists():txroot.mkdir(mode=0o700);sync_dir(self.data)
            self.protected(txroot)
            store=txroot/token
            if not store.exists():store.mkdir(mode=0o700);sync_dir(txroot)
            self.protected(store)
            yield store,regular(key)
        finally:os.close(fd)

    def paused_transport(self):
        for root in ['hector-link-sync','hector-link']:
            p=self.root/'run'/root/'session'
            if p.exists():
                raw=regular(p)
                if transport_playing(raw):
                    raise ValueError('Pause Link transport before installation or rollback')

    @staticmethod
    def service(unit):
        return dict(l.split('=',1) for l in command(['/bin/systemctl','show',unit,
            '--property=LoadState,ActiveState,MainPID']).splitlines() if '=' in l)

    def pause(self):
        if Path('/run/hector-poly-view-platform-enable-consumers').exists():raise ValueError('Unexpected pause marker')
        dynamic=command(['/bin/systemctl','list-units','--all','--plain','--no-legend','--no-pager'])
        extras=[l.split()[0] for l in dynamic.splitlines() if l.split() and
                re.fullmatch(r'hector-(?:link-(?:send|receive)-(?:ethernet|wifi)|repository-fetch)@[^/\s]+\.service',l.split()[0])]
        units=list(CONSUMERS)+extras
        self.initial_services={u:self.service(u) for u in units}
        for unit in units:
            if self.initial_services[unit]['LoadState']=='not-found':continue
            parent=Path('/run/systemd/system')/(unit+'.d')
            if parent.is_symlink():raise ValueError('Linked service pause directory')
            parent.mkdir(mode=0o755,exist_ok=True)
            p=parent/'95-hector-poly-view-platform-pause.conf'
            fd=os.open(str(p),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
            with os.fdopen(fd,'wb') as f:f.write(PAUSE);f.flush();os.fsync(f.fileno())
            self.pause_files.append(p)
        command(['/bin/systemctl','daemon-reload'])
        for p in self.pause_files:
            shown=command(['/bin/systemctl','show',p.parent.name[:-2],'--property=DropInPaths'])
            if str(p) not in shown:raise ValueError('Consumer pause was not loaded')
        command(['/bin/systemctl','stop']+[u for u in units if self.initial_services[u]['LoadState']!='not-found'],75)
        self.quiescent()

    def quiescent(self):
        for unit in self.initial_services:
            s=self.service(unit)
            if s['LoadState']=='not-found':continue
            # Socket units have no MainPID property. Their inactive state closes
            # the listener; every accepted worker is stopped separately above.
            if (s['ActiveState'] not in ['inactive','failed'] or
                    (unit.endswith('.service') and s.get('MainPID')!='0') or
                    not unit.endswith(('.service','.socket'))):
                raise ValueError('Consumer still active: '+unit)
        for p in Path('/proc').glob('[0-9]*/cmdline'):
            try:raw=p.read_bytes()
            except OSError:continue
            if b'/home/debian/UI/show_widget.py\0' in raw or raw.startswith(b'/usr/bin/ingen\0'):
                raise ValueError('UI/audio host still running')

    def resume_before_write(self):
        for p in self.pause_files:
            if regular(p)!=PAUSE:raise ValueError('Runtime pause file changed')
            p.unlink()
        command(['/bin/systemctl','daemon-reload'])
        order=['jack.service','pcm_power.service','hector-link-send-manager.service',
               'hector-link-receive-manager.service','hector-link-sync-control.service','lightdm.service']
        for unit in order+[u for u in self.initial_services if u not in order]:
            if self.initial_services.get(unit,{}).get('ActiveState')=='active':
                command(['/bin/systemctl','start',unit],60)

    def writable(self):
        self.check_mounts('ro');self.quiescent()
        command(['/usr/bin/mount','-o','remount,rw',str(self.lower)])
        self.check_mounts('rw')

    def readonly(self):
        command(['/usr/bin/sync','-f',str(self.lower)])
        command(['/usr/bin/mount','-o','remount,ro',str(self.lower)])
        self.check_mounts('ro')

    def is_readonly(self):
        return bool(os.statvfs(str(self.lower)).f_flag & os.ST_RDONLY)

    def reboot(self):
        os.sync();command(['/bin/systemctl','reboot'])


def operate(device, action, manifest, payload, approval, token, checkpoint=lambda stage,name:None):
    if action not in ['install','rollback'] or not re.fullmatch('[a-f0-9]{32}',token):
        raise ValueError('Explicit action and transaction ID required')
    authorize(approval,action,manifest,device.identity,device.boot,token)
    device.check_mounts('ro')
    device.overlay(manifest, visible=False)
    device.guard_metadata(manifest)
    device.paused_transport()
    with device.storage(token) as (store,key):
        tx=Transaction(device.lower,store,key)
        exists=(store/'journal.json').exists()
        if exists:
            tx.load()
            if tx.record['identity']!=device.identity or tx.record['package_digest']!=digest(canonical(manifest)):
                raise ValueError('Backup belongs to another device or package')
            if action=='install':
                if tx.record['phase']=='committed':tx.verify_installed();return dict(status='already-installed',device_written=False)
                raise ValueError('Existing transaction: verify or recover with rollback')
            tx.admit_rollback()
            if tx.record['phase']=='rolled-back':tx.verify_restored();return dict(status='already-restored',device_written=False)
        elif action=='rollback':
            raise ValueError('No matching durable backup')
        else:
            device.admit_environment(manifest,payload)
            tx.prepare(manifest,payload,device.identity,device.boot)
        before=device.presets()
        paused=False;write_attempted=False;failure=None;outcome=None;deferred_readonly=False
        try:
            paused=True;device.pause();device.paused_transport()
            device.overlay(manifest, visible=action=='install');device.guard_metadata(manifest)
            if action=='install':tx.preflight(manifest,payload)
            else:tx.admit_rollback()
            write_attempted=True;device.writable()
            if action=='install':tx.apply(payload,checkpoint)
            else:tx.rollback(checkpoint)
            device.guard_metadata(manifest)
            if device.presets()!=before:raise ValueError('Saved presets changed during operation')
            outcome='installed-reboot-required' if action=='install' else 'restored-reboot-required'
        except Exception as error:
            failure=str(error)
            if write_attempted:
                try:
                    if device.is_readonly():device.writable()
                    tx.rollback()
                    device.guards(manifest)
                    if device.presets()!=before:raise ValueError('Saved presets differ')
                    outcome='restored-after-error-reboot-required'
                except Exception as restore:
                    outcome='recovery-required'
                    failure+='; rollback refused or failed: '+str(restore)
            else:outcome='refused-before-firmware-write'
        try:
            # Run even when remount-rw raised after changing the mount state.
            if write_attempted:device.readonly()
        except Exception as closeout:
            # An ordinary reboot closes the known synced overlay/Btrfs EBUSY.
            # This exception is admitted only after exact committed verification
            # with consumers still inhibited and unchanged saved presets.
            if failure is None and (outcome, tx.record['phase']) in [('installed-reboot-required','committed'),('restored-reboot-required','rolled-back')] and 'mount point is busy' in str(closeout):
                try:
                    device.quiescent();device.paused_transport()
                    if device.presets()!=before:raise ValueError('Saved presets changed before deferred read-only reboot')
                    deferred_readonly=True
                except Exception as review:
                    outcome='recovery-required';failure='Deferred read-only reboot refused: '+str(review)
            else:
                outcome='recovery-required'
                failure=(failure or '')+'; read-only closeout failed: '+str(closeout)
        if not write_attempted and paused:
            try:device.resume_before_write()
            except Exception as resume:
                outcome='recovery-required';failure=(failure or '')+'; consumer resume failed: '+str(resume)
        result=dict(status=outcome,error=failure,transaction=token,
                    device_written=write_attempted,presets_unchanged=device.presets()==before,
                    journal_phase=tx.record['phase'],reboot_requested=False,readonly_deferred_to_normal_reboot=deferred_readonly)
        atomic(store/'result.json',canonical(result))
        if outcome in ['installed-reboot-required','restored-reboot-required','restored-after-error-reboot-required']:
            # Keep consumers inhibited until reboot: never hot-read renamed lower files.
            try:
                result['reboot_requested']=True;atomic(store/'result.json',canonical(result));device.reboot()
            except Exception as error:
                result.update(status='recovery-required',error='Reboot request failed: '+str(error),reboot_requested=False)
                atomic(store/'result.json',canonical(result))
        return result

