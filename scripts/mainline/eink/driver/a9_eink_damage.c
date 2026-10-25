// SPDX-License-Identifier: GPL-2.0-only
/*
 * a9_eink damage-limited prepare (branch eink-damage). Pure C like the engine:
 * the kernel module and the host tests build this same file.
 *
 * What the reference a9e_prepare() does on every update: condition the WHOLE
 * canvas (1.36 Mpx; for Balanced/Smooth a serial 1-bit error diffusion), diff
 * it against `old`, pack all pairs. Nothing but the pixels that changed need
 * that work. This file does it for the changed tiles only and produces the
 * very same engine state (nlv, pairs, rect, unchanged), proven bit for bit by
 * test/damage-equiv.c over chained updates of real frames.
 *
 * Invariant J ("inc_valid"), established by a9e_end() of a prepare that left
 * the state consistent, destroyed by anything else:
 *   (a) old == nlv everywhere,
 *   (b) nlv == condition(grey_prev) for the signature `sig`, grey_prev being
 *       the grey of that prepare,
 *   (c) pairs[i] == old[i] << 4 | old[i] for every pixel outside the tiles
 *       marked in pk (pk = 8-neighbourhood of the tiles that changed in the
 *       previous prepare: stale transitions and clear sentinels live there),
 *   (d) for the 1-bit diffusion: ee holds every pixel's diffusion error of the
 *       run that produced nlv (ee_valid).
 * Then a prepare with a tile mask `dmg` covering EVERY grey change since the
 * previous prepare gives:
 *   - level conditioning that is a pure per-pixel function (threshold LUT,
 *     ordered dither, plain >> 4): recompute only the marked tiles;
 *   - serial diffusion: incremental dataflow over the stored per-pixel errors
 *     (see below): the marked tiles, then only pixels whose inputs changed;
 *   - diff against old only inside what was recomputed (J(a) says the rest is
 *     equal), repack only pk plus the neighbourhood of the new changes.
 * Anything that breaks the premises (mode/LUT/clear-shadow change = different
 * signature, clear flag, mode-switch steps, unknown damage, 16-level
 * diffusion, no checkpoints, J destroyed) takes the reference full pass, which
 * (with inc_enable) leaves J established again.
 */
#include "a9_eink_engine.h"

#include <string.h>

/* floor(v / 2^n), as in a9tcon.c (independent of the compiler's negative shift) */
static inline int32_t asr(int32_t v, int n)
{
	return v >= 0 ? (v >> n) : -(int32_t)((uint32_t)(-(int64_t)v + ((1 << n) - 1)) >> n);
}

/* ---- tile helpers ---------------------------------------------------------- */
int a9e_tiles_any(const uint8_t *tm)
{
	int i;

	for (i = 0; i < A9E_NTILES; i++)
		if (tm[i])
			return 1;
	return 0;
}

void a9e_tiles_or(uint8_t *dst, const uint8_t *src)
{
	int i;

	for (i = 0; i < A9E_NTILES; i++)
		dst[i] |= src[i];
}

struct a9e_rect a9e_tiles_bbox(const uint8_t *tm)
{
	struct a9e_rect r = { 0, 0, 0, 0 };
	int tx, ty;

	for (ty = 0; ty < A9E_TY; ty++)
		for (tx = 0; tx < A9E_TX; tx++)
			if (tm[ty * A9E_TX + tx]) {
				struct a9e_rect t = { tx * A9E_TILE, ty * A9E_TILE,
					(tx + 1) * A9E_TILE < A9T_W ? (tx + 1) * A9E_TILE : A9T_W,
					(ty + 1) * A9E_TILE < A9T_H ? (ty + 1) * A9E_TILE : A9T_H };

				a9e_rect_union(&r, &t);
			}
	return r;
}

void a9e_inc_invalidate(struct a9e_engine *e)
{
	e->inc_valid = 0;
}

/* ---- framebuffer -> grey, with the changed-tile mask -------------------------
 * Same arithmetic and the same bounding box as a9e_fb_to_grey()/_bounce(); the
 * only addition is the tile mark on a pixel whose grey byte changed. */
void a9e_fb_to_grey_tiles(uint8_t *grey, const uint8_t *src, uint32_t pitch, enum a9e_fmt fmt,
			  int c1, int r1, int c2, int r2, struct a9e_rect *changed,
			  uint8_t *tmask, uint8_t *scratch)
{
	int c, r, rb, bpp = fmt == A9E_FMT_R8 ? 1 : 4;
	int i0 = fmt == A9E_FMT_BGRX ? 2 : 0, i2 = fmt == A9E_FMT_BGRX ? 0 : 2;
	struct a9e_rect ch = { A9T_W, A9T_H, 0, 0 };
	size_t bw;

	if (!tmask) {
		a9e_fb_to_grey_bounce(grey, src, pitch, fmt, c1, r1, c2, r2, changed, scratch);
		return;
	}
	if (c1 < 0) c1 = 0;
	if (r1 < 0) r1 = 0;
	if (c2 > A9E_FB_W) c2 = A9E_FB_W;
	if (r2 > A9E_FB_H) r2 = A9E_FB_H;
	if (c1 >= c2 || r1 >= r2)
		return;
	if (!scratch) {
		/* direct per-pixel path (the reference loop order) */
		for (c = c1; c < c2; c++) {
			int y = A9E_FB_W - 1 - c, rmin = A9T_W, rmax = -1;
			uint8_t *o = grey + (size_t)y * A9T_W;
			uint8_t *trow = tmask + (size_t)(y >> 5) * A9E_TX;
			const uint8_t *p = src + (size_t)r1 * pitch + (size_t)c * bpp;

			for (r = r1; r < r2; r++, p += pitch) {
				uint8_t v = bpp == 1 ? p[0] :
					(uint8_t)((76u * p[i0] + 150u * p[1] + 30u * p[i2]) >> 8);

				if (o[r] != v) {
					if (r < rmin) rmin = r;
					rmax = r;
					trow[r >> 5] = 1;
				}
				o[r] = v;
			}
			if (rmax >= 0) {
				if (rmin < ch.x1) ch.x1 = rmin;
				if (rmax + 1 > ch.x2) ch.x2 = rmax + 1;
				if (y < ch.y1) ch.y1 = y;
				if (y + 1 > ch.y2) ch.y2 = y + 1;
			}
		}
		if (changed)
			a9e_rect_union(changed, &ch);
		return;
	}
	bw = (size_t)(c2 - c1) * bpp;
	for (rb = r1; rb < r2; rb += A9E_BAND_ROWS) {
		int n = r2 - rb < A9E_BAND_ROWS ? r2 - rb : A9E_BAND_ROWS;

		for (r = 0; r < n; r++)
			memcpy(scratch + (size_t)r * bw,
			       src + (size_t)(rb + r) * pitch + (size_t)c1 * bpp, bw);
		for (c = c1; c < c2; c++) {
			int y = A9E_FB_W - 1 - c, rmin = n, rmax = -1;
			uint8_t *o = grey + (size_t)y * A9T_W + rb;
			uint8_t *trow = tmask + (size_t)(y >> 5) * A9E_TX;
			const uint8_t *p = scratch + (size_t)(c - c1) * bpp;

			for (r = 0; r < n; r++, p += bw) {
				uint8_t v = bpp == 1 ? p[0] :
					(uint8_t)((76u * p[i0] + 150u * p[1] + 30u * p[i2]) >> 8);

				if (o[r] != v) {
					o[r] = v;
					if (r < rmin) rmin = r;
					rmax = r;
					trow[(rb + r) >> 5] = 1;
				}
			}
			if (rmax >= 0) {
				if (rb + rmin < ch.x1) ch.x1 = rb + rmin;
				if (rb + rmax + 1 > ch.x2) ch.x2 = rb + rmax + 1;
				if (y < ch.y1) ch.y1 = y;
				if (y + 1 > ch.y2) ch.y2 = y + 1;
			}
		}
	}
	if (changed)
		a9e_rect_union(changed, &ch);
}

/* ---- conditioning kinds ----------------------------------------------------- */
enum { K_FULL_ONLY, K_LUT3, K_ORD, K_SHIFT, K_ERRDIFF };

/* Mirrors the switch in a9t_condition(). */
static int cond_kind(uint32_t mode)
{
	uint32_t lo = mode & 0xff, hi = (mode >> 8) & 0xff;

	switch (lo) {
	case 1:
	case 6:
		return (hi & 4) ? K_ORD : K_ERRDIFF;
	case 9:
		return K_ORD;
	case 2:
		return K_FULL_ONLY;	/* 16-level diffusion over three rows: reference pass */
	case 3:
		return (hi & 1) ? K_FULL_ONLY : K_LUT3;
	default:
		return K_SHIFT;
	}
}

static void sig_of(uint32_t mode, const struct a9t_state *st, struct a9e_cond_sig *s)
{
	memset(s, 0, sizeof(*s));
	s->mode16 = mode & 0xffffu;
	s->clear_shadow = (uint32_t)st->clear_shadow;
	memcpy(s->g2, st->g2, 256);
	memcpy(s->g16, st->g16, 256);
	memcpy(s->g16s, st->g16s, 256);
}

static void sig_make(const struct a9e_engine *e, struct a9e_cond_sig *s)
{
	sig_of(e->mode, &e->st, s);
}

/* ---- stock-style same-target skip (see a9_eink_engine.h) -------------------------- */
void a9e_same_record(const struct a9e_engine *e, struct a9e_same *s)
{
	if (!s->grey || !s->old) {
		s->valid = 0;
		return;
	}
	/* after a9e_end(): old == nlv == condition(grey, sig of e->mode) */
	memcpy(s->grey, e->grey, A9T_N);
	memcpy(s->old, e->old, A9T_N);
	sig_make(e, &s->sig);
	s->valid = e->have_pre && !e->init;
}

int a9e_same_target(const struct a9e_engine *e, struct a9e_same *s, uint32_t mode, int clear)
{
	struct a9e_cond_sig sg;	/* about 780 bytes: fine on the kernel worker stack */

	if (!s->valid || clear || !e->have_pre || !e->flag_6c7 || e->init)
		return 0;
	/* a steady request: a9t_request_build() returns mode unchanged (flag_6c7), no steps */
	if ((e->pre_mode & 0xffu) != (mode & 0xffu) || e->pre_mode != (mode & ~0x100u))
		return 0;
	/* the signature the prepare would condition with: mode word + the current LUTs */
	sig_of(mode, &e->st, &sg);
	if (memcmp(&sg, &s->sig, sizeof(sg)))
		return 0;
	s->checks++;
	if (memcmp(e->grey, s->grey, A9T_N) || memcmp(e->old, s->old, A9T_N))
		return 0;
	s->hits++;
	return 1;
}

/* ---- per-pixel kinds over a rectangle ------------------------------------------ */
static void cond_rect(const struct a9e_engine *e, int kind, int x1, int y1, int x2, int y2)
{
	const uint8_t *lut3 = e->st.clear_shadow ? e->st.g16s : e->st.g16;
	const uint8_t *g2 = e->st.g2, *m = e->t->dither;
	int x, y;

	for (y = y1; y < y2; y++) {
		const uint8_t *g = e->grey + (size_t)y * A9T_W;
		uint8_t *lv = e->nlv + (size_t)y * A9T_W;

		switch (kind) {
		case K_LUT3:
			for (x = x1; x < x2; x++)
				lv[x] = lut3[g[x]] >> 4;
			break;
		case K_ORD:
			for (x = x1; x < x2; x++) {
				uint8_t a = g2[g[x]];

				lv[x] = (a > m[(y & 31) * 32 + (x & 31)] || a > 0xef) ? 15 : 0;
			}
			break;
		default: /* K_SHIFT */
			for (x = x1; x < x2; x++)
				lv[x] = g[x] >> 4;
		}
	}
}

/* ---- serial 1-bit error diffusion, stored errors, incremental dataflow ---------------
 * The reference (errdiff_1bit in a9tcon.c) scans every row serpentine (even rows
 * left to right, odd rows right to left) and pushes each pixel's error e on:
 *   (x + f, y)      asr(5e, 4)                      f = +1 / -1, not at the last scan position
 *   (x, y + 1)      asr(7e, 4)
 *   (x + f, y + 1)  asr(e, 3)   if its scan index is even, else (x - f, y + 1)
 * so the input of a pixel is a pure function of its own luma and of the stored
 * errors of at most four neighbours:
 *   p(x,y) = lut[grey] + asr(5 e(x-f, y), 4)                      [scan index > 0]
 *          + asr(7 e(x, y-1), 4) + asr(e(xb, y-1), 3) + asr(e(xc, y-1), 3)   [y > 0]
 *   xb = x - f', scan index of xb even;  xc = x + f', scan index of xc odd   (f' of row y-1)
 * and e = p > 127 ? p - 255 : p, level = p > 127 ? 15 : 0. The sum is order independent
 * (integer addition, asr applied per term), so recomputing a pixel from the CURRENT stored
 * errors of its inputs gives exactly the reference value. Incremental run: every pixel of a
 * damaged tile is recomputed, then any pixel with a changed input, in scan order, row by row,
 * until a row changes nothing and no damaged tile is left below. Where the error dies out (flat
 * black/white, i.e. all UI) that is the damaged tiles themselves; where it does not (photos) it
 * grows, and after A9E_INC_CAP pixels the rest is run plainly (same result, bounded cost). */
static inline int sidx(int y, int x)
{
	return (y & 1) ? A9T_W - 1 - x : x;
}

/* contribution of row y-1 into pixel (x, y), y > 0 */
static inline int32_t above(const int16_t *ep, int y, int x)
{
	int fp = ((y - 1) & 1) ? -1 : 1, xb = x - fp, xc = x + fp;
	int32_t a = asr(7 * (int32_t)ep[x], 4);

	if (xb >= 0 && xb < A9T_W && !(sidx(y - 1, xb) & 1))
		a += asr((int32_t)ep[xb], 3);
	if (xc >= 0 && xc < A9T_W && (sidx(y - 1, xc) & 1))
		a += asr((int32_t)ep[xc], 3);
	return a;
}

/* One row of the reference, accumulate form, from cur (row y with its carries) and nxt (row y+1);
 * also stores the errors. */
static void ed_row(int32_t *cur, int32_t *nxt, uint8_t *lvrow, int16_t *erow, int y)
{
	int rev = y & 1, last = y >= A9T_H - 1, f = rev ? -1 : 1, scan;

	for (scan = 0; scan < A9T_W; scan++) {
		int x = rev ? A9T_W - 1 - scan : scan;
		int32_t p = cur[x];
		int32_t er = p > 127 ? p - 255 : p;

		lvrow[x] = p > 127 ? 15 : 0;
		erow[x] = (int16_t)er;
		if (scan < A9T_W - 1)
			cur[x + f] += asr(5 * er, 4);
		if (!last) {
			nxt[x] += asr(7 * er, 4);
			if (scan < A9T_W - 1 && !(scan & 1))
				nxt[x + f] += asr(er, 3);
			else if (scan & 1)
				nxt[x - f] += asr(er, 3);
		}
	}
}

static void ed_load(int32_t *dst, const uint8_t *grey, const uint8_t *lut, int y)
{
	const uint8_t *g = grey + (size_t)y * A9T_W;
	int x;

	for (x = 0; x < A9T_W; x++)
		dst[x] = lut[g[x]];
}

/* Plain run of rows y0..H-1 from the stored errors of row y0-1 (y0 = 0: nothing above). */
static void ed_plain_from(struct a9e_engine *e, int y0)
{
	const uint8_t *g = e->grey, *lut = e->st.g2;
	int32_t *cur = e->work->row[0], *nxt = e->work->row[1], *t;
	int y, x;

	ed_load(cur, g, lut, y0);
	if (y0 > 0) {
		const int16_t *ep = e->ee + (size_t)(y0 - 1) * A9T_W;

		for (x = 0; x < A9T_W; x++)
			cur[x] += above(ep, y0, x);
	}
	if (y0 + 1 < A9T_H)
		ed_load(nxt, g, lut, y0 + 1);
	for (y = y0; y < A9T_H; y++) {
		ed_row(cur, nxt, e->nlv + (size_t)y * A9T_W, e->ee + (size_t)y * A9T_W, y);
		if (y + 1 >= A9T_H)
			break;
		t = cur; cur = nxt; nxt = t;
		if (y + 2 < A9T_H)
			ed_load(nxt, g, lut, y + 2);
	}
}

/* ---- the engine hooks --------------------------------------------------------- */
void a9d_condition_full(struct a9e_engine *e)
{
	if (cond_kind(e->mode) == K_ERRDIFF && e->ee) {
		/* the same levels as a9t_condition(), plus the stored errors for the incremental path */
		ed_plain_from(e, 0);
		e->ee_valid = 1;
		return;
	}
	e->ee_valid = 0;
	a9t_condition(&e->st, e->grey, e->mode, e->nlv, e->work);
}

void a9d_after_full(struct a9e_engine *e)
{
	sig_make(e, &e->sig);
	memset(e->pk, 1, sizeof(e->pk));	/* every tile may hold a transition: repack all next time */
	e->inc_prep_ok = 1;
	/* an update that proceeds ends with old == nlv; one with no change keeps it */
	e->inc_valid = e->td_empty && !e->req.count && !e->clear && !e->req.kind;
}

static void dilate(uint8_t *dst, const uint8_t *src)
{
	int tx, ty;

	memset(dst, 0, A9E_NTILES);
	for (ty = 0; ty < A9E_TY; ty++)
		for (tx = 0; tx < A9E_TX; tx++) {
			int dx, dy;

			if (!src[ty * A9E_TX + tx])
				continue;
			for (dy = -1; dy <= 1; dy++)
				for (dx = -1; dx <= 1; dx++) {
					int nx = tx + dx, ny = ty + dy;

					if (nx >= 0 && nx < A9E_TX && ny >= 0 && ny < A9E_TY)
						dst[ny * A9E_TX + nx] = 1;
				}
		}
}

static void tile_px(int t, int *x1, int *y1, int *x2, int *y2)
{
	int tx = t % A9E_TX, ty = t / A9E_TX;

	*x1 = tx * A9E_TILE;
	*y1 = ty * A9E_TILE;
	*x2 = *x1 + A9E_TILE < A9T_W ? *x1 + A9E_TILE : A9T_W;
	*y2 = *y1 + A9E_TILE < A9T_H ? *y1 + A9E_TILE : A9T_H;
}

/* diff old vs nlv over a rect: marks U, grows the row-union bounding box */
static void diff_rect(const struct a9e_engine *e, int x1, int y1, int x2, int y2, uint8_t *U,
		      struct a9e_rect *bb)
{
	int x, y;

	for (y = y1; y < y2; y++) {
		const uint8_t *o = e->old + (size_t)y * A9T_W, *n = e->nlv + (size_t)y * A9T_W;
		int first = A9T_W, last = -1;

		for (x = x1; x < x2; x++)
			if (o[x] != n[x]) {
				if (x < first) first = x;
				last = x;
				U[(y >> 5) * A9E_TX + (x >> 5)] = 1;
			}
		if (last >= 0) {
			struct a9e_rect row = { first, y, last + 1, y + 1 };

			a9e_rect_union(bb, &row);
		}
	}
}

/* Incremental diffusion over the damaged tiles. Marks U / grows bb for every pixel whose level
 * changed (nlv == old before: J), returns the number of pixels recomputed. */
static uint32_t ed_inc(struct a9e_engine *e, const uint8_t *tm, uint8_t *U, struct a9e_rect *bb,
		       int *capped)
{
	const uint8_t *g = e->grey, *lut = e->st.g2;
	uint8_t *cp = (uint8_t *)e->work->row[2], *cc = (uint8_t *)e->work->row[3], *tmp;
	uint32_t n = 0, cap = e->inc_cap ? e->inc_cap : A9E_INC_CAP;
	int tyf = -1, tyl = -1, ty, tx, y, ylast, cp_any = 0, cp_min = A9T_W, cp_max = -1;

	for (ty = 0; ty < A9E_TY; ty++)
		for (tx = 0; tx < A9E_TX; tx++)
			if (tm[ty * A9E_TX + tx]) {
				if (tyf < 0) tyf = ty;
				tyl = ty;
			}
	if (tyf < 0)
		return 0;
	ylast = (tyl + 1) * A9E_TILE - 1;
	if (ylast > A9T_H - 1)
		ylast = A9T_H - 1;
	memset(cp, 0, A9T_W);
	for (y = tyf * A9E_TILE; y < A9T_H; y++) {
		int f = (y & 1) ? -1 : 1, fp = ((y - 1) & 1) ? -1 : 1;
		int lo = A9T_W, hi = -1, x, prev = 0, cc_any = 0, cc_min = A9T_W, cc_max = -1;
		const int16_t *ep = y ? e->ee + (size_t)(y - 1) * A9T_W : NULL;
		int16_t *er = e->ee + (size_t)y * A9T_W;
		uint8_t *lv = e->nlv + (size_t)y * A9T_W;
		const uint8_t *trow = tm + (size_t)(y >> 5) * A9E_TX;

		if (y > ylast && !cp_any)
			break;
		if (n > cap) {
			/* the error does not die out: run the rest plainly (same result) */
			*capped = 1;
			ed_plain_from(e, y);
			diff_rect(e, 0, y, A9T_W, A9T_H, U, bb);
			return n;
		}
		if (y <= ylast) {
			int txmin = -1, txmax = -1;

			for (tx = 0; tx < A9E_TX; tx++)
				if (trow[tx]) {
					if (txmin < 0) txmin = tx;
					txmax = tx;
				}
			if (txmin >= 0) {
				lo = txmin * A9E_TILE;
				hi = (txmax + 1) * A9E_TILE - 1;
				if (hi > A9T_W - 1)
					hi = A9T_W - 1;
			}
		}
		if (cp_any) {
			int l2 = cp_min > 0 ? cp_min - 1 : 0, h2 = cp_max < A9T_W - 1 ? cp_max + 1 : A9T_W - 1;

			if (l2 < lo) lo = l2;
			if (h2 > hi) hi = h2;
		}
		if (hi >= 0) {
			memset(cc, 0, A9T_W);
			for (x = f > 0 ? lo : hi; x >= 0 && x < A9T_W; x += f) {
				int inh = x >= lo && x <= hi, cand = prev, changed = 0;

				if (!inh && !prev)
					break;
				if (!cand && inh) {
					cand = trow[x >> 5] != 0;
					if (!cand && cp_any) {
						int xb = x - fp, xc = x + fp;

						cand = cp[x] ||
						       (xb >= 0 && xb < A9T_W && cp[xb] && !(sidx(y - 1, xb) & 1)) ||
						       (xc >= 0 && xc < A9T_W && cp[xc] && (sidx(y - 1, xc) & 1));
					}
				}
				if (cand) {
					int32_t p = lut[g[(size_t)y * A9T_W + x]], e2;
					uint8_t l2;

					if (sidx(y, x) > 0)
						p += asr(5 * (int32_t)er[x - f], 4);
					if (y > 0)
						p += above(ep, y, x);
					e2 = p > 127 ? p - 255 : p;
					l2 = p > 127 ? 15 : 0;
					n++;
					if (e2 != er[x] || l2 != lv[x]) {
						changed = 1;
						if (l2 != lv[x]) {
							struct a9e_rect px = { x, y, x + 1, y + 1 };

							U[(y >> 5) * A9E_TX + (x >> 5)] = 1;
							a9e_rect_union(bb, &px);
						}
						er[x] = (int16_t)e2;
						lv[x] = l2;
						cc[x] = 1;
						cc_any = 1;
						if (x < cc_min) cc_min = x;
						if (x > cc_max) cc_max = x;
					}
				}
				prev = changed;
			}
		} else {
			memset(cc, 0, A9T_W);
		}
		tmp = cp; cp = cc; cc = tmp;
		cp_any = cc_any; cp_min = cc_min; cp_max = cc_max;
	}
	return n;
}

int a9d_prepare(struct a9e_engine *e, int mask)
{
	const uint8_t *tm = e->dmg;
	uint8_t *U = e->tU, *Q = e->tQ;
	struct a9e_cond_sig *sg = &e->sgn;
	struct a9e_rect td = { 0, 0, 0, 0 };
	int kind, t, rows = 0, ntiles = 0;
	size_t i;

	e->dmg = NULL;
	kind = cond_kind(e->mode);
	sig_make(e, sg);
	if (!tm || !e->inc_valid || !e->have_pre || e->clear || e->req.count || e->req.kind ||
	    kind == K_FULL_ONLY || (kind == K_ERRDIFF && (!e->ee || !e->ee_valid)) ||
	    memcmp(sg, &e->sig, sizeof(*sg)))
		return 0;

	for (t = 0; t < A9E_NTILES; t++)
		ntiles += tm[t] != 0;
	if (kind == K_ERRDIFF ? (uint32_t)ntiles * A9E_TILE * A9E_TILE > A9E_INC_GATE_PX :
				ntiles * 5 > A9E_NTILES * 3)
		return 0;	/* too much damage for the incremental path to pay: reference pass */
	ntiles = 0;
	memset(U, 0, A9E_NTILES);
	if (kind == K_ERRDIFF) {
		if (a9e_tiles_any(tm)) {
			int capped = 0;

			rows = (int)ed_inc(e, tm, U, &td, &capped);
			e->inc_capped += (uint64_t)capped;
			for (t = 0; t < A9E_NTILES; t++)
				ntiles += tm[t] != 0;
		}
	} else {
		for (t = 0; t < A9E_NTILES; t++) {
			int x1, y1, x2, y2;

			if (!tm[t])
				continue;
			tile_px(t, &x1, &y1, &x2, &y2);
			cond_rect(e, kind, x1, y1, x2, y2);
			diff_rect(e, x1, y1, x2, y2, U, &td);
			ntiles++;
		}
	}

	e->rect = td;
	e->td_empty = a9e_rect_empty(&td);
	e->unchanged = e->have_pre && e->flag_6c7 &&
		(e->pre_mode == (e->mode & ~0x100u)) &&
		!e->req.count && !e->req.kind && !e->clear &&
		a9e_rect_empty(&e->rect);
	e->mask = mask && !e->req.count && !e->clear;

	/* pairs: stale tiles of the previous prepare plus the neighbourhood of the new changes */
	dilate(Q, U);
	for (t = 0; t < A9E_NTILES; t++) {
		int x1, y1, x2, y2, x, y;

		if (!(Q[t] | e->pk[t]))
			continue;
		tile_px(t, &x1, &y1, &x2, &y2);
		for (y = y1; y < y2; y++) {
			size_t o = (size_t)y * A9T_W;

			for (x = x1; x < x2; x++)
				e->pairs[o + x] = (uint16_t)(e->old[o + x] << 4 | e->nlv[o + x]);
		}
	}
	if ((e->mode & 0xff) == 3 && !e->clear) {
		/* the clear sentinel, as a9t_pack(); a sentinel source pixel changed, so lies in U */
#define STEADY(j) (e->old[j] == 15 && e->nlv[j] == 15)
		for (t = 0; t < A9E_NTILES; t++) {
			int x1, y1, x2, y2, x, y;

			if (!U[t])
				continue;
			tile_px(t, &x1, &y1, &x2, &y2);
			for (y = y1; y < y2; y++)
				for (x = x1; x < x2; x++) {
					i = (size_t)y * A9T_W + (size_t)x;
					if (e->old[i] == 15 || e->nlv[i] != 15)
						continue;
					if (x > 0 && STEADY(i - 1))
						e->pairs[i - 1] = 256;
					else if (x < A9T_W - 1 && STEADY(i + 1))
						e->pairs[i + 1] = 256;
					else if (y > 0 && STEADY(i - A9T_W))
						e->pairs[i - A9T_W] = 256;
					else if (y < A9T_H - 1 && STEADY(i + A9T_W))
						e->pairs[i + A9T_W] = 256;
				}
		}
#undef STEADY
		for (t = 0; t < A9E_NTILES; t++) {
			int x1, y1, x2, y2, x, y;

			if (!Q[t])
				continue;
			tile_px(t, &x1, &y1, &x2, &y2);
			for (y = y1; y < y2; y++)
				for (x = x1; x < x2; x++)
					if (e->pairs[(size_t)y * A9T_W + (size_t)x] == 256) {
						struct a9e_rect px = { x, y, x + 1, y + 1 };

						a9e_rect_union(&e->rect, &px);
					}
		}
	}
	memcpy(e->pk, Q, A9E_NTILES);
	e->type = (uint32_t)a9t_final_type(e->mode, e->clear);

	e->inc_prep_ok = 1;
	e->inc_valid = e->td_empty;	/* an update that proceeds restores it in a9e_end() */
	e->inc_prepares++;
	e->inc_tiles += (uint64_t)ntiles;
	e->inc_rows += (uint64_t)rows;
	e->last_inc = 1;
	e->last_tiles = (uint32_t)ntiles;
	e->last_rows = (uint32_t)rows;
	return 1;
}
