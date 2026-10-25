#!/usr/bin/env bash
# build-stage-a.sh -- build the ES9218 stage-A/B image pair + module bundle,
# offline (runs/audio-stage-plan.md section 4). No adb/fastboot/edl: they are
# shimmed to fail. Never writes ~/a9-assets (reads the stock boot.img only, via
# mkramdisk.sh). Needs ~5 GB on / (refuses below 7 GiB free).
#
#   build-stage-a.sh [--dry-run] [step...]     steps: kernel frontlight snapshot
#                                              bundle vendor boot verify offline
#   T=${A9_AUDIO_STAGE:-~/A9-build/mainline/sol-audio/stage-a}
#
# Result (the tuple android-boot.sh and audio-stage.sh take):
#   $T/android/out/a9-android-eink-boot.img   audio kernel + ESS-enabled, ADSP-disabled DTB
#   $T/android/out/vendor_b-eink.img          E Ink vendor with THIS kernel's modules
#                                             (modules.load = E Ink + frontlight only;
#                                             no sound module is shipped in the vendor)
#   $T/android/audio-bundle/                  sound core + codec closure, pushed at
#                                             stage time by audio-stage.sh
# Gates stay CLOSED (no waveform/template, no --open-gates). Steps are recorded
# in $T/steps.log with the git HEAD; a finished step is skipped for the same HEAD.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
AUD=$(cd "$HERE/.." && pwd)
REPO=$(cd "$HERE/../../../.." && pwd)
T=${A9_AUDIO_STAGE:-$HOME/A9-build/mainline/sol-audio/stage-a}
AW=$T/android
BUILD=$T/build                                   # build-kernel.py --work (private source + objects)
OBJ=$BUILD/objects
VAR=out-audio-ess-enabled                        # --es9218 --enable-es9218, no --enable-adsp
DTB=$BUILD/$VAR/sm6115-hisense-hlte556n-android-audio-ess-enabled.dtb
SNAP=$AW/kernel-snap-audio
BUNDLE=$AW/audio-bundle
DTBO=${A9_DTBO:-$HOME/A9-build/mainline/out-android/recovery_dtbo.img}   # the null overlay = dtbo_b
NIXSH=$REPO/scripts/mainline/kernel/shell.nix
DRY=0; STEPS=()
for a in "$@"; do case $a in --dry-run) DRY=1;; kernel|frontlight|snapshot|bundle|vendor|boot|verify|offline) STEPS+=("$a");; *) sed -n '2,20p' "$0"; exit 64;; esac; done
[ ${#STEPS[@]} -gt 0 ] || STEPS=(kernel frontlight snapshot bundle vendor boot verify offline)
case "$T" in *a9-assets*|"$REPO"*) echo "refusing: T must be a private local build dir" >&2; exit 2;; esac
HEAD=$(git -C "$REPO" rev-parse HEAD)
[ -z "$(git -C "$REPO" status --porcelain --untracked-files=no)" ] || { echo "dirty worktree: commit first (the receipts bind HEAD)" >&2; exit 2; }
free=$(df --output=avail -B1 / | tail -n 1)
echo "stage-A build: T=$T HEAD=${HEAD:0:12} free=$((free >> 30)) GiB, need ~5 GiB (BT pair: 4.7 GB)"
[ "$free" -ge $((7 << 30)) ] || { echo "refusing: less than 7 GiB free on /" >&2; exit 2; }
# offline boundary: device tools fail loudly if anything below reaches for them
SHIM=$T/offline-bin; mkdir -p "$SHIM" "$T/logs"
for n in adb fastboot edl curl wget; do printf '#!/bin/sh\necho "stage-A build: %s is prohibited offline" >&2\nexit 99\n' "$n" > "$SHIM/$n"; chmod 755 "$SHIM/$n"; done
export PATH=$SHIM:$PATH GIT_ALLOW_PROTOCOL=file GIT_TERMINAL_PROMPT=0 LC_ALL=C
unset A9_MAINLINE A9_ANDROID A9_ANDROID_KERNEL A9_ANDROID_DTB A9_BLUETOOTH A9_WIFI A9_THERMAL A9_HEALTH 2>/dev/null || true

step() {   # step <name> <command...>
  local name=$1; shift
  [[ " ${STEPS[*]} " == *" $name "* ]] || return 0
  if grep -qx "$name $HEAD 0" "$T/steps.log" 2>/dev/null; then echo "ALREADY $name"; return 0; fi
  echo "START $name: $*"
  [ $DRY = 1 ] && return 0
  if "$@" > "$T/logs/$name.log" 2>&1; then echo "$name $HEAD 0" >> "$T/steps.log"; echo "END $name 0"
  else r=$?; echo "$name $HEAD $r" >> "$T/steps.log"; tail -n 25 "$T/logs/$name.log"; echo "END $name $r"; exit "$r"; fi
}

k_kernel() {
  nix-shell "$NIXSH" --run "A9_AUDIO=1 python3 '$AUD/build-kernel.py' --es9218 --enable-es9218 --work '$BUILD' --jobs ${J:-8}"
}
k_frontlight() {
  A9_KSRC=$BUILD/linux-7.2.10 A9_ANDROID=$AW bash "$REPO/scripts/mainline/android/build-frontlight-module.sh" "$OBJ"
}
k_snapshot() {
  A9_DTBO=$DTBO A9_FRONTLIGHT_KO=$AW/work/frontlight-mod/a9-frontlight.ko \
    bash "$REPO/scripts/mainline/android/snapshot-kernel.sh" "$OBJ" "$SNAP"
}
k_bundle() {   # the codec's depends= closure, from the SAME objects tree as the snapshot
  python3 -I - "$OBJ" "$BUNDLE" "$SNAP" <<'PY'
import hashlib, os, shutil, sys
obj, out, snap = sys.argv[1:4]
n = lambda s: s.replace('-', '_')
mods = {}
for root, ds, fs in os.walk(obj):
    for f in fs:
        if f.endswith('.ko'): mods[n(f[:-3])] = os.path.join(root, f)
def field(p, k):
    d = open(p, 'rb').read(); i = d.find(b'\0' + k.encode() + b'=')
    return d[i + len(k) + 2:d.index(b'\0', i + 1)].decode() if i >= 0 else ''
order = []
def visit(m):
    if m in order: return
    for x in field(mods[m], 'depends').split(','):
        if x: visit(n(x))
    order.append(m)
visit('snd_soc_es9218_a9')
assert order[-1] == 'snd_soc_es9218_a9', order
bad = [m for m in order if not m.startswith('snd')]
assert not bad, f'unexpected codec closure members {bad}'
vm = {field(mods[m], 'vermagic') for m in order} | {field(os.path.join(snap, 'modules', 'a9_eink.ko'), 'vermagic')}
assert len(vm) == 1, f'vermagic differs from the snapshot: {vm}'
assert b'parm=bringup:' in open(mods['snd_soc_es9218_a9'], 'rb').read(), 'codec lacks the bringup parameter (old driver?)'
shutil.rmtree(out, ignore_errors=True); os.makedirs(out)
for m in order: shutil.copy2(mods[m], out)
names = [os.path.basename(mods[m]) for m in order]
open(os.path.join(out, 'modules.order'), 'w').write(''.join(x + '\n' for x in names))
shutil.copy2(os.path.join(snap, 'kernel.release'), os.path.join(out, 'kernel.release'))
img = hashlib.sha256(open(os.path.join(snap, 'Image.gz'), 'rb').read()).hexdigest()
open(os.path.join(out, 'kernel-image.sha256'), 'w').write(img + '\n')
files = names + ['modules.order', 'kernel.release', 'kernel-image.sha256']
open(os.path.join(out, 'SHA256SUMS'), 'w').write(''.join(
    f'{hashlib.sha256(open(os.path.join(out, f), "rb").read()).hexdigest()}  {f}\n' for f in files))
print('bundle:', ' '.join(names), 'vermagic', vm.pop(), 'kernel', img[:12])
PY
}
k_vendor() {
  mkdir -p "$AW"; [ -e "$AW/dl" ] || ln -s "$HOME/A9-build/mainline/android/dl" "$AW/dl"
  mkdir -p "$AW/out" "$AW/work"
  A9_ANDROID=$AW bash "$REPO/scripts/mainline/android/build-vendor.sh" --eink "$SNAP"
}
k_boot() {
  A9_MAINLINE=$T A9_ANDROID_KERNEL=$SNAP A9_ANDROID_DTB=$DTB bash "$REPO/scripts/mainline/android/mkramdisk.sh" --eink --check
}
k_verify() {
  local u; u=$(mktemp -d "$T/unpack.XXXX")
  unpack_bootimg --boot_img "$AW/out/a9-android-eink-boot.img" --out "$u" >/dev/null
  cmp "$u/kernel" "$SNAP/Image.gz"; cmp "$u/dtb" "$DTB"; cmp "$u/recovery_dtbo" "$DTBO"
  echo "boot: kernel = snapshot, dtb = $VAR, recovery_dtbo = null overlay"
  [ "$(debugfs -R 'cat /lib/modules/kernel-image.sha256' "$AW/out/vendor_b-eink.img" 2>/dev/null | tr -d ' \n')" = "$(tr -d ' \n' < "$BUNDLE/kernel-image.sha256")" ]
  echo "vendor kernel-image.sha256 = bundle = boot kernel"
  if debugfs -R 'cat /lib/modules/modules.load' "$AW/out/vendor_b-eink.img" 2>/dev/null | grep -Eq '^(snd|q6|qcom_q6v5|apr|pinctrl-.*lpass)'; then
    echo "vendor modules.load names a sound/ADSP module" >&2; return 1; fi
  if debugfs -R 'ls -p /lib/modules' "$AW/out/vendor_b-eink.img" 2>/dev/null | grep -Eq '/(snd|q6|qcom_q6v5_pas)[^/]*\.ko/'; then
    echo "vendor ships a sound/ADSP module (the bundle is pushed at stage time instead)" >&2; return 1; fi
  echo "vendor: no sound/ADSP module shipped or loaded"
  nix-shell "$NIXSH" --run "dtc -q -I dtb -O dts '$DTB'" > "$u/dt.dts"
  python3 -I - "$u/dt.dts" <<'PY'
import re, sys
s = open(sys.argv[1]).read()
def node(name):
    i = s.index(name + ' {'); d = 0
    for j in range(i, len(s)):
        d += (s[j] == '{') - (s[j] == '}')
        if d == 0 and s[j] == '}': return s[i:j]
    raise SystemExit('unterminated ' + name)
c = node('audio-codec@48')
assert 'status = "okay"' in c and 'hisense,a9-es9218' in c, 'codec node not enabled'
a = node('remoteproc@a400000')
assert 'status = "disabled"' in a, 'ADSP not disabled'
print('dtb: audio-codec@48 okay (hisense,a9-es9218), remoteproc@a400000 disabled')
PY
  rm -rf "$u"
}
k_offline() {
  bash "$REPO/scripts/mainline/android/android-boot.sh" --offline --eink \
    --boot "$AW/out/a9-android-eink-boot.img" --vendor "$AW/out/vendor_b-eink.img"
}
step kernel k_kernel
step frontlight k_frontlight
step snapshot k_snapshot
step bundle k_bundle
step vendor k_vendor
step boot k_boot
step verify k_verify
step offline k_offline
[ $DRY = 1 ] && { echo "dry run: nothing built"; exit 0; }
echo "STAGE-A TUPLE"
(cd "$AW" && sha256sum out/a9-android-eink-boot.img out/vendor_b-eink.img out/vendor_b-eink.sparse.img kernel-snap-audio/Image.gz audio-bundle/snd-soc-es9218-a9.ko) | tee "$T/tuple.txt"
