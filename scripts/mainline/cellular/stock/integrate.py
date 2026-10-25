#!/usr/bin/env python3
"""Package the no-SIM-qualified stock radio stack into a copied vendor. Host only.

Local proprietary assets are hash-pinned in assets.json, never downloaded or
stored in git. This entry point builds the RTP shim/policy and vendor derivative;
importing its text-merging helpers performs no build or device operation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess as sp

HERE = Path(__file__).resolve().parent
MAINLINE = HERE.parents[1]
BUILD = Path.home() / 'A9-build/mainline'
ASSETS = BUILD / 'modem-parity-20261023/candidate8/readback'
CACHE = BUILD / 'android/cache/gsi-sepolicy/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v8-3569860608-1792259254'
NDK = Path(os.environ.get('ANDROID_NDK_HOME', '/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973'))
EXEC_TYPES = {
    'bin/hw/qcrild': 'vendor_stock_rild_exec',
    'bin/imsqmidaemon': 'vendor_ims_exec',
    'bin/imsdatadaemon': 'vendor_ims_exec',
    'bin/ims_rtp_daemon': 'vendor_hal_imsrtp_exec',
    'bin/imsrcsd': 'vendor_hal_rcsservice_exec',
    'bin/netmgrd': 'vendor_netmgrd_exec',
}
ALIASES = {n: 'a9-stock-radio/' + n for n in ('deviceInfoServiceModule.so', 'lib-imsqimf.so')}


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(command, **kwargs):
    return sp.run(list(map(str, command)), check=True, **kwargs)


def debug(image, command):
    return run(['debugfs', '-R', command, image], capture_output=True).stdout


def asset_plan(root, files):
    """Validate the explicit shipped closure, without scanning/copying other data."""
    root = root.resolve()
    result = {}
    for relative, digest in files.items():
        path = Path(relative)
        if not re.fullmatch(r'[A-Za-z0-9_./+@-]+', relative) or path.is_absolute() or '..' in path.parts:
            raise ValueError('invalid asset-relative path')
        source = root / path
        if any(p.is_symlink() for p in (source, *source.parents) if p != root and p.is_relative_to(root)):
            raise ValueError('stock asset must be a regular file without symlink components')
        if not source.is_file() or sha(source) != digest:
            raise ValueError('missing or mismatched stock asset: ' + relative)
        result[relative] = source
    return result


def merge_properties(text, extra):
    keys = {line.split('=', 1)[0] for line in extra.splitlines() if line and not line.startswith('#')}
    # Keep the same telephony defaults already installed by cellular/build-images.
    # Remove only Cuttlefish's disabling override, not unrelated platform props.
    keys.add('ro.radio.noril')
    header = '# Stock radio defaults; runtime cellular opt-in remains explicit.'
    lines = [line for line in text.splitlines() if line.split('=', 1)[0] not in keys and line != header]
    return '\n'.join(lines).rstrip() + '\n\n' + extra.strip() + '\n'


def merge_accounts(text, group=False):
    """Normalize empty records (candidate3 fix); preserve other vendor accounts."""
    expected = 'vendor_qti_diag::2901:' if group else 'vendor_qti_diag::2901:2901::/:/bin/sh'
    rows = [line for line in text.splitlines() if line.strip()]
    if expected not in rows:
        rows.append(expected)
    names, ids = set(), set()
    for line in rows:
        fields = line.split(':')
        if (len(fields) != (4 if group else 7) or not re.fullmatch(r'vendor_[A-Za-z0-9_]+', fields[0])
                or not fields[2].isdigit() or fields[0] in names or fields[2] in ids):
            raise ValueError('invalid or conflicting vendor account')
        names.add(fields[0]); ids.add(fields[2])
    return '\n'.join(rows) + '\n'


def merge_contexts(text, addition):
    if addition.strip() in text:
        return text
    keys = {line.split()[0] for line in addition.splitlines() if line.strip() and not line.lstrip().startswith('#')}
    lines = [line for line in text.splitlines() if not line.strip() or line.lstrip().startswith('#') or line.split()[0] not in keys]
    return '\n'.join(lines).rstrip() + '\n' + addition


def merge_policy(text):
    # Separate known fragments support an incoming candidate2..8 or an unmodified
    # integrated base, without duplicating declarations or dropping later work.
    for name in ('domains', 'prepare', 'library-path', 'qcrild', 'qtibus'):
        fragment = (HERE / 'policy' / (name + '.cil')).read_text().strip()
        if fragment in text:
            continue
        declarations = re.findall(r'\(type ([^\s)]+)\)', fragment)
        if any(re.search(r'\(type ' + re.escape(t) + r'\)', text) for t in declarations):
            raise ValueError('incoming stock-radio policy differs; reconcile the existing fragment')
        text = text.rstrip() + '\n\n' + fragment + '\n'
    return text


def compile_policy(base, out, overlay, cache, secilc):
    work = out / 'policy'
    work.mkdir()
    debug(base, f'rdump /etc/selinux {work}')
    sel = work / 'selinux'
    (sel / 'vendor_sepolicy.cil').write_text(merge_policy((sel / 'vendor_sepolicy.cil').read_text()))
    for name in ('vendor_file_contexts', 'vendor_property_contexts', 'vendor_hwservice_contexts'):
        (sel / name).write_text(merge_contexts((sel / name).read_text(), (HERE / 'policy' / (name + '.additions')).read_text()))
    version = (sel / 'plat_sepolicy_vers.txt').read_text().strip()
    system = cache / 'system'
    plat = system / 'etc/selinux'
    files = [plat / 'plat_sepolicy.cil', plat / f'mapping/{version}.cil']
    for p in (plat / f'mapping/{version}.compat.cil',
              system / 'system_ext/etc/selinux/system_ext_sepolicy.cil',
              system / f'system_ext/etc/selinux/mapping/{version}.cil',
              system / f'system_ext/etc/selinux/mapping/{version}.compat.cil',
              system / 'product/etc/selinux/product_sepolicy.cil',
              system / f'product/etc/selinux/mapping/{version}.cil'):
        if p.is_file():
            files.append(p)
    files += [sel / 'plat_pub_versioned.cil', sel / 'vendor_sepolicy.cil']
    if (sel / 'odm_sepolicy.cil').is_file():
        files.append(sel / 'odm_sepolicy.cil')
    # Same production flags as the incoming development vendor. Preserve its
    # permissive declarations and policycaps; add no permissive stock domains.
    command = [secilc, '-D', '-m', '-M', 'true', '-G', '-N', '-c', '30',
               *files, '-o', sel / 'precompiled_sepolicy', '-f', '/dev/null']
    with (out / 'policy.log').open('wb') as log:
        run(command, stdout=log, stderr=sp.STDOUT)
    names = ['vendor_sepolicy.cil', 'vendor_file_contexts', 'vendor_property_contexts',
             'vendor_hwservice_contexts', 'precompiled_sepolicy']
    for prefix, directory in [('plat', plat), ('system_ext', system / 'system_ext/etc/selinux'),
                              ('product', system / 'product/etc/selinux')]:
        name = prefix + '_sepolicy_and_mapping.sha256'
        if not (directory / name).is_file():
            raise ValueError('platform cache lacks policy identity: ' + prefix)
        dest = 'precompiled_sepolicy.' + name
        shutil.copyfile(directory / name, sel / dest)
        names.append(dest)
    target = overlay / 'etc/selinux'
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copyfile(sel / name, target / name)
    (out / 'policy-command.json').write_text(json.dumps(list(map(str, command)), indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-vendor', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--assets', type=Path, default=Path(os.environ.get('A9_STOCK_RADIO_ASSETS', ASSETS)))
    parser.add_argument('--platform-cache', type=Path, default=CACHE)
    parser.add_argument('--ndk', type=Path, default=NDK)
    parser.add_argument('--secilc', type=Path, default=BUILD / 'android/dl/cf_arm64_x/otatools/bin/secilc')
    args = parser.parse_args()
    os.umask(0o077)
    base = args.base_vendor.resolve()
    out = args.out.resolve()
    if (out / 'vendor.img').exists() or base == out / 'vendor.img':
        parser.error('use a fresh output directory; never overwrite the input vendor')
    files = json.loads((HERE / 'assets.json').read_text())['files']
    assets = asset_plan(args.assets, files)
    if b'Inode:' not in debug(base, 'stat /bin/hw/android.hardware.radio-service.a9'):
        raise ValueError('integrated cellular base must retain its custom AIDL fallback executable')
    out.mkdir(parents=True, exist_ok=True)
    overlay = out / 'overlay'
    shutil.copytree(HERE / 'vendor', overlay)
    for relative, source in assets.items():
        dest = overlay / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
    for name, group in [('passwd', False), ('group', True)]:
        (overlay / 'etc' / name).write_text(merge_accounts(debug(base, 'cat /etc/' + name).decode(), group))
    (overlay / 'build.prop').write_text(merge_properties(debug(base, 'cat /build.prop').decode(), (HERE / 'stock.properties').read_text()))
    shim = overlay / 'lib64/a9-stock-radio/liba9-rtp-getline.so'
    compiler = args.ndk / 'toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang'
    run([compiler, '-O2', '-Wall', '-Wextra', '-Werror', '-fPIC', '-shared', '-Wl,-z,defs',
         '-Wl,-soname,liba9-rtp-getline.so', '-Wl,--version-script=' + str(HERE / 'compat/exports.map'),
         HERE / 'compat/rtp-getline.c', '-o', shim])
    compile_policy(base, out, overlay, args.platform_cache, args.secilc)
    # The existing refresh recipe copies the image and preserves unrelated files.
    image_out = out / 'refresh'
    run(['python3', '-I', MAINLINE / 'android/refresh-integrated-vendor.py', '--overlay-only',
         '--base-vendor', base, '--overlay', overlay, '--out', image_out])
    image = image_out / 'vendor.img'
    commands = []
    labels = out / 'labels'
    labels.mkdir()
    def label(path, kind):
        source = labels / kind
        if not source.exists():
            source.write_bytes(('u:object_r:' + kind + ':s0\0').encode())
        commands.append(f'ea_set -f {source} /{path} security.selinux')
    # Generic refresh labels new executables generically. Assign the exact
    # enforcing stock domains and firmware labels before the image is handed off.
    expected_labels = {}
    for p in overlay.rglob('*'):
        if not p.is_file():
            continue
        name = str(p.relative_to(overlay))
        kind = EXEC_TYPES.get(name, 'a9ml_firmware_file' if name.startswith('firmware/')
                              else 'vendor_file' if name.startswith(('bin/', 'lib64/')) else None)
        # Preserve incoming config/property/policy/account inode labels. Refresh
        # already assigns vendor_configs_file to newly added config files.
        if kind:
            label(name, kind)
            expected_labels[name] = kind
    firmware = overlay / 'firmware/modem_pr/mcfg'
    for p in [firmware, *firmware.rglob('*')]:
        if p.is_dir():
            name = str(p.relative_to(overlay))
            label(name, 'a9ml_firmware_file')
            expected_labels[name] = 'a9ml_firmware_file'
    for name, target in ALIASES.items():
        path = 'lib64/' + name
        if b'Inode:' in debug(image, 'stat /' + path):
            commands.append('rm /' + path)
        commands += ['symlink /' + path + ' ' + target, 'sif /' + path + ' uid 0', 'sif /' + path + ' gid 0']
        label(path, 'vendor_file')
    batch = out / 'labels.debugfs'
    batch.write_text('\n'.join(commands) + '\n')
    with (out / 'labels.log').open('wb') as log:
        run(['debugfs', '-w', '-f', batch, image], stdout=log, stderr=sp.STDOUT)
    log = (out / 'labels.log').read_text().lower()
    if any(s in log for s in ('file not found', 'no such file', 'could not', 'command not found',
                               'usage:', 'permission denied', 'while ')):
        raise ValueError('stock vendor metadata installation failed; see labels.log')
    # debugfs can return success after an individual command error. Check both
    # file payload and resulting label, then check the filesystem itself.
    for p in overlay.rglob('*'):
        if p.is_file() and debug(image, 'cat /' + str(p.relative_to(overlay))) != p.read_bytes():
            raise ValueError('stock vendor payload readback differs')
    for path, kind in expected_labels.items():
        if ('u:object_r:' + kind + ':s0').encode() not in debug(image, 'ea_get /' + path + ' security.selinux'):
            raise ValueError('stock executable label differs')
    for name, target in ALIASES.items():
        if ('Fast link dest: "' + target + '"').encode() not in debug(image, 'stat /lib64/' + name):
            raise ValueError('stock plugin alias differs')
    with (out / 'fsck.log').open('wb') as log:
        run(['e2fsck', '-fn', image], stdout=log, stderr=sp.STDOUT)
    shutil.move(image, out / 'vendor.img')
    digest = sha(out / 'vendor.img')
    (out / 'vendor.img.sha256').write_text(digest + '  vendor.img\n')
    (out / 'result.json').write_text(json.dumps(dict(vendor=str(out / 'vendor.img'), sha256=digest,
        assets=len(assets), asset_manifest_sha256=sha(HERE / 'assets.json'),
        runtime='stock-radio/IMS; custom AIDL retained-disabled; netmgrd disabled',
        base_sha256=sha(base), boot_changed=False, device_tested=False), indent=2) + '\n')
    print(str(out / 'vendor.img'))


if __name__ == '__main__':
    main()
