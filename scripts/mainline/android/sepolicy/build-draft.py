#!/usr/bin/env python3
"""Extract a small policy/init overlay and link the B4 static draft. Host only.
No image writes, mounts, device operations, or changes to the input tree.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
DOMAINS = {
    'a9ml-eink.sh': 'a9ml_eink', 'a9ml-gpu.sh': 'a9ml_gpu',
    'a9ml-gpu-admit.sh': 'a9ml_gpu', 'a9ml-gles-version.sh': 'a9ml_gpu',
    'a9ml-audio.sh': 'a9ml_audio', 'a9ml-key-aod.sh': 'a9ml_key_aod',
    'a9-camcap': 'a9ml_camcap',
    'a9-audio-load.sh': 'a9ml_audio',
    'android.hardware.audio.service-aidl.a9': 'hal_audio_default',
    'android.hardware.graphics.allocator-service.minigbm': 'hal_graphics_allocator_default',
    'android.hardware.camera.provider@2.4-service.a9': 'hal_camera_default',
    'android.hardware.media.c2@1.2-service-v4l2-64': 'mediacodec', 'a9ml-bluetooth.sh': 'a9ml_bt',
    'a9-eink-compat': 'a9ml_compat', 'a9-sensors.sh': 'a9ml_sensors',
    'a9-video-start': 'a9ml_video', 'a9ml-deadman.sh': 'a9ml_deadman', 'a9ml-usb.sh': 'a9ml_usb',
    'a9ml-wifi-firmware.sh': 'a9ml_fw', 'a9ml-wifi-modules.sh': 'a9_wifi_radio',
    'a9ml-wifi-session.sh': 'a9_wifi_radio', 'a9-wifi-sandbox': 'a9_wifi_radio',
    'android.hardware.sensors-service.a9': 'hal_sensors_default',
    'android.hardware.thermal-service.a9': 'hal_thermal_default',
    'android.hardware.health-service.cuttlefish': 'hal_health_default',
    'android.hardware.vibrator-service.a9': 'hal_vibrator_default',
    'android.hardware.gnss-service.a9': 'hal_gnss_default',
    'android.hardware.bluetooth-service.default': 'hal_bluetooth_default',
}


def sha(path):
    return hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()


def rewrite_rc(text, rel, inventory):
    # Change domain selection only. Leave triggers, users, caps and argv intact.
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.startswith(('service ', 'on ', 'import '))]
    starts.append(len(lines))
    for begin, end in reversed(list(zip(starts, starts[1:]))):
        line = lines[begin]
        if not line.startswith('service '):
            continue
        fields = line.split()
        name = fields[1]
        domain = 'a9ml_rootsh' if name == 'a9ml_rootsh' else None
        for token in fields[2:]:
            if Path(token).name in DOMAINS:
                domain = DOMAINS[Path(token).name]
                break
        block = lines[begin:end]
        old = next((l.split()[1] for l in block if l.strip().startswith('seclabel ')), None)
        if domain:
            block = [l for l in block if not l.strip().startswith('seclabel ')]
            block.insert(1, f'    seclabel u:r:{domain}:s0\n')
            lines[begin:end] = block
        elif old == 'u:r:phhsu_daemon:s0':
            raise ValueError(f'unmapped functional service {name} in {rel}')
        inventory.append({'rc': str(rel), 'service': name, 'old_domain': old, 'draft_domain': domain or old or 'file transition (unchanged)'})
    return ''.join(lines).replace('exec_background u:r:phhsu_daemon:s0 ',
                                 'exec_background u:r:a9ml_rootsh:s0 ')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--vendor-image', type=Path, required=True)
    ap.add_argument('--platform-cache', type=Path, required=True,
                    help='GSI policy cache whose system/ subtree matches the test system image')
    ap.add_argument('--secilc', type=Path, default=Path.home() / 'A9-build/mainline/android/dl/cf_arm64_x/otatools/bin/secilc')
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--enforcing', action='store_true', help='link without inherited permissive domains and supply matching precompiled hashes')
    ap.add_argument('--qualification-audit', action='store_true',
                    help='disable dontaudit in the qualification binary (normal builds retain it)')
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    source = a.out / 'input'; source.mkdir()
    for remote in ('/etc/selinux', '/etc/init'):
        result = subprocess.run(['debugfs', '-R', f'rdump {remote} {source}', str(a.vendor_image)], capture_output=True, text=True)
        (a.out / ('extract-' + Path(remote).name + '.log')).write_text(result.stdout + result.stderr)
    overlay = a.out / 'overlay'
    sel = overlay / 'etc/selinux'
    shutil.copytree(source / 'selinux', sel)
    cil = sel / 'vendor_sepolicy.cil'
    base = cil.read_text()
    # The integrated image already includes Wi-Fi type declarations. Bare CF
    # vendor inputs can still be used for syntax development, never hardware.
    if not re.search(r'^\(type a9_wifi_radio\)', base, re.M):
        base += '\n' + (HERE.parent.parent / 'wifi/android/sepolicy/a9-wifi.cil').read_text()
    cil.write_text(base + '\n; B4 STATIC DRAFT 2026-10-20\n' + '\n'.join(
        (HERE / name).read_text() for name in ('a9ml.cil', 'integrated.cil', 'services.cil', 'avc-f.cil', 'avc-h.cil', 'source-round3.cil', 'avc-enf3.cil', 'source-round5.cil', 'source-round6.cil', 'source-round7.cil', 'source-round8.cil', 'source-round9.cil', 'source-round10.cil', 'source-round11.cil', 'source-round12.cil', 'integration-audio-r5.cil', 'integration-audio-memfd.cil', 'row29-service-data.cil', 'rootsh-enforcing.cil')))
    # Remove exact Cuttlefish card rules, replaced by the existing A9 policy.
    fc = sel / 'vendor_file_contexts'
    old = [line for line in fc.read_text().splitlines() if not re.match(r'^/dev/dri/card[01]\s', line)]
    additions = (HERE / 'file_contexts').read_text().splitlines()
    # Existing exact patterns must be replaced rather than duplicated.
    keys = {line.split()[0] for line in additions if line and not line.startswith('#')}
    fc.write_text('\n'.join(line for line in old if not line or line.startswith('#') or line.split()[0] not in keys) + '\n' + '\n'.join(additions) + '\n')
    pc = sel / 'vendor_property_contexts'
    additions = (HERE / 'property_contexts').read_text().splitlines()
    keys = {line.split()[0] for line in additions if line and not line.startswith('#')}
    pc.write_text('\n'.join(line for line in pc.read_text().splitlines() if not line or line.startswith('#') or line.split()[0] not in keys) + '\n' + '\n'.join(additions) + '\n')
    inventory = []
    for rc in sorted((source / 'init').rglob('*.rc')):
        rel = rc.relative_to(source / 'init')
        dest = overlay / 'etc/init' / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = rewrite_rc(rc.read_text(), rel, inventory)
        if rel.name == 'init.a9ml-debug.rc':
            # Retry the existing fallback only after the system logd has exited.
            # These event triggers cover a stop before property actions enabled.
            text += ('\n# B4: recover a missed early system-logd stop trigger.\n'
                     'on post-fs-data && property:init.svc.logd=stopped && property:ro.a9ml.vendor=1\n'
                     '    start vendor.a9ml_logd\n'
                     'on property:sys.boot_completed=1 && property:init.svc.logd=stopped && property:ro.a9ml.vendor=1\n'
                     '    start vendor.a9ml_logd\n')
        dest.write_text(text)
    # Same confined auditctl domain; small static tool adds AUDIT_GET readback
    # and sets backlog as well as rate. No daemon PID/audit-enable/rule changes.
    ndk = Path(os.environ.get('ANDROID_NDK_HOME', '/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973'))
    helper = overlay / 'bin/a9ml-audit'
    helper.parent.mkdir(parents=True, exist_ok=True)
    # R9 failure evidence belongs in the normal logcat capture and AOD setup
    # output. Keep the pinned native radio controller/tool manifests intact.
    shutil.copyfile(HERE.parent / 'vendor/bin/a9ml-key-aod.sh', overlay / 'bin/a9ml-key-aod.sh')
    shutil.copyfile(HERE.parent.parent / 'wifi/session/a9ml-wifi-session.sh', overlay / 'bin/a9ml-wifi-session.sh')
    build = [str(ndk / 'toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang'),
             '-std=c11', '-Os', '-static', '-Wall', '-Wextra', '-Werror',
             str(HERE / 'a9ml-audit.c'), '-o', str(helper)]
    with (a.out / 'audit-helper-build.log').open('w') as log:
        subprocess.run(build, check=True, stdout=log, stderr=subprocess.STDOUT)
    shutil.copyfile(HERE / 'init.a9ml-audit.rc', overlay / 'etc/init/init.a9ml-audit.rc')
    inventory.append({'rc': 'init.a9ml-audit.rc', 'service': 'vendor.a9ml_auditctl',
                      'old_domain': None, 'draft_domain': 'u:r:auditctl:s0'})
    (a.out / 'service-inventory.json').write_text(json.dumps(sorted(inventory, key=lambda e: (e['rc'], e['service'])), indent=2) + '\n')
    ver = (sel / 'plat_sepolicy_vers.txt').read_text().strip()
    system = a.platform_cache / 'system'
    p = system / 'etc/selinux'
    files = [p / 'plat_sepolicy.cil', p / f'mapping/{ver}.cil']
    for f in (p / f'mapping/{ver}.compat.cil',
              system / 'system_ext/etc/selinux/system_ext_sepolicy.cil',
              system / f'system_ext/etc/selinux/mapping/{ver}.cil',
              system / f'system_ext/etc/selinux/mapping/{ver}.compat.cil',
              system / 'product/etc/selinux/product_sepolicy.cil',
              system / f'product/etc/selinux/mapping/{ver}.cil'):
        if f.is_file():
            files.append(f)
    files += [sel / 'plat_pub_versioned.cil', cil]
    if (sel / 'odm_sepolicy.cil').is_file():
        files.append(sel / 'odm_sepolicy.cil')
    original_files = list(files)
    removed_permissive = []
    if a.enforcing:
        # Use private compiler inputs, preserving cached GSI policy byte-for-byte.
        # Hash sidecars still describe the actual installed system, so init uses
        # this binary on that matching system rather than the old split policy.
        private = a.out / 'platform-inputs'
        private.mkdir()
        for i, path in enumerate(files):
            text = path.read_text()
            # The diagnostic console now has the observed explicit closure too.
            removed_permissive += re.findall(r'\(typepermissive ([^\s)]+)\)', text)
            text = re.sub(r'\(typepermissive ([^\s)]+)\)', '', text)
            dest = private / (str(i) + '-' + path.name)
            dest.write_text(text)
            files[i] = dest
        for prefix, directory in [('plat', p),
                ('system_ext', system / 'system_ext/etc/selinux'),
                ('product', system / 'product/etc/selinux')]:
            source = directory / (prefix + '_sepolicy_and_mapping.sha256')
            if source.exists():
                shutil.copyfile(source, sel / ('precompiled_sepolicy.' + source.name))
    binary = sel / 'precompiled_sepolicy'
    command = [str(a.secilc), '-m', '-M', 'true', '-G', '-N', '-c', '30',
               *map(str, files), '-o', str(binary), '-f', '/dev/null']
    if a.qualification_audit:
        command.insert(1, '-D')
    (a.out / 'compile-command.json').write_text(json.dumps(command, indent=2) + '\n')
    result = subprocess.run(command, capture_output=True, text=True)
    (a.out / 'compile.log').write_text(result.stdout + result.stderr)
    result.check_returncode()
    # Include the generated binary so old precompiled policy cannot silently
    # bypass the new CIL on a system matching the inherited hash sidecars.
    # On a different system Android falls back to compiling the included CIL.
    report = {
        'status': 'ENFORCING_HOST_LINK_PASS_PHONE_PENDING' if a.enforcing else 'STATIC_DRAFT_RUNTIME_LINK_PASS_NOT_PHONE_TESTED',
        'removed_inherited_permissive': sorted(set(removed_permissive)),
        'intentional_permissive': [],
        'qualification_audit_disable_dontaudit': a.qualification_audit,
        'vendor_image': str(a.vendor_image), 'vendor_sha256': sha(a.vendor_image),
        'platform_inputs': {str(p): sha(p) for p in original_files if not p.is_relative_to(a.out)},
        'vendor_typepermissive': re.findall(r'\(typepermissive (\S+)\)', cil.read_text()),
        'platform_typepermissive': sorted(set(re.findall(
            r'\(typepermissive (\S+)\)', '\n'.join(
                p.read_text() for p in files if not p.is_relative_to(a.out))))),
        'outputs': {str(p.relative_to(a.out)): sha(p) for p in sorted(overlay.rglob('*')) if p.is_file()},
        'linked_policy_bytes': binary.stat().st_size,
    }
    (a.out / 'build-result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(report['status'])


if __name__ == '__main__':
    main()
