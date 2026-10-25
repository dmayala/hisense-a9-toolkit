#!/usr/bin/env python3
"""Coordinator pipe adapter. JSON on stdin -> bounded private frame on stdout.
No 1Password access here. No files, argv/env secrets, input echo, traceback or logging.
A coordinator must pipe stdout straight into the reviewed RAM receiver, never tee it.
"""
import base64
import json
import sys
import os
import stat


def unique(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError()
        result[key]=value
    return result


def encode(raw):
    if len(raw)>1024:
        raise ValueError()
    value=json.loads(raw,object_pairs_hook=unique)
    if not isinstance(value,dict) or set(value)!={'ssid','passphrase'}:
        raise ValueError()
    name=value['ssid']; password=value['passphrase']
    if not isinstance(name,str) or not isinstance(password,str):
        raise ValueError()
    if not 1<=len(name.encode('utf-8'))<=32 or name!=name.strip() or any(not 32<=ord(c)<=126 for c in name):
        raise ValueError()
    if not 8<=len(password)<=63 or any(not 32<=ord(c)<=126 or c in '\\"' for c in password):
        raise ValueError()
    return ('version=1\ncountry=US\nfrequency=2427\nssid_hex='+name.encode().hex()
            +'\npassphrase_b64='+base64.b64encode(password.encode('ascii')).decode()+'\n').encode('ascii')


ROOT_STDIN = r'''#!/system/bin/sh
set +x
set -eu
umask 077
PATH=/system/bin:/system/xbin
export PATH
TMPDIR=/mnt/a9-wifi/trial
export TMPDIR
[ "$(id -u)" = 0 ]
[ -d "$TMPDIR" ] && [ ! -L "$TMPDIR" ]
[ "$(stat -c '%u:%a' "$TMPDIR")" = 0:700 ]
awk '$2=="/mnt/a9-wifi/trial" {n++;split($4,a,",");for(i in a)o[a[i]]=1;
 if($3!="tmpfs" || !o["noswap"] || !o["noexec"] || !o["nodev"] || !o["nosuid"])bad=1}
 END {exit !(n==1 && !bad)}' /proc/mounts
/system/bin/sh /data/local/tmp/a9-wifi-s2/s2-target.sh receive <<'A9_ANDROID_S2_PRIVATE_INPUT'
@FRAME@A9_ANDROID_S2_PRIVATE_INPUT
'''


def payload(frame):
    return ROOT_STDIN.replace('@FRAME@',frame.decode('ascii')).encode('ascii')


def main():
    try:
        if sys.argv[1:] not in ([],['--root-stdin']) or not all(stat.S_ISFIFO(os.fstat(fd).st_mode) for fd in (0,1)):
            raise ValueError()
        result=encode(sys.stdin.buffer.read(1025))
        sys.stdout.buffer.write(payload(result) if sys.argv[1:] else result)
        return 0
    except BaseException:
        print('S2_ENCODE input=rejected',file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
