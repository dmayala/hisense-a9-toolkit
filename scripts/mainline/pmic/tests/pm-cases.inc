/* SPDX-License-Identifier: GPL-2.0-only */
static struct a9_pmic p;
static struct device dev = { .data = &p };
static struct power_supply battery = { .data = &p }, usb = { .data = &p };
static union power_supply_propval value;

static int batt(enum power_supply_property prop)
{ assert(!a9_battery_get(&battery, prop, &value)); return value.intval; }
static int usbp(enum power_supply_property prop)
{ assert(!a9_usb_get(&usb, prop, &value)); return value.intval; }
static void poll(void)
{ p.work.pending = false; a9_poll(&p.work.work); assert(p.work.pending); }
static void invalid(void)
{
	static const enum power_supply_property props[] = {
		POWER_SUPPLY_PROP_STATUS, POWER_SUPPLY_PROP_PRESENT,
		POWER_SUPPLY_PROP_TECHNOLOGY, POWER_SUPPLY_PROP_SCOPE,
		POWER_SUPPLY_PROP_VOLTAGE_NOW, POWER_SUPPLY_PROP_CURRENT_NOW,
		POWER_SUPPLY_PROP_TEMP, POWER_SUPPLY_PROP_CAPACITY,
		POWER_SUPPLY_PROP_CONSTANT_CHARGE_VOLTAGE_MAX,
		POWER_SUPPLY_PROP_CONSTANT_CHARGE_CURRENT_MAX,
	};
	static const enum power_supply_property uprops[] = {
		POWER_SUPPLY_PROP_PRESENT, POWER_SUPPLY_PROP_ONLINE,
		POWER_SUPPLY_PROP_INPUT_CURRENT_LIMIT,
	};
	for (unsigned int i = 0; i < sizeof(props) / sizeof(*props); i++)
		assert(a9_battery_get(&battery, props[i], &value) == -ENODATA);
	for (unsigned int i = 0; i < sizeof(uprops) / sizeof(*uprops); i++)
		assert(a9_usb_get(&usb, uprops[i], &value) == -ENODATA);
	assert(batt(POWER_SUPPLY_PROP_HEALTH) == POWER_SUPPLY_HEALTH_UNSPEC_FAILURE);
	assert(!a9_battery_get(&battery, POWER_SUPPLY_PROP_MODEL_NAME, &value));
	assert(!strcmp(value.strval, "A9-PMI632-voltage-estimate"));
}

int main(void)
{
	p.dev = &dev; p.battery = &battery; p.usb = &usb;
	jiffies = 1234;
	next_sample = (struct a9_sample) {
		.uv = 4000000, .ua = 100000, .temp_dc = 250, .temp_valid = 1,
		.usb = 0x10, .qg = 0x81, .enabled = 1, .status1 = 3,
		.fv_raw = 85, .fcc_raw = 10, .icl_raw = 10, .powerpath = 0x11,
	};
	poll();
	assert(batt(POWER_SUPPLY_PROP_VOLTAGE_NOW) == 4000000);
	assert(batt(POWER_SUPPLY_PROP_HEALTH) == POWER_SUPPLY_HEALTH_GOOD);
	if (a9_is_observe()) {
		assert(batt(POWER_SUPPLY_PROP_STATUS) == POWER_SUPPLY_STATUS_CHARGING);
		assert(usbp(POWER_SUPPLY_PROP_ONLINE) == 1);
		assert(bus_writes == 0);
	}
	jiffies += 5 * HZ + 1;
	invalid(); /* Awake sample expiry is unchanged. */
	poll();
	assert(!p.error);
	int before = bus_writes;
	assert(!a9_suspend(&dev));
	assert(p.suspended && !p.work.pending);
	invalid(); /* No jiffies advancement: old data must already be unavailable. */
	assert(bus_writes == before + (a9_is_observe() ? 0 : 2));
	p.have_raw = true;
	assert(!a9_resume(&dev));
	assert(!p.suspended && !p.have_raw && p.work.pending);
	invalid(); /* Resume has queued a poll, but it has not run. */
	assert(bus_writes == before + (a9_is_observe() ? 0 : 2));
	read_error = -EIO;
	poll();
	invalid(); /* A failed first read must not resurrect the cache. */
	read_error = 0;
	next_sample.usb = 0; next_sample.ua = -100000;
	next_sample.uv = 3800000; next_sample.temp_dc = 275;
	poll();
	assert(batt(POWER_SUPPLY_PROP_VOLTAGE_NOW) == 3800000);
	assert(batt(POWER_SUPPLY_PROP_CURRENT_NOW) == -100000);
	assert(batt(POWER_SUPPLY_PROP_TEMP) == 275);
	assert(batt(POWER_SUPPLY_PROP_CAPACITY) == a9_voltage_capacity(3800000));
	assert(batt(POWER_SUPPLY_PROP_STATUS) == POWER_SUPPLY_STATUS_DISCHARGING);
	assert(usbp(POWER_SUPPLY_PROP_PRESENT) == 0);
	assert(usbp(POWER_SUPPLY_PROP_ONLINE) == 0);
	p.fv_error = -EIO;
	assert(!a9_suspend(&dev)); assert(!a9_resume(&dev)); poll();
	assert(p.fv_error == -EIO);
	assert(batt(POWER_SUPPLY_PROP_HEALTH) == POWER_SUPPLY_HEALTH_UNSPEC_FAILURE);
	p.fv_error = 0;
	for (int i = 0; i < 10; i++) {
		assert(!a9_suspend(&dev)); invalid();
		assert(!a9_resume(&dev)); invalid(); poll();
		assert(batt(POWER_SUPPLY_PROP_VOLTAGE_NOW) == 3800000);
	}
	/* Restore/thaw callback must invalidate even without this suspend callback. */
	assert(!a9_resume(&dev)); invalid(); poll();
	if (!a9_is_observe()) {
		off_error = -EIO;
		assert(a9_suspend(&dev) == -EIO);
		assert(p.latched && p.error == -EIO && p.work.pending);
		invalid(); /* Existing control-mode stop failure still vetoes suspend. */
	} else {
		assert(bus_writes == 0); /* Observe PM/poll/error path stays zero-write. */
	}
	assert(changes > 0);
	puts("PASS: stale/invalid cache, PM ordering, read failure, refresh, latches, repeated cycles, write budget");
	return 0;
}
