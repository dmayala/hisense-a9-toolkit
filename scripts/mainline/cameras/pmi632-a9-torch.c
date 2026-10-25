// SPDX-License-Identifier: GPL-2.0-only
/* PMI632 torch0 only, 100mA. Register provenance: AF-TORCH.md. */
#include <linux/delay.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/spmi.h>
#include <linux/workqueue.h>
#include <media/v4l2-ctrls.h>
#include <media/v4l2-device.h>
#include <media/v4l2-event.h>

#define FLASH_BASE 0xd300
#define TORCH_LIMIT_MS 5000
struct a9_torch {
	struct v4l2_subdev sd;
	struct v4l2_ctrl_handler ctrls;
	struct regmap *map, *boost;
	struct mutex lock;
	struct delayed_work watch;
	unsigned long deadline;
	bool on, opened;
	u32 fault;
};

static int a9_off(struct a9_torch *t)
{
	int ret, module_ret;

	ret = regmap_update_bits(t->map, FLASH_BASE + 0x4c, 3, 0);
	module_ret = regmap_update_bits(t->map, FLASH_BASE + 0x46, BIT(7), 0);
	if (!ret && !module_ret)
		t->on = false;
	else
		t->fault |= V4L2_FLASH_FAULT_INPUT_VOLTAGE;
	return ret ? ret : module_ret;
}

static int a9_fault(struct a9_torch *t)
{
	unsigned int status1, status2, state;
	int ret;

	ret = regmap_read(t->map, FLASH_BASE + 0x08, &status1);
	if (!ret)
		ret = regmap_read(t->map, FLASH_BASE + 0x09, &status2);
	if (!ret)
		ret = regmap_read(t->boost, 0xa608, &state);
	if (ret)
		return ret;
	/* Stop even at the first thermal warning; no threshold is raised. */
	if ((status1 & 0x3f) || status2 || (state & 7) == 7) {
		t->fault = V4L2_FLASH_FAULT_OVER_TEMPERATURE |
			   V4L2_FLASH_FAULT_SHORT_CIRCUIT | V4L2_FLASH_FAULT_INPUT_VOLTAGE;
		return -EIO;
	}
	return 0;
}

static int a9_on(struct a9_torch *t)
{
	unsigned int status;
	int ret, i;
	/* All writes stay in the flash peripheral; boost map is read-only. */
	static const struct reg_sequence settings[] = {
		{ FLASH_BASE + 0x4d, 0xb4 }, /* stock 400mV headroom / 100mV window */
		{ FLASH_BASE + 0x50, 0x01 }, /* auto headroom, channel0 */
		{ FLASH_BASE + 0x40, 0x13 }, /* stock torch timer mode */
		{ FLASH_BASE + 0x43, 0x07 }, /* (7+1)*12.5mA = 100mA */
	};

	if (t->on)
		return 0; /* repeated requests never extend the absolute deadline */
	if (t->fault)
		return -EIO;
	ret = a9_fault(t);
	if (ret)
		return ret;
	ret = regmap_multi_reg_write(t->map, settings, ARRAY_SIZE(settings));
	if (ret)
		goto fail;
	ret = regmap_update_bits(t->map, FLASH_BASE + 0x47, 0x0f, 0); /* 12.5mA */
	if (!ret)
		ret = regmap_update_bits(t->map, FLASH_BASE + 0x49, BIT(2), 0); /* SW */
	if (!ret)
		ret = regmap_update_bits(t->map, FLASH_BASE + 0x46, BIT(7), BIT(7));
	if (ret)
		goto fail;
	/* PMI632 SCHGM VREG_OK, as downstream FLASH_TRIGGER. */
	for (i = 0; i < 60; i++) {
		usleep_range(5000, 5500);
		ret = a9_fault(t);
		if (ret)
			goto fail;
		ret = regmap_read(t->boost, 0xa607, &status);
		if (ret)
			goto fail;
		if (status & BIT(4))
			break;
	}
	if (i == 60) {
		ret = -ETIMEDOUT;
		goto fail;
	}
	ret = regmap_update_bits(t->map, FLASH_BASE + 0x4c, 3, 1);
	if (ret)
		goto fail;
	t->on = true;
	t->deadline = jiffies + msecs_to_jiffies(TORCH_LIMIT_MS);
	mod_delayed_work(system_wq, &t->watch, msecs_to_jiffies(100));
	return 0;
fail:
	a9_off(t);
	return ret;
}

static void a9_watch(struct work_struct *work)
{
	struct a9_torch *t = container_of(to_delayed_work(work), struct a9_torch, watch);

	mutex_lock(&t->lock);
	if (t->on) {
		if (time_after_eq(jiffies, t->deadline))
			t->fault |= V4L2_FLASH_FAULT_TIMEOUT;
		if (t->fault || a9_fault(t))
			a9_off(t);
		if (t->on)
			mod_delayed_work(system_wq, &t->watch, msecs_to_jiffies(100));
	}
	mutex_unlock(&t->lock);
}

static int a9_set(struct v4l2_ctrl *ctrl)
{
	struct a9_torch *t = container_of(ctrl->handler, struct a9_torch, ctrls);

	if (ctrl->id == V4L2_CID_FLASH_TORCH_INTENSITY)
		return 0; /* fixed stock default, no brighter setting */
	if (ctrl->id != V4L2_CID_FLASH_LED_MODE)
		return -EINVAL;
	if (ctrl->val == V4L2_FLASH_LED_MODE_TORCH)
		return a9_on(t);
	t->fault &= ~V4L2_FLASH_FAULT_TIMEOUT;
	return a9_off(t);
}

static int a9_get(struct v4l2_ctrl *ctrl)
{
	struct a9_torch *t = container_of(ctrl->handler, struct a9_torch, ctrls);
	unsigned int enabled;
	int ret;

	if (ctrl->id == V4L2_CID_FLASH_FAULT) {
		ctrl->val = t->fault;
		return 0;
	}
	ret = regmap_read(t->map, FLASH_BASE + 0x4c, &enabled);
	if (ret)
		return ret;
	ctrl->val = (enabled & 1) ? V4L2_FLASH_LED_MODE_TORCH : V4L2_FLASH_LED_MODE_NONE;
	return 0;
}
static const struct v4l2_ctrl_ops a9_ctrl_ops = { .s_ctrl = a9_set, .g_volatile_ctrl = a9_get };

static int a9_open(struct v4l2_subdev *sd, struct v4l2_subdev_fh *fh)
{
	struct a9_torch *t = container_of(sd, struct a9_torch, sd);
	int ret = 0;

	mutex_lock(&t->lock);
	if (t->opened)
		ret = -EBUSY;
	else
		t->opened = true;
	mutex_unlock(&t->lock);
	return ret;
}
static int a9_close(struct v4l2_subdev *sd, struct v4l2_subdev_fh *fh)
{
	struct a9_torch *t = container_of(sd, struct a9_torch, sd);
	int ret;

	mutex_lock(&t->lock);
	ret = a9_off(t);
	t->opened = false;
	mutex_unlock(&t->lock);
	return ret;
}
static const struct v4l2_subdev_internal_ops a9_internal_ops = { .open = a9_open, .close = a9_close };
static const struct v4l2_subdev_core_ops a9_core_ops = {
	.log_status = v4l2_ctrl_subdev_log_status,
	.subscribe_event = v4l2_ctrl_subdev_subscribe_event,
	.unsubscribe_event = v4l2_event_subdev_unsubscribe,
};
static const struct v4l2_subdev_ops a9_ops = { .core = &a9_core_ops };
static void a9_put_boost(void *data) { put_device(data); }

static int a9_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct device_node *node;
	struct spmi_device *boost;
	struct a9_torch *t;
	struct v4l2_ctrl *mode;
	unsigned int type, subtype;
	int ret;

	t = devm_kzalloc(dev, sizeof(*t), GFP_KERNEL);
	if (!t)
		return -ENOMEM;
	t->map = dev_get_regmap(dev->parent, NULL);
	if (!t->map)
		return -EPROBE_DEFER;
	node = of_parse_phandle(dev->of_node, "hisense,boost", 0);
	if (!node)
		return -EINVAL;
	{
		struct device_node *pmic = of_get_parent(node);
		boost = spmi_find_device_by_of_node(pmic);
		of_node_put(pmic);
	}
	of_node_put(node);
	if (!boost)
		return -EPROBE_DEFER;
	ret = devm_add_action_or_reset(dev, a9_put_boost, &boost->dev);
	if (ret)
		return ret;
	t->boost = dev_get_regmap(&boost->dev, NULL);
	if (!t->boost)
		return -EPROBE_DEFER;
	ret = regmap_read(t->map, FLASH_BASE + 4, &type);
	if (!ret)
		ret = regmap_read(t->map, FLASH_BASE + 5, &subtype);
	if (ret)
		return ret;
	if (type != 0x18)
		return -ENODEV;
	dev_info(dev, "PMI632 flash type=%#x subtype=%#x torch0 100mA max5s\n", type, subtype);
	mutex_init(&t->lock);
	INIT_DELAYED_WORK(&t->watch, a9_watch);
	ret = a9_off(t);
	/* Stock short/open/droop detectors and thermal ramp backup stay enabled. */
	if (!ret)
		ret = regmap_update_bits(t->map, FLASH_BASE + 0x76, 7, 7);
	if (!ret)
		ret = regmap_update_bits(t->map, FLASH_BASE + 0x55, BIT(7), BIT(7));
	if (!ret)
		ret = regmap_update_bits(t->map, FLASH_BASE + 0x52, 3, 3);
	if (!ret)
		ret = regmap_update_bits(t->map, FLASH_BASE + 0x51, 3, 2);
	if (ret)
		return ret;
	v4l2_subdev_init(&t->sd, &a9_ops);
	t->sd.dev = dev;
	t->sd.owner = THIS_MODULE;
	t->sd.fwnode = dev_fwnode(dev);
	t->sd.flags = V4L2_SUBDEV_FL_HAS_DEVNODE | V4L2_SUBDEV_FL_HAS_EVENTS;
	t->sd.internal_ops = &a9_internal_ops;
	strscpy(t->sd.name, "a9-rear-torch", sizeof(t->sd.name));
	t->sd.entity.function = MEDIA_ENT_F_FLASH;
	v4l2_ctrl_handler_init(&t->ctrls, 3);
	t->ctrls.lock = &t->lock;
	mode = v4l2_ctrl_new_std_menu(&t->ctrls, &a9_ctrl_ops, V4L2_CID_FLASH_LED_MODE,
		V4L2_FLASH_LED_MODE_TORCH, BIT(V4L2_FLASH_LED_MODE_FLASH), V4L2_FLASH_LED_MODE_NONE);
	if (mode)
		mode->flags |= V4L2_CTRL_FLAG_VOLATILE | V4L2_CTRL_FLAG_EXECUTE_ON_WRITE;
	v4l2_ctrl_new_std(&t->ctrls, &a9_ctrl_ops, V4L2_CID_FLASH_TORCH_INTENSITY,
		100000, 100000, 1, 100000);
	mode = v4l2_ctrl_new_std(&t->ctrls, &a9_ctrl_ops, V4L2_CID_FLASH_FAULT, 0, 0x3ff, 0, 0);
	if (mode)
		mode->flags |= V4L2_CTRL_FLAG_VOLATILE | V4L2_CTRL_FLAG_READ_ONLY;
	t->sd.ctrl_handler = &t->ctrls;
	ret = t->ctrls.error;
	if (!ret)
		ret = media_entity_pads_init(&t->sd.entity, 0, NULL);
	if (!ret)
		ret = v4l2_async_register_subdev(&t->sd);
	if (ret) {
		v4l2_ctrl_handler_free(&t->ctrls);
		media_entity_cleanup(&t->sd.entity);
		return ret;
	}
	platform_set_drvdata(pdev, t);
	return 0;
}
static void a9_remove(struct platform_device *pdev)
{
	struct a9_torch *t = platform_get_drvdata(pdev);

	v4l2_async_unregister_subdev(&t->sd);
	cancel_delayed_work_sync(&t->watch);
	a9_off(t);
	v4l2_ctrl_handler_free(&t->ctrls);
	media_entity_cleanup(&t->sd.entity);
}
static int a9_suspend(struct device *dev)
{
	struct a9_torch *t = dev_get_drvdata(dev);
	int ret;

	cancel_delayed_work_sync(&t->watch);
	mutex_lock(&t->lock);
	ret = a9_off(t);
	mutex_unlock(&t->lock);
	return ret;
}
static const struct dev_pm_ops a9_pm = { .suspend = a9_suspend, .freeze = a9_suspend, .poweroff = a9_suspend };
static const struct of_device_id a9_match[] = { { .compatible = "hisense,a9-pmi632-torch" }, { } };
MODULE_DEVICE_TABLE(of, a9_match);
static struct platform_driver a9_driver = {
	.probe = a9_probe, .remove = a9_remove,
	.driver = { .name = "pmi632-a9-torch", .of_match_table = a9_match, .pm = &a9_pm },
};
module_platform_driver(a9_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Hisense A9 PMI632 bounded torch");
