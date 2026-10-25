#!/usr/bin/env python3
"""Build the current integrated POC, without accessing the phone.

R4 supplies the proven kernel/modules and GPU/BT/ADSP/Wi-Fi composition.
Feature steps rebuild merged HAL sources and add them through existing recipes.
GNSS includes the merged unsupported-extension crash fix; use --without gnss to omit it.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BUILD = Path.home() / 'A9-build/mainline'
R4 = BUILD / 'integrated-latency-v3-runtime-probe-r4-20261019'


def release_default():
    value = os.environ.get('A9_RELEASE', '0')
    if value not in ('0', '1'):
        raise ValueError('A9_RELEASE must be 0 or 1')
    return value == '1'


def integ_system_default():
    value = os.environ.get('A9_INTEG_SYSTEM', '')
    return Path(value) if value else None


def bundle_system_delivery(runtime):
    """Freeze the existing check-only path with the runner that consumes it."""
    target = runtime / 'system-delivery'
    target.mkdir(parents=True, exist_ok=True)
    sources = {}
    for name in ('flash-test-b.sh', 'a9-env.sh', 'a9-health.sh', 'a9-compat.sh'):
        source = HERE.parent / name
        shutil.copyfile(source, target / name)
        (target / name).chmod(0o755)
        sources['scripts/' + name] = sha(source)
    return sources


def bundle_acceptance_sources(runtime, assets):
    """Stage every pinned source, including files absent from the base runtime."""
    manifest = json.loads((assets / 'assets.json').read_text())
    for name, digest in manifest['sources'].items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('invalid acceptance source: ' + name)
        source = HERE / relative
        if sha(source) != digest:
            raise ValueError('acceptance source hash mismatch: ' + name)
        target = runtime / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if sha(target) != digest:
            raise ValueError('staged acceptance source hash mismatch: ' + name)


def apply_release(out, args, run):
    if not args.release:
        return None
    target = out / 'release'
    run(['python3', '-I', HERE / 'release/build.py', '--boot', out / 'boot.img',
         '--vendor', out / 'vendor.img', '--system', args.release_system,
         '--keys', args.release_keys, '--out', target], 'release.log')
    for name in ('boot.img', 'vendor.img', 'system.img'):
        shutil.move(target / name, out / name)
    receipt = json.loads((target / 'release-build.json').read_text())
    shutil.copyfile(target / 'release-build.json', out / 'release-build.json')
    return receipt


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def apply_phone_startup_fix(out, args, run):
    """A framework repair needs SYSTEM; boot/vendor alone cannot carry it."""
    source = args.phone_anr_system
    if source is None:
        return None
    if args.release and source.resolve() != args.release_system.resolve():
        raise ValueError('--phone-anr-system must match --release-system for a release build')
    target = out / 'phone-anr'
    run(['python3', '-B', HERE / 'phone-anr/build.py', '--system', source,
         '--out', target], 'phone-anr.log')
    shutil.move(target / 'system.img', out / 'system.img')
    if args.release:
        args.release_system = out / 'system.img'
    return json.loads((target / 'result.json').read_text())


def apply_stock_radio(out, platform_cache, run):
    """Final vendor-only feature step; preserve the cumulative boot/modules."""
    boot_hash = sha(out / 'boot.img')
    target = out / 'stock-radio'
    run(['python3', '-I', HERE / 'cellular/stock/integrate.py',
         '--base-vendor', out / 'vendor.img', '--out', target,
         '--platform-cache', platform_cache], 'stock-radio.log')
    shutil.move(target / 'vendor.img', out / 'vendor.img')
    if sha(out / 'boot.img') != boot_hash:
        raise RuntimeError('stock radio packaging changed boot bytes')


def apply_data_mode(out, mode, platform_cache, run):
    """Select the data profile; retain all incoming boot bytes/modules."""
    boot_hash = sha(out / 'boot.img')
    command = ['python3', '-I', HERE / 'storage-row29/data/integrate.py',
               '--base-vendor', out / 'vendor.img', '--out', out / 'data-mode',
               '--data-mode', mode, '--platform-cache', platform_cache]
    if mode != 'ram':
        config = out / 'snapshot/config'
        if not config.is_file():
            raise RuntimeError('ROW29 requires a matching snapshot/config; use --data-mode ram for RAM data')
        command += ['--kernel-config', config]
    run(command, 'data-mode.log')
    shutil.move(out / 'data-mode/vendor.img', out / 'vendor.img')
    if sha(out / 'boot.img') != boot_hash:
        raise RuntimeError('data vendor step changed boot bytes')


def apply_stock_gatekeeper(out, run):
    """Keep the authentication-token producer paired with stock QTI Keymaster."""
    image = out / 'vendor.img'
    stock = subprocess.check_output([
        'debugfs', '-R', 'stat /bin/hw/android.hardware.keymaster@4.1-service-qti',
        str(image)], stderr=subprocess.DEVNULL)
    if b'Inode:' not in stock:
        return None
    boot_hash = sha(out / 'boot.img')
    target = out / 'gatekeeper'
    run(['python3', '-I', HERE / 'credentials/prepare-vendor.py',
         '--base-vendor', image, '--out', target], 'gatekeeper.log')
    shutil.move(target / 'vendor.img', image)
    if sha(out / 'boot.img') != boot_hash:
        raise RuntimeError('gatekeeper packaging changed boot bytes')
    return json.loads((target / 'result.json').read_text())


def verify_audio_vendor(vendor, audio_out, helper, env):
    """Reject a stale overlay or later overwrite of freshly compiled audio."""
    built = json.loads((audio_out / 'a5-build.json').read_text())
    verified = {}
    with tempfile.TemporaryDirectory(prefix='vendor-check-', dir=audio_out) as directory:
        for name in ('snd-soc-a9-audio.ko', 'q6afe.ko'):
            expected = built.get('fresh_modules', {}).get(name)
            if not expected:
                raise RuntimeError('audio module was not built from source this run: ' + name)
            staged = audio_out / 'overlay-r2/lib/modules' / name
            if sha(staged) != expected['sha256']:
                raise RuntimeError('fresh audio staging hash mismatch: ' + name)
            readback = Path(directory) / name
            subprocess.run(['debugfs', '-R', f'dump /lib/modules/{name} {readback}', str(vendor)],
                           env=env, check=True, capture_output=True, text=True)
            if not readback.is_file() or sha(readback) != expected['sha256']:
                raise RuntimeError('final vendor audio module hash mismatch: ' + name)
            vermagic = subprocess.check_output(['modinfo', '-F', 'vermagic', str(readback)],
                                               env=env, text=True).strip()
            if not expected['vermagic'] or vermagic != expected['vermagic']:
                raise RuntimeError('final vendor audio vermagic mismatch: ' + name)
            verified[name] = {'sha256': sha(readback), 'vermagic': vermagic}
        readback = Path(directory) / 'a9ml-audio.sh'
        subprocess.run(['debugfs', '-R', f'dump /bin/a9ml-audio.sh {readback}', str(vendor)],
                       env=env, check=True, capture_output=True, text=True)
        if not readback.is_file() or sha(readback) != sha(helper):
            raise RuntimeError('final vendor audio helper differs from current source')
        verified['a9ml-audio.sh'] = {'sha256': sha(readback)}
    (audio_out / 'vendor-check.json').write_text(json.dumps(verified, indent=2) + '\n')
    return verified


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    try:
        release = release_default()
    except ValueError as error:
        parser.error(str(error))
    parser.add_argument('--release', action='store_true', default=release,
                        help='A9_RELEASE=1: finalize secure user system/vendor; dev defaults unchanged')
    parser.add_argument('--release-system', type=Path,
                        help='audited LOS v8 GSI input; copied into release output, never edited in place')
    parser.add_argument('--release-keys', type=Path,
                        help='external private release key directory; prepare APEX keys before building')
    parser.add_argument('--phone-anr-system', '--integ-system', type=Path, default=integ_system_default(),
                        help='copy and fix the current v8 SYSTEM startup timer lifecycle; must also flash the resulting system.img')
    updates = parser.add_mutually_exclusive_group()
    updates.add_argument('--update-vibration', type=Path, help='incrementally replace vibration in an existing integrated pair')
    updates.add_argument('--update-suspend', type=Path, help='add suspend observability and watchdogd handoff to an existing integrated pair')
    parser.add_argument('--suspend-observable', action=argparse.BooleanOptionalAction, default=True,
                        help='finalize suspend logging DT/cmdline after feature packaging (default; --without suspend skips it)')
    parser.add_argument('--enforcing', action='store_true', help='opt in to the B4 labelled policy and enforcing boot (phone qualification pending)')
    parser.add_argument('--enforcing-platform-cache', type=Path, default=BUILD / 'android/cache/gsi-sepolicy/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v8-3569860608-1792259254', help='policy cache matching the actual GSI for --enforcing')
    parser.add_argument('--data-mode', choices=('row29', 'row29-fbe', 'ram'), default='row29',
                        help='B-only persistent f2fs by default; ram explicitly restores disposable ext4 /data (no automatic fallback)')
    parser.add_argument('--out', type=Path, default=BUILD / 'integration-build-20261020')
    parser.add_argument('--without', choices=('sensors', 'vibration', 'suspend', 'battery', 'cameras', 'video', 'gnss', 'audio', 'cellular', 'usb'), action='append', default=[],
                        help='omit a feature step; repeat to omit several (R4 baseline remains)')
    parser.add_argument('--gnss', action='store_true', default=True, help='include GNSS (now the default)')
    parser.add_argument('--video', action='store_true', default=True, help='include Venus kernel support and Android16 Codec2 HIDL1.2 HAL (default)')
    parser.add_argument('--video-kernel-only', action='store_true', help='with video enabled, omit Codec2 for native V4L2 diagnosis')
    parser.add_argument('--video-build', type=Path, default=BUILD/'video-20261020', help='cached Venus modules and pinned firmware')
    parser.add_argument('--kernel-build', type=Path, help='reuse an already packaged B3 private kernel build')
    parser.add_argument('--audio', action='store_true', help='include experimental A5 WCD/ESS card and manual routes (outputs stay off)')
    parser.add_argument('--audio-build', type=Path, default=BUILD / 'audio-20261020',
                        help='legacy cache argument; integrated audio modules are always rebuilt from source')
    parser.add_argument('--camera-build', type=Path, help='reuse an existing R4-compatible camera/media build (no kernel build)')
    parser.add_argument('--camera-af-torch-build', type=Path, help='AF/torch module extension when reusing an older camera build')
    parser.add_argument('--camera-android-build', type=Path, default=BUILD/'agent-stab4-20261024/android', help='host-built libcamera HAL/SoftISP/provider bundle with current child cleanup')
    camera_graph = parser.add_mutually_exclusive_group()
    camera_graph.add_argument('--camera-front-probe', action='store_true', help='select the isolated front-only camera graph')
    camera_graph.add_argument('--camera-rear-only', action='store_true', help='select the proven rear-only graph instead of both sensors')
    parser.add_argument('--base-build', type=Path, help='reuse an existing integrated boot/vendor/snapshot; --without skips rebuilds, retaining base features')
    parser.add_argument('--cellular', action=argparse.BooleanOptionalAction, default=True,
                        help='include stock radio/IMS plus IPA/rmnet; retain the disabled AIDL fallback (default)')
    args = parser.parse_args()
    if args.phone_anr_system and not args.phone_anr_system.is_file():
        parser.error('--phone-anr-system/A9_INTEG_SYSTEM must name an input GSI file')
    if args.release:
        if not args.release_system or not args.release_keys:
            parser.error('--release/A9_RELEASE=1 requires --release-system and --release-keys')
        if args.update_vibration or args.update_suspend:
            parser.error('release uses --base-build, not --update-vibration/--update-suspend')
        args.enforcing = True
    if args.enforcing and (args.update_vibration or args.update_suspend):
        parser.error('use --base-build with --enforcing for an incremental existing pair')
    os.umask(0o077)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    features = []

    def run(argv, log):
        print('Building ' + log.removesuffix('.log'), flush=True)
        with (out / log).open('w') as stream:
            subprocess.run(list(map(str, argv)), env=env, check=True,
                           stdout=stream, stderr=subprocess.STDOUT)

    def cache(source, target, names):
        # Copy only cached build dependencies, never generated feature binaries.
        # This leaves shared caches and other workers' generated outputs untouched.
        for name in names:
            src, dest = source / name, target / name
            if not dest.exists() and src.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                if src.is_dir():
                    shutil.copytree(src, dest, symlinks=True)
                else:
                    shutil.copyfile(src, dest)

    if args.update_suspend:
        base = args.update_suspend.resolve()
        if base == out:
            parser.error('use a different --out to preserve the existing phone image')
        if shutil.disk_usage(out).free < 15*1024**3 + 512*1024**2:
            raise RuntimeError('need 15 GiB floor plus 512 MiB incremental image budget')
        suspend = out / 'suspend'
        run(['python3', '-I', HERE / 'suspend/build.py', '--out', suspend,
             '--boot', base / 'boot.img', '--config', base / 'snapshot/config',
             'observable'], 'suspend-observable.log')
        shutil.move(suspend / 'boot.img', out / 'boot.img')
        vendor = out / 'vendor.img'
        shutil.copyfile(base / 'vendor.img', vendor)
        label = suspend / 'vendor-file-label'
        label.write_bytes(b'u:object_r:vendor_file:s0\0')
        helper = suspend / 'overlay/bin/a9ml-suspend'
        batch = suspend / 'helper.debugfs'
        batch.write_text('\n'.join([
            'rm /bin/a9ml-suspend', f'write {helper} /bin/a9ml-suspend',
            'sif /bin/a9ml-suspend mode 0100755',
            f'ea_set -f {label} /bin/a9ml-suspend security.selinux', '']) )
        run(['debugfs', '-w', '-f', batch, vendor], 'suspend-vendor-install.log')
        readback = suspend / 'helper-readback'
        run(['debugfs', '-R', f'dump /bin/a9ml-suspend {readback}', vendor], 'suspend-readback.log')
        if readback.read_bytes() != helper.read_bytes():
            raise RuntimeError('suspend helper vendor readback mismatch')
        run(['e2fsck', '-fn', vendor], 'vendor-fsck.log')
        snapshot = out / 'snapshot'
        shutil.copytree(base / 'snapshot', snapshot, symlinks=False)
        for dtb in snapshot.glob('*.dtb'):
            shutil.copyfile(suspend / 'suspend-observable.dtb', dtb)
        manifest_path = snapshot / 'wifi-manifest.json'
        manifest = json.loads(manifest_path.read_text())
        for name in manifest['sha256']:
            manifest['sha256'][name] = sha(snapshot / name)
        manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
        (snapshot / 'SHA256SUMS').write_text(''.join(
            sha(path)+'  '+str(path.relative_to(snapshot))+'\n'
            for path in sorted(snapshot.rglob('*')) if path.is_file() and path.name != 'SHA256SUMS'))
        apply_data_mode(out, args.data_mode, args.enforcing_platform_cache, run)
        gatekeeper = apply_stock_gatekeeper(out, run)
        hashes = {name: sha(out / name) for name in ('boot.img', 'vendor.img')}
        for name, digest in hashes.items():
            (out / (name+'.sha256')).write_text(digest+'  '+name+'\n')
        (out / 'SHA256SUMS').write_text(''.join(digest+'  '+name+'\n' for name,digest in hashes.items()))
        result = {'status': 'HOST BUILT; suspend persistence/resume phone probe pending',
                  'base': str(base), 'kernel_rebuilt': False,
                  'source_commit': subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip(),
                  'data_mode': args.data_mode,
                  'gatekeeper': gatekeeper,
                  'updated_feature': 'stock ramoops DT, live suspend console, kmsg progress, watchdogd-gsi handoff',
                  'sha256': hashes, 'helper_sha256': sha(helper)}
        (out / 'integrated-build.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2))
        return

    if args.update_vibration:
        base = args.update_vibration.resolve()
        if base == out:
            parser.error('use a different --out to preserve the existing phone image')
        vibration = out / 'vibration'
        env['A9_VIBRATION_OUT'] = str(vibration)
        hal = out / 'existing-vibrator-hal'
        run(['debugfs', '-R', 'dump /bin/hw/android.hardware.vibrator-service.a9 '+str(hal),
             base / 'vendor.img'], 'hal-readback.log')
        run(['python3', '-I', HERE / 'vibration/build-images.py', '--boot', base / 'boot.img',
             '--vendor', base / 'vendor.img', '--hal', hal], 'vibration-build.log')
        for name in ('boot.img', 'vendor.img'):
            shutil.move(vibration / 'out' / name, out / name)
        snapshot = out / 'snapshot'
        shutil.copytree(base / 'snapshot', snapshot, symlinks=False)
        shutil.copyfile(vibration / 'out/a9-aw86xx.ko', snapshot / 'modules/a9-aw86xx.ko')
        manifest_path = snapshot / 'wifi-manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['sha256']['modules/a9-aw86xx.ko'] = sha(snapshot / 'modules/a9-aw86xx.ko')
        manifest_path.write_text(json.dumps(manifest, indent=2)+'\n')
        (snapshot / 'SHA256SUMS').write_text(''.join(
            sha(path)+'  '+str(path.relative_to(snapshot))+'\n'
            for path in sorted(snapshot.rglob('*')) if path.is_file() and path.name != 'SHA256SUMS'))
        apply_data_mode(out, args.data_mode, args.enforcing_platform_cache, run)
        gatekeeper = apply_stock_gatekeeper(out, run)
        hashes = {name: sha(out / name) for name in ('boot.img', 'vendor.img')}
        for name, digest in hashes.items():
            (out / (name+'.sha256')).write_text(digest+'  '+name+'\n')
        (out / 'SHA256SUMS').write_text(''.join(digest+'  '+name+'\n' for name,digest in hashes.items()))
        result = {'status': 'HOST BUILT; AW8695 phone probe pending', 'base': str(base),
                  'source_commit': subprocess.check_output(['git','-C',str(REPO),'rev-parse','HEAD'],text=True).strip(),
                  'data_mode': args.data_mode,
                  'gatekeeper': gatekeeper,
                  'updated_feature': 'vibration module AW8695 ID 0x95', 'sha256': hashes}
        (out / 'integrated-build.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2))
        return

    overlay = out / 'overlay'
    overlay.mkdir(exist_ok=True)
    video = args.video and 'video' not in args.without
    boot = args.base_build/'boot.img' if args.base_build else R4/'images/boot.img'
    kernel_build = None
    snapshot_source = args.base_build/'snapshot' if args.base_build else R4/'snapshot'
    if 'suspend' not in args.without:
        kernel_build = (args.kernel_build or (out / 'suspend')).resolve()
        if not args.kernel_build:
            # Reuse B3 source read-only with private objects and current patch.
            cached = BUILD / 'suspend-20261020/kernel'
            source = ['--source', cached] if cached.exists() else []
            run(['python3', '-I', HERE / 'suspend/build.py', '--out', kernel_build,
                 *source, 'kernel'], 'suspend-kernel.log')
            run(['python3', '-I', HERE / 'suspend/build.py', '--out', kernel_build,
                 '--no-vendor', 'package'], 'suspend-package.log')
        shutil.copytree(kernel_build / 'overlay', overlay, dirs_exist_ok=True)
        boot = kernel_build / 'boot.img'
        if args.kernel_build:
            refreshed = out / 'suspend-update'
            run(['python3', '-I', HERE / 'suspend/build.py', '--out', refreshed,
                 '--boot', boot, '--config', kernel_build / 'obj/.config',
                 'observable'], 'suspend-update.log')
            shutil.copytree(refreshed / 'overlay', overlay, dirs_exist_ok=True)
            boot = refreshed / 'boot.img'
        snapshot_source = kernel_build / 'snapshot'
        features.append('B3 rebuilt kernel with watchdog s2idle patch + a9ml-suspend helper')

    if 'battery' not in args.without:
        battery = out / 'battery'
        env['A9_BATTERY_OUT'] = str(battery)
        run(['python3', '-I', HERE / 'pmic/battery/build-images.py', '--boot', boot,
             '--stage-only'], 'battery-build.log')
        shutil.copytree(battery / 'vendor-overlay', overlay, dirs_exist_ok=True)
        boot = battery / 'out/boot.img'
        features.append('B6 sampled battery/charging module + DT selection + vendor init')

    if 'cameras' not in args.without:
        cameras = (args.camera_build or (out / 'cameras')).resolve()
        if not args.camera_build:
            cache(BUILD / 'cameras-20261020', cameras, ('rear', 'front'))
            if not (cameras / 'rear/s5k3l6-stock.h').exists():
                cameras.mkdir(parents=True, exist_ok=True)
                run(['debugfs', '-R', 'dump /lib64/camera/com.qti.sensormodule.s5k3l6_bnd32b_shine.bin '
                     + str(cameras / 'rear.bin'), BUILD / 'android/ref/vendor_a.img'], 'camera-extract.log')
                run(['python3', '-I', HERE / 'cameras/extract-stock.py', cameras / 'rear.bin',
                     '--out', cameras / 'rear'], 'camera-tables.log')
            run(['python3', '-I', HERE / 'cameras/build.py', '--out', cameras, *(['--video'] if video else []), 'kernel'], 'camera-modules.log')
            run(['python3', '-I', HERE / 'cameras/build.py', '--out', cameras, '--boot', boot,
                 '--stage-only', *(['--video'] if video else []), 'package'], 'camera-package.log')
            # front reuses package's base.dtb and overlay.
            if (cameras / 'front/hi556-stock.h').exists():
                run(['python3', '-I', HERE / 'cameras/build.py', '--out', cameras, 'front'], 'camera-front-module.log')
        # Stage onto the incoming boot so other features' DT changes survive.
        # The cached-build path also leaves its original candidate files intact.
        camera_stage = out / 'camera-android'
        run(['python3', '-I', HERE / 'cameras/stage.py', '--built', cameras,
             '--android-built', args.camera_android_build, '--boot', boot,
             *(['--af-torch-built', args.camera_af_torch_build] if args.camera_af_torch_build else []),
             '--out', camera_stage, '--overlay', overlay,
             *(['--base-vendor', args.base_build/'vendor.img'] if args.base_build else []),
             *(['--front-probe'] if args.camera_front_probe else []),
             *(['--rear-only'] if args.camera_rear_only else [])], 'camera-android.log')
        boot = camera_stage / 'boot.img'
        # A cached ELF can have correct ABI CRCs and still contain the wrong
        # CSID resource table (integ2-5 shipped csid0,csid2,csid2).
        run(['python3', '-B', HERE / 'reboot/inspect-camss-elf.py', '--check',
             overlay / 'lib/modules/qcom-camss.ko'], 'camera-resource-check.log')
        features.append('B9 camera modules/DT + libcamera Android HAL/CPU SoftISP + opt-in HIDL provider (phone untested)')

    sensors = 'sensors' not in args.without
    # Keep each feature's build + staging together; future features add a step here.
    if sensors:
        sensor_out = out / 'sensors'
        cache(BUILD / 'sensors', sensor_out, ('aosp', 'gsi-libs'))
        env['A9_SENSORS_OUT'] = str(sensor_out)
        run(['bash', HERE / 'android/sensors-hal/build.sh'], 'sensors-build.log')
        dest = overlay / 'bin/hw/android.hardware.sensors-service.a9'
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sensor_out / 'out' / dest.name, dest)
        listener = sensor_out / 's1-listener'
        run(['python3', '-I', HERE / 'sensors/s1-build-listener.py', listener,
             '--root', '/mnt/vendor/a9-sensors/s1-listener-root'], 'sensors-listener.log')
        shutil.copyfile(listener, overlay / 'bin/s1-listener')
        shutil.copyfile(HERE / 'android/vendor/bin/a9-sensors.sh', overlay / 'bin/a9-sensors.sh')
        features.append('rebuilt B2 sensors HAL and private RAM listener separate from TEE persist')
    else:
        # A previous invocation may have populated this same output directory.
        (overlay / 'bin/hw/android.hardware.sensors-service.a9').unlink(missing_ok=True)

    if video:
        video_out = out / 'video'
        built_modules = args.video_build / 'built-modules'
        if 'cameras' not in args.without:
            # The common V4L2 core must use the same media configuration as
            # CAMSS and Venus. Select the Venus subset from that one build.
            built_modules = video_out / 'built-modules'
            built_modules.mkdir(parents=True, exist_ok=True)
            names = ('mc', 'videodev', 'v4l2-mem2mem', 'videobuf2-common',
                     'videobuf2-v4l2', 'videobuf2-memops', 'videobuf2-dma-contig',
                     'venus-core', 'venus-dec', 'venus-enc')
            for name in names:
                shutil.copyfile(overlay / 'lib/modules' / (name+'.ko'), built_modules / (name+'.ko'))
        run(['python3', '-I', HERE / 'video/build.py', 'package', '--out', video_out,
             '--boot', boot, '--overlay', overlay, '--built-modules', built_modules,
             '--stock-firmware-dir', args.video_build / 'stock-firmware-a9',
             *(['--media-build', cameras] if 'cameras' not in args.without else [])], 'video-package.log')
        boot = video_out / 'boot.img'
        run(['python3', '-B', HERE / 'reboot/inspect-camss-elf.py', '--check',
             overlay / 'lib/modules/venus-core.ko'], 'venus-firmware-check.log')
        features.append('B10 Venus DT, shared media modules, OEM-signed 6.0.39 firmware + SM6115 decoder/EOS compatibility')
        if not args.video_kernel_only:
            run(['python3', '-I', HERE / 'video/codec2/build.py',
                 '--out', args.video_build / 'codec2', '--overlay', overlay], 'video-codec2.log')
            features.append('B10 rebuilt Android16 V4L2 Codec2 HIDL1.2 service + private platform libraries + H.264/HEVC XML')
            run(['python3', '-I', HERE / 'video/minigbm/build.py', '--overlay', overlay],
                'video-minigbm.log')
            features.append('B10 linear NV12 decoder Surfaces via vkms/freedreno; standalone patched minigbm allocator')

    audio = args.audio and 'audio' not in args.without
    if audio:
        audio_out = out / 'audio'
        # Never promote cached .ko files to current source. The external-only
        # build privately copies prepared headers/tools, not full kernel objects.
        run(['python3', '-I', HERE / 'audio/build-a5.py', '--out', audio_out,
             '--boot', boot,
             *(['--base-obj', kernel_build / 'obj', '--kernel-source', kernel_build / 'kernel']
               if kernel_build else [])],
            'audio-build.log')
        shutil.copytree(audio_out / 'overlay-r2', overlay, dirs_exist_ok=True)
        boot = audio_out / 'boot.img'
        features.append('A5 WCD/ESS card + stock-tuned speaker + MM1/MM2 AIDL primary policy; manual routes, no automatic output')

    cellular = args.cellular and 'cellular' not in args.without
    if cellular:
        cellular_out = out / 'cellular'
        cache(BUILD / 'cellular-20261020', cellular_out, ('aosp',))
        # Stage into the shared overlay; only the IPA external module builds
        # against existing R4 objects. Keep every earlier boot/DT change.
        run(['python3', '-I', HERE / 'cellular/build-images.py', '--out', cellular_out,
             '--ipa', '--stage-only', '--boot', boot, '--overlay', overlay,
             *(['--base-vendor', args.base_build/'vendor.img'] if args.base_build else [])], 'cellular-build.log')
        boot = cellular_out / 'boot.img'
        features.append('IPA4.2/rmnet DT/modules + stock ipa_fws; custom AIDL binary retained as disabled fallback by final stock-radio step')

    if 'usb' not in args.without:
        run(['python3', '-I', HERE / 'usb/build.py', '--out', out / 'usb',
             '--overlay', overlay], 'usb-build.log')
        features.append('B7 FunctionFS MTP/PTP, RNDIS/NCM gadget HAL; ADB retained with automatic fallback')

    gnss = args.gnss and 'gnss' not in args.without
    run(['bash', HERE / 'android/build-vendor.sh', '--refresh-integrated',
         '--out', out, '--overlay', overlay,
         *(['--base-vendor', args.base_build/'vendor.img'] if args.base_build else []),
         *(['--gnss'] if gnss else [])], 'vendor-refresh.log')
    if gnss:
        features.append('experimental GNSS HAL/helper')

    if 'vibration' not in args.without:
        vibration = out / 'vibration'
        cache(BUILD / 'vibration-20261020', vibration, ('aosp', 'aidl-imports'))
        env['A9_VIBRATION_OUT'] = str(vibration)
        run(['python3', '-I', HERE / 'vibration/build-images.py', '--vendor', out / 'vendor.img',
             '--boot', boot, *(['--kernel-build', kernel_build] if kernel_build else [])],
            'vibration-build.log')
        for name in ('boot.img', 'vendor.img'):
            shutil.move(vibration / 'out' / name, out / name)
        features.append('vibration module + HAL; haptics DT enabled; only TLMM67 released')
    else:
        shutil.copyfile(boot, out / 'boot.img')

    if args.suspend_observable and 'suspend' not in args.without:
        # Repack the actual cumulative boot, including --kernel-build reuse.
        # The earlier cached boot can have critical flags only in extra_cmdline;
        # snapshot DTs below must describe this final observability update too.
        observable = out / 'suspend-final'
        run(['python3', '-I', HERE / 'suspend/build.py', '--out', observable,
             '--boot', out / 'boot.img', '--config', snapshot_source / 'config',
             'observable-boot'], 'suspend-final.log')
        shutil.move(observable / 'boot.img', out / 'boot.img')
        features.append('suspend logging flags in primary boot cmdline + stock ramoops DT (diagnostic default)')

    snapshot = out / 'snapshot'
    shutil.copytree(snapshot_source, snapshot, symlinks=False, dirs_exist_ok=True)
    # Snapshot DTs must match the cumulative boot, never the R4 placeholders.
    data = (out / 'boot.img').read_bytes()
    page = struct.unpack_from('<I', data, 36)[0]
    sizes = [struct.unpack_from('<I', data, off)[0] for off in (8, 16, 24, 1632, 1648)]
    offset = page + sum((n+page-1)//page*page for n in sizes[:4])
    for dtb in snapshot.glob('*.dtb'):
        dtb.write_bytes(data[offset:offset+sizes[4]])
    module_paths = []
    for feature, name in [('vibration', 'a9-aw86xx.ko'), ('battery', 'pmi632-battery.ko')]:
        module = out / feature / 'out' / name
        if feature not in args.without:
            dest = snapshot / 'modules' / name
            shutil.copyfile(module, dest)
            module_paths.append(str(dest.relative_to(snapshot)))
    for module in sorted((overlay / 'lib/modules').glob('*.ko')):
        dest = snapshot / 'modules' / module.name
        shutil.copyfile(module, dest)
        module_paths.append(str(dest.relative_to(snapshot)))
    manifest_path = snapshot / 'wifi-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    for name in [*manifest['sha256'], *module_paths]:
        manifest['sha256'][name] = sha(snapshot / name)
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    (snapshot / 'SHA256SUMS').write_text(''.join(
        sha(path) + '  ' + str(path.relative_to(snapshot)) + '\n'
        for path in sorted(snapshot.rglob('*')) if path.is_file() and path.name != 'SHA256SUMS'))

    if args.enforcing:
        enforcing = out / 'enforcing'
        run(['python3', '-I', HERE / 'android/sepolicy/build-enforcing.py',
             '--vendor', out / 'vendor.img', '--boot', out / 'boot.img',
             '--platform-cache', args.enforcing_platform_cache,
             '--out', enforcing], 'enforcing-build.log')
        for name in ('boot.img', 'vendor.img'):
            shutil.move(enforcing / name, out / name)
        features.append('B4 opt-in enforcing kernel cmdline, enforcing functional service domains, permissive development root shell and real inode labels (phone untested)')

    apply_data_mode(out, args.data_mode, args.enforcing_platform_cache, run)
    if cellular:
        apply_stock_radio(out, args.enforcing_platform_cache, run)
        features.append('stock qcrild/IMS closure, MCFG/data assets and gated prep; candidate8 passed 22 no-SIM checks; existing MPSS/RAM rmtfs')
    gatekeeper = apply_stock_gatekeeper(out, run)
    if gatekeeper:
        features.append('stock QTI Gatekeeper paired with the Keymaster TA for password authentication')
    features.append('B-only ROW29 persistent f2fs /data; profile='+args.data_mode if args.data_mode != 'ram' else 'explicit RAM ext4 /data; candidate left untouched')
    if audio:
        verify_audio_vendor(out / 'vendor.img', audio_out, HERE / 'audio/a9ml-audio.sh', env)
    phone_anr = apply_phone_startup_fix(out, args, run)
    release_receipt = apply_release(out, args, run)
    if release_receipt:
        features.append('release user props, authenticated non-root ADB, release APEX signing, no test consoles/deadman')
    hashes = {name: sha(out / name) for name in
              (('boot.img', 'vendor.img', 'system.img') if (out/'system.img').is_file() else ('boot.img', 'vendor.img'))}
    for name, digest in hashes.items():
        (out / (name + '.sha256')).write_text(digest + '  ' + name + '\n')
    (out / 'SHA256SUMS').write_text(''.join(digest + '  ' + name + '\n' for name, digest in hashes.items()))
    result = {
        'status': 'HOST BUILT; coordinator phone test pending',
        'source_commit': subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip(),
        'baseline': 'R4-compatible config/modules; refreshed Wi-Fi/BT/session/logd/E Ink/key-AOD scripts; GPU/Mesa; ADSP',
        'inherited_features': json.loads((args.base_build/'integrated-build.json').read_text()).get('features', []) if args.base_build else [],
        'features': features, 'data_mode': args.data_mode, 'base_build': str(args.base_build) if args.base_build else None, 'camera_front_probe': args.camera_front_probe, 'camera_rear_only': args.camera_rear_only, 'kernel_build': str(kernel_build) if kernel_build else None, 'suspend_observable': args.suspend_observable and 'suspend' not in args.without, 'enforcing': args.enforcing, 'gnss': gnss, 'video': video, 'audio': audio, 'cellular': cellular, 'without': args.without,
        'gatekeeper': gatekeeper,
        'release': release_receipt,
        'phone_anr': phone_anr,
        'sha256': hashes,
    }
    (out / 'integrated-build.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
