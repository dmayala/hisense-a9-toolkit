/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef A9_BATTERY_STATUS_H
#define A9_BATTERY_STATUS_H

/* Ordinary USB-online branch of stock smblib_get_prop_batt_status
 * (package VA0xffffff8008a2dd80). ADC current sign is telemetry, not
 * attachment or charger state. Charging policy and fault handling are separate.
 */
static inline int a9_reported_status(const struct a9_sample *s, int fault)
{
	unsigned int state = s->status1 & 7;

	if (!a9_observed_online(s))
		return POWER_SUPPLY_STATUS_DISCHARGING;
	if (fault)
		return POWER_SUPPLY_STATUS_NOT_CHARGING;
	if (state == 0 || state == 5)
		return POWER_SUPPLY_STATUS_FULL;
	if (state >= 1 && state <= 4 && (s->status7 & 7) && (s->enabled & 1))
		return POWER_SUPPLY_STATUS_CHARGING;
	return POWER_SUPPLY_STATUS_NOT_CHARGING;
}
#endif
