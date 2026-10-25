#!/usr/bin/env python3
"""Focused host smoke of the inherited SFS init routing correction."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess as sp

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('prepare_credentials', HERE / 'prepare-vendor.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--vendor', type=Path, required=True, help='immutable existing integrated vendor')
p.add_argument('--out', type=Path, required=True)
a = p.parse_args()
a.out.mkdir(parents=True, exist_ok=True)
original = m.read(a.vendor, 'etc/init/hw/init.hlte556n.rc')
result = m.omit_legacy_qsee_directory(original)
legacy = (b'    mkdir /data/misc/qsee 0770 system system\n',
          b'    restorecon_recursive /data/misc/qsee\n')
assert all(original.count(line) == 1 for line in legacy), 'use inherited pre-fix fixture'
assert result == b''.join(line for line in original.splitlines(keepends=True) if line not in legacy)
assert m.omit_legacy_qsee_directory(result) == result
assert b'    mkdir /data/vendor/tzstorage 0770 system system\n' in result
assert b'    restorecon_recursive /data/vendor/tzstorage\n' in result
assert not any(b'/data/misc/qsee' in line for line in result.splitlines())
# Persistent-store startup, service order and all unrelated directives survive.
rc = a.out / 'init.hlte556n.rc'
rc.write_bytes(result)
r = sp.run([str(m.TOOLS / 'host_init_verifier'), str(rc)], capture_output=True, timeout=30)
(a.out / 'init-verifier.log').write_bytes(r.stdout + r.stderr)
assert r.returncode == 0, 'host_init_verifier failed; inspect private artifact'
summary = {'inherited_init_only_two_directives_removed': 'PASS',
           'idempotent_vendor_root_and_other_bytes_preserved': 'PASS',
           'host_init_verifier': 'PASS', 'device_tested': False,
           'source_image_written': False, 'persist_changed': False}
(a.out / 'tests.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(summary, indent=2))
