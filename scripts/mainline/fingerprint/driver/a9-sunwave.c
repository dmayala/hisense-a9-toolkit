// SPDX-License-Identifier: GPL-2.0-only
/* Hisense A9 Sunwave control ABI, reconstructed from the shipped ARM64 driver.
 * Sensor transfers belong to the sw_fp TA. No Linux SPI controller is claimed.
 * Addresses and intentional no-ops are documented in ../README.md.
 */
#include <linux/compat.h>
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/input.h>
#include <linux/interrupt.h>
#include <linux/kref.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/pm_wakeup.h>
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/workqueue.h>

#define SF_GET_VERSION 0x80087320U /* encoded pointer size 8; stock copies 32 */
#define SF_REPORT_KEY  0x40087309U
#define SF_SPI_SPEED   0xc008730aU

struct sf_device {
	struct device *dev;
	struct miscdevice misc;
	struct gpio_desc *power, *reset, *finger, *logic;
	struct input_dev *input;
	struct work_struct finger_work, logic_work;
	struct mutex lock;
	struct kref refs;
	int irq, logic_irq;
	bool dead, irq_wake, logic_wake;
};

static const unsigned short sf_keys[] = {
	102, 139, 158, 530, 531, 535, 532, 533, 534, 143,
};

static void sf_free(struct kref *ref)
{
	kfree(container_of(ref, struct sf_device, refs));
}

static int sf_power(struct sf_device *sf, bool enabled)
{
	gpiod_set_value_cansleep(sf->power, enabled);
	if (enabled)
		msleep(10);
	return 0;
}

static int sf_reset(struct sf_device *sf)
{
	/* Stock high 1ms, low 10ms, high; HAL supplies the post-reset delay. */
	gpiod_set_value_cansleep(sf->reset, 1);
	msleep(1);
	gpiod_set_value_cansleep(sf->reset, 0);
	msleep(10);
	gpiod_set_value_cansleep(sf->reset, 1);
	return 0;
}

static void sf_logic_check(struct sf_device *sf)
{
	int first = gpiod_get_value_cansleep(sf->logic);

	msleep(300);
	if (first == 1 && gpiod_get_value_cansleep(sf->logic) == 1)
		sf_power(sf, false);
}

static void sf_finger_work(struct work_struct *work)
{
	struct sf_device *sf = container_of(work, struct sf_device, finger_work);
	char *env[] = { "SPI_STATE=finger", NULL };

	/* Stock emits from its misc device kobject, NETLINK_KOBJECT_UEVENT. */
	kobject_uevent_env(&sf->misc.this_device->kobj, KOBJ_CHANGE, env);
}

static void sf_logic_work(struct work_struct *work)
{
	struct sf_device *sf = container_of(work, struct sf_device, logic_work);

	mutex_lock(&sf->lock);
	if (!sf->dead)
		sf_logic_check(sf);
	mutex_unlock(&sf->lock);
}

static irqreturn_t sf_finger_irq(int irq, void *data)
{
	struct sf_device *sf = data;

	pm_wakeup_event(sf->dev, 500);
	schedule_work(&sf->finger_work);
	return IRQ_HANDLED;
}

static irqreturn_t sf_logic_irq(int irq, void *data)
{
	struct sf_device *sf = data;

	schedule_work(&sf->logic_work);
	return IRQ_HANDLED;
}

static int sf_open(struct inode *inode, struct file *file)
{
	struct sf_device *sf = container_of(file->private_data, struct sf_device, misc);
	int ret = 0;

	mutex_lock(&sf->lock);
	if (sf->dead)
		ret = -ENODEV;
	else {
		kref_get(&sf->refs);
		file->private_data = sf;
		nonseekable_open(inode, file);
	}
	mutex_unlock(&sf->lock);
	return ret;
}

static int sf_release(struct inode *inode, struct file *file)
{
	kref_put(&((struct sf_device *)file->private_data)->refs, sf_free);
	return 0;
}

static long sf_ioctl(struct file *file, unsigned int cmd, unsigned long arg)
{
	struct sf_device *sf = file->private_data;
	void __user *up = (void __user *)arg;
	static const char version[32] = "v2.2.10-2018-03-05";
	struct { s32 key, value; } event;
	unsigned int key;
	long ret = 0;

	mutex_lock(&sf->lock);
	if (sf->dead) {
		ret = -ENODEV;
		goto out;
	}
	switch (cmd) {
	case 0x7300: /* INIT: stock already initialized in probe */
	case 0x7301: /* DEINIT: no-op in this shipped build */
	case 0x7303: /* ENABLE_IRQ */
	case 0x7304: /* DISABLE_IRQ */
	case 0x7305: /* ENABLE_SPI_CLK: stock sf_spi_clock_enable returns 0 */
	case 0x7306: /* DISABLE_SPI_CLK */
	case 0x730b: /* SYNC_CONFIG */
	case 0x7321: /* REQUEST_IRQ: already requested by probe */
	case SF_SPI_SPEED:
		break;
	case 0x7302:
		ret = sf_reset(sf);
		break;
	case 0x7307:
	case 0x7308:
		ret = sf_power(sf, cmd == 0x7307);
		break;
	case 0x730d: /* GET_ATTRIBUTE */
		ret = put_user(0U, (u32 __user *)up);
		break;
	case SF_GET_VERSION:
		ret = copy_to_user(up, version, sizeof(version)) ? -EFAULT : 0;
		break;
	case SF_REPORT_KEY:
		if (copy_from_user(&event, up, sizeof(event))) {
			ret = -EFAULT;
			break;
		}
		key = event.key >= 1 && event.key <= ARRAY_SIZE(sf_keys) ?
			sf_keys[event.key - 1] : KEY_UNKNOWN;
		input_report_key(sf->input, key, !!event.value);
		input_sync(sf->input);
		break;
	default:
		/* Includes 0x7330 SET_VERSION, rejected by the actual stock driver. */
		ret = -EINVAL;
	}
out:
	mutex_unlock(&sf->lock);
	return ret;
}

#ifdef CONFIG_COMPAT
static long sf_compat_ioctl(struct file *file, unsigned int cmd, unsigned long arg)
{
	return sf_ioctl(file, cmd, (unsigned long)compat_ptr(arg));
}
#endif

static const struct file_operations sf_fops = {
	.owner = THIS_MODULE,
	.open = sf_open,
	.release = sf_release,
	.unlocked_ioctl = sf_ioctl,
#ifdef CONFIG_COMPAT
	.compat_ioctl = sf_compat_ioctl,
#endif
};

static int sf_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct sf_device *sf;
	int ret, i;

	sf = kzalloc_obj(*sf);
	if (!sf)
		return -ENOMEM;
	sf->dev = dev;
	mutex_init(&sf->lock);
	kref_init(&sf->refs);
	INIT_WORK(&sf->finger_work, sf_finger_work);
	INIT_WORK(&sf->logic_work, sf_logic_work);
	sf->power = devm_gpiod_get(dev, "power", GPIOD_OUT_HIGH);
	if (IS_ERR(sf->power)) {
		ret = PTR_ERR(sf->power);
		goto free;
	}
	msleep(10);
	sf->reset = devm_gpiod_get(dev, "reset", GPIOD_OUT_HIGH);
	if (IS_ERR(sf->reset)) {
		ret = PTR_ERR(sf->reset);
		goto power_off;
	}
	sf->finger = devm_gpiod_get(dev, "irq", GPIOD_IN);
	if (IS_ERR(sf->finger)) {
		ret = PTR_ERR(sf->finger);
		goto power_off;
	}
	sf->logic = devm_gpiod_get(dev, "logic", GPIOD_IN);
	if (IS_ERR(sf->logic)) {
		ret = PTR_ERR(sf->logic);
		goto power_off;
	}
	sf->irq = gpiod_to_irq(sf->finger);
	if (sf->irq < 0) {
		ret = sf->irq;
		goto power_off;
	}
	sf->logic_irq = gpiod_to_irq(sf->logic);
	if (sf->logic_irq < 0) {
		ret = sf->logic_irq;
		goto power_off;
	}
	sf_reset(sf);
	sf_logic_check(sf);
	sf->input = devm_input_allocate_device(dev);
	if (!sf->input) {
		ret = -ENOMEM;
		goto power_off;
	}
	sf->input->name = "sf-keys";
	for (i = 0; i < ARRAY_SIZE(sf_keys); i++)
		input_set_capability(sf->input, EV_KEY, sf_keys[i]);
	input_set_capability(sf->input, EV_KEY, KEY_UNKNOWN);
	ret = input_register_device(sf->input);
	if (ret)
		goto power_off;
	sf->misc = (struct miscdevice) {
		.minor = MISC_DYNAMIC_MINOR, .name = "sunwave_fp",
		.fops = &sf_fops, .parent = dev, .mode = 0600,
	};
	ret = misc_register(&sf->misc);
	if (ret)
		goto power_off;
	ret = device_init_wakeup(dev, true);
	if (ret)
		goto misc;
	ret = devm_request_irq(dev, sf->irq, sf_finger_irq,
			       IRQF_TRIGGER_FALLING, "sf-irq", sf);
	if (ret)
		goto wakeup;
	ret = devm_request_irq(dev, sf->logic_irq, sf_logic_irq,
			       IRQF_TRIGGER_RISING | IRQF_NO_SUSPEND,
			       "sf-logic-irq", sf);
	if (ret)
		goto finger_irq;
	/* Stock requests wake on both lines without making probe depend on it. */
	ret = enable_irq_wake(sf->irq);
	sf->irq_wake = !ret;
	if (ret)
		dev_warn(dev, "finger IRQ wake unavailable: %d\n", ret);
	ret = enable_irq_wake(sf->logic_irq);
	sf->logic_wake = !ret;
	if (ret)
		dev_warn(dev, "logic IRQ wake unavailable: %d\n", ret);
	platform_set_drvdata(pdev, sf);
	dev_info(dev, "stock control ABI ready; sensor I/O via sw_fp TA\n");
	return 0;
finger_irq:
	devm_free_irq(dev, sf->irq, sf);
	cancel_work_sync(&sf->finger_work);
	cancel_work_sync(&sf->logic_work);
wakeup:
	device_init_wakeup(dev, false);
misc:
	mutex_lock(&sf->lock);
	sf->dead = true;
	mutex_unlock(&sf->lock);
	misc_deregister(&sf->misc);
power_off:
	sf_power(sf, false);
free:
	kref_put(&sf->refs, sf_free);
	return dev_err_probe(dev, ret, "Sunwave control probe failed\n");
}

static void sf_remove(struct platform_device *pdev)
{
	struct sf_device *sf = platform_get_drvdata(pdev);

	mutex_lock(&sf->lock);
	sf->dead = true;
	mutex_unlock(&sf->lock);
	if (sf->irq_wake)
		disable_irq_wake(sf->irq);
	if (sf->logic_wake)
		disable_irq_wake(sf->logic_irq);
	devm_free_irq(&pdev->dev, sf->irq, sf);
	devm_free_irq(&pdev->dev, sf->logic_irq, sf);
	cancel_work_sync(&sf->finger_work);
	cancel_work_sync(&sf->logic_work);
	misc_deregister(&sf->misc);
	device_init_wakeup(&pdev->dev, false);
	sf_power(sf, false);
	kref_put(&sf->refs, sf_free);
}

static const struct of_device_id sf_of_match[] = {
	{ .compatible = "sunwave,fingerprint" }, {}
};
MODULE_DEVICE_TABLE(of, sf_of_match);

static struct platform_driver sf_driver = {
	.probe = sf_probe,
	.remove = sf_remove,
	.driver = {
		.name = "a9-sunwave", .of_match_table = sf_of_match,
		.suppress_bind_attrs = true,
	},
};
module_platform_driver(sf_driver);
MODULE_DESCRIPTION("Hisense A9 stock Sunwave GPIO/uevent control ABI");
MODULE_AUTHOR("A9 mainline contributors");
MODULE_LICENSE("GPL");
