#!/usr/bin/env python3
"""Diff proof for eink-settle-fix (M4, review 89): only the 0053 settle path moved and gained a cap.

usage: settle-fix-safety-diff.py <base-ref> [<new-ref|WORKTREE>]   (base = eink-baseline 3a467cb)

1. Every driver file except a9_eink_drv.c / a9_eink.h, and the tcon core, is byte-identical.
2. Every rail/power/latch/ring/session function is textually identical (a9_rail_fault, a9_power_off,
   a9_check_rail_sample, a9_check_live_rails, a9_power_on, a9_run_ring, ...). The settle may only read, wait, count.
3. The only changed functions: a9_diag_wf_end (returns the low time; nothing else), a9_pg_settle (clock origin,
   per-boot cap, counters/log), a9_pg_wf_end (now: decide from the given sample), __a9_update (sample under the
   lock, decide after the unlock), rail_diag_show (appended fields). New: a9_pg_wf_end_low (read and count only).
4. In __a9_update the 0053 decision sits after the LAST mutex_unlock(&a9->eng_lock) of the success path, and no
   0053 call precedes that unlock except the sample.
Exit 0 = proof holds.
"""
import difflib, os, pathlib, re, subprocess, sys

os.chdir(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip())
base = sys.argv[1]
new = sys.argv[2] if len(sys.argv) > 2 else 'WORKTREE'
D = 'scripts/mainline/eink/driver/'
IDENT = [D + f for f in ['a9_eink_transport.c', 'a9_eink_guard.c', 'a9_eink_ring.c', 'a9_eink_pattern.h',
                         'a9_eink_kms_test.c', 'a9_eink_engine.c', 'a9_eink_engine.h', 'a9_eink_damage.c',
                         'Kconfig', 'Makefile']] + ['scripts/mainline/tcon/a9tcon.c', 'scripts/mainline/tcon/a9tcon.h']
PROTECTED = ['a9_session_frames', 'a9_hv_sample', 'a9_hv_good', 'a9_hv_is_on', 'a9_hv_maybe_on',
             'a9_tx_fault', 'a9_force_hv_off', 'a9_power_off', 'a9_rail_fault', 'a9_rail_report',
             'a9_check_live_rails', 'a9_check_rail_sample', 'a9_power_on', 'a9_run_frames', 'a9_run_ring',
             'a9_produce', 'a9_render_thread', 'a9_idle_timeout', 'a9_bin_admitted', 'status_show',
             'a9_temps', 'a9_clear_fault_set', 'a9_load_waveform', 'a9_diag_bucket', 'a9_diag_rail_sample',
             'a9_diag_pd_push', 'a9_diag_copy', 'a9_idle_clip', 'a9_hold_ms', 'a9_session_hold', 'a9_thread',
             'a9_take_job', 'a9_unprepare', 'a9_plane_atomic_update', 'damage_stats_show', 'a9_probe']
ALLOWED = {'a9_diag_wf_end', 'a9_pg_settle', 'a9_pg_wf_end', '__a9_update', 'rail_diag_show'}
NEW = {'a9_pg_wf_end_low'}


def show(ref, path):
    if ref == 'WORKTREE':
        return pathlib.Path(path).read_text()
    return subprocess.check_output(['git', 'show', ref + ':' + path], text=True)


def func(src, name):
    m = re.search(r'^static [^\n;]*?\b' + name + r'\([^;]*?\)\s*\{', src, re.M | re.S)
    if not m:
        return None
    i = src.index('{', m.start())
    lvl, j = 1, i + 1
    while lvl:
        lvl += (src[j] == '{') - (src[j] == '}')
        j += 1
    return src[m.start():j]


fail = []
for f in IDENT:
    if show(base, f) != show(new, f):
        fail.append('file changed: ' + f)
bd, nd = show(base, D + 'a9_eink_drv.c'), show(new, D + 'a9_eink_drv.c')
for n in PROTECTED:
    if func(bd, n) != func(nd, n):
        fail.append('protected function changed: ' + n)
names = lambda s: set(re.findall(r'^static [^\n(;]*?\b(\w+)\(', s, re.M))
changed = {n for n in names(bd) & names(nd) if func(bd, n) != func(nd, n)}
if changed - ALLOWED:
    fail.append('unexpected changed functions: ' + repr(sorted(changed - ALLOWED)))
newfn = names(nd) - names(bd)
if newfn != NEW:
    fail.append('new functions: ' + repr(sorted(newfn)))

# a9_diag_wf_end: only the return type and the return statements
a, b = func(bd, 'a9_diag_wf_end'), func(nd, 'a9_diag_wf_end')
d = [l for l in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm='', n=0) if l[:1] in '+-' and l[:3] not in ('+++', '---')]
rem = [l[1:].strip() for l in d if l[0] == '-']
add = [l[1:].strip() for l in d if l[0] == '+']
if rem != ['static void a9_diag_wf_end(struct a9_eink *a9)', 'return;'] or \
   [x for x in add if not (x.startswith('return') or x.startswith('/*') or x.startswith('static s64 a9_diag_wf_end('))]:
    fail.append('a9_diag_wf_end changed beyond its return value: -%r +%r' % (rem, add))

# the locked sample only reads and counts
low = func(nd, 'a9_pg_wf_end_low') or ''
for bad in ('a9_check_rail_sample', 'a9_pg_settle(', 'a9_rail_fault(', 'a9_power_', 'a9_force_hv_off', 'atomic_set',
            'WRITE_ONCE', 'force_clear', 'regulator_', 'a9x_', 'mutex_', 'sleep', 'delay', 'enable'):
    if bad in low.replace('a9_hv_good', ''):
        fail.append('a9_pg_wf_end_low touches ' + bad)
# the settle may only read, wait and count
st = func(nd, 'a9_pg_settle')
for bad in ('a9_rail_fault(', 'a9_power_', 'a9_force_hv_off', 'atomic_set', 'WRITE_ONCE', 'force_clear',
            'regulator_', 'a9x_emergency', 'hv_fault', 'mutex_', 'msleep', 'mdelay', 'udelay', 'a9_check_rail_sample'):
    if bad in st:
        fail.append('a9_pg_settle touches ' + bad)
for need in ('A9_PG_SETTLE_MAX_MS', 'pg_settle_total_max', 'settle_cap_skip', 'settle_first_us = 0'):
    if need not in st:
        fail.append('a9_pg_settle lacks ' + need)
wf = func(nd, 'a9_pg_wf_end')
if wf.count('a9_check_rail_sample(a9, true, 0, st)') != 1:
    fail.append('a9_pg_wf_end does not decide through a9_check_rail_sample')

# __a9_update: decision after the success path's unlock, sample before it
u = func(nd, '__a9_update')
i_abort = u.index('\nabort:')
succ = u[:i_abort]
i_unlock = succ.rindex('mutex_unlock(&a9->eng_lock);')
i_dec = succ.find('a9_pg_wf_end(a9, &pg_st)')
i_smp = succ.find('a9_pg_wf_end_low(a9, pg_first, &pg_st)')
if i_dec < i_unlock or u.count('a9_pg_wf_end(a9') != 1:
    fail.append('__a9_update: the 0053 decision is not after the success-path unlock (or occurs twice)')
if i_smp < 0 or i_smp > i_unlock:
    fail.append('__a9_update: the 0053 sample is not inside the locked section')
if 'a9->aborts++' not in succ[i_dec:]:
    fail.append('__a9_update: a latch after the unlock is not counted as an abort')
du = [l for l in difflib.unified_diff(func(bd, '__a9_update').splitlines(), u.splitlines(), lineterm='', n=0)
      if l[:1] in '-' and l[:3] != '---']
if [l[1:].strip() for l in du] != ['struct fp9929a_status st;', 'bool was_on;',
                                   'a9_diag_wf_end(a9);	/* 0052: one recorded, never-acted-on PG read */',
                                   'if (!ret && a9_pg_wf_end(a9))	/* 0053: opt-in settle; latched = abort */',
                                   'ret = -EIO;']:
    fail.append('__a9_update: removed lines differ: ' + repr([l[1:].strip() for l in du]))

if fail:
    print('SAFETY DIFF FAILED')
    for f in fail:
        print(' -', f)
    sys.exit(1)
print('settle-fix safety diff holds: %d files byte-identical, %d protected functions identical, changed %s, new %s;'
      ' 0053 sample under eng_lock, decision after the unlock' % (len(IDENT), len(PROTECTED), sorted(changed), sorted(newfn)))
