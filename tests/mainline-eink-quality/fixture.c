/* SPDX-License-Identifier: GPL-2.0-only */
/* Host-only ctypes bridge to the production engine, no board/power code. */
#include "a9_eink_engine.h"
#include <stdlib.h>
#include <string.h>

struct fixture { struct a9e_engine e; struct a9t_tables t; };
static void *zalloc(size_t n) { void *p = calloc(1, n); if (!p) abort(); return p; }
void *q_new(const void *composer, size_t len, const void *template)
{
	struct fixture *q = zalloc(sizeof(*q));
	struct a9e_engine *e = &q->e;
	if (a9t_load(&q->t, composer, len)) abort();
	e->t = &q->t;
	e->grey = zalloc(A9T_N); e->old = zalloc(A9T_N); e->nlv = zalloc(A9T_N);
	e->pairs = zalloc(A9T_N * 2);
	for (int k = 0; k < 5; k++) e->arr[k] = zalloc(A9T_N * 2);
	e->work = zalloc(sizeof(*e->work)); e->cache = zalloc(A9T_FRAME_BYTES);
	e->flag_6c7 = 1;
	a9e_setup(e);
	memcpy(e->cache, template, A9T_FRAME_BYTES);
	memset(e->old, 15, A9T_N);
	e->have_pre = 1; e->pre_mode = 515;
	return q;
}
void q_free(struct fixture *q)
{
	struct a9e_engine *e = &q->e;
	free(e->grey); free(e->old); free(e->nlv); free(e->pairs);
	for (int k = 0; k < 5; k++) free(e->arr[k]);
	free(e->work); free(e->cache); free(q);
}
int q_prepare(struct fixture *q, const void *grey, unsigned mode, int clear, int tiny)
{
	struct a9e_rect r = tiny ? (struct a9e_rect){33, 240, 34, 241} : a9e_rect_full();
	memcpy(q->e.grey, grey, A9T_N);
	return a9e_prepare(&q->e, mode, clear, &r, 0);
}
const void *q_data(struct fixture *q, int field)
{
	switch (field) {
	case 0: return q->e.old;
	case 1: return q->e.nlv;
	case 2: return q->e.pairs;
	case 3: return &q->e.req;
	case 4: return &q->e.rect;
	default: return q->e.arr[field - 5];
	}
}
unsigned q_value(struct fixture *q, int field)
{
	switch (field) {
	case 0: return q->e.mode;
	case 1: return q->e.clear;
	case 2: return q->e.type;
	case 3: return q->e.unchanged;
	case 4: return q->e.pre_mode;
	default: return q->e.nframes;
	}
}
void q_start(struct fixture *q, int bin) { a9e_start(&q->e, bin); }
int q_frame(struct fixture *q, void *out) { return a9e_frame(&q->e, out); }
void q_end(struct fixture *q) { a9e_end(&q->e); }
