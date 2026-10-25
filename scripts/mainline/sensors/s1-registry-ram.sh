#!/system/bin/sh
# Future reviewed staging only. Does not start any DSP or file listener.
# Seed/hash manifests are private inputs, never put them in the repository.
set -eu
fail() { echo "S1-RAM-FAIL $*" >&2; exit 1; }
[ "$#" = 2 ] && [ "$1" = --stage-ram ] || fail 'usage: --stage-ram <reviewed-bundle-tuple>'
tuple=$2
[ "${#tuple}" = 64 ] || fail tuple
case "$tuple" in *[!0-9a-f]*) fail tuple;; esac
[ "$(id -u)" = 0 ] || fail root
cd /data/local/tmp/sensors-s1 || fail bundle
[ ! -L registry-seed.tar ] && [ ! -L registry.sha256 ] && [ ! -L SHA256SUMS ] || fail bundle-link
[ "$(sha256sum SHA256SUMS | cut -d ' ' -f 1)" = "$tuple" ] || fail tuple-mismatch
sha256sum -c SHA256SUMS >/dev/null || fail bundle-hashes
[ "$(getprop ro.boot.slot_suffix)" = _b ] || fail slot
[ "$(getprop sys.boot_completed)" = 1 ] || fail boot
[ ! -d /sys/module/qcom_q6v5_pas ] || fail prior-pas
data_count=0
while read -r dev point fs rest; do
    if [ "$point" = /data ]; then
        data_count=$((data_count + 1))
        case "$dev $fs" in '/dev/block/ram0 ext4'|'/dev/ram0 ext4') ;; *) fail nonram-data;; esac
    fi
done </proc/mounts
[ "$data_count" = 1 ] || fail nonram-data
for gate in /sys/module/a9_eink/parameters/enable /sys/module/panel_hisense_hlte556n_eink/parameters/enable /sys/module/fp9929a/parameters/allow_power_on; do
    [ "$(cat "$gate")" = N ] || fail gate
done
# Refuse any existing mount of the destination or descendants. A real persist
# mount is never replaced, unmounted, read, copied, or used as a seed.
while read -r mid parent major root point rest; do
    case "$point" in /mnt/vendor/persist|/mnt/vendor/persist/*) fail prior-persist-mount;; esac
done </proc/self/mountinfo
for part in /mnt /mnt/vendor /mnt/vendor/persist; do
    [ ! -L "$part" ] || fail mountpoint-link
done
[ -d /mnt/vendor ] || fail missing-parent
# Refuse an existing populated directory before opening any file beneath it.
if [ -e /mnt/vendor/persist ]; then
    [ -d /mnt/vendor/persist ] || fail mountpoint
    [ -z "$(ls -A /mnt/vendor/persist)" ] || fail populated-destination
else
    mkdir /mnt/vendor/persist || fail mkdir
fi
mounted=0
cleanup() {
    rc=$?
    trap - EXIT HUP INT TERM
    if [ "$mounted" = 1 ] && [ "$rc" != 0 ]; then
        timeout 5 umount /mnt/vendor/persist || echo S1-RAM-UNMOUNT-UNVERIFIED >&2
    fi
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
timeout 5 mount -t tmpfs -o size=8m,mode=0700,nosuid,nodev,noexec tmpfs /mnt/vendor/persist || fail mount
mounted=1
persist_count=0
while read -r dev point fs rest; do
    if [ "$point" = /mnt/vendor/persist ]; then
        persist_count=$((persist_count + 1))
        [ "$dev $fs" = 'tmpfs tmpfs' ] || fail wrong-mount
    fi
done </proc/mounts
[ "$persist_count" = 1 ] || fail wrong-mount
# The hash-bound archive is constructed with ordinary files/directories only
# by s1-prepare.py, whose bounded seed walk rejects links/special files.
timeout 10 tar -xf registry-seed.tar -C /mnt/vendor/persist || fail extract
timeout 10 sha256sum -c registry.sha256 >/dev/null || fail seed-hashes
echo 'S1-RAM-COPY STAGED; registry listener NOT_IMPLEMENTED; no DSP start'
