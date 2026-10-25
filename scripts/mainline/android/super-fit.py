#!/usr/bin/env python3
"""super-fit.py <lpdump.txt> <partition> <new_size_bytes>

Does a logical partition of <new_size_bytes> fit in super in place of the
current one? Reads host `lpdump super.bin` output (golden dump). Checks the
partition's group limit and the device's free space (sectors not used by any
other partition). Exit 0 = fits. Used by android-boot.sh --offline.
"""
import re
import sys

txt = open(sys.argv[1]).read()
part, new = sys.argv[2], int(sys.argv[3])
SECT = 512
parts, groups, cur = {}, {}, None
for blk in txt.split('------------------------'):
    m = re.search(r'Name: (\S+)\s+Group: (\S+).*?Extents:(.*)', blk, re.S)
    if m:
        n = sum(int(b) - int(a) + 1 for a, b in re.findall(r'(\d+) \.\. (\d+) linear', m.group(3)))
        parts[m.group(1)] = (m.group(2), n * SECT)
        continue
    g = re.search(r'Name: (\S+)\s+Maximum size: (\d+) bytes', blk)
    if g:
        groups[g.group(1)] = int(g.group(2))
dev = re.search(r'Partition name: super\s+First sector: (\d+)\s+Size: (\d+) bytes', txt)
if part not in parts or not dev:
    sys.exit(f'cannot parse {part} / block device from {sys.argv[1]}')
first, size = int(dev.group(1)), int(dev.group(2))
grp, old = parts[part]
new_al = (new + 1048575) // 1048576 * 1048576          # 1 MiB alignment slack
others = sum(s for p, (g, s) in parts.items() if p != part)
in_grp = sum(s for p, (g, s) in parts.items() if g == grp and p != part)
free_dev = size - first * SECT - others
gmax = groups.get(grp, 0)
print(f'{part}: old {old} B, new {new} B (aligned {new_al}); device free for it {free_dev} B; '
      f'group {grp} used by others {in_grp} of {gmax or "unlimited"}')
ok = new_al <= free_dev and (gmax == 0 or in_grp + new_al <= gmax)
sys.exit(0 if ok else 1)
