#!/usr/bin/env python3
"""Five focused Android mksh mount-flow checks; synthetic command interfaces."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess as sp

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('prepare_credentials', HERE / 'prepare-vendor.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--out', type=Path, required=True)
p.add_argument('--vendor', type=Path, required=True)
a = p.parse_args()
a.out.mkdir(parents=True, exist_ok=True)
source = (HERE.parent / 'reboot/vendor/bin/a9ml-wrapped-start.sh').read_text()
assert 'ro,noload,nosuid,nodev,noexec' in source
assert '/metadata/a9-tee-persist' not in source
assert 'restorecon -R /mnt/vendor/persist' not in source
shell = '/home/user/A9-build/mainline/sol-wifi/target-audit/bin/sh'
results = {}
for mode in ('ok', 'ram_mountpoint', 'occupied', 'setro_failed', 'mount_failed', 'bad_options'):
    root = a.out / mode
    commands = root / 'commands'
    commands.mkdir(parents=True, exist_ok=True)
    mountpoint = root / 'mount'
    mountpoint.mkdir(exist_ok=True)
    if mode == 'ram_mountpoint':
        mountpoint.rmdir()
    device = root / 'synthetic-node'
    device.write_bytes(b'SYNTHETIC\n')
    mounts = root / 'mountinfo'
    mounts.write_text('1 0 0:1 / / ro - rootfs rootfs ro\n')
    if mode == 'ram_mountpoint':
        mounts.write_text('1 0 0:1 / / ro - rootfs rootfs ro\n2 1 0:2 / /mnt rw - tmpfs tmpfs rw\n')
    if mode == 'occupied':
        mounts.write_text(f'17 1 8:1 / {mountpoint} ro - ext4 synthetic ro,norecovery\n')
    bodies = {
        'stat': 'printf "8 1\\n"',
        'ls': 'exit 0',
        'mkdir': '''printf 'ram-mountpoint\\n' >>"$TEST_CALLS"
exec /run/current-system/sw/bin/mkdir "$@"''',
        'blockdev': '''printf '%s\\n' "$1" >>"$TEST_CALLS"
case "$1" in
--setro) [ "$TEST_MODE" != setro_failed ] || exit 9; printf 1 >"$TEST_RO" ;;
--getro) cat "$TEST_RO" ;;
*) exit 70 ;;
esac''',
        'mount': '''printf 'mount %s\\n' "$*" >>"$TEST_CALLS"
[ "$TEST_MODE" != mount_failed ] || exit 32
options=ro,norecovery
[ "$TEST_MODE" != bad_options ] || options=ro
printf '17 1 8:1 / %s ro,nosuid,nodev,noexec - ext4 synthetic %s\\n' "$TEST_MOUNT" "$options" >>"$TEST_MOUNTS"''',
        'log': 'printf "%s\\n" "$*" >>"$TEST_LOG"',
    }
    for name, body in bodies.items():
        f = commands / name
        f.write_text('#!/bin/sh\n' + body + '\n')
        f.chmod(0o755)
    script = source.replace('/dev/block/by-name/persist', str(device))
    script = script.replace('/mnt/vendor/persist', str(mountpoint))
    script = script.replace('/proc/self/mountinfo', str(mounts))
    script = script.replace('/dev/kmsg', str(root / 'kmsg'))
    # The host fixture is a regular file; production still requires a block node.
    assert '[ -b "$persist_dev" ]' in script
    script = script.replace('[ -b "$persist_dev" ]', '[ -f "$persist_dev" ]')
    for name in bodies:
        script = script.replace('/system/bin/' + name, str(commands / name))
    script = script.replace('/system/bin/timeout', '/run/current-system/sw/bin/timeout')
    env = dict(os.environ, TEST_MODE=mode, TEST_RO=str(root / 'ro'),
               TEST_CALLS=str(root / 'calls'), TEST_LOG=str(root / 'logcat'),
               TEST_MOUNT=str(mountpoint), TEST_MOUNTS=str(mounts))
    r = sp.run([shell, '-c', script, 'persist-fixture', 'persist'], env=env,
               text=True, capture_output=True, timeout=8)
    (root / 'stderr').write_text(r.stderr)
    calls = (root / 'calls').read_text() if (root / 'calls').exists() else ''
    record = (root / 'kmsg').read_text().removeprefix('a9ml-wrapped: ')
    if mode in ('ok', 'ram_mountpoint'):
        assert r.returncode == 0 and record.endswith('phase=3 rc=0\n'), r.stderr
        assert calls.count('--setro\n') == 1 and calls.count('--getro\n') == 2
        assert '-t ext4 -o ro,noload,nosuid,nodev,noexec ' in calls
        if mode == 'ram_mountpoint':
            assert calls.startswith('ram-mountpoint\n--setro\n')
            assert mountpoint.is_dir()
            assert mountpoint.stat().st_mode & 0o777 == 0o755
    else:
        assert r.returncode != 0 and 'rc=0' not in record, (mode, r.stderr)
        if mode == 'occupied':
            assert calls == ''
        elif mode == 'setro_failed':
            assert calls == '--setro\n' and 'phase=1 rc=9' in record
        elif mode == 'mount_failed':
            assert calls.count('mount ') == 1 and 'phase=2 rc=32' in record
        else:
            assert 'phase=3 rc=1' in record
    assert (root / 'logcat').read_text().endswith(record.strip() + '\n')
    assert '--setrw' not in calls
    results[mode] = 'PASS'

# Exercise the actual packaging transform against an inherited image RC.
original = m.read(a.vendor, 'etc/init/hw/init.hlte556n.rc')
changed = m.persist_ro_startup(original)
assert m.persist_ro_startup(changed) == changed
directive = b'    exec_start a9ml_persist_ro\n'
assert changed.count(directive) == 1 and changed.replace(directive, b'') == original
assert changed.index(directive) < changed.index(b'    start vendor.qseecomd\n')
rc = a.out / 'combined.rc'
rc.write_bytes(changed + b'\n' + (HERE / 'init.a9-persist-ro.rc').read_bytes())
r = sp.run([str(m.TOOLS / 'host_init_verifier'), str(rc)], capture_output=True, timeout=30)
(a.out / 'init-verifier.log').write_bytes(r.stdout + r.stderr)
assert r.returncode == 0
results['packaging_order_idempotence_init_verifier'] = 'PASS'
results['device_tested'] = False
results['actual_ext4_mount_tested'] = False
(a.out / 'tests.json').write_text(json.dumps(results, indent=2) + '\n')
print(json.dumps(results, indent=2))
