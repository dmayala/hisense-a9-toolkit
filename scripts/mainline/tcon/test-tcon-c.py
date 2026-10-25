#!/usr/bin/env python3
"""Byte-for-byte test of the C TCON (liba9tcon.so + the a9tcon CLI) against the stock
composer code under Unicorn (../tcon-oracle.py) and the Python model (../a9tcon.py).

usage (host only, no phone; builds the library with make if needed):
  nix-shell -p gcc gnumake 'python3.withPackages(p:[p.unicorn p.numpy p.pillow])' --run \\
    'python3 -I scripts/mainline/tcon/test-tcon-c.py COMPOSER [IMAGE.png] [--quick] [--tmp DIR]
     [--template FILE]'

COMPOSER = vendor.qti.hardware.display.composer-service, sha256 8c89557b... The report
holds hashes and counts only. --tmp: where the CLI test writes its raw inputs
(default: a temporary directory, removed afterwards; it receives template copies, so
never point it into git).

Whole 32-bit words. The stock side's output buffers start as the control template,
built by stock's own SetPowerMode code inside the oracle (tcon-oracle.py, Stock.template:
stock's lookUpTable writes byte 0 only into a ring slot whose bytes 1..3 hold the
template, and its null frames are a memcpy of that template). The C side and the
Python model get the same template in every output buffer and in the hold cache (and
the model's null buffer), so every frame is compared on all 4 bytes, and every frame's
bytes 1..3 must equal the template (a mismatch there is a core bug: a memset or a write
outside byte 0). --template FILE (hisense/a9-ctrl-template.bin with its A9CT header, or
a raw template) must equal the stock-built template; the CLI checks then use it,
otherwise they use a raw copy of the stock-built template written to the tmp dir.

Sections: A grey builders, B temperature bins, C init flash (all bins), D steady
updates (the oracle's 12 cases), E lookUpTable for every type at every bin, F the
mode-switch request builder (all pre/cur pairs, clear 0/1), G full mode-switch
updates (steps + final pass, every frame), H RGBA -> grey + rotation, I the CLI.
"""
import ctypes as ct
import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import a9tcon  # noqa: E402

_spec = importlib.util.spec_from_file_location('tcon_oracle', os.path.join(HERE, '..', 'tcon-oracle.py'))
oracle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(oracle)

N, W, H, FB = a9tcon.N, a9tcon.W, a9tcon.H, a9tcon.FRAME_BYTES
SRC_STRIDE, SRC_ROWS = 832, 1648
u8p = ct.POINTER(ct.c_uint8)


class State(ct.Structure):
    _fields_ = [('t', ct.c_void_p), ('g2', ct.c_uint8 * 256), ('g16', ct.c_uint8 * 256),
                ('scratch', ct.c_uint8 * 257), ('counter', ct.c_uint32), ('step', ct.c_uint32),
                ('init_done', ct.c_int), ('balanced_full', ct.c_uint32), ('cache', ct.c_void_p)]


class Request(ct.Structure):
    _fields_ = [('kind', ct.c_uint32), ('full', ct.c_uint32), ('code', ct.c_uint32),
                ('count', ct.c_uint32), ('type', ct.c_uint32 * 5), ('level', ct.c_uint8 * 5)]


def ptr(a):
    return a.ctypes.data_as(ct.c_void_p)


class C:
    """The C library, one engine instance (state + persistent step arrays)."""

    def __init__(self, lib, composer, template):
        self.tmpl = template
        self.L = L = ct.CDLL(lib)
        for name, res, args in (
                ('a9t_sizeof', ct.c_size_t, [ct.c_int]),
                ('a9t_load', ct.c_int, [ct.c_void_p, ct.c_char_p, ct.c_size_t]),
                ('a9t_init', None, [ct.c_void_p, ct.c_void_p, ct.c_void_p]),
                ('a9t_set_controls', None, [ct.c_void_p, ct.c_int32, ct.c_int32, ct.c_int32]),
                ('a9t_temp_bin', ct.c_int, [ct.c_void_p, ct.c_int32, ct.c_int32]),
                ('a9t_rgba_to_grey', None, [ct.c_void_p, ct.c_void_p]),
                ('a9t_condition', None, [ct.c_void_p, ct.c_void_p, ct.c_uint32, ct.c_void_p, ct.c_void_p]),
                ('a9t_pack', None, [ct.c_void_p, ct.c_void_p, ct.c_uint32, ct.c_int, ct.c_void_p]),
                ('a9t_final_type', ct.c_int, [ct.c_uint32, ct.c_int]),
                ('a9t_lookup', ct.c_int, [ct.c_void_p, ct.c_uint32, ct.c_int, ct.c_void_p, ct.c_void_p]),
                ('a9t_init_frame', ct.c_int, [ct.c_void_p, ct.c_int, ct.c_void_p]),
                ('a9t_request_build', ct.c_uint32, [ct.c_void_p, ct.c_uint32, ct.c_uint32, ct.c_int,
                                                    ct.POINTER(ct.c_int), ct.POINTER(ct.c_int)]),
                ('a9t_request_arrays', None, [ct.c_void_p, ct.c_void_p, ct.c_void_p]),
                ('a9t_update_begin', None, [ct.c_void_p]),
                ('a9t_update_frame', ct.c_int, [ct.c_void_p, ct.c_void_p, ct.c_void_p, ct.c_uint32,
                                                ct.c_int, ct.c_void_p, ct.c_void_p])):
            f = getattr(L, name)
            f.restype, f.argtypes = res, args
        assert L.a9t_sizeof(1) == ct.sizeof(State) and L.a9t_sizeof(3) == ct.sizeof(Request)
        self.tables = ct.create_string_buffer(L.a9t_sizeof(0))
        if L.a9t_load(self.tables, composer, len(composer)):
            raise SystemExit('a9t_load refused the composer')
        self.work = ct.create_string_buffer(L.a9t_sizeof(2))
        self.cache = np.zeros(FB, np.uint8)
        self.st = State()
        L.a9t_init(ct.byref(self.st), self.tables, ptr(self.cache))
        self.cache[:] = template   # a9t_init zeroes it; the caller pre-fills
        self.req = Request()
        self.arrays = [np.zeros(N, np.uint16) for _ in range(5)]
        self.arr_ptrs = (ct.c_void_p * 5)(*[a.ctypes.data for a in self.arrays])

    def controls(self, *k):
        self.L.a9t_set_controls(ct.byref(self.st), *k)
        return bytes(self.st.g2), bytes(self.st.g16)

    def temp_bin(self, t1, t2):
        return self.L.a9t_temp_bin(ct.byref(self.st), t1, t2)

    def condition(self, grey, mode):
        lv = np.zeros(N, np.uint8)
        g = np.ascontiguousarray(grey, np.uint8)
        self.L.a9t_condition(ct.byref(self.st), ptr(g), mode, ptr(lv), self.work)
        return lv

    def pack(self, old, new, mode, clear):
        pr = np.zeros(N, np.uint16)
        o, n = np.ascontiguousarray(old, np.uint8), np.ascontiguousarray(new, np.uint8)
        self.L.a9t_pack(ptr(o), ptr(n), mode, clear, ptr(pr))
        return pr

    def lookup(self, wtype, b, pairs):
        out = self.tmpl.copy()
        r = self.L.a9t_lookup(ct.byref(self.st), wtype, b, ptr(pairs), ptr(out))
        return r, out

    def init_frame(self, b):
        out = self.tmpl.copy()
        return self.L.a9t_init_frame(ct.byref(self.st), b, ptr(out)), out

    def build(self, pre, cur, clear, old, flag6c7=1):
        """Builder + arrays; old is modified in place. Returns mode, clear, clear_reset."""
        c, cr = ct.c_int(clear), ct.c_int(0)
        mode = self.L.a9t_request_build(ct.byref(self.req), pre, cur, flag6c7, ct.byref(c), ct.byref(cr))
        if not (pre & 0xff == cur & 0xff and not clear):
            self.L.a9t_request_arrays(ct.byref(self.req), ptr(old), self.arr_ptrs)
        return mode, c.value, cr.value

    def begin(self):
        self.L.a9t_update_begin(ct.byref(self.st))

    def frame(self, wtype, b, pairs):
        out = self.tmpl.copy()
        r = self.L.a9t_update_frame(ct.byref(self.st), ct.byref(self.req), self.arr_ptrs,
                                    wtype, b, ptr(pairs), ptr(out))
        return r, out

    def rgba_to_grey(self, rgba):
        g = np.zeros(N, np.uint8)
        self.L.a9t_rgba_to_grey(ptr(np.ascontiguousarray(rgba, np.uint8)), ptr(g))
        return g


def h16(b):
    return hashlib.sha256(bytes(b)).hexdigest()[:16]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    quick = '--quick' in sys.argv
    tmp_arg = sys.argv[sys.argv.index('--tmp') + 1] if '--tmp' in sys.argv else None
    if tmp_arg in args:
        args.remove(tmp_arg)
    tmpl_arg = sys.argv[sys.argv.index('--template') + 1] if '--template' in sys.argv else None
    if tmpl_arg in args:
        args.remove(tmpl_arg)
    composer = open(args[0], 'rb').read()
    img_path = args[1] if len(args) > 1 else None

    build = os.path.join(HERE, 'build')
    subprocess.run(['make', '-s', '-C', HERE], check=True)
    stock = oracle.Stock(composer)
    tmpl = stock.template_np.copy()
    tmpl_sha = hashlib.sha256(stock.template).hexdigest()
    c = C(os.path.join(build, 'liba9tcon.so'), composer, tmpl)
    model = a9tcon.Tcon(composer)
    model.null, model.cache = tmpl.copy(), tmpl.copy()   # model writes byte 0 only too
    counts = {'checks': 0, 'fail': 0, 'frames_vs_stock': 0, 'frames_vs_model': 0,
              'ctl_frames': 0, 'ctl_bad_stock': 0, 'ctl_bad_c': 0}
    t_start = time.time()
    tw = tmpl.reshape(-1, 4)

    def check(name, ok, **kw):
        counts['checks'] += 1
        counts['fail'] += 0 if ok else 1
        print(('OK   ' if ok else 'FAIL ') + name, kw if kw else '', flush=True)

    def ctlchk(fs, fc):
        """bytes 1..3 of a stock and a C frame == the template; returns True if both are"""
        a = np.frombuffer(fs, np.uint8).reshape(-1, 4) if isinstance(fs, bytes) else fs.reshape(-1, 4)
        b = fc.reshape(-1, 4)
        okc = np.array_equal(b[:, 1:], tw[:, 1:])
        oks = np.array_equal(a[:, 1:], tw[:, 1:])
        counts['ctl_frames'] += 1
        counts['ctl_bad_stock'] += 0 if oks else 1
        counts['ctl_bad_c'] += 0 if okc else 1
        if not okc:
            bad = np.nonzero((b[:, 1:] != tw[:, 1:]).any(1))[0]
            print('   C frame bytes 1..3 differ from the template at words', bad[:4], flush=True)
        return oks and okc

    # ---- 0. the control template (stock's own builder) and the --template file
    check('0 stock-built control template: byte 0 = 0, byte 2 = 0, byte 3 = 0xff',
          bool((tw[:, 0] == 0).all() and (tw[:, 2] == 0).all() and (tw[:, 3] == 0xff).all()),
          sha256=tmpl_sha[:16])
    tmpl_file = None
    if tmpl_arg:
        raw = open(tmpl_arg, 'rb').read()
        if raw[:4] == b'A9CT':
            hdr, raw = raw[:80], raw[80:]
            ok_h = (int.from_bytes(hdr[4:8], 'little') == 1 and int.from_bytes(hdr[8:12], 'little') == 80
                    and int.from_bytes(hdr[12:16], 'little') == FB
                    and hdr[48:80] == hashlib.sha256(raw).digest())
            check('0 --template A9CT header (version, sizes, payload sha256)', ok_h)
        check('0 --template payload == stock-built template', raw == stock.template,
              file_sha256=hashlib.sha256(raw).hexdigest()[:16])
        tmpl_file = os.path.abspath(tmpl_arg)

    def set_stock_counter(v=0):
        stock.uc.mem_write(oracle.G_COUNTER, int(v).to_bytes(4, 'little'))

    # ---- A. grey builders
    for ctl in ((30, 30, 0), (0, 100, 100), (101, -1, 50), (55, 70, 33)):
        g2s, g16s = stock.builders(*ctl)
        g2c, g16c = c.controls(*ctl)
        model.set_controls(*ctl)
        check(f'A builders {ctl}', g2c == g2s and g16c == g16s
              and g2c == bytes(model.g2) and g16c == bytes(model.g16))
    stock.builders(30, 30, 0)
    c.controls(30, 30, 0)
    model.set_controls(30, 30, 0)

    # ---- B. temperature -> bin
    bad, n = [], 0
    temps = list(range(-5, 60)) + [127, 128, 200, 255, 256, -129, -128, -100000]
    for t1 in temps:
        for t2 in (-1, 0, 20, 33, 49, 50, 51, 128, -129):
            n += 1
            sb = oracle.stock_bin(stock, t1, t2)
            if not (c.temp_bin(t1, t2) == sb == model.temp_bin(t1, t2)):
                bad.append((t1, t2))
    check(f'B temperature bin ({n} pairs)', not bad, mismatches=bad[:5])

    # one (t1, t2) per bin, from the bin map
    bin_map = composer[a9tcon.BIN_MAP:a9tcon.BIN_MAP + 51]
    bin_temp = {}
    for a, b in enumerate(bin_map):
        bin_temp.setdefault(b, a)
    bins = sorted(bin_temp)
    check('B bin map covers bins 0..13', bins == list(range(14)), bins=bins)

    # ---- C. init flash at every bin
    for b in (bins if not quick else [0, 10, 13]):
        t = bin_temp[b]
        stock.uc.mem_write(oracle.G_INIT, b'\0')
        set_stock_counter()
        sb = oracle.stock_bin(stock, t, t)
        stock.uc.mem_write(oracle.G_BIN, bytes([sb]))
        c.st.counter = 0
        model.counter = 0
        n, same = 0, True
        while True:
            rs, fs = stock.init_frame()
            rc, fc = c.init_frame(sb)
            fm = tmpl.copy()
            rm = model.init_frame(sb, fm)
            same &= rs == rc == rm and fs == fc.tobytes() and np.array_equal(fc, fm)
            same &= ctlchk(fs, fc)
            n += 1
            if not rs or not rc or n > 200:
                break
        counts['frames_vs_stock'] += n
        counts['frames_vs_model'] += n
        check(f'C init flash bin {sb}', same and rs == rc == 0 and sb == b, frames=n)
    stock.uc.mem_write(oracle.G_INIT, b'\1')

    # ---- D. the oracle's steady updates
    grey, name = oracle.test_image(img_path)
    print('image:', name)
    rng = np.random.default_rng(7)
    noise = rng.integers(0, 256, N, dtype=np.uint8)
    cases = [(0x201, 0, 28, 34, 'white'), (0x206, 0, 27, 33, 'white'),
             (0x209, 0, 10, 12, 'white'), (0x203, 0, 28, 33, 'prev'),
             (0x201, 0, 28, 34, 'prev'), (0x203, 0, 28, 33, 'inverse'),
             (0x203, 1, 28, 33, 'prev'), (0x201, 1, 0, 0, 'prev'),
             (0x401, 0, 45, 45, 'white'), (0x303, 0, 20, 20, 'prev'),
             (0x202, 0, 33, 30, 'white'), (0x208, 0, 5, 5, 'prev')]
    if quick:
        cases = cases[:4] + [cases[9]]
    prev = np.full(N, 15, np.uint8)
    for mode, clear, t1, t2, src in cases:
        old = {'white': np.full(N, 15, np.uint8), 'prev': prev,
               'inverse': (15 - prev).astype(np.uint8)}[src]
        pic = grey if mode != 0x208 else noise
        st = stock.prepare(old, pic, mode, clear, t1, t2)
        t0 = time.time()
        lv = c.condition(pic, mode)
        pr = c.pack(old, lv, mode, clear)
        tc = time.time() - t0
        ct_, cb = c.L.a9t_final_type(mode, clear), c.temp_bin(t1, t2)
        lm = model.condition(pic, mode)
        pm = model.pack(old, lm, mode, clear)
        check(f'D prepare {mode:#x} clear {clear}',
              np.array_equal(st['levels'], lv) and np.array_equal(st['pairs'], pr)
              and st['type'] == ct_ and st['bin'] == cb
              and np.array_equal(lm, lv) and np.array_equal(pm, pr),
              type=ct_, bin=cb, levels=h16(lv), pairs=h16(pr),
              sentinels=int((pr == 256).sum()), secs_c=round(tc, 2))
        for flag in ((0, 1) if ct_ == 1 else (0,)):
            stock.uc.mem_write(stock.S + 4, flag.to_bytes(4, 'little'))
            c.st.balanced_full = model.balanced_full = flag
            set_stock_counter()
            c.st.counter = model.counter = 0
            n, same, same_m = 0, True, True
            fm = tmpl.copy()
            while True:
                rs, fs = stock.lookup(st['type'])
                rc, fc = c.lookup(ct_, cb, pr)
                fm[:] = tmpl
                rm = model.lookup(ct_, cb, pm, fm)
                same &= rs == rc and fs == fc.tobytes() and ctlchk(fs, fc)
                same_m &= rm == rc and np.array_equal(fc, fm)
                n += 1
                if not rs or not rc or n > 300:
                    break
            counts['frames_vs_stock'] += n
            counts['frames_vs_model'] += n
            check(f'D   frames {mode:#x} clear {clear} type {ct_} bin {cb}'
                  + (f' S+4={flag}' if ct_ == 1 else ''),
                  same and same_m and rs == rc == 0 and fs == stock.template, frames=n)
        prev = lv
    stock.uc.mem_write(stock.S + 4, bytes(4))
    c.st.balanced_full = 0

    # ---- E. lookUpTable: every type at every bin, all 257 transitions present
    allp = (np.arange(N, dtype=np.int64) * 7 % 257).astype(np.uint16)
    stock.uc.mem_write(oracle.PAIRS, allp.tobytes())
    stock.uc.mem_write(oracle.G_PAIRS, oracle.PAIRS.to_bytes(8, 'little'))
    sweep = [(1, 0), (1, 1), (2, 0), (3, 0), (6, 0), (9, 0)]
    spot = [(0, 0), (4, 0), (5, 0), (7, 0), (8, 0), (10, 0), (0xff, 0), (6, 1), (9, 1)]
    plan = [(t, f, b) for t, f in sweep for b in (bins if not quick else (0, 13))]
    plan += [(t, f, b) for t, f in spot for b in (3, 12)]
    for wtype, flag, b in plan:
        stock.uc.mem_write(stock.S + 4, flag.to_bytes(4, 'little'))
        stock.uc.mem_write(oracle.G_BIN, bytes([b]))
        c.st.balanced_full = flag
        set_stock_counter()
        c.st.counter = 0
        n, same = 0, True
        while True:
            rs, fs = stock.lookup(wtype)
            rc, fc = c.lookup(wtype, b, allp)
            same &= rs == rc and fs == fc.tobytes() and ctlchk(fs, fc)
            n += 1
            if not rs or not rc or n > 300:
                break
        counts['frames_vs_stock'] += n
        check(f'E lookUpTable type {wtype} bin {b} S+4={flag}', same and rs == rc == 0, frames=n)
    stock.uc.mem_write(stock.S + 4, bytes(4))
    c.st.balanced_full = 0

    # ---- F. mode-switch request builder: every (pre, cur) low byte, clear 0/1
    pres = list(range(0, 11)) if not quick else [1, 2, 6, 9]
    curs = list(range(0, 11)) if not quick else [1, 2, 3, 6, 9]
    old = rng.integers(0, 16, N, dtype=np.uint8)
    steps_seen, bad = {}, []
    nb = 0
    for flag6c7 in (1, 0):
        for pre in pres:
            for cur in curs:
                for clear in (0, 1):
                    if flag6c7 == 0 and (pre not in (1, 6) or clear):
                        continue
                    hi = 0x200 if cur != 3 else 0x300
                    oc = old.copy()
                    stock.uc.mem_write(oracle.OLD, old.tobytes())
                    s = stock.build_request(pre | 0x200, cur | hi, clear, flag6c7)
                    mode, cl, cr = c.build(pre | 0x200, cur | hi, clear, oc, flag6c7)
                    r, k = c.req, s['count']
                    ok = (r.kind == s['kind'] and r.full == s['full'] and r.code == s['code']
                          and r.count == k and list(r.type)[:k] == s['types'][:k]
                          and mode == s['mode'] and cl == s['clear'] and cr == s['clear_reset']
                          and np.array_equal(oc, s['old']))
                    if k:   # the levels the arrays use: byte 0x24.. (count 1 uses 0xf)
                        ok &= list(r.level)[:k] == s['levels'][:k]
                    for j in range(5):   # persistent arrays, including never-written ones
                        ok &= np.array_equal(c.arrays[j], stock.step_array(j))
                    nb += 1
                    if not ok:
                        bad.append((pre, cur, clear, flag6c7))
                    if s['count'] or s['full'] or s['kind']:
                        steps_seen[(pre, cur, clear)] = (s['kind'], s['full'], s['types'][:k], s['clear'])
    check(f'F request builder + step arrays ({nb} (pre, cur, clear, 0x6c7) cases)', not bad,
          mismatches=bad[:6])
    print('   requests (pre, cur, clear) -> (kind, full, steps, clear after):')
    for key in sorted(steps_seen):
        print('     ', key, '->', steps_seen[key])

    # ---- G. full mode-switch updates, every frame
    switches = [(1, 0x206, 0, 28, 34), (6, 0x201, 0, 28, 34), (9, 0x201, 0, 20, 20),
                (1, 0x202, 0, 28, 33), (6, 0x202, 0, 28, 33), (9, 0x202, 0, 5, 5),
                (6, 0x303, 0, 28, 33), (2, 0x206, 0, 40, 40), (6, 0x209, 0, 28, 34),
                (3, 0x209, 0, 28, 34), (2, 0x201, 0, 28, 34), (6, 0x206, 1, 28, 34),
                (3, 0x203, 1, 28, 33), (9, 0x209, 1, 28, 34), (1, 0x201, 1, 28, 34),
                (9, 0x206, 0, 0, 0)]
    if quick:
        switches = switches[:3] + [switches[10], switches[11]]
    cli_cases = []
    prev = np.full(N, 15, np.uint8)
    for pre, cur, clear, t1, t2 in switches:
        old = prev.copy()
        stock.uc.mem_write(oracle.OLD, old.tobytes())
        s = stock.build_request(pre | 0x200, cur, clear)
        oc = old.copy()
        mode, cl, cr = c.build(pre | 0x200, cur, clear, oc)
        st = stock.prepare(s['old'], grey, s['mode'], s['clear'], t1, t2, steps=None)
        lv = c.condition(grey, mode)
        pr = c.pack(oc, lv, mode, cl)
        ty, b = c.L.a9t_final_type(mode, cl), c.temp_bin(t1, t2)
        ok = (np.array_equal(st['levels'], lv) and np.array_equal(st['pairs'], pr)
              and st['type'] == ty and st['bin'] == b and st['count'] == c.req.count)
        set_stock_counter()
        c.st.counter = 0
        c.begin()
        n, same = 0, True
        digest, digest0 = hashlib.sha256(), hashlib.sha256()
        while True:
            rs, fs = stock.dispatch()
            rc, fc = c.frame(ty, b, pr)
            same &= rs == rc and fs == fc.tobytes() and ctlchk(fs, fc)
            digest.update(fs)
            f0 = np.zeros(FB, np.uint8)          # byte 0 only: the CLI without --template
            f0[0::4] = np.frombuffer(fs, np.uint8)[0::4]
            digest0.update(f0.tobytes())
            n += 1
            if not rs or not rc or n > 600:
                break
        counts['frames_vs_stock'] += n
        steps = list(c.req.type)[:c.req.count]
        check(f'G switch {pre}->{cur:#x} clear {clear}: steps {steps} + final type {ty} bin {b}',
              ok and same and rs == rc == 0 and fs == stock.template, frames=n, full=c.req.full, clear_after=cl,
              frames_sha256=digest.hexdigest()[:16])
        cli_cases.append((pre, cur, clear, t1, t2, old, digest.hexdigest(), n, digest0.hexdigest()))
        prev = lv

    # ---- H. RGBA -> grey + rotation
    yy, xx = np.mgrid[0:SRC_ROWS, 0:SRC_STRIDE]
    inputs = [('random', rng.integers(0, 256, SRC_ROWS * SRC_STRIDE * 4, dtype=np.uint8))]
    # the test picture, back in SF's portrait layout (grey -> RGB with distinct channels)
    land = grey.reshape(H, W)
    port = np.zeros((SRC_ROWS, SRC_STRIDE, 4), np.uint8)
    port[:, :H, 0] = np.rot90(land, -1)               # portrait row x, column 823 - y
    port[:, :H, 1] = 255 - port[:, :H, 0]
    port[:, :H, 2] = (port[:, :H, 0] // 2 + yy[:, :H]) & 0xff
    port[:, :, 3] = 0xff
    inputs.append((name + ' (portrait RGBA)', port.ravel()))
    for label, rgba in inputs:
        gs = stock.rgba_to_grey(rgba)
        gc = c.rgba_to_grey(rgba)
        check(f'H RGBA -> grey + rotation, {label}', np.array_equal(gs, gc), grey=h16(gc))
    src = inputs[1][1].reshape(SRC_ROWS, SRC_STRIDE, 4).astype(np.uint32)
    luma = ((76 * src[..., 0] + 150 * src[..., 1] + 30 * src[..., 2]) >> 8).astype(np.uint8)
    rot = np.ascontiguousarray(np.rot90(luma[:, :H], 1))  # grey[y][x] = luma[x][823 - y]
    check('H rotation is grey[y][x] = luma(row x, column 823 - y)',
          np.array_equal(rot.ravel(), c.rgba_to_grey(inputs[1][1])))

    # ---- I. the CLI on raw files, whole words with --template: steady update and init
    # flash (vs a9tcon.py), mode switches (vs stock), template file handling
    tmp = tmp_arg or tempfile.mkdtemp(prefix='a9tcon-test-')
    os.makedirs(tmp, exist_ok=True)
    try:
        cli = os.path.join(build, 'a9tcon')
        comp = os.path.abspath(args[0])
        gpath, opath = os.path.join(tmp, 'grey.raw'), os.path.join(tmp, 'old.raw')
        grey.tofile(gpath)
        # the template as files: raw and with the A9CT header (stock-built bytes)
        t_raw, t_ct = os.path.join(tmp, 'template.raw'), os.path.join(tmp, 'template-a9ct.bin')
        with open(t_raw, 'wb') as f:
            f.write(stock.template)
        hdr = (b'A9CT' + (1).to_bytes(4, 'little') + (80).to_bytes(4, 'little')
               + FB.to_bytes(4, 'little') + hashlib.sha256(composer).digest()
               + hashlib.sha256(stock.template).digest())
        with open(t_ct, 'wb') as f:
            f.write(hdr + stock.template)
        tfile = tmpl_file or t_ct

        def run_cli(*extra, tf=tfile):
            pre_args = ['--template', tf] if tf else []
            out = subprocess.run([cli, comp, *pre_args, *extra], check=True, capture_output=True,
                                 text=True).stdout
            last = out.strip().splitlines()[-1].split()
            return int(last[1]), last[3], out

        def model_frames(gen_frame):
            dg, n = hashlib.sha256(), 0
            while True:
                f = tmpl.copy()
                more = gen_frame(f)
                dg.update(f.tobytes())
                n += 1
                if not more:
                    return n, dg.hexdigest()

        # steady 0x201 from white, digest vs the Python model (template buffers)
        model.counter, model.balanced_full = 0, 0
        lm = model.condition(grey, 0x201)
        pm = model.pack(np.full(N, 15, np.uint8), lm, 0x201, 0)
        tm, bm = model.final_type(0x201, 0), model.temp_bin(28, 34)
        n, dg = model_frames(lambda f: model.lookup(tm, bm, pm, f))
        k, d, out = run_cli('--grey', gpath, '--mode', '0x201', '--t1', '28', '--t2', '34')
        check('I CLI --template (A9CT) steady 0x201 vs a9tcon.py', k == n and d == dg
              and f'template {tmpl_sha}' in out, frames=k)
        model.counter = 0
        n, dg = model_frames(lambda f: model.init_frame(model.temp_bin(28, 34), f))
        k, d, _ = run_cli('--init', '--t1', '28', '--t2', '34', tf=t_raw)
        check('I CLI --template (raw) init flash vs a9tcon.py', k == n and d == dg, frames=k)
        # mode switches vs the stock digests of section G; without --template = byte 0 only
        for pre, cur, clear, t1, t2, old, dstock, nstock, d0 in cli_cases[:3]:
            old.tofile(opath)
            a = ('--grey', gpath, '--old', opath, '--pre', hex(pre | 0x200), '--mode', hex(cur),
                 '--clear', str(clear), '--t1', str(t1), '--t2', str(t2))
            k, d, _ = run_cli(*a)
            check(f'I CLI --template switch {pre}->{cur:#x} vs stock (whole words)',
                  k == nstock and d == dstock, frames=k)
            k, d, _ = run_cli(*a, tf=None)
            check(f'I CLI without --template switch {pre}->{cur:#x} vs stock byte 0',
                  k == nstock and d == d0, frames=k)
        # a corrupt template is refused
        bad = os.path.join(tmp, 'template-bad.bin')
        with open(bad, 'wb') as f:
            f.write(hdr + stock.template[:-1] + b'\0')
        r = subprocess.run([cli, comp, '--template', bad, '--init'], capture_output=True, text=True)
        check('I CLI refuses an A9CT file whose payload sha256 does not match', r.returncode != 0,
              err=r.stderr.strip()[-60:])
        # the sequencer's E5a known case (first-boot.sh eink_cli_selftest)
        steps = np.repeat((17 * ((np.arange(W) * 16) // W)).astype(np.uint8)[None, :], H, 0)
        steps.tofile(gpath)
        np.full(N, 15, np.uint8).tofile(opath)
        e5a = ('--grey', gpath, '--old', opath, '--mode', '0x203', '--pre', '0x203', '--clear', '1',
               '--t1', '30', '--t2', '30')
        k0, d0, _ = run_cli(*e5a, tf=None)
        k1, d1, _ = run_cli(*e5a)
        check('I E5a known case without --template (first-boot.sh)',
              (k0, d0) == (60, '6661424b107d3839bf06d690e8d9a27413144a8f93f3f3f8f5868d24c29b39f7'),
              frames=k0, sha256=d0)
        print(f'   E5a known case WITH --template: frames {k1} sha256 {d1}')
    finally:
        if not tmp_arg:
            shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nSUMMARY: {counts['checks'] - counts['fail']}/{counts['checks']} checks match, "
          f"{counts['fail']} failures; frames compared byte for byte: "
          f"{counts['frames_vs_stock']} vs stock, {counts['frames_vs_model']} vs a9tcon.py "
          f"({FB} bytes each, whole words); control bytes 1..3 == template checked on "
          f"{counts['ctl_frames']} frame pairs: {counts['ctl_bad_stock']} stock and "
          f"{counts['ctl_bad_c']} C frames differ; template {tmpl_sha[:16]}; "
          f"{time.time() - t_start:.0f} s")
    sys.exit(1 if counts['fail'] or counts['ctl_bad_stock'] or counts['ctl_bad_c'] else 0)


if __name__ == '__main__':
    main()
