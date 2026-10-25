/*
 * Host tests of the a9_eink frame-progress interlock (patch 0026): the REAL
 * drivers/gpu/drm/a9_eink/a9_eink_transport.c and a9_eink_guard.c are
 * #included (their statics are reachable) and run against mock.h. The
 * engine (a9_eink_engine.c, a9tcon.c) is linked unchanged for
 * a9e_frame_is_null. Built and run by run.sh.
 *
 * Every timing assertion is relative to the START of the last drive-frame
 * commit (recorded inside the mock commit, i.e. after the driver armed the
 * timer): emergency_off must come at >= the deadline (3 periods = 35.29 ms)
 * and within MARGIN_MS of it.
 */
#include "mock.h"

#include "a9_eink_transport.c"
#include "a9_eink_guard.c"

#define MARGIN_MS 15.0

/* =================== mock runtime =================== */
static u64 t0_ns;
u64 mock_now_ns(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return (u64)ts.tv_sec * 1000000000ull + ts.tv_nsec - t0_ns + 1000000000ull;
}

void mock_sleep_us(u64 us)
{
	struct timespec ts = { us / 1000000, (us % 1000000) * 1000 };

	while (nanosleep(&ts, &ts) && errno == EINTR)
		;
}

static int verbose;
static char logbuf[64][560];
static int nlog;
static pthread_mutex_t loglock = PTHREAD_MUTEX_INITIALIZER;
static void (*log_observer)(const char *message);

void mock_log(const char *lvl, const char *fmt, ...)
{
	char b[512];
	va_list ap;

	va_start(ap, fmt);
	vsnprintf(b, sizeof(b), fmt, ap);
	va_end(ap);
	if (log_observer)
		log_observer(b);
	pthread_mutex_lock(&loglock);
	if (nlog < 64)
		snprintf(logbuf[nlog++], sizeof(logbuf[0]), "%s: %s", lvl, b);
	pthread_mutex_unlock(&loglock);
	if (verbose)
		fprintf(stderr, "    [%9.3f ms] %s: %s", mock_now_ns() / 1e6, lvl, b);
}

static bool log_has(const char *s)
{
	bool r = false;

	pthread_mutex_lock(&loglock);
	for (int i = 0; i < nlog; i++)
		if (strstr(logbuf[i], s))
			r = true;
	pthread_mutex_unlock(&loglock);
	return r;
}

/* ---- hrtimer: one thread per timer; callback runs with no lock held ---- */
static void *hrt_thread(void *arg)
{
	struct hrtimer *h = arg;

	pthread_mutex_lock(&h->m);
	for (;;) {
		while (!h->stop && !h->armed)
			pthread_cond_wait(&h->c, &h->m);
		if (h->stop)
			break;
		u64 now = mock_now_ns();
		if (now < h->expires_ns) {
			u64 d = (h->expires_ns - now) / 1000;

			pthread_mutex_unlock(&h->m);
			mock_sleep_us(d < 200 ? d : 200);
			pthread_mutex_lock(&h->m);
			continue;
		}
		h->armed = false;
		h->running = true;
		pthread_mutex_unlock(&h->m);
		h->function(h);
		pthread_mutex_lock(&h->m);
		h->running = false;
		h->fired++;
		pthread_cond_broadcast(&h->c);
	}
	pthread_mutex_unlock(&h->m);
	return NULL;
}

void hrtimer_setup(struct hrtimer *h, enum hrtimer_restart (*fn)(struct hrtimer *),
		   clockid_t clk, enum hrtimer_mode mode)
{
	(void)clk;
	if (mode != HRTIMER_MODE_REL_HARD)
		abort();	/* the interlock must expire in hardirq context */
	memset(h, 0, sizeof(*h));
	h->function = fn;
	pthread_mutex_init(&h->m, NULL);
	pthread_cond_init(&h->c, NULL);
	h->started = true;
	pthread_create(&h->thr, NULL, hrt_thread, h);
}

void hrtimer_start(struct hrtimer *h, ktime_t rel, enum hrtimer_mode mode)
{
	(void)mode;
	pthread_mutex_lock(&h->m);
	h->armed = true;
	h->expires_ns = mock_now_ns() + (u64)rel;
	pthread_cond_broadcast(&h->c);
	pthread_mutex_unlock(&h->m);
}

int hrtimer_try_to_cancel(struct hrtimer *h)
{
	int r;

	pthread_mutex_lock(&h->m);
	if (h->running) {
		r = -1;
	} else {
		r = h->armed;
		h->armed = false;
	}
	pthread_mutex_unlock(&h->m);
	return r;
}

int hrtimer_cancel(struct hrtimer *h)
{
	int r;

	pthread_mutex_lock(&h->m);
	r = h->armed;
	h->armed = false;
	while (h->running)
		pthread_cond_wait(&h->c, &h->m);
	pthread_mutex_unlock(&h->m);
	return r;
}

void mock_hrtimer_stop(struct hrtimer *h)
{
	pthread_mutex_lock(&h->m);
	h->stop = true;
	pthread_cond_broadcast(&h->c);
	pthread_mutex_unlock(&h->m);
	pthread_join(h->thr, NULL);
}

static struct a9x_transport *T;

/* ---- device / drm ---- */
static struct device mdev = { "a9-eink" }, msm_dev = { "msm" };
static struct device_node panel_np = { "panel" };
static struct device_link dlink;
static const struct drm_driver msm_drv = { "msm" };
static struct drm_device ddev = { true, &msm_drv, &msm_dev, { PTHREAD_MUTEX_INITIALIZER } };
static struct drm_bridge bridge = { &ddev };
static struct drm_plane_state primary_state;
static struct drm_plane primary = { &primary_state, 0 };
static struct drm_crtc_state crtc_state;
static struct drm_crtc crtc = { &primary, &crtc_state };
static struct drm_client_dev *active_client;
static int full_client_calls, atomic_flip_calls, master_refs, modeset_refs;
static int atomic_wrong, atomic_backoffs, atomic_inject_deadlock;
static bool master_available = true;
static u64 atomic_phase_delay[DRM_ATOMIC_PROFILE_DONE];
/* period = htotal * vtotal * 1000 / clock = 11764 us (85 Hz) */
static struct drm_display_mode tmode = { A9T_TW, A9T_TH, 1000, 1000, 85000 };
static struct drm_mode_set msets[2];
static struct drm_connector connector = { .index = 0, .possible_encoders = 1u };
static struct drm_connector *connectors[] = { &connector };
static bool abort_foreign_scanout, prefill_foreign_idle;
static u64 foreign_injected_ns, foreign_deadline_ns;
static int atomic_alloc_refs;
static bool foreign_admission_armed;
/* Observe the first actual DRM drive without changing production admission. */
static struct a9_ring *observe_ring;
static unsigned int first_drive_ready;
static bool first_drive_producer_done;
static atomic_int observed_drives;

struct device_link *device_link_add(struct device *c, struct device *s, u32 f)
{ (void)c; (void)s; (void)f; return &dlink; }
struct drm_bridge *of_drm_find_and_get_bridge(struct device_node *np) { (void)np; return &bridge; }

int drm_client_init(struct drm_device *d, struct drm_client_dev *c, const char *name,
		    const struct drm_client_funcs *f)
{
	(void)name;
	c->dev = d;
	active_client = c;
	c->funcs = f;
	mutex_init(&c->modeset_mutex);
	return 0;
}

int drm_client_modeset_probe(struct drm_client_dev *c, unsigned int w, unsigned int h)
{
	(void)w; (void)h;
	memset(msets, 0, sizeof(msets));
	msets[0].crtc = &crtc;
	msets[0].mode = &tmode;
	msets[0].num_connectors = 1;
	msets[0].connectors = connectors;
	c->modesets = msets;
	return 0;
}

struct drm_client_buffer *drm_client_buffer_create_dumb(struct drm_client_dev *c, u32 w, u32 h, u32 f)
{
	struct drm_client_buffer *b = calloc(1, sizeof(*b));

	(void)c; (void)f;
	b->fb = calloc(1, sizeof(*b->fb));
	b->fb->pitches[0] = w * 4;
	b->fb->width = w;
	b->fb->height = h;
	b->fb->mem = calloc(1, (size_t)w * 4 * h);
	return b;
}

int drm_client_buffer_vmap(struct drm_client_buffer *b, struct iosys_map *m)
{
	m->vaddr = b->fb->mem;
	m->is_iomem = false;
	return 0;
}

void drm_client_buffer_delete(struct drm_client_buffer *b) { (void)b; /* leaked: a stale thread may look */ }

u64 drm_crtc_vblank_count(struct drm_crtc *c)
{
	(void)c;
	return mock_now_ns() / 11764000ull;
}

/* F6b: the same mock 85 Hz scan; the time is the start of the current frame */
u64 drm_crtc_vblank_count_and_time(struct drm_crtc *c, ktime_t *t)
{
	u64 n = drm_crtc_vblank_count(c);

	*t = (ktime_t)(n * 11764000ull);
	return n;
}

/* ---- commit / dpms hooks ---- */
enum cmode { C_OK, C_BLOCK, C_FAIL };
struct crec { u64 t_ns; bool null; int ret; u64 end_ns; u64 h; };
static bool fake_enable;
static int abort_after_drive, abort_drive_count;
static u64 abort_blank_fail_ns;
static int abort_blank_commit_count, emerg_commit_count;
static bool hash_commits;	/* (17): FNV-1a of every committed buffer */
static u64 fnv(const u8 *p, size_t n)
{
	u64 h = 1469598103934665603ull;

	while (n--)
		h = (h ^ *p++) * 1099511628211ull;
	return h;
}
static struct crec crecs[512];
static int ncommits;
static pthread_mutex_t clock_m = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t clock_c = PTHREAD_COND_INITIALIZER;
static enum cmode next_mode[512];	/* per commit index; default C_OK */
static bool block_release;
static bool late_ready_started;
static struct a9_ring *late_ready_ring; /* case 23 readiness barrier only */
static u64 commit_us = 11700;		/* a blocking commit returns ~1 period later */
static u64 dpms_t_ns;
static int dpms_calls, dpms_ret;

static int mock_scanout_commit(struct drm_client_dev *c)
{
	struct drm_framebuffer *fb = c->modesets[0].fb;
	bool null = a9e_frame_is_null(fb->mem);
	enum cmode m;
	int n, ret = 0;

	if (!null && !atomic_load(&observed_drives) && observe_ring) {
		spin_lock(&observe_ring->lock);
		first_drive_ready = observe_ring->head - observe_ring->tail;
		first_drive_producer_done = observe_ring->done;
		spin_unlock(&observe_ring->lock);
	}
	if (!null)
		atomic_fetch_add(&observed_drives, 1);
	pthread_mutex_lock(&clock_m);
	n = ncommits++;
	m = n < 512 ? next_mode[n] : C_OK;
	if (n < 512)
		crecs[n] = (struct crec){ mock_now_ns(), null, 0, 0,
					  hash_commits ? fnv(fb->mem, A9T_FRAME_BYTES) : 0 };
	pthread_mutex_unlock(&clock_m);
	switch (m) {
	case C_OK:
		if (late_ready_ring)
			WRITE_ONCE(late_ready_started, true);
		mock_sleep_us(commit_us);
		if (late_ready_ring) {
			/* Establish the late-ready prerequisite without benchmarking
			 * producer CPU speed. The real prefill deadline stays 40 ms.
			 */
			u64 limit = mock_now_ns() + 5000000000ull;

			while (!READ_ONCE(late_ready_ring->done) && mock_now_ns() < limit)
				mock_sleep_us(1000);
		}
		break;
	case C_FAIL:
		ret = -EBUSY;
		break;
	case C_BLOCK:
		pthread_mutex_lock(&clock_m);
		while (!block_release)
			pthread_cond_wait(&clock_c, &clock_m);
		pthread_mutex_unlock(&clock_m);
		ret = -ETIMEDOUT;
		break;
	}
	if (prefill_foreign_idle && null && !ret) {
		crtc_state.plane_mask |= 2u;
		prefill_foreign_idle = false;
		foreign_injected_ns = mock_now_ns();
	}
	if (abort_after_drive && !null && !ret && ++abort_drive_count == abort_after_drive) {
		WRITE_ONCE(fake_enable, false);
		if (abort_foreign_scanout) {
			crtc_state.plane_mask |= 1u << 1; /* foreign overlay */
			primary_state.rotation = DRM_MODE_ROTATE_90;
			foreign_injected_ns = mock_now_ns();
		} else {
			next_mode[n + 1] = C_FAIL;
		}
	}
	if (abort_after_drive && null && ret) {
		abort_blank_fail_ns = mock_now_ns();
		abort_blank_commit_count = ncommits;
	}
	if (n < 512) {
		crecs[n].ret = ret;
		crecs[n].end_ns = mock_now_ns();
	}
	return ret;
}

int drm_client_modeset_commit(struct drm_client_dev *c)
{
	full_client_calls++;
	crtc_state = (struct drm_crtc_state){
		.active = true, .enable = true,
		.plane_mask = drm_plane_mask(&primary),
		.connector_mask = drm_connector_mask(&connector),
		.encoder_mask = connector.possible_encoders,
		.mode = *c->modesets[0].mode,
		.adjusted_mode = *c->modesets[0].mode,
	};
	primary_state = (struct drm_plane_state){
		.crtc = &crtc, .fb = c->modesets[0].fb,
		.src_w = A9T_TW << 16, .src_h = A9T_TH << 16,
		.crtc_w = A9T_TW, .crtc_h = A9T_TH,
		.rotation = DRM_MODE_ROTATE_0,
	};
	return mock_scanout_commit(c);
}

bool drm_master_internal_acquire(struct drm_device *dev)
{
	(void)dev;
	if (!master_available)
		return false;
	master_refs++;
	return true;
}
void drm_master_internal_release(struct drm_device *dev) { (void)dev; master_refs--; }
void drm_modeset_acquire_init(struct drm_modeset_acquire_ctx *ctx, unsigned int flags)
{ (void)ctx; (void)flags; modeset_refs++; }
void drm_modeset_acquire_fini(struct drm_modeset_acquire_ctx *ctx)
{ (void)ctx; modeset_refs--; }
void drm_modeset_drop_locks(struct drm_modeset_acquire_ctx *ctx) { (void)ctx; }
int drm_modeset_backoff(struct drm_modeset_acquire_ctx *ctx)
{ (void)ctx; atomic_backoffs++; return 0; }
struct drm_atomic_commit *drm_atomic_commit_alloc(struct drm_device *dev)
{
	struct drm_atomic_commit *s = calloc(1, sizeof(*s));
	atomic_alloc_refs++;
	s->dev = dev;
	s->allow_modeset = true; /* helper must explicitly forbid a modeset */
	return s;
}
void drm_atomic_commit_put(struct drm_atomic_commit *state) { atomic_alloc_refs--; free(state); }
void drm_atomic_commit_clear(struct drm_atomic_commit *state)
{ memset(&state->plane_state, 0, sizeof(state->plane_state)); }
struct drm_crtc_state *drm_atomic_get_crtc_state(struct drm_atomic_commit *state, struct drm_crtc *c)
{
	(void)state;
	if (abort_foreign_scanout && !fake_enable && !foreign_deadline_ns) {
		foreign_deadline_ns = T->stall.expires_ns;
		foreign_admission_armed = T->stall_armed;
	}
	return c->state;
}
struct drm_plane_state *drm_atomic_get_plane_state(struct drm_atomic_commit *state, struct drm_plane *plane)
{
	if (atomic_inject_deadlock) {
		atomic_inject_deadlock--;
		return ERR_PTR(-EDEADLK);
	}
	state->plane_state = *plane->state;
	return &state->plane_state;
}
int drm_atomic_set_crtc_for_plane(struct drm_plane_state *state, struct drm_crtc *c)
{ state->crtc = c; return 0; }
void drm_atomic_set_fb_for_plane(struct drm_plane_state *state, struct drm_framebuffer *fb)
{ state->fb = fb; }
int drm_atomic_commit(struct drm_atomic_commit *state)
{
	atomic_flip_calls++;
	if (state->allow_modeset || !state->acquire_ctx || !state->profile ||
	    state->dev != active_client->dev || master_refs != 1 || modeset_refs != 1)
		atomic_wrong++;
	active_client->modesets[0].fb = state->plane_state.fb;
	for (u32 phase = DRM_ATOMIC_PROFILE_FENCES; phase <= DRM_ATOMIC_PROFILE_FLIP_DONE; phase++) {
		drm_atomic_profile_enter(state->profile, phase);
		if (atomic_phase_delay[phase])
			mock_sleep_us(atomic_phase_delay[phase]);
	}
	int ret = mock_scanout_commit(active_client);
	drm_atomic_profile_enter(state->profile, DRM_ATOMIC_PROFILE_CLEANUP);
	if (atomic_phase_delay[DRM_ATOMIC_PROFILE_CLEANUP])
		mock_sleep_us(atomic_phase_delay[DRM_ATOMIC_PROFILE_CLEANUP]);
	if (!ret)
		primary_state = state->plane_state;
	return ret;
}

int drm_client_modeset_dpms(struct drm_client_dev *c, int mode)
{
	(void)c; (void)mode;
	dpms_t_ns = mock_now_ns();
	dpms_calls++;
	return dpms_ret;
}

/* ---- fp9929a + fault hook recorders ---- */
static atomic_int n_emerg;
static u64 emerg_t_ns, emerg_deadline_ns;
static bool emerg_front_null, emerg_stall_armed;
static char emerg_why[128];

void fp9929a_emergency_off(const char *why)
{
	if (atomic_fetch_add(&n_emerg, 1) == 0) {
		emerg_t_ns = mock_now_ns();
		emerg_commit_count = ncommits;
		emerg_front_null = T->front_null;
		emerg_stall_armed = T->stall_armed;
		emerg_deadline_ns = T->stall.expires_ns;
		snprintf(emerg_why, sizeof(emerg_why), "%s", why);
	}
}

static atomic_int n_fault;
static bool mock_commit_hard;
bool msm_a9_commit_faulted(void) { return mock_commit_hard; }
void fp9929a_starvation_off(const char *why) { fp9929a_emergency_off(why); }
static void test_fault(struct a9x_transport *t) { (void)t; WRITE_ONCE(fake_enable, false); atomic_fetch_add(&n_fault, 1); }

/* =================== helpers =================== */
static u8 *drive, *zero, *tmpl;
static int failures, cur_fail;
static const char *cur_name;

#define CHECK(cond, ...) do {						\
	if (!(cond)) {							\
		fprintf(stderr, "    FAIL %s:%d: %s: ", __FILE__, __LINE__, #cond); \
		fprintf(stderr, __VA_ARGS__);				\
		fputc('\n', stderr);					\
		cur_fail++;						\
	}								\
} while (0)

static void setup(const char *name)
{
	log_observer = NULL;
	cur_name = name;
	cur_fail = 0;
	nlog = 0;
	full_client_calls = atomic_flip_calls = master_refs = modeset_refs = 0;
	atomic_wrong = atomic_backoffs = atomic_inject_deadlock = 0;
	master_available = true;
	memset(atomic_phase_delay, 0, sizeof(atomic_phase_delay));
	primary_state = (struct drm_plane_state){ .crtc = &crtc };
	atomic_alloc_refs = 0;
	abort_foreign_scanout = prefill_foreign_idle = false;
	foreign_injected_ns = foreign_deadline_ns = 0;
	foreign_admission_armed = false;
	observe_ring = NULL;
	first_drive_ready = 0;
	first_drive_producer_done = false;
	atomic_store(&observed_drives, 0);
	abort_after_drive = abort_drive_count = 0;
	abort_blank_fail_ns = 0;
	abort_blank_commit_count = emerg_commit_count = 0;
	ncommits = 0;
	memset(next_mode, 0, sizeof(next_mode));
	block_release = false;
	hash_commits = false;
	commit_us = 11700;
	late_ready_ring = NULL;
	late_ready_started = false;
	dpms_calls = 0;
	dpms_ret = 0;
	dpms_t_ns = 0;
	atomic_store(&n_emerg, 0);
	emerg_t_ns = emerg_deadline_ns = 0;
	emerg_front_null = emerg_stall_armed = false;
	emerg_why[0] = 0;
	atomic_store(&n_fault, 0);
	fake_enable = true;
	atomic_set(&a9x_stall_fault, 0);
	atomic_set(&a9x_rail_fault, 0);
	/* as a9_probe: a fresh transport per case (the old one is leaked) */
	T = calloc(1, sizeof(*T));
	T->dev = &mdev;
	T->panel_np = &panel_np;
	T->fourcc = 0x34325258;	/* XR24 */
	mutex_init(&T->lock);
	T->fault = test_fault;
	T->tmpl = tmpl;		/* patch 0028: set by the driver before the first attach */
	a9x_init(T);
	if (a9x_attach(T))
		abort();
	fprintf(stderr, "  ---- %s\n", name);
}

static void teardown(void)
{
	mock_hrtimer_stop(&T->stall);
	if (cur_fail) {
		for (int i = 0; i < ncommits && i < 512; i++)
			fprintf(stderr, "      commit %3d at %9.3f ms null %d ret %d\n", i,
				crecs[i].t_ns / 1e6, crecs[i].null, crecs[i].ret);
		if (atomic_load(&n_emerg))
			fprintf(stderr, "      emergency at %9.3f ms (%d calls): %s\n",
				emerg_t_ns / 1e6, atomic_load(&n_emerg), emerg_why);
		for (int i = 0; i < nlog; i++)
			fprintf(stderr, "      log %s", logbuf[i]);
		fprintf(stderr, "  FAIL  %s\n", cur_name);
		failures++;
	} else {
		fprintf(stderr, "  PASS  %s\n", cur_name);
	}
}

/* start time of the last drive-frame commit begun before t (0 if none) */
static u64 last_drive_start_before(u64 t)
{
	u64 r = 0;

	for (int i = 0; i < ncommits && i < 512; i++)
		if (!crecs[i].null && crecs[i].t_ns <= t)
			r = crecs[i].t_ns;
	return r;
}

static double deadline_ms(void) { return A9X_STALL_FRAMES * 11764 / 1000.0; }

/*
 * Review 8: the reference is the last CONFIRMED progress before t: the
 * return of the last successful drive commit, or, if there is none since
 * the last successful zero commit, the start of the first drive commit
 * after it (it may latch and block).
 */
static u64 last_progress_before(u64 t)
{
	u64 ref = 0;
	bool open = false;

	for (int i = 0; i < ncommits && i < 512; i++) {
		struct crec *c = &crecs[i];

		if (c->t_ns > t)
			break;
		if (c->null) {
			if (c->ret == 0 && c->end_ns && c->end_ns <= t)
				open = false, ref = 0;
			continue;
		}
		if (!open && !ref)
			ref = c->t_ns, open = true;	/* first drive commit: armed at its start */
		if (c->ret == 0 && c->end_ns && c->end_ns <= t)
			ref = c->end_ns;		/* confirmed progress */
	}
	return ref;
}

/* emergency exactly once, at deadline .. deadline + MARGIN after the last
 * drive-frame commit started; fault latched, fault hook ran */
static void check_emergency_on_time(void)
{
	u64 s;
	double d;

	CHECK(atomic_load(&n_emerg) == 1, "emergency_off calls: %d (want 1)", atomic_load(&n_emerg));
	if (!atomic_load(&n_emerg))
		return;
	s = last_progress_before(emerg_t_ns);
	CHECK(s, "no drive commit before the emergency");
	d = (emerg_t_ns - s) / 1e6;
	fprintf(stderr, "      emergency %.3f ms after the last confirmed progress\n", d);
	CHECK(d >= deadline_ms() - 0.05 && d <= deadline_ms() + MARGIN_MS,
	      "emergency %.3f ms after the last confirmed progress (want %.2f .. +%.0f)",
	      d, deadline_ms(), MARGIN_MS);
	CHECK(atomic_read(&a9x_stall_fault) == 1, "stall_fault not latched");
	CHECK(atomic_load(&n_fault) >= 1 && !fake_enable, "fault hook (enable := 0) not run");
	CHECK(strstr(emerg_why, "no frame progress for 36 ms with a drive frame on the panel") != NULL,
	      "why: '%s'", emerg_why);
}

static void check_no_emergency(void)
{
	CHECK(atomic_load(&n_emerg) == 0, "emergency_off called %d times", atomic_load(&n_emerg));
	CHECK(atomic_read(&a9x_stall_fault) == 0, "stall_fault latched");
	CHECK(atomic_load(&n_fault) == 0, "fault hook ran");
}

struct show_res { const u8 *frame; int ret; };
static void *show_thread(void *arg)
{
	struct show_res *r = arg;

	r->ret = a9x_show(T, r->frame);
	return NULL;
}

/* =================== cases =================== */

/* (1) worker stops after a drive frame (mid-waveform) */
static void case_1(void)
{
	setup("(1) worker stalls mid-waveform after a drive frame: emergency at the deadline");
	CHECK(a9x_power_on(T) == 0, "power on");
	for (int i = 0; i < 10; i++)
		CHECK(a9x_show(T, drive) == 0, "show %d", i);
	mock_sleep_us(120000);		/* worker gone: nothing calls a9x_show */
	check_emergency_on_time();
	CHECK(log_has("FP9929A emergency off, fault latched"), "no crit log");
	CHECK(T->stall_kind == 1, "between-commit starvation not distinguished");
	/* the later power-down still blanks and does not re-fire */
	CHECK(a9x_power_off(T) == 0, "power off");
	CHECK(T->front_null && !T->stall_armed, "front_null %d armed %d", T->front_null, T->stall_armed);
	mock_sleep_us(60000);
	CHECK(atomic_load(&n_emerg) == 1, "re-fired: %d", atomic_load(&n_emerg));
	teardown();
}

/* (2) a drive-frame commit never returns (holds t->lock) */
static void case_2(void)
{
	struct show_res r = { drive, 0 };
	pthread_t th;
	int base;

	setup("(2) drive commit blocks forever (t->lock held): emergency at the deadline, no lock needed");
	CHECK(a9x_power_on(T) == 0, "power on");
	for (int i = 0; i < 5; i++)
		CHECK(a9x_show(T, drive) == 0, "show %d", i);
	base = ncommits;
	next_mode[base] = C_BLOCK;
	pthread_create(&th, NULL, show_thread, &r);
	mock_sleep_us(120000);
	CHECK(pthread_mutex_trylock(&T->lock.m) == EBUSY, "t->lock not held by the blocked commit");
	CHECK(ncommits == base + 1, "blocked commit not reached");
	CHECK(T->profile_valid && READ_ONCE(T->profile.phase) == DRM_ATOMIC_PROFILE_FLIP_DONE,
	      "blocked in-flight commit phase not observable");
	check_emergency_on_time();
	CHECK(T->stall_kind == 3, "in-flight expiry must remain unapproved until return");
	pthread_mutex_lock(&clock_m);
	block_release = true;
	pthread_cond_broadcast(&clock_c);
	pthread_mutex_unlock(&clock_m);
	pthread_join(th, NULL);
	CHECK(r.ret == -ETIMEDOUT, "blocked show returned %d", r.ret);
	CHECK(T->transport_error && T->stall_kind == 2 && !T->stall_commit_ok,
	      "real return error was not sticky/hard");
	/* the blocked commit failed; the power-down must still commit a zero frame */
	CHECK(a9x_power_off(T) == 0, "power off");
	CHECK(crecs[ncommits - 1].null && crecs[ncommits - 1].ret == 0, "no zero commit at power-down");
	teardown();
}

/* (11) review 8: a drive frame succeeds, 18 ms later the next drive commit
 * starts and blocks: the deadline must NOT restart at that attempt */
static void case_11(void)
{
	struct show_res r = { drive, 0 };
	pthread_t th;

	setup("(11) [0027] drive frame OK, next drive commit 18 ms later blocks: emergency 35 ms after the OK frame");
	CHECK(a9x_power_on(T) == 0, "power on");
	CHECK(a9x_show(T, drive) == 0, "show");
	mock_sleep_us(18000);
	next_mode[ncommits] = C_BLOCK;
	pthread_create(&th, NULL, show_thread, &r);
	mock_sleep_us(120000);
	check_emergency_on_time();
	pthread_mutex_lock(&clock_m);
	block_release = true;
	pthread_cond_broadcast(&clock_c);
	pthread_mutex_unlock(&clock_m);
	pthread_join(th, NULL);
	a9x_power_off(T);
	teardown();
}

/* (12) review 8: drive commits that keep failing at once must not keep
 * postponing the deadline */
static void case_12(void)
{
	setup("(12) [0027] drive commits failing every 8 ms after an OK frame: emergency 35 ms after the OK frame");
	CHECK(a9x_power_on(T) == 0, "power on");
	CHECK(a9x_show(T, drive) == 0, "show");
	for (int i = 0; i < 12; i++) {
		next_mode[ncommits] = C_FAIL;
		int ret = a9x_show(T, drive);
		CHECK(ret == -EBUSY || (ret == -EIO && atomic_read(&a9x_stall_fault)),
		      "failing show %d ret %d", i, ret);
		mock_sleep_us(8000);
	}
	mock_sleep_us(40000);
	check_emergency_on_time();
	next_mode[ncommits] = C_OK; /* fault refusal never submitted its attempt */
	a9x_power_off(T);
	teardown();
}

/* (3) the zero commit of the power-down fails */
static void case_3(void)
{
	int ret;
	u64 s;

	setup("(3) power-down zero commit fails: emergency at once, fault latched, DPMS still runs");
	CHECK(a9x_power_on(T) == 0, "power on");
	CHECK(a9x_show(T, drive) == 0, "show");
	next_mode[ncommits] = C_FAIL;
	ret = a9x_power_off(T);
	CHECK(ret == 0, "power off %d", ret);
	CHECK(atomic_load(&n_emerg) >= 1, "emergency calls %d", atomic_load(&n_emerg));
	CHECK(strstr(emerg_why, "neutral commit failed") != NULL, "why '%s'", emerg_why);
	CHECK(dpms_calls == 1 && emerg_t_ns && emerg_t_ns < dpms_t_ns, "emergency not before DPMS off");
	s = last_drive_start_before(emerg_t_ns);
	CHECK(s && (emerg_t_ns - s) / 1e6 < deadline_ms(), "came from the timer, not the failed zero commit");
	CHECK(atomic_read(&a9x_stall_fault) == 1 && atomic_load(&n_fault) >= 1 && !fake_enable,
	      "fault %d hook %d", atomic_read(&a9x_stall_fault), atomic_load(&n_fault));
	int emergencies = atomic_load(&n_emerg);
	mock_sleep_us(80000);
	CHECK(atomic_load(&n_emerg) == emergencies, "timer fired again: %d", atomic_load(&n_emerg));
	teardown();
}

/* (4) a late drive frame: 60 ms between two drive frames */
static void case_4(void)
{
	setup("(4) late frame: 60 ms gap between drive frames: emergency before the second");
	CHECK(a9x_power_on(T) == 0, "power on");
	CHECK(a9x_show(T, drive) == 0, "show 1");
	mock_sleep_us(60000 - commit_us);
	int n = ncommits;
	CHECK(a9x_show(T, drive) == -EIO, "drive attempt after fault must be refused");
	CHECK(ncommits == n, "submitted a late frame after HV removal");
	check_emergency_on_time();
	CHECK(a9x_power_off(T) == 0, "power off");
	teardown();
}

/* (5) a normal 135-frame waveform at ~12 ms cadence, ending on a null frame */
static void case_5(void)
{
	u64 t0, t1;

	setup("(5) normal 135-frame waveform (~12 ms cadence, last frame null): no emergency");
	CHECK(a9x_power_on(T) == 0, "power on");
	t0 = mock_now_ns();
	for (int i = 0; i < 135; i++) {
		mock_sleep_us(300);	/* frame computation */
		CHECK(a9x_show(T, i < 134 ? drive : zero) == 0, "show %d", i);
	}
	t1 = mock_now_ns();
	CHECK(!T->stall_armed && T->front_null, "armed %d front_null %d after the null frame",
	      T->stall_armed, T->front_null);
	mock_sleep_us(100000);		/* hold with the null frame on */
	check_no_emergency();
	CHECK(a9x_power_off(T) == 0, "power off");
	check_no_emergency();
	fprintf(stderr, "      135 frames in %.1f ms (%.2f ms/frame)\n", (t1 - t0) / 1e6, (t1 - t0) / 1e6 / 135);
	teardown();
}

/* (6) idle hold: only zero frames, long gaps */
static void case_6(void)
{
	setup("(6) zero-only frames with 200 ms gaps (idle hold): no emergency");
	CHECK(a9x_power_on(T) == 0, "power on");
	for (int i = 0; i < 4; i++) {
		CHECK(a9x_show(T, zero) == 0, "show %d", i);
		mock_sleep_us(200000);
	}
	check_no_emergency();
	CHECK(a9x_power_off(T) == 0, "power off");
	check_no_emergency();
	teardown();
}

/* (7) the second initial zero commit of the power-up fails */
static void case_7(void)
{
	int ret;

	setup("(7) power-up: second idle refresh fails: refuse power-on and immediately remove HV");
	next_mode[1] = C_FAIL;
	ret = a9x_power_on(T);
	CHECK(ret == -EBUSY, "power on returned %d", ret);
	CHECK(T->on, "on not left set for the caller's power-off");
	CHECK(log_has("second idle refresh failed"), "no log");
	CHECK(atomic_load(&n_emerg) == 1 && atomic_read(&a9x_stall_fault), "failed powered idle was not contained");
	CHECK(a9x_power_off(T) == 0 && !T->on && dpms_calls == 1, "caller's power-off");
	teardown();
}

/* (8) a failed drive commit after a null front still forces the zero frame;
 * a9x_blank disarms */
static void case_8(void)
{
	setup("(8) failed drive commit -> power-down still commits zero; a9x_blank disarms");
	CHECK(a9x_power_on(T) == 0, "power on");
	next_mode[ncommits] = C_FAIL;
	CHECK(a9x_show(T, drive) == -EBUSY, "failing show");
	CHECK(T->front_null && T->stall_armed, "front_null %d armed %d", T->front_null, T->stall_armed);
	CHECK(a9x_power_off(T) == 0, "power off");
	CHECK(crecs[ncommits - 1].null && crecs[ncommits - 1].ret == 0, "no zero commit at power-down");
	mock_sleep_us(60000);
	check_no_emergency();
	/* blank */
	CHECK(a9x_power_on(T) == 0, "power on 2");
	CHECK(a9x_show(T, drive) == 0, "show");
	CHECK(a9x_blank(T) == 0, "blank");
	CHECK(!T->stall_armed && T->front_null, "blank did not disarm");
	mock_sleep_us(60000);
	check_no_emergency();
	CHECK(a9x_power_off(T) == 0, "power off 2");
	teardown();
}

/* (9) clear_fault (a9_eink_guard.c) */
static void case_9(void)
{
	const struct kernel_param *kp = &__param_clear_fault;

	setup("(9) a9_eink.clear_fault: 1 clears the latch, 0 does not, garbage -EINVAL");
	atomic_set(&a9x_stall_fault, 1);
	CHECK(kp->ops->set("0", kp) == 0 && atomic_read(&a9x_stall_fault) == 1, "0 cleared");
	CHECK(kp->ops->set("x", kp) == -EINVAL, "garbage accepted");
	CHECK(kp->ops->set("1", kp) == 0 && atomic_read(&a9x_stall_fault) == 0, "1 did not clear");
	CHECK(log_has("frame-progress fault cleared"), "no log");
	teardown();
}

/* (10) bin admission window (a9_eink_guard.c) */
static void case_10(void)
{
	const struct kernel_param *mn = &__param_bin_min, *mx = &__param_bin_max;
	int lo, hi;
	char b[16];

	setup("(10) bin_min/bin_max: 0..13, min <= max, admission [min,max]");
	CHECK(bin_min == 0 && bin_max == 13, "defaults %d %d", bin_min, bin_max);
	for (int b2 = 0; b2 <= 13; b2++)
		CHECK(a9_bin_admitted(b2, &lo, &hi), "default refuses bin %d", b2);
	CHECK(mn->ops->set("-1", mn) == -EINVAL && mx->ops->set("14", mx) == -EINVAL, "range");
	CHECK(mn->ops->set("8", mn) == 0 && mx->ops->set("8", mx) == 0, "narrow to [8,8]");
	CHECK(mx->ops->set("7", mx) == -EINVAL && bin_max == 8, "max < min accepted");
	CHECK(mn->ops->set("9", mn) == -EINVAL && bin_min == 8, "min > max accepted");
	CHECK(!a9_bin_admitted(7, &lo, &hi) && a9_bin_admitted(8, &lo, &hi) &&
	      !a9_bin_admitted(9, &lo, &hi) && lo == 8 && hi == 8, "window [8,8]");
	CHECK(!a9_bin_admitted(-1, &lo, &hi) && !a9_bin_admitted(14, &lo, &hi), "out-of-table bins");
	mn->ops->get(b, mn);
	CHECK(!strcmp(b, "8\n"), "get '%s'", b);
	CHECK(mx->ops->set("13", mx) == 0 && mn->ops->set("0", mn) == 0, "widen back");
	teardown();
}

/* buffer idx of the transport == template, except byte 0 taken from frame (NULL: 0) */
static long buf_mismatch(int idx, const u8 *frame)
{
	const u8 *b = T->map[idx].vaddr;
	long bad = 0;

	for (size_t i = 0; i < A9T_FRAME_BYTES; i++) {
		u8 want = (i & 3) ? tmpl[i] : (frame ? frame[i] : 0);

		bad += b[i] != want;
	}
	return bad;
}

/* (13) patch 0028: no control template -> the transport never powers up */
static void case_13(void)
{
	setup("(13) no control template: power-on refused, no commit, no DPMS");
	T->tmpl = NULL;
	CHECK(a9x_power_on(T) == -ENODATA, "power on without template");
	CHECK(ncommits == 0 && dpms_calls == 0 && !T->on, "commits %d dpms %d on %d", ncommits, dpms_calls, T->on);
	CHECK(a9x_show(T, drive) == -ENODEV, "show without power");
	CHECK(log_has("no control template"), "no log");
	check_no_emergency();
	teardown();
}

/* (14) patch 0028: what the panel scans is byte 0 of the frame | template bytes 1..3 */
static void case_14(void)
{
	u8 *junk = malloc(A9T_FRAME_BYTES), *junk0 = malloc(A9T_FRAME_BYTES);

	setup("(14) template composition: idle = template, frames = byte 0 | template, null on byte 0");
	CHECK(buf_mismatch(0, NULL) == 0 && buf_mismatch(1, NULL) == 0, "buffers after attach != template");
	/* a frame whose bytes 1..3 are garbage: the buffer still gets the template there */
	for (size_t i = 0; i < A9T_FRAME_BYTES; i++)
		junk[i] = (i & 3) ? (u8)(i * 37 + 11) : drive[i];
	for (size_t i = 0; i < A9T_FRAME_BYTES; i++)
		junk0[i] = (i & 3) ? (u8)(i * 13 + 5) : 0;
	CHECK(a9x_power_on(T) == 0, "power on");
	CHECK(ncommits == 2 && crecs[0].null && crecs[1].null, "power-on commits %d", ncommits);
	CHECK(buf_mismatch(0, NULL) == 0 && buf_mismatch(1, NULL) == 0, "idle buffers after power-on != template");
	CHECK(a9x_show(T, junk) == 0, "show junk drive");
	CHECK(buf_mismatch(T->front, drive) == 0, "drive buffer: %ld bytes differ from byte0|template",
	      buf_mismatch(T->front, drive));
	CHECK(!T->front_null && T->stall_armed, "drive frame not recognised");
	/* byte 0 all zero but garbage in 1..3: a null (idle) frame, interlock released */
	CHECK(a9x_show(T, junk0) == 0, "show junk null");
	CHECK(buf_mismatch(T->front, NULL) == 0, "null frame buffer != template");
	CHECK(T->front_null && !T->stall_armed, "null frame: front_null %d armed %d", T->front_null, T->stall_armed);
	CHECK(a9x_show(T, drive) == 0, "show drive");
	CHECK(a9x_blank(T) == 0, "blank");
	CHECK(buf_mismatch(T->front, NULL) == 0 && T->front_null, "blank buffer != template");
	CHECK(a9x_show(T, drive) == 0, "show drive 2");
	CHECK(a9x_power_off(T) == 0, "power off");
	CHECK(buf_mismatch(T->front, NULL) == 0 && T->front_null, "power-off frame != template");
	check_no_emergency();
	teardown();
	free(junk);
	free(junk0);
}

/* (15) patch 0029: drive-gap counters count only gaps with a drive frame on the glass */
static void case_15(void)
{
	u64 l0, ld0;

	setup("(15) drive-gap counters: late after a null frame is not late_drive; after a drive frame it is");
	CHECK(a9x_power_on(T) == 0, "power on");
	CHECK(a9x_show(T, zero) == 0, "show zero");
	l0 = T->late;
	ld0 = T->late_drive;
	mock_sleep_us(30000);		/* a long gap with the template on the glass */
	CHECK(a9x_show(T, drive) == 0, "show drive after the gap");
	CHECK(T->late == l0 + 1 && T->late_drive == ld0, "late %llu->%llu late_drive %llu->%llu",
	      (unsigned long long)l0, (unsigned long long)T->late, (unsigned long long)ld0,
	      (unsigned long long)T->late_drive);
	CHECK(T->max_gap_us >= 41000 && T->max_gap_drive_us < 17646, "max_gap %u max_gap_drive %u",
	      T->max_gap_us, T->max_gap_drive_us);
	CHECK(T->vbl_skips_drive == 0, "vbl_skips_drive %llu", (unsigned long long)T->vbl_skips_drive);
	mock_sleep_us(12000);		/* drive frame held ~2 periods: late, inside the deadline */
	CHECK(a9x_show(T, zero) == 0, "show zero after the held drive frame");
	CHECK(T->late_drive == ld0 + 1, "late_drive %llu (want %llu)", (unsigned long long)T->late_drive,
	      (unsigned long long)(ld0 + 1));
	CHECK(T->max_gap_drive_us >= 23000, "max_gap_drive %u", T->max_gap_drive_us);
	CHECK(T->vbl_skips_drive >= 1, "vbl_skips_drive %llu", (unsigned long long)T->vbl_skips_drive);
	CHECK(a9x_power_off(T) == 0, "power off");
	check_no_emergency();
	teardown();
}

/* (16) review 10: a slow FIRST drive commit after idle is drive time */
static void case_16(void)
{
	setup("(16) [review 10] first drive commit after idle takes 25 ms, then a normal idle commit: late_drive +1");
	CHECK(a9x_power_on(T) == 0, "power on");
	CHECK(a9x_show(T, zero) == 0, "show zero");
	commit_us = 25000;		/* the drive frame may latch early in this call */
	CHECK(a9x_show(T, drive) == 0, "slow drive commit");
	commit_us = 11700;
	CHECK(a9x_show(T, zero) == 0, "normal idle commit");
	fprintf(stderr, "      late=%llu late_drive=%llu skips=%llu skips_drive=%llu max_gap=%u max_gap_drive=%u\n",
		(unsigned long long)T->late, (unsigned long long)T->late_drive,
		(unsigned long long)T->vbl_skips, (unsigned long long)T->vbl_skips_drive,
		T->max_gap_us, T->max_gap_drive_us);
	CHECK(T->late_drive >= 1, "slow drive commit not counted as drive lateness");
	CHECK(T->max_gap_drive_us >= 25000, "max_gap_drive %u (want >= 25000)", T->max_gap_drive_us);
	CHECK(T->vbl_skips_drive >= 1, "vbl_skips_drive %llu", (unsigned long long)T->vbl_skips_drive);
	CHECK(a9x_power_off(T) == 0, "power off");
	check_no_emergency();
	teardown();
}

/* ---------------- render-ahead ring (patch 0033) ---------------- */
static struct a9_ring R;
static u8 *ring_mem;

/* frame k of a synthetic update: byte 0 of the data region pseudo-random,
 * the last one null; bytes 1..3 junk (the transport must ignore them) */
static void synth_frame(u8 *f, int k, int n)
{
	for (size_t i = 0; i < A9T_FRAME_BYTES; i++)
		f[i] = (i & 3) ? (u8)(i * 29 + k) : 0;
	if (k == n - 1)
		return;
	for (int r = A9T_HEAD_ROWS; r < A9T_TH; r++)
		for (int w = 0; w < A9T_DATA_WORDS; w++)
			f[((size_t)r * A9T_TW + w) * 4] = (u8)(((r * 7 + w * 13 + k * 5) % 3 == 0) ? (k % 255) + 1 : 0);
}

/* what the panel must get for that frame: byte 0 | template */
static u64 expect_hash(const u8 *f)
{
	static u8 *e;

	if (!e)
		e = malloc(A9T_FRAME_BYTES);
	for (size_t i = 0; i < A9T_FRAME_BYTES; i++)
		e[i] = (i & 3) ? tmpl[i] : f[i];
	return fnv(e, A9T_FRAME_BYTES);
}

struct prod { int n, slow_after; u64 frame_us, slow_us; u64 exp[256]; int produced; int err; bool never, wait_for_commit; };
static void *producer(void *arg)
{
	struct prod *p = arg;
	u8 *f = malloc(A9T_FRAME_BYTES);
	bool null;
	int err = 0;

	while (p->wait_for_commit && !READ_ONCE(late_ready_started) && !READ_ONCE(R.stop))
		mock_sleep_us(100);
	if (p->never) {		/* starts, renders nothing until stopped */
		a9r_slot(&R);	/* returns at once (space), then waits forever: emulate a stall */
		while (!READ_ONCE(R.stop))
			mock_sleep_us(100);
		a9r_finish(&R, -ECANCELED);
		free(f);
		return NULL;
	}
	for (int k = 0; k < p->n; k++) {
		u8 *slot = a9r_slot(&R);

		if (!slot) {
			err = -ECANCELED;
			break;
		}
		mock_sleep_us(k >= p->slow_after ? p->slow_us : p->frame_us);
		synth_frame(f, k, p->n);
		p->exp[k] = expect_hash(f);
		err = a9r_pack(f, slot, &null);
		if (err)
			break;
		a9r_publish(&R, null);
		p->produced = k + 1;
	}
	p->err = err;
	a9r_finish(&R, err);
	free(f);
	return NULL;
}

static int ring_run(struct prod *p, pthread_t *th, unsigned int prefill_ms)
{
	int ret;

	a9r_reset(&R);
	pthread_create(th, NULL, producer, p);
	ret = a9r_prefill(&R, T, prefill_ms, &fake_enable, 32);
	if (!ret)
		ret = a9r_drive(&R, T, &fake_enable);
	if (ret)
		a9x_blank(T);
	a9r_stop(&R);
	pthread_join(*th, NULL);
	return ret;
}

/* (17) 0033: render-ahead: every frame sent once, in order, == byte 0 | template; prefill first */
static void case_17(void)
{
	struct prod *p = calloc(1, sizeof(*p));
	pthread_t th;
	int first = -1, drives = 0, idle_pre = 0, k = 0, bad = 0;

	setup("(17) [0033] render-ahead ring: 135 frames, prefill on idle refreshes, each sent once == byte0|template");
	p->n = 135;
	p->frame_us = 2000;	/* renders faster than the 11.7 ms commits */
	p->slow_after = 1000;
	CHECK(a9x_power_on(T) == 0, "power on");
	hash_commits = true;
	commit_us = 8000;	/* + ~3 ms hashing each buffer inside the mock commit = one period */
	int c0 = ncommits;
	CHECK(ring_run(p, &th, 2000) == 0, "ring run");
	hash_commits = false;
	commit_us = 11700;
	for (int i = c0; i < ncommits && i < 512; i++) {
		if (!crecs[i].null && first < 0)
			first = i;
		if (first < 0) {
			idle_pre++;
			continue;
		}
		if (k < p->n && crecs[i].h != p->exp[k])
			bad++;
		k++;
		drives += !crecs[i].null;
	}
	fprintf(stderr, "      %d idle refreshes during prefill, %d frames sent (%d drive), %d mismatches, fill_min %u\n",
		idle_pre, k, drives, bad, R.fill_min);
	CHECK(k == p->n, "frames sent %d (want %d)", k, p->n);
	CHECK(bad == 0, "%d sent buffers != byte0|template of their frame", bad);
	CHECK(idle_pre >= 2, "prefill idle refreshes %d (power-on 2 + at least one while filling)", idle_pre);
	CHECK(R.underruns == 0, "underruns %llu", (unsigned long long)R.underruns);
	CHECK(T->front_null && !T->stall_armed, "ends on a null frame");
	CHECK(T->late_drive == 0, "late_drive %llu", (unsigned long long)T->late_drive);
	CHECK(a9x_power_off(T) == 0, "power off");
	check_no_emergency();
	teardown();
	free(p);
}

/* (18) ring component reports starvation; actual driver fault policy is case 28. */
static void case_18(void)
{
	struct prod *p = calloc(1, sizeof(*p));
	pthread_t th;
	int ret;

	setup("(18) ring component starvation returns -EAGAIN; caller policy tested in (28)");
	p->n = 135;
	p->frame_us = 2000;
	p->slow_after = 40;
	p->slow_us = 40000;
	CHECK(a9x_power_on(T) == 0, "power on");
	ret = ring_run(p, &th, 2000);
	CHECK(ret == -EAGAIN, "ret %d (want -EAGAIN)", ret);
	CHECK(R.underruns == 1, "underruns %llu", (unsigned long long)R.underruns);
	CHECK(T->front_null && !T->stall_armed, "idle frame after the underrun");
	CHECK(p->produced < p->n, "producer stopped early (%d)", p->produced);
	CHECK(a9x_power_off(T) == 0, "power off");
	check_no_emergency();
	teardown();
	free(p);
}

/* (19) the producer never delivers: prefill times out on idle refreshes only */
static void case_19(void)
{
	struct prod *p = calloc(1, sizeof(*p));
	pthread_t th;
	u64 t0;
	int ret, c0;

	setup("(19) [0033] producer stalls before the first frame: prefill -ETIMEDOUT after ~300 ms, idle only");
	p->never = true;
	CHECK(a9x_power_on(T) == 0, "power on");
	c0 = ncommits;
	t0 = mock_now_ns();
	ret = ring_run(p, &th, 300);
	CHECK(ret == -ETIMEDOUT, "ret %d", ret);
	CHECK((mock_now_ns() - t0) / 1000000 < 450, "took %llu ms", (unsigned long long)((mock_now_ns() - t0) / 1000000));
	for (int i = c0; i < ncommits && i < 512; i++)
		CHECK(crecs[i].null, "commit %d drives during prefill", i);
	CHECK(a9x_power_off(T) == 0, "power off");
	check_no_emergency();
	teardown();
	free(p);
}

/* (20) enable := 0 mid-update: -ECANCELED after the current frame, idle frame, producer stopped */
struct en_arg { int after; };
static void *disable_later(void *arg)
{
	mock_sleep_us(((struct en_arg *)arg)->after * 1000ull);
	WRITE_ONCE(fake_enable, false);
	return NULL;
}
static void case_20(void)
{
	struct prod *p = calloc(1, sizeof(*p));
	struct en_arg ea = { 150 };
	pthread_t th, dt;
	int ret;

	setup("(20) [0033] enable=0 mid-update: -ECANCELED, idle frame, producer stopped, no emergency");
	p->n = 135;
	p->frame_us = 2000;
	p->slow_after = 1000;
	CHECK(a9x_power_on(T) == 0, "power on");
	pthread_create(&dt, NULL, disable_later, &ea);
	ret = ring_run(p, &th, 2000);
	pthread_join(dt, NULL);
	CHECK(ret == -ECANCELED, "ret %d", ret);
	CHECK(T->front_null && !T->stall_armed, "idle after cancel");
	CHECK(!READ_ONCE(R.running), "producer still running");
	fake_enable = true;
	CHECK(a9x_power_off(T) == 0, "power off");
	check_no_emergency();
	teardown();
	free(p);
}

/* (21) a9r_pack: data region only, null detection, refusal outside it; show_data == show */
static void case_21(void)
{
	u8 *f = calloc(1, A9T_FRAME_BYTES), *slot = malloc(A9R_DATA), *a = malloc(A9T_FRAME_BYTES);
	bool null;

	setup("(21) [0033] a9r_pack: null / drive / refusal outside the data region; show_data buffer == show buffer");
	CHECK(a9r_pack(f, slot, &null) == 0 && null, "all-zero frame: null");
	synth_frame(f, 3, 10);
	CHECK(a9r_pack(f, slot, &null) == 0 && !null, "drive frame");
	CHECK(a9x_power_on(T) == 0, "power on");
	CHECK(a9x_show(T, f) == 0, "show");
	memcpy(a, T->map[T->front].vaddr, A9T_FRAME_BYTES);
	CHECK(a9x_show_data(T, slot) == 0, "show_data");
	CHECK(!memcmp(a, T->map[T->front].vaddr, A9T_FRAME_BYTES), "show_data buffer differs from show");
	f[(size_t)2 * A9T_TW * 4 + 8] = 1;		/* head row */
	CHECK(a9r_pack(f, slot, &null) == -EINVAL, "drive in a head row not refused");
	f[(size_t)2 * A9T_TW * 4 + 8] = 0;
	f[((size_t)100 * A9T_TW + 420) * 4] = 2;	/* word 420 */
	CHECK(a9r_pack(f, slot, &null) == -EINVAL, "drive in words 412.. not refused");
	CHECK(a9x_show(T, zero) == 0, "zero");
	CHECK(a9x_power_off(T) == 0, "power off");
	check_no_emergency();
	teardown();
	free(f);
	free(slot);
	free(a);
}

/* (22) report only: host cost of the per-frame paths (commit itself mocked as 0) */
static void case_22(void)
{
	u8 *f = malloc(A9T_FRAME_BYTES), *slot = malloc(A9R_DATA);
	bool null;
	u64 t0, pack = 0, sdata = 0, sfull = 0;
	const int n = 50;

	setup("(22) [0033] benchmark (report only): a9r_pack, show_data (compose), show (full compose)");
	CHECK(a9x_power_on(T) == 0, "power on");
	commit_us = 0;
	synth_frame(f, 3, 10);
	for (int i = 0; i < n; i++) {
		t0 = mock_now_ns(); a9r_pack(f, slot, &null); pack += mock_now_ns() - t0;
		t0 = mock_now_ns(); a9x_show_data(T, slot); sdata += mock_now_ns() - t0;
		t0 = mock_now_ns(); a9x_show(T, f); sfull += mock_now_ns() - t0;
	}
	fprintf(stderr, "      host per frame: a9r_pack %.3f ms (producer), show_data %.3f ms, show %.3f ms (consumer, incl. mock commit)\n",
		pack / 1e6 / n, sdata / 1e6 / n, sfull / 1e6 / n);
	commit_us = 11700;
	CHECK(a9x_show(T, zero) == 0, "zero");
	CHECK(a9x_power_off(T) == 0, "power off");
	teardown();
	free(f);
	free(slot);
}

/* (32) New steady frame helper is the real production a9x_flip. */
static void case_32(void)
{
	setup("(32) [0036] cold full modeset then FB-only BLOCKING flips; WW recovery and exclusions");
	CHECK(!a9x_power_on(T), "power on");
	CHECK(full_client_calls == 1 && atomic_flip_calls == 1, "powerup route full%d flip%d", full_client_calls, atomic_flip_calls);
	CHECK(T->modesets == 1 && T->flips == 1, "route counters");
	atomic_inject_deadlock = 1;
	CHECK(!a9x_show(T, zero), "flip with WW retry");
	CHECK(atomic_backoffs == 1 && !atomic_wrong, "WW retry/profile/allow_modeset contract");
	CHECK(full_client_calls == 1 && atomic_flip_calls == 2, "steady frame fell back to full modeset");
	CHECK(!master_refs && !modeset_refs, "ownership leaked");
	master_available = false;
	int n = ncommits;
	CHECK(a9x_show(T, zero) == -EBUSY && ncommits == n, "master exclusion");
	master_available = true;
	crtc_state.active = false;
	CHECK(a9x_show(T, zero) == -ENODEV && ncommits == n, "inactive CRTC refusal");
	crtc_state.active = true;
	primary_state.crtc = NULL;
	CHECK(a9x_show(T, zero) == -ENODEV && ncommits == n, "foreign primary plane refusal");
	primary_state.crtc = &crtc;
	CHECK(!master_refs && !modeset_refs, "failed flip leaked master/WW ownership");
	CHECK(!a9x_power_off(T), "poweroff");
	CHECK(atomic_load(&n_emerg) >= 1 && atomic_read(&a9x_stall_fault), "failed powered neutral was not contained");
	teardown();
}
static void case_33(void)
{
	setup("(33) [0036] successful DRM return after hard deadline stays failed; no progress resurrection");
	CHECK(!a9x_power_on(T), "power on");
	commit_us = 60000;
	CHECK(a9x_show(T, drive) == -EIO, "late success was accepted after emergency");
	check_emergency_on_time();
	u64 fired = T->stalls;
	mock_sleep_us(50000);
	CHECK(T->stalls == fired && atomic_load(&n_emerg) == 1, "late success rearmed progress after HV cut");
	int n = ncommits;
	CHECK(a9x_show(T, drive) == -EIO && ncommits == n, "further drive not refused");
	commit_us = 11700;
	CHECK(!a9x_power_off(T), "neutral shutdown after fault");
	CHECK(!T->on && !T->stall_armed, "shutdown did not stop pipeline");
	CHECK(T->stall_kind == 3 && T->stall_commit_ok && T->stall_raw_ret == 0,
	      "normal late DRM return not distinguished from a real error");
	CHECK(T->stall_snapshot.valid && T->stall_snapshot.in_commit,
	      "missing frozen in-flight snapshot");
	CHECK(T->stall_snapshot.phase < DRM_ATOMIC_PROFILE_DONE &&
	      T->profile.phase == DRM_ATOMIC_PROFILE_DONE,
	      "neutral overwrote original fault-time profile");
	teardown();
	setup("(33b) failed DPMS remains hard after a later successful shutdown");
	CHECK(!a9x_power_on(T), "power on");
	dpms_ret = -EIO;
	CHECK(a9x_power_off(T) == -EIO && T->transport_error && T->stall_kind == 2,
	      "failed DPMS was not sticky/hard");
	dpms_ret = 0;
	CHECK(!a9x_power_off(T) && T->transport_error, "later off erased hard error");
	teardown();
}
static void case_34(void)
{
	setup("(34) [0036] actual transport records flip phase maxima and closes failed profiles");
	CHECK(!a9x_power_on(T), "power on");
	commit_us = 1000;
	atomic_phase_delay[DRM_ATOMIC_PROFILE_WAIT_PREV] = 3000;
	atomic_phase_delay[DRM_ATOMIC_PROFILE_PROGRAM] = 2000;
	atomic_phase_delay[DRM_ATOMIC_PROFILE_FLIP_DONE] = 5000;
	CHECK(!a9x_show(T, zero), "profiled idle flip");
	CHECK(T->profile_valid && T->profile.phase == DRM_ATOMIC_PROFILE_DONE, "profile not completed");
	CHECK(T->phase_max_us[DRM_ATOMIC_PROFILE_WAIT_PREV] >= 3000, "prior-flush accounting");
	CHECK(T->phase_max_us[DRM_ATOMIC_PROFILE_PROGRAM] >= 2000, "program accounting");
	CHECK(T->phase_max_us[DRM_ATOMIC_PROFILE_FLIP_DONE] >= 6000, "flip-done accounting");
	u64 total = 0;
	for (unsigned int i = 0; i < DRM_ATOMIC_PROFILE_DONE; i++) {
		total += T->profile.ns[i];
		CHECK(T->phase_max_us[i] >= T->profile.ns[i] / 1000, "phase%u max", i);
	}
	CHECK(T->flip_max_us >= total / 1000 && total >= 10000000, "phase/flip total mismatch");
	u32 oldmax = T->phase_max_us[DRM_ATOMIC_PROFILE_PROGRAM];
	memset(atomic_phase_delay, 0, sizeof(atomic_phase_delay));
	next_mode[ncommits] = C_FAIL;
	CHECK(a9x_show(T, zero) == -EBUSY, "failed flip");
	CHECK(T->profile.phase == DRM_ATOMIC_PROFILE_DONE && T->phase_max_us[DRM_ATOMIC_PROFILE_PROGRAM] == oldmax,
	      "failure left profile running or erased maxima");
	CHECK(!atomic_wrong && !master_refs && !modeset_refs, "atomic ownership/transaction contract");
	commit_us = 11700;
	CHECK(!a9x_power_off(T), "power off");
	CHECK(atomic_load(&n_emerg) >= 1 && atomic_read(&a9x_stall_fault), "failed powered neutral was not contained");
	teardown();
}

/* A refusal must not be confused with a confirmed neutral or drive frame. */
static void check_scanout_refusal(int expected, const char *mutation)
{
	int n = ncommits, flips = atomic_flip_calls, front = T->front;
	u64 frames = T->frames, deadline = T->stall.expires_ns;
	ktime_t last = T->last_commit;
	int emergencies = atomic_load(&n_emerg);
	u64 began = mock_now_ns();

	CHECK(T->stall_armed && !T->front_null, "%s: missing outstanding drive", mutation);
	CHECK(a9x_show(T, drive) == expected, "%s: drive was not refused", mutation);
	CHECK(atomic_load(&n_emerg) == emergencies && !atomic_read(&a9x_stall_fault),
	      "%s: refused drive unexpectedly affected fault policy", mutation);
	CHECK(a9x_show(T, zero) == expected, "%s: neutral was not refused", mutation);
	CHECK(atomic_load(&n_emerg) == emergencies + 1 && atomic_read(&a9x_stall_fault),
	      "%s: refused neutral did not cut HV and latch fault", mutation);
	CHECK(emerg_t_ns || emergencies, "%s: missing emergency timestamp", mutation);
	CHECK(mock_now_ns() - began < 10000000, "%s: failed neutral containment delayed", mutation);
	CHECK(ncommits == n && atomic_flip_calls == flips, "%s: reached DRM commit", mutation);
	CHECK(T->stall_armed && T->stall.expires_ns == deadline,
	      "%s: refusal disarmed/rearmed progress", mutation);
	CHECK(!T->front_null && T->front == front && T->frames == frames && T->last_commit == last,
	      "%s: refusal recorded successful output", mutation);
	CHECK(!master_refs && !modeset_refs && !atomic_alloc_refs, "%s: ownership leaked", mutation);
	CHECK(T->profile.phase == DRM_ATOMIC_PROFILE_DONE, "%s: profile left active", mutation);
	/* Isolate the next mocked admission variant. This does not represent a
	 * physical retry/power-cycle or bypass the production min-off policy. */
	atomic_set(&a9x_stall_fault, 0);
	WRITE_ONCE(fake_enable, true);
}

static void case_36(void)
{
	setup("(36) [Review 17] foreign overlay and cursor refuse neutral/drive without progress changes");
	CHECK(!a9x_power_on(T), "power on");
	commit_us = 0;
	for (unsigned int foreign = 1; foreign <= 2; foreign++) {
		CHECK(!a9x_show(T, drive), "drive before foreign plane %u", foreign);
		crtc_state.plane_mask |= 1u << foreign;
		check_scanout_refusal(-EBUSY, foreign == 1 ? "overlay" : "cursor");
		crtc_state.plane_mask = drm_plane_mask(&primary);
	}
	CHECK(!a9x_power_off(T), "power off");
	CHECK(atomic_load(&n_emerg) >= 1, "expected neutral-refusal emergencies");
	teardown();
}

static void case_37(void)
{
	setup("(37) [Review 17] primary source/destination rect, rotation and owned FB admission");
	CHECK(!a9x_power_on(T), "power on");
	commit_us = 0;
	/* Either owned buffer is valid: failed atomic tail may have swapped FB
	 * state while the transport's last confirmed front remains unchanged. */
	for (int i = 0; i < 2; i++) {
		primary_state.fb = T->buf[i]->fb;
		T->front = i ^ 1;
		CHECK(!a9x_show(T, zero), "owned FB %d rejected with different confirmed front", i);
	}
	for (int mutation = 0; mutation < 12; mutation++) {
		struct drm_plane_state good;
		struct drm_framebuffer foreign_fb = { 0 };
		char label[48];

		CHECK(!a9x_show(T, drive), "drive before geometry mutation %d", mutation);
		good = primary_state;
		switch (mutation) {
		case 0: primary_state.src_x = 1; break;
		case 1: primary_state.src_y = 1; break;
		case 2: primary_state.src_w--; break;
		case 3: primary_state.src_h--; break;
		case 4: primary_state.crtc_x = 1; break;
		case 5: primary_state.crtc_y = -1; break;
		case 6: primary_state.crtc_w--; break;
		case 7: primary_state.crtc_h--; break;
		case 8: primary_state.rotation = DRM_MODE_ROTATE_90; break;
		case 9: primary_state.rotation = DRM_MODE_ROTATE_0 | (1u << 4); break;
		case 10: primary_state.fb = &foreign_fb; break;
		case 11: primary_state.fb = NULL; break;
		}
		snprintf(label, sizeof(label), "primary mutation %d", mutation);
		check_scanout_refusal(-EINVAL, label);
		primary_state = good;
	}
	CHECK(!a9x_power_off(T), "power off");
	CHECK(atomic_load(&n_emerg) >= 1, "expected neutral-refusal emergencies");
	teardown();
}

static void case_38(void)
{
	setup("(38) [Review 17] unexpected transport mode and connector/encoder routing refuse");
	CHECK(!a9x_power_on(T), "power on");
	commit_us = 0;
	for (int mutation = 0; mutation < 12; mutation++) {
		struct drm_crtc_state good_cs;
		struct drm_display_mode good_mode = tmode;
		struct drm_mode_set good_ms = msets[0];
		u32 good_encoders = connector.possible_encoders;
		char label[48];

		CHECK(!a9x_show(T, drive), "drive before mode/routing mutation %d", mutation);
		good_cs = crtc_state;
		switch (mutation) {
		case 0: crtc_state.mode.clock++; break;
		case 1: crtc_state.mode.hdisplay--; break;
		case 2: crtc_state.mode.vdisplay--; break;
		case 3: tmode.hdisplay--; crtc_state.mode = tmode; break;
		case 4: tmode.vdisplay--; crtc_state.mode = tmode; break;
		case 5: crtc_state.connector_mask = 2u; break;
		case 6: crtc_state.encoder_mask = 2u; break;
		case 7: msets[0].num_connectors = 0; break;
		case 8: msets[0].connectors = NULL; break;
		case 9: connector.possible_encoders = 3u; break;
		case 10: msets[0].x = 1; break;
		case 11: msets[0].y = 1; break;
		}
		snprintf(label, sizeof(label), "mode/routing mutation %d", mutation);
		check_scanout_refusal(-EINVAL, label);
		crtc_state = good_cs;
		tmode = good_mode;
		msets[0] = good_ms;
		connector.possible_encoders = good_encoders;
	}
	CHECK(!a9x_power_off(T), "power off");
	CHECK(atomic_load(&n_emerg) >= 1, "expected neutral-refusal emergencies");
	teardown();
}

static void case_39(void)
{
	setup("(39) [Review 17] attempted neutral with drive outstanding refuses; HV removed immediately");
	CHECK(!a9x_power_on(T), "power on");
	commit_us = 0;
	CHECK(!a9x_show(T, drive), "outstanding drive");
	crtc_state.plane_mask |= 2u;
	primary_state.rotation = DRM_MODE_ROTATE_90;
	primary_state.src_x = 1;
	u64 deadline = T->stall.expires_ns, begin = mock_now_ns();
	int n = ncommits;
	CHECK(a9x_blank(T) == -EBUSY, "contaminated neutral blank accepted");
	CHECK(T->stall_armed && T->stall.expires_ns == deadline && !T->front_null,
	      "refused blank recorded neutral or changed deadline");
	CHECK(atomic_load(&n_emerg) == 1 && emerg_t_ns < deadline, "blank refusal did not immediately contain HV");
	CHECK(!a9x_power_off(T), "power off");
	CHECK(atomic_load(&n_emerg) >= 1 && atomic_read(&a9x_stall_fault), "failed neutral not contained");
	CHECK(emerg_t_ns - begin < 10000000 && emerg_t_ns < deadline,
	      "emergency waited for timer rather than failed-neutral path");
	CHECK(ncommits == n && emerg_commit_count == n, "contaminated neutral reached scanout");
	CHECK(!T->front_null && T->stall_armed && T->stall.expires_ns == deadline,
	      "containment pretended neutral succeeded");
	CHECK(!master_refs && !modeset_refs && !atomic_alloc_refs, "ownership leaked");
	teardown();
}

#include "job-test.inc"
#include "power-test.inc"

int main(int argc, char **argv)
{
	struct timespec ts;

	verbose = argc > 1 && !strcmp(argv[1], "-v");
	clock_gettime(CLOCK_MONOTONIC, &ts);
	t0_ns = (u64)ts.tv_sec * 1000000000ull + ts.tv_nsec;
	drive = calloc(1, A9T_FRAME_BYTES);
	zero = calloc(1, A9T_FRAME_BYTES);
	/* a SYNTHETIC control template (no vendor data): byte 0 = 0, byte 1 a
	 * pattern, byte 2 = 0, byte 3 = 0xff; frames are composed onto it */
	tmpl = calloc(1, A9T_FRAME_BYTES);
	for (size_t w = 0; w < A9T_FRAME_BYTES / 4; w++) {
		tmpl[w * 4 + 1] = (u8)(w % 7 == 0 ? 0x0a : w % 3);
		tmpl[w * 4 + 3] = 0xff;
	}
	drive[0] = 1;			/* one drive code in word 0 */
	drive[(size_t)400 * A9T_TW * 4 + 100 * 4] = 2;
	if (argc > 1 && !strcmp(argv[1], "--stall-recovery")) {
		case_1(); /* between-commit expiry, then successful neutral */
		case_2(); /* in-flight commit expiry stays hard */
		case_3(); /* failed neutral stays hard */
		case_9(); /* explicit manual clear semantics retained */
		case_33(); /* raw-zero late return, frozen snapshot, neutral/off */
		goto result;
	}

	case_1();
	case_2();
	case_3();
	case_4();
	case_5();
	case_6();
	case_7();
	case_8();
	case_9();
	case_10();
	case_11();
	case_12();
	case_13();
	case_14();
	case_15();
	case_16();
	ring_mem = malloc(A9R_SLOTS * A9R_DATA);
	a9r_init(&R, ring_mem);
	case_17();
	case_18();
	case_19();
	case_20();
	case_21();
	case_22();
	case_23();
	case_24();
	case_25();
	case_26();
	case_27();
	case_28();
	case_29();
	case_30();
	case_31();
	case_32();
	case_33();
	case_34();
	case_35();
	case_36();
	case_37();
	case_38();
	case_39();
	case_40();
	case_41();
	case_42();
	case_43();
	case_44();
	case_45();
	case_46();
	case_47();
	case_48();
	case_49();
	case_50();
	case_51();
	case_52();
	case_53();
	case_54();
	case_55();
	case_56();
	case_57();
	case_58();
	case_59();
	case_60();
	case_61();
	case_62();
	case_63();
	case_64();
	case_65();
	case_66();
	case_67();
	case_68();
	case_69();
	case_70();
	case_71();
	case_72();
	case_73();
	case_74();
	case_75();
	case_79();
	case_80();
	case_81();
	case_82();
	case_83();
	case_84();
	case_76();
	case_77();
	case_78();
result:
	if (failures)
		fprintf(stderr, "FAILED: %d case(s)\n", failures);
	else
		fprintf(stderr, "all cases passed\n");
	return failures ? 1 : 0;
}
