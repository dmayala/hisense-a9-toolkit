// SPDX-License-Identifier: GPL-2.0-only
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>

#include "a9-frontlight-core.h"

struct fake {
	enum a9_fl_kind kind;
	unsigned int regs[16];
	unsigned int writes, reads, edges, waits, calls;
	unsigned int fail_call;
	unsigned int enable_calls;
	int fail_error;
	int enabled;
	unsigned int log_reg[32], log_val[32];
};

static void reset_regs(struct fake *f)
{
	memset(f->regs, 0, sizeof(f->regs));
	if (f->kind == A9_FL_AW99703) {
		f->regs[0] = 3;
		f->regs[3] = 0x9f;
		f->regs[4] = 0x2e;
		f->regs[6] = 7;
		f->regs[7] = 255;
	} else {
		f->regs[0] = 0x18;
		f->regs[2] = 0x98;
		f->regs[3] = 0x68;
		f->regs[4] = 7;
		f->regs[5] = 255;
		f->regs[6] = 0x1f;
	}
}

static int read_reg(void *ctx, unsigned int reg)
{
	struct fake *f = ctx;

	f->reads++;
	if (++f->calls == f->fail_call)
		return f->fail_error ? f->fail_error : -EIO;
	assert(f->enabled);
	return f->regs[reg];
}

static int write_reg(void *ctx, unsigned int reg, unsigned int val)
{
	struct fake *f = ctx;

	if (++f->calls == f->fail_call)
		return -EIO;
	assert(f->enabled && reg < 16 && val < 256);
	f->log_reg[f->writes % 32] = reg;
	f->log_val[f->writes % 32] = val;
	f->writes++;
	f->regs[reg] = val;
	if (reg == 2 && (val & 3)) {
		if (f->kind == A9_FL_AW99703) {
			assert(val == 0x15);
			assert(f->regs[3] == 1);
			assert(f->regs[4] == 0x33);
			assert(f->regs[5] == 0x40);
		} else {
			assert(val == 1); /* Never flash or reset-default 20.2 mA. */
			assert(f->regs[3] == 0x62);
			assert(f->regs[6] & 0x80);
		}
	}
	return 0;
}

static int enable(void *ctx, int on)
{
	struct fake *f = ctx;

	f->enable_calls++;
	if (f->enabled != on)
		f->edges++;
	if (!f->enabled && on)
		reset_regs(f);
	f->enabled = on;
	return 0;
}

static void settle(void *ctx)
{
	((struct fake *)ctx)->waits++;
}

static struct a9_fl setup(struct fake *f, enum a9_fl_kind kind)
{
	memset(f, 0, sizeof(*f));
	f->kind = kind;
	reset_regs(f);
	return (struct a9_fl) {
		.kind = kind,
		.io = { f, read_reg, write_reg, enable, settle },
	};
}

static void test_raw(enum a9_fl_kind kind)
{
	struct fake f;
	struct a9_fl fl = setup(&f, kind);
	unsigned int b, lsb = kind == A9_FL_AW99703 ? 6 : 4;

	assert(!a9_fl_adopt(&fl, 0));
	assert(!f.reads && !f.writes && !f.edges && !fl.level);
	assert(!a9_fl_set(&fl, 0));
	assert(!f.reads && !f.writes && !f.edges);
	assert(a9_fl_set(&fl, 2048) == -EINVAL);
	assert(!f.writes && !f.edges);
	for (b = 1; b <= A9_FL_MAX; b++) {
		assert(!a9_fl_set(&fl, b));
		assert(fl.level == b);
		assert(f.regs[lsb] == (b & 7));
		assert(f.regs[lsb + 1] == (b >> 3));
		assert(f.log_reg[(f.writes - 3) % 32] == lsb);
		assert(f.log_reg[(f.writes - 2) % 32] == lsb + 1);
	}
	assert(f.edges == 1 && f.waits == 1);
	assert(!a9_fl_set(&fl, 0));
	assert(!f.enabled && !fl.level && !fl.configured);
	assert(!a9_fl_set(&fl, 11));
	assert(fl.level == 11 && f.waits == 2);
}

static void test_keep(enum a9_fl_kind kind)
{
	struct fake f;
	struct a9_fl fl = setup(&f, kind);
	unsigned int saved[16], b;

	/* Use the production cold path to establish a stock-equivalent profile. */
	assert(!a9_fl_adopt(&fl, 0));
	assert(!a9_fl_set(&fl, 1782));
	memcpy(saved, f.regs, sizeof(saved));
	f.writes = f.reads = f.edges = f.waits = 0;
	assert(!a9_fl_adopt(&fl, 1));
	assert(fl.level == 1782 && fl.configured);
	assert(!f.writes && !f.edges && !f.waits);
	assert(!memcmp(saved, f.regs, sizeof(saved)));
	/* Reject external PWM, altered current/OVP, and flash without writes. */
	memcpy(f.regs, saved, sizeof(saved));
	f.regs[kind == A9_FL_AW99703 ? 4 : 3] ^= 0x20;
	assert(a9_fl_adopt(&fl, 1) == -EBUSY);
	memcpy(f.regs, saved, sizeof(saved));
	f.regs[kind == A9_FL_AW99703 ? 2 : 6] ^= kind == A9_FL_AW99703 ? 0x10 : 0x80;
	assert(a9_fl_adopt(&fl, 1) == -EBUSY);
	memcpy(f.regs, saved, sizeof(saved));
	f.regs[2] |= 2;
	assert(a9_fl_adopt(&fl, 1) == -EBUSY);
	assert(!f.writes && !f.edges);
	for (b = 1; b < 0x100; b++) {
		/* Higher-current, flash, PWM and channel mistakes must not be adopted. */
		memcpy(f.regs, saved, sizeof(saved));
		f.regs[kind == A9_FL_AW99703 ? 3 : 2] = b;
		if (kind == A9_FL_AW99703 ? b == 1 : !(b & 3) || b == 1)
			continue;
		assert(a9_fl_adopt(&fl, 1) == -EBUSY);
		assert(!f.writes && !f.edges);
	}
	memcpy(f.regs, saved, sizeof(saved));
	f.regs[0] = 0;
	assert(a9_fl_adopt(&fl, 1) == -ENODEV);
	assert(!f.writes && !f.edges);
	/* HWEN high but standby must remain dark until explicitly requested. */
	reset_regs(&f);
	assert(!a9_fl_adopt(&fl, 1));
	assert(!fl.level && !fl.configured && !f.writes && !f.edges);
	assert(!a9_fl_set(&fl, 19));
	assert(fl.level == 19 && !f.edges);
}

static void test_errors(enum a9_fl_kind kind)
{
	struct fake f;
	struct a9_fl fl = setup(&f, kind);
	unsigned int calls, n;

	assert(!a9_fl_adopt(&fl, 0));
	assert(!a9_fl_set(&fl, 1473));
	calls = f.calls;
	for (n = 1; n <= calls; n++) {
		fl = setup(&f, kind);
		assert(!a9_fl_adopt(&fl, 0));
		f.fail_call = n;
		assert(a9_fl_set(&fl, 1473) == -EIO);
		assert(!f.enabled && !fl.level && !fl.configured);
		f.fail_call = 0;
		assert(!a9_fl_set(&fl, 7));
		assert(fl.level == 7); /* Recovery after every partial init/write. */
	}
	for (n = 1; n <= 3; n++) {
		fl = setup(&f, kind);
		assert(!a9_fl_set(&fl, 100));
		f.fail_call = f.calls + n;
		assert(a9_fl_set(&fl, 101) == -EIO);
		assert(!f.enabled && !fl.level);
	}
	fl = setup(&f, kind);
	assert(!a9_fl_set(&fl, 100));
	f.fail_call = f.calls + 1;
	assert(a9_fl_set(&fl, 0) == -EIO);
	assert(!f.enabled && !fl.level); /* Off even if standby write fails. */
	/* A probe read error must never toggle HWEN or write anything. */
	f.fail_call = f.calls + 1;
	f.enabled = 1;
	f.writes = f.edges = 0;
	assert(a9_fl_adopt(&fl, 1) == -EIO);
	assert(!f.writes && !f.edges && f.enabled);
}

static void test_adopt_read_errors(enum a9_fl_kind kind)
{
	static const int errors[] = { -ENXIO, -EIO, -ETIMEDOUT };
	struct fake f;
	struct a9_fl fl;
	unsigned int e, n, saved[16];

	for (e = 0; e < sizeof(errors) / sizeof(errors[0]); e++) {
		/* ID and all six profile reads must fail binding without mutation. */
		for (n = 1; n <= 7; n++) {
			fl = setup(&f, kind);
			assert(!a9_fl_set(&fl, 1782));
			memcpy(saved, f.regs, sizeof(saved));
			f.calls = f.reads = f.writes = f.edges = f.waits = f.enable_calls = 0;
			f.fail_call = n;
			f.fail_error = errors[e];
			assert(a9_fl_adopt(&fl, 1) == errors[e]);
			assert(f.reads == n && !fl.configured && f.enabled);
			assert(!f.writes && !f.edges && !f.waits && !f.enable_calls);
			assert(!memcmp(saved, f.regs, sizeof(saved)));
		}
		fl = setup(&f, kind);
		f.fail_call = 1;
		f.fail_error = errors[e];
		assert(!a9_fl_adopt(&fl, 0));
		assert(!f.reads && !f.writes && !f.enable_calls && !fl.powered);
	}
}

int main(void)
{
	enum a9_fl_kind k;

	for (k = A9_FL_AW99703; k <= A9_FL_KTD3137; k++) {
		test_raw(k);
		test_keep(k);
		test_errors(k);
		test_adopt_read_errors(k);
	}
	puts("PASS: both chips, all 2048 raw levels, cold/keep/off/restart, fault paths, read-only adoption errors");
	return 0;
}
