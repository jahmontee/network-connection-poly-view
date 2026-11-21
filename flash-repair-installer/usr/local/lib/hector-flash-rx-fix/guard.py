# SPDX-License-Identifier: MIT
"""Independent hardware-watchdog guardian, active only during boot acceptance."""
import fcntl,json,os,signal,socket,struct,sys,time
from pathlib import Path
sys.path.insert(0,'/usr/local/lib/hector-flash-rx-fix')
import boot

def notify():
    target=os.environ['NOTIFY_SOCKET'];target='\0'+target[1:] if target.startswith('@') else target
    with socket.socket(socket.AF_UNIX,socket.SOCK_DGRAM) as sock:sock.sendto(b'READY=1',target)
def terminate(signum,frame):raise SystemExit('Guardian stopped')
signal.signal(signal.SIGTERM,terminate)
fd=None;safe_close=False;completion=None
try:
    v=boot.state();current=boot.boot_id();assert v['enabled'] and v.get('pending_boot')==current
    assert Path('/sys/class/watchdog/watchdog0/device/driver').resolve().name=='sunxi-wdt'
    fd=os.open('/dev/watchdog0',os.O_WRONLY|os.O_CLOEXEC)
    support=bytearray(40);fcntl.ioctl(fd,0x80285700,support,True)
    options,firmware,identity=struct.unpack('II32s',support)
    assert options&0x100 and identity.rstrip(b'\0')==b'sunxi-wdt','Expected disarmable sunxi hardware watchdog'
    timeout=bytearray(struct.pack('I',16));fcntl.ioctl(fd,0xc0045706,timeout,True)
    assert struct.unpack('I',timeout)[0]==16
    boot.RUN.mkdir(mode=0o700,exist_ok=True)
    boot.atomic(boot.RUN/'guard-armed.json',dict(boot=current,hardware_watchdog=True,timeout_seconds=16))
    os.write(fd,b'\0');notify();deadline=time.monotonic()+120
    while time.monotonic()<deadline:
        v=boot.state()
        try:r=boot.snapshot(boot.RUN/'status.json')
        except FileNotFoundError:r={}
        confirmed,fallback=boot.guard_decision(v,current,boot.driver(),r.get('status'),boot.filesystem())
        if confirmed or fallback:
            safe_close=True;completion=dict(boot=current,confirmed=confirmed,fallback=fallback,watchdog_disarmed=True);break
        os.write(fd,b'\0');time.sleep(1)
    else:
        def deadline_policy(latest):
            status=boot.snapshot(boot.RUN/'status.json')
            healthy,_=boot.guard_decision(latest,current,boot.driver(),status.get('status'),boot.filesystem())
            return boot.timeout_policy(latest,current,healthy=healthy)
        latest=boot.change_state(deadline_policy)
        if (latest.get('confirmed_boot')==current and latest.get('pending_boot') is None
                and boot.driver()==boot.FIX and boot.filesystem()=='fixed'
                and boot.snapshot(boot.RUN/'status.json').get('status')=='active'):
            safe_close=True;completion=dict(boot=current,confirmed=True,fallback=False,watchdog_disarmed=True)
        else:
            boot.report('reboot-to-stock',reason=latest['failure'])
            try:boot.command(['/bin/systemctl','--no-block','reboot'])
            except Exception:pass
        # With no further keepalives the hardware enforces recovery even if
        # the normal reboot path or kernel scheduler has stopped responding.
finally:
    if fd is not None:
        if safe_close:os.write(fd,b'V')
        os.close(fd)
        if completion is not None:boot.atomic(boot.RUN/'guard-complete.json',completion)
