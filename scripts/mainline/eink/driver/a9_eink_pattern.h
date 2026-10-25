/* SPDX-License-Identifier: GPL-2.0-only */
/* Diagnostic input only: the caller uses the normal engine/transport path. */
#ifndef A9_EINK_PATTERN_H
#define A9_EINK_PATTERN_H

#include "a9_eink_engine.h"

/*
 * Portrait coordinates (824 columns x 1648 rows), half-open ranges:
 * rows    0..824: columns 0..412 = grey 0, 412..824 = grey 255.
 * rows  824..1236: sixteen bands, grey 17 * floor(column * 16 / 824).
 * rows 1236..1648: columns 0..275 = 0, 275..549 = 27, 549..824 = 255.
 *
 * With Clear's stock contrast-0 grey >> 4 quantisation, the references target levels
 * 0 and 15; the final middle patch targets level 1. The ramp targets every
 * level 0..15 exactly. Physical black depth remains a waveform/HV question.
 * The buffer is the engine's LANDSCAPE grey array, not a portrait framebuffer.
 */
static inline void a9e_pattern_contrast(uint8_t *grey)
{
	unsigned int x, y;

	for (y = 0; y < A9T_H; y++) {
		unsigned int col = A9E_FB_W - 1 - y;

		for (x = 0; x < A9T_W; x++) {
			uint8_t value;

			if (x < 824)
				value = col < 412 ? 0 : 255;
			else if (x < 1236)
				value = (col * 16 / A9E_FB_W) * 17;
			else
				value = col < 275 ? 0 : col < 549 ? 27 : 255;
			grey[y * A9T_W + x] = value;
		}
	}
}

#endif /* A9_EINK_PATTERN_H */
