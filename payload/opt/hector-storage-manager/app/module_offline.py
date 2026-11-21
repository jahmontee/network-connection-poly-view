"""Root-side offline data staging; the same signed records are used by Poly / View."""
import argparse
import io
import json
import os
from pathlib import Path
import pwd
import re
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from core import MIB
from module_manager import DATA,RUNTIME,ModuleManager,canonical
from publisher_release import PublisherPolicy
import native_module
import platform_service
import platform_transaction as tr


class OfflineManager(ModuleManager):
    def community_asset_path(self,asset):
        path=super().community_asset_path(asset)
        if not asset.get('owned') and str(path).startswith(('/usr/','/opt/','/home/','/etc/')):
            return self.firmware_root_for_assets/str(path).lstrip('/')
        return path


def stage(raw,folder,firmware=Path('/'),source_sha=None):
    if os.geteuid()!=0:raise ValueError('The trusted offline worker is required')
    app=firmware/'opt/hector-storage-manager/app'
    manager=OfflineManager(DATA,RUNTIME,'/mnt/presets',32*MIB,'/home/debian/start_up.ingen',
        community_catalog=app/'community-catalog.json',publishers=PublisherPolicy(app/'publishers.json'),
        migration_policy=app/'native-migrations.json',firmware_root=firmware)
    manager.firmware_root_for_assets=firmware
    # Root-protected rollback of the affected module's previous data. Unrelated
    # module records and all user presets/audio are retained without copying them.
    platform_service.backup_module(manager,raw,folder)
    state=manager.upload('signed-usb-module.zip',io.BytesIO(raw),len(raw),offline=True)
    key=__import__('hashlib').sha256(raw).hexdigest()
    with manager.locked():
        value=manager.state();record=value['queued'].pop(key)
        if source_sha:record['source_release_sha256']=source_sha
        value['target']={k:v for k,v in value['target'].items() if v['id']!=record['id']}
        value['target'][key]=record;manager.save(value)
        marker=dict(key=key,id=record['id'],source_sha256=source_sha,
                    before_state_sha256=tr.digest(tr.regular(folder/'module-state-before.json')))
        tr.atomic(folder/'module-record.json',canonical(marker))
    user=pwd.getpwnam('debian')
    owned=[DATA,DATA/'state.json',DATA/'lock',DATA/(key+'.so')]
    package=DATA/(key+'.pkg')
    if package.exists():owned+=[package,*package.rglob('*')]
    for path in owned:
        if path.is_symlink():raise ValueError('Unexpected offline module link')
        os.chown(path,user.pw_uid,user.pw_gid)
    return marker


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release',type=Path,required=True)
    parser.add_argument('--backup',type=Path,required=True)
    parser.add_argument('--firmware-root',choices=('/','/overlay/lower'),default='/')
    parser.add_argument('--source-sha256',required=True)
    args=parser.parse_args()
    if not re.fullmatch('[a-f0-9]{64}',args.source_sha256):raise ValueError('Invalid signed source identity')
    if not str(args.backup).startswith(str(platform_service.DATA/'jobs')+'/'):raise ValueError('Use a protected offline job')
    result=stage(tr.regular(args.release,16*MIB),args.backup,Path(args.firmware_root),args.source_sha256)
    print(json.dumps(result))


if __name__=='__main__':
    os.umask(0o077);main()
