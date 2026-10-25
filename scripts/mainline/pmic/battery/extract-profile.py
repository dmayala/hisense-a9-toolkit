#!/usr/bin/env python3
"""Extract the numerical A9 profile from the previously saved stock DTBO DTS."""
from pathlib import Path
import hashlib
import json
import re
import sys

source = Path(sys.argv[1])
text = source.read_text()
result = {'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
for name in ('fcc2-temp-lut', 'pc-temp-v2-lut'):
    block = re.search(r'qcom,' + name + r'\s*\{(.*?)\};', text, re.S).group(1)
    result[name] = {key: [(lambda n: n if n < 0x80000000 else n - 0x100000000)(int(v, 16)) for v in values.split()]
                   for key, values in re.findall(r'qcom,([\w-]+)\s*=\s*<([^>]+)>;', block)}
profile = result['pc-temp-v2-lut']
cols, rows, data = (profile[k] for k in ('lut-col-legend', 'lut-row-legend', 'lut-data'))
assert len(data) == len(rows) * len(cols)
# pc-temp-v1 values use 0.1 mV. Keep all temperature columns.
header = '/* SPDX-License-Identifier: GPL-2.0-only */\n/* Generated numerical stock A9 DTBO profile; see extract-profile.py. */\n'
for name, values in [('profile_temp', cols), ('profile_soc', rows)]:
    header += 'static const int ' + name + '[] = {' + ', '.join(map(str, values)) + '};\n'
header += '#define PROFILE_COLS ' + str(len(cols)) + '\n'
header += 'static const int profile_ocv[][PROFILE_COLS] = {\n'
for i in range(len(rows)):
    header += '\t{' + ', '.join(str(v*100) for v in data[i*len(cols):(i+1)*len(cols)]) + '},\n'
header += '};\n'
fcc = result['fcc2-temp-lut']
assert fcc['lut-col-legend'] == cols
header += 'static const int profile_fcc[] = {' + ', '.join(str(v*1000) for v in fcc['lut-data']) + '};\n'
Path(sys.argv[2]).write_text(header)
Path(sys.argv[3]).write_text(json.dumps(result, indent=2)+'\n')
