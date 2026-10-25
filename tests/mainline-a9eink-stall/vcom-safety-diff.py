#!/usr/bin/env python3
"""Diff proof for eink-settle-f6 (review 89d F6/F6b/F9/item 6): what changed besides log-only diagnostics.

usage: vcom-safety-diff.py <base-ref> [<new-ref|WORKTREE>] [--kernel <tree> <base-commit> <new-commit>]
       (base = eink-settle-fix ea763e3 = the 0054z driver; kernel: the regulator before/after patch 0057)

1. Driver files byte-identical except a9_eink_drv.c, a9_eink.h and a9_eink_transport.c (one new function).
2. Every protected rail/power/latch/ring function of a9_eink_drv.c identical. Changed only: a9_pg_settle,
   a9_pg_wf_end, rail_diag_show, a9_diag_wf_end (the full_n/full_low rect: 0052 F1 fix), __a9_update (one added
   HV-on accounting line). New: a9_hv_on_ms, a9_rate_ok (F9); transport: a9x_vblank (F6b).
3. a9_pg_settle: every added/removed line is a trace (F6b), vblank (F6b), ok_now (F6) or F9 rate line; its decision
   lines are unchanged: no removed line at all.
4. a9_pg_wf_end: the decision is still exactly a9_check_rail_sample(a9, true, 0, st); everything after it is
   behind `if (!a9->settle_ok_now) return false;` and only reads (fp9929a_read_vcom), counts and logs: no enable,
   latch, force_clear, power, aborts, gate or transport write.
5. --kernel: fp9929a.c: every function identical except the new fp9929a_read_vcom, which takes fp->lock, calls only
   i2c_smbus_read_byte_data, never a write, gpiod, the gate lock or a sleep.
Exit 0 = proof holds.
"""
import difflib, os, pathlib, re, subprocess, sys

os.chdir(subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], text=True).strip())
args = sys.argv[1:]
kern = None
if '--kernel' in args:
    i = args.index('--kernel'); kern = args[i + 1:i + 4]; args = args[:i]
base = args[0]
new = args[1] if len(args) > 1 else 'WORKTREE'
D = 'scripts/mainline/eink/driver/'
IDENT = [D + f for f in ['a9_eink_guard.c', 'a9_eink_ring.c', 'a9_eink_pattern.h', 'a9_eink_kms_test.c',
                         'a9_eink_engine.c', 'a9_eink_engine.h', 'a9_eink_damage.c', 'Kconfig', 'Makefile']] + \
        ['scripts/mainline/tcon/a9tcon.c', 'scripts/mainline/tcon/a9tcon.h']
PROTECTED = ['a9_session_frames', 'a9_hv_sample', 'a9_hv_good', 'a9_hv_is_on', 'a9_hv_maybe_on',
             'a9_tx_fault', 'a9_force_hv_off', 'a9_power_off', 'a9_rail_fault', 'a9_rail_report',
             'a9_check_live_rails', 'a9_check_rail_sample', 'a9_power_on', 'a9_run_frames', 'a9_run_ring',
             'a9_produce', 'a9_render_thread', 'a9_idle_timeout', 'a9_bin_admitted', 'status_show',
             'a9_temps', 'a9_clear_fault_set', 'a9_load_waveform', 'a9_diag_bucket', 'a9_diag_rail_sample',
             'a9_diag_pd_push', 'a9_diag_copy', 'a9_idle_clip', 'a9_hold_ms', 'a9_session_hold', 'a9_thread',
             'a9_take_job', 'a9_unprepare', 'a9_plane_atomic_update', 'damage_stats_show',
             'a9_pg_wf_end_low']
ALLOWED = {'a9_pg_settle', 'a9_pg_wf_end', 'rail_diag_show', 'a9_diag_wf_end', '__a9_update', 'a9_probe'}
NEW = {'a9_hv_on_ms', 'a9_rate_ok'}


def show(ref, path, repo=None):
    if ref == 'WORKTREE':
        return pathlib.Path(path).read_text()
    return subprocess.check_output(['git'] + (['-C', repo] if repo else []) + ['show', ref + ':' + path], text=True)


def func(src, name):
    m = re.search(r'^(?:static )?[^\n;#]*?\b' + name + r'\([^;]*?\)\s*\{', src, re.M | re.S)
    if not m:
        return None
    i = src.index('{', m.start())
    lvl, j = 1, i + 1
    while lvl:
        lvl += (src[j] == '{') - (src[j] == '}')
        j += 1
    return src[m.start():j]


def names(s):
    return set(re.findall(r'^(?:static )?(?!return)[a-z_][\w ]*?[ *]\**(\w+)\([^;]*?\)\s*\{', s, re.M))


def delta(a, b):
    d = [l for l in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm='', n=0)
         if l[:1] in '+-' and l[:3] not in ('+++', '---')]
    return [l[1:].strip() for l in d if l[0] == '-'], [l[1:].strip() for l in d if l[0] == '+']


fail = []
for f in IDENT:
    if show(base, f) != show(new, f):
        fail.append('file changed: ' + f)
bt, nt = show(base, D + 'a9_eink_transport.c'), show(new, D + 'a9_eink_transport.c')
for n in names(bt):
    if func(bt, n) != func(nt, n):
        fail.append('transport function changed: ' + n)
if names(nt) - names(bt) != {'a9x_vblank'}:
    fail.append('transport new functions: %r' % sorted(names(nt) - names(bt)))
vb = func(nt, 'a9x_vblank') or ''
for bad in ('spin_', 'WRITE_ONCE', 'vblank_get', 'vblank_put', '= true', '= false', 't->ms =', 't->vbl_ref ='):
    if bad in vb:
        fail.append('a9x_vblank does ' + bad)
# review 89e C2: the read is under t->lock and checks the modeset is still there
if vb.count('mutex_lock(&t->lock);') != 1 or vb.count('mutex_unlock(&t->lock);') != 1 or 't->vbl_ref && t->ms' not in vb:
    fail.append('a9x_vblank: not a t->lock-protected read with a modeset check (C2)')

bd, nd = show(base, D + 'a9_eink_drv.c'), show(new, D + 'a9_eink_drv.c')
for n in PROTECTED:
    if func(bd, n) != func(nd, n):
        fail.append('protected function changed: ' + n)
changed = {n for n in names(bd) & names(nd) if func(bd, n) != func(nd, n)}
if changed - ALLOWED:
    fail.append('unexpected changed functions: ' + repr(sorted(changed - ALLOWED)))
if names(nd) - names(bd) != NEW:
    fail.append('new driver functions: ' + repr(sorted(names(nd) - names(bd))))

# 3. a9_pg_settle: additions only, each a trace / vblank / ok_now / rate line
rem, add = delta(func(bd, 'a9_pg_settle'), func(nd, 'a9_pg_settle'))
if rem:
    fail.append('a9_pg_settle: lines removed: %r' % rem)
for l in add:
    if l in ('}', '{', '') or l.startswith('/*') or l.startswith('*'):
        continue
    if not re.search(r'settle_trace|vbl_|settle_ok_now|a9_rate_ok|settle_rate_skip|rate_tokens|spin_lock_irqsave|spin_unlock_irqrestore|return A9_SETTLE_NO;|if \(a9->settle_trace_n < 64\)', l):
        fail.append('a9_pg_settle: added line not trace/vblank/ok_now/rate: %r' % l)

# 4. a9_pg_wf_end
wf = func(nd, 'a9_pg_wf_end')
if wf.count('a9_check_rail_sample(a9, true, 0, st)') != 1:
    fail.append('a9_pg_wf_end: decision changed')
i_ok = wf.find('if (!a9->settle_ok_now)')
if i_ok < 0 or wf.find('lockdep_assert_not_held(&a9->eng_lock);') < i_ok or wf.find('fp9929a_read_vcom(') < i_ok:
    fail.append('a9_pg_wf_end: read not behind the recovered-dip check / lockdep assert')
tail = wf[i_ok:]
for bad in ('enable', 'a9x_rail_fault', 'force_clear', 'a9_power', 'aborts', 'a9_rail_fault', 'atomic_set',
            'WRITE_ONCE', 'a9x_', 'regulator_', 'mutex_'):
    if bad in tail:
        fail.append('a9_pg_wf_end: log-only tail touches ' + bad)
if 'A9_VCOM_READS_MAX' not in tail:
    fail.append('a9_pg_wf_end: no per-boot read cap')

# a9_diag_wf_end: only the full-rect source changed (a9->last.rect -> a9->e.rect) plus comments
rem, add = delta(func(bd, 'a9_diag_wf_end'), func(nd, 'a9_diag_wf_end'))
if [r.replace('a9->last.rect', 'a9->e.rect') for r in rem] != [a for a in add if not a.startswith('/*') and not a.startswith('*')]:
    fail.append('a9_diag_wf_end: changed beyond the rect source: -%r +%r' % (rem, add))
# a9_probe: the two F6 initialisers only
rem, add = delta(func(bd, 'a9_probe'), func(nd, 'a9_probe'))
if rem or add != ['a9->diag.vcom_last = -1;	/* F6: no read yet */', 'a9->diag.vcom_last_ms = -1;']:
    fail.append('a9_probe: -%r +%r' % (rem, add))
# __a9_update: one added line
rem, add = delta(func(bd, '__a9_update'), func(nd, '__a9_update'))
if rem or add != ['a9_hv_on_ms(a9);	/* F9: HV-on time seen after every real waveform */']:
    fail.append('__a9_update: -%r +%r' % (rem, add))
# the F9 helpers read transport state, write only their own fields
for n in NEW:
    body = func(nd, n)
    for bad in (r'a9x_\w+\(', r'\benable\b', 'force_clear', 'a9_power', 'atomic_set', 'mutex_', 'sleep', 'fp9929a_',
                r't->\w+ =', 'WRITE_ONCE'):
        if re.search(bad, body):
            fail.append('%s touches %s' % (n, bad))

# 5. the regulator
if kern:
    tree, kb, kn = kern
    rb, rn = show(kb, 'drivers/regulator/fp9929a.c', tree), show(kn, 'drivers/regulator/fp9929a.c', tree)
    for n in names(rb):
        if func(rb, n) != func(rn, n):
            fail.append('fp9929a function changed: ' + n)
    if names(rn) - names(rb) != {'fp9929a_read_vcom'}:
        fail.append('fp9929a new functions: %r' % sorted(names(rn) - names(rb)))
    rv = func(rn, 'fp9929a_read_vcom') or ''
    for bad in ('write', 'gpiod_', 'fp9929a_gate_lock', 'sleep', 'msleep', 'fp9929a_read_reg', 'WRITE_ONCE', 'atomic_'):
        if bad in rv:
            fail.append('fp9929a_read_vcom uses ' + bad)
    if rv.count('i2c_smbus_read_byte_data(') != 1 or 'mutex_lock(&fp->lock);' not in rv:
        fail.append('fp9929a_read_vcom: not one read under fp->lock')
    if 'EXPORT_SYMBOL_GPL(fp9929a_read_vcom);' not in rn:
        fail.append('fp9929a_read_vcom not exported')

if fail:
    print('SAFETY DIFF FAILED')
    for f in fail:
        print(' -', f)
    sys.exit(1)
print('vcom safety diff holds: %d files identical, %d protected functions identical, transport +a9x_vblank only,'
      ' changed %s, new %s%s' % (len(IDENT), len(PROTECTED), sorted(changed), sorted(names(nd) - names(bd)),
                                 '; fp9929a: only +fp9929a_read_vcom (read-only)' if kern else ''))
