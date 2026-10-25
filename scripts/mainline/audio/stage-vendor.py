#!/usr/bin/env python3
"""Stage a private audio-only vendor add-on. Never installs runtime start actions."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import struct
import tempfile

from snapshot import enabled, no_symlinks, private_path, read, relative_file, sha, verify_snapshot

HERE = Path(__file__).resolve().parent


def firmware_api():
    spec = importlib.util.spec_from_file_location('a9_audio_firmware', HERE/'firmware.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def static_arm64(data):
    if len(data) < 64 or data[:7] != b'\x7fELF\x02\x01\x01':
        raise ValueError('audio control utility must be an ELF64 binary')
    typ, machine = struct.unpack_from('<HH',data,16)
    off = struct.unpack_from('<Q',data,32)[0]
    size,count = struct.unpack_from('<HH',data,54)
    if typ not in (2,3) or machine != 183 or size != 56 or not count or off+size*count > len(data):
        raise ValueError('audio control utility must be a valid AArch64 executable')
    load = False
    for i in range(count):
        kind,flags,start,va,pa,filesz,memsz,align = struct.unpack_from('<IIQQQQQQ',data,off+i*size)
        if start+filesz > len(data) or filesz > memsz:
            raise ValueError('truncated executable segment')
        if kind == 1:
            load = True
        if kind == 3:
            raise ValueError('dynamic ELF interpreter is forbidden')
        if kind == 2:
            if filesz % 16:
                raise ValueError('invalid ELF dynamic table')
            for pos in range(start,start+filesz,16):
                tag,value = struct.unpack_from('<qQ',data,pos)
                if tag == 1:
                    raise ValueError('shared-library dependency is forbidden')
    if not load:
        raise ValueError('ELF executable lacks a loadable segment')


def binary_payload(directory):
    directory = no_symlinks(directory).resolve()
    checks = {}
    receipt = read(directory/'SHA256SUMS')
    for line in receipt.decode().splitlines():
        m = re.fullmatch(r'([0-9a-f]{64}) [ *](.+)',line)
        if not m:
            raise ValueError('malformed control utility SHA256SUMS')
        relative_file(m[2])
        if m[2] in checks or m[2] == 'SHA256SUMS':
            raise ValueError('ambiguous control utility checksum receipt')
        checks[m[2]] = m[1]
    name = 'a9-audio-ctl-android'
    if name not in checks:
        raise ValueError('missing Android control utility checksum')
    data = None
    for filename,digest in checks.items():
        blob = read(directory/filename)
        if sha(blob) != digest:
            raise ValueError('control utility receipt hash mismatch '+filename)
        if filename == name:
            data = blob
    static_arm64(data)
    return data,sha(receipt)


def preflight(stage,payload):
    for rel,data in payload.items():
        relative_file(rel)
        path = stage/rel
        no_symlinks(path)
        for parent in path.parents:
            if parent.exists() and not parent.is_dir():
                raise ValueError('vendor parent is not a directory '+str(parent))
        if path.exists():
            if not path.is_file() or path.stat().st_nlink != 1 or read(path) != data:
                raise ValueError('refusing different/hardlinked existing vendor payload '+rel)
            expected_mode = 0o755 if rel == 'bin/a9-audio-ctl' else 0o644
            if stat.S_IMODE(path.stat().st_mode) != expected_mode:
                raise ValueError('existing vendor payload has wrong mode '+rel)


def stage_vendor(snapshot,assets,binaries,stage):
    enabled()
    stage = private_path(stage)
    for source in (snapshot,assets,binaries):
        source = no_symlinks(source).resolve()
        if source == stage or source in stage.parents or stage in source.parents:
            raise ValueError('vendor stage and input directories must be disjoint')
    info,payload = verify_snapshot(snapshot)
    mode = os.environ.get('A9_WIFI_PD_MAPPER')
    if mode is not None and mode != info['mapper']:
        raise ValueError('Wi-Fi/audio mapper modes disagree')
    utility,binary_receipt = binary_payload(binaries)
    fw = firmware_api()
    files,geometry = fw.read_assets(assets)
    # Use the same verified firmware API as the parent. Render to a private
    # temporary directory so ALL conflicts are detected before vendor writes.
    stage.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.audio-firmware-plan-',dir=stage.parent) as tmp:
        fw_info = fw.stage_files(files,geometry,Path(tmp))
        planned = {p.relative_to(tmp).as_posix():read(p) for p in Path(tmp).rglob('*') if p.is_file()}
    metadata = json.loads(payload['module-metadata.json'])
    for name in info['module_order']:
        filename = metadata[name]['filename']
        planned['lib/modules/'+filename] = payload['modules/'+filename]
    for filename in ('modules.audio','modules.audio.dep'):
        planned['lib/modules/'+filename] = payload['modules/'+filename]
    planned['lib/modules/kernel-image.sha256'] = payload['kernel-image.sha256']
    planned['bin/a9-audio-ctl'] = utility
    for filename in ('snapshot.json','module-metadata.json','kernel.release'):
        planned['etc/a9-audio/'+filename] = payload[filename]
    result = {'schema':1,'kind':'a9-audio-vendor-addon','kernel_image_sha256':payload['kernel-image.sha256'].decode().strip(),
              'mapper':info['mapper'],'mapper_owner':'wifi','load_contract':info['load_contract'],
              'binary_receipt_sha256':binary_receipt,'firmware':fw_info,
              'files':{name:sha(data) for name,data in sorted(planned.items())},
              'installed_runtime_actions':[],
              'integration':'Use the same kernel snapshot for boot/display/Wi-Fi/audio. Loading PAS starts ADSP only in an --enable-adsp DTB (Wi-Fi also loads PAS); mount firmware and make the selected mapper ready before explicitly loading modules.audio.'}
    planned['etc/a9-audio/staged.json'] = (json.dumps(result,indent=2,sort_keys=True)+'\n').encode()
    preflight(stage,planned)
    stage.mkdir(parents=True,exist_ok=True)
    for rel,data in sorted(planned.items()):
        target = stage/rel
        if target.exists():
            continue  # Same bytes were preflighted; do not mutate existing mode/content.
        target.parent.mkdir(parents=True,exist_ok=True)
        no_symlinks(target)
        with target.open('xb') as f:
            f.write(data)
        target.chmod(0o755 if rel=='bin/a9-audio-ctl' else 0o644)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot',type=Path,required=True)
    p.add_argument('--assets',type=Path,required=True)
    p.add_argument('--binaries',type=Path,required=True)
    p.add_argument('--stage',type=Path,required=True)
    a=p.parse_args()
    try:
        result=stage_vendor(a.snapshot,a.assets,a.binaries,a.stage)
    except (ValueError,OSError,KeyError,struct.error,json.JSONDecodeError) as error:
        p.error(str(error))
    print(json.dumps({'stage':str(a.stage),'files':len(result['files']),'mapper':result['mapper'],
                      'runtime_actions':[],'load_contract':result['load_contract']},indent=2))


if __name__=='__main__':
    main()
