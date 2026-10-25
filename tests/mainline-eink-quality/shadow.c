/* SPDX-License-Identifier: GPL-2.0-only */
/* Clear UI shadow curve: deterministic, no vendor data, no oracle needed. */
#include "a9tcon.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int stock_g16(int x, int c)
{
	if (c == 0) return x;
	if (x <= 200) return x * (200 - c) / 200;
	return (x - 200) * (c + 55) / 55 + 200 - c;
}

int main(void)
{
	static struct a9t_state st;
	static struct a9t_work work;
	static uint8_t cache[A9T_FRAME_BYTES], grey[A9T_N], off[A9T_N], on[A9T_N], ref[A9T_N];
	struct a9t_tables *t = calloc(1, sizeof(*t));
	unsigned mode, n = 0;
	int c, x, prev = 0;

	assert(t);
	a9t_init(&st, t, cache);
	assert(st.clear_shadow == 0);	/* core default reproduces today's bytes */

	/* curve properties */
	for (x = 0; x < 256; x++) {
		int y = a9t_clear_ui_grey((uint8_t)x);
		assert(y >= prev && y <= x);
		prev = y;
		if (x <= 30) assert(y == 0);
		if (x >= 200) assert(y == x);
	}
	assert(a9t_clear_ui_grey(31) == 1 && a9t_clear_ui_grey(33) == 3 && a9t_clear_ui_grey(199) == 198);
	for (x = 0; x < 256; x++) {	/* every tabulated level for the C=0 default */
		assert((st.g16s[x] >> 4) == (a9t_clear_ui_grey((uint8_t)x) >> 4));
		assert(st.g16[x] == x);
	}
	for (x = 0; x <= 43; x++) assert((st.g16s[x] >> 4) == 0);	/* 0..43 -> level 0 */
	for (x = 200; x < 256; x++) assert((st.g16s[x] >> 4) == (x >> 4));
	{ int seen[16] = {0}; for (x = 0; x < 256; x++) seen[st.g16s[x] >> 4] = 1;
	  for (x = 0; x < 16; x++) assert(seen[x]); }

	/* composition with every legal contrast: shadow first, then stock G16 */
	for (c = 0; c <= 100; c++) {
		a9t_set_controls(&st, 30, 30, c);
		for (x = 0; x < 256; x++) {
			assert(st.g16[x] == stock_g16(x, c));
			assert(st.g16s[x] == stock_g16(a9t_clear_ui_grey((uint8_t)x), c));
		}
	}
	a9t_set_controls(&st, 30, 30, 0);

	/* scene: rows of text/card/photo greys, deterministic */
	{ static const uint8_t v[] = {0, 1, 2, 30, 31, 33, 43, 44, 69, 71, 128, 199, 200, 230, 235, 245, 255};
	  for (size_t i = 0; i < A9T_N; i++) grey[i] = v[(i / 7 + i / A9T_W) % sizeof(v)]; }

	for (mode = 0x200; mode <= 0x2ff; mode++) {	/* every low byte at hi=2 */
		memset(off, 0xee, sizeof(off)); memset(on, 0xdd, sizeof(on));
		a9t_set_clear_shadow(&st, 0);
		a9t_condition(&st, grey, mode, off, &work);
		a9t_set_clear_shadow(&st, 1);
		a9t_condition(&st, grey, mode, on, &work);
		if ((mode & 0xff) == 3) {
			/* plain Clear: OFF == stock g16>>4 exactly, ON == shadow g16 >> 4 */
			for (size_t i = 0; i < A9T_N; i++) {
				assert(off[i] == (grey[i] >> 4));
				assert(on[i] == (a9t_clear_ui_grey(grey[i]) >> 4));
				if (grey[i] >= 200) assert(on[i] == off[i]);
				if (grey[i] == 31 || grey[i] == 33) assert(on[i] == 0 && off[i] != 0);
			}
		} else {
			assert(!memcmp(off, on, sizeof(off)));	/* every other mode byte identical */
		}
		n++;
	}
	/* Clear with bit 8 (optional 16-level diffusion): light greys keep levels away from edges,
	 * dark text goes to level 0; OFF must be byte-stable between runs. */
	a9t_set_clear_shadow(&st, 0); a9t_condition(&st, grey, 0x303, ref, &work);
	a9t_set_clear_shadow(&st, 0); a9t_condition(&st, grey, 0x303, off, &work);
	assert(!memcmp(ref, off, sizeof(off)));
	a9t_set_clear_shadow(&st, 1); a9t_condition(&st, grey, 0x303, on, &work);
	{ size_t darker = 0; for (size_t i = 0; i < A9T_N; i++) darker += on[i] < off[i];
	  assert(darker > 0); }
	/* uniform fields, as in the evidence table */
	memset(grey, 31, sizeof(grey)); a9t_set_clear_shadow(&st, 1); a9t_condition(&st, grey, 515, on, &work);
	for (size_t i = 0; i < A9T_N; i++) assert(on[i] == 0);
	a9t_set_clear_shadow(&st, 0); a9t_condition(&st, grey, 515, off, &work);
	for (size_t i = 0; i < A9T_N; i++) assert(off[i] == 1);
	memset(grey, 33, sizeof(grey)); a9t_condition(&st, grey, 515, off, &work);
	for (size_t i = 0; i < A9T_N; i++) assert(off[i] == 2);
	a9t_set_clear_shadow(&st, 1); a9t_condition(&st, grey, 515, on, &work);
	for (size_t i = 0; i < A9T_N; i++) assert(on[i] == 0);
	memset(grey, 235, sizeof(grey)); a9t_condition(&st, grey, 515, on, &work);
	for (size_t i = 0; i < A9T_N; i++) assert(on[i] == 14);
	printf("PASS clear shadow: curve, %u mode bytes, uniform fields\n", n);
	free(t);
	return 0;
}
