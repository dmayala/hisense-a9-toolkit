// SPDX-License-Identifier: GPL-2.0-only
/* Board-specific raw frontlight LEDs for the Hisense A9. */
#include <linux/delay.h>
#include <linux/err.h>
#include <linux/gpio/consumer.h>
#include <linux/i2c.h>
#include <linux/leds.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/property.h>

#include "a9-frontlight-core.h"

struct a9_frontlight {
	struct i2c_client *client;
	struct gpio_desc *enable;
	struct mutex lock;
	struct led_classdev led;
	struct a9_fl core;
};

static int a9_frontlight_read(void *ctx, unsigned int reg)
{
	struct a9_frontlight *fl = ctx;
	int i, ret;

	for (i = 0; i < 5; i++) {
		ret = i2c_smbus_read_byte_data(fl->client, reg);
		if (ret >= 0)
			return ret;
		usleep_range(2000, 2500);
	}
	dev_warn_ratelimited(&fl->client->dev,
			     "read register 0x%02x failed after 5 attempts: %d\n",
			     reg, ret);
	return ret;
}

static int a9_frontlight_write(void *ctx, unsigned int reg, unsigned int val)
{
	struct a9_frontlight *fl = ctx;
	int i, ret;

	for (i = 0; i < 5; i++) {
		ret = i2c_smbus_write_byte_data(fl->client, reg, val);
		if (!ret)
			return 0;
		usleep_range(2000, 2500);
	}
	return ret;
}

static int a9_frontlight_enable(void *ctx, int on)
{
	struct a9_frontlight *fl = ctx;

	return gpiod_direction_output(fl->enable, on);
}

static void a9_frontlight_settle(void *ctx)
{
	/* Stock AW99703 delay; also exceeds KTD3137's 100 us minimum. */
	usleep_range(3500, 4000);
}

static int a9_frontlight_set(struct led_classdev *led, enum led_brightness value)
{
	struct a9_frontlight *fl = container_of(led, struct a9_frontlight, led);
	int ret;

	mutex_lock(&fl->lock);
	ret = a9_fl_set(&fl->core, value);
	mutex_unlock(&fl->lock);
	if (ret)
		dev_err_ratelimited(&fl->client->dev, "brightness %u failed: %d\n",
				   value, ret);
	return ret;
}

static enum led_brightness a9_frontlight_get(struct led_classdev *led)
{
	struct a9_frontlight *fl = container_of(led, struct a9_frontlight, led);
	unsigned int value;

	mutex_lock(&fl->lock);
	/* Preserve the LED core's resume target during system suspend. */
	value = led->flags & LED_SUSPENDED ? led->brightness : fl->core.level;
	mutex_unlock(&fl->lock);
	return value;
}

static const enum a9_fl_kind aw_kind = A9_FL_AW99703;
static const enum a9_fl_kind ktd_kind = A9_FL_KTD3137;

static int a9_frontlight_probe(struct i2c_client *client)
{
	struct device *dev = &client->dev;
	const enum a9_fl_kind *kind = device_get_match_data(dev);
	struct a9_frontlight *fl;
	const char *name, *state;
	int ret, enabled;

	if (!kind || !i2c_check_functionality(client->adapter,
					     I2C_FUNC_SMBUS_BYTE_DATA))
		return -EOPNOTSUPP;
	ret = device_property_read_string(dev, "label", &name);
	if (ret)
		return ret;
	if ((*kind == A9_FL_AW99703 && strcmp(name, "a9:warm:center") &&
	     strcmp(name, "a9:white:center")) ||
	    (*kind == A9_FL_KTD3137 && strcmp(name, "a9:warm:edge") &&
	     strcmp(name, "a9:white:edge")))
		return dev_err_probe(dev, -EINVAL, "Invalid A9 LED label\n");
	ret = device_property_read_string(dev, "default-state", &state);
	if (ret || strcmp(state, "keep"))
		return dev_err_probe(dev, -EINVAL, "default-state must be keep\n");
	fl = devm_kzalloc(dev, sizeof(*fl), GFP_KERNEL);
	if (!fl)
		return -ENOMEM;
	fl->client = client;
	mutex_init(&fl->lock);
	/* Never request OUT_LOW: that would flash an already lit bootloader LED. */
	fl->enable = devm_gpiod_get(dev, "enable", GPIOD_ASIS);
	if (IS_ERR(fl->enable))
		return dev_err_probe(dev, PTR_ERR(fl->enable), "HWEN unavailable\n");
	if (gpiod_is_active_low(fl->enable))
		return dev_err_probe(dev, -EINVAL, "HWEN must be active high\n");
	enabled = gpiod_get_value_cansleep(fl->enable);
	if (enabled < 0)
		return enabled;
	fl->core.kind = *kind;
	fl->core.io = (struct a9_fl_io) {
		.ctx = fl,
		.read = a9_frontlight_read,
		.write = a9_frontlight_write,
		.enable = a9_frontlight_enable,
		.settle = a9_frontlight_settle,
	};
	ret = a9_fl_adopt(&fl->core, enabled);
	if (ret)
		return dev_err_probe(dev, ret,
				     "Cannot safely adopt boot state (sampled HWEN=%d)\n",
				     enabled);
	/* Drive the sampled level without an edge; no HWEN pinctrl output preset. */
	ret = gpiod_direction_output(fl->enable, enabled);
	if (ret)
		return ret;
	fl->led.name = name;
	fl->led.max_brightness = A9_FL_MAX;
	fl->led.brightness = fl->core.level;
	fl->led.brightness_set_blocking = a9_frontlight_set;
	fl->led.brightness_get = a9_frontlight_get;
	fl->led.flags = LED_CORE_SUSPENDRESUME | LED_REJECT_NAME_CONFLICT;
	i2c_set_clientdata(client, fl);
	ret = devm_led_classdev_register(dev, &fl->led);
	if (ret)
		return ret;
	dev_info(dev, "%s: kept raw brightness %u (HWEN=%d)\n",
		 name, fl->core.level, enabled);
	return 0;
}

static void a9_frontlight_shutdown(struct i2c_client *client)
{
	struct a9_frontlight *fl = i2c_get_clientdata(client);

	/* Stop triggers/work before the final off, including during shutdown. */
	devm_led_classdev_unregister(&client->dev, &fl->led);
	a9_frontlight_set(&fl->led, LED_OFF);
}

static const struct of_device_id a9_frontlight_of_match[] = {
	{ .compatible = "hisense,a9-aw99703-frontlight", .data = &aw_kind },
	{ .compatible = "hisense,a9-ktd3137-frontlight", .data = &ktd_kind },
	{ }
};
MODULE_DEVICE_TABLE(of, a9_frontlight_of_match);

static struct i2c_driver a9_frontlight_driver = {
	.driver = {
		.name = "a9-frontlight",
		.of_match_table = a9_frontlight_of_match,
	},
	.probe = a9_frontlight_probe,
	.remove = a9_frontlight_shutdown,
	.shutdown = a9_frontlight_shutdown,
};
module_i2c_driver(a9_frontlight_driver);

MODULE_DESCRIPTION("Hisense A9 raw AW99703 and KTD3137 frontlight LEDs");
MODULE_LICENSE("GPL");
