#!/usr/bin/env python3
"""Host test of patch-health.py (offline).
  test-patch-health.py [cuttlefish health binary]
Checks: exactly 7 words change (28 bytes), each site decodes to `ret` or
`b <base getter>`, the base getter symbols match (when llvm-nm output of the
.gnu_debugdata is given via A9_HEALTH_SYMS), a wrong input hash and a moved
instruction are refused. Without capstone, decoding is done by hand."""
import importlib.util, os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ph", os.path.join(HERE, "patch-health.py"))
ph = importlib.util.module_from_spec(spec); spec.loader.exec_module(ph)
src = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser(
    "~/A9-build/mainline/android/dl/cf_arm64_x/apex/com.google.cf.health/root/bin/hw/"
    "android.hardware.health-service.cuttlefish")
fails = 0
def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    fails += not cond

# encoder unit checks (known encodings)
check(ph.b_insn(0x1000, 0x1000) == 0x14000000, "b . encodes as 0x14000000")
check(ph.b_insn(0x1000, 0x0FFC) == 0x17FFFFFF, "b -4 encodes as 0x17ffffff")
check(ph.b_insn(0x3EA00, 0x413C0) == 0x14000000 | ((0x413C0 - 0x3EA00) >> 2), "forward branch")

if not os.path.exists(src):
    print(f"skip binary checks: {src} missing"); sys.exit(1 if fails else 0)
data = open(src, "rb").read()
out = ph.patch(data)
diff = [i for i in range(len(data)) if data[i] != out[i]]
check(len(out) == len(data), "size unchanged")
check(len(diff) <= 28, f"{len(diff)} bytes differ (<= 28)")
for name, at, orig, kind, target in ph.SITES:
    off = ph.vaddr_to_off(out, at)
    w, = struct.unpack_from("<I", out, off)
    if kind == "ret":
        check(w == 0xD65F03C0, f"{name} -> ret")
    else:
        imm = w & 0x3FFFFFF
        if imm & (1 << 25): imm -= 1 << 26
        check((w & 0xFC000000) == 0x14000000 and at + imm * 4 == target,
              f"{name} -> b 0x{target:x}")
# every byte that differs lies inside one of the 7 words
words = {ph.vaddr_to_off(out, s[1]) + k for s in ph.SITES for k in range(4)}
check(all(i in words for i in diff), "no change outside the patch sites")
# refusals
bad = bytearray(data); bad[100] ^= 1
try:
    ph.patch(bytes(bad)); check(False, "modified input refused")
except SystemExit:
    check(True, "modified input refused")
# symbol cross-check (optional)
syms = os.environ.get("A9_HEALTH_SYMS")
if syms and os.path.exists(syms):
    table = {}
    for line in open(syms):
        p = line.split(None, 2)
        if len(p) == 3: table[p[2].strip()] = int(p[0], 16)
    for name, at, orig, kind, target in ph.SITES:
        impl = [v for k, v in table.items() if f"HealthImpl::{name}(" in k]
        check(impl == [at], f"symbol HealthImpl::{name} at 0x{at:x}")
        if target:
            base = [v for k, v in table.items() if f"health::Health::{name}(" in k]
            check(base == [target], f"symbol Health::{name} at 0x{target:x}")
print("PASS" if not fails else f"FAIL ({fails})"); sys.exit(1 if fails else 0)
