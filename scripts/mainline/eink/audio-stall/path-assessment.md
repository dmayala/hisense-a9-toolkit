# N-d source assessment and flush observation candidate

The user reports that N-c eliminated speaker pops and volume keys work. Its
first captured stall was in_commit1, stage2, phase5 (flip_done), phase age33.9ms,
with a successful raw return27.7ms after cutoff. A second stall happened during
continuous playback without a new audio start; a frontlight action preceded it.
The second trace was armed after that stall. These are supplied, sanitized facts;
this worker did not open private evidence or operate the phone.

Phase5 in `msm_atomic_commit_tail` begins immediately before `wait_flush`.
`enable_commit`, plane programming and conditional performance votes for that
commit have returned. A direct DPU-resume/CCF wait cannot explain that captured
phase; indirect IRQ/clock interference is still possible. A start transition is
not required to reproduce the problem.

## Clock and domain ownership

The prepared kernel used by N is available under
`/home/user/A9-build/mainline/suspend-pmtest-kernel-20261020/kernel`.

| Call or resource | Actual ownership and conditional behavior |
| --- | --- |
| a9r_drive -> a9x_show_data -> a9x_flip | Blocking FB_ID flip. Transport validates unchanged mode, routing, geometry, format and rotation. No direct audio, I2C or regulator call in the frame loop. |
| msm_atomic_commit_tail -> dpu_kms_enable_commit | Calls pm_runtime_get_sync per commit. `drivers/base/power/runtime.c:rpm_resume` returns early on RPM_ACTIVE, before resume callbacks, parent or genpd work. An invocation is not proof of a clock-prepare operation. |
| disp/dpu1/dpu_encoder.c resource control | resource_enable acquires a DPU runtime reference. Video ENTER_IDLE disables IRQs without dropping that reference; STOP releases it. |
| disp/dpu1/dpu_crtc.c enable/disable | Holds DRM/MDSS runtime ownership across enabled video operation. Its extra video bandwidth reference prevents frame_done from releasing the last bandwidth reference. |
| disp/dpu1/dpu_core_perf.c:dpu_core_perf_crtc_update | Only changed requirements raise/lower bus and core OPP votes. Stable FB-only flips retain the current votes. Changed state, initial enable and shutdown can still execute ICC/OPP operations, potentially taking CCF or domain locks. |
| drivers/iommu/arm/arm-smmu/arm-smmu.c | Mapping operations use runtime get/put, but the DPU's PM-runtime supplier link retains SMMU while the active consumer holds power. Per-frame FB preparation/cleanup still churns GEM/VM mappings; this is a separate possible latency source, not a proven CCF wait. |

The smallest session hold for DPU clocks/domains is therefore already present.
Adding another global hold or bypassing per-frame PM APIs has no demonstrated
benefit for this video session. A retained-two-FB mapping candidate was parked
without changing the product or adding exports. It can be revisited if a trace
localizes a delay before the atomic tail to GEM/SMMU mapping.

## Phase5 candidate selected for N-d

Main already contains
`0059-drm-msm-dpu-observe-flush-without-IRQ-wakeup.patch` (a60d8ca), but N's
private kernel recipe applied the *other*0059 regulator patch and0060 only.
N therefore waits on `pending_kickoff_wq`, testing CTL_FLUSH==0 with a50ms
wait_event_timeout. A register clear without a timely IRQ wake can return success
only at timeout, past the35.292ms A9 cutoff. The actual phone IRQ behavior is
unproven; other scheduling or hardware delays can also produce phase5.

N-d applies that existing patch to the MSM module: sleepable1ms register polling
of the identical CTL_FLUSH==0 predicate, retaining the50ms DPU timeout. It keeps
0060's hard-error latch. An uncleared flush remains an error; scheduling delays
and true scanout failures are not repaired by polling. E Ink cutoff, rail logic,
render-ahead, scheduling, gates and the two-grant budget remain unchanged.
No kernel Image change is needed if imports, exports and vermagic match N.

`build-msm.py --out /absolute/empty/build-dir` builds a private MSM tree using
prepared ancestor headers. It pins the borrowed config, symvers and Image.gz,
checks every MSM import/export CRC and vermagic against N, and runs the existing
hard-latch tests plus `test-path.py`. The latter compiles actual old/new wait
functions with the real iopoll macro under a model clock (timely wake, missing
wake, uncleared flush), and the actual performance-update function (1000 stable
frames, changed votes and disable). Structural checks assert the active-PM fast
return and video idle ownership. Models and source structure do not establish
phone timing or exercise real IRQ delivery.

The audio worker's separate clock-hold candidate is opt-in for a bounded user
comparison, default OFF. It must keep synchronous ACK-before-use and balanced
prepare references; no fire-and-forget or CCF unlocking is justified. Its purpose
is to remove long stream-transition APR waits under CCF, not to claim the observed
phase5 stall fixed. Activation and exact signed handoff belong to its report.

N-d later stalled at phase5 under no-audio UI use. See [the updated audit](phase5-after-n-d.md), including the real-iopoll sleep-overrun model and N-e telemetry limits. Polling is insufficient, and actual hardware-clear time is still unknown.
