#!/usr/bin/env python3
"""Exercise production RX map/fixup with mocked ASoC map calls; no hardware."""
import argparse
import json
import os
from pathlib import Path
import subprocess as sp

HERE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--out', type=Path, default=Path.home()/'A9-build/mainline/audio-output-20261020')
out = parser.parse_args().out
test = out/'tests'
test.mkdir(parents=True, exist_ok=True)
source = (HERE.parent/'driver/a9-audio-card.c').read_text()
functions = []
for name in ('rx_map', 'fixup', 'init'):
    a = source.index('static int '+name+'(')
    b = source.index('\n}\n', a) + 3
    functions.append(source[a:b])
# Use the real common helper too: a stub returning success would miss the
# RX-versus-TX dispatch bug that left the jack control permanently zero.
common = (Path.home()/'A9-build/mainline/demo-integrated-20261019/obj/source/sound/soc/qcom/common.c').read_text()
a = common.index('static struct snd_soc_jack_pin qcom_headset_jack_pins[]')
b = common.index('EXPORT_SYMBOL_GPL(qcom_snd_wcd_jack_setup)', a)
jack_helper = common[a:b]
prelude = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#define RX_CODEC_DMA_RX_0 1
#define RX_CODEC_DMA_RX_1 2
#define TX_CODEC_DMA_TX_3 3
#define TX_CODEC_DMA_TX_4 4
#define SECONDARY_MI2S_RX 5
#define TX_CODEC_DMA_TX_0 6
#define TX_CODEC_DMA_TX_1 7
#define TX_CODEC_DMA_TX_2 8
#define SND_JACK_HEADSET 3
#define SND_JACK_HEADPHONE 1
#define SND_JACK_MICROPHONE 2
#define SND_JACK_LINEOUT 4
#define SND_JACK_MECHANICAL 8
#define SND_JACK_BTN_0 16
#define SND_JACK_BTN_1 32
#define SND_JACK_BTN_2 64
#define SND_JACK_BTN_3 128
#define SND_JACK_BTN_4 256
#define SND_JACK_BTN_5 512
#define KEY_MEDIA 1
#define KEY_VOICECOMMAND 2
#define KEY_VOLUMEUP 3
#define KEY_VOLUMEDOWN 4
#define ENOTSUPP 524
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
#define dev_info(...) ((void)0)
#define dev_err(...) ((void)0)
#define dev_warn(...) ((void)0)
#define SNDRV_PCM_HW_PARAM_RATE 1
#define SNDRV_PCM_HW_PARAM_CHANNELS 2
#define SNDRV_PCM_HW_PARAM_FORMAT 3
#define SNDRV_PCM_FORMAT_S32_LE 32
#define SNDRV_PCM_FORMAT_S16_LE 16
#define hweight32 __builtin_popcount
struct dev { int of_node; };
struct snd_soc_dai { int id; struct dev *dev; void *component; };
struct snd_soc_jack { void *jack; };
struct a9_card { struct snd_soc_jack jack; bool jack_ready; };
struct snd_soc_jack_pin { const char *pin; unsigned mask; };
struct snd_soc_card { struct dev *dev; struct a9_card data; };
struct link { int no_pcm; };
struct snd_soc_pcm_runtime { struct snd_soc_card *card; struct snd_soc_dai cpu, codec; struct link *dai_link; };
struct snd_interval { unsigned min, max; };
struct snd_mask { int fmt; };
struct snd_pcm_hw_params { struct snd_interval rate, channels; struct snd_mask format; };
static struct snd_soc_dai *snd_soc_rtd_to_cpu(struct snd_soc_pcm_runtime *r, int i) { (void)i; return &r->cpu; }
static struct snd_soc_dai *snd_soc_rtd_to_codec(struct snd_soc_pcm_runtime *r, int i) { (void)i; return &r->codec; }
static struct a9_card *snd_soc_card_get_drvdata(struct snd_soc_card *c) {return &c->data;}
static unsigned jack_creations, mbhc_starts;
static int jack_error;
static int snd_soc_card_jack_new_pins(struct snd_soc_card *c,const char *name,unsigned mask,struct snd_soc_jack *j,struct snd_soc_jack_pin *pins,size_t count) {
 (void)c;(void)name;(void)mask;(void)pins;assert(count==2);j->jack=j;jack_creations++;return 0;
}
static void snd_jack_set_key(void *j,unsigned button,unsigned key) {(void)j;(void)button;(void)key;}
static int snd_soc_component_set_jack(void *c,struct snd_soc_jack *j,void *data) {
 (void)c;(void)data;assert(j && j->jack);mbhc_starts++;return jack_error;
}
#define for_each_rtd_codec_dais(r,i,c) for (i=0,c=&r->codec;i<1;i++)
static bool of_device_is_compatible(int node, const char *s) { (void)s; return node==1; }
static unsigned map_count, map_mask;
static int map_error;
static int snd_soc_dai_get_channel_map(struct snd_soc_dai *d, unsigned *t, unsigned *tm, unsigned *r, unsigned *rm) {
 (void)d; (void)t; (void)tm; *r=map_count; *rm=map_mask; return map_error;
}
static int dev_err_probe(struct dev *d, int e, const char *s) { (void)d; (void)s; return e; }
static struct snd_interval *hw_param_interval(struct snd_pcm_hw_params *p, int n) { return n==SNDRV_PCM_HW_PARAM_RATE?&p->rate:&p->channels; }
static struct snd_mask *hw_param_mask(struct snd_pcm_hw_params *p, int n) { (void)n; return &p->format; }
static void snd_mask_none(struct snd_mask *f) { f->fmt=0; }
static void snd_mask_set_format(struct snd_mask *f, int n) { f->fmt=n; }
'''
main = r'''
int main(void) {
 struct dev dev={1}; struct snd_soc_card card={.dev=&dev};struct link link={.no_pcm=1};
 struct snd_soc_pcm_runtime r={.card=&card,.codec={.dev=&dev},.dai_link=&link};
 struct snd_pcm_hw_params p={0};
 for (int id=RX_CODEC_DMA_RX_0; id<=RX_CODEC_DMA_RX_1; id++) {
  r.cpu.id=id;
  for (unsigned n=1;n<=2;n++) {
   map_count=n; map_mask=(1u<<n)-1;
   assert(fixup(&r,&p)==0 && p.channels.min==n && p.channels.max==n);
   assert(p.rate.min==48000 && p.rate.max==48000 && p.format.fmt==16);
  }
 }
 puts("PASS mono handset/speaker and stereo headphones backend format/count");
 map_count=0; map_mask=0; assert(fixup(&r,&p)==-EINVAL);
 map_count=1; map_mask=3; assert(fixup(&r,&p)==-EINVAL);
 map_count=1; map_mask=4; assert(fixup(&r,&p)==-EINVAL);
 map_error=-EIO; assert(fixup(&r,&p)==-EIO); map_error=0;
 dev.of_node=0; assert(fixup(&r,&p)==-ENODEV); dev.of_node=1;
 puts("PASS missing/invalid RX lane map refuses configuration");
 r.cpu.id=TX_CODEC_DMA_TX_3; assert(fixup(&r,&p)==0 && p.channels.min==1 && p.format.fmt==16);
 r.cpu.id=SECONDARY_MI2S_RX; assert(fixup(&r,&p)==0 && p.channels.min==2 && p.format.fmt==32);
 puts("PASS existing capture and ESS backend formats retained");
 r.cpu.id=RX_CODEC_DMA_RX_0;
 assert(qcom_snd_wcd_jack_setup(&r,&card.data.jack,&card.data.jack_ready)==0);
 assert(jack_creations==1 && mbhc_starts==0); /* Reproduce old silent success. */
 assert(init(&r)==0 && mbhc_starts==0);
 r.cpu.id=TX_CODEC_DMA_TX_3;
 assert(init(&r)==0 && jack_creations==1 && mbhc_starts==1);
 puts("PASS real common helper RX0 creates controls only; production TX3 init starts WCD MBHC");
 jack_error=-EIO;assert(init(&r)==-EIO && mbhc_starts==2);
 r.cpu.id=TX_CODEC_DMA_TX_4;assert(init(&r)==0 && mbhc_starts==2);
 link.no_pcm=0;r.cpu.id=TX_CODEC_DMA_TX_3;assert(init(&r)==0 && mbhc_starts==2);
 puts("PASS codec set_jack failure propagates; other links/frontends do not restart detector");
 return 0;
}
'''
p = test/'card-test.c'; p.write_text(prelude+jack_helper+'\n'.join(functions)+main)
env=dict(os.environ)
env['PATH']=':'.join(json.loads((Path.home()/'A9-build/mainline/demo-integrated-20261019/source.json').read_text())['tools'])+':'+env['PATH']
sp.run(['gcc','-O2','-Wall','-Wextra','-Werror',str(p),'-o',str(test/'card-test')],env=env,check=True)
r=sp.run([str(test/'card-test')],capture_output=True,text=True)
(out/'card-test.log').write_text(r.stdout+r.stderr)
print(r.stdout+r.stderr); assert r.returncode==0
