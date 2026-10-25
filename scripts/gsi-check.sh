#!/usr/bin/env bash
#
# gsi-check.sh — could this GSI base run on the A9 (stock Android 11 vendor,
# kernel 4.19)? Offline, read-only (debugfs on the image). Written 2026-10-13
# for the LOS 24 / Android 17 question (docs/a6l-harvest-2026-10-13.md §2).
#
# Checks, each OK / WARN / FAIL:
#   base       SDK, release, fingerprint; phh/TrebleDroid markers (a plain
#              lineage_gsi lacks them and has its own traps)
#   vndk       com.android.vndk.v30 APEX present (our vendor is VNDK 30;
#              vanilla lineage_gsi_arm64 on 24.0 ships 31-34 only) -> FAIL if missing
#   fcm        framework compatibility matrices in the image vs our vendor
#              manifest target-level 5, and vs our vendor's HAL versions
#              (lineage-24.0 dropped FCM 5 and 6; FCM 7 still accepts HIDL
#              keymaster 4.0-1, gatekeeper 1.0, composer 2.1-4, audio 6.0)
#   bpf        netbpfload.rc quarter line (25Q4 / 26Q2 need kernel 5.10 upstream,
#              26Q4 5.15; we run 4.19, so the base must patch the gate; only a boot
#              test proves it). Never remove netbpfload.rc: system_server aborts.
#   bluetooth  bluetooth.profile.* set by the image (A17 defaults profiles OFF)
#   key        platform key vs the running /data (a9-compat.sh; needs the phone)
#
# Usage: gsi-check.sh <system.img> [--no-phone]
# Exit: 0 no FAIL, 1 at least one FAIL, 2 usage.
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"
IMG="${1:-}"; PHONE=1; [ "${2:-}" = "--no-phone" ] && PHONE=0
[ -f "$IMG" ] || { sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

# Our vendor's HALs (measured 2026-10-13 with lshal / vintf manifest on v6, slot a)
VENDOR_LEVEL=5
VENDOR_HALS="hidl:android.hardware.keymaster:4.1 hidl:android.hardware.gatekeeper:1.0 hidl:android.hardware.graphics.composer:2.4 hidl:android.hardware.audio:6.0 hidl:android.hardware.health:2.1 hidl:android.hardware.bluetooth.audio:2.0 aidl:android.hardware.power:2"

FAILS=0
res() { printf "  %-5s %-10s %s\n" "$1" "$2" "$3"; [ "$1" = FAIL ] && FAILS=$((FAILS + 1)); return 0; }
dfs() { debugfs -R "$1" "$IMG" 2>/dev/null; }

echo "== $(basename "$IMG")"
[ "$(od -A n -t x1 -j 1080 -N 2 "$IMG" | tr -d ' ')" = 53ef ] || { echo "not a raw ext4 image (use simg2img first)"; exit 2; }
P=$(dfs "cat /system/build.prop")
g() { printf '%s\n' "$P" | sed -n "s/^$1=//p" | head -1; }
res INFO base "Android $(g ro.build.version.release), sdk $(g ro.build.version.sdk_full)$( [ -z "$(g ro.build.version.sdk_full)" ] && g ro.build.version.sdk), patch $(g ro.build.version.security_patch)"
res INFO base "$(g ro.build.fingerprint)"
PHH=""
dfs "ls /system/bin" | grep -q -w "rw-system.sh" && PHH="$PHH rw-system.sh"
dfs "ls /system/bin" | grep -q -w "phh-su" && PHH="$PHH phh-su"
dfs "ls /system/priv-app" | grep -qi "TrebleApp" && PHH="$PHH TrebleApp"
[ -n "$PHH" ] && res OK base "phh/TrebleDroid markers:$PHH" || res WARN base "no phh/TrebleDroid markers: plain lineage_gsi (no phh vndk.rc / IMS / device fixes)"

APEX=$(dfs "ls /system/system_ext/apex"; dfs "ls /system/apex")
V=$(printf '%s\n' "$APEX" | grep -o 'com\.android\.vndk\.v[0-9]*' | sort -u | sed 's/com.android.vndk.//' | tr '\n' ' ')
case " $V " in *" v30 "*) res OK vndk "VNDK APEXes: $V" ;; *) res FAIL vndk "no com.android.vndk.v30 (have: ${V:-none}): every vendor HAL fails to link" ;; esac

MATS=$(dfs "ls /system/etc/vintf" | grep -o 'compatibility_matrix\.[0-9]*\.xml' | sort -u)
LEVELS=$(printf '%s\n' "$MATS" | sed 's/compatibility_matrix\.\([0-9]*\)\.xml/\1/' | sort -n | tr '\n' ' ')
MIN=$(printf '%s\n' $LEVELS | head -1)
if [ -z "$MIN" ]; then res WARN fcm "no compatibility_matrix.N.xml found in /system/etc/vintf"
elif [ "$MIN" -le "$VENDOR_LEVEL" ]; then res OK fcm "matrices $LEVELS include our vendor level $VENDOR_LEVEL"
else
  res WARN fcm "matrices $LEVELS: none for our vendor level $VENDOR_LEVEL (runtime effect on a GSI unmeasured; boot test on slot B)"
  M=$(dfs "cat /system/etc/vintf/compatibility_matrix.$MIN.xml")
  for h in $VENDOR_HALS; do
    fmt=${h%%:*}; rest=${h#*:}; name=${rest%%:*}; ver=${rest##*:}
    line=$(printf '%s' "$M" | python3 -I -c "
import sys,re
x=sys.stdin.read(); fmt,name,ver=sys.argv[1:4]; ok=False; seen=[]
for f,b in re.findall(r'<hal format=\"(\w+)\"[^>]*>(.*?)</hal>',x,re.S):
    if f!=fmt or re.search(r'<name>([^<]+)</name>',b).group(1)!=name: continue
    for v in re.findall(r'<version>([^<]+)</version>',b):
        seen.append(v)
        if fmt=='aidl':
            lo,_,hi=v.partition('-'); ok|= int(lo)<=int(ver)<=int(hi or lo)
        else:
            mj,_,mn=v.partition('.'); mn_lo,_,mn_hi=mn.partition('-'); vmj,vmn=ver.split('.')
            ok|= mj==vmj and int(mn_lo)<=int(vmn)<=int(mn_hi or mn_lo) or (mj==vmj and int(vmn)>=int(mn_lo) and not mn_hi and False)
print(('OK' if ok else 'WARN')+' '+(','.join(seen) or 'not listed'))
" "$fmt" "$name" "$ver")
    res "${line%% *}" fcm "FCM $MIN: $fmt $name@$ver -> matrix ${line#* }"
  done
fi

Q=$(dfs "cat /system/etc/init/netbpfload.rc" | head -1)
if [ -z "$Q" ]; then res FAIL bpf "no /system/etc/init/netbpfload.rc (system_server's libmemevents aborts without it)"
else
  case "$Q" in
    *"# 2025 4 "*|*"# 2026 "*|*"# 202[7-9] "*) res WARN bpf "netbpfload.rc '$Q': upstream gate needs kernel >= 5.10 (we have 4.19); the base must patch it, boot-test on B" ;;
    *) res OK bpf "netbpfload.rc '$Q'" ;;
  esac
fi

BT=$( { printf '%s\n' "$P"; dfs "cat /system/system_ext/etc/build.prop"; dfs "cat /system/product/etc/build.prop"; } | grep -c '^bluetooth\.profile\..*=true')
[ "$BT" -gt 0 ] && res OK bluetooth "$BT bluetooth.profile.* props set by the image" || res WARN bluetooth "image sets no bluetooth.profile.* (A17 defaults profiles OFF: no A2DP/HFP unless something sets them)"

if [ "$PHONE" = 1 ] && [ "$(a9_adb_state)" = device ]; then
  if "$HERE/a9-compat.sh" "$IMG" >/tmp/a9-gsi-compat.$$ 2>&1; then res OK key "a9-compat: COMPATIBLE with the running /data"
  else res FAIL key "a9-compat: $(grep -m1 REFUSE /tmp/a9-gsi-compat.$$ | sed 's/^REFUSE: //')"; fi
  rm -f /tmp/a9-gsi-compat.$$
else res INFO key "phone not checked (run a9-compat.sh <img> with the phone in Android)"; fi

echo "RESULT: $([ "$FAILS" = 0 ] && echo "no blocking failures (WARNs still need a slot-B boot test)" || echo "$FAILS blocking failure(s)")"
[ "$FAILS" = 0 ]
