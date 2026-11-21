"""Shared overview; existing Ethernet and Wi-Fi setup managers retain ownership.

Only Ethernet's existing local API and WLAN rfkill switches are writable.
There are no Link, DSP, namespace, profile, driver or service-control operations.
"""
import json
import os
from pathlib import Path
import re
import socket
import stat
import struct
import threading
import time
import uuid

SOCKET='/run/hector-network-panel/control.sock'
PREFERENCE=Path('/mnt/community-lab/network-connection/private/wifi.json')
ETHERNET='/run/hector-ethernet-panel/control.sock'
WIFI='/run/hector-wifi-manager/control.sock'


def rpc(path,value):
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as peer:
        peer.settimeout(3)
        peer.connect(path)
        uid=struct.unpack('3i',peer.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]
        if uid!=0:
            raise RuntimeError('unexpected-service-owner')
        peer.sendall(json.dumps(value).encode()+b'\n')
        raw=b''
        while b'\n' not in raw and len(raw)<=131072:
            chunk=peer.recv(8192)
            if not chunk:break
            raw+=chunk
        if len(raw)>131072 or not raw.endswith(b'\n'):
            raise RuntimeError('invalid-response')
        return json.loads(raw)


class Radios:
    def __init__(self,root=Path('/sys/class/rfkill'),device=Path('/dev/rfkill'),writer=None):
        self.root,self.device,self.writer=root,device,writer

    def rows(self):
        rows=[]
        for path in self.root.glob('rfkill*'):
            if not re.fullmatch(r'rfkill[0-9]{1,10}',path.name):continue
            try:
                if (path/'type').read_text().strip()!='wlan':continue
                rows.append(dict(index=int(path.name[6:]),name=(path/'name').read_text().strip()[:128],
                                 soft=(path/'soft').read_text().strip()=='1',
                                 hard=(path/'hard').read_text().strip()=='1'))
            except OSError:continue
        return rows

    def set(self,old,blocked):
        current=next((r for r in self.rows() if r['index']==old['index'] and r['name']==old['name']),None)
        if current is None:return
        # Linux's packed 8-byte rfkill_event: WLAN=1, CHANGE=2. An individual
        # index is used; Bluetooth and non-WLAN radios are never affected.
        raw=struct.pack('=IBBBB',current['index'],1,2,int(blocked),0)
        if self.writer:
            self.writer(raw)
            return
        fd=os.open(str(self.device),os.O_WRONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
        try:
            if not stat.S_ISCHR(os.fstat(fd).st_mode):raise RuntimeError('invalid-radio-device')
            if os.write(fd,raw)!=len(raw):raise RuntimeError('short-radio-write')
        finally:os.close(fd)


class Supplicant:
    """Only connection state and manual disconnect/reconnect; no profiles."""
    root=Path('/run/hector-wifi-trial/private/wpa-control')
    runtime=Path('/run/hector-network-panel')

    @classmethod
    def available(cls,interface):
        if not isinstance(interface,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,15}',interface):return False
        try:
            st=(cls.root/interface).lstat()
            return stat.S_ISSOCK(st.st_mode) and st.st_uid==0
        except OSError:return False

    def __init__(self,interface):
        if not self.available(interface):raise RuntimeError('connection-control-unavailable')
        target=self.root/interface;st=target.lstat()
        self.identity=(interface,st.st_dev,st.st_ino)
        self.path=self.runtime/('reply-'+uuid.uuid4().hex)
        self.peer=socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM);self.peer.settimeout(3)
        try:
            self.peer.bind(str(self.path));os.chmod(str(self.path),0o600);self.peer.connect(str(target))
        except Exception:
            self.close();raise

    def command(self,value):
        if value not in ['STATUS','DISCONNECT','RECONNECT']:raise ValueError('unsupported-command')
        self.peer.send(value.encode());raw=self.peer.recv(65536)
        if len(raw)>=65536:raise RuntimeError('reply-too-large')
        return raw.decode('utf-8','replace').strip()

    def state(self):
        rows=dict(line.split('=',1) for line in self.command('STATUS').splitlines() if '=' in line)
        return rows.get('wpa_state','')

    def close(self):
        self.peer.close()
        if self.path.exists():self.path.unlink()

    def __enter__(self):return self
    def __exit__(self,*_):self.close()


class Controller:
    def __init__(self,state=PREFERENCE,call=rpc,radios=None,supplicant=Supplicant):
        self.file,self.call,self.radios,self.supplicant=state,call,radios or Radios(),supplicant
        self.applied={}
        self.lock=threading.Lock()
        self.enabled,self.owned=True,False
        if state.exists():
            saved=json.loads(state.read_text())
            if set(saved)!={'enabled'} or type(saved['enabled']) is not bool:raise RuntimeError('invalid-preference')
            self.enabled,self.owned=saved['enabled'],True
        self.data=dict(online=True,ethernet=dict(online=False,enabled=True,busy=False,adapters=[],status='Checking…'),
                       wifi=dict(online=False,enabled=self.enabled,busy=False,adapters=[],status='Checking…',error=''))
        self.applying=False

    def request(self,value):
        if value=={'action':'status'}:
            with self.lock:return dict(ok=True,data=dict(self.data))
        if (not isinstance(value,dict) or set(value)!={'action','transport','enabled'} or value['action']!='set'
                or value['transport'] not in ['ethernet','wifi'] or type(value['enabled']) is not bool):
            return dict(ok=False,error='invalid-request')
        if value['transport']=='ethernet':
            try:return self.call(ETHERNET,dict(action='set',enabled=value['enabled']))
            except Exception:return dict(ok=False,error='ethernet-unavailable')
        with self.lock:
            if self.applying or self.data['wifi'].get('busy'):return dict(ok=False,error='busy')
            # A setup/recovery operation already in progress must finish before
            # a radio change; the existing manager handles that operation.
            try:
                current=self.call(WIFI,{'action':'status'})
                if current.get('ok') and current['data'].get('busy'):return dict(ok=False,error='busy')
            except Exception:pass
            self.file.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            temp=self.file.with_suffix('.new')
            fd=os.open(str(temp),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            try:
                with os.fdopen(fd,'w') as f:
                    json.dump({'enabled':value['enabled']},f);f.flush();os.fsync(f.fileno())
                os.replace(str(temp),str(self.file))
            finally:
                if temp.exists():temp.unlink()
            self.enabled,self.owned=value['enabled'],True
            self.applied=({k:v for k,v in self.applied.items() if v is False} if self.enabled else {})
            self.data['wifi']=dict(self.data['wifi'],enabled=self.enabled,busy=True,error='')
        return dict(ok=True,data={'accepted':True})

    def refresh(self):
        try:
            reply=self.call(ETHERNET,{'action':'status'})
            if not reply.get('ok'):raise RuntimeError('unavailable')
            ethernet=dict(reply['data'])
            rows=ethernet.get('adapters',[])
            ethernet['status']=('Unavailable' if not ethernet.get('online') else 'Updating…' if ethernet.get('busy')
                else 'Off' if not ethernet.get('enabled') else 'No adapter' if not rows
                else 'Connected' if any(r.get('status')=='Connected' for r in rows)
                else rows[0].get('status','Disconnected'))
        except Exception:
            ethernet=dict(online=False,enabled=self.data['ethernet'].get('enabled',True),busy=False,
                          status='Unavailable',adapters=[],error='Ethernet controls are unavailable.')
        try:
            reply=self.call(WIFI,{'action':'status'})
            manager_online=bool(reply.get('ok'))
            data=reply['data'] if manager_online else {}
        except Exception:
            manager_online=False;data={}
        try:
            adapters=[{k:r.get(k,'') for k in ['mac','interface','phy','name','supported']} for r in data.get('adapters',[])]
            connection=data.get('connection',{})
            active=next((r for r in adapters if r['mac']==connection.get('adapter')),adapters[0] if len(adapters)==1 else {})
            native=bool(data.get('native_nm'))
            control_available=native or self.supplicant.available(active.get('interface'))
            with self.lock:
                self.applying=True
                enabled,owned=self.enabled,self.owned
            radio_error=''
            radios=self.radios.rows()
            if owned:
                try:
                    for radio in radios:
                        if radio['soft']==enabled:self.radios.set(radio,not enabled)
                    radios=self.radios.rows()
                    if any(r['soft']==enabled for r in radios):raise RuntimeError('radio-change-not-confirmed')
                except Exception:
                    radio_error='Could not confirm the Wi-Fi radio setting.'
                if native and not data.get('busy'):
                    try:
                        reply=self.call(WIFI,dict(action='radio',enabled=enabled))
                        if not reply.get('ok'):raise RuntimeError('native-radio-refused')
                        radio_error=''
                    except Exception:
                        radio_error='Could not confirm the Wi-Fi setting.'
                elif control_available and not data.get('busy'):
                    try:
                        with self.supplicant(active['interface']) as control:
                            state=control.state();previous=self.applied.get(control.identity)
                            if not enabled and (previous is not False or state not in ['DISCONNECTED','INACTIVE']):
                                if control.command('DISCONNECT')!='OK':raise RuntimeError('disconnect-refused')
                            elif enabled and (previous is False or previous is None and state=='DISCONNECTED'):
                                if control.command('RECONNECT')!='OK':raise RuntimeError('reconnect-refused')
                            self.applied[control.identity]=enabled
                        # The panel controls connection availability. A verified
                        # supplicant command also supports drivers without rfkill.
                        radio_error=''
                    except Exception:
                        radio_error='Could not confirm the Wi-Fi connection setting.'
                elif adapters and not radios and connection.get('wpa_state')=='COMPLETED' and not control_available:
                    radio_error='Wi-Fi connection control is unavailable.'
            blocked=any(r['soft'] or r['hard'] for r in radios)
            connected=enabled and not blocked and not radio_error and connection.get('wpa_state')=='COMPLETED'
            status=('Changing radio…' if radio_error else 'Off' if not enabled else 'Unavailable' if not manager_online else 'No adapter' if not adapters
                    else 'Radio blocked' if blocked else 'Connecting…' if data.get('busy')
                    else 'Connected' if connected and connection.get('ip_address') else 'Obtaining IP address' if connected else 'Disconnected')
            wifi=dict(online=manager_online,enabled=enabled,busy=bool(data.get('busy')),adapters=adapters,status=status,
                      name=active.get('name',''),mac=active.get('mac',''),ip=connection.get('ip_address','') if connected else '',
                      ssid=connection.get('ssid','') if connected else '',error=radio_error or ('' if manager_online else 'Wi-Fi setup is unavailable.'),
                      radio_control_available=bool(radios),connection_control_available=control_available,phase=data.get('phase','ready'))
        except Exception:
            wifi=dict(online=False,enabled=self.enabled,busy=False,adapters=[],status='Unavailable',name='',mac='',ip='',ssid='',
                      error='Wi-Fi setup is unavailable.')
        with self.lock:
            self.applying=False
            self.data=dict(online=True,ethernet=ethernet,wifi=wifi)


def main():
    controller=Controller()
    path=Path(SOCKET)
    path.parent.mkdir(mode=0o750,parents=True,exist_ok=True)
    if path.exists():path.unlink()
    server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);server.bind(str(path))
    os.chown(str(path.parent),0,1000);os.chmod(str(path.parent),0o750)
    os.chown(str(path),0,1000);os.chmod(str(path),0o660)
    server.listen(4);server.settimeout(.5)
    def worker():
        while True:
            controller.refresh();time.sleep(1.5)
    threading.Thread(target=worker,daemon=True).start()
    while True:
        try:peer,_=server.accept()
        except socket.timeout:continue
        with peer:
            peer.settimeout(1)
            try:
                _,uid,_=struct.unpack('3i',peer.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                if uid not in [0,1000]:raise ValueError('unauthorized')
                raw=b''
                while b'\n' not in raw and len(raw)<=1024:
                    chunk=peer.recv(1024)
                    if not chunk:break
                    raw+=chunk
                if len(raw)>1024:raise ValueError('oversize')
                result=controller.request(json.loads(raw))
            except Exception:result=dict(ok=False,error='service-unavailable')
            try:peer.sendall(json.dumps(result).encode()+b'\n')
            except OSError:pass


if __name__=='__main__':main()
