# Enforcing R12 — GNSS UTC pipe and E Ink composer initialization

R11 retains all R10 Wi-Fi/AOD/media/audio/BT/sensor/vibration and audit passes.
GNSS remains at zero SV reports. R12 retains that GNSS pipe fix and completes the composer DRM ioctl list below.
All runtime payloads remain unchanged. **Earlier E Ink passes mean gates open
only: glass updates were not observed and must be requalified.**

## Diagnosis

The raw R11 logcat contains a GNSS denial at21:02:46.284, at the probe's startup:
**hal_gnss_default -> hal_gnss_default fifo_file setattr**, pipefs. It is also
present in the supplied vectors-r11.json (count1). The initial report that no
GNSS vector remained was mistaken; sanitization already handles it correctly
and is unchanged. The AVC-only dmesg capture lacks this line, so use both inputs.

`Service::start()` in gnss-hal/service.cpp checks `fchmod(command[0],0600)` after
creating its nonblocking UTC-injection pipe. A failure closes its descriptors
and returns EX_ILLEGAL_STATE **before posix_spawn**. The child independently
requires an anonymous root0600 nonblocking read pipe on FD5. Retain both checks;
allow the HAL to set its own pipe mode. Round6 admitted create/read/write/open/
getattr/ioctl on that same pipe but missed setattr. No generic tmpfs chmod,
memfd broadening, filesystem relabel or service/transport binary change is needed.
R11's private memfd labels/permissions remain intact.

R11 **already compiled with secilc -D**, and R12 retains it. This is an observed
ordinary denial, not evidence of a hidden dontaudit or QMI transport failure.
R11's final dump has running0/client_exit-1/client_stage0/no SV reports; the
permissive direct-m and direct-cut/cut2/cut3/cut-idx logs have child_stage6,
stop_ack1 and88–89 SV reports with13–15 satellites. Those same-desk comparisons
support failure before the worker starts, rather than an indoor RF explanation.
The private G1 transcript intentionally stays in anonymous memory rather than
logcat. Saved logcat core-GNSS coverage and direct scalar-counter comparisons
are recorded in evidence/gnss-comparison.json; do not invent a detailed QMI
comparison from absent transcript lines. The raw AVC and checked source branch
identify a concrete blocker. Runtime success remains pending.

## Glass regression diagnosis and fix

R11 demo observation supersedes prior E Ink pass claims: gates open/no fault,
launcher screenshot/display ON, but the physical glass stays on the boot splash
and completed-update logging remains zero after input. Codec2 render callbacks
and Camera2 JPEGs do not demonstrate scanout. No old report is optical proof.

The policy labels the a9_eink DRM card graphics_device and gives the composer
an ioctl whitelist of **642e,643a,64a0,64af,64bc**. Those cover resource probing,
import and steady-state commits but omit required startup operations. Kernel
security/selinux/avc.c:avc_has_extended_perms checks only the explicit bitmap
once one exists for a source/target/class. Generic ioctl permission does not
bypass the missing commands.

[AOSP DrmDevice](https://android.googlesource.com/platform/external/drm_hwcomposer/+/refs/heads/main/drm/DrmDevice.cpp)
requires SET_CLIENT_CAP before accepting a device, then capability/master,
CRTC/encoder/connector/plane/property discovery and mode blobs. It also creates
an initial dumb modeset buffer. The current list blocks SET_CLIENT_CAP (640d)
right after the admitted GETRESOURCES probe. [ResourceManager](https://android.googlesource.com/platform/external/drm_hwcomposer/+/refs/heads/main/drm/ResourceManager.cpp)
opens the configured vendor.hwc.drm.device and initializes its DRM-device list
once; failed devices are absent from later hotplug handling. A later setenforce0
does not redo this initialization. [Connector initialization](https://android.googlesource.com/platform/external/drm_hwcomposer/+/refs/heads/main/drm/DrmConnector.cpp)
also needs object/property queries and DPMS configuration. [libdrm](https://android.googlesource.com/platform/external/libdrm/+/refs/heads/main/xf86drm.c)
uses AUTH_MAGIC(token0) for drmIsMaster, so it too needs explicit admission.

R12 adds24 named DRM ioctls for this existing composer/device pair: version,
GEM close, caps, master check/set/drop, PRIME export, CRTC/encoder/connector and
property queries, DPMS property setters, dumb create/map/destroy, plane queries,
ADDFB2 and mode blob create/destroy. Existing atomic/import/vblank/RMFB stay.
Numbers are checked against this kernel's include/uapi/drm/drm.h. No whole DRM
range, GPU-private ioctls, device-label/DAC changes, new gate writes, permissive
functional domain or compositor restart is added.

This is a source-proven startup-blocking policy defect and matches the reported
latched behavior. **The first composer failure is not in the supplied R10/R11
ring captures**: they start after display initialization and rate-limited early
boot. Do not claim a captured SET_CLIENT_CAP denial or confirmed glass recovery.
The demo's full early-boot/composer dump was requested as an optional host path;
no additional path is required to demonstrate the policy/startup mismatch.
SurfaceFlinger RenderEngine's vendor_default_prop read denials are separate and
are not broadened or asserted as this regression's cause.

## Build and verification

```sh
R=/home/user/A9-build/mainline/enforcing-round12-20261020
python3 -I scripts/mainline/android/sepolicy/build-enforcing.py \
  --vendor /home/user/A9-build/mainline/camera-preview-20261020/vendor.img \
  --boot /home/user/A9-build/mainline/integrated-20261020m/boot.img \
  --min-free-gib 8 --qualification-audit --out "$R"
cp scripts/mainline/android/sepolicy/glass-proof.sh "$R/glass-proof.sh"
(cd "$R" && sha256sum -c SHA256SUMS)
```

No kernel build. Full vendor file/symlink comparison with R11 must differ only
in vendor CIL and compiled policy; boot and all inode labels remain identical.
CIL compilation, context/overlay readback and fsck pass. Source queries cover the
new own-pipe permission, retained pipe operations, absent generic tmpfs file
open/write or directory setattr, sole diagnostic-rootsh permissive and -D on
both R11/R12 builds. The unchanged sanitizer replays the actual R11 vector.
The composer ioctl check additionally evaluates explicit xperm bitmaps in
the compiler inputs and compares old/new required/forbidden calls. These are
host checks, not live-kernel or GNSS/glass qualification.

## Coordinator commands — worker executes none

Use the existing exclusive slot-B owner/current CODE, matching GSI and original
dead-man/restore procedure. The boot is exactly R11; vendor-only replacement is
sufficient when that same boot is already selected. For the normal pair wrapper:

```sh
R=/home/user/A9-build/mainline/enforcing-round12-20261020
/home/user/A9-build/mainline/vendor-test-20261020/direct-b-aodn.sh \
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
  adb shell 'timeout 30 nc 127.0.0.1 2324' > gnss-memfd-labels-r12-private.txt
adb shell dumpsys android.hardware.gnss.IGnss/default > gnss-dump-active-r12.txt
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
adb shell 'run-as org.a9.gnssprobe cat files/summary; dumpsys android.hardware.gnss.IGnss/default' > gnss-result-r12.txt
```

Do not infer RF success from a running Binder service. Preserve client_exit,
client_stage, qmi_error, stop_ack, engine/session states, UTC injection counters
and SV report/CN0 counters. On completion require the LOC worker to reach its
start-success stage6 and orderly stop acknowledgement; any different stage/error
is the next concrete failure to investigate. Satellite/fix availability indoors
is not guaranteed. Use actual reports/CN0/fixes to assess reception separately.
Do not copy coordinates, SV details, MAC/SSID/IP or raw prefs into public reports.

```sh
adb logcat -b all -d > logcat-r12-private.txt
printf '%s\n' 'dmesg | grep -E "a9ml-audit:|a9wifi-session|avc:|audit_lost|audit_rate_limit|audit_backlog|callbacks suppressed"' |
  adb shell 'timeout 30 nc 127.0.0.1 2324' > dmesg-avc-r12-private.txt
cat dmesg-avc-r12-private.txt logcat-r12-private.txt |
  python3 -I scripts/mainline/android/sepolicy/sanitize-avc.py > vectors-r12.json
```

Keep audit loss delta and final rate0/backlog8192. No same-boot Wi-Fi retries or
admission/closed-AOD-gate bypasses. Retain R11's complete pass list and commands
in [ROUND10](ROUND10.md): working Wi-Fi/factory identity, AOD clock/wake, both
Codec2 paths/sameSFpid, both Camera2 paths, mic, BT, sensors, vibration, GLES,
E Ink, four native services and stable SF/system_server/logd. Phone behavior is
pending; host checks do not constitute GNSS or enforcing runtime qualification.

## Required physical-display qualification

Use named-domain AOD setup at the existing CLOSED point before dev-open, then
open the panel only through the coordinator's existing admitted procedure.
Before calling E Ink a pass, require all of these under Enforcing:

1. SurfaceFlinger/composer dump identifies the physical824x1648 E Ink display,
   rather than a headless fallback; vendor.hwc.drm.device matches the E Ink card.
2. While awake on HOME, enable only the log_updates diagnostic, record a baseline,
   issue one swipe/HOME, and require **a positive completed-update log delta** plus
   a positive driver updates-counter delta, no new abort/fault/commit failure.
3. The user confirms that the actual glass changes. A screenshot, render callback,
   gates-open state or driver log alone is not optical proof. Then verify AOD
   clock/minute progress and wake/redraw, with unchanged SF/system_server PIDs.

The ready host-only artifact `glass-proof.sh` is a manual phone-side snippet;
the worker never runs it. It checks Enforcing/open gates, changes only logging,
restores the prior logging value on exit, performs one bounded input sequence
and prints counts/status. Run from the existing owned root console after opening:

```sh
R=/home/user/A9-build/mainline/enforcing-round12-20261020
adb shell 'timeout 30 nc 127.0.0.1 2324' < "$R/glass-proof.sh" > glass-proof-r12.txt
adb shell dumpsys SurfaceFlinger > sf-r12-private.txt
adb shell dumpsys android.hardware.graphics.composer3.IComposer/default > composer-r12-private.txt
```

For a failure, retain **full early-boot** dmesg from the owned root console and
logcat including drmhwc before Android/app property noise rotates it away. Do
not restart composer/SF or use setenforce0 as a qualification workaround. No glass
or combined GNSS/E Ink pass is claimed until the coordinator records these proofs.
