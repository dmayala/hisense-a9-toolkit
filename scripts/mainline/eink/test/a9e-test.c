#define _POSIX_C_SOURCE 200809L
/* a9e-test: userspace harness for the a9_eink engine (the exact .c file the
 * kernel module builds), against the a9tcon CLI and against invariants.
 *
 *   a9e-test BLOB update [--grey F] [--old F] [--mode M] [--pre P] [--clear C]
 *            [--t1 N] [--t2 N] [--rect x1,y1,x2,y2] [--mask] [--flag6c7 0|1]
 *            [--levels OUT] [--time] [--template T]
 *       prints "frames N sha256 X" exactly like `a9tcon ... ` does.
 *       --pre P makes the engine believe the previous update ran mode P.
 *   a9e-test BLOB init --t1 N --t2 N [--template T]   the init flash
 *   a9e-test BLOB selftest [--template T]    grey/rect/mask/null-frame invariants
 *   a9e-test BLOB apitest --template T       a9e_ct_check, split API == a9e_begin
 *   a9e-test BLOB bench --template T         host timings (report only)
 *
 * --template T: the control template, hisense/a9-ctrl-template.bin (A9CT
 * header, checked with a9e_ct_check + its payload sha256) or a raw
 * A9T_FRAME_BYTES template. Every output buffer and the hold cache start as
 * the template, as on the phone, so frames are whole 32-bit words (and their
 * sha256 equals `a9tcon --template T`). Then EVERY frame is also checked:
 * bytes 1..3 == the template byte for byte, and a null frame (a9e_frame_is_null)
 * == the template exactly. Without --template, bytes 1..3 are zero.
 */
#include "a9_eink_engine.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static uint8_t *slurp(const char *path, size_t *len)
{
	FILE *f = fopen(path, "rb");
	if (!f) {
		perror(path);
		exit(2);
	}
	fseek(f, 0, SEEK_END);
	long n = ftell(f);
	fseek(f, 0, SEEK_SET);
	uint8_t *b = malloc((size_t)n ? (size_t)n : 1);
	if (!b || fread(b, 1, (size_t)n, f) != (size_t)n)
		exit(2);
	fclose(f);
	*len = (size_t)n;
	return b;
}

static void read_exact(const char *path, uint8_t *dst, size_t want)
{
	size_t n;
	uint8_t *b = slurp(path, &n);
	if (n != want) {
		fprintf(stderr, "%s: %zu bytes, expected %zu\n", path, n, want);
		exit(2);
	}
	memcpy(dst, b, want);
	free(b);
}

static double now_ms(void)
{
	struct timespec ts;
	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec * 1e3 + ts.tv_nsec / 1e6;
}

static struct a9e_engine E;
static int NOHASH;
static uint8_t *OUT;
static uint8_t *TMPL;		/* control template (or zeros without --template) */
static int HAVE_TMPL;
static long CTL_FRAMES, CTL_BAD, NULL_FRAMES, NULL_BAD;

static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; fprintf(stderr, "FAIL %s:%d ", __FILE__, __LINE__); fprintf(stderr, __VA_ARGS__); fprintf(stderr, "\n"); } } while (0)

/* a9e_setup + the caller's part: the hold cache starts as the template */
static void setup(void)
{
	a9e_setup(&E);
	memcpy(E.cache, TMPL, A9T_FRAME_BYTES);
}

/* bytes 1..3 of out == template; a null frame == template exactly */
static void ctl_check(const uint8_t *out)
{
	if (!HAVE_TMPL)
		return;
	CTL_FRAMES++;
	for (size_t i = 0; i < A9T_FRAME_BYTES; i += 4)
		if (out[i + 1] != TMPL[i + 1] || out[i + 2] != TMPL[i + 2] || out[i + 3] != TMPL[i + 3]) {
			if (!CTL_BAD)
				fprintf(stderr, "FAIL frame bytes 1..3 differ from the template at word %zu\n", i / 4);
			CTL_BAD++;
			break;
		}
	if (a9e_frame_is_null(out)) {
		NULL_FRAMES++;
		if (memcmp(out, TMPL, A9T_FRAME_BYTES)) {
			if (!NULL_BAD)
				fprintf(stderr, "FAIL null frame != template\n");
			NULL_BAD++;
		}
	}
}

static void load_template(const char *path)
{
	size_t n;
	uint8_t *b = slurp(path, &n), d[32];
	const uint8_t *p = 0;
	char hx[65];
	if (n >= 4 && b[0] == 'A' && b[1] == '9' && b[2] == 'C' && b[3] == 'T') {
		int rc = a9e_ct_check(b, n, &p);
		if (rc) {
			fprintf(stderr, "%s: a9e_ct_check %d\n", path, rc);
			exit(1);
		}
		a9t_sha256(p, A9T_FRAME_BYTES, d);
		if (memcmp(d, ((const struct a9e_ct_header *)b)->payload_sha256, 32)) {
			fprintf(stderr, "%s: payload sha256 != header\n", path);
			exit(1);
		}
	} else if (n == A9T_FRAME_BYTES) {	/* raw: same byte checks through a header */
		uint8_t *h = calloc(1, sizeof(struct a9e_ct_header) + A9T_FRAME_BYTES);
		struct a9e_ct_header *hd = (struct a9e_ct_header *)h;
		hd->magic = A9E_CT_MAGIC, hd->version = A9E_CT_VERSION;
		hd->header_size = sizeof(*hd), hd->payload_size = A9T_FRAME_BYTES;
		memcpy(h + sizeof(*hd), b, A9T_FRAME_BYTES);
		int rc = a9e_ct_check(h, sizeof(*hd) + A9T_FRAME_BYTES, 0);
		free(h);
		if (rc) {
			fprintf(stderr, "%s: raw template fails a9e_ct_check %d\n", path, rc);
			exit(1);
		}
		p = b;
		a9t_sha256(p, A9T_FRAME_BYTES, d);
	} else {
		fprintf(stderr, "%s: not a control template\n", path);
		exit(1);
	}
	memcpy(TMPL, p, A9T_FRAME_BYTES);
	HAVE_TMPL = 1;
	for (int i = 0; i < 32; i++)
		snprintf(hx + 2 * i, 3, "%02x", d[i]);
	if (strcmp(hx, A9E_CT_SHA256))
		fprintf(stderr, "warning: template sha256 %s is not A9E_CT_SHA256\n", hx);
	free(b);
}

static void engine_alloc(const struct a9t_tables *t)
{
	E.t = t;
	E.grey = malloc(A9T_N);
	E.old = malloc(A9T_N);
	E.nlv = malloc(A9T_N);
	E.pairs = malloc(A9T_N * 2);
	for (int k = 0; k < 5; k++)
		E.arr[k] = malloc(A9T_N * 2);
	E.work = malloc(sizeof(*E.work));
	E.cache = malloc(A9T_FRAME_BYTES);
	E.flag_6c7 = 1;
	OUT = calloc(1, A9T_FRAME_BYTES);
	setup();
}

/* run the current update to the end; sha256 over all frames */
static int run_frames(uint8_t d[32], double *ms_frames, int check_null_last)
{
	struct a9t_sha256_ctx c;
	int more, n = 0;
	double t0 = now_ms();
	a9t_sha256_init(&c);
	do {
		memcpy(OUT, TMPL, A9T_FRAME_BYTES);	/* a fresh buffer: the template */
		more = a9e_frame(&E, OUT);
		if (!NOHASH) {
			a9t_sha256_update(&c, OUT, A9T_FRAME_BYTES);
			ctl_check(OUT);
		}
		n++;
		if (!more && check_null_last && !a9e_frame_is_null(OUT)) {
			fprintf(stderr, "FAIL: last frame not null\n");
			exit(1);
		}
	} while (more);
	if (ms_frames)
		*ms_frames = now_ms() - t0;
	a9t_sha256_final(&c, d);
	return n;
}

static void hex(const uint8_t *d, int n)
{
	for (int i = 0; i < n; i++)
		printf("%02x", d[i]);
}

static uint32_t rng = 7;
static uint32_t rnd(void)
{
	rng = rng * 1103515245u + 12345u;
	return rng >> 8;
}

static void selftest(void)
{
	uint8_t *rgba = malloc(A9T_SRC_BYTES), *bgra = malloc(A9T_SRC_BYTES), *r8 = malloc(A9T_SRC_STRIDE * A9T_SRC_ROWS);
	uint8_t *g1 = malloc(A9T_N), *g2 = malloc(A9T_N);
	for (size_t i = 0; i < A9T_SRC_BYTES; i++)
		rgba[i] = (uint8_t)rnd();
	for (size_t i = 0; i < A9T_SRC_BYTES; i += 4)
		bgra[i] = rgba[i + 2], bgra[i + 1] = rgba[i + 1], bgra[i + 2] = rgba[i], bgra[i + 3] = rgba[i + 3];
	/* 1: full RGBX conversion == the core's (verified vs stock) rotation */
	a9t_rgba_to_grey(rgba, g1);
	memset(g2, 0xaa, A9T_N);
	a9e_fb_to_grey(g2, rgba, A9T_SRC_STRIDE * 4, A9E_FMT_RGBX, 0, 0, A9E_FB_W, A9E_FB_H, NULL);
	CHECK(!memcmp(g1, g2, A9T_N), "RGBX full != a9t_rgba_to_grey");
	memset(g2, 0x55, A9T_N);
	a9e_fb_to_grey(g2, bgra, A9T_SRC_STRIDE * 4, A9E_FMT_BGRX, 0, 0, A9E_FB_W, A9E_FB_H, NULL);
	CHECK(!memcmp(g1, g2, A9T_N), "BGRX full != RGBX of swapped bytes");
	/* 2: a rect converts exactly the mapped landscape rect, nothing else */
	for (int it = 0; it < 50; it++) {
		int c1 = (int)(rnd() % A9E_FB_W), c2 = c1 + 1 + (int)(rnd() % (A9E_FB_W - c1));
		int r1 = (int)(rnd() % A9E_FB_H), r2 = r1 + 1 + (int)(rnd() % (A9E_FB_H - r1));
		memset(g2, 0x5a, A9T_N);
		a9e_fb_to_grey(g2, rgba, A9T_SRC_STRIDE * 4, A9E_FMT_RGBX, c1, r1, c2, r2, NULL);
		struct a9e_rect rc = a9e_rect_from_fb(c1, r1, c2, r2);
		int bad = 0;
		for (int y = 0; y < A9T_H; y++)
			for (int x = 0; x < A9T_W; x++) {
				int in = x >= rc.x1 && x < rc.x2 && y >= rc.y1 && y < rc.y2;
				uint8_t v = g2[y * A9T_W + x];
				if (in ? v != g1[y * A9T_W + x] : v != 0x5a)
					bad++;
			}
		CHECK(!bad, "rect %d,%d-%d,%d: %d bad pixels", c1, r1, c2, r2, bad);
	}
	/* 3: R8 with an odd pitch */
	for (int r = 0; r < A9E_FB_H; r++)
		for (int c = 0; c < A9E_FB_W; c++)
			r8[(size_t)r * 830 + c] = (uint8_t)(r * 7 + c * 3);
	a9e_fb_to_grey(g2, r8, 830, A9E_FMT_R8, 0, 0, A9E_FB_W, A9E_FB_H, NULL);
	int bad = 0;
	for (int y = 0; y < A9T_H; y++)
		for (int x = 0; x < A9T_W; x++)
			if (g2[y * A9T_W + x] != (uint8_t)(x * 7 + (823 - y) * 3))
				bad++;
	CHECK(!bad, "R8 rotation: %d bad", bad);

	/* 3b: changed-pixel bbox */
	for (int it = 0; it < 30; it++) {
		a9e_fb_to_grey(g2, rgba, A9T_SRC_STRIDE * 4, A9E_FMT_RGBX, 0, 0, A9E_FB_W, A9E_FB_H, NULL);
		struct a9e_rect ch = { 0, 0, 0, 0 };
		a9e_fb_to_grey(g2, rgba, A9T_SRC_STRIDE * 4, A9E_FMT_RGBX, 0, 0, A9E_FB_W, A9E_FB_H, &ch);
		CHECK(a9e_rect_empty(&ch), "identical redraw reports damage");
		int c = (int)(rnd() % A9E_FB_W), r0 = (int)(rnd() % A9E_FB_H);
		size_t off = (size_t)r0 * A9T_SRC_STRIDE * 4 + (size_t)c * 4;
		uint8_t sv = rgba[off];
		rgba[off] ^= 0xff, rgba[off + 1] ^= 0xff;
		a9e_fb_to_grey(g2, rgba, A9T_SRC_STRIDE * 4, A9E_FMT_RGBX, 0, 0, A9E_FB_W, A9E_FB_H, &ch);
		struct a9e_rect want = a9e_rect_from_fb(c, r0, c + 1, r0 + 1);
		CHECK(ch.x1 == want.x1 && ch.x2 == want.x2 && ch.y1 == want.y1 && ch.y2 == want.y2,
		      "bbox %d,%d-%d,%d want %d,%d-%d,%d", ch.x1, ch.y1, ch.x2, ch.y2, want.x1, want.y1, want.x2, want.y2);
		rgba[off] = sv, rgba[off + 1] ^= 0xff;
	}
	/* 4: mask the derived conditioned-target footprint, not raw source damage. */
	uint8_t *grey = malloc(A9T_N), *old = malloc(A9T_N);
	for (size_t i = 0; i < A9T_N; i++)
		grey[i] = (uint8_t)rnd(), old[i] = (uint8_t)(rnd() & 15);
	uint32_t modes[] = { 0x201, 0x203, 0x206, 0x209 };
	for (int m = 0; m < 4; m++) {
		struct a9e_rect rc = { 101, 37, 777, 500 };
		uint8_t *ref = malloc(A9T_FRAME_BYTES);
		/* unmasked partial */
		setup();
		memcpy(E.old, old, A9T_N);
		memcpy(E.grey, grey, A9T_N);
		E.have_pre = 1, E.pre_mode = modes[m];
		a9e_begin(&E, modes[m], 0, &rc, 0, 10);
		struct a9e_rect target = E.rect;
		/* Outside the derived target/sentinel footprint, pairs must be k->k. */
		int bp = 0;
		for (int y = 0; y < A9T_H; y++)
			for (int x = 0; x < A9T_W; x++)
				if (!(x >= target.x1 && x < target.x2 && y >= target.y1 && y < target.y2)) {
					uint16_t p = E.pairs[y * A9T_W + x];
					if (p != 256 && (p >> 4) != (p & 15))
						bp++;
				}
		CHECK(!bp, "mode %#x: %d outside pairs change level", modes[m], bp);
		int nf = 0, more;
		uint8_t **frames = malloc(sizeof(uint8_t *) * 300);
		do {
			frames[nf] = malloc(A9T_FRAME_BYTES);
			memcpy(frames[nf], TMPL, A9T_FRAME_BYTES);
			more = a9e_frame(&E, frames[nf]);
			ctl_check(frames[nf]);
			nf++;
		} while (more && nf < 300);
		/* masked */
		setup();
		memcpy(E.old, old, A9T_N);
		memcpy(E.grey, grey, A9T_N);
		E.have_pre = 1, E.pre_mode = modes[m];
		a9e_begin(&E, modes[m], 0, &rc, 1, 10);
		int k = 0, bo = 0, bi = 0;
		do {
			memcpy(ref, TMPL, A9T_FRAME_BYTES);
			more = a9e_frame(&E, ref);
			ctl_check(ref);
			for (int y = 0; y < A9T_H; y++)
				for (int x = 0; x < A9T_W; x++) {
					size_t off = ((size_t)(y + 5) * A9T_TW + x / 4) * 4;
					int sh = 6 - 2 * (x % 4);
					int a = (ref[off] >> sh) & 3, b = (frames[k][off] >> sh) & 3;
					int in = x >= target.x1 && x < target.x2 && y >= target.y1 && y < target.y2;
					if (in && a != b) bi++;
					if (!in && a) bo++;
				}
			k++;
		} while (more);
		CHECK(k == nf, "mode %#x: masked %d frames vs %d", modes[m], k, nf);
		CHECK(!bo && !bi, "mode %#x: mask outside %d inside %d", modes[m], bo, bi);
		for (int i = 0; i < nf; i++)
			free(frames[i]);
		free(frames);
		free(ref);
		printf("mask %#x: %d frames ok\n", modes[m], nf);
	}
	printf("selftest: %d failures\n", fails);
}

/* ---- apitest: a9e_ct_check and the split update API -------------------------- */
struct outcome {
	int n_init, n_upd, n_next;
	uint8_t d_init[32], d_upd[32], d_next[32], d_old[32];
	uint32_t pre_mode, mode, type;
	int clear, steps;
};

static void sha_levels(uint8_t d[32])
{
	a9t_sha256(E.old, A9T_N, d);
}

/* one scenario: [init flash] + update (mode, clear, rect, mask) at bin, then a
 * steady follow-up update (0x203) that runs on the state the first one left.
 * split = 0: begin_init/frames/a9e_end then a9e_begin (the old driver order);
 * split = 1: [a9e_assume_white], a9e_prepare, [begin_init/frames/a9e_init_end],
 *            a9e_start (the driver order of patch 0029). */
static void scenario(struct outcome *o, int split, int init, uint32_t pre, uint32_t mode, int clear,
		     const struct a9e_rect *rc, int mask, int bin, const uint8_t *grey, const uint8_t *old,
		     const uint8_t *grey2)
{
	struct a9e_rect full = a9e_rect_full();

	setup();
	memcpy(E.grey, grey, A9T_N);
	memcpy(E.old, old, A9T_N);
	E.have_pre = 1;
	E.pre_mode = pre;
	memset(o, 0, sizeof(*o));
	if (!split) {
		if (init) {
			a9e_begin_init(&E, bin);
			o->n_init = run_frames(o->d_init, 0, 1);
			a9e_end(&E);
		}
		o->steps = a9e_begin(&E, mode, clear, rc, mask, bin);
	} else {
		if (init)
			a9e_assume_white(&E);
		o->steps = a9e_prepare(&E, mode, clear, rc, mask);
		if (init) {
			a9e_begin_init(&E, bin);
			o->n_init = run_frames(o->d_init, 0, 1);
			a9e_init_end(&E);
		}
		a9e_start(&E, bin);
	}
	o->mode = E.mode, o->type = E.type, o->clear = E.clear;
	o->n_upd = run_frames(o->d_upd, 0, 1);
	a9e_end(&E);
	o->pre_mode = E.pre_mode;
	sha_levels(o->d_old);
	memcpy(E.grey, grey2, A9T_N);
	if (!split) {
		a9e_begin(&E, 0x203, 0, &full, 0, bin);
	} else {
		a9e_prepare(&E, 0x203, 0, &full, 0);
		a9e_start(&E, bin);
	}
	o->n_next = run_frames(o->d_next, 0, 1);
	a9e_end(&E);
}

static void apitest(void)
{
	/* 1. a9e_ct_check: the real file passes, each corruption is refused */
	size_t L = sizeof(struct a9e_ct_header) + A9T_FRAME_BYTES;
	uint8_t *b = malloc(L);
	struct a9e_ct_header *h = (struct a9e_ct_header *)b;
	const uint8_t *pl = 0;
	memset(b, 0, sizeof(*h));
	h->magic = A9E_CT_MAGIC, h->version = A9E_CT_VERSION;
	h->header_size = sizeof(*h), h->payload_size = A9T_FRAME_BYTES;
	memcpy(b + sizeof(*h), TMPL, A9T_FRAME_BYTES);
	CHECK(a9e_ct_check(b, L, &pl) == 0 && pl == b + sizeof(*h), "valid template refused");
	struct { size_t off; uint8_t x; int want; const char *what; } bad[] = {
		{ 0, 0x01, A9E_CT_EMAGIC, "magic" },
		{ 4, 0x02, A9E_CT_EVERSION, "version" },
		{ 8, 0x01, A9E_CT_ESIZE, "header_size" },
		{ 12, 0x04, A9E_CT_ESIZE, "payload_size" },
		{ 80 + 0, 0x01, A9E_CT_EBYTE0, "byte 0 of word 0" },
		{ 80 + 4 * 300000 + 0, 0x40, A9E_CT_EBYTE0, "byte 0 of word 300000" },
		{ 80 + A9T_FRAME_BYTES - 4, 0x80, A9E_CT_EBYTE0, "byte 0 of the last word" },
		{ 80 + 4 * 1234 + 2, 0x10, A9E_CT_EBYTE2, "byte 2" },
		{ 80 + A9T_FRAME_BYTES - 2, 0x01, A9E_CT_EBYTE2, "byte 2 of the last word" },
		{ 80 + 4 * 77777 + 3, 0x01, A9E_CT_EBYTE3, "byte 3" },
		{ 80 + A9T_FRAME_BYTES - 1, 0x80, A9E_CT_EBYTE3, "byte 3 of the last word" },
	};
	for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
		b[bad[i].off] ^= bad[i].x;
		int rc = a9e_ct_check(b, L, &pl);
		CHECK(rc == bad[i].want, "corrupt %s: rc %d, want %d", bad[i].what, rc, bad[i].want);
		b[bad[i].off] ^= bad[i].x;
	}
	CHECK(a9e_ct_check(b, L - 1, 0) == A9E_CT_ESIZE, "short file accepted");
	CHECK(a9e_ct_check(b, 79, 0) == A9E_CT_ESIZE, "79-byte file accepted");
	uint8_t *b2 = calloc(1, L + 4);
	memcpy(b2, b, L);
	CHECK(a9e_ct_check(b2, L + 4, 0) == A9E_CT_ESIZE, "long file accepted");
	CHECK(a9e_ct_check(b, L, 0) == 0, "restored template refused");
	free(b2);
	free(b);
	printf("apitest: a9e_ct_check: %zu corruptions + 3 sizes refused, %d failures so far\n",
	       sizeof(bad) / sizeof(bad[0]), fails);

	/* 2. split API == a9e_begin */
	uint8_t *grey = malloc(A9T_N), *grey2 = malloc(A9T_N), *old = malloc(A9T_N);
	for (size_t i = 0; i < A9T_N; i++)
		grey[i] = (uint8_t)rnd(), grey2[i] = (uint8_t)(i % A9T_W / 7), old[i] = (uint8_t)(rnd() & 15);
	uint32_t modes[] = { 0x201, 0x203, 0x206, 0x209, 0x202, 0x303 };
	uint32_t pres[] = { 0x201, 0x203, 0x206, 0x209 };
	int bins[] = { 0, 10, 13 };
	struct a9e_rect full = a9e_rect_full(), part = { 101, 37, 777, 500 };
	int n = 0, nframes = 0;
	struct outcome a, c;
	for (int init = 1; init >= 0; init--)
		for (size_t m = 0; m < sizeof(modes) / sizeof(modes[0]); m++)
			for (size_t p = 0; p < sizeof(pres) / sizeof(pres[0]); p++)
				for (int clr = 0; clr <= 1; clr++) {
					int bin = bins[(m + p + (size_t)clr) % 3];
					int partial = !init && clr == 0 && p == m % 4; /* some partial/masked */
					const struct a9e_rect *rc = partial ? &part : &full;
					scenario(&a, 0, init, pres[p], modes[m], clr, rc, partial, bin, grey, old, grey2);
					scenario(&c, 1, init, pres[p], modes[m], clr, rc, partial, bin, grey, old, grey2);
					int ok = a.n_init == c.n_init && !memcmp(a.d_init, c.d_init, 32) &&
						 a.n_upd == c.n_upd && !memcmp(a.d_upd, c.d_upd, 32) &&
						 a.n_next == c.n_next && !memcmp(a.d_next, c.d_next, 32) &&
						 !memcmp(a.d_old, c.d_old, 32) && a.pre_mode == c.pre_mode &&
						 a.mode == c.mode && a.type == c.type && a.clear == c.clear &&
						 a.steps == c.steps;
					CHECK(ok, "split != begin: init %d pre %#x mode %#x clear %d bin %d%s",
					      init, pres[p], modes[m], clr, bin, partial ? " partial+mask" : "");
					n++;
					nframes += a.n_init + a.n_upd + a.n_next;
				}
	free(grey), free(grey2), free(old);
	printf("apitest: split API == a9e_begin in %d scenarios (%d frames each side; frames sha256, "
	       "final levels, pre_mode, mode/type/clear/steps, follow-up update)\n", n, nframes);
}

/* ---- bench: host timings, report only ------------------------------------------- */
static int cmp_d(const void *a, const void *b)
{
	double x = *(const double *)a, y = *(const double *)b;
	return x < y ? -1 : x > y;
}

static double median(double *v, int n)
{
	qsort(v, (size_t)n, sizeof(*v), cmp_d);
	return n % 2 ? v[n / 2] : (v[n / 2 - 1] + v[n / 2]) / 2;
}

/* exactly a9x_compose (kernel a9_eink_transport.c): dst := (frame & byte-0
 * lanes) | template, u64 at a time, OR of the masked values decides null */
static int compose(uint8_t *dst, const uint8_t *frame, const uint8_t *tmpl)
{
	const uint64_t lanes = 0x000000ff000000ffULL;
	uint64_t acc = 0;
	for (int r = 0; r < A9T_TH; r++) {
		const uint64_t *s = (const uint64_t *)(frame + (size_t)r * A9T_TW * 4);
		const uint64_t *c = (const uint64_t *)(tmpl + (size_t)r * A9T_TW * 4);
		uint64_t *d = (uint64_t *)(dst + (size_t)r * A9T_TW * 4);
		for (int i = 0; i < A9T_TW / 2; i++) {
			uint64_t v = s[i] & lanes;
			acc |= v;
			d[i] = v | c[i];
		}
	}
	return !acc;
}

#define REPS 15
#define MAXF 400
static volatile int SINK;

static void bench(void)
{
	uint8_t *steps = malloc(A9T_N), *grey = malloc(A9T_N), *dst = aligned_alloc(64, A9T_FRAME_BYTES);
	struct a9e_rect full = a9e_rect_full();
	static double v[REPS * MAXF];
	double prep, beg, f_clear, f_hold, f_init, cpy, fused, dry, drynosha, sha, prepm[3];
	uint32_t pm[3] = { 0x201, 0x202, 0x206 };
	int k, n, bin = a9t_temp_bin(&E.st, 30, 30);
	for (int y = 0; y < A9T_H; y++)
		for (int x = 0; x < A9T_W; x++)
			steps[y * A9T_W + x] = (uint8_t)(17 * ((x * 16) / A9T_W));
	for (size_t i = 0; i < A9T_N; i++)
		grey[i] = (uint8_t)rnd();

	/* a9e_prepare / a9e_begin: the E5a update (0x203 pre 0x203 clear 1, steps image) */
	for (k = 0; k < REPS; k++) {
		setup();
		memcpy(E.grey, steps, A9T_N);
		E.have_pre = 1, E.pre_mode = 0x203;
		double t0 = now_ms();
		a9e_prepare(&E, 0x203, 1, &full, 0);
		v[k] = now_ms() - t0;
	}
	prep = median(v, REPS);
	for (k = 0; k < REPS; k++) {
		setup();
		memcpy(E.grey, steps, A9T_N);
		E.have_pre = 1, E.pre_mode = 0x203;
		double t0 = now_ms();
		a9e_begin(&E, 0x203, 1, &full, 0, bin);
		v[k] = now_ms() - t0;
	}
	beg = median(v, REPS);
	/* heavier conditioning: error diffusion (0x201), 16-level dither (0x202), 0x206 */
	for (int m = 0; m < 3; m++) {
		for (k = 0; k < REPS; k++) {
			setup();
			memcpy(E.grey, grey, A9T_N);
			E.have_pre = 1, E.pre_mode = pm[m];
			double t0 = now_ms();
			a9e_prepare(&E, pm[m], 0, &full, 0);
			v[k] = now_ms() - t0;
		}
		prepm[m] = median(v, REPS);
	}

	/* a9e_frame: Clear type-2 frames (the E5a update), all of them */
	n = 0;
	for (k = 0; k < 5; k++) {
		setup();
		memcpy(E.grey, steps, A9T_N);
		E.have_pre = 1, E.pre_mode = 0x203;
		a9e_begin(&E, 0x203, 1, &full, 0, bin);
		int more, j = 0;
		do {
			memcpy(OUT, TMPL, A9T_FRAME_BYTES);
			int fin = E.st.step >= E.req.count;	/* in the final type-2 pass */
			double t0 = now_ms();
			more = a9e_frame(&E, OUT);
			double dt = now_ms() - t0;
			if (fin && n < REPS * MAXF)
				v[n++] = dt;
			j++;
		} while (more);
		a9e_end(&E);
	}
	f_clear = median(v, n);

	/* hold-pass frames: Smooth steady update, frames 1..last-1 (memcpy of the cache) */
	n = 0;
	for (k = 0; k < 5; k++) {
		setup();
		memcpy(E.grey, grey, A9T_N);
		E.have_pre = 1, E.pre_mode = 0x206;
		a9e_begin(&E, 0x206, 0, &full, 0, bin);
		int more, j = 0;
		do {
			memcpy(OUT, TMPL, A9T_FRAME_BYTES);
			double t0 = now_ms();
			more = a9e_frame(&E, OUT);
			double dt = now_ms() - t0;
			if (j >= 1 && more && n < REPS * MAXF && !a9e_frame_is_null(OUT))
				v[n++] = dt;
			j++;
		} while (more);
		a9e_end(&E);
	}
	f_hold = median(v, n);

	/* init flash frames */
	n = 0;
	for (k = 0; k < 5; k++) {
		setup();
		a9e_begin_init(&E, bin);
		int more;
		do {
			memcpy(OUT, TMPL, A9T_FRAME_BYTES);
			double t0 = now_ms();
			more = a9e_frame(&E, OUT);
			if (n < REPS * MAXF)
				v[n++] = now_ms() - t0;
		} while (more);
		a9e_end(&E);
	}
	f_init = median(v, n);

	/* full-frame memcpy, and the fused compose (a9x_compose) */
	for (k = 0; k < REPS * 4; k++) {
		double t0 = now_ms();
		memcpy(dst, OUT, A9T_FRAME_BYTES);
		v[k] = now_ms() - t0;
		SINK += dst[k];
	}
	cpy = median(v, REPS * 4);
	for (k = 0; k < REPS * 4; k++) {
		double t0 = now_ms();
		SINK += compose(dst, OUT, TMPL);
		v[k] = now_ms() - t0;
	}
	fused = median(v, REPS * 4);
	for (k = 0; k < REPS * 4; k++) {
		uint8_t d[32];
		double t0 = now_ms();
		a9t_sha256(OUT, A9T_FRAME_BYTES, d);
		v[k] = now_ms() - t0;
		SINK += d[0];
	}
	sha = median(v, REPS * 4);

	/* the E5a dry run as the phone timed it: init flash (t 30/30) + the update,
	 * sha256 of the update's frames (the a9tcon software sha256; pass 1 without) */
	for (k = 0; k < 10; k++) {
		int with_sha = k >= 5;
		uint8_t d[32];
		struct a9t_sha256_ctx c;
		int more;
		setup();
		double t0 = now_ms();
		a9e_begin_init(&E, bin);
		do {
			memcpy(OUT, TMPL, A9T_FRAME_BYTES);
			more = a9e_frame(&E, OUT);
		} while (more);
		a9e_end(&E);
		memcpy(E.grey, steps, A9T_N);
		E.have_pre = 1, E.pre_mode = 0x203;
		a9e_begin(&E, 0x203, 1, &full, 0, bin);
		a9t_sha256_init(&c);
		do {
			memcpy(OUT, TMPL, A9T_FRAME_BYTES);
			more = a9e_frame(&E, OUT);
			if (with_sha)
				a9t_sha256_update(&c, OUT, A9T_FRAME_BYTES);
		} while (more);
		a9t_sha256_final(&c, d);
		a9e_end(&E);
		v[k] = now_ms() - t0;
	}
	drynosha = median(v, 5);
	dry = median(v + 5, 5);

	printf("bench (host, median, ms; report only):\n");
	printf("  a9e_prepare (E5a 0x203 clear 1)   %8.3f\n", prep);
	printf("  a9e_begin   (same)                %8.3f\n", beg);
	printf("  a9e_frame Clear type-2            %8.3f\n", f_clear);
	printf("  a9e_frame hold pass (Smooth)      %8.3f\n", f_hold);
	printf("  a9e_frame init flash              %8.3f\n", f_init);
	printf("  memcpy 1,485,568 B                %8.3f\n", cpy);
	printf("  fused compose (a9x_compose)       %8.3f\n", fused);
	printf("  a9e_prepare 0x201 (err. diffusion) %7.3f\n", prepm[0]);
	printf("  a9e_prepare 0x202 (16-level dither)%7.3f\n", prepm[1]);
	printf("  a9e_prepare 0x206                 %8.3f\n", prepm[2]);
	printf("  sha256 of one frame (a9t_sha256)  %8.3f\n", sha);
	printf("  E5a dry run without sha           %8.3f\n", drynosha);
	printf("  E5a dry run (init + update + sha) %8.3f\n", dry);
	printf("ratios: prepare/frame(Clear) %.2f  begin/frame(Clear) %.2f  copy/frame(Clear) %.3f  "
	       "fused/frame(Clear) %.3f  hold/Clear %.3f  init/Clear %.3f\n",
	       prep / f_clear, beg / f_clear, cpy / f_clear, fused / f_clear, f_hold / f_clear,
	       f_init / f_clear);
	printf("ratios: prepare 0x201/frame %.2f  0x202/frame %.2f  0x206/frame %.2f  sha/frame %.2f\n",
	       prepm[0] / f_clear, prepm[1] / f_clear, prepm[2] / f_clear, sha / f_clear);
	printf("phone scale (dry 561.563 ms / host dry, host sha dominates: use with care): x%.1f -> prepare ~%.1f ms, begin ~%.1f ms "
	       "(measured 109.7), Clear frame ~%.2f ms, memcpy ~%.2f ms, fused ~%.2f ms\n",
	       561.563 / dry, prep * 561.563 / dry, beg * 561.563 / dry, f_clear * 561.563 / dry,
	       cpy * 561.563 / dry, fused * 561.563 / dry);
	free(steps), free(grey), free(dst);
}

int main(int argc, char **argv)
{
	if (argc < 3) {
		fprintf(stderr, "usage: see source header\n");
		return 2;
	}
	size_t blen;
	uint8_t *blob = slurp(argv[1], &blen);
	const struct a9t_tables *t;
	int rc = a9e_wf_check(blob, blen, &t);
	if (rc) {
		fprintf(stderr, "%s: waveform check %d\n", argv[1], rc);
		return 1;
	}
	uint8_t d[32];
	a9t_sha256(blob + sizeof(struct a9e_wf_header), blen - sizeof(struct a9e_wf_header), d);
	if (memcmp(d, blob + 48, 32)) {
		fprintf(stderr, "payload sha256 mismatch\n");
		return 1;
	}
	TMPL = calloc(1, A9T_FRAME_BYTES);
	for (int i = 3; i + 1 < argc; i++)
		if (!strcmp(argv[i], "--template"))
			load_template(argv[i + 1]);
	engine_alloc(t);
	const char *cmd = argv[2];
	if (!strcmp(cmd, "selftest")) {
		selftest();
		printf("selftest: template checks on %ld frames: %ld bytes-1..3 mismatches, "
		       "%ld null frames, %ld != template\n", CTL_FRAMES, CTL_BAD, NULL_FRAMES, NULL_BAD);
		return fails || CTL_BAD || NULL_BAD ? 1 : 0;
	}
	if (!strcmp(cmd, "apitest") || !strcmp(cmd, "bench")) {
		if (!HAVE_TMPL) {
			fprintf(stderr, "%s needs --template\n", cmd);
			return 2;
		}
		if (!strcmp(cmd, "bench")) {
			NOHASH = 1;
			bench();
			return 0;
		}
		apitest();
		printf("apitest: %d failures; template checks on %ld frames: %ld bytes-1..3 mismatches, "
		       "%ld null frames, %ld != template\n", fails, CTL_FRAMES, CTL_BAD, NULL_FRAMES, NULL_BAD);
		return fails || CTL_BAD || NULL_BAD ? 1 : 0;
	}

	const char *grey_p = 0, *old_p = 0, *lv_p = 0;
	uint32_t mode = 0x201, pre = 0;
	int clear = 0, t1 = 25, t2 = 25, have_pre = 0, mask = 0, timing = 0;
	struct a9e_rect r = a9e_rect_full();
	for (int i = 3; i < argc; i++) {
		const char *a = argv[i], *v = i + 1 < argc ? argv[i + 1] : 0;
#define OPT(name) (!strcmp(a, name) && v && (i++, 1))
		if (OPT("--grey")) grey_p = v;
		else if (OPT("--old")) old_p = v;
		else if (OPT("--mode")) mode = (uint32_t)strtoul(v, 0, 0);
		else if (OPT("--pre")) pre = (uint32_t)strtoul(v, 0, 0), have_pre = 1;
		else if (OPT("--clear")) clear = atoi(v);
		else if (OPT("--t1")) t1 = atoi(v);
		else if (OPT("--t2")) t2 = atoi(v);
		else if (OPT("--flag6c7")) E.flag_6c7 = atoi(v);
		else if (OPT("--levels")) lv_p = v;
		else if (OPT("--template")) ;	/* loaded above */
		else if (OPT("--rect")) sscanf(v, "%d,%d,%d,%d", &r.x1, &r.y1, &r.x2, &r.y2);
		else if (!strcmp(a, "--mask")) mask = 1;
		else if (!strcmp(a, "--time")) timing = 1, NOHASH = 1;
		else {
			fprintf(stderr, "unknown option %s\n", a);
			return 2;
		}
#undef OPT
	}
	int bin = a9t_temp_bin(&E.st, t1, t2);
	if (!strcmp(cmd, "init")) {
		a9e_begin_init(&E, bin);
		int n = run_frames(d, 0, 0);
		a9e_end(&E);
		printf("frames %d sha256 ", n);
		hex(d, 32);
		printf("\n");
		return CTL_BAD || NULL_BAD ? 1 : 0;
	}
	if (strcmp(cmd, "update")) {
		fprintf(stderr, "unknown command %s\n", cmd);
		return 2;
	}
	if (grey_p)
		read_exact(grey_p, E.grey, A9T_N);
	else
		memset(E.grey, 255, A9T_N);
	if (old_p)
		read_exact(old_p, E.old, A9T_N);
	E.have_pre = 1;
	E.pre_mode = have_pre ? pre : mode;
	double t0 = now_ms();
	int steps = a9e_begin(&E, mode, clear, &r, mask, bin);
	double t1ms = now_ms() - t0, tf;
	int n = run_frames(d, &tf, 1);
	if (lv_p) {
		FILE *f = fopen(lv_p, "wb");
		fwrite(E.nlv, 1, A9T_N, f);
		fclose(f);
	}
	a9e_end(&E);
	printf("mode %#x clear %d type %u bin %d steps %d\n", E.mode, E.clear, E.type, bin, steps);
	if (timing)
		printf("time begin %.1f ms, frames %.1f ms (%.2f ms/frame)\n", t1ms, tf, tf / n);
	printf("frames %d sha256 ", n);
	hex(d, 32);
	printf("\n");
	return CTL_BAD || NULL_BAD ? 1 : 0;
}
