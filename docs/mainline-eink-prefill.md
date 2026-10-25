# Reduced render-ahead admission: patch 0038

2026-10-16 UTC. Offline only. **MEASURED** means inspected source/saved logs,
compiler output or host tests. **INFERRED** means a timing model or unmeasured
phone result. No device access or phone/sequencer script was performed.

## Change and scope

Keep the 32-slot render-ahead capacity, producer, waveforms and serial job
ownership. Admit a posted job when **K ready slots** exist or the producer has
finished successfully. New module parameter:

`a9_eink.prefill_frames`, unsigned, 0644, **default 12**, minimum 2, maximum 32.
Invalid values refuse without changing the previous setting. Each `a9_run_ring`
snapshots the setting with READ_ONCE before publishing its job. Writes affect
subsequent postings; they cannot change a running admission threshold. The
callback publishes accepted values with WRITE_ONCE. The direct prefill API
also checks bounds. K=32 restores the previous full-ring/producer-done rule.

The change is only initial admission. `a9r_drive` still returns immediately
on an empty unfinished ring; it does not sleep waiting for production, repeat
a drive frame, drop a column or restart a waveform. Producer failure still
wins over a queued prefix. Cancellation and the 2-second admission deadline
still win over readiness, including after an idle commit returns late.

## Review 19: twelve-frame qualification setting

[Review 19, section B](/home/user/A9/runs/sol-review-19.md) rejected the
original K=4 default. Run-10 saved status records a lifetime render maximum
of **84.652 ms**, in a window of 288 Smooth/bin-10 completed updates. The
exact job/frame/rectangle is unknown. The earlier 17–20 ms observations do
not qualify a four-frame reserve.

At the nominal 11.764 ms period, K=12 represents 141.168 ms of queued output,
or 129.404 ms after first consumption: roughly 44.8 ms beyond that observed
delay. This is a queue model, not a scheduler guarantee. Review 19's isolated
84.652/100 ms delayed-producer cases complete at K=12, whereas K=4 underruns.
K=12 is for supervised qualification; it does not certify unattended timing.
K=32 retains the previous admission policy. Eight-frame Smooth updates render
completely before admission at K=12, so reduced prefill offers them no gain;
longer Clear/init sequences can benefit. Actual phone latency is unmeasured.

The Android vendor helper `a9ml-eink.sh open` explicitly writes **12** to
`/sys/module/a9_eink/parameters/prefill_frames` and reads it back while all
three gates remain closed, before the dry-run update. Missing parameters,
failed writes/reads and unexpected values refuse opening. Optional property
`vendor.a9ml.eink.prefill_frames` selects canonical decimal **2..32** for
future qualification; unset/empty uses 12, invalid values refuse. An override
does not itself qualify a lower reserve. The helper logs the verified setting.

Queued credit does not change the 35.292 ms frame-progress interlock, the
2-second prefill deadline or the existing HV budget. A sustained producer
slower than the consumer eventually drains any finite reserve and must fault.

## Safety and ownership

Unchanged on any drive error, including underrun:

1. Request producer stop.
2. Latch unexpected trial failure and disable drive; explicit enable=0
   cancellation remains distinct. No silent retry of a truncated waveform.
3. Establish validated neutral output. If neutral fails/refuses, remove HV
   immediately. Patch 0036's exclusive scanout validation and emergency after
   failed powered neutral remain active.
4. Power down before joining the producer. The engine/frame/stack hash remain
   borrowed until the actual persistent producer has finished.

Release/acquire job publication and spinlocked slot publication/consumption
are unchanged. Early admission uses the same fully published slots. Init and
mode-switch passes remain ordered within the same single producer stream;
no waveform columns are skipped. Producer completion still permits a short
sequence with fewer than K slots, but never admits a producer error.

No new timer, worker, priority or power cycle is introduced. The same 2-second
prefill deadline, three 11764 us progress periods, PMIC max_on/min_off and
transport off spacing remain. Prefill still consumes the active continuous-HV
budget; beginning drive does not restart that budget. Nothing clears a fault,
opens a gate, changes neutral-success accounting or relaxes post-fault drive
refusal. Fault containment is not a claim of charge balance for a truncated
waveform.

## Host tests and compile validation

The production-code host suite has 48 numbered cases. Cases 42–48 include:

- Real posted-job admission at twelve while production is deliberately gated
  until the first drive; producer is unfinished and capacity 32 is not full.
  A concurrent parameter write 12→32 does not change that job's captured 12.
- Eight-frame short jobs render completely before first drive at default K=12.
- Deterministic starvation for **every K=2..32**. The real run_ring caller
  latches the failed trial, establishes neutral/power-down before producer
  join, retains borrowed engine/hash lifetime, and refuses an enable-only retry.
- Sustained 20 ms producer versus 11.7 ms consumer: reserve drains, underrun
  faults rather than waiting/repeating a drive frame.
- Cancellation while only two of twelve frames are ready, and abort after an
  early-admitted drive. Failed neutral removes HV before the producer join.
- Exact production setter accepts bounds 2/32, refuses invalid/negative/text/
  overflowing input without changing the old setting; the component API also
  rejects invalid K. Existing late-ready/deadline, stop and producer-error
  precedence fixtures remain.

Legacy tests whose purpose is full-ring behavior explicitly select 32 rather
than accidentally relying on the old default. The frame-progress/neutral/
scanout and stack-lifetime regressions remain. Host wall times are not phone
measurements. Exact results and private ARM64 module path are in the task
report. The pattern/control suite from 0037 remains applicable.

## Next-job preparation overlap is deferred

The worker holds eng_lock across the current update and lends the mutable
engine, frame and optional stack hash to its one producer until join. The
next `a9e_prepare` changes old levels, step arrays, conditioning/target state
and mode history; writing them during the current job would violate that
ownership and make abort recovery ambiguous. Successful history is committed
only after current drain/join. Stock's separate preparation milestone does not
make sharing these mainline buffers safe.

A follow-up needs separate immutable target/prepared storage, generations and
invalidation after any active failure, while physical waveform sequences stay
serial. The measured warm preparation opportunity is approximately 9–10 ms;
it is not permission to count the entire drain as a latency saving. This patch
makes no speculative preparation or second engine borrower.
