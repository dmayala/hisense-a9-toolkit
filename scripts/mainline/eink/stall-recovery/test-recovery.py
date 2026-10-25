#!/usr/bin/env python3
"""Compile the actual worker and PMIC recovery functions against a small host shim."""
from pathlib import Path
import subprocess as sp
import tempfile
import sys

repo = Path(__file__).resolve().parents[4]

def function(source, signature):
    start = source.index(signature)
    end = source.index('{', start)
    depth = 1
    end += 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]

pmic = Path(sys.argv[1]).read_text()
driver = (repo / 'scripts/mainline/eink/driver/a9_eink_drv.c').read_text()
shim = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <errno.h>
#include <stdio.h>
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define dev_info(...) ((void)0)
#define pr_crit(...) ((void)0)
#define pr_crit_ratelimited(...) ((void)0)
#define mutex_lock(...) ((void)0)
#define mutex_unlock(...) ((void)0)
#define raw_spin_lock_irqsave(p,f) do {(void)(p);(f)=0;}while(0)
#define raw_spin_unlock_irqrestore(p,f) do {(void)(p);(void)(f);}while(0)
#define wake_up_all(...) ((void)0)
#define hrtimer_cancel(...) ((void)0)
#define gpiod_get_value_cansleep(p) (*(p))
#define gpiod_set_value(p,v) (*(p)=(v))
#define msecs_to_jiffies(x) (x)
#define queue_delayed_work(...) 1
#define ktime_get() mock_ktime()
#define ktime_ms_delta(a,b) ((a)-(b))
#define put_device(...) ((void)0)
typedef unsigned long long u64;
typedef long ktime_t;
typedef int atomic_t;
static int atomic_read(atomic_t *a) { return *a; }
static void atomic_set(atomic_t *a, int v) { *a=v; }
static int atomic_cmpxchg(atomic_t *a,int old,int val) { int r=*a; if(r==old)*a=val; return r; }
static void atomic_inc(atomic_t *a) { ++*a; }
static void atomic_dec(atomic_t *a) { --*a; }
struct device_node { int dummy; };
struct i2c_client { struct {int driver;} dev; } client = {{1}};
struct fp9929a { bool on, energized, forced_off; int *pg, *vcom_en, *pwrup; long emerg_at; } fp;
static struct fp9929a *fp9929a_inst = &fp;
static struct i2c_client *of_find_i2c_device_by_node(struct device_node *np) { return np ? &client : NULL; }
static struct fp9929a *i2c_get_clientdata(struct i2c_client *c) { return &fp; }
static bool fp9929a_fault_latched(struct fp9929a *p) { return p->forced_off; }
static atomic_t fp9929a_emerg, fp9929a_emerg_busy, a9x_stall_fault, a9x_rail_fault;
static bool allow_power_on, enable;
static long now;
static long mock_ktime(void) { return now; }
static bool commit_hard;
static bool msm_a9_commit_faulted(void) { return commit_hard; }
static bool a9x_fault_latched(void) { return a9x_stall_fault || a9x_rail_fault; }
#define A9X_RECOVERY_CLEAN_MS 600000
struct a9x_transport { int stall_lock; bool gone,on,front_null,stall_armed,commit_active; int stall_kind; bool stall_commit_ok, transport_error; long stall_at; unsigned long long soft_stalls,stalls; };
struct a9_eink { struct a9x_transport tx; bool suspending,hv_fault,force_clear; struct {int valid;} same;
    struct device_node *pmic_np; unsigned stall_retries; unsigned long long stall_grants,stall_clean_soft,stall_clean_hard; long stall_clean_since; struct {bool aborted;} last; int recover_ret; atomic_t recover_req; };
'''
code = shim + '\n' + function(pmic, 'static void fp9929a_do_emergency_off(') + '\n'
code += function(pmic, 'int fp9929a_recover_starvation(') + '\n'
code += function(driver, 'static void a9_recover_stall(') + '\n'
code += function(driver, 'static void a9_recovery_clean_progress(') + '\n'
code += r'''
static int pg,vcom,pwr;
static struct device_node node;
static struct a9_eink a;
static void reset(void) {
    commit_hard=false;
    fp=(struct fp9929a){.pg=&pg,.vcom_en=&vcom,.pwrup=&pwr}; pg=vcom=pwr=0;
    a=(struct a9_eink){.tx={.front_null=true,.stall_kind=1,.stall_at=0},.pmic_np=&node};
    fp9929a_inst=&fp; fp9929a_emerg=2; fp9929a_emerg_busy=0;
    allow_power_on=enable=false; a9x_stall_fault=1; a9x_rail_fault=0; now=5000;
}
static void denied(void) { a9_recover_stall(&a); assert(a.recover_ret<0 && a9x_stall_fault==1 && fp9929a_emerg!=0 && !a.stall_retries); }
int main(void) {
    reset(); now=4999; denied();
    reset(); a9_recover_stall(&a); assert(a.recover_ret==0 && a.stall_retries==1 && a.stall_grants==1 && a.force_clear && !a9x_stall_fault && !fp9929a_emerg && !enable && !allow_power_on);
    a.tx.stall_kind=1; fp9929a_emerg=2; a9x_stall_fault=1; now=29999; a9_recover_stall(&a); assert(a.recover_ret==-EAGAIN);
    now=30000; a9_recover_stall(&a); assert(a.recover_ret==0 && a.stall_retries==2 && a.stall_grants==2);
    a.tx.stall_kind=1; fp9929a_emerg=2; a9x_stall_fault=1; now=90000; a9_recover_stall(&a); assert(a.recover_ret==-EPERM && a9x_stall_fault && fp9929a_emerg==2);
    puts("PASS backoff, two grants before clean refill, cap; all gates stay closed; GC16 owed");
    a.tx.stall_kind=0; fp9929a_emerg=0; a9x_stall_fault=0; now=100000;
    a9_recovery_clean_progress(&a); now=699999; a9_recovery_clean_progress(&a); assert(a.stall_retries==2 && a.stall_grants==2);
    now=700000; a9_recovery_clean_progress(&a); assert(a.stall_retries==0 && a.stall_grants==2);
    a.tx.stall_kind=1; a.tx.stall_at=695000; fp9929a_emerg=2; a9x_stall_fault=1;
    a9_recover_stall(&a); assert(a.recover_ret==0 && a.stall_retries==1 && a.stall_grants==3);
    puts("PASS actual refill then third lifetime grant: retries0->1, monotonic receipt2->3");
    reset(); a.tx.stall_kind=2; denied(); reset(); a.tx.commit_active=true; denied();
    reset(); a.tx.front_null=false; denied(); reset(); a.tx.on=true; denied();
    reset(); a.hv_fault=true; denied(); reset(); a9x_rail_fault=1; denied();
    reset(); a.tx.stall_kind=3; denied();
    reset(); a.tx.stall_kind=3; a.tx.stall_commit_ok=true; a.tx.transport_error=true; denied();
    reset(); a.tx.stall_kind=3; a.tx.stall_commit_ok=true; commit_hard=true; denied();
    reset(); a.tx.stall_kind=3; a.tx.stall_commit_ok=true; a9_recover_stall(&a);
    assert(a.recover_ret==0 && a.stall_retries==1 && a.stall_grants==1 && a.force_clear);
    puts("PASS pending/failed commit stays stopped; returned-zero + no DPU error + neutral/off qualifies");
    reset(); fp.forced_off=true; denied(); reset(); fp9929a_emerg=1; denied();
    reset(); fp9929a_emerg_busy=1; denied(); reset(); pg=1; a9_recover_stall(&a); assert(a.recover_ret==0); reset(); pg=-EIO; a9_recover_stall(&a); assert(a.recover_ret==0);
    reset(); vcom=1; denied(); reset(); pwr=1; denied(); reset(); allow_power_on=true; denied();
    puts("PASS max-on, hard emergency, pending drop and control-GPIO checks; PG stays observational");
    reset(); fp9929a_emerg=0; fp9929a_do_emergency_off("test",true); assert(fp9929a_emerg==2);
    fp9929a_do_emergency_off("test",false); assert(fp9929a_emerg==1);
    fp9929a_do_emergency_off("test",true); assert(fp9929a_emerg==1);
    puts("PASS hard emergency dominates starvation in both call orders");
    reset(); a9x_stall_fault=0; a9_recover_stall(&a); assert(a.recover_ret==0 && a.force_clear);
    puts("PASS safe recovery after prior manual a9 clear_fault");
}
'''
assert 'fp->forced_off = false' not in function(pmic, 'static int fp9929a_disable(')
with tempfile.TemporaryDirectory(prefix='a9-stall-recovery-') as tmp:
    src=Path(tmp)/'test.c'; src.write_text(code)
    sp.run(['gcc','-std=gnu11','-Wall','-Werror','-Wno-unused-function',str(src),'-o',tmp+'/test'],check=True)
    sp.run([tmp+'/test'],check=True)
