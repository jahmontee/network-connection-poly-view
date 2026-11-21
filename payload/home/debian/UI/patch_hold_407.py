"""Receive authenticated local patch gestures on Hector's GUI thread."""
import json
import os
import socket
import struct

from PySide2.QtCore import QObject, QPoint, QSocketNotifier, QTimer
from poly_view_keyboard import send_key

ENDPOINT = '\0hector-poly-view-hold-' + str(os.geteuid())
CREDENTIALS = struct.Struct('3i')


class PatchHold(QObject):
    def __init__(self, window, endpoint=ENDPOINT):
        super().__init__(window)
        self.window = window
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_NONBLOCK)
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            self.socket.bind(endpoint)
        except OSError:
            self.socket.close()
            raise
        self.notifier = QSocketNotifier(self.socket.fileno(), QSocketNotifier.Read, self)
        self.notifier.activated.connect(self.receive)
        # Hector pumps Qt manually. Keep local requests moving even if the
        # platform dispatcher misses a socket-notifier wakeup after startup.
        self.poll = QTimer(self)
        self.poll.setInterval(20)
        self.poll.timeout.connect(self.receive)
        self.poll.start()

    def dispatch(self, message):
        if not isinstance(message, dict):
            return False
        action = message.get('action')
        if action == 'key':
            return send_key(self.window, message)
        if action in ('hold-renew', 'hold-cancel') and set(message) == {'action'}:
            return self.window.remotePatchGesture(action, 0, 0)
        if action in ('hold-start', 'hold-apply') and set(message) == {'action', 'x', 'y'}:
            x, y = message['x'], message['y']
            if type(x) is int and type(y) is int and 0 <= x < 1280 and 0 <= y < 720:
                point = self.window.mapFromGlobal(QPoint(x, y))
                return self.window.remotePatchGesture(action, point.x(), point.y())
        return False

    def receive(self, *_):
        for _ in range(4):
            try:
                raw, credentials, flags, address = self.socket.recvmsg(1024, socket.CMSG_SPACE(CREDENTIALS.size))
            except BlockingIOError:
                return
            if not address or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
                continue
            same_user = any(level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS
                            and CREDENTIALS.unpack(data)[1] == os.geteuid()
                            for level, kind, data in credentials)
            if not same_user:
                continue
            try:
                held = self.dispatch(json.loads(raw))
                self.socket.sendto(b'held' if held else b'ok', address)
            except (OSError, ValueError):
                continue
