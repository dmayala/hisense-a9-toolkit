#!/system/bin/sh
# Inventory only: never open, print, export or delete a fingerprint dump payload.
set -o pipefail
root=${1:-/data/vendor_de/fsfp_dump}
parent=${root%/*}
[ -d "$parent" ] && [ -x "$parent" ] || exit 1
if [ -L "$root" ] || { [ -e "$root" ] && [ ! -d "$root" ]; }; then
    files=1
elif [ -d "$root" ]; then
    # No symlink following. Count every non-directory entry, without its name.
    files=$(find "$root" ! -type d -exec echo x \; 2>/dev/null | wc -l) || exit 1
else
    files=0
fi
printf 'completed 1\nfiles %d\n' "$files"
