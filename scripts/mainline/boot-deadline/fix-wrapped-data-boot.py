#!/usr/bin/env python3
"""Host-only fix8: kernel-compatible F2FS format and platform APEX startup order.

The fix4-wdt kernel has no CONFIG_UNICODE. Use Android's supported casefold
format switch, preserving its module ABI. Defer main/late_start until the GSI's
boot event, after post-fs-data/APEX activation. Requires a fresh-data trial.
"""
import argparse
import difflib
import importlib.util
import json
from pathlib import Path
import subprocess as sp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    source, out = args.source.resolve(strict=True), args.output.absolute()
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location('startup', here / 'fix-wrapped-startup.py')
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    expected = '46e9de0eb22aada6aa4402c1557fdfa3e63c35b314d3c7585d9da13fb4d7f7c3'
    assert helpers.digest(source) == expected, 'expected exact fix7 vendor'
    image = out / 'vendor.img'
    assert not image.exists(), 'output exists'
    out.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location('metadata', here.parent / 'android/refresh-integrated-vendor.py')
    meta = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(meta)
    hw, shell = 'etc/init/hw/init.hlte556n.rc', 'bin/a9ml-wrapped-start.sh'
    before = {p: helpers.read(source, p).decode() for p in (hw, shell)}
    after = dict(before)
    after[hw] = helpers.replace(after[hw], '    exec_start a9ml_wrapped_mount\n',
        '    # Match this kernel: CONFIG_UNICODE is absent; do not format casefold.\n'
        '    setprop external_storage.casefold.enabled false\n'
        '    exec_start a9ml_wrapped_mount\n')
    after[hw] = helpers.replace(after[hw], '    class_start main\n    class_start late_start\n',
        '    # GSI post-fs-data activates APEXes and configures the default namespace.\n'
        '    # Framework classes start on boot, after that platform sequence.\n')
    after[hw] = helpers.replace(after[hw], 'on boot\n    setprop vendor.a9.trace.boot entered\n',
        'on boot\n    setprop vendor.a9.trace.boot entered\n'
        '    # The manual vold worker does not emit mount_all\'s nonencrypted event.\n'
        '    # Preserve class starts here, after platform post-fs-data/zygote-start.\n'
        '    class_start main\n    class_start late_start\n'
        '    setprop vendor.a9.trace.framework_classes issued\n')
    after[hw] += ('\non property:apexd.status=activated\n'
                  '    setprop vendor.a9.trace.apex activated\n'
                  '\non property:apexd.status=ready\n'
                  '    setprop vendor.a9.trace.apex ready\n'
                  '\non zygote-start\n'
                  '    setprop vendor.a9.trace.zygote_start entered\n')
    after[shell] = helpers.replace(after[shell], '    trace data entered\n',
        '    trace data entered\n'
        '    trace data_casefold "$(getprop external_storage.casefold.enabled)"\n')
    after[shell] = helpers.replace(after[shell], "    grep -q ' /data f2fs ' /proc/mounts || exit 1\n",
        "    grep -q ' /data f2fs ' /proc/mounts || { trace data f2fs_missing; log \"vold returned but /data is not f2fs mounted\"; exit 1; }\n")
    commands = []
    for i, path in enumerate(before):
        local = out / 'overlay' / path
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_text(after[path])
        mt = meta.metadata(source, path)
        label = out / f'label-{i}'
        label.write_bytes(mt['label'].encode() + b'\0')
        commands += [f'rm /{path}', f'write {local} /{path}',
                     f'sif /{path} mode 010{mt["mode"]}', f'sif /{path} uid {mt["uid"]}',
                     f'sif /{path} gid {mt["gid"]}', f'ea_set -f {label} /{path} security.selinux']
        commands += [f'sif /{path} {field} @{int(mt[field], 16)}'
                     for field in ('atime', 'mtime', 'ctime', 'crtime')]
    batch = out / 'install.debugfs'
    batch.write_text('\n'.join(commands) + '\n')
    sp.run(['cp', '--reflink=auto', '--sparse=always', str(source), str(image)], check=True)
    with (out / 'install.log').open('wb') as log:
        sp.run(['debugfs', '-w', '-f', str(batch), str(image)], stdout=log, stderr=sp.STDOUT, check=True)
    with (out / 'changed-ea_get.txt').open('wb') as log:
        for path in before:
            assert helpers.read(image, path) == after[path].encode(), path
            assert meta.metadata(image, path) == meta.metadata(source, path), path
            log.write(sp.check_output(['debugfs', '-R', f'ea_get /{path} security.selinux', str(image)], stderr=sp.DEVNULL))
    with (out / 'fsck.log').open('wb') as log:
        sp.run(['e2fsck', '-fn', str(image)], stdout=log, stderr=sp.STDOUT, check=True)
    (out / 'vendor.patch').write_text(''.join(''.join(difflib.unified_diff(
        before[p].splitlines(True), after[p].splitlines(True),
        fromfile='fix7/' + p, tofile='fix8/' + p)) for p in before))
    result = dict(source=str(source), source_sha256=expected, image=str(image),
                  sha256=helpers.digest(image), changed_files=list(before),
                  kernel_modified=False, policy_modified=False, fresh_data_required=True,
                  fsck_rc=0, phone_access=False, hardware_tested=False)
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    (out / 'vendor.img.sha256').write_text(result['sha256'] + '  vendor.img\n')
    print('WRAPPED FIX8 READY ' + str(image), flush=True)


if __name__ == '__main__':
    main()
