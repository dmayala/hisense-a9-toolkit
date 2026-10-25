#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
from pathlib import Path
import argparse, subprocess, shutil, json, tempfile
parser=argparse.ArgumentParser(description='Offline ESS DT compilation and reservation/order checks')
parser.add_argument('--repo-root', type=Path, required=True)
parser.add_argument('--kernel-source', type=Path, required=True)
parser.add_argument('--ess-dtsi', type=Path, default=Path(__file__).resolve().parents[1]/'a9-es9218.dtsi')
parser.add_argument('--audio-dtsi', type=Path, default=Path(__file__).resolve().parents[1]/'a9-audio.dtsi')
parser.add_argument('--stock-dts', type=Path, default=Path.home()/'A9-build/mainline/sol-pmic/evidence/base_0.dts',
                    help='stock base DTB/DTS for the reserved-memory audit (review 33 F1)')
parser.add_argument('--output-dir', type=Path)
parser.add_argument('--cpp', default='cpp')
parser.add_argument('--dtc', default='dtc')
parser.add_argument('--fdtget', default='fdtget')
args=parser.parse_args()
k=args.kernel_source.resolve(); repo=args.repo_root.resolve()
s=args.output_dir.resolve() if args.output_dir else Path(tempfile.mkdtemp(prefix='a9-es9218-dt-'))
s.mkdir(parents=True,exist_ok=True)
cpp=args.cpp; dtc=args.dtc; fdtget=args.fdtget
shutil.copyfile(repo/'docs/mainline/sm6115-hisense-hlte556n.dts',s/'sm6115-hisense-hlte556n.dts')
if args.ess_dtsi.resolve() != (s/'a9-es9218.dtsi').resolve():
 shutil.copyfile(args.ess_dtsi,s/'a9-es9218.dtsi')
if args.audio_dtsi.resolve() != (s/'a9-audio.dtsi').resolve():
 shutil.copyfile(args.audio_dtsi,s/'a9-audio.dtsi')
assert args.stock_dts.is_file(), f'stock base DTS for the reserved audit is missing: {args.stock_dts}'
stock=args.stock_dts.resolve(); audit=Path(__file__).resolve().parents[1]/'dt-reserved-audit.py'
a=(repo/'scripts/mainline/android/sm6115-hisense-hlte556n-android.dts').read_text()
results={}
# Variants mirror build-kernel.py: every audio DT includes a9-audio.dtsi; ESS and
# ADSP each have their own opt-in define (review 33 F2).
MODES={'baseline':[],'disabled':[],'enabled':['-DA9_AUDIO_ENABLE_ES9218=1'],'adsp':['-DA9_AUDIO_ENABLE_ADSP=1'],
       'macros':['-DA9_AUDIO_ENABLE_MACROS=1','-DA9_AUDIO_ENABLE_ES9218=1']}
for mode in MODES:
 (s/'validate.dts').write_text(a+('' if mode=='baseline' else '\n#include "a9-audio.dtsi"\n#include "a9-es9218.dtsi"\n'))
 args=[cpp,'-nostdinc','-undef','-D__DTS__','-x','assembler-with-cpp','-I'+str(s),'-I'+str(k/'arch/arm64/boot/dts/qcom'),'-I'+str(k/'include')]
 args+=MODES[mode]
 r=subprocess.run(args+[str(s/'validate.dts')],capture_output=True,text=True);assert not r.returncode,r.stderr
 (s/(mode+'.pp.dts')).write_text(r.stdout)
 dtb=s/(mode+'.dtb');r=subprocess.run([dtc,'-I','dts','-O','dtb','-o',str(dtb),str(s/(mode+'.pp.dts'))],capture_output=True,text=True)
 (s/(mode+'.dtc.log')).write_text(r.stderr);assert not r.returncode,r.stderr
 def get(node,prop,typ='s'):
  return subprocess.check_output([fdtget,'-t',typ,str(dtb),node,prop],text=True).strip()
 ranges=[int(x,16) for x in get('/soc@0/pinctrl@500000','gpio-reserved-ranges','x').split()]
 reserved=set()
 for i in range(0,len(ranges),2):reserved.update(range(ranges[i],ranges[i]+ranges[i+1]))
 results[mode]={'reserved':sorted(reserved)}
 adsp_status=get('/soc@0/remoteproc@a400000','status')
 # F2: ADSP is okay only with the audio-specific ADSP opt-in; never in the
 # baseline (what the Wi-Fi and default builds use) or any other variant.
 assert adsp_status==('okay' if mode=='adsp' else 'disabled'),(mode,adsp_status)
 results[mode]['adsp']=adsp_status
 if mode!='baseline':
  # F1: every stock no-map region stays covered, no overlaps, no baseline byte released.
  r=subprocess.run(['python3','-I',str(audit),str(dtb),str(stock),'--baseline',str(s/'baseline.dtb'),
                   '--adsp',adsp_status],capture_output=True,text=True)
  (s/(mode+'.reserved-audit.txt')).write_text(r.stdout+r.stderr)
  assert r.returncode==0 and 'dt-reserved-audit: PASS' in r.stdout,r.stdout+r.stderr
  status='okay' if mode in ('enabled','macros') else 'disabled'
  assert get('/sound-ess','status')==status
  assert get('/soc@0/geniqup@4ac0000/i2c@4a84000/audio-codec@48','status')==status
  assert get('/soc@0/geniqup@4ac0000/i2c@4a84000/audio-codec@48','reg','x')=='48'
  assert get('/sound-ess/mm1-dai-link/cpu','sound-dai','x').split()[-1]=='0'
  assert get('/sound-ess/ess-dai-link/cpu','sound-dai','x').split()[-1]=='12'
  assert get('/soc@0/remoteproc@a400000/glink-edge/apr/service@7/dais/dai@0','reg','x')=='0'
  assert get('/soc@0/remoteproc@a400000/glink-edge/apr/service@7/dais/dai@1','reg','x')=='1'
  assert get('/soc@0/remoteproc@a400000/glink-edge/apr/service@7/dais/dai@2','reg','x')=='2'
  children=subprocess.check_output([fdtget,'-l',str(dtb),'/soc@0/remoteproc@a400000/glink-edge/apr/service@7/dais'],text=True).splitlines()
  assert children==['dai@0','dai@1','dai@2'],children
# Negative control: the pre-review-33 DT (ADSP shrunk, no replacement
# reservations) must FAIL the audit, so the audit really detects F1.
import re
old=re.sub(r'/ \{\n\treserved-memory \{.*?\n\t\};\n\};\n','',(s/'a9-audio.dtsi').read_text(),count=1,flags=re.S)
assert 'memory@55617000' not in old and '&pil_adsp_mem' in old
(s/'a9-audio-pre33.dtsi').write_text(old)
(s/'validate.dts').write_text(a+'\n#include "a9-audio-pre33.dtsi"\n')
r=subprocess.run([cpp,'-nostdinc','-undef','-D__DTS__','-x','assembler-with-cpp','-I'+str(s),'-I'+str(k/'arch/arm64/boot/dts/qcom'),'-I'+str(k/'include'),str(s/'validate.dts')],capture_output=True,text=True);assert not r.returncode,r.stderr
(s/'pre33.pp.dts').write_text(r.stdout)
r=subprocess.run([dtc,'-I','dts','-O','dtb','-o',str(s/'pre33.dtb'),str(s/'pre33.pp.dts')],capture_output=True,text=True);assert not r.returncode,r.stderr
r=subprocess.run(['python3','-I',str(audit),str(s/'pre33.dtb'),str(stock),'--baseline',str(s/'baseline.dtb')],capture_output=True,text=True)
(s/'pre33.reserved-audit.txt').write_text(r.stdout+r.stderr)
assert r.returncode==1 and 'NOT COVERED' in r.stdout and 'no longer fully reserved' in r.stdout,r.stdout+r.stderr
r=subprocess.run(['python3','-I',str(audit),str(s/'disabled.dtb'),str(stock),'--adsp','okay'],capture_output=True,text=True)
assert r.returncode==1,r.stdout
for mode in ('disabled','adsp'):assert results[mode]['reserved']==results['baseline']['reserved'],mode
b=set(results['baseline']['reserved']);e=set(results['enabled']['reserved'])
assert b-e=={65,66,68,69},b-e
assert not e-b,e-b
for pin in [4,5,83,84,85]:assert pin not in e,pin
print('PASS: DT variants compile; stock reservations covered and ADSP disabled unless A9_AUDIO_ENABLE_ADSP; exact four ESS pins opened; frontlight/key pins preserved; I2C1@4a84000; FE order0/1/2; backend18decimal')
(s/'test-dt-result.json').write_text(json.dumps({'result':'PASS','removed_reservations':sorted(b-e),'added_reservations':sorted(e-b),'variants':list(results),'adsp':{m:results[m]['adsp'] for m in results}},indent=2)+'\n')
