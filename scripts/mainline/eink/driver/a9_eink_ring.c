// SPDX-License-Identifier: GPL-2.0-only
/*
 * a9_eink render-ahead ring (patch 0033, frame pacing on Android).
 *
 * Mainline Android runs 5-7: with the worker rendering each frame between
 * its own blocking commits, drive frames were repeated (late_drive 11-12,
 * max_gap_drive 23.7-25.5 ms) and once held past the 35.3 ms interlock
 * deadline. Now a producer thread renders the update's frames AHEAD into
 * this ring and the worker (the consumer) does per frame only the byte-0
 * compose into the template buffer and the commit.
 *
 * Each slot holds byte 0 of the data region only (rows 5..828, words
 * 0..411: 339,488 bytes): outside it byte 0 is 0 in every frame the core
 * produces (a9r_pack refuses a frame where it is not), and bytes 1..3 come
 * from the validated control template at compose time (a9x_show_data), so a
 * composed buffer equals what a9x_compose made of the full frame.
 *
 * Rules, all enforced here:
 *  - the waveform starts once the initial watermark is ready (or the update is
 *    rendered): a9r_prefill; until then the panel scans the idle frame;
 *  - the consumer never waits for a frame with a drive frame on the glass:
 *    an empty ring before the producer is done is an UNDERRUN (-EAGAIN); the
 *    caller stops the trial, establishes idle and powers down BEFORE joining
 *    the producer. Unexpected errors latch a fault; a partial waveform is
 *    not a complete/DC-balanced waveform and must not be retried silently;
 *  - stop (abort, enable=0, transport lost) wakes the producer, which ends
 *    after at most the frame it is rendering.
 * Locking: r->lock (spinlock) protects head/tail/flags; the producer writes
 * a slot only while it is free (head - tail < A9R_SLOTS) and the consumer
 * reads it only while it is filled; publishing/consuming moves the index
 * under the lock. Separate so tests/mainline-a9eink-stall compiles it.
 */
#include <linux/jiffies.h>
#include <linux/spinlock.h>
#include <linux/string.h>
#include <linux/wait.h>

#include "a9_eink.h"

void a9r_init(struct a9_ring *r, u8 *mem)
{
	r->mem = mem;
	spin_lock_init(&r->lock);
	init_waitqueue_head(&r->wq);
	r->head = r->tail = 0;
	r->running = false;
}

/* frame (A9T_FRAME_BYTES) -> slot: byte 0 of the data region; *null = no drive */
int a9r_pack(const u8 *frame, u8 *slot, bool *null)
{
	u8 acc = 0, out = 0;
	int r, w;

	for (r = 0; r < A9T_TH; r++) {
		const u8 *f = frame + (size_t)r * A9T_TW * 4;

		if (r < A9T_HEAD_ROWS) {
			for (w = 0; w < A9T_TW; w++)
				out |= f[w * 4];
			continue;
		}
		for (w = 0; w < A9T_DATA_WORDS; w++) {
			u8 v = f[w * 4];

			acc |= v;
			*slot++ = v;
		}
		for (; w < A9T_TW; w++)
			out |= f[w * 4];
	}
	*null = !acc;
	return out ? -EINVAL : 0;	/* drive data outside the data region: refuse */
}

/* consumer: a fresh ring for one update (the producer is idle) */
void a9r_reset(struct a9_ring *r)
{
	spin_lock(&r->lock);
	r->head = r->tail = 0;
	r->done = r->stop = false;
	r->err = 0;
	r->running = true;
	r->fill_min = A9R_SLOTS;
	spin_unlock(&r->lock);
}

static bool a9r_space(struct a9_ring *r)
{
	bool ok;

	spin_lock(&r->lock);
	ok = r->stop || r->head - r->tail < A9R_SLOTS;
	spin_unlock(&r->lock);
	return ok;
}

/* producer: the next free slot, waiting for space; NULL once stopped */
u8 *a9r_slot(struct a9_ring *r)
{
	u8 *s = NULL;

	wait_event(r->wq, a9r_space(r));
	spin_lock(&r->lock);
	if (!r->stop)
		s = r->mem + (size_t)(r->head % A9R_SLOTS) * A9R_DATA;
	spin_unlock(&r->lock);
	return s;
}

void a9r_publish(struct a9_ring *r, bool null)
{
	spin_lock(&r->lock);
	r->null[r->head % A9R_SLOTS] = null;
	r->head++;
	spin_unlock(&r->lock);
	wake_up_all(&r->wq);
}

/* producer: the update is rendered (err 0) or ended (err < 0) */
void a9r_finish(struct a9_ring *r, int err)
{
	spin_lock(&r->lock);
	r->done = true;
	r->err = err;
	r->running = false;
	spin_unlock(&r->lock);
	wake_up_all(&r->wq);
}

/*
 * consumer: until min_frames slots are ready or the update is rendered, keep
 * the panel on idle refreshes at the frame rate (the stock idle frame, as
 * between updates; no gap for the timing counters). Reject admission at the
 * deadline, including a late ready/done return from idle_refresh. This cannot
 * bound a stuck DRM call's duration: FP9929A's independent cap still applies.
 */
int a9r_prefill(struct a9_ring *r, struct a9x_transport *t, unsigned int timeout_ms,
		const bool *enable, unsigned int min_frames)
{
	unsigned long end = jiffies + msecs_to_jiffies(timeout_ms);
	int err;

	/* Bound direct callers too; parameter writes reject this range already. */
	if (min_frames < 2 || min_frames > A9R_SLOTS)
		return -EINVAL;
	for (;;) {
		bool ready;

		/* Also run on the ready/done fast path and after EVERY idle commit. */
		spin_lock(&r->lock);
		ready = r->done || r->head - r->tail >= min_frames;
		if (r->stop || !READ_ONCE(*enable))
			err = -ECANCELED;
		else if (time_after_eq(jiffies, end))
			err = -ETIMEDOUT;
		else
			err = r->done ? r->err : 0;
		spin_unlock(&r->lock);
		if (err || ready)
			return err;
		err = a9x_idle_refresh(t);
		if (err)
			return err;
	}
}

/*
 * consumer: send every rendered frame, one commit each. 0 = the whole update
 * was sent; -EAGAIN = underrun (the producer is not done and nothing is
 * ready); -ECANCELED = *enable went 0; else the producer's or the
 * transport's error. On any error the caller blanks and aborts.
 */
int a9r_drive(struct a9_ring *r, struct a9x_transport *t, const bool *enable)
{
	for (;;) {
		unsigned int fill;
		const u8 *s;
		int ret;

		spin_lock(&r->lock);
		/* Cancellation wins over an empty ring; don't mislabel it starvation. */
		if (r->stop || !READ_ONCE(*enable)) {
			spin_unlock(&r->lock);
			return -ECANCELED;
		}
		/* A known producer failure must not send a queued valid prefix. */
		if (r->done && r->err) {
			ret = r->err;
			spin_unlock(&r->lock);
			return ret;
		}
		fill = r->head - r->tail;
		if (fill < r->fill_min)
			r->fill_min = fill;
		if (!fill) {
			ret = r->done ? r->err : -EAGAIN;
			if (ret == -EAGAIN)
				r->underruns++;
			spin_unlock(&r->lock);
			return ret;
		}
		s = r->mem + (size_t)(r->tail % A9R_SLOTS) * A9R_DATA;
		spin_unlock(&r->lock);
		/* emergency stop: enable=0 mid-update aborts after this frame */
		if (!READ_ONCE(*enable))
			return -ECANCELED;
		ret = a9x_show_data(t, s);
		if (ret)
			return ret;
		spin_lock(&r->lock);
		r->tail++;
		spin_unlock(&r->lock);
		wake_up_all(&r->wq);
	}
}

static bool a9r_idle(struct a9_ring *r)
{
	bool idle;

	spin_lock(&r->lock);
	idle = !r->running;
	spin_unlock(&r->lock);
	return idle;
}

/* consumer: prevent further work without waiting for an engine borrower */
void a9r_request_stop(struct a9_ring *r)
{
	spin_lock(&r->lock);
	r->stop = true;
	spin_unlock(&r->lock);
	wake_up_all(&r->wq);
}

/* consumer: join only AFTER neutral/power-down on errors; retain engine/hash */
void a9r_stop(struct a9_ring *r)
{
	a9r_request_stop(r);
	/* One frame of work can take unbounded wall time (including scheduling). */
	wait_event(r->wq, a9r_idle(r));
}
