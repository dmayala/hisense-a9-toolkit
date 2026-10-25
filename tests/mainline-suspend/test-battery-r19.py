#!/usr/bin/env python3
"""Compile actual battery PM/property/count functions with register-free mocks."""
from pathlib import Path
import subprocess,tempfile
ROOT=Path(__file__).resolve().parents[2]
src=(ROOT/'scripts/mainline/pmic/battery/pmi632-battery.c').read_text()
def piece(start,end):return src[src.index(start):src.index(end,src.index(start))]
functions='\n'.join([piece('static void count_charge(', 'static int classify('),piece('static int bat_get(', 'static enum power_supply_property usb_props'),piece('static int suspend(', 'static DEFINE_SIMPLE_DEV_PM_OPS')])
props=['MODEL_NAME','STATUS','HEALTH','PRESENT','TECHNOLOGY','VOLTAGE_NOW','CURRENT_NOW','TEMP','CAPACITY','CHARGE_COUNTER','CHARGE_FULL','CHARGE_FULL_DESIGN','CONSTANT_CHARGE_VOLTAGE_MAX','CONSTANT_CHARGE_CURRENT_MAX']
prefix=r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
typedef long long s64;
#define BIT(n) (1U<<(n))
#define WRITE_ONCE(x,v) ((x)=(v))
#define clamp_t(type,x,lo,hi) ((x)<(lo)?(lo):(x)>(hi)?(hi):(x))
#define div_s64(x,y) ((x)/(y))
#define div64_s64(x,y) ((x)/(y))
#define POWER_SUPPLY_TECHNOLOGY_LION 1
struct sample {int status1,usb,uv,ua,temp_dc,batif,qg,fv_raw,fcc_raw;};
struct battery {int lock,work;bool stopped,seeded,counter_gap,charge_unknown,full_anchor;int fcc_uah,last_ua,status,health,error;s64 last_ms,charge_uams;struct sample s;};
struct device {struct battery *b;};
struct power_supply {struct battery *b;};
union power_supply_propval {int intval;const char *strval;};
static struct battery *dev_get_drvdata(struct device*d){return d->b;}
static struct battery *power_supply_get_drvdata(struct power_supply*p){return p->b;}
static void mutex_lock(int*p){(void)p;}
static void mutex_unlock(int*p){(void)p;}
static int stop_ret,stop_count,schedules;
static int stop(struct battery*b){(void)b;stop_count++;return stop_ret;}
static void cancel_delayed_work_sync(int*p){(void)p;}
static int system_freezable_wq;
static void queue_delayed_work(int q,int*p,int n){assert(q==system_freezable_wq);(void)p;assert(n==0);schedules++;}
static s64 sampled_charge_delta(int previous,int now,s64 ms){return ((s64)previous+now)*ms/2;}
'''
prefix+='enum power_supply_property {'+','.join('POWER_SUPPLY_PROP_'+p for p in props)+'};\n'
main=r'''
static int property(struct battery*b,int prop){struct power_supply p={b};union power_supply_propval v={0};int rc=bat_get(&p,prop,&v);return rc?rc:v.intval;}
int main(void){
 struct battery b={.seeded=true,.fcc_uah=4000000,.last_ms=1000,.s={.uv=3900000,.qg=1,.temp_dc=250}};
 struct device d={&b};b.charge_uams=(s64)b.fcc_uah*3600000*60/100;s64 charge=b.charge_uams;
 assert(property(&b,POWER_SUPPLY_PROP_CAPACITY)==60);
 assert(!suspend(&d) && b.stopped && b.counter_gap && b.charge_unknown && !b.last_ms && !b.error);
 assert(property(&b,POWER_SUPPLY_PROP_CAPACITY)==60 && property(&b,POWER_SUPPLY_PROP_CHARGE_COUNTER)==-ENODATA);
 assert(!resume(&d) && !b.stopped && !b.counter_gap && b.charge_unknown && !b.last_ms);
 count_charge(&b,21000);assert(b.charge_uams==charge && b.last_ms==21000);assert(property(&b,POWER_SUPPLY_PROP_CAPACITY)==60);
 b.error=-EIO;assert(property(&b,POWER_SUPPLY_PROP_CAPACITY)==60);assert(property(&b,POWER_SUPPLY_PROP_VOLTAGE_NOW)==-ENODATA);
 assert(!suspend(&d) && b.error==-EIO);assert(!resume(&d) && b.error==-EIO);
 b.seeded=false;assert(property(&b,POWER_SUPPLY_PROP_CAPACITY)==-ENODATA);b.seeded=true;b.error=0;
 b.counter_gap=b.charge_unknown=false;b.last_ms=1000;count_charge(&b,7001);
 assert(b.counter_gap && b.charge_unknown && b.charge_uams==charge && property(&b,POWER_SUPPLY_PROP_CAPACITY)==60);
 b.s.status1=5;b.s.usb=BIT(4);b.s.uv=4450000;b.s.temp_dc=250;count_charge(&b,8001);
 assert(!b.counter_gap && !b.charge_unknown && b.full_anchor && property(&b,POWER_SUPPLY_PROP_CAPACITY)==100);
 assert(property(&b,POWER_SUPPLY_PROP_CHARGE_COUNTER)==4000000);
 stop_ret=-EIO;assert(suspend(&d)==-EIO && !b.stopped && b.charge_unknown);assert(stop_count==3 && schedules==3);
 puts("R19 actual source PASS: cached SOC across suspend, no sleep integration, gap/charge uncertainty, preserved real errors, unseeded refusal, measured full anchor and failed-suspend restart");
}
'''
with tempfile.TemporaryDirectory() as td:
 p=Path(td)/'test.c';p.write_text(prefix+functions+main);binary=Path(td)/'test'
 cc='/nix/store/23wxjvr0712zdrjlkg3p4533g8f8g1if-gcc-wrapper-14.3.0/bin/gcc'
 subprocess.run([cc,'-std=gnu11','-O0','-Wall','-Wextra','-Werror','-Wno-unused-parameter',str(p),'-o',str(binary)],check=True)
 subprocess.run([str(binary)],check=True)
