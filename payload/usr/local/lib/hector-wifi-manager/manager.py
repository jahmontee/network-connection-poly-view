"""GPL-2.0-or-later. Public AR9271 Wi-Fi RPC; NetworkManager owns networking."""
import json
import os
from pathlib import Path
import pwd
import re
import socket
import stat
import struct
import sys
import threading

sys.path.insert(0,str(Path(__file__).resolve().parent))
import wifi_core as core
from wifi_native import Native

SOCKET=Path('/run/hector-wifi-manager/control.sock')

def inventory(root=Path('/sys/class/net')):
    rows=[]
    for path in root.iterdir():
        try:
            if not (path/'wireless').is_dir():continue
            address=core.mac((path/'address').read_text().strip().lower())
            phy=(path/'phy80211').resolve(strict=True).name
            rows.append(dict(interface=path.name,mac=address,phy=phy,name=path.name,supported=False))
        except (OSError,core.WifiError):continue
    return rows


def respond(connection,controller,user):
    with connection:
        connection.settimeout(4)
        try:
            _,uid,_=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
            # The overview is a root service; the touchscreen runs as debian.
            if uid not in (0,user):return
            with connection.makefile('rb') as request:raw=request.readline(4097)
            if len(raw)>4096 or not raw.endswith(b'\n'):raise core.WifiError('invalid-request')
            reply=dict(ok=True,data=controller.dispatch(json.loads(raw)))
        except core.WifiError as error:reply=dict(ok=False,error=error.code)
        except Exception:reply=dict(ok=False,error='service-unavailable')
        try:connection.sendall(json.dumps(reply,ensure_ascii=True).encode()+b'\n')
        except OSError:pass


def main():
    if os.geteuid()!=0:raise RuntimeError('root-required')
    os.umask(0o077)
    core.private_directory(core.DATA)
    controller=Native(core,inventory,core.DATA/'ar9271.json')
    controller.restore_saved()
    user=pwd.getpwnam('debian')
    if SOCKET.exists():
        info=SOCKET.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=0:raise RuntimeError('untrusted-socket')
        SOCKET.unlink()
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as listener:
        listener.bind(str(SOCKET));os.chown(SOCKET,0,user.pw_gid);os.chmod(SOCKET,0o660);listener.listen(4)
        while True:
            connection,_=listener.accept()
            # Requests are bounded; connection attempts run in Native's single
            # guarded worker. No unbounded thread is created per local client.
            respond(connection,controller,user.pw_uid)

if __name__=='__main__':main()
