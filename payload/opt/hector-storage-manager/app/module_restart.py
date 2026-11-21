"""The web process can ask the local restart service for one normal reboot."""
import json
import socket
import stat
import struct
from pathlib import Path

from core import Problem

SOCKET = Path('/run/hector-module-restart.sock')
MARKER = Path('/run/hector-module-restarting')


class ModuleRestart:
    def require_ready(self):
        reply=self.exchange(b'readiness',512)
        if reply==b'ready\n':return
        try:
            value=json.loads(reply)
            if (value.get('status')=='not-ready' and value.get('phase') in ('waiting','deadline','support-version','upgrade-recovery','recovery')
                    and isinstance(value.get('message'),str) and len(value['message'])<=256):
                raise Problem(value['message'],409)
        except (ValueError,TypeError,AttributeError):pass
        raise Problem('Flash Repair is not ready. Check installation or recovery status before changing modules.',409)

    def available(self):
        try:
            info = SOCKET.lstat()
            return info.st_uid == 0 and stat.S_ISSOCK(info.st_mode)
        except OSError:
            return False

    def pending(self):
        return MARKER.exists()

    def exchange(self, message, limit=32):
        request_sent = False
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(15)
                connection.connect(str(SOCKET))
                _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                if uid != 0:
                    raise OSError('Unexpected restart service identity')
                request_sent = True
                connection.sendall(message + b'\n')
                with connection.makefile('rb') as reply:
                    return reply.readline(limit)
        except OSError as exc:
            failure = Problem('Hector’s restart service is unavailable.', 503)
            failure.request_sent = request_sent
            raise failure from exc

    def request(self):
        result = self.exchange(b'restart')
        if result == b'not-ready\n':
            raise Problem('Flash Repair is not ready. Check installation or recovery status before changing modules.',409)
        if result == b'busy\n':
            raise Problem('Hector is updating or already restarting.', 409)
        if result != b'accepted\n':
            raise Problem('Hector could not schedule a restart.', 503)

    def native_status(self):
        try:
            status = json.loads(self.exchange(b'native-status', 2048))
        except (Problem, ValueError):
            return {'state': 'unavailable'}
        return status

    def native_request(self, action):
        result = self.exchange({'install': b'native-install', 'rollback': b'native-rollback'}[action])
        if result == b'not-ready\n':
            raise Problem('Flash Repair is not ready. Check installation or recovery status before changing modules.',409)
        if result == b'busy\n':
            raise Problem('Hector is updating or already restarting.', 409)
        if result != b'accepted\n':
            raise Problem('Hector could not start the native release installer.', 503)

    def batch_request(self, native):
        if native:
            self.native_request('install')
            return
        result = self.exchange(b'batch-install')
        if result == b'not-ready\n':
            raise Problem('Flash Repair is not ready. Check installation or recovery status before changing modules.',409)
        if result == b'busy\n':
            raise Problem('Hector is updating or already restarting.', 409)
        if result != b'accepted\n':
            raise Problem('Hector could not start the module install batch.', 503)

    def platform_request(self):
        result=self.exchange(b'platform-install')
        if result==b'not-ready\n':raise Problem('Flash Repair is not ready. Check installation or recovery status before changing modules.',409)
        if result==b'busy\n':raise Problem('Hector is updating or already restarting.',409)
        if result!=b'accepted\n':raise Problem('Hector could not start the app update.',503)
