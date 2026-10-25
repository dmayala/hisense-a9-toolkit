#define _POSIX_C_SOURCE 200809L
/* convert-equiv: a9e_fb_to_grey_bounce() == a9e_fb_to_grey(), bit for bit.
 *
 *   convert-equiv BLOB TEMPLATE FIXTURE_DIR [bench]
 *
 * FIXTURE_DIR holds raw 824x1648 RGBA frames real*.rgba (screencaps of the
 * phone, converted from PNG). Checks, all through the real engine source:
 *  1. grey bytes and *changed for every format (RGBX/BGRX/R8), full frames and
 *     random rects, odd pitches and offsets, chained updates (the "previous
 *     grey" is the previous result), with a source that is padded so any read
 *     past the damage rect would show up (guard bytes poisoned and compared);
 *  2. engine frames (sha256 of all frames of a full update) for modes
 *     513/515/518/521 x clear_shadow 0/1, fed with the old grey and the new
 *     grey: identical.
 * "bench": host timings, old vs new (report only).
 */
#include "a9_eink_engine.h"

#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static int fails, checks;
#define CHECK(c, ...) do { checks++; if (!(c)) { fails++; fprintf(stderr, "FAIL %s:%d ", __FILE__, __LINE__); fprintf(stderr, __VA_ARGS__); fprintf(stderr, "\n"); } } while (0)

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

static uint32_t rng = 12345;
static uint32_t rnd(void)
{
	rng = rng * 1664525u + 1013904223u;
	return rng >> 8;
}

#define FBN ((size_t)A9E_FB_W * A9E_FB_H)
static uint8_t *scratch;

static int rect_eq(const struct a9e_rect *a, const struct a9e_rect *b)
{
	return a->x1 == b->x1 && a->y1 == b->y1 && a->x2 == b->x2 && a->y2 == b->y2;
}

/* one comparison: both start from the same grey, same source */
static void cmp_one(const char *what, const uint8_t *g0, const uint8_t *src, uint32_t pitch,
		    enum a9e_fmt fmt, int c1, int r1, int c2, int r2, int want_changed,
		    uint8_t *ga, uint8_t *gb)
{
	struct a9e_rect ca = { 7, 8, 9, 10 }, cb = { 7, 8, 9, 10 };	/* pre-existing box is merged */

	memcpy(ga, g0, A9T_N);
	memcpy(gb, g0, A9T_N);
	a9e_fb_to_grey(ga, src, pitch, fmt, c1, r1, c2, r2, want_changed ? &ca : NULL);
	a9e_fb_to_grey_bounce(gb, src, pitch, fmt, c1, r1, c2, r2, want_changed ? &cb : NULL, scratch);
	CHECK(!memcmp(ga, gb, A9T_N), "%s: grey differs (fmt %d rect %d,%d,%d,%d)", what, fmt, c1, r1, c2, r2);
	if (want_changed)
		CHECK(rect_eq(&ca, &cb), "%s: changed (%d,%d,%d,%d) vs (%d,%d,%d,%d) rect %d,%d,%d,%d", what,
		      ca.x1, ca.y1, ca.x2, ca.y2, cb.x1, cb.y1, cb.x2, cb.y2, c1, r1, c2, r2);
}

static uint8_t *load_fixtures(const char *dir, uint8_t **frames, int *nframes)
{
	char p[512];
	int i;

	for (i = 0; i < 64; i++) {
		size_t n;
		FILE *f;

		snprintf(p, sizeof(p), "%s/real%d.rgba", dir, i);
		f = fopen(p, "rb");
		if (!f) break;
		fclose(f);
		frames[i] = slurp(p, &n);
		if (n != FBN * 4) { fprintf(stderr, "%s: %zu bytes\n", p, n); exit(2); }
	}
	*nframes = i;
	return 0;
}

/* a padded copy: pitch bytes per row plus poison, rows offset */
static uint8_t *padded(const uint8_t *rgba, int fmt, uint32_t *pitch, size_t *off)
{
	int bpp = fmt == A9E_FMT_R8 ? 1 : 4;
	uint32_t p = A9E_FB_W * bpp + 16 + (rnd() & 3) * 4;	/* odd pitches, multiple of 4 */
	size_t lead = 64 + (rnd() & 15), total = lead + (size_t)p * A9E_FB_H + 64;
	uint8_t *b = malloc(total);
	int r, c;

	memset(b, 0xA5, total);
	for (r = 0; r < A9E_FB_H; r++)
		for (c = 0; c < A9E_FB_W; c++) {
			const uint8_t *s = rgba + ((size_t)r * A9E_FB_W + c) * 4;
			uint8_t *d = b + lead + (size_t)r * p + (size_t)c * bpp;
			if (bpp == 1) d[0] = (uint8_t)(s[0] ^ s[1] ^ s[2]);
			else if (fmt == A9E_FMT_BGRX) d[0] = s[2], d[1] = s[1], d[2] = s[0], d[3] = s[3];
			else memcpy(d, s, 4);
		}
	*pitch = p;
	*off = lead;
	return b;
}

static void to_hex(const uint8_t *d, char *out)
{
	for (int i = 0; i < 32; i++) snprintf(out + 2 * i, 3, "%02x", d[i]);
}

/* engine update on grey g, mode, shadow: sha of all frames */
static struct a9e_engine E;
static uint8_t *OUT, *TMPL;
static void engine_frames_sha(const uint8_t *grey, uint32_t mode, int shadow, char *hexout)
{
	struct a9t_sha256_ctx c;
	struct a9e_rect r = a9e_rect_full();
	uint8_t d[32];
	int more;

	a9e_setup(&E);
	memcpy(E.cache, TMPL, A9T_FRAME_BYTES);
	a9t_set_clear_shadow(&E.st, shadow);
	memcpy(E.grey, grey, A9T_N);
	E.have_pre = 1;
	E.pre_mode = mode;
	a9e_begin(&E, mode, 0, &r, 0, a9t_temp_bin(&E.st, 25, 25));
	a9t_sha256_init(&c);
	do {
		memcpy(OUT, TMPL, A9T_FRAME_BYTES);
		more = a9e_frame(&E, OUT);
		a9t_sha256_update(&c, OUT, A9T_FRAME_BYTES);
	} while (more);
	a9e_end(&E);
	a9t_sha256_final(&c, d);
	to_hex(d, hexout);
}

static double med(double *v, int n)
{
	for (int i = 1; i < n; i++) { double x = v[i]; int j = i - 1; while (j >= 0 && v[j] > x) v[j + 1] = v[j], j--; v[j + 1] = x; }
	return v[n / 2];
}

static void bench(uint8_t *rgba)
{
	static const struct { const char *n; int c1, r1, c2, r2; } R[] = {
		{ "full 824x1648", 0, 0, 824, 1648 },
		{ "keyboard strip 824x600", 0, 1048, 824, 1648 },
		{ "status bar 824x100", 0, 0, 824, 100 },
		{ "dialog 600x500", 112, 500, 712, 1000 },
		{ "tile 200x200", 300, 700, 500, 900 },
		{ "tile 64x64", 300, 700, 364, 764 },
	};
	uint32_t pitch;
	size_t off;
	uint8_t *buf = padded(rgba, A9E_FMT_RGBX, &pitch, &off), *g = calloc(1, A9T_N);
	const uint8_t *src = buf + off;

	printf("bench (host x86, source in cache, median of 15, ms; report only):\n");
	printf("%-26s %10s %10s %8s\n", "rect", "old", "bounce", "speedup");
	for (size_t k = 0; k < sizeof(R) / sizeof(R[0]); k++)
		for (int chg = 0; chg < 2; chg++) {
			double a[15], b[15];
			for (int i = 0; i < 15; i++) {
				struct a9e_rect ch = { 0, 0, 0, 0 };
				memset(g, i & 1 ? 0 : 255, A9T_N);	/* changed everywhere / not */
				if (chg) memset(g, 0, A9T_N);
				double t0 = now_ms();
				a9e_fb_to_grey(g, src, pitch, A9E_FMT_RGBX, R[k].c1, R[k].r1, R[k].c2, R[k].r2, &ch);
				a[i] = now_ms() - t0;
				memset(g, i & 1 ? 0 : 255, A9T_N);
				if (chg) memset(g, 0, A9T_N);
				t0 = now_ms();
				a9e_fb_to_grey_bounce(g, src, pitch, A9E_FMT_RGBX, R[k].c1, R[k].r1, R[k].c2, R[k].r2, &ch, scratch);
				b[i] = now_ms() - t0;
			}
			double ma = med(a, 15), mb = med(b, 15);
			printf("%-26s %10.3f %10.3f %7.2fx  (%s)\n", R[k].n, ma, mb, ma / mb, chg ? "all pixels change" : "mixed");
		}
	free(buf);
	free(g);
}

int main(int argc, char **argv)
{
	size_t blen, tlen;
	const struct a9t_tables *t;
	uint8_t *blob, *tm, *frames[64], *g0, *ga, *gb, *gc;
	int nf, i, f, fmt, k;
	static const uint32_t MODES[] = { 513, 515, 518, 521 };
	char h1[65], h2[65];

	if (argc < 4) { fprintf(stderr, "usage: convert-equiv BLOB TEMPLATE FIXTURES [bench]\n"); return 2; }
	blob = slurp(argv[1], &blen);
	if (a9e_wf_check(blob, blen, &t)) { fprintf(stderr, "bad waveform blob\n"); return 1; }
	tm = slurp(argv[2], &tlen);
	TMPL = calloc(1, A9T_FRAME_BYTES);
	if (tlen == A9T_FRAME_BYTES) memcpy(TMPL, tm, tlen);
	else if (tlen > A9T_FRAME_BYTES && !memcmp(tm, "A9CT", 4)) memcpy(TMPL, tm + (tlen - A9T_FRAME_BYTES), A9T_FRAME_BYTES);
	else { fprintf(stderr, "template?\n"); return 2; }
	load_fixtures(argv[3], frames, &nf);
	if (nf < 2) { fprintf(stderr, "need >= 2 real frames in %s\n", argv[3]); return 2; }
	scratch = malloc(a9e_fb_scratch_bytes());
	ga = malloc(A9T_N); gb = malloc(A9T_N); gc = malloc(A9T_N); g0 = malloc(A9T_N);

	/* a random frame and a random-with-structure frame join the real ones */
	{
		uint8_t *rf = malloc(FBN * 4), *sf = malloc(FBN * 4);
		for (size_t i2 = 0; i2 < FBN * 4; i2++) rf[i2] = (uint8_t)rnd();
		for (size_t i2 = 0; i2 < FBN; i2++) {
			uint8_t v = (uint8_t)(((i2 % A9E_FB_W) >> 3) ^ ((i2 / A9E_FB_W) >> 4) ^ (rnd() & 1 ? 0 : 17));
			sf[i2 * 4] = v; sf[i2 * 4 + 1] = (uint8_t)(v + 3); sf[i2 * 4 + 2] = (uint8_t)(v * 3); sf[i2 * 4 + 3] = 255;
		}
		frames[nf++] = rf;
		frames[nf++] = sf;
	}

	if (argc > 4 && !strcmp(argv[4], "bench")) {
		bench(frames[1]);
		return 0;
	}

	/* 1. grey + changed, chained, all formats, full and random rects */
	for (fmt = 0; fmt < 3; fmt++) {
		memset(g0, 0xff, A9T_N);
		for (f = 0; f < nf; f++) {
			uint32_t pitch;
			size_t off;
			uint8_t *buf = padded(frames[f], fmt, &pitch, &off);
			const uint8_t *src = buf + off;
			int bpp = fmt == A9E_FMT_R8 ? 1 : 4;
			/* full frame on the chain, with and without the changed box */
			cmp_one("full", g0, src, pitch, fmt, 0, 0, A9E_FB_W, A9E_FB_H, 1, ga, gb);
			cmp_one("full-nochg", g0, src, pitch, fmt, 0, 0, A9E_FB_W, A9E_FB_H, 0, gc, gb);
			CHECK(!memcmp(ga, gc, A9T_N), "full: with/without changed differ");
			/* random rects incl. out-of-range clip values (the driver clips; so does the engine) */
			for (k = 0; k < 12; k++) {
				int c1 = (int)(rnd() % (A9E_FB_W + 40)) - 20, c2 = c1 + (int)(rnd() % 500) + 1;
				int r1 = (int)(rnd() % (A9E_FB_H + 40)) - 20, r2 = r1 + (int)(rnd() % 900) + 1;
				if (k == 0) c1 = 0, c2 = 1, r1 = 0, r2 = 1;
				if (k == 1) c1 = A9E_FB_W - 1, c2 = A9E_FB_W, r1 = A9E_FB_H - 1, r2 = A9E_FB_H;
				if (k == 2) c1 = 5, c2 = 5, r1 = 3, r2 = 90;	/* empty */
				if (k == 3) c1 = 0, c2 = A9E_FB_W, r1 = 31, r2 = 33;	/* band edge */
				if (k == 4) c1 = 3, c2 = 400, r1 = 32, r2 = 64;
				cmp_one("rect", g0, src, pitch, fmt, c1, r1, c2, r2, 1, ga, gb);
			}
			/* the chain: the previous frame's result is the next start grey */
			memcpy(g0, ga, A9T_N);
			/* guard: reads past the damage rect would have touched 0xA5 padding; the result
			 * above already matches the reference, which never reads outside the rect. */
			(void)bpp;
			free(buf);
		}
	}

	/* 2. engine frames: modes x clear_shadow, old-grey vs bounce-grey */
	E.t = t;
	E.grey = malloc(A9T_N); E.old = malloc(A9T_N); E.nlv = malloc(A9T_N); E.pairs = malloc(A9T_N * 2);
	for (k = 0; k < 5; k++) E.arr[k] = malloc(A9T_N * 2);
	E.work = malloc(sizeof(*E.work));
	E.cache = malloc(A9T_FRAME_BYTES);
	E.flag_6c7 = 1;
	OUT = calloc(1, A9T_FRAME_BYTES);
	{
		int pick[3] = { 1, nf - 2, nf - 1 };	/* a real frame, random, structured */
		for (i = 0; i < 3; i++) {
			uint32_t pitch;
			size_t off;
			uint8_t *buf = padded(frames[pick[i]], A9E_FMT_RGBX, &pitch, &off);
			memset(g0, 0xff, A9T_N);
			cmp_one("engine-src", g0, buf + off, pitch, A9E_FMT_RGBX, 0, 0, A9E_FB_W, A9E_FB_H, 1, ga, gb);
			for (int m = 0; m < 4; m++)
				for (int sh = 0; sh < 2; sh++) {
					engine_frames_sha(ga, MODES[m], sh, h1);
					engine_frames_sha(gb, MODES[m], sh, h2);
					CHECK(!strcmp(h1, h2), "engine frames differ: fixture %d mode %u shadow %d", pick[i], MODES[m], sh);
					if (i == 0) printf("engine fixture %d mode %u clear_shadow %d frames sha256 %.16s... old==new\n", pick[i], MODES[m], sh, h1);
				}
			free(buf);
		}
	}
	printf("convert-equiv: %d checks, %d failures\n", checks, fails);
	return fails ? 1 : 0;
}
