# a9-env.sh — the one place that says where things live. Sourced by every
# script in this directory; override any of these from the environment.
#
#   A9_DATA     firmware, loaders, backups   (~/a9-assets)
#   A9_FW       stock factory package — READ-ONLY reference, the unbrick path
#   A9_BACKUPS  where new backups are written; existing ones are read-only
#   ADB / FB    native android-tools from PATH
#   EDL         bkerler edlclient, via scripts/edl (adds UFS + loader defaults)
#   LOADER      firehose programmer. The _001360E1 variant — see the a9-edl skill.
#
# shellcheck shell=bash

A9_HOME="${A9_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
A9_DATA="${A9_DATA:-$HOME/a9-assets}"
A9_FW="${A9_FW:-$A9_DATA/HLTE556N}"
A9_BACKUPS="${A9_BACKUPS:-$A9_DATA/Backups}"

ADB="${ADB:-adb}"
FB="${FB:-fastboot}"
EDL="${EDL:-$A9_HOME/scripts/edl}"

# Both firehose loaders are 638,972 bytes but differ from byte 4161; only
# _001360E1 is proven on this handset. It lives in the firmware package.
LOADER="${LOADER:-$A9_FW/prog_firehose_ddr_001360E1.elf}"

# The phone's USB serial, so a second adb/fastboot device (e.g. the Thor) is
# never targeted by accident. adb and fastboot both honour ANDROID_SERIAL.
# Device-specific values live OUTSIDE git, in $A9_HOME/local/device.json
# (gitignored; format: data/device.example.json).
# Precedence: A9_SERIAL from the environment, then A9_DEVICE (name or serial),
# then the registry's default; empty = "the only device attached".
# A9_SERIAL= (set but empty) forces "any".
# Registry of known A9s: local/device.json (scripts/a9-devices.sh). With one
# phone nothing needs configuring: the first run records it as the default.
# Several phones: `a9-devices.sh use <name>`, or A9_DEVICE=<name|serial> per command.
A9_DEVICE_JSON="${A9_DEVICE_JSON:-$A9_HOME/local/device.json}"
if [ -z "${A9_SERIAL+x}" ]; then
  # An unknown A9_DEVICE must not fall back to "any device": pin a serial that
  # matches nothing, so adb/fastboot fail instead of touching another phone.
  A9_SERIAL=$(A9_HOME="$A9_HOME" A9_DEVICE_JSON="$A9_DEVICE_JSON" python3 -I "$A9_HOME/scripts/a9_devices.py" resolve "${A9_DEVICE:-}") \
    || A9_SERIAL="unknown-A9_DEVICE-${A9_DEVICE:-}"
fi
A9_SERIAL="${A9_SERIAL:-}"
if [ -n "$A9_SERIAL" ]; then export ANDROID_SERIAL="$A9_SERIAL"; fi

export A9_HOME A9_DATA A9_FW A9_BACKUPS ADB FB EDL LOADER

# adb state with no trailing whitespace. Native adb does not emit CR, but the
# strip is kept: it costs nothing and the compare is load-bearing.
a9_adb_state() { "$ADB" get-state 2>/dev/null | tr -d '\r\n'; }

# Wait until adb is in STATE and stays usable: `adb shell true` must succeed 3
# times in a row, 2 s apart. "get-state says recovery" alone is not enough: TWRP
# re-enumerates USB ~1 s after adbd first answers, so the next command fails with
# "no devices/emulators found" (seen 2026-10-11; docs/twrp-port-findings.md).
# Usage: a9_wait_stable recovery|device [timeout_s]   (returns 1 on timeout)
a9_wait_stable() {
  local want="$1" end=$(( $(date +%s) + ${2:-300} )) ok=0
  while [ "$(date +%s)" -lt "$end" ]; do
    if [ "$(a9_adb_state)" = "$want" ] && "$ADB" shell true >/dev/null 2>&1; then
      ok=$((ok + 1)); [ "$ok" -ge 3 ] && return 0
    else
      ok=0
    fi
    sleep 2
  done
  return 1
}

# Is exactly ONE Qualcomm EDL (9008) device on USB right now? Two (say a second
# Qualcomm phone in EDL) is refused: edl would talk to whichever it finds first.
a9_edl_count() { lsusb -d 05c6:9008 2>/dev/null | grep -c . ; }
a9_in_edl() {
  local n; n=$(a9_edl_count)
  if [ "$n" -gt 1 ]; then echo "ERROR: $n EDL (9008) devices on USB; unplug all but the A9." >&2; return 1; fi
  [ "$n" -eq 1 ]
}

# One USB-touching write operation at a time, across sessions (flock). Usage:
# a9_usb_lock   (exits 3 if another holds it)
# The adb server is started BEFORE the lock is taken: a server spawned by a
# later adb call would inherit the lock fd and hold the lock forever.
# The lock is owned by a KEEPER process, not by this shell's fd: every child of
# this shell (tee from `exec > >(tee ...)`, sleep, subshells, $(...)) would
# inherit an fd 9, and one survivor of a killed script (2026-10-17: a hold
# loop's subshell + tee + sleep after the parent PID was killed) kept the lock
# for hours. Here fd 9 is closed again in this shell right after the keeper
# took over, so no later child ever holds it. The keeper dies with this shell
# (PR_SET_PDEATHSIG, immediate, also on kill -9; a 0.2 s polling loop where
# setpriv is missing), which releases the lock.
a9_usb_lock() {
  local lock="${XDG_RUNTIME_DIR:-/tmp}/a9-usb.lock"
  command "${ADB:-adb}" start-server </dev/null >/dev/null 2>&1 || true
  exec 9>"$lock"
  # very short wait: a previous script's keeper may still be dying (milliseconds)
  flock -w 0.3 9 || { exec 9>&-; echo "ERROR: another A9 USB write operation is running (lock $lock)." >&2; exit 3; }
  if command -v setpriv >/dev/null 2>&1; then
    setpriv --pdeathsig KILL sh -c 'kill -0 "$1" 2>/dev/null || exit 0; exec sleep 2147483647' a9-usb-lock-keeper $$ \
      </dev/null >/dev/null 2>&1 &
  else
    ( while kill -0 $$ 2>/dev/null; do sleep 0.2; done ) </dev/null >/dev/null 2>&1 &
  fi
  disown $! 2>/dev/null || true
  exec 9>&-
  # Children of this script (twrp.sh, edl, edl-cli.sh ...) must not take the lock
  # again: they see the owner and skip. Owner = this shell's PID, checked alive.
  export A9_USB_LOCKED=1 A9_USB_LOCK_OWNER=$$
}

# Take the USB lock unless an ancestor script that is still alive already holds
# it (A9_USB_LOCK_OWNER, set by a9_usb_lock). Use this in every script that
# enters EDL, reboots the phone or writes over USB, so it works both standalone
# and when called by another locked script. Exits 3 if someone else holds it.
a9_usb_lock_inherit() {
  if [ "${A9_USB_LOCKED:-}" = 1 ] && [ -n "${A9_USB_LOCK_OWNER:-}" ] && kill -0 "$A9_USB_LOCK_OWNER" 2>/dev/null; then
    return 0
  fi
  a9_usb_lock
}

# Per-run evidence directory, never reused: $A9_HOME/runs/<UTC>-<op>/ with
# meta.txt (argv, git HEAD + dirty flag, start time). Sets A9_RUN.
# Usage: a9_run_dir <op> "$@"      then log into "$A9_RUN/..."
a9_run_dir() {
  local op="$1"; shift
  A9_RUN="$A9_HOME/runs/$(date -u +%Y%m%dT%H%M%SZ)-$op"
  mkdir -p "$A9_RUN" || return 1
  {
    echo "op: $op"; echo "argv: $*"; echo "start: $(date -u +%FT%TZ)"
    echo "git: $(git -C "$A9_HOME" rev-parse --short HEAD 2>/dev/null)$(git -C "$A9_HOME" diff --quiet 2>/dev/null || echo ' (dirty)')"
    echo "serial: ${A9_SERIAL:-<any>}"
  } > "$A9_RUN/meta.txt"
  export A9_RUN
}

# Root shell on the phone (Magisk su). Usage: a9_su 'cmd'
a9_su() { "$ADB" shell "su -c '$1'" </dev/null 2>/dev/null | tr -d '\r'; }

# fastboot getvar <var> -> value (empty if unavailable)
a9_fbvar() { timeout 10 "$FB" getvar "$1" 2>&1 </dev/null | tr -d '\r' | sed -n "s/^\(.*) \)\{0,1\}$1: *//p" | head -1; }

# Wait for fastboot with is-userspace = WANT (no = bootloader, yes = fastbootd).
# Usage: a9_wait_fastboot no|yes [timeout_s]   (returns 1 on timeout)
a9_wait_fastboot() {
  local want="$1" end=$(( $(date +%s) + ${2:-120} ))
  while [ "$(date +%s)" -lt "$end" ]; do
    if timeout 5 "$FB" devices 2>/dev/null </dev/null | grep -q fastboot \
       && [ "$(a9_fbvar is-userspace)" = "$want" ]; then return 0; fi
    sleep 2
  done
  return 1
}

# Wait for Android with sys.boot_completed=1. Usage: a9_wait_boot [timeout_s]
a9_wait_boot() {
  local end=$(( $(date +%s) + ${1:-600} ))
  while [ "$(date +%s)" -lt "$end" ]; do
    [ "$(timeout 5 "$ADB" shell getprop sys.boot_completed 2>/dev/null </dev/null | tr -d '\r')" = 1 ] && return 0
    sleep 3
  done
  return 1
}
