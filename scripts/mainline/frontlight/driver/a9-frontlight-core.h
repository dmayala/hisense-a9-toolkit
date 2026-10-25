/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef A9_FRONTLIGHT_CORE_H
#define A9_FRONTLIGHT_CORE_H

/* No Linux dependencies: this exact register logic is exercised on the host. */
#define A9_FL_MAX 2047U

enum a9_fl_kind { A9_FL_AW99703, A9_FL_KTD3137 };

struct a9_fl_io {
	void *ctx;
	int (*read)(void *ctx, unsigned int reg);
	int (*write)(void *ctx, unsigned int reg, unsigned int val);
	int (*enable)(void *ctx, int on);
	void (*settle)(void *ctx);
};

struct a9_fl {
	enum a9_fl_kind kind;
	struct a9_fl_io io;
	unsigned int level;
	int powered;
	int configured;
};

/* Probe only reads. A live unrecognized configuration is left untouched. */
int a9_fl_adopt(struct a9_fl *fl, int enabled);
/* Caller serializes all operations. Values are raw, without per-chip gains. */
int a9_fl_set(struct a9_fl *fl, unsigned int level);

#endif
