#!/usr/bin/env python3
"""Link the observed root-console closure without permissive domains. Host only."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess as sp

HERE = Path(__file__).resolve().parent
ROOT = Path('/home/user/A9-build/mainline')
CACHE = ROOT/'android/cache/gsi-sepolicy/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v8-3569860608-1792259254'


def sha(p):
    with Path(p).open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()


def compile_policy(image, out, cache=CACHE):
    assert not out.exists(), 'new policy output required'
    out.mkdir(parents=True)
    sp.run(['debugfs','-R',f'rdump /etc/selinux {out}',image],check=True,stdout=sp.DEVNULL,stderr=sp.DEVNULL)
    sel = out/'selinux'; vendor = sel/'vendor_sepolicy.cil'; original = vendor.read_text()
    addition = (HERE/'rootsh-enforcing.cil').read_text()
    original = re.sub(r'\(typepermissive a9ml_rootsh\)', '', original)
    assert not re.search(r'\(typepermissive\s',original), 'unexpected permissive vendor type'
    vendor.write_text(original+'\n'+addition)
    version = (sel/'plat_sepolicy_vers.txt').read_text().strip()
    system = cache/'system'; plat = system/'etc/selinux'
    files = [plat/'plat_sepolicy.cil', plat/f'mapping/{version}.cil']
    for p in (plat/f'mapping/{version}.compat.cil',
              system/'system_ext/etc/selinux/system_ext_sepolicy.cil',
              system/f'system_ext/etc/selinux/mapping/{version}.cil',
              system/f'system_ext/etc/selinux/mapping/{version}.compat.cil',
              system/'product/etc/selinux/product_sepolicy.cil',
              system/f'product/etc/selinux/mapping/{version}.cil'):
        if p.is_file(): files.append(p)
    files += [sel/'plat_pub_versioned.cil',vendor]
    if (sel/'odm_sepolicy.cil').is_file(): files.append(sel/'odm_sepolicy.cil')
    private = out/'inputs'; private.mkdir(); inputs = []
    for i,p in enumerate(files):
        text = re.sub(r'\(typepermissive [^\s)]+\)', '', p.read_text())
        dest = private/(str(i)+'-'+p.name); dest.write_text(text); inputs.append(dest)
    # Match the incoming development policy's flags/ABI. -D keeps denials
    # observable; -N preserves inherited Treble exceptions, not release claims.
    cmd = [ROOT/'android/dl/cf_arm64_x/otatools/bin/secilc','-D','-m','-M','true','-G','-N','-c','30',
           *inputs,'-o',sel/'precompiled_sepolicy','-f','/dev/null']
    with (out/'compile.log').open('w') as f: sp.run(list(map(str,cmd)),check=True,stdout=f,stderr=sp.STDOUT)
    names = ['vendor_sepolicy.cil','precompiled_sepolicy']
    for prefix,directory in [('plat',plat),('system_ext',system/'system_ext/etc/selinux'),('product',system/'product/etc/selinux')]:
        name = prefix+'_sepolicy_and_mapping.sha256'
        assert (directory/name).is_file()
        dest = 'precompiled_sepolicy.'+name; shutil.copyfile(directory/name,sel/dest); names.append(dest)
    result = dict(base=str(image),base_sha256=sha(image),host_only=True,permissive_types=[],
                  rules_sha256=sha(HERE/'rootsh-enforcing.cil'),payload={n:sha(sel/n) for n in names})
    (out/'build.json').write_text(json.dumps(result,indent=2)+'\n')
    return { 'etc/selinux/'+n:sel/n for n in names }


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--vendor',type=Path,required=True); ap.add_argument('--out',type=Path,required=True)
    a = ap.parse_args(); compile_policy(a.vendor.resolve(),a.out.resolve()); print('ROOTSH policy link PASS')
