#!/usr/bin/env python3
"""Build an opt-in radio vendor via the existing integrated refresh recipe. Host only."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = Path.home() / 'A9-build/mainline'
R4 = ROOT / 'integrated-latency-v3-runtime-probe-r4-20261019'


def stage_snapshot(out, ipa):
    """Use the existing integrated snapshot format for the coordinator's recipe."""
    snapshot = out / 'snapshot'
    shutil.copytree(R4 / 'snapshot', snapshot, symlinks=False, dirs_exist_ok=True)
    if ipa:
        for dtb in snapshot.glob('*.dtb'):
            shutil.copyfile(out / 'ipa/sm6115-hisense-hlte556n-ipa.dtb', dtb)
        for module in (out / 'ipa/vendor-overlay/lib/modules').glob('*.ko'):
            shutil.copyfile(module, snapshot / 'modules' / module.name)
    manifest_path = snapshot / 'wifi-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    additions = ['modules/ipa.ko', 'modules/rmnet.ko'] if ipa else []
    for name in [*manifest['sha256'], *additions]:
        manifest['sha256'][name] = hashlib.sha256((snapshot / name).read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
    (snapshot / 'SHA256SUMS').write_text(''.join(
        hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + str(path.relative_to(snapshot)) + '\n'
        for path in sorted(snapshot.rglob('*')) if path.is_file() and path.name != 'SHA256SUMS'))


def run(argv, **kwargs):
    return subprocess.run(list(map(str, argv)), check=True, **kwargs)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, default=ROOT / 'cellular-20261020')
    ap.add_argument('--hal-binary', type=Path, help='reuse an already built unchanged HAL with --skip-build')
    ap.add_argument('--ipa', action='store_true', help='build/add IPA module, firmware and R4 DT derivative')
    ap.add_argument('--boot', type=Path, default=R4 / 'images/boot.img', help='input boot with cumulative integrated DT changes')
    ap.add_argument('--base-vendor', type=Path, default=R4 / 'images/vendor.img', help='inherit properties/contexts from the selected integrated vendor')
    ap.add_argument('--stage-only', action='store_true', help='emit boot.img and overlay without packing a vendor image or snapshot')
    ap.add_argument('--overlay', type=Path, help='merge cellular into an existing integrated vendor overlay')
    ap.add_argument('--skip-build', action='store_true', help='use the HAL just built in --out')
    ap.add_argument('--integration-repo', type=Path, default=Path.home() / 'A9',
                    help='checkout containing build-vendor.sh --refresh-integrated')
    args = ap.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if not args.skip_build:
        run(['python3', '-I', HERE / 'build.py', '--out', out])
    overlay = (args.overlay or out / 'vendor-overlay').resolve()
    shutil.copytree(HERE / 'vendor', overlay, dirs_exist_ok=True)
    if args.ipa:
        run(['python3', '-I', HERE / 'build-ipa.py', '--out', out / 'ipa', '--boot', args.boot])
        shutil.copytree(out / 'ipa/vendor-overlay', overlay, dirs_exist_ok=True)
    else:
        # SIM/SMS-only image still starts the HAL without an IPA derivative.
        rc = overlay / 'etc/init/android.hardware.radio-service.a9.rc'
        rc.write_text(rc.read_text().replace('    exec_start a9ml-cellular-prepare\n', ''))
    binary = overlay / 'bin/hw/android.hardware.radio-service.a9'
    binary.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.hal_binary or out / binary.name, binary)
    base = args.base_vendor.resolve()
    build_prop = ((overlay / 'build.prop').read_text() if (overlay / 'build.prop').exists()
                  else run(['debugfs', '-R', 'cat /build.prop', base], capture_output=True, text=True).stdout)
    extra = (HERE / 'cellular.properties').read_text()
    keys = {s.split('=', 1)[0] for s in extra.splitlines() if s and not s.startswith('#')}
    # Cuttlefish may carry telephony-disabling properties on a future base.
    keys |= {'ro.radio.noril', 'ro.carrier', 'ro.telephony.default_network'}
    build_prop = '\n'.join(s for s in build_prop.splitlines() if s.split('=', 1)[0] not in keys)
    (overlay / 'build.prop').write_text(build_prop + '\n\n' + extra + '\n')
    contexts_file = overlay / 'etc/selinux/vendor_file_contexts'
    contexts = (contexts_file.read_text() if contexts_file.exists()
                else run(['debugfs', '-R', 'cat /etc/selinux/vendor_file_contexts', base], capture_output=True, text=True).stdout)
    radio_context = '/vendor/bin/hw/android\\.hardware\\.radio-service\\.a9 u:object_r:hal_radio_default_exec:s0'
    if radio_context not in contexts.splitlines():
        contexts += '\n' + radio_context + '\n'
    (overlay / 'etc/selinux').mkdir(parents=True, exist_ok=True)
    (overlay / 'etc/selinux/vendor_file_contexts').write_text(contexts)
    boot = out / 'ipa/boot-ipa.img' if args.ipa else args.boot.resolve()
    if args.stage_only:
        link = out / 'boot.img'
        if link.is_symlink():
            link.unlink()
        link.symlink_to(boot)
        (out / 'stage-result.json').write_text(json.dumps({
            'boot': str(link), 'overlay': str(overlay), 'ipa_candidate': args.ipa,
            'boot_base': str(args.boot), 'device_tested': False,
        }, indent=2) + '\n')
        return
    images = out / 'images'
    run(['bash', args.integration_repo / 'scripts/mainline/android/build-vendor.sh',
         '--refresh-integrated', '--out', images, '--overlay', overlay])
    # Refresh currently assigns a generic exec label to new binaries; set the
    # radio label using the same debugfs method before handing off the image.
    label = out / 'radio-label'
    label.write_bytes(b'u:object_r:hal_radio_default_exec:s0\0')
    image = images / 'vendor.img'
    run(['debugfs', '-w', '-R', f'ea_set -f {label} /bin/hw/{binary.name} security.selinux', image], capture_output=True)
    if args.ipa:
        # Match the existing R4 firmware/module inode labels.
        module_label = out / 'module-label'
        module_label.write_bytes(b'u:object_r:vendor_file:s0\0')
        commands = []
        for source in sorted((out / 'ipa/vendor-overlay').rglob('*')):
            if source.is_file():
                relative = str(source.relative_to(out / 'ipa/vendor-overlay'))
                commands.append(f'ea_set -f {module_label} /{relative} security.selinux')
        batch = out / 'ipa-labels.debugfs'
        batch.write_text('\n'.join(commands) + '\n')
        run(['debugfs', '-w', '-f', batch, image], capture_output=True)
    run(['e2fsck', '-fn', image], capture_output=True)
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    (images / 'vendor.img.sha256').write_text(digest + '  ' + str(image) + '\n')
    result_path = images / 'build-result.json'
    result = json.loads(result_path.read_text())
    result['before_radio_exec_label_sha256'] = result['sha256']
    result['sha256'] = digest
    result['radio_exec_label'] = 'u:object_r:hal_radio_default_exec:s0'
    result_path.write_text(json.dumps(result, indent=2) + '\n')
    link = images / 'boot.img'
    if link.is_symlink():
        link.unlink()
    if not link.exists():
        link.symlink_to(boot)
    boot_sha = hashlib.sha256(boot.read_bytes()).hexdigest()
    (images / 'boot.img.sha256').write_text(boot_sha + '  ' + str(link) + '\n')
    stage_snapshot(out, args.ipa)
    (out / 'images.json').write_text(json.dumps({
        'vendor': str(image), 'vendor_sha256': digest,
        'boot': str(link), 'boot_sha256': boot_sha, 'boot_unchanged': not args.ipa,
        'integration_commit': subprocess.check_output(['git', '-C', args.integration_repo, 'rev-parse', 'HEAD'], text=True).strip(),
        'radio_source_commit': subprocess.check_output(['git', '-C', HERE, 'rev-parse', 'HEAD'], text=True).strip(),
        'device_tested': False, 'ipa_candidate': args.ipa, 'mobile_data_tested': False,
    }, indent=2) + '\n')
    print(str(image) + '\nsha256 ' + digest)


if __name__ == '__main__':
    main()
