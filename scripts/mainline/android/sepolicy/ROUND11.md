# Enforcing R11 — private GNSS memfd labels and diagnostic access

R10 phone passes enforcing, Wi-Fi serving/connected/ping/MPSS running/factory
MAC, AOD overlay/doze1/1, both Codec2 and Camera2 paths, named microphone, BT,
sensors, vibration and audit rate0/backlog8192. R11 changes policy only. Boot,
all other regular-file contents, symlink targets and vendor inode labels are
compared with R10. No GNSS binary, probe, kernel, firmware or passing helper is
changed. Only diagnostic rootsh remains permissive.

## What failed

The three hex-encoded GNSS tmpfs AVC paths decode to **/memfd:gnss-events
(deleted)** twice and **/memfd:gnss-status (deleted)** once. These are anonymous
HAL-created files, not persist data or named files on a mounted tmpfs.
`gnss-hal/service.cpp:Service::start()` creates them root0600 and reopens their
/proc/self/fd links for independent read/write offsets. Failure returns
EX_ILLEGAL_STATE **before spawning the LOC child**. Thus this failure can produce
zero SV/fix callbacks regardless of reception; indoor reception remains a
separate, unqualified question after the session actually starts.

A creator transition labels new GNSS tmpfs file inodes
**a9ml_gnss_memfd_file**. Only hal_gnss_default gets create/read/write/open/
getattr/setattr/append, covering its same-SID child and inherited descriptors.
No executable or mmap access is granted. The earlier generic tmpfs/write rule
is removed; no generic tmpfs/open is added. Anonymous memfds have no persistent
pathname, so a vendor file_contexts regex would not label them.

The shell Binder denial is `dumpsys android.hardware.gnss.IGnss/default`, not
the Android app probe's LocationManager request. Add shell->HAL call/transfer
and the reverse transfer plus HAL use/write/getattr of the shell output pipe.
The HAL still requires calling UID1000 for data-plane dispatch; shell cannot
start/change a location session through that API. Its dump emits counters/state
only. No direct untrusted_app->HAL call or private memfd read permission is added.

The minor observations receive narrow rules: HAL read/map of its public
persist_vendor_debug_wifi_prop property area (no setter); supplicant directory
**search only** on wifi_data_file (no enumeration, mutation or file access);
camcap **getattr only** on the rmtfs device. Camcap's entity resolver stats every
/dev entry to match major/minor, causing the repeated denial. Keep the real MPSS
label and prohibit open/read/write/ioctl/map; do not hide it with dontaudit.

## Host build

```sh
R=/home/user/A9-build/mainline/enforcing-round11-20261020
python3 -I scripts/mainline/android/sepolicy/build-enforcing.py \
  --vendor /home/user/A9-build/mainline/camera-preview-20261020/vendor.img \
  --boot /home/user/A9-build/mainline/integrated-20261020m/boot.img \
  --min-free-gib 8 --qualification-audit --out "$R"
(cd "$R" && sha256sum -c SHA256SUMS)
```

Fresh sparse output, no kernel build. Compiled policy, full inode label/overlay
readback and fsck pass. Source queries check transition, positive permissions
and absence of generic tmpfs open/write, shell/app private-buffer reads, memory
execute/map, Wi-Fi property setters, supplicant directory mutation and camera
rmtfs I/O. This is not a live-kernel policy decision test. Actual new memfd labels
must be observed on the coordinator's next boot, during an active GNSS request.

## Coordinator commands — worker executes none

Use the existing exclusive slot-B owner/current CODE, matching GSI and original
dead-man/restore procedure. The boot is exactly R10; vendor-only replacement is
sufficient when that same boot is already selected. For the normal pair wrapper:

```sh
R=/home/user/A9-build/mainline/enforcing-round11-20261020
/home/user/A9-build/mainline/vendor-test-20261020/direct-b.sh \
  "$R/boot.img" "$R/vendor.img" "$CODE"
adb shell getenforce
adb shell cat /sys/fs/selinux/enforce
adb shell getprop ro.boot.a9audit
adb shell getprop sys.boot_completed
```

The wrapper already performs its bounded90s GNSS request after Wi-Fi. Preserve
that one attempt and its output; do not automatically repeat it. To observe the
memfd labels/dump **during that existing request**, schedule this at its active
GNSS stage before the request finishes (only target names, never file contents):

```sh
printf '%s\n' 'p=$(pidof android.hardware.gnss-service.a9); if [ -n "$p" ]; then for f in /proc/$p/fd/*; do case "$(readlink "$f")" in /memfd:gnss-events*|/memfd:gnss-status*) ls -lLZ "$f";; esac; done; fi' |
  adb shell 'timeout 30 nc 127.0.0.1 2324' > gnss-memfd-labels-r11-private.txt
adb shell dumpsys android.hardware.gnss.IGnss/default > gnss-dump-active-r11.txt
```

Require a9ml_gnss_memfd_file on both event/status file objects; root0600 and no
private bytes exposed. `running=1` during a request demonstrates startup passed.
client_stage updates when the worker finishes; its initial0 while active is
normal. The wrapper already collects summary and final HAL dump after100s.
If the coordinator instead schedules a standalone attempt in its owned session,
use this **instead of**, not in addition to, the wrapper's attempt:

```sh
adb install -r /home/user/A9-build/mainline/gnss-integrated-20261020/out/a9-gnss-count-probe.apk
adb shell 'pm grant org.a9.gnssprobe android.permission.ACCESS_COARSE_LOCATION; pm grant org.a9.gnssprobe android.permission.ACCESS_FINE_LOCATION; cmd location set-location-enabled true --user 0; am start -n org.a9.gnssprobe/.Start --ei seconds 90'
# Capture active labels/dump above, then await that bounded probe's completion.
adb shell 'run-as org.a9.gnssprobe cat files/summary; dumpsys android.hardware.gnss.IGnss/default' > gnss-result-r11.txt
```

Do not infer RF success from a running Binder service. Preserve client_exit,
client_stage, qmi_error, stop_ack, engine/session states, UTC injection counters
and SV report/CN0 counters. On completion require the LOC worker to reach its
start-success stage6 and orderly stop acknowledgement; any different stage/error
is the next concrete failure to investigate. Satellite/fix availability indoors
is not guaranteed. Use actual reports/CN0/fixes to assess reception separately.
Do not copy coordinates, SV details, MAC/SSID/IP or raw prefs into public reports.

```sh
adb logcat -b all -d > logcat-r11-private.txt
printf '%s\n' 'dmesg | grep -E "a9ml-audit:|a9wifi-session|avc:|audit_lost|audit_rate_limit|audit_backlog|callbacks suppressed"' |
  adb shell 'timeout 30 nc 127.0.0.1 2324' > dmesg-avc-r11-private.txt
cat dmesg-avc-r11-private.txt logcat-r11-private.txt |
  python3 -I scripts/mainline/android/sepolicy/sanitize-avc.py > vectors-r11.json
```

Keep audit loss delta and final rate0/backlog8192. No same-boot Wi-Fi retries or
admission/closed-AOD-gate bypasses. Retain R10's complete pass list and commands
in [ROUND10](ROUND10.md): working Wi-Fi/factory identity, AOD clock/wake, both
Codec2 paths/sameSFpid, both Camera2 paths, mic, BT, sensors, vibration, GLES,
E Ink, four native services and stable SF/system_server/logd. Phone behavior is
pending; host checks do not constitute GNSS or enforcing runtime qualification.
