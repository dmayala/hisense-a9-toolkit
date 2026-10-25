#!/usr/bin/env python3
"""Print selected DTBO nodes with overlay fixups resolved to base-DT labels.
usage: python3 -I dtbo-resolve.py dtbo.dts 'fragment@20' ['fragment@6' ...]
Input is the dtc -I dtb -O dts output of the stock dtbo. Each 0xffffffff phandle
cell is replaced by &label from __fixups__ (path:property:offset)."""
import re, sys

s = open(sys.argv[1]).read()
fx = s[s.find('\t__fixups__ {'):]
fix = {}
for label, refs in re.findall(r'\t\t(\S+) = ((?:"[^"]+"(?:, )?)+);', fx):
    for r in re.findall(r'"([^"]+)"', refs):
        path, prop, off = r.rsplit(':', 2)
        fix.setdefault((path, prop), {})[int(off)] = label
lines = s.split('\n')
stack = []
want = sys.argv[2:]
for l in lines:
    t = l.strip()
    if t.endswith('{'):
        stack.append(t[:-1].strip())
    elif t == '};':
        stack.pop() if stack else None
        continue
    path = '/' + '/'.join(stack[1:])
    if not any(path.startswith('/' + w) for w in want):
        continue
    if '=' in t and not t.endswith('{'):
        prop = t.split('=')[0].strip()
        refs = fix.get((path, prop))
        if refs:
            m = re.match(r'(.*?<)(.*)(>.*)', t)
            cells = m.group(2).split()
            for off, lab in refs.items():
                if off // 4 < len(cells):
                    cells[off // 4] = '&' + lab
            t = m.group(1) + ' '.join(cells) + m.group(3)
    print('  ' * (len(stack) - 1) + t if not t.endswith('{') else '  ' * (len(stack) - 2) + t)
