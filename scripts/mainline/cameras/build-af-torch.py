#!/usr/bin/env python3
"""Host-only AF/torch modules for an existing camera-enabled kernel ABI.

Use --kernel-source with the same kernel source/version as --base-obj.
All generated objects stay under --out; existing builds are read-only inputs.
The resulting overlay adds two modules to --camera-build for stage.py.
"""
import argparse, hashlib, json, os, shutil, subprocess
from pathlib import Path
HERE = Path(__file__).resolve().parent
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--kernel-source', type=Path, required=True)
p.add_argument('--base-obj', type=Path, required=True)
p.add_argument('--camera-build', type=Path, required=True)
p.add_argument('--out', type=Path, required=True)
p.add_argument('--media-symvers', type=Path, help='exports of the camera media build')
a = p.parse_args(); a.out.mkdir(parents=True, exist_ok=True)
if shutil.disk_usage(a.out).free < 15 * 1024**3 + 200 * 1024**2:
    raise SystemExit('need 15GiB reserve plus 200MiB build budget')
obj = a.out/'obj'; obj.mkdir(exist_ok=True)
mods = a.out/'modules'; mods.mkdir(exist_ok=True)
# Make's prepare phase must not modify the coordinator's object/source tree.
if not (obj/'.config').exists():
    shutil.copyfile(a.base_obj/'.config', obj/'.config')
with (obj/'.config').open('a') as f:
    f.write('\n'+(HERE/'a9-cameras.config').read_text())
for n in ['dw9714p-a9', 'pmi632-a9-torch']:
    shutil.copyfile(HERE/(n+'.c'), mods/(n+'.c'))
(mods/'Makefile').write_text('obj-m += dw9714p-a9.o pmi632-a9-torch.o\n')
cmd = ['make', '-C', str(a.kernel_source.resolve()), 'O='+str(obj.resolve()),
       'ARCH=arm64', 'CROSS_COMPILE=aarch64-unknown-linux-gnu-', 'LOCALVERSION=+', '-j4']
def run(args):
    subprocess.run(cmd+args, check=True)
run(['olddefconfig']); run(['modules_prepare'])
# Base exports plus camera extension exports are required by the lens/flash.
exports = {}
media = a.media_symvers or next((p for p in [a.camera_build/'kernel/drivers/media/Module.symvers', a.camera_build/'repair-media/kernel/drivers/media/Module.symvers'] if p.exists()), None)
if media is None:
    raise SystemExit('pass --media-symvers from the camera media module build')
for source in [a.base_obj/'Module.symvers', media]:
    for line in source.read_text().splitlines():
        exports[line.split()[1]] = line
(obj/'Module.symvers').write_text('\n'.join(exports.values())+'\n')
run(['M='+str(mods.resolve()), 'modules'])
overlay = a.out/'overlay/lib/modules'; overlay.mkdir(parents=True, exist_ok=True)
for m in mods.glob('*.ko'):
    target = overlay/m.name; shutil.copyfile(m, target)
    subprocess.run(['aarch64-unknown-linux-gnu-strip', '--strip-debug', str(target)], check=True)
result = {str(f.relative_to(a.out)):hashlib.sha256(f.read_bytes()).hexdigest()
          for f in sorted(overlay.glob('*.ko'))}
(a.out/'modules.json').write_text(json.dumps(result, indent=2)+'\n')
inputs = {'kernel_source': str(a.kernel_source.resolve()),
          'base_obj': str(a.base_obj.resolve()), 'media_symvers': str(media.resolve()),
          'sha256': {str(f.resolve()): hashlib.sha256(f.read_bytes()).hexdigest()
                     for f in [a.kernel_source/'Makefile', a.base_obj/'.config',
                               a.base_obj/'Module.symvers', media, obj/'.config']}}
(a.out/'build-inputs.json').write_text(json.dumps(inputs, indent=2)+'\n')
print(json.dumps(result, indent=2))
