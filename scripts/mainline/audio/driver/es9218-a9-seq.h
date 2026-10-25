/* SPDX-License-Identifier: GPL-2.0-only */
/* Board-specific sequence recovered from Hisense audio_es9218.ko, not a datasheet. */
#ifndef ES9218_A9_SEQ_H
#define ES9218_A9_SEQ_H
#ifdef __KERNEL__
#include <linux/errno.h>
#include <linux/types.h>
#else
#include <errno.h>
#include <stdbool.h>
#endif

enum es9218_pin { ES_RESET, ES_SELECT, ES_OSC, ES_PA };
enum es9218_mode { ES_STANDBY, ES_BYPASS, ES_HIFI };
struct es9218_io {
 int (*write)(void *ctx, unsigned int reg, unsigned int val);
 int (*read)(void *ctx, unsigned int reg, unsigned int *val);
 void (*gpio)(void *ctx, enum es9218_pin pin, int physical_level);
 void (*delay)(void *ctx, unsigned int min_us, unsigned int max_us);
};
struct es9218_seq {
 const struct es9218_io *io;
 void *ctx;
 enum es9218_mode mode;
 bool registers_live;
 unsigned int width;
};
struct es9218_pair { unsigned char reg, val; };
static const struct es9218_pair es9218_stock_init[] = {
 { 0x00, 0x00 },
 { 0x01, 0x80 },
 { 0x02, 0x34 },
 { 0x03, 0x70 },
 { 0x04, 0x00 },
 { 0x05, 0x68 },
 { 0x06, 0x07 },
 { 0x07, 0x40 },
 { 0x08, 0xdd },
 { 0x09, 0x00 },
 { 0x0a, 0x02 },
 { 0x0b, 0x00 },
 { 0x0c, 0x77 },
 { 0x0d, 0x00 },
 { 0x0e, 0x25 },
 { 0x0f, 0x00 },
 { 0x10, 0x00 },
 { 0x11, 0xff },
 { 0x12, 0xff },
 { 0x13, 0xff },
 { 0x14, 0x62 },
 { 0x15, 0x00 },
 { 0x16, 0x90 },
 { 0x17, 0x00 },
 { 0x18, 0x36 },
 { 0x19, 0x00 },
 { 0x1a, 0x62 },
 { 0x1b, 0xc4 },
 { 0x1c, 0xf0 },
 { 0x1d, 0x05 },
 { 0x1e, 0x00 },
 { 0x1f, 0x00 },
 { 0x20, 0x03 },
 { 0x21, 0x3c },
 { 0x22, 0x00 },
 { 0x23, 0x00 },
 { 0x24, 0x00 },
 { 0x25, 0x00 },
 { 0x26, 0x00 },
 { 0x27, 0x00 },
 { 0x28, 0x00 },
 { 0x29, 0x00 },
 { 0x2a, 0x00 },
 { 0x2b, 0x00 },
 { 0x2c, 0x00 },
 { 0x2d, 0x00 },
 { 0x2e, 0x00 },
};

static inline int es9218_validate_params(unsigned int rate, unsigned int channels,
                                       unsigned int width)
{
 /* Deliberate initial board policy; broader stock advertised rates are unvalidated. */
 return rate == 48000 && channels == 2 && (width == 16 || width == 24 || width == 32) ? 0 : -EINVAL;
}

static inline void es9218_gpio_standby(struct es9218_seq *s)
{
 s->io->gpio(s->ctx, ES_RESET, 0);
 s->io->delay(s->ctx, 5000, 5200);
 s->io->gpio(s->ctx, ES_SELECT, 1);
 s->io->delay(s->ctx, 5000, 5200);
 s->io->gpio(s->ctx, ES_OSC, 0);
 s->io->gpio(s->ctx, ES_PA, 0);
 s->registers_live = false;
 s->mode = ES_STANDBY;
}

static inline int es9218_power_off(struct es9218_seq *s)
{
 int ret = 0;
 /* Stock power_enable(0): always finish GPIO containment even when I2C fails. */
 if (s->registers_live) {
  ret = s->io->write(s->ctx, 0x20, 0);
  s->io->delay(s->ctx, 10000, 15000);
 }
 s->io->gpio(s->ctx, ES_RESET, 0);
 s->io->gpio(s->ctx, ES_SELECT, 1);
 s->io->delay(s->ctx, 5000, 5500);
 s->io->gpio(s->ctx, ES_OSC, 0);
 s->io->gpio(s->ctx, ES_PA, 0);
 s->registers_live = false;
 s->mode = ES_STANDBY;
 return ret;
}

static inline int es9218_write_checked(struct es9218_seq *s, unsigned int reg,
                                       unsigned int val)
{
 unsigned int readback;
 int ret, attempt;
 /* Stock do_reg_write_check retries one mismatched readback; errors propagate. */
 for (attempt = 0; attempt < 2; attempt++) {
  ret = s->io->write(s->ctx, reg, val);
  if (ret) return ret;
  ret = s->io->read(s->ctx, reg, &readback);
  if (ret) return ret;
  if (readback == val) return 0;
 }
 return -EIO;
}

/* Stock probe pattern: up to six reads of 0x40, 5 ms apart. *id = last value read. */
static inline int es9218_read_id(struct es9218_seq *s, unsigned int *id)
{
 int ret, i;
 for (i = 0; i < 6; i++) {
  ret = s->io->read(s->ctx, 0x40, id);
  if (ret) return ret;
  if ((*id & 0xfc) == 0xc0) return 0;
  if (i < 5) s->io->delay(s->ctx, 5000, 5000);
 }
 return -ENODEV;
}

static inline int es9218_identify(struct es9218_seq *s)
{
 unsigned int id;
 return es9218_read_id(s, &id);
}

static inline int es9218_set_bypass(struct es9218_seq *s)
{
 if (s->mode == ES_HIFI) return -EBUSY;
 s->io->gpio(s->ctx, ES_RESET, 0);
 s->io->delay(s->ctx, 10000, 15000);
 s->io->gpio(s->ctx, ES_OSC, 0);
 s->io->delay(s->ctx, 5000, 5200);
 s->io->gpio(s->ctx, ES_SELECT, 1);
 s->io->gpio(s->ctx, ES_PA, 1);
 s->mode = ES_BYPASS;
 s->registers_live = false;
 return 0;
}

static inline int es9218_hifi_on(struct es9218_seq *s)
{
 unsigned int i, val;
 int ret;
 if (s->mode != ES_STANDBY) return -EBUSY;
 if (s->width != 16 && s->width != 24 && s->width != 32) return -EINVAL;
 /* Stock set_mode_HiFi physical levels and all delay ranges verbatim. */
 s->io->gpio(s->ctx, ES_PA, 1);
 s->io->gpio(s->ctx, ES_OSC, 1);
 s->io->delay(s->ctx, 10000, 15000);
 s->io->gpio(s->ctx, ES_RESET, 1);
 s->io->delay(s->ctx, 5000, 5200);
 s->io->gpio(s->ctx, ES_RESET, 0);
 s->io->delay(s->ctx, 10000, 15000);
 s->io->gpio(s->ctx, ES_RESET, 1);
 s->io->delay(s->ctx, 5000, 5200);
 ret = es9218_identify(s);
 if (ret) goto fail;
 s->registers_live = true;
#define ES_WRITE(r, v) do { ret = s->io->write(s->ctx, r, v); if (ret) goto fail; } while (0)
 ES_WRITE(0x0e, 0x45);
 ES_WRITE(0x02, 0xb4);
 ES_WRITE(0x05, 0x00);
 ES_WRITE(0x04, 0xff);
 ES_WRITE(0x20, 0x80);
 s->io->delay(s->ctx, 3000, 3200);
 ES_WRITE(0x1d, 0x0d);
 s->io->delay(s->ctx, 5000, 5200);
 ES_WRITE(0x2e, 0x80);
 s->io->delay(s->ctx, 3000, 3200);
 ES_WRITE(0x20, 0x83);
 s->io->delay(s->ctx, 120000, 121000);
 ES_WRITE(0x2e, 0);
 ES_WRITE(0x05, 0x7f);
 s->io->delay(s->ctx, 5000, 5200);
 ES_WRITE(0x04, 0);
 ES_WRITE(0x0f, 0);
 ES_WRITE(0x10, 0);
 ES_WRITE(0x1b, 0xc4);
 for (i = 0; i < sizeof(es9218_stock_init) / sizeof(es9218_stock_init[0]); i++) {
  ret = es9218_write_checked(s, es9218_stock_init[i].reg, es9218_stock_init[i].val);
  if (ret) goto fail;
 }
 /* Stock 24-valid-bit route selects bit_32: S24_LE uses a 32-bit serial slot. */
 ret = s->io->read(s->ctx, 0x01, &val);
 if (ret) goto fail;
 ES_WRITE(0x01, (val & ~0xc0) | (s->width >= 24 ? 0x80 : 0x00));
 /* Start muted; the DAI mute callback explicitly releases this after stream setup. */
 ret = s->io->read(s->ctx, 0x07, &val);
 if (ret) goto fail;
 ES_WRITE(0x07, val | 1);
#undef ES_WRITE
 s->mode = ES_HIFI;
 return 0;
fail:
 es9218_power_off(s);
 return ret;
}

static inline int es9218_stop(struct es9218_seq *s)
{
 int ret = 0;
 if (!s->registers_live) return es9218_power_off(s);
#define ES_STOP_WRITE(r, v) do { ret = s->io->write(s->ctx, r, v); if (ret) goto off; } while (0)
 /* Stock DAI shutdown sequence; GPIO power-off is explicit containment added here. */
 ES_STOP_WRITE(0x0e, 0x05);
 ES_STOP_WRITE(0x02, 0xb4);
 ES_STOP_WRITE(0x05, 0);
 ES_STOP_WRITE(0x04, 0xff);
 ES_STOP_WRITE(0x0f, 0xff);
 ES_STOP_WRITE(0x10, 0xff);
 s->io->delay(s->ctx, 100000, 110000);
 ES_STOP_WRITE(0x20, 0x80);
 s->io->delay(s->ctx, 5000, 5200);
 ES_STOP_WRITE(0x1d, 0x0d);
 s->io->delay(s->ctx, 10000, 15000);
#undef ES_STOP_WRITE
off:
 if (ret) { es9218_power_off(s); return ret; }
 return es9218_power_off(s);
}

static inline int es9218_mute(struct es9218_seq *s, bool mute)
{
 unsigned int val;
 int ret;
 if (s->mode != ES_HIFI) return 0;
 ret = s->io->read(s->ctx, 0x07, &val);
 if (ret) goto fail;
 /* New driver policy: only known reg7 mute bit. Stock ASoC callback is a NOOP.
  * Do not transplant the unrelated legacy helper's reg0e=02/8a mode changes. */
 ret = s->io->write(s->ctx, 0x07, mute ? val | 1 : val & ~1);
 if (ret) goto fail;
 return 0;
fail:
 es9218_power_off(s);
 return ret;
}
/*
 * POWERED bring-up self test (runs/audio-stage-plan.md, stages P1/P2; NOT
 * stage A). No PCM, no I2S clock, no DAPM. It raises PA68 (PA/PVCC) and OSC66
 * exactly as stock probe and stock HiFi do before their ID read (review 51
 * R51-1: stock never reads the ID with PA68 low, so there is no evidence-backed
 * unpowered ID path). Reset is held while PA/OSC rise, as in stock.
 *   ES_SELFTEST_ID:   read 0x40 only; ZERO register writes.
 *   ES_SELFTEST_INIT: right after the ID, mute (07=41) and full raw attenuation
 *                     (0f/10=ff) are written and read back BEFORE the stock
 *                     table; the table is then replayed with those three
 *                     entries replaced by the muted values, so no write in the
 *                     sequence clears the mute bit or lowers attenuation
 *                     (R51-2). Dump 0x00..0x2e, then the stock DAI shutdown
 *                     ramp (es9218_stop) and the stock power-off. On any error
 *                     after the first write: best-effort mute/attenuate, the
 *                     shutdown ramp, then GPIO power-off.
 * The mute bit's meaning, 0xff = quietest and the role of 0x20 (table 0x20=03)
 * are INFERRED; this lowers, but cannot prove away, an analog power-up click.
 * Always ends in Standby.
 */
enum es9218_selftest_level { ES_SELFTEST_ID = 1, ES_SELFTEST_INIT = 2 };
#define ES9218_DUMP_LEN 0x2f
#define ES9218_ID_NONE 0x100u
#define ES9218_MUTED_07 0x41u

static inline unsigned int es9218_muted_value(unsigned int reg, unsigned int val)
{
 if (reg == 0x07) return val | 1;
 if (reg == 0x0f || reg == 0x10) return 0xff;
 return val;
}

static inline int es9218_selftest(struct es9218_seq *s, int level, unsigned int *id,
                                  unsigned char *dump)
{
 unsigned int i, val;
 int ret, off;
 if (s->mode != ES_STANDBY) return -EBUSY;
 if (level != ES_SELFTEST_ID && level != ES_SELFTEST_INIT) return -EINVAL;
 *id = ES9218_ID_NONE;
 s->io->gpio(s->ctx, ES_PA, 1);
 s->io->gpio(s->ctx, ES_OSC, 1);
 s->io->delay(s->ctx, 10000, 15000);
 s->io->gpio(s->ctx, ES_RESET, 1);
 s->io->delay(s->ctx, 5000, 5200);
 s->io->gpio(s->ctx, ES_RESET, 0);
 s->io->delay(s->ctx, 10000, 15000);
 s->io->gpio(s->ctx, ES_RESET, 1);
 s->io->delay(s->ctx, 5000, 5200);
 ret = es9218_read_id(s, id);
 if (ret || level == ES_SELFTEST_ID) {
  off = es9218_power_off(s);   /* registers_live is false: no write */
  return ret ? ret : off;
 }
 s->registers_live = true;
 ret = es9218_write_checked(s, 0x07, ES9218_MUTED_07);
 if (!ret) ret = es9218_write_checked(s, 0x0f, 0xff);
 if (!ret) ret = es9218_write_checked(s, 0x10, 0xff);
 for (i = 0; !ret && i < sizeof(es9218_stock_init) / sizeof(es9218_stock_init[0]); i++)
  ret = es9218_write_checked(s, es9218_stock_init[i].reg,
                             es9218_muted_value(es9218_stock_init[i].reg, es9218_stock_init[i].val));
 for (i = 0; !ret && i < ES9218_DUMP_LEN; i++) {
  ret = s->io->read(s->ctx, i, &val);
  if (!ret) dump[i] = (unsigned char)val;
 }
 if (ret) {   /* best effort; errors ignored, containment below always runs */
  s->io->write(s->ctx, 0x07, ES9218_MUTED_07);
  s->io->write(s->ctx, 0x0f, 0xff);
  s->io->write(s->ctx, 0x10, 0xff);
 }
 off = es9218_stop(s);   /* stock DAI shutdown ramp, then stock power-off (20=0) */
 return ret ? ret : off;
}
#endif
