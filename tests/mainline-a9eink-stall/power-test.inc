/*
 * Patch 0039 (run 11): the ACTUAL a9_power_on/a9_power_off/a9_rail_fault/
 * a9_take_job, extracted verbatim (extract-power.py), against the real
 * transport and guard. Only the FP9929A status getter (a PG/getter script),
 * regulator_force_disable (a recorder) and the `enable` parameter are mocked.
 *
 * Run 11: PG read low at an update's start with tx.on; the kernel powered
 * down, but the NEXT update re-powered the FP9929A and submitted a partial
 * (clear=0) frame before the userspace watcher closed the gates.
 */
static int pw_get_ret;		/* fp9929a_get_status() return (lookup error) */
static int pw_pg = 1;		/* raw PG read: 1, 0 or -errno */
static int pw_get_calls, pw_force_disables;
static int pw_off_pg = 1, pw_off_get_calls, pw_preoff_logs;
static u64 pw_pd_seq = 7;
static bool pw_pd_new = true;
static bool mask_outside_damage;
static struct a9_eink *pw_a9;
static int pw_regulator_dummy;
/* 0053 module parameters (the driver declares them with module_param) */
static unsigned int pg_settle_ms, pg_settle_budget = 3, pg_settle_total_max = 1000;	/* + M4 cap */
/* F9 token bucket (review 89d) */
static unsigned int pg_settle_rate_n = 15, pg_settle_rate_window_s = 600, pg_settle_rate_burst = 6;
#define A9_VCOM_READS_MAX 64
/* F6: the FP9929A VCOM readback and the vblank source, mocked; the readback asserts eng_lock is free */
static int pw_vcom_calls, pw_vcom_ret, pw_vcom_now = 0x7e, pw_vcom_prog = 0x7e, pw_vcom_lock_depth = -1;
static int fp9929a_read_vcom(struct device_node *np, u8 *now, u8 *prog);
#define lockdep_assert_not_held(l) CHECK(!mutex_is_locked(l), "lockdep: %s held", #l)
static bool damage_prep;	/* eink-damage module parameter */
static u64 pw_pg_good_from_ns;	/* PG reads 1 from this mock time on (0 = follow pw_pg only) */
static bool pw_pg_flap;		/* PG alternates 0/1 on every read */
static u64 pw_pg_frame_from;	/* F6b: from this mock frame on, PG is good in the first 4 ms of every frame only */
#ifndef min
#define min(a, b) ((a) < (b) ? (a) : (b))
#endif
#ifndef min_t
#define min_t(t, a, b) ((t)(a) < (t)(b) ? (t)(a) : (t)(b))
#endif
#ifndef U32_MAX
#define U32_MAX 0xffffffffu
#endif

static inline s64 ktime_to_ms(ktime_t t) { return t / 1000000; }
static inline ktime_t us_to_ktime(u64 us) { return (ktime_t)(us * 1000); }
#ifndef EPROBE_DEFER
#define EPROBE_DEFER 517
#endif
#define dev_crit_ratelimited(d, ...) mock_log("crit", __VA_ARGS__)
#define dev_info_ratelimited(d, ...) mock_log("info", __VA_ARGS__)
#define regulator_force_disable pw_regulator_force_disable
static int pw_regulator_force_disable(struct regulator *r)
{
	(void)r;
	pw_force_disables++;
	return 0;
}

#include "a9_eink_pattern.h"
#define fp9929a_get_status pw_get_status
struct fp9929a_status;
static int pw_get_status(struct device_node *np, struct fp9929a_status *st);
#define enable fake_enable
#include "driver-power.inc"
#undef enable
#undef fp9929a_get_status

static void pw_observe_log(const char *message)
{
	if ((strstr(message, "FP9929A dropped out while the pipeline was on") ||
	     strstr(message, "rail shutdown PG samples")) && T->on)
		pw_preoff_logs++;
}

/* The rails follow the transport (CRTC on = panel prepared = FP9929A up). */
static int fp9929a_read_vcom(struct device_node *np, u8 *now, u8 *prog)
{
	(void)np;
	pw_vcom_calls++;
	pw_vcom_lock_depth = mutex_is_locked(&pw_a9->eng_lock);
	if (pw_vcom_ret)
		return pw_vcom_ret;
	*now = (u8)pw_vcom_now;
	*prog = (u8)pw_vcom_prog;
	return 0;
}

static int pw_get_status(struct device_node *np, struct fp9929a_status *st)
{
	(void)np;
	pw_get_calls++;
	if (!T->on)
		pw_off_get_calls++;
	if (pw_get_ret)
		return pw_get_ret;
	if (st) {
		st->on = READ_ONCE(T->on);
		st->energized = st->on;
		st->forced_off = false;
		st->emergency = atomic_load(&n_emerg) > 0;
		st->temp_valid = true;
		st->temp_c = 25;
		st->pg_valid = true;
		st->pg_raw = st->on ? pw_pg : pw_off_pg; /* same read supplies pg_good */
		if (st->on && pw_pg_good_from_ns && mock_now_ns() >= pw_pg_good_from_ns)
			st->pg_raw = 1;
		if (st->on && pw_pg_flap)
			st->pg_raw = pw_get_calls & 1;
		if (st->on && pw_pg_frame_from) {	/* F6b: PG follows the 85 Hz scan */
			u64 fr = mock_now_ns() / 11764000ull, ph = mock_now_ns() % 11764000ull;

			st->pg_raw = fr >= pw_pg_frame_from && ph < 4000000ull;
		}
		st->pg_at_us = (s64)(mock_now_ns() / 1000);
		st->pdtrace.seq = pw_pd_seq + (!st->on && pw_pd_new && !atomic_load(&n_emerg));
		st->pdtrace.entry = (struct fp9929a_pg_point){ st->pg_at_us - 51000, 1 };
		st->pdtrace.vcom_low = (struct fp9929a_pg_point){ st->pg_at_us - 50000, 1 };
		st->pdtrace.pwrup_low = (struct fp9929a_pg_point){ st->pg_at_us, 0 };
		st->pg_good = st->pg_raw == 1;
	}
	return 0;
}

static void pw_setup(const char *name)
{
	setup(name);
	mock_hrtimer_stop(&T->stall);	/* discard setup's transport */
	pw_a9 = calloc(1, sizeof(*pw_a9));
	T = &pw_a9->tx;
	T->dev = &mdev;
	T->panel_np = &panel_np;
	T->fourcc = 0x34325258;
	T->tmpl = tmpl;
	T->fault = test_fault;
	mutex_init(&T->lock);
	a9x_init(T);
	CHECK(!a9x_attach(T), "attach");
	pw_a9->dev = &mdev;
	pw_a9->hv = (struct regulator *)&pw_regulator_dummy;
	mutex_init(&pw_a9->shadow_lock);
	mutex_init(&pw_a9->eng_lock);
	pw_a9->shadow = calloc(1, A9T_N);
	pw_a9->e.grey = calloc(1, A9T_N);
	pw_a9->mode = 518;
	pw_a9->e.have_pre = 1;
	pw_a9->e.pre_mode = 518;
	pw_get_ret = 0;
	pw_pg = 1;
	pw_get_calls = pw_force_disables = 0;
	pw_off_get_calls = pw_preoff_logs = 0;
	pw_off_pg = 1;
	pg_settle_ms = 0;
	pg_settle_budget = 3;
	pg_settle_total_max = 1000;
	pg_settle_rate_n = 15;
	pg_settle_rate_window_s = 600;
	pg_settle_rate_burst = 6;
	pw_vcom_calls = pw_vcom_ret = 0;
	pw_vcom_now = pw_vcom_prog = 0x7e;
	pw_vcom_lock_depth = -1;
	pw_pg_good_from_ns = 0;
	pw_pg_flap = false;
	pw_pg_frame_from = 0;
	pw_pd_new = true;
	pw_pd_seq = 7;
	log_observer = pw_observe_log;
	mask_outside_damage = false;
	damage_prep = false;
	atomic_set(&a9x_rail_fault, 0);
	/* mirrors the probe-time init of the log-only counters (0052) */
	spin_lock_init(&pw_a9->diag.lock);
	pw_a9->diag.end_first_low_ms = -1;
	pw_a9->diag.end_last_low_ms = -1;
	pw_a9->diag.end_last_pg = -ENODATA;
}

/* the next queued job, as the worker would take it after a damage commit */
static bool pw_next_job(struct a9_job *job)
{
	mutex_lock(&pw_a9->shadow_lock);
	pw_a9->kick = true;
	pw_a9->dirty = (struct a9e_rect){ 10, 20, 30, 40 };
	mutex_unlock(&pw_a9->shadow_lock);
	return a9_take_job(pw_a9, job);
}

/* power up, run a short waveform (drive frames then neutral), as in run 11 */
static void pw_waveform(void)
{
	CHECK(!a9_power_on(pw_a9), "power on with PG good");
	CHECK(T->on, "transport not on");
	for (int i = 0; i < 3; i++)
		CHECK(!a9x_show(T, drive), "drive frame %d", i);
	CHECK(!a9x_blank(T), "neutral");
	pw_a9->wf_end = ktime_get();
	pw_a9->wf_ret = 0;
}

/* (49) run 11: PG low at the start of the next update */
static void case_49(void)
{
	struct a9_job job;
	int drives, commits, modesets, ret;

	pw_setup("(49) [0039] run 11: PG low at update start -> capture, latch, ordered power-down; no re-power, no drive until clear");
	pw_waveform();
	mock_sleep_us(26000);		/* run 11: detected 26.7 ms after completion */
	pw_pg = 0;
	ret = a9_power_on(pw_a9);
	CHECK(ret == -EIO, "dropout returned %d", ret);
	CHECK(atomic_read(&a9x_rail_fault) && !fake_enable, "rail fault not latched / drive still enabled");
	CHECK(pw_a9->force_clear, "no full Clear owed");
	CHECK(!T->on && T->front_null && !T->stall_armed && dpms_calls == 1, "ordered power-down missing");
	CHECK(!atomic_load(&n_emerg), "neutral was fine: no emergency expected");
	/* the capture describes the moment of detection, not the power-down */
	CHECK(pw_a9->rail.count == 1 && pw_a9->rail.getter == 0 && pw_a9->rail.pg_raw == 0 &&
	      pw_a9->rail.on && pw_a9->rail.energized && pw_a9->rail.pg_valid && !pw_a9->rail.pg_good &&
	      !pw_a9->rail.forced && !pw_a9->rail.emergency,
	      "fp capture: n%llu getter %d pg_raw %d on %d en %d", (unsigned long long)pw_a9->rail.count,
	      pw_a9->rail.getter, pw_a9->rail.pg_raw, pw_a9->rail.on, pw_a9->rail.energized);
	CHECK(pw_a9->rail.tx_on && pw_a9->rail.front_null && !pw_a9->rail.armed && !pw_a9->rail.tx_gone,
	      "tx capture: on %d null %d armed %d", pw_a9->rail.tx_on, pw_a9->rail.front_null,
	      pw_a9->rail.armed);
	CHECK(pw_a9->rail.session_ms >= 26 && pw_a9->rail.since_wf_ms >= 26 &&
	      pw_a9->rail.since_wf_ms < pw_a9->rail.session_ms + 1 && pw_a9->rail.wf_ret == 0,
	      "ages: session %lld wf %lld", (long long)pw_a9->rail.session_ms,
	      (long long)pw_a9->rail.since_wf_ms);
	CHECK(log_has("FP9929A dropped out while the pipeline was on: rail fault latched (getter 0 pg_raw 0 on 1 energized 1"),
	      "no structured fault line");
	/* the subsequent update of run 11, with PG good again and enable rewritten */
	drives = atomic_load(&observed_drives);
	commits = ncommits;
	modesets = full_client_calls;
	pw_pg = 1;
	mock_sleep_us(250000);
	fake_enable = true;		/* an enable-only write must not bypass */
	ret = a9_power_on(pw_a9);
	CHECK(ret == -EIO && !T->on, "re-powered after the rail fault (%d)", ret);
	CHECK(ncommits == commits && full_client_calls == modesets, "a commit/modeset ran while latched");
	/* even a direct transport power-up cannot commit a drive frame */
	CHECK(!a9x_power_on(T), "transport idle power-up");
	CHECK(a9x_show(T, drive) == -EIO, "drive commit accepted while the rail fault is latched");
	CHECK(atomic_load(&observed_drives) == drives, "a drive frame reached scanout");
	CHECK(!a9x_power_off(T), "transport off");
	/* A queued manual clear cannot downgrade latched recovery. */
	pw_a9->manual_clear_req = true;
	CHECK(pw_next_job(&job) && job.clear == 1 && job.mode == A9_MODE_CLEAR,
	      "next job clear %d mode %#x", job.clear, job.mode);
	check_no_emergency();
	teardown();
}

/* (50) after clear_fault: the first power-up's update is a full Clear */
static void case_50(void)
{
	struct a9_job job;

	pw_setup("(50) [0039] clear_fault: power-up allowed again, first job a full Clear, partial only after it completed");
	pw_waveform();
	pw_pg = 0;
	CHECK(a9_power_on(pw_a9) == -EIO && atomic_read(&a9x_rail_fault), "latch");
	pw_pg = 1;
	CHECK(a9_clear_fault_set("0", NULL) == 0 && atomic_read(&a9x_rail_fault), "clear_fault=0 cleared it");
	CHECK(a9_power_on(pw_a9) == -EIO, "power-up before the clear");
	CHECK(a9_clear_fault_set("1", NULL) == 0 && !atomic_read(&a9x_rail_fault), "clear_fault=1 kept it");
	CHECK(log_has("rail fault cleared (clear_fault)"), "no clear log");
	CHECK(pw_a9->force_clear, "clear_fault dropped the owed full Clear");
	pw_a9->manual_clear_req = true; /* user clear after the latch reset */
	fake_enable = true;		/* the operator reopens the gate */
	CHECK(pw_next_job(&job) && job.clear == 1 && job.mode == A9_MODE_CLEAR &&
	      job.rect.x1 == 0 && job.rect.y1 == 0 && job.rect.x2 == A9T_W && job.rect.y2 == A9T_H &&
	      !job.dry, "first job after clear: clear %d mode %#x rect %d,%d-%d,%d dry %d",
	      job.clear, job.mode, job.rect.x1, job.rect.y1, job.rect.x2, job.rect.y2, job.dry);
	CHECK(!pw_a9->manual_clear_req && pw_a9->force_clear, "manual clear erased recovery");
	mock_sleep_us(250000);
	CHECK(!a9_power_on(pw_a9) && T->on, "power-up after the clear");
	/* still owed until a real update completes (__a9_update clears it there) */
	CHECK(pw_next_job(&job) && job.clear == 1, "a second job before the Clear completed is partial");
	pw_a9->force_clear = false;	/* the Clear completed */
	CHECK(pw_next_job(&job) && job.clear == 0 && job.mode == 518, "partial updates resume after the Clear");
	CHECK(!a9x_power_off(T), "off");
	check_no_emergency();
	teardown();
}

/* (51) rail fault with a drive frame armed and a failing neutral: emergency */
static void case_51(void)
{
	pw_setup("(51) [0039] rail fault, drive frame armed, neutral fails: immediate emergency HV off before DPMS");
	CHECK(!a9_power_on(pw_a9), "power on");
	CHECK(!a9x_show(T, drive), "drive");
	pw_pg = 0;
	next_mode[ncommits] = C_FAIL;	/* the power-down's neutral commit */
	CHECK(a9_power_on(pw_a9) == -EIO, "dropout");
	CHECK(pw_a9->rail.armed && !pw_a9->rail.front_null, "capture: armed %d null %d",
	      pw_a9->rail.armed, pw_a9->rail.front_null);
	CHECK(atomic_load(&n_emerg) >= 1 && atomic_read(&a9x_stall_fault) && atomic_read(&a9x_rail_fault),
	      "emergency %d stall %d rail %d", atomic_load(&n_emerg), atomic_read(&a9x_stall_fault),
	      atomic_read(&a9x_rail_fault));
	CHECK(emerg_t_ns && dpms_t_ns && emerg_t_ns <= dpms_t_ns, "emergency not before DPMS off");
	CHECK(!T->on && dpms_calls == 1, "transport not off");
	teardown();
}

/* (52) rail fault and the transport power-down fails: regulator forced off */
static void case_52(void)
{
	pw_setup("(52) [0039] rail fault, DPMS off fails: FP9929A forced down, hv_fault, no power-up");
	pw_waveform();
	pw_pg = 0;
	dpms_ret = -EBUSY;
	CHECK(a9_power_on(pw_a9) == -EIO, "dropout");
	CHECK(pw_force_disables == 1 && pw_a9->hv_fault, "force-disable %d hv_fault %d",
	      pw_force_disables, pw_a9->hv_fault);
	CHECK(atomic_read(&a9x_rail_fault) && log_has("pipeline power-down failed"), "latch/log");
	dpms_ret = 0;
	pw_pg = 1;
	fake_enable = true;
	CHECK(a9_power_on(pw_a9) == -EIO, "power-up while latched");
	teardown();
}

/* (53) lookup error and GPIO error are captured distinctly, both latch */
static void case_53(void)
{
	pw_setup("(53) [0039] getter lookup error vs PG read error: distinct capture, both latch");
	pw_waveform();
	pw_get_ret = -EPROBE_DEFER;
	CHECK(a9_power_on(pw_a9) == -EIO, "lookup error");
	CHECK(pw_a9->rail.getter == -EPROBE_DEFER && pw_a9->rail.pg_raw == -ENODATA &&
	      !pw_a9->rail.on && atomic_read(&a9x_rail_fault),
	      "lookup capture: getter %d pg_raw %d on %d", pw_a9->rail.getter, pw_a9->rail.pg_raw,
	      pw_a9->rail.on);
	pw_get_ret = 0;
	CHECK(!a9_clear_fault_set("1", NULL), "clear");
	mock_sleep_us(250000);
	pw_waveform();
	pw_pg = -EIO;
	CHECK(a9_power_on(pw_a9) == -EIO, "GPIO error");
	CHECK(pw_a9->rail.getter == 0 && pw_a9->rail.pg_raw == -EIO && pw_a9->rail.on &&
	      !pw_a9->rail.pg_good && pw_a9->rail.count == 2 && atomic_read(&a9x_rail_fault),
	      "gpio capture: getter %d pg_raw %d n %llu", pw_a9->rail.getter, pw_a9->rail.pg_raw,
	      (unsigned long long)pw_a9->rail.count);
	check_no_emergency();
	teardown();
}

/* (54) the ACTUAL run_ring after a rail fault: an enable write cannot start drive */
static void case_54(void)
{
	struct sha256_ctx sc;
	bool shown = false;
	int drives, ret;

	setup("(54) [0039] actual run_ring after a rail fault: enable=1 alone posts no job and drives nothing");
	atomic_set(&a9x_rail_fault, 0);
	job_setup();			/* transport powered, PG good */
	pw_pg = 1;
	pw_get_ret = 0;
	CHECK(!a9_power_on(job_a9), "PG good: power_on keeps the pipeline");
	pw_pg = 0;
	CHECK(a9_power_on(job_a9) == -EIO && atomic_read(&a9x_rail_fault) && !T->on, "dropout latch");
	pw_pg = 1;
	fake_enable = true;
	drives = atomic_load(&observed_drives);
	job_prepare(5, &sc);
	ret = a9_run_ring(job_a9, false, job_bin, &sc, &shown);
	CHECK(ret == -EIO && !shown, "run_ring ret %d", ret);
	CHECK(atomic_load(&observed_drives) == drives && !atomic_load(&job_frames),
	      "frames rendered/driven while latched");
	job_end();
	check_no_emergency();
	teardown();
}

/* (55) PG can recover during shutdown without undoing the original fault. */
static void case_55(void)
{
	int calls;

	pw_setup("(55) [0039b] post-off PG recovery is passive; original timestamp/counters frozen, report deferred");
	pw_waveform();
	pw_a9->session.waveforms = 2;
	pw_a9->session.full_started = 2;
	pw_a9->session.full_done = 1;
	pw_a9->session.full_frames = 3;
	pw_pg = 0;
	pw_off_pg = 1;
	calls = pw_get_calls;
	CHECK(a9_power_on(pw_a9) == -EIO, "fault");
	CHECK(pw_get_calls == calls + 3 && pw_off_get_calls == 2,
	      "getters: total delta%d off%d", pw_get_calls - calls, pw_off_get_calls);
	CHECK(!pw_preoff_logs, "diagnostic log preceded power-off");
	CHECK(pw_a9->rail.pg_raw == 0 && pw_a9->rail.pg_at_us > 0,
	      "original PG sample/timestamp lost");
	CHECK(pw_a9->rail.pd_off_confirmed && pw_a9->rail.pd_valid &&
	      pw_a9->rail.pd_seq_before == 7 && pw_a9->rail.pd_seq == 8 &&
	      pw_a9->rail.pd_pg_raw[0] == 1 && pw_a9->rail.pd_pg_raw[1] == 1 &&
	      pw_a9->rail.pd_pg_raw[2] == 0, "new shutdown trace missing");
	CHECK(pw_a9->rail.session.id == 1 && pw_a9->rail.session_frames == 3 &&
	      pw_a9->rail.session.full_frames == 3 && pw_a9->rail.session.full_started == 2 &&
	      pw_a9->rail.session.full_done == 1, "session snapshot lost");
	CHECK(atomic_read(&a9x_rail_fault) && !fake_enable && pw_a9->force_clear,
	      "good post-off PG changed containment/history");
	CHECK(log_has("rail shutdown PG samples"), "no deferred PG timeline");
	teardown();
}

static void case_56(void)
{
	int calls;

	pw_setup("(56) [0039b] failed/unknown shutdown adds no diagnostic status lookup or timeline log");
	pw_waveform();
	pw_pg = 0;
	dpms_ret = -EBUSY;
	calls = pw_get_calls;
	CHECK(a9_power_on(pw_a9) == -EIO, "fault");
	CHECK(pw_get_calls == calls + 1 && !pw_off_get_calls, "new lookup despite unknown rails");
	CHECK(!pw_a9->rail.pd_off_confirmed && !pw_a9->rail.pd_valid &&
	      pw_a9->rail.pd_getter == -ENODATA, "unknown shutdown reported a trace");
	CHECK(!log_has("rail shutdown PG samples"), "timeline emitted before confirmed shutdown");
	CHECK(pw_force_disables == 1 && pw_a9->hv_fault && atomic_read(&a9x_rail_fault),
	      "force-disable/rail latch changed");
	teardown();
}

static void case_57(void)
{
	u64 id;

	pw_setup("(57) [0039b] session reset only after new PG-qualified startup; warm/rejected/failed startup keep counters");
	pw_waveform();
	pw_a9->session.waveforms = 5;
	pw_a9->session.full_frames = 3;
	id = pw_a9->session.id;
	CHECK(id == 1 && a9_session_frames(pw_a9) == 3, "first session");
	CHECK(!a9_power_on(pw_a9) && pw_a9->session.id == id &&
	      pw_a9->session.waveforms == 5 && a9_session_frames(pw_a9) == 3, "warm validation reset");
	CHECK(!a9x_power_off(T), "off");
	mock_sleep_us(250000);
	pw_pg = 0;
	CHECK(a9_power_on(pw_a9) == -EIO && pw_a9->session.id == id &&
	      pw_a9->session.waveforms == 5, "failed fresh PG reset counters");
	mock_sleep_us(250000);
	pw_pg = 1;
	CHECK(!a9_power_on(pw_a9) && pw_a9->session.id == id + 1 &&
	      !pw_a9->session.waveforms && !pw_a9->session.full_frames && !a9_session_frames(pw_a9),
	      "new qualified session did not reset");
	atomic_set(&a9x_rail_fault, 1);
	CHECK(a9_power_on(pw_a9) == -EIO && pw_a9->session.id == id + 1,
	      "latched admission reset counters");
	a9x_power_off(T);
	teardown();
}

static void case_58(void)
{
	pw_setup("(58) [0039b] unchanged normal-powerdown sequence reports absent, never reuses stale PG trace");
	pw_waveform();
	pw_pg = 0;
	pw_pd_new = false;
	CHECK(a9_power_on(pw_a9) == -EIO, "fault");
	CHECK(pw_a9->rail.pd_off_confirmed && !pw_a9->rail.pd_valid &&
	      pw_a9->rail.pd_seq_before == 7 && !pw_a9->rail.pd_seq &&
	      pw_a9->rail.pd_pg_raw[0] == -ENODATA, "old trace reused");
	CHECK(atomic_read(&a9x_rail_fault) && !fake_enable && pw_a9->force_clear, "latch/history changed");
	teardown();
}

static void case_59(void)
{
	struct sha256_ctx sc;
	bool shown = false;
	u64 frames;

	setup("(59) [0039b] actual ring: final full rect counts init/null slots, partial job excluded, failure prefix retained");
	job_setup();
	commit_us = 0;
	job_a9->session.id = 9;
	job_a9->session.frame0 = T->frames;
	job_prepare(5, &sc);
	job_a9->e.rect = a9e_rect_full();
	job_a9->e.clear = 1;
	job_a9->e.req.kind = 1;
	frames = T->frames;
	CHECK(!a9_run_ring(job_a9, true, job_bin, &sc, &shown) && shown, "full init job failed");
	CHECK(job_a9->session.waveforms == 1 && job_a9->session.waveforms_done == 1 &&
	      job_a9->session.full_started == 1 && job_a9->session.full_done == 1 &&
	      job_a9->session.full_frames == T->frames - frames && T->frames - frames == 6 &&
	      job_a9->session.clears == 1 && job_a9->session.clears_done == 1 &&
	      job_a9->session.transitions == 1 && job_a9->session.transitions_done == 1,
	      "full/init/null/clear/transition accounting");
	job_prepare(5, &sc);
	job_a9->e.rect = (struct a9e_rect){ 1, 2, 3, 4 };
	job_a9->e.clear = 0;
	job_a9->e.req.kind = 0;
	frames = job_a9->session.full_frames;
	CHECK(!a9_run_ring(job_a9, false, job_bin, &sc, &shown), "partial job");
	CHECK(job_a9->session.waveforms == 2 && job_a9->session.waveforms_done == 2 &&
	      job_a9->session.full_started == 1 && job_a9->session.full_frames == frames,
	      "partial job counted full");
	job_prepare(5, &sc);
	job_a9->e.rect = a9e_rect_full();
	job_nframes = 100;
	job_pause_after = atomic_load(&job_frames) + 12;
	abort_after_drive = 1;
	CHECK(a9_run_ring(job_a9, false, job_bin, &sc, &shown) == -ECANCELED, "failure prefix");
	abort_after_drive = 0;
	CHECK(job_a9->session.waveforms == 3 && job_a9->session.waveforms_done == 2 &&
	      job_a9->session.full_started == 2 && job_a9->session.full_done == 1 &&
	      job_a9->session.full_frames == frames + 1 && !T->on &&
	      job_poweroff_ns && job_poweroff_ns < job_done_ns,
	      "failed prefix count/off-before-join ordering");
	atomic_set(&a9x_rail_fault, 1);
	fake_enable = true;
	CHECK(a9_run_ring(job_a9, false, job_bin, &sc, &shown) == -EIO &&
	      job_a9->session.waveforms == 3, "rejected job counted");
	job_end();
	teardown();
}

static void case_60(void)
{
	struct fp9929a_status failed;
	int ret;

	pw_setup("(60) [0039b] failed original lookup has no trace baseline; recovered getter cannot validate old trace");
	pw_waveform();
	pw_get_ret = -EPROBE_DEFER;
	ret = a9_hv_sample(pw_a9, &failed);
	CHECK(ret == -EPROBE_DEFER, "original lookup");
	a9_rail_fault(pw_a9, ret, &failed);
	pw_get_ret = 0;
	pw_pd_new = false;
	a9_power_off(pw_a9);
	a9_rail_report(pw_a9);
	CHECK(pw_a9->rail.pd_off_confirmed && pw_a9->rail.pd_getter == 0 &&
	      !pw_a9->rail.pd_valid && pw_a9->rail.pg_raw == -ENODATA &&
	      pw_a9->rail.getter == -EPROBE_DEFER, "unknown baseline accepted stale trace");
	CHECK(atomic_read(&a9x_rail_fault) && !fake_enable && pw_a9->force_clear,
	      "lookup recovery changed latch/history");
	teardown();
}

/* ---- patch 0052 (rail-diag): log-only counters. Latches/shutdown are exercised above, unchanged. ---- */
static void rd_latched_nothing(const char *what)
{
	CHECK(!atomic_read(&a9x_rail_fault) && !pw_a9->force_clear && pw_a9->rail.count == 0 &&
	      !pw_force_disables && !atomic_load(&n_emerg) && T->on && !pw_a9->hv_fault,
	      "%s changed latch/containment state", what);
}

/* (61) the since-waveform-end histogram buckets every checked sample; semantics of the check unchanged */
static void case_61(void)
{
	struct a9_rail_diag *d;

	pw_setup("(61) [0052] rail sample histogram: buckets 0-3/4-15/16-63/64+/none by delay after wf_end; a good sample never latches");
	pw_waveform();
	d = &pw_a9->diag;
	CHECK(!a9_check_live_rails(pw_a9), "immediate sample");
	mock_sleep_us(8000);
	CHECK(!a9_check_live_rails(pw_a9), "8 ms sample");
	mock_sleep_us(30000);
	CHECK(!a9_check_live_rails(pw_a9), "~40 ms sample");
	mock_sleep_us(100000);
	CHECK(!a9_check_live_rails(pw_a9), "~140 ms sample");
	pw_a9->wf_end = 0;
	CHECK(!a9_check_live_rails(pw_a9), "no waveform yet");
	CHECK(d->hist_n[0] == 1 && d->hist_n[1] == 1 && d->hist_n[2] == 1 && d->hist_n[3] == 1 &&
	      d->hist_n[4] == 1, "buckets %llu %llu %llu %llu %llu", (unsigned long long)d->hist_n[0],
	      (unsigned long long)d->hist_n[1], (unsigned long long)d->hist_n[2],
	      (unsigned long long)d->hist_n[3], (unsigned long long)d->hist_n[4]);
	CHECK(!d->hist_low[0] && !d->hist_low[1] && !d->hist_low[2] && !d->hist_low[3] && !d->hist_low[4],
	      "a good sample counted low");
	rd_latched_nothing("good samples");
	/* pipeline off: not a checked sample, nothing counted */
	CHECK(!a9x_power_off(T), "transport off");
	CHECK(!a9_check_live_rails(pw_a9), "off pipeline");
	CHECK(d->hist_n[0] + d->hist_n[1] + d->hist_n[2] + d->hist_n[3] + d->hist_n[4] == 5,
	      "off-pipeline sample counted");
	teardown();
}

/* (62) a low sample is counted in its bucket AND still latches exactly as before (0039) */
static void case_62(void)
{
	pw_setup("(62) [0052] a low sample lands in its bucket and latches, no retry, as in case 49");
	pw_waveform();
	mock_sleep_us(6000);
	pw_pg = 0;
	CHECK(a9_check_live_rails(pw_a9) == -EIO, "not latched");
	CHECK(pw_a9->diag.hist_n[1] == 1 && pw_a9->diag.hist_low[1] == 1 && pw_a9->diag.hist_low[0] == 0,
	      "low bucket %llu/%llu", (unsigned long long)pw_a9->diag.hist_low[1],
	      (unsigned long long)pw_a9->diag.hist_n[1]);
	CHECK(atomic_read(&a9x_rail_fault) && !fake_enable && pw_a9->force_clear && pw_a9->rail.count == 1 &&
	      !T->on && pw_a9->rail.since_wf_ms >= 6, "latch changed");
	pw_pg = 1;
	CHECK(a9_power_on(pw_a9) == -EIO && !T->on, "re-powered after the fault");
	teardown();
}

/* (63) one PG read at waveform end: counted, logged, NEVER latches; the next update's sample still decides */
static void case_63(void)
{
	struct a9_rail_diag *d;
	int calls, drives, commits;

	pw_setup("(63) [0052] PG read at waveform end is recorded only; a low at wf end does not latch, the next sample still does");
	pw_waveform();
	d = &pw_a9->diag;
	calls = pw_get_calls;
	commits = ncommits;
	drives = atomic_load(&observed_drives);
	/* review 89d item 6: classified by THIS update (the engine's rect); the previous record must not count */
	pw_a9->e.rect = a9e_rect_full();
	pw_a9->last.rect = (struct a9e_rect){ 10, 20, 30, 40 };	/* the previous, partial update */
	a9_diag_wf_end(pw_a9);
	CHECK(pw_get_calls == calls + 1, "exactly one getter call (%d)", pw_get_calls - calls);
	CHECK(d->end_full_n == 1 && d->end_full_low == 0, "full-screen waveform not counted");
	CHECK(d->end_n == 1 && !d->end_low && !d->end_err && d->end_last_pg == 1 && d->end_first_low_ms == -1,
	      "good end read");
	rd_latched_nothing("good wf-end read");
	/* a low read at wf end */
	pw_pg = 0;
	pw_a9->e.rect = (struct a9e_rect){ 10, 20, 30, 40 };	/* partial */
	pw_a9->last.rect = a9e_rect_full();			/* the previous update was full: must not count */
	a9_diag_wf_end(pw_a9);
	CHECK(d->end_full_n == 1 && d->end_full_low == 0, "partial waveform counted as full");
	CHECK(d->end_n == 2 && d->end_low == 1 && d->end_last_pg == 0 && d->end_first_low_ms >= 0 &&
	      d->end_first_low_n == 2 && d->end_last_low_ms == d->end_first_low_ms, "low end read not recorded");
	CHECK(log_has("rail-diag: PG low at waveform end"), "no log of the low");
	rd_latched_nothing("LOW wf-end read");
	CHECK(ncommits == commits && atomic_load(&observed_drives) == drives, "diag drove or committed");
	/* PG recovers: the next update's own sample passes (the diag did not poison anything) */
	pw_pg = 1;
	CHECK(!a9_check_live_rails(pw_a9), "next sample after a recovered low");
	/* PG still low at the next update's sample: the existing latch fires, once */
	pw_pg = 0;
	CHECK(a9_check_live_rails(pw_a9) == -EIO && atomic_read(&a9x_rail_fault) && pw_a9->rail.count == 1,
	      "next sample did not latch");
	CHECK(d->end_n == 2, "latch path read the wf-end counter");
	teardown();
}

/* (64) wf-end read edge cases: PG read error, lookup error, pipeline already off */
static void case_64(void)
{
	struct a9_rail_diag *d;
	int calls;

	pw_setup("(64) [0052] wf-end read: -EIO and lookup errors are counted as errors (not low), no read when the pipeline is off");
	pw_waveform();
	d = &pw_a9->diag;
	pw_pg = -EIO;
	a9_diag_wf_end(pw_a9);
	pw_pg = 1;
	pw_get_ret = -EPROBE_DEFER;
	a9_diag_wf_end(pw_a9);
	pw_get_ret = 0;
	CHECK(d->end_err == 2 && d->end_n == 0 && d->end_low == 0 && d->end_first_low_ms == -1, "errors miscounted");
	rd_latched_nothing("error wf-end reads");
	CHECK(!a9x_power_off(T), "transport off");
	calls = pw_get_calls;
	a9_diag_wf_end(pw_a9);
	CHECK(pw_get_calls == calls && d->end_skipped == 1, "read with the pipeline off");
	teardown();
}

static void rd_trace(struct fp9929a_pd_trace *tr, u64 seq, int pg2)
{
	tr->seq = seq;
	tr->entry = (struct fp9929a_pg_point){ 1000 * (s64)seq, 1 };
	tr->vcom_low = (struct fp9929a_pg_point){ 1000 * (s64)seq + 1100, 1 };
	tr->pwrup_low = (struct fp9929a_pg_point){ 1000 * (s64)seq + 51100, pg2 };
}

/* (65) the shutdown-trace ring: last 16, in order, no duplicates, gaps counted, a fault's own trace skipped */
static void case_65(void)
{
	struct a9_rail_diag *d;
	struct fp9929a_pd_trace tr;

	pw_setup("(65) [0052] shutdown PG trace ring keeps the last A9_DIAG_PD (12) in order; duplicates ignored; gaps counted; fault trace not mixed in");
	d = &pw_a9->diag;
	for (u64 s = 1; s <= 20; s++) {
		rd_trace(&tr, s, 1);
		pw_a9->session.id = 100 + s;
		a9_diag_pd_push(pw_a9, &tr);
		a9_diag_pd_push(pw_a9, &tr);	/* the same trace seen at the next sample too */
	}
	CHECK(d->pd_total == 20 && d->pd_missed == 0 && d->pd_head == (20 % A9_DIAG_PD) && d->pd_seen_seq == 20,
	      "total %llu missed %llu head %u", (unsigned long long)d->pd_total,
	      (unsigned long long)d->pd_missed, d->pd_head);
	for (unsigned int k = 0; k < A9_DIAG_PD; k++) {
		unsigned int i = (d->pd_head + A9_DIAG_PD - 1 - k) % A9_DIAG_PD;

		CHECK(d->pd[i].seq == 20 - k && d->pd[i].session == 100 + 20 - k &&
		      d->pd[i].pg[0] == 1 && d->pd[i].pg[2] == 1 &&
		      d->pd[i].at_us[1] - d->pd[i].at_us[0] == 1100 &&
		      d->pd[i].at_us[2] - d->pd[i].at_us[0] == 51100, "ring slot %u seq %llu", k,
		      (unsigned long long)d->pd[i].seq);
	}
	rd_trace(&tr, 23, 1);		/* 21 and 22 were never sampled */
	a9_diag_pd_push(pw_a9, &tr);
	CHECK(d->pd_missed == 2 && d->pd_total == 21, "gap: missed %llu total %llu",
	      (unsigned long long)d->pd_missed, (unsigned long long)d->pd_total);
	/* a fault's own shutdown trace is in rail.pd_*, not in the healthy ring */
	pw_a9->rail.count = 1;
	pw_a9->rail.pd_seq = 24;
	rd_trace(&tr, 24, 0);
	a9_diag_pd_push(pw_a9, &tr);
	CHECK(d->pd_total == 21 && d->pd_skipped_fault == 1 && d->pd_seen_seq == 24, "fault trace mixed in");
	/* a stale (older) trace is never pushed */
	rd_trace(&tr, 5, 1);
	a9_diag_pd_push(pw_a9, &tr);
	CHECK(d->pd_total == 21, "older trace pushed");
	teardown();
}

/* (66) the ordered shutdown is untouched: no extra getter call, no diag lock use, then the next session's wf end harvests its trace */
static void case_66(void)
{
	struct a9_rail_diag *d;
	int calls;

	pw_setup("(66) [0052] healthy ordered shutdown: only its pre-existing getter call, no diag access; the next wf-end read harvests its trace");
	pw_waveform();
	d = &pw_a9->diag;
	calls = pw_get_calls;
	CHECK(pthread_mutex_trylock(&d->lock.m) == 0, "diag lock busy before shutdown");
	/* hold the diag lock: if the shutdown touched it, it would deadlock the test */
	a9_power_off(pw_a9);
	pthread_mutex_unlock(&d->lock.m);
	/* the one pre-existing a9_hv_maybe_on() lookup of a9_power_off, and nothing added by 0052 */
	CHECK(!T->on && pw_get_calls == calls + 1, "shutdown getter calls %d", pw_get_calls - calls);
	CHECK(d->pd_total == 0 && d->end_n == 0, "shutdown wrote diag state");
	pw_pd_seq = 8;			/* the FP driver recorded the shutdown trace */
	pw_waveform();
	a9_diag_wf_end(pw_a9);
	CHECK(d->pd_total == 1 && d->pd_missed == 7 && d->pd[0].seq == 8 && d->pd[0].pg[0] == 1 &&
	      d->pd[0].pg[1] == 1 && d->pd[0].pg[2] == 0 && d->pd[0].session == pw_a9->session.id,
	      "harvest total %llu seq %llu", (unsigned long long)d->pd_total, (unsigned long long)d->pd[0].seq);
	rd_latched_nothing("harvest");
	teardown();
}

/* ---- patch 0053 (pg-settle): bounded PG settle before the latch. 0 = off = the 0052 behaviour. ---- */
static void st_dip(unsigned int recover_us)
{
	pw_pg = 0;
	pw_pg_good_from_ns = mock_now_ns() + (u64)recover_us * 1000;
}

static s64 st_elapsed_ms(u64 t0) { return (s64)((mock_now_ns() - t0) / 1000000); }
/* a latch alone costs the ordered shutdown (about 52 ms of mock time); a settle wait would add >= the window */
#define ST_IMMEDIATE(t0) (st_elapsed_ms(t0) < 70)

/* (67) off by default: a low latches at once, no wait, no counters; the wf-end hook does nothing */
static void case_67(void)
{
	u64 t0;
	int calls;

	pw_setup("(67) [0053] pg_settle_ms=0: a low latches at once (no wait, no read), the wf-end hook is inert");
	pw_waveform();
	pw_pg = 0;
	calls = pw_get_calls;
	{
		struct fp9929a_status st;

		CHECK(!a9_pg_wf_end_low(pw_a9, 0, &st) && pw_get_calls == calls, "wf-end hook acted while off");
	}
	st_dip(3000);
	t0 = mock_now_ns();
	CHECK(a9_check_live_rails(pw_a9) == -EIO, "not latched");
	CHECK(ST_IMMEDIATE(t0) && pw_a9->diag.settle_n == 0 && pw_a9->diag.settle_skip == 0, "waited/counted while off");
	CHECK(atomic_read(&a9x_rail_fault) && !fake_enable && pw_a9->force_clear && pw_a9->rail.count == 1 && !T->on,
	      "latch changed");
	teardown();
}

/* (68) a dip that comes back within the window: no fault, bounded wait, counted, good PG needed twice */
static void case_68(void)
{
	u64 t0;

	pw_setup("(68) [0053] low at a sample, back within the window (two good reads): no latch, pipeline stays on, counted");
	pg_settle_ms = 100;
	pw_waveform();
	mock_sleep_us(2000);
	st_dip(6000);
	t0 = mock_now_ns();
	CHECK(!a9_check_live_rails(pw_a9), "recovered dip latched");
	CHECK(st_elapsed_ms(t0) >= 6 && st_elapsed_ms(t0) <= 12, "wait %lld ms", (long long)st_elapsed_ms(t0));
	CHECK(!atomic_read(&a9x_rail_fault) && fake_enable && !pw_a9->force_clear && pw_a9->rail.count == 0 &&
	      T->on && !pw_force_disables && !pw_a9->hv_fault, "recovered dip changed containment");
	CHECK(pw_a9->diag.settle_n == 1 && pw_a9->diag.settle_ok == 1 && pw_a9->diag.settle_timeout == 0 &&
	      pw_a9->diag.settle_hist[2] == 1 && pw_a9->diag.settle_max_us >= 5000 && pw_a9->settle_used == 1,
	      "settle counters ok %llu used %u", (unsigned long long)pw_a9->diag.settle_ok, pw_a9->settle_used);
	CHECK(log_has("pg-settle: PG low after a waveform came back"), "no log of the recovered dip");
	/* the session continues */
	CHECK(!a9_check_live_rails(pw_a9) && T->on, "next sample");
	teardown();
}

/* (69) a low that stays low: the window ends, the SAME latch as before, from the FIRST low sample, then the usual shutdown */
static void case_69(void)
{
	u64 t0;
	s64 first_us;

	pw_setup("(69) [0053] low that never returns: waits the window, then latches exactly as before from the first sample");
	pg_settle_ms = 100;
	pw_waveform();
	mock_sleep_us(3000);
	pw_pg = 0;
	first_us = (s64)(mock_now_ns() / 1000);
	t0 = mock_now_ns();
	CHECK(a9_check_live_rails(pw_a9) == -EIO, "not latched");
	CHECK(st_elapsed_ms(t0) >= 100 && st_elapsed_ms(t0) <= 104, "wait %lld ms", (long long)st_elapsed_ms(t0));
	CHECK(atomic_read(&a9x_rail_fault) && !fake_enable && pw_a9->force_clear && pw_a9->rail.count == 1 && !T->on,
	      "latch/shutdown changed");
	CHECK(pw_a9->rail.pg_raw == 0 && pw_a9->rail.pg_at_us >= first_us && pw_a9->rail.pg_at_us <= first_us + 1000,
	      "original sample not frozen: pg_at %lld first %lld", (long long)pw_a9->rail.pg_at_us, (long long)first_us);
	CHECK(pw_a9->diag.settle_n == 1 && pw_a9->diag.settle_timeout == 1 && pw_a9->diag.settle_ok == 0,
	      "timeout counters");
	CHECK(log_has("pg-settle: PG still low"), "no timeout log");
	/* a later good PG never un-latches */
	pw_pg = 1;
	pw_pg_good_from_ns = 0;
	CHECK(a9_power_on(pw_a9) == -EIO && !T->on, "re-powered after the fault");
	teardown();
}

/* (70) a low outside the window (idle dropout, long after the waveform) latches at once */
static void case_70(void)
{
	u64 t0;

	pw_setup("(70) [0053] a low later than the window after the waveform latches at once, no wait");
	pg_settle_ms = 100;
	pw_waveform();
	mock_sleep_us(150000);
	st_dip(3000);
	t0 = mock_now_ns();
	CHECK(a9_check_live_rails(pw_a9) == -EIO, "not latched");
	CHECK(ST_IMMEDIATE(t0) && pw_a9->diag.settle_n == 0, "waited outside the window");
	CHECK(atomic_read(&a9x_rail_fault) && !T->on && pw_a9->rail.count == 1, "latch changed");
	pw_a9->wf_end = 0;	/* no waveform yet: also immediate */
	teardown();
	pw_setup("(70b) [0053] no waveform record: a low latches at once");
	pg_settle_ms = 100;
	pw_waveform();
	pw_a9->wf_end = 0;
	pw_pg = 0;
	t0 = mock_now_ns();
	CHECK(a9_check_live_rails(pw_a9) == -EIO && ST_IMMEDIATE(t0), "no-waveform low settled");
	teardown();
}

/* (71) flapping PG (never two consecutive good reads) is not a recovery */
static void case_71(void)
{
	pw_setup("(71) [0053] PG that flaps 0/1 never counts as recovered: times out and latches");
	pg_settle_ms = 60;
	pw_waveform();
	pw_pg = 0;
	pw_pg_flap = true;
	pw_get_calls = 1;	/* the first read is call 2 -> raw 0, then 1, 0, 1 ... */
	CHECK(a9_check_live_rails(pw_a9) == -EIO, "flapping PG accepted");
	CHECK(atomic_read(&a9x_rail_fault) && pw_a9->diag.settle_timeout == 1 && pw_a9->diag.settle_ok == 0 && !T->on,
	      "flap not latched");
	teardown();
}

/* (72) budget per HV session: the 4th recovered dip latches at once; a new HV session starts again */
static void case_72(void)
{
	u64 t0;

	pw_setup("(72) [0053] budget: 3 recovered dips per HV session, the 4th latches at once; the next session has a fresh budget");
	pg_settle_ms = 100;
	pw_waveform();
	for (int i = 0; i < 3; i++) {
		mock_sleep_us(2000);
		pw_a9->wf_end = ktime_get();
		st_dip(4000);
		CHECK(!a9_check_live_rails(pw_a9), "dip %d latched", i);
	}
	CHECK(pw_a9->settle_used == 3 && pw_a9->diag.settle_ok == 3 && !atomic_read(&a9x_rail_fault), "3 recovered");
	pw_a9->wf_end = ktime_get();
	st_dip(4000);
	t0 = mock_now_ns();
	CHECK(a9_check_live_rails(pw_a9) == -EIO && ST_IMMEDIATE(t0), "4th dip settled");
	CHECK(pw_a9->diag.settle_skip == 1 && atomic_read(&a9x_rail_fault) && !T->on, "budget skip/latch");
	teardown();
	pw_setup("(72b) [0053] budget resets with a new HV session (on_since changes)");
	pg_settle_ms = 100;
	pw_waveform();
	for (int i = 0; i < 3; i++) {
		mock_sleep_us(2000);
		pw_a9->wf_end = ktime_get();
		st_dip(4000);
		CHECK(!a9_check_live_rails(pw_a9), "dip %d latched", i);
	}
	a9_power_off(pw_a9);
	mock_sleep_us(5000);
	pw_pg = 1;
	pw_pg_good_from_ns = 0;
	pw_waveform();
	mock_sleep_us(2000);
	pw_a9->wf_end = ktime_get();
	st_dip(4000);
	CHECK(!a9_check_live_rails(pw_a9) && pw_a9->settle_used == 1, "fresh session budget %u", pw_a9->settle_used);
	teardown();
	pw_setup("(72c) [0053] budget 0: never settles");
	pg_settle_ms = 100;
	pg_settle_budget = 0;
	pw_waveform();
	st_dip(4000);
	CHECK(a9_check_live_rails(pw_a9) == -EIO && pw_a9->diag.settle_skip == 1, "budget 0 settled");
	teardown();
}

/* (73) ineligible failures latch at once: getter error, FP forced/emergency; and the wait ends on gate close / pipeline off */
static void case_73(void)
{
	u64 t0;

	pw_setup("(73) [0053] a getter error is not settled: latches at once");
	pg_settle_ms = 100;
	pw_waveform();
	pw_get_ret = -EIO;
	t0 = mock_now_ns();
	CHECK(a9_check_live_rails(pw_a9) == -EIO && ST_IMMEDIATE(t0) && pw_a9->diag.settle_n == 0,
	      "getter error settled");
	teardown();

	pw_setup("(73b) [0053] emergency-off flagged by the FP driver is not settled");
	pg_settle_ms = 100;
	pw_waveform();
	pw_pg = 0;
	atomic_store(&n_emerg, 1);
	t0 = mock_now_ns();
	CHECK(a9_check_live_rails(pw_a9) == -EIO && ST_IMMEDIATE(t0) && pw_a9->diag.settle_n == 0,
	      "emergency settled");
	atomic_store(&n_emerg, 0);
	teardown();

	pw_setup("(73c) [0053] the gate closing during the wait ends it at once and latches");
	pg_settle_ms = 100;
	pw_waveform();
	pw_pg = 0;
	fake_enable = false;
	t0 = mock_now_ns();
	CHECK(a9_check_live_rails(pw_a9) == -EIO && ST_IMMEDIATE(t0), "waited with the gate closed");
	CHECK(atomic_read(&a9x_rail_fault) && pw_a9->diag.settle_timeout == 1, "not latched");
	teardown();

	pw_setup("(73d) [0053] a window larger than the ceiling is clamped to 200 ms");
	pg_settle_ms = 5000;
	pw_waveform();
	pw_pg = 0;
	t0 = mock_now_ns();
	CHECK(a9_check_live_rails(pw_a9) == -EIO, "not latched");
	CHECK(st_elapsed_ms(t0) >= 200 && st_elapsed_ms(t0) <= 204, "clamped wait %lld ms", (long long)st_elapsed_ms(t0));
	teardown();
}

/* (74) the waveform-end hook (M4 split): a9_pg_wf_end_low() only samples (it runs under eng_lock in
 * __a9_update); a9_pg_wf_end() settles or latches (after the unlock) through a9_check_rail_sample */
static bool st_wf_end(s64 first_us)
{
	struct fp9929a_status st;

	if (!a9_pg_wf_end_low(pw_a9, first_us, &st))
		return false;
	return a9_pg_wf_end(pw_a9, &st);
}

static void case_74(void)
{
	struct fp9929a_status st;
	int calls;
	u64 t0;

	pw_setup("(74) [0053/M4] wf-end sample: good PG costs exactly one read, no wait, no decision");
	pg_settle_ms = 100;
	pw_waveform();
	calls = pw_get_calls;
	t0 = mock_now_ns();
	CHECK(!a9_pg_wf_end_low(pw_a9, 0, &st) && pw_get_calls == calls + 1 && ST_IMMEDIATE(t0), "good PG cost");
	teardown();

	pw_setup("(74a) [M4] wf-end sample of a stuck low: one read, no wait, no latch, no power-off (decision deferred)");
	pg_settle_ms = 100;
	pw_waveform();
	pw_pg = 0;
	calls = pw_get_calls;
	t0 = mock_now_ns();
	CHECK(a9_pg_wf_end_low(pw_a9, 0, &st) && pw_get_calls == calls + 1 && ST_IMMEDIATE(t0), "sample cost");
	CHECK(!atomic_read(&a9x_rail_fault) && T->on && fake_enable && !pw_a9->force_clear &&
	      pw_a9->diag.settle_n == 0, "the sample alone acted");
	teardown();

	pw_setup("(74b) [0053] wf-end: recovering dip is absorbed, nothing latched");
	pg_settle_ms = 100;
	pw_waveform();
	st_dip(7000);
	CHECK(!st_wf_end(0), "recovering dip latched");
	CHECK(!atomic_read(&a9x_rail_fault) && T->on && pw_a9->diag.settle_ok == 1, "state after absorbed dip");
	teardown();

	pw_setup("(74c) [0053] wf-end: stuck low latches through a9_check_rail_sample and powers down");
	pg_settle_ms = 100;
	pw_waveform();
	pw_pg = 0;
	CHECK(st_wf_end(0), "stuck low not latched");
	CHECK(atomic_read(&a9x_rail_fault) && !fake_enable && pw_a9->force_clear && pw_a9->rail.count == 1 && !T->on,
	      "latch/shutdown changed");
	CHECK(pw_a9->rail.since_wf_ms >= 100, "since_wf %lld", (long long)pw_a9->rail.since_wf_ms);
	teardown();

	pw_setup("(74d) [0053] wf-end: a read error is left to the 0052 path (no wait, no latch)");
	pg_settle_ms = 100;
	pw_waveform();
	pw_get_ret = -EIO;
	CHECK(!st_wf_end(0) && !atomic_read(&a9x_rail_fault) && pw_a9->diag.settle_n == 0, "error acted on");
	teardown();

	pw_setup("(74e) [M4] pg_settle_ms 0: the wf-end sample makes no read at all");
	pw_waveform();
	pw_pg = 0;
	calls = pw_get_calls;
	CHECK(!a9_pg_wf_end_low(pw_a9, 12345, &st) && pw_get_calls == calls && pw_a9->diag.settle_reread_high == 0,
	      "read while off");
	teardown();
}

/* (79) M4 F2: the settle clock starts at the FIRST low reading (the 0052 read), not at the settle entry */
static void case_79(void)
{
	s64 first_us;

	pw_setup("(79) [M4] dip duration and window count from the 0052 read's low, not from the re-read");
	pg_settle_ms = 100;
	pw_waveform();
	pw_pg = 0;
	first_us = (s64)(mock_now_ns() / 1000);
	pw_pg_good_from_ns = mock_now_ns() + 30000ull * 1000;	/* back 30 ms after the first low reading */
	mock_sleep_us(16000);				/* the ~16 ms between the 0052 read and the settle (F2) */
	CHECK(!st_wf_end(first_us), "dip latched");
	CHECK(pw_a9->diag.settle_ok == 1 && pw_a9->diag.settle_hist[4] == 1 && pw_a9->diag.settle_max_us >= 30000 &&
	      pw_a9->diag.settle_max_us < 33000, "duration not from the first reading: max_us %u hist4 %llu",
	      pw_a9->diag.settle_max_us, (unsigned long long)pw_a9->diag.settle_hist[4]);
	CHECK(pw_a9->settle_first_us == 0, "clock origin not consumed");
	/* F6b: the settle reads saw a low run (to 30 ms after the first reading), then exactly two goods */
	{
		unsigned n = pw_a9->settle_trace_n;
		u64 bits = pw_a9->settle_trace;

		CHECK(n >= 8 && ((bits >> (n - 2)) & 3) == 3 && !(bits & ((1ull << (n - 2)) - 1)),
		      "trace bits %016llx n %u", (unsigned long long)bits, n);
	}
	CHECK(log_has("pg-settle trace: reads ") && log_has(" bits 0") && log_has("vblank first ") &&
	      log_has(" from the first low reading (recovered dip 1 of 3 this session"), "trace/dip line format");
	teardown();

	pw_setup("(79b) [M4] a stuck low times out 100 ms after the FIRST low reading (not 100 ms after the re-read)");
	pg_settle_ms = 100;
	pw_waveform();
	pw_pg = 0;
	first_us = (s64)(mock_now_ns() / 1000);
	mock_sleep_us(16000);
	{
		u64 t0 = mock_now_ns();

		CHECK(st_wf_end(first_us), "not latched");
		CHECK(st_elapsed_ms(t0) >= 82 && st_elapsed_ms(t0) <= 90, "window not from the first reading: %lld ms",
		      (long long)st_elapsed_ms(t0));
	}
	CHECK(atomic_read(&a9x_rail_fault) && !T->on && pw_a9->diag.settle_timeout == 1, "latch changed");
	teardown();

	pw_setup("(79c) [M4/F1] 0052 read low, 0053 re-read high: counted as reread_high, no settle, no budget");
	pg_settle_ms = 100;
	pw_waveform();
	{
		struct fp9929a_status st;

		CHECK(!a9_pg_wf_end_low(pw_a9, (s64)(mock_now_ns() / 1000), &st), "high re-read treated as low");
	}
	CHECK(pw_a9->diag.settle_reread_high == 1 && pw_a9->diag.settle_n == 0 && pw_a9->settle_used == 0 &&
	      pw_a9->settle_total == 0 && pw_a9->settle_first_us == 0, "reread_high %llu n %llu",
	      (unsigned long long)pw_a9->diag.settle_reread_high, (unsigned long long)pw_a9->diag.settle_n);
	/* a high re-read without a 0052 low is not counted */
	{
		struct fp9929a_status st;

		CHECK(!a9_pg_wf_end_low(pw_a9, 0, &st) && pw_a9->diag.settle_reread_high == 1, "counted without a low");
	}
	teardown();
}

/* (81) F6 (review 89d): VCOM readback after a recovered wf-end dip, post-unlock, log only, at most 64 per boot */
static void f6_snapshot(int *en, int *rf, int *fc, int *on, unsigned *ab)
{
	*en = fake_enable;
	*rf = atomic_read(&a9x_rail_fault);
	*fc = pw_a9->force_clear;
	*on = T->on;
	*ab = pw_a9->aborts;
}

static void case_81(void)
{
	int en, rf, fc, on, en2, rf2, fc2, on2;
	unsigned ab, ab2;

	pw_setup("(81) [F6] recovered wf-end dip: one VCOM read with eng_lock free, counters, trace line with THIS update");
	pg_settle_ms = 100;
	pw_waveform();
	pw_a9->last.mode = 0x206;
	pw_a9->last.rect = (struct a9e_rect){ 0, 0, 824, 1648 };
	pw_a9->last.clear = 0;
	st_dip(7000);
	CHECK(!st_wf_end(0), "recovered dip latched");
	CHECK(pw_vcom_calls == 1 && pw_vcom_lock_depth == 0, "vcom reads %d, eng_lock held %d", pw_vcom_calls, pw_vcom_lock_depth);
	CHECK(pw_a9->diag.vcom_reads == 1 && pw_a9->diag.vcom_ok == 1 && !pw_a9->diag.vcom_mismatch &&
	      !pw_a9->diag.vcom_err && pw_a9->diag.vcom_last == 0x7e && pw_a9->diag.vcom_last_ms >= 0, "vcom counters");
	CHECK(log_has("pg-settle trace: reads ") && log_has("update mode 0x206 rect 0,0-824,1648 clear 0, VCOM reg 0x7e (programmed 0x7e, r 0)"),
	      "trace line");
	CHECK(!log_has("!= programmed"), "mismatch warning on a match");
	teardown();

	pw_setup("(81b) [F6] a VCOM mismatch and a read error change counters and logs only");
	pg_settle_ms = 100;
	pw_waveform();
	pw_vcom_now = 0x74;
	st_dip(7000);
	f6_snapshot(&en, &rf, &fc, &on, &ab);
	CHECK(!st_wf_end(0), "recovered dip latched");
	f6_snapshot(&en2, &rf2, &fc2, &on2, &ab2);
	CHECK(en == en2 && rf == rf2 && fc == fc2 && on == on2 && ab == ab2 && T->on,
	      "mismatch changed state: enable %d->%d rail %d->%d clear %d->%d on %d->%d aborts %u->%u", en, en2, rf, rf2, fc, fc2, on, on2, ab, ab2);
	CHECK(pw_a9->diag.vcom_mismatch == 1 && pw_a9->diag.vcom_last == 0x74, "mismatch not counted");
	CHECK(log_has("pg-settle: VCOM register 0x74 != programmed 0x7e after a recovered dip"), "no mismatch warning");
	pw_vcom_ret = -EIO;
	mock_sleep_us(5000);
	pw_a9->wf_end = ktime_get();
	st_dip(7000);
	CHECK(!st_wf_end(0) && pw_a9->diag.vcom_err == 1 && pw_a9->diag.vcom_reads == 2 && T->on, "error not counted / acted on");
	teardown();

	pw_setup("(81c) [F6] no VCOM read for a timeout, a good PG, pg_settle_ms 0 or a plain sample (only the wf-end decision reads)");
	pg_settle_ms = 100;
	pw_waveform();
	CHECK(!st_wf_end(0) && pw_vcom_calls == 0, "good PG read VCOM");
	mock_sleep_us(2000);
	pw_a9->wf_end = ktime_get();
	st_dip(4000);
	CHECK(!a9_check_live_rails(pw_a9) && pw_a9->diag.settle_ok == 1 && pw_vcom_calls == 0, "a non-wf-end settle read VCOM");
	pw_pg = 0;
	pw_pg_good_from_ns = 0;
	CHECK(st_wf_end(0) && pw_vcom_calls == 0, "timeout read VCOM");
	teardown();
	pw_setup("(81d) [F6] pg_settle_ms 0: no read");
	pw_waveform();
	st_dip(4000);
	CHECK(!st_wf_end(0) && pw_vcom_calls == 0, "read with the settle off");
	teardown();

	pw_setup("(81e) [F6] after 64 reads in a boot, no further read (the dip itself still settles)");
	pg_settle_ms = 100;
	pg_settle_budget = 1000;
	pg_settle_rate_burst = 1000;
	pw_waveform();
	pw_a9->vcom_reads = A9_VCOM_READS_MAX - 1;
	st_dip(4000);
	CHECK(!st_wf_end(0) && pw_vcom_calls == 1 && pw_a9->vcom_reads == A9_VCOM_READS_MAX, "the 64th read");
	mock_sleep_us(5000);
	pw_a9->wf_end = ktime_get();
	st_dip(4000);
	CHECK(!st_wf_end(0) && pw_vcom_calls == 1 && pw_a9->diag.settle_ok == 2, "a 65th read, or the dip did not settle");
	CHECK(log_has("r -61"), "the trace line does not say there was no read (-ENODATA)");
	teardown();
}

/* (82) F6b: PG follows the 85 Hz scan after the dip; the recovery lands on a frame phase, the vblank pair says which frame */
static void case_82(void)
{
	u64 f0, bits;
	unsigned n;

	pw_setup("(82) [F6b] PG good only in the first 4 ms of each frame from frame +3: trace = low run, two goods; vblank pair 3 frames apart");
	pg_settle_ms = 100;
	pw_waveform();
	/* start just after a frame boundary */
	mock_sleep_us((11764 - (mock_now_ns() / 1000) % 11764) + 500);
	f0 = mock_now_ns() / 11764000ull;
	pw_pg = 0;
	pw_pg_frame_from = f0 + 3;
	CHECK(!st_wf_end((s64)(mock_now_ns() / 1000)), "frame-locked recovery latched");
	n = pw_a9->settle_trace_n;
	bits = pw_a9->settle_trace;
	CHECK(pw_a9->diag.settle_ok == 1 && n >= 4 && n <= 64, "reads %u", n);
	CHECK(((bits >> (n - 2)) & 3) == 3 && !(bits & ((1ull << (n - 2)) - 1)), "trace bits %016llx n %u: not a low run then two goods",
	      (unsigned long long)bits, n);
	CHECK(pw_a9->vbl_rec - pw_a9->vbl_first == 3, "vblank first %llu rec %llu", (unsigned long long)pw_a9->vbl_first,
	      (unsigned long long)pw_a9->vbl_rec);
	CHECK(pw_a9->vbl_rec_us > pw_a9->vbl_first_us && (pw_a9->vbl_rec_us - pw_a9->vbl_first_us) % 11764 == 0,
	      "vblank times %lld %lld", (long long)pw_a9->vbl_first_us, (long long)pw_a9->vbl_rec_us);
	CHECK(pw_a9->diag.settle_max_us >= 3 * 11764 - 1000 && pw_a9->diag.settle_max_us < 4 * 11764,
	      "frame-quantized duration %u us", pw_a9->diag.settle_max_us);
	teardown();
}

/* (83) F9: token bucket over HV-on time */
static void case_83(void)
{
	u64 t0;

	pw_setup("(83) [F9] burst 2: the third dip in a short HV-on span latches (rate skip); HV-on time refills");
	pg_settle_ms = 100;
	pg_settle_budget = 100;
	pg_settle_rate_burst = 2;
	pg_settle_rate_n = 6;
	pg_settle_rate_window_s = 600;		/* one token per 100 s of HV-on time */
	pw_waveform();
	for (int i = 0; i < 2; i++) {
		mock_sleep_us(2000);
		pw_a9->wf_end = ktime_get();
		st_dip(4000);
		CHECK(!a9_check_live_rails(pw_a9), "dip %d latched", i);
	}
	mock_sleep_us(2000);
	pw_a9->wf_end = ktime_get();
	st_dip(4000);
	t0 = mock_now_ns();
	CHECK(a9_check_live_rails(pw_a9) == -EIO && ST_IMMEDIATE(t0), "third dip settled with an empty bucket");
	CHECK(pw_a9->diag.settle_rate_skip == 1 && pw_a9->diag.settle_skip == 0 && pw_a9->diag.settle_cap_skip == 0 &&
	      atomic_read(&a9x_rail_fault) && !T->on, "rate skip not counted or not latched");
	teardown();

	pw_setup("(83b) [F9] 100 s of HV-on time refill one token; HV-off time does not");
	pg_settle_ms = 100;
	pg_settle_budget = 100;
	pg_settle_rate_burst = 1;
	pg_settle_rate_n = 6;
	pg_settle_rate_window_s = 600;
	pw_waveform();
	mock_sleep_us(2000);
	pw_a9->wf_end = ktime_get();
	st_dip(4000);
	CHECK(!a9_check_live_rails(pw_a9), "first dip");
	/* HV off, then on again: the off time is not HV-on time (the mock clock is real time, so the off
	 * span is short; what matters is that no on-time beyond a few ms was credited) */
	a9_power_off(pw_a9);
	mock_sleep_us(20000);
	pw_pg = 1;
	pw_pg_good_from_ns = 0;
	pw_waveform();
	mock_sleep_us(2000);
	pw_a9->wf_end = ktime_get();
	st_dip(4000);
	CHECK(a9_check_live_rails(pw_a9) == -EIO && pw_a9->diag.settle_rate_skip == 1, "HV-off time refilled the bucket");
	teardown();
	pw_setup("(83c) [F9] the same, with 100 s of HV-on time in between: refilled");
	pg_settle_ms = 100;
	pg_settle_budget = 100;
	pg_settle_rate_burst = 1;
	pg_settle_rate_n = 6;
	pg_settle_rate_window_s = 600;
	pw_waveform();
	mock_sleep_us(2000);
	pw_a9->wf_end = ktime_get();
	st_dip(4000);
	CHECK(!a9_check_live_rails(pw_a9), "first dip");
	/* still on: move this HV session's start and the accounting mark 101 s into the past (the mock
	 * clock is real time), as if 101 s of HV-on time had passed */
	T->on_since -= 101000000000ll;
	pw_a9->hvon_since = T->on_since;
	pw_a9->hvon_mark -= 101000000000ll;
	pw_a9->settle_on_since = T->on_since;
	pw_a9->wf_end = ktime_get();
	st_dip(4000);
	CHECK(!a9_check_live_rails(pw_a9) && pw_a9->diag.settle_rate_skip == 0, "HV-on time did not refill (tokens %llu)",
	      (unsigned long long)pw_a9->rate_tokens);
	CHECK(pw_a9->hvon_ms >= 101000, "hv-on ms %llu", (unsigned long long)pw_a9->hvon_ms);
	teardown();

	pw_setup("(83d) [F9] burst 0: never settles");
	pg_settle_ms = 100;
	pg_settle_rate_burst = 0;
	pw_waveform();
	st_dip(4000);
	CHECK(a9_check_live_rails(pw_a9) == -EIO && pw_a9->diag.settle_rate_skip == 1, "burst 0 settled");
	teardown();
}

/* (84) review 89e C2: a9x_vblank vs an msm unbind (a9x_client_unregister frees the modeset under t->lock) */
static void *c2_hold_lock(void *a)
{
	(void)a;
	mutex_lock(&T->lock);
	mock_sleep_us(30000);
	mutex_unlock(&T->lock);
	return NULL;
}

static void case_84(void)
{
	s64 us = -1;
	u64 n;

	pw_setup("(84) [C2] a9x_vblank takes t->lock; after an msm unbind (t->ms NULL, no vblank ref) it returns 0, no oops");
	pw_waveform();
	n = a9x_vblank(T, &us);
	CHECK(n > 0 && us > 0, "live: count %llu at %lld", (unsigned long long)n, (long long)us);
	/* the lock is taken: another thread holds t->lock for 30 ms, a9x_vblank must wait for it */
	{
		pthread_t th;
		struct timespec ts0, ts1;
		pthread_create(&th, NULL, c2_hold_lock, NULL);
		mock_sleep_us(5000);
		clock_gettime(CLOCK_MONOTONIC, &ts0);
		n = a9x_vblank(T, &us);
		clock_gettime(CLOCK_MONOTONIC, &ts1);
		pthread_join(th, NULL);
		CHECK((ts1.tv_sec - ts0.tv_sec) * 1000 + (ts1.tv_nsec - ts0.tv_nsec) / 1000000 >= 15,
		      "a9x_vblank did not wait for t->lock");
	}
	/* msm unbind: the client's unregister callback (power-down, free, gone) */
	T->client.funcs->unregister(&T->client);
	CHECK(!T->ms && !T->vbl_ref && T->gone, "unregister did not free the modeset");
	us = -1;
	CHECK(a9x_vblank(T, &us) == 0 && us == 0, "after unbind: count or time not 0");
	teardown();
}

/* (80) M4 D1: per-boot cap across HV sessions */
static void case_80(void)
{
	u64 t0;

	pw_setup("(80) [M4] pg_settle_total_max: the cap spans HV sessions; once used up a low latches at once");
	pg_settle_ms = 100;
	pg_settle_total_max = 4;
	pw_waveform();
	for (int i = 0; i < 4; i++) {
		if (i == 2) {	/* new HV session: the per-session budget resets, the per-boot count does not */
			a9_power_off(pw_a9);
			mock_sleep_us(5000);
			pw_pg = 1;
			pw_pg_good_from_ns = 0;
			pw_waveform();
		}
		mock_sleep_us(2000);
		pw_a9->wf_end = ktime_get();
		st_dip(4000);
		CHECK(!a9_check_live_rails(pw_a9), "dip %d latched", i);
	}
	CHECK(pw_a9->settle_total == 4 && pw_a9->settle_used == 2 && pw_a9->diag.settle_ok == 4, "total %u used %u",
	      pw_a9->settle_total, pw_a9->settle_used);
	pw_a9->wf_end = ktime_get();
	st_dip(4000);
	t0 = mock_now_ns();
	CHECK(a9_check_live_rails(pw_a9) == -EIO && ST_IMMEDIATE(t0), "5th dip settled past the cap");
	CHECK(pw_a9->diag.settle_cap_skip == 1 && pw_a9->diag.settle_skip == 0 && atomic_read(&a9x_rail_fault) && !T->on,
	      "cap skip/latch");
	teardown();

	pw_setup("(80b) [M4] pg_settle_total_max 0: never settles");
	pg_settle_ms = 100;
	pg_settle_total_max = 0;
	pw_waveform();
	st_dip(4000);
	CHECK(a9_check_live_rails(pw_a9) == -EIO && pw_a9->diag.settle_cap_skip == 1, "cap 0 settled");
	teardown();
}

/* (75) containment ordering is the old one after a timeout: no drive after the latch, a second update refused */
static void case_75(void)
{
	pw_setup("(75) [0053] after a settle timeout the next power-up is refused and no frame is sent");
	pg_settle_ms = 100;
	pw_waveform();
	pw_pg = 0;
	CHECK(a9_check_live_rails(pw_a9) == -EIO, "latched");
	{
		u64 frames = T->frames;
		CHECK(a9_power_on(pw_a9) == -EIO && !T->on && T->frames == frames, "re-powered / frame sent");
	}
	CHECK(pw_a9->rail.count == 1, "second fault recorded");
	teardown();
}

/* (76..78) [eink-damage] a9_take_job hands the damage tiles to the job only when they are complete */
static void case_76(void)
{
	struct a9_job job;

	pw_setup("(76) [eink-damage] damage_prep off: no tile mask is handed over, tiles dropped, the job is the reference one");
	pw_a9->dirty_tm[5] = 1;
	CHECK(pw_next_job(&job) && !job.tm_ok && !pw_a9->dirty_tm[5], "mask handed over with damage_prep off (ok %d)", job.tm_ok);
	teardown();

	pw_setup("(76b) [eink-damage] damage_prep on, every convert tracked: the tiles go to the job and the accumulator is cleared");
	damage_prep = true;
	pw_a9->e.have_pre = 1;
	pw_a9->dirty_tm[5] = 1;
	pw_a9->dirty_tm[A9E_NTILES - 1] = 1;
	CHECK(pw_next_job(&job) && job.tm_ok && job.tm[5] == 1 && job.tm[A9E_NTILES - 1] == 1 && job.tm[6] == 0 &&
	      !pw_a9->dirty_tm[5] && !pw_a9->dirty_tm[A9E_NTILES - 1] && !pw_a9->tm_lost, "tiles not handed over");
	CHECK(pw_next_job(&job) && job.tm_ok && !job.tm[5], "second job carries stale tiles");
	teardown();

	pw_setup("(76c) [eink-damage] a convert made while untracked (damage_prep was off) taints exactly the next job");
	damage_prep = true;
	pw_a9->tm_lost = true;
	pw_a9->dirty_tm[9] = 1;
	CHECK(pw_next_job(&job) && !job.tm_ok && !pw_a9->tm_lost && !pw_a9->dirty_tm[9], "lost flag not honoured");
	CHECK(pw_next_job(&job) && job.tm_ok, "the taint outlived one job");
	teardown();
}

static void case_77(void)
{
	struct a9_job job;

	pw_setup("(77) [eink-damage] test pattern: neither its job nor the one after it may use tiles (the grey was not the shadow)");
	damage_prep = true;
	pw_a9->test_pattern = true;
	CHECK(pw_next_job(&job) && !job.tm_ok, "pattern job used tiles");
	pw_a9->test_pattern = false;
	CHECK(pw_next_job(&job) && !job.tm_ok, "the job after the pattern used tiles");
	CHECK(pw_next_job(&job) && job.tm_ok, "tiles never came back");
	teardown();
}

static void case_78(void)
{
	struct a9_job job;

	pw_setup("(78) [eink-damage] recovery/clear jobs keep their full rect and the safety flags; tiles never widen or narrow them");
	damage_prep = true;
	pw_a9->clear_req = true;
	pw_a9->dirty_tm[3] = 1;
	CHECK(pw_next_job(&job) && job.clear == 1 && job.mode == A9_MODE_CLEAR &&
	      job.rect.x1 == 0 && job.rect.y1 == 0 && job.rect.x2 == A9T_W && job.rect.y2 == A9T_H,
	      "clear job rect/mode changed by the tiles");
	teardown();
}
