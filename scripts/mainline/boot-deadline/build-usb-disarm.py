#!/usr/bin/env python3
"""Host-only staged refresh of all three WDT packages from our fix3 cache.

Does not replace the published packages: publish completed staged directories
only after checking results. Preserve pre-refresh packages for provenance.
"""
from pathlib import Path
import hashlib
import importlib.util
import json
import shutil
import struct
import build as b

STAGE = b.ROOT / 'usb-disarm-20261022'
spec = importlib.util.spec_from_file_location('wdt', b.HERE / 'build-wdt.py')
wdt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wdt)


def repack(parent, out, name):
    raw = (parent / name).read_bytes()
    page = struct.unpack_from('<I', raw, 36)[0]
    pos = page
    fields = (8, 16, 24, 1632, 1648)
    blobs = []
    for f in fields:
        size = struct.unpack_from('<I', raw, f)[0]
        blobs.append(raw[pos:pos + size])
        pos += (size + page - 1) // page * page
    original = blobs.copy()
    blobs[0] = (out / 'Image.gz').read_bytes()
    header = bytearray(raw[:page])
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
    (out / name).write_bytes(header + body)
    (out / (name + '.sha256')).write_text(b.sha(out / name) + '  ' + name + '\n')
    assert blobs[1:] == original[1:]
    assert header[64:576] == raw[64:576] and header[608:1632] == raw[608:1632]


def refresh(name):
    parent = b.ROOT / name
    out = STAGE / name
    shutil.copytree(parent, out)
    b.OUT = out
    print('VARIANT ' + name, flush=True)
    b.run(wdt.make_command() + ['olddefconfig'], 'usb-olddefconfig')
    assert (b.OBJ / '.config').read_bytes() == (parent / 'kernel.config').read_bytes()
    b.run(wdt.make_command() + ['Image.gz'], 'usb-kernel-build')
    for src, dst in [('arch/arm64/boot/Image.gz', 'Image.gz'), ('.config', 'kernel.config'), ('vmlinux.symvers', 'vmlinux.symvers')]:
        shutil.copyfile(b.OBJ / src, out / dst)
    def exports(p):
        return {r[1]: r[0] for line in p.read_text().splitlines() if len(r := line.split()) >= 4}
    old, new = exports(parent / 'vmlinux.symvers'), exports(out / 'vmlinux.symvers')
    bad = {n: [v, new.get(n)] for n, v in old.items() if new.get(n) != v}
    assert not bad, bad
    (out / 'abi.json').write_text(json.dumps(dict(existing_exports=len(old), mismatches=bad)) + '\n')
    for rel in ['init/a9_boot_deadline.c', 'include/linux/a9_boot_deadline.h',
                'drivers/usb/gadget/udc/core.c', 'drivers/watchdog/qcom-wdt.c',
                'drivers/power/reset/qcom-pon.c', 'drivers/firmware/qcom/qcom_scm.c',
                'drivers/soc/qcom/ice.c', 'drivers/ufs/host/ufs-qcom.c']:
        p = out / 'source' / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(b.K / rel, p)
    for name in ['a9_boot_deadline-wdt.c', 'a9_boot_deadline-wdt.h', 'build-wdt.py', 'build-usb-disarm.py', 'README.txt']:
        shutil.copyfile(b.HERE / name, out / 'source' / name)
    for name in ['boot.img', 'boot-hang.img']:
        repack(parent, out, name)
    b.run(['python3', '-P', b.REPO / 'scripts/abl-check.py', out / 'boot.img', 'boot',
           '--dtbo', out / 'recovery_dtbo.img', '--json'], 'usb-abl')
    assert json.loads((out / 'usb-abl.log').read_text())['result'] == 'PASS'
    if (out / 'vendor.img').exists():
        spec = importlib.util.spec_from_file_location('prepare', b.HERE / 'prepare-vendor.py')
        prepare = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prepare)
        prepare.prepare(out / 'boot.img', out / 'vendor.img', out / 'vendor.img', out / 'usb-vendor-refresh')
    result = dict(parent=str(parent), previous_boot_sha256=b.sha(parent / 'boot.img'),
                  sha256={p: b.sha(out / p) for p in ['boot.img', 'boot-hang.img', 'Image.gz']},
                  disarm_requires=['boot_completed_request', 'actual_UDC_USB_STATE_CONFIGURED'],
                  notification='usb_gadget_set_state under state_lock; dedicated keeper performs handoff',
                  kernel_export_crcs_unchanged=True, abl_result='PASS',
                  nonkernel_payloads_and_cmdline_unchanged=True, hardware_tested=False, phone_access=False)
    (out / 'usb-disarm.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)


def main():
    assert shutil.disk_usage(b.ROOT).free > 16 * 1024**3
    previous = b.ROOT / 'plain-wrapped-fix3-wdt-selftest-20261022'
    assert b.sha(b.OBJ / 'arch/arm64/boot/Image.gz') == b.sha(previous / 'Image.gz'), 'cache changed'
    for p in (previous / 'source').rglob('*'):
        if p.is_file() and p.relative_to(previous / 'source').parts[0] in ('init', 'include', 'drivers'):
            assert b.sha(b.K / p.relative_to(previous / 'source')) == b.sha(p), str(p)
    STAGE.mkdir()
    b.materialize(b.K / 'init/a9_boot_deadline.c', (b.HERE / 'a9_boot_deadline-wdt.c').read_bytes())
    b.materialize(b.K / 'include/linux/a9_boot_deadline.h', (b.HERE / 'a9_boot_deadline-wdt.h').read_bytes())
    wdt.inject_usb()
    refresh('plain-wrapped-fix3-wdt-20261022')
    parent = b.ROOT / 'plain-wrapped-fix3-20261022'
    manifest = json.loads((parent / 'source.json').read_text())
    for rel, hashes in manifest['changed'].items():
        assert b.sha(b.K / rel) == hashes['after']
        original = parent / 'parent-sources' / rel
        assert b.sha(original) == hashes['before']
        b.materialize(b.K / rel, original.read_bytes())
    refresh('plain-wrapped-deadline-logfix1-wdt-20261022')
    for patch in [wdt.PATCHES / 'metadata/dm-default-key-7.2.patch', wdt.PATCHES / 'ice-wrapped/0001-a9-ice31-legacy-wrapped.patch']:
        b.run(['patch', '--batch', '--fuzz=0', '-R', '-p1', '-d', b.K, '-i', patch], patch.stem + '-usb-undo')
    parent = b.ROOT / 'plain-deadline-logfix1-wdt-20261022'
    shutil.copyfile(parent / 'kernel.config', b.OBJ / '.config')
    b.materialize(b.K / 'drivers/watchdog/qcom-wdt.c', (parent / 'source/drivers/watchdog/qcom-wdt.c').read_bytes())
    refresh(parent.name)
    print('ALL STAGED; publish only after final host artifact checks', flush=True)


if __name__ == '__main__':
    main()
