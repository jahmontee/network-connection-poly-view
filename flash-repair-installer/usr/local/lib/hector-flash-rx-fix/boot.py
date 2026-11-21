# SPDX-License-Identifier: MIT
"""Independent early-boot correction; only operates before NOR is mounted."""
import base64,fcntl,gzip,hashlib,json,os,platform,signal,stat,struct,subprocess,sys,tempfile,time,zlib
from pathlib import Path

BASE=Path('/usr/local/lib/hector-flash-rx-fix')
DATA=Path('/mnt/community-lab/hector-flash-rx-fix')
RUN=Path('/run/hector-flash-rx-fix')
CONTROLLER=Path('/sys/devices/platform/soc/1c68000.spi')
STOCK='sun6i-spi';FIX='hector-sun6i-rx-fix';MODULE='hector_sun6i_rx_fix'
MODULE_SHA='f17184df4b6a8e4f75dcce5062e3bb43cadb0b325faa180c74c95a127cea869e'
CONFIG_SHA='c50e0755d8a9a382333122ec0c0d2f21b42ba91985db99d16197ebe0e96bdfb6'
PUBLIC_DER=base64.b64decode('MCowBQYDK2VwAyEAnhDDI1l3Yg4Kn4wrxQcebb2u3B1MzSmheHPZ0CyBV0M=')
VERSION = '0.1.0-spi-rx.9'

def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':')).encode()
def boot_id():return Path('/proc/sys/kernel/random/boot_id').read_text().strip()
def command(args,timeout=15):
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if p.returncode:raise RuntimeError(' '.join(args)+': '+p.stderr[-600:])
    return p.stdout.strip()
def atomic(path,value):
    raw=canonical(value)+b'\n';tmp=path.with_name('.'+path.name+'.new-'+str(os.getpid())+'-'+str(time.monotonic_ns()))
    fd=os.open(str(tmp),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as out:out.write(raw);out.flush();os.fsync(out.fileno())
        os.replace(str(tmp),str(path))
        fd=os.open(str(path.parent),os.O_DIRECTORY|os.O_RDONLY);os.fsync(fd);os.close(fd)
    finally:
        if tmp.exists():tmp.unlink()
def snapshot(path,limit=4096):
    """Read one atomic mutable-record generation, even if already unlinked.

    A root-only atomic publisher may replace the directory entry after open.
    Link count zero on that immutable fd is legitimate; multiple hard links,
    unsafe parents, symlinks, special files and unbounded input remain refused.
    This contract is only for our mutable state/status, not recovery archives.
    """
    assert path.parent in (DATA,RUN)
    for parent in (path.parent,*path.parent.parents):
        info=parent.lstat()
        assert stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022
    directory=os.open(str(path.parent),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        info=os.fstat(directory)
        assert info.st_uid==0 and not info.st_mode&0o077
        fd=os.open(path.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
    finally:os.close(directory)
    with os.fdopen(fd,'rb') as source:
        info=os.fstat(source.fileno())
        assert (stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink in (0,1)
                and not info.st_mode&0o077 and 0<info.st_size<=limit)
        raw=source.read(limit+1)
        assert len(raw)<=limit
        after=os.fstat(source.fileno())
        # ctime also changes on rename/unlink, independently of contents.
        assert (after.st_size==info.st_size and after.st_mtime_ns==info.st_mtime_ns
                and stat.S_ISREG(after.st_mode) and after.st_uid==0
                and not after.st_mode&0o077 and after.st_nlink in (0,1))
    value=json.loads(raw);assert isinstance(value,dict)
    return value

def state():
    v=snapshot(DATA/'state.json')
    assert v['schema']==1 and v['version']==VERSION and v['module_sha256']==MODULE_SHA
    assert type(v['enabled']) is bool
    return v
def put_state(v):atomic(DATA/'state.json',v)
def change_state(action):
    with (DATA/'state.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        value=action(state());put_state(value);return value
def report(status,**fields):
    RUN.mkdir(mode=0o700,exist_ok=True)
    atomic(RUN/'status.json',dict(status=status,boot=boot_id(),version=VERSION,**fields))
# Fallback is embedded in the bootstrap, independent of optional helper files.
FILESYSTEM_SOURCE=b'# SPDX-License-Identifier: MIT\n"""Exact JFFS2 activation before mounting; original module file stays untouched."""\nimport hashlib\nfrom pathlib import Path\n\nNAME=\'jffs2\'\nCANDIDATE=\'jffs2-preserve-read-errors.ko\'\nCANDIDATE_SHA=\'4e2b9264417a9859481c0b789d7c008a430c53b6d462c9f9b95654fe10fd8049\'\nSTOCK_SHA=\'f618062ee8a52faeeabb315a80166cbd517d777b8516288246416357522b24a1\'\nSTOCK_FILE=Path(\'/usr/lib/modules/5.4.2-rt5-001/kernel/fs/jffs2/jffs2.ko\')\nNOTE=Path(\'/sys/module/jffs2/notes/.note.gnu.build-id\')\nMODULE=Path(\'/sys/module/jffs2\')\nSTOCK_NOTE=bytes.fromhex(\'040000001400000003000000474e55002906c094af02e1b72a2469267aa036b734f9753a\')\nCANDIDATE_NOTE=bytes.fromhex(\'040000001400000003000000474e5500596f746eb07055e6e5a23d013e0810da21437a5d\')\n\ndef sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()\ndef current():\n    if not MODULE.exists(): return \'absent\'\n    if not NOTE.is_file(): return \'unknown\'\n    value=NOTE.read_bytes()\n    return \'stock\' if value==STOCK_NOTE else \'fixed\' if value==CANDIDATE_NOTE else \'unknown\'\n\ndef verify_files(base,stock_file=None):\n    stock_file=STOCK_FILE if stock_file is None else stock_file\n    assert stock_file.is_file() and not stock_file.is_symlink()\n    assert sha(stock_file)==STOCK_SHA,\'Original JFFS2 recovery module differs\'\n    candidate=base/CANDIDATE\n    assert candidate.is_file() and not candidate.is_symlink()\n    assert sha(candidate)==CANDIDATE_SHA,\'Candidate JFFS2 differs\'\n\ndef activate(base,command,quiescent):\n    verify_files(base);quiescent()\n    mode=current()\n    if mode==\'fixed\': return\n    assert mode in (\'stock\',\'absent\'),\'Unrecognized loaded JFFS2; never force unload\'\n    if mode==\'stock\':\n        command([\'/sbin/rmmod\',NAME]);assert current()==\'absent\'\n    # A failed load leaves original bytes available for independent restoration.\n    command([\'/sbin/insmod\',str(base/CANDIDATE)])\n    assert current()==\'fixed\',\'Candidate filesystem did not load\'\n\ndef restore(command,quiescent):\n    quiescent()\n    assert STOCK_FILE.is_file() and not STOCK_FILE.is_symlink() and sha(STOCK_FILE)==STOCK_SHA\n    mode=current();assert mode in (\'stock\',\'fixed\',\'absent\'),\'Unrecognized JFFS2; recovery needs a reboot\'\n    if mode==\'fixed\':\n        command([\'/sbin/rmmod\',NAME]);assert current()==\'absent\'\n    if current()==\'absent\':command([\'/sbin/insmod\',str(STOCK_FILE)])\n    assert current()==\'stock\',\'Original JFFS2 not restored\'\n'
FILESYSTEM_SOURCE_SHA='ec5b7db3d679987e521ec8b6f42a0044c2fb15a7299cb9db636695db12705a8f'
def filesystem_support():
    import types
    assert sha(FILESYSTEM_SOURCE)==FILESYSTEM_SOURCE_SHA
    module=types.ModuleType('hector_embedded_filesystem_support')
    exec(compile(FILESYSTEM_SOURCE,'<pinned-independent-filesystem-support>','exec'),module.__dict__)
    return module
def filesystem():return filesystem_support().current()

def driver():
    p=CONTROLLER/'driver';return p.resolve().name if p.exists() else None
def mounted():return any(s.split()[4]=='/nor_flash' for s in Path('/proc/1/mountinfo').read_text().splitlines())
def no_flash_mounts():
    assert not mounted(),'Never detach a mounted preset device'
    for proc in Path('/proc').glob('[0-9]*'):
        try:assert not any(' 31:0 ' in s or s.split(' - ',1)[1].split()[0]=='jffs2' for s in (proc/'mountinfo').read_text().splitlines()),'JFFS2/flash mounted in another namespace'
        except (FileNotFoundError,ProcessLookupError):pass
def check_flash():
    spi=Path('/sys/bus/spi/devices/spi0.0')
    for unused in range(40):
        if (spi/'driver').exists() and Path('/dev/mtd0ro').exists():break
        time.sleep(.05)
    assert (spi/'driver').resolve().name=='spi-nor'
    assert int(Path('/sys/class/mtd/mtd0/size').read_text())==16777216
    assert Path('/sys/class/mtd/mtd0/type').read_text().strip()=='nor'
    assert int.from_bytes((spi/'of_node/spi-max-frequency').read_bytes(),'big')==40000000
def bind(name):
    assert name in (STOCK,FIX) and driver() is None
    Path('/sys/bus/platform/drivers',name,'bind').write_text(CONTROLLER.name+'\n')
    assert driver()==name
def detach(name):
    assert name in (STOCK,FIX) and driver()==name;no_flash_mounts()
    Path('/sys/bus/platform/drivers',name,'unbind').write_text(CONTROLLER.name+'\n')
    assert driver() is None
def restore_unmounted():
    no_flash_mounts()
    filesystem_support().restore(command,no_flash_mounts)
    if driver()==FIX:
        no_flash_mounts();detach(FIX)
    assert driver() in (None,STOCK)
    (CONTROLLER/'driver_override').write_text('\n')
    if driver() is None:bind(STOCK)
    check_flash()
    if Path('/sys/module',MODULE).exists():command(['/sbin/rmmod',MODULE])
    return True

def verify_release(root=Path('/'),owner=0,scratch='/run'):
    proof=json.loads((BASE/'publisher-proof.json').read_text())
    message=base64.b64decode(proof['manifest'],validate=True);sig=base64.b64decode(proof['signature'],validate=True)
    value=json.loads(message)
    assert message==canonical(value) and value['schema']=='poly-view-publisher/1'
    assert value['key']==sha(PUBLIC_DER) and value['module']=='hector-flash-rx-fix' and value['target']=='hector-407-aarch64'
    assert len(sig)==64
    with tempfile.TemporaryDirectory(prefix='flash-rx-signature-',dir=scratch) as tmp:
        p=Path(tmp);(p/'public.der').write_bytes(PUBLIC_DER);(p/'message').write_bytes(message);(p/'signature').write_bytes(sig)
        command(['/usr/bin/openssl','pkeyutl','-verify','-pubin','-keyform','DER','-inkey',str(p/'public.der'),'-rawin','-in',str(p/'message'),'-sigfile',str(p/'signature')])
    for name,expected in value['files'].items():
        rel=Path(name);assert not rel.is_absolute() and '..' not in rel.parts
        p=root/rel;assert not p.is_symlink() and p.is_file() and p.stat().st_uid==owner
        assert sha(p.read_bytes())==expected,'Installed signed file differs: '+name
    assert sha((BASE/(MODULE+'.ko')).read_bytes())==MODULE_SHA
    filesystem_support().verify_files(BASE,root/filesystem_support().STOCK_FILE.relative_to('/'))

REPAIR_FILES=('usr/local/lib/hector-flash-rx-fix/boot.py', 'usr/local/lib/hector-flash-rx-fix/guard.py', 'usr/local/lib/hector-flash-rx-fix/hector_sun6i_rx_fix.ko', 'usr/local/lib/hector-flash-rx-fix/jffs2-preserve-read-errors.ko', 'usr/local/lib/hector-flash-rx-fix/fix_contract.py', 'usr/local/lib/hector-flash-rx-fix/transaction.py', 'usr/local/lib/hector-flash-rx-fix/remove.py', 'usr/share/doc/hector-flash-rx-fix/GPL-2.0.txt', 'usr/share/doc/hector-flash-rx-fix/NOTICE.txt', 'usr/local/lib/hector-flash-rx-fix/publisher-proof.json', 'etc/systemd/system/hector-flash-rx-guard.service', 'etc/systemd/system/hector-flash-rx-fallback.service', 'etc/systemd/system/hector-flash-rx-confirm.service', 'etc/systemd/system/hector-flash-rx-fix.service', 'etc/systemd/system/multi-user.target.d/96-flash-rx-fix.conf', 'etc/systemd/system/jack.service.d/96-flash-rx-fix.conf', 'etc/systemd/system/lightdm.service.d/96-flash-rx-fix.conf', 'etc/systemd/system/nor_flash.mount.d/96-flash-rx-fix.conf')
# Independent migration recovery. This code lives in boot.py itself, which is
# the FIRST atomic upgrade target. It embeds the unchanged, hash-pinned
# transaction engine with preparation disabled, independent of a mixed tree.
UPGRADE='upgrade-rx7-legacy-disabled'
UPGRADE_FILES=('usr/local/lib/hector-flash-rx-fix/boot.py', 'usr/local/lib/hector-flash-rx-fix/guard.py', 'usr/local/lib/hector-flash-rx-fix/fix_contract.py', 'usr/local/lib/hector-flash-rx-fix/remove.py', 'usr/local/lib/hector-flash-rx-fix/publisher-proof.json', 'etc/systemd/system/hector-flash-rx-fix.service')
OLD_VERSION='0.1.0-spi-rx.1'
OLD_PROOF_SHA='f14e82c0d152c7287ef755777b0f6f248652d1c82ed17f364cac169551b9554d'
TRANSACTION_SHA='a487859df8e8884b5eb5d6ba6bbadfc224b8cd2fb5b1c5890a9b4aa6ec8a86ef'

def protected_record(path,limit=1024*1024,private=True):
    for p in (path.parent,*path.parent.parents):
        info=p.lstat();assert stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not info.st_mode&0o022
    fd=os.open(str(path),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        info=os.fstat(stream.fileno())
        assert stat.S_ISREG(info.st_mode) and info.st_uid==0 and info.st_nlink==1 and not info.st_mode&(0o077 if private else 0o022) and 0<info.st_size<=limit
        raw=stream.read(limit+1);assert len(raw)<=limit;return raw

def verified_proof(raw):
    proof=json.loads(raw);message=base64.b64decode(proof['manifest'],validate=True);signature=base64.b64decode(proof['signature'],validate=True)
    value=json.loads(message)
    assert message==canonical(value) and set(value)=={'schema','key','module','target','files'}
    assert value['schema']=='poly-view-publisher/1' and value['key']==sha(PUBLIC_DER) and value['module']=='hector-flash-rx-fix' and value['target']=='hector-407-aarch64' and len(signature)==64
    with tempfile.TemporaryDirectory(prefix='flash-upgrade-proof-',dir='/run') as tmp:
        p=Path(tmp);(p/'public.der').write_bytes(PUBLIC_DER);(p/'message').write_bytes(message);(p/'signature').write_bytes(signature)
        command(['/usr/bin/openssl','pkeyutl','-verify','-pubin','-keyform','DER','-inkey',str(p/'public.der'),'-rawin','-in',str(p/'message'),'-sigfile',str(p/'signature')])
    return value['files']

RECOVERY_TRANSACTION_SOURCE='IyBTUERYLUxpY2Vuc2UtSWRlbnRpZmllcjogTUlUCiIiIjR4NCB0cmFuc2FjdGlvbiBhZGFwdGVkIGZyb20gbWFsYS1oZWN0b3IvaW5zdGFsbGVyL3RyYW5zYWN0aW9uLnB5IChNSVQpLgoKTm8gbW91bnRzLCBzZXJ2aWNlcywgbmV0d29yaywgb3IgcGh5c2ljYWwtZGV2aWNlIGFjY2VzcyBleGlzdHMgaW4gdGhpcyBtb2R1bGUuCkNvbXBhdGlibGUgd2l0aCBQeXRob24gMy41IGFuZCBuZXdlci4KIiIiCmltcG9ydCBiYXNlNjQKaW1wb3J0IGVycm5vCmltcG9ydCBoYXNobGliCmltcG9ydCBobWFjCmltcG9ydCBqc29uCmltcG9ydCBvcwpmcm9tIHBhdGhsaWIgaW1wb3J0IFBhdGgsIFB1cmVQb3NpeFBhdGgKaW1wb3J0IHN0YXQKCgpkZWYgZGlnZXN0KHJhdyk6CiAgICByZXR1cm4gaGFzaGxpYi5zaGEyNTYocmF3KS5oZXhkaWdlc3QoKQoKCmRlZiBjYW5vbmljYWwob2JqKToKICAgIHJldHVybiBqc29uLmR1bXBzKG9iaiwgc29ydF9rZXlzPVRydWUsIHNlcGFyYXRvcnM9KCcsJywgJzonKSkuZW5jb2RlKCd1dGYtOCcpCgoKZGVmIHNhZmUocm9vdCwgbmFtZSk6CiAgICBwID0gUHVyZVBvc2l4UGF0aChuYW1lKQogICAgaWYgbm90IG5hbWUgb3IgcC5pc19hYnNvbHV0ZSgpIG9yICcuLicgaW4gcC5wYXJ0cyBvciBzdHIocCkgIT0gbmFtZToKICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCdVbnNhZmUgcmVsYXRpdmUgcGF0aDogJyArIG5hbWUpCiAgICBvdXQgPSByb290LmpvaW5wYXRoKCpwLnBhcnRzKQogICAgZm9yIHBhcnQgaW4gW291dF0gKyBsaXN0KG91dC5wYXJlbnRzKToKICAgICAgICBpZiBwYXJ0LmlzX3N5bWxpbmsoKToKICAgICAgICAgICAgcmFpc2UgVmFsdWVFcnJvcignTGlua2VkIHRyYW5zYWN0aW9uIHBhdGg6ICcgKyBuYW1lKQogICAgICAgIGlmIHBhcnQgPT0gcm9vdDoKICAgICAgICAgICAgYnJlYWsKICAgIHJldHVybiBvdXQKCgpkZWYgcmVndWxhcihwYXRoLCBsaW1pdD04ICogMTAyNCAqIDEwMjQpOgogICAgZmQgPSBvcy5vcGVuKHN0cihwYXRoKSwgb3MuT19SRE9OTFkgfCBvcy5PX05PRk9MTE9XIHwgb3MuT19OT05CTE9DSykKICAgIHRyeToKICAgICAgICBpbmZvID0gb3MuZnN0YXQoZmQpCiAgICAgICAgaWYgbm90IHN0YXQuU19JU1JFRyhpbmZvLnN0X21vZGUpIG9yIGluZm8uc3RfbmxpbmsgIT0gMSBvciBpbmZvLnN0X3NpemUgPiBsaW1pdDoKICAgICAgICAgICAgcmFpc2UgVmFsdWVFcnJvcignRXhwZWN0ZWQgYm91bmRlZCwgdW5saW5rZWQgcmVndWxhciBmaWxlOiAnICsgc3RyKHBhdGgpKQogICAgICAgIHdpdGggb3MuZmRvcGVuKGZkLCAncmInKSBhcyBmOgogICAgICAgICAgICBmZCA9IE5vbmUKICAgICAgICAgICAgcmF3ID0gZi5yZWFkKGxpbWl0ICsgMSkKICAgICAgICBpZiBsZW4ocmF3KSA+IGxpbWl0OgogICAgICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCdSZWFkIGJ1ZGdldCBleGNlZWRlZCcpCiAgICAgICAgcmV0dXJuIHJhdwogICAgZmluYWxseToKICAgICAgICBpZiBmZCBpcyBub3QgTm9uZToKICAgICAgICAgICAgb3MuY2xvc2UoZmQpCgoKZGVmIGZpbGVfaGFzaChwYXRoKToKICAgIGlmIG5vdCBvcy5wYXRoLmxleGlzdHMoc3RyKHBhdGgpKToKICAgICAgICByZXR1cm4gTm9uZQogICAgcmV0dXJuIGRpZ2VzdChyZWd1bGFyKHBhdGgsIGxpbWl0PTgwICogMTAyNCAqIDEwMjQpKQoKCmRlZiBzeW5jX2RpcihwYXRoKToKICAgIGZkID0gb3Mub3BlbihzdHIocGF0aCksIG9zLk9fUkRPTkxZIHwgb3MuT19ESVJFQ1RPUlkgfCBvcy5PX05PRk9MTE9XKQogICAgdHJ5OgogICAgICAgIG9zLmZzeW5jKGZkKQogICAgZmluYWxseToKICAgICAgICBvcy5jbG9zZShmZCkKCgpkZWYgbWV0YWRhdGEocGF0aCk6CiAgICBzdCA9IHBhdGguc3RhdCgpCiAgICByZXR1cm4gZGljdChtb2RlPXN0YXQuU19JTU9ERShzdC5zdF9tb2RlKSwgdWlkPXN0LnN0X3VpZCwgZ2lkPXN0LnN0X2dpZCwKICAgICAgICAgICAgICAgIGF0aW1lX25zPXN0LnN0X2F0aW1lX25zLCBtdGltZV9ucz1zdC5zdF9tdGltZV9ucywKICAgICAgICAgICAgICAgIHhhdHRycz17bjogYmFzZTY0LmI2NGVuY29kZShvcy5nZXR4YXR0cihzdHIocGF0aCksIG4pKS5kZWNvZGUoJ2FzY2lpJykKICAgICAgICAgICAgICAgICAgICAgICAgZm9yIG4gaW4gb3MubGlzdHhhdHRyKHN0cihwYXRoKSl9KQoKCmRlZiBhdG9taWMocGF0aCwgcmF3LCBtZXRhPU5vbmUsIHRlbXBvcmFyeT1Ob25lKToKICAgIHRlbXAgPSB0ZW1wb3Jhcnkgb3IgcGF0aC53aXRoX25hbWUocGF0aC5uYW1lICsgJy5uZXctJyArIG9zLnVyYW5kb20oOCkuaGV4KCkpCiAgICBmZCA9IG9zLm9wZW4oc3RyKHRlbXApLCBvcy5PX1dST05MWSB8IG9zLk9fQ1JFQVQgfCBvcy5PX0VYQ0wgfCBvcy5PX05PRk9MTE9XLCAwbzYwMCkKICAgIHRyeToKICAgICAgICB3aXRoIG9zLmZkb3BlbihmZCwgJ3diJykgYXMgZjoKICAgICAgICAgICAgZi53cml0ZShyYXcpCiAgICAgICAgICAgIGYuZmx1c2goKQogICAgICAgICAgICBpZiBtZXRhOgogICAgICAgICAgICAgICAgb3MuZmNobW9kKGYuZmlsZW5vKCksIG1ldGFbJ21vZGUnXSkKICAgICAgICAgICAgICAgIG9zLmZjaG93bihmLmZpbGVubygpLCBtZXRhWyd1aWQnXSwgbWV0YVsnZ2lkJ10pCiAgICAgICAgICAgICAgICBmb3IgbmFtZSwgdmFsdWUgaW4gbWV0YVsneGF0dHJzJ10uaXRlbXMoKToKICAgICAgICAgICAgICAgICAgICBvcy5zZXR4YXR0cihmLmZpbGVubygpLCBuYW1lLCBiYXNlNjQuYjY0ZGVjb2RlKHZhbHVlKSkKICAgICAgICAgICAgICAgIG9zLnV0aW1lKGYuZmlsZW5vKCksIG5zPShtZXRhWydhdGltZV9ucyddLCBtZXRhWydtdGltZV9ucyddKSkKICAgICAgICAgICAgb3MuZnN5bmMoZi5maWxlbm8oKSkKICAgICAgICBvcy5yZXBsYWNlKHN0cih0ZW1wKSwgc3RyKHBhdGgpKQogICAgICAgIHN5bmNfZGlyKHBhdGgucGFyZW50KQogICAgZmluYWxseToKICAgICAgICBpZiBvcy5wYXRoLmxleGlzdHMoc3RyKHRlbXApKToKICAgICAgICAgICAgdGVtcC51bmxpbmsoKQoKCmZyb20gZml4X2NvbnRyYWN0IGltcG9ydCB2YWxpZGF0ZV9tYW5pZmVzdAoKCmNsYXNzIFRyYW5zYWN0aW9uOgogICAgZGVmIF9faW5pdF9fKHNlbGYsIHJvb3QsIHN0b3JlLCBrZXkpOgogICAgICAgIHNlbGYucm9vdCwgc2VsZi5zdG9yZSwgc2VsZi5rZXkgPSBQYXRoKHJvb3QpLCBQYXRoKHN0b3JlKSwga2V5CiAgICAgICAgc2VsZi5yZWNvcmQgPSBOb25lCgogICAgZGVmIHNhdmUoc2VsZik6CiAgICAgICAgY29udGVudCA9IHNlbGYucmVjb3JkCiAgICAgICAgZW52ZWxvcGUgPSBkaWN0KGNvbnRlbnQ9Y29udGVudCwgaG1hYz1obWFjLm5ldyhzZWxmLmtleSwgY2Fub25pY2FsKGNvbnRlbnQpLCBoYXNobGliLnNoYTI1NikuaGV4ZGlnZXN0KCkpCiAgICAgICAgYXRvbWljKHNlbGYuc3RvcmUgLyAnam91cm5hbC5qc29uJywgY2Fub25pY2FsKGVudmVsb3BlKSkKCiAgICBkZWYgbG9hZChzZWxmKToKICAgICAgICBlbnZlbG9wZSA9IGpzb24ubG9hZHMocmVndWxhcihzZWxmLnN0b3JlIC8gJ2pvdXJuYWwuanNvbicsIGxpbWl0PTI0ICogMTAyNCAqIDEwMjQpLmRlY29kZSgpKQogICAgICAgIGV4cGVjdGVkID0gaG1hYy5uZXcoc2VsZi5rZXksIGNhbm9uaWNhbChlbnZlbG9wZVsnY29udGVudCddKSwgaGFzaGxpYi5zaGEyNTYpLmhleGRpZ2VzdCgpCiAgICAgICAgaWYgbm90IGhtYWMuY29tcGFyZV9kaWdlc3QoZXhwZWN0ZWQsIGVudmVsb3BlWydobWFjJ10pOgogICAgICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCdCYWNrdXAgam91cm5hbCBhdXRoZW50aWNhdGlvbiBmYWlsZWQnKQogICAgICAgIHNlbGYucmVjb3JkID0gZW52ZWxvcGVbJ2NvbnRlbnQnXQogICAgICAgIHJldHVybiBzZWxmLnJlY29yZAoKICAgIGRlZiBwcmVmbGlnaHQoc2VsZiwgbWFuaWZlc3QsIHBheWxvYWQpOgogICAgICAgIHZhbGlkYXRlX21hbmlmZXN0KG1hbmlmZXN0KQogICAgICAgIGlmIHNldChwYXlsb2FkKSAhPSB7clsncGF0aCddIGZvciByIGluIG1hbmlmZXN0WydmaWxlcyddfToKICAgICAgICAgICAgcmFpc2UgVmFsdWVFcnJvcignUGF5bG9hZCBmaWxlIHNldCBkaWZmZXJzJykKICAgICAgICBmb3Igcm93IGluIG1hbmlmZXN0WydmaWxlcyddOgogICAgICAgICAgICBuYW1lID0gcm93WydwYXRoJ10KICAgICAgICAgICAgaWYgZGlnZXN0KHBheWxvYWRbbmFtZV0pICE9IHJvd1snc2hhMjU2J10gb3IgbGVuKHBheWxvYWRbbmFtZV0pICE9IHJvd1snc2l6ZSddOgogICAgICAgICAgICAgICAgcmFpc2UgVmFsdWVFcnJvcignQ29ycnVwdCBwYXlsb2FkOiAnICsgbmFtZSkKICAgICAgICAgICAgaWYgZmlsZV9oYXNoKHNhZmUoc2VsZi5yb290LCBuYW1lKSkgIT0gcm93WydiZWZvcmVfc2hhMjU2J106CiAgICAgICAgICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCdDdXJyZW50IEhlY3RvciBiYXNlbGluZSBtaXNtYXRjaDogJyArIG5hbWUpCiAgICAgICAgICAgIGlmIHJvdy5nZXQoJ2JlZm9yZV9tZXRhZGF0YScpIGlzIG5vdCBOb25lOgogICAgICAgICAgICAgICAgb2JzZXJ2ZWQgPSBtZXRhZGF0YShzYWZlKHNlbGYucm9vdCwgbmFtZSkpCiAgICAgICAgICAgICAgICBpZiBhbnkob2JzZXJ2ZWRba10gIT0gcm93WydiZWZvcmVfbWV0YWRhdGEnXVtrXSBmb3IgayBpbiBbJ21vZGUnLCd1aWQnLCdnaWQnLCdtdGltZV9ucycsJ3hhdHRycyddKToKICAgICAgICAgICAgICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCdPcmlnaW5hbCBtZXRhZGF0YSBjaGFuZ2VkOiAnICsgbmFtZSkKICAgICAgICBmb3IgbmFtZSwgdmFsdWUgaW4gbWFuaWZlc3RbJ2d1YXJkcyddLml0ZW1zKCk6CiAgICAgICAgICAgIGlmIGZpbGVfaGFzaChzYWZlKHNlbGYucm9vdCwgbmFtZSkpICE9IHZhbHVlOgogICAgICAgICAgICAgICAgcmFpc2UgVmFsdWVFcnJvcignU3RvY2sgY2F0YWxvZyBndWFyZCBkaWZmZXJzOiAnICsgbmFtZSkKICAgICAgICBmb3IgbmFtZSBpbiBtYW5pZmVzdFsncmVxdWlyZV9hYnNlbnQnXToKICAgICAgICAgICAgaWYgb3MucGF0aC5sZXhpc3RzKHN0cihzYWZlKHNlbGYucm9vdCwgbmFtZSkpKToKICAgICAgICAgICAgICAgIHJhaXNlIFZhbHVlRXJyb3IoJ0V4aXN0aW5nIDR4NC9jb21tdW5pdHkgdGFyZ2V0OiAnICsgbmFtZSkKCiAgICBkZWYgcHJlcGFyZShzZWxmLCBtYW5pZmVzdCwgcGF5bG9hZCwgaWRlbnRpdHksIGJvb3QpOgogICAgICAgIHNlbGYucHJlZmxpZ2h0KG1hbmlmZXN0LCBwYXlsb2FkKQogICAgICAgIGlmIChzZWxmLnN0b3JlIC8gJ2pvdXJuYWwuanNvbicpLmV4aXN0cygpOgogICAgICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCdFeGlzdGluZyB0cmFuc2FjdGlvbiBtdXN0IG5vdCBiZSByZXVzZWQnKQogICAgICAgIHJvd3MgPSBbXQogICAgICAgIHVpX293bmVyID0gbWV0YWRhdGEoc2FmZShzZWxmLnJvb3QsICdob21lL2RlYmlhbi9VSS9zaG93X3dpZGdldC5weScpKQogICAgICAgIGZvciByb3cgaW4gbWFuaWZlc3RbJ2ZpbGVzJ106CiAgICAgICAgICAgIHAgPSBzYWZlKHNlbGYucm9vdCwgcm93WydwYXRoJ10pCiAgICAgICAgICAgIHJhdyA9IHJlZ3VsYXIocCkgaWYgcm93WydiZWZvcmVfc2hhMjU2J10gZWxzZSBOb25lCiAgICAgICAgICAgIG9sZF9tZXRhID0gbWV0YWRhdGEocCkgaWYgcmF3IGlzIG5vdCBOb25lIGVsc2UgTm9uZQogICAgICAgICAgICBuZXdfbWV0YSA9IGRpY3Qob2xkX21ldGEpIGlmIG9sZF9tZXRhIGVsc2UgZGljdChtb2RlPTBvNzU1IGlmIHJvd1sncGF0aCddPT0ndXNyL2xvY2FsL2xpYi9oZWN0b3ItbGluay1zeW5jL2NvbnRyb2wnIGVsc2UgMG82NDQsIHVpZD11aV9vd25lclsndWlkJ10gaWYgcm93WydwYXRoJ10uc3RhcnRzd2l0aCgnaG9tZS8nKSBlbHNlIHNlbGYucm9vdC5zdGF0KCkuc3RfdWlkLAogICAgICAgICAgICAgICAgZ2lkPXVpX293bmVyWydnaWQnXSBpZiByb3dbJ3BhdGgnXS5zdGFydHN3aXRoKCdob21lLycpIGVsc2Ugc2VsZi5yb290LnN0YXQoKS5zdF9naWQsCiAgICAgICAgICAgICAgICBhdGltZV9ucz0wLCBtdGltZV9ucz0wLCB4YXR0cnM9e30pCiAgICAgICAgICAgIG5ld19tZXRhWydtdGltZV9ucyddID0gbWFuaWZlc3RbJ3B1Ymxpc2hlZF9tdGltZV9ucyddCiAgICAgICAgICAgIHJvd3MuYXBwZW5kKGRpY3Qocm93LCBvcmlnaW5hbD1iYXNlNjQuYjY0ZW5jb2RlKHJhdykuZGVjb2RlKCdhc2NpaScpIGlmIHJhdyBpcyBub3QgTm9uZSBlbHNlIE5vbmUsCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgbWV0YWRhdGE9b2xkX21ldGEsIGluc3RhbGxlZF9tZXRhZGF0YT1uZXdfbWV0YSkpCiAgICAgICAgc2VsZi5yZWNvcmQgPSBkaWN0KHNjaGVtYT0xLCB2ZXJzaW9uPW1hbmlmZXN0Wyd2ZXJzaW9uJ10sIGlkZW50aXR5PWlkZW50aXR5LCBib290PWJvb3QsCiAgICAgICAgICAgIHBoYXNlPSdwcmVwYXJlZCcsIGZpbGVzPXJvd3MsIGd1YXJkcz1tYW5pZmVzdFsnZ3VhcmRzJ10sIGd1YXJkX21ldGFkYXRhPW1hbmlmZXN0WydndWFyZF9tZXRhZGF0YSddLCBhcHBsaWVkPVtdLCBwZW5kaW5nPU5vbmUsCiAgICAgICAgICAgIGNyZWF0ZWRfZGlycz1bXSwgc2VydmljZXM9e30sIGlkPXNlbGYuc3RvcmUubmFtZSwgcGVuZGluZ19yZXN0b3JlPU5vbmUsCiAgICAgICAgICAgIHBhY2thZ2VfZGlnZXN0PWRpZ2VzdChjYW5vbmljYWwobWFuaWZlc3QpKSkKICAgICAgICBzZWxmLnNhdmUoKQogICAgICAgICMgUm91bmQtdHJpcCB0aGUgY29tcGxldGUgZHVyYWJsZSBvcmlnaW5hbCBzZXQgYmVmb3JlIGFueSB0YXJnZXQgd3JpdGUuCiAgICAgICAgc2VsZi5sb2FkKCkKICAgICAgICBmb3Igcm93IGluIHJvd3M6CiAgICAgICAgICAgIGlmIHJvd1snb3JpZ2luYWwnXSBpcyBub3QgTm9uZSBhbmQgZGlnZXN0KGJhc2U2NC5iNjRkZWNvZGUocm93WydvcmlnaW5hbCddKSkgIT0gcm93WydiZWZvcmVfc2hhMjU2J106CiAgICAgICAgICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCdPcmlnaW5hbCBiYWNrdXAgZG9lcyBub3QgbWF0Y2gnKQoKICAgIGRlZiBlbnN1cmVfcGFyZW50cyhzZWxmLCBwKToKICAgICAgICBtaXNzaW5nID0gW10KICAgICAgICBwYXJlbnQgPSBwLnBhcmVudAogICAgICAgIHdoaWxlIG5vdCBwYXJlbnQuZXhpc3RzKCk6CiAgICAgICAgICAgIG1pc3NpbmcuYXBwZW5kKHBhcmVudCkKICAgICAgICAgICAgcGFyZW50ID0gcGFyZW50LnBhcmVudAogICAgICAgIGZvciBwYXJlbnQgaW4gcmV2ZXJzZWQobWlzc2luZyk6CiAgICAgICAgICAgIHNlbGYucmVjb3JkWydjcmVhdGVkX2RpcnMnXS5hcHBlbmQoc3RyKHBhcmVudC5yZWxhdGl2ZV90byhzZWxmLnJvb3QpKSkKICAgICAgICAgICAgc2VsZi5zYXZlKCkKICAgICAgICAgICAgcGFyZW50Lm1rZGlyKG1vZGU9MG83NTUpCiAgICAgICAgICAgIHN5bmNfZGlyKHBhcmVudC5wYXJlbnQpCgogICAgZGVmIHRlbXBfcGF0aChzZWxmLCBwKToKICAgICAgICByZXR1cm4gcC53aXRoX25hbWUoJy4nICsgcC5uYW1lICsgJy5mb3VyLWJ5LWZvdXItJyArIHNlbGYucmVjb3JkWydpZCddICsgJy50bXAnKQoKICAgIGRlZiBhcHBseShzZWxmLCBwYXlsb2FkLCBjaGVja3BvaW50PWxhbWJkYSBzdGFnZSwgbmFtZTogTm9uZSk6CiAgICAgICAgc2VsZi5yZWNvcmRbJ3BoYXNlJ10gPSAncHVibGlzaGluZycKICAgICAgICBzZWxmLnNhdmUoKQogICAgICAgIGZvciByb3cgaW4gc2VsZi5yZWNvcmRbJ2ZpbGVzJ106CiAgICAgICAgICAgIG5hbWUgPSByb3dbJ3BhdGgnXQogICAgICAgICAgICBwID0gc2FmZShzZWxmLnJvb3QsIG5hbWUpCiAgICAgICAgICAgIGlmIGZpbGVfaGFzaChwKSAhPSByb3dbJ2JlZm9yZV9zaGEyNTYnXSBvciBkaWdlc3QocGF5bG9hZFtuYW1lXSkgIT0gcm93WydzaGEyNTYnXToKICAgICAgICAgICAgICAgIHJhaXNlIFZhbHVlRXJyb3IoJ1RhcmdldCBvciBwYXlsb2FkIGNoYW5nZWQgYmVmb3JlIHB1YmxpY2F0aW9uOiAnICsgbmFtZSkKICAgICAgICAgICAgc2VsZi5lbnN1cmVfcGFyZW50cyhwKQogICAgICAgICAgICBzZWxmLnJlY29yZFsncGVuZGluZyddID0gbmFtZQogICAgICAgICAgICBzZWxmLnNhdmUoKQogICAgICAgICAgICBjaGVja3BvaW50KCdiZWZvcmUnLCBuYW1lKQogICAgICAgICAgICBhdG9taWMocCwgcGF5bG9hZFtuYW1lXSwgcm93WydpbnN0YWxsZWRfbWV0YWRhdGEnXSwgc2VsZi50ZW1wX3BhdGgocCkpCiAgICAgICAgICAgIGNoZWNrcG9pbnQoJ2FmdGVyJywgbmFtZSkKICAgICAgICAgICAgc2VsZi5yZWNvcmRbJ2FwcGxpZWQnXS5hcHBlbmQobmFtZSkKICAgICAgICAgICAgc2VsZi5yZWNvcmRbJ3BlbmRpbmcnXSA9IE5vbmUKICAgICAgICAgICAgc2VsZi5zYXZlKCkKICAgICAgICBzZWxmLnZlcmlmeV9pbnN0YWxsZWQoKQogICAgICAgIHNlbGYucmVjb3JkWydwaGFzZSddID0gJ2NvbW1pdHRlZCcKICAgICAgICBzZWxmLnNhdmUoKQoKICAgIGRlZiB2ZXJpZnlfaW5zdGFsbGVkKHNlbGYpOgogICAgICAgIGZvciByb3cgaW4gc2VsZi5yZWNvcmRbJ2ZpbGVzJ106CiAgICAgICAgICAgIHAgPSBzYWZlKHNlbGYucm9vdCwgcm93WydwYXRoJ10pCiAgICAgICAgICAgIGlmIGZpbGVfaGFzaChwKSAhPSByb3dbJ3NoYTI1NiddOgogICAgICAgICAgICAgICAgcmFpc2UgVmFsdWVFcnJvcignSW5zdGFsbGVkIGZpbGUgZGlmZmVyczogJyArIHJvd1sncGF0aCddKQogICAgICAgICAgICBtZXRhID0gbWV0YWRhdGEocCkKICAgICAgICAgICAgaWYgYW55KG1ldGFbbl0gIT0gcm93WydpbnN0YWxsZWRfbWV0YWRhdGEnXVtuXSBmb3IgbiBpbiBbJ21vZGUnLCAndWlkJywgJ2dpZCcsICdtdGltZV9ucycsICd4YXR0cnMnXSk6CiAgICAgICAgICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCdJbnN0YWxsZWQgbWV0YWRhdGEgZGlmZmVyczogJyArIHJvd1sncGF0aCddKQogICAgICAgIGZvciBuYW1lLCB2YWx1ZSBpbiBzZWxmLnJlY29yZFsnZ3VhcmRzJ10uaXRlbXMoKToKICAgICAgICAgICAgaWYgZmlsZV9oYXNoKHNhZmUoc2VsZi5yb290LCBuYW1lKSkgIT0gdmFsdWU6CiAgICAgICAgICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCdQcmVzZXJ2ZWQgY2F0YWxvZyBkaWZmZXJzJykKICAgICAgICAgICAgZXhwZWN0ZWQ9c2VsZi5yZWNvcmQuZ2V0KCdndWFyZF9tZXRhZGF0YScse30pLmdldChuYW1lKQogICAgICAgICAgICBpZiBleHBlY3RlZCBpcyBub3QgTm9uZToKICAgICAgICAgICAgICAgIG9ic2VydmVkPW1ldGFkYXRhKHNhZmUoc2VsZi5yb290LG5hbWUpKQogICAgICAgICAgICAgICAgaWYgYW55KG9ic2VydmVkW2tdIT1leHBlY3RlZFtrXSBmb3IgayBpbiBbJ21vZGUnLCd1aWQnLCdnaWQnLCdtdGltZV9ucycsJ3hhdHRycyddKTpyYWlzZSBWYWx1ZUVycm9yKCdQcmVzZXJ2ZWQgbWV0YWRhdGEgZGlmZmVyczogJytuYW1lKQoKICAgIGRlZiB2ZXJpZnlfcmVzdG9yZWQoc2VsZik6CiAgICAgICAgZm9yIHJvdyBpbiBzZWxmLnJlY29yZFsnZmlsZXMnXToKICAgICAgICAgICAgcCA9IHNhZmUoc2VsZi5yb290LCByb3dbJ3BhdGgnXSkKICAgICAgICAgICAgaWYgZmlsZV9oYXNoKHApICE9IHJvd1snYmVmb3JlX3NoYTI1NiddOgogICAgICAgICAgICAgICAgcmFpc2UgVmFsdWVFcnJvcignUmVzdG9yZWQgZmlsZSBkaWZmZXJzOiAnICsgcm93WydwYXRoJ10pCiAgICAgICAgICAgIGlmIHJvd1snbWV0YWRhdGEnXSBpcyBub3QgTm9uZToKICAgICAgICAgICAgICAgIG9ic2VydmVkID0gbWV0YWRhdGEocCkKICAgICAgICAgICAgICAgIGlmIGFueShvYnNlcnZlZFtrXSAhPSByb3dbJ21ldGFkYXRhJ11ba10gZm9yIGsgaW4gWydtb2RlJywgJ3VpZCcsICdnaWQnLCAnbXRpbWVfbnMnLCAneGF0dHJzJ10pOgogICAgICAgICAgICAgICAgICAgIHJhaXNlIFZhbHVlRXJyb3IoJ1Jlc3RvcmVkIG1ldGFkYXRhIGRpZmZlcnM6ICcgKyByb3dbJ3BhdGgnXSkKICAgICAgICBmb3IgbmFtZSwgdmFsdWUgaW4gc2VsZi5yZWNvcmRbJ2d1YXJkcyddLml0ZW1zKCk6CiAgICAgICAgICAgIGlmIGZpbGVfaGFzaChzYWZlKHNlbGYucm9vdCwgbmFtZSkpICE9IHZhbHVlOgogICAgICAgICAgICAgICAgcmFpc2UgVmFsdWVFcnJvcignUmVzdG9yZWQgY2F0YWxvZyBndWFyZCBkaWZmZXJzJykKCiAgICBkZWYgYWRtaXRfcm9sbGJhY2soc2VsZik6CiAgICAgICAgZm9yIHJvdyBpbiBzZWxmLnJlY29yZFsnZmlsZXMnXToKICAgICAgICAgICAgcCA9IHNhZmUoc2VsZi5yb290LCByb3dbJ3BhdGgnXSkKICAgICAgICAgICAgdGVtcCA9IHNlbGYudGVtcF9wYXRoKHApCiAgICAgICAgICAgIGlmIG9zLnBhdGgubGV4aXN0cyhzdHIodGVtcCkpOgogICAgICAgICAgICAgICAgcmVndWxhcih0ZW1wKSAgIyBSZWZ1c2UgbGlua3MsIHNwZWNpYWwgZmlsZXMgYW5kIG92ZXJzaXplZCBzdGFnaW5nIGFydGlmYWN0cy4KICAgICAgICAgICAgICAgIGlmIHJvd1sncGF0aCddIG5vdCBpbiAoc2VsZi5yZWNvcmQuZ2V0KCdwZW5kaW5nJyksIHNlbGYucmVjb3JkLmdldCgncGVuZGluZ19yZXN0b3JlJykpIGFuZCBmaWxlX2hhc2godGVtcCkgbm90IGluIChyb3dbJ2JlZm9yZV9zaGEyNTYnXSwgcm93WydzaGEyNTYnXSk6CiAgICAgICAgICAgICAgICAgICAgcmFpc2UgVmFsdWVFcnJvcignVW5leHBlY3RlZCBpbmNvbXBsZXRlIHN0YWdpbmcgZmlsZScpCiAgICAgICAgICAgIGlmIGZpbGVfaGFzaChwKSBub3QgaW4gKHJvd1snYmVmb3JlX3NoYTI1NiddLCByb3dbJ3NoYTI1NiddKToKICAgICAgICAgICAgICAgIHJhaXNlIFZhbHVlRXJyb3IoJ1JvbGxiYWNrIHJlZnVzZXMgYW4gZWRpdGVkIG9yIHVuZXhwZWN0ZWQgdGFyZ2V0OiAnICsgcm93WydwYXRoJ10pCiAgICAgICAgICAgIGN1cnJlbnQgPSBmaWxlX2hhc2gocCkKICAgICAgICAgICAgY2FuZGlkYXRlcyA9IFtdCiAgICAgICAgICAgIGlmIGN1cnJlbnQgPT0gcm93WydzaGEyNTYnXTogY2FuZGlkYXRlcy5hcHBlbmQocm93WydpbnN0YWxsZWRfbWV0YWRhdGEnXSkKICAgICAgICAgICAgaWYgY3VycmVudCA9PSByb3dbJ2JlZm9yZV9zaGEyNTYnXTogY2FuZGlkYXRlcy5hcHBlbmQocm93WydtZXRhZGF0YSddKQogICAgICAgICAgICBpZiBjdXJyZW50IGlzIG5vdCBOb25lOgogICAgICAgICAgICAgICAgb2JzZXJ2ZWQgPSBtZXRhZGF0YShwKQogICAgICAgICAgICAgICAgaWYgbm90IGFueShtIGlzIG5vdCBOb25lIGFuZCBhbGwob2JzZXJ2ZWRba10gPT0gbVtrXSBmb3IgayBpbiBbJ21vZGUnLCAndWlkJywgJ2dpZCcsICdtdGltZV9ucycsICd4YXR0cnMnXSkgZm9yIG0gaW4gY2FuZGlkYXRlcyk6CiAgICAgICAgICAgICAgICAgICAgcmFpc2UgVmFsdWVFcnJvcignUm9sbGJhY2sgcmVmdXNlcyBjaGFuZ2VkIHRhcmdldCBtZXRhZGF0YTogJyArIHJvd1sncGF0aCddKQogICAgICAgICAgICBpZiByb3dbJ29yaWdpbmFsJ10gaXMgbm90IE5vbmUgYW5kIGRpZ2VzdChiYXNlNjQuYjY0ZGVjb2RlKHJvd1snb3JpZ2luYWwnXSkpICE9IHJvd1snYmVmb3JlX3NoYTI1NiddOgogICAgICAgICAgICAgICAgcmFpc2UgVmFsdWVFcnJvcignQmFja3VwIG9yaWdpbmFsIGNvcnJ1cHQnKQogICAgICAgIGZvciBuYW1lLCB2YWx1ZSBpbiBzZWxmLnJlY29yZFsnZ3VhcmRzJ10uaXRlbXMoKToKICAgICAgICAgICAgaWYgZmlsZV9oYXNoKHNhZmUoc2VsZi5yb290LCBuYW1lKSkgIT0gdmFsdWU6CiAgICAgICAgICAgICAgICByYWlzZSBWYWx1ZUVycm9yKCdSb2xsYmFjayBjYXRhbG9nIGd1YXJkIGRpZmZlcnMnKQoKICAgIGRlZiByb2xsYmFjayhzZWxmLCBjaGVja3BvaW50PWxhbWJkYSBzdGFnZSwgbmFtZTogTm9uZSk6CiAgICAgICAgc2VsZi5hZG1pdF9yb2xsYmFjaygpCiAgICAgICAgc2VsZi5yZWNvcmRbJ3BoYXNlJ10gPSAncmVzdG9yaW5nJwogICAgICAgIHNlbGYuc2F2ZSgpCiAgICAgICAgZm9yIHJvdyBpbiByZXZlcnNlZChzZWxmLnJlY29yZFsnZmlsZXMnXSk6CiAgICAgICAgICAgIHAgPSBzYWZlKHNlbGYucm9vdCwgcm93WydwYXRoJ10pCiAgICAgICAgICAgIHRlbXAgPSBzZWxmLnRlbXBfcGF0aChwKQogICAgICAgICAgICBpZiBvcy5wYXRoLmxleGlzdHMoc3RyKHRlbXApKToKICAgICAgICAgICAgICAgIHRlbXAudW5saW5rKCkKICAgICAgICAgICAgICAgIHN5bmNfZGlyKHRlbXAucGFyZW50KQogICAgICAgICAgICBjdXJyZW50ID0gZmlsZV9oYXNoKHApCiAgICAgICAgICAgIGlmIGN1cnJlbnQgPT0gcm93WydzaGEyNTYnXToKICAgICAgICAgICAgICAgIHNlbGYucmVjb3JkWydwZW5kaW5nX3Jlc3RvcmUnXSA9IHJvd1sncGF0aCddCiAgICAgICAgICAgICAgICBzZWxmLnNhdmUoKQogICAgICAgICAgICAgICAgY2hlY2twb2ludCgnYmVmb3JlX3Jlc3RvcmUnLCByb3dbJ3BhdGgnXSkKICAgICAgICAgICAgICAgIGlmIHJvd1snb3JpZ2luYWwnXSBpcyBOb25lOgogICAgICAgICAgICAgICAgICAgIHAudW5saW5rKCkKICAgICAgICAgICAgICAgICAgICBzeW5jX2RpcihwLnBhcmVudCkKICAgICAgICAgICAgICAgIGVsc2U6CiAgICAgICAgICAgICAgICAgICAgYXRvbWljKHAsIGJhc2U2NC5iNjRkZWNvZGUocm93WydvcmlnaW5hbCddKSwgcm93WydtZXRhZGF0YSddLCBzZWxmLnRlbXBfcGF0aChwKSkKICAgICAgICAgICAgICAgIGNoZWNrcG9pbnQoJ2FmdGVyX3Jlc3RvcmUnLCByb3dbJ3BhdGgnXSkKICAgICAgICAgICAgICAgIHNlbGYucmVjb3JkWydwZW5kaW5nX3Jlc3RvcmUnXSA9IE5vbmUKICAgICAgICAgICAgICAgIHNlbGYuc2F2ZSgpCiAgICAgICAgZm9yIG5hbWUgaW4gcmV2ZXJzZWQoc2VsZi5yZWNvcmRbJ2NyZWF0ZWRfZGlycyddKToKICAgICAgICAgICAgcGF0aCA9IHNhZmUoc2VsZi5yb290LCBuYW1lKQogICAgICAgICAgICB0cnk6CiAgICAgICAgICAgICAgICBwYXRoLnJtZGlyKCkKICAgICAgICAgICAgICAgIHN5bmNfZGlyKHBhdGgucGFyZW50KQogICAgICAgICAgICBleGNlcHQgT1NFcnJvciBhcyBlOgogICAgICAgICAgICAgICAgaWYgZS5lcnJubyBub3QgaW4gKGVycm5vLkVOT0VOVCwgZXJybm8uRU5PVEVNUFRZKToKICAgICAgICAgICAgICAgICAgICByYWlzZQogICAgICAgIHNlbGYudmVyaWZ5X3Jlc3RvcmVkKCkKICAgICAgICBzZWxmLnJlY29yZFsncGVuZGluZyddID0gTm9uZQogICAgICAgIHNlbGYucmVjb3JkWydwaGFzZSddID0gJ3JvbGxlZC1iYWNrJwogICAgICAgIHNlbGYuc2F2ZSgpCiAgICAgICAgcmV0dXJuICdyb2xsZWQtYmFjaycK'

def recovery_transaction():
    """Exact signed/pinned legacy engine, with prepare disabled in recovery."""
    import types
    source=base64.b64decode(RECOVERY_TRANSACTION_SOURCE,validate=True)
    assert sha(source)==TRANSACTION_SHA
    contract=types.ModuleType('fix_contract')
    def refuse_prepare(manifest):raise ValueError('The independent recovery engine cannot prepare new manifests')
    contract.validate_manifest=refuse_prepare
    prior=sys.modules.get('fix_contract');sys.modules['fix_contract']=contract
    module=types.ModuleType('hector_fixed_component_recovery')
    try:exec(compile(source,'<signed-original-component-transaction>','exec'),module.__dict__)
    finally:
        if prior is None:sys.modules.pop('fix_contract',None)
        else:sys.modules['fix_contract']=prior
    return module

def recover_initial(root=Path('/'),lower=Path('/overlay/lower'),write_window=None):
    import hmac
    path=DATA/'initial-intent.json'
    if not path.exists():return None
    key=protected_record(DATA/'transaction-key',128);assert len(key)==32
    envelope=json.loads(protected_record(path));intent=envelope['content']
    assert hmac.compare_digest(envelope['hmac'],hmac.new(key,canonical(intent),hashlib.sha256).hexdigest())
    assert set(intent)=={'schema','identity','manifest','proof','initial_state','installation_record'} and intent['schema']==1
    identity={n:sha((root/p).read_bytes()) for n,p in [('card','sys/block/mmcblk0/device/cid'),('device','pedal_state/hardware_info.json')]}
    assert intent['identity']==identity
    proof=base64.b64decode(intent['proof'],validate=True);hashes=verified_proof(proof)
    hashes['usr/local/lib/hector-flash-rx-fix/publisher-proof.json']=sha(proof)
    manifest=intent['manifest'];assert manifest['version']==VERSION and tuple(r['path'] for r in manifest['files'])==REPAIR_FILES
    assert set(hashes)==set(REPAIR_FILES) and manifest['require_absent']==list(REPAIR_FILES)
    assert all(r['before_sha256'] is None and r['sha256']==hashes[r['path']] for r in manifest['files'])
    assert set(manifest['guards'])=={'home/debian/UI/show_widget.py', 'usr/bin/start_beebo.sh', 'usr/lib/modules/5.4.2-rt5-001/kernel/fs/jffs2/jffs2.ko', 'etc/fstab'}
    module=recovery_transaction();tx=module.Transaction(lower,DATA/'transaction',key)
    if not (DATA/'transaction/journal.json').exists():
        assert all(not (lower/n).exists() for n in REPAIR_FILES);return 'not-started'
    record=tx.load();assert record['identity']==identity and record['package_digest']==sha(canonical(manifest))
    assert tuple(r['path'] for r in record['files'])==REPAIR_FILES and record['guards']==manifest['guards'] and record['guard_metadata']==manifest['guard_metadata']
    assert all(r['before_sha256'] is None and r['original'] is None and r['sha256']==hashes[r['path']] for r in record['files'])
    current=snapshot(DATA/'state.json')
    if record['phase']=='committed':
        if current.get('status')!='staging':
            # A cut may follow the state write but precede the discovery record.
            # Only owned support files are checked here; a later app may own GUI.
            if not (DATA/'installation-record.json').exists():
                assert current['version']==VERSION and current['module_sha256']==MODULE_SHA
                for row in record['files']:
                    assert module.file_hash(lower/row['path'])==row['sha256']
                    assert all(module.metadata(lower/row['path'])[k]==row['installed_metadata'][k] for k in ('mode','uid','gid','mtime_ns','xattrs'))
                atomic(DATA/'installation-record.json',intent['installation_record'])
            return None
        assert current==intent['initial_state'] and not current['enabled'] and current.get('pending_boot') is None
        tx.verify_installed();value=dict(current,enabled=True,status='ready-for-first-boot');put_state(value)
        atomic(DATA/'installation-record.json',intent['installation_record']);return 'initial-finalized'
    assert record['phase'] in ('prepared','publishing','restoring','rolled-back')
    assert not current['enabled'] and current.get('pending_boot') is None and current['status'] in ('staging','deployment-restored')
    tx.admit_rollback()
    if record['phase']=='rolled-back':tx.verify_restored();return 'initial-restored-stock'
    if write_window is None:
        assert driver()==STOCK;no_flash_mounts();command(['/bin/mount','-o','remount,rw',str(lower)])
    try:
        tx.rollback();put_state(dict(current,enabled=False,status='deployment-restored'));tx.verify_restored()
    finally:
        if write_window is None:
            os.sync();command(['/bin/mount','-o','remount,ro',str(lower)])
    return 'initial-restored-stock'

def upgrade_context(root=Path('/'),lower=Path('/overlay/lower')):
    import hmac,importlib.util
    folder=DATA/UPGRADE
    assert not (folder/'intent.json').exists(),'Legacy migration requires separate reviewed admission'
    return None
    if not (folder/'intent.json').exists():return None
    key=protected_record(DATA/'transaction-key',128);assert len(key)==32
    envelope=json.loads(protected_record(folder/'intent.json'))
    intent=envelope['content'];assert hmac.compare_digest(envelope['hmac'],hmac.new(key,canonical(intent),hashlib.sha256).hexdigest())
    assert set(intent)=={'schema','identity','manifest','old_state','new_state','old_proof','new_proof','old_installation_record','new_installation_record'} and intent['schema']==1
    identity={n:sha((root/p).read_bytes()) for n,p in [('card','sys/block/mmcblk0/device/cid'),('device','pedal_state/hardware_info.json')]}
    assert intent['identity']==identity
    old_proof=base64.b64decode(intent['old_proof'],validate=True);new_proof=base64.b64decode(intent['new_proof'],validate=True)
    assert sha(old_proof)==OLD_PROOF_SHA
    old=verified_proof(old_proof);new=verified_proof(new_proof)
    proof_path='usr/local/lib/hector-flash-rx-fix/publisher-proof.json'
    old[proof_path]=sha(old_proof);new[proof_path]=sha(new_proof)
    assert set(old)==set(new)==set(REPAIR_FILES)
    driver_path='usr/local/lib/hector-flash-rx-fix/'+MODULE+'.ko'
    assert old[driver_path]==new[driver_path]==MODULE_SHA
    manifest=intent['manifest'];assert manifest['version']==VERSION and manifest['migration_from']==OLD_VERSION
    assert manifest['require_absent']==[] and set(manifest['guards'])==({'etc/fstab','usr/bin/start_beebo.sh'}|set(REPAIR_FILES)-set(UPGRADE_FILES))
    assert all(manifest['guards'][n]==old[n]==new[n] for n in set(REPAIR_FILES)-set(UPGRADE_FILES))
    assert tuple(r['path'] for r in manifest['files'])==UPGRADE_FILES
    assert all(r['before_sha256']==old[r['path']] and r['sha256']==new[r['path']] for r in manifest['files'])
    for version,keyname in ((OLD_VERSION,'old_state'),(VERSION,'new_state')):
        value=intent[keyname];assert value['schema']==1 and value['version']==version and value['module_sha256']==MODULE_SHA and type(value['enabled']) is bool
        assert value.get('pending_boot') is None
    assert intent['new_state']['enabled'] and intent['new_state']['status']=='ready-for-first-boot'
    assert intent['old_installation_record']['version']==OLD_VERSION and intent['new_installation_record']['version']==VERSION
    assert intent['old_installation_record']['publisher_proof']==old_proof.decode() and intent['new_installation_record']['publisher_proof']==new_proof.decode()
    assert old['usr/local/lib/hector-flash-rx-fix/transaction.py']==new['usr/local/lib/hector-flash-rx-fix/transaction.py']==TRANSACTION_SHA
    module=recovery_transaction()
    tx=module.Transaction(lower,folder/'transaction',key)
    if not (folder/'transaction/journal.json').exists():
        assert all(sha((lower/n).read_bytes())==h for n,h in old.items())
        assert snapshot(DATA/'state.json')==intent['old_state']
        return intent,None,None
    record=tx.load()
    assert record['identity']==identity and record['package_digest']==sha(canonical(manifest))
    assert tuple(r['path'] for r in record['files'])==UPGRADE_FILES
    for row,expected in zip(record['files'],manifest['files']):
        assert all(row[k]==v for k,v in expected.items() if k!='before_metadata')
        assert sha(base64.b64decode(row['original'],validate=True))==old[row['path']]
    assert record['guards']==manifest['guards'] and record['guard_metadata']==manifest['guard_metadata']
    base=module.Transaction(lower,DATA/'transaction',key);original=base.load()
    assert original['identity']==identity and tuple(r['path'] for r in original['files'])==REPAIR_FILES
    assert all(r['before_sha256'] is None and r['original'] is None and r['sha256']==old[r['path']] for r in original['files'])
    assert all(original['guards'][n]==manifest['guards'][n] for n in ('etc/fstab','usr/bin/start_beebo.sh'))
    return intent,tx,base

def recover_upgrade(root=Path('/'),lower=Path('/overlay/lower'),write_window=None):
    context=upgrade_context(root,lower)
    if context is None:return None
    intent,tx,base=context
    if tx is None:return 'not-started'
    if tx.record['phase']=='committed':
        tx.verify_installed()
        current=snapshot(DATA/'state.json')
        if current['version']==OLD_VERSION:
            assert current==intent['old_state']
            put_state(intent['new_state'])
        else:assert current['version']==VERSION and current['module_sha256']==MODULE_SHA
        record=json.loads(protected_record(DATA/'installation-record.json'))
        assert record in (intent['old_installation_record'],intent['new_installation_record'])
        if record!=intent['new_installation_record']:atomic(DATA/'installation-record.json',intent['new_installation_record'])
        return 'committed'
    if tx.record['phase']=='rolled-back':
        tx.verify_restored();assert snapshot(DATA/'state.json')==intent['old_state']
        return 'restored-old'
    assert tx.record['phase'] in ('prepared','publishing','restoring')
    tx.admit_rollback()
    # Callers may supply an already guarded write window. The standalone boot
    # path owns its normal pre-NOR remount and never rebinds a live controller.
    if write_window is None:
        assert driver()==STOCK;no_flash_mounts()
        command(['/bin/mount','-o','remount,rw',str(lower)])
    try:
        tx.rollback();put_state(intent['old_state']);atomic(DATA/'installation-record.json',intent['old_installation_record']);tx.verify_restored()
    finally:
        if write_window is None:
            os.sync();command(['/bin/mount','-o','remount,ro',str(lower)])
    return 'restored-old'

# Full removal keeps the fix service/NOR edge until every executable target is
# stock. Once boot.py is absent, the signed unit's ConditionPathExists skips it.
REMOVAL='removal-rx2'
REMOVAL_LAST=('usr/local/lib/hector-flash-rx-fix/boot.py','etc/systemd/system/nor_flash.mount.d/96-flash-rx-fix.conf','etc/systemd/system/hector-flash-rx-fix.service')
REMOVAL_ORDER=tuple(n for n in reversed(REPAIR_FILES) if n not in REMOVAL_LAST)+REMOVAL_LAST

def late_quiescent():
    for name in ('fix','guard','confirm','fallback'):
        raw=command(['/bin/systemctl','show','hector-flash-rx-'+name+'.service','--property=LoadState,ActiveState,SubState,Result,MainPID,ControlPID,Job,ExecMainCode,ExecMainStatus,ExecMainStartTimestampMonotonic'])
        row=dict(line.split('=',1) for line in raw.splitlines() if '=' in line)
        assert row['LoadState']=='loaded' and row['Result']=='success' and row['MainPID']==row['ControlPID']=='0' and row['Job'] in ('','0')
        assert row['ActiveState']==('active' if name=='fix' else 'inactive')
        if name in ('fix','confirm'):assert row['ExecMainCode']=='1' and row['ExecMainStatus']=='0' and int(row['ExecMainStartTimestampMonotonic'])>0
        if name=='fallback':assert row['ExecMainStartTimestampMonotonic']=='0'
    value=state();status=snapshot(RUN/'status.json');receipt=snapshot(RUN/'guard-complete.json')
    assert value.get('pending_boot') is None and status['boot']==receipt['boot']==boot_id() and receipt.get('watchdog_disarmed') is True
    if driver()==FIX:
        assert value['enabled'] and value.get('confirmed_boot')==boot_id()
        assert status.get('confirmed') is True and status['status']=='active'
        assert receipt.get('confirmed') is True and receipt.get('fallback') is False
    else:
        # Component-only recovery changes SD files and never reads NOR. A
        # positive fallback/disarmed receipt is required; missing watchdog
        # sysfs state or a never-started/default service is not disarm proof.
        assert driver()==STOCK and not value['enabled'] and status['status']=='fallback-stock'
        assert receipt.get('fallback') is True and receipt.get('confirmed') is False

def removal_record(intent,token):
    base=intent['base_record'];rows=[]
    for name in reversed(REMOVAL_ORDER):
        row=next(r for r in base['files'] if r['path']==name)
        rows.append(dict(row,sha256=intent['installed_hashes'][name],size=intent['installed_sizes'][name],installed_metadata=intent['installed_metadata'][name]))
    return dict(schema=1,version=VERSION,identity=intent['identity'],boot=intent['boot'],phase='committed',
        files=rows,guards=base['guards'],guard_metadata=base['guard_metadata'],applied=list(REMOVAL_ORDER),pending=None,
        created_dirs=base['created_dirs'],services={},id=token,pending_restore=None,package_digest=sha(canonical(intent)),
        purpose='authenticated-full-component-removal')

def removal_context(root=Path('/'),lower=Path('/overlay/lower')):
    import hmac,importlib.util
    folder=DATA/REMOVAL
    if not (folder/'intent.json').exists():return None
    key=protected_record(DATA/'transaction-key',128);assert len(key)==32
    envelope=json.loads(protected_record(folder/'intent.json',2*1024*1024));intent=envelope['content']
    assert hmac.compare_digest(envelope['hmac'],hmac.new(key,canonical(intent),hashlib.sha256).hexdigest())
    assert intent['schema']==1 and intent['version']==VERSION
    identity={n:sha((root/p).read_bytes()) for n,p in [('card','sys/block/mmcblk0/device/cid'),('device','pedal_state/hardware_info.json')]}
    assert intent['identity']==identity
    proof_raw=base64.b64decode(intent['installed_proof'],validate=True);hashes=verified_proof(proof_raw)
    hashes['usr/local/lib/hector-flash-rx-fix/publisher-proof.json']=sha(proof_raw)
    base_proof=base64.b64decode(intent['base_proof'],validate=True)
    assert sha(base_proof) in (OLD_PROOF_SHA,sha(proof_raw))
    old=verified_proof(base_proof);old['usr/local/lib/hector-flash-rx-fix/publisher-proof.json']=sha(base_proof)
    assert set(hashes)==set(old)==set(REPAIR_FILES) and hashes==intent['installed_hashes']
    assert hashes['usr/local/lib/hector-flash-rx-fix/'+MODULE+'.ko']==MODULE_SHA
    assert hashes['usr/local/lib/hector-flash-rx-fix/transaction.py']==TRANSACTION_SHA
    module=recovery_transaction()
    base=module.Transaction(lower,DATA/'transaction',key);base.load()
    original=intent['base_record']
    assert original['identity']==base.record['identity']==identity and tuple(r['path'] for r in original['files'])==REPAIR_FILES
    assert all(r['before_sha256'] is None and r['original'] is None and r['sha256']==old[r['path']] for r in original['files'])
    for field in ('files','guards','guard_metadata','identity','package_digest','created_dirs'):
        assert original[field]==base.record[field]
    assert set(original['guards'])=={'home/debian/UI/show_widget.py', 'usr/bin/start_beebo.sh', 'usr/lib/modules/5.4.2-rt5-001/kernel/fs/jffs2/jffs2.ko', 'etc/fstab'}
    if intent['upgrade_intent_sha256'] is not None:
        assert sha(protected_record(DATA/UPGRADE/'intent.json'))==intent['upgrade_intent_sha256']
    expected=removal_record(intent,'transaction')
    tx=module.Transaction(lower,folder/'transaction',key)
    if (folder/'transaction/journal.json').exists():
        tx.load()
        for field in ('files','guards','guard_metadata','identity','package_digest','created_dirs','purpose'):
            assert tx.record[field]==expected[field]
    else:
        # No target is touched until this authenticated derived journal exists.
        tx.record=expected;tx.verify_installed();tx.save()
    return intent,tx,base

def recover_removal(root=Path('/'),lower=Path('/overlay/lower'),write_window=None,checkpoint=lambda stage,name:None):
    context=removal_context(root,lower)
    if context is None:return None
    intent,tx,base=context
    tx.admit_rollback()
    if tx.record['phase']=='rolled-back':tx.verify_restored();return 'stock-restored'
    if write_window is None:
        assert driver()==STOCK;no_flash_mounts()
        command(['/bin/mount','-o','remount,rw',str(lower)])
    try:
        value=dict(intent['state_before'],enabled=False,pending_boot=None,status='removed')
        put_state(value);tx.rollback(checkpoint);tx.verify_restored()
    finally:
        if write_window is None:
            os.sync();command(['/bin/mount','-o','remount,ro',str(lower)])
    return 'stock-restored'

def begin_removal(root=Path('/'),lower=Path('/overlay/lower'),checkpoint=lambda stage,name:None):
    import hmac,importlib.util
    assert os.geteuid()==0
    late_quiescent();verify_release(root=lower)
    folder=DATA/REMOVAL
    if (folder/'intent.json').exists():return recover_removal(root,lower)
    proof_raw=(BASE/'publisher-proof.json').read_bytes();hashes=verified_proof(proof_raw)
    hashes['usr/local/lib/hector-flash-rx-fix/publisher-proof.json']=sha(proof_raw)
    assert set(hashes)==set(REPAIR_FILES)
    # Existing upgrade context authenticates the original stock lineage before
    # a derived journal can select any restoration byte or metadata.
    context=upgrade_context(root,lower)
    if context:
        prior,upgrade,base=context;assert upgrade is not None and upgrade.record['phase']=='committed';upgrade.verify_installed()
        base_proof=base64.b64decode(prior['old_proof']);upgrade_digest=sha(protected_record(DATA/UPGRADE/'intent.json'))
        module=recovery_transaction()
    else:
        module=recovery_transaction()
        base=module.Transaction(lower,DATA/'transaction',protected_record(DATA/'transaction-key',128));base.load();base.verify_installed()
        base_proof=proof_raw;upgrade_digest=None
    assert base.record['phase']=='committed'
    # Full platform/runtime rollback must have restored these original stock
    # guards. Refuse BEFORE creating/removing anything when GUI lineage differs.
    for name,digest in base.record['guards'].items():
        assert module.file_hash(lower/name)==digest
        assert all(module.metadata(lower/name)[k]==base.record['guard_metadata'][name][k] for k in ('mode','uid','gid','mtime_ns','xattrs'))
    intent=dict(schema=1,version=VERSION,identity=base.record['identity'],boot=boot_id(),base_record=base.record,
        base_proof=base64.b64encode(base_proof).decode(),upgrade_intent_sha256=upgrade_digest,
        installed_proof=base64.b64encode(proof_raw).decode(),installed_hashes=hashes,
        installed_sizes={n:(lower/n).stat().st_size for n in REPAIR_FILES},installed_metadata={n:module.metadata(lower/n) for n in REPAIR_FILES},state_before=state())
    folder.mkdir(mode=0o700,exist_ok=True);(folder/'transaction').mkdir(mode=0o700,exist_ok=True)
    key=protected_record(DATA/'transaction-key',128)
    module.atomic(folder/'intent.json',canonical(dict(content=intent,hmac=hmac.new(key,canonical(intent),hashlib.sha256).hexdigest())))
    # This root-owned journal is a NEW plan derived from the authenticated stock
    # originals and current signed generation; no historical manifest is edited.
    context=removal_context(root,lower);assert context is not None
    command(['/bin/mount','-o','remount,rw',str(lower)])
    try:result=recover_removal(root,lower,write_window=True,checkpoint=checkpoint)
    finally:
        os.sync()
        try:command(['/bin/mount','-o','remount,ro',str(lower)])
        except Exception:pass # Ordinary reboot closes the known overlay EBUSY.
    command(['/bin/systemctl','daemon-reload']);command(['/bin/systemctl','--no-block','reboot'])
    return result

def close_removal(root=Path('/'),lower=Path('/overlay/lower')):
    context=removal_context(root,lower);assert context is not None
    intent,tx,base=context
    assert boot_id()!=intent['boot'] and driver()==STOCK and tx.record['phase']=='rolled-back'
    tx.verify_restored()
    assert all(not (lower/n).exists() and not (root/n).exists() for n in REPAIR_FILES)
    # Standard original transaction closeout now observes its stock preimages;
    # it performs no firmware writes, and preserves all old backup/identity data.
    base.admit_rollback();base.verify_restored();base.rollback()
    atomic(DATA/REMOVAL/'verified.json',dict(status='stock-restored-verified',boot=boot_id(),base_package=base.record['package_digest'],removal_package=tx.record['package_digest'],upgrade_retired_by_full_restore=intent['upgrade_intent_sha256']))
    return dict(status='stock-restored-verified',boot=boot_id())

def preflight():
    assert os.geteuid()==0 and platform.machine()=='aarch64' and platform.release()=='5.4.2-rt5-001'
    assert sha(gzip.decompress(Path('/proc/config.gz').read_bytes()))==CONFIG_SHA
    assert (CONTROLLER/'subsystem').resolve().name=='platform'
    assert DATA.is_dir() and DATA.stat().st_uid==0 and DATA.stat().st_mode&0o077==0
    assert '/dev/mmcblk0p3' in command(['/usr/bin/findmnt','-n','-o','SOURCE','/mnt'])

def boot_policy(v,current):
    """An unconfirmed prior attempt disables activation, including power loss."""
    v=dict(v)
    if v.get('pending_boot') and v['pending_boot']!=current:
        v.update(enabled=False,status='disabled-after-unconfirmed-boot',failure='Previous activation did not confirm',pending_boot=None)
    return v

def guard_decision(v,current,current_driver,status,current_filesystem):
    confirmed=v.get('confirmed_boot')==current and v.get('pending_boot') is None and current_driver==FIX and status=='active' and current_filesystem=='fixed'
    fallback=not v['enabled'] and current_driver==STOCK and status=='fallback-stock' and current_filesystem=='stock'
    return confirmed,fallback

def timeout_policy(v,current,healthy=False):
    v=dict(v)
    if healthy and v.get('confirmed_boot')==current and v.get('pending_boot') is None:return v
    v.update(enabled=False,status='boot-guardian-timeout',failure='Boot activation did not confirm before deadline',pending_boot=None)
    return v

def probe(turns=96):
    offsets=[0xd38a0,0xfc9414,0xc8baf0,0xe5054c,0x33e28,0x28400c,0x9da248,0xf90390,0x5d4418,0x323ac,0xf4f310]
    fd=os.open('/dev/mtd0ro',os.O_RDONLY|os.O_CLOEXEC);count=0
    try:
        refs={}
        for off in offsets:
            raw=[os.pread(fd,4096,off) for unused in range(3)]
            assert len(raw[0])==4096 and raw[0]==raw[1]==raw[2],'Long reference disagreement'
            refs[off]=raw[0]
        for turn in range(turns):
            for off in offsets:
                for length in (68,72,128):
                    assert os.pread(fd,length,off)==refs[off][:length],'Receive comparison failed'
                    count+=1
    finally:os.close(fd)
    return dict(reads=count,mismatches=0)


STORAGE_UPGRADE_SOURCE=b'# SPDX-License-Identifier: MIT\n"""Signed paired-storage upgrade engine. SD files only; no NOR/driver operations."""\nimport base64,hashlib,hmac,json,os,stat,subprocess,time,tempfile,zlib\nfrom pathlib import Path\n\ndef canonical(v):return json.dumps(v,sort_keys=True,separators=(\',\',\':\')).encode()\n\ndef sha(b):return hashlib.sha256(b).hexdigest()\n\ndef command(a,timeout=20):return subprocess.run(a,check=True,capture_output=True,text=True,timeout=timeout).stdout.strip()\n\ndef boot_id():return Path(\'/proc/sys/kernel/random/boot_id\').read_text().strip()\n\ndef put(tr,key,path,value):tr.atomic(path,canonical(dict(content=value,hmac=hmac.new(key,canonical(value),hashlib.sha256).hexdigest())))\n\ndef get(tr,key,path):\n e=json.loads(tr.regular(path,16*1024*1024));v=e[\'content\'];assert hmac.compare_digest(e[\'hmac\'],hmac.new(key,canonical(v),hashlib.sha256).hexdigest());return v\n\ndef state(tr):return json.loads(tr.regular(DATA/\'state.json\',4096))\n\ndef readonly(value):\n try:\n  command([\'/bin/mount\',\'-o\',\'remount,\'+(\'ro\' if value else \'rw\'),str(ROOT)])\n  return True\n except subprocess.CalledProcessError as exc:\n  # Overlayfs can retain write references to replaced lower inodes. A fully\n  # authenticated file handoff must still reboot, rather than stop halfway.\n  if value and exc.returncode==32 and \'busy\' in (exc.stderr or \'\').lower():\n   return False\n  raise\n\ndef configure(boot,root=Path(\'/\'),lower=Path(\'/overlay/lower\')):\n global B,ROOT,HOST,DATA,WORK,BASE,NEW_FILES\n B=boot;ROOT=Path(lower);HOST=Path(root);DATA=HOST/\'mnt/community-lab/hector-flash-rx-fix\';WORK=DATA/\'upgrade-storage-v1\';BASE=\'usr/local/lib/hector-flash-rx-fix/\';NEW_FILES={}\n\ndef identity():\n return {n:sha((HOST/p).read_bytes()) for n,p in [(\'card\',\'sys/block/mmcblk0/device/cid\'),(\'device\',\'pedal_state/hardware_info.json\')]}\n\ndef key_read(tr):\n p=DATA/\'transaction-key\';st=p.lstat();assert stat.S_ISREG(st.st_mode) and st.st_uid==os.geteuid() and not st.st_mode&0o077\n key=tr.regular(p,128);assert len(key)==32;return key\n\ndef proof_hashes(proof):\n values=B.verified_proof(proof);values=dict(values,**{BASE+\'publisher-proof.json\':sha(proof)});return values\n\ndef checked_new(proof):\n values=proof_hashes(proof);assert set(values)==set(B.REPAIR_FILES)\n assert values[BASE+\'hector_sun6i_rx_fix.ko\']==B.MODULE_SHA\n assert values[BASE+\'jffs2-preserve-read-errors.ko\']==B.filesystem_support().CANDIDATE_SHA\n return {n:values[n] for n in B.REPAIR_FILES}\n\ndef guards_ok(tr,guards,metadata):\n for n,h in guards.items():\n  assert tr.file_hash(tr.safe(ROOT,n))==h,\'Stable original guard changed: \'+n\n  actual=tr.metadata(tr.safe(ROOT,n));assert all(actual[k]==metadata[n][k] for k in (\'mode\',\'uid\',\'gid\',\'mtime_ns\',\'xattrs\'))\n\ndef verify_rows(tr,tx):\n for row in tx.record[\'files\']:\n  p=tr.safe(ROOT,row[\'path\']);assert tr.file_hash(p)==row[\'sha256\']\n  actual=tr.metadata(p);assert all(actual[k]==row[\'installed_metadata\'][k] for k in (\'mode\',\'uid\',\'gid\',\'mtime_ns\',\'xattrs\'))\n guards_ok(tr,tx.record[\'guards\'],tx.record[\'guard_metadata\'])\n\ndef bridge_rows(value):\n if not value:return []\n proof=json.loads(base64.b64decode(value[\'proof\'],validate=True));message=base64.b64decode(proof[\'manifest\'],validate=True);signature=base64.b64decode(proof[\'signature\'],validate=True)\n signed=json.loads(message)\n assert message==canonical(signed) and signed[\'schema\']==\'poly-view-publisher/1\' and signed[\'key\']==sha(B.PUBLIC_DER)\n assert signed[\'module\'] in (\'poly-view-module-runtime\',\'network-connection-poly-view\') and signed[\'target\']==\'hector-407-aarch64\'\n with tempfile.TemporaryDirectory(prefix=\'storage-bridge-proof-\',dir=\'/run\') as temp:\n  d=Path(temp);(d/\'key\').write_bytes(B.PUBLIC_DER);(d/\'message\').write_bytes(message);(d/\'signature\').write_bytes(signature)\n  B.command([\'/usr/bin/openssl\',\'pkeyutl\',\'-verify\',\'-pubin\',\'-keyform\',\'DER\',\'-inkey\',str(d/\'key\'),\'-rawin\',\'-in\',str(d/\'message\'),\'-sigfile\',str(d/\'signature\')])\n packed=base64.b64decode(value[\'platform_z\'],validate=True);assert len(packed)<=8*1024*1024+4096\n dec=zlib.decompressobj();raw=dec.decompress(packed,8*1024*1024+1);assert len(raw)<=8*1024*1024 and dec.eof and not dec.unused_data\n assert sha(raw)==signed[\'files\'][\'platform.json\']\n info=json.loads(raw);assert info[\'schema\']==\'poly-view-hector-platform/2\' and info[\'id\']==signed[\'module\']\n profiles=[m for m in info[\'profiles\'] if sha(canonical(m))==value[\'profile_sha256\']];assert len(profiles)==1\n profile=profiles[0];rows={r[\'path\']:r for r in profile[\'files\']};result=[]\n for name in BRIDGE_NAMES:\n  assert name in rows\n  row=rows[name];assert signed[\'files\'][\'payload/\'+row[\'sha256\']]==row[\'sha256\']\n  result.append(dict(path=name,before_sha256=row[\'before_sha256\'],sha256=row[\'sha256\'],size=row[\'size\']))\n return result\n\ndef load_current(tr,key):\n global NEW_FILES\n pointer=get(tr,key,WORK/\'active.json\');assert set(pointer)=={\'schema\',\'attempt\',\'phase\',\'trial_boot\'} and pointer[\'schema\']==1\n assert pointer[\'phase\'] in (\'publishing\',\'ready\',\'accepted\',\'rollback-requested\',\'restored\')\n name=pointer[\'attempt\'];assert name.startswith(\'attempt-\') and name[8:].isdigit()\n folder=WORK/name;assert folder.is_dir() and not folder.is_symlink()\n intent=get(tr,key,folder/\'intent.json\');assert intent[\'schema\']==1 and intent[\'identity\']==identity()\n old_version=intent[\'old_state\'][\'version\'];assert old_version in OLD_PROFILES\n old=proof_hashes(base64.b64decode(intent[\'old_proof\'],validate=True));assert old==OLD_PROFILES[old_version]\n NEW_FILES=checked_new(base64.b64decode(intent[\'new_proof\'],validate=True))\n manifest=intent[\'update_manifest\'];assert manifest[\'version\']==B.VERSION and manifest[\'migration_from\']==old_version\n extra=bridge_rows(intent.get(\'bridge\'));expected_order=B.REPAIR_FILES+tuple(row[\'path\'] for row in extra)\n assert tuple(r[\'path\'] for r in manifest[\'files\'])==expected_order\n assert manifest[\'require_absent\']==[n for n in B.REPAIR_FILES if n not in old]+[row[\'path\'] for row in extra if row[\'before_sha256\'] is None] and set(manifest[\'guards\'])==set(STABLE)\n\n before=dict(old,**{row[\'path\']:row[\'before_sha256\'] for row in extra});after=dict(NEW_FILES,**{row[\'path\']:row[\'sha256\'] for row in extra})\n assert all(row[\'before_sha256\']==before.get(row[\'path\']) and row[\'sha256\']==after[row[\'path\']] for row in manifest[\'files\'])\n assert manifest[\'guards\']=={n:intent[\'base_manifest\'][\'guards\'][n] for n in STABLE}\n tx=tr.Transaction(ROOT,folder/\'transaction\',key);record=tx.load()\n assert record[\'identity\']==intent[\'identity\'] and record[\'package_digest\']==sha(canonical(manifest))\n assert tuple(row[\'path\'] for row in record[\'files\'])==expected_order\n for row in record[\'files\']:\n  assert row[\'before_sha256\']==before.get(row[\'path\']) and row[\'sha256\']==after[row[\'path\']]\n  assert (row[\'original\'] is None if row[\'before_sha256\'] is None else sha(base64.b64decode(row[\'original\'],validate=True))==row[\'before_sha256\'])\n assert record[\'guards\']==manifest[\'guards\'] and record[\'guard_metadata\']==manifest[\'guard_metadata\']\n return pointer,folder,intent,tx\n\ndef restore(tr,key,pointer,folder,intent,tx,write_window=None):\n if write_window is None:readonly(False)\n try:\n  tx.rollback();tx.verify_restored()\n  for n,item in intent[\'old_controls\'].items():\n   assert n in CONTROL\n   p=DATA/n\n   if item[\'raw\'] is None:\n    if p.exists():p.unlink();tr.sync_dir(p.parent)\n   else:tr.atomic(p,base64.b64decode(item[\'raw\'],validate=True),item[\'metadata\'])\n  pointer[\'phase\']=\'restored\';put(tr,key,WORK/\'active.json\',pointer)\n finally:\n  os.sync()\n  if write_window is None:readonly(True)\n return \'restored-previous-support\'\n\ndef stage(boot,root,lower,payload,checkpoint=lambda stage,name:None,bridge=None):\n global NEW_FILES\n configure(boot,root,lower);tr=B.recovery_transaction();key=key_read(tr)\n if WORK.exists():\n  assert not WORK.is_symlink() and WORK.stat().st_uid==os.geteuid() and not WORK.stat().st_mode&0o077\n  if (WORK/\'active.json\').exists():\n   p,f,i,tx=load_current(tr,key)\n   if p[\'phase\']!=\'restored\':\n    result=recover(boot,root,lower,write_window=True)\n    if result==\'previous-upgrade-healthy\':return dict(action=\'upgrade-reused\',version=B.VERSION)\n    raise ValueError(\'Interrupted paired-support upgrade restored; restart and review before retry\')\n else:WORK.mkdir(mode=0o700)\n old_state=state(tr);old_version=old_state[\'version\'];assert old_version in OLD_PROFILES and old_state[\'enabled\'] and old_state.get(\'pending_boot\') is None\n old_files=OLD_PROFILES[old_version]\n old_payload={n:tr.regular(tr.safe(ROOT,n),256*1024) for n in old_files}\n assert all(sha(old_payload[n])==h for n,h in old_files.items())\n assert proof_hashes(old_payload[BASE+\'publisher-proof.json\'])==old_files\n assert not any((ROOT/n).exists() for n in set(B.REPAIR_FILES)-set(old_files))\n proof=payload[BASE+\'publisher-proof.json\'];NEW_FILES=checked_new(proof)\n extra=bridge_rows(bridge[\'evidence\']) if bridge else []\n if extra:\n  assert set(bridge[\'payload\'])==set(BRIDGE_NAMES)\n  for row in extra:\n   assert sha(bridge[\'payload\'][row[\'path\']])==row[\'sha256\'] and tr.file_hash(ROOT/row[\'path\'])==row[\'before_sha256\']\n assert set(payload)==set(NEW_FILES) and all(sha(payload[n])==h for n,h in NEW_FILES.items())\n original=tr.Transaction(ROOT,DATA/\'transaction\',key);record=original.load()\n assert record[\'phase\']==\'committed\' and record[\'identity\']==identity() and tuple(r[\'path\'] for r in record[\'files\'])==tuple(old_files)\n assert all(r[\'before_sha256\'] is None and r[\'original\'] is None for r in record[\'files\'])\n base_hashes={row[\'path\']:row[\'sha256\'] for row in record[\'files\']}\n if base_hashes!=old_files:\n  assert old_version==\'0.1.0-spi-rx.2\' and base_hashes==OLD_PROFILES[\'0.1.0-spi-rx.1\'],\'Unknown original support lineage\'\n  prior=get(tr,key,DATA/\'upgrade-rx2/intent.json\');assert prior[\'identity\']==identity()\n  assert proof_hashes(base64.b64decode(prior[\'old_proof\'],validate=True))==OLD_PROFILES[\'0.1.0-spi-rx.1\']\n  assert proof_hashes(base64.b64decode(prior[\'new_proof\'],validate=True))==old_files\n  previous=tr.Transaction(ROOT,DATA/\'upgrade-rx2/transaction\',key);lineage=previous.load()\n  assert lineage[\'phase\']==\'committed\' and lineage[\'identity\']==identity() and lineage[\'package_digest\']==sha(canonical(prior[\'manifest\']))\n  assert all(row[\'before_sha256\']==OLD_PROFILES[\'0.1.0-spi-rx.1\'][row[\'path\']] and row[\'sha256\']==old_files[row[\'path\']] for row in lineage[\'files\'])\n  previous.verify_installed()\n\n # GUI ownership can pass to a later signed app; only stable startup/kernel\n # guards must still match the original support transaction at upgrade time.\n for n in STABLE[:2]:guards_ok(tr,{n:record[\'guards\'][n]},{n:record[\'guard_metadata\'][n]})\n assert tr.file_hash(tr.safe(ROOT,STABLE[-1]))==B.filesystem_support().STOCK_SHA\n controls={}\n for n in CONTROL:\n  p=DATA/n\n  if p.exists():controls[n]=dict(raw=base64.b64encode(tr.regular(p,4*1024*1024)).decode(),metadata=tr.metadata(p))\n  else:assert n==\'initial-intent.json\';controls[n]=dict(raw=None,metadata=None)\n guards={n:tr.file_hash(tr.safe(ROOT,n)) for n in BASE_GUARDS};metadata={n:tr.metadata(tr.safe(ROOT,n)) for n in BASE_GUARDS}\n base=dict(schema=\'four-by-four-hector/guarded/1\',hardware=\'Hector\',firmware=\'407\',architecture=\'aarch64\',version=B.VERSION,\n  published_mtime_ns=1700000000000000001,require_absent=list(B.REPAIR_FILES),guards=guards,guard_metadata=metadata,\n  files=[dict(path=n,before_sha256=None,sha256=h,size=len(payload[n])) for n,h in NEW_FILES.items()])\n update=dict(base,migration_from=old_version,require_absent=[n for n in B.REPAIR_FILES if n not in old_files],guards={n:guards[n] for n in STABLE},guard_metadata={n:metadata[n] for n in STABLE},\n  files=[dict(path=n,before_sha256=old_files.get(n),before_metadata=tr.metadata(ROOT/n) if n in old_files else None,sha256=h,size=len(payload[n])) for n,h in NEW_FILES.items()])\n if extra:\n  update[\'files\']+= [dict(row,before_metadata=tr.metadata(ROOT/row[\'path\']) if row[\'before_sha256\'] is not None else None) for row in extra]\n  update[\'require_absent\']+= [row[\'path\'] for row in extra if row[\'before_sha256\'] is None]\n apply_payload=dict(payload,**(bridge[\'payload\'] if bridge else {}))\n new_state=dict(schema=1,version=B.VERSION,module_sha256=B.MODULE_SHA,enabled=True,pending_boot=None,status=\'ready-for-first-boot\')\n new_record=dict(schema=\'hector-signed-system-component/1\',id=\'hector-flash-rx-fix\',version=B.VERSION,api_version=1,\n  publisher=sha(B.PUBLIC_DER),file_manifest=base,independent_of_networking=True,protected_system_component=True,\n  ordered_ports=[],lv2_uri=None,transaction=\'transaction\',publisher_proof=proof.decode())\n initial=dict(schema=1,identity=identity(),manifest=base,proof=base64.b64encode(proof).decode(),initial_state=dict(new_state,enabled=False,status=\'staging\'),installation_record=new_record)\n folder=WORK/(\'attempt-\'+str(time.monotonic_ns()));folder.mkdir(mode=0o700);(folder/\'transaction\').mkdir(mode=0o700)\n expected=canonical(update)\n def validate(m):assert canonical(m)==expected\n tr.validate_manifest=validate;tx=tr.Transaction(ROOT,folder/\'transaction\',key);tx.prepare(update,apply_payload,identity(),boot_id())\n journal=dict(tx.record,version=B.VERSION,id=\'transaction\',phase=\'committed\',package_digest=sha(canonical(base)),guards=guards,guard_metadata=metadata,created_dirs=record[\'created_dirs\'])\n journal[\'files\']=[dict(row,original=None,metadata=None,installed_metadata=tx.record[\'files\'][idx][\'installed_metadata\']) for idx,row in enumerate(base[\'files\'])]\n journal.update(applied=list(B.REPAIR_FILES),pending=None,pending_restore=None)\n intent=dict(schema=1,identity=identity(),update_manifest=update,base_manifest=base,old_controls=controls,old_state=old_state,\n  bridge=bridge[\'evidence\'] if bridge else None,old_proof=base64.b64encode(old_payload[BASE+\'publisher-proof.json\']).decode(),new_proof=base64.b64encode(proof).decode(),\n  new_state=new_state,new_record=new_record,new_initial=initial,new_journal=journal)\n put(tr,key,folder/\'intent.json\',intent);pointer=dict(schema=1,attempt=folder.name,phase=\'publishing\',trial_boot=None);put(tr,key,WORK/\'active.json\',pointer)\n tx.apply(apply_payload,checkpoint);tx.verify_installed()\n for n,v in [(\'initial-intent.json\',initial),(\'installation-record.json\',new_record),(\'transaction/journal.json\',journal),(\'state.json\',new_state)]:\n  checkpoint(\'before-control\',n)\n  if n in (\'initial-intent.json\',\'transaction/journal.json\'):put(tr,key,DATA/n,v)\n  else:tr.atomic(DATA/n,canonical(v))\n  checkpoint(\'after-control\',n)\n pointer[\'phase\']=\'ready\';put(tr,key,WORK/\'active.json\',pointer)\n return dict(action=\'paired-support-upgraded\',version=B.VERSION,previous=old_version,driver_unchanged_until_reboot=True)\n\ndef verify_candidate(tr,key,intent,tx,bridge_released=False):\n if bridge_released:\n  tx.record=dict(tx.record,files=[row for row in tx.record[\'files\'] if row[\'path\'] in B.REPAIR_FILES])\n verify_rows(tr,tx)\n base=tr.Transaction(ROOT,DATA/\'transaction\',key);record=base.load()\n assert record[\'version\']==B.VERSION and record[\'identity\']==identity() and record[\'package_digest\']==sha(canonical(intent[\'base_manifest\']))\n assert tuple(row[\'path\'] for row in record[\'files\'])==B.REPAIR_FILES\n for row in record[\'files\']:\n  p=tr.safe(ROOT,row[\'path\']);assert row[\'before_sha256\'] is None and row[\'original\'] is None and tr.file_hash(p)==NEW_FILES[row[\'path\']]\n assert get(tr,key,DATA/\'initial-intent.json\')==intent[\'new_initial\']\n assert json.loads(tr.regular(DATA/\'installation-record.json\'))==intent[\'new_record\']\n\ndef recover(boot,root=Path(\'/\'),lower=Path(\'/overlay/lower\'),write_window=None):\n configure(boot,root,lower)\n if not (WORK/\'active.json\').exists():return None\n tr=B.recovery_transaction();key=key_read(tr);p,f,i,tx=load_current(tr,key)\n if p[\'phase\']==\'restored\':return \'previous-support-restored\'\n if p[\'phase\'] in (\'publishing\',\'rollback-requested\'):return restore(tr,key,p,f,i,tx,write_window)\n verify_candidate(tr,key,i,tx,bridge_released=p[\'phase\']==\'accepted\');v=state(tr);assert v[\'version\']==B.VERSION and v[\'module_sha256\']==B.MODULE_SHA\n if write_window is not None:return \'previous-upgrade-healthy\'\n current=boot_id()\n if p.get(\'trial_boot\')==current:return \'same-boot-no-mutation\'\n if not v[\'enabled\'] or (v.get(\'pending_boot\') and v[\'pending_boot\']!=current):\n  if p[\'phase\']==\'accepted\':return \'accepted-support-needs-independent-recovery\'\n  return restore(tr,key,p,f,i,tx)\n if p.get(\'trial_boot\'):\n  if v.get(\'confirmed_boot\')!=p[\'trial_boot\'] and p[\'phase\']!=\'accepted\':return restore(tr,key,p,f,i,tx)\n  p[\'phase\']=\'accepted\'\n p[\'trial_boot\']=current;put(tr,key,WORK/\'active.json\',p);return \'candidate-boot-admitted\'\n\nOLD_PROFILES={\'0.1.0-spi-rx.1\': {\'usr/local/lib/hector-flash-rx-fix/boot.py\': \'d5cdeeca7a996eefd6f425c51cb2c9c0b203d10120792c4dd2e8cf2f3a0bca4a\', \'usr/local/lib/hector-flash-rx-fix/guard.py\': \'0b659e032d74645e69a503350eb499c3f47c70517cdfadf9a4d8a3aab173a40f\', \'usr/local/lib/hector-flash-rx-fix/hector_sun6i_rx_fix.ko\': \'1f0d51ae1bfea574ad3c83a8d993f53050c2d818c88a0445b93a96b13ac5ba87\', \'usr/local/lib/hector-flash-rx-fix/fix_contract.py\': \'19d660937a10ad4333043e707cc59d71ecf2a0420e999de18b04179ced9d1c10\', \'usr/local/lib/hector-flash-rx-fix/transaction.py\': \'a487859df8e8884b5eb5d6ba6bbadfc224b8cd2fb5b1c5890a9b4aa6ec8a86ef\', \'usr/local/lib/hector-flash-rx-fix/remove.py\': \'3a05220155c38efd337527fe3300e16dcd321658e64fa34d593b4afd90646155\', \'usr/share/doc/hector-flash-rx-fix/GPL-2.0.txt\': \'f6b78c087c3ebdf0f3c13415070dd480a3f35d8fc76f3d02180a407c1c812f79\', \'usr/share/doc/hector-flash-rx-fix/NOTICE.txt\': \'216833db6c279baed8d87e47795cbde6367a94680adce12ed89bd4abeedb8454\', \'usr/local/lib/hector-flash-rx-fix/publisher-proof.json\': \'f14e82c0d152c7287ef755777b0f6f248652d1c82ed17f364cac169551b9554d\', \'etc/systemd/system/hector-flash-rx-guard.service\': \'3b3afdbfe713adb862b46f5dd8b6e362ee20f77d017d1303faaf28fbcaf0d169\', \'etc/systemd/system/hector-flash-rx-fallback.service\': \'97d2d9e72ea226d1b9c5a6f9788dc5660462a8a58a0d4cb6afdf1e4cce0300e1\', \'etc/systemd/system/hector-flash-rx-confirm.service\': \'7e47abeee1e220cbfcd7126f6b6479a8705ad6a02a5f5ac948e7e057a3e1c39f\', \'etc/systemd/system/hector-flash-rx-fix.service\': \'700620a27511ec60d3d2c358e031fbfd7a18f9cb0491f3fbe960b673f42d7697\', \'etc/systemd/system/multi-user.target.d/96-flash-rx-fix.conf\': \'b3786db6c44a2b64259baaddccaccb69f05231b53d68851ab7b1e315ec0b877d\', \'etc/systemd/system/jack.service.d/96-flash-rx-fix.conf\': \'d5064b0568b6f00f3585bfc7cfe8ceffd4fcd0d2ee4a4918e0813d13d16883fa\', \'etc/systemd/system/lightdm.service.d/96-flash-rx-fix.conf\': \'d5064b0568b6f00f3585bfc7cfe8ceffd4fcd0d2ee4a4918e0813d13d16883fa\', \'etc/systemd/system/nor_flash.mount.d/96-flash-rx-fix.conf\': \'253f0b748cea8f455e762efc7724220e75a2b4f6bf018214e4b77b5e5ecc89de\'}, \'0.1.0-spi-rx.2\': {\'usr/local/lib/hector-flash-rx-fix/boot.py\': \'cd63bdedd4bc2e500061b43e7bb99d9afcb9785b9f80f100d7b0910f075a5c77\', \'usr/local/lib/hector-flash-rx-fix/guard.py\': \'4ee5b364836e156d28468776ca846f39804b089cf5f09adb3b3d73e4030df3cd\', \'usr/local/lib/hector-flash-rx-fix/hector_sun6i_rx_fix.ko\': \'1f0d51ae1bfea574ad3c83a8d993f53050c2d818c88a0445b93a96b13ac5ba87\', \'usr/local/lib/hector-flash-rx-fix/fix_contract.py\': \'5ec8700a6efe96474035f3c41a24b634288ff4a09006d7d3f51a04f6dde94e60\', \'usr/local/lib/hector-flash-rx-fix/transaction.py\': \'a487859df8e8884b5eb5d6ba6bbadfc224b8cd2fb5b1c5890a9b4aa6ec8a86ef\', \'usr/local/lib/hector-flash-rx-fix/remove.py\': \'868260d044f6c394d9778ed7dd41605de3cb340f7b3c883d9f1f3bae1a9918f1\', \'usr/share/doc/hector-flash-rx-fix/GPL-2.0.txt\': \'f6b78c087c3ebdf0f3c13415070dd480a3f35d8fc76f3d02180a407c1c812f79\', \'usr/share/doc/hector-flash-rx-fix/NOTICE.txt\': \'216833db6c279baed8d87e47795cbde6367a94680adce12ed89bd4abeedb8454\', \'usr/local/lib/hector-flash-rx-fix/publisher-proof.json\': \'a1654a158c2971f1f8fa74949c50c4368871b98d8726c4850cb17cba5e4ed15f\', \'etc/systemd/system/hector-flash-rx-guard.service\': \'3b3afdbfe713adb862b46f5dd8b6e362ee20f77d017d1303faaf28fbcaf0d169\', \'etc/systemd/system/hector-flash-rx-fallback.service\': \'97d2d9e72ea226d1b9c5a6f9788dc5660462a8a58a0d4cb6afdf1e4cce0300e1\', \'etc/systemd/system/hector-flash-rx-confirm.service\': \'7e47abeee1e220cbfcd7126f6b6479a8705ad6a02a5f5ac948e7e057a3e1c39f\', \'etc/systemd/system/hector-flash-rx-fix.service\': \'9db9d26b5fb43743e8d6484e257ff756ea01a0dfc64a6c3a5b35704840e617e8\', \'etc/systemd/system/multi-user.target.d/96-flash-rx-fix.conf\': \'b3786db6c44a2b64259baaddccaccb69f05231b53d68851ab7b1e315ec0b877d\', \'etc/systemd/system/jack.service.d/96-flash-rx-fix.conf\': \'d5064b0568b6f00f3585bfc7cfe8ceffd4fcd0d2ee4a4918e0813d13d16883fa\', \'etc/systemd/system/lightdm.service.d/96-flash-rx-fix.conf\': \'d5064b0568b6f00f3585bfc7cfe8ceffd4fcd0d2ee4a4918e0813d13d16883fa\', \'etc/systemd/system/nor_flash.mount.d/96-flash-rx-fix.conf\': \'253f0b748cea8f455e762efc7724220e75a2b4f6bf018214e4b77b5e5ecc89de\'}}\nCONTROL=(\'initial-intent.json\', \'installation-record.json\', \'transaction/journal.json\', \'state.json\')\nBASE_GUARDS=(\'etc/fstab\', \'usr/bin/start_beebo.sh\', \'home/debian/UI/show_widget.py\', \'usr/lib/modules/5.4.2-rt5-001/kernel/fs/jffs2/jffs2.ko\')\nSTABLE=(BASE_GUARDS[0],BASE_GUARDS[1],BASE_GUARDS[3])\n\nBRIDGE_NAMES=(\'opt/hector-storage-manager/app/system_support.py\', \'opt/hector-storage-manager/app/system_support_contract.py\', \'opt/hector-storage-manager/app/system_readiness.py\', \'opt/hector-storage-manager/app/flash-repair.zip\')\n'
STORAGE_UPGRADE_SHA='a9e47c1661fe68e58c960a2b7459507efa2ecbd7e2b0fe919f2af405f36a9d0c'
def storage_upgrade_support():
    import types
    assert sha(STORAGE_UPGRADE_SOURCE)==STORAGE_UPGRADE_SHA
    module=types.ModuleType('hector_embedded_paired_upgrade')
    exec(compile(STORAGE_UPGRADE_SOURCE,'<signed-paired-storage-upgrade>','exec'),module.__dict__)
    return module
def recover_storage_upgrade(root=Path('/'),lower=Path('/overlay/lower'),write_window=None):
    return storage_upgrade_support().recover(__import__('types').SimpleNamespace(**globals()),root,lower,write_window)

def startup():
    try:
        removed=recover_removal()
        if removed=='stock-restored':
            assert driver()==STOCK
            report('fallback-stock',reason='Authenticated component removal restored stock; normal restart requested')
            command(['/bin/systemctl','--no-block','reboot']);return
        paired=recover_storage_upgrade()
        if paired in ('restored-previous-support','previous-support-restored'):
            assert driver()==STOCK
            report('reboot-to-previous-support',reason='Authenticated paired-support recovery needs a fresh overlay')
            command(['/bin/systemctl','--no-block','reboot'])
            time.sleep(30)
            raise RuntimeError('Waiting for reboot into the previous signed support')
        recovered=recover_upgrade()
        if recovered=='restored-old':
            assert driver()==STOCK
            report('fallback-stock',reason='Interrupted user-space support upgrade restored; normal restart required')
            command(['/bin/systemctl','--no-block','reboot']);return
        initial=recover_initial()
        if initial=='initial-restored-stock':
            assert driver()==STOCK
            report('fallback-stock',reason='Interrupted initial support publication restored stock')
            command(['/bin/systemctl','--no-block','reboot']);return
    except Exception as error:
        assert driver()==STOCK
        report('recovery-refused',reason='Independent recovery refused: '+type(error).__name__+': '+str(error));raise
    current=boot_id()
    try:preflight();original=state()
    except Exception as exc:
        # A future kernel or damaged activation state must not prevent the
        # original firmware from mounting presets and starting normally.
        assert driver()==STOCK
        report('fallback-stock',reason='Admission failed: '+type(exc).__name__+': '+str(exc));return
    v=boot_policy(original,current)
    if v!=original:put_state(v)
    if driver()==FIX and filesystem()=='fixed' and v['enabled'] and (v.get('pending_boot')==current or v.get('confirmed_boot')==current):
        prior=snapshot(RUN/'status.json')
        assert prior['boot']==current and prior['status']=='active'
        return # Same-boot restart never detaches mounted preset storage.
    if not v['enabled']:
        try:prior=snapshot(RUN/'status.json')
        except FileNotFoundError:prior={}
        if prior.get('boot')==current and prior.get('status')=='fallback-stock' and driver()==STOCK and filesystem()=='stock':return
        assert driver()==STOCK
        filesystem_support().restore(command,no_flash_mounts)
        check_flash();report('fallback-stock',reason=v.get('failure','Disabled'));return
    if v.get('pending_boot')==current and driver()==FIX:
        assert snapshot(RUN/'status.json')['status']=='active' and filesystem()=='fixed';return
    try:
        no_flash_mounts();assert driver()==STOCK
        verify_release()
        v.update(pending_boot=current,status='activating');put_state(v)
        # The independent hardware guard acknowledges that it is armed before
        # the built-in controller can be detached.
        command(['/bin/systemctl','start','hector-flash-rx-guard.service'],20)
        armed=snapshot(RUN/'guard-armed.json')
        assert armed['boot']==current and armed['hardware_watchdog'] and armed['timeout_seconds']==16
        command(['/sbin/insmod',str(BASE/(MODULE+'.ko'))])
        detach(STOCK);(CONTROLLER/'driver_override').write_text(FIX+'\n');bind(FIX);check_flash()
        measured=probe()
        filesystem_support().activate(BASE,command,no_flash_mounts)
        report('active',probe=measured,driver=FIX,filesystem='fixed',confirmed=False)
        v.update(status='active-unconfirmed');put_state(v)
    except Exception as exc:
        v.update(enabled=False,status='activation-failed',failure=type(exc).__name__+': '+str(exc),pending_boot=None);put_state(v)
        restore_unmounted();report('fallback-stock',reason=v['failure'])
        if (DATA/'upgrade-storage-v1/active.json').exists():
            command(['/bin/systemctl','--no-block','reboot'])
            time.sleep(30)
            raise RuntimeError('Storage support upgrade activation failed; reboot recovery required')

def recover_startup():
    # ExecStopPost may also run during normal shutdown; never detach active NOR.
    try:
        r=snapshot(RUN/'status.json')
        if r['boot']==boot_id() and r['status'] in ('active','fallback-stock'):return
    except FileNotFoundError:pass
    v=state();v.update(enabled=False,status='startup-interrupted',failure='Startup exited without a verified result',pending_boot=None);put_state(v)
    restore_unmounted();report('fallback-stock',reason=v['failure'])

def confirm():
    if snapshot(RUN/'status.json')['status']=='fallback-stock':return
    preflight();v=state();current=boot_id()
    if not v['enabled']:return
    if v.get('confirmed_boot')==current and v.get('pending_boot') is None:
        assert driver()==FIX and mounted() and filesystem()=='fixed'
        assert snapshot(RUN/'status.json').get('status')=='active'
        return
    assert v.get('pending_boot')==current and driver()==FIX and mounted() and filesystem()=='fixed'
    for unused in range(10):
        assert all(subprocess.run(['/bin/systemctl','is-active','--quiet',name],timeout=5).returncode==0 for name in ('jack.service','lightdm.service'))
        assert driver()==FIX and mounted() and filesystem()=='fixed';time.sleep(1)
    measured=probe(32)
    # The NOR mount must have followed correction activation in this boot.
    assert snapshot(RUN/'status.json')['status']=='active' and filesystem()=='fixed'
    def accept(latest):
        assert latest['enabled'] and latest.get('pending_boot')==current and latest['status']=='active-unconfirmed'
        latest.update(pending_boot=None,confirmed_boot=current,status='confirmed');return latest
    change_state(accept)
    report('active',confirmed=True,driver=FIX,filesystem='fixed',probe=measured)

def fail_confirmation():
    v=state();v.update(enabled=False,status='confirmation-failed',failure='Boot health confirmation failed',pending_boot=None);put_state(v)
    report('reboot-to-stock',reason=v['failure'])
    command(['/bin/systemctl','--no-block','reboot'])

if __name__=='__main__':
    action=sys.argv[1]
    if action=='start':startup()
    elif action=='recover':recover_startup()
    elif action=='confirm':confirm()
    elif action=='fail-confirmation':fail_confirmation()
    else:raise SystemExit('Unsupported boot action')
