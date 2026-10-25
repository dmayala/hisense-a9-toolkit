#!/usr/bin/env bash
#
# first-boot.sh -- the first boots of the A9 mainline image, designed so that
# no reset can trap the phone (docs/mainline-kernel-build.md section 4).
# Written 2026-10-15 after the incident in docs/state-log.md. NOT YET RUN.
#
# Route (MEASURED in the phone's ABL, LinuxLoader 1dd55dcb..., doc section 4):
#   mainline goes into boot_b (+ the null overlay as dtbo_b), slot B is set
#   active (retry count 7, not successful), and it is entered with a plain
#   `fastboot reboot` (PON reason 0, misc BCB not "boot-recovery").
#   - a reset after the kernel's PON driver probed: reason 2 (qcom_pon.beacon=2
#     in the image) -> bootloader FASTBOOT. Reason 2 beats the BCB and costs no
#     A/B retry.
#   - a reset before that: a normal boot of slot B again, which decrements its
#     retry count; after 7 failed boots the ABL marks B unbootable and boots
#     slot A (Android, the user's main slot) by itself.
#   Never `fastboot reboot recovery` (it writes the BCB that looped the phone
#   on 2026-10-15), never write misc, recovery_*, slot A, devinfo, diag.
#
# Modes:
#   --offline   host-only checks (image, abl-check recovery + boot, cmdline,
#               golden copies). No phone. The only mode an agent may run.
#   (default)   CHECK: offline checks + read-only phone checks in Android
#               (slot a booted, battery, boot_b/dtbo_b = golden, misc BCB).
#   --write     the test: flash boot_b + dtbo_b, set_active b, reboot, watch,
#               then ALWAYS restore boot_b/dtbo_b from golden and slot a.
#               Refused unless a human runs it: needs a terminal, the env
#               A9_MAINLINE_HUMAN=yes, and a typed confirmation.
#   --restore   only the restore half (golden boot_b + dtbo_b, slot a).
#
# Log capture (boot 2 on): when the mainline gadget appears, the host finds the
# NCM interface by its MAC (02:a9:00:00:00:02), gives it 172.16.42.2 with
# `sudo -n ip`, and talks to a9-ctl on 172.16.42.1:2323: "stay <s>" holds the
# dead-man, "capture" and "capture-dbg" fetch tar.gz bundles into
# $A9_RUN/capture/, analyze-capture.py summarizes them, "release" lets the
# dead-man send the phone to fastboot. Fallback: the ACM shell via
# `sudo -n python3 a9-capture-host.py acm` (the user is not in dialout).
#
# After the first capture it waits --late seconds (default 60; past the 10 s
# deferred-probe timeout, so sync_state and late probe lines are in) and
# captures again into capture/late/. --e1 (docs/mainline-eink-steps.md E1)
# then sends "eink-load core" (dispcc + msm: MDSS, DPU, DSI; no bridge, no
# power gate), waits 60 s and captures into capture/e1/.
#
# --e2 (implies --e1; docs/mainline-eink-steps.md E2): after E1, "i2c-probe"
# (READ-ONLY raw I2C: bridge ID, FP9929A VCOM/temp registers) saved to
# capture/e2a.txt, then "eink-load bridge" (panel + tc358767: VDDC/reset/ID
# read, DSI attach, msm DRM binds; no modeset, power gates stay closed), 30 s,
# capture into capture/e2/.
#
# The kernel log is streamed for the whole session from tcp 2324 into
# capture/kmsg-stream.txt (last lines before a hang or reset). After each E
# step the host pings a9-ctl; if the phone stops answering, it sends nothing
# more and waits for the dead-man / beacon. --e2pre adds the read-only I2C
# probe BEFORE E1 (boot 5: E1 then lost USB; keep it opt-in).
#
# --e3 (implies --e2; docs/mainline-eink-steps.md "E3-E5 sequencer"): after
# E2b, the on-phone sequencer /bin/a9-eink-seq via a9-ctl: "pre" (a9_eink
# loaded with hv_hold_ms=0 hv_max_ms=15000, preconditions), then "e3" (HV on,
# all-zero frames only; PG sampled across power-up/on/down; polarity decided
# from the steady-on samples). --e5ab adds E5a (dry run; the host reproduces
# the hash with the a9tcon CLI and sends the verdict) and E5b (panel gate +
# a9_eink.enable, FP9929A gate closed). --e5 adds E5c (first glass). Each
# stage runs only if the previous one passed, on the phone AND on the host;
# any failure or timeout runs the phone's abort path (gates closed, FP9929A
# forced off, PWRUP/VCOM_EN verified low) and then the normal release ->
# dead-man -> fastboot -> restore. Needs an A9_EINK_ASSETS=1 image (default
# for these flags: $A9_MAINLINE/out-eink/). Results: capture/eink-seq/.
# --e3full: E3 sessions A and B ("e3 full": zero frames only, timing, VCOM,
# MX, vsync), then a latched stop; no E5.
# --e3t: pre + "e3 full" (A + B) + "e3t stop": one more template-only HV
# session (patch 0028: template frames clock the gates, drive no pixel) with
# read-only raw I2C reads of the TC358767, the DSI clock rates and one DPU
# state print (info, capture/eink-seq/phone/e3t/), then a latched stop. The
# user WATCHES THE GLASS: the host prints LIVE lines when HV goes on and off;
# expected: unchanged. No E5.
# The control template (a9-ctrl-template.bin, local only, never committed)
# must be in the image and on the host next to the waveform; pre compares both.
# --sim (tests only): run only the E3-E5 host driver against a9-ctl at
# $A9_CTL_ADDR:$A9_CTL_PORT (QEMU), never the phone's 172.16.42.1.
#
# Usage: first-boot.sh [--offline|--write|--restore|--sim] [--image <img>] [--watch <s>] [--stay <s>] [--late <s>] [--e1] [--e2] [--e2pre] [--e3|--e3full|--e3t|--e5ab|--e5]
#
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

MODE=check
W=${A9_MAINLINE:-$HOME/A9-build/mainline}
IMG="$W/out/a9-mainline-recovery.img"
WATCH=900
STAY=300
LATE=60
E1=0
E2=0
E2PRE=0
E3=0 E3FULL=0 E3T=0 E5AB=0 E5C=0
IMG_SET=0
while [ $# -gt 0 ]; do
  case "$1" in
    --offline) MODE=offline ;; --write) MODE=write ;; --restore) MODE=restore ;;
    --image) IMG="${2:-}"; IMG_SET=1; shift ;;
    --sim) MODE=sim ;;
    --watch) WATCH="${2:-}"; shift ;;
    --stay) STAY="${2:-}"; shift ;;
    --late) LATE="${2:-}"; shift ;;
    --e1) E1=1 ;;
    --e2) E1=1; E2=1 ;;
    --e2pre) E2PRE=1 ;;
    --e3) E1=1; E2=1; E3=1 ;;
    --e3full) E1=1; E2=1; E3=1; E3FULL=1 ;;
    --e3t) E1=1; E2=1; E3=1; E3FULL=1; E3T=1 ;;
    --e5ab) E1=1; E2=1; E3=1; E5AB=1 ;;
    --e5) E1=1; E2=1; E3=1; E5AB=1; E5C=1 ;;
    -h|--help) sed -n '2,83p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument $1" >&2; exit 2 ;;
  esac
  shift
done
[ "$E3" = 1 ] && [ "$IMG_SET" = 0 ] && IMG="$W/out-eink/a9-mainline-recovery.img"
DTBO="$(dirname "$IMG")/recovery_dtbo.img"
case "$STAY$LATE" in ''|*[!0-9]*) echo "--stay/--late need seconds" >&2; exit 2 ;; esac
[ "$STAY" -ge $(( LATE + 60 * E1 + 60 * E2 + 600 * E3 + 120 )) ] || STAY=$(( LATE + 60 * E1 + 60 * E2 + 600 * E3 + 120 ))
[ "$WATCH" -ge $(( STAY + 300 )) ] || WATCH=$(( STAY + 300 ))
CAPHOST="$HERE/a9-capture-host.py"
CTL_ADDR=172.16.42.1 CTL_PORT=2323
EINK_FW=${A9_EINK_FW:-$W/firmware/hisense/a9-waveform.bin}
EINK_CT=${A9_EINK_CT:-$W/firmware/hisense/a9-ctrl-template.bin}   # control template (patch 0028), local only
CT_PAYLOAD_SHA=a88ae8efc5325e1ab52e4e48b73d2abb1a3c81a7989baa2a226513b07cebfbe7   # = the kernel's A9_CT_SHA256
# E5a known case (steps after the init flash, 30/30 C) WITH the control
# template. Not pinned yet: the CLI's --template is new. The offline check
# prints the value; pin it here after one review (the template-free value was
# 6661424b107d3839bf06d690e8d9a27413144a8f93f3f3f8f5868d24c29b39f7).
E5A_KNOWN=1b76ea3d87d0b767eddbe2620772f839ac9a42957fb3b685ce3d7a483682be1f
E5A_OLD=6661424b107d3839bf06d690e8d9a27413144a8f93f3f3f8f5868d24c29b39f7
COMPOSER=${A9_COMPOSER:-$HOME/source/a9-display-evidence-20261012/vendor/vendor.qti.hardware.display.composer-service}
TCON_CLI=${A9_TCON_CLI:-$HERE/tcon/build/a9tcon}
PY3=$(command -v python3); IPBIN=$(command -v ip)

fail() { echo "FAIL: $*" >&2; exit 1; }
ok()   { echo "  ok   $*"; }

# env without touching the phone (a9-env.sh only sets variables and functions)
# shellcheck source=../a9-env.sh
. "$REPO/scripts/a9-env.sh"
GOLD="${A9_GOLDEN:-$A9_BACKUPS/edl_2026-10-17_102417}"
G_BOOT="$GOLD/lun4/boot_b.bin"; G_DTBO="$GOLD/lun4/dtbo_b.bin"

offline_checks() {
  echo "== host checks"
  [ -f "$IMG" ] && [ -f "$IMG.sha256" ] || fail "image or .sha256 missing: $IMG"
  [ "$(sha256sum "$IMG" | awk '{print $1}')" = "$(awk '{print $1}' "$IMG.sha256")" ] || fail "sha256 mismatch: $IMG"
  ok "image sha256 $(cut -c1-16 "$IMG.sha256")..."
  [ -f "$DTBO" ] || fail "null-overlay DTBO missing: $DTBO"
  [ "$(head -c 4 "$DTBO" | od -A n -t x1 | tr -d ' ')" = d7b7ab1e ] || fail "$DTBO is not a DTBO table image"
  ok "dtbo_b image $(basename "$DTBO") ($(stat -c %s "$DTBO") bytes)"
  "$REPO/scripts/abl-check.sh" "$IMG" boot --dtbo "$DTBO" --quiet || fail "abl-check boot + null dtbo"
  ok "abl-check: boot_b + dtbo_b route PASS"
  "$REPO/scripts/abl-check.sh" "$IMG" recovery --quiet || fail "abl-check recovery"
  ok "abl-check: recovery route PASS (not used by this script)"
  python3 -I - "$IMG" <<'PY' || fail "boot header cmdline lacks the beacon"
import sys
h = open(sys.argv[1], 'rb').read(4096)
c = (h[64:576].split(b'\0')[0] + h[608:1632].split(b'\0')[0]).decode().split()
sys.exit(0 if 'qcom_pon.beacon=2' in c and not [w for w in c if w.startswith('qcom_pon.beacon=') and w != 'qcom_pon.beacon=2'] else 1)
PY
  ok "cmdline arms qcom_pon.beacon=2 (any reset after PON probe -> fastboot)"
  for f in "$G_BOOT" "$G_DTBO"; do
    [ -f "$f" ] || fail "golden copy missing: $f"
    want=$(awk -v p="./lun4/$(basename "$f")" '$2==p{print $1}' "$GOLD/SHA256SUMS")
    [ -n "$want" ] && [ "$(sha256sum "$f" | awk '{print $1}')" = "$want" ] || fail "golden $(basename "$f") does not match SHA256SUMS"
  done
  ok "golden boot_b.bin and dtbo_b.bin match $GOLD/SHA256SUMS"
  [ "$E3" = 1 ] && eink_offline_checks
  return 0
}

# --e3/--e5*: the image must carry the reviewed sequencer, the waveform and the
# pattern tool; the host must be able to reproduce E5a.
eink_offline_checks() {
  echo "== E Ink sequencer checks (--e3/--e5)"
  python3 -I - "$IMG" "$HERE/kernel/initramfs/a9-eink-seq" "$EINK_FW" "$EINK_CT" "$CT_PAYLOAD_SHA" <<'PY' || fail "E Ink image checks"
import gzip, hashlib, struct, sys
img, seq, fw, ct, ctsha = sys.argv[1:6]
b = open(img, 'rb').read()
ks, rs, page = struct.unpack_from('<I', b, 8)[0], struct.unpack_from('<I', b, 16)[0], struct.unpack_from('<I', b, 36)[0]
off = page + (ks + page - 1) // page * page
rd = gzip.decompress(b[off:off + rs])
files, i = {}, 0
while i + 110 <= len(rd):
    assert rd[i:i + 6] == b'070701', 'not a newc cpio'
    f = [int(rd[i + 6 + 8 * k:i + 14 + 8 * k], 16) for k in range(13)]
    nl, fs = f[11], f[6]
    name = rd[i + 110:i + 110 + nl - 1].decode()
    i = (i + 110 + nl + 3) & ~3
    files[name.lstrip('./')] = rd[i:i + fs]
    i = (i + fs + 3) & ~3
    if name == 'TRAILER!!!':
        break
sha = lambda x: hashlib.sha256(x).hexdigest()
bad = 0
def chk(c, m):
    global bad
    print(('  ok   ' if c else 'FAIL: ') + m); bad |= not c
chk(files.get('bin/a9-eink-seq') == open(seq, 'rb').read(), 'image /bin/a9-eink-seq == repo copy')
w = files.get('lib/firmware/hisense/a9-waveform.bin')
chk(w is not None and w == open(fw, 'rb').read(), 'image waveform == %s (%s...)' % (fw, sha(w or b'')[:16]))
try:
    hc = open(ct, 'rb').read()
except OSError:
    hc = b''
chk(len(hc) == 80 + 1485568 and hc[:16] == struct.pack('<4I', 0x54433941, 1, 80, 1485568)
    and sha(hc[80:]) == ctsha and hc[48:80].hex() == ctsha,
    'host control template %s: A9CT v1 header, payload sha256 %s...' % (ct, ctsha[:16]))
t = files.get('lib/firmware/hisense/a9-ctrl-template.bin')
chk(t is not None and t == hc, 'image control template == host copy (%s...)' % sha(t or b'')[:16])
chk('bin/a9eink-pattern' in files, 'image has /bin/a9eink-pattern')
chk(any(n.endswith('/a9_eink.ko') for n in files), 'image has a9_eink.ko')
h = b[:4096]
c = (h[64:576].split(b'\0')[0] + h[608:1632].split(b'\0')[0]).decode().split()
p = [x for x in c if x.startswith('panic=')]
chk('oops=panic' in c and p and p[-1] == 'panic=1', 'cmdline oops=panic, last panic=1')
chk(not [x for x in c if any(g in x for g in ('allow_power_on', 'hlte556n_eink.enable', 'a9_eink.'))], 'no E Ink gate or a9_eink parameter on the cmdline')
sys.exit(bad)
PY
  if [ "$E5AB" = 1 ]; then
    [ -x "$TCON_CLI" ] || fail "a9tcon CLI missing: nix-shell -p gcc gnumake --run 'make -C $HERE/tcon build/a9tcon'"
    [ -f "$EINK_CT" ] || fail "control template (for the E5a CLI check) missing: $EINK_CT"
    [ -f "$COMPOSER" ] || fail "composer (vendor tables for the E5a CLI check) missing: $COMPOSER"
    out=$(eink_cli_selftest) || fail "a9tcon CLI self-test: $out"
    ok "E5a host check ready: $out"
  fi
}

# the CLI reproduces a known update (steps image after the init flash, 30 C):
# the same recipe the E5a verdict uses, with the control template (patch 0028)
eink_cli_selftest() {
  local t; t=$(mktemp -d)
  python3 -I - "$t" <<'PY'
import sys
d = sys.argv[1]
g = bytearray(1648 * 824)
for y in range(824):
    for x in range(1648):
        g[y * 1648 + x] = 17 * ((x * 16) // 1648)
open(d + '/grey', 'wb').write(g)
open(d + '/old', 'wb').write(bytes([15]) * (1648 * 824))
PY
  local r; r=$("$TCON_CLI" "$COMPOSER" --template "$EINK_CT" --grey "$t/grey" --old "$t/old" --mode 0x203 --pre 0x203 --clear 1 --t1 30 --t2 30 2>&1 | tail -n1)
  rm -rf "$t"
  # engine (init flash, then this update) == CLI fresh: checked 2026-10-15
  # (template-free). With the template the frame bytes 1-3 change, so the
  # hash must differ from the template-free one; it is pinned in E5A_KNOWN.
  case "$r" in "frames 60 sha256 "*) ;; *) echo "got '$r' (CLI without --template support?)"; return 1 ;; esac
  [ "$r" != "frames 60 sha256 $E5A_OLD" ] || { echo "got the template-free hash: --template had no effect"; return 1; }
  if [ -n "$E5A_KNOWN" ]; then
    [ "$r" = "frames 60 sha256 $E5A_KNOWN" ] && echo "$r" || { echo "got '$r', pinned $E5A_KNOWN"; return 1; }
  else
    echo "$r (known-case hash NOT PINNED yet: set E5A_KNOWN in first-boot.sh)"
  fi
}

phone_checks() {
  echo "== phone checks (read-only, Android)"
  H=$("$REPO/scripts/a9-health.sh" --json); HRC=$?
  case "$H" in *'"slot":"_a"'*) ;; *) fail "phone must be in Android on slot a: $H" ;; esac
  [ "$HRC" = 0 ] || fail "Android on slot a has not finished booting"
  ok "Android, slot a, boot completed"
  LVL=$(timeout 10 "$ADB" shell dumpsys battery 2>/dev/null | tr -d '\r' | sed -n 's/^ *level: //p')
  [ -n "$LVL" ] && [ "$LVL" -ge 60 ] || fail "battery ${LVL:-?} % < 60 % (mainline neither charges nor monitors the battery)"
  ok "battery $LVL %"
  "$REPO/scripts/a9-readback.sh" boot_b "$G_BOOT" >/dev/null || fail "boot_b on the phone is not the golden copy: restore source would be wrong"
  "$REPO/scripts/a9-readback.sh" dtbo_b "$G_DTBO" >/dev/null || fail "dtbo_b on the phone is not the golden copy"
  ok "boot_b and dtbo_b = golden (the restore puts back exactly these)"
  BCB=$(a9_su 'dd if=/dev/block/by-name/misc bs=32 count=1 2>/dev/null' | tr -d '\000' | tr -cd '[:print:]')
  case "$BCB" in
    boot-recovery*|boot-fastboot*) fail "misc BCB says '$BCB': the ABL would boot RECOVERY. Clear it with scripts/twrp.sh exit first (never by hand)." ;;
  esac
  ok "misc BCB command '${BCB:-<empty>}' (ABL acts only on boot-recovery / boot-fastboot)"
}

bootloader_checks() {   # in bootloader fastboot
  [ "$(a9_fbvar is-userspace)" = no ] || return 1
  [ "$(a9_fbvar current-slot)" = a ] || { echo "current-slot is not a"; return 1; }
  [ "$(a9_fbvar slot-successful:a)" = yes ] || { echo "slot a not marked successful: the fallback to a would fail"; return 1; }
  [ "$(a9_fbvar slot-unbootable:a)" = no ] || { echo "slot a marked unbootable"; return 1; }
  s=$(a9_fbvar snapshot-update-status); case "$s" in ""|none) ;; *) echo "snapshot-update-status $s"; return 1 ;; esac
  # charger screen enabled + cable present = the ABL does not decrement the
  # retry count (LinuxLoader 0x1350), which would remove the bound
  timeout 10 "$FB" oem device-info 2>&1 | tr -d '\r' | grep -qi 'charger screen enabled: false' \
    || { echo "charger screen not reported disabled"; return 1; }
  return 0
}

restore() {
  echo "== restore: golden boot_b + dtbo_b, slot a"
  case "$(a9_adb_state)" in device|recovery) "$ADB" reboot bootloader ;; esac
  a9_wait_fastboot no 180 || { echo "!! no bootloader fastboot: follow the a9-recover skill"; return 1; }
  "$FB" flash boot_b "$G_BOOT" || return 1
  "$FB" flash dtbo_b "$G_DTBO" || return 1
  "$FB" set_active a && [ "$(a9_fbvar current-slot)" = a ] || { echo "!! set_active a failed"; return 1; }
  "$FB" reboot
  a9_wait_boot 600 || { echo "!! slot a did not boot: a9-recover skill"; return 1; }
  sleep 15
  "$REPO/scripts/a9-readback.sh" boot_b "$G_BOOT" && "$REPO/scripts/a9-readback.sh" dtbo_b "$G_DTBO" || { echo "!! readback mismatch"; return 1; }
  "$REPO/scripts/a9-health.sh"
}

# ---------------------------------------------------------------- E3-E5 host side
# eink_run <capdir>: drives /bin/a9-eink-seq stage by stage over a9-ctl. The
# phone enforces the order and runs the abort path itself; the host adds a
# second opinion (its own scan of the live kmsg stream for each session
# window, and the E5a hash) and stops at the first stage that is not a PASS.
# Whatever happens, it ends with "eink-seq safe" (gates closed, HV verified
# off) and a fetch of the phone's session directory. Never fatal.
eink_run() {
  local cap="$1" d="$1/eink-seq" r st
  mkdir -p "$d"
  if [ "$MODE" != sim ]; then
    grep -q '\[drm\] Initialized msm' "$cap/e2/dmesg.txt" 2>/dev/null \
      || { echo "   E3: E2b did not bind the msm DRM device: not starting"; return 1; }
  fi
  local fw ct; fw=$(sha256sum "$EINK_FW" 2>/dev/null | cut -d' ' -f1); ct=$(sha256sum "$EINK_CT" 2>/dev/null | cut -d' ' -f1)
  stage() { # stage <name> <host timeout> <a9-eink-seq args...>: 0 = PASS
    local n=$1 to=$(( $2 * ${SIM_SLOW:-1} )); shift 2
    echo "   eink-seq $*"
    H eink-seq "$@" --timeout "$to" --out "$d/$n.txt" || echo "   (no answer)"
    sed 's/^/   | /' "$d/$n.txt" 2>/dev/null | grep -v '^   |   ok ' | tail -n 40
    grep -E "^RESULT $1 (PASS|DRY-OK|STOP-A|STOP-B|STOP-T)" "$d/$n.txt" >/dev/null 2>&1 || return 1
    eink_host_scan "$cap/kmsg-stream.txt" "$n" > "$d/$n.host.txt" 2>&1 \
      || { sed 's/^/   host| /' "$d/$n.host.txt"; return 1; }
    alive || { echo "   !! phone stopped answering after $n"; return 1; }
    return 0
  }
  stop() { # host-side stop: latch on the phone with the reason
    echo "   STOP: $*"
    H eink-seq safe "host: $*" --timeout $(( 60 * ${SIM_SLOW:-1} )) --out "$d/safe-stop.txt"
    sed 's/^/   | /' "$d/safe-stop.txt" 2>/dev/null
  }
  r=0
  if ! stage pre 120 pre "${fw:--}" "${ct:--}"; then stop "pre"; r=1
  elif [ "$E3T" = 1 ]; then
    # --e3t: A + B, then the template session; the PHONE latches the stop
    # after it ("e3t stop" -> RESULT e3t STOP-T). The user watches the glass:
    # the phone's HV ON/OFF lines are echoed live from the kmsg stream.
    if ! stage e3 180 e3 full; then stop "e3"; r=1
    else
      echo "   >>> e3t: WATCH THE GLASS now. HV goes on within a few seconds for ~5 s; expected: UNCHANGED"
      ( tail -n0 -F "$cap/kmsg-stream.txt" 2>/dev/null | grep --line-buffered -E 'a9-eink-seq: e3t: HV (ON|OFF)' \
          | while IFS= read -r l; do echo "   LIVE $(date +%T) ${l#*;}"; done ) &
      local lp=$!
      if stage e3t 120 e3t stop; then
        grep -q '^RESULT e3t STOP-T (template frames only; e3t PASS' "$d/e3t.txt" || r=1
      else stop "e3t"; r=1; fi
      sleep 1; pkill -P "$lp" 2>/dev/null; kill "$lp" 2>/dev/null
      sed 's/^/   | /' "$d/e3t.txt" 2>/dev/null | grep -E 'info e3t\.(tc_|dsierr|disp_cc|drm_state|glass)' | cut -c1-200
      echo "   >>> e3t glass: NOT machine-checked. Tell the coordinator what the glass did between LIVE HV ON and HV OFF."
    fi
  elif [ "$E3FULL" = 0 ] && [ "$E5AB" = 0 ]; then
    # --e3: measurement only. The phone stops (latched) after session A
    # whatever the result; STOP-A is the expected end, not a failure
    stage e3 180 e3 || { stop "e3"; r=1; }
  elif [ "$E5AB" = 0 ]; then
    # --e3full: sessions A and B (zero frames only); the PHONE latches the stop
    # after B by itself ("e3 full stop" -> RESULT e3 STOP-B)
    if stage e3 180 e3 full stop; then
      grep -q '^RESULT e3 STOP-B (zero frames only; sessions A+B PASS' "$d/e3.txt" || r=1
    else stop "e3"; r=1; fi
  elif ! stage e3 180 e3 full; then stop "e3"; r=1
  else
    if ! stage e5a 120 e5a; then stop "e5a"; r=1
    elif ! eink_e5a_verify "$d"; then stop "e5a host CLI check failed"; r=1
    elif ! stage verdict 40 verdict e5a $(cat "$d/e5a.cli"); then stop "verdict"; r=1
    elif ! stage e5b 90 e5b; then stop "e5b"; r=1
    elif [ "$E5C" = 1 ] && ! stage e5c 120 e5c; then stop "e5c"; r=1
    fi
  fi
  H eink-seq safe --timeout $(( 60 * ${SIM_SLOW:-1} )) --out "$d/safe-final.txt"
  if grep -q 'safe: verified PWRUP=0 VCOM_EN=0' "$d/safe-final.txt" 2>/dev/null; then
    echo "   final: $(grep -m1 'safe: verified' "$d/safe-final.txt")"
  else
    # never silent: no verified-off answer means the off state is UNKNOWN
    echo "   !! final safe NOT verified: $(grep -m1 'CRITICAL' "$d/safe-final.txt" 2>/dev/null || echo 'no answer') (the dead-man/kernel shutdown still run)"
    r=1
  fi
  H eink-seq fetch --timeout $(( 60 * ${SIM_SLOW:-1} )) --out "$d/phone.tgz" && mkdir -p "$d/phone" && tar -xzf "$d/phone.tgz" -C "$d/phone" 2>/dev/null
  sed 's/^/   | /' "$d/phone/hv_total_ms" 2>/dev/null | sed 's/|/| HV-on total ms:/'
  grep -h '^PG_VERDICT' "$d/e3.txt" 2>/dev/null | sed 's/^/   /'
  return $r
}

# eink_host_scan <kmsg-stream> <stage>: the host's own look at each session
# window of that stage (records "pri,seq,ts,flags;msg" from /dev/kmsg): fail on
# kernel errors (level <= 3) and on known bad signatures, except the lines a
# stage expects (E5b: the refused power-up). The phone's watcher is stricter
# (warnings too); this only guards against a phone-side bug.
eink_host_scan() {
  python3 -I - "$1" "$2" <<'PY'
import re, sys
path, stage = sys.argv[1], sys.argv[2]
tags = {'pre': ['pre'], 'e3': ['e3a', 'e3b'], 'e3t': ['e3t'], 'e5a': ['e5a'], 'e5b': ['e5b'], 'e5c': ['e5c'], 'verdict': []}[stage]
try:
    lines = open(path, 'rb').read().decode('utf-8', 'replace').splitlines()
except OSError:
    print('no kmsg stream'); sys.exit(1 if tags else 0)
bad = re.compile(r'Unhandled context fault|SError|Internal error|Oops|BUG:|WARNING:|Kernel panic|\*ERROR\*|'
                 r'underrun|underflow|timed out|timeout|a9-irqmon:|init seq #|forcing the FP9929A|max_on_ms .* expired|'
                 r'VCOM write failed|update aborted|dropped out|transport (enable|disable) failed')
expect_e5b = re.compile(r'power-up refused|FP9929A power-up failed|FP9929A is not on after enable|update dropped|init seq #|Skipping unprepare|^tc358767 0-000f: main link stream start error: -6$')
user = re.compile(r'^(a9-eink-seq|a9-ctl|a9-init|a9-eink): ')
# the phone's BENIGN list (a9-eink-seq): known-harmless, once per boot
benign = re.compile(r'no GPU device was found|low vbp.vfp may lead to perf issues|supply \S+ not found, using dummy regulator')
rc = 0
for t in tags:
    on, seen = False, False
    for l in lines:
        if l.startswith(' '):
            continue        # /dev/kmsg dictionary continuation line
        m = re.match(r'^(\d+),\d+,\d+,[^;]*;(.*)$', l)
        lvl, msg = (int(m.group(1)) % 8, m.group(2)) if m else (6, re.sub(r'^\[[^]]*\] ', '', l))
        if msg.startswith('a9-eink-seq: MARK %s open' % t):
            on, seen = True, True; continue
        if msg.startswith('a9-eink-seq: MARK %s close' % t):
            on = False; continue
        if not on or user.match(msg) or benign.search(msg):
            continue
        if stage == 'e5b' and expect_e5b.search(msg):
            continue
        if lvl <= 3 or bad.search(msg):
            print('%s: L%d %s' % (t, lvl, msg)); rc = 1
    if not seen:
        print('%s: session window not in the kmsg stream' % t)
        rc = 1 if t != 'e3b' else rc   # e3b is skipped when e3a already stops
sys.exit(rc)
PY
}

# eink_e5a_verify <dir>: fetch the phone's E5a files, check them, reproduce the
# dry-run update with the a9tcon CLI. Writes "<frames> <sha256>" to e5a.cli.
eink_e5a_verify() {
  local d="$1" x="$1/e5a-files"
  rm -rf "$x"; mkdir -p "$x"
  H eink-seq fetch --timeout $(( 60 * ${SIM_SLOW:-1} )) --out "$d/e5a.tgz" && tar -xzf "$d/e5a.tgz" -C "$x" ./e5a 2>/dev/null
  python3 -I - "$x/e5a" "$TCON_CLI" "$COMPOSER" "$d/e5a.cli" "$EINK_CT" <<'PY'
import hashlib, re, subprocess, sys
e, cli, comp, outp, ct = sys.argv[1:6]
def rd(n): return open('%s/%s' % (e, n), 'rb').read()
ok = True
def chk(c, m):
    global ok
    print(('   ok   ' if c else '   FAIL ') + m); ok &= bool(c)
try:
    sums = dict((l.split()[1], l.split()[0]) for l in open(e + '/SHA256SUMS'))
except OSError:
    print('   FAIL no e5a files from the phone'); sys.exit(1)
for n in ('grey', 'levels_before', 'last'):
    chk(hashlib.sha256(rd(n)).hexdigest() == sums.get(n), '%s arrived intact' % n)
g, old, last = rd('grey'), rd('levels_before'), rd('last').decode()
N = 1648 * 824
chk(len(old) == N and old == bytes([15]) * N, 'levels_before = all 15 (glass taken as white after the dry init flash)')
want = bytearray(N)
for y in range(824):
    for x in range(1648):
        want[y * 1648 + x] = 17 * ((x * 16) // 1648)
chk(g == bytes(want), 'grey = the "steps" pattern through the stock luma + rotation (16 bands)')
m = re.search(r'mode (0x[0-9a-f]+) pre (0x[0-9a-f]+) clear (\d) type (\d+) bin (-?\d+) t1 (-?\d+) t2 (-?\d+)', last)
f = re.search(r'frames (\d+) sha256 ([0-9a-f]{64})', last)
chk(m and f, 'last: ' + ' / '.join(last.splitlines()))
if not ok:
    sys.exit(1)
mode, pre, clear, typ, b, t1, t2 = m.groups()
# the phone's dry run fills every frame with the control template (patch 0028)
r = subprocess.run([cli, comp, '--template', ct, '--grey', e + '/grey', '--old', e + '/levels_before', '--mode', '0x203',
                    '--pre', pre, '--clear', '1', '--t1', t1, '--t2', t2], capture_output=True, text=True)
o = r.stdout.strip().splitlines()
print('   CLI: ' + ' | '.join(o[-3:]))
cm = re.search(r'mode (0x[0-9a-f]+) clear (\d) type (\d+) bin (-?\d+)', r.stdout)
cf = re.search(r'frames (\d+) sha256 ([0-9a-f]{64})', o[-1] if o else '')
chk(r.returncode == 0 and cm and cf, 'CLI ran')
if not ok:
    sys.exit(1)
chk(cm.groups() == (mode, clear, typ, b), 'mode/clear/type/bin agree: CLI %s, phone %s' % (cm.groups(), (mode, clear, typ, b)))
chk(cf.groups() == f.groups(), 'frames + sha256 agree: CLI %s %s, phone %s %s' % (cf.groups() + f.groups()))
open(outp, 'w').write('%s %s\n' % cf.groups())
sys.exit(0 if ok else 1)
PY
}

# capture_mainline <dir>: called once when the gadget is up. Never fatal: the
# watch loop and the restore continue whatever happens here.
capture_mainline() {
  local cap="$1" ifc="" i ok=0 how="" t
  mkdir -p "$cap"
  H() { python3 -I "$CAPHOST" net "$CTL_ADDR" "$CTL_PORT" "$@"; }
  for i in $(seq 20); do
    ifc=$(grep -il '^02:a9:00:00:00:02$' /sys/class/net/*/address 2>/dev/null | head -n1 | cut -d/ -f5)
    [ -n "$ifc" ] && break; sleep 0.5
  done
  if [ -n "$ifc" ]; then
    echo "   NCM interface $ifc"
    sudo -n "$IPBIN" addr replace 172.16.42.2/24 dev "$ifc" && sudo -n "$IPBIN" link set "$ifc" up || echo "   sudo ip failed"
    for i in $(seq 20); do H ping --timeout 3 2>/dev/null | grep -q 'a9-ctl ok' && { ok=1; break; }; sleep 1; done
  fi
  if [ "$ok" = 1 ]; then
    how=net
    # live kernel log for the rest of the session (background, never fatal)
    python3 -I "$CAPHOST" net "$CTL_ADDR" $((CTL_PORT + 1)) --follow --out "$cap/kmsg-stream.txt" --timeout 900 >/dev/null 2>&1 &
    echo "   kmsg stream -> $cap/kmsg-stream.txt"
    H stay "$STAY" --timeout 5 | sed 's/^/   /'
    H capture --out "$cap/capture.tgz" --timeout 60 || echo "   capture failed"
    H capture-dbg --out "$cap/capture-dbg.tgz" --timeout 60 || echo "   capture-dbg failed"
  else
    echo "   no a9-ctl over NCM; trying the ACM shell"
    t=""
    for i in $(seq 20); do t=$(ls /dev/ttyACM* 2>/dev/null | head -n1); [ -n "$t" ] && break; sleep 0.5; done
    if [ -n "$t" ]; then
      how=acm
      sudo -n "$PY3" -I "$CAPHOST" acm "$t" stay "$STAY" --out "$cap/stay.txt" --timeout 10 || true
      sudo -n "$PY3" -I "$CAPHOST" acm "$t" capture --out "$cap/capture.tgz" --timeout 90 || echo "   capture failed"
      sudo -n "$PY3" -I "$CAPHOST" acm "$t" capture-dbg --out "$cap/capture-dbg.tgz" --timeout 90 || echo "   capture-dbg failed"
      sudo -n chown "$(id -u):$(id -g)" "$cap"/* 2>/dev/null
    else
      echo "   no /dev/ttyACM* either: no capture"; return 1
    fi
  fi
  for f in capture capture-dbg; do
    [ -s "$cap/$f.tgz" ] && tar -xzf "$cap/$f.tgz" -C "$cap" 2>/dev/null
  done
  echo "   captured over $how: $(ls "$cap" | tr '\n' ' ')"
  [ -f "$HERE/analyze-capture.py" ] && python3 -I "$HERE/analyze-capture.py" "$cap" > "$cap/summary.txt" 2>&1 \
    && sed -n '1,40p' "$cap/summary.txt" | sed 's/^/   | /'
  # late capture, then optionally E1, each into its own subdirectory
  fetch() {  # fetch <subdir>
    local sub="$cap/$1"; mkdir -p "$sub"
    if [ "$how" = net ]; then
      H capture --out "$sub/capture.tgz" --timeout 60; H capture-dbg --out "$sub/capture-dbg.tgz" --timeout 60
    else
      sudo -n "$PY3" -I "$CAPHOST" acm "$t" capture --out "$sub/capture.tgz" --timeout 90
      sudo -n "$PY3" -I "$CAPHOST" acm "$t" capture-dbg --out "$sub/capture-dbg.tgz" --timeout 90
      sudo -n chown "$(id -u):$(id -g)" "$sub"/* 2>/dev/null
    fi
    for f in capture capture-dbg; do [ -s "$sub/$f.tgz" ] && tar -xzf "$sub/$f.tgz" -C "$sub" 2>/dev/null; done
    [ -f "$HERE/analyze-capture.py" ] && python3 -I "$HERE/analyze-capture.py" "$sub" > "$sub/summary.txt" 2>&1
    echo "   $1 capture: $(ls "$sub" | wc -l) files"
  }
  echo "   waiting $LATE s for the late capture"; sleep "$LATE"; fetch late
  alive() { [ "$how" != net ] && return 0; H ping --timeout 5 2>/dev/null | grep -q 'a9-ctl ok'; }
  if [ "$E2PRE" = 1 ]; then
    # before E1: the bootloader's DSI state is still live (MDSS not reset yet).
    # Bridge ACK here but NACK after E1 = the bridge needs the DSI clock.
    echo "   E2a-pre: i2c-probe before E1 (read-only)"
    if [ "$how" = net ]; then H i2c-probe --out "$cap/e2a-pre.txt" --timeout 40
    else sudo -n "$PY3" -I "$CAPHOST" acm "$t" i2c-probe --out "$cap/e2a-pre.txt" --timeout 40; fi
    sed 's/^/   | /' "$cap/e2a-pre.txt" 2>/dev/null
  fi
  if [ "$E1" = 1 ]; then
    echo "   E1: eink-load core (dispcc + msm; no bridge, no power gate)"
    if [ "$how" = net ]; then H eink-load core --timeout 60 | sed 's/^/   | /'
    else sudo -n "$PY3" -I "$CAPHOST" acm "$t" eink-load core --out "$cap/e1-load.txt" --timeout 60; cat "$cap/e1-load.txt" 2>/dev/null; fi
    sleep 60
    if alive; then fetch e1; else echo "   !! phone stopped answering after E1: no more commands (see kmsg-stream.txt)"; E2=0; fi
  fi
  if [ "$E2" = 1 ]; then
    echo "   E2a: i2c-probe (read-only)"
    if [ "$how" = net ]; then H i2c-probe --out "$cap/e2a.txt" --timeout 40
    else sudo -n "$PY3" -I "$CAPHOST" acm "$t" i2c-probe --out "$cap/e2a.txt" --timeout 40; fi
    sed 's/^/   | /' "$cap/e2a.txt" 2>/dev/null
    echo "   E2b: eink-load bridge (panel + tc358767; gates stay closed)"
    if [ "$how" = net ]; then H eink-load bridge --timeout 60 | sed 's/^/   | /'
    else sudo -n "$PY3" -I "$CAPHOST" acm "$t" eink-load bridge --out "$cap/e2-load.txt" --timeout 60; cat "$cap/e2-load.txt" 2>/dev/null; fi
    sleep 30
    if alive; then fetch e2; else echo "   !! phone stopped answering after E2b: no more commands"; E3=0; fi
  fi
  if [ "$E3" = 1 ]; then
    if [ "$how" = net ]; then eink_run "$cap" || echo "   E3-E5 sequence stopped (see $cap/eink-seq/)"
    else echo "   E3-E5 need the NCM link (a9-ctl over TCP); ACM only: skipped"; fi
  fi
  # hand the timer back: the dead-man is long past due and fires now -> fastboot
  if [ "$how" = net ]; then alive && H release --timeout 5 | sed 's/^/   /'
  else sudo -n "$PY3" -I "$CAPHOST" acm "$t" release --out /dev/null --timeout 10 || true; fi
  return 0
}

if [ "$MODE" = sim ]; then
  # tests only (tests/mainline-eink-seq/qemu-e2e.sh): QEMU's a9-ctl, never the phone
  CTL_ADDR=${A9_CTL_ADDR:?--sim needs A9_CTL_ADDR}; CTL_PORT=${A9_CTL_PORT:-2323}
  SIM_SLOW=${A9_SIM_SLOW:-1}   # QEMU is slower than the phone: longer host timeouts
  case "$CTL_ADDR" in 172.16.42.*) fail "--sim refuses the phone's address $CTL_ADDR" ;; esac
  [ "$E3" = 1 ] || fail "--sim needs --e3, --e5ab or --e5"
  eink_offline_checks
  how=net; cap=${A9_SIM_CAP:?}; mkdir -p "$cap"
  H() { python3 -I "$CAPHOST" net "$CTL_ADDR" "$CTL_PORT" "$@"; }
  alive() { H ping --timeout 5 2>/dev/null | grep -q 'a9-ctl ok'; }
  python3 -I "$CAPHOST" net "$CTL_ADDR" $((CTL_PORT + 1)) --follow --out "$cap/kmsg-stream.txt" --timeout $(( 600 * SIM_SLOW )) >/dev/null 2>&1 &
  sleep 2
  eink_run "$cap"; rc=$?
  sleep 1; kill %1 2>/dev/null
  echo "SIM: eink_run rc $rc"; exit $rc
fi
offline_checks
[ "$MODE" = offline ] && { echo "OFFLINE CHECK: PASS"; exit 0; }

if [ "$MODE" = write ] || [ "$MODE" = restore ]; then
  [ -t 0 ] && [ -t 1 ] || fail "--$MODE needs a human at a terminal (stdin/stdout are not a TTY)"
  [ "${A9_MAINLINE_HUMAN:-}" = yes ] || fail "--$MODE needs A9_MAINLINE_HUMAN=yes, set by the human operator"
  code=$(( (RANDOM % 9000) + 1000 ))
  echo "This $MODE writes boot_b and dtbo_b and switches slots. Type: I approve $code"
  read -r ans; [ "$ans" = "I approve $code" ] || fail "not approved"
fi
if [ "$MODE" = restore ]; then a9_usb_lock; restore; exit $?; fi

phone_checks
[ "$MODE" = check ] && { echo "CHECK: PASS. A human may now run --write."; exit 0; }

# ------------------------------------------------------------------ write
a9_usb_lock
a9_run_dir mainline-first-boot "$IMG"
exec > >(tee -a "$A9_RUN/log.txt") 2>&1
echo "image $(basename "$IMG") $(awk '{print $1}' "$IMG.sha256")" >> "$A9_RUN/meta.txt"
"$ADB" reboot bootloader
a9_wait_fastboot no 120 || fail "bootloader fastboot did not appear"
bootloader_checks || { echo "precondition failed in the bootloader; nothing written"; "$FB" reboot; exit 1; }
ok "bootloader: slot a active+successful, no snapshot, charger screen off"
"$FB" flash boot_b "$IMG" || { echo "flash boot_b failed"; restore; exit 1; }
"$FB" flash dtbo_b "$DTBO" || { echo "flash dtbo_b failed"; restore; exit 1; }
"$FB" set_active b && [ "$(a9_fbvar current-slot)" = b ] || { echo "set_active b failed"; restore; exit 1; }
echo "-- fastboot reboot (reason 0, BCB untouched) into mainline on slot b"
"$FB" reboot
T0=$(date +%s)

# watch: who shows up on USB, and when
GADGET=0 OUTCOME=timeout
while [ $(( $(date +%s) - T0 )) -lt "$WATCH" ]; do
  t=$(( $(date +%s) - T0 ))
  if [ "$GADGET" = 0 ] && lsusb -d 1d6b:0104 >/dev/null 2>&1; then
    GADGET=1; echo "t+${t}s mainline USB gadget 1d6b:0104 up (ACM shell /dev/ttyACM*, NCM 172.16.42.1)"
    capture_mainline "$A9_RUN/capture" || echo "   (capture incomplete; the dead-man still runs)"
  fi
  if timeout 5 "$FB" devices 2>/dev/null </dev/null | grep -q fastboot; then
    OUTCOME=fastboot; echo "t+${t}s bootloader fastboot (beacon / dead-man / fail-fast; gadget seen: $GADGET)"; break
  fi
  case "$(a9_adb_state)" in
    device) OUTCOME=android; echo "t+${t}s Android: pre-PON-probe resets exhausted slot B's retries, ABL fell back"; break ;;
    recovery) OUTCOME=recovery; echo "t+${t}s recovery (unexpected: no reason 1 is ever written)"; break ;;
  esac
  sleep 2
done
echo "outcome: $OUTCOME after $(( $(date +%s) - T0 )) s (gadget seen: $GADGET)" | tee -a "$A9_RUN/meta.txt"
# interpretation (doc section 4.4): fastboot ~1 min after reboot = init ran, no UDC;
# with gadget: healthy, fastboot after the capture's release (or 120 s without
# capture); earlier/without gadget = kernel reset after PON probe
if [ "$OUTCOME" = timeout ]; then
  echo "!! nothing on USB for $WATCH s. Phone may be hung in mainline with the watchdog not running."
  echo "!! Exit B of the a9-recover skill: ask the human to hold Power 10-15 s (PON reason stays 2 -> fastboot)."
  exit 1
fi
restore
