// SPDX-License-Identifier: GPL-2.0-only
/* A9 AW8624/AW8695/AW8697 LRA bring-up: input FF_RUMBLE, one generated sine in SRAM.
 * Register descriptions: Awinic aw8624, AW8695/AW8697 input patches,
 * and stock AW869x set_gain/get_glb_state register accesses.
 * AW8695 defaults to stock steady-wave amplitude and RAM_LOOP bypass.
 * See stock-equivalent.md for the stock profile and boost comparison.
 * F0 measurement runs once before registering input, and can be repeated
 * while idle. It zeros/restores volatile trim; no persistent calibration
 * writes. See resonance.md, f0-diagnostics.md and amplitude.md.
 */
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/i2c.h>
#include <linux/input.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/pm.h>
#include <linux/regmap.h>
#include <linux/sysfs.h>
#include <linux/workqueue.h>
#include "sine.h"

#define SYSCTRL 0x04
#define GO 0x05
#define STANDBY BIT(0)
#define BOOST BIT(1)
#define RAMINIT BIT(5)
#define RAMBASE 0x0800
#define WAVE_1X BIT(6) /* SYSCTRL WAVDAT_MODE=1: stock 1x upsampling */
#define F0_CYCLES 8 /* Reduce whole-sample period rounding in the RAM loop. */

static bool auto_f0 = true;
module_param(auto_f0, bool, 0444);
MODULE_PARM_DESC(auto_f0, "Measure AW8695 resonance at probe (brief calibration buzz)");

struct a9_haptics {
	struct device *dev;
	struct regmap *regmap;
	struct gpio_desc *reset;
	struct input_dev *input;
	struct work_struct work;
	struct mutex lock;
	u16 level;
	u8 gain_reg, state_reg, gain;
	bool aw8695;
	bool suspended;
	bool measuring, playing, ram_ready;
	bool stock_profile;
	unsigned int vbat_mv;
	u32 f0_tenths;
	int f0_error;
	unsigned int wave_count, wave_cycles;
	u8 wave[240 * F0_CYCLES]; /* Eight cycles at 100 Hz; ends below 4 KiB. */
	ktime_t started;
};

/* Readbacks, not assertions that the requested voltage reached the motor.
 * SYSINT is latched/read-to-clear: consume it only after stopping playback.
 */
static void a9_status(struct a9_haptics *h, const char *phase, bool stopped)
{
	static const u8 regs[] = {
		0x01, 0x04, 0x05, 0x2e, 0x30, 0x34, 0x39, 0x3a,
		0x46, 0x47, 0x48, 0x5b, 0x62, 0x66, 0x68, 0x69,
		0x6a, 0x6b, 0x2b, 0x49, 0x4a,
	};
	unsigned int v[ARRAY_SIZE(regs)], irq = 0;
	int i, ret;

	if (!h->aw8695)
		return;
	for (i = 0; i < ARRAY_SIZE(regs); i++) {
		ret = regmap_read(h->regmap, regs[i], &v[i]);
		if (ret) {
			dev_warn(h->dev, "%s readback reg %02x failed: %d\n",
				 phase, regs[i], ret);
			return;
		}
	}
	if (stopped) {
		ret = regmap_read(h->regmap, 0x02, &irq);
		if (ret) {
			dev_warn(h->dev, "%s SYSINT read failed: %d\n", phase, ret);
			return;
		}
	}
	dev_info(h->dev, "%s F0=%u.%u Hz (%s) RAM=%u samples/%u mHz; SYSST=%02x SYSCTRL=%02x GO=%02x PWM=%02x DBGSTAT=%02x BSTCFG=%02x GAIN=%02x BSTVOL=%02x STATE=%02x AUTO=%02x stock_profile=%u VBAT=%u mV\n",
		 phase, h->f0_tenths / 10, h->f0_tenths % 10,
		 h->f0_tenths ? "measured" : "not measured", h->wave_count,
		 24000000U * h->wave_cycles / h->wave_count, v[0], v[1], v[2], v[3], v[4],
		 v[5], v[6], v[7], v[8], v[9], h->stock_profile, h->vbat_mv);
	dev_info(h->dev, "%s CONT=%02x TRIM=%02x VBATraw=%02x ADCTEST=%02x F0raw=%02x%02x CONTraw=%02x%02x DATCTRL=%02x FPRE=%02x%02x SYSINT=%s/%02x\n",
		 phase, v[10], v[11], v[12], v[13], v[14], v[15],
		 v[16], v[17], v[18], v[19], v[20],
		 stopped ? "latched" : "unread", irq);
}

static int a9_init_ram(struct a9_haptics *h);

/* Stock RAM_LOOP gain compensation. gain is the configured baseline, not
 * the final register value. The boosted comparison keeps its original gain.
 */
static unsigned int a9_drive_gain(struct a9_haptics *h, unsigned int gain)
{
	if (h->aw8695 && h->stock_profile && h->vbat_mv)
		return min(gain * 4200U / h->vbat_mv, 179U);
	return gain;
}

/* Stock aw869x_get_vbat: detect in standby with RAMINIT, read VBATDET62,
 * convert 6100*raw/256 and clamp to 3000..4500 mV. Always exit RAMINIT.
 */
static int a9_measure_vbat(struct a9_haptics *h)
{
	unsigned int state, raw;
	int ret, cleanup;

	h->vbat_mv = 0;
	ret = regmap_set_bits(h->regmap, SYSCTRL, RAMINIT);
	if (!ret)
		ret = regmap_set_bits(h->regmap, 0x5f, BIT(1));
	if (!ret)
		ret = regmap_read_poll_timeout(h->regmap, 0x5f, state,
					      !(state & BIT(1)), 100, 10000);
	if (!ret)
		ret = regmap_read(h->regmap, 0x62, &raw);
	cleanup = regmap_clear_bits(h->regmap, SYSCTRL, RAMINIT);
	if (cleanup)
		h->ram_ready = false;
	if (ret || cleanup)
		return ret ?: cleanup;
	h->vbat_mv = clamp(raw * 6100U / 256U, 3000U, 4500U);
	return 0;
}

static int a9_stop(struct a9_haptics *h)
{
	unsigned int state;
	int ret, idle, standby;

	/* The GO pulse must last at least 2 ms (Awinic reference). */
	if (ktime_ms_delta(ktime_get(), h->started) < 2)
		usleep_range(2000, 2500);
	ret = regmap_write(h->regmap, GO, 0);
	idle = regmap_read_poll_timeout(h->regmap, h->state_reg, state,
				       !(state & 0x0f), 2000, 200000);
	if (idle)
		dev_warn_ratelimited(h->dev, "standby wait failed: %d\n", idle);
	/* Force standby even if GO/readback failed. */
	standby = regmap_update_bits(h->regmap, SYSCTRL,
				 STANDBY | (h->aw8695 ? BOOST : 0), STANDBY);
	if (ret || idle || standby)
		h->ram_ready = false;
	if (h->playing) {
		h->playing = false;
		a9_status(h, "stop", true);
	}
	return ret ?: idle ?: standby;
}

static int a9_start(struct a9_haptics *h, u16 level)
{
	int ret;

	ret = a9_stop(h);
	if (ret)
		return ret;
	if (!h->ram_ready)
		return -EIO;
	if (h->aw8695 && h->stock_profile) {
		ret = a9_measure_vbat(h);
		if (ret)
			return ret;
	}
	/* Wave 1 forever; wave 2 is the sequence terminator. */
	ret = regmap_write(h->regmap, 0x07, 1);
	if (ret)
		return ret;
	ret = regmap_write(h->regmap, 0x08, 0);
	if (ret)
		return ret;
	ret = regmap_write(h->regmap, 0x0f, 0xf0);
	if (ret)
		return ret;
	ret = regmap_write(h->regmap, h->gain_reg,
			   DIV_ROUND_UP(level * a9_drive_gain(h, h->gain), 65535));
	if (ret)
		return ret;
	/* Activate RAM in bypass first, as in stock aw869x_play_mode(). */
	ret = regmap_update_bits(h->regmap, SYSCTRL, GENMASK(3, 0), 0);
	if (ret)
		return ret;
	if (h->aw8695 && !h->stock_profile) {
		/* Stock boosted RAM mode, with its 2..2.5 ms boost settle. */
		ret = regmap_set_bits(h->regmap, SYSCTRL, BOOST);
		if (ret)
			return ret;
		usleep_range(2000, 2500);
	}
	ret = regmap_write(h->regmap, GO, 1);
	if (!ret) {
		h->started = ktime_get();
		h->playing = true;
		if (h->aw8695) {
			usleep_range(2000, 2500);
			a9_status(h, "play", false);
		}
	}
	return ret;
}

static void a9_play_work(struct work_struct *work)
{
	struct a9_haptics *h = container_of(work, struct a9_haptics, work);
	u16 level;
	int ret;

	mutex_lock(&h->lock);
	if (!READ_ONCE(h->suspended)) {
		level = READ_ONCE(h->level);
		ret = level ? a9_start(h, level) : a9_stop(h);
		if (ret) {
			a9_stop(h);
			dev_err_ratelimited(h->dev, "FF playback failed: %d\n", ret);
		}
	}
	mutex_unlock(&h->lock);
}

static int a9_play(struct input_dev *input, void *data, struct ff_effect *effect)
{
	struct a9_haptics *h = input_get_drvdata(input);

	if (READ_ONCE(h->suspended) || READ_ONCE(h->measuring))
		return -EBUSY;
	WRITE_ONCE(h->level, max(effect->u.rumble.strong_magnitude,
				effect->u.rumble.weak_magnitude));
	schedule_work(&h->work);
	return 0;
}

static void a9_close(struct input_dev *input)
{
	struct a9_haptics *h = input_get_drvdata(input);

	cancel_work_sync(&h->work);
	mutex_lock(&h->lock);
	WRITE_ONCE(h->level, 0);
	if (!READ_ONCE(h->suspended))
		a9_stop(h);
	mutex_unlock(&h->lock);
}

static int a9_init_ram(struct a9_haptics *h)
{
	const u8 *wave = h->aw8695 ? h->wave : a9_sine;
	size_t count = h->aw8695 ? h->wave_count : ARRAY_SIZE(a9_sine);
	/* SRAM header: version, first sample address, inclusive last address. */
	u16 end = RAMBASE + 5 + count - 1;
	u8 header[] = { 1, (RAMBASE + 5) >> 8, (RAMBASE + 5) & 0xff,
			end >> 8, end & 0xff };
	int ret, i;

	h->ram_ready = false;
	ret = regmap_write(h->regmap, SYSCTRL, STANDBY | RAMINIT |
			   (h->aw8695 ? WAVE_1X : 0));
	if (ret)
		return ret;
	usleep_range(1000, 1500);
	ret = regmap_write(h->regmap, 0x21, RAMBASE >> 8);
	if (ret)
		return ret;
	ret = regmap_write(h->regmap, 0x22, RAMBASE & 0xff);
	if (ret)
		return ret;
	ret = regmap_write(h->regmap, 0x40, RAMBASE >> 8);
	if (ret)
		return ret;
	ret = regmap_write(h->regmap, 0x41, RAMBASE & 0xff);
	if (ret)
		return ret;
	/* Byte writes work on every adapter and do not increment the register. */
	for (i = 0; i < ARRAY_SIZE(header); i++) {
		ret = regmap_write(h->regmap, 0x42, header[i]);
		if (ret)
			return ret;
	}
	for (i = 0; i < count; i++) {
		ret = regmap_write(h->regmap, 0x42, wave[i]);
		if (ret)
			return ret;
	}
	ret = regmap_clear_bits(h->regmap, SYSCTRL, RAMINIT);
	if (!ret)
		h->ram_ready = true;
	return ret;
}

static int a9_aw8695_drive_init(struct a9_haptics *h)
{
	/* A9 stock DT values and aw869x_misc_para_init() register mapping.
	 * File offsets and the deliberate RAM_LOOP/boost difference are in
	 * stock-strength.md. Do not apply this board profile to other chip IDs.
	 */
	static const struct reg_sequence setup[] = {
		{ 0x31, 0x30 }, { 0x32, 0xeb }, { 0x33, 0xd4 }, /* bstdbg */
		{ 0x4d, 0x12 }, /* tset */
		{ 0x5d, 0x68 }, /* r_spare */
		{ 0x74, 0x10 }, { 0x75, 0x08 }, /* bemf_config */
		{ 0x76, 0x03 }, { 0x77, 0xf8 },
	};
	int ret;

	ret = regmap_multi_reg_write(h->regmap, setup, ARRAY_SIZE(setup));
	if (ret)
		return ret;
	ret = regmap_update_bits(h->regmap, 0x35, GENMASK(3, 2), 0x0c);
	if (ret)
		return ret;
	ret = regmap_set_bits(h->regmap, 0x36, BIT(5));
	if (ret)
		return ret;
	/* AW8695 peak-current code 1 (2 A); AW8697's code 5 is different. */
	ret = regmap_update_bits(h->regmap, 0x34, GENMASK(2, 0), 1);
	if (ret)
		return ret;
	/* Stock 8500 mV request: floor((8500 - 6000) / 142) << 1 = 0x22. */
	ret = regmap_update_bits(h->regmap, 0x3a, GENMASK(5, 1), 0x22);
	if (ret)
		return ret;
	/* RAM_LOOP uses software compensation, not CONT hardware compensation. */
	return regmap_clear_bits(h->regmap, 0x66, BIT(6));
}

/* Resample the existing generated sine. Preserve its peak and 24 kHz PWM;
 * only the period changes. No imported vendor waveform or floating point.
 */
static void a9_tune_wave(struct a9_haptics *h, unsigned int f0_tenths)
{
	unsigned int i, index, fraction, count;
	int a, b;

	count = DIV_ROUND_CLOSEST(240000U * F0_CYCLES, f0_tenths);
	for (i = 0; i < count; i++) {
		index = (i * ARRAY_SIZE(a9_sine_aw8695) * F0_CYCLES / count) %
			ARRAY_SIZE(a9_sine_aw8695);
		fraction = i * ARRAY_SIZE(a9_sine_aw8695) * F0_CYCLES % count;
		a = (s8)a9_sine_aw8695[index];
		b = (s8)a9_sine_aw8695[(index + 1) % ARRAY_SIZE(a9_sine_aw8695)];
		a += DIV_ROUND_CLOSEST((b - a) * (int)fraction, (int)count);
		h->wave[i] = h->stock_profile ? DIV_ROUND_CLOSEST(a * 78, 126) : a;
	}
	h->wave_count = count;
	h->wave_cycles = F0_CYCLES;
}

/* A9 stock aw869x_get_f0(), without its persistent calibration writes.
 * Probe runs this before input registration; sysfs can repeat it while idle.
 * Save and restore volatile trim/config even when detection fails.
 */
static int a9_measure_f0(struct a9_haptics *h)
{
	static const u8 regs[] = {
		0x2b, 0x48, 0x5b, 0x66, 0x49, 0x4a, 0x7b, 0x7d, 0x7e, 0x7f, 0x39,
	};
	static const struct reg_sequence setup[] = {
		{ 0x49, 0x06 }, { 0x4a, 0x64 }, /* 1e9 / (2350 * 260) */
		{ 0x7b, 0x7d }, /* stock cont_drv_lev */
		{ 0x7d, 0x53 }, { 0x7e, 0x02 }, { 0x7f, 0x0f },
	};
	unsigned int saved[ARRAY_SIZE(regs)], state, hi, lo, raw = 0, f0 = 0;
	int ret, cleanup, i;

	ret = a9_stop(h);
	if (ret)
		return ret;
	for (i = 0; i < ARRAY_SIZE(regs); i++) {
		ret = regmap_read(h->regmap, regs[i], &saved[i]);
		if (ret)
			return ret; /* No measurement settings have changed yet. */
	}
	ret = regmap_multi_reg_write(h->regmap, setup, ARRAY_SIZE(setup));
	if (ret)
		goto restore;
	/* f0_show() uploads zero into volatile TRIM_LRA before measuring. */
	ret = regmap_write(h->regmap, 0x5b, 0);
	if (ret)
		goto restore;
	ret = regmap_write(h->regmap, h->gain_reg, 128);
	if (ret)
		goto restore;
	ret = regmap_update_bits(h->regmap, 0x48, GENMASK(3, 2), BIT(2));
	if (ret)
		goto restore;
	/* Stock helper masks 0xe0 with ~0x9f: only bits 6:5 become 0x60. */
	ret = regmap_update_bits(h->regmap, 0x2b, GENMASK(6, 5), 0x60);
	if (ret)
		goto restore;
	ret = regmap_set_bits(h->regmap, 0x66, BIT(6));
	if (ret)
		goto restore;
	ret = regmap_update_bits(h->regmap, SYSCTRL, GENMASK(3, 0), BIT(3));
	if (ret)
		goto restore;
	/* Clear old interrupts as stock does, before GO. */
	ret = regmap_read(h->regmap, 0x02, &state);
	if (ret)
		goto restore;
	ret = regmap_write(h->regmap, GO, 1);
	if (ret)
		goto restore;
	h->started = ktime_get();
	usleep_range(2000, 2500);
	a9_status(h, "f0-start", false);
	ret = regmap_read(h->regmap, h->state_reg, &state);
	if (ret)
		goto restore;
	if (!(state & 0x0f)) {
		ret = -EIO; /* An immediately idle chip cannot supply a fresh F0. */
		goto restore;
	}
	/* Stock trace wait: (10000/2350)*(5+3+(15+3)*(2-1)) = 104 ms. */
	msleep(104);
	ret = regmap_read_poll_timeout(h->regmap, h->state_reg, state,
				      !(state & 0x0f), 2000, 200000);
	if (ret)
		goto restore;
	ret = regmap_read(h->regmap, 0x68, &hi);
	if (ret)
		goto restore;
	ret = regmap_read(h->regmap, 0x69, &lo);
	if (ret)
		goto restore;
	raw = (hi << 8) | lo;
	if (!raw) {
		ret = -ERANGE;
		goto restore;
	}
	f0 = 1000000000U / (raw * 260U); /* tenths of Hz, stock coefficient */
	if (f0 < 1000 || f0 > 4000)
		ret = -ERANGE; /* Log it, but do not index outside the wave buffer. */
restore:
	cleanup = a9_stop(h);
	if (cleanup)
		h->ram_ready = false;
	if (!ret)
		ret = cleanup;
	for (i = 0; i < ARRAY_SIZE(regs); i++) {
		cleanup = regmap_write(h->regmap, regs[i], saved[i]);
		if (cleanup)
			h->ram_ready = false;
		if (!ret)
			ret = cleanup;
	}
	if (!ret) {
		a9_tune_wave(h, f0);
		ret = a9_init_ram(h);
		if (!ret)
			h->f0_tenths = f0;
	}
	dev_info(h->dev, "F0 detection result=%d raw=%04x measured=%u.%u Hz nominal=235.0 Hz; RAM=%u samples\n",
		 ret, raw, f0 / 10, f0 % 10, h->wave_count);
	a9_status(h, "f0-stop", true);
	return ret;
}

static int a9_calibrate(struct a9_haptics *h)
{
	WRITE_ONCE(h->measuring, true);
	h->f0_error = a9_measure_f0(h);
	WRITE_ONCE(h->measuring, false);
	return h->f0_error;
}

static ssize_t f0_measure_show(struct device *dev,
			       struct device_attribute *attr, char *buf)
{
	struct a9_haptics *h = dev_get_drvdata(dev);
	ssize_t ret;

	mutex_lock(&h->lock);
	ret = sysfs_emit(buf, "measured_tenths_hz=%u ram_samples=%u playback_mHz=%u ready=%u cycles=%u last_error=%d\n",
			 h->f0_tenths, h->wave_count,
			 24000000U * h->wave_cycles / h->wave_count,
			 h->ram_ready, h->wave_cycles, h->f0_error);
	mutex_unlock(&h->lock);
	return ret;
}

static ssize_t f0_measure_store(struct device *dev,
				struct device_attribute *attr,
				const char *buf, size_t count)
{
	struct a9_haptics *h = dev_get_drvdata(dev);
	bool trigger;
	int ret;

	ret = kstrtobool(buf, &trigger);
	if (ret || !trigger)
		return -EINVAL;
	mutex_lock(&h->lock);
	if (!h->aw8695) {
		ret = -EOPNOTSUPP;
	} else if (READ_ONCE(h->suspended) || READ_ONCE(h->level)) {
		ret = -EBUSY;
	} else {
		ret = a9_calibrate(h);
	}
	mutex_unlock(&h->lock);
	return ret ?: count;
}
static DEVICE_ATTR_RW(f0_measure);

/* Switch only while idle. 1 restores the stock steady RAM_LOOP profile;
 * 0 reproduces the earlier boosted peak126 profile for a tactile comparison.
 * Both retain the configured gain and measured resonance.
 */
static int a9_set_stock_profile(struct a9_haptics *h, bool stock)
{
	int ret;

	if (!h->aw8695)
		return -EOPNOTSUPP;
	if (READ_ONCE(h->suspended) || READ_ONCE(h->measuring) ||
	    READ_ONCE(h->level) || h->playing)
		return -EBUSY;
	ret = a9_stop(h);
	if (ret)
		return ret;
	h->stock_profile = stock;
	h->vbat_mv = 0;
	a9_tune_wave(h, h->f0_tenths ?: 2350);
	ret = a9_init_ram(h);
	dev_info(h->dev, "stock_profile=%u peak=%u boost=%u vbat_comp=%u result=%d\n",
		 stock, stock ? 78 : 126, !stock, stock, ret);
	return ret;
}

static ssize_t stock_profile_show(struct device *dev,
				 struct device_attribute *attr, char *buf)
{
	struct a9_haptics *h = dev_get_drvdata(dev);

	return sysfs_emit(buf, "%u\n", READ_ONCE(h->stock_profile));
}

static ssize_t stock_profile_store(struct device *dev,
				  struct device_attribute *attr,
				  const char *buf, size_t count)
{
	struct a9_haptics *h = dev_get_drvdata(dev);
	bool stock;
	int ret;

	ret = kstrtobool(buf, &stock);
	if (ret)
		return ret;
	mutex_lock(&h->lock);
	ret = a9_set_stock_profile(h, stock);
	mutex_unlock(&h->lock);
	return ret ?: count;
}
static DEVICE_ATTR_RW(stock_profile);

/* Base gain at full FF magnitude; stock RAM_LOOP compensates for battery
 * voltage (maximum179). Zero mutes without restarting GO.
 * A live change scales the current effect, and persists across later effects
 * and F0 measurements until the driver is reloaded.
 */
static int a9_set_gain(struct a9_haptics *h, unsigned int gain)
{
	unsigned int effective;
	int ret;

	if (gain > 255)
		return -ERANGE;
	if (!h->aw8695)
		return -EOPNOTSUPP;
	if (READ_ONCE(h->suspended) || READ_ONCE(h->measuring))
		return -EBUSY;
	effective = h->playing ?
		DIV_ROUND_UP(READ_ONCE(h->level) * a9_drive_gain(h, gain), 65535) : gain;
	ret = regmap_write(h->regmap, h->gain_reg, effective);
	if (ret) {
		a9_stop(h);
		return ret;
	}
	h->gain = gain;
	dev_info(h->dev, "gain configured=%u effective=%u; peak=%u stock_profile=%u VBAT=%u mV\n",
		 gain, effective, h->stock_profile ? 78 : 126,
		 h->stock_profile, h->vbat_mv);
	a9_status(h, "gain", false);
	return 0;
}

static ssize_t gain_show(struct device *dev,
			 struct device_attribute *attr, char *buf)
{
	struct a9_haptics *h = dev_get_drvdata(dev);
	ssize_t ret;

	mutex_lock(&h->lock);
	ret = sysfs_emit(buf, "%u\n", h->gain);
	mutex_unlock(&h->lock);
	return ret;
}

static ssize_t gain_store(struct device *dev,
			  struct device_attribute *attr,
			  const char *buf, size_t count)
{
	struct a9_haptics *h = dev_get_drvdata(dev);
	unsigned int gain;
	int ret;

	ret = kstrtouint(buf, 0, &gain);
	if (ret)
		return ret;
	mutex_lock(&h->lock);
	ret = a9_set_gain(h, gain);
	mutex_unlock(&h->lock);
	return ret ?: count;
}
static DEVICE_ATTR_RW(gain);

static struct attribute *a9_attrs[] = {
	&dev_attr_f0_measure.attr,
	&dev_attr_gain.attr,
	&dev_attr_stock_profile.attr,
	NULL,
};
ATTRIBUTE_GROUPS(a9);

static const struct regmap_config a9_regmap = {
	.reg_bits = 8, .val_bits = 8, .max_register = 0x7f,
};

static void a9_reset(void *data)
{
	struct a9_haptics *h = data;
	gpiod_set_value_cansleep(h->reset, 1);
}

static int a9_probe(struct i2c_client *client)
{
	struct a9_haptics *h;
	unsigned int id, gain, reset_ctrl;
	int ret;

	h = devm_kzalloc(&client->dev, sizeof(*h), GFP_KERNEL);
	if (!h)
		return -ENOMEM;
	h->dev = &client->dev;
	h->gain = 128; /* Preserve the legacy AW8624/AW8697 limit. */
	h->stock_profile = true;
	h->wave_count = ARRAY_SIZE(a9_sine_aw8695);
	h->wave_cycles = 1;
	h->f0_error = -ENODATA;
	memcpy(h->wave, a9_sine_aw8695, sizeof(a9_sine_aw8695));
	mutex_init(&h->lock);
	INIT_WORK(&h->work, a9_play_work);
	i2c_set_clientdata(client, h);
	h->regmap = devm_regmap_init_i2c(client, &a9_regmap);
	if (IS_ERR(h->regmap))
		return PTR_ERR(h->regmap);
	h->reset = devm_gpiod_get(h->dev, "reset", GPIOD_OUT_HIGH);
	if (IS_ERR(h->reset))
		return dev_err_probe(h->dev, PTR_ERR(h->reset), "reset GPIO\n");
	ret = devm_add_action_or_reset(h->dev, a9_reset, h);
	if (ret)
		return ret;
	usleep_range(1000, 2000);
	gpiod_set_value_cansleep(h->reset, 0);
	usleep_range(3500, 4000);
	ret = regmap_read(h->regmap, 0x00, &id);
	if (ret)
		return dev_err_probe(h->dev, ret, "chip ID read\n");
	/* The bootloader's function names do not establish the fitted chip. */
	switch (id) {
	case 0x24: /* AW8624 register layout */
		h->gain_reg = 0x3b;
		h->state_reg = 0x47;
		break;
	case 0x95: /* AW8695: AW869x DATDBG=0x39, GLB_STATE=0x46 */
		h->aw8695 = true;
		fallthrough;
	case 0x97: /* AW8697 uses the same registers */
		h->gain_reg = 0x39;
		h->state_reg = 0x46;
		break;
	default:
		return dev_err_probe(h->dev, -ENODEV,
				     "unsupported chip ID 0x%02x\n", id);
	}
	ret = regmap_write(h->regmap, 0, 0xaa);
	if (ret)
		return ret;
	usleep_range(2000, 2500);
	if (h->aw8695) {
		/* Stock misc_para_init reads the reset gain rather than assuming FF_MAX. */
		ret = regmap_read(h->regmap, h->gain_reg, &gain);
		if (ret)
			return ret;
		h->gain = gain;
		ret = regmap_read(h->regmap, SYSCTRL, &reset_ctrl);
		if (ret)
			return ret;
		dev_info(h->dev, "stock reset GAIN=%02x SYSCTRL=%02x; using reset gain and 1x RAM data\n",
			 gain, reset_ctrl);
	}
	ret = regmap_write(h->regmap, SYSCTRL, STANDBY |
			   (h->aw8695 ? WAVE_1X : 0));
	if (ret)
		return ret;
	/* Mask IRQs; silicon's overcurrent/thermal shutdown remains in force. */
	ret = regmap_write(h->regmap, 0x03, 0xff);
	if (ret)
		return ret;
	ret = regmap_update_bits(h->regmap, 0x2e, GENMASK(6, 5), 2 << 5);
	if (ret)
		return ret;
	if (h->aw8695) {
		ret = a9_aw8695_drive_init(h);
		if (ret)
			return dev_err_probe(h->dev, ret, "AW8695 stock drive init\n");
		a9_tune_wave(h, 2350);
	}
	ret = a9_init_ram(h);
	if (ret)
		return dev_err_probe(h->dev, ret, "RAM init\n");
	if (h->aw8695)
		dev_info(h->dev, "chip ID 0x95; nominal RAM 235 Hz, 1x data, peak 78, base gain %u (stock reset); RAM_LOOP bypass with VBAT compensation; boost request 8500 mV inactive\n", h->gain);
	else
		dev_info(h->dev, "chip ID 0x%02x; RAM sine at 235 Hz, VBAT drive\n", id);
	if (h->aw8695 && auto_f0) {
		ret = a9_calibrate(h);
		if (ret && !h->ram_ready)
			return dev_err_probe(h->dev, ret, "F0 restore/RAM init\n");
		if (ret)
			dev_warn(h->dev, "F0 detection failed: %d; retaining nominal RAM period; retry via f0_measure\n", ret);
	} else if (h->aw8695) {
		dev_info(h->dev, "automatic F0 disabled; nominal RAM until f0_measure\n");
	}
	h->input = devm_input_allocate_device(h->dev);
	if (!h->input)
		return -ENOMEM;
	h->input->name = "a9-aw86xx-haptics";
	h->input->id.bustype = BUS_I2C;
	h->input->close = a9_close;
	input_set_drvdata(h->input, h);
	input_set_capability(h->input, EV_FF, FF_RUMBLE);
	ret = input_ff_create_memless(h->input, NULL, a9_play);
	if (ret)
		return ret;
	return input_register_device(h->input);
}

static void a9_quiesce(struct a9_haptics *h)
{
	int ret;

	WRITE_ONCE(h->suspended, true);
	cancel_work_sync(&h->work);
	mutex_lock(&h->lock);
	WRITE_ONCE(h->level, 0);
	ret = a9_stop(h);
	/* Known active-low reset: logical1 holds the chip inactive even if
	 * I2C GO/standby failed, or later bus/pinctrl PM changes its registers. */
	gpiod_set_value_cansleep(h->reset, 1);
	h->ram_ready = false;
	h->playing = false;
	mutex_unlock(&h->lock);
	if (ret) dev_warn(h->dev, "stop failed: %d; held in reset\n", ret);
}

static void a9_remove(struct i2c_client *client)
{
	a9_quiesce(i2c_get_clientdata(client));
}

static int a9_suspend(struct device *dev)
{
	a9_quiesce(dev_get_drvdata(dev));
	return 0;
}

static int a9_resume(struct device *dev)
{
	struct a9_haptics *h = dev_get_drvdata(dev);
	int ret;

	mutex_lock(&h->lock);
	/* Reset loses SRAM and volatile drive setup. Restore the same probe
	 * profile and saved waveform; never replay a pre-suspend FF request. */
	WRITE_ONCE(h->level, 0);
	gpiod_set_value_cansleep(h->reset, 0);
	usleep_range(3500, 4000);
	ret = regmap_write(h->regmap, 0, 0xaa);
	if (ret) goto fail;
	usleep_range(2000, 2500);
	ret = regmap_write(h->regmap, SYSCTRL, STANDBY | (h->aw8695 ? WAVE_1X : 0));
	if (ret) goto fail;
	ret = regmap_write(h->regmap, 0x03, 0xff);
	if (ret) goto fail;
	ret = regmap_update_bits(h->regmap, 0x2e, GENMASK(6, 5), 2 << 5);
	if (ret) goto fail;
	if (h->aw8695) {
		ret = a9_aw8695_drive_init(h);
		if (ret) goto fail;
	}
	ret = a9_init_ram(h);
	if (ret) goto fail;
	ret = a9_stop(h);
	if (ret) goto fail;
	WRITE_ONCE(h->suspended, false);
	mutex_unlock(&h->lock);
	return 0;
fail:
	gpiod_set_value_cansleep(h->reset, 1);
	h->ram_ready = false;
	h->playing = false;
	mutex_unlock(&h->lock);
	dev_err(h->dev, "resume failed: %d; held in reset\n", ret);
	return ret;
}
static DEFINE_SIMPLE_DEV_PM_OPS(a9_pm, a9_suspend, a9_resume);
static const struct of_device_id a9_match[] = {
	{ .compatible = "awinic,haptic_hv" }, { }
};
MODULE_DEVICE_TABLE(of, a9_match);
static struct i2c_driver a9_driver = {
	.driver = { .name = "a9-aw86xx", .of_match_table = a9_match,
		.pm = pm_sleep_ptr(&a9_pm), .dev_groups = a9_groups },
	.probe = a9_probe, .remove = a9_remove, .shutdown = a9_remove,
};
module_i2c_driver(a9_driver);
MODULE_DESCRIPTION("Hisense A9 AW8624/AW8695/AW8697 input force-feedback POC");
MODULE_AUTHOR("OpenAI");
MODULE_LICENSE("GPL");
