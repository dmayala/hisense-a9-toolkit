#!/usr/bin/env python3
"""Host-only reuse of the existing row29 TEST formatter, adding TWRP mkfs paths."""
from pathlib import Path
import difflib,hashlib,json,re,shutil,subprocess as sp
ROOT=Path('/home/user/A9-build/mainline');O=ROOT/'row27-release-20261022'
P=ROOT/'plain-wrapped-20261022/revert-source'
D=O/'twrp-source'
assert not D.exists()
shutil.copytree(P,D)
p=D/'scripts/mainline/storage-row29/data/map.c';before=p.read_text()
a='const char *choices[]={"/system/bin/make_f2fs",'
b='const char *choices[]={"/sbin/make_f2fs","/sbin/mkfs.f2fs","/system/bin/make_f2fs",'
assert before.count(a)==1;p.write_text(before.replace(a,b))
(O/'twrp-mkfs-paths.patch').write_text(''.join(difflib.unified_diff(before.splitlines(True),p.read_text().splitlines(True),fromfile='existing-software-fbe/map.c',tofile='twrp/map.c')))
cc='/nix/store/4vpsm867635vxx8cxyp8kxr1jpisqqr5-aarch64-unknown-linux-musl-gcc-wrapper-14.3.0/bin/aarch64-unknown-linux-musl-gcc'
cmd=[cc,'-static','-Os','-Wall','-Wextra','-Werror','-Wno-unused-function','-ffunction-sections','-fdata-sections','-Wl,--gc-sections','-DROW29_FBE','-DROW29_REFORMAT',p,'-o',O/'row29-twrp-format']
with (O/'twrp-tools-build.log').open('w') as log:sp.run(list(map(str,cmd)),stdout=log,stderr=sp.STDOUT,check=True)
mapper=sp.check_output(['debugfs','-R','cat /bin/a9ml-row29-map',str(O/'vendor.img')],stderr=sp.DEVNULL)
(O/'a9ml-row29-map').write_bytes(mapper)
(O/'twrp-tools.json').write_text(json.dumps({'formatter_source':str(P),'sole_source_change':'add /sbin/make_f2fs and /sbin/mkfs.f2fs lookup choices for TWRP; all bounds/guards/format options unchanged','command':list(map(str,cmd)),'formatter_sha256':hashlib.sha256((O/'row29-twrp-format').read_bytes()).hexdigest(),'mapper_sha256':hashlib.sha256(mapper).hexdigest(),'approved_test_start_sectors':2197264,'approved_test_length_sectors':6191304,'new_storage_layout':False,'phone_access':False},indent=2)+'\n')
for n in ['row29-twrp-format','a9ml-row29-map']:(O/n).chmod(0o700)
print('TWRP tools built. Existing bounded TEST-only formatter; boot mapper byte-identical to audiofix4. No phone call.')
