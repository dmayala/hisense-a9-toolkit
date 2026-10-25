#!/usr/bin/env python3
"""
Decode A/B slot state from a Hisense A9 LUN4 GPT dump.

The bootloader records which slot is active, how many boot attempts remain,
and whether a slot has been marked unbootable in the GPT *partition attribute
bits* of boot_a / boot_b -- not in misc, and not in any regular partition.
Qualcomm's bootctrl (gpt-utils) layout:

    bit 48-49  priority        (higher wins)
    bit 50     active
    bit 51-53  tries remaining
    bit 54     successful
    bit 55     unbootable

Note the A9 uses UFS with 4096-byte sectors, and each UFS LUN has its own
independent GPT -- hence gpt_main0..5.bin in the stock package.

Usage:  parse-slot-attrs.py <gpt-lun4.bin>
Get the dump with:  ./edl-cli.sh slots   (dumps LUN4 and runs this)
"""
import struct, sys

SECTOR = 4096

def main(path):
    d = open(path, 'rb').read()
    off = None
    for cand in (SECTOR, 512):
        if d[cand:cand + 8] == b'EFI PART':
            off = cand
            break
    if off is None:
        sys.exit(f"no GPT header found in {path} (checked 512 and 4096 byte sectors)")

    hdr = d[off:off + 92]
    ents_lba = struct.unpack('<Q', hdr[72:80])[0]
    nents = struct.unpack('<I', hdr[80:84])[0]
    entsz = struct.unpack('<I', hdr[84:88])[0]
    base = ents_lba * off

    print(f"GPT header at 0x{off:x}, {nents} entries of {entsz} bytes\n")
    print("%-18s %20s  %s" % ("name", "attributes", "slot state"))
    print("-" * 78)

    slots = {}
    for i in range(nents):
        e = d[base + i * entsz: base + (i + 1) * entsz]
        if len(e) < 128 or e[:16] == b'\x00' * 16:
            continue
        name = e[56:128].decode('utf-16-le').rstrip('\x00')
        attr = struct.unpack('<Q', e[48:56])[0]
        if not (name.startswith('boot_') or name.startswith('recovery_')):
            continue
        s = dict(priority=(attr >> 48) & 0x3, active=(attr >> 50) & 1,
                 tries=(attr >> 51) & 0x7, successful=(attr >> 54) & 1,
                 unbootable=(attr >> 55) & 1)
        slots[name] = s
        print("%-18s %20s  priority=%d active=%d tries=%d successful=%d unbootable=%d"
              % (name, hex(attr), s['priority'], s['active'], s['tries'],
                 s['successful'], s['unbootable']))

    print()
    a, b = slots.get('boot_a'), slots.get('boot_b')
    if a and b:
        if b['unbootable']:
            print("*** boot_b is marked UNBOOTABLE -- the bootloader gave up on slot B.")
        if a['active'] and not b['active']:
            print("*** Slot A is ACTIVE. If the GSI lives on slot B, slot A likely has")
            print("    no usable system (GSI installs commonly delete system_a for space),")
            print("    which produces exactly a bootloop.")
        if b['active'] and not a['active']:
            print("Slot B is still active -- the slot-switch theory is WRONG; look elsewhere.")

if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
