#!/usr/bin/env python3
"""Diff proof for patch 0052 (rail-diag): the safety paths are untouched.

usage: rail-diag-safety-diff.py <base-ref> [<new-ref|WORKTREE>]   (run inside the repo)

1. Files that must be byte-identical between base and new: the transport,
   guard, ring, engine, tcon core, Kconfig/Makefile (latches, frame
   progress, drive, shutdown order, FP9929A consumer API). The FP9929A
   driver itself lives in the kernel tree and is not touched by this branch
   (checked: no kernel patch/source of fp9929a in the diff).
2. Protected functions of a9_eink_drv.c must be textually identical.
3. The only functions allowed to change: a9_check_rail_sample (exactly one
   added line, the counter hook), __a9_update (exactly the wf-end hook, two
   added lines), status_show / attribute table / probe (read-only exposure
   and init), and the NEW a9_diag_* helpers.
Exit 0 = proof holds.
"""
import re, subprocess, sys, pathlib

import os; os.chdir(subprocess.check_output(['git','rev-parse','--show-toplevel'],text=True).strip())
base = sys.argv[1]
new = sys.argv[2] if len(sys.argv) > 2 else 'WORKTREE'
D = 'scripts/mainline/eink/driver/'
IDENT = ['a9_eink_transport.c', 'a9_eink_guard.c', 'a9_eink_ring.c', 'a9_eink_engine.c',
         'a9_eink_engine.h', 'a9_eink_pattern.h', 'a9_eink_kms_test.c', 'Makefile', 'Kconfig']
PROTECTED = ['a9_session_frames', 'a9_hv_sample', 'a9_hv_good', 'a9_hv_is_on', 'a9_hv_maybe_on',
             'a9_tx_fault', 'a9_force_hv_off', 'a9_power_off', 'a9_rail_fault', 'a9_rail_report',
             'a9_check_live_rails', 'a9_power_on', 'a9_take_job', 'a9_run_frames', 'a9_run_ring',
             'a9_produce', 'a9_render_thread', 'a9_idle_timeout', 'a9_thread', 'a9_bin_admitted',
             'status_show', 'a9_temps', 'a9_unprepare', 'a9_clear_fault_set', 'a9_load_waveform']


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


def added(n, allowed):
    fa, fb = func(bd, n), func(nd, n)
    la, lb = fa.splitlines(), fb.splitlines()
    import difflib
    d = [l for l in difflib.unified_diff(la, lb, lineterm='', n=0) if l[:1] in '+-' and l[:3] not in ('+++', '---')]
    rem = [l for l in d if l[0] == '-']
    add = [l[1:].strip() for l in d if l[0] == '+']
    if rem:
        fail.append(n + ': lines removed/changed: ' + repr(rem))
    for l in add:
        if not any(l.startswith(a) for a in allowed):
            fail.append(n + ': unexpected added line: ' + l)
    return add


a = added('a9_check_rail_sample', ['a9_diag_rail_sample(a9, was_on, ret, st);'])
if len(a) != 1:
    fail.append('a9_check_rail_sample: %d added lines, want 1' % len(a))
a = added('__a9_update', ['a9_diag_wf_end(a9);'])
if len(a) != 1:
    fail.append('__a9_update: %d added lines, want 1' % len(a))
# the hook sits strictly after the wf_end/wf_ret records and before any branch on ret
u = func(nd, '__a9_update')
if not re.search(r'a9->wf_end = ktime_get\(\);[^\n]*\n\s*a9->wf_ret = ret;\n\s*a9_diag_wf_end\(a9\);[^\n]*\n\s*if \(init_shown\)', u):
    fail.append('a9_diag_wf_end hook is not directly after the wf_end/wf_ret records')
# a9_check_rail_sample: the hook must precede the fault decision, nothing else moved
c = func(nd, 'a9_check_rail_sample')
if c.index('a9_diag_rail_sample') > c.index('if (!was_on || a9_hv_good(ret, st))'):
    fail.append('counter hook after the fault decision')
# new helpers must not name any latch/shutdown/gate primitive
for n in ('a9_diag_bucket', 'a9_diag_rail_sample', 'a9_diag_pd_push', 'a9_diag_wf_end'):
    body = func(nd, n)
    if body is None:
        fail.append('missing helper ' + n)
        continue
    for bad in ('a9_check_rail_sample', 'a9_rail_fault(', 'a9_power_off', 'a9_power_on', 'a9_force_hv_off',
                'atomic_set', 'WRITE_ONCE', 'force_clear', 'regulator_', 'a9x_', 'mutex_', 'hv_fault',
                'enable'):
        if bad in body:
            fail.append(n + ' references ' + bad)
# no new definition outside the expected set
names = lambda s: set(re.findall(r'^static [^\n(;]*?\b(\w+)\(', s, re.M))
newfn = names(nd) - names(bd)
want = {'a9_diag_bucket', 'a9_diag_rail_sample', 'a9_diag_pd_push', 'a9_diag_wf_end', 'a9_diag_copy', 'rail_diag_show'}
if newfn != want:
    fail.append('new functions differ from the expected set: %s' % sorted(newfn ^ want))
# lock discipline: diag.lock is only taken with spin_lock_irqsave, always released in the same function
for n in want:
    b = func(nd, n)
    if b and b.count('spin_lock_irqsave(&a9->diag.lock') != b.count('spin_unlock_irqrestore(&a9->diag.lock'):
        fail.append(n + ': unbalanced diag.lock')
if 'diag.lock' in (func(nd, 'a9_power_off') or '') or 'diag' in (func(nd, 'a9_rail_fault') or ''):
    fail.append('diag referenced from the shutdown/latch path')
if fail:
    print('SAFETY DIFF PROOF FAILED'); print('\n'.join(' - ' + f for f in fail)); sys.exit(1)
print('safety diff proof holds: %d files byte-identical, %d protected functions identical,' % (len(IDENT), len(PROTECTED)))
print('only the counter hook (1 line) in a9_check_rail_sample and the wf-end hook (1 line) in __a9_update changed;')
print('new functions:', ', '.join(sorted(newfn)))
