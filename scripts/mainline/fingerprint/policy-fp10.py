#!/usr/bin/env python3
"""Compile and query FP10's metadata-only enforcing-policy repair on the host."""
import argparse
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess as sp

HERE = Path(__file__).resolve().parent
ROOT = Path('/home/user/A9-build/mainline')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def stage(image, out):
    assert not out.exists(), 'preserve earlier policy evidence'
    out.mkdir(parents=True)
    def read(name):
        return sp.check_output(['debugfs', '-R', 'cat /etc/selinux/' + name, str(image)], stderr=sp.DEVNULL)
    original = read('vendor_sepolicy.cil')
    rules = [line for line in (HERE / 'policy/fingerprint.cil').read_bytes().splitlines()
             if line.startswith(b'(allow a9ml_rootsh ')]
    assert len(rules) == 5 and all(line not in original.splitlines() for line in rules)
    root_rules = HERE.parent / 'android/sepolicy/rootsh-enforcing.cil'
    main_additions = [line for line in root_rules.read_bytes().splitlines()
                      if line.startswith(b'(') and line not in original.splitlines()]
    vendor = out / 'vendor_sepolicy.cil'
    vendor.write_bytes(original + b'\n; FP10 diagnostic observation only\n' + b'\n'.join(rules + main_additions) + b'\n')
    public = out / 'plat_pub_versioned.cil'
    public.write_bytes(read('plat_pub_versioned.cil'))
    bundle = HERE.parent / 'boot-deadline/policy-platform'
    manifest = json.loads((bundle / 'inputs.json').read_text())
    inputs = []
    for name, digest in manifest['files'].items():
        data = gzip.decompress((bundle / (name + '.gz')).read_bytes())
        assert hashlib.sha256(data).hexdigest() == digest, name
        path = out / name
        path.write_bytes(data)
        inputs.append(path)
    binary = out / 'precompiled_sepolicy'
    command = [ROOT / 'android/dl/cf_arm64_x/otatools/bin/secilc', '-D', '-m', '-M', 'true', '-G', '-N', '-c', '30',
               *inputs, public, vendor, '-o', binary, '-f', '/dev/null']
    with (out / 'compile.log').open('w') as log:
        sp.run(list(map(str, command)), stdout=log, stderr=sp.STDOUT, check=True)
    (out / 'compile-command.json').write_text(json.dumps(list(map(str, command)), indent=2) + '\n')
    # Reuse the existing libsepol helper and host-only capability-bit adapter.
    cfg = load('fp_policy_cfg', HERE / 'policy-smoke.py')
    acceptance = load('fp_policy_host', HERE.parent / 'acceptance/onb.py')
    tools = {}
    for name, source in [('query', HERE / 'policy-check.c'), ('permissive', HERE.parent / 'acceptance/permissive.c')]:
        tools[name] = out / name
        sp.run(list(map(str, [cfg.CC, '-O2', '-Wall', '-Wextra', '-Werror', '-I' + cfg.DEV,
                              source, cfg.LIB, '-o', tools[name]])), check=True)
    policies = {}
    for name, raw in [('before', read('precompiled_sepolicy')), ('after', binary.read_bytes())]:
        adapted, cleared = acceptance.policy_for_host(raw)
        path = out / (name + '-host-query.policy')
        path.write_bytes(adapted)
        policies[name] = path
        assert sp.check_output([tools['permissive'], path], text=True).strip() == '', 'policy must remain enforcing'
    # Five denied operations establish the regression; the other three are
    # required parts of the same existing stat/cmdline/PID-birth read path.
    fixed = [('a9ml_fingerprint_device', 'chr_file', 'getattr'), ('tee_device', 'chr_file', 'getattr'),
             ('ion_device', 'chr_file', 'getattr'), ('hal_fingerprint_default', 'dir', 'search'),
             ('hal_fingerprint_default', 'file', 'read')]
    positive = fixed + [('hal_fingerprint_default', 'dir', 'getattr'),
                        ('hal_fingerprint_default', 'file', 'getattr'), ('hal_fingerprint_default', 'file', 'open')]
    fingerprint_positive = len(positive)
    # Carry the concurrently merged enforcing Wi-Fi cap socket rule too.
    positive += [('a9ml_rootsh', 'netlink_generic_socket', perm)
                 for perm in ('create', 'bind', 'read', 'write', 'getattr', 'getopt', 'setopt')]
    negative = [(target, 'chr_file', perm) for target in ('a9ml_fingerprint_device', 'tee_device', 'ion_device')
                for perm in ('open', 'read', 'write', 'ioctl')]
    negative += [('hal_fingerprint_default', 'file', 'write'), ('hal_fingerprint_default', 'process', 'ptrace'),
                 ('init', 'dir', 'search'), ('block_device', 'blk_file', 'write')]
    queries = []
    with (out / 'decisions.log').open('w') as log:
        for version, cases, expected in [('before', fixed, 0), ('after', positive, 1), ('after', negative, 0)]:
            for target, cls, perm in cases:
                context = ('u:r:' if target in ('hal_fingerprint_default', 'init', 'a9ml_rootsh') else 'u:object_r:') + target + ':s0'
                args = [tools['query'], policies[version], 'u:r:a9ml_rootsh:s0', context, cls, perm, str(expected)]
                sp.run(list(map(str, args)), check=True, stdout=log, stderr=sp.STDOUT)
                queries.append([version, target, cls, perm, expected])
    result = dict(base=str(image), base_sha256=sha(image), host_only=True, passed=True, permissive_domains=[],
                  source_sha256={p.name: sha(p) for p in [Path(__file__), HERE / 'policy/fingerprint.cil',
                                                         HERE / 'policy-check.c', HERE.parent / 'acceptance/permissive.c', root_rules]},
                  before_policy_sha256=hashlib.sha256(read('precompiled_sepolicy')).hexdigest(),
                  payload={name: sha(out / name) for name in ('vendor_sepolicy.cil', 'precompiled_sepolicy')},
                  queries=queries, repaired_denials=len(fixed), fingerprint_positive_decisions=fingerprint_positive,
                  positive_decisions=len(positive), negative_decisions=len(negative))
    (out / 'tests.json').write_text(json.dumps(result, indent=2) + '\n')
    print('PASS FP10 compiled policy: 5 denials reproduced, 8 metadata/proc reads and current-main netlink cap allowed, 16 I/O/write/ptrace controls denied; zero permissive domains')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    stage(args.base, args.out)
