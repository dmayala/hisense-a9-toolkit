// SPDX-License-Identifier: GPL-2.0-only
#include <assert.h>
#include <stdio.h>
#include "../pmi632-fv.h"

struct fake {
	unsigned int initial, readback;
	int fail_read, fail_write, reads, writes;
	char trace[3];
	unsigned int used;
};

static int read_fv(void *context, unsigned int reg, unsigned int *value)
{
	struct fake *f = context;

	assert(reg == 0x1070);
	assert(f->used < sizeof(f->trace));
	f->trace[f->used++] = 'R';
	f->reads++;
	if (f->reads == f->fail_read)
		return -EACCES;
	*value = f->reads == 1 ? f->initial : f->readback;
	return 0;
}

static int write_fv(void *context, unsigned int reg, unsigned int value)
{
	struct fake *f = context;

	assert(reg == 0x1070 && value == 0x55);
	assert(f->used < sizeof(f->trace));
	f->trace[f->used++] = 'W';
	f->writes++;
	return f->fail_write ? -EACCES : 0;
}

static int attempt(struct fake *f)
{
	const struct a9_fv_io io = { f, read_fv, write_fv };
	int ret = a9_fv_stock_once(&io, 1);

	assert(f->writes <= 1 && f->reads <= 2);
	return ret;
}

int main(void)
{
	struct fake f;
	unsigned int raw;

	/* Pure observe has no hardware access, even with no callbacks installed. */
	assert(!a9_fv_stock_once(NULL, 0));
	for (raw = 0; raw <= 256; raw++) {
		f = (struct fake){ .initial = raw, .readback = 0x55 };
		if (raw == 0x55 || raw == 0x5a) {
			assert(!attempt(&f));
			assert(f.reads == 2 && f.writes == 1);
			assert(f.trace[0] == 'R' && f.trace[1] == 'W' && f.trace[2] == 'R');
		} else {
			assert(attempt(&f) == -ERANGE);
			assert(f.reads == 1 && f.writes == 0);
		}
	}
	/* Every wrong readback fails after one write, with no corrective write. */
	for (raw = 0; raw < 256; raw++) {
		f = (struct fake){ .initial = 0x5a, .readback = raw };
		assert(attempt(&f) == (raw == 0x55 ? 0 : -EIO));
		assert(f.reads == 2 && f.writes == 1);
	}
	f = (struct fake){ .initial = 0x5a, .readback = 0x55, .fail_read = 1 };
	assert(attempt(&f) == -EACCES && f.reads == 1 && f.writes == 0);
	f = (struct fake){ .initial = 0x5a, .readback = 0x55, .fail_write = 1 };
	assert(attempt(&f) == -EACCES && f.reads == 1 && f.writes == 1);
	f = (struct fake){ .initial = 0x5a, .readback = 0x55, .fail_read = 2 };
	assert(attempt(&f) == -EACCES && f.reads == 2 && f.writes == 1);
	puts("PASS: disabled FV zero I/O, handoff whitelist, one-write budget, every readback, I/O errors without retries");
}
