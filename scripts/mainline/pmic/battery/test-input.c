/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include <stdio.h>
#include "input-policy.h"

int main(void)
{
	/* Stock A9 DT and update_sw_icl_max, not generic upstream SMB5 values. */
	assert(stock_input_current(1, 1, 500000, 2000000, 900000) == 500000);
	assert(stock_input_current(4, 1, 500000, 2000000, 900000) == 1500000);
	assert(stock_input_current(8, 1, 500000, 2000000, 900000) == 2000000);
	assert(stock_input_current(16, 1, 500000, 2000000, 900000) == 900000);
	/* USB reset, configure and suspend votes; CDP is independent of suspend. */
	assert(stock_input_current(1, 1, 100000, 2000000, 900000) == 100000);
	assert(stock_input_current(1, 1, 2000, 2000000, 900000) == 0);
	assert(stock_input_current(4, 1, 2000, 2000000, 900000) == 1500000);
	/* Exact four USB51 encodings in stock set_sdp_current. */
	assert(stock_usb51_options(100000) == 0);
	assert(stock_usb51_options(150000) == 4);
	assert(stock_usb51_options(500000) == 2);
	assert(stock_usb51_options(900000) == 6);
	/* An unfinished APSD or unsupported QC result cannot select DCP's limit. */
	assert(stock_input_current(8, 0, 500000, 2000000, 900000) == 100000);
	assert(stock_input_current(0x28, 1, 500000, 2000000, 900000) == 100000);
	/* Board limits remain inputs, rather than a permanently inherited selector. */
	assert(stock_input_current(8, 1, 500000, 1500000, 700000) == 1500000);
	assert(stock_input_current(16, 1, 500000, 1500000, 700000) == 700000);
	puts("PASS: stock source limits, USB votes/encodings, APSD and board limits");
	return 0;
}
