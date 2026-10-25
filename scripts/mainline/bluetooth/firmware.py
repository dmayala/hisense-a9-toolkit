#!/usr/bin/env python3
"""Offline A9 Bluetooth assets. Never opens a device or modifies an input.
Outputs contain proprietary firmware and a private BD address: keep out of git.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import sys
import subprocess
import tempfile

# Sole real-image identity input, supplied by the coordinator; never committed.
BDADDR_FILE = Path('/home/user/A9-build/mainline/sol-bt/private/bdaddr.txt')

IMAGE_SHA256 = '7f3b66f92a6693c223a931ecdc7d05d4a83c696f7ff36ccfe2dce5e7dd786c1c'
NAMES = ('apbtfw10.tlv apbtfw11.tlv apnv10.bin apnv11.bin '
         'crbtfw11.tlv crbtfw20.tlv crbtfw21.tlv crnv11.bin crnv20.bin crnv21.bin '
         'cmbtfw10.tlv cmbtfw11.tlv cmbtfw12.tlv cmbtfw12.ver cmbtfw13.tlv cmbtfw13.ver '
         'cmnv10.bin cmnv11.bin cmnv12.bin cmnv13.bin cmnv13s.bin cmnv13t.bin').split()

FILE_HASHES = {'apbtfw10.tlv': '07f0f45cff57347a73ada68bf2bd564e17998383388ef27bd2e9de5269c1986f', 'apbtfw11.tlv': '29942c61a068d0b790ad4733473d86c176c239880cd2fa1aefb2da0ee2fc1817', 'apnv10.bin': '4cb35fec741a3294f5c64d41aefba45cf899c42cc4c00800bba7029cca5919d9', 'apnv11.bin': '9224718007bcc203b6e8b505f6bb178284776d7d47d72b7d155f4ea1e2597e36', 'crbtfw11.tlv': 'd6532b99875890fb7969874ff6e904939ca0d6f2d2bd1e1fd3ae86470bebc9f5', 'crbtfw20.tlv': 'd9afa5d9facd528f437e83bcc8147681afd432296ebbba5e77904c13fab28209', 'crbtfw21.tlv': '29942c61a068d0b790ad4733473d86c176c239880cd2fa1aefb2da0ee2fc1817', 'crnv11.bin': '206deb651dcfd187d1fd39f1f064af20324d1b4e28dcd72f5ed23bb8ef605d00', 'crnv20.bin': '6c96fe067860a79813b466a1d4644dc2748a4191b21a986a44d2cc0fe6ccaca7', 'crnv21.bin': 'b803e66675b06e4a18ffa0c41611e4792ee62c362e6d12f23610e53b2b9be08a', 'cmbtfw10.tlv': '987b1dadda62ca29f4a25ae0b43b9b14bcbda814fce52621e4a66ec3ccedc8b4', 'cmbtfw11.tlv': '825e0d96c6511bc5935944ada14334090eab5d82da7e2fa872b30b9667cfd48b', 'cmbtfw12.tlv': 'f1380ca2253e280196bbae298de818b897f4a60a65e0040e01f487744117542b', 'cmbtfw12.ver': 'd788e133daa8e6712d44a51ad2d1967af6218b70665303aa20630846bf11235e', 'cmbtfw13.tlv': 'd06fc120beb3555de123c37d0f43d29caf3c400177eabbc471b2aa95f30bebbc', 'cmbtfw13.ver': '8123d5f19492e499fe7cf7965351d86093341cfa902d2cf1ed37be3d32cefda4', 'cmnv10.bin': 'cc9e0d06a659fcb61dd9d88eca651687208dd3b6e34c467867b0928693daf391', 'cmnv11.bin': 'cf6ce8a3c81bab76ed8608a2fb1fa0b3bcf36b7c3e88636e91a27a74f392fe74', 'cmnv12.bin': 'eb69feeda1b84ec0bf1c6cefdb68c05a41223023e67556cadb6a894a35dfce74', 'cmnv13.bin': '9f3fec2b66fd0f97bda24f6f8f6a878d1f35e0770efab817d32d22fcc5cfc9cb', 'cmnv13s.bin': '9f3fec2b66fd0f97bda24f6f8f6a878d1f35e0770efab817d32d22fcc5cfc9cb', 'cmnv13t.bin': '9f3fec2b66fd0f97bda24f6f8f6a878d1f35e0770efab817d32d22fcc5cfc9cb'}

def digest(b):
    return hashlib.sha256(b).hexdigest()

def read_regular(path, cap):
    # O_NOFOLLOW protects the final component; inputs are operator-selected offline files.
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or before.st_size > cap:
        raise ValueError('input must be a size-bounded regular file')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_size > cap or
                (st.st_dev, st.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError('input must be a size-bounded regular file')
        with os.fdopen(os.dup(fd), 'rb') as f:
            b = f.read(cap + 1)
        if len(b) != st.st_size:
            raise ValueError('input changed or exceeds size bound')
        return b
    finally:
        os.close(fd)

def address(data, fmt):
    if fmt == 'text':
        if not re.fullmatch(rb'\s*(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\s*', data):
            raise ValueError('address text must contain exactly six colon-separated octets')
        canonical = bytes.fromhex(data.strip().decode().replace(':', ''))
    elif fmt == 'le-bytes':
        if len(data) != 6:
            raise ValueError('little-endian address must be exactly six bytes')
        canonical = data[::-1]
    else:
        # libbtnv .bt_nv.bin consists of [u8 id,u8 writeflag,u8 length,payload].
        off = 0
        found = []
        ids = set()
        while off < len(data):
            if off + 3 > len(data):
                raise ValueError('truncated stock NV record')
            item, flag, size = data[off:off + 3]
            off += 3
            if item not in (1, 2, 3) or item in ids or flag not in (0, 1) or off + size > len(data):
                raise ValueError('invalid stock NV record')
            ids.add(item)
            if size != (6 if item == 1 else 1):
                raise ValueError('invalid stock NV item length')
            if item == 1:
                found.append(data[off:off + size])
            off += size
        if len(found) != 1:
            raise ValueError('stock NV has no unique address record')
        # GetLocalAddress copies libbtnv bytes directly into its LE HCI buffer.
        canonical = found[0][::-1]
    if canonical in (bytes(6), b'\xff' * 6) or canonical[0] & 1:
        raise ValueError('address must be nonzero unicast and nonbroadcast')
    return canonical[::-1]

def coordinator_address():
    """Read the one fixed private text file; no alternate source or fallback."""
    if any(p.is_symlink() for p in (BDADDR_FILE, *BDADDR_FILE.parents)):
        raise ValueError('private address path must not contain symlinks')
    data = read_regular(BDADDR_FILE, 18)
    if not re.fullmatch(rb'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}\n?', data):
        raise ValueError('private address must be one colon-separated line')
    return address(data, 'text')


def extract(image):
    if len(image) != 1048576 or digest(image) != IMAGE_SHA256:
        raise ValueError('Bluetooth image is not the pinned golden image')
    # Stock declares a larger FAT16 volume than the saved used extent. Every read
    # below is independently bounded by the actual 1 MiB backup bytes.
    bps = struct.unpack_from('<H', image, 11)[0]
    spc = image[13]
    reserved = struct.unpack_from('<H', image, 14)[0]
    nf = image[16]
    nr = struct.unpack_from('<H', image, 17)[0]
    spf = struct.unpack_from('<H', image, 22)[0]
    if (bps, spc, reserved, nf, nr, spf) != (4096, 4, 1, 2, 512, 3):
        raise ValueError('unexpected FAT geometry')
    fat = reserved * bps
    root = (reserved + nf * spf) * bps
    start = root + ((nr * 32 + bps - 1) // bps) * bps
    def chain(cluster):
        seen = set()
        out = bytearray()
        while 2 <= cluster < 0xfff8:
            if cluster in seen:
                raise ValueError('FAT cluster cycle')
            seen.add(cluster)
            off = start + (cluster - 2) * spc * bps
            if off + spc * bps > len(image) or fat + cluster * 2 + 2 > root:
                raise ValueError('FAT cluster outside saved image')
            out.extend(image[off:off + spc * bps])
            cluster = struct.unpack_from('<H', image, fat + cluster * 2)[0]
        if cluster < 0xfff8:
            raise ValueError('invalid FAT chain end')
        return bytes(out)
    def entries(raw):
        for off in range(0, len(raw), 32):
            e = raw[off:off + 32]
            if len(e) != 32 or e[0] == 0:
                break
            if e[0] == 0xe5 or e[11] == 15 or e[11] & 8:
                continue
            name = e[:8].decode('ascii').strip()
            ext = e[8:11].decode('ascii').strip()
            name += '.' + ext if ext else ''
            if name in ('.', '..'):
                continue
            yield name, e[11], struct.unpack_from('<H', e, 26)[0], struct.unpack_from('<I', e, 28)[0]
    images = [c for n, a, c, s in entries(image[root:root + nr * 32]) if n == 'IMAGE' and a & 16]
    if len(images) != 1:
        raise ValueError('missing unique IMAGE directory')
    files = {}
    for name, attr, cluster, size in entries(chain(images[0])):
        name = name.lower()
        if attr & 16 or name not in NAMES or name in files:
            raise ValueError('unexpected firmware directory member')
        raw = chain(cluster)[:size]
        if len(raw) != size:
            raise ValueError('truncated file')
        files[name] = raw
    if set(files) != set(NAMES):
        raise ValueError('incomplete golden firmware set')
    for name, raw in files.items():
        validate_tlv(name, raw)
    return files

def validate_tlv(name, b):
    if name.endswith('.ver'):
        if not 1 <= len(b) <= 256:
            raise ValueError('invalid version metadata')
        return
    if len(b) < 4 or int.from_bytes(b[1:4], 'little') + 4 != len(b):
        raise ValueError('TLV outer length mismatch')
    if name.endswith('.tlv'):
        if b[0] != 1 or len(b) < 28:
            raise ValueError('invalid patch TLV')
        return
    if b[0] != 4:
        raise ValueError('expected stock enclosing NVM type 4')
    off = 4
    address_tags = []
    while off < len(b):
        if off + 4 > len(b):
            raise ValueError('truncated NVM set')
        typ, length = b[off], int.from_bytes(b[off + 1:off + 4], 'little')
        end = off + 4 + length
        if end > len(b):
            raise ValueError('NVM set exceeds enclosing TLV')
        if typ == 2:
            i = off + 4
            while i < end:
                if i + 12 > end:
                    raise ValueError('truncated NVM tag header')
                tag, size = struct.unpack_from('<HH', b, i)
                if i + 12 + size > end:
                    raise ValueError('truncated NVM tag data')
                if tag == 2:
                    if size != 6:
                        raise ValueError('invalid NVM address tag length')
                    address_tags.append(i + 12)
                i += 12 + size
        off = end
    if len(address_tags) != 1:
        raise ValueError('missing unique NVM address tag')

def write_new(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(data)

def stage(args):
    ba = coordinator_address()  # Fail before creating any output.
    files = extract(read_regular(args.image, 1048576))
    out = Path(args.output)
    if out.resolve().is_relative_to(Path('/home/user/a9-assets').resolve()):
        raise ValueError('output cannot be in the read-only golden archive')
    if any(out.resolve().is_relative_to(base.resolve()) for base in
           (Path.home() / 'A9', Path.home() / 'A9-wt')):
        raise ValueError('private assets cannot be staged in a project worktree')
    out.mkdir(mode=0o700, exist_ok=False)
    (out / 'qca').mkdir(mode=0o700)
    manifest = {'schema': 2, 'source_image_sha256': IMAGE_SHA256,
                'bdaddr_source': str(BDADDR_FILE), 'files': {}}
    for name, data in sorted(files.items()):
        rel = 'qca/' + name
        write_new(out / rel, data)
        manifest['files'][rel] = {'bytes': len(data), 'sha256': digest(data)}
    dt = ('/* PRIVATE generated Bluetooth address; never commit. */\n'
          '&a9_bluetooth {\n\tlocal-bd-address = [' + ' '.join(f'{x:02x}' for x in ba) + '];\n};\n').encode()
    write_new(out / 'address.dtsi', dt)
    manifest['files']['address.dtsi'] = {'bytes': len(dt), 'sha256': digest(dt)}
    write_new(out / 'manifest.json', (json.dumps(manifest, indent=2) + '\n').encode())
    check_directory(out)
    print('Staged and verified private Bluetooth assets; no device accessed.')

def check_directory(out):
    admitted = coordinator_address()
    if out.is_symlink() or not out.is_dir() or stat.S_IMODE(out.stat().st_mode) & 0o077:
        raise ValueError('asset directory must be private and not a symlink')
    manifest = json.loads(read_regular(out / 'manifest.json', 65536))
    if (manifest.get('schema') != 2 or manifest.get('source_image_sha256') != IMAGE_SHA256
            or manifest.get('bdaddr_source') != str(BDADDR_FILE)):
        raise ValueError('invalid manifest identity')
    expected = {'qca/' + n for n in NAMES} | {'address.dtsi'}
    if set(manifest.get('files', {})) != expected:
        raise ValueError('manifest must list the complete expected set')
    if (out / 'qca').is_symlink() or not (out / 'qca').is_dir():
        raise ValueError('qca must be a regular directory')
    actual = {str(p.relative_to(out)) for p in out.rglob('*') if p != out / 'qca'}
    if actual != expected | {'manifest.json'}:
        raise ValueError('unexpected asset files')
    for rel in sorted(expected):
        path = out / rel
        data = read_regular(path, 300000)
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError('asset file is not private')
        if manifest['files'][rel] != {'bytes': len(data), 'sha256': digest(data)}:
            raise ValueError('asset checksum mismatch')
        if rel.startswith('qca/'):
            if digest(data) != FILE_HASHES[path.name]:
                raise ValueError('firmware differs from pinned golden content')
            validate_tlv(rel, data)
        else:
            m = re.fullmatch(rb'/\* PRIVATE generated Bluetooth address; never commit\. \*/\n&a9_bluetooth \{\n\tlocal-bd-address = \[((?:[0-9a-f]{2} ){5}[0-9a-f]{2})\];\n\};\n', data)
            if not m:
                raise ValueError('invalid generated address include')
            if address(bytes.fromhex(m[1].decode()), 'le-bytes') != admitted:
                raise ValueError('staged identity differs from coordinator input')


PERSIST_SHA256 = 'b8dd040d049732cfa6d4fab08889fffb123de2215779ffcbb10690aae30407f3'


def import_persist(args):
    """Read only the known stock BT record; no identity inference or fallback."""
    raw = read_regular(args.image, 33554432)
    if len(raw) != 33554432 or digest(raw) != PERSIST_SHA256:
        raise ValueError('persist image is not the pinned golden image')
    out = Path(args.output)
    resolved = out.resolve()
    if resolved.is_relative_to(Path('/home/user/a9-assets').resolve()):
        raise ValueError('output cannot be in the read-only golden archive')
    if any(resolved.is_relative_to(base.resolve()) for base in
           (Path.home() / 'A9', Path.home() / 'A9-wt')):
        raise ValueError('private addresses cannot be written in a project worktree')
    if any(((parent / '.git').is_dir() and (parent / '.git' / 'HEAD').is_file()) or
           (parent / '.git').is_file() for parent in resolved.parents):
        raise ValueError('private addresses cannot be written inside a Git checkout')
    parent = out.parent
    if (parent.is_symlink() or not parent.is_dir() or
            stat.S_IMODE(parent.stat().st_mode) & 0o077):
        raise ValueError('output parent must be an existing private directory')
    provenance = out.with_name(out.name + '.provenance.json')
    if os.path.lexists(out) or os.path.lexists(provenance):
        raise FileExistsError('private output already exists')
    # Parse the exact bytes just verified, rather than reopening an input that
    # could change between hashing and debugfs. This is an offline regular copy.
    with tempfile.TemporaryDirectory(prefix='a9-bt-persist-') as scratch:
        image = Path(scratch) / 'persist.img'
        write_new(image, raw)
        result = subprocess.run(
            ['debugfs', '-R', 'cat /bluetooth/.bt_nv.bin', str(image)],
            capture_output=True, timeout=30, check=False)
    record = result.stdout
    if result.returncode or not record:
        print('Golden persist has no valid Bluetooth NV address record; no output written.',
              file=sys.stderr)
        return 1
    try:
        ba = address(record, 'stock-nv')
    except ValueError:
        print('Golden persist has no valid Bluetooth NV address record; no output written.',
              file=sys.stderr)
        return 1
    meta = {'schema': 1, 'source_persist_sha256': PERSIST_SHA256,
            'source_path_in_image': '/bluetooth/.bt_nv.bin',
            'source_record_sha256': digest(record), 'source_record_bytes': len(record),
            'output_format': 'six little-endian bytes', 'output_sha256': digest(ba)}
    write_new(out, ba)
    write_new(provenance, (json.dumps(meta, indent=2) + '\n').encode())
    print('Imported and verified a private Bluetooth address; value withheld.')
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('stage', help='requires the fixed private bdaddr.txt; no fallback')
    s.add_argument('--image', required=True)
    s.add_argument('--output', required=True, help='new private directory; never overwrites')
    i = sub.add_parser('import-persist', help='fixed-path read-only import; absent record fails')
    i.add_argument('--image', required=True, help='pinned offline golden persist regular file')
    i.add_argument('--output', required=True, help='new private six-byte LE file, outside Git')
    c = sub.add_parser('check')
    c.add_argument('--directory', required=True)
    args = p.parse_args()
    try:
        if args.command == 'stage':
            stage(args)
        elif args.command == 'import-persist':
            return import_persist(args)
        else:
            check_directory(Path(args.directory))
            print('Verified private Bluetooth assets; address withheld.')
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, struct.error, subprocess.TimeoutExpired) as e:
        # Never echo operator input, paths or address values on errors.
        print('Bluetooth asset validation failed (' + type(e).__name__ + ').', file=sys.stderr)
        return 1
    return 0

if __name__ == '__main__':
    sys.exit(main())
