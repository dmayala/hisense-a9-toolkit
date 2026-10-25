/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "a9_eink_pattern.h"

static void rectangle(uint8_t *portrait, unsigned int x1, unsigned int y1,
		      unsigned int x2, unsigned int y2, unsigned int value)
{
	unsigned int x, y;

	for (y = y1; y < y2; y++)
		for (x = x1; x < x2; x++)
			portrait[y * A9E_FB_W + x] = value;
}

int main(void)
{
	uint8_t *allocation = malloc(A9T_N + 2);
	uint8_t *portrait = malloc(A9T_N);
	uint8_t *second = malloc(A9T_N);
	uint8_t *grey;
	unsigned int n, row, col;
	size_t ramp_pixels[16] = { 0 };

	assert(allocation && portrait && second);
	grey = allocation + 1;
	memset(allocation, 0xa5, A9T_N + 2);
	memset(portrait, 0xa5, A9T_N);

	/* Independent portrait reference, then compare after rotation. */
	rectangle(portrait, 0, 0, 412, 824, 0);
	rectangle(portrait, 412, 0, 824, 824, 255);
	for (n = 0; n < 16; n++) {
		unsigned int x1 = (n * 824 + 15) / 16;
		unsigned int x2 = ((n + 1) * 824 + 15) / 16;

		rectangle(portrait, x1, 824, x2, 1236, n * 17);
	}
	rectangle(portrait, 0, 1236, 275, 1648, 0);
	rectangle(portrait, 275, 1236, 549, 1648, 27);
	rectangle(portrait, 549, 1236, 824, 1648, 255);
	a9e_pattern_contrast(grey);
	for (row = 0; row < A9E_FB_H; row++) {
		for (col = 0; col < A9E_FB_W; col++) {
			size_t landscape = (823 - col) * A9T_W + row;
			uint8_t value = grey[landscape];

			assert(value == portrait[row * A9E_FB_W + col]);
			if (row >= 824 && row < 1236) {
				assert(value % 17 == 0);
				ramp_pixels[value >> 4]++;
			}
		}
	}
	assert(allocation[0] == 0xa5 && allocation[A9T_N + 1] == 0xa5);
	puts("PASS: exact pixels, full coverage, portrait rotation and buffer bounds");
	for (n = 0; n < 16; n++)
		assert(ramp_pixels[n] == (n % 2 == 0 ? 52u : 51u) * 412);
	assert((grey[(823 - 275) * A9T_W + 1236] >> 4) == 1);
	assert((grey[(823 - 274) * A9T_W + 1236] >> 4) == 0);
	assert((grey[(823 - 549) * A9T_W + 1236] >> 4) == 15);
	puts("PASS: all 16 Clear levels and explicit grey-27/level-1 comparison");
	memset(second, 0x5a, A9T_N);
	a9e_pattern_contrast(second);
	assert(memcmp(grey, second, A9T_N) == 0);
	puts("PASS: deterministic output independent of previous framebuffer");
	free(second);
	free(portrait);
	free(allocation);
	return 0;
}
