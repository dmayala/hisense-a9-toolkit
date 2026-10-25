/* SPDX-License-Identifier: GPL-2.0-only */
/* Actual engine + TCON manual-clear request/frames; no device access. */
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

/* Independent core path: build the stock request, arrays and full target,
 * then compare every complete drive frame with the prepared engine path. */
static unsigned core_compare(struct a9e_engine *e, unsigned mode, int grey)
{
	struct a9e_engine r;
	struct a9t_request req;
	uint8_t *out = alloc(A9T_FRAME_BYTES), *ref = alloc(A9T_FRAME_BYTES);
	unsigned n = 0;
	int clear = 1, reset = 0, more, expected;
	create(&r);
	history(&r, mode, grey);
	memset(&req, 0, sizeof(req));
	unsigned effective = a9t_request_build(&req, mode, mode, 1, &clear, &reset);
	a9t_request_arrays(&req, r.old, r.arr);
	a9t_condition(&r.st, r.grey, effective, r.nlv, r.work);
	a9t_pack(r.old, r.nlv, effective, clear, r.pairs);
	CHECK(!memcmp(e->nlv, r.nlv, A9T_N));
	CHECK(!memcmp(e->pairs, r.pairs, A9T_N * sizeof(*e->pairs)));
	a9t_update_begin(&r.st);
	a9e_start(e, 10);
	do {
		more = a9e_frame(e, out);
		expected = a9t_update_frame(&r.st, &req, r.arr,
			(unsigned)a9t_final_type(effective, clear), 10, r.pairs, ref);
		CHECK(more == expected);
		CHECK(!memcmp(out, ref, A9T_FRAME_BYTES));
		CHECK(++n < 500);
	} while (more);
	CHECK(a9e_frame_is_null(out));
	CHECK(e->nframes == n && e->bin == 10);
	free(out); free(ref); destroy(&r);
	return n;
}

static void manual(unsigned mode)
{
	struct a9e_engine e;
	struct a9e_rect raw = {20, 20, 21, 21};
	create(&e);
	history(&e, mode, 128);
	/* The exact same target must still execute the explicit manual clear. */
	CHECK(a9e_prepare(&e, mode, 1, &raw, 1) == (mode == 513 ? 0 : 5));
	CHECK(e.mode == mode && e.clear && !e.unchanged && !e.mask);
	CHECK(e.req.kind == 2);
	CHECK(e.rect.x1 == 0 && e.rect.y1 == 0 && e.rect.x2 == A9T_W && e.rect.y2 == A9T_H);
	CHECK(e.type == (mode == 513 ? 2u : mode & 255u));
	if (mode != 513) {
		for (unsigned k = 0; k < 5; ++k)
			CHECK(e.req.type[k] == (mode & 255u));
		CHECK(e.req.level[0] == 15 && e.req.level[1] == 0 &&
		      e.req.level[2] == 0 && e.req.level[3] == 0 && e.req.level[4] == 15);
	}
	unsigned frames = core_compare(&e, mode, 128);
	if (actual_tables)
		CHECK(frames == (mode == 513 ? 30u : mode == 518 ? 48u : 42u));
	else
		CHECK(frames == (mode == 513 ? 4u : 24u));
	a9e_end(&e);
	CHECK(e.pre_mode == mode && e.have_pre);
	CHECK(!memcmp(e.old, e.nlv, A9T_N));
	CHECK(a9e_prepare(&e, mode, 0, &raw, 0) == 0);
	CHECK(e.unchanged && !e.req.kind && !e.req.count && !e.clear);
	/* The next changed ordinary target requires no mode-entry compensation. */
	memset(e.grey, 0, A9T_N);
	CHECK(a9e_prepare(&e, mode, 0, &raw, 0) == 0);
	CHECK(!e.unchanged && !e.req.kind && !e.req.count && e.mode == mode);
	printf("PASS manual %u: %u complete frames equal core, final mode retained; subsequent ordinary target has no entry sequence\n", mode, frames);
	destroy(&e);
}

static void recovery(unsigned from)
{
	struct a9e_engine e;
	struct a9e_rect raw = {20, 20, 21, 21};
	create(&e);
	history(&e, from, 128);
	a9e_prepare(&e, from, 1, &raw, 0);
	a9e_start(&e, 10);
	uint8_t *out = alloc(A9T_FRAME_BYTES);
	CHECK(a9e_frame(&e, out));
	a9e_abort(&e); /* caller now owes full GC16; history is not committed */
	CHECK(!e.unchanged && e.have_pre && e.pre_mode == from);
	a9e_prepare(&e, 515, 1, &raw, 1);
	CHECK(e.mode == 515 && e.clear && !e.unchanged && !e.mask && e.type == 2);
	CHECK(e.rect.x1 == 0 && e.rect.y1 == 0 && e.rect.x2 == A9T_W && e.rect.y2 == A9T_H);
	free(out); destroy(&e);
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
	if (actual_tables) load_tables(argv[1]); else make_tables();
	printf("Clear waveform fixture: %s\n", actual_tables ? argv[1] : "synthetic");
	const unsigned modes[] = {513, 518, 521};
	for (unsigned k = 0; k < 3; ++k) { manual(modes[k]); recovery(modes[k]); }
	printf("manual clear engine: %u assertions passed\n", checks);
	return 0;
}
