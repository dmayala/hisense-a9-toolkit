// SPDX-License-Identifier: GPL-2.0-only
/* A9 register-addressed DW9714P. Stock records and offsets: AF-TORCH.md. */
#include <linux/delay.h>
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/pm_runtime.h>
#include <linux/regulator/consumer.h>
#include <media/v4l2-ctrls.h>
#include <media/v4l2-device.h>
#include <media/v4l2-event.h>

#define A9_AF_MIN 116
#define A9_AF_MAX 515

struct a9_lens {
	struct v4l2_subdev sd;
	struct v4l2_ctrl_handler ctrls;
	struct regulator *vcc;
	u16 position;
};

static int a9_write(struct a9_lens *lens, u8 reg, u16 value, bool word)
{
	struct i2c_client *client = v4l2_get_subdevdata(&lens->sd);
	u8 data[] = { reg, word ? value >> 8 : value, value & 0xff };
	int size = word ? 3 : 2;
	int ret = i2c_master_send(client, data, size);

	return ret == size ? 0 : (ret < 0 ? ret : -EIO);
}

static int a9_position(struct a9_lens *lens, u16 value)
{
	int ret = a9_write(lens, 0x03, value, true);

	if (!ret)
		lens->position = value;
	return ret;
}

static int a9_set_ctrl(struct v4l2_ctrl *ctrl)
{
	struct a9_lens *lens = container_of(ctrl->handler, struct a9_lens, ctrls);

	if (ctrl->id != V4L2_CID_FOCUS_ABSOLUTE)
		return -EINVAL;
	return a9_position(lens, ctrl->val);
}

static const struct v4l2_ctrl_ops a9_ctrl_ops = { .s_ctrl = a9_set_ctrl };

static int a9_resume(struct device *dev)
{
	struct a9_lens *lens = container_of(i2c_get_clientdata(to_i2c_client(dev)),
					  struct a9_lens, sd);
	static const u8 init[][2] = {
		{ 0xed, 0xab }, { 0x02, 0x01 }, { 0x02, 0x00 },
		{ 0x06, 0x84 }, { 0x07, 0x01 }, { 0x08, 0x71 },
	};
	int ret, i;

	ret = regulator_enable(lens->vcc);
	if (ret)
		return ret;
	usleep_range(1000, 1500);
	for (i = 0; i < ARRAY_SIZE(init); i++) {
		ret = a9_write(lens, init[i][0], init[i][1], false);
		if (ret)
			goto fail;
		if (i == 2)
			usleep_range(100, 200);
	}
	ret = a9_position(lens, A9_AF_MIN);
	if (ret)
		goto fail;
	usleep_range(7000, 7500);
	return 0;
fail:
	regulator_disable(lens->vcc);
	return ret;
}

static int a9_suspend(struct device *dev)
{
	struct a9_lens *lens = container_of(i2c_get_clientdata(to_i2c_client(dev)),
					  struct a9_lens, sd);
	int ret = 0, power_ret;

	/* Ramp to the stock initial code before standby; never jump to DAC zero. */
	while (lens->position > A9_AF_MIN) {
		u16 next = max_t(int, A9_AF_MIN, lens->position - 32);

		ret = a9_position(lens, next);
		if (ret)
			break;
		usleep_range(7000, 7500);
	}
	if (!ret)
		ret = a9_write(lens, 0x02, 0x01, false);
	power_ret = regulator_disable(lens->vcc);
	return ret ? ret : power_ret;
}

static int a9_open(struct v4l2_subdev *sd, struct v4l2_subdev_fh *fh)
{
	return pm_runtime_resume_and_get(sd->dev);
}

static int a9_close(struct v4l2_subdev *sd, struct v4l2_subdev_fh *fh)
{
	pm_runtime_put(sd->dev);
	return 0;
}

static const struct v4l2_subdev_internal_ops a9_internal_ops = {
	.open = a9_open, .close = a9_close,
};
static const struct v4l2_subdev_core_ops a9_core_ops = {
	.log_status = v4l2_ctrl_subdev_log_status,
	.subscribe_event = v4l2_ctrl_subdev_subscribe_event,
	.unsubscribe_event = v4l2_event_subdev_unsubscribe,
};
static const struct v4l2_subdev_ops a9_ops = { .core = &a9_core_ops };

static int a9_probe(struct i2c_client *client)
{
	struct a9_lens *lens;
	struct v4l2_ctrl *focus;
	int ret;

	lens = devm_kzalloc(&client->dev, sizeof(*lens), GFP_KERNEL);
	if (!lens)
		return -ENOMEM;
	lens->vcc = devm_regulator_get(&client->dev, "vcc");
	if (IS_ERR(lens->vcc))
		return dev_err_probe(&client->dev, PTR_ERR(lens->vcc), "AF supply\n");
	v4l2_i2c_subdev_init(&lens->sd, client, &a9_ops);
	lens->sd.flags = V4L2_SUBDEV_FL_HAS_DEVNODE | V4L2_SUBDEV_FL_HAS_EVENTS;
	lens->sd.internal_ops = &a9_internal_ops;
	lens->sd.entity.function = MEDIA_ENT_F_LENS;
	lens->position = A9_AF_MIN;
	v4l2_ctrl_handler_init(&lens->ctrls, 1);
	focus = v4l2_ctrl_new_std(&lens->ctrls, &a9_ctrl_ops, V4L2_CID_FOCUS_ABSOLUTE,
			 A9_AF_MIN, A9_AF_MAX, 1, A9_AF_MIN);
	if (focus)
		focus->flags |= V4L2_CTRL_FLAG_EXECUTE_ON_WRITE;
	lens->sd.ctrl_handler = &lens->ctrls;
	ret = lens->ctrls.error;
	if (ret)
		goto fail;
	ret = media_entity_pads_init(&lens->sd.entity, 0, NULL);
	if (ret)
		goto fail;
	pm_runtime_set_suspended(&client->dev);
	pm_runtime_enable(&client->dev);
	ret = v4l2_async_register_subdev(&lens->sd);
	if (!ret)
		return 0;
	pm_runtime_disable(&client->dev);
fail:
	v4l2_ctrl_handler_free(&lens->ctrls);
	media_entity_cleanup(&lens->sd.entity);
	return ret;
}

static void a9_remove(struct i2c_client *client)
{
	struct a9_lens *lens = container_of(i2c_get_clientdata(client), struct a9_lens, sd);

	v4l2_async_unregister_subdev(&lens->sd);
	pm_runtime_disable(&client->dev);
	if (!pm_runtime_status_suspended(&client->dev))
		a9_suspend(&client->dev);
	v4l2_ctrl_handler_free(&lens->ctrls);
	media_entity_cleanup(&lens->sd.entity);
}

static const struct dev_pm_ops a9_pm = {
	SET_RUNTIME_PM_OPS(a9_suspend, a9_resume, NULL)
	SET_SYSTEM_SLEEP_PM_OPS(pm_runtime_force_suspend, pm_runtime_force_resume)
};
static const struct of_device_id a9_of[] = {
	{ .compatible = "hisense,a9-dw9714p" }, { }
};
MODULE_DEVICE_TABLE(of, a9_of);
static struct i2c_driver a9_driver = {
	.driver = { .name = "dw9714p-a9", .pm = &a9_pm, .of_match_table = a9_of },
	.probe = a9_probe, .remove = a9_remove,
};
module_i2c_driver(a9_driver);
MODULE_DESCRIPTION("Hisense A9 DW9714P lens");
MODULE_LICENSE("GPL");
