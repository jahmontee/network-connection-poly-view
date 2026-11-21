"""Initialize new local data only; never replace credentials, presets or profiles."""
import json
import os
from pathlib import Path
import pwd
import secrets
import stat
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from server import password_record
from browser_access import read_password


def directory(path,uid,gid,private=True):
    if path.is_symlink():raise ValueError('Linked data directory')
    if not path.exists():path.mkdir(mode=0o700);os.chown(path,uid,gid)
    info=path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=uid or info.st_mode&(0o077 if private else 0o022):
        raise ValueError('Private data directory permissions changed')


def create(path,value,uid,gid):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as output:
        os.fchown(output.fileno(),uid,gid)
        output.write(json.dumps(value,sort_keys=True).encode()+b'\n');output.flush();os.fsync(output.fileno())
    fd=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:os.fsync(fd)
    finally:os.close(fd)


def initialize(root=Path('/')):
    if os.geteuid()!=0:raise ValueError('root-required')
    user=pwd.getpwnam('debian');data=root/'mnt/hector-storage-manager'
    # Earlier signed offline runtimes can create this parent as root while
    # the modules inside belong to debian. Adopt only that fixed safe directory.
    if data.exists() and not data.is_symlink():
        info=data.lstat()
        if stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022:
            os.chown(data,user.pw_uid,user.pw_gid);os.chmod(data,0o700)
    directory(data,user.pw_uid,user.pw_gid)
    for name in ('modules','browser-access'):directory(data/name,user.pw_uid,user.pw_gid)
    config=data/'config.json';password=data/'browser-access/password.json'
    if not config.exists() and not config.is_symlink():
        record=password_record(secrets.token_urlsafe(36))
        create(config,dict(preset_root='/mnt/presets',scratch='/run/hector-storage-manager',data_root='/mnt',
            reserve=32*1024*1024,password=record,hosts=['localhost','127.0.0.1','pine64so.lan'],
            inspector_socket='/run/hector-storage-inspector/control.sock'),user.pw_uid,user.pw_gid)
    # Read a bounded existing configuration without following references. Its
    # settings are preserved byte for byte, including a pre-existing password.
    fd=os.open(config,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as source:
        info=os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=user.pw_uid or info.st_mode&0o077 or info.st_nlink!=1 or not 0<info.st_size<=65536:raise ValueError('Unsafe configuration')
        value=json.loads(source.read(65537))
    if not password.exists() and not password.is_symlink():create(password,value['password'],user.pw_uid,user.pw_gid)
    fd=os.open(password,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as source:
        info=os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid!=user.pw_uid or info.st_mode&0o077 or info.st_nlink!=1 or not 0<info.st_size<=1024:raise ValueError('Unsafe password record')
        record=json.loads(source.read(1025))
        if set(record)!={'salt','digest'} or len(bytes.fromhex(record['salt']))!=16 or len(bytes.fromhex(record['digest']))!=64:raise ValueError('Invalid password record')
    lab=root/'mnt/community-lab';directory(lab,0,0,private=False)
    for name in ('network-connection','ethernet-panel','wifi-manager'):
        directory(lab/name,0,0,private=(name=='wifi-manager'))
        if name!='wifi-manager':directory(lab/name/'private',0,0)
    print('Local browser ready. Set your browser password in Hector Network Connection.')

if __name__=='__main__':initialize()
