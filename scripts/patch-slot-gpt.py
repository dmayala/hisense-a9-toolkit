#!/usr/bin/env python3
"""
Patch the A/B slot attribute bits in the Hisense A9's LUN4 GPT so slot B is
active and bootable again.

Why: after TWRP failed to boot from recovery_b, the bootloader marked boot_b
UNBOOTABLE and switched to slot A. Slot A has no usable system (the GSI lives
on B, and GSI installs typically delete system_a for space), so the device
bootloops. The slot state lives ONLY in these GPT attribute bits -- not in
misc, not in any partition.

Qualcomm bootctrl bit layout (within the 8-byte attributes field):
    48-49 priority   50 active   51-53 tries   54 successful   55 unbootable

This rewrites both the primary and backup GPT and recomputes both CRC32s
(entry array CRC, then header CRC with its own field zeroed), which is what
makes the result valid rather than merely edited.
"""
import os, struct, binascii, shutil, sys

S = 4096
A9 = os.environ.get("A9_DATA", os.path.expanduser("~/a9-assets"))

# target attributes
ATTR_ACTIVE   = 0x7f000000000000  # priority=3 active=1 tries=7 successful=1 unbootable=0
ATTR_INACTIVE = 0x7a000000000000  # priority=2 active=0 tries=7 successful=1 unbootable=0
TARGET = {'boot_b': ATTR_ACTIVE, 'boot_a': ATTR_INACTIVE}


def decode(a):
    return (f"priority={(a>>48)&3} active={(a>>50)&1} tries={(a>>51)&7} "
            f"successful={(a>>54)&1} unbootable={(a>>55)&1}")


def patch_entries(buf, ent_off, nents, entsz):
    """Patch attribute fields in place; return list of (name, old, new)."""
    changed = []
    for i in range(nents):
        o = ent_off + i * entsz
        e = buf[o:o + entsz]
        if len(e) < 128 or e[:16] == b'\x00' * 16:
            continue
        name = e[56:128].decode('utf-16-le').rstrip('\x00')
        if name in TARGET:
            old = struct.unpack('<Q', bytes(e[48:56]))[0]
            new = TARGET[name]
            struct.pack_into('<Q', buf, o + 48, new)
            changed.append((name, old, new))
    return changed


def fix_header(buf, hdr_off, ent_off):
    """Recompute entry-array CRC then header CRC. Order matters."""
    hsize = struct.unpack_from('<I', buf, hdr_off + 12)[0]
    nents = struct.unpack_from('<I', buf, hdr_off + 80)[0]
    entsz = struct.unpack_from('<I', buf, hdr_off + 84)[0]
    ecrc = binascii.crc32(bytes(buf[ent_off:ent_off + nents * entsz])) & 0xffffffff
    struct.pack_into('<I', buf, hdr_off + 88, ecrc)
    struct.pack_into('<I', buf, hdr_off + 16, 0)          # zero before computing
    hcrc = binascii.crc32(bytes(buf[hdr_off:hdr_off + hsize])) & 0xffffffff
    struct.pack_into('<I', buf, hdr_off + 16, hcrc)
    return ecrc, hcrc


def do(src, dst, hdr_lba_off, ent_lba_off, label):
    shutil.copyfile(src, dst)
    buf = bytearray(open(dst, 'rb').read())
    nents = struct.unpack_from('<I', buf, hdr_lba_off + 80)[0]
    entsz = struct.unpack_from('<I', buf, hdr_lba_off + 84)[0]
    print(f"\n=== {label}")
    for name, old, new in patch_entries(buf, ent_lba_off, nents, entsz):
        print(f"  {name}")
        print(f"    before {hex(old)}  {decode(old)}")
        print(f"    after  {hex(new)}  {decode(new)}")
    ecrc, hcrc = fix_header(buf, hdr_lba_off, ent_lba_off)
    print(f"  entries_crc -> {hex(ecrc)}    header_crc -> {hex(hcrc)}")
    open(dst, 'wb').write(bytes(buf))
    print(f"  wrote {dst} ({len(buf)} bytes)")


# Primary: 34 sectors from LBA0. Header at LBA1, entries at LBA2.
do(f"{A9}/gpt-lun4.bin", f"{A9}/gpt-lun4-patched.bin",
   1 * S, 2 * S, "PRIMARY GPT (write to LUN4 sector 0, 34 sectors)")

# Backup: 5 sectors from LBA1048571. Entries at 1048571 (offset 0),
# header at 1048575 (offset 4 sectors in).
do(f"{A9}/gpt-lun4-backup5.bin", f"{A9}/gpt-lun4-backup5-patched.bin",
   4 * S, 0, "BACKUP GPT (write to LUN4 sector 1048571, 5 sectors)")

print("\nVerification pass:")
for f, hoff, eoff in ((f"{A9}/gpt-lun4-patched.bin", S, 2 * S),
                      (f"{A9}/gpt-lun4-backup5-patched.bin", 4 * S, 0)):
    b = open(f, 'rb').read()
    hsize = struct.unpack_from('<I', b, hoff + 12)[0]
    nents = struct.unpack_from('<I', b, hoff + 80)[0]
    entsz = struct.unpack_from('<I', b, hoff + 84)[0]
    ecrc = struct.unpack_from('<I', b, hoff + 88)[0]
    calc_e = binascii.crc32(b[eoff:eoff + nents * entsz]) & 0xffffffff
    hb = bytearray(b[hoff:hoff + hsize]); hb[16:20] = b'\0\0\0\0'
    calc_h = binascii.crc32(bytes(hb)) & 0xffffffff
    stored_h = struct.unpack_from('<I', b, hoff + 16)[0]
    ok = (calc_e == ecrc) and (calc_h == stored_h)
    print(f"  {f.split('/')[-1]:<34} entries_crc {'OK' if calc_e==ecrc else 'BAD'}  "
          f"header_crc {'OK' if calc_h==stored_h else 'BAD'}  -> {'VALID' if ok else 'INVALID'}")
