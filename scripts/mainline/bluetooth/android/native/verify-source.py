#!/usr/bin/env python3
"""Verify immutable upstream build inputs before generating the real AIDL API."""
import hashlib
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
manifest=json.loads((ROOT/'source-manifest.json').read_text())
if manifest['revision']!='5688f7eb1e117ed26e642a695de300b7683acb87':
    raise ValueError('unexpected interfaces revision')
for record in manifest['files']:
    if record.get('repository')=='platform/frameworks/native':
        if record['revision']!='2827a4a16b0340ecd07c2d5a6c89991799b362bb':
            raise ValueError('unexpected native revision')
        name=Path(record['path']).name
        if name not in {'binder_manager.h','binder_process.h','binder_stability.h'}:
            raise ValueError('unexpected platform header')
        path=ROOT/'source/platform-include/android'/name
    else:
        relative=Path(record['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('unexpected source path')
        path=ROOT/'source'/relative
    if hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']:
        raise ValueError('upstream source checksum mismatch')
if len(manifest['files'])!=16:
    raise ValueError('unexpected upstream source count')
api=ROOT/'source/bluetooth/aidl/aidl_api/android.hardware.bluetooth/1'
if (api/'.hash').read_text().strip()!='f0eb2eb6d3b970cef8fb1b6e22c4ec39a4da5119':
    raise ValueError('unexpected frozen AIDL hash')
print('Pinned source checksums and official frozen Bluetooth V1 API hash verified')
