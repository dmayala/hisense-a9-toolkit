/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef _LINUX_A9_BOOT_DEADLINE_H
#define _LINUX_A9_BOOT_DEADLINE_H
#include <linux/types.h>
struct regmap;
struct watchdog_device;
bool a9_boot_wdt_scm_ready(void);
int a9_boot_wdt_pon(struct regmap *, u32, unsigned int);
bool a9_boot_wdt_pending(void);
bool a9_boot_wdt_block(unsigned long *flags);
void a9_boot_wdt_unlock(unsigned long flags);
void a9_boot_wdt_attach(struct watchdog_device *, int (*start)(struct watchdog_device *));
void a9_boot_wdt_usb_configured(bool);
#endif
