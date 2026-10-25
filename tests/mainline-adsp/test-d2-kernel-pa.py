#!/usr/bin/env python3
"""Run actual A9 inner PA and control callbacks with hardware-only fault stubs."""
import argparse,hashlib,re,shutil,subprocess,tempfile,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser();p.add_argument('--source',required=True,type=Path);p.add_argument('--out',required=True,type=Path);a=p.parse_args()
assert shutil.disk_usage('/').free>=16_000_000_000
a.out.mkdir(parents=True,exist_ok=True)
h=(a.source/'sound/soc/codecs/wcd937x-a9-d2.h').read_text();c=(a.source/'sound/soc/codecs/wcd937x.c').read_text()
def fn(s,n):
 m=re.search(r'static [^\n]+ '+n+r'\([^;]*?\n\{.*?\n}\n',s,re.S);return m[0] if m else ''
names=('wcd937x_d2_contain','wcd937x_d2_check_route','wcd937x_update_bits','wcd937x_d2_read','wcd937x_d2_write_verify','wcd937x_d2_timeout')
actual='\n'.join(fn(h,n) for n in names)+'\n'+fn(c,'wcd937x_codec_enable_ear_pa')+'\n'+fn(c,'wcd937x_a9_d2_ear_pa')
fixed=bool(fn(c,'wcd937x_d2_gain_get'))
if fixed: actual+='\n'+'\n'.join(fn(c,n) for n in ('wcd937x_d2_gain_get','wcd937x_d2_gain_put','wcd937x_d2_mux_get','wcd937x_d2_mux_put','wcd937x_d2_switch_put'))
widget=c[c.index('static const struct snd_soc_dapm_widget wcd937x_d2_widgets'):c.index('static const struct snd_soc_dapm_route wcd937x_d2_routes')]
core=bool(re.search(r'PGA_E\("EAR PGA", WCD937X_ANA_EAR',widget))
base=Path(__file__).with_suffix('.c').read_text();known=set(re.findall(r'\bWCD937X_[A-Z0-9_]+\b',base));needed=set(re.findall(r'\b(?:WCD937X_[A-Z0-9_]+|WCD_CLSH_[A-Z0-9_]+|EAR_RX_PATH_AUX|CLS_AB_HIFI|CLS_AB)\b',actual))
constants='\n'.join('#define %s %d'%(n,i+12) for i,n in enumerate(sorted(needed-known)))+'\n#undef EAR_RX_PATH_AUX\n#define EAR_RX_PATH_AUX 1\n'
(a.out/'production.inc').write_text(actual)
(a.out/'test.c').write_text('#define CORE_PA_REGISTER %d\n#define FIXED_CONTROLS %d\n'%(core,fixed)+constants+base)
cc=shutil.which('cc') or str(sorted(Path('/nix/store').glob('*-gcc-wrapper-14.3.0/bin/cc'))[0])
subprocess.run([cc,'-std=c11','-O2','-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-unused-function','-Wno-unused-variable','-Wno-sign-compare',str(a.out/'test.c'),'-o',str(a.out/'test')],check=True)
r=subprocess.run([str(a.out/'test')],capture_output=True,text=True)
(a.out/'run.log').write_text(r.stdout+r.stderr)
receipt={'exit_code':r.returncode,'source':str(a.source),'source_sha256':{n:hashlib.sha256((a.source/n).read_bytes()).hexdigest() for n in ('sound/soc/codecs/wcd937x.c','sound/soc/codecs/wcd937x-a9-d2.h')},'production_sha256':hashlib.sha256(actual.encode()).hexdigest(),'test_py_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'test_c_sha256':hashlib.sha256(Path(__file__).with_suffix('.c').read_bytes()).hexdigest(),'generated_test_sha256':hashlib.sha256((a.out/'test.c').read_bytes()).hexdigest(),'core_register_backed':core,'fixed_controls':fixed}
(a.out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(r.stdout+r.stderr,end='');raise SystemExit(0 if r.returncode==0 else 1)
