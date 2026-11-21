"""Verify a confirmed install batch as Hector's Module Manager user."""
import argparse
import hashlib
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from module_manager import (DATA, LIMIT, NATIVE_BATCH_KEY, RESTART_MARKER, RUNTIME,
                            ModuleManager, boot_id)
from publisher_release import PublisherPolicy
from core import Problem
import native_module


def verify_binary(path, expected):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= LIMIT:
            raise ValueError('Invalid staged module file.')
        checksum = hashlib.sha256(source.read(LIMIT + 1)).hexdigest()
        after = os.fstat(source.fileno())
        if checksum != expected or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('The staged module failed its integrity check.')


def check(manager, batch):
    with manager.locked():
        state = manager.state()
        current = manager.batch.read()
        if (not current or current['id'] != batch['id'] or batch['phase'] != 'checking'
                or state['applied'] != state['target'] or state.get('community_pending')):
            raise ValueError('The module queue changed. Review the latest list.')
        records = manager.batch_targets(state, batch)
        for row in batch['modules']:
            key = row['key']
            row['status'] = 'checking'
            if key == NATIVE_BATCH_KEY:
                continue
            row.update(applied=0, total=1, percent=0)
            manager.batch.save(batch)
            try:
                manager.verify_publisher(records[key])
                if native_module.is_native(records[key]):
                    manager.check_native_identity(records[key], state, records)
                    def progress(applied, total):
                        row.update(applied=applied, total=total, percent=10 + round(75 * applied / total))
                        manager.batch.save(batch)
                    native_module.verify_files(manager.root, key, records[key], progress)
                verify_binary(manager.root / (key + '.so'), records[key]['binary_sha256'])
            except (OSError, ValueError, Problem) as error:
                row['status'] = 'failed'
                raise ValueError(row['name'] + ': ' + str(error)) from error
            row.update(status='ready', applied=row['total'], percent=95)
            manager.batch.save(batch)
        batch['phase'] = 'installing' if batch['native'] else 'ready'
        manager.batch.save(batch)


def arm(manager, batch):
    # The native installer already holds the module lock at this point.
    if batch['phase'] not in ('ready', 'installing'):
        raise ValueError('The installation is not ready to restart.')
    for row in batch['modules']:
        row.update(status='ready', percent=95)
    batch['phase'] = 'restarting'
    manager.batch.save(batch)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'arm', 'fail'))
    parser.add_argument('batch')
    parser.add_argument('message', nargs='?', default='The installation stopped.')
    args = parser.parse_args()
    os.umask(0o077)
    manager = ModuleManager(DATA, RUNTIME, '/mnt/presets', 0, publishers=PublisherPolicy())
    batch = manager.batch.read()
    if not RESTART_MARKER.exists() or not batch or batch['id'] != args.batch or batch['boot'] != boot_id():
        raise ValueError('This installation request is no longer current.')
    if args.action == 'fail':
        manager.batch.fail(batch, args.message)
        return
    try:
        (check if args.action == 'check' else arm)(manager, batch)
    except Exception as error:
        current = manager.batch.read()
        if current and current['id'] == batch['id']:
            manager.batch.fail(batch, error)
        raise


if __name__ == '__main__':
    main()
