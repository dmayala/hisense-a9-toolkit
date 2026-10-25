/* SPDX-License-Identifier: GPL-2.0-only */
/* Host kernel-service stubs. Driver definitions are extracted verbatim. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "pmi632-policy.h"
#include "pmi632-fv.h"
#define BIT(n) (1U << (n))
#define GENMASK(h, l) ((~0U << (l)) & (~0U >> (31 - (h))))
#define IS_ENABLED(x) (x)
#define HZ 100UL
#define time_after(a, b) ((long)((b) - (a)) < 0)
#define msecs_to_jiffies(ms) ((ms) * HZ / 1000)
#define container_of(ptr, type, member) ((type *)((char *)(ptr) - offsetof(type, member)))
#define to_delayed_work(ptr) container_of(ptr, struct delayed_work, work)
typedef uint32_t u32;
struct mutex { int held; };
struct work_struct { int unused; };
struct delayed_work { struct work_struct work; bool pending; };
struct device { void *data; };
struct power_supply { void *data; };
/* PROPERTY_ENUM */
union power_supply_propval { int intval; const char *strval; };
/* DRIVER_STRUCT */
static unsigned long jiffies;
static bool observe = TEST_OBSERVE, fv_stock = true;
static int read_error, off_error, bus_writes, changes;
static struct a9_sample next_sample;
static void mutex_lock(struct mutex *m) { assert(!m->held); m->held = 1; }
static void mutex_unlock(struct mutex *m) { assert(m->held); m->held = 0; }
static void *dev_get_drvdata(struct device *d) { return d->data; }
static void *power_supply_get_drvdata(struct power_supply *p) { return p->data; }
static void cancel_delayed_work_sync(struct delayed_work *w)
{
	struct a9_pmic *p = container_of(w, struct a9_pmic, work);
	assert(!p->lock.held); w->pending = false;
}
static void schedule_delayed_work(struct delayed_work *w, unsigned long delay)
{
	struct a9_pmic *p = container_of(w, struct a9_pmic, work);
	(void)delay; assert(!p->lock.held); w->pending = true;
}
static void power_supply_changed(struct power_supply *p) { (void)p; changes++; }
static int a9_read_sample(struct a9_pmic *p, struct a9_sample *s)
{ assert(p->lock.held); *s = next_sample; return read_error; }
static int a9_update_check(struct a9_pmic *p, unsigned int reg,
			   unsigned int mask, unsigned int value)
{ (void)p; (void)reg; (void)mask; (void)value; bus_writes++; return off_error; }
static int a9_settings_check(struct a9_pmic *p, bool program)
{ (void)p; (void)program; assert(0 && "control enable path outside this test"); return -EPERM; }
static int regmap_read(struct regmap *m, unsigned int reg, unsigned int *v)
{ (void)m; (void)reg; (void)v; assert(0 && "unexpected control read"); return -EPERM; }
static int regmap_write(struct regmap *m, unsigned int reg, unsigned int v)
{ (void)m; (void)reg; (void)v; bus_writes++; return -EPERM; }
#define dev_err(...) ((void)0)
#define dev_err_ratelimited(...) ((void)0)
#define dev_crit_ratelimited(...) ((void)0)
