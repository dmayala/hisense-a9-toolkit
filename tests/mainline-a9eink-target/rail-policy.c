/* Review 22 regression: actual driver temperature, live-rail, power, update,
 * job-selection and fault-reset control flow is extracted verbatim. Engine,
 * render and hardware dependencies are mocks; test.c covers real conditioning.
 * The fallback FP status structure models hardware fields, not kernel ABI layout.
 * Set A9_FP_STATUS_HEADER when extracting to check against an actual header. */
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
typedef long long ktime_t;
typedef long long s64;
/* 0042: production struct a9_session_stats, extracted from a9_eink.h. */
#include "rail-policy-session.inc"
struct mutex { int depth; };
struct sha256_ctx { int unused; };
struct a9x_transport { u64 frames, late_drive, on_since; bool on, gone, front_null, stall_armed, unreg_hv_unknown; struct mutex lock; };
struct record {
 int t1_raw, t2_raw, t1, t2, bin, clear, dry, aborted;
 u32 pre, mode, type, frames, steps, prep_us, begin_us, total_us;
 struct a9e_rect rect;
 bool sha_valid;
 uint8_t sha[32];
};
struct a9_eink {
 void *dev, *pmic_np, *hv, *therm;
 bool hv_fault; ktime_t wf_end; int wf_ret;
 /* 0052 counters (a9_diag_rail_sample is extracted verbatim), 0054 damage tiles */
 struct { int lock; u64 hist_n[5], hist_low[5], settle_reread_high;
  u64 vcom_reads, vcom_ok, vcom_mismatch, vcom_err; int vcom_last; s64 vcom_last_ms; } diag;
 s64 settle_first_us;	/* M4: settle clock origin handed over by the wf-end sample */
 /* F6/F6b (review 89d): production fields read by the verbatim a9_pg_wf_end */
 bool settle_ok_now; u64 settle_trace; u8 settle_trace_n; u64 vbl_first, vbl_rec; s64 vbl_first_us, vbl_rec_us;
 unsigned int vcom_reads;
 u8 dirty_tm[A9E_NTILES]; bool tm_lost, prev_pattern;
 struct mutex shadow_lock;
 bool kick, clear_req, manual_clear_req, controls_dirty, test_pattern;
 u32 mode; int controls[4]; int wq;
 uint8_t *shadow; struct a9e_rect dirty;
 struct {bool tx_on,tx_gone,front_null,armed,on,energized,forced,emergency,pg_valid,pg_good; long long session_ms,since_wf_ms,at_ms; int getter,pg_raw,wf_ret; unsigned long long count;
  /* 0042 diagnostic capture */
  s64 pg_at_us; struct a9_session_stats session; u64 session_frames, pd_seq_before, pd_seq;
  bool pd_off_confirmed, pd_valid; int pd_getter; s64 pd_at_us[3]; int pd_pg_raw[3];} rail;
 struct a9_session_stats session;
 struct a9e_engine e;
 struct mutex eng_lock;
 struct a9x_transport tx;
 struct record last;
 bool init_pending, dry_init_pending, force_clear, dry_force_clear;
 uint8_t *dry_save_old, *prep_save_old, *trace_old, *dry_levels, *frame, *ct;
 u32 dry_save_pre;
 int dry_save_have_pre;
 unsigned updates, aborts;
};
static bool trace, enable = true, log_updates, mask_outside_damage;
static unsigned zero_test;
static int a9x_stall_fault, a9x_rail_fault;
static bool a9x_fault_latched(void) { return a9x_stall_fault || a9x_rail_fault; }
static unsigned prepares, powers, poweroffs, frames, commits, starts;
static unsigned temps, assertions, cases;
static bool prepared_unchanged;
static bool expect_containment;
static int ring_result;
#define A9_TEMP_MIN_C 0
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define atomic_set(p,v) (*(p)=(v))
static int atomic_xchg(int *p,int value) {int previous=*p;*p=value;return previous;}
#define dev_crit_ratelimited log_message
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
static int policy_temp_bin(struct a9t_state *s, int a, int b) {(void)s;(void)a;(void)b; return 10;}
static bool a9_bin_admitted(int bin, int *lo, int *hi) {*lo=0;*hi=13;return bin>=*lo&&bin<=*hi;}
#ifdef A9_TEST_FP_STATUS_EXTRACTED
#include "rail-policy-status.inc"
#else
struct fp9929a_pg_point { s64 at_us; int pg_raw; };
struct fp9929a_pd_trace { u64 seq; struct fp9929a_pg_point entry, vcom_low, pwrup_low; };
struct fp9929a_status {
	bool on;		/* rails up (power-up sequence completed) */
	bool forced_off;	/* max_on_ms fault latched: power-up returns -EIO */
	bool emergency;		/* fp9929a_emergency_off() latched: power-up -EIO
				 * until 1 is written to fp9929a.clear_fault */
	bool pg_valid;		/* a PG line is wired */
	bool pg_good;		/* PG logical 1 (DT active-high): rails settled good;
				 * docs/mainline-fp9929a-pg.md */
	int pg_raw;		/* the one gpiod read pg_good came from: 1, 0 or
				 * -errno; -ENOENT when no PG line is wired */
	s64 pg_at_us;		/* 0042: completion time of that read */
	struct fp9929a_pd_trace pdtrace;	/* 0042: normal power-down samples */
	bool energized;		/* PWRUP may be high (set before it is raised) */
	bool temp_valid;	/* a TMST sample exists */
	int temp_c;		/* TMST byte from the last power-up, unsigned deg C */
};
#endif
static int temp_fallback=25, iio_error;
static int iio_read_channel_processed(void *t,int *v) {(void)t;if(iio_error)return iio_error;*v=25000;return 0;}
static int pg=1, getter_error, status_reads, tx_powerups, force_disables, post_off_reads;
static bool pg_valid=true, fp_reports_on=true;
static int pg_sequence[8];
static unsigned pg_sequence_len;
static struct a9_eink *active;
static long long ktime_ms_delta(ktime_t a,ktime_t b) {return a-b;}
static long long ktime_to_ms(ktime_t a) {return a;}
static int fp9929a_get_status(void *np, struct fp9929a_status *st) {
 (void)np;
 unsigned sample=(unsigned)status_reads++;
 int raw=sample<pg_sequence_len ? pg_sequence[sample] : pg;
 /* 0042: no extra status read between the rail latch and its power-down */
 CHECK(!(a9x_rail_fault && active->tx.on));
 if(a9x_rail_fault && !active->tx.on) post_off_reads++;
 if(getter_error) return getter_error;
 st->on=active->tx.on && fp_reports_on;st->energized=active->tx.on;
 st->pg_valid=pg_valid;st->pg_good=raw==1;st->pg_raw=raw;
 st->temp_valid=true;st->temp_c=25;return 0;
}
static void regulator_force_disable(void *r) {(void)r;force_disables++;}
static int a9x_power_off(struct a9x_transport *t) {
 CHECK(active->eng_lock.depth==0);
 if(expect_containment) {CHECK(a9x_rail_fault);CHECK(!enable);CHECK(active->force_clear);CHECK(active->rail.count==1);CHECK(active->rail.tx_on);}
 poweroffs++;t->on=false;t->front_null=true;t->stall_armed=false;return 0;
}
static int a9x_power_on(struct a9x_transport *t) {tx_powerups++;t->on=true;t->on_since=ktime_get();return 0;}
static void policy_assume_white(struct a9e_engine *e) {memset(e->old,15,A9T_N);}
static int policy_prepare(struct a9e_engine *e, u32 mode, int clear, const struct a9e_rect *rect, int mask) {
 (void)mask;prepares++;e->unchanged=prepared_unchanged;e->mode=mode;e->clear=clear;e->rect=*rect;return 0;
}
static void policy_begin_init(struct a9e_engine *e,int bin) {(void)e;(void)bin;starts++;}
static void policy_init_end(struct a9e_engine *e) {(void)e;}
static void policy_start(struct a9e_engine *e,int bin) {(void)e;(void)bin;starts++;}
static void policy_end(struct a9e_engine *e) {e->pre_mode=e->mode & ~0x100u;e->have_pre=1;commits++;}
static void policy_abort(struct a9e_engine *e) {(void)e;}
static int a9_run_frames(struct a9_eink *a,bool dry,struct sha256_ctx *s) {(void)a;(void)dry;(void)s;frames++;return 0;}
static bool ring_drops_pg;	/* M4: PG reads low from the end of this waveform on */
static int a9_run_ring(struct a9_eink *a,bool init,int bin,struct sha256_ctx *s,bool *shown) {(void)a;(void)bin;(void)s;*shown=init;frames++;if(ring_drops_pg)pg=0;return ring_result;}
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
struct kernel_param {int unused;};
static int kstrtobool(const char *value,bool *out) {
 if(!strcmp(value,"0")) {*out=false;return 0;}
 if(!strcmp(value,"1")) {*out=true;return 0;}
 return -EINVAL;
}
#define pr_info(...) log_message(NULL, __VA_ARGS__)
static void policy_set_controls(struct a9t_state *state,int black,int white,int contrast) {(void)state;(void)black;(void)white;(void)contrast;}
static void policy_pattern(uint8_t *grey) {(void)grey;}
static struct a9e_rect policy_full(void) {return (struct a9e_rect){0,0,A9T_W,A9T_H};}
static int policy_empty(const struct a9e_rect *r) {return r->x1>=r->x2 || r->y1>=r->y2;}
static void policy_set_shadow(struct a9t_state *state,int on) {(void)state;(void)on;}
#define a9t_set_controls policy_set_controls
#define a9t_set_clear_shadow policy_set_shadow
#define a9e_pattern_contrast policy_pattern
#define a9e_rect_full policy_full
#define a9e_rect_empty policy_empty
/* 0052-0054 helpers outside the extracted set. M4: the 0053 wf-end sample
 * (a9_pg_wf_end_low) and decision (a9_pg_wf_end) are now EXTRACTED verbatim,
 * so with pg_settle_ms > 0 the real a9_check_rail_sample -> a9_rail_fault ->
 * a9_power_off chain runs from __a9_update, and a9x_power_off's mock asserts
 * eng_lock is not held. a9_pg_settle stays a mock (its loop is covered by the
 * stall harness): it answers settle_result and records the clock origin. */
static bool damage_prep;
static unsigned int pg_settle_ms;
enum a9_settle { A9_SETTLE_NO, A9_SETTLE_OK, A9_SETTLE_TIMEOUT };
#define spin_lock_irqsave(l, f) ((void)(l), (f) = 0)
#define spin_unlock_irqrestore(l, f) ((void)(l), (void)(f))
static unsigned diag_samples, wf_end_hooks, settle_calls;
static s64 diag_first_us, settle_origin_seen;
static enum a9_settle settle_result = A9_SETTLE_NO;
static unsigned int a9_diag_bucket(struct a9_eink *a, ktime_t now) {(void)a;(void)now;diag_samples++;return 4;}
struct a9_eink;
static enum a9_settle a9_pg_settle(struct a9_eink *a, int ret, const struct fp9929a_status *st) {
 (void)ret;(void)st;CHECK(a->eng_lock.depth==0);settle_calls++;settle_origin_seen=a->settle_first_us;a->settle_first_us=0;
 a->settle_ok_now = pg_settle_ms && settle_result == A9_SETTLE_OK;
 return pg_settle_ms ? settle_result : A9_SETTLE_NO;
}
static s64 a9_diag_wf_end(struct a9_eink *a) {CHECK(a->eng_lock.depth==1);wf_end_hooks++;return diag_first_us;}
/* F9: HV-on accounting after every real waveform (reads transport state only) */
static unsigned hv_on_calls;
static u64 a9_hv_on_ms(struct a9_eink *a) {(void)a;hv_on_calls++;return 0;}
/* F6: the FP9929A VCOM readback (mock); the verbatim a9_pg_wf_end must call it only with eng_lock free */
#define A9_VCOM_READS_MAX 64
#define lockdep_assert_not_held(l) CHECK((l)->depth==0)
#define dev_info_ratelimited log_message
#define dev_warn log_message
static unsigned vcom_calls; static int vcom_now = 0x7e, vcom_ret;
static int fp9929a_read_vcom(void *np, u8 *now, u8 *prog) {
 (void)np; CHECK(active->eng_lock.depth==0); vcom_calls++;
 if (vcom_ret)
  return vcom_ret;
 *now=(u8)vcom_now; *prog=0x7e; return 0;
}
#define a9e_inc_invalidate(e) ((void)(e))
#include "rail-policy-production.inc"

static struct a9_eink a;
static uint8_t old[A9T_N], saved[A9T_N], dry_saved[A9T_N], traced[A9T_N], dry_levels[A9T_N], grey[A9T_N], shadow[A9T_N];
static void setup(void) {
 memset(&a,0,sizeof(a));memset(old,7,sizeof(old));
 a.e.old=old;a.e.grey=grey;a.shadow=shadow;a.mode=518;a.e.have_pre=1;a.e.pre_mode=518;a.therm=&a;a.hv=&a;active=&a;
 a.prep_save_old=saved;a.dry_save_old=dry_saved;a.trace_old=traced;a.dry_levels=dry_levels;
 a.tx.frames=97;a.tx.late_drive=3;a.tx.on_since=123456789;
 a.updates=11;a.aborts=2;a.last.frames=47;a.last.mode=518;
 trace=false;enable=true;zero_test=0;a9x_stall_fault=a9x_rail_fault=0;prepared_unchanged=true;expect_containment=false;ring_result=0;
 pg_settle_ms=0;settle_result=A9_SETTLE_NO;settle_calls=0;vcom_calls=0;vcom_now=0x7e;vcom_ret=0;hv_on_calls=0;settle_origin_seen=-1;diag_first_us=0;ring_drops_pg=false;
 pg=1;getter_error=status_reads=tx_powerups=force_disables=post_off_reads=0;pg_valid=fp_reports_on=true;pg_sequence_len=0;iio_error=0;
 prepares=powers=poweroffs=frames=commits=starts=temps=0;
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


static struct a9_job ordinary(void) {
 return (struct a9_job){.dry=false,.mode=518,.clear=0,.mask=0,.rect={20,20,21,21}};
}
static void clean_skip(void) {
 stable();CHECK(!powers);CHECK(!poweroffs);CHECK(!tx_powerups);CHECK(!force_disables);
 CHECK(a.aborts==2);CHECK(!a.force_clear);CHECK(!a9x_fault_latched());
 CHECK(a.last.frames==47);CHECK(a.last.mode==518);CHECK(!a.rail.count);
}
static void check_captured_fault(int error,int raw) {
 stable();CHECK(!tx_powerups);CHECK(poweroffs==1);CHECK(a.aborts==3);
 CHECK(a9x_rail_fault);CHECK(!enable);CHECK(a.force_clear);CHECK(!a.tx.on);
 CHECK(a.rail.count==1);CHECK(a.rail.getter==error);CHECK(a.rail.pg_raw==raw);
 CHECK(a.rail.tx_on);CHECK(a.rail.front_null);CHECK(!a.rail.armed);
 CHECK(a.rail.session_ms != -1);CHECK(a.rail.wf_ret==0);
 /* 0042: the deferred report ran after the validator's power-off. Only a
  * confirmed clean shutdown earns one post-off lookup; the mock trace seq
  * never advances, so no record is ever marked valid here. */
 CHECK(!a.rail.pd_valid);CHECK(!a.rail.pd_seq);
 if(a.hv_fault) {CHECK(!a.rail.pd_off_confirmed);CHECK(a.rail.pd_getter==-ENODATA);}
 else {CHECK(a.rail.pd_off_confirmed);CHECK(a.rail.pd_getter==(getter_error?getter_error:0));}
}
int main(void) {
 struct a9_job job=ordinary(), recovery;
 /* Regression reproduces PG low on the duplicate, then high before the next
  * changed target. A new PG=1 observation alone cannot restore eligibility. */
 setup();a.tx.on=true;a.tx.front_null=true;pg=0;expect_containment=true;
 __a9_update(&a,&job);check_captured_fault(0,0);CHECK(prepares==0);
 pg=1;prepared_unchanged=false;enable=true;
 int before_reads=status_reads;
 unsigned before_frames=frames,before_commits=commits;
 __a9_update(&a,&job);
 CHECK(a9x_rail_fault);CHECK(a.force_clear);CHECK(a.aborts==4);CHECK(!tx_powerups);
 CHECK(status_reads==before_reads);CHECK(frames==before_frames);CHECK(commits==before_commits);
 CHECK(!a9_clear_fault_set("0",NULL));CHECK(a9x_rail_fault);CHECK(a.force_clear);
 CHECK(!a9_clear_fault_set("1",NULL));CHECK(!a9x_rail_fault);CHECK(a.force_clear);
 /* Actual selector must retain full GC16 recovery after explicit reset.
  * Only successful real completion consumes the obligation. */
 expect_containment=false;a.kick=true;
 CHECK(a9_take_job(&a,&recovery));CHECK(recovery.mode==A9_MODE_CLEAR);CHECK(recovery.clear==1);
 CHECK(!recovery.mask);CHECK(!recovery.dry);CHECK(recovery.rect.x1==0);CHECK(recovery.rect.y1==0);
 CHECK(recovery.rect.x2==A9T_W);CHECK(recovery.rect.y2==A9T_H);CHECK(a.force_clear);
 ring_result=-EIO;__a9_update(&a,&recovery);CHECK(a.force_clear);CHECK(!commits);CHECK(a.updates==11);
 ring_result=0;__a9_update(&a,&recovery);CHECK(!a.force_clear);CHECK(commits==1);CHECK(a.updates==12);
 CHECK(a.e.pre_mode==A9_MODE_CLEAR);CHECK(a.last.clear==1);CHECK(!a.last.aborted);
 a.kick=true;a.dirty=(struct a9e_rect){10,20,30,40};
 CHECK(a9_take_job(&a,&job));CHECK(job.mode==518);CHECK(job.clear==0);
 /* Preserve the precise bad temperature-path status even if the next
  * available status read is good. A reread must not erase an observed loss. */
 setup();job=ordinary();a.tx.on=true;a.tx.front_null=true;expect_containment=true;
 pg_sequence[0]=0;pg_sequence[1]=1;pg_sequence_len=2;
 __a9_update(&a,&job);check_captured_fault(0,0);CHECK(prepares==0);
 CHECK(status_reads>=1);CHECK(a.rail.pg_raw==0);CHECK(pg==1);
 prepared_unchanged=false;enable=true;before_frames=frames;before_commits=commits;
 __a9_update(&a,&job);CHECK(a9x_rail_fault);CHECK(a.force_clear);CHECK(!tx_powerups);
 CHECK(frames==before_frames);CHECK(commits==before_commits);
 /* Also catch a loss beginning after the good temperature prefilter: the
  * final skip validation must remain a fresh, non-energizing observation. */
 setup();a.tx.on=true;a.tx.front_null=true;expect_containment=true;
 pg_sequence[0]=1;pg_sequence[1]=0;pg_sequence[2]=1;pg_sequence_len=3;
 __a9_update(&a,&job);check_captured_fault(0,0);CHECK(prepares==1);
 /* Fresh startup passes PG, then the post-power temperature read reports a
  * loss. Its sample cannot be discarded before the renderer is admitted. */
 setup();prepared_unchanged=false;expect_containment=true;
 pg_sequence[0]=1;pg_sequence[1]=1;pg_sequence[2]=0;pg_sequence[3]=1;pg_sequence_len=4;
 __a9_update(&a,&job);CHECK(a9x_rail_fault);CHECK(a.force_clear);CHECK(!enable);CHECK(!a.tx.on);
 CHECK(tx_powerups==1);CHECK(poweroffs==1);CHECK(!frames);CHECK(!commits);CHECK(a.updates==11);
 CHECK(a.aborts==3);CHECK(a.rail.pg_raw==0);CHECK(a.e.pre_mode==518);CHECK(a.eng_lock.depth==0);
 bool restored=true;for(size_t i=0;i<A9T_N;i++) if(old[i]!=7) {restored=false;break;}
 CHECK(restored);
 /* An off pipeline may query cached temperature, but must not start just
  * to accept equality. A good on-session must validate without renewal. */
 setup();__a9_update(&a,&job);clean_skip();CHECK(!a.tx.on);CHECK(status_reads==1);
 setup();pg=0;__a9_update(&a,&job);clean_skip();CHECK(!a.tx.on);CHECK(status_reads==1);
 setup();a.tx.on=true;a.tx.front_null=true;__a9_update(&a,&job);clean_skip();CHECK(a.tx.on);CHECK(status_reads==2);
 /* Lookup failures, GPIO errno and missing/false health components all
  * retain distinct capture data and invoke the same synchronous latch. */
 setup();job=ordinary();a.tx.on=true;a.tx.front_null=true;getter_error=-ENODEV;expect_containment=true;
 __a9_update(&a,&job);check_captured_fault(-ENODEV,-ENODATA);CHECK(force_disables==1);
 setup();a.tx.on=true;a.tx.front_null=true;pg=-EIO;expect_containment=true;
 __a9_update(&a,&job);check_captured_fault(0,-EIO);CHECK(!force_disables);
 setup();a.tx.on=true;a.tx.front_null=true;pg_valid=false;expect_containment=true;
 __a9_update(&a,&job);check_captured_fault(0,1);CHECK(!a.rail.pg_valid);
 setup();a.tx.on=true;a.tx.front_null=true;fp_reports_on=false;expect_containment=true;
 __a9_update(&a,&job);check_captured_fault(0,1);CHECK(!a.rail.on);CHECK(a.rail.energized);
 /* A simultaneous missing thermistor cannot hide an already observed
  * rail loss behind temperature-prefilter refusal. */
 setup();job=ordinary();a.tx.on=true;a.tx.front_null=true;pg=0;iio_error=-EIO;expect_containment=true;
 __a9_update(&a,&job);check_captured_fault(0,0);CHECK(prepares==0);
 /* Dry execution reads telemetry and renders its model without turning a
  * bad physical sample into a real latch or changing the live pipeline. */
 setup();job=ordinary();job.dry=true;a.tx.on=true;a.tx.front_null=true;pg=0;
 __a9_update(&a,&job);CHECK(!a9x_fault_latched());CHECK(!a.force_clear);CHECK(a.tx.on);
 CHECK(!tx_powerups);CHECK(!poweroffs);CHECK(!force_disables);CHECK(a.tx.on_since==123456789);
 CHECK(a.e.pre_mode==518);CHECK(a.e.have_pre==1);CHECK(frames==1);CHECK(a.eng_lock.depth==0);
 job=ordinary();
 /* Existing latched faults remain earlier than all temperature/power calls. */
 setup();a9x_stall_fault=1;__a9_update(&a,&job);stable();CHECK(!prepares);CHECK(!status_reads);CHECK(!tx_powerups);CHECK(a9x_stall_fault==1);
 setup();a9x_rail_fault=1;a.force_clear=true;__a9_update(&a,&job);stable();CHECK(!prepares);CHECK(!status_reads);CHECK(!tx_powerups);CHECK(a.force_clear);
 /* Nonduplicate live admission must still contain a dropout after a good
  * prefilter sample, before rendering. */
 setup();a.tx.on=true;a.tx.front_null=true;prepared_unchanged=false;expect_containment=true;
 pg_sequence[0]=1;pg_sequence[1]=0;pg_sequence_len=2;
 __a9_update(&a,&job);check_captured_fault(0,0);CHECK(!frames);
 /* M4: pg_settle on, PG drops at the end of a completed real waveform. The
  * sample is taken under eng_lock, the settle and the latch run after the
  * unlock: a9x_power_off (mock) asserts depth 0 and the containment state;
  * the update itself is committed, the latch counts as an abort, Clear owed. */
 setup();job=ordinary();prepared_unchanged=false;pg_settle_ms=100;ring_drops_pg=true;diag_first_us=777;
 expect_containment=true;
 __a9_update(&a,&job);
 CHECK(a.eng_lock.depth==0);CHECK(frames==1);CHECK(commits==1);CHECK(a.updates==12);CHECK(!a.last.aborted);
 CHECK(settle_calls==1);CHECK(settle_origin_seen==777);CHECK(a.settle_first_us==0);
 CHECK(a9x_rail_fault);CHECK(!enable);CHECK(a.force_clear);CHECK(!a.tx.on);CHECK(poweroffs==1);
 CHECK(a.aborts==3);CHECK(a.rail.count==1);CHECK(a.rail.pg_raw==0);
 /* the recovered dip: no latch, no power-off, no abort, no Clear owed */
 setup();job=ordinary();prepared_unchanged=false;pg_settle_ms=100;ring_drops_pg=true;diag_first_us=777;
 settle_result=A9_SETTLE_OK;
 __a9_update(&a,&job);
 CHECK(a.eng_lock.depth==0);CHECK(commits==1);CHECK(a.updates==12);CHECK(settle_calls==1);
 CHECK(!a9x_rail_fault);CHECK(enable);CHECK(!a.force_clear);CHECK(a.tx.on);CHECK(!poweroffs);CHECK(a.aborts==2);
 /* 0052 read low, 0053 re-read high: no settle, counted (F1) */
 setup();job=ordinary();prepared_unchanged=false;pg_settle_ms=100;diag_first_us=777;
 __a9_update(&a,&job);
 CHECK(!settle_calls);CHECK(a.diag.settle_reread_high==1);CHECK(a.settle_first_us==0);CHECK(!poweroffs);CHECK(a.aborts==2);
 /* pg_settle_ms 0 (default): PG low at the end is left to the next sample, as 0052 */
 setup();job=ordinary();prepared_unchanged=false;ring_drops_pg=true;diag_first_us=777;
 __a9_update(&a,&job);
 CHECK(!settle_calls);CHECK(!a9x_rail_fault);CHECK(a.tx.on);CHECK(!poweroffs);CHECK(commits==1);CHECK(a.aborts==2);
 /* F6 (review 89d): after a recovered wf-end dip exactly one VCOM read, eng_lock free (mock asserts depth 0) */
 setup();job=ordinary();prepared_unchanged=false;pg_settle_ms=100;ring_drops_pg=true;diag_first_us=777;settle_result=A9_SETTLE_OK;
 __a9_update(&a,&job);
 CHECK(vcom_calls==1);CHECK(a.diag.vcom_reads==1);CHECK(a.diag.vcom_ok==1);CHECK(a.vcom_reads==1);CHECK(hv_on_calls==1);
 CHECK(!a9x_rail_fault);CHECK(enable);CHECK(!a.force_clear);CHECK(a.tx.on);CHECK(a.aborts==2);CHECK(commits==1);
 /* a mismatch, then a read error: counters only */
 setup();job=ordinary();prepared_unchanged=false;pg_settle_ms=100;ring_drops_pg=true;settle_result=A9_SETTLE_OK;vcom_now=0x74;
 __a9_update(&a,&job);
 CHECK(vcom_calls==1);CHECK(a.diag.vcom_mismatch==1);CHECK(a.diag.vcom_last==0x74);
 CHECK(!a9x_rail_fault);CHECK(enable);CHECK(!a.force_clear);CHECK(a.tx.on);CHECK(a.aborts==2);
 setup();job=ordinary();prepared_unchanged=false;pg_settle_ms=100;ring_drops_pg=true;settle_result=A9_SETTLE_OK;vcom_ret=-EIO;
 __a9_update(&a,&job);
 CHECK(vcom_calls==1);CHECK(a.diag.vcom_err==1);CHECK(!a9x_rail_fault);CHECK(enable);CHECK(a.tx.on);CHECK(a.aborts==2);
 /* zero reads: settle NO (latch), TIMEOUT (latch), pg_settle_ms 0, a good PG at the end */
 setup();job=ordinary();prepared_unchanged=false;pg_settle_ms=100;ring_drops_pg=true;expect_containment=true;settle_result=A9_SETTLE_NO;
 __a9_update(&a,&job);CHECK(!vcom_calls);CHECK(a9x_rail_fault);
 setup();job=ordinary();prepared_unchanged=false;pg_settle_ms=100;ring_drops_pg=true;expect_containment=true;settle_result=A9_SETTLE_TIMEOUT;
 __a9_update(&a,&job);CHECK(!vcom_calls);CHECK(a9x_rail_fault);
 setup();job=ordinary();prepared_unchanged=false;ring_drops_pg=true;settle_result=A9_SETTLE_OK;
 __a9_update(&a,&job);CHECK(!vcom_calls);
 setup();job=ordinary();prepared_unchanged=false;pg_settle_ms=100;settle_result=A9_SETTLE_OK;
 __a9_update(&a,&job);CHECK(!vcom_calls);CHECK(!settle_calls);
 /* after 64 reads in the boot: no further read, the dip still recovers */
 setup();job=ordinary();prepared_unchanged=false;pg_settle_ms=100;ring_drops_pg=true;settle_result=A9_SETTLE_OK;a.vcom_reads=64;
 __a9_update(&a,&job);CHECK(!vcom_calls);CHECK(settle_calls==1);CHECK(!a9x_rail_fault);CHECK(a.tx.on);
 printf("rail/duplicate policy: %u production integration cases, %u assertions passed\n",cases,assertions);
 return 0;
}
