"""Touch panel bridge for the storage manager's private Unix socket."""
import json
import os
import time

from PySide2.QtCore import QObject, Property, Signal, Slot
from local_control_407 import LocalControlProcess


class BrowserAccess(QObject):
    changed = Signal()
    passwordSaved = Signal(str)
    passwordPanelReady = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self._available = False
        self._port = 8787
        self._error = ''
        self._desktop_enabled = False
        self._desktop_error = ''
        self._action = None
        self._request = None
        self._pending = None
        self._next_check = 0
        self.process = LocalControlProcess('/run/hector-storage-manager/browser-access.sock', self, os.getuid())
        self.process.started.connect(self.send)
        self.process.finished.connect(self.complete)

    available = Property(bool, lambda self: self._available, notify=changed)
    port = Property(int, lambda self: self._port, notify=changed)
    error = Property(str, lambda self: self._error, notify=changed)
    busy = Property(bool, lambda self: self._action not in (None, 'status') or self._pending is not None, notify=changed)

    remoteDesktopEnabled = Property(bool, lambda self: self._desktop_enabled, notify=changed)
    remoteDesktopError = Property(str, lambda self: self._desktop_error, notify=changed)

    @Slot(bool)
    def setRemoteDesktopEnabled(self, enabled):
        if self._available and not self.busy:
            self._desktop_error = ''
            self.request('desktop', enabled=enabled)

    @Slot()
    def preparePassword(self):
        if self._available and not self.busy:
            self._desktop_error = ''
            self.request('prepare-password')

    def refresh(self):
        if self._action is None and time.monotonic() >= self._next_check:
            self.request('status')

    def request(self, action, **fields):
        if self._action is not None:
            # Keep routine status polling invisible to the controls, while
            # retaining one user action that arrives during the read.
            if self._action == 'status' and action != 'status' and self._pending is None:
                self._pending = (action, fields)
                self.changed.emit()
            return
        self._action = action
        self._request = json.dumps(dict(action=action, **fields)).encode() + b'\n'
        self.changed.emit()
        self.process.start('', [])

    def send(self):
        raw, self._request = self._request, None
        self.process.write(raw)
        self.process.closeWriteChannel()

    @Slot(str)
    def submitPassword(self, password):
        if self._available and not self.busy:
            self._error = ''
            self.request('password', password=password)

    def complete(self, code, _status):
        action, self._action = self._action, None
        self._request = None
        if action is None:
            return
        self._next_check = time.monotonic() + 5
        try:
            reply = json.loads(bytes(self.process.readAllStandardOutput())) if code == 0 else {}
        except (ValueError, UnicodeError):
            reply = {}
        if reply.get('ok') is True and type(reply.get('remote_desktop')) is bool:
            self._desktop_enabled = reply['remote_desktop']
        if action == 'status':
            port = reply.get('port')
            self._available = reply.get('ok') is True and type(port) is int and 0 < port < 65536
            if self._available:
                self._port = port
        elif action in ('desktop', 'prepare-password'):
            if reply.get('ok') is not True:
                self._desktop_error = reply.get('error') or 'Could not reach the dashboard. Try again.'
                if self._desktop_error == 'service-unavailable':
                    self._desktop_error = 'Could not reach the dashboard. Try again.'
            elif action == 'prepare-password':
                self.passwordPanelReady.emit()
        elif action == 'password':
            if reply.get('ok') is True:
                message = 'Browser password saved.' if reply.get('durable') else 'Password applied. Disk sync could not be confirmed.'
                self.passwordSaved.emit(message)
            else:
                self._error = reply.get('error') or 'Could not reach the dashboard. Try again.'
                if self._error == 'service-unavailable':
                    self._error = 'Could not reach the dashboard. Try again.'
        self.changed.emit()
        if self._pending is not None:
            action, fields = self._pending
            self._pending = None
            self.request(action, **fields)

    def close(self):
        self._action = None
        self._request = None
        self._pending = None
        self.process.kill()
