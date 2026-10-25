#!/usr/bin/env bash
#
# a9-compat.sh — will this system image boot on the CURRENT /data? Read-only.
#
# /data is shared by both slots, and it is tied to the platform key it was set
# up with. Booting an image with a different platform key needs a /data wipe,
# and the phone is in daily use, so that is refused (A6L harvest §4,
# docs/slots-and-promotion.md "Platform keys").
#
# Compares IMAGE against the running phone (default) or against a second image:
#   platform key  the platform signer whose certificate is in framework-res.apk's
#                 CERT.RSA (= the key /data is bound to). Different -> REFUSE.
#   SDK           ro.build.version.sdk_full. Lower than the reference -> REFUSE
#                 (framework downgrade over existing /data). Higher -> WARN.
#   security patch ro.build.version.security_patch. Older -> REFUSE (keystore
#                 rollback risk, inferred). Newer -> WARN: it may be one-way for
#                 the other slot on the shared /data (inferred, unmeasured).
#   extra platform signers (our SystemUI/a9service key) are listed, not judged.
#
# Usage:
#   a9-compat.sh <image>                 # vs the running phone (adb, needs su)
#   a9-compat.sh <image> --against <img> # vs another image, no phone needed
#
# Exit: 0 compatible (maybe with WARN lines), 1 REFUSE, 2 usage/read error.
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"

IMG="${1:-}"; AGAINST=""
[ "${2:-}" = "--against" ] && AGAINST="${3:-}"
[ -f "$IMG" ] || { echo "usage: $0 <system.img> [--against <system.img>]" >&2; exit 2; }
[ -z "${2:-}" ] || [ -f "$AGAINST" ] || { echo "usage: $0 <system.img> [--against <system.img>]" >&2; exit 2; }

W=$(mktemp -d); trap 'rm -rf "$W"' EXIT

# collect <dir> <image|"phone">: writes mac.xml, fr.rsa, props into <dir>
collect() {
  local d="$1" src="$2"; mkdir -p "$d"
  if [ "$src" = phone ]; then
    [ "$(a9_adb_state)" = device ] || { echo "ERROR: phone not in Android (adb device); use --against <img>" >&2; return 1; }
    a9_su 'cat /system/etc/selinux/plat_mac_permissions.xml' > "$d/mac.xml"
    "$ADB" pull /system/framework/framework-res.apk "$d/fr.apk" >/dev/null 2>&1 || return 1
    "$ADB" shell 'for p in ro.build.version.sdk_full ro.build.version.sdk ro.build.version.security_patch; do echo "$p=$(getprop $p)"; done; echo "ro.build.fingerprint=$(getprop ro.system.build.fingerprint)"' </dev/null | tr -d '\r' > "$d/props"
  else
    debugfs -R "cat /system/etc/selinux/plat_mac_permissions.xml" "$src" 2>/dev/null > "$d/mac.xml"
    debugfs -R "dump /system/framework/framework-res.apk $d/fr.apk" "$src" >/dev/null 2>&1
    debugfs -R "cat /system/build.prop" "$src" 2>/dev/null \
      | grep -E '^ro\.build\.(version\.(sdk_full|sdk|security_patch)|fingerprint)=' > "$d/props"
  fi
  unzip -p "$d/fr.apk" 'META-INF/*.RSA' 'META-INF/*.EC' 'META-INF/*.DSA' > "$d/fr.rsa" 2>/dev/null
  [ -s "$d/mac.xml" ] && [ -s "$d/fr.rsa" ] && [ -s "$d/props" ] \
    || { echo "ERROR: could not read mac_permissions / framework-res cert / build.prop from $src" >&2; return 1; }
}

collect "$W/new" "$IMG" || exit 2
if [ -n "$AGAINST" ]; then collect "$W/ref" "$AGAINST" || exit 2; REFNAME="$(basename "$AGAINST")"
else collect "$W/ref" phone || exit 2; REFNAME="running phone (slot $("$ADB" shell getprop ro.boot.slot_suffix </dev/null | tr -d '\r'))"; fi

python3 -I - "$W/new" "$W/ref" "$(basename "$IMG")" "$REFNAME" <<'PY'
import sys, re, hashlib
new, ref, newname, refname = sys.argv[1:5]

def load(d):
    x = open(d + '/mac.xml').read(); rsa = open(d + '/fr.rsa', 'rb').read()
    plat = []
    for sig, body in re.findall(r'<signer signature="([0-9a-fA-F]+)"\s*>(.*?)</signer>', x, re.S):
        if 'value="platform"' in body:
            der = bytes.fromhex(sig)
            plat.append((hashlib.sha256(der).hexdigest(), der in rsa))
    base = [h for h, inrsa in plat if inrsa]
    props = dict(l.strip().split('=', 1) for l in open(d + '/props') if '=' in l)
    return base[0] if len(base) == 1 else None, [h for h, inrsa in plat if not inrsa], props

def ver(p):
    v = p.get('ro.build.version.sdk_full') or p.get('ro.build.version.sdk', '0')
    return tuple(int(x) for x in re.findall(r'\d+', v)) or (0,)

nk, nextra, np_ = load(new); rk, rextra, rp = load(ref)
refuse, warn = [], []
print(f"image:     {newname}\n  {np_.get('ro.build.fingerprint','?')}")
print(f"reference: {refname}\n  {rp.get('ro.build.fingerprint','?')}")
print(f"platform key (framework-res): image {nk[:16] if nk else '??'}  reference {rk[:16] if rk else '??'}")
print(f"extra platform signers:       image {[h[:8] for h in nextra]}  reference {[h[:8] for h in rextra]}")
if not nk or not rk:
    refuse.append("could not identify the framework-res platform key on one side")
elif nk != rk:
    refuse.append(f"platform key differs ({nk[:16]} vs {rk[:16]}): this image needs a /data wipe. Back up first (twrp-backup.sh); the backup restores only on this platform key.")
ns, rs = ver(np_), ver(rp)
print(f"SDK: image {'.'.join(map(str, ns))}  reference {'.'.join(map(str, rs))}")
if ns < rs: refuse.append("SDK downgrade over the existing /data")
elif ns > rs: warn.append("SDK upgrade: /data gets migrated; the other slot (older SDK) may not boot on it afterwards (inferred)")
npat, rpat = np_.get('ro.build.version.security_patch', ''), rp.get('ro.build.version.security_patch', '')
print(f"security patch: image {npat}  reference {rpat}")
if npat and rpat and npat < rpat: refuse.append("security-patch downgrade (keystore rollback risk, inferred)")
elif npat and rpat and npat > rpat: warn.append("newer security patch than the reference: may be one-way for the other slot on shared /data (inferred, unmeasured)")
for w in warn: print("WARN:", w)
for r in refuse: print("REFUSE:", r)
print("RESULT:", "REFUSE" if refuse else "COMPATIBLE")
sys.exit(1 if refuse else 0)
PY
