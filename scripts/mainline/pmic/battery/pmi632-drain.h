/* SPDX-License-Identifier: GPL-2.0-only */
/* Optional, volatile QG batch collection. Included after charging policy. */
static int drain_release_locked(struct battery *b)
{
	int ret, first = 0, attempt;

	if (!b->drain_held)
		return 0;
	for (attempt = 0; attempt < 3; attempt++) {
		ret = update(b, 0x4841, BIT(0), 0);
		if (!first)
			first = ret;
		if (!ret) {
			b->drain_held = false;
			break;
		}
	}
	return first;
}

static void drain_arm_locked(struct battery *b);

static int drain_restore_locked(struct battery *b)
{
	struct a9_sample fresh = {};
	int ret = 0, step = 0, attempt;

	if (!b->drain_user_suspend && !b->drain_restore_pending && !b->drain_held)
		return 0;
	b->drain_user_suspend = false;
	b->drain_restore_pending = true;
	b->applied_ua = -1;
	/* Explicitly release this USER pause even on unplug, OTG or source fault,
	 * where normal input_policy intentionally does no register writes.
	 * Battery charging remains off until fresh normal safety policy runs.
	 */
	for (attempt = 0; attempt < 3; attempt++) {
		step = update(b, 0x1340, BIT(0), 0);
		if (!ret)
			ret = step;
		if (!step) {
			b->drain_restore_pending = false;
			break;
		}
	}
	step = drain_release_locked(b);
	if (!ret)
		ret = step;
	step = b->drain_held ? -EIO : sample(b, &fresh);
	if (!step) {
		b->s = fresh;
		b->status = a9_reported_status(&b->s, b->fault);
		if (!b->drain_restore_pending)
			step = input_policy(b); /* Reapply the independent DWC3 vote. */
		if (!step && !b->drain_restore_pending)
			step = charge_policy(b); /* Fault/JEITA/voltage limits still apply. */
	}
	if (step || b->drain_restore_pending) {
		b->fault = true;
		stop(b);
	}
	if (!ret)
		ret = step;
	/* A failed write must not disappear behind a false USER flag. Retry
	 * independently of ordinary polling, including its latched fault path.
	 */
	if (b->drain_restore_pending || b->drain_held)
		drain_arm_locked(b);
	else
		alarm_cancel(&b->drain_alarm);
	return ret;
}

static int drain_capacity_locked(struct battery *b)
{
	int fresh = profile_seed(b->s.uv, b->s.temp_dc) / 100;
	int integrated;
	s64 uah;

	if (!b->drain_user_suspend)
		return fresh;
	uah = div_s64(-b->drain_raw_ms * b->scale, 3600000000LL);
	integrated = b->drain_start_soc + div_s64(uah * 100, b->fcc_uah);
	/* Loaded voltage is a conservative second bound while discharging.
	 * Never trust the legacy cached percentage to enforce this stage's floor.
	 */
	return clamp(min(fresh, integrated), 0, 100);
}

static int drain_config_locked(struct battery *b, struct a9_qg_config *c)
{
	unsigned int s2, interval, sleep;
	int ret = regmap_read(b->map, 0x4851, &s2);

	if (!ret)
		ret = regmap_read(b->map, 0x4852, &interval);
	if (!ret)
		ret = regmap_read(b->map, 0x485d, &sleep);
	if (!ret)
		ret = a9_qg_config(s2, interval, sleep, c);
	return ret;
}

static int drain_capture_locked(struct battery *b, bool discard)
{
	struct a9_qg_config cfg;
	unsigned int length, count, covered = 0;
	u8 fifo_data[16], acc[3];
	short fifo[8];
	s64 raw = 0, now;
	int ret, release, i, sum;

	ret = drain_config_locked(b, &cfg);
	if (ret)
		return ret;
	if (!discard && memcmp(&cfg, &b->drain_cfg, sizeof(cfg)))
		return -ESTALE;
	/* Stock qg_master_hold: clear then hold. Only bit0 changes; no sampling
	 * configuration, SOC, persistent state or calibration is written.
	 */
	b->drain_held = true;
	ret = update(b, 0x4841, BIT(0), 0);
	if (!ret)
		ret = update(b, 0x4841, BIT(0), BIT(0));
	if (!ret && !discard) {
		ret = regmap_read(b->map, 0x480a, &length);
		if (!ret)
			ret = regmap_bulk_read(b->map, 0x48a0, fifo_data, sizeof(fifo_data));
		if (!ret)
			ret = regmap_read(b->map, 0x488e, &count);
		if (!ret)
			ret = regmap_bulk_read(b->map, 0x488b, acc, sizeof(acc));
		if (!ret) {
			for (i = 0; i < ARRAY_SIZE(fifo); i++)
				fifo[i] = get_unaligned_le16(fifo_data + i * 2);
			sum = sign_extend32(acc[0] | acc[1] << 8 | acc[2] << 16, 23);
			now = ktime_to_ms(ktime_get_boottime());
			ret = a9_qg_batch(&cfg, length & 15, fifo, count & 255, sum,
					 now - b->drain_last_ms, &raw, &covered);
		}
	}
	/* Always attempt release, including failed hold/read/decode paths. */
	release = drain_release_locked(b);
	if (!ret)
		ret = release;
	if (!ret) {
		b->drain_cfg = cfg;
		b->drain_last_ms = ktime_to_ms(ktime_get_boottime());
		b->drain_raw_ms += raw;
		b->drain_sample_ms += covered;
	}
	return ret;
}

static void drain_arm_locked(struct battery *b)
{
	alarm_start_timer(&b->drain_alarm, ms_to_ktime(b->drain_restore_pending || b->drain_held ? 1000 : b->drain_cfg.wake_ms), true);
}

static void drain_alarm(struct alarm *alarm, ktime_t now)
{
	struct battery *b = container_of(alarm, struct battery, drain_alarm);

	/* Keep the device awake until its bus has resumed and work has run. */
	pm_stay_awake(b->dev);
	queue_work(system_freezable_wq, &b->drain_work);
}

static void drain_work(struct work_struct *work)
{
	struct battery *b = container_of(work, struct battery, drain_work);
	struct a9_sample s = {};
	int ret = 0;

	mutex_lock(&b->lock);
	if (!READ_ONCE(b->stopped) && (b->drain_restore_pending || b->drain_held))
		drain_restore_locked(b);
	if (b->drain_user_suspend && !READ_ONCE(b->stopped)) {
		b->drain_wakeups++;
		ret = b->fault ? -EIO : sample(b, &s);
		if (!ret) {
			b->s = s;
			b->status = a9_reported_status(&b->s, b->fault);
			ret = drain_capture_locked(b, false);
		}
		if (!ret && (!(s.usb & BIT(4)) || drain_capacity_locked(b) <= 35 ||
			    ktime_to_ms(ktime_get_boottime()) - b->drain_started_ms >= 4200000))
			ret = -ECANCELED;
		if (ret) {
			b->drain_error = ret;
			drain_restore_locked(b);
		} else {
			drain_arm_locked(b);
		}
	}
	mutex_unlock(&b->lock);
	pm_relax(b->dev);
}

static ssize_t input_suspend_show(struct device *dev,
				 struct device_attribute *attr, char *buf)
{
	struct battery *b = power_supply_get_drvdata(dev_get_drvdata(dev));
	unsigned int actual;
	int ret;

	mutex_lock(&b->lock);
	ret = regmap_read(b->map, 0x1340, &actual);
	if (!ret)
		ret = sysfs_emit(buf, "%d\n", b->drain_restore_pending || b->drain_held ||
				 (b->drain_user_suspend && (actual & 1)));
	mutex_unlock(&b->lock);
	return ret;
}

static ssize_t input_suspend_store(struct device *dev,
				  struct device_attribute *attr, const char *buf, size_t count)
{
	struct battery *b = power_supply_get_drvdata(dev_get_drvdata(dev));
	struct a9_sample s = {};
	bool paused;
	int ret;

	ret = kstrtobool(buf, &paused);
	if (ret)
		return ret;
	mutex_lock(&b->lock);
	if (!paused) {
		ret = drain_restore_locked(b);
		goto out;
	}
	if (b->drain_user_suspend) {
		ret = 0;
		goto out;
	}
	ret = sample(b, &s);
	if (!ret) {
		b->s = s;
		b->status = a9_reported_status(&b->s, b->fault);
	}
	if (!ret && (!b->seeded || b->fault || !alarmtimer_get_rtcdev() ||
		     !(s.usb & BIT(4)) || drain_capacity_locked(b) <= 35))
		ret = -EAGAIN;
	if (!ret) {
		b->drain_restore_pending = true;
		ret = stop(b);
	}
	if (!ret)
		ret = update(b, 0x1340, BIT(0), BIT(0));
	if (!ret) {
		b->drain_user_suspend = true;
		b->drain_restore_pending = false;
		b->drain_raw_ms = 0;
		b->drain_sample_ms = 0;
		b->drain_wakeups = 0;
		b->drain_start_soc = profile_seed(s.uv, s.temp_dc) / 100;
		ret = drain_capture_locked(b, true);
		b->drain_started_ms = b->drain_last_ms;
	}
	b->drain_error = ret;
	if (ret) {
		/* Also covers a partially completed suspend write. */
		drain_restore_locked(b);
	} else {
		drain_arm_locked(b);
	}
out:
	mutex_unlock(&b->lock);
	return ret ? ret : count;
}
static DEVICE_ATTR(input_suspend, 0600, input_suspend_show, input_suspend_store);
static struct attribute *drain_usb_attrs[] = { &dev_attr_input_suspend.attr, NULL };
ATTRIBUTE_GROUPS(drain_usb);

static ssize_t drain_counter_supported_show(struct device *dev,
					   struct device_attribute *attr, char *buf)
{
	struct battery *b = power_supply_get_drvdata(dev_get_drvdata(dev));
	struct a9_qg_config c;
	int ready;

	mutex_lock(&b->lock);
	ready = b->seeded && alarmtimer_get_rtcdev() && !drain_config_locked(b, &c);
	mutex_unlock(&b->lock);
	return sysfs_emit(buf, "%d\n", ready);
}
static DEVICE_ATTR_RO(drain_counter_supported);

static ssize_t drain_capacity_show(struct device *dev,
                                  struct device_attribute *attr, char *buf)
{
	struct battery *b = power_supply_get_drvdata(dev_get_drvdata(dev));
	struct a9_sample fresh = {};
	int ret, capacity;

	mutex_lock(&b->lock);
	ret = sample(b, &fresh);
	if (!ret) {
		b->s = fresh;
		b->status = a9_reported_status(&b->s, b->fault);
		capacity = drain_capacity_locked(b);
		if (b->drain_user_suspend && capacity <= 35) {
			b->drain_error = -ECANCELED;
			drain_restore_locked(b);
		}
		ret = sysfs_emit(buf, "%d\n", capacity);
	} else if (b->drain_user_suspend) {
		b->drain_error = ret;
		drain_restore_locked(b);
	}
	mutex_unlock(&b->lock);
	return ret;
}
static DEVICE_ATTR(drain_capacity, 0400, drain_capacity_show, NULL);


static ssize_t drain_snapshot_show(struct device *dev,
				   struct device_attribute *attr, char *buf)
{
	struct battery *b = power_supply_get_drvdata(dev_get_drvdata(dev));
	struct a9_sample s = {};
	s64 counter = 0, elapsed = 0;
	int ret;

	mutex_lock(&b->lock);
	ret = sample(b, &s);
	if (!ret) {
		b->s = s;
		b->status = a9_reported_status(&b->s, b->fault);
	}
	if (ret && b->drain_user_suspend) {
		b->drain_error = ret;
		drain_restore_locked(b);
	}
	if (!ret && b->drain_user_suspend) {
		ret = b->fault ? -EIO : drain_capture_locked(b, false);
		if (!ret && (!(s.usb & BIT(4)) || drain_capacity_locked(b) <= 35 ||
			    ktime_to_ms(ktime_get_boottime()) - b->drain_started_ms >= 4200000))
			ret = -ECANCELED;
		if (ret) {
			b->drain_error = ret;
			drain_restore_locked(b);
		} else {
			counter = div_s64(-b->drain_raw_ms * b->scale, 3600000000LL);
			elapsed = b->drain_last_ms - b->drain_started_ms;
			drain_arm_locked(b);
		}
	}
	if (!ret)
		ret = sysfs_emit(buf, "capacity %d\ncharge_counter %lld\ncharge_full_design %d\n"
				"counter_sample_ms %llu\ncounter_elapsed_ms %lld\ncounter_wakeups %u\n",
				drain_capacity_locked(b), counter, b->fcc_uah,
				b->drain_sample_ms, elapsed, b->drain_wakeups);
	mutex_unlock(&b->lock);
	return ret;
}
static DEVICE_ATTR(drain_snapshot, 0400, drain_snapshot_show, NULL);
static struct attribute *drain_bat_attrs[] = {
	&dev_attr_drain_counter_supported.attr, &dev_attr_drain_snapshot.attr,
	&dev_attr_drain_capacity.attr, NULL
};
ATTRIBUTE_GROUPS(drain_bat);
