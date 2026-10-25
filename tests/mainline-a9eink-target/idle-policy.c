#include <stdio.h>
#include <stdbool.h>
#include <assert.h>
#include <string.h>
typedef unsigned long long u64;
typedef long long s64;
typedef long long ktime_t;
struct a9_job { bool dry; };
struct a9_eink {
	bool suspending, parked, hv_fault, kick, busy;
	struct { bool on, unreg_hv_unknown; s64 on_since; u64 frames; } tx;
	int wq;
	/* 0054: a9_thread invalidates the incremental state for a dropped job */
	struct { u64 full_prepares, inc_prepares; } e;
	int eng_lock;
};
static unsigned hv_hold_ms, hv_max_ms;
/* 0048 v2 at its production default (interactive hold off); run-boost.sh covers it on */
static unsigned hv_hold_interactive_ms, hv_interactive_window_ms = 10000;
static s64 now, first_off;
static unsigned nwait, updates, powers, stop, event_count, next_event;
static bool initial_kick, require_live_check;
static unsigned live_checks, sequence, check_order, first_off_order;
static struct a9_eink *active;
struct event { s64 at, work; unsigned frames; bool dry, reject, pending; };
static struct event events[8], current;
#define READ_ONCE(v) (v)
#define WRITE_ONCE(v,x) ((v)=(x))
#define min(a,b) ((a)<(b)?(a):(b))
#define max(a,b) ((a)>(b)?(a):(b))
#define A9_IDLE_BUDGET_MS 15000u
#define mutex_lock(l) ((void)(l))
#define mutex_unlock(l) ((void)(l))
#define a9e_inc_invalidate(e) ((void)(e))
#define HZ 1000
#define MAX_SCHEDULE_TIMEOUT 999999999
#define msecs_to_jiffies(v) (v)
#define smp_store_release(p,v) (*(p)=(v))
#define smp_mb() ((void)0)
#define wake_up_all(wq) ((void)0)
#define wait_event_interruptible(wq,cond) ((void)0)
#define wait_event_interruptible_timeout(wq,cond,tmo) wait_mock(tmo)
static void wait_mock(long tmo)
{
	assert(++nwait < 20);
	if (active->kick) return;
	if (next_event < event_count && events[next_event].at <= now + tmo) {
		current = events[next_event++];
		if (current.at > now) now = current.at;
		active->kick = true;
	} else {
		if (!active->tx.on) { stop = 1; return; }
		now += tmo;
	}
}
static bool kthread_should_stop(void) { return stop; }
static s64 ktime_get(void) { return now; }
static s64 ktime_ms_delta(s64 a, s64 b) { return a-b; }
static void a9_power_off(struct a9_eink *a)
{
	if (a->tx.on && first_off < 0) { first_off = now; first_off_order = ++sequence; }
	a->tx.on = false;
	stop = 1;
}
static int a9_check_live_rails(struct a9_eink *a)
{
	assert(a->tx.on);
	live_checks++;
	check_order = ++sequence;
	return 0;
}
static int a9_load_waveform(struct a9_eink *a) { (void)a; return current.reject; }
static bool a9_take_job(struct a9_eink *a, struct a9_job *j)
{ a->kick = false; j->dry = current.dry; return true; }
static void a9_update(struct a9_eink *a, const struct a9_job *j)
{
	updates++;
	now += current.work;
	if (!j->dry) a->tx.frames += current.frames;
	if (current.pending) {
		assert(next_event < event_count);
		current = events[next_event++];
		a->kick = true;
	}
}
#include "idle-policy.inc"

static void setup(void)
{
	memset(events, 0, sizeof(events)); memset(&current, 0, sizeof(current));
	now = 0; first_off = -1;
	nwait = updates = powers = stop = next_event = 0;
	initial_kick=false; require_live_check=false;
	live_checks=sequence=check_order=first_off_order=0; event_count = 1; hv_hold_ms = 3000; hv_max_ms = 60000;
}
static void run_case(const char *name, s64 want_off, unsigned want_updates)
{
	struct a9_eink a = { .tx = { .on = true, .on_since = 0, .frames = 100 } };
	active = &a;
	if (initial_kick) { current=events[next_event++]; a.kick=true; }
	a9_thread(&a);
	printf("%s: off=%lld updates=%u on_since=%lld\n", name, first_off, updates, a.tx.on_since);
	assert(first_off == want_off);
	if (require_live_check) assert(live_checks && check_order < first_off_order);
	assert(a.tx.on_since == 0 && updates == want_updates && !powers);
}
int main(void)
{
	setup(); events[0].at = 2900;
	run_case("duplicate at 2900", 3000, 1);
	setup(); event_count = 3;
	for (unsigned k=0;k<3;k++) events[k].at = (k+1)*100;
	run_case("duplicates at 100/200/300", 3000, 3);
	setup(); events[0] = (struct event){.at=2900,.work=100,.frames=8};
	run_case("real complete update", 6000, 1);
	setup(); events[0] = (struct event){.at=2900,.work=200};
	run_case("duplicate preparation passes deadline", 3100, 1);
	setup(); events[0] = (struct event){.at=2900,.dry=true};
	run_case("dry job", 3000, 1);
	setup(); events[0] = (struct event){.at=2900,.work=50,.frames=3};
	run_case("real zero-test template output", 5950, 1);
	setup(); events[0] = (struct event){.at=2900,.work=100,.frames=100};
	run_case("real init plus update", 6000, 1);
	setup(); event_count=0; hv_hold_ms=0;
	run_case("hold zero", 0, 0);
	setup(); event_count=2;
	events[0]=(struct event){.at=2900,.work=200,.pending=true};
	events[1]=(struct event){.at=3100,.pending=false};
	run_case("pending duplicate cannot avoid expired deadline", 3100, 1);
	setup(); events[0]=(struct event){.at=2900,.reject=true};
	run_case("waveform load refusal", 3000, 0);
	setup(); hv_hold_ms=10000; hv_max_ms=4000;
	events[0]=(struct event){.at=4100};
	/* 0048 v2 (review 66b): the idle wait itself is clipped to hv_max_ms
	 * (a9_idle_clip), so HV goes off at the cap (4000) instead of at the
	 * next job's between-job check (4100, the pre-0048 expectation) */
	run_case("existing between-job HV cap (idle wait clipped, 0048 v2)", 4000, 0);
	setup(); hv_hold_ms=0; initial_kick=true;
	events[0]=(struct event){.work=100,.frames=8};
	run_case("hold zero drops immediately after unqueued output", 100, 1);
	setup(); hv_hold_ms=0; initial_kick=true; event_count=2;
	events[0]=(struct event){.work=100,.frames=8,.pending=true};
	events[1]=(struct event){.work=100,.frames=8};
	run_case("hold zero keeps queued physical successor", 200, 2);
	setup(); hv_hold_ms=0; initial_kick=true; event_count=2;
	events[0]=(struct event){.pending=true};
	events[1]=(struct event){.frames=8};
	run_case("hold zero queued duplicate cannot retain HV", 0, 1);
	setup(); require_live_check=true; events[0]=(struct event){.at=3000,.work=100,.frames=8};
	run_case("physical job at expiry cannot erase owed idle-off", 3000, 1);
	puts("PASS 15 deterministic production-worker cases; original transport origin preserved");
}
