#!/usr/bin/env bash
# extract-stock.sh -- offline, read-only: copy the stock sensor configuration
# (vendor /etc/sensors) and the persist sensor registry (/sensors only, the
# factory calibration) out of the golden dump into the build dir, then print
# the inventory (inventory.py). No phone; ~/a9-assets is only read (debugfs -R
# opens the images read-only).
#   scripts/mainline/sensors/extract-stock.sh
# Output: ~/A9-build/mainline/sensors/stock/{sensors,persist-sensors,inventory.tsv}
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
GOLD=${A9_GOLDEN:-$HOME/a9-assets/Backups/edl_2026-10-14_110759}
VEN=${A9_GOLDEN_VENDOR:-$HOME/A9-build/mainline/android/golden/vendor_b.img}   # extract-golden-vendor.sh
O=${A9_SENSORS_OUT:-$HOME/A9-build/mainline/sensors}/stock
case "$O" in *a9-assets*) echo "refusing to write under a9-assets" >&2; exit 2;; esac
[ -r "$VEN" ] || { echo "missing $VEN (scripts/mainline/android/extract-golden-vendor.sh)" >&2; exit 1; }
[ -r "$GOLD/lun0/persist.bin" ] || { echo "missing $GOLD/lun0/persist.bin" >&2; exit 1; }
rm -rf "$O/sensors" "$O/persist-sensors"; mkdir -p "$O/persist-sensors"
debugfs -R "rdump /etc/sensors $O" "$VEN" >/dev/null 2>&1
debugfs -R "rdump /sensors $O/persist-sensors" "$GOLD/lun0/persist.bin" >/dev/null 2>&1
[ -f "$O/sensors/sns_reg_config" ] && [ -d "$O/persist-sensors/sensors/registry/registry" ] || { echo "extraction failed" >&2; exit 1; }
python3 -I "$HERE/inventory.py" "$O/sensors/config" "$O/persist-sensors/sensors" | tee "$O/inventory.tsv"
