"""Root-owned startup condition for networking's shared Sync helper."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from module_manager import ModuleManager,DATA,RUNTIME
from publisher_release import PublisherPolicy
import native_module

KEY='505e67909f48b534ab3df6304c807d361bcc0ec29fcac85bb4cb0bd6b0853776'
URI='urn:hector-community:link:sync'


def active():
    manager=ModuleManager(DATA,RUNTIME,'/mnt/presets',0,publishers=PublisherPolicy())
    with manager.locked():
        state=manager.state()
        for key,record in state['applied'].items():
            if record.get('id')=='ableton-link-sync' and record.get('uri')==URI:
                manager.verify_publisher(record);manager.migrations.reserve(record)
                native_module.verify_files(manager.root,key,record)
                return True
        # Only the explicitly reviewed old community registration can keep its
        # existing helper. A removed/retired old identity is never revived.
        if (KEY in manager.activation().get('community',[]) and KEY in manager.community
                and KEY in manager.migrations.rules and KEY not in manager.migrations.tombstones(state)
                and KEY not in state.get('community_removed',[])):
            package=manager.community[KEY];rule=manager.migrations.rules[KEY]
            if package['files']!=rule['legacy_files']:return False
            manager.community_assets(package)
            return True
    return False


if __name__=='__main__':
    try:result=active()
    except Exception:result=False
    raise SystemExit(0 if result else 1)
