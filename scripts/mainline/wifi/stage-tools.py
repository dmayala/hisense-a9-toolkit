#!/usr/bin/env python3
"""Verify/copy pinned cached coordinator tools offline; never execute a tool.

Reads the local lock, immutable Nix outputs and cached strace source archive.
The output is private host staging, not a target deployment or phone action.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import stat
import struct
import tarfile

HERE = Path(__file__).resolve().parent


def sha(data):
    return hashlib.sha256(data).hexdigest()


def static_arm64(data):
    if len(data) < 64 or data[:7] != b'\x7fELF\x02\x01\x01':
        raise ValueError('expected ELF64 little-endian executable')
    kind, machine = struct.unpack_from('<HH', data, 16)
    if kind not in (2, 3) or machine != 183:
        raise ValueError('expected ARM64 executable')
    offset = struct.unpack_from('<Q', data, 32)[0]
    width, count = struct.unpack_from('<HH', data, 54)
    if width != 56 or not count or count > 1024 or offset < 64 or offset + width * count > len(data):
        raise ValueError('malformed ELF program headers')
    for index in range(count):
        header = struct.unpack_from('<IIQQQQQQ', data, offset + index * width)
        typ, _, position, _, _, size, _, _ = header
        if position + size > len(data):
            raise ValueError('truncated ELF segment')
        if typ == 3:
            raise ValueError('PT_INTERP is forbidden')
        if typ == 2:
            if size % 16:
                raise ValueError('malformed ELF dynamic section')
            if any(struct.unpack_from('<qQ', data, p)[0] == 1 for p in range(position, position + size, 16)):
                raise ValueError('DT_NEEDED is forbidden')


def binary(row, data):
    if len(data) != row['bytes'] or sha(data) != row['sha256']:
        raise ValueError('pinned tool size/hash mismatch: ' + row['name'])
    static_arm64(data)
    if row['name'] == 'strace':
        for literal in (b'--kill-on-exit kill all tracees if strace is killed',
                        b'--kill-on-exit and -p/--attach are mutually exclusive options', b'kill-on-exit\0'):
            if literal not in data:
                raise ValueError('pinned strace lacks audited exit-kill help/parser evidence')
    return data


def read_regular(path):
    p = Path(path)
    if p.is_symlink() or not stat.S_ISREG(p.stat().st_mode):
        raise ValueError('tool/provenance input is not a regular file')
    return p.read_bytes()


def strace_source(row):
    proof = row['provenance']
    archive = read_regular(proof['source_archive'])
    if sha(archive) != proof['source_archive_sha256']:
        raise ValueError('strace source archive pin mismatch')
    # extractfile reads members into memory; never extract a path to disk.
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:xz') as source:
        members = [m for m in source.getmembers() if m.name.endswith('/src/strace.c')]
        if len(members) != 1 or not members[0].isreg():
            raise ValueError('unique strace source member missing')
        code = source.extractfile(members[0]).read()
        for evidence in (b'kill-on-exit', b'opt_kill_on_exit = true', b'ptrace_setoptions |= PTRACE_O_EXITKILL'):
            if evidence not in code:
                raise ValueError('cached strace source does not contain reviewed exit-kill implementation')
    return True


def inputs():
    lock_bytes = (HERE / 'coordinator-tools.lock.json').read_bytes()
    lock = json.loads(lock_bytes)
    if lock.get('version') != 1 or [r['name'] for r in lock['tools']] != ['strace', 'iw']:
        raise ValueError('unexpected coordinator tool inventory')
    result = {}
    for row in lock['tools']:
        if row['abi'] != 'aarch64-linux-musl-static' or Path(row['alias']).resolve() != Path(row['store']):
            raise ValueError('tool alias does not resolve to pinned immutable output')
        result[row['name']] = binary(row, read_regular(row['store']))
        if row['name'] == 'strace':
            strace_source(row)
    for name in ('coordinator-target.sh', 'audit-opens.awk'):
        result[name] = read_regular(HERE / name)
    result['coordinator-tools.lock.json'] = lock_bytes
    return result


def output_path(output):
    raw = Path(output).absolute()
    if any(p.is_symlink() for p in (raw, *raw.parents)):
        raise ValueError('tool output must not traverse symlinks')
    dest = raw.resolve()
    forbidden = [HERE.parents[2], Path('/home/user/A9'), Path('/home/user/a9-assets').resolve(),
                 Path('/srv/nas/A9'), Path('/nix/store')]
    if any(dest == p or p in dest.parents for p in forbidden):
        raise ValueError('tool output must be outside source, golden storage and Nix inputs')
    if any((p / '.git').exists() for p in (dest, *dest.parents)):
        raise ValueError('tool output must be outside every git checkout')
    if dest.exists():
        raise ValueError('tool output already exists; refusing overwrite')
    return dest


def stage(output):
    dest = output_path(output)
    payload = inputs()
    # All source verification precedes every output mutation. The private
    # directory and exclusive files prevent replacement of existing objects.
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.mkdir(mode=0o700)
    for name, content in payload.items():
        if name not in ('strace', 'iw', 'coordinator-target.sh', 'audit-opens.awk', 'coordinator-tools.lock.json'):
            raise ValueError('unexpected staged tool object')
        with (dest / name).open('xb') as stream:
            stream.write(content)
        (dest / name).chmod(0o700 if name in ('strace', 'iw') else 0o600)
    sums = ''.join(sha(payload[name]) + '  ' + name + '\n' for name in sorted(payload))
    with (dest / 'SHA256SUMS').open('x') as stream:
        stream.write(sums)
    (dest / 'SHA256SUMS').chmod(0o600)
    return dest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    select = p.add_mutually_exclusive_group(required=True)
    select.add_argument('--check', action='store_true', help='read-only pin/ELF/help/source verification')
    select.add_argument('--out', type=Path, help='new private output directory outside git/golden inputs')
    args = p.parse_args()
    if args.check:
        inputs()
        print('Pinned ARM64 static strace/iw and strace exit-kill source evidence verified; no target executable ran.')
    else:
        dest = stage(args.out)
        print('Staged pinned tools and current coordinator sources privately at ' + str(dest) + '; no deployment or execution.')


if __name__ == '__main__':
    main()
