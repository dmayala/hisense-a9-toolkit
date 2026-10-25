# GNSS UTC injection POC (2026-10-20)

The HAL now accepts Android's `IGnss.injectTime` and sends LOC v02
`INJECT_UTC_TIME` (`0x0038`): TLV `0x01` u64 UTC milliseconds, `0x02` u32
uncertainty milliseconds. `REG_EVENTS` changes from `0x187` to `0x197` to
subscribe to `INJECT_TIME_REQUEST` (`0x0028`, event bit `0x10`). The HAL requests
framework time at each start and on modem requests (callbacks limited to once
per ten seconds). `gnssRequestTimeCb` is callback transaction **9**, with no
arguments. Capabilities are SCHEDULING | ON_DEMAND_TIME (`0x11`); Android's
historical ON_DEMAND_TIME name means periodic time injection.

The framework supplies network time; the HAL never contacts an NTP server or
uses the modem's optional server hints. An NTP sample arriving before `start`
is cached in process RAM. A private anonymous pipe carries samples to the
confined child. Immediately before QMI encoding, the child advances the sample
by `CLOCK_BOOTTIME now - timeReferenceMs`, including suspend and queue delay.
It rejects future/negative references, samples older than 24 hours, UTC outside
2020–2100 and uncertainty outside 0–24 hours. Accepted uncertainty gains a
50 ppm drift allowance for the sample's age. Injection is serialized with
other QMI requests, at most once per ten seconds and 128 times per session.
Pipe saturation returns an AIDL error; a stale sample is dropped. A dead child
returns EPIPE rather than terminating the Binder service with SIGPIPE.

The QMI response ACK and asynchronous indication status are separate. A modem
rejection is counted and the standalone session continues; malformed frames or
transport errors retain the bounded STOP path. The UID/group/capability drop,
seccomp filter, unique LOC endpoint lookup, RAM modem-store ownership and
standalone operation mode remain in place. No kernel, DT, init or SELinux
changes are needed for this POC. Enforcing behavior still needs a phone check.

`injectLocation` and `injectBestLocation` remain unsupported. We deliberately
omit the position-request event (`0x40`) and `0x39` injection for this first time
experiment. No SUPL, PSDS/XTRA, engine unlock, assistance deletion or NV writes
are added. This is not full GNSS HAL conformance.

## Satellite and engine diagnostics

There is no captured A9 modem frame establishing a reporting bug. The existing
stock-derived legacy 28-byte / expanded 29-byte SV decoder is retained. The
mapping still uses validity bits system=1, ID=2, C/N0=128; positive finite C/N0
below 70 dB-Hz is reported, otherwise zero. Missing C/N0 does **not** remove an
SV; only absent system/ID, unknown constellation, zero ID or malformed numeric
fields can filter it. Baseband C/N0 is zero and carrier-frequency flags are
unset, rather than estimated.

The old adapter never set USED_IN_FIX. It now maps the latest successful
position report's expanded used-SV list (`0x36`), falling back to legacy
`0x2c`, to Android constellation/SVID and retains it for at most five seconds.
Tracking, almanac and ephemeris flags alone never imply use in a fix. A
non-success position report clears the list. This change cannot create a fix;
it makes the reporting faithful when the modem actually supplies one.

The HAL dump adds:

- `modem_time_requests`, `framework_time_callbacks`, `time_queued`, `time_ack`,
  `time_rejected`, `time_results`, `last_time_error`, `last_time_result`.
- Actual modem `engine_state` (0 unknown, 1 on, 2 off) and `session_state`
  (0 unknown, 1 started, 2 finished). These are last observations; worker exit
  does not prove physical engine-off. No fabricated ENGINE_ON at child spawn.
- `sv_raw_max` before mapping, `sv_max` after mapping, `sv_with_cn0_max`,
  `cn0_max`, `sv_used_max`. Counts/maxima persist until service restart; compare
  before/after dumps when running more than one trial.

The diagnostic APK's app-private `events` file is bounded to 256 KiB. It
records elapsed time, provider session callbacks, first-fix TTFF, and per-SV
constellation/SVID/C/N0/used/ephemeris/almanac for the first five reports and each
30th report. It never serializes locations, coordinates, angles or NMEA and
never emits those records to logcat. Provider session callbacks are not direct
modem power observations; collect the HAL dump alongside them for actual modem
engine state and time-request counters. The CLI logs actual engine/time events
and, with `--sv-diagnostics`, per-SV validity/C/N0 under a separate 64 KiB budget.

## Build and integrated payload

From the checkout containing this change:

```sh
python3 scripts/mainline/android/gnss-hal/build.py --out /home/user/A9-build/mainline/gnss-inject-20261020/out
python3 scripts/mainline/android/gnss-hal/probe/build.py --out /home/user/A9-build/mainline/gnss-inject-20261020
python3 scripts/mainline/android/gnss-hal/tests/run.py --out /home/user/A9-build/mainline/gnss-inject-20261020
```

The existing `build-integrated.sh` enables GNSS by default. Its vendor refresh
builds these sources and replaces `/vendor/bin/hw/android.hardware.gnss-service.a9`
and `/vendor/bin/a9-gnss-loc`. The diagnostic APK is a separate test artifact;
it is not automatically installed in the vendor. The GNSS patch requires no
new boot/kernel and does not change feature selection in the integrated builder.
The worker produced native binaries and APK only, not a new boot/vendor pair.

## Coordinator commands (worker must not execute)

Use the coordinator's existing integrated boot and MPSS RAM-store session.
Keep network access available for framework NTP and place the phone by a window.
Install the new `out/a9-gnss-count-probe.apk` via the coordinator's normal path;
if its test signing key differs from the installed old probe, uninstall the old
`org.a9.gnssprobe` first. Then run these in the coordinator's phone shell:

```sh
pm grant org.a9.gnssprobe android.permission.ACCESS_COARSE_LOCATION
pm grant org.a9.gnssprobe android.permission.ACCESS_FINE_LOCATION
cmd location is-location-enabled --user 0
# If needed, enable location and restore its previous setting after the trial.
cmd location set-location-enabled true --user 0
dumpsys android.hardware.gnss.IGnss/default
am start -n org.a9.gnssprobe/.Start --ei seconds 900 --ez inject_time true
```

Collect a HAL dump after about 30 seconds, and after the 900-second probe:

```sh
dumpsys android.hardware.gnss.IGnss/default
run-as org.a9.gnssprobe cat files/summary
run-as org.a9.gnssprobe cat files/events
```

The explicit probe option sends the framework `force_time_injection` extra
command. Its accepted boolean means command delivery, **not** modem injection.
Look for `time_queued>0`, `time_ack>0`, `time_results>0`, `last_time_error=0`,
`last_time_result=0` to demonstrate the complete path. `modem_time_requests`
may remain zero if the modem never asks; the initial HAL callback and explicit
probe still request NTP. `time_ack>0` alone is only a QMI request ACK.
If no sample arrives, inspect the framework's network-time availability;
no wall-clock fallback is substituted into the framework path.

`sv_raw_max > sv_max` identifies mapping/filtering; `sv_raw_max=sv_max=1`
means the decoder exported only one SV, not merely one SV above a signal
threshold. Use the per-SV C/N0 records to assess reception. `fixes>0` in the
probe demonstrates a framework location callback; no-fix remains no-fix.
After the probe, expect `running=0 stop_ack=1`; existing cancellation can yield
`client_exit=1`. Keep the coordinator's normal MPSS/session cleanup owner.

For a separate direct CLI comparison, stop the app and ensure the HAL's
`running=0` first. In the existing root phone session, run:

```sh
/vendor/bin/a9-gnss-loc --discover --seconds 900 --inject-time --sv-diagnostics
```

This explicit CLI option uses the **device CLOCK_REALTIME**, initially and on
modem time requests, with a nominal 1000 ms uncertainty. Only use it when that
clock is known to be correct. It is not an NTP client; the framework probe is
the preferred NTP test. No fix descriptor is supplied, so the CLI logs no
coordinates. Do not run competing HAL/CLI sessions. No `--cold` or unlock step.

## Sources and evidence limits

- [A6L GNSS note](https://github.com/PaddleStroke/hisense-a6l-lineageos/blob/main/docs/gnss-20260924.md),
  read using `gh api` per the brief. Its status explicitly says real modem
  untested; the reported fixes are synthetic replay, not a measured A6L fix.
- [libqmi LOC schema](https://github.com/linux-mobile-broadband/libqmi/blob/main/data/qmi-service-loc.json),
  fetched using `gh api`: `Inject Time Request` (0x28), `Inject UTC Time`
  request/indication (0x38) and legacy `Satellites Used` (0x2c).
- [Android callback AIDL](https://github.com/LineageOS/android_hardware_interfaces/blob/lineage-23.0/gnss/aidl/android/hardware/gnss/IGnssCallback.aidl):
  time callback order/signature, capabilities and SV flags. The local cached
  V7 NDK serializers are the parcel compatibility oracle in the tests.
- [Android framework provider](https://github.com/LineageOS/android_frameworks_base/blob/lineage-23.0/services/core/java/com/android/server/location/gnss/GnssLocationProvider.java):
  `force_time_injection` and `onRequestUtcTime` both demand time injection;
  `injectTime` passes epoch/reference/uncertainty to the native HAL.

Host checks exercise real Android parcels under QEMU, production dispatch with
no Binder driver, SV/used mapping, production time pipe parsing, four synthetic
LOC sessions (success, rejection, send failure, malformed indication) including
STOP, and the exhaustive seven-ID gate. They do not establish A9 RF reception,
framework NTP availability, modem acceptance or a real first fix.
