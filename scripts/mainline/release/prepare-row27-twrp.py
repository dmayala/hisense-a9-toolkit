#!/usr/bin/env python3
"""Coordinator-only b-try TWRP hook: fresh row29 TEST FBE and trusted public key.

Never called by the host build. Uses the original b-try's selected transport
and USB ownership. Does not format TWRP's daily /data or alter its fstab.
"""
from pathlib import Path
import base64,hashlib,json,os,re,shlex,subprocess as sp,tempfile
HERE=Path(__file__).resolve().parent
ADB=os.environ.get('ADB','adb')
assert os.environ.get('A9_BTRY_IN_HOOK')=='1' and os.environ.get('A9_BTRY_FORMAT')=='1'
key=Path(os.environ['A9_BTRY_ADB_KEYS']).expanduser().read_bytes().split()
assert key and len(base64.b64decode(key[0],validate=True))==524
public=key[0]+b' a9-row27-release\n'
manifest=json.loads((HERE/'twrp-tools.json').read_text())
for n,h in [('row29-twrp-format',manifest['formatter_sha256']),('a9ml-row29-map',manifest['mapper_sha256'])]:
 assert hashlib.sha256((HERE/n).read_bytes()).hexdigest()==h

def call(args,limit=30):return sp.check_output([ADB,*map(str,args)],timeout=limit)
assert call(['get-state']).strip()==b'recovery','TWRP recovery transport required'
assert call(['shell','id -u']).strip()==b'0','TWRP root required'
remote=call(['shell','mktemp -d /tmp/a9-row27-release.XXXXXX']).decode().strip()
assert re.fullmatch(r'/tmp/a9-row27-release\.[A-Za-z0-9]+',remote)
q=shlex.quote(remote)
script=r'''set -eu
[ "$(id -u)" = 0 ]
[ -n "$(getprop ro.twrp.version)" ]
T=__TEMP__
MOUNTED=0
finish() {
    status=$?
    trap - EXIT HUP INT TERM
    if [ "$MOUNTED" = 1 ]; then umount "$T/data" || status=2; fi
    rm -f "$T/key.pub" "$T/row29-twrp-format" "$T/a9ml-row29-map" "$T/prepare.sh"
    rmdir "$T/data" 2>/dev/null || :
    rmdir "$T" 2>/dev/null || :
    exit "$status"
}
trap finish EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# Stock recovery may expose this namespace only through bootdevice.
if [ ! -e /dev/block/by-name ]; then
    [ -d /dev/block/bootdevice/by-name ]
    ln -s /dev/block/bootdevice/by-name /dev/block/by-name
fi
chmod 700 "$T/row29-twrp-format" "$T/a9ml-row29-map"
# Existing native guard: named-neighbor parent, internal controller/LUN4,
# 4GiB parent/4096B geometry and exact2197264+6191304 sector TEST map.
# Parent is read-only; mkfs operates ONLY on that bounded DM device.
"$T/row29-twrp-format" reformat-existing --write-approved
# Publish the SAME unchanged mapper Android's vendor init uses.
"$T/a9ml-row29-map"
mkdir "$T/data"
mount -t f2fs -o noatime,nosuid,nodev,nodiscard /dev/block/mapper/a9-row29-data "$T/data"
MOUNTED=1
mkdir -p "$T/data/misc/adb"
chown 1000:9998 "$T/data/misc"
chmod 1771 "$T/data/misc"
chcon u:object_r:system_data_file:s0 "$T/data/misc"
chown 1000:2000 "$T/data/misc/adb"
chmod 2750 "$T/data/misc/adb"
chcon u:object_r:adb_keys_file:s0 "$T/data/misc/adb"
cat "$T/key.pub" > "$T/data/misc/adb/adb_keys"
chown 1000:2000 "$T/data/misc/adb/adb_keys"
chmod 640 "$T/data/misc/adb/adb_keys"
chcon u:object_r:adb_keys_file:s0 "$T/data/misc/adb/adb_keys"
cmp "$T/key.pub" "$T/data/misc/adb/adb_keys"
sync
umount "$T/data"
MOUNTED=0
# The unused published DM mapping is kernel-local and disappears on the
# original b-try reboot. No daemon, reader, mount or extra USB owner remains.
echo ROW27_TWRP_FRESH_TEST_DATA_AND_ADB_KEY_READY
'''.replace('__TEMP__',q)
with tempfile.TemporaryDirectory(prefix='row27-key-',dir=os.environ.get('A9_BTRY_LOGS','/tmp')) as temp:
 p=Path(temp);(p/'key.pub').write_bytes(public);(p/'prepare.sh').write_text(script)
 for name,source in [('row29-twrp-format',HERE/'row29-twrp-format'),('a9ml-row29-map',HERE/'a9ml-row29-map'),('key.pub',p/'key.pub'),('prepare.sh',p/'prepare.sh')]:
  call(['push',source,remote+'/'+name],90)
 result=call(['shell','sh '+q+'/prepare.sh'],300).decode(errors='replace')
 print(result,end='')
 assert 'ROW27_TWRP_FRESH_TEST_DATA_AND_ADB_KEY_READY' in result
