"""Local platform queue; a separate trusted service performs the self-update."""
import json
import base64
import os
from pathlib import Path
import uuid
import time
import platform_transaction as transaction

from core import MIB,Problem
from module_manager import canonical,publish,boot_id
from module_restart import MARKER
import platform_release as release


class PlatformUpdate:
    def __init__(self,manager,version=release.VERSION,progress=release.PROGRESS):
        self.manager=manager;self.version=Path(version);self.progress=Path(progress)

    def installed(self):
        try:
            value=json.loads(self.version.read_bytes())
            return dict(id=release.ID,name='Network Connection / Poly / View',version=value['version'],
                        status='installed',platform=True,key=release.ID,size=0,file='Platform app')
        except FileNotFoundError:return None

    def batch(self):
        try:progress=json.loads(self.progress.read_bytes())
        except FileNotFoundError:progress=None
        try:request=json.loads((self.manager.root/'platform-request.json').read_bytes())
        except FileNotFoundError:request=None
        if (request and request.get('boot')==boot_id() and request.get('batch') and
                (not progress or progress.get('id')!=request['id'])):
            progress=request['batch']
        # A completed app update must not hide a later module install or error.
        # Requests retain their start time across the automatic reboot.
        try:module=json.loads((self.manager.root/'install-batch.json').read_bytes())
        except FileNotFoundError:module=None
        if progress and module and module.get('requested',0)>progress.get('requested',0):return None
        return progress

    @staticmethod
    def available():
        if MARKER.exists():raise Problem('Hector is updating or already restarting.',409)

    def queued(self):
        path=self.manager.root/release.STAGED
        try:info_on_disk=path.lstat()
        except FileNotFoundError:return None
        invalid_key=release.sha(str((info_on_disk.st_ino,info_on_disk.st_size,info_on_disk.st_mtime_ns)).encode())
        try:
            raw=transaction.regular(path,release.LIMIT)
            info,_,proof=release.read(raw,self.manager.publishers)
        except (OSError,ValueError,Problem):
            return dict(id=release.ID,name='Unreadable app update',version='Upload again',platform=True,
                        invalid=True,key=invalid_key,size=info_on_disk.st_size,file='Discard this damaged package and upload it again.')
        installed=self.installed()
        return dict(id=release.ID,name=info['name'],version=info['version'],platform=True,
                    key=release.sha(raw),size=len(raw),file='Network Connection / Poly / View ZIP',
                    source_release_sha256=release.sha(base64.b64decode(proof['manifest'],validate=True)),
                    previous_version=installed['version'] if installed else None)

    def upload(self,raw):
        info,_,_=release.read(raw,self.manager.publishers)
        if info['id']!=release.ID:raise Problem('Use the Network Connection / Poly / View app release.')
        installed=self.installed()
        if installed and installed['version']==info['version']:raise Problem('This app version is already installed.',409)
        with self.manager.locked():
            self.available()
            state=self.manager.state()
            if (state['queued'] or state['applied']!=state['target'] or state.get('community_pending')
                    or state.get('native_queued')):
                raise Problem('Apply or discard pending module changes before updating Poly / View.',409)
            space=os.statvfs(self.manager.root)
            if space.f_bavail*space.f_frsize<len(raw)+32*MIB:raise Problem('There is too little free space for an app update.',507)
            publish(self.manager.root/release.STAGED,raw)
        return info

    def discard(self,key):
        with self.manager.locked():
            self.available()
            queued=self.queued()
            if not queued or queued['key']!=key:raise Problem('The app queue changed. Refresh before continuing.',409)
            (self.manager.root/release.STAGED).unlink()

    def start(self,key,restarter):
        restarter.require_ready()
        with self.manager.locked():
            self.available()
            queued=self.queued()
            state=self.manager.state()
            if (not queued or queued.get('invalid') or queued['key']!=key or state['queued'] or state['applied']!=state['target']
                    or state.get('community_pending') or state.get('native_queued')):
                raise Problem('The app or module queue changed. Refresh before updating.',409)
            request=dict(schema=1,id=uuid.uuid4().hex,boot=boot_id(),key=key,saved_patch=True,
                         version=queued['version'],requested=time.time(),source_release_sha256=queued['source_release_sha256'])
            batch=dict(id=request['id'],requested=request['requested'],phase='checking',platform=True,modules=[dict(queued,status='checking',percent=0)])
            request['batch']=batch
            publish(self.manager.root/'platform-request.json',canonical(request))
            restarter.platform_request()
        return batch
