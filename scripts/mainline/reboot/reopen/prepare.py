#!/usr/bin/env python3
"""Bundle one reopen launcher alongside integrated images. Host file I/O only."""
from pathlib import Path
import argparse,hashlib,json,shutil,subprocess as sp
HERE=Path(__file__).resolve().parent
MAIN=HERE.parents[1]
ROOT=Path('/home/user/A9-build/mainline')

def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def install(image,stage):
    image,stage=Path(image),Path(stage)
    record=json.loads((stage/'client-build.json').read_text())
    assert record['source_sha256']==sha(HERE/'client.cpp')
    assert record['android_arm64_self_test'] and not record['hardware_tested']
    for name,digest in record['files'].items():assert sha(stage/'payload'/name)==digest
    client=image/'reopen-client'
    assert not client.exists(),'do not replace an active or already bundled reopen test'
    shutil.copytree(stage/'payload',client)
    (client/'SHA256SUMS').write_text(''.join(d+'  '+n+'\n' for n,d in record['files'].items()))
    runtime=image/'runtime'
    for dest in runtime.rglob('*'):
        source=MAIN/dest.relative_to(runtime)
        if dest.is_file() and source.is_file():shutil.copyfile(source,dest)
    shutil.copytree(HERE,runtime/'reboot/reopen',ignore=shutil.ignore_patterns('__pycache__'),dirs_exist_ok=True)
    # The only persistent repository runtime dependency is the established
    # registry/USB-lock environment; image inputs, policy/compiler and client
    # libraries are all adjacent, including the Android BRE capture repair.
    (image/'run-reopen.sh').write_text('''#!/usr/bin/env bash
set -euo pipefail
image=$(cd -- "$(dirname -- "$0")" && pwd)
. "${A9_HOME:-/home/user/A9}/scripts/a9-env.sh"
a9_usb_lock_inherit
export A9_REOPEN_IMAGE="$image"
exec python3 -B "$image/runtime/reboot/reopen/launcher.py"
''')
    for phase in ('before-b','on-b'):
        p=image/('reopen-'+phase+'.sh')
        p.write_text('#!/usr/bin/env bash\nset -euo pipefail\nexec python3 -B "$A9_REOPEN_IMAGE/runtime/reboot/reopen/launcher.py" '+phase+'\n')
        p.chmod(0o755)
    (image/'run-reopen.sh').chmod(0o755)
    files=[image/'boot.img',image/'vendor.img',*runtime.rglob('*'),*client.rglob('*'),image/'run-reopen.sh',image/'reopen-before-b.sh',image/'reopen-on-b.sh']
    manifest=dict(host_only_prepared=True,hardware_tested=False,client=record,
                  sha256={str(p.relative_to(image)):sha(p) for p in files if p.is_file()},
                  B_image='adjacent integrated mainline boot/vendor',A_image='existing daily slot A, stock 4.19 kernel and stock Keymaster, not flashed',
                  writes=['boot_b','vendor_b','normal metadata/userdata/kdebuginfo filesystem activity','normal slot selection','existing b-try data repair if A cannot boot'],
                  direct_control_key_file_writes=False,raw_inputs_persisted_to_logs=False)
    (image/'reopen-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    for p in (image/'run-reopen.sh',image/'reopen-before-b.sh',image/'reopen-on-b.sh'):
        sp.run(['bash','-n',p],check=True)
    return manifest

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base',type=Path,default=ROOT/'integ-20261024-12')
    ap.add_argument('--stage',type=Path,default=ROOT/'reopen-host')
    ap.add_argument('--out',type=Path,default=ROOT/'fbe-reopen-20261024')
    args=ap.parse_args();base=args.base.resolve();out=args.out.resolve()
    assert not out.exists(),'preserve existing candidates'
    assert shutil.disk_usage(ROOT).free>15*1024**3+1200*1024**2
    out.mkdir(mode=0o700)
    for name in ('boot.img','vendor.img'):shutil.copyfile(base/name,out/name)
    shutil.copytree(base/'runtime',out/'runtime')
    result=install(out,args.stage.resolve())
    (out/'README.txt').write_text((HERE/'README.md').read_text())
    print('HOST PREPARED '+str(out/'run-reopen.sh'))
if __name__=='__main__':main()
