// SPDX-License-Identifier: GPL-2.0-only
/*
 * a9_eink guards that do not need the DRM side: the frame-progress and
 * rail fault latches with their clear_fault parameter, and the temperature-bin admission
 * window (bin_min/bin_max). Separate so the host test harness
 * (tests/mainline-a9eink-stall in the A9 repo) compiles exactly this code.
 */
#include <linux/atomic.h>
#include <linux/kernel.h>
#include <linux/moduleparam.h>
#include <linux/mutex.h>
#include <linux/printk.h>

#include "a9_eink.h"

atomic_t a9x_stall_fault = ATOMIC_INIT(0);
atomic_t a9x_rail_fault = ATOMIC_INIT(0);

static int a9_clear_fault_set(const char *val, const struct kernel_param *kp)
{
	bool clear;
	int ret = kstrtobool(val, &clear);

	if (ret)
		return ret;
	if (clear && atomic_xchg(&a9x_stall_fault, 0))
		pr_info("a9_eink: frame-progress fault cleared (clear_fault)\n");
	if (clear && atomic_xchg(&a9x_rail_fault, 0))
		pr_info("a9_eink: rail fault cleared (clear_fault): the next real update is a full Clear\n");
	return 0;
}

static const struct kernel_param_ops a9_clear_fault_ops = {
	.set = a9_clear_fault_set,
};
module_param_cb(clear_fault, &a9_clear_fault_ops, NULL, 0200);
MODULE_PARM_DESC(clear_fault,
		 "Write 1 to clear a latched frame-progress or rail fault (write-only); the FP9929A's own fp9929a.clear_fault is separate");

/*
 * Temperature-bin admission (review 7 finding 2): a real waveform update runs
 * only if its bin is in [bin_min, bin_max]; checked before power-up (previous
 * FP9929A sample + fresh thermistor) and again after it (fresh samples),
 * before any frame. 0..13, bin_min <= bin_max (a write that would invert the
 * window is refused: narrow by raising bin_min last, widen by bin_max first).
 */
static int bin_min;
static int bin_max = A9T_BINS - 1;
static DEFINE_MUTEX(a9_bin_lock);

static int a9_bin_set(const char *val, const struct kernel_param *kp)
{
	int v, ret = kstrtoint(val, 0, &v);

	if (ret)
		return ret;
	if (v < 0 || v > A9T_BINS - 1)
		return -EINVAL;
	mutex_lock(&a9_bin_lock);
	if (kp->arg == &bin_min ? v > READ_ONCE(bin_max) : v < READ_ONCE(bin_min))
		ret = -EINVAL;
	else
		WRITE_ONCE(*(int *)kp->arg, v);
	mutex_unlock(&a9_bin_lock);
	return ret;
}

static const struct kernel_param_ops a9_bin_ops = {
	.set = a9_bin_set,
	.get = param_get_int,
};
module_param_cb(bin_min, &a9_bin_ops, &bin_min, 0644);
MODULE_PARM_DESC(bin_min, "Lowest temperature bin a real update may run at (0..13, default 0)");
module_param_cb(bin_max, &a9_bin_ops, &bin_max, 0644);
MODULE_PARM_DESC(bin_max, "Highest temperature bin a real update may run at (0..13, default 13)");

bool a9_bin_admitted(int bin, int *lo, int *hi)
{
	*lo = READ_ONCE(bin_min);
	*hi = READ_ONCE(bin_max);
	return bin >= *lo && bin <= *hi;
}
