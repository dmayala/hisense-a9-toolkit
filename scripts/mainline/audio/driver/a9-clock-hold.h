/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef A9_CLOCK_HOLD_H
#define A9_CLOCK_HOLD_H
#include <linux/clk.h>

/* Diagnostic mitigation: pay six acknowledged DSP clock votes at card bind,
 * before serving PCM, and prevent zero-count transitions until card removal.
 * Initial prepare/unprepare still use CCF locking. No provider API is bypassed.
 */
struct a9_clock_hold {
 struct clk *clocks[6];
 unsigned int count;
};

static void a9_clock_hold_release(void *data)
{
 struct a9_clock_hold *h = data;
 while (h->count) {
  struct clk *clk = h->clocks[--h->count];
  clk_unprepare(clk);
  clk_put(clk);
  h->clocks[h->count] = NULL;
 }
}

static int a9_clock_hold_pair(struct a9_clock_hold *h, struct device_node *np)
{
 static const char * const names[] = {"mclk", "npl"};
 struct clk *clk;
 unsigned int i;
 int ret;
 for (i = 0; i < ARRAY_SIZE(names); i++) {
  if (h->count == ARRAY_SIZE(h->clocks)) return -EOVERFLOW;
  clk = of_clk_get_by_name(np, names[i]);
  if (IS_ERR(clk)) return PTR_ERR(clk);
  /* Existing SM6115 macro probes select these rates. Never retune a clock. */
  if (clk_get_rate(clk) != 19200000) { clk_put(clk); return -EINVAL; }
  ret = clk_prepare(clk);
  if (ret) { clk_put(clk); return ret; }
  h->clocks[h->count++] = clk;
 }
 return 0;
}

static int a9_clock_hold_start(struct snd_soc_card *card, struct a9_clock_hold *h)
{
 struct snd_soc_component *c;
 struct device_node *rx = NULL, *tx = NULL;
 struct of_phandle_args va;
 int index, ret;

 if (h->count == ARRAY_SIZE(h->clocks)) return 0;
 if (h->count) return -EBUSY;
 for_each_card_components(card, c) {
  if (of_device_is_compatible(c->dev->of_node, "qcom,sm6115-lpass-rx-macro"))
   rx = c->dev->of_node;
  if (of_device_is_compatible(c->dev->of_node, "qcom,sm6115-lpass-tx-macro"))
   tx = c->dev->of_node;
 }
 if (!rx || !tx) return -ENODEV;
 /* VA is RX's fsgen provider, not an A9 backend DAI component. */
 index = of_property_match_string(rx, "clock-names", "fsgen");
 if (index < 0) return index;
 ret = of_parse_phandle_with_args(rx, "clocks", "#clock-cells", index, &va);
 if (ret) return ret;
 if (!of_device_is_compatible(va.np, "qcom,sm6115-lpass-va-macro")) {
  of_node_put(va.np);
  return -ENODEV;
 }
 ret = a9_clock_hold_pair(h, va.np);
 of_node_put(va.np);
 if (!ret) ret = a9_clock_hold_pair(h, tx);
 if (!ret) ret = a9_clock_hold_pair(h, rx);
 if (ret) a9_clock_hold_release(h);
 return ret;
}
#endif
