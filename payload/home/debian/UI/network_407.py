"""Asynchronous Qt5 bridge for one Network Connection utility."""
import json
from pathlib import Path
import re
from local_control_407 import control_process
from browser_access_407 import BrowserAccess
from PySide2.QtCore import QObject,Property,QProcess,QTimer,Signal,Slot


def installed_version():
    try:
        with Path('/opt/hector-storage-manager/platform-version.json').open('rb') as stream:
            record=json.loads(stream.read(513))
        value=record.get('version','')
        if record.get('id')=='network-connection-poly-view' and isinstance(value,str) and re.fullmatch(r'[0-9][A-Za-z0-9.+-]{0,63}',value):
            return value
    except (OSError,ValueError,AttributeError):pass
    return 'unavailable'


class NetworkHub(QObject):
    changed=Signal()
    def __init__(self,client='/usr/local/lib/hector-network-panel/client.py'):
        super().__init__()
        self.client=client
        self._installed_version=installed_version()
        self._browser_access = BrowserAccess(self)
        empty=dict(online=False,enabled=True,busy=False,adapters=[],status='Checking…')
        self.data=dict(online=False,ethernet=dict(empty),wifi=dict(empty))
        self.request=None;self.pending={};self.visible=False;self.active_pages=set()
        self.process=control_process(self,self.client)
        self.process.started.connect(self.send)
        self.process.finished.connect(self.complete)
        self.process.errorOccurred.connect(self.failed)
        self.timeout=QTimer(self);self.timeout.setSingleShot(True);self.timeout.timeout.connect(self.process.kill)
        self.timer=QTimer(self);self.timer.setInterval(1500);self.timer.timeout.connect(self.poll)

    status=Property('QVariantMap',lambda self:self.data,notify=changed)
    browserAccess = Property(QObject, lambda self: self._browser_access, constant=True)
    installedVersion = Property(str, lambda self: self._installed_version, constant=True)

    @Slot(str,bool)
    def activate(self,page,visible):
        if not isinstance(page,str) or not page.startswith(('overview-','wifi-')) or len(page)>80:return
        if visible:self.active_pages.add(page)
        else:self.active_pages.discard(page)
        self.visible=bool(self.active_pages)
        if self.visible:self.timer.start();self.poll()
        else:self.timer.stop()

    def call(self,request):
        if self.process.state()!=QProcess.NotRunning:
            if request['action']=='set':self.pending[request['transport']]=request
            return
        self.request=request
        self.process.start('/usr/bin/python3',['-I','-B',self.client]);self.timeout.start(6000)

    def send(self):
        self.process.write(json.dumps(self.request).encode()+b'\n');self.process.closeWriteChannel()

    @Slot()
    def poll(self):
        if self.visible:
            self.call({'action':'status'})
            self._browser_access.refresh()

    @Slot(str,bool)
    def setEnabled(self,transport,enabled):
        if transport in ['ethernet','wifi'] and self.data.get('online') and not self.data[transport].get('busy'):
            self.call(dict(action='set',transport=transport,enabled=enabled))

    def failed(self,error):
        if error==QProcess.FailedToStart:self.complete(-1,QProcess.NormalExit)

    def complete(self,code,_):
        if self.request is None:return
        self.timeout.stop();request,self.request=self.request,None
        raw=bytes(self.process.readAllStandardOutput());self.process.readAllStandardError()
        try:
            reply=json.loads(raw) if code==0 and len(raw)<=65536 else {}
            if reply.get('ok') and not reply['data'].get('accepted'):
                self.data=reply['data']
            elif request['action']=='set':
                transport=request['transport'];row=dict(self.data[transport])
                if reply.get('ok') or reply.get('error')=='busy':row.update(busy=True,error='')
                else:row.update(busy=False,error='Could not change this connection. Check its status and try again.')
                self.data=dict(self.data,**{transport:row})
            else:
                self.data=dict(self.data,online=False)
        except (ValueError,KeyError,TypeError):self.data=dict(self.data,online=False)
        self.changed.emit()
        if self.pending:
            transport=next(iter(self.pending));self.call(self.pending.pop(transport))

    def close(self):
        self._browser_access.close()
        self.timer.stop();self.timeout.stop();self.pending.clear();self.active_pages.clear()
        if self.process.state()!=QProcess.NotRunning:
            self.process.kill();self.process.waitForFinished(500)
