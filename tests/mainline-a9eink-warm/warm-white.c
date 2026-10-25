/* Offline test: why a warmth change washes the whole panel out (eink-trial2, 2026-10-17).
 * The framework Night Display matrix measured in logcat at the warm end (2596 K slider max):
 * diag(1.000000, 0.775627, 0.562570). SurfaceFlinger applies it to the client-composed frame,
 * a9_eink converts RGB to grey with the stock luma (76,150,30)>>8 and the Smooth/Speed binary
 * dither maps grey through g2[] built from the default controls (30,30,0). Real production
 * code (a9tcon.c) is linked; nothing here is modelled except the matrix multiplication. */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include "a9tcon.h"

static int fails;
#define CHECK(c, ...) do { if (!(c)) { fails++; printf("FAIL " __VA_ARGS__); printf("\n"); } else { printf("ok   " __VA_ARGS__); printf("\n"); } } while (0)

/* grey of a flat RGB colour through the production RGBA->grey path */
static int grey_of(uint8_t r, uint8_t g, uint8_t b)
{
	uint8_t *src = malloc(A9T_SRC_BYTES), *grey = malloc(A9T_N);
	for (size_t i = 0; i < (size_t)A9T_SRC_STRIDE * A9T_SRC_ROWS; i++) {
		src[4 * i] = r; src[4 * i + 1] = g; src[4 * i + 2] = b; src[4 * i + 3] = 255;
	}
	a9t_rgba_to_grey(src, grey);
	int v = grey[12345];
	free(src); free(grey);
	return v;
}
static uint8_t clampb(double x) { return x < 0 ? 0 : x > 255 ? 255 : (uint8_t)(x + 0.5); }

int main(void)
{
	struct a9t_state s;
	static uint8_t cache[A9T_FRAME_BYTES];
	a9t_init(&s, NULL, cache);	/* controls 30/30/0: the kernel defaults */

	/* identity (night display off or cool end ~0.9996/0.9991): white stays white */
	int white_id = grey_of(clampb(255 * 1.0), clampb(255 * 0.999557), clampb(255 * 0.999135));
	CHECK(white_id >= 245 && s.g2[white_id] == 255, "cool end (max coefficients): luma %d -> g2 %d (pure white)", white_id, s.g2[white_id]);

	/* warm end: the measured matrix */
	int white_warm = grey_of(255, clampb(255 * 0.775627), clampb(255 * 0.562570));
	CHECK(white_warm == 208, "warm end: white luma %d (expected 208 of 255)", white_warm);
	CHECK(s.g2[white_warm] > 120 && s.g2[white_warm] < 200,
	      "warm end: g2[%d] = %d: white lands in the dither ramp (about 60 %% grey), not 255", white_warm, s.g2[white_warm]);
	/* Clear (515) uses g16, linear at contrast 0: a clean light grey, no speckle */
	CHECK(s.g16[white_warm] == white_warm, "warm end in Clear: g16 keeps %d (clean grey, no dither)", s.g16[white_warm]);

	/* black text does not move: contrast loss is all in the white */
	CHECK(s.g2[grey_of(33, 33, 36)] <= 4, "dark text luma stays black through g2 (%d)", s.g2[grey_of(33, 33, 36)]);

	/* how much warmth survives: flat G=B=k, smallest k that still gives pure white */
	double kmin = -1;
	for (int i = 0; i <= 1000; i++) {
		double k = i / 1000.0;
		if (s.g2[grey_of(255, clampb(255 * k), clampb(255 * k))] == 255) { kmin = k; break; }
	}
	printf("     smallest G=B coefficient that still gives pure white: %.3f (warm end is 0.776/0.563)\n", kmin);
	CHECK(kmin > 0.93 && kmin < 0.96, "pure white needs G=B >= %.3f: only the last few %% of the warmth slider keep white white", kmin);

	puts(fails ? "warm-white: FAILED" : "warm-white: PASS");
	return fails != 0;
}
