#!/usr/bin/env python3
"""Create a private DT include; never print device addresses or write source."""
import argparse
import json
import hashlib
import os
from pathlib import Path
import re
import tempfile

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--assets', type=Path, required=True)
p.add_argument('--output', type=Path, required=True)
a = p.parse_args()
repo = Path(__file__).resolve().parents[3]
if any(n.is_symlink() for n in (a.output.absolute(),*a.output.absolute().parents)):
    p.error('private DT output must not traverse symlinks')
output = a.output.resolve()
forbidden=[repo,Path('/home/user/A9'),Path('/home/user/a9-assets').resolve(),Path('/srv/nas/A9')]
if any(output==n or n in output.parents for n in forbidden):
    p.error('private DT output must be outside the source checkout')
root = a.assets.resolve()
source = root / 'private/wlan_mac.bin'
if source.is_symlink() or not source.is_file():
    p.error('missing regular private MAC asset')
rows = json.loads((root/'manifest.json').read_text())
record = next((r for r in rows if r['path'] == 'private/wlan_mac.bin'), None)
data = source.read_bytes()
if not record or hashlib.sha256(data).hexdigest() != record['sha256']:
    p.error('private MAC asset integrity mismatch')
matches = re.findall(rb'^Intf0MacAddress=([0-9A-Fa-f]{12})\r?$', data, re.M)
if len(matches) != 1:
    p.error('private MAC asset has no unique Intf0 entry')
address = bytes.fromhex(matches[0].decode())
if address[0] & 1 or address in (bytes(6), b'\xff'*6):
    p.error('private MAC asset is not a valid unicast address')
output.parent.mkdir(parents=True, exist_ok=True)
fd, temporary = tempfile.mkstemp(prefix='.a9-wifi-private-',dir=output.parent)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write('/* PRIVATE generated device identity: never commit. */\n')
        f.write('&wifi { local-mac-address = [' + address.hex(' ') + ']; };\n')
    os.replace(temporary,output)
finally:
    if os.path.exists(temporary):os.unlink(temporary)
