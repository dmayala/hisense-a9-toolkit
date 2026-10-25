# integ29 r5: bootloader exit, drain and rear photo

Host-only investigation, 2026-10-25. No adb, fastboot, EDL or phone access.
Evidence root: `/home/user/A9-build/mainline/sweep-integ29-r5-20261025/`;
owner output: sibling `sweep-integ29-r5-20261025.out`. Comparisons use the
corresponding r3/r4 directories and the frozen integ29 runtime/vendor image.
Line references below are to those retained files, not newly collected data.

## Android exit: userspace deadman expiration, not a kernel deadline or panic

There are two distinct boots in r5. Boot143 completes the ordinary rows and
the requested warm restart. Boot144 passes restart health, then remains up
until the development deadman deliberately requests the bootloader.

| Boot | Uptime | Retained event |
|---|---:|---|
| 143 | 160.918 s | Kernel boot deadline disarmed, USB configured, PON=0, watchdog handoff rc0 (`kernel.log:3929`). |
| 143 | 525.766 s | `sys.powerctl='reboot,shell'` from `/system/bin/reboot`, matching the sweep's one requested restart (`kernel.log:6503`). |
| 144 | 107.214 s | Kernel boot deadline disarmed again (`kernel.log:4334`). |
| 144 | 110.509 s | Userspace deadman observes boot complete and starts its **5400 s** hold (`kernel.log:5152`). |
| 144 | 5513.156 s | `a9ml-deadman: firing (hold-expired) at 5513s: reboot to bootloader` (`kernel.log:14102`). |
| 144 | 5513.212 s | init receives `sys.powerctl='reboot,bootloader'` from `setprop` (`kernel.log:14103`). |
| 144 | 5528.998 s | `watchdog0: watchdog did not stop!`, during the ensuing shutdown (`kernel.log:14224`). |

The exit occurs about90 minutes after boot144 completes, not halfway through
the7200 s hold. The owner reports `elapsed=7353s` only after waiting for the
hook. `hold_b()` waits synchronously for that hook and does not poll USB while
it runs, so its final `fastboot_during_hold` classification is delayed. Android
wall-clock timestamps jump across boots; the kernel uptime sequence is the
reliable chronology. The final logcat timestamp is10-17 10:31:52.781.

The actual vendor `bin/a9ml-deadman.sh`, read offline with debugfs, has
`HOLD=$(flag a9ml.hold 5400)`, a volatile
`STAY=/dev/a9-row29-hold/stay`, freshness600 s and default uptime cap7200 s.
No `a9ml.hold` override appears in the retained command line. The b-try
`A9_BTRY_HOLD=7200` controls the **host** wait; it does not update that Android
hold or renew its volatile lease. The successful warm reboot also removes
any previous `/dev` lease. The deadman expiry proves no effective fresh lease
extended the5400 s hold on boot144; the retained files do not distinguish an
absent lease from a stale one.

There is no panic/reset signature before this explicit shutdown request.
Both saved `pstore.previous.log` files are empty; `pstore.tar` contains only
an empty directory. Empty pstore alone would not exclude a panic, but here
the live kernel log supplies the initiating userspace request. The later
watchdog shutdown warning is not its cause. The bootloader-message error
about missing `/misc` is also downstream of the request.

r3 has two separate incidents: its known earlier encoder reset and, on
boot140, the **same** deadman expiry at5514.959 s followed by
`reboot,bootloader` at5515.043 s (`boot-0000000140/kernel.log:7297/7299`).
The shared final HANG label must not attribute r5 to an encoder that did not run.

**Proposed fix:** connect the owned long-hold deadline to the existing bounded
Android hold/lease, including re-establishing it after the intentional warm
restart. Verify against the *actual* `/dev/a9-row29-hold/stay` path and its
freshness/uptime cap. Preserve expiration after owner loss. This report does
not change deadman, watchdog or boot/recovery behavior.

## The crash/tombstone is the camera provider, before the reboot

`boot-0000000143/logcat.log:10762–10765` records:

- 16:05:45.494: `Control 0x00980915 type and info type mismatch`.
- 16:05:45.495: `controls.cpp:750 assertion "validate()" failed in ControlInfoMap()`.
- The camera provider aborts with SIGABRT in its `CameraManager` thread.

The process is `android.hardware.camera.provider@2.4-service.a9`;
`tombstones.jsonl` records signal6 and245041 bytes. The control ID is
`V4L2_CID_VFLIP` (`V4L2_CID_BASE+21`), not an encoder control. The failing
ControlInfoMap validation requires each control's range type to match its
ControlId type. The exact producer of the bad range is not established by
the symbol-limited tombstone. The log places the assertion immediately after
the rear pipeline's first frame/statistics/control write.

`stability-traces.jsonl` captures that provider tombstone on boot0/sample2.
The identical tombstone reappears on boot1/sample4 because `/data` survives
the warm reboot; it is not evidence of a second provider crash. Likewise,
the pre-existing `com.android.phone` ANR (Safety Center broadcast, cause2)
is separate. Both crash summaries report zero system_server fatals.

`A_stability.lost=207` is a count of failed observations:103 failed
native/event/incomplete snapshots,103 failed window reads, plus one failed
diagnostic collection. `probes=409` counts successful window observations;
207/409 is not a failure fraction over one common denominator. The single
crash-event and tombstone signature changes identify the earlier camera
abort; the later repeated transport losses follow Android leaving for fastboot.

## Rear photo: real intermittent provider failure, not a timing false negative

r5's rear result is a **completed failure**: phase4, zero warmup frames,
`timed_out=0`, and raw app reason `disconnected`
(`boot143/logcat.log:11101`). The host table does not currently retain that
reason. The provider abort above explains the disconnection before the first
preview result reaches the app. There is no JPEG to accept.

r4 produced a valid112027-byte1280×960 rear JPEG; r5's front photo succeeded.
Later rear AF/torch/video rows can recover after init restarts the provider,
but do not erase the crash. This is an intermittent **real camera HAL/libcamera
defect**, distinct from integ28's UI-mode Activity recreation and host timeout.

**Proposed fix:** trace and repair the VFLIP ControlId/ControlInfo type mismatch
at the sensor-control/IPA map boundary. Log the numeric ID and expected/actual
types there, and exercise front→rear configure/reconfigure plus the first
capture. Keep map validation; increasing capture time or automatically retrying
would conceal the abort. No speculative camera patch is included here.

## Drain phase6: Android grep rejects the sleep-state expression

r4 and r5 both reach charging pause, screen preparation and a valid start
snapshot (capacity87/86); they abort in the first sampling loop at phase6.
The failure is unrelated to the later deadman exit. Kernel/framework logs
show the display actually sleeping at04:33:00.989 in r4 and04:50:10 in r5,
then waking about3 s later during restoration. Both restoration receipts pass.

The production snapshot uses this basic-regex expression:

```sh
grep -q 'mWakefulness=Asleep\|mWakefulness=Dozing'
```

The cached Android Toybox grep returns1 for **both** matching input states.
Its basic-regex implementation does not implement GNU grep's `\|` extension.
Consequently `screen_off=0`, and the required standby conditions reject the
measurement. Earlier host tests ran Android mksh but resolved `grep` through
the host PATH to GNU grep, which accepted the expression and masked the bug.
The old receipt stores only `failed_phase`, not each condition; the exact
reproduced predicate failure plus the recorded sleeping state establish this
fault without claiming other unrecorded condition values were all healthy.

**Landed fix:** use `grep -Eq 'mWakefulness=(Asleep|Dozing)'`. The existing
focused tests now select the actual cached Android tools and check Asleep,
Dozing and Awake. Charging restoration and all measurement conditions remain.
`wakeup_sources_before=0` is diagnostic unavailability and is not phase6's
cause. Integ28's phase4 was an earlier screen-preparation failure; moving to
phase6 means that stage is now passed, not that an hour was measured.

## Encoder skip: supported on main

The frozen runtime patch checked `A9_SWEEP_SKIP_ENCODER=1` only around the
ordinary `hardware_encoder()` call. A qualification/variants run could still
load its encoder and reserve time.

**Landed fix:** main validates the option as0/1, defaults to0, and when1:

- `hardware_encoder()` records NOT_RUN, attempted0, skipped_known_reset1,
  before asset inspection, staging or codec execution.
- Qualification and variants are disabled along with their time reservations.
- Other rows, cleanup, USB and warm restart retain their normal execution.

The skip is explicit opt-out data, never an encoder PASS. Export
`A9_SWEEP_SKIP_ENCODER=1` through the launcher environment; no frozen runtime
needs editing. Default/0 retains the original encoder behavior.

## Host validation and scope

- `python3 -I scripts/mainline/acceptance/test-skip-encoder.py`:3 tests PASS,
  including default/0, opt-out with ordinary and qualification/variants options,
  later photo/USB/restart/cleanup rows, and malformed-option refusal.
- Existing `test-real-tests.py`: PASS; default qualification and PIN still run
  before USB when the skip is unset, despite failed diagnostic observations.
- `python3 -I scripts/mainline/acceptance/test-drain.py
  Tests.test_5_real_android_clock_and_input_suspend_readback
  Tests.test_6_framework_commands_use_shell_and_keep_watchdog_tracking`:
  2 tests PASS using fake sysfs/services with Android mksh/Toybox and the cached
  drain-clock under QEMU. This supplies no real battery/residency measurement.
- Python syntax and `git diff --check`: PASS.

Test logs: `/home/user/A9-build/mainline/agent-integ29-r5-20261025/`.
The initial analysis implemented the drain regex and encoder opt-out.
The follow-ups below record the subsequently requested lease and camera fixes.

## Implemented follow-up: owner-bounded hold lease

`b-try.sh` now starts `boot-deadline/hold-lease.py` with the same absolute
`A9_BTRY_OWNER_END` supplied to the hook. Every60 s it uses ordinary slot-B
shell to renew the vendor's existing `STAY` path and set the existing uptime
cap to current uptime plus the **remaining** owner budget and60 s return
margin, limited to the existing21600 s ceiling. Reboot removes the volatile
file; the keeper tolerates the disconnect and recreates it when B shell
returns. It never starts a new7200 s budget after `01_restart`.

The owner stops/reaps the keeper before return-to-A; parent death also stops
it. The Android deadman implementation,5400 s fallback hold,600 s freshness,
uptime ceiling, watchdog and boot/recovery paths are unchanged. Lease traffic
is one small shell heartbeat per minute, matching the existing keeper cadence.
`hold-lease.log` records numeric renewal status without raw transport output.
The incremental builder includes the new host helper beside `b-try.sh`.

`python3 -I tests/test-hold-lease.py`:3 host tests PASS, using Android
mksh/Toybox and fake files/properties. They simulate reboot then lease renewal,
cross the old5400 s expiry with the original deadline, retain owner-loss
freshness/uptime limits, reject slot A, and check keeper exit with its owner.
Syntax/diff checks pass. **No new integrated image is needed for this fix**;
use refreshed host runtime files (`b-try.sh` plus `hold-lease.py`) for a new
session. No frozen or running session was edited.

## Implemented follow-up: refresh lens ControlIds after IPA configure reset

The sensor already uses `v4l2_ctrl_new_std(... V4L2_CID_VFLIP, 0, 1, 1, 0)`:
the standard V4L2 boolean control is correct. The fault is in the AF addition's
IPA map lifetime. The generated isolated proxy **and worker** call
`controlSerializer_.reset()` before `configure`. That deletes the ControlIds
owned by the serializer, but `lensInfoMap_` was a shallow copy retained from
`init`. Its focus range is int32; reuse of a freed ControlId for sensor VFLIP
gives the observed boolean-ID/int32-range validation failure. The retained
stack reaches `setLensControlsHandler` and `ControlInfoMap` deserialization.

Patch `0009-refresh-lens-map-after-configure-reset.patch` carries `lensControls`
in `IPAConfigInfo` alongside sensor controls. The pipeline supplies the current
lens map on every configuration; the IPA rebinds `lensInfoMap_` after the reset.
Front configuration supplies an empty map. Init still reads lens ranges for
algorithm setup, without retaining those borrowed IDs. Driver control types,
sensor orientation/register writes, stock-derived modes/timings, focus ranges
and map validation are unchanged.

The wire data changed, so incremental vendor payloads now include the matching
camera core, IPA module and worker together. **A new vendor/integrated image
is needed for the camera fix**; no kernel or boot-image rebuild is required
by this change alone. The builder's `--camera-android-build` must reference a
fresh build with the current patchset. Existing stale patch pins are rejected.

Host validation: all229 camera build targets and installation passed in an
isolated directory. `cameras/tests/lens-map.py --build <directory>` compiles
the actual IPA implementation and runs it under bionic/QEMU with production
ControlSerializer/IPADataSerializer. The regression fails with the old
init-only map, then passes front→rear→rear reconfiguration with serializer
resets, boolean VFLIP ranges, int32 focus ranges and a focus-command round trip.
The three-member staged payload passes `native-payload.verify`; syntax and
diff checks pass. These are host results, not a physical rear-JPEG pass.

Build, matching staged payload and test evidence:
`/home/user/A9-build/mainline/agent-integ29-lease-camera-20261025/`.
