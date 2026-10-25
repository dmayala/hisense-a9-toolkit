// SPDX-License-Identifier: GPL-2.0-only
/* A9 APR card: MM1 playback, MM2 capture, WCD RX/TX and ESS secondary MI2S. */
#include <linux/module.h>
#include <linux/delay.h>
#include <linux/regmap.h>
#include <linux/platform_device.h>
#include <linux/of_platform.h>
#include <linux/i2c.h>
#include <dt-bindings/sound/qcom,q6afe.h>
#include <sound/soc.h>
#include <sound/pcm_params.h>
#include <sound/jack.h>
#include "common.h"
#include "sdw.h"
#include "qdsp6/q6afe.h"
#include "a9-clock-hold.h"
static bool lpass_clock_hold;
module_param(lpass_clock_hold, bool, 0444);
MODULE_PARM_DESC(lpass_clock_hold, "Hold LPASS macro clock votes at card bind (idle power cost; default off)");
struct a9_card { bool prepared[AFE_PORT_MAX]; struct snd_soc_jack jack; bool jack_ready; struct a9_clock_hold clock_hold; };
static const struct snd_soc_dapm_widget widgets[] = {
 SND_SOC_DAPM_SPK("Speaker", NULL), SND_SOC_DAPM_SPK("Earpiece", NULL),
 SND_SOC_DAPM_HP("Headphones", NULL), SND_SOC_DAPM_HP("HiFi Headphones", NULL),
 SND_SOC_DAPM_MIC("Main Mic", NULL), SND_SOC_DAPM_MIC("Headset Mic", NULL),
};
static const struct snd_kcontrol_new controls[] = {
 SOC_DAPM_PIN_SWITCH("Speaker"), SOC_DAPM_PIN_SWITCH("Earpiece"),
 SOC_DAPM_PIN_SWITCH("Headphones"), SOC_DAPM_PIN_SWITCH("HiFi Headphones"),
 SOC_DAPM_PIN_SWITCH("Main Mic"), SOC_DAPM_PIN_SWITCH("Headset Mic"),
};
static int rx_map(struct snd_soc_pcm_runtime *rtd, unsigned int *count,
                  unsigned int *mask)
{
 struct snd_soc_dai *codec;
 unsigned int tx_count = 0, tx_mask = 0;
 int i, ret;
 for_each_rtd_codec_dais(rtd, i, codec) {
  if (!of_device_is_compatible(codec->dev->of_node, "qcom,sm6115-lpass-rx-macro")) continue;
  *count = *mask = 0;
  ret = snd_soc_dai_get_channel_map(codec, &tx_count, &tx_mask, count, mask);
  if (ret) return ret;
  if (!*count || *count > 2 || hweight32(*mask) != *count || (*mask & ~3U))
   return dev_err_probe(rtd->card->dev, -EINVAL, "A9-CARD RX macro route/channel mismatch\n");
  return 0;
 }
 return -ENODEV;
}
static int fixup(struct snd_soc_pcm_runtime *rtd, struct snd_pcm_hw_params *p)
{
 int id = snd_soc_rtd_to_cpu(rtd, 0)->id;
 unsigned int count, mask;
 int ret;
 struct snd_interval *r = hw_param_interval(p, SNDRV_PCM_HW_PARAM_RATE);
 struct snd_interval *c = hw_param_interval(p, SNDRV_PCM_HW_PARAM_CHANNELS);
 struct snd_mask *f = hw_param_mask(p, SNDRV_PCM_HW_PARAM_FORMAT);
 r->min = r->max = 48000;
 c->min = c->max = (id == RX_CODEC_DMA_RX_1 || id == TX_CODEC_DMA_TX_3 || id == TX_CODEC_DMA_TX_4) ? 1 : 2;
 /* Handset uses RX0 alone (mono); headphones use RX0+RX1 (stereo).
  * Match the AFE backend to the selected macro lanes, as stock does. */
 if (id == RX_CODEC_DMA_RX_0 || id == RX_CODEC_DMA_RX_1) {
  ret = rx_map(rtd, &count, &mask);
  if (ret) return ret;
  c->min = c->max = count;
 }
 snd_mask_none(f);
 snd_mask_set_format(f, id == SECONDARY_MI2S_RX ? SNDRV_PCM_FORMAT_S32_LE : SNDRV_PCM_FORMAT_S16_LE);
 return 0;
}
static int init(struct snd_soc_pcm_runtime *rtd)
{
 struct a9_card *a = snd_soc_card_get_drvdata(rtd->card);
 int ret;
 dev_info(rtd->card->dev, "A9-CARD link init: %s cpu_id=%d\n",
          rtd->dai_link->name, snd_soc_rtd_to_cpu(rtd, 0)->id);
 if (!rtd->dai_link->no_pcm) return 0;
 /* The shared helper only calls codec set_jack (and hence WCD MBHC start)
  * on TX_CODEC_DMA_TX_0..3. RX0 creates controls but never starts detection. */
 if (snd_soc_rtd_to_cpu(rtd, 0)->id != TX_CODEC_DMA_TX_3) return 0;
 ret = qcom_snd_wcd_jack_setup(rtd, &a->jack, &a->jack_ready);
 dev_info(rtd->card->dev, "A9-CARD jack setup rc=%d ready=%d\n", ret, a->jack_ready);
 if (ret) dev_err(rtd->card->dev, "A9-CARD jack setup failed: %d\n", ret);
 return ret;
}
static int hw_params(struct snd_pcm_substream *s, struct snd_pcm_hw_params *p)
{
 struct snd_soc_pcm_runtime *r = snd_soc_substream_to_rtd(s);
 struct snd_soc_dai *cpu = snd_soc_rtd_to_cpu(r, 0);
 struct snd_soc_dai *codec;
 unsigned int tx_num, rx_num, tx_mask, rx_mask;
 int ret, i;
 dev_info(r->card->dev, "A9-CARD hw_params %s rate=%u channels=%u width=%u\n",
          r->dai_link->name, params_rate(p), params_channels(p), params_width(p));
 if (cpu->id == RX_CODEC_DMA_RX_0 || cpu->id == RX_CODEC_DMA_RX_1) {
  ret = rx_map(r, &rx_num, &rx_mask);
  if (ret) return ret;
  if (rx_num != params_channels(p)) return -EINVAL;
  ret = snd_soc_dai_set_channel_map(cpu, 0, NULL, rx_num, &rx_mask);
  dev_info(r->card->dev, "A9-CARD CPU %s set RX DMA count=%u mask=%#x rc=%d\n",
           cpu->name, rx_num, rx_mask, ret);
  return ret;
 }
 /* The TX macro reports decimator bits, not contiguous PCM channel bits.
  * DEC2 mono must use DMA mask BIT(2), rather than Q6AFE's mono default BIT(0).
  * SoundWire's port masks belong to a different channel namespace. */
 if (cpu->id == TX_CODEC_DMA_TX_3 || cpu->id == TX_CODEC_DMA_TX_4) {
  for_each_rtd_codec_dais(r, i, codec) {
   if (!of_device_is_compatible(codec->dev->of_node, "qcom,sm6115-lpass-tx-macro")) continue;
   tx_num = rx_num = tx_mask = rx_mask = 0;
   ret = snd_soc_dai_get_channel_map(codec, &tx_num, &tx_mask, &rx_num, &rx_mask);
   dev_info(r->card->dev, "A9-CARD TX macro %s map rc=%d count=%u mask=%#x\n",
            codec->name, ret, tx_num, tx_mask);
   if (ret) return ret;
   if (!tx_mask || tx_num != params_channels(p))
    return dev_err_probe(r->card->dev, -EINVAL, "A9-CARD TX macro route/channel mismatch\n");
   ret = snd_soc_dai_set_channel_map(cpu, tx_num, &tx_mask, 0, NULL);
   dev_info(r->card->dev, "A9-CARD CPU %s set DMA mask=%#x rc=%d\n", cpu->name, tx_mask, ret);
   return ret;
  }
  return dev_err_probe(r->card->dev, -ENODEV, "A9-CARD TX macro DAI missing\n");
 }
 if (cpu->id != SECONDARY_MI2S_RX) return 0;
 ret = snd_soc_dai_set_fmt(cpu, SND_SOC_DAIFMT_BP_FP | SND_SOC_DAIFMT_I2S | SND_SOC_DAIFMT_NB_NF);
 if (ret) return ret;
 ret = snd_soc_dai_set_fmt(snd_soc_rtd_to_codec(r, 0), SND_SOC_DAIFMT_BC_FC | SND_SOC_DAIFMT_I2S | SND_SOC_DAIFMT_NB_NF);
 if (ret) return ret;
 return snd_soc_dai_set_sysclk(cpu, Q6AFE_LPASS_CLK_ID_SEC_MI2S_IBIT, 48000 * 64, SNDRV_PCM_STREAM_PLAYBACK);
}
static int prepare(struct snd_pcm_substream *s)
{
 struct snd_soc_pcm_runtime *r = snd_soc_substream_to_rtd(s);
 struct a9_card *a = snd_soc_card_get_drvdata(r->card);
 int id = snd_soc_rtd_to_cpu(r, 0)->id;
 int ret;
 dev_info(r->card->dev, "A9-CARD prepare %s stream=%s prepared=%d\n",
          r->dai_link->name, qcom_snd_sdw_get_stream(s) ? "present" : "none", a->prepared[id]);
 ret = qcom_snd_sdw_prepare(s, &a->prepared[id]);
 dev_info(r->card->dev, "A9-CARD SoundWire prepare/enable %s rc=%d prepared=%d\n",
          r->dai_link->name, ret, a->prepared[id]);
 return ret;
}
static int startup(struct snd_pcm_substream *s)
{
 struct snd_soc_pcm_runtime *r = snd_soc_substream_to_rtd(s);
 int ret = qcom_snd_sdw_startup(s);
 dev_info(r->card->dev, "A9-CARD startup %s direction=%d rc=%d\n",
          r->dai_link->name, s->stream, ret);
 return ret;
}
static int hw_free(struct snd_pcm_substream *s)
{
 struct snd_soc_pcm_runtime *r = snd_soc_substream_to_rtd(s);
 struct a9_card *a = snd_soc_card_get_drvdata(r->card);
 struct snd_soc_dai *cpu = snd_soc_rtd_to_cpu(r, 0);
 int ret;
 if (cpu->id == SECONDARY_MI2S_RX)
  return snd_soc_dai_set_sysclk(cpu, Q6AFE_LPASS_CLK_ID_SEC_MI2S_IBIT, 0, SNDRV_PCM_STREAM_PLAYBACK);
 ret = qcom_snd_sdw_hw_free(s, &a->prepared[cpu->id]);
 return ret;
}
static void shutdown(struct snd_pcm_substream *s)
{
 qcom_snd_sdw_shutdown(s);
}
static const struct snd_soc_ops ops = {
 .startup = startup, .shutdown = shutdown,
 .hw_params = hw_params, .prepare = prepare, .hw_free = hw_free,
};
static int late_probe(struct snd_soc_card *card)
{
 struct snd_soc_dapm_context *d = card->dapm;
 int ret;
 dev_info(card->dev, "A9-CARD late_probe enter: runtimes=%d\n", card->num_rtd);
 /* Routing is explicit. Merely starting the HAL does not select an output. */
 for (int i = 0; i < ARRAY_SIZE(widgets); i++) {
  ret = snd_soc_dapm_disable_pin(d, widgets[i].name);
  dev_info(card->dev, "A9-CARD disable pin %s rc=%d\n", widgets[i].name, ret);
  if (ret < 0) dev_err(card->dev, "A9-CARD disable pin failed: %s rc=%d\n", widgets[i].name, ret);
 }
 ret = snd_soc_dapm_sync(d);
 dev_info(card->dev, "A9-CARD late_probe sync rc=%d\n", ret);
 if (ret) dev_err(card->dev, "A9-CARD late_probe sync failed: %d\n", ret);
 if (!ret && lpass_clock_hold) {
  struct a9_card *a = snd_soc_card_get_drvdata(card);
  ret = a9_clock_hold_start(card, &a->clock_hold);
  if (ret) dev_err(card->dev, "A9-CARD LPASS clock hold failed: %d\n", ret);
  else dev_info(card->dev, "A9-CARD LPASS clock hold ready: %u prepare references\n", a->clock_hold.count);
 }
 return ret;
}

static int card_remove(struct snd_soc_card *card)
{
 struct a9_card *a = snd_soc_card_get_drvdata(card);
 a9_clock_hold_release(&a->clock_hold);
 return 0;
}

static int card_probe(struct snd_soc_card *card)
{
 struct snd_soc_pcm_runtime *rtd;
 struct snd_soc_component *component;
 int i;

 dev_info(card->dev, "A9-CARD core reached card probe: runtimes=%d\n", card->num_rtd);
 for_each_card_rtds(card, rtd) {
  dev_info(card->dev, "A9-CARD runtime %s\n", rtd->dai_link->name);
  for_each_rtd_components(rtd, i, component)
   dev_info(card->dev, "A9-CARD component %s probe_order=%d\n",
            component->name, component->driver->probe_order);
 }
 return 0;
}

static int aux_init(struct snd_soc_component *component)
{
 dev_info(component->dev, "A9-CARD aux init: %s rc=0\n", component->name);
 return 0;
}

/* q6routing's routes reference q6asm's MM_DL and MM_UL widgets.
 * ASoC probes components in link order within each probe
 * priority, so the frontends must precede backends. Overlay insertion can
 * reverse DT child order; do not rely on that order for component probing or
 * PCM numbering. Keep the backend order and put frontends in ascending ID. */
static void order_frontends(struct snd_soc_card *card)
{
 int i, j;

 for (i = 1; i < card->num_links; i++) {
  struct snd_soc_dai_link link = card->dai_link[i];

  if (!link.dynamic) continue;
  for (j = i; j > 0; j--) {
   struct snd_soc_dai_link *previous = &card->dai_link[j - 1];

   if (previous->dynamic && previous->id <= link.id) break;
   card->dai_link[j] = *previous;
  }
  card->dai_link[j] = link;
 }
 for (i = 0; i < card->num_links; i++)
  dev_info(card->dev, "A9-CARD probe order[%d]: %s dynamic=%u id=%d\n",
           i, card->dai_link[i].name, card->dai_link[i].dynamic,
           card->dai_link[i].id);
}

static void dump_component(struct snd_soc_card *card, const char *kind,
                           const struct snd_soc_dai_link_component *dlc)
{
 struct platform_device *pdev = NULL;
 struct i2c_client *client = NULL;
 struct snd_soc_component *component = NULL;
 struct device *dev = NULL;

 if (dlc->of_node) {
  pdev = of_find_device_by_node(dlc->of_node);
  if (pdev) dev = &pdev->dev;
  else {
   client = of_find_i2c_device_by_node(dlc->of_node);
   if (client) dev = &client->dev;
  }
  if (dev) component = snd_soc_lookup_component(dev, NULL);
 } else if (dlc->name) {
  component = snd_soc_lookup_component_by_name(dlc->name);
 }
 dev_info(card->dev, "A9-CARD %s node=%pOF name=%s device=%s driver=%s component=%s\n",
          kind, dlc->of_node, dlc->name ?: "-", dev ? dev_name(dev) : "-",
          dev && dev->driver ? dev->driver->name : "-",
          component ? component->name : "MISSING/unresolved");
 if (dev) put_device(dev);
}

static void dump_dai(struct snd_soc_card *card, const char *kind,
                     const struct snd_soc_dai_link_component *dlc)
{
 struct snd_soc_dai *dai = snd_soc_find_dai_with_mutex(dlc);

 dev_info(card->dev, "A9-CARD %s node=%pOF name=%s dai=%s found=%s\n",
          kind, dlc->of_node, dlc->name ?: "-", dlc->dai_name ?: "-",
          dai ? dai->name : "MISSING");
}

static void dump_dependencies(struct snd_soc_card *card)
{
 struct snd_soc_dai_link *link;
 struct snd_soc_dai_link_component *dlc;
 int i, j;

 for (i = 0; i < card->num_aux_devs; i++)
  dump_component(card, "aux", &card->aux_dev[i].dlc);
 for_each_card_prelinks(card, i, link) {
  dev_info(card->dev, "A9-CARD link[%d] %s id=%d no_pcm=%u dynamic=%u cpu=%u codec=%u platform=%u\n",
           i, link->name, link->id, link->no_pcm, link->dynamic,
           link->num_cpus, link->num_codecs, link->num_platforms);
  for_each_link_cpus(link, j, dlc) dump_dai(card, "cpu", dlc);
  for_each_link_codecs(link, j, dlc) dump_dai(card, "codec", dlc);
  for_each_link_platforms(link, j, dlc) dump_component(card, "platform", dlc);
 }
}

static int probe(struct platform_device *pdev)
{
 struct snd_soc_card *c;
 struct a9_card *a;
 struct snd_soc_dai_link *l; int i, ret;
 dev_info(&pdev->dev, "A9-CARD probe enter node=%pOF\n", pdev->dev.of_node);
 c = devm_kzalloc(&pdev->dev, sizeof(*c), GFP_KERNEL);
 a = devm_kzalloc(&pdev->dev, sizeof(*a), GFP_KERNEL);
 if (!c || !a) {
  dev_err(&pdev->dev, "A9-CARD allocation failed\n");
  return -ENOMEM;
 }
 dev_info(&pdev->dev, "A9-CARD allocations complete\n");
 c->dev = &pdev->dev; c->owner = THIS_MODULE; c->driver_name = "A9-Audio";
 c->dapm_widgets = widgets; c->num_dapm_widgets = ARRAY_SIZE(widgets);
 c->controls = controls; c->num_controls = ARRAY_SIZE(controls); c->late_probe = late_probe;
 c->probe = card_probe; c->remove = card_remove;
 snd_soc_card_set_drvdata(c, a);
 /* Backstop for a failed/deferred bind; devm card unregister runs first. */
 ret = devm_add_action_or_reset(c->dev, a9_clock_hold_release, &a->clock_hold);
 if (ret) return ret;
 dev_info(c->dev, "A9-CARD qcom_snd_parse_of enter\n");
 ret = qcom_snd_parse_of(c);
 dev_info(c->dev, "A9-CARD qcom_snd_parse_of rc=%d name=%s links=%d aux=%d\n",
          ret, c->name ?: "-", c->num_links, c->num_aux_devs);
 if (ret) {
  dev_err(c->dev, "A9-CARD DT parse failed: %d\n", ret);
  return ret;
 }
 order_frontends(c);
 for_each_card_prelinks(c, i, l) {
  l->init = init;
  if (l->dynamic) l->trigger[SNDRV_PCM_STREAM_PLAYBACK] = SND_SOC_DPCM_TRIGGER_PRE;
  if (l->no_pcm) { l->ops = &ops; l->be_hw_params_fixup = fixup; }
 }
 for (i = 0; i < c->num_aux_devs; i++) c->aux_dev[i].init = aux_init;
 dev_info(c->dev, "A9-CARD backend callbacks installed\n");
 dump_dependencies(c);
 dev_info(c->dev, "A9-CARD snd_soc_register_card enter (devm wrapper)\n");
 ret = devm_snd_soc_register_card(&pdev->dev, c);
 dev_info(c->dev, "A9-CARD snd_soc_register_card rc=%d instantiated=%u runtimes=%d snd_card=%s\n",
          ret, c->instantiated, c->num_rtd, c->snd_card ? "allocated" : "none");
 if (ret) dev_err(c->dev, "A9-CARD registration failed: %d\n", ret);
 else if (!c->instantiated) {
  dev_warn(c->dev, "A9-CARD core deferred internally: unbind_card_list, not devices_deferred; awaiting component rebind\n");
  dump_dependencies(c);
 }
 return ret;
}
static const struct of_device_id match[] = {{ .compatible = "hisense,a9-audio-card" }, {}};
MODULE_DEVICE_TABLE(of, match);
static struct platform_driver a9_card_driver = { .probe = probe, .driver = { .name = "a9-audio-card", .of_match_table = match, .pm = &snd_soc_pm_ops }};
module_platform_driver(a9_card_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Hisense A9 APR WCD/ESS media card");
