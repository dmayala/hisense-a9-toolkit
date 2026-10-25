// SPDX-License-Identifier: GPL-2.0-only
/* A9-only AW87519: stock pid59 Music sequence, DAPM lifetime, reset off.
 * No tuning blob is embedded. The firmware is copied locally from stock.
 */
#include <linux/delay.h>
#include <linux/firmware.h>
#include <linux/gpio/consumer.h>
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/regmap.h>
#include <sound/soc.h>
struct a9_aw {
 struct regmap *map; struct gpio_desc *reset; const struct firmware *fw;
 struct mutex lock;
 bool music, powered;
};
static void off(void *ptr) { struct a9_aw *a = ptr; gpiod_set_value_cansleep(a->reset, 0); }
/* Stock aw87xxx_i2c_write: at most five writes, sleep 2ms after each failure.
 * Unlike stock scene replay, retain the final error and reset on failure. */
static int write_retry(struct a9_aw *a, unsigned int reg, unsigned int value)
{
 int ret;
 for (int attempt = 0; attempt < 5; attempt++) {
  ret = regmap_write(a->map, reg, value);
  if (!ret) return 0;
  msleep(2);
 }
 return ret;
}
/* Caller holds the amp mutex. I2C controller runtime PM can prepare clocks. */
static int power_on(struct a9_aw *a, struct device *dev)
{
 unsigned int id;
 int ret;
 if (a->powered) return 0;
 gpiod_set_value_cansleep(a->reset, 0); usleep_range(2000, 2500);
 gpiod_set_value_cansleep(a->reset, 1); usleep_range(2000, 2500);
 ret = regmap_read(a->map, 0, &id);
 if (ret) goto fail;
 if (id != 0x59) { dev_err(dev, "A9 speaker amp is not pid59\n"); ret = -ENODEV; goto fail; }
 /* Preserve stock tuning/order. Its final pair enables playback. */
 for (size_t i = 0; i < a->fw->size; i += 2) {
  ret = write_retry(a, a->fw->data[i], a->fw->data[i + 1]);
  if (ret) goto fail;
 }
 a->powered = true;
 return 0;
fail:
 off(a); return ret;
}
static void power_off(struct a9_aw *a)
{
 if (!a->powered) return;
 /* Stock AW87519 hw_off asserts reset. */
 off(a);
 a->powered = false;
 usleep_range(2000, 2500);
}
static int mode_get(struct snd_kcontrol *k, struct snd_ctl_elem_value *v)
{
 struct a9_aw *a = snd_soc_component_get_drvdata(snd_kcontrol_chip(k));
 mutex_lock(&a->lock);
 v->value.enumerated.item[0] = a->music;
 mutex_unlock(&a->lock);
 return 0;
}
static int mode_put(struct snd_kcontrol *k, struct snd_ctl_elem_value *v)
{
 struct snd_soc_component *c = snd_kcontrol_chip(k);
 struct a9_aw *a = snd_soc_component_get_drvdata(c);
 unsigned int mode = v->value.enumerated.item[0];
 int ret, changed;
 if (mode > 1) return -EINVAL;
 mutex_lock(&a->lock);
 changed = a->music != !!mode;
 if (mode) ret = power_on(a, c->dev);
 else { power_off(a); ret = 0; }
 if (!ret) a->music = !!mode;
 else { a->music = false; power_off(a); }
 mutex_unlock(&a->lock);
 return ret ? ret : changed;
}
static int event(struct snd_soc_dapm_widget *w, struct snd_kcontrol *k, int ev)
{
 struct snd_soc_component *c = snd_soc_dapm_to_component(w->dapm);
 struct a9_aw *a = snd_soc_component_get_drvdata(c);
 int ret = 0;
 mutex_lock(&a->lock);
 if (ev == SND_SOC_DAPM_PRE_PMU) {
  /* Music is applied by device selection before PCM, as stock does. A fresh
   * DAPM power-up may restore that same request after a transport teardown. */
  if (a->music) ret = power_on(a, c->dev);
 } else {
  /* Before WCD AUX loses clocks/bias, not after the upstream path is gone. */
  power_off(a);
 }
 mutex_unlock(&a->lock);
 return ret;
}
static const struct snd_soc_dapm_widget widgets[] = {
 SND_SOC_DAPM_INPUT("AW IN"),
 SND_SOC_DAPM_OUT_DRV_E("AW AMP", SND_SOC_NOPM, 0, 0, NULL, 0, event, SND_SOC_DAPM_PRE_PMU | SND_SOC_DAPM_PRE_PMD),
 SND_SOC_DAPM_OUTPUT("AW OUT"),
};
static const struct snd_soc_dapm_route routes[] = {{"AW AMP", NULL, "AW IN"}, {"AW OUT", NULL, "AW AMP"}};
static const char * const mode_text[] = { "Off", "Music" };
static SOC_ENUM_SINGLE_EXT_DECL(mode_enum, mode_text);
static const struct snd_kcontrol_new controls[] = {
 SOC_ENUM_EXT("aw87xxx_mode_switch_a", mode_enum, mode_get, mode_put),
};
static int component_probe(struct snd_soc_component *c)
{
 dev_info(c->dev, "A9-AMP component probe: %s rc=0, reset held\n", c->name);
 return 0;
}
static const struct snd_soc_component_driver component = { .probe = component_probe, .controls = controls, .num_controls = ARRAY_SIZE(controls), .dapm_widgets = widgets, .num_dapm_widgets = ARRAY_SIZE(widgets), .dapm_routes = routes, .num_dapm_routes = ARRAY_SIZE(routes) };
static void release_fw(void *ptr) { release_firmware(ptr); }
static const struct regmap_config config = { .reg_bits = 8, .val_bits = 8, .max_register = 0x69, .cache_type = REGCACHE_NONE };
static int probe(struct i2c_client *client)
{
 struct device *dev = &client->dev;
 struct a9_aw *a = devm_kzalloc(dev, sizeof(*a), GFP_KERNEL); int ret;
 dev_info(dev, "A9-AMP I2C probe enter\n");
 if (!a) return dev_err_probe(dev, -ENOMEM, "A9-AMP allocation failed\n");
 mutex_init(&a->lock);
 a->reset = devm_gpiod_get(dev, "reset", GPIOD_OUT_LOW);
 if (IS_ERR(a->reset)) return dev_err_probe(dev, PTR_ERR(a->reset), "A9-AMP reset GPIO failed\n");
 dev_info(dev, "A9-AMP reset asserted\n");
 ret = devm_add_action_or_reset(dev, off, a); if (ret) return dev_err_probe(dev, ret, "A9-AMP reset cleanup failed\n");
 a->map = devm_regmap_init_i2c(client, &config); if (IS_ERR(a->map)) return dev_err_probe(dev, PTR_ERR(a->map), "A9-AMP regmap failed\n");
 dev_info(dev, "A9-AMP regmap ready; requesting stock tuning\n");
 ret = request_firmware(&a->fw, "qcom/sm6115/hisense/hlte556n/aw87xxx_pid_59_music_0.bin", dev);
 if (ret) return dev_err_probe(dev, ret, "stock speaker tuning unavailable\n");
 ret = devm_add_action_or_reset(dev, release_fw, (void *)a->fw); if (ret) return dev_err_probe(dev, ret, "A9-AMP firmware cleanup failed\n");
 if (a->fw->size != 42 || a->fw->data[0] != 0x69 || a->fw->data[1] != 0xa5 ||
     a->fw->data[40] != 1 || a->fw->data[41] != 0xf0) return dev_err_probe(dev, -EINVAL, "A9-AMP invalid stock tuning\n");
 dev_info(dev, "A9-AMP tuning validated; register component\n");
 i2c_set_clientdata(client, a);
 ret = devm_snd_soc_register_component(dev, &component, NULL, 0);
 dev_info(dev, "A9-AMP register component rc=%d\n", ret);
 if (ret) dev_err(dev, "A9-AMP component registration failed: %d\n", ret);
 return ret;
}
static const struct of_device_id match[] = {{ .compatible = "hisense,a9-aw87519" }, {}};
MODULE_DEVICE_TABLE(of, match);
static void shutdown(struct i2c_client *c) { off(i2c_get_clientdata(c)); }
static struct i2c_driver a9_aw_driver = { .probe = probe, .shutdown = shutdown, .driver = { .name = "a9-aw87519", .of_match_table = match }};
module_i2c_driver(a9_aw_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Hisense A9 stock-tuned AW87519 speaker amplifier POC");
