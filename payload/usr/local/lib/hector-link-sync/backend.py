"""Bounded sync recovery; Network Connection alone configures interfaces."""
import json
import os
from pathlib import Path
import signal
import socket
import stat
import struct
import subprocess
import threading
import time
import uuid

SOCKET='/run/hector-link-sync-control/control.sock'
PREFERENCE=Path('/mnt/community-lab/link-sync/private/connection.json')
SESSION='/run/hector-link-sync/session'
UNITS={k:'hector-link-sync-'+k+'.service' for k in ('ethernet','wifi')}
WIFI_NAMESPACE=Path('/run/hector-link-sync-control/wifi.namespace')
NAMESPACES={'ethernet':'/proc/1/ns/net','wifi':str(WIFI_NAMESPACE)}
INTERFACE_ENV=Path('/run/hector-link-sync-control/interface.env')


def command(args,timeout=8):
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if p.returncode: raise RuntimeError(p.stderr.strip()[:250] or 'Helper service unavailable')
    return p.stdout


def network():
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as peer:
        peer.settimeout(3);peer.connect('/run/hector-network-panel/control.sock')
        if struct.unpack('3i',peer.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]!=0:
            raise RuntimeError('Unexpected network service')
        peer.sendall(b'{"action":"status"}\n');raw=b''
        while b'\n' not in raw and len(raw)<65536:
            part=peer.recv(8192)
            if not part: break
            raw+=part
        if len(raw)>=65536: raise RuntimeError('Invalid network status')
        result=json.loads(raw)
        if not result.get('ok'): raise RuntimeError('Network status unavailable')
        return result['data']


def ready_connections(data):
    rows={}
    for kind in UNITS:
        row=data.get(kind,{})
        selected=(row if row.get('interface') else next((a for a in row.get('adapters',[]) if a.get('status')=='Connected'),{})) if kind=='ethernet' else dict(row,interface=row.get('interface') or next((a.get('interface','') for a in row.get('adapters',[]) if a.get('mac')==row.get('mac')),''))
        rows[kind]=dict(available=bool(row.get('enabled') and row.get('status')=='Connected' and selected.get('ip')),
                        ip=selected.get('ip',''),device=selected.get('name',''),interface=selected.get('interface',''))
    return rows


def control(*args):
    return json.loads(command(['/usr/sbin/runuser','-u','debian','--',
        '/usr/local/lib/hector-link-sync/control',SESSION,*args]))


def service_state(kind):
    raw=command(['/bin/systemctl','show',UNITS[kind],'--property=ActiveState,MainPID'])
    return dict(line.split('=',1) for line in raw.splitlines() if '=' in line)


def service_states():
    # One fresh snapshot per tick; no status cache to delay recovery or switching.
    raw=command(['/bin/systemctl','show',UNITS['ethernet'],UNITS['wifi'],
                 '--property=Id,ActiveState,MainPID'])
    result={}
    for block in raw.strip().split('\n\n'):
        fields=dict(line.split('=',1) for line in block.splitlines() if '=' in line)
        kind=next((k for k,u in UNITS.items() if fields.get('Id')==u),None)
        if kind is None or kind in result or not {'ActiveState','MainPID'}<=set(fields):
            raise RuntimeError('Invalid Link service status')
        result[kind]={k:v for k,v in fields.items() if k!='Id'}
    if set(result)!=set(UNITS):raise RuntimeError('Incomplete Link service status')
    return result


def helper_health(interface,session=SESSION):
    """Bounded read-only fallback; never claim a client lease or map for writes."""
    fd=None
    try:
        fd=os.open(session,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
        info=os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_uid!=1000 or info.st_mode&0o077:
            return 'invalid-session'
        raw=os.read(fd,72)
        if len(raw)!=72:return 'invalid-session'
        magic,version,size=struct.unpack_from('<QII',raw)
        if magic!=0x484543544c4e4b31 or version!=4 or size!=info.st_size or struct.unpack_from('<Q',raw,16)[0]!=1:
            return 'invalid-session'
        if raw[56:72].split(b'\0',1)[0].decode('ascii')!=interface:return 'interface-mismatch'
        current=os.lstat(session)
        if (current.st_dev,current.st_ino)!=(info.st_dev,info.st_ino):return 'session-replaced'
        age=time.clock_gettime(time.CLOCK_MONOTONIC_RAW)*1000000-struct.unpack_from('<Q',raw,24)[0]
        return 'fresh' if 0<=age<500000 else 'stale'
    except (OSError,ValueError,UnicodeError,struct.error):
        return 'unavailable'
    finally:
        if fd is not None:os.close(fd)


def report_event(value):
    print(json.dumps(value,sort_keys=True),flush=True)


def namespace_ok(kind,pid):
    return str(pid).isdigit() and int(pid)>0 and os.stat('/proc/'+str(pid)+'/ns/net').st_ino==os.stat(NAMESPACES[kind]).st_ino


def wifi_namespace_target(interface,sysnet=Path('/sys/class/net'),legacy=Path('/run/netns/hector-mala-trial')):
    if not isinstance(interface,str) or not __import__('re').fullmatch(r'[A-Za-z0-9_.-]{1,15}',interface):
        raise ValueError('Invalid selected interface')
    p=sysnet/interface
    if p.exists():
        dev=(p/'device').resolve(strict=True)
        usb=next(q for q in dev.parents if (q/'idVendor').exists())
        if ((p/'device/driver').resolve(strict=True).name!='ath9k_htc'
                or (usb/'idVendor').read_text().strip()!='0cf3'
                or (usb/'idProduct').read_text().strip()!='9271'):
            raise ValueError('Unexpected native Wi-Fi adapter')
        return '/proc/1/ns/net'
    if not legacy.exists():raise ValueError('Selected Wi-Fi namespace is unavailable')
    return str(legacy)


def write_wifi_namespace(interface):
    target=wifi_namespace_target(interface)
    temp=WIFI_NAMESPACE.with_name('namespace.'+uuid.uuid4().hex)
    try:
        os.symlink(target,str(temp))
        os.replace(str(temp),str(WIFI_NAMESPACE))
    finally:
        if temp.is_symlink():temp.unlink()


def write_interface(interface):
    if not isinstance(interface,str) or not __import__('re').fullmatch(r'[A-Za-z0-9_.-]{1,15}',interface):
        raise ValueError('Invalid selected interface')
    temp=INTERFACE_ENV.with_name('interface.'+uuid.uuid4().hex)
    try:
        fd=os.open(str(temp),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as f:f.write('HECTOR_LINK_INTERFACE='+interface+'\n');f.flush();os.fsync(f.fileno())
        os.replace(str(temp),str(INTERFACE_ENV))
    finally:
        if temp.exists():temp.unlink()


class Controller:
    MAX_ATTEMPTS=4
    START_GRACE=3.0
    STABLE_SECONDS=15.0
    UNHEALTHY_SECONDS=3.0
    UNHEALTHY_CHECKS=3

    def __init__(self,preference=PREFERENCE,clock=time.monotonic,report=report_event):
        self.preference=preference;self.clock=clock
        self.report=report
        self.lock=threading.Lock();self.operation=threading.Lock()
        self.stopping=threading.Event()
        self.selected='ethernet'
        if preference.exists():
            saved=json.loads(preference.read_text())
            if set(saved)!={'connection'} or saved['connection'] not in UNITS:
                raise ValueError('Invalid saved connection')
            self.selected=saved['connection']
        self.engine='';self.interface='';self.engine_interface='';self.pending=None;self.applying=False
        self.attempts=0;self.retry_at=0.;self.started_at=None;self.healthy_since=None;self.was_available=False
        self.failure_identity=None;self.failure_since=None;self.failure_checks=0
        self.unhealthy_since=None;self.unhealthy_checks=0;self.last_status_error=''
        self.data=dict(online=False,selected=self.selected,active='',status='Checking…',ip='',busy=False,connections={},session={},attempts=0)

    def save(self):
        self.preference.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        temp=self.preference.with_name(self.preference.name+'.'+uuid.uuid4().hex)
        try:
            fd=os.open(str(temp),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,'w') as f:
                json.dump({'connection':self.selected},f);f.flush();os.fsync(f.fileno())
            os.replace(str(temp),str(self.preference))
            fd=os.open(str(self.preference.parent),os.O_RDONLY|os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
        finally:
            if temp.exists(): temp.unlink()

    def reset_recovery(self):
        self.attempts=0;self.retry_at=0.;self.started_at=self.healthy_since=None

    def clear_status_failure(self,recovered=False):
        if recovered and self.failure_since is not None:
            self.report(dict(event='link_status_recovered',connection=self.selected,checks=self.failure_checks))
        self.failure_identity=None;self.failure_since=None;self.failure_checks=0
        self.unhealthy_since=None;self.unhealthy_checks=0;self.last_status_error=''

    def confirmed_unhealthy(self,now,own,reason):
        identity=(self.selected,self.interface,own.get('MainPID'))
        if self.failure_identity!=identity:
            self.clear_status_failure();self.failure_identity=identity
        first=self.failure_since is None
        if first:self.failure_since=now
        self.failure_checks+=1;self.last_status_error=reason
        heartbeat=helper_health(self.interface)
        if first:
            self.report(dict(event='link_status_failed',connection=self.selected,
                             reason=reason[:250],heartbeat=heartbeat))
        if heartbeat=='fresh':
            self.unhealthy_since=None;self.unhealthy_checks=0
            return False,heartbeat
        if self.unhealthy_since is None:self.unhealthy_since=now
        self.unhealthy_checks+=1
        return (self.unhealthy_checks>=self.UNHEALTHY_CHECKS and
                now-self.unhealthy_since>=self.UNHEALTHY_SECONDS),heartbeat

    def request(self,value):
        if value=={'action':'status'}:
            with self.lock: return dict(ok=True,data=dict(self.data))
        retry=value=={'action':'retry'}
        if not retry and (not isinstance(value,dict) or set(value)!={'action','connection'} or value.get('action')!='set' or value['connection'] not in UNITS):
            return dict(ok=False,error='Invalid request')
        with self.lock:
            if self.applying: return dict(ok=False,error='Connection change in progress')
            target=self.selected if retry else value['connection']
            if not retry and not self.data.get('connections',{}).get(target,{}).get('available'):
                return dict(ok=False,error='Enable and connect this adapter in Network Connection first')
            self.pending=target;self.applying=True
            return dict(ok=True,data=dict(self.data,busy=True))

    def apply_pending(self):
        with self.lock: target,self.pending=self.pending,None
        if target is None: return
        old=self.selected
        if target!=old:
            self.selected=target
            try: self.save()
            except Exception:
                self.selected=old
                with self.lock: self.applying=False
                raise
            if self.engine:
                command(['/bin/systemctl','stop',UNITS[self.engine]],12);self.engine=''
        self.reset_recovery()
        self.clear_status_failure()

    def publish(self,connections,state=None,error='',status=None):
        state=state or {};row=connections.get(self.selected,{})
        helper=bool(state.get('online'));attached=bool(state.get('sync_attached'));processing=bool(state.get('sync_processing'))
        if status is None:
            if not row.get('available'): status='Connection unavailable'
            elif not helper: status='Recovery paused — choose Retry' if self.attempts>=self.MAX_ATTEMPTS else 'Reconnecting…'
            elif not attached: status='Network connected — add Ableton Link to a patch'
            elif not processing: status='Module attached — waiting for audio engine'
            elif not state.get('enabled'): status='Link Off'
            elif not state.get('peers'): status='Waiting for Link peers'
            else: status='Ready'
        with self.lock:
            self.applying=self.pending is not None
            self.data=dict(online=helper,selected=self.pending or self.selected,active=self.engine if helper else '',
                status=status,ip=row.get('ip','') if row.get('available') else '',busy=self.applying,connections=connections,
                session=state,error=error,attempts=self.attempts,network_connected=bool(row.get('available')),
                sync_attached=attached,sync_processing=processing,
                ready=helper and attached and processing and bool(state.get('enabled')) and bool(state.get('peers')))

    def recover(self,now,active,reason='helper-not-running',heartbeat='unavailable'):
        if self.stopping.is_set() or self.attempts>=self.MAX_ATTEMPTS or now<self.retry_at: return
        self.attempts+=1;self.retry_at=now+min(30.,2.**self.attempts);self.started_at=now
        # Recovery never requests tempo or transport, including after switching.
        self.report(dict(event='link_helper_recovery',connection=self.selected,
                         action='restart' if active else 'start',reason=reason[:250],
                         heartbeat=heartbeat,checks=self.unhealthy_checks,
                         unhealthy_seconds=0 if self.unhealthy_since is None else max(0,now-self.unhealthy_since),
                         attempt=self.attempts))
        if self.selected=='wifi':write_wifi_namespace(self.interface)
        write_interface(self.interface)
        command(['/bin/systemctl','restart' if active else 'start',UNITS[self.selected]],12)
        self.engine_interface=self.interface
        self.engine=self.selected

    def update(self):
        now=self.clock();connections=ready_connections(network());available=connections[self.selected]['available']
        other='wifi' if self.selected=='ethernet' else 'ethernet'
        states=service_states();opposite=states[other]
        if opposite.get('ActiveState') in ('active','activating','deactivating'):
            command(['/bin/systemctl','stop',UNITS[other]],12)
            # Unit dependencies may change own state after the stop operation.
            states[self.selected]=service_state(self.selected)
        if not available:
            self.clear_status_failure()
            own=states[self.selected]
            if own.get('ActiveState') in ('active','activating'):
                command(['/bin/systemctl','stop',UNITS[self.selected]],12)
            self.engine=''
            if self.was_available: self.reset_recovery()
            self.was_available=False;self.publish(connections);return
        if not self.was_available: self.reset_recovery()
        self.was_available=True
        self.interface=connections[self.selected].get('interface','')
        if not self.interface:raise ValueError('Selected connection has no interface')
        own=states[self.selected];active=own.get('ActiveState') in ('active','activating');state={};reason='helper-not-running'
        if own.get('ActiveState')=='active' and (not self.engine_interface or self.engine_interface==self.interface):
            if not namespace_ok(self.selected,own.get('MainPID','0')):
                command(['/bin/systemctl','stop',UNITS[self.selected]],12);self.engine='';self.attempts=self.MAX_ATTEMPTS
                self.publish(connections,error='Helper used an unexpected network namespace',status='Connection needs review');return
            try:
                state=control('status')
                if not isinstance(state,dict):raise ValueError('Invalid Link status reply')
                if not state.get('online'):reason='helper-heartbeat-not-current'
            except Exception as error:
                state={};reason='status-read-failed: '+str(error)[:200]
        if state.get('online') and state.get('interface')!=self.interface:
            state={};reason='helper-interface-mismatch'
        if state.get('online'):
            self.clear_status_failure(recovered=True)
            self.engine=self.selected
            self.engine_interface=self.interface
            if self.healthy_since is None: self.healthy_since=now
            if now-self.healthy_since>=self.STABLE_SECONDS:
                self.reset_recovery();self.healthy_since=now
            self.publish(connections,state);return
        self.healthy_since=None
        heartbeat='unavailable'
        if active:
            confirmed,heartbeat=self.confirmed_unhealthy(now,own,reason)
            if not confirmed:
                self.publish(connections,error='Link status temporarily unavailable',status='Checking Link status…')
                return
        else:self.clear_status_failure()
        if self.started_at is None or now-self.started_at>=self.START_GRACE:
            self.recover(now,active,reason,heartbeat)
        self.publish(connections,error='Waiting for Link helper')

    def tick(self):
        if self.stopping.is_set(): return
        if not self.operation.acquire(False): return
        try: self.apply_pending();self.update()
        except Exception as error:
            self.publish(self.data.get('connections',{}),error=str(error)[:250],status='Connection unavailable')
        finally: self.operation.release()


def main():
    c=Controller();stopping=c.stopping
    signal.signal(signal.SIGTERM,lambda *_:stopping.set());signal.signal(signal.SIGINT,lambda *_:stopping.set())
    def update():
        while not stopping.is_set(): c.tick();stopping.wait(.5)
    worker=threading.Thread(target=update,daemon=True);worker.start()
    if os.path.lexists(SOCKET): os.unlink(SOCKET)
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as listener:
        listener.bind(SOCKET);os.chown(SOCKET,0,1000);os.chmod(SOCKET,0o660);listener.listen(4);listener.settimeout(.5)
        while not stopping.is_set():
            try: peer,_=listener.accept()
            except socket.timeout: continue
            with peer:
                peer.settimeout(2)
                try:
                    uid=struct.unpack('3i',peer.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]
                    if uid not in (0,1000): raise ValueError('Unauthorized user')
                    raw=b''
                    while b'\n' not in raw and len(raw)<4096:
                        part=peer.recv(4096)
                        if not part: break
                        raw+=part
                    if len(raw)>=4096: raise ValueError('Request too large')
                    reply=c.request(json.loads(raw))
                except Exception: reply=dict(ok=False,error='Invalid request')
                try: peer.sendall(json.dumps(reply).encode()+b'\n')
                except OSError: pass
    stopping.set();worker.join(65)
    if worker.is_alive(): raise RuntimeError('Controller worker did not stop')
    for unit in UNITS.values(): command(['/bin/systemctl','stop',unit],12)
    if os.path.lexists(SOCKET): os.unlink(SOCKET)


if __name__=='__main__': main()
