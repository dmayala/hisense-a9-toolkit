# Enforcing R6 — main6e2d925

Host-only candidate; enforcing runtime parity must be tested by agent. This
supersedes the never-run round5 image derived from stale integrated j.

Inputs are the tested `cam-aod-surf-20261020/vendor.img` (SHA256
5e1740be2a7cacf45179313a8730a8e34d2da92d72aa267229e2b2749e5c95ae) and
`integrated-20261020m/boot.img`. That vendor includes the camera15–30fps bundle,
AOD DPU msm module/prefs jar, audio modules, Codec2 RGBA, GNSS time and GLES
helper. No feature binary, module, trigger or service UID/capability is rebuilt.
Boot kernel/ramdisk/DT and all non-SELinux arguments stay byte-identical; its
three mode arguments become enforcing=1, selinux_force_permissive=0,
androidboot.selinux=enforcing. Both output images are required.

## Policy changes

- Standalone minigbm allocator keeps hal_graphics_allocator_default_exec and
  hal_graphics_allocator_default. Targeted init noatsecure preserves its private
  LD_LIBRARY_PATH, following round5/A6L. Its private a9-gralloc DSOs get their
  own read/map/execute label. Global core/mapper labels remain compatible with
  other clients. Captured allocator DRM_VERSION0x6400 is added to the existing
  PRIME/CREATE_DUMB ioctl list.
- Camera provider/Codec2 retain the existing init noatsecure, private-library
  and media/DMA-heap rules. The isolated IPA proxy can execute in the provider
  domain; its statistics memfds have a private non-executable tmpfs type.
  The raw a9-camcap tool gets a9ml_camcap with V4L2/media ioctls and output-file
  access. Its static executable needs no private-library exception.
- a9ml-audio.sh/a9-audio-ctl/a9-audio-pcm get a9ml_audio, ALSA control/PCM ioctl
  families, manual module loading and the existing media-session volume command.
  No route, playback or audio startup is added.
- a9ml-key-aod.sh gets a9ml_key_aod: ART/app_process, private JIT memfd and
  userfaultfd access, framework Binder commands, app SharedPreferences ownership/
  context restoration and input events. It reads gates/status and cannot write
  panel gates. App data permissions cover the existing Android app-data types
  and bypass MLS for this root-only development helper; this is POC policy,
  not package-scoped release confinement.
- The GLES service maps to existing a9ml_gpu instead of the old phhsu label.
  GNSS time injection gets its same-domain anonymous command pipe; no system
  clock setting, network client or position-injection grant is added.
- Existing Wi-Fi session/RAM-only format/fsck rules and round5 Wi-Fi launcher
  noatsecure remain. Fresh cas denials add strace's read-only INET_DIAG socket
  and namespaced sys_ptrace capability used in proc metadata checks. No
  cross-domain process ptrace or physical block write is added.
- logd stays logd, retaining the existing system-file entrypoint and stopped-
  system-logd fallback triggers. It has no private library environment and gets
  no noatsecure exception. The observed USB HAL sysfs state reads are included.

`evidence-avc-cas` supplies327 unique sanitized type/class/permission vectors
(2216 line copies, not unique events). Many are already covered by rounds3–5 or
will change target type after relabeling. Audit loss reported by the coordinator
makes this a lower bound. Unrelated diagnostic lookups, failed sysfs create
probes, GMS adbd-property reads and physical block ioctls are not blindly allowed.
Raw captures remain local; reports contain no identifiers or raw location data.

Only the existing a9ml_rootsh console is permissive. Private matching-GSI compiler
inputs remove phhsu_daemon/tkcore/ueventd permissive declarations. The supplied
precompiled policy and all three hash sidecars require the same v8 GSI as the
current tests. A different system can select original split-policy compilation.
The existing Android secilc -N development recipe is retained; this is not a
Treble neverallow/CTS/release-policy claim.

## Rebuild on the host

```sh
R=/home/user/A9-build/mainline/enforcing-round6-20261020
python3 -I scripts/mainline/android/sepolicy/build-enforcing.py \
  --vendor /home/user/A9-build/mainline/cam-aod-surf-20261020/vendor.img \
  --boot /home/user/A9-build/mainline/integrated-20261020m/boot.img \
  --min-free-gib 8 --out "$R"
sha256sum "$R/boot.img" "$R/vendor.img"
```

Use a fresh output directory for a reproduction (policy/labels are intentionally
not silently overwritten). Default disk reserve remains15GiB; the explicit R6
option allows the user's8GiB floor. The builder uses sparse copies, labels every
vendor inode with the real libselinux matcher, reads back payloads/labels and
runs e2fsck. No kernel rebuild or phone command occurs on the host.

## Coordinator phone commands

Worker executes none of these. Use the existing exclusive slot-B owner and its
normal recovery/dead-man path with this matching pair; no system change. If
using the existing direct-b.sh wrapper, its positional interface is:

```sh
R=/home/user/A9-build/mainline/enforcing-round6-20261020
/home/user/A9-build/mainline/vendor-test-20261020/direct-b.sh \
  "$R/boot.img" "$R/vendor.img" "$CODE"
```

`CODE` is the coordinator's existing current lp-write admission, not a new value
provided by this worker. Do not toggle enforcing on the old running vendor.

Initial read-only checks (save full output privately):

```sh
adb shell getenforce
adb shell cat /sys/fs/selinux/enforce
adb shell getprop sys.boot_completed
adb shell getprop ro.hardware.egl
adb shell getprop ro.opengles.version
adb shell ps -AZ
adb shell pidof surfaceflinger system_server
adb shell getprop init.svc.vendor.graphics.allocator
adb shell getprop init.svc.vendor.camera-provider-a9
adb shell getprop init.svc.android-hardware-media-c2-v4l2-hal-1-2
adb shell getprop init.svc.vendor.a9ml_logd
```

Manual tools must be tested in their named enforcing domains, rather than
getting a false pass from the permissive console. In the existing root channel:

```sh
printf '%s\n' 'runcon u:r:a9ml_camcap:s0 /vendor/bin/a9-camcap -l' |
  adb shell 'timeout 30 nc 127.0.0.1 2324'
printf '%s\n' 'runcon u:r:a9ml_audio:s0 /system/bin/sh -c "id -Z; exec /vendor/bin/a9ml-audio.sh inventory"' |
  adb shell 'timeout 30 nc 127.0.0.1 2324'
printf '%s\n' 'runcon u:r:a9ml_key_aod:s0 /system/bin/sh -c "id -Z; exec /vendor/bin/a9ml-key-aod.sh state"' |
  adb shell 'timeout 30 nc 127.0.0.1 2324'
```

For the existing AOD setup test, run the same key-AOD command with `setup` while
gates are closed, then normal E Ink opening and `sleep`/`wake` in that domain.
Setup restarts the app and changes its prefs by design. Do not call `sh script`
from the permissive console and count that as a functional-domain test.

Use the unchanged, previously accepted probes:

```sh
adb install -r /home/user/A9-build/mainline/codec2-surface-20261020/probe/a9-codec2-playback.apk
adb shell am force-stop org.a9.videoprobe
adb shell am start -W -n org.a9.videoprobe/.PlaybackActivity --ez surface true
# Wait about10s.
adb exec-out run-as org.a9.videoprobe cat files/result.json
adb shell pidof surfaceflinger
adb shell am force-stop org.a9.videoprobe
adb shell am start -W -n org.a9.videoprobe/.PlaybackActivity --ez surface false
# Wait about10s; collect the fresh result again.
adb exec-out run-as org.a9.videoprobe cat files/result.json

adb install -r /home/user/A9-build/mainline/camera-fps-20261020/probe/a9-camera2-probe.apk
adb shell pm grant org.a9.cameraprobe android.permission.CAMERA
adb shell am force-stop org.a9.cameraprobe
adb shell am start -W -n org.a9.cameraprobe/.CaptureActivity --es facing back --ei min_fps 15 --ei max_fps 30
# Wait for still/result; keep the activity foreground.
adb exec-out run-as org.a9.cameraprobe cat files/result.json
```

Repeat camera with facing front if enabled in the tested base. Preserve JPEG
locally; do not publish private pictures. Use the existing owned Wi-Fi/session
connection procedure and HTTP test; this policy changes no credentials, store
handling or network startup. Then check BT and sensors:

```sh
adb shell cmd wifi status
adb shell cmd bluetooth_manager enable
adb shell timeout 40 cmd bluetooth_manager wait-for-state:STATE_ON
adb shell dumpsys sensorservice
adb shell dumpsys android.hardware.gnss.IGnss/default
```

For GNSS NTP/time, use the already installed inject probe in the existing
working Wi-Fi/MPSS session (or install the current gnss-inject APK first):

```sh
adb shell pm grant org.a9.gnssprobe android.permission.ACCESS_FINE_LOCATION
adb shell am start -n org.a9.gnssprobe/.Start --ei seconds 120 --ez inject_time true
# After~30s, collect non-location counters; let the probe finish before another session.
adb shell dumpsys android.hardware.gnss.IGnss/default
```

Capture AVCs before USB changes or teardown, without clearing/rate-limit changes:

```sh
printf '%s\n' 'dmesg | grep -E "avc:|audit_lost|rate limit|callbacks suppressed"' |
  adb shell 'timeout 30 nc 127.0.0.1 2324' > dmesg-avc-r6-private.txt
adb logcat -b all -d > logcat-r6-private.txt
cat dmesg-avc-r6-private.txt logcat-r6-private.txt |
  python3 -I scripts/mainline/android/sepolicy/sanitize-avc.py > vectors-r6.json
```

## Required enforcing pass list

| Check | Required result |
|---|---|
| Mode/policy | Enforcing and enforce=1; matching GSI sidecars; only diagnostic rootsh exempt, no functional permissive SID |
| Boot/graphics/logd | boot_completed1; stable system_server/SF; Launcher/E Ink redraw works; allocator/camera/Codec2/logd running in expected domains; logcat usable |
| GLES/AOD | Mesa selection reports196610; named-domain AOD prefs setup succeeds while closed, visible clock/minute update and wake/redraw work; no gate/power-policy relaxation |
| Wi-Fi | a9_wifi_radio session/MPSS serving, HAL/supplicant loaded through private paths, framework connection and existing HTTP204 test pass |
| BT/sensors/basic hardware | BT STATE_ON, five sensors and samples; existing vibration/battery checks retain behavior |
| Camera | named-domain raw enumeration; provider/isolated IPA healthy; Camera2 JPEG pass with requested15–30fps and truthful frame duration/AE metadata |
| Codec2 | Surface60/60 queued, render callbacks/EOS, same SF PID; ByteBuffer60/60 decoded/drawn |
| Audio | named-domain inventory/controls/PCM capability probe, native HAL registration; microphone test if part of current session. Actual sound only with user present, retaining existing manual routes |
| GNSS | HAL/helper alive in hal_gnss_default; UTC command pipe works; with NTP available, queued/ack/result counters advance with result/error0. No-fix is not a fabricated RF pass |
| AVC review | No unhandled enforcing denial on a required feature path; record missing/lost audit evidence. Zero captured AVCs alone is not a pass |

A failure or missing evidence stays a failure/pending row. Report the first
failed service and sanitized vectors; do not relax a functional domain to
permissive merely to complete the list.
