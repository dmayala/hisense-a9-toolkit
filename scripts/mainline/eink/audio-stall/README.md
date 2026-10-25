# Audio-start E Ink stall, 2026-10-21

Host finding and a user-run capture; the blocking dependency is not yet identified.
See [the N-d assessment](path-assessment.md) for the later phase5 evidence,
source ownership audit and flush observation candidate. The original capture
instructions and earlier findings below remain available.

The coordinator reports two cutoffs0.3-0.5s after WCD AUX/SoundWire/DMA startup,
with speaker pops. Recovery r4 visibly works but exhausts two grants per boot.
This localizes a trigger, not which lock, IRQ or power rail caused the delay.
Raw phone logs remain private; this worker used only the supplied facts.

The shipped N module already runs real updates in SCHED_FIFO50 (`a9_update`),
with producer OTHER/nice-10 and a hard hrtimer cutoff. Render-ahead is always
used for real updates (`a9_run_ring`); touch prewarm is separate, default0,
and inert with the production helper's default hv_hold_ms0. Source and shipped
module staging match; actual runtime parameter readback belongs to the user.
`render-ahead trial failed (-5)` is logged after transport -EIO; after the
cutoff, a successful raw DRM return is deliberately converted to -EIO.
It is not evidence of producer starvation. Ring starvation has its own
`render-ahead underrun`/-EAGAIN message and underruns counter.

Between drive frames, a9r_drive -> a9x_show_data composes and performs a blocking
FB-only DRM flip. It does not access FP9929A/AW8695/WCD I2C or regulators.
Transport/client/modeset locks are private to DRM; audio does not directly use
those locks. Lower layers can still wait on GEM reservations/VM, DPU PM and
scanout completion. DPU enable_commit calls pm_runtime_get_sync; clock/bandwidth
votes are conditional on performance changes. Video-mode encoder holds a DPU
runtime reference, so a global clock lock is a hypothesis, not a demonstrated
per-frame dependency. A long shared clk enable spinlock can also delay IRQs.
FIFO cannot bypass a sleeping mutex or disabled IRQs. A new timer alone cannot
make blocking DRM commits safe in hardirq context.

`kernel.config` for N has EVENT_TRACING, KPROBE_EVENTS, STACKTRACE and SCHEDSTATS.
FUNCTION_TRACER, FUNCTION_GRAPH_TRACER, IRQSOFF_TRACER, LOCK_STAT, snapshot and
hist triggers are absent. Thus function_graph/irqsoff/lockstat instructions would
require a different kernel. Ordinary lock:contention_begin/end are nevertheless
compiled into this kernel's mutex implementation, including optimistic spin.

After copying capture.sh to the test phone, the coordinator may run it from
its existing root console on the unchanged N boot, with a NEW private output:

```
sh /data/local/tmp/capture.sh /data/local/tmp/a9-audio-stall-one 15
```

On READY, reproduce one ordinary audio start with visible screen activity.
The script plays nothing and changes no E Ink gate, policy, priority, affinity,
IRQ routing or power setting. It uses a uniquely named trace instance,512KiB
per CPU (4MiB on eight cores), and only its own dynamic probes. It stops recording
on the first fp9929a_starvation_off entry, or at15seconds; it then saves the trace,
per-CPU loss counters, thread scheduling/affinity, parameters, interrupts and
read-only E Ink status. Cleanup destroys its instance/triggers before deleting
its probes. It never resets the global tracer or removes another owner's probe.
If mandatory events/probe writes are denied or absent, it exits and cleans up;
do not change SELinux policy or retry a consumed phone attempt automatically.
No live phone/tracefs validation was performed by the worker.

Interpret the first cutoff with the frozen stall_snapshot and private trace:

| Evidence before cutoff | Meaning and next fix |
| --- | --- |
| contention_begin in a9_eink, >35ms to matching end, stack in clk_prepare_lock or regulator/I2C | Shared-lock dependency established. Match lock_addr against contended_release to identify unlocker; move that exact operation before drive admission/after neutral, or fix the audio holder. Do not replace clock APIs with unlocked register reads. |
| contention stack in GEM/VM/SMMU, or long msm_framebuffer_prepare | Pre-pin both owned transport buffers before HV/first drive; balance references on all teardown/error paths. Only do this if the captured call demonstrates the dependency. |
| a9_eink runnable after wakeup or switched out with prev_state R, long delay, no mutex wait | CPU/RT throttling/affinity or long IRQ/softirq interference. Compare target CPU, sched_rt_* and IRQ durations; a big-core affinity trial is then meaningful. |
| in_commit1 stage2 phase5, wait_flush pending, normal CPU scheduling | DPU/DSI latch/IRQ/flush wait. Follow msm wait_flush and DPU events; check IRQ delivery and audio clock/power transitions. Moving the render thread or adding another hrtimer does not fix this wait. |
| phase3 | Includes DPU enable_commit/runtime PM, commit mutex and previous flush: use stack/events to distinguish them. |
| phase0/stage2 before tail | DRM lock/check/setup/FB preparation; use the blocking stack and entry/return probes. |
| first PG/rail fault before cutoff | Power stability remains a separate cause; timing and a later emergency-off PG low are insufficient to prove a PA-induced dip. |

Phase numbers:0prepare,1fences,2dependencies,3wait_prev,4program,5flip_done,
6cleanup,7done. First snapshot is boot-frozen; after multiple stalls its first
phase and later return fields may belong to different events. A fresh boot and
capture-stopping first cutoff avoids that ambiguity. Ignore later recovered
profile values as evidence of the first block. Read buffer-stats: overruns mean
older events were overwritten, not that missing activity never happened.
Clock tracepoints fire after lock acquisition; entry probes and contention stacks
cover the time waiting before them. Optional static function probes may be
unavailable/inlined; probes.txt records actual support. Symbolic stacks and
lock addresses are private diagnostics; return only sanitized timing/phase,
blocking symbol chain, matching unlocker comm, RT policy/CPU and IRQ name/count.

Coordination for the audio pane: no E Ink I2C call occurs in the active-frame
loop. Please distinguish whole ALSA/DAPM prepare duration from the time actually
holding clk prepare/enable, genpd, regulator or I2C locks; inspect PA enable/mute
ordering separately for the pop. Global clock enable uses an IRQ-safe spinlock,
while prepare uses a sleeping mutex. A single long prepare call is not by itself
proof either lock was held for its full duration. This capture will identify
whether a9_eink waits on one and which task releases it.

Stock timing check: the exact stock composer SHA8c89557b was re-opened with REA
Ghidra12.1.4. EpdUpdateThread at ELF0x1a4c74 (analysis0x2a4c74) uses an eight-slot
producer/consumer path; Flip at ELF0x1caee4 (analysis0x2caee4) changes FB_ID and
calls drmModeAtomicCommit flags0x400, then rotates buffers even on error. This
supports retaining the blocking FB-only path; it supplies no allowable physical
hold duration or proof that a 94ms drive hold is harmless. Prior stock-kernel
wait analysis reports up-to42ms slices and conditional waits; timeout constants
are not panel tolerance measurements. REA evidence IDs and full dossiers are in
/home/user/A9-build/mainline/eink-audio-stall-20261021/rea-*.json. Source of prior
kernel findings: docs/a9-display-measurement-boundaries.md. No threshold increase.

The proposed two-grants-per10min policy would require both the E Ink admission
and built-in FP9929A budget to agree, a monotonic total grant receipt for the
userspace watcher, and tests at rolling-window boundaries plus hard-fault
precedence. Resetting only a counter breaks the r4 admission floor and PMIC
budget. It is left unchanged while the reproducible trigger is unresolved;
a replenished budget would not eliminate pops, drive gaps or partial waveforms.

Host validation: sh and actual Android mksh syntax checks rc0; two fake tracefs
scenarios PASS (first-cutoff retention/owned cleanup and mandatory-event failure
cleanup). These do not validate real event filter/probe support on the phone.
