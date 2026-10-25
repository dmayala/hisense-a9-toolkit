#!/usr/bin/env python3
"""Offline artifact validation using independent unpack_bootimg + ABL emulator.
No phone access. All temporary files live beside the private artifact manifest.
"""
import gzip
import hashlib
import json
import pathlib
import struct
import subprocess
import sys
import tempfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dtprops(blob):
    assert blob[:4] == bytes.fromhex('d00dfeed')
    pos, strings = struct.unpack_from('>II', blob, 8)
    stack, props = [], {}
    align = lambda n: (n + 3) // 4 * 4
    while True:
        token = struct.unpack_from('>I', blob, pos)[0]
        pos += 4
        if token == 1:
            end = blob.index(0, pos)
            stack.append(blob[pos:end].decode())
            pos = align(end + 1)
        elif token == 2:
            stack.pop()
        elif token == 3:
            length, name = struct.unpack_from('>II', blob, pos)
            pos += 8
            end = blob.index(0, strings + name)
            key = '/'.join(stack) + '/' + blob[strings + name:end].decode()
            props[key] = blob[pos:pos + length]
            pos = align(pos + length)
        elif token == 9:
            break
        else:
            assert token == 4
    return props


def config(kernel):
    raw = gzip.decompress(kernel)
    assert raw[56:60] == b'ARMd'
    assert struct.unpack_from('<Q', raw, 16)[0] <= 36024320, 'stock-derived conservative runtime size ceiling'
    return gzip.decompress(raw[raw.index(b'IKCFG_ST') + 8:raw.index(b'IKCFG_ED')])


def main():
    out = pathlib.Path(sys.argv[1]).resolve()
    repo = pathlib.Path(__file__).resolve().parents[3]
    manifest = json.loads((out / 'manifest.json').read_text())
    baseline = (out / 'baseline.img').read_bytes()
    assert sha(baseline) == manifest['base_sha256']
    base_dtb = dtprops((out / 'components/board.dtb').read_bytes())
    base_cfg = config((out / 'components/Image.gz').read_bytes())
    merged_base = None
    for stage, item in manifest['stages'].items():
        if 'sha256' not in item:
            continue
        image = out / ('a9-smp-' + stage + '.img')
        data = image.read_bytes()
        assert sha(data) == item['sha256'] and len(data) <= 100663296
        if stage != 'C1':
            assert data[:64] == baseline[:64] and data[576:608] == baseline[576:608] and data[1632:] == baseline[1632:]
        with tempfile.TemporaryDirectory(prefix='validate-', dir=out) as td:
            subprocess.run(['unpack_bootimg', '--boot_img', str(image), '--out', td],
                           check=True, stdout=subprocess.DEVNULL)
            unpack = pathlib.Path(td)
            for name, key in [('ramdisk', 'ramdisk'), ('recovery_dtbo', 'dtbo')]:
                assert sha((unpack / name).read_bytes()) == manifest['component_sha256'][key]
            kernel = (unpack / 'kernel').read_bytes()
            cfg = config(kernel)
            props = dtprops((unpack / 'dtb').read_bytes())
            if stage != 'C1':
                assert sha(kernel) == manifest['component_sha256']['kernel']
                assert props == base_dtb and cfg == base_cfg
            else:
                removed = {'/psci/power-domain-cpu-cluster0/domain-idle-states',
                           '/psci/power-domain-cpu-cluster1/domain-idle-states'}
                assert set(base_dtb) - set(props) == removed
                assert props == {k: v for k, v in base_dtb.items() if k not in removed}
                assert sha(kernel) == item['kernel_sha256']
                assert sha((unpack / 'dtb').read_bytes()) == item['dtb_sha256']
                assert sha(cfg) == item['config_sha256']
                for symbol in ['CONFIG_ARM_PSCI_CPUIDLE=y', 'CONFIG_ARM_PSCI_CPUIDLE_DOMAIN=y']:
                    assert symbol.encode() in cfg
        merged = out.parent / ('merged-' + stage + '.dtb')
        with (out.parent / ('check-' + stage + '.log')).open('w') as log:
            subprocess.run(['bash', str(repo / 'scripts/abl-check.sh'), str(image), 'boot',
                            '--dtbo', str(out / 'components/recovery_dtbo.img'), '--quiet',
                            '--save-dtb', str(merged)],
                           check=True, stdout=log, stderr=subprocess.STDOUT)
        merged_props = dtprops(merged.read_bytes())
        if stage == 'A':
            merged_base = merged_props
        elif stage == 'C1':
            assert merged_props == {k: v for k, v in merged_base.items() if k not in removed}, 'overlay changed C1 idle isolation'
        else:
            assert merged_props == merged_base, 'overlay changed A/B isolation'
        print('PASS: ' + stage + ' hash, components, config/DT isolation, load window and offline ABL')


if __name__ == '__main__':
    main()
