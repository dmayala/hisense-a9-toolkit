/* SPDX-License-Identifier: GPL-2.0-only */
#include "profile.h"

/* Same 0..10 / 10.1..45 / 45.1..53 C bands as stock DTBO. */
static inline void stock_jeita(int dc, int *fcc, int *fv)
{
	*fv = dc > 450 ? 4100000 : 4450000;
	*fcc = dc < 0 || dc > 530 ? 0 :
		dc <= 100 ? 800000 : dc <= 450 ? 3000000 : 1200000;
}

static inline int profile_at_temp(const int *values, int dc)
{
	unsigned int col;

	if (dc <= profile_temp[0] * 10)
		return values[0];
	for (col = 1; col < PROFILE_COLS; col++) {
		int lo = profile_temp[col - 1] * 10;
		int hi = profile_temp[col] * 10;

		if (dc <= hi)
			return values[col - 1] +
				(values[col] - values[col - 1]) * (dc - lo) / (hi - lo);
	}
	return values[PROFILE_COLS - 1];
}

/* Normal runtime SOC is integrated current. Drain separately uses this table
 * as a conservative loaded-voltage floor, not as the Android runtime SOC. */
static inline int profile_seed(int uv, int dc)
{
	unsigned int row;
	int high = profile_at_temp(profile_ocv[0], dc);

	if (uv >= high)
		return 10000;
	for (row = 1; row < sizeof(profile_soc) / sizeof(profile_soc[0]); row++) {
		int low = profile_at_temp(profile_ocv[row], dc);

		if (uv >= low)
			return profile_soc[row] + (uv - low) *
				(profile_soc[row - 1] - profile_soc[row]) / (high - low);
		high = low;
	}
	return 0;
}

/* Microamp * millisecond; retain sub-uAh remainder in the accumulator. */
static inline long long sampled_charge_delta(int previous, int ua_now, long long ms)
{
	return ((long long)previous + ua_now) * ms / 2;
}
