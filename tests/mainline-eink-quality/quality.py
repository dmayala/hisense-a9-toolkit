#!/usr/bin/env python3
"""Deterministic synthetic targets + full frames vs the existing stock oracle.
Usage: quality.py COMPOSER FIXTURE_SO. Offline; no images or output files.
Requires numpy and unicorn as used by scripts/mainline/tcon/test-tcon-c.py.
"""
import ctypes as ct
import hashlib
import importlib.util
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("tcon_test", ROOT / "scripts/mainline/tcon/test-tcon-c.py")
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
N, W, H, FB = core.N, core.W, core.H, core.FB
composer = Path(sys.argv[1]).read_bytes()
assert hashlib.sha256(composer).hexdigest() == "8c89557bf47b57b1687b9fbcc3e55bedbee175d20f80d14bc5317fcf893df5ed"
L = ct.CDLL(sys.argv[2])
for name, result, args in (
    ("q_new", ct.c_void_p, [ct.c_void_p, ct.c_size_t, ct.c_void_p]),
    ("q_free", None, [ct.c_void_p]),
    ("q_prepare", ct.c_int, [ct.c_void_p, ct.c_void_p, ct.c_uint, ct.c_int, ct.c_int]),
    ("q_data", ct.c_void_p, [ct.c_void_p, ct.c_int]),
    ("q_value", ct.c_uint, [ct.c_void_p, ct.c_int]),
    ("q_start", None, [ct.c_void_p, ct.c_int]),
    ("q_frame", ct.c_int, [ct.c_void_p, ct.c_void_p]),
    ("q_end", None, [ct.c_void_p]),
):
    f = getattr(L, name)
    f.restype, f.argtypes = result, args


def data(q, field, size=N):
    return ct.string_at(L.q_data(q, field), size)


def scene():
    # Full landscape target. Identical grey and RGB ordering for all modes.
    g = np.full((H, W), 255, np.uint8)
    g[200:400] = 235
    g[400:600] = 230
    g[600:700] = 0
    # Synthetic 5x7 E glyphs, supersampled 3x and integer box-filtered.
    # Fixed subpixel offset produces grey edge coverage without host fonts.
    glyph = ("11111", "10000", "10000", "11110", "10000", "10000", "11111")
    hi = np.full((120, 1200), 255, np.uint16)
    for origin in range(3, 1100, 60):
        for y, row in enumerate(glyph):
            for x, bit in enumerate(row):
                if bit == "1": hi[4+y*11:4+(y+1)*11, origin+x*9:origin+(x+1)*9] = 0
    g[720:760, 100:500] = (hi.reshape(40, 3, 400, 3).sum(axis=(1, 3)) // 9).astype(np.uint8)
    assert np.any((g[720:760] > 0) & (g[720:760] < 255))
    return g.reshape(-1)


base = scene()
changed = base.copy()
changed[240 * W + 33] = 0  # exactly one source pixel, in the 235 band
assert np.count_nonzero(changed != base) == 1
all_targets = {}
frames_checked = 0
for mode, name in ((518, "Smooth"), (515, "Clear"), (513, "Balanced")):
    stock = core.oracle.Stock(composer)
    stock.builders(30, 30, 0)
    tmpl = ct.create_string_buffer(stock.template)
    q = L.q_new(composer, len(composer), tmpl)
    try:
        # Reset each mode to exactly the same white old-level history, previous
        # Clear mode, default controls, zeroed persistent step arrays, t1/t2=30/34.
        for phase, grey, clear in (("entry", base, 0), ("one-pixel", changed, 0),
                                    ("manual", changed, 1)):
            old = data(q, 0)
            pre = L.q_value(q, 4)
            stock.uc.mem_write(core.oracle.OLD, old)
            request = stock.build_request(pre, mode, clear, 1)
            ref = stock.prepare(request['old'], grey, request['mode'], request['clear'], 30, 34, steps=None)
            count = L.q_prepare(q, core.ptr(grey), mode, clear, phase == "one-pixel")
            assert count == request['count']
            req = core.Request.from_buffer_copy(data(q, 3, ct.sizeof(core.Request)))
            assert (req.kind, req.full, req.code, req.count) == tuple(request[k] for k in ('kind', 'full', 'code', 'count'))
            assert list(req.type)[:count] == request['types'][:count] and list(req.level)[:count] == request['levels'][:count]
            assert data(q, 0) == bytes(request['old']), "complete old history after request"
            for k in range(count):
                assert data(q, k + 5, 2 * N) == bytes(stock.step_array(k))
            target = data(q, 1)
            assert target == bytes(ref['levels']), "entire target, including outside raw damage"
            assert data(q, 2, 2 * N) == bytes(ref['pairs']), "entire packed old/new history"
            assert L.q_value(q, 2) == ref['type'] and ref['bin'] == 10
            if phase == "entry":
                all_targets[mode] = target
                lv = np.frombuffer(target, np.uint8).reshape(H, W)
                assert (lv[:200] == 15).all() and (lv[600:700] == 0).all()
                if mode != 515: assert set(target) <= {0, 15}
                else: assert (lv[200:600] == 14).all()
                for label, region in (("white", lv[:200]), ("235", lv[200:400]),
                                      ("230", lv[400:600]), ("black", lv[600:700]),
                                      ("AA-text", lv[720:760, 100:500])):
                    histogram = np.bincount(region.reshape(-1), minlength=16).tolist()
                    print(name, label, "levels", histogram, flush=True)
            if phase == "one-pixel":
                delta = np.frombuffer(old, np.uint8) != np.frombuffer(target, np.uint8)
                beyond = int(np.count_nonzero(delta)) - int(delta[240 * W + 33])
                if mode != 515: assert beyond > 0, "diffusion beyond raw one-pixel damage"
                rect = (ct.c_int * 4).from_buffer_copy(data(q, 4, 16))
                yy, xx = np.nonzero(delta.reshape(H, W))
                assert rect[0] <= xx.min() and rect[1] <= yy.min()
                assert rect[2] > xx.max() and rect[3] > yy.max()
                print(name, "one-pixel outside_raw", beyond, "target_rect", list(rect), flush=True)
            L.q_start(q, 10)
            frame = ct.create_string_buffer(stock.template, FB)
            frame_hash = hashlib.sha256()
            n = 0
            while True:
                more = L.q_frame(q, frame)
                expected_more, expected_frame = stock.dispatch()
                assert more == expected_more and frame.raw == expected_frame
                frame_hash.update(frame.raw)
                n += 1
                assert n < 200
                if not more: break
            assert frame.raw == stock.template, "complete final neutral"
            expected_counts = {"entry": {518: 70, 515: 30, 513: 14},
                               "one-pixel": {518: 8, 515: 30, 513: 14},
                               "manual": {518: 48, 515: 60, 513: 30}}
            assert n == expected_counts[phase][mode], (mode, phase, n)
            L.q_end(q)
            assert data(q, 0) == target and L.q_value(q, 4) == mode
            frames_checked += n
            print(name, phase, "steps", count, "frames", n, "sha256", frame_hash.hexdigest(), flush=True)
        L.q_prepare(q, core.ptr(changed), mode, 0, 1)
        assert L.q_value(q, 3) == 1, "identical subsequent input suppressed"
    finally:
        L.q_free(q)
assert all_targets[518] == all_targets[513], "Smooth/Balanced identical full digital targets"
assert all_targets[515] != all_targets[518], "Clear preserves intermediate grey levels"
print("PASS quality: stock targets, requests, arrays, packed history and", frames_checked, "complete frames; neutral and committed history")
