#!/usr/bin/env python3
"""Host-only opt-in all-CPU hard-hang image on the worker's fix3 WDT cache."""
from pathlib import Path
import hashlib
import importlib.util
import json
import shutil
import struct
import build as b


def main():
    parent = b.ROOT / 'plain-wrapped-fix3-wdt-20261022'
    out = b.ROOT / 'plain-wrapped-fix3-wdt-selftest-20261022'
    b.room(400 * 1024**2)
    assert b.sha(b.OBJ / 'arch/arm64/boot/Image.gz') == b.sha(parent / 'Image.gz'), 'cache changed'
    assert (b.OBJ / '.config').read_bytes() == (parent / 'kernel.config').read_bytes()
    for p in (parent / 'source').rglob('*'):
        if p.is_file() and p.relative_to(parent / 'source').parts[0] in ('init', 'include', 'drivers'):
            assert b.sha(b.K / p.relative_to(parent / 'source')) == b.sha(p), str(p)
    out.mkdir()
    b.OUT = out
    b.materialize(b.K / 'init/a9_boot_deadline.c', (b.HERE / 'a9_boot_deadline-wdt.c').read_bytes())
    spec = importlib.util.spec_from_file_location('wdt', b.HERE / 'build-wdt.py')
    wdt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(wdt)
    b.run(wdt.make_command() + ['Image.gz'], 'kernel-build')
    for src, dst in [('arch/arm64/boot/Image.gz', 'Image.gz'), ('.config', 'kernel.config'), ('vmlinux.symvers', 'vmlinux.symvers')]:
        shutil.copyfile(b.OBJ / src, out / dst)
    assert (out / 'kernel.config').read_bytes() == (parent / 'kernel.config').read_bytes()

    def exports(p):
        return {r[1]: r[0] for line in p.read_text().splitlines() if len(r := line.split()) >= 4}
    old, new = exports(parent / 'vmlinux.symvers'), exports(out / 'vmlinux.symvers')
    bad = {n: [crc, new.get(n)] for n, crc in old.items() if new.get(n) != crc}
    assert not bad, bad
    (out / 'abi.json').write_text(json.dumps(dict(existing_exports=len(old), mismatches=bad)) + '\n')

    raw = (parent / 'boot.img').read_bytes()
    page = struct.unpack_from('<I', raw, 36)[0]
    assert raw[:8] == b'ANDROID!' and page == 4096
    fields = (8, 16, 24, 1632, 1648)
    pos = page
    original = []
    for f in fields:
        size = struct.unpack_from('<I', raw, f)[0]
        original.append(raw[pos:pos + size])
        pos += (size + page - 1) // page * page
    primary = raw[64:576].split(b'\0')[0].decode().split()
    extra = raw[608:1632].split(b'\0')[0].decode().split()
    assert 'a9_deadline=300' in primary and not any(x.startswith('a9_deadline_selftest=') for x in primary + extra)
    # ABL may ignore extra_cmdline. Move only the Wi-Fi firmware search path
    # there; the test freezes before PID1 and never loads Wi-Fi firmware.
    moved = [x for x in primary if x.startswith('firmware_class.path=')]
    primary = ['a9_deadline_selftest=hang'] + [x for x in primary if x not in moved]
    extra = moved + extra
    assert len(' '.join(primary).encode()) < 512 and len(' '.join(extra).encode()) < 1024
    header = bytearray(raw[:page])
    header[64:576] = ' '.join(primary).encode().ljust(512, b'\0')
    header[608:1632] = ' '.join(extra).encode().ljust(1024, b'\0')
    blobs = original.copy()
    blobs[0] = (out / 'Image.gz').read_bytes()
    body = bytearray()
    digest = hashlib.sha1()
    for f, blob in zip(fields, blobs):
        if f == 1632:
            struct.pack_into('<Q', header, 1636, page + len(body))
        struct.pack_into('<I', header, f, len(blob))
        digest.update(blob)
        digest.update(struct.pack('<I', len(blob)))
        body += blob
        body += bytes(-len(body) % page)
    header[576:608] = digest.digest() + bytes(12)
    (out / 'boot.img').write_bytes(header + body)
    (out / 'recovery_dtbo.img').write_bytes(blobs[3])
    assert blobs[1:] == original[1:]
    shutil.copytree(parent / 'source', out / 'source')
    shutil.copyfile(b.K / 'init/a9_boot_deadline.c', out / 'source/init/a9_boot_deadline.c')
    for name in ['build-wdt-selftest.py', 'a9_boot_deadline-wdt.c', 'b-try.sh', 'README.txt']:
        shutil.copyfile(b.HERE / name, out / 'source' / name)
    for name in ['boot.img', 'Image.gz']:
        (out / (name + '.sha256')).write_text(b.sha(out / name) + '  ' + name + '\n')
    (out / 'provenance.json').write_text(json.dumps(dict(
        parent_boot=str(parent / 'boot.img'), parent_sha256=b.sha(parent / 'boot.img'),
        changed_payloads=['kernel'], unchanged_payloads=['ramdisk', 'second', 'recovery_dtbo', 'dtb'],
        primary_cmdline=primary, extra_cmdline=extra, selftest='all online CPUs IRQ-off spin at late_initcall_sync',
        software_deadline_seconds=300, hardware_bite_seconds_since_last_pet=30,
        hardware_tested=False, phone_access=False), indent=2) + '\n')
    b.run(['python3', '-P', b.REPO / 'scripts/abl-check.py', out / 'boot.img', 'boot',
           '--dtbo', out / 'recovery_dtbo.img', '--json'], 'abl-check')
    print(out, b.sha(out / 'boot.img'), flush=True)


if __name__ == '__main__':
    main()
