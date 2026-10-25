/* Production __a9_update runs verbatim; only engine/board dependencies are
 * mocked. Conditioning/target equality itself is covered by test.c. */
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <stdarg.h>
#include <string.h>
#include <limits.h>
#include <errno.h>
#include "a9_eink_engine.h"
typedef uint8_t u8;
typedef uint32_t u32;
typedef unsigned long long u64;
typedef long long s64;
typedef long long ktime_t;
struct mutex { int depth; };
struct sha256_ctx { int unused; };
struct a9x_transport { u64 frames, late_drive, on_since; bool on; };
struct fp9929a_status { int unused; };
struct record {
 int t1_raw, t2_raw, t1, t2, bin, clear, dry, aborted;
 u32 pre, mode, type, frames, steps, prep_us, begin_us, total_us;
 struct a9e_rect rect;
 bool sha_valid;
 uint8_t sha[32];
};
struct a9_eink {
 void *dev;
 struct a9e_engine e;
 struct mutex eng_lock;
 struct a9x_transport tx;
 struct record last;
 bool init_pending, dry_init_pending, force_clear, dry_force_clear;
 uint8_t *dry_save_old, *prep_save_old, *trace_old, *dry_levels, *frame, *ct;
 u32 dry_save_pre;
 int dry_save_have_pre;
 unsigned updates, aborts;
 ktime_t wf_end;
 int wf_ret;
};
static bool trace, enable = true, log_updates;
static unsigned zero_test;
static int a9x_stall_fault, a9x_rail_fault;
static bool a9x_fault_latched(void) { return a9x_stall_fault || a9x_rail_fault; }
static unsigned prepares, powers, poweroffs, frames, commits, starts, live_checks;
static unsigned temps, assertions, cases;
static bool prepared_unchanged;
static bool cold_after_prepare;
static int power_result = -EIO, ring_result;
#define A9_TEMP_MIN_C 0
#define READ_ONCE(x) (x)
#define atomic_read(p) (*(p))
#define min(a,b) ((a) < (b) ? (a) : (b))
static void log_message(void *dev, const char *fmt, ...) {(void)dev; (void)fmt;}
#define dev_err_ratelimited log_message
#define dev_warn_ratelimited log_message
#define dev_err log_message
#define dev_info log_message
#define CHECK(cond) do { assertions++; if (!(cond)) { fprintf(stderr,"policy assertion line %d: %s\n", __LINE__, #cond); exit(1); } } while (0)
static void mutex_lock(struct mutex *m) { CHECK(m->depth == 0); m->depth++; }
static void mutex_unlock(struct mutex *m) { CHECK(m->depth == 1); m->depth--; }
static ktime_t ktime_get(void) { static ktime_t clock; return ++clock; }
static ktime_t ktime_us_delta(ktime_t a, ktime_t b) { return a-b; }
static bool a9x_attached(struct a9x_transport *t) {(void)t; return true;}
static int a9x_attach(struct a9x_transport *t) {(void)t; return 0;}
static int a9_temps(struct a9_eink *a, int *t1, int *t2, struct fp9929a_status *st) {
 temps++; *t1 = *t2 = 25;
 a->last.t1_raw = a->last.t2_raw = cold_after_prepare && prepares ? -10 : 25;
 memset(st,0,sizeof(*st));return 0;
}
static int policy_temp_bin(struct a9t_state *s, int a, int b) {(void)s;(void)a;(void)b; return 10;}
static bool a9_bin_admitted(int bin, int *lo, int *hi) {*lo=0;*hi=13;return bin>=*lo&&bin<=*hi;}
static int a9_power_on(struct a9_eink *a) {(void)a;powers++;return power_result;}
/* review 22 / fix-0040: no power-off ever runs under eng_lock (M4 makes 0053 obey it too) */
static void a9_power_off(struct a9_eink *a) {CHECK(a->eng_lock.depth==0);poweroffs++;}
static int a9_check_rail_sample(struct a9_eink *a,bool was_on,int ret,const struct fp9929a_status *st) { (void)was_on;(void)ret;(void)st;CHECK(a->eng_lock.depth==0);return 0; }
/* 0052/0053 wf-end hooks (M4): the 0052 read and the 0053 SAMPLE run with
 * eng_lock held, right after the ring; the 0053 DECISION (settle, latch,
 * ordered power-off) runs after the unlock. The decision mock behaves like
 * the production latch (rail fault, enable off, Clear owed, power-off). */
static bool damage_prep;
static unsigned wf_end_hooks, pg_low_samples, pg_decisions, hv_on_calls;
/* F9: HV-on accounting after every real waveform (reads transport state only) */
static unsigned long long a9_hv_on_ms(struct a9_eink *a) { (void)a; hv_on_calls++; return 0; }
static bool pg_low_next, pg_latch_next;
static s64 a9_diag_wf_end(struct a9_eink *a) { CHECK(a->eng_lock.depth==1); wf_end_hooks++; return pg_low_next ? 4242 : 0; }
static bool a9_pg_wf_end_low(struct a9_eink *a, s64 first_us, struct fp9929a_status *st) {
 CHECK(a->eng_lock.depth==1); CHECK(first_us==(pg_low_next ? 4242 : 0)); (void)st; pg_low_samples++; return pg_low_next;
}
static bool a9_pg_wf_end(struct a9_eink *a, const struct fp9929a_status *st) {
 (void)st; CHECK(a->eng_lock.depth==0); CHECK(pg_low_samples==1); pg_decisions++;
 if (!pg_latch_next) return false;
 a9x_rail_fault=1; enable=false; a->force_clear=true; a9_power_off(a); return true;
}
static int a9_check_live_rails(struct a9_eink *a) { CHECK(a->eng_lock.depth==0); live_checks++; return 0; }
static void policy_assume_white(struct a9e_engine *e) {memset(e->old,15,A9T_N);}
static int policy_prepare(struct a9e_engine *e, u32 mode, int clear, const struct a9e_rect *rect, int mask) {
 (void)mask;prepares++;e->unchanged=prepared_unchanged;e->mode=mode;e->clear=clear;e->rect=*rect;return 0;
}
static unsigned inc_invalidates;
static void policy_inc_invalidate(struct a9e_engine *e) {(void)e;inc_invalidates++;}
static void policy_begin_init(struct a9e_engine *e,int bin) {(void)e;(void)bin;starts++;}
static void policy_init_end(struct a9e_engine *e) {(void)e;}
static void policy_start(struct a9e_engine *e,int bin) {(void)e;(void)bin;starts++;}
static void policy_end(struct a9e_engine *e) {(void)e;commits++;}
static void policy_abort(struct a9e_engine *e) {(void)e;}
static int a9_run_frames(struct a9_eink *a,bool dry,struct sha256_ctx *s) {(void)a;(void)dry;(void)s;frames++;return 0;}
static int a9_run_ring(struct a9_eink *a,bool init,int bin,struct sha256_ctx *s,bool *shown) {(void)a;(void)bin;(void)s;*shown=init;frames++;return ring_result;}
static int a9x_show(struct a9x_transport *t,uint8_t *f) {(void)t;(void)f;frames++;return 0;}
static void sha256_init(struct sha256_ctx *s) {(void)s;}
static void sha256_final(struct sha256_ctx *s,uint8_t *out) {(void)s;(void)out;}
#define a9t_temp_bin policy_temp_bin
#define a9e_assume_white policy_assume_white
#define a9e_prepare policy_prepare
#define a9e_begin_init policy_begin_init
#define a9e_init_end policy_init_end
#define a9e_start policy_start
#define a9e_end policy_end
#define a9e_abort policy_abort
#define a9e_inc_invalidate policy_inc_invalidate
#include "policy-update.inc"

static struct a9_eink a;
static uint8_t old[A9T_N], saved[A9T_N], dry_saved[A9T_N], traced[A9T_N], dry_levels[A9T_N];
static void setup(void) {
 memset(&a,0,sizeof(a));memset(old,7,sizeof(old));
 a.e.old=old;a.e.have_pre=1;a.e.pre_mode=518;
 a.prep_save_old=saved;a.dry_save_old=dry_saved;a.trace_old=traced;a.dry_levels=dry_levels;
 a.tx.frames=97;a.tx.late_drive=3;a.tx.on_since=123456789;
 a.updates=11;a.aborts=2;a.last.frames=47;a.last.mode=518;
 trace=false;zero_test=0;a9x_stall_fault=0;prepared_unchanged=true;cold_after_prepare=false;
 prepares=powers=poweroffs=frames=commits=starts=temps=0;
 power_result=-EIO;ring_result=0;a9x_rail_fault=0;live_checks=0;
 enable=true;pg_low_next=pg_latch_next=false;wf_end_hooks=pg_low_samples=pg_decisions=hv_on_calls=0;
 cases++;
}
static void stable(void) {
 CHECK(a.eng_lock.depth==0);CHECK(!frames);CHECK(!starts);CHECK(!commits);
 CHECK(a.updates==11);CHECK(a.tx.frames==97);CHECK(a.tx.late_drive==3);
 CHECK(a.tx.on_since==123456789);CHECK(a.e.have_pre==1);CHECK(a.e.pre_mode==518);
 bool preserved=true;
 for(size_t i=0;i<A9T_N;i++) if(old[i]!=7) {preserved=false;break;}
 CHECK(preserved);
}
int main(void) {
 struct a9_job job = {.dry=false,.mode=518,.clear=0,.mask=0,.rect={20,20,21,21}};
 setup();__a9_update(&a,&job);stable();
 CHECK(prepares==1);CHECK(powers==0);CHECK(poweroffs==0);CHECK(a.aborts==2);CHECK(live_checks==1);
 CHECK(!a.force_clear);CHECK(!a.init_pending);CHECK(a.last.frames==47);CHECK(a.last.mode==518);
 /* Even tracing a duplicate must not synthesize a successful display record. */
 setup();trace=true;__a9_update(&a,&job);stable();CHECK(!powers);CHECK(a.aborts==2);
 /* Init and recovery exclude suppression even if preparation advertises
  * equality. Power-up refuses deliberately, before any physical operation. */
 setup();a.init_pending=true;__a9_update(&a,&job);stable();CHECK(powers==1);CHECK(a.init_pending);CHECK(a.aborts==3);
 setup();a.force_clear=true;__a9_update(&a,&job);stable();CHECK(powers==1);CHECK(a.force_clear);CHECK(a.aborts==3);
 setup();prepared_unchanged=false;__a9_update(&a,&job);stable();CHECK(powers==1);CHECK(a.aborts==3);
 /* Diagnostic zero-test must bypass conditioning and stale equality. */
 setup();zero_test=1;a.e.unchanged=true;__a9_update(&a,&job);stable();CHECK(!prepares);CHECK(powers==1);CHECK(a.aborts==3);
 /* Dry execution must continue past equality. A cold mocked reading ends
  * it before rendering; the production rollback still restores real levels. */
 setup();job.dry=true;cold_after_prepare=true;__a9_update(&a,&job);stable();CHECK(prepares==1);CHECK(temps==1);CHECK(a.aborts==3);job.dry=false;
 /* Equality cannot clear or bypass an already latched starvation fault. */
 setup();a9x_stall_fault=1;__a9_update(&a,&job);stable();CHECK(!prepares);CHECK(!powers);CHECK(a9x_stall_fault==1);CHECK(a.aborts==3);
 /* A real explicit-clear-shaped job aborts in the actual update body.
  * Its failure must create a recovery obligation, not commit mode/history. */
 setup();job.clear=1;prepared_unchanged=false;power_result=0;ring_result=-EIO;
 __a9_update(&a,&job);
 CHECK(a.eng_lock.depth==0);CHECK(powers==1);CHECK(frames==1);CHECK(poweroffs==1);
 CHECK(!commits);CHECK(a.updates==11);CHECK(a.aborts==3);CHECK(a.force_clear);
 CHECK(a.last.aborted==1);CHECK(a.e.pre_mode==518);CHECK(a.e.have_pre==1);
 /* A explicit clear cannot authorize a latched rail-fault update or erase
  * the mandatory recovery owed after the explicit fault reset. */
 setup();a9x_rail_fault=1;a.force_clear=true;__a9_update(&a,&job);stable();
 CHECK(!prepares);CHECK(!powers);CHECK(a9x_rail_fault==1);CHECK(a.force_clear);CHECK(a.aborts==3);
 /* M4: a completed real update with pg_settle on. (a) A PG low sampled at the
  * waveform end that does not recover: the history is committed (the waveform
  * finished), THEN, outside eng_lock, the latch powers off, counts an abort
  * and leaves the full Clear owed. */
 job.clear=0;
 setup();prepared_unchanged=false;power_result=0;pg_low_next=true;pg_latch_next=true;
 __a9_update(&a,&job);
 CHECK(a.eng_lock.depth==0);CHECK(powers==1);CHECK(frames==1);CHECK(commits==1);CHECK(a.updates==12);
 CHECK(wf_end_hooks==1);CHECK(pg_low_samples==1);CHECK(pg_decisions==1);CHECK(hv_on_calls==1);
 CHECK(poweroffs==1);CHECK(a.aborts==3);CHECK(a.force_clear);CHECK(a9x_rail_fault==1);CHECK(!enable);
 CHECK(a.last.aborted==0);
 /* (b) the dip recovers: committed, no abort, no Clear owed, no power-off */
 setup();prepared_unchanged=false;power_result=0;pg_low_next=true;pg_latch_next=false;
 __a9_update(&a,&job);
 CHECK(a.eng_lock.depth==0);CHECK(commits==1);CHECK(a.updates==12);CHECK(pg_decisions==1);
 CHECK(!poweroffs);CHECK(a.aborts==2);CHECK(!a.force_clear);CHECK(!a9x_rail_fault);CHECK(enable);
 /* (c) good PG at the end: sampled, no decision at all */
 setup();prepared_unchanged=false;power_result=0;
 __a9_update(&a,&job);
 CHECK(commits==1);CHECK(pg_low_samples==1);CHECK(!pg_decisions);CHECK(!poweroffs);CHECK(a.aborts==2);
 /* (d) the ring failed: no 0053 sample, the existing abort path (power-off after the unlock) */
 setup();prepared_unchanged=false;power_result=0;ring_result=-EIO;pg_low_next=true;pg_latch_next=true;
 __a9_update(&a,&job);
 CHECK(!commits);CHECK(!pg_low_samples);CHECK(!pg_decisions);CHECK(poweroffs==1);CHECK(a.aborts==3);CHECK(a.force_clear);
 printf("target policy: %u production __a9_update cases, %u assertions passed\n",cases,assertions);
 return 0;
}
