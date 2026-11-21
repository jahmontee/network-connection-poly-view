"""Independent PySide2 touchscreen panel; never imports the firmware UI."""
import json
from local_control_407 import control_process
from pathlib import Path
import sys
from PySide2.QtCore import QObject, Property, QProcess, QTimer, QUrl, Signal, Slot
from PySide2.QtGui import QGuiApplication
from PySide2.QtQml import QQmlApplicationEngine


class EthernetHub(QObject):
    changed = Signal()
    def __init__(self, client=None):
        super().__init__()
        self.client = str(client or '/usr/local/lib/hector-ethernet-panel/client.py')
        self.data = dict(online=False, enabled=True, busy=False, adapters=[], error='')
        self.request = None
        self.pending = None
        self.process = control_process(self, self.client)
        self.process.started.connect(self.send)
        self.process.finished.connect(self.complete)
        self.process.errorOccurred.connect(self.failed)
        self.timeout = QTimer(self)
        self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self.process.kill)
        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.poll)
        self.visible = False

    status = Property('QVariantMap', lambda self: self.data, notify=changed)

    def call(self, request):
        if self.process.state() != QProcess.NotRunning:
            if request['action'] == 'set':
                self.pending = request
            return
        self.request = request
        self.process.start('/usr/bin/python3', ['-I', '-B', self.client])
        self.timeout.start(3500)

    def send(self):
        self.process.write(json.dumps(self.request).encode() + b'\n')
        self.process.closeWriteChannel()

    @Slot(bool)
    def activate(self, visible):
        self.visible = visible
        if visible:
            self.timer.start()
            self.poll()
        else:
            self.timer.stop()

    @Slot()
    def poll(self):
        if not self.visible:
            return
        self.call({'action': 'status'})

    @Slot(bool)
    def setEnabled(self, enabled):
        if self.data.get('online') and not self.data.get('busy'):
            self.call({'action': 'set', 'enabled': enabled})

    def failed(self, error):
        if error == QProcess.FailedToStart:
            self.complete(-1, QProcess.NormalExit)

    def complete(self, code, _):
        if self.request is None:
            return
        self.timeout.stop()
        self.request = None
        raw = bytes(self.process.readAllStandardOutput())
        self.process.readAllStandardError()
        try:
            reply = json.loads(raw) if code == 0 and len(raw) < 32768 else {}
            if reply.get('error') == 'busy':
                self.data = dict(self.data, busy=True, error='')
            elif not reply.get('ok'):
                self.data = dict(self.data, online=False, busy=False,
                                 error='Ethernet controls are unavailable. The patch can keep running.')
            elif reply['data'].get('accepted'):
                self.data = dict(self.data, busy=True, error='')
            else:
                self.data = reply['data']
        except (ValueError, KeyError, TypeError):
            self.data = dict(self.data, online=False, busy=False, error='Ethernet status is unavailable.')
        self.changed.emit()
        if self.pending:
            pending, self.pending = self.pending, None
            self.call(pending)

    def close(self):
        self.timer.stop()
        self.timeout.stop()
        if self.process.state() != QProcess.NotRunning:
            self.process.kill()
            self.process.waitForFinished(500)

