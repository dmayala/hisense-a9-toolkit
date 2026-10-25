#!/usr/bin/env python3
"""Five host-only groups exercising production Music controls and I2C retries."""
import argparse
import json
import os
from pathlib import Path
import subprocess as sp

HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument('--out', type=Path, required=True)
out = ap.parse_args().out
out.mkdir(parents=True, exist_ok=True)
source = (HERE.parent/'driver/a9-aw87519.c').read_text()
amp = source[source.index('struct a9_aw {'):source.index('static const struct snd_soc_dapm_widget widgets[]')]
prelude = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;
#define SND_SOC_DAPM_PRE_PMU 1
#define SND_SOC_DAPM_PRE_PMD 2
#define dev_err(...) ((void)0)
struct regmap { int unused; };
struct gpio_desc { int value; };
struct mutex { bool held; };
struct firmware { size_t size; const u8 *data; };
struct device { int unused; };
struct snd_soc_component { struct device *dev; void *data; };
struct snd_soc_dapm_widget { struct snd_soc_component *dapm; };
struct snd_kcontrol { struct snd_soc_component *c; };
struct snd_ctl_elem_value { union { struct { unsigned item[128]; } enumerated; } value; };
#define snd_soc_component_get_drvdata(c) ((c)->data)
#define snd_soc_dapm_to_component(d) (d)
#define snd_kcontrol_chip(k) ((k)->c)
static unsigned reset_high, writes, retry_sleeps, elapsed;
static int failures, chip_id=0x59, read_error;
static void mutex_lock(struct mutex *m) { assert(!m->held);m->held=true; }
static void mutex_unlock(struct mutex *m) { assert(m->held);m->held=false; }
static void gpiod_set_value_cansleep(struct gpio_desc *g,int v) {g->value=v;if(v)reset_high++;}
static void usleep_range(unsigned lo,unsigned hi) {assert(lo<=hi);elapsed+=lo;}
static void msleep(unsigned ms) {assert(ms==2);retry_sleeps++;elapsed+=ms*1000;}
static int regmap_read(struct regmap *m,unsigned r,unsigned *v) {(void)m;assert(r==0);if(read_error)return -EIO;*v=chip_id;return 0;}
static int regmap_write(struct regmap *m,unsigned r,unsigned v) {
 (void)m;assert((r==0x69 && v==0xa5)||(r==1 && v==0xf0));writes++;
 if(failures){failures--;return -EIO;}return 0;
}
'''
main = r'''
int main(void) {
 const u8 bytes[]={0x69,0xa5,1,0xf0}; /* synthetic shape, no proprietary tuning */
 const struct firmware fw={sizeof(bytes),bytes};
 struct regmap map={0};struct gpio_desc gpio={0};struct device dev={0};
 struct a9_aw a={.map=&map,.reset=&gpio,.fw=&fw};
 struct snd_soc_component c={&dev,&a};struct snd_kcontrol k={&c};
 struct snd_soc_dapm_widget w={&c};struct snd_ctl_elem_value v={0};
 assert(!event(&w,&k,SND_SOC_DAPM_PRE_PMU) && !a.powered && !reset_high);
 v.value.enumerated.item[0]=1;
 assert(mode_put(&k,&v)==1 && a.music && a.powered && gpio.value && writes==2 && elapsed>=4000);
 assert(!mode_get(&k,&v) && v.value.enumerated.item[0]==1);
 unsigned n=writes,g=reset_high;
 assert(!mode_put(&k,&v) && writes==n && reset_high==g);
 puts("PASS group1 Music before any PCM/START powers stock scene; repeats idempotent");
 assert(!event(&w,&k,SND_SOC_DAPM_PRE_PMD) && !a.powered && a.music && !gpio.value);
 assert(!event(&w,&k,SND_SOC_DAPM_PRE_PMU) && a.powered && a.music && gpio.value);
 v.value.enumerated.item[0]=0;
 assert(mode_put(&k,&v)==1 && !a.music && !a.powered && !gpio.value);
 assert(!mode_put(&k,&v));
 assert(!event(&w,&k,SND_SOC_DAPM_PRE_PMU) && !a.powered);
 puts("PASS group2 DAPM off resets, same Music request restores; Off stays off");
 for(int f=0;f<5;f++) {
  failures=f;n=writes;unsigned pauses=retry_sleeps;
  v.value.enumerated.item[0]=1;
  assert(mode_put(&k,&v)==1 && a.powered && writes-n==(unsigned)(2+f) && retry_sleeps-pauses==(unsigned)f);
  v.value.enumerated.item[0]=0;assert(mode_put(&k,&v)==1);
 }
 puts("PASS group3 zero through four failed writes retry at2ms, then succeed");
 failures=5;n=writes;unsigned pauses=retry_sleeps;v.value.enumerated.item[0]=1;
 assert(mode_put(&k,&v)==-EIO && writes-n==5 && retry_sleeps-pauses==5 && !a.music && !a.powered && !gpio.value);
 assert(mode_put(&k,&v)==1 && a.powered);
 v.value.enumerated.item[0]=0;assert(mode_put(&k,&v)==1);
 puts("PASS group4 five failed writes abort/reset, later explicit Music can retry");
 n=writes;chip_id=0;v.value.enumerated.item[0]=1;
 assert(mode_put(&k,&v)==-ENODEV && !a.powered && !a.music && !gpio.value && writes==n);
 chip_id=0x59;read_error=1;
 assert(mode_put(&k,&v)==-EIO && !a.powered && !a.music && !gpio.value && writes==n);
 read_error=0;v.value.enumerated.item[0]=2;g=reset_high;
 assert(mode_put(&k,&v)==-EINVAL && writes==n && reset_high==g);
 puts("PASS group5 wrong chip/read error/invalid mode never leave amp enabled");
 return 0;
}
'''
p = out/'speaker-test.c'
p.write_text(prelude+amp+main)
env = dict(os.environ)
base = Path.home()/'A9-build/mainline/demo-integrated-20261019/source.json'
env['PATH'] = ':'.join(json.loads(base.read_text())['tools'])+':'+env['PATH']
sp.run(['gcc','-O2','-Wall','-Wextra','-Werror','-Wno-unused-parameter',str(p),'-o',str(out/'speaker-test')],env=env,check=True)
r = sp.run([str(out/'speaker-test')],capture_output=True,text=True)
(out/'speaker-test.log').write_text(r.stdout+r.stderr)
print(r.stdout+r.stderr)
r.check_returncode()
