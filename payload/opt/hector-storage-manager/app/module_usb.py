"""Expose the same Module Manager on Hector's touchscreen for offline USB use."""
import argparse
import json
import io
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core import MIB, Problem, component
from module_manager import DATA, LIMIT, RUNTIME, ModuleManager
from module_restart import ModuleRestart
from publisher_release import PublisherPolicy
from platform_update import PlatformUpdate
import platform_release


USB = Path('/usb_flash')


def usb_entries(usb):
    if not usb.is_mount():
        return []
    names = []
    for path in usb.iterdir():
        if not path.name.lower().endswith('.zip'):
            continue
        try:
            info = path.lstat()
            component(path.name)
        except (OSError, Problem):
            continue
        if stat.S_ISREG(info.st_mode) and 0 < info.st_size <= platform_release.LIMIT:
            names.append(path.name)
    return sorted(names, key=str.casefold)[:100]


def view(manager, restarter, usb):
    state = manager.list()
    release = state['native_release'] or {}
    native_status = restarter.native_status()
    staged = release.get('staged') and (release.get('signed') or native_status['state'] != 'installed')
    queued = [dict(row, native=False) for row in state['queue']]
    platform=PlatformUpdate(manager)
    platform_queued=platform.queued()
    if platform_queued:queued.append(platform_queued)
    if staged:
        queued.append(dict(release, key='native', native=True))
    installed = state['installed'] + state['community_removal']
    if platform.installed():installed.append(platform.installed())
    return dict(revision=state['revision'], usb=usb_entries(usb), queue=queued, installed=installed,
                restart_required=state['restart_required'], working=restarter.pending(),
                error=state['activation_error'])


def run(manager, restarter, usb, action, selection='', revision='', confirmation=''):
    if action!='list' and restarter.pending():raise Problem('Hector is updating or restarting. Wait until it returns.',409)
    platform=PlatformUpdate(manager)
    platform_queued=platform.queued()
    if action == 'stage':
        component(selection)
        if not usb.is_mount() or selection not in usb_entries(usb):
            raise Problem('Insert the USB drive and select a ZIP at the top level.')
        fd = os.open(usb / selection, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_dev != usb.stat().st_dev:
                raise Problem('Select a ZIP on the mounted USB drive.')
            raw=source.read(platform_release.LIMIT+1)
            if len(raw)!=info.st_size:raise Problem('The USB package changed during reading.')
            if platform_release.is_platform(raw):platform.upload(raw)
            else:
                if platform_queued:raise Problem('Install or discard the queued app update first.',409)
                manager.upload(selection, io.BytesIO(raw),len(raw))
    elif action == 'install':
        if confirmation != 'saved':
            raise Problem('Save your patch before installing.')
        if platform_queued and selection==platform_queued['key']:
            platform.start(selection,restarter)
            return {'restarting':True}
        native = selection == 'native'
        manager.start_batch([] if native else [selection], native, revision, restarter)
        return {'restarting': True}
    elif action == 'review':
        return manager.review_removal([selection], revision)
    elif action in ('remove', 'undo'):
        manager.change(action, [selection], revision, confirmation)
    elif action == 'discard':
        if platform_queued and selection==platform_queued['key']:platform.discard(selection)
        elif selection == 'native':
            with manager.locked():
                state = manager.checked_state(revision, 'discard')
                manager.discard_native(state)
        else:
            manager.change('discard', [selection], revision)
    elif action == 'restart':
        if confirmation != 'saved':
            raise Problem('Save your patch before restarting.')
        with manager.locked():
            manager.checked_state(revision, 'restart')
            restarter.request()
        return {'restarting': True}
    elif action != 'list':
        raise Problem('Unknown Module Manager action.')
    return view(manager, restarter, usb)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action')
    parser.add_argument('selection')
    parser.add_argument('revision')
    parser.add_argument('confirmation')
    args = parser.parse_args()
    os.umask(0o077)
    manager = ModuleManager(DATA, RUNTIME, '/mnt/presets', 32 * MIB, '/home/debian/start_up.ingen',
                            native=Path('/opt/hector-storage-manager/native/install.py').is_file(), publishers=PublisherPolicy())
    try:
        result = run(manager, ModuleRestart(), USB, args.action, args.selection, args.revision, args.confirmation)
    except Problem as error:
        result = {'error': str(error)}
    except (OSError, ValueError):
        result = {'error': 'The operation could not finish. Refresh the module list and try again.'}
    print(json.dumps(result))


if __name__ == '__main__':
    main()
