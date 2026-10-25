// SPDX-License-Identifier: GPL-2.0-only
/* Android 16 UsbService still requires /sys/class/android_usb before creating
 * UsbDeviceManager. Expose that legacy discovery/state ABI for mainline UDCs.
 * The existing root USB helper forwards real UDC state here; the gadget HAL
 * alone owns functions/binding. This module performs no USB hardware control.
 */
#include <linux/device.h>
#include <linux/kdev_t.h>
#include <linux/module.h>
#include <linux/mutex.h>

static struct class *android_usb_class;
static struct device *android_usb_device;
static DEFINE_MUTEX(state_lock);
static const char * const states[] = { "DISCONNECTED", "CONNECTED", "CONFIGURED" };
static int usb_state;
static bool event_debug;
module_param(event_debug, bool, 0644);

static ssize_t state_show(struct device *dev, struct device_attribute *attr, char *buf)
{
	ssize_t result;

	mutex_lock(&state_lock);
	result = sysfs_emit(buf, "%s\n", states[usb_state]);
	mutex_unlock(&state_lock);
	return result;
}

static ssize_t state_store(struct device *dev, struct device_attribute *attr,
			   const char *buf, size_t count)
{
	char *env[] = { NULL, NULL };
	int next;
	bool refresh = sysfs_streq(buf, "CONFIGURED_REFRESH");

	for (next = 0; !refresh && next < ARRAY_SIZE(states); next++)
		if (sysfs_streq(buf, states[next]))
			break;
	if (refresh)
		next = 2;
	if (next == ARRAY_SIZE(states))
		return -EINVAL;
	mutex_lock(&state_lock);
	/* Periodic unchanged observations are not new USB events. The helper
	 * explicitly requests one refresh on a new function/UDC/FFS generation
	 * to retain the short reconnect/function-switch timeout workaround. */
	if (usb_state == next && !refresh) {
		mutex_unlock(&state_lock);
		return count;
	}
	if (event_debug)
		pr_info("A9_USB_STATE old=%s new=%s refresh=%d\n", states[usb_state], states[next], refresh);
	/* Polling the UDC can skip its short CONNECTED state. Android cancels a
	 * queued, debounced DISCONNECTED update on CONNECTED, not CONFIGURED.
	 * Preserve the stock event sequence so a stale disconnect cannot later
	 * overwrite a configured connection and force the framework back to ADB.
	 */
	if (usb_state == 0 && next == 2) {
		usb_state = 1;
		env[0] = "USB_STATE=CONNECTED";
		kobject_uevent_env(&dev->kobj, KOBJ_CHANGE, env);
	}
	usb_state = next;
	switch (next) {
	case 1: env[0] = "USB_STATE=CONNECTED"; break;
	case 2: env[0] = "USB_STATE=CONFIGURED"; break;
	default: env[0] = "USB_STATE=DISCONNECTED"; break;
	}
	kobject_uevent_env(&dev->kobj, KOBJ_CHANGE, env);
	mutex_unlock(&state_lock);
	return count;
}
static DEVICE_ATTR_RW(state);

static int __init a9_usb_compat_init(void)
{
	int ret;

	android_usb_class = class_create("android_usb");
	if (IS_ERR(android_usb_class))
		return PTR_ERR(android_usb_class);
	android_usb_device = device_create(android_usb_class, NULL, MKDEV(0, 0), NULL, "android0");
	if (IS_ERR(android_usb_device)) {
		ret = PTR_ERR(android_usb_device);
		goto destroy_class;
	}
	ret = device_create_file(android_usb_device, &dev_attr_state);
	if (!ret)
		return 0;
	device_unregister(android_usb_device);
destroy_class:
	class_destroy(android_usb_class);
	return ret;
}

static void __exit a9_usb_compat_exit(void)
{
	device_remove_file(android_usb_device, &dev_attr_state);
	device_unregister(android_usb_device);
	class_destroy(android_usb_class);
}
module_init(a9_usb_compat_init);
module_exit(a9_usb_compat_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("A9 Android USB discovery and UDC state compatibility");
