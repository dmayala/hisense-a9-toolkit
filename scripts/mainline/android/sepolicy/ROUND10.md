# Enforcing R10 — Wi-Fi native exec and AOD sentinel/state

Host-only candidate on fix/enforcing-r6, based on main6cebb58 and the full
camera-preview vendor. R9 phone passed enforcing, factory MAC, working audit
rate0/backlog8192, Camera2, Codec2/same SF pid, named microphone, BT, sensors,
vibration, doze settings and all four native service states. No phone access by
this worker. Only the existing diagnostic rootsh is permissive.

## Observed failures and scoped fixes

The R9 native logs now establish the failure: prepare reaches confined
companions with MPSS offline; run stops at Android HAL/supplicant readiness.
`comm=sh` in hal_wifi_default is denied execute_no_trans on
hal_wifi_default_exec 19 times; the supplicant has its equivalent denial once.
The native file labels are correct. The init rc deliberately starts each shell
wrapper with its **final HAL seclabel**, preserving private library paths through
the round5 noatsecure rule. The wrapper validates opt-in/readiness/phy before
execing its own native binary. A new transition is inappropriate: this exec
must retain the domain already selected by init. R10 grants exactly those two
source/own-executable pairs execute_no_trans, without a blanket HAL or vendor
execute grant. The disabled AP launcher is unchanged.

AOD setup reached its final setprop, implying its Java prefs rewrite/readback
had succeeded. `sys.linevibrator_type` matches the GSI's **sys. -> system_prop**
prefix, not default_prop. R10 grants the named helper read/set on that existing
context, as the compat service already has. It retains the framework label and
sentinel96. No exact vendor property relabel or generic property grant is added.
The earlier default_prop rule remains for debug.a9.colorfade/doze.display.*.
The supplied captures contain no matching AOD property AVC; this correction is
source/context-derived, not a recovered property-service denial.

The state parser failure is independently reproduced using cached **Android
Toybox under QEMU**: the old GNU BRE `overlay\|static\|stock` expression produces
no match. Portable `[^<]*` capture plus the existing shell enum validation returns
all three modes and rejects an unknown value. No ART startup is added to state.
Missing backup metadata uses ordinary ls rather than ls -Z (which reported
EINVAL); parent and existing-file labels are still printed. This retains access
errors without dumping private XML. Setup still requires closed E Ink gates.

## Rebuild

```sh
R=/home/user/A9-build/mainline/enforcing-round10-20261020
python3 -I scripts/mainline/android/sepolicy/build-enforcing.py \
  --vendor /home/user/A9-build/mainline/camera-preview-20261020/vendor.img \
  --boot /home/user/A9-build/mainline/integrated-20261020m/boot.img \
  --min-free-gib 8 --qualification-audit --out "$R"
sha256sum "$R/boot.img" "$R/vendor.img"
```

Fresh sparse output, no kernel rebuild. Boot and audit helper are byte-identical
to R9. Current audio/camera/preview/Codec2 RGBA/GNSS time/GLES/E Ink/kernel pieces,
factory-MAC handling and pinned Wi-Fi controller/manifests remain intact.

## Coordinator commands — worker executes none

Use the existing exclusive slot-B owner/current CODE, matching GSI, both images
and original dead-man/restore procedure. No same-boot retry after Wi-Fi failure.

```sh
R=/home/user/A9-build/mainline/enforcing-round10-20261020
/home/user/A9-build/mainline/vendor-test-20261020/direct-b.sh \
  "$R/boot.img" "$R/vendor.img" "$CODE"
adb shell getenforce
adb shell cat /sys/fs/selinux/enforce
adb shell getprop ro.boot.a9audit
adb shell getprop sys.boot_completed
printf '%s\n' 'dmesg | grep -E "a9ml-audit:|audit_lost|audit_rate_limit|audit_backlog"' |
  adb shell 'timeout 30 nc 127.0.0.1 2324'
```

Schedule setup at the wrapper's **closed setup point before dev-open**. Never
bypass the script's gate check or run setup during composition. State is cheap.

```sh
printf '%s\n' 'runcon u:r:a9ml_key_aod:s0 /system/bin/sh -c "id -Z; exec /vendor/bin/a9ml-key-aod.sh setup"' |
  adb shell 'timeout 60 nc 127.0.0.1 2324' > aod-setup-r10-private.txt
printf '%s\n' 'runcon u:r:a9ml_key_aod:s0 /system/bin/sh -c "id -Z; exec /vendor/bin/a9ml-key-aod.sh state"' |
  adb shell 'timeout 30 nc 127.0.0.1 2324' > aod-state-r10-private.txt
adb shell ps -AZ | grep -E 'wifi-service|wpa_supplicant|camera.provider|graphics.allocator'
```

Run the existing admitted Wi-Fi session/connection/HTTP procedure once. Require
live native binaries in their HAL SIDs, readiness, MPSS running, session success,
factory-MAC equality at its normal stage and HTTP204. If it fails, retain the
first native reason immediately, before teardown:

```sh
printf '%s\n' 'for p in /mnt/a9-wifi/session/prepare.out /mnt/a9-wifi/session/run.out; do echo "FILE $p"; if [ -f "$p" ]; then grep -nE "STOP:|Cannot create control socket|SIOC|Operation not permitted|Permission denied|A9_WIFI_(FAULT|FATAL)|PREPARED" "$p"; fi; done; for p in /mnt/a9-wifi/trial/stage /mnt/a9-wifi/trial/fault; do echo "FILE $p"; [ ! -f "$p" ] || cat "$p"; done' |
  adb shell 'timeout 30 nc 127.0.0.1 2324' > wifi-native-r10-private.txt
adb logcat -b all -d > logcat-r10-private.txt
printf '%s\n' 'dmesg | grep -E "a9ml-audit:|a9wifi-session|avc:|audit_lost|audit_rate_limit|audit_backlog|callbacks suppressed"' |
  adb shell 'timeout 30 nc 127.0.0.1 2324' > dmesg-avc-r10-private.txt
cat dmesg-avc-r10-private.txt logcat-r10-private.txt |
  python3 -I scripts/mainline/android/sepolicy/sanitize-avc.py > vectors-r10.json
```

Keep metadata and stage output/status. AOD must finish setup, state must report
overlay and sentinel96, then clock/minute updates and wake/redraw must work.
Retain private captures locally; never commit MAC/SSID/IP or raw prefs XML.

## Required enforcing passes

| Check | Required result |
|---|---|
| Mode/audit | Enforcing/enforce1/boot_completed1, a9audit1, final rate0/backlog8192; preserve loss delta |
| Wi-Fi/GNSS | native HAL/supplicant live in intended SIDs, ready/session succeeds, MPSS running, factory-MAC equality, connected/HTTP204, actual GNSS counters |
| AOD | named setup metadata/stages, prefs edit/readback, property96, state overlay/doze1/1; visible clock/minute update and wake/redraw |
| Media | current RGBA previews, front converged/rear Camera2 JPEG, Codec2 Surface60queued/60callbacks/EOS/sameSFpid and ByteBuffer60/60 |
| Other retained | named mic capture, four native service states, GLES196610, stable SF/system_server/logd, E Ink, BT, five sensors, vibration/battery |
| Evidence | first native failure reason if failed; private AVCs/loss capture; no unhandled functional denial |

Unchanged media/audio commands are in [ROUND8](ROUND8.md). Host compilation,
source permission queries and QEMU regex tests do not qualify phone behavior.
