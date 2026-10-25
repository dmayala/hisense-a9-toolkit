/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef PMI632_A9_FV_H
#define PMI632_A9_FV_H

#ifdef __KERNEL__
#include <linux/errno.h>
#else
#include <errno.h>
#endif

#define A9_STOCK_FV_REG 0x1070
#define A9_STOCK_FV_RAW 0x55 /* 3.6 V + 85 * 10 mV = 4.450 V */
#define A9_BOOT_FV_RAW 0x5a /* Observed UEFI / initial stock Linux value */

struct a9_fv_io {
	void *context;
	int (*read)(void *context, unsigned int reg, unsigned int *value);
	int (*write)(void *context, unsigned int reg, unsigned int value);
};

/* Called once, after all probe dependencies; never from work or cleanup.
 * Exactly one write on the expected path, no retries or compensating writes.
 * Callers must latch errors instead of failing/retrying probe after this call.
 */
static inline int a9_fv_stock_once(const struct a9_fv_io *io, int enabled)
{
	unsigned int value;
	int ret;

	if (!enabled)
		return 0;
	ret = io->read(io->context, A9_STOCK_FV_REG, &value);
	if (ret)
		return ret;
	/* Never raise a lower target or reinterpret an unknown handoff setting. */
	if (value != A9_BOOT_FV_RAW && value != A9_STOCK_FV_RAW)
		return -ERANGE;
	ret = io->write(io->context, A9_STOCK_FV_REG, A9_STOCK_FV_RAW);
	if (ret)
		return ret;
	ret = io->read(io->context, A9_STOCK_FV_REG, &value);
	if (ret)
		return ret;
	return value == A9_STOCK_FV_RAW ? 0 : -EIO;
}

#endif
