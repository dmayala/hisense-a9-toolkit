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
	/* 0054 (damage_prep): a9_thread invalidates the incremental state when a
	 * taken job was dropped before its prepare; engine counters + lock only */
	struct { u64 full_prepares, inc_prepares; } e;
	int eng_lock;
};
static unsigned hv_hold_ms, hv_max_ms, hv_hold_interactive_ms = 8000, hv_interactive_window_ms = 10000;
static s64 now, first_off, last_off, resume_at; static unsigned off_count; static bool keep_going;
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
#define HZ 1000
#define A9_IDLE_BUDGET_MS 15000u
#define MAX_SCHEDULE_TIMEOUT 999999999
#define msecs_to_jiffies(v) (v)
#define smp_store_release(p,v) (*(p)=(v))
#define smp_mb() ((void)0)
#define mutex_lock(l) ((void)(l))
#define mutex_unlock(l) ((void)(l))
#define a9e_inc_invalidate(e) ((void)(e))
#define wake_up_all(wq) ((void)0)
#define wait_event_interruptible(wq,cond) do { if (resume_at) { now=resume_at; active->suspending=false; active->kick=false; } } while (0)
#define wait_event_interruptible_timeout(wq,cond,tmo) wait_mock(tmo)
static void wait_mock(long tmo)
{
	assert(++nwait < 20);
	if (active->kick) return;
	if (next_event < event_count && events[next_event].at <= now + tmo) {
		current = events[next_event++];
		if (current.at > now) now = current.at;
		if (resume_at && current.at == 4000) active->suspending = true; /* suspend request, not an update */
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
	if (a->tx.on) { last_off = now; off_count++; }
	a->tx.on = false;
	if (!keep_going) stop = 1;
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
	if (keep_going && !a->tx.on && !j->dry) { a->tx.on = true; a->tx.on_since = now; }
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
	now = 0; first_off = -1; last_off = -1; off_count = 0; keep_going = false; resume_at = 0;
	nwait = updates = powers = stop = next_event = 0;
	initial_kick=false; require_live_check=false;
	live_checks=sequence=check_order=first_off_order=0; event_count = 1; hv_hold_ms = 3000; hv_max_ms = 60000; hv_hold_interactive_ms = 8000; hv_interactive_window_ms = 10000;
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
	setup(); events[0]=(struct event){.at=1000,.work=100,.frames=8};
	run_case("single real update: base hold", 4100, 1);
	setup(); event_count=2;
	events[0]=(struct event){.at=1000,.work=100,.frames=8};
	events[1]=(struct event){.at=3000,.work=100,.frames=8};
	run_case("second real update inside window: 8 s hold", 11100, 2);
	setup(); event_count=2; hv_interactive_window_ms=1500;
	events[0]=(struct event){.at=1000,.work=100,.frames=8};
	events[1]=(struct event){.at=4000,.work=100,.frames=8};
	run_case("outside window: base hold", 7100, 2);
	setup(); event_count=2; hv_hold_interactive_ms=0;
	events[0]=(struct event){.at=1000,.work=100,.frames=8};
	events[1]=(struct event){.at=3000,.work=100,.frames=8};
	run_case("disabled by 0", 6100, 2);
	setup(); event_count=2; hv_hold_ms=9000;
	events[0]=(struct event){.at=1000,.work=100,.frames=8};
	events[1]=(struct event){.at=3000,.work=100,.frames=8};
	run_case("never below hv_hold_ms", 12100, 2);
	setup(); event_count=2; hv_hold_ms=0; initial_kick=true;
	events[0]=(struct event){.work=100,.frames=8,.pending=true};
	events[1]=(struct event){.work=100,.frames=8};
	run_case("hold 0 is never extended", 200, 2);
	setup(); event_count=2;
	events[0]=(struct event){.at=1000,.work=100,.frames=8};
	events[1]=(struct event){.at=3000,.work=100,.frames=8};
	hv_hold_interactive_ms=50000;
	run_case("interactive hold clamped to 10 s", 13100, 2);
	setup(); event_count=3; hv_max_ms=4000;
	events[0]=(struct event){.at=1000,.work=100,.frames=8};
	events[1]=(struct event){.at=3000,.work=100,.frames=8};
	events[2]=(struct event){.at=5000,.work=100,.frames=8};
	run_case("hv_max_ms caps a boosted session in the idle wait", 4000, 2);
	setup(); event_count=2;
	events[0]=(struct event){.at=1000,.work=100,.frames=8};
	events[1]=(struct event){.at=3000,.work=100,.dry=true};
	run_case("dry job neither boosts nor renews", 4100, 2);
		/* review 66 regressions: the mock keeps running across shutdowns */
	struct a9_eink a;
	/* P1a: hv_max 15 s, last output ends 13100, no further kick: orderly
	 * close at the software budget (15000), 5 s before the FP 20 s cap */
	setup(); event_count=4; hv_max_ms=15000;
	events[0]=(struct event){.at=1000,.work=100,.frames=8};
	events[1]=(struct event){.at=3000,.work=100,.frames=8};
	events[2]=(struct event){.at=9000,.work=100,.frames=8};
	events[3]=(struct event){.at=13000,.work=100,.frames=8};
	run_case("idle wait respects the remaining budget", 15000, 4);
	/* the default hv_max_ms (60000) is still held to 15 s */
	setup(); event_count=4;
	events[0]=(struct event){.at=1000,.work=100,.frames=8};
	events[1]=(struct event){.at=3000,.work=100,.frames=8};
	events[2]=(struct event){.at=9000,.work=100,.frames=8};
	events[3]=(struct event){.at=13000,.work=100,.frames=8};
	run_case("default hv_max_ms clipped to 15 s", 15000, 4);
	/* P1b: rollover then a lone update outside the window: base hold again */
	setup(); keep_going=true; event_count=3; hv_max_ms=5000; hv_interactive_window_ms=1500;
	events[0]=(struct event){.at=1000,.work=100,.frames=8};
	events[1]=(struct event){.at=2000,.work=100,.frames=8};
	events[2]=(struct event){.at=6000,.work=100,.frames=8};
	a = (struct a9_eink){ .tx = { .on=true, .on_since=0, .frames=100 } };
	active=&a; a9_thread(&a);
	printf("rollover: first off=%lld final off=%lld count=%u\n", first_off, last_off, off_count);
	assert(first_off==5000 && last_off==9100 && off_count==2 && updates==3);
	/* history crosses a session boundary on purpose: tap 3.7 s after an idle
	 * power-down, inside the 10 s window, boosts the new session */
	setup(); keep_going=true; event_count=2;
	events[0]=(struct event){.at=1000,.work=100,.frames=8};
	events[1]=(struct event){.at=7800,.work=100,.frames=8};
	a = (struct a9_eink){ .tx = { .on=true, .on_since=0, .frames=100 } };
	active=&a; a9_thread(&a);
	printf("cross-session history: first off=%lld final off=%lld count=%u\n", first_off, last_off, off_count);
	assert(first_off==4100 && last_off==15900 && off_count==2); /* 7900+8000 */
	/* suspend: no boost survives, even inside the default window */
	setup(); keep_going=true; resume_at=20000; event_count=4; hv_max_ms=15000;
	events[0]=(struct event){.at=1000,.work=100,.frames=8};
	events[1]=(struct event){.at=3000,.work=100,.frames=8};
	events[2]=(struct event){.at=4000};
	events[3]=(struct event){.at=20000,.work=100,.frames=8};
	a = (struct a9_eink){ .tx = { .on=true, .on_since=0, .frames=100 } };
	active=&a; a9_thread(&a);
	printf("suspend: first off=%lld final off=%lld count=%u\n", first_off, last_off, off_count);
	assert(first_off==4000 && last_off==23100 && off_count==2);
	puts("PASS 9 candidate-0048 cases + 5 review-66 regressions (multi-session)");
}
