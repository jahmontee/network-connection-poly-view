# SPDX-License-Identifier: MIT
"""Late mutation admission. Never called by pre-UI catalog/recovery activation.

Observe PID1 completion BEFORE opening mutable repair state. No unit starts,
driver operations, preset reads or firmware writes occur in this module.
"""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

FIX='hector-flash-rx-fix.service'
CONFIRM='hector-flash-rx-confirm.service'
GUARD='hector-flash-rx-guard.service'
FALLBACK='hector-flash-rx-fallback.service'
BOOTSTRAP='hector-platform-flash-bootstrap.service'
UNITS=(FIX,CONFIRM,GUARD,FALLBACK,BOOTSTRAP)
PROPERTIES=('Id','LoadState','ActiveState','SubState','Type','RemainAfterExit',
            'Result','ExecMainCode','ExecMainStatus','ExecMainStartTimestampMonotonic',
            'ExecMainExitTimestampMonotonic','MainPID','ControlPID','InvocationID','Job')


class NotReady(ValueError):
    def __init__(self, phase, message, details=None):
        self.phase,self.details=phase,details or {}
        super().__init__('Flash Repair '+phase+': '+message)


def protected(path, root=Path('/'), limit=8192):
    """Strict immutable snapshot, with descriptor diagnostics on refusal."""
    owner=0 if root==Path('/') else os.geteuid()
    for parent in [path,*path.parents]:
        info=parent.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid!=owner or info.st_mode&0o022:
            raise NotReady('snapshot','Unprotected path',dict(path=str(parent)))
        if parent==root:break
    fd=os.open(str(path),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        detail=dict(path=str(path),inode=info.st_ino,device=info.st_dev,nlink=info.st_nlink,
                    mode=stat.S_IMODE(info.st_mode),uid=info.st_uid,size=info.st_size)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=owner
                or info.st_mode&0o022 or not 0<info.st_size<=limit):
            raise NotReady('snapshot','Expected a bounded protected single-link regular file',detail)
        with os.fdopen(fd,'rb') as stream:
            fd=None;raw=stream.read(limit+1)
        if len(raw)>limit:raise NotReady('snapshot','Read budget exceeded',detail)
        value=json.loads(raw)
        if not isinstance(value,dict):raise NotReady('snapshot','Expected an object',detail)
        return value
    finally:
        if fd is not None:os.close(fd)


def query_units(timeout):
    args=['/bin/systemctl','show','--no-pager','--all','--property='+','.join(PROPERTIES),'--',*UNITS]
    result=subprocess.run(args,capture_output=True,text=True,timeout=timeout,
                          env=dict(PATH='/usr/sbin:/usr/bin:/sbin:/bin',LC_ALL='C'))
    if result.returncode or len(result.stdout)>65536:
        raise NotReady('services','PID1 query failed',dict(returncode=result.returncode,stderr=result.stderr[-600:]))
    units={}
    for group in result.stdout.strip().split('\n\n'):
        row={}
        for line in group.splitlines():
            key,sep,value=line.partition('=')
            if not sep or key in row:raise NotReady('services','Malformed PID1 reply')
            row[key]=value
        name=row.get('Id')
        if name not in UNITS or name in units:raise NotReady('services','Unexpected PID1 unit identity')
        units[name]=row
    if set(units)!=set(UNITS):raise NotReady('services','Incomplete PID1 reply')
    return units


def number(row,key):
    value=row.get(key,'')
    if not re.fullmatch(r'[0-9]+',value):raise NotReady('services','Missing or invalid '+key,dict(unit=row.get('Id')))
    return int(value)


def settled(units):
    """Return False only while an expected boot writer can still be running."""
    waiting=False
    for name,row in units.items():
        load=row.get('LoadState')
        if load=='not-found' and name==BOOTSTRAP:continue
        if load!='loaded':raise NotReady('services','Required unit is not loaded',dict(unit=name,load=load))
        expected_type='notify' if name==GUARD else 'oneshot'
        if row.get('Type')!=expected_type:raise NotReady('services','Unexpected unit type',dict(unit=name))
        if row.get('Result')!='success' or row.get('ActiveState')=='failed':
            raise NotReady('services','Boot writer failed',dict(unit=name,result=row.get('Result')))
        start=number(row,'ExecMainStartTimestampMonotonic')
        if name==FALLBACK and (start or row.get('Job') not in ('','0') or row.get('ActiveState')!='inactive'):
            raise NotReady('fallback','Recovery to the stock driver was requested',dict(unit=name))
        if (row.get('Job') not in ('','0') or number(row,'MainPID') or number(row,'ControlPID')
                or row.get('ActiveState') in ('activating','deactivating','reloading')):
            waiting=True;continue
        if name==FALLBACK:continue
        if not start and name==GUARD:
            # Unanchored successful guardian units may be garbage collected.
            # Its protected current-boot final receipt is mandatory below;
            # inactive/default success alone never authorizes anything.
            continue
        if not start:
            # Confirm may not have been scheduled yet. Never accept its default
            # zero exit status as a completed invocation.
            waiting=True;continue
        expected=('active','exited') if name in (FIX,BOOTSTRAP) else ('inactive','dead')
        if (row.get('ActiveState'),row.get('SubState'))!=expected:
            raise NotReady('services','Unexpected completed service state',dict(unit=name))
        if row.get('RemainAfterExit')!=('yes' if name in (FIX,BOOTSTRAP) else 'no'):
            raise NotReady('services','Unexpected service lifetime',dict(unit=name))
        if number(row,'ExecMainCode')!=1 or number(row,'ExecMainStatus')!=0:
            raise NotReady('services','Boot writer did not exit successfully',dict(unit=name))
        if number(row,'ExecMainExitTimestampMonotonic')<start:
            raise NotReady('services','Boot writer has no completed invocation',dict(unit=name))
        if not re.fullmatch(r'[a-f0-9]{32}',row.get('InvocationID','')):
            raise NotReady('services','Missing invocation identity',dict(unit=name))
    return not waiting


def same_generation(first,second):
    if any(first[n]!=second[n] for n in UNITS if n!=GUARD):return False
    before,after=first[GUARD],second[GUARD]
    if before==after:return True
    # PID1 can garbage collect this unanchored successful notify unit after
    # process exit. The anchored fix/confirm/bootstrap identities and protected
    # current-boot disarm receipt still bind completion. A new nonzero ID, a
    # pending job, a live PID or any failure is not this collection transition.
    return (number(before,'ExecMainStartTimestampMonotonic')>0 and before.get('InvocationID')
        and after.get('LoadState')=='loaded' and after.get('ActiveState')=='inactive'
        and after.get('SubState')=='dead' and after.get('Result')=='success'
        and after.get('InvocationID')=='' and after.get('Job') in ('','0')
        and all(number(after,k)==0 for k in ('MainPID','ControlPID','ExecMainCode','ExecMainStatus',
                    'ExecMainStartTimestampMonotonic','ExecMainExitTimestampMonotonic')))


def wait(*, timeout=90, allow_absent=False, allow_legacy_upgrade=False, allow_initial_staging=False, continuation_marker=None, root=Path('/'), query=None, emit=print):
    """Read-only current-boot gate; timeout=0 performs one bounded admission."""
    import system_support_contract as contract
    root=Path(root);query=query or query_units
    lower=root/'overlay/lower'
    present=[os.path.lexists(lower/name) for name in contract.FILES]
    legacy_full=allow_legacy_upgrade and all(os.path.lexists(lower/name) for name in contract.LEGACY_FILES)
    complete=all(present) or legacy_full
    if not any(present) and allow_absent:
        return dict(status='absent',boot=(root/'proc/sys/kernel/random/boot_id').read_text().strip())
    if not complete and not allow_initial_staging:raise NotReady('inventory','Repair is absent or incomplete; use independent recovery')
    current=(root/'proc/sys/kernel/random/boot_id').read_text().strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',current):raise NotReady('boot','Invalid boot identity')
    started=time.monotonic();deadline=started+max(timeout,1.5);next_message=started
    last={}
    try:
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise NotReady('deadline','Boot confirmation did not finish',last)
            first=query(min(3,remaining,.6 if timeout==0 else 3));last=first
            if allow_initial_staging:
                from system_support import initial_staging_ready
                if initial_staging_ready(root,first,continuation_marker):
                    second=query(min(3,max(.1,deadline-time.monotonic())))
                    if first!=second:raise NotReady('services','Initial recovery service generation changed')
                    emit('Authenticated initial support publication needs recovery; no preset or app admission.')
                    return dict(status='initial-staging-recovery',boot=current)
            if not complete:raise NotReady('inventory','Incomplete support has no safe authenticated initial recovery')
            if settled(first):
                run=root/'run/hector-flash-rx-fix';data=root/'mnt/community-lab/hector-flash-rx-fix'
                guard=protected(run/'guard-complete.json',root)
                state=protected(data/'state.json',root)
                status=protected(run/'status.json',root)
                driver=(root/'sys/devices/platform/soc/1c68000.spi/driver').resolve().name
                if not (guard.get('boot')==current and guard.get('confirmed') is True
                        and guard.get('fallback') is False and guard.get('watchdog_disarmed') is True
                        and state.get('enabled') is True and state.get('confirmed_boot')==current
                        and state.get('pending_boot') is None and state.get('status')=='confirmed'
                        and status.get('boot')==current and status.get('status')=='active'
                        and status.get('confirmed') is True and driver=='hector-sun6i-rx-fix'):
                    raise NotReady('snapshot','Current boot has no complete healthy repair confirmation')
                paired=state.get('version')=='0.1.0-spi-rx.9'
                if not paired:
                    if not allow_legacy_upgrade or state.get('version') not in contract.LEGACY_VERSIONS:
                        raise NotReady('support-version','Paired preset-storage support is required before normal mutations')
                else:
                    note=root/'sys/module/jffs2/notes/.note.gnu.build-id'
                    if (state.get('module_sha256')!='f17184df4b6a8e4f75dcce5062e3bb43cadb0b325faa180c74c95a127cea869e'
                            or status.get('filesystem')!='fixed' or not note.is_file()
                            or note.read_bytes().hex()!='040000001400000003000000474e5500596f746eb07055e6e5a23d013e0810da21437a5d'):
                        raise NotReady('filesystem','The reviewed preset filesystem is not loaded')
                remaining=deadline-time.monotonic()
                if remaining<=0:raise NotReady('deadline','Snapshot exceeded admission deadline')
                second=query(min(3,remaining,.6 if timeout==0 else 3))
                if (settled(second) and same_generation(first,second)
                        and (root/'proc/sys/kernel/random/boot_id').read_text().strip()==current):
                    emit('Flash Repair writers completed; current boot confirmed and watchdog disarmed.')
                    return dict(status='ready',boot=current,support_version=state['version'],legacy_upgrade_only=not paired,invocations={n:r.get('InvocationID') for n,r in first.items()})
                raise NotReady('services','Repair invocation changed during admission; review recovery')
            if timeout==0:raise NotReady('waiting','Boot confirmation is still running; wait before changing modules')
            now=time.monotonic()
            if now>=next_message:
                emit('Waiting for Flash Repair services to finish. No preset reads or module changes.');next_message=now+10
            time.sleep(min(.25,max(0,deadline-time.monotonic())))
    except NotReady as exc:
        emit(json.dumps(dict(phase=exc.phase,error=str(exc),boot=current,details=exc.details),sort_keys=True));raise
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        detail=dict(type=type(exc).__name__,errno=getattr(exc,'errno',None),path=str(getattr(exc,'filename','') or ''))
        emit(json.dumps(dict(phase='readiness',error=str(exc),boot=current,details=detail),sort_keys=True))
        raise NotReady('readiness','Cannot verify repair completion',detail) from exc


def require_mutation():
    """Unprivileged UI asks the fixed trusted socket; root rechecks directly."""
    if os.geteuid()==0:return wait(timeout=0)
    from module_restart import ModuleRestart
    return ModuleRestart().require_ready()
