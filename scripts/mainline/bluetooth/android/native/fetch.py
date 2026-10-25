#!/usr/bin/env python3
"""Explicit read-only fetch of the locked upstream files; never run by build.sh."""
import base64
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent
manifest = json.loads((ROOT/'source-manifest.json').read_text())
revisions = {
    'platform/hardware/interfaces': '5688f7eb1e117ed26e642a695de300b7683acb87',
    'platform/frameworks/native': '2827a4a16b0340ecd07c2d5a6c89991799b362bb',
}
if manifest.get('revision') != revisions['platform/hardware/interfaces']:
    raise ValueError('unexpected source lock revision')
pending = []
for item in manifest['files']:
    repository = item.get('repository', 'platform/hardware/interfaces')
    revision = item.get('revision', manifest['revision'])
    relative = Path(item['path'])
    if repository not in revisions or revision != revisions[repository]:
        raise ValueError('source lock names an unsupported repository/revision')
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('invalid locked source path')
    url = f'https://android.googlesource.com/{repository}/+/{revision}/{relative}?format=TEXT'
    if item['url'] != url:
        raise ValueError('source lock URL mismatch')
    data = base64.b64decode(urllib.request.urlopen(url, timeout=60).read(), validate=True)
    if len(data) != item['size'] or hashlib.sha256(data).hexdigest() != item['sha256']:
        raise ValueError('downloaded bytes differ from locked source')
    if repository == 'platform/frameworks/native':
        if relative.name not in {'binder_manager.h', 'binder_process.h', 'binder_stability.h'}:
            raise ValueError('unexpected platform header')
        target = ROOT/'source/platform-include/android'/relative.name
    else:
        target = ROOT/'source'/relative
    if target.is_symlink() or any(p.is_symlink() for p in target.parents):
        raise ValueError('source destination is a symlink')
    pending.append((target, data))
if len(pending) != 16:
    raise ValueError('unexpected locked source count')
for target, data in pending:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
print('Fetched and checksum-verified 16 pinned source files; source lock unchanged.')
