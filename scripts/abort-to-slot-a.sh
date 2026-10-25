#!/usr/bin/env bash
#
# abort-to-slot-a.sh — get the phone back to working stock Android after a
# failed custom-recovery test.
#
# !! THE LAYOUT THIS SCRIPT WAS WRITTEN FOR NO LONGER HOLDS !!
#
# Written for the layout established 2026-08-09:
#   slot A  stock system_a + stock recovery_a   <- the good slot
#   slot B  no system_b, used only for recovery testing
#
# Measured 2026-08-11 that was reversed (system_b only), but as of 2026-10-10
# slot A is POPULATED again: sync-slot-a.sh made it a byte copy of slot B (LOS
# 23.2, the golden fallback). The preflight below is unchanged.
#
# Still NOT what agents want: step 2 re-flashes recovery_b with STOCK recovery
# from the old 2026-08-08 backup, removing the working TWRP. Use the a9-recover
# skill, rung R3b ("boot the golden slot A": fastboot set_active a; fastboot
# reboot), instead.
#
# Run this the moment a TWRP test fails. It is idempotent and safe to re-run.
#
# If the device is NOT in fastboot:
#   * bootlooping   -> hold Power ~20s to force off, then Vol Down + Power,
#                      release Power at the Hisense logo, choose "Enter fastboot"
#   * in inkOS recovery -> pick 正常启动系统 (Boot system normally), or
#                      重新启动 (Reboot), then use the key combo
#   * nothing on USB -> EDL. See scripts/restore-factory-gpt-lun4.sh and
#                      scripts/flash-stock-super.sh
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"
STOCK_RECOVERY="${STOCK_RECOVERY:-$A9_BACKUPS/2026-08-08_055553/recovery_b.img}"

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

a9_usb_lock_inherit
echo "== Abort: return to working stock on slot A"

if ! timeout 15 "$FB" devices 2>/dev/null | grep -q fastboot; then
  echo "ERROR: device is not in fastboot. See the header of this script." >&2
  exit 1
fi

echo "-- current slot state:"
timeout 40 "$FB" getvar all 2>&1 | sed 's/(bootloader) //' \
  | grep -E "current-slot|slot-retry-count|slot-successful|slot-unbootable" | sed 's/^/   /'

# 0. PREFLIGHT: does slot A actually have a system to boot?
#
#    system_a/system_b are LOGICAL partitions inside `super`. Only fastbootd
#    can enumerate them -- bootloader fastboot cannot see them at all, so a
#    "not found" there means nothing. Hence two paths.
if [ "$FORCE" = "1" ]; then
  echo "-- preflight SKIPPED (--force)"
else
  userspace=$(timeout 15 "$FB" getvar is-userspace 2>&1 | sed -n 's/^is-userspace: *//p' | head -1)
  if [ "$userspace" = "yes" ]; then
    if timeout 20 "$FB" getvar partition-size:system_a 2>&1 | grep -q "^partition-size:system_a: 0x"; then
      echo "-- preflight OK: system_a exists"
    else
      cat >&2 <<'EOF'

REFUSING TO SWITCH SLOTS: system_a does not exist.

  fastbootd reports no logical partition system_a, so slot A has no system to
  boot. Making it active would repeat the 2026-08-08 brick -- the bootloader
  will try slot A, fail, burn its retry budget and land in recovery.

  As of 2026-08-11 the LIVE slot is B. If you are trying to recover a broken
  system, you almost certainly want to stay on B.

  To repopulate slot A first:  scripts/flash-stock-super.sh
  To override anyway:          scripts/abort-to-slot-a.sh --force
EOF
      exit 2
    fi
  else
    cat >&2 <<'EOF'

-- preflight CANNOT VERIFY (bootloader fastboot, not fastbootd).

  Logical partitions are invisible here, so this script cannot tell whether
  slot A has a system. As of 2026-08-11 it did NOT, and switching would brick.

  Check first, from Android or TWRP:   adb reboot fastboot
                                       fastboot getvar partition-size:system_a
EOF
    if [ ! -t 0 ]; then
      echo "  Non-interactive, refusing. Re-run with --force to override." >&2
      exit 2
    fi
    printf '  Type "yes" to switch to slot A anyway: ' >&2
    read -r reply
    [ "$reply" = "yes" ] || { echo "  Aborted." >&2; exit 2; }
  fi
fi

# 1. Put the known-good slot back in charge. This also resets its retry count
#    and is the single most important step.
echo "-- set_active a"
timeout 60 "$FB" set_active a 2>&1 | grep -E "OKAY|FAILED|Setting" | sed 's/^/   /'

# 2. Remove the failed custom recovery so nothing tries to boot it again.
if [ -f "$STOCK_RECOVERY" ]; then
  echo "-- restoring stock recovery_b"
  timeout 180 "$FB" flash recovery_b "$STOCK_RECOVERY" 2>&1 \
    | grep -E "OKAY|FAILED" | sed 's/^/   /'
else
  echo "-- WARNING: stock recovery_b backup not found at $STOCK_RECOVERY" >&2
fi

# 3. Clear any sticky BCB command. NOTE: do NOT use `fastboot erase misc` --
#    on this Virtual A/B device misc also carries the virtual_ab message at
#    offset 32768, and erasing destroys it.
echo "-- (skipping misc: erase would destroy the virtual_ab header)"

echo "-- rebooting"
timeout 20 "$FB" reboot 2>&1 | tail -1 | sed 's/^/   /'

echo
echo "If the phone shows the inkOS recovery prompt, choose:"
echo "    正常启动系统   (Boot system normally)"
echo "That is expected -- set_active marks the slot unsuccessful, so the first"
echo "boot goes via the prompt. Verified working 2026-08-09."
