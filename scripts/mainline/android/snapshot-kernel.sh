#!/usr/bin/env bash
# Optional A9_BLUETOOTH=1: default-off BT variant; see docs/mainline-bluetooth.md.
# snapshot-kernel.sh -- freeze one consistent Android kernel build for the
# mainline Android images: Image.gz + Android DTB + the null-overlay DTBO +
# the E Ink module closure (a9_eink, msm, tc358767, panel and their depends=),
# with a flat modules.dep / modules.load for Android's modprobe, and
# SHA256SUMS. Offline. The kernel build itself belongs to the kernel agent
# (A9_ANDROID=1 scripts/mainline/kernel/build.sh); this only copies its output,
# so a later rebuild there cannot leave the boot image and the vendor modules
# out of sync.
#
#   snapshot-kernel.sh [--wifi] [build_dir] [out_dir]
#     build_dir default ~/A9-build/mainline/build-7.2.10-android
#     out_dir   default ~/A9-build/mainline/android/kernel-snap
#
# A9_GPU=1 (runs/gpu.md): snapshot the GPU kernel (A9_ANDROID=1 A9_GPU=1
# build.sh: build-7.2.10-android-gpu, DTB sm6115-hisense-hlte556n-android-gpu)
# into kernel-snap-gpu, with a modules.gpu list (the msm closure, for the
# vkms image; gpucc is built in since G1 attempt 1) and
# modules.options (msm.ko separate_gpu_kms=1: the GPU gets its own DRM device,
# so a GPU failure can never hold up the msm KMS card the E Ink driver uses).
set -euo pipefail
WIFI=${A9_WIFI:-0}
[ "${1:-}" != --wifi ] || { WIFI=1; shift; }
case "$WIFI" in 0|1) ;; *) echo "A9_WIFI must be 0 or 1" >&2; exit 2 ;; esac
PD_MAPPER=${A9_WIFI_PD_MAPPER:-kernel}
case "$PD_MAPPER" in kernel|userspace) ;; *) echo "invalid A9_WIFI_PD_MAPPER" >&2; exit 2 ;; esac
[ "$WIFI" = 0 ] || umask 077 # snapshot DTB contains the privately provisioned MAC
[ $# -le 2 ] || { echo "usage: $0 [--wifi] [build_dir] [out_dir]" >&2; exit 2; }
if [ "$WIFI" = 1 ] && { [ "${A9_GPU:-0}" = 1 ] || [ "${A9_BLUETOOTH:-0}" = 1 ] || [ "${A9_THERMAL:-0}" = 1 ]; }; then
  echo "the Wi-Fi snapshot does not combine with the GPU/Bluetooth/thermal variants" >&2; exit 2
fi
HERE=$(cd "$(dirname "$0")" && pwd)
GPU=${A9_GPU:-0}
BT=${A9_BLUETOOTH:-0}
case "$BT" in 0|1) ;; *) echo 'A9_BLUETOOTH must be 0 or 1' >&2; exit 2 ;; esac
BT_SUFFIX=; [ "$BT" = 1 ] && BT_SUFFIX=-bt
TH=${A9_THERMAL:-0}
case "$TH" in 0|1) ;; *) echo 'A9_THERMAL must be 0 or 1' >&2; exit 2 ;; esac
TH_SUFFIX=; [ "$TH" = 1 ] && TH_SUFFIX=-thermal
# integ-1: the variant suffixes compose in build.sh's order (-gpu, -thermal,
# -bt): A9_GPU=1 A9_THERMAL=1 A9_BLUETOOTH=1 = build-7.2.10-android-gpu-thermal-bt
# -> kernel-snap-gpu-thermal-bt. SDTB is the DTB's name INSIDE the snapshot
# (what mkramdisk.sh / build-vendor.sh read): -gpu for a GPU kernel, never -thermal.
ROOTS="dispcc_sm6115 msm panel_hisense_hlte556n_eink tc358767 a9_eink"   # GPU: gpucc is BUILT IN (a9-gpu.config, G1 attempt 1)
if [ "$GPU" = 1 ]; then
  B=${1:-$HOME/A9-build/mainline/build-7.2.10-android-gpu$TH_SUFFIX$BT_SUFFIX}
  O=${2:-$HOME/A9-build/mainline/android/kernel-snap-gpu$TH_SUFFIX$BT_SUFFIX}
  SDTB=sm6115-hisense-hlte556n-android-gpu.dtb
  # the null-overlay DTBO is board-generic; the GPU branch (reviewed) takes the
  # default Android build's, also for the combined variants
  DTBO=${A9_DTBO:-$HOME/A9-build/mainline/out-android/recovery_dtbo.img}
else
  B=${1:-$HOME/A9-build/mainline/build-7.2.10-android$TH_SUFFIX$BT_SUFFIX}
  O=${2:-$HOME/A9-build/mainline/android/kernel-snap$TH_SUFFIX$BT_SUFFIX}
  SDTB=sm6115-hisense-hlte556n-android.dtb
  DTBO=${A9_DTBO:-$HOME/A9-build/mainline/out-android$BT_SUFFIX/recovery_dtbo.img}
  if [ "$WIFI" = 1 ]; then
    # Wi-Fi: own build and snapshot directories; the DTB is built as ...-android-wifi.dtb and kept under
    # the canonical snapshot name; the null overlay is the unchanged board-generic one.
    B=${1:-$HOME/A9-build/mainline/build-7.2.10-android-wifi}
    O=${2:-$HOME/A9-build/mainline/android/kernel-snap-wifi}
    [[ "$O" == *-wifi ]] || { echo "Wi-Fi snapshot output must end in -wifi" >&2; exit 2; }
    DTBO=${A9_DTBO:-$HOME/A9-build/mainline/out-android/recovery_dtbo.img}
  fi
fi
BT_FW=${A9_BT_FIRMWARE:-$HOME/A9-build/mainline/sol-bt/runtime}
# Refuse accidental BT config/snapshot mixing, including a default-off invocation.
if grep -q '^CONFIG_BT=[ym]$' "$B/.config"; then
    [ "$BT" = 1 ] || { echo 'Bluetooth kernel requires A9_BLUETOOTH=1 snapshot' >&2; exit 1; }
elif [ "$BT" = 1 ]; then
    echo 'Bluetooth snapshot requires an opted-in kernel' >&2; exit 1
fi
case "$O" in *a9-assets*) echo "refusing to write under a9-assets" >&2; exit 2 ;; esac
# DTB: the A9_THERMAL=1 kernel build (build dir *-thermal, also *-thermal-bt)
# makes <board DTB>-thermal.dtb (sm6115-hisense-hlte556n-android[-gpu]-thermal.dtb);
# it is snapshotted under the snapshot's normal name $SDTB (mkramdisk.sh
# unchanged) and its origin recorded in dtb.source.
# A thermal DTB is only accepted with a config that has the review-24 gates.
DTBN=${A9_SNAP_DTB:-}
[ "$WIFI" = 0 ] || DTBN=sm6115-hisense-hlte556n-android-wifi.dtb
case "$B" in *-thermal|*-thermal/|*-thermal-bt|*-thermal-bt/) TH=1 ;; esac
[ "$TH" = 1 ] && DTBN=${DTBN:-${SDTB%.dtb}-thermal.dtb}
DTBN=${DTBN:-$SDTB}
for f in "$B/arch/arm64/boot/Image.gz" "$B/arch/arm64/boot/dts/qcom/$DTBN" "$B/.config" "$DTBO"; do
  [ -f "$f" ] || { echo "missing $f" >&2; exit 1; }
done
case "$DTBN" in *-thermal.dtb|*-thermal-adsp.dtb|*-thermal-adsp-d2.dtb)
  for c in CONFIG_THERMAL_CRITICAL_CONFIRM_SAMPLES=3 CONFIG_QCOM_TSENS_REQUIRE_VALID=y CONFIG_THERMAL_EMERGENCY_POWEROFF_DELAY_MS=100; do
    grep -qx "$c" "$B/.config" || { echo "thermal DTB but $B/.config lacks $c" >&2; exit 1; }
  done ;;
esac
case "$DTBO" in *out-android*-thermal/*|*out-android*-thermal-bt/*)
  case "$DTBN" in *-thermal.dtb|*-thermal-adsp.dtb|*-thermal-adsp-d2.dtb) ;;
    *) echo "thermal DTBO with a non-thermal DTB" >&2; exit 1 ;; esac ;;
esac
# a build in progress would give a mixed snapshot (checked against Module.symvers below)
pgrep -f "make .*O=$B( |$)" >/dev/null && { echo "a make is running on $B" >&2; exit 1; }
rm -rf "$O.tmp"; mkdir -p "$O.tmp/modules"
cp "$B/arch/arm64/boot/Image.gz" "$O.tmp/"
cp "$B/arch/arm64/boot/dts/qcom/$DTBN" "$O.tmp/$SDTB"
echo "$DTBN" > "$O.tmp/dtb.source"
cp "$B/.config" "$O.tmp/config"; cp "$DTBO" "$O.tmp/recovery_dtbo.img"
cp "$B/include/config/kernel.release" "$O.tmp/kernel.release"
# LOAD ORDER (eink run 4: msm-mdss never bound without dispcc; the diskless
# a9-eink loader's order): display clock controller, msm, panel, bridge, then
# a9_eink once the msm card exists (a9ml-eink.sh setup waits there).
python3 -I - "$B" "$O.tmp/modules" "$WIFI" "$PD_MAPPER" $ROOTS <<'PY'
import os, shutil, sys
b, dst, wifi, pd, *want = sys.argv[1:]
n = lambda s: s.replace('-', '_')
wifi_want = []
if wifi == '1':
    config = dict(line.strip().split('=', 1) for line in open(os.path.join(b, '.config'))
                  if line.startswith('CONFIG_') and '=' in line)
    seeds = [('QCOM_Q6V5_PAS', 'qcom_q6v5_pas'),
             ('QCOM_RMTFS_MEM', 'rmtfs_mem'), ('QRTR_SMD', 'qrtr_smd'),
             ('ATH10K_SNOC', 'ath10k_snoc')]
    for symbol in ('WLAN', 'CFG80211', 'MAC80211'):
        assert config.get('CONFIG_' + symbol) == 'y', f'{symbol} must be built in'
    if pd == 'kernel':
        seeds.append(('QCOM_PD_MAPPER', 'qcom_pd_mapper'))
    else:
        assert 'CONFIG_QCOM_PD_MAPPER' not in config, 'both PD mapper modes enabled'
    for symbol, module in seeds:
        assert config.get('CONFIG_' + symbol) == 'm', f'{symbol} must be modular'
        wifi_want.append(module)
mods = {}
for root, ds, fs in os.walk(b):
    if '/.tmp' in root or 'modstage' in root: continue
    for f in fs:
        if f.endswith('.ko'): mods[n(f[:-3])] = os.path.join(root, f)
def field(p, k):
    d = open(p, 'rb').read(); i = d.find(k.encode() + b'=')
    return d[i + len(k) + 1:d.index(b'\0', i)].decode() if i >= 0 else ''
# modules may legitimately be newer than Image.gz (a module-only change leaves
# the image untouched); modpost writes Module.symvers after every module of
# the build, so a module newer than it means a build is still running
kimg = os.path.getmtime(os.path.join(b, 'Module.symvers'))
need, todo = [], [n(w) for w in want + wifi_want]
while todo:
    m = todo.pop()
    if m in need: continue
    need.append(m); todo += [n(x) for x in field(mods[m], 'depends').split(',') if x]
vm = {field(mods[m], 'vermagic') for m in need}
assert len(vm) == 1, f'mixed vermagic {vm}'
newer = [m for m in need if os.path.getmtime(mods[m]) > kimg + 300]   # .ko links run just after modpost
assert not newer, f'modules newer than Module.symvers (build in progress?): {newer}'
order = []
def visit(m):
    if m in order: return
    for x in field(mods[m], 'depends').split(','):
        if x: visit(n(x))
    order.append(m)
for w in want: visit(n(w))
display_order = order.copy()
for w in wifi_want: visit(n(w))
for m in order: shutil.copy2(mods[m], os.path.join(dst, os.path.basename(mods[m])))
base = lambda m: os.path.basename(mods[m])
with open(os.path.join(dst, 'modules.dep'), 'w') as f:
    for m in order:
        deps = [n(x) for x in field(mods[m], 'depends').split(',') if x]
        f.write(f"{base(m)}: {' '.join(base(d) for d in deps)}\n".replace(': \n', ':\n'))
open(os.path.join(dst, 'modules.load'), 'w').write(''.join(base(m) + '\n' for m in display_order))
if wifi == '1':
    # Not autoloaded by the display init. The gated Wi-Fi controller owns this.
    order = []
    for w in wifi_want: visit(n(w))
    open(os.path.join(dst, 'modules.wifi'), 'w').write(''.join(base(m) + '\n' for m in order))
for x in ('modules.alias', 'modules.softdep'): open(os.path.join(dst, x), 'w').close()
print(f"  modules: {len(order)} ({', '.join(base(m) for m in order)}), vermagic {vm.pop()}")
if os.environ.get('A9_GPU') == '1':
    assert 'gpucc_sm6115' not in mods, 'gpucc-sm6115.ko in the GPU build: must be built in (CONFIG_SM_GPUCC_6115=y)' 
    gpu = []
    def gvisit(m):
        if m in gpu: return
        for x in field(mods[m], 'depends').split(','):
            if x: gvisit(n(x))
        gpu.append(m)
    gvisit('msm')
    open(os.path.join(dst, 'modules.gpu'), 'w').write(''.join(base(m) + '\n' for m in gpu))
    open(os.path.join(dst, 'modules.options'), 'w').write('msm.ko separate_gpu_kms=1\n')
    print(f"  GPU: gpucc built in; modules.gpu {len(gpu)} ({', '.join(base(m) for m in gpu)}), modules.options msm.ko separate_gpu_kms=1")
PY
if [ "$BT" = 1 ]; then
    python3 -I "$HERE/../bluetooth/kernel-assets.py" --build "$B" --snapshot "$O.tmp" \
        --config "$O.tmp/config" --dtb "$O.tmp/$SDTB" --firmware "$BT_FW"
fi
# frontlight LED driver: external module built against this same build dir
# (build-frontlight-module.sh); same vermagic required. Android only.
FL=${A9_FRONTLIGHT_KO:-$HOME/A9-build/mainline/android/work/frontlight-mod/a9-frontlight.ko}
if [ -f "$FL" ]; then
  python3 -I - "$FL" "$O.tmp/modules" <<'PY'
import os, shutil, sys
ko, dst = sys.argv[1], sys.argv[2]
def field(p, k):
    d = open(p, 'rb').read(); i = d.find(k.encode() + b'=')
    return d[i + len(k) + 1:d.index(b'\0', i)].decode() if i >= 0 else ''
ref = field(os.path.join(dst, 'a9_eink.ko'), 'vermagic')
assert field(ko, 'vermagic') == ref, f"frontlight vermagic {field(ko, 'vermagic')} != {ref}"
deps = [x for x in field(ko, 'depends').split(',') if x]
assert not deps, f'unexpected frontlight module depends: {deps}'
shutil.copy2(ko, dst)
open(os.path.join(dst, 'modules.dep'), 'a').write('a9-frontlight.ko:\n')
open(os.path.join(dst, 'modules.load'), 'a').write('a9-frontlight.ko\n')
print('  + a9-frontlight.ko (same vermagic)')
PY
else
  echo "  (no frontlight module at $FL: build-frontlight-module.sh)"
fi
if [ "$WIFI" = 1 ]; then
  printf '%s\n' "$PD_MAPPER" > "$O.tmp/wifi-mode"
  python3 -I - "$O.tmp" "$PD_MAPPER" <<'PY'
import hashlib, json, pathlib, sys
p = pathlib.Path(sys.argv[1])
dtb = p / 'sm6115-hisense-hlte556n-android.dtb'
assert b'Hisense_HLTE556N\0' in dtb.read_bytes(), 'Wi-Fi DT variant missing'
assert b'qcom/sm6115/hisense/hlte556n/modem.mdt\0' in dtb.read_bytes(), 'MPSS firmware missing'
names = ['Image.gz', 'config', dtb.name, 'modules/modules.wifi']
(p / 'wifi-manifest.json').write_text(json.dumps({
    'version': 1, 'pd_mapper': sys.argv[2],
    'sha256': {name: hashlib.sha256((p / name).read_bytes()).hexdigest() for name in names}
}, sort_keys=True, indent=2) + '\n')
PY
fi
# the Android DTB must carry the frontlight block, in sync with its source
python3 -I - "$O.tmp/$SDTB" <<'PY'
import sys
d = open(sys.argv[1], 'rb').read()
miss = [k for k in ('a9:warm:center', 'a9:white:center', 'a9:warm:edge', 'a9:white:edge') if k.encode() not in d]
assert not miss, f'Android DTB lacks frontlight nodes {miss}'
PY
HERE=$(cd "$(dirname "$0")" && pwd)
want=$(sha256sum "$HERE/../frontlight/a9-frontlight.dtsi" | cut -c1-64)
grep -q "sha256 $want" "$HERE/sm6115-hisense-hlte556n-android.dts" \
  || { echo "frontlight block in the Android DTS is out of sync with scripts/mainline/frontlight/a9-frontlight.dtsi" >&2; exit 1; }
(cd "$O.tmp" && find . -type f ! -name SHA256SUMS | LC_ALL=C sort | xargs sha256sum > SHA256SUMS)
rm -rf "$O"; mv "$O.tmp" "$O"
echo "kernel snapshot: $O ($(cat "$O/kernel.release"), Image.gz $(sha256sum "$O/Image.gz" | cut -c1-12))"
