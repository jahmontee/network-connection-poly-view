"""Root-side, fixed-purpose handler for a normal reboot of Hector."""
import os
import json
from pathlib import Path
import pwd
import socket
import struct
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from native_release import BUSY_MARKER as MARKER, installed_job_status



def installation_active():
    mounts = (line.split() for line in Path('/proc/1/mountinfo').read_text().splitlines())
    if not any(fields[4] == '/overlay/lower' and 'ro' in fields[5].split(',') for fields in mounts):
        return True
    for entry in Path('/proc').glob('[0-9]*/cmdline'):
        try:
            arguments = entry.read_bytes().split(b'\0')
        except OSError:
            continue
        if (b'--action' in arguments or b'--worker' in arguments) and any(
                arg.endswith((b'/worker.py', b'/install.py', b'/ssh_install.py')) for arg in arguments):
            return True
    return False


def schedule(command):
    from system_readiness import wait,NotReady
    try:wait(timeout=0,emit=lambda message:print(message,file=sys.stderr))
    except NotReady:return b'not-ready\n'
    if installation_active():
        return b'busy\n'
    try:
        fd = os.open(MARKER, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return b'busy\n'
    os.close(fd)
    accepted = False
    try:
        if installation_active():
            return b'busy\n'
        subprocess.run(['/bin/systemctl', '--no-block', *command], check=True, timeout=3)
        accepted = True
        return b'accepted\n'
    except (OSError, subprocess.SubprocessError):
        return b'unavailable\n'
    finally:
        if not accepted:
            MARKER.unlink(missing_ok=True)


def restart():
    return schedule(['reboot'])


def serve(connection):
    connection.settimeout(2)
    _, uid, _ = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if uid != pwd.getpwnam('debian').pw_uid:
        return
    with connection.makefile('rb') as request:
        action = request.readline(32)
        if action == b'readiness\n':
            from system_readiness import wait,NotReady
            try:
                wait(timeout=0,emit=lambda message:print(message,file=sys.stderr));reply=b'ready\n'
            except NotReady as error:
                phase=error.phase if error.phase in ('waiting','deadline','support-version','upgrade-recovery') else 'recovery'
                messages={'waiting':'Flash Repair is still starting. Wait for boot confirmation, then try again.',
                    'deadline':'Flash Repair startup did not finish. Check installation status before retrying.',
                    'support-version':'Use the complete updated USB installer before changing modules.',
                    'upgrade-recovery':'The Flash Repair update needs recovery. Automatic retry is disabled.',
                    'recovery':'Flash Repair needs recovery or inspection before changing modules.'}
                reply=(json.dumps(dict(status='not-ready',phase=phase,message=messages[phase]))+'\n').encode()
        elif action == b'restart\n':
            reply = restart()
        elif action == b'native-status\n':
            reply = (json.dumps(installed_job_status()) + '\n').encode()
        elif action == b'batch-install\n':
            reply = schedule(['start', 'hector-module-batch.service'])
        elif action == b'platform-install\n':
            reply = schedule(['start', 'hector-platform-update.service'])
        elif action in (b'native-install\n', b'native-rollback\n'):
            operation = 'install' if action == b'native-install\n' else 'rollback'
            reply = schedule(['start', 'hector-native-aetherscope@' + operation + '.service'])
        else:
            return
        connection.sendall(reply)


if __name__ == '__main__':
    with socket.fromfd(0, socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        serve(connection)
