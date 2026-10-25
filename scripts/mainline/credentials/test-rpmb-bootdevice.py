#!/usr/bin/env python3
"""Focused offline checks of RPMB discovery setup against a saved vendor RC."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess as sp

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('credentials', HERE / 'prepare-vendor.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vendor', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    original = m.read(args.vendor, 'etc/init/hw/init.hlte556n.rc')
    anchor = b'    start vendor.qseecomd\n'
    diag = b'    start a9_rpmb_diag\n'
    setup = (b'    # Stock librpmb selects UFS from this legacy directory link.\n'
             b'    wait /dev/block/platform/soc@0/4804000.ufshc 5\n'
             b'    symlink /dev/block/platform/soc@0/4804000.ufshc /dev/block/bootdevice\n')

    # Run the same ordered composition used in prepare(), including the existing
    # diagnostic startup. Re-preparation must not duplicate or reorder services.
    def transform(data):
        data = m.persist_ro_startup(m.omit_legacy_qsee_directory(data))
        data = data.replace(diag + anchor, anchor)
        data = m.rpmb_bootdevice_startup(data)
        return data.replace(anchor, diag + anchor)

    changed = transform(original)
    assert transform(changed) == changed
    assert changed.count(setup) == changed.count(diag) == changed.count(anchor) == 1
    assert changed.index(b'    exec_start a9ml_metadata_mount\n') < changed.index(setup)
    assert changed.index(b'    exec_start a9ml_persist_ro\n') < changed.index(setup)
    assert setup + diag + anchor in changed
    # All unrelated bytes survive; the earlier independent transforms may only
    # remove their known inherited directives or insert their existing services.
    baseline = m.persist_ro_startup(m.omit_legacy_qsee_directory(original))
    baseline = baseline.replace(diag + anchor, anchor).replace(anchor, diag + anchor)
    assert changed.replace(setup, b'') == baseline
    checks = {'actual_rc_order_idempotence_unrelated_bytes': 'PASS'}

    # Ambiguous imported startup must fail host packaging rather than silently
    # choose another controller, or add a link after a different daemon start.
    for data in (b'on fs\n', original + anchor,
                 original + b'    symlink /dev/block/other /dev/block/bootdevice\n',
                 original + setup + setup):
        try:
            m.rpmb_bootdevice_startup(data)
        except ValueError:
            pass
        else:
            raise AssertionError('ambiguous RPMB startup accepted')
    checks['ambiguous_startup_rejected'] = 'PASS'

    # Actual Android init parser, no service/command execution or device access.
    rc = args.out / 'combined.rc'
    rc.write_bytes(changed + b'\n' +
                   (HERE / 'init.a9-persist-ro.rc').read_bytes() + b'\n' +
                   (m.DIAGNOSTICS / 'init.a9-rpmb-diag.rc').read_bytes())
    result = sp.run([str(m.TOOLS / 'host_init_verifier'), str(rc)],
                    capture_output=True, timeout=30)
    (args.out / 'init-verifier.log').write_bytes(result.stdout + result.stderr)
    assert result.returncode == 0, 'init verifier failed; inspect local log'
    checks['android_init_verifier'] = 'PASS'
    (args.out / 'tests.json').write_text(json.dumps(checks, indent=2) + '\n')
    print(json.dumps(checks, indent=2))


if __name__ == '__main__':
    main()
