#!/usr/bin/env python3
"""Extract only the stock fingerprint closure from saved host images. No device I/O."""
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import struct
import subprocess as sp

ROOT = Path('/home/user/A9-build/mainline')
STOCK = ROOT / 'android/ref/vendor_a.img'
# The QFIL package uses a different OEM signing root from this device's TZ.
# Use the saved device firmware, never alter/re-sign the supplied TA files.
MODEM = Path('/home/user/a9-assets/Backups/edl_2026-10-14_110759/lun4/modem_a.bin')
READELF = Path('/nix/store/vgcifiwvqk663pmk7d70jdyffi1p5y78-aarch64-unknown-linux-gnu-binutils-2.44/bin/aarch64-unknown-linux-gnu-readelf')


def member(image, path):
    return sp.check_output(['debugfs', '-R', 'cat /' + path, str(image)], stderr=sp.DEVNULL)


def needed(path):
    return re.findall(r'\(NEEDED\).*?\[(.*?)\]', sp.check_output([READELF, '-d', path], text=True))


def firmware(output, image=None):
    """Read selected sw_fp signed files from stock FAT16. No modem/NV payload copy."""
    result = {}
    with (image or MODEM).open('rb') as f:
        boot = f.read(512)
        u16 = lambda off: struct.unpack_from('<H', boot, off)[0]
        bs, cluster = u16(11), boot[13] * u16(11)
        reserved, fats, entries, sectors = u16(14), boot[16], u16(17), u16(22)
        assert boot[54:62] == b'FAT16   '
        root = (reserved + fats * sectors) * bs
        data = root + ((entries * 32 + bs - 1) // bs) * bs
        f.seek(reserved * bs)
        table = f.read(sectors * bs)

        def chain(c):
            chunks, seen = [], set()
            while 2 <= c < 0xfff8:
                assert c not in seen
                seen.add(c)
                f.seek(data + (c - 2) * cluster)
                chunks.append(f.read(cluster))
                c = struct.unpack_from('<H', table, c * 2)[0]
            return b''.join(chunks)

        def directory(raw, prefix=''):
            long = []
            for off in range(0, len(raw), 32):
                e = raw[off:off+32]
                if not e[0]: break
                if e[0] == 0xe5: long = []; continue
                if e[11] == 15:
                    long.insert(0, e[1:11] + e[14:26] + e[28:32]); continue
                name = e[:8].decode().rstrip()
                ext = e[8:11].decode().rstrip()
                name += '.' + ext if ext else ''
                if long: name = b''.join(long).decode('utf-16le').split('\0')[0].rstrip('\uffff')
                long = []
                if name in ('.', '..') or e[11] & 8: continue
                c, size = struct.unpack_from('<H', e, 26)[0], struct.unpack_from('<I', e, 28)[0]
                path = prefix + '/' + name
                if e[11] & 16: directory(chain(c), path)
                elif re.fullmatch(r'sw_fp\.(?:mdt|b\d\d)', name.lower()):
                    payload = chain(c)[:size]
                    (output / name.lower()).write_bytes(payload)
                    result[name.lower()] = dict(source=path, sha256=hashlib.sha256(payload).hexdigest(), bytes=size)
        f.seek(root)
        directory(f.read(entries * 32))
    assert 'sw_fp.mdt' in result and 'sw_fp.b00' in result
    return result


def extract(out):
    overlay = out / 'overlay'
    private = overlay / 'lib64/a9-fingerprint'
    private.mkdir(parents=True, exist_ok=True)
    sources = {}

    def write(path, data, source):
        p = overlay / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        sources[path] = dict(source=source, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
        return p

    service = 'bin/hw/android.hardware.biometrics.fingerprint@2.1-service'
    hal = 'lib64/hw/fingerprint.default.so'
    queue = [write(p, member(STOCK, p), str(STOCK) + ':/' + p) for p in [service, hal]]
    assert sources[service]['sha256'] == '014d6c4ee5129db573c6dd5dfc7c8db2cfcd02c3f787f9ec51ca71ea41c81f6c'
    assert sources[hal]['sha256'] == '5e2bbcf3458a251786a14ba3b01c17c8f6c79017c0eec0071a574f55719ed193'
    spec = importlib.util.spec_from_file_location('stock_package', Path(__file__).with_name('stock-package.py'))
    package = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package)
    copied, inherited = set(), set()
    while queue:
        binary = queue.pop()
        for name in needed(binary):
            if name in copied: continue
            copied.add(name)
            # Bionic must match the current GSI. All other legacy dependencies
            # use the saved stock vendor and its matching package VNDK closure.
            if name in ('libc.so', 'libm.so', 'libdl.so', 'libdl_android.so', 'ld-android.so'):
                inherited.add(name); continue
            data = member(STOCK, 'lib64/' + name)
            source = str(STOCK) + ':/lib64/' + name
            if not data.startswith(b'\x7fELF'):
                data = package.library(name)
                source = 'stock package VNDK30/system:/lib64/' + name
            assert data.startswith(b'\x7fELF'), 'missing ELF dependency: ' + name
            queue.append(write('lib64/a9-fingerprint/' + name, data, source))
    # Remove only stale files from this task's private extraction directory.
    for p in private.glob('*.so'):
        if str(p.relative_to(overlay)) not in sources: p.unlink()
    write('etc/sw_config.xml', member(STOCK, 'etc/sw_config.xml'), str(STOCK) + ':/etc/sw_config.xml')
    fw = overlay / 'firmware'
    fw.mkdir(exist_ok=True)
    ta = firmware(fw)
    for name, row in ta.items(): sources['firmware/' + name] = row | dict(image=str(MODEM))
    result = dict(files=sources, inherited_libraries=sorted(inherited), host_only=True)
    (out / 'stock-assets.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    result = extract(args.output)
    print('Stock fingerprint closure extracted:', len(result['files']), 'files')
