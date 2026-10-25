# Offline frame-progress and render-ahead tests

Run `tests/mainline-a9eink-stall/run.sh /path/to/private/linux-7.2.10`.
No phone access, sequencer or waveform binary is used. With no argument the
runner archives the configured source HEAD and applies missing repository
0026/0027/0035/0036/0037/0038 patches to a temporary directory only. It never modifies the
source tree. The tree must otherwise contain the series through 0034.

The 47 numbered integrated C cases compile the real transport, guard, ring and engine. Tests
23–31 extend the previous 22 cases for Review 14. Cases 23 and 24 preserve
its late-prefill and failed-neutral regressions; case 24 now uses the actual
driver cleanup instead of reproducing its helper sequence.

`extract-job.py` copies `a9_produce`, `a9_render_thread` and `a9_run_ring`
verbatim from the selected production source. `job-test.inc` runs that
persistent producer thread and the actual consumer concurrently. Rendering
is replaced by a deterministic synthetic frame generator, hashes by a
context recorder, and the board PMIC helper by the real transport power-off.
The dispatcher, job handoff, ring ownership, frame packing, scanout, fault
policy and producer join are production code. A scheduler gate models a
posted job whose thread has not yet accepted it.

Coverage includes 24 successive jobs carrying varying bin/init/stack-hash
and prepared-engine state, cancellation while rendering and before job
acceptance, emergency removal before a slow producer join, late-ready
prefill and cancellation during its blocking idle commit, partial-waveform
starvation latching a fault and refusing retry, cancellation on an empty
ring, queued-prefix refusal after producer failure, and explicit stop.
Case 18 is a ring component test; case 28 exercises the driver's fault policy.

Pthread execution cannot establish an ARM64 memory-model proof. The
extractor also requires the production release/acquire primitives, and
review/build disassembly must establish their placement on ARM64. The
35.3 ms progress timer is exercised unchanged; a mock idle commit can return
late, and the test asserts rejection rather than claiming to bound a stuck
DRM call. Real FP9929A max_on_ms/min_off enforcement is outside this harness.

The runner also executes the Android scheduling policy test. Its mocked
actual-script checks always run. A same-CPU FIFO/OTHER contention probe
reports SKIP when the host lacks CAP_SYS_NICE; SKIP is not physical scheduling
evidence from Android.

Patch 0036 adds real `a9x_flip` execution with mocked DRM leaf APIs. Cases
32–34 verify cold full modeset versus steady blocking FB-only commits,
master/WW ownership and backoff, inactive/foreign-plane refusal, phase
maxima and failed-profile closure. A successful commit returning after the
hard deadline must return EIO, refuse further drive and never rearm progress
after emergency removal. Case 35 extracts the actual update wrapper and
checks scoped FIFO50 for real updates, OTHER/0 for dry and restoration on
return. The persistent producer begins with a simulated stale FIFO policy;
its actual thread entry must restore OTHER/-10.

Two additional standalone fixtures compile the actual kernel profiling
header and extract the actual `msm_atomic_commit_tail` function. Controlled
clock/stage costs check exact phase sums, reset/repeated-phase/DONE behavior,
invalid-phase refusal and NULL opt-out. The MSM fixture verifies both prior
and new flush waits are attributed to distinct phases. These are accounting
and call-path tests; real fence, IRQ, flush and scheduler latency still
requires coordinator-run measurements.

Review 17 adds cases 36–41. The real steady transport admission rejects a
foreign overlay or cursor; primary source/destination offsets and sizes,
rotation/reflection and foreign/missing FBs; changed mode geometry/timing,
connector/encoder routing, client offsets and invalid topology. Either owned
FB remains admissible when it differs from the last confirmed front. Each
refusal must skip DRM submission, close profiling and ownership, leave the
existing drive deadline unchanged, and record no neutral/progress success.
Case 39 attempts neutral cleanup with a contaminated scanout and an outstanding
drive, then checks immediate emergency removal before transport power-off.
Case 40 injects foreign scanout after a drive within the actual concurrent
`a9_run_ring`; its refused blank must cause HV removal before the slow producer
join, latch the fault, and preserve borrowed engine/hash lifetime until return.
These fixtures model DRM scanout state; they do not measure physical scanout.
Case 41 also covers refusal during idle prefill when no drive was submitted:
a failed powered neutral commit must remove HV immediately, even though
`front_null` remains true and the progress timer was never armed. The matrix
fixtures reset mocked fault globals between variants solely to isolate each
admission check; this is not a physical power-cycle or retry test.

Patch 0038 adds cases 42–48. The default twelve-slot admission is tested with
an actual producer that cannot finish or publish slot thirteen until the first
DRM drive starts. Changing the parameter to 32 inside that job must not
change its captured twelve-slot admission watermark. This detects regressions
back to full-ring admission rather than merely measuring elapsed host time.
The actual production module-parameter setter accepts every value 2–32 and
refuses out-of-range or malformed writes without changing the previous value;
the ring API separately refuses invalid thresholds before any commit.

Case 43 runs the actual dispatcher/consumer and abort cleanup for every K
from 2 through 32. The producer publishes exactly K frames, then remains
inside its borrowed engine until HV shutdown. Every case must drain exactly
that prefix, return EAGAIN once, latch the fault, establish neutral and power
off before joining, and refuse retry despite a later enable write. Case 44
uses a continuously slower producer (~20 ms versus ~11.7 ms consumer), so a
finite reserve must eventually starve and preserve the same failed-trial
policy. Case 45 cancels admission with two of twelve slots ready and requires
no drive and no starvation fault. Case 46 aborts after the first early-admitted
drive and forces the neutral commit to fail: emergency HV removal must precede
both ordinary power-off and producer join, with no false neutral or progress
disarm. Older full-ring-dependent fixtures explicitly select K=32 to retain
their original Review 14/17 regressions. Case 48 verifies producer-completion
admission for eight frames at K=12; extraction rejects a production default
other than 12 before compiling. Android gate tests separately exercise every
qualification override 2..32, invalid input, missing/write/read failures and
mismatched readback with every gate closed.

These tests establish admission and containment logic, not a hard producer
throughput guarantee. Reducing K can expose a slow renderer as starvation;
physical scheduling and waveform continuity still need coordinator-run data.

## Patch 0039: rail fault (run 11)

`extract-power.py` copies the production `a9_power_on`/`a9_power_off`,
`a9_rail_fault`, the HV sample helpers and `a9_take_job` verbatim, plus
`struct fp9929a_status` from the real header (it refuses a source whose
power-up path does not refuse latched faults first, or that powers down
before latching). Only the FP9929A getter (scripted PG/getter result; the
rails follow the transport), `regulator_force_disable` (recorder) and the
`enable` parameter are mocked; transport and guard are the real files.

Case 49 replays run 11: a waveform, PG low 26 ms after it; the fault line
and status capture describe the moment of detection (getter, raw PG, FP
on/energized, transport on/front-null/armed, session and waveform ages),
the latch precedes the ordered power-down, and with PG good again plus an
enable write there is no power-up, no commit and no drive frame (even a
direct transport power-up refuses drive commits); the queued job is a full
Clear. Case 50: only clear_fault=1 clears; the owed Clear survives the
clear and partial jobs resume only after it completed. Case 51: drive frame
armed and a failing neutral -> emergency HV off before DPMS. Case 52: DPMS
off fails -> regulator forced off, hv_fault. Case 53: lookup error
(-EPROBE_DEFER, pg_raw -ENODATA) and PG read error (-EIO) are distinct and
both latch. Case 54: the actual run_ring refuses with no job posted after a
rail fault, despite enable=1. FP9929A suite case (s) checks that the status
reports the single PG read behind pg_good and the energized state.

Review 22 reruns exposed case 23's CPU-speed prerequisite: 32 synthesized/hash
frames did not always finish inside its 200-ms fake commit. That fixture now
uses test-only producer-start and readiness barriers in the mock commit (bounded at five host
seconds). It still checks the unchanged 40-ms production admission deadline,
requires ready/done before the late commit returns, and must return ETIMEDOUT.
No production timing constant, interlock margin or safety assertion is relaxed.
