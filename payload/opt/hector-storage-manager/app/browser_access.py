"""Local browser credential storage and control for Hector's touch panel."""
import json
import logging
import os
from pathlib import Path
import secrets
import socket
from socketserver import StreamRequestHandler, UnixStreamServer
import stat
import struct
import threading
from core import Problem

MAX_PASSWORD_LENGTH = 24


def read_password(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                info.st_mode & 0o077 or info.st_nlink != 1 or info.st_size > 1024):
            raise ValueError('Unsafe browser password file')
        value = json.load(handle)
    if (not isinstance(value, dict) or set(value) != {'salt', 'digest'} or
            not isinstance(value['salt'], str) or not isinstance(value['digest'], str) or
            len(value['salt']) != 32 or len(value['digest']) != 128):
        raise ValueError('Invalid browser password record')
    if len(bytes.fromhex(value['salt'])) != 16 or len(bytes.fromhex(value['digest'])) != 64:
        raise ValueError('Invalid browser password record')
    return value


def store_password(path, record):
    """Publish a private record atomically. Report whether the directory was synced."""
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temp = '.password-' + secrets.token_hex(12)
    created = False
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('Unsafe browser password directory')
        out = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        created = True
        with os.fdopen(out, 'w') as handle:
            json.dump(record, handle)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path.name, src_dir_fd=fd, dst_dir_fd=fd)
        created = False
        try:
            os.fsync(fd)
        except OSError:
            logging.warning('Browser password directory sync could not be confirmed')
            return False
        return True
    finally:
        try:
            if created:
                os.unlink(temp, dir_fd=fd)
        finally:
            os.close(fd)


class BrowserRequest(StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(2)
        try:
            _, uid, _ = struct.unpack('3i', self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid != os.geteuid():
                return
            line = self.rfile.readline(1025)
            if not line.endswith(b'\n') or len(line) > 1024:
                return
            message = json.loads(line)
            reply = self.server.dispatch(message)
            self.wfile.write(json.dumps(reply).encode() + b'\n')
        except (OSError, ValueError):
            return


class BrowserControl(UnixStreamServer):
    def __init__(self, endpoint, port, change_password, desktop=None):
        self.endpoint = Path(endpoint)
        self.port = port
        self.change_password = change_password
        self.desktop = desktop
        super().__init__(str(self.endpoint), BrowserRequest)
        try:
            self.endpoint.chmod(0o600)
        except OSError:
            self.server_close()
            self.endpoint.unlink()
            raise

    def dispatch(self, request):
        if request == {'action': 'status'}:
            result = {'ok': True, 'port': self.port}
            if self.desktop:
                result['remote_desktop'] = self.desktop.local()
            return result
        if self.desktop and isinstance(request, dict):
            if request == {'action': 'prepare-password'}:
                return {'ok': True, 'remote_desktop': self.desktop.local(False)}
            if (set(request) == {'action', 'enabled'} and request['action'] == 'desktop'
                    and type(request['enabled']) is bool):
                try:
                    return {'ok': True, 'remote_desktop': self.desktop.local(request['enabled'])}
                except Problem as exc:
                    return {'ok': False, 'error': str(exc)}
        if not isinstance(request, dict) or set(request) != {'action', 'password'} or request['action'] != 'password':
            return {'ok': False, 'error': 'Unsupported browser access request.'}
        password = request['password']
        if not isinstance(password, str) or not 1 <= len(password) <= MAX_PASSWORD_LENGTH or not password.isprintable():
            return {'ok': False, 'error': f'Enter 1–{MAX_PASSWORD_LENGTH} printable characters.'}
        try:
            durable = self.change_password(password)
        except (OSError, ValueError):
            return {'ok': False, 'error': 'Could not save the browser password.'}
        return {'ok': True, 'durable': durable}

    def __enter__(self):
        threading.Thread(target=self.serve_forever, name='browser-access', daemon=True).start()
        return self

    def __exit__(self, *_):
        self.shutdown()
        self.server_close()
        self.endpoint.unlink(missing_ok=True)
