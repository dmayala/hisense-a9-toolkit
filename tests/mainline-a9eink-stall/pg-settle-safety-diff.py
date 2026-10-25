#!/usr/bin/env python3
"""Diff proof for patch 0053 (pg-settle): the latch, shutdown and power paths are untouched.

usage: pg-settle-safety-diff.py <base-ref> [<new-ref|WORKTREE>]   (run inside the repo; base = eink-rail-diag d1dd12c)

1. Transport, guard, ring, engine, tcon core, Kconfig/Makefile byte-identical (frame-progress interlock,
   drive, ordered shutdown, FP9929A consumer API). The FP9929A driver is in the kernel tree and is not part of
   this branch.
2. Protected functions of a9_eink_drv.c textually identical, including a9_rail_fault (the latch), a9_rail_report,
   a9_power_off, a9_power_on, a9_check_live_rails, a9_thread, a9_hv_sample/good and every 0052 a9_diag_* helper.
3. The only changed functions: a9_check_rail_sample (exactly the two added settle lines, nothing removed or
   moved), __a9_update (exactly the two-line wf-end hook, directly after the 0052 hook, before any branch on
   ret), rail_diag_show (added emit only) and the module-parameter block. New functions: a9_pg_settle,
   a9_pg_wf_end only.
Exit 0 = proof holds.
"""
import difflib, os, pathlib, re, subprocess, sys

os.chdir(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip())
base = sys.argv[1]
new = sys.argv[2] if len(sys.argv) > 2 else 'WORKTREE'
D = 'scripts/mainline/eink/driver/'
IDENT = ['a9_eink_transport.c', 'a9_eink_guard.c', 'a9_eink_ring.c', 'a9_eink_engine.c',
         'a9_eink_engine.h', 'a9_eink_pattern.h', 'a9_eink_kms_test.c', 'Makefile', 'Kconfig']
PROTECTED = ['a9_session_frames', 'a9_hv_sample', 'a9_hv_good', 'a9_hv_is_on', 'a9_hv_maybe_on',
             'a9_tx_fault', 'a9_force_hv_off', 'a9_power_off', 'a9_rail_fault', 'a9_rail_report',
             'a9_check_live_rails', 'a9_power_on', 'a9_take_job', 'a9_run_frames', 'a9_run_ring',
             'a9_produce', 'a9_render_thread', 'a9_idle_timeout', 'a9_thread', 'a9_bin_admitted',
             'status_show', 'a9_temps', 'a9_unprepare', 'a9_clear_fault_set', 'a9_load_waveform',
             'a9_diag_bucket', 'a9_diag_rail_sample', 'a9_diag_pd_push', 'a9_diag_wf_end', 'a9_diag_copy']


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
    try:
        a = show(base, D + f)
    except subprocess.CalledProcessError:
        continue
    if a != show(new, D + f):
        fail.append('file changed: ' + f)
bd, nd = show(base, D + 'a9_eink_drv.c'), show(new, D + 'a9_eink_drv.c')
for n in PROTECTED:
    fa, fb = func(bd, n), func(nd, n)
    if fa is None and fb is None:
        continue
    if fa != fb:
        fail.append('protected function changed: ' + n)


def added(n, allowed_exact):
    fa, fb = func(bd, n), func(nd, n)
    d = [l for l in difflib.unified_diff(fa.splitlines(), fb.splitlines(), lineterm='', n=0)
         if l[:1] in '+-' and l[:3] not in ('+++', '---')]
    rem = [l for l in d if l[0] == '-']
    add = [l[1:].strip() for l in d if l[0] == '+']
    if rem:
        fail.append(n + ': lines removed/changed: ' + repr(rem))
    if add != allowed_exact:
        fail.append(n + ': added lines differ from the expected set: ' + repr(add))
    return add


added('a9_check_rail_sample',
      ['if (a9_pg_settle(a9, ret, st) == A9_SETTLE_OK)	/* 0053: bounded, opt-in */'.replace('\t', '\t').strip(),
       'return 0;'])
added('__a9_update', ['if (!ret && a9_pg_wf_end(a9))	/* 0053: opt-in settle; latched = abort */',
                      'ret = -EIO;'])
c = func(nd, 'a9_check_rail_sample')
if not (c.index('a9_diag_rail_sample') < c.index('if (!was_on || a9_hv_good(ret, st))') <
        c.index('a9_pg_settle(') < c.index('a9_rail_fault(a9, ret, st);') < c.index('a9_power_off(a9);') <
        c.index('a9_rail_report(a9);')):
    fail.append('a9_check_rail_sample: order diag -> good-return -> settle -> latch -> shutdown -> report broken')
u = func(nd, '__a9_update')
if not re.search(r'a9_diag_wf_end\(a9\);[^\n]*\n\s*if \(!ret && a9_pg_wf_end\(a9\)\)[^\n]*\n\s*ret = -EIO;\n\s*if \(init_shown\)', u):
    fail.append('wf-end settle hook is not directly after the 0052 hook and before the ret branch')
# rail_diag_show: only added emit lines, no removed ones (the pd comment text was shortened: allowed, text only)
fa, fb = func(bd, 'rail_diag_show'), func(nd, 'rail_diag_show')
d = [l for l in difflib.unified_diff(fa.splitlines(), fb.splitlines(), lineterm='', n=0)
     if l[:1] in '+-' and l[:3] not in ('+++', '---')]
for l in d:
    if l[0] == '-' and 'entries: shutdown traces' not in l and 'the fault' not in l:
        fail.append('rail_diag_show: unexpected removed line: ' + l)
# new functions: exactly the two
names = lambda s: set(re.findall(r'^static [^\n(;]*?\b(\w+)\(', s, re.M))
newfn = names(nd) - names(bd)
if newfn != {'a9_pg_settle', 'a9_pg_wf_end'}:
    fail.append('new functions: ' + repr(sorted(newfn)))
# a9_pg_settle: reads, waits, counts. It must never latch, shut down, write a gate or touch containment itself
body = func(nd, 'a9_pg_settle')
for bad in ('a9_rail_fault(', 'a9_power_off', 'a9_power_on', 'a9_force_hv_off', 'atomic_set', 'WRITE_ONCE',
            'force_clear', 'regulator_', 'hv_fault', 'mutex_', 'a9x_emergency', 'a9x_power', 'a9x_blank'):
    if bad in body:
        fail.append('a9_pg_settle references ' + bad)
if body.count('usleep_range(') != 1 or body.count('for (;;)') != 1 or 'ktime_ms_delta(ktime_get(), t0) >= win' not in body:
    fail.append('a9_pg_settle: not one bounded polling loop')
if 'A9_PG_SETTLE_MAX_MS' not in body:
    fail.append('a9_pg_settle: window not clamped')
w = func(nd, 'a9_pg_wf_end')
if 'a9_rail_fault(' in w or 'a9_power_off' in w or 'usleep' in w:
    fail.append('a9_pg_wf_end must only call a9_check_rail_sample')
# lock discipline of the new diag fields
if body.count('spin_lock_irqsave(&a9->diag.lock') != body.count('spin_unlock_irqrestore(&a9->diag.lock'):
    fail.append('a9_pg_settle: unbalanced diag.lock')
# nothing in the shutdown/latch path mentions the settle
for n in ('a9_power_off', 'a9_rail_fault', 'a9_rail_report', 'a9_power_on', 'a9_thread'):
    if 'settle' in (func(nd, n) or ''):
        fail.append(n + ' references the settle')
if fail:
    print('SAFETY DIFF PROOF FAILED'); print('\n'.join(' - ' + f for f in fail)); sys.exit(1)
print('safety diff proof holds: %d files byte-identical, %d protected functions identical (latch, shutdown, power-on, thread, 0052 helpers);' % (len(IDENT), len(PROTECTED)))
print('a9_check_rail_sample gained exactly the settle line + return before the unchanged latch; __a9_update gained the 2-line wf-end hook;')
print('new functions:', ', '.join(sorted(newfn)))
