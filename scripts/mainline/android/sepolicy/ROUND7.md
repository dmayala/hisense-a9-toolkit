# Enforcing R7 — qualification of the current integrated vendor

R6 booted enforcing and retained E Ink/GLES, stable SF/system_server, BT, five
sensors, vibration, Codec2 Surface and ByteBuffer, microphone capture and raw
camera enumeration. R7 addresses the actual failed paths. Phone qualification
remains with agent; the worker performs no device operations.

The base is still tested `cam-aod-surf-20261020/vendor.img` (current main6e2d925
content), plus `integrated-20261020m/boot.img`. All feature executables, modules
and private libraries are retained, including camera15–30fps, AOD DPU, audio,
Codec2 RGBA, GNSS time and GLES. No kernel build is required. Both images below
are required for the qualification boot.

## Failure diagnosis and policy

- Radio: observed init->a9_wifi_radio noatsecure, mnt_vendor_file read by sed
  and sys_rawio during umount. R7 admits those operations, following the A6L
  launcher pattern. No physical block write or additional block ioctl is
  granted; existing read-only seed mounts and RAM-only writes are retained.
- Camera: the full R6 log contains **two CameraManager user_namespace/create
  denials**, despite the initial rate-limited summary. Libcamera's
  `src/libcamera/process.cpp:Process::start` calls clone3 with NEWUSER, NEWNET
  and PIDFD for its isolated IPA. R7 permits that user namespace and SYS_ADMIN
  in the new user namespace, as required by kernel `copy_namespaces`; it does
  not grant host SYS_ADMIN. Existing media/subdevice/video, dma_heap, private
  libraries, IPA proxy execution and private shared memfd rules remain. The
  raw enumerator and camera module loader gain only the observed node stats.
- AOD: Binder service lookup/call already succeeded far enough for the
  server to reply. R6 denied system_server writing the helper's FIFO, causing
  `cmd settings` to fail. R7 admits the transferred pipe for framework replies,
  plus source-backed /data/user symlink and ART boot-cache/APEX metadata reads
  needed by prefs.jar. No new generic system-data file read or panel write is
  granted. An unreadable-or-absent prefs message alone does not establish an
  AVC: RAM /data may have no prefs yet. Check existence/labels before setup.
- Audio: add the observed self dac_read_search capability (54 denials during
  the otherwise successful capture). No route or automatic playback changes.
- Service names are **vendor.graphics.allocator**, **vendor.camera-provider-a9**
  and **vendor.a9ml_logd**, verified in the actual init overlay and R6 ps.
  GSI `init.svc.vendor.*` matches vendor_default_prop before the shorter
  init.svc.* private-status rule; the earlier shell private-status read did
  not cover them. These three exact state properties and the new audit helper
  state get a dedicated type readable by shell/manual helpers. No functional
  caller gains property_service/set on that type. Empty shell getprop values
  must not be treated as proof a native service has exited.

Only the previously authorized diagnostic **a9ml_rootsh** remains permissive.
All functional domains and the audit helper enforce. Matching-GSI private
compiler inputs remove its other inherited permissive declarations; the GSI
cache is unchanged. Existing development secilc -N linking remains, without a
Treble/CTS/neverallow qualification claim.

## Qualification audit switch

`--qualification-audit` adds:

- `androidboot.a9ml.audit_qualification=1` (ro.boot.a9ml.audit_qualification),
  and kernel `audit=1 audit_backlog_limit=8192` in the boot header. Audit=1
  enables CONFIG_CHANGE records: the kernel otherwise defaults audit_enabled
  to OFF and can change the rate without logging that configuration change;
- a qualification precompiled policy linked with **secilc -D**, exposing
  dontaudit-denied operations without changing their allow decisions;
- `vendor.a9ml_auditctl`, the existing `/system/bin/auditctl -r 0`, in its
  existing **auditctl** enforcing domain with AUDIT_CONTROL. No functional
  domain gets audit-control powers and no persistent property is changed.

The new init actions all require the boot flag. They run at early-init and
post-fs-data, and again after the GSI's logd-auditctl oneshot stops: its normal
boot-completion command resets the rate to five/sec. Reapplying after exit
avoids leaving a qualification run at that limit. `-r 0` removes the record
rate limit; it does not disable auditing. The kernel backlog only increases
queue capacity. It does not guarantee loss-free capture, so retain audit_lost
and full logcat evidence. CONFIG_CHANGE `audit_rate_limit=0 ... res=1` and a
successful auditctl exit are the runtime verification, not the flag alone.

Omit `--qualification-audit` for an ordinary build from the same base: no boot
flag/backlog override, no -D, and no automatic auditctl action. To restore
dontaudit after qualification, use that ordinary rebuilt policy as well as its
boot; changing a boot flag cannot alter an already compiled binary policy.

## Host build

```sh
R=/home/user/A9-build/mainline/enforcing-round7-20261020
python3 -I scripts/mainline/android/sepolicy/build-enforcing.py \
  --vendor /home/user/A9-build/mainline/cam-aod-surf-20261020/vendor.img \
  --boot /home/user/A9-build/mainline/integrated-20261020m/boot.img \
  --min-free-gib 8 --qualification-audit --out "$R"
sha256sum "$R/boot.img" "$R/vendor.img"
```

Reproductions need a fresh output directory. Sparse copies preserve the shared
disk reserve; existing source and object caches are not changed. Compiler,
inode-label readback and e2fsck results live with the output pair. Host checks
are not an enforcing runtime pass.

## Coordinator phone commands — worker executes none

Use the existing exclusive slot-B owner, admission and restore/dead-man path.
`CODE` is the coordinator's current lp-write admission, not supplied by this
worker. Use the matching current GSI; do not toggle mode on the old vendor.

```sh
R=/home/user/A9-build/mainline/enforcing-round7-20261020
/home/user/A9-build/mainline/vendor-test-20261020/direct-b.sh \
  "$R/boot.img" "$R/vendor.img" "$CODE"
adb shell getenforce
adb shell cat /sys/fs/selinux/enforce
adb shell getprop sys.boot_completed
adb shell getprop ro.boot.a9ml.audit_qualification
adb shell getprop init.svc.vendor.a9ml_auditctl
adb shell getprop init.svc.vendor.graphics.allocator
adb shell getprop init.svc.vendor.camera-provider-a9
adb shell getprop init.svc.vendor.a9ml_logd
adb shell ps -AZ
adb shell pidof surfaceflinger system_server
adb shell getprop ro.opengles.version
```

The audit service is oneshot: **stopped after exit0 is expected**. Verify its
actual exit/status and final CONFIG_CHANGE rate after boot_completed. Capture
logs before tests as well as afterward; retain raw data privately.

Manual helpers must run in their enforcing SIDs. Before opening the panel,
test AOD setup through the existing root channel (this changes its app prefs
and accessibility binding by design). Diagnose prefs existence using only
metadata; do not dump private app XML:

```sh
printf '%s\n' 'ls -Zd /data/user/0/com.lmqr.ha9_comp_service/shared_prefs /data/user/0/com.lmqr.ha9_comp_service/shared_prefs/com.lmqr.ha9_comp_service_preferences.xml' |
  adb shell 'timeout 30 nc 127.0.0.1 2324'
printf '%s\n' 'runcon u:r:a9ml_key_aod:s0 /system/bin/sh -c "id -Z; exec /vendor/bin/a9ml-key-aod.sh setup"' |
  adb shell 'timeout 60 nc 127.0.0.1 2324'
printf '%s\n' 'runcon u:r:a9ml_key_aod:s0 /system/bin/sh -c "id -Z; exec /vendor/bin/a9ml-key-aod.sh state"' |
  adb shell 'timeout 30 nc 127.0.0.1 2324'
printf '%s\n' 'runcon u:r:a9ml_camcap:s0 /vendor/bin/a9-camcap -l' |
  adb shell 'timeout 30 nc 127.0.0.1 2324'
printf '%s\n' 'runcon u:r:a9ml_audio:s0 /system/bin/sh -c "id -Z; exec /vendor/bin/a9ml-audio.sh inventory"' |
  adb shell 'timeout 30 nc 127.0.0.1 2324'
```

If direct-b.sh has already opened the panel, use `state` now and schedule
`setup` at its existing closed setup point on the next owned boot; do not call
setup while gates are open. Then use the existing Wi-Fi/session+HTTP procedure
and named-domain mic capture from R6, with original route/user-presence rules.
No new credential or RF-start procedure is introduced. Check real MPSS state,
session outcome and GNSS counters; service registration alone is insufficient.

Camera and codec probes remain the phone-passed versions:

```sh
adb install -r /home/user/A9-build/mainline/camera-fps-20261020/probe/a9-camera2-probe.apk
adb shell pm grant org.a9.cameraprobe android.permission.CAMERA
adb shell am force-stop org.a9.cameraprobe
adb shell am start -W -n org.a9.cameraprobe/.CaptureActivity --es facing back --ei min_fps 15 --ei max_fps 30
# Wait for capture, retain result/JPEG privately; repeat with facing front.
adb exec-out run-as org.a9.cameraprobe cat files/result.json
adb install -r /home/user/A9-build/mainline/codec2-surface-20261020/probe/a9-codec2-playback.apk
adb shell am force-stop org.a9.videoprobe
adb shell am start -W -n org.a9.videoprobe/.PlaybackActivity --ez surface true
# Wait about10s, read result and compare SF pid with the value before launch.
adb exec-out run-as org.a9.videoprobe cat files/result.json
adb shell pidof surfaceflinger
# Repeat force-stop/start/result with --ez surface false for ByteBuffer.
adb shell cmd wifi status
adb shell dumpsys android.hardware.gnss.IGnss/default
```

Use the existing GNSS inject-time probe after Wi-Fi/MPSS recover; [R6](ROUND6.md)
contains its unchanged exact invocation. Read counters only, not coordinates.
Capture full logs, including rate/backlog evidence, before USB changes/teardown:

```sh
printf '%s\n' 'dmesg | grep -E "avc:|audit_lost|audit_rate_limit|audit_backlog|callbacks suppressed"' |
  adb shell 'timeout 30 nc 127.0.0.1 2324' > dmesg-avc-r7-private.txt
adb logcat -b all -d > logcat-r7-private.txt
cat dmesg-avc-r7-private.txt logcat-r7-private.txt |
  python3 -I scripts/mainline/android/sepolicy/sanitize-avc.py > vectors-r7.json
```

Do not clear logs or reset audit_lost. Preserve initial and final captures so
old losses can be distinguished from new losses. A final rate0 setting cannot
restore already lost records. Unrelated probe/noatsecure/dontaudit denials may
be visible; return the failing feature's full error chain plus sanitized vectors.

## Required enforcing pass list

| Check | Pass condition |
|---|---|
| Mode/boot | Enforcing, enforce1, boot_completed1; only diagnostic rootsh exempt |
| Audit | qualification flag1, backlog8192, auditctl exit0 and final rate0 after the GSI reset; loss reported explicitly |
| Wi-Fi/GNSS | radio SID enforcing, session succeeds, MPSS running, Wi-Fi association/HTTP204; real GNSS/time counters progress with network available |
| Camera | provider and isolated IPA stay in hal_camera_default; both cameras enumerate and Camera2 produces JPEG with truthful15–30fps metadata; raw graph still visible |
| AOD | named-domain settings replies work; closed setup creates/reads overlay prefs, then visible clock/minute update and wake/redraw |
| Audio | named-domain inventory and same R6 microphone capture pass, no dac_read_search blocker; no automatic output route |
| Retained parity | stable SF/system_server/logd; E Ink redraw, GLES196610, BT ON, five sensor samples, vibration/battery; Surface60queued/60callbacks/EOS/sameSFpid and ByteBuffer60/60 |
| Diagnostics | exact service states readable; full AVC captures retained; no unhandled enforcing denial on a required feature path |

Missing evidence stays pending. No functional domain is relaxed to permissive
to complete a pass row, and host linking is not a phone pass.
