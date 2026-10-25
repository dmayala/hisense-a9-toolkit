/* SPDX-License-Identifier: GPL-2.0-only */
/* Production sysfs + selector, real control curves, mocked kernel plumbing. */
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include "a9_eink_pattern.h"

typedef uint8_t u8;
typedef uint32_t u32;
static unsigned checks;
#define CHECK(c) do { ++checks; if (!(c)) { \
	fprintf(stderr, "FAIL selector line %d: %s\n", __LINE__, #c); exit(1); } } while (0)
struct mutex { int locked; };
static void mutex_lock(struct mutex *m) { CHECK(!m->locked); m->locked = 1; }
static void mutex_unlock(struct mutex *m) { CHECK(m->locked); m->locked = 0; }
#define READ_ONCE(v) (v)
#define A9_MODE_CLEAR 515
struct device { struct a9_eink *a9; };
struct device_attribute { int unused; };
struct a9_eink {
	struct mutex shadow_lock, eng_lock;
	int wq;
	u8 *shadow;
	struct a9e_rect dirty;
	bool clear_req, manual_clear_req, kick, controls_dirty, test_pattern;
	u8 dirty_tm[A9E_NTILES];
	bool tm_lost, prev_pattern;
	bool dry_force_clear, force_clear, dry_init_pending, init_pending, hv_fault;
	u32 mode;
	int controls[4];
	void *thread;
	struct a9e_engine e;
};
static struct a9_eink a;
static struct device dev = { &a };
static u8 shadow[A9T_N], grey[A9T_N];
static bool enable, mask_outside_damage, fp_allow_power_on, damage_prep;
/* Diagnostics have no selector effect (covered separately by KUnit). */
static void a9_input_request(struct a9_eink *a9, bool manual)
{ (void)a9; (void)manual; }
static bool stall_fault, rail_fault;
static bool a9x_fault_latched(void) { return stall_fault || rail_fault; }
static struct a9_eink *kdev_to_a9(struct device *d) { return d->a9; }
static void wake_up(int *wq) { ++*wq; }
static bool sysfs_streq(const char *s, const char *expect)
{
	size_t n = strlen(expect);
	return !strncmp(s, expect, n) && (s[n] == '\0' || (s[n] == '\n' && s[n+1] == '\0'));
}
static int kstrtoint(const char *s, unsigned base, int *out)
{
	char *end;
	errno = 0;
	long n = strtol(s, &end, base);
	if (errno || end == s || !(*end == '\0' || (*end == '\n' && end[1] == '\0')) ||
	    n < INT32_MIN || n > INT32_MAX) return -EINVAL;
	*out = (int)n;
	return 0;
}
#include "production.inc"

static void setup(unsigned mode, bool dry)
{
	memset(&a, 0, sizeof(a));
	memset(shadow, 128, sizeof(shadow));
	a.shadow = shadow; a.e.grey = grey; a.thread = &a;
	a.mode = mode; a.e.have_pre = 1; a.e.pre_mode = mode;
	mask_outside_damage = true; enable = !dry;
	stall_fault = rail_fault = fp_allow_power_on = false;
}
static ssize_t manual(const char *s)
{ return manual_clear_store(&dev, NULL, s, strlen(s)); }
static void full_job(const struct a9_job *j, unsigned mode, bool dry)
{
	CHECK(j->dry == dry && j->mode == mode && j->clear == 1 && j->mask == 0);
	CHECK(j->rect.x1 == 0 && j->rect.y1 == 0 && j->rect.x2 == A9T_W && j->rect.y2 == A9T_H);
	CHECK(!memcmp(a.e.grey, shadow, A9T_N) || a.test_pattern);
	CHECK(!a.kick && !a.manual_clear_req && !a.clear_req && a.mode == 518);
	CHECK(!a.shadow_lock.locked && !a.eng_lock.locked && !fp_allow_power_on);
}
static void parsers(void)
{
	setup(518, false);
	const char *bad[] = {"", "0", "2", "1 ", " 1", "1\nextra", "1\n\n"};
	for (unsigned k = 0; k < sizeof(bad)/sizeof(bad[0]); ++k) {
		CHECK(manual(bad[k]) == -EINVAL);
		CHECK(!a.kick && !a.manual_clear_req && !a.clear_req && !a.wq);
	}
	a.thread = NULL;
	CHECK(manual("1") == -ENODEV);
	CHECK(!a.kick && !a.manual_clear_req && !a.wq);
	a.thread = &a;
	CHECK(manual("1\n") == 2 && a.manual_clear_req && a.kick && !a.clear_req && a.wq == 1);
	CHECK(manual("1") == 1 && a.manual_clear_req && a.wq == 2);
	CHECK(enable && !fp_allow_power_on && !stall_fault && !rail_fault && !a.hv_fault);
	puts("PASS exact manual parser, missing worker refusal, repeats queue without changing gates/faults");
}
static void steady(void)
{
	const unsigned modes[] = {513, 518, 521};
	for (unsigned d = 0; d < 2; ++d)
		for (unsigned k = 0; k < 3; ++k) {
			struct a9_job job, active;
			setup(modes[k], d);
			CHECK(manual("1") == 1 && a9_take_job(&a, &job));
			CHECK(job.mode == modes[k] && job.clear && !job.mask && job.dry == (bool)d);
			CHECK(job.rect.x1 == 0 && job.rect.y1 == 0 && job.rect.x2 == A9T_W && job.rect.y2 == A9T_H);
			CHECK(a.mode == modes[k] && !a.manual_clear_req && !a.clear_req);
			CHECK(!a9_take_job(&a, &job));
			/* A request after the worker took its active job belongs to a
			 * successor, regardless of whether the active job later succeeds. */
			active = job;
			CHECK(manual("1") == 1 && a.manual_clear_req);
			CHECK(active.mode == modes[k] && active.clear);
			CHECK(a9_take_job(&a, &job));
			CHECK(job.mode == modes[k] && job.clear && !a.manual_clear_req);
			CHECK(!a9_take_job(&a, &job));
		}
	puts("PASS manual clears retain entered 513/518/521 in real/dry jobs; successor requests survive an active job");
}
static void priority(void)
{
	/* Each reason independently forbids a mode-preserving manual clear. */
	for (unsigned d = 0; d < 2; ++d)
		for (unsigned reason = 0; reason < 11; ++reason) {
			struct a9_job job;
			setup(518, d);
			CHECK(manual("1") == 1);
			switch (reason) {
			case 0: a.clear_req = true; break;
			case 1: a.test_pattern = true; break;
			case 2: if (d) a.dry_force_clear = true; else a.force_clear = true; break;
			case 3: if (d) a.dry_init_pending = true; else a.init_pending = true; break;
			case 4: a.e.have_pre = 0; break;
			case 5: a.e.pre_mode = 515; break; /* pending mode entry */
			case 6: a.hv_fault = true; break;
			case 7: stall_fault = true; break;
			case 8: rail_fault = true; break;
			case 9: CHECK(a9_ctl_store(&dev, 2, "50", 2) == 2); break;
			case 10: CHECK(test_pattern_store(&dev, NULL, "off", 3) == 3); break;
			}
			CHECK(a9_take_job(&a, &job));
			full_job(&job, 515, d);
			CHECK(!enable == (bool)d);
			CHECK(stall_fault == (reason == 7) && rail_fault == (reason == 8));
			CHECK(a.hv_fault == (reason == 6));
			if (reason == 2) CHECK(d ? a.dry_force_clear : a.force_clear);
			if (reason == 3) CHECK(d ? a.dry_init_pending : a.init_pending);
			CHECK(!a9_take_job(&a, &job));
		}
	puts("PASS 22 real/dry recovery-precedence cases: init, unknown history, mode entry, forced GC16, controls, pattern, abort/rail/stall flags");
}
static void separate(void)
{
	struct a9_job job;
	/* Dry recovery cannot make or satisfy a real recovery obligation. */
	setup(518, false); a.dry_force_clear = a.dry_init_pending = true;
	CHECK(manual("1") == 1 && a9_take_job(&a, &job));
	CHECK(job.mode == 518 && job.clear && a.dry_force_clear && a.dry_init_pending);
	setup(518, true); a.force_clear = a.init_pending = true;
	CHECK(manual("1") == 1 && a9_take_job(&a, &job));
	CHECK(job.mode == 518 && job.clear && a.force_clear && a.init_pending);
	/* The old root-only force-clear request retains its conservative meaning. */
	setup(518, false);
	CHECK(force_clear_store(&dev, NULL, "1", 1) == 1);
	CHECK(a.clear_req && !a.manual_clear_req && a9_take_job(&a, &job));
	full_job(&job, 515, false);
	/* Fault release alone cannot waive the full recovery already owed. */
	setup(518, false); a.force_clear = true; a.hv_fault = true; rail_fault = true;
	CHECK(manual("1") == 1);
	a.hv_fault = false; rail_fault = false;
	CHECK(a9_take_job(&a, &job)); full_job(&job, 515, false);
	CHECK(a.force_clear);
	puts("PASS real/dry recovery flags remain separate; legacy force_clear and fault-release recovery stay full GC16");
}
int main(void)
{
	parsers(); steady(); priority(); separate();
	printf("manual clear selector: %u assertions passed\n", checks);
	return 0;
}
