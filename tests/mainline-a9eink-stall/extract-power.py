#!/usr/bin/env python3
"""Copy the production power/rail-fault path (patch 0039) verbatim.

argv: a9_eink_drv.c include/linux/regulator/fp9929a.h out.inc
The status structure comes from the real header; the functions from the
real driver. Only the FP9929A getter, regulator_force_disable and the
module parameter `enable` are supplied by the harness (power-test.inc).
"""
import pathlib
import re
import sys

drv = pathlib.Path(sys.argv[1]).read_text()
hdr = pathlib.Path(sys.argv[2]).read_text()


def func(source, name):
    m = re.search(r'^static (?:void|int|bool|u64|s64|unsigned int|enum a9_settle) ' + name + r'\([^;]*?\)\s*\{', source, re.M)
    if not m:
        raise SystemExit('missing production function: ' + name)
    start = source.index('{', m.start())
    level, end = 1, start + 1
    while level:
        level += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[m.start():end]


nested = []
for name in ('fp9929a_pg_point', 'fp9929a_pd_trace'):
    item = re.search(r'^struct ' + name + r' \{.*?^\};', hdr, re.M | re.S)
    if not item:
        raise SystemExit('missing production ' + name + ' (0039b)')
    nested.append(item.group())
st = re.search(r'^struct fp9929a_status \{.*?^\};', hdr, re.M | re.S)
if not st:
    raise SystemExit('missing struct fp9929a_status')
for field in ('int pg_raw;', 'bool energized;'):
    if field not in st.group():
        raise SystemExit('fp9929a_status lacks ' + field + ' (patch 0039)')
clear = re.search(r'^#define A9_MODE_CLEAR .+$', drv, re.M)
if not clear:
    raise SystemExit('missing A9_MODE_CLEAR')
names = ('a9_session_frames', 'a9_hv_sample', 'a9_hv_good', 'a9_hv_is_on', 'a9_hv_maybe_on',
         'a9_force_hv_off', 'a9_power_off',
         'a9_diag_bucket', 'a9_diag_rail_sample', 'a9_diag_pd_push', 'a9_diag_wf_end', 'a9_hv_on_ms', 'a9_rate_ok', 'a9_pg_settle',
         'a9_rail_fault', 'a9_rail_report',
         'a9_check_rail_sample', 'a9_pg_wf_end_low', 'a9_pg_wf_end', 'a9_check_live_rails', 'a9_power_on',
         'a9_take_job')
parts = [func(drv, n) for n in names]
pon = parts[names.index('a9_power_on')]
# containment order: capture+latch strictly before the ordered power-down
live = parts[names.index('a9_check_rail_sample')]
fresh = parts[names.index('a9_check_live_rails')]
if 'return a9_check_rail_sample(a9, was_on, ret, &st);' not in fresh:
    raise SystemExit('live check discards sampled status')
if 'return a9_check_live_rails(a9);' not in pon:
    raise SystemExit('power-on bypasses live-rail validation')
i_fault = live.find('a9_rail_fault(a9, ret, st);')
i_off = live.find('a9_power_off(a9);', i_fault)
# 0042: the deferred report follows the validator's ordered power-off
i_report = live.find('a9_rail_report(a9);', i_fault)
if i_fault < 0 or i_off < 0 or i_report < i_off:
    raise SystemExit('a9_power_on: rail fault is not latched before its power-down')
i_latch = pon.find('if (a9x_fault_latched())')
if i_latch < 0 or i_latch > pon.find('a9x_power_on(&a9->tx)') or i_latch > pon.find('a9x_attach('):
    raise SystemExit('a9_power_on: latched faults are not refused before any power-up')
rf = parts[names.index('a9_rail_fault')]
if 'dev_err(' in rf or 'fp9929a_get_status(' in rf or 'a9_hv_sample(' in rf:
    raise SystemExit('rail capture must not log or add a PG lookup before shutdown')
report = parts[names.index('a9_rail_report')]
if report.index('if (!a9->rail.pd_off_confirmed)') > report.index('a9_hv_sample(a9, &st)'):
    raise SystemExit('post-off trace lookup is not gated on confirmed shutdown')
for need in ('atomic_set(&a9x_rail_fault, 1);', 'WRITE_ONCE(enable, false);',
             'a9->force_clear = true;'):
    if need not in rf:
        raise SystemExit('a9_rail_fault lacks ' + need)
# 0052 log-only guards: the diagnostics must stay passive
for n in ('a9_diag_bucket', 'a9_diag_rail_sample', 'a9_diag_pd_push', 'a9_diag_wf_end'):
    body = parts[names.index(n)]
    for bad in ('a9_check_rail_sample', 'a9_rail_fault(', 'a9_power_off', 'a9_power_on', 'a9_force_hv_off',
                'atomic_set', 'WRITE_ONCE', 'force_clear', 'regulator_', 'a9x_', 'mutex_', 'hv_fault',
                'msleep', 'usleep', 'mdelay', 'udelay'):
        if bad in body:
            raise SystemExit('rail-diag helper ' + n + ' touches ' + bad + ': diagnostics must be log-only')
# 0053 pg-settle guards: the settle never replaces the latch, it only precedes it
chk = parts[names.index('a9_check_rail_sample')]
if chk.index('a9_pg_settle(a9, ret, st) == A9_SETTLE_OK') > chk.index('a9_rail_fault(a9, ret, st);'):
    raise SystemExit('a9_check_rail_sample: settle after the latch')
if chk.count('a9_pg_settle(') != 1 or chk.count('a9_rail_fault(a9, ret, st);') != 1:
    raise SystemExit('a9_check_rail_sample: settle/latch call counts changed')
if 'return 0;' not in chk[chk.index('a9_pg_settle('):chk.index('a9_rail_fault(')]:
    raise SystemExit('a9_check_rail_sample: recovered dip does not return 0')
st_body = parts[names.index('a9_pg_settle')]
for bad in ('a9_rail_fault(', 'a9_power_off', 'a9_power_on', 'a9_force_hv_off', 'atomic_set', 'WRITE_ONCE',
            'force_clear', 'regulator_', 'a9x_emergency', 'hv_fault', 'mutex_', 'msleep', 'mdelay', 'udelay'):
    if bad in st_body:
        raise SystemExit('a9_pg_settle touches ' + bad + ': it may only read, wait and count')
# M4: the wf-end sample runs under eng_lock: read and count only, never settle, latch or power off
low_body = parts[names.index('a9_pg_wf_end_low')]
for bad in ('a9_check_rail_sample', 'a9_pg_settle(', 'a9_rail_fault(', 'a9_power_off', 'a9_power_on', 'a9_force_hv_off',
            'atomic_set', 'WRITE_ONCE', 'force_clear', 'regulator_', 'a9x_', 'mutex_', 'msleep', 'usleep', 'mdelay',
            'udelay'):
    if bad in low_body:
        raise SystemExit('a9_pg_wf_end_low touches ' + bad + ': the locked wf-end sample may only read and count')
if 'pg_settle_total_max' not in st_body or 'settle_cap_skip' not in st_body:
    raise SystemExit('a9_pg_settle: the per-boot cap (M4 D1) is missing')
if 'A9_PG_SETTLE_MAX_MS' not in st_body:
    raise SystemExit('a9_pg_settle: the window is not clamped by A9_PG_SETTLE_MAX_MS')
if st_body.count('usleep_range(') != 1 or st_body.count('for (;;)') != 1:
    raise SystemExit('a9_pg_settle: the wait must be one bounded polling loop')
if 'ktime_ms_delta(ktime_get(), t0) >= win' not in st_body:
    raise SystemExit('a9_pg_settle: loop is not bounded by the window')
wfe = parts[names.index('a9_pg_wf_end')]
if 'a9_rail_fault(' in wfe or 'a9_power_off' in wfe:
    raise SystemExit('a9_pg_wf_end must latch only through a9_check_rail_sample')
if chk.count('a9_diag_rail_sample(a9, was_on, ret, st);') != 1:
    raise SystemExit('a9_check_rail_sample: diag counter hook missing')
pathlib.Path(sys.argv[3]).write_text(
    '/* Verbatim production power path (patch 0039b). */\n' + '\n'.join(nested) + '\n' + st.group() + '\n' +
    clear.group() + '\n' + '\n\n'.join(parts) + '\n')
