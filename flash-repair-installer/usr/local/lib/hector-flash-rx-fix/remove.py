# SPDX-License-Identifier: MIT
"""Independent signed component removal. Retain records until postboot closure."""
import json,os,sys
sys.path.insert(0,'/usr/local/lib/hector-flash-rx-fix')
import boot
assert os.geteuid()==0
print(json.dumps(dict(status=boot.begin_removal(),normal_restart_requested=True,postboot_closure_required=True)))
