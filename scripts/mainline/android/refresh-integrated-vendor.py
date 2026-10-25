#!/usr/bin/env python3
"""Host-only refresh of the R4 integrated vendor using its original debugfs recipe.

Preserves the integrated GPU/BT/sensors/Wi-Fi composition, session configuration,
S2 init gate, modules and labels. Run through build-vendor.sh --refresh-integrated.
--overlay accepts vendor files; --gnss builds and adds the standalone GNSS HAL.
GPU vendors also refresh Turnip selection/features from their existing Mesa build.
Images contain the existing private modem seeds; keep outputs local.
"""
import argparse
from collections import defaultdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
ROOT = Path.home() / 'A9-build/mainline'
R4 = ROOT / 'integrated-latency-v3-runtime-probe-r4-20261019'
BASE_SHA = '785ab05311654bab19f49079f6ccbde780bce71750fac949b75824d4c061dac6'
# Before the runtime-MAC POC fixes. Diff sources, rather than all vendor bytes:
# R4 intentionally customizes some unchanged templates (session init/scan policy).
SOURCE_BASE = '079d0884d52e3775b73d81f5bca40c9c056c14db'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def debug(image, command):
    return subprocess.run(['debugfs', '-R', command, str(image)],
                          check=True, capture_output=True, text=True).stdout


def metadata(image, path):
    text = debug(image, 'stat /' + path)
    # stat omits xattr values over 40 bytes (e.g. hal_usb_gadget_default_exec); ea_get prints them.
    text += debug(image, f'ea_get /{path} security.selinux')
    fields = {k: re.search(pattern, text).group(1) for k, pattern in {
        'mode': r'Mode:\s+([0-7]+)', 'uid': r'User:\s+(\d+)',
        'gid': r'Group:\s+(\d+)',
        'label': r'security.selinux \(\d+\) = "([^"\\]+)\\000"',
        **{n: r'\b' + n + r':\s+0x([0-9a-f]+):00000000'
           for n in ('atime', 'mtime', 'ctime', 'crtime')},
    }.items()}
    if 'Type: regular' not in text or 'security.capability' in text:
        raise ValueError('replacement requires an existing regular file without capabilities: ' + path)
    return fields


def vendor_files(image, directory=''):
    """List existing regular files without extracting firmware or private seeds."""
    for row in debug(image, 'ls -p /' + directory).splitlines():
        fields = row.split('/')
        if len(fields) < 7 or fields[5] in ('.', '..'):
            continue
        path = directory + '/' + fields[5] if directory else fields[5]
        kind = int(fields[2], 8) & 0o170000
        if kind == 0o040000:
            yield from vendor_files(image, path)
        elif kind == 0o100000:
            yield path


def subsystem_sources(repo, existing):
    """Map changed tracked BT/Wi-Fi sources onto files already shipped by R4."""
    by_name = defaultdict(list)
    for path in existing:
        by_name[Path(path).name].append(path)
    changed = subprocess.check_output([
        'git', '-C', str(repo), 'diff', '--name-only', '--diff-filter=ACMRT', '-z',
        SOURCE_BASE, '--', 'scripts/mainline/bluetooth', 'scripts/mainline/wifi',
    ]).decode().split('\0')
    sources = {}
    for relative in filter(None, changed):
        source = repo / relative
        matches = by_name[source.name]
        # vendor/ subtrees retain their destination suffix, disambiguating XML
        # files with the same basename in permissions/ and vintf/manifest/.
        exact = [p for p in matches if relative.endswith('/' + p)]
        if exact:
            matches = exact
        if not matches and source.suffix == '.rc':
            # Wi-Fi stagers name a9-*.rc as etc/init/init.a9ml-*.rc.
            matches = by_name['init.' + source.name.replace('a9-', 'a9ml-', 1)]
        if len(matches) > 1:
            raise ValueError('ambiguous vendor destination for ' + relative)
        if matches:
            target = matches[0]
            if target in sources and sources[target] != source:
                raise ValueError('multiple sources for vendor member ' + target)
            sources[target] = source
    return sources


def early_stats(hardware):
    """Refresh one bounded action, preserving inherited hardware RC bytes."""
    begin = b'\n# A9 early statistics service\n'
    end = b'# End A9 early statistics service\n'
    action = begin + (HERE/'early-stats.rc').read_bytes() + end
    if begin not in hardware:
        return hardware + action
    if hardware.count(begin) != 1 or hardware.count(end) != 1:
        raise ValueError('ambiguous early statistics action')
    start = hardware.index(begin); stop = hardware.index(end, start) + len(end)
    return hardware[:start] + action + hardware[stop:]


def keep_service_contexts(source, inherited):
    """Keep the base's qualified service domains while refreshing init actions.

    B4 assigns dedicated domains to the shared legacy source templates. In
    particular, BT setup must retain a9ml_bt rather than reverting to phhsu.
    Explicit caller overlays are applied later and can still change a domain.
    """
    split = lambda data: re.split(rb'(?=^(?:service|on|import)[ \t])', data, flags=re.M)
    name = re.compile(rb'^service[ \t]+([^ \t\r\n]+)')
    label = re.compile(rb'^[ \t]+seclabel[ \t]+([^ \t\r\n]+)[ \t]*$', re.M)
    contexts = {}
    for block in split(inherited):
        service, context = name.match(block), label.search(block)
        if service and context:
            contexts[service[1]] = context[1]
    result = []
    for block in split(source):
        service = name.match(block)
        context = contexts.get(service[1]) if service else None
        current = label.search(block)
        if context and (not current or current[1] != context):
            block = label.sub(b'', block)
            head, tail = block.split(b'\n', 1)
            block = head + b'\n    seclabel ' + context + b'\n' + tail
        result.append(block)
    return b''.join(result)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, default=ROOT / 'vendor-rebuild-20261020')
    ap.add_argument('--overlay', type=Path)
    ap.add_argument('--base-vendor', type=Path, help='refresh an existing integrated vendor instead of R4')
    ap.add_argument('--overlay-only', action='store_true',
                    help='replace only explicit overlay files in an existing vendor; retain other subsystem payloads')
    ap.add_argument('--gnss', action='store_true', help='build/add AIDL GNSS using the existing MPSS session')
    args = ap.parse_args()
    if args.overlay_only and (not args.base_vendor or not args.overlay or args.gnss):
        ap.error('--overlay-only requires --base-vendor and --overlay, without --gnss')
    os.umask(0o077)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(out).free < 15 * 1024**3 + 512 * 1024**2:
        raise RuntimeError('need 15 GiB free plus 512 MiB rebuild budget')
    base = (args.base_vendor or (R4 / 'images/vendor.img')).resolve()
    with base.open('rb') as f:
        base_sha = hashlib.file_digest(f, 'sha256').hexdigest()
        if not args.base_vendor and base_sha != BASE_SHA:
            raise ValueError('R4 vendor differs from its seal')
    inputs = out / 'inputs'
    inputs.mkdir(exist_ok=True)

    def read(path):
        dest = inputs / 'readback'
        dest.unlink(missing_ok=True)
        debug(base, f'dump /{path} {dest}')
        return dest.read_bytes()

    toolroot = 'etc/a9-wifi/session/tools/'
    sources = {}
    if not args.overlay_only:
        sources = subsystem_sources(REPO, vendor_files(base))
        sources['bin/a9ml-eink.sh'] = HERE / 'vendor/bin/a9ml-eink.sh'
        sources['bin/a9ml-key-aod.sh'] = HERE / 'vendor/bin/a9ml-key-aod.sh'
        sources['usr/keylayout/gpio-keys.kl'] = HERE / 'vendor/usr/keylayout/gpio-keys.kl'
        sources['etc/init/init.a9ml-gpu.rc'] = HERE / 'vendor/etc/init/init.a9ml-gpu.rc'
        sources['etc/init/init.a9ml-debug.rc'] = HERE / 'vendor/etc/init/init.a9ml-debug.rc'
        sources['bin/a9ml-gles-version.sh'] = HERE / 'vendor/bin/a9ml-gles-version.sh'
        # Replace inherited AOSP network-location exclusion on integrated bases.
        sources['etc/permissions/aosp_excluded_hardware.xml'] = HERE / 'vendor/etc/permissions/aosp_excluded_hardware.xml'
    payload = {p: source.read_bytes() for p, source in sources.items()}
    if not args.overlay_only:
        hardware = 'etc/init/hw/init.hlte556n.rc'
        payload[hardware] = early_stats(read(hardware))
    for path in sources:
        if path.startswith('etc/init/') and path.endswith('.rc') and 'Inode:' in debug(base, 'stat /' + path):
            payload[path] = keep_service_contexts(payload[path], read(path))
    if args.gnss:
        gnss = HERE / 'gnss-hal'
        binary_out = out / 'gnss-out'
        subprocess.run(['python3', '-I', str(gnss / 'build.py'), '--out', str(binary_out)], check=True)
        payload['bin/hw/android.hardware.gnss-service.a9'] = (binary_out / 'android.hardware.gnss-service.a9').read_bytes()
        payload['bin/a9-gnss-loc'] = (binary_out / 'a9-gnss-loc').read_bytes()
        for source in sorted((gnss / 'vendor').rglob('*')):
            if source.is_file():
                payload[str(source.relative_to(gnss / 'vendor'))] = source.read_bytes()
    # Retain R4's Wi-Fi firmware-before-GPU ordering while refreshing GPU init.
    gpu_rc = 'etc/init/init.a9ml-gpu.rc'
    trigger = b'on post-fs-data && property:ro.vendor.a9ml.gpu=1\n'
    firmware = (b'    mkdir /mnt/a9-wifi 0700 root root\n'
                b'    mkdir /mnt/a9-wifi/firmware 0755 root root\n'
                b'    exec_start a9ml_wifi_firmware\n')
    if not args.overlay_only:
        payload[gpu_rc] = payload[gpu_rc].replace(trigger, trigger + firmware, 1)
    removed = () if args.overlay_only else ('bin/a9ml-gpu-admit.sh',)
    if args.overlay:
        for source in sorted(args.overlay.rglob('*')):
            if source.is_file():
                payload[str(source.relative_to(args.overlay))] = source.read_bytes()
    if 'bin/a9ml-key-aod.sh' in payload and 'etc/a9-aod/prefs.jar' not in payload:
        prefs_out = out / 'aod-prefs'
        subprocess.run(['python3', '-I', str(HERE / 'aod/build.py'), '--out', str(prefs_out)], check=True)
        payload['etc/a9-aod/prefs.jar'] = (prefs_out / 'prefs.jar').read_bytes()
    # R4/k retain the old static GLES 3.1 claim. GPU init now sets this once
    # after app EGL selection, before system_server queries PackageManager.
    props = payload.get('build.prop', read('build.prop'))
    if re.search(rb'^ro\.vendor\.a9ml\.gpu=1$', props, re.M):
        payload['build.prop'] = re.sub(rb'^ro\.opengles\.version=[^\n]*\n?', b'', props, flags=re.M)
        spec = importlib.util.spec_from_file_location('a9_vulkan', HERE/'gpu/vulkan.py')
        vulkan = importlib.util.module_from_spec(spec); spec.loader.exec_module(vulkan)
        vk_payload, vk_removed = vulkan.payload(read, payload['build.prop'].decode())
        payload.update(vk_payload)
        removed += tuple(path for path in vk_removed if 'Inode:' in debug(base, 'stat /'+path))

    allocator_service = 'bin/hw/android.hardware.graphics.allocator-service.minigbm'
    if allocator_service in payload:
        # The old allocator runs inside CF's APEX and would load its private
        # unpatched core. The explicit standalone bundle replaces that APEX,
        # sharing /vendor/lib64's patched core with mapper v5/v4 and Codec2.
        required = ('lib64/libminigbm_gralloc.so', 'lib64/a9-gralloc/libminigbm_gralloc4_utils.so',
                    'etc/init/init.a9ml-minigbm.rc', 'etc/vintf/manifest/allocator.minigbm.xml')
        if any(path not in payload for path in required):
            raise ValueError('incomplete standalone minigbm overlay')
        removed += ('apex/com.google.cf.gralloc.apex',)
        contexts = 'etc/selinux/vendor_file_contexts'
        payload[contexts] = payload.get(contexts, read(contexts)) + (
            b'\n/vendor/bin/hw/android\\.hardware\\.graphics\\.allocator-service\\.minigbm u:object_r:hal_graphics_allocator_default_exec:s0\n')
    if args.gnss:
        # Append after the overlay merge so cellular/audio context additions
        # and the freshly built GNSS service all retain their exec mappings.
        contexts = 'etc/selinux/vendor_file_contexts'
        payload[contexts] = payload.get(contexts, read(contexts)) + (
            '\n/vendor/bin/hw/android\\.hardware\\.gnss-service\\.a9 u:object_r:hal_gnss_default_exec:s0\n'
            '/vendor/bin/a9-gnss-loc u:object_r:hal_gnss_default_exec:s0\n').encode()
    usb_service = 'bin/hw/android.hardware.usb.gadget-service.a9'
    if usb_service in payload and not args.overlay_only:
        # The Android discovery/state module replaces the old RRO, including
        # when refreshing an existing integrated vendor that carried the APK.
        removed += ('overlay/A9Usb/A9Usb.apk',)
        contexts = 'etc/selinux/vendor_file_contexts'
        payload[contexts] = payload.get(contexts, read(contexts)) + (
            b'\n/vendor/bin/hw/android\\.hardware\\.usb\\.gadget-service\\.a9 u:object_r:hal_usb_gadget_default_exec:s0\n')
    video_service = 'bin/hw/android.hardware.media.c2@1.2-service-v4l2-64'
    if video_service in payload and not args.overlay_only:
        contexts = 'etc/selinux/vendor_file_contexts'
        payload[contexts] = payload.get(contexts, read(contexts)) + (
            b'\n/vendor/bin/hw/android\\.hardware\\.media\\.c2@1\\.2-service-v4l2-64 u:object_r:mediacodec_exec:s0\n'
            b'/vendor/lib64/a9-video(/.*)? u:object_r:vendor_file:s0\n')
    for path in removed:
        payload.pop(path, None)
    if not args.overlay_only:
        names = [line.split()[1] for line in read(toolroot + 'SHA256SUMS').decode().splitlines()]
        payload[toolroot + 'SHA256SUMS'] = ''.join(
            sha(payload[p] if p in payload else read(p)) + '  ' + n + '\n'
            for n in sorted(names) for p in [toolroot + n]).encode()
        manifest_path = 'etc/a9-wifi/session/manifest.json'
        manifest = json.loads(read(manifest_path))
        for path in manifest['files']:
            if path in payload:
                manifest['files'][path] = sha(payload[path])
        overlay_path = 'etc/wifi/wpa_supplicant_overlay.conf'
        if overlay_path in payload:
            manifest['overlay_sha256'] = sha(payload[overlay_path])
        payload[manifest_path] = (json.dumps(manifest, indent=2) + '\n').encode()
        # This inventory also records the firmware bridge script now refreshed.
        build_manifest_path = 'etc/a9-wifi/build-manifest.json'
        build_manifest = json.loads(read(build_manifest_path))
        for entry in build_manifest['installed']:
            if entry['path'] in payload:
                data = payload[entry['path']]
                entry.update(bytes=len(data), sha256=sha(data))
        payload[build_manifest_path] = (json.dumps(build_manifest, indent=2) + '\n').encode()

    image = out / 'vendor.img'
    # Preserve zero ext4 blocks as holes (or use COW) instead of reserving the
    # full logical image size again on a shared, space-constrained host.
    subprocess.run(['cp', '--reflink=auto', '--sparse=always', str(base), str(image)], check=True)
    # R4's vendor has 1056 inodes, all used once the camera/video/radio payloads land.
    # Grow by the payload plus three block groups (new groups bring their own inodes);
    # lp-write.sh resizes the logical partition to the image.
    head = subprocess.check_output(['dumpe2fs', '-h', str(image)], text=True, stderr=subprocess.DEVNULL)
    field = lambda name: int(re.search(name + r':\s+(\d+)', head).group(1))
    blocks, need = field('Block count'), sum(len(d) // 4096 + 1 for d in payload.values())
    # Only grow when short: an already-grown base (e.g. an earlier integrated vendor) must not
    # grow again, or vendor_b no longer fits in super.
    short = field('Free inodes') < 2 * len(payload) + 64 or field('Free blocks') < need + 4096
    target = blocks + 3 * 32768 + need if short else blocks
    if subprocess.run(['e2fsck', '-fy', str(image)], stdout=subprocess.DEVNULL,
                      stderr=subprocess.STDOUT).returncode > 1:
        raise ValueError('e2fsck failed on the copied base vendor')
    if target != blocks:
        subprocess.run(['resize2fs', str(image), str(target)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    commands, changed = [f'rm /{path}' for path in removed], {}
    expected_metadata = {}
    added_dirs = set()
    def exists(path):
        return 'Inode:' in debug(base, 'stat /' + path)
    def parents(path):
        parent = str(Path(path).parent)
        if parent == '.' or exists(parent) or parent in added_dirs:
            return
        parents(parent)
        label = inputs / ('directory-label-' + str(len(added_dirs)))
        directory_type = 'vendor_file' if parent.startswith(('bin/', 'libexec/', 'lib/', 'lib64/', 'firmware/')) else 'vendor_configs_file'
        label.write_bytes(f'u:object_r:{directory_type}:s0\0'.encode())
        commands.extend([f'mkdir /{parent}', f'sif /{parent} mode 040755',
                         f'sif /{parent} uid 0', f'sif /{parent} gid 0',
                         f'ea_set -f {label} /{parent} security.selinux'])
        added_dirs.add(parent)
    for index, (path, data) in enumerate(sorted(payload.items())):
        if not re.fullmatch(r'[A-Za-z0-9_./+@-]+', path) or '..' in Path(path).parts or path.startswith('/'):
            raise ValueError('invalid vendor-relative path')
        present = exists(path)
        old = read(path) if present else None
        if old == data:
            continue
        if present:
            info = metadata(base, path)
        else:
            # New vendor payloads follow the existing bin/config conventions.
            executable = path.startswith(('bin/', 'libexec/'))
            template = 'bin/a9ml-eink.sh' if executable else gpu_rc
            info = metadata(base, template).copy()
            info.update(mode='0755' if executable else '0644', uid='0', gid='0')
            info['label'] = ('u:object_r:same_process_hal_file:s0' if path == 'lib64/hw/vulkan.adreno.so'
                             else 'u:object_r:mediacodec_exec:s0' if path == video_service
                             else 'u:object_r:hal_radio_default_exec:s0' if path == 'bin/hw/android.hardware.radio-service.a9'
                             else 'u:object_r:hal_gnss_default_exec:s0' if path in ('bin/hw/android.hardware.gnss-service.a9', 'bin/a9-gnss-loc')
                             else 'u:object_r:vendor_file:s0' if path.startswith(('bin/', 'libexec/', 'lib/', 'lib64/', 'firmware/'))
                             else 'u:object_r:vendor_configs_file:s0')
            parents(path)
        if path == allocator_service:
            info['label'] = 'u:object_r:hal_graphics_allocator_default_exec:s0'
        if path == usb_service:
            info['label'] = 'u:object_r:hal_usb_gadget_default_exec:s0'
        expected_metadata[path] = info
        src = inputs / f'payload-{index}'
        src.write_bytes(data)
        label = inputs / f'label-{index}'
        label.write_bytes(info['label'].encode() + b'\0')
        if present:
            commands.append(f'rm /{path}')
        commands += [f'write {src} /{path}',
                     f'sif /{path} mode 010{info["mode"]}',
                     f'sif /{path} uid {info["uid"]}', f'sif /{path} gid {info["gid"]}']
        for field in ('atime', 'mtime', 'ctime', 'crtime'):
            commands.append(f'sif /{path} {field} @{int(info[field], 16)}')
        commands.append(f'ea_set -f {label} /{path} security.selinux')
        changed[path] = {'before': sha(old) if old is not None else None, 'after': sha(data)}
    script = out / 'debugfs-refresh.txt'
    script.write_text('\n'.join(commands) + '\n')
    with (out / 'debugfs.log').open('w') as log:
        subprocess.run(['debugfs', '-w', '-f', str(script), str(image)],
                       check=True, stdout=log, stderr=subprocess.STDOUT)
    # Existing R4 recipe's readback/metadata and filesystem checks.
    for path in changed:
        dest = inputs / 'readback'
        dest.unlink(missing_ok=True)
        debug(image, f'dump /{path} {dest}')
        if dest.read_bytes() != payload[path] or metadata(image, path) != expected_metadata[path]:
            raise ValueError('vendor readback differs: ' + path)
    for path in removed:
        if 'Inode:' in debug(image, 'stat /' + path):
            raise ValueError('deleted vendor member remains: ' + path)
    with (out / 'fsck.log').open('w') as log:
        subprocess.run(['e2fsck', '-fn', str(image)], check=True,
                       stdout=log, stderr=subprocess.STDOUT)
    with image.open('rb') as f:
        digest = hashlib.file_digest(f, 'sha256').hexdigest()
    (out / 'vendor.img.sha256').write_text(digest + '  ' + str(image) + '\n')
    result = {'base': str(base), 'base_sha256': base_sha,
              'overlay_only': args.overlay_only,
              'source_base': SOURCE_BASE,
              'sources': {p: str(s.relative_to(REPO)) for p, s in sources.items()},
              'source_commit': subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip(),
              'recipe_sha256': sha(Path(__file__).read_bytes()),
              'vendor': str(image), 'sha256': digest, 'changed': changed,
              'removed': list(removed)}
    (out / 'build-result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(str(image) + '\nsha256 ' + digest)


if __name__ == '__main__':
    main()
