# SWEEP4: MTP reset, transport admission and storage diagnostics

Host-only analysis, build and tests. No adb/fastboot/EDL/phone operation, live
credentials or playback. No image or device-state change. Use current main's
acceptance hook and rebuilt assets at
`/home/user/A9-build/mainline/acceptance-sweep-20261023/`.

## Why native and ordinary commands stopped working

Saved evidence: `/home/user/A9-build/mainline/sweep-integ6r2-20261023/`,
`repull/boot-0000000078/`.

| Evidence | Interpretation |
|---|---|
| onb9 MTP session1, device1, same ADB/MTP device1 | The host successfully opened an actual MTP session. |
| logcat28828–28835 | Framework switches to MTP at16:03:06.811; ADB endpoint disconnect during composition change is expected. |
| logcat30520–30531,16:03:35.523; kernel3684–3706,204.88s | At session completion both MTP and ADB endpoints shut down again; gadget becomes unbound and adbd reopens ep0. |
| kernel3715/3723 | UDC rebind warning, then configured1 at206s. This is USB configuration, not proof the host ADB stream or loopback-root command is usable. |
| onb10 onward | Both ordinary app preparation and native helper fail. Remote exit marker is absent, so the old numeric detail cannot distinguish push failure, timeout or missing remote completion. |
| repulled kernel/logcat | No rootsh service stop/crash record or enforcing nc denial explains this boundary. Root-domain logger runs throughout; adbd remains PID1762 and Android stays on the same boot. Root listener failure alone would not explain ordinary-shell app_stage1 failures. |

The host `mtp-folders` uses libmtp1.1.22. Its public
[device table](https://github.com/libmtp/libmtp/blob/v1.1.22/src/music-players.h#L2575)
assigns Android flags to this composite USB product. Those
[Android flags](https://github.com/libmtp/libmtp/blob/v1.1.22/src/device-flags.h#L314)
include forced reset on close. The
[USB close implementation](https://github.com/libmtp/libmtp/blob/v1.1.22/src/libusb1-glue.c#L1961)
therefore resets the entire composite device after a successful MTP session,
including the owning ADB connection. This source behavior matches the second
endpoint shutdown. A successful MTP command return was incorrectly treated as
proof the owning transport remained usable.

SWEEP4 replaces that CLI with a pinned host `mtp-session` executable. It uses
libmtp for a real session/storage response, prints only numeric verdicts, and
exports `libusb_reset_device` to refuse resets with NOT_SUPPORTED. It never
reports that a reset succeeded. It lists no folders/objects and transfers no
media. It opens a device only after exactly one MTP device is proved to be the
same device as ADB; ambiguous enumeration fails. The executable interposition
is linked into the probe, without a runtime optional preload or unguarded tool
override. If a session cannot work without reset, the MTP check fails.

USB measurement now follows the feature checks and existing cleanup. A bounded
post-session admission requires an ordinary-shell B/boot-completed response
and a fresh numeric root response. These are read-only retries; mutations with
unknown completion are never replayed. No rootsh/adbd/server restart, policy
widening, background recovery owner or USB power reset is added. Missing proof
fails USB/stability and blocks the sweep's optional warm reboot. The existing
b-try owner retains return-to-A and all recovery authority.

MTP configured0 was distinct from its successful descriptors/session. The old
framework parser required one exact text form. It now accepts repeated and list
forms of current_functions, checks applied/connected/configured flags, repeats
readback after transport admission, and records each numeric predicate/command
return. The exact original dump was not retained, so a text-format mismatch
alone cannot be proved to explain that configured0. Actual MTP and framework
state must both pass; the repair does not turn descriptors alone into PASS.

Native helper failures now emit a fixed numeric phase on stdout even when the
caller suppresses stderr:1=push,2=execution/timeout,3=remote completion/status
validation. Acceptance preserves that phase and rc beside the remote rc/line.
Saved failing scripts and normal remote status validation remain intact.

## Storage failed before USB

Kernel946–949,17.55s: keystore createOperation error`-8`,
decryptWithKeystoreKey failure, read_key failure in mountFstab, vold return25.
Later `/data` is confirmed tmpfs (onb71), rather than f2fs. The declared
real-wrapped candidate therefore correctly has data_healthy0 before MTP/onb7.
This is not evidence loss from the later USB reset. The existing6r correction
of vendor patch properties has not established successful unwrap of the stored
key; **that actual storage blocker remains open**. This task does not delete,
format, migrate, replace or bypass existing keys to obtain a pass.

Row01 now retains real_data_required, data_ready, data_rc, mount count and
f2fs/tmpfs counts. An intentional RAM trial still makes row29 NOT_RUN with
not_applicable1; it cannot satisfy a real-data boot. No false storage PASS or
automatic real-data repair was introduced.

## The939s reboot request

Kernel6021–6023 records `/system/bin/reboot` requesting reboot,bootloader at
939.234s; init also reports missing `/misc` for bootloader-message lookup.
The saved evidence does not identify the host invoker of that process. It does
not show the earlier6r Wi-Fi fallback sequence or a dead-man expiry: dead-man
armed2400s boot completion/5400s hold and observed boot completion at130s
(kernel867/2954). A request is not proof of reaching fastboot, so b-try's failed
return is retained as a failure. Its recovery state machine is unchanged.
Preventing the MTP-triggered reset and proving transport afterward addresses
the harness-induced loss that preceded this; return-to-A recovery still needs
the owning run to verify it. This host task consumes no attempt.

## Validation

- `/home/user/A9-build/mainline/sweep4-focused.log`: five offline groups PASS.
  The real compiled probe runs only with a fully synthetic libmtp backend whose
  reset function aborts if called. Executable interposition refuses it; valid
  storage succeeds, empty/ambiguous/open-failed sessions fail. No real USB scan.
  Also checks bounded ADB/nc read-only reconnection, loss blocking reboot while
  prior feature verdicts survive, numeric helper phases and real-data failure.
- `sweep4-selftest.log`: five existing fake groups PASS, including list-form USB
  parsing, feature/cleanup-before-MTP order and mute fail-closed behavior.
- `sweep4-build.log`: all three APKs signed/verified, host policy helper and new
  host MTP probe built offline. Five asset and seven source pins verified
  against final main; the probe is never installed on the phone.
- `git diff --check` PASS. This is host self-check, not hardware qualification;
  camera/codec/mic/FBE and return-to-A need a future owning run's actual evidence.
