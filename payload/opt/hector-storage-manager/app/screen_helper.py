"""Private X11 screen capture and pointer bridge. Runs as Hector's UI user."""
import argparse
import ctypes as c
import json
import os
from pathlib import Path
import signal
import socket
from socketserver import StreamRequestHandler, UnixStreamServer
import struct
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from PySide2.QtCore import QBuffer, QByteArray, QIODevice
from PySide2.QtGui import QGuiApplication
from desktop_keys import checked_key

WIDTH, HEIGHT = 1280, 720
SOCKET = Path('/run/hector-remote-screen/screen.sock')
INPUT_SOCKET = Path('/run/hector-remote-input/input.sock')
HOLD_ENDPOINT = '\0hector-poly-view-hold-' + str(os.geteuid())
CREDENTIALS = struct.Struct('3i')


def patch_hold(message):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as peer:
            peer.settimeout(1.0)
            peer.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            peer.bind('')
            peer.connect(HOLD_ENDPOINT)
            peer.send(json.dumps(message).encode())
            reply, credentials, flags, _ = peer.recvmsg(4, socket.CMSG_SPACE(CREDENTIALS.size))
            if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
                raise ValueError('Incomplete native hold reply')
            for level, kind, data in credentials:
                if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS:
                    if CREDENTIALS.unpack(data)[1] == os.geteuid() and reply in (b'ok', b'held'):
                        return reply
    except OSError as error:
        raise ValueError('Native patch hold is unavailable') from error
    raise ValueError('Invalid native hold reply')


class Screen:
    def __init__(self, capture=True):
        self.app = QGuiApplication([]) if capture else None
        self.image_data = QByteArray() if capture else None
        self.image_buffer = QBuffer(self.image_data) if capture else None
        self.x11 = c.CDLL('libX11.so.6')
        self.xtest = c.CDLL('libXtst.so.6')
        self.x11.XOpenDisplay.argtypes = [c.c_char_p]
        self.x11.XOpenDisplay.restype = c.c_void_p
        self.x11.XFlush.argtypes = [c.c_void_p]
        self.xtest.XTestFakeMotionEvent.argtypes = [c.c_void_p, c.c_int, c.c_int, c.c_int, c.c_ulong]
        self.xtest.XTestFakeButtonEvent.argtypes = [c.c_void_p, c.c_uint, c.c_int, c.c_ulong]
        self.display = self.x11.XOpenDisplay(None)
        if not self.display:
            raise RuntimeError('Hector display is unavailable')
        self.pressed = False
        self.release_after = 0

    def frame(self):
        if self.app is None:
            raise ValueError('The input connection does not capture frames')
        self.app.processEvents()
        pixmap = self.app.primaryScreen().grabWindow(0)
        if pixmap.width() != WIDTH or pixmap.height() != HEIGHT:
            raise ValueError('Unexpected Hector screen size')
        self.image_data.clear()
        if not self.image_buffer.open(QIODevice.WriteOnly | QIODevice.Truncate):
            raise ValueError('Screen capture buffer is unavailable')
        try:
            # A modest quality increase reduces ringing around pixel lettering
            # while retaining the existing lightweight JPEG capture path.
            if not pixmap.save(self.image_buffer, 'JPEG', 85):
                raise ValueError('Screen capture failed')
            return self.image_data.data()
        finally:
            self.image_buffer.close()

    def pointer(self, x, y, down):
        if not self.xtest.XTestFakeMotionEvent(self.display, -1, x, y, 0):
            raise ValueError('Pointer movement failed')
        if down != self.pressed:
            if not self.xtest.XTestFakeButtonEvent(self.display, 1, int(down), 0):
                raise ValueError('Pointer button failed')
            self.pressed = down
        self.release_after = time.monotonic() + 1.5
        self.x11.XFlush(self.display)

    def release(self):
        self.xtest.XTestFakeButtonEvent(self.display, 1, 0, 0)
        self.x11.XFlush(self.display)
        self.pressed = False

    def dispatch(self, message):
        if not isinstance(message, dict):
            raise ValueError('Unsupported screen action')
        if message == {'action': 'status'}:
            return b'ok'
        if message == {'action': 'frame'}:
            return self.frame()
        if message == {'action': 'release'}:
            self.release()
            return b'ok'
        action = message.get('action')
        if action == 'key':
            checked_key(message)
            return patch_hold(message)
        if action in ('hold-renew', 'hold-cancel') and set(message) == {'action'}:
            return patch_hold(message)
        if action in ('hold-start', 'hold-apply') and set(message) == {'action', 'x', 'y'}:
            x, y = message['x'], message['y']
            if type(x) is int and type(y) is int and 0 <= x < WIDTH and 0 <= y < HEIGHT:
                return patch_hold(message)
        if set(message) == {'action', 'x', 'y', 'down'} and action == 'pointer':
            x, y, down = message['x'], message['y'], message['down']
            if type(x) is int and type(y) is int and type(down) is bool and 0 <= x < WIDTH and 0 <= y < HEIGHT:
                self.pointer(x, y, down)
                return b'ok'
        raise ValueError('Unsupported screen action')


class ScreenRequest(StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(0.75)
        try:
            _, uid, _ = struct.unpack('3i', self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.geteuid():
                return
            line = self.rfile.readline(1025)
            if not line.endswith(b'\n') or len(line) > 1024:
                return
            body = self.server.screen.dispatch(json.loads(line))
            self.wfile.write(struct.pack('!I', len(body)) + body)
        except (ValueError, OSError):
            return


class ScreenServer(UnixStreamServer):
    def service_actions(self):
        if self.screen.pressed and time.monotonic() >= self.screen.release_after:
            self.screen.release()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-only', action='store_true')
    args = parser.parse_args()
    endpoint = INPUT_SOCKET if args.input_only else SOCKET
    os.umask(0o077)
    def stop(_signum, _frame):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    screen = Screen(capture=not args.input_only)
    with ScreenServer(str(endpoint), ScreenRequest) as server:
        server.screen = screen
        endpoint.chmod(0o600)
        try:
            server.serve_forever(poll_interval=0.15)
        finally:
            screen.release()
            endpoint.unlink(missing_ok=True)
