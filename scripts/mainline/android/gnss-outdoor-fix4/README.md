# Outdoor FIX6

Host-only preparation for the existing combined XTRA + SoC stats launcher:

```sh
/home/user/A9-build/mainline/gnss-outdoor-fix3-20261023/run.sh outing
```

Use `rehearsal` to stay plugged for the shorter run. These commands access the
phone only when the user/coordinator executes them. This worker never did.

The launcher now defaults to `integ-20261023-7/{boot.img,vendor.img}`, overridable
with `A9_INTEG_IMAGE`. The historical FIX6 composition below used integ6.
That boot keeps the
integ-5 kernel, RPM node, ramdisk, modules and recovery DTBO; one DT byte changes
the Bluetooth UART RX wake trigger to falling-edge. Relative to FIX4, the vendor
changes boot-radio init and the outdoor deadman's post-boot allowance. XTRA,
sensor fixes, E Ink and policy remain unchanged. The prior FIX6 images and
launcher are preserved in `integ-20261023-6/before-boot69-addendum/`.

Both incoming vendors already contained all 306 candidate-8 assets and current
stock service templates from `85e6578`/`8f4cfef`. The combined hook omitted the
normal cellular opt-in, so those clients remained disabled. Vendor init now
requests the existing owner at boot and requests stock preparation as soon as
its original boot-completed/serving/Android-ready gates pass. The existing
prepare-ready property still controls client start. The hook only observes the
boot request. Radio preparation, running states and interfaces remain optional:
failures print fixed WARN labels and GPS continues. Normal client stop still
precedes Wi-Fi-owner stop and screen-off. No new modem-store path is added.

FIX4 rehearsal boot-62 falsely failed its running-state check: unprivileged
`getprop init.svc.vendor.qcrild` was denied 43 times. FIX5 uses the same existing
native helper as candidate-8 and checks both instances (`qcrild2` runs the same
binary with `-c 2`). The service name was correct. Radio init/VINTF, prep helper,
shim and accounts match candidate-8 and integ-2; no radio rebase loss was found.
Boot-62 contains two RIL processes, while phone ANRs cease after the first
post-start report (2.7 seconds after the qcrild start request); the later false
failure unnecessarily revoked cellular opt-in. Keeping that normal opt-in
through GPS addresses this observed failure chain. FIX5 rehearsal boot-65 passed
the flow; FIX6 and sustained ANR absence remain untested. Pre-completion ANRs
still need measurement because the original owner gates remain in place.
AIDL lookup messages from the framework
alone do not prove that the disabled custom AIDL daemon ran.

Before the single GPS app start, a detached root logcat reader mirrors only this
session's public A9GNSS enums/numeric fields to kmsg: at most one sample per
30 elapsed seconds and one final. It expires before the sleep phase. Coordinates,
NMEA, raw exceptions and arbitrary fields never enter the mirror. The app/APK,
XTRA framing/download, credential path, quiet 1800-second host interval,
return-A runner are retained. The host hold is now 4200 seconds, with a
20-minute replug allowance after the 30-minute quiet interval (replug by
READY +50 minutes). The vendor deadman's post-boot hold is 5400 seconds;
its 7200-second total-uptime cap and fallback behavior stay unchanged.

Integ-5 already contains CONFIG_QCOM_STATS=y, CONFIG_DEBUG_FS=y, the driver's
late initcall, and the enabled RPM node at 0x04690000. These vlow/vmin records
use MMIO and do not require an SMEM item. This Android's unmounted-debugfs
behavior is documented in `android-boot.sh`; the logger previously read files
without mounting it. FIX6 mounts debugfs in the logger's own namespace first
and emits numeric mounted/driver-bound/file availability diagnostics. Silent
old kernel logs cannot prove the actual mount/probe state; the next boot will
distinguish them directly. Android host fixtures reproduce zero sources before
the mount and two afterward, with explicit unavailability if mounting fails.

Each sleep sample records `/sys/class/wakeup` counters and per-source deltas,
plus `/sys/power/pm_wakeup_irq`. Missing data has explicit availability records.
`wakeup_id` is the kernel wakeup class index. Each record adds a sanitized
`wake_name`; IRQ records add the sanitized action/chip and numeric hardware IRQ.
Address-like identifiers are redacted. Counter resets omit the delta.

Boot-69's six post-start samples all record wake IRQ 116, mapped in earlier
suspend diagnostics to `4a8c000.serial`, TLMM GPIO11. The UART RX line idles high,
and our DT's level-high trigger continually re-wakes the CPU. Qualcomm's stock
[`msm_geni_serial` driver](https://android.googlesource.com/kernel/msm/+/f31097e7182e763ee7debc98b40e9dc09f432785/drivers/tty/serial/msm_geni_serial.c)
requests `IRQF_TRIGGER_FALLING` for this wake IRQ even though its DT describes
level-high. Upstream inherits the DT trigger. FIX6 therefore sets the RX wake
IRQ to falling-edge, retaining wake-on-data; the main UART interrupt is unchanged.
The USB PHY warnings alone do not identify the waker. Hardware confirmation of
the correction is pending.

Boot-61 has 190 MCLK0 and 190 MCLK2 double-disable warnings. The saved call stack
points to our sensor `sns_power_off` runtime callback. Both sensor drivers now
track ownership of a successful supply/clock enable and skip duplicate on/off
callbacks. Stock timing/error unwind is retained. The native modules pass fresh
modpost and the complete 100-module/6202-import integ-5 ABI audit, including CRCs
and vermagic. Clock balance passed both drivers' mocked callbacks; hardware
confirmation and the upstream reason for revisiting the idle callbacks remain
pending. Future FIX11 builds consume the corrected tracked sensor sources.

`extract.py` reads A9GNSS from kernel and logcat records, including rotations and
kernel-only directories/tar archives, and deduplicates mirror copies. Boot-69
contains a real fix (459562 ms TTFF, 34/41 satellites used). Its false host FAIL
came from reading the rotated live ring/logcat instead of the persistent logger.
The hook now reads the current logger's retained kernel files through its private
mount namespace, checking the boot/PID header without exposing metadata. The
post-pull extractor also writes the authoritative `outdoor-result.json`.
Missing capture prints `UNAVAILABLE GNSS_FIX`. Named wakeups/IRQs and
RPM/suspend deltas contain only sanitized labels and numeric fields:

```sh
python3 /home/user/A9-build/mainline/gnss-outdoor-fix3-20261023/extract.py /path/to/boot-directory
```

FIX6 host build, validation and staging:

```sh
# Initial FIX6 assembly (already built): build-fix6.py, stage.py --fix6
python3 scripts/mainline/android/gnss-outdoor-fix4/test-fix6-outing.py
python3 scripts/mainline/android/gnss-outdoor-fix4/build-fix6-outing.py
python3 scripts/mainline/android/gnss-outdoor-fix4/stage.py --fix6-outing
```

Build evidence is under `integ-20261023-6/`; minimum disk reserve defaults to
15 GiB and can be changed only with the user's task-specific authorization.
The addendum builder preserves all boot payloads except the one-byte DT trigger
change, verifies ABL, changes only the deadman default in the previous FIX6
vendor, compares every other regular file, and runs fsck. Original radio init
verification remains applicable because init files are byte-identical. Debugfs tests
use an actual Android shell with a mocked mount; no host mount occurs.

Prior FIX5 host validation and staging (reuses the unchanged FIX4 images):

```sh
python3 scripts/mainline/android/gnss-outdoor-fix4/test-radio.py
python3 scripts/mainline/android/gnss-outdoor-fix4/stage.py --fix5
```

The five fake-hook paths cover shell-denied/native-running success and optional
request, preparation, running-state and interface failures; all reach one GPS
start and its first summary. The finite native state reducer passed actual
Android mksh under QEMU. Owner interruption still propagates.
Evidence: `integ-20261023-5/fix5/{READY.txt,radio-smoke.json,image-comparison.json,failure-analysis.json,runtime.json}`.

Original FIX4 build/validation commands:

```sh
python3 scripts/mainline/android/gnss-outdoor-fix4/build.py
python3 scripts/mainline/android/gnss-outdoor-fix4/smoke.py
python3 scripts/mainline/android/gnss-outdoor-fix4/stage.py
```

The builder uses the existing private FIX11 native compiler recipes and headers,
fresh GCC dependency files and fresh modpost against integ-5 symbol versions.
It does not alter shared source/object caches. No binary/private asset is committed.
The completed candidate cannot be overwritten by the builder.

Host evidence: `integ-20261023-5/fix4/{READY.txt,result.json,smoke.log,runtime.json}`.
Five focused checks cover actual Android mksh/awk mirror filtering, wakeup/IRQ/RPM
sampling, kernel/tar extraction, both sensor callbacks, and a full fake hook.
The saved boot-69 GNSS fix is confirmed. IRQ116/UART attribution uses saved
samples and earlier IRQ mapping. FIX6's hardware behavior, sustained ANR absence
and improved sleep residency remain untested. All addendum work was host-only.
# SoC sleep observation update (2026-10-24)

The [SoC sleep plan](../../../../docs/plans/soc-sleep-20261024.md) records the
boot84 finding and the separate CPU-idle/PSCI change sequence. The logger now
uses `resume-stats.c`: absolute BOOTTIME waits expire across suspend without
waking the phone, then read RPM counters promptly after thaw. A final full
snapshot covers the end of the interval. Short/coalesced transitions and
post-thaw scheduling latency remain explicit limitations.

Build `build-resume-stats.py` offline before staging the hook. Put its static
`a9-resume-stats` binary under the launcher's `out/` directory; the hook pushes
it with the sleep scripts. `test-resume-stats.py` uses five host-only fixture
groups, including actual Android mksh and old boot84 replay. The extractor also
recovers escaped kmsg newlines, which previously hid RPM Count and timestamps.
No cpuidle, PSCI, alarm or image change is part of this logger update.

