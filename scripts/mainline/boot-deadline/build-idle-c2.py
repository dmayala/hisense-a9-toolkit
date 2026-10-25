#!/usr/bin/env python3
"""Host-only DT repack: native C2 or silver-GDHS-only C3 on proven idle2."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fdt_properties(raw):
    """Read property bytes, ignoring packing/order, for the actual DT delta."""
    magic, size, offset, strings = struct.unpack_from('>4I', raw)
    assert magic == 0xd00dfeed and size <= len(raw)
    stack, props = [], {}
    while True:
        token = struct.unpack_from('>I', raw, offset)[0]
        offset += 4
        if token == 1:
            end = raw.index(b'\0', offset)
            stack.append(raw[offset:end].decode())
            offset = (end + 4) & ~3
        elif token == 2:
            stack.pop()
        elif token == 3:
            length, nameoff = struct.unpack_from('>2I', raw, offset)
            offset += 8
            name = raw[strings + nameoff:raw.index(b'\0', strings + nameoff)].decode()
            props['/'.join(stack) + '/' + name] = raw[offset:offset + length]
            offset = (offset + length + 3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            assert not stack
            return props
        else:
            raise ValueError(f'bad FDT token {token}')


def unpack(raw):
    assert raw[:8] == b'ANDROID!'
    assert struct.unpack_from('<I', raw, 40)[0] == 2
    page = struct.unpack_from('<I', raw, 36)[0]
    pos, blobs = page, []
    for field in (8, 16, 24, 1632, 1648):
        size = struct.unpack_from('<I', raw, field)[0]
        blobs.append(raw[pos:pos + size])
        pos += (size + page - 1) // page * page
    return page, blobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=Path('/home/user/A9-build/mainline/idle2-20261022'))
    parser.add_argument('--profile', choices=['c2', 'c3'], default='c2')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--dtc-bin', type=Path, required=True)
    args = parser.parse_args()
    base = args.base
    out = args.out or Path(f'/home/user/A9-build/mainline/idle-{args.profile}-20261023')
    profile = 'c3-silver-gdhs-only' if args.profile == 'c3' else 'c2-native-sm6115-cluster-idle'
    selections = {0: [0]} if args.profile == 'c3' else {0: [0, 1], 1: [0, 1]}
    raw = (base / 'boot.img').read_bytes()
    assert sha(raw) == '279a4236edecf11b11feebcd88e8c7be5e0f2900728bc042aabcfc3664ccc2ea'
    page, blobs = unpack(raw)
    assert blobs[0] == (base / 'Image.gz').read_bytes()
    assert blobs[4] == (base / 'idle2.dtb').read_bytes()
    assert sha(blobs[0]) == '790823f576badda8e99094f5d3af82efb0d020428e39f4fe535de881fc90198e'
    out.mkdir()  # Never overwrite a published/running package.
    dtb = out / f'idle-{args.profile}.dtb'
    dtb.write_bytes(blobs[4])
    before = fdt_properties(blobs[4])
    native = fdt_properties((base / 'parent.dtb').read_bytes())
    expected = {'/chosen/a9,idle-profile': profile.encode() + b'\0'}
    for cluster, indices in selections.items():
        node = f'/psci/power-domain-cpu-cluster{cluster}'
        key = node + '/domain-idle-states'
        assert key not in before and len(native[key]) == 8
        native_values = struct.unpack('>2I', native[key])
        values = [native_values[i] for i in indices]
        subprocess.run([str(args.dtc_bin / 'fdtput'), '-t', 'x', str(dtb), node,
                        'domain-idle-states', *(f'{v:x}' for v in values)], check=True)
        expected[key] = struct.pack('>' + 'I' * len(values), *values)
    subprocess.run([str(args.dtc_bin / 'fdtput'), '-t', 's', str(dtb), '/chosen',
                    'a9,idle-profile', profile], check=True)
    after = fdt_properties(dtb.read_bytes())
    delta = {k: after.get(k) for k in before.keys() | after.keys() if before.get(k) != after.get(k)}
    assert delta == expected, delta
    # The native platform states already exist in C1; only selection changes.
    phandles = {struct.unpack('>I', v)[0]: k.rsplit('/', 1)[0]
                for k, v in after.items() if k.endswith('/phandle')}
    states = []
    for cluster in selections:
        selected = expected[f'/psci/power-domain-cpu-cluster{cluster}/domain-idle-states']
        for phandle in struct.unpack('>' + 'I' * (len(selected) // 4), selected):
            node = phandles[phandle]
            param = struct.unpack('>I', after[node + '/arm,psci-suspend-param'])[0]
            states.append(dict(cluster=cluster, node=node, psci_param=hex(param)))
    if args.profile == 'c3':
        assert [s['psci_param'] for s in states] == ['0x40000022']
        assert '/psci/power-domain-cpu-cluster1/domain-idle-states' not in after
    else:
        assert [s['psci_param'] for s in states] == ['0x40000022', '0x41000044', '0x40000042', '0x41000044']
    blobs[4] = dtb.read_bytes()
    header, body, digest = bytearray(raw[:page]), bytearray(), hashlib.sha1()
    for field, blob in zip((8, 16, 24, 1632, 1648), blobs):
        struct.pack_into('<I', header, field, len(blob))
        digest.update(blob)
        digest.update(struct.pack('<I', len(blob)))
        body.extend(blob)
        body.extend(bytes(-len(body) % page))
    header[576:608] = digest.digest() + bytes(12)
    packed = bytes(header + body)
    _, check = unpack(packed)
    _, original = unpack(raw)
    assert check[:4] == original[:4] and check[4] == dtb.read_bytes()
    assert packed[64:576] == raw[64:576] and packed[608:1632] == raw[608:1632]
    allowed = set(range(576, 608)) | set(range(1648, 1652))
    assert all(a == b or i in allowed for i, (a, b) in enumerate(zip(raw[:page], packed[:page])))
    (out / 'boot.img').write_bytes(packed)
    for name in ['Image.gz', 'kernel.config', 'Module.symvers', 'vmlinux.symvers',
                 'recovery_dtbo.img', 'clock-sample', 'hook.sh', 'idle2-snapshot.sh',
                 'idle2-summary.py', 'module-audit-idle2.json']:
        shutil.copy2(base / name, out / name)
    (out / 'idle2.dtb').write_bytes(original[4])
    shutil.copy2(base / 'parent.dtb', out / 'native-parent.dtb')
    shutil.copy2(__file__, out / 'build-idle-c2.py')
    command = ((f'A9_BTRY_ONB= A9_BTRY_HOLD=900 ' if args.profile == 'c3' else
                f'A9_IDLE2_ASSETS={out} A9_BTRY_HOLD=900 A9_PLAIN_IDLE_MINUTES=10 A9_BTRY_ONB={out}/hook.sh ')
               + f'/home/user/A9-build/mainline/b-try.sh {out}/boot.img '
               '/home/user/A9-build/mainline/plain-integ-20261022/vendor.img\n')
    (out / 'b-try-command.txt').write_text(command)
    proof = dict(profile=profile, boot_sha256=sha(packed), base_boot_sha256=sha(raw),
                 DT_changes={k: v.hex() for k, v in delta.items()}, selected_states=states,
                 Image_sha256=sha(blobs[0]), all_other_boot_payloads_identical=True,
                 cmdline_byte_identical=True, full_module_ABI_preserved_by_kernel_identity=True,
                 Module_symvers_sha256=sha((out / 'Module.symvers').read_bytes()),
                 vmlinux_symvers_sha256=sha((out / 'vmlinux.symvers').read_bytes()),
                 kernel_rebuilt=False, hook_changed=False, phone_access=False, hardware_tested=False)
    (out / 'packed-proof.json').write_text(json.dumps(proof, indent=2) + '\n')
    for name in ['boot.img', 'Image.gz']:
        (out / (name + '.sha256')).write_text(sha((out / name).read_bytes()) + '  ' + name + '\n')
    print(json.dumps(proof, indent=2))


if __name__ == '__main__':
    main()
