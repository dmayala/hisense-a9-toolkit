#!/system/bin/sh
# Explicit offline-prepared staging. All sources/destinations must be RAM.
# Keeps the original seed immutable for health checks; listener changes a copy.
set -eu
fail() { echo "S1-LISTENER-ROOT-FAIL $*" >&2; exit 1; }
[ "$#" = 2 ] && [ "$1" = --prepare-root ] || fail arguments
tuple=$2
[ "${#tuple}" = 64 ] || fail tuple
case "$tuple" in *[!0-9a-f]*) fail tuple;; esac
[ "$(id -u)" = 0 ] || fail root
cd /data/local/tmp/sensors-s1 || fail bundle
[ "$(sha256sum SHA256SUMS | cut -d ' ' -f 1)" = "$tuple" ] || fail tuple-mismatch
sha256sum -c SHA256SUMS >/dev/null || fail bundle-hash
persist=0; data=0
while read -r dev point fs options rest; do
    case "$dev" in *by-name/persist*|*/persist|*/persist_a|*/persist_b) fail real-persist;; esac
    case "$point" in
        /data)
            case "$dev $fs" in '/dev/block/ram0 ext4'|'/dev/ram0 ext4') ;; *) fail nonram-data;; esac
            data=$((data+1));;
        /mnt/vendor/persist)
            [ "$dev $fs" = 'tmpfs tmpfs' ] || fail nonram-persist
            persist=$((persist+1));;
        /mnt/vendor/persist/*) fail nested-mount;;
    esac
done </proc/mounts
[ "$persist" = 1 ] && [ "$data" = 1 ] || fail mounts
root=/mnt/vendor/persist/s1-listener-root
[ ! -e "$root" ] && [ ! -L "$root" ] || fail consumed-root
[ "$(readlink -f /mnt/vendor/persist/sensors)" = /mnt/vendor/persist/sensors ] || fail seed-alias
[ "$(readlink -f vendor-config)" = /data/local/tmp/sensors-s1/vendor-config ] || fail vendor-alias
# Read-only SoC identity files (hash-pinned bundle copies; the real /sys is never read).
[ "$(readlink -f soc0)" = /data/local/tmp/sensors-s1/soc0 ] || fail soc0-alias
for n in hw_platform platform_subtype platform_subtype_id platform_version revision soc_id; do
    [ -f "soc0/$n" ] && [ ! -L "soc0/$n" ] || fail soc0-missing
done
[ "$(find soc0 | wc -l)" = 7 ] || fail soc0-extra
find /mnt/vendor/persist/sensors vendor-config soc0 \( -type l -o -type b -o -type c -o -type p -o -type s \) > root-specials || fail source-scan
[ ! -s root-specials ] || fail source-special
sha256sum -c registry.sha256 >/dev/null || fail seed-hash
umask 077
mkdir "$root" || fail create
chmod 0700 "$root" || fail root-mode
mkdir -p "$root/persist" "$root/vendor/etc"
timeout 10 cp -R /mnt/vendor/persist/sensors "$root/persist/sensors" || fail copy-registry
timeout 10 cp -R vendor-config "$root/vendor/etc/sensors" || fail copy-config
mkdir -p "$root/sys/devices"
timeout 10 cp -R soc0 "$root/sys/devices/soc0" || fail copy-soc0
find "$root" -type l > root-specials || fail copied-scan
[ ! -s root-specials ] || fail copied-link
[ "$(readlink -f "$root")" = "$root" ] || fail root-alias
echo 'S1-LISTENER-ROOT-STAGED RAM-COPY; no attach or registry-delivery claim'
