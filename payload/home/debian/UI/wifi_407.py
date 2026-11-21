"""GPL-2.0-or-later. Qt5 Wi-Fi page bridge; no network or password in presets."""
import json
from PySide2.QtCore import QObject, Property, Signal, Slot, QTimer, QProcess

MESSAGES = {
    'authentication-failed': 'The new network’s security check did not complete. The previous network was restored.',
    'join-failed': 'Could not join. Check the password, security choice and signal. The previous network was restored.',
    'password-length': 'WPA2 needs 8–63 password bytes. WPA3 needs 1–63.',
    'invalid-password': 'The password contains an unsupported control character.',
    'service-unavailable': 'Wi-Fi Setup is unavailable. Your audio patch can keep running.',
    'unsupported-adapter': 'This adapter needs a supported Hector driver before it can be selected.',
    'unsupported-security': 'This network security is not supported by this setup page.',
    'select-adapter-first': 'Use the selected adapter, then scan its networks.',
    'scan-again': 'Scan again before selecting this network.',
    'busy': 'A connection change is already in progress.',
    'cancelled': 'Cancelled. The previous network was restored.',
    'recovery-needed': 'Automatic recovery could not confirm a connection. Check the adapter and access point.',
    'interrupted': 'The previous setup attempt was interrupted.',
}


class WifiHub(QObject):
    changed = Signal()
    def __init__(self, client='/usr/local/lib/hector-wifi-manager/client.py'):
        super().__init__()
        self.client, self.data, self.problem = client, {}, ''
        self.request, self.pending, self.visible = None, None, False
        self.process = QProcess(self)
        self.process.started.connect(self.send)
        self.process.finished.connect(self.complete)
        self.process.errorOccurred.connect(self.failed)
        self.timeout = QTimer(self); self.timeout.setSingleShot(True)
        self.timeout.timeout.connect(self.expire)
        self.timer = QTimer(self); self.timer.setInterval(1500); self.timer.timeout.connect(self.poll)
    status = Property('QVariantMap', lambda s: s.data, notify=changed)
    error = Property(str, lambda s: s.problem, notify=changed)
    @Slot(bool)
    def activate(self, visible):
        self.visible = visible
        if visible:
            self.timer.start(); self.poll()
        else:
            self.timer.stop()
    def call(self, request):
        if self.process.state() != QProcess.NotRunning:
            if request['action'] != 'status' and self.pending is None:
                self.pending = request
            return
        self.request = request
        self.process.start('/usr/bin/python3', ['-I', '-B', self.client])
        self.timeout.start(15000)
    def send(self):
        raw = json.dumps(self.request, ensure_ascii=True).encode() + b'\n'
        self.request.pop('password', None)
        self.process.write(raw); self.process.closeWriteChannel()
    @Slot()
    def poll(self):
        if self.visible:
            self.call(dict(action='status'))
    @Slot()
    def scan(self):
        self.problem = ''; self.call(dict(action='scan'))
    @Slot(str)
    def useAdapter(self, address):
        self.problem = ''; self.call(dict(action='adapter', adapter=address))
    @Slot(str, str, str, str, bool)
    def joinNetwork(self, adapter, network, password, mode, remember):
        self.problem = ''
        self.call(dict(action='connect', adapter=adapter, network=network,
                       password=password, mode=mode, remember=remember))
    @Slot()
    def cancel(self):
        self.call(dict(action='cancel'))
    def failed(self, error):
        if error == QProcess.FailedToStart:
            self.complete(-1, QProcess.NormalExit)
    def expire(self):
        self.process.kill()
    def complete(self, code, _):
        self.timeout.stop()
        action = (self.request or {}).get('action'); self.request = None
        raw = bytes(self.process.readAllStandardOutput())
        self.process.readAllStandardError()  # Never display subprocess diagnostic text.
        try:
            result = json.loads(raw) if not code and len(raw) <= 131072 else {}
            if result.get('ok') is not True:
                error = result.get('error', 'service-unavailable')
                self.problem = MESSAGES.get(error, 'Wi-Fi Setup could not complete this operation.')
                if action == 'status':
                    self.data = dict(self.data, online=False)
            elif 'accepted' in result['data']:
                self.data = dict(self.data, busy=True, phase='connecting')
            else:
                self.data = result['data']
                self.problem = MESSAGES.get(self.data.get('error'), '')
        except (ValueError, KeyError, TypeError):
            self.problem = MESSAGES['service-unavailable']
        self.changed.emit()
        if self.pending:
            request, self.pending = self.pending, None
            self.call(request)
    def close(self):
        self.timer.stop(); self.timeout.stop()
        self.pending = self.request = None
        if self.process.state() != QProcess.NotRunning:
            self.process.kill(); self.process.waitForFinished(1000)
