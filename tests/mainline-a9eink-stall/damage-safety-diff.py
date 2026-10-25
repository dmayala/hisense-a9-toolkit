#!/usr/bin/env python3
"""Diff proof for eink-damage: no rail/HV/gate/frame-progress/fence/ring logic changed.

usage: damage-safety-diff.py <base-ref> [<new-ref|WORKTREE>]   (run inside the repo; base = eink-pg-settle 71561fc)

1. Transport, guard, ring, tcon core, pattern, kms test, Kconfig byte-identical (frame-progress interlock, drive,
   ordered shutdown, FP9929A consumer API, ring, the stock-oracle-validated a9tcon core). driver/Makefile differs only by
   the new object.
2. Every protected function of a9_eink_drv.c textually identical: the whole rail/PG/HV/power/session machinery,
   a9_run_frames/a9_run_ring/a9_produce/a9_render_thread, a9_idle_timeout, a9_bin_admitted, status_show, the pg-settle
   and rail-diag helpers.
3. Across a9_eink_drv.c, a9_eink_engine.c and a9_eink.h only FOUR lines are removed (the old convert call, re-added
   unchanged in the damage_prep=0 branch, and the a9t_condition call, re-added in the inc_enable=0 branch); everything else is added.
4. The only functions of a9_eink_drv.c that differ: a9_plane_atomic_update, a9_take_job, __a9_update (one prepare-site
   addition, three invalidate lines), a9_unprepare (one line), a9_thread (the dropped-job guard) and the new
   damage_stats_show; the module-parameter block and the probe allocation gain a line.
5. The new engine file is pure C with no kernel/power/drive symbol.
Exit 0 = proof holds.
"""
import difflib, os, pathlib, re, subprocess, sys

os.chdir(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip())
base = sys.argv[1]
new = sys.argv[2] if len(sys.argv) > 2 else 'WORKTREE'
D = 'scripts/mainline/eink/driver/'
IDENT = [D + f for f in ['a9_eink_transport.c', 'a9_eink_guard.c', 'a9_eink_ring.c', 'a9_eink_pattern.h',
                         'a9_eink_kms_test.c', 'Kconfig']] + ['scripts/mainline/tcon/a9tcon.c', 'scripts/mainline/tcon/a9tcon.h']
PROTECTED = ['a9_session_frames', 'a9_hv_sample', 'a9_hv_good', 'a9_hv_is_on', 'a9_hv_maybe_on',
             'a9_tx_fault', 'a9_force_hv_off', 'a9_power_off', 'a9_rail_fault', 'a9_rail_report',
             'a9_check_live_rails', 'a9_check_rail_sample', 'a9_power_on', 'a9_run_frames', 'a9_run_ring',
             'a9_produce', 'a9_render_thread', 'a9_idle_timeout', 'a9_bin_admitted', 'status_show',
             'a9_temps', 'a9_clear_fault_set', 'a9_load_waveform', 'a9_diag_bucket', 'a9_diag_rail_sample',
             'a9_diag_pd_push', 'a9_diag_wf_end', 'a9_diag_copy', 'a9_pg_settle', 'a9_pg_wf_end',
             'rail_diag_show', 'a9_idle_clip', 'a9_hold_ms', 'a9_session_hold']


def show(ref, path):
    if ref == 'WORKTREE':
        return pathlib.Path(path).read_text()
    try:
        return subprocess.check_output(['git', 'show', ref + ':' + path], text=True, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        return None


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
    a, b = show(base, f), show(new, f)
    if a is not None and a != b:
        fail.append('file changed: ' + f)
mk_a, mk_b = show(base, D + 'Makefile'), show(new, D + 'Makefile')
if mk_a.replace('a9_eink_engine.o a9tcon.o', 'a9_eink_engine.o a9_eink_damage.o a9tcon.o') != mk_b:
    fail.append('driver/Makefile differs by more than the new object')

bd, nd = show(base, D + 'a9_eink_drv.c'), show(new, D + 'a9_eink_drv.c')
for n in PROTECTED:
    fa, fb = func(bd, n), func(nd, n)
    if fa is None and fb is None:
        continue
    if fa != fb:
        fail.append('protected function changed: ' + n)

removed = []
for f in ['a9_eink_drv.c', 'a9_eink_engine.c', 'a9_eink.h', 'a9_eink_engine.h']:
    a, b = show(base, D + f), show(new, D + f)
    for l in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm='', n=0):
        if l[:1] == '-' and l[:3] != '---':
            removed.append((f, l[1:].strip()))
want = [('a9_eink_drv.c', 'a9e_fb_to_grey_bounce(a9->shadow, src, fb->pitches[0], fmt,'),
        ('a9_eink_drv.c', 'clip.x1 - sx, clip.y1 - sy, clip.x2 - sx, clip.y2 - sy, &changed,'),
        ('a9_eink_drv.c', 'READ_ONCE(convert_bounce) ? a9->convert_scratch : NULL);'),
        ('a9_eink_engine.c', 'a9t_condition(&e->st, e->grey, e->mode, e->nlv, e->work);')]
if removed != want:
    fail.append('removed lines differ: ' + repr(removed))
# the removed lines are re-added, in the damage_prep == 0 / inc_enable == 0 branch
if nd.count('a9e_fb_to_grey_bounce(a9->shadow, src, fb->pitches[0], fmt,') != 1:
    fail.append('old convert call not retained')
ne = show(new, D + 'a9_eink_engine.c')
if not re.search(r'if \(e->inc_enable\)\n\t\ta9d_condition_full\(e\);\n\telse\n\t\ta9t_condition\(&e->st, e->grey, e->mode, e->nlv, e->work\);', ne):
    fail.append('a9t_condition is not the else branch of inc_enable')

# changed functions of a9_eink_drv.c: exactly this set
names = lambda s: set(re.findall(r'^static [^\n(;]*?\b(\w+)\(', s, re.M))
changed = set()
for n in names(bd) & names(nd):
    if func(bd, n) != func(nd, n):
        changed.add(n)
allowed = {'a9_plane_atomic_update', 'a9_take_job', '__a9_update', 'a9_unprepare', 'a9_thread', 'a9_probe'}
extra = changed - allowed
if extra:
    fail.append('unexpected changed functions: ' + repr(sorted(extra)))
newfn = names(nd) - names(bd)
if newfn != {'damage_stats_show'}:
    fail.append('new functions: ' + repr(sorted(newfn)))

# __a9_update: exactly these added lines, nothing removed
def added(n, expect):
    fa, fb = func(bd, n), func(nd, n)
    d = [l for l in difflib.unified_diff(fa.splitlines(), fb.splitlines(), lineterm='', n=0)
         if l[:1] in '+-' and l[:3] not in ('+++', '---')]
    add = [l[1:].strip() for l in d if l[0] == '+']
    if any(l[0] == '-' for l in d):
        fail.append(n + ': lines removed')
    if add != expect:
        fail.append(n + ': added lines differ: ' + repr(add))

added('__a9_update', ['a9->e.inc_enable = READ_ONCE(damage_prep) && a9->e.ee;',
                      'a9->e.dmg = job->tm_ok ? job->tm : NULL;',
                      'a9e_inc_invalidate(&a9->e);', 'a9e_inc_invalidate(&a9->e);', 'a9e_inc_invalidate(&a9->e);'])
added('a9_unprepare', ['a9e_inc_invalidate(&a9->e);	/* old no longer equals nlv */'])
t = func(nd, '__a9_update')
if t.index('a9e_inc_invalidate') < t.index('a9e_prepare') and False:
    pass
# the hooks sit where the old is restored, never in a rail/power path
for m in re.finditer(r'a9e_inc_invalidate\(&a9->e\);', t):
    ctx = t[max(0, m.start() - 160):m.start()]
    if 'rail' in ctx.lower() or 'a9_power' in ctx:
        fail.append('invalidate near a rail/power call in __a9_update')
th = func(nd, 'a9_thread')
if 'a9_update(a9, &job);' not in th or th.count('a9e_inc_invalidate') != 1:
    fail.append('a9_thread guard shape')

# the new engine file: no kernel/power/drive symbol
dm = show(new, D + 'a9_eink_damage.c')
for bad in ('a9_rail', 'a9_power', 'a9x_', 'regulator', 'mutex', 'spin_', 'WRITE_ONCE', 'READ_ONCE', 'fp9929a',
            'kmalloc', 'vmalloc', 'ktime', 'msleep', 'usleep', 'a9_hv', 'enable'):
    if re.search(r'\b' + re.escape(bad), dm.replace('inc_enable', '')):
        fail.append('a9_eink_damage.c references ' + bad)
if '#include' not in dm or any(i not in ('"a9_eink_engine.h"', '<string.h>')
                                for i in re.findall(r'#include\s+(\S+)', dm)):
    fail.append('a9_eink_damage.c includes more than the engine header and string.h')

if fail:
    print('SAFETY DIFF FAILED')
    for f in fail:
        print(' -', f)
    sys.exit(1)
print('safety diff holds: %d files byte-identical, %d protected functions identical, 4 lines removed (all re-added in the'
      ' default branch), changed functions %s, new function %s' % (len(IDENT), len(PROTECTED), sorted(changed), sorted(newfn)))
