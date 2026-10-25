#!/usr/bin/env bash
#
# android-boot.sh -- boot our Android 16 GSI (system_b, unchanged) on the
# mainline kernel with the new vendor_b, then put the phone back in its daily
# state by itself. docs/mainline-android.md section 5. Modeled on
# scripts/mainline/first-boot.sh (same fail-safe route, same restore).
#
# What is written, and only this (slot A is never written):
#   vendor_b  (logical, in super)  from fastbootd entered from Android slot a
#   boot_b    mainline kernel + Android first-stage ramdisk (bootloader fastboot)
#   dtbo_b    the null overlay (bootloader fastboot)
#   slot      set_active b for the test, set_active a in the restore
# Never: misc, recovery_*, userdata, metadata, persist, modem*, devinfo, diag,
# any _a partition. The images keep userdata/metadata unmounted (fstab has no
# entry; /data is a RAM disk).
#
# Fail-safe (MEASURED in the ABL, docs/mainline-kernel-build.md section 4):
#   - reset after the kernel's PON probe -> reason 2 -> bootloader fastboot
#   - reset before it -> slot B retry count runs down (7), ABL boots slot a
#   - Android itself: the vendor dead-man (a9_deadman, init.a9m.rc) sends the
#     phone to the bootloader (setprop sys.powerctl reboot,bootloader = PON
#     reason 2) 2400 s without boot_completed, or 1200 s after it, unless
#     /data/local/tmp/a9-stay exists (this script creates it while collecting).
#
# Modes:
#   --offline  host-only checks (images, abl-check, cmdline, sizes, golden copies)
#   (default)  CHECK: offline + read-only phone checks in Android slot a; prints
#              the approval code for exactly these images
#   --write --code <c>   stage vendor_b, flash boot_b/dtbo_b, slot b, reboot,
#              watch, collect, then ALWAYS restore (golden boot_b, dtbo_b,
#              vendor_b, slot a) and verify
#   --restore  only the restore half (golden boot_b, dtbo_b, vendor_b, slot a);
#              works from any state: mainline Android on b, bootloader, slot a
#              with a test build on b
#   --to-slot-a  only switch to slot a (bootloader set_active a, reboot); no
#              partition writes, slot B keeps the test build
#   --no-restore (with --write): iterate without the golden restore. Before:
#              boot_b/dtbo_b/vendor_b may hold a previous test build (their
#              hashes are recorded in the run dir instead of requiring golden).
#              After: gates closed as always, slot B left as is, the phone is
#              switched to slot a (no writes) unless a --hold keeps it on b.
#              Run --restore once at the end of the series.
#   --stage-only --code <c>   only write vendor_b (fastbootd) and return to slot a
#
# Usage: android-boot.sh [--offline|--write|--restore|--stage-only] [--code c]
#          [--boot <img>] [--vendor <img>] [--watch <s>] [--wifi]
#          [--eink [--waveform <a9-waveform.bin> --template <a9-ctrl-template.bin>]
#                  [--open-gates --bins <lo-hi> [--gate-secs <s>]]] [--hold [<s>]]
#
# --hold [s] (default 14400, max 21600): after boot_completed and the
#   collection, do NOT restore. The host refreshes the vendor dead-man's stay
#   file every 60 s and raises its uptime cap (vendor.a9ml.maxstay), saves E
#   Ink status / kmsg every 5 min into the run dir. With --open-gates the gates
#   stay open for the whole hold (every kernel cap, the fault watch and the
#   latch stay active; hv_hold_ms = driver default, stock-like). The hold ends
#   at the timeout, on a fault, on Ctrl-C/TERM, or when the phone is lost: the
#   gates are closed, the script exits and prints the restore command
#   (`android-boot.sh --restore`). After that the dead-man sends the phone to
#   fastboot within ~10-20 min if nobody restores it. A fault ends the hold and
#   restores at once.
#
# Phase 2 (docs section 9): --eink uses out/a9-android-eink-boot.img +
#   out/vendor_b-eink.img (E Ink modules; drm_hwcomposer on the a9_eink card,
#   found by platform path; ALL GATES CLOSED). --waveform pushes the user's own
#   blob (vendor data; never from inside the repo) to /data/local/tmp/a9fw after
#   adb is up (firmware_class.path). --open-gates additionally opens the panel,
#   FP9929A and a9_eink.enable gates for --gate-secs (default 120) through the
#   vendor property vendor.a9ml.eink.gate, then closes them; refused unless
#   A9_EINK_E5C_PASSED=yes (stage E5c passed on the diskless image). --bins is
#   the temperature-bin range that E5c admitted (its E5C_BINS line); the vendor
#   sets a9_eink.bin_min/bin_max to it and refuses without it. --template is the
#   control template (scripts/mainline/eink/tools/a9ct-extract.py, from the
#   user's own composer; patch 0028 refuses to drive without it), pushed next to
#   the waveform; required with --open-gates. Kernel caps
#   (fp9929a.max_on_ms 20000, min_off_ms) are the driver defaults, checked only.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"

REQUIRE_PREPARATION=0 LATENCY_PREP_FD="" GHOST_CLOSED_PROBE=0
WIFI=0
S2_QUIET=0
MODE=check CODE="" WATCH=2700 NORESTORE=0 HOLD=0 EINK=0 WAVEFORM="" TEMPLATE="" OPEN_GATES=0 GATE_SECS=120 BINS="" EINK_FAULT="" BOOT_SET=0 VENDOR_SET=0
AW=${A9_ANDROID:-$HOME/A9-build/mainline/android}
BOOT="$AW/out/a9-android-boot.img"
VENDOR="$AW/out/vendor_b.img"
DTBO=${A9_MAINLINE:-$HOME/A9-build/mainline}/out-android/recovery_dtbo.img
G_VENDOR="$AW/golden/vendor_b.img"          # lpunpack of the golden super (extract-golden-vendor.sh)
while [ $# -gt 0 ]; do
  case "$1" in
    --require-preparation) REQUIRE_PREPARATION=1 ;;
    --ghost-closed-probe) GHOST_CLOSED_PROBE=1; REQUIRE_PREPARATION=1 ;;
    --latency-prepared-fd) REQUIRE_PREPARATION=1; LATENCY_PREP_FD="${2:-}"; shift ;;
    --offline) MODE=offline ;; --write) MODE=write ;; --restore) MODE=restore ;; --stage-only) MODE=stage ;;
    --to-slot-a) MODE=slota ;; --no-restore) NORESTORE=1 ;;
    --code) CODE="${2:-}"; shift ;;
    --boot) BOOT="${2:-}"; BOOT_SET=1; shift ;;
    --vendor) VENDOR="${2:-}"; VENDOR_SET=1; shift ;;
    --eink) EINK=1 ;;
    --wifi) WIFI=1 ;;
    --s2-quiet) S2_QUIET=1 ;;
    --waveform) WAVEFORM="${2:-}"; shift ;;
    --template) TEMPLATE="${2:-}"; shift ;;
    --open-gates) OPEN_GATES=1 ;;
    --gate-secs) GATE_SECS="${2:-}"; shift ;;
    --hold) HOLD=14400; case "${2:-}" in [0-9]*) HOLD="$2"; shift ;; esac ;;
    --bins) BINS="${2:-}"; shift ;;
    --watch) WATCH="${2:-}"; shift ;;
    -h|--help) sed -n '2,48p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
  shift
done

fail() { echo "FAIL: $*" >&2; exit 1; }
# The ghost coordinator opts in; all write/open calls on that route need its pipe.
if [ "$MODE" = write ] && [ "$REQUIRE_PREPARATION" = 1 ] && { [ "$OPEN_GATES" = 1 ] || [ "$GHOST_CLOSED_PROBE" = 1 ]; }; then
  case "$LATENCY_PREP_FD" in ''|*[!0-9]*) fail "safe3 requires inherited preparation pipe";; esac
  [ "$LATENCY_PREP_FD" -ge 3 ] && [ -p "/proc/$$/fd/$LATENCY_PREP_FD" ] || fail "safe3 preparation fd is not a pipe"
fi
if [ "$EINK" = 1 ]; then
  [ "$BOOT_SET" = 1 ] || BOOT="$AW/out/a9-android-eink-boot.img"
  [ "$VENDOR_SET" = 1 ] || VENDOR="$AW/out/vendor_b-eink.img"
fi
# Wi-Fi has separate artifacts and an additional source/binary provenance gate.
# This flag permits staging the reviewed confined servers, never modem startup.
if [ "$WIFI" = 1 ]; then
  umask 077
  [ "$BOOT_SET" = 1 ] || BOOT="$AW/out/boot-wifi.img"
  if [ "$VENDOR_SET" = 0 ]; then
    if [ "$EINK" = 1 ]; then VENDOR="$AW/out/vendor_b-eink-wifi.img"; else VENDOR="$AW/out/vendor_b-wifi.img"; fi
  fi
  DTBO="${A9_WIFI_SNAPSHOT:-$AW/kernel-snap-wifi}/recovery_dtbo.img"
fi
[ "$S2_QUIET" = 0 ] || { [ "$WIFI" = 1 ] && [ "$EINK" = 1 ] && [ "$OPEN_GATES" = 0 ] && [ "$HOLD" -ge 300 ]; } || fail '--s2-quiet requires Wi-Fi/E Ink, closed gates, and a bounded hold'
[ "$OPEN_GATES" = 0 ] || { [ "$EINK" = 1 ] && [ -n "$WAVEFORM" ] && [ -n "$TEMPLATE" ]; } || fail "--open-gates needs --eink, --waveform and --template"
[ "$GHOST_CLOSED_PROBE" = 0 ] || { [ "$OPEN_GATES" = 0 ] && [ "$EINK" = 1 ] && [ -n "$WAVEFORM" ] && [ -n "$TEMPLATE" ]; } || fail "closed probe forbids --open-gates and needs the exact E Ink blobs"
[ -z "$TEMPLATE" ] || [ -n "$WAVEFORM" ] || fail "--template needs --waveform"
if [ "$OPEN_GATES" = 1 ]; then
  case "$BINS" in [0-9]-[0-9]|[0-9]-1[0-3]|1[0-3]-1[0-3]) ;; *) fail "--open-gates needs --bins <lo-hi> (the E5C_BINS range E5c admitted)" ;; esac
  [ "${BINS%-*}" -le "${BINS#*-}" ] || fail "--bins lo > hi"
fi
case "$GATE_SECS" in ''|*[!0-9]*) fail "--gate-secs needs seconds" ;; esac
case "$HOLD" in *[!0-9]*) fail "--hold needs seconds" ;; esac
[ "$HOLD" -le 21600 ] || fail "--hold is at most 21600 s (the vendor dead-man's hard cap)"
[ "$HOLD" = 0 ] || [ "$HOLD" -ge 300 ] || fail "--hold below 300 s makes no sense; use --gate-secs"
[ "$HOLD" -gt 0 ] && [ "$OPEN_GATES" = 1 ] && GATE_SECS=$HOLD
ok()   { echo "  ok   $*"; }
# shellcheck source=../../a9-env.sh
. "$REPO/scripts/a9-env.sh"
GOLD="${A9_GOLDEN:-$A9_BACKUPS/edl_2026-10-17_102417}"
G_BOOT="$GOLD/lun4/boot_b.bin"; G_DTBO="$GOLD/lun4/dtbo_b.bin"
BOOT_B_MAX=100663296

sha() { sha256sum "$1" | awk '{print $1}'; }
sha_ok() { [ -f "$1.sha256" ] && [ "$(sha "$1")" = "$(awk '{print $1}' "$1.sha256")" ]; }
approval_code() { { printf '%s wifi=%s eink=%s gates=%s/%s wf=%s' "$(cat "$BOOT.sha256" "$VENDOR.sha256" 2>/dev/null | awk '{print $1}')" \
  "$WIFI" "$EINK" "$OPEN_GATES" "$GATE_SECS/$BINS/hold$HOLD/nr$NORESTORE" "$( [ -n "$WAVEFORM" ] && sha256sum "$WAVEFORM" | cut -c1-64)$( [ -n "$TEMPLATE" ] && sha256sum "$TEMPLATE" | cut -c1-64)"; [ "$S2_QUIET" = 0 ] || printf ' s2quiet=1'; } | sha256sum | cut -c1-8; }

offline_checks() {
  echo "== host checks"
  for f in "$BOOT" "$VENDOR" "$G_VENDOR"; do
    [ -f "$f" ] || fail "missing: $f"
    sha_ok "$f" || fail "sha256 missing or mismatch: $f"
  done
  ok "boot $(cut -c1-12 "$BOOT.sha256")  vendor $(cut -c1-12 "$VENDOR.sha256")  golden vendor $(cut -c1-12 "$G_VENDOR.sha256")"
  [ "$(stat -c %s "$BOOT")" -le "$BOOT_B_MAX" ] || fail "boot image larger than boot_b"
  [ "$(head -c 4 "$DTBO" | od -A n -t x1 | tr -d ' ')" = d7b7ab1e ] || fail "$DTBO is not a DTBO table image"
  "$REPO/scripts/abl-check.sh" "$BOOT" boot --dtbo "$DTBO" --quiet || fail "abl-check boot + null dtbo"
  ok "abl-check: boot_b + dtbo_b route PASS"
  python3 -I - "$BOOT" "$EINK" "$WIFI" <<'PY' || fail "boot cmdline: required params missing from the EFFECTIVE cmdline"
import sys
h = open(sys.argv[1], 'rb').read(4096)
# EFFECTIVE cmdline = the 512-byte header field only: the ABL drops extra_cmdline
# (MEASURED 2026-10-15, dmesg 'Kernel command line' cut at byte ~511)
extra = h[608:1632].split(b'\0')[0]
if extra:
    print('extra_cmdline is not empty (the ABL ignores it):', extra[:60]); sys.exit(1)
c = h[64:576].split(b'\0')[0].decode().split()
need = ['qcom_pon.beacon=2', 'selinux_force_permissive=1', 'enforcing=0', 'androidboot.slot_suffix=_b',
        'androidboot.hardware=hlte556n', 'androidboot.boot_devices=soc@0/4804000.ufshc',
        'androidboot.init_fatal_reboot_target=bootloader', 'brd.rd_size=3145728', 'maxcpus=8']
if sys.argv[2] == '1':
    need += ['androidboot.a9ml.eink=1']
    if sys.argv[3] != '1': need += ['firmware_class.path=/data/local/tmp/a9fw']
else:
    need += ['androidboot.a9ml.eink=0']   # init.hlte556n.rc starts the composer only for eink=0
if sys.argv[3] == '1': need += ['firmware_class.path=/mnt/a9-wifi/firmware']
miss = [n for n in need if n not in c]
bad = [w for w in c if w.startswith('qcom_pon.beacon=') and w != 'qcom_pon.beacon=2']
if miss or bad: print('missing', miss, 'bad', bad); sys.exit(1)
PY
  ok "effective cmdline (512-byte field only, no extra): beacon=2, forced permissive, slot _b, hardware, boot_devices, brd$( [ "$EINK" = 1 ] && echo ', a9ml.eink=1, firmware path')"
  # ext4 magic at 1080 (0x438) = 53 ef, raw (not sparse) images only
  for f in "$VENDOR" "$G_VENDOR"; do
    [ "$(od -A n -t x1 -j 1080 -N 2 "$f" | tr -d ' ')" = 53ef ] || fail "$f is not a raw ext4 image"
  done
  ok "vendor images are raw ext4"
  if [ "$WIFI" = 1 ]; then
    command -v debugfs >/dev/null || fail "--wifi requires read-only image inspection with debugfs"
    python3 -I "$REPO/scripts/mainline/wifi/verify-images.py" --boot "$BOOT" --vendor "$VENDOR" --dtbo "$DTBO" \
      || fail "Wi-Fi payload/source/binary/snapshot provenance mismatch"
  fi
  # the new vendor must never ship the things that write UFS partitions we protect
  if command -v debugfs >/dev/null; then
    for p in /etc/fstab.hlte556n; do
      debugfs -R "cat $p" "$VENDOR" 2>/dev/null | grep -v '^[[:space:]]*#' | grep -Eq '(userdata|metadata|persist|modemst|fsg|misc)[[:space:]]' \
        && fail "vendor $p names a protected partition"
    done
    for b in rmtfs tqftpserv android.hardware.boot-service.default android.hardware.boot@1.2-service; do
      debugfs -R "ls -p /bin/hw" "$VENDOR" 2>/dev/null | grep -q "/$b/" && fail "vendor ships $b (writes modem/misc partitions)"
      if [ "$WIFI" = 0 ] || { [ "$b" != rmtfs ] && [ "$b" != tqftpserv ]; }; then
        debugfs -R "ls -p /bin" "$VENDOR" 2>/dev/null | grep -q "/$b/" && fail "vendor ships $b"
      fi
    done
    if [ "$WIFI" = 1 ]; then
      ok "protected fstab guard retained; reviewed confined radio payload verified; no bootctl"
    else
      ok "vendor fstab names no protected partition; no rmtfs/tqftpserv/bootctl"
    fi
  fi
  for f in "$G_BOOT" "$G_DTBO"; do
    [ -f "$f" ] || fail "golden copy missing: $f"
    want=$(awk -v p="./lun4/$(basename "$f")" '$2==p{print $1}' "$GOLD/SHA256SUMS")
    [ -n "$want" ] && [ "$(sha "$f")" = "$want" ] || fail "golden $(basename "$f") does not match SHA256SUMS"
  done
  ok "golden boot_b.bin and dtbo_b.bin match SHA256SUMS"
  # super space: new vendor_b must fit in the group's free space + the old vendor_b
  if [ -f "$AW/golden/super-lpdump.txt" ]; then
    python3 -I "$HERE/super-fit.py" "$AW/golden/super-lpdump.txt" vendor_b "$(stat -c %s "$VENDOR")" \
      || fail "vendor image does not fit super (group qti_dynamic_partitions_b / device free space)"
    ok "vendor_b fits super"
  else
    fail "$AW/golden/super-lpdump.txt missing (extract-golden-vendor.sh writes it)"
  fi
  gpu_offline_checks
  [ "$EINK" = 1 ] && eink_offline_checks
  hal_offline_checks
  return 0
}

# Opt-in HALs (A9_HEALTH=real / A9_THERMAL_HAL=1, runs/android-thermal-health.md).
# Thermal HAL: only with the kernel it was paired with at build time
# (/etc/thermal-a9.kernel-image.sha256 == the boot image's kernel), a thermal
# boot DTB (quiet-therm zone), the reviewed config, and the host tests passing.
# Health: the patched binary must be the pinned one. Nothing here talks to the phone.
hal_offline_checks() {
  local t k pair tv hb
  command -v debugfs >/dev/null || return 0
  if debugfs -R "ls -p /bin/hw" "$VENDOR" 2>/dev/null | grep -q '/android.hardware.thermal-service.a9/'; then
    t=$(mktemp -d); unpack_bootimg --boot_img "$BOOT" --out "$t" >/dev/null
    k=$(sha "$t/kernel")
    grep -aq 'quiet-therm-thermal' "$t/dtb" 2>/dev/null || { rm -rf "$t"; fail "thermal HAL vendor, but the boot DTB has no quiet-therm zone (not a thermal kernel)"; }
    rm -rf "$t"
    pair=$(debugfs -R "cat /etc/thermal-a9.kernel-image.sha256" "$VENDOR" 2>/dev/null | tr -d '\n ')
    [ -n "$pair" ] && [ "$pair" = "$k" ] || fail "thermal HAL was paired with kernel ${pair:0:12}, boot kernel is ${k:0:12}"
    tv=$(mktemp -d)
    debugfs -R "dump /etc/thermal-a9.conf $tv/conf" "$VENDOR" >/dev/null 2>&1
    cmp -s "$tv/conf" "$HERE/thermal-hal/thermal-a9.conf" || { rm -rf "$tv"; fail "vendor's thermal-a9.conf differs from thermal-hal/thermal-a9.conf (rebuild the vendor)"; }
    rm -rf "$tv"
    debugfs -R "ls -p /apex" "$VENDOR" 2>/dev/null | grep -q '/com.android.hardware.thermal.apex/' && fail "thermal HAL vendor still ships the example thermal APEX"
    "$HERE/thermal-hal/tests/check-readonly.sh" >/dev/null || fail "thermal-hal/tests/check-readonly.sh failed"
    ok "thermal HAL: paired with the boot kernel (${k:0:12}), thermal boot DTB, reviewed config, example APEX gone, read-only audit"
  fi
  if debugfs -R "ls -p /bin/hw" "$VENDOR" 2>/dev/null | grep -q '/android.hardware.health-service.cuttlefish/'; then
    tv=$(mktemp -d)
    debugfs -R "dump /bin/hw/android.hardware.health-service.cuttlefish $tv/h" "$VENDOR" >/dev/null 2>&1
    hb=$(sha "$tv/h"); rm -rf "$tv"
    [ "$hb" = 13e186d508283150b0c9c4908114181478dec7cb46db66da60c51dd514653908 ] \
      || fail "vendor health binary ${hb:0:12} is not the pinned patched HAL (patch-health.py)"
    debugfs -R "ls -p /apex" "$VENDOR" 2>/dev/null | grep -q '/com.google.cf.health.apex/' && fail "real health vendor still ships the Cuttlefish health APEX"
    ok "health: patched HAL ${hb:0:12} (real power_supply values), cf health APEX gone"
  fi
  return 0
}

# GPU mode (runs/gpu.md): a GPU boot image (its DTB enables the Adreno and has
# the stock zap region memory@55615000) and a GPU vendor (ro.vendor.a9ml.gpu=1)
# only come as a pair: without the vendor's msm separate_gpu_kms=1 the msm KMS
# card would wait for the GPU and the E Ink transport would never appear; a GPU
# vendor needs the vkms.map_wc kernel and the gpucc module of the same build.
# vendor_cat <path>: a vendor image file's content via debugfs, or rc 1 after 3 tries. A read that
# failed (debugfs rc != 0, or anything on its stderr besides the version banner) is never taken as
# "absent": S2c-2's CHECK read ro.vendor.a9ml.gpu as 0 once, 86 s after its --offline read the same
# image as 1 (sensors-s2c2-result0.md); the debugfs error is now printed.
vendor_cat() {
  local i rc out err
  for i in 1 2 3; do
    err=$(mktemp)
    out=$(debugfs -R "cat $1" "$VENDOR" 2>"$err"); rc=$?
    if [ "$rc" = 0 ] && ! grep -vq '^debugfs [0-9]' "$err"; then rm -f "$err"; printf '%s\n' "$out"; return 0; fi
    echo "   !! debugfs cat $1 (try $i): rc=$rc $(grep -v '^debugfs [0-9]' "$err" | head -n 3 | tr '\n' ' ')" >&2
    rm -f "$err"; sleep 1
  done
  return 1
}
gpu_offline_checks() {
  local t k want dgpu=0 vgpu=0 bp mo
  t=$(mktemp -d); unpack_bootimg --boot_img "$BOOT" --out "$t" >/dev/null
  grep -aq 'memory@55615000' "$t/dtb" 2>/dev/null && dgpu=1
  if [ $dgpu = 1 ]; then
    # semantic DT check (review 27): GPU okay, zap region = stock, every stock
    # no-map region covered, no overlap, nothing the default Android DT
    # reserves released
    python3 -I "$HERE/gpu/dt-reserved-audit.py" "$t/dtb" "${A9_STOCK_BASE_DTS:-$HOME/A9-build/mainline/sol-pmic/evidence/base_0.dts}" \
      --baseline "${A9_BASE_ANDROID_DTB:-$AW/kernel-snap/sm6115-hisense-hlte556n-android.dtb}" --gpu-okay > "$t/audit" 2>&1 \
      || { cat "$t/audit"; rm -rf "$t"; fail "GPU DTB reserved-memory / GPU node audit failed"; }
  fi
  k=$(sha "$t/kernel"); rm -rf "$t"
  bp=$(vendor_cat /build.prop) && [ -n "$bp" ] || fail "cannot read the vendor's /build.prop (debugfs, 3 tries): GPU pairing not checked"
  grep -qx 'ro.vendor.a9ml.gpu=1' <<<"$bp" && vgpu=1
  [ $dgpu = $vgpu ] || fail "GPU pairing: boot DTB GPU=$dgpu but vendor ro.vendor.a9ml.gpu=$vgpu (use both -gpu images or neither)"
  [ $vgpu = 1 ] || return 0
  want=$(vendor_cat /lib/modules/kernel-image.sha256 | tr -d '\n ')
  [ "$k" = "$want" ] || fail "GPU vendor modules were built for kernel ${want:0:12}, boot kernel is ${k:0:12}"
  mo=$(vendor_cat /lib/modules/modules.options) || fail "cannot read the vendor's /lib/modules/modules.options (debugfs, 3 tries)"
  grep -qx 'msm.ko separate_gpu_kms=1' <<<"$mo" || fail "GPU vendor must load msm with separate_gpu_kms=1"
  # the INSTALLED helper (not only the source tree) must be the reviewed one
  local tv; tv=$(mktemp -d)
  debugfs -R "dump /bin/a9ml-gpu.sh $tv/a9ml-gpu.sh" "$VENDOR" >/dev/null 2>&1
  cmp -s "$tv/a9ml-gpu.sh" "$HERE/vendor/bin/a9ml-gpu.sh" || { rm -rf "$tv"; fail "vendor's /bin/a9ml-gpu.sh differs from vendor/bin/a9ml-gpu.sh (rebuild the vendor)"; }
  rm -rf "$tv"
  # review 34 F3: the vendor's E Ink variant must match the boot image's androidboot.a9ml.eink
  local var be; var=$(sed -n 's/^ro.vendor.a9ml.gpu.variant=//p' <<<"$bp")
  be=$(python3 -I -c "import sys;h=open(sys.argv[1],'rb').read(4096);c=h[64:576].split(b'\\0')[0].decode().split();print(1 if 'androidboot.a9ml.eink=1' in c else 0)" "$BOOT")
  case "$be:$var" in 1:eink|0:vkms) ;; *) fail "GPU pairing: boot androidboot.a9ml.eink=$be but vendor variant '${var:-none}' (vkms GPU boot needs vendor_b-gpu, E Ink GPU boot needs vendor_b-eink-gpu)" ;; esac
  "$HERE/tests/test-gpu-setup.sh" >/dev/null || fail "tests/test-gpu-setup.sh failed"
  "$HERE/tests/test-gpu-egl-props.sh" "$VENDOR" >/dev/null || fail "tests/test-gpu-egl-props.sh: the built GPU vendor pins apps to ANGLE (ro.hardware.egl in build.prop) or lacks the angle fallback"
  ok "GPU: boot DTB + vendor are a GPU pair, modules match the boot kernel (${k:0:12}), msm separate_gpu_kms=1, DT audit, installed helper = tested helper, vendor script host test"
}

eink_offline_checks() {
  "$HERE/tests/test-eink-setup.sh" >/dev/null || fail "tests/test-eink-setup.sh: the eink setup would lock/point the wrong KMS card"
  ok "eink setup host test: cards classified by sysfs path/driver (vkms untouched, msm root-only, composer on a9_eink)"
  "$HERE/tests/test-eink-gates.sh" >/dev/null || fail "tests/test-eink-gates.sh: the gate open/close logic is wrong (Y/N bools, refusals)"
  ok "eink gates host test: Y/N bool params, refusals, dry-run update, close read-back"
  python3 -I - "$BOOT" "$WIFI" <<'PY' || fail "eink boot image cmdline lacks androidboot.a9ml.eink=1 / firmware_class.path"
import sys
h = open(sys.argv[1], 'rb').read(4096)
c = h[64:576].split(b'\0')[0].decode().split()   # effective: the 512-byte field only
path = '/mnt/a9-wifi/firmware' if sys.argv[2] == '1' else '/data/local/tmp/a9fw'
sys.exit(0 if 'androidboot.a9ml.eink=1' in c and ('firmware_class.path=' + path) in c else 1)
PY
  local t k want; t=$(mktemp -d); unpack_bootimg --boot_img "$BOOT" --out "$t" >/dev/null
  k=$(sha "$t/kernel"); rm -rf "$t"; t=$(mktemp -u)
  want=$(debugfs -R "cat /lib/modules/kernel-image.sha256" "$VENDOR" 2>/dev/null | tr -d '\n ')
  [ -n "$want" ] || fail "eink vendor carries no /lib/modules/kernel-image.sha256 (build-vendor.sh --eink)"
  [ "$k" = "$want" ] || fail "boot kernel ${k:0:12} != the kernel the vendor's modules were built for (${want:0:12})"
  ok "eink: cmdline gate flag set, vendor modules match the boot kernel (${k:0:12})"
  # load order: dispcc before msm before panel/tc358767 before a9_eink (run 4)
  debugfs -R "cat /lib/modules/modules.load" "$VENDOR" 2>/dev/null | python3 -I -c '
import sys
l = [x.strip() for x in sys.stdin if x.strip()]
want = ["dispcc-sm6115.ko", "msm.ko", "panel-hisense-hlte556n-eink.ko", "tc358767.ko", "a9_eink.ko"]
miss = [w for w in want if w not in l]
pos = [l.index(w) for w in want if w in l]
sys.exit(0 if not miss and pos == sorted(pos) else (print("modules.load", l, "missing", miss) or 1))' \
    || fail "eink vendor modules.load must load dispcc-sm6115, msm, panel, tc358767, a9_eink in that order"
  ok "eink: module load order dispcc-sm6115 -> msm -> panel -> tc358767 -> a9_eink"
  # the snapshot must carry the E Ink safety patches the diskless sequencer
  # relies on: fp9929a (built in) max_on_ms/min_off_ms/pg_dip_policy in the
  # kernel image, a9_eink bin_min/bin_max (patch 0026) in the module
  local miss=""
  local eink_snap="$AW/kernel-snap"; [ "$WIFI" = 0 ] || eink_snap="${A9_WIFI_SNAPSHOT:-$AW/kernel-snap-wifi}"
  python3 -I -c 'import gzip,sys; d=gzip.decompress(open(sys.argv[1],"rb").read()); m=[k for k in ("max_on_ms","min_off_ms","pg_dip_policy") if k.encode() not in d]; print(" ".join(m)); sys.exit(1 if m else 0)' "$eink_snap/Image.gz" >/dev/null || miss="fp9929a(max_on_ms/min_off_ms/pg_dip_policy)"
  debugfs -R "dump /lib/modules/a9_eink.ko $t.ko" "$VENDOR" >/dev/null 2>&1
  python3 -I -c 'import sys; d=open(sys.argv[1],"rb").read(); sys.exit(0 if all(k in d for k in (b"bin_min", b"bin_max", b"clear_fault", b"frame-progress fault")) else 1)' "$t.ko" 2>/dev/null || miss="$miss a9_eink(bin_min/bin_max/stall fault, 0026)"
  python3 -I -c 'import sys; d=open(sys.argv[1],"rb").read(); sys.exit(0 if all(k in d for k in (b"a9-ctrl-template.bin", b"late_drive", b"max_gap_drive_us")) else 1)' "$t.ko" 2>/dev/null || miss="$miss a9_eink(control template + drive counters, 0028-0030)"
  rm -f "$t.ko"
  if [ -n "$miss" ]; then
    if [ "$OPEN_GATES" = 1 ]; then fail "kernel snapshot lacks E Ink safety patches: $miss (rebuild A9_ANDROID=1, snapshot-kernel.sh, both images)"; fi
    echo "  WARN kernel snapshot lacks E Ink safety patches: $miss -- fine with gates closed; --open-gates would be refused (vendor refuses too)"
  else
    ok "eink: kernel carries the E Ink safety params (fp9929a caps, a9_eink bins)"
  fi
  if [ -n "$WAVEFORM" ]; then
    [ -f "$WAVEFORM" ] || fail "no waveform file $WAVEFORM"
    case "$(readlink -f "$WAVEFORM")" in "$REPO"/*) fail "the waveform blob must live outside the git repo" ;; esac
    [ "$(head -c 4 "$WAVEFORM")" = A9WF ] || fail "$WAVEFORM is not an A9WF blob"
    [ -x "$HERE/eink-fw-install.sh" ] || fail "missing $HERE/eink-fw-install.sh (root install of the blobs)"
    ok "waveform $(basename "$WAVEFORM") ($(stat -c %s "$WAVEFORM") B, A9WF), installed at runtime only (as root, eink-fw-install.sh)"
  fi
  if [ -n "$TEMPLATE" ]; then
    [ -f "$TEMPLATE" ] || fail "no template file $TEMPLATE"
    case "$(readlink -f "$TEMPLATE")" in "$REPO"/*) fail "the control template must live outside the git repo" ;; esac
    python3 -I - "$TEMPLATE" "$REPO/scripts/mainline/eink/driver/a9_eink_engine.h" <<'PY' || fail "$TEMPLATE is not the control template the kernel accepts"
import hashlib, re, struct, sys
b = open(sys.argv[1], 'rb').read()
want = re.search(r'#define A9E_CT_SHA256 "([0-9a-f]{64})"', open(sys.argv[2]).read()).group(1)
magic, ver, hsz, psz = struct.unpack_from('<4I', b)
pay = b[hsz:hsz + psz]
ok = magic == 0x54433941 and ver == 1 and len(pay) == psz and hashlib.sha256(pay).hexdigest() == want == b[48:80].hex()
print(f'  template: magic {magic:#x} v{ver} payload {psz} B sha256 {hashlib.sha256(pay).hexdigest()[:12]} (kernel wants {want[:12]})')
sys.exit(0 if ok else 1)
PY
    ok "control template $(basename "$TEMPLATE") matches the kernel's A9E_CT_SHA256, pushed at runtime only"
  fi
  if [ "$OPEN_GATES" = 1 ]; then
    [ "${A9_EINK_E5C_PASSED:-}" = yes ] || fail "--open-gates refused: set A9_EINK_E5C_PASSED=yes only after stage E5c passed on the diskless image"
    ok "gates will open for ${GATE_SECS}s after boot_completed (E5c passed, per the operator)"
  else
    ok "gates stay CLOSED (no --open-gates)"
  fi
}

# hold_loop <dir> [card]: keep the phone on the test image (--hold). Returns
# when the hold ends; sets EINK_FAULT on a fault, HOLD_LOST when the phone
# went away. Gates (if open) are closed before returning, unless a fault
# already closed them.
hold_close() {
  [ -n "${HOLD_CARD:-}" ] && [ -z "${EINK_FAULT:-}" ] && timeout 10 "$ADB" shell setprop vendor.a9ml.eink.gate closed </dev/null >/dev/null 2>&1
  HOLD_CARD=""
}
s2_quiet_hold() {
  local end=$(( $(date +%s) + HOLD ))
  HOLD_STOP=0
  trap 'HOLD_STOP=1; [ -z "${HOLD_SLEEP:-}" ] || kill "$HOLD_SLEEP" 2>/dev/null || :' INT TERM HUP
  echo 'S2_QUIET_READY'
  while [ "$(date +%s)" -lt "$end" ] && [ "$HOLD_STOP" = 0 ]; do
    [ "$(timeout 10 "$ADB" get-state 2>/dev/null </dev/null)" = device ] || { HOLD_LOST=1; break; }
    stay
    sleep 5 </dev/null & HOLD_SLEEP=$!; wait "$HOLD_SLEEP" 2>/dev/null || :; HOLD_SLEEP=
  done
  trap - INT TERM HUP
}
hold_loop() {
  if [ "$S2_QUIET" = 1 ]; then s2_quiet_hold; return; fi
  local d="$1" card="${2:-}" end last=0 up g miss=0
  HOLD_CARD="$card"
  trap 'echo "   hold interrupted: closing the gates"; kill "${HOLD_SLEEP:-0}" 2>/dev/null; hold_close; print_restore; exit 0' INT TERM HUP
  up=$(timeout 5 "$ADB" shell cut -d. -f1 /proc/uptime </dev/null | tr -d '\r')
  timeout 10 "$ADB" shell setprop vendor.a9ml.maxstay $(( ${up:-0} + HOLD + 600 )) </dev/null
  end=$(( $(date +%s) + HOLD ))
  echo "-- HOLD: the phone stays on the test image for up to ${HOLD}s (until $(date -u -d "@$end" +%H:%M) UTC); Ctrl-C ends it"
  print_restore
  mkdir -p "$d/hold"
  local last5=0 kd
  while [ "$(date +%s)" -lt "$end" ]; do
    if [ "$(a9_adb_state)" != device ]; then
      miss=$((miss+1)); [ "$miss" -ge 30 ] && { HOLD_LOST=1; echo "   !! phone lost for 5 min: hold ends (the dead-man returns it to fastboot)"; break; }
    else
      miss=0; stay
    fi
    if [ -n "$card" ]; then
      g=$(timeout 5 "$ADB" shell getprop vendor.a9ml.eink.gate </dev/null | tr -d '\r')
      case "$g" in
        faulted|refused)
          EINK_FAULT="$g: $(timeout 5 "$ADB" shell getprop vendor.a9ml.eink.fault </dev/null | tr -d '\r')"
          echo "   !! E Ink session stopped: $EINK_FAULT (gates closed by the vendor; the hold ends)"
          # the fault's kernel lines first, before anything else can drop USB
          kd="$d/hold/fault-$(date -u +%H%M%S)"
          timeout 30 "$ADB" shell dmesg > "$kd-dmesg.txt" 2>&1 </dev/null
          timeout 10 "$ADB" shell "cat /sys/class/drm/$card-*/eink/status" > "$kd-eink-status.txt" 2>&1 </dev/null
          timeout 30 "$ADB" pull /data/local/tmp/a9ml-fault-dmesg.txt "$kd-vendor-dmesg.txt" >/dev/null 2>&1
          timeout 30 "$ADB" shell "logcat -d -b all | tail -n 2000" > "$kd-logcat.txt" 2>&1 </dev/null
          echo "   fault evidence: $kd-*"
          break ;;
      esac
    fi
    # every 30 s: E Ink kmsg tail + status (rolling "latest"), every 5 min a kept copy
    if [ $(( $(date +%s) - last )) -ge 30 ]; then
      last=$(date +%s)
      if [ -n "$card" ]; then
        timeout 10 "$ADB" shell "cat /sys/class/drm/$card-*/eink/status" > "$d/hold/latest-eink-status.txt" 2>&1 </dev/null
        # Lines 4-6: pacing plus 0036 flip phases, timestamped every 30 s.
        stamp=$(date -u +%H:%M:%S)
        sed -n '4,6p' "$d/hold/latest-eink-status.txt" | while IFS= read -r line; do
          printf '%s %s\n' "$stamp" "$line"
        done >> "$d/hold/pace.log"
      fi
      timeout 30 "$ADB" shell "dmesg | grep -i -e a9_eink -e a9-eink -e a9ml -e fp9929a -e frontlight -e msm -e tc358767 | tail -n 300" > "$d/hold/latest-kmsg.txt" 2>&1 </dev/null
      if [ $(( last - last5 )) -ge 300 ]; then
        last5=$last; f="$d/hold/$(date -u +%H%M%S)"
        cp "$d/hold/latest-kmsg.txt" "$f-kmsg.txt" 2>/dev/null; cp "$d/hold/latest-eink-status.txt" "$f-eink-status.txt" 2>/dev/null
      fi
    fi
    # sleep in the background + wait: a TERM/INT/HUP trap then runs at once instead of
    # after the sleep, and the trap can kill the sleep (no orphan outliving the script)
    sleep 10 </dev/null & HOLD_SLEEP=$!; wait "$HOLD_SLEEP" 2>/dev/null; HOLD_SLEEP=
  done
  hold_close; trap - INT TERM HUP
  [ -s "$d/hold/pace.log" ] && { echo "   pace + flip phases (status lines 4-6), last samples:"; tail -n 9 "$d/hold/pace.log" | sed 's/^/     /'; }
}
print_restore() {
  echo "   final golden restore: $REPO/scripts/mainline/android/android-boot.sh --restore"
  [ "$NORESTORE" = 1 ] && echo "   or keep slot B for the next iteration and only go back to slot a: $REPO/scripts/mainline/android/android-boot.sh --to-slot-a"
}

# This is the only route from eink_session to GPU admission / gate request.
# EOF (parent death), timeout or any other record refuses without admission.
latency_prepared() {
  local record
  echo "-- SAFE3 PREPARATION BARRIER: admission has not started"
  IFS= read -r -t 600 record <&"$LATENCY_PREP_FD" || fail "safe3 preparation pipe EOF/timeout"
  [ "$record" = PREPARED_LAUNCHER3_VERIFIED_V1 ] || fail "safe3 invalid preparation record"
  exec {LATENCY_PREP_FD}<&-
  echo "-- SAFE3 PREPARATION ACCEPTED"
}

# eink_session <dir>: phase-2 steps on the running mainline Android
eink_session() {
  local d="$1" card g; mkdir -p "$d"; HOLD_LOST=""
  card=$(timeout 5 "$ADB" shell getprop vendor.a9ml.eink.card </dev/null | tr -d '\r')
  echo "   a9_eink card: ${card:-none} (vendor.a9ml.eink.ready=$(timeout 5 "$ADB" shell getprop vendor.a9ml.eink.ready </dev/null | tr -d '\r'))"
  [ -n "$card" ] || return 1
  if [ -n "$WAVEFORM" ]; then
    # G2a 2026-10-16: install AS ROOT into the root-owned a9fw tree, verified
    # by sha256 on the phone (eink-fw-install.sh). A failure is a hard stop:
    # no dry run, no gates, the run goes straight to reboot + restore.
    local rc=0
    ADB="$ADB" "$HERE/eink-fw-install.sh" "$WAVEFORM" ${TEMPLATE:+"$TEMPLATE"} 2>&1 | tee "$d/fw-install.txt"
    rc=${PIPESTATUS[0]}
    if [ "$rc" != 0 ]; then
      EINK_FAULT="blob-install rc=$rc"
      echo "   !! E Ink session stopped: waveform/template NOT installed (eink-fw-install.sh rc $rc); no dry run, no gates"
      return 2
    fi
    sleep 5
  fi
  timeout 10 "$ADB" shell "cat /sys/class/drm/$card-*/eink/status" > "$d/eink-status-before.txt" 2>&1 </dev/null
  # review 38 R2 (G2a): a9_eink attaches its transport on msm lazily, at the
  # first update. With the gates CLOSED on the GPU vendor, kick one DRY-RUN
  # update (force_clear with a9_eink.enable 0: computed and hashed, no commit,
  # no HV; the same step a9ml-eink.sh open runs before any gate opens), so the
  # transport attach is observed with the gates closed.
  if [ "$OPEN_GATES" = 0 ] && [ -n "$WAVEFORM" ] && [ -n "$TEMPLATE" ] \
     && [ "$(timeout 5 "$ADB" shell getprop ro.vendor.a9ml.gpu </dev/null | tr -d '\r')" = 1 ]; then
    eink_dry_run "$d" "$card"
  fi
  if { [ "$OPEN_GATES" = 1 ] || [ "$GHOST_CLOSED_PROBE" = 1 ]; } && [ "$REQUIRE_PREPARATION" = 1 ]; then latency_prepared; fi
  if [ "$GHOST_CLOSED_PROBE" = 1 ]; then
    echo "-- GHOST CLOSED PROBE ADMITTED (no gate request)"
    return 0
  fi
  if [ "$OPEN_GATES" = 1 ]; then
    echo "-- opening the E Ink gates for ${GATE_SECS}s, bins $BINS (vendor.a9ml.eink.gate=open)"
    [ "$HOLD" -gt 0 ] && timeout 10 "$ADB" shell setprop vendor.a9ml.eink.hv_hold default </dev/null
    timeout 10 "$ADB" shell setprop vendor.a9ml.eink.bins "$BINS" </dev/null
    if [ -n "${GHOST_ATTEMPT_OUT:-}" ]; then
      python3 -B "$REPO/scripts/eink-probe/sessions/ghost_attempt.py" request --out "$GHOST_ATTEMPT_OUT" || fail "gate request accounting failed"
    fi
    timeout 10 "$ADB" shell setprop vendor.a9ml.eink.gate open </dev/null
    sleep 3
    g=$(timeout 5 "$ADB" shell getprop vendor.a9ml.eink.gate </dev/null | tr -d '\r')
    if [ -n "${GHOST_ATTEMPT_OUT:-}" ]; then
      { printf 'GHOST_GATE %s\n' "$g"; su_ 'cat /sys/module/a9_eink/parameters/enable /sys/module/fp9929a/parameters/allow_power_on /sys/module/panel_hisense_hlte556n_eink/parameters/enable'; } > "$d/gates-open-readback.txt"
      python3 -B "$REPO/scripts/eink-probe/sessions/ghost_attempt.py" opened --out "$GHOST_ATTEMPT_OUT" --evidence "$d/gates-open-readback.txt" || fail "gate-open readback/accounting failed; outcome pending"
    fi
    [ "$g" = refused ] && echo "   !! the vendor REFUSED to open the gates (see eink-dmesg.txt)"
    [ "$HOLD" -gt 0 ] && { hold_loop "$d" "$card"; return 0; }
    local end=$(( $(date +%s) + GATE_SECS )) gpuv nextp=0
    gpuv=$(timeout 5 "$ADB" shell getprop ro.vendor.a9ml.gpu </dev/null | tr -d '\r')
    while [ "$(date +%s)" -lt "$end" ]; do
      stay; sleep 5
      # GPU vendor (runs/gpu.md, G2b): the pace line every 30 s, as the hold does
      if [ "$gpuv" = 1 ] && [ "$(date +%s)" -ge "$nextp" ]; then
        nextp=$(( $(date +%s) + 30 ))
        echo "$(date -u +%FT%TZ) $(timeout 10 "$ADB" shell "cat /sys/class/drm/$card-*/eink/status" </dev/null 2>/dev/null | sed -n 4p | tr -d '\r')" >> "$d/pace.log"
      fi
      g=$(timeout 5 "$ADB" shell getprop vendor.a9ml.eink.gate </dev/null | tr -d '\r')
      case "$g" in
        faulted|refused)
          EINK_FAULT="$g: $(timeout 5 "$ADB" shell getprop vendor.a9ml.eink.fault </dev/null | tr -d '\r')"
          echo "   !! E Ink session stopped: $EINK_FAULT (gates closed by the vendor; no clear_fault; the run stops)"
          break ;;
      esac
    done
    timeout 10 "$ADB" shell "cat /sys/class/drm/$card-*/eink/status" > "$d/eink-status-open.txt" 2>&1 </dev/null
    # GPU vendor: does the glass hold Android's GPU-rendered frame? (settled frame, gates still open)
    if [ "${gpuv:-}" = 1 ] && [ -z "${EINK_FAULT:-}" ]; then
      sleep 10; "$HERE/eink-compare.sh" "$d/compare" > "$d/compare.log" 2>&1; echo $? > "$d/compare.rc"
      echo "   eink-compare (GPU): rc $(cat "$d/compare.rc"), $(grep -m1 -iE 'exact|psnr' "$d/compare.log" | tr -s ' ')"
    fi
    # close unless the vendor already closed on a fault (gate stays 'faulted')
    [ -n "${EINK_FAULT:-}" ] || timeout 10 "$ADB" shell setprop vendor.a9ml.eink.gate closed </dev/null
    sleep 3
    echo "   gates after close (kernel bools print Y/N): a9_eink.enable=$(timeout 5 "$ADB" shell cat /sys/module/a9_eink/parameters/enable </dev/null | tr -d '\r') fp9929a.allow_power_on=$(timeout 5 "$ADB" shell cat /sys/module/fp9929a/parameters/allow_power_on </dev/null | tr -d '\r')"
  fi
  timeout 10 "$ADB" shell "cat /sys/class/drm/$card-*/eink/status" > "$d/eink-status-after.txt" 2>&1 </dev/null
  timeout 30 "$ADB" shell "dmesg | grep -i -e a9_eink -e a9ml-eink -e fp9929a -e tc358767 -e hlte556n" > "$d/eink-dmesg.txt" 2>&1 </dev/null
  # review 38 R3: the WHOLE kmsg after the session (the gates-open window
  # included), as root; gpu-supervise.sh applies its stop patterns to it
  su_ dmesg > "$d/dmesg-after.txt"
}
# eink_dry_run <dir> <card>: one dry-run update with every gate verified
# closed before and after (root). Never opens a gate; refuses on any doubt.
eink_dry_run() {
  local d="$1" card="$2" g i st
  eink_gates_rd() { su_ "cat /sys/module/a9_eink/parameters/enable /sys/module/panel_hisense_hlte556n_eink/parameters/enable /sys/module/fp9929a/parameters/allow_power_on" | tr -d '\r' | tr '\n' ' '; }
  g=$(eink_gates_rd); echo "gates before: $g" > "$d/dryrun.txt"
  case "$g" in "N N N "|"0 0 0 ") ;; *) echo "   !! dry run skipped: gates not all closed ($g)"; echo "skipped: gates not closed" >> "$d/dryrun.txt"; return 1 ;; esac
  su_ 'for f in /sys/class/drm/'"$card"'-*/eink/force_clear; do echo 1 > $f && echo kicked $f; break; done' >> "$d/dryrun.txt"
  for i in $(seq 1 20); do
    st=$(timeout 10 "$ADB" shell "cat /sys/class/drm/$card-*/eink/status" </dev/null 2>/dev/null | tr -d '\r')
    printf '%s\n' "$st" | tr ' \n' '\n\n' | awk 'p=="transport"{t=$0} p=="waveform"{w=$0} p=="template"{c=$0} {p=$0} END{exit !(t==1 && w==1 && c==1)}' && break
    sleep 1
  done
  printf '%s\n' "$st" > "$d/eink-status-dryrun.txt"
  echo "gates after: $(eink_gates_rd)" >> "$d/dryrun.txt"
  echo "   dry-run update (gates closed): $(head -n1 "$d/eink-status-dryrun.txt" | cut -c1-120)"
}

# lp_readback <logical partition> <image>: hash the partition's extents in
# super ON THE PHONE (Android, su) and compare. Works for the non-active slot,
# whose partitions are not mapped in /dev/block/mapper.
lp_readback() {
  local part="$1" img="$2" want got size ext
  want=$(awk '{print $1}' "$img.sha256" 2>/dev/null); [ -n "$want" ] || want=$(sha "$img")
  size=$(stat -c %s "$img")
  # lpdump extent lines: "<lstart> .. <lend> linear super <pstart>"
  ext=$(a9_su lpdump | awk -v p="$part" '
    $1=="Name:" {cur=$2; next}
    cur==p && $2==".." && $4=="linear" {print $1, $3, $6}')
  [ -n "$ext" ] || { echo "  no extents for $part in lpdump"; return 1; }
  local cmd="" ls le ps n
  while read -r ls le ps; do
    n=$(( le - ls + 1 ))
    if [ $(( ps % 2048 )) = 0 ] && [ $(( n % 2048 )) = 0 ]; then   # 1 MiB aligned: fast path
      cmd="$cmd dd if=/dev/block/by-name/super bs=1048576 skip=$(( ps / 2048 )) count=$(( n / 2048 )) 2>/dev/null;"
    elif [ $(( ps % 8 )) = 0 ] && [ $(( n % 8 )) = 0 ]; then
      cmd="$cmd dd if=/dev/block/by-name/super bs=4096 skip=$(( ps / 8 )) count=$(( n / 8 )) 2>/dev/null;"
    else
      cmd="$cmd dd if=/dev/block/by-name/super bs=512 skip=$ps count=$n 2>/dev/null;"
    fi
  done <<< "$ext"
  a9_su "echo 3 > /proc/sys/vm/drop_caches"   # dm writes bypass super's page cache
  got=$(a9_su "( $cmd ) | head -c $size | sha256sum" | awk '{print $1}')
  if [ "$got" = "$want" ]; then echo "  MATCH  $part = $(basename "$img")"; return 0; fi
  echo "  MISMATCH  $part: phone ${got:0:16}, image ${want:0:16}"; return 1
}

phone_checks() {
  echo "== phone checks (read-only, Android slot a)"
  H=$("$REPO/scripts/a9-health.sh" --json); HRC=$?
  case "$H" in *'"slot":"_a"'*) ;; *) fail "phone must be in Android on slot a: $H" ;; esac
  [ "$HRC" = 0 ] || fail "Android on slot a has not finished booting"
  ok "Android, slot a, boot completed"
  LVL=$(timeout 10 "$ADB" shell dumpsys battery 2>/dev/null | tr -d '\r' | sed -n 's/^ *level: //p')
  [ -n "$LVL" ] && [ "$LVL" -ge 60 ] || fail "battery ${LVL:-?} % < 60 % (mainline neither charges nor monitors the battery)"
  ok "battery $LVL %"
  local p
  for p in boot_b:$G_BOOT dtbo_b:$G_DTBO; do
    if "$REPO/scripts/a9-readback.sh" "${p%%:*}" "${p#*:}" >/dev/null; then ok "${p%%:*} = golden"
    elif [ "$NORESTORE" = 1 ]; then
      echo "  note ${p%%:*} holds a non-golden (previous test) build: $(phone_part_hash "${p%%:*}" | cut -c1-16)... (recorded; --no-restore iterate path)"
    else fail "${p%%:*} on the phone is not the golden copy (use --no-restore to iterate, or --restore first)"; fi
  done
  if [ "${1:-}" != staged ]; then
    if lp_readback vendor_b "$G_VENDOR" >/dev/null; then ok "vendor_b = golden"
    elif [ "$NORESTORE" = 1 ]; then echo "  note vendor_b holds a non-golden (previous test) vendor (recorded)"
    else fail "vendor_b on the phone is not the golden copy (restore source would be wrong; --no-restore to iterate)"; fi
  else
    lp_readback vendor_b "$VENDOR" || fail "vendor_b on the phone is not the new vendor image"
  fi
  BCB=$(a9_su 'dd if=/dev/block/by-name/misc bs=32 count=1 2>/dev/null' | tr -d '\000' | tr -cd '[:print:]')
  case "$BCB" in boot-recovery*|boot-fastboot*) fail "misc BCB says '$BCB': run scripts/twrp.sh exit first" ;; esac
  ok "misc BCB '${BCB:-<empty>}'"
}

# phone_part_hash <physical partition>: sha256 of the whole partition, also
# appended to the run dir's prior-state.txt when a run dir exists
phone_part_hash() {
  local h; h=$(a9_su "sha256sum /dev/block/by-name/$1" | awk '{print $1}')
  [ -n "${A9_RUN:-}" ] && echo "$1 $h" >> "$A9_RUN/prior-state.txt"
  echo "$h"
}

# to_slot_a: from mainline Android on b, the bootloader or fastbootd, make
# slot a active and boot it. No partition writes (slot B keeps its images).
to_slot_a() {
  echo "== switch to slot a (no writes; slot B keeps the test build)"
  local end=$(( $(date +%s) + ${A9M_RESTORE_WAIT:-1800} )) s
  while [ "$(date +%s)" -lt "$end" ]; do
    case "$(a9_adb_state)" in device|recovery) break ;; esac
    timeout 5 "$FB" devices 2>/dev/null </dev/null | grep -q fastboot && break
    sleep 3
  done
  if [ "$(a9_adb_state)" = device ]; then
    s=$(timeout 5 "$ADB" shell getprop ro.boot.slot_suffix </dev/null | tr -d '\r')
    case "$(timeout 5 "$ADB" shell uname -r </dev/null | tr -d '\r')" in
      7.*) ;;
      *) [ "$s" = _a ] && { echo "   already in Android on slot a"; "$REPO/scripts/a9-health.sh"; return 0; } ;;
    esac
    "$ADB" reboot bootloader
  elif [ "$(a9_adb_state)" = recovery ]; then "$ADB" reboot bootloader; fi
  if ! a9_wait_fastboot no 180; then
    if a9_wait_fastboot yes 5; then "$FB" reboot bootloader; a9_wait_fastboot no 120; fi
  fi
  a9_wait_fastboot no 5 || { echo "!! no bootloader fastboot: follow the a9-recover skill"; return 1; }
  [ "$(a9_fbvar slot-successful:a)" = yes ] || echo "   WARNING slot a not reported successful"
  "$FB" set_active a && [ "$(a9_fbvar current-slot)" = a ] || { echo "!! set_active a failed"; return 1; }
  "$FB" reboot
  a9_wait_boot 600 || { echo "!! slot a did not boot: a9-recover skill"; return 1; }
  "$REPO/scripts/a9-health.sh"
  echo "   slot B keeps the test build; final golden restore: $REPO/scripts/mainline/android/android-boot.sh --restore"
}
# finish: the end of a --write run: golden restore, or with --no-restore only
# the switch to slot a
finish() { if [ "$NORESTORE" = 1 ]; then to_slot_a; else restore; fi; }

bootloader_checks() {   # in bootloader fastboot, before set_active b
  [ "$(a9_fbvar is-userspace)" = no ] || return 1
  [ "$(a9_fbvar current-slot)" = a ] || { echo "current-slot is not a"; return 1; }
  [ "$(a9_fbvar slot-successful:a)" = yes ] || { echo "slot a not successful: the fallback to a would fail"; return 1; }
  [ "$(a9_fbvar slot-unbootable:a)" = no ] || { echo "slot a unbootable"; return 1; }
  s=$(a9_fbvar snapshot-update-status); case "$s" in ""|none) ;; *) echo "snapshot-update-status $s"; return 1 ;; esac
  timeout 10 "$FB" oem device-info 2>&1 | tr -d '\r' | grep -qi 'charger screen enabled: false' \
    || { echo "charger screen not reported disabled"; return 1; }
}

# flash_vendor_b <img>: write vendor_b from Android slot a with lp-write.sh
# (fastbootd only for a resize; the data goes through a dmctl RW mapping of
# vendor_b's own extents). fastbootd's `flash vendor_b` resized and then wrote
# nothing on 2026-10-15 (docs section 5.1). The lp-write code is derived here:
# this script's own --code already approved these images.
flash_vendor_b() {
  local img="$1" c
  c=$("$HERE/lp-write.sh" "$img" | tee /dev/stderr | sed -n 's/.*code: \([0-9a-f]*\).*/\1/p')
  [ -n "$c" ] || { echo "lp-write check failed"; return 1; }
  "$HERE/lp-write.sh" "$img" --write --code "$c"
}

restore() {
  echo "== restore: golden boot_b + dtbo_b, slot a, golden vendor_b"
  # the phone may still be rebooting (e.g. back from fastbootd): wait for any
  # mode first (fixes the 2026-10-15 race), up to 30 min (the vendor dead-man)
  local end=$(( $(date +%s) + ${A9M_RESTORE_WAIT:-1800} ))
  while [ "$(date +%s)" -lt "$end" ]; do
    case "$(a9_adb_state)" in device|recovery) break ;; esac
    timeout 5 "$FB" devices 2>/dev/null </dev/null | grep -q fastboot && break
    sleep 3
  done
  # an Android still booting (stock, slot a) gets up to 300 s; our mainline
  # test image (kernel 7.x or ro.a9ml.vendor=1) may never complete boot (e.g. a
  # SurfaceFlinger crash loop), so it goes to the bootloader at once
  if [ "$(a9_adb_state)" = device ]; then
    local kr mv
    kr=$(timeout 5 "$ADB" shell uname -r </dev/null 2>/dev/null | tr -d '\r')
    mv=$(timeout 5 "$ADB" shell getprop ro.a9ml.vendor </dev/null 2>/dev/null | tr -d '\r')
    case "$kr" in
      7.*) echo "   mainline test image (kernel $kr): no boot wait" ;;
      *) if [ "$mv" = 1 ]; then echo "   mainline test vendor: no boot wait"; else a9_wait_boot 300 || true; fi ;;
    esac
  fi
  case "$(a9_adb_state)" in device|recovery) "$ADB" reboot bootloader ;; esac
  if ! a9_wait_fastboot no 180; then
    if a9_wait_fastboot yes 5; then "$FB" reboot bootloader; a9_wait_fastboot no 120; fi
  fi
  a9_wait_fastboot no 5 || { echo "!! no bootloader fastboot: follow the a9-recover skill"; return 1; }
  "$FB" flash boot_b "$G_BOOT" || return 1
  "$FB" flash dtbo_b "$G_DTBO" || return 1
  "$FB" set_active a && [ "$(a9_fbvar current-slot)" = a ] || { echo "!! set_active a failed"; return 1; }
  "$FB" reboot
  a9_wait_boot 600 || { echo "!! slot a did not boot: a9-recover skill"; return 1; }
  sleep 15
  local rc=0
  "$REPO/scripts/a9-readback.sh" boot_b "$G_BOOT" || rc=1
  "$REPO/scripts/a9-readback.sh" dtbo_b "$G_DTBO" || rc=1
  if ! lp_readback vendor_b "$G_VENDOR" >/dev/null; then
    flash_vendor_b "$G_VENDOR" || { echo "!! golden vendor_b flash failed; slot a is fine, B's Android needs it"; return 1; }
    lp_readback vendor_b "$G_VENDOR" || rc=1
  else
    echo "  vendor_b already golden"
  fi
  "$REPO/scripts/a9-health.sh"
  return $rc
}

# collect <dir>: everything worth keeping from the mainline Android (adb as
# shell; root via `su` is absent, the GSI is debuggable: adb root is tried)
collect() {
  local d="$1" c f; mkdir -p "$d"
  # NO `adb root`: it restarts adbd, which unbinds the gadget (boot 3 lost adb
  # here, 2026-10-15). Root reads go through the GSI's su when it works.
  stay
  sh_() { timeout 60 "$ADB" shell "$1" </dev/null 2>&1; }
  root_check >/dev/null 2>&1
  su_() { if [ "${ROOT:-}" = nc ]; then timeout 60 "$ADB" shell "echo '$1' | nc -w 30 127.0.0.1 2324" </dev/null 2>&1; else sh_ "$1"; fi; }
  # mini bundle first: whatever happens next, these exist
  sh_ getprop > "$d/getprop.txt"; su_ dmesg > "$d/dmesg.txt"; sh_ "logcat -d -b all" > "$d/logcat-all.txt"
  echo "   mini bundle: $(wc -c < "$d/getprop.txt") B getprop, $(wc -c < "$d/dmesg.txt") B dmesg, $(wc -c < "$d/logcat-all.txt") B logcat"
  stay
  for c in "uname -a" "ps -A -o PID,PPID,USER,STAT,NAME" "mount" \
           "ls -l /dev/dri /dev/binderfs /dev/block/by-name" "cat /proc/meminfo" "dumpsys SurfaceFlinger --displays" \
           "dumpsys SurfaceFlinger" "service list" "lshal --neat" "cat /sys/kernel/debug/dri/0/state" \
           "cat /sys/class/drm/*/status" "getenforce" "cat /proc/cmdline" "ls -l /config/usb_gadget/g1/configs/b.1" \
           "cat /config/usb_gadget/g1/UDC"; do
    f="$d/$(echo "$c" | tr -c 'A-Za-z0-9' '_' | cut -c1-60).txt"
    su_ "$c" > "$f"
  done
  timeout 30 "$ADB" exec-out screencap -p > "$d/screencap.png" 2>/dev/null </dev/null
  # GPU vendor only (runs/gpu.md): the qualification record, module state and
  # the GPU/msm/SMMU kmsg; read-only, no new GPU opens
  if [ "$(sh_ 'getprop ro.vendor.a9ml.gpu' | tr -d '\r')" = 1 ]; then
    su_ "cat /data/local/tmp/a9ml-gpu.log" > "$d/a9ml-gpu.log"
    su_ "cat /data/local/tmp/a9ml-gpu.qual" > "$d/a9ml-gpu.qual"
    su_ "cat /proc/modules" > "$d/proc_modules.txt"
    su_ "cat /sys/module/vkms/parameters/map_wc /sys/module/msm/parameters/separate_gpu_kms" > "$d/gpu-params.txt"
    su_ "ls -l /dev/dri; ls -l /sys/class/drm" > "$d/gpu-drm.txt"
    # review 38 R1: debugfs is not mounted on this Android; mount it (as
    # eink-compare.sh does) before reading the DRM atomic states. Layout:
    # dri/<minor> (symlink) and dri/<unique> (dir), both with name + state
    su_ "mountpoint -q /sys/kernel/debug || mount -t debugfs debugfs /sys/kernel/debug; mountpoint /sys/kernel/debug" > "$d/debugfs-mount.txt"
    su_ 'for d in /sys/kernel/debug/dri/*; do echo "== $d $(cat $d/name 2>/dev/null)"; cat $d/state 2>/dev/null; done' > "$d/dri-states.txt"
    su_ "cat /sys/class/devfreq/*/cur_freq /sys/class/devfreq/*/trans_stat" > "$d/gpu-devfreq.txt"
    su_ "top -b -n 2 -d 5" > "$d/top.txt"
    echo "   GPU: state $(sh_ 'getprop vendor.a9ml.gpu.state' | tr -d '\r'), qual $(tr -d '\r' < "$d/a9ml-gpu.qual")"
  fi
  stay
  echo "   collected into $d"
}
# root_check: the test vendor's development root shell (init.hlte556n.rc):
# adbd as root (ro.secure=0 kept by securize_disable), else the loopback
# root shell (started at boot_completed). Non-fatal.
root_check() {
  local u r
  u=$(timeout 10 "$ADB" shell id -u </dev/null 2>/dev/null | tr -d '\r')
  if [ "$u" = 0 ]; then echo "   root: adbd runs as root (adb shell id -u = 0)"; ROOT=adbd; return 0; fi
  r=$(timeout 10 "$ADB" shell "echo id -u | nc -w 3 127.0.0.1 2324" </dev/null 2>/dev/null | tr -d '\r' | tail -n1)
  if [ "$r" = 0 ]; then echo "   root: adbd is uid ${u:-?}; loopback root shell OK (adb shell 'echo CMD | nc 127.0.0.1 2324')"; ROOT=nc; return 0; fi
  echo "   WARN no root shell yet (adbd uid ${u:-?}; the loopback shell starts at boot_completed); 'adb root' is the manual next step"
  ROOT=""; return 1
}
# stay: refresh the vendor dead-man's hold file (it only holds while fresh, 600 s)
stay() { timeout 10 "$ADB" shell touch /data/local/tmp/a9-stay </dev/null >/dev/null 2>&1; }

offline_checks
[ "$MODE" = offline ] && { echo "OFFLINE CHECK: PASS"; exit 0; }
if [ "$MODE" = restore ]; then a9_usb_lock_inherit; export A9_USB_LOCKED=1; a9_run_dir mainline-android-restore; restore; exit $?; fi
if [ "$MODE" = slota ]; then a9_usb_lock_inherit; export A9_USB_LOCKED=1; a9_run_dir mainline-android-to-slot-a; to_slot_a; exit $?; fi

phone_checks
if [ "$MODE" = check ]; then
  echo "CHECK: PASS. Approval code for exactly these images: $(approval_code)"
  echo "  run: $0 --write --code $(approval_code)"
  exit 0
fi
[ "$CODE" = "$(approval_code)" ] || fail "--code does not match these images (run check mode first)"

# ------------------------------------------------------------------ write
a9_usb_lock_inherit; export A9_USB_LOCKED=1
a9_run_dir mainline-android "$BOOT" "$VENDOR"
exec > >(tee -a "$A9_RUN/log.txt") 2>&1
{ echo "boot $(awk '{print $1}' "$BOOT.sha256")"; echo "vendor $(awk '{print $1}' "$VENDOR.sha256")"; } >> "$A9_RUN/meta.txt"

[ "$NORESTORE" = 1 ] && { echo "== --no-restore: prior slot-B state"; for p in boot_b dtbo_b; do echo "   $p $(phone_part_hash $p | cut -c1-16)..."; done; }
echo "== stage vendor_b (lp-write from slot a)"
flash_vendor_b "$VENDOR" || { echo "staging failed"; finish; exit 1; }
phone_checks staged || { echo "post-stage checks failed"; finish; exit 1; }
[ "$MODE" = stage ] && { echo "vendor_b staged; slot a unchanged. Restore with --restore."; exit 0; }

"$ADB" reboot bootloader
a9_wait_fastboot no 120 || { echo "bootloader fastboot did not appear"; finish; exit 1; }
bootloader_checks || { echo "precondition failed in the bootloader; nothing more written"; "$FB" reboot; a9_wait_boot 600; finish; exit 1; }
ok "bootloader: slot a active+successful, no snapshot, charger screen off"
"$FB" flash boot_b "$BOOT" || { echo "flash boot_b failed"; finish; exit 1; }
"$FB" flash dtbo_b "$DTBO" || { echo "flash dtbo_b failed"; finish; exit 1; }
"$FB" set_active b && [ "$(a9_fbvar current-slot)" = b ] || { echo "set_active b failed"; finish; exit 1; }
echo "-- fastboot reboot into mainline Android on slot b"
"$FB" reboot
T0=$(date +%s)

OUTCOME=timeout SEEN_ADB=0
while [ $(( $(date +%s) - T0 )) -lt "$WATCH" ]; do
  t=$(( $(date +%s) - T0 ))
  if timeout 5 "$FB" devices 2>/dev/null </dev/null | grep -q fastboot; then
    OUTCOME=fastboot; echo "t+${t}s bootloader fastboot (beacon reset / dead-man; adb seen: $SEEN_ADB)"; break
  fi
  st=$(a9_adb_state)
  if [ "$SEEN_ADB" = 0 ] && lsusb 2>/dev/null | grep -qi '18d1:4e'; then echo "t+${t}s Android USB gadget: $(lsusb | grep -i 18d1:4e)"; SEEN_ADB=1; fi
  if [ "$st" = unauthorized ] && [ "$SEEN_ADB" != U ]; then echo "t+${t}s adb UNAUTHORIZED (adb_keys not installed?)"; SEEN_ADB=U; fi
  if [ "$st" = device ]; then
    K=$(timeout 10 "$ADB" shell uname -r </dev/null | tr -d '\r'); S=$(timeout 10 "$ADB" shell getprop ro.boot.slot_suffix </dev/null | tr -d '\r')
    case "$K" in
      7.*)
        echo "t+${t}s adb UP from mainline Android: kernel $K slot $S"
        stay   # hold the dead-man while we look (refreshed below; stale after 600 s)
        root_check
        echo "   adb transport: $(timeout 5 "$ADB" devices -l </dev/null | grep -v '^List' | tr -s ' ' | tr '\n' ';')"
        # wait for boot_completed up to BC_WAIT s (default 1200), but stop
        # early on a system_server crash loop (start_count >= 15): collect
        # while the logs still hold the first crashes
        end=$(( $(date +%s) + ${A9M_BC_WAIT:-1200} ))
        while [ "$(date +%s)" -lt "$end" ]; do
          stay
          [ "$(timeout 5 "$ADB" shell getprop sys.boot_completed </dev/null | tr -d '\r')" = 1 ] && break
          ssc=$(timeout 5 "$ADB" shell getprop sys.system_server.start_count </dev/null | tr -d '\r')
          if [ "${ssc:-0}" -ge 15 ] 2>/dev/null; then echo "t+$(( $(date +%s) - T0 ))s system_server crash loop (start_count $ssc): collecting now"; break; fi
          sleep 5
        done
        BC=$(timeout 5 "$ADB" shell getprop sys.boot_completed </dev/null | tr -d '\r')
        SF=$(timeout 5 "$ADB" shell pidof surfaceflinger </dev/null | tr -d '\r')
        echo "t+$(( $(date +%s) - T0 ))s sys.boot_completed=${BC:-0} surfaceflinger pid=${SF:-none}"
        if [ "$S2_QUIET" = 0 ]; then collect "$A9_RUN/android"; fi
        if [ "$EINK" = 1 ] && [ "$S2_QUIET" = 0 ]; then eink_session "$A9_RUN/android/eink"; erc=$?; [ "$erc" = 1 ] && echo "   (no a9_eink card)"; fi
        OUTCOME="android-mainline boot_completed=${BC:-0} sf=${SF:+yes}"
        [ -n "${EINK_FAULT:-}" ] && OUTCOME="$OUTCOME eink=FAULT"
        if [ "$HOLD" -gt 0 ] && [ -z "${EINK_FAULT:-}" ]; then
          # gates-open holds already ran inside eink_session; others hold here
          [ "$OPEN_GATES" = 1 ] || hold_loop "$A9_RUN/android"
          if [ -z "${EINK_FAULT:-}" ]; then
            echo "outcome: $OUTCOME hold-ended${HOLD_LOST:+ phone-lost} after $(( $(date +%s) - T0 )) s; NOT restored" | tee -a "$A9_RUN/meta.txt"
            echo "   the phone is still on the mainline test image (slot b). Restore:"; print_restore
            exit 0
          fi
          OUTCOME="$OUTCOME eink=FAULT"
        fi
        echo "-- adb reboot bootloader (PON reason 2)"
        if ! timeout 20 "$ADB" reboot bootloader </dev/null; then
          echo "!! adb reboot failed: adb devices -l = $(timeout 5 "$ADB" devices -l </dev/null | grep -v '^List' | tr '\n' ';')"
          echo "   waiting for the vendor dead-man (boot_completed + 1200 s, stay file stale after 600 s) -> fastboot"
        fi
        a9_wait_fastboot no "${A9M_REBOOT_WAIT:-1800}" || echo "!! bootloader did not appear after reboot bootloader / dead-man"
        break ;;
      *) OUTCOME=android-stock; echo "t+${t}s Android on kernel $K slot $S: ABL fell back (pre-PON resets exhausted B)"; break ;;
    esac
  fi
  sleep 2
done
echo "outcome: $OUTCOME after $(( $(date +%s) - T0 )) s" | tee -a "$A9_RUN/meta.txt"
if [ "$OUTCOME" = timeout ]; then
  echo "!! nothing decisive on USB for $WATCH s; the vendor dead-man should have sent it to fastboot."
  echo "!! a9-recover skill exit B: ask the human to hold Power 10-15 s (PON reason 2 -> fastboot), then --restore"
  exit 1
fi
finish
