#!/usr/bin/env python3
"""Host-only release finalizer for an immutable integrated boot/vendor + GSI.

Preserve upstream release APK signing and re-sign test-signed APEXes in copies.
No phone commands, mounts, key generation, key rotation or partition writes.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess as sp
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = Path.home() / 'A9-build/mainline'
SECURE = {'ro.build.type': 'user', 'ro.build.tags': 'release-keys',
          'ro.debuggable': '0', 'ro.secure': '1', 'ro.adb.secure': '1',
          'ro.force.debuggable': '0'}
REMOVE_VENDOR = {'etc/adb_keys', 'bin/a9ml-ctl.sh', 'bin/a9ml-deadman.sh'}
DROP_SERVICES = {'a9ml_rootsh', 'a9ml_deadman', 'phh-remotectl'}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


meta = load('release_metadata', HERE.parent / 'android/refresh-integrated-vendor.py')


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def read(image, path):
    return sp.check_output(['debugfs', '-R', 'cat /' + path, str(image)], stderr=sp.DEVNULL)


def properties(text, vendor=False):
    values = dict(SECURE, **{'persist.sys.usb.config': 'none'})
    if vendor:
        values.update({'ro.vendor.build.type': 'user', 'ro.vendor.build.tags': 'release-keys',
                       'ro.vendor.a9ml.release': '1'})
    lines = []
    for line in text.splitlines():
        key, sep, value = line.partition('=')
        key = key.strip()
        if key in values and sep:
            continue
        if sep and key.endswith('.build.fingerprint'):
            line = key + '=' + re.sub(r':(?:userdebug|eng)/(?:test-keys|dev-keys)$',
                                      ':user/release-keys', value)
        elif sep and key.endswith('.build.type'):
            line = key + '=user'
        elif sep and key.endswith('.build.tags'):
            line = key + '=release-keys'
        lines.append(line)
    return '\n'.join(lines) + '\n# A9 release configuration\n' + ''.join(
        k + '=' + v + '\n' for k, v in values.items())


def release_rc(text, vendor=False, hardware=False):
    """Remove whole console services/actions, keeping functional root services.

    a9ml_rootsh is ALSO the enforcing domain of the data mount and logger;
    never remove that type/domain or every service with that seclabel.
    """
    result = []
    for block in re.split(r'(?=^(?:service|on|import)\s)', text, flags=re.M):
        lines = block.splitlines(True)
        if not lines:
            continue
        head = lines[0].strip()
        if head.startswith('service ') and head.split()[1] in DROP_SERVICES:
            continue
        if head.startswith('on ') and any(s in head for s in (
                'vendor.a9ml.rootsh=', 'persist.sys.phh.remote=', 'persist.sys.phh.debuggable=')):
            continue
        keep = []
        for line in lines:
            command = line.strip()
            if command.startswith('#'):
                continue
            if any(token in command for token in ('/securize_disable', '/adb_keys',
                    '/acm.gs9', '/ncm.gs8', 'a9ml_test_awake', 'stay_on_while_plugged_in')):
                continue
            if re.match(r'(?:start|stop) (?:a9ml_rootsh|a9ml_deadman|phh-remotectl)$', command):
                continue
            if vendor and command.startswith('exec_background u:r:a9ml_rootsh:s0'):
                continue
            if hardware and command in ('start adbd', 'setprop sys.usb.config adb'):
                continue
            keep.append(line)
        if head.startswith('on ') and not any(l.strip() for l in keep[1:]):
            continue
        result.extend(keep)
    return ''.join(result)


def check_boot(boot):
    data = boot.read_bytes()
    if data[:8] != b'ANDROID!' or struct.unpack_from('<I', data, 40)[0] != 2:
        raise ValueError('release requires an Android v2 boot image')
    primary = data[64:576].split(b'\0')[0].decode().split()
    flags = primary + data[608:1632].split(b'\0')[0].decode().split()
    if 'enforcing=1' not in primary or any(f in flags for f in (
            'enforcing=0', 'selinux_force_permissive=1', 'androidboot.selinux=permissive',
            'selinux=0', 'androidboot.force_debuggable=1')):
        raise ValueError('release requires enforcing in ABL primary cmdline, without debug overrides')


def install(source, target, changes, removed, out):
    """Offline ext4 copy; preserve replacement ownership, labels and timestamps."""
    if target.exists() or source.resolve() == target.resolve():
        raise ValueError('output image exists or aliases input')
    sp.run(['cp', '--reflink=auto', '--sparse=always', str(source), str(target)], check=True)
    batch, expected = [], {}
    for i, (path, data) in enumerate(changes.items()):
        mt = meta.metadata(source, path)
        f = out / ('file-' + str(i)); f.write_bytes(data)
        label = out / ('label-' + str(i)); label.write_bytes(mt['label'].encode() + b'\0')
        batch += ['rm /' + path, f'write {f} /{path}',
                  f'sif /{path} mode 010{mt["mode"]}', f'sif /{path} uid {mt["uid"]}',
                  f'sif /{path} gid {mt["gid"]}', f'ea_set -f {label} /{path} security.selinux']
        for field in ('atime', 'mtime', 'ctime', 'crtime'):
            batch.append(f'sif /{path} {field} @{int(mt[field], 16)}')
        expected[path] = mt
    batch += ['rm /' + path for path in sorted(removed)]
    commands = out / 'install.debugfs'; commands.write_text('\n'.join(batch) + '\n')
    with (out / 'install.log').open('w') as log:
        sp.run(['debugfs', '-w', '-f', str(commands), str(target)], check=True,
               stdout=log, stderr=sp.STDOUT)
    for path, data in changes.items():
        if read(target, path) != data or meta.metadata(target, path) != expected[path]:
            raise RuntimeError('release readback/metadata mismatch: ' + path)
    for path in removed:
        if b'Inode:' in meta.debug(target, 'stat /' + path).encode():
            raise RuntimeError('release removal failed: ' + path)
    with (out / 'fsck.log').open('w') as log:
        sp.run(['e2fsck', '-fn', str(target)], stdout=log, stderr=sp.STDOUT, check=True)


def prepare_policy(system, vendor, out, secilc):
    """Compile against exact installed GSI CIL; no permissive-domain exception."""
    ver = read(vendor, 'etc/selinux/plat_sepolicy_vers.txt').decode().strip()
    paths = ['system/etc/selinux/plat_sepolicy.cil', f'system/etc/selinux/mapping/{ver}.cil',
             'system/system_ext/etc/selinux/system_ext_sepolicy.cil',
             f'system/system_ext/etc/selinux/mapping/{ver}.cil',
             'system/product/etc/selinux/product_sepolicy.cil',
             f'system/product/etc/selinux/mapping/{ver}.cil']
    files, removed = [], []
    for i, path in enumerate(paths + ['etc/selinux/plat_pub_versioned.cil',
                                      'etc/selinux/vendor_sepolicy.cil']):
        text = read(system if path.startswith('system/') else vendor, path).decode()
        removed += re.findall(r'\(typepermissive\s+([^)]+)\)', text)
        f = out / ('input-' + str(i) + '.cil')
        f.write_text(re.sub(r'\(typepermissive\s+[^)]+\)', '', text)); files.append(f)
    binary = out / 'precompiled_sepolicy'
    with (out / 'compile.log').open('w') as log:
        sp.run(list(map(str, [secilc, '-m', '-M', 'true', '-G', '-N', '-c', '30',
                              *files, '-o', binary, '-f', '/dev/null'])), check=True,
               stdout=log, stderr=sp.STDOUT)
    count = permissive_count(binary.read_bytes())
    if count:
        raise RuntimeError('compiled release policy has permissive domains')
    # Exact source selector hashes select this enforcing precompiled binary.
    # Original upstream split CIL stays unchanged; an unsupported GSI is refused
    # by the whole-image trust seal instead of silently falling back to it.
    changes = {'etc/selinux/precompiled_sepolicy': binary.read_bytes(),
               'etc/selinux/vendor_sepolicy.cil': files[-1].read_bytes()}
    for prefix, directory in [('plat', 'system/etc/selinux'),
            ('system_ext', 'system/system_ext/etc/selinux'), ('product', 'system/product/etc/selinux')]:
        name = prefix + '_sepolicy_and_mapping.sha256'
        changes['etc/selinux/precompiled_sepolicy.' + name] = read(system, directory + '/' + name)
    return changes, {'permissive_domains': count, 'removed': removed}


def permissive_count(data):
    """Read the policy-v30 permissive ebitmap, independently of CIL comments."""
    magic, length = struct.unpack_from('<II', data)
    if magic != 0xf97cff8c or data[8:8+length] != b'SE Linux':
        raise ValueError('invalid binary policy header')
    pos = 8 + length
    if struct.unpack_from('<I', data, pos)[0] != 30:
        raise ValueError('unsupported policy version')
    pos += 16
    count = 0
    for index in range(2):  # policycaps then permissive types
        width, _, nodes = struct.unpack_from('<III', data, pos); pos += 12
        if width != 64 or nodes > (len(data)-pos)//12:
            raise ValueError('invalid policy ebitmap')
        for _ in range(nodes):
            _, bits = struct.unpack_from('<IQ', data, pos); pos += 12
            if index == 1:
                count += bits.bit_count()
    return count


def sign_apex(image, path, out, keys, ota, java, env):
    name = Path(path).stem
    key = keys / 'apex' / (name + '.pem')
    for f in (key, keys / 'releasekey.pk8', keys / 'releasekey.x509.pem'):
        if not f.is_file() or stat.S_IMODE(f.stat().st_mode) & 0o077:
            raise ValueError('missing/private-key permissions: ' + str(f))
    original = out / 'original.apex'; original.write_bytes(read(image, path))
    signed = out / 'signed.apex'
    listing = sp.check_output([str(ota/'bin/deapexer'), '--debugfs_path',
                              str(ota/'bin/debugfs_static'), 'list', str(original)], env=env)
    if b'.apk' in listing:
        raise ValueError('APEX with embedded APKs needs a separate signer policy: ' + path)
    with (out/'sign.log').open('w') as log:
        sp.run(list(map(str, [ota/'bin/sign_apex', '-p', ota, '--container_key', keys/'releasekey',
                              '--payload_key', key, '--avbtool', ota/'bin/avbtool', original, signed])),
               check=True, env=env, stdout=log, stderr=sp.STDOUT)
    cert = sp.check_output([str(java), '-jar', str(ota/'framework/apksigner.jar'),
                           'verify', '--print-certs', str(signed)], env=env, text=True)
    expected = sp.check_output(['openssl', 'x509', '-in', str(keys/'releasekey.x509.pem'),
                                '-outform', 'DER'], env=env)
    digest = hashlib.sha256(expected).hexdigest()
    if re.findall(r'certificate SHA-256 digest: ([0-9a-f]+)', cert) != [digest]:
        raise RuntimeError('unexpected release container signer')
    with zipfile.ZipFile(original) as z:
        before = z.read('apex_payload.img')
    with zipfile.ZipFile(signed) as z:
        after = z.read('apex_payload.img')
        pub = z.read('apex_pubkey')
    payload = out / 'payload.img'; payload.write_bytes(before)
    info = sp.check_output([str(ota/'bin/avbtool'), 'info_image', '--image', str(payload)], env=env, text=True)
    size = int(re.search(r'Original image size:\s+(\d+)', info)[1])
    if before[:size] != after[:size]:
        raise RuntimeError('APEX filesystem bytes changed during signing')
    payload.write_bytes(after)
    with (out/'payload-verify.log').open('w') as log:
        sp.run(list(map(str, [ota/'bin/avbtool', 'verify_image', '--image', payload, '--key', key])),
               check=True, env=env, stdout=log, stderr=sp.STDOUT)
    generated = out/'expected.avbpubkey'
    sp.run(list(map(str, [ota/'bin/avbtool', 'extract_public_key', '--key', key, '--output', generated])),
           check=True, env=env)
    if pub != generated.read_bytes():
        raise RuntimeError('APEX embedded public key mismatch')
    data = signed.read_bytes()
    return data, {'certificate_sha256': digest, 'filesystem_bytes_unchanged': size,
                  'payload_public_key_sha256': hashlib.sha256(pub).hexdigest(),
                  'signed_sha256': hashlib.sha256(data).hexdigest()}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ('boot', 'vendor', 'system', 'out'):
        ap.add_argument('--' + name, type=Path, required=True)
    # Private signing dir (releasekey.pk8/.x509.pem): flag or A9_RELEASE_KEYS, never in-tree.
    env_keys = os.environ.get('A9_RELEASE_KEYS')
    ap.add_argument('--keys', type=Path, default=Path(env_keys) if env_keys else None,
                    required=not env_keys)
    ap.add_argument('--system-trust', type=Path, default=HERE/'los-v8-trust.json')
    ap.add_argument('--otatools', type=Path, default=ROOT/'android/dl/cf_arm64_x/otatools')
    ap.add_argument('--java', type=Path, default=Path.home()/'A9-build/tools/jdk21/bin/java')
    a = ap.parse_args()
    os.umask(0o077)
    for image in (a.boot, a.vendor, a.system):
        if not image.is_file() or not stat.S_ISREG(image.stat().st_mode):
            ap.error('inputs must be ordinary host image files')
    check_boot(a.boot)
    profile = json.loads(a.system_trust.read_text())
    if sha(a.system) != profile['system_sha256']:
        ap.error('system differs from audited signing profile; audit a new base before release')
    if a.out.exists():
        ap.error('use a fresh release output directory')
    # Reserve for both copies, one-at-a-time signing and metadata. No bypass.
    budget = a.system.stat().st_size + a.vendor.stat().st_size + a.boot.stat().st_size + 1024**3
    if shutil.disk_usage(a.out.parent).free < 15*1024**3 + budget:
        ap.error('need 15 GiB reserve plus system/vendor copies and 1 GiB signing workspace')
    a.out.mkdir(parents=True)
    env = dict(os.environ, PATH=str(a.java.parent) + ':' + os.environ.get('PATH', ''))
    changes = {'system': {}, 'vendor': {}}
    vfiles = set(meta.vendor_files(a.vendor))
    sfiles = set(meta.vendor_files(a.system))
    if any(p.endswith(('.apk', '.capex')) for p in vfiles):
        ap.error('new vendor APK/compressed APEX needs an explicit release signer mapping')
    if stat.S_IMODE(a.keys.stat().st_mode) & 0o077:
        ap.error('private release key directory must be 0700')
    # Early copied adbd remains the same user-build daemon; secure props make
    # it drop privilege. No resetprop hack or developer authorization is baked.
    for part, image, files in [('system', a.system, sfiles), ('vendor', a.vendor, vfiles)]:
        for path in sorted(files):
            if path.endswith('build.prop') or path.endswith('prop.default'):
                old = read(image, path).decode()
                changes[part][path] = properties(old, part == 'vendor').encode()
            elif path.endswith('.rc') and '/init/' in path:
                old = read(image, path).decode()
                new = release_rc(old, part == 'vendor', path == 'etc/init/hw/init.hlte556n.rc')
                if new != old:
                    changes[part][path] = new.encode()
    changes['vendor']['bin/a9ml-usb.sh'] = (HERE/'a9ml-usb-release.sh').read_bytes()
    signatures = {}
    signing = [('system', p) for p in profile['resign_apexes']] + [
        ('vendor', p) for p in sorted(vfiles) if p.endswith('.apex')]
    # Refuse missing keys before signing/output images; keys are never generated.
    for _, path in signing:
        if not (a.keys/'apex'/(Path(path).stem+'.pem')).is_file():
            ap.error('missing APEX release key; use prepare-keys.py first: ' + Path(path).stem)
    for part, path in signing:
        with tempfile.TemporaryDirectory(prefix='sign-', dir=a.out) as work:
            data, receipt = sign_apex(a.system if part == 'system' else a.vendor,
                                      path, Path(work), a.keys, a.otatools, a.java, env)
        changes[part][path] = data
        signatures[part + '/' + path] = receipt
    policy = a.out/'policy'; policy.mkdir()
    policy_changes, policy_receipt = prepare_policy(a.system, a.vendor, policy, a.otatools/'bin/secilc')
    changes['vendor'].update(policy_changes)
    removed = {'system': set(profile['remove']), 'vendor': REMOVE_VENDOR & vfiles}
    for part, source in [('system', a.system), ('vendor', a.vendor)]:
        work = a.out/part; work.mkdir()
        install(source, a.out/(part+'.img'), changes[part], removed[part], work)
    shutil.copyfile(a.boot, a.out/'boot.img')
    # Trust pin binds every preserved APK/APEX byte to the audited release base.
    for path, record in profile['signed_files'].items():
        if path in removed['system'] or path in changes['system']:
            continue
        if hashlib.sha256(read(a.out/'system.img', path)).hexdigest() != record['sha256']:
            raise RuntimeError('preserved system signing bytes changed: ' + path)
    result = {'host_only': True, 'phone_access': False, 'hardware_tested': False,
              'release': True, 'system_platform_signer_retained': profile['platform_certificate_sha256'],
              'input_sha256': {n: sha(getattr(a, n)) for n in ('boot', 'vendor', 'system')},
              'sha256': {n+'.img': sha(a.out/(n+'.img')) for n in ('boot', 'vendor', 'system')},
              'properties': SECURE, 'policy': policy_receipt, 'apex_signatures': signatures,
              'removed': {k: sorted(v) for k, v in removed.items()},
              'changed': {k: sorted(v) for k, v in changes.items()},
              'boot_bytes_unchanged': True, 'system_trust_sha256': sha(a.system_trust)}
    (a.out/'release-build.json').write_text(json.dumps(result, indent=2)+'\n')
    (a.out/'SHA256SUMS').write_text(''.join(v+'  '+k+'\n' for k, v in result['sha256'].items()))
    print('Release system/vendor/boot assembled; host-only checks passed. Phone qualification pending.')


if __name__ == '__main__':
    main()
