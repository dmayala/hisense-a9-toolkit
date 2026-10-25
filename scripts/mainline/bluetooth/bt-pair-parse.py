#!/usr/bin/env python3
"""Value-free parsers for bt-pair.sh (runs/bt-phase2-plan.md).

Every command reads one PRIVATE capture (raw phone output, may hold addresses
and neighbour names) and prints only key=value lines built from counts,
states and redacted text. Nothing from the capture is printed unredacted.

  redact                     stdin -> stdout, addresses and 12-hex runs masked
  leak  <bdaddr-file> <f..>  exit 1 if any file holds an address or the private one
  dmesg <file>               BT power-on sequences, -84 rule (review 43 s2), stop lines
  btdump <file>              state, crash count, bonded count, A2DP/HFP connected, profiles
  input <file>               Bluetooth (bus 0x0005) input device paths
  ui <file> find <name>      center of the node whose text is <name> (case-insensitive)
  ui <file> dialog           pairing dialog: kind, passkey, button centers
  ui <file> gear <name>      center of the details button on <name>'s row
  ui <file> text <regex>     center of the first node whose text matches <regex>
                             (find/text/gear: com.android.settings nodes only, tap point inside the screen)
  focus <file>               dumpsys window focus: package, window, ok=1 only for Settings Bluetooth windows
  getevent <file>            counts of EV_KEY / EV_REL / EV_ABS records
"""
import re
import sys
import xml.etree.ElementTree as ET

ADDR = re.compile(r'(?i)\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b')
ANON = re.compile(r'(?i)\b(?:XX:){4}[0-9a-f]{2}:[0-9a-f]{2}\b')
HEX12 = re.compile(r'(?i)\b[0-9a-f]{12}\b')


def redact(s):
    s = ADDR.sub('<addr>', s)
    s = ANON.sub('<addr>', s)
    return HEX12.sub('<hex12>', s)


def out(k, v):
    print(f'{k}={redact(str(v))}')


def read(p):
    with open(p, errors='replace') as f:
        return f.read()


# ---------------------------------------------------------------- leak check
def addr_forms(text):
    """All spellings of the private address that must never appear."""
    a = text.strip().upper().replace('-', ':')
    if not re.fullmatch(r'(?:[0-9A-F]{2}:){5}[0-9A-F]{2}', a):
        return []
    b = a.split(':')
    forms = set()
    for oc in (b, b[::-1]):
        for sep in (':', '-', '', ' '):
            s = sep.join(oc)
            forms |= {s, s.lower()}
    return sorted(forms)


def cmd_leak(bdfile, files):
    forms = []
    try:
        forms = addr_forms(read(bdfile))
    except OSError:
        print('leak: private address file absent; generic patterns only')
    bad = 0
    for p in files:
        t = read(p)
        if ADDR.search(t) or ANON.search(t):
            print(f'leak: address pattern in {p}'); bad = 1
        if any(f in t for f in forms):
            print(f'leak: PRIVATE address in {p}'); bad = 1
    print('leak: none' if not bad else 'leak: FOUND')
    return bad


# --------------------------------------------------------------------- dmesg
TS = re.compile(r'^\s*\[\s*(\d+\.\d+)\]\s?(.*)$')
BT_LINE = re.compile(r'(?i)^(bluetooth:|hci_uart|btqca|qca[ _])')
BAD_BT = re.compile(r'(?i)fail|error|timeout|timed out|fallback|unexpected|\bibs\b|-1?\d+\)$|not found|unable')
REG = re.compile(r'(?i)^(regulator|vreg|qcom-rpm|qcom_rpm|rpm-smd|pm6125|pmi632)\S*|regulator')
REG_BAD = re.compile(r'(?i)fail|error|over.?current|\bocp\b|short|unbalanced|underflow')
SER = re.compile(r'(?i)geni|ttyhs|4a8c000|serial')
SER_BAD = re.compile(r'(?i)error|fail|overrun|timeout|parity|break')
SVC = re.compile(r"(?i)^init: .*service '(vendor\.a9-bt-hal|vendor\.a9-bt-setup|audioserver|zygote\S*|system_server|bluetooth\S*)'"
                 r".*(crashed|killed by signal (6|11)|restarting)")
RPROC = re.compile(r'(?i)^remoteproc\S*: .*(is now up|powering up|booting|start)')
THERM = re.compile(r'(?i)critical temperature|thermal.*(shutdown|emergency)|overheat')


def cmd_dmesg(path):
    seqs, setups, products, done, bad = None, 0, 0, 0, []
    admitted = 0
    for raw in read(path).splitlines():
        m = TS.match(raw)
        if not m:
            continue
        t, msg = float(m.group(1)), m.group(2)
        if msg.startswith('Kernel command line'):
            continue
        if BT_LINE.match(msg):
            if 'setting up wcn399x' in msg:
                setups += 1
                seqs = {'t0': t, 'product': False, 'n84': 0}
                continue
            if 'Frame reassembly failed' in msg:
                ok = (seqs is not None and '(-84)' in msg and not seqs['product']
                      and seqs['n84'] == 0 and 0 <= t - seqs['t0'] <= 0.150)
                if ok:
                    seqs['n84'] = 1; admitted += 1
                else:
                    bad.append(f'[{t:.6f}] framing error outside the admitted -84 window')
                continue
            if 'QCA Product ID' in msg:
                products += 1
                if seqs is not None:
                    seqs['product'] = True
                continue
            if 'setup on UART is completed' in msg:
                done += 1
                continue
            if 'Retry BT power ON' in msg or BAD_BT.search(msg):
                bad.append(f'[{t:.6f}] {msg[:150]}')
            continue
        if REG.search(msg) and REG_BAD.search(msg) and not msg.startswith('init:'):
            bad.append(f'[{t:.6f}] rail: {msg[:150]}')
        elif SER.search(msg) and SER_BAD.search(msg) and not msg.startswith('init:'):
            bad.append(f'[{t:.6f}] serial: {msg[:150]}')
        elif SVC.search(msg):
            bad.append(f'[{t:.6f}] restart: {msg[:150]}')
        elif RPROC.search(msg):
            bad.append(f'[{t:.6f}] modem/remoteproc: {msg[:150]}')
        elif THERM.search(msg):
            bad.append(f'[{t:.6f}] thermal: {msg[:150]}')
    out('setups', setups)
    out('products', products)
    out('completed', done)
    out('admitted84', admitted)
    for b in bad:
        out('bad', b)


# ------------------------------------------------------------ dumpsys parsers
def cmd_btdump(path):
    t = read(path)
    lines = t.splitlines()
    g = lambda rx, d='?': (re.search(rx, t, re.M) or [None, d])[1]
    out('state', g(r'^\s+state:\s*(\S+)'))
    out('crashed', g(r'^Bluetooth crashed (\d+) times'))
    out('bonded', g(r'^\s+Bonded devices:\s*(\d+)'))
    out('bond_events', g(r'^\s+Total Number of events:\s*(\d+)'))
    sect, prof = None, None
    a2dp = hfp = '?'
    profiles = []
    for ln in lines:
        s = ln.strip()
        if ln and not ln.startswith(' '):
            sect = s
            prof = None
            continue
        if sect == 'BluetoothActiveDeviceManager':
            if s in ('A2DP:', 'HFP:'):
                prof = s[:-1]
            elif s.startswith('Connected:') and prof:
                v = s.split(':', 1)[1].strip()
                if prof == 'A2DP':
                    a2dp = v
                else:
                    hfp = v
                prof = None
        elif sect == 'Enabled Profile Services:' and re.fullmatch(r'[A-Z0-9_]+', s):
            profiles.append(s)
    out('a2dp_connected', a2dp)
    out('hfp_connected', hfp)
    out('profiles', ','.join(profiles))


def cmd_input(path):
    """dumpsys input: Event Hub devices with bus=0x0005 (Bluetooth)."""
    cur, n = None, 0
    for ln in read(path).splitlines():
        if re.match(r'^\s+-?\d+: ', ln):
            cur = None
        m = re.match(r'^\s+Path: (/dev/input/event\d+)\s*$', ln)
        if m:
            cur = m.group(1)
        if re.search(r'Identifier: bus=0x0005\b', ln) and cur:
            out('bt_input', cur); n += 1; cur = None
    out('bt_inputs', n)


# ------------------------------------------------------------------ UI parser
def nodes(path):
    try:
        root = ET.fromstring(read(path))
    except ET.ParseError:
        return []
    res = []
    for n in root.iter('node'):
        b = re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', n.get('bounds', ''))
        if not b:
            continue
        x1, y1, x2, y2 = map(int, b.groups())
        res.append({'text': (n.get('text') or '').strip(), 'id': n.get('resource-id') or '',
                    'cls': n.get('class') or '', 'pkg': n.get('package') or '',
                    'desc': (n.get('content-desc') or '').strip(),
                    'cx': (x1 + x2) // 2, 'cy': (y1 + y2) // 2, 'y1': y1, 'y2': y2, 'x2': x2})
    return res


SETTINGS = 'com.android.settings'
# Settings windows a tap may go to (review 55 B1): the Bluetooth pages, the connected-devices
# dashboard, SubSettings (device details, Forget confirmation) and the pairing dialog.
FOCUS_OK = re.compile(r'com\.android\.settings/\S*(Bluetooth|BlueTooth|ConnectedDevice|SubSettings|PairingDialog)\S*')


def cmd_focus(path):
    t = read(path)
    m = re.search(r'mCurrentFocus=Window\{\S+ \S+ ([^}\s]+)\}', t)
    win = m.group(1) if m else ''
    pkg = win.split('/')[0] if '/' in win else (win or 'none')
    out('focus_pkg', pkg)
    out('focus_win', win.split('/')[-1] if win else 'none')
    out('focus_ok', 1 if FOCUS_OK.fullmatch(win) else 0)


def cmd_ui(path, what, arg=None):
    ns = nodes(path)
    out('ui_nodes', len(ns))
    W = max([n['x2'] for n in ns] + [0])
    H = max([n['y2'] for n in ns] + [0])
    inside = lambda n: 0 < n['cx'] < W and 0 < n['cy'] < H
    st = [n for n in ns if n['pkg'] == SETTINGS and inside(n)]
    tap = lambda n: out('tap', f"{n['cx']} {n['cy']}")
    if what == 'find':
        for n in st:
            if n['text'].casefold() == arg.strip().casefold():
                tap(n); return
        out('tap', 'none')
    elif what == 'text':
        rx = re.compile(arg, re.I)
        for n in st:
            if n['text'] and rx.fullmatch(n['text']):
                tap(n); return
        out('tap', 'none')
    elif what == 'gear':
        row = [n for n in st if n['text'].casefold() == arg.strip().casefold()]
        gears = [n for n in st if n['id'].endswith(':id/settings_button')
                 or n['desc'].casefold() in ('settings', 'device details', 'details')]
        if not row or not gears:
            out('tap', 'none'); return
        r = row[0]
        g = min(gears, key=lambda n: abs(n['cy'] - r['cy']))
        if abs(g['cy'] - r['cy']) <= 120:
            tap(g)
        else:
            out('tap', 'none')
    elif what == 'dialog':
        pk = [n for n in ns if n['pkg'] in (SETTINGS, 'android', 'com.android.bluetooth') and inside(n)]
        texts = ' '.join(n['text'] for n in pk)
        b1 = [n for n in pk if n['id'] == 'android:id/button1']
        deny = [n for n in pk if re.fullmatch(r"(?i)don.t allow|deny", n['text'])]
        edit = [n for n in pk if n['cls'].endswith('EditText')]
        passkey = [n['text'] for n in pk if re.fullmatch(r'\d{6}', n['text'])]
        if re.search(r'(?i)access (to )?your (contacts|messages)|phonebook|message access', texts) and deny:
            out('dialog', 'access'); tap(deny[0])
        elif edit and b1:
            out('dialog', 'pin'); out('edit', f"{edit[0]['cx']} {edit[0]['cy']}")
            tap(b1[0])
        elif passkey and re.search(r'(?i)type|enter', texts):
            out('dialog', 'passkey'); out('passkey', passkey[0])
        elif b1 and re.fullmatch(r'(?i)pair|pair & connect|ok', b1[0]['text']) and \
                re.search(r'(?i)pair', texts):
            out('dialog', 'consent'); tap(b1[0])
        else:
            out('dialog', 'none')


def cmd_getevent(path):
    c = {'0001': 0, '0002': 0, '0003': 0}
    for ln in read(path).splitlines():
        m = re.fullmatch(r'\s*(?:/dev/input/event\d+: )?([0-9a-f]{4}) ([0-9a-f]{4}) ([0-9a-f]{8})\s*', ln)
        if m and m.group(1) in c:
            c[m.group(1)] += 1
    out('ev_key', c['0001']); out('ev_rel', c['0002']); out('ev_abs', c['0003'])


def main(a):
    if not a:
        print(__doc__); return 2
    c = a[0]
    if c == 'redact':
        for ln in sys.stdin:
            sys.stdout.write(redact(ln))
        return 0
    if c == 'leak':
        return cmd_leak(a[1], a[2:])
    if c == 'dmesg':
        cmd_dmesg(a[1]); return 0
    if c == 'btdump':
        cmd_btdump(a[1]); return 0
    if c == 'input':
        cmd_input(a[1]); return 0
    if c == 'ui':
        cmd_ui(a[1], a[2], a[3] if len(a) > 3 else None); return 0
    if c == 'focus':
        cmd_focus(a[1]); return 0
    if c == 'getevent':
        cmd_getevent(a[1]); return 0
    print(__doc__); return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
