#!/usr/bin/env python3
"""patch-health.py -- turn the Cuttlefish health HAL into the AOSP default one.

Input: android.hardware.health-service.cuttlefish from the com.google.cf.health
APEX of Cuttlefish CI build 15581820 (sha256 pinned below). Its HealthImpl
(device/google/cuttlefish/guest/hals/health/health-aidl.cpp) OVERWRITES every
value BatteryMonitor read from /sys/class/power_supply with constants
(MEASURED by disassembly, symbols from the binary's .gnu_debugdata):

  UpdateHealthInfo    0x3e970  AC+USB online, level 85, 3600 mV, 25.0 C, +400 mA,
                               charge counter 1.9 Ah, full 4.0 Ah, "Li-ion"
  getChargeCounterUah 0x3ea00  1900000          getCurrentNowMicroamps 0x3ea30  400000
  getCurrentAverage.. 0x3ea60  UNSUPPORTED      getCapacity            0x3ea90  85
  getChargeStatus     0x3eac0  CHARGING         getBatteryHealthData   0x3eaf0  fake SoH 99

So Android shows 85 %, charging, 25 C whatever the PMI632 reports, and the
framework's low-battery and over-temperature shutdowns can never fire.

Patch: UpdateHealthInfo -> `ret` (the base class hook is empty), each getter
override -> `b Health::<same getter>` (same this/x1/x8 ABI, tail call). The
result behaves like hardware/interfaces/health/aidl/default (Health without
overrides): every value comes from BatteryMonitor (sysfs). Nothing else changes.

Usage: patch-health.py <in> <out>   (offline; verifies input hash and every
original instruction word before writing; exit 1 on any mismatch)
"""
import hashlib, struct, sys

IN_SHA256 = "fba610876392af015e5a966c7c9ad16990ddc32f45116407da50e018685e82d5"
RET = 0xD65F03C0

# (vaddr, expected original word, replacement description)
# originals: every override starts with `stp x29, x30, [sp, #-0x20]!` (a9bf7bfd),
# UpdateHealthInfo with `adrp x8, #0x8000` (MEASURED, capstone).
SITES = [
    # name,                     at,      orig word,   kind, target
    ("UpdateHealthInfo",        0x3E970, None,        "ret", None),
    ("getChargeCounterUah",     0x3EA00, 0xA9BE7BFD,  "b",   0x413C0),
    ("getCurrentNowMicroamps",  0x3EA30, 0xA9BE7BFD,  "b",   0x41590),
    ("getCurrentAverageMicroamps", 0x3EA60, 0xA9BE7BFD, "b", 0x415A0),
    ("getCapacity",             0x3EA90, 0xA9BE7BFD,  "b",   0x415B0),
    ("getChargeStatus",         0x3EAC0, 0xA9BE7BFD,  "b",   0x41790),
    ("getBatteryHealthData",    0x3EAF0, 0xA9BE7BFD,  "b",   0x41B30),
]


def b_insn(pc, target):
    off = target - pc
    assert off % 4 == 0 and -(1 << 27) <= off < (1 << 27)
    return 0x14000000 | ((off >> 2) & 0x3FFFFFF)


def vaddr_to_off(elf, va):
    """ELF64 LE: map a virtual address through the PT_LOAD headers."""
    assert elf[:4] == b"\x7fELF" and elf[4] == 2 and elf[5] == 1
    phoff, = struct.unpack_from("<Q", elf, 0x20)
    phentsize, phnum = struct.unpack_from("<HH", elf, 0x36)
    for i in range(phnum):
        p_type, p_flags, p_off, p_va, _, p_filesz, _, _ = struct.unpack_from(
            "<IIQQQQQQ", elf, phoff + i * phentsize)
        if p_type == 1 and p_va <= va < p_va + p_filesz:
            if not p_flags & 1:
                raise SystemExit(f"0x{va:x} is not in an executable segment")
            return va - p_va + p_off
    raise SystemExit(f"0x{va:x} not mapped")


def patch(data):
    if hashlib.sha256(data).hexdigest() != IN_SHA256:
        raise SystemExit("input is not the pinned Cuttlefish 15581820 health HAL")
    out = bytearray(data)
    # UpdateHealthInfo's first word (adrp x8) is taken from the binary itself
    for name, at, orig, kind, target in SITES:
        off = vaddr_to_off(data, at)
        word, = struct.unpack_from("<I", data, off)
        if orig is not None and word != orig:
            raise SystemExit(f"{name}: unexpected word {word:08x} at 0x{at:x}")
        if orig is None and (word & 0x9F00001F) != 0x90000008:   # adrp x8, ...
            raise SystemExit(f"{name}: expected adrp x8 at 0x{at:x}, got {word:08x}")
        new = RET if kind == "ret" else b_insn(at, target)
        struct.pack_into("<I", out, off, new)
    return bytes(out)


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    data = open(sys.argv[1], "rb").read()
    out = patch(data)
    open(sys.argv[2], "wb").write(out)
    print(f"patched {len(SITES)} sites -> {sys.argv[2]} sha256 {hashlib.sha256(out).hexdigest()}")


if __name__ == "__main__":
    main()
