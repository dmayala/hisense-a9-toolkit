#!/usr/bin/env python3
"""Freeze private active-S1 images and sidecar; never admit or contact a device."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tarfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
WORK = Path.home() / 'A9-build/mainline'
FLOOR = 15_000_000_000
SEED_MAX_MEMBERS = 8192
SEED_MAX_FILES = 4096
SEED_MAX_TOTAL = 32 * 1024 * 1024
SEED_MAX_FILE = 8 * 1024 * 1024
# system_b is v8 (coordinator, 2026-10-17). Only these two live files differ from the v7 D1 manifest.
V8_IMAGE_SHA = '14525f4852a3c56f816a6de15fd45b93bdf390503b5c8e01b47b2ff6331ef793'
V8_CHANGED = ('/system/framework/services.jar', '/system/priv-app/a9service.apk')
V8_BINDING = WORK / 'adsp/d2-v8-20261017'
SOC0_FILES = ('hw_platform', 'platform_subtype', 'platform_subtype_id', 'platform_version', 'revision', 'soc_id')
EMPTY_SHA = hashlib.sha256(b'').hexdigest()


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def ordinary_file(path, maximum):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= maximum:
        raise ValueError('bounded ordinary unlinked source file required')


def seed_name(name, directory=False):
    if directory and name.endswith('/'):
        name = name[:-1]
    if (not re.fullmatch(r'[A-Za-z0-9_./+-]+', name) or
            any(part in ('', '.', '..') for part in name.split('/')) or
            not name.startswith('sensors/')):
        raise ValueError('unsafe registry archive member path')
    return name


def validate_seed_archive(archive, registry_manifest):
    """Validate without extraction; hashes bind the exact subsequently copied seed.

    Both the TAR namespace and its absolute target hash manifest are untrusted.
    Refuse links/devices/sparse/PAX/duplicates and file/directory conflicts before
    any future root tar extraction. Never include a private member name in errors.
    """
    ordinary_file(archive, SEED_MAX_TOTAL + SEED_MAX_MEMBERS * 1024 + 10240)
    ordinary_file(registry_manifest, 2 * 1024 * 1024)
    archive_digest, manifest_digest = sha(archive), sha(registry_manifest)
    expected = {}
    for line in registry_manifest.read_text(encoding='ascii').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  /mnt/vendor/persist/(sensors/.+)', line)
        if not match:
            raise ValueError('invalid private registry hash manifest')
        name = seed_name(match[2])
        if name in expected:
            raise ValueError('duplicate private registry hash manifest entry')
        expected[name] = match[1]
        if len(expected) > SEED_MAX_FILES:
            raise ValueError('oversized private registry hash manifest')
    if not expected:
        raise ValueError('empty private registry hash manifest')
    nodes, actual, total = {}, {}, 0
    with tarfile.open(archive, 'r:') as tar:
        for member in tar:
            if len(nodes) >= SEED_MAX_MEMBERS:
                raise ValueError('too many registry archive members')
            if member.type not in (tarfile.REGTYPE, tarfile.DIRTYPE) or member.pax_headers or member.sparse:
                raise ValueError('registry archive links or special metadata forbidden')
            if member.offset_data != member.offset + 512:
                raise ValueError('registry archive extension headers forbidden')
            directory = member.type == tarfile.DIRTYPE
            name = seed_name(member.name, directory)
            if name in nodes:
                raise ValueError('duplicate registry archive member')
            parents = ['/'.join(name.split('/')[:i]) for i in range(1, len(name.split('/')))]
            if any(nodes.get(parent) == 'file' for parent in parents):
                raise ValueError('registry archive file/directory conflict')
            if not directory and any(path.startswith(name + '/') for path in nodes):
                raise ValueError('registry archive file/directory conflict')
            if (member.uid or member.gid or member.uname or member.gname or
                    member.mode != (0o700 if directory else 0o600)):
                raise ValueError('registry archive ownership or mode is not normalized')
            nodes[name] = 'directory' if directory else 'file'
            if directory:
                if member.size:
                    raise ValueError('registry archive directory contains data')
                continue
            if not 0 <= member.size <= SEED_MAX_FILE:
                raise ValueError('oversized registry archive file')
            total += member.size
            if total > SEED_MAX_TOTAL or len(actual) >= SEED_MAX_FILES:
                raise ValueError('oversized registry archive payload')
            stream = tar.extractfile(member)
            if stream is None:
                raise ValueError('unreadable registry archive payload')
            digest, count = hashlib.sha256(), 0
            with stream:
                for block in iter(lambda: stream.read(65536), b''):
                    count += len(block)
                    if count > member.size:
                        raise ValueError('registry archive payload length mismatch')
                    digest.update(block)
            if count != member.size:
                raise ValueError('registry archive payload length mismatch')
            actual[name] = digest.hexdigest()
        end_offset = tar.offset
    # A second concatenated archive or hidden metadata after the first end
    # marker must not be interpreted differently by the eventual phone tar.
    trailing = 0
    with archive.open('rb') as stream:
        stream.seek(end_offset)
        for block in iter(lambda: stream.read(65536), b''):
            trailing += len(block)
            if block.strip(b'\0'):
                raise ValueError('registry archive has nonzero unparsed trailer')
    if trailing < 1024 or archive.stat().st_size % 512:
        raise ValueError('registry archive end marker or block alignment missing')
    if (nodes.get('sensors/registry') != 'directory' or
            nodes.get('sensors/registry/registry') != 'directory' or
            not any(name.startswith('sensors/registry/registry/') for name in actual)):
        raise ValueError('factory registry archive layout required')
    if actual != expected:
        raise ValueError('registry archive payload and hash manifest differ')
    if sha(archive) != archive_digest or sha(registry_manifest) != manifest_digest:
        raise ValueError('private registry sources changed during validation')
    return {'archive_sha256': archive_digest, 'manifest_sha256': manifest_digest,
            'regular_files': len(actual), 'members': len(nodes), 'payload_bytes': total,
            'status': 'PASS-BOUNDED-REGISTRY-ARCHIVE'}


def v8_system_binding(binding):
    """Digests of the v8 system files, derived from the v8 image, never typed.

    The D2 read-only binding (fresh debugfs walk of the pinned image: every inode,
    label, file byte and symlink target) supplies the inventory. Here its receipt
    is bound to the image digest, and the two files that differ from v7 are read
    again straight out of the raw image with debugfs. No mount, no device."""
    receipt = json.loads((binding / 'system-image.json').read_text())
    inventory_path = binding / 'system-files.json'
    if (receipt['status'] != 'PASS-READONLY-SYSTEM-BINDING' or receipt['image_sha256'] != V8_IMAGE_SHA or
            receipt['files_sha256'] != sha(inventory_path) or
            receipt['manifest_sha256'] != sha(binding / 'system-image.manifest.tsv')):
        raise ValueError('v8 system binding receipt is not the pinned verified v8 binding')
    image = Path(receipt['image'])
    # The private path may be a symlink to the identical verified read-only NFS copy (digest-checked below).
    real = image.resolve(strict=True)
    if (not image.is_relative_to(Path.home() / 'A9-build') or
            not (real.is_relative_to(Path.home() / 'A9-build') or real.is_relative_to((Path.home() / 'a9-assets/roms/a16').resolve())) or
            real.stat().st_size != receipt['image_bytes']):
        raise ValueError('v8 image must be the private A9-build path (local file or symlink to the read-only roms copy)')
    if sha(image) != V8_IMAGE_SHA:
        raise ValueError('system image digest mismatch')
    inventory = json.loads(inventory_path.read_text())
    for path in V8_CHANGED:
        record = inventory[path]
        digest, count = hashlib.sha256(), 0
        child = subprocess.Popen(['debugfs', '-R', 'cat ' + path, str(image)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            for block in iter(lambda: child.stdout.read(1048576), b''):
                digest.update(block); count += len(block)
            if child.wait(timeout=10) != 0:
                raise ValueError('debugfs read failed')
        finally:
            if child.poll() is None:
                child.kill(); child.wait()
        if digest.hexdigest() != record['sha256'] or count != record['bytes']:
            raise ValueError('v8 inventory disagrees with the raw image: ' + path)
    return receipt, inventory


def bind_dt_recipe(kernel, d1, checker=None):
    checker = checker or load('s1-kernel-check')
    recipe, built_recipe = HERE / 'a9-sensors-s1.dtsi', kernel / 'a9-sensors-s1.dtsi'
    ordinary_file(recipe, 1024 * 1024)
    ordinary_file(built_recipe, 1024 * 1024)
    if sha(recipe) != sha(built_recipe):
        raise ValueError('S1 DT recipe changed after the kernel build')
    baseline = d1 / 'build/arch/arm64/boot/dts/qcom/sm6115-hisense-hlte556n-android-gpu-thermal-adsp.dtb'
    checker.check_dt(checker.D0.read_fdt(baseline), checker.D0.read_fdt(kernel / 's1.dtb'))
    return {'recipe_sha256': sha(recipe), 'baseline_dtb_sha256': sha(baseline),
            's1_dtb_sha256': sha(kernel / 's1.dtb'), 'status': 'PASS-EXACT-DT-DELTA'}


def recipe_hashes():
    paths = {p for p in HERE.glob('s1-*') if p.is_file()} | {p for p in HERE.glob('s2*') if p.is_file()}
    paths.update((HERE / 'a9-sensors-s1.dtsi', HERE / 's1.config'))
    paths.update(p for p in (HERE / 's1-soc0').iterdir() if p.is_file())
    return {p.relative_to(REPO).as_posix(): sha(p) for p in sorted(paths)}


def manifest(root):
    files = [p for p in sorted(root.rglob('*')) if p.is_file() and p.name not in ('SHA256SUMS', 'tuple')]
    (root / 'SHA256SUMS').write_text(''.join(sha(p) + '  ' + p.relative_to(root).as_posix() + '\n' for p in files))
    digest = sha(root / 'SHA256SUMS')
    (root / 'tuple').write_text(digest + '\n')
    return digest


def prepare(out, kernel, images, listener, observer, seed_bundle, d1, system_binding=V8_BINDING, probe=None):
    """probe=None: the S1 bundle exactly as before. probe=<s2-sns-probe>: the S2a bundle (s2a-derive target + probe)."""
    prep = load('s1-prepare')
    v8_receipt, v8_inventory = v8_system_binding(system_binding)
    out = out.expanduser().absolute()
    if (out.parent != WORK / 'sensors-s1' or out.parent.resolve() != out.parent or
            out.exists() or out.is_symlink()):
        raise ValueError('fresh canonical private S1 output required')
    k = json.loads((kernel / 'kernel-receipt.json').read_text())
    image = json.loads((images / 'image-receipt.json').read_text())
    if k['status'] != 'PASS-OFFLINE-KERNEL' or image['status'] != 'PASS-OFFLINE-IMAGE-READBACK':
        raise ValueError('completed kernel and image readback required')
    if image['kernel_receipt'] != sha(kernel / 'kernel-receipt.json'):
        raise ValueError('kernel/image receipt mismatch')
    if k['config'] != sha(kernel / 'build/.config') or k['dtb'] != sha(kernel / 's1.dtb'):
        raise ValueError('kernel configuration/DT changed')
    if (sha(kernel / 'build/arch/arm64/boot/Image.gz') != k['Image.gz'] or
            image['sections']['kernel'] != k['Image.gz'] or
            sha(kernel / 'kernel.notes') != k['kernel_notes']):
        raise ValueError('kernel image/notes changed')
    boot_name = 'a9-android-eink-boot-gpu.img'
    vendor_name = 'vendor_b-eink-gpu-bt-health-thermal.img'
    if sha(images / boot_name) != image['boot'] or sha(images / vendor_name) != image['vendor']:
        raise ValueError('built image changed')
    if k['fastrpc.ko'] != sha(kernel / 'fastrpc-module/fastrpc.ko'):
        raise ValueError('gated module changed')
    if k['gate_patch'] != sha(HERE / 's1-kernel-fastrpc-gate.patch'):
        raise ValueError('gate patch changed')
    load('s1-kernel-check').check_config((d1 / 'build/.config').read_text(), (kernel / 'build/.config').read_text())
    dt_binding = bind_dt_recipe(kernel, d1)
    for name, digest in prep.IMAGES.items():
        if sha(d1 / name) != digest:
            raise ValueError('measured D1 source changed')
    for binary in (listener, observer) + ((probe,) if probe else ()):
        prep.static_observer(binary)
    if probe:
        pb = json.loads(probe.with_name(probe.name + '.build.json').read_text())
        if pb['output_sha256'] != sha(probe) or not pb['static_aarch64'] or set(pb['sources']) != {'s2-sns.c', 's2-client.c', 's2-qrtr.c', 's2-sns-probe.c', 's2-sns.h', 's2-client.h', 's2-qrtr.h'}:
            raise ValueError('probe build receipt mismatch')
        for name, digest in pb['sources'].items():
            if sha(HERE / name) != digest:
                raise ValueError('probe source changed since build')
    lb = json.loads(listener.with_name(listener.name + '.build.json').read_text())
    if lb['output_sha256'] != sha(listener) or not lb['static_aarch64']:
        raise ValueError('listener build receipt mismatch')
    for name, digest in lb['sources'].items():
        if sha(HERE / name) != digest:
            raise ValueError('listener source changed since build')
    ob = json.loads(observer.with_name(observer.name + '.build.json').read_text())
    if ob['output_sha256'] != sha(observer) or ob['source_sha256'] != sha(HERE / 's1-observe.c'):
        raise ValueError('observer build receipt mismatch')
    if sha(d1 / 'bundle/SHA256SUMS') != prep.D1_TUPLE:
        raise ValueError('D1 bundle not the measured source')
    d1_files = prep.checked_tree(d1 / 'bundle', max_bytes=128*1024*1024)
    d1_expected = {'SHA256SUMS', 'tuple'}
    for line in (d1 / 'bundle/SHA256SUMS').read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        if name.startswith('/') or '..' in Path(name).parts or sha(d1 / 'bundle' / name) != digest:
            raise ValueError('D1 sidecar file changed')
        d1_expected.add(name)
    if {p.relative_to(d1 / 'bundle').as_posix() for p in d1_files} != d1_expected:
        raise ValueError('unmanifested D1 sidecar file')
    seed_manifest = seed_bundle / 'SHA256SUMS'
    if sha(seed_manifest) != (seed_bundle / 'tuple').read_text().strip():
        raise ValueError('private seed bundle manifest changed')
    seed_files = dict((name, digest) for digest, name in (line.split(maxsplit=1) for line in seed_manifest.read_text().splitlines()))
    for name in ('registry-seed.tar', 'registry.sha256'):
        if sha(seed_bundle / name) != seed_files[name]:
            raise ValueError('private seed changed')
    seed_validation = validate_seed_archive(seed_bundle / 'registry-seed.tar', seed_bundle / 'registry.sha256')
    prep.checked_tree(seed_bundle / 'vendor-config')
    for name, digest in seed_files.items():
        if name.startswith('vendor-config/') and (not (seed_bundle/name).is_file() or sha(seed_bundle/name) != digest):
            raise ValueError('private vendor config missing or changed')
    for p in (seed_bundle / 'vendor-config').rglob('*'):
        if p.is_file() and sha(p) != seed_files[p.relative_to(seed_bundle).as_posix()]:
            raise ValueError('private vendor config changed')
    generated = load('s2a-derive' if probe else 's1-active-derive').derive()
    copy_sources = [images/boot_name, images/vendor_name, kernel/'build/arch/arm64/boot/Image.gz',
                    kernel/'build/.config', kernel/'kernel.notes', kernel/'s1.dtb',
                    d1/'snapshot/recovery_dtbo.img', listener, observer, *((probe,) if probe else ()),
                    seed_bundle/'registry-seed.tar', seed_bundle/'registry.sha256']
    projected = sum(p.stat().st_size for p in copy_sources + d1_files)
    projected += sum(p.stat().st_size for p in (seed_bundle/'vendor-config').rglob('*') if p.is_file())
    projected += 64_000_000  # generated manifests/receipts and filesystem overhead
    if shutil.disk_usage('/').free < FLOOR + projected:
        raise RuntimeError('insufficient projected space above 15 GB floor; ask coordinator')
    os.umask(0o077)
    out.mkdir(mode=0o700)
    aw = out / 'android/out'; aw.mkdir(parents=True)
    for name in (boot_name, vendor_name):
        shutil.copyfile(images / name, aw / name)
        (aw / name).chmod(0o400)
        (aw / (name + '.sha256')).write_text(sha(aw / name) + '  ' + name + '\n')
    snap = out / 'snapshot'; snap.mkdir()
    for source, name in ((kernel / 'build/arch/arm64/boot/Image.gz', 'Image.gz'),
                         (kernel / 'build/.config', 'config'), (kernel / 'kernel.notes', 'kernel.notes'),
                         (kernel / 's1.dtb', 'sm6115-hisense-hlte556n-android-gpu.dtb'),
                         (d1 / 'snapshot/recovery_dtbo.img', 'recovery_dtbo.img')):
        shutil.copyfile(source, snap / name)
    bundle = out / 'bundle'; shutil.copytree(d1 / 'bundle', bundle)
    for name in ('SHA256SUMS', 'tuple', 'd1-target.sh'):
        (bundle / name).unlink()
    for name, text in generated.items():
        (bundle / name).write_text(text); (bundle / name).chmod(0o500)
    for binary, name in ((listener, 's1-listener'), (observer, 's1-observe')) + (((probe, 's2-sns-probe'),) if probe else ()):
        shutil.copyfile(binary, bundle / name); (bundle / name).chmod(0o500)
    for name in ('s1-registry-ram.sh', 's1-listener-root.sh'):
        shutil.copyfile(HERE / name, bundle / name)
    for name in ('registry-seed.tar', 'registry.sha256'):
        shutil.copyfile(seed_bundle / name, bundle / name)
    if (sha(bundle / 'registry-seed.tar') != seed_validation['archive_sha256'] or
            sha(bundle / 'registry.sha256') != seed_validation['manifest_sha256']):
        raise ValueError('private registry changed before the frozen copy')
    shutil.copytree(seed_bundle / 'vendor-config', bundle / 'vendor-config')
    soc0_source = HERE / 's1-soc0'
    if sorted(p.name for p in soc0_source.iterdir() if p.is_file()) != sorted(SOC0_FILES + ('PROVENANCE.txt',)):
        raise ValueError('unexpected s1-soc0 file set')
    (bundle / 'soc0').mkdir()
    for name in SOC0_FILES:
        ordinary_file(soc0_source / name, 64)
        shutil.copyfile(soc0_source / name, bundle / 'soc0' / name)
    (bundle / 'sns-service-id').write_text('400\n')
    shutil.copyfile(kernel / 'fastrpc-module/fastrpc.ko', bundle / 'modules/fastrpc.ko')
    order = (bundle / 'modules.d1').read_text().splitlines()
    if order[-1] != 'qcom_q6v5_pas.ko' or 'fastrpc.ko' in order:
        raise ValueError('unexpected D1 module order')
    order.insert(len(order)-1, 'fastrpc.ko')
    (bundle / 'modules.d1').write_text(''.join(name+'\n' for name in order))
    dependencies = subprocess.check_output(['modinfo', '-F', 'depends', str(kernel / 'fastrpc-module/fastrpc.ko')], text=True).strip()
    module_names = {p.stem.replace('-', '_'):p.name for p in (bundle / 'modules').glob('*.ko')}
    dependency_files = [module_names[name.replace('-', '_')] for name in dependencies.split(',') if name]
    with (bundle / 'modules/modules.dep').open('a') as stream:
        stream.write('fastrpc.ko:' + ''.join(' '+name for name in dependency_files) + '\n')
    (bundle / 'modules/modules.options').write_text('qcom_q6v5_pas.ko a9_adsp_manual_start=1\nfastrpc.ko a9_sensor_fastrpc=1\n')
    (bundle / 'config.sha256').write_text(k['config']+'\n')
    (bundle / 'boot.bytes').write_text(str((aw / boot_name).stat().st_size)+'\n')
    (bundle / 'boot.sha256').write_text(image['boot']+'\n')
    # Rebuild every live DT property hash rather than assuming one binary offset.
    dt = load('s1-kernel-check').D0
    nodes, _ = dt.read_fdt(kernel / 's1.dtb')
    live = []
    pair = {'/vendor' + name for name in image['vendor_changed_files']}
    pair_digest = hashlib.sha256((image['sections']['kernel']+'\n').encode()).hexdigest()
    seen = set()
    v8_changed_seen = set()
    empty_overlays = []
    d1_audit = json.loads((d1 / 'bundle/service-audit.json').read_text())
    for line in (d1 / 'bundle/live.sha256').read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        if name.startswith('/sys/firmware/devicetree/base'):
            continue
        if name in pair:
            digest = pair_digest; seen.add(name)
        elif name == '/sys/kernel/notes':
            digest = sha(kernel / 'kernel.notes')
        elif name.startswith('/system/'):
            record = v8_inventory.get(name)
            if record is None or 'sha256' not in record:
                raise ValueError('live system file absent from the v8 inventory')
            if record['sha256'] != digest and digest == EMPTY_SHA and name.startswith('/system/etc/permissions/'):
                # Pre-existing, unchanged D1 live rule: the vendor build empties these permission
                # files. Accept only if the image content is the same in v7 and v8.
                if d1_audit['files']['system-image' + name]['sha256'] != record['sha256']:
                    raise ValueError('permission overlay file changed between v7 and v8')
                empty_overlays.append(name)
            elif record['sha256'] != digest:
                if name not in V8_CHANGED or name in v8_changed_seen:
                    raise ValueError('unexpected v7 to v8 system difference')
                v8_changed_seen.add(name)
                digest = record['sha256']
        live.append(digest+'  '+name+'\n')
    if seen != pair:
        raise ValueError('kernel pairing files not present in original live admission')
    if v8_changed_seen != set(V8_CHANGED):
        raise ValueError('v8 system delta is not exactly the two reviewed files')
    for path, properties in sorted(nodes.items()):
        if path.startswith(('/chosen', '/__symbols__')):
            continue
        for name, data in sorted(properties.items()):
            absolute = '/sys/firmware/devicetree/base'+path.rstrip('/')+'/'+name
            if absolute == '/sys/firmware/devicetree/base/memory@80000000/reg':
                continue  # measured ABL mutation, same exclusion as D1
            live.append(hashlib.sha256(data).hexdigest()+'  '+absolute+'\n')
    (bundle / 'live.sha256').write_text(''.join(live))
    # D1 admission is evidence of the source only; it cannot admit the new
    # kernel, DT, sidecar module or listener. Retain it under an explicit name.
    shutil.move(bundle/'service-admission.json', bundle/'receipts/d1-service-admission.json')
    shutil.move(bundle/'service-audit.json', bundle/'receipts/d1-service-audit.json')
    audit = json.loads((bundle/'receipts/d1-service-audit.json').read_text())
    for name in pair:
        key = name.removeprefix('/')
        if key not in audit['files'] or 'sha256' not in audit['files'][key]:
            raise ValueError('kernel pairing file absent from source service inventory')
        audit['files'][key]['sha256'] = pair_digest
    for name in V8_CHANGED:
        key = 'system-image' + name
        if key not in audit['files'] or v8_inventory[name]['sha256'] == audit['files'][key]['sha256']:
            raise ValueError('system audit record missing or already v8')
        audit['files'][key] = {'sha256': v8_inventory[name]['sha256'], 'bytes': v8_inventory[name]['bytes']}
    jar = [f for f in audit['findings'] if f['file'] == 'system-image' + V8_CHANGED[0]]
    if len(jar) != 1 or any(f['file'] == 'system-image' + V8_CHANGED[1] for f in audit['findings']):
        raise ValueError('unexpected audit findings for the v8 files')
    jar[0]['sha256'] = v8_inventory[V8_CHANGED[0]]['sha256']
    audit['status'] = 'DERIVED-S1-INDEPENDENT-REVIEW-REQUIRED'
    audit['s1_derivation'] = {'d1_audit_sha256':sha(bundle/'receipts/d1-service-audit.json'),
                             'actual_vendor_delta':sorted(pair), 'new_services_installed':False,
                             'new_sidecar':'gated fastrpc plus explicit confined listener',
                             'image_receipt_sha256':sha(images/'image-receipt.json'),
                             'system_b':{'image_sha256':V8_IMAGE_SHA,'changed_files':list(V8_CHANGED),
                                         'binding_receipt_sha256':sha(system_binding/'system-image.json'),
                                         'inventory_sha256':v8_receipt['files_sha256']}}
    (bundle/'service-audit.json').write_text(json.dumps(audit,indent=2)+'\n')
    (bundle/'service-admission.json').write_text(json.dumps({'status':'PENDING-INDEPENDENT-S1-REVIEW',
        'audit_sha256':sha(bundle/'service-audit.json'), 'd1_admission_is_source_evidence_only':True},indent=2)+'\n')
    for source, name in ((kernel / 'kernel-receipt.json', 's1-kernel.json'),
                         (images / 'image-receipt.json', 's1-images.json'),
                         (listener.with_name(listener.name+'.build.json'), 's1-listener-build.json'),
                         (observer.with_name(observer.name+'.build.json'), 's1-observer-build.json')) + (
                         ((probe.with_name(probe.name+'.build.json'), 's2-probe-build.json'),) if probe else ()):
        shutil.copyfile(source, bundle / 'receipts' / name)
    provenance = {'stage': 'S2A' if probe else 'S1', 'status': 'BUILT_OFFLINE_REVIEW_REQUIRED',
                  'execution_admission': 'NO_GO_INDEPENDENT_REVIEW_REQUIRED',
                  'registry_transport': 'IMPLEMENTED_OFFLINE_TESTED_RAM_ONLY',
                  'hardware_result': 'NOT_RUN', 'system_b_sha256': V8_IMAGE_SHA,
                  'system_b_changed_files': {name: v8_inventory[name]['sha256'] for name in V8_CHANGED}, 'live_empty_permission_overlays_unchanged': empty_overlays, 'images': {boot_name:image['boot'],vendor_name:image['vendor']},
                  'kernel': k['Image.gz'], 'dtb': k['dtb'], 'fastrpc': k['fastrpc.ko'],
                  'recipes': recipe_hashes(), 'dt_recipe_binding': dt_binding,
                  'registry_archive_validation': seed_validation,
                  'd1_source_tuple':prep.D1_TUPLE, 'private_seed_tuple':sha(seed_manifest),
                  'review_required': ['FastRPC gate/CB DMA and upstream ABI', 'listener wire subset and RAM confinement',
                                      'guardian launch/stop/join and fallback ownership', 'new live/vendor/service audit and exact tuple']}
    (bundle / 's1-build.json').write_text(json.dumps(provenance,indent=2)+'\n')
    tuple_ = manifest(bundle)
    pending = {**provenance, 'tuple':tuple_, 'frozen_manifest_sha256':tuple_,
               'independent_review':'PENDING', 'service_admission':'PENDING',
               'fallback_owner':'PENDING', 'root_transport':'loopback',
               'fallback':'attended PON2/Power then golden restore', 'rehearsal':False,
               'd0_pass_evidence':'PENDING reviewer must bind retained D0 evidence',
               'd1_pass_evidence':'PENDING reviewer must bind retained D1 evidence'}
    (out / 'admission.pending.json').write_text(json.dumps(pending,indent=2)+'\n')
    if shutil.disk_usage('/').free < FLOOR:
        raise RuntimeError('15 GB disk floor; stop and ask coordinator')
    print('S1 PRIVATE ACTIVE BUILD PREPARED; independent review required:',out)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('--kernel',type=Path,required=True)
    parser.add_argument('--images',type=Path,required=True)
    parser.add_argument('--listener',type=Path,required=True)
    parser.add_argument('--observer',type=Path,required=True)
    parser.add_argument('--seed-bundle',type=Path,default=WORK/'sensors-s1/prep-20261017-sealed/bundle')
    parser.add_argument('--d1',type=Path,default=WORK/'adsp/d1-20261017')
    parser.add_argument('--system-binding',type=Path,default=V8_BINDING)
    parser.add_argument('--probe',type=Path,help='S2a: include s2-sns-probe and use the s2a-derive target')
    a=parser.parse_args()
    prepare(a.output,a.kernel,a.images,a.listener,a.observer,a.seed_bundle,a.d1,a.system_binding,a.probe)
