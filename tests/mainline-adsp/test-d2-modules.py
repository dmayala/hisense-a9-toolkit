#!/usr/bin/env python3
"""Check a real D2 ELF sidecar and reject mutations without changing its inodes."""
import argparse
import hashlib
from importlib.machinery import SourceFileLoader
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT/'scripts/mainline/adsp/check-d2-modules.py'
closure = SourceFileLoader('d2_modules_test', str(CHECKER)).load_module()
parser = argparse.ArgumentParser()
parser.add_argument('--snapshot', required=True, type=Path)
parser.add_argument('--out', required=True, type=Path)
a = parser.parse_args()
assert shutil.disk_usage('/').free >= 16_000_000_000
assert a.out.resolve().is_relative_to(Path.home()/'A9-build')
a.out.mkdir(parents=True, exist_ok=True)
receipt = {'checker_sha256':closure.sha(CHECKER), 'test_sha256':closure.sha(Path(__file__)),
           'snapshot':str(a.snapshot.resolve()), 'mutations':[]}
try:
    report = closure.check(a.snapshot)
except (closure.ClosureError, OSError, subprocess.CalledProcessError) as error:
    receipt.update(status='REFUSED', reason=str(error))
    (a.out/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    (a.out/'run.log').write_text('D2 MODULE REGRESSION REFUSED: '+str(error)+'\n')
    parser.exit(1, 'D2 MODULE REGRESSION REFUSED: '+str(error)+'\n')

assert report['module_count'] == 47, report['module_count']
assert all(report['order'].index(n) < report['order'].index('qcom_q6v5_pas.ko') for n in
           ('snd-soc-wcd937x.ko','snd-soc-wcd-mbhc.ko','snd-soc-wcd-classh.ko'))

def clone(root):
    snap = root/'snapshot'
    modules = snap/'d2/modules'
    modules.mkdir(parents=True)
    (snap/'modules').mkdir()
    for p in (a.snapshot/'d2/modules').iterdir():
        if p.suffix == '.ko':
            os.link(p, modules/p.name)  # All mutation functions leave .ko bytes alone.
        elif p.is_file():
            shutil.copy2(p, modules/p.name)
    for rel in ('Module.symvers','config','d2/modules.d2','modules/modules.alias','modules/modules.softdep'):
        p = a.snapshot/rel
        if p.exists():
            shutil.copy2(p, snap/rel)
    shutil.copy2(a.snapshot.parent/'module-crcs.json', root/'module-crcs.json')
    return snap

def rows(snap):
    return (snap/'d2/modules.d2').read_text().splitlines()

def write_rows(snap, values):
    (snap/'d2/modules.d2').write_text(''.join(x+'\n' for x in values))

def drop(snap, name):
    write_rows(snap, [x for x in rows(snap) if x != name])
    (snap/'d2/modules'/name).unlink()

def swap(snap, x, y):
    values = rows(snap)
    i, j = values.index(x), values.index(y)
    values[i], values[j] = values[j], values[i]
    write_rows(snap, values)

def bad_hash(snap):
    path = snap.parent/'module-crcs.json'
    data = json.loads(path.read_text())
    data['snd-soc-wcd937x.ko']['sha256'] = '0'*64
    path.write_text(json.dumps(data))

def bad_import_receipt(snap):
    path = snap.parent/'module-crcs.json'
    data = json.loads(path.read_text())
    data['snd-soc-wcd937x.ko']['imports'] += '0x00000000 nonexistent_symbol\n'
    path.write_text(json.dumps(data))

def bad_symvers(snap):
    # Corrupt one symbol actually imported by the platform codec.
    data = json.loads((snap.parent/'module-crcs.json').read_text())
    symbol = data['snd-soc-wcd937x.ko']['imports'].splitlines()[0].split()[1]
    path = snap/'Module.symvers'
    lines = path.read_text().splitlines(keepends=True)
    found = False
    for i, line in enumerate(lines):
        if line.split()[1] == symbol:
            lines[i] = '0x00000000'+line[len(line.split()[0]):]
            found = True
    assert found
    path.write_text(''.join(lines))

def symlink_module(snap):
    p = snap/'d2/modules/snd-soc-wcd937x.ko'
    p.unlink()
    p.symlink_to(a.snapshot/'d2/modules/snd-soc-wcd937x.ko')

def dangling_alias(snap):
    p = snap/'modules/modules.alias'
    p.unlink(missing_ok=True)
    p.symlink_to('missing-alias-file')

def forbidden(snap):
    write_rows(snap, rows(snap)[:-1]+['snd-soc-wsa-macro.ko','qcom_q6v5_pas.ko'])
    (snap/'d2/modules/snd-soc-wsa-macro.ko').write_bytes(b'forbidden fixture')

cases = [
    ('missing platform codec', lambda s:drop(s,'snd-soc-wcd937x.ko'), 'missing D2 platform codec'),
    ('missing MBHC library', lambda s:drop(s,'snd-soc-wcd-mbhc.ko'), 'missing ELF dependency'),
    ('missing classH library', lambda s:drop(s,'snd-soc-wcd-classh.ko'), 'missing ELF dependency'),
    ('codec before dependency', lambda s:swap(s,'snd-soc-wcd937x.ko','snd-soc-wcd-classh.ko'), 'exact ELF/runtime'),
    ('PAS first', lambda s:swap(s,'qcom_q6v5_pas.ko',rows(s)[0]), 'PAS must load strictly last'),
    ('duplicate load', lambda s:write_rows(s,rows(s)[:-1]+['snd-soc-wcd937x.ko','qcom_q6v5_pas.ko']), 'duplicate'),
    ('path traversal', lambda s:write_rows(s,['../codec.ko']+rows(s)), 'unsafe module filename'),
    ('forbidden hardware', forbidden, 'forbidden D2 hardware'),
    ('autoload alias', lambda s:(s/'modules/modules.alias').write_text('alias * snd_soc_wcd937x\n'), 'autoload aliases'),
    ('sidecar softdep', lambda s:(s/'d2/modules/modules.softdep').write_text('softdep snd_soc_wcd937x pre: x\n'), 'autoload aliases'),
    ('bad dependency manifest', lambda s:(s/'d2/modules/modules.dep').write_text(''), 'modules.dep differs'),
    ('bad module options', lambda s:(s/'d2/modules/modules.options').write_text(''), 'module options'),
    ('unbound CRC hash', bad_hash, 'CRC receipt hash mismatch'),
    ('stale imported CRC receipt', bad_import_receipt, 'actual ELF imports differ'),
    ('stale full symvers CRC', bad_symvers, 'ELF import CRC differs'),
    ('missing full symvers', lambda s:(s/'Module.symvers').unlink(), 'missing regular full Module.symvers'),
    ('dangling autoload alias', dangling_alias, 'autoload aliases'),
    ('nested loader directory', lambda s:(s/'d2/modules/nested').mkdir(), 'unexpected sidecar file'),
    ('extra loader list', lambda s:(s/'d2/modules/modules.load').write_text('snd-soc-wcd937x.ko\n'), 'unexpected sidecar file'),
    ('extra unlisted blob', lambda s:(s/'d2/modules/unlisted.blob').write_bytes(b'x'), 'unexpected sidecar file'),
    ('symlink ELF', symlink_module, 'unexpected sidecar file'),
]
for name, mutate, reason in cases:
    assert shutil.disk_usage('/').free >= 16_000_000_000
    with tempfile.TemporaryDirectory(prefix='d2-closure-', dir=a.out) as directory:
        snap = clone(Path(directory))
        mutate(snap)
        try:
            closure.check(snap)
        except closure.ClosureError as error:
            assert reason in str(error), (name, str(error))
            receipt['mutations'].append({'case':name,'refusal':str(error)})
        else:
            raise AssertionError('unsafe closure accepted: '+name)
# Every original module remains byte-identical after hardlink-based fixtures.
assert closure.check(a.snapshot) == report
receipt.update(status='PASS', actual=report)
(a.out/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
message = 'ADSP-D2-MODULE-CLOSURE PASS: 47 real ELF modules, platform codec+libraries before PAS, 21 mutations refused\n'
(a.out/'run.log').write_text(message)
print(message, end='')
