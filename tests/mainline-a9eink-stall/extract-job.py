#!/usr/bin/env python3
"""Copy production job functions verbatim; never replace their control flow."""
import pathlib
import re
import sys
source = pathlib.Path(sys.argv[1]).read_text()
names = ('a9_prefill_set', 'a9_produce', 'a9_render_thread', 'a9_run_ring', 'a9_update')
parts = []
for name in names:
    m = re.search(r'^static (?:void|int) ' + name + r'\(', source, re.M)
    if not m:
        raise SystemExit('missing production function: ' + name)
    start = source.index('{', m.start())
    level = 1
    end = start + 1
    while level:
        level += (source[end] == '{') - (source[end] == '}')
        end += 1
    parts.append(source[m.start():end])
# A host pthread execution cannot prove ARM64 ordering. Also require the
# production message-passing primitives rather than silently testing volatile.
if 'smp_store_release(&a9->rjob, true)' not in parts[3]:
    raise SystemExit('production job publication lacks smp_store_release')
if 'smp_load_acquire(&a9->rjob)' not in parts[2]:
    raise SystemExit('production job acceptance lacks smp_load_acquire')
if parts[2].index('smp_load_acquire(&a9->rjob)') > parts[2].index('a9_produce(a9)'):
    raise SystemExit('production producer entered before acquire')
setting = re.search(r'^static unsigned int prefill_frames = .+;$', source, re.M)
if not setting:
    raise SystemExit('missing production prefill default')
if setting.group() != 'static unsigned int prefill_frames = 12;':
    raise SystemExit('Review 19 requires production prefill default 12')
prefill = re.search(r'^#define A9_PREFILL_MS .+$', source, re.M)
if not prefill:
    raise SystemExit('missing production prefill timeout')
pathlib.Path(sys.argv[2]).write_text('/* Verbatim production job functions. */\n' +
                                     setting.group() + '\n' + prefill.group() + '\n' + '\n\n'.join(parts[:4]) + '\n')
job_struct = re.search(r'^struct a9_job \{.*?^\};', source, re.M | re.S)
if not job_struct:
    raise SystemExit('missing production a9_job')
pathlib.Path(sys.argv[3]).write_text('/* Verbatim production policy wrapper. */\n' +
    job_struct.group() + '\n' + parts[4] + '\n')
