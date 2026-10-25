// SPDX-License-Identifier: GPL-2.0-only
/* A9 ESS-only APR/secondary-MI2S test card, deliberately separate from SoundWire. */
#include <dt-bindings/sound/qcom,q6afe.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <sound/pcm_params.h>
#include <sound/soc.h>
#include "common.h"
#include "qdsp6/q6afe.h"

static int a9_es_fixup(struct snd_soc_pcm_runtime *rtd, struct snd_pcm_hw_params *p)
{
 struct snd_interval *rate = hw_param_interval(p, SNDRV_PCM_HW_PARAM_RATE);
 struct snd_interval *channels = hw_param_interval(p, SNDRV_PCM_HW_PARAM_CHANNELS);
 struct snd_mask *fmt = hw_param_mask(p, SNDRV_PCM_HW_PARAM_FORMAT);
 /* Stock 24-bit HiFi paths select bit_32; use S32_LE on the backend to make the 32-bit serial width explicit. */
 rate->min = rate->max = 48000;
 channels->min = channels->max = 2;
 snd_mask_none(fmt);
 snd_mask_set_format(fmt, SNDRV_PCM_FORMAT_S32_LE);
 return 0;
}
static int a9_es_hw_params(struct snd_pcm_substream *s, struct snd_pcm_hw_params *p)
{
 struct snd_soc_pcm_runtime *rtd = snd_soc_substream_to_rtd(s);
 struct snd_soc_dai *cpu = snd_soc_rtd_to_cpu(rtd, 0);
 struct snd_soc_dai *codec = snd_soc_rtd_to_codec(rtd, 0);
 int ret;
 if (cpu->id != SECONDARY_MI2S_RX || params_rate(p) != 48000 ||
     params_channels(p) != 2 || params_format(p) != SNDRV_PCM_FORMAT_S32_LE)
  return -EINVAL;
 ret = snd_soc_dai_set_fmt(cpu, SND_SOC_DAIFMT_BP_FP | SND_SOC_DAIFMT_I2S |
                                SND_SOC_DAIFMT_NB_NF);
 if (ret) return ret;
 ret = snd_soc_dai_set_fmt(codec, SND_SOC_DAIFMT_BC_FC | SND_SOC_DAIFMT_I2S |
                                  SND_SOC_DAIFMT_NB_NF);
 if (ret) return ret;
 return snd_soc_dai_set_sysclk(cpu, Q6AFE_LPASS_CLK_ID_SEC_MI2S_IBIT,
                               48000 * 2 * 32, SNDRV_PCM_STREAM_PLAYBACK);
}
static int a9_es_hw_free(struct snd_pcm_substream *s)
{
 struct snd_soc_pcm_runtime *rtd = snd_soc_substream_to_rtd(s);
 return snd_soc_dai_set_sysclk(snd_soc_rtd_to_cpu(rtd, 0),
                               Q6AFE_LPASS_CLK_ID_SEC_MI2S_IBIT, 0,
                               SNDRV_PCM_STREAM_PLAYBACK);
}
static const struct snd_soc_ops a9_es_ops = {
 .hw_params = a9_es_hw_params, .hw_free = a9_es_hw_free,
};
static const struct snd_soc_dapm_widget a9_es_widgets[] = {
 SND_SOC_DAPM_HP("Headphone Jack", NULL),
};
static int a9_es_probe(struct platform_device *pdev)
{
 struct snd_soc_card *card;
 struct snd_soc_dai_link *link;
 int ret, i;
 card = devm_kzalloc(&pdev->dev, sizeof(*card), GFP_KERNEL);
 if (!card) return -ENOMEM;
 card->owner = THIS_MODULE;
 card->dev = &pdev->dev;
 card->driver_name = "A9-ESS";
 card->dapm_widgets = a9_es_widgets;
 card->num_dapm_widgets = ARRAY_SIZE(a9_es_widgets);
 ret = qcom_snd_parse_of(card);
 if (ret) return ret;
 for_each_card_prelinks(card, i, link) {
  if (link->no_pcm) {
   link->be_hw_params_fixup = a9_es_fixup;
   link->ops = &a9_es_ops;
  }
 }
 return devm_snd_soc_register_card(&pdev->dev, card);
}
static const struct of_device_id a9_es_match[] = {
 { .compatible = "hisense,a9-es9218-sndcard" }, { }
};
MODULE_DEVICE_TABLE(of, a9_es_match);
static struct platform_driver a9_es_driver = {
 .probe = a9_es_probe,
 .driver = { .name = "a9-es9218-sndcard", .of_match_table = a9_es_match,
             .pm = &snd_soc_pm_ops },
};
module_platform_driver(a9_es_driver);
MODULE_DESCRIPTION("Hisense A9 ESS secondary MI2S experimental sound card");
MODULE_LICENSE("GPL");
