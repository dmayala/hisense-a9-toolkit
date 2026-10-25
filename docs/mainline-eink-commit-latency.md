# Run 9 commit latency and patch 0036

2026-10-16 UTC. Offline source/log analysis. **MEASURED** means a saved
run-9 observation, inspected source, compiler result or host test;
**INFERRED** means a mechanism not isolated by a phone trace. No phone
operation, phone script or sequencer was run for this task.

## Diagnosis

**MEASURED:** run 9 completed the 123-frame Clear with late_drive=0, then
two 30-frame partial updates had late_drive +6/+3. The following partial
stopped after seven sent frames with the progress fault. Its ring_min=24,
underruns=0 excludes ring starvation at that fault. Rendering/compose maxima
were 19575/19400 us; those elapsed measurements include descheduling and are
not pure CPU execution costs. `commit_max_us=146545` covers **all** commits,
including initial power-on/modeset; it was already 138340 with two slow
commits after the clean Clear. It does not prove a steady page flip took
146 ms. The drive gap max of 62435 us does establish bad drive pacing.

Evidence root:
`/home/user/A9-wt/run9/runs/20261015T235605Z-mainline-android/android/`.
The `eink/hold/fault-000031-eink-status.txt`, `pace.log` and
`fault-000031-vendor-dmesg.txt` preserve these observations.

**MEASURED defects/contributors:**

* Every old frame calls the full DRM client modeset helper, which acquires
  all plane states, disables non-primary planes and disconnects/reconnects
  connectors in the proposed atomic state. This is unnecessary for swapping
  two identical-format primary framebuffers. Saved kmsg at 207.7695–207.7714
  shows plane43 acquisition/disable, connector NOCRTC→CRTC, routing and bridge
  checks immediately before the next commit. This is software state churn;
  a log saying "first commit" is not proof of a repeated hardware modeset.
* The boot debug-off service **failed**, rather than successfully ending its
  60-second window. Vendor dmesg line1887 (56.139225 s) reports init argument
  expansion failure in the inline shell expression; line1953 reports SIGABRT.
  DRM atomic logging remains active around the 207 s fault. The capture has
  13854 DRM lines out of 17667, including 12994 in uptime148..209 s.
* The same log line1832 (40.632884 s) says producer scheduling setup failed.
  No successful FIFO setup log is present. Saved `ps` has no scheduling-policy
  or RT-priority columns. The intended shell policy is not proof of effective
  runtime policy, and changing nice alone cannot establish FIFO or undo it.

**INFERRED:** full-state preparation, verbose logging and possible scheduling
delays can consume the drive budget. The capture does not isolate how much
time was spent on CPU, fences, prior commits, hardware flush waits or wakeup
latency. In particular, the command line is `loglevel=6` without
`ignore_loglevel`: KERN_DEBUG traces are ordinarily filtered at the console,
so a synchronous debug-UART bottleneck is **not established**. Formatting,
kmsg/logd traffic and contention remain plausible. Patch 0036 removes the
confirmed unnecessary work and reliance on shell scheduling, and measures
the remaining phases; it does not claim a proven hardware pacing cure.

## Source path and execution context

Paths below are Linux 7.2.10, base kernel commit
`6d8479f419fb48abfa967bf5d8b19d53e4c3c752` (0001–0035).

| Stage | Source / function | Execution and waits |
|---|---|---|
| Old submission | `a9_eink_transport.c:a9x_commit_locked` → `drm_client_modeset.c:drm_client_modeset_commit_atomic` | Same a9_eink consumer task; full plane/connector preparation and atomic validation |
| Core dispatch | `drm_atomic.c:drm_atomic_commit` → `msm_kms.c:msm_mode_config_funcs.atomic_commit` | Calls `drm_atomic_helper_commit(..., false)` |
| Preparation | `drm_atomic_helper.c:drm_atomic_helper_commit` | Setup, prepare_planes, blocking fence wait, swap_state; setup/swap can wait for preceding commits |
| Tail | `drm_atomic_helper.c:commit_tail` | INLINE for blocking commits; fences/dependencies, then msm tail. Only nonblocking commits queue `system_dfl_wq` |
| MSM | `msm_atomic.c:msm_atomic_commit_tail` | Runtime-PM enable, per-CRTC commit_lock, prior wait_flush; prepare/planes/flush; new wait_flush; completion/cleanup |
| DSI video | `dpu_kms.c:dpu_kms_wait_for_commit_done` → `dpu_encoder_phys_vid.c:dpu_encoder_phys_vid_wait_for_commit_done` | Waits up to 50 ms for CTL flush register=0 on pending_kickoff_wq |
| IRQ | `dpu_encoder_phys_vid.c:dpu_encoder_phys_vid_vblank_irq` | Wakes that waitqueue before queueing frame-done event work |
| Event work | `msm_kms.c:msm_drm_kms_init` | `crtc_event:%d`, kernel sched_set_fifo (50); Android helper attempts49. Does not deliver the video flush-wait wakeup |
| Async worker | `msm_atomic.c:msm_atomic_init_pending_timer` | `atomic-worker-%d`, kernel FIFO50; legacy cursor/async optimization, not this blocking flip path |

Thus promoting a hypothetical CFS commit worker would not fix this path:
the hardware commit runs inline in the caller. The task called "producer"
in the run description is the render producer `a9_eink_r`, intended
OTHER/nice-10; the FIFO50 design role is the commit **consumer** `a9_eink`.
There are separate DRM devices for vkms, a9_eink and msm. Their master,
client-modeset, plane/CRTC and msm commit locks are not a single cross-card
mutex. The virtual card's shadow/input-buffer path can compete for CPU,
memory bandwidth and printk resources, but its buffers are not the transport's
CPU-written dumb scanout buffers. No saved evidence proves a cross-card lock
owner stalled this commit.

Run-9 configuration enables schedutil with eight CPUs and `cpuidle.off=1`;
the built kernel has PREEMPT, not PREEMPT_RT. There is no captured actual
frequency, task affinity or scheduling trace. DPU `core_clk=33546576` is a
display clock, not CPU frequency. Pinning the consumer without measuring IRQ
and event-worker placement could make scheduling worse. 0036 therefore does
not change affinity, cpufreq, cpuidle or Android RT priorities.

## Minimal path changes

* After initial full enable, `a9x_flip` changes only the existing primary
  plane's FB. It preserves geometry/routing, internal-master exclusion,
  client and WW locks, validation and **blocking** atomic completion. It
  forbids modesets, inactive CRTCs and foreign primary-plane routing; errors
  go through the abort policy. Review 17 additionally requires exclusive
  primary scanout, exact geometry/rotation/mode and fixed connector/encoder
  routing, as detailed below. No queued/unconfirmed flip is counted
  as progress. Initial connector/panel/rail enable and DPMS-off stay full
  modesets. There is no direct register programming or bypass of DRM helpers.
* The consumer scopes `sched_set_fifo(current)` (kernel FIFO50) around a real
  update; dry work and return to idle use `sched_set_normal(...,0)`. The render
  producer explicitly selects `sched_set_normal(...,-10)`. These kernel APIs
  avoid a dependency on shell tool availability, capabilities or successful
  setup timing. Existing shell policy remains a compatible setup attempt.
* Android's open helper writes DRM debug=0 and checks it before any gate opens;
  failure refuses opening. The boot debug timer now calls a shell-file mode
  rather than passing dollar expressions through init argument expansion.
* A drive commit returning success after the hard fault is rejected with EIO;
  it cannot count as confirmed progress or rearm after an observed fault.
  Neutral cleanup is still permitted. The ring fault, emergency-before-join,
  release/acquire and cancellation policies from 0035 are preserved.

No cap or interlock is relaxed: three 11764 us frame periods (~35.3 ms), FP
max_on_ms and min_off, and the transport off-time/neutral shutdown rules are
unchanged. A DPU wait can itself outlast the drive budget; its existing void
wait_flush path can log a timeout yet let the generic commit return 0. The
independent fault check and physical interlocks remain necessary. No claim of
DC balance for a truncated waveform is made.

## Review 17: fail-closed scanout admission

2026-10-16 UTC. Review 17 reproduced an unsafe acceptance in a modeled host
case: an idle primary FB could be committed through unchanged foreign planes
or primary geometry, then falsely cancel the progress interlock. This is a
source/test finding, not an observed foreign writer or damaged panel on run 9.

Before every steady commit, the acquired CRTC state must have exactly the
primary-plane bit set; an overlay or cursor bit refuses with EBUSY. After
acquiring the primary state too, mode timings/clock/flags must match the
client's fixed 448x829 mode (`drm_mode_equal`), client offsets must be zero,
and the connector/encoder masks must match the single transport route.
The connector must offer exactly one encoder. This topology is source-backed:
`msm/dsi/dsi_manager.c:msm_dsi_manager_connector_init` creates the bridge
connector for one encoder; `display/drm_bridge_connector.c` attaches that
encoder once. Routing masks are protected by the CRTC state lock; encoder
attachment topology is not mutable atomic scanout state.

The locked primary must point at the transport CRTC and either owned FB, use
zero source/destination offsets, exact 448x829 destination and 16.16 source
sizes, and `DRM_MODE_ROTATE_0` without reflection bits. Both FBs are accepted
because a failed atomic tail may already have swapped FB state without
advancing the last confirmed `front`. Refusals occur before FB replacement;
the existing WW retry/put/drop/master cleanup remains. There is no live-HV
modeset or sanitizing takeover of foreign state.

Every failed neutral commit while powered now calls emergency HV removal
after DRM locks are released, before another DRM attempt or producer join.
This also covers failed prefill idle refreshes when cached `front_null` is
true. It leaves `front_null`, `stall_armed`, the recorded deadline and confirmed
progress untouched; emergency timer cancellation is physical fault containment,
not a successful-blank disarm. The existing outer abort emergency path remains.
Only successful validated neutral output can take the normal progress-disarm
path. Caps and timer constants remain unchanged.

## Phase diagnostics

Status retains its original four lines, then adds `flip_phases` and `flip_max`.
The Android capture stores lines 4–6 in pace.log. Values are microseconds.

| Phase number | Field | Included work |
|---|---|---|
| 0 | prepare_us | Allocation/master/WW locks, validation, setup, prepare_planes and swap_state; may include prior-completion waits |
| 1 | fences_us | Both blocking plane-fence wait sites |
| 2 | dependencies_us | Explicit tail wait_for_dependencies |
| 3 | wait_prev_us | MSM runtime enable, commit_lock and prior hardware flush wait |
| 4 | program_us | MSM prepare_commit, plane/modeset programming and new flush |
| 5 | flip_done_us | New DPU flush/vsync wait; not a DRM completion-object timestamp |
| 6 | cleanup_us | MSM complete_commit, planes, generic cleanup and return/unlock |
| 7 | DONE | No active call |

`phase`/`age_us` show an in-flight phase; concurrent snapshots are approximate.
`flip_max` stores per-phase maxima across flips plus separate total flip and
initial modeset maxima. Maxima need not belong to the same frame. `valid=0`
means no flip profile exists yet; cold full modesets are deliberately not
attributed to these flip phases. The old commit_max_us still covers both
paths for compatibility. No per-frame printk is added.

Profiling is opt-in via a pointer on the atomic commit; other callers leave
it NULL. The caller owns the storage through its blocking call. The helpers
observe durations only; they do not change waits, fences or return values.
**Rebuild matching DRM core, msm and a9_eink/kernel artifacts together:** the
internal atomic structure changes; this is not a standalone module hotfix.

## Offline validation

The host suite passes 41/41 integrated cases, plus tests using the actual
profiling header and extracted production msm tail. Coverage includes full
enable versus FB-only flip, WW backoff, master/CRTC/plane refusals, late
successful commit after fault, phase/error closure, kernel scheduling scopes
and all previous ring/lifetime/interlock regressions. Review 17 adds foreign
plane, primary transform/rectangle, mode/routing and immediate refused-neutral
containment cases, including actual concurrent producer cleanup with either a
drive outstanding or only cached idle state. Gate tests pass 14/14;
setup tests pass 4/4. The optional privileged same-CPU scheduler probe skips
without CAP_SYS_NICE; no device scheduling result is claimed.

The private 7.2.10 ARM64 a9_eink module and changed DRM core/MSM objects compile
with W=1. Exact patch hash, artifact paths and final validation are in
`runs/sol-fix-0036.md`. The new phase data, rather than the aggregate run-9
maximum, should guide any further change to the wait path.
