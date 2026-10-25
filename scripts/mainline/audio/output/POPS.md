# Speaker startup and shutdown candidate

Superseded for the 2026-10-22 FIX2 candidate: the sequence below required a
backend START before enabling the amp. FIX2 removes that dependency and the
machine gain ramp. It selects `aw87xxx_mode_switch_a=Music` before opening
speaker PCM, matching stock device selection, and selects Off during route
cleanup. DAPM power-down still resets the amp; a subsequent power-up restores
the selected Music scene. Probe and capability queries leave the amp off.
Register writes make up to five attempts with a 2 ms sleep after each failure;
the final failure still aborts and resets. RX2 defaults to index75 (-9 dB),
matching the stock QRD XML. The stock tuning is unchanged. This may change
transition pops; neither sound restoration nor pop reduction is proven.
See [live diagnostics](LIVE-DIAGNOSTICS.md) for the FIX2 build and capture.
The remaining sections describe the earlier candidate and its evidence.

The coordinator confirmed framework YouTube speaker sound on integrated N/n-a,
with pops around stream transitions and two E Ink cutoffs 0.3–0.5 seconds after
audio prepare/start. This change addresses speaker sequencing. Its audible
effect and the cause of the E Ink stalls still require the coordinator's test.
Earpiece qualification remains pending.

Previously the AW87519 replayed its stock Music register pairs, including PA
enable, in DAPM PRE_PMU. That occurs during backend prepare, before the MM1
frontend submits ASM RUN. Shutdown reset the amplifier in POST_PMD, after its
upstream WCD path could already have lost bias. The HAL removes its route only
after PCM close, so userspace route ordering alone cannot cover those events.

The machine and amplifier modules now work together:

1. Before SoundWire prepare, save the configured RX2 gain, reset the external
   amp if necessary, and attenuate RX2 to its supported minimum, -84 dB.
2. DAPM PRE_PMU only arms the external amp. Probe, route selection and prepare
   leave reset asserted. WCD/SoundWire/AFE keep their existing order.
3. Qcom's non-atomic FE_PRE ordering submits MM1 ASM RUN before the backend
   START callback. That callback uses the existing reset timing and unchanged
   stock tuning to enable AW87519, waits 1–1.5 ms, then ramps RX2 in at most
   6 dB increments, 300–500 us apart, up to the saved gain.
4. Backend STOP/pause/suspend ramps down to -84 dB and resets the amp before
   the frontend submits EOS/PAUSE. The AW AMP widget is an output driver (DAPM down stage 5), so
   PRE_PMD provides reset fallback before WCD AUX PGA (down stage 6). HW_FREE/shutdown restore the saved control value with
   the amp off. Read/write failures reset the amp; no successful retry depends
   on stale powered state.

ASM RUN is asynchronous: submission is not DSP acknowledgment or proof of a
consumed frame. A few initial milliseconds can be attenuated. At the existing
framework gain 54 (-30 dB), the ramp has nine steps (2.7–4.5 ms of requested
sleeps). I2C, DSP and scheduling latency have no hard bound here. These sleeps
are permitted by the existing non-atomic PCM links; they are not busy loops.
RX2 gain accesses use the macro's MMIO regmap and check read errors. The ramp
does not deliberately hold CCF/regulator locks, although the I2C controller's
runtime resume can prepare clocks.

No stock tuning bytes, gain increase, guessed amp soft-mute register, WCD
analog setting, SoundWire port, audio policy, Bluetooth, SELinux or E Ink change
is included. The working HAL's mixer/open/query behavior is retained. A normal
prepare cannot energize this external amp; it still prepares the upstream WCD
path. Thus codec bias or rail transients may remain even if the audible pop is
reduced.

## Stock evidence

Using the [reverse-engineer-anything skill](/home/user/.agents/skills/reverse-engineer-anything/SKILL.md),
the private stock `audio_aw87xxx.ko` (SHA256
`9eddb3b6ad86907268975a1e494d1b773c5c81993daa025f9a4550b2bac0e391`)
was examined statically with REA/Ghidra 12.1.4. `aw87xxx_hw_off` at analysis
0x100aa0 asserts GPIO reset; `aw87xxx_hw_on` at 0x100b48 toggles reset;
`aw87xxx_scene_reg_update` at 0x100790 serializes register-pair replay.
The scene function alone shows no digital ramp. This supports retaining the
existing tuning/reset shutdown rather than inventing register writes; it does
not reconstruct stock HAL/ACDB timing or prove every stock device variant.
Local evidence is in `audio-output-20261020/pops/stock-amp.rea`.

## Coordination note for agent: E Ink audio-start stall

The audio source gives a concrete global sleeping-lock holder candidate:

```
clk_prepare / clk_core_prepare_lock
  holds CCF prepare_lock across clk_core_prepare -> provider .prepare
    clk_q6dsp_prepare -> descriptor lpass_set_clk -> q6afe_set_lpass_clock
      afe_apr_send_pkt: AFE mutex + synchronous APR wait, timeout 3000 ms
```

Unprepare has the corresponding synchronous clock vote too. The full ALSA
prepare interval is not a global-lock interval: this chain specifically is
inside CCF's mutex. The separate CCF enable lock is a spinlock; no synchronous
APR wait was found in these provider enable callbacks. Source locations in the
shared kernel are `drivers/clk/clk.c` (prepare_lock / clk_core_prepare),
`sound/soc/qcom/qdsp6/q6dsp-lpass-clocks.c` (clk_q6dsp_prepare/unprepare), and
`q6afe.c` (afe_apr_send_pkt / q6afe_set_lpass_clock).

This does **not** establish that a drive-frame DRM commit actually waited on
that mutex. agent's `scripts/mainline/eink/audio-stall/capture.sh` already
captures contention stacks, matching unlockers, clock events and the first
cutoff. If supported, optional entry/return probes on `clk_q6dsp_prepare`,
`clk_q6dsp_unprepare`, `afe_apr_send_pkt` and `a9_aw87519_set_stream` would link
the audio holder interval to that capture. Keep probes bounded and in its
existing owned instance; do not run a second competing capture. Clock
tracepoints occur after lock acquisition, so missing events alone cannot
exclude lock wait. The shipped config has KPROBE_EVENTS, but FUNCTION_TRACER,
FUNCTION_GRAPH_TRACER and LOCK_STAT are disabled; do not prescribe those without
rebuilding the kernel.

The AW87519 speaker amp is on **I2C1 at 0x58**, not the vibrator's I2C0/0x5a.
WCD937x uses SoundWire. There is no direct audio transfer on FP9929A's I2C0/0x48
in this route. Indirect runtime-PM/clock dependency remains possible. SoundWire
FIFO retry uses sleeps; its threaded IRQ loop remains an IRQ-duration/count
trace target if contention does not explain the gap. No observed IRQ storm or
PA-induced rail dip is claimed. Correlate the first PG fault before cutoff;
PG low after emergency-off is not evidence of an earlier dip.

This commit moves PA current onset out of prepare and gives it attenuated
input, but does not eliminate upstream clock votes or prove a freeze fix.
Keep E Ink watchdogs, rail guards, recovery budgets, priorities and IRQ affinity
unchanged while the first blocking dependency is captured. Do not move an
unknown sleepable DRM operation into hardirq context.

## Host verification and integration

`python3 scripts/mainline/audio/output/test-speaker.py` compiles the production
amp lifecycle and machine ramp/trigger functions with mocked GPIO/regmap/ASoC.
It checks prepare without PA power, prepared-state gating, repeated START,
pause/resume/stop ordering, all supported gain targets, invalid gain rejection,
and injected amp/RX I/O failures and retry. This proves the tested local state
machine, not kernel callback timing, electrical behavior or audibility.

`output/build-card.py` now builds **both** modules in one private external
module tree, verifies imported CRCs, their new shared export CRC and vermagic,
and preserves the pinned base config/symvers. `build-a5.py` stages the shared
header for a normal integrated module rebuild too. Its dependency ordering
already places the amplifier before the machine. Replace both together;
the new machine cannot load against the old amplifier module.

The small artifact overlay is rooted at `/vendor`, containing only:

```
lib/modules/snd-soc-a9-audio.ko
lib/modules/snd-soc-a9-aw87519.ko
```

Prefer cherry-picking the delivery commit into `integ/20261021n` and building
against that integration's prepared kernel ABI. The provided pair is checked
against build-m's pinned ABI; verify the integrated ABI and dependencies if
using the binaries. Preserve the integrated enforcing/memfd policy, volume
config, current recovery and other subsystem payloads. No full vendor rebuild
or flash is performed by this worker.

On the next attended coordinator run, use the existing low-volume framework
setting and an ordinary media start/stop while the existing E Ink capture is
armed. Confirm boot/card/HAL/APM first, then whether sound still works, whether
start/stop pops change, and the first drive-gap/PG/lock timing. Do not raise
hardware gain as part of this comparison. The pending r6 earpiece test is a
separate observation; this patch changes only the speaker backend.
