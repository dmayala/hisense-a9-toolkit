/* SPDX-License-Identifier: GPL-2.0-only */
/* Actual production control/worker/KMS functions, mocked kernel plumbing. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>

#include "a9_eink_pattern.h"

typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t u64;
/* 0047 instrumentation mocks. Diagnostics stay off (default) in the control
 * cases; the sampling gate is the real one: it needs input_diagnostics. */
#ifndef READ_ONCE
#define READ_ONCE(v) (v)
#endif
#define max(a, b) ((a) > (b) ? (a) : (b))
#define min(a, b) ((a) < (b) ? (a) : (b))
#define clamp(v, lo, hi) min(max(v, lo), hi)
typedef struct { int v; } atomic_t;
static bool input_diagnostics;
#define A9_INPUT_SAMPLES 4096
struct a9_input_stats {
	u64 count, pixels, full, partial, unchanged, failures;
	u64 total_ns[4], max_ns[4];
	u64 fences, fence_total_ns, fence_max_ns;
	u64 mode_requests, manual_requests, mode_ms, manual_ms;
	u32 requested_mode, manual_mode;
};
static u64 mock_ns;
static u64 ktime_get_ns(void) { return mock_ns += 1000; }
static bool a9_input_sample(atomic_t *reserved)
{
	if (!READ_ONCE(input_diagnostics) || reserved->v >= A9_INPUT_SAMPLES)
		return false;
	reserved->v++;
	return true;
}
struct mutex { int locked; };
static void mutex_lock(struct mutex *m) { assert(!m->locked); m->locked = 1; }
static void mutex_unlock(struct mutex *m) { assert(m->locked); m->locked = 0; }
#define A9_MODE_CLEAR 515
#define DRM_FORMAT_XRGB8888 1
#define DRM_FORMAT_ARGB8888 2
#define DRM_FORMAT_R8 3
#define DMA_FROM_DEVICE 0

struct drm_rect { int x1, y1, x2, y2; };
struct drm_format { u32 format; int cpp[1]; };
struct drm_framebuffer { struct drm_format *format; u32 pitches[1]; };
struct drm_shadow_plane_state { struct { bool is_iomem; void *vaddr; } data[1]; };
struct drm_plane_state {
	struct drm_framebuffer *fb;
	struct drm_rect src;
	bool visible;
	struct drm_shadow_plane_state *shadow;
};
struct drm_plane { void *dev; struct drm_plane_state *state; };
struct drm_atomic_commit { struct drm_plane_state *old; };
struct drm_crtc_state { bool active; };
struct drm_atomic_helper_damage_iter { int n; };
struct device { struct a9_eink *a9; };
struct device_attribute { int unused; };
struct a9_eink {
	struct mutex shadow_lock, eng_lock;
	struct a9_input_stats input;
	atomic_t input_reserved, fence_reserved;
	struct { struct drm_crtc_state *state; } crtc;
	int drm, wq;
	u8 *shadow;
	struct a9e_rect dirty;
	bool clear_req, manual_clear_req, kick, controls_dirty, test_pattern;
	bool dry_force_clear, force_clear, hv_fault, init_pending, dry_init_pending;
	u32 mode;
	int controls[4];
	void *thread;
	struct a9e_engine e;
	/* 0051 bounce band, 0054 damage tiles (production fields) */
	void *convert_scratch;
	u8 dirty_tm[A9E_NTILES];
	bool tm_lost, prev_pattern;
};
static struct a9_eink *to_a9(void *dev) { return dev; }
static struct a9_eink *kdev_to_a9(struct device *dev) { return dev->a9; }
static bool enable, mask_outside_damage;
static bool damage_prep, convert_bounce = true;	/* production defaults (0054, 0051) */
static bool fp_allow_power_on;
static int a9x_stall_fault;
static bool a9x_fault_latched(void) { return a9x_stall_fault != 0; }
static struct drm_rect damage;
static void wake_up(int *wq) { ++*wq; }
static bool sysfs_streq(const char *s, const char *expect)
{
	size_t len = strlen(expect);
	return strncmp(s, expect, len) == 0 &&
	       (s[len] == '\0' || (s[len] == '\n' && s[len + 1] == '\0'));
}
static int kstrtoint(const char *s, unsigned int base, int *out)
{
	char *end;
	long v;
	errno = 0;
	v = strtol(s, &end, base);
	if (errno || end == s || !(*end == '\0' ||
	    (*end == '\n' && end[1] == '\0')) || v < INT32_MIN || v > INT32_MAX)
		return -EINVAL;
	*out = v;
	return 0;
}
static struct drm_plane_state *drm_atomic_get_old_plane_state(
	struct drm_atomic_commit *s, struct drm_plane *p)
{ (void)p; return s->old; }
static struct drm_shadow_plane_state *to_drm_shadow_plane_state(struct drm_plane_state *p)
{ return p->shadow; }
static bool drm_dev_enter(int *drm, int *idx) { (void)drm; *idx = 0; return true; }
static void drm_dev_exit(int idx) { (void)idx; }
#define drm_warn_once(...) ((void)0)
static int drm_gem_fb_begin_cpu_access(struct drm_framebuffer *fb, int dir)
{ (void)fb; (void)dir; return 0; }
static void drm_gem_fb_end_cpu_access(struct drm_framebuffer *fb, int dir)
{ (void)fb; (void)dir; }
static void drm_atomic_helper_damage_iter_init(struct drm_atomic_helper_damage_iter *i,
	struct drm_plane_state *old, struct drm_plane_state *new)
{ (void)old; (void)new; i->n = 0; }
#define drm_atomic_for_each_plane_damage(iter, clip) \
	for (; (iter)->n++ == 0 && (*(clip) = damage, true); )

#include "production.inc"

static void unchanged_safety(struct a9_eink *a9, bool enabled)
{
	assert(enable == enabled);
	assert(!fp_allow_power_on);
	assert(a9x_stall_fault == 1);
	assert(a9->hv_fault);
	assert(a9->mode == 521);
}
static void full_clear(const struct a9_job *job, bool dry)
{
	assert(job->dry == dry);
	assert(job->mode == A9_MODE_CLEAR && job->clear == 1 && job->mask == 0);
	assert(job->rect.x1 == 0 && job->rect.y1 == 0);
	assert(job->rect.x2 == A9T_W && job->rect.y2 == A9T_H);
}
static ssize_t pattern_store(struct device *dev, const char *s)
{ return test_pattern_store(dev, NULL, s, strlen(s)); }

int main(void)
{
	struct a9_eink a9 = { .mode = 521, .hv_fault = true };
	struct device dev = { .a9 = &a9 };
	struct a9_job job = { 0 };
	struct drm_crtc_state crtc = { .active = true };
	struct drm_format fmt = { .format = DRM_FORMAT_R8, .cpp = { 1 } };
	struct drm_framebuffer fb = { .format = &fmt, .pitches = { A9E_FB_W } };
	struct drm_shadow_plane_state shadow = { 0 };
	struct drm_plane_state ps = { .fb = &fb, .visible = true, .shadow = &shadow };
	struct drm_plane plane = { .dev = &a9, .state = &ps };
	struct drm_atomic_commit commit = { .old = &ps };
	u8 *expected = malloc(A9T_N);
	u8 *portrait = malloc(A9T_N);
	int wake, i;

	a9.shadow = malloc(A9T_N);
	a9.e.grey = malloc(A9T_N);
	assert(a9.shadow && a9.e.grey && expected && portrait);
	memset(a9.shadow, 77, A9T_N);
	memset(a9.e.grey, 0, A9T_N);
	a9.crtc.state = &crtc;
	a9x_stall_fault = 1;
	assert(pattern_store(&dev, "contrast") == -ENODEV);
	assert(pattern_store(&dev, "off") == -ENODEV);
	assert(pattern_store(&dev, "invalid") == -EINVAL);
	assert(!a9.kick && !a9.clear_req && !a9.test_pattern && !a9.wq);
	a9.thread = &a9;
	assert(pattern_store(&dev, "contrast \n") == -EINVAL);
	assert(pattern_store(&dev, "contrast\nextra") == -EINVAL);
	assert(pattern_store(&dev, "1") == -EINVAL);
	assert(!a9.kick && !a9.test_pattern);
	puts("PASS: exact pattern parser, missing worker refusal and no queued job on error");

	assert(pattern_store(&dev, "contrast\n") == 9);
	assert(a9.test_pattern && a9.clear_req && a9.kick && a9.wq == 1);
	assert(a9_take_job(&a9, &job));
	full_clear(&job, true);
	a9e_pattern_contrast(expected);
	assert(memcmp(expected, a9.e.grey, A9T_N) == 0);
	assert(!a9.kick && !a9.clear_req && a9.test_pattern);
	assert(!a9_take_job(&a9, &job));
	unchanged_safety(&a9, false);
	assert(pattern_store(&dev, "contrast") == 8);
	assert(a9_take_job(&a9, &job));
	full_clear(&job, true);
	assert(memcmp(expected, a9.e.grey, A9T_N) == 0);
	puts("PASS: contrast and repeat queue full Clear; disabled stays dry; steady mode and faults unchanged");

	/* KMS keeps the UI shadow current but must not overwrite a held pattern. */
	shadow.data[0].vaddr = portrait;
	damage = (struct drm_rect){ 0, 0, A9E_FB_W, A9E_FB_H };
	memset(portrait, 93, A9T_N);
	wake = a9.wq;
	a9_plane_atomic_update(&plane, &commit);
	assert(!a9.kick && a9.wq == wake && !a9e_rect_empty(&a9.dirty));
	for (i = 0; i < A9T_N; i++) assert(a9.shadow[i] == 93);
	assert(memcmp(expected, a9.e.grey, A9T_N) == 0);
	/* Another Android commit, after the diagnostic job has already finished. */
	memset(portrait, 27, A9T_N);
	a9_plane_atomic_update(&plane, &commit);
	assert(!a9.kick && a9.wq == wake);
	for (i = 0; i < A9T_N; i++) assert(a9.shadow[i] == 27);
	assert(pattern_store(&dev, "off\n") == 4);
	assert(!a9.test_pattern && a9.kick && a9.clear_req && a9.wq == wake + 1);
	assert(a9_take_job(&a9, &job));
	full_clear(&job, true);
	for (i = 0; i < A9T_N; i++) assert(a9.e.grey[i] == 27);
	assert(a9e_rect_empty(&a9.dirty));
	unchanged_safety(&a9, false);
	/* Model the completed restore and operator-cleared faults for an
	 * ordinary job. The earlier assertions checked no setter cleared them.
	 */
	a9.e.have_pre = 1;
	a9.e.pre_mode = 521;
	a9.hv_fault = false;
	a9x_stall_fault = 0;
	memset(portrait, 44, A9T_N);
	wake = a9.wq;
	a9_plane_atomic_update(&plane, &commit);
	assert(a9.kick && a9.wq == wake + 1);
	assert(a9_take_job(&a9, &job));
	assert(job.mode == 521 && !job.clear && job.dry);
	for (i = 0; i < A9T_N; i++) assert(a9.e.grey[i] == 44);
	puts("PASS: KMS commits stay suppressed while held; off restores newest UI; normal KMS updates resume");

	a9.hv_fault = true;
	a9x_stall_fault = 1;
	/* A static UI must redraw when the digital transfer function changes. */
	wake = a9.wq;
	assert(a9_ctl_store(&dev, 2, "0", 1) == 1);
	assert(!a9.kick && !a9.clear_req && !a9.controls_dirty && a9.wq == wake);
	assert(a9_ctl_store(&dev, 2, "-1", 2) == -EINVAL);
	assert(a9_ctl_store(&dev, 2, "101", 3) == -EINVAL);
	assert(a9_ctl_store(&dev, 2, "bogus", 5) == -EINVAL);
	assert(a9_ctl_store(&dev, 2, "1x", 2) == -EINVAL);
	assert(!a9.kick && a9.wq == wake);
	for (i = 0; i < 3; i++) {
		assert(a9_ctl_store(&dev, i, "50\n", 3) == 3);
		assert(a9.controls[i] == 50 && a9.controls_dirty && a9.clear_req && a9.kick);
		assert(a9_take_job(&a9, &job));
		full_clear(&job, true);
		assert(!a9.controls_dirty && !a9.clear_req && !a9.kick);
		unchanged_safety(&a9, false);
		wake = a9.wq;
		assert(a9_ctl_store(&dev, i, "50", 2) == 2);
		assert(!a9.kick && !a9.clear_req && !a9.controls_dirty && a9.wq == wake);
	}
	assert(a9.e.st.g16[27] == 20); /* actual stock control curve, contrast 50 */
	puts("PASS: changed controls queue full conditioned redraw; same value and invalid writes queue nothing");

	enable = true;
	assert(pattern_store(&dev, "contrast") == 8);
	assert(a9_take_job(&a9, &job));
	full_clear(&job, false);
	unchanged_safety(&a9, true);
	assert(pattern_store(&dev, "off") == 3);
	assert(a9_take_job(&a9, &job));
	full_clear(&job, false);
	unchanged_safety(&a9, true);
	puts("PASS: enabled pattern/restore stay normal jobs; no enable, PMIC permission or fault-latch mutation");
	free(portrait);
	free(expected);
	free(a9.e.grey);
	free(a9.shadow);
	return 0;
}
