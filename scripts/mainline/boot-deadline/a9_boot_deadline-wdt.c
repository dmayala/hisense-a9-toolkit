// SPDX-License-Identifier: GPL-2.0-only
/* Hisense A9 test boot: early APSS MMIO watchdog, independent of SCM/init. */
#include <linux/a9_boot_deadline.h>
#include <linux/atomic.h>
#include <linux/completion.h>
#include <linux/cpu.h>
#include <linux/delay.h>
#include <linux/fs.h>
#include <linux/kmsg_dump.h>
#include <linux/hrtimer.h>
#include <linux/init.h>
#include <linux/io.h>
#include <linux/kthread.h>
#include <linux/moduleparam.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/panic.h>
#include <linux/panic_notifier.h>
#include <linux/regmap.h>
#include <linux/sched.h>
#include <linux/smp.h>
#include <linux/spinlock.h>
#include <linux/watchdog.h>

#define WDT_RESET 0x04
#define WDT_ENABLE 0x08
#define WDT_BARK 0x10
#define WDT_BITE 0x14
#define MAX_TICKS 0xfffffU
#define BITE_SECONDS 30
#define PON_SPARE 0x8f
#define PET_SECONDS 8

static unsigned int deadline = 300;
static bool selftest_hang;
static atomic_t selftest_cpus = ATOMIC_INIT(0);
/* 0 inactive, 1 armed, 2 complete, 3 expiring, 4 disarm in progress. */
static atomic_t state = ATOMIC_INIT(0);
static DEFINE_RAW_SPINLOCK(hw_lock);
static DECLARE_COMPLETION(wake);
static struct hrtimer timer;
static ktime_t expires;
static void __iomem *apss;
static u32 rate;
static bool enabled, pon_ready;
/* Protected by hw_lock; neither a property nor a successful adbd start is
 * evidence that the USB host has configured this board's sole UDC. */
static bool boot_complete_requested, usb_configured;
static struct regmap *pon_map;
static u32 pon_reg, pon_mask;
static struct watchdog_device *normal_wdd;
static int (*normal_start)(struct watchdog_device *);
static int disarm_if_ready(void);

static int __init deadline_setup(char *s)
{
	return !kstrtouint(s, 10, &deadline);
}
__setup("a9_deadline=", deadline_setup);

static int __init selftest_setup(char *s)
{
	if (strcmp(s, "hang"))
		return 0;
	selftest_hang = true;
	return 1;
}
__setup("a9_deadline_selftest=", selftest_setup);

bool a9_boot_wdt_pending(void)
{
	int s = atomic_read(&state);
	return s == 1 || s == 3 || s == 4;
}

/* Normal watchdog operations hold the same lock as the exclusive early owner.
 * Return true with the lock released if the operation must be suppressed. */
bool a9_boot_wdt_block(unsigned long *flags)
{
	raw_spin_lock_irqsave(&hw_lock, *flags);
	if (a9_boot_wdt_pending()) {
		raw_spin_unlock_irqrestore(&hw_lock, *flags);
		return true;
	}
	return false;
}

void a9_boot_wdt_unlock(unsigned long flags)
{
	raw_spin_unlock_irqrestore(&hw_lock, flags);
}

void a9_boot_wdt_attach(struct watchdog_device *wdd,
			int (*start)(struct watchdog_device *))
{
	if (!enabled || !of_machine_is_compatible("hisense,hlte556n"))
		return;
	normal_start = start;
	smp_store_release(&normal_wdd, wdd);
	pr_notice("A9_BOOT_WDT normal watchdog attached timeout=%u; hardware remains deadline-owned\n",
		  wdd->timeout);
}

/* Called from usb_gadget_set_state under the gadget state lock, including
 * disconnect/reset transitions. IRQ-safe: no PMIC access or sleeping here. */
void a9_boot_wdt_usb_configured(bool configured)
{
	unsigned long flags;
	bool changed;
	if (!READ_ONCE(enabled))
		return;
	raw_spin_lock_irqsave(&hw_lock, flags);
	changed = usb_configured != configured;
	usb_configured = configured;
	raw_spin_unlock_irqrestore(&hw_lock, flags);
	if (changed) {
		pr_notice("A9_BOOT_WDT USB configured=%u boot_completed_requested=%u\n",
			  configured, READ_ONCE(boot_complete_requested));
		if (configured)
			complete(&wake);
	}
}

bool a9_boot_wdt_scm_ready(void)
{
	return !enabled || smp_load_acquire(&pon_ready);
}

/* Called as soon as the ordinary PMIC regmap exists, before any SCM probe
 * can run. This is the reboot-mode driver's exact SPMI masked write/readback. */
int a9_boot_wdt_pon(struct regmap *map, u32 base, unsigned int shift)
{
	unsigned int value;
	int ret;
	if (!enabled)
		return 0;
	pon_map = map;
	pon_reg = base + PON_SPARE;
	pon_mask = GENMASK(7, shift);
	ret = regmap_update_bits(map, pon_reg, pon_mask, 2U << shift);
	if (!ret)
		ret = regmap_read(map, pon_reg, &value);
	if (!ret && (value & pon_mask) != (2U << shift))
		ret = -EIO;
	if (ret) {
		pr_emerg("A9_BOOT_WDT PON arm failed rc=%d; SCM stays deferred\n", ret);
		return ret;
	}
	pr_notice("A9_BOOT_WDT PON bootloader=2 armed via SPMI reg=%#x shift=%u; SCM released\n",
		  pon_reg, shift);
	smp_store_release(&pon_ready, true);
	return 0;
}

/* No clock, regmap, notifier, firmware or SCM operation on this path. */
static void program_ticks(u32 ticks)
{
	writel(0, apss + WDT_ENABLE);
	writel(1, apss + WDT_RESET);
	writel(ticks, apss + WDT_BARK);
	writel(ticks, apss + WDT_BITE);
	writel(1, apss + WDT_ENABLE); /* no secure/unmasked bark interrupt */
	wmb();
	readl(apss + WDT_ENABLE);
}

static void expire_now(bool disarm_error)
{
	unsigned long flags;
	raw_spin_lock_irqsave(&hw_lock, flags);
	if (atomic_read(&state) != 1 &&
	    !(disarm_error && atomic_read(&state) == 4)) {
		raw_spin_unlock_irqrestore(&hw_lock, flags);
		return;
	}
	atomic_set(&state, 3);
	/* Hardware enforces the flush cap even if the thread/IRQs stop now. */
	program_ticks(rate * 9 / 4); /* 2.25s, including write/flush overhead */
	raw_spin_unlock_irqrestore(&hw_lock, flags);
	pr_emerg("A9_BOOT_DEADLINE EXPIRED seconds=%u target=bootloader hardware_bite_ms=2250 SCM=none\n",
		 deadline);
	complete(&wake);
}

static enum hrtimer_restart expire_timer(struct hrtimer *t)
{
	expire_now(false);
	return HRTIMER_NORESTART;
}

static int keeper(void *unused)
{
	for (;;) {
		unsigned long flags;
		wait_for_completion_timeout(&wake, PET_SECONDS * HZ);
		if (atomic_read(&state) == 2)
			return 0;
		if (atomic_read(&state) == 1 &&
		    !ktime_before(ktime_get_boottime(), expires))
			expire_now(false);
		if (atomic_read(&state) == 1)
			disarm_if_ready();
		if (atomic_read(&state) == 2)
			return 0;
		if (atomic_read(&state) == 3) {
			/* emergency_sync queues work and returns; no storage wait. The
			 * already programmed 2.25s bite remains the independent cap. */
			emergency_sync();
			/* Best effort, worker context only, after the independent reset is
			 * armed. Ramoops max-reason=3 includes this non-panic dump. No
			 * timer, hardware bite, reset route or shutdown wait is extended. */
			kmsg_dump(KMSG_DUMP_EMERG);
			msleep(2000);
			raw_spin_lock_irqsave(&hw_lock, flags);
			program_ticks(max(1U, rate * 128 / 1000));
			raw_spin_unlock_irqrestore(&hw_lock, flags);
			return 0;
		}
		raw_spin_lock_irqsave(&hw_lock, flags);
		if (atomic_read(&state) == 1 &&
		    ktime_before(ktime_get_boottime(), expires)) {
			writel(1, apss + WDT_RESET);
			wmb();
		}
		raw_spin_unlock_irqrestore(&hw_lock, flags);
	}
}

static int disarm_if_ready(void)
{
	struct watchdog_device *wdd;
	unsigned long flags;
	unsigned int value;
	int old, ret;
	wdd = smp_load_acquire(&normal_wdd);
	if (!wdd || !smp_load_acquire(&pon_ready))
		return -EAGAIN;
	raw_spin_lock_irqsave(&hw_lock, flags);
	/* Check both prerequisites and the absolute deadline at the transition.
	 * A USB reset before boot_completed revokes the earlier configured state. */
	if (!boot_complete_requested || !usb_configured ||
	    !ktime_before(ktime_get_boottime(), expires)) {
		raw_spin_unlock_irqrestore(&hw_lock, flags);
		return -EAGAIN;
	}
	old = atomic_cmpxchg(&state, 1, 4);
	raw_spin_unlock_irqrestore(&hw_lock, flags);
	if (old != 1)
		return old == 3 ? -ETIME : -EAGAIN;
	/* Stop pets but retain the hardware timeout while touching SPMI. */
	ret = regmap_update_bits(pon_map, pon_reg, pon_mask, 0);
	if (!ret)
		ret = regmap_read(pon_map, pon_reg, &value);
	if (!ret && (value & pon_mask))
		ret = -EIO;
	if (ret) {
		pr_emerg("A9_BOOT_WDT PON clear failed rc=%d\n", ret);
		expire_now(true);
		return ret;
	}
	hrtimer_cancel(&timer);
	raw_spin_lock_irqsave(&hw_lock, flags);
	old = atomic_cmpxchg(&state, 4, 2);
	raw_spin_unlock_irqrestore(&hw_lock, flags);
	if (old != 4)
		return -ETIME;
	ret = normal_start(wdd);
	if (!ret)
		set_bit(WDOG_HW_RUNNING, &wdd->status);
	complete(&wake);
	pr_notice("A9_BOOT_DEADLINE disarmed boot_completed=1 usb_configured=1 PON=0 handoff_timeout=%u watchdog_active=%u rc=%d\n",
		  wdd->timeout, watchdog_active(wdd), ret);
	return ret;
}

static int completed_set(const char *s, const struct kernel_param *kp)
{
	unsigned long flags;
	unsigned int val;
	int current_state;
	bool configured;
	if (kstrtouint(s, 10, &val) || val != 1)
		return -EINVAL;
	raw_spin_lock_irqsave(&hw_lock, flags);
	current_state = atomic_read(&state);
	if (current_state == 1)
		boot_complete_requested = true;
	configured = usb_configured;
	raw_spin_unlock_irqrestore(&hw_lock, flags);
	if (current_state == 2)
		return 0;
	if (current_state != 1)
		return current_state == 3 ? -ETIME : -EAGAIN;
	pr_notice("A9_BOOT_DEADLINE boot_completed=1 requested; usb_configured=%u; deadline remains armed until both\n",
		  configured);
	complete(&wake);
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

static int panic_bite(struct notifier_block *nb, unsigned long event, void *ptr)
{
	unsigned long flags;
	if (!a9_boot_wdt_pending())
		return NOTIFY_DONE;
	raw_spin_lock_irqsave(&hw_lock, flags);
	atomic_set(&state, 3);
	program_ticks(rate * 6); /* panic=5 plus dump/firmware-stall backstop */
	raw_spin_unlock_irqrestore(&hw_lock, flags);
	pr_emerg("A9_BOOT_WDT panic backstop bite=6s PON_prearmed=%u SCM=none\n",
		 READ_ONCE(pon_ready));
	return NOTIFY_DONE;
}
static struct notifier_block panic_nb = {
	.notifier_call = panic_bite, .priority = INT_MAX,
};

static int __init deadline_init(void)
{
	struct device_node *np, *clock;
	struct task_struct *task;
	struct resource res;
	if (!of_machine_is_compatible("hisense,hlte556n"))
		return 0;
	if (!deadline) {
		atomic_set(&state, 2);
		pr_notice("A9_BOOT_DEADLINE disabled\n");
		return 0;
	}
	np = of_find_compatible_node(NULL, NULL, "qcom,apss-wdt-sm6115");
	if (!np || of_address_to_resource(np, 0, &res) ||
	    res.start != 0x0f017000 || resource_size(&res) < 0x18)
		panic("A9_BOOT_WDT invalid APSS DT resource");
	clock = of_parse_phandle(np, "clocks", 0);
	if (!clock || of_property_read_u32(clock, "clock-frequency", &rate) || rate != 32764)
		panic("A9_BOOT_WDT invalid fixed sleep clock");
	of_node_put(clock);
	apss = of_iomap(np, 0);
	of_node_put(np);
	if (!apss)
		panic("A9_BOOT_WDT cannot map APSS");
	enabled = true;
	expires = ktime_add(ktime_get_boottime(), ktime_set(deadline, 0));
	atomic_set(&state, 1);
	program_ticks(min(MAX_TICKS, rate * BITE_SECONDS));
	if (readl(apss + WDT_BITE) != min(MAX_TICKS, rate * BITE_SECONDS) ||
	    !(readl(apss + WDT_ENABLE) & 1))
		panic("A9_BOOT_WDT early hardware readback failed");
	atomic_notifier_chain_register(&panic_notifier_list, &panic_nb);
	hrtimer_setup(&timer, expire_timer, CLOCK_BOOTTIME, HRTIMER_MODE_ABS);
	hrtimer_start(&timer, expires, HRTIMER_MODE_ABS);
	task = kthread_create(keeper, NULL, "a9-deadline-wdt");
	if (IS_ERR(task))
		panic("A9_BOOT_WDT cannot create exclusive deadline keeper");
	sched_set_fifo(task);
	wake_up_process(task);
	pr_notice("A9_BOOT_DEADLINE armed seconds=%u early=1 HW_bite_ticks=%u clock=%u pet_s=%u SCM_gate=PON\n",
		  deadline, min(MAX_TICKS, rate * BITE_SECONDS), rate, PET_SECONDS);
	return 0;
}
early_initcall(deadline_init);

/* Explicit destructive test only. No userspace, keeper, hrtimer, panic/restart
 * path or SCM call can recover this CPU set; the independent APSS bite must.
 * Async SMP callbacks release their CSD before entering this nonreturning fn. */
static void selftest_freeze_cpu(void *unused)
{
	local_irq_disable();
	atomic_inc(&selftest_cpus);
	for (;;)
		cpu_relax();
}

static int __init deadline_selftest(void)
{
	unsigned int count;
	if (!selftest_hang)
		return 0;
	if (!enabled || atomic_read(&state) != 1 ||
	    !smp_load_acquire(&pon_ready) || !(readl(apss + WDT_ENABLE) & 1)) {
		pr_emerg("A9_BOOT_WDT SELFTEST REFUSED: watchdog/PON not armed\n");
		return -EINVAL;
	}
	cpus_read_lock();
	preempt_disable();
	count = num_online_cpus();
	pr_emerg("A9_BOOT_WDT SELFTEST hang: freezing %u online CPUs with IRQs off; software_deadline=%u hardware_bite<=%us since last pet PON=2\n",
		 count, deadline, BITE_SECONDS);
	smp_call_function(selftest_freeze_cpu, NULL, 0);
	local_irq_disable();
	/* Observe every remote callback before publishing the final marker. */
	while (atomic_read(&selftest_cpus) != count - 1)
		cpu_relax();
	pr_emerg("A9_BOOT_WDT SELFTEST all %u CPUs entered hard hang; waiting for hardware bite only\n", count);
	selftest_freeze_cpu(NULL);
	return 0;
}
late_initcall_sync(deadline_selftest);
