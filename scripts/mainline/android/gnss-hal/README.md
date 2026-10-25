# A9 integrated GNSS

Host-built Android 16 stable AIDL V7 `IGnss/default` over the proven G1b standalone
QMI LOC client. Hardware qualification is pending; this change is prepared for a
short test inside the coordinator's existing integrated E Ink/Wi-Fi session.

Build the vendor from the checkout containing the desired integrated fixes:

```sh
bash scripts/mainline/android/build-vendor.sh --refresh-integrated --gnss \
  --out /home/user/A9-build/mainline/gnss-integrated-20261020/image
python3 scripts/mainline/android/gnss-hal/probe/build.py
```

The vendor refresh adds the service, static LOC helper, init service, VINTF
fragment, GPS feature declaration and file labels. It retains the R4 module and
firmware composition and applies main's installed Wi-Fi and GPU-opener fixes.
The original kernel/DT is sufficient. The image includes existing private modem
seeds; keep it local. Build logs, hashes and the probe APK remain under the build
root. `build.py --out DIR` builds just the two native binaries.

Android starts/stops the LOC child through `start()`/`stop()`; registration alone
does not open LOC. Each start discovers one LOC v2/instance 0 endpoint through
QRTR. MPSS must already be running through the integrated Wi-Fi session. Nothing
here starts remoteproc, mounts modem stores, supplies credentials or writes NV.
The QMI request allowlist now has seven IDs: register-master (0xba),
register-events (0x21), standalone mode (0x4a), start (0x22), stop (0x23), the
read-only engine-lock query (0x3b), and UTC time injection (0x38). Engine unlock
(0x3a), assistance deletion, position and XTRA injection remain refused.
See [UTC injection and diagnostics](time-injection.md) for the framework NTP
path, explicit CLI/probe options, and a 900-second coordinator test. All extension getters report
`EX_UNSUPPORTED_OPERATION`. Android 16 JNI constructs extension wrappers on a
successful getter status, including for null interfaces; those wrappers assert
or dereference null during initialization. Returning an exception leaves the
unimplemented extension unavailable to the framework.
This is a standalone POC: the AIDL-mandated configuration, measurement and
power-indication extensions remain unimplemented, so full HAL conformance is
not claimed. The [Android 16 JNI getters](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android16-release/services/core/jni/gnss/Gnss.cpp)
handle failed extension transactions; the [AGnssRil wrapper](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android16-release/services/core/jni/gnss/AGnssRil.cpp)
asserts on successful null replies.

The parent uses private anonymous RAM file descriptors for fixes, SV records and
client status. The child drops groups, capabilities and UID/GID and installs the
existing G1 seccomp filter. Coordinates are never logged or persisted. The diagnostic APK can save bounded
app-private per-SV C/N0 records; the CLI does so only with `--sv-diagnostics`.
The service reports GNSS locations and satellite callbacks; baseband C/N0
and carrier frequency remain unset because this adapter does not have those
measurements. Used-in-fix now comes from the latest successful position report
used-SV list, with a five-second freshness limit. QMI constellation/PRN mapping follows the A6L reference
at `~/A9-build/ref/hisense-a6l-lineageos/device/hisense/a6l/gnss/lib/`.
The G1b base was derived from `d8d6cd1`. The UTC change extends its session
request gate, event mask, time encoder and runtime event export; it does not
change its stock-derived SV wire decoder.

The direct Binder parcel adapter comes from the earlier A9 GNSS draft; its V7
location, mode and satellite layout are checked against the cached platform GNSS
NDK library. Satellite fields also match the [AOSP callback declaration](https://android.googlesource.com/platform/hardware/interfaces/+/refs/heads/main/gnss/aidl/android/hardware/gnss/IGnssCallback.aidl).
There is no runtime dependency on that GNSS library or the A6L vendor binaries.

Coordinator phone test (worker must not run these): use the normal integrated
boot/session path with this vendor and the usual MPSS RAM-store owner. Keep the
existing Wi-Fi connection available. Then, in the coordinator's phone shell:

```sh
service check android.hardware.gnss.IGnss/default
pm list features | grep android.hardware.location.gps
cmd location is-location-enabled --user 0
```

Install the local `out/a9-gnss-count-probe.apk`, then run:

```sh
pm grant org.a9.gnssprobe android.permission.ACCESS_COARSE_LOCATION
pm grant org.a9.gnssprobe android.permission.ACCESS_FINE_LOCATION
cmd location set-location-enabled true --user 0
am start -n org.a9.gnssprobe/.Start --ei seconds 90
```

After 90 seconds, collect these counts only:

```sh
run-as org.a9.gnssprobe cat files/summary
dumpsys android.hardware.gnss.IGnss/default
```

`sv_reports>0` and `sv_max>0` in the app summary prove satellite callbacks reached
LocationManager. `fixes>0` proves a location callback. A no-fix result indoors is
not a successful fix. The HAL should end with `running=0 stop_ack=1`; client exit 1
on cancellation is expected from G1's existing error-to-STOP path. To end early:
`am stopservice -n org.a9.gnssprobe/.Probe`, then check the HAL counters. Keep any
full `dumpsys location` private; it may include locations. Restore the previous
location setting if changed, remove the probe if desired, then let the existing
session owner stop MPSS and perform its usual cleanup.

HAL `client_stage` categories: 0 unknown/not finished; 1 socket failure; 2 LOC
lookup failure; 3 confinement failure; 4 configured child before START success;
5 START rejected; 6 START acknowledged. `qmi_error` is the last nonzero QMI response
error, without raw frames. Counts persist across starts until the service exits.

Limits: UTC assistance only; no position/XTRA/SUPL, NMEA, measurement extensions, altitude/speed/bearing or
SELinux-enforcing qualification. Existing G1 limits remain: a session stops after
35 minutes and forwards at most 400 fixes. Stop/start works again without a
reboot, but this is a short-test POC rather than a daily-driver GNSS provider.
The coordinator still needs to prove service registration, reception and Wi-Fi
coexistence on hardware. No phone access was used to produce this candidate.

Focused host checks (five groups; no phone or real QRTR socket):

```sh
python3 scripts/mainline/android/gnss-hal/tests/run.py
```

They cover platform parcel compatibility, production Binder dispatch without a
callback/driver, SV mapping, synthetic transport/discovery/cancellation/SV export,
the time pipe/session paths, and the seven-ID request gate. The APK build separately verifies its signature.
