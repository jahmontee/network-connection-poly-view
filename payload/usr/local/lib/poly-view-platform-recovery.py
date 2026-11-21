"""Fixed boot entry: use the protected pre-update worker, even after a power cut."""
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import stat
import sys

ROOT=Path('/mnt/community-lab/hector-poly-view-platform')


def checked(path,limit=8*1024*1024):
    for parent in (path,*path.parents):
        info=parent.lstat()
        if info.st_uid!=0 or info.st_mode&0o022 or stat.S_ISLNK(info.st_mode):
            raise ValueError('Unprotected app recovery path')
        if parent==ROOT:break
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as source:
        info=os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or not 0<info.st_size<=limit:
            raise ValueError('Invalid app recovery file')
        return source.read(limit+1)


try:token=json.loads(checked(ROOT/'current.json'))['id']
except FileNotFoundError:raise SystemExit(0)
if not isinstance(token,str) or not re.fullmatch('[a-f0-9]{32}',token):raise ValueError('Invalid app recovery job')
folder=ROOT/'jobs'/token
job=json.loads(checked(folder/'job.json'))
allowed={'platform_service.py','platform_release.py','platform_transaction.py','platform_engine.py',
         'publisher_release.py','publishers.json','core.py'}
with_support=allowed|{'system_support.py','system_support_contract.py','system_support_transaction.py'}
# Both exact reviewed generations remain recoverable. A subset, arbitrary
# addition or missing support member must never become executable recovery.
if set(job['engine']) not in (allowed,with_support):raise ValueError('Incomplete app recovery engine')
for name,expected in job['engine'].items():
    if hashlib.sha256(checked(folder/'engine'/name)).hexdigest()!=expected:
        raise ValueError('App recovery engine changed')
sys.path.insert(0,str(folder/'engine'))
runpy.run_path(str(folder/'engine/platform_service.py'),run_name='__main__')
