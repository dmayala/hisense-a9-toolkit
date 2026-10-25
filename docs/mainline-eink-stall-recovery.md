# E Ink starvation-stall recovery

The frame-progress interlock in `a9_eink_transport.c` still cuts HV after three
frame periods (about 35.3 ms at 85 Hz) without confirmed drive progress. An
observed `max_gap_us` near three seconds is lateness, not the configured deadline.
An idle UI with no waveform in flight does not arm this timer.

The 2026-10-20 demo capture starts after the original event. It shows repeated
frame-progress-latch refusals; it does not establish audioserver starvation as
the cause or identify the original commit phase.

A timer expiration **between completed commits** now records `kind 1` and tags
the PMIC emergency as starvation. R3 records an in-flight timer as `kind 3`, pending: only the interrupted call's
raw return zero within one second of cutoff can qualify it for later recovery.
A real commit/DPMS error, failed neutral, and other emergency paths stay hard
(`kind 2`). Real transport errors are sticky and cannot be overwritten by a
racing timer or a later successful shutdown. A commit sequence binds the raw
return to the interrupted call so cleanup cannot supply the proof. These
are eligibility classes, not proof of an electrically healthy panel.

Both classes retain the original emergency-off sequence and block drive. The
vendor watcher may request recovery of kind 1 or qualified kind 3 after closing all three gates and
waiting five seconds. A second successful grant waits thirty seconds. The kernel
limits grants to two per boot; manual `clear_fault` does not refill that budget.
The sole E Ink worker processes the request after the producer has joined. It
requires a successful neutral, completed transport shutdown, no unknown HV state,
no rail latch, and a tagged PMIC starvation latch with no pending emergency work,
no max-on fault, and PG/PWRUP/VCOM-EN all low. R3 also requires no MSM/DPU
completion-error latch: `wait_flush` returns void and otherwise discards genuine
DPU wait failures, so outer DRM return zero is insufficient alone. This
read-only MSM boot latch has no recovery clear API. Recovery clears only that tag and
the E Ink stall latch, leaves gates closed, and owes a full GC16 redraw. The
normal opener then repeats waveform/template, temperature/bin and power admission.
A close during backoff or admission cancels automatic reopening.

Electrical/PG/temperature checks and hard emergency/max-on latches remain stops.
Normal regulator disable no longer acknowledges the max-on latch; explicit
manual FP9929A `clear_fault` is required for it. A hard emergency overrides the
starvation tag, including overlapping calls. Recovery never writes either
manual `clear_fault` parameter. Refusal or exhausted budget leaves gates closed.

The root-only connector attributes are:

- `stall_recovery`: `kind`, successful `retries`, `limit`, `backoff_ms`, last
  `result`, and live `pmic_hard` summary.
- `stall_snapshot`: first-event phase/stage/timing frozen by the hardirq before
  cleanup can replace the live profile; latest raw return and hard-error state.
- `recover_stall`: write `1` to request the bounded worker operation; never
  opens gates. Missing on older kernels, which therefore cannot auto-recover.

The old development reopen snippet cleared only `a9_eink.clear_fault`; the same
stall also latched FP9929A emergency-off. Independently, the vendor opener scanned
whole-boot fault messages and cumulative aborts, so old evidence could refuse
opening before any gate write. `NNN` is the readback of those three closed bool
gates. The replacement `scripts/mainline/eink/stall-recovery/dev-reopen.sh` invokes
`sh /vendor/bin/a9ml-eink.sh recover`. It works for a typed starvation event even
if the operator already cleared only the a9 stall latch. Do not clear the PMIC
tag first: the recovery path requires it to distinguish the cause. Hard events
are deliberately refused.

After a successful kernel grant only, the helper records a monotonic log floor
and counter baselines. Historical logs/counters remain intact; new errors still
stop the session. The generic severity watch has its own session-start timestamp
so benign boot-time template-load errors do not close an admitted session.

R12's `glass-proof.sh` now treats `vendor.a9ml.eink.gate` as diagnostic. All three
actual module gate readbacks, empty fault property, positive real driver-update
and log deltas, and unchanged fault counters are still required. A driver pass
still requires human confirmation that the glass changed.

Host checks use the actual transport and extracted unchanged recovery function
bodies. Run the focused transport cases with
`tests/mainline-a9eink-stall/run-from-repo.sh --stall-recovery`, the recovery
policy test against the patched PMIC source with
`scripts/mainline/eink/stall-recovery/test-recovery.py <fp9929a.c>`, and the
Android tests `test-eink-gates.sh`, `test-stall-recovery.sh`, `test-glass-gates.sh`.
The new helper/proof tests were also run with cached Android mksh under QEMU.
The broad transport suite hit timing failures in cases 17/28 on this loaded
host; only the focused changed-path suite is claimed passing. The real FIFO
contention test was skipped because CAP_SYS_NICE was unavailable.

FP9929A is built into the demo kernel. `stall-recovery/build.py` therefore builds
a private kernel, packages a new boot image preserving m's non-kernel blobs and
command line, builds the E Ink module against the new exports, and changes only
that module, the helper and two kernel-hash marker files in the camera-preview
vendor base. Existing built-in API CRCs, config and kernel release are checked.
Every build/packaging subprocess requires rc=0; final files are read back and
the vendor filesystem checked. Build from a committed worktree with explicit
`--out`, `--scratch`, and `--vendor`; the local artifact report holds exact pins.
All phone testing for this change belongs to the user.

R3 diagnosis: the UI-only r2 run reproduced without audioserver failure. The
3,007,948 us global gap was present before the event; it measures between-commit
wall time, including neutral idle, not one blocked call or HV exposure. Global
late/vblank skips include that idle. Commit maximum 372,658 us is cumulative and
includes full cold modesets (panel/PMIC/bridge setup); drive maximum 81,529 us can
include neutral cleanup after cutoff. The saved 400-character lines omit the
phase fields, so no specific lock/fence/vblank wait is established. Steady
primary-FB-only flips disallow modesets, so they normally do not execute panel/
TC358767 I2C setup; their path includes DRM locks/prepare/fences/dependencies,
MSM prior-flush/CRTC lock, programming, post-flush/vblank and cleanup waits.
R3 does not claim to have fixed the latency root cause or reproduced stock
recovery semantics.

On any watched fault, the vendor now closes gates first, then saves full dmesg,
full status, frozen stall snapshot, properties/gates, CPU/IO/memory pressure and
selected kernel-task stacks under `/data/local/tmp/a9ml-eink-faults/`. Files are
private (umask077), at most three timestamped bundles per boot, captured before
backoff/recovery. Keep the whole bundle from the next run; tail-only dmesg and
400-character status lines discard the distinguishing evidence. `stage 1` means
client-buffer lock; stage2 is a profiled blocking flip; stage3 is full modeset.
Phase0 prepare/locks,1 fences,2 dependencies,3 prior-flush/CRTC-lock,4 programming,
5 post-flush completion,6 cleanup,7 done. Frozen fields describe the first event;
latest return/error fields may describe a later event. DPU latch tests and
late-success/frozen-profile/failed-DPMS host replays are included in R3.

## R4: complete the userspace reopening path

The supplied r3 soak records a successful kernel grant (`kind0 retries1 result0`)
followed by closed gates. Updates then rise while frames remain3078 and dry=1;
that is computation, not scanout. The original fault bundle was lost on reboot,
so the exact first admission refusal is not recoverable from that evidence.

The shipped helper nevertheless has a reproducible reopen defect: after checking
waveform=1/template=1 it requires the original "control template loaded" printk.
That line can rotate out of dmesg. A production-helper replay reproduces grant,
kind reset to0, admission refusal and faulted gates under both host sh and the
actual Android mksh. R4 uses the kernel's loaded status instead of the historical
log prerequisite. Firmware validation, temperature/bin admission, hard latches,
three gate readbacks and the two-grant/backoff budget remain in force.

R4 checks the synchronous grant receipt (kind0, result0, retries+1), logs each
recovery stage and preserves bounded nonempty refusal details. It then requests
the owed clear after opening, and requires both transport-frame and completed
non-dry update counters to advance, with no new fault, before reporting recovery.
The bounded40half-second polls do not mistake dry updates for physical progress.
The log floor is sampled before the grant, then advanced only after success,
so new messages after a grant are not silently skipped.

Fault capture now writes selected numeric status and the full numeric
stall_snapshot/stall_recovery lines into kmsg as well as private RAM files. No
raw dmesg, process argv or environment is copied to the summary. A dmesg pull
before reboot retains those fields. The first-event kernel snapshot remains
unchanged. No kernel/module/PMIC code changes are required by R4.

`test-reopen.py` runs the actual watch/recover/open subprocess chain against fake
sysfs and a documented synchronous-grant model, with old template logs absent,
kind resetting only after the request, and driven progress conditional on all
three gates. It also checks a new hard transport fault, explicit cancellation,
and a nonzero manual-kind0 refusal. Run it with `--shell` for an Android mksh
wrapper and `--expect-old --script` to reproduce the shipped r3 failure. These
are host tests, not proof of phone scanout or a successful30minute soak.
