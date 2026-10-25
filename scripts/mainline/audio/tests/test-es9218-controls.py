#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile actual production callback bodies with host-only kernel/I/O stubs."""
import argparse, pathlib, tempfile, subprocess, json, re
p=argparse.ArgumentParser();p.add_argument('--driver',type=pathlib.Path,required=True);p.add_argument('--cc',default='cc');p.add_argument('--output-dir',type=pathlib.Path)
a=p.parse_args();driver=a.driver.resolve();src=driver.read_text()
out=a.output_dir.resolve() if a.output_dir else pathlib.Path(tempfile.mkdtemp(prefix='a9-es9218-controls-'));out.mkdir(parents=True,exist_ok=True)
def function(name):
 m=re.search(r'static (?:int|void) '+re.escape(name)+r'\(',src);assert m,name
 start=m.start();pos=src.index('{',start);depth=1;i=pos+1
 while depth:
  depth+=(src[i]=='{')-(src[i]=='}');i+=1
 return src[start:i]
prefix=r'''
#include <stdio.h>
#include <assert.h>
#include <string.h>
#include <limits.h>
HEADER
struct es9218_a9 { int lock; struct es9218_seq seq; bool dapm_active; unsigned int users; int fault; unsigned int volume[2]; };
struct snd_soc_component { struct es9218_a9 *es; };
struct snd_kcontrol { struct snd_soc_component *component; };
struct snd_soc_dapm_widget { struct snd_soc_component *dapm; };
struct snd_ctl_elem_value { union { struct { long value[2]; } integer; } value; };
struct snd_soc_dai { struct snd_soc_component *component; };
struct snd_pcm_substream { int unused; };
struct device { struct es9218_a9 *es; };
static struct es9218_a9 *dev_get_drvdata(struct device *d) { return d->es; }
#define dev_warn(...) ((void)0)
#define SND_SOC_DAPM_POST_PMU 1
#define SND_SOC_DAPM_PRE_PMD 2
#define SNDRV_PCM_STREAM_PLAYBACK 0
#define SNDRV_PCM_TRIGGER_START 0
#define SNDRV_PCM_TRIGGER_RESUME 1
#define SNDRV_PCM_TRIGGER_PAUSE_RELEASE 2
#define READ_ONCE(x) (x)
static void mutex_lock(int *x) { (void)x; }
static void mutex_unlock(int *x) { (void)x; }
static void usleep_range(int a, int b) { (void)a;(void)b; }
static struct snd_soc_component *snd_kcontrol_chip(struct snd_kcontrol *k) { return k->component; }
static struct snd_soc_component *snd_soc_dapm_to_component(struct snd_soc_component *c) { return c; }
static struct es9218_a9 *snd_soc_component_get_drvdata(struct snd_soc_component *c) { return c->es; }
static int pins[4],wr_count,fail_at;
static unsigned int registers[0x41];
static int wr(void *p,unsigned int reg,unsigned int val) { (void)p;if (++wr_count==fail_at)return -EREMOTEIO;registers[reg]=val;return 0; }
static int rd(void *p,unsigned int reg,unsigned int *val) { (void)p;*val=reg==0x40 ? 0xc0 : registers[reg];return 0; }
static void pin(void *p,enum es9218_pin n,int v) { (void)p;pins[n]=v; }
static void delay(void *p,unsigned int min,unsigned int max) { (void)p;(void)min;(void)max; }
static int es_write(void *p,unsigned int r,unsigned int v) { return wr(p,r,v); }
static const struct es9218_io io={wr,rd,pin,delay};
'''.replace('HEADER','#include '+json.dumps(str(driver.with_name('es9218-a9-seq.h'))))
main=r'''
static void reset(struct es9218_a9 *es) {
 memset(es,0,sizeof(*es));es->seq=(struct es9218_seq){.io=&io,.mode=ES_HIFI,.registers_live=true,.width=32};
 es->dapm_active=true;es->users=1;es->volume[0]=255;es->volume[1]=255;
 memset(registers,0,sizeof(registers));memset(pins,0,sizeof(pins));pins[ES_PA]=pins[ES_OSC]=pins[ES_RESET]=1;wr_count=0;fail_at=0;
}
int main(void) {
 struct es9218_a9 es;struct snd_soc_component component={&es};struct snd_kcontrol k={&component};struct snd_soc_dai dai={&component};
 struct snd_soc_dapm_widget widget={&component};
 struct snd_ctl_elem_value u={.value.integer.value={128,128}};int result;
 for(int f=1;f<=2;f++) {
  reset(&es);fail_at=f;u.value.integer.value[0]=u.value.integer.value[1]=128;
  result=es_volume_put(&k,&u);assert(result==-EREMOTEIO && es.fault==-EREMOTEIO);
  assert(es.seq.mode==ES_STANDBY && !pins[ES_PA] && !pins[ES_OSC]);
  assert(es.volume[0]==255 && es.volume[1]==255);
  u.value.integer.value[0]=1;assert(es_bypass_put(&k,&u)==-EBUSY && !pins[ES_PA]);
  es.users=0;assert(es_bypass_put(&k,&u)==-EBUSY && !pins[ES_PA]);
  es.dapm_active=false;assert(es_bypass_put(&k,&u)==1 && pins[ES_PA]);
 }
 reset(&es);fail_at=1;assert(es_dai_mute(&dai,1,SNDRV_PCM_STREAM_PLAYBACK)==-EREMOTEIO);
 assert(es.fault==-EREMOTEIO && es.seq.mode==ES_STANDBY && !pins[ES_PA]);
 u.value.integer.value[0]=1;assert(es_bypass_put(&k,&u)==-EBUSY && !pins[ES_PA]);
 assert(es_trigger(NULL,SNDRV_PCM_TRIGGER_START,&dai)==-EREMOTEIO);
 reset(&es);es.users=0;es.dapm_active=false;es.seq.mode=ES_STANDBY;es.seq.registers_live=false;
 assert(es_startup(NULL,&dai)==0 && es.users==1);
 assert(es_startup(NULL,&dai)==-EBUSY);
 assert(es_bypass_put(&k,&u)==-EBUSY);
 es_pcm_shutdown(NULL,&dai);assert(es.users==0 && es.seq.mode==ES_STANDBY);
 assert(es_bypass_put(&k,&u)==1 && pins[ES_PA]);
 reset(&es);
 struct device dev={&es};assert(es_suspend(&dev)==0);
 assert(es.seq.mode==ES_STANDBY && es.seq.width==0 && es.fault==-EHOSTDOWN);
 assert(!pins[ES_PA] && !pins[ES_OSC] && !pins[ES_RESET]);
 u.value.integer.value[0]=1;assert(es_bypass_put(&k,&u)==-EBUSY);
 assert(es_trigger(NULL,SNDRV_PCM_TRIGGER_RESUME,&dai)==-EHOSTDOWN);
 reset(&es);
 long invalid[]={-1,256,LONG_MAX,(long)1<<32};
 for(unsigned i=0;i<sizeof(invalid)/sizeof(invalid[0]);i++) {
  u.value.integer.value[0]=invalid[i];u.value.integer.value[1]=128;wr_count=0;
  assert(es_volume_put(&k,&u)==-EINVAL && wr_count==0);
 }

 /* A legal DAPM graph power-cycle keeps an already configured/open PCM.
  * This models endpoint pin off/sync/on/sync, not plain ALSA STOP itself. */
 unsigned int widths[]={16,24,32};
 for(unsigned int n=0;n<sizeof(widths)/sizeof(widths[0]);n++) {
  reset(&es);es.seq.mode=ES_STANDBY;es.seq.registers_live=false;es.seq.width=widths[n];es.dapm_active=false;
  es.volume[0]=17;es.volume[1]=33;
  assert(es_dac_event(&widget,NULL,SND_SOC_DAPM_POST_PMU)==0 && es.seq.mode==ES_HIFI);
  for(unsigned int cycle=0;cycle<3;cycle++) {
   assert(es_dac_event(&widget,NULL,SND_SOC_DAPM_PRE_PMD)==0);
   assert(es.seq.width==widths[n] && es.users==1 && !es.dapm_active && es.seq.mode==ES_STANDBY);
   assert(!pins[ES_PA] && !pins[ES_OSC] && !pins[ES_RESET]);
   u.value.integer.value[0]=1;assert(es_bypass_put(&k,&u)==-EBUSY);
   assert(es_dac_event(&widget,NULL,SND_SOC_DAPM_POST_PMU)==0);
   assert(es.seq.width==widths[n] && es.users==1 && es.dapm_active && es.seq.mode==ES_HIFI && !es.fault);
   assert(registers[0x0f]==17 && registers[0x10]==33);
  }
  assert(es_dac_event(&widget,NULL,SND_SOC_DAPM_PRE_PMD)==0);
  es_pcm_shutdown(NULL,&dai);
  assert(es.seq.width==0 && es.users==0 && es.seq.mode==ES_STANDBY);
  int before=wr_count;
  assert(es_dac_event(&widget,NULL,SND_SOC_DAPM_POST_PMU)==-EINVAL);
  assert(wr_count==before && !pins[ES_PA] && !pins[ES_OSC] && !pins[ES_RESET]);
  reset(&es);es.seq.mode=ES_STANDBY;es.seq.registers_live=false;es.seq.width=widths[n];es.dapm_active=false;
  assert(es_dac_event(&widget,NULL,SND_SOC_DAPM_POST_PMU)==0);
  assert(es_suspend(&dev)==0 && es.seq.width==0 && es.users==1 && es.fault==-EHOSTDOWN);
  before=wr_count;
  assert(es_dac_event(&widget,NULL,SND_SOC_DAPM_POST_PMU)==-EINVAL);
  assert(wr_count==before && !pins[ES_PA] && !pins[ES_OSC] && !pins[ES_RESET]);
 }
 puts("PASS actual callbacks: fault ownership, DAPM-only ownership, idle bypass, mute START fault, open/close interlock, suspend containment, 64-bit volume bounds, DAPM cycles retain 16/24/32-bit params, close/suspend invalidate params");
 return 0;
}
'''
code=prefix+'\n'.join(function(n) for n in ['es_volume_put','es_bypass_put','es_dai_mute','es_trigger','es_startup','es_pcm_shutdown','es_suspend','es_dac_event'])+main
(out/'controls.c').write_text(code)
subprocess.run([a.cc,'-std=c11','-O2','-Wall','-Wextra','-Werror','-Wno-unused-parameter',str(out/'controls.c'),'-o',str(out/'controls')],check=True)
r=subprocess.run([str(out/'controls')],cwd=out,capture_output=True,text=True)
(out/'result.log').write_text(r.stdout+r.stderr);print(r.stdout+r.stderr,end='');r.check_returncode()
