#!/usr/bin/env python3
"""Prepare a separate host hook for unchanged integ27 images. Never runs USB tools."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess as sp
HERE=Path(__file__).resolve().parent
MAIN=HERE.parent

def sha(p):
 with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def prepare(image,out):
 image,out=Path(image).resolve(),Path(out).resolve()
 assert not out.exists(), 'preserve earlier rerun hooks'
 build=json.loads((image/'integrated-build.json').read_text())
 assert build['host_checks_passed'] and build['build_rc']==0
 for n in ('boot.img','vendor.img'):assert sha(image/n)==build['sha256'][n],n
 for n,digest in build['runtime_sha256'].items():assert sha(image/n)==digest,n
 out.mkdir(parents=True)
 for n in ('runtime','acceptance-assets','hvenc-qualification','encoder-probes'):
  shutil.copytree(image/n,out/n,ignore=shutil.ignore_patterns('__pycache__'))
 for n in ('boot.img','encoder-stage.json'):shutil.copyfile(image/n,out/n)
 # Only host orchestration changes. Keep every native/Java input from the image.
 overlays=('acceptance/onb.py','acceptance/drain.py','video/qualification/variants.py')
 for n in overlays:shutil.copyfile(MAIN/n,out/'runtime'/n)
 manifest_path=out/'hvenc-qualification/manifest.json'
 manifest=json.loads(manifest_path.read_text());before=json.loads(json.dumps(manifest))
 host_source='video/qualification/variants.py'
 assert manifest['sources'][host_source]==sha(image/'runtime'/host_source)
 manifest['sources'][host_source]=sha(out/'runtime'/host_source)
 assert manifest['files']==before['files'] and manifest['encoder_receipt_sha256']==before['encoder_receipt_sha256']
 manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
 spec=importlib.util.spec_from_file_location('rerun_validation',out/'runtime/video/qualification/validation.py')
 v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
 v.verify_bundle(out/'hvenc-qualification',out/'runtime',out/'encoder-stage.json')
 assets=json.loads((out/'acceptance-assets/assets.json').read_text())
 for n,digest in assets['assets'].items():assert sha(out/'acceptance-assets'/n)==digest,n
 for n,digest in assets['sources'].items():assert sha(out/'runtime'/n)==digest,n
 hook=out/'onb.py'
 hook.write_text('''#!/usr/bin/env python3
import os
from pathlib import Path
import subprocess as sp
import sys
p=Path(__file__).resolve().parent
if os.environ.get('A9_BTRY_IN_HOOK')!='1':sys.exit(2)
sp.run([sys.executable,'-B',str(p/'runtime/reboot/fbe-onb.py')],check=False)
sys.exit(sp.call([sys.executable,'-B',str(p/'runtime/acceptance/onb.py')]))
''');hook.chmod(0o755)
 launcher=out/'run.sh'
 launcher.write_text('''#!/usr/bin/env bash
set -euo pipefail
p=$(cd -- "$(dirname -- "$0")" && pwd)
export A9_SWEEP_IMAGE="$p" A9_SWEEP_ASSETS="$p/acceptance-assets"
export A9_SECILC="$p/runtime/tools/bin/secilc" A9_BTRY_ONB="$p/onb.py"
export A9_BTRY_FORMAT="${A9_BTRY_FORMAT:-0}" A9_BTRY_HOLD="${A9_BTRY_HOLD:-7200}" A9_BTRY_SOUND=0
export A9_SWEEP_PIN_LIFECYCLE="${A9_SWEEP_PIN_LIFECYCLE:-}"
export A9_SWEEP_HVENC_QUALIFY="${A9_SWEEP_HVENC_QUALIFY:-1}"
export A9_SWEEP_HVENC_VARIANTS="${A9_SWEEP_HVENC_VARIANTS:-1}"
export A9_SWEEP_DRAIN="${A9_SWEEP_DRAIN:-1}"
'''+f'exec "{image}/runtime/boot-deadline/b-try.sh" "{image}/boot.img" "{image}/vendor.img"\n')
 launcher.chmod(0o755);sp.run(['bash','-n',launcher],check=True)
 receipt=dict(host_only=True,phone_access=False,image_rebuilt=False,image_source=build['source_commit'],
              host_source=sp.check_output(['git','-C',MAIN.parents[1],'rev-parse','HEAD'],text=True).strip(),
              image_sha256={n:build['sha256'][n] for n in ('boot.img','vendor.img')},
              host_overlays={n:sha(out/'runtime'/n) for n in overlays},
              native_qualification_files_unchanged=before['files'],
              qualification_host_pin_updated=host_source,launcher=str(launcher))
 (out/'host-rerun.json').write_text(json.dumps(receipt,indent=2)+'\n')
 print('HOST_RERUN READY '+str(launcher)+'; original images/native assets unchanged')
 return receipt

if __name__=='__main__':
 ap=argparse.ArgumentParser(description=__doc__)
 ap.add_argument('--image',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
 a=ap.parse_args();prepare(a.image,a.out)
