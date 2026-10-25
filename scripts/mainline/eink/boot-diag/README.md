# A9 MSM boot-hang diagnostics

## Current PG policy: stock telemetry only

The user's latest instruction supersedes the historical settle/quota discussion
below. Stock PMIC active_mode at0xffffff8008f1ed50 logs PG without using it to
reject power-up. Its PG getter is0xffffff8008f1ea18. Stock composer update loop
ELF0x1a4c74 has no PG retry/quota; its PanelOpen delay uses the time since
panel-off, not PG. See the address-cited report under runs/pg-stock-20261024.md.

Mainline now samples PG only for diagnostics: no PG enable rejection, bounded
qualification, recovered-dip quota, storm/rate latch or PG-driven re-powering.
PG low, absent and unreadable values cannot change update success. Non-PG
device/API failures and the separate emergency, temperature, HV/time and
transport controls retain their existing behavior. Waveform-end PG counts,
histograms, shutdown traces, bounded VCOM readback and persistent fault context
remain. `pg-observe` logs the completed update's mode/bin/temperatures/rectangle;
the logger cannot trigger containment or change the update result.

Integ26 needs **both** pieces: rebuild the kernel with0065 stock-PG PMIC patch
(`build14-kernel.py` applies it and recompiles PMIC), and replace a9_eink.ko.
`pg-payload.py` builds the module and a private ARM64 PMIC object, checks symbol
versions, and pins sources. `build14.py` uses the verified stage (default
agent-pg-stock-20261024/stage4) and rejects an old kernel receipt. The
opener verifies read-only policy ID1 in both kernel and module before opening
the existing gates. This prevents an inherited quota-bearing component.

Host replay: `test-pg-stock.py --stage STAGE --out NEW_DIR` (HOSTCC may select
the compiler). It compiles the production decision and PMIC-enable functions,
including100000 low/unreadable/absent PG samples and independent-error controls.
`test-rail-context.py` retains the synthetic snapshot checks. No phone run or
physical reliability qualification is implied by these tests.

Boot 102 stopped after the UBWC success line, before MSM insertion returned.
The loader now brackets `msm.ko` insertion with `MSM_INSMOD begin/end rc=N`.
Patch 0063 emits `A9_MSM_TRACE phase=... rc=N` only on the A9 compatible:
MSM init registrations, Adreno probe/bind, GPU IRQ/clock/regulator/VM setup,
and DPU probe stages. Existing kmsg timestamps supply timing. No global
initcall/debug tracing, addresses, per-frame/IRQ logging, new module arguments,
or changes to provider order, error handling, IRQ/power operations are added.
A missing end marker localizes an operation; it does not prove its cause.
Registration wrappers return void: their end rc=0 means the call returned,
not that every deferred probe succeeded. Probe markers carry actual results.

The early logger writes each critical record then immediately fsyncs its
kernel log on the existing kdebuginfo filesystem. Normal logging retains its
periodic flush, size and retention limits. At deadline expiry, the existing
worker additionally requests a non-panic EMERG kmsg dump after its reset
backstop is armed. Ramoops max-reason is 3 to accept EMERG. Its inherited
address, size, record/console/pmsg offsets are unchanged. The 300 s deadline,
hardware bite, shutdown wait and reset route are unchanged. Pstore retention
is best effort: firmware may clear RAM, and no software logger can guarantee
progress through an all-CPU/storage hang. No new raw partition writes exist.

Stock uses built-in KGSL/GPUCC/DISPCC rather than mainline MSM. The integ13
DT/config/module-list supplier-order check already passes. A GDSC being off
is normal idle behavior, so no forced-power wait, sleep or speculative IRQ
mitigation is justified by the saved evidence. These diagnostics should first
separate registration, IRQ and provider-acquisition stalls.

Future integrated packaging must rebuild the MSM module with patch 0063,
compile the current deadline implementation into the kernel, rebuild the
ramdisk logger, refresh the loader in vendor and run `observable_boot` on the
DT so max-reason=3 is included. A runtime-hook-only refresh or reuse of a
cached boot/MSM module will omit diagnostics. Keep existing DPU/GPU patches,
module symbol-version checks and reset safeguards. This task builds no image.

Host checks: fake-sysfs loader rehearsal (including unchanged module order),
actual Android mksh marker success/failure controls, clean patch application
with existing MSM patches, ARM64 compilation of four affected MSM objects and
both deadline variants, native and ARM64-musl logger compilation, and the
regular-file logger test below. Hardware hang localization/persistence remains
unqualified until an owned run.

```
cc -O2 -Wall -Wextra -Wno-unused-parameter test-logger.c -Wl,--wrap=fsync -o /tmp/a9-test-logger
/tmp/a9-test-logger
```

The test never calls the real logger entry point, mounts storage, or contacts
a device. It checks full-record write before immediate sync, kernel-only
marker selection, and continued logging after sync failure.

INTEG20 boot119 reboot attribution: the logger also immediately persists init's
`sys.powerctl` requester, internal shutdown command, critical-service failure,
`reboot_on_failure`, fatal signal and reboot-start records, plus the existing
deadman-firing and shutdown-trigger messages. These specific markers apply to
both kmsg and logcat; ordinary reboot mentions retain periodic flushing.
The regular-file test covers both streams, write-before-sync and bounded
matching. This closes the two-second buffered-evidence window; it does not
identify an absent historical requester or guarantee storage survives a reset.
Rebuild the ramdisk logger and companion policy in the next image (build14
already does this). Boot119 lacked the rootfs-read correction05c5f81 as well.
See [the line-cited investigation](../../../../runs/integ20-reboot-20261024.md).

## Five-second insertion snapshot

The integ20–25 comparison found eight completed MSM insertions, all below
0.51 seconds, and one pre-insertion logger cutoff. Integ13 boot102 still has
no stack establishing an ordering or IRQ race. The loader now starts a
read-only sidecar around MSM insertion. After five seconds, if the exact
child remains alive with the same `/proc/stat` start time, it records its
state, wait channel and at most twelve kernel stack symbol names. Addresses,
offsets, process names and PIDs are omitted. Unreadable stack data is marked
unavailable. These `MSM_INSMOD` records already receive immediate logger
fsync. Combine them with the last paired `A9_MSM_TRACE` phase to distinguish
an insertion wait from driver registration, IRQ, clock or component binding.

This captures once and never signals, retries or unloads the insertion.
The parent waits for its original return code and continues the same module
order. The sidecar sleeps once and exits; init owns its service process group.
The existing setup timeout, independent boot deadline and panel fault latches
are unchanged. Capture still needs a runnable CPU and readable procfs; an
all-CPU/storage hang may leave only the existing paired phase markers/pstore.
Refresh the vendor loader from main for the next image (`build14.py` already
copies it). This addition needs no new MSM or kernel build beyond the existing
0063 trace and logger fixes.

`analyze.py KERNEL_LOG [KERNEL_LOG ...]` reads saved logger files offline and
emits numeric milestones, line references, warning counts and pending phases.
Identical files are deduplicated by hash. Raw messages and command lines are
never emitted. Rail-latch counts exclude shell echoes and repeated refusals.
DRM card initialization and dry-run commits are not physical refresh proof:
power-on and the first waveform-end record only bound a completed drive.

Host check: `python3 -B scripts/mainline/eink/boot-diag/test-boot-diag.py`.
Use `--shell ANDROID_SH_WRAPPER` for Android mksh/Toybox under QEMU. It checks
analysis, bounded/sanitized capture, PID reuse/unavailable data and a slow
fake insertion with unchanged arguments/return code. The existing
`scripts/mainline/android/tests/test-eink-setup.sh` checks card classification,
module order and insertion errors with `A9ML_TEST_SHELL` selecting that shell.

## Rail-fault context

The three driven boots in integ20–25 all latched on the fourth waveform-end
PG dip of an HV session. Their first three dips recovered in25–48ms; the
snapshots show session budget3, one skipped qualification, zero timeouts and
no rate-bucket skip. A rate burst64 does not override the separate session
budget. The fourth dip's natural recovery is unobserved because containment
immediately removes power. Stock PMIC routines do not qualify/count PG dips;
that alone does not establish that ignoring these mainline dips is sound.
See [the stock-address and per-boot investigation](../../../../runs/integ20-25-einkrails-20261024.md).

The existing bounded fault snapshot now persists the numeric `last` status row
(mode/clear/bin/temperatures/rectangle/timing), independent PG limits, four
fixed-label cached frontlight levels and selected init Wi-Fi service states
to kmsg. Previously, mode/bin were kept only in the RAM bundle and lost on
reboot. At a waveform-end latch, `last_update` identifies that completed
waveform; for an earlier failure it may identify the previous update. Missing
or malformed values become unavailable. Light levels are cached software
values; service liveness does not establish association or traffic. No I2C
access, gate, latch or recovery behavior is added. The existing three-bundle
limit applies. At that revision the opener reported session budget/boot cap separately
from rate burst. The current PG policy above supersedes those controls and
requires a rebuilt kernel and E Ink module.

Run `python3 -B scripts/mainline/eink/boot-diag/test-rail-context.py`, optionally
with `--shell ANDROID_SH_WRAPPER` for Android mksh/awk/Toybox under QEMU.
Three groups check numeric context, missing/malformed values, unchanged sysfs
contents and the inherited snapshot limit.

## Kernel reader across Android policy load (INTEG19)

Boot 118 retained kernel records only through 5.085742 seconds, ending at the
policy-capability lines, while the same logger kept writing and rotating
logcat. This rules out logger death or a full output store as the explanation
for that cutoff. There is no retained read errno from the old implementation.

The early-ring change moved the reader from private tmpfs `/kmsg` to the
original rootfs `/a9-log-kmsg`. Both initial descriptors were opened before
`adopt_android_domain()`: the kernel assigns pre-policy execs SID_INIT, which
Android maps to unlabeled at policy load. The companion policy permits
kernel-owned descriptors and tmpfs character reads, but grants neither
unlabeled-fd use nor rootfs character reads. SELinux revalidates an already-open
file when credentials or the policy sequence change. The old read loop silently
ignored the resulting error; keeping the fd open did not preserve permission.
EPIPE handling and the early-ring buffer do not repair this access mismatch.

The logger now opens both private tmpfs descriptors after SID_KERNEL adoption.
The existing kernel-fd use rule covers the subsequent diagnostic-domain
transition. The companion policy adds write to the existing private tmpfs
character-device grants for kernel and a9ml_rootsh; no unlabeled-fd use or
permissive domain is added. A record-sequence cursor skips the replay from a
new kmsg open without SEEK_END's record-loss window. Permission errors retry
the existing domain handoff; changed persistent read errors are saved directly
to logcat instead of relying on the possibly broken kmsg writer.

Run the host regression:

```
HOSTCC=cc python3 -I scripts/mainline/boot-deadline/test-kmsg-follow.py --out NEW_HOST_DIR
```

Four groups exercise production helpers with synthetic descriptors and actual
compiled TE/MLS permissions: original descriptor denial at simulated policy
load, permitted private descriptors, continued wrapped-key/native-crypto/RPMB/
Venus capture without duplicate or lost handoff records, and EINTR/EPIPE recovery.
The pinned host libsepol 3.8 predates Android's FunctionFS capability, so only
the host replay omits that unrelated flag; a full production policy binary is
compiled and retained separately. All TE/MLS rules remain in the replay.
The test never calls logger_main, opens a device or mounts storage.

Host evidence: `/home/user/A9-build/mainline/logger-follow-host-20261024/`.
Four replay groups, existing regular-file logger tests, unchanged logger-header
Android-shell replay, and ARM64 static compilation with warnings as errors pass.
Phone validation remains pending. The next image must rebuild **both** the
ramdisk logger and companion vendor policy; existing images remain unchanged.


## INTEG20 retained rootfs read denial

Boot 119 again stopped `kernel.log` at 5.085742 seconds. Its retained logcat
contains 8,030 read denials for logger PID 146 against rootfs character inode
20, `/a9-log-kmsg (deleted)`, in `a9ml_rootsh`. The process remained alive;
ActivityManager CPU samples reached 75–91%. The INTEG20 ramdisk contains the
rebuilt e57a00d logger, but the earlier host fixture only modeled a private
tmpfs inode. That was insufficient coverage of the observed inode label.

Grant rootfs character **read** alongside existing open/getattr/write for the
kernel and diagnostic identities. Keep the private descriptor handoff and
sequence cursor; do not permit original unlabeled descriptors. If a persistent
read error still occurs, pause 500 ms before retrying: a pending unread kmsg
record can keep poll immediately ready despite read returning EACCES. Avoid a
second permission-error read once the diagnostic-domain handoff is complete.

The four existing replay groups now cover both tmpfs and the observed rootfs
label, continued post-policy crypto/RPMB/Venus records, denied original INIT
fd use, and retry backoff. Evidence and the boot-observer investigation:
[`runs/integ20-followup-20261024.md`](../../../../runs/integ20-followup-20261024.md).
Actual device validation remains pending.
