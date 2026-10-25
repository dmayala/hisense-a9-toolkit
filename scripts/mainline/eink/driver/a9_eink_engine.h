/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * a9_eink engine: one E Ink update on top of the a9tcon core
 * (scripts/mainline/tcon/a9tcon.c, byte-exact vs the stock composer).
 *
 * Pure C, no kernel or libc headers beyond what a9tcon.h pulls in: the same
 * file builds into the a9_eink kernel module and into the userspace test
 * harness (scripts/mainline/eink/test/). All buffers are owned by the caller.
 *
 * Sequence per update (mirrors a9tcon-cli.c, which test-tcon-c.py checks
 * against the stock code under Unicorn):
 *   a9e_fb_to_grey()   damage rect of the portrait framebuffer -> landscape grey
 *   a9e_begin()        request builder (mode switch) + step arrays,
 *                      whole-canvas conditioning, target damage, pack, type
 *   a9e_frame()        one drive frame per call until it returns 0
 *   a9e_end()          old := new, previous mode := this mode
 * With a full-screen rect and mask = 0, the frames are exactly those of
 * `a9tcon --pre <prev> --mode <mode> --clear <c>` on the same grey and old.
 */
#ifndef A9_EINK_ENGINE_H
#define A9_EINK_ENGINE_H

#include "a9tcon.h"

/* Framebuffer (portrait, what userspace draws): 824 x 1648 */
#define A9E_FB_W A9T_H
#define A9E_FB_H A9T_W

/* landscape TCON coordinates, half-open: x in [x1, x2) < 1648, y < 824 */
struct a9e_rect {
	int x1, y1, x2, y2;
};

enum a9e_fmt {
	A9E_FMT_RGBX,	/* bytes R,G,B,x: DRM XBGR8888/ABGR8888, Android RGBA_8888 (= stock) */
	A9E_FMT_BGRX,	/* bytes B,G,R,x: DRM XRGB8888/ARGB8888 */
	A9E_FMT_R8,	/* one grey byte per pixel: DRM R8 */
};

/* The waveform blob (/lib/firmware/hisense/a9-waveform.bin), little endian. */
#define A9E_WF_MAGIC 0x46573941u	/* "A9WF" */
#define A9E_WF_VERSION 1u
struct a9e_wf_header {
	uint32_t magic;
	uint32_t version;
	uint32_t header_size;		/* sizeof(struct a9e_wf_header) = 80 */
	uint32_t payload_size;		/* sizeof(struct a9t_tables) */
	uint8_t source_sha256[32];	/* the composer the tables came from */
	uint8_t payload_sha256[32];	/* of the payload that follows */
};

/*
 * The control template (/lib/firmware/hisense/a9-ctrl-template.bin), little
 * endian: this header, then one 448 x 829 frame of 32-bit words whose bytes
 * 1..3 are the panel's control signals (a9tcon.h) and byte 0 is 0. Made from
 * the user's own composer by scripts/mainline/eink/tools (vendor-derived:
 * never part of the source). Only one payload is accepted: its SHA-256 is
 * A9E_CT_SHA256 (MEASURED: the composer's builder, recovery's builder and 16
 * live stock ring buffers agree on bytes 1..3).
 */
#define A9E_CT_MAGIC 0x54433941u	/* "A9CT" */
#define A9E_CT_VERSION 1u
#define A9E_CT_SHA256 "a88ae8efc5325e1ab52e4e48b73d2abb1a3c81a7989baa2a226513b07cebfbe7"
struct a9e_ct_header {
	uint32_t magic;
	uint32_t version;
	uint32_t header_size;		/* sizeof(struct a9e_ct_header) = 80 */
	uint32_t payload_size;		/* A9T_FRAME_BYTES */
	uint8_t source_sha256[32];	/* the composer the template came from */
	uint8_t payload_sha256[32];	/* of the payload that follows */
};

/*
 * Damage-limited prepare (a9_eink_damage.c). Tiles are 32 x 32 landscape
 * pixels, row-major: tile (tx, ty) = ty * A9E_TX + tx. A tile mask is one
 * byte per tile, 0 = no change. Opt-in (a9e_engine.inc_enable); with it off
 * nothing below is reached and every prepare is the reference full pass.
 */
#define A9E_TILE 32
#define A9E_TX ((A9T_W + A9E_TILE - 1) / A9E_TILE)	/* 52 */
#define A9E_TY ((A9T_H + A9E_TILE - 1) / A9E_TILE)	/* 26 */
#define A9E_NTILES (A9E_TX * A9E_TY)			/* 1352 */
/* serial 1-bit diffusion: the error e of every pixel, int16 (the incremental path re-derives
 * every pixel's input from its neighbours' stored errors) */
#define A9E_EE_BYTES ((size_t)A9T_N * sizeof(int16_t))
/* Cost guards (host-measured: the incremental diffusion costs about 2.8x the plain pass per pixel it
 * recomputes, and error cascades can be much larger than the damage): the incremental path is not taken
 * when the damaged tiles alone exceed A9E_INC_GATE_PX (diffusion) or 60 % of the tiles (per-pixel kinds), and the
 * incremental diffusion gives up, finishing the rest plainly, after A9E_INC_CAP pixels recomputed. */
#define A9E_INC_GATE_PX ((uint32_t)(A9T_N / 10))
#define A9E_INC_CAP ((uint32_t)(A9T_N / 8))

/* What the conditioned levels depend on besides the grey buffer. */
struct a9e_cond_sig {
	uint32_t mode16;	/* mode word & 0xffff */
	uint32_t clear_shadow;
	uint8_t g2[256], g16[256], g16s[256];
};

struct a9e_engine {
	/* caller-provided buffers (assign, then a9e_setup) */
	const struct a9t_tables *t;
	uint8_t *grey;		/* A9T_N: landscape grey of the update */
	uint8_t *old;		/* A9T_N: levels currently on the glass */
	uint8_t *nlv;		/* A9T_N: levels of the update */
	uint16_t *pairs;	/* A9T_N */
	uint16_t *arr[5];	/* 5 x A9T_N, persistent between updates (stock) */
	struct a9t_work *work;
	uint8_t *cache;		/* A9T_FRAME_BYTES, hold-pass frame cache */

	int16_t *ee;		/* optional A9E_EE_BYTES: per-pixel diffusion error of the run that made nlv */

	/* knobs */
	int flag_6c7;		/* engine byte this+0x6c7; 1 = no 0x100 added */
	int inc_enable;		/* 1 = damage-limited prepare (default 0: reference full pass) */

	/* damage-limited prepare state; see a9_eink_damage.c for the invariants */
	const uint8_t *dmg;	/* tile mask of grey changes since the last prepare; consumed (cleared)
				 * by a9e_prepare; NULL = unknown, full pass */
	int inc_valid;		/* old == nlv everywhere and pairs/ee/pk/sig describe nlv */
	int inc_prep_ok;	/* this prepare left that state consistent (a9e_end makes it valid) */
	int ee_valid;		/* ee holds the errors of the run that produced nlv */
	uint32_t inc_cap;	/* 0 = A9E_INC_CAP */
	int td_empty;		/* target damage (before steps/clear) was empty */
	struct a9e_cond_sig sig;	/* what nlv was conditioned with */
	uint8_t pk[A9E_NTILES];	/* tiles whose pairs may differ from steady (old<<4|old) */
	uint8_t tU[A9E_NTILES], tQ[A9E_NTILES];	/* a9d_prepare scratch (kept off the kernel stack) */
	struct a9e_cond_sig sgn;
	/* statistics (read by the driver, never acted on) */
	uint64_t inc_prepares, full_prepares;	/* prepares through each path */
	uint64_t inc_tiles, inc_rows;		/* tiles / rows reconditioned by the incremental path */
	uint32_t last_inc;			/* 1 = the last prepare was incremental */
	uint32_t last_tiles, last_rows;		/* its work: tiles reconditioned / pixels recomputed by the diffusion */
	uint64_t inc_capped;			/* incremental diffusions that hit the cap */

	/* state */
	struct a9t_state st;
	uint32_t pre_mode;	/* previous update's mode word */
	int have_pre;
	int unchanged;		/* prepared ordinary target equals old; caller must gate skips */

	/* current update */
	struct a9t_request req;
	uint32_t mode;		/* mode word the update runs with */
	int clear, clear_reset, bin, init;
	uint32_t type;
	struct a9e_rect rect;
	int mask;		/* zero all drive outside rect (non-stock option) */
	uint32_t nframes;
};

#ifdef __cplusplus
extern "C" {
#endif

/* Valid stock mode word? Low byte 0..10 is what the stock jump tables cover. */
int a9e_mode_valid(uint32_t mode);

/* Check a waveform blob's header and every table: lengths fit the rows,
 * drive codes 0..2 (3 = both drive lines, never produced by stock tables),
 * bin map < 14. Returns 0 or a negative reason code (A9E_WF_E*). The SHA-256
 * of the payload is checked by the caller (kernel: lib/crypto). */
#define A9E_WF_ESIZE (-1)
#define A9E_WF_EMAGIC (-2)
#define A9E_WF_EVERSION (-3)
#define A9E_WF_EBINMAP (-4)
#define A9E_WF_ELEN (-5)
#define A9E_WF_ECODE (-6)
#define A9E_WF_EINIT (-7)
#define A9E_WF_ELAST (-8)	/* a table pass would end on a driving frame */
int a9e_wf_check(const uint8_t *blob, size_t len, const struct a9t_tables **tables);

/* Check a control-template blob: header, sizes, and every word of the payload
 * (byte 0 == 0: no drive; byte 2 == 0x00; byte 3 == 0xff, as stock). Returns 0
 * and the payload, or a negative A9E_CT_E* code. The SHA-256s (header's and
 * A9E_CT_SHA256) are checked by the caller. */
#define A9E_CT_ESIZE (-1)
#define A9E_CT_EMAGIC (-2)
#define A9E_CT_EVERSION (-3)
#define A9E_CT_EBYTE0 (-4)
#define A9E_CT_EBYTE2 (-5)
#define A9E_CT_EBYTE3 (-6)
int a9e_ct_check(const uint8_t *blob, size_t len, const uint8_t **payload);

/* After assigning the buffers: engine state, old := 15 (white), arrays := 0. */
void a9e_setup(struct a9e_engine *e);

/* Portrait framebuffer rect [c1,c2) x [r1,r2) -> landscape rect. */
struct a9e_rect a9e_rect_from_fb(int c1, int r1, int c2, int r2);
/* Union; empty rects (x1 >= x2) are ignored. */
void a9e_rect_union(struct a9e_rect *acc, const struct a9e_rect *r);
int a9e_rect_empty(const struct a9e_rect *r);
struct a9e_rect a9e_rect_full(void);

/* Convert the framebuffer rect [c1,c2) x [r1,r2) of a portrait buffer
 * (src points at pixel (0,0), pitch in bytes) into the landscape grey buffer.
 * For A9E_FMT_RGBX, full rect and pitch 832*4 this equals a9t_rgba_to_grey().
 * If changed is not NULL, the bounding box (landscape) of the grey pixels that
 * differ from what grey held before is merged into *changed: the effective
 * damage, so a commit that redraws identical content causes no update. */
void a9e_fb_to_grey(uint8_t *grey, const uint8_t *src, uint32_t pitch, enum a9e_fmt fmt,
		    int c1, int r1, int c2, int r2, struct a9e_rect *changed);

/* Same result as a9e_fb_to_grey() (bit-identical grey and *changed), but the
 * source is read in storage order into a cached band first: use it when src
 * is write-combined or uncached memory. scratch: a9e_fb_scratch_bytes() bytes
 * (cached, any alignment); NULL = plain a9e_fb_to_grey(). */
#define A9E_BAND_ROWS 32
size_t a9e_fb_scratch_bytes(void);
void a9e_fb_to_grey_bounce(uint8_t *grey, const uint8_t *src, uint32_t pitch, enum a9e_fmt fmt,
			   int c1, int r1, int c2, int r2, struct a9e_rect *changed,
			   uint8_t *scratch);

/* Same grey bytes and *changed as a9e_fb_to_grey_bounce(), and additionally
 * tmask[tile] = 1 for every tile that contains a grey pixel that changed
 * (exactly: a tile is marked iff one of its pixels differs from what grey held
 * before). tmask may be NULL (then it is the bounce function). scratch may be
 * NULL (direct per-pixel path). Marks are only ever set, never cleared. */
void a9e_fb_to_grey_tiles(uint8_t *grey, const uint8_t *src, uint32_t pitch, enum a9e_fmt fmt,
			  int c1, int r1, int c2, int r2, struct a9e_rect *changed,
			  uint8_t *tmask, uint8_t *scratch);

/* Tile helpers. */
int a9e_tiles_any(const uint8_t *tm);
void a9e_tiles_or(uint8_t *dst, const uint8_t *src);
/* Bounding rect (landscape) of the marked tiles, empty if none. */
struct a9e_rect a9e_tiles_bbox(const uint8_t *tm);

/* The damage-limited state no longer describes the glass/levels (the caller
 * changed old, restored it, dropped a job whose damage was consumed, ...): the
 * next prepare is the reference full pass. Always safe to call. */
void a9e_inc_invalidate(struct a9e_engine *e);

/*
 * Stock-style same-target skip (eink-latency r2). The stock composer compares each
 * promoted target with the previous one before any conditioning and starts no
 * update when they are equal (memcmp of 0x14b880 bytes, composer 0x1a6ee4..0x1a6ef8).
 * Here: a9e_same_record() after a COMPLETED real update (a9e_end) keeps that
 * update's grey and the levels it left (old == nlv); a9e_same_target() then says,
 * without running a9e_prepare(), that a steady request for the same grey would be
 * prepared as unchanged: same conditioning signature (mode word, LUTs,
 * clear_shadow), a steady request (have_pre, flag_6c7, pre_mode == mode without
 * bit 8, no clear), and BOTH grey and old byte-equal to the record. Because old is
 * compared too, no invalidation site is needed for correctness: anything that
 * changed the levels since the record (abort restore, init, clear, dry run) makes
 * the check fail and the normal prepare decides. Soundness (skip => prepare finds
 * unchanged) is tested in test/same-equiv.c. The caller owns the two A9T_N buffers.
 */
struct a9e_same {
	uint8_t *grey, *old;		/* A9T_N each (caller-allocated) */
	struct a9e_cond_sig sig;
	int valid;
	uint64_t checks, hits;		/* statistics, never acted on */
};
void a9e_same_record(const struct a9e_engine *e, struct a9e_same *s);
int a9e_same_target(const struct a9e_engine *e, struct a9e_same *s, uint32_t mode, int clear);

/* Internal hooks of a9e_prepare (a9_eink_damage.c), reached only with inc_enable. */
int a9d_prepare(struct a9e_engine *e, int mask);	/* 1 = prepared incrementally */
void a9d_condition_full(struct a9e_engine *e);		/* conditioning, writes the carry checkpoints */
void a9d_after_full(struct a9e_engine *e);		/* record sig/pk/validity after a full pass */

/* Start the one-time init flash at bin. Frames via a9e_frame(). */
void a9e_begin_init(struct a9e_engine *e, int bin);

/* Start an update of e->grey. clear = the update's clear flag (GC16-type full
 * clear on Balanced/Clear, the stock clear request on Smooth/Speed). A mode
 * switch or clear forces a full-screen rect (the steps drive every pixel).
 * Returns the number of steps of the mode switch (0 = steady). This low-level
 * begin/frame API always renders; a caller wanting duplicate suppression must
 * use prepare and check unchanged before starting or powering the transport.
 * rect is a source-damage hint; the prepared rect covers complete conditioned
 * target differences (plus packed Clear sentinel neighbours), or the full
 * canvas for a required clear/transition. No conditioned targets are clipped.
 */
int a9e_begin(struct a9e_engine *e, uint32_t mode, int clear, const struct a9e_rect *rect,
	      int mask, int bin);

/*
 * The same update in two halves (frame pacing): a9e_prepare() is everything
 * of a9e_begin() that does not depend on the temperature bin (request
 * builder, step arrays, conditioning, pack, final type; the heavy part, tens
 * of ms on the phone), so it can run BEFORE the pipeline powers up;
 * a9e_start() then only sets the bin and rewinds the frame counters.
 * a9e_begin() == a9e_prepare() + a9e_start(). Between the two, only the init
 * flash may run (a9e_begin_init/a9e_frame/a9e_init_end): it uses neither the
 * levels nor the arrays. unchanged is only an equality result, not proof of
 * known glass: equality is eligible only with normal flag_6c7; callers must
 * exclude init, explicit clear/recovery, dry runs and
 * diagnostic jobs, and must invalidate eligibility after an aborted trial.
 * A skip does not call start/end or advance history/counters/power deadlines.
 * Final-target equality is stronger than stock's raw-luma equality predicate;
 * conditioning and raw-luma changed-pixel detection themselves are unchanged.
 * An update preceded by the init flash is prepared on
 * the levels the flash leaves (a9e_assume_white() first), and the flash then
 * ends with a9e_init_end(), which does not touch the levels.
 */
int a9e_prepare(struct a9e_engine *e, uint32_t mode, int clear, const struct a9e_rect *rect,
		int mask);
void a9e_start(struct a9e_engine *e, int bin);
void a9e_assume_white(struct a9e_engine *e);	/* old := 15, as a9e_end() of the flash */
void a9e_init_end(struct a9e_engine *e);	/* end the flash, levels untouched */

/* Next drive frame into out (A9T_FRAME_BYTES). Only byte 0 of each word is
 * written; bytes 1..3 must already hold the control template (a9tcon.h), so
 * pre-fill out (and e->cache) with it once. Returns 1 = more frames follow,
 * 0 = this was the last one (always a null frame, byte 0 all zero, for stock
 * tables). */
int a9e_frame(struct a9e_engine *e, uint8_t *out);

/* Commit the update: old := new, previous mode := this mode. */
void a9e_end(struct a9e_engine *e);

/* Abort an update midway (transport lost): the glass state is unknown, so the
 * next update is forced to a full clear. */
void a9e_abort(struct a9e_engine *e);

/* 1 if byte 0 (the data code) of every word is 0: no drive. Bytes 1..3 (the
 * control template) are not looked at. */
int a9e_frame_is_null(const uint8_t *out);

#ifdef __cplusplus
}
#endif
#endif
