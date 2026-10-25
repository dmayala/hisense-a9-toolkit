#!/usr/bin/env python3
"""Production clock-hold ownership/failure tests and Android loader rehearsal."""
import json
import os
from pathlib import Path
import subprocess as sp

HERE = Path(__file__).resolve().parent
ROOT = Path.home()/'A9-build/mainline'
OUT = ROOT/'audio-output-20261020/stall'
OUT.mkdir(parents=True, exist_ok=True)
header = (HERE.parent/'driver/a9-clock-hold.h').read_text().replace('#include <linux/clk.h>', '')
prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
#define ARRAY_SIZE(a) (sizeof(a)/sizeof(a[0]))
#define IS_ERR(p) ((uintptr_t)(p)>(uintptr_t)-4096)
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define ERR_PTR(e) ((void *)(intptr_t)(e))
struct device_node { const char *compat; unsigned index; };
struct device { struct device_node *of_node; };
struct snd_soc_component { struct device *dev; struct snd_soc_component *next; };
struct snd_soc_card { struct snd_soc_component *first; };
struct of_phandle_args { struct device_node *np; };
struct clk { unsigned gets, prepares; unsigned long rate; };
#define for_each_card_components(card,c) for(c=(card)->first;c;c=c->next)
static struct device_node va={"qcom,sm6115-lpass-va-macro",0};
static struct clk clocks[6];
static unsigned get_attempts, prepare_attempts, on_packets, off_packets, nodes;
static int fail_get=-1, fail_prepare=-1, fail_index, fail_parse;
static bool ccf_locked;
static unsigned release_order[6], releases;
static bool of_device_is_compatible(struct device_node *n,const char *s) {
 return n && !strcmp(n->compat,s);
}
static int of_property_match_string(struct device_node *n,const char *p,const char *s) {
 (void)n;assert(!strcmp(p,"clock-names") && !strcmp(s,"fsgen"));return fail_index?-ENOENT:3;
}
static int of_parse_phandle_with_args(struct device_node *n,const char *p,const char *c,int i,struct of_phandle_args *a) {
 (void)n;assert(!strcmp(p,"clocks") && !strcmp(c,"#clock-cells") && i==3);
 if(fail_parse)return -EINVAL;a->np=&va;nodes++;return 0;
}
static void of_node_put(struct device_node *n) { assert(n==&va && nodes);nodes--; }
static struct clk *of_clk_get_by_name(struct device_node *n,const char *name) {
 unsigned id=n->index*2+(strcmp(name,"mclk")!=0);
 assert(id<6 && (!strcmp(name,"mclk") || !strcmp(name,"npl")));
 if((int)get_attempts++==fail_get)return ERR_PTR(-ENOENT);
 clocks[id].gets++;return &clocks[id];
}
static unsigned long clk_get_rate(struct clk *c) { return c->rate; }
static void clk_put(struct clk *c) { assert(c->gets);c->gets--; }
/* Model the real CCF 0->1 / 1->0 provider rule, with acknowledged APR votes.
 * A packet occurs under CCF only at the outer card-bind/removal transition.
 * This does not emulate the DSP or prove the display waited on the mutex. */
static int clk_prepare(struct clk *c) {
 assert(!ccf_locked);ccf_locked=true;
 if((int)prepare_attempts++==fail_prepare){ccf_locked=false;return -ETIMEDOUT;}
 if(!c->prepares++)on_packets++;
 ccf_locked=false;return 0;
}
static void clk_unprepare(struct clk *c) {
 assert(!ccf_locked && c->prepares);ccf_locked=true;
 if(!--c->prepares)off_packets++;
 ccf_locked=false;
 if(releases<6)release_order[releases++]=(unsigned)(c-clocks);
}
'''
main = r'''
static void clean(void) {
 for(unsigned i=0;i<6;i++)assert(!clocks[i].gets && !clocks[i].prepares);
 assert(!nodes && !ccf_locked);
}
static void reset(void) {
 clean();memset(clocks,0,sizeof(clocks));
 for(unsigned i=0;i<6;i++)clocks[i].rate=19200000;
 get_attempts=prepare_attempts=on_packets=off_packets=releases=0;
 fail_get=fail_prepare=-1;fail_index=fail_parse=0;
}
int main(void) {
 struct device_node rx={"qcom,sm6115-lpass-rx-macro",2},tx={"qcom,sm6115-lpass-tx-macro",1};
 struct device rd={&rx},td={&tx};
 struct snd_soc_component tc={&td,NULL},rc={&rd,&tc};struct snd_soc_card card={&rc};
 struct a9_clock_hold h={0};reset();
 assert(!a9_clock_hold_start(&card,&h) && h.count==6 && on_packets==6);
 assert(!a9_clock_hold_start(&card,&h) && get_attempts==6 && prepare_attempts==6);
 releases=6; /* Ignore stream unprepare records; inspect final lease release. */
 for(unsigned run=0;run<100;run++)for(unsigned i=0;i<6;i++) {
  assert(!clk_prepare(&clocks[i]));clk_unprepare(&clocks[i]);
 }
 assert(on_packets==6 && !off_packets && h.count==6);
 releases=0;a9_clock_hold_release(&h);
 assert(off_packets==6 && !h.count);
 for(unsigned i=0;i<6;i++)assert(release_order[i]==5-i);
 a9_clock_hold_release(&h);clean();
 puts("PASS boot hold acknowledged; 100 stream cycles send no clock APR; reverse/idempotent cleanup");
 reset();for(unsigned i=0;i<6;i++)clocks[i].prepares=1;
 assert(!a9_clock_hold_start(&card,&h) && !on_packets);
 releases=6;for(unsigned i=0;i<6;i++)clk_unprepare(&clocks[i]);
 assert(!off_packets);a9_clock_hold_release(&h);assert(off_packets==6);clean();
 puts("PASS pre-existing macro users transfer to extra hold without an extra provider transition");
 for(unsigned i=0;i<6;i++) {
  reset();fail_get=i;assert(a9_clock_hold_start(&card,&h)==-ENOENT && !h.count);clean();
  reset();fail_prepare=i;assert(a9_clock_hold_start(&card,&h)==-ETIMEDOUT && !h.count);clean();
  reset();clocks[i].rate=22579200;assert(a9_clock_hold_start(&card,&h)==-EINVAL && !h.count);clean();
 }
 reset();assert(!a9_clock_hold_start(&card,&h));a9_clock_hold_release(&h);clean();
 puts("PASS every get/rate/ACK failure unwinds all references; a later retry succeeds");
 reset();card.first=&tc;assert(a9_clock_hold_start(&card,&h)==-ENODEV);clean();card.first=&rc;
 fail_index=1;assert(a9_clock_hold_start(&card,&h)==-ENOENT);clean();fail_index=0;
 fail_parse=1;assert(a9_clock_hold_start(&card,&h)==-EINVAL);clean();fail_parse=0;
 va.compat="wrong";assert(a9_clock_hold_start(&card,&h)==-ENODEV);clean();
 puts("PASS missing macro/clock topology refuses opt-in rather than silently claiming a hold");
 return 0;
}
'''
env = dict(os.environ)
env['PATH'] = ':'.join(json.loads((ROOT/'demo-integrated-20261019/source.json').read_text())['tools'])+':'+env['PATH']
p = OUT/'clock-hold-test.c'
p.write_text(prelude+header+main)
sp.run(['gcc','-O2','-Wall','-Wextra','-Werror','-Wno-misleading-indentation',str(p),'-o',str(OUT/'clock-hold-test')],env=env,check=True)
r = sp.run([str(OUT/'clock-hold-test')],capture_output=True,text=True)
(OUT/'clock-hold-test.log').write_text(r.stdout+r.stderr)
print(r.stdout+r.stderr)
assert r.returncode == 0

# Rehearse the actual load branch with paths redirected to a fake filesystem.
# No device scripts or real module syscalls are invoked.
QEMU = '/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64'
SYSTEM = ROOT/'adsp/d2-v8-20261017/system-image/system'
MKSH = SYSTEM/'bin/sh'
test = OUT/'loader-test'
test.mkdir(exist_ok=True)
sysroot = ROOT/'audio-output-20261020/tests/sysroot'
source = (HERE.parent/'a9ml-audio.sh').read_text()
start = source.index(' load)')+len(' load)')
branch = source[start:source.index('  ;;',start)]
modules = test/'modules'
modules.mkdir(exist_ok=True)
(modules/'modules.audio-poc').write_text('snd-soc-a9-aw87519.ko\nsnd-soc-a9-audio.ko\n')
sysmodules = test/'sys'
sysmodules.mkdir(exist_ok=True)
branch = branch.replace('/vendor/lib/modules/',str(modules)+'/').replace('/sys/module/',str(sysmodules)+'/')
pre = '''set -eu
getprop() { [ "$1" = ro.boot.a9_audio_clock_hold ]; printf '%s\\n' "$FLAG"; }
insmod() { printf '%s\\n' "$*" >> "$LOG"; [ "${FAIL:-0}" != 1 ]; }
tr() { value=$(</dev/stdin); value=${value//-/_}; printf '%s' "$value"; }
'''
script = test/'load.sh'
script.write_text(pre+branch)
base = [QEMU,'-L',str(sysroot),'-E','LD_LIBRARY_PATH='+str(SYSTEM/'lib64/bootstrap')+':'+str(SYSTEM/'lib64'),str(MKSH)]
sp.run(base+['-n',str(HERE.parent/'a9ml-audio.sh')],check=True)
sp.run(base+['-n',str(script)],check=True)
cases = []
for flag, already, fail in [('0',False,False),('1',False,False),('garbage',False,False),('1',True,False),('1',False,True)]:
    loaded = sysmodules/'snd_soc_a9_audio'
    if already: loaded.mkdir(exist_ok=True)
    elif loaded.exists(): loaded.rmdir()
    log = test/'insmod.log'
    log.unlink(missing_ok=True)
    r = sp.run(base+['-c',f'FLAG={flag} LOG={log} FAIL={int(fail)} . {script}'],capture_output=True,text=True)
    text = log.read_text() if log.exists() else ''
    expected = 1 if flag=='1' and not already and not fail else 0
    assert text.count('lpass_clock_hold=1') == expected, text
    assert (r.returncode != 0) == fail, r.stderr
    assert text.count('snd-soc-a9-audio.ko') == (0 if already or fail else 1), text
    cases.append({'flag':flag,'already_loaded':already,'fail_insmod':fail,'rc':r.returncode,'output':text,'stderr':r.stderr})
(OUT/'loader-test.json').write_text(json.dumps(cases,indent=2)+'\n')
print('PASS Android mksh loader: default/opt-in/malformed/already-loaded/insmod-failure')
