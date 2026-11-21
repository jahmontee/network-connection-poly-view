#!/usr/bin/env python3
"""Hector's local-network storage manager. Vendored Waitress, no pip on device."""
import argparse
from contextlib import ExitStack
import hashlib
import hmac
from http import HTTPStatus
from http.cookies import SimpleCookie
import ipaddress
import io
import json
import logging
import mimetypes
import os
import re
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, quote

APP = Path(__file__).resolve().parent
sys.path.insert(0, str(APP.parent / 'vendor'))
sys.path.insert(0, str(APP))
from core import Library, MAX_BYTES, MAX_UPLOAD, MIB, Problem, storage, card_geometry, inspector_request
from browser_access import BrowserControl, read_password, store_password
from audio_library import AudioLibrary
from module_manager import ModuleManager, DATA, RUNTIME, LIMIT as MODULE_LIMIT
from remote_desktop import HOLD_ACTIONS, KEY_ACTIONS, RemoteDesktop, ScreenConnection
from module_restart import ModuleRestart
from platform_update import PlatformUpdate
import platform_release
from native_release import STAGED_ARCHIVE
from publisher_release import PublisherPolicy
from repository_manager import Repositories


def installed_version():
    try:
        with (APP.parent / 'platform-version.json').open('rb') as stream:
            record=json.loads(stream.read(513))
        value=record.get('version','')
        if record.get('id')==platform_release.ID and isinstance(value,str) and re.fullmatch(r'[0-9][A-Za-z0-9.+-]{0,63}',value):
            return value
    except (OSError,ValueError,AttributeError):pass
    return 'unavailable'


def password_record(password, salt=None):
    salt = salt or secrets.token_hex(16)
    return {'salt': salt, 'digest': hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()}


class Application:
    def __init__(self, config, password_file=None, screen=None, modules=None, restarter=None):
        self.config = dict(config)
        self.password_file = Path(password_file) if password_file else None
        if self.password_file:
            self.config['password'] = read_password(self.password_file)
        self.library = Library(config['preset_root'], config['scratch'], config.get('data_root', '/mnt'), config.get('reserve', 32 * MIB))
        self.audio = AudioLibrary(self.library)
        self.modules = modules
        self.repositories = Repositories(modules) if modules else None
        self.platform = PlatformUpdate(modules) if modules else None
        self.restarter = restarter
        self.sessions = {}
        self.failures = {}
        self.guard = threading.Lock()
        self.limit_login = threading.Lock()
        self.desktop = RemoteDesktop(screen, self.is_signed_in) if screen else None

    def is_signed_in(self, token):
        with self.guard:
            return self.sessions.get(token, {}).get('expires', 0) > time.monotonic()

    def change_local_password(self, password):
        record = password_record(password)
        with self.limit_login, self.guard:
            durable = store_password(self.password_file, record)
            self.config['password'] = record
            self.sessions.clear()
            self.failures.clear()
        if self.desktop:
            self.desktop.revoke()
        return durable

    def response(self, start, status, data, content_type='application/json', headers=()):
        body = json.dumps(data).encode() if content_type == 'application/json' else data
        start('{} {}'.format(status, HTTPStatus(status).phrase), self.headers(content_type) + [('Content-Length', str(len(body)))] + list(headers))
        return [body]

    def headers(self, content_type):
        return [('Content-Type', content_type), ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'), ('X-Frame-Options', 'DENY'), ('Referrer-Policy', 'no-referrer'), ('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")]

    def body(self, env, limit):
        try:
            length = int(env.get('CONTENT_LENGTH') or 0)
        except ValueError:
            raise Problem('Invalid request length.')
        if length <= 0 or length > limit:
            raise Problem('The request is empty or exceeds the size limit.', 413)
        return length

    def deletion(self, env):
        message = json.loads(env['wsgi.input'].read(self.body(env, 8192)))
        if not isinstance(message,dict) or message.get('confirmed') is not True:
            raise Problem('Confirm the deletion before continuing.')
        return message

    def module_view(self, state):
        if state.get('native_release') is not None:
            status = self.restarter.native_status() if self.restarter else {'state': 'unavailable'}
            state['native_release'].update(status)
        if self.platform:
            queued=self.platform.queued()
            if queued:state['queue'].append(queued)
            installed=self.platform.installed()
            if installed:state['installed'].append(installed)
            batch=self.platform.batch()
            if batch:state['install_batch']=batch
        return state

    def download_response(self, start, output, length, name, content_type, fallback):
        disposition = "attachment; filename=\"{}\"; filename*=UTF-8''{}".format(fallback,quote(name))
        start('200 OK', self.headers(content_type) + [('Content-Length', str(length)), ('Content-Disposition', disposition)])
        def chunks():
            try:
                while True:
                    chunk = output.read(65536)
                    if not chunk:
                        break
                    yield chunk
            finally:
                output.close()
        return chunks()

    def identity(self, env):
        host = env.get('HTTP_HOST', '')
        if not host or '/' in host or '@' in host:
            raise Problem('Invalid address.', 403)
        hostname = host.split(':')[0].lower()
        allowed = hostname in self.config.get('hosts', ['localhost', '127.0.0.1', 'pine64so.lan'])
        try:
            address = ipaddress.ip_address(hostname)
            allowed = address.is_loopback or any(address in ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
        except ValueError:
            pass
        if not allowed:
            raise Problem('Use Hector’s local address.', 403)
        try:
            remote = ipaddress.ip_address(env.get('REMOTE_ADDR', ''))
            if not remote.is_loopback and not any(remote in ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')):
                raise ValueError()
        except ValueError:
            raise Problem('This manager is available on the local network only.', 403)
        origin = env.get('HTTP_ORIGIN')
        expected = env.get('wsgi.url_scheme', 'http') + '://' + host
        if origin and origin != expected:
            raise Problem('Open this page directly on Hector before continuing.', 403)
        if env['REQUEST_METHOD'] == 'POST' and origin != expected:
            raise Problem('The request has no matching page origin.', 403)
        return remote.compressed

    def session(self, env):
        cookie = SimpleCookie()
        try:
            cookie.load(env.get('HTTP_COOKIE', ''))
            token = cookie['hector_session'].value
        except (KeyError, ValueError):
            raise Problem('Sign in to manage Hector.', 401)
        with self.guard:
            now = time.monotonic()
            self.sessions = {k: v for k, v in self.sessions.items() if v['expires'] > now}
            session = self.sessions.get(token)
            if session is None:
                raise Problem('Your session expired. Sign in again.', 401)
            session['expires'] = now + 7200
        if env['REQUEST_METHOD'] == 'POST' and not hmac.compare_digest(env.get('HTTP_X_HECTOR_CSRF', ''), session['csrf']):
            raise Problem('Refresh the page before trying again.', 403)
        return token, session

    def __call__(self, env, start):
        try:
            remote = self.identity(env)
            path = env.get('PATH_INFO', '/')
            method = env['REQUEST_METHOD']
            if method not in ('GET', 'POST'):
                raise Problem('Method not supported.', 405)
            static = {'/': 'index.html', '/app.js': 'app.js', '/reconnect.js': 'reconnect.js', '/style.css': 'style.css', '/c64.css':'c64.css', '/favicon.svg': 'favicon.svg',
                      '/fonts/PressStart2P-Regular.ttf':'fonts/PressStart2P-Regular.ttf',
                      '/fonts/Inter-700.ttf':'fonts/Inter-700.ttf','/fonts/Inter-800.ttf':'fonts/Inter-800.ttf'}
            if method == 'GET' and path in static:
                name = static[path]
                kind = {'html': 'text/html; charset=utf-8', 'js': 'text/javascript; charset=utf-8', 'css': 'text/css; charset=utf-8', 'svg': 'image/svg+xml','ttf':'font/ttf'}[name.rsplit('.', 1)[1]]
                return self.response(start, 200, (APP / 'static' / name).read_bytes(), kind)
            if path == '/api/info' and method == 'GET':
                return self.response(start, 200, {'name': 'Hector', 'version': installed_version(), 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(), 'preview': self.config.get('preview', False), 'preview_note': self.config.get('preview_note'), 'upload_limit': MAX_UPLOAD, 'modules': self.modules is not None,
                    'module_restart': bool(self.modules and self.restarter and self.restarter.available())})
            if path == '/api/login' and method == 'POST':
                if env.get('CONTENT_TYPE', '').split(';')[0] != 'application/json':
                    raise Problem('Use a JSON sign-in request.', 415)
                now = time.monotonic()
                with self.guard:
                    self.failures = {k: v for k, v in self.failures.items() if v['until'] > now}
                    if len(self.failures) >= 128 and remote not in self.failures or self.failures.get(remote, {}).get('count', 0) >= 5:
                        raise Problem('Too many sign-in attempts. Wait one minute.', 429)
                    self.failures.setdefault(remote, {'count': 0, 'until': now + 60})['count'] += 1
                if not self.limit_login.acquire(False):
                    raise Problem('A sign-in is already being checked. Try again.', 429)
                try:
                    raw = env['wsgi.input'].read(self.body(env, 2048))
                    data = json.loads(raw)
                    password = data.get('password', '')
                    if not isinstance(password, str) or len(password) > 512:
                        raise Problem('Incorrect access password.', 401)
                    expected = self.config['password']
                    actual = password_record(password, expected['salt'])['digest']
                    if not hmac.compare_digest(expected['digest'], actual):
                        raise Problem('Incorrect access password.', 401)
                    token = secrets.token_urlsafe(32)
                    session = {'csrf': secrets.token_urlsafe(32), 'expires': now + 7200}
                    with self.guard:
                        self.sessions[token] = session
                        if len(self.sessions) > 32:
                            self.sessions.pop(next(iter(self.sessions)))
                        self.failures.pop(remote, None)
                finally:
                    self.limit_login.release()
                return self.response(start, 200, {'csrf': session['csrf']}, headers=[('Set-Cookie', 'hector_session={}; Path=/; HttpOnly; SameSite=Strict; Max-Age=7200'.format(token))])
            token, session = self.session(env)
            if (method == 'POST' and self.restarter and self.restarter.pending()
                    and path in ('/api/upload', '/api/presets/delete', '/api/audio/upload', '/api/audio/delete')):
                raise Problem('Hector is already restarting.', 409)
            if path == '/api/session' and method == 'GET':
                return self.response(start, 200, {'csrf': session['csrf']})
            if path == '/api/logout' and method == 'POST':
                with self.guard:
                    self.sessions.pop(token, None)
                if self.desktop:
                    self.desktop.revoke(token)
                return self.response(start, 200, {'ok': True}, headers=[('Set-Cookie', 'hector_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0')])
            if self.desktop and path in ('/api/desktop', '/api/desktop/frame'):
                viewer = env.get('HTTP_X_HECTOR_DESKTOP', '')
                if method == 'GET':
                    action = 'frame' if path.endswith('/frame') else 'status'
                    result = self.desktop.request(token, viewer, action, {})
                    if action == 'frame':
                        frame, wait_ms = result
                        return self.response(start, 200 if frame else 204, frame, 'image/jpeg',
                                             [('Cross-Origin-Resource-Policy', 'same-origin'),
                                              ('X-Hector-Next-Frame-Ms', str(wait_ms))])
                elif path == '/api/desktop':
                    message = json.loads(env['wsgi.input'].read(self.body(env, 1024)))
                    action = message.get('action') if isinstance(message, dict) else None
                    if action not in ('open', 'close', 'take', 'view', 'pointer', 'release-input') + HOLD_ACTIONS + KEY_ACTIONS:
                        raise Problem('Unsupported Remote Desktop action.')
                    result = self.desktop.request(token, viewer, action, message)
                else:
                    raise Problem('Method not supported.', 405)
                return self.response(start, 200, result)
            if path == '/api/storage' and method == 'GET':
                rows = self.config.get('storage_preview') if self.config.get('preview') else storage()
                if self.config.get('inspector_socket'):
                    self.library.breakdown = inspector_request(self.config['inspector_socket'], 'status')
                return self.response(start, 200, {'mounts': rows, 'preset_root': str(self.library.root), 'reserve': self.library.reserve, 'breakdown': self.library.breakdown, 'updated': time.time(), 'geometry': self.config.get('geometry') if self.config.get('preview') else card_geometry()})
            if path == '/api/storage/scan' and method == 'POST':
                result = inspector_request(self.config['inspector_socket'], 'scan') if self.config.get('inspector_socket') else self.library.scan()
                return self.response(start, 202, result)
            if path == '/api/presets' and method == 'GET':
                return self.response(start, 200, {'presets': self.library.list()})
            if path == '/api/presets/delete' and method == 'POST':
                message = self.deletion(env)
                return self.response(start, 200, self.library.delete(message['bank'],message['name'],message['revision']))
            if self.modules:
                if path.startswith('/api/creators/') and method == 'POST':
                    if self.restarter and self.restarter.pending():raise Problem('Hector is updating or already restarting.',409)
                    message=json.loads(env['wsgi.input'].read(self.body(env,2048)))
                    if not isinstance(message,dict):raise Problem('Choose a creator action.')
                    if path == '/api/creators/add':
                        return self.response(start,201,self.repositories.add_creator(message.get('url')))
                    if path == '/api/creators/check':
                        return self.response(start,202,self.repositories.refresh(message.get('id'),creator=True))
                    if path == '/api/creators/remove':
                        return self.response(start,200,self.repositories.remove_creator(message.get('id')))
                    if path == '/api/creators/queue':
                        with self.library.operation():state=self.repositories.stage(message.get('id'),message.get('key'),creator=True)
                        return self.response(start,201,self.module_view(state))
                    raise Problem('Choose a creator action.',404)
                if path == '/api/repositories' and method == 'GET':
                    return self.response(start,200,self.repositories.view())
                if path.startswith('/api/repositories/') and method == 'POST':
                    message=json.loads(env['wsgi.input'].read(self.body(env,2048)))
                    if not isinstance(message,dict):raise Problem('Choose a repository action.')
                    if path == '/api/repositories/add':
                        return self.response(start,201,self.repositories.add(message.get('url')))
                    if path == '/api/repositories/check':
                        return self.response(start,202,self.repositories.refresh(message.get('id')))
                    if path == '/api/repositories/remove':
                        return self.response(start,200,self.repositories.remove(message.get('id')))
                    if path == '/api/repositories/queue':
                        if self.restarter and self.restarter.pending():raise Problem('Hector is already restarting.',409)
                        with self.library.operation():
                            state=self.repositories.stage(message.get('id'),message.get('key'))
                        return self.response(start,201,self.module_view(state))
                    raise Problem('Choose a repository action.',404)
                if path.startswith('/api/modules/') and method == 'POST' and self.restarter and self.restarter.pending():
                    raise Problem('Hector is already restarting.', 409)
                if path == '/api/modules' and method == 'GET':
                    return self.response(start, 200, self.module_view(self.modules.list()))
                if path == '/api/modules/batch' and method == 'GET':
                    return self.response(start, 200, {'batch': self.platform.batch() or self.modules.batch.view()})
                if path == '/api/modules/platform' and method == 'POST' and self.restarter:
                    message=json.loads(env['wsgi.input'].read(self.body(env,2048)))
                    if not isinstance(message,dict):raise Problem('Choose an app update action.')
                    with self.library.operation():
                        if message.get('action')=='discard':
                            self.platform.discard(message.get('key'))
                            return self.response(start,200,self.module_view(self.modules.list()))
                        if message.get('action')!='install' or message.get('saved_patch') is not True:
                            raise Problem('Confirm your patch is saved before updating Poly / View.')
                        batch=self.platform.start(message.get('key'),self.restarter)
                    return self.response(start,202,{'batch':batch})
                if path == '/api/modules/batch' and method == 'POST' and self.restarter:
                    message = self.deletion(env)
                    if message.get('saved_patch') is not True:
                        raise Problem('Confirm your patch is saved before restarting Hector.')
                    with self.library.operation():
                        batch = self.modules.start_batch(message.get('keys'), message.get('native'),
                                                         message.get('revision'), self.restarter)
                    return self.response(start, 202, {'batch': batch})
                if path == '/api/modules/native' and method == 'POST' and self.modules.native and self.restarter:
                    message = self.deletion(env)
                    action = message.get('action')
                    if action not in ('install', 'rollback', 'discard'):
                        raise Problem('Choose a native release action.')
                    with self.library.operation(), self.modules.locked():
                        state = self.modules.checked_state(message.get('revision'), action)
                        archive = self.modules.root / STAGED_ARCHIVE
                        if action == 'discard':
                            self.modules.discard_native(state)
                            return self.response(start, 200, self.module_view(self.modules.listing(state)))
                        if message.get('saved_patch') is not True:
                            raise Problem('Confirm your patch is saved before restarting Hector.')
                        if self.modules.listing(state)['restart_required']:
                            raise Problem('Apply or undo pending module changes before installing this release.', 409)
                        if action == 'install' and not archive.is_file():
                            raise Problem('Upload the verified AetherScope release first.', 409)
                        self.restarter.native_request(action)
                    return self.response(start, 202, {'working': True})
                if path == '/api/modules/restart' and method == 'POST' and self.restarter:
                    message = json.loads(env['wsgi.input'].read(self.body(env, 512)))
                    if (not isinstance(message, dict) or set(message) != {'revision', 'confirmed'}
                            or message['confirmed'] is not True):
                        raise Problem('Confirm the restart before continuing.')
                    with self.library.operation(), self.modules.locked():
                        state = self.modules.listing(self.modules.state())
                        if message['revision'] != state['revision']:
                            raise Problem('The module library changed. Refresh before restarting.', 409)
                        self.restarter.request()
                    return self.response(start, 202, {'restarting': True})
                if path == '/api/modules/upload' and method == 'POST':
                    if env.get('CONTENT_TYPE', '').split(';')[0] != 'application/zip':
                        raise Problem('Choose a ZIP file.', 415)
                    length = self.body(env, platform_release.LIMIT)
                    name = parse_qs(env.get('QUERY_STRING', '')).get('name', [''])[0]
                    raw=env['wsgi.input'].read(length)
                    if len(raw)!=length:raise Problem('The upload was interrupted.')
                    if platform_release.is_platform(raw):
                        self.platform.upload(raw);state=self.modules.list()
                    else:
                        if (self.modules.root/platform_release.STAGED).exists():
                            raise Problem('Install or discard the queued app update before adding modules.',409)
                        state = self.modules.upload(name, io.BytesIO(raw), length)
                    return self.response(start, 201, self.module_view(state))
                if path == '/api/modules/removal-review' and method == 'POST':
                    message = json.loads(env['wsgi.input'].read(self.body(env, 8192)))
                    if not isinstance(message, dict):
                        raise Problem('Select the modules you want to remove.')
                    return self.response(start, 200, self.modules.review_removal(message.get('keys'), message.get('revision')))
                if path == '/api/modules/change' and method == 'POST':
                    message = self.deletion(env)
                    state = self.modules.change(message['action'], message['keys'], message['revision'], message.get('review'))
                    return self.response(start, 200, self.module_view(state))
            if path in ('/api/audio','/api/audio/download','/api/audio/upload','/api/audio/delete'):
                query = parse_qs(env.get('QUERY_STRING', ''), keep_blank_values=True)
                library = query.get('library',['samples'])[0]
                if path=='/api/audio' and method=='GET':
                    return self.response(start, 200, self.audio.listing(library))
                if path=='/api/audio/download' and method=='GET':
                    output,length,name = self.audio.download(library,query['path'][0])
                    return self.download_response(start,output,length,name,'audio/wav','hector-audio.wav')
                if path=='/api/audio/upload' and method=='POST':
                    length=self.body(env,MAX_UPLOAD)
                    result=self.audio.upload(library,query.get('folder',[''])[0],query['name'][0],env['wsgi.input'],length)
                    return self.response(start, 201, result)
                if path=='/api/audio/delete' and method=='POST':
                    message=self.deletion(env)
                    return self.response(start, 200, self.audio.delete(library,message['path'],message['revision']))
            if path == '/api/upload' and method == 'POST':
                if env.get('CONTENT_TYPE', '').split(';')[0] != 'application/zip':
                    raise Problem('Choose a ZIP file.', 415)
                length = self.body(env, MAX_UPLOAD)
                self.library._available(self.library.scratch, length + MIB, 32 * MIB)
                bank = parse_qs(env.get('QUERY_STRING', '')).get('bank', ['beebo'])[0]
                # Input staging is temporary memory, never a retained data-partition ZIP.
                with tempfile.TemporaryFile(dir=self.library.scratch) as source:
                    left = length
                    while left:
                        chunk = env['wsgi.input'].read(min(65536, left))
                        if not chunk:
                            raise Problem('The upload was interrupted.')
                        source.write(chunk)
                        left -= len(chunk)
                    source.seek(0)
                    result = self.library.upload(source, bank)
                return self.response(start, 201, result)
            if path == '/api/export' and method == 'GET':
                query = parse_qs(env.get('QUERY_STRING', ''))
                selected = (query.get('bank', ['beebo'])[0], query['name'][0]) if 'name' in query else None
                output = self.library.archive(selected)
                length = output.seek(0, os.SEEK_END)
                output.seek(0)
                name = selected[1] + '.zip' if selected else 'hector-presets-' + time.strftime('%Y-%m-%d') + '.zip'
                return self.download_response(start,output,length,name,'application/zip','hector-presets.zip')
            raise Problem('This page or action was not found.', 404)
        except Problem as exc:
            return self.response(start, exc.status, {'error': str(exc)})
        except (ValueError, TypeError, KeyError):
            return self.response(start, 400, {'error': 'The request could not be read.'})
        except FileNotFoundError:
            return self.response(start, 404, {'error': 'A preset or storage location changed. Refresh and retry.'})
        except OSError as exc:
            logging.exception('Storage operation failed')
            return self.response(start, 507 if exc.errno == 28 else 409, {'error': 'The storage operation could not finish. Existing presets were kept. Refresh and check free space.'})
        except Exception:
            logging.exception('Request failed')
            return self.response(start, 500, {'error': 'The operation could not finish. Refresh and retry.'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--listen', default='127.0.0.1:8787')
    parser.add_argument('--browser-password-file', type=Path)
    parser.add_argument('--remote-desktop-socket', type=Path)
    parser.add_argument('--module-manager', action='store_true')
    args = parser.parse_args()
    if args.remote_desktop_socket and not args.browser_password_file:
        parser.error('Remote Desktop requires the private on-device control connection.')
    config = json.loads(Path(args.config).read_text())
    os.umask(0o077)
    os.environ['TMPDIR'] = config['scratch']
    tempfile.tempdir = config['scratch']
    screen = ScreenConnection(args.remote_desktop_socket, Path('/run/hector-remote-input/input.sock')) if args.remote_desktop_socket else None
    modules = ModuleManager(DATA, RUNTIME, config['preset_root'], config.get('reserve', 32 * MIB),
                            native=Path('/opt/hector-storage-manager/native/install.py').is_file(), publishers=PublisherPolicy()) if args.module_manager else None
    application = Application(config, args.browser_password_file, screen, modules, ModuleRestart() if modules else None)
    from waitress import serve
    print('Hector manager listening at http://' + args.listen, flush=True)
    with ExitStack() as stack:
        if args.browser_password_file:
            port = int(args.listen.rsplit(':', 1)[1])
            endpoint = Path('/run/hector-storage-manager/browser-access.sock')
            stack.enter_context(BrowserControl(endpoint, port, application.change_local_password, application.desktop))
        serve(application, listen=args.listen, threads=2, connection_limit=8, backlog=8, channel_timeout=30, cleanup_interval=5, max_request_header_size=8192, max_request_body_size=MAX_UPLOAD, inbuf_overflow=128 * 1024, outbuf_overflow=128 * 1024, outbuf_high_watermark=MIB, expose_tracebacks=False, ident='Hector')


if __name__ == '__main__':
    main()
