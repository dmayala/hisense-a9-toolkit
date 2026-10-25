#!/usr/bin/env python3
"""Host libsepol decisions for the fp1 denials and fp2 repair; no target code."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess as sp

HERE = Path(__file__).resolve().parent
ROOT = Path('/home/user/A9-build/mainline')
OUT = ROOT / 'integ-20261023-fp2'
CC = '/nix/store/23wxjvr0712zdrjlkg3p4533g8f8g1if-gcc-wrapper-14.3.0/bin/gcc'
DEV = '/nix/store/blq676h0hx4dq5hlrn6gbxa7zygkvqvz-libsepol-3.8.1-dev/include'
LIB = '/nix/store/q97pqkiy6bc27zh80167c8w478wbvg42-libsepol-3.8.1/lib/libsepol.a'


def main():
    # Reuse the acceptance sweep's existing host parser compatibility helper.
    # Only functionfs_seclabel bit12 is removed in these throwaway query copies;
    # the shipped binary, all allow rules and type/permissive maps stay intact.
    spec = importlib.util.spec_from_file_location('acceptance_policy', HERE.parent / 'acceptance/onb.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    files = {}
    receipt = {}
    for version in ['fp1', 'fp2']:
        source = ROOT / ('integ-20261023-' + version) / 'overlay/etc/selinux/precompiled_sepolicy'
        raw = source.read_bytes()
        data, cleared = module.policy_for_host(raw)
        path = OUT / (version + '-host-query.policy')
        path.write_bytes(data); files[version] = path
        receipt[version] = dict(source_sha256=hashlib.sha256(raw).hexdigest(), host_query_sha256=hashlib.sha256(data).hexdigest(),
                                functionfs_bit12_cleared_for_host_only=cleared)
    tool = OUT / 'policy-check'
    command = [CC, '-O2', '-Wall', '-Wextra', '-Werror', '-I' + DEV, HERE / 'policy-check.c', LIB, '-o', tool]
    sp.run(list(map(str, command)), check=True)
    # Reproduce the exact fp1 caller/label denial before checking the new image.
    queries = [
        ('fp1', 'vendor_init', 'vendor_file', 'system', 'module_load', 0),
        ('fp2', 'vendor_init', 'vendor_file', 'system', 'module_load', 1),
        ('fp2', 'vendor_init', 'fingerprint_vendor_data_file', 'dir', 'create', 1),
        ('fp2', 'system_server', 'fingerprint_vendor_data_file', 'dir', 'search', 1),
        ('fp2', 'hal_fingerprint_default', 'fingerprint_vendor_data_file', 'dir', 'write', 1),
        ('fp2', 'shell', 'a9ml_tee_listener_prop', 'file', 'read', 0),
    ]
    with (OUT / 'policy-smoke.log').open('w') as log:
        for version, subject, target, cls, perm, expected in queries:
            args = [tool, files[version], 'u:r:' + subject + ':s0', 'u:object_r:' + target + ':s0', cls, perm, str(expected)]
            sp.run(list(map(str, args)), check=True, stdout=log, stderr=sp.STDOUT)
    (OUT / 'policy-smoke.json').write_text(json.dumps(dict(policies=receipt, queries=queries, passed=True, host_only=True), indent=2) + '\n')
    print('PASS compiled policy: fp1 module denial reproduced, fp2 module/data access allowed; shell listener property remains private')


if __name__ == '__main__': main()
