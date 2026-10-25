# Integ 20261023-6r2 — preserve the shared firmware lookup path

Host-only analysis/build/tests; no adb, fastboot, EDL, phone, credentials or
playback. Hardware remeasurement remains with the owning coordinator.

Build outputs: `/home/user/A9-build/mainline/integ-20261023-6r2/`:
`boot.img`, `vendor.img`, `SHA256SUMS`, `integrated-build.json`, source handoff
and host test logs. Source recipe: `python3 -B scripts/mainline/reboot/build2.py`.
Use a fresh output path to reproduce. The current acceptance hook on main must
accompany the image; the primary correction is in the host hook's E Ink setup.
Existing pinned APKs remain valid (no Java/manifest changes).

## First failure, not the teardown symptoms

Saved sweep: `/home/user/A9-build/mainline/sweep-integ6r-20261023/`, boot77.

| Evidence | Meaning |
|---|---|
| kernel1241–1242,25.80s | Sensor starter reuses the verified shared `/mnt/a9-wifi/firmware` tree; it does not replace the firmware path. Sensors later enumerate5 and deliver events. |
| kernel2941,134.29s | Factory MAC read from the real persist partition succeeds. The helper reads persist read-only/noload at its private mount point, then unmounts it. It does not read the TEE bind's data. |
| kernel3235,160.89s | PREPARED: all confined companions ready; MPSS **not yet started**. “MPSS is still offline” is this expected preparation message. |
| kernel3554–3561,192.30s | First activation fails: modem.mdt lookup `-2`/ENOENT, request_firmware failed, remoteproc boot failed. |
| kernel3562–3565,192.33s | `label=die`; rmtfs/tqftpserv/diag-router exit records all **none**. This label marks controller failure; it is not proof any companion exited first. |
| kernel3581,193.43s;3607,195.74s | Controller stage failed; rmtfs reports teardown-signalled at194.199s. Companion termination follows the failed modem start. No preceding companion fault is present in the retained evidence. |
| kernel3596,3654 onward | No successful start-write-returned marker, so uncertain teardown trips the existing fail-safe. Its independent paths repeatedly request bootloader201–223s. This behavior is preserved. |

Root cause: `play/onb.py:EINK_OPEN`, reused by acceptance, unconditionally wrote
`/data/local/tmp/a9fw` into `firmware_class.path` after installing display blobs.
That directory contains the display files, not modem.mdt. Integrated images
already have the correct root-owned shared bridge, whose `hisense` link points
at that same display directory. The E Ink PASS proves the old command ran.
Preparation can validate the shared path before the hook overwrites it; later
MPSS activation then fails. No persist or wrapped metadata change is needed to
explain the missing firmware.

Integ5 sweep6 did **not** have this `label=die` sequence. Its boot73
`kernel.log:6002–6030`,358–360s, records the same factory-MAC success followed by
`firmware-lookup kernel-path`, then “prepare rc=1 (modem never started)”. It hit
the same hook defect earlier in the lifecycle, before a start attempt.6r's
boot-time session preparation exposed the later activation/cleanup path.

The6r delivery moved sensor RAM staging away from `/mnt/vendor/persist`; it
retained the existing TEE bind onto that path. Its Wi-Fi controller, bridge and
session helper were byte-identical to FIX6.6r2 leaves persist/metadata/bind,
factory-MAC handling, wrapped-data script and vendor properties unchanged.
The previous6r report's wrapped-key runtime limitation remains open.

## Correction and unchanged safety

- E Ink setup retains either the shared bridge or the already selected plain
  E Ink path. Shared mode runs the existing strict bridge validation; unknown
  paths/bad links refuse before opening gates. The hook never writes the global
  parameter. Display files still go through the existing private directory and
  vendor gate/fault watcher; settle interval/caps/gate sequence stay unchanged.
- Controller adds a firmware-path/readable-modem check before the sole MPSS
  start attempt and RMTFS release. It refuses a changed/missing lookup; it does
  not repair it, retry MPSS or publish a successful write marker on error.
- Single-start/one-boot admission, companion confinement, RMTFS gate, QRTR
  policy, FD/auditor freshness, recovery-disable readback, bounded deadlines,
  stop-before-server teardown, real-store protections, watchdog, guardian and
  bootloader fallback code remain byte-identical. Build verifies that stripping
  only the added check restores the exact shipped6r controller.
- Packaged controller SHA256, tools/SHA256SUMS and session manifest are updated
  together and verified by installed-byte readback. No tool lock/provenance,
  session authorization, country or identity settings change.

## Cameras and lost checks

Both sensor chips now probe: boot77 kernel1194 S5K3L6 ID0x30c6 and1198 Hi556
ID0x556. No duplicate-CSID probe failure recurs. This does not prove usable
Camera2 cameras or a successful photograph.

The photo rows report app_stage1, activity_resumed0, no_camera0, warmup_frames0.
Stage1 is the host's `run-as ... rm -f files/result.json` prerequisite, before
`am start`. Other app rows similarly report command_rc1. They contain no capture
attempt or Camera2 callback error and occur after the decoder wait crosses the
Wi-Fi failure/reset interval. Transport/shutdown loss contaminates these rows;
saved logs do not establish another camera hardware regression. No camera
permissions or driver changes are justified by that result.

Acceptance now saves numeric `service-inventory.json` and a SERVICE_INVENTORY
log line before the long codec probe: completion, app stage/command rc, camera
and sensor counts from the existing app health mode. This is diagnostic only;
photo/video PASS still requires their original actual-capture predicates. Photo
rows retain command_rc instead of discarding it. The other post-reset failures
(BT/evidence loss, SELinux unreadable, mic/encoder transport failures) remain
FAIL, without being called independent subsystem regressions.

Row01 data_healthy0 was already measured before the Wi-Fi failure (onb8);
that storage result is not explained by later transport loss. Confirmed tmpfs
still makes row29 not applicable, and cannot qualify the declared real-data
candidate.6r2 adds no storage change or claim of wrapped-key recovery.

## Host validation and artifacts

```text
boot.img   853710225e508e6514ad48137449e0836916ee0ed0060e980bc9f2daad4c9c16
vendor.img ab8bcdd674fec46a69ccaadc4db2b4ad52908d2dcbfa049b6937702c1cb4281f
```

Only three regular vendor files change: controller, tools sums and session
manifest. All1312 other regular files compare byte-for-byte, including all100
kernel modules, firmware, scripts, rc and SELinux policy. Boot/kernel/DT remain
identical to6r. The previous native module ABI/ABL/init checks therefore remain
applicable. Changed-file mode/uid/gid/SELinux label/timestamps preserved;
e2fsck and tool/source readback PASS. Minimum15GiB disk reserve maintained.

Self-checks (no independent hardware qualification):

- `selftest2.log`: five groups PASS. Actual Android mksh/Toybox/helper tests
  integrated and plain firmware paths, unknown-path/corrupt-bridge refusals;
  actual new controller admission on Android; packaged delta/storage/safety
  preservation; camera prelaunch failure distinct from capture evidence.
- `controller-tests.log`: four selected existing-controller tests PASS:
  changed/missing lookup refuses before start/release; prior RMTFS death still
  refuses release; valid start keeps both full FD inventories/freshness passes;
  real fail-safe code still records evidence then requests reset (stubbed I/O).
- `acceptance-selftest.log`: five host fake groups PASS, including early service
  inventory before decoder and unchanged mute/capture acceptance.
- Final source/image hashes and all existing APK asset/source pins verified.
  No APK installed. The build uses no mounts/network/device tools and does not
  alter the6r input image or another owner's artifacts.
