#!/usr/bin/env python3
"""Local read-only size inspector for root-protected lab/rollback folders.

No HTTP, shell commands, requested paths, file contents, or mutation interface.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import socket
import struct
import sys
import threading

sys.path.insert(0,str(Path(__file__).resolve().parent))
from core import Library
from storage_metrics import system_file_groups


class Inspector:
    def __init__(self, library, allowed_uid, system_root=None):
        self.library=library
        self.allowed_uid=allowed_uid
        self.system_root=system_root
        self.system={'state':'idle'}

    def inspect_system(self):
        try:
            self.system={'state':'ready','groups':system_file_groups(self.system_root)}
        except (ValueError,OSError):
            self.system={'state':'error'}

    def dispatch(self, peer_uid, message):
        if peer_uid not in (0,self.allowed_uid):
            raise PermissionError('Only the local manager account may inspect storage.')
        if not isinstance(message,dict) or set(message)!={'action'} or message['action'] not in ('status','scan'):
            raise ValueError('Only fixed inspection actions are available.')
        if message['action']=='scan':
            for entry in Path('/proc').glob('[0-9]*/cmdline'):
                try:args=entry.read_bytes().split(b'\0')
                except OSError:continue
                if (b'--action' in args or b'--worker' in args) and any(a.endswith((b'/worker.py',b'/install.py',b'/ssh_install.py')) for a in args):
                    return {'state':'error','rows':[],'message':'An installer is active. Inspect folders after that update has finished.'}
            result=self.library.scan()
            if self.system_root and self.system['state'] in ('idle','error'):
                self.system={'state':'running'}
                threading.Thread(target=self.inspect_system,daemon=True).start()
        else:
            result=self.library.breakdown
        return dict(result,system=self.system) if self.system_root else result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket',default='/run/hector-storage-inspector/control.sock')
    args=parser.parse_args()
    if os.geteuid()!=0:parser.error('The read-only inspector runs as root under its restricted service.')
    account=pwd.getpwnam('debian')
    path=Path(args.socket)
    if path.exists() or path.is_symlink():parser.error('The inspector socket already exists; inspect the existing service first.')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o750)
    os.chown(path.parent,0,account.pw_gid)
    library=Library('/mnt/presets',path.parent,'/mnt')
    inspector=Inspector(library,account.pw_uid,Path('/overlay/lower'))
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as server:
        server.bind(str(path));os.chown(path,0,account.pw_gid);os.chmod(path,0o660);server.listen(4)
        try:
            while True:
                connection,_=server.accept()
                with connection:
                    connection.settimeout(2)
                    try:
                        _,uid,_=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                        raw=b''
                        while b'\n' not in raw:
                            chunk=connection.recv(128)
                            if not chunk or len(raw)+len(chunk)>128:raise ValueError('Invalid request')
                            raw+=chunk
                        result=inspector.dispatch(uid,json.loads(raw.split(b'\n',1)[0]))
                    except (ValueError,OSError,PermissionError):
                        result={'state':'error','rows':[],'message':'Inspection request refused.'}
                    try:connection.sendall(json.dumps(result).encode()+b'\n')
                    except OSError:pass
        finally:
            path.unlink(missing_ok=True)


if __name__=='__main__':main()
