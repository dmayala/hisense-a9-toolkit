// SPDX-License-Identifier: GPL-2.0-only
/*
 * a9_eink transport: drive frames out through the msm DRM device.
 *
 * An in-kernel DRM client (the fbdev emulation's mechanism) on the msm card:
 * two 448x829 dumb buffers, one blocking atomic commit per frame. A blocking
 * msm commit returns once the hardware has latched the new buffer, so the
 * next frame is written into the other buffer while this one scans out, and
 * the commit rate is the panel's 85 Hz (INFERRED: msm wait_flush semantics;
 * measured on the phone by the "late" and "vbl_skips" counters).
 *
 * The msm card is found through the panel: the drm_panel_bridge that the
 * TC358767 driver creates for the E Ink transport panel carries the panel's
 * DT node, and once attached its ->dev is the msm drm_device.
 *
 * What the panel scans (2026-10-15, boot 12): every 32-bit word is byte 0 =
 * drive data | bytes 1..3 = the CONTROL TEMPLATE (gate clocks and source
 * strobes on the G lane, 0x00 on R, 0xff on X), which stock builds once at TCON
 * start and never overwrites. Without it the panel's gate/source drivers are
 * never clocked and nothing changes on the glass. So: both buffers hold the
 * template from attach on; a frame is composed as (frame & byte-0 lanes) |
 * template, so bytes 1..3 come from the validated template whatever the
 * frame's bytes 1..3 hold; the idle ("zero", null) frame is the template
 * itself (byte 0 = 0 everywhere). The null test looks at byte 0 only, so the
 * interlock below is unchanged. Without a template the transport never
 * powers up.
 *
 * Userspace must never become DRM master of the msm card (Android: only the
 * a9_eink card is given to the composer). If it does, our commits fail with
 * -EBUSY and the update is aborted; power-off then falls back to forcing the
 * FP9929A down (a9_eink_drv.c).
 *
 * Frame-progress policy:100ms without confirmed drive progress only logs and
 * counts. The same absolute deadline continues to500ms, when hardirq drops
 * FP9929A HV and latches the existing recoverable timing fault. A successful
 * drive commit starts a new interval; only confirmed null disarms it. A soft
 * event never changes gates, faults or recovery grants. DRM hard errors and
 * rail faults retain emergency shutdown independently of these thresholds.
 */
#include <linux/delay.h>
#include <linux/device.h>
#include <linux/hrtimer.h>
#include <linux/of.h>
#include <linux/regulator/fp9929a.h>

#include <drm/drm_atomic.h>
#include <drm/drm_auth.h>
#include <drm/drm_bridge.h>
#include <drm/drm_client.h>
#include <drm/drm_drv.h>
#include <drm/drm_framebuffer.h>
#include <drm/drm_fourcc.h>
#include <drm/drm_modes.h>
#include <drm/drm_modeset_lock.h>
#include <drm/drm_atomic_uapi.h>
#include <drm/drm_print.h>
#include <drm/drm_vblank.h>

#include "a9_eink.h"

#define TX_W A9T_TW
#define TX_H A9T_TH

static int a9x_power_off_locked(struct a9x_transport *t);
static int a9x_commit_locked(struct a9x_transport *t, int idx, bool null);

#if __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "a9x_compose assumes little-endian words (byte 0 = bits 0..7)"
#endif
#define A9X_BYTE0_LANES 0x000000ff000000ffULL	/* byte 0 of two 32-bit words */

/*
 * buffer idx := template, with byte 0 of the data region (rows 5..828,
 * words 0..411) from a ring slot (a9_eink_ring.c). Returns true if every
 * data byte is 0. The result equals a9x_compose() of the full frame the slot
 * was packed from (a9r_pack refuses drive data outside the data region).
 * t->lock held, t->tmpl set.
 */
static bool a9x_compose_data(struct a9x_transport *t, int idx, const u8 *data)
{
	u8 *dst = t->map[idx].vaddr;
	u32 acc = 0;
	int r, w;

	for (r = 0; r < TX_H; r++) {
		const u32 *c = (const u32 *)(t->tmpl + (size_t)r * TX_W * 4);
		u32 *d = (u32 *)(dst + (size_t)r * t->pitch);

		if (r < A9T_HEAD_ROWS) {
			memcpy(d, c, TX_W * 4);
			continue;
		}
		for (w = 0; w < A9T_DATA_WORDS; w++) {
			u32 v = *data++;

			acc |= v;
			d[w] = c[w] | v;	/* byte 0 = bits 0..7 (little endian) */
		}
		for (; w < TX_W; w++)
			d[w] = c[w];
	}
	return !acc;
}

/* buffer idx := the control template: the idle frame (no drive). t->lock held */
static void a9x_fill_idle(struct a9x_transport *t, int idx)
{
	u8 *d = t->map[idx].vaddr;
	int r;

	if (!t->tmpl) {		/* never powered without one (a9x_power_on) */
		memset(d, 0, (size_t)t->pitch * TX_H);
		return;
	}
	for (r = 0; r < TX_H; r++)
		memcpy(d + (size_t)r * t->pitch, t->tmpl + (size_t)r * TX_W * 4, TX_W * 4);
}

/*
 * buffer idx := byte 0 of every word of frame | bytes 1..3 of the template,
 * in one pass (u64: two words). Returns true if byte 0 is 0 everywhere (a null
 * frame: what went into the buffer drives nothing). t->lock held, t->tmpl set.
 */
static bool a9x_compose(struct a9x_transport *t, int idx, const u8 *frame)
{
	u8 *dst = t->map[idx].vaddr;
	u64 acc = 0;
	int r, i;

	for (r = 0; r < TX_H; r++) {
		const u64 *s = (const u64 *)(frame + (size_t)r * TX_W * 4);
		const u64 *c = (const u64 *)(t->tmpl + (size_t)r * TX_W * 4);
		u64 *d = (u64 *)(dst + (size_t)r * t->pitch);

		for (i = 0; i < TX_W / 2; i++) {
			u64 v = s[i] & A9X_BYTE0_LANES;

			acc |= v;
			d[i] = v | c[i];
		}
	}
	return !acc;
}

static u32 a9x_period_us(struct a9x_transport *t)
{
	return t->period_us ? t->period_us : A9X_PERIOD_DEFAULT_US;
}

/* Hard hrtimer: short raw-spin critical section; no sleeping/DRM/I2C. */
static enum hrtimer_restart a9x_stall_fn(struct hrtimer *h)
{
	struct a9x_transport *t = container_of(h, struct a9x_transport, stall);
	struct a9x_stall_snapshot snap = { 0 };
	u64 mark, now, seq;
	unsigned int i;
	unsigned long flags;
	u64 age_us;
	bool report;

	raw_spin_lock_irqsave(&t->stall_lock, flags);
	if (!READ_ONCE(t->on) || !t->stall_armed || a9x_fault_latched()) {
		raw_spin_unlock_irqrestore(&t->stall_lock, flags);
		return HRTIMER_NORESTART;
	}
	now = ktime_get_ns();
	age_us = ktime_us_delta(ns_to_ktime(now), t->stall_started);
	if (age_us < A9X_STALL_SOFT_MS * 1000u) {
		hrtimer_set_expires(h, ktime_add_ms(t->stall_started, A9X_STALL_SOFT_MS));
		raw_spin_unlock_irqrestore(&t->stall_lock, flags);
		return HRTIMER_RESTART;
	}
	if (age_us < A9X_STALL_HARD_MS * 1000u) {
		report = !t->stall_soft_reported;
		if (report) {
			t->stall_soft_reported = true;
			t->soft_stalls++;
			t->soft_last_age_us = age_us;
		}
		/* Absolute500ms from last progress, never500ms after the warning. */
		hrtimer_set_expires(h, ktime_add_ms(t->stall_started, A9X_STALL_HARD_MS));
		raw_spin_unlock_irqrestore(&t->stall_lock, flags);
		if (report)
			dev_warn_ratelimited(t->dev, "slow frame progress %llu us: soft event, HV unchanged\n", age_us);
		return HRTIMER_RESTART;
	}
	/* Only lock-free register/memory reads before cutoff: freeze the
	 * interrupted call BEFORE the fault is visible to another CPU's cleanup.
	 * No allocation, sleep, I2C or copying live DRM state. */
	now = ktime_get_ns();
	seq = READ_ONCE(t->commit_seq);
	snap.at_ns = now;
	snap.in_commit = READ_ONCE(t->commit_active);
	snap.front_null = READ_ONCE(t->front_null);
	snap.stage = READ_ONCE(t->commit_stage);
	snap.phase = READ_ONCE(t->profile.phase);
	mark = READ_ONCE(t->profile.mark_ns);
	snap.phase_age_us = now >= mark ? div_u64(now - mark, 1000) : 0;
	snap.progress_age_us = ktime_us_delta(ktime_get(), READ_ONCE(t->last_commit));
	snap.commit_age_us = snap.in_commit ? ktime_us_delta(ktime_get(), READ_ONCE(t->commit_started)) : 0;
	snap.frames = READ_ONCE(t->frames);
	for (i = 0; i < DRM_ATOMIC_PROFILE_DONE; i++)
		snap.phase_ns[i] = READ_ONCE(t->profile.ns[i]);
	/* A separate real-error latch is sticky and dominates this timing tag. */
	WRITE_ONCE(t->stall_kind, snap.in_commit ? 3 : 1);
	WRITE_ONCE(t->stall_commit_seq, seq);
	WRITE_ONCE(t->stall_commit_ok, false);
	WRITE_ONCE(t->stall_returned, false);
	WRITE_ONCE(t->stall_raw_ret, -EINPROGRESS);
	WRITE_ONCE(t->stall_return_us, 0);
	WRITE_ONCE(t->stall_at, ktime_get());
	fp9929a_starvation_off(t->stall_why);
	atomic_set(&a9x_stall_fault, 1);
	WRITE_ONCE(t->stalls, t->stalls + 1);
	/* First-event phase never changes even after later neutral/retry jobs. */
	if (!READ_ONCE(t->stall_snapshot.valid)) {
		t->stall_snapshot = snap;
		smp_store_release(&t->stall_snapshot.valid, true);
	}
	if (t->fault)
		t->fault(t);
	raw_spin_unlock_irqrestore(&t->stall_lock, flags);
	dev_crit(t->dev, "%s: FP9929A emergency off, fault latched (a9_eink.clear_fault)\n",
		 t->stall_why);
	return HRTIMER_NORESTART;
}

/* t->lock held: a drive frame is about to be committed */
static bool a9x_stall_arm(struct a9x_transport *t)
{
	unsigned long flags;

	raw_spin_lock_irqsave(&t->stall_lock, flags);
	if (a9x_fault_latched()) {
		raw_spin_unlock_irqrestore(&t->stall_lock, flags);
		return false;
	}
	t->stall_armed = true;
	t->stall_started = ktime_get();
	t->stall_soft_reported = false;
	hrtimer_start(&t->stall, ktime_add_ms(t->stall_started, A9X_STALL_SOFT_MS),
		      HRTIMER_MODE_ABS_HARD);
	raw_spin_unlock_irqrestore(&t->stall_lock, flags);
	return true;
}

/*
 * t->lock held: an all-zero frame is established. Never waits for a running
 * callback. Serialization decides whether confirmed null or hard expiry wins.
 */
static void a9x_stall_disarm(struct a9x_transport *t)
{
	unsigned long flags;

	raw_spin_lock_irqsave(&t->stall_lock, flags);
	hrtimer_try_to_cancel(&t->stall);
	t->stall_armed = false;
	raw_spin_unlock_irqrestore(&t->stall_lock, flags);
}

/*
 * Failed neutral: remove HV NOW, before any producer join or DRM shutdown.
 * No transport lock required: only hardirq-safe timer/PMIC/fault operations.
 */
void a9x_emergency_off(struct a9x_transport *t, const char *why)
{
	unsigned long flags;

	raw_spin_lock_irqsave(&t->stall_lock, flags);
	WRITE_ONCE(t->transport_error, true);
	WRITE_ONCE(t->stall_kind, 2);
	hrtimer_try_to_cancel(&t->stall);
	fp9929a_emergency_off(why);
	atomic_set(&a9x_stall_fault, 1);
	if (t->fault)
		t->fault(t);
	raw_spin_unlock_irqrestore(&t->stall_lock, flags);
	dev_crit(t->dev, "%s: FP9929A emergency off, fault latched (a9_eink.clear_fault)\n", why);
}

void a9x_init(struct a9x_transport *t)
{
	raw_spin_lock_init(&t->stall_lock);
	hrtimer_setup(&t->stall, a9x_stall_fn, CLOCK_MONOTONIC, HRTIMER_MODE_REL_HARD);
	snprintf(t->stall_why, sizeof(t->stall_why),
		 "a9_eink: no frame progress for %u ms with a drive frame on the panel",
		 A9X_STALL_HARD_MS);
}

/* teardown, after a9x_detach: an armed interlock is still a drive frame */
void a9x_fini(struct a9x_transport *t)
{
	if (hrtimer_cancel(&t->stall) && READ_ONCE(t->stall_armed))
		a9x_emergency_off(t, "a9_eink: teardown with a drive frame armed");
}

static void a9x_free_locked(struct a9x_transport *t)
{
	int i;

	for (i = 0; i < 2; i++) {
		if (!t->buf[i])
			continue;
		drm_client_buffer_vunmap(t->buf[i]);
		drm_client_buffer_delete(t->buf[i]);
		t->buf[i] = NULL;
	}
	t->ms = NULL;
	t->on = false;
	t->vbl_ref = false;
	/* nothing scans out any more (the unregister path tried the zero frame) */
	a9x_stall_disarm(t);
}

/* msm card going away: called under its clientlist_mutex */
static void a9x_client_unregister(struct drm_client_dev *client)
{
	struct a9x_transport *t = container_of(client, struct a9x_transport, client);

	mutex_lock(&t->lock);
	if (t->on) {
		/* the device is still alive here: the normal zero-frame power-down */
		dev_crit(t->dev, "msm device unregistering with the E Ink pipeline on\n");
		if (a9x_power_off_locked(t)) {
			WRITE_ONCE(t->unreg_hv_unknown, true);	/* the worker forces the FP9929A down */
			wake_up_all(t->wq);
		}
	}
	if (t->link) {
		device_link_del(t->link);
		t->link = NULL;
	}
	a9x_free_locked(t);
	t->registered = false;
	t->gone = true;
	drm_client_release(client);
	mutex_unlock(&t->lock);
}

/*
 * Backstop for system sleep (the driver's PM notifier normally has the
 * pipeline down already): before msm saves its atomic state, take the
 * CRTC down so a resume never restores it with the rails up.
 */
static int a9x_client_suspend(struct drm_client_dev *client)
{
	struct a9x_transport *t = container_of(client, struct a9x_transport, client);
	int ret;

	mutex_lock(&t->lock);
	ret = a9x_power_off_locked(t);
	mutex_unlock(&t->lock);
	if (ret)
		dev_crit(t->dev, "suspend with the E Ink pipeline on (%d)\n", ret);
	return 0;
}

static const struct drm_client_funcs a9x_client_funcs = {
	.owner = THIS_MODULE,
	.unregister = a9x_client_unregister,
	.suspend = a9x_client_suspend,
};

bool a9x_attached(struct a9x_transport *t)
{
	return READ_ONCE(t->registered) && !READ_ONCE(t->gone);
}

int a9x_attach(struct a9x_transport *t)
{
	struct drm_bridge *bridge;
	struct drm_device *ddev;
	struct drm_mode_set *ms, *found = NULL;
	int ret, i;

	mutex_lock(&t->lock);
	if (t->registered) {
		mutex_unlock(&t->lock);
		return 0;
	}
	bridge = of_drm_find_and_get_bridge(t->panel_np);
	if (!bridge) {
		mutex_unlock(&t->lock);
		return -EAGAIN;
	}
	ddev = READ_ONCE(bridge->dev);
	if (!ddev || !READ_ONCE(ddev->registered)) {
		drm_bridge_put(bridge);
		mutex_unlock(&t->lock);
		return -EAGAIN;
	}
	drm_dev_get(ddev);	/* drm_client_init needs a reference held by the caller */
	drm_bridge_put(bridge);
	ret = drm_client_init(ddev, &t->client, "a9-eink", &a9x_client_funcs);
	drm_dev_put(ddev);
	if (ret) {
		dev_err(t->dev, "drm_client_init on %s: %d\n", ddev->driver->name, ret);
		mutex_unlock(&t->lock);
		return ret;
	}

	ret = drm_client_modeset_probe(&t->client, TX_W, TX_H);
	if (ret) {
		dev_err(t->dev, "modeset probe: %d\n", ret);
		goto err_release;
	}
	mutex_lock(&t->client.modeset_mutex);
	drm_client_for_each_modeset(ms, &t->client) {
		if (ms->mode && ms->num_connectors &&
		    ms->mode->hdisplay == TX_W && ms->mode->vdisplay == TX_H) {
			found = ms;
			break;
		}
	}
	if (found) {
		/* period from the mode: htotal * vtotal / clock */
		t->period_us = div_u64((u64)found->mode->htotal * found->mode->vtotal * 1000,
				       found->mode->clock);
		snprintf(t->stall_why, sizeof(t->stall_why),
			 "a9_eink: no frame progress for %u ms with a drive frame on the panel",
			 A9X_STALL_HARD_MS);
	}
	mutex_unlock(&t->client.modeset_mutex);
	if (!found) {
		dev_err(t->dev, "no connected %ux%u connector on %s\n", TX_W, TX_H,
			ddev->driver->name);
		ret = -ENODEV;
		goto err_release;
	}

	for (i = 0; i < 2; i++) {
		t->buf[i] = drm_client_buffer_create_dumb(&t->client, TX_W, TX_H, t->fourcc);
		if (IS_ERR(t->buf[i])) {
			ret = PTR_ERR(t->buf[i]);
			t->buf[i] = NULL;
			goto err_bufs;
		}
		ret = drm_client_buffer_vmap(t->buf[i], &t->map[i]);
		if (ret) {
			drm_client_buffer_delete(t->buf[i]);
			t->buf[i] = NULL;
			goto err_bufs;
		}
		/* a9x_compose writes u64 words through the CPU mapping */
		if (t->map[i].is_iomem || t->buf[i]->fb->pitches[0] < TX_W * 4 ||
		    t->buf[i]->fb->pitches[0] % 8 || t->buf[i]->fb->pitches[0] != t->buf[0]->fb->pitches[0]) {
			dev_err(t->dev, "transport buffer %d: iomem %d pitch %u unsupported\n", i,
				t->map[i].is_iomem, t->buf[i]->fb->pitches[0]);
			ret = -EINVAL;
			goto err_bufs;
		}
		t->pitch = t->buf[i]->fb->pitches[0];
		a9x_fill_idle(t, i);
	}
	/* order shutdown/suspend: this driver before the msm device */
	t->link = device_link_add(t->dev, ddev->dev, DL_FLAG_STATELESS);
	if (!t->link)
		dev_warn(t->dev, "no device link to %s: shutdown order not guaranteed\n",
			 dev_name(ddev->dev));
	t->ms = found;
	t->front = 0;
	t->front_null = true;
	t->gone = false;
	t->registered = true;
	mutex_unlock(&t->lock);

	/* after this only the unregister callback may release the client */
	drm_client_register(&t->client);
	dev_info(t->dev, "transport on %s: %ux%u %p4cc pitch %u, frame period %u us, %s\n",
		 ddev->driver->name, TX_W, TX_H, &t->fourcc, t->pitch, t->period_us,
		 t->tmpl ? "control template in both buffers" : "NO control template");
	return 0;

err_bufs:
	dev_err(t->dev, "transport buffers: %d\n", ret);
	a9x_free_locked(t);
err_release:
	drm_client_release(&t->client);
	mutex_unlock(&t->lock);
	return ret;
}

void a9x_detach(struct a9x_transport *t)
{
	struct drm_device *ddev;

	mutex_lock(&t->lock);
	if (!t->registered) {
		mutex_unlock(&t->lock);
		return;
	}
	ddev = t->client.dev;
	drm_dev_get(ddev);
	mutex_unlock(&t->lock);

	/* same lock order as drm_client_dev_unregister() */
	mutex_lock(&ddev->clientlist_mutex);
	mutex_lock(&t->lock);
	if (t->registered) {
		list_del(&t->client.list);
		if (t->link) {
			device_link_del(t->link);
			t->link = NULL;
		}
		a9x_free_locked(t);
		t->registered = false;
		drm_client_release(&t->client);
	}
	mutex_unlock(&t->lock);
	mutex_unlock(&ddev->clientlist_mutex);
	drm_dev_put(ddev);
}

/*
 * Steady scanout needs only a new primary FB, not a client takeover of every
 * plane/connector on msm. Keep the same master exclusion, WW locks, atomic
 * validation and BLOCKING msm tail/flush wait. Never return after queueing an
 * unconfirmed flip or allow an accidental modeset during a waveform.
 * Locked current state must describe ONLY our untransformed transport. A
 * primary-only neutral FB cannot establish neutral output through foreign
 * planes, cropping or routing; refuse rather than repair live scanout.
 */
static int a9x_flip(struct a9x_transport *t, int idx)
{
	struct drm_device *dev = t->client.dev;
	struct drm_crtc *crtc = t->ms->crtc;
	struct drm_modeset_acquire_ctx ctx;
	struct drm_atomic_commit *state;
	struct drm_crtc_state *cs;
	struct drm_plane_state *ps;
	struct drm_connector *connector;
	u32 encoders;
	int ret;

	if (!drm_master_internal_acquire(dev))
		return -EBUSY;
	mutex_lock(&t->client.modeset_mutex);
	drm_modeset_acquire_init(&ctx, 0);
	state = drm_atomic_commit_alloc(dev);
	if (!state) {
		ret = -ENOMEM;
		goto out_ctx;
	}
	state->acquire_ctx = &ctx;
retry:
	state->allow_modeset = false;
	state->profile = &t->profile;
	cs = drm_atomic_get_crtc_state(state, crtc);
	if (IS_ERR(cs)) {
		ret = PTR_ERR(cs);
		goto out_state;
	}
	if (!cs->enable || !cs->active) {
		ret = -ENODEV;
		goto out_state;
	}
	/* Plane/routing masks change through the acquired CRTC state lock. */
	if (cs->plane_mask != drm_plane_mask(crtc->primary)) {
		ret = -EBUSY;
		goto out_state;
	}
	ps = drm_atomic_get_plane_state(state, crtc->primary);
	if (IS_ERR(ps)) {
		ret = PTR_ERR(ps);
		goto out_state;
	}
	if (!t->ms->mode || t->ms->x || t->ms->y ||
	    t->ms->mode->hdisplay != TX_W || t->ms->mode->vdisplay != TX_H ||
	    !drm_mode_equal(&cs->mode, t->ms->mode) ||
	    t->ms->num_connectors != 1 || !t->ms->connectors || !t->ms->connectors[0]) {
		ret = -EINVAL;
		goto out_state;
	}
	/* A9's bridge connector attaches exactly one DSI encoder. Fail closed
	 * on clones, another connector, or ambiguous/different encoder routing.
	 * possible_encoders is attachment topology, not unlocked atomic state.
	 */
	connector = t->ms->connectors[0];
	encoders = connector->possible_encoders;
	if (!encoders || (encoders & (encoders - 1)) ||
	    cs->connector_mask != drm_connector_mask(connector) ||
	    cs->encoder_mask != encoders) {
		ret = -EINVAL;
		goto out_state;
	}
	if (ps->crtc != crtc) {
		ret = -ENODEV;
		goto out_state;
	}
	/* Primary state is now locked too. Either owned FB is valid: a failed
	 * commit can have swapped state without advancing t->front.
	 */
	if ((ps->fb != t->buf[0]->fb && ps->fb != t->buf[1]->fb) ||
	    ps->src_x || ps->src_y ||
	    ps->src_w != (TX_W << 16) || ps->src_h != (TX_H << 16) ||
	    ps->crtc_x || ps->crtc_y || ps->crtc_w != TX_W || ps->crtc_h != TX_H ||
	    ps->rotation != DRM_MODE_ROTATE_0) {
		ret = -EINVAL;
		goto out_state;
	}
	drm_atomic_set_fb_for_plane(ps, t->buf[idx]->fb);
	ret = drm_atomic_commit(state);
out_state:
	if (ret == -EDEADLK) {
		drm_atomic_commit_clear(state);
		drm_modeset_backoff(&ctx);
		goto retry;
	}
	drm_atomic_commit_put(state);
out_ctx:
	drm_modeset_drop_locks(&ctx);
	drm_modeset_acquire_fini(&ctx);
	mutex_unlock(&t->client.modeset_mutex);
	drm_master_internal_release(dev);
	return ret;
}

/*
 * @null: the buffer carries no drive. A drive frame arms the interlock BEFORE
 * the commit (it may latch even if the call then fails or never returns); a
 * zero frame cancels it only once its commit succeeded.
 */
static int a9x_commit_locked(struct a9x_transport *t, int idx, bool null)
{
	struct drm_crtc *crtc;
	ktime_t now, cstart;
	u64 vbl, vbl0 = 0;
	u32 gap, cgap;
	int ret;

	if (!null && a9x_fault_latched())
		return -EIO;
	WRITE_ONCE(t->commit_seq, t->commit_seq + 1);
	WRITE_ONCE(t->commit_started, ktime_get());
	WRITE_ONCE(t->commit_stage, 1);
	WRITE_ONCE(t->commit_active, true);
	mutex_lock(&t->client.modeset_mutex);
	t->ms->fb = t->buf[idx]->fb;
	mutex_unlock(&t->client.modeset_mutex);
	/*
	 * Review 8: the deadline only moves on CONFIRMED progress. A drive
	 * commit arms the interlock if it is not armed (it may latch and then
	 * block); it never postpones an armed deadline. A successful drive
	 * commit restarts it from now; a successful null commit cancels it;
	 * a failed commit leaves the running deadline alone. So no drive frame
	 * stays on the panel longer than one deadline after the last commit
	 * that returned, or after the start of the first drive commit.
	 */
	if (!null && !t->stall_armed && !a9x_stall_arm(t)) {
		WRITE_ONCE(t->commit_active, false);
		return -EIO;
	}
	/*
	 * Review 10: the incoming buffer may latch while this call still runs
	 * (or its caller is descheduled), so a slow commit of a DRIVE frame is
	 * drive time even when the front was idle: record its start.
	 */
	cstart = ktime_get();
	if (t->on && t->vbl_ref)
		vbl0 = drm_crtc_vblank_count(t->ms->crtc);
	if (t->on) {
		unsigned int i;

		drm_atomic_profile_init(&t->profile);
		WRITE_ONCE(t->profile_valid, true);
		WRITE_ONCE(t->commit_stage, 2);
		ret = a9x_flip(t, idx);
		drm_atomic_profile_enter(&t->profile, DRM_ATOMIC_PROFILE_DONE);
		for (i = 0; i < DRM_ATOMIC_PROFILE_DONE; i++) {
			u32 us = (u32)div_u64(READ_ONCE(t->profile.ns[i]), 1000);

			if (us > t->phase_max_us[i])
				t->phase_max_us[i] = us;
		}
		cgap = (u32)ktime_us_delta(ktime_get(), cstart);
		if (cgap > t->flip_max_us)
			t->flip_max_us = cgap;
		t->flips++;
	} else {
		/* First enable needs the full connector/mode/panel power sequence. */
		WRITE_ONCE(t->commit_stage, 3);
		ret = drm_client_modeset_commit(&t->client);
		cgap = (u32)ktime_us_delta(ktime_get(), cstart);
		if (cgap > t->modeset_max_us)
			t->modeset_max_us = cgap;
		t->modesets++;
	}
	/* pacing diagnosis: does the commit itself take more than a period? */
	cgap = (u32)ktime_us_delta(ktime_get(), cstart);
	if (cgap > t->commit_max_us)
		t->commit_max_us = cgap;
	if (cgap > a9x_period_us(t) + a9x_period_us(t) / 2)
		t->commit_slow++;
	/* A real DRM error cuts HV immediately, before producer joins/cleanup.
	 * Do this BEFORE converting a late raw success to post-cutoff-EIO. */
	if (ret || msm_a9_commit_faulted()) {
		a9x_emergency_off(t, "a9_eink: DRM commit hard error");
		if (!ret)
			ret = -EIO;
	}
	/* Preserve the RAW return before the deliberate post-cutoff -EIO. The
	 * worker may use this only after neutral, off and PMIC proofs; a DPU
	 * wait error is independently boot-latched because wait_flush is void. */
	if (READ_ONCE(t->stall_kind) == 3 && !READ_ONCE(t->stall_returned) &&
	    READ_ONCE(t->stall_commit_seq) == t->commit_seq) {
		s64 elapsed = ktime_us_delta(ktime_get(), READ_ONCE(t->stall_at));

		WRITE_ONCE(t->stall_raw_ret, ret);
		WRITE_ONCE(t->stall_return_us, min_t(s64, max_t(s64, elapsed, 0), U32_MAX));
		WRITE_ONCE(t->stall_commit_ok, !ret && elapsed >= 0 && elapsed <= 1000000 &&
		           !msm_a9_commit_faulted());
		WRITE_ONCE(t->stall_returned, true);
	}
	/* A successful DRM return cannot resurrect progress after HV was cut. */
	if (!ret && !null && a9x_fault_latched())
		ret = -EIO;
	if (ret) {
		if (READ_ONCE(t->stall_kind) != 3 || !READ_ONCE(t->stall_commit_ok))
			WRITE_ONCE(t->stall_kind, 2);
		t->commits_failed++;
		/* A refused/failed neutral is no proof of neutral scanout, even
		 * if the cached front was idle. Cut HV after DRM locks are gone,
		 * before another DRM attempt or a render producer can be joined.
		 * Keep front_null/stall_armed unchanged: this is not progress.
		 */
		if (null && t->on)
			a9x_emergency_off(t, "a9_eink: neutral commit failed");
		WRITE_ONCE(t->commit_active, false);
		return ret;
	}
	if (null)
		a9x_stall_disarm(t);
	else if (!a9x_stall_arm(t)) { /* confirmed progress cannot clear a hard event */
		WRITE_ONCE(t->commit_active, false);
		return -EIO;
	}
	WRITE_ONCE(t->commit_active, false);
	now = ktime_get();
	crtc = t->ms->crtc;
	if (t->on && t->vbl_ref) {
		/*
		 * Drive time in this interval: all of it if the front (scanned
		 * since the last return) drove; else, if the incoming frame
		 * drives, the commit's own execution (it may have latched at any
		 * vblank inside it). Idle work before submitting is not drive.
		 */
		bool front_drive = !t->front_null, in_drive = !null;
		u64 skips;

		vbl = drm_crtc_vblank_count(crtc);
		skips = vbl > t->last_vbl + 1 ? vbl - t->last_vbl - 1 : 0;
		t->vbl_skips += skips;
		if (front_drive)
			t->vbl_skips_drive += skips;
		else if (in_drive && vbl > vbl0 + 1)
			t->vbl_skips_drive += vbl - vbl0 - 1;
		t->last_vbl = vbl;
		gap = (u32)ktime_us_delta(now, t->last_commit);
		cgap = front_drive ? gap : in_drive ? (u32)ktime_us_delta(now, cstart) : 0;
		if (gap > t->max_gap_us)
			t->max_gap_us = gap;
		if (cgap > t->max_gap_drive_us)
			t->max_gap_drive_us = cgap;
		if (t->period_us && gap > t->period_us + t->period_us / 2)
			t->late++;
		if (t->period_us && cgap > t->period_us + t->period_us / 2)
			t->late_drive++;
	}
	t->last_commit = now;
	t->front = idx;
	return 0;
}

int a9x_power_on(struct a9x_transport *t)
{
	int ret, i;

	mutex_lock(&t->lock);
	if (!t->registered || t->gone) {
		mutex_unlock(&t->lock);
		return -ENODEV;
	}
	if (t->on) {
		mutex_unlock(&t->lock);
		return 0;
	}
	if (!t->tmpl) {
		/* without bytes 1..3 the panel is not clocked: never drive */
		mutex_unlock(&t->lock);
		dev_err(t->dev, "no control template: transport stays off\n");
		return -ENODATA;
	}
	/* stock PanelOpen keeps the panel off for at least 200 ms */
	if (t->off_since) {
		s64 off = ktime_ms_delta(ktime_get(), t->off_since);

		if (off < 200)
			msleep(200 - off);
	}
	for (i = 0; i < 2; i++)
		a9x_fill_idle(t, i);
	t->front_null = true;
	/* full modeset with the idle (template) buffer: DSI on, panel prepare
	 * (lines, FP9929A up), bridge init; video starts before the bridge init */
	ret = a9x_commit_locked(t, 0, true);
	if (ret) {
		dev_err(t->dev, "transport enable failed: %d\n", ret);
		/* a failed enable may have left the CRTC half on: caller powers off */
		t->on = true;
		t->on_since = ktime_get();
		mutex_unlock(&t->lock);
		return ret;
	}
	t->on = true;
	t->on_since = ktime_get();
	/* one more idle refresh before any drive frame; failure: caller powers off */
	ret = a9x_commit_locked(t, 1, true);
	if (ret) {
		dev_err(t->dev, "second idle refresh failed: %d\n", ret);
		mutex_unlock(&t->lock);
		return ret;
	}
	t->vbl_ref = !drm_crtc_vblank_get(t->ms->crtc);
	if (t->vbl_ref)
		t->last_vbl = drm_crtc_vblank_count(t->ms->crtc);
	mutex_unlock(&t->lock);
	return 0;
}

/*
 * F6b (review 89d): the scanout's vblank count and time, for the PG settle
 * trace. Read-only. Under t->lock (review 89e C2): a9x_free_locked() clears
 * t->ms and vbl_ref under the same lock when msm unbinds, so the modeset is
 * never used after it is freed. The callers (the settle loop, worker thread)
 * hold no transport lock and no lock that nests inside t->lock. 0 when no
 * vblank reference is held or the modeset is gone.
 */
u64 a9x_vblank(struct a9x_transport *t, s64 *at_us)
{
	ktime_t kt = 0;
	u64 n = 0;

	mutex_lock(&t->lock);
	if (t->vbl_ref && t->ms)
		n = drm_crtc_vblank_count_and_time(t->ms->crtc, &kt);
	mutex_unlock(&t->lock);
	*at_us = ktime_to_us(kt);
	return n;
}

int a9x_show(struct a9x_transport *t, const u8 *frame)
{
	int back, ret;
	bool null;

	mutex_lock(&t->lock);
	if (!t->on || t->gone || !t->tmpl) {
		mutex_unlock(&t->lock);
		return -ENODEV;
	}
	back = t->front ^ 1;
	/* null judged on what went into the buffer */
	null = a9x_compose(t, back, frame);
	ret = a9x_commit_locked(t, back, null);
	if (!ret) {
		t->frames++;
		t->front_null = null;
	}
	mutex_unlock(&t->lock);
	return ret;
}

/* one ring slot (a9_eink_ring.c): the per-frame work is the compose only */
int a9x_show_data(struct a9x_transport *t, const u8 *data)
{
	ktime_t c0;
	int back, ret;
	bool null;
	u32 us;

	mutex_lock(&t->lock);
	if (!t->on || t->gone || !t->tmpl) {
		mutex_unlock(&t->lock);
		return -ENODEV;
	}
	back = t->front ^ 1;
	c0 = ktime_get();
	null = a9x_compose_data(t, back, data);
	us = (u32)ktime_us_delta(ktime_get(), c0);
	if (us > t->compose_max_us)
		t->compose_max_us = us;
	ret = a9x_commit_locked(t, back, null);
	if (!ret) {
		t->frames++;
		t->front_null = null;
	}
	mutex_unlock(&t->lock);
	return ret;
}

/* idle (template) frame, let it scan two refreshes, CRTC off. t->lock held. */
static int a9x_power_off_locked(struct a9x_transport *t)
{
	int ret;

	if (!t->on || t->gone)
		return 0;
	/* armed: a drive commit was started, whatever front_null says */
	if (!t->front_null || t->stall_armed) {
		/* never power down (or idle) on a frame that drives */
		int back = t->front ^ 1;

		a9x_fill_idle(t, back);
		if (!a9x_commit_locked(t, back, true))
			t->front_null = true;
		else
			t->front_null = false;	/* the drive commit may have latched */
	}
	/* no zero frame established: remove HV directly, then still DPMS off */
	if (!t->front_null || t->stall_armed)
		a9x_emergency_off(t, "a9_eink: zero frame before power-down failed");
	/* let the zero frame scan out for at least two refreshes */
	if (t->period_us) {
		s64 since = ktime_us_delta(ktime_get(), t->last_commit);

		if (since < 2 * (s64)t->period_us)
			usleep_range(2 * t->period_us - since, 2 * t->period_us - since + 500);
	}
	if (t->vbl_ref) {
		drm_crtc_vblank_put(t->ms->crtc);
		t->vbl_ref = false;
	}
	/* CRTC off: bridge disable, panel unprepare = FP9929A stock power-down */
	ret = drm_client_modeset_dpms(&t->client, DRM_MODE_DPMS_OFF);
	if (ret) {
		WRITE_ONCE(t->transport_error, true);
		WRITE_ONCE(t->stall_kind, 2);
		t->commits_failed++;
		dev_err(t->dev, "transport disable failed: %d\n", ret);
	} else {
		t->on = false;
		t->off_since = ktime_get();
	}
	return ret;
}

int a9x_power_off(struct a9x_transport *t)
{
	int ret;

	mutex_lock(&t->lock);
	ret = a9x_power_off_locked(t);
	mutex_unlock(&t->lock);
	return ret;
}

/* replace a driving frame on screen by the idle (template) frame (HV stays as is) */
int a9x_blank(struct a9x_transport *t)
{
	int back, ret = 0;

	mutex_lock(&t->lock);
	if (t->on && !t->gone && (!t->front_null || t->stall_armed)) {
		back = t->front ^ 1;
		a9x_fill_idle(t, back);
		ret = a9x_commit_locked(t, back, true);
		if (!ret)
			t->front_null = true;
	}
	mutex_unlock(&t->lock);
	return ret;
}

/* commit one idle (template) frame now, whatever is on screen (render-ahead prefill) */
int a9x_idle_refresh(struct a9x_transport *t)
{
	int back, ret = -ENODEV;

	mutex_lock(&t->lock);
	if (t->on && !t->gone) {
		back = t->front ^ 1;
		a9x_fill_idle(t, back);
		ret = a9x_commit_locked(t, back, true);
		if (!ret)
			t->front_null = true;
	}
	mutex_unlock(&t->lock);
	return ret;
}
