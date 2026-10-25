#!/usr/bin/env bash
set -euo pipefail
export A9_ANDROID=/home/user/A9-build/mainline/wifi-android
export A9_BACKUPS=/home/user/a9-assets/Backups
export A9_DATA=/home/user/a9-assets
export A9_GOLDEN=/home/user/a9-assets/Backups/edl_2026-10-14_110759
export A9_GSI_IMG=/home/user/a9-assets/roms/a16/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v8.img
export A9_WIFI_BINARIES=/home/user/A9-build/mainline/sol-wifi/run14-fix/android
export A9_WIFI_SNAPSHOT=/home/user/A9-build/mainline/wifi-android/kernel-snap-wifi
export PATH=/nix/store/829wb290i87wngxlh404klwxql5v18p4-python3-3.13.7/bin:/home/user/.codex/packages/app-server-daemon/releases/0.163.0-x86_64-unknown-linux-musl/codex-path:/home/user/.codex/tmp/arg0-linux/codex-arg0TQ8ezl:/home/user/.codex/tmp/arg0-linux/codex-arg0NKWWmZ:/home/user/.npm-global/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/codex-path:/home/user/.local/bin:/home/user/.npm-global/bin:/run/wrappers/bin:/home/user/.nix-profile/bin:/nix/profile/bin:/home/user/.local/state/nix/profile/bin:/etc/profiles/per-user/user/bin:/nix/var/nix/profiles/default/bin:/run/current-system/sw/bin
export PYTHONPATH=/nix/store/g85cbzcan6ja3y749ycizqw6cbbzahbq-python3.13-unicorn-2.1.4/lib/python3.13/site-packages
export A9_HOME=/home/user/A9 A9_DEVICE_JSON=/home/user/A9/local/device.json ADB=/nix/store/q17av8m8k96y1yqqssbbpi9r8fnbyhp2-android-tools-35.0.2/bin/adb FB=/nix/store/q17av8m8k96y1yqqssbbpi9r8fnbyhp2-android-tools-35.0.2/bin/fastboot
exec bash /home/user/A9/scripts/mainline/android/android-boot.sh --wifi --eink --boot /home/user/A9-build/mainline/wifi-android/out/boot-demo-wifi.img --vendor /home/user/A9-build/mainline/wifi-android/s2-87d/vendor_b-s2-gated.img --waveform /home/user/A9-build/mainline/firmware/hisense/a9-waveform.bin --template /home/user/A9-build/mainline/firmware/hisense/a9-ctrl-template.bin --hold 5400 --no-restore "$@"
