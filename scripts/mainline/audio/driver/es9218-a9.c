// SPDX-License-Identifier: GPL-2.0-only
/* Experimental Hisense A9 ESS control path recovered from shipped stock code. */
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/pm.h>
#include <linux/regmap.h>
#include <sound/pcm_params.h>
#include <sound/soc.h>
#include "es9218-a9-seq.h"

struct es9218_a9 {
 struct mutex lock;
 struct regmap *regmap;
 struct gpio_desc *pins[4];
 struct es9218_seq seq;
 bool fmt_valid;
 bool dapm_active;
 unsigned int users;
 int fault;
 unsigned int volume[2];
 /* Last attended self test (bringup=1 only). */
 int st_level, st_ret;
 unsigned int st_id;
 unsigned char st_dump[ES9218_DUMP_LEN];
};

static bool bringup;
module_param(bringup, bool, 0444);
MODULE_PARM_DESC(bringup, "allow the root-only selftest attribute (attended bring-up only)");

static int es_write(void *ctx, unsigned int reg, unsigned int val)
{
 struct es9218_a9 *es = ctx;
 return regmap_write(es->regmap, reg, val);
}
static int es_read(void *ctx, unsigned int reg, unsigned int *val)
{
 struct es9218_a9 *es = ctx;
 return regmap_read(es->regmap, reg, val);
}
static void es_gpio(void *ctx, enum es9218_pin pin, int level)
{
 struct es9218_a9 *es = ctx;
 /* reset-gpios is active-low; the recovered sequence records physical levels. */
 gpiod_set_value_cansleep(es->pins[pin], pin == ES_RESET ? !level : level);
}
static void es_delay(void *ctx, unsigned int min_us, unsigned int max_us)
{
 usleep_range(min_us, max_us);
}
static const struct es9218_io es_io = {
 .write = es_write, .read = es_read, .gpio = es_gpio, .delay = es_delay,
};

static int es_set_fmt(struct snd_soc_dai *dai, unsigned int fmt)
{
 struct es9218_a9 *es = snd_soc_component_get_drvdata(dai->component);
 if ((fmt & SND_SOC_DAIFMT_FORMAT_MASK) != SND_SOC_DAIFMT_I2S ||
     (fmt & SND_SOC_DAIFMT_INV_MASK) != SND_SOC_DAIFMT_NB_NF ||
     (fmt & SND_SOC_DAIFMT_CLOCK_PROVIDER_MASK) != SND_SOC_DAIFMT_BC_FC)
  return -EINVAL;
 mutex_lock(&es->lock);
 es->fmt_valid = true;
 mutex_unlock(&es->lock);
 return 0;
}
static int es_hw_params(struct snd_pcm_substream *substream,
                        struct snd_pcm_hw_params *params, struct snd_soc_dai *dai)
{
 struct es9218_a9 *es = snd_soc_component_get_drvdata(dai->component);
 int ret;
 if (params_format(params) != SNDRV_PCM_FORMAT_S16_LE &&
     params_format(params) != SNDRV_PCM_FORMAT_S24_LE &&
     params_format(params) != SNDRV_PCM_FORMAT_S32_LE)
  return -EINVAL;
 ret = es9218_validate_params(params_rate(params), params_channels(params),
                              params_width(params));
 if (ret) return ret;
 mutex_lock(&es->lock);
 if (!es->fmt_valid || es->seq.mode != ES_STANDBY) ret = -EBUSY;
 else es->seq.width = params_width(params);
 mutex_unlock(&es->lock);
 return ret;
}
static int es_dai_mute(struct snd_soc_dai *dai, int mute, int direction)
{
 struct es9218_a9 *es = snd_soc_component_get_drvdata(dai->component);
 int ret;
 if (direction != SNDRV_PCM_STREAM_PLAYBACK) return -EINVAL;
 mutex_lock(&es->lock);
 ret = es9218_mute(&es->seq, !!mute);
 if (ret) es->fault = ret;
 mutex_unlock(&es->lock);
 return ret;
}
static int es_trigger(struct snd_pcm_substream *s, int cmd, struct snd_soc_dai *dai)
{
 struct es9218_a9 *es = snd_soc_component_get_drvdata(dai->component);
 int fault = READ_ONCE(es->fault);
 switch (cmd) {
 case SNDRV_PCM_TRIGGER_START:
 case SNDRV_PCM_TRIGGER_RESUME:
 case SNDRV_PCM_TRIGGER_PAUSE_RELEASE:
  return fault ? fault : (READ_ONCE(es->seq.mode) == ES_HIFI ? 0 : -EIO);
 default:
  return 0;
 }
}
static int es_startup(struct snd_pcm_substream *s, struct snd_soc_dai *dai)
{
 struct es9218_a9 *es = snd_soc_component_get_drvdata(dai->component);
 int ret = 0;
 mutex_lock(&es->lock);
 if (es->users || es->seq.mode != ES_STANDBY || es->dapm_active) ret = -EBUSY;
 else es->users = 1;
 mutex_unlock(&es->lock);
 if (!ret) usleep_range(100000, 110000);
 return ret;
}
static void es_pcm_shutdown(struct snd_pcm_substream *s, struct snd_soc_dai *dai)
{
 struct es9218_a9 *es = snd_soc_component_get_drvdata(dai->component);
 int ret;
 mutex_lock(&es->lock);
 ret = es9218_stop(&es->seq);
 if (ret) es->fault = ret;
 es->users = 0;
 es->seq.width = 0;
 mutex_unlock(&es->lock);
}
static int es_dai_probe(struct snd_soc_dai *dai)
{
 dev_info(dai->dev, "A9-ESS DAI probe: %s rc=0\n", dai->name);
 return 0;
}
static const struct snd_soc_dai_ops es_dai_ops = {
 .probe = es_dai_probe,
 .startup = es_startup,
 .shutdown = es_pcm_shutdown,
 .trigger = es_trigger,
 .set_fmt = es_set_fmt,
 .hw_params = es_hw_params,
 .mute_stream = es_dai_mute,
 .no_capture_mute = 1,
};
static struct snd_soc_dai_driver es_dai = {
 .name = "es9218-a9-hifi",
 .playback = {
  .stream_name = "HiFi Playback",
  .channels_min = 2, .channels_max = 2,
  .rates = SNDRV_PCM_RATE_48000,
  .formats = SNDRV_PCM_FMTBIT_S16_LE | SNDRV_PCM_FMTBIT_S24_LE | SNDRV_PCM_FMTBIT_S32_LE,
 },
 .ops = &es_dai_ops,
};
static int es_dac_event(struct snd_soc_dapm_widget *w,
                         struct snd_kcontrol *kcontrol, int event)
{
 struct snd_soc_component *c = snd_soc_dapm_to_component(w->dapm);
 struct es9218_a9 *es = snd_soc_component_get_drvdata(c);
 int ret;
 mutex_lock(&es->lock);
 if (event == SND_SOC_DAPM_POST_PMU) {
  es->dapm_active = true;
  ret = es9218_hifi_on(&es->seq);
  if (!ret) ret = es_write(es, 0x0f, es->volume[0]);
  if (!ret) ret = es_write(es, 0x10, es->volume[1]);
  if (ret) es9218_power_off(&es->seq);
  es->fault = ret;
 } else {
  ret = es9218_stop(&es->seq);
  /* DAPM may cycle a route while the PCM still owns its hw_params. */
  es->dapm_active = false;
 }
 mutex_unlock(&es->lock);
 return ret;
}
static int es_bypass_get(struct snd_kcontrol *k, struct snd_ctl_elem_value *u)
{
 struct es9218_a9 *es = snd_soc_component_get_drvdata(snd_kcontrol_chip(k));
 mutex_lock(&es->lock);
 u->value.integer.value[0] = es->seq.mode == ES_BYPASS;
 mutex_unlock(&es->lock);
 return 0;
}
static int es_bypass_put(struct snd_kcontrol *k, struct snd_ctl_elem_value *u)
{
 struct es9218_a9 *es = snd_soc_component_get_drvdata(snd_kcontrol_chip(k));
 bool bypass = !!u->value.integer.value[0];
 int ret = 0;
 mutex_lock(&es->lock);
 if (es->users || es->dapm_active || es->seq.mode == ES_HIFI) ret = -EBUSY;
 else if (bypass != (es->seq.mode == ES_BYPASS)) {
  ret = bypass ? es9218_set_bypass(&es->seq) : es9218_power_off(&es->seq);
  if (!ret) ret = 1;
 }
 mutex_unlock(&es->lock);
 return ret;
}
static int es_volume_info(struct snd_kcontrol *k, struct snd_ctl_elem_info *u)
{
 u->type = SNDRV_CTL_ELEM_TYPE_INTEGER;
 u->count = 2;
 u->value.integer.min = 0;
 u->value.integer.max = 255;
 return 0;
}
static int es_volume_get(struct snd_kcontrol *k, struct snd_ctl_elem_value *u)
{
 struct es9218_a9 *es = snd_soc_component_get_drvdata(snd_kcontrol_chip(k));
 mutex_lock(&es->lock);
 u->value.integer.value[0] = es->volume[0];
 u->value.integer.value[1] = es->volume[1];
 mutex_unlock(&es->lock);
 return 0;
}
static int es_volume_put(struct snd_kcontrol *k, struct snd_ctl_elem_value *u)
{
 struct es9218_a9 *es = snd_soc_component_get_drvdata(snd_kcontrol_chip(k));
 long l = u->value.integer.value[0], r = u->value.integer.value[1];
 int ret = 0;
 if (l < 0 || l > 255 || r < 0 || r > 255) return -EINVAL;
 mutex_lock(&es->lock);
 if (l != es->volume[0] || r != es->volume[1]) {
  if (es->seq.mode == ES_HIFI) {
   ret = es_write(es, 0x0f, l);
   if (!ret) ret = es_write(es, 0x10, r);
   if (ret) { es->fault = ret; es9218_power_off(&es->seq); }
  }
  if (!ret) { es->volume[0] = l; es->volume[1] = r; ret = 1; }
 }
 mutex_unlock(&es->lock);
 return ret;
}
static const struct snd_kcontrol_new es_controls[] = {
 SOC_SINGLE_BOOL_EXT("ES9218 Bypass Switch", 0, es_bypass_get, es_bypass_put),
 { .iface = SNDRV_CTL_ELEM_IFACE_MIXER, .name = "ES9218 Digital Volume",
   .info = es_volume_info, .get = es_volume_get, .put = es_volume_put },
};
static const struct snd_soc_dapm_widget es_widgets[] = {
 SND_SOC_DAPM_DAC_E("HiFi DAC", "HiFi Playback", SND_SOC_NOPM, 0, 0,
                     es_dac_event, SND_SOC_DAPM_POST_PMU | SND_SOC_DAPM_PRE_PMD),
 SND_SOC_DAPM_OUTPUT("HPOUTL"), SND_SOC_DAPM_OUTPUT("HPOUTR"),
};
static const struct snd_soc_dapm_route es_routes[] = {
 { "HPOUTL", NULL, "HiFi DAC" }, { "HPOUTR", NULL, "HiFi DAC" },
};
static int es_component_probe(struct snd_soc_component *c)
{
 dev_info(c->dev, "A9-ESS component probe: %s rc=0, standby\n", c->name);
 return 0;
}
static const struct snd_soc_component_driver es_component = {
 .probe = es_component_probe,
 .controls = es_controls, .num_controls = ARRAY_SIZE(es_controls),
 .dapm_widgets = es_widgets, .num_dapm_widgets = ARRAY_SIZE(es_widgets),
 .dapm_routes = es_routes, .num_dapm_routes = ARRAY_SIZE(es_routes),
 .endianness = 1,
};
static const struct regmap_config es_regmap = {
 .reg_bits = 8, .val_bits = 8, .max_register = 0x40,
 .cache_type = REGCACHE_NONE,
};
/*
 * echo id|init > /sys/bus/i2c/devices/<bus>-0048/selftest  (root, bringup=1).
 * Refused while a PCM or DAPM path owns the codec; always ends in Standby.
 */
static ssize_t selftest_store(struct device *dev, struct device_attribute *attr,
                              const char *buf, size_t count)
{
 struct es9218_a9 *es = dev_get_drvdata(dev);
 unsigned int id = ES9218_ID_NONE;
 int level, ret;
 if (!bringup) return -EPERM;
 if (sysfs_streq(buf, "id")) level = ES_SELFTEST_ID;
 else if (sysfs_streq(buf, "init")) level = ES_SELFTEST_INIT;
 else return -EINVAL;
 mutex_lock(&es->lock);
 if (es->users || es->dapm_active || es->seq.mode != ES_STANDBY) {
  ret = -EBUSY;
 } else {
  memset(es->st_dump, 0, sizeof(es->st_dump));
  ret = es9218_selftest(&es->seq, level, &es->st_id, es->st_dump);
  es->st_level = level;
  es->st_ret = ret;
  id = es->st_id;
 }
 mutex_unlock(&es->lock);
 dev_info(dev, "selftest %s: ret=%d id=0x%x\n", level == ES_SELFTEST_ID ? "id" : "init",
          ret, id);
 return ret ? ret : (ssize_t)count;
}
static ssize_t selftest_show(struct device *dev, struct device_attribute *attr, char *buf)
{
 struct es9218_a9 *es = dev_get_drvdata(dev);
 int i, n;
 mutex_lock(&es->lock);
 n = sysfs_emit(buf, "level=%s ret=%d id=0x%x match=%d mode=%d\n",
                es->st_level == ES_SELFTEST_INIT ? "init" : es->st_level == ES_SELFTEST_ID ? "id" : "none",
                es->st_ret, es->st_id,
                es->st_id != ES9218_ID_NONE && (es->st_id & 0xfc) == 0xc0, es->seq.mode);
 if (es->st_level == ES_SELFTEST_INIT && !es->st_ret) {
  n += sysfs_emit_at(buf, n, "regs=");
  for (i = 0; i < ES9218_DUMP_LEN; i++)
   n += sysfs_emit_at(buf, n, "%02x:%02x%c", i, es->st_dump[i], i + 1 < ES9218_DUMP_LEN ? ' ' : '\n');
 }
 mutex_unlock(&es->lock);
 return n;
}
static DEVICE_ATTR_RW(selftest);
static struct attribute *es_attrs[] = { &dev_attr_selftest.attr, NULL };
ATTRIBUTE_GROUPS(es);

static void es_off_action(void *ctx)
{
 struct es9218_a9 *es = ctx;
 mutex_lock(&es->lock);
 es9218_power_off(&es->seq);
 mutex_unlock(&es->lock);
}
static int es_probe(struct i2c_client *client)
{
 static const char * const names[] = { "reset", "select", "oscillator-enable", "pa-enable" };
 const int initial[] = { 1, 1, 0, 0 };
 const unsigned int order[] = { ES_PA, ES_OSC, ES_RESET, ES_SELECT };
 struct device *dev = &client->dev;
 struct es9218_a9 *es;
 int ret, i;
 unsigned int pin;
 dev_info(dev, "A9-ESS I2C probe enter\n");
 es = devm_kzalloc(dev, sizeof(*es), GFP_KERNEL);
 if (!es) return dev_err_probe(dev, -ENOMEM, "A9-ESS allocation failed\n");
 es->regmap = devm_regmap_init_i2c(client, &es_regmap);
 if (IS_ERR(es->regmap)) return dev_err_probe(dev, PTR_ERR(es->regmap), "A9-ESS regmap failed\n");
 dev_info(dev, "A9-ESS regmap ready; configure standby GPIOs\n");
 /* Disable owned power first. Validate polarity before driving any GPIO. */
 for (i = 0; i < ARRAY_SIZE(order); i++) {
  pin = order[i];
  es->pins[pin] = devm_gpiod_get(dev, names[pin], GPIOD_ASIS);
  if (IS_ERR(es->pins[pin])) return dev_err_probe(dev, PTR_ERR(es->pins[pin]), "missing %s GPIO\n", names[pin]);
  if (gpiod_is_active_low(es->pins[pin]) != (pin == ES_RESET))
   return dev_err_probe(dev, -EINVAL, "incorrect %s GPIO polarity\n", names[pin]);
  ret = gpiod_direction_output(es->pins[pin], initial[pin]);
  if (ret) return dev_err_probe(dev, ret, "cannot drive %s GPIO\n", names[pin]);
  dev_info(dev, "A9-ESS %s GPIO initialized\n", names[pin]);
 }
 mutex_init(&es->lock);
 es->seq.io = &es_io; es->seq.ctx = es;
 es->volume[0] = 255; es->volume[1] = 255;
 es->st_id = ES9218_ID_NONE;
 i2c_set_clientdata(client, es);
 ret = devm_add_action_or_reset(dev, es_off_action, es);
 if (ret) return dev_err_probe(dev, ret, "A9-ESS shutdown cleanup failed\n");
 dev_info(dev, "A9-ESS standby ready; register component/DAI\n");
 ret = devm_snd_soc_register_component(dev, &es_component, &es_dai, 1);
 dev_info(dev, "A9-ESS register component rc=%d\n", ret);
 if (ret) dev_err(dev, "A9-ESS component registration failed: %d\n", ret);
 if (!ret) dev_info(dev, "standby, no I2C at probe (bringup=%d)\n", bringup);
 return ret;
}
static void es_shutdown(struct i2c_client *client)
{
 es_off_action(i2c_get_clientdata(client));
}
static int es_suspend(struct device *dev)
{
 struct es9218_a9 *es = dev_get_drvdata(dev);
 bool was_active;
 int ret;
 mutex_lock(&es->lock);
 was_active = es->seq.mode != ES_STANDBY;
 ret = es9218_stop(&es->seq);
 es->seq.width = 0;
 /* New fail-closed policy: resume requires a fresh PCM open/prepare cycle. */
 if (was_active) es->fault = ret ? ret : -EHOSTDOWN;
 mutex_unlock(&es->lock);
 if (ret) dev_warn(dev, "I2C suspend shutdown failed; GPIO power disabled: %d\n", ret);
 return 0;
}
static DEFINE_SIMPLE_DEV_PM_OPS(es_pm_ops, es_suspend, NULL);
static const struct of_device_id es_of_match[] = {
 { .compatible = "hisense,a9-es9218" }, { }
};
MODULE_DEVICE_TABLE(of, es_of_match);
static const struct i2c_device_id es_ids[] = { { "a9-es9218", 0 }, { } };
MODULE_DEVICE_TABLE(i2c, es_ids);
static struct i2c_driver es_driver = {
 .driver = { .name = "a9-es9218", .of_match_table = es_of_match,
             .pm = pm_sleep_ptr(&es_pm_ops), .dev_groups = es_groups },
 .probe = es_probe, .shutdown = es_shutdown, .id_table = es_ids,
};
module_i2c_driver(es_driver);
MODULE_DESCRIPTION("Experimental Hisense A9 ESS HiFi DAC sequence driver");
MODULE_LICENSE("GPL");
