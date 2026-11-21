"""Restart Hector once a confirmed standalone install batch is ready."""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))


MARKER = Path('/run/hector-module-restarting')
BATCH_FILE = Path('/mnt/hector-storage-manager/modules/install-batch.json')
WORKER = '/opt/hector-storage-manager/app/module_batch_worker.py'


def run_worker(action, batch, *args):
    command = ['/usr/sbin/runuser', '-u', 'debian', '--', '/usr/bin/python3', '-I', '-B', WORKER,
               action, batch, *args]
    subprocess.run(command, check=True, timeout=120)


def read_batch():
    try:
        fd = os.open(BATCH_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 65536:
            raise ValueError('Invalid install batch record.')
        raw = source.read(65537)
        if len(raw) > 65536:
            raise ValueError('The install batch record exceeds its size limit.')
    return json.loads(raw)


def current_batch(native):
    batch = read_batch()
    if batch is None:
        return None
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    if (batch['boot'] == boot and batch['native'] is native and batch['phase'] == 'checking'
            and re.fullmatch('[a-f0-9]{32}', batch['id'])):
        return batch['id']
    return None


def main():
    info = MARKER.lstat()
    if info.st_uid != 0 or not MARKER.is_file() or MARKER.is_symlink():
        raise ValueError('Module Manager did not authorize a restart.')
    batch = None
    rebooting = False
    try:
        batch = current_batch(False)
        if batch is None or not os.statvfs('/overlay/lower').f_flag & os.ST_RDONLY:
            raise ValueError('The installation request is no longer current.')
        from system_readiness import wait
        wait()
        run_worker('check', batch)
        run_worker('arm', batch)
        subprocess.run(['/bin/systemctl', '--no-block', 'reboot'], check=True, timeout=10)
        rebooting = True
    except Exception as error:
        if batch:
            state = read_batch()
            if state['phase'] != 'failed':
                run_worker('fail', batch, str(error)[:300])
        raise
    finally:
        if not rebooting:
            MARKER.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
