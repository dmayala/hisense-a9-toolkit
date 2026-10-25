#!/usr/bin/env python3
"""Compile production PG decision/enable paths; controlled GPIO/API replay."""
import argparse
import os
import subprocess as sp
from pathlib import Path

MAIN = Path(__file__).resolve().parents[2]


def function(s, signature):
    start = s.index(signature)
    end = s.index('{', start)+1
    depth = 1
    while depth:
        depth += (s[end] == '{')-(s[end] == '}')
        end += 1
    return s[start:end]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--stage', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    driver = (MAIN/'eink/driver/a9_eink_drv.c').read_text()
    pmic = (a.stage/'pmic/drivers/regulator/fp9929a.c').read_text()
    assert 'a9_pg_settle(' not in driver and 'a9_rate_ok(' not in driver
    assert 'pg_settle_budget' not in driver and 'pg_settle_total_max' not in driver
    assert 'fp9929a_wait_pg(' not in pmic and 'pg_timeout_ms' not in pmic
    observer = function(driver, 'static void a9_pg_wf_end(')
    for forbidden in ('a9_check_rail_sample(', 'a9_rail_fault(', 'a9_power_off(',
                      'a9_power_on(', 'atomic_set(', 'WRITE_ONCE(', 'usleep_range('):
        assert forbidden not in observer
    code = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <errno.h>
typedef int64_t ktime_t;
typedef int8_t s8;
#define READ_ONCE(v) (v)
#define WRITE_ONCE(v,x) ((v)=(x))
#define mutex_lock(...) ((void)0)
#define mutex_unlock(...) ((void)0)
#define atomic_read(p) (*(p))
#define dev_warn(...) ((void)0)
#define dev_err(...) ((void)0)
#define dev_err_ratelimited(...) ((void)0)
#define dev_info(dev,fmt,...) snprintf(logline,sizeof(logline),fmt,##__VA_ARGS__)
#define msecs_to_jiffies(v) (v)
#define mod_delayed_work(...) (++timer_armed)
#define ktime_get() now_us
#define ktime_us_delta(a,b) ((a)-(b))
#define FP9929A_REG_TMST 0
#define MAX_JIFFY_OFFSET 999999
struct fp9929a_status {bool on,forced_off,emergency,pg_valid,pg_good;int pg_raw;};
struct a9_eink {int unused;};
static unsigned sampled,latched,off,reports;
static void a9_diag_rail_sample(struct a9_eink*a,bool on,int r,const struct fp9929a_status*s){sampled++;}
static void a9_rail_fault(struct a9_eink*a,int r,const struct fp9929a_status*s){assert(latched==off&&off==reports);latched++;}
static void a9_power_off(struct a9_eink*a){assert(latched==off+1);off++;}
static void a9_rail_report(struct a9_eink*a){assert(off==reports+1);reports++;}
static bool allow_power_on=true,trip_after_delay;
static int fp9929a_emerg,fp9929a_gate_lock,timer_armed;
static unsigned max_on_ms=20000;
static long jiffies,now_us;
static char logline[256];
struct fp9929a {const char*vcom_src;bool on,forced_off,have_temp,expired,energized;
 int lock,max_on_work,temp_c;unsigned max_on_ms,vcom_mv,vcom_code;
 long deadline,vcom_on_at;int *pwrup,*vcom_en,*pg;void *dev,*client;};
static int vcom_error;
static bool fp9929a_fault_latched(struct fp9929a*f){return f->forced_off;}
static int fp9929a_min_off_wait(struct fp9929a*f){return 0;}
static bool fp9929a_gate_open(struct fp9929a*f){return allow_power_on&&!f->expired&&!fp9929a_emerg;}
static void fp9929a_abort_power_up(struct fp9929a*f){*f->pwrup=*f->vcom_en=0;f->on=f->energized=false;}
static int fp9929a_gate_closed(struct fp9929a*f,const char*w){fp9929a_abort_power_up(f);return -EIO;}
static int fp9929a_write_vcom(struct fp9929a*f){return vcom_error;}
static int fp9929a_check_vcom(struct fp9929a*f){return 0;}
static int i2c_smbus_read_byte_data(void*c,int r){return 28;}
static int gpiod_get_value_cansleep(int*p){return *p;}
static void gpiod_set_value_cansleep(int*p,int v){*p=v;}
static void usleep_range(unsigned lo,unsigned hi){now_us+=lo;if(trip_after_delay&&lo==20000)fp9929a_emerg=1;}
'''
    for signature in ('static bool a9_hv_good(', 'static int a9_check_rail_sample('):
        code += function(driver, signature)+'\n'
    code += function(pmic, 'static int __fp9929a_enable(')+'\n'
    code += r'''
static void reset(struct fp9929a*f,int*pg,int*p,int*v){
 *p=*v=0;now_us=0;timer_armed=0;allow_power_on=true;fp9929a_emerg=0;vcom_error=0;trip_after_delay=false;
 *f=(struct fp9929a){.vcom_src="cmdline",.pg=pg,.pwrup=p,.vcom_en=v,.vcom_mv=2730,.vcom_code=126};
}
int main(void){
 struct a9_eink a={0};struct fp9929a_status s={.on=true,.pg_valid=true};
 /* More than the old session/boot quotas, including an arbitrarily dense storm. */
 for(unsigned i=0;i<100000;i++) {s.pg_raw=(i%3)?0:-EIO;s.pg_good=false;
   s.pg_valid=(i%2)==0;assert(!a9_check_rail_sample(&a,true,0,&s));}
 assert(sampled==100000&&!latched&&!off&&!reports);
 puts("PASS 100000 low/unreadable/absent PG observations: no quota, storm, retry or latch");
 s.pg_valid=true;s.pg_good=false;
 assert(a9_check_rail_sample(&a,true,-ENODEV,&s)==-EIO);
 s.on=false;assert(a9_check_rail_sample(&a,true,0,&s)==-EIO);
 s.on=true;s.forced_off=true;assert(a9_check_rail_sample(&a,true,0,&s)==-EIO);
 s.forced_off=false;s.emergency=true;assert(a9_check_rail_sample(&a,true,0,&s)==-EIO);
 assert(latched==4&&off==4&&reports==4);
 puts("PASS independent device/hard-off failures still latch before ordered shutdown");
 int pg,p,v;struct fp9929a f;
 for(int i=0;i<4;i++){
   pg=i==0?0:i==1?1:-EIO;reset(&f,i==3?NULL:&pg,&p,&v);
   assert(!__fp9929a_enable(&f)&&f.on&&p==1&&v==1&&now_us==25000&&timer_armed==1);
   char value[32];snprintf(value,sizeof(value),"PG %d sampled",i==3?-ENOENT:pg);assert(strstr(logline,value));
 }
 puts("PASS stock 5ms+20ms PMIC sequence: raw PG never affects success or timing");
 reset(&f,&pg,&p,&v);trip_after_delay=true;assert(__fp9929a_enable(&f)==-EIO&&!p&&!v&&!f.on);
 reset(&f,&pg,&p,&v);vcom_error=-EIO;assert(__fp9929a_enable(&f)==-EIO&&!p&&!v&&!f.on);
 puts("PASS explicit emergency during enable and VCOM API error still abort");
 return 0;
}
'''
    source = a.out/'test.c'
    source.write_text(code)
    sp.run([os.environ.get('HOSTCC', 'gcc'), '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
            '-Wno-unused-parameter', '-Wno-unused-variable', str(source), '-o', str(a.out/'test')], check=True)
    sp.run([a.out/'test'], check=True)


if __name__ == '__main__':
    main()
