# Phase5 after N-d's UI-only stall

User-supplied result: N-d stalled at uptime498.5s during a no-audio UI soak;
first snapshot in_commit1, stage2, phase5, phase_age33.7ms, flip_done_us0;
recovered1/2. Audio is not required, and flush polling alone was insufficient.
The user is moving to N-e. No private evidence or device/webcam access here.
Neither the source pass nor this snapshot establishes when hardware cleared.

## Exact boundary and reachable waits

The actual N-d module source is frozen at
`/home/user/A9-build/mainline/integrated-20261021n-d/msm-build2/source/drivers/gpu/drm/msm/`.

| Phase | Source operations | Consequence for this snapshot |
| --- | --- | --- |
|3 WAIT_PREV | msm_atomic_commit_tail: enable_commit, lock_crtcs, previous wait_flush | DPU runtime resume and earlier flush have returned before phase5. |
|4 PROGRAM | prepare/planes/modeset hooks, vblank_get, dpu_kms_flush_commit -> dpu_crtc_commit_kickoff -> trigger_flush/start, unlock_crtcs | CTL start, register programming, prior-resource/reset checks and enabling the timing engine occur before phase5. |
|5 FLIP_DONE | trace start; dpu_kms_wait_flush -> per-CRTC/encoder loop -> dpu_encoder_wait_for_commit_done -> physical VIDEO wait; trace finish | In this topology the sleepable CTL_FLUSH poll is the only explicit wait here. Trace/debug overhead, MMIO latency, descheduling/IRQs are also included in elapsed time. |
|6 CLEANUP | vblank_put, lock_crtcs, complete_commit/performance reduction, disable_commit, cleanup_planes | These are not inside phase5. |

The video callback is `dpu_encoder_phys_vid_wait_for_commit_done` (line539),
assigned in `dpu_encoder_phys_vid_init_ops` (line761). Its body reads CTL_FLUSH
until zero with read_poll_timeout(sleep_us1000, timeout_us50000). The read is
`dpu_hw_ctl_get_flush_register` -> dpu_reg_read -> readl_relaxed, no CCF/genpd
call or sleeping mutex. Wrapper loops do not acquire a mutex. The timeout path
can queue `msm_disp_snapshot_state`, but this only queues a dump worker and is
followed by the existing MSM hard-error latch. A zero-error late return cannot
be ascribed to an unreported synchronous dump in this wrapper.

This custom MSM tail does **not** call generic
`drm_atomic_helper_wait_for_flip_done` or `wait_for_vblanks`. The separate video
`wait_for_tx_complete` uses a vsync waitqueue, but the phase5 callback calls
`wait_for_commit_done`, not that function. Command-mode CTL_START completion
and writeback are different physical ops; DSI host VIDEO flags select the video
encoder. A non-bonded DSI0 gets one physical video encoder, not cmd+vid in series.
Bonded topology would add a physical encoder, so a live topology contradiction
should be checked rather than inferred from the phase name.

`flip_done_us0` is expected **while phase5 is open**: drm_atomic_profile_enter
adds elapsed time to ns[old] only upon the next phase transition. Its current
age is33.7ms; zero in the not-yet-closed accumulator does not mean no wait.

## Vsync and the63ms ambiguity

Panel source mode is562x839 total at40079kHz, approximately85Hz/11.765ms.
One missed latch boundary can defer an update another frame. Two periods are
about23.53ms; three35.29ms. There is no explicit software rule in phase5 to wait
two vsyncs or drain a previous frame and then wait a second flip event. A63ms
completion is over five nominal periods; two ordinary periods alone do not
explain it. Whether hardware actually delayed that many periods needs counters
and register observations. Mode-derived frequency is not a live-clock proof.

The vsync handler itself comments that software IRQ latency may mean vblank
already happened. It tests `(flush & pending_mask)==0` before decrementing the
pending count, then wakes the queue. Phase5's predicate is stricter (`flush==0`),
both before and after the N-d patch. Record the actual bits and pending mask if
nonzero persists; do not change the predicate based only on a phase age.

Crucially, read_poll_timeout is not a real-time1ms or50ms execution guarantee.
It requests usleep_range(251,1000) in this kernel. Delayed timer delivery or
runnable scheduling can resume it much later. The real iopoll macro computes
whether timeout expired, then performs a read and accepts a true condition
**before returning timeout**. Thus hardware can clear early, the task resume
past50ms, and the call still return0. Higher-priority RT work, IRQ/preemption-off
regions, delayed hrtimer service or other scheduler latency remain possible.
No causality is claimed from those possibilities alone.

The updated host test compiles the actual N-d callback and actual iopoll macro.
With modeled hardware clear at12ms and an injected62ms sleep overrun, it returns
success at63ms. With uncleared hardware the same overrun returns-ETIMEDOUT.
This demonstrates why N-d's failure **does not rule out early hardware clear**;
it does not demonstrate that this overrun happened on the phone. No busy wait,
priority/affinity change or shortened timeout is proposed on that basis.

The consumer already runs FIFO50 during physical updates. This prepared kernel's
RT runtime and period source defaults are both1000000us; do not import a generic
950000/1000000 assumption and diagnose a50ms throttle from it. Android can change
those sysctls; the existing user-run capture saves actual values and sched data.

## What N-e already records

N-e vendor3640432d is unchanged by this analysis. Its soft warning at100ms prints
the **age at timer service**, once per progress interval, rate-limited. It does
not print the final duration when progress returns. `soft_events` counts those
warning intervals. `soft_last_age_us` is stored internally but not exposed.
There is no histogram, per-completed-delay log or sub100ms warning.

Available read-only status telemetry:

- `pace commit_max_us` and `commit_slow`: cumulative maximum/count, with slow
  meaning greater than1.5mode periods; these include drive and neutral commits.
- `flip_max ... flip_done_us total_us`: per-phase maxima and total flip maximum,
  not necessarily from the same commit. `max_gap_drive_us` retains the longest
  measured drive gap. No count distribution is recoverable from a maximum.
- `flip_phases`: last/current profile; a neutral update can overwrite the last
  drive profile. At a hard event, the first stall snapshot is frozen instead.

A63ms update can therefore succeed on N-e with soft_events0. That is expected,
not evidence the35ms tail disappeared. Reading final maxima still tests whether
such a tail remains without another image or additional video wear.

## Smallest next diagnostic distinction

Use a short user-owned capture under the same UI activity. Retain existing
sched_waking/wakeup/switch, IRQ entry/exit and wait_flush entry/return. Bracket
`dpu_encoder_phys_vid_wait_for_commit_done`; observe actual CTL_FLUSH values at
its reads and the frame/vsync count, with low-volume aggregation rather than
printk per poll. Then distinguish:

| Evidence | Meaning |
| --- | --- |
| tens-of-ms between successive reads; task sleeping until late wake | Timer/IRQ delivery or sleep-overrun path; hardware completion time still unknown during the gap. |
| wakeup occurs promptly but task does not run for tens of ms | Runnable delay/RT interference; match target CPU and intervening scheduled/IRQ work. |
| reads continue roughly every1ms and register stays nonzero across real vsyncs | Hardware/latch or pending-bit behavior becomes the lead. Record flush bits vs pending mask and INTF frame/line progression. |
| MMIO entry/return itself is long | Bus/read latency or IRQ interruption of that call; distinguish with IRQ/scheduling timeline. |
| callback returns promptly but outer wait_flush/phase remains long | Encoder/CRTC loop, debug/trace overhead or scheduling outside the inner poll; bracket the wrapper. |

For a future passive telemetry candidate, fixed completed DRIVE-flip bins
<20,20-35,35-50,50-75,75-100,100-200,200-500,>=500ms would capture the missing
35-100ms population. Keep total commit and phase5 histograms distinct; retain
raw result, poll sample count/max sampling gap and first/last flush for slow
waits. Report aggregate counters through sysfs and rate-limit only summaries,
not event counts. This would distinguish tails with minimal output, without
changing N-e100/500ms gates, recovery or votes. It is a proposed diagnostic delta,
not a feature already shipped in N-e.

The old capture accepts5-30seconds and freezes on the hard starvation cutoff.
On N-e, a63ms successful delay will not trigger that cutoff. Do not run that
script after an event and infer missing traces exonerate a subsystem; a future
completion-tail capture needs its own passive duration trigger or bounded UI
window. No new capture was executed or phone action consumed here.
