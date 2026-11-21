"""Manage WAVs in the three user audio libraries without following filesystem links."""
from contextlib import contextmanager, ExitStack
import json
import logging
import os
from pathlib import Path
import stat
import struct
from urllib.parse import quote, unquote
import uuid

from core import Problem, component, directory, regular, rename_new, revision, safe_parts

LIBRARIES = {
    'reverb': ('reverbs','Reverb IRs'),
    'cabinet': ('cabs','Cabinet IRs'),
    'samples': ('sample_box','Samples'),
}


def parts(value):
    result = safe_parts(value)
    for item in result:
        component(item)
    return result


def check_wav(handle, length, library):
    header = handle.read(12)
    if len(header) != 12 or header[:4] != b'RIFF' or header[8:12] != b'WAVE':
        raise Problem('Choose a standard WAV file.')
    end = 8 + struct.unpack_from('<I', header, 4)[0]
    if end > length:
        raise Problem('The WAV file is incomplete.')
    fmt = None
    audio = None
    for _ in range(256):
        if handle.tell() + 8 > end or fmt is not None and audio is not None:
            break
        name, count = struct.unpack('<4sI',handle.read(8))
        start = handle.tell()
        if start + count > end:
            raise Problem('The WAV file is incomplete.')
        if name == b'fmt ' and 16 <= count <= 4096:
            fmt = handle.read(count)
        elif name == b'data':
            audio = count
        handle.seek(start + count + count%2)
    if fmt is None or not audio:
        raise Problem('The WAV file has no playable audio.')
    encoding,channels,rate,_,alignment,bits = struct.unpack_from('<HHIIHH',fmt)
    if encoding == 65534 and len(fmt)>=40 and fmt[26:28]==b'\x00\x00':
        subtype = fmt[24:40]
        if subtype[4:]==bytes.fromhex('00001000800000aa00389b71') and struct.unpack_from('<H',fmt,18)[0]==bits:
            encoding=struct.unpack_from('<I',subtype)[0]
    if not (encoding==1 and bits in (16,24,32) or encoding==3 and bits==32):
        raise Problem('Use a 16-, 24-, or 32-bit PCM WAV, or a 32-bit float WAV.')
    if library=='samples':
        if channels not in (1,2) or not 8000<=rate<=192000:
            raise Problem('Sample Box uses mono or stereo WAVs at 8–192 kHz.')
    elif rate!=48000 or channels not in (1,2,4):
        raise Problem('IRs need a 48 kHz WAV with one, two, or four channels.')
    if alignment!=channels*(bits//8) or audio%alignment:
        raise Problem('The WAV has invalid audio frame data.')


class AudioLibrary:
    def __init__(self,presets):
        self.presets=presets
        self.root=presets.data_root/'audio'

    def folder_name(self,library):
        if library not in LIBRARIES:
            raise Problem('Choose Reverb IRs, Cabinet IRs, or Samples.')
        return LIBRARIES[library][0]

    @contextmanager
    def open_folder(self,library,subfolders=()):
        name=self.folder_name(library)
        with ExitStack() as stack:
            fd=stack.enter_context(directory(self.root))
            for folder in (name,*subfolders):
                fd=stack.enter_context(directory(parent=fd,name=folder))
            yield fd

    def listing(self,library):
        name=self.folder_name(library)
        result={'folders':[''],'files':[],'available':False}
        if not (self.root/name).is_dir():
            return result
        with self.open_folder(library) as root:
            result['available']=True
            result['writable']=os.access('.',os.W_OK,dir_fd=root)
            count=0

            def walk(fd,prefix=()):
                nonlocal count
                with os.scandir(fd) as entries:
                    names=sorted((e.name for e in entries if not e.name.startswith('.')),key=str.casefold)
                for entry in names:
                    count+=1
                    if count>16000:
                        raise Problem('This audio library has too many items to list.',413)
                    info=os.stat(entry,dir_fd=fd,follow_symlinks=False)
                    relative=prefix+(entry,)
                    path='/'.join(relative)
                    if stat.S_ISDIR(info.st_mode) and len(relative)<24:
                        result['folders'].append(path)
                        with directory(parent=fd,name=entry) as child:
                            walk(child,relative)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink==1 and entry.lower().endswith('.wav'):
                        result['files'].append(dict(path=path,bytes=info.st_size,revision=revision(info)))
            walk(root)
        return result

    def download(self,library,path):
        names=parts(path)
        if not names[-1].lower().endswith('.wav'):
            raise Problem('Choose a WAV file.')
        with self.open_folder(library) as root,regular(root,names) as (fd,info):
            return os.fdopen(os.dup(fd),'rb'),info.st_size,names[-1]

    def upload(self,library,folder,name,source,length):
        component(name)
        if not name.lower().endswith('.wav'):
            raise Problem('Choose a WAV file.')
        subfolders=parts(folder) if folder else ()
        name_on_disk=self.folder_name(library)
        self.presets._available(self.root/name_on_disk,length,self.presets.reserve)
        with self.presets.operation(),self.open_folder(library,subfolders) as fd:
            temporary='.hector-audio-'+uuid.uuid4().hex
            out=os.open(temporary,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644,dir_fd=fd)
            try:
                with os.fdopen(out,'w+b') as handle:
                    remaining=length
                    while remaining:
                        block=source.read(min(remaining,65536))
                        if not block:
                            raise Problem('The audio upload was interrupted.')
                        handle.write(block)
                        remaining-=len(block)
                    handle.seek(0)
                    check_wav(handle,length,library)
                    os.fsync(handle.fileno())
                try:
                    rename_new(temporary,name,fd)
                except Problem as error:
                    if error.status!=409:
                        raise
                    raise Problem('A file with that name already exists in this folder.',409)
                os.fsync(fd)
            finally:
                try:
                    os.unlink(temporary,dir_fd=fd)
                except FileNotFoundError:
                    pass
        return {'uploaded':True}

    def check_references(self,target):
        try:
            self._check_references(target)
        except OSError as error:
            logging.exception('Audio reference check could not finish')
            raise Problem('Audio deletion was cancelled because a saved preset, bank, or device setting could not be checked. Your audio file was kept.',409) from error

    def _check_references(self,target):
        text=target.name
        spellings={text.encode(),quote(text,safe='').encode(),json.dumps(text)[1:-1].encode()}
        banks=self.root/'sample_box/.sample-box/banks'
        roots=[self.presets.root,banks,self.presets.data_root/'pedal_state']
        inspected=0
        def fail(error):
            raise error
        for root in roots:
            if not root.is_dir():
                continue
            for folder,_,files in os.walk(root,followlinks=False,onerror=fail):
                for name in files:
                    inspected+=1
                    if inspected>60000:
                        raise Problem('The audio reference check reached its limit.',409)
                    path=Path(folder)/name
                    if path.is_symlink():
                        try:
                            used=path.resolve()==target
                        except RuntimeError:
                            raise Problem('An audio link could not be checked.',409)
                    elif (root==self.presets.root and path.parent.parent==root
                          and path.parent.name in ('beebo','digit')
                          and name.endswith('.ingen.startup-before.json')):
                        # Older lab installers saved the previous startup preset
                        # list beside its .ingen bundle. This is rollback metadata,
                        # not a preset or audio state. Files inside bundles, banks,
                        # and the active pedal_state directory are always checked.
                        continue
                    elif path.suffix.lower() in ('.ttl','.json','.ini','.conf'):
                        with directory(path.parent.resolve()) as fd,regular(fd,[name]) as (source,info):
                            if info.st_size>4*1024*1024:
                                raise Problem('A preset or bank file is too large to check safely.',409)
                            with os.fdopen(os.dup(source),'rb') as handle:
                                raw=handle.read(4*1024*1024+1)
                            if info.st_mtime_ns!=os.fstat(source).st_mtime_ns:
                                raise Problem('A preset or bank changed during the check. Try again.',409)
                        used=any(term in raw for term in spellings)
                    else:
                        continue
                    if used:
                        raise Problem('A saved preset, bank, or device setting references this file. Remove the reference first.',409)

    def delete(self,library,path,expected):
        names=parts(path)
        if not names[-1].lower().endswith('.wav'):
            raise Problem('Choose a WAV file.')
        with self.presets.operation(),self.open_folder(library,names[:-1]) as folder:
            with regular(folder,[names[-1]]) as (_,info):
                if expected!=revision(info):
                    raise Problem('This audio file changed. Refresh and review it again.',409)
            self.check_references((self.root/self.folder_name(library)).joinpath(*names).resolve(strict=True))
            if expected!=revision(os.stat(names[-1],dir_fd=folder,follow_symlinks=False)):
                raise Problem('This audio file changed. Refresh and review it again.',409)
            os.unlink(names[-1],dir_fd=folder)
            os.fsync(folder)
        return {'removed':True}
