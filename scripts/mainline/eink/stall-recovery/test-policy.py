#!/usr/bin/env python3
"""Compile production timer and clean-refill paths with a controlled clock.
No device access. Real hrtimer scheduling and electrical hold tolerance untested.
"""
from pathlib import Path
import argparse,json,subprocess as sp
R=Path(__file__).resolve().parents[4];D=R/'scripts/mainline/eink/driver'
ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
def function(text,sig):
 start=text.index(sig);end=text.index('{',start)+1;depth=1
 while depth:depth+=(text[end]=='{')-(text[end]=='}');end+=1
 return text[start:end]
t=(D/'a9_eink_transport.c').read_text();d=(D/'a9_eink_drv.c').read_text();h=(D/'a9_eink.h').read_text()
code=r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
typedef uint64_t u64;typedef uint32_t u32;typedef int64_t ktime_t;
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define smp_store_release(p,v) (*(p)=(v))
#define container_of(p,type,member) ((type *)((char *)(p)-offsetof(type,member)))
#define div_u64(a,b) ((a)/(b))
#define DRM_ATOMIC_PROFILE_DONE 7
#define dev_crit(...) ((void)0)
#define dev_info(...) ((void)0)
static unsigned warnings;
#define dev_warn_ratelimited(...) ((void)++warnings)
static ktime_t now;
static ktime_t mock_ktime(void){return now;}
#define ktime_get() mock_ktime()
#define ktime_get_ns() ((u64)mock_ktime())
#define ns_to_ktime(n) (n)
#define ktime_add_ms(t,n) ((t)+(ktime_t)(n)*1000000)
#define ktime_us_delta(a,b) (((a)-(b))/1000)
#define ktime_ms_delta(a,b) (((a)-(b))/1000000)
typedef int atomic_t;
static atomic_t a9x_stall_fault,a9x_rail_fault;
static void atomic_set(atomic_t *p,int v){*p=v;}
static bool a9x_fault_latched(void){return a9x_stall_fault||a9x_rail_fault;}
static bool commit_hard;
static bool msm_a9_commit_faulted(void){return commit_hard;}
enum hrtimer_restart {HRTIMER_NORESTART,HRTIMER_RESTART};
#define HRTIMER_MODE_ABS_HARD 1
struct hrtimer {ktime_t expires;bool queued;};
static void hrtimer_set_expires(struct hrtimer *h,ktime_t n){h->expires=n;}
static void hrtimer_start(struct hrtimer *h,ktime_t n,int mode){assert(mode==HRTIMER_MODE_ABS_HARD);h->expires=n;h->queued=true;}
static void hrtimer_try_to_cancel(struct hrtimer *h){h->queued=false;}
/* Deterministic serialization checks, not a real SMP/IRQ scheduler. */
typedef bool raw_spinlock_t;
#define raw_spin_lock_irqsave(p,f) do {(f)=0;assert(!*(p));*(p)=true;}while(0)
#define raw_spin_unlock_irqrestore(p,f) do {(void)(f);assert(*(p));*(p)=false;}while(0)
static unsigned cut,hard_cut,fault_hook;
static void fp9929a_starvation_off(const char *why){(void)why;cut++;}
static void fp9929a_emergency_off(const char *why){(void)why;hard_cut++;}
'''
code+='\n'.join(l for l in h.splitlines() if l.startswith(('#define A9X_STALL_','#define A9X_RECOVERY_')))+'\n'
code+=h[h.index('struct a9x_stall_snapshot {'):h.index('/* Read-only boot latch')]
code+=r'''
struct a9x_transport {
 struct hrtimer stall;raw_spinlock_t stall_lock;ktime_t stall_started;
 bool on,stall_armed,stall_soft_reported,commit_active,front_null,stall_commit_ok,stall_returned,transport_error;
 unsigned soft_last_age_us,commit_stage,stall_return_us;
 u64 soft_stalls,commit_seq,stall_commit_seq,frames,stalls;
 int stall_kind,stall_raw_ret;ktime_t last_commit,commit_started,stall_at;
 struct {unsigned phase;u64 mark_ns,ns[DRM_ATOMIC_PROFILE_DONE];} profile;
 struct a9x_stall_snapshot stall_snapshot;char stall_why[96];void *dev;
 void (*fault)(struct a9x_transport *);
};
static void fault(struct a9x_transport *t){(void)t;fault_hook++;}
struct a9_eink {
 struct a9x_transport tx;bool hv_fault;struct {bool aborted;} last;
 unsigned stall_retries;u64 stall_grants;ktime_t stall_clean_since;u64 stall_clean_soft,stall_clean_hard;void *dev;
};
'''
for sig in ('static enum hrtimer_restart a9x_stall_fn(', 'static bool a9x_stall_arm(', 'static void a9x_stall_disarm(', 'void a9x_emergency_off('):code+=function(t,sig)+'\n'
code+=function(d,'static void a9_recovery_clean_progress(')+'\n'
# Execute the actual DRM-result block, before the post-cutoff conversion.
start=t.index('\t/* A real DRM error cuts HV immediately');end=t.index('\t/* Preserve the RAW return',start)
code+='static int check_commit_result(struct a9x_transport *t,int ret) {\n'+t[start:end]+'return ret;}\n'
assert end<t.index('\t/* A successful DRM return cannot resurrect progress')
code+=r'''
static void reset(struct a9_eink *a){memset(a,0,sizeof(*a));a->tx.on=true;a->tx.fault=fault;now=1000000000;cut=hard_cut=fault_hook=warnings=0;a9x_stall_fault=a9x_rail_fault=0;commit_hard=false;}
#define ADV_MS(ms) (now+=(ktime_t)(ms)*1000000)
int main(void){
 struct a9_eink a;struct a9x_transport *t=&a.tx;ktime_t start;
 reset(&a);assert(a9x_stall_arm(t));start=t->stall_started;ADV_MS(63);
 assert(a9x_stall_arm(t));assert(t->stall.expires==start+163000000);
 now=start+100000000;assert(a9x_stall_fn(&t->stall)==HRTIMER_RESTART);
 assert(!warnings&&!cut&&!a9x_fault_latched()&&!t->soft_stalls);
 a9x_stall_disarm(t);ADV_MS(1000);assert(a9x_stall_fn(&t->stall)==HRTIMER_NORESTART);assert(!cut);
 puts("PASS 63ms raw success/progress re-arms without soft event; confirmed neutral cancels");
 reset(&a);a.stall_retries=1;a.stall_grants=5;assert(a9x_stall_arm(t));start=now;t->commit_active=true;t->profile.phase=5;
 ADV_MS(100);assert(a9x_stall_fn(&t->stall)==HRTIMER_RESTART);assert(t->soft_stalls==1&&warnings==1);
 assert(!cut&&!hard_cut&&!fault_hook&&!a9x_fault_latched()&&!t->stall_snapshot.valid&&!t->stall_kind&&a.stall_retries==1&&a.stall_grants==5);
 assert(t->stall.expires==start+500000000);ADV_MS(399);assert(a9x_stall_fn(&t->stall)==HRTIMER_RESTART);assert(!cut&&t->soft_stalls==1);
 ADV_MS(1);assert(a9x_stall_fn(&t->stall)==HRTIMER_NORESTART);assert(cut==1&&fault_hook==1&&a9x_stall_fault&&t->stall_kind==3&&t->stall_snapshot.valid);
 assert(t->stall_snapshot.phase==5&&t->stall_snapshot.at_ns==(u64)(start+500000000));assert(!a9x_stall_arm(t));a9x_stall_disarm(t);assert(a9x_stall_fault);
 puts("PASS 100ms soft has no HV/fault/grant effect; 499ms stays soft; absolute500ms cuts and latches once");
 reset(&a);assert(a9x_stall_arm(t));ADV_MS(1);assert(check_commit_result(t,-EIO)==-EIO);assert(hard_cut==1&&t->stall_kind==2&&t->transport_error&&fault_hook==1);ADV_MS(600);assert(a9x_stall_fn(&t->stall)==HRTIMER_NORESTART&&t->stall_kind==2);
 reset(&a);commit_hard=true;assert(check_commit_result(t,0)==-EIO&&hard_cut==1);
 reset(&a);a9x_stall_fault=1;t->stall_kind=3;assert(check_commit_result(t,0)==0&&!hard_cut&&t->stall_kind==3);
 reset(&a);a9x_rail_fault=1;assert(!a9x_stall_arm(t));
 puts("PASS real DRM/void-wait errors cut immediately; late raw success preserves timing recovery; rail/hard faults dominate");
 reset(&a);a.stall_retries=2;a.stall_grants=7;a9_recovery_clean_progress(&a);ADV_MS(599999);a9_recovery_clean_progress(&a);assert(a.stall_retries==2);ADV_MS(1);a9_recovery_clean_progress(&a);assert(!a.stall_retries&&a.stall_grants==7);
 a.stall_retries=1;ADV_MS(599999);t->soft_stalls++;a9_recovery_clean_progress(&a);ADV_MS(1);a9_recovery_clean_progress(&a);assert(a.stall_retries==1);ADV_MS(599999);a9_recovery_clean_progress(&a);assert(!a.stall_retries&&a.stall_grants==7);
 a.stall_retries=2;ADV_MS(600000);a9x_stall_fault=1;a9_recovery_clean_progress(&a);assert(a.stall_retries==2&&!a.stall_clean_since);a9x_stall_fault=0;a9_recovery_clean_progress(&a);ADV_MS(600000);a.last.aborted=true;a9_recovery_clean_progress(&a);assert(a.stall_retries==2&&!a.stall_clean_since);
 puts("PASS ten-minute clean boundary refills budget without resetting receipts; soft event/fault/abort restart clean interval");
}
'''
p=a.out/'policy-extracted.c';p.write_text(code)
sp.run(['gcc','-std=gnu11','-Wall','-Wextra','-Werror',str(p),'-o',str(a.out/'policy-test')],check=True)
result=sp.check_output([str(a.out/'policy-test')],text=True);print(result,end='')
(a.out/'result.json').write_text(json.dumps({'status':'PASS host extracted production functions; no physical hold or real IRQ claims','checks':result.splitlines()},indent=2)+'\n')
