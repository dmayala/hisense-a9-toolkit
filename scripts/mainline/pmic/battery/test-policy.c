/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include <stdio.h>
#include "policy.h"

int main(void)
{
	int i, fcc, fv;
	const int temps[] = {-1, 0, 100, 101, 450, 451, 530, 531};
	const int currents[] = {0, 800000, 800000, 3000000, 3000000, 1200000, 1200000, 0};

	/* 1: exact temperature-band boundaries from the stock DT. */
	for (i = 0; i < 8; i++) {
		stock_jeita(temps[i], &fcc, &fv);
		assert(fcc == currents[i]);
		assert(fv == (temps[i] > 450 ? 4100000 : 4450000));
	}
	/* 2: preserve measured temperature-dependent FCC instead of label 4000. */
	assert(profile_at_temp(profile_fcc, 250) == 3971000);
	assert(profile_at_temp(profile_fcc, 0) == 3954000);
	assert(profile_at_temp(profile_fcc, 500) == 3976000);
	/* 3: invert every stock OCV knot in all five temperature columns. */
	for (i = 0; i < PROFILE_COLS; i++) {
		unsigned int row;

		for (row = 0; row < sizeof(profile_soc)/sizeof(profile_soc[0]); row++)
			assert(profile_seed(profile_ocv[row][i], profile_temp[i]*10) == profile_soc[row]);
	}
	/* 4: interpolation and physical saturation of the boot estimate. */
	assert(profile_seed(5000000, 250) == 10000);
	assert(profile_seed(2000000, 250) == 0);
	assert(profile_seed((profile_ocv[0][3]+profile_ocv[1][3])/2, 250) >= 9899);
	/* 5: trapezoidal current integration: +1 A for 1h = +1 Ah; sign reversal. */
	assert(sampled_charge_delta(1000000, 1000000, 3600000) == 3600000000000LL);
	assert(sampled_charge_delta(-1000000, -1000000, 3600000) == -3600000000000LL);
	assert(sampled_charge_delta(-1000000, 1000000, 1000) == 0);
	puts("PASS: five focused stock profile/counter groups");
	return 0;
}
