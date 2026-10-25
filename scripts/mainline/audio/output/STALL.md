# Opt-in LPASS clock hold candidate

The latest n-c phone result changes the causal assessment: **no pops, volume
keys work**, but an E Ink stall occurred during continuous playback without a
new audio start. Its phase5 `flip_done` wait begins after DPU enable/runtime-PM
and performance programming returned. Audio clock startup is not a necessary
trigger. No private runtime logs were opened by this worker.

agent's source pass confirms that the video encoder normally holds the DPU
runtime reference throughout an active video session, and stable FB-only flips
do not normally change performance votes. A per-frame `pm_runtime_get_sync`
does not prove a per-frame CCF prepare. His directly relevant phase5 candidate
applies the omitted `0059-drm-msm-dpu-observe-flush-without-IRQ-wakeup.patch`
(a60d8ca): poll the existing CTL_FLUSH-zero predicate using sleepable 1 ms
polling, retaining the 50 ms DPU timeout and all E Ink guards. He owns that
integration. This audio patch is a separate, **default-off comparison**, not a
claim that the phase5 freeze is caused or fixed by LPASS clocks.

## Concrete change

The existing CCF provider calls `clk_q6dsp_prepare` -> `q6afe_set_lpass_clock`
-> `afe_apr_send_pkt` synchronously while the global sleeping `prepare_lock`
is held. The APR wait has a 3000 ms timeout. The matching unprepare vote also
waits under CCF. This is a real long-lock-holder path even though its causal
relationship to the observed display stall is unproven.

When opted in, the A9 card takes six extra **prepare references** at late probe:
VA MCLK/NPL, TX MCLK/NPL, RX MCLK/NPL. It locates RX/TX among its real ASoC
components and obtains VA from RX's actual `fsgen` clock phandle; VA is not a
backend DAI component on this card. All six existing provider rates must be
19.2 MHz, as selected by this build's macro probes; the patch never retunes
them. The shared dcodec vote is already held across runtime PM by the macro
probe paths and needs no extra reference here.

CCF calls a provider's prepare only on core prepare_count 0->1 and unprepare
only on 1->0. Holding one reference therefore prevents these six DSP clock
providers from sending on/off APR commands during later stream startup,
runtime-resume/autosuspend, and shutdown. SoundWire fsgen/macro preparation
can still run its own MMIO operations, and AFE port start still waits for its
normal acknowledgment. This removes one concrete stream-transition long
CCF holder; it does not remove all global lock use or all DSP waits.

**The initial card-bind votes and final removal votes still hold CCF's mutex.**
They have been moved earlier/later in the card lifetime, not made asynchronous
or moved outside that lock. This is the user-proposed probe-time voting option,
not an unsafe unlock of CCF internals. It can still stall another consumer
during card bind/removal. Do not load/rebind the card inside an active E Ink
drive window. It retains reply-before-use and DSP error/timeout handling.

Every failed clock lookup, unexpected rate or prepare/ACK failure unwinds
prepared references in reverse order and puts every acquired handle. Card
remove releases the hold; an idempotent devm cleanup also covers failed and
deferred registration. Repeated late-probe calls do not add duplicate holds.
Unprepare's existing void API cannot propagate a DSP disable failure; this
patch does not change that upstream limitation or retry blindly.

Clock residency has an **unmeasured idle-power/suspend cost** and can prevent
normal LPASS clock gating. This is for a bounded attended comparison, not the
default production power policy. ADSP restart/SSR with retained references is
not qualified; use a fresh normal boot after such a restart. It does not hold
analog PA, select a route, play samples, or change the n-c pop/gain fix.

## Stock REA comparison

The [reverse-engineer-anything skill](/home/user/.agents/skills/reverse-engineer-anything/SKILL.md)
was used on shipped stock vendor modules, statically, without phone access.

| Stock artifact/function (REA analysis address) | Observation and evidence |
| --- | --- |
| `audio_q6.ko`, SHA256 `3d77c642be8b4bf3c6c534dffa1123cedd258ee8d12e9ec8a21b58e7f11115d9`; `afe_set_lpass_clk_cfg` (0x1152c8) | Takes a private AFE mutex around parameter packing/send. `ev_ca7a8e54dfa9bc9f230ea87373a547131da121adeba036ed54492d0bcf1706bf` |
| `q6afe_svc_pack_and_set_param_in_band` (0x10db70) | Clock module 0x1028f dispatches to `afe_apr_send_clk_pkt` with a waitqueue. `ev_8b8557c299e57547aa4a51c019f59f9205e60aa68952b857124cc77ca40cbecc` |
| `afe_apr_send_clk_pkt` (0x116988) | Sends APR, waits for reply/state change with a 200-tick budget and maps DSP status to error. Tick rate is not inferred here. `ev_9d32b941d8dac6add80a7e3e6b3225aa1e4197fd2fbec65b8e9a79a3dcf63a36` |
| `audio_bolero_cdc.ko`, SHA256 `1f0796f35272bc8e51814dc61cefd5620c8e07d8df26d57843b845056372205c`; `bolero_clk_rsc_request_clock` (0x103078) | Serializes clock/mux ownership under a private resource mutex and refuses enable during SSR. `ev_c67ced5528ddba9d3e5c1494b06f4c65367098b4d1f11635a667c4cd7d04f7fb` |
| `bolero_clk_rsc_enable_all_clocks` (0x102c50) | Uses CCF prepare/enable and disable/unprepare with a resource mutex. `ev_b6b9a38d23a45a309f6bb3d323a62a928069151e109e9e9c89eede095383d25d` |

This evidence supports preserving synchronous acknowledgments and reference
ownership. **It does not prove stock pins all six clocks at card bind**, nor
that stock avoids the global CCF prepare mutex. Our longer hold lifetime is an
explicit mitigation experiment, not a stock-equivalence claim. REA/Ghidra
12.1.4 snapshots are local `audio-output-20261020/stall/stock-{q6,bolero}.rea`;
both analysis sessions were closed. The stock Q6 module was extracted read-only
from the existing `android/ref/vendor_a.img`, member `/lib/modules/audio_q6.ko`.
No firmware/module bytes are committed.

## Integration and morning comparison

Cherry-pick the delivery commit after the pop fix (`eb9a6ff`, already integrated
as `0f019fd`). The normal build and small pair builder stage the new header.
Use the updated machine module and loader. The supplied amplifier is unchanged
source from the pop fix; keep a matching amp exporting `a9_aw87519_set_stream`.
Preserve enforcing/memfd policy, volume configuration and agent's display
change. No default kernel/DT clock provider or E Ink guard is modified.

With no boot flag, the new module parameter `lpass_clock_hold` defaults to 0 and
the loader passes no new argument. To opt in on a **separate candidate boot**,
append `androidboot.a9_audio_clock_hold=1` to that boot image's command line.
Init exposes `ro.boot.a9_audio_clock_hold=1`, and `a9ml-audio.sh load` passes
`lpass_clock_hold=1` only to the machine module. The parameter is read-only
after loading (0444); an already-loaded card is deliberately not changed.
Do not use `setprop` or a live rebind to pretend the hold became active.
Rollback is the same module/loader with the boot flag omitted on a fresh boot.

From the coordinator's existing root console, read these first (worker does
not execute them):

```sh
getprop ro.boot.a9_audio_clock_hold
cat /sys/module/snd_soc_a9_audio/parameters/lpass_clock_hold
getprop vendor.a9.audio.ready
cat /proc/asound/cards
dumpsys media.audio_policy
dmesg | grep -E 'A9-CARD LPASS clock hold|q6afe.*(error|timeout|failed)'
```

Default comparison must show parameter `N`; opt-in must show `Y`, card ready1,
nonnull APM and `A9-CARD LPASS clock hold ready: 6 prepare references`.
A flag alone does not prove the hold. A failed opt-in fails card binding rather
than silently claiming success. The boot flag's property visibility under the
actual enforcing policy still needs this readback.

1. First validate agent's phase5 candidate with this mitigation **off**.
   Use ordinary low-volume media start/stop, then continuous playback plus the
   same frontlight/screen activity. Keep the n-c digital/PA gains unchanged.
2. If comparing the clock mitigation, use the same display candidate on a fresh
   opt-in boot. Arm agent's existing first-cutoff capture; no competing trace
   owner. Compare the same actions and confirm no pops, working volume keys,
   and stable card/APM. Keep the existing E Ink rail/watchdog/retry limits.
3. If his bounded capture supports it, add entry/return probes on
   `clk_q6dsp_prepare`, `clk_q6dsp_unprepare`, and `afe_apr_send_pkt`. After the
   hold-ready log, those six MCLK/NPL providers should not send clock-vote APR
   commands at stream transitions; unrelated port-start APR is expected.
   Compare measured holder intervals and display completion, not just log order.
4. Record a short screen-on idle current/temperature sample against flag-off,
   and a bounded suspend/resume observation only under coordinator scope.
   No long unattended hold or power-equivalence claim. End the comparison with
   a flag-off boot. Earpiece qualification remains separate.

A continuous-playback phase5 stall with the hold active disproves this hold as
a sufficient fix. Missing CCF events after a trace armed too late prove nothing.
Return sanitized first-cutoff phase, CTL_FLUSH/vsync timing, blocking stack and
matching unlocker; keep raw private traces with the coordinator.

## Host validation

`python3 scripts/mainline/audio/output/test-clock-hold.py` exercises production
hold functions with modeled CCF first/last-user transitions: balanced/idempotent
ownership, 100 simulated stream cycles without extra provider APR, pre-existing
users, every get/rate/prepare timeout failure and retry, and missing/wrong DT
topology. It also rehearses the actual loader branch under Android mksh/QEMU
with fake module/control paths for default, opt-in, malformed flag, already
loaded and insmod failure. No real module syscall/device access is made.

Module compile/import CRC/vermagic checks cover the real kernel types; the
mocked CCF model does not prove DSP replies, electrical stability, the cause of
the phase5 wait or physical freeze prevention. Morning device qualification
remains with the coordinator.
