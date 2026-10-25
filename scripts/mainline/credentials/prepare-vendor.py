#!/usr/bin/env python3
"""Host-only stock QTI gatekeeper overlay. Proprietary input stays outside git."""
import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess as sp
import xml.etree.ElementTree as ET
import zipfile

HERE = Path(__file__).resolve().parent
MAINLINE = HERE.parent
BUILD = Path('/home/user/A9-build/mainline')
STOCK = BUILD / 'android/ref/vendor_a.img'
TOOLS = BUILD / 'android/dl/cf_arm64_x/otatools/bin'
APEX = 'apex/com.android.hardware.gatekeeper.nonsecure.apex'
PROP = 'ro.boot.vendor.apex.com.android.hardware.gatekeeper'
SERVICE = 'bin/hw/android.hardware.gatekeeper@1.0-service-qti'
RC = 'etc/init/init.a9-stock-gatekeeper.rc'
VINTF = 'etc/vintf/manifest/android.hardware.gatekeeper-qti.xml'
INTERFACE = 'lib64/android.hardware.gatekeeper@1.0.so'
VNDK_APEX = BUILD / 'adsp/d2-v8-20261017/system-image/system/system_ext/apex/com.android.vndk.v30.apex'
DIAGNOSTICS = MAINLINE / 'qseecom/diagnostics'
DIAG_BINARY = 'bin/a9-rpmb-diag'
DIAG_RC = 'etc/init/init.a9-rpmb-diag.rc'
PERSIST_RC = 'etc/init/init.a9-persist-ro.rc'


def run(args, **kw):
    kw.setdefault('check', True)
    return sp.run(list(map(str, args)), **kw)


def debug(image, command):
    return run(['debugfs', '-R', command, image], capture_output=True).stdout


def exists(image, path):
    return b'Inode:' in debug(image, 'stat /' + path)


def read(image, path):
    if not exists(image, path):
        raise ValueError('missing image member: ' + path)
    return debug(image, 'cat /' + path)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def image_sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def metadata_module():
    spec = importlib.util.spec_from_file_location('vendor_metadata', MAINLINE / 'android/refresh-integrated-vendor.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def merge_properties(data):
    return b'\n'.join(line for line in data.split(b'\n') if line.split(b'=', 1)[0].strip() != PROP.encode())


def merge_manifest(data):
    """Remove only gatekeeper declarations; keep all unrelated declarations."""
    # Preserve unrelated XML bytes and comments instead of reserializing a manifest.
    for match in reversed(list(re.finditer(rb'<hal\b[^>]*>.*?</hal>', data, re.S))):
        element = ET.fromstring(match[0])
        if element.findtext('name') == 'android.hardware.gatekeeper':
            data = data[:match.start()] + data[match.end():]
    ET.fromstring(data)
    return data


def compile_policy(source, work, additions, secilc):
    if read(source, 'etc/selinux/plat_sepolicy_vers.txt').strip() != b'202504':
        raise ValueError('bundled platform policy requires version 202504')
    vendor = read(source, 'etc/selinux/vendor_sepolicy.cil')
    if re.search(rb'\(typepermissive\s+(?:hal_gatekeeper_default|a9_rpmb_diag)\)', vendor):
        raise ValueError('gatekeeper domain must be enforcing')
    for line in additions.splitlines():
        if line.startswith(b'(') and line not in vendor.splitlines():
            vendor += b'\n' + line
    dest = work / 'vendor_sepolicy.cil'
    dest.write_bytes(vendor + b'\n')
    public = work / 'plat_pub_versioned.cil'
    public.write_bytes(read(source, 'etc/selinux/plat_pub_versioned.cil'))
    bundled = MAINLINE / 'boot-deadline/policy-platform'
    manifest = json.loads((bundled / 'inputs.json').read_text())
    files = []
    for name, digest in manifest['files'].items():
        data = gzip.decompress((bundled / (name + '.gz')).read_bytes())
        if sha(data) != digest:
            raise ValueError('platform input hash differs: ' + name)
        path = work / name
        path.write_bytes(data)
        files.append(path)
    args = [secilc, '-D', '-m', '-M', 'true', '-G', '-N', '-c', '30', *files, public, dest,
            '-o', work / 'precompiled_sepolicy', '-f', '/dev/null']
    with (work / 'compile.log').open('wb') as log:
        run(args, stdout=log, stderr=sp.STDOUT)
    (work / 'compile-command.json').write_text(json.dumps(list(map(str, args)), indent=2) + '\n')
    return {f'etc/selinux/{name}': (work / name).read_bytes()
            for name in ('vendor_sepolicy.cil', 'precompiled_sepolicy')}



ATTEST_LIBRARY = 'lib64/a9-wrapped-stock/liba9-attest-fs.so'
KEYMASTER_RC = 'etc/init/init.a9-wrapped-keymaster.rc'


def attest_observer_init(data):
    """Add the passive preload only to the exact stock qseecomd service."""
    blocks = re.split(rb'(?=^(?:service|on|import)[ \t])', data, flags=re.M)
    matches = 0
    directive = b'    setenv LD_PRELOAD /vendor/' + ATTEST_LIBRARY.encode() + b'\n'
    for i, block in enumerate(blocks):
        if not re.match(rb'^service vendor\.qseecomd /vendor/bin/qseecomd[ \t]*\n', block):
            continue
        matches += 1
        existing = re.findall(rb'^[ \t]+setenv[ \t]+LD_PRELOAD[^\n]*\n', block, re.M)
        if existing and existing != [directive]:
            raise ValueError('unexpected qseecomd preload; reconcile explicitly')
        if not existing:
            head, tail = block.split(b'\n', 1)
            blocks[i] = head + b'\n' + directive + tail
    if matches != 1:
        raise ValueError('need exactly one stock qseecomd service')
    return b''.join(blocks)


def attest_observer_payload(source, out):
    spec = importlib.util.spec_from_file_location('build_attest_observer', DIAGNOSTICS / 'attest-build.py')
    builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)
    return {ATTEST_LIBRARY: builder.build(Path(out) / 'attest-observer').read_bytes(),
            KEYMASTER_RC: attest_observer_init(read(source, KEYMASTER_RC))}


def omit_legacy_qsee_directory(data):
    """Keep GPFS's stock vendor-data routing on freshly formatted data.

    Stock vendor init creates tzstorage; our inherited builder additionally
    creates /data/misc/qsee. libdrmfs checks the parent before its vendor-data
    rewrite, so creating that legacy parent can select a different store.
    Remove only our inherited init directives; never delete or migrate files.
    """
    for line in (b'    mkdir /data/misc/qsee 0770 system system\n',
                 b'    restorecon_recursive /data/misc/qsee\n'):
        data = data.replace(line, b'')
    return data


def gatekeeper_interface(apex, out, pin):
    """Ship the stock-version HIDL interface inside the vendor namespace.

    /system/lib64 having a copy is insufficient for a vendor executable. The
    selected VNDK30 package is pinned; never borrow an arbitrary host library.
    """
    if image_sha(apex) != pin['apex_sha256']:
        raise ValueError('Gatekeeper VNDK30 APEX mismatch')
    image = out / 'vndk30-interface.img'
    with zipfile.ZipFile(apex) as archive, archive.open('apex_payload.img') as src, image.open('wb') as dest:
        shutil.copyfileobj(src, dest)
    data = read(image, pin['member'])
    if sha(data) != pin['sha256']:
        raise ValueError('Gatekeeper VNDK30 interface mismatch')
    image.unlink()  # Only this invocation's temporary extraction.
    return data


def persist_ro_startup(data):
    """Wait for the separate read-only mount worker before TEE startup."""
    anchor = b'    exec_start a9ml_metadata_mount\n'
    if data.count(anchor) != 1:
        raise ValueError('need exactly one metadata startup anchor')
    directive = b'    exec_start a9ml_persist_ro\n'
    data = data.replace(anchor + directive, anchor)
    return data.replace(anchor, anchor + directive)


def rpmb_bootdevice_startup(data):
    """Restore stock librpmb's legacy discovery link before its cached init."""
    anchor = b'    start vendor.qseecomd\n'
    if data.count(anchor) != 1:
        raise ValueError('need exactly one qseecomd startup anchor')
    directive = (b'    # Stock librpmb selects UFS from this legacy directory link.\n'
                 b'    wait /dev/block/platform/soc@0/4804000.ufshc 5\n'
                 b'    symlink /dev/block/platform/soc@0/4804000.ufshc /dev/block/bootdevice\n')
    if data.count(directive) > 1:
        raise ValueError('duplicate RPMB bootdevice setup')
    data = data.replace(directive, b'')
    if re.search(rb'^\s*symlink\s+\S+\s+/dev/block/bootdevice\s*$', data, re.M):
        raise ValueError('conflicting bootdevice symlink; reconcile explicitly')
    return data.replace(anchor, directive + anchor)


def prepare(source, target, out, stock_ref=STOCK, secilc=TOOLS / 'secilc', verifier=TOOLS / 'host_init_verifier', vndk_apex=VNDK_APEX):
    """Copy immutable source into fresh target; return changes/additions/removals."""
    source, target, out, stock_ref = map(lambda p: Path(p).resolve(), (source, target, out, stock_ref))
    if target in (source, stock_ref) or target.exists():
        raise ValueError('target must be fresh and distinct from immutable inputs')
    # debugfs batch syntax uses unquoted paths. Refuse ambiguous host paths.
    if any(not re.fullmatch(r'[A-Za-z0-9_./+@-]+', str(p)) for p in (source, target, out, stock_ref)):
        raise ValueError('unsupported host path characters')
    if out.exists() and any(out.iterdir()):
        raise ValueError('use a fresh output directory')
    out.mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(out).free < 15 * 1024**3 + 700 * 1024**2:
        raise RuntimeError('need 15GiB floor plus 700MiB task budget')
    os.umask(0o077)
    meta = metadata_module()
    source_hash = image_sha(source)
    stock_hash = image_sha(stock_ref)
    assets = json.loads((HERE / 'assets.json').read_text())
    payload = {}
    # Pin each source member as well as all reused stock private ABI libraries.
    for path, digest in assets['install'].items():
        data = read(stock_ref, path)
        if sha(data) != digest:
            raise ValueError('stock gatekeeper asset mismatch: ' + path)
        payload[path] = data
    payload[INTERFACE] = gatekeeper_interface(vndk_apex, out, assets['vndk_interface'])
    # Passthrough dlopen must resolve in the normal vendor namespace. The
    # service's private environment did not expose these helpers in boot122.
    for path, item in assets.get('default_helpers', {}).items():
        data = read(source, item['source'])
        if sha(data) != item['sha256']:
            raise ValueError('default helper source mismatch: ' + path)
        payload[path] = data
    for path, digest in assets['reuse'].items():
        if sha(read(source, path)) != digest:
            raise ValueError('incoming QTI private ABI mismatch: ' + path)
    if not exists(source, 'bin/hw/android.hardware.keymaster@4.1-service-qti'):
        raise ValueError('QTI Keymaster service is required')
    payload[RC] = (HERE / 'init.a9-stock-gatekeeper.rc').read_bytes()
    payload[VINTF] = (HERE / 'android.hardware.gatekeeper-qti.xml').read_bytes()
    payload['build.prop'] = merge_properties(read(source, 'build.prop'))
    # Independent read-only kmsg cursor; survives any early-logger failure.
    spec = importlib.util.spec_from_file_location('build_rpmb_diag', DIAGNOSTICS / 'build.py')
    builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)
    payload[DIAG_BINARY] = builder.build(out / 'diagnostics').read_bytes()
    payload[DIAG_RC] = (DIAGNOSTICS / 'init.a9-rpmb-diag.rc').read_bytes()
    payload[PERSIST_RC] = (HERE / 'init.a9-persist-ro.rc').read_bytes()
    # Standalone preparation must not pair the new service with an inherited
    # script that still binds writable metadata over persist.
    payload['bin/a9ml-wrapped-start.sh'] = (MAINLINE / 'reboot/vendor/bin/a9ml-wrapped-start.sh').read_bytes()
    payload.update(attest_observer_payload(source, out))
    hardware = 'etc/init/hw/init.hlte556n.rc'
    hw = omit_legacy_qsee_directory(read(source, hardware))
    # The metadata service no longer overlays an isolated writable TEE store.
    # Give physical persist its own bounded read-only worker before TEE startup.
    hw = persist_ro_startup(hw)
    anchor = b'    start vendor.qseecomd\n'
    if anchor not in hw:
        raise ValueError('QTI startup anchor absent')
    hw = hw.replace(b'    start a9_rpmb_diag\n' + anchor, anchor)
    hw = rpmb_bootdevice_startup(hw)
    payload[hardware] = hw.replace(anchor, b'    start a9_rpmb_diag\n' + anchor)
    removed = [APEX] if exists(source, APEX) else []
    existing = list(meta.vendor_files(source))
    for path in existing:
        if (path == 'etc/vintf/manifest.xml' or path.startswith('etc/vintf/manifest/')) and path.endswith('.xml') and path != VINTF:
            old = read(source, path)
            new = merge_manifest(old)
            if new != old:
                payload[path] = new
    fp_spec = importlib.util.spec_from_file_location('fingerprint_privacy', HERE / 'fingerprint-privacy.py')
    fp_privacy = importlib.util.module_from_spec(fp_spec); fp_spec.loader.exec_module(fp_privacy)
    def current(name):
        return payload[name] if name in payload else read(source, name)
    payload.update(fp_privacy.payload(current))
    payload.update(compile_policy(source, out, (HERE / 'gatekeeper.cil').read_bytes() + b'\n' +
                                  (HERE / 'persist-ro.cil').read_bytes() + b'\n' +
                                  (DIAGNOSTICS / 'policy.cil').read_bytes() + b'\n' +
                                  (HERE / 'fingerprint-privacy.cil').read_bytes(), secilc))
    contexts = 'etc/selinux/vendor_file_contexts'
    fc = payload[contexts]
    # These are expected stock names, not a relabel operation. The physical
    # mount retains existing xattrs; no init restorecon runs on that mount.
    fc = fc.replace(b'/mnt/vendor/persist(/.*)? u:object_r:tee_data_file:s0\n', b'')
    for line in (b'/mnt/vendor/persist(/.*)? u:object_r:mnt_vendor_file:s0',
                 b'/mnt/vendor/persist/data(/.*)? u:object_r:vendor_persist_data_file:s0'):
        if line not in fc.splitlines():
            fc += b'\n' + line + b'\n'
    line = b'/vendor/bin/hw/android\\.hardware\\.gatekeeper@1\\.0-service-qti u:object_r:hal_gatekeeper_default_exec:s0'
    if line not in fc.splitlines():
        fc += b'\n' + line + b'\n'
    line = b'/vendor/bin/a9-rpmb-diag u:object_r:a9_rpmb_diag_exec:s0'
    if line not in fc.splitlines():
        fc += b'\n' + line + b'\n'
    payload[contexts] = fc
    hwcontexts = 'etc/selinux/vendor_hwservice_contexts'
    hc = read(source, hwcontexts)
    line = b'android.hardware.gatekeeper::IGatekeeper u:object_r:hal_gatekeeper_hwservice:s0'
    matches = [l for l in hc.splitlines() if l.split()[:1] == [b'android.hardware.gatekeeper::IGatekeeper']]
    if matches and matches != [line]:
        raise ValueError('conflicting gatekeeper hwservice context')
    if not matches:
        payload[hwcontexts] = hc + b'\n' + line + b'\n'
    rc_path = out / 'gatekeeper.rc'
    rc_path.write_bytes(payload[RC])
    with (out / 'init-verifier.log').open('wb') as log:
        run([verifier, rc_path], stdout=log, stderr=sp.STDOUT)
        rc_path = out / 'diagnostics.rc'; rc_path.write_bytes(payload[DIAG_RC])
        run([verifier, rc_path], stdout=log, stderr=sp.STDOUT)
        rc_path = out / 'persist-ro.rc'; rc_path.write_bytes(payload[PERSIST_RC])
        run([verifier, rc_path], stdout=log, stderr=sp.STDOUT)
        rc_path = out / 'fingerprint-privacy.rc'; rc_path.write_bytes(payload['etc/init/init.a9-fingerprint.rc'])
        run([verifier, rc_path], stdout=log, stderr=sp.STDOUT)
        rc_path = out / 'qseecomd-observer.rc'; rc_path.write_bytes(payload[KEYMASTER_RC])
        run([verifier, rc_path], stdout=log, stderr=sp.STDOUT)
    run(['cp', '--reflink=auto', '--sparse=always', source, target])
    if run(['e2fsck', '-fy', target], capture_output=True, check=False).returncode > 1:
        raise ValueError('copied source filesystem is invalid')
    commands = ['rm /' + p for p in removed]
    changed, added, expected = {}, {}, {}
    for index, (path, data) in enumerate(sorted(payload.items())):
        present = exists(source, path)
        old = read(source, path) if present else None
        if old == data:
            continue
        # All destinations have existing parents on supported integrated vendors.
        parent = str(Path(path).parent)
        if not exists(source, parent):
            raise ValueError('required vendor directory absent: ' + parent)
        info = meta.metadata(source, path) if present else meta.metadata(source, 'bin/a9ml-eink.sh' if path in (SERVICE, DIAG_BINARY) else 'etc/init/hw/init.hlte556n.rc').copy()
        if not present:
            info.update(mode='0755' if path in (SERVICE, DIAG_BINARY) else '0644', uid='0', gid='0',
                        label='u:object_r:hal_gatekeeper_default_exec:s0' if path == SERVICE else
                        'u:object_r:vendor_file:s0' if path.startswith('lib64/') else 'u:object_r:vendor_configs_file:s0')
        if path == SERVICE:
            info['label'] = 'u:object_r:hal_gatekeeper_default_exec:s0'
        if path == DIAG_BINARY:
            info['label'] = 'u:object_r:a9_rpmb_diag_exec:s0'
        file = out / ('payload-' + str(index))
        file.write_bytes(data)
        label = out / ('label-' + str(index))
        label.write_bytes(info['label'].encode() + b'\0')
        if present:
            commands.append('rm /' + path)
        commands += [f'write {file} /{path}', f'sif /{path} mode 010{info["mode"]}',
                     f'sif /{path} uid {info["uid"]}', f'sif /{path} gid {info["gid"]}',
                     f'ea_set -f {label} /{path} security.selinux']
        commands += [f'sif /{path} {t} @{int(info[t], 16)}' for t in ('atime', 'mtime', 'ctime', 'crtime')]
        expected[path] = info
        (changed if present else added)[path] = {'before': sha(old) if present else None, 'after': sha(data)}
    script = out / 'install.debugfs'
    script.write_text('\n'.join(commands) + '\n')
    with (out / 'install.log').open('wb') as log:
        run(['debugfs', '-w', '-f', script, target], stdout=log, stderr=sp.STDOUT)
    for path, info in expected.items():
        if read(target, path) != payload[path] or meta.metadata(target, path) != info:
            raise ValueError('payload/metadata readback differs: ' + path)
    for path in removed:
        if exists(target, path):
            raise ValueError('software gatekeeper APEX remains')
    if PROP.encode() in read(target, 'build.prop'):
        raise ValueError('software gatekeeper selection remains')
    if source.stat().st_size != target.stat().st_size:
        raise ValueError('unexpected image growth')
    with (out / 'fsck.log').open('wb') as log:
        run(['e2fsck', '-fn', target], stdout=log, stderr=sp.STDOUT)
    if image_sha(source) != source_hash or image_sha(stock_ref) != stock_hash:
        raise ValueError('immutable source changed during preparation')
    digest = image_sha(target)
    Path(str(target) + '.sha256').write_text(digest + '  ' + target.name + '\n')
    result = dict(source=str(source), source_sha256=source_hash, stock_ref_sha256=stock_hash,
                  target=str(target), sha256=digest, changed=changed, added=added, removed=removed,
                  stock_manifest_sha256=image_sha(HERE / 'assets.json'), policy_files=['etc/selinux/vendor_sepolicy.cil', 'etc/selinux/precompiled_sepolicy'], init=RC, manifest=VINTF,
                  vendor_interface=dict(path=INTERFACE, sha256=assets['vndk_interface']['sha256'], source_apex_sha256=assets['vndk_interface']['apex_sha256']),
                  diagnostics=dict(binary=DIAG_BINARY, init=DIAG_RC, domain='a9_rpmb_diag', transport='logdw-main-WARN', kernel_input='read-only /dev/kmsg'),
                  attest_observer=dict(library=ATTEST_LIBRARY, init=KEYMASTER_RC, passive=True, schema='A9_SFS_OPEN', event_cap=None),
                  fingerprint_privacy=dict(hal_sha256=sha(payload[fp_privacy.HAL]), stock_user_debug_closed=True, dump_root_type='a9_fp_dump_file'),
                  persist_readonly=dict(init=PERSIST_RC, script='bin/a9ml-wrapped-start.sh', fs='ext4', options='ro,noload,nosuid,nodev,noexec', block_readonly=True, relabel=False), image_grown=False, device_tested=False)
    (out / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base-vendor', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--stock-ref', type=Path, default=STOCK)
    args = ap.parse_args()
    print(json.dumps(prepare(args.base_vendor, args.out / 'vendor.img', args.out, args.stock_ref), indent=2))
