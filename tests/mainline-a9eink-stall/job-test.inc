/* Actual driver dispatcher/producer/run_ring, with only rendering/hashing and
 * board power helpers replaced. No handwritten producer/job loop is used. */
struct sha256_ctx { u64 magic; unsigned int updates; u64 last_ns; };
static atomic_bool job_thread_stop, job_dispatch_hold;
static atomic_int job_bad, job_frames, job_starts, job_inits, job_init_ends;
static int job_bin, job_nframes, job_slow_after, job_nice, job_sched_policy;
static int job_pause_after;
static bool job_pause_until_drive;
static unsigned int job_change_prefill;
static unsigned int prefill_frames; /* actual initialized definition is extracted below */
static atomic_bool job_pause_released;
static _Thread_local int mock_current_policy, mock_current_nice, mock_current_rtprio;
static u64 job_cookie, job_frame_delay, job_done_ns, job_poweroff_ns;
static struct sha256_ctx *job_hash;
static pthread_t job_thread;
static struct a9_eink *job_a9;
#define JOB_HASH_MAGIC 0x27ca5b94fe613809ull
#define current NULL
#define kthread_should_stop() atomic_load(&job_thread_stop)
#define set_user_nice(task, n) do { (void)(task); job_nice = (n); } while (0)
static void mock_sched_normal(int nice)
{
	mock_current_policy = job_sched_policy = SCHED_OTHER;
	mock_current_nice = job_nice = nice;
	mock_current_rtprio = 0;
}
static void mock_sched_fifo(void)
{
	mock_current_policy = SCHED_FIFO;
	mock_current_rtprio = 50; /* sched_set_fifo kernel default MAX_RT_PRIO/2 */
}
#define sched_set_normal(task, nice) do { (void)(task); mock_sched_normal(nice); } while (0)
#define sched_set_fifo(task) do { (void)(task); mock_sched_fifo(); } while (0)

static void job_check_engine(struct a9e_engine *e, int bin)
{
	if (e != &job_a9->e || bin != job_bin || e->pre_mode != job_cookie ||
	    e->grey != (u8 *)&job_cookie)
		atomic_fetch_add(&job_bad, 1);
}
static void job_begin_init(struct a9e_engine *e, int bin)
{
	job_check_engine(e, bin);
	e->init = 1;
	e->nframes = 0;
	atomic_fetch_add(&job_inits, 1);
}
static void job_start(struct a9e_engine *e, int bin)
{
	job_check_engine(e, bin);
	e->init = 0;
	e->nframes = 0;
	atomic_fetch_add(&job_starts, 1);
}
static void job_init_end(struct a9e_engine *e)
{
	job_check_engine(e, job_bin);
	atomic_fetch_add(&job_init_ends, 1);
}
static int job_frame(struct a9e_engine *e, u8 *out)
{
	int n = e->init ? 2 : job_nframes;
	int k = atomic_fetch_add(&job_frames, 1);

	job_check_engine(e, job_bin);
	if (!k && job_change_prefill)
		WRITE_ONCE(prefill_frames, job_change_prefill);
	if (k >= job_pause_after)
		while (!atomic_load(&job_pause_released) &&
		       !(job_pause_until_drive && atomic_load(&observed_drives)))
			mock_sleep_us(50);
	if (k >= job_slow_after)
		mock_sleep_us(job_frame_delay);
	memset(out, 0, A9T_FRAME_BYTES);
	if (++e->nframes < (unsigned int)n)
		out[(size_t)A9T_HEAD_ROWS * A9T_TW * 4] = (u8)(job_bin + 1);
	job_done_ns = mock_now_ns();
	return e->nframes < (unsigned int)n;
}
static void job_sha_update(struct sha256_ctx *sc, const u8 *data, size_t n)
{
	if (sc != job_hash || sc->magic != JOB_HASH_MAGIC || n != A9T_FRAME_BYTES ||
	    data != job_a9->frame)
		atomic_fetch_add(&job_bad, 1);
	sc->updates++;
	sc->last_ns = mock_now_ns();
}
static void job_power_off(struct a9_eink *a9)
{
	/* Board PMIC helper is outside the extracted code; transport is real. */
	a9x_power_off(&a9->tx);
	job_poweroff_ns = mock_now_ns();
	atomic_store(&job_pause_released, true);
}
#define a9e_begin_init job_begin_init
#define a9e_start job_start
#define a9e_init_end job_init_end
#define a9e_frame job_frame
#define sha256_update job_sha_update
#define a9_power_off job_power_off
#define enable fake_enable
/* A scheduler gate can hold the real thread before it accepts a posted job. */
#undef wait_event_interruptible
#define wait_event_interruptible(wq, cond) do { \
	while (atomic_load(&job_dispatch_hold) || !(cond)) mock_sleep_us(50); \
} while (0)
#include "driver-job.inc"
#undef enable
#undef a9_power_off
#undef sha256_update
#undef a9e_frame
#undef a9e_init_end
#undef a9e_start
#undef a9e_begin_init

static void *job_thread_entry(void *arg)
{
	/* Simulate an old userspace helper having incorrectly promoted us. */
	mock_current_policy = job_sched_policy = SCHED_FIFO;
	mock_current_rtprio = 50;
	a9_render_thread(arg);
	return NULL;
}
static void job_setup(void)
{
	mock_hrtimer_stop(&T->stall); /* discard setup's unused transport */
	/* Attach transport directly in its final object: never copy pthread locks. */
	job_a9 = calloc(1, sizeof(*job_a9));
	T = &job_a9->tx;
	T->dev = &mdev;
	T->panel_np = &panel_np;
	T->fourcc = 0x34325258;
	T->tmpl = tmpl;
	T->fault = test_fault;
	mutex_init(&T->lock);
	a9x_init(T);
	CHECK(!a9x_attach(T), "attach job transport");
	job_a9->dev = &mdev;
	job_a9->frame = malloc(A9T_FRAME_BYTES);
	a9r_init(&job_a9->ring, malloc(A9R_SLOTS * A9R_DATA));
	init_waitqueue_head(&job_a9->rwq);
	job_nframes = 4;
	job_slow_after = INT_MAX;
	job_pause_after = INT_MAX;
	job_pause_until_drive = false;
	job_change_prefill = 0;
	atomic_store(&job_pause_released, false);
	prefill_frames = 12;
	observe_ring = &job_a9->ring;
	job_frame_delay = 0;
	job_poweroff_ns = job_done_ns = 0;
	atomic_store(&job_thread_stop, false);
	atomic_store(&job_dispatch_hold, false);
	atomic_store(&job_bad, 0);
	atomic_store(&job_frames, 0);
	atomic_store(&job_starts, 0);
	atomic_store(&job_inits, 0);
	atomic_store(&job_init_ends, 0);
	pthread_create(&job_thread, NULL, job_thread_entry, job_a9);
	CHECK(!a9x_power_on(T), "job power on");
}
static void job_end(void)
{
	atomic_store(&job_thread_stop, true);
	wake_up(&job_a9->rwq);
	pthread_join(job_thread, NULL);
	CHECK(!atomic_load(&job_bad), "job/bin/engine/hash visibility errors %d", atomic_load(&job_bad));
	CHECK(job_nice == -10 && job_sched_policy == SCHED_OTHER, "producer policy%d nice%d", job_sched_policy, job_nice);
}
static void job_prepare(int bin, struct sha256_ctx *sc)
{
	job_bin = bin;
	job_cookie = 0x731a2400u + bin;
	job_a9->e.pre_mode = (u32)job_cookie;
	job_a9->e.grey = (u8 *)&job_cookie;
	job_hash = sc;
	*sc = (struct sha256_ctx){ .magic = JOB_HASH_MAGIC };
}

/* Review 14 regression 23: fill can become ready inside a late idle commit. */
static void case_23(void)
{
	struct prod p = { .n = 32, .frame_us = 1000, .slow_after = INT_MAX, .wait_for_commit = true };
	pthread_t th;
	u64 start;
	int ret;

	setup("(23) [0035] late-ready prefill is rejected after idle commit returns");
	CHECK(!a9x_power_on(T), "power on");
	a9r_reset(&R);
	pthread_create(&th, NULL, producer, &p);
	commit_us = 200000;
	late_ready_ring = &R; /* mock commit waits for the required producer state */
	start = mock_now_ns();
	ret = a9r_prefill(&R, T, 40, &fake_enable, 32);
	late_ready_ring = NULL;
	CHECK(ret == -ETIMEDOUT, "late prefill ret %d, elapsed %.3f ms", ret, (mock_now_ns() - start) / 1e6);
	CHECK(R.head == 32 && R.done, "producer did not become ready inside late commit");
	a9r_stop(&R);
	pthread_join(th, NULL);
	commit_us = 11700;
	a9x_power_off(T);
	check_no_emergency();
	teardown();
}

/* Review 14 regression 24 through the ACTUAL driver cleanup. */
static void case_24(void)
{
	struct sha256_ctx sc;
	bool shown;
	int ret;
	u64 returned;

	setup("(24) [0035] actual run_ring: failed abort-neutral emergency precedes slow producer join");
	job_setup();
	prefill_frames = 32; /* preserve the original full-ring regression */
	job_nframes = 100;
	job_slow_after = 32;
	job_frame_delay = 90000;
	job_prepare(5, &sc);
	abort_after_drive = 2;
	ret = a9_run_ring(job_a9, false, job_bin, &sc, &shown);
	returned = mock_now_ns();
	abort_after_drive = 0;
	CHECK(ret == -ECANCELED, "run_ring ret %d", ret);
	CHECK(atomic_load(&n_emerg) > 0, "no immediate emergency");
	CHECK(emerg_t_ns < job_done_ns && emerg_t_ns < returned, "emergency not before join completion");
	CHECK(returned - emerg_t_ns >= 30000000, "slow producer was not still rendering during emergency");
	CHECK(emerg_commit_count == abort_blank_commit_count, "another commit occurred before emergency");
	fprintf(stderr, "      emergency %.3f ms after failed neutral; join finished %.3f ms later\n",
		(emerg_t_ns - abort_blank_fail_ns) / 1e6, (returned - emerg_t_ns) / 1e6);
	CHECK(emerg_t_ns - abort_blank_fail_ns < 3000000, "emergency delayed %.3f ms", (emerg_t_ns - abort_blank_fail_ns) / 1e6);
	CHECK(job_poweroff_ns && job_poweroff_ns < job_done_ns, "power down did not precede join");
	CHECK(sc.magic == JOB_HASH_MAGIC && sc.last_ns <= returned, "hash context lifetime");
	CHECK(atomic_read(&a9x_stall_fault), "failed neutral did not latch fault");
	job_end();
	teardown();
}

static void case_25(void)
{
	int inits = 0;
	setup("(25) [0035] real persistent dispatcher: 24 jobs publish bin/init/hash/prepared-engine state");
	job_setup();
	commit_us = 0;
	for (int k = 0; k < 24; k++) {
		struct sha256_ctx sc;
		bool init = k & 1, shown = false;

		job_prepare((k * 7) % 14, &sc);
		CHECK(!a9_run_ring(job_a9, init, job_bin, &sc, &shown), "job %d failed", k);
		CHECK(shown == init, "job %d init visibility/count", k);
		CHECK(sc.updates == 4 && sc.magic == JOB_HASH_MAGIC, "job %d hash context/updates %u", k, sc.updates);
		CHECK(!READ_ONCE(job_a9->rjob) && !job_a9->ring.running, "job %d not quiescent", k);
		inits += init;
	}
	CHECK(atomic_load(&job_starts) == 24, "start count");
	CHECK(atomic_load(&job_inits) == inits && atomic_load(&job_init_ends) == inits, "init counts");
	job_end();
	a9x_power_off(T);
	check_no_emergency();
	teardown();
}

static void case_26(void)
{
	struct sha256_ctx sc;
	struct en_arg ea = { 20 };
	pthread_t dt;
	bool shown;
	u64 returned;
	int ret;

	setup("(26) [0035] cancel during slow producer: actual dispatcher joins before stack hash reuse");
	job_setup();
	job_slow_after = 0;
	job_frame_delay = 90000;
	job_prepare(9, &sc);
	pthread_create(&dt, NULL, disable_later, &ea);
	ret = a9_run_ring(job_a9, false, job_bin, &sc, &shown);
	returned = mock_now_ns();
	pthread_join(dt, NULL);
	CHECK(ret == -ECANCELED, "cancel prefill ret %d", ret);
	CHECK(!job_a9->ring.running && !READ_ONCE(job_a9->rjob), "producer/job still live");
	CHECK(sc.updates == 1 && sc.last_ns <= returned, "stack hash reused before producer stopped");
	CHECK(job_poweroff_ns && job_poweroff_ns < sc.last_ns, "physical shutdown did not precede slow hash completion");
	sc.magic = 0; /* stack owner can now reuse; producer must never touch it */
	mock_sleep_us(3000);
	CHECK(!sc.magic && sc.updates == 1, "hash touched after join");
	job_end();
	check_no_emergency();
	teardown();
}

static void case_27(void)
{
	struct prod p = { .n = 32, .frame_us = 1000, .slow_after = INT_MAX };
	struct en_arg ea = { 30 };
	pthread_t th, dt;
	int ret;

	setup("(27) [0035] ready plus enable=0 inside idle refresh must cancel admission");
	CHECK(!a9x_power_on(T), "power on");
	a9r_reset(&R);
	pthread_create(&th, NULL, producer, &p);
	pthread_create(&dt, NULL, disable_later, &ea);
	commit_us = 200000;
	ret = a9r_prefill(&R, T, 1000, &fake_enable, 32);
	CHECK(ret == -ECANCELED, "late cancellation ret %d", ret);
	a9r_stop(&R);
	pthread_join(th, NULL);
	pthread_join(dt, NULL);
	commit_us = 11700;
	a9x_power_off(T);
	check_no_emergency();
	teardown();
}

static void case_28(void)
{
	struct sha256_ctx sc;
	bool shown;
	int ret;

	setup("(28) [0035] actual run_ring unexpected partial-waveform starvation latches failed trial");
	job_setup();
	prefill_frames = 32; /* preserve the original full-ring regression */
	job_nframes = 135;
	job_slow_after = 40;
	job_frame_delay = 250000; /* stays rendering through neutral + power-down */
	job_prepare(3, &sc);
	ret = a9_run_ring(job_a9, false, job_bin, &sc, &shown);
	CHECK(ret == -EAGAIN, "starvation ret %d", ret);
	CHECK(job_a9->ring.underruns == 1, "underrun count");
	CHECK(atomic_read(&a9x_stall_fault) && !fake_enable, "unexpected abort must stop trial");
	CHECK(!T->on && T->front_null, "pipeline not neutral and off");
	CHECK(job_poweroff_ns < job_done_ns, "HV remained up while joining slow producer");
	CHECK(!atomic_load(&n_emerg), "successful neutral should allow ordinary shutdown");
	/* Even an accidental userspace enable cannot submit another waveform. */
	unsigned int starts = atomic_load(&job_starts);
	WRITE_ONCE(fake_enable, true);
	CHECK(a9_run_ring(job_a9, false, job_bin, &sc, &shown) == -EIO, "latched trial accepted a retry");
	CHECK(atomic_load(&job_starts) == (int)starts, "posted another job despite latched fault");
	job_end();
	teardown();
}

static void case_29(void)
{
	setup("(29) [0035] empty-ring cancellation precedes underrun and queued producer error refuses drive");
	CHECK(!a9x_power_on(T), "power on");
	a9r_reset(&R);
	u64 underruns = R.underruns;
	WRITE_ONCE(fake_enable, false);
	CHECK(a9r_drive(&R, T, &fake_enable) == -ECANCELED, "empty ring cancellation");
	CHECK(R.underruns == underruns, "intentional cancel counted as underrun");
	WRITE_ONCE(fake_enable, true);
	a9r_reset(&R);
	memset(a9r_slot(&R), 0x55, A9R_DATA);
	a9r_publish(&R, false);
	a9r_finish(&R, -EIO);
	int n = ncommits;
	CHECK(a9r_drive(&R, T, &fake_enable) == -EIO, "producer error hidden by queued prefix");
	CHECK(ncommits == n, "drove queued prefix after producer failed");
	a9x_power_off(T);
	check_no_emergency();
	teardown();
}

static void *cancel_and_release_dispatch(void *arg)
{
	(void)arg;
	mock_sleep_us(20000);
	WRITE_ONCE(fake_enable, false);
	mock_sleep_us(70000);
	atomic_store(&job_dispatch_hold, false);
	return NULL;
}
static void case_30(void)
{
	struct sha256_ctx sc;
	pthread_t dt;
	bool shown;
	int ret;

	setup("(30) [0035] posted job cancelled before acquire: join retains engine/hash until actual thread accepts");
	job_setup();
	atomic_store(&job_dispatch_hold, true);
	job_prepare(11, &sc);
	pthread_create(&dt, NULL, cancel_and_release_dispatch, NULL);
	ret = a9_run_ring(job_a9, false, job_bin, &sc, &shown);
	pthread_join(dt, NULL);
	CHECK(ret == -ECANCELED, "pending-job cancel ret %d", ret);
	CHECK(atomic_load(&job_starts) == 1 && !atomic_load(&job_frames), "real dispatcher did not drain stopped pending job");
	CHECK(!READ_ONCE(job_a9->rjob) && !job_a9->ring.running, "pending job outlived join");
	CHECK(sc.magic == JOB_HASH_MAGIC && !sc.updates, "cancelled pending job used hash");
	job_end();
	check_no_emergency();
	teardown();
}
static void case_31(void)
{
	setup("(31) [0035] explicit ring stop cancels prefill and drive even while enable remains true");
	CHECK(!a9x_power_on(T), "power on");
	a9r_reset(&R);
	a9r_request_stop(&R);
	int n = ncommits;
	CHECK(a9r_prefill(&R, T, 1000, &fake_enable, 32) == -ECANCELED, "stop not checked in prefill");
	CHECK(a9r_drive(&R, T, &fake_enable) == -ECANCELED, "stop not checked in drive");
	CHECK(ncommits == n, "commit after stop");
	a9r_finish(&R, -ECANCELED);
	a9r_stop(&R);
	a9x_power_off(T);
	check_no_emergency();
	teardown();
}

struct a9_job;
static int policy_body_calls, policy_body_bad;
static void mock_update_body(struct a9_eink *a9, const struct a9_job *job);
#define __a9_update mock_update_body
#include "driver-policy.inc"
#undef __a9_update
static void mock_update_body(struct a9_eink *a9, const struct a9_job *job)
{
	(void)a9;
	policy_body_calls++;
	if (job->dry ? (mock_current_policy != SCHED_OTHER || mock_current_nice != 0) :
	    (mock_current_policy != SCHED_FIFO || mock_current_rtprio != 50))
		policy_body_bad++;
}
static void case_35(void)
{
	struct a9_job job = { .dry = false };
	setup("(35) [0036] actual update wrapper scopes real FIFO50; dry and return OTHER/0");
	policy_body_calls = policy_body_bad = 0;
	mock_sched_normal(0);
	a9_update(NULL, &job);
	CHECK(mock_current_policy == SCHED_OTHER && !mock_current_nice && !mock_current_rtprio,
	      "real update leaked FIFO policy");
	/* Dry path must actively correct stale RT policy too. */
	mock_sched_fifo();
	job.dry = true;
	a9_update(NULL, &job);
	CHECK(policy_body_calls == 2 && !policy_body_bad, "update body policy mismatch");
	CHECK(mock_current_policy == SCHED_OTHER && !mock_current_nice && !mock_current_rtprio,
	      "dry update retained stale FIFO policy");
	teardown();
}

/* Review 17: foreign state first appears after a confirmed drive. The next
 * attempted neutral must refuse before commit; real cleanup must cut HV
 * while the actual persistent producer still borrows its engine/hash. */
static void case_40(void)
{
	struct sha256_ctx sc;
	bool shown;
	int ret;
	u64 returned;

	setup("(40) [Review 17] real run_ring foreign-scanout neutral refusal cuts HV before producer join");
	job_setup();
	prefill_frames = 32; /* preserve the original full-ring regression */
	job_nframes = 100;
	job_slow_after = 32;
	job_frame_delay = 90000;
	job_prepare(5, &sc);
	abort_after_drive = 2;
	abort_foreign_scanout = true;
	ret = a9_run_ring(job_a9, false, job_bin, &sc, &shown);
	returned = mock_now_ns();
	abort_after_drive = 0;
	abort_foreign_scanout = false;
	CHECK(ret == -ECANCELED, "run_ring ret %d", ret);
	CHECK(foreign_admission_armed && foreign_deadline_ns, "no armed progress at neutral refusal");
	CHECK(T->stall_armed && T->stall.expires_ns == foreign_deadline_ns && !T->front_null,
	      "neutral refusal altered progress or recorded neutral success");
	CHECK(atomic_load(&n_emerg) >= 1 && atomic_read(&a9x_stall_fault), "missing emergency/fault");
	CHECK(emerg_t_ns >= foreign_injected_ns && emerg_t_ns < foreign_deadline_ns,
	      "emergency waited for hard deadline rather than refused blank");
	CHECK(emerg_t_ns - foreign_injected_ns < 10000000, "foreign-state containment delayed");
	CHECK(emerg_t_ns < job_done_ns && returned - emerg_t_ns >= 30000000,
	      "HV removal did not precede slow producer join");
	CHECK(job_poweroff_ns && job_poweroff_ns < job_done_ns, "power down waited for producer");
	CHECK(emerg_commit_count == ncommits && ncommits && !crecs[ncommits - 1].null,
	      "foreign neutral reached DRM or false neutral commit recorded");
	CHECK(!master_refs && !modeset_refs && !atomic_alloc_refs, "refusal leaked DRM ownership");
	CHECK(!READ_ONCE(job_a9->rjob) && !job_a9->ring.running && sc.last_ns <= returned,
	      "producer/hash lifetime not drained before return");
	fprintf(stderr, "      foreign neutral refused; emergency %.3f ms after injection, join %.3f ms later\n",
		(emerg_t_ns - foreign_injected_ns) / 1e6, (returned - emerg_t_ns) / 1e6);
	job_end();
	teardown();
}

static void case_41(void)
{
	struct sha256_ctx sc;
	bool shown;
	int ret;
	u64 returned;

	setup("(41) [Review 17] refused idle prefill removes HV even with no previous drive frame");
	job_setup();
	job_nframes = 100;
	job_slow_after = 0;
	job_frame_delay = 250000;
	job_prepare(7, &sc);
	prefill_foreign_idle = true;
	ret = a9_run_ring(job_a9, false, job_bin, &sc, &shown);
	returned = mock_now_ns();
	CHECK(ret == -EBUSY, "prefill refusal ret %d", ret);
	CHECK(atomic_load(&n_emerg) >= 1 && atomic_read(&a9x_stall_fault), "idle refusal skipped HV containment");
	CHECK(emerg_t_ns >= foreign_injected_ns && emerg_t_ns - foreign_injected_ns < 40000000,
	      "idle refusal containment delayed");
	CHECK(emerg_t_ns < job_done_ns && returned - emerg_t_ns >= 100000000,
	      "idle refusal joined producer before HV removal");
	CHECK(job_poweroff_ns && job_poweroff_ns < job_done_ns, "idle refusal poweroff followed producer join");
	CHECK(!T->stall_armed && T->front_null, "idle refusal fabricated drive/progress state");
	for (int i = 0; i < ncommits; i++)
		CHECK(crecs[i].null, "unexpected drive during rejected prefill");
	CHECK(!master_refs && !modeset_refs && !atomic_alloc_refs, "idle refusal leaked ownership");
	job_end();
	teardown();
}

/* 0038: the producer cannot finish before the first actual drive. A full-ring
 * admission mutant consequently times out instead of passing this fixture. */
static void case_42(void)
{
	struct sha256_ctx sc;
	bool shown = false;

	setup("(42) [0038] default K=12 admits unfinished job at twelve slots, without waiting for full ring");
	job_setup();
	job_nframes = 30;
	job_pause_after = 12;
	job_pause_until_drive = true;
	job_change_prefill = 32; /* a concurrent write must affect the next job only */
	job_prepare(4, &sc);
	CHECK(!a9_run_ring(job_a9, false, job_bin, &sc, &shown), "early-admitted job failed");
	CHECK(first_drive_ready == 12 && !first_drive_producer_done,
	      "first drive fill %u done%d", first_drive_ready, first_drive_producer_done);
	CHECK(atomic_load(&observed_drives) == 29 && sc.updates == 30, "job lost/repeated frames");
	CHECK(!job_a9->ring.underruns && !atomic_read(&a9x_stall_fault), "healthy early job faulted");
	CHECK(T->front_null && !T->stall_armed, "completed job did not finish neutral");
	job_end();
	a9x_power_off(T);
	check_no_emergency();
	teardown();
}

static void case_43(void)
{
	for (unsigned int k = 2; k <= A9R_SLOTS; k++) {
		struct sha256_ctx sc;
		bool shown = false;
		char title[160];
		int ret;

		snprintf(title, sizeof(title), "(43) [0038] actual run_ring K=%u starvation preserves failed-trial neutral/off/join policy", k);
		setup(title);
		job_setup();
		prefill_frames = k;
		job_nframes = 100;
		/* Render exactly K frames, then retain borrowed engine/hash until
		 * shutdown. Admission and starvation are deterministic for every K. */
		job_pause_after = k;
		job_prepare(6, &sc);
		ret = a9_run_ring(job_a9, false, job_bin, &sc, &shown);
		CHECK(ret == -EAGAIN && job_a9->ring.underruns == 1, "K%u ret%d underruns%llu", k, ret, (unsigned long long)job_a9->ring.underruns);
		CHECK(first_drive_ready == k && !first_drive_producer_done, "K%u first fill%u done%d", k, first_drive_ready, first_drive_producer_done);
		CHECK(atomic_load(&observed_drives) == (int)k, "K%u drove%d frames", k, atomic_load(&observed_drives));
		CHECK(atomic_read(&a9x_stall_fault) && !fake_enable, "K%u trial not latched off", k);
		CHECK(T->front_null && !T->on && !T->stall_armed, "K%u not neutral/off", k);
		CHECK(job_poweroff_ns && job_poweroff_ns < job_done_ns, "K%u joined producer before HV shutdown", k);
		CHECK(!READ_ONCE(job_a9->rjob) && !job_a9->ring.running, "K%u borrower still active", k);
		CHECK(!atomic_load(&n_emerg), "K%u neutral unexpectedly failed", k);
		WRITE_ONCE(fake_enable, true);
		CHECK(a9_run_ring(job_a9, false, job_bin, &sc, &shown) == -EIO, "K%u latched trial admitted retry", k);
		job_end();
		teardown();
	}
}

static void case_44(void)
{
	struct sha256_ctx sc;
	bool shown = false;
	int ret;

	setup("(44) [0038] sustained slower producer drains K=12 reserve and fails closed");
	job_setup();
	job_nframes = 100;
	job_slow_after = 0;
	job_frame_delay = 20000; /* ~20 ms render versus ~11.7 ms consumer */
	job_prepare(8, &sc);
	ret = a9_run_ring(job_a9, false, job_bin, &sc, &shown);
	CHECK(ret == -EAGAIN && job_a9->ring.underruns == 1, "slow producer ret%d", ret);
	CHECK(first_drive_ready >= 12 && first_drive_ready < A9R_SLOTS && !first_drive_producer_done,
	      "slow producer admission fill%u done%d", first_drive_ready, first_drive_producer_done);
	CHECK(atomic_load(&observed_drives) >= 12 && atomic_load(&observed_drives) < 99, "not partial starvation");
	CHECK(atomic_read(&a9x_stall_fault) && !fake_enable && !T->on && T->front_null, "slow producer failed open");
	CHECK(!T->stall_armed && !atomic_load(&n_emerg), "successful neutral containment changed");
	job_end();
	teardown();
}

static void case_45(void)
{
	struct sha256_ctx sc;
	struct en_arg ea = { 40 };
	pthread_t dt;
	bool shown = false;
	int ret;

	setup("(45) [0038] cancellation with two of K=12 slots ready refuses early drive and joins after shutdown");
	job_setup();
	job_nframes = 100;
	job_pause_after = 2;
	job_prepare(10, &sc);
	pthread_create(&dt, NULL, disable_later, &ea);
	ret = a9_run_ring(job_a9, false, job_bin, &sc, &shown);
	pthread_join(dt, NULL);
	CHECK(ret == -ECANCELED && !atomic_load(&observed_drives), "cancel ret%d drives%d", ret, atomic_load(&observed_drives));
	CHECK(!job_a9->ring.underruns && !atomic_read(&a9x_stall_fault), "intentional prefill cancel became starvation/fault");
	CHECK(T->front_null && !T->on && !T->stall_armed, "prefill cancel not neutral/off");
	CHECK(job_poweroff_ns && job_poweroff_ns < job_done_ns, "prefill cancel joined before shutdown");
	CHECK(!READ_ONCE(job_a9->rjob) && !job_a9->ring.running, "prefill cancel leaked borrowed job");
	job_end();
	check_no_emergency();
	teardown();
}

static void case_46(void)
{
	struct sha256_ctx sc;
	bool shown = false;
	int ret;

	setup("(46) [0038] abort after early-admitted drive: failed neutral removes HV before paused producer join");
	job_setup();
	job_nframes = 100;
	job_pause_after = 12;
	job_prepare(12, &sc);
	abort_after_drive = 1;
	ret = a9_run_ring(job_a9, false, job_bin, &sc, &shown);
	abort_after_drive = 0;
	CHECK(ret == -ECANCELED && first_drive_ready == 12 && !first_drive_producer_done, "early abort ret%d fill%u", ret, first_drive_ready);
	CHECK(atomic_load(&observed_drives) == 1, "abort admitted extra drives");
	CHECK(atomic_load(&n_emerg) && atomic_read(&a9x_stall_fault), "failed neutral did not emergency-off/latch");
	CHECK(emerg_t_ns && emerg_t_ns < job_poweroff_ns && job_poweroff_ns < job_done_ns,
	      "failed neutral waited for producer/HV shutdown");
	CHECK(emerg_t_ns - abort_blank_fail_ns < 10000000 && emerg_commit_count == abort_blank_commit_count,
	      "failed neutral emergency delayed or additional commit attempted");
	CHECK(!emerg_front_null && emerg_stall_armed && emerg_t_ns < emerg_deadline_ns,
	      "failed neutral pretended blank/progress disarm before emergency");
	CHECK(!READ_ONCE(job_a9->rjob) && !job_a9->ring.running, "early abort leaked borrower");
	job_end();
	teardown();
}

static void case_47(void)
{
	unsigned int value = 4;
	struct kernel_param kp = { .arg = &value };
	const char *bad[] = { "0", "1", "33", "4294967295", "4294967296", "-1", "4junk", "" };

	setup("(47) [0038] actual prefill parameter setter bounds2..32; component rejects invalid watermark before commit");
	for (unsigned int i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
		CHECK(a9_prefill_set(bad[i], &kp) < 0, "accepted invalid parameter %s", bad[i]);
		CHECK(value == 4, "invalid parameter %s changed value%u", bad[i], value);
	}
	for (unsigned int k = 2; k <= 32; k++) {
		char text[12];
		snprintf(text, sizeof(text), "%u", k);
		CHECK(!a9_prefill_set(text, &kp) && value == k, "valid parameter %u refused", k);
	}
	CHECK(!a9x_power_on(T), "power on");
	a9r_reset(&R);
	int commits = ncommits;
	CHECK(a9r_prefill(&R, T, 1000, &fake_enable, 1) == -EINVAL, "component accepted K1");
	CHECK(a9r_prefill(&R, T, 1000, &fake_enable, 33) == -EINVAL, "component accepted K33");
	CHECK(ncommits == commits, "invalid K attempted idle or drive commit");
	a9r_finish(&R, 0);
	a9r_stop(&R);
	a9x_power_off(T);
	check_no_emergency();
	teardown();
}

/* Review 19: eight-frame Smooth updates must render completely at K=12. */
static void case_48(void)
{
	struct sha256_ctx sc;
	bool shown = false;

	setup("(48) [Review 19] default K=12 admits an eight-frame job only after producer completion");
	job_setup();
	job_nframes = 8;
	job_prepare(10, &sc);
	CHECK(!a9_run_ring(job_a9, false, job_bin, &sc, &shown), "short job failed");
	CHECK(first_drive_ready == 8 && first_drive_producer_done,
	      "short job first fill%u done%d", first_drive_ready, first_drive_producer_done);
	CHECK(atomic_load(&observed_drives) == 7 && sc.updates == 8, "short job lost/repeated frames");
	CHECK(!job_a9->ring.underruns && !atomic_read(&a9x_stall_fault), "short job faulted");
	CHECK(T->front_null && !T->stall_armed, "short job not neutral");
	job_end();
	a9x_power_off(T);
	check_no_emergency();
	teardown();
}
