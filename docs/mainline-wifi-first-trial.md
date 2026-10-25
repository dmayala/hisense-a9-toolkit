# Coordinator-only first Wi-Fi trial

2026-10-16. Staged controller; **no phone command was executed during development**.
The offline host tests do not make the preliminary production target checks
pass. Complete those checks in a separately device-enabled coordinator session.
The user has authorized Wi-Fi tests and explicitly accepted the residual opaque
MPSS/TZ storage risk. MPSS startup is RF-capable and needs the controlled
coordinator conditions described below. Linux process
confinement remains mandatory and is not a hardware isolation claim.

`scripts/mainline/wifi/coordinator-target.sh` runs only in a root shell on the
diskless mainline slot-B vendor with `ro.a9ml.wifi=1`. It cannot select a device,
flash anything, invoke a host transport or restore a calibration store. Its
manual `run` command owns one MPSS activation, observation and shutdown. The
ordinary default-off images and ordinary deployment guard retain their behavior.

## Build and review before any phone access

Build reviewed static userspace, the full Wi-Fi kernel/modules and private DT,
snapshot, vendor and boot images through the existing `--wifi`/`A9_WIFI=1` path.
Android integration is separately opt-in with `A9_WIFI_ANDROID=1`; its filename
ends `-wifi-android.img`. No firmware, golden EFS inputs, MAC, device identifiers
or generated image belongs in git. All artifact and runtime evidence is private.

The effective 512-byte boot command line must contain the single custom path
`firmware_class.path=/mnt/a9-wifi/firmware`. Root's reviewed bridge helper links
radio inputs to immutable `/vendor/firmware` and the E Ink `hisense` directory
to the existing runtime display-firmware location. The coordinator invokes and
checks this helper **before** the wireless module list; it never silently
replaces the display path. Supply/board/MDT/split/config/module-ABI and existing
ABL checks must all pass for the final artifacts together.

Run `android/android-boot.sh --wifi --offline` with the exact selected images,
then the established coordinator's check/approval-code and slot-B boot path.
`--wifi` adds `verify-images.py`: current source pins/archive and patch hashes,
native boundary hashes, selected locally built static ARM64 binaries, exact
reviewed radio init/prep scripts, complete installed inventory, snapshot modules,
boot kernel/DT and deployment DTBO must agree. A flag or renamed binary does not
bypass this check. Root-bin reviewed radio daemons are allowed only in this
route; boot-control and protected fstab/hw-bin guards remain. The normal
rollback/health/readback procedure is unchanged.

For a bounded held boot, use the existing `--hold` coordinator mechanism in one
pane, and the staged target operations below in another. Do not open E Ink
gates merely for the radio trial. Never restart SurfaceFlinger or adbd.

## Preliminary production boot, MPSS offline throughout

The user supplied the following cached ARM64 static-musl tools. Exact bytes,
ELF64/AArch64 identity, absence of PT_INTERP/DT_NEEDED and strace exit-kill
support are verified offline; neither executable was run.

| Tool | Version | Bytes | SHA256 |
| --- | --- | --- | --- |
| strace | 6.16 | 1793648 | `1d33daed173360a7d7726fc37ef1a2af958a8493ea36644f29c2b8f25bdbf82e` |
| iw | 6.17 | 643912 | `6a6951202b0e1120e7ba05fc57d7faec60790ede5614b5477c79fa386002703b` |

`coordinator-tools.lock.json` records the immutable Nix-store paths and the
aliases `~/A9-build/tools/arm64-static-{strace,iw}/bin/{strace,iw}`. Strace's
cached derivation identifies `/nix/store/k5db1n20pfd9rscjm8lw513bkyv697cf-strace-6.16.tar.xz`,
SHA256 `3d7aee7e4f044b2f67f3d51a8a76eda18076e9fb2774de54ac351d777d4ebffa`.
Reading that actual source established the help and option-table entry,
`opt_kill_on_exit=true`, attach/kernel checks and assignment of
`PTRACE_O_EXITKILL` in `src/strace.c:339,2388,2591,3045–3050`; the manpage documents
it at `doc/strace.1.in:525`. The built binary also contains the corresponding
help, parser option and attach-incompatibility diagnostic. No ARM64 `--help`
or `--version` execution was used to obtain this evidence.

`python3 -I scripts/mainline/wifi/stage-tools.py --check` verifies these cached
inputs without writes. `--out <new-private-directory>` copies only the two
verified tools, current coordinator/awk source, lock and `SHA256SUMS`. It rejects
existing output, symlink traversal, git/source trees, Nix inputs and golden
storage. Nothing is fetched, executed or deployed. The runtime controller also
requires these literal strace/iw pins: choosing an arbitrary hash argument does
not admit a different tool. Missing, changed or incompatible inputs **stop
before MPSS**; there is no weaker fallback.
The pinned-source-built `/vendor/bin/qrtr-lookup` is included in the radio
inventory. Target `/system/bin/runcon`, `timeout`, `nohup`, `awk`, `stat`, `cmp`
and the required procfs visibility must work; absent tooling is a gate failure.

Android mounts `/mnt` with `noexec`. Before copying any tools on a fresh boot,
the coordinator creates a separate executable tmpfs at the tools directory:

```sh
mkdir -p /mnt/a9-wifi/tools
chmod 0700 /mnt/a9-wifi /mnt/a9-wifi/tools
mount -t tmpfs -o nosuid,nodev,size=8m,mode=0700 a9-tools /mnt/a9-wifi/tools
```

Do this once, before copying; overmounting an existing populated directory
hides its files. Keep `/mnt` and the evidence/helper roots `noexec`. Copy the
reviewed strace/iw binaries, `coordinator-target.sh` and `audit-opens.awk` onto
the new tools mount and set binary modes to0700 and source modes to0600.
The controller verifies an exact writable tmpfs mount at the tools path with
`nosuid,nodev` and without `noexec` before invoking strace. No remount of the
Android parent is needed.

The files must have no symlinks or multiply-linked files.
Both `/mnt/a9-wifi` and `tools` must be root0700;
no shell-writable ancestor can rename the trusted inputs. Trial evidence gets a
fresh noswap tmpfs at `/mnt/a9-wifi/trial`. `/data` must be the existing ext4
`/dev/block/ram0` mount, verified as block major1/minor0; it is not tmpfs. Execute through the established coordinator
root channel, without restarting adbd. Keep
`persist.vendor.a9ml.wifi.services` unset or0. Current init contains only disabled
companion definitions and no persistent-property start triggers. The controller
rejects a stale legacy value1 so an older trial configuration is reviewed and
cleared explicitly. The manual worker uses `runcon u:r:a9_wifi_radio:s0` and the same
production native executable as init, with write-only pipe logging.

```
/system/bin/sh /mnt/a9-wifi/tools/coordinator-target.sh \
    prepare <reviewed-strace-sha256> <reviewed-iw-sha256> <reviewed-audit-awk-sha256>
```

An EXIT/signal trap is armed before launching the first worker. A failed
prepare proves MPSS offline, cancels late worker launches, and tears down the
workers it started, preserving the fault and all evidence. A successful
prepare leaves the helpers ready for review. `abort` before a watchdog exists
cancels an in-progress prepare and synchronously stops prepared helpers.
Teardown freezes each recorded tracer, checks its start time to reject a
recycled PID, kills its verified direct child, then kills the tracer with SIGKILL;
the pinned strace's `--kill-on-exit` also removes its remaining tracees.
Normal teardown uses the cached tracer/helper PID and start times, checking
parentage and start time again after freezing. Partial-prepare cleanup, before
a helper has passed full observation, retains full child enumeration. A bad
cache or recycled/reparented child is never treated as permission to kill it.

Process discovery uses PPIDs from `/proc/[0-9]*/stat`, parsing past the final
`)` in comm, and requires exactly one child with the expected executable.
The full offline observation saves that binding in each role's `.identity`
file. This host-wide discovery is not repeated during the guarded window.
It needs neither `ps` column layouts nor `CONFIG_PROC_CHILDREN`, which is off
in the validated Wi-Fi snapshot. The kernel and images need no rebuild for
these controller-only changes. A fresh source/tool bundle is required.

Preparation resolves the **6080000.remoteproc SM6115 PAS device**, checks the
exact modem filename, requires offline, disables recovery and reads it back.
No remoteproc index or SDM660 address is guessed. Each production helper checks
the fixed golden seed hashes/sizes/ownership, transport client/base/size and
character-device identity, creates fresh noswap RAM and applies the native
boundary before serving. It cannot use live stores as a seed fallback.

Before `prepare` returns it requires native QRTR readiness, actual local
nameserver rows4097/0,2,4 on the node discovered by DIAG at runtime, and complete
helper-to-daemon lifetime pathname traces. The strict AWK audit rejects live
block/persist paths or block FDs, paths outside preparation's exact allowlist,
missing pivot/detach/filter/exec evidence and **every successful post-exec open**.
It accepts the pinned strace's `AT_FDCWD</>` and nested character-device FD
annotations and aligned syscall results. The six exact Bionic property files
observed in the offline target traces are admitted read-only before pivot;
successful property opens after pivot or exec, writes and unreviewed names fail.
It handles literal nontruncated traces only and fails closed on split relevant
syscalls. It never traces read/write/recv/send buffers containing private data.

The procfs verifier supplements this with production UID/GID65534, no groups or
capabilities, NNP+seccomp, correct SELinux domain, detached noswap/noexec/nodev/
nosuid root, no dev/proc/sys, no executable FD30, no block FD and exact regular/
character descriptor roles. A post-hoc FD snapshot alone is not a lifetime-open
proof. The claim covers traced trusted Linux helpers/daemons, not opaque MPSS/TZ
or every process on the phone. The separately authorized coordinator store
reads below deliberately occur outside those process traces.

Review and preserve this private evidence, run harmless negative checks under
the production boundary, exercise the offline controller/abort path, then
`stop` while MPSS remains offline. A failing security/readiness/provenance check
does not permit a weaker mode. The first active trial uses a **fresh boot and
fresh golden-seeded RAM**, with the reviewed preliminary evidence digest recorded.

## First active trial: capture identity, without a scan

After a new `prepare`, execute exactly once:

```
/system/bin/sh /mnt/a9-wifi/tools/coordinator-target.sh run \
    I_AUTHORIZE_ONE_MPSS_START I_ACCEPT_UNPROVEN_MPSS_TZ_AND_CELLULAR_RF \
    <sha256-of-reviewed-preliminary-production-evidence>
```

The acknowledgement records the user's explicit test exception, not a claim of
proven modem isolation. Review RF conditions, thermal/power monitoring, shared
regulator consumers and the stock A9 calibration/BDF before issuing it. No SIM,
airplane mode, missing RIL or a sandbox is proof of no cellular RF activity.
RF and thermal assessment remain operator responsibilities: the controller does
not implement a thermal sensor or RF monitor. This capture-only run starts MPSS
and is therefore RF-capable, even though it never requests link-up or a scan.

The first run captures the actual ath10k QMI chip/family/board/SOC IDs, firmware
version, build timestamp and build ID, together with the stable platform-device
prefix. It rejects missing, repeated, ambiguous or malformed identity records
and the chip/board `0xff` missing-field defaults. Family/SOC zero is permitted;
there is no invented SKU or numeric chip/board allowlist. The canonical record
excludes variable printk timestamps, retains bounded firmware metadata and is
hashed into a private `A9_WIFI_IDENTITY_SHA256` stdout record. Preserve that
record and `identity.txt` with the other private evidence. The controller then
observes at least180 healthy seconds and stops without running `ip link` or `iw`.

Review this identity and the complete private boot evidence offline before
authorizing a scan. The fingerprint binds a later trial's observed identity and
firmware to that review; it does not by itself establish board compatibility.
The fixed staged vendor BDF bytes and their audited provenance supply the
calibration identity. Do not require a `board_file` API/CRC boot message: ath10k's
NON_BMI SNOC route omits that BMI-specific message. An API1 raw-board fallback
alone is not evidence of failure.

Only after that review, boot afresh and prepare new golden-seeded RAM for a
second single-start trial. Add `--scan-reviewed <private-reviewed-identity-sha256>`
to `run`; optionally also add `--android` for the separately reviewed Android
payload. Both options belong to `run`. Its root-private RAM gate must match this
trial's canonical identity before any link-up/scan. A standalone `check` accepts
no digest argument, and each trial allows at most one scan attempt. A mismatch
ends the trial and stops MPSS. `--android` without `--scan-reviewed` is rejected
before activation; stale, capture-only or incomplete-scan evidence cannot enable
the Android stage.

Kernel evidence is captured with a10second bound before identity review, again
immediately before any reviewed scan, and after the observation window. The
source-grounded rejection covers watchdog/starvation/crash failures and ath10k
failure/timeout, rejected capability request, invalid board magic, firmware-fetch
and core-initialization diagnostics. Missing or unassessable evidence fails
closed. These checks complement the independent lifecycle watchdog; they do not
turn an unproved chip/BDF combination into an approved one.

Only this coordinator, per the user's R3 exception, takes **read-only** pre/post
copies of `/dev/block/by-name/{modemst1,modemst2,fsg,fsc}` into a separate root-only
noswap tmpfs. It first verifies the source is a block partition with its exact whole-partition
size from sysfs, then compares all four SHA256s after MPSS is proven offline.
Before activation it emits `PRE_STORE_SHA256` records. The coordinator MUST
stream stdout/stderr to a root-private0700/0600 host log outside git and
`$A9_DATA`, so baseline hashes survive an emergency reset. Treat these hashes
as private identifiers; publish only equal/changed/incomplete outcomes. Any
change is a hard failure and is never repaired/restored/zeroed/written back.
It does not open persist. Comparison detects observed changes, not transient
writes later reversed, flash-controller activity or every secure-world path.
If reset prevents post-capture, comparison is incomplete and the trial fails.

The controller arms two independent target processes before the sole explicit
state write: a fault/death/crash/total-deadline watchdog and a hard-deadline
bootloader-reset guardian. Startup has30seconds; the actual post-start DIAG
feature/control response handshake has30seconds; PD64/WLFW69 readiness has30seconds;
wlan0 has30seconds; an explicitly reviewed
nl80211 scan has25seconds. It requires valid private DT MAC equality without
printing the address and observes MPSS healthy for at least180seconds since
running, beyond A6L's approximately100second DIAG starvation horizon. PD/WLFW service visibility is independently monitored after readiness; one
failed bounded lookup ends the trial (each lookup has a5second cap). Total
active trial is capped at240seconds. Stop has20seconds, with a270second hard
guardian if normal control/monitoring does not complete. At240seconds the hard
guardian launches its orderly-stop attempt in a separate child with redirected
stdio and **never waits for it**. The guardian retains the270second reset path
even if that child's sysfs write or timeout wait blocks. A completed trial skips
the stop child and disarms reset; failing to record a deadline fault/PID cannot
suppress reset. These are software
deadlines: a wedged kernel/secure world can defeat software reset requests.

### Audit placement and pre-start budget

`run` repeats full confinement observation and trace auditing while MPSS is
still offline, before reading the pre-store copies and arming the monitors.
This work can take time proportional to the number of Android processes. It
does not consume the guarded active window. Prepare still performs its full
audit for the coordinator's preliminary review. Capture must already be live
before `run`; `A9_WIFI_TIMING` records expose offline-audit, arming, final
freshness, offline-stop, teardown and post-store completion times.

Immediately before the sole start, retain these checks:

| Check | Why it must be fresh |
|---|---|
| Exact MPSS device/firmware, offline state and recovery disabled | Refuse a changed target, an intervening start, or recovery being re-enabled. |
| Cached tracer/helper PID, start time, parentage and executable | Detect death, PID reuse, reparenting or an executable mismatch since the full proof. |
| Nonempty trace, READY log, no helper exit or fault marker | Detect loss of the observed logging/serving process; unreadable logs also fail. |
| Actual local DIAG4097/0,2,4 lookup | Nameserver visibility can change even while the helper remains alive. Repeat process freshness after this bounded lookup. |
| Live watchdog/guardian command identities, no fault/abort/complete, prepared stage and unused start latch | A stale readiness file cannot authorize activation. Recheck monitors after other work. |
| Less than15 integer seconds since the pre-arming timestamp | Refuse a slow final check before touching the start latch. Never re-arm or renew either deadline. |

Cached credentials, caps, NNP/seccomp, mount/root isolation and admitted FD
proofs are safe to reuse only for those verified process lifetimes: the native
default-deny policy forbids fork/clone, executable replacement, credential/cap
changes, mount/namespace changes and pathname opens; the initial executable FD
is closed and admitted writable RAM is noexec. QRTR cannot deliver new file
descriptors. No untrusted helper can replace the root-owned identity files.
The trusted coordinator/strace and kernel remain part of the existing boundary.
The full trace audit is repeated after MPSS is offline; forbidden attempts are
still evidence failures even when the native boundary denied them.

Running `check`/Android stages use process/log freshness rather than repeating
host-wide/FD/trace audits. Identity, scan eligibility, live service checks,
180-second observation and abort checks remain. Stop proves MPSS offline,
audits the trace, tears down helpers using verified identities, then captures
the read-only post-store comparison. `complete` is still written only after
all those steps, and the guardian remains armed until then. The240/270second
deadlines, stop timeout and fallback are unchanged.
The established coordinator's recovery ladder remains the hardware fallback.

`--android` activates the separately reviewed matching vendor payload only after
this trial's reviewed direct driver scan has completed. Its explicit nonpersistent property starts HAL and
supplicant; it checks bounded service readiness and saves framework evidence.
It makes no association, internet, tethering, AP or setup-wizard success claim.
Hostapd's AP gate is not enabled. A successful empty kernel scan is recorded as
such. Android process liveness is narrower than usable framework functionality.

Unknown/denied requests, parser/range errors, short I/O/sync errors, TFTP expiry,
daemon/tracer death, absent DIAG handshake, stalled progress, modem crash,
unexpected storage capability, thermal/power/RF anomaly, lost abort channel or
any deadline ends the trial. There is no retry/reseed/restart/remoteproc crash
recovery. Operator abort in a second root channel is:

```
/system/bin/sh /mnt/a9-wifi/tools/coordinator-target.sh abort
```

Surviving support and RAM stay alive during stop. Framework services are gated
off; MPSS must be offline before post-copy comparison and companion teardown.
The watchdog retires normally only after both a stop request and an actual
offline state read, so intentional companion/tracer exits do not manufacture a
fault. It still treats companion death before offline as a failure. Existing
fault evidence is retained; retirement does not mark the trial complete or
disarm the independent guardian. Losing modem identity/state during a stop
requests the reset fallback even if writing the fault log fails.
If bounded stop fails, independently request reboot to bootloader; do not
discard backing RAM beneath a running modem or attempt compatibility by exposing
real storage. Preserve private evidence through the coordinator's existing
channel before reset where possible. Only the normal slot-B boot/vendor
rollback is used; there is no calibration-store rollback in this script.

## Offline test scope

`python3 -I tests/mainline-wifi/coordinator.py` executes the actual staged shell
functions/branches against temporary fake sysfs/store files and fake identity/
reset commands. The production script has no environment test-path bypass.
Fixtures test failed preparation, parser/short-I/O/stall fault markers, companion
death, operator abort, crash/deadline, bounded stop failure/reset fallback,
independent guardian/disarming, readonly four-store comparison/change rejection,
and the strict trace allowlist. Synthetic markers exercise controller response;
native parser/backend tests separately prove those faults are emitted.
Shutdown regressions cover intentional post-offline teardown, companion death
while a requested stop is still running, preserved earlier failures, lost modem
identity with failed fault logging, a deliberately blocked orderly-stop child
while the independent hard guardian still requests reset before fixture release,
and rejected failed/empty/malformed hash
results. Snapshot digests require successful hashing and64 lowercase hex digits.
Identity regressions execute the actual shell/awk parser with valid and malformed
synthetic QMI records, altered printk timestamps and joined log lines. They
verify missing/default/ambiguous identity rejection, capture-only behavior,
digest mismatch preventing link-up/scan, exactly one matching reviewed scan,
pre-scan kernel fault rejection, standalone-gate rejection and stale Android
evidence rejection. A directory in place of the fault log proves failed logging
cannot prevent the watchdog's required MPSS stop; signal/controller-failure
logging is also best effort before stop. An unwritable abort marker triggers
direct MPSS stop and returns failure. Property-service failure while disabling
Android cannot suppress MPSS stop, and its failure status survives teardown
even if the fault log is unwritable. Actual-shell fixtures cover both cases.
`tests/mainline-wifi/stage-tools.py` reads the actual cached tools/source archive
and tests exact private staging, lock/runtime pin agreement, binary/source pin
tampering, ELF interpreter/dependency rejection, protected-output rejection and
refusal to overwrite existing output. It executes no target executable.
`tests/mainline-wifi/verify-images.py` tests exact provenance and tampering of
patch records, binary/module/DT/DTBO content, fstab and effective cmdline.
These checks never execute a device transport or ARM64 executable. Target
production preparation, QRTR/DIAG exchange, actual scan, reset/abort efficacy,
RF behavior and real-store equality remain unobserved until the coordinator's
future test.

Credit: PaddleStroke's Hisense A6L mainline project at
`ede802bcb12d3c8854b8ed0e147d6477d0ea0f9d` demonstrated disposable EFS support
and DIAG-before-MPSS ordering. This SM6115 port uses PAS4 rather than SDM660's
MBA/q6v5_mss route and preserves stricter private seeds, noswap and confinement.
