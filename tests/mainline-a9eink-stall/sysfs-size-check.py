#!/usr/bin/env python3
"""Worst-case rendered length of the eink sysfs attributes (status, rail_diag) from the format strings in
a9_eink_drv.c. Every conversion is charged its maximum width (u64/s64 20 chars + sign, int 11, u32 10, %#x 10).
sysfs gives one page (4096 B): a longer attribute is silently truncated. usage: sysfs-size-check.py a9_eink_drv.c"""
import re, sys

src = open(sys.argv[1]).read()
W = {'llu': 20, 'lld': 20, 'llx': 16, 'u': 10, 'd': 11, 'x': 8, '#x': 10, 'lu': 20, 'ld': 20, 's': 24}


def lit(call_start, fn=None):
    """the concatenated string literal following call_start (searched inside function fn)"""
    i = src.index(call_start, src.index('static ssize_t %s(' % fn) if fn else 0)
    j = src.index('"', i)
    out, depth = [], 0
    k = j
    while True:
        if src[k] == '"':
            e = k + 1
            while src[e] != '"' or src[e - 1] == '\\':
                e += 1
            out.append(src[k + 1:e]); k = e + 1
        elif src[k] in ' \t\n':
            k += 1
        else:
            break
    return ''.join(out)


def width(fmt):
    fmt = fmt.encode().decode('unicode_escape')
    n = 0
    pos = 0
    for m in re.finditer(r'%(#?)(ll|l)?([udxs])', fmt):
        n += m.start() - pos
        key = (m.group(2) or '') + m.group(3)
        key = '#x' if (m.group(1) and m.group(3) == 'x') else key
        n += W[key]
        pos = m.end()
    return n + len(fmt) - pos


def func_body(name):
    i = src.index('static ssize_t %s(' % name)
    return src[i:src.index('\n}\n', i)]


status = width(lit('return sysfs_emit(buf,', 'status_show'))
body = func_body('rail_diag_show')
head = width(lit('n = sysfs_emit(buf,', 'rail_diag_show'))
lits = [''.join(re.findall(r'"([^"]*)"', m.group(1)))
        for m in re.finditer(r'sysfs_emit_at\(buf, n, ((?:"[^"]*"\s*)+),', body)]
# once-only lines (settle, F9 diag_rate, F6 diag_vcom, the pd_total header), then the per-entry line (A9_DIAG_PD times)
assert len(lits) >= 3 and lits[-1].startswith('pd %u') and lits[-2].startswith('pd_total'), lits
n_pd = int(re.search(r'#define A9_DIAG_PD (\d+)', open(sys.argv[1].replace('a9_eink_drv.c', 'a9_eink.h')).read()).group(1))
rail = head + sum(width(x) for x in lits[:-1]) + n_pd * width(lits[-1])
print('status worst case %d B, rail_diag worst case %d B (limit 4096)' % (status, rail))
LIM = 3584      # keep 512 B of margin under the page
bad = [n for n, v in (('status', status), ('rail_diag', rail)) if v > LIM]
if bad:
    print('TOO LONG (> %d): %s' % (LIM, bad)); sys.exit(1)
