// SPDX-License-Identifier: GPL-2.0-only
/*
 * Hisense A9 (HLTE556N) E Ink display: a KMS device with an in-kernel
 * software TCON. Never run on hardware as of 2026-10-15.
 *
 *   userspace (SurfaceFlinger via drm_hwcomposer, minigbm dumb/shmem buffers)
 *      | 824x1648 portrait, XRGB/ARGB/XBGR/ABGR8888 or R8, damage clips
 *      v
 *   this card: plane atomic_update converts the damaged area to the TCON's
 *   landscape grey (stock luma + rotation) and records the pixels that changed
 *      v  (worker thread, one update at a time, newest content coalesced)
 *   a9tcon engine: mode-switch steps, conditioning, pack, waveform frames
 *   (byte-exact port of the stock composer, scripts/mainline/tcon)
 *      v
 *   transport: drm_client on the msm card, one 448x829 drive frame per
 *   85 Hz refresh -> DSI0 -> TC358767 -> E Ink source drivers
 *   (CRTC on = panel prepared = FP9929A rails up; off after each update)
 *
 * Waveform tables: request_firmware("hisense/a9-waveform.bin"), made by
 * scripts/mainline/eink/tools/a9wf-extract from the user's own composer.
 * Control template: request_firmware("hisense/a9-ctrl-template.bin"), made
 * from the same composer (scripts/mainline/eink/tools): bytes 1..3 of every
 * transport word, the panel's gate/source control signals, which stock builds
 * once and never overwrites. Accepted only with the one known SHA-256; without
 * it nothing is driven and no update (dry or real) runs. Every "zero" frame
 * (power-on/off, blank, zero_test) is the template with byte 0 = 0.
 *
 * SAFETY GATES (all default off):
 *   a9_eink.enable=1                        this driver sends frames at all;
 *                                           with 0 everything runs as a dry
 *                                           run (frames computed + hashed)
 *   panel_hisense_hlte556n_eink.enable=1    the panel may be prepared
 *   fp9929a.allow_power_on=1                the PMIC may raise the rails
 * Before the first frame the driver checks that the FP9929A really is on;
 * after every update it ends on an all-zero (no drive) frame and powers the
 * pipeline down (hv_hold_ms later, default 0). hv_max_ms caps any continuous
 * high-voltage period. If a power-down commit fails, the FP9929A is forced
 * down through its regulator.
 * Frame-progress interlock (a9_eink_transport.c): a drive frame left on the
 * glass without progress logs at100ms; at500ms, or a failed
 * zero commit at power-down, drops the FP9929A rails from hardirq context
 * (fp9929a_emergency_off), sets enable = 0 and latches a fault: updates are
 * refused until 1 is written to a9_eink.clear_fault (and fp9929a.clear_fault
 * for the PMIC). The same holds for a rail fault (patch 0039): the FP9929A
 * unavailable or hard-off at an update's start while the pipeline is on latches,
 * powers down, and the first real update after the clear is a full Clear. Real updates also run only inside the temperature-bin
 * window a9_eink.bin_min..bin_max (default 0..13), checked before power-up
 * and again, with the fresh samples, before the first frame.
 *
 * Userspace control ABI (docs/mainline-eink-compat.md): the connector's
 * "eink" sysfs group: mode (513/515/518/521, default 518), black_threshold,
 * white_threshold, contrast, manual_clear, force_clear. Debug: debugfs a9_eink/.
 */
#include <linux/debugfs.h>
#include <linux/delay.h>
#include <linux/dma-mapping.h>
#include <linux/firmware.h>
#include <linux/hex.h>
#include <linux/iio/consumer.h>
#include <linux/kthread.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/suspend.h>
#include <linux/regulator/consumer.h>
#include <linux/regulator/fp9929a.h>
#include <linux/seq_file.h>
#include <linux/slab.h>
#include <linux/sysfs.h>
#include <linux/vmalloc.h>

#include <crypto/sha2.h>

#include <drm/drm_atomic.h>
#include <drm/drm_atomic_helper.h>
#include <drm/drm_atomic_uapi.h>
#include <drm/drm_damage_helper.h>
#include <drm/drm_drv.h>
#include <drm/drm_fourcc.h>
#include <drm/drm_framebuffer.h>
#include <drm/drm_gem_atomic_helper.h>
#include <drm/drm_gem_framebuffer_helper.h>
#include <drm/drm_gem_shmem_helper.h>
#include <drm/drm_managed.h>
#include <drm/drm_modes.h>
#include <drm/drm_print.h>
#include <drm/drm_probe_helper.h>
#include <drm/drm_vblank.h>
#include <drm/drm_vblank_helper.h>

#include "a9_eink.h"
#include "a9_eink_pattern.h"

static bool enable;
module_param(enable, bool, 0644);
MODULE_PARM_DESC(enable, "Send drive frames to the E Ink panel (default: no = dry run)");

static bool clear_shadow = true;
module_param(clear_shadow, bool, 0444);
MODULE_PARM_DESC(clear_shadow, "Initial Clear UI shadow curve (near-black text -> level 0); 0 = stock-compatible Clear bytes. Runtime: eink/clear_shadow");

static bool damage_prep;
module_param(damage_prep, bool, 0644);
MODULE_PARM_DESC(damage_prep, "Damage-limited prepare: condition, diff and pack only the tiles whose grey changed (bit-identical engine state, tests/damage-equiv). 0 = the reference full pass (default). Runtime");

/*
 * eink-latency r2, cold start (runs/eink-latency-r2.md §next c). Default OFF; HV
 * policy: attended review before any session enables it. 1 = a touch-down
 * (BTN_TOUCH 1 or ABS_MT_TRACKING_ID >= 0) while the pipeline is off starts the
 * normal power-up (same gates, bin pre-filter, PG and rail/temperature checks as
 * the first update would do) so that it overlaps the app's rendering. The
 * session is then held exactly like after an update (hv_hold_ms, hv_max_ms, the
 * FP9929A max_on_ms/min_off_ms untouched); a prewarm that sees no output before
 * the hold expires blocks further prewarms for prewarm_gap_ms.
 */
static unsigned int prewarm;
module_param(prewarm, uint, 0644);
MODULE_PARM_DESC(prewarm, "Cold-start prewarm: 0 = off (default), 1 = a touch-down while the pipeline is off powers it up ahead of the first update (bounded by hv_hold_ms; see prewarm_gap_ms). HV policy: attended review first");
static unsigned int prewarm_gap_ms = 10000;
module_param(prewarm_gap_ms, uint, 0644);
MODULE_PARM_DESC(prewarm_gap_ms, "After a prewarm that saw no update, no new prewarm for this long (default 10000, clamped 3000..60000)");

static bool skip_same_target;
module_param(skip_same_target, bool, 0644);
MODULE_PARM_DESC(skip_same_target, "Stock-style no-change skip (composer 0x1a6ee4): a steady job whose grey AND starting levels equal those of the last completed update is dropped before its prepare (it would be prepared as unchanged and send nothing). Default N (review 89: default-on is a separate decision after its A/B) = always prepare, the 0054 behaviour. Runtime");

static bool convert_bounce = true;
module_param(convert_bounce, bool, 0644);
MODULE_PARM_DESC(convert_bounce, "Convert the plane through a cached bounce band read in storage order (same output as the direct per-pixel path; the source is write-combined). 0 = direct path, for A/B. Runtime");

static bool input_diagnostics;
module_param(input_diagnostics, bool, 0600);
MODULE_PARM_DESC(input_diagnostics, "Opt-in numeric input stats; first 4096 samples per device, no reset");

/* Called under shadow_lock, only on accepted userspace requests. */
static void a9_input_request(struct a9_eink *a9, bool manual)
{
	struct a9_input_stats *d = &a9->input;

	if (!READ_ONCE(input_diagnostics))
		return;
	if (manual && d->manual_requests < A9_INPUT_SAMPLES) {
		d->manual_requests++;
		d->manual_ms = ktime_to_ms(ktime_get());
		d->manual_mode = a9->mode;
	} else if (!manual && d->mode_requests < A9_INPUT_SAMPLES) {
		d->mode_requests++;
		d->mode_ms = ktime_to_ms(ktime_get());
		d->requested_mode = a9->mode;
	}
}

/* Reservation prevents concurrent commits from exceeding the fixed budget. */
static bool a9_input_sample(atomic_t *reserved)
{
	return READ_ONCE(input_diagnostics) &&
		atomic_add_unless(reserved, 1, A9_INPUT_SAMPLES);
}

static unsigned int hv_hold_ms = 3000;
module_param(hv_hold_ms, uint, 0644);
MODULE_PARM_DESC(hv_hold_ms, "Power the pipeline (HV) down this long after the last update; stock: 2-3 s (default 3000, 0 = right after each update)");

static unsigned int hv_hold_interactive_ms;
module_param(hv_hold_interactive_ms, uint, 0644);
MODULE_PARM_DESC(hv_hold_interactive_ms, "Idle hold while interacting (a real update ended less than hv_interactive_window_ms after the previous real update); never below hv_hold_ms, clamped to 10000, 0 = off (default 0, opt-in)");

static unsigned int hv_interactive_window_ms = 10000;
module_param(hv_interactive_window_ms, uint, 0644);
MODULE_PARM_DESC(hv_interactive_window_ms, "Two real updates this close together count as interaction (default 10000)");

/* Stock PG is telemetry, never an enable/refresh predicate. */
static unsigned int pg_dip_policy = 1;
module_param(pg_dip_policy, uint, 0444);
MODULE_PARM_DESC(pg_dip_policy, "Read-only policy ID: 1 = stock PG telemetry only; no wait, dip quota or rate latch");

/* F6: at most this many VCOM readbacks per boot (log-only diagnostic) */
#define A9_VCOM_READS_MAX 64

static unsigned int hv_max_ms = 60000;
module_param(hv_max_ms, uint, 0644);
MODULE_PARM_DESC(hv_max_ms, "Power down between updates after this much continuous HV-on time; stock has no cap, 33 s seen (default 60000)");

/* Initial admission credit, independent of the fixed 32-slot capacity.
 * Review 19 selects twelve slots for supervised qualification; this is not
 * a throughput or scheduling guarantee. Empty-ring failure still immediately
 * aborts the whole trial.
 */
static unsigned int prefill_frames = 12;

static int a9_prefill_set(const char *val, const struct kernel_param *kp)
{
	unsigned int v;
	int ret = kstrtouint(val, 0, &v);

	if (ret)
		return ret;
	if (v < 2 || v > A9R_SLOTS)
		return -EINVAL;
	WRITE_ONCE(*(unsigned int *)kp->arg, v);
	return 0;
}

static const struct kernel_param_ops a9_prefill_ops = {
	.set = a9_prefill_set,
	.get = param_get_uint,
};
module_param_cb(prefill_frames, &a9_prefill_ops, &prefill_frames, 0644);
MODULE_PARM_DESC(prefill_frames, "Initial ready-slot watermark (2..32, default 12); snapshotted per posted job; capacity remains 32");

/*
 * Stage E3 of docs/mainline-eink-steps.md: with zero_test = N > 0 and
 * enable = 1, an update powers the pipeline up exactly as a real one (HV and
 * PG verified first), commits N all-zero (no drive) frames, and changes no
 * engine state: the glass must not change. Then the normal hold/power-down.
 */
static unsigned int zero_test;
module_param(zero_test, uint, 0644);
MODULE_PARM_DESC(zero_test, "E3 test: send this many all-zero frames per update instead of the waveform (default 0 = off)");

static bool init_flash = true;
module_param(init_flash, bool, 0644);
MODULE_PARM_DESC(init_flash, "Run the stock one-time init flash before the first update (default: yes)");

static bool trace;
module_param(trace, bool, 0644);
MODULE_PARM_DESC(trace, "SHA-256 every update's frames and keep the inputs in debugfs (always on in dry run)");

/*
 * XR24 only: stock's BA24 puts byte 0 (data) on the B lane and byte 1
 * (control) on G, which is XRGB8888's layout (MEASURED 2026-10-15); XB24
 * would move the data to the R lane. Kept as a read-only parameter for the
 * log line and the sequencer's read-back.
 */
static char *transport_format = "XR24";
module_param(transport_format, charp, 0444);
MODULE_PARM_DESC(transport_format, "fourcc of the 448x829 transport buffers: XR24 only (MEASURED lane mapping)");

/*
 * Diagnosis (0034): one kmsg line per real update (start parameters, frames
 * SENT, drive lateness, result), so a photo of the glass can be matched to
 * the update that left it (Android run 8: inverted-looking glass).
 */
static bool log_updates;
module_param(log_updates, bool, 0644);
MODULE_PARM_DESC(log_updates, "Log one line per real update: mode, steps, bin, rect, frames sent, late_drive, result (default no)");

static int temp_fallback = 28;
module_param(temp_fallback, int, 0644);
MODULE_PARM_DESC(temp_fallback, "Temperature (deg C) used when a sensor gives nothing; the stock composer starts with 28 (default 28)");

static int flag_6c7 = 1;
module_param(flag_6c7, int, 0644);
MODULE_PARM_DESC(flag_6c7, "Stock engine byte this+0x6c7 (0 adds 0x100 to the mode word); default 1");

static bool mask_outside_damage;
module_param(mask_outside_damage, bool, 0644);
MODULE_PARM_DESC(mask_outside_damage, "Partial updates drive nothing outside the damage (not stock; default no)");

static bool any_mode;
module_param(any_mode, bool, 0644);
MODULE_PARM_DESC(any_mode, "Testing: accept any stock mode word in sysfs mode, not only 513/515/518/521");

#define A9_FW_NAME "hisense/a9-waveform.bin"
#define A9_CT_NAME "hisense/a9-ctrl-template.bin"
#define A9_MODE_DEFAULT 518	/* Smooth, the compat contract default */
#define A9_MODE_CLEAR 0x203	/* force_clear: Clear + clear flag = type-2 table */
#define A9_DRAIN_MS 15000
/*
 * Lowest panel temperature this driver drives at. The FP9929A reports a
 * signed TMST (datasheet: 0xf6 = -10 C) and the PMI632 thermistor can read
 * below zero; the composer's temperature bins start at 0 C and the panel's
 * cold rating is unknown, so below this nothing is driven (no clamp to 0).
 */
#define A9_TEMP_MIN_C 0

static inline struct a9_eink *to_a9(struct drm_device *d)
{
	return container_of(d, struct a9_eink, drm);
}

/* ---- front side: KMS ------------------------------------------------------- */
static const u32 a9_formats[] = {
	DRM_FORMAT_XRGB8888, DRM_FORMAT_ARGB8888,
	DRM_FORMAT_XBGR8888, DRM_FORMAT_ABGR8888,
	DRM_FORMAT_R8,
};

/* nominal 60 Hz: paces the vblank timer that SurfaceFlinger's vsync follows */
static const struct drm_display_mode a9_mode = {
	DRM_MODE_INIT(60, A9E_FB_W, A9E_FB_H, 76, 152),
};

static enum a9e_fmt a9_fmt(u32 fourcc)
{
	switch (fourcc) {
	case DRM_FORMAT_XRGB8888:
	case DRM_FORMAT_ARGB8888:
		return A9E_FMT_BGRX;
	case DRM_FORMAT_R8:
		return A9E_FMT_R8;
	default:
		return A9E_FMT_RGBX;
	}
}

static int a9_plane_atomic_check(struct drm_plane *plane, struct drm_atomic_commit *state)
{
	struct drm_plane_state *ps = drm_atomic_get_new_plane_state(state, plane);
	struct a9_eink *a9 = to_a9(plane->dev);
	struct drm_crtc_state *cs = drm_atomic_get_new_crtc_state(state, &a9->crtc);

	return drm_atomic_helper_check_plane_state(ps, cs, DRM_PLANE_NO_SCALING,
						   DRM_PLANE_NO_SCALING, false, false);
}

static void a9_plane_atomic_update(struct drm_plane *plane, struct drm_atomic_commit *state)
{
	struct drm_plane_state *old = drm_atomic_get_old_plane_state(state, plane);
	struct drm_plane_state *ps = plane->state;
	struct drm_shadow_plane_state *sps = to_drm_shadow_plane_state(ps);
	struct a9_eink *a9 = to_a9(plane->dev);
	struct drm_framebuffer *fb = ps->fb;
	struct drm_atomic_helper_damage_iter iter;
	struct drm_rect clip;
	struct a9e_rect changed = { 0, 0, 0, 0 };
	enum a9e_fmt fmt;
	const u8 *src;
	int sx, sy, idx;
	bool measure, full = false;
	u64 mark = 0, ns[4] = { 0 }, pixels = 0;
	int access;

	if (!fb || !a9->crtc.state->active || !ps->visible)
		return;
	if (sps->data[0].is_iomem) {
		drm_warn_once(&a9->drm, "I/O memory framebuffers are not supported\n");
		return;
	}
	if (!drm_dev_enter(&a9->drm, &idx))
		return;
	fmt = a9_fmt(fb->format->format);
	sx = ps->src.x1 >> 16;
	sy = ps->src.y1 >> 16;
	/* data[0] already includes fb->offsets[0] (drm_gem_fb_vmap) */
	src = (const u8 *)sps->data[0].vaddr +
	      (size_t)sy * fb->pitches[0] + (size_t)sx * fb->format->cpp[0];

	measure = a9_input_sample(&a9->input_reserved);
	if (measure)
		mark = ktime_get_ns();
	access = drm_gem_fb_begin_cpu_access(fb, DMA_FROM_DEVICE);
	if (measure) {
		ns[0] = ktime_get_ns() - mark;
		mark = ktime_get_ns();
	}
	if (access) {
		if (measure) {
			mutex_lock(&a9->shadow_lock);
			a9->input.failures++;
			mutex_unlock(&a9->shadow_lock);
		}
		goto out;
	}
	mutex_lock(&a9->shadow_lock);
	if (measure) {
		ns[1] = ktime_get_ns() - mark;
		mark = ktime_get_ns();
	}
	drm_atomic_helper_damage_iter_init(&iter, old, ps);
	drm_atomic_for_each_plane_damage(&iter, &clip) {
		if (measure) {
			pixels += (u64)(clamp(clip.x2 - sx, 0, A9E_FB_W) -
				clamp(clip.x1 - sx, 0, A9E_FB_W)) *
				(clamp(clip.y2 - sy, 0, A9E_FB_H) -
				 clamp(clip.y1 - sy, 0, A9E_FB_H));
			full |= clip.x1 <= sx && clip.y1 <= sy &&
				clip.x2 >= sx + A9E_FB_W && clip.y2 >= sy + A9E_FB_H;
		}
		/* Called under shadow_lock, which also serializes convert_scratch.
		 * No scratch (allocation failed at probe) or the param off: direct path. */
		if (READ_ONCE(damage_prep))
			a9e_fb_to_grey_tiles(a9->shadow, src, fb->pitches[0], fmt,
					     clip.x1 - sx, clip.y1 - sy, clip.x2 - sx, clip.y2 - sy,
					     &changed, a9->dirty_tm,
					     READ_ONCE(convert_bounce) ? a9->convert_scratch : NULL);
		else {
			/* changes made while untracked: the next job falls back to the full pass */
			a9->tm_lost = true;
			a9e_fb_to_grey_bounce(a9->shadow, src, fb->pitches[0], fmt,
					      clip.x1 - sx, clip.y1 - sy, clip.x2 - sx, clip.y2 - sy, &changed,
					      READ_ONCE(convert_bounce) ? a9->convert_scratch : NULL);
		}
	}
	if (measure)
		ns[2] = ktime_get_ns() - mark;
	if (!a9e_rect_empty(&changed)) {
		a9e_rect_union(&a9->dirty, &changed);
		/* Keep the newest UI for restore without overwriting the test. */
		if (!a9->test_pattern) {
			a9->kick = true;
			wake_up(&a9->wq);
		}
	}
	mutex_unlock(&a9->shadow_lock);
	if (measure)
		mark = ktime_get_ns();
	drm_gem_fb_end_cpu_access(fb, DMA_FROM_DEVICE);
	if (measure) {
		int i;
		struct a9_input_stats *d = &a9->input;

		ns[3] = ktime_get_ns() - mark;
		mutex_lock(&a9->shadow_lock);
		d->count++;
		d->pixels += pixels; /* includes overlaps, actual converted work */
		if (full)
			d->full++;
		else
			d->partial++;
		d->unchanged += a9e_rect_empty(&changed);
		for (i = 0; i < 4; i++) {
			d->total_ns[i] += ns[i];
			d->max_ns[i] = max(d->max_ns[i], ns[i]);
		}
		mutex_unlock(&a9->shadow_lock);
	}
out:
	drm_dev_exit(idx);
}

static const struct drm_plane_helper_funcs a9_plane_helper_funcs = {
	DRM_GEM_SHADOW_PLANE_HELPER_FUNCS,
	.atomic_check = a9_plane_atomic_check,
	.atomic_update = a9_plane_atomic_update,
};

static const struct drm_plane_funcs a9_plane_funcs = {
	.update_plane = drm_atomic_helper_update_plane,
	.disable_plane = drm_atomic_helper_disable_plane,
	.destroy = drm_plane_cleanup,
	DRM_GEM_SHADOW_PLANE_FUNCS,
};

static enum drm_mode_status a9_crtc_mode_valid(struct drm_crtc *crtc,
					       const struct drm_display_mode *mode)
{
	return drm_crtc_helper_mode_valid_fixed(crtc, mode, &a9_mode);
}

static int a9_crtc_atomic_check(struct drm_crtc *crtc, struct drm_atomic_commit *state)
{
	struct drm_crtc_state *cs = drm_atomic_get_new_crtc_state(state, crtc);
	int ret;

	/* enabled-but-inactive is this CRTC's resting state (fixed mode, below) */
	if (!cs->active)
		return 0;
	ret = drm_atomic_helper_check_crtc_primary_plane(cs);
	if (ret)
		return ret;
	return drm_atomic_add_affected_planes(state, crtc);
}

/*
 * Display on/off of THIS card (screen off, doze) changes nothing on the
 * glass: the last image stays, queued work is finished by the worker and the
 * rails go down after it as usual. No clear, no init flash on disable.
 */
static const struct drm_crtc_helper_funcs a9_crtc_helper_funcs = {
	.mode_valid = a9_crtc_mode_valid,
	.atomic_check = a9_crtc_atomic_check,
	DRM_CRTC_HELPER_VBLANK_FUNCS,
};

/*
 * Fixed single-mode CRTC (patch 0032, mainline Android run 6): the composer
 * (android16-qpr2 drm_hwcomposer) believes the one mode is already set and
 * commits ACTIVE=1 + FB without MODE_ID or a connector link, which the DRM
 * core refuses ("active without enabled"). So this CRTC is ENABLED with
 * a9_mode and linked to its one connector from reset on, and a commit that
 * would disable it (MODE_ID 0 / connector CRTC_ID 0) is turned into
 * "inactive, mode and link kept" (a9_mode_config_check). Enabled/active of
 * THIS virtual card has no power meaning: HV comes up only when the worker
 * runs a real update (changed pixels, a9_eink.enable and both power gates
 * open, the same caps, interlock and bin window as before).
 */
/*
 * The fixed mode as a userspace MODE_ID would give it (drm_mode_convert_umode
 * runs drm_mode_set_crtcinfo): with the derived crtc_* timings, which the
 * virtual vblank timer and the timestamping constants use (review 12: a raw
 * DRM_MODE_INIT copy has crtc_clock 0, so the timer would never run).
 */
static void a9_fixed_mode(struct drm_display_mode *m)
{
	drm_mode_copy(m, &a9_mode);
	drm_mode_set_crtcinfo(m, 0);
}

/* cs := enabled with the prepared fixed mode (mode and adjusted_mode), inactive */
static int a9_set_fixed_mode(struct drm_crtc_state *cs)
{
	struct drm_display_mode m;
	int ret;

	a9_fixed_mode(&m);
	ret = drm_atomic_set_mode_for_crtc(cs, &m);
	if (ret)
		return ret;
	drm_mode_copy(&cs->adjusted_mode, &m);
	cs->enable = true;
	cs->active = false;
	return 0;
}

static void a9_crtc_reset(struct drm_crtc *crtc)
{
	drm_atomic_helper_crtc_reset(crtc);
	if (crtc->state)
		a9_set_fixed_mode(crtc->state);	/* -ENOMEM: stays disabled */
}

static const struct drm_crtc_funcs a9_crtc_funcs = {
	.reset = a9_crtc_reset,
	.destroy = drm_crtc_cleanup,
	.set_config = drm_atomic_helper_set_config,
	.page_flip = drm_atomic_helper_page_flip,
	.atomic_duplicate_state = drm_atomic_helper_crtc_duplicate_state,
	.atomic_destroy_state = drm_atomic_helper_crtc_destroy_state,
	DRM_CRTC_VBLANK_TIMER_FUNCS,
};

static const struct drm_encoder_funcs a9_encoder_funcs = {
	.destroy = drm_encoder_cleanup,
};

static int a9_connector_get_modes(struct drm_connector *connector)
{
	return drm_connector_helper_get_modes_fixed(connector, &a9_mode);
}

static const struct drm_connector_helper_funcs a9_connector_hfuncs = {
	.get_modes = a9_connector_get_modes,
};

static const struct attribute_group a9_eink_group;

static int a9_connector_late_register(struct drm_connector *connector)
{
	return sysfs_create_group(&connector->kdev->kobj, &a9_eink_group);
}

static void a9_connector_early_unregister(struct drm_connector *connector)
{
	sysfs_remove_group(&connector->kdev->kobj, &a9_eink_group);
}

static const struct drm_connector_funcs a9_connector_funcs = {
	.reset = drm_atomic_helper_connector_reset,
	.fill_modes = drm_helper_probe_single_connector_modes,
	.destroy = drm_connector_cleanup,
	.atomic_duplicate_state = drm_atomic_helper_connector_duplicate_state,
	.atomic_destroy_state = drm_atomic_helper_connector_destroy_state,
	.late_register = a9_connector_late_register,
	.early_unregister = a9_connector_early_unregister,
};

/*
 * Keep the fixed mode and the connector link through every form of "off":
 * MODE_ID 0 (CRTC disabled) and a connector-only detach (connector CRTC_ID
 * 0, review 12) both become "inactive, mode and link kept". ACTIVE=0 alone
 * needs nothing. Runs after the core's own checks (a MODE_ID 0 with ACTIVE
 * still 1 is refused there, as for any driver). Errors (-EDEADLK included)
 * are returned for the caller's backoff, as atomic checks do.
 */
static int a9_keep_fixed_mode(struct a9_eink *a9, struct drm_atomic_commit *state)
{
	struct drm_crtc_state *cs = drm_atomic_get_new_crtc_state(state, &a9->crtc);
	struct drm_connector_state *conn = drm_atomic_get_new_connector_state(state, &a9->connector);
	bool detached = conn && conn->crtc != &a9->crtc;
	int ret;

	/* driver removal: let drm_atomic_helper_shutdown unlink everything, so
	 * the connector reference of the linked state is released */
	if (READ_ONCE(a9->kms_teardown))
		return 0;
	if (!detached && (!cs || cs->enable))
		return 0;
	if (!cs) {
		cs = drm_atomic_get_crtc_state(state, &a9->crtc);
		if (IS_ERR(cs))
			return PTR_ERR(cs);
	}
	ret = a9_set_fixed_mode(cs);
	if (ret)
		return ret;
	if (!conn) {
		conn = drm_atomic_get_connector_state(state, &a9->connector);
		if (IS_ERR(conn))
			return PTR_ERR(conn);
	}
	/* takes the connector reference a linked state owns, sets connector_mask */
	return drm_atomic_set_crtc_for_connector(conn, &a9->crtc);
}

static int a9_mode_config_check(struct drm_device *dev, struct drm_atomic_commit *state)
{
	int ret = a9_keep_fixed_mode(to_a9(dev), state);

	return ret ? ret : drm_atomic_helper_check(dev, state);
}

/* after drm_mode_config_reset(): the connector's reset state routed to the CRTC */
static void a9_fixed_route(struct a9_eink *a9)
{
	struct drm_connector_state *conn = a9->connector.state;
	struct drm_crtc_state *cs = a9->crtc.state;

	if (!conn || !cs || conn->crtc)
		return;
	/*
	 * A linked connector state owns a connector reference (released by
	 * __drm_atomic_helper_connector_destroy_state, review 12): take it,
	 * as drm_atomic_set_crtc_for_connector() would.
	 */
	drm_connector_get(&a9->connector);
	conn->crtc = &a9->crtc;
	conn->best_encoder = &a9->encoder;
	cs->connector_mask = drm_connector_mask(&a9->connector);
	cs->encoder_mask = drm_encoder_mask(&a9->encoder);
}

/* Per-commit storage lives through asynchronous fences and cleanup. Reuse the
 * existing DRM profiler; no extra waits and no changes to the commit sequence. */
struct a9_input_commit {
	struct drm_atomic_commit base;
	struct drm_atomic_commit_profile profile;
};

static struct drm_atomic_commit *a9_input_alloc(struct drm_device *dev)
{
	struct a9_input_commit *s = kzalloc(sizeof(*s), GFP_KERNEL);

	if (!s)
		return NULL;
	if (drm_atomic_commit_init(dev, &s->base) < 0) {
		kfree(s);
		return NULL;
	}
	return &s->base;
}

static void a9_input_clear(struct drm_atomic_commit *state)
{
	drm_atomic_commit_default_clear(state);
	state->profile = NULL;
}

static void a9_input_free(struct drm_atomic_commit *state)
{
	drm_atomic_commit_default_release(state);
	kfree(container_of(state, struct a9_input_commit, base));
}

static int a9_input_commit(struct drm_device *dev, struct drm_atomic_commit *state,
			   bool nonblock)
{
	struct a9_input_commit *s = container_of(state, struct a9_input_commit, base);

	state->profile = NULL;
	if (a9_input_sample(&to_a9(dev)->fence_reserved)) {
		drm_atomic_profile_init(&s->profile);
		state->profile = &s->profile;
	}
	return drm_atomic_helper_commit(dev, state, nonblock);
}

static void a9_input_tail(struct drm_atomic_commit *state)
{
	struct a9_eink *a9 = to_a9(state->dev);

	/* FENCES has completed before the helper enters this callback. */
	if (state->profile) {
		u64 ns = state->profile->ns[DRM_ATOMIC_PROFILE_FENCES];

		mutex_lock(&a9->shadow_lock);
		a9->input.fences++;
		a9->input.fence_total_ns += ns;
		a9->input.fence_max_ns = max(a9->input.fence_max_ns, ns);
		mutex_unlock(&a9->shadow_lock);
	}
	drm_atomic_helper_commit_tail(state);
}

static const struct drm_mode_config_helper_funcs a9_input_helpers = {
	.atomic_commit_tail = a9_input_tail,
};

static const struct drm_mode_config_funcs a9_mode_config_funcs = {
	.fb_create = drm_gem_fb_create_with_dirty,
	.atomic_check = a9_mode_config_check,
	.atomic_commit = a9_input_commit,
	.atomic_state_alloc = a9_input_alloc,
	.atomic_state_clear = a9_input_clear,
	.atomic_state_free = a9_input_free,
};

DEFINE_DRM_GEM_FOPS(a9_fops);

static const struct drm_driver a9_drm_driver = {
	.driver_features = DRIVER_GEM | DRIVER_MODESET | DRIVER_ATOMIC,
	.fops = &a9_fops,
	DRM_GEM_SHMEM_DRIVER_OPS,
	.name = "a9_eink",
	.desc = "Hisense A9 E Ink (software TCON)",
	.major = 1,
	.minor = 0,
};

/* ---- sysfs: the compat contract's "eink" group on the connector ---------------- */
static struct a9_eink *kdev_to_a9(struct device *kdev)
{
	struct drm_connector *c = dev_get_drvdata(kdev);	/* drm_sysfs: kdev drvdata = connector */

	return to_a9(c->dev);
}

static bool a9_mode_allowed(u32 m)
{
	if (any_mode)
		return a9e_mode_valid(m);
	return m == 513 || m == 515 || m == 518 || m == 521;
}

static ssize_t mode_show(struct device *kdev, struct device_attribute *attr, char *buf)
{
	struct a9_eink *a9 = kdev_to_a9(kdev);

	return sysfs_emit(buf, "%u\n", READ_ONCE(a9->mode));
}

static ssize_t mode_store(struct device *kdev, struct device_attribute *attr,
			  const char *buf, size_t len)
{
	struct a9_eink *a9 = kdev_to_a9(kdev);
	u32 m;

	if (kstrtou32(buf, 0, &m) || !a9_mode_allowed(m))
		return -EINVAL;
	mutex_lock(&a9->shadow_lock);
	a9->mode = m;	/* used by the next update's transition builder */
	a9_input_request(a9, false);
	mutex_unlock(&a9->shadow_lock);
	return len;
}
static DEVICE_ATTR_RW(mode);

static ssize_t a9_ctl_show(struct device *kdev, int i, char *buf)
{
	return sysfs_emit(buf, "%d\n", READ_ONCE(kdev_to_a9(kdev)->controls[i]));
}

static ssize_t a9_ctl_store(struct device *kdev, int i, const char *buf, size_t len)
{
	struct a9_eink *a9 = kdev_to_a9(kdev);
	int v;

	if (kstrtoint(buf, 10, &v) || v < 0 || v > (i == 3 ? 1 : 100))
		return -EINVAL;
	mutex_lock(&a9->shadow_lock);
	if (a9->controls[i] != v) {
		a9->controls[i] = v;
		a9->controls_dirty = true;
		/* A new mapping affects every pixel, not just the next UI damage.
		 * Queue a full conditioned redraw even if the UI is static.
		 */
		a9->clear_req = true;
		a9->kick = true;
		wake_up(&a9->wq);
	}
	mutex_unlock(&a9->shadow_lock);
	return len;
}

#define A9_CTL_ATTR(name, i)							\
static ssize_t name##_show(struct device *d, struct device_attribute *a, char *b) \
{ return a9_ctl_show(d, i, b); }						\
static ssize_t name##_store(struct device *d, struct device_attribute *a,	\
			    const char *b, size_t l)				\
{ return a9_ctl_store(d, i, b, l); }						\
static DEVICE_ATTR_RW(name)

A9_CTL_ATTR(black_threshold, 0);
A9_CTL_ATTR(white_threshold, 1);
A9_CTL_ATTR(contrast, 2);
A9_CTL_ATTR(clear_shadow, 3);

static ssize_t force_clear_store(struct device *kdev, struct device_attribute *attr,
				 const char *buf, size_t len)
{
	struct a9_eink *a9 = kdev_to_a9(kdev);

	if (!sysfs_streq(buf, "1"))
		return -EINVAL;
	if (!a9->thread)
		return -ENODEV;
	mutex_lock(&a9->shadow_lock);
	/* reserved: satisfied by the next GC16 redraw that starts after now */
	a9->clear_req = true;
	a9->kick = true;
	mutex_unlock(&a9->shadow_lock);
	wake_up(&a9->wq);
	return len;
}
static DEVICE_ATTR_WO(force_clear);

/* Queue to the sole worker: no latch may clear while an old job owns the
 * engine/transport. This request never opens a gate or changes temperature
 * admission. A timed-out caller cannot cause a later power-up. */
static ssize_t recover_stall_store(struct device *kdev, struct device_attribute *attr,
                                  const char *buf, size_t len)
{
	struct a9_eink *a9 = kdev_to_a9(kdev);

	if (!sysfs_streq(buf, "1"))
		return -EINVAL;
	if (!a9->thread || READ_ONCE(enable))
		return -EBUSY;
	if (atomic_cmpxchg(&a9->recover_req, 0, 1))
		return -EBUSY;
	wake_up_all(&a9->wq);
	if (!wait_event_timeout(a9->wq, !atomic_read(&a9->recover_req),
	                        msecs_to_jiffies(2000)))
		return -ETIMEDOUT;
	return a9->recover_ret ?: len;
}
static DEVICE_ATTR(recover_stall, 0200, NULL, recover_stall_store);

static ssize_t stall_recovery_show(struct device *kdev, struct device_attribute *attr, char *buf)
{
	struct a9_eink *a9 = kdev_to_a9(kdev);

	struct fp9929a_status st;
	int ret = fp9929a_get_status(a9->pmic_np, &st);

	return sysfs_emit(buf, "kind %d retries %u limit 2 backoff_ms %u result %d pmic_hard %d transport_hard %d grants %llu soft_events %llu soft_ms %u hard_ms %u clean_refill_ms %u\n",
	                  READ_ONCE(a9->tx.stall_kind), READ_ONCE(a9->stall_retries),
	                  READ_ONCE(a9->stall_retries) ? 30000 : 5000,
	                  READ_ONCE(a9->recover_ret), ret || st.forced_off ||
	                  (st.emergency && READ_ONCE(a9->tx.stall_kind) != 1 && READ_ONCE(a9->tx.stall_kind) != 3),
	                  READ_ONCE(a9->tx.transport_error) || msm_a9_commit_faulted(),
	                  READ_ONCE(a9->stall_grants), READ_ONCE(a9->tx.soft_stalls),
	                  A9X_STALL_SOFT_MS, A9X_STALL_HARD_MS, A9X_RECOVERY_CLEAN_MS);
}
static DEVICE_ATTR_RO(stall_recovery);

static ssize_t stall_snapshot_show(struct device *kdev, struct device_attribute *attr, char *buf)
{
	struct a9x_transport *t = &kdev_to_a9(kdev)->tx;
	const struct a9x_stall_snapshot *s = &t->stall_snapshot;

	if (!smp_load_acquire(&s->valid))
		return sysfs_emit(buf, "valid 0\n");
	return sysfs_emit(buf,
		"valid 1 first_at_ns %llu in_commit %d front_null %d stage %u phase %u phase_age_us %llu"
		" progress_age_us %llu commit_age_us %llu frames %llu\n"
		"prepare_us %llu fences_us %llu dependencies_us %llu wait_prev_us %llu program_us %llu"
		" flip_done_us %llu cleanup_us %llu\n"
		"latest_kind %d raw_ret %d return_after_cut_us %u return_ok %d commit_hard %d transport_error %d\n",
		s->at_ns, s->in_commit, s->front_null, s->stage, s->phase, s->phase_age_us,
		s->progress_age_us, s->commit_age_us, s->frames,
		div_u64(s->phase_ns[0], 1000), div_u64(s->phase_ns[1], 1000),
		div_u64(s->phase_ns[2], 1000), div_u64(s->phase_ns[3], 1000),
		div_u64(s->phase_ns[4], 1000), div_u64(s->phase_ns[5], 1000),
		div_u64(s->phase_ns[6], 1000), READ_ONCE(t->stall_kind),
		READ_ONCE(t->stall_raw_ret), READ_ONCE(t->stall_return_us),
		READ_ONCE(t->stall_commit_ok), msm_a9_commit_faulted(), READ_ONCE(t->transport_error));
}
static DEVICE_ATTR_RO(stall_snapshot);


/* Stock-style user clear: retain the entered mode, never authorize recovery.
 * Selection under shadow_lock gives recovery/entry requirements priority.
 */
static ssize_t manual_clear_store(struct device *kdev, struct device_attribute *attr,
				  const char *buf, size_t len)
{
	struct a9_eink *a9 = kdev_to_a9(kdev);

	if (!sysfs_streq(buf, "1"))
		return -EINVAL;
	if (!a9->thread)
		return -ENODEV;
	mutex_lock(&a9->shadow_lock);
	a9->manual_clear_req = true;
	a9_input_request(a9, true);
	a9->kick = true;
	mutex_unlock(&a9->shadow_lock);
	wake_up(&a9->wq);
	return len;
}
static DEVICE_ATTR_WO(manual_clear);

/* Root-only diagnostic input; neither this nor "off" changes any drive gate.
 * Full Clear jobs share normal temperature, bin, fault and timing admission.
 */
static ssize_t test_pattern_show(struct device *kdev, struct device_attribute *attr,
				 char *buf)
{
	return sysfs_emit(buf, "%s\n", READ_ONCE(kdev_to_a9(kdev)->test_pattern) ?
			  "contrast" : "off");
}

static ssize_t test_pattern_store(struct device *kdev, struct device_attribute *attr,
				  const char *buf, size_t len)
{
	struct a9_eink *a9 = kdev_to_a9(kdev);
	bool on;

	if (sysfs_streq(buf, "contrast"))
		on = true;
	else if (sysfs_streq(buf, "off"))
		on = false;
	else
		return -EINVAL;
	if (!a9->thread)
		return -ENODEV;
	mutex_lock(&a9->shadow_lock);
	a9->test_pattern = on;
	/* Repeating "contrast" deliberately reconditions the same references.
	 * "off" redraws the newest UI shadow, which KMS has kept current.
	 */
	a9->clear_req = true;
	a9->kick = true;
	mutex_unlock(&a9->shadow_lock);
	wake_up(&a9->wq);
	return len;
}
static DEVICE_ATTR(test_pattern, 0600, test_pattern_show, test_pattern_store);

/* Accepted transport slots since this PG-qualified power session began.
 * Includes zero_test slots; power-on/shutdown template refreshes are excluded
 * by the existing transport frames counter. No analog-delivery claim. */
static u64 a9_session_frames(const struct a9_eink *a9)
{
	return a9->tx.frames - a9->session.frame0;
}

/* read-only status (not part of the contract) */
/* snapshot of the log-only counters (0052); the caller kfree()s it */
static struct a9_rail_diag *a9_diag_copy(struct a9_eink *a9)
{
	struct a9_rail_diag *d = kmalloc(sizeof(*d), GFP_KERNEL);
	unsigned long flags;

	if (!d)
		return NULL;
	spin_lock_irqsave(&a9->diag.lock, flags);
	*d = a9->diag;
	spin_unlock_irqrestore(&a9->diag.lock, flags);
	return d;
}

static ssize_t status_show(struct device *kdev, struct device_attribute *attr, char *buf)
{
	struct a9_eink *a9 = kdev_to_a9(kdev);
	struct a9x_transport *t = &a9->tx;
	u32 phase = READ_ONCE(t->profile.phase);
	u64 age_us = 0;

	if (READ_ONCE(t->profile_valid) && phase < DRM_ATOMIC_PROFILE_DONE) {
		u64 mark = READ_ONCE(t->profile.mark_ns);
		u64 now = ktime_get_ns();

		if (now >= mark)
			age_us = div_u64(now - mark, 1000);
	}

	return sysfs_emit(buf,
		"enable %d waveform %d transport %d hv_on %d stall_fault %d template %d\n"
		"updates %llu aborts %llu frames %llu late %llu vbl_skips %llu commit_fail %llu max_gap_us %u"
		" late_drive %llu vbl_skips_drive %llu max_gap_drive_us %u\n"
		"last mode %#x pre %#x clear %d type %u bin %d t1 %d t2 %d frames %u steps %u dry %d aborted %d"
		" rect %d,%d-%d,%d begin_us %u total_us %u prep_us %u\n"
		"pace commit_max_us %u commit_slow %llu compose_max_us %u render_max_us %u"
		" prefill_us %u ring_min %u underruns %llu\n"
		"flip_phases valid %d phase %u age_us %llu prepare_us %llu fences_us %llu"
		" dependencies_us %llu wait_prev_us %llu program_us %llu flip_done_us %llu cleanup_us %llu\n"
		"flip_max prepare_us %u fences_us %u dependencies_us %u wait_prev_us %u"
		" program_us %u flip_done_us %u cleanup_us %u total_us %u modeset_us %u flips %llu modesets %llu\n"
		"rail_fault %d rail_faults %llu rail_at_ms %lld rail_getter %d rail_pg_raw %d rail_on %d"
		" rail_energized %d rail_forced %d rail_emergency %d rail_pg_valid %d rail_tx_on %d"
		" rail_gone %d rail_front_null %d rail_armed %d rail_session_ms %lld rail_since_wf_ms %lld"
		" rail_wf_ret %d force_clear %d\n"
		"session id %llu frames %llu full_frames %llu waveforms %llu done %llu"
		" full_started %llu full_done %llu clear_started %llu clear_done %llu"
		" transition_started %llu transition_done %llu\n"
		"rail_session id %llu frames %llu full_frames %llu waveforms %llu done %llu"
		" full_started %llu full_done %llu clear_started %llu clear_done %llu"
		" transition_started %llu transition_done %llu\n"
		"rail_pg_at_us %lld pd_off_confirmed %d pd_valid %d pd_getter %d"
		" pd_seq_before %llu pd_seq %llu pd_entry_at_us %lld pd_entry_pg %d"
		" pd_vcom_at_us %lld pd_vcom_pg %d pd_pwrup_at_us %lld pd_pwrup_pg %d\n",
		enable, a9->wf_loaded, a9x_attached(t), t->on, atomic_read(&a9x_stall_fault),
		a9->ct_loaded,
		a9->updates, a9->aborts, t->frames, t->late, t->vbl_skips, t->commits_failed,
		t->max_gap_us, t->late_drive, t->vbl_skips_drive, t->max_gap_drive_us,
		a9->last.mode, a9->last.pre, a9->last.clear, a9->last.type,
		a9->last.bin, a9->last.t1, a9->last.t2, a9->last.frames, a9->last.steps,
		a9->last.dry, a9->last.aborted, a9->last.rect.x1, a9->last.rect.y1,
		a9->last.rect.x2, a9->last.rect.y2, a9->last.begin_us, a9->last.total_us,
		a9->last.prep_us, t->commit_max_us, t->commit_slow, t->compose_max_us,
		a9->render_max_us, a9->prefill_us, a9->ring.fill_min, a9->ring.underruns,
		READ_ONCE(t->profile_valid), phase, age_us,
		div_u64(READ_ONCE(t->profile.ns[DRM_ATOMIC_PROFILE_PREPARE]), 1000),
		div_u64(READ_ONCE(t->profile.ns[DRM_ATOMIC_PROFILE_FENCES]), 1000),
		div_u64(READ_ONCE(t->profile.ns[DRM_ATOMIC_PROFILE_DEPENDENCIES]), 1000),
		div_u64(READ_ONCE(t->profile.ns[DRM_ATOMIC_PROFILE_WAIT_PREV]), 1000),
		div_u64(READ_ONCE(t->profile.ns[DRM_ATOMIC_PROFILE_PROGRAM]), 1000),
		div_u64(READ_ONCE(t->profile.ns[DRM_ATOMIC_PROFILE_FLIP_DONE]), 1000),
		div_u64(READ_ONCE(t->profile.ns[DRM_ATOMIC_PROFILE_CLEANUP]), 1000),
		READ_ONCE(t->phase_max_us[DRM_ATOMIC_PROFILE_PREPARE]),
		READ_ONCE(t->phase_max_us[DRM_ATOMIC_PROFILE_FENCES]),
		READ_ONCE(t->phase_max_us[DRM_ATOMIC_PROFILE_DEPENDENCIES]),
		READ_ONCE(t->phase_max_us[DRM_ATOMIC_PROFILE_WAIT_PREV]),
		READ_ONCE(t->phase_max_us[DRM_ATOMIC_PROFILE_PROGRAM]),
		READ_ONCE(t->phase_max_us[DRM_ATOMIC_PROFILE_FLIP_DONE]),
		READ_ONCE(t->phase_max_us[DRM_ATOMIC_PROFILE_CLEANUP]),
		READ_ONCE(t->flip_max_us), READ_ONCE(t->modeset_max_us),
		READ_ONCE(t->flips), READ_ONCE(t->modesets),
		atomic_read(&a9x_rail_fault), a9->rail.count, a9->rail.at_ms, a9->rail.getter,
		a9->rail.pg_raw, a9->rail.on, a9->rail.energized, a9->rail.forced,
		a9->rail.emergency, a9->rail.pg_valid, a9->rail.tx_on, a9->rail.tx_gone,
		a9->rail.front_null, a9->rail.armed, a9->rail.session_ms, a9->rail.since_wf_ms,
		a9->rail.wf_ret, READ_ONCE(a9->force_clear),
		a9->session.id, a9_session_frames(a9), a9->session.full_frames,
		a9->session.waveforms, a9->session.waveforms_done,
		a9->session.full_started, a9->session.full_done, a9->session.clears,
		a9->session.clears_done, a9->session.transitions, a9->session.transitions_done,
		a9->rail.session.id, a9->rail.session_frames, a9->rail.session.full_frames,
		a9->rail.session.waveforms, a9->rail.session.waveforms_done,
		a9->rail.session.full_started, a9->rail.session.full_done, a9->rail.session.clears,
		a9->rail.session.clears_done, a9->rail.session.transitions, a9->rail.session.transitions_done,
		a9->rail.pg_at_us, a9->rail.pd_off_confirmed, a9->rail.pd_valid, a9->rail.pd_getter,
		a9->rail.pd_seq_before, a9->rail.pd_seq,
		a9->rail.pd_at_us[0], a9->rail.pd_pg_raw[0],
		a9->rail.pd_at_us[1], a9->rail.pd_pg_raw[1],
		a9->rail.pd_at_us[2], a9->rail.pd_pg_raw[2]);
}
static DEVICE_ATTR_RO(status);

/* 0052: the shutdown PG trace ring (healthy baseline), newest first. Read-only. */
static ssize_t rail_diag_show(struct device *kdev, struct device_attribute *attr, char *buf)
{
	struct a9_eink *a9 = kdev_to_a9(kdev);
	struct a9_rail_diag *d = a9_diag_copy(a9);
	ssize_t n;
	unsigned int k;

	if (!d)
		return -ENOMEM;
	n = sysfs_emit(buf, "diag_hist_n %llu %llu %llu %llu %llu diag_hist_low %llu %llu %llu %llu %llu"
		" (since_wf_ms buckets 0-3 4-15 16-63 64+ none)\n"
		"diag_pg_end n %llu low %llu err %llu skipped %llu last_pg %d last_at_us %lld"
		" full_n %llu full_low %llu"
		" first_low_ms %lld first_low_session %llu first_low_n %llu first_low_total_us %u last_low_ms %lld"
		" diag_pd_total %llu diag_pd_missed %llu diag_pd_skipped_fault %llu\n",
		d->hist_n[0], d->hist_n[1], d->hist_n[2], d->hist_n[3], d->hist_n[4],
		d->hist_low[0], d->hist_low[1], d->hist_low[2], d->hist_low[3], d->hist_low[4],
		d->end_n, d->end_low, d->end_err, d->end_skipped, d->end_last_pg, d->end_last_at_us,
		d->end_full_n, d->end_full_low,
		d->end_first_low_ms, d->end_first_low_session, d->end_first_low_n,
		d->end_first_low_total_us, d->end_last_low_ms,
		d->pd_total, d->pd_missed, d->pd_skipped_fault);
	n += sysfs_emit_at(buf, n, "diag_pg_policy observe_only 1 quotas 0 storm_latch 0 reread_high %llu\n",
		d->settle_reread_high);
	n += sysfs_emit_at(buf, n, "diag_vcom reads %llu ok %llu mismatch %llu err %llu last %d last_ms %lld\n",
		d->vcom_reads, d->vcom_ok, d->vcom_mismatch, d->vcom_err, d->vcom_last, d->vcom_last_ms);
	n += sysfs_emit_at(buf, n, "pd_total %llu pd_missed %llu pd_skipped_fault %llu pd_seen_seq %llu"
		" (newest first)\n",
		d->pd_total, d->pd_missed, d->pd_skipped_fault, d->pd_seen_seq);
	for (k = 0; k < A9_DIAG_PD && k < d->pd_total; k++) {
		unsigned int i = (d->pd_head + A9_DIAG_PD - 1 - k) % A9_DIAG_PD;

		n += sysfs_emit_at(buf, n, "pd %u seq %llu session %llu pg %d,%d,%d dt_us 0,%lld,%lld\n",
			k, d->pd[i].seq, d->pd[i].session, d->pd[i].pg[0], d->pd[i].pg[1], d->pd[i].pg[2],
			d->pd[i].at_us[1] - d->pd[i].at_us[0], d->pd[i].at_us[2] - d->pd[i].at_us[0]);
	}
	kfree(d);
	return n;
}
static DEVICE_ATTR_RO(rail_diag);

/* Separate from the safety/status snapshot. No per-frame logging or data dump. */
static ssize_t input_stats_show(struct device *kdev, struct device_attribute *attr, char *buf)
{
	struct a9_eink *a9 = kdev_to_a9(kdev);
	struct a9_input_stats d;
	ssize_t n;
	int i;

	mutex_lock(&a9->shadow_lock);
	d = a9->input;
	mutex_unlock(&a9->shadow_lock);
	n = sysfs_emit(buf, "enabled %d limit %u samples %llu failures %llu pixels %llu full %llu partial %llu unchanged %llu\n",
		READ_ONCE(input_diagnostics), A9_INPUT_SAMPLES, d.count, d.failures,
		d.pixels, d.full, d.partial, d.unchanged);
	for (i = 0; i < 4; i++)
		n += sysfs_emit_at(buf, n, "%s_total_ns %llu %s_max_ns %llu\n",
			(const char *[]) { "cpu_begin", "shadow_wait", "convert", "cpu_end" }[i], d.total_ns[i],
			(const char *[]) { "cpu_begin", "shadow_wait", "convert", "cpu_end" }[i], d.max_ns[i]);
	n += sysfs_emit_at(buf, n, "fences %llu fence_total_ns %llu fence_max_ns %llu\n"
		"mode_requests %llu mode_ms %llu requested_mode %u manual_requests %llu manual_ms %llu manual_mode %u\n",
		d.fences, d.fence_total_ns, d.fence_max_ns, d.mode_requests, d.mode_ms,
		d.requested_mode, d.manual_requests, d.manual_ms, d.manual_mode);
	return n;
}
static DEVICE_ATTR_RO(input_stats);

/* Damage-limited prepare counters: one line, separate from status (which is unchanged). */
static ssize_t damage_stats_show(struct device *kdev, struct device_attribute *attr, char *buf)
{
	struct a9_eink *a9 = kdev_to_a9(kdev);
	u64 inc, full, tiles, rows, capped, sc, sh;
	u32 last_inc, lt, lr;

	mutex_lock(&a9->eng_lock);
	sc = a9->same.checks;
	sh = a9->same.hits;
	inc = a9->e.inc_prepares;
	full = a9->e.full_prepares;
	capped = a9->e.inc_capped;
	tiles = a9->e.inc_tiles;
	rows = a9->e.inc_rows;
	last_inc = a9->e.last_inc;
	lt = a9->e.last_tiles;
	lr = a9->e.last_rows;
	mutex_unlock(&a9->eng_lock);
	return sysfs_emit(buf, "enabled %d ee %d inc %llu full %llu capped %llu tiles %llu px %llu last_inc %u last_tiles %u last_px %u same_enabled %d same_checks %llu same_skips %llu\n",
			  READ_ONCE(damage_prep), a9->e.ee != NULL, inc, full, capped, tiles, rows, last_inc, lt, lr,
			  READ_ONCE(skip_same_target) && a9->same.grey, sc, sh);
}
static DEVICE_ATTR_RO(damage_stats);

/* eink-latency r2 prewarm counters: one line (worker-owned fields read without a lock: diagnostic) */
static ssize_t prewarm_stats_show(struct device *kdev, struct device_attribute *attr, char *buf)
{
	struct a9_eink *a9 = kdev_to_a9(kdev);

	return sysfs_emit(buf, "prewarm %u gap_ms %u handler %d pokes %ld req %llu up %llu unused %llu fail %llu\n",
			  READ_ONCE(prewarm), READ_ONCE(prewarm_gap_ms), a9->input_h_registered,
			  atomic_long_read(&a9->prewarm_pokes), READ_ONCE(a9->prewarm_req_n),
			  READ_ONCE(a9->prewarm_up), READ_ONCE(a9->prewarm_unused), READ_ONCE(a9->prewarm_fail));
}
static DEVICE_ATTR_RO(prewarm_stats);

static struct attribute *a9_eink_attrs[] = {
	&dev_attr_input_stats.attr,
	&dev_attr_damage_stats.attr,
	&dev_attr_prewarm_stats.attr,
	&dev_attr_mode.attr,
	&dev_attr_black_threshold.attr,
	&dev_attr_white_threshold.attr,
	&dev_attr_contrast.attr,
	&dev_attr_clear_shadow.attr,
	&dev_attr_force_clear.attr,
	&dev_attr_recover_stall.attr,
	&dev_attr_stall_recovery.attr,
	&dev_attr_stall_snapshot.attr,
	&dev_attr_manual_clear.attr,
	&dev_attr_test_pattern.attr,
	&dev_attr_status.attr,
	&dev_attr_rail_diag.attr,
	NULL
};

static const struct attribute_group a9_eink_group = {
	.name = "eink",
	.attrs = a9_eink_attrs,
};

/* ---- control template ------------------------------------------------------- */
static int a9_load_template(struct a9_eink *a9)
{
	const struct a9e_ct_header *h;
	const struct firmware *fw;
	const u8 *payload;
	u8 want[SHA256_DIGEST_SIZE], d[SHA256_DIGEST_SIZE];
	int ret;

	if (a9->ct_loaded)
		return 0;
	ret = hex2bin(want, A9E_CT_SHA256, sizeof(want));
	if (ret)
		return ret;
	ret = request_firmware(&fw, A9_CT_NAME, a9->dev);
	if (ret) {
		dev_err_once(a9->dev, "no valid control template (%d): refusing to drive (no %s)\n",
			     ret, A9_CT_NAME);
		return ret;
	}
	ret = a9e_ct_check(fw->data, fw->size, &payload);
	if (!ret) {
		h = (const struct a9e_ct_header *)fw->data;
		sha256(payload, A9T_FRAME_BYTES, d);
		if (memcmp(d, h->payload_sha256, sizeof(d)) || memcmp(d, want, sizeof(d)))
			ret = -EBADMSG;
	}
	if (ret) {
		dev_err(a9->dev, "no valid control template (%d): refusing to drive (%s rejected)\n",
			ret, A9_CT_NAME);
		release_firmware(fw);
		return -EINVAL;
	}
	memcpy(a9->ct, payload, A9T_FRAME_BYTES);
	release_firmware(fw);
	/* the staging frame carries the template; the engine writes byte 0 only */
	memcpy(a9->frame, a9->ct, A9T_FRAME_BYTES);
	mutex_lock(&a9->tx.lock);
	a9->tx.tmpl = a9->ct;
	mutex_unlock(&a9->tx.lock);
	a9->ct_loaded = true;
	dev_info(a9->dev, "control template loaded (sha256 %*phN...)\n", 4, d);
	return 0;
}

/* ---- waveform ------------------------------------------------------------------ */
static int a9_load_waveform(struct a9_eink *a9)
{
	const struct firmware *fw;
	const struct a9t_tables *t;
	u8 d[SHA256_DIGEST_SIZE];
	int ret;

	/* no template, no frames at all (not even a dry run: it would hash
	 * frames the panel never gets) */
	ret = a9_load_template(a9);
	if (ret)
		return ret;
	if (a9->wf_loaded)
		return 0;
	ret = request_firmware(&fw, A9_FW_NAME, a9->dev);
	if (ret) {
		dev_warn_once(a9->dev, "no %s (%d): make it with a9wf-extract; updates are dropped\n",
			      A9_FW_NAME, ret);
		return ret;
	}
	ret = a9e_wf_check(fw->data, fw->size, &t);
	if (!ret) {
		sha256(fw->data + sizeof(struct a9e_wf_header),
		       fw->size - sizeof(struct a9e_wf_header), d);
		if (memcmp(d, ((const struct a9e_wf_header *)fw->data)->payload_sha256, sizeof(d)))
			ret = -EBADMSG;
	}
	if (ret) {
		dev_err(a9->dev, "%s rejected (%d)\n", A9_FW_NAME, ret);
		release_firmware(fw);
		return -EINVAL;
	}
	memcpy(a9->tables, t, sizeof(*t));
	memcpy(a9->wf_src_sha, ((const struct a9e_wf_header *)fw->data)->source_sha256, 32);
	release_firmware(fw);

	mutex_lock(&a9->eng_lock);
	a9->e.t = a9->tables;
	a9->e.flag_6c7 = flag_6c7;
	a9e_setup(&a9->e);
	memcpy(a9->e.cache, a9->ct, A9T_FRAME_BYTES);	/* hold cache: template too */
	mutex_unlock(&a9->eng_lock);
	a9->wf_loaded = true;
	a9->init_pending = a9->dry_init_pending = init_flash;
	a9->force_clear = a9->dry_force_clear = true;	/* glass content unknown at start */
	dev_info(a9->dev, "waveform tables loaded (composer %*phN...)\n", 4, a9->wf_src_sha);
	return 0;
}

/* ---- temperatures (stock rules, docs/mainline-plan.md section 4.5 item 6) -------- */
static int a9_hv_sample(struct a9_eink *a9, struct fp9929a_status *st);

static int a9_temps(struct a9_eink *a9, int *t1, int *t2,
		    struct fp9929a_status *st)
{
	int v, ret;

	/* t1: the FP9929A's TMST byte, sampled inside the power-up sequence */
	*t1 = temp_fallback;
	a9->last.t1_raw = INT_MIN;
	ret = a9_hv_sample(a9, st);
	if (!ret && st->temp_valid) {
		a9->last.t1_raw = st->temp_c;
		/* composer compatibility (it saw TMST as unsigned): above 85 reads
		 * as 0; negative values are refused in a9_update(), not mapped */
		*t1 = st->temp_c > 85 ? 0 : st->temp_c;
	}
	/* t2: PMI632 epd_top_therm in m deg C (mainline interpolates in mV, stock
	 * in uV: up to 73 m deg C apart, a different integer for ~3% of codes) */
	*t2 = *t1;
	a9->last.t2_raw = INT_MIN;
	if (a9->therm && !iio_read_channel_processed(a9->therm, &v)) {
		a9->last.t2_raw = v;
		*t2 = v < -999 ? 0 : (v > 50999 ? 50 : v / 1000);
	}
	return ret;
}

/* Stock uses PG only as telemetry. Device/API and explicit hard-off failures
 * remain errors; a raw PG level or missing PG descriptor is not one. */
static int a9_hv_sample(struct a9_eink *a9, struct fp9929a_status *st)
{
	memset(st, 0, sizeof(*st));
	st->pg_raw = -ENODATA;	/* not read (lookup failed) */
	return fp9929a_get_status(a9->pmic_np, st);
}

static bool a9_hv_good(int ret, const struct fp9929a_status *st)
{
	return !ret && st->on && !st->forced_off && !st->emergency;
}

static bool a9_hv_is_on(struct a9_eink *a9)
{
	struct fp9929a_status st;
	int ret = a9_hv_sample(a9, &st);

	return a9_hv_good(ret, &st);
}

static bool a9_hv_maybe_on(struct a9_eink *a9)
{
	struct fp9929a_status st;

	return fp9929a_get_status(a9->pmic_np, &st) || st.on;
}

/* transport fault (hardirq, from the interlock): stop the worker's waveform */
static void a9_tx_fault(struct a9x_transport *t)
{
	WRITE_ONCE(enable, false);
}

/* ---- pipeline power ------------------------------------------------------------- */
static void a9_force_hv_off(struct a9_eink *a9, const char *why)
{
	dev_crit_ratelimited(a9->dev, "%s: forcing the FP9929A down\n", why);
	if (a9->hv)
		regulator_force_disable(a9->hv);
	/* latched until a pipeline power-down succeeds again */
	a9->hv_fault = true;
}

static void a9_power_off(struct a9_eink *a9)
{
	if (READ_ONCE(a9->tx.unreg_hv_unknown)) {
		WRITE_ONCE(a9->tx.unreg_hv_unknown, false);
		if (a9_hv_maybe_on(a9))
			a9_force_hv_off(a9, "msm went away with the pipeline on");
	}
	if (!a9->tx.on) {
		if (a9->hv_fault && !a9_hv_maybe_on(a9))
			a9->hv_fault = false;
		return;
	}
	if (a9x_power_off(&a9->tx) || a9_hv_maybe_on(a9))
		a9_force_hv_off(a9, "pipeline power-down failed");
	else
		a9->hv_fault = false;
}

/*
 * Patch 0052 (rail-diag), LOG-ONLY. These helpers only count and record: no
 * latch, gate, shutdown, hold, cap or timing decision reads any of it, and
 * none of them calls a9_check_rail_sample(), a9_rail_fault() or the power
 * paths. diag.lock is a leaf lock (nothing else is taken, no FP9929A call is
 * made, with it held). The FP9929A driver API is the existing
 * fp9929a_get_status() only.
 */
static unsigned int a9_diag_bucket(struct a9_eink *a9, ktime_t now)
{
	s64 ms;

	if (!a9->wf_end)
		return 4;		/* no waveform yet */
	ms = ktime_ms_delta(now, a9->wf_end);
	if (ms < 4)
		return 0;
	if (ms < 16)
		return 1;
	return ms < 64 ? 2 : 3;
}

/* every CHECKED rail sample (pipeline was on): delay since the last waveform end */
static void a9_diag_rail_sample(struct a9_eink *a9, bool was_on, int ret,
				const struct fp9929a_status *st)
{
	unsigned long flags;
	unsigned int b;

	if (!was_on)
		return;
	b = a9_diag_bucket(a9, ktime_get());
	spin_lock_irqsave(&a9->diag.lock, flags);
	a9->diag.hist_n[b]++;
	if (ret || !st->pg_valid || !st->pg_good)
		a9->diag.hist_low[b]++;
	spin_unlock_irqrestore(&a9->diag.lock, flags);
}

/* keep a shutdown trace not seen yet; a fault's own trace lives in a9->rail.pd_* */
static void a9_diag_pd_push(struct a9_eink *a9, const struct fp9929a_pd_trace *tr)
{
	unsigned long flags;
	unsigned int i;
	u64 seq = tr->seq;

	spin_lock_irqsave(&a9->diag.lock, flags);
	if (seq > a9->diag.pd_seen_seq) {
		a9->diag.pd_missed += seq - a9->diag.pd_seen_seq - 1;
		a9->diag.pd_seen_seq = seq;
		if (a9->rail.count && seq == a9->rail.pd_seq) {
			a9->diag.pd_skipped_fault++;
		} else {
			i = a9->diag.pd_head;
			a9->diag.pd[i].seq = seq;
			a9->diag.pd[i].session = a9->session.id;
			a9->diag.pd[i].at_us[0] = tr->entry.at_us;
			a9->diag.pd[i].pg[0] = tr->entry.pg_raw;
			a9->diag.pd[i].at_us[1] = tr->vcom_low.at_us;
			a9->diag.pd[i].pg[1] = tr->vcom_low.pg_raw;
			a9->diag.pd[i].at_us[2] = tr->pwrup_low.at_us;
			a9->diag.pd[i].pg[2] = tr->pwrup_low.pg_raw;
			a9->diag.pd_head = (i + 1) % A9_DIAG_PD;
			a9->diag.pd_total++;
		}
	}
	spin_unlock_irqrestore(&a9->diag.lock, flags);
}

/*
 * One PG read at a waveform's end (the worker thread, after a9_run_ring
 * returned). RECORDED, NEVER ACTED ON: a low here does not latch; the next
 * update's own sample still decides, exactly as before. Only when the
 * pipeline is still on (an aborted or faulted waveform has already powered
 * down: nothing to read).
 */
static s64 a9_diag_wf_end(struct a9_eink *a9)
{
	struct fp9929a_status st;
	unsigned long flags;
	struct a9e_rect fr = a9e_rect_full();
	bool low, first = false, full;
	s64 up_ms;
	int ret;

	if (!READ_ONCE(a9->tx.on)) {
		spin_lock_irqsave(&a9->diag.lock, flags);
		a9->diag.end_skipped++;
		spin_unlock_irqrestore(&a9->diag.lock, flags);
		return 0;
	}
	ret = a9_hv_sample(a9, &st);
	up_ms = ktime_to_ms(ktime_get());
	low = !ret && st.pg_raw == 0;
	/* review 89d item 6: THIS update's rect (the engine's, prepared under
	 * eng_lock); a9->last still describes the previous update here */
	full = a9->e.rect.x1 == fr.x1 && a9->e.rect.y1 == fr.y1 &&
	       a9->e.rect.x2 == fr.x2 && a9->e.rect.y2 == fr.y2;
	spin_lock_irqsave(&a9->diag.lock, flags);
	if (ret || st.pg_raw < 0) {
		a9->diag.end_err++;
	} else {
		a9->diag.end_n++;
		if (full)
			a9->diag.end_full_n++;
		a9->diag.end_last_pg = st.pg_raw;
		a9->diag.end_last_at_us = st.pg_at_us;
		if (low) {
			a9->diag.end_low++;
			if (full)
				a9->diag.end_full_low++;
			a9->diag.end_last_low_ms = up_ms;
			if (a9->diag.end_first_low_ms < 0) {
				first = true;
				a9->diag.end_first_low_ms = up_ms;
				a9->diag.end_first_low_session = a9->session.id;
				a9->diag.end_first_low_n = a9->diag.end_n;
				a9->diag.end_first_low_total_us = a9->last.total_us;
			}
		}
	}
	spin_unlock_irqrestore(&a9->diag.lock, flags);
	if (low)
		dev_warn_ratelimited(a9->dev, "rail-diag: PG low at waveform end (not a fault, not latched;"
			" stock telemetry only): session %llu uptime_ms %lld first %d\n",
			a9->session.id, up_ms, first);
	if (!ret)
		a9_diag_pd_push(a9, &st.pdtrace);
	/* Passive time of a low sample (0 = none). */
	return low && st.pg_at_us > 0 ? st.pg_at_us : 0;
}

/* Stock never qualifies or counts PG dips to decide whether scanning may
 * continue. No PG wait, lifetime quota or rate latch exists here. */

/*
 * Patch 0039 (run 11): the rails were not valid at an update's start with the
 * pipeline on. Record why from the failed sample and the transport BEFORE
 * anything is shut down, latch the rail fault (drive off, full Clear owed),
 * then let the caller run the ordered neutral/power-down. No retry, no
 * debounce: a later sample cannot erase a device/API or explicit hard-off failure.
 */
static void a9_rail_fault(struct a9_eink *a9, int ret, const struct fp9929a_status *st)
{
	struct a9x_transport *t = &a9->tx;
	ktime_t now = ktime_get();

	mutex_lock(&t->lock);
	a9->rail.tx_on = t->on;
	a9->rail.tx_gone = t->gone;
	a9->rail.front_null = t->front_null;
	a9->rail.armed = t->stall_armed;
	a9->rail.session_ms = t->on ? ktime_ms_delta(now, t->on_since) : -1;
	a9->rail.session = a9->session;
	a9->rail.session_frames = a9_session_frames(a9);
	mutex_unlock(&t->lock);
	a9->rail.getter = ret;
	a9->rail.pg_raw = st->pg_raw;
	a9->rail.pg_at_us = st->pg_at_us;
	a9->rail.pd_seq_before = st->pdtrace.seq;
	a9->rail.pd_seq = 0;
	a9->rail.pd_off_confirmed = false;
	a9->rail.pd_valid = false;
	a9->rail.pd_getter = -ENODATA;
	memset(a9->rail.pd_at_us, 0, sizeof(a9->rail.pd_at_us));
	a9->rail.pd_pg_raw[0] = a9->rail.pd_pg_raw[1] = a9->rail.pd_pg_raw[2] = -ENODATA;
	a9->rail.on = st->on;
	a9->rail.energized = st->energized;
	a9->rail.forced = st->forced_off;
	a9->rail.emergency = st->emergency;
	a9->rail.pg_valid = st->pg_valid;
	a9->rail.pg_good = st->pg_good;
	a9->rail.since_wf_ms = a9->wf_end ? ktime_ms_delta(now, a9->wf_end) : -1;
	a9->rail.wf_ret = a9->wf_ret;
	a9->rail.at_ms = ktime_to_ms(now);
	a9->rail.count++;
	/* synchronous containment: nothing drives or powers up until clear_fault */
	atomic_set(&a9x_rail_fault, 1);
	WRITE_ONCE(enable, false);
	a9->force_clear = true;	/* glass state unknown: full Clear first */
	/* Reporting is deliberately deferred until the existing power-off returns. */
}

static void a9_rail_report(struct a9_eink *a9)
{
	struct fp9929a_status st;
	int ret;

	/* No new lookup, GPIO read or log was added ahead of rail removal.
	 * An unknown/failed shutdown does not justify a diagnostic status read. */
	a9->rail.pd_off_confirmed = !a9->tx.on && !a9->hv_fault;
	dev_err(a9->dev, "FP9929A dropped out while the pipeline was on: rail fault latched"
		" (getter %d pg_raw %d on %d energized %d forced %d emergency %d pg_valid %d"
		" tx_on %d gone %d front_null %d armed %d session_ms %lld since_wf_ms %lld wf_ret %d);"
		" write 1 to a9_eink.clear_fault, then a full Clear runs first\n",
		a9->rail.getter, a9->rail.pg_raw, a9->rail.on, a9->rail.energized,
		a9->rail.forced, a9->rail.emergency, a9->rail.pg_valid,
		a9->rail.tx_on, a9->rail.tx_gone, a9->rail.front_null,
		a9->rail.armed, a9->rail.session_ms, a9->rail.since_wf_ms, a9->rail.wf_ret);
	if (!a9->rail.pd_off_confirmed)
		return;
	ret = a9_hv_sample(a9, &st);
	a9->rail.pd_getter = ret;
	if (!ret && !a9->rail.getter && st.pdtrace.seq != a9->rail.pd_seq_before) {
		a9->rail.pd_seq = st.pdtrace.seq;
		a9->rail.pd_at_us[0] = st.pdtrace.entry.at_us;
		a9->rail.pd_pg_raw[0] = st.pdtrace.entry.pg_raw;
		a9->rail.pd_at_us[1] = st.pdtrace.vcom_low.at_us;
		a9->rail.pd_pg_raw[1] = st.pdtrace.vcom_low.pg_raw;
		a9->rail.pd_at_us[2] = st.pdtrace.pwrup_low.at_us;
		a9->rail.pd_pg_raw[2] = st.pdtrace.pwrup_low.pg_raw;
		a9->rail.pd_valid = true;
	}
	dev_info(a9->dev, "rail shutdown PG samples (normal path only, not PG edges/recovery):"
		" fault %lld/%d valid %d getter %d seq %llu->%llu"
		" entry %lld/%d vcom_low %lld/%d pwrup_low %lld/%d"
		" session %llu frames %llu full_frames %llu full %llu/%llu\n",
		a9->rail.pg_at_us, a9->rail.pg_raw, a9->rail.pd_valid, ret,
		a9->rail.pd_seq_before, a9->rail.pd_seq,
		a9->rail.pd_at_us[0], a9->rail.pd_pg_raw[0],
		a9->rail.pd_at_us[1], a9->rail.pd_pg_raw[1],
		a9->rail.pd_at_us[2], a9->rail.pd_pg_raw[2],
		a9->rail.session.id, a9->rail.session_frames, a9->rail.session.full_frames,
		a9->rail.session.full_started, a9->rail.session.full_done);
}

/* Only device/API/hard-off failures latch. PG observations never act. */
static int a9_check_rail_sample(struct a9_eink *a9, bool was_on, int ret,
			      const struct fp9929a_status *st)
{
	a9_diag_rail_sample(a9, was_on, ret, st);	/* 0052: counters only */
	if (!was_on || a9_hv_good(ret, st))
		return 0;
	a9_rail_fault(a9, ret, st);
	a9_power_off(a9); /* ordered neutral; emergency HV off if that fails */
	a9_rail_report(a9); /* original sample stays frozen, no recovery */
	return -EIO;
}

/* Read-only PG sample under eng_lock, selecting logging only. */
static bool a9_pg_wf_end_low(struct a9_eink *a9, s64 first_us, struct fp9929a_status *st)
{
	unsigned long flags;
	int ret;

	if (!READ_ONCE(a9->tx.on))
		return false;
	ret = a9_hv_sample(a9, st);
	if (ret || !st->on || st->forced_off || st->emergency || !st->pg_valid || st->pg_good) {
		if (first_us && !ret && st->pg_good) {
			spin_lock_irqsave(&a9->diag.lock, flags);
			a9->diag.settle_reread_high++;
			spin_unlock_irqrestore(&a9->diag.lock, flags);
		}
		return false;
	}
	return true;
}

/* Log the completed update after eng_lock is released. No policy decisions. */
static void a9_pg_wf_end(struct a9_eink *a9, const struct fp9929a_status *st)
{
	unsigned long flags;
	u8 now = 0, prog = 0;
	int r = -ENODATA;

	/* Bounded VCOM readback: no result changes scanning, gates or faults. */
	lockdep_assert_not_held(&a9->eng_lock);
	if (a9->vcom_reads < A9_VCOM_READS_MAX) {
		a9->vcom_reads++;
		r = fp9929a_read_vcom(a9->pmic_np, &now, &prog);
		spin_lock_irqsave(&a9->diag.lock, flags);
		a9->diag.vcom_reads++;
		if (r)
			a9->diag.vcom_err++;
		else if (now == prog)
			a9->diag.vcom_ok++;
		else
			a9->diag.vcom_mismatch++;
		if (!r) {
			a9->diag.vcom_last = now;
			a9->diag.vcom_last_ms = ktime_to_ms(ktime_get());
		}
		spin_unlock_irqrestore(&a9->diag.lock, flags);
	}
	dev_info_ratelimited(a9->dev, "pg-observe: raw %d sample_us %lld mode %#x bin %d t1 %d t2 %d"
		" rect %d,%d-%d,%d clear %d, VCOM 0x%02x programmed 0x%02x r %d (stock telemetry only)\n",
		st->pg_raw, st->pg_at_us, a9->last.mode, a9->last.bin, a9->last.t1, a9->last.t2,
		a9->last.rect.x1, a9->last.rect.y1, a9->last.rect.x2, a9->last.rect.y2,
		a9->last.clear, now, prog, r);
	if (!r && now != prog)
		dev_warn(a9->dev, "pg-observe: VCOM register 0x%02x != programmed 0x%02x at a PG-low sample"
			 " (FP9929A reset?); log only, the next power-up rewrites it\n", now, prog);
}

/* No-op validation never energizes an off pipeline. */
static int a9_check_live_rails(struct a9_eink *a9)
{
	struct fp9929a_status st;
	bool was_on = READ_ONCE(a9->tx.on);
	int ret;

	if (!was_on)
		return 0;
	ret = a9_hv_sample(a9, &st);
	return a9_check_rail_sample(a9, was_on, ret, &st);
}

static int a9_power_on(struct a9_eink *a9)
{
	int ret;

	if (a9x_fault_latched())
		return -EIO;	/* stall or rail fault latched: a9_eink.clear_fault */

	if (a9->hv_fault) {
		a9_power_off(a9);
		if (a9->hv_fault)
			return -EIO;	/* still unknown: refuse */
	}
	if (a9->tx.on)
		return a9_check_live_rails(a9);
	if (!a9x_attached(&a9->tx)) {
		ret = a9x_attach(&a9->tx);
		if (ret)
			return ret;
	}
	ret = a9x_power_on(&a9->tx);
	if (ret) {
		a9_power_off(a9);
		return ret;
	}
	if (!a9_hv_is_on(a9)) {
		/* a gate refused (panel or fp9929a) or the PMIC failed: drive nothing */
		dev_err(a9->dev, "FP9929A is not on after enable: no frames sent "
			"(panel_hisense_hlte556n_eink.enable / fp9929a.allow_power_on?)\n");
		a9_power_off(a9);
		return -EIO;
	}
	/* Fresh, completed power-up only: a warm validation never resets these.
	 * Keep id monotonic and the previous rail-fault capture frozen. */
	{
		u64 id = a9->session.id + 1;

		memset(&a9->session, 0, sizeof(a9->session));
		a9->session.id = id;
		a9->session.frame0 = a9->tx.frames;
	}
	return 0;
}

/* ---- one update ------------------------------------------------------------------- */
struct a9_job {
	u32 mode;
	int clear, mask;
	bool dry;
	struct a9e_rect rect;
	bool tm_ok;			/* tm lists every grey change since the previous job */
	u8 tm[A9E_NTILES];
};

static int a9_run_frames(struct a9_eink *a9, bool dry, struct sha256_ctx *sc)
{
	int more, ret;

	do {
		more = a9e_frame(&a9->e, a9->frame);
		if (sc)
			sha256_update(sc, a9->frame, A9T_FRAME_BYTES);
		if (!dry) {
			/* emergency stop: enable=0 mid-update aborts after this frame */
			if (!READ_ONCE(enable))
				return -ECANCELED;
			ret = a9x_show(&a9->tx, a9->frame);
			if (ret)
				return ret;
		}
	} while (more);
	/* never hold a driving frame, whatever the table's last column says */
	if (!dry && !a9e_frame_is_null(a9->frame)) {
		dev_warn_once(a9->dev, "waveform pass ended on a driving frame: blanking\n");
		return a9x_blank(&a9->tx);
	}
	return 0;
}

/*
 * ---- render-ahead (patch 0033) -------------------------------------------------
 * A real update's frames are rendered by the producer thread into the ring
 * (a9_eink_ring.c) while the worker sends them. The worker holds eng_lock for
 * the whole update and LENDS the engine to the producer: between posting the
 * job and a9r_stop() returning only the producer touches a9->e, a9->frame and
 * the hash context.
 */
static void a9_produce(struct a9_eink *a9)
{
	struct a9_ring *r = &a9->ring;
	bool init = a9->rjob_init, null = true;
	int bin = a9->rjob_bin, err = 0, more;
	u8 *slot;

	if (init)
		a9e_begin_init(&a9->e, bin);
	else
		a9e_start(&a9->e, bin);
	for (;;) {
		ktime_t r0;
		u32 us;

		slot = a9r_slot(r);
		if (!slot) {
			err = -ECANCELED;
			break;
		}
		r0 = ktime_get();
		more = a9e_frame(&a9->e, a9->frame);
		if (!init && a9->rjob_sc)
			sha256_update(a9->rjob_sc, a9->frame, A9T_FRAME_BYTES);
		err = a9r_pack(a9->frame, slot, &null);
		us = (u32)ktime_us_delta(ktime_get(), r0);
		if (us > a9->render_max_us)
			a9->render_max_us = us;
		if (err) {
			dev_err(a9->dev, "frame with drive data outside the data region: update aborted\n");
			break;
		}
		a9r_publish(r, null);
		if (more)
			continue;
		if (!null) {
			/* never hold a driving frame, whatever the table's last column says */
			dev_warn_once(a9->dev, "waveform pass ended on a driving frame: blanking\n");
			slot = a9r_slot(r);
			if (!slot) {
				err = -ECANCELED;
				break;
			}
			memset(slot, 0, A9R_DATA);
			a9r_publish(r, true);
		}
		if (init) {
			a9e_init_end(&a9->e);
			a9->ring_init_n = r->head;	/* only the producer moves head */
			init = false;
			a9e_start(&a9->e, bin);
			continue;
		}
		break;
	}
	a9r_finish(r, err);
}

static int a9_render_thread(void *data)
{
	struct a9_eink *a9 = data;

	/* Own the policy in kernel; Android chrt may be absent or lack caps. */
	sched_set_normal(current, -10);
	for (;;) {
		wait_event_interruptible(a9->rwq, READ_ONCE(a9->rjob) || kthread_should_stop());
		/* Pairs with run_ring's release, before ANY engine/job field read. */
		if (!smp_load_acquire(&a9->rjob)) {
			if (kthread_should_stop())
				break;
			continue;
		}
		/* Accept a posted borrower even on teardown; stop must join it. */
		WRITE_ONCE(a9->rjob, false);
		a9_produce(a9);
	}
	return 0;
}

/*
 * Real update with the pipeline up (idle frame on the glass): post the job,
 * wait for the initial watermark (or the update completely rendered), then send.
 * Any error: request stop, establish neutral/power down BEFORE joining the
 * producer. Unexpected failures latch the trial off; enable=0 cancellation
 * remains distinct. *init_shown: every init-flash frame was sent.
 */
#define A9_PREFILL_MS 2000
static int a9_run_ring(struct a9_eink *a9, bool init, int bin, struct sha256_ctx *sc,
		       bool *init_shown)
{
	u64 f0 = a9->tx.frames;
	ktime_t p0 = ktime_get();
	unsigned int watermark = READ_ONCE(prefill_frames);
	bool full, clear, transition;
	int ret;

	if (a9x_fault_latched())
		return -EIO;	/* writing enable alone cannot retry a failed trial */
	/* FINAL prepared landscape rectangle, including forced mode/Clear expansion.
	 * These are waveform-job counts, never a charge/PG-continuity estimate. */
	full = a9->e.rect.x1 == 0 && a9->e.rect.y1 == 0 &&
	       a9->e.rect.x2 == A9T_W && a9->e.rect.y2 == A9T_H;
	clear = !!a9->e.clear;
	transition = a9->e.req.kind == 1;
	a9->session.waveforms++;
	if (full)
		a9->session.full_started++;
	if (clear)
		a9->session.clears++;
	if (transition)
		a9->session.transitions++;
	a9r_reset(&a9->ring);
	a9->ring_init_n = 0;
	a9->rjob_init = init;
	a9->rjob_bin = bin;
	a9->rjob_sc = sc;
	/* Publish prepared engine, init/bin and optional stack hash as one job. */
	smp_store_release(&a9->rjob, true);
	wake_up(&a9->rwq);
	ret = a9r_prefill(&a9->ring, &a9->tx, A9_PREFILL_MS, &enable, watermark);
	a9->prefill_us = (u32)ktime_us_delta(ktime_get(), p0);
	if (ret)
		dev_err(a9->dev, "render-ahead prefill failed (%d): update not started\n", ret);
	else
		ret = a9r_drive(&a9->ring, &a9->tx, &enable);
	if (ret == -EAGAIN)
		dev_err(a9->dev, "render-ahead underrun after %llu frames: update aborted\n",
			a9->tx.frames - f0);
	if (ret) {
		bool cancelled = ret == -ECANCELED && !READ_ONCE(enable);

		a9r_request_stop(&a9->ring);
		if (!cancelled) {
			/* A genuine underrun/error must dominate a racing timer; only
			 * post-cutoff -EIO belongs to the existing timing event. */
			if (ret != -EIO || !atomic_read(&a9x_stall_fault))
				WRITE_ONCE(a9->tx.transport_error, true);
			if (!atomic_read(&a9x_stall_fault))
				WRITE_ONCE(a9->tx.stall_kind, 2);
			/* A partial prefix is not stock waveform/DC balance. No retry. */
			atomic_set(&a9x_stall_fault, 1);
			WRITE_ONCE(enable, false);
			dev_err(a9->dev, "render-ahead trial failed (%d): fault latched\n", ret);
		}
		if (a9x_blank(&a9->tx))
			a9x_emergency_off(&a9->tx, "a9_eink: render-ahead abort neutral failed");
		/* Transport/PMIC state only: engine and stack hash remain borrowed. */
		a9_power_off(a9);
	}
	a9r_stop(&a9->ring);	/* the producer has left the engine */
	/* Include a failed/partial prefix, even when cleanup powered down before
	 * joining. Diagnostic accounting must never move that safety ordering. */
	if (full)
		a9->session.full_frames += a9->tx.frames - f0;
	if (!ret) {
		a9->session.waveforms_done++;
		if (full)
			a9->session.full_done++;
		if (clear)
			a9->session.clears_done++;
		if (transition)
			a9->session.transitions_done++;
	}
	*init_shown = init && a9->ring_init_n && a9->tx.frames - f0 >= a9->ring_init_n;
	return ret;
}

/*
 * An update refused after its a9e_prepare() but before any frame (power-up
 * refused, temperature, bin): the glass is untouched, so the levels go back
 * to what they were (a9e_prepare may have set them to the last step's level,
 * a9e_assume_white to 15). a9e_prepare changes nothing else that outlives
 * the update: the previous mode only changes in a9e_end, and the step arrays
 * it wrote are rewritten by the next prepare that reads them.
 */
static void a9_unprepare(struct a9_eink *a9, bool dry)
{
	mutex_lock(&a9->eng_lock);
	memcpy(a9->e.old, dry ? a9->dry_save_old : a9->prep_save_old, A9T_N);
	a9e_inc_invalidate(&a9->e);	/* old no longer equals nlv */
	mutex_unlock(&a9->eng_lock);
}

static void __a9_update(struct a9_eink *a9, const struct a9_job *job)
{
	bool dry = job->dry;
	bool hash = dry || READ_ONCE(trace);
	bool *init_pending = dry ? &a9->dry_init_pending : &a9->init_pending;
	bool *force_clear = dry ? &a9->dry_force_clear : &a9->force_clear;
	struct sha256_ctx sc;
	struct fp9929a_status st, pg_st;
	bool was_on, pg_low = false;
	ktime_t t0 = ktime_get();
	unsigned int zt = READ_ONCE(zero_test);
	bool prepared = false, init_white = false;
	u64 sent0 = a9->tx.frames, ld0 = a9->tx.late_drive;
	u32 pre = 0;
	int t1, t2, bin, ret, steps = 0, lo, hi;

	if (a9x_fault_latched()) {
		dev_err_ratelimited(a9->dev, "update refused: %s fault latched (write 1 to a9_eink.clear_fault)\n",
				    atomic_read(&a9x_rail_fault) ? "rail" : "frame-progress");
		a9->aborts++;
		return;
	}
	/* attaching only looks the msm card up and allocates buffers: also done in
	 * a dry run, so the transport side can be checked without any commit */
	if (!a9x_attached(&a9->tx)) {
		ret = a9x_attach(&a9->tx);
		if (ret && ret != -EAGAIN)
			dev_warn_ratelimited(a9->dev, "transport attach: %d\n", ret);
	}
	if (!dry && !zt) {
		/*
		 * Bin pre-filter before any power-up, never a fallback (review 8):
		 * the thermistor must read; t1 is the PREVIOUS power-up's TMST if
		 * there is one (no temp_fallback), else the pre-filter is skipped.
		 * It can only refuse. The real admission is after power-up, with
		 * this power-up's valid TMST, before any frame.
		 */
		was_on = READ_ONCE(a9->tx.on);
		ret = a9_temps(a9, &t1, &t2, &st);
		if (a9_check_rail_sample(a9, was_on, ret, &st)) {
			a9->aborts++;
			return;
		}
		if (a9->last.t2_raw == INT_MIN) {
			dev_err(a9->dev, "no thermistor reading: update refused (no fallback)\n");
			a9->aborts++;
			return;
		}
		mutex_lock(&a9->eng_lock);
		bin = a9t_temp_bin(&a9->e.st, t1, t2);
		mutex_unlock(&a9->eng_lock);
		if (a9->last.t1_raw != INT_MIN && !a9_bin_admitted(bin, &lo, &hi)) {
			dev_err(a9->dev, "bin %d outside [%d,%d] before power-up (t1 %d from the previous power-up, t2 %d): update refused\n",
				bin, lo, hi, t1, t2);
			a9->aborts++;
			return;
		}
	}
	/*
	 * Frame pacing (boot 12: a 47.8 ms gap between the init flash and the
	 * update): everything of the update that does not depend on the
	 * temperature bin (request builder, step arrays, conditioning, pack)
	 * runs here, BEFORE the pipeline powers up, so nothing heavy runs
	 * between frames with HV on. The init flash, if pending, uses neither
	 * the levels nor the arrays, so the update is prepared on the levels
	 * the flash leaves (white) and the flash then ends without touching
	 * them (a9e_init_end).
	 */
	if (!zt || dry) {
		ktime_t p0 = ktime_get();

		mutex_lock(&a9->eng_lock);
		/*
		 * eink-latency r2, stock 0x1a6ee4: the same target as the last
		 * completed update, from the levels it left, is a guaranteed
		 * "unchanged" prepare (a9e_same_target compares grey AND old):
		 * drop it before conditioning, exactly as the unchanged path below
		 * drops it after. Nothing is sent, nothing is consumed or renewed.
		 */
		if (!dry && !*init_pending && !*force_clear && !job->clear &&
		    READ_ONCE(skip_same_target) &&
		    a9e_same_target(&a9->e, &a9->same, job->mode, 0)) {
			mutex_unlock(&a9->eng_lock);
			if (a9_check_live_rails(a9))
				a9->aborts++;
			return;
		}
		if (dry) {
			/* a dry run must not change what the real pipeline believes */
			memcpy(a9->dry_save_old, a9->e.old, A9T_N);
			a9->dry_save_pre = a9->e.pre_mode;
			a9->dry_save_have_pre = a9->e.have_pre;
		} else {
			memcpy(a9->prep_save_old, a9->e.old, A9T_N);
		}
		pre = a9->e.have_pre ? a9->e.pre_mode : job->mode;
		init_white = *init_pending;
		if (init_white)
			a9e_assume_white(&a9->e);
		/* the levels the update starts from (trace / E5a levels_before) */
		if (hash)
			memcpy(a9->trace_old, a9->e.old, A9T_N);
		a9->e.inc_enable = READ_ONCE(damage_prep) && a9->e.ee;
		a9->e.dmg = job->tm_ok ? job->tm : NULL;
		steps = a9e_prepare(&a9->e, job->mode, job->clear, &job->rect, job->mask);
		prepared = true;
		a9->last.prep_us = (u32)ktime_us_delta(ktime_get(), p0);
		/* Only complete real history is eligible. A coalesced reversion or
		 * quantized-equivalent target sends nothing: no power/start/end,
		 * update count, clear/init consumption or HV-budget renewal.
		 */
		if (a9->e.unchanged && !dry && !init_white && !*force_clear) {
			mutex_unlock(&a9->eng_lock);
			if (a9_check_live_rails(a9))
				a9->aborts++;
			return;
		}
		mutex_unlock(&a9->eng_lock);
	}
	if (!dry) {
		ret = a9_power_on(a9);
		if (ret) {
			dev_warn_ratelimited(a9->dev, "update dropped: pipeline not available (%d)\n", ret);
			a9->aborts++;
			if (prepared)
				a9_unprepare(a9, dry);
			return;
		}
	}
	was_on = !dry && READ_ONCE(a9->tx.on);
	ret = a9_temps(a9, &t1, &t2, &st); /* this power-up's TMST */
	if (a9_check_rail_sample(a9, was_on, ret, &st)) {
		a9->aborts++;
		if (prepared)
			a9_unprepare(a9, dry);
		return;
	}
	if ((a9->last.t1_raw != INT_MIN && a9->last.t1_raw < A9_TEMP_MIN_C) ||
	    (a9->last.t2_raw != INT_MIN && a9->last.t2_raw < A9_TEMP_MIN_C * 1000)) {
		dev_err(a9->dev, "panel temperature below %d C (FP9929A %d C, epd_therm %d mC): update refused\n",
			A9_TEMP_MIN_C, a9->last.t1_raw, a9->last.t2_raw);
		a9->aborts++;
		if (!dry)
			a9_power_off(a9);
		if (prepared)
			a9_unprepare(a9, dry);
		return;
	}
	/*
	 * Review 8: a real waveform update needs VALID samples from THIS
	 * power-up (fp9929a clears have_temp at every power-up start) and the
	 * thermistor; temp_fallback is never used to admit drive frames.
	 */
	if (!dry && !zt && (a9->last.t1_raw == INT_MIN || a9->last.t2_raw == INT_MIN)) {
		dev_err(a9->dev, "no valid fresh temperature (FP9929A %s, thermistor %s): update refused\n",
			a9->last.t1_raw == INT_MIN ? "missing" : "ok",
			a9->last.t2_raw == INT_MIN ? "missing" : "ok");
		a9->aborts++;
		a9_power_off(a9);
		if (prepared)
			a9_unprepare(a9, dry);
		return;
	}

	if (!dry && zt) {
		unsigned int i, n = min(zt, 850U);	/* <= 10 s at 85 Hz */

		/* the idle frame: template, byte 0 = 0 (gates scan, nothing driven) */
		memcpy(a9->frame, a9->ct, A9T_FRAME_BYTES);
		for (i = 0; i < n; i++) {
			ret = READ_ONCE(enable) ? a9x_show(&a9->tx, a9->frame) : -ECANCELED;
			if (ret)
				break;
		}
		mutex_lock(&a9->eng_lock);
		a9->last.mode = 0;
		a9->last.pre = 0;
		a9->last.clear = 0;
		a9->last.type = 0;
		a9->last.bin = a9t_temp_bin(&a9->e.st, t1, t2);
		a9->last.t1 = t1;
		a9->last.t2 = t2;
		a9->last.frames = i;
		a9->last.steps = 0;
		a9->last.dry = 0;
		a9->last.aborted = ret ? 1 : 0;
		a9->last.sha_valid = false;
		a9->last.total_us = (u32)ktime_us_delta(ktime_get(), t0);
		if (ret)
			a9->aborts++;
		else
			a9->updates++;
		mutex_unlock(&a9->eng_lock);
		dev_info(a9->dev, "zero test: %u/%u template frames with HV on (%d)\n", i, n, ret);
		if (ret)
			a9_power_off(a9);
		return;
	}

	mutex_lock(&a9->eng_lock);
	bin = a9t_temp_bin(&a9->e.st, t1, t2);
	/* bin admission with this power-up's samples, before any frame */
	if (!a9_bin_admitted(bin, &lo, &hi)) {
		if (dry) {
			dev_info(a9->dev, "dry run: bin %d outside [%d,%d]: a real update would be refused\n",
				 bin, lo, hi);
		} else {
			a9->last.bin = bin;
			a9->last.t1 = t1;
			a9->last.t2 = t2;
			a9->last.frames = 0;
			a9->last.aborted = 1;
			memcpy(a9->e.old, a9->prep_save_old, A9T_N);	/* glass untouched */
			a9e_inc_invalidate(&a9->e);
			mutex_unlock(&a9->eng_lock);
			dev_err(a9->dev, "bin %d outside [%d,%d]: update refused\n", bin, lo, hi);
			a9->aborts++;
			a9_power_off(a9);
			return;
		}
	}
	a9->last.pre = pre;
	if (!dry) {
		/* render-ahead: init flash (if pending) + update, in one sequence */
		bool init_shown = false;
		s64 pg_first;

		if (hash)
			sha256_init(&sc);
		a9->last.begin_us = (u32)ktime_us_delta(ktime_get(), t0);
		ret = a9_run_ring(a9, init_white, bin, hash ? &sc : NULL, &init_shown);
		a9->wf_end = ktime_get();	/* rail-fault diagnosis (0039) */
		a9->wf_ret = ret;
		pg_first = a9_diag_wf_end(a9);	/* 0052: one recorded, never-acted-on PG read */
		/* 0053 (M4): sample only under eng_lock; settle/latch after the unlock */
		pg_low = !ret && a9_pg_wf_end_low(a9, pg_first, &pg_st);
		if (init_shown)
			*init_pending = false;
		if (ret)
			goto abort;
		*init_pending = false;
	} else {
		if (init_white) {
			/* prepared on old := 15, what the flash leaves (INFERRED: glass white) */
			a9e_begin_init(&a9->e, bin);
			ret = a9_run_frames(a9, dry, NULL);
			a9e_init_end(&a9->e);
			if (ret)
				goto abort;
			*init_pending = false;
		}
		if (hash)
			sha256_init(&sc);
		a9e_start(&a9->e, bin);
		a9->last.begin_us = (u32)ktime_us_delta(ktime_get(), t0);
		ret = a9_run_frames(a9, dry, hash ? &sc : NULL);
		if (ret)
			goto abort;
	}
	a9e_end(&a9->e);
	if (!dry)
		a9e_same_record(&a9->e, &a9->same);	/* old == nlv now; a dry run keeps the real record */

	a9->last.mode = a9->e.mode;
	a9->last.clear = a9->e.clear;
	a9->last.type = a9->e.type;
	a9->last.bin = bin;
	a9->last.t1 = t1;
	a9->last.t2 = t2;
	a9->last.frames = a9->e.nframes;
	a9->last.steps = steps;
	a9->last.rect = a9->e.rect;
	a9->last.dry = dry;
	a9->last.aborted = 0;
	a9->last.sha_valid = hash;
	if (hash)
		sha256_final(&sc, a9->last.sha);
	a9->last.total_us = (u32)ktime_us_delta(ktime_get(), t0);
	a9->updates++;
	*force_clear = false;
	if (!dry && READ_ONCE(log_updates))
		dev_info(a9->dev, "update done: mode %#x pre %#x clear %d steps %d bin %d init %d rect %d,%d-%d,%d sent %llu late_drive +%llu\n",
			 a9->e.mode, pre, a9->e.clear, steps, bin, init_white, a9->e.rect.x1,
			 a9->e.rect.y1, a9->e.rect.x2, a9->e.rect.y2, a9->tx.frames - sent0,
			 a9->tx.late_drive - ld0);
	if (dry) {
		/* the debugfs "levels" of a dry run are the model's; restore the real one */
		memcpy(a9->dry_levels, a9->e.old, A9T_N);
		memcpy(a9->e.old, a9->dry_save_old, A9T_N);
		a9->e.pre_mode = a9->dry_save_pre;
		a9->e.have_pre = a9->dry_save_have_pre;
		a9e_inc_invalidate(&a9->e);
	}
	mutex_unlock(&a9->eng_lock);
	/* Post-waveform PG/VCOM diagnostics never affect the update result. */
	if (pg_low)
		a9_pg_wf_end(a9, &pg_st);
	return;

abort:
	dev_err(a9->dev, "update aborted after %u frames (%d): forcing a clear next\n",
		a9->e.nframes, ret);
	/* the record describes THIS update (it used to keep the previous one's) */
	a9->last.mode = a9->e.mode;
	a9->last.pre = pre;
	a9->last.clear = a9->e.clear;
	a9->last.type = a9->e.type;
	a9->last.bin = bin;
	a9->last.t1 = t1;
	a9->last.t2 = t2;
	a9->last.frames = (u32)(a9->tx.frames - sent0);	/* sent (real) */
	a9->last.steps = steps;
	a9->last.rect = a9->e.rect;
	a9->last.dry = dry;
	a9->last.sha_valid = false;
	if (!dry)
		dev_info(a9->dev, "update aborted: mode %#x pre %#x clear %d steps %d bin %d init %d rect %d,%d-%d,%d sent %llu (incl. init) late_drive +%llu (%d): the glass holds a partial waveform until the forced clear\n",
			 a9->e.mode, pre, a9->e.clear, steps, bin, init_white, a9->e.rect.x1,
			 a9->e.rect.y1, a9->e.rect.x2, a9->e.rect.y2, a9->tx.frames - sent0,
			 a9->tx.late_drive - ld0, ret);
	a9e_abort(&a9->e);
	if (!dry)
		a9->same.valid = 0;	/* review 88: the record never describes an aborted glass */
	a9->last.aborted = 1;
	a9->aborts++;
	*force_clear = true;
	if (dry) {
		memcpy(a9->e.old, a9->dry_save_old, A9T_N);
		a9->e.pre_mode = a9->dry_save_pre;
		a9->e.have_pre = a9->dry_save_have_pre;
		a9e_inc_invalidate(&a9->e);
	}
	mutex_unlock(&a9->eng_lock);
	if (!dry)
		a9_power_off(a9);
}

/*
 * The blocking msm atomic tail runs inline in this task. Give real updates
 * FIFO 50 in kernel, independent of Android's chrt availability/capabilities.
 * It sleeps for hardware flush/vblank; the render producer stays OTHER/-10.
 * Dry runs and the idle worker use OTHER/0. IRQ interlocks remain independent;
 * neither this policy nor a nice workqueue guarantees delivery under a wedged
 * kernel. Do not pin both tasks to one CPU without measured affinity evidence.
 */
static void a9_update(struct a9_eink *a9, const struct a9_job *job)
{
	if (job->dry) {
		sched_set_normal(current, 0);
		__a9_update(a9, job);
		return;
	}
	sched_set_fifo(current);
	__a9_update(a9, job);
	sched_set_normal(current, 0);
}

/* ---- worker ------------------------------------------------------------------------ */
static bool a9_take_job(struct a9_eink *a9, struct a9_job *job)
{
	bool have = false, recovery;

	mutex_lock(&a9->shadow_lock);
	if (a9->kick) {
		a9->kick = false;
		job->dry = !READ_ONCE(enable);
		if (a9->controls_dirty) {
			mutex_lock(&a9->eng_lock);
			a9t_set_controls(&a9->e.st, a9->controls[0], a9->controls[1], a9->controls[2]);
			a9t_set_clear_shadow(&a9->e.st, a9->controls[3]);
			mutex_unlock(&a9->eng_lock);
			a9->controls_dirty = false;
		}
		/* Userspace can request a manual clear, never waive recovery. The
		 * worker owns these recovery flags; engine history is read locked.
		 * A manual request during pending mode entry uses the conservative
		 * GC16 path. Ordinary entry still uses the existing request builder.
		 */
		recovery = a9->test_pattern || a9->clear_req || a9->hv_fault ||
			a9x_fault_latched() ||
			(job->dry ? a9->dry_force_clear || a9->dry_init_pending :
			 a9->force_clear || a9->init_pending);
		mutex_lock(&a9->eng_lock);
		recovery |= !a9->e.have_pre || (a9->manual_clear_req &&
			a9->e.pre_mode != (a9->mode & ~0x100u));
		mutex_unlock(&a9->eng_lock);
		if (recovery) {
			/* full GC16 of the newest image; the steady mode stays as set */
			job->mode = A9_MODE_CLEAR;
			job->clear = 1;
			job->mask = 0;
			job->rect = a9e_rect_full();
		} else if (a9->manual_clear_req) {
			job->mode = a9->mode;
			job->clear = 1;
			job->mask = 0;
			job->rect = a9e_rect_full();
		} else {
			job->mode = a9->mode;
			job->clear = 0;
			job->mask = READ_ONCE(mask_outside_damage);
			job->rect = a9->dirty;
		}
		/* The selected full job satisfies both snapshotted requests. A
		 * request written during this job remains queued for its successor.
		 */
		a9->clear_req = false;
		a9->manual_clear_req = false;
		a9->dirty = (struct a9e_rect){ 0, 0, 0, 0 };
		/* Damage tiles: valid only if every convert since the previous job was tracked
		 * and neither this nor the previous job used the test pattern as its grey. */
		job->tm_ok = READ_ONCE(damage_prep) && !a9->tm_lost && !a9->test_pattern &&
			     !a9->prev_pattern;
		if (job->tm_ok)
			memcpy(job->tm, a9->dirty_tm, sizeof(job->tm));
		memset(a9->dirty_tm, 0, sizeof(a9->dirty_tm));
		a9->tm_lost = false;
		a9->prev_pattern = a9->test_pattern;
		if (a9->test_pattern)
			a9e_pattern_contrast(a9->e.grey);
		else
			memcpy(a9->e.grey, a9->shadow, A9T_N);
		have = job->clear || !a9e_rect_empty(&job->rect);
	}
	mutex_unlock(&a9->shadow_lock);
	return have;
}

static unsigned int a9_hold_ms(void)
{
	return min(READ_ONCE(hv_hold_ms), 10000u);
}

/* Orderly-close budget for the idle wait: hv_max_ms (0 = unset), never above
 * 15 s, i.e. 5 s inside the FP9929A 20 s hard cap (fp9929a.max_on_ms, which is
 * neither changed nor consulted here). The between-job hv_max_ms check is
 * unchanged.
 */
#define A9_IDLE_BUDGET_MS 15000u

static long a9_idle_clip(bool on, ktime_t on_since, long tmo)
{
	unsigned int cap = READ_ONCE(hv_max_ms);
	s64 left;

	if (!on)
		return tmo;
	cap = cap ? min(cap, A9_IDLE_BUDGET_MS) : A9_IDLE_BUDGET_MS;
	left = (s64)cap - ktime_ms_delta(ktime_get(), on_since);
	if (left <= 0)
		return 0;
	return min(tmo, (long)msecs_to_jiffies(left));
}

/* Idle hold for the CURRENT power session: the base hold, raised to the
 * interactive hold only when the boost was earned in this very session
 * (same on_since) and the pipeline is still on. A zero base hold is never
 * extended.
 */
static unsigned int a9_session_hold(unsigned int hold, bool on, ktime_t on_since,
				    bool boosted, ktime_t boost_since)
{
	if (!hold || !boosted || !on || boost_since != on_since)
		return hold;
	return max(hold, min(READ_ONCE(hv_hold_interactive_ms), 10000u));
}

/* Remaining soft idle hold, independent of the absolute HV-on origins. */
static long a9_idle_timeout(unsigned int hold, ktime_t idle_since)
{
	s64 left = (s64)hold - ktime_ms_delta(ktime_get(), idle_since);

	return left > 0 ? msecs_to_jiffies(left) : 0;
}

/*
 * eink-latency r2 prewarm: the cold path of __a9_update() up to (not including)
 * its first frame, with nothing prepared and no engine state touched: fault latch,
 * thermistor + previous-TMST bin pre-filter, a9_power_on() (panel/fp9929a gates,
 * FP9929A PG), this power-up's TMST + rail sample, temperature floor and bin
 * admission. Any refusal leaves the pipeline off. Worker thread only.
 */
static int a9_prewarm_up(struct a9_eink *a9)
{
	struct fp9929a_status st;
	int t1, t2, bin, lo, hi, ret;

	if (a9x_fault_latched() || !READ_ONCE(enable) || READ_ONCE(zero_test) || a9->hv_fault)
		return -EIO;
	if (!a9x_attached(&a9->tx)) {
		ret = a9x_attach(&a9->tx);
		if (ret)
			return ret;
	}
	ret = a9_temps(a9, &t1, &t2, &st);
	if (a9_check_rail_sample(a9, false, ret, &st))
		return -EIO;
	if (a9->last.t2_raw == INT_MIN)
		return -ENODATA;
	mutex_lock(&a9->eng_lock);
	bin = a9t_temp_bin(&a9->e.st, t1, t2);
	mutex_unlock(&a9->eng_lock);
	if (a9->last.t1_raw != INT_MIN && !a9_bin_admitted(bin, &lo, &hi))
		return -ERANGE;
	ret = a9_power_on(a9);
	if (ret)
		return ret;
	ret = a9_temps(a9, &t1, &t2, &st);	/* this power-up's TMST */
	if (a9_check_rail_sample(a9, true, ret, &st))
		return -EIO;			/* latched and powered down by the check */
	mutex_lock(&a9->eng_lock);
	bin = a9t_temp_bin(&a9->e.st, t1, t2);
	mutex_unlock(&a9->eng_lock);
	if (a9->last.t1_raw == INT_MIN || a9->last.t2_raw == INT_MIN ||
	    a9->last.t1_raw < A9_TEMP_MIN_C || a9->last.t2_raw < A9_TEMP_MIN_C * 1000 ||
	    !a9_bin_admitted(bin, &lo, &hi)) {
		a9_power_off(a9);
		return -ERANGE;
	}
	return 0;
}

/* worker only: a prewarm session ends (powered down, or about to be) without an update */
static void a9_prewarm_expired(struct a9_eink *a9)
{
	if (a9->prewarm_session && a9->tx.on) {
		/* the prewarm saw no update: rate-limit the next one */
		a9->prewarm_unused++;
		WRITE_ONCE(a9->prewarm_block_until,
			   ktime_add_ms(ktime_get(), clamp(READ_ONCE(prewarm_gap_ms), 3000u, 60000u)));
	}
	a9->prewarm_session = false;
}

/* input event context (atomic, event_lock held): only flags and a wake-up.
 * No prewarm with a zero hold: it would be powered down at once (review 88 C4). */
static void a9_prewarm_poke(struct a9_eink *a9)
{
	if (!READ_ONCE(prewarm) || !READ_ONCE(hv_hold_ms) || READ_ONCE(a9->tx.on) ||
	    READ_ONCE(a9->suspending) || READ_ONCE(a9->prewarm_req))
		return;
	atomic_long_inc(&a9->prewarm_pokes);
	if (ktime_before(ktime_get(), READ_ONCE(a9->prewarm_block_until)))
		return;
	WRITE_ONCE(a9->prewarm_req, true);
	wake_up(&a9->wq);
}

static void a9_input_event(struct input_handle *handle, unsigned int type,
			   unsigned int code, int value)
{
	if ((type == EV_KEY && code == BTN_TOUCH && value == 1) ||
	    (type == EV_ABS && code == ABS_MT_TRACKING_ID && value >= 0))
		a9_prewarm_poke(handle->handler->private);
}

static int a9_input_connect(struct input_handler *handler, struct input_dev *dev,
			    const struct input_device_id *id)
{
	struct input_handle *h = kzalloc(sizeof(*h), GFP_KERNEL);
	int ret;

	if (!h)
		return -ENOMEM;
	h->dev = dev;
	h->handler = handler;
	h->name = "a9_eink_prewarm";
	ret = input_register_handle(h);
	if (ret)
		goto free;
	ret = input_open_device(h);	/* an extra listener, never a grab */
	if (ret)
		goto unreg;
	return 0;
unreg:
	input_unregister_handle(h);
free:
	kfree(h);
	return ret;
}

static void a9_input_disconnect(struct input_handle *h)
{
	input_close_device(h);
	input_unregister_handle(h);
	kfree(h);
}

/* touchscreens only: multitouch position or a BTN_TOUCH key */
static const struct input_device_id a9_input_ids[] = {
	{ .flags = INPUT_DEVICE_ID_MATCH_EVBIT | INPUT_DEVICE_ID_MATCH_ABSBIT,
	  .evbit = { BIT_MASK(EV_ABS) },
	  .absbit = { [BIT_WORD(ABS_MT_POSITION_X)] = BIT_MASK(ABS_MT_POSITION_X) } },
	{ .flags = INPUT_DEVICE_ID_MATCH_EVBIT | INPUT_DEVICE_ID_MATCH_KEYBIT,
	  .evbit = { BIT_MASK(EV_KEY) },
	  .keybit = { [BIT_WORD(BTN_TOUCH)] = BIT_MASK(BTN_TOUCH) } },
	{ },
};

/* Called only by the consumer after the producer has joined and shutdown
 * completed. Unknown/unreturned progress, failed neutral/DPMS, PG/rail faults and PMIC
 * hard latches never qualify. Grants are refilled only by clean real updates. */
static void a9_recover_stall(struct a9_eink *a9)
{
	struct a9x_transport *t = &a9->tx;
	int ret = -EPERM;

	hrtimer_cancel(&t->stall); /* join any hardirq before examining its reason */
	if (READ_ONCE(enable) || a9->suspending || a9->hv_fault || t->gone ||
	    t->on || !t->front_null || t->stall_armed || t->commit_active ||
	    (READ_ONCE(t->stall_kind) != 1 &&
	     !(READ_ONCE(t->stall_kind) == 3 && READ_ONCE(t->stall_commit_ok))) ||
	    READ_ONCE(t->transport_error) || msm_a9_commit_faulted() || atomic_read(&a9x_rail_fault) ||
	    a9->stall_retries >= 2)
		goto out;
	if (ktime_ms_delta(ktime_get(), t->stall_at) <
	    (a9->stall_retries ? 30000 : 5000)) {
		ret = -EAGAIN;
		goto out;
	}
	ret = fp9929a_recover_starvation(a9->pmic_np);
	if (ret)
		goto out;
	/* Full GC16 owed, including after a prior manual a9 clear_fault. */
	a9->force_clear = true;
	a9->same.valid = 0;
	atomic_set(&a9x_stall_fault, 0);
	WRITE_ONCE(t->stall_kind, 0);
	a9->stall_retries++;
	a9->stall_grants++;
	a9->stall_clean_since = 0;
	dev_info(a9->dev, "starvation recovery granted %u/2: gates remain closed, full Clear owed\n",
	         a9->stall_retries);
out:
	a9->recover_ret = ret;
	atomic_set(&a9->recover_req, 0);
	wake_up_all(&a9->wq);
}

/* Sole worker, after actual non-dry output. Soft events are diagnostics, not
 * grants, but restart the clean interval; idle time can count between successful
 * real updates. Never replenish while faulted or after an aborted update. */
static void a9_recovery_clean_progress(struct a9_eink *a9)
{
	u64 soft, hard;
	ktime_t now;
	unsigned long flags;
	bool refilled = false;

	raw_spin_lock_irqsave(&a9->tx.stall_lock, flags);
	soft = a9->tx.soft_stalls;
	hard = a9->tx.stalls;
	now = ktime_get();
	if (a9x_fault_latched() || a9->hv_fault || a9->last.aborted ||
	    READ_ONCE(a9->tx.transport_error) || msm_a9_commit_faulted()) {
		a9->stall_clean_since = 0;
		goto out;
	}
	if (!a9->stall_clean_since || soft != a9->stall_clean_soft ||
	    hard != a9->stall_clean_hard) {
		a9->stall_clean_since = now;
		a9->stall_clean_soft = soft;
		a9->stall_clean_hard = hard;
		goto out;
	}
	if (a9->stall_retries &&
	    ktime_ms_delta(now, a9->stall_clean_since) >= A9X_RECOVERY_CLEAN_MS) {
		a9->stall_retries = 0;
		a9->stall_clean_since = now;
		refilled = true;
	}
out:
	raw_spin_unlock_irqrestore(&a9->tx.stall_lock, flags);
	if (refilled)
		dev_info(a9->dev, "starvation recovery budget refilled after10min clean progress; total grants %llu\n",
		         a9->stall_grants);
}

static int a9_thread(void *data)
{
	struct a9_eink *a9 = data;
	struct a9_job job;
	ktime_t idle_since = 0, prev_output = 0, boost_since = 0;
	bool boosted = false, have_prev = false;

	while (!kthread_should_stop()) {
		unsigned int hold = a9_session_hold(a9_hold_ms(), a9->tx.on, a9->tx.on_since,
						    boosted, boost_since);
		bool physical = false;
		long tmo;

		if (READ_ONCE(a9->suspending)) {
			/* system suspend: rails down, history kept, wait for resume */
			WRITE_ONCE(a9->prewarm_req, false);
			a9->prewarm_session = false;
			a9_power_off(a9);
			have_prev = boosted = false;	/* no interaction across suspend */
			smp_store_release(&a9->parked, true);
			wake_up_all(&a9->wq);
			wait_event_interruptible(a9->wq, !READ_ONCE(a9->suspending) ||
						 kthread_should_stop());
			smp_store_release(&a9->parked, false);
			continue;
		}
		if (a9->hv_fault) {
			tmo = HZ;			/* retry the power-down, no busy loop */
			have_prev = boosted = false;	/* a fault ends the interaction */
		} else if (a9->tx.on)
			tmo = a9_idle_clip(true, a9->tx.on_since,
					   a9_idle_timeout(hold, idle_since));
		else
			tmo = MAX_SCHEDULE_TIMEOUT;
		if (tmo)
			wait_event_interruptible_timeout(a9->wq,
				READ_ONCE(a9->kick) || READ_ONCE(a9->suspending) ||
				READ_ONCE(a9->prewarm_req) ||
				READ_ONCE(a9->tx.unreg_hv_unknown) || atomic_read(&a9->recover_req) || kthread_should_stop(), tmo);
		if (kthread_should_stop())
			break;
		if (READ_ONCE(a9->suspending))
			continue;
		if (atomic_cmpxchg(&a9->recover_req, 1, 2) == 1) {
			a9_power_off(a9);
			a9_recover_stall(a9);
			continue;
		}
		if (!READ_ONCE(a9->kick) && READ_ONCE(a9->prewarm_req)) {
			/* eink-latency r2 prewarm: power up now, hold as after an update.
			 * busy before the request is cleared: a9_drain never sees a gap. */
			WRITE_ONCE(a9->busy, true);
			smp_mb();
			WRITE_ONCE(a9->prewarm_req, false);
			a9->prewarm_req_n++;
			if (READ_ONCE(prewarm) && a9_hold_ms() && !a9->tx.on && !a9->hv_fault) {
				int ret;

				ret = a9_load_waveform(a9);
				if (!ret)
					ret = a9_prewarm_up(a9);
				if (!ret) {
					idle_since = ktime_get();	/* the hold runs from the power-up */
					a9->prewarm_session = true;
					a9->prewarm_up++;
				} else {
					a9->prewarm_fail++;
				}
			}
			smp_store_release(&a9->busy, false);
			wake_up_all(&a9->wq);
			continue;
		}
		if (!READ_ONCE(a9->kick)) {
			a9_prewarm_expired(a9);
			a9_power_off(a9);	/* idle */
			continue;
		}
		WRITE_ONCE(a9->busy, true);
		smp_mb();	/* busy is visible before kick reads as cleared (a9_drain) */
		/* A new kick cannot erase an already-expired physical idle hold. */
		if (a9->tx.on && !a9->hv_fault && hold &&
		    !a9_idle_timeout(hold, idle_since)) {
			/* Observe a live dropout before closing the expired session. */
			a9_check_live_rails(a9);
			a9_prewarm_expired(a9);	/* review 88 C2: an expiring prewarm counts here too */
			a9_power_off(a9);
		}
		if (a9_load_waveform(a9)) {
			WRITE_ONCE(a9->kick, false);	/* dropped; retried at the next commit */
		} else {
			if (a9->tx.on && hv_max_ms &&
			    ktime_ms_delta(ktime_get(), a9->tx.on_since) > hv_max_ms)
				a9_power_off(a9);	/* bounded continuous HV time */
			if (a9_take_job(a9, &job)) {
				u64 frames0 = a9->tx.frames;
				u64 preps0 = a9->e.full_prepares + a9->e.inc_prepares;

				/* review 88 C3: a touch-down queued before or during this job is
				 * answered by the job itself; never by a later prewarm */
				WRITE_ONCE(a9->prewarm_req, false);
				a9_update(a9, &job);
				WRITE_ONCE(a9->prewarm_req, false);
				if (a9->e.full_prepares + a9->e.inc_prepares == preps0) {
					/* the job was dropped before its prepare: its damage tiles are
					 * gone, the damage-limited state no longer describes the grey */
					mutex_lock(&a9->eng_lock);
					a9e_inc_invalidate(&a9->e);
					mutex_unlock(&a9->eng_lock);
				}
				/* Only actual real output renews the idle hold. */
				if (!job.dry && a9->tx.on && a9->tx.frames != frames0) {
					idle_since = ktime_get();
					a9_recovery_clean_progress(a9);
					physical = true;
					a9->prewarm_session = false;	/* the prewarm was used */
					/* Output history may cross a session boundary (that is
					 * the point: the next tap after a power-down), but the
					 * boost is recomputed from it at every output, never
					 * carried by a flag, and is bound to this session.
					 */
					boosted = have_prev && ktime_ms_delta(idle_since, prev_output) <
						  min(READ_ONCE(hv_interactive_window_ms), 60000u);
					boost_since = a9->tx.on_since;
					prev_output = idle_since;
					have_prev = true;
					hold = a9_session_hold(a9_hold_ms(), a9->tx.on, a9->tx.on_since,
							       boosted, boost_since);
				}
			}
		}
		/* Expiry wins over queued duplicates. Keep failed-off HZ retries
		 * and hold=0 reuse for actual queued physical output.
		 */
		if (a9->tx.on && !a9->hv_fault &&
		    ((hold && !a9_idle_timeout(hold, idle_since)) ||
		     (!hold && (!READ_ONCE(a9->kick) || !physical))))
			a9_power_off(a9);
		smp_store_release(&a9->busy, false);
		wake_up_all(&a9->wq);	/* drainers */
	}
	a9_power_off(a9);
	return 0;
}

static bool a9_idle(struct a9_eink *a9)
{
	bool kick = READ_ONCE(a9->kick);

	smp_rmb();
	return !kick && !smp_load_acquire(&a9->busy);
}

/* finish every queued update (never truncate a waveform). Returns false on timeout. */
static bool a9_drain(struct a9_eink *a9)
{
	if (!a9->thread)
		return true;
	wake_up_all(&a9->wq);
	if (!wait_event_timeout(a9->wq, a9_idle(a9), msecs_to_jiffies(A9_DRAIN_MS))) {
		dev_warn(a9->dev, "drain timed out\n");
		return false;
	}
	return true;
}

/*
 * System sleep: msm saves and later restores its atomic state in .prepare
 * (drm_mode_config_helper_suspend), so the pipeline must be down BEFORE the
 * PM core starts: a PM notifier, run before the freezer and dpm_prepare.
 */
static int a9_pm_notify(struct notifier_block *nb, unsigned long action, void *data)
{
	struct a9_eink *a9 = container_of(nb, struct a9_eink, pm_nb);

	switch (action) {
	case PM_SUSPEND_PREPARE:
	case PM_HIBERNATION_PREPARE:
		a9_drain(a9);
		WRITE_ONCE(a9->suspending, true);
		wake_up_all(&a9->wq);
		if (!wait_event_timeout(a9->wq, smp_load_acquire(&a9->parked),
					msecs_to_jiffies(A9_DRAIN_MS)))
			a9_force_hv_off(a9, "worker did not park for suspend");
		break;
	case PM_POST_SUSPEND:
	case PM_POST_HIBERNATION:
	case PM_POST_RESTORE:
		WRITE_ONCE(a9->suspending, false);
		wake_up_all(&a9->wq);
		break;
	}
	return NOTIFY_DONE;
}

/* ---- debugfs ----------------------------------------------------------------------- */
static int a9_dbg_last_show(struct seq_file *m, void *v)
{
	struct a9_eink *a9 = m->private;

	mutex_lock(&a9->eng_lock);
	seq_printf(m, "mode %#x pre %#x clear %d type %u bin %d t1 %d t2 %d t1_raw %d t2_raw %d\n",
		   a9->last.mode, a9->last.pre, a9->last.clear, a9->last.type, a9->last.bin,
		   a9->last.t1, a9->last.t2, a9->last.t1_raw, a9->last.t2_raw);
	seq_printf(m, "rect %d,%d-%d,%d steps %u dry %d aborted %d\n",
		   a9->last.rect.x1, a9->last.rect.y1, a9->last.rect.x2, a9->last.rect.y2,
		   a9->last.steps, a9->last.dry, a9->last.aborted);
	if (a9->last.sha_valid)
		seq_printf(m, "frames %u sha256 %*phN\n", a9->last.frames, 32, a9->last.sha);
	else
		seq_printf(m, "frames %u sha256 -\n", a9->last.frames);
	mutex_unlock(&a9->eng_lock);
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(a9_dbg_last);

static void a9_debugfs(struct a9_eink *a9)
{
	static struct debugfs_blob_wrapper grey, old, lv, dlv;

	a9->dbg = debugfs_create_dir("a9_eink", NULL);
	grey.data = a9->e.grey;		/* grey of the last update */
	grey.size = A9T_N;
	old.data = a9->trace_old;	/* levels before it (trace / dry run) */
	old.size = A9T_N;
	lv.data = a9->e.old;		/* levels now on the glass */
	lv.size = A9T_N;
	debugfs_create_blob("grey", 0400, a9->dbg, &grey);
	debugfs_create_blob("levels_before", 0400, a9->dbg, &old);
	debugfs_create_blob("levels", 0400, a9->dbg, &lv);
	dlv.data = a9->dry_levels;	/* levels after the last dry-run update */
	dlv.size = A9T_N;
	debugfs_create_blob("dry_levels", 0400, a9->dbg, &dlv);
	debugfs_create_file("last", 0400, a9->dbg, a9, &a9_dbg_last_fops);
}

/* ---- probe -------------------------------------------------------------------------- */
static void a9_put_node(void *np)
{
	of_node_put(np);
}

static void *a9_vzalloc(struct device *dev, size_t n)
{
	void *p = vzalloc(n);

	if (p && devm_add_action_or_reset(dev, (void (*)(void *))vfree, p))
		return NULL;
	return p;
}

static int a9_pipe_init(struct a9_eink *a9)
{
	struct drm_device *drm = &a9->drm;
	int ret;

	drm_plane_helper_add(&a9->plane, &a9_plane_helper_funcs);
	ret = drm_universal_plane_init(drm, &a9->plane, 0, &a9_plane_funcs, a9_formats,
				       ARRAY_SIZE(a9_formats), NULL, DRM_PLANE_TYPE_PRIMARY, NULL);
	if (ret)
		return ret;
	drm_plane_enable_fb_damage_clips(&a9->plane);
	drm_crtc_helper_add(&a9->crtc, &a9_crtc_helper_funcs);
	ret = drm_crtc_init_with_planes(drm, &a9->crtc, &a9->plane, NULL, &a9_crtc_funcs, NULL);
	if (ret)
		return ret;
	a9->encoder.possible_crtcs = drm_crtc_mask(&a9->crtc);
	ret = drm_encoder_init(drm, &a9->encoder, &a9_encoder_funcs, DRM_MODE_ENCODER_DSI, NULL);
	if (ret)
		return ret;
	/* DSI type: the contract path is cardN-DSI-1 (and HWC treats DSI as internal) */
	ret = drm_connector_init(drm, &a9->connector, &a9_connector_funcs, DRM_MODE_CONNECTOR_DSI);
	if (ret)
		return ret;
	drm_connector_helper_add(&a9->connector, &a9_connector_hfuncs);
	return drm_connector_attach_encoder(&a9->connector, &a9->encoder);
}

/* KMS objects and their initial (fixed-mode) state; shared with the KUnit test */
static int a9_kms_init(struct a9_eink *a9)
{
	struct drm_device *drm = &a9->drm;
	int ret;

	drm->mode_config.funcs = &a9_mode_config_funcs;
	drm->mode_config.helper_private = &a9_input_helpers;
	/*
	 * Any framebuffer size may be created (the composer imports small layer
	 * buffers, run 6: "bad framebuffer height 49"); the primary plane's
	 * check (no positioning, no scaling) still requires a full 824x1648
	 * source on the only plane.
	 */
	drm->mode_config.min_width = 1;
	drm->mode_config.max_width = 4096;
	drm->mode_config.min_height = 1;
	drm->mode_config.max_height = 4096;
	drm->mode_config.preferred_depth = 24;
	ret = a9_pipe_init(a9);
	if (ret)
		return ret;
	ret = drm_vblank_init(drm, 1);
	if (ret)
		return ret;
	drm_mode_config_reset(drm);
	a9_fixed_route(a9);
	return 0;
}

static int a9_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct a9_eink *a9;
	struct drm_device *drm;
	const char *f = transport_format;
	int ret, k;

	if (!f || strcmp(f, "XR24"))
		return dev_err_probe(dev, -EINVAL, "transport_format must be XR24 (data on B, control on G)\n");

	a9 = devm_drm_dev_alloc(dev, &a9_drm_driver, struct a9_eink, drm);
	if (IS_ERR(a9))
		return PTR_ERR(a9);
	drm = &a9->drm;
	a9->dev = dev;
	platform_set_drvdata(pdev, a9);
	mutex_init(&a9->shadow_lock);
	mutex_init(&a9->eng_lock);
	spin_lock_init(&a9->diag.lock);
	a9->diag.end_first_low_ms = -1;
	a9->diag.end_last_low_ms = -1;
	a9->diag.vcom_last = -1;	/* F6: no read yet */
	a9->diag.vcom_last_ms = -1;
	a9->diag.end_last_pg = -ENODATA;
	init_waitqueue_head(&a9->wq);
	a9->mode = A9_MODE_DEFAULT;
	a9->controls[0] = 30;
	a9->controls[1] = 30;
	a9->controls[2] = 0;
	a9->controls[3] = READ_ONCE(clear_shadow) ? 1 : 0;
	a9->controls_dirty = true;

	a9->pmic_np = of_parse_phandle(dev->of_node, "hisense,epd-pmic", 0);
	a9->tx.panel_np = of_parse_phandle(dev->of_node, "hisense,transport-panel", 0);
	if (a9->pmic_np && devm_add_action_or_reset(dev, a9_put_node, a9->pmic_np))
		return -ENOMEM;
	if (a9->tx.panel_np && devm_add_action_or_reset(dev, a9_put_node, a9->tx.panel_np))
		return -ENOMEM;
	if (!a9->pmic_np || !a9->tx.panel_np)
		return dev_err_probe(dev, -EINVAL, "need hisense,epd-pmic and hisense,transport-panel\n");
	ret = fp9929a_get_status(a9->pmic_np, NULL);
	if (ret == -EPROBE_DEFER)
		return dev_err_probe(dev, ret, "FP9929A not bound yet\n");
	a9->hv = devm_regulator_get_optional(dev, "epd");
	if (IS_ERR(a9->hv)) {
		if (PTR_ERR(a9->hv) == -EPROBE_DEFER)
			return -EPROBE_DEFER;
		dev_warn(dev, "no epd-supply: no forced FP9929A power-down fallback\n");
		a9->hv = NULL;
	}
	a9->therm = devm_iio_channel_get(dev, "epd-therm");
	if (IS_ERR(a9->therm)) {
		if (PTR_ERR(a9->therm) == -EPROBE_DEFER)
			return -EPROBE_DEFER;
		dev_warn(dev, "no epd-therm channel: t2 = t1\n");
		a9->therm = NULL;
	}

	a9->tx.dev = dev;
	a9->tx.wq = &a9->wq;
	a9->tx.fourcc = DRM_FORMAT_XRGB8888;
	mutex_init(&a9->tx.lock);
	a9->tx.fault = a9_tx_fault;
	a9x_init(&a9->tx);

	a9->tables = a9_vzalloc(dev, sizeof(*a9->tables));
	a9->shadow = a9_vzalloc(dev, A9T_N);
	/* optional: without it the direct conversion path is used */
	a9->convert_scratch = a9_vzalloc(dev, a9e_fb_scratch_bytes());
	a9->e.grey = a9_vzalloc(dev, A9T_N);
	a9->e.old = a9_vzalloc(dev, A9T_N);
	a9->e.nlv = a9_vzalloc(dev, A9T_N);
	a9->e.pairs = a9_vzalloc(dev, (size_t)A9T_N * 2);
	for (k = 0; k < 5; k++)
		a9->e.arr[k] = a9_vzalloc(dev, (size_t)A9T_N * 2);
	a9->e.work = a9_vzalloc(dev, sizeof(*a9->e.work));
	/* optional: without the stored diffusion errors only the per-pixel conditioning kinds are damage-limited */
	a9->e.ee = a9_vzalloc(dev, A9E_EE_BYTES);
	a9->e.cache = a9_vzalloc(dev, A9T_FRAME_BYTES);
	a9->frame = a9_vzalloc(dev, A9T_FRAME_BYTES);	/* bytes 1..3 := template at load */
	a9->ct = a9_vzalloc(dev, A9T_FRAME_BYTES);
	a9->trace_old = a9_vzalloc(dev, A9T_N);
	a9->dry_save_old = a9_vzalloc(dev, A9T_N);
	a9->prep_save_old = a9_vzalloc(dev, A9T_N);
	/* optional: without the two buffers the same-target skip stays off */
	a9->same.grey = a9_vzalloc(dev, A9T_N);
	a9->same.old = a9->same.grey ? a9_vzalloc(dev, A9T_N) : NULL;
	if (!a9->same.old)
		a9->same.grey = NULL;
	a9r_init(&a9->ring, a9_vzalloc(dev, A9R_SLOTS * A9R_DATA));	/* ~10.9 MB */
	init_waitqueue_head(&a9->rwq);
	a9->dry_levels = a9_vzalloc(dev, A9T_N);
	if (!a9->tables || !a9->shadow || !a9->e.grey || !a9->e.old || !a9->e.nlv ||
	    !a9->e.pairs || !a9->e.arr[4] || !a9->e.arr[0] || !a9->e.arr[1] ||
	    !a9->e.arr[2] || !a9->e.arr[3] || !a9->e.work || !a9->e.cache ||
	    !a9->frame || !a9->ct || !a9->trace_old || !a9->dry_save_old || !a9->dry_levels ||
	    !a9->prep_save_old || !a9->ring.mem)
		return -ENOMEM;
	memset(a9->shadow, 0xff, A9T_N);	/* white, like the levels */

	dma_coerce_mask_and_coherent(dev, DMA_BIT_MASK(64));
	ret = drmm_mode_config_init(drm);
	if (ret)
		return ret;
	ret = a9_kms_init(a9);
	if (ret)
		return dev_err_probe(dev, ret, "KMS init\n");

	a9->rthread = kthread_run(a9_render_thread, a9, "a9_eink_r");
	if (IS_ERR(a9->rthread)) {
		ret = PTR_ERR(a9->rthread);
		a9->rthread = NULL;
		return ret;
	}
	a9->thread = kthread_run(a9_thread, a9, "a9_eink");
	if (IS_ERR(a9->thread)) {
		ret = PTR_ERR(a9->thread);
		a9->thread = NULL;
		kthread_stop(a9->rthread);
		a9->rthread = NULL;
		return ret;
	}
	ret = drm_dev_register(drm, 0);
	if (ret) {
		kthread_stop(a9->thread);
		a9->thread = NULL;
		kthread_stop(a9->rthread);
		a9->rthread = NULL;
		return ret;
	}
	a9->pm_nb.notifier_call = a9_pm_notify;
	register_pm_notifier(&a9->pm_nb);
	/* prewarm listener: registered always, inert while a9_eink.prewarm=0 */
	a9->input_h.event = a9_input_event;
	a9->input_h.connect = a9_input_connect;
	a9->input_h.disconnect = a9_input_disconnect;
	a9->input_h.name = "a9_eink_prewarm";
	a9->input_h.id_table = a9_input_ids;
	a9->input_h.private = a9;
	if (input_register_handler(&a9->input_h))
		dev_warn(dev, "prewarm input handler not registered: a9_eink.prewarm has no effect\n");
	else
		a9->input_h_registered = true;
	a9_debugfs(a9);
	dev_info(dev, "E Ink KMS %ux%u; frames %s; transport %s; HV hold %u ms\n",
		 A9E_FB_W, A9E_FB_H, enable ? "SENT TO THE PANEL" : "dry run (a9_eink.enable=0)",
		 f, hv_hold_ms);
	return 0;
}

static void a9_teardown(struct a9_eink *a9)
{
	bool drained;

	if (a9->input_h_registered) {
		input_unregister_handler(&a9->input_h);	/* no poke after this */
		a9->input_h_registered = false;
	}
	WRITE_ONCE(a9->prewarm_req, false);
	drained = a9_drain(a9);

	if (!drained)
		/* a wedged commit could also wedge kthread_stop: rails first */
		a9_force_hv_off(a9, "shutdown drain timed out");
	if (a9->thread) {
		kthread_stop(a9->thread);	/* powers the pipeline down */
		a9->thread = NULL;
	}
	if (a9->rthread) {
		kthread_stop(a9->rthread);	/* idle: the worker always stops it */
		a9->rthread = NULL;
	}
	a9x_detach(&a9->tx);
	a9x_fini(&a9->tx);
}

static void a9_remove(struct platform_device *pdev)
{
	struct a9_eink *a9 = platform_get_drvdata(pdev);

	unregister_pm_notifier(&a9->pm_nb);
	drm_dev_unplug(&a9->drm);
	a9_teardown(a9);
	debugfs_remove_recursive(a9->dbg);
	WRITE_ONCE(a9->kms_teardown, true);
	drm_atomic_helper_shutdown(&a9->drm);
}

static void a9_shutdown(struct platform_device *pdev)
{
	struct a9_eink *a9 = platform_get_drvdata(pdev);

	/* power-off image: its force_clear must finish before the rails go */
	a9_teardown(a9);
}

static const struct of_device_id a9_of_match[] = {
	{ .compatible = "hisense,hlte556n-eink-display" },
	{ }
};
MODULE_DEVICE_TABLE(of, a9_of_match);

static struct platform_driver a9_driver = {
	.probe = a9_probe,
	.remove = a9_remove,
	.shutdown = a9_shutdown,
	.driver = {
		.name = "a9-eink",
		.of_match_table = a9_of_match,
	},
};
module_platform_driver(a9_driver);

#if IS_ENABLED(CONFIG_DRM_A9_EINK_KUNIT_TEST)
#include "a9_eink_kms_test.c"
#endif

MODULE_FIRMWARE(A9_FW_NAME);
MODULE_FIRMWARE(A9_CT_NAME);
MODULE_DESCRIPTION("Hisense A9 E Ink display with in-kernel software TCON");
MODULE_LICENSE("GPL");
