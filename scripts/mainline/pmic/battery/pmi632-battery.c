// SPDX-License-Identifier: GPL-2.0-only
/* A9 PMI632 POC: sampled coulomb counter and stock-profile primary charging.
 * Register evidence and limits are in README.md and drain-stock.md.
 * Optional drain uses volatile QG master hold; no SDAM/calibration writes.
 */
#include <linux/alarmtimer.h>
#include <linux/iio/consumer.h>
#include <linux/ktime.h>
#include <linux/math64.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/platform_device.h>
#include <linux/pm_wakeup.h>
#include <linux/power_supply.h>
#include <linux/property.h>
#include <linux/regmap.h>
#include <linux/unaligned.h>
#include <linux/workqueue.h>
#include <soc/qcom/qcom-spmi-pmic.h>
#include "pmi632-policy.h"
#include "policy.h"
#include "input-policy.h"
#include "status.h"
#include "drain-qg.h"

/* Optional known stock percentage, set once by coordinator before this boot. */
static int initial_soc = -1;
module_param(initial_soc, int, 0400);
MODULE_PARM_DESC(initial_soc, "Optional measured starting percentage 0..100; otherwise stock S7 OCV profile seed");
static bool event_debug;
module_param(event_debug, bool, 0644);
MODULE_PARM_DESC(event_debug, "Log actual USB presence/online transitions for suspend diagnosis");

/* Raw voltage/current/counter noise is available through get_property().
 * Uevents describe changes Android needs, not the 1 Hz sampling clock. */
struct battery_notice {
	int status, health, present, capacity, temp_dc, error;
	unsigned int fv_raw, fcc_raw;
};

struct usb_notice {
	int present, online, type, input_ua, error;
	unsigned int icl_raw, options, override, suspended;
};

struct battery {
	struct device *dev;
	struct regmap *map;
	struct iio_channel *therm, *vbus, *die;
	struct power_supply *bat, *usb;
	struct mutex lock;
	struct delayed_work work;
	struct alarm drain_alarm;
	struct work_struct drain_work;
	struct a9_qg_config drain_cfg;
	s64 drain_raw_ms, drain_last_ms, drain_started_ms;
	u64 drain_sample_ms;
	unsigned int drain_wakeups;
	int drain_start_soc, drain_error;
	bool drain_user_suspend, drain_held, drain_restore_pending;
	struct a9_sample s;
	struct battery_notice bat_notice;
	struct usb_notice usb_notice;
	bool bat_notified, usb_notified;
	s64 charge_uams, last_ms;
	int fcc_uah, last_ua, scale, error, health, status, usb_type;
	int usb_ua, dcp_ua, float_ua, input_ua, applied_ua, applied_mode;
	unsigned int icl_options, icl_override;
	bool seeded, counter_gap, charge_unknown, stopped, fault, full_anchor, usb_voted;
};

static int update(struct battery *b, unsigned int reg, unsigned int mask, unsigned int value)
{
	unsigned int val;
	int ret = regmap_update_bits(b->map, reg, mask, value);

	if (!ret)
		ret = regmap_read(b->map, reg, &val);
	if (!ret && (val & mask) != value)
		ret = -EIO;
	return ret;
}

/* Preserve USB input/data: stopping battery charging does not suspend USBIN. */
static int stop(struct battery *b)
{
	return update(b, 0x1042, BIT(0), 0);
}

static int sample(struct battery *b, struct a9_sample *s)
{
	const unsigned int regs[] = {0x1310, 0x1210, 0x4808, 0x1006, 0x1007,
		0x100d, 0x1607, 0x1609, 0x1140, 0x1307, 0x1308, 0x4843,
		0x1042, 0x1340, 0x110b, 0x1070, 0x1061, 0x1370};
	unsigned int *vals[] = {&s->usb, &s->batif, &s->qg, &s->status1,
		&s->status2, &s->status7, &s->die, &s->conn, &s->otg, &s->apsd,
		&s->source, &s->qgmode, &s->enabled, &s->suspend, &s->powerpath,
		&s->fv_raw, &s->fcc_raw, &s->icl_raw};
	int i, ret, therm_uv, die_mc;
	u8 raw[4], again[4];

	for (i = 0; i < ARRAY_SIZE(regs); i++) {
		ret = regmap_read(b->map, regs[i], vals[i]);
		if (ret)
			return ret;
	}
	for (i = 0; i < 3; i++) {
		ret = regmap_bulk_read(b->map, 0x48c0, raw, sizeof(raw));
		if (ret)
			return ret;
		ret = regmap_bulk_read(b->map, 0x48c0, again, sizeof(again));
		if (ret)
			return ret;
		if (!memcmp(raw, again, sizeof(raw)))
			break;
	}
	if (i == 3)
		return -EAGAIN;
	s->uv = div_u64((u64)get_unaligned_le16(raw) * 194637, 1000);
	s->ua = -div_s64((s64)(s16)get_unaligned_le16(raw + 2) * b->scale, 1000);
	ret = iio_read_channel_processed(b->therm, &therm_uv);
	if (ret < 0)
		return ret;
	if (a9_temp_dc(therm_uv, &s->temp_dc))
		return -ERANGE;
	ret = iio_read_channel_processed(b->vbus, &s->vbus_uv);
	if (ret < 0)
		return ret;
	ret = iio_read_channel_processed(b->die, &die_mc);
	if (ret < 0)
		return ret;
	/* Stock enables die mitigation at 80 C. */
	if (die_mc >= 80000)
		s->die |= BIT(7);
	if (s->uv < 2500000 || s->uv > 4500000 || abs(s->ua) > 5000000)
		return -ERANGE;
	s->temp_valid = 1;
	return 0;
}

static int seed_counter(struct battery *b)
{
	u8 raw[2];
	int ret, uv, soc;

	b->fcc_uah = profile_at_temp(profile_fcc, b->s.temp_dc);
	if (initial_soc >= 0) {
		soc = initial_soc * 100;
	} else {
		/* DTBO explicitly selects qg-use-s7-ocv. Read, do not reinitialize QG. */
		ret = regmap_bulk_read(b->map, 0x4870, raw, sizeof(raw));
		if (ret)
			return ret;
		uv = div_u64((u64)get_unaligned_le16(raw) * 194637, 1000);
		if (uv < 2500000 || uv > 4500000)
			return -ENODATA;
		soc = profile_seed(uv, b->s.temp_dc);
	}
	b->charge_uams = div_s64((s64)b->fcc_uah * 3600000 * soc, 10000);
	b->seeded = true;
	dev_info(b->dev, "sampled coulomb counter seeded (%s); profile FCC=%duAh\n",
		 initial_soc >= 0 ? "initial_soc" : "S7 OCV estimate", b->fcc_uah);
	return 0;
}

static void count_charge(struct battery *b, s64 now)
{
	s64 elapsed = now - b->last_ms;
	s64 full = (s64)b->fcc_uah * 3600000;

	/* Do not invent integrated charge across missing samples or suspend. */
	if (b->last_ms && elapsed > 5000) {
		b->counter_gap = true;
		b->charge_unknown = true;
	}
	if (b->last_ms && elapsed > 0 && elapsed <= 5000)
		b->charge_uams += sampled_charge_delta(b->last_ua, b->s.ua, elapsed);
	b->charge_uams = clamp_t(s64, b->charge_uams, 0, full);
	/* Anchor on entry to termination, once per charge cycle. */
	if ((b->s.status1 & 7) != 5 || !(b->s.usb & BIT(4)))
		b->full_anchor = false;
	if (!b->full_anchor && (b->s.status1 & 7) == 5 && (b->s.usb & BIT(4)) &&
	    b->s.uv >= 4400000 && b->s.temp_dc >= 0 && b->s.temp_dc <= 450) {
		b->charge_uams = full;
		b->counter_gap = false;
		b->charge_unknown = false;
		b->full_anchor = true;
	}
	b->last_ms = now;
	b->last_ua = b->s.ua;
}

static int classify(const struct a9_sample *s)
{
	if (!(s->usb & BIT(4)) || !(s->apsd & BIT(0)))
		return POWER_SUPPLY_USB_TYPE_UNKNOWN;
	switch (s->source & 0x7f) {
	case BIT(0): return POWER_SUPPLY_USB_TYPE_SDP;
	case BIT(2): return POWER_SUPPLY_USB_TYPE_CDP;
	case BIT(3): return POWER_SUPPLY_USB_TYPE_DCP;
	default: return POWER_SUPPLY_USB_TYPE_UNKNOWN;
	}
}

/* Stock three-state override, not the older public SMB5 boolean helper.
 * 0 = APSD auto, 1 = USB51, 2 = software high-current selector.
 */
static int input_mode(struct battery *b, int mode)
{
	int ret = update(b, 0x1366, BIT(0), mode == 1 ? 0 : BIT(0));

	if (!ret)
		ret = update(b, 0x1342, BIT(0), mode == 1 ? BIT(0) : 0);
	if (!ret)
		ret = update(b, 0x1365, BIT(4), mode == 2 ? BIT(4) : 0);
	return ret;
}

static int input_policy(struct battery *b)
{
	struct a9_sample *s = &b->s;
	int ua, mode, ret;
	bool sdp = (s->apsd & 1) && (s->source & 0x7f) == 1;
	bool floating = (s->apsd & 1) && (s->source & 0x7f) == 0x10;

	if (b->drain_user_suspend) {
		b->input_ua = 0;
		b->applied_ua = -1;
		return update(b, 0x1340, BIT(0), BIT(0));
	}
	if (!(s->usb & BIT(4))) {
		/* Stock starts SDP at 500 mA; USB vbus_draw supplies later votes. */
		b->usb_ua = 500000;
		b->usb_voted = false;
		b->input_ua = 0;
		b->applied_ua = -1;
		return 0;
	}
	if ((s->otg & 1) || (s->usb & 0x0f)) {
		b->input_ua = 0;
		b->applied_ua = -1;
		return 0;
	}
	/* A FLOAT source that the USB stack can enumerate is really an SDP. */
	if (floating && b->usb_voted) {
		sdp = true;
		b->usb_type = POWER_SUPPLY_USB_TYPE_SDP;
	}
	ua = stock_input_current(sdp ? 1 : s->source, s->apsd & 1,
				 b->usb_ua, b->dcp_ua, b->float_ua);
	mode = ua > 900000 || (floating && !sdp) ? 2 :
		sdp ? 0 : 1;
	if (ua == b->applied_ua && mode == b->applied_mode)
		return 0;
	/* Program the ceiling before selecting HC. Keep AICL and BC1.2 enabled. */
	ret = update(b, 0x1370, 0xff, ua / 50000);
	if (!ret)
		ret = update(b, 0x1363, 0x07, floating && sdp ? BIT(0) : 0);
	if (!ret && mode != 2)
		ret = update(b, 0x1366, 0x06, stock_usb51_options(ua));
	/* Stock set_sdp_current selects USB51 before set_icl_current restores auto. */
	if (!ret && mode == 0)
		ret = input_mode(b, 1);
	if (!ret)
		ret = input_mode(b, mode);
	/* Like stock, <=25 mA means USB suspend. Configure before resuming. */
	if (!ret)
		ret = update(b, 0x1340, BIT(0), ua ? 0 : BIT(0));
	if (!ret && ua && mode != 2) {
		ret = regmap_read(b->map, 0x110b, &s->powerpath);
		if (!ret && !(s->powerpath & BIT(6)))
			/* Stock smblib_run_aicl(0): self-clearing rerun, not a new limit. */
			ret = regmap_update_bits(b->map, 0x1644, BIT(0), BIT(0));
	}
	if (!ret) {
		b->input_ua = ua;
		b->applied_ua = ua;
		b->applied_mode = mode;
		dev_info(b->dev, "USB source=0x%02x input=%duA mode=%d\n",
			 s->source & 0x7f, ua, mode);
	}
	return ret;
}

static int charge_policy(struct battery *b)
{
	struct a9_sample *s = &b->s;
	int ret, fcc, fv;

	stock_jeita(s->temp_dc, &fcc, &fv);
	b->health = POWER_SUPPLY_HEALTH_GOOD;
	if (b->fault)
		b->health = POWER_SUPPLY_HEALTH_UNSPEC_FAILURE;
	else if (s->temp_dc < 0 || (s->status7 & (BIT(2) | BIT(0))))
		b->health = POWER_SUPPLY_HEALTH_COLD;
	else if (s->temp_dc > 530 || (s->status7 & (BIT(3) | BIT(1))) || s->die || s->conn)
		b->health = POWER_SUPPLY_HEALTH_OVERHEAT;
	else if (a9_battery_overvoltage(s))
		b->health = POWER_SUPPLY_HEALTH_OVERVOLTAGE;
	if (b->drain_user_suspend || b->fault || b->health != POWER_SUPPLY_HEALTH_GOOD || !fcc ||
	    (s->batif & 0x30) || !(s->qg & BIT(0)) || (s->otg & 1) ||
	    (s->usb & 0x0f))
		return stop(b);
	/* Lower current before changing FV across bands.
	 * Secondary remains disabled: primary FCC is the total permitted FCC.
	 */
	ret = update(b, 0x1061, 0xff, min((int)s->fcc_raw, fcc / 50000));
	if (!ret)
		ret = update(b, 0x1070, 0xff, (fv - 3600000) / 10000);
	if (!ret)
		ret = update(b, 0x1061, 0xff, fcc / 50000);
	if (!ret && (s->usb & BIT(4)))
		ret = regmap_write(b->map, 0x1643, BIT(0)); /* self-clearing pet */
	/* Stock uses 98% recharge. This fallback owns that decision in software,
	 * since the inherited QG monotonic byte is not a live hardware SOC.
	 */
	if (!ret && fv == 4450000 && (s->status1 & 7) == 5 && b->seeded && !b->counter_gap &&
	    b->charge_uams <= div_s64((s64)b->fcc_uah * 3600000 * 98, 100))
		ret = update(b, 0x1042, BIT(0), 0);
	if (!ret)
		ret = update(b, 0x1042, BIT(0), BIT(0));
	return ret;
}

#include "pmi632-drain.h"

/* Called under lock. Compare against the last published snapshot so that a
 * small temperature drift eventually notifies without one event per ADC step.
 * Do not compare raw voltage/current: those change on essentially every poll. */
static unsigned int changed_supplies(struct battery *b)
{
	struct battery_notice bat = {
		.status = b->status, .health = b->health,
		.present = !(b->s.batif & 0x30) && (b->s.qg & 1),
		.capacity = b->seeded ? div64_s64(b->charge_uams * 100 +
			(s64)b->fcc_uah * 1800000, (s64)b->fcc_uah * 3600000) : -ENODATA,
		.temp_dc = b->s.temp_dc, .error = b->error,
		.fv_raw = b->s.fv_raw, .fcc_raw = b->s.fcc_raw,
	};
	struct usb_notice usb = {
		.present = !!(b->s.usb & BIT(4)),
		.online = a9_observed_online(&b->s), .type = b->usb_type,
		.input_ua = b->input_ua, .error = b->error,
		.icl_raw = b->s.icl_raw, .options = b->icl_options,
		.override = b->icl_override, .suspended = b->s.suspend & 1,
	};
	struct battery_notice *old = &b->bat_notice;
	struct usb_notice *ou = &b->usb_notice;
	unsigned int changed = 0;
	if (event_debug && b->usb_notified &&
	    (usb.present != ou->present || usb.online != ou->online))
		dev_info(b->dev, "A9_USB_POWER present=%d->%d online=%d->%d suspend=0x%x powerpath=0x%x input=%d\n",
			 ou->present, usb.present, ou->online, usb.online,
			 b->s.suspend, b->s.powerpath, b->input_ua);

	if (event_debug && b->bat_notified && bat.status != old->status)
		dev_info(b->dev, "A9_BAT_STATUS status=%d->%d state=%u status7=0x%x enabled=%u online=%d current_ua=%d fault=%d\n",
			 old->status, bat.status, b->s.status1 & 7, b->s.status7,
			 b->s.enabled & 1, a9_observed_online(&b->s), b->s.ua, b->fault);

	if (!b->bat_notified || bat.status != old->status ||
	    bat.health != old->health || bat.present != old->present ||
	    bat.capacity != old->capacity || abs(bat.temp_dc - old->temp_dc) >= 10 ||
	    bat.error != old->error || bat.fv_raw != old->fv_raw || bat.fcc_raw != old->fcc_raw) {
		b->bat_notice = bat;
		b->bat_notified = true;
		changed |= BIT(0);
	}
	if (!b->usb_notified || usb.present != ou->present || usb.online != ou->online ||
	    usb.type != ou->type || usb.input_ua != ou->input_ua || usb.error != ou->error ||
	    usb.icl_raw != ou->icl_raw || usb.options != ou->options ||
	    usb.override != ou->override || usb.suspended != ou->suspended) {
		b->usb_notice = usb;
		b->usb_notified = true;
		changed |= BIT(1);
	}
	return changed;
}

static void poll(struct work_struct *work)
{
	struct battery *b = container_of(to_delayed_work(work), struct battery, work);
	struct a9_sample s = {};
	s64 now = ktime_to_ms(ktime_get_boottime());
	int ret;
	unsigned int changed;

	mutex_lock(&b->lock);
	if (READ_ONCE(b->stopped)) {
		mutex_unlock(&b->lock);
		return;
	}
	ret = sample(b, &s);
	if (!ret) {
		b->s = s;
		b->usb_type = classify(&s);
		if (!b->seeded) {
			int seed_ret = seed_counter(b);

			if (seed_ret)
				dev_warn_ratelimited(b->dev, "SOC seed unavailable: %d\n", seed_ret);
		}
		if (b->seeded)
			count_charge(b, now);
		if (!b->fault)
			ret = input_policy(b);
		if (!ret)
			ret = charge_policy(b);
		/* Report the post-policy selectors and enable state, not old samples. */
		if (!ret)
			ret = regmap_read(b->map, 0x1370, &b->s.icl_raw);
		if (!ret)
			ret = regmap_read(b->map, 0x1366, &b->icl_options);
		if (!ret)
			ret = regmap_read(b->map, 0x1365, &b->icl_override);
		if (!ret)
			ret = regmap_read(b->map, 0x1042, &b->s.enabled);
		if (!ret)
			ret = regmap_read(b->map, 0x1340, &b->s.suspend);
		if (!ret)
			ret = regmap_read(b->map, 0x110b, &b->s.powerpath);
		if (ret)
			b->fault = true;
	}
	if (ret) {
		b->fault = true;
		b->counter_gap = true;
		b->charge_unknown = true;
		b->health = POWER_SUPPLY_HEALTH_UNSPEC_FAILURE;
		stop(b);
		if (b->drain_user_suspend) {
			b->drain_error = ret;
			drain_restore_locked(b);
		}
		dev_err_ratelimited(b->dev, "battery sample/policy: %d\n", ret);
	}
	b->error = ret;
	b->status = a9_reported_status(&b->s, b->fault);
	changed = changed_supplies(b);
	mutex_unlock(&b->lock);
	if (changed & BIT(0))
		power_supply_changed(b->bat);
	if (changed & BIT(1))
		power_supply_changed(b->usb);
	if (!READ_ONCE(b->stopped))
		queue_delayed_work(system_freezable_wq, &b->work, HZ);
}

static enum power_supply_property bat_props[] = {
	POWER_SUPPLY_PROP_STATUS, POWER_SUPPLY_PROP_HEALTH, POWER_SUPPLY_PROP_PRESENT,
	POWER_SUPPLY_PROP_TECHNOLOGY, POWER_SUPPLY_PROP_VOLTAGE_NOW,
	POWER_SUPPLY_PROP_CURRENT_NOW, POWER_SUPPLY_PROP_TEMP, POWER_SUPPLY_PROP_CAPACITY,
	POWER_SUPPLY_PROP_CHARGE_COUNTER, POWER_SUPPLY_PROP_CHARGE_FULL_DESIGN,
	POWER_SUPPLY_PROP_CHARGE_FULL, POWER_SUPPLY_PROP_MODEL_NAME,
	POWER_SUPPLY_PROP_CONSTANT_CHARGE_VOLTAGE_MAX,
	POWER_SUPPLY_PROP_CONSTANT_CHARGE_CURRENT_MAX,
};

static int bat_get(struct power_supply *psy, enum power_supply_property prop,
		   union power_supply_propval *v)
{
	struct battery *b = power_supply_get_drvdata(psy);
	int ret = 0;

	mutex_lock(&b->lock);
	switch (prop) {
	case POWER_SUPPLY_PROP_MODEL_NAME: v->strval = "A9-PMI632-sampled-CC-POC"; break;
	case POWER_SUPPLY_PROP_STATUS: v->intval = b->status; break;
	case POWER_SUPPLY_PROP_HEALTH: v->intval = b->health; break;
	case POWER_SUPPLY_PROP_PRESENT: v->intval = !(b->s.batif & 0x30) && (b->s.qg & 1); break;
	case POWER_SUPPLY_PROP_TECHNOLOGY: v->intval = POWER_SUPPLY_TECHNOLOGY_LION; break;
	case POWER_SUPPLY_PROP_VOLTAGE_NOW: v->intval = b->s.uv; break;
	case POWER_SUPPLY_PROP_CURRENT_NOW: v->intval = b->s.ua; break;
	case POWER_SUPPLY_PROP_TEMP: v->intval = b->s.temp_dc; break;
	/* Stock qg_psy_get_property (0xffffff8008a45928) returns cached SOC.
	 * A missing sampled interval does not mean the battery became empty. */
	case POWER_SUPPLY_PROP_CAPACITY:
		if (!b->seeded)
			ret = -ENODATA;
		else
			v->intval = div64_s64(b->charge_uams * 100 +
				(s64)b->fcc_uah * 1800000, (s64)b->fcc_uah * 3600000);
		break;
	case POWER_SUPPLY_PROP_CHARGE_COUNTER:
		if (!b->seeded || b->counter_gap || b->charge_unknown)
			ret = -ENODATA;
		else
			v->intval = div_s64(b->charge_uams, 3600000);
		break;
	case POWER_SUPPLY_PROP_CHARGE_FULL:
	case POWER_SUPPLY_PROP_CHARGE_FULL_DESIGN: v->intval = b->fcc_uah; break;
	case POWER_SUPPLY_PROP_CONSTANT_CHARGE_VOLTAGE_MAX: v->intval = 3600000 + b->s.fv_raw * 10000; break;
	case POWER_SUPPLY_PROP_CONSTANT_CHARGE_CURRENT_MAX: v->intval = b->s.fcc_raw * 50000; break;
	default: ret = -EINVAL;
	}
	if (b->error && prop != POWER_SUPPLY_PROP_MODEL_NAME &&
	    prop != POWER_SUPPLY_PROP_HEALTH && prop != POWER_SUPPLY_PROP_CAPACITY)
		ret = -ENODATA;
	mutex_unlock(&b->lock);
	return ret;
}

static enum power_supply_property usb_props[] = {POWER_SUPPLY_PROP_PRESENT,
	POWER_SUPPLY_PROP_ONLINE, POWER_SUPPLY_PROP_USB_TYPE, POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT,
	POWER_SUPPLY_PROP_CURRENT_MAX, POWER_SUPPLY_PROP_VOLTAGE_NOW,
	POWER_SUPPLY_PROP_VOLTAGE_MAX};

static int usb_get(struct power_supply *psy, enum power_supply_property prop,
		   union power_supply_propval *v)
{
	struct battery *b = power_supply_get_drvdata(psy);
	int ret = 0;

	mutex_lock(&b->lock);
	switch (prop) {
	case POWER_SUPPLY_PROP_PRESENT: v->intval = !!(b->s.usb & BIT(4)); break;
	case POWER_SUPPLY_PROP_ONLINE: v->intval = a9_observed_online(&b->s); break;
	case POWER_SUPPLY_PROP_USB_TYPE: v->intval = b->usb_type; break;
	case POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT:
		if (!(b->s.usb & BIT(4)) || (b->s.suspend & 1))
			v->intval = 0;
		else if (b->icl_override & BIT(4))
			v->intval = b->s.icl_raw * 50000;
		else
			v->intval = b->icl_options & BIT(1) ?
				(b->icl_options & BIT(2) ? 900000 : 500000) :
				(b->icl_options & BIT(2) ? 150000 : 100000);
		break;
	case POWER_SUPPLY_PROP_CURRENT_MAX: v->intval = b->input_ua; break;
	case POWER_SUPPLY_PROP_VOLTAGE_NOW: v->intval = b->s.vbus_uv; break;
	case POWER_SUPPLY_PROP_VOLTAGE_MAX: v->intval = 5000000; break;
	default: ret = -EINVAL;
	}
	if (b->error)
		ret = -ENODATA;
	mutex_unlock(&b->lock);
	return ret;
}

/* DWC3's existing vbus_draw work calls this after reset/config/suspend. */
static int usb_set(struct power_supply *psy, enum power_supply_property prop,
		   const union power_supply_propval *v)
{
	struct battery *b = power_supply_get_drvdata(psy);
	bool changed;
	int ua;

	if (prop != POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT || v->intval < 0)
		return -EINVAL;
	mutex_lock(&b->lock);
	ua = stock_usb_current(v->intval);
	changed = b->usb_ua != ua || (v->intval > 25000 && !b->usb_voted);
	b->usb_ua = ua;
	if (v->intval > 25000)
		b->usb_voted = true;
	mutex_unlock(&b->lock);
	if (changed && !READ_ONCE(b->stopped))
		mod_delayed_work(system_freezable_wq, &b->work, 0);
	return 0;
}

static int usb_writeable(struct power_supply *psy, enum power_supply_property prop)
{
	return prop == POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT;
}

static const struct power_supply_desc bat_desc = {
	.name = "battery", .type = POWER_SUPPLY_TYPE_BATTERY,
	.properties = bat_props, .num_properties = ARRAY_SIZE(bat_props), .get_property = bat_get,
};
static const struct power_supply_desc usb_desc = {
	.name = "a9-usb", .type = POWER_SUPPLY_TYPE_USB,
	.properties = usb_props, .num_properties = ARRAY_SIZE(usb_props), .get_property = usb_get,
	.set_property = usb_set, .property_is_writeable = usb_writeable,
	.usb_types = BIT(POWER_SUPPLY_USB_TYPE_UNKNOWN) | BIT(POWER_SUPPLY_USB_TYPE_SDP) |
		BIT(POWER_SUPPLY_USB_TYPE_CDP) | BIT(POWER_SUPPLY_USB_TYPE_DCP),
};

static void cleanup(void *data)
{
	struct battery *b = data;

	WRITE_ONCE(b->stopped, true);
	cancel_delayed_work_sync(&b->work);
	mutex_lock(&b->lock);
	drain_restore_locked(b);
	stop(b);
	alarm_cancel(&b->drain_alarm);
	mutex_unlock(&b->lock);
	cancel_work_sync(&b->drain_work);
	pm_relax(b->dev);
}

static int probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	const struct qcom_spmi_pmic *pmic = qcom_pmic_get(dev);
	/* These are polling telemetry supplies, with no IRQ in this DT node.
	 * Do not synthesize wakeup events for sampling/uevent bookkeeping.
	 * Existing physical USB/PMIC IRQ wake policy belongs to its IRQ drivers. */
	struct power_supply_config cfg = { .no_wakeup_source = true };
	struct battery *b;
	unsigned int type, subtype;
	/* Stock hard thresholds HOT then COLD (BE16); secondary override OFF;
	 * watchdog charge-disable on bite, no AFP; stock 768-minute safety timer.
	 * Hardware termination configuration is preserved, not guessed from QG iterm.
	 */
	static const u8 hard[] = {0x0c, 0xa0, 0x36, 0xb9};
	static const u8 soft[] = {0x0f, 0xdc, 0x2a, 0xac};
	u8 check[4];
	int ret;

	if (IS_ERR(pmic))
		return PTR_ERR(pmic);
	if (pmic->subtype != PMI632_SUBTYPE || initial_soc < -1 || initial_soc > 100)
		return -EINVAL;
	b = devm_kzalloc(dev, sizeof(*b), GFP_KERNEL);
	if (!b)
		return -ENOMEM;
	b->dev = dev;
	b->usb_ua = 500000;
	b->applied_ua = -1;
	b->dcp_ua = 2000000;
	b->float_ua = 900000;
	device_property_read_u32(dev, "qcom,usb-icl-ua", &b->dcp_ua);
	device_property_read_u32(dev, "qcom,float-icl-ua", &b->float_ua);
	if (b->dcp_ua < 100000 || b->dcp_ua > 2200000 || b->dcp_ua % 50000 ||
	    b->float_ua < 100000 || b->float_ua > 2200000 || b->float_ua % 50000)
		return -EINVAL;
	b->map = dev_get_regmap(dev->parent, NULL);
	if (!b->map)
		return -ENODEV;
	ret = regmap_read(b->map, 0x4804, &type);
	if (ret)
		return ret;
	ret = regmap_read(b->map, 0x4805, &subtype);
	if (ret)
		return ret;
	if (type != 0x0d || (subtype != 3 && subtype != 4))
		return -ENODEV;
	b->scale = subtype == 3 ? 152588 : 305176;
	b->therm = devm_iio_channel_get(dev, "batt-therm-voltage");
	if (IS_ERR(b->therm))
		return dev_err_probe(dev, PTR_ERR(b->therm), "battery NTC\n");
	b->vbus = devm_iio_channel_get(dev, "usb-voltage");
	if (IS_ERR(b->vbus))
		return dev_err_probe(dev, PTR_ERR(b->vbus), "USB voltage\n");
	b->die = devm_iio_channel_get(dev, "die-temp");
	if (IS_ERR(b->die))
		return dev_err_probe(dev, PTR_ERR(b->die), "die temperature\n");
	mutex_init(&b->lock);
	INIT_DELAYED_WORK(&b->work, poll);
	INIT_WORK(&b->drain_work, drain_work);
	alarm_init(&b->drain_alarm, ALARM_BOOTTIME, drain_alarm);
	ret = devm_device_init_wakeup(dev);
	if (ret)
		return ret;
	platform_set_drvdata(pdev, b);
	ret = stop(b);
	if (ret)
		return ret;
	ret = regmap_bulk_write(b->map, 0x1098, hard, sizeof(hard));
	if (!ret)
		ret = regmap_bulk_read(b->map, 0x1098, check, sizeof(check));
	if (!ret && memcmp(hard, check, sizeof(hard)))
		ret = -EIO;
	if (!ret)
		ret = regmap_bulk_write(b->map, 0x1094, soft, sizeof(soft));
	if (!ret)
		ret = regmap_bulk_read(b->map, 0x1094, check, sizeof(check));
	if (!ret && memcmp(soft, check, sizeof(soft)))
		ret = -EIO;
	if (!ret)
		ret = update(b, 0x1648, 0x1f, BIT(3));
	if (!ret)
		ret = update(b, 0x1653, 0x8f, BIT(7) | 0x03);
	if (!ret)
		ret = update(b, 0x1651, BIT(7) | BIT(6) | BIT(1), BIT(1));
	if (!ret)
		ret = update(b, 0x10a2, 0x03, 0x02);
	/* No autonomous SOC comparison against a stale firmware QG_SOC byte. */
	if (!ret)
		ret = update(b, 0x1051, 0x06, 0);
	if (ret)
		return ret;
	cfg.drv_data = b;
	cfg.fwnode = dev_fwnode(dev);
	b->error = -ENODATA;
	b->health = POWER_SUPPLY_HEALTH_UNKNOWN;
	cfg.attr_grp = drain_bat_groups;
	b->bat = devm_power_supply_register(dev, &bat_desc, &cfg);
	if (IS_ERR(b->bat))
		return PTR_ERR(b->bat);
	cfg.attr_grp = drain_usb_groups;
	b->usb = devm_power_supply_register(dev, &usb_desc, &cfg);
	if (IS_ERR(b->usb))
		return PTR_ERR(b->usb);
	ret = devm_add_action_or_reset(dev, cleanup, b);
	if (ret)
		return ret;
	queue_delayed_work(system_freezable_wq, &b->work, 0);
	return 0;
}

static int suspend(struct device *dev)
{
	struct battery *b = dev_get_drvdata(dev);
	int ret;

	WRITE_ONCE(b->stopped, true);
	cancel_delayed_work_sync(&b->work);
	mutex_lock(&b->lock);
	ret = stop(b);
	b->counter_gap = true;
	b->charge_unknown = true;
	/* Keep the last measured SOC and telemetry; do not create a transport
	 * error solely for sleep. Existing sample/control errors stay latched. */
	b->last_ms = 0;
	mutex_unlock(&b->lock);
	if (ret) {
		WRITE_ONCE(b->stopped, false);
		queue_delayed_work(system_freezable_wq, &b->work, 0);
	}
	return ret;
}

static int resume(struct device *dev)
{
	struct battery *b = dev_get_drvdata(dev);

	mutex_lock(&b->lock);
	b->counter_gap = false;
	b->last_ms = 0; /* First new sample rebaselines; never integrate sleep. */
	/* charge_unknown remains until a measured full anchor: no FIFO/OCV
	 * reconciliation is implemented by this short-suspend cached-SOC POC. */
	mutex_unlock(&b->lock);
	WRITE_ONCE(b->stopped, false);
	queue_delayed_work(system_freezable_wq, &b->work, 0);
	return 0;
}

static DEFINE_SIMPLE_DEV_PM_OPS(pm_ops, suspend, resume);
static const struct of_device_id matches[] = {
	{ .compatible = "hisense,hlte556n-pmi632-battery" }, {}
};
MODULE_DEVICE_TABLE(of, matches);
static void shutdown(struct platform_device *pdev) { cleanup(platform_get_drvdata(pdev)); }
static struct platform_driver a9_battery_driver = {
	.probe = probe, .shutdown = shutdown,
	.driver = { .name = "pmi632-battery", .of_match_table = matches, .pm = pm_sleep_ptr(&pm_ops) },
};
module_platform_driver(a9_battery_driver);
MODULE_LICENSE("GPL");
MODULE_VERSION("r22e");
MODULE_DESCRIPTION("A9 PMI632 sampled coulomb counter and stock-profile charging POC");
