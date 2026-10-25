#!/usr/bin/env python3
"""Fifth focused group: inspect the actual prepared vendor, no device access."""
import argparse
import importlib.util
import json
from pathlib import Path
HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('credentials', HERE.parents[1] / 'credentials/prepare-vendor.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
p = argparse.ArgumentParser(description=__doc__); p.add_argument('--out', required=True, type=Path)
a = p.parse_args(); result = json.loads((a.out / 'result.json').read_text()); image = Path(result['target'])
build = json.loads((a.out / 'diagnostics/build.json').read_text())
for name in ('main.c', 'diag.c', 'transport.c', 'diag.h', 'schema.h', 'transport.h'):
    assert build['sources'][name] == m.image_sha(HERE / name), name
binary = m.read(image, m.DIAG_BINARY)
assert m.sha(binary) == build['sha256']
meta = m.metadata_module().metadata(image, m.DIAG_BINARY)
assert meta['label'] == 'u:object_r:a9_rpmb_diag_exec:s0' and meta['mode'] == '0755'
assert m.read(image, m.DIAG_RC) == (HERE / 'init.a9-rpmb-diag.rc').read_bytes()
hardware = m.read(image, 'etc/init/hw/init.hlte556n.rc')
assert b'    start a9_rpmb_diag\n    start vendor.qseecomd\n' in hardware
cil = m.read(image, 'etc/selinux/vendor_sepolicy.cil')
assert b'(typepermissive a9_rpmb_diag)' not in cil
for line in (HERE / 'policy.cil').read_bytes().splitlines():
    if line.startswith(b'('): assert line in cil.splitlines(), line
assert (a.out / 'compile.log').stat().st_size == 0
assert (a.out / 'init-verifier.log').stat().st_size == 0
assert 'Pass 5:' in (a.out / 'fsck.log').read_text()
assert image.stat().st_size == Path(result['source']).stat().st_size
assert set(result['added']) >= {m.DIAG_BINARY, m.DIAG_RC}
assert set(result['changed']) >= {'etc/init/hw/init.hlte556n.rc', 'etc/selinux/vendor_sepolicy.cil'}
check = dict(vendor_policy_init_readback='PASS', executable_sha256=build['sha256'],
             enforcing_domain='a9_rpmb_diag', no_growth=True, credential_policy_weakened=False)
(a.out / 'diagnostics-test.json').write_text(json.dumps(check, indent=2)+'\n')
print(json.dumps(check, indent=2))
