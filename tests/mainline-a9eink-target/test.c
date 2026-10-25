/* SPDX-License-Identifier: GPL-2.0-only */
/* Production engine + TCON; reference is a complete unmasked target canvas.
 * Synthetic waveforms deliberately drive sentinel 256 and all changed pairs.
 * This tests target/drive selection, not vendor waveform or optical quality. */
#include "a9_eink_engine.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static struct a9t_tables tables;
static unsigned checks;
static int actual_tables;
#define CHECK(c) do { ++checks; if (!(c)) { \
	fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); exit(1); } } while (0)

static void *alloc(size_t n)
{
	void *p = calloc(1, n);
	assert(p);
	return p;
}

static void table_rows(uint8_t *p, size_t stride)
{
	for (int b = 0; b < A9T_BINS; ++b)
		for (int i = 0; i < A9T_SLOTS; ++i) {
			uint8_t *r = p + ((size_t)b * A9T_SLOTS + i) * stride;
			int changed = i == 256 || (i >> 4) != (i & 15);
			r[0] = 4;
			r[1] = changed ? 1 : 0;
			r[2] = changed ? 2 : 0;
			r[3] = changed ? 1 : 0;
			r[4] = 0;
		}
}

static void make_tables(void)
{
	for (int i = 0; i < 1024; ++i) tables.dither[i] = (i * 73) & 255;
	table_rows(tables.clear3, A9T_RS_CLEAR);
	table_rows(tables.type2, A9T_RS_CLEAR);
	table_rows(tables.balanced, A9T_RS_BAL);
	table_rows(tables.smooth, A9T_RS_SMOOTH);
	table_rows(tables.speed, A9T_RS_SMOOTH);
	for (int b = 0; b < A9T_BINS; ++b) {
		tables.init[b * A9T_INIT_STRIDE] = 4;
		tables.init[b * A9T_INIT_STRIDE + 1] = 1;
		tables.init[b * A9T_INIT_STRIDE + 2] = 2;
	}
}

static void create(struct a9e_engine *e)
{
	memset(e, 0, sizeof(*e));
	e->t = &tables;
	e->grey = alloc(A9T_N);
	e->old = alloc(A9T_N);
	e->nlv = alloc(A9T_N);
	e->pairs = alloc(A9T_N * sizeof(*e->pairs));
	for (int k = 0; k < 5; ++k) e->arr[k] = alloc(A9T_N * sizeof(*e->arr[k]));
	e->work = alloc(sizeof(*e->work));
	e->cache = alloc(A9T_FRAME_BYTES);
	e->flag_6c7 = 1;
	a9e_setup(e);
}

static void destroy(struct a9e_engine *e)
{
	free(e->grey); free(e->old); free(e->nlv); free(e->pairs);
	for (int k = 0; k < 5; ++k) free(e->arr[k]);
	free(e->work); free(e->cache);
}

static void history(struct a9e_engine *e, unsigned mode, int grey)
{
	memset(e->grey, grey, A9T_N);
	a9t_condition(&e->st, e->grey, mode, e->old, e->work);
	e->have_pre = 1;
	e->pre_mode = mode & ~0x100u;
}

static unsigned same_frames(struct a9e_engine *e, struct a9e_engine *reference)
{
	uint8_t *a = alloc(A9T_FRAME_BYTES), *b = alloc(A9T_FRAME_BYTES);
	unsigned n = 0;
	int more, refmore;
	a9e_start(e, 3);
	a9e_start(reference, 3);
	do {
		more = a9e_frame(e, a);
		refmore = a9e_frame(reference, b);
		CHECK(more == refmore);
		CHECK(!memcmp(a, b, A9T_FRAME_BYTES));
		CHECK(++n < 128);
	} while (more);
	CHECK(a9e_frame_is_null(a));
	free(a); free(b);
	return n;
}

static void full_reference(struct a9e_engine *reference, struct a9e_engine *e)
{
	memcpy(reference->old, e->old, A9T_N);
	memcpy(reference->grey, e->grey, A9T_N);
	reference->mode = e->mode;
	reference->clear = e->clear;
	reference->type = e->type;
	memset(&reference->req, 0, sizeof(reference->req));
	a9t_condition(&reference->st, reference->grey, reference->mode,
		      reference->nlv, reference->work);
	a9t_pack(reference->old, reference->nlv, reference->mode,
		 reference->clear, reference->pairs);
}

static void one_pixel(unsigned mode, int diffusion)
{
	struct a9e_engine e, reference;
	struct a9e_rect raw = {20, 20, 21, 21};
	const size_t pixel = 20 * A9T_W + 20;
	size_t differences = 0, outside = 0;
	create(&e); create(&reference);
	int baseline = mode == 514 || mode == 0x303 ? 136 : mode == 521 ? 0 : 128;
	history(&e, mode, baseline);
	e.grey[pixel] = 255;
	int mask = !actual_tables || mode == 513 || mode == 518;
	CHECK(a9e_prepare(&e, mode, 0, &raw, mask) >= 0);
	CHECK(!e.req.count && !e.req.kind && !e.clear);
	full_reference(&reference, &e);
	CHECK(!memcmp(e.nlv, reference.nlv, A9T_N));
	CHECK(!memcmp(e.pairs, reference.pairs, A9T_N * sizeof(*e.pairs)));
	for (size_t i = 0; i < A9T_N; ++i) {
		if (reference.nlv[i] != reference.old[i]) {
			++differences;
			if (i != pixel) ++outside;
			CHECK((int)(i % A9T_W) >= e.rect.x1 && (int)(i % A9T_W) < e.rect.x2);
			CHECK((int)(i / A9T_W) >= e.rect.y1 && (int)(i / A9T_W) < e.rect.y2);
		}
	}
	CHECK(diffusion ? outside > 0 : outside == 0);
	if (mode == 518 || mode == 513) {
		CHECK(differences == 164760);
		CHECK(outside == 164760);
	}
	unsigned frames = same_frames(&e, &reference);
	a9e_end(&e);
	CHECK(!memcmp(e.old, reference.nlv, A9T_N));
	printf("PASS mode %u (mask=%d): %zu target changes, %zu outside raw pixel; packed target and %u frames match full redraw\n",
	       mode, mask, differences, outside, frames);
	destroy(&e); destroy(&reference);
}

static void sentinel(void)
{
	struct a9e_engine e, reference;
	struct a9e_rect raw = {20, 20, 21, 21};
	size_t pixel = 20 * A9T_W + 20;
	create(&e); create(&reference);
	history(&e, 515, 255);
	e.old[pixel] = 0;
	CHECK(a9e_prepare(&e, 515, 0, &raw, !actual_tables) >= 0);
	full_reference(&reference, &e);
	CHECK(e.pairs[pixel - 1] == 256);
	CHECK(e.rect.x1 <= 19 && e.rect.x2 > 20 && e.rect.y1 <= 20 && e.rect.y2 > 20);
	CHECK(!memcmp(e.pairs, reference.pairs, A9T_N * sizeof(*e.pairs)));
	same_frames(&e, &reference);
	printf("PASS Clear sentinel 256 neighbor remains in drive footprint (mask=%d)\n", !actual_tables);
	destroy(&e); destroy(&reference);
}

static void duplicates(void)
{
	struct a9e_engine e;
	struct a9e_rect raw = {20, 20, 21, 21}, full = a9e_rect_full();
	size_t pixel = 20 * A9T_W + 20;
	create(&e);
	const unsigned modes[] = {513, 518, 514, 0x303, 521, 515, 520};
	for (unsigned m = 0; m < sizeof(modes) / sizeof(modes[0]); ++m) {
		unsigned mode = modes[m];
		history(&e, mode, 0);
		e.grey[pixel] = 255;
		CHECK(a9e_prepare(&e, mode, 0, &raw, 0) >= 0);
		/* Coalescing replaced pending B with A; neither was drained. */
		e.grey[pixel] = 0;
		CHECK(a9e_prepare(&e, mode, 0, &raw, 0) >= 0);
		CHECK(e.unchanged);
		CHECK(e.have_pre && e.pre_mode == (mode & ~0x100u) && e.nframes == 0);
		CHECK(!e.req.kind && !e.req.count && !e.clear);
		/* Raw bytes changed, final target did not: an explicit extension of
		 * stock's raw-luma equality predicate. */
		history(&e, mode, 255);
		e.grey[pixel] = 254;
		CHECK(a9e_prepare(&e, mode, 0, &raw, 0) >= 0);
		CHECK(e.unchanged);
		CHECK(e.nframes == 0 && e.old[pixel] == 15);
	}
	puts("PASS duplicate coalesced A-B-A and distinct raw/same target: all seven modes, zero produced frames");

	a9e_setup(&e);
	memset(e.grey, 255, A9T_N);
	CHECK(a9e_prepare(&e, 518, 0, &full, 0) >= 0); /* no trusted history */
	CHECK(!e.unchanged);
	history(&e, 518, 255);
	e.flag_6c7 = 0; /* stock's non-normal path is not duplicate-eligible */
	CHECK(a9e_prepare(&e, 518, 0, &full, 0) >= 0);
	CHECK(!e.unchanged && e.nframes == 0);
	e.flag_6c7 = 1;
	CHECK(a9e_prepare(&e, 518, 1, &raw, 1) >= 0);
	CHECK(e.clear && e.req.count && !e.mask && !e.unchanged);
	history(&e, 513, 255);
	CHECK(a9e_prepare(&e, 518, 0, &raw, 1) >= 0);
	CHECK(e.req.kind && e.req.count && !e.mask && !e.unchanged);
	a9e_begin_init(&e, 3);
	uint8_t *out = alloc(A9T_FRAME_BYTES);
	CHECK(a9e_frame(&e, out));
	CHECK(!a9e_frame_is_null(out));
	free(out);
	puts("PASS unknown history, explicit clear, mode switch and init remain required sequences");
	destroy(&e);
}

static void load_tables(const char *path)
{
	FILE *f = fopen(path, "rb");
	CHECK(f != NULL);
	CHECK(!fseek(f, 0, SEEK_END));
	long n = ftell(f);
	CHECK(n > 0 && !fseek(f, 0, SEEK_SET));
	uint8_t *blob = alloc((size_t)n);
	CHECK(fread(blob, 1, (size_t)n, f) == (size_t)n);
	CHECK(!fclose(f));
	const struct a9t_tables *t = NULL;
	CHECK(a9e_wf_check(blob, (size_t)n, &t) == 0);
	memcpy(&tables, t, sizeof(tables));
	free(blob);
}

int main(int argc, char **argv)
{
	CHECK(argc == 1 || argc == 2);
	actual_tables = argc == 2;
	if (argc == 2) load_tables(argv[1]); else make_tables();
	printf("Waveform fixture: %s\n", argc == 2 ? argv[1] : "synthetic");
	one_pixel(513, 1);
	one_pixel(518, 1);
	one_pixel(514, 1);
	one_pixel(0x303, 1); /* Clear's bit-8 DitherSStyle16 path */
	one_pixel(521, 0);
	one_pixel(515, 0);
	one_pixel(520, 0);
	sentinel();
	duplicates();
	printf("target regression: %u assertions passed\n", checks);
	return 0;
}
