#define _POSIX_C_SOURCE 200809L
/* same-equiv: the stock-style same-target skip (a9e_same_target / a9e_same_record,
 * eink-latency r2) is SOUND: whenever it says "skip", the prepare it replaces would
 * have found e->unchanged with a steady request (nothing sent), and skipping it
 * leaves the engine exactly where the prepare would have (old, pre_mode, have_pre).
 *
 *   same-equiv BLOB TEMPLATE FIXTURE_DIR [bench]
 *
 * FIXTURE_DIR: raw 824x1648 RGBA frames real*.rgba (run-same-equiv.sh makes them).
 * A driver-shaped loop over a few thousand jobs drawn from a SMALL pool of greys (so
 * repeats and A->B->A reversions are frequent), with mode switches, the clear flag,
 * controls (LUT) and clear_shadow changes, aborts with the driver's old-restore,
 * dry runs, waveform reloads (a9e_setup) and the damage-limited prepare on and off.
 * For every job: S = a9e_same_target() BEFORE the prepare; the prepare runs anyway on
 * a copy of the state, and S => (unchanged && steady). The "driver" then follows S
 * (skip) or the prepare. Also counts the jobs the prepare finds unchanged that S
 * does not catch (missed, informative), and checks mutations are caught.
 * "bench": host timings (report only): the check (equal and unequal), the record,
 * the full prepare, the plane conversion and a memcmp of a whole 824x1648 RGBA
 * source (the alternative the coordinator asked about).
 */
#include "a9_eink_engine.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

static int fails, checks;
#define CHECK(c, ...) do { checks++; if (!(c)) { fails++; if (fails < 40) { fprintf(stderr, "FAIL %s:%d ", __FILE__, __LINE__); fprintf(stderr, __VA_ARGS__); fprintf(stderr, "\n"); } } } while (0)
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

static uint32_t rng = 1234567u;
static uint32_t rnd(void) { rng = rng * 1664525u + 1013904223u; return rng >> 8; }

static const struct a9t_tables *T;
static uint8_t *TMPL, *OUT, *SCR, *SAVE_OLD, *SAVE_NLV;
static struct a9e_engine E;
static struct a9e_same SM;

static void alloc_engine(struct a9e_engine *e)
{
	memset(e, 0, sizeof(*e));
	e->t = T;
	e->grey = malloc(A9T_N); e->old = malloc(A9T_N); e->nlv = malloc(A9T_N);
	e->pairs = malloc(A9T_N * 2);
	for (int k = 0; k < 5; k++) e->arr[k] = malloc(A9T_N * 2);
	e->work = malloc(sizeof(*e->work));
	e->cache = malloc(A9T_FRAME_BYTES);
	e->ee = (int16_t *)malloc(A9E_EE_BYTES);
	e->flag_6c7 = 1;
	a9e_setup(e);
	memcpy(e->cache, TMPL, A9T_FRAME_BYTES);
}

static long n_jobs, n_skip, n_unch, n_missed, n_updates, n_aborts, n_dry;

/* the driver's job: grey g, mode, clear; force_clear / init are the driver's latches */
static int force_clear;
static void job(const uint8_t *g, uint32_t mode, int clear, int dry, int abort_it)
{
	struct a9e_rect full = a9e_rect_full();
	uint32_t pre0 = E.pre_mode;
	int have0 = E.have_pre, s, steady;

	memcpy(E.grey, g, A9T_N);
	n_jobs++;
	/* the driver gate: !dry && !init && !force_clear && !clear && param */
	s = !dry && !force_clear && !clear && a9e_same_target(&E, &SM, mode, 0);
	/* run the prepare anyway, to check the claim; keep the levels to restore like the driver */
	memcpy(SAVE_OLD, E.old, A9T_N);
	memcpy(SAVE_NLV, E.nlv, A9T_N);
	E.inc_enable = (rnd() & 1) && E.ee;
	E.dmg = NULL;
	a9e_prepare(&E, mode, clear, &full, 0);
	steady = !E.clear && !E.req.count && !E.req.kind;
	if (s) {
		n_skip++;
		CHECK(E.unchanged && steady, "skip but prepare says unchanged=%d steady=%d (mode %#x)", E.unchanged, steady, mode);
		CHECK(!memcmp(E.nlv, SAVE_OLD, A9T_N), "skip but nlv != old");
		CHECK(!memcmp(E.old, SAVE_OLD, A9T_N), "skip but the prepare moved old");
		/* the skip leaves the state: old untouched, pre_mode/have_pre untouched (the prepare does not change them either) */
		CHECK(E.pre_mode == pre0 && E.have_pre == have0, "pre_mode/have_pre moved");
		a9e_inc_invalidate(&E);		/* the driver's thread: a job dropped before its prepare */
		return;
	}
	if (E.unchanged && !dry && !force_clear) {
		n_unch++;
		if (!clear && steady)
			n_missed++;
		return;
	}
	a9e_start(&E, 10);
	if (abort_it) {
		memcpy(OUT, TMPL, A9T_FRAME_BYTES);
		a9e_frame(&E, OUT);
		a9e_abort(&E);
		if (!dry) {			/* the driver: glass state unknown, levels restored, clear forced */
			memcpy(E.old, SAVE_OLD, A9T_N);
			a9e_inc_invalidate(&E);
			force_clear = 1;
		}
		n_aborts++;
		return;
	}
	while (1) {
		memcpy(OUT, TMPL, A9T_FRAME_BYTES);
		if (!a9e_frame(&E, OUT))
			break;
	}
	{
		uint32_t pre_save = E.pre_mode;
		int have_save = E.have_pre;

		a9e_end(&E);
		if (dry) {			/* the driver: a dry run restores the real levels, keeps the real record */
			memcpy(E.old, SAVE_OLD, A9T_N);
			E.pre_mode = pre_save;
			E.have_pre = have_save;
			a9e_inc_invalidate(&E);
			n_dry++;
			return;
		}
	}
	a9e_same_record(&E, &SM);
	force_clear = 0;		/* a completed update consumes the forced clear */
	n_updates++;
}

static void bench(uint8_t **frames, int nf, uint8_t **greys)
{
	uint8_t *src = frames[0], *copy = malloc(FBN * 4), *gtmp = malloc(A9T_N);
	struct a9e_rect full = a9e_rect_full(), ch;
	double t0;
	int i, n = 40, sink = 0;

	(void)nf;
	memcpy(copy, src, FBN * 4);
	/* a record of greys[0] from a completed update */
	memcpy(E.grey, greys[0], A9T_N);
	a9e_prepare(&E, 0x206, 0, &full, 0); a9e_start(&E, 10);
	while (a9e_frame(&E, OUT)) ;
	a9e_end(&E); a9e_same_record(&E, &SM);
	t0 = now_ms(); for (i = 0; i < n; i++) { __asm__ volatile("" ::: "memory"); sink += a9e_same_target(&E, &SM, 0x206, 0); }
	printf("bench same_target, equal (2 x 1.36 MB memcmp): %.3f ms\n", (now_ms() - t0) / n);
	memcpy(E.grey, greys[1], A9T_N);
	t0 = now_ms(); for (i = 0; i < n; i++) sink += a9e_same_target(&E, &SM, 0x206, 0);
	printf("bench same_target, different grey (early exit): %.3f ms\n", (now_ms() - t0) / n);
	t0 = now_ms(); for (i = 0; i < n; i++) a9e_same_record(&E, &SM);
	printf("bench same_record (2 x 1.36 MB memcpy): %.3f ms\n", (now_ms() - t0) / n);
	t0 = now_ms(); for (i = 0; i < 10; i++) { E.inc_enable = 0; a9e_prepare(&E, 0x206, 0, &full, 0); }
	printf("bench a9e_prepare full pass, Smooth 0x206: %.3f ms\n", (now_ms() - t0) / 10);
	t0 = now_ms(); for (i = 0; i < n; i++) { memset(&ch, 0, sizeof(ch)); a9e_fb_to_grey_bounce(gtmp, src, A9E_FB_W * 4, A9E_FMT_RGBX, 0, 0, A9E_FB_W, A9E_FB_H, &ch, SCR); }
	printf("bench plane convert (bounce), full frame, CACHED source: %.3f ms\n", (now_ms() - t0) / n);
	{
		int (*volatile cmp)(const void *, const void *, size_t) = memcmp;	/* not inlined or hoisted */

		t0 = now_ms(); for (i = 0; i < n; i++) sink += cmp(src, copy, FBN * 4) != 0;
	}
	printf("bench memcmp of the whole RGBA source vs a cached copy (5.4 MB, equal), CACHED: %.3f ms\n", (now_ms() - t0) / n);
	t0 = now_ms(); for (i = 0; i < n; i++) memcpy(copy, src, FBN * 4);
	printf("bench memcpy of the whole RGBA source into the copy (5.4 MB), CACHED: %.3f ms\n", (now_ms() - t0) / n);
	printf("(host, cached memory only; the phone's source is write-combined: see runs/eink-latency-r2.md)%s\n", sink == -1 ? "" : "");
	free(copy); free(gtmp);
}

int main(int argc, char **argv)
{
	size_t blen, tlen;
	uint8_t *blob, *tm, *frames[16], *greys[16];
	int nf = 0, i, ng;
	char p[512];

	if (argc < 4) { fprintf(stderr, "usage: same-equiv BLOB TEMPLATE FIXTURES [bench]\n"); return 2; }
	blob = slurp(argv[1], &blen);
	if (a9e_wf_check(blob, blen, &T)) { fprintf(stderr, "bad waveform blob\n"); return 1; }
	tm = slurp(argv[2], &tlen);
	TMPL = calloc(1, A9T_FRAME_BYTES);
	if (tlen == A9T_FRAME_BYTES) memcpy(TMPL, tm, tlen);
	else if (tlen > A9T_FRAME_BYTES && !memcmp(tm, "A9CT", 4)) memcpy(TMPL, tm + (tlen - A9T_FRAME_BYTES), A9T_FRAME_BYTES);
	else { fprintf(stderr, "template?\n"); return 2; }
	for (i = 0; i < 12; i++) {
		size_t n;
		FILE *f;

		snprintf(p, sizeof(p), "%s/real%d.rgba", argv[3], i);
		if (!(f = fopen(p, "rb"))) break;
		fclose(f);
		frames[nf] = slurp(p, &n);
		if (n != FBN * 4) { fprintf(stderr, "%s: %zu bytes\n", p, n); return 2; }
		nf++;
	}
	if (nf < 3) { fprintf(stderr, "need >= 3 real frames\n"); return 2; }
	SCR = malloc(a9e_fb_scratch_bytes());
	OUT = malloc(A9T_FRAME_BYTES);
	SAVE_OLD = malloc(A9T_N); SAVE_NLV = malloc(A9T_N);
	for (ng = 0; ng < nf; ng++) {
		greys[ng] = malloc(A9T_N);
		memset(greys[ng], 0xff, A9T_N);
		a9e_fb_to_grey_bounce(greys[ng], frames[ng], A9E_FB_W * 4, A9E_FMT_RGBX, 0, 0, A9E_FB_W, A9E_FB_H, NULL, SCR);
	}
	/* one grey that differs from greys[0] in a single pixel (a 1-level change) */
	greys[ng] = malloc(A9T_N); memcpy(greys[ng], greys[0], A9T_N); greys[ng][A9T_N / 2] ^= 0x10; ng++;
	alloc_engine(&E);
	SM.grey = malloc(A9T_N); SM.old = malloc(A9T_N);

	{
		static const uint32_t modes[] = { 0x206, 0x206, 0x206, 0x201, 0x209, 0x203, 0x206, 0x201 };
		uint32_t mode = 0x206;

		for (i = 0; i < 4000; i++) {
			uint32_t r = rnd() % 100;
			int clear = 0, dry = 0, ab = 0;
			const uint8_t *g = greys[rnd() % (unsigned)(ng > 4 ? 4 : ng)];

			if (r < 8) mode = modes[rnd() % 8];
			else if (r < 11) clear = 1;
			else if (r < 13) dry = 1;
			else if (r < 15) ab = 1;
			else if (r < 16) a9t_set_controls(&E.st, (int)(rnd() % 7) - 3, (int)(rnd() % 7) - 3, (int)(rnd() % 7) - 3);
			else if (r < 17) E.st.clear_shadow ^= 1;
			else if (r < 18) { a9e_setup(&E); memcpy(E.cache, TMPL, A9T_FRAME_BYTES); SM.valid = 0; force_clear = 1; }
			else if (r < 22) g = greys[ng - 1];	/* the one-pixel neighbour of greys[0] */
			job(g, mode, clear, dry, ab);
		}
	}
	CHECK(n_skip > 50, "the skip was exercised only %ld times", n_skip);
	/* mutation: a record that lies about old must not produce a skip whose prepare is not unchanged */
	{
		struct a9e_rect full = a9e_rect_full();

		memcpy(E.grey, greys[0], A9T_N);
		a9e_prepare(&E, 0x206, 0, &full, 0); a9e_start(&E, 10); while (a9e_frame(&E, OUT)) ; a9e_end(&E);
		a9e_same_record(&E, &SM);
		E.old[100] ^= 1;	/* the levels changed behind the record */
		CHECK(!a9e_same_target(&E, &SM, 0x206, 0), "mutation: old changed, still a skip");
		E.old[100] ^= 1;
		CHECK(a9e_same_target(&E, &SM, 0x206, 0), "restored: no skip");
		E.grey[5] ^= 0x40;
		CHECK(!a9e_same_target(&E, &SM, 0x206, 0), "mutation: grey changed, still a skip");
		E.grey[5] ^= 0x40;
		CHECK(!a9e_same_target(&E, &SM, 0x201, 0), "mutation: other mode, still a skip");
		CHECK(!a9e_same_target(&E, &SM, 0x206, 1), "mutation: clear, still a skip");
		E.st.clear_shadow ^= 1;
		CHECK(!a9e_same_target(&E, &SM, 0x206, 0), "mutation: clear_shadow changed, still a skip");
		E.st.clear_shadow ^= 1;
		E.st.g2[17] ^= 1;
		CHECK(!a9e_same_target(&E, &SM, 0x206, 0), "mutation: LUT changed, still a skip");
		E.st.g2[17] ^= 1;
		E.flag_6c7 = 0;
		CHECK(!a9e_same_target(&E, &SM, 0x206, 0), "mutation: flag_6c7 off, still a skip");
		E.flag_6c7 = 1;
	}
	printf("same-equiv: %ld jobs, %ld skipped by same_target, %ld more found unchanged by the prepare (%ld of them steady = missed), "
	       "%ld updates, %ld aborts, %ld dry; %d checks, %d failures\n",
	       n_jobs, n_skip, n_unch, n_missed, n_updates, n_aborts, n_dry, checks, fails);
	if (argc > 4 && !strcmp(argv[4], "bench"))
		bench(frames, nf, greys);
	return fails ? 1 : 0;
}
