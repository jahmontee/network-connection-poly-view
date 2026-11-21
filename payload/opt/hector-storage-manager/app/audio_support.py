"""Root-pinned boot services for the two reviewed signed Audio modules.

Module data is verified before copying to a root-only volatile directory.
Unit templates and privileges come from this trusted installer, not archives.
No network settings, preset data, firmware or original USB policy are changed.
"""
import hashlib,json,os,pwd,re,shutil,stat,subprocess,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from module_manager import ModuleManager,DATA,RUNTIME
from publisher_release import PublisherPolicy
import native_module
import platform_transaction as tr

ROOT=Path('/run/hector-audio-support')
EXEC_ROOT=Path('/tmp/hector-audio-support')
UNITS=Path('/run/systemd/system')
OWNER='26e23e5cf387aed68bfd58bdf366f602760acadb072e7609224387e29f862a31'
RECIPES={
    'ableton-link-audio-send':dict(kind='send',ports=19,files=('manager.py','client.py','helper','control','network_binding.py')),
    'ableton-link-audio-receive':dict(kind='receive',ports=18,files=('manager.py','client.py','helper','control','network_binding.py')),
}

def trusted_directory(path,mode=0o700):
    if path.exists():
        info=path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
            raise ValueError('Unprotected Audio service directory')
    else:path.mkdir(mode=mode,parents=True)
    os.chmod(path,mode)

def plan(manager,record,key):
    recipe=RECIPES.get(record.get('id'))
    if not record.get('support'):return None
    if (not isinstance(key,str) or not re.fullmatch('[a-f0-9]{64}',key)
            or recipe is None or record.get('publisher')!=OWNER
            or record['support']!={'api':1,'kind':'link-audio-'+recipe['kind']}
            or record['uri']!='urn:hector-community:link:'+recipe['kind']
            or record.get('effect')!='link_audio_'+recipe['kind']
            or len(record['ports'])<recipe['ports']):
        raise ValueError('Unreviewed privileged Audio service')
    manager.verify_publisher(record)
    native_module.validate_audio_ports(record)
    native_module.verify_files(manager.root,key,record)
    payload={}
    for leaf in recipe['files']:
        name='editor/support/'+leaf
        data=tr.regular(manager.root/(key+'.pkg')/name,8*1024*1024)
        if hashlib.sha256(data).hexdigest()!=record['native_files'].get(name):
            raise ValueError('Audio service payload changed during verification')
        if leaf in ('helper','control') and (data[:7]!=b'\x7fELF\x02\x01\x01' or data[18:20]!=b'\xb7\x00'):
            raise ValueError('Audio service requires an ARM64 executable')
        payload[leaf]=data
    return dict(kind=recipe['kind'],plugin=str(manager.root/(key+'.so')),key=key,payload=payload)

def templates(row):
    kind=row['kind'];folder=ROOT/kind;executables=EXEC_ROOT/kind
    manager=f'''[Unit]
Description=Verified Poly / {kind.title()} Audio helper manager
After=hector-module-catalog.service hector-network-panel.service
[Service]
Type=simple
ExecStart=/usr/bin/python3 -I -B {folder}/manager.py
Environment=HECTOR_AUDIO_PLUGIN={row['plugin']}
Environment=HECTOR_AUDIO_RUNTIME={executables}
RuntimeDirectory=hector-link-{kind}-control
RuntimeDirectoryMode=0755
Restart=on-failure
RestartSec=2
TimeoutStopSec=45
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/run/hector-link-{kind} {executables}
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
LimitCORE=0
UMask=0077
'''
    result={f'hector-link-{kind}-manager.service':manager.encode()}
    for connection in ('ethernet','wifi'):
        session=f'/run/hector-link-{kind}/%i/session'
        command=f'{executables}/helper --session {session} --name "Hector"' if kind=='send' else f'{executables}/helper --receive-only --session {session} --replace-stale --name "Hector Receive %i"'
        # Both current transports remain in the host namespace. The helper's
        # SDK sockets are pinned to the selected NIC, never to an arbitrary route.
        helper=f'''[Unit]
Description=Verified Poly / {kind.title()} %i ({connection})
After=hector-link-{kind}-manager.service
[Service]
Type=simple
Nice=-10
User=debian
Group=debian
NetworkNamespacePath=/proc/1/ns/net
EnvironmentFile={executables}/instances/%i.env
ExecStart={command}
Restart=on-failure
RestartSec=2
TimeoutStopSec=5
KillMode=control-group
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
PrivateDevices=true
ReadWritePaths=/run/hector-link-{kind}/%i
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
CapabilityBoundingSet=CAP_NET_RAW
AmbientCapabilities=CAP_NET_RAW
LimitCORE=0
UMask=0077
'''
        if kind=='send':helper=helper.replace('Nice=-10\n','Nice=-10\nCPUSchedulingPolicy=rr\nCPUSchedulingPriority=1\n')
        result[f'hector-link-{kind}-{connection}@.service']=helper.encode()
    return result

def publish(manager,unit_root=UNITS,root=ROOT):
    if os.geteuid()!=0:raise ValueError('Root Audio service publisher required')
    if root!=ROOT:raise ValueError('Use the fixed volatile Audio service root')
    trusted_directory(root,0o755);trusted_directory(EXEC_ROOT,0o755);trusted_directory(unit_root,0o755)
    user=pwd.getpwnam('debian')
    rows=[]
    with manager.locked():
        state=manager.state()
        for key,record in state['target'].items():
            row=plan(manager,record,key)
            if row is None:continue
            rows.append(row)
    names=[]
    for row in rows:
        kind=row['kind'];folder=root/kind;executables=EXEC_ROOT/kind
        trusted_directory(folder,0o755);trusted_directory(executables,0o755);trusted_directory(executables/'instances')
        sessions=Path('/run/hector-link-'+kind)
        if not sessions.exists():sessions.mkdir(mode=0o700);os.chown(sessions,user.pw_uid,user.pw_gid)
        info=sessions.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=user.pw_uid or info.st_mode&0o077:
            raise ValueError('Unprotected Audio sessions')
        for name,data in row['payload'].items():
            # Data was copied under the module lock and then verified against
            # its signed inventory. Pin it before a root manager can execute it.
            target=(executables if name in ('helper','control') else folder)/name
            mode=0o755 if name in ('helper','control') else 0o644
            tr.atomic(target,data,dict(mode=mode,uid=0,gid=0,atime_ns=0,mtime_ns=0,xattrs={}))
        for name,data in templates(row).items():
            if (Path('/etc/systemd/system')/name).exists():raise ValueError('A legacy Audio service requires reviewed adoption')
            tr.atomic(unit_root/name,data,dict(mode=0o644,uid=0,gid=0,atime_ns=0,mtime_ns=0,xattrs={}))
        names.append(f'hector-link-{kind}-manager.service')
    if names:
        subprocess.run(['/bin/systemctl','daemon-reload'],check=True,timeout=15)
        # Do not block on managers ordered after catalog publication. No helper
        # starts until an actual live plugin instance registers its private queue.
        subprocess.run(['/bin/systemctl','--no-block','start',*names],check=True,timeout=15)
    return names

def main():
    manager=ModuleManager(DATA,RUNTIME,'/mnt/presets',0,publishers=PublisherPolicy())
    print(json.dumps(dict(verified_Audio_services=publish(manager))))

if __name__=='__main__':main()
