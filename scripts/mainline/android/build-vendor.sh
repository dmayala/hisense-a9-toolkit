#!/usr/bin/env bash
# Optional A9_BLUETOOTH=1: default-off BT variant; see docs/mainline-bluetooth.md.
# build-vendor.sh -- assemble the mainline vendor_b image from the Android 16
# QPR2 Cuttlefish arm64 vendor (CI build 15581820, target files) plus our own
# A9 files, then pack it with pack-vendor.sh. Offline; no phone.
# docs/mainline-android.md section 3.
#
#   scripts/mainline/android/build-vendor.sh [--wifi] [--eink] [--gpu] [--sensors] [kernel_snapshot_dir]
#
# --eink (phase 2): also ship the E Ink module closure from a kernel snapshot
# (snapshot-kernel.sh; default $AW/kernel-snap, must be the SAME kernel as the
# boot image), set ro.sf.lcd_density for 824x1648, output vendor_b-eink.img.
# The phase-2 rc/script are in every vendor but inert without
# androidboot.a9ml.eink=1.
#
# --gpu (opt-in, runs/gpu.md): Adreno 610 + Mesa freedreno. Needs a GPU kernel
# snapshot (A9_GPU=1 snapshot-kernel.sh; default $AW/kernel-snap-gpu: gpucc +
# the msm closure, modules.gpu, modules.options) - the vendor then always
# carries /vendor/lib/modules, also without --eink - plus the Mesa build
# (gpu/build-mesa.sh, $GW/out/mesa), a9-gpu-probe and the staged firmware
# (gpu/stage-firmware.sh, LOCAL ONLY). Adds ro.vendor.a9ml.gpu=1,
# init.a9ml-gpu.rc and a9ml-gpu.sh; the EGL choice is made at boot (mesa only
# if the probe renders, else today's ANGLE). Output name gets "-gpu".
#
# Inputs (local disk): $AW/dl/cf_arm64_x/tf/VENDOR (unzipped target files),
# the A9 files next to this script (vendor/), the E Ink compat shim
# (scripts/mainline/eink-compat, built with the NDK), ~/.android/adbkey.pub.
# Output: $AW/out/vendor_b.img (+ .sparse.img, .sha256), staging in
# $AW/work/vendor-stage.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# R4 combines GPU/BT/sensors with Wi-Fi through an image merge, rather than
# the mutually exclusive flags below. Refresh its scripts without losing that
# composition: build-vendor.sh --refresh-integrated [--out DIR] [--overlay DIR] [--gnss].
if [ "${1:-}" = --refresh-integrated ]; then
  shift
  exec python3 -I "$HERE/refresh-integrated-vendor.py" "$@"
fi
REPO=$(cd "$HERE/../../.." && pwd)
AW=${A9_ANDROID:-$HOME/A9-build/mainline/android}
CF=$AW/dl/cf_arm64_x/tf/VENDOR
EINK=0 GPU=0 SENSORS=0 WIFI=${A9_WIFI:-0} SNAP=
while [ $# -gt 0 ]; do
  case "$1" in
    --wifi) WIFI=1 ;;
    --eink) EINK=1 ;;
    --gpu) GPU=1 ;;
    --sensors) SENSORS=1 ;;
    --snapshot) [ $# -ge 2 ] || { echo "--snapshot needs a directory" >&2; exit 2; }; SNAP=$2; shift ;;
    -*) echo "usage: $0 [--wifi] [--eink] [--gpu] [--sensors] [snapshot]" >&2; exit 2 ;;
    *) [ -z "$SNAP" ] || { echo "only one snapshot directory is allowed" >&2; exit 2; }; SNAP=$1 ;;
  esac
  shift
done
case "$WIFI" in 0|1) ;; *) echo "A9_WIFI must be 0 or 1" >&2; exit 2 ;; esac
[ "$WIFI" = 0 ] || umask 077 # resulting Wi-Fi images contain private EFS seeds
WIFI_ANDROID=${A9_WIFI_ANDROID:-0}
case "$WIFI_ANDROID" in 0|1) ;; *) echo "A9_WIFI_ANDROID must be 0 or 1" >&2; exit 2 ;; esac
[ "$WIFI_ANDROID" = 0 ] || [ "$WIFI" = 1 ] || { echo "A9_WIFI_ANDROID requires --wifi" >&2; exit 2; }
# Integrated demo session (docs: runs/wifi-android-plan.md): boot-time pre-authorised controller
# session. Needs A9_WIFI_ANDROID=1 and, because building it IS the human authorisation, explicit
# A9_WIFI_SESSION_AUTHORIZE / A9_WIFI_SESSION_ACCEPT_RISK / A9_WIFI_IDENTITY_SHA256 /
# A9_WIFI_PRELIMINARY_SHA256 (optional A9_WIFI_SESSION_SECONDS 600..14400, A9_WIFI_COUNTRY XX).
WIFI_SESSION=${A9_WIFI_SESSION:-0}
case "$WIFI_SESSION" in 0|1) ;; *) echo "A9_WIFI_SESSION must be 0 or 1" >&2; exit 2 ;; esac
[ "$WIFI_SESSION" = 0 ] || [ "$WIFI_ANDROID" = 1 ] || { echo "A9_WIFI_SESSION requires A9_WIFI_ANDROID=1" >&2; exit 2; }
PD_MAPPER=${A9_WIFI_PD_MAPPER:-kernel}
case "$PD_MAPPER" in kernel|userspace) ;; *) echo "invalid A9_WIFI_PD_MAPPER" >&2; exit 2 ;; esac
BT=${A9_BLUETOOTH:-0}
case "$BT" in 0|1) ;; *) echo 'A9_BLUETOOTH must be 0 or 1' >&2; exit 2 ;; esac
BT_SUFFIX=; [ "$BT" = 1 ] && BT_SUFFIX=-bt
SUFFIX=; [ "$WIFI" = 0 ] || SUFFIX=-wifi
if [ "$WIFI" = 1 ] && { [ "$GPU" = 1 ] || [ "$BT" = 1 ]; }; then echo "--wifi does not combine with --gpu or A9_BLUETOOTH" >&2; exit 2; fi
# integ-1: default snapshot = snapshot-kernel.sh's default for the same variant
[ -n "$SNAP" ] || { SNAP=$AW/kernel-snap$SUFFIX; [ $GPU = 1 ] && SNAP=$AW/kernel-snap-gpu; SNAP=$SNAP$BT_SUFFIX; }
GW=${A9_GPU_WORK:-$HOME/A9-build/mainline/gpu}
ST=$AW/work/vendor-stage$SUFFIX
FSCONFIG=$AW/work/vendor-fsconfig$SUFFIX.txt
PACKOUT=$AW/work/vendor-out$SUFFIX
NAME=vendor_b; [ $EINK = 1 ] && NAME=vendor_b-eink; [ $GPU = 1 ] && NAME=$NAME-gpu
[ "$WIFI" = 0 ] || NAME=$NAME-wifi
[ "$WIFI_ANDROID" = 0 ] || NAME=$NAME-android
[ "$WIFI_SESSION" = 0 ] || NAME=$NAME-session
[ "${A9_WIFI_SESSION_INERT:-0}" = 0 ] || NAME=$NAME-inert
if [ "$BT" = 1 ]; then
    [ "$EINK" = 1 ] || { echo 'Bluetooth vendor requires --eink and matching opted-in snapshot' >&2; exit 2; }
    : "${A9_BT_HAL_BUNDLE:?A9_BLUETOOTH=1 requires a separately built Linux HCI_USER HAL bundle}"
    BT_FW=${A9_BT_FIRMWARE:-$HOME/A9-build/mainline/sol-bt/runtime}
    python3 -I "$HERE/../bluetooth/firmware.py" check --directory "$BT_FW"
    [ "$(cat "$SNAP/bluetooth.enabled" 2>/dev/null)" = 1 ] || { echo 'snapshot lacks validated Bluetooth assets' >&2; exit 1; }
    NAME=$NAME-bt
    ST=$ST-bt
fi
if [ "$BT" = 0 ] && [ -f "$SNAP/bluetooth.enabled" ]; then
    echo 'Bluetooth snapshot requires A9_BLUETOOTH=1; default build stays off' >&2; exit 1
fi
# Opt-in HALs (sensors worktree, runs/sensors.md; default off = unchanged image):
#   A9_HEALTH=real     Health HAL reporting the real PMI632 values instead of
#                      Cuttlefish's fixed 85 % / charging / 25 C (health/)
#   A9_THERMAL_HAL=1   thermal HAL reading tsens zones, PM6125 thermistors and
#                      the battery instead of the example HAL's fixed values
#                      (thermal-hal/; built with the NDK)
HEALTH=${A9_HEALTH:-cf} THERMAL_HAL=${A9_THERMAL_HAL:-0}
case "$HEALTH" in cf|real) ;; *) echo "A9_HEALTH must be cf or real" >&2; exit 2 ;; esac
case "$THERMAL_HAL" in 0|1) ;; *) echo "A9_THERMAL_HAL must be 0 or 1" >&2; exit 2 ;; esac
[ "$HEALTH" = real ] && NAME=$NAME-health
[ "$THERMAL_HAL" = 1 ] && NAME=$NAME-thermal
# --sensors (opt-in, default off; runs/sensors-s2c-prep.md, review 79k C1): the ADSP sensors
# service, its registry listener and the sensors HAL, from a hash-pinned stage made by
# scripts/mainline/sensors/s2c-stage.py (A9_SENSORS_STAGE; LOCAL ONLY, it holds the phone's
# registry seed). Needs --eink (the sensors modules pair with the snapshot's kernel). Adds
# ro.vendor.a9ml.sensors=1; persist.a9.sensors=1 only if the stage was made with --test-switch.
if [ $SENSORS = 1 ]; then
  [ $EINK = 1 ] || { echo "--sensors needs --eink (kernel-paired modules)" >&2; exit 2; }
  : "${A9_SENSORS_STAGE:?--sensors needs A9_SENSORS_STAGE=<s2c-stage.py output>}"
  [ -f "$A9_SENSORS_STAGE/stage.json" ] || { echo "A9_SENSORS_STAGE has no stage.json" >&2; exit 1; }
  NAME=$NAME-sensors
fi
case "$AW" in *a9-assets*) echo "refusing to build under a9-assets" >&2; exit 2 ;; esac
[ -f "$CF/build.prop" ] || { echo "missing Cuttlefish vendor tree $CF (see docs, section 3)" >&2; exit 1; }
grep -q '^ro.vendor.build.version.incremental=15581820$' "$CF/build.prop" || { echo "unexpected Cuttlefish build" >&2; exit 1; }

rm -rf "$ST"; mkdir -p "$ST"
# --- base: Cuttlefish vendor, minus what is host-specific or writes storage
cp -a "$CF/." "$ST/"
rm -rf "$ST/apex" "$ST/priv-app" "$ST/overlay" "$ST/framework" "$ST/lib/modules" "$ST/odm" \
       "$ST/lib64/egl" "$ST/etc/fstab.cf."* "$ST/etc/ueventd.rc"
mkdir -p "$ST/apex" "$ST/odm"
# init: only boringssl self test survives; cf init / graphics / trusty / biometrics / serial log go
find "$ST/etc/init" -maxdepth 1 -type f ! -name 'boringssl_self_test.rc' -delete
rm -f "$ST/bin/hw/"* "$ST/bin/socket_vsock_proxy" "$ST/bin/cuttlefish_sensor_injection" "$ST/bin/dlkm_loader"
rm -f "$ST/etc/vintf/manifest/face-default.xml" "$ST/etc/vintf/manifest/fingerprint-default.xml"
# feature XMLs: keep the phone core only (a declared feature without a HAL makes services wait)
find "$ST/etc/permissions" -type f ! -name 'handheld_core_hardware.xml' \
     ! -name 'android.hardware.touchscreen.multitouch.distinct.xml' ! -name 'aosp_excluded_hardware.xml' \
     ! -name 'android.hardware.wifi.xml' ! -name 'android.hardware.wifi.direct.xml' -delete
# wifi + wifi.direct features are kept although there is no Wi-Fi HAL yet: the
# GSI's resources set config_enableWifiDisplay, and DisplayManagerService then
# throws "WiFi display was requested, but there is no WiFi Direct feature" on
# android.display, killing system_server (MEASURED, boot 2, 2026-10-15). The
# stock vendor declares both features.

# --- vendor APEXes: software HALs only, one variant per multi-install name.
# Omitted on purpose: boot (writes misc), rebootescrow (pmem), every *remote /
# cf_remote / trusty / vsock / hvc HAL, light (vsock), bt, rild, wifi, gnss,
# nfc, uwb, identity, oemlock, confirmationui, cameras, ranchu composer.
APEXES="com.android.hardware.graphics.composer.drm_hwcomposer com.google.cf.gralloc com.google.cf.vulkan
 com.android.hardware.keymint.rust_nonsecure com.android.hardware.gatekeeper.nonsecure
 com.android.hardware.security.authgraph com.google.cf.health com.google.cf.health.storage
 com.android.hardware.usb com.android.hardware.vibrator com.android.hardware.power
 com.android.hardware.memtrack com.android.hardware.thermal com.android.hardware.authsecret
 com.android.hardware.audio"
# audio: without an audio.core IModule + effect IFactory, AudioFlinger::onFirstRef
# dereferences a null HAL factory and audioserver SIGSEGVs in a loop (MEASURED,
# first boot 2026-10-15). The AIDL example HAL serves stub/r_submix modules.
[ "$HEALTH" = real ] && APEXES=${APEXES/com.google.cf.health /}
[ "$THERMAL_HAL" = 1 ] && APEXES=${APEXES/com.android.hardware.thermal /}
for a in $APEXES; do cp "$CF/apex/$a.apex" "$ST/apex/"; done

# --- A9 files
install -D -m 0644 "$HERE/vendor/etc/fstab.hlte556n" "$ST/etc/fstab.hlte556n"
install -D -m 0644 "$HERE/vendor/etc/ueventd.rc" "$ST/etc/ueventd.rc"
install -D -m 0644 "$HERE/vendor/etc/init/hw/init.hlte556n.rc" "$ST/etc/init/hw/init.hlte556n.rc"
printf '\n# A9 early statistics service\n' >> "$ST/etc/init/hw/init.hlte556n.rc"
cat "$HERE/early-stats.rc" >> "$ST/etc/init/hw/init.hlte556n.rc"
printf '# End A9 early statistics service\n' >> "$ST/etc/init/hw/init.hlte556n.rc"
install -D -m 0644 "$HERE/vendor/etc/init/init.a9ml-eink.rc" "$ST/etc/init/init.a9ml-eink.rc"
# InputReader matches the gpio-keys device name. The merged platform file
# contexts label /vendor/usr/keylayout/*.kl as vendor_keylayout_file.
install -D -m 0644 "$HERE/vendor/usr/keylayout/gpio-keys.kl" "$ST/usr/keylayout/gpio-keys.kl"
if [ $EINK = 1 ] || [ $GPU = 1 ] || [ "$WIFI" = 1 ]; then
  [ -f "$SNAP/modules/modules.load" ] && (cd "$SNAP" && sha256sum -c --quiet SHA256SUMS) || { echo "bad kernel snapshot $SNAP" >&2; exit 1; }
  if [ "$WIFI" = 1 ]; then
    [ -s "$SNAP/modules/modules.wifi" ] && [ -s "$SNAP/wifi-manifest.json" ] \
      && [ "$(cat "$SNAP/wifi-mode")" = "$PD_MAPPER" ] \
      || { echo "Wi-Fi snapshot missing or PD mapper mode mismatch" >&2; exit 1; }
    python3 -I - "$SNAP" "$PD_MAPPER" <<'PY'
import hashlib, json, pathlib, sys
p = pathlib.Path(sys.argv[1]); manifest = json.loads((p / 'wifi-manifest.json').read_text())
assert manifest['version'] == 1 and manifest['pd_mapper'] == sys.argv[2], 'Wi-Fi snapshot mode mismatch'
for name in ('Image.gz', 'config', 'sm6115-hisense-hlte556n-android.dtb', 'modules/modules.wifi'):
    assert hashlib.sha256((p / name).read_bytes()).hexdigest() == manifest['sha256'][name], name
PY
  else
    [ ! -e "$SNAP/wifi-mode" ] || { echo "Wi-Fi snapshot requires --wifi" >&2; exit 1; }
  fi
  mkdir -p "$ST/lib/modules"; cp "$SNAP"/modules/* "$ST/lib/modules/"
  # the kernel these modules belong to (android-boot.sh checks the boot image against it)
  sha256sum "$SNAP/Image.gz" | awk '{print $1}' > "$ST/lib/modules/kernel-image.sha256"
  echo "  modules from $SNAP ($(cat "$SNAP/kernel.release")): $(wc -l < "$SNAP/modules/modules.load")"
fi
for f in "$HERE"/vendor/bin/*.sh; do
  case "${f##*/}" in a9ml-gpu.sh) [ $GPU = 0 ] && continue ;; esac   # default vendors stay as before
  [ "${f##*/}" = a9-sensors.sh ] && continue                  # only through --sensors (the stage's pinned copy)
  install -m 0755 "$f" "$ST/bin/"
done
if [ $EINK = 1 ]; then
  python3 -I "$HERE/aod/build.py" --out "$AW/work/aod-prefs"
  install -D -m 0644 "$AW/work/aod-prefs/prefs.jar" "$ST/etc/a9-aod/prefs.jar"
fi
if [ $GPU = 1 ]; then
  # kernel side: the snapshot must be a GPU one (gpucc, GPU DTB, options)
  for f in modules/modules.gpu modules/modules.options sm6115-hisense-hlte556n-android-gpu.dtb config; do
    [ -f "$SNAP/$f" ] || { echo "--gpu: $SNAP has no $f (A9_GPU=1 snapshot-kernel.sh)" >&2; exit 1; }
  done
  # G1 attempt 1: every GPU/SMMU (and, E Ink image, MDSS) supplier must be bound
  # before its consumer probes, given the 10 s deferred-probe timeout
  ML=$SNAP/modules/modules.gpu; MD=""; [ $EINK = 1 ] && { ML=$SNAP/modules/modules.load; MD=--mdss; }
  python3 -I "$HERE/gpu/check-probe-order.py" "$SNAP/sm6115-hisense-hlte556n-android-gpu.dtb" "$SNAP/config" "$ML" $MD \
    > "$AW/work/check-probe-order.log" || { cat "$AW/work/check-probe-order.log"; echo "--gpu: probe-order check failed" >&2; exit 1; }
  echo "  GPU: $(tail -n1 "$AW/work/check-probe-order.log")"
  grep -qx 'msm.ko separate_gpu_kms=1' "$SNAP/modules/modules.options" || { echo "--gpu: msm must load with separate_gpu_kms=1" >&2; exit 1; }
  # Mesa freedreno EGL/GLES and Turnip Vulkan, from the same msm build.
  MS=$GW/out/mesa
  (cd "$MS" && sha256sum -c --quiet SHA256SUMS) || { echo "--gpu: Mesa build $MS missing or changed (gpu/build-mesa.sh)" >&2; exit 1; }
  mkdir -p "$ST/lib64/egl" "$ST/lib64/hw"
  install -m 0644 "$MS"/lib64/egl/*_mesa.so "$ST/lib64/egl/"
  install -m 0644 "$MS/lib64/libgallium_dri.so" "$ST/lib64/"
  install -m 0644 "$MS/lib64/hw/vulkan.adreno.so" "$ST/lib64/hw/"
  # the probe (NDK, no deps beyond libc/libdl)
  P=$GW/out/probe/a9-gpu-probe
  "${ANDROID_NDK_HOME:-/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973}/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android30-clang" \
    -O2 -std=c11 -Wall -Wextra -Werror -fPIE -pie "$HERE/gpu/a9-gpu-probe.c" -ldl -o "$P"
  install -m 0755 "$P" "$ST/bin/a9-gpu-probe"
  # firmware: staged locally, verified, never in git
  FW=$GW/fw/stage
  (cd "$FW" && sha256sum -c --quiet "$GW/fw/stage.SHA256SUMS") || { echo "--gpu: firmware stage missing/changed (gpu/stage-firmware.sh)" >&2; exit 1; }
  mkdir -p "$ST/firmware/a9gpu"; cp -R "$FW/." "$ST/firmware/a9gpu/"
  install -m 0644 "$GW/fw/stage.SHA256SUMS" "$ST/firmware/a9gpu.SHA256SUMS"   # a9ml-gpu.sh verifies the copy
  install -D -m 0644 "$HERE/vendor/etc/init/init.a9ml-gpu.rc" "$ST/etc/init/init.a9ml-gpu.rc"
  # labels for the sphal libraries (apps load them in-process)
  cat >> "$ST/etc/selinux/vendor_file_contexts" <<'FC'
/(vendor|system/vendor)/lib(64)?/egl(/.*)?    u:object_r:same_process_hal_file:s0
/(vendor|system/vendor)/lib(64)?/libgallium_dri\.so    u:object_r:same_process_hal_file:s0
/(vendor|system/vendor)/lib(64)?/hw/vulkan\.adreno\.so    u:object_r:same_process_hal_file:s0
FC
  echo "  GPU: Mesa $(head -1 "$MS/BUILDINFO"), probe $(sha256sum "$P" | cut -c1-12), firmware $(sha256sum "$GW/fw/stage.SHA256SUMS" | cut -c1-12) (LOCAL)"
fi
install -m 0644 "$HOME/.android/adbkey.pub" "$ST/etc/adb_keys"       # main's host key, never committed
install -m 0644 "$HERE/vendor/etc/permissions/a9ml-unavailable-features.xml" "$ST/etc/permissions/"
# Row 28: GApps supplies network location; retain the handheld feature declaration.
install -m 0644 "$HERE/vendor/etc/permissions/aosp_excluded_hardware.xml" "$ST/etc/permissions/"
# E Ink compat shim (replaces the ROM's a9_eink_server; fails soft while no
# E Ink sysfs exists). Always (re)built through make, which rebuilds when the
# source is newer; then the binary must not be older than any source file.
EC=$REPO/scripts/mainline/eink-compat
: "${ANDROID_NDK_HOME:=/nix/store/f2n16g0sj97brhnfvj6wavpgv0qix94b-android-sdk-ndk-27.0.12077973/libexec/android-sdk/ndk/27.0.12077973}"
export ANDROID_NDK_HOME
nix-shell -p gnumake --run "make -C '$EC' android" >/dev/null
for f in "$EC"/*.c "$EC"/*.h "$EC/Makefile"; do
  [ -e "$f" ] || continue
  [ "$EC/build/a9-eink-compat-android" -nt "$f" ] || [ ! "$f" -nt "$EC/build/a9-eink-compat-android" ] \
    || { echo "compat shim binary older than $f" >&2; exit 1; }
done
echo "  compat shim: $(sha256sum "$EC/build/a9-eink-compat-android" | cut -c1-12) (git $(git -C "$REPO" log -1 --format=%h -- scripts/mainline/eink-compat))"
install -m 0755 "$EC/build/a9-eink-compat-android" "$ST/bin/a9-eink-compat"
install -m 0644 "$HERE/vendor/etc/init/a9-eink-compat.rc" "$ST/etc/init/a9-eink-compat.rc"   # replaces $EC's (see the file)
if [ "$BT" = 1 ]; then
    bash "$HERE/../bluetooth/build-tools.sh" "$AW/work/bt-tools"
    python3 -I "$HERE/../bluetooth/stage-vendor.py" --firmware "$BT_FW" \
        --hal-bundle "$A9_BT_HAL_BUNDLE" --snapshot "$SNAP" \
        --vendor-stage "$ST" --tools "$AW/work/bt-tools" \
        --dtb-name "$([ $GPU = 1 ] && echo sm6115-hisense-hlte556n-android-gpu.dtb || echo sm6115-hisense-hlte556n-android.dtb)"
fi

# --- opt-in HALs (see the top). Each replaces exactly one APEX dropped above.
if [ "$HEALTH" = real ]; then
  [ ! -e "$ST/apex/com.google.cf.health.apex" ] || { echo "cf health APEX still present" >&2; exit 1; }
  HB=$AW/dl/cf_arm64_x/apex/com.google.cf.health/root/bin/hw/android.hardware.health-service.cuttlefish
  python3 -I "$HERE/health/patch-health.py" "$HB" "$AW/work/health-a9.bin" >/dev/null
  install -D -m 0755 "$AW/work/health-a9.bin" "$ST/bin/hw/android.hardware.health-service.cuttlefish"
  install -D -m 0644 "$HERE/health/android.hardware.health-service.a9.rc" "$ST/etc/init/android.hardware.health-service.a9.rc"
  install -D -m 0644 "$HERE/health/android.hardware.health-service.a9.xml" "$ST/etc/vintf/manifest/android.hardware.health-service.a9.xml"
  grep -q '^/vendor/bin/hw/android\\.hardware\\.health-service\\.cuttlefish u:object_r:hal_health_default_exec:s0' \
    "$ST/etc/selinux/vendor_file_contexts" || { echo "health binary label missing from vendor_file_contexts" >&2; exit 1; }
  echo "  health: patched Cuttlefish HAL $(sha256sum "$AW/work/health-a9.bin" | cut -c1-12) (real power_supply values)"
fi
if [ "$THERMAL_HAL" = 1 ]; then
  # review 28: the HAL is only valid paired with a review-gated thermal kernel
  # (TSENS VALID-only + time-separated critical confirmation). A9_THERMAL_SNAP
  # = a snapshot-kernel.sh snapshot of an A9_THERMAL=1 build; with --eink it
  # must be the snapshot the modules come from.
  TS=${A9_THERMAL_SNAP:-}
  [ -n "$TS" ] && [ -f "$TS/SHA256SUMS" ] || { echo "A9_THERMAL_HAL=1 needs A9_THERMAL_SNAP=<thermal kernel snapshot>" >&2; exit 1; }
  (cd "$TS" && sha256sum -c --quiet SHA256SUMS) || { echo "bad snapshot $TS" >&2; exit 1; }
  for c in CONFIG_QCOM_TSENS_REQUIRE_VALID=y CONFIG_THERMAL_CRITICAL_CONFIRM_SAMPLES=3 CONFIG_THERMAL_EMERGENCY_POWEROFF_DELAY_MS=100; do
    grep -qx "$c" "$TS/config" || { echo "A9_THERMAL_SNAP config lacks $c: not a gated thermal kernel" >&2; exit 1; }
  done
  grep -Eq -- '-thermal(-adsp(-d2)?)?\.dtb$' "$TS/dtb.source" 2>/dev/null || { echo "A9_THERMAL_SNAP is not a thermal DTB snapshot" >&2; exit 1; }
  # integ-1: a --gpu vendor also ships $SNAP's modules (and pairs with its kernel)
  { [ $EINK = 0 ] && [ $GPU = 0 ]; } || [ "$(realpath "$TS")" = "$(realpath "$SNAP")" ] || { echo "--eink/--gpu modules snapshot $SNAP differs from A9_THERMAL_SNAP $TS" >&2; exit 1; }
  [ ! -e "$ST/apex/com.android.hardware.thermal.apex" ] || { echo "thermal example APEX still present" >&2; exit 1; }
  SO=${A9_SENSORS_OUT:-$HOME/A9-build/mainline/sensors}
  "$HERE/thermal-hal/build.sh" >/dev/null
  install -D -m 0755 "$SO/out/android.hardware.thermal-service.a9" "$ST/bin/hw/android.hardware.thermal-service.a9"
  install -D -m 0644 "$HERE/thermal-hal/android.hardware.thermal-service.a9.rc" "$ST/etc/init/android.hardware.thermal-service.a9.rc"
  install -D -m 0644 "$HERE/thermal-hal/android.hardware.thermal-service.a9.xml" "$ST/etc/vintf/manifest/android.hardware.thermal-service.a9.xml"
  install -D -m 0644 "$HERE/thermal-hal/thermal-a9.conf" "$ST/etc/thermal-a9.conf"
  sha256sum "$TS/Image.gz" | awk '{print $1}' > "$ST/etc/thermal-a9.kernel-image.sha256"   # the pairing, for the image gate
  printf '%s\n' '/vendor/bin/hw/android\.hardware\.thermal-service\.a9 u:object_r:hal_thermal_default_exec:s0' \
    >> "$ST/etc/selinux/vendor_file_contexts"
  echo "  thermal: A9 HAL $(cut -c1-12 "$SO/out/android.hardware.thermal-service.a9.sha256") + thermal-a9.conf"
fi

if [ $SENSORS = 1 ]; then
  : > "$AW/work/vendor-fsconfig-sensors.txt"
  python3 -I "$REPO/scripts/mainline/sensors/s2c-install-stage.py" --stage "$A9_SENSORS_STAGE" --vendor-stage "$ST" \
    --kernel-image-sha256 "$(cat "$ST/lib/modules/kernel-image.sha256")" --fsconfig "$AW/work/vendor-fsconfig-sensors.txt"
fi

# --- build.prop: Cuttlefish's, minus host/pmem/VAB/FRP items, plus ours
grep -Ev '^(ro\.product\.vendor\.|ro\.vendor\.build\.fingerprint|ro\.frp\.pst|ro\.rebootescrow|ro\.vendor\.hwcomposer\.pmem|vendor\.ser\.|ro\.vendor\.uwb|ro\.vendor\.virtwifi|ro\.virtual_ab\.|ro\.hardware\.virtual_device|ro\.vendor\.fingerprint_virtual|ro\.vendor\.init_dev_config|ro\.oem_unlock_supported|ro\.board\.platform|ro\.product\.board|ro\.soc\.|ro\.product\.first_api_level|persist\.sys\.zram_enabled|ro\.build\.ab_update|ro\.vendor\.build\.ab_ota_partitions|ro\.crypto\.metadata_init_delete_all_keys|ro\.minui\.|ro\.vendor\.hwc\.drm\.present_fence_not_reliable|ro\.surface_flinger\.running_without_sync_framework|ro\.logd\.size=)' \
  "$CF/build.prop" > "$ST/build.prop"
cat >> "$ST/build.prop" <<'EOF'

# ---- Hisense A9 mainline (scripts/mainline/android/build-vendor.sh) ----
ro.product.vendor.brand=Hisense
ro.product.vendor.device=HLTE556N
ro.product.vendor.manufacturer=Hisense
ro.product.vendor.model=A9 (mainline)
ro.product.vendor.name=hlte556n_mainline
ro.vendor.build.fingerprint=Hisense/hlte556n_mainline/HLTE556N:16/BP4A.251205.006/15581820:userdebug/test-keys
ro.board.platform=bengal
ro.product.board=bengal
ro.soc.manufacturer=QTI
ro.soc.model=SM6115
ro.product.first_api_level=30
persist.sys.zram_enabled=0
# VNDK-less vendor (202504). The GSI's vndk.rc (post-fs) does
# `setprop ro.vndk.version ${persist.sys.vndk}`, and vndk-detect falls back to
# ro.vendor.build.version.sdk (36) -> linkerconfig would look for a VNDK v36
# APEX that does not exist. An EMPTY ro. prop set here first makes that
# setprop fail (ro. is write-once), as Cuttlefish does for ro.hwui.use_vulkan.
ro.vndk.version=
# graphics: software (ANGLE from /system over SwiftShader vulkan.pastel), minigbm
# on the first DRM card (vkms in phase 1), drm_hwcomposer
ro.hardware.egl=angle
ro.hardware.vulkan=pastel
ro.hardware.gralloc=minigbm
ro.opengles.version=196609
ro.cpuvulkan.version=4202496
debug.hwui.renderer=skiagl
ro.zygote.disable_gl_preload=1
ro.sf.lcd_density=160
# (Cuttlefish's present-fence workarounds are dropped: vkms and a9_eink signal
# real fences; boot-4 review)
# logs: boot 4 lost the early main buffer (1M)
ro.logd.size=16M
# perfetto's traced crash-looped on traced.relay_producer_port=vsock://...
# (no CONFIG_VSOCKETS): 34 restarts, flags_health_check UPDATABLE_CRASHING
# (MEASURED boot 4); on a real /data that can roll back updated APEXes
persist.traced.enable=0
# E Ink: GPU composition only (the ROM's vndk.rc also forces it)
debug.sf.hw=0
# ART on one slow core (cmdline maxcpus=1 cpufreq.off=1): zygote64 aborted
# with "HeapTaskDaemon failure : SuspendAll timeout" (Jit thread pool worker
# Runnable), MEASURED boot 2. Bring-up only: a 60 s suspend timeout
# (default 4 s in release ART). The option string "-XX:ThreadSuspendTimeout="
# is MEASURED present in the v7 GSI's libart.so (com.android.art.apex).
dalvik.vm.extra-opts=-XX:ThreadSuspendTimeout=60000
# vendor APEX selection for the multi-install names. Back in build.prop
# (2026-10-15): the kernel cmdline has no room (the ABL passes only 511 bytes).
# vendor_init setting ro.boot.* is an apexd_select_prop denial: fine while
# permissive, must move (e.g. a single variant per name) before enforcing.
ro.boot.vendor.apex.com.android.hardware.keymint=com.android.hardware.keymint.rust_nonsecure
ro.boot.vendor.apex.com.android.hardware.gatekeeper=com.android.hardware.gatekeeper.nonsecure
ro.boot.vendor.apex.com.android.hardware.graphics.composer=com.android.hardware.graphics.composer.drm_hwcomposer
# adb (the ROM is a user build with debug props; adbd always wants a key)
persist.sys.usb.config=adb
ro.a9ml.vendor=1
# E Ink default UI mode: Smooth (518), stock-like (user decision 2026-10-17 after
# eink-trial2; it was Clear 515 + lock_mode=1 in trials 1-2). The kernel default is
# also Smooth. a9-eink-compat applies this at start; the app's own mode menu stays
# live because there is no ro.vendor.a9ml.eink.lock_mode line (set it to 1 only for
# controlled mode comparisons: compat then ignores app mode requests).
ro.vendor.a9ml.eink.default_mode=518
# Stock-like policy options (all off unless set; docs/mainline-eink-quality.md section 5).
# The kernel module parameter clear_shadow defaults to 1 (Clear UI shadow curve);
# this prop forces 0|1 at start:
#ro.vendor.a9ml.eink.clear_shadow=0
# Scroll-time Balanced/Smooth, rest in the chosen mode; needs "sv" socket events:
#ro.vendor.a9ml.eink.dyn_refresh=1
# Clear on activity change (6 s re-arm); needs "ac" socket events:
#ro.vendor.a9ml.eink.activity_clear=1
EOF
if [ $GPU = 1 ]; then cat >> "$ST/build.prop" <<'EOF'
# ---- GPU mode (build-vendor.sh --gpu, runs/gpu.md): a9ml-gpu.sh select sets
# persist.graphics.egl=mesa (SurfaceFlinger) and ro.hardware.egl=mesa (apps) only if
# the Adreno answers and a pbuffer renders; otherwise both are angle. ro.hardware.egl
# is deliberately NOT defined in this file in a GPU build (the base line is removed
# below): the A16 GraphicsEnvironment turns ANGLE on for every app when it reads
# "angle", so a static angle line pins apps to SwiftShader (eink-trial2: launcher at
# 345 % CPU). a9ml-gpu.sh select sets it once; init.a9ml-gpu.rc falls back to angle.
# GPU images select Turnip below; non-GPU images retain SwiftShader.
ro.vendor.a9ml.gpu=1
ro.vendor.a9ml.gpu.egl=mesa
# apps use Mesa EGL when selected; "angle" = apps stay on ANGLE/SwiftShader (old behaviour)
ro.vendor.a9ml.gpu.app_egl=mesa
ro.vendor.a9ml.gpu.variant=VARIANT
debug.renderengine.backend=skiaglthreaded
EOF
  # The GPU init action reports the GLES version of the selected app driver:
  # Mesa FD610 = 3.2; ANGLE/SwiftShader fallback = 3.1. Both ro. props are
  # write-once, so leave them unset until that selection has completed.
  sed -i '/^ro\.hardware\.egl=angle$/d; /^ro\.opengles\.version=/d' "$ST/build.prop"
  python3 -I "$HERE/gpu/vulkan.py" --stage "$ST" --mesa "$MS"
  grep -q '^ro\.hardware\.egl=' "$ST/build.prop" && { echo "build-vendor: ro.hardware.egl still defined in a GPU build" >&2; exit 1; }
  V=vkms; [ $EINK = 1 ] && V=eink
  sed -i "s/^ro.vendor.a9ml.gpu.variant=VARIANT$/ro.vendor.a9ml.gpu.variant=$V/" "$ST/build.prop"
fi
if [ "$BT" = 1 ]; then
    # Filter inherited values first: Android properties must have one definition.
    python3 -I - "$ST/build.prop" "$HERE/../bluetooth/android/bluetooth.properties" <<'PYBT'
from pathlib import Path
import sys
p, extra = map(Path, sys.argv[1:])
rows = extra.read_text().splitlines()
keys = {s.split('=',1)[0] for s in rows if s and not s.startswith('#')}
base = [s for s in p.read_text().splitlines() if s.split('=',1)[0] not in keys]
p.write_text('\n'.join(base) + '\n\n' + '\n'.join(rows) + '\nro.vendor.a9.bluetooth=1\n')
PYBT
fi
[ $SENSORS = 1 ] && python3 -I "$REPO/scripts/mainline/sensors/s2c-install-stage.py" --stage "$A9_SENSORS_STAGE" --vendor-stage "$ST" --props-only
# phase 2: the panel is 824x1648 at ~6.1" (~300 dpi); vkms phase 1 keeps 160
[ $EINK = 1 ] && sed -i 's/^ro.sf.lcd_density=160$/ro.sf.lcd_density=280/' "$ST/build.prop"
grep -q '^ro.zygote=zygote64$' "$ST/build.prop" || { echo "ro.zygote missing" >&2; exit 1; }
if grep -Eq '^ro\.adb\.secure=1|^ro\.frp\.pst=' "$ST/build.prop"; then echo "forbidden prop in build.prop" >&2; exit 1; fi

# Wi-Fi remains an opt-in image; coordinator stages the first radio/MPSS test.
# Android HAL inclusion is independently opt-in and remains runtime gated.
if [ "$WIFI" = 1 ]; then
  A9_WIFI_PD_MAPPER=$PD_MAPPER python3 -I "$REPO/scripts/mainline/wifi/stage-vendor.py" \
    --stage "$ST" --assets "${A9_WIFI_ASSETS:-$HOME/A9-build/mainline/sol-wifi}" \
    --binaries "${A9_WIFI_BINARIES:-$HOME/A9-build/mainline/sol-wifi/build/android}"
  python3 -I "$REPO/scripts/mainline/wifi/android/stage-android.py" --stage "$ST" --policy-only
  if [ "$WIFI_ANDROID" = 1 ]; then
    python3 -I "$REPO/scripts/mainline/wifi/android/stage-android.py" --stage "$ST" --cf "$AW/dl/cf_arm64_x"
  fi
  if [ "$WIFI_SESSION" = 1 ]; then
    SESSION_ACK=
    if [ "${A9_WIFI_SESSION_INERT:-0}" = 1 ]; then
      SESSION_ACK="--inert"   # S0: placeholders only; the image can never start the session
      : "${A9_WIFI_IDENTITY_SHA256:=$(printf '0%.0s' $(seq 64))}" "${A9_WIFI_PRELIMINARY_SHA256:=$(printf '0%.0s' $(seq 64))}"
    else
      : "${A9_WIFI_SESSION_AUTHORIZE:?building a session image is the human authorisation: name it}"
      : "${A9_WIFI_SESSION_ACCEPT_RISK:?building a session image is the human risk acceptance: name it}"
      : "${A9_WIFI_IDENTITY_SHA256:?reviewed QMI identity digest required}"
      : "${A9_WIFI_PRELIMINARY_SHA256:?reviewed preliminary evidence digest required}"
    fi
    STOOLS=$AW/work/session-tools$SUFFIX
    rm -rf "$STOOLS"
    python3 -I "$REPO/scripts/mainline/wifi/stage-tools.py" --out "$STOOLS" >/dev/null
    python3 -I "$REPO/scripts/mainline/wifi/session/stage-session.py" --stage "$ST" --tools "$STOOLS" \
      --seconds "${A9_WIFI_SESSION_SECONDS:-3600}" --identity-sha256 "$A9_WIFI_IDENTITY_SHA256" \
      --preliminary-sha256 "$A9_WIFI_PRELIMINARY_SHA256" --country "${A9_WIFI_COUNTRY:?A9_WIFI_COUNTRY (two letters, user decision US) is mandatory for a session image}" \
      ${SESSION_ACK:---authorize "$A9_WIFI_SESSION_AUTHORIZE" --accept-risk "$A9_WIFI_SESSION_ACCEPT_RISK"}
  fi
fi

# --- safety: nothing in the vendor may name a protected partition
if grep -rIlE --exclude-dir=selinux '/dev/block/(by-name/)?(userdata|metadata|misc|persist|modemst|fsg|frp|devinfo|diag)' "$ST/etc" "$ST/bin" 2>/dev/null | grep -v '^$'; then
  echo "a vendor file names a protected partition (above)" >&2; exit 1
fi

# --- pack (ext4 raw + sparse, labels from the GSI plat + Cuttlefish vendor file_contexts)
cat > "$FSCONFIG" <<'EOF'
bin/a9ml-*.sh 0 2000 0755
bin/a9-eink-compat 0 2000 0755
bin/a9-gpu-probe 0 2000 0755
etc/adb_keys 0 0 0644
EOF
if [ "$WIFI" = 1 ]; then
  cat "$REPO/scripts/mainline/wifi/fs-config.txt" >> "$FSCONFIG"
fi
[ $SENSORS = 1 ] && cat "$AW/work/vendor-fsconfig-sensors.txt" >> "$FSCONFIG"
if [ "$BT" = 1 ]; then
    cat >> "$FSCONFIG" <<'EOFBT'
bin/a9-bt-identity 0 0 0755
bin/a9ml-bluetooth.sh 0 0 0755
etc/bluetooth/local-bd-address 0 1002 0640
EOFBT
fi
"$HERE/pack-vendor.sh" -C "$FSCONFIG" -n vendor -o "$PACKOUT" --check-sepolicy "$ST"
for s in img sparse.img; do
  mv -f "$PACKOUT/vendor.$s" "$AW/out/$NAME.$s"
  (cd "$AW/out" && sha256sum "$NAME.$s" > "$NAME.$s.sha256")
done
echo "$NAME: $AW/out/$NAME.img $(cut -c1-16 "$AW/out/$NAME.img.sha256")..."
