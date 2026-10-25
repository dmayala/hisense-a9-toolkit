#!/usr/bin/env python3
"""Host-only fence-fixed plain kernel, CPU-only PSCI idle, frozen module ABI."""
from pathlib import Path
import difflib, hashlib, importlib.util, json, shutil, struct, subprocess
import build as b


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, b.HERE / file)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def main(resume=False):
    parent = b.ROOT / 'plain-integ-fencefix-deadline-logfix1-wdt-20261022'
    previous = b.ROOT / 'plain-wrapped-fix4-wdt-20261022'
    out = b.ROOT / 'idle2-20261022'
    assert shutil.disk_usage(b.ROOT).free > 17 * 1024**3
    if resume:
        assert (out / 'kernel-build.rc').read_text().strip() == '0'
        assert b.sha(b.OBJ / 'arch/arm64/boot/Image.gz') == b.sha(out / 'Image.gz')
        return finish(parent, out)
    assert b.sha(b.OBJ / 'arch/arm64/boot/Image.gz') == b.sha(previous / 'Image.gz'), 'cache changed'
    out.mkdir(); b.OUT = out
    wdt = load('wdt', 'build-wdt.py')
    b.run(['patch', '--batch', '--fuzz=0', '-R', '-p1', '-d', b.K, '-i', previous / 'bio-abi.patch'], 'undo-bio-fix4')
    fix3 = b.ROOT / 'plain-wrapped-fix3-20261022'
    for rel in json.loads((fix3 / 'source.json').read_text())['changed']:
        b.materialize(b.K / rel, (fix3 / 'parent-sources' / rel).read_bytes())
    for patch in [wdt.PATCHES / 'metadata/dm-default-key-7.2.patch', wdt.PATCHES / 'ice-wrapped/0001-a9-ice31-legacy-wrapped.patch']:
        b.run(['patch', '--batch', '--fuzz=0', '-R', '-p1', '-d', b.K, '-i', patch], patch.stem + '-undo')
    manifest = json.loads((parent / 'source.json').read_text())['source_files']
    for rel, digest in manifest.items():
        p = parent / 'source' / rel
        assert b.sha(p) == digest
        b.materialize(b.K / rel, p.read_bytes())
    # dt_init_idle_driver is new in this configuration and all its consumers
    # here are built in. Keep this internal helper out of the frozen module
    # API; never copy/patch generated CRC values or remove an existing export.
    p = b.K / 'drivers/cpuidle/dt_idle_states.c'
    text = p.read_text(); anchor = 'EXPORT_SYMBOL_GPL(dt_init_idle_driver);'
    assert text.count(anchor) == 1
    new = text.replace(anchor, '/* A9 idle2: built-in PSCI helper; no new module API. */')
    (out / 'builtin-idle-helper.patch').write_text(''.join(difflib.unified_diff(text.splitlines(True), new.splitlines(True), fromfile='a/drivers/cpuidle/dt_idle_states.c', tofile='b/drivers/cpuidle/dt_idle_states.c')))
    b.materialize(p, new.encode())
    shutil.copyfile(parent / 'kernel.config', b.OBJ / '.config')
    b.run([b.K / 'scripts/config', '--file', b.OBJ / '.config', '-e', 'ARM_PSCI_CPUIDLE', '-e', 'ARM_PSCI_CPUIDLE_DOMAIN', '-e', 'QCOM_STATS'], 'idle-config')
    b.run(wdt.make_command() + ['olddefconfig'], 'olddefconfig')
    original = (parent / 'kernel.config').read_text(); candidate = (b.OBJ / '.config').read_text()
    (out / 'config.diff').write_text(''.join(difflib.unified_diff(original.splitlines(True), candidate.splitlines(True), fromfile='fencefix', tofile='idle2')))
    b.run(wdt.make_command() + ['Image.gz', 'modules'], 'kernel-build')
    for src, dst in [('arch/arm64/boot/Image.gz', 'Image.gz'), ('.config', 'kernel.config'), ('vmlinux.symvers', 'vmlinux.symvers'), ('Module.symvers', 'Module.symvers')]:
        shutil.copyfile(b.OBJ / src, out / dst)
    finish(parent, out)


def finish(parent, out):
    b.OUT = out
    manifest = json.loads((parent / 'source.json').read_text())['source_files']
    # A combined Image.gz+modules build generates Module.symvers; any old
    # vmlinux.symvers can remain stale. Derive builtin rows from fresh modpost.
    generated = (b.OBJ / 'Module.symvers').read_bytes()
    (out / 'Module.generated.symvers').write_bytes(generated)
    builtin = b''.join(line for line in generated.splitlines(keepends=True) if line.split()[2] == b'vmlinux')
    assert builtin == (parent / 'vmlinux.symvers').read_bytes(), 'actual kernel export table changed'
    expected = (parent / 'Module.symvers').read_bytes()
    assert sorted(generated.splitlines()) == sorted(expected.splitlines()), 'actual full module CRC table changed'
    rows = {line.split()[1]:line for line in generated.splitlines(keepends=True)}
    ordered = b''.join(rows[line.split()[1]] for line in expected.splitlines())
    assert ordered == expected
    (out / 'vmlinux.symvers').write_bytes(builtin)
    (out / 'Module.symvers').write_bytes(ordered)
    (out / 'abi.json').write_text(json.dumps(dict(vmlinux_symvers_byte_identical=True, Module_symvers_byte_identical=True,
        generated_modpost_rows_identical=True, row_order='parent order; raw generated table retained', CRC_values_edited=False)) + '\n')
    raw = (parent / 'boot.img').read_bytes(); page = struct.unpack_from('<I', raw, 36)[0]; pos = page; blobs = []
    fields = (8, 16, 24, 1632, 1648)
    for field in fields:
        size = struct.unpack_from('<I', raw, field)[0]; blobs.append(raw[pos:pos+size]); pos += (size+page-1)//page*page
    (out / 'parent.dtb').write_bytes(blobs[4]); (out / 'idle2.dtb').write_bytes(blobs[4])
    for node in ['power-domain-cpu-cluster0', 'power-domain-cpu-cluster1']:
        b.run(['fdtput', '-d', out / 'idle2.dtb', '/psci/' + node, 'domain-idle-states'], 'dt-' + node)
    b.run(['fdtput', '-t', 's', out / 'idle2.dtb', '/chosen', 'a9,idle-profile', 'c1-wfi-cpu-pc-only'], 'dt-profile')
    blobs[0] = (out / 'Image.gz').read_bytes(); blobs[4] = (out / 'idle2.dtb').read_bytes()
    header = bytearray(raw[:page])
    for start, end in [(64,576),(608,1632)]:
        tokens = raw[start:end].split(b'\0')[0].decode().split(); tokens = [t for t in tokens if not t.startswith('cpuidle.off=')]
        header[start:end] = ' '.join(tokens).encode().ljust(end-start, b'\0')
    body = bytearray(); digest = hashlib.sha1()
    for field, data in zip(fields, blobs):
        if field == 1632: struct.pack_into('<Q', header, 1636, page+len(body))
        struct.pack_into('<I', header, field, len(data)); digest.update(data); digest.update(struct.pack('<I', len(data)))
        body += data; body += bytes(-len(body)%page)
    header[576:608] = digest.digest()+bytes(12)
    (out / 'boot.img').write_bytes(header+body); (out / 'recovery_dtbo.img').write_bytes(blobs[3])
    for rel in [*manifest, 'drivers/cpuidle/dt_idle_states.c', 'drivers/cpuidle/cpuidle-psci.c', 'drivers/cpuidle/cpuidle-psci-domain.c']:
        p = out / 'source' / rel; p.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(b.K / rel, p)
    shutil.copyfile(b.HERE / 'build-idle2.py', out / 'source/build-idle2.py')
    b.run(['/nix/store/ik128k4xm49sbm27i1nzxlr06aflixma-aarch64-unknown-linux-musl-gcc-wrapper-14.3.0/bin/aarch64-unknown-linux-musl-gcc', '-static', '-O2', '-Wall', '-Wextra', b.HERE / 'idle2-clock.c', '-o', out / 'clock-sample'], 'clock-build')
    b.run(['python3', '-P', b.REPO / 'scripts/abl-check.py', out / 'boot.img', 'boot', '--dtbo', out / 'recovery_dtbo.img', '--json'], 'abl-check')
    for name in ['boot.img','Image.gz']: (out / (name+'.sha256')).write_text(b.sha(out/name)+'  '+name+'\n')
    print('IDLE2 kernel built: '+b.sha(out/'boot.img'), flush=True)


if __name__ == '__main__':
    import sys
    assert sys.argv[1:] in ([], ['resume'])
    main(bool(sys.argv[1:]))
