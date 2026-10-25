#!/usr/bin/env python3
"""Exercise D2 system binding with a tiny real ext4 image and refusal mutations.

Only the producer's home lookup is redirected into a private fixture. Production
debugfs walks/reads and bundle inventory checks execute unchanged. No transports,
mounts, network, or golden-data paths are used.
"""
import argparse
import contextlib
import copy
import hashlib
from importlib.machinery import SourceFileLoader
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess as sp
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
producer = SourceFileLoader('d2_system_producer_test', str(ROOT/'scripts/mainline/adsp/prepare-system-d2.py')).load_module()
bundle = SourceFileLoader('d2_system_bundle_test', str(ROOT/'scripts/mainline/adsp/make-bundle-d2.py')).load_module()
checks = []
minimum = shutil.disk_usage('/').free


def floor():
    global minimum
    minimum = min(minimum, shutil.disk_usage('/').free)
    assert minimum >= 16_000_000_000, 'retain 16 GB before fixture writes'


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def passed(name):
    checks.append(name)
    print('PASS system-binding', name, flush=True)


def refuse(name, fn):
    floor()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            fn()
    except (AssertionError, ValueError, KeyError, FileNotFoundError):
        passed(name)
        return
    raise AssertionError('unsafe system binding accepted: '+name)


ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--receipt', type=Path)
a = ap.parse_args()
floor()
private = Path.home()/'A9-build/mainline/adsp'
assert private.is_dir()
with tempfile.TemporaryDirectory(prefix='d2-system-test-', dir=private) as temporary:
    fixture = Path(temporary)
    fakehome = fixture/'home'
    work = fakehome/'A9-build/mainline/adsp'
    work.mkdir(parents=True)
    src = work/'input'
    (src/'system/framework').mkdir(parents=True)
    (src/'system/framework/same.bin').write_bytes(b'unchanged private reference bytes\n')
    (src/'system/framework/changed.bin').write_bytes(b'fresh image version\n')
    (src/'system/current').symlink_to('framework/same.bin')
    image = work/'fixture.ext4'
    floor()
    sp.run(['mke2fs', '-q', '-t', 'ext4', '-b', '1024', '-I', '256',
            '-O', '^has_journal', '-d', str(src), str(image), '8192'],
           check=True, stdout=sp.PIPE, stderr=sp.PIPE, timeout=30)
    original_image = sha(image)
    rows = {'/lost+found': ('d', 0o700, '0:0', 0, '-', '-')}
    for p in sorted(src.rglob('*')):
        st = p.lstat()
        kind = 'l' if p.is_symlink() else 'd' if p.is_dir() else 'f'
        value = str(p.readlink()) if kind == 'l' else sha(p) if kind == 'f' else '-'
        rows['/'+str(p.relative_to(src))] = (kind, stat.S_IMODE(st.st_mode),
            f'{st.st_uid}:{st.st_gid}', st.st_size if kind == 'f' else 0, value, '-')

    def write_manifest(path, content):
        floor()
        path.write_text(''.join(f'{p}\t{k}\t{mode:o}\t{owner}\t{size}\t{value}\t{label}\n'
            for p, (k, mode, owner, size, value, label) in sorted(content.items())))

    manifest = work/'manifest.tsv'
    write_manifest(manifest, rows)
    reference = work/'reference'
    shutil.copytree(src, reference, symlinks=True)
    changed = reference/'system/framework/changed.bin'
    changed.write_bytes(b'older reference version\n')
    old = {'system-image/'+str(p.relative_to(reference)):
           {'sha256': sha(p), 'bytes': p.stat().st_size}
           for p in reference.rglob('*') if p.is_file() and not p.is_symlink()}
    audit = work/'reference-audit.json'
    audit.write_text(json.dumps({'files': old}))
    reference_before = {str(p.relative_to(reference)): (sha(p), p.stat().st_ino, p.stat().st_mtime_ns)
        for p in reference.rglob('*') if p.is_file() and not p.is_symlink()}
    serial = 0

    def prepare(**kw):
        global serial
        floor()
        serial += 1
        args = dict(image=image, sha256=original_image, manifest=manifest,
                    reference=reference, reference_audit=audit, output=work/f'output-{serial}')
        args.update(kw)
        # All producer writes remain inside this real A9-build private fixture.
        # The real README exports D2_DISK_LOG under the caller's A9-build.
        # A fixture home must not reinterpret that external log as its own.
        with patch.dict(os.environ, {'D2_DISK_LOG': ''}), \
             patch.object(Path, 'home', return_value=fakehome), \
             contextlib.redirect_stdout(io.StringIO()):
            producer.prepare(SimpleNamespace(**args))
        return args['output']

    out = prepare()
    result = out/'system-image'
    same = 'system/framework/same.bin'
    assert (result/same).stat().st_ino == (reference/same).stat().st_ino
    passed('unchanged file reused by hardlink')
    changed_rel = 'system/framework/changed.bin'
    assert (result/changed_rel).stat().st_ino != (reference/changed_rel).stat().st_ino
    assert (result/changed_rel).read_bytes() == (src/changed_rel).read_bytes()
    passed('changed file copied privately from actual image')
    assert {'/'+str(p.relative_to(result)) for p in result.rglob('*')} == rows.keys()
    for name, row in rows.items():
        p = result/name.lstrip('/')
        if row[0] == 'f':
            assert sha(p) == row[4] and p.stat().st_size == row[3]
        elif row[0] == 'l':
            assert str(p.readlink()) == row[4]
        else:
            assert p.is_dir()
    passed('fresh ext4 exact content symlinks and complete path set')
    refuse('wrong image digest', lambda: prepare(sha256='0'*64))
    for name, mutate in [
        ('missing manifest path', lambda d: d.pop('/system/framework/same.bin')),
        ('extra manifest path', lambda d: d.update({'/absent': ('f', 0o644, '0:0', 0, '0'*64, '-')})),
        ('changed manifest content', lambda d: d.update({'/system/framework/changed.bin': ('f', *rows['/system/framework/changed.bin'][1:4], '0'*64, '-')})),
        ('changed inode mode', lambda d: d.update({'/system/framework/same.bin': ('f', 0o777, *rows['/system/framework/same.bin'][2:])})),
        ('changed SELinux label', lambda d: d.update({'/system': (*rows['/system'][:5], 'u:object_r:wrong:s0')})),
        ('changed symlink target', lambda d: d.update({'/system/current': (*rows['/system/current'][:4], 'wrong-target', '-')})),
    ]:
        mutated = copy.deepcopy(rows)
        mutate(mutated)
        path = work/(name.replace(' ', '-')+'.tsv')
        write_manifest(path, mutated)
        refuse(name, lambda path=path: prepare(manifest=path))
    escaped = fakehome/'outside-A9-build'
    escaped.mkdir()
    refuse('reference root outside private A9-build', lambda: prepare(reference=escaped))
    escaped_reference = work/'escaped-reference'
    shutil.copytree(reference, escaped_reference, symlinks=True)
    escaped_same = escaped_reference/same
    escaped_same.unlink()
    escaped_same.symlink_to(reference/same)
    refuse('reference file escapes reference tree', lambda: prepare(reference=escaped_reference))
    assert sha(image) == original_image
    passed('producer preserves raw ext4 image bytes')

    inventory = json.loads((out/'system-files.json').read_text())
    good_audit = {'files': {'system-image'+name: rec for name, rec in inventory.items()}}
    assert bundle.system_binding(out, good_audit)['image_sha256'] == original_image
    passed('bundle accepts matching complete audit and image binding')
    for name, mutate in [
        ('audit missing file', lambda d: d['files'].pop('system-image/system/framework/same.bin')),
        ('audit extra file', lambda d: d['files'].update({'system-image/extra': {'sha256': '0'*64, 'bytes': 0}})),
        ('audit changed image bytes', lambda d: d['files']['system-image/system/framework/same.bin'].update(sha256='0'*64)),
    ]:
        changed_audit = copy.deepcopy(good_audit)
        mutate(changed_audit)
        refuse(name, lambda changed_audit=changed_audit: bundle.system_binding(out, changed_audit))
    for filename, name in [('system-image.manifest.tsv', 'bundle changed manifest'),
                           ('system-files.json', 'bundle changed inventory')]:
        p = out/filename
        saved = p.read_bytes()
        p.write_bytes(saved+b'\n')
        refuse(name, lambda: bundle.system_binding(out, good_audit))
        p.write_bytes(saved)
    receipt_path = out/'system-image.json'
    saved_receipt = receipt_path.read_bytes()
    record = json.loads(saved_receipt)
    record['image_sha256'] = '0'*64
    receipt_path.write_text(json.dumps(record))
    refuse('bundle wrong image digest receipt', lambda: bundle.system_binding(out, good_audit))
    receipt_path.write_bytes(saved_receipt)
    original_size = image.stat().st_size
    with image.open('ab') as f:
        f.write(b'changed')
    refuse('bundle changed raw image', lambda: bundle.system_binding(out, good_audit))
    with image.open('r+b') as f:
        f.truncate(original_size)
    assert sha(image) == original_image
    assert reference_before == {str(p.relative_to(reference)): (sha(p), p.stat().st_ino, p.stat().st_mtime_ns)
        for p in reference.rglob('*') if p.is_file() and not p.is_symlink()}
    passed('all refusal mutations preserve reference inodes and bytes')
    floor()
    record = {'status': 'PASS', 'checks': checks, 'check_count': len(checks),
              'test_sha256': sha(Path(__file__)),
              'producer_sha256': sha(Path(producer.__file__)),
              'bundle_sha256': sha(Path(bundle.__file__)),
              'fixture_image_sha256': original_image, 'free_minimum': minimum,
              'caller_disk_log': os.environ.get('D2_DISK_LOG'),
              'tools': {tool: shutil.which(tool) for tool in ('mke2fs', 'debugfs')},
              'scope': 'real tiny ext4; read-only debugfs; producer home mocked only; no phone/network/golden-data access'}
    if a.receipt:
        assert a.receipt.resolve().is_relative_to(private)
        assert not a.receipt.exists()
        a.receipt.write_text(json.dumps(record, indent=2)+'\n')
print(f'ADSP-D2-SYSTEM TEST PASS: {len(checks)} checks')
