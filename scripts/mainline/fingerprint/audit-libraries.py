#!/usr/bin/env python3
"""Static symbol/NEEDED audit; does not execute the ARM64 HAL or its libraries."""
from pathlib import Path
import argparse
import hashlib
import json
import re
import subprocess as sp

READELF = '/nix/store/vgcifiwvqk663pmk7d70jdyffi1p5y78-aarch64-unknown-linux-gnu-binutils-2.44/bin/aarch64-unknown-linux-gnu-readelf'
GSI = Path('/home/user/A9-build/mainline/adsp/d2-v8-20261017/system-image/system')


def audit(out):
    files = sorted((out / 'overlay/lib64').rglob('*.so'))
    files += [out / 'overlay/bin/hw/android.hardware.biometrics.fingerprint@2.1-service']
    files += [GSI / 'lib64/bootstrap' / (n + '.so') for n in ['libc', 'libm', 'libdl', 'libdl_android']]
    files += [GSI / 'bin/bootstrap/linker64']
    imports, definitions, inputs = {}, set(), {}
    needed = {}
    for path in files:
        raw = path.read_bytes()
        assert raw[:6] == b'\x7fELF\x02\x01' and raw[18:20] == b'\xb7\x00', path
        inputs[str(path)] = hashlib.sha256(raw).hexdigest()
        text = sp.check_output([READELF, '-W', '--dyn-syms', path], text=True)
        names = []
        for line in text.splitlines():
            # GNU readelf spells Bionic IFUNC as '<OS specific>: 10'. Parse
            # the right-hand Bind/Vis/Ndx fields rather than fixed columns.
            m = re.match(r'\s*\d+:\s+[0-9a-f]+\s+\d+\s+.+?\s+(GLOBAL|WEAK)\s+\w+\s+(\w+)\s+(\S+)', line)
            if not m: continue
            bind, index, name = m.groups()
            name = name.split('@')[0]
            if index == 'UND':
                if bind == 'GLOBAL': names.append(name)
            else: definitions.add(name)
        imports[str(path)] = names
        needed[str(path)] = re.findall(r'\(NEEDED\).*?\[(.*?)\]', sp.check_output([READELF, '-d', path], text=True))
    missing = {p: sorted(set(names) - definitions) for p, names in imports.items() if set(names) - definitions}
    result = dict(inputs=inputs, needed=needed, global_definitions=len(definitions), missing=missing,
                  executed_target=False, limitations='Static global-symbol coverage only; runtime linker namespaces and actual TA loading remain untested.')
    (out / 'link-symbol-audit.json').write_text(json.dumps(result, indent=2) + '\n')
    assert not missing, missing
    print('PASS stock HAL/closure ARM64 ELF and global-symbol coverage (' + str(len(files)) + ' ELFs)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    audit(parser.parse_args().output)
