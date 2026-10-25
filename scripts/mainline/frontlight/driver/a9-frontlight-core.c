// SPDX-License-Identifier: GPL-2.0-only
/* Hisense A9 stock-derived profiles; see docs/mainline-frontlight.md. */
#ifdef __KERNEL__
#include <linux/array_size.h>
#include <linux/errno.h>
#else
#include <errno.h>
#define ARRAY_SIZE(array) (sizeof(array) / sizeof((array)[0]))
#endif
#include "a9-frontlight-core.h"

struct a9_fl_reg {
	unsigned int reg;
	unsigned int val;
};

/* Standby first; safety settings and zero ratio before any backlight enable. */
static const struct a9_fl_reg aw_init[] = {
	{ 0x02, 0x14 }, /* PWM disabled, linear, standby */
	{ 0x03, 0x01 }, /* 4.8 mA full scale, CH1 only */
	{ 0x04, 0x33 }, /* stock OVP code 4, 1 MHz, OCP code 3 */
	{ 0x05, 0x40 }, /* stock 10 uH inductor selection */
	{ 0x06, 0x00 }, { 0x07, 0x00 },
	{ 0x09, 0x44 }, { 0x0a, 0x00 },
};

static const struct a9_fl_reg ktd_init[] = {
	{ 0x02, 0x00 }, /* 5 mA full scale, backlight/flash disabled */
	{ 0x03, 0x62 }, /* stock OVP 32 V, 1 MHz, 1.8 A, linear */
	{ 0x06, 0x9f }, /* PWM disabled; reset hysteresis and CH1..3 mask */
	{ 0x04, 0x00 }, { 0x05, 0x00 },
	{ 0x07, 0x00 }, { 0x08, 0x00 },
};

static int a9_fl_id(struct a9_fl *fl)
{
	int id = fl->io.read(fl->io.ctx, 0x00);

	if (id < 0)
		return id;
	if (fl->kind == A9_FL_AW99703)
		return id == 0x03 ? 0 : -ENODEV;
	/* KTD3137 device ID bits [5:3]; revision/reserved bits are ignored. */
	return (id & 0x38) == 0x18 ? 0 : -ENODEV;
}

int a9_fl_adopt(struct a9_fl *fl, int enabled)
{
	int r[11] = { 0 };
	unsigned int i, lsb, msb;
	int ret;

	fl->level = 0;
	fl->configured = 0;
	fl->powered = !!enabled;
	if (!enabled)
		return 0; /* Do not power up a dark chip just to identify it. */
	ret = a9_fl_id(fl);
	if (ret)
		return ret;
	for (i = 2; i <= 7; i++) {
		r[i] = fl->io.read(fl->io.ctx, i);
		if (r[i] < 0)
			return r[i];
	}
	if (fl->kind == A9_FL_AW99703) {
		if ((r[2] & 3) == 0)
			return 0; /* Standby: defer initialization to first nonzero. */
		if ((r[2] & 0x17) != 0x15 || r[3] != 0x01 ||
		    (r[4] & 0x3f) != 0x33 || !(r[5] & 0x40))
			return -EBUSY;
		lsb = 6;
		msb = 7;
	} else {
		if (!(r[2] & 3))
			return 0;
		if (r[2] != 0x01 || r[3] != 0x62 ||
		    !(r[6] & 0x80) || !(r[6] & 7))
			return -EBUSY;
		lsb = 4;
		msb = 5;
	}
	fl->level = ((unsigned int)r[msb] << 3) | (r[lsb] & 7);
	fl->configured = 1;
	return 0;
}

static int a9_fl_fail_off(struct a9_fl *fl, int error)
{
	/* HWEN is the fail-safe if the bus is dead or a ratio write was partial. */
	int ret = fl->io.enable(fl->io.ctx, 0);

	fl->configured = 0;
	if (!ret) {
		fl->powered = 0;
		fl->level = 0;
	}
	return error ? error : ret;
}

static int a9_fl_configure(struct a9_fl *fl)
{
	const struct a9_fl_reg *seq;
	unsigned int i, count;
	int ret;

	if (!fl->powered) {
		ret = fl->io.enable(fl->io.ctx, 1);
		if (ret)
			return ret;
		fl->powered = 1;
		fl->io.settle(fl->io.ctx);
	}
	ret = a9_fl_id(fl);
	if (ret)
		return a9_fl_fail_off(fl, ret);
	if (fl->kind == A9_FL_AW99703) {
		seq = aw_init;
		count = ARRAY_SIZE(aw_init);
	} else {
		seq = ktd_init;
		count = ARRAY_SIZE(ktd_init);
	}
	for (i = 0; i < count; i++) {
		ret = fl->io.write(fl->io.ctx, seq[i].reg, seq[i].val);
		if (ret)
			return a9_fl_fail_off(fl, ret);
	}
	fl->configured = 1;
	return 0;
}

int a9_fl_set(struct a9_fl *fl, unsigned int level)
{
	unsigned int lsb = fl->kind == A9_FL_AW99703 ? 6 : 4;
	unsigned int standby = fl->kind == A9_FL_AW99703 ? 0x14 : 0;
	int ret;

	if (level > A9_FL_MAX)
		return -EINVAL;
	if (!level) {
		if (!fl->powered)
			return 0;
		/* Disable both backlight and flash, then HWEN low (also on error). */
		ret = fl->io.write(fl->io.ctx, 2, standby);
		return a9_fl_fail_off(fl, ret);
	}
	if (!fl->configured) {
		ret = a9_fl_configure(fl);
		if (ret)
			return ret;
	}
	/* Always LSB then MSB; KTD3137 latches both on the MSB write. */
	ret = fl->io.write(fl->io.ctx, lsb, level & 7);
	if (ret)
		return a9_fl_fail_off(fl, ret);
	ret = fl->io.write(fl->io.ctx, lsb + 1, level >> 3);
	if (ret)
		return a9_fl_fail_off(fl, ret);
	ret = fl->io.write(fl->io.ctx, 2, standby | 1);
	if (ret)
		return a9_fl_fail_off(fl, ret);
	fl->level = level;
	return 0;
}
