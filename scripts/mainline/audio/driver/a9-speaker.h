/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef A9_SPEAKER_H
#define A9_SPEAKER_H
#include <linux/types.h>
struct snd_soc_card;
int a9_aw87519_set_stream(struct snd_soc_card *card, bool running);
#endif
