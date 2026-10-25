#!/usr/bin/env python3
"""Smoke actual battery poll/notification/vote/PM functions with register-free mocks."""
from pathlib import Path
import os
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
source = (ROOT / 'scripts/mainline/pmic/battery/pmi632-battery.c').read_text()
def piece(start, end):
    return source[source.index(start):source.index(end, source.index(start))]

prefix = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef long long s64;
typedef unsigned long long u64;
struct alarm {int dummy;};
struct a9_qg_config {int dummy;};
#define BIT(n) (1U << (n))
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define div64_s64(x,y) ((x)/(y))
#define HZ 100
#define POWER_SUPPLY_HEALTH_UNSPEC_FAILURE 7
#define POWER_SUPPLY_STATUS_NOT_CHARGING 3
#define POWER_SUPPLY_STATUS_DISCHARGING 2
#define POWER_SUPPLY_STATUS_FULL 4
#define POWER_SUPPLY_STATUS_CHARGING 1
static bool event_debug;
#define dev_info(...) ((void)0)
#define dev_warn_ratelimited(...) ((void)0)
#define dev_err_ratelimited(...) ((void)0)
#define container_of(p,t,m) ((t *)((char *)(p)-offsetof(t,m)))
#define to_delayed_work(p) container_of(p,struct delayed_work,work)
struct work_struct {int dummy;};
struct delayed_work {struct work_struct work;};
struct mutex {int dummy;};
struct iio_channel;struct regmap;struct battery;
struct device {struct battery *b;};
struct power_supply {struct battery *b;int changed;};
struct a9_sample {
 int uv,ua,temp_dc,vbus_uv;
 unsigned int usb,batif,qg,status1,status2,status7,die,conn,otg,apsd,source,qgmode;
 unsigned int enabled,suspend,powerpath,fv_raw,fcc_raw,icl_raw;
 int temp_valid;
};
static int queue, system_freezable_wq = 19;
static int samples, stops, stop_ret, sample_ret, cancelled;
static s64 ktime_get_boottime(void){return 1000;}
static s64 ktime_to_ms(s64 n){return n;}
static void mutex_lock(struct mutex *p){(void)p;}
static void mutex_unlock(struct mutex *p){(void)p;}
static int queue_delayed_work(int q,struct delayed_work *w,int delay){
 (void)w;assert(q==system_freezable_wq);assert(delay==HZ||delay==0);queue++;return 1;
}
static int mod_delayed_work(int q,struct delayed_work *w,int delay){return queue_delayed_work(q,w,delay);}
static void cancel_delayed_work_sync(struct delayed_work *w){(void)w;cancelled++;}
static int regmap_read(struct regmap *m,unsigned reg,unsigned *v){(void)m;(void)reg;(void)v;return 0;}
static void power_supply_changed(struct power_supply *p){p->changed++;}
union power_supply_propval {int intval;};
enum power_supply_property {POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT,OTHER_PROP};
'''
functions = piece('struct battery_notice {', 'static int update(')
stubs = r'''
static struct battery *power_supply_get_drvdata(struct power_supply *p){return p->b;}
static struct battery *dev_get_drvdata(struct device *d){return d->b;}
static int sample(struct battery *b,struct a9_sample *s){samples++;*s=b->s;return sample_ret;}
static int classify(struct a9_sample *s){(void)s;return 1;}
static int seed_counter(struct battery *b){b->seeded=true;return 0;}
static void count_charge(struct battery *b,s64 now){(void)b;(void)now;}
static int input_policy(struct battery *b){(void)b;return 0;}
static int drain_restore_locked(struct battery *b){(void)b;return 0;}
static int charge_policy(struct battery *b){(void)b;return 0;}
static int stop(struct battery *b){(void)b;stops++;return stop_ret;}
static int a9_observed_online(const struct a9_sample *s){return !!(s->usb&BIT(4))&&!(s->suspend&1);}
static int stock_usb_current(int ua){return ua;}
'''
functions += stubs
functions += (ROOT / "scripts/mainline/pmic/battery/status.h").read_text()
functions += piece('static unsigned int changed_supplies(', 'static enum power_supply_property bat_props')
functions += piece('static int usb_set(', 'static int usb_writeable(')
functions += piece('static int suspend(', 'static DEFINE_SIMPLE_DEV_PM_OPS')
main = r'''
int main(void){
 struct battery b={.seeded=true,.fcc_uah=4000000,.usb_ua=500000,.usb_voted=true,
  .s={.uv=4400000,.ua=0,.temp_dc=250,.qg=1,.usb=BIT(4),.enabled=1,.fv_raw=85,.fcc_raw=60}};
 struct power_supply bat={.b=&b},usb={.b=&b};struct device d={&b};b.bat=&bat;b.usb=&usb;
 b.charge_uams=(s64)4000000*3600000;
 poll(&b.work.work);assert(samples==1&&bat.changed==1&&usb.changed==1);
 for(int i=0;i<50;i++){b.s.uv++;b.s.ua++;b.s.temp_dc=250+i%9;poll(&b.work.work);}
 assert(samples==51&&bat.changed==1&&usb.changed==1&&queue==51);
 puts("1 PASS: actual 51 polls with analog noise emit only initial notifications");
 b.charge_uams=(s64)4000000*3600000*99/100;poll(&b.work.work);assert(bat.changed==2&&usb.changed==1);
 b.s.temp_dc=b.bat_notice.temp_dc+10;poll(&b.work.work);assert(bat.changed==3);
 sample_ret=-EIO;poll(&b.work.work);assert(b.fault&&bat.changed==4&&usb.changed==2&&stops==1);
 poll(&b.work.work);assert(bat.changed==4&&usb.changed==2&&stops==2);sample_ret=0;
 puts("2 PASS: SOC, temperature and first error notify; repeated fault does not flood");
 b.s.usb=0;poll(&b.work.work);assert(usb.changed==3);
 b.s.usb=BIT(4);poll(&b.work.work);assert(usb.changed==4);
 b.input_ua=900000;poll(&b.work.work);assert(usb.changed==5);
 puts("3 PASS: USB detach, attach and input policy changes notify");
 int q=queue;union power_supply_propval v={.intval=500000};
 assert(!usb_set(&usb,POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT,&v)&&queue==q);
 v.intval=900000;assert(!usb_set(&usb,POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT,&v)&&queue==q+1);
 assert(usb_set(&usb,OTHER_PROP,&v)==-EINVAL);
 puts("4 PASS: duplicate DWC3 current votes do not queue work; changed votes use freezable queue");
 s64 charge=b.charge_uams;b.error=0;int reads=samples;
 assert(!suspend(&d)&&b.stopped&&b.counter_gap&&b.charge_unknown&&cancelled==1);
 poll(&b.work.work);assert(samples==reads);q=queue;v.intval=0;
 assert(!usb_set(&usb,POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT,&v)&&queue==q);
 assert(!resume(&d)&&!b.stopped&&!b.counter_gap&&!b.last_ms&&b.charge_uams==charge&&queue==q+1);
 stop_ret=-EIO;q=queue;assert(suspend(&d)==-EIO&&!b.stopped&&queue==q+1);
 puts("5 PASS: stopped poll/vote cannot touch hardware; resume and failed suspend restart on freezable queue, cached SOC retained");
}
'''
with tempfile.TemporaryDirectory(prefix='a9-battery-notify-') as tmp:
    c = Path(tmp) / 'smoke.c'; exe = Path(tmp) / 'smoke'
    c.write_text(prefix + functions + main)
    cc = os.environ.get('CC', '/nix/store/23wxjvr0712zdrjlkg3p4533g8f8g1if-gcc-wrapper-14.3.0/bin/gcc')
    subprocess.run([cc, '-std=gnu11', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
