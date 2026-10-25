#!/usr/bin/env python3
"""Offline Bluetooth DT/config checks and snapshot module closure. No devices."""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
import struct
import sys

HERE = Path(__file__).resolve().parent


def firmware(directory):
    spec = importlib.util.spec_from_file_location('bt_firmware', HERE / 'firmware.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.check_directory(directory)
    text = (directory / 'address.dtsi').read_text()
    value = bytes.fromhex(re.search(r'local-bd-address = \[([^]]+)\]', text)[1])
    return value


def fdt(blob):
    if len(blob) < 40 or struct.unpack_from('>I', blob)[0] != 0xd00dfeed:
        raise ValueError('not a DTB')
    _, total, tree, strings, _, _, _, _, slen, tlen = struct.unpack_from('>10I', blob)
    if total > len(blob) or tree + tlen > total or strings + slen > total:
        raise ValueError('invalid DTB bounds')
    names = blob[strings:strings+slen]
    pos, end, stack, result = tree, tree+tlen, [], {}
    while pos + 4 <= end:
        token = struct.unpack_from('>I', blob, pos)[0]
        pos += 4
        if token == 1:
            z = blob.find(b'\0', pos, end)
            if z < 0: raise ValueError('invalid node')
            stack.append(blob[pos:z].decode())
            result['/'.join(stack) or '/'] = {}
            pos = (z+4) & ~3
        elif token == 2:
            if not stack: raise ValueError('unbalanced DTB')
            stack.pop()
        elif token == 3:
            if not stack or pos+8 > end: raise ValueError('invalid property')
            size, noff = struct.unpack_from('>II', blob, pos)
            pos += 8
            z = names.find(b'\0', noff)
            if z < 0 or pos+size > end: raise ValueError('invalid property bounds')
            result['/'.join(stack) or '/'][names[noff:z].decode()] = blob[pos:pos+size]
            pos = (pos+size+3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            if stack: raise ValueError('unbalanced DTB')
            return result
        else:
            raise ValueError('invalid DTB token')
    raise ValueError('missing DTB end')


def verify(config, dtb, fw):
    wanted = {}
    for line in (HERE / 'a9-bluetooth.config').read_text().splitlines():
        m = re.match(r'(CONFIG_\w+)=', line) or re.match(r'# (CONFIG_\w+) is not set', line)
        if m: wanted[m[1]] = line
    actual = set(config.read_text().splitlines())
    for name, line in wanted.items():
        if line not in actual:
            # Kconfig omits invisible disabled options rather than spelling out # unset.
            if line.startswith('# ') and not any(x.startswith(name+'=') for x in actual):
                continue
            raise ValueError('Bluetooth config mismatch: ' + name)
    nodes = fdt(dtb.read_bytes())
    keys = [k for k in nodes if k.endswith('/serial@4a8c000/bluetooth')]
    if len(keys) != 1: raise ValueError('Bluetooth DT node not unique')
    node = keys[0]
    props = nodes[node]
    if props.get('compatible') != b'qcom,wcn3990-bt\0':
        raise ValueError('wrong Bluetooth compatible')
    if props.get('local-bd-address') != firmware(fw):
        raise ValueError('Bluetooth DT identity differs from private inputs')
    if nodes[node.rsplit('/', 1)[0]].get('status') != b'okay\0':
        raise ValueError('Bluetooth UART is disabled')
    if props.get('max-speed') != struct.pack('>I', 3200000):
        raise ValueError('unexpected Bluetooth UART speed')
    if any(k in props for k in ('enable-gpios', 'swctrl-gpios')):
        raise ValueError('unproved Bluetooth control GPIO')
    print('Bluetooth config, UART and private DT identity verified (identity withheld).')


def field(path, name):
    data = path.read_bytes()
    # .modinfo entries begin at a NUL boundary; avoid unrelated embedded strings.
    marker = b'\0' + name.encode() + b'='
    pos = data.find(marker)
    if pos < 0: return ''
    pos += len(marker)
    return data[pos:data.index(b'\0', pos)].decode()


def verify_driver(path):
    if field(path, 'a9_qca_firmware_required') != '1':
        raise ValueError('QCA driver lacks A9 required-firmware policy')


def snapshot(build, output):
    normalize = lambda s: s.replace('-', '_')
    mods = {}
    for p in build.rglob('*.ko'):
        if 'modstage' not in p.parts and not any(x.startswith('.tmp') for x in p.parts):
            key = normalize(p.stem)
            if key in mods and mods[key] != p: raise ValueError('ambiguous module: '+key)
            mods[key] = p
    order, visiting = [], set()
    def visit(key):
        if key in order: return
        if key in visiting or key not in mods: raise ValueError('missing/cyclic module: '+key)
        visiting.add(key)
        for dep in field(mods[key], 'depends').split(','):
            if dep: visit(normalize(dep))
        visiting.remove(key)
        order.append(key)
    visit('hci_uart')
    verify_driver(mods['hci_uart'])
    reference = field(output / 'modules/a9_eink.ko', 'vermagic')
    if not reference: raise ValueError('missing reference E Ink module identity')
    existing = (output / 'modules/modules.dep').read_text().splitlines()
    generic = (output/'modules/modules.load').read_text().splitlines()
    if any(mods[k].name in generic for k in order):
        raise ValueError('Bluetooth unexpectedly in generic boot module list')
    copies = []
    for key in order:
        src = mods[key]
        if field(src, 'vermagic') != reference: raise ValueError('module vermagic mismatch')
        if src.stat().st_mtime > (build/'Module.symvers').stat().st_mtime + 300:
            raise ValueError('module newer than modpost; build may be running')
        dst = output/'modules'/src.name
        if dst.exists() and dst.read_bytes() != src.read_bytes():
            raise ValueError('snapshot module collision')
        if not dst.exists(): copies.append((src, dst))
        deps = [mods[normalize(d)].name for d in field(src, 'depends').split(',') if d]
        row = src.name + ':' + (' ' + ' '.join(deps) if deps else '')
        if not any(x.split(':',1)[0] == src.name for x in existing): existing.append(row)
    # All module checks above precede the first snapshot mutation.
    for src, dst in copies: shutil.copy2(src, dst)
    (output/'modules/modules.dep').write_text('\n'.join(existing)+'\n')
    (output/'modules/modules.bluetooth').write_text(''.join(mods[k].name+'\n' for k in order))
    # Keep BT out of modules.load: only the dedicated identity-gated setup owns it.
    (output/'bluetooth.enabled').write_text('1\n')
    print('Bluetooth module closure copied; generic display module order preserved.')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', required=True, type=Path)
    p.add_argument('--dtb', required=True, type=Path)
    p.add_argument('--firmware', required=True, type=Path)
    p.add_argument('--driver', type=Path)
    p.add_argument('--build', type=Path)
    p.add_argument('--snapshot', type=Path)
    a = p.parse_args()
    verify(a.config, a.dtb, a.firmware)
    if a.driver:
        verify_driver(a.driver)
    if a.snapshot:
        if not a.build: raise ValueError('--build required for snapshot')
        snapshot(a.build, a.snapshot)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, struct.error, UnicodeError):
        # Do not echo private source names or DT property bytes.
        print('Bluetooth kernel asset verification failed.', file=sys.stderr)
        sys.exit(1)
