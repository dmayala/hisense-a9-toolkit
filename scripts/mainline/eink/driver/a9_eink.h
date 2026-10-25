/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef A9_EINK_H
#define A9_EINK_H

#include <linux/atomic.h>
#include <linux/hrtimer.h>
#include <linux/input.h>
#include <linux/iosys-map.h>
#include <linux/ktime.h>
#include <linux/mutex.h>
#include <linux/notifier.h>
#include <linux/spinlock.h>
#include <linux/wait.h>

#include <drm/drm_client.h>
#include <drm/drm_atomic_profile.h>
#include <drm/drm_connector.h>
#include <drm/drm_crtc.h>
#include <drm/drm_device.h>
#include <drm/drm_encoder.h>
#include <drm/drm_plane.h>

#include "a9_eink_engine.h"

struct iio_channel;
struct sha256_ctx;
struct regulator;
struct task_struct;
struct dentry;

/*
 * Transport: an in-kernel DRM client on the msm DRM device that scans the
 * 448x829 drive frames out through DSI0 -> TC358767 -> the E Ink source
 * drivers. CRTC on = panel prepared = FP9929A high voltage on.
 */
/* First interlock event, frozen before later neutral/profile reuse. */
struct a9x_stall_snapshot {
	bool valid, in_commit, front_null;
	u32 stage, phase;
	u64 at_ns, progress_age_us, commit_age_us, phase_age_us;
	u64 phase_ns[DRM_ATOMIC_PROFILE_DONE];
	u64 frames;
};
/* Read-only boot latch exported by msm; no clear API. */
bool msm_a9_commit_faulted(void);

struct a9x_transport {
	struct device *dev;		/* ours, for logging */
	struct device_node *panel_np;	/* hisense,transport-panel */
	u32 fourcc;
	struct mutex lock;		/* order: drm clientlist_mutex, then this */
	struct drm_client_dev client;
	bool registered;		/* client on the msm device's list */
	bool gone;			/* msm device unregistered under us */
	bool unreg_hv_unknown;		/* its power-down failed at unregister */
	struct device_link *link;	/* us -> msm (shutdown/suspend order) */
	wait_queue_head_t *wq;		/* the driver worker, woken on unregister */
	struct drm_mode_set *ms;	/* the modeset that drives the panel */
	struct drm_client_buffer *buf[2];
	struct iosys_map map[2];
	u32 pitch;
	/*
	 * Control template (validated, A9T_FRAME_BYTES, byte 0 = 0), owned by
	 * the driver; set under ->lock before the first attach. Every buffer
	 * the panel scans is template | byte 0 of a frame; the idle (null)
	 * frame is the template itself. NULL: nothing is ever driven.
	 */
	const u8 *tmpl;
	int front;			/* buffer being scanned out */
	bool front_null;		/* it carries no drive */
	bool on;			/* CRTC active: HV may be up */
	bool vbl_ref;
	u64 last_vbl;
	ktime_t last_commit, on_since, off_since;
	u32 period_us;			/* frame period of the transport mode */
	/*
	 * Frame-progress interlock (a9x_stall_*, a9_eink_transport.c): armed
	 * before every commit of a drive frame, cancelled by a successful
	 * all-zero commit. At100ms log/count only; at500ms emergency off.
	 */
	struct hrtimer stall;
	raw_spinlock_t stall_lock; /* serialize progress/disarm with hardirq */
	ktime_t stall_started;
	bool stall_soft_reported;
	u64 soft_stalls;
	u32 soft_last_age_us;
	bool stall_armed;		/* a drive frame may be on the glass */
	bool commit_active;		/* includes waiting for DRM locks */
	int stall_kind;			/* 1: between commits; 2: hard; 3: timed in-flight */
	ktime_t stall_at, commit_started;
	u32 commit_stage; /* 1 client lock, 2 blocking flip, 3 full modeset */
	bool stall_commit_ok, stall_returned, transport_error;
	u64 commit_seq, stall_commit_seq;
	int stall_raw_ret;
	u32 stall_return_us;
	struct a9x_stall_snapshot stall_snapshot;
	char stall_why[96];		/* message for fp9929a_emergency_off */
	/* hardirq-safe hook run on a fault (driver: enable := 0); may be NULL */
	void (*fault)(struct a9x_transport *t);
	/* stats */
	u64 frames, late, vbl_skips, commits_failed, stalls;
	u32 max_gap_us;
	/* the same, only for drive time: the whole gap if the front drove, else
	 * the commit's own execution if the incoming frame drives (review 10) */
	u64 late_drive, vbl_skips_drive;
	u32 max_gap_drive_us;
	/* pacing diagnosis (0033): the commit call itself, and the compose */
	u32 commit_max_us, compose_max_us;
	u64 commit_slow;		/* commit calls > 1.5 periods */
	/* 0036: blocking FB-only flips, separate from cold modeset latency. */
	struct drm_atomic_commit_profile profile;
	u32 phase_max_us[DRM_ATOMIC_PROFILE_DONE];
	u32 flip_max_us, modeset_max_us;
	u64 flips, modesets;
	bool profile_valid;
};

/*
 * Render-ahead ring (a9_eink_ring.c, patch 0033): slots hold byte 0 of the
 * data region (rows 5..828, words 0..411) of one frame each.
 */
#define A9R_SLOTS 32
#define A9R_DATA ((size_t)A9T_H * A9T_DATA_WORDS)	/* 339,488 bytes */
struct a9_ring {
	u8 *mem;			/* A9R_SLOTS * A9R_DATA */
	bool null[A9R_SLOTS];
	spinlock_t lock;
	wait_queue_head_t wq;
	unsigned int head, tail;	/* produced / consumed (monotonic) */
	bool running, done, stop;	/* producer in the engine; finished; asked to stop */
	int err;
	unsigned int fill_min;		/* lowest fill the consumer saw after prefill */
	u64 underruns;
};
void a9r_init(struct a9_ring *r, u8 *mem);
int a9r_pack(const u8 *frame, u8 *slot, bool *null);
void a9r_reset(struct a9_ring *r);
u8 *a9r_slot(struct a9_ring *r);
void a9r_publish(struct a9_ring *r, bool null);
void a9r_finish(struct a9_ring *r, int err);
int a9r_prefill(struct a9_ring *r, struct a9x_transport *t, unsigned int timeout_ms,
		const bool *enable, unsigned int min_frames);
int a9r_drive(struct a9_ring *r, struct a9x_transport *t, const bool *enable);
void a9r_request_stop(struct a9_ring *r);
void a9r_stop(struct a9_ring *r);

/* User-selected N-e policy: diagnostic100ms, emergency500ms. These are
 * engineering thresholds, not measured stock panel hold tolerances. */
#define A9X_STALL_SOFT_MS 100
#define A9X_STALL_HARD_MS 500
#define A9X_RECOVERY_CLEAN_MS 600000
#define A9X_PERIOD_DEFAULT_US 11764	/* 85 Hz, if the mode gave none */

/*
 * Latched by the interlock, failed neutral, or unexpected render-ahead abort;
 * cleared only
 * by writing 1 to a9_eink.clear_fault (a9_eink_guard.c).
 */
extern atomic_t a9x_stall_fault;
/*
 * Rail fault (patch 0039): FP9929A on/PG not valid at an update's start while
 * the pipeline was on. Latched before the power-down; refuses every drive
 * commit, power-up and real update until 1 is written to a9_eink.clear_fault.
 * There is no automatic recovery; after the clear the next real update is a
 * full Clear (force_clear stays set until one completes).
 */
extern atomic_t a9x_rail_fault;

/* any latched fault: no power-up, no real update, no drive commit */
static inline bool a9x_fault_latched(void)
{
	return atomic_read(&a9x_stall_fault) || atomic_read(&a9x_rail_fault);
}
/* bin admission (a9_eink.bin_min/bin_max): true if bin is in [*lo, *hi] */
bool a9_bin_admitted(int bin, int *lo, int *hi);

void a9x_init(struct a9x_transport *t);	/* once, before anything else */
void a9x_fini(struct a9x_transport *t);	/* after a9x_detach, process context */

int a9x_attach(struct a9x_transport *t);	/* -EAGAIN: msm not there yet */
void a9x_detach(struct a9x_transport *t);
int a9x_power_on(struct a9x_transport *t);
int a9x_show(struct a9x_transport *t, const u8 *frame);	/* uses byte 0 of each word only */
/* F6b (review 89d): vblank count and time of the scanout, read-only, no lock; 0 when no vblank reference is held */
u64 a9x_vblank(struct a9x_transport *t, s64 *at_us);
int a9x_show_data(struct a9x_transport *t, const u8 *data);	/* a ring slot (A9R_DATA) */
int a9x_power_off(struct a9x_transport *t);
int a9x_blank(struct a9x_transport *t);
void a9x_emergency_off(struct a9x_transport *t, const char *why);
int a9x_idle_refresh(struct a9x_transport *t);	/* one idle (template) frame, always */
bool a9x_attached(struct a9x_transport *t);

/* Diagnostic only: counts software waveform jobs/accepted slots, not energy. */
struct a9_session_stats {
	u64 id, frame0;
	u64 full_frames; /* accepted slots (incl. init/null) of full-rect jobs */
	u64 waveforms, waveforms_done;
	u64 full_started, full_done;
	u64 clears, clears_done, transitions, transitions_done;
};

/* Numeric input diagnostics only; no framebuffer, process or client identity. */
#define A9_INPUT_SAMPLES 4096
struct a9_input_stats {
	u64 count, pixels, full, partial, unchanged, failures;
	u64 total_ns[4], max_ns[4]; /* CPU begin, shadow wait, conversion, CPU end */
	u64 fences, fence_total_ns, fence_max_ns;
	u64 mode_requests, manual_requests, mode_ms, manual_ms;
	u32 requested_mode, manual_mode;
};

#define A9_DIAG_BUCKETS 5	/* 0-3 ms, 4-15, 16-63, 64+, no waveform yet */
#define A9_DIAG_PD 12
/* 0053 pg-settle: hard ceiling of the post-waveform PG settle window (the module parameter is clamped to it) */
#define A9_PG_SETTLE_MAX_MS 200u
#define A9_SETTLE_BUCKETS 5	/* recovered-dip duration: <2, <5, <10, <25, >=25 ms */
enum a9_settle { A9_SETTLE_NO, A9_SETTLE_OK, A9_SETTLE_TIMEOUT };

struct a9_eink {
	struct drm_device drm;
	struct drm_plane plane;
	struct drm_crtc crtc;
	struct drm_encoder encoder;
	struct drm_connector connector;
	struct device *dev;

	struct a9x_transport tx;
	struct device_node *pmic_np;	/* hisense,epd-pmic (FP9929A) */
	struct iio_channel *therm;	/* PMI632 epd_top_therm, optional */
	struct regulator *hv;		/* emergency handle on the FP9929A */

	/* front side: written by the plane's atomic_update */
	struct mutex shadow_lock;
	struct a9_input_stats input; /* shadow_lock, never used by power/drive */
	atomic_t input_reserved, fence_reserved;
	u8 *shadow;			/* landscape grey of the latest commit */
	u8 *convert_scratch;		/* cached bounce band for the plane conversion (under shadow_lock) */
	struct a9e_rect dirty;
	u8 dirty_tm[A9E_NTILES];	/* tiles with a changed grey pixel since the last job (damage_prep), shadow_lock */
	bool tm_lost;			/* a convert since the last job was not tile-tracked */
	bool prev_pattern;		/* the previous job's grey was the test pattern */
	bool clear_req, manual_clear_req, kick, busy, suspending, parked;
	atomic_t recover_req;		/* 0 idle, 1 queued, 2 worker owns */
	int recover_ret;
	unsigned int stall_retries; /* two grants, refilled after10min clean progress */
	u64 stall_grants; /* monotonic receipt, never reset by refill/clear_fault */
	ktime_t stall_clean_since;
	u64 stall_clean_soft, stall_clean_hard;
	bool kms_teardown;		/* remove: a disable really disables (unlinks) */
	u32 mode;			/* stock mode word, sysfs "mode" */
	int controls[4];		/* black, white, contrast, clear_shadow (0/1) */
	bool controls_dirty;
	bool test_pattern;		/* root diagnostic input override, shadow_lock */
	wait_queue_head_t wq;

	/* engine, owned by the worker thread; eng_lock for debugfs readers */
	struct mutex eng_lock;
	struct a9e_engine e;
	struct a9t_tables *tables;
	u8 *ct;				/* control template, A9T_FRAME_BYTES */
	bool ct_loaded;			/* validated (a9_load_template) */
	bool wf_loaded;
	u8 wf_src_sha[32];
	u8 *frame;			/* staging frame, A9T_FRAME_BYTES; bytes 1..3 = template */
	u8 *trace_old;			/* levels before the last update (trace) */
	bool init_pending, force_clear;		/* real pipeline */
	bool dry_init_pending, dry_force_clear;	/* dry-run model */
	u8 *dry_save_old, *dry_levels;
	u8 *prep_save_old;			/* levels before a9e_prepare (real path) */
	struct a9e_same same;			/* stock-style same-target skip (eink-latency r2), eng_lock */
	u32 dry_save_pre;
	int dry_save_have_pre;
	bool hv_fault;				/* forced HV off, state unknown */
	/* patch 0039: end of the last real waveform run (0 = none) and its result */
	ktime_t wf_end;
	int wf_ret;
	struct a9_session_stats session; /* reset only after fresh PG-qualified power-up */
	/* patch 0039: state captured at the last rail fault, BEFORE its power-down */
	struct {
		u64 count;			/* rail faults this boot */
		s64 at_ms;			/* ktime of the capture */
		int getter;			/* fp9929a_get_status() return */
		int pg_raw;			/* raw PG read, -errno; -ENODATA: not read */
		bool on, energized, forced, emergency, pg_valid, pg_good;
		bool tx_on, tx_gone, front_null, armed;
		s64 session_ms;			/* since the transport power-up, -1 off */
		s64 since_wf_ms;		/* since the last waveform run, -1 none */
		int wf_ret;			/* that run's result */
		s64 pg_at_us; /* completion time of the ORIGINAL failed PG read */
		struct a9_session_stats session; /* frozen before shutdown */
		u64 session_frames; /* includes zero_test; excludes shutdown neutral */
		u64 pd_seq_before, pd_seq;
		bool pd_off_confirmed, pd_valid;
		int pd_getter; /* -ENODATA: no post-off lookup was attempted */
		s64 pd_at_us[3]; /* normal shutdown: entry, VCOM-low, PWRUP-low */
		int pd_pg_raw[3];
	} rail;
	/*
	 * Patch 0052 (rail-diag): LOG-ONLY. Nothing here is read by any latch,
	 * gate, shutdown or timing decision. Written by the worker thread only,
	 * read by sysfs under diag.lock (a leaf lock: no other lock is taken, and
	 * no FP9929A call is made, while it is held).
	 */
	struct a9_rail_diag {
		spinlock_t lock;
		/* delay after the last waveform end at each checked rail sample */
		u64 hist_n[A9_DIAG_BUCKETS], hist_low[A9_DIAG_BUCKETS];
		/* one non-latching PG read at each waveform end */
		u64 end_n, end_low, end_err, end_skipped;
		u64 end_full_n, end_full_low;	/* the same, for full-screen rects only */
		int end_last_pg;
		s64 end_last_at_us;
		s64 end_first_low_ms;		/* uptime ms, -1 none */
		u64 end_first_low_session, end_first_low_n;
		u32 end_first_low_total_us;	/* that waveform's total_us */
		s64 end_last_low_ms;
		/* 0053: bounded PG settle after a waveform end (pg_settle_ms); all zero while it is off */
		u64 settle_n, settle_ok, settle_timeout, settle_skip;
		u64 settle_hist[A9_SETTLE_BUCKETS];	/* recovered dips by duration (from the first low reading) */
		u32 settle_max_us;			/* longest recovered dip */
		/* M4 (review 89): per-boot cap and the lows that never reached the settle */
		u64 settle_cap_skip;	/* lows latched because pg_settle_total_max was used up */
		u64 settle_reread_high;	/* wf-end low (0052 read) already high on 0053's re-read: no settle, no budget */
		u64 settle_rate_skip;	/* F9: lows latched because the HV-on-time token bucket was empty */
		/* F6 (review 89d): FP9929A VCOM register read back after recovered dips; log only */
		u64 vcom_reads, vcom_ok, vcom_mismatch, vcom_err;
		int vcom_last;		/* last value read, -1 none */
		s64 vcom_last_ms;	/* uptime ms of that read, -1 none */
		/* ring of the last A9_DIAG_PD shutdown traces (healthy baseline) */
		u64 pd_seen_seq, pd_total, pd_missed, pd_skipped_fault;
		unsigned int pd_head;		/* next slot */
		struct {
			u64 seq, session;
			s64 at_us[3];
			int pg[3];
		} pd[A9_DIAG_PD];
	} diag;
	/* 0053: recovered-dip budget of the current HV session (worker thread only) */
	ktime_t settle_on_since;
	unsigned int settle_used;
	/* M4: recovered dips since boot (pg_settle_total_max), and the settle clock
	 * origin handed over by the wf-end path (0 = use the sample's own read time) */
	unsigned int settle_total;
	s64 settle_first_us;
	/* F6/F6b (review 89d), worker only: what the last a9_pg_settle saw */
	bool settle_ok_now;		/* the last call recovered (set at A9_SETTLE_OK, cleared at entry) */
	u64 settle_trace;		/* bit i = settle read i was good (first 64 reads) */
	u8 settle_trace_n;		/* reads recorded */
	u64 vbl_first, vbl_rec;		/* vblank count at the settle entry / at the recovery */
	s64 vbl_first_us, vbl_rec_us;	/* and their timestamps */
	unsigned int vcom_reads;	/* F6 reads this boot (A9_VCOM_READS_MAX) */
	/* F9: token bucket over HV-on time (milli-dips) and the HV-on accounting it refills from */
	u64 rate_tokens, rate_at_ms;	/* tokens, and the HV-on ms they were last refilled at */
	bool rate_init;
	u64 hvon_ms;			/* cumulative HV-on time seen by the worker */
	ktime_t hvon_since, hvon_mark;	/* the session being counted (on_since) and how far */
	struct notifier_block pm_nb;
	struct task_struct *thread;

	/* eink-latency r2 prewarm (opt-in, a9_eink.prewarm): a touch-down while the
	 * pipeline is off asks the worker to power up ahead of the first update */
	struct input_handler input_h;
	bool input_h_registered;
	bool prewarm_req;		/* set from input event context, cleared by the worker */
	bool prewarm_session;		/* worker only: this HV session came from a prewarm, no output yet */
	ktime_t prewarm_block_until;	/* worker writes, event context reads: no poke before this */
	atomic_long_t prewarm_pokes;
	u64 prewarm_req_n, prewarm_up, prewarm_unused, prewarm_fail;	/* worker only */

	/* render-ahead (0033): the producer thread renders a real update's frames */
	struct a9_ring ring;
	struct task_struct *rthread;
	wait_queue_head_t rwq;
	bool rjob;			/* a job is posted for the producer */
	bool rjob_init;			/* it starts with the init flash */
	int rjob_bin;
	struct sha256_ctx *rjob_sc;	/* trace: hash of the update frames */
	unsigned int ring_init_n;	/* frames of the init pass, incl. a blank */
	u32 render_max_us, prefill_us;

	/* last update, for sysfs/debugfs */
	struct {
		u32 mode, pre, type, frames, steps;
		int clear, bin, t1, t2, t1_raw, t2_raw, dry, aborted;
		struct a9e_rect rect;
		u32 begin_us, total_us, prep_us;
		u8 sha[32];
		bool sha_valid;
	} last;
	u64 updates, aborts;
	struct dentry *dbg;
};

#endif
