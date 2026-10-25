#!/usr/bin/env python3
"""Offline stock evidence extraction; writes ONLY --out, never the inputs.
Requires pyelftools only with --elf. See README.md for symbol regeneration.
No phone access and no vendor binaries are stored in the repository.
"""
import argparse
import gzip
import hashlib
import json
import pathlib
import struct

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--boot', type=pathlib.Path, required=True)
p.add_argument('--dtbo', type=pathlib.Path, required=True)
p.add_argument('--out', type=pathlib.Path, required=True)
p.add_argument('--elf', type=pathlib.Path)
a = p.parse_args()
out = a.out.resolve()
# Refuse writing into the backup/reference tree, including via symlinks.
if any(x in out.parts for x in ['a9-assets', 'Backups', 'HLTE556N']):
    p.error('--out must be local scratch, outside all reference data')
out.mkdir(parents=True, exist_ok=True)
boot, dtbo = a.boot.read_bytes(), a.dtbo.read_bytes()
assert boot[:8] == b'ANDROID!'
u32 = lambda off: struct.unpack_from('<I', boot, off)[0]
assert u32(40) == 2
page, kernel_size, ramdisk_size = u32(36), u32(8), u32(16)
raw = gzip.decompress(boot[page:page + kernel_size])
(out / 'Image.raw').write_bytes(raw)
align = lambda n: (n + page - 1) // page * page
off = page + align(kernel_size) + align(ramdisk_size) + align(u32(24))
# Header v2 recovery_dtbo could sit before the DTB; boot_a has none.
assert struct.unpack_from('<I', boot, 1632)[0] == 0
blob = boot[off:off + u32(1648)]
dtb_hashes = []
for i in range(8):
    if not blob:
        break
    assert blob[:4] == bytes.fromhex('d00dfeed')
    n = struct.unpack_from('>I', blob, 4)[0]
    assert 40 <= n <= len(blob)
    b, blob = blob[:n], blob[n:]
    (out / f'base_{i}.dtb').write_bytes(b)
    dtb_hashes.append(hashlib.sha256(b).hexdigest())
assert dtbo[:4] == bytes.fromhex('d7b7ab1e')
entry_size, entries, entry_off = struct.unpack_from('>III', dtbo, 12)
assert entries == 1 and entry_size >= 32
size, off = struct.unpack_from('>II', dtbo, entry_off)
overlay = dtbo[off:off + size]
assert overlay[:4] == bytes.fromhex('d00dfeed') and len(overlay) == size
(out / 'dtbo_0.dtb').write_bytes(overlay)
config = gzip.decompress(raw[raw.index(b'IKCFG_ST') + 8:raw.index(b'IKCFG_ED')]).decode()
selected_config = [line for line in config.splitlines() if any(key in line for key in
                   ['CONFIG_QPNP_SMB5=', 'CONFIG_QPNP_QG=',
                    'CONFIG_HISENSE_CHARGE_FG_FUNCTION=', 'CONFIG_HISENSE_BATTERY_THERM_NTC_10K='])]
report = {'stock_config': selected_config, 'source': "phone's golden boot_a/dtbo_a, 2026-10-14_110759",
          'raw_image_sha256': hashlib.sha256(raw).hexdigest(),
          'raw_image_bytes': len(raw), 'base_dtb_sha256': dtb_hashes,
          'dtbo_entry_sha256': hashlib.sha256(overlay).hexdigest(),
          'dtbo_entry_bytes': len(overlay)}
if a.elf:
    from elftools.elf.elffile import ELFFile
    with a.elf.open('rb') as f:
        elf = ELFFile(f)
        syms = {s.name: s['st_value'] for s in elf.get_section_by_name('.symtab').iter_symbols()}
        segs = [(s['p_vaddr'], s['p_offset'], s['p_filesz']) for s in elf.iter_segments()
                if s['p_type'] == 'PT_LOAD']

        def read(va, n):
            for v, off, sz in segs:
                if v <= va and va + n <= v + sz:
                    f.seek(off + va - v)
                    return f.read(n)
            raise ValueError(hex(va))

        params = []
        va = syms['smb5_pmi632_params']
        # Stock struct smb_chg_param is 40 B (NOT 48 B).
        for i in range(12):
            b = read(va + 40 * i, 40)
            params.append({'reg': hex(struct.unpack_from('<H', b, 8)[0]),
                           'min': struct.unpack_from('<i', b, 12)[0],
                           'max': struct.unpack_from('<i', b, 16)[0],
                           'step': struct.unpack_from('<i', b, 20)[0]})
        report['smb5_pmi632_params_va'] = hex(va)
        report['smb5_pmi632_params'] = params
        va = syms['adcmap_batt_therm_30k_NCP_XH103']
        report['thermistor_table_va'] = hex(va)
        report['thermistor_mv_decic'] = [struct.unpack('<ii', read(va + 8 * i, 8)) for i in range(70)]
        # vmlinux-to-elf leaves static data pointers zero. Recover RELATIVE
        # relocation addends from the original Image; do NOT infer zero pointers.
        lut_va = syms['lut_table_30']
        pointers = {}
        for i in range(78379):
            dst, kind, addend = struct.unpack_from('<QQQ', raw, 0x1cb4e30 + i * 24)
            if lut_va <= dst < lut_va + 48 and kind == 0x403:
                pointers[hex(dst)] = hex(addend)
        report['lut_table_30_relative_relocations'] = pointers
        assert pointers[hex(lut_va)] == hex(va)
        report['stock_functions'] = {name: hex(syms[name]) for name in [
            'qg_get_battery_voltage', 'qg_get_battery_current', 'qg_get_battery_temp',
            'smblib_get_prop_usb_present', 'smblib_get_prop_batt_present',
            'smblib_get_prop_batt_health', 'smblib_update_jeita',
            'adc_probe', 'adc_read_raw', 'qcom_vadc_hw_scale']}
print(json.dumps(report, indent=2))
