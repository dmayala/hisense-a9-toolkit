#!/usr/bin/env bash
#
# restore-factory-gpt-lun4.sh — rewrite LUN4's GPT from the factory package.
#
# Why: LUN4 holds boot_a/boot_b, and after hand-patching its A/B slot attribute
# bits on 2026-08-08 the bootloader began reporting
#   "Failed to load image from partition: Device Error"
# on BOTH slots, even with a pristine factory boot image. The primary GPT was
# verified after that write; the BACKUP copy never was. This replaces both with
# the factory originals so no hand-edited table remains.
#
# This does exactly what a factory flash does for LUN4's GPT:
#   1. write gpt_main4.bin   -> PrimaryGPT, LUN4 sector 0, 6 sectors
#   2. write gpt_backup4.bin -> BackupGPT,  LUN4 NUM_DISK_SECTORS-5, 5 sectors
#   3. apply patch4.xml      -> 26 fixups: last-usable-LBA, header LBAs, CRC32s,
#                               computed against the REAL disk size
#
# Step 3 matters. Writing the two files alone leaves last_usable_lba and the
# CRCs wrong for this device, because the factory files are generic. Do not
# skip it, and do not hand-write these files.
#
# NOTE: the factory GPT has ALL-ZERO A/B slot attributes (priority=0 active=0
# tries=0 successful=0 unbootable=0). That is the unconfigured state -- no slot
# is marked bootable. Afterwards you MUST run `fastboot set_active a` (or b) so
# the bootloader writes proper attributes through its own bootctrl code path.
#
# It touches ONLY LUN4's GPT: no partition contents, no other LUN, no
# bootloader chain, no modem NV.
#
# Implementation: `edl qfil <rawprogram> <patch> <imagedir>` programs each
# <program> entry (evaluating NUM_DISK_SECTORS-5 against the LUN's real size),
# then sends every DISK <patch> entry to the device (program XML, then
# patch4.xml). qfil also calls
# setbootablestoragedrive when the rawprogram names an xbl partition; the
# two-entry XML below names none, so that does not happen here.
#
# The XML and symlinks go in a scratch dir, never into the read-only firmware
# package.
#
# Requires: EDL (9008), visible here (lsusb -d 05c6:9008).
#
# Usage:  ./restore-factory-gpt-lun4.sh            # check the inputs, writes nothing
#         ./restore-factory-gpt-lun4.sh --write    # restore
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"
FW="$A9_FW"

for f in gpt_main4.bin gpt_backup4.bin patch4.xml; do
  [ -f "$FW/$f" ] || { echo "ERROR: missing $FW/$f" >&2; exit 1; }
done
# sanity: sizes must match the sector counts the factory XML declares
[ "$(stat -c %s "$FW/gpt_main4.bin")"   = 24576 ] || { echo "ERROR: gpt_main4.bin not 6 sectors" >&2; exit 1; }
[ "$(stat -c %s "$FW/gpt_backup4.bin")" = 20480 ] || { echo "ERROR: gpt_backup4.bin not 5 sectors" >&2; exit 1; }
# patch4.xml is applied verbatim, so it must only touch LUN4.
if grep -o 'physical_partition_number="[0-9]*"' "$FW/patch4.xml" | grep -qv '"4"'; then
  echo "ERROR: patch4.xml has entries outside LUN4 — refusing." >&2; exit 1
fi
echo "Inputs OK: gpt_main4.bin, gpt_backup4.bin, patch4.xml ($(grep -c '<patch ' "$FW/patch4.xml") patches)."

# Writing needs --write; a bare run only checks inputs.
if [ "${1:-}" != "--write" ]; then
  echo "Re-run with --write to restore." >&2
  exit 1
fi
a9_usb_lock_inherit
a9_in_edl || { echo "ERROR: no 9008 (EDL) device on USB." >&2; exit 1; }

WORK=$(mktemp -d "${TMPDIR:-/tmp}/a9-gpt-lun4.XXXXXX")
trap 'rm -rf "$WORK"' EXIT
ln -s "$FW/gpt_main4.bin"   "$WORK/gpt_main4.bin"
ln -s "$FW/gpt_backup4.bin" "$WORK/gpt_backup4.bin"
ln -s "$FW/patch4.xml"      "$WORK/patch4.xml"
cat > "$WORK/gpt_lun4_only.xml" <<'EOF'
<?xml version="1.0" ?>
<data>
  <program SECTOR_SIZE_IN_BYTES="4096" file_sector_offset="0" filename="gpt_main4.bin" label="PrimaryGPT" num_partition_sectors="6" partofsingleimage="true" physical_partition_number="4" readbackverify="false" size_in_KB="24.0" sparse="false" start_byte_hex="0" start_sector="0"/>
  <program SECTOR_SIZE_IN_BYTES="4096" file_sector_offset="0" filename="gpt_backup4.bin" label="BackupGPT" num_partition_sectors="5" partofsingleimage="true" physical_partition_number="4" readbackverify="false" size_in_KB="20.0" sparse="false" start_byte_hex="0" start_sector="NUM_DISK_SECTORS-5."/>
</data>
EOF

echo "== Restoring factory GPT on LUN4 (PrimaryGPT + BackupGPT + patch4.xml)"
A9_EDL_ALLOW_RAW=1 "$EDL" qfil gpt_lun4_only.xml patch4.xml "$WORK" 2>&1 | grep -vE '^\s*$' | tail -40
rc=${PIPESTATUS[0]}
[ "$rc" -eq 0 ] || { echo "ERROR: edl exited $rc — stay in EDL and re-run." >&2; exit 1; }

echo
echo "Next:"
echo "  1. ./edl-cli.sh reset          # out of EDL"
echo "  2. fastboot set_active <slot>  # factory GPT leaves slot attrs all-zero;"
echo "                                 # pick the slot that HAS a system"
echo "  3. fastboot continue"
