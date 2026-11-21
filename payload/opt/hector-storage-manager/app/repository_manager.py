"""Explicit GitHub connections; every offered module is independently verified."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import re
import socket
import stat
import struct
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid

from core import Problem
from module_manager import LIMIT, canonical, check_binary, check_manifest, publish
import native_module
import platform_release
import usb_release
from native_release import NATIVE_ID, native_zip, signed_release
from publisher_release import PublisherPolicy, native_archive_files

MAX_REPOSITORIES=12
MAX_CREATORS=12
MAX_CREATOR_REPOSITORIES=200
MAX_CREATOR_ASSETS=64
MAX_CREATOR_SECONDS=240
DEFAULT_CREATOR='jahmontee'
MAX_RELEASES=20
MAX_ASSETS=32
METADATA_LIMIT=2*1024*1024
ASSET_HOSTS={'github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'}
DOWNLOAD_LIMIT=platform_release.LIMIT
FETCH_SOCKET=Path('/run/hector-repository-fetch.sock')
CHECKS=ThreadPoolExecutor(max_workers=1,thread_name_prefix='poly-view-repository-check')

def creator_url(value):
    if not isinstance(value,str) or len(value)>120:raise Problem('Paste a GitHub profile URL.')
    url=urlsplit(value.strip())
    match=re.fullmatch(r'/([A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?)/?',url.path)
    if url.scheme!='https' or url.netloc.lower()!='github.com' or url.query or url.fragment or not match:
        raise Problem('Use a GitHub profile URL such as https://github.com/creator.')
    owner=match[1].lower()
    return owner,'https://github.com/'+owner


def creator_record(owner):
    return dict(id='creator-'+hashlib.sha256(owner.encode()).hexdigest()[:24],
                kind='creator',owner=owner,slug=owner,url='https://github.com/'+owner,
                default=owner==DEFAULT_CREATOR,status='connected',modules=[],checked=None,error=None)


def repository_url(value):
    if not isinstance(value,str) or len(value)>240:
        raise Problem('Paste a GitHub repository URL.')
    url=urlsplit(value.strip())
    match=re.fullmatch(r'/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?',url.path)
    if (url.scheme!='https' or url.netloc.lower()!='github.com' or url.query or url.fragment or not match):
        raise Problem('Use a GitHub repository URL such as https://github.com/creator/module.')
    owner,name=match.groups();name=name.removesuffix('.git')
    if owner in ('.','..') or name in ('','.','..'):
        raise Problem('Choose a GitHub repository.')
    slug=(owner+'/'+name).lower()
    return slug,'https://github.com/'+slug


def asset_url(value,slug):
    if not isinstance(value,str):raise Problem('The release has an invalid download address.')
    url=urlsplit(value)
    prefix='/'+slug+'/releases/download/'
    if (url.scheme!='https' or url.netloc.lower()!='github.com' or url.query or url.fragment
            or not url.path.lower().startswith(prefix) or len(url.path)<=len(prefix)):
        raise Problem('The release download is outside this connected repository.')
    return value


class BoundedRedirect(HTTPRedirectHandler):
    def __init__(self,metadata=False):
        self.metadata=metadata

    def redirect_request(self,request,fp,code,message,headers,newurl):
        url=urlsplit(newurl)
        if (self.metadata or url.scheme!='https' or url.hostname not in ASSET_HOSTS
                or url.username or url.password or url.port not in (None,443)):
            raise Problem('The repository moved or its download address is unsupported. Add its current GitHub URL.')
        return super().redirect_request(request,fp,code,message,headers,newurl)


class GitHubClient:
    def read(self,url,limit,metadata=False):
        request=Request(url,headers={'User-Agent':'Poly-View-Module-Manager/1','Accept':'application/vnd.github+json' if metadata else 'application/octet-stream'})
        try:
            with build_opener(BoundedRedirect(metadata)).open(request,timeout=15) as response:
                length=response.headers.get('Content-Length')
                if length and int(length)>limit:raise Problem('The repository response exceeds the download limit.',413)
                raw=response.read(limit+1)
                if len(raw)>limit:raise Problem('The repository response exceeds the download limit.',413)
                return raw
        except HTTPError as error:
            if error.code in (403,429):raise Problem('GitHub has temporarily limited requests. Try again later.',429) from error
            if error.code==404:raise Problem('This repository or release is unavailable. Public GitHub repositories are supported.',404) from error
            raise Problem('GitHub could not provide this release. Try again later.',503) from error
        except (URLError,TimeoutError,OSError,ValueError) as error:
            raise Problem('Cannot reach the connected repository. Check Hector’s internet connection and try again.',503) from error

    def releases(self,slug):
        raw=self.read('https://api.github.com/repos/'+slug+'/releases?per_page='+str(MAX_RELEASES),METADATA_LIMIT,True)
        try:value=json.loads(raw)
        except (ValueError,UnicodeError) as error:raise Problem('GitHub returned unreadable release information.') from error
        if not isinstance(value,list) or len(value)>MAX_RELEASES:raise Problem('GitHub returned unsupported release information.')
        return value

    def creator_repositories(self,owner):
        owner,_=creator_url('https://github.com/'+owner)
        slugs=[]
        for page in (1,2):
            raw=self.read('https://api.github.com/users/'+owner+'/repos?type=owner&sort=updated&per_page=100&page='+str(page),METADATA_LIMIT,True)
            try:rows=json.loads(raw)
            except (ValueError,UnicodeError) as error:raise Problem('GitHub returned unreadable repository information.') from error
            if not isinstance(rows,list) or len(rows)>100:raise Problem('GitHub returned unsupported repository information.')
            for row in rows:
                if not isinstance(row,dict) or row.get('private') is not False or row.get('disabled'):continue
                slug=row.get('full_name')
                if not isinstance(slug,str):continue
                normalized,_=repository_url('https://github.com/'+slug)
                if normalized.split('/')[0]!=owner:raise Problem('GitHub returned a repository outside this creator.')
                if normalized not in slugs:slugs.append(normalized)
            if len(rows)<100:return dict(repositories=slugs,limited=False)
        return dict(repositories=slugs[:MAX_CREATOR_REPOSITORIES],limited=True)

    def download(self,url,slug):
        return self.read(asset_url(url,slug),DOWNLOAD_LIMIT)


def release_summary(raw,policy):
    if usb_release.wrapped(raw):
        inner,_,_=usb_release.read(raw,policy)
        return dict(release_summary(inner,policy),complete_usb=True)
    if platform_release.is_platform(raw):
        info,_,_=platform_release.read(raw,policy)
        return dict(id=info['id'],name=info['name'],version=info['version'],publisher=platform_release.OWNER,kind='platform')
    files=native_archive_files(raw)
    if not {'publisher.json','publisher.sig'}<=files.keys():
        return None
    if native_zip(raw):
        manifest,_,_,publisher=signed_release(raw,policy)
        return dict(id=NATIVE_ID,name='AetherScope',version=manifest['version'],publisher=publisher,kind='native-adapter')
    try:manifest=json.loads(files.get('module.json',b'null'))
    except ValueError as error:raise Problem('The signed module manifest could not be read.') from error
    if native_module.is_native(manifest):
        module,_,proof,_=native_module.read(raw,policy)
        return dict(id=module['id'],name=module['name'],version=module['version'],publisher=proof['publisher'],kind='native',uri=module['uri'])
    module,binary,proof=policy.read(raw)
    check_manifest(module);check_binary(module,binary)
    return dict(id=module['id'],name=module['name'],version=module['version'],publisher=proof['publisher'],kind='standalone')


class RepositoryProxy:
    """The web service keeps its LAN-only firewall; outbound work is on demand."""
    def exchange(self,message,limit):
        try:
            info=FETCH_SOCKET.lstat()
            if info.st_uid!=0 or not stat.S_ISSOCK(info.st_mode):raise OSError('Unexpected repository helper')
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
                connection.settimeout(40);connection.connect(str(FETCH_SOCKET))
                _,uid,_=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                if uid!=0:raise OSError('Unexpected repository helper peer')
                connection.sendall(canonical(message)+b'\n')
                with connection.makefile('rb') as reply:
                    header=json.loads(reply.readline(2049))
                    if header.get('error'):raise Problem(header['error'],header.get('status',503))
                    length=header['length']
                    if type(length) is not int or not 0<=length<=limit:raise OSError('Oversized repository response')
                    raw=reply.read(length+1)
                    if len(raw)!=length:raise OSError('Incomplete repository response')
                    return raw
        except Problem:raise
        except (OSError,ValueError,KeyError,TypeError) as error:
            raise Problem('The repository download service is unavailable. Try again shortly.',503) from error

    def releases(self,slug):
        return json.loads(self.exchange(dict(action='releases',slug=slug),METADATA_LIMIT))

    def creator_repositories(self,owner):
        return json.loads(self.exchange(dict(action='creator-repositories',owner=owner),METADATA_LIMIT))

    def download(self,url,slug):
        return self.exchange(dict(action='download',slug=slug,url=asset_url(url,slug)),DOWNLOAD_LIMIT)


class Repositories:
    def __init__(self,manager,client=None,launch=None):
        self.manager=manager
        self.client=client or RepositoryProxy()
        self.path=manager.root/'repositories.json'
        self.launch=launch or self._launch
        self.run=uuid.uuid4().hex

    @staticmethod
    def _launch(task):
        CHECKS.submit(task)

    def _read(self):
        try:
            if self.path.is_symlink() or self.path.stat().st_size>METADATA_LIMIT:
                raise Problem('The saved repository list could not be read.')
            state=json.loads(self.path.read_bytes())
        except FileNotFoundError:state=dict(schema=1,repositories=[])
        except (ValueError,OSError) as error:raise Problem('The saved repository list could not be read.') from error
        if (not isinstance(state,dict) or state.get('schema')!=1
                or not isinstance(state.get('repositories'),list) or len(state['repositories'])>MAX_REPOSITORIES):
            raise Problem('The saved repository list is invalid.')
        creators=state.setdefault('creators',[])
        if not isinstance(creators,list) or len(creators)>MAX_CREATORS:raise Problem('The saved creator list is invalid.')
        default=creator_record(DEFAULT_CREATOR)
        if not any(row.get('id')==default['id'] for row in creators):creators.insert(0,default)
        interrupted=False
        for repo in state['repositories']+creators:
            if repo.get('status')=='checking' and repo.get('run')!=self.run:
                repo.update(status='error',progress='',error='The last check was interrupted. Choose Check for updates to try again.')
                interrupted=True
        if interrupted:self._save(state)
        return state

    def _save(self,state):
        raw=canonical(state)
        if len(raw)>METADATA_LIMIT:raise Problem('The saved repository list exceeds its size limit.')
        publish(self.path,raw)

    def view(self):
        with self.manager.locked():
            state=self._read()
        return {name:[{k:v for k,v in repo.items() if k!='job'} for repo in state[name]] for name in ('repositories','creators')}

    def add_creator(self,url):
        owner,_=creator_url(url)
        with self.manager.locked():
            state=self._read()
            if any(row['owner']==owner for row in state['creators']):raise Problem('This creator is already included.',409)
            if len(state['creators'])>=MAX_CREATORS:raise Problem('Up to 12 creators can be connected.',409)
            state['creators'].append(creator_record(owner));self._save(state)
        return self.view()

    def remove_creator(self,identifier):
        with self.manager.locked():
            state=self._read()
            if identifier==creator_record(DEFAULT_CREATOR)['id']:raise Problem('Jahmontee is the included default creator.',409)
            state['creators']=[row for row in state['creators'] if row['id']!=identifier];self._save(state)
        return self.view()

    def add(self,url):
        slug,url=repository_url(url)
        identifier=hashlib.sha256(slug.encode()).hexdigest()[:24]
        with self.manager.locked():
            state=self._read()
            if any(row['id']==identifier for row in state['repositories']):raise Problem('This repository is already connected.',409)
            if len(state['repositories'])>=MAX_REPOSITORIES:raise Problem('Up to 12 repositories can be connected.',409)
            state['repositories'].append(dict(id=identifier,slug=slug,url=url,status='connected',modules=[],checked=None,error=None))
            self._save(state)
        return self.view()

    def remove(self,identifier):
        with self.manager.locked():
            state=self._read()
            state['repositories']=[row for row in state['repositories'] if row['id']!=identifier]
            self._save(state)
        return self.view()

    def refresh(self,identifier,creator=False):
        job=uuid.uuid4().hex
        with self.manager.locked():
            state=self._read()
            repo=next((row for row in state['creators' if creator else 'repositories'] if row['id']==identifier),None)
            if repo is None:raise Problem('Connect this repository first.',404)
            if repo['status']=='checking':raise Problem('This repository is already being checked.',409)
            repo.update(status='checking',job=job,run=self.run,error=None,progress='Finding repositories…' if creator else 'Checking releases…')
            slug=repo['slug'];self._save(state)
        self.launch(lambda:self._check_creator(identifier,slug,job) if creator else self._check(identifier,slug,job))
        return self.view()

    def _commit(self,identifier,job,fields,creator=False):
        with self.manager.locked():
            state=self._read()
            repo=next((row for row in state['creators' if creator else 'repositories'] if row['id']==identifier and row.get('job')==job),None)
            if repo is None:return False
            repo.update(fields);self._save(state)
            return True

    def _collect(self,slug,modules,stats,update,limit,deadline=None):
        releases=self.client.releases(slug)
        policy=self.manager.publishers or PublisherPolicy()
        for release in releases:
            if not isinstance(release,dict) or release.get('draft'):continue
            assets=release.get('assets',[])
            if not isinstance(assets,list):continue
            for asset in assets:
                if not isinstance(asset,dict):continue
                name=asset.get('name','')
                if not isinstance(name,str) or not name.lower().endswith('.zip'):continue
                size=asset.get('size')
                if type(size) is not int or not 0<size<=DOWNLOAD_LIMIT:continue
                if stats['packages_checked']>=limit or deadline is not None and time.monotonic()>deadline:
                    stats['limited']=True;return True
                stats['packages_checked']+=1
                if not update('Checking package '+str(stats['packages_checked'])+'…'):return False
                try:
                    raw=self.client.download(asset_url(asset.get('browser_download_url'),slug),slug)
                    if not 0<len(raw)<=DOWNLOAD_LIMIT:raise Problem('The module exceeds its size limit.')
                    row=release_summary(raw,policy)
                    if row is None:stats['unsigned']+=1;continue
                    row.update(asset=name,url=asset_url(asset['browser_download_url'],slug),sha256=hashlib.sha256(raw).hexdigest(),
                        bytes=len(raw),beta=bool(release.get('prerelease')) or bool(re.search(r'(?i)(?:^|[-.])(beta|alpha|rc|test)(?:[.-]|$)',row['version'])),
                        published=str(release.get('published_at',''))[:40],notes=str(release.get('body') or '')[:5000],
                        release_url='https://github.com/'+slug+'/releases',repository_slug=slug,verified=True)
                    key=hashlib.sha256((row['publisher']+'\0'+row['id']+'\0'+row['sha256']).encode()).hexdigest()
                    row['key']=key
                    module=modules.setdefault((row['publisher'],row['id']),dict(id=row['id'],name=row['name'],publisher=row['publisher'],versions=[]))
                    if not any(previous['sha256']==row['sha256'] for previous in module['versions']):module['versions'].append(row)
                except Problem:
                    stats['rejected']+=1
        return True

    def _check(self,identifier,slug,job):
        modules={};stats=dict(unsigned=0,rejected=0,packages_checked=0,limited=False)
        try:
            if not self._commit(identifier,job,dict(progress='Checking releases…')):return
            if not self._collect(slug,modules,stats,lambda text:self._commit(identifier,job,dict(progress=text)),MAX_ASSETS):return
            self._commit(identifier,job,dict(status='ready',modules=sorted(modules.values(),key=lambda row:row['name'].casefold()),
                checked=int(time.time()),error=None,progress='',**stats))
        except Exception as error:
            message=str(error) if isinstance(error,Problem) else 'The repository check could not finish. Try again.'
            self._commit(identifier,job,dict(status='error',error=message[:400],progress=''))

    def _check_creator(self,identifier,owner,job):
        modules={};stats=dict(unsigned=0,rejected=0,packages_checked=0,limited=False)
        scanned=0
        try:
            if not self._commit(identifier,job,dict(progress='Finding public repositories…'),True):return
            owner,_=creator_url('https://github.com/'+owner)
            result=self.client.creator_repositories(owner)
            slugs=result.get('repositories')
            if not isinstance(slugs,list) or len(slugs)>MAX_CREATOR_REPOSITORIES:raise Problem('The creator returned too many repositories.')
            stats['limited']=bool(result.get('limited'));deadline=time.monotonic()+MAX_CREATOR_SECONDS
            for slug in slugs:
                normalized,_=repository_url('https://github.com/'+slug)
                if normalized!=slug or slug.split('/')[0]!=owner:raise Problem('A repository is outside this creator.')
                if stats['packages_checked']>=MAX_CREATOR_ASSETS or time.monotonic()>deadline:
                    stats['limited']=True;break
                text='Checking repository '+str(scanned+1)+' of '+str(len(slugs))+'…'
                if not self._commit(identifier,job,dict(progress=text),True):return
                if not self._collect(slug,modules,stats,lambda note:self._commit(identifier,job,dict(progress=text+' '+note),True),MAX_CREATOR_ASSETS,deadline):return
                scanned+=1
            self._commit(identifier,job,dict(status='ready',modules=sorted(modules.values(),key=lambda row:row['name'].casefold()),
                checked=int(time.time()),error=None,progress='',repositories_checked=scanned,repositories_found=len(slugs),**stats),True)
        except Exception as error:
            message=str(error) if isinstance(error,Problem) else 'The creator check could not finish. Try again.'
            self._commit(identifier,job,dict(status='error',error=message[:400],progress=''),True)

    def stage(self,identifier,key,creator=False):
        with self.manager.locked():
            state=self._read()
            repo=next((row for row in state['creators' if creator else 'repositories'] if row['id']==identifier),None)
            if repo is None:raise Problem('This repository is no longer connected.',404)
            release=next((row for module in repo['modules'] for row in module['versions'] if row['key']==key),None)
            if release is None:raise Problem('Check this repository and choose a verified release.',404)
            release=copy.deepcopy(release);slug=release['repository_slug'] if creator else repo['slug']
            if creator and slug.split('/')[0]!=repo['owner']:raise Problem('The package is outside this creator.',409)
        raw=self.client.download(asset_url(release['url'],slug),slug)
        if len(raw)>DOWNLOAD_LIMIT or hashlib.sha256(raw).hexdigest()!=release['sha256']:
            raise Problem('The release changed since it was checked. Refresh the repository before installing.',409)
        # Reverify the current publisher policy; cached badges never authorize writes.
        summary=release_summary(raw,self.manager.publishers or PublisherPolicy())
        if summary is None or (summary['id'],summary['publisher'])!=(release['id'],release['publisher']):
            raise Problem('This release no longer matches its verified publisher.',409)
        with self.manager.locked():
            if not any(row['id']==identifier for row in self._read()['creators' if creator else 'repositories']):
                raise Problem('This repository was disconnected. No package was queued.',409)
        if summary['kind']=='platform':
            from platform_update import PlatformUpdate
            PlatformUpdate(self.manager).upload(raw)
            return self.manager.list()
        if (self.manager.root/platform_release.STAGED).exists():
            raise Problem('Install or discard the queued app update before adding modules.',409)
        return self.manager.upload(release['asset'],io.BytesIO(raw),len(raw))
