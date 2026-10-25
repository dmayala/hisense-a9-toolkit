# SWEEP17: fresh-data regression diagnosis and host fix

Evidence: `A9-build/mainline/sweep-integ17-f1-20261024/`, its sibling `.out`,
and integ12/integ14 sweeps dated20261024. This work uses saved files only.
No adb, fastboot, EDL, USB enumeration, phone scripts or credentials were used.
qseecom/FBE sources and images are unchanged.

## Shared failure

Boot114 is the measurement boot; boot115 is the later A-return recovery.
Boot114 reaches boot completion at137s and Wi-Fi serving/Android-ready at109s.
It retains a working front JPEG and decoded hardware playback. The first
front-video recording allocates **c2.v4l2.avc.encoder** (`logcat.log:5390,5401`)
and enters its surface-buffer path (`logcat.log:5556,5557,5705`). Both retained
kernel/logcat streams then stop around299s, with no encoder completion, kernel
panic, HFI error or USB teardown proving the internal mechanism. Compare the
same encoder transport-loss boundary in [ENC-HANG.md](../video/ENC-HANG.md).
The changed dispatch is established; a Venus/firmware/interconnect root cause
is still unproven and remains with the encoder investigation.

The former MediaRecorder H264 request chooses an encoder automatically. The
new hardware encoder registration therefore also changed **camera recording**,
although commit37e1e91 moved only the synthetic row18 test to the end. Integ12
and14 lacked that hardware encoder and passed both camera videos. This is the
shared regression path supported by source and logs. It precedes the root
push, app-launch, Wi-Fi and cleanup failures. The helper reports six snapshot
push failures (phase1/rc1), two completion failures (phase3/rc1), and a Wi-Fi
START push failure. No remote exit/line was received. These are transport/
command failures, **not evidence that root privileges were revoked**. Final
native policy/snapshot/E Ink closure succeed, so a permanent root loss is not
supported either. Original raw push error text was suppressed by the caller;
its exact errno cannot be reconstructed from saved command sources.

The camera probe now explicitly creates a software-only AVC encoder by its
reported component name, requires a surface-capable supported configuration,
drains concurrently into an MP4 muxer and verifies EOS, real file duration,
track samples and camera frames. It never falls back to automatic hardware
selection. `software_encoder=1` is required for row19 PASS. Hardware AVC/HEVC
qualification remains row18 with its existing strict predicate. Both photo
checks run before either recording. Camera capabilities and numeric stages are
saved atomically before encoder access; incomplete results cannot pass.

## Per-row disposition

| Row | Diagnosis and change | Remaining limit |
|---|---|---|
|12_wifi|`controller_rc=1` is the failed **push of START**, not a controller refusal. Service readiness was short-circuited and never queried. Add `service_checked` to distinguish this; remove the earlier unintended hardware encoder selection.|A new connected/validated run is still needed.|
|13_bluetooth|It was observed ON. `crash_or_evidence_loss` combines missing/failed samples with crash-count/signature changes; `dropped` means a parsed non-ON state, not a proved Bluetooth crash. These observations overlap the recording/transport failure; boot114 contains no retained Bluetooth fatal/ANR evidence establishing a separate regression.|Keep the FAIL until a complete uninterrupted60s window passes. No BT stack workaround is justified by these logs.|
|19_rear_photo|Stage1/command_rc1 is failure of the ordinary-shell `run-as ... rm files/result.json` prerequisite, before launching capture. It is not a rear-sensor/JPEG error. Run both photos before video.|Requires a new rear-JPEG result.|
|19_front_video|Stage3 timeout provides no completed result. Saved log shows the new hardware AVC component selected and surface input beginning immediately before retained logs stop. Explicit software selection removes this dispatch regression.|Internal hardware hang cause and actual repaired recording remain unqualified.|
|19_rear_video|Stage1 failure occurred before activity launch after the front-video disruption. It never tested rear recording. Use the same explicit software path.|Requires a new >=10s MP4 with frames/samples/EOS.|
|19_front_af,19_front_torch|`exposed=-1` was synthesized from missing video JSON, not a capability read. Unknown capabilities now yield NOT_RUN/prerequisite_failed. Atomic partial results retain successful enumeration even on timeout.|HI556 has neither actuator nor flash; measured absence stays hardware N/A.|
|19_rear_af,19_rear_torch|Same missing-result attribution bug. Earlier sweeps measured exposed0; rear hardware support exists but its mainline implementation is missing. Do not relabel this hardware N/A.|This change does not implement rear AF/flash. Known exposed0 remains NOT_RUN/implementation_missing.|
|A_stability|Twelve lost observations include native7, events5, window5 (categories overlap). Shell logcat repeatedly gets an enforcing **map** denial on the public event-tag dictionary (`kernel.log:3036,3947`); read/open alone was insufficient. Add only map. The final retained ANR count is2 and saved summaries name system_server, not com.android.phone.|`crash_or_rotation_observations=1` is an ANR-file signature change, **not one proved crash**. ANR trace payloads/blocked stacks were not retained; their cause cannot be honestly assigned or repaired from these files. Stability remains open.|
|cleanup / onb rc1|Both framework Wi-Fi and BT disable commands return failure, while app stops and native E Ink close succeed. Failed cleanup makes onb return1 by design. It is part of the transport-failure cascade; no independent root-policy denial is established.|Preserve failed cleanup, no warm-reboot after failed closure, and owning b-try A return. No blind mutation retries.|

The provider's fdsan diagnostic at `logcat.log:5646` is **signal35 (debuggerd)**,
not a fatal-signal death: the same isolated IPA PID continues producing stats
at line5690 onward. It cannot establish an additional provider crash. An E Ink
rail fault also latched **before** encoder startup (watcher closes gates at
`logcat.log.prev:47133–47134`); this is real independent evidence, not proof of
what stopped the encoder/transport. Neither fault is hidden by these changes.

## FORMAT=1 and Wi-Fi session properties

The fresh-data boot itself proves `vendor.a9ml.wifi.session.state=serving` and
`vendor.a9ml.wifi.android.ready=1`: kernel line2575 runs the stock-radio action
whose compound property trigger requires both. Firmware preparation completed
at58s and ath10k firmware starts at89s. These are volatile, per-boot properties,
set by the existing init/controller session; START reads them without a
`persist.*` dependency. The one-attempt state lives on `/mnt/a9-wifi`, and the
factory MAC is read from persist, not `/data`. Formatting `/data` removes saved
framework networks and settings but did not prevent this run's owner startup.
There is no evidence supporting property resets, another MPSS start or an RF
cap bypass. The Android-shell host replay verifies serving/starting state is
accepted even with the one-shot start property already1; failed/consumed starts
remain refused without mutation.

## Host validation and consumption

`A9-build/mainline/sweep17-host/` holds the offline APK build/sign/verification,
full five-group acceptance regression, four focused SWEEP17 cases, compiled
policy and read/map-allow/write-deny decision checks. Focused cases exercise
atomic partial polling/terminal error/timeout, missing capability attribution,
strict software/EOS video predicates, both photos before videos, the actual
root-helper subprocess with fake adb errors, and actual cached Android shell
START replay. Numeric transport errors distinguish offline1, closed/reset2,
permission3, space/read-only4, missing-path5, timeout6 and unknown0; raw error
text remains private. No retries or privilege changes are introduced.

Rebuild acceptance assets and compile current policy for the next integrated
image; an older manifest/APK will deliberately fail source pins. Existing
integ17 images and sweep verdicts are not modified. This is host-tested source
repair, not new hardware qualification or a claim all rows now pass.
