/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef PMI632_A9_POLICY_H
#define PMI632_A9_POLICY_H

/* Fixed bring-up ceilings; all below the phone's stock DT limits. */
#define A9_FV_UV 4100000
#define A9_FCC_UA 500000
#define A9_ICL_UA 500000
#define A9_COLD_DC 100
#define A9_HOT_DC 400
#define A9_PROFILE_FV_UV 4450000
#define A9_OV_MARGIN_UV 40000

/*
 * MEASURED: adcmap_batt_therm_30k_NCP_XH103 in the phone's own Image,
 * VA 0xffffff8009731278. Millivolts at -40 C, -38 C, ... +98 C.
 * This is the 10 kohm NTC table, NOT the generic 100 kohm battery table.
 * Selection and calibration on mainline still require hardware validation.
 */
static const int a9_therm_mv[] = {
	1663, 1639, 1613, 1585, 1555, 1523, 1490, 1454, 1418, 1379,
	1339, 1298, 1257, 1214, 1170, 1127, 1083, 1039, 995, 952,
	909, 867, 826, 786, 747, 709, 672, 637, 603, 571,
	540, 510, 482, 456, 430, 406, 383, 362, 341, 322,
	304, 287, 271, 256, 242, 228, 216, 204, 193, 182,
	172, 163, 155, 146, 139, 131, 125, 118, 112, 107,
	101, 96, 91, 87, 83, 79, 75, 71, 68, 65,
};

/* Fail on open/short/out-of-table values rather than clamping to room temp. */
static inline int a9_temp_dc(int uv, int *dc)
{
	unsigned int i;
	int hi, lo;

	if (uv > a9_therm_mv[0] * 1000 || uv < 65000)
		return -1;
	for (i = 1; i < sizeof(a9_therm_mv) / sizeof(a9_therm_mv[0]); i++) {
		hi = a9_therm_mv[i - 1] * 1000;
		lo = a9_therm_mv[i] * 1000;
		if (uv >= lo) {
			*dc = -400 + (int)(i - 1) * 20 +
				(hi - uv) * 20 / (hi - lo);
			return 0;
		}
	}
	return -1;
}

struct a9_sample {
	int uv, ua, temp_dc, vbus_uv;
	unsigned int usb, batif, qg, status1, status2, status7, die, conn, otg;
	unsigned int apsd, source, qgmode;
	unsigned int enabled, suspend, powerpath, fv_raw, fcc_raw, icl_raw;
	int temp_valid;
};

static inline int a9_battery_overvoltage(const struct a9_sample *s)
{
	int fv_uv = 3600000 + (int)s->fv_raw * 10000;

	/* Stock smblib_get_prop_batt_health qualifies BAT_OV with FV + 40 mV.
	 * Never raise this threshold above the A9 stock profile for bad firmware FV.
	 */
	if (fv_uv > A9_PROFILE_FV_UV)
		fv_uv = A9_PROFILE_FV_UV;
	return (s->status2 & 0x02) && s->uv >= fv_uv + A9_OV_MARGIN_UV;
}

static inline int a9_observed_limits_ok(const struct a9_sample *s)
{
	/* Read-only warning against the A9 normal-temperature stock profile. */
	return s->fv_raw <= 85 && s->fcc_raw <= 60;
}

static inline int a9_observed_online(const struct a9_sample *s)
{
	/* Stock smblib_get_prop_usb_online (0xffffff8008a2e400) uses
	 * POWER_PATH_STATUS USBIN + VALID_INPUT bits, not the USB suspend
	 * selector or ICL-limit bit. A bus suspend is not a cable removal.
	 * This polling driver has no USER_VOTER/CHG_TERMINATION_VOTER override.
	 * Preserve the physical-presence check; never fabricate an attachment. */
	return (s->usb & 0x10) && (s->powerpath & 0x11) == 0x11;
}

static inline int a9_observed_charging(const struct a9_sample *s)
{
	unsigned int state = s->status1 & 7;

	return (s->usb & 0x10) && (s->enabled & 1) && !(s->suspend & 1) &&
		state >= 1 && state <= 4 && s->ua > 0;
}

static inline int a9_charge_window(int uv, int dc, int vbus_uv)
{
	/* No recovery/precharge of an exhausted cell in this prototype. */
	return uv > 3350000 && uv < A9_FV_UV &&
		dc > A9_COLD_DC && dc < A9_HOT_DC &&
		vbus_uv >= 4500000 && vbus_uv <= 5500000;
}

static inline int a9_hardware_safe(const struct a9_sample *s)
{
	/* Only BC1.2 CDP/DCP sources; never grant an unenumerated SDP 500 mA. */
	return (s->usb & 0x10) && !(s->usb & 0x0f) &&
		!(s->batif & 0x30) && (s->qg & 0x80) && (s->qg & 1) &&
		!s->status2 && !(s->status7 & 0x3f) &&
		!s->die && !s->conn && !(s->otg & 1) && !(s->qgmode & 0x80) &&
		(s->apsd & 1) && (s->source == 0x04 || s->source == 0x08) &&
		a9_charge_window(s->uv, s->temp_dc, s->vbus_uv);
}

static inline int a9_voltage_capacity(int uv)
{
	/* INFERRED terminal-voltage estimate, NOT coulomb-counted SOC/OCV. */
	if (uv <= 3350000)
		return 0;
	if (uv >= 4450000)
		return 100;
	return (uv - 3350000) / 11000;
}

#endif
