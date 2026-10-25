// SPDX-License-Identifier: GPL-2.0-only
/* Independent of init, watchdogd and shared workqueues. A9 test kernels only. */
#include <linux/atomic.h>
#include <linux/completion.h>
#include <linux/delay.h>
#include <linux/fs.h>
#include <linux/kmsg_dump.h>
#include <linux/hrtimer.h>
#include <linux/init.h>
#include <linux/kthread.h>
#include <linux/moduleparam.h>
#include <linux/of.h>
#include <linux/panic.h>
#include <linux/reboot.h>
#include <linux/sched.h>

static unsigned int deadline = 300;
static atomic_t state = ATOMIC_INIT(0); /* 0 unarmed, 1 armed, 2 complete, 3 expired */
static DECLARE_COMPLETION(expired);
static struct hrtimer timer, fallback;

static int __init deadline_setup(char *s)
{
	return !kstrtouint(s, 10, &deadline);
}
__setup("a9_deadline=", deadline_setup);

static int completed_set(const char *s, const struct kernel_param *kp)
{
	unsigned int val;
	int old;
	if (kstrtouint(s, 10, &val) || val != 1)
		return -EINVAL;
	old = atomic_cmpxchg(&state, 1, 2);
	if (!old)
		return -EAGAIN; /* No command-line bypass before late_initcall. */
	if (old == 3)
		return -ETIME;
	if (old == 1) {
		hrtimer_cancel(&timer);
		pr_notice("A9_BOOT_DEADLINE disarmed boot_completed=1\n");
	}
	return 0;
}

static int completed_get(char *s, const struct kernel_param *kp)
{
	return sysfs_emit(s, "%u\n", atomic_read(&state) == 2);
}

static const struct kernel_param_ops completed_ops = {
	.set = completed_set, .get = completed_get,
};
module_param_cb(boot_completed, &completed_ops, NULL, 0600);

static enum hrtimer_restart force_restart(struct hrtimer *t)
{
	/* Existing beacon=2 is armed at PON probe; SCM dload is forced off.
	 * This IRQ path bypasses a stuck orderly shutdown's locks/notifiers. */
	pr_emerg("A9_BOOT_DEADLINE emergency restart after shutdown stall\n");
	emergency_restart();
	return HRTIMER_NORESTART;
}

static enum hrtimer_restart expire(struct hrtimer *t)
{
	if (atomic_cmpxchg(&state, 1, 3) == 1) {
		pr_emerg("A9_BOOT_DEADLINE EXPIRED seconds=%u target=bootloader\n", deadline);
		hrtimer_start(&fallback, ktime_set(15, 0), HRTIMER_MODE_REL);
		complete(&expired);
	}
	return HRTIMER_NORESTART;
}

static int restart_thread(void *unused)
{
	wait_for_completion(&expired);
	/* The asynchronous ramdisk logger fsyncs immediately on EXPIRED.
	 * Queue an additional filesystem flush, but never wait on its workers. */
	emergency_sync();
	/* Best effort, worker context only, after the independent reset is
	 * armed. Ramoops max-reason=3 includes this non-panic dump. No
	 * timer, hardware bite, reset route or shutdown wait is extended. */
	kmsg_dump(KMSG_DUMP_EMERG);
	msleep(3000);
	kernel_restart("bootloader");
	return 0;
}

static int __init deadline_init(void)
{
	struct task_struct *task;
	if (!of_machine_is_compatible("hisense,hlte556n"))
		return 0;
	if (!deadline) {
		atomic_set(&state, 2);
		pr_notice("A9_BOOT_DEADLINE disabled\n");
		return 0;
	}
	hrtimer_setup(&timer, expire, CLOCK_BOOTTIME, HRTIMER_MODE_REL);
	hrtimer_setup(&fallback, force_restart, CLOCK_BOOTTIME, HRTIMER_MODE_REL);
	task = kthread_create(restart_thread, NULL, "a9-boot-deadline");
	if (IS_ERR(task))
		panic("A9_BOOT_DEADLINE cannot create restart thread");
	sched_set_fifo(task);
	wake_up_process(task);
	atomic_set(&state, 1);
	hrtimer_start(&timer, ktime_set(deadline, 0), HRTIMER_MODE_REL);
	pr_notice("A9_BOOT_DEADLINE armed seconds=%u panic=%d\n", deadline, panic_timeout);
	return 0;
}
late_initcall(deadline_init);
