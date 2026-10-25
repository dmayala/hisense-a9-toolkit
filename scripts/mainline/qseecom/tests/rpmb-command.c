/* SPDX-License-Identifier: GPL-2.0-only */
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#include <limits.h>
#include "../driver/a9-rpmb-command.h"
int main(void)
{
	const unsigned char short_packet[] = { 1 };
	unsigned char packet[] = { 1, 1, 0, 0 };
	assert(a9_rpmb_command(NULL, 4) == 0);
	for (unsigned int size=0;size<4;size++)
		assert(a9_rpmb_command(short_packet, size) == 0);
	for (unsigned int low=0;low<5;low++) {
		packet[0]=low;
		assert(a9_rpmb_command(packet, 4) ==
		       (low >= 1 && low <= 3 ? low : 0));
	}
	packet[0]=1;packet[3]=1;
	assert(a9_rpmb_command(packet, 4) == 0);
	/* The getter never reads a null/short result, and clears its output. */
	int result = 99;
	assert(!a9_rpmb_operation_result(NULL, 8, &result) && result == 0);
	for (unsigned int size=0;size<8;size++) {
		result = 99;
		assert(!a9_rpmb_operation_result(short_packet, size, &result));
		assert(result == 0);
	}
	assert(!a9_rpmb_operation_result(NULL, 8, NULL));
	_Static_assert(sizeof(int) == 4, "signed32 result ABI");
	const unsigned char responses[][8] = {
		{2,1,0,0,0,0,0,0}, {2,1,0,0,255,255,255,255},
		{3,1,0,0,0,0,0,128}, {3,1,0,0,255,255,255,127},
	};
	const int expected[] = { 0, -1, INT_MIN, INT_MAX };
	for (unsigned int i=0;i<4;i++) {
		assert(a9_rpmb_operation_result(responses[i], 8, &result));
		assert(result == expected[i]);
	}
	return 0;
}
