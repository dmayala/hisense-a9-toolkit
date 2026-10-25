#!/usr/bin/env python3
"""Offline opt-in vendor payload. No partition mounts or radio access."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def install(source, target, mode):
    if target.is_symlink() or any(p.is_symlink() for p in target.parents):
        raise ValueError('unsafe vendor staging symlink')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(mode)


def firmware_sums(qca):
    """Runtime manifest for a9ml-bluetooth.sh: the pinned hashes, in sha256sum -c
    format, for exactly the files staged (no extra, no missing, no altered file)."""
    fw = module('bt_firmware_sums', HERE/'firmware.py')
    names = sorted(p.name for p in qca.iterdir())
    if names != sorted(fw.FILE_HASHES):
        raise ValueError('firmware set differs from the pinned set')
    rows = []
    for name in names:
        src = qca/name
        if src.is_symlink() or not src.is_file():
            raise ValueError('firmware input is not a regular file')
        if fw.digest(src.read_bytes()) != fw.FILE_HASHES[name]:
            raise ValueError('firmware differs from pinned golden content')
        rows.append(f'{fw.FILE_HASHES[name]}  {name}\n')
    return ''.join(rows)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--firmware', type=Path, required=True)
    p.add_argument('--hal-bundle', type=Path, required=True)
    p.add_argument('--snapshot', type=Path, required=True)
    p.add_argument('--vendor-stage', type=Path, required=True)
    p.add_argument('--tools', type=Path, required=True)
    # integ-1: a GPU snapshot (A9_GPU=1 snapshot-kernel.sh) keeps its DTB as
    # sm6115-hisense-hlte556n-android-gpu.dtb; build-vendor.sh --gpu passes it
    p.add_argument('--dtb-name', default='sm6115-hisense-hlte556n-android.dtb',
                   choices=('sm6115-hisense-hlte556n-android.dtb', 'sm6115-hisense-hlte556n-android-gpu.dtb'))
    a = p.parse_args()
    archive = (Path.home()/'a9-assets').resolve()
    stage = a.vendor_stage.resolve()
    if stage == Path('/') or archive == stage or archive in stage.parents:
        raise ValueError('unsafe vendor output')
    kernel = module('bt_kernel_assets', HERE/'kernel-assets.py')
    expected = kernel.firmware(a.firmware)
    sums = firmware_sums(a.firmware/'qca')
    kernel.verify(a.snapshot/'config',
                  a.snapshot/a.dtb_name, a.firmware)
    kernel.verify_driver(a.snapshot/'modules/hci_uart.ko')
    if (a.snapshot/'bluetooth.enabled').read_text() != '1\n':
        raise ValueError('snapshot was not made with Bluetooth enabled')
    if not (stage/'lib/modules/modules.bluetooth').is_file():
        raise ValueError('Bluetooth snapshot modules were not installed')
    hal = module('bt_stage_hal', HERE/'android/stage-hal.py')
    hal.inspect(a.hal_bundle.resolve(), stage)
    tool = a.tools/'a9-bt-identity'
    if tool.is_symlink() or not tool.is_file():
        raise ValueError('missing identity gate binary')
    hal.elf_needed(tool.read_bytes())  # verifies AArch64/ELF64; NDK libc-only tool
    # Validate all inputs before any Bluetooth payload or feature mutation.
    subprocess.run([sys.executable, str(HERE/'android/stage-hal.py'),
                    '--bundle', str(a.hal_bundle), '--vendor-stage', str(stage)], check=True)
    for src in sorted((a.firmware/'qca').iterdir()):
        install(src, stage/'firmware/qca'/src.name, 0o644)
    # firmware_class.path (E Ink cmdline) is a single directory, so setup copies
    # these files there and checks them against this list before and after load.
    sums_out = stage/'etc/bluetooth/firmware.sha256'
    if sums_out.is_symlink(): raise ValueError('firmware manifest output is a symlink')
    sums_out.parent.mkdir(parents=True, exist_ok=True)
    sums_out.write_text(sums)
    sums_out.chmod(0o644)
    identity = stage/'etc/bluetooth/local-bd-address'
    if identity.is_symlink(): raise ValueError('identity output is a symlink')
    identity.parent.mkdir(parents=True, exist_ok=True)
    identity.write_bytes(expected)
    identity.chmod(0o640)
    install(tool, stage/'bin/a9-bt-identity', 0o755)
    install(HERE/'a9ml-bluetooth.sh', stage/'bin/a9ml-bluetooth.sh', 0o755)
    install(HERE/'init.a9-bluetooth.rc', stage/'etc/init/init.a9-bluetooth.rc', 0o644)
    for src in sorted((HERE/'android/vendor').rglob('*')):
        if src.is_file(): install(src, stage/src.relative_to(HERE/'android/vendor'), 0o644)
    # This override suppresses Bluetooth in the default build. Remove it only
    # after the actual HAL, firmware, private identity and modules all validated.
    unavailable = stage/'etc/permissions/a9ml-unavailable-features.xml'
    unavailable.unlink()
    print('Opt-in Bluetooth vendor payload staged; private identity withheld.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError):
        print('Bluetooth vendor staging failed; do not use partial stage.', file=sys.stderr)
        sys.exit(1)
