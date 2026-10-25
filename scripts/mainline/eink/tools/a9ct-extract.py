#!/usr/bin/env python3
"""a9ct-extract: build /lib/firmware/hisense/a9-ctrl-template.bin for the a9_eink
kernel driver from the user's OWN composer binary.

  a9ct-extract.py [--any] [-o OUT.bin] [--no-write]
                  [--cross-recovery LIBRECOVERY_UI.so] [--cross-dumps DIR...] COMPOSER

COMPOSER = vendor.qti.hardware.display.composer-service from the phone's /vendor
(sha256 8c89557b..., checked; any other build is refused because the builder's
addresses are only known for that one). The template (bytes 1-3 of every
448x829 drive-frame word; byte 0 = drive data = 0) is reproduced by emulating
the composer's own builder in HWCDisplay::SetPowerMode (0x19ee98..0x19f054:
memset of the 0x16ab00-byte template at 0x1f29a8, rows 0-4 via a 0x700-byte
scratch row + memcpy, rows 5-828 written in place) with Unicorn. Only that
code runs; the memset/memcpy PLT stubs are done in Python.

Output (little endian): 80-byte header
  u32 magic 0x54433941 ("A9CT"), u32 version 1, u32 header_size 80,
  u32 payload_size 1485568, u8 source_sha256[32], u8 payload_sha256[32]
then the payload. Default OUT: ~/A9-build/mainline/firmware/hisense/a9-ctrl-template.bin
(refuses to write inside a git work tree).

Cross-checks (host only, nothing is written by them):
  --cross-recovery SO  emulate librecovery_ui.so's builder (Init_Eink_SWTcon
                       0x3c4c0..0x3c6e0, params (1648, 824) as EinkSwTconInit
                       passes them) and compare its template word by word.
  --cross-dumps DIR... compare every 1,486,848-byte ring-buffer dump in DIR
                       (live stock composer heap) with the template.

The output contains vendor data: keep it on the device / your own disk, never
commit or redistribute it (docs/mainline-eink-driver.md).

Run: nix-shell -p 'python3.withPackages(p:[p.unicorn p.pyelftools p.numpy])' \
       --run 'python3 -I scripts/mainline/eink/tools/a9ct-extract.py COMPOSER'
"""
import argparse
import glob
import hashlib
import os
import struct
import subprocess
import sys

from elftools.elf.elffile import ELFFile
from unicorn import Uc, UC_ARCH_ARM64, UC_MODE_ARM, UC_HOOK_CODE
from unicorn.arm64_const import (UC_ARM64_REG_SP, UC_ARM64_REG_LR, UC_ARM64_REG_PC,
                                 UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
                                 UC_ARM64_REG_X19, UC_ARM64_REG_X22, UC_ARM64_REG_X23)

COMPOSER_SHA256 = "8c89557bf47b57b1687b9fbcc3e55bedbee175d20f80d14bc5317fcf893df5ed"
TEMPLATE_SHA256 = "a88ae8efc5325e1ab52e4e48b73d2abb1a3c81a7989baa2a226513b07cebfbe7"
W, H = 448, 829
N = W * H * 4                    # 1,485,568 = A9T_FRAME_BYTES
MAGIC, VERSION, HDR = 0x54433941, 1, 80
DEFAULT_OUT = os.path.expanduser("~/A9-build/mainline/firmware/hisense/a9-ctrl-template.bin")

MEM_TOP = 0x6000000
STACK = 0x5f00000
RET = 0x5ff0000
SCRATCH = 0x5800000              # free memory above every LOAD segment


def sha(b):
    return hashlib.sha256(b).hexdigest()


def load(path):
    raw = open(path, "rb").read()
    mu = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
    mu.mem_map(0, MEM_TOP)
    with open(path, "rb") as f:
        for s in ELFFile(f).iter_segments():
            if s["p_type"] == "PT_LOAD":
                if s["p_vaddr"] + s["p_memsz"] > SCRATCH:
                    sys.exit("%s: LOAD segment overlaps the scratch area" % path)
                mu.mem_write(s["p_vaddr"], raw[s["p_offset"]:s["p_offset"] + s["p_filesz"]])
    mu.reg_write(UC_ARM64_REG_SP, STACK)
    mu.reg_write(UC_ARM64_REG_LR, RET)
    return mu


def run(mu, begin, until, memset, memcpy):
    """Run [begin, until); any PC outside it (other than the two stubs) aborts."""
    bad = []

    def hook(uc, addr, size, ud):
        if addr in (memset, memcpy):
            x0, x1, x2 = (uc.reg_read(r) for r in (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2))
            if x2 > N:
                bad.append(addr)
                uc.emu_stop()
                return
            if addr == memset:
                uc.mem_write(x0, bytes([x1 & 0xff]) * x2)
            else:
                uc.mem_write(x0, bytes(uc.mem_read(x1, x2)))
            uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))
        elif not begin <= addr < until:
            bad.append(addr)
            uc.emu_stop()

    h = mu.hook_add(UC_HOOK_CODE, hook)
    mu.emu_start(begin, until, count=50_000_000)
    mu.hook_del(h)
    if bad or mu.reg_read(UC_ARM64_REG_PC) != until:
        sys.exit("emulation left the builder (pc %#x)" % (bad[0] if bad else mu.reg_read(UC_ARM64_REG_PC)))


def composer_template(path):
    mu = load(path)
    # 0x19ee98: x22 = 0x1f29a8; memset(x22, 0, 0x16ab00); loop -> 0x19f054.
    # x19 (the HWCDisplay this) only receives one store: point it at scratch.
    mu.reg_write(UC_ARM64_REG_X19, SCRATCH)
    mu.mem_write(0x1f29a8, b"\xa5" * N)          # poison: every byte must be written
    run(mu, 0x19ee98, 0x19f054, memset=0x1e6960, memcpy=0x1e6800)
    return bytes(mu.mem_read(0x1f29a8, N))


def recovery_template(path):
    mu = load(path)
    tpl, row, par = SCRATCH + 0x10000, SCRATCH + 0x400000, SCRATCH
    # Init_Eink_SWTcon 0x3c4c0: ldp w25, w24, [x23] (params), memset(x22, 0, 0x16ab00),
    # x23 = *GOT(0x68e28) = scratch row; 5 head rows + w24 rows -> 0x3c6e0.
    mu.mem_write(par, struct.pack("<3I", 1648, 824, 50))   # EinkSwTconInit: d0 @0x1a7b0, 0x32
    mu.mem_write(0x68e28, struct.pack("<Q", row))
    mu.mem_write(tpl, b"\xa5" * N)
    mu.reg_write(UC_ARM64_REG_X22, tpl)
    mu.reg_write(UC_ARM64_REG_X23, par)
    run(mu, 0x3c4c0, 0x3c6e0, memset=0x65250, memcpy=0x65660)
    return bytes(mu.mem_read(tpl, N))


def check_invariants(t):
    errs = []
    for k, want in ((0, 0), (2, 0), (3, 0xff)):
        nbad = sum(1 for v in t[k::4] if v != want)
        if nbad:
            errs.append("byte %d != %#x on %d words" % (k, want, nbad))
    return errs


def runs_of(t, r):
    out, cur, st = [], None, 0
    for w in range(W):
        v = t[(r * W + w) * 4 + 1]
        if v != cur:
            if cur is not None:
                out.append("%d-%d:%#x" % (st, w - 1, cur))
            cur, st = v, w
    out.append("%d-%d:%#x" % (st, W - 1, cur))
    return " ".join(out)


def cross_recovery(t, so):
    r = recovery_template(so)
    print("recovery %s sha256 %s" % (so, sha(open(so, "rb").read())))
    print("  recovery template sha256 %s, invariants %s" % (sha(r), check_invariants(r) or "ok"))
    diff = [i for i in range(W * H) if r[i * 4:i * 4 + 4] != t[i * 4:i * 4 + 4]]
    print("  words differing from the composer template (bytes 0-3): %d of %d" % (len(diff), W * H))
    rows = sorted({i // W for i in diff})
    for row in rows[:8]:
        print("  row %d composer: %s" % (row, runs_of(t, row)))
        print("  row %d recovery: %s" % (row, runs_of(r, row)))
    if len(rows) > 8:
        print("  ... %d differing rows in total (%d..%d)" % (len(rows), rows[0], rows[-1]))
    return not diff


def cross_dumps(t, dirs):
    import numpy as np
    tt = np.frombuffer(t, np.uint8).reshape(H, W, 4)
    outside = np.ones((H, W), bool)
    outside[5:, :412] = False                        # data area = rows 5..828, words 0..411
    files = sorted(f for d in dirs for f in glob.glob(os.path.join(d, "*.bin"))
                   if os.path.getsize(f) == 1486848)
    ident = 0
    for f in files:
        d = np.frombuffer(open(f, "rb").read(), np.uint8)
        head = tt[:5].reshape(-1)                    # rows 0..4, all bytes (byte 0 = 0)
        best = max(range(0, len(d) - N + 1, 4),
                   key=lambda o: int((d[o:o + head.size] == head).sum()))
        s = d[best:best + N].reshape(H, W, 4)
        eq = [int((s[:, :, k] == tt[:, :, k]).sum()) for k in range(4)]
        same = all(e == W * H for e in eq)
        ident += same
        b0 = s[:, :, 0]
        print("%s off %d bytes1..3 equal %s/%d%s | byte0 nonzero %d (outside data area %d)" % (
            os.path.relpath(f, os.path.dirname(os.path.dirname(f))), best, eq[1:], W * H,
            " | IDENTICAL to template" if same else "", int((b0 != 0).sum()), int((b0[outside] != 0).sum())))
    print("dumps: %d files, %d identical to the template" % (len(files), ident))
    return len(files) > 0


def in_git_tree(path):
    d = os.path.dirname(os.path.abspath(path)) or "."
    while not os.path.isdir(d):
        d = os.path.dirname(d)
    return subprocess.run(["git", "-C", d, "rev-parse", "--is-inside-work-tree"],
                          capture_output=True, text=True).stdout.strip() == "true"


def main():
    ap = argparse.ArgumentParser(usage="%(prog)s [--any] [-o OUT.bin] [--no-write] "
                                 "[--cross-recovery SO] [--cross-dumps DIR...] COMPOSER")
    ap.add_argument("composer")
    ap.add_argument("-o", "--out", default=DEFAULT_OUT)
    ap.add_argument("--any", action="store_true", help="write even if the template sha256 differs")
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--cross-recovery", metavar="SO")
    ap.add_argument("--cross-dumps", metavar="DIR", nargs="+")
    a = ap.parse_args()

    src = open(a.composer, "rb").read()
    if sha(src) != COMPOSER_SHA256:
        sys.exit("%s: not the supported composer (sha256 %s, want %s)" % (a.composer, sha(src), COMPOSER_SHA256))
    t = composer_template(a.composer)
    errs = check_invariants(t)
    print("template sha256 %s (expected %s)" % (sha(t), TEMPLATE_SHA256))
    print("invariants (byte0 == 0, byte2 == 0, byte3 == 0xff on all %d words): %s" % (W * H, "; ".join(errs) or "ok"))
    rc = 0
    if errs or sha(t) != TEMPLATE_SHA256:
        if errs or not a.any:
            sys.exit("template mismatch: refusing to write")
        print("WARNING: sha256 mismatch accepted (--any)")
    if a.cross_recovery:
        rc |= 0 if cross_recovery(t, a.cross_recovery) else 1
    if a.cross_dumps:
        rc |= 0 if cross_dumps(t, a.cross_dumps) else 1
    if a.no_write:
        return rc
    if in_git_tree(a.out):
        sys.exit("%s is inside a git work tree: vendor data, refusing" % a.out)
    hdr = struct.pack("<4I", MAGIC, VERSION, HDR, N) + bytes.fromhex(sha(src)) + bytes.fromhex(sha(t))
    assert len(hdr) == HDR
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "wb") as f:
        f.write(hdr + t)
    print("%s: %d bytes, file sha256 %s, payload sha256 %s (vendor data: keep it local)" % (
        a.out, HDR + N, sha(hdr + t), sha(t)))
    return rc


if __name__ == "__main__":
    sys.exit(main())
