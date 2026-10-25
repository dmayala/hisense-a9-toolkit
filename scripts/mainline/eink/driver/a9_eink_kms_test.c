// SPDX-License-Identifier: GPL-2.0-only
/*
 * KUnit tests of the a9_eink KMS state (patches 0032 + review 12), #included
 * from a9_eink_drv.c so they run the driver's own a9_kms_init(), checks and
 * commit helpers on a KUnit DRM device. The worker thread is not started and
 * the msm transport is never attached: a commit may set a9->kick (queued
 * work), nothing here reaches the transport or any power path.
 */
#include <kunit/test.h>
#include <drm/drm_kunit_helpers.h>
#include <drm/drm_modeset_helper.h>

static const struct drm_framebuffer_funcs a9t_fb_funcs = {
	.destroy = drm_gem_fb_destroy,
};

static void a9t_fb_put(void *fb)
{
	drm_framebuffer_put(fb);
}

static void a9t_kvfree(void *p)
{
	kvfree(p);
}

/* a shmem-backed XRGB8888 framebuffer (what the plane update vmaps) */
static struct drm_framebuffer *a9t_fb(struct kunit *test, struct a9_eink *a9, u32 w, u32 h)
{
	struct drm_mode_fb_cmd2 cmd = { .width = w, .height = h,
					.pixel_format = DRM_FORMAT_XRGB8888,
					.pitches = { w * 4 } };
	struct drm_gem_shmem_object *obj;
	struct drm_framebuffer *fb;

	obj = drm_gem_shmem_create(&a9->drm, PAGE_ALIGN((size_t)w * 4 * h));
	KUNIT_ASSERT_NOT_ERR_OR_NULL(test, obj);
	fb = kzalloc(sizeof(*fb), GFP_KERNEL);	/* freed by drm_gem_fb_destroy */
	KUNIT_ASSERT_NOT_NULL(test, fb);
	fb->obj[0] = &obj->base;
	drm_helper_mode_fill_fb_struct(&a9->drm, fb, drm_format_info(DRM_FORMAT_XRGB8888), &cmd);
	KUNIT_ASSERT_EQ(test, drm_framebuffer_init(&a9->drm, fb, &a9t_fb_funcs), 0);
	KUNIT_ASSERT_EQ(test, kunit_add_action_or_reset(test, a9t_fb_put, fb), 0);
	return fb;
}

/* as a9_remove: a real disable before the device goes (releases the
 * connector, vblank and shadow-plane references; any leak WARNs) */
static void a9t_shutdown(void *p)
{
	struct a9_eink *a9 = p;

	WRITE_ONCE(a9->kms_teardown, true);
	drm_atomic_helper_shutdown(&a9->drm);
}

static struct a9_eink *a9t_dev(struct kunit *test)
{
	struct device *dev = drm_kunit_helper_alloc_device(test);
	struct a9_eink *a9;

	KUNIT_ASSERT_NOT_ERR_OR_NULL(test, dev);
	a9 = drm_kunit_helper_alloc_drm_device_with_driver(test, dev, struct a9_eink, drm,
							   &a9_drm_driver);
	KUNIT_ASSERT_NOT_ERR_OR_NULL(test, a9);
	a9->dev = dev;
	mutex_init(&a9->shadow_lock);
	init_waitqueue_head(&a9->wq);
	a9->shadow = kvzalloc(A9T_N, GFP_KERNEL);
	KUNIT_ASSERT_NOT_NULL(test, a9->shadow);
	KUNIT_ASSERT_EQ(test, kunit_add_action_or_reset(test, a9t_kvfree, a9->shadow), 0);
	memset(a9->shadow, 0xff, A9T_N);
	KUNIT_ASSERT_EQ(test, a9_kms_init(a9), 0);
	KUNIT_ASSERT_EQ(test, kunit_add_action_or_reset(test, a9t_shutdown, a9), 0);
	return a9;
}

enum a9t_op {
	A9T_ACTIVE_FB,		/* drm_hwcomposer: ACTIVE=1 + plane FB/CRTC, no MODE_ID, no connector */
	A9T_ACTIVE_FB_ASYNC,
	A9T_ACTIVE_OFF,		/* ACTIVE=0 only */
	A9T_MODE_OFF,		/* MODE_ID=0 + ACTIVE=0 + connector CRTC_ID=0 + plane off */
	A9T_CONN_OFF,		/* connector CRTC_ID=0 only */
};

/* build, check and (commit != 0) commit one transaction; returns the result */
static int a9t_do(struct kunit *test, struct a9_eink *a9, enum a9t_op op,
		  struct drm_framebuffer *fb, bool commit)
{
	struct drm_modeset_acquire_ctx ctx;
	struct drm_atomic_commit *st;
	struct drm_crtc_state *cs;
	struct drm_plane_state *ps;
	struct drm_connector_state *conn;
	int ret;

	drm_modeset_acquire_init(&ctx, 0);
	st = drm_kunit_helper_atomic_state_alloc(test, &a9->drm, &ctx);
	KUNIT_ASSERT_NOT_ERR_OR_NULL(test, st);
	switch (op) {
	case A9T_ACTIVE_FB:
	case A9T_ACTIVE_FB_ASYNC:
		cs = drm_atomic_get_crtc_state(st, &a9->crtc);
		KUNIT_ASSERT_NOT_ERR_OR_NULL(test, cs);
		cs->active = true;
		ps = drm_atomic_get_plane_state(st, &a9->plane);
		KUNIT_ASSERT_NOT_ERR_OR_NULL(test, ps);
		KUNIT_ASSERT_EQ(test, drm_atomic_set_crtc_for_plane(ps, &a9->crtc), 0);
		drm_atomic_set_fb_for_plane(ps, fb);
		ps->crtc_x = 0;
		ps->crtc_y = 0;
		ps->crtc_w = A9E_FB_W;
		ps->crtc_h = A9E_FB_H;
		ps->src_x = 0;
		ps->src_y = 0;
		ps->src_w = (u32)fb->width << 16;
		ps->src_h = (u32)fb->height << 16;
		break;
	case A9T_ACTIVE_OFF:
		cs = drm_atomic_get_crtc_state(st, &a9->crtc);
		KUNIT_ASSERT_NOT_ERR_OR_NULL(test, cs);
		cs->active = false;
		break;
	case A9T_MODE_OFF:
		cs = drm_atomic_get_crtc_state(st, &a9->crtc);
		KUNIT_ASSERT_NOT_ERR_OR_NULL(test, cs);
		KUNIT_ASSERT_EQ(test, drm_atomic_set_mode_for_crtc(cs, NULL), 0);
		cs->active = false;
		conn = drm_atomic_get_connector_state(st, &a9->connector);
		KUNIT_ASSERT_NOT_ERR_OR_NULL(test, conn);
		KUNIT_ASSERT_EQ(test, drm_atomic_set_crtc_for_connector(conn, NULL), 0);
		ps = drm_atomic_get_plane_state(st, &a9->plane);
		KUNIT_ASSERT_NOT_ERR_OR_NULL(test, ps);
		KUNIT_ASSERT_EQ(test, drm_atomic_set_crtc_for_plane(ps, NULL), 0);
		drm_atomic_set_fb_for_plane(ps, NULL);
		break;
	case A9T_CONN_OFF:
		conn = drm_atomic_get_connector_state(st, &a9->connector);
		KUNIT_ASSERT_NOT_ERR_OR_NULL(test, conn);
		KUNIT_ASSERT_EQ(test, drm_atomic_set_crtc_for_connector(conn, NULL), 0);
		break;
	}
	ret = !commit ? drm_atomic_check_only(st) :
		op == A9T_ACTIVE_FB_ASYNC ? drm_atomic_nonblocking_commit(st) : drm_atomic_commit(st);
	drm_modeset_drop_locks(&ctx);
	drm_modeset_acquire_fini(&ctx);
	return ret;
}

/* the committed state: enabled, fixed mode with derived timings, connector routed */
static void a9t_expect_fixed(struct kunit *test, struct a9_eink *a9, bool active)
{
	struct drm_crtc_state *cs = a9->crtc.state;

	KUNIT_EXPECT_TRUE(test, cs->enable);
	KUNIT_EXPECT_EQ(test, cs->active, active);
	KUNIT_EXPECT_TRUE(test, drm_mode_equal(&cs->mode, &a9_mode));
	KUNIT_EXPECT_NOT_NULL(test, cs->mode_blob);
	KUNIT_EXPECT_EQ(test, cs->mode.crtc_clock, a9_mode.clock);
	KUNIT_EXPECT_EQ(test, cs->mode.crtc_vtotal, a9_mode.vtotal);
	KUNIT_EXPECT_EQ(test, cs->adjusted_mode.crtc_clock, a9_mode.clock);
	KUNIT_EXPECT_EQ(test, cs->adjusted_mode.crtc_htotal, a9_mode.htotal);
	KUNIT_EXPECT_PTR_EQ(test, a9->connector.state->crtc, &a9->crtc);
	KUNIT_EXPECT_EQ(test, cs->connector_mask, drm_connector_mask(&a9->connector));
}

/* after an ACTIVE commit: the virtual vblank runs at ~60 Hz (nonzero frame duration) */
static void a9t_expect_vblank(struct kunit *test, struct a9_eink *a9, s64 commit_ms)
{
	struct drm_vblank_crtc *vb = drm_crtc_vblank_crtc(&a9->crtc);
	struct drm_crtc_state *cs = a9->crtc.state;

	KUNIT_EXPECT_EQ(test, a9->crtc.mode.crtc_clock, a9_mode.clock);
	KUNIT_EXPECT_GE(test, vb->framedur_ns, 16000000);
	KUNIT_EXPECT_LE(test, vb->framedur_ns, 17500000);
	/* Check the actual flip completion. Total commit wall time also includes
	 * full-frame CPU conversion and varies wildly under loaded-host TCG. */
	KUNIT_ASSERT_NOT_NULL(test, cs->commit);
	KUNIT_EXPECT_TRUE(test, completion_done(&cs->commit->flip_done));
	KUNIT_EXPECT_GT(test, drm_crtc_vblank_count(&a9->crtc), 0u);
	kunit_info(test, "commit completed in %lld ms including input conversion", (long long)commit_ms);
}

static s64 a9t_commit_ms(struct kunit *test, struct a9_eink *a9, enum a9t_op op,
			 struct drm_framebuffer *fb, int *ret)
{
	ktime_t t0 = ktime_get();

	*ret = a9t_do(test, a9, op, fb, true);
	return ktime_ms_delta(ktime_get(), t0);
}

static void a9t_initial_state(struct kunit *test)
{
	struct a9_eink *a9 = a9t_dev(test);

	a9t_expect_fixed(test, a9, false);
	/* before any transaction: the object's own reference + the one the
	 * linked reset state owns (later, uncommitted test states hold more until
	 * the test ends; the teardown's leak WARNs check the balance) */
	KUNIT_EXPECT_EQ(test, kref_read(&a9->connector.base.refcount), 2);
	KUNIT_EXPECT_EQ(test, a9->drm.mode_config.min_height, 1);
	KUNIT_EXPECT_EQ(test, a9->drm.mode_config.max_width, 4096);
}

static void a9t_active_only_check(struct kunit *test)
{
	struct a9_eink *a9 = a9t_dev(test);

	KUNIT_EXPECT_EQ(test, a9t_do(test, a9, A9T_ACTIVE_FB, a9t_fb(test, a9, A9E_FB_W, A9E_FB_H),
				     false), 0);
	KUNIT_EXPECT_FALSE(test, READ_ONCE(a9->kick));	/* a check queues nothing */
}

/*
 * From reset, without any MODE_ID commit: ACTIVE+FB, ACTIVE=0, ACTIVE+FB,
 * MODE_ID=0, ACTIVE+FB, connector-only CRTC_ID=0 (while active), ACTIVE+FB.
 */
static void a9t_sequence(struct kunit *test)
{
	struct a9_eink *a9 = a9t_dev(test);
	struct drm_framebuffer *fb = a9t_fb(test, a9, A9E_FB_W, A9E_FB_H);
	s64 ms;
	int ret;

	ms = a9t_commit_ms(test, a9, A9T_ACTIVE_FB, fb, &ret);
	KUNIT_ASSERT_EQ(test, ret, 0);
	a9t_expect_fixed(test, a9, true);
	a9t_expect_vblank(test, a9, ms);
	KUNIT_EXPECT_TRUE(test, READ_ONCE(a9->kick));	/* black fb vs white shadow: work queued */
	KUNIT_EXPECT_FALSE(test, a9->tx.on);		/* and nothing powered (no worker, no transport) */
	WRITE_ONCE(a9->kick, false);

	KUNIT_ASSERT_EQ(test, a9t_do(test, a9, A9T_ACTIVE_OFF, NULL, true), 0);
	a9t_expect_fixed(test, a9, false);
	ms = a9t_commit_ms(test, a9, A9T_ACTIVE_FB, fb, &ret);
	KUNIT_ASSERT_EQ(test, ret, 0);
	a9t_expect_fixed(test, a9, true);
	a9t_expect_vblank(test, a9, ms);

	/* MODE_ID=0 with ACTIVE still 1 is refused by the core, as for any driver */
	KUNIT_ASSERT_EQ(test, a9t_do(test, a9, A9T_MODE_OFF, NULL, true), 0);
	a9t_expect_fixed(test, a9, false);
	ms = a9t_commit_ms(test, a9, A9T_ACTIVE_FB, fb, &ret);
	KUNIT_ASSERT_EQ(test, ret, 0);
	a9t_expect_fixed(test, a9, true);
	a9t_expect_vblank(test, a9, ms);

	KUNIT_ASSERT_EQ(test, a9t_do(test, a9, A9T_CONN_OFF, NULL, true), 0);
	a9t_expect_fixed(test, a9, false);	/* detached = inactive, link restored */
	ms = a9t_commit_ms(test, a9, A9T_ACTIVE_FB, fb, &ret);
	KUNIT_ASSERT_EQ(test, ret, 0);
	a9t_expect_fixed(test, a9, true);
	a9t_expect_vblank(test, a9, ms);
	KUNIT_EXPECT_FALSE(test, a9->tx.on);
	/* teardown (a9t_shutdown, then drm_mode_config_cleanup) WARNs on any
	 * leaked connector/vblank/vmap reference: test.log is scanned for that */
}

/* small framebuffers can be created, but never scanned on the only (full-screen) plane */
static void a9t_small_fb(struct kunit *test)
{
	struct a9_eink *a9 = a9t_dev(test);

	KUNIT_EXPECT_NE(test, a9t_do(test, a9, A9T_ACTIVE_FB, a9t_fb(test, a9, A9E_FB_W, 49), false), 0);
}

static void a9t_input_diagnostics(struct kunit *test)
{
	struct a9_eink *a9 = a9t_dev(test);
	struct drm_framebuffer *fb = a9t_fb(test, a9, A9E_FB_W, A9E_FB_H);
	char *buf = kunit_kzalloc(test, PAGE_SIZE, GFP_KERNEL);
	/* These stores use only drvdata. A second DRM mock device in the same
	 * test would collide with the helper's already-registered mock driver. */
	struct device control = { };

	dev_set_drvdata(&control, &a9->connector);

	KUNIT_ASSERT_NOT_NULL(test, buf);
	WRITE_ONCE(input_diagnostics, false);
	KUNIT_ASSERT_EQ(test, a9t_do(test, a9, A9T_ACTIVE_FB, fb, true), 0);
	KUNIT_EXPECT_EQ(test, a9->input.count, 0);
	KUNIT_EXPECT_EQ(test, a9->input.fences, 0);
	WRITE_ONCE(input_diagnostics, true);
	KUNIT_ASSERT_EQ(test, a9t_do(test, a9, A9T_ACTIVE_FB, fb, true), 0);
	KUNIT_EXPECT_EQ(test, a9->input.count, 1);
	KUNIT_EXPECT_EQ(test, a9->input.fences, 1);
	KUNIT_EXPECT_EQ(test, a9->input.pixels, (u64)A9T_N);
	KUNIT_EXPECT_EQ(test, a9->input.full, 1);
	KUNIT_EXPECT_EQ(test, a9->input.unchanged, 1);
	KUNIT_EXPECT_GT(test, a9->input.total_ns[2], 0);
	KUNIT_EXPECT_EQ(test, a9->input.total_ns[2], a9->input.max_ns[2]);
	KUNIT_EXPECT_EQ(test, mode_store(&control, NULL, "515", 3), 3);
	KUNIT_EXPECT_EQ(test, a9->input.mode_requests, 1);
	KUNIT_EXPECT_EQ(test, a9->input.requested_mode, 515);
	KUNIT_EXPECT_GT(test, a9->input.mode_ms, 0);
	KUNIT_EXPECT_EQ(test, mode_store(&control, NULL, "520", 3), -EINVAL);
	KUNIT_EXPECT_EQ(test, a9->input.mode_requests, 1);
	/* Request-only diagnostics: a dummy non-NULL thread, no worker/power. */
	a9->thread = (struct task_struct *)a9;
	KUNIT_EXPECT_EQ(test, manual_clear_store(&control, NULL, "1", 1), 1);
	a9->thread = NULL;
	KUNIT_EXPECT_EQ(test, a9->input.manual_requests, 1);
	KUNIT_EXPECT_EQ(test, a9->input.manual_mode, 515);
	KUNIT_EXPECT_GT(test, input_stats_show(&control, NULL, buf), 0);
	KUNIT_EXPECT_NOT_NULL(test, strstr(buf, "samples 1 failures 0"));
	/* Flush via the following synchronous transaction. Both commits own their
	 * profiler until cleanup, including when fence waits run on a workqueue. */
	KUNIT_ASSERT_EQ(test, a9t_do(test, a9, A9T_ACTIVE_FB_ASYNC, fb, true), 0);
	KUNIT_ASSERT_EQ(test, a9t_do(test, a9, A9T_ACTIVE_FB, fb, true), 0);
	KUNIT_EXPECT_EQ(test, a9->input.count, 3);
	KUNIT_EXPECT_EQ(test, a9->input.fences, 3);
	/* Exhaustion and off/on never reset the budgets or affect plane work. */
	atomic_set(&a9->input_reserved, A9_INPUT_SAMPLES);
	atomic_set(&a9->fence_reserved, A9_INPUT_SAMPLES);
	WRITE_ONCE(input_diagnostics, false);
	WRITE_ONCE(input_diagnostics, true);
	memset(a9->shadow, 255, A9T_N);
	KUNIT_ASSERT_EQ(test, a9t_do(test, a9, A9T_ACTIVE_FB, fb, true), 0);
	KUNIT_EXPECT_EQ(test, a9->shadow[0], 0);
	KUNIT_EXPECT_EQ(test, a9->input.count, 3);
	KUNIT_EXPECT_EQ(test, a9->input.fences, 3);
	KUNIT_EXPECT_FALSE(test, a9->tx.on);
	WRITE_ONCE(input_diagnostics, false);
}

static struct kunit_case a9t_cases[] = {
	KUNIT_CASE(a9t_initial_state),
	KUNIT_CASE(a9t_active_only_check),
	KUNIT_CASE_SLOW(a9t_sequence),
	KUNIT_CASE(a9t_small_fb),
	KUNIT_CASE_SLOW(a9t_input_diagnostics),
	{}
};

static struct kunit_suite a9t_suite = {
	.name = "a9_eink_kms",
	.test_cases = a9t_cases,
};
kunit_test_suite(a9t_suite);
