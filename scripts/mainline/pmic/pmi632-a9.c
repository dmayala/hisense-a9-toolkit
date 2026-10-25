// SPDX-License-Identifier: GPL-2.0-only
/*
 * Hisense A9 PMI632 bring-up proposal, 2026-10-15. NOT hardware validated.
 * Register facts: phone's own 4.19.157 qpnp-smb5/qpnp-qg and stock DTBO.
 * See docs/mainline-pmic.md before binding. Never bind qcom_smbx here too.
 */
#include <linux/bitops.h>
#include <linux/iio/consumer.h>
#include <linux/jiffies.h>
#include <linux/math64.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/pm.h>
#include <linux/power_supply.h>
#include <linux/property.h>
#include <linux/regmap.h>
#include <linux/unaligned.h>
#include <linux/workqueue.h>
#include <soc/qcom/qcom-spmi-pmic.h>

#include "pmi632-policy.h"
#include "pmi632-fv.h"

#define CHG_STATUS1	0x1006
#define CHG_STATUS2	0x1007
#define CHG_STATUS7	0x100d
#define CHG_ENABLE	0x1042
#define CHG_FCC		0x1061
#define CHG_FV		A9_STOCK_FV_REG
#define JEITA_HARD	0x1098
#define BATIF_RT	0x1210
#define USB_RT		0x1310
#define USB_SUSPEND	0x1340
#define USB_ICL		0x1370
#define OTG_CMD		0x1140
#define DIE_STATUS	0x1607
#define CONN_STATUS	0x1609
#define WD_PET		0x1643
#define SMB_ENABLE	0x1648
#define WD_CFG		0x1651
#define WD_TIMEOUT	0x1653
#define QG_TYPE		0x4804
#define QG_SUBTYPE	0x4805
#define QG_STATUS	0x4808
#define QG_LAST_ADC	0x48c0
#define USB_PRESENT	BIT(4)
#define POLL_MS		1000

/* Read-only after load: enabling requires a reload and the explicit DT gate. */
static bool allow_charging;
module_param(allow_charging, bool, 0400);
MODULE_PARM_DESC(allow_charging, "EXPERIMENTAL charging after coordinator validation; default false");

/* Observe excludes all control writes except the opt-in probe-only stock FV.
 * CONFIG forces observe; fv_stock=0 preserves pure observation.
 */
static bool observe = IS_ENABLED(CONFIG_CHARGER_PMI632_A9_OBSERVE);
module_param(observe, bool, 0400);
MODULE_PARM_DESC(observe, "Observe firmware charger; CONFIG_OBSERVE forces true; see fv_stock");

static bool fv_stock = IS_ENABLED(CONFIG_CHARGER_PMI632_A9_FV_STOCK);
module_param(fv_stock, bool, 0400);
MODULE_PARM_DESC(fv_stock, "Observe only: one probe write of stock 4.450V FV, with readback; no retries");

/* Optional exception: only the existing upstream ADC5 conversion path writes.
 * fv_stock=0 observe_adc=0 gives zero consumer writes, with TEMP unavailable.
 */
static bool observe_adc = true;
module_param(observe_adc, bool, 0400);
MODULE_PARM_DESC(observe_adc, "Use normal upstream ADC5 conversions in observe (default true)");

static bool a9_is_observe(void)
{
	return IS_ENABLED(CONFIG_CHARGER_PMI632_A9_OBSERVE) || observe;
}

struct a9_pmic {
	struct device *dev;
	struct regmap *map;
	struct iio_channel *therm, *vbus, *die;
	struct power_supply *battery, *usb;
	struct mutex lock;
	struct delayed_work work;
	struct a9_sample sample;
	unsigned long last_change, sampled_at;
	u32 last_raw;
	int current_scale, error, fv_error;
	bool permitted, charging, latched, suspended, have_raw;
};

struct a9_setting {
	unsigned int reg, mask, val;
};

/*
 * No QC/PD negotiation, APSD override or Type-C writes. USB stays in its
 * USB2 500 mA mode, additionally capped by the 500 mA ICL selector.
 * Charging requires BC1.2 CDP/DCP detection; SDP/unknown/QC stay OFF.
 * 1651 bit0 (immediate watchdog start) is inferred from SMB family; the
 * validation gate MUST include proof of watchdog bite with USB already in.
 */
static const struct a9_setting a9_settings[] = {
	{ CHG_FV, 0xff, (A9_FV_UV - 3600000) / 10000 },
	{ CHG_FCC, 0xff, A9_FCC_UA / 50000 },
	{ USB_ICL, 0xff, A9_ICL_UA / 50000 },
	{ 0x1362, BIT(6) | BIT(5) | BIT(2), 0 }, /* no HVDCP */
	{ 0x1360, 0x0f, 0 }, /* 5 V only */
	{ 0x1365, BIT(4), 0 }, /* no ICL override after APSD */
	{ 0x1366, 0x07, BIT(1) }, /* USB2 500 mA mode */
	{ SMB_ENABLE, 0x1f, BIT(3) }, /* secondary charger override OFF */
	{ 0x10a2, 0x03, 0 }, /* shortest fast charge timer: 192 min */
	/* Bite stops charge: 16 s bark + 8 s bite. No watchdog IRQ handler. */
	{ WD_TIMEOUT, 0xff, BIT(7) | 0x03 },
	{ WD_CFG, BIT(7) | BIT(6) | BIT(1) | BIT(0), BIT(1) | BIT(0) },
};

static int a9_fv_read(void *context, unsigned int reg, unsigned int *value)
{
	return regmap_read(context, reg, value);
}

static int a9_fv_write(void *context, unsigned int reg, unsigned int value)
{
	if (!a9_is_observe() || reg != A9_STOCK_FV_REG || value != A9_STOCK_FV_RAW)
		return -EPERM;
	return regmap_write(context, reg, value);
}

static int a9_update_check(struct a9_pmic *p, unsigned int reg,
			   unsigned int mask, unsigned int val)
{
	unsigned int readback;
	int ret;

	if (a9_is_observe())
		return -EPERM;
	ret = regmap_update_bits(p->map, reg, mask, val);
	if (ret)
		return ret;
	ret = regmap_read(p->map, reg, &readback);
	if (ret)
		return ret;
	return (readback & mask) == val ? 0 : -EIO;
}

/* Control mode attempts BOTH stops; observe returns before any bus access. */
static int a9_off(struct a9_pmic *p)
{
	int ret, other;

	if (a9_is_observe())
		return 0;
	p->charging = false;
	ret = a9_update_check(p, CHG_ENABLE, BIT(0), 0);
	other = a9_update_check(p, USB_SUSPEND, BIT(0), BIT(0));
	if (ret || other)
		dev_crit_ratelimited(p->dev, "charging stop failed (%d/%d); disconnect USB\n",
				     ret, other);
	return ret ? ret : other;
}

static int a9_settings_check(struct a9_pmic *p, bool program)
{
	/* Stock threshold order is HOT BE16, COLD BE16, not DT order. */
	static const u8 hard[] = { 0x0c, 0xa0, 0x36, 0xb9 };
	u8 readback[sizeof(hard)];
	unsigned int i, val;
	int ret;

	if (program) {
		if (a9_is_observe())
			return -EPERM;
		ret = regmap_bulk_write(p->map, JEITA_HARD, hard, sizeof(hard));
		if (ret)
			return ret;
	}
	ret = regmap_bulk_read(p->map, JEITA_HARD, readback, sizeof(readback));
	if (ret)
		return ret;
	if (memcmp(hard, readback, sizeof(hard)))
		return -EIO;
	for (i = 0; i < ARRAY_SIZE(a9_settings); i++) {
		const struct a9_setting *s = &a9_settings[i];

		if (program) {
			ret = a9_update_check(p, s->reg, s->mask, s->val);
		} else {
			ret = regmap_read(p->map, s->reg, &val);
			if (!ret && (val & s->mask) != s->val)
				ret = -EIO;
		}
		if (ret)
			return ret;
	}
	return 0;
}

static int a9_read_sample(struct a9_pmic *p, struct a9_sample *s)
{
	u8 raw[4], again[4];
	int ret, tries, therm_uv, die_mc;

	const struct {
		unsigned int reg;
		unsigned int *value;
	} reads[] = {
		{ USB_RT, &s->usb }, { BATIF_RT, &s->batif },
		{ QG_STATUS, &s->qg }, { CHG_STATUS1, &s->status1 },
		{ CHG_STATUS2, &s->status2 }, { CHG_STATUS7, &s->status7 },
		{ DIE_STATUS, &s->die }, { CONN_STATUS, &s->conn },
		{ OTG_CMD, &s->otg }, { 0x1307, &s->apsd }, { 0x1308, &s->source },
		{ 0x4843, &s->qgmode },
		{ CHG_ENABLE, &s->enabled }, { USB_SUSPEND, &s->suspend },
		{ 0x110b, &s->powerpath }, { CHG_FV, &s->fv_raw },
		{ CHG_FCC, &s->fcc_raw }, { USB_ICL, &s->icl_raw },
	};
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(reads); i++) {
		ret = regmap_read(p->map, reads[i].reg, reads[i].value);
		if (ret)
			return ret;
	}
	s->source &= 0x7f;

	/* No QG hold/clear/ESR/SDAM writes. Reject a changing/torn read. */
	for (tries = 0; tries < 3; tries++) {
		ret = regmap_bulk_read(p->map, QG_LAST_ADC, raw, sizeof(raw));
		if (ret)
			return ret;
		ret = regmap_bulk_read(p->map, QG_LAST_ADC, again, sizeof(again));
		if (ret)
			return ret;
		if (!memcmp(raw, again, sizeof(raw)))
			break;
	}
	if (tries == 3)
		return -EAGAIN;
	if (!p->have_raw || p->last_raw != get_unaligned_le32(raw)) {
		p->last_raw = get_unaligned_le32(raw);
		p->last_change = jiffies;
		p->have_raw = true;
	}
	/* Constant quantized data is valid at rest/termination. In observe it
	 * cannot prove stale hardware, so retain this fail-closed guard only for
	 * the experimental control path. Getters still reject failed/old samples.
	 */
	if (!a9_is_observe() && time_after(jiffies, p->last_change + 30 * HZ))
		return -ESTALE;
	s->uv = div_u64((u64)get_unaligned_le16(raw) * 194637, 1000);
	/* QG is negative on charge. power_supply battery current is positive in. */
	s->ua = -div_s64((s64)(s16)get_unaligned_le16(raw + 2) *
			 p->current_scale, 1000);
	if (s->uv < 2500000 || s->uv > 4500000 || abs(s->ua) > 5000000)
		return -ERANGE;
	if (a9_is_observe() && !observe_adc)
		return 0;
	ret = iio_read_channel_processed(p->therm, &therm_uv);
	if (ret < 0)
		return ret;
	if (a9_temp_dc(therm_uv, &s->temp_dc))
		return -ERANGE;
	s->temp_valid = 1;
	ret = iio_read_channel_processed(p->vbus, &s->vbus_uv);
	if (ret < 0)
		return ret;
	ret = iio_read_channel_processed(p->die, &die_mc);
	if (ret < 0)
		return ret;
	/* Stock charger threshold is 80 C; choose 70 C for bring-up. */
	if (die_mc < -40000 || die_mc > 150000 ||
	    (!a9_is_observe() && die_mc >= 70000))
		return -ERANGE;
	return 0;
}

static int a9_health(const struct a9_pmic *p)
{
	const struct a9_sample *s = &p->sample;

	if (p->error || p->fv_error || p->latched ||
	    time_after(jiffies, p->sampled_at + 5 * HZ))
		return POWER_SUPPLY_HEALTH_UNSPEC_FAILURE;
	if (a9_battery_overvoltage(s))
		return POWER_SUPPLY_HEALTH_OVERVOLTAGE;
	if (a9_is_observe() && !a9_observed_limits_ok(s))
		return POWER_SUPPLY_HEALTH_UNSPEC_FAILURE;
	if ((!a9_is_observe() && s->temp_dc <= A9_COLD_DC) ||
	    (s->status7 & (BIT(2) | BIT(0))))
		return POWER_SUPPLY_HEALTH_COLD;
	if ((!a9_is_observe() && s->temp_dc >= A9_HOT_DC) ||
	    (s->status7 & (BIT(3) | BIT(1))) ||
	    s->die || s->conn)
		return POWER_SUPPLY_HEALTH_OVERHEAT;
	if (!s->temp_valid)
		return POWER_SUPPLY_HEALTH_UNKNOWN;
	return POWER_SUPPLY_HEALTH_GOOD;
}

static void a9_poll(struct work_struct *work)
{
	struct a9_pmic *p = container_of(to_delayed_work(work), struct a9_pmic, work);
	struct a9_sample s = {};
	unsigned int plugin, otg, source;
	bool safe;
	int ret;

	mutex_lock(&p->lock);
	ret = a9_read_sample(p, &s);
	p->error = ret;
	if (ret) {
		if (a9_is_observe())
			goto done;
		goto fault;
	}
	p->sample = s;
	p->sampled_at = jiffies;
	if (a9_is_observe() && fv_stock && !p->fv_error && s.fv_raw != A9_STOCK_FV_RAW) {
		p->fv_error = -EIO;
		dev_err(p->dev, "stock FV changed after probe; health failure, no retry\n");
	}
	if (a9_is_observe())
		goto done;
	if (p->permitted && s.ua > A9_FCC_UA + 100000) {
		ret = -EOVERFLOW;
		goto fault;
	}
	safe = !IS_ENABLED(CONFIG_CHARGER_PMI632_A9_TELEMETRY_ONLY) &&
		p->permitted && !p->latched && !p->suspended &&
		a9_hardware_safe(&s);
	if (!safe) {
		ret = a9_off(p);
		if (ret)
			goto fault;
		goto done;
	}
	ret = a9_settings_check(p, false);
	if (ret)
		goto fault;
	/* Recheck source/OTG after the slow ADC conversions and before enabling. */
	ret = regmap_read(p->map, USB_RT, &plugin);
	if (ret)
		goto fault;
	ret = regmap_read(p->map, OTG_CMD, &otg);
	if (ret)
		goto fault;
	ret = regmap_read(p->map, 0x1308, &source);
	if (ret)
		goto fault;
	source &= 0x7f;
	if (!(plugin & USB_PRESENT) || (plugin & GENMASK(3, 0)) || (otg & BIT(0)) ||
	    (source != BIT(2) && source != BIT(3))) {
		ret = a9_off(p);
		if (ret)
			goto fault;
		goto done;
	}
	/* Pet only after every guard passed; a stuck worker must stop charging. */
	ret = a9_is_observe() ? -EPERM : regmap_write(p->map, WD_PET, BIT(0));
	if (ret)
		goto fault;
	ret = a9_update_check(p, USB_SUSPEND, BIT(0), 0);
	if (ret)
		goto fault;
	ret = a9_update_check(p, CHG_ENABLE, BIT(0), BIT(0));
	if (ret)
		goto fault;
	p->charging = true;
	goto done;
fault:
	p->error = ret;
	p->latched = true; /* reload required; never retry ON after an I/O failure */
	a9_off(p);
	dev_err_ratelimited(p->dev, "charging locked off: %d\n", ret);
done:
	mutex_unlock(&p->lock);
	power_supply_changed(p->battery);
	power_supply_changed(p->usb);
	schedule_delayed_work(&p->work, msecs_to_jiffies(POLL_MS));
}

static enum power_supply_property a9_battery_props[] = {
	POWER_SUPPLY_PROP_STATUS, POWER_SUPPLY_PROP_HEALTH,
	POWER_SUPPLY_PROP_PRESENT, POWER_SUPPLY_PROP_TECHNOLOGY,
	POWER_SUPPLY_PROP_VOLTAGE_NOW, POWER_SUPPLY_PROP_CURRENT_NOW,
	POWER_SUPPLY_PROP_TEMP, POWER_SUPPLY_PROP_CAPACITY,
	POWER_SUPPLY_PROP_MODEL_NAME, POWER_SUPPLY_PROP_SCOPE,
	POWER_SUPPLY_PROP_CONSTANT_CHARGE_VOLTAGE_MAX,
	POWER_SUPPLY_PROP_CONSTANT_CHARGE_CURRENT_MAX,
};

static int a9_battery_get(struct power_supply *psy,
			  enum power_supply_property prop, union power_supply_propval *v)
{
	struct a9_pmic *p = power_supply_get_drvdata(psy);
	struct a9_sample *s = &p->sample;
	int ret = 0, state;

	mutex_lock(&p->lock);
	if (prop == POWER_SUPPLY_PROP_HEALTH) {
		v->intval = a9_health(p);
		goto out;
	}
	if (prop == POWER_SUPPLY_PROP_MODEL_NAME) {
		v->strval = "A9-PMI632-voltage-estimate";
		goto out;
	}
	if (p->error || time_after(jiffies, p->sampled_at + 5 * HZ)) {
		ret = -ENODATA;
		goto out;
	}
	switch (prop) {
	case POWER_SUPPLY_PROP_STATUS:
		state = s->status1 & 7;
		v->intval = POWER_SUPPLY_STATUS_NOT_CHARGING;
		if (!(s->usb & USB_PRESENT) || s->ua < -50000)
			v->intval = POWER_SUPPLY_STATUS_DISCHARGING;
		else if ((a9_is_observe() && a9_observed_charging(s)) ||
			 (!a9_is_observe() && p->charging && state >= 1 && state <= 4 && s->ua > 0))
			v->intval = POWER_SUPPLY_STATUS_CHARGING;
		break;
	case POWER_SUPPLY_PROP_PRESENT:
		v->intval = !(s->batif & (BIT(5) | BIT(4))) && (s->qg & BIT(0));
		break;
	case POWER_SUPPLY_PROP_TECHNOLOGY:
		v->intval = POWER_SUPPLY_TECHNOLOGY_LION;
		break;
	case POWER_SUPPLY_PROP_SCOPE:
		v->intval = POWER_SUPPLY_SCOPE_SYSTEM;
		break;
	case POWER_SUPPLY_PROP_VOLTAGE_NOW:
		v->intval = s->uv;
		break;
	case POWER_SUPPLY_PROP_CURRENT_NOW:
		v->intval = s->ua;
		break;
	case POWER_SUPPLY_PROP_TEMP:
		if (!s->temp_valid) {
			ret = -ENODATA;
			break;
		}
		v->intval = s->temp_dc;
		break;
	case POWER_SUPPLY_PROP_CONSTANT_CHARGE_VOLTAGE_MAX:
		v->intval = 3600000 + s->fv_raw * 10000;
		break;
	case POWER_SUPPLY_PROP_CONSTANT_CHARGE_CURRENT_MAX:
		v->intval = s->fcc_raw * 50000;
		break;
	case POWER_SUPPLY_PROP_CAPACITY:
		v->intval = a9_voltage_capacity(s->uv);
		break;
	default:
		ret = -EINVAL;
		break;
	}
out:
	mutex_unlock(&p->lock);
	return ret;
}

static enum power_supply_property a9_usb_props[] = {
	POWER_SUPPLY_PROP_PRESENT, POWER_SUPPLY_PROP_ONLINE,
	POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT,
};

static int a9_usb_get(struct power_supply *psy,
		      enum power_supply_property prop, union power_supply_propval *v)
{
	struct a9_pmic *p = power_supply_get_drvdata(psy);
	int ret = 0;

	mutex_lock(&p->lock);
	if (p->error || time_after(jiffies, p->sampled_at + 5 * HZ)) {
		ret = -ENODATA;
		goto out;
	}
	switch (prop) {
	case POWER_SUPPLY_PROP_PRESENT:
		v->intval = !!(p->sample.usb & USB_PRESENT);
		break;
	case POWER_SUPPLY_PROP_ONLINE:
		v->intval = a9_is_observe() ? a9_observed_online(&p->sample) : p->charging;
		break;
	case POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT:
		v->intval = a9_is_observe() ? p->sample.icl_raw * 50000 :
			(p->permitted ? A9_ICL_UA : 0);
		break;
	default:
		ret = -EINVAL;
		break;
	}
out:
	mutex_unlock(&p->lock);
	return ret;
}

static const struct power_supply_desc a9_battery_desc = {
	.name = "battery", .type = POWER_SUPPLY_TYPE_BATTERY,
	.properties = a9_battery_props, .num_properties = ARRAY_SIZE(a9_battery_props),
	.get_property = a9_battery_get,
};
static const struct power_supply_desc a9_usb_desc = {
	.name = "a9-usb", .type = POWER_SUPPLY_TYPE_USB,
	.properties = a9_usb_props, .num_properties = ARRAY_SIZE(a9_usb_props),
	.get_property = a9_usb_get,
};

static void a9_cleanup(void *data)
{
	struct a9_pmic *p = data;

	cancel_delayed_work_sync(&p->work);
	a9_off(p);
}

static int a9_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct power_supply_config cfg = {};
	struct a9_fv_io fv_io;
	const struct qcom_spmi_pmic *pmic;
	struct a9_pmic *p;
	unsigned int base, type, subtype;
	int ret;

	pmic = qcom_pmic_get(dev);
	if (IS_ERR(pmic))
		return dev_err_probe(dev, PTR_ERR(pmic), "PMIC identity\n");
	if (pmic->subtype != PMI632_SUBTYPE ||
	    !of_machine_is_compatible("hisense,hlte556n"))
		return -ENODEV;
	ret = device_property_read_u32(dev, "reg", &base);
	if (ret || base != 0x1000)
		return -EINVAL;
	p = devm_kzalloc(dev, sizeof(*p), GFP_KERNEL);
	if (!p)
		return -ENOMEM;
	p->dev = dev;
	p->map = dev_get_regmap(dev->parent, NULL);
	if (!p->map)
		return -ENODEV;
	p->error = -ENODATA;
	mutex_init(&p->lock);
	INIT_DELAYED_WORK(&p->work, a9_poll);
	platform_set_drvdata(pdev, p);

	/* Control mode stops before dependency deferral; observe leaves it alone. */
	ret = a9_off(p);
	if (ret)
		return ret;
	if (!a9_is_observe()) {
		ret = a9_update_check(p, SMB_ENABLE, 0x1f, BIT(3));
		if (ret)
			return ret;
	}
	ret = regmap_read(p->map, QG_TYPE, &type);
	if (ret)
		return ret;
	ret = regmap_read(p->map, QG_SUBTYPE, &subtype);
	if (ret)
		return ret;
	if (type != 0x0d || (subtype != 3 && subtype != 4))
		return dev_err_probe(dev, -ENODEV, "unsupported QG %x/%x\n", type, subtype);
	p->current_scale = subtype == 3 ? 152588 : 305176;
	if (!a9_is_observe() || observe_adc) {
		p->therm = devm_iio_channel_get(dev, "batt-therm-voltage");
		if (IS_ERR(p->therm))
			return dev_err_probe(dev, PTR_ERR(p->therm), "battery NTC ADC\n");
		p->vbus = devm_iio_channel_get(dev, "usb-voltage");
		if (IS_ERR(p->vbus))
			return dev_err_probe(dev, PTR_ERR(p->vbus), "USB ADC\n");
		p->die = devm_iio_channel_get(dev, "die-temp");
		if (IS_ERR(p->die))
			return dev_err_probe(dev, PTR_ERR(p->die), "PMIC die ADC\n");
	}

	/* Register cleanup AFTER supplies so it cancels work before unregister. */
	cfg.drv_data = p;
	cfg.fwnode = dev_fwnode(dev);
	p->battery = devm_power_supply_register(dev, &a9_battery_desc, &cfg);
	if (IS_ERR(p->battery))
		return PTR_ERR(p->battery);
	p->usb = devm_power_supply_register(dev, &a9_usb_desc, &cfg);
	if (IS_ERR(p->usb))
		return PTR_ERR(p->usb);
	ret = devm_add_action_or_reset(dev, a9_cleanup, p);
	if (ret)
		return ret;
	if (!a9_is_observe() && allow_charging &&
	    !IS_ENABLED(CONFIG_CHARGER_PMI632_A9_TELEMETRY_ONLY)) {
		if (!device_property_read_bool(dev, "hisense,charging-validated"))
			return dev_err_probe(dev, -EPERM, "missing coordinator validation gate\n");
		ret = a9_settings_check(p, true);
		if (ret)
			return dev_err_probe(dev, ret, "charging configuration\n");
		p->permitted = true;
	}
	/* No deferral/failure return after this one-shot: keep health observable. */
	fv_io.context = p->map;
	fv_io.read = a9_fv_read;
	fv_io.write = a9_fv_write;
	mutex_lock(&p->lock);
	p->fv_error = a9_fv_stock_once(&fv_io, a9_is_observe() && fv_stock);
	mutex_unlock(&p->lock);
	if (p->fv_error)
		dev_err(dev, "stock FV probe failed: %d; health failure, no retry\n", p->fv_error);
	else if (a9_is_observe() && fv_stock)
		dev_info(dev, "stock FV 0x1070=0x55 verified; no further charger writes\n");
	dev_warn(dev, "mode=%s, SOC is a voltage estimate\n",
		 a9_is_observe() ? (fv_stock ? "observe (probe-only stock FV)" :
				  "observe (firmware charger unchanged)") :
		 (p->permitted ? "control (4.10V/500mA/500mA)" : "control (OFF)"));
	schedule_delayed_work(&p->work, 0);
	return 0;
}

static int a9_suspend(struct device *dev)
{
	struct a9_pmic *p = dev_get_drvdata(dev);
	int ret;

	cancel_delayed_work_sync(&p->work);
	mutex_lock(&p->lock);
	p->suspended = true;
	/* jiffies excludes sleep: never publish the old sample across suspend. */
	p->error = -ENODATA;
	ret = a9_off(p);
	mutex_unlock(&p->lock);
	/* If stop failed, reject suspend and resume the worker. */
	if (ret) {
		p->latched = true;
		p->error = ret;
		schedule_delayed_work(&p->work, 0);
	}
	return ret;
}

static int a9_resume(struct device *dev)
{
	struct a9_pmic *p = dev_get_drvdata(dev);

	mutex_lock(&p->lock);
	p->suspended = false;
	p->have_raw = false;
	/* The asynchronous poll must complete before telemetry is valid again. */
	p->error = -ENODATA;
	mutex_unlock(&p->lock);
	schedule_delayed_work(&p->work, 0);
	return 0;
}

static DEFINE_SIMPLE_DEV_PM_OPS(a9_pm_ops, a9_suspend, a9_resume);

static void a9_shutdown(struct platform_device *pdev)
{
	a9_cleanup(platform_get_drvdata(pdev));
}

static const struct of_device_id a9_match[] = {
	{ .compatible = "hisense,hlte556n-pmi632-power" },
	{}
};
MODULE_DEVICE_TABLE(of, a9_match);

static struct platform_driver a9_driver = {
	.probe = a9_probe,
	.shutdown = a9_shutdown,
	.driver = {
		.name = "pmi632-a9",
		.of_match_table = a9_match,
		.pm = pm_sleep_ptr(&a9_pm_ops),
	},
};
module_platform_driver(a9_driver);
MODULE_DESCRIPTION("Hisense A9 PMI632 observation and guarded charger proposal");
MODULE_LICENSE("GPL");
