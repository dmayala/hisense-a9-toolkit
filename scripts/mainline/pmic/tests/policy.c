// SPDX-License-Identifier: GPL-2.0-only
#include <assert.h>
#include <stdio.h>
#include "../pmi632-policy.h"

int main(void)
{
	struct a9_sample good = { .uv = 3800000, .temp_dc = 250,
		.vbus_uv = 5000000, .usb = 0x10, .qg = 0x81,
		.apsd = 1, .source = 8 };
	struct a9_sample s;
	unsigned int i, field;
	int dc, prev = 10000, v;

	/* Stock OV qualification: sticky BAT_OV alone is not a health alarm. */
	s = good; s.status2 = 0x02; s.fv_raw = 0x55;
	s.uv = 4469644; assert(!a9_battery_overvoltage(&s));
	s.uv = 4448428; assert(!a9_battery_overvoltage(&s));
	s.uv = 4448623; assert(!a9_battery_overvoltage(&s));
	s.uv = 4489999; assert(!a9_battery_overvoltage(&s));
	s.uv = 4490000; assert(a9_battery_overvoltage(&s));
	s.uv = 4500000; assert(a9_battery_overvoltage(&s));
	s.status2 = 0; assert(!a9_battery_overvoltage(&s));
	s.status2 = 0x02; s.fv_raw = 0x32; /* Control mode's lower FV. */
	s.uv = 4139999; assert(!a9_battery_overvoltage(&s));
	s.uv = 4140000; assert(a9_battery_overvoltage(&s));
	s.fv_raw = 0x5a; s.uv = 4490000;
	assert(a9_battery_overvoltage(&s)); /* No higher alarm limit for bad FV. */

	/* Observe status comes from firmware state, not the driver's enable flag. */
	s = good; s.enabled = 1; s.status1 = 3; s.ua = 500000;
	s.powerpath = 0x11; s.fv_raw = 0x55; s.fcc_raw = 0x3c;
	assert(a9_observed_charging(&s));
	assert(a9_observed_online(&s));
	assert(a9_observed_limits_ok(&s));
	s.fv_raw = 0x5a; assert(!a9_observed_limits_ok(&s));
	s.fv_raw = 0x55; s.fcc_raw = 61; assert(!a9_observed_limits_ok(&s));
	s.suspend = 1;
	assert(!a9_observed_charging(&s) && !a9_observed_online(&s));
	s.suspend = 0; s.enabled = 0;
	assert(!a9_observed_charging(&s) && a9_observed_online(&s));
	s.enabled = 1; s.ua = -100000; assert(!a9_observed_charging(&s));
	s.ua = 100000; s.status1 = 5; assert(!a9_observed_charging(&s));
	s.powerpath = 0x51; assert(!a9_observed_online(&s));

	/* All stock table knots and interpolation monotonicity over the domain. */
	for (i = 0; i < sizeof(a9_therm_mv) / sizeof(a9_therm_mv[0]); i++) {
		assert(!a9_temp_dc(a9_therm_mv[i] * 1000, &dc));
		assert(dc == -400 + (int)i * 20);
	}
	for (v = 65000; v <= 1663000; v++) {
		assert(!a9_temp_dc(v, &dc));
		assert(dc <= prev);
		prev = dc;
	}
	assert(a9_temp_dc(0, &dc));
	assert(a9_temp_dc(-1, &dc));
	assert(a9_temp_dc(1875000, &dc));
	assert(a9_temp_dc(1663001, &dc));
	assert(a9_temp_dc(64999, &dc));
	assert(a9_hardware_safe(&good));
	s = good; s.source = 4; assert(a9_hardware_safe(&s));
	/* Test all source combinations: only unambiguous CDP and DCP qualify. */
	for (i = 0; i < 128; i++) {
		s = good; s.source = i;
		assert(!!a9_hardware_safe(&s) == (i == 4 || i == 8));
	}
	/* Every known thermal/fault bit must independently veto charging. */
	for (i = 0; i < 8; i++) {
		for (field = 0; field < 4; field++) {
			s = good;
			if (field == 0) s.status2 = 1U << i;
			if (field == 1) s.die = 1U << i;
			if (field == 2) s.conn = 1U << i;
			if (field == 3) s.status7 = 1U << i;
			if (field != 3 || i < 6) assert(!a9_hardware_safe(&s));
		}
	}
	for (i = 0; i < 4; i++) {
		s = good; s.usb |= 1U << i; assert(!a9_hardware_safe(&s));
	}
	s = good; s.usb = 0; assert(!a9_hardware_safe(&s));
	s = good; s.batif = 0x10; assert(!a9_hardware_safe(&s));
	s = good; s.batif = 0x20; assert(!a9_hardware_safe(&s));
	s = good; s.qg = 0x80; assert(!a9_hardware_safe(&s));
	s = good; s.qg = 1; assert(!a9_hardware_safe(&s));
	s = good; s.otg = 1; assert(!a9_hardware_safe(&s));
	s = good; s.qgmode = 0x80; assert(!a9_hardware_safe(&s));
	s = good; s.apsd = 0; assert(!a9_hardware_safe(&s));
	assert(!a9_charge_window(3350000, 250, 5000000));
	assert(a9_charge_window(3350001, 250, 5000000));
	assert(a9_charge_window(A9_FV_UV - 1, 250, 5000000));
	assert(!a9_charge_window(A9_FV_UV, 250, 5000000));
	assert(!a9_charge_window(3800000, A9_COLD_DC, 5000000));
	assert(a9_charge_window(3800000, A9_COLD_DC + 1, 5000000));
	assert(a9_charge_window(3800000, A9_HOT_DC - 1, 5000000));
	assert(!a9_charge_window(3800000, A9_HOT_DC, 5000000));
	assert(!a9_charge_window(3800000, 250, 4499999));
	assert(a9_charge_window(3800000, 250, 4500000));
	assert(a9_charge_window(3800000, 250, 5500000));
	assert(!a9_charge_window(3800000, 250, 5500001));
	assert(A9_FV_UV <= 4100000 && A9_FV_UV <= 4450000);
	assert(A9_FCC_UA <= 800000 && A9_FCC_UA <= 1200000);
	assert(A9_ICL_UA <= 900000 && A9_ICL_UA <= 2000000);
	prev = 0;
	for (v = 2000000; v <= 5000000; v++) {
		int soc = a9_voltage_capacity(v);
		assert(soc >= prev && soc >= 0 && soc <= 100);
		prev = soc;
	}
	assert(a9_voltage_capacity(3350000) == 0);
	assert(a9_voltage_capacity(4450000) == 100);
	puts("PASS: thermistor curve, open/short, all source codes, thermal/fault vetoes, charge boundaries, SOC bounds");
}
