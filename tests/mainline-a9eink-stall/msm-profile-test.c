/* Actual msm tail plus deterministic leaf timings; no display hardware. */
#include "mock.h"
#include <drm/drm_atomic_profile.h>

static u64 clock_ns;
u64 mock_now_ns(void) { return clock_ns; }
struct msm_kms;
struct msm_funcs {
	void (*enable_commit)(struct msm_kms *);
	void (*disable_commit)(struct msm_kms *);
	void (*wait_flush)(struct msm_kms *, unsigned int);
	void (*prepare_commit)(struct msm_kms *, struct drm_atomic_commit *);
	void (*flush_commit)(struct msm_kms *, unsigned int);
	void (*complete_commit)(struct msm_kms *, unsigned int);
};
struct msm_pending_timer { int work; };
struct msm_kms {
	const struct msm_funcs *funcs;
	unsigned int pending_crtc_mask;
	atomic_t fault_snapshot_capture;
	struct msm_pending_timer pending_timers[1];
};
struct msm_drm_private { struct msm_kms *kms; };
static unsigned int waits;
static void enable(struct msm_kms *k) { (void)k; clock_ns += 10; }
static void disable(struct msm_kms *k) { (void)k; clock_ns += 140; }
static void wait_flush(struct msm_kms *k, unsigned int mask)
{ (void)k; (void)mask; clock_ns += ++waits == 1 ? 30 : 110; }
static void prepare(struct msm_kms *k, struct drm_atomic_commit *s)
{ (void)k; (void)s; clock_ns += 40; }
static void flush(struct msm_kms *k, unsigned int mask)
{ (void)k; (void)mask; clock_ns += 90; }
static void complete(struct msm_kms *k, unsigned int mask)
{ (void)k; (void)mask; clock_ns += 130; }
static void lock_crtcs(struct msm_kms *k, unsigned int mask)
{ (void)k; (void)mask; clock_ns += 20; }
static void unlock_crtcs(struct msm_kms *k, unsigned int mask)
{ (void)k; (void)mask; clock_ns += 100; }
static void vblank_get(struct msm_kms *k, unsigned int mask)
{ (void)k; (void)mask; clock_ns += 80; }
static void vblank_put(struct msm_kms *k, unsigned int mask)
{ (void)k; (void)mask; clock_ns += 120; }
static unsigned int get_crtc_mask(struct drm_atomic_commit *s) { (void)s; return 1; }
static bool can_do_async(struct drm_atomic_commit *s, struct drm_crtc **c)
{ (void)s; (void)c; return false; }
static int drm_crtc_index(struct drm_crtc *c) { (void)c; return 0; }
static unsigned int drm_crtc_mask(struct drm_crtc *c) { (void)c; return 1; }
static int drm_crtc_next_vblank_start(struct drm_crtc *c, ktime_t *t)
{ (void)c; *t = 0; return 0; }
static ktime_t ms_to_ktime(int ms) { return ms * 1000000ll; }
static ktime_t ktime_sub(ktime_t a, ktime_t b) { return a - b; }
static void msm_hrtimer_queue_work(void *w, ktime_t t, int mode)
{ (void)w; (void)t; (void)mode; }
#define HRTIMER_MODE_ABS 0
#define WARN_ON(c) ((void)(c))
#define trace_msm_atomic_commit_tail_start(...) ((void)0)
#define trace_msm_atomic_wait_flush_start(...) ((void)0)
#define trace_msm_atomic_wait_flush_finish(...) ((void)0)
#define trace_msm_atomic_commit_tail_finish(...) ((void)0)
#define trace_msm_atomic_flush_commit(...) ((void)0)
static void drm_atomic_helper_commit_modeset_disables(struct drm_device *d, struct drm_atomic_commit *s)
{ (void)d; (void)s; clock_ns += 50; }
static void drm_atomic_helper_commit_planes(struct drm_device *d, struct drm_atomic_commit *s, int flags)
{ (void)d; (void)s; (void)flags; clock_ns += 60; }
static void drm_atomic_helper_commit_modeset_enables(struct drm_device *d, struct drm_atomic_commit *s)
{ (void)d; (void)s; clock_ns += 70; }
static void drm_atomic_helper_commit_hw_done(struct drm_atomic_commit *s)
{ (void)s; clock_ns += 150; }
static void drm_atomic_helper_cleanup_planes(struct drm_device *d, struct drm_atomic_commit *s)
{ (void)d; (void)s; clock_ns += 160; }
#include "msm-tail.inc"

#define REQUIRE(c) do { if (!(c)) { \
	fprintf(stderr, "msm profile FAIL line %d: %s\n", __LINE__, #c); return 1; \
} } while (0)
int main(void)
{
	static const struct msm_funcs funcs = { enable, disable, wait_flush, prepare, flush, complete };
	struct msm_kms kms = { .funcs = &funcs };
	struct msm_drm_private priv = { .kms = &kms };
	struct drm_device dev = { .dev_private = &priv };
	struct drm_atomic_commit_profile profile;
	struct drm_atomic_commit state = { .dev = &dev, .profile = &profile };

	clock_ns = 1000;
	drm_atomic_profile_init(&profile);
	msm_atomic_commit_tail(&state);
	drm_atomic_profile_enter(&profile, DRM_ATOMIC_PROFILE_DONE);
	REQUIRE(waits == 2);
	REQUIRE(profile.ns[DRM_ATOMIC_PROFILE_WAIT_PREV] == 60);
	REQUIRE(profile.ns[DRM_ATOMIC_PROFILE_PROGRAM] == 490);
	REQUIRE(profile.ns[DRM_ATOMIC_PROFILE_FLIP_DONE] == 110);
	REQUIRE(profile.ns[DRM_ATOMIC_PROFILE_CLEANUP] == 820);
	REQUIRE(!profile.ns[DRM_ATOMIC_PROFILE_PREPARE]);
	REQUIRE(!profile.ns[DRM_ATOMIC_PROFILE_FENCES]);
	REQUIRE(!profile.ns[DRM_ATOMIC_PROFILE_DEPENDENCIES]);
	REQUIRE(clock_ns == 2480);
	state.profile = NULL;
	waits = 0;
	msm_atomic_commit_tail(&state);
	REQUIRE(waits == 2 && clock_ns == 3960);
	fprintf(stderr, "PASS: actual msm tail separates prior flush, program, new flush and cleanup; NULL opt-out\n");
	return 0;
}
