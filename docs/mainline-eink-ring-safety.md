# Render-ahead safety fixes after Review 14

2026-10-15 UTC. Offline only. Patch **0035** is based on kernel patches
0001–0034; 0034's abort diagnostics and `log_updates` parameter are preserved.
No inversion, waveform, frame-composition, PMIC-cap or panel-sequence changes
are included. Review 14's two failing regressions are permanent host cases.

**MEASURED** here means inspected source, host execution or compiler output.
There was no device run. The report is `runs/sol-fix-0035.md` in the worktree
and main checkout. The branch is `fix-0035`; nothing is merged or pushed.

## Required changes

1. **Job publication.** `a9_run_ring` initializes the engine/job payload and
   publishes `rjob` with `smp_store_release`. After its wait, the actual
   `a9_render_thread` accepts the flag with `smp_load_acquire` before reading
   init/bin/hash or touching the engine. Its ordinary wait condition may read
   the flag relaxed; that condition never authorizes payload consumption.
   The producer clears the flag and owns the borrowed engine/hash until it
   publishes `running=false` under the ring lock. The worker's locked join
   observation orders all completed writes before stack-hash reuse. A pending
   job is accepted even if thread teardown is requested, preventing a join on
   a posted-but-abandoned borrower. Only the worker publishes, with one job
   outstanding and `eng_lock` held throughout.
2. **Physical stop before producer join.** `a9r_request_stop` sets stop and
   wakes a producer waiting for a slot without joining. Error cleanup requests
   stop, attempts neutral and powers the transport/PMIC down before
   `a9r_stop` joins. If the abort-neutral commit fails, the existing transport
   fail-safe body, now exposed as `a9x_emergency_off`, invokes FP9929A emergency
   off immediately, latches the fault and disables drive **before** DRM
   shutdown or producer waiting. It needs no transport lock or engine state.
   Ring/engine/hash storage stays alive until completion; no elapsed-time bound
   is claimed for a thread rendering one remaining frame.
3. **Prefill admission.** Cancellation (`enable=0` or ring stop), deadline
   (`time_after_eq`) and producer error are checked on the ready/done fast
   path and after every successful idle-refresh call. Becoming ready inside a
   late DRM call cannot bypass the deadline. The two-second limit rejects a
   late return; it cannot make a stuck blocking DRM call return on time.
4. **Android scheduling companion.** The actual vendor setup script matches
   `a9_eink_r` first and explicitly applies SCHED_OTHER, priority 0, nice -10.
   Only exact `a9_eink` gets FIFO 50; commit workers retain FIFO 49.
   `set_user_nice` alone cannot undo FIFO. **The vendor script change must ship
   with kernel patch 0035**; applying the kernel patch to an old vendor leaves
   the old wildcard promotion bug. No vendor image is built by this task.
5. **Unexpected starvation stops the trial.** Unexpected ring failures,
   conservatively including prefill failures, latch the existing
   `a9x_stall_fault` and set `enable=false`. A known producer error is returned
   before sending remaining queued prefix frames. A user cancellation is
   distinct only when the return is `-ECANCELED` and enable is observed false;
   empty-ring cancellation is checked before underrun classification. Failed
   neutral still faults even during cancellation. `a9_run_ring` refuses a new
   job while the fault is latched, including after enable is rewritten true.
   Existing update/power-on checks remain. Coordinator-controlled `clear_fault`
   is required for intentional recovery; ordinary forced-clear bookkeeping
   does not override this latch. The legacy `stall_fault` status name now
   also covers failed render-ahead trials.

A neutral template stops further nonzero drive but does not establish DC
balance for an arbitrary truncated waveform. Neither a later forced clear nor
the host tests prove charge cancellation. Repeated truncated waveforms are
not an accepted starvation strategy.

## Unchanged bounds

**MEASURED source comparison:** the commit interlock remains
`A9X_STALL_FRAMES=3` at `A9X_PERIOD_DEFAULT_US=11764`, approximately 35.3 ms.
Its arm-before-incoming-drive and rearm-only-after-confirmed-progress logic
are unchanged. FP9929A's driver, `max_on_ms` ceiling/default of 20000 ms,
minimum off-time bounds/default of 500..10000/1000 ms, and the transport's
200 ms minimum-off delay are unchanged. No cap is raised, bypassed or paused
during prefill, abort or producer joining.

Successful neutral permits ordinary pipeline shutdown before the join;
failure removes HV through the independent emergency path first. Blocking
DRM operations, thread descheduling and workqueue delivery still have no
general software wall-time bound. The retained independent physical guards
remain necessary.

## Permanent tests

Run only the host harness, passing a private kernel copy with 0001–0035:

```sh
tests/mainline-a9eink-stall/run.sh \
  /home/user/A9-build/mainline/sol-fix-0035/linux-7.2.10
bash scripts/mainline/android/tests/test-eink-setup.sh
```

The suite includes the prior 22 cases and these new cases:

| Case | Actual path exercised |
|---|---|
| 23 | Review 14: 40 ms prefill deadline, 200 ms idle commit, ring becomes full/done inside the call; admission must fail |
| 24 | Review 14: actual driver abort cleanup, failed neutral and 90 ms producer; immediate emergency and shutdown precede join |
| 25 | Actual persistent dispatcher and producer: 24 jobs changing bin/init/hash/prepared-engine state |
| 26 | Actual prefill cancellation during slow rendering; shutdown before hash completion, stack hash retained until join |
| 27 | enable=0 while idle commit runs and ring becomes ready; admission cancels |
| 28 | Actual unexpected partial-waveform starvation latches stop; enable alone cannot post a retry |
| 29 | Empty-ring cancellation is not starvation; a known producer error prevents queued-prefix driving |
| 30 | Pending job is cancelled before the real producer accepts it; join retains engine/hash until acceptance/completion |
| 31 | Explicit stop prevents prefill/drive even with enable still true |

The harness extracts `a9_produce`, `a9_render_thread` and `a9_run_ring`
**verbatim from the driver being tested**. It executes them on a persistent
pthread alongside the real ring, transport and guard code. Only deterministic
engine frame generation, hash updates and board PMIC plumbing are replaced in
the job tests. The pre-existing engine/null tests still link the engine/core.
The host memory primitives model the kernel release/acquire with C atomics;
host success alone does not prove ARM64 ordering. Inspecting the compiled
ARM64 acquire/release instructions supplies separate compiler evidence.

The suite also invokes the real Android setup policy against fake
sysfs/proc/commands, starting with a simulated FIFO-50 producer. It checks the
OTHER/-10 reset and exact consumer match. An additional real same-CPU
FIFO/OTHER test uses disposable child processes when CAP_SYS_NICE is available;
it explicitly skips on this host because that capability is unavailable.
That measurement is not claimed as passing here. Existing Android setup
fixtures now use fake proc, so no host/system task is reprioritized.

**MEASURED validation:** all 31 driver/transport host cases and the scheduling
policy regression pass. The four existing Android setup cases pass. ARM64
`a9_eink.ko` compiles/links against the private Linux 7.2.10 copy with `W=1`.
The exact patch hash, module path and compiler-ordering evidence are recorded
in the handoff report. This is an offline fix, not approval for another panel
trial or byte-verification of a deployed Android image.
