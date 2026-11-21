"""Bounded asynchronous requests to the existing local control services.

The small process-compatible interface preserves the bridges' completion,
pending-action and patch-load cancellation paths without launching Python.
Custom client paths retain QProcess for existing diagnostic fixtures.
"""
import json
import os
import socket
import struct
from PySide2.QtCore import QObject, QProcess, QTimer, Signal, QByteArray
try:
    from PySide2.QtNetwork import QLocalSocket
except ImportError:
    QLocalSocket = None

ENDPOINTS = {
    '/usr/local/lib/hector-link-sync/client.py': '/run/hector-link-sync-control/control.sock',
    '/usr/local/lib/hector-network-panel/client.py': '/run/hector-network-panel/control.sock',
    '/usr/local/lib/hector-ethernet-panel/client.py': '/run/hector-ethernet-panel/control.sock',
}


def control_process(parent, client):
    endpoint = ENDPOINTS.get(client)
    return LocalControlProcess(endpoint, parent) if endpoint and QLocalSocket is not None else QProcess(parent)


class LocalControlProcess(QObject):
    started = Signal()
    finished = Signal(int, int)
    errorOccurred = Signal(int)
    MAX_REPLY = 32768
    IDLE_TIMEOUT_MS = 2000

    def __init__(self, endpoint, parent=None, expected_uid=0):
        super().__init__(parent)
        self.endpoint, self.expected_uid = endpoint, expected_uid
        self.peer = None
        self._state = QProcess.NotRunning
        self._output = b''
        self._request = bytearray()
        self._reply = bytearray()
        self._sent = False
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(self.unavailable)
        self.total_deadline = QTimer(self)
        self.total_deadline.setSingleShot(True)
        self.total_deadline.timeout.connect(self.unavailable)

    def state(self):
        return self._state

    def start(self, _program, _arguments):
        if self.peer is not None:
            return
        self._state = QProcess.Starting
        self._output = b''
        self._request.clear()
        self._reply.clear()
        self._sent = False
        peer = QLocalSocket(self)
        self.peer = peer
        peer.setReadBufferSize(self.MAX_REPLY + 1)
        peer.connected.connect(lambda: self.connected(peer))
        peer.readyRead.connect(lambda: self.receive(peer))
        peer.disconnected.connect(lambda: self.disconnected(peer))
        peer.error.connect(lambda error: self.failed(peer, error))
        self.deadline.start(self.IDLE_TIMEOUT_MS)
        self.total_deadline.start(6000)
        # Defer connection failures until the calling bridge has armed its timer.
        QTimer.singleShot(0, lambda: self.connect_peer(peer))

    def connect_peer(self, peer):
        if peer is self.peer:
            peer.connectToServer(self.endpoint)

    def connected(self, peer):
        if peer is not self.peer:
            return
        try:
            with socket.socket(fileno=os.dup(int(peer.socketDescriptor()))) as raw:
                uid = struct.unpack('3i', raw.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]
            if uid != self.expected_uid:
                raise ValueError('Unexpected control service')
        except (OSError, ValueError, TypeError):
            self.unavailable()
            return
        self._state = QProcess.Running
        self.started.emit()

    def write(self, data):
        if self.peer is None or self._sent:
            return -1
        self._request.extend(bytes(data))
        if len(self._request) > 1024:
            self.unavailable()
            return -1
        return len(data)

    def closeWriteChannel(self):
        if self.peer is None or self._sent:
            return
        try:
            json.loads(bytes(self._request))
        except (ValueError, UnicodeError):
            self.unavailable()
            return
        self._sent = True
        self.peer.write(bytes(self._request).rstrip(b'\n') + b'\n')
        self.deadline.start(self.IDLE_TIMEOUT_MS)

    def receive(self, peer, eof=False):
        if peer is not self.peer:
            return
        remaining = self.MAX_REPLY + 1 - len(self._reply)
        part = bytes(peer.read(remaining))
        self._reply.extend(part)
        if part:
            # Match the old client's per-receive timeout, bounded by the
            # bridges' existing six-second operation limit.
            self.deadline.start(self.IDLE_TIMEOUT_MS)
        if len(self._reply) > self.MAX_REPLY:
            self.unavailable()
            return
        if b'\n' not in self._reply and not eof:
            return
        try:
            value = json.loads(bytes(self._reply))
            if not isinstance(value, dict):
                raise ValueError('Invalid reply')
        except (ValueError, UnicodeError):
            self.unavailable()
            return
        self.finish(value)

    def disconnected(self, peer):
        if peer is self.peer:
            self.receive(peer, eof=True)

    def failed(self, peer, error):
        if peer is self.peer:
            # PeerClosedError may arrive before the final readyRead/disconnected.
            if error == QLocalSocket.PeerClosedError:
                self.receive(peer, eof=True)
            else:
                self.unavailable()

    def unavailable(self):
        self.finish(dict(ok=False, error='service-unavailable'))

    def finish(self, value, code=0, status=QProcess.NormalExit):
        peer, self.peer = self.peer, None
        if peer is None:
            return
        self.deadline.stop()
        self.total_deadline.stop()
        self._state = QProcess.NotRunning
        self._output = json.dumps(value).encode() if value is not None else b''
        peer.abort()
        peer.deleteLater()
        # State and resources are settled before a bridge starts a queued action.
        self.finished.emit(code, status)

    def kill(self):
        self.finish(None, -1, QProcess.CrashExit)

    def waitForFinished(self, _milliseconds):
        # Bridges only call this immediately after kill() during close.
        return self._state == QProcess.NotRunning

    def readAllStandardOutput(self):
        raw, self._output = self._output, b''
        return QByteArray(raw)

    def readAllStandardError(self):
        return QByteArray()
