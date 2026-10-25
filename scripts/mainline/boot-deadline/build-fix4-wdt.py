#!/usr/bin/env python3
"""Host-only fix4 ABI plus APSS watchdog and actual-USB-gated disarm."""
from pathlib import Path
import importlib.util
import json
import shutil
import build as b


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, b.HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parent = b.ROOT / 'plain-wrapped-fix4-20261022'
    previous = b.ROOT / 'plain-deadline-logfix1-wdt-20261022'
    out = b.ROOT / 'plain-wrapped-fix4-wdt-20261022'
    assert shutil.disk_usage(b.ROOT).free > 16 * 1024**3
    assert b.sha(b.OBJ / 'arch/arm64/boot/Image.gz') == b.sha(previous / 'Image.gz'), 'cache changed'
    assert (b.OBJ / '.config').read_bytes() == (previous / 'kernel.config').read_bytes()
    for p in (previous / 'source').rglob('*'):
        if p.is_file() and p.relative_to(previous / 'source').parts[0] in ('init', 'include', 'drivers'):
            assert b.sha(b.K / p.relative_to(previous / 'source')) == b.sha(p), str(p)
    out.mkdir()
    b.OUT = out
    wdt = load('wdt', 'build-wdt.py')
    usb = load('usb', 'build-usb-disarm.py')
    for patch in [wdt.PATCHES / 'ice-wrapped/0001-a9-ice31-legacy-wrapped.patch',
                  wdt.PATCHES / 'metadata/dm-default-key-7.2.patch']:
        for line in patch.read_text().splitlines():
            if line.startswith('+++ b/'):
                p = b.K / line[6:].split('\t')[0]
                if p.is_symlink():
                    b.materialize(p, p.read_bytes())
        b.run(['patch', '--batch', '--fuzz=0', '-p1', '-d', b.K, '-i', patch], patch.stem + '-apply')
    fix3 = b.ROOT / 'plain-wrapped-fix3-20261022'
    changes = json.loads((fix3 / 'source.json').read_text())['changed']
    for rel, meta in changes.items():
        assert b.sha(b.K / rel) == meta['before'], rel
        replacement = parent / 'source' / rel
        assert b.sha(replacement) == meta['after'], rel
        b.materialize(b.K / rel, replacement.read_bytes())
    manifest = json.loads((parent / 'source.json').read_text())
    for rel, meta in manifest['files'].items():
        assert b.sha(b.K / rel) == meta['before'], rel
        replacement = parent / 'source' / rel
        assert b.sha(replacement) == meta['after'], rel
        b.materialize(b.K / rel, replacement.read_bytes())
    b.materialize(b.K / 'drivers/watchdog/qcom-wdt.c',
                  (b.ROOT / 'plain-wrapped-fix3-wdt-20261022/source/drivers/watchdog/qcom-wdt.c').read_bytes())
    assert (b.K / 'init/a9_boot_deadline.c').read_bytes() == (b.HERE / 'a9_boot_deadline-wdt.c').read_bytes()
    assert (b.K / 'include/linux/a9_boot_deadline.h').read_bytes() == (b.HERE / 'a9_boot_deadline-wdt.h').read_bytes()
    assert 'a9_boot_wdt_usb_configured' in (b.K / 'drivers/usb/gadget/udc/core.c').read_text()
    shutil.copyfile(parent / 'kernel.config', b.OBJ / '.config')
    b.run(wdt.make_command() + ['olddefconfig'], 'olddefconfig')
    assert (b.OBJ / '.config').read_bytes() == (parent / 'kernel.config').read_bytes()
    b.run(wdt.make_command() + ['Image.gz'], 'kernel-build')
    for src, dst in [('arch/arm64/boot/Image.gz', 'Image.gz'), ('.config', 'kernel.config'), ('vmlinux.symvers', 'vmlinux.symvers')]:
        shutil.copyfile(b.OBJ / src, out / dst)
    def exports(p):
        return {r[1]: r[0] for line in p.read_text().splitlines() if len(r := line.split()) >= 4}
    actual = exports(out / 'vmlinux.symvers')
    abi = {}
    for reference in [parent, b.ROOT / 'plain-integ-20261022']:
        expected = exports(reference / 'vmlinux.symvers')
        bad = {n: [v, actual.get(n)] for n, v in expected.items() if actual.get(n) != v}
        abi[reference.name] = dict(existing_exports=len(expected), mismatches=bad)
        assert not bad, (reference, bad)
    (out / 'abi.json').write_text(json.dumps(abi, indent=2) + '\n')
    usb.repack(parent, out, 'boot.img')
    shutil.copyfile(parent / 'recovery_dtbo.img', out / 'recovery_dtbo.img')
    for rel in ['init/a9_boot_deadline.c', 'include/linux/a9_boot_deadline.h',
                'drivers/usb/gadget/udc/core.c', 'drivers/watchdog/qcom-wdt.c',
                'drivers/power/reset/qcom-pon.c', 'drivers/firmware/qcom/qcom_scm.c',
                *changes, *manifest['files']]:
        p = out / 'source' / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(b.K / rel, p)
    for name in ['a9_boot_deadline-wdt.c', 'a9_boot_deadline-wdt.h', 'build-fix4-wdt.py', 'build-wdt.py', 'README.txt']:
        shutil.copyfile(b.HERE / name, out / 'source' / name)
    shutil.copyfile(parent / 'bio-abi.patch', out / 'bio-abi.patch')
    shutil.copyfile(parent / 'audit-modules.py', out / 'audit-modules.py')
    b.run(['python3', out / 'audit-modules.py', out, 'fix4-wdt', parent / 'vendor.img',
           b.ROOT / 'plain-integ-20261022/vendor.img'], 'module-audit')
    audit = json.loads((out / 'module-audit-fix4-wdt.json').read_text())
    assert all(v['failing_modules'] == 0 for v in audit['vendors'].values()), audit
    b.run(['python3', '-P', b.REPO / 'scripts/abl-check.py', out / 'boot.img', 'boot',
           '--dtbo', out / 'recovery_dtbo.img', '--json'], 'abl-check')
    assert json.loads((out / 'abl-check.log').read_text())['result'] == 'PASS'
    prepare = load('prepare', 'prepare-vendor.py')
    prepare.prepare(out / 'boot.img', parent / 'vendor.img', out / 'vendor.img', out / 'vendor-overlay')
    result = dict(base_boot=str(parent / 'boot.img'), base_sha256=b.sha(parent / 'boot.img'),
                  sha256={p: b.sha(out / p) for p in ['boot.img', 'Image.gz', 'vendor.img']},
                  abi=abi, module_audit={n: {k: v for k, v in d.items() if k != 'details'} for n, d in audit['vendors'].items()},
                  config_unchanged=True, nonkernel_payloads_and_cmdline_unchanged=True,
                  disarm_requires=['boot_completed_request', 'actual_UDC_USB_STATE_CONFIGURED'],
                  hardware_tested=False, phone_access=False)
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    main()
