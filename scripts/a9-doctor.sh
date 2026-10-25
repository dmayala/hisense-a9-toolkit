#!/usr/bin/env bash
#
# a9-doctor.sh — is this machine ready to work on the A9? Ported from
# A9Toolkit's `cmd/doctor` (2026-08-13), adapted to build-host.
#
# Checks, in order: required tools, USB/udev access, the phone's mode (via
# a9-health.sh --json, only when something is attached), asset status (via
# a9-assets.sh status) and the git status of ~/A9.
#
# STRICTLY READ-ONLY and fast: no writes, no reboots, no flashing, never talks
# to an EDL device. The phone checks are a9-health.sh, which only reads.
#
# Exit: 0 = no problems (advisories allowed), 1 = at least one problem.
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"

if [ -t 1 ]; then G=$'\e[32m'; R=$'\e[31m'; Y=$'\e[33m'; D=$'\e[2m'; B=$'\e[1m'; N=$'\e[0m'; else G=; R=; Y=; D=; B=; N=; fi
PROBLEMS=0; ADVISORIES=0
hdr()  { printf '\n%s== %s ==%s\n' "$B" "$1" "$N"; }
ok()   { printf '  %sok%s    %s\n' "$G" "$N" "$*"; }
bad()  { printf '  %sFAIL%s  %s\n' "$R" "$N" "$*"; PROBLEMS=$((PROBLEMS + 1)); }
note() { printf '  %swarn%s  %s\n' "$Y" "$N" "$*"; ADVISORIES=$((ADVISORIES + 1)); }
dim()  { printf '  %s%s%s\n' "$D" "$*" "$N"; }

case "${1:-}" in -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;; esac

# ---- 1. tools ---------------------------------------------------------------
hdr "Tools"
for entry in "adb:Android debugging" "fastboot:bootloader flashing" "edl:EDL/firehose (bkerler)" \
             "python3:scripts and mkbootimg" "lsusb:USB enumeration"; do
  t="${entry%%:*}"; why="${entry#*:}"
  if p=$(command -v "$t" 2>/dev/null); then ok "$(printf '%-9s %s' "$t" "$p")"
  else bad "$(printf '%-9s missing: %s' "$t" "$why")"; fi
done
# optional, via nix shell
for entry in "qdl:whole-package rawprogram flashing (linux-msm)" "gdown:Google Drive downloads (a9-assets.sh fetch)" \
             "apksigner:check manager APK signatures (root/README.md)"; do
  t="${entry%%:*}"; why="${entry#*:}"
  if command -v "$t" >/dev/null 2>&1; then ok "$(printf '%-9s on PATH' "$t")"
  else dim "$(printf '%-9s not installed; optional, use: nix shell nixpkgs#%s   (%s)' "$t" "$t" "$why")"; fi
done
if command -v mkbootimg >/dev/null 2>&1 && command -v unpack_bootimg >/dev/null 2>&1; then
  ok "mkbootimg / unpack_bootimg on PATH"
elif [ -f "$A9_HOME/tools/mkbootimg.py" ]; then
  ok "mkbootimg.py / unpack_bootimg.py in $A9_HOME/tools"
else
  note "no mkbootimg/unpack_bootimg (android-tools or ~/A9/tools)"
fi
[ -x "$EDL" ] && ok "edl wrapper: $EDL" || bad "edl wrapper missing or not executable: $EDL"

# ---- 2. USB access ----------------------------------------------------------
hdr "USB access"
if id -nG | tr ' ' '\n' | grep -qx adbusers; then ok "$(id -un) is in group adbusers"
else bad "$(id -un) is not in group adbusers (see ~/host-config/a9-usb.nix / thor-usb.nix)"; fi
if [ -d /dev/bus/usb ]; then
  grp=$(stat -c %G /dev/bus/usb 2>/dev/null)
  ok "/dev/bus/usb present (dir group: $grp)"
  # Check the device node of any phone identity, which is what the rules chmod.
  for vp in 109b: 05c6:9008 05c6:900e; do
    lsusb -d "$vp" 2>/dev/null | while read -r _ bus _ dev _; do
      node="/dev/bus/usb/$bus/${dev%:}"
      g=$(stat -c %G "$node" 2>/dev/null); m=$(stat -c %a "$node" 2>/dev/null)
      if [ "$g" = adbusers ] && [ "$m" = 660 ]; then ok "$node  group adbusers mode 660 ($vp)"
      else printf '  %swarn%s  %s group %s mode %s: the udev rule did not apply (%s)\n' "$Y" "$N" "$node" "$g" "$m" "$vp"; fi
    done
  done
else
  bad "/dev/bus/usb missing: no USB stack visible"
fi
if [ -f "$HOME/host-config/a9-usb.nix" ]; then
  ok "udev rules defined in ~/host-config/a9-usb.nix (109b:*, 05c6:9008/900e -> adbusers)"
else
  note "~/host-config/a9-usb.nix not found; the udev rules for the phone may not be installed"
fi
# Known/attached A9s (local/device.json, gitignored); records any new one.
while read -r line; do ok "A9 registry: $line"; done < <(python3 -I "$A9_HOME/scripts/a9_devices.py" scan 2>&1 | sed -n '2,$p')
# Host services that grab Android/Qualcomm USB devices: fwupd probes fastboot
# (getvar) and caused -108 disconnects on the A6L port; ModemManager opens 9008.
for svc in fwupd ModemManager; do
  if systemctl is-active --quiet "$svc" 2>/dev/null; then
    note "$svc is running: it can probe the phone in fastboot/EDL mid-flash (stop it for flashing sessions)"
  else ok "$svc not running"; fi
done
n9008=$(lsusb -d 05c6:9008 2>/dev/null | grep -c .)
[ "$n9008" -gt 1 ] && bad "$n9008 EDL (9008) devices attached: EDL scripts refuse to run until only the A9 is left"

# ---- 3. the phone -----------------------------------------------------------
hdr "Phone"
attached=0
usbseen=$(lsusb -d 109b: 2>/dev/null; lsusb -d 05c6: 2>/dev/null)
[ -n "$usbseen" ] && attached=1
if [ "$attached" = 1 ] && [ -x "$HERE/a9-health.sh" ]; then
  J=$("$HERE/a9-health.sh" --json 2>/dev/null)
  if [ -n "$J" ] && command -v python3 >/dev/null; then
    printf '%s' "$J" | python3 -I -c '
import json,sys
d=json.load(sys.stdin)
for k,v in d.items():
    if isinstance(v,(list,dict)): v=json.dumps(v)
    print("  %-22s %s" % (k,v))
' 2>/dev/null || printf '%s\n' "$J" | sed 's/^/  /'
    mode=$(printf '%s' "$J" | python3 -I -c 'import json,sys; print(json.load(sys.stdin).get("mode",""))' 2>/dev/null)
    case "$mode" in
      android|recovery|bootloader|fastbootd|sideload|edl) ok "mode: $mode" ;;
      diag) note "mode: diag (05c6:900e) is the diagnostics port, NOT EDL" ;;
      "") note "a9-health.sh returned no mode" ;;
      *) note "mode: $mode" ;;
    esac
  else
    note "a9-health.sh --json gave no usable output"
  fi
else
  dim "no phone on USB (109b:* / 05c6:*); nothing to check. Not a problem."
fi

# ---- 4. assets --------------------------------------------------------------
hdr "Assets ($A9_DATA)"
if [ -x "$HERE/a9-assets.sh" ]; then
  "$HERE/a9-assets.sh" status | sed 's/^/  /'
  rc=${PIPESTATUS[0]}
  [ "$rc" -eq 0 ] || bad "a9-assets.sh status reports a required asset missing or a failed check"
else
  bad "a9-assets.sh missing"
fi

# ---- 5. git -----------------------------------------------------------------
hdr "Git ($A9_HOME)"
if git -C "$A9_HOME" rev-parse --git-dir >/dev/null 2>&1; then
  br=$(git -C "$A9_HOME" rev-parse --abbrev-ref HEAD 2>/dev/null)
  ok "branch $br at $(git -C "$A9_HOME" rev-parse --short HEAD 2>/dev/null)"
  nchg=$(git -C "$A9_HOME" status --porcelain 2>/dev/null | wc -l)
  if [ "$nchg" -eq 0 ]; then ok "working tree clean"
  else note "$nchg changed/untracked path(s):"; git -C "$A9_HOME" status --short 2>/dev/null | head -15 | sed 's/^/        /'; fi
  up=$(git -C "$A9_HOME" rev-list --left-right --count '@{u}...HEAD' 2>/dev/null)
  [ -n "$up" ] && dim "behind/ahead upstream: $up"
else
  note "$A9_HOME is not a git repository"
fi

# ---- summary ----------------------------------------------------------------
hdr "Summary"
if [ "$PROBLEMS" -eq 0 ]; then ok "no problems, $ADVISORIES advisory/advisories"
else printf "  %sFAIL%s  %s problem(s), %s advisory/advisories\n" "$R" "$N" "$PROBLEMS" "$ADVISORIES"; fi
[ "$PROBLEMS" -eq 0 ]
