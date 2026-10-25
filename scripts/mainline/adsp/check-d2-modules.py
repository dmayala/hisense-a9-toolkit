#!/usr/bin/env python3
"""Validate the explicit D2 sidecar against actual ELF dependencies and CRC receipt."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

# Runtime providers as well as ELF roots; PAS is deliberately the final loader.
ROOTS = ('apr','q6core','q6afe','q6afe_dai','q6afe_clocks','q6adm','q6routing',
         'q6asm','q6asm_dai','snd_soc_sm8250','lpasscc_sm6115',
         'pinctrl_sm6115_lpass_lpi','snd_soc_lpass_va_macro','snd_soc_lpass_rx_macro',
         'soundwire_qcom','snd_soc_wcd937x_sdw','snd_soc_wcd937x',
         'qrtr','qrtr_smd','qcom_pdr_msg','qcom_pd_mapper','qcom_q6v5_pas')
FORBIDDEN = re.compile(r'wsa|tx_macro|es9218|aw87|tfa|q6apm|q6prm|gpr|usb|offload|fastrpc|qcom_apr_gpr|hdmi|sdw_codec', re.I)

class ClosureError(ValueError):
    pass

def require(ok, message):
    if not ok:
        raise ClosureError(message)

def canonical(name):
    return name.removesuffix('.ko').replace('-', '_')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def check(snapshot, modinfo='modinfo', crcs=None, modprobe='modprobe'):
    snapshot = Path(snapshot)
    directory = snapshot/'d2/modules'
    rows = (snapshot/'d2/modules.d2').read_text().splitlines()
    require(all(re.fullmatch(r'[A-Za-z0-9_-]+\.ko', x) for x in rows), 'unsafe module filename')
    order = [canonical(x) for x in rows]
    require(len(order) == len(set(order)), 'duplicate module in D2 load order')
    # Check this before incidental inventory discrepancies: it is the original
    # packaging regression (the SDW transport alone cannot bind the codec).
    require('snd_soc_wcd937x' in order, 'missing D2 platform codec: snd-soc-wcd937x.ko')
    require(bool(order) and order[-1] == 'qcom_q6v5_pas', 'PAS must load strictly last')
    for root in ROOTS:
        require(root in order, 'missing D2 root: '+root)
    for name in order:
        require(not FORBIDDEN.search(name), 'forbidden D2 hardware/provider: '+name)
    files = {p.name: p for p in directory.glob('*.ko')}
    require(set(files) == set(rows), 'module file inventory differs from explicit D2 list')
    permitted = set(rows) | {'modules.dep','modules.options','modules.alias','modules.softdep'}
    require(all(p.name in permitted and p.is_file() and not p.is_symlink() for p in directory.iterdir()), 'unexpected sidecar file or symlink')
    dependencies = {}
    vermagic = set()
    for row, name in zip(rows, order):
        path = files[row]
        require(path.is_file() and not path.is_symlink(), 'module must be a regular non-symlink file: '+row)
        def field(key):
            return subprocess.check_output([str(modinfo), '-F', key, str(path)], text=True).strip()
        require(canonical(field('name')) == name, 'ELF module name differs from filename: '+row)
        dependencies[name] = [canonical(x) for x in field('depends').split(',') if x]
        vermagic.add(field('vermagic'))
    require(len(vermagic) == 1 and bool(next(iter(vermagic))), 'module vermagic differs or is empty')
    expected, visiting = [], set()
    def visit(name):
        require(name in dependencies, 'missing ELF dependency: '+name)
        if name in expected:
            return
        require(name not in visiting, 'ELF dependency cycle: '+name)
        visiting.add(name)
        for dependency in dependencies[name]:
            visit(dependency)
        visiting.remove(name)
        expected.append(name)
    for root in ROOTS:
        visit(root)
    require(order == expected, 'D2 load order differs from exact ELF/runtime root closure')
    names = dict(zip(order, rows))
    expected_dep = ''.join(names[n]+': '+' '.join(names[d] for d in dependencies[n])+'\n' for n in order)
    require((directory/'modules.dep').read_text() == expected_dep, 'modules.dep differs from actual ELF dependencies')
    require((directory/'modules.options').read_text() == 'qcom_q6v5_pas.ko a9_adsp_manual_start=1\n', 'unexpected D2 module options')
    for namespace in (snapshot/'modules', directory):
        for filename in ('modules.alias','modules.softdep'):
            path = namespace/filename
            require((not path.exists() and not path.is_symlink()) or (path.is_file() and not path.is_symlink() and path.read_bytes() == b''), 'autoload aliases/softdeps present: '+str(path))
    config = (snapshot/'config').read_text().splitlines()
    require('CONFIG_SND_SOC_WCD937X=m' in config, 'platform WCD937X must be modular')
    receipt = json.loads(Path(crcs or snapshot.parent/'module-crcs.json').read_text())
    symvers_path = snapshot/'Module.symvers'
    require(symvers_path.is_file() and not symvers_path.is_symlink(), 'missing regular full Module.symvers')
    symbols = {}
    for line in symvers_path.read_text().splitlines():
        fields = line.split()
        require(len(fields) >= 2, 'malformed full Module.symvers')
        symbols[fields[1]] = fields[0].lower()
    actual_imports = {}
    for row in rows:
        require(row in receipt, 'module missing from CRC receipt: '+row)
        require(receipt[row]['sha256'] == sha(files[row]), 'module CRC receipt hash mismatch: '+row)
        require(receipt[row]['vermagic'] in vermagic, 'module CRC receipt vermagic mismatch: '+row)
        imported = subprocess.check_output([str(modprobe), '--show-modversions', str(files[row])], text=True)
        require(receipt[row]['imports'] == imported, 'actual ELF imports differ from CRC receipt: '+row)
        actual_imports[row] = imported
        for line in imported.splitlines():
            value, name = line.split()
            require(symbols.get(name) == value.lower(), 'ELF import CRC differs from full Module.symvers: '+row+':'+name)
    return {'status':'PASS-D2-MODULE-CLOSURE', 'module_count':len(rows),
            'roots':list(ROOTS), 'order':rows, 'depends':dependencies,
            'modules_sha256':{row:sha(files[row]) for row in rows},
            'crc_receipt_sha256':sha(Path(crcs or snapshot.parent/'module-crcs.json')),
            'symvers_sha256':sha(symvers_path), 'actual_imports':actual_imports}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--modinfo', default='modinfo')
    args = parser.parse_args()
    try:
        report = check(args.snapshot, args.modinfo)
    except (ClosureError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, 'D2 MODULE CLOSURE REFUSED: '+str(error)+'\n')
    print(json.dumps(report, indent=2))
