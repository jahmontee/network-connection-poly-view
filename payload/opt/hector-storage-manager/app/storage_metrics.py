"""Read allocated file blocks from Hector's protected system filesystem."""
import os
from pathlib import Path
import stat
import time

SYSTEM_DATA = {'audio', 'etc', 'var', 'usr/share/sounds', 'home/debian/.config'}
OS_PATHS = {'bin', 'boot', 'lib', 'lib64', 'sbin', 'usr', 'home/debian/UI', 'opt/hector-storage-manager'}
MODULE_PATHS = {'usr/lib/lv2', 'usr/local/lib/lv2', 'usr/lib/aarch64-linux-gnu/lv2', 'home/debian/.lv2'}
MEASURED_PATHS = OS_PATHS | SYSTEM_DATA | MODULE_PATHS | {'opt/beebo-lab'}
PARENT_PATHS = {
    '/'.join(path.split('/')[:depth])
    for path in MEASURED_PATHS
    for depth in range(1, len(path.split('/')))
}


def system_file_groups(root):
    """Count each inode once and avoid following links or crossing into other mounts."""
    root = Path(root)
    device = root.stat().st_dev
    totals = dict(os=0, system_data=0, modules=0, other=0)
    linked = set()
    stack = [(root, '', 'other')]
    deadline = time.monotonic() + 360
    count = 0
    while stack:
        folder, prefix, inherited = stack.pop()
        with os.scandir(folder) as entries:
            for entry in entries:
                count += 1
                if count % 256 == 0:
                    if count > 300000 or time.monotonic() > deadline:
                        raise ValueError('System inspection reached its limit.')
                    time.sleep(0.01)
                if entry.is_symlink():
                    continue
                relative = prefix + entry.name
                if inherited == 'other' and relative not in MEASURED_PATHS and relative not in PARENT_PATHS and not relative.startswith('opt/beebo-lab/'):
                    continue
                info = entry.stat(follow_symlinks=False)
                if info.st_dev != device:
                    continue
                if relative in MODULE_PATHS or entry.name.endswith('.lv2') and stat.S_ISDIR(info.st_mode):
                    group = 'modules'
                elif relative in SYSTEM_DATA:
                    group = 'system_data'
                elif relative in OS_PATHS:
                    group = 'os'
                else:
                    group = inherited
                if stat.S_ISDIR(info.st_mode):
                    stack.append((entry.path, relative + '/', group))
                elif not stat.S_ISREG(info.st_mode):
                    continue
                elif info.st_nlink > 1:
                    if info.st_ino in linked:
                        continue
                    linked.add(info.st_ino)
                totals[group] += info.st_blocks * 512
    return totals
