#define _POSIX_C_SOURCE 200809L
/* damage-equiv: the damage-limited prepare (a9_eink_damage.c) == the reference
 * full pass, bit for bit, over chained updates of real frames.
 *
 *   damage-equiv BLOB TEMPLATE FIXTURE_DIR [bench]
 *
 * FIXTURE_DIR holds raw 824x1648 RGBA frames real*.rgba (screencaps of the phone).
 * Two engines run in lockstep on identical input: R = reference (inc_enable 0),
 * I = incremental (inc_enable 1, tile mask from a9e_fb_to_grey_tiles). After every
 * prepare the whole engine state is compared (nlv, pairs, rect, unchanged, mask,
 * type, request); for every update that is not skipped the sha256 of EVERY frame
 * is compared (and so the glass result). Checks:
 *  1. a9e_fb_to_grey_tiles: grey and *changed equal the bounce function, the tile
 *     mask is exactly the set of tiles holding a changed pixel (brute force);
 *  2. chained edits (strips, tiles, text-line sized rects, noise, two distant rects,
 *     identical commits, coalesced commits, full-frame clips of unchanged content)
 *     in modes 513/515/518/521 and 0x401/0x208 (local kinds) and 0x202/0x303
 *     (16-level diffusion: reference pass); the same with mode switches, the clear
 *     flag, LUT (controls) and clear_shadow changes, an abort, a dry-run restore,
 *     invalidation, a lost damage mask;
 *  3. unchanged pixels are never driven: in every incremental steady update, every
 *     pixel whose pair is steady (old == new, no sentinel) has code 0 in every frame;
 *  4. the incremental path was really used (counters), and mutations are caught.
 * "bench": host timings of prepare, reference vs incremental (report only).
 */
#include "a9_eink_engine.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static int fails, checks, quiet, quick;	/* quick: fewer steps, the steady-pixel drive scan on one frame per update (for qemu) */
#define CHECK(c, ...) do { checks++; if (!(c)) { fails++; if (!quiet && fails < 40) { fprintf(stderr, "FAIL %s:%d ", __FILE__, __LINE__); fprintf(stderr, __VA_ARGS__); fprintf(stderr, "\n"); } } } while (0)

#define FBN ((size_t)A9E_FB_W * A9E_FB_H)

static uint8_t *slurp(const char *path, size_t *len)
{
	FILE *f = fopen(path, "rb");
	long n;
	uint8_t *b;

	if (!f) { perror(path); exit(2); }
	fseek(f, 0, SEEK_END); n = ftell(f); fseek(f, 0, SEEK_SET);
	b = malloc((size_t)n ? (size_t)n : 1);
	if (!b || fread(b, 1, (size_t)n, f) != (size_t)n) exit(2);
	fclose(f);
	*len = (size_t)n;
	return b;
}

static double now_ms(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec * 1e3 + ts.tv_nsec / 1e6;
}

static uint32_t rng = 987654321;
static uint32_t rnd(void)
{
	rng = rng * 1664525u + 1013904223u;
	return rng >> 8;
}

static const struct a9t_tables *T;
static uint8_t *TMPL, *OUT, *SCR;

static void alloc_engine(struct a9e_engine *e, int inc)
{
	int k;

	memset(e, 0, sizeof(*e));
	e->t = T;
	e->grey = malloc(A9T_N); e->old = malloc(A9T_N); e->nlv = malloc(A9T_N);
	e->pairs = malloc(A9T_N * 2);
	for (k = 0; k < 5; k++) e->arr[k] = malloc(A9T_N * 2);
	e->work = malloc(sizeof(*e->work));
	e->cache = malloc(A9T_FRAME_BYTES);
	e->ee = (int16_t *)malloc(A9E_EE_BYTES);
	e->flag_6c7 = 1;
	e->inc_enable = inc;
	a9e_setup(e);
	memcpy(e->cache, TMPL, A9T_FRAME_BYTES);
}

/* ---- 1. the converter ---------------------------------------------------------- */
static void tiles_brute(const uint8_t *before, const uint8_t *after, uint8_t *tm)
{
	memset(tm, 0, A9E_NTILES);
	for (int y = 0; y < A9T_H; y++)
		for (int x = 0; x < A9T_W; x++)
			if (before[(size_t)y * A9T_W + x] != after[(size_t)y * A9T_W + x])
				tm[(y >> 5) * A9E_TX + (x >> 5)] = 1;
}

static void test_converter(uint8_t **frames, int nf)
{
	uint8_t *g0 = malloc(A9T_N), *ga = malloc(A9T_N), *gb = malloc(A9T_N), *gc = malloc(A9T_N);
	uint8_t tm[A9E_NTILES], want[A9E_NTILES];
	int f, k, fmt;

	memset(g0, 0xff, A9T_N);
	for (fmt = 0; fmt < 3; fmt++)
		for (f = 0; f < nf; f++) {
			uint8_t *src = frames[f];
			uint32_t pitch = A9E_FB_W * 4;
			uint8_t *r8 = NULL;

			if (fmt == A9E_FMT_R8) {
				r8 = malloc(FBN);
				for (size_t i = 0; i < FBN; i++) r8[i] = (uint8_t)(src[i * 4] ^ src[i * 4 + 1]);
				src = r8; pitch = A9E_FB_W;
			}
			for (k = 0; k < 14; k++) {
				int c1 = 0, c2 = A9E_FB_W, r1 = 0, r2 = A9E_FB_H;
				struct a9e_rect ca = { 0, 0, 0, 0 }, cb = { 0, 0, 0, 0 }, cc = { 0, 0, 0, 0 };

				if (k) {
					c1 = (int)(rnd() % (A9E_FB_W + 40)) - 20; c2 = c1 + (int)(rnd() % 500) + 1;
					r1 = (int)(rnd() % (A9E_FB_H + 40)) - 20; r2 = r1 + (int)(rnd() % 900) + 1;
				}
				memcpy(ga, g0, A9T_N); memcpy(gb, g0, A9T_N); memcpy(gc, g0, A9T_N);
				a9e_fb_to_grey_bounce(ga, src, pitch, fmt, c1, r1, c2, r2, &ca, SCR);
				memset(tm, 0, sizeof(tm));
				a9e_fb_to_grey_tiles(gb, src, pitch, fmt, c1, r1, c2, r2, &cb, tm, SCR);
				CHECK(!memcmp(ga, gb, A9T_N), "tiles(bounce): grey differs");
				CHECK(!memcmp(&ca, &cb, sizeof(ca)), "tiles(bounce): changed differs");
				tiles_brute(g0, gb, want);
				CHECK(!memcmp(tm, want, sizeof(tm)), "tiles(bounce): mask != brute force (fmt %d rect %d,%d,%d,%d)", fmt, c1, r1, c2, r2);
				memset(tm, 0, sizeof(tm));
				a9e_fb_to_grey_tiles(gc, src, pitch, fmt, c1, r1, c2, r2, &cc, tm, NULL);
				CHECK(!memcmp(ga, gc, A9T_N), "tiles(direct): grey differs");
				CHECK(!memcmp(&ca, &cc, sizeof(ca)), "tiles(direct): changed differs");
				CHECK(!memcmp(tm, want, sizeof(tm)), "tiles(direct): mask != brute force");
				if (k == 13) memcpy(g0, ga, A9T_N);
			}
			free(r8);
		}
	free(g0); free(ga); free(gb); free(gc);
}

/* ---- 2. lockstep engines ------------------------------------------------------------- */
static struct a9e_engine R, I;
static uint8_t *SHADOW, *CUR;	/* grey shadow of the driver, current RGBA frame */
static uint8_t TM[A9E_NTILES];	/* accumulated damage since the last prepare */
static long n_updates, n_skipped, n_inc, drv_pixels_checked, drv_violations;
static int bin_for_test = 10;

static void hexs(const uint8_t *d, char *o) { for (int i = 0; i < 32; i++) snprintf(o + 2 * i, 3, "%02x", d[i]); }

/* commit an edit: RGBA rect (portrait) from src frame into CUR, convert to SHADOW, accumulate TM */
static void edit(const uint8_t *srcframe, int c1, int r1, int c2, int r2, int fullclip)
{
	int r;

	if (c1 < 0) c1 = 0;
	if (r1 < 0) r1 = 0;
	if (c2 > A9E_FB_W) c2 = A9E_FB_W;
	if (r2 > A9E_FB_H) r2 = A9E_FB_H;
	if (srcframe)
		for (r = r1; r < r2; r++)
			if (c2 > c1)
				memcpy(CUR + ((size_t)r * A9E_FB_W + c1) * 4, srcframe + ((size_t)r * A9E_FB_W + c1) * 4, (size_t)(c2 - c1) * 4);
	if (fullclip)
		a9e_fb_to_grey_tiles(SHADOW, CUR, A9E_FB_W * 4, A9E_FMT_RGBX, 0, 0, A9E_FB_W, A9E_FB_H, NULL, TM, SCR);
	else
		a9e_fb_to_grey_tiles(SHADOW, CUR, A9E_FB_W * 4, A9E_FMT_RGBX, c1, r1, c2, r2, NULL, TM, SCR);
}

static void noise_rect(int c1, int r1, int c2, int r2)
{
	for (int r = r1; r < r2 && r < A9E_FB_H; r++)
		for (int c = c1; c < c2 && c < A9E_FB_W; c++) {
			uint8_t v = (uint8_t)rnd();
			uint8_t *p = CUR + ((size_t)r * A9E_FB_W + c) * 4;
			p[0] = p[1] = p[2] = v; p[3] = 255;
		}
	edit(NULL, c1, r1, c2, r2, 0);
}

static int steady_code_check(struct a9e_engine *e, const uint8_t *frame)
{
	int bad = 0;

	for (int y = 0; y < A9T_H; y++)
		for (int x = 0; x < A9T_W; x++) {
			size_t i = (size_t)y * A9T_W + x;
			uint16_t pr = e->pairs[i];
			uint8_t code;

			if (pr == 256 || (pr >> 4) != (pr & 15))
				continue;
			code = (frame[(size_t)(y + A9T_HEAD_ROWS) * A9T_TW * 4 + (size_t)(x >> 2) * 4] >> (6 - 2 * (x & 3))) & 3;
			drv_pixels_checked++;
			if (code) bad++;
		}
	return bad;
}

static void cmp_state(const char *what)
{
	CHECK(!memcmp(R.nlv, I.nlv, A9T_N), "%s: nlv differs", what);
	CHECK(!memcmp(R.pairs, I.pairs, (size_t)A9T_N * 2), "%s: pairs differ", what);
	CHECK(!memcmp(&R.rect, &I.rect, sizeof(R.rect)), "%s: rect (%d,%d,%d,%d) vs (%d,%d,%d,%d)", what,
	      R.rect.x1, R.rect.y1, R.rect.x2, R.rect.y2, I.rect.x1, I.rect.y1, I.rect.x2, I.rect.y2);
	CHECK(R.unchanged == I.unchanged, "%s: unchanged %d vs %d", what, R.unchanged, I.unchanged);
	CHECK(R.mask == I.mask, "%s: mask", what);
	CHECK(R.type == I.type && R.mode == I.mode && R.clear == I.clear, "%s: type/mode/clear", what);
	CHECK(R.req.count == I.req.count && R.req.kind == I.req.kind, "%s: request", what);
	CHECK(!memcmp(R.old, I.old, A9T_N), "%s: old differs", what);
}

/* one prepare on both; if not skipped run the whole update on both and compare every frame */
struct upd { uint32_t mode; int clear; int mask; int no_dmg; };
static void step(const char *what, const struct upd *u)
{
	struct a9e_rect full = a9e_rect_full();
	uint8_t savetm[A9E_NTILES];
	int steady, more_r, more_i, nf = 0;
	uint64_t inc0 = I.inc_prepares;
	struct a9t_sha256_ctx cr, ci;
	uint8_t dr[32], di[32];

	memcpy(R.grey, SHADOW, A9T_N);
	memcpy(I.grey, SHADOW, A9T_N);
	memcpy(savetm, TM, sizeof(TM));
	I.dmg = u->no_dmg ? NULL : TM;
	a9e_prepare(&R, u->mode, u->clear, &full, u->mask);
	a9e_prepare(&I, u->mode, u->clear, &full, u->mask);
	CHECK(I.dmg == NULL, "%s: dmg not consumed", what);
	memset(TM, 0, sizeof(TM));
	cmp_state(what);
	n_updates++;
	if (I.inc_prepares != inc0) n_inc++;
	if (R.unchanged) {
		n_skipped++;
		CHECK(I.unchanged, "%s: skip differs", what);
		return;
	}
	steady = !R.clear && !R.req.count && !R.req.kind;
	a9e_start(&R, bin_for_test);
	a9e_start(&I, bin_for_test);
	a9t_sha256_init(&cr); a9t_sha256_init(&ci);
	do {
		memcpy(OUT, TMPL, A9T_FRAME_BYTES);
		more_r = a9e_frame(&R, OUT);
		a9t_sha256_update(&cr, OUT, A9T_FRAME_BYTES);
		memcpy(OUT, TMPL, A9T_FRAME_BYTES);
		more_i = a9e_frame(&I, OUT);
		a9t_sha256_update(&ci, OUT, A9T_FRAME_BYTES);
		if (steady && I.last_inc && (!quick || nf == 0)) {
			int bad = steady_code_check(&I, OUT);

			drv_violations += bad;
			CHECK(!bad, "%s: %d steady pixels driven in frame %d", what, bad, nf);
		}
		nf++;
		CHECK(more_r == more_i, "%s: frame count differs", what);
	} while (more_r && more_i);
	a9t_sha256_final(&cr, dr); a9t_sha256_final(&ci, di);
	{
		char h1[65], h2[65];

		hexs(dr, h1); hexs(di, h2);
		CHECK(!strcmp(h1, h2), "%s: frames differ (%d frames) %s vs %s", what, nf, h1, h2);
	}
	a9e_end(&R);
	a9e_end(&I);
	CHECK(!memcmp(R.old, I.old, A9T_N), "%s: old after end", what);
}

static void reset_pair(void)
{
	a9e_setup(&R); a9e_setup(&I);
	memcpy(R.cache, TMPL, A9T_FRAME_BYTES); memcpy(I.cache, TMPL, A9T_FRAME_BYTES);
	memset(TM, 0, sizeof(TM));
}

static void random_edit(uint8_t **frames, int nf)
{
	int kind = (int)(rnd() % 10), o = (int)(rnd() % nf);
	int c1, r1;

	switch (kind) {
	case 0: /* status bar strip (portrait: top rows) */
		edit(frames[o], 0, 0, A9E_FB_W, 20 + (int)(rnd() % 100), rnd() & 1); break;
	case 1: /* keyboard strip */
		edit(frames[o], 0, 1048 + (int)(rnd() % 200), A9E_FB_W, A9E_FB_H, rnd() & 1); break;
	case 2: /* text line */
		r1 = (int)(rnd() % (A9E_FB_H - 60)); edit(frames[o], 20, r1, A9E_FB_W - 20, r1 + 24 + (int)(rnd() % 40), rnd() & 1); break;
	case 3: /* small tile */
		c1 = (int)(rnd() % 700); r1 = (int)(rnd() % 1500);
		edit(frames[o], c1, r1, c1 + 8 + (int)(rnd() % 120), r1 + 8 + (int)(rnd() % 120), rnd() & 1); break;
	case 4: /* noise in a small rect (chaotic diffusion) */
		c1 = (int)(rnd() % 700); r1 = (int)(rnd() % 1500);
		noise_rect(c1, r1, c1 + 16 + (int)(rnd() % 90), r1 + 16 + (int)(rnd() % 90)); break;
	case 5: /* two distant rects */
		edit(frames[o], 50, 40, 150, 90, 0);
		edit(frames[(o + 1) % nf], 600, 1500, 800, 1640, 0); break;
	case 6: /* identical commit, full clip */
		edit(NULL, 0, 0, A9E_FB_W, A9E_FB_H, 1); break;
	case 7: /* dialog */
		edit(frames[o], 112, 500, 712, 1000, rnd() & 1); break;
	case 8: /* single pixel */
		c1 = (int)(rnd() % A9E_FB_W); r1 = (int)(rnd() % A9E_FB_H);
		noise_rect(c1, r1, c1 + 1, r1 + 1); break;
	default: /* whole frame replaced */
		edit(frames[o], 0, 0, A9E_FB_W, A9E_FB_H, 1);
	}
}

static void test_lockstep(uint8_t **frames, int nf, int steps)
{
	static const uint32_t MODES[] = { 0x201, 0x203, 0x206, 0x209, 0x401, 0x208, 0x202, 0x303 };
	int m, i, k;
	char what[96];

	for (m = 0; m < 8; m++) {
		uint32_t mode = MODES[m];
		struct upd u = { mode, 0, 0, 0 };

		reset_pair();
		memcpy(CUR, frames[0], FBN * 4);
		memset(SHADOW, 0xff, A9T_N);
		edit(NULL, 0, 0, A9E_FB_W, A9E_FB_H, 1);
		snprintf(what, sizeof(what), "mode %#x first", mode);
		step(what, &u);	/* first update: have_pre = 0, request path */
		step(what, &u);
		for (i = 0; i < steps; i++) {
			int ne = 1 + (rnd() % 4 == 0);	/* sometimes coalesce two commits into one prepare */

			for (k = 0; k < ne; k++)
				random_edit(frames, nf);
			snprintf(what, sizeof(what), "mode %#x step %d", mode, i);
			u.mask = (int)(rnd() & 1);
			step(what, &u);
		}
	}

	/* events: every path that must fall back to the reference pass, then resume incremental */
	reset_pair();
	memcpy(CUR, frames[0], FBN * 4);
	memset(SHADOW, 0xff, A9T_N);
	edit(NULL, 0, 0, A9E_FB_W, A9E_FB_H, 1);
	{
		struct upd u = { 0x206, 0, 0, 0 };
		uint8_t *sold = malloc(A9T_N);

		step("ev first", &u); step("ev second", &u);
		random_edit(frames, nf); step("ev a", &u);
		/* 1. lost damage mask: the driver passes NULL (test pattern, dropped job) */
		random_edit(frames, nf); u.no_dmg = 1; step("ev no-dmg", &u); u.no_dmg = 0;
		random_edit(frames, nf); step("ev after no-dmg", &u);
		/* 2. a job whose damage was consumed but never prepared: invalidate */
		random_edit(frames, nf); memset(TM, 0, sizeof(TM)); a9e_inc_invalidate(&I);
		random_edit(frames, nf); step("ev invalidated", &u);
		random_edit(frames, nf); step("ev after invalidated", &u);
		/* 3. dry run: update ends, then the driver restores old and invalidates */
		random_edit(frames, nf);
		memcpy(sold, R.old, A9T_N);
		memcpy(R.grey, SHADOW, A9T_N); memcpy(I.grey, SHADOW, A9T_N);
		{
			struct a9e_rect full = a9e_rect_full();
			uint32_t pm = R.pre_mode;

			I.dmg = TM;
			a9e_prepare(&R, 0x206, 0, &full, 0); a9e_prepare(&I, 0x206, 0, &full, 0);
			memset(TM, 0, sizeof(TM));
			cmp_state("ev dry prepare");
			a9e_start(&R, 10); a9e_start(&I, 10);
			while (a9e_frame(&R, OUT)) ;
			while (a9e_frame(&I, OUT)) ;
			a9e_end(&R); a9e_end(&I);
			memcpy(R.old, sold, A9T_N); memcpy(I.old, sold, A9T_N);
			R.pre_mode = I.pre_mode = pm;
			a9e_inc_invalidate(&I);
		}
		random_edit(frames, nf); step("ev after dry", &u);
		random_edit(frames, nf); step("ev after dry 2", &u);
		/* 4. abort: next update is a forced clear in both, then incremental again */
		random_edit(frames, nf);
		{
			struct a9e_rect full = a9e_rect_full();

			memcpy(R.grey, SHADOW, A9T_N); memcpy(I.grey, SHADOW, A9T_N);
			I.dmg = TM; a9e_prepare(&R, 0x206, 0, &full, 0); a9e_prepare(&I, 0x206, 0, &full, 0);
			memset(TM, 0, sizeof(TM));
			a9e_start(&R, 10); a9e_start(&I, 10);
			a9e_frame(&R, OUT); a9e_frame(&I, OUT);
			a9e_abort(&R); a9e_abort(&I);
		}
		{ struct upd uc = { 0x206, 1, 0, 0 }; random_edit(frames, nf); step("ev forced clear", &uc); }
		random_edit(frames, nf); step("ev after clear", &u);
		random_edit(frames, nf); step("ev after clear 2", &u);
		/* 5. controls change (black/white/contrast LUTs) and clear_shadow toggle */
		a9t_set_controls(&R.st, 3, -2, 5); a9t_set_controls(&I.st, 3, -2, 5);
		random_edit(frames, nf); step("ev controls", &u);
		random_edit(frames, nf); step("ev after controls", &u);
		a9t_set_clear_shadow(&R.st, 1); a9t_set_clear_shadow(&I.st, 1);
		{ struct upd uc = { 0x203, 0, 0, 0 }; random_edit(frames, nf); step("ev shadow mode switch", &uc);
		  random_edit(frames, nf); step("ev shadow steady", &uc);
		  a9t_set_clear_shadow(&R.st, 0); a9t_set_clear_shadow(&I.st, 0);
		  random_edit(frames, nf); step("ev shadow off", &uc);
		  random_edit(frames, nf); step("ev shadow off 2", &uc); }
		/* 6. assume_white (init flash) */
		a9e_assume_white(&R); a9e_assume_white(&I);
		{ struct upd uc = { 0x203, 0, 0, 0 }; random_edit(frames, nf); step("ev assume white", &uc);
		  random_edit(frames, nf); step("ev after assume white", &uc); }
		/* 7. an unprepare (old restored after a prepare) */
		random_edit(frames, nf);
		{
			struct a9e_rect full = a9e_rect_full();
			struct upd uc = { 0x203, 0, 0, 0 };

			memcpy(sold, R.old, A9T_N);
			memcpy(R.grey, SHADOW, A9T_N); memcpy(I.grey, SHADOW, A9T_N);
			I.dmg = TM; a9e_prepare(&R, 0x203, 0, &full, 0); a9e_prepare(&I, 0x203, 0, &full, 0);
			memset(TM, 0, sizeof(TM));
			memcpy(R.old, sold, A9T_N); memcpy(I.old, sold, A9T_N);
			a9e_inc_invalidate(&I);
			random_edit(frames, nf); step("ev after unprepare", &uc);
			random_edit(frames, nf); step("ev after unprepare 2", &uc);
		}
		free(sold);
	}
}

/* mutation: a mask that misses one changed tile must be caught (the test would FAIL) */
static void test_mutation(uint8_t **frames, int nf)
{
	struct upd u = { 0x203, 0, 0, 0 };
	int caught = 0, tries = 0, t;

	for (int rep = 0; rep < 16 && tries < 6; rep++) {
		int before = fails, hit = -1;

		reset_pair();
		memcpy(CUR, frames[0], FBN * 4);
		memset(SHADOW, 0xff, A9T_N);
		edit(NULL, 0, 0, A9E_FB_W, A9E_FB_H, 1);
		step("mut first", &u); step("mut second", &u);
		edit(frames[1 + rep % (nf - 1)], 100, 300, 500, 900, 0);
		{	/* drop the tile with the most changed levels (a one-nibble change could be benign) */
			int best = 0;

			for (t = 0; t < A9E_NTILES; t++) {
				int n = 0;

				if (!TM[t]) continue;
				for (int y = (t / A9E_TX) * 32; y < (t / A9E_TX) * 32 + 32 && y < A9T_H; y++)
					for (int x = (t % A9E_TX) * 32; x < (t % A9E_TX) * 32 + 32 && x < A9T_W; x++)
						n += (SHADOW[(size_t)y * A9T_W + x] >> 4) != R.old[(size_t)y * A9T_W + x];
				if (n > best) { best = n; hit = t; }
			}
		}
		if (hit < 0) { fails = before; continue; }	/* this edit changed no level: nothing to mutate */
		TM[hit] = 0;	/* drop one tile that did change */
		{
			int f0 = fails;

			quiet = 1;
			step("mutated", &u);
			quiet = 0;
			tries++;
			if (fails > f0) caught++;
		}
		fails = before;	/* the failures here are the point: do not count them */
	}
	CHECK(caught == tries && tries > 0, "mutation (one changed tile dropped) caught %d of %d", caught, tries);
	printf("mutation check: a mask missing one changed tile (mode 515) was caught %d of %d times\n", caught, tries);
}

/* ---- bench ------------------------------------------------------------------------------- */
static double med(double *v, int n)
{
	for (int i = 1; i < n; i++) { double x = v[i]; int j = i - 1; while (j >= 0 && v[j] > x) v[j + 1] = v[j], j--; v[j + 1] = x; }
	return v[n / 2];
}

static void bench(uint8_t **frames)
{
	static const struct { const char *n; int c1, r1, c2, r2; } RR[] = {
		{ "full frame", 0, 0, 824, 1648 },
		{ "keyboard strip 824x600", 0, 1048, 824, 1648 },
		{ "dialog 600x500", 112, 500, 712, 1000 },
		{ "status bar 824x100", 0, 0, 824, 100 },
		{ "text line 700x32", 60, 800, 760, 832 },
		{ "tile 200x200", 300, 700, 500, 900 },
		{ "tile 64x64", 300, 700, 364, 764 },
		{ "one pixel", 400, 800, 401, 801 },
	};
	static const uint32_t MODES[] = { 0x206, 0x201, 0x203, 0x209 };
	static const char *MN[] = { "Smooth 518", "Balanced 513", "Clear 515", "Speed 521" };

	printf("bench: prepare() on the host (x86-64, cached memory), median of 15, ms. Edit = paste a region of a\n"
	       "second real frame, prepare+commit, paste the original back, prepare+commit; both prepares timed.\n");
	printf("%-14s %-24s %9s %9s %8s %7s %7s\n", "mode", "damage", "ref ms", "inc ms", "speedup", "tiles", "rows");
	for (int m = 0; m < 4; m++)
		for (size_t k = 0; k < sizeof(RR) / sizeof(RR[0]); k++) {
			double a[15], b[15];
			struct upd u = { MODES[m], 0, 0, 0 };
			uint32_t lt = 0, lr = 0;

			reset_pair();
			memcpy(CUR, frames[0], FBN * 4);
			memset(SHADOW, 0xff, A9T_N);
			edit(NULL, 0, 0, A9E_FB_W, A9E_FB_H, 1);
			step("bench first", &u); step("bench second", &u);
			for (int i = 0; i < 15; i++) {
				struct a9e_rect full = a9e_rect_full();
				double t0;

				edit(frames[1], RR[k].c1, RR[k].r1, RR[k].c2, RR[k].r2, 0);
				memcpy(R.grey, SHADOW, A9T_N); memcpy(I.grey, SHADOW, A9T_N);
				I.dmg = TM;
				t0 = now_ms(); a9e_prepare(&R, MODES[m], 0, &full, 0); a[i] = now_ms() - t0;
				t0 = now_ms(); a9e_prepare(&I, MODES[m], 0, &full, 0); b[i] = now_ms() - t0;
				memset(TM, 0, sizeof(TM));
				lt = I.last_tiles; lr = I.last_rows;
				CHECK(!memcmp(R.nlv, I.nlv, A9T_N) && !memcmp(R.pairs, I.pairs, (size_t)A9T_N * 2), "bench state");
				a9e_start(&R, 10); a9e_start(&I, 10); a9e_end(&R); a9e_end(&I);
				/* revert */
				memcpy(CUR, frames[0], FBN * 4);
				edit(NULL, 0, 0, A9E_FB_W, A9E_FB_H, 1);
				memcpy(R.grey, SHADOW, A9T_N); memcpy(I.grey, SHADOW, A9T_N);
				I.dmg = TM;
				a9e_prepare(&R, MODES[m], 0, &full, 0); a9e_prepare(&I, MODES[m], 0, &full, 0);
				memset(TM, 0, sizeof(TM));
				a9e_start(&R, 10); a9e_start(&I, 10); a9e_end(&R); a9e_end(&I);
				memcpy(CUR, frames[0], FBN * 4);
			}
			{
				double ma = med(a, 15), mb = med(b, 15);

				printf("%-14s %-24s %9.3f %9.3f %7.1fx %7u %7u\n", MN[m], RR[k].n, ma, mb, ma / (mb > 1e-6 ? mb : 1e-6), lt, lr);
			}
		}
}

int main(int argc, char **argv)
{
	size_t blen, tlen;
	uint8_t *blob, *tm, *frames[70];
	int nf = 0, i;
	char p[512];

	if (argc < 4) { fprintf(stderr, "usage: damage-equiv BLOB TEMPLATE FIXTURES [bench]\n"); return 2; }
	blob = slurp(argv[1], &blen);
	if (a9e_wf_check(blob, blen, &T)) { fprintf(stderr, "bad waveform blob\n"); return 1; }
	tm = slurp(argv[2], &tlen);
	TMPL = calloc(1, A9T_FRAME_BYTES);
	if (tlen == A9T_FRAME_BYTES) memcpy(TMPL, tm, tlen);
	else if (tlen > A9T_FRAME_BYTES && !memcmp(tm, "A9CT", 4)) memcpy(TMPL, tm + (tlen - A9T_FRAME_BYTES), A9T_FRAME_BYTES);
	else { fprintf(stderr, "template?\n"); return 2; }
	for (i = 0; i < 64; i++) {
		size_t n;
		FILE *f;

		snprintf(p, sizeof(p), "%s/real%d.rgba", argv[3], i);
		f = fopen(p, "rb");
		if (!f) break;
		fclose(f);
		frames[nf] = slurp(p, &n);
		if (n != FBN * 4) { fprintf(stderr, "%s: %zu bytes\n", p, n); return 2; }
		nf++;
	}
	if (nf < 3) { fprintf(stderr, "need >= 3 real frames\n"); return 2; }
	SCR = malloc(a9e_fb_scratch_bytes());
	OUT = malloc(A9T_FRAME_BYTES);
	SHADOW = malloc(A9T_N);
	CUR = malloc(FBN * 4);
	{	/* a structured synthetic frame joins the real ones (flat areas + hard edges) */
		uint8_t *sf = malloc(FBN * 4);

		for (size_t k = 0; k < FBN; k++) {
			size_t x = k % A9E_FB_W, y = k / A9E_FB_W;
			uint8_t v = (uint8_t)(((x >> 5) + (y >> 5)) & 1 ? 235 : 20);

			sf[k * 4] = sf[k * 4 + 1] = sf[k * 4 + 2] = v; sf[k * 4 + 3] = 255;
		}
		frames[nf++] = sf;
		/* a smooth ramp (dense diffusion, long error cascades) and a textured frame */
		sf = malloc(FBN * 4);
		for (size_t k = 0; k < FBN; k++) {
			size_t x = k % A9E_FB_W, y = k / A9E_FB_W;
			uint8_t v = (uint8_t)((x * 255 / A9E_FB_W + y * 90 / A9E_FB_H) & 255);

			sf[k * 4] = sf[k * 4 + 1] = sf[k * 4 + 2] = v; sf[k * 4 + 3] = 255;
		}
		frames[nf++] = sf;
		sf = malloc(FBN * 4);
		for (size_t k = 0; k < FBN; k++) {
			uint8_t v = (uint8_t)(110 + (rnd() % 60));

			sf[k * 4] = sf[k * 4 + 1] = sf[k * 4 + 2] = v; sf[k * 4 + 3] = 255;
		}
		frames[nf++] = sf;
	}
	alloc_engine(&R, 0);
	alloc_engine(&I, 1);

	if (argc > 4 && !strcmp(argv[4], "quick")) quick = 1;
	if (argc > 4 && !strcmp(argv[4], "bench")) {
		bench(frames);
		return fails ? 1 : 0;
	}
	test_converter(frames, nf);
	printf("converter: %d checks so far, %d failures\n", checks, fails);
	test_lockstep(frames, nf, quick ? 6 : 40);
	{	/* the cap fallback: a tiny cap makes the error cascades of the dense frames hit it */
		I.inc_cap = 1500;
		test_lockstep(frames, nf, quick ? 4 : 14);
		I.inc_cap = 0;
		printf("cap fallback: %llu incremental diffusions finished plainly\n", (unsigned long long)I.inc_capped);
		CHECK(I.inc_capped > 0, "the cap was never reached");
	}
	test_mutation(frames, nf);
	printf("lockstep: %ld prepares (%ld incremental, %ld skipped as unchanged), %lld steady pixels checked for drive, %ld violations\n",
	       n_updates, n_inc, n_skipped, (long long)drv_pixels_checked, drv_violations);
	CHECK(n_inc > (quick ? 50 : 100), "the incremental path was barely used (%ld)", n_inc);
	printf("damage-equiv: %d checks, %d failures; incremental prepares %llu, full %llu, tiles %llu, rows %llu\n", checks, fails,
	       (unsigned long long)I.inc_prepares, (unsigned long long)I.full_prepares,
	       (unsigned long long)I.inc_tiles, (unsigned long long)I.inc_rows);
	return fails ? 1 : 0;
}
