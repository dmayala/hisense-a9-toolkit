#!/usr/bin/env python3
"""Exercise the actual Android ARM64 trace SO with a synthetic stock backend."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess as sp

HERE = Path(__file__).resolve().parent
ROOT = Path('/home/user/A9-build/mainline')
CC = Path('/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang')
QEMU = '/nix/store/abpmwxx8hzfv201mjri0bxmg4jsf4gw9-qemu-10.1.0/bin/qemu-aarch64'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT/'integ8-fbe-host')
    out = parser.parse_args().out.resolve(); out.mkdir(exist_ok=True)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    common = [CC, '-O2', '-Wall', '-Wextra', '-Werror']
    commands = [common+['-shared', '-fPIC', HERE/'fbe-trace.c', '-ldl', '-llog',
                       '-Wl,-z,defs', '-Wl,-soname,liba9-fbe-trace.so', '-o', out/'liba9-fbe-trace.so'],
                common+['-shared', '-fPIC', '-DBACKEND', HERE/'test-fbe-trace.c',
                        '-Wl,-soname,libfake-stock.so', '-o', out/'libfake-stock.so'],
                common+[HERE/'test-fbe-trace.c', '-ldl', '-Wl,--export-dynamic', '-o', out/'test-fbe-trace']]
    with (out/'compile.log').open('w') as log:
        for command in commands:
            sp.run(list(map(str, command)), check=True, stdout=log, stderr=sp.STDOUT)
    audit = ROOT/'sol-wifi/target-audit'
    system = ROOT/'adsp/d2-v8-20261017/system-image/system'
    libs = ':'.join(map(str, [out, audit/'lib64', system/'lib64/bootstrap', system/'lib64']))
    results = []
    for begin, chars in [(-8, 0), (-8, -33), (-62, 0), (0, 0), (-33, 0)]:
        command = [QEMU, '-E', 'LD_LIBRARY_PATH='+libs, '-E', 'BEGIN_RC='+str(begin),
                   '-E', 'CHARS_RC='+str(chars), audit/'linker64', out/'test-fbe-trace', out/'liba9-fbe-trace.so']
        r = sp.run(list(map(str, command)), text=True, capture_output=True, timeout=20)
        (out/f'begin{begin}-chars{chars}.log').write_text(r.stdout+r.stderr)
        assert r.returncode == 0, r.stderr
        assert 'SYNTHETIC_SECRET' not in r.stdout+r.stderr
        assert r.stdout.count('BACKEND begin_called=1') == 3
        assert r.stdout.count('BACKEND configure_called=1') == 1
        assert ('A9_FBE_CONFIG configure_rc='+str(chars)+' os_version=160000 os_patch=202605 vendor_patch=20220405 boot_patch_present=0 boot_patch=0') in r.stdout
        expected = 2 if begin < 0 else 0
        assert r.stdout.count('BACKEND characteristics_called=1') == expected
        assert r.stdout.count('A9_FBE_KEY ') == expected
        assert r.stdout.count('A9_FBE_LOCAL local_rc=-33 verified=0 ') == expected
        if expected and chars == 0:
            assert 'hardware_aes=1 hardware_gcm=1 hardware_decrypt=1 os_version=160000 os_patch=202605 vendor_patch=20220405' in r.stdout
        assert 'PASS original_result_and_parameters_preserved=1' in r.stdout
        results.append(dict(begin=begin, chars=chars, passed=True))
    shell_source = (HERE/'vendor/bin/a9ml-wrapped-start.sh').read_text()
    function = shell_source[shell_source.index('fbe_failure_trace() {'):shell_source.index('\ncase "$1" in')]
    valid = ('A9_FBE_KEY begin_rc=-8 chars_rc=0 requested_gcm=1 hardware_aes=1 '
             'hardware_gcm=1 hardware_decrypt=1 os_version=160000 os_patch=202605 vendor_patch=20220405')
    config = 'A9_FBE_CONFIG configure_rc=0 os_version=160000 os_patch=202605 vendor_patch=20220405 boot_patch_present=0 boot_patch=0'
    local = 'A9_FBE_LOCAL local_rc=0 verified=0 hardware_aes=1 hardware_gcm=1 hardware_decrypt=1 os_version=160000 os_patch=202605 vendor_patch=20220405 boot_patch_present=1 boot_patch=20220405'
    (out/'trace-input').write_text(valid+'\n'+config+'\n'+local+'\n'+valid+' secret=SYNTHETIC_SECRET\n'+config+' secret=SYNTHETIC_SECRET\n'+local+' secret=SYNTHETIC_SECRET\nSYNTHETIC_SECRET\n')
    (out/'trace-output').write_text('')
    fixture = ('bounded() { cat "$FIXTURE_INPUT"; }\n'
               'log() { printf "%s\\n" "$*" >>"$FIXTURE_OUTPUT"; }\n'+function+'\nfbe_failure_trace\n')
    r = sp.run([audit/'bin/sh'], input=fixture, text=True, capture_output=True, timeout=20,
               env=dict(os.environ, FIXTURE_INPUT=str(out/'trace-input'), FIXTURE_OUTPUT=str(out/'trace-output')))
    assert r.returncode == 0, r.stderr
    assert (out/'trace-output').read_text() == valid+'\n'+config+'\n'+local+'\n'
    (out/'shell-trace-test.json').write_text(json.dumps(dict(actual_android_mksh=True, passed=True),indent=2)+'\n')
    names = ['fbe-trace.c', 'fbe-trace-abi.h', 'fbe-public-auth.h', 'test-fbe-trace.c', 'test-fbe-trace.py']
    names += ['vendor/bin/a9ml-wrapped-start.sh']
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    result = dict(groups=results, actual_android_arm64_qemu=True,
                  source_sha256={n:sha(HERE/n) for n in names},
                  library_sha256=sha(out/'liba9-fbe-trace.so'),
                  commands=[list(map(str, c)) for c in commands])
    (out/'tests.json').write_text(json.dumps(result, indent=2)+'\n')
    print('PASS five Android ARM64 FBE trace groups; no hardware test')


if __name__ == '__main__': main()
