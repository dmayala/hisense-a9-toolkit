#!/system/bin/sh
# Fixed bridge for kernel direct firmware lookup. No storage/device access.
# E Ink continues to use a9fw/hisense; never replace its directory or path.
set -eu
fail() { echo "A9_WIFI_FAULT firmware-lookup $*" >&2; exit 1; }
[ "$(getprop ro.a9ml.wifi)" = 1 ] || fail build-disabled
[ "$(cat /sys/module/firmware_class/parameters/path)" = /mnt/a9-wifi/firmware ] || fail kernel-path
for dir in /mnt /mnt/a9-wifi; do
    [ -d "$dir" ] && [ ! -L "$dir" ] || fail unsafe-parent
    [ "$(stat -c %u "$dir")" = 0 ] || fail parent-owner
done
[ "$(stat -c %a /mnt/a9-wifi)" = 700 ] || fail private-parent-mode
if [ ! -e /mnt/a9-wifi/firmware ]; then mkdir -m 0755 /mnt/a9-wifi/firmware; fi
[ -d /mnt/a9-wifi/firmware ] && [ ! -L /mnt/a9-wifi/firmware ] || fail unsafe-root
[ "$(stat -c %u /mnt/a9-wifi/firmware)" = 0 ] || fail root-owner
case "$(stat -c %a /mnt/a9-wifi/firmware)" in 700|750|755) ;; *) fail root-mode ;; esac
for name in qcom ath10k regulatory.db regulatory.db.p7s; do
    src=/vendor/firmware/$name
    dst=/mnt/a9-wifi/firmware/$name
    [ -e "$src" ] && [ ! -L "$src" ] || fail missing-source
    if [ -e "$dst" ] || [ -L "$dst" ]; then
        [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ] || fail existing-target
    else
        ln -s "$src" "$dst" || fail link
    fi
done
# Direct-loader names from the DT/ath10k/regdb consumer, before module probes.
for file in qcom/sm6115/hisense/hlte556n/modem.mdt ath10k/WCN3990/hw1.0/hisense/hlte556n/board.bin regulatory.db regulatory.db.p7s; do
    [ -s "/mnt/a9-wifi/firmware/$file" ] || fail incomplete-tree
done
# Display firmware remains supplied privately by the existing E Ink gate flow.
# The child may not exist yet: consumers request it only when gates are opened.
if [ -e /mnt/a9-wifi/firmware/hisense ] || [ -L /mnt/a9-wifi/firmware/hisense ]; then
    [ -L /mnt/a9-wifi/firmware/hisense ] && [ "$(readlink /mnt/a9-wifi/firmware/hisense)" = /data/local/tmp/a9fw/hisense ] || fail display-target
else
    ln -s /data/local/tmp/a9fw/hisense /mnt/a9-wifi/firmware/hisense || fail display-link
fi
# Bluetooth (WCN3990 BT) firmware, when the vendor ships it.
if [ -d /vendor/firmware/qca ] && [ ! -L /vendor/firmware/qca ]; then
    if [ -e /mnt/a9-wifi/firmware/qca ] || [ -L /mnt/a9-wifi/firmware/qca ]; then
        [ -L /mnt/a9-wifi/firmware/qca ] && [ "$(readlink /mnt/a9-wifi/firmware/qca)" = /vendor/firmware/qca ] || fail bt-target
    else
        ln -s /vendor/firmware/qca /mnt/a9-wifi/firmware/qca || fail bt-link
    fi
fi
echo 'A9_WIFI_FIRMWARE_READY path=/mnt/a9-wifi/firmware'
