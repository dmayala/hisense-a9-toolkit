#!/usr/bin/env python3
"""Run only android-boot.sh --offline against the frozen S1 diagnostic images."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
WORK = Path.home() / 'A9-build/mainline'


def ordinary_dir(path):
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError('ordinary wrapper directory required: ' + str(path))
    path.mkdir(mode=0o700, exist_ok=True)
    if path.resolve() != path:
        raise ValueError('redirected wrapper directory: ' + str(path))


def prepare_wrapper(out, snapshot):
    """Check every directory before using it; never follow an existing alias."""
    wrapper = out / 'wrapper'
    ordinary_dir(wrapper)
    android = wrapper / 'android'
    ordinary_dir(android)
    mainline = wrapper / 'mainline'
    ordinary_dir(mainline)
    ordinary_dir(mainline / 'out-android')
    def link(path, target):
        if path.is_symlink():
            if path.readlink() != target:
                raise ValueError('wrapper link changed')
        elif path.exists():
            raise ValueError('wrapper path is not expected symlink')
        else:
            path.symlink_to(target)
    link(android / 'golden', WORK / 'android/golden')
    link(android / 'kernel-snap', snapshot)
    link(mainline / 'out-android/recovery_dtbo.img', out / 'snapshot/recovery_dtbo.img')
    return android, mainline


def result_paths(out):
    for index in range(100):
        suffix = '.' + str(index) if index else ''
        log = out / ('offline-check' + suffix + '.log')
        receipt = out / ('offline-check' + suffix + '.json')
        if log.is_symlink() or receipt.is_symlink():
            raise ValueError('offline result alias refused')
        if not log.exists() and not receipt.exists():
            return log, receipt
    raise ValueError('too many prior offline checks')


def check(output, active=False):
    out = output.expanduser().absolute()
    if (out.parent != WORK / 'sensors-s1' or out.resolve() != out or
            out.is_symlink() or not out.is_dir()):
        raise ValueError('private S1 output required')
    spec = importlib.util.spec_from_file_location('s1_prepare', HERE / 's1-prepare.py')
    prep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prep)
    wanted = prep.IMAGES
    snapshot = WORK / 'adsp/d1-20261017/snapshot'
    if active:
        build = json.loads((out / 'bundle/s1-build.json').read_text())
        images = json.loads((out / 'bundle/receipts/s1-images.json').read_text())
        if build['status'] != 'BUILT_OFFLINE_REVIEW_REQUIRED' or images['status'] != 'PASS-OFFLINE-IMAGE-READBACK':
            raise ValueError('active S1 build/readback required')
        wanted = {'android/out/' + name: digest for name, digest in build['images'].items()}
        wanted['snapshot/recovery_dtbo.img'] = images['sections']['recovery_dtbo']
        snapshot = out / 'snapshot'
    for name, digest in wanted.items():
        if prep.sha(out / name) != digest:
            raise ValueError('S1 image changed: ' + name)
    logpath, receiptpath = result_paths(out)
    android, mainline = prepare_wrapper(out, snapshot)
    env = {**os.environ, 'A9_HOME': str(REPO), 'A9_SERIAL': '', 'ANDROID_SERIAL': '',
           'ADB': '/nonexistent/S1-offline-only', 'FB': '/nonexistent/S1-offline-only',
           'EDL': '/nonexistent/S1-offline-only',
           'A9_ANDROID': str(android), 'A9_MAINLINE': str(mainline),
           'A9_BASE_ANDROID_DTB': str(WORK / 'adsp/dt-ky9UCr0D/base.dtb'),
           'A9_STOCK_BASE_DTS': str(WORK / 'sol-pmic/evidence/base_0.dts'),
           'A9_GOLDEN': str(Path.home() / 'a9-assets/Backups/edl_2026-10-14_110759'),
           'NIX_CONFIG': 'substitute = false\nsubstituters =\nbuilders =\n'}
    argv = ['bash', str(REPO / 'scripts/mainline/android/android-boot.sh'), '--offline',
            '--eink', '--boot', str(out / 'android/out/a9-android-eink-boot-gpu.img'),
            '--vendor', str(out / 'android/out/vendor_b-eink-gpu-bt-health-thermal.img'),
            '--watch', '600', '--hold', '3000', '--no-restore']
    with logpath.open('x') as log:
        result = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, timeout=180)
    text = logpath.read_text()
    if result.returncode or 'OFFLINE CHECK: PASS\n' not in text:
        raise RuntimeError('offline image check failed; inspect ' + str(logpath))
    with receiptpath.open('x') as receipt:
        receipt.write(json.dumps({
            'status': 'PASS-OFFLINE', 'argv': argv, 'images': wanted,
            'device_contact': False, 'gates': 'CLOSED', 'execution_admission': 'NO_GO', 'log': str(logpath),
        }, indent=2) + '\n')
    print('S1 android-boot.sh --offline PASS; no execution admission')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--active', action='store_true', help='validate actual built S1 images and their readback receipt')
    a = parser.parse_args()
    check(a.output, a.active)
