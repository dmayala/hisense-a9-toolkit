#!/usr/bin/env bash
# run-tests.sh -- every offline check of the sensors/thermal/health work
# (runs/sensors.md). No phone. Writes only under ~/A9-build/mainline/sensors.
#   scripts/mainline/sensors/run-tests.sh [--vendor]
#   --vendor  also build both vendor variants privately (A9_ANDROID=<private>)
#             and check the flagged image's HAL files and labels (~1 min each)
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
M=$(cd "$HERE/.." && pwd)
REPO=$(cd "$M/../.." && pwd)
O=${A9_SENSORS_OUT:-$HOME/A9-build/mainline/sensors}
fails=0
# private tree = v7.2.10 + the whole kernel/patches series (0043-0046 included),
# cloned from the shared tree's objects (read-only use; ~2 GB; removable).
# A9_THERMAL_KTREE=<tree> reuses a tree build.sh already patched (e.g. the
# private A9_THERMAL=1 build tree).
series_tree() {
  local K=$1
  rm -rf "$K"; git clone -q --shared --no-checkout "$HOME/A9-build/mainline/linux-7.2.10" "$K" &&
    git -C "$K" checkout -q "v7.2.10" &&
    git -C "$K" -c user.name=a9 -c user.email=dmayala@users.noreply.github.com am -q "$M"/kernel/patches/*.patch
}
kunit_thermal() {
  local K=${A9_THERMAL_KTREE:-$O/linux}
  # rebuild the PRIVATE cached tree if it predates 0046 (its case count would be
  # short); never delete a tree passed in with A9_THERMAL_KTREE
  if ! grep -qs emul_trip_high "$K/drivers/thermal/thermal_helpers.c"; then
    [ -z "${A9_THERMAL_KTREE:-}" ] || { echo "   $K lacks patch 0046" >&2; return 1; }
    series_tree "$K" || return 1
  fi
  "$M/thermal/tests/kunit.sh" "$K"
}
patches_apply() {   # the full series (0001-0046) applies to v7.2.10 in a scratch clone
  local t rc; t=$(mktemp -d "$O/papply.XXXX")
  series_tree "$t/l"; rc=$?
  rm -rf "$t"; return $rc
}
default_off() {     # 0043/0044 change nothing unless a fragment asks: only a9-thermal.config sets them
  local ok=0 f
  for f in "$M"/kernel/*.config; do
    grep -E 'CONFIG_(THERMAL_CRITICAL_CONFIRM_SAMPLES|QCOM_TSENS_REQUIRE_VALID|THERMAL_CRITICAL_CONFIRM_KUNIT_TEST)' "$f" && { echo "   $f sets a thermal gate"; ok=1; }
  done
  grep -A3 '^+config THERMAL_CRITICAL_CONFIRM_SAMPLES$' "$M"/kernel/patches/0043-*.patch | grep -q '^+\s*default 1$' || ok=1
  grep -A3 '^+config QCOM_TSENS_REQUIRE_VALID$' "$M"/kernel/patches/0044-*.patch | grep -q '^+\s*default n$' || ok=1
  return $ok
}
step() { echo "== $1"; shift; if "$@"; then echo "   PASS"; else echo "   FAIL"; fails=$((fails+1)); fi; }

step "health patch (patch-health.py)" python3 -I "$M/android/health/test-patch-health.py"
step "health gates (health-gate.py: artifact, telemetry, compare)" "$M/android/health/test-health-gate.sh"
step "kernel thermal KUnit 0043+0045/0046 on arm64 QEMU, all cases must pass (patched private tree)" kunit_thermal
step "kernel series 0001-0046 applies to v7.2.10 (private clone check)" patches_apply
step "thermal emulation step script (emul-step.sh, mksh, fake sysfs; review 41 F1 faults)" "$M/thermal/tests/emul-step-test.sh"
step "thermal host steps (host-step.sh + cd-step.sh, fake adb/root shell/live log; review 41 F2/F3)" "$M/thermal/tests/host-step-test.sh"
step "0043/0044 default off (no default fragment sets them)" default_off
step "thermal HAL core (host, ASan/UBSan)" nix-shell -p gcc --run "$M/android/thermal-hal/tests/run-tests.sh"
step "thermal DT (a9-thermal.dtsi on the Android board DT)" nix-shell "$M/kernel/shell.nix" --run "$M/thermal/check-dt.sh"
step "ADSP sensors DT prep (compile only, wired into no build)" nix-shell "$M/kernel/shell.nix" --run "$HERE/check-adsp-dt.sh"
step "sensor inventory from the golden dump (read-only)" "$HERE/extract-stock.sh"
step "thermal HAL cross-build (NDK, NEEDED = LLNDK only)" "$M/android/thermal-hal/build.sh"
consistency() {
  local ok=0
  grep -q '^service vendor.health-a9 /vendor/bin/hw/android.hardware.health-service.cuttlefish$' "$M/android/health/android.hardware.health-service.a9.rc" || ok=1
  grep -q '"$ST/bin/hw/android.hardware.health-service.cuttlefish"' "$M/android/build-vendor.sh" || ok=1
  grep -q '^service vendor.thermal-a9 /vendor/bin/hw/android.hardware.thermal-service.a9$' "$M/android/thermal-hal/android.hardware.thermal-service.a9.rc" || ok=1
  grep -q '"$ST/bin/hw/android.hardware.thermal-service.a9"' "$M/android/build-vendor.sh" || ok=1
  grep -q '<version>3</version>' "$M/android/thermal-hal/android.hardware.thermal-service.a9.xml" || ok=1
  grep -q '<version>4</version>' "$M/android/health/android.hardware.health-service.a9.xml" || ok=1
  grep -q '/etc/thermal-a9.conf"' "$M/android/build-vendor.sh" && grep -q 'kConf = "/vendor/etc/thermal-a9.conf"' "$M/android/thermal-hal/Thermal.cpp" || ok=1
  grep -qx 'CONFIG_THERMAL_EMERGENCY_POWEROFF_DELAY_MS=100' "$M/thermal/a9-thermal.config" || ok=1
  grep -qx 'CONFIG_THERMAL_CRITICAL_CONFIRM_SAMPLES=3' "$M/thermal/a9-thermal.config" || ok=1
  grep -qx 'CONFIG_QCOM_TSENS_REQUIRE_VALID=y' "$M/thermal/a9-thermal.config" || ok=1
  grep -q 'lacks patches 0043/0044' "$M/kernel/build.sh" || ok=1
  ! grep -E '^skin' "$M/android/thermal-hal/thermal-a9.conf" | grep -q power_supply || ok=1
  return $ok
}
step "rc / install path / VINTF / config consistency" consistency
if [ "${1:-}" = --vendor ]; then
  S=$O/aw; mkdir -p "$S/dl" "$S/out" "$S/work"; ln -sfn "$HOME/A9-build/mainline/android/dl/cf_arm64_x" "$S/dl/cf_arm64_x"
  vendor_check() {
    # pairing: refused without a gated thermal kernel snapshot ...
    if A9_ANDROID=$S A9_ANDROID_WORK=$S A9_THERMAL_HAL=1 A9_THERMAL_SNAP= "$M/android/build-vendor.sh" >"$O/vendor-nopair.log" 2>&1; then
      echo "   thermal HAL built without A9_THERMAL_SNAP"; return 1; fi
    # ... and with an ungated one; a FAKE gated snapshot only exercises the check (not a phone image)
    local F=$O/fake-thermal-snap; rm -rf "$F"; mkdir -p "$F"
    printf 'fake' > "$F/Image.gz"; echo sm6115-hisense-hlte556n-android-thermal.dtb > "$F/dtb.source"
    printf 'CONFIG_QCOM_TSENS_REQUIRE_VALID=y\nCONFIG_THERMAL_CRITICAL_CONFIRM_SAMPLES=1\nCONFIG_THERMAL_EMERGENCY_POWEROFF_DELAY_MS=100\n' > "$F/config"
    (cd "$F" && sha256sum Image.gz config dtb.source > SHA256SUMS)
    if A9_ANDROID=$S A9_ANDROID_WORK=$S A9_THERMAL_HAL=1 A9_THERMAL_SNAP=$F "$M/android/build-vendor.sh" >"$O/vendor-ungated.log" 2>&1; then
      echo "   thermal HAL built against an ungated kernel"; return 1; fi
    sed -i 's/CONFIRM_SAMPLES=1/CONFIRM_SAMPLES=3/' "$F/config"; (cd "$F" && sha256sum Image.gz config dtb.source > SHA256SUMS)
    A9_ANDROID=$S A9_ANDROID_WORK=$S A9_HEALTH=real A9_THERMAL_HAL=1 A9_THERMAL_SNAP=$F "$M/android/build-vendor.sh" >"$O/vendor-flagged.log" 2>&1 || return 1
    local img=$S/out/vendor_b-health-thermal.img st=$S/work/vendor-stage ok=0
    [ ! -e "$st/apex/com.google.cf.health.apex" ] && [ ! -e "$st/apex/com.android.hardware.thermal.apex" ] || ok=1
    [ -e "$st/apex/com.google.cf.health.storage.apex" ] || ok=1
    for f in bin/hw/android.hardware.health-service.cuttlefish bin/hw/android.hardware.thermal-service.a9 etc/thermal-a9.conf \
             etc/init/android.hardware.health-service.a9.rc etc/init/android.hardware.thermal-service.a9.rc \
             etc/vintf/manifest/android.hardware.health-service.a9.xml etc/vintf/manifest/android.hardware.thermal-service.a9.xml; do
      debugfs -R "stat /$f" "$img" 2>/dev/null | grep -q 'Inode:' || { echo "   missing in image: $f"; ok=1; }
    done
    debugfs -R 'ea_get /bin/hw/android.hardware.health-service.cuttlefish security.selinux' "$img" 2>/dev/null | grep -q hal_health_default_exec || ok=1
    debugfs -R 'ea_get /bin/hw/android.hardware.thermal-service.a9 security.selinux' "$img" 2>/dev/null | grep -q hal_thermal_default_exec || ok=1
    [ "$(grep -l 'IThermal/default' "$st"/etc/vintf/manifest/*.xml 2>/dev/null | wc -l)" = 1 ] || ok=1
    [ "$(grep -l 'IHealth/default' "$st"/etc/vintf/manifest/*.xml 2>/dev/null | wc -l)" = 1 ] || ok=1
    [ "$(cat "$st/etc/thermal-a9.kernel-image.sha256")" = "$(sha256sum "$F/Image.gz" | cut -d' ' -f1)" ] || ok=1
    rm -f "$S/out/vendor_b-health-thermal".*   # test-only image (fake kernel pairing): never keep it
    return $ok
  }
  step "vendor image with A9_HEALTH=real A9_THERMAL_HAL=1 (private build)" vendor_check
fi
echo; [ $fails = 0 ] && echo "ALL PASS" || echo "$fails FAILED"; exit $((fails > 0))
