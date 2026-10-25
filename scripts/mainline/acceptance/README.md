# Current-image acceptance sweep

Optional row10 standby measurement: [DRAIN.md](DRAIN.md), enabled only by
`A9_SWEEP_DRAIN=1` with a sufficiently long owner hold. The current sampled A9
battery counter is explicitly unsupported across suspend; the stage refuses
charging changes until its counter/control prerequisites are available.

The optional `29_pin_lifecycle` check changes the primary user's credential.
It defaults to `NOT_RUN`. Only the existing phone owner may opt in by setting
`A9_SWEEP_PIN_LIFECYCLE=fresh-disposable-formatted-b` for a slot-B acceptance run
with newly formatted, disposable shared `/data`. **Slot B shares `/data` with A**:
the flag attests the format/disposable state; the hook cannot infer it from the
slot. Never use it with daily-driver data. The check requires no current
credential, the required `locksettings` commands, the rebuilt acceptance APK,
and enough remaining test time. FBE diagnostics, restart health and cleanup
verdicts do not veto this independent test on a live slot-B boot.

The hook generates an ephemeral six-digit test PIN in memory, sets it through
`locksettings`, explicitly verifies it, observes a secure locked keyguard after
sleep, enters the PIN in the standard lockscreen UI, and observes authenticated
unlock. A `finally` block clears the test PIN and checks `isDeviceSecure=false`,
including after failed verification or interruption. The PIN is sent on stdin;
credential command output is suppressed on the device. Only numeric stage
results are saved. A failed clear, failed unsecured readback, or uncertain
setter completion fails the final cleanup verdict. PIN runs after ordinary
feature measurements and optional hardware-codec qualification, so this failure
cannot skip Wi-Fi, Bluetooth, camera or encoder rows. USB/MTP re-enumeration and
warm restart run afterwards: their transport outage cannot skip PIN or encoding.
There is no credential bypass or retry of a credential mutation.

When opted in, the scheduler reserves up to240 seconds for the final PIN
window, without extending the existing owner deadline. PIN clear and unsecured
readback stay in pin_lifecycle's finally block; the final cleanup row reflects
their result.
An owner interruption runs cleanup and never starts a new PIN test. Ordinary
row exceptions produce numeric failure evidence and leave independent rows
running. Image/asset/mute and network prerequisites still apply to their rows;
diagnostic failures are recorded without blocking PIN or hardware encoding.

Host regression for diagnostic loss and USB ordering:
`python3 -I scripts/mainline/acceptance/test-real-tests.py`.

The PIN UI swipe targets the A9's 824×1648 portrait display. A different UI,
delayed lock policy, unsupported shell command, or missing keyguard probe fails
the check and triggers its cleanup; it does not count as a successful unlock.
This tests credential lifecycle on the running boot. It does not prove CE data
survives a cold boot or that a Gatekeeper/Keymaster HMAC implementation is sound.
Rebuild acceptance assets after the `Probe.java` change. Host rehearsal only:
`python3 -B scripts/mainline/acceptance/test-pin.py` (five groups; fake transport
and framework commands, cached Android ARM64 mksh under QEMU; no phone access).

[SWEEP4 transport/storage diagnosis and changes](SWEEP4.md) covers the6r2 MTP
reset, the guarded host MTP probe, transport admission and storage failure cause.

First-boot/reboot follow-up: [integ6r evidence and repair](../reboot/README.md).
It supersedes the sweep5-only scope of the historical diagnosis below. Row01
now requires service/data health after reboot; confirmed RAM `/data` makes row29
NOT_RUN with not_applicable1, while a declared real-data candidate still fails
row01 if its mount fails. Mute remains the first app operation.

`onb.py` is a host hook for the existing b-try owner. It performs one sweep on
slot B, closes its E Ink admission, forgets its test Wi-Fi network, disables
Bluetooth/Wi-Fi, then requests exactly **one normal Android warm reboot**.
It observes the remaining hold after B returns. b-try owns flashing, the kernel
deadline, USB lock, final collection and return to A. The hook never invokes
fastboot, EDL, recovery, an external camera or a separate owner.

**Implementation/build/fake tests are complete; no device qualification was
performed to deliver this hook.** Input audit: `/home/user/A9/runs/parity-audit-20261023.md`.

## Host preparation and exact run

The assets are already built at
`/home/user/A9-build/mainline/acceptance-sweep-20261023/`.
Rebuild them from main after changing any probe source:

```sh
python3 -B /home/user/A9/scripts/mainline/acceptance/build.py
python3 -B /home/user/A9/scripts/mainline/acceptance/selftest.py
```

The following is the **future owning coordinator's run command**, not a command
executed during the host-only delivery:

```sh
image=/home/user/A9-build/mainline/integ-20261023-7
A9_SWEEP_IMAGE="$image" \
A9_SWEEP_ASSETS="$image/acceptance-assets" \
A9_BTRY_SOUND=0 \
A9_BTRY_ONB=/home/user/A9/scripts/mainline/acceptance/onb.py \
A9_BTRY_HOLD=3600 \
  /home/user/A9-build/mainline/b-try.sh "$image/boot.img" "$image/vendor.img"
```

The current integ7 pair includes FIX6, 6r/6r2, FP6 and candidate8. The equivalent
launcher is `scripts/mainline/acceptance/run.sh`; override `A9_SWEEP_IMAGE` or
`A9_INTEG_IMAGE` to select another pair. There is no fixed runtime image identity:
`A9_SWEEP_IMAGE` selects it, and the hook compares the owning `A9_BTRY_BOOT` bytes
to its `boot.img`. b-try performs its existing vendor readback after adding its
deadline overlay. No formatting option is added by this command.

The default sweep budget is `A9_BTRY_HOLD - 30` seconds. It reserves 300 seconds
for cleanup/reboot, then observes the remaining time. `A9_SWEEP_SECONDS` can
shorten the budget; keep it below the owning hold. The stability result reports
the actual sampled window, which excludes the expected USB outage during the
warm reboot and the short final report/owner-return interval. The numeric
`lost` counter fails unexplained observation losses. This is an actively polled
awake sweep, not a suspend/power test.

The hook requires b-try's `ADB`, `A9_BTRY_LOGS`, `A9_BTRY_IN_HOOK=1` and
`A9_BTRY_BOOT`. Importing it, building it, running `selftest.py`, or running it
without the owner context does not execute device commands.

## Results and scope

`A9_BTRY_LOGS/acceptance.json` contains every check with exactly
`PASS`, `FAIL`, or `NOT_RUN` and **numeric evidence only**. Each result is saved
atomically as it completes, so interruption leaves partial results. Unattempted
checks remain `NOT_RUN`. `onb.log` prints only fixed check labels/statuses and
numeric measurements. A qualification failure is data; hook success means its
cleanup completed, and b-try retains the full hold/return lifecycle.

The one requested nonnumeric companion is `sweep-permissive-domains.txt`: the
list of permissive type names from the **loaded policy**, not source guesses.
Raw UI, account labels, IP/MAC/USB identifiers, key values and subprocess output
are not saved or printed. No camera/audio payload is exported to the host.

| Check | Numeric evidence and PASS boundary |
|---|---|
| 00 context/assets | Owning boot matches chosen image; APK/source pins match; all three probe APKs install. |
| 01 boot/restart | B + boot completed; one reboot request, changed boot ID, B completed again. Boot IDs stay in memory. |
| 02/03 display/HWC | Vendor E Ink admission succeeds, SurfaceFlinger/composer processes, SF binder and HWC display enumerate. No optical/glass-quality claim. |
| 06/07 input | `getevent -lp` enumerates touch axes, power and volume capabilities. No injected events or physical touch/button acceptance. Audit matrix row06 is frontlight; this requested label measures input inventory only. |
| 08/22 sensors | Sensor types/wake flags, successful registration count, ten-second per-sensor event counts and timestamp ordering. No stimulus, near/far, batching/flush or AOD clock claim; matrix motion is row21. |
| 11 USB | Framework functions contain adb+MTP; sysfs descriptors bind exactly one MTP endpoint to this adb transport and enumerate both interfaces; libmtp opens/list-folders successfully. Names/serials stay in memory. No asleep/OTG/transfer claim. |
| 12 Wi-Fi | Exact test SSID connected and live Wi-Fi NetworkAgentInfo VALIDATED in the same record. Terminal owners retain numeric existing-file reasons and cannot restart; cleanup requires positive bounded off readback. See [integ19 owner/cleanup evidence](WIFI-OWNER.md). |
| 13 BT | ON observed for at least60 seconds, no drops, unchanged crash/recovery evidence, then OFF observed. No pairing/A2DP/audio claim. |
| 19 photos | Existing Camera2 warmup/still probe, both lens facings; valid JPEG dimensions/bytes and capture completion. AE timeout is explicit numeric evidence and does not silently become convergence. Temporary JPEGs are removed. |
| 19 video | Both facings attempt silent H.264 MP4 recording for >=10 seconds; demuxed track duration (or real sample PTS span), sample count, bytes and Camera2 callbacks required. Numeric finalization/readback stages distinguish failures; see [rear-video repair](REARVIDEO.md). No audio track or shutter-sound API. Temporary MP4s are removed. |
| 19 AF/torch | Tagged AF focused-lock results and two-second capture TORCH/FIRED then OFF/READY results, plus CameraManager on/off callbacks. Intermediate scan/lens motion are diagnostic. See [probe repair and stock semantics](../cameras/AF-TORCH.md#integ25-dedicated-probe-repair). Metadata is not optical/tactile acceptance. |
| 18 HW encoder | MediaCodec advertises actual hardware/nonsoftware AVC encoder; 30 frames in, encoded bytes/frames out, EOS. No encoder or no supported YUV input = NOT_RUN with availability evidence; attempted codec failure = FAIL. No screenrecord software fallback credit. |
| 17 decoder muted | Existing named c2.v4l2 AVC decoder, hardware identity, 60/60 decoded and drawn frames plus EOS. All five relevant stream volumes verified at their reported minima first (alarm minimum is 1). The generated fixture has **no audio track** and the probe has no audio output API. Matrix row18 is the codec row; requested label17 is retained. |
| 20 mic | Five seconds at48kHz PCM, sample/nonzero count, peak and RMS. PCM is aggregated in memory and discarded. Ambient amplitude is not a speech-response test or listening acceptance. |
| 25 thermal | Every enumerated thermal-zone temperature and zone count; plausible readings. No emulation or mitigation claim. |
| 24 battery | Level/status/health/voltage/temperature properties; numeric telemetry only, no QC3/calibration claim. |
| 26 SELinux | Global enforcing, total/permissive AVC counts, loaded-policy permissive-domain count and companion list. Any permissive domain/AVC fails this stricter acceptance. Enforced AVC count alone is reported, not hidden. |
| 27 release | Informational `NOT_RUN`, with system/vendor user/release-keys and secure/adbauth/debuggable flags. `release_configuration` reports the stricter predicate without failing this expected development image. Does not certify signing. |
| 28 Play | Reuses row28 run5's one forced check-in and actual completion/GSF-length predicates. A separate install check requires a fresh <=20MiB app, tapping the Play **Install** control, PM attribution to com.android.vending, and launcher activity observed resumed. |
| 29 FBE | Encrypted/file props, mounted f2fs data, ICE-wrapped selection, actual68-byte wrapped-key addition and native blk-crypto kernel evidence. No key bytes/IDs leave the device; not an endurance/preexisting-user-unlock test. |
| 30 fingerprint | Separate [stock fingerprint hook](../fingerprint/README.md) and `integ-20261023-fp1` host candidate: HIDL registration, real chip ID/ready message and 30s stable process. Not attempted by this sweep; enrollment requires the user and is NOT_RUN. |
| A stability | Dialog observations, tombstone/ANR counts and filename/size/mtime fingerprints, crash-event signature, sample/loss counts and elapsed seconds. Changes/rotation fail even if counts match. Persistent fingerprints are compared across reboot; event buffers get an explicit new baseline. |

No audio is played. The installed acceptance APK declares
`MODIFY_AUDIO_SETTINGS` and calls `AudioManager.setStreamVolume(stream,
getStreamMinVolume(stream), 0)` for system/ring/music/alarm/notification. Flags
are zero, so no `FLAG_PLAY_SOUND` or volume UI is requested. All five streams
are set before any readback because streams may alias. A fresh completed JSON
result must contain each exact stream ID once, with `getStreamVolume(stream)`
equal to its reported minimum; alarm minimum 1 is accepted. Minimum volume is
not a claim that arbitrary alarm playback would be inaudible: this sweep never
starts an alarm, shutter sound, or audio playback. There is no AudioTrack/MediaPlayer or audible output fixture;
recording uses AudioRecord/MediaRecorder video-only. Volumes are left muted.
Failure to verify mute prevents decoder/Play launch; no listening route is
credited. All pinned APKs install before the initial mute attempt. Asset/install failure
or missing, partial, malformed or nonminimum readback ends the sweep before
media, Play or other feature checks. The post-reboot mute also fails closed.

Android 16 source inspection explains the old shell-success trap:
[MediaShellCommand.java](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-16.0.0_r1/services/core/java/com/android/server/media/MediaShellCommand.java)
returns 0 after `VolumeCtrl.run()` returns normally.
[VolumeCtrl.java](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-16.0.0_r1/services/core/java/com/android/server/media/VolumeCtrl.java)
does parse `--set`; it passes `com.android.server.media` as the caller package
to AudioService, rather than a package belonging to the shell UID. It also
prints an error and returns normally when zero is below a stream's minimum.
[AudioService.java](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-16.0.0_r1/services/core/java/com/android/server/audio/AudioService.java)
checks caller package/UID and the stream's AppOp: `checkNoteAppOp()` catches
invalid-identity exceptions and returns false; `setStreamVolume()` then
returns without applying the volume. Thus rc=0 does not prove the setter ran.
The caller identity mismatch is a source-supported explanation for the LOS23
observation, not a runtime log diagnosis from this host-only change. The APK
uses its real installed package identity; any policy refusal still fails the
readback check. The `--set`/`--adj` shell loop is removed entirely.

Wi-Fi credentials reuse the existing row28 run5 path: pinned `tools.op` from
`wifi-android/s2-87k/release.json`, service token read in memory from
`/run/agenix/op-token`, and only the two established fields under
`op://vault/item/field`. They go directly to ordinary shell stdin, never
host argv, files, exceptions, root-helper scripts or logs. The hook enables the existing controller and waits for wlan0 before inspecting
saved entries; disabled Wi-Fi is not interpreted as an unknown saved network.
An existing target entry is observed for automatic connection and validation,
without replacing its credentials. Unknown saved state still refuses injection.
Cleanup forgets only the exact entry created by this invocation. No secret was retrieved during host development/tests.

The default fresh app is [Fossify Calculator Beta on Play](https://play.google.com/store/apps/details?id=org.fossify.math),
package `org.fossify.math` (listing checked23 Oct2026). The developer's
[install inventory](https://www.fossify.org/install/) also identifies its Play
availability. Runtime Play UI must expose a free `Install` control; the hook
never accepts a price, purchase, account/password input or sideload path.
If Play is signed out, the check fails with a numeric signed-out observation;
it cannot supply a Google account. Already installed = NOT_RUN, not a new
installation. `A9_SWEEP_PLAY_PACKAGE` selects another known small free app
when the owner has an appropriate signed-in Play session.

Host MTP uses the manifest-pinned `mtp-session` client linked with libmtp1.1.22.
It reads storage information and refuses the Android reset-on-close quirk.
Framework getters and the reboot baseline are retried as read-only observations
across FunctionFS reconnect; no setter or reboot is replayed. Failed framework
reads are unknown (-1), not an observed absence of MTP. See
[INTEG25 USB/restart diagnosis](../usb/INTEG25.md). Probe builders reuse the local Android36
SDK, JDK21 and ffmpeg pins already used by the existing photo/decoder builders.
The host permissive reader links pinned libsepol3.8.1. Its host parsing copy
removes only unsupported functionfs_seclabel capability bit12 from v30 policy;
all type/permissive bytes remain intact. The installed/loaded policy is never
changed. Other unknown capabilities still fail parsing. This compatibility path
was checked against the saved run5 policy and returned `a9ml_rootsh`.

## Fingerprint and USB asleep rows

**The A9 has stock fingerprint hardware support, and should keep an explicit
fingerprint parity row.** The selected saved stock merged DT enables
`sunwave,fingerprint` (`status="ok"`, IRQ97/reset19). The saved stock vendor
`init.sunwave_hs.rc` loads `hs_sunwave_fp.ko` and sets permissions for
`/dev/sunwave_fp`; its fingerprint2.1 init service starts the fingerprint HAL.
A host-prepared row30 implementation and standalone B-try command are now in
[`scripts/mainline/fingerprint`](../fingerprint/README.md); hardware qualification
and enrollment remain unproven.
These are board/vendor artifacts, rather than a generic GSI feature flag.
They establish the hardware/interface presence supported by stock, not working
mainline enrollment/authentication or an independently verified chip revision.

Host evidence inspected for this answer (no device access):

| Artifact | SHA256 |
|---|---|
| `/home/user/A9-build/mainline/sol-wifi/stock-merged.dts`, lines539–550 | `33d536040e96a0989ff1097370de41ee781b594fedd01a3cf706f252975033ff` |
| `/home/user/A9-build/mainline/vibration-f0-20261020/stock-init/init/init.sunwave_hs.rc` | `3e074958128e23b2ab5551e174723f51794edd90725bd2ad1e859e0243c17759` |
| Same stock-init directory, `android.hardware.biometrics.fingerprint@2.1-service.rc` | `e7ac035f7021ea4905177eef1dc0835f98c6a83b2c436e5e4a8e067ad65762b3` |

**USB while asleep needs its own row**, e.g.11b. Awake adb/MTP enumeration,
post-resume reconnect and continuous suspend-time connectivity are three
different results. This active sweep covers awake enumeration only. A sleep
row should report an actually confirmed suspend window, USB continuity or
expected disconnect/reconnect, and post-wake adb/MTP recovery separately.
A6L's asleep-USB claim is also OPEN in the source audit, so this is coverage to
add, not an inferred A6L lead. Fingerprint likewise remains unaccepted on both
ports in that audit. Neither missing row can be closed by this sweep's polling.

## Host verification

Five fake test groups cover owner-context refusal; complete numeric reporting,
credential stdin and exactly one reboot; missing hardware/MTP/signed-out Play;
crash rotation/dialogs/BT recovery/permissive domains; mute refusal/timeout
suppression. No adb/fastboot/EDL, phone script, camera service, USB enumeration
or credential retrieval is performed by the tests. All probe APKs compiled,
signed and verified offline. Device behavior remains untested until the owning
coordinator runs the command above.


## Sweep 5 diagnosis and SWEEP3 changes (23 Oct 2026)

Evidence root: `/home/user/A9-build/mainline/sweep-integ5-5-20261023/`.
`onb.log` contains initial measurements and the later warm-reboot mute failure.
**The retained `boot-0000000072/` logs are from the reboot, not the initial
measurement boot.** `mainline.tar` contains that same one boot. Consequently,
a reboot failure corroborates a broken integration state but cannot by itself
prove the initial failure had exactly the same cause. No source revision is
proved responsible by these artifacts. No phone commands were run for SWEEP3.

| Finding | Evidence and disposition |
|---|---|
| Camera integration failure is real in retained boot | `boot-0000000072/kernel.log:1215` reports CAMSS `-EBUSY` requesting the csid2 memory region; lines1218/1221 report subdevice/probe failure. `logcat.log:6383` reports the provider ready with **0 camera devices**. This precedes probe app access, so app permission grants cannot repair it. Initial photo warmup0 and video available0 are consistent with it. AF/torch are now NOT_RUN when their camera prerequisite is absent. |
| Sensor integration failure is real in retained boot | `kernel.log:1245`: setup state `fail:persist-already-mounted`; `logcat.log:5282`: HAL reports no sensors this boot. Initial sensors completed1/listed0 is real empty enumeration, not an exception hidden by the host. The harness reports setup-ready/setup-failed separately; it does not unmount persist, restart ADSP or bypass the setup refusal. |
| Wrapped-data mount failure is real in retained boot | `logcat.log:1031` reports `INCOMPATIBLE_BLOCK_MODE`, lines1032–1038 report keystore operation error -8, decrypt/read-key failure and vold rc25. Initial encrypted-file0/data-f2fs0 cannot be turned into a pass. Earlier integ4 `eink-quality-20261023/mainline4/boot-0000000055/kernel.log:1024,1042,1045` records mounted f2fs, 68-byte wrapped key and native crypto. The harness now reads early crypto evidence from the current boot's retained kernel log, matching its boot ID, rather than depending on an overwritten live ring. Actual mount/crypto predicates remain required. No storage repair/format is attempted. |
| Audio backend startup gap is real in retained boot; initial mic cause remains unresolved | `kernel.log:1989`: vendor.a9-audio-load exits1 after65.855s; the loader waits for the PCM card and depends on sensor-owned ADSP. Initial mic completed1/samples0 is not valid capture. The new probe starts its worker after onResume, reports permission, microphone-mute, initialized/recording state and numeric exception kind. This removes a foreground-context ambiguity; it does not claim to fix the backend. Earlier `vendor-test-20261020/evidence-k5-audio/mic-gain.txt` has144000-frame captures with peaks63/65. |
| Functional enforcing still includes a permissive diagnostic domain | `sweep-permissive-domains.txt` contains a9ml_rootsh; retained `logcat.log:549` has permissive1. The strict SELinux check remains FAIL. The former AVC -1 was missing snapshot evidence, not evidence of zero denials. |
| Decoder/encoder regression is not established | `onb.log:17` shows the **initial** decoder failed with mute_verified1. The later post-reboot mute failure overwrote it, incorrectly attributing the failure to mute. `logcat.log:39962` says acceptance package is invalid after reboot, consistent with lost probe installation. SWEEP3 reinstalls pinned probes after reboot and records that mute result on restart, preserving the decoder result. Encoder `{}` and missing decoder output lack a launch/timeout reason; new numeric app-stage/return/timeout diagnostics expose it. Missing default Codec2-store messages in retained reboot are a lead, not proof that the named decoder or hardware encoder failed in the initial boot. Earlier R12/front-photo and Surface2 decoder JSON demonstrate older working endpoints, not current acceptance. |
| HWC false negative | `logcat.log:6346` explicitly loads the AIDL composer3 HAL; the initial sweep also had SF/binder/display/E Ink admission. The old ps NAME match is brittle/truncated and used an unrelated init service name. SWEEP3 queries `android.hardware.graphics.composer3.IComposer/default` and SurfaceFlinger directly. |
| MTP harness request is invalid | Old `svc usb setFunctions mtp,adb` is rejected by framework validation. The existing [USB notes](../usb/README.md) specify requesting `mtp`, then independently reading applied HAL functions0x5/applied1/bound1 and host MTP+ADB interfaces. SWEEP3 uses that path and permits the expected brief transport reconnect. Initial adb1/configured0 does not establish a USB driver regression. |
| Wi-Fi failure was unclassified | `preexisting_or_unknown=1` merged two different states before enabling Wi-Fi. SWEEP3 enables first, waits for the interface, accepts flexible header whitespace, and observes a preexisting target without replacing/forgetting it. Unknown state and failed validation still fail. Initial artifacts do not prove a Wi-Fi radio regression; integ2 Play/BT9/9 remains older evidence. |
| Snapshot, cleanup, release and stability reporting | Every saved onb-root-failed script is the old all-or-nothing snapshot. A missing crash directory formerly aborted before AVCs; SWEEP3 treats confirmed absence as an empty set, keeps per-source failure status, and reads framework event logs through the ordinary shell. Cleanup now reports each failed phase numerically and does not warm-reboot after failed closure. Its original failing phase is unresolved because raw command diagnostics were suppressed. Stability bookkeeping runs even if post-reboot reinstall/mute fails. Expected development release flags are informational NOT_RUN, not FAIL. |

Probe launches wake the display and, after mute verification, request dismissal
of an unsecured keyguard. The mute activity shows over keyguard and never
requests dismissal before lowering volume. Launches
use user0 explicitly and poll against a fresh invocation deadline (including
the reserved reboot phase). No credential-protected lock is bypassed. Existing
install `-g` permission grants are retained, and permission failures are exposed
as numeric evidence. No new sound fixture or audio-output API was added.

Host validation: five fake test groups, including Wi-Fi initially disabled,
preexisting-entry preservation, actual app polling beyond the feature budget,
USB request/applied-state separation, reboot reinstall failure without decoder
overwrite, numeric reporting and existing refusal checks. Cached Android mksh,
Toybox and awk also exercised absent/populated crash directories under errexit
and accepted only same-boot retained kernel logs. Logs:
`acceptance-sweep3-selftest.log`, `acceptance-sweep3-shell.log` and
`acceptance-sweep3-build.log` under `/home/user/A9-build/mainline/`.
All APKs rebuilt/signed/verified offline at the documented default assets path.
SWEEP3 changes have no device qualification yet; real integration failures above
remain open for the owning coordinator.

Opt-in encoder startup comparison: `A9_SWEEP_HVENC_VARIANTS=1` together with
`A9_SWEEP_HVENC_QUALIFY=1` runs A/B/C/D from
[the stock causality report](../../../docs/mainline-venus-hfi-causality.md).
Mapped EOS stays on; output-size/work-mode combinations are 1/1, 1/0, 0/1,
then 0/0. Each ordinary qualifier starts a new native process and fresh codecs.
There is one feature sweep and one B boot for these four cases. PIN follows
qualification, with USB/restart afterwards. No module or service is restarted
between variants. A hardware hang can prevent later cases from completing.

The optional stage reserves 480 additional seconds from the existing ordinary
observation window, within the same owner hold; it does not extend the hold.
Each case keeps the original 145-second qualification window, with time retained
for parameter reset. Insufficient remaining time is recorded for that case.
A failed case does not skip later cases. An owner interruption stops new cases
and PIN admission, and parameter reset is attempted in `finally`.

`hvenc-variants/case-N/` retains each original qualification JSON and synthetic
MP4 independently; `hvenc-variants.json` records actual parameter readback,
case results and reset status. Row 18 passes only if at least one case passes
its complete native plus MP4 qualification and all three parameters reset to N.
Its numeric case counts distinguish that result from every case passing.
Defaults remain all N and the variant stage remains off unless selected.

Row `04_gpu_vulkan` now runs automatically after the basic rows, before sensor,
camera and network tests. It adds the missing GPU execution measurement; no new
prerequisite gates other rows. Rebuild the acceptance assets with
`python3 -I scripts/mainline/acceptance/build.py --out <new-assets-dir>` and use
that directory through the existing `A9_SWEEP_ASSETS` mechanism. A prebuilt,
host-tested bundle is at
`/home/user/A9-build/mainline/vulkan28-20261025/assets`.

The existing acceptance APK's `vulkan` mode loads its small arm64 JNI library
through Android's system Vulkan loader in the ordinary app domain. It requires
Qualcomm/A610 device-name recognition, Turnip in the name and
`VK_DRIVER_ID_MESA_TURNIP=18` from driver properties, rather than accepting CPU
Vulkan or a loader-only success. It creates one compute queue, dispatches one
64-thread workgroup into a256-byte storage buffer, waits at most2 seconds on its
fence, makes shader writes visible to the host, and checks all64 words against
`i*3+7`. The GLSL is compiled and SPIR-V validated for Vulkan1.0 during the offline
asset build; the APK contains that library and the manifest pins both sources.
The app result has a20-second polling budget using the existing bounded activity
runner and force-stop cleanup. It never calls an unbounded device-idle wait;
timeout/device-loss leaves potentially in-use resources for process cleanup.
No surface, presentation, CTS claim or GPU power/sysfs change is involved.

After the app attempt, a bounded kernel-log scan counts GPU faults/hangs/recovery/
timeouts and SMMU faults. This is **whole-current-boot scope**, so an earlier
fault also fails this row; previous pstore is not included. An unreadable/empty
kernel log fails diagnostics but does not veto the compute attempt or later
rows. Identity, successful submit/fence/readback and no recorded kernel faults
are all required for PASS. A crash or missing result cannot pass. Existing
`A_stability` continues to report process crashes and ANRs independently.
Only numeric identity-match/API/driver/stage/VkResult/readback/fault fields are
exported; no UUIDs, raw device strings, memory contents or kernel lines.

Host fixtures: `python3 -I scripts/mainline/acceptance/test-vulkan.py` covers
success, wrong/software driver, loader failure, device loss, timeout, corruption,
missing crash result, diagnostic loss and independent later tests. Its actual
Android mksh/awk replay uses synthetic dmesg only. No host test opens DRM or
executes GPU commands; phone qualification remains for the coordinator.
