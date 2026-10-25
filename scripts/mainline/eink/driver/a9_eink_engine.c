// SPDX-License-Identifier: GPL-2.0-only
/*
 * a9_eink engine, see a9_eink_engine.h. No vendor data, no allocation;
 * needs only memcpy/memset (through a9tcon.h's includes).
 */
#include "a9_eink_engine.h"

#include <string.h>

int a9e_mode_valid(uint32_t mode)
{
	return (mode & 0xff) <= 10 && mode <= 0xffff;
}

/* ---- waveform blob --------------------------------------------------------- */
struct wf_cls {
	size_t off;
	uint32_t rs;
};

static const struct wf_cls WF_CLS[] = {
	{ offsetof(struct a9t_tables, clear3), A9T_RS_CLEAR },
	{ offsetof(struct a9t_tables, type2), A9T_RS_CLEAR },
	{ offsetof(struct a9t_tables, balanced), A9T_RS_BAL },
	{ offsetof(struct a9t_tables, smooth), A9T_RS_SMOOTH },
	{ offsetof(struct a9t_tables, speed), A9T_RS_SMOOTH },
};

static uint32_t rd32(const uint8_t *p)
{
	return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

int a9e_wf_check(const uint8_t *blob, size_t len, const struct a9t_tables **tables)
{
	const struct a9t_tables *t;
	size_t c;
	int b, i;

	if (len < sizeof(struct a9e_wf_header))
		return A9E_WF_ESIZE;
	if (rd32(blob) != A9E_WF_MAGIC)
		return A9E_WF_EMAGIC;
	if (rd32(blob + 4) != A9E_WF_VERSION)
		return A9E_WF_EVERSION;
	if (rd32(blob + 8) != sizeof(struct a9e_wf_header) ||
	    rd32(blob + 12) != sizeof(struct a9t_tables) ||
	    len != sizeof(struct a9e_wf_header) + sizeof(struct a9t_tables))
		return A9E_WF_ESIZE;
	t = (const struct a9t_tables *)(blob + sizeof(struct a9e_wf_header));

	for (i = 0; i < 51; i++)
		if (t->bin_map[i] >= A9T_BINS)
			return A9E_WF_EBINMAP;
	for (b = 0; b < A9T_BINS; b++) {
		uint32_t n = t->init[(size_t)b * A9T_INIT_STRIDE];
		if (n < 1 || n > A9T_INIT_STRIDE - 1)
			return A9E_WF_EINIT;
		if (t->init[(size_t)b * A9T_INIT_STRIDE + n] & 3)
			return A9E_WF_ELAST;
	}
	for (c = 0; c < sizeof(WF_CLS) / sizeof(WF_CLS[0]); c++) {
		const uint8_t *base = (const uint8_t *)t + WF_CLS[c].off;
		uint32_t rs = WF_CLS[c].rs;
		for (b = 0; b < A9T_BINS; b++) {
			const uint8_t *bin = base + (size_t)b * A9T_SLOTS * rs;
			uint32_t n = bin[0], s, f;
			/* the frame count of a bin is row 0's length byte; every
			 * frame index read must stay inside its own row */
			if (n < 2 || n > rs - 1)
				return A9E_WF_ELEN;
			for (s = 0; s < A9T_SLOTS; s++) {
				for (f = 1; f <= n; f++)
					if (bin[(size_t)s * rs + f] > 2)
						return A9E_WF_ECODE;
				/* table passes (Clear, type 2, Balanced full) end on
				 * their last column: it must not drive */
				if (c <= 2 && bin[(size_t)s * rs + n])
					return A9E_WF_ELAST;
			}
		}
	}
	if (tables)
		*tables = t;
	return 0;
}

/* ---- control template ------------------------------------------------------ */
int a9e_ct_check(const uint8_t *blob, size_t len, const uint8_t **payload)
{
	const uint8_t *p;
	size_t i;

	if (len < sizeof(struct a9e_ct_header))
		return A9E_CT_ESIZE;
	if (rd32(blob) != A9E_CT_MAGIC)
		return A9E_CT_EMAGIC;
	if (rd32(blob + 4) != A9E_CT_VERSION)
		return A9E_CT_EVERSION;
	if (rd32(blob + 8) != sizeof(struct a9e_ct_header) ||
	    rd32(blob + 12) != A9T_FRAME_BYTES ||
	    len != sizeof(struct a9e_ct_header) + A9T_FRAME_BYTES)
		return A9E_CT_ESIZE;
	p = blob + sizeof(struct a9e_ct_header);
	for (i = 0; i < A9T_FRAME_BYTES; i += 4) {
		if (p[i])
			return A9E_CT_EBYTE0;
		if (p[i + 2])
			return A9E_CT_EBYTE2;
		if (p[i + 3] != 0xff)
			return A9E_CT_EBYTE3;
	}
	if (payload)
		*payload = p;
	return 0;
}

/* ---- setup and rects ------------------------------------------------------- */
void a9e_setup(struct a9e_engine *e)
{
	int k;

	a9t_init(&e->st, e->t, e->cache);
	memset(e->old, 15, A9T_N);
	memset(e->nlv, 15, A9T_N);
	for (k = 0; k < 5; k++)
		memset(e->arr[k], 0, (size_t)A9T_N * 2); /* stock: zeroed at TCON start */
	e->have_pre = 0;
	e->unchanged = 0;
	e->pre_mode = 0;
	e->nframes = 0;
	e->init = 0;
	e->inc_valid = e->inc_prep_ok = e->ee_valid = 0;
	e->dmg = NULL;
	memset(e->pk, 1, sizeof(e->pk));
}

struct a9e_rect a9e_rect_from_fb(int c1, int r1, int c2, int r2)
{
	/* grey[y][x] = fb(row x, column 823 - y) */
	struct a9e_rect r = { r1, A9E_FB_W - c2, r2, A9E_FB_W - c1 };
	return r;
}

int a9e_rect_empty(const struct a9e_rect *r)
{
	return r->x1 >= r->x2 || r->y1 >= r->y2;
}

struct a9e_rect a9e_rect_full(void)
{
	struct a9e_rect r = { 0, 0, A9T_W, A9T_H };
	return r;
}

void a9e_rect_union(struct a9e_rect *acc, const struct a9e_rect *r)
{
	if (a9e_rect_empty(r))
		return;
	if (a9e_rect_empty(acc)) {
		*acc = *r;
		return;
	}
	if (r->x1 < acc->x1) acc->x1 = r->x1;
	if (r->y1 < acc->y1) acc->y1 = r->y1;
	if (r->x2 > acc->x2) acc->x2 = r->x2;
	if (r->y2 > acc->y2) acc->y2 = r->y2;
}

/* ---- framebuffer -> grey ----------------------------------------------------
 * Stock luma (76 b0 + 150 b1 + 30 b2) >> 8 on bytes 0..2 of an RGBA_8888
 * buffer (b0 = R, INFERRED from the Android default format). The weights stay
 * on R, G, B for the other byte orders. */
void a9e_fb_to_grey(uint8_t *grey, const uint8_t *src, uint32_t pitch, enum a9e_fmt fmt,
		    int c1, int r1, int c2, int r2, struct a9e_rect *changed)
{
	int c, r, bpp = fmt == A9E_FMT_R8 ? 1 : 4;
	int i0 = fmt == A9E_FMT_BGRX ? 2 : 0, i2 = fmt == A9E_FMT_BGRX ? 0 : 2;
	struct a9e_rect ch = { A9T_W, A9T_H, 0, 0 };

	if (c1 < 0) c1 = 0;
	if (r1 < 0) r1 = 0;
	if (c2 > A9E_FB_W) c2 = A9E_FB_W;
	if (r2 > A9E_FB_H) r2 = A9E_FB_H;
	for (c = c1; c < c2; c++) {
		int y = A9E_FB_W - 1 - c, rmin = A9T_W, rmax = -1;
		uint8_t *o = grey + (size_t)y * A9T_W;
		const uint8_t *p = src + (size_t)r1 * pitch + (size_t)c * bpp;
		for (r = r1; r < r2; r++, p += pitch) {
			uint8_t v = bpp == 1 ? p[0] :
				(uint8_t)((76u * p[i0] + 150u * p[1] + 30u * p[i2]) >> 8);
			if (changed && o[r] != v) {
				if (r < rmin) rmin = r;
				rmax = r;
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
}

/* ---- framebuffer -> grey through a cached bounce band ----------------------------
 * a9e_fb_to_grey() above walks the portrait source column by column: one 1-4
 * byte load per pixel with a pitch-sized stride. On a write-combined (Normal-NC)
 * dumb buffer every such load is its own uncached bus read (MEASURED on the A9:
 * 220 ms per full-frame commit = 162 ns per pixel), and the strided walk also
 * defeats any prefetch. This variant reads the source in storage order, row
 * segments of the damage columns copied with memcpy (wide, back-to-back loads)
 * into a small cached band of A9E_BAND_ROWS rows, then does the same per-pixel
 * conversion and the same transposed store from that band. Output (grey bytes
 * and *changed) is bit-identical to a9e_fb_to_grey(). scratch must hold
 * a9e_fb_scratch_bytes(); NULL falls back to a9e_fb_to_grey(). */
size_t a9e_fb_scratch_bytes(void)
{
	return (size_t)A9E_BAND_ROWS * A9E_FB_W * 4;
}

void a9e_fb_to_grey_bounce(uint8_t *grey, const uint8_t *src, uint32_t pitch, enum a9e_fmt fmt,
			   int c1, int r1, int c2, int r2, struct a9e_rect *changed,
			   uint8_t *scratch)
{
	int c, r, rb, bpp = fmt == A9E_FMT_R8 ? 1 : 4;
	int i0 = fmt == A9E_FMT_BGRX ? 2 : 0, i2 = fmt == A9E_FMT_BGRX ? 0 : 2;
	struct a9e_rect ch = { A9T_W, A9T_H, 0, 0 };
	size_t bw;

	if (!scratch) {
		a9e_fb_to_grey(grey, src, pitch, fmt, c1, r1, c2, r2, changed);
		return;
	}
	if (c1 < 0) c1 = 0;
	if (r1 < 0) r1 = 0;
	if (c2 > A9E_FB_W) c2 = A9E_FB_W;
	if (r2 > A9E_FB_H) r2 = A9E_FB_H;
	if (c1 >= c2 || r1 >= r2)
		return;	/* the reference merges an empty box: nothing */
	bw = (size_t)(c2 - c1) * bpp;
	for (rb = r1; rb < r2; rb += A9E_BAND_ROWS) {
		int n = r2 - rb < A9E_BAND_ROWS ? r2 - rb : A9E_BAND_ROWS;

		for (r = 0; r < n; r++)
			memcpy(scratch + (size_t)r * bw,
			       src + (size_t)(rb + r) * pitch + (size_t)c1 * bpp, bw);
		for (c = c1; c < c2; c++) {
			int y = A9E_FB_W - 1 - c, rmin = n, rmax = -1;
			uint8_t *o = grey + (size_t)y * A9T_W + rb;
			const uint8_t *p = scratch + (size_t)(c - c1) * bpp;

			for (r = 0; r < n; r++, p += bw) {
				uint8_t v = bpp == 1 ? p[0] :
					(uint8_t)((76u * p[i0] + 150u * p[1] + 30u * p[i2]) >> 8);

				if (o[r] != v) {
					o[r] = v;
					if (r < rmin) rmin = r;
					rmax = r;
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

/* Complete conditioned target damage, not the source-luma damage hint.
 * Diffusion can propagate arbitrarily far beyond a one-pixel input change.
 */
static struct a9e_rect target_damage(const uint8_t *old, const uint8_t *target)
{
	struct a9e_rect rect = { 0, 0, 0, 0 };
	int x, y;

	for (y = 0; y < A9T_H; y++) {
		int first = A9T_W, last = -1;

		for (x = 0; x < A9T_W; x++) {
			size_t i = (size_t)y * A9T_W + x;

			if (old[i] != target[i]) {
				if (x < first) first = x;
				last = x;
			}
		}
		if (last >= 0) {
			struct a9e_rect row = { first, y, last + 1, y + 1 };

			a9e_rect_union(&rect, &row);
		}
	}
	return rect;
}

/* ---- update ------------------------------------------------------------------ */
void a9e_begin_init(struct a9e_engine *e, int bin)
{
	e->inc_valid = 0;
	e->init = 1;
	e->unchanged = 0;
	e->bin = bin;
	e->nframes = 0;
	e->st.counter = 0;
}

void a9e_assume_white(struct a9e_engine *e)
{
	memset(e->old, 15, A9T_N);	/* what a9e_end() of the init flash sets */
	e->inc_valid = 0;
}

void a9e_init_end(struct a9e_engine *e)
{
	e->init = 0;	/* levels untouched: a9e_assume_white() ran before a9e_prepare() */
}

int a9e_prepare(struct a9e_engine *e, uint32_t mode, int clear, const struct a9e_rect *rect,
		int mask)
{
	uint32_t pre = e->have_pre ? e->pre_mode : mode;
	size_t y;

	e->inc_prep_ok = 0;
	e->last_inc = 0;
	e->init = 0;
	e->clear = clear ? 1 : 0;
	e->rect = *rect;
	e->nframes = 0;
	memset(&e->req, 0, sizeof(e->req));
	e->mode = a9t_request_build(&e->req, pre, mode, e->flag_6c7, &e->clear, &e->clear_reset);
	/* Condition before request arrays can replace the committed old levels. */
	if (e->inc_enable && a9d_prepare(e, mask))
		return (int)e->req.count;	/* damage-limited: same state as the full pass below */
	if (e->inc_enable)
		a9d_condition_full(e);
	else
		a9t_condition(&e->st, e->grey, e->mode, e->nlv, e->work);
	e->dmg = NULL;
	e->rect = target_damage(e->old, e->nlv);
	e->td_empty = a9e_rect_empty(&e->rect);
	/* have_pre alone is not a recovery authorization: the caller also excludes
	 * unknown/aborted history, pending init, dry runs and explicit recovery.
	 * Compare the full mode except bit 8, which end() normalizes as before.
	 */
	e->unchanged = e->have_pre && e->flag_6c7 &&
		(e->pre_mode == (e->mode & ~0x100u)) &&
		!e->req.count && !e->req.kind && !e->clear &&
		a9e_rect_empty(&e->rect);
	if (e->req.count)
		a9t_request_arrays(&e->req, e->old, e->arr);	/* old := last step level */
	if (e->req.count || e->clear || e->req.kind)
		e->rect = a9e_rect_full();
	e->mask = mask && !e->req.count && !e->clear;

	/* Preserve every conditioned level, including diffusion outside raw damage. */
	a9t_pack(e->old, e->nlv, e->mode, e->clear, e->pairs);
	/* Clear's sentinel may drive a steady-white neighbour outside target damage.
	 * Retain that exact packed footprint when the optional mask is selected.
	 */
	if ((e->mode & 0xff) == 3 && !e->clear) {
		for (y = 0; y < A9T_H; y++) {
			size_t x;

			for (x = 0; x < A9T_W; x++) {
				if (e->pairs[y * A9T_W + x] == 256) {
					struct a9e_rect pixel = { (int)x, (int)y,
						(int)x + 1, (int)y + 1 };

					a9e_rect_union(&e->rect, &pixel);
				}
			}
		}
	}
	e->type = (uint32_t)a9t_final_type(e->mode, e->clear);
	if (e->inc_enable)
		a9d_after_full(e);
	e->full_prepares++;
	return (int)e->req.count;
}

void a9e_start(struct a9e_engine *e, int bin)
{
	e->init = 0;
	e->bin = bin;
	e->nframes = 0;
	a9t_update_begin(&e->st);
	e->st.counter = 0;
}

int a9e_begin(struct a9e_engine *e, uint32_t mode, int clear, const struct a9e_rect *rect,
	      int mask, int bin)
{
	int steps = a9e_prepare(e, mode, clear, rect, mask);

	a9e_start(e, bin);
	return steps;
}

static void mask_frame(uint8_t *out, const struct a9e_rect *rc)
{
	int y, w, k;

	for (y = 0; y < A9T_H; y++) {
		uint8_t *o = out + (size_t)(y + A9T_HEAD_ROWS) * A9T_TW * 4;
		if (y < rc->y1 || y >= rc->y2) {
			for (w = 0; w < A9T_DATA_WORDS; w++)
				o[w * 4] = 0;
			continue;
		}
		for (w = 0; w < A9T_DATA_WORDS; w++) {
			int x0 = 4 * w;
			uint8_t m = 0;
			if (x0 >= rc->x1 && x0 + 4 <= rc->x2)
				continue;
			for (k = 0; k < 4; k++)
				if (x0 + k >= rc->x1 && x0 + k < rc->x2)
					m |= (uint8_t)(3u << (6 - 2 * k));
			o[w * 4] &= m;
		}
	}
}

int a9e_frame(struct a9e_engine *e, uint8_t *out)
{
	int more;

	if (e->init) {
		more = a9t_init_frame(&e->st, e->bin, out);
	} else {
		more = a9t_update_frame(&e->st, &e->req, e->arr, e->type, e->bin, e->pairs, out);
		if (e->mask)
			mask_frame(out, &e->rect);
	}
	e->nframes++;
	return more;
}

void a9e_end(struct a9e_engine *e)
{
	if (e->init) {
		e->init = 0;
		memset(e->old, 15, A9T_N);	/* glass taken as white after the flash (INFERRED) */
		e->inc_valid = 0;
		return;
	}
	memcpy(e->old, e->nlv, A9T_N);
	e->pre_mode = e->mode & ~0x100u;	/* the builder only looks at the low byte */
	e->have_pre = 1;
	e->inc_valid = e->inc_prep_ok;	/* old == nlv again; valid only if this prepare kept the state */
}

void a9e_abort(struct a9e_engine *e)
{
	e->inc_valid = 0;
	e->init = 0;
	e->unchanged = 0;
	e->st.counter = 0;
	e->st.step = 0;
	/* nothing reliable is known about the glass: keep old as is; the caller
	 * forces the next update to clear = 1 */
}

int a9e_frame_is_null(const uint8_t *out)
{
	int r, w;

	for (r = 0; r < A9T_TH; r++) {
		const uint8_t *o = out + (size_t)r * A9T_TW * 4;
		for (w = 0; w < A9T_TW; w++)
			if (o[w * 4])
				return 0;
	}
	return 1;
}
