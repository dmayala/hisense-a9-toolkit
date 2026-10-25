#!/usr/bin/env python3
"""Phone mksh/toybox rehearsal of RAM staging; only paths/mounts are modeled.

Synthetic non-calibration seed. No mount syscall, block device, or phone access.
The production shell body is executed with absolute paths rooted in a fixture;
mount/umount and Android identity/property queries are the only effect stubs.
"""
import argparse
import hashlib
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/mainline/sensors/s1-registry-ram.sh'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(sysroot, qemu, selected):
    cases = ('pass', 'tuple', 'root', 'slot', 'boot', 'prior-pas', 'nonram',
             'gate', 'prior-persist', 'nested-mount', 'mountpoint-link',
             'populated', 'mount-fail', 'wrong-mount', 'extract-fail', 'seed-hash')
    with tempfile.TemporaryDirectory(prefix='s1-registry-phone-') as tmp:
        base = Path(tmp)
        xbin = base / 'bin'
        xbin.mkdir()
        q = str(qemu)
        linker = str(sysroot / 'system/bin/linker64')
        toybox = str(sysroot / 'system/bin/toybox')
        shell = str(sysroot / 'system/bin/sh')
        bash = shutil.which('bash')
        python = shutil.which('python3')
        for name in ('cat', 'cut', 'sha256sum', 'ls', 'mkdir', 'timeout', 'tar'):
            wrapper = xbin / name
            wrapper.write_text(f'#!{bash}\nexec {q} -L / {linker} {toybox} {name} "$@"\n')
            wrapper.chmod(0o755)
        # A missing toybox applet must fail the production script, never be
        # accidentally satisfied by a convenient host binary.
        for name in ('awk', 'sed', 'find', 'grep', 'readlink'):
            wrapper = xbin / name
            wrapper.write_text(f'#!{bash}\nexec {q} -L / {linker} {toybox} {name} "$@"\n')
            wrapper.chmod(0o755)
        (xbin / 'id').write_text(f'#!{bash}\n[ "$CASE" = root ] && echo 1000 || echo 0\n')
        (xbin / 'getprop').write_text(f'#!{bash}\ncase "$1" in ro.boot.slot_suffix) [ "$CASE" = slot ] && echo _a || echo _b;; sys.boot_completed) [ "$CASE" = boot ] && echo 0 || echo 1;; *) exit 1;; esac\n')
        effect = '''import os,sys
from pathlib import Path
r=Path(os.environ['FIX']); case=os.environ['CASE']; op=Path(sys.argv[0]).name
with (r/'effects').open('a') as stream: stream.write(op+' '+' '.join(sys.argv[1:])+'\\n')
if op=='mount':
    if case=='mount-fail': sys.exit(1)
    assert sys.argv[1:]==['-t','tmpfs','-o','size=8m,mode=0700,nosuid,nodev,noexec','tmpfs',str(r/'mnt/vendor/persist')]
    fs='ext4' if case=='wrong-mount' else 'tmpfs'
    with (r/'proc/mounts').open('a') as stream: stream.write('tmpfs '+str(r/'mnt/vendor/persist')+' '+fs+' rw 0 0\\n')
elif op=='umount':
    assert sys.argv[1:]==[str(r/'mnt/vendor/persist')]
else: raise AssertionError(op)
'''
        for name in ('mount', 'umount'):
            (xbin / name).write_text(f'#!{python}\n' + effect)
        for p in xbin.iterdir():
            p.chmod(0o755)
        for case in cases:
            if selected and case not in selected:
                continue
            root = base / case
            bundle = root / 'data/local/tmp/sensors-s1'
            bundle.mkdir(parents=True)
            (root / 'proc/self').mkdir(parents=True)
            (root / 'mnt/vendor').mkdir(parents=True)
            for module, parameter in (('a9_eink', 'enable'), ('panel_hisense_hlte556n_eink', 'enable'), ('fp9929a', 'allow_power_on')):
                gate = root / f'sys/module/{module}/parameters/{parameter}'
                gate.parent.mkdir(parents=True)
                gate.write_text('Y\n' if case == 'gate' else 'N\n')
            (root / 'proc/mounts').write_text(('/dev/block/sda1' if case == 'nonram' else '/dev/block/ram0') + ' /data ext4 rw 0 0\n')
            mountinfo = root / 'proc/self/mountinfo'
            mountinfo.write_text('')
            if case in ('prior-persist', 'nested-mount'):
                point = str(root / 'mnt/vendor/persist') + ('/sensors' if case == 'nested-mount' else '')
                mountinfo.write_text(f'1 2 8:1 / {point} rw - ext4 /dev/block/sda1 rw\n')
            if case == 'prior-pas':
                (root / 'sys/module/qcom_q6v5_pas').mkdir()
            if case == 'mountpoint-link':
                (root / 'mnt/vendor/persist').symlink_to(base / 'protected')
            if case == 'populated':
                (root / 'mnt/vendor/persist').mkdir()
                (root / 'mnt/vendor/persist/canary').write_text('protected')
            payload = b'synthetic registry fixture\n'
            path = 'sensors/registry/registry/test'
            archive = bundle / 'registry-seed.tar'
            with tarfile.open(archive, 'w') as tar:
                info = tarfile.TarInfo(path)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
            if case == 'extract-fail':
                archive.write_bytes(b'not a tar archive')
            expected = hashlib.sha256(payload).hexdigest() if case != 'seed-hash' else '0' * 64
            (bundle / 'registry.sha256').write_text(expected + '  ' + str(root / 'mnt/vendor/persist' / path) + '\n')
            manifest = bundle / 'SHA256SUMS'
            manifest.write_text(''.join(digest(bundle / name) + '  ' + name + '\n' for name in ('registry-seed.tar', 'registry.sha256')))
            value = digest(manifest) if case != 'tuple' else '0' * 64
            script = root / 'stage.sh'
            body = SCRIPT.read_text()
            prefixes = ('/data/local/tmp/sensors-s1', '/sys/module', '/proc/mounts', '/proc/self/mountinfo', '/mnt')
            body = re.sub('|'.join(re.escape(p) for p in prefixes), lambda m: str(root) + m[0], body)
            script.write_text(body)
            env = dict(os.environ, PATH=str(xbin), CASE=case, FIX=str(root), LD_LIBRARY_PATH=str(sysroot / 'system/lib64'))
            result = subprocess.run([q, '-L', '/', linker, shell, str(script), '--stage-ram', value], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
            output = result.stdout.decode(errors='replace')
            assert (result.returncode == 0) == (case == 'pass'), (case, result.returncode, output)
            effects = (root / 'effects').read_text() if (root / 'effects').exists() else ''
            if case == 'pass':
                assert 'S1-RAM-COPY STAGED; registry listener NOT_IMPLEMENTED; no DSP start' in output, output
                assert (root / 'mnt/vendor/persist' / path).read_bytes() == payload
                assert effects.count('mount ') == 1 and 'umount ' not in effects, effects
            elif case in ('wrong-mount', 'extract-fail', 'seed-hash'):
                assert 'umount ' in effects, (case, effects, output)
            elif case != 'mount-fail':
                assert not effects, (case, effects, output)
            if case == 'populated':
                assert (root / 'mnt/vendor/persist/canary').read_text() == 'protected'
            print('PASS phone-shell RAM staging:', case)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sysroot', required=True, type=Path)
    parser.add_argument('--qemu', required=True, type=Path)
    parser.add_argument('--case', action='append')
    args = parser.parse_args()
    run(args.sysroot.resolve(), args.qemu.resolve(), args.case)
