#define _POSIX_C_SOURCE 200809L
/* pp-model: HOST MODEL ONLY (not linked into the driver) of per-pixel frame counters with queue
 * semantics for the hold-class modes (Smooth 518 = type 6, Speed 521 = type 9), built on the real
 * waveform tables and real conditioned levels. It exists to turn the design of
 * runs/eink-damage.md section 2 into checked facts before any driver code:
 *
 *   pp-model BLOB TEMPLATE FIXTURE_DIR
 *
 *  A. single update from idle == today's frames, bit for bit (every frame of the update incl. the
 *     terminal null frame) for Smooth and Speed, all 14 bins, 3 transitions between real frames;
 *  B. two overlapping updates (the second arrives k frames into the first, k = 0,1,3,Ls-2,Ls-1,Ls):
 *     per pixel, the waveform rows executed (from level, to level) are exactly those of the two
 *     updates run serially (so the per-pixel drive sequence, its length and its signed sum, the DC
 *     proxy, are identical to serial: queue semantics add no drive and remove none), the epoch is
 *     shorter than the serial total, and the start latency of the second update's idle pixels is 0;
 *  C. (Caster-style early cancel, model only) a pixel flicker workload: net signed drive per pixel
 *     with queue-only vs cancel (reversal drives k+1 frames after k): the DC drift numbers the
 *     design's accumulator decision rests on.
 * Pixel state is the Caster state machine of the study's D3.3 (fc, dir, mindrv) with the start
 * pair taken from the table, as D3.3 prescribes for equivalence.
 */
#include "a9_eink_engine.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int fails, checks;
#define CHECK(c, ...) do { checks++; if (!(c)) { fails++; if (fails < 30) { fprintf(stderr, "FAIL %s:%d ", __FILE__, __LINE__); fprintf(stderr, __VA_ARGS__); fprintf(stderr, "\n"); } } } while (0)

#define N A9T_N
#define NONE 0xff

static const struct a9t_tables *T;
static uint8_t *TMPL;

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

/* ---- hold-class table access (layout of a9tcon.c: bin block = [len] then rows interleaved) ---- */
static const uint8_t *cls_base(int type, int bin)
{
	return type == 9 ? T->speed + (size_t)bin * A9T_SLOTS * A9T_RS_SMOOTH
			 : T->smooth + (size_t)bin * A9T_SLOTS * A9T_RS_SMOOTH;
}
static int cls_len(int type, int bin) { return cls_base(type, bin)[0]; }
static uint8_t frame0_code(int type, int bin, int from, int to)
{
	return cls_base(type, bin)[1 + (size_t)(from << 4 | to) * A9T_RS_SMOOTH];
}

/* ---- the per-pixel queue machine ----------------------------------------------------------- */
struct pp {
	int type, bin, Ls;		/* Ls = table length: Ls-1 drive frames + 1 null */
	uint8_t *lvl, *tgt, *fc, *pend, *code;
	uint16_t *startpair;		/* per pixel: executed (from<<4|to), first two starts */
	uint8_t *nstarts;
	int *startframe;		/* frame of the first start of each pixel */
	int frame;
	long dc_sum;			/* signed drive sum over all pixels (DC proxy), +1 per code 2, -1 per code 1 */
	int8_t *dc;			/* per pixel signed sum */
	int cancel;			/* early cancel (model C) */
	uint8_t *mind, *dir;
};

static void pp_alloc(struct pp *p, int type, int bin, const uint8_t *lvl0)
{
	memset(p, 0, sizeof(*p));
	p->type = type; p->bin = bin; p->Ls = cls_len(type, bin);
	p->lvl = malloc(N); p->tgt = malloc(N); p->fc = calloc(1, N); p->pend = malloc(N); p->code = calloc(1, N);
	p->startpair = calloc(N, 4);	/* two entries of uint16 per pixel */
	p->nstarts = calloc(1, N); p->startframe = malloc(N * sizeof(int)); p->dc = calloc(1, N);
	p->mind = calloc(1, N); p->dir = calloc(1, N);
	memcpy(p->lvl, lvl0, N); memcpy(p->tgt, lvl0, N); memset(p->pend, NONE, N);
	for (size_t i = 0; i < N; i++) p->startframe[i] = -1;
}

static void pp_start(struct pp *p, size_t i, int to)
{
	int from = p->lvl[i];

	if (p->nstarts[i] < 2) ((uint16_t *)p->startpair)[i * 2 + p->nstarts[i]] = (uint16_t)(from << 4 | to);
	p->startframe[i] = p->frame;
	p->nstarts[i]++;
	p->tgt[i] = (uint8_t)to;
	p->code[i] = frame0_code(p->type, p->bin, from, to);
	p->fc[i] = (uint8_t)(p->Ls - 1);
}

/* a new update's conditioned levels arrive (queue semantics) */
static void pp_arrive(struct pp *p, const uint8_t *nlv)
{
	for (size_t i = 0; i < N; i++) {
		if (p->fc[i]) p->pend[i] = nlv[i] == p->tgt[i] ? NONE : nlv[i];
		else p->pend[i] = nlv[i] == p->lvl[i] ? NONE : nlv[i];
	}
}

/* one frame; codes out in p->code (0 for idle pixels). returns number of pixels with drive. */
static int pp_frame(struct pp *p, uint8_t *emit)
{
	int active = 0;

	for (size_t i = 0; i < N; i++) {
		if (!p->fc[i] && p->pend[i] != NONE) { pp_start(p, i, p->pend[i]); p->pend[i] = NONE; }
		if (p->fc[i]) {
			emit[i] = p->code[i];
			active++;
			if (emit[i] == 2) { p->dc[i]++; p->dc_sum++; } else if (emit[i] == 1) { p->dc[i]--; p->dc_sum--; }
			if (--p->fc[i] == 0) p->lvl[i] = p->tgt[i];
		} else emit[i] = 0;
	}
	p->frame++;
	return active;
}

static int pp_busy(const struct pp *p)
{
	for (size_t i = 0; i < N; i++) if (p->fc[i] || p->pend[i] != NONE) return 1;
	return 0;
}

/* codes -> a DSI frame exactly as render() lays it out; bytes 1..3 from the template */
static void pack(const uint8_t *emit, uint8_t *out)
{
	memcpy(out, TMPL, A9T_FRAME_BYTES);
	for (int y = 0; y < A9T_H; y++)
		for (int w = 0; w < A9T_DATA_WORDS; w++) {
			const uint8_t *c = emit + (size_t)y * A9T_W + (size_t)w * 4;

			out[((size_t)(y + A9T_HEAD_ROWS) * A9T_TW + w) * 4] = (uint8_t)(c[0] << 6 | c[1] << 4 | c[2] << 2 | c[3]);
		}
}

/* ---- inputs from the real engine ----------------------------------------------------------------- */
static struct a9e_engine E;
static uint8_t *alloc_levels(const uint8_t *rgba, uint32_t mode, uint8_t *old_out, int first)
{
	/* conditioned levels of a frame under `mode` via the reference engine; old_out gets the levels before */
	static uint8_t *g;
	struct a9e_rect full = a9e_rect_full();
	uint8_t *lv = malloc(N);

	if (!g) g = malloc(N);
	a9e_fb_to_grey(g, rgba, A9E_FB_W * 4, A9E_FMT_RGBX, 0, 0, A9E_FB_W, A9E_FB_H, NULL);
	memcpy(E.grey, g, N);
	if (first) { a9e_setup(&E); memcpy(E.cache, TMPL, A9T_FRAME_BYTES); memcpy(E.grey, g, N); }
	if (old_out) memcpy(old_out, E.old, N);
	E.have_pre = 1; E.pre_mode = mode;
	a9e_prepare(&E, mode, 0, &full, 0);
	memcpy(lv, E.nlv, N);
	return lv;
}

static void commit_levels(const uint8_t *lv)
{
	memcpy(E.old, lv, N);
}

static void alloc_engine(void)
{
	int k;

	memset(&E, 0, sizeof(E));
	E.t = T;
	E.grey = malloc(N); E.old = malloc(N); E.nlv = malloc(N); E.pairs = malloc(N * 2);
	for (k = 0; k < 5; k++) E.arr[k] = malloc(N * 2);
	E.work = malloc(sizeof(*E.work));
	E.cache = malloc(A9T_FRAME_BYTES);
	E.flag_6c7 = 1;
	a9e_setup(&E);
	memcpy(E.cache, TMPL, A9T_FRAME_BYTES);
}

static void hexs(const uint8_t *d, char *o) { for (int i = 0; i < 32; i++) snprintf(o + 2 * i, 3, "%02x", d[i]); }

/* A. single update from idle == legacy frames */
static void test_equiv(uint8_t **frames, int nf)
{
	static const uint32_t MODES[2] = { 0x206, 0x209 };
	uint8_t *emit = malloc(N), *out = malloc(A9T_FRAME_BYTES), *ref = malloc(A9T_FRAME_BYTES);
	int m, bin, pair;

	for (m = 0; m < 2; m++)
		for (pair = 0; pair < 3; pair++) {
			uint8_t *lvA, *lvB;
			int fa = pair % nf, fb = (pair + 1) % nf;

			alloc_engine();
			lvA = alloc_levels(frames[fa], MODES[m], NULL, 0);
			commit_levels(lvA);
			lvB = alloc_levels(frames[fb], MODES[m], NULL, 0);
			for (bin = 0; bin < A9T_BINS; bin++) {
				struct pp p;
				struct a9t_sha256_ctx cr, cm;
				uint8_t dr[32], dm[32];
				char h1[65], h2[65];
				int more, nfr = 0, active, lastnull = 0;

				/* legacy: start at this bin on the prepared engine, all frames */
				a9e_start(&E, bin);
				a9t_sha256_init(&cr);
				do {
					memcpy(ref, TMPL, A9T_FRAME_BYTES);
					more = a9e_frame(&E, ref);
					a9t_sha256_update(&cr, ref, A9T_FRAME_BYTES);
					nfr++;
				} while (more);
				a9t_sha256_final(&cr, dr);
				/* model: the same epoch, one frame_step per frame, then the terminal null frame */
				pp_alloc(&p, MODES[m] & 0xff, bin, lvA);
				pp_arrive(&p, lvB);
				a9t_sha256_init(&cm);
				{
					int mf = 0;

					do {
						active = pp_frame(&p, emit);
						if (!active) break;
						pack(emit, out);
						a9t_sha256_update(&cm, out, A9T_FRAME_BYTES);
						mf++;
					} while (1);
					memset(emit, 0, N); pack(emit, out);
					a9t_sha256_update(&cm, out, A9T_FRAME_BYTES);	/* the terminal null frame */
					mf++; lastnull = 1;
					CHECK(mf == nfr, "mode %#x bin %d: frame count model %d legacy %d", MODES[m], bin, mf, nfr);
				}
				a9t_sha256_final(&cm, dm);
				hexs(dr, h1); hexs(dm, h2);
				CHECK(!strcmp(h1, h2), "mode %#x pair %d bin %d: frames differ", MODES[m], pair, bin);
				CHECK(!memcmp(p.lvl, lvB, N), "mode %#x bin %d: settled levels != target", MODES[m], bin);
				(void)lastnull;
				free(p.lvl); free(p.tgt); free(p.fc); free(p.pend); free(p.code); free(p.startpair); free(p.nstarts); free(p.startframe); free(p.dc); free(p.mind); free(p.dir);
			}
			printf("A: mode %#x transition %d->%d: model frames == legacy frames for all 14 bins\n", MODES[m], fa, fb);
			free(lvA); free(lvB);
		}
	free(emit); free(out); free(ref);
}

/* B. two overlapping updates */
static uint8_t *edited_frame(const uint8_t *base, const uint8_t *other, int r1, int r2)
{
	uint8_t *e = malloc((size_t)A9E_FB_W * A9E_FB_H * 4);

	memcpy(e, base, (size_t)A9E_FB_W * A9E_FB_H * 4);
	memcpy(e + (size_t)r1 * A9E_FB_W * 4, other + (size_t)r1 * A9E_FB_W * 4, (size_t)(r2 - r1) * A9E_FB_W * 4);
	return e;
}

static void free_pp(struct pp *p)
{
	free(p->lvl); free(p->tgt); free(p->fc); free(p->pend); free(p->code); free(p->startpair); free(p->nstarts);
	free(p->startframe); free(p->dc); free(p->mind); free(p->dir);
}

static void overlap_case(const char *name, uint32_t mode, int bin, const uint8_t *lvA, const uint8_t *lvB, const uint8_t *lvC)
{
	uint8_t *emit = malloc(N);
	int Ls = cls_len(mode & 0xff, bin);
	int ks[6] = { 0, 1, 3, Ls - 2, Ls - 1, Ls };

	for (int ki = 0; ki < 6; ki++) {
		struct pp p;
		int k = ks[ki], f, nb = k, serial_len = 2 * Ls;
		long bad_seq = 0, changed2 = 0, idle_n = 0, busy_n = 0, idle_lat_max = 0, busy_lat_sum = 0, dc_ref = 0;
		uint8_t *was_idle = malloc(N);

		pp_alloc(&p, mode & 0xff, bin, lvA);
		pp_arrive(&p, lvB);
		for (f = 0; f < k; f++) pp_frame(&p, emit);
		for (size_t i = 0; i < N; i++) was_idle[i] = p.fc[i] == 0;
		pp_arrive(&p, lvC);	/* the second update arrives k frames into the first (k == Ls: it has ended) */
		while (pp_busy(&p)) { pp_frame(&p, emit); nb++; }
		nb++;	/* terminal null frame */
		for (size_t i = 0; i < N; i++) {
			uint16_t want[2];
			int nw = 0;
			long s = 0;

			if (k == 0) {
				/* the first update has not started anywhere: its pending target is replaced (coalesced) */
				if (lvA[i] != lvC[i]) want[nw++] = (uint16_t)(lvA[i] << 4 | lvC[i]);
			} else {
				if (lvA[i] != lvB[i]) want[nw++] = (uint16_t)(lvA[i] << 4 | lvB[i]);
				if (lvB[i] != lvC[i]) want[nw++] = (uint16_t)(lvB[i] << 4 | lvC[i]);
			}
			if (p.nstarts[i] != nw) { bad_seq++; continue; }
			for (int j = 0; j < nw; j++) {
				uint8_t c = frame0_code(mode & 0xff, bin, want[j] >> 4, want[j] & 15);

				if (((uint16_t *)p.startpair)[i * 2 + j] != want[j]) bad_seq++;
				s += (c == 2) - (c == 1);
			}
			s *= Ls - 1;
			dc_ref += s;
			CHECK(p.dc[i] == (int8_t)s, "pixel %zu: signed drive %d vs serial %ld", i, p.dc[i], s);
			CHECK(p.lvl[i] == lvC[i], "pixel %zu settled at %d, target %d", i, p.lvl[i], lvC[i]);
			if (lvB[i] != lvC[i]) {
				long lat = p.startframe[i] - k;

				changed2++;
				if (was_idle[i]) { idle_n++; if (lat > idle_lat_max) idle_lat_max = lat; }
				else { busy_n++; busy_lat_sum += lat; }
			}
		}
		CHECK(!bad_seq, "%s mode %#x k=%d: %ld pixels executed rows differ from the expected rows", name, mode, k, bad_seq);
		CHECK(p.dc_sum == dc_ref, "%s mode %#x k=%d: total signed drive %ld vs expected %ld", name, mode, k, p.dc_sum, dc_ref);
		CHECK(nb <= serial_len, "%s mode %#x k=%d: epoch %d frames > serial %d", name, mode, k, nb, serial_len);
		CHECK(idle_lat_max == 0, "%s mode %#x k=%d: an idle pixel waited %ld frames", name, mode, k, idle_lat_max);
		printf("B[%s]: mode %#x bin %d, 2nd update at frame %2d: epoch %3d frames (serial %3d); update-2 pixels %6ld: idle at arrival %6ld "
		       "(latency max %ld), busy %6ld (mean wait %.1f; serial wait %d for all); rows == %s for every pixel\n",
		       name, mode, bin, k, nb, serial_len, changed2, idle_n, idle_lat_max, busy_n,
		       busy_n ? (double)busy_lat_sum / busy_n : 0.0, k < Ls ? Ls - k : 0, k ? "serial rows" : "coalesced A->C");
		free(was_idle);
		free_pp(&p);
	}
	free(emit);
}

static void test_overlap(uint8_t **frames, int nf)
{
	static const uint32_t MODES[2] = { 0x206, 0x209 };

	for (int m = 0; m < 2; m++) {
		uint8_t *lvA, *lvB, *lvC, *f2, *f3;

		alloc_engine();
		lvA = alloc_levels(frames[0], MODES[m], NULL, 0);
		commit_levels(lvA);
		lvB = alloc_levels(frames[1 % nf], MODES[m], NULL, 0);
		commit_levels(lvB);
		f2 = edited_frame(frames[1 % nf], frames[2 % nf], 1048, A9E_FB_H);	/* keyboard strip */
		lvC = alloc_levels(f2, MODES[m], NULL, 0);
		overlap_case("full change, then keyboard", MODES[m], 10, lvA, lvB, lvC);
		/* disjoint: update 1 = keyboard strip, update 2 = status bar */
		commit_levels(lvC);
		f3 = edited_frame(f2, frames[3 % nf], 0, 100);
		free(lvA);
		lvA = lvB; lvB = lvC;
		lvC = alloc_levels(f3, MODES[m], NULL, 0);
		overlap_case("keyboard, then status bar (disjoint)", MODES[m], 10, lvA, lvB, lvC);
		free(f2); free(f3); free(lvA); free(lvB); free(lvC);
	}
}

/* C. flicker workload on binary pixels: DC drift, queue-only vs early cancel (Caster D1.3 rule) */
static uint32_t rng = 4242;
static uint32_t rnd(void) { rng = rng * 1664525u + 1013904223u; return rng >> 8; }

static void test_flicker(void)
{
	/* a pixel toggles its target every `per` frames (random jitter) for 400 frames; Ls-1 = 7 drive frames (Smooth bin 10).
	 * Model of one pixel is enough: state (fc, dir, mindrv) exactly as D3.3. net = signed frames driven white - black. */
	int Ls = cls_len(6, 10), Nd = Ls - 2;	/* N = Ls-2 (+1 start frame) as D3.1 */
	int MINDRV = 2;

	printf("C: flicker workload, one binary pixel, Smooth bin 10 (N=%d, drive = N+1 = %d frames), 400 frames, 2000 trials per period:\n", Nd, Nd + 1);
	printf("   %-8s %-12s %10s %10s %10s %12s\n", "period", "policy", "mean|net|", "max|net|", "mean drive", "reversals");
	for (int per = 2; per <= 12; per += (per < 8 ? 2 : 4))
		for (int policy = 0; policy < 2; policy++) {
			double sum_abs = 0, sum_drv = 0, sum_rev = 0;
			int max_abs = 0;

			for (int tr = 0; tr < 2000; tr++) {
				int fc = 0, dir = 0, md = 0, tgt = 0, nxt_tog = per, net = 0, drv = 0, rev = 0, pendq = -1;

				for (int f = 0; f < 400 + 40; f++) {
					int in = tgt;

					if (f < 400 && f == nxt_tog) { tgt ^= 1; in = tgt; nxt_tog += per + (int)(rnd() % 3) - 1; if (nxt_tog <= f) nxt_tog = f + 1; }
					if (f >= 400) in = tgt;
					if (policy == 0) {	/* queue only: finish the run, then follow the latest target */
						if (fc == 0) {
							if (in != dir) { dir = in; fc = Nd; net += in ? 1 : -1; drv++; }
						} else { net += dir ? 1 : -1; drv++; fc--; }
						(void)pendq;
					} else {	/* early cancel, exactly D1.3 */
						if (fc == 0) {
							if (in != dir) { dir = in; fc = Nd; md = MINDRV; net += in ? 1 : -1; drv++; }
						} else if (in != dir && md == 0) {
							fc = Nd - fc + 1; if (fc < 1) fc = 1; dir = in; md = MINDRV; net += in ? 1 : -1; drv++; rev++;
						} else { net += dir ? 1 : -1; drv++; fc--; if (md) md--; }
					}
				}
				/* settle: finish whatever is running */
				while (fc) { net += dir ? 1 : -1; drv++; fc--; }
				{
					int a = net < 0 ? -net : net;

					sum_abs += a; if (a > max_abs) max_abs = a; sum_drv += drv; sum_rev += rev;
				}
			}
			printf("   %-8d %-12s %10.2f %10d %10.1f %12.2f\n", per, policy ? "early cancel" : "queue only", sum_abs / 2000, max_abs, sum_drv / 2000, sum_rev / 2000);
		}
}

int main(int argc, char **argv)
{
	size_t blen, tlen;
	uint8_t *blob, *tm, *frames[16];
	int nf = 0;
	char p[512];

	if (argc < 4) { fprintf(stderr, "usage: pp-model BLOB TEMPLATE FIXTURES\n"); return 2; }
	blob = slurp(argv[1], &blen);
	if (a9e_wf_check(blob, blen, &T)) { fprintf(stderr, "bad waveform blob\n"); return 1; }
	tm = slurp(argv[2], &tlen);
	TMPL = calloc(1, A9T_FRAME_BYTES);
	if (tlen == A9T_FRAME_BYTES) memcpy(TMPL, tm, tlen);
	else if (tlen > A9T_FRAME_BYTES && !memcmp(tm, "A9CT", 4)) memcpy(TMPL, tm + (tlen - A9T_FRAME_BYTES), A9T_FRAME_BYTES);
	else { fprintf(stderr, "template?\n"); return 2; }
	for (int i = 0; i < 16; i++) {
		size_t n;
		FILE *f;

		snprintf(p, sizeof(p), "%s/real%d.rgba", argv[3], i);
		f = fopen(p, "rb");
		if (!f) break;
		fclose(f);
		frames[nf++] = slurp(p, &n);
	}
	if (nf < 3) { fprintf(stderr, "need >= 3 frames\n"); return 2; }
	printf("hold-class lengths: Smooth bin10 Ls=%d, Speed bin10 Ls=%d (drive frames Ls-1)\n", cls_len(6, 10), cls_len(9, 10));
	test_equiv(frames, nf);
	test_overlap(frames, nf);
	test_flicker();
	printf("pp-model: %d checks, %d failures\n", checks, fails);
	return fails ? 1 : 0;
}
