#!/usr/bin/env python3
"""Offline boot-v2 variants. No device transports, shell scripts or hardware I/O.
A/B preserve every byte except the header command line. C needs a separately
built PSCI-idle kernel/config/DTB. Artifacts stay outside the repository.
"""
import argparse
import hashlib
import json
import pathlib
import struct
import subprocess


def sha(data):
    return hashlib.sha256(data).hexdigest()


def command(base, stage):
    words = base.split()
    assert not any('serialno=' in x or 'serialnumber=' in x for x in words)
    assert 'nosmp' not in words and not any(x.startswith('nr_cpus=') for x in words), 'unexpected SMP limit'
    for key in ['maxcpus', 'cpuidle.off', 'cpufreq.off']:
        assert len([w for w in words if w.startswith(key + '=')]) == 1, key
    assert 'maxcpus=1' in words and 'cpuidle.off=1' in words and 'cpufreq.off=1' in words
    for required in ['qcom_pon.beacon=2', 'qcom_scm.skip_qtee=1',
                     'clk_ignore_unused', 'pd_ignore_unused', 'regulator_ignore_unused']:
        assert required in words, required
    # Full replacement, never depend on repeated-key/last-value semantics.
    result = ['maxcpus=8' if w == 'maxcpus=1' else w for w in words]
    if stage != 'A':
        result.remove('cpufreq.off=1')
        result = [w for w in result if not w.startswith('cpufreq.default_governor=')]
        result.append('cpufreq.default_governor=' + ('userspace' if stage == 'B0' else 'schedutil'))
    if stage == 'C1':
        result.remove('cpuidle.off=1')
    return ' '.join(result)


def header_command(data, text):
    encoded = text.encode()
    assert len(encoded) <= 1535
    b = bytearray(data)
    b[64:576] = encoded[:512].ljust(512, b'\0')
    b[608:1632] = encoded[512:].ljust(1024, b'\0')
    return bytes(b)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base', type=pathlib.Path, required=True)
    p.add_argument('--out', type=pathlib.Path, required=True)
    p.add_argument('--c-kernel', type=pathlib.Path)
    p.add_argument('--c-config', type=pathlib.Path)
    p.add_argument('--c-dtb', type=pathlib.Path)
    a = p.parse_args()
    out = a.out.resolve()
    repo = pathlib.Path(__file__).resolve().parents[3]
    assert not out.is_relative_to(repo), 'binary output must be outside the repository'
    assert 'a9-assets' not in out.parts and 'Backups' not in out.parts
    out.mkdir(parents=True, exist_ok=True)
    b = a.base.read_bytes()
    frozen = out / 'baseline.img'
    if frozen.exists():
        assert frozen.read_bytes() == b, 'baseline changed; use the frozen baseline or a new output directory'
    else:
        frozen.write_bytes(b)
    u32 = lambda o: struct.unpack_from('<I', b, o)[0]
    assert b[:8] == b'ANDROID!' and u32(40) == 2 and u32(36) == 4096
    assert u32(24) == 0, 'unexpected second-stage image'
    align = lambda n: (n + 4095) // 4096 * 4096
    kernel = b[4096:4096 + u32(8)]
    rd_start = 4096 + align(u32(8))
    ramdisk = b[rd_start:rd_start + u32(16)]
    qstart = struct.unpack_from('<Q', b, 1636)[0]
    assert qstart == rd_start + align(u32(16))
    dtbo = b[qstart:qstart + u32(1632)]
    dtb_start = qstart + align(u32(1632))
    dtb = b[dtb_start:dtb_start + u32(1648)]
    assert dtb[:4] == bytes.fromhex('d00dfeed')
    components = out / 'components'
    components.mkdir(exist_ok=True)
    for name, data in [('Image.gz', kernel), ('ramdisk.cpio.gz', ramdisk),
                       ('recovery_dtbo.img', dtbo), ('board.dtb', dtb)]:
        (components / name).write_bytes(data)
    base_cmd = (b[64:576].split(b'\0')[0] + b[608:1632].split(b'\0')[0]).decode()
    report = {'base_sha256': sha(b), 'base_path': str(a.base.resolve()),
              'component_sha256': {'kernel': sha(kernel), 'ramdisk': sha(ramdisk),
                                   'dtb': sha(dtb), 'dtbo': sha(dtbo)}, 'stages': {}}
    for stage in ['A', 'B0', 'B1', 'C1']:
        cmd = command(base_cmd, stage)
        (out / (stage + '.cmdline')).write_text(cmd + '\n')
        image = out / ('a9-smp-' + stage + '.img')
        if stage == 'C1':
            if not a.c_kernel:
                report['stages'][stage] = {'status': 'recipe only; requires rebuilt kernel/config and C1 DTB'}
                continue
            assert a.c_config and a.c_dtb
            config = a.c_config.read_text()
            for symbol in ['SMP', 'ARM_QCOM_CPUFREQ_HW', 'ARM_PSCI_CPUIDLE', 'ARM_PSCI_CPUIDLE_DOMAIN']:
                assert 'CONFIG_' + symbol + '=y' in config, symbol
            # Preserve header geometry and version. mkbootimg updates component ID/offsets.
            osword = u32(44)
            version, patch = osword >> 11, osword & 2047
            subprocess.run(['mkbootimg', '--header_version', '2', '--pagesize', '4096',
                            '--base', '0', '--kernel_offset', str(u32(12)),
                            '--ramdisk_offset', str(u32(20)), '--tags_offset', str(u32(32)),
                            '--second_offset', str(u32(28)), '--dtb_offset',
                            str(struct.unpack_from('<Q', b, 1652)[0]),
                            '--os_version', f'{version >> 14}.{(version >> 7) & 127}.{version & 127}',
                            '--os_patch_level', f'{2000 + (patch >> 4):04d}-{patch & 15:02d}',
                            '--kernel', str(a.c_kernel), '--ramdisk', str(components / 'ramdisk.cpio.gz'),
                            '--dtb', str(a.c_dtb), '--recovery_dtbo', str(components / 'recovery_dtbo.img'),
                            '--cmdline', cmd, '-o', str(image)], check=True)
        else:
            data = header_command(b, cmd)
            assert data[:64] == b[:64] and data[576:608] == b[576:608] and data[1632:] == b[1632:]
            image.write_bytes(data)
        digest = sha(image.read_bytes())
        image.with_suffix('.img.sha256').write_text(digest + '  ' + image.name + '\n')
        report['stages'][stage] = {'status': 'packed, not boot-tested', 'sha256': digest,
                                    'bytes': image.stat().st_size, 'cmdline': cmd}
        if stage == 'C1':
            report['stages'][stage].update(kernel_sha256=sha(a.c_kernel.read_bytes()),
                                          dtb_sha256=sha(a.c_dtb.read_bytes()),
                                          config_sha256=sha(a.c_config.read_bytes()))
    (out / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Packed offline variants under ' + str(out))


if __name__ == '__main__':
    main()
