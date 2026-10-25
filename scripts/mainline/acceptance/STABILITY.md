# Stability evidence and boot94 diagnosis

Host-only follow-up to `sweep-integ8-r2-20261024`. No phone access or image build.

## What the saved evidence establishes

The final sweep reports65 ANR files,228 crash/rotation observations,10 lost
observations, and221 ANR-file/210 event-buffer signature changes in3560seconds.
Those signatures cover file metadata and the current selected event set, not
unique incidents. Retention, ongoing trace writes and event-buffer rotation can
all change them;65 retained files cannot establish the total number of ANRs.
Real startup ANRs and lost observations still fail the existing stability row.

Boot94 is the **post-reboot** boot. Its logcat is empty and the archive has no
ANR trace payloads. The initial feature boot and boot94 must not be conflated.
The kernel nevertheless establishes this failure chain:

| Kernel line | Observed event |
|---|---|
|3143–3144|Owner preparation completes around175s; MPSS is still offline at that stage.|
|3480|Owner enters `die` around215s.|
|3501,3521|Controller-stage failure, then companion/controller watchdog fault.|
|3574|Exact STOP reason: `tqftpserv descriptor inventory unavailable`.|
|3577–3587|MPSS is taken offline around220s, before tracer/companion teardown.|
|3591|Session ends failed, rc1.|

There is no QCRIL startup in this boot's kernel record. The stock-radio init
requires this owner's serving/Android-ready release before preparation and
startup. It cannot reach that release after this abort. The earlier6r3 boot89
logcat directly shows `com.android.phone` startup ANRs while its main thread
waits for HIDL IRadio/slot1, followed later by slot2 waits. Missing radio service
after the new owner abort is therefore a supported **likely** cause of the
boot94 ANR storm. It is not proof that every one of the65 retained traces belongs
to that process; the new collector will establish the names.

No SIM alone is not a supported cause. The imported stock-radio candidate8
passed22 no-SIM checks in211seconds, including radio service instances and
ABSENT SIM state; see [the stock integration record](../cellular/stock/README.md).
The shipped stock qcrild service definitions are disabled services activated by
stock startup actions, as in the integrated definitions. This change neither
disables the phone process nor starts stock radio clients ahead of the MPSS
owner's readiness boundary. The board-slot2 selector fix is already in integ11.

The immediate owner abort is real. Its `stat -L /proc/.../fd/*` failure did not
retain errno. A descriptor closing during the snapshot and denied proc access
require different repairs. The controller now reports only role, command status
and numeric error class2 (missing object),13 (permission), or0 (unclassified).
The complete-inventory requirement and terminal failure are unchanged; no
partial inventory, retry or bypass is accepted. The underlying descriptor
failure is **not yet repaired or attributed to a particular errno**.

## Fixed logging failures

Boot94 contains6022 root-console denials for searching init's proc directory.
The pre-switch-root logger first accesses `/proc/1/root/system/bin/logcat`, so
it never gets past that prerequisite. The new policy grants only init proc
directory metadata/search, symlink reads and the FILE__READ permission used
by Linux's PTRACE_MODE_READ check. It does not grant PROCESS__PTRACE attachment.
Existing logd socket permissions were already allowed.

Fourteen shell read denials target the public runtime event-tag dictionary.
Its read/open/getattr permissions are added so the event reader can decode
events. They are a concrete observation-loss cause; the saved output does not
prove they account for every one of the ten lost observations.

The compiled host policy allows the seven tested read permissions and still
denies init ptrace attachment, ANR-file writes, and event-dictionary writes.
It has zero permissive domains. ANR file read/open is added specifically for
the requested bounded summary collector.

## Evidence saved by the sweep itself

The owning log directory now receives two0600 files, independent of the boot
logger:

- `stability-events.jsonl`: new `am_anr`/`am_crash` events, with only the event
  tag, validated process name and numeric pre/post-reboot index. Full event
  timestamps/PIDs/UIDs/reasons remain in private memory and are never saved.
  A private in-memory deduplication set avoids replaying whole snapshots.
- `stability-evidence.json`: process names from bounded `dumpsys activity
  lastanr` and `/data/anr` prefixes, plus numeric radio-wait/startup-timeout,
  collection status, error and limit fields. Trace file names, raw command
  arguments, stacks and dump payloads are not saved. Retained trace observations
  are not labeled as unique new ANRs or assumed to belong to the current boot.

Collection occurs on the first observed ANR/crash, at most once per minute
thereafter, and at completion/interruption. It reads at most128 regular,
non-symlink trace files and64KiB per file. Activity output is capped at128KiB.
Valid process-name evidence from a partial event read is retained while that
read's nonzero status still fails the observation. Numeric `STABILITY_LOSS`
records distinguish native, event-reader and window-dump losses. Acceptance
criteria do not become weaker.

Five focused cases PASS, including actual Android head/awk execution, private
text/PID/timestamp removal, duplicate snapshots, partial reads, retained-count
ANR changes and loss attribution. The existing five acceptance cases PASS.
Two focused controller cases cover five inventory outcomes and preserve the
existing native-stat missing-object refusal. Local compiled-policy decisions
are under `A9-build/mainline/stability-fixes-20261024/policy`.

The next integrated build must compile current policy and restage the changed
coordinator with its normal tool manifest. Existing queued images are untouched.
The logging failures are repaired; a stable radio session and zero ANRs remain
unproven until an owned run supplies the missing descriptor/ANR evidence.
