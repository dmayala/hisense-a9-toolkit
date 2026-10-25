# Enforcing R9 — radio control socket and named AOD setup

Host-only candidate on fix/enforcing-r6, still based on main6cebb58 and the
camera-preview vendor. R8 phone confirms enforcing1, audit rate0/backlog8192,
both Camera2 JPEGs, Codec2 paths/same SF pid, named microphone capture, BT,
sensors, vibration, service states and named-domain settings reads. Retain these
passes while qualifying the two remaining paths. Only diagnostic rootsh is
permissive; all functional domains enforce. No kernel rebuild.

## Diagnosis and changes

R8's only captured radio vector is `ip` denied udp_socket/create twice. The
controller uses ip to bring wlan0 down/up and install the verified factory MAC.
R9 admits interface-control socket creation and the relevant SIOC ioctls, with
no UDP bind/connect/send/receive. The compatibility MAC path also gets its
packet-socket metadata operations, without packet read/write/send/receive.
[AOSP iplink](https://android.googlesource.com/platform/external/iproute2/+/refs/heads/main/ip/iplink.c)
uses an INET datagram control socket for flag/address ioctls and a packet socket
for hardware-address type/length metadata (blob e4b81b5474f45089e01e73b46b2bae1c735bc6b1).

The supplied R8 direct/logcat/AVC-only dmesg files contain **no controller STOP
or session note lines**. The exact first native prepare/run failure therefore
cannot be recovered from them. The control-socket denial is concrete; mapping
the resulting failure to the MAC stage is a source-supported inference, not a
captured native STOP. Final modem-offline state can follow orderly error cleanup.
No native success or fixed Wi-Fi claim is made before the R9 run.

The session starter now sends its existing stage/tail messages to ordinary
logcat as well as stdout/kmsg, and preserves up to three STOP lines explicitly
before cleanup tails can hide them. The pinned controller, tool manifests,
one-attempt admissions, private-store handling and factory-MAC equality check
are unchanged. Do not retry the controller in the same boot after failure.

AOD setup's first input keyevent needs `input_service` lookup, missing from its
otherwise working Binder allowlist. R9 adds that lookup. The script prints its
domain and ls -Zd metadata for the app directory, prefs directory, XML and backup
**before** setup commands, and again after editing. It prints stage names so a
failure identifies input, framework configuration or SharedPreferences editing.
No XML content, generic new app-data grant, gate write or auto-start is added.
Absent prefs are not automatically a SELinux failure; closed setup creates them.

## Host rebuild

```sh
R=/home/user/A9-build/mainline/enforcing-round9-20261020
python3 -I scripts/mainline/android/sepolicy/build-enforcing.py \
  --vendor /home/user/A9-build/mainline/camera-preview-20261020/vendor.img \
  --boot /home/user/A9-build/mainline/integrated-20261020m/boot.img \
  --min-free-gib 8 --qualification-audit --out "$R"
sha256sum "$R/boot.img" "$R/vendor.img"
```

Use a fresh reproduction output directory. The boot is byte-identical to R8;
the qualification switch stays in its499-byte primary field. The existing
confined audit helper/readback and matching GSI policy hashes remain. All tested
current media/audio/GNSS/GLES/kernel payloads stay intact; only the two requested
shell helpers, policy/init and inode labels change. Sparse copies, >8GiB floor.

## Coordinator commands — worker executes none

Use the existing exclusive slot-B owner/current admission CODE and original
dead-man/restore path, current matching GSI and both output images:

```sh
R=/home/user/A9-build/mainline/enforcing-round9-20261020
/home/user/A9-build/mainline/vendor-test-20261020/direct-b.sh \
  "$R/boot.img" "$R/vendor.img" "$CODE"
adb shell getenforce
adb shell cat /sys/fs/selinux/enforce
adb shell getprop ro.boot.a9audit
adb shell getprop sys.boot_completed
printf '%s\n' 'dmesg | grep -E "a9ml-audit:|audit_lost|audit_rate_limit|audit_backlog"' |
  adb shell 'timeout 30 nc 127.0.0.1 2324'
```

Require final audit readback rate0/backlog8192 after boot completion; the oneshot
stopped state is normal. Do not clear logs or reset lost counters.

Schedule named AOD setup at direct-b's existing **closed** setup point, before
dev-open. If the wrapper has already opened gates, run only state and leave
setup for the next normal closed setup point; do not bypass the gate check.
The script now includes the missing metadata capture itself:

```sh
printf '%s\n' 'runcon u:r:a9ml_key_aod:s0 /system/bin/sh -c "id -Z; exec /vendor/bin/a9ml-key-aod.sh setup"' |
  adb shell 'timeout 60 nc 127.0.0.1 2324' > aod-setup-r9-private.txt
printf '%s\n' 'runcon u:r:a9ml_key_aod:s0 /system/bin/sh -c "id -Z; exec /vendor/bin/a9ml-key-aod.sh state"' |
  adb shell 'timeout 30 nc 127.0.0.1 2324' > aod-state-r9-private.txt
```

Keep full output/return status, including the last stage and metadata. After
successful closed setup, test clock/minute update and wake with existing named
sleep/wake commands. No actual sound without the user-present route procedure.

Run the existing Wi-Fi session/HTTP procedure once with original admissions.
If it fails, collect native files immediately in that same owned session before
USB changes or teardown. No controller retry, companion restart or MAC bypass:

```sh
printf '%s\n' 'for p in /mnt/a9-wifi/session/prepare.out /mnt/a9-wifi/session/run.out; do echo "FILE $p"; if [ -f "$p" ]; then grep -nE "STOP:|Cannot create control socket|SIOC|Operation not permitted|Permission denied|A9_WIFI_(FAULT|FATAL)|PREPARED" "$p"; fi; done; for p in /mnt/a9-wifi/trial/stage /mnt/a9-wifi/trial/fault; do echo "FILE $p"; [ ! -f "$p" ] || cat "$p"; done' |
  adb shell 'timeout 30 nc 127.0.0.1 2324' > wifi-native-r9-private.txt
adb logcat -b all -d > logcat-r9-private.txt
printf '%s\n' 'dmesg | grep -E "a9ml-audit:|a9wifi-session|avc:|audit_lost|audit_rate_limit|audit_backlog|callbacks suppressed"' |
  adb shell 'timeout 30 nc 127.0.0.1 2324' > dmesg-avc-r9-private.txt
cat dmesg-avc-r9-private.txt logcat-r9-private.txt |
  python3 -I scripts/mainline/android/sepolicy/sanitize-avc.py > vectors-r9.json
```

The new logcat tag is **a9wifi-session**. Preserve private captures; reports get
only sanitized vectors and non-identifying failure reasons. Never copy MAC,
SSID, IP, raw prefs or private photos into commits/reports.

## Required enforcing pass list

| Check | Required result |
|---|---|
| Mode/audit | Enforcing/enforce1/boot_completed1, a9audit1, final rate0/backlog8192; loss delta retained |
| Wi-Fi/GNSS | session succeeds, MPSS running, factory-MAC equality at its normal stage, connection/HTTP204; real GNSS counters with network/session available |
| AOD | named-domain setup prints metadata/stages, input lookup succeeds, overlay prefs edit/read succeeds; visible clock/minute update and wake/redraw |
| Retained features | current RGBA previews and both Camera2 JPEG paths; Codec2 Surface60queued/60callbacks/EOS/sameSFpid and ByteBuffer60/60; named microphone capture, stable SF/system_server/logd, GLES196610, E Ink, BT, five sensors, vibration/battery |
| Evidence | four native service states; native prepare/run reason captured if failed; no unhandled functional denial; missing evidence remains pending |

The unchanged media/audio/probe commands are in [ROUND8](ROUND8.md). Host linking,
shell stubs and source queries are not an enforcing phone pass.
