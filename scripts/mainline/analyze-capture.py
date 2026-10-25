#!/usr/bin/env python3
"""Summarise a mainline first-boot capture from the Hisense A9.

Usage: python3 -I analyze-capture.py <dir|file> [--dtb <dtb>] [--all]

<dir> holds text files grabbed from the mainline initramfs (any may be missing):
  dmesg.txt progress.txt beacon.txt devices_deferred.txt interrupts.txt
  regulator_summary.txt clk_summary.txt drivers.txt devices.txt cmdline.txt
  modules.txt iomem.txt
If dmesg.txt is absent, mainline-dmesg.txt (first-boot.sh's raw ACM grab) is used.
A single file argument is treated as a raw combined grab (dmesg + progress + beacon).

--dtb parses the flattened DTB itself (stdlib, no dtc needed) and lists ENABLED
DT nodes with a compatible string that have no bound platform device.
Read-only, offline, always exits 0.
"""
import os
import re
import struct
import sys

LIMIT = 25          # max lines printed per section unless --all
ALL = False

PROMPT_RE = re.compile(r'^\s*(?:~|/[^#$]*)?\s*[#$]\s+')
ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')
DMESG_RE = re.compile(r'^(?:<\d+>)?\[\s*(\d+\.\d+)\]\s?(.*)$')


def clean(text):
    out = []
    for ln in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        ln = ANSI_RE.sub('', ln)
        ln = ''.join(c for c in ln if c == '\t' or c >= ' ')
        if PROMPT_RE.match(ln) and not DMESG_RE.match(ln):
            # shell prompt / echoed command line: drop it, but keep any text after
            # a prompt that turned out to be a dmesg line glued to it
            rest = PROMPT_RE.sub('', ln, count=1)
            m = re.search(r'(?:<\d+>)?\[\s*\d+\.\d+\].*', rest)
            if m:
                out.append(m.group(0))
            continue
        out.append(ln.rstrip())
    return out


def read(d, *names):
    for n in names:
        p = os.path.join(d, n)
        try:
            with open(p, 'rb') as f:
                return clean(f.read().decode('utf-8', 'replace'))
        except OSError:
            continue
    return None


def head(title):
    print('\n== ' + title + ' ' + '=' * max(3, 66 - len(title)))


def show(lines, indent='  ', limit=None):
    limit = LIMIT if limit is None else limit
    lines = list(lines)
    for ln in (lines if ALL else lines[:limit]):
        print(indent + ln)
    if not ALL and len(lines) > limit:
        print(indent + '... %d more (--all)' % (len(lines) - limit))


def uniq(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ---------------------------------------------------------------- dmesg
def parse_dmesg(lines):
    """Return [(time or None, msg)] for lines that look like kernel log."""
    out = []
    for ln in lines or []:
        m = DMESG_RE.match(ln.strip())
        if m:
            out.append((float(m.group(1)), m.group(2)))
        else:
            # dmesg embedded after other junk on the same line
            m = re.search(r'(?:<\d+>)?\[\s*(\d+\.\d+)\]\s?(.*)$', ln)
            if m:
                out.append((float(m.group(1)), m.group(2)))
    return out


def fmt(t, msg):
    return ('[%9.3f] ' % t if t is not None else '') + msg


# ---------------------------------------------------------------- DTB
def parse_dtb(path):
    """Minimal FDT parser -> list of (path, props dict)."""
    with open(path, 'rb') as f:
        b = f.read()
    magic, _tot, off_struct, off_str = struct.unpack('>IIII', b[:16])
    if magic != 0xd00dfeed:
        raise ValueError('not an FDT')
    def s(o):
        return b[off_str + o: b.index(b'\0', off_str + o)].decode('ascii', 'replace')
    nodes, stack, i = [], [], off_struct
    while True:
        tok = struct.unpack('>I', b[i:i + 4])[0]
        i += 4
        if tok == 1:                                   # BEGIN_NODE
            e = b.index(b'\0', i)
            name = b[i:e].decode('ascii', 'replace')
            i = (e + 4) & ~3
            p = '/' if not stack else (stack[-1][0].rstrip('/') + '/' + name)
            node = (p, {})
            stack.append(node)
            nodes.append(node)
        elif tok == 2:                                 # END_NODE
            stack.pop()
        elif tok == 3:                                 # PROP
            ln, no = struct.unpack('>II', b[i:i + 8])
            i += 8
            stack[-1][1][s(no)] = b[i:i + ln]
            i = (i + ln + 3) & ~3
        elif tok == 4:                                 # NOP
            continue
        elif tok == 9:                                 # END
            break
        else:
            raise ValueError('bad token %d' % tok)
    return nodes


def strlist(v):
    return [x.decode('ascii', 'replace') for x in v.split(b'\0') if x]


# compatibles that never become platform devices
NONDEV_COMPAT = re.compile(r'^(cache|arm,idle-state|domain-idle-state|operating-points-v2|'
                           r'arm,psci.*|qcom,kryo\d*|arm,cortex-a\d+)$')


def dt_enabled_compat(path):
    nodes = parse_dtb(path)
    disabled = set()
    out = []
    for p, props in nodes:
        st = props.get('status', b'okay').rstrip(b'\0')
        parent_off = any(p.startswith(d + '/') for d in disabled)
        if st not in (b'okay', b'ok') or parent_off:
            disabled.add(p)
            continue
        if 'compatible' in props and p != '/' and not NONDEV_COMPAT.match(
                strlist(props['compatible'])[0]):
            out.append((p, strlist(props['compatible']), props))
    return out


def dt_dev_names(p, props):
    """Candidate /sys/bus/platform/devices names for DT node p."""
    name = p.rsplit('/', 1)[-1]
    base, _, unit = name.partition('@')
    c = {name, base}
    if unit:
        u = unit.split(',')[0]
        c.add('%s.%s' % (u.lstrip('0') or '0', base))
        c.add('%s.%s' % (u, base))
    return c


# ---------------------------------------------------------------- sections
def sec_kernel(d, dm, raw):
    head('kernel')
    ver = [m for _, m in dm if m.startswith('Linux version')]
    print('  ' + (ver[0][:160] if ver else 'Linux version line: not found'))
    cl = read(d, 'cmdline.txt')
    cmd = None
    if cl:
        cmd = next((x for x in cl if x.strip()), None)
    if not cmd:
        km = [m for _, m in dm if m.startswith('Kernel command line:')]
        cmd = km[0].split(':', 1)[1].strip() if km else None
    print('  cmdline: ' + (cmd or 'not found'))
    if cmd and 'qcom_pon.beacon=' not in cmd:
        print('  !! cmdline has no qcom_pon.beacon=')
    mods = read(d, 'modules.txt')
    if mods is not None:
        mods = [m.split()[0] for m in mods if m.strip()]
        print('  modules: %d loaded%s' % (len(mods), (' (' + ' '.join(mods[:12]) + ')') if mods else ''))
    if dm:
        print('  dmesg: %d lines, last t=%.3f s' % (len(dm), max(t for t, _ in dm)))
    elif raw is not None:
        print('  !! no parseable dmesg lines (%d raw lines)' % len(raw))
    else:
        print('  !! no dmesg capture')


def sec_beacon(d, dm, raw):
    head('PON beacon')
    found = False
    for t, m in dm:
        mm = re.search(r'beacon: reboot reason was (\d+), now (\d+)', m)
        if mm:
            found = True
            x, y = int(mm.group(1)), int(mm.group(2))
            print('  ' + fmt(t, m))
            if x != 0:
                print('  !! boot chain left reason %d (expected 0 after "fastboot reboot"):'
                      ' a previous run reset after PON probe, or the ABL wrote it' % x)
            else:
                print('  ok: boot chain left reason 0; now armed with %d' % y)
        elif 'beacon:' in m:
            found = True
            print('  ' + fmt(t, m))
    b = read(d, 'beacon.txt')
    if b is None and raw:   # combined grab: a bare integer line is the beacon param
        b = [l for l in raw if re.match(r'^\s*\d+\s*$', l)][:1] or None
    if b:
        v = [x.strip() for x in b if x.strip()]
        print('  /sys/module/qcom_pon/parameters/beacon = ' + (v[-1] if v else '(empty)'))
        found = True
    if not found:
        print('  !! no beacon line (qcom-pon did not probe, or patch 0004 missing)')


def sec_progress(d, dm, raw=None):
    head('init progress (/tmp/a9-progress)')
    p = read(d, 'progress.txt')
    if p is None and raw:   # combined ACM grab: progress lines are not dmesg lines
        p = [l for l in raw if not DMESG_RE.match(l.strip()) and '[' not in l]
    rows = []
    for ln in p or []:
        mm = re.match(r'^\s*(\d+(?:\.\d+)?)\s+(.*\S)', ln)
        if mm:
            rows.append('%9s  %s' % (mm.group(1), mm.group(2)))
    if not rows:
        # fall back to "a9-init" style lines in dmesg (kmsg echo)
        rows = [fmt(t, m) for t, m in dm if re.search(r'\ba9[-_](init|progress)\b', m)]
    if rows:
        show(rows, limit=40)
    else:
        print('  (none)')


PROBE_PATS = [
    re.compile(p, re.I) for p in (
        r'probe .* failed with error', r'probe of \S+ failed', r'error -\d+',
        r'deferred probe pending', r'supply \S+ not found, using dummy',
        r'failed to get clk', r'-EPROBE_DEFER', r'timed out', r'timeout',
        r'sync_state\(\) pending due to', r'\bfailed\b')
]


def sec_probe(dm):
    head('probe failures / errors')
    hits = [fmt(t, m) for t, m in dm if any(p.search(m) for p in PROBE_PATS)]
    # collapse identical messages (keep first timestamp, count)
    counts, order = {}, []
    for t, m in dm:
        if any(p.search(m) for p in PROBE_PATS):
            if m not in counts:
                counts[m] = [t, 0]
                order.append(m)
            counts[m][1] += 1
    rows = [fmt(counts[m][0], m) + ('  (x%d)' % counts[m][1] if counts[m][1] > 1 else '')
            for m in order]
    dummy = [m for m in order if 'using dummy regulator' in m]
    rows = [r for r in rows if 'using dummy regulator' not in r]
    show(rows or ['(none)'], limit=40)
    if dummy:
        print('  dummy regulators (%d):' % len(dummy))
        show(sorted(set(re.sub(r'^\s*', '', m) for m in dummy)), indent='    ')
    return hits


def sec_deferred(d, dm):
    head('devices_deferred')
    lines = read(d, 'devices_deferred.txt')
    rows = []
    for ln in lines or []:
        if not ln.strip():
            continue
        dev, _, reason = ln.partition('\t')
        if not reason:
            parts = ln.split(None, 1)
            dev, reason = parts[0], (parts[1] if len(parts) > 1 else '')
        rows.append('%-40s %s' % (dev.strip(), reason.strip() or '(no reason given)'))
    if lines is None:
        rows = ['%s' % m for _, m in dm if 'deferred probe pending' in m]
        print('  (no devices_deferred.txt; from dmesg)')
    show(rows or ['(none)'], limit=40)


def load_devices(d):
    lines = read(d, 'devices.txt')
    if lines is None:
        return None
    devs = {}
    for ln in lines:
        parts = ln.split()
        if not parts or '/' in parts[0] and len(parts) == 1:
            continue
        dev = parts[0]
        drv = parts[1] if len(parts) > 1 else '-'
        drv = drv.rsplit('/', 1)[-1] if drv != '-' else '-'
        devs[dev] = drv
    return devs


def load_drivers(d):
    lines = read(d, 'drivers.txt')
    if lines is None:
        return None
    drv, cur = {}, None
    for ln in lines:
        mm = re.match(r'^(?:/sys/bus/platform/drivers/)?(\S+):$', ln.strip())
        if mm:
            cur = mm.group(1).rsplit('/', 1)[-1]
            drv.setdefault(cur, [])
        elif cur and ln.strip():
            drv[cur].extend(x for x in ln.split()
                            if x not in ('bind', 'unbind', 'uevent', 'module', 'new_id', 'remove_id'))
    return drv


def sec_unbound(d, dtb):
    head('platform devices without a driver')
    devs = load_devices(d)
    drivers = load_drivers(d)
    if devs is None:
        print('  (no devices.txt)')
    else:
        bound = sum(1 for v in devs.values() if v != '-')
        print('  %d platform devices, %d bound, %d unbound' % (len(devs), bound, len(devs) - bound))
        groups = {}
        for dev, drv in sorted(devs.items()):
            if drv != '-':
                continue
            name = dev.split('.', 1)[1] if re.match(r'^[0-9a-f]+\.', dev) else dev
            key = re.sub(r'[-_@:]?\d+$', '', name) or name
            groups.setdefault(key, []).append(dev)
        rows = ['%-28s %s' % (k, ' '.join(v)) for k, v in sorted(groups.items())]
        show(rows or ['(none)'], limit=40)
    if drivers is not None:
        empty = sorted(k for k, v in drivers.items() if not v)
        print('  registered platform drivers: %d (%d with no device bound)' % (len(drivers), len(empty)))
    if not dtb:
        return
    head('enabled DT nodes with no bound device (--dtb)')
    try:
        nodes = dt_enabled_compat(dtb)
    except Exception as e:  # noqa: BLE001
        print('  !! cannot parse %s: %s' % (dtb, e))
        return
    if devs is None:
        print('  %d enabled compatible nodes; no devices.txt to compare' % len(nodes))
        return
    # bus-attached children (i2c/spi/dsi/usb) are not platform devices: mark them
    skip_parent = re.compile(r'/(i2c|spi|dsi|usb|mdio|ethernet-phy|port|ports|endpoint)[@/]')
    rows, nonplat = [], []
    for p, compat, props in nodes:
        cands = dt_dev_names(p, props)
        name = p.rsplit('/', 1)[-1]
        hit = [c for c in cands if c in devs] + [k for k in devs if k.endswith(':' + name)]
        if hit and any(devs[c] != '-' for c in hit):
            continue
        state = 'present, unbound' if hit else 'no device'
        row = '%-55s %-18s %s' % (p, state, compat[0])
        parent = p.rsplit('/', 1)[0]
        if not hit and (skip_parent.search(p.rsplit('/', 1)[0] + '/') or re.search(
                r'(i2c|spi|dsi|usb)@', parent.rsplit('/', 1)[-1])):
            nonplat.append(row)
        else:
            rows.append(row)
    rows.sort(key=lambda r: 'present, unbound' not in r)
    print('  %d enabled compatible nodes; %d without a bound platform device'
          ' ("present, unbound" first; "no device" = never created)' % (len(nodes), len(rows)))
    show(rows or ['(none)'], limit=60)
    if nonplat:
        print('  children of i2c/spi/dsi/usb buses (not platform devices; check dmesg):')
        show(nonplat, indent='    ', limit=20)


def chain(title, dm, devs, items):
    head(title)
    for label, dmesg_re, dev_re in items:
        r = re.compile(dmesg_re, re.I)
        lines = [fmt(t, m) for t, m in dm if r.search(m)]
        bind = ''
        if devs is not None and dev_re:
            dr = re.compile(dev_re, re.I)
            m = ['%s->%s' % (k, v) for k, v in sorted(devs.items()) if dr.search(k)]
            bind = ' [dev: %s]' % (', '.join(m) if m else 'none')
        bad = [l for l in lines if re.search(r'fail|error|-\d{1,3}\b|timed? ?out|defer|not found', l, re.I)]
        tag = 'ERR ' if bad else ('ok  ' if lines else '--  ')
        print('  %s%-18s %d dmesg lines%s' % (tag, label, len(lines), bind))
        show((bad or lines)[:6], indent='        ', limit=6)


def sec_usb(dm, devs):
    chain('USB chain', dm, devs, [
        ('qusb2 phy', r'qusb2|qcom-qusb2|hsphy|1613000\.phy', r'^1613000\.phy$'),
        ('dwc3-qcom', r'dwc3-qcom|dwc3_qcom|dwc3-qcom-legacy|4ef8800\.usb', r'^4ef8800\.usb$'),
        ('dwc3 core', r'^dwc3 |\bdwc3:|^platform 4e00000\.usb', r'^4e00000\.(usb|dwc3)$'),
        ('udc', r'\budc\b|UDC core|/sys/class/udc', None),
        ('gadget', r'gadget|configfs|g_|f_acm|f_ncm|usb0|ncm|acm', None),
    ])


def sec_eink(dm, devs):
    chain('E Ink chain', dm, devs, [
        ('fp9929a (i2c)', r'fp9929|fitipower|0-0048\b', None),
        ('tc358767 (i2c)', r'tc358767|0-000f\b', None),
        ('msm/mdss', r'\bmsm\b|mdss|dpu|msm_dpu|drm', r'mdss|5e00000|5e01000'),
        ('dsi', r'dsi', r'5e94000|dsi'),
        ('panel-hisense', r'panel|hisense|eink', r'eink|panel'),
    ])


REG_RE = re.compile(r'^(\s*)(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(-?\d+)mV(?:\s+(-?\d+)mA)?'
                    r'(?:\s+(-?\d+)mV\s+(-?\d+)mV)?')


def sec_regulators(d):
    head('regulators')
    lines = read(d, 'regulator_summary.txt')
    if lines is None:
        print('  (no regulator_summary.txt)')
        return
    want = re.compile(r'(^|[-_])l(4|9|12|15|18)a?$|^pm6125[-_]l(4|9|12|15|18)$', re.I)
    rows, fixed, n_on, n = [], [], 0, 0
    for ln in lines:
        m = REG_RE.match(ln)
        if not m:
            continue
        indent, name, use, open_, bypass, mode, mv = m.group(1, 2, 3, 4, 5, 6, 7)
        # rails supplied by another regulator are indented too (the PM6125
        # LDOs sit under regulator-dummy); consumer rows are "<dev>-<supply>"
        if re.match(r'^[0-9a-f]+\.[^ ]*-', name) or name.startswith(('i2c-', 'spi')):
            continue
        n += 1
        on = int(use) > 0
        n_on += on
        mn, mx = m.group(9), m.group(10)
        rng = ' (%s..%s mV)' % (mn, mx) if mn else ''
        row = '%-28s %-3s use=%s open=%s %6s mV%s' % (name, 'ON' if on else 'off', use, open_, mv, rng)
        if want.search(name):
            rows.append(row)
        elif re.search(r'fixed|vreg|vdd|vcc|regulator', name, re.I) and not re.match(r'^(pm|smb)', name):
            fixed.append(row)
    print('  %d regulators parsed, %d with use>0' % (n, n_on))
    print('  l4/l9/l12/l15/l18:')
    show(rows or ['(not found)'], indent='    ')
    print('  fixed / board regulators:')
    show(fixed or ['(none)'], indent='    ')


CLK_RE = re.compile(r'^(\s*)(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\b')


def sec_clocks(d):
    head('clocks')
    lines = read(d, 'clk_summary.txt')
    if lines is None:
        print('  (no clk_summary.txt)')
        return
    n = en = 0
    zero, orphan = [], []
    roots = set()
    for ln in lines:
        m = CLK_RE.match(ln)
        if not m:
            continue
        indent, name, ecnt, pcnt, _prot, rate = m.group(1, 2, 3, 4, 5, 6)
        n += 1
        on = int(ecnt) > 0
        en += on
        depth = len(indent.expandtabs(8))
        if depth <= 1:
            roots.add(name)
        if on and int(rate) == 0:
            zero.append('%s (enable=%s)' % (name, ecnt))
        # orphan heuristic: top-level clock that is not a known root/xo source
        if depth <= 1 and on and not re.search(r'xo|sleep|bi_tcxo|ref|pll|osc|fixed|rpmcc|clk32|board', name, re.I):
            orphan.append(name)
    print('  %d clocks parsed, %d enabled' % (n, en))
    print('  enabled with rate 0:')
    show(zero or ['(none)'], indent='    ')
    print('  enabled top-level clocks that do not look like a root (possible orphans):')
    show(orphan or ['(none)'], indent='    ')
    if any('orphan' in l.lower() for l in lines):
        show([l.strip() for l in lines if 'orphan' in l.lower()], indent='    ')


def sec_irqs(d):
    head('interrupts (top 15)')
    lines = read(d, 'interrupts.txt')
    if lines is None:
        print('  (no interrupts.txt)')
        return
    ncpu = 0
    rows = []
    for ln in lines:
        if re.match(r'^\s*CPU\d', ln):
            ncpu = len(ln.split())
            continue
        m = re.match(r'^\s*(\S+):\s+(.*)$', ln)
        if not m:
            continue
        irq, rest = m.groups()
        f = rest.split()
        k = ncpu or len([x for x in f if x.isdigit()])
        cnts, desc = [], []
        for i, x in enumerate(f):
            if i < k and x.isdigit():
                cnts.append(int(x))
            else:
                desc = f[i:]
                break
        if not cnts:
            continue
        if irq in ('Err', 'MIS'):
            continue
        rows.append((sum(cnts), irq, ' '.join(desc)))
    rows.sort(reverse=True)
    show(['%10d  %-6s %s' % r for r in rows[:15]] or ['(none)'])
    zero = [r for r in rows if r[0] == 0]
    if zero:
        print('  %d lines with count 0, e.g.: %s' % (len(zero), ', '.join(
            (r[2].split()[-1] if r[2] else r[1]) for r in zero[:10])))


TRACE_RE = re.compile(r'(WARNING:|------------\[ cut here|BUG:|Oops|Kernel panic|Call trace:|'
                      r'Unable to handle|Internal error|watchdog: BUG|rcu: INFO|hung_task|SError)')


def sec_traces(dm):
    head('WARN / BUG / Oops')
    out, i = [], 0
    while i < len(dm):
        t, m = dm[i]
        if TRACE_RE.search(m) and 'cut here' not in m:
            snip = [fmt(t, m)]
            j = i + 1
            while j < len(dm) and len(snip) < 6:
                tm = dm[j][1]
                if re.search(r'^(pc|lr|Modules linked|CPU:|Hardware name|Call trace:|\s+\S+\+0x)', tm):
                    snip.append('   ' + tm.strip())
                elif TRACE_RE.search(tm):
                    break
                j += 1
            out.append(snip)
            i = j
            continue
        i += 1
    if not out:
        print('  (none)')
        return
    print('  %d event(s)' % len(out))
    for s in (out if ALL else out[:8]):
        show(s, limit=6)
    if not ALL and len(out) > 8:
        print('  ... %d more (--all)' % (len(out) - 8))


def sec_iomem(d):
    lines = read(d, 'iomem.txt')
    if not lines:
        return
    head('iomem')
    ram = [l.strip() for l in lines if 'System RAM' in l and not l.startswith(' ')]
    print('  System RAM ranges: %d%s' % (len(ram), (' (' + '; '.join(r.split()[0] for r in ram[:4]) + ')') if ram else ''))
    print('  %d top-level regions' % sum(1 for l in lines if re.match(r'^[0-9a-f]+-[0-9a-f]+ :', l)))


def main(argv):
    global ALL
    args, dtb, i = [], None, 1
    while i < len(argv):
        a = argv[i]
        if a == '--dtb' and i + 1 < len(argv):
            dtb = argv[i + 1]
            i += 2
            continue
        if a == '--all':
            ALL = True
        elif a in ('-h', '--help'):
            print(__doc__)
            return 0
        else:
            args.append(a)
        i += 1
    if not args:
        print(__doc__)
        return 0
    src = args[0]
    if os.path.isfile(src):
        d = os.path.dirname(os.path.abspath(src))
        with open(src, 'rb') as f:
            raw = clean(f.read().decode('utf-8', 'replace'))
    elif os.path.isdir(src):
        d = src
        raw = read(d, 'dmesg.txt', 'mainline-dmesg.txt')
    else:
        print('!! %s: not found' % src)
        return 0
    if dtb and not os.path.isfile(dtb):
        print('!! --dtb %s: not found, skipping DT cross-check' % dtb)
        dtb = None
    print('capture: ' + os.path.abspath(src))
    present = [n for n in ('dmesg.txt', 'progress.txt', 'beacon.txt', 'devices_deferred.txt',
                           'interrupts.txt', 'regulator_summary.txt', 'clk_summary.txt',
                           'drivers.txt', 'devices.txt', 'cmdline.txt', 'modules.txt', 'iomem.txt')
               if os.path.isfile(os.path.join(d, n))]
    print('files: ' + (' '.join(present) or '(none of the expected names)'))
    dm = parse_dmesg(raw)
    devs = load_devices(d)
    steps = [
        lambda: sec_kernel(d, dm, raw), lambda: sec_beacon(d, dm, raw), lambda: sec_progress(d, dm, raw),
        lambda: sec_probe(dm), lambda: sec_deferred(d, dm), lambda: sec_unbound(d, dtb),
        lambda: sec_usb(dm, devs), lambda: sec_eink(dm, devs), lambda: sec_regulators(d),
        lambda: sec_clocks(d), lambda: sec_irqs(d), lambda: sec_traces(dm), lambda: sec_iomem(d),
    ]
    for s in steps:
        try:
            s()
        except Exception as e:  # noqa: BLE001 -- never die on garbled input
            print('  !! section failed: %s: %s' % (type(e).__name__, e))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv))
    except Exception as e:  # noqa: BLE001
        print('!! analyze-capture: %s: %s' % (type(e).__name__, e))
        sys.exit(0)
