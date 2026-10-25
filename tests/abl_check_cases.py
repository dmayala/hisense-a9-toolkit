#!/usr/bin/env python3
"""Positive and negative controls for scripts/abl-check.py (run by
tests/abl-check-tests.sh inside nix-shell). Negative controls are built in
memory from the current TWRP image; nothing is written to disk.
Exit 0 = every case behaved as expected."""
import importlib.util
import os
import struct
import sys
import time

A9 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('abl_check', os.path.join(A9, 'scripts/abl-check.py'))
ac = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ac)

D = ac.A9_DATA
G = ac.GOLDEN
TWRP = os.path.join(D, 'recovery/twrp-HLTE556N-31434216263.img')
MODEL = 'Qualcomm Technologies, Inc. Bengal QRD'  # stock kernel log, "Machine model:"

POSITIVE = [
    ('stock package recovery.img', os.path.join(D, 'HLTE556N/recovery.img'), 'recovery'),
    ('stock recovery_b (2026-08-08 adb backup)', os.path.join(D, 'Backups/2026-08-08_055553/recovery_b.img'), 'recovery'),
    ('golden recovery_a (TWRP)', os.path.join(G, 'lun0/recovery_a.bin'), 'recovery'),
    ('golden recovery_b (TWRP)', os.path.join(G, 'lun0/recovery_b.bin'), 'recovery'),
    ('current TWRP image', TWRP, 'recovery'),
    ('recovery-adb-permissive.img', os.path.join(D, 'recovery/recovery-adb-permissive.img'), 'recovery'),
    ('golden boot_a (Magisk)', os.path.join(G, 'lun4/boot_a.bin'), 'boot'),
    ('magisk_patched-30700_boot_b.img', os.path.join(D, 'root/magisk_patched-30700_boot_b.img'), 'boot'),
    ('stock package boot.img', os.path.join(D, 'HLTE556N/boot.img'), 'boot'),
]

PASS = FAIL = 0


def report(ok, name, detail=''):
    global PASS, FAIL
    print('  %s  %s%s' % ('PASS' if ok else 'FAIL', name, ('  (' + detail + ')') if detail else ''))
    if ok:
        PASS += 1
    else:
        FAIL += 1


def logs(rep):
    return '\n'.join(m for s in rep['stages'].values() for _, m in s.get('log', []))


# ---------------------------------------------------------------- image edits
def set32(b, o, v):
    struct.pack_into('<I', b, o, v)


def set64(b, o, v):
    struct.pack_into('<Q', b, o, v)


def rdtbo(b):
    size, off = struct.unpack_from('<IQ', b, 1632)
    return off, size


def fdt_walk(b, base):
    """Yield (path, prop_name, name_off_abs, value_off_abs, len) for one FDT at base."""
    _, tot, off_s, off_str = struct.unpack_from('>4I', b, base)
    p, path = base + off_s, []
    while True:
        tok = struct.unpack_from('>I', b, p)[0]
        p += 4
        if tok == 1:
            e = b.index(b'\0', p)
            path.append(bytes(b[p:e]).decode())
            p = (e + 4) & ~3
        elif tok == 2:
            path.pop()
        elif tok == 3:
            ln, no = struct.unpack_from('>II', b, p)
            p += 8
            na = base + off_str + no
            name = bytes(b[na:b.index(b'\0', na)]).decode()
            yield '/'.join(path), name, na, p, ln
            p = (p + ln + 3) & ~3
        elif tok == 9:
            return


def dtbo_entry_base(b):
    off, _ = rdtbo(b)
    eo = struct.unpack_from('>I', b, off + 20)[0]
    return off + struct.unpack_from('>I', b, off + eo + 4)[0]


def soc_dtb_base(b):
    ps = struct.unpack_from('<I', b, 36)[0]
    pg = lambda n: (n + ps - 1) // ps
    k, r, s2 = struct.unpack_from('<I', b, 8)[0], struct.unpack_from('<I', b, 16)[0], struct.unpack_from('<I', b, 24)[0]
    return ps * (1 + pg(k) + pg(r) + pg(s2) + pg(rdtbo(b)[1]))


def patch_prop(b, base, name, old, new):
    for path, n, _, v, ln in fdt_walk(b, base):
        if n == name and path == '':
            vals = list(struct.unpack_from('>%dI' % (ln // 4), b, v))
            assert old in vals, (name, vals)
            vals = [new if x == old else x for x in vals]
            struct.pack_into('>%dI' % len(vals), b, v, *vals)
            return
    raise AssertionError('property %s not found' % name)


def break_fixup(b):
    """Rename the first label in the overlay's __fixups__ so ufdt cannot resolve it."""
    base = dtbo_entry_base(b)
    for path, n, na, _, _ in fdt_walk(b, base):
        if path.endswith('__fixups__'):
            b[na] = ord('Z') if b[na] != ord('Z') else ord('Y')
            return n
    raise AssertionError('no __fixups__ in the overlay')


def m(fn):
    def apply(src):
        b = bytearray(src)
        fn(b)
        return bytes(b)
    return apply


def without_recovery_dtbo(src):
    """What mkbootimg builds without --recovery_dtbo (TWRP builds 1-2): no
    recovery_dtbo pages, the DTB section directly after the ramdisk, header
    recovery_dtbo_size/offset 0."""
    off, size = rdtbo(src)
    ps = struct.unpack_from('<I', src, 36)[0]
    dtb_size = struct.unpack_from('<I', src, 1648)[0]
    dtb = soc_dtb_base(src)
    b = bytearray(src[:off]) + src[dtb:dtb + dtb_size]
    b += bytes(-len(b) % ps)
    set32(b, 1632, 0)
    set64(b, 1636, 0)
    assert soc_dtb_base(b) == off
    return bytes(b)


NEGATIVE = [
    ('TWRP builds 1-2 analog: built without --recovery_dtbo (DTB directly after the ramdisk)',
     without_recovery_dtbo, 'recovery', 'dtbo', 'kernel image does not have appended DTB'),
    # name, edit, target, failing stage, expected ABL message (substring)
    ('recovery_dtbo absent (size 0, offset 0: mkbootimg without --recovery_dtbo, as TWRP builds 1-2)',
     m(lambda b: (set32(b, 1632, 0), set64(b, 1636, 0))), 'recovery', 'dtbo', 'Dtbo hdr magic mismatch'),
    ('recovery_dtbo_size zeroed, offset kept',
     m(lambda b: set32(b, 1632, 0)), 'recovery', 'dtbo', 'DTB header is corrupted'),
    ('recovery_dtbo bytes stripped (zeroed), header intact',
     m(lambda b: b.__setitem__(slice(rdtbo(b)[0], rdtbo(b)[0] + rdtbo(b)[1]), bytes(rdtbo(b)[1]))),
     'recovery', 'dtbo', 'Dtbo hdr magic mismatch'),
    ('recovery_dtbo_size 0x1800001 (over the 24 MiB limit)',
     m(lambda b: set32(b, 1632, 0x1800001)), 'recovery', 'header', 'Recovery Dtbo Size too big'),
    ('recovery_dtbo offset past the end of the partition',
     m(lambda b: set64(b, 1636, (96 << 20) - 0x1000)), 'recovery', 'dtbo', 'Invalid recovery dtbo'),
    ('corrupt magic "ANDROID?"',
     m(lambda b: b.__setitem__(slice(0, 8), b'ANDROID?')), 'recovery', 'header', 'Invalid boot image header'),
    ('header version 3 (no vendor_boot)',
     m(lambda b: set32(b, 40, 3)), 'recovery', 'header', 'Invalid vendor_boot image header'),
    ('header version 1 with a v2 header_size',
     m(lambda b: set32(b, 40, 1)), 'recovery', 'header', 'Invalid boot image header: 1660'),
    ('v2 header_size 1648',
     m(lambda b: set32(b, 1644, 1648)), 'recovery', 'header', 'Invalid boot image header: 1648'),
    ('page size 8192',
     m(lambda b: set32(b, 36, 8192)), 'recovery', 'header', 'Invalid image pagesize'),
    ('kernel size 0',
     m(lambda b: set32(b, 8, 0)), 'recovery', 'header', 'Invalid image Sizes'),
    ('DTBO table header_size 0x24',
     m(lambda b: struct.pack_into('>I', b, rdtbo(b)[0] + 8, 0x24)), 'recovery', 'dtbo', 'HeaderSize got corrupted'),
    ('overlay qcom,board-id for platform 12 (not QRD)',
     m(lambda b: patch_prop(b, dtbo_entry_base(b), 'qcom,board-id', 0x1000b, 0x1000c)),
     'recovery', 'dtb', 'Dtbo count = 0'),
    ('SoC DTB qcom,msm-id 0x1bc -> 0x1bd',
     m(lambda b: patch_prop(b, soc_dtb_base(b), 'qcom,msm-id', 0x1bc, 0x1bd)),
     'recovery', 'dtb', 'qcom,msm-id does not match'),
    ('overlay __fixups__ label renamed (unresolvable)',
     m(break_fixup), 'recovery', 'dtb', 'failed to perform fixups in overlay'),
]


def check_negative(name, edit, target, stage, needle, src, dtbo=None):
    t = time.time()
    try:
        _, rep = ac.run('<%s>' % name, target, image=edit(src), dtbo=dtbo)
    except ac.HarnessError as e:
        report(False, 'negative: ' + name, 'harness error: %s' % e)
        return
    host_ok = rep['host_checks']['pass']
    got = ac.failed_stage(rep)
    text = logs(rep)
    if stage == 'file':   # host-side check, not ABL code
        ok, got = ac.result_of(rep) == 'FAIL' and not host_ok, 'file' if not host_ok else got
    else:
        ok = ac.result_of(rep) == 'FAIL' and got == stage and needle in text
    st = rep['stages'].get(stage, {})
    detail = 'fails at %s' % got
    if 'rc' in st:
        detail += ', rc=%#x' % st['rc']
    if st.get('failed_at'):
        detail += ', %s' % st['failed_at']
    if not ok:
        detail += '; expected %s with "%s"; log: %s' % (stage, needle, text[-400:])
    report(ok, 'negative: ' + name, detail + ', %.1fs' % (time.time() - t))


def main():
    print('== abl-check positive controls (real images; expect PASS on all 3 stages)')
    for name, path, target in POSITIVE:
        if not os.path.exists(path):
            report(False, name, 'missing ' + path)
            continue
        t = time.time()
        try:
            _, rep = ac.run(path, target)
        except ac.HarnessError as e:
            report(False, name, 'harness error: %s' % e)
            continue
        merged = rep['stages'].get('dtb', {}).get('merged') or {}
        model = (merged.get('props') or {}).get('model')
        ok = ac.result_of(rep) == 'PASS' and model == MODEL
        report(ok, '%s [%s]' % (name, target), 'model "%s", %.1fs' % (model, time.time() - t))

    print('== abl-check negative controls (expect FAIL at the named stage, with the ABL\'s own message)')
    src = open(TWRP, 'rb').read()
    for name, edit, target, stage, needle in NEGATIVE:
        check_negative(name, edit, target, stage, needle, src)
    check_negative('image file truncated inside the DTB section (host file check)',
                   lambda b: b[:soc_dtb_base(b) + 4096], 'recovery', 'file', '', src)
    del src
    boot = open(os.path.join(G, 'lun4/boot_a.bin'), 'rb').read()
    check_negative('boot image flashed as recovery (no recovery_dtbo)', lambda b: b, 'recovery',
                   'dtbo', 'Dtbo hdr magic mismatch', boot)
    dtbo = bytearray(open(ac.DEFAULT_DTBO, 'rb').read())
    dtbo[0:4] = b'\0\0\0\0'
    check_negative('boot with a corrupt dtbo partition', lambda b: b, 'boot',
                   'dtbo', 'Dtbo hdr magic mismatch', boot, dtbo=bytes(dtbo))

    print('== abl-check: %d passed, %d failed' % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
