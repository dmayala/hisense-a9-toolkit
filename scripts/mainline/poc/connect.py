#!/usr/bin/env python3
"""POC: join Wi-Fi. On a /data with a network the user saved in Settings, only wait for Android to
join it. On a fresh (RAM) /data, inject the test network from 1Password: credentials are piped over
adb stdin (never host argv); on the phone the PSK is briefly in the argv of `cmd wifi connect-network`
(no argv-free route there). Prints no identifiers."""
import json,os,subprocess,sys
c=json.load(open('/home/user/A9-build/mainline/wifi-android/s2-87k/release.json'))
adb=c['tools']['adb']; op=c['tools']['op']
WAIT=r'''i=0; while [ $i -lt 60 ]; do
  cmd wifi status 2>/dev/null | grep -q "Wifi is connected" && { echo "POC_CONNECT connected after=${i}s"; exit 0; }
  sleep 2; i=$((i+2))
done
echo "POC_CONNECT not connected after 120s"
'''
saved=subprocess.run([adb,'shell','cmd wifi list-networks 2>/dev/null | tail -n +2 | grep -c .'],
                     stdout=subprocess.PIPE,text=True,timeout=30).stdout.strip()
if saved not in ('','0'):
    r=subprocess.run([adb,'shell',WAIT],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=150)
    print('saved networks: %s; ' % saved + r.stdout.decode(errors='replace').strip()); sys.exit(0)
token=open('/run/agenix/op-token').read().strip()
env={k:os.environ[k] for k in ('PATH','HOME')}
env.update(OP_SERVICE_ACCOUNT_TOKEN=token,OP_CACHE='false',OP_BIOMETRIC_UNLOCK_ENABLED='false')
v=[subprocess.run([op,'read','--no-newline','op://vault/item/field'+f],stdout=subprocess.PIPE,
   stderr=subprocess.DEVNULL,env=env,timeout=20,check=True).stdout for f in ('network name','wireless network password')]
if any(b'\n' in x for x in v):sys.exit('bad cred format')
script=r'''read -r s; read -r p
out=$(cmd wifi connect-network "$s" wpa2 "$p" 2>&1); rc=$?
unset s p
case "$out" in *ailed*|*rror*|*xception*) cls=error;; *) cls=ok;; esac
echo "POC_CONNECT injected rc=$rc class=$cls"
'''+WAIT
r=subprocess.run([adb,'shell',script],input=v[0]+b'\n'+v[1]+b'\n',stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=180)
v=None
print(r.stdout.decode(errors='replace').strip())
