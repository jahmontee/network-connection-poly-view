"""Root-owned self-update worker and boot recovery. No internet or archive scripts."""
import base64
import fcntl
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parent))
import platform_release as release
import platform_transaction as tr
import platform_engine as engine

DATA=engine.DATA
MODULES=Path('/mnt/hector-storage-manager/modules')
MARKER=Path('/run/hector-module-restarting')
ENGINE_FILES=('platform_service.py','platform_release.py','platform_transaction.py','platform_engine.py',
              'publisher_release.py','publishers.json','core.py')
# The independent rollback contract stays compatible with the original boot
# launcher. Flash preparation belongs to USB bootstrap, not boot recovery.
USB_ENGINE_FILES=ENGINE_FILES+('system_support.py','system_support_contract.py','system_support_transaction.py')


def mark_processed(job):
    source=job.get('source_release_sha256')
    if not isinstance(source,str) or not __import__('re').fullmatch('[a-f0-9]{64}',source):return
    parent=DATA/'processed';parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    engine.Device.protected(parent)
    value=dict(source=source,id=job.get('module_id',job.get('platform_id',release.ID)),
               version=job['version'],status=job['status'])
    tr.atomic(parent/(source+'.json'),tr.canonical(value))


def automatic_allowed(source,module_id,usb=Path('/usb_flash')):
    """An inserted installer is processed once; manual manager actions stay explicit."""
    if not __import__('re').fullmatch('[a-f0-9]{64}',source):raise ValueError('Invalid release receipt identity')
    if MARKER.exists():return False
    try:
        current=json.loads(protected(DATA/'current.json'))
        if not __import__('re').fullmatch('[a-f0-9]{32}',current['id']):raise ValueError('Invalid recovery job')
        job=json.loads(protected(DATA/'jobs'/current['id']/'job.json'))
        if job['status'] not in ('verified','restored-verified'):return False
    except FileNotFoundError:pass
    try:previous=json.loads(protected(DATA/'processed'/(source+'.json')))
    except FileNotFoundError:return True
    if previous['source']!=source:raise ValueError('Invalid processed-release record')
    flag=tr.safe(usb,'RETRY-POLY-VIEW.txt')
    try:retry=tr.regular(flag,128).decode().strip()==module_id
    except FileNotFoundError:retry=False
    if not retry:return False
    # Consumed before writes so a failed attempt cannot become a reboot loop.
    os.replace(flag,usb/'RETRY-POLY-VIEW.used')
    return True


def resume_pending():
    """USB can recover even if a first runtime install stopped before its boot unit."""
    try:current=json.loads(protected(DATA/'current.json'))
    except FileNotFoundError:return
    token=current['id']
    if not isinstance(token,str) or not __import__('re').fullmatch('[a-f0-9]{32}',token):raise ValueError('Invalid recovery job')
    folder=DATA/'jobs'/token;job=json.loads(protected(folder/'job.json'))
    if job['status'] in ('verified','restored-verified') or MARKER.exists():return
    # A newer independently verified USB worker can close an older pre-write
    # refusal without invoking that older worker's failing service checks.
    store=DATA/'transactions'/token
    if (store/'journal.json').exists():
        tx=tr.Transaction(Path('/overlay/lower'),store,protected(DATA/'key'))
        record=tx.load()
        if (record['phase']=='prepared' and not record['applied'] and
                not record.get('pending') and not record.get('pending_restore')):
            recover(False);return
        if record['phase']=='rolled-back':
            # An explicitly removed older app has already restored its SD
            # preimages. Verify that authenticated journal with this trusted
            # engine before closing the receipt; no NOR read or old code runs.
            recover(False);return
    if job['boot']==Path('/proc/sys/kernel/random/boot_id').read_text().strip():return
    if set(job['engine']) not in (set(ENGINE_FILES),set(USB_ENGINE_FILES)):raise ValueError('Incomplete recovery engine')
    for name,digest in job['engine'].items():
        if tr.digest(protected(folder/'engine'/name))!=digest:raise ValueError('Changed recovery engine')
    subprocess.run(['/usr/bin/python3','-I','-B',str(folder/'engine/platform_service.py'),'recover'],check=True,timeout=180)


def protected(path,limit=release.LIMIT):
    info=path.lstat()
    if info.st_uid!=0 or info.st_mode&0o022:raise ValueError('Untrusted platform recovery file')
    return tr.regular(path,limit)


def progress(job,phase,percent,error=None):
    row=dict(key=job['key'],id=job.get('module_id',release.ID),name=job.get('module_name','Network Connection / Poly / View'),version=job['version'],
             status='installed' if phase=='installed' else 'failed' if phase=='failed' else 'applying',percent=percent)
    value=dict(id=job['id'],requested=job.get('requested',0),phase=phase,platform=not job.get('module_only',False),modules=[row],error=error)
    tr.atomic(release.PROGRESS,tr.canonical(value),dict(mode=0o644,uid=0,gid=0,atime_ns=0,mtime_ns=time.time_ns(),xattrs={}))


def bind(manifest,root,boot):
    manifest=json.loads(tr.canonical(manifest))
    manifest.update(identity_files={name:tr.digest(Path(name).read_bytes()) for name in engine.IDENTITY_PATHS},baseline_boot=boot)
    manifest['guard_metadata']={name:{key:value for key,value in tr.metadata(tr.safe(root,name)).items() if key!='atime_ns'} for name in manifest['guards']}
    for row in manifest['files']:
        row['before_metadata']=({key:value for key,value in tr.metadata(tr.safe(root,row['path'])).items() if key!='atime_ns'}
                                if row['before_sha256'] is not None else None)
    return manifest


def independent_engine(folder):
    destination=folder/'engine';destination.mkdir(mode=0o700)
    hashes={}
    for name in ENGINE_FILES:
        raw=protected(Path(__file__).parent/name)
        tr.atomic(destination/name,raw);hashes[name]=tr.digest(raw)
    return hashes


def backup_module(manager,raw,folder):
    import native_module
    if manager.native_module_zip(raw):module,_,_,_=native_module.read(raw,manager.publishers)
    else:module,_,_=manager.publishers.read(raw)
    with manager.locked():
        state=manager.state()
        if state['queued'] or state['target']!=state['applied'] or state.get('community_pending') or state.get('native_queued'):
            raise ValueError('Finish pending module changes before a USB installation')
        previous={key:record for key,record in state['applied'].items() if record['id']==module['id']}
        if len(previous)>1:raise ValueError('Ambiguous previous module installation')
        files={};directories={}
        def add(name):
            path=tr.safe(manager.root,name);content=tr.regular(path)
            files[name]=dict(sha256=tr.digest(content),metadata=tr.metadata(path),raw=content)
        for key,record in previous.items():
            manager.verify_publisher(record)
            add(key+'.so')
            if native_module.is_native(record):
                native_module.verify_files(manager.root,key,record)
                for name in record['native_files']:
                    if name=='module.so':continue
                    add(key+'.pkg/'+name)
                for path in [manager.root/(key+'.pkg'),*(p for p in (manager.root/(key+'.pkg')).rglob('*') if p.is_dir())]:
                    directories[path.relative_to(manager.root).as_posix()]=tr.metadata(path)
        state_raw=tr.canonical(state)
        tr.atomic(folder/'module-state-before.json',state_raw)
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,row in files.items():archive.writestr(name,row.pop('raw'))
        archive=output.getvalue();tr.atomic(folder/'module-owned-before.zip',archive)
        backup=dict(id=module['id'],new_key=release.sha(raw),previous=list(previous),files=files,directories=directories,
                    state_sha256=tr.digest(state_raw),archive_sha256=tr.digest(archive),state_existed=(manager.root/'state.json').exists(),
                    state_metadata=tr.metadata(manager.root/'state.json') if (manager.root/'state.json').exists() else None)
        tr.atomic(folder/'module-backup.json',tr.canonical(backup))


def restore_module(folder):
    try:backup=json.loads(protected(folder/'module-backup.json'))
    except FileNotFoundError:return
    raw=protected(folder/'module-state-before.json');archive=protected(folder/'module-owned-before.zip',32*1024*1024)
    if tr.digest(raw)!=backup['state_sha256'] or tr.digest(archive)!=backup['archive_sha256']:
        raise ValueError('The offline module recovery backup changed')
    for name in [*backup['files'],*backup['directories']]:
        if not __import__('re').fullmatch(r'[a-f0-9]{64}\.(?:so|pkg(?:/[A-Za-z0-9_./-]+)?)',name):
            raise ValueError('Invalid offline module recovery path')
        tr.safe(MODULES,name)
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        if set(source.namelist())!=set(backup['files']):raise ValueError('Incomplete offline module recovery assets')
        for name,meta in sorted(backup['directories'].items(),key=lambda item:len(item[0])):
            path=tr.safe(MODULES,name);path.mkdir(parents=True,exist_ok=True,mode=0o700)
            os.chown(path,meta['uid'],meta['gid']);os.chmod(path,meta['mode'])
        for name,row in backup['files'].items():
            content=source.read(name)
            if tr.digest(content)!=row['sha256']:raise ValueError('An offline recovery asset changed')
            path=tr.safe(MODULES,name);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            tr.atomic(path,content,row['metadata'])
    state=tr.safe(MODULES,'state.json')
    if backup['state_existed']:tr.atomic(state,raw,backup['state_metadata'])
    elif state.exists():state.unlink();tr.sync_dir(MODULES)


def module_healthy(folder):
    try:record=json.loads(protected(folder/'module-record.json'))
    except FileNotFoundError:return not (folder/'module-backup.json').exists()
    try:active=json.loads(Path('/run/hector-module-catalog/activation.json').read_bytes())['active']
    except (FileNotFoundError,ValueError,KeyError):return False
    return record['key'] in active and active[record['key']]['id']==record['id']


def diagnostic_text(value,limit=512):
    return ''.join(c if c.isprintable() else ' ' for c in str(value))[:limit]


def diagnostic_error(error):
    return dict(type=type(error).__name__,detail=diagnostic_text(error),errno=getattr(error,'errno',None))


def required_boot_units(job):
    if job.get('module_only'):
        return ['hector-module-catalog.service','lightdm.service','jack.service']
    units=['hector-module-catalog.service','hector-module-audio-support.service',
           'hector-platform-flash-bootstrap.service','hector-platform-flash-status.service',
           'lightdm.service','jack.service']
    if job.get('platform_id')!=release.CORE_ID:units.append('hector-storage-manager.service')
    return units


def boot_health(folder,job,tx=None):
    """Evaluate each required condition; never confuse any-active with all-active."""
    result={}
    if tx is not None:
        try:tx.verify_installed();result['integrity']=dict(ok=True)
        except (OSError,ValueError,KeyError,TypeError) as error:
            result['integrity']=dict(ok=False,error=diagnostic_error(error))
        try:
            path=release.VERSION.with_name('runtime-version.json') if job.get('platform_id')==release.CORE_ID else release.VERSION
            raw=protected(path,4096);installed=json.loads(raw)
            result['descriptor']=dict(ok=installed['version']==job['version'],sha256=tr.digest(raw),
                expected=job['version'],observed=diagnostic_text(installed['version'],64))
        except (OSError,ValueError,KeyError,TypeError) as error:
            result['descriptor']=dict(ok=False,error=diagnostic_error(error))
    required=required_boot_units(job)
    try:
        units=subprocess.run(['/bin/systemctl','is-active',*required],capture_output=True,timeout=4)
        lines=units.stdout.decode('ascii',errors='replace').splitlines()
        result['units']=dict(ok=units.returncode==0 and lines==['active']*len(required),
            returncode=units.returncode,states={name:diagnostic_text(lines[i],32) if i<len(lines) else 'missing'
                for i,name in enumerate(required)},response_count=len(lines))
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        result['units']=dict(ok=False,error=diagnostic_error(error))
    try:result['module']=dict(ok=module_healthy(folder))
    except (OSError,ValueError,KeyError,TypeError) as error:
        result['module']=dict(ok=False,error=diagnostic_error(error))
    # This is diagnostic data only. Keep a digest and the expected module's
    # result, never credentials, complete publisher records, keys or preimages.
    try:
        marker=json.loads(protected(folder/'module-record.json',4096));key=marker['key']
        if not isinstance(key,str) or not __import__('re').fullmatch('[a-f0-9]{64}',key):raise ValueError('Invalid module receipt key')
        result['module'].update(expected_key=key,expected_id=diagnostic_text(marker['id'],64))
        raw=tr.regular(Path('/run/hector-module-catalog/activation.json'),4*1024*1024);activation=json.loads(raw)
        result['module'].update(activation_sha256=tr.digest(raw),key_present=key in activation['active'],
            observed_id=diagnostic_text(activation['active'].get(key,{}).get('id'),64),
            activation_error=diagnostic_text(activation.get('error'),384))
    except FileNotFoundError:
        if 'expected_key' in result['module']:result['module']['activation_missing']=True
        elif (folder/'module-backup.json').exists():result['module']['record_missing']=True
    except (OSError,ValueError,KeyError,TypeError) as error:
        result['module']['diagnostic_error']=diagnostic_error(error)
    return result


class VerificationLog:
    """Bounded protected evidence survives the original guarded rollback/reboot."""
    def __init__(self,folder,job):
        self.folder=folder;self.job=job;self.last=None;self.changed=None;self.history=[];self.warned=False

    def record(self,conditions,outcome='checking'):
        try:
            signature=tr.digest(tr.canonical(conditions))
            if outcome=='checking' and signature==self.last:return
            if signature!=self.last:
                self.last=signature
                self.changed=dict(monotonic=round(time.monotonic(),3),conditions=conditions)
                self.history.append(dict(monotonic=self.changed['monotonic'],failed=[n for n,v in conditions.items() if not v['ok']]))
                self.history=self.history[-12:]
            value=dict(schema=1,transaction=self.job['id'],boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                installation_boot=self.job['boot'],release_key=self.job['key'],version=self.job['version'],
                outcome=outcome,last_changed=self.changed,changes=self.history)
            if outcome=='failed-before-rollback':
                required=required_boot_units(self.job)
                try:
                    props=subprocess.run(['/bin/systemctl','show',*required,
                        '--property=Id,LoadState,ActiveState,SubState,Result,ExecMainCode,ExecMainStatus,MainPID,ControlPID,Job'],capture_output=True,timeout=4)
                    value['terminal_units']=dict(code=props.returncode,properties=props.stdout.decode(errors='replace')[:16384])
                    args=['/bin/journalctl','-b','--no-pager','-n','60','-o','short-monotonic']
                    # Fixed system publishers/verifier only; no user UI/session
                    # or browser request logs are included in this receipt.
                    for unit in required:
                        if unit not in ('lightdm.service','jack.service','hector-storage-manager.service'):args+=['-u',unit]
                    args+=['-u','hector-platform-verify.service']
                    journal=subprocess.run(args,capture_output=True,timeout=4)
                    value['terminal_journal']=dict(code=journal.returncode,text=journal.stdout.decode(errors='replace')[-16384:])
                except (OSError,ValueError,subprocess.SubprocessError) as error:value['terminal_capture_error']=diagnostic_error(error)
            import stat
            for parent in (self.folder,*self.folder.parents):
                info=parent.lstat()
                if info.st_uid!=0 or not stat.S_ISDIR(info.st_mode) or info.st_mode&0o022:raise ValueError('Unprotected verification log directory')
                if parent==DATA:break
            if self.folder.stat().st_mode&0o077:raise ValueError('Verification log directory must be private')
            raw=tr.canonical(value)
            if len(raw)>48*1024:raise ValueError('Verification diagnostic budget exceeded')
            tr.atomic(self.folder/'verification.json',raw)
        except Exception as error:
            # Logging must never prevent an already-authorized recovery.
            if not self.warned:
                self.warned=True
                print('Cannot retain boot verification diagnostics: '+diagnostic_text(error),file=sys.stderr,flush=True)


def verify_boot(folder,job,tx=None):
    log=VerificationLog(folder,job);deadline=time.monotonic()+60;conditions={}
    while time.monotonic()<deadline:
        conditions=boot_health(folder,job,tx)
        healthy=all(row['ok'] for row in conditions.values())
        log.record(conditions,'healthy' if healthy else 'checking')
        if healthy:return True
        time.sleep(1)
    if not conditions:
        conditions=dict(deadline=dict(ok=False,error=dict(type='TimeoutError',detail='No boot health sample completed before the verification deadline')))
    log.record(conditions,'failed-before-rollback')
    job['error']='Postboot verification failed: '+', '.join(n for n,v in conditions.items() if not v['ok'])
    try:tr.atomic(folder/'job.json',tr.canonical(job))
    except Exception as error:
        print('Cannot retain boot verification failure summary: '+diagnostic_text(error),file=sys.stderr,flush=True)
    return False


def run_module(raw,source_sha):
    """Existing offline runtime: stage data and leave a protected boot receipt."""
    from module_offline import stage
    if os.geteuid()!=0:raise ValueError('The trusted offline worker is required')
    from module_manager import ModuleManager,RUNTIME
    from publisher_release import PublisherPolicy
    import native_module
    policy=PublisherPolicy()
    if ModuleManager.native_module_zip(raw):module,_,proof,_=native_module.read(raw,policy)
    else:module,_,proof=policy.read(raw)
    # Older signed USB ZIPs may call this installed API early during boot.
    # Keep them compatible while preventing a module restart from interrupting
    # the independent repair's UI/audio confirmation.
    import system_support
    system_support.requirement(module,Path('/'),staging=True)
    prepared=system_support.usb_prepare_repair(protected(Path(__file__).with_name('flash-repair.zip'),512*1024),policy,source_sha)
    if prepared['reboot_requested']:return prepared
    manager=ModuleManager(MODULES,RUNTIME,'/mnt/presets',0,publishers=policy)
    with manager.locked():
        state=manager.state()
        for key,record in state['applied'].items():
            if (record['id']==module['id'] and record['version']==module['version']
                    and record.get('publisher_proof')==proof['publisher_proof'] and state['target'].get(key)==record):
                manager.verify_publisher(record)
                if native_module.is_native(record):native_module.verify_files(manager.root,key,record)
                if tr.digest(tr.regular(manager.root/(key+'.so')))!=record['binary_sha256']:raise ValueError('The installed module differs')
                mark_processed(dict(source_release_sha256=source_sha,module_id=module['id'],version=module['version'],status='verified'))
                return dict(status='already-installed',reboot_requested=False)
    if os.geteuid()!=0 or MARKER.exists():raise ValueError('Hector is already updating or restarting')
    fd=os.open(MARKER,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
    token=uuid.uuid4().hex
    DATA.mkdir(mode=0o700,parents=True,exist_ok=True)
    folder=DATA/'jobs'/token;folder.mkdir(mode=0o700,parents=True)
    job=dict(id=token,key=source_sha,source_release_sha256=source_sha,module_id=module['id'],module_name=module['name'],
             boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
             module_only=True,manifest=None,version=module['version'],engine=independent_engine(folder),status='installing')
    tr.atomic(folder/'job.json',tr.canonical(job));tr.atomic(DATA/'current.json',tr.canonical(dict(id=token)))
    try:
        marker=stage(raw,folder,Path('/'),source_sha)
        job.update(version=module['version'],module_id=module['id'],module_name=module['name'],status='restarting')
        tr.atomic(folder/'job.json',tr.canonical(job));progress(job,'restarting',95)
        engine.command(['/bin/systemctl','reboot'])
        return dict(status='installed-reboot-required',reboot_requested=True)
    except Exception:
        restore_module(folder);job['status']='restored-verified';tr.atomic(folder/'job.json',tr.canonical(job))
        mark_processed(job)
        MARKER.unlink(missing_ok=True);raise


def recover_module(folder,job,verify):
    if not verify and job['status']=='restarting':return
    if verify and job['status']=='restarting':
        if verify_boot(folder,job):
            job['status']='verified';tr.atomic(folder/'job.json',tr.canonical(job))
            mark_processed(job)
            progress(job,'installed',100);MARKER.unlink(missing_ok=True);return
    if verify:
        identity={name:tr.digest(Path(name).read_bytes()) for name in engine.IDENTITY_PATHS}
        device=engine.Device(dict(identity_files=identity));device.pause()
    restore_module(folder)
    job['status']='restored-verified';tr.atomic(folder/'job.json',tr.canonical(job))
    mark_processed(job)
    progress(job,'failed',0,'The previous module version was restored.')
    if verify:device.reboot()
    else:MARKER.unlink(missing_ok=True)


def run_install(raw,request,before_restart=None):
    from system_readiness import wait
    info,files,_=release.read(raw)
    # Physical USB migrates storage first. Both app and module transactions
    # require confirmed paired support before any preset access.
    import system_support
    from publisher_release import PublisherPolicy
    repair=system_support.validate_runtime(info,files,PublisherPolicy())
    component,_=system_support.read(repair,PublisherPolicy())
    if component['version']!=system_support.PAIRED_VERSION:raise ValueError('The replacement must include the reviewed paired preset-storage support')
    wait()
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if request['boot']!=boot or request['key']!=release.sha(raw) or request.get('saved_patch') is not True:
        raise ValueError('The platform update request is no longer current')
    manifest,payload=release.select(info,files,Path('/overlay/lower'))
    manifest=bind(manifest,Path('/overlay/lower'),boot)
    device=engine.Device(manifest)
    device.data=DATA
    device.baseline(manifest,payload)
    DATA.mkdir(mode=0o700,parents=True,exist_ok=True)
    if DATA.stat().st_uid!=0 or DATA.stat().st_mode&0o077:raise ValueError('Unprotected platform recovery store')
    folder=DATA/'jobs'/request['id'];folder.mkdir(mode=0o700,parents=True)
    job=dict(request,version=info['version'],platform_id=info['id'],manifest=manifest,engine=independent_engine(folder),status='installing')
    tr.atomic(folder/'archive.zip',raw);tr.atomic(folder/'job.json',tr.canonical(job))
    tr.atomic(DATA/'current.json',tr.canonical(dict(id=request['id'])))
    approval=dict(action='install',manifest_sha256=tr.digest(tr.canonical(manifest)),identity=device.identity,
                  boot=boot,transaction=request['id'],owner_approved=True,transport_paused_confirmed=True,
                  reboot_approved=True,saved_patch_confirmed=True)
    total=len(payload);done=set()
    def checkpoint(stage,name):
        if stage=='after':done.add(name)
        progress(job,'installing',min(90,10+round(80*len(done)/total)))
        if stage=='after' and len(done)==total and before_restart:before_restart(folder,job)
    progress(job,'installing',10)
    try:
        result=engine.operate(device,'install',manifest,payload,approval,request['id'],checkpoint)
    except Exception as error:
        # Preserve pre-write errors as well as transaction results. Early boot
        # can fail while reading presets, before operate enters its write block.
        job.update(status='worker-failed',error=str(error))
        tr.atomic(folder/'job.json',tr.canonical(job))
        progress(job,'failed',0,str(error))
        raise
    job['status']=result['status'];tr.atomic(folder/'job.json',tr.canonical(job))
    success=result['status']=='installed-reboot-required'
    if not success:restore_module(folder)
    progress(job,'restarting' if success else 'failed',95 if success else 0,result.get('error'))
    if success:
        # Delete only the exact queued artifact that was installed.
        staged=MODULES/release.STAGED
        if staged.exists() and release.sha(tr.regular(staged,release.LIMIT))==request['key']:staged.unlink()
    return result



def consume_verified_upload(job):
    """Finish cleanup even when reboot stopped the original updater early."""
    staged=MODULES/release.STAGED
    if not staged.exists():return
    fd=os.open(str(MODULES/'lock'),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        try:raw=tr.regular(staged,release.LIMIT)
        except FileNotFoundError:return
        if release.sha(raw)==job['key']:
            staged.unlink()
            directory=os.open(str(MODULES),os.O_DIRECTORY|os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)


def run_usb_rollback(raw,request):
    """Explicit physical-USB restore of this exact installed platform transaction."""
    from system_readiness import wait
    wait()
    info,_,_=release.read(raw)
    current=json.loads(protected(DATA/'current.json'))
    token=current['id']
    if not __import__('re').fullmatch('[a-f0-9]{32}',token):raise ValueError('Invalid recovery transaction')
    folder=DATA/'jobs'/token;job=json.loads(protected(folder/'job.json'))
    source=tr.digest(tr.canonical(json.loads(release.archive_files(raw)['publisher.json'])))
    if (request.get('saved_patch') is not True or request.get('operation')!='rollback' or
            request['boot']!=Path('/proc/sys/kernel/random/boot_id').read_text().strip() or
            request['key']!=release.sha(raw) or job.get('source_release_sha256')!=source or
            job.get('platform_id')!=info['id'] or job['version']!=info['version'] or
            job['status']!='verified'):
        raise ValueError('Rollback requires the exact verified installed release and its protected backup')
    try:state=json.loads(tr.regular(MODULES/'state.json'))
    except FileNotFoundError:state={}
    if state.get('queued') or state.get('community_pending') or state.get('native_queued') or state.get('target',{})!=state.get('applied',{}):
        raise ValueError('Finish pending module changes before restoring Network Connection')
    manifest=job['manifest'];device=engine.Device(manifest);device.data=DATA
    device.paused_transport()
    reboot=device.reboot
    def restoring_reboot():
        job.update(status='restored-reboot-required',error=None)
        tr.atomic(folder/'job.json',tr.canonical(job))
        reboot()
    device.reboot=restoring_reboot
    approval=dict(action='rollback',manifest_sha256=tr.digest(tr.canonical(manifest)),identity=device.identity,
                  boot=device.boot,transaction=token,owner_approved=True,transport_paused_confirmed=True,
                  reboot_approved=True,saved_patch_confirmed=True)
    result=engine.operate(device,'rollback',manifest,{},approval,token)
    job.update(status=result['status'],error=result.get('error'))
    tr.atomic(folder/'job.json',tr.canonical(job))
    return result


def recover(verify=False):
    try:current=json.loads(protected(DATA/'current.json'))
    except FileNotFoundError:return
    token=current['id']
    if not isinstance(token,str) or len(token)!=32 or any(c not in '0123456789abcdef' for c in token):raise ValueError('Invalid platform job')
    folder=DATA/'jobs'/token;job=json.loads(protected(folder/'job.json'));manifest=job['manifest']
    if job.get('status')=='verified':
        consume_verified_upload(job);progress(job,'installed',100);return
    if job.get('status')=='restored-verified':
        progress(job,'failed',0,'The previous working version was restored.');return
    marker=('platform verification '+token+'\n').encode()
    if MARKER.exists() and tr.regular(MARKER)!=marker:raise ValueError('Another Hector operation is in progress')
    if not MARKER.exists():tr.atomic(MARKER,marker)
    if job.get('module_only'):
        if job['boot']!=Path('/proc/sys/kernel/random/boot_id').read_text().strip():recover_module(folder,job,verify)
        return
    store=DATA/'transactions'/token
    if not (store/'journal.json').exists():
        restore_module(folder)
        job['status']='restored-verified';tr.atomic(folder/'job.json',tr.canonical(job));mark_processed(job)
        progress(job,'failed',0,'The app update stopped before changing files.');MARKER.unlink(missing_ok=True);return
    key=protected(DATA/'key')
    tx=tr.Transaction(Path('/overlay/lower'),store,key);record=tx.load()
    if (record['phase']=='prepared' and not record['applied'] and
            not record.get('pending') and not record.get('pending_restore')):
        identity=tr.digest(tr.canonical({name:tr.digest(Path(name).read_bytes()) for name in engine.IDENTITY_PATHS}))
        if (record['identity']!=identity or
                record['package_digest']!=tr.digest(tr.canonical(manifest))):
            raise ValueError('Prepared recovery belongs to another device or package')
        tx.cancel_prepared();restore_module(folder)
        job['status']='restored-verified';tr.atomic(folder/'job.json',tr.canonical(job))
        mark_processed(job)
        progress(job,'failed',0,'The update stopped before changing files. The previous version is intact.')
        MARKER.unlink(missing_ok=True);return
    if record['phase']=='rolled-back':
        tx.verify_restored();restore_module(folder)
        job['status']='restored-verified';tr.atomic(folder/'job.json',tr.canonical(job))
        mark_processed(job)
        progress(job,'failed',0,'The previous app was restored.');MARKER.unlink(missing_ok=True);return
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if record['boot']==boot:return
    if record['phase']=='committed' and not verify:return
    if record['phase']=='committed':
        if verify_boot(folder,job,tx):
            progress(job,'installed',100)
            job['status']='verified';tr.atomic(folder/'job.json',tr.canonical(job));consume_verified_upload(job);mark_processed(job);MARKER.unlink(missing_ok=True);return
    if job.get('status')=='verified':return
    class RecoveryDevice(engine.Device):
        # A protected interrupted transaction authorizes recovery. The normal
        # engine still stops and verifies every consumer before remounting;
        # a stale pre-crash Link play flag must not prevent that recovery.
        def paused_transport(self):pass
    device=RecoveryDevice(manifest);device.data=DATA
    restore_module(folder)
    # Recovery is authorized by the saved signed transaction; no new upload or
    # UI command can change its rollback files, identity, or affected paths.
    approval=dict(action='rollback',manifest_sha256=tr.digest(tr.canonical(manifest)),identity=device.identity,
                  boot=device.boot,transaction=token,owner_approved=True,transport_paused_confirmed=True,
                  reboot_approved=True,saved_patch_confirmed=True)
    result=engine.operate(device,'rollback',manifest,{},approval,token)
    progress(job,'failed',0,'The app update did not finish. The previous app has been restored.' if result['status'].startswith('restored') else 'Recovery needs attention.')


def main():
    action=sys.argv[1]
    if action in ('recover','verify'):recover(action=='verify');return
    if action=='module':
        if len(sys.argv)!=4 or not __import__('re').fullmatch('[a-f0-9]{64}',sys.argv[3]):raise ValueError('Use a verified offline module release')
        print(json.dumps(run_module(tr.regular(Path(sys.argv[2]),16*1024*1024),sys.argv[3])));return
    if action!='install' or not MARKER.exists():raise ValueError('An explicit app update is required')
    with (MODULES/'lock').open('ab') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        request=json.loads(tr.regular(MODULES/'platform-request.json',4096))
        if not isinstance(request.get('id'),str) or not __import__('re').fullmatch('[a-f0-9]{32}',request['id']):raise ValueError('Invalid platform request')
        try:state=json.loads(tr.regular(MODULES/'state.json'))
        except FileNotFoundError:state=dict(queued={},applied={},target={})
        if state['queued'] or state['applied']!=state['target'] or state.get('community_pending') or state.get('native_queued'):
            raise ValueError('Apply pending module changes before updating the app')
        raw=tr.regular(MODULES/release.STAGED,release.LIMIT)
    # Pin the request and exact archive under the queue lock, then release it.
    # The restart marker rejects mutations. Catalog restoration needs this same
    # lock, so retaining it across stop/start would deadlock before-write recovery.
    completed=False
    try:
        progress(dict(request,version=request.get('version','')), 'checking',0)
        result=run_install(raw,request)
        completed=result.get('reboot_requested') is True
    except Exception as error:
        progress(dict(request,version=request.get('version','')), 'failed',0,str(error)[:300])
        raise
    finally:
        if not completed and not any(line.split()[4]=='/overlay/lower' and 'rw' in line.split()[5].split(',') for line in Path('/proc/self/mountinfo').read_text().splitlines()):
            MARKER.unlink(missing_ok=True)


if __name__=='__main__':
    os.umask(0o077)
    def interrupted(*unused):raise InterruptedError('App update interrupted')
    for sig in (signal.SIGTERM,signal.SIGINT,signal.SIGHUP):signal.signal(sig,interrupted)
    main()
