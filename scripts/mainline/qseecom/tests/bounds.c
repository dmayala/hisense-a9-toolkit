/* SPDX-License-Identifier: GPL-2.0-only */
#include <stdbool.h>
#include <stdint.h>
#include <assert.h>
#include <stdio.h>
#include "../driver/a9-qseecom-bounds.h"
int main(void)
{
	__u32 off;
	assert(a9q_load_extent(7260, 3894274, 64U << 20, &off) && off == 3901534);
	assert(a9q_load_extent(4096, (64U << 20) - 4096, 64U << 20, &off) &&
	       off == (64U << 20));
	assert(!a9q_load_extent(4096, 64U << 20, 64U << 20, &off));
	assert(!a9q_load_extent(0, 4096, 64U << 20, &off));
	assert(!a9q_load_extent(4097, 4096, 64U << 20, &off));
	assert(!a9q_load_extent(UINT32_MAX, UINT32_MAX, UINT32_MAX, &off));
	assert(a9q_range(0x12340000, 0x1000, 0x12340fc0, 0x40, &off) &&
	       off == 0xfc0);
	assert(!a9q_range(0x12340000, 0x1000, 0x12341000, 1, &off));
	assert(!a9q_range(0x12340000, 0x1000, 0x1233ffff, 1, &off));
	assert(!a9q_range(0x12340000, 0x1000, 0x12340000, 0, &off));
	assert(!a9q_range(UINT64_MAX - 15, 32, UINT64_MAX - 10, 1, &off));
	assert(!a9q_range(0x12340000, 32, 0x12340010, UINT32_MAX, &off));
	assert(a9q_fd_offset(4096, 128, 64, 60, false, &off) && off == 188);
	assert(!a9q_fd_offset(4096, 128, 64, 60, true, &off));
	assert(a9q_fd_offset(4096, 128, 64, 56, true, &off) && off == 184);
	assert(a9q_fd_offset(4096, 1, 64, 3, true, &off) && off == 4);
	assert(!a9q_fd_offset(4096, 4097, 0, 0, false, &off));
	assert(!a9q_fd_offset(4096, 1, UINT32_MAX, 0, false, &off));
	assert(!a9q_fd_offset(4096, 128, 64, UINT32_MAX, true, &off));
	assert(!a9q_fd_offset(UINT32_MAX, 0, UINT32_MAX, 1U << 30, true, &off));
	puts("PASS: LOAD extent, command/reply and modified-fd bounds, overflow/end/width cases");
	return 0;
}
