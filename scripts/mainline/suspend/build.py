#!/usr/bin/env python3
"""Host-only R4 derivative: existing integrated kernel recipe + PM guard.

No phone tools. Uses cached sources/toolchain and the main checkout's integrated
vendor refresh. Outputs contain the original private firmware; keep local.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess as sp

HERE = Path(__file__).resolve().parent
BASE = Path.home() / 'A9-build/mainline/demo-integrated-20261019'
R4 = Path.home() / 'A9-build/mainline/integrated-latency-v3-runtime-probe-r4-20261019'
MAIN = HERE.parents[2]
BOOT_SHA = '02571e365120f8b7fd290110304591fb225a82bffaba194dab6139db05e14dfd'


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def run(command, log, env=None):
    print(' '.join(map(str, command)), flush=True)
    with log.open('w') as f:
        sp.run(list(map(str, command)), check=True, env=env,
               stdout=f, stderr=sp.STDOUT)


def prepare_source(out, source=None):
    src = out / 'kernel'
    # Private directories, read-only source links. Break each patched file's
    # link before writing, including when reusing the old watchdog source.
    run(['cp', '-as', (source or BASE / 'kernel').resolve(), src], out / 'kernel-links.log')
    for name in ('drivers/watchdog/qcom-wdt.c', 'drivers/clk/qcom/gcc-sm6115.c',
                 'drivers/usb/dwc3/dwc3-qcom-legacy.c', 'drivers/misc/fastrpc.c'):
        path = src / name
        data = path.read_bytes()
        path.unlink()
        path.write_bytes(data)
    if source:
        run(['patch', '--dry-run', '-R', '-d', src, '-p1', '-i',
             HERE / 'qcom-wdt-a9-s2idle-guard.patch'], out / 'kernel-patch.log')
    else:
        run(['patch', '-d', src, '-p1', '-i', HERE / 'qcom-wdt-a9-s2idle-guard.patch'], out / 'kernel-patch.log')
    patch = HERE.parent / 'video/sm6115-vcodec-hw-trigger.patch'
    gcc = src / 'drivers/clk/qcom/gcc-sm6115.c'
    domain = gcc.read_text().split('static struct gdsc gcc_vcodec0_gdsc = {', 1)[1].split('\n};', 1)[0]
    if 'HW_CTRL_TRIGGER' not in domain:
        run(['patch', '--batch', '--fuzz=0', '-d', src, '-p1', '-i', patch], out / 'video-power-patch.log')
    # The cached integrated source already has the S1 gate/drain semantics.
    # Keep them, then apply the clean restore patch. Only private regular files
    # are written; shared cache links above were broken before git apply.
    if 'a9_sensor_fastrpc' not in (src / 'drivers/misc/fastrpc.c').read_text():
        raise RuntimeError('integrated FastRPC source must retain the S1 gate')
    for patch, label in [(HERE.parent / 'usb/dwc3-sm6115-restore-qscratch.patch', 'usb-qscratch'),
                         (HERE.parent / 'sensors/fastrpc-restore-context.patch', 'fastrpc-restore')]:
        command = ['git', 'apply', '--unidiff-zero']
        check = sp.run([*command, '--check', patch], cwd=src, capture_output=True)
        if check.returncode == 0:
            with (out / (label + '-patch.log')).open('w') as log:
                sp.run([*command, patch], cwd=src, check=True, stdout=log, stderr=sp.STDOUT)
        else:
            sp.run([*command, '--reverse', '--check', patch], cwd=src, check=True, capture_output=True)
            (out / (label + '-patch.log')).write_text('Exact clean patch already applied; reverse check PASS\n')
    return src


def kernel(out, source=None, pm_debug=False):
    # Original integrated recipe is archived at parity-next/wake/build.py:
    # identical .config, original source export, same cached cross GCC, unchanged
    # module ABI. Private patched sources and objects cannot mutate inputs.
    budget = (2 if source else 5) * 1024**3
    if shutil.disk_usage(out).free < 15 * 1024**3 + budget:
        raise RuntimeError('need 15 GiB reserve plus kernel build budget')
    obj = out / 'obj'
    for name in ('kernel', 'obj'):
        if (out / name).exists():
            raise RuntimeError('use a fresh --out for a kernel build')
    src = prepare_source(out, source)
    run(['cp', '-a', '--reflink=auto', BASE / 'obj', obj], out / 'obj-copy.log')
    (obj / 'source').unlink()
    (obj / 'source').symlink_to(src)
    (obj / 'Makefile').write_text('export KBUILD_OUTPUT = ' + str(obj) + '\ninclude ' + str(src / 'Makefile') + '\n')
    env = dict(os.environ)
    tools = json.loads((BASE / 'source.json').read_text())['tools']
    env['PATH'] = ':'.join(tools) + ':' + env['PATH']
    command = ['make', '-C', src, 'O=' + str(obj), 'ARCH=arm64',
               'CROSS_COMPILE=aarch64-unknown-linux-gnu-', 'HOSTCC=gcc',
               'HOSTCFLAGS=-I/nix/store/4rw03gwh97laxj6sgf630xdbnynkj43z-openssl-3.5.1-dev/include',
               'HOSTLDFLAGS=-L/nix/store/j09xkbypljdhxwspfiqd8sf64zgxhp52-openssl-3.5.1/lib',
               'LOCALVERSION=+', 'DTC_FLAGS=-@', '-j8', 'Image.gz']
    if pm_debug:
        run([src / 'scripts/config', '--file', obj / '.config',
             '-e', 'PM_DEBUG', '-e', 'PM_SLEEP_DEBUG', '-d', 'PM_ADVANCED_DEBUG',
             '-d', 'PM_TEST_SUSPEND', '-d', 'DPM_WATCHDOG'], out / 'pm-debug-config.log')
        run([*command[:-1], 'olddefconfig'], out / 'pm-debug-olddefconfig.log', env)
        # Image.gz refreshes vmlinux.symvers, not the copied combined module
        # inventory. Require a fresh export table for this changed config.
        (obj / 'vmlinux.symvers').unlink(missing_ok=True)
    (out / 'kernel-command.json').write_text(json.dumps(list(map(str, command)), indent=2) + '\n')
    run(command, out / 'kernel-build.log', env)
    # M= keeps the combined kernel/provider symvers intact and emits a fresh
    # module from the same patched source/config instead of inheriting R4's.
    module = out / 'fastrpc-module'
    module.mkdir()
    shutil.copyfile(src / 'drivers/misc/fastrpc.c', module / 'fastrpc.c')
    (module / 'Makefile').write_text('obj-m += fastrpc.o\n')
    run([*command[:-1], 'M=' + str(module), 'W=1', 'modules'], out / 'fastrpc-build.log', env)
    if pm_debug:
        def options(path):
            return {line.split('=')[0]: line.split('=', 1)[1]
                    for line in path.read_text().splitlines() if line.startswith('CONFIG_')}
        before, after = options(BASE / 'obj/.config'), options(obj / '.config')
        changed = {key for key in before.keys() | after.keys()
                   if before.get(key, 'n') != after.get(key, 'n')}
        assert changed == {'CONFIG_PM_DEBUG', 'CONFIG_PM_SLEEP_DEBUG'}, changed
        assert after['CONFIG_PM_DEBUG'] == after['CONFIG_PM_SLEEP_DEBUG'] == 'y'
        def exports(path):
            return {row[1]: (row[0], *row[3:]) for line in path.read_text().splitlines()
                    if len(row := line.split()) >= 4 and row[2] == 'vmlinux'}
        old_exports, new_exports = exports(BASE / 'obj/Module.symvers'), exports(obj / 'vmlinux.symvers')
        assert old_exports and all(new_exports.get(name) == value for name, value in old_exports.items()), 'built-in export ABI changed'
        (out / 'pm-debug-abi.json').write_text(json.dumps({
            'fresh_builtin_exports_sha256': sha(obj / 'vmlinux.symvers'),
            'baseline_module_inventory_sha256': sha(BASE / 'obj/Module.symvers'),
            'unchanged_existing_exports': len(old_exports),
            'additional_exports': len(new_exports.keys() - old_exports.keys()),
        }, indent=2) + '\n')
    else:
        assert (obj / '.config').read_bytes() == (BASE / 'obj/.config').read_bytes()
    # Debug mode still requires the original module ABI. Never bless stale
    # modules if the new config changes any exported type CRC or release.
    assert (obj / 'Module.symvers').read_bytes() == (BASE / 'obj/Module.symvers').read_bytes()
    assert (obj / 'include/config/kernel.release').read_bytes() == (BASE / 'obj/include/config/kernel.release').read_bytes()


DTC = '/nix/store/hmiqb4x74j3a3dqxdz4l8zl8wihsvx9g-dtc-1.7.2/bin/'


def observable_cmdline(header, config):
    """Keep all boot-critical arguments in ABL's primary 512-byte field.

    j already contained the observability flags, but only in extra_cmdline.
    Its runtime helper refused the observability prerequisites. Preserve the overflow
    field for readers that use it, without relying on it for suspend logging.
    """
    cmdline = (header[64:576].split(b'\0')[0] + header[608:1632].split(b'\0')[0]).decode().split()
    updates = ['qcom_wdt.a9_guard=1', 'no_console_suspend', 'pstore.backend=ramoops',
               'pstore.kmsg_bytes=262144', 'loglevel=7']
    keys = {arg.split('=')[0] for arg in updates}
    retained = [arg for arg in cmdline if arg.split('=')[0] not in keys]
    # These diagnostics may remain in extra_cmdline. The governor override is
    # redundant ONLY when this exact governor is already the kernel default.
    optional = {'log_buf_len', 'printk.devkmsg'}
    default_governor = ('CONFIG_CPU_FREQ_DEFAULT_GOV_SCHEDUTIL=y' in config.splitlines())
    overflow = [arg for arg in retained if arg.split('=')[0] in optional
                or (default_governor and arg == 'cpufreq.default_governor=schedutil')]
    primary = ' '.join([arg for arg in retained if arg not in overflow] + updates).encode()
    extra = ' '.join(overflow).encode()
    # Retain a separator for bootloaders that concatenate the arrays directly.
    if extra:
        primary += b' '
    if len(primary) >= 512 or len(extra) >= 1024:
        raise RuntimeError('boot-critical suspend cmdline must fit the primary 512-byte field')
    header[64:576] = primary.ljust(512, b'\0')
    header[608:1632] = extra.ljust(1024, b'\0')
    return updates


def helper(out):
    ndk = Path(os.environ.get('ANDROID_NDK_HOME', '/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973'))
    helper = out / 'overlay/bin/a9ml-suspend'
    helper.parent.mkdir(parents=True, exist_ok=True)
    run([ndk / 'toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang',
         '-std=c11', '-Os', '-static', '-Wall', '-Wextra', '-Werror',
         HERE / 'a9ml-suspend.c', '-o', helper], out / 'helper-build.log')
    return helper


def observable_boot(out, boot, config, image=None):
    # R4 already contains the stock region AND built-in pstore drivers. Reuse
    # those bytes/ABI; no source/object copy or built-in kernel compile needed.
    for option in ('PSTORE', 'PSTORE_CONSOLE', 'PSTORE_PMSG', 'PSTORE_RAM'):
        if 'CONFIG_' + option + '=y' not in config.read_text().splitlines():
            raise RuntimeError('existing kernel lacks built-in ' + option)
    old = boot.read_bytes()
    page = struct.unpack_from('<I', old, 36)[0]
    assert page == 4096 and struct.unpack_from('<I', old, 40)[0] == 2
    pad = lambda n: (n + page - 1) // page * page
    sizes = [struct.unpack_from('<I', old, off)[0] for off in (8, 16, 24, 1632, 1648)]
    blobs, pos = [], page
    for size in sizes:
        blobs.append(old[pos:pos + size])
        pos += pad(size)
    if image:
        blobs[0] = image.read_bytes()
    dtb = out / 'suspend-observable.dtb'
    dtb.write_bytes(blobs[4])
    node = '/reserved-memory/ramoops@b0580000'
    # Use exactly the inherited stock allocation/layout, never allocate an
    # overlapping second region or change previous-record offsets.
    expected = {'reg': '0 b0580000 0 100000', 'record-size': '40000',
                'console-size': '40000', 'pmsg-size': '40000'}
    for prop, value in expected.items():
        actual = sp.check_output([DTC+'fdtget', '-t', 'x', str(dtb), node, prop], text=True).strip()
        if actual != value:
            raise RuntimeError('unexpected stock ramoops ' + prop + ': ' + actual)
    sp.run([DTC+'fdtput', '-t', 's', str(dtb), node, 'compatible', 'ramoops'], check=True)
    sp.run([DTC+'fdtput', '-t', 's', str(dtb), node, 'status', 'okay'], check=True)
    for prop, value in [('mem-type', '0'), ('max-reason', '3')]:
        sp.run([DTC+'fdtput', '-t', 'x', str(dtb), node, prop, value], check=True)
    blobs[4] = dtb.read_bytes()
    header = bytearray(old[:page])
    updates = observable_cmdline(header, config.read_text())
    for offset, blob in zip((8, 16, 24, 1632, 1648), blobs):
        struct.pack_into('<I', header, offset, len(blob))
    struct.pack_into('<Q', header, 1636, page + sum(pad(len(b)) for b in blobs[:3]))
    digest = hashlib.sha1()
    for blob in blobs:
        digest.update(blob)
        digest.update(struct.pack('<I', len(blob)))
    header[576:608] = digest.digest() + b'\0' * 12
    target = out / 'boot.img'
    target.write_bytes(header + b''.join(b.ljust(pad(len(b)), b'\0') for b in blobs))
    (out / 'boot.img.sha256').write_text(sha(target) + '  boot.img\n')
    (out / 'observability.json').write_text(json.dumps({
        'kernel_rebuilt': image is not None, 'kernel_sha256': hashlib.sha256(blobs[0]).hexdigest(),
        'ramoops': {'address': '0xb0580000', 'size': '0x100000',
                    'record_size': '0x40000', 'console_size': '0x40000', 'pmsg_size': '0x40000',
                    'max_reason': 3},
        'cmdline_additions': updates, 'suspend_flags_in_primary_cmdline': True,
        'hardware_persistence_tested': False,
    }, indent=2) + '\n')


def package(out, vendor=True):
    boot = R4 / 'images/boot.img'
    assert sha(boot) == BOOT_SHA
    observable_boot(out, boot, out / 'obj/.config', out / 'obj/arch/arm64/boot/Image.gz')
    image = out / 'boot.img'
    helper_path = helper(out)
    fresh_fastrpc = out / 'fastrpc-module/fastrpc.ko'
    target = out / 'overlay/lib/modules/fastrpc.ko'
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fresh_fastrpc, target)
    # Pair the unchanged module ABI with the new built-in kernel hash.
    for rel in ('lib/modules/kernel-image.sha256', 'etc/thermal-a9.kernel-image.sha256'):
        target = out / 'overlay' / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(sha(out / 'obj/arch/arm64/boot/Image.gz') + '\n')
    snapshot = out / 'snapshot'
    if snapshot.exists():
        raise RuntimeError('snapshot already exists; use a fresh package output')
    # Dereference all inherited links: never write through an R4 input link.
    shutil.copytree(R4 / 'snapshot', snapshot, symlinks=False)
    shutil.copyfile(out / 'obj/arch/arm64/boot/Image.gz', snapshot / 'Image.gz')
    shutil.copyfile(out / 'obj/.config', snapshot / 'config')
    shutil.copyfile(fresh_fastrpc, snapshot / 'modules/fastrpc.ko')
    for dtb in snapshot.glob('*.dtb'):
        shutil.copyfile(out / 'suspend-observable.dtb', dtb)
    (snapshot / 'modules/kernel-image.sha256').write_text(sha(snapshot / 'Image.gz') + '\n')
    manifest_path = snapshot / 'wifi-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    for name in manifest['sha256']:
        manifest['sha256'][name] = sha(snapshot / name)
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    (snapshot / 'SHA256SUMS').write_text(''.join(
        sha(p) + '  ' + str(p.relative_to(snapshot)) + '\n'
        for p in sorted(snapshot.rglob('*')) if p.is_file() and p.name != 'SHA256SUMS'))
    if vendor:
        run(['bash', MAIN / 'scripts/mainline/android/build-vendor.sh', '--refresh-integrated',
             '--out', out / 'vendor', '--overlay', out / 'overlay'], out / 'vendor-build.log')
    paths = [image, *([out / 'vendor/vendor.img'] if vendor else []), helper_path, out / 'obj/.config',
             out / 'obj/Module.symvers', out / 'obj/arch/arm64/boot/Image.gz',
             HERE / 'a9ml-suspend.c', HERE / 'qcom-wdt-a9-s2idle-guard.patch',
             HERE.parent / 'video/sm6115-vcodec-hw-trigger.patch',
             HERE.parent / 'usb/dwc3-sm6115-restore-qscratch.patch',
             HERE.parent / 'sensors/fastrpc-restore-context.patch', fresh_fastrpc,
             MAIN / 'scripts/mainline/android/refresh-integrated-vendor.py']
    (out / 'artifacts.json').write_text(json.dumps({
        'base_boot_sha256': BOOT_SHA, 'hardware_tested': False,
        'module_abi': 'same Module.symvers and kernel.release; actual .config in snapshot/config',
        'dt_ramdisk': 'stock ramoops layout explicitly enabled; ramdisk unchanged; no private MAC added',
        'video_power': 'GCC VCODEC0 HW_CTRL_TRIGGER; rebuilt built-in provider',
        'vendor_source_commit': sp.check_output(['git', '-C', str(MAIN), 'rev-parse', 'HEAD'], text=True).strip(),
        'sha256': {str(p): sha(p) for p in paths}}, indent=2) + '\n')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, default=Path.home() / 'A9-build/mainline/suspend-20261020')
    ap.add_argument('--source', type=Path, help='read-only cached B3 source with the current patch applied')
    ap.add_argument('--no-vendor', action='store_true', help='package boot/helper/snapshot for a larger integration')
    ap.add_argument('--boot', type=Path, help='existing integrated boot for an incremental observability update')
    ap.add_argument('--config', type=Path, help='configuration of that existing kernel')
    ap.add_argument('--pm-debug', action='store_true', help='opt-in kernel build: enable only PM_DEBUG/PM_SLEEP_DEBUG, retain ABI checks')
    ap.add_argument('--image', type=Path, help='new kernel for observable-boot, retaining the input boot DT/ramdisk')
    ap.add_argument('step', choices=('kernel', 'package', 'all', 'observable', 'observable-boot', 'helper'))
    args = ap.parse_args()
    if args.image and args.step != 'observable-boot':
        ap.error('--image is only for observable-boot')
    os.umask(0o077)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.step == 'helper':
        helper(out)
    if args.step in ('observable', 'observable-boot'):
        if not args.boot or not args.config:
            ap.error('observable requires --boot and --config')
        observable_boot(out, args.boot, args.config, args.image)
        if args.step == 'observable':
            helper(out)
    if args.step in ('kernel', 'all'):
        kernel(out, args.source, args.pm_debug)
    if args.step in ('package', 'all'):
        package(out, not args.no_vendor)
