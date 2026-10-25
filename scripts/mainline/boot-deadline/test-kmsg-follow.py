#!/usr/bin/env python3
"""Compile real enforcing policy and replay logger read/identity handoff on host.

Only synthetic descriptors and temporary regular files are used by the C test.
The actual logger entry point, device access and mount operations are not run.
"""
import argparse
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess as sp

HERE = Path(__file__).resolve().parent
ROOT = Path('/home/user/A9-build/mainline')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--vendor-image', type=Path, default=ROOT/'integ-20261024-17/vendor.img')
    a = ap.parse_args(); out = a.out.resolve(); out.mkdir(parents=True, exist_ok=True)

    spec = importlib.util.spec_from_file_location('logger_policy', HERE/'prepare-vendor.py')
    prep = importlib.util.module_from_spec(spec); spec.loader.exec_module(prep)
    base = sp.check_output(['debugfs', '-R', 'cat /etc/selinux/vendor_sepolicy.cil', a.vendor_image], stderr=sp.DEVNULL)
    public = out/'plat_pub_versioned.cil'
    public.write_bytes(sp.check_output(['debugfs', '-R', 'cat /etc/selinux/plat_pub_versioned.cil', a.vendor_image], stderr=sp.DEVNULL))
    base = base.replace(b'(typepermissive a9ml_rootsh)', b'')
    additions = [line for line in prep.RULES.splitlines() if line.startswith(b'(') and line not in base.splitlines()]
    vendor = out/'vendor.cil'; vendor.write_bytes(base+b'\n'+b'\n'.join(additions)+b'\n')
    platform = HERE/'policy-platform'
    manifest = json.loads((platform/'inputs.json').read_text()); inputs = []
    for name, digest in manifest['files'].items():
        data = gzip.decompress((platform/(name+'.gz')).read_bytes())
        assert hashlib.sha256(data).hexdigest() == digest
        p = out/name; p.write_bytes(data); inputs.append(p)
    policy = out/'production-policy.bin'
    command = [prep.SECILC, '-D', '-m', '-M', 'true', '-G', '-N', '-c', '30', *inputs,
               public, vendor, '-o', policy, '-f', '/dev/null']
    with (out/'policy-compile.log').open('w') as log:
        sp.run(list(map(str, command)), stdout=log, stderr=sp.STDOUT, check=True)

    # The pinned host libsepol 3.8 cannot parse Android's newer FunctionFS
    # capability. Compile a replay-only projection without that unrelated
    # superblock-label flag; retain every TE/MLS rule and the full production
    # binary above. No production policy or image is changed for this test.
    replay_inputs = []
    removed = 0
    for p in inputs:
        data = p.read_bytes(); removed += data.count(b'(policycap functionfs_seclabel)')
        q = out/('replay-'+p.name)
        q.write_bytes(data.replace(b'(policycap functionfs_seclabel)', b'')); replay_inputs.append(q)
    assert removed == 1
    replay_policy = out/'replay-policy.bin'
    command = [prep.SECILC, '-D', '-m', '-M', 'true', '-G', '-N', '-c', '30', *replay_inputs,
               public, vendor, '-o', replay_policy, '-f', '/dev/null']
    with (out/'replay-policy-compile.log').open('w') as log:
        sp.run(list(map(str, command)), stdout=log, stderr=sp.STDOUT, check=True)

    cc = os.environ.get('HOSTCC', 'cc')
    dev = Path('/nix/store/blq676h0hx4dq5hlrn6gbxa7zygkvqvz-libsepol-3.8.1-dev/include')
    lib = Path('/nix/store/q97pqkiy6bc27zh80167c8w478wbvg42-libsepol-3.8.1/lib/libsepol.a')
    wrappers = ('open', 'read', 'write', 'close', 'lseek', 'setxattr', 'dprintf', 'poll')
    command = [cc, '-O2', '-Wall', '-Wextra', '-Wno-unused-parameter', '-Werror',
               '-I'+str(dev), HERE/'test-kmsg-follow.c', lib,
               *['-Wl,--wrap='+name for name in wrappers], '-o', out/'test-kmsg-follow']
    with (out/'build.log').open('w') as log:
        sp.run(list(map(str, command)), stdout=log, stderr=sp.STDOUT, check=True)
    r = sp.run([out/'test-kmsg-follow', replay_policy], text=True, capture_output=True, timeout=20)
    (out/'test.log').write_text(r.stdout+r.stderr); print(r.stdout, end=''); r.check_returncode()
    assert r.stdout.count('PASS ') == 4
    result = dict(host_only=True, phone_access=False, real_logger_main=False,
                  simulated_policy_load=True, compiled_enforcing_policy=True,
                  kernel_read_continues=True, original_descriptors_refused=True,
                  handoff_no_duplicates_or_loss=True, interrupted_overrun=True,
                  observed_rootfs_read_allowed=True, persistent_denial_backoff=True,
                  replay_only_functionfs_capability_omitted=True,
                  policy_sha256=hashlib.sha256(policy.read_bytes()).hexdigest(),
                  source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in (HERE/'logger.c', HERE/'crash-evidence.h', HERE/'prepare-vendor.py',
                                           HERE.parent/'android/sepolicy/rootsh-enforcing.cil',
                                           HERE/'test-kmsg-follow.c', Path(__file__))})
    (out/'checks.json').write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__':
    main()
