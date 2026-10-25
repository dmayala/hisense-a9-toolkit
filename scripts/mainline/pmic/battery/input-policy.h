/* SPDX-License-Identifier: GPL-2.0-only */
/* Stock A9 SMB5 policy/USB51 encodings; see charging-control.md. */
static inline int stock_usb_current(int ua)
{
	if (ua <= 25000)
		return 0;
	if (ua >= 900000)
		return 900000;
	if (ua >= 500000)
		return 500000;
	if (ua >= 150000)
		return 150000;
	return 100000;
}

static inline int stock_input_current(unsigned int source, int apsd_done,
				      int usb_ua, int dcp_ua, int float_ua)
{
	if (!apsd_done)
		return 100000;
	switch (source & 0x7f) {
	case 0x01: return stock_usb_current(usb_ua);
	case 0x04: return 1500000; /* CDP */
	case 0x08: return dcp_ua;
	case 0x10: return float_ua;
	default: return 100000; /* No QC/PD negotiation in this fallback. */
	}
}

static inline unsigned int stock_usb51_options(int ua)
{
	return (ua == 150000 || ua == 900000 ? 4 : 0) |
		(ua >= 500000 ? 2 : 0);
}
