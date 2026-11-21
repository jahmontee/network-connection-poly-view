"""Gate Hector screen frames and pointer input behind local opt-in and browser login."""
import json
import math
import os
from pathlib import Path
import secrets
import socket
import struct
import threading
import time

from core import Problem
from desktop_keys import checked_key

CAPTURE_INTERVAL = 0.28
HOLD_ACTIONS = ('shift-click', 'hold-start', 'hold-apply', 'hold-renew', 'hold-cancel')
KEY_ACTIONS = ('key',)


class ScreenConnection:
    def __init__(self, endpoint, input_endpoint=None):
        self.endpoint = Path(endpoint)
        self.input_endpoint = Path(input_endpoint) if input_endpoint else self.endpoint

    def __call__(self, action, **fields):
        try:
            with socket.socket(socket.AF_UNIX) as peer:
                peer.settimeout(3)
                endpoint = self.input_endpoint if action not in ('frame', 'status') and self.input_endpoint.exists() else self.endpoint
                peer.connect(str(endpoint))
                _, uid, _ = struct.unpack('3i', peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != os.geteuid():
                    raise OSError('Unexpected screen service user')
                peer.sendall(json.dumps(dict(action=action, **fields)).encode() + b'\n')
                with peer.makefile('rb') as response:
                    size, = struct.unpack('!I', response.read(4))
                    if not 0 < size <= 512 * 1024:
                        raise OSError('Invalid screen response size')
                    body = response.read(size)
                    if len(body) != size:
                        raise OSError('Incomplete screen response')
                    if action == 'frame' and body.startswith(b'\xff\xd8') and body.endswith(b'\xff\xd9'):
                        return body
                    if action in ('hold-start', 'hold-renew') and body == b'held':
                        return body
                    if action != 'frame' and body == b'ok':
                        return body
        except (OSError, struct.error):
            pass
        raise Problem('Hector’s screen is temporarily unavailable. Try again shortly.', 503)


class RemoteDesktop:
    def __init__(self, screen, authorized):
        self.screen = screen
        self.authorized = authorized
        self.lock = threading.Lock()
        self.enabled = False
        self.viewer = None
        self.capture_after = 0
        self.capture_pending = False
        self.pointer_after = 0
        self.pressed = False
        self.held = False

    def cancel_hold(self):
        if self.held:
            self.screen('hold-cancel')
            self.held = False

    def release(self):
        try:
            self.cancel_hold()
        except Problem:
            self.held = False  # The native hold also expires independently.
        if self.pressed:
            try:
                self.screen('release')
            except Problem:
                pass  # The screen helper also releases an idle button independently.
            self.pressed = False

    def close(self):
        self.release()
        self.viewer = None

    def revoke(self, session=None):
        with self.lock:
            if self.viewer and (session is None or self.viewer['session'] == session):
                self.close()

    def local(self, enabled=None):
        with self.lock:
            if enabled is True and not self.enabled:
                self.screen('status')
            if enabled is False:
                self.close()
            if enabled is not None:
                self.enabled = enabled
            return self.enabled

    def active_viewer(self, session, viewer_id, now):
        if not self.authorized(session):
            raise Problem('Sign in to view Hector’s screen.', 401)
        if not self.enabled:
            raise Problem('Enable screen sharing in Network Connection on Hector.', 409)
        if self.viewer and self.viewer['expires'] <= now:
            self.close()
        if not self.viewer or self.viewer['session'] != session or self.viewer['id'] != viewer_id:
            raise Problem('The remote desktop session ended. Open the viewer again.', 409)
        self.viewer['expires'] = now + 8

    def frame(self, session, viewer_id):
        with self.lock:
            now = time.monotonic()
            self.active_viewer(session, viewer_id, now)
            if self.capture_pending:
                return b'', math.ceil(CAPTURE_INTERVAL * 1000)
            if now < self.capture_after:
                return b'', math.ceil((self.capture_after - now) * 1000)
            self.capture_after = now + CAPTURE_INTERVAL
            self.capture_pending = True
        try:
            frame = self.screen('frame')
        finally:
            with self.lock:
                self.capture_pending = False
        with self.lock:
            self.active_viewer(session, viewer_id, time.monotonic())
            wait_ms = max(0, math.ceil((self.capture_after - time.monotonic()) * 1000))
            return frame, wait_ms

    def request(self, session, viewer_id, action, message):
        if action == 'frame':
            return self.frame(session, viewer_id)
        with self.lock:
            if not self.authorized(session):
                raise Problem('Sign in to view Hector’s screen.', 401)
            if action == 'status':
                return {'enabled': self.enabled}
            if not self.enabled:
                raise Problem('Enable screen sharing in Network Connection on Hector.', 409)
            now = time.monotonic()
            if self.viewer and self.viewer['expires'] <= now:
                self.close()
            if action == 'open':
                if self.viewer:
                    raise Problem('Hector is already sharing its screen with a browser.', 409)
                viewer = secrets.token_urlsafe(24)
                self.viewer = dict(id=viewer, session=session, expires=now + 8, control=False)
                return {'viewer': viewer}
            self.active_viewer(session, viewer_id, now)
            if action == 'close':
                self.close()
            elif action == 'take':
                self.viewer['control'] = True
            elif action == 'view':
                self.release()
                self.viewer['control'] = False
            elif action == 'release-input':
                self.release()
            elif action == 'pointer' or action in HOLD_ACTIONS + KEY_ACTIONS:
                if not self.viewer['control']:
                    raise Problem('Select Take Control before editing the patch.', 403)
                if action == 'key':
                    try:
                        fields = checked_key(message)
                    except ValueError as error:
                        raise Problem(str(error)) from error
                    self.screen('key', **fields)
                    return {'ok': True}
                if action == 'hold-cancel':
                    self.cancel_hold()
                    return {'held': False}
                if action == 'hold-renew':
                    if self.held:
                        self.held = self.screen('hold-renew') == b'held'
                    return {'held': self.held}
                x, y = message.get('x'), message.get('y')
                if type(x) is not int or type(y) is not int or not (0 <= x < 1280 and 0 <= y < 720):
                    raise Problem('Invalid screen position.')
                if action in ('shift-click', 'hold-start', 'hold-apply'):
                    if self.pressed:
                        raise Problem('Release the pointer before holding a module.', 409)
                    if action == 'hold-apply' and not self.held:
                        return {'held': False}
                    if action == 'hold-start':
                        self.cancel_hold()
                    native_action = ('hold-apply' if self.held else 'hold-start') if action == 'shift-click' else action
                    self.held = self.screen(native_action, x=x, y=y) == b'held'
                    return {'held': self.held}
                down = message.get('down')
                if type(down) is not bool:
                    raise Problem('Invalid pointer button state.')
                self.cancel_hold()
                if now >= self.pointer_after or down != self.pressed:
                    self.pressed = down
                    self.screen('pointer', x=x, y=y, down=down)
                    self.pointer_after = now + 0.04
            else:
                raise Problem('Unsupported Remote Desktop action.')
            return {'ok': True}
