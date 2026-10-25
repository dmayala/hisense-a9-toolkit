# A5 integrated audio POC

Host-built and hardware-untested. This adds the A9 WCD937x speaker/earpiece/mic
backends and ESS headset backend to R4's APR MM1/MM2 frontends. It reuses the
installed Android16 AIDL primary HAL with ALSA device addresses. Output routes
start disabled; no script here starts playback or automatically enables one.

## Host build

```sh
python3 -I scripts/mainline/audio/build-a5.py --vendor
# Or the older entry point, now delegated to the same external build:
python3 -I scripts/mainline/audio/build-poc.py --vendor
```

Default output: `~/A9-build/mainline/audio-20261020/`. Only prepared headers,
tools, config and symbol versions are copied privately; only external modules
are compiled. Config, kernel Image and original symbol CRC table remain
unchanged. The private prepared tree and external objects are
removed after success. No 20 GiB admission requirement remains. Keep the final
boot/vendor, `overlay-r2`, logs, `a5-build.json` and `A5-SHA256SUMS`.

`--package-only` reuses this output's built `overlay-r2/lib/modules` and refreshes
tools/DT/policy. `--boot FILE` applies audio to a cumulative boot image. It retains
that kernel/ramdisk and releases only audio's TLMM65/66/68/69/92/107 reservations.
`--base-obj DIR` selects an existing compatible prepared object tree;
`--kernel-source DIR` selects its matching source (defaults to the object's
source link).

The integrated command now accepts audio:

```sh
scripts/mainline/build-integrated.sh --audio --out ~/A9-build/mainline/integration-audio-20261020
```

Integration always compiles the current audio drivers and Q6AFE patch from
source, adds the DT and overlay before vendor refresh, and includes the modules
in the final snapshot. The old `--audio-build` argument remains accepted but
its generated module cache is no longer copied. Standalone `--package-only`
remains an explicit cache-reuse option and does not produce a fresh-module
receipt. `--without audio` omits the step.

After all vendor mutations, integration checks the final card and Q6AFE module
hashes and vermagic against this run's fresh build, and the helper hash against
current source (including the gain84 correction). A missing fresh build,
stale cached module or later overwrite fails the build. Evidence is
`audio/a5-build.json` (`fresh_modules`) and `audio/vendor-check.json`.

## Coordinator: silent phone checks

Only the coordinator runs these commands, after booting the matching image via
the existing slot-B path. Let the existing sensors service own ADSP, firmware,
QRTR and mapper setup. Do not stop/restart ADSP for audio.

```sh
/vendor/bin/a9ml-audio.sh load
/vendor/bin/a9ml-audio.sh inventory
/vendor/bin/a9ml-audio.sh probe
```

Expect card `Hisense-A9-Audio`, MM1 at PCM0, MM2 at PCM1, and WCD/ESS controls.
`probe` opens/closes PCM0 with zero samples and no HW_PARAMS, PREPARE or START.
All six endpoint switches remain off after registration. Amp reset stays off;
its hardware ID is first read when its DAPM playback path powers up.

To check the main microphone unattended (capture is authorized):

```sh
/vendor/bin/a9ml-audio.sh mic
/vendor/bin/a9ml-audio.sh capture-levels
/vendor/bin/a9ml-audio.sh off
```

The capture helper reads three seconds of 48 kHz mono from PCM1 and prints only
peak, mean and mean square; it stores no recording. Compare quiet and speech/tap
levels. A completed capture or nonzero levels alone do not prove mic placement.

The primary policy is read at HAL startup. If the HAL initialized before the
new card was loaded, the coordinator can restart its existing service, with all
output switches still off:

```sh
setprop ctl.restart vendor.audio-hal-aidl
```

Then test a framework recorder separately. MM2 capture is selected by the
built-in mic address `CARD_0_DEV_1`; no capture-PCM property/source patch is used.

## Coordinator: attended output only

User present; headphones off the ears. Stop media before selecting or changing
a route: Q6 routing binds the backend at PCM prepare. Close/reopen the stream
for every route change.

```sh
/vendor/bin/a9ml-audio.sh speaker --user-present
/vendor/bin/a9-audio-pcm tone 0 Hisense-A9-Audio --user-present
/vendor/bin/a9ml-audio.sh off
```

The tone is one second at about −60 dBFS. The route sets Android music volume to
index 1; WCD RX digital gains start 30 dB below nominal. After a direct ALSA pass,
test Android media at that low volume. `headphone --user-present` selects ESS;
`earpiece` and `headphone-wcd` select their WCD paths. Every output route requires
the explicit attendance argument. `off` disconnects outputs and inputs.

Android still sees the ordinary primary Speaker port while the manual POC route
selects the physical endpoint. Automatic wired-device policy, hot switching of
an already-running PCM, telephony and full-duplex voice are not implemented.
The HAL's existing ALSA path is used; the audio APEX/effect service remains intact.

## Implementation and limits

- `poc-kernel/lpi-static-groups.patch` exposes SM6115's existing static pin groups
  without enabling R4's absent `CONFIG_GENERIC_PINCTRL_GROUPS` or changing its
  built-in pinctrl structure layout.
- SoundWire runtime-version selection/timing comes from the existing D2 work.
  The general upstream WCD driver retains ADC/MBHC functionality; an A9-specific
  compatible uses stock's three L9/L14 supplies, with 1.8 V micbias settings.
  No invented external fourth micbias regulator is installed.
- Main mic follows stock AMIC1 → ADC1 → TX SMIC0/DEC2 → TX codec DMA3 → MM2.
  Speaker follows RX codec DMA1 → RX2/AUX → AW87519. Earpiece and WCD headphones
  use DMA0. ESS uses secondary MI2S with a 48 kHz stereo S32 backend.
- The amp driver requests the private stock pid59 Music file, checks ID 0x59,
  replays its 42 register/value bytes, and resets off on failure/shutdown/DAPM
  power-down. The firmware is copied locally and added to the existing ADSP
  firmware manifest; no tuning bytes are committed. Stock's adaptive battery/
  vmax monitor is not ported; start with short attended playback tests.
- Exact physical output, SoundWire hardware version, clock/DSP compatibility,
  mic response and ESS selector behavior remain phone tests. No A6L PM660 or
  TFA9894 values/binaries are used.

HAL behavior was checked in official Android16
[StreamPrimary.cpp](https://android.googlesource.com/platform/hardware/interfaces/+/refs/heads/android16-release/audio/aidl/default/primary/StreamPrimary.cpp):
`getCardAndDeviceId` accepts `CARD_n_DEV_m`. The shipped service binary contains
the same format and simulation-property names. Existing emulator-oriented
buffering remains; framework capture quality is unverified.

Host validation: external modules compile/link with unchanged config/symbols;
DT overlay compiles/applies; integrated audio packaging/refresh/fsck passes.
Three route cases pass with the actual Android mksh under QEMU and fake mixer
commands, including no writes for an unattended speaker request and no output
route writes in the mic case. No device or playback command was executed.

## Integrated sensors firmware fix (build d)

Build d failed sensors startup at `fail:firmware-copy` before its module or SNS
steps. Its inherited R4 starter verifies `a9adsp.SHA256SUMS` through
`/mnt/a9-wifi/firmware/qcom -> /vendor/firmware/qcom`. Audio appended the 42-byte
`aw87xxx_pid_59_music_0.bin` to that manifest, but staged it only under
`/vendor/firmware/a9adsp/qcom`. All 39 original firmware entries matched; only
the new amp tuning file was missing through the shared bridge.

The A5 builder now stages identical tuning under both firmware roots. The
standalone copy path and integrated shared bridge can both satisfy the same
manifest. No sensor starter, ADSP ownership, calibration, audio route or
manifest entry is changed. `--package-only` and the integrated audio cache path
both refresh this file, so cached modules do not retain the broken layout.

## SoundWire registration fix (build j -> k)

Build j lacked `slimbus.ko` (`soundwire-qcom` imports `slimbus_bus`) and
`regmap-sdw.ko` (WCD937x imports `__devm_regmap_init_sdw`). The A5 builder now
recursively stages every audio module's `modinfo depends` from `--base-obj`,
including in `--package-only` mode, and writes a complete dependency-first
`modules.audio-poc`. `a9ml-audio.sh load` uses that order; the machine driver is
last. `audio-module-dependencies.json` records the closure.

The coordinator's build-j/build-k COMP_PARAMS readback requires TX **4 DIN / 0 DOUT**
and RX **1 DIN / 6 DOUT**. `qcom_swrm_get_port_config` in
`drivers/soundwire/qcom.c` extracts both counts from `SWRM_COMP_PARAMS` with
`FIELD_GET(SWRM_COMP_PARAMS_DIN_PORTS_MASK, val)` and the corresponding DOUT
mask, then checks DIN before DOUT. Build j stopped at RX DIN; only build k
reached and reported RX DOUT=6. TX enumerated its WCD9370 on build k.
Stock A9 DTBO entry SHA256
`74138d6535db5897db0c80ae093dc5840e8c51cf578b5d395499ded185069931`,
fragment@1's `va-macro@a730000/va_swr_master` and
`rx-macro@a600000/rx_swr_master`, maps only TX ports 1..3 and RX ports 1..5.
Active mappings/timing bytes are preserved. Each of the nine per-port arrays
now has DIN+DOUT entries, with `0xff` for unmapped TX4 and RX6/RX7.
The sentinel is the upstream qcom driver's `SWR_INVALID_PARAM` convention.

The earlier count choices matched the three-port VA and five-port RX examples
in [upstream SM8250](https://github.com/torvalds/linux/blob/master/arch/arm64/boot/dts/qcom/sm8250.dtsi).
They describe active stock ports, not the extra ports reported by this A9's
v1.6 controller. [Upstream SM6115](https://github.com/torvalds/linux/blob/master/arch/arm64/boot/dts/qcom/sm6115.dtsi)
does not currently supply SoundWire port-count nodes. Controller readback takes
precedence over either SoC example; the driver's mismatch check remains intact.

The missing MBHC button-threshold message is nonfatal: upstream
`wcd_dt_parse_mbhc_data` installs defaults and returns zero. Stock DT does not
provide a matching threshold array, so this fix does not invent button voltages.
Card registration, enumeration and silent capture remain coordinator tests;
no playback is started by packaging or loading these modules.

Build k3 uses the canonical `/sound` node with the complete A9 seven-link graph,
`compatible="hisense,a9-audio-card"` and explicit `status="okay"`. Older A5
boots instead had a disabled legacy `/sound` and the A9 graph at `/sound-ess`
(absent status already means enabled). Packaging removes that old A9 node when
reapplying the overlay. This removes the node-name ambiguity; if the card still
does not register, inspect the machine device's deferred-probe reason rather
than interpreting the old `/sound` compatible as the active A9 graph.
Q6AFE `Unknown cmd 0x100f6` names
`AFE_CMD_REMOTE_LPASS_CORE_HW_DEVOTE_REQUEST`, a hardware-clock vote release;
the basic-response switch lacks that case. It does not itself prove a missing
DT card node or the cause of card-probe deferral.

### Debugging a bound machine device with no ALSA card

In this kernel, `sound/soc/soc-core.c:snd_soc_bind_card`, at `probe_end`, converts
`-EPROBE_DEFER` into zero after adding the card to ASoC's `unbind_card_list`.
This is separate from driver-core `devices_deferred`. The platform driver can
therefore bind successfully while `card->instantiated` remains zero. Component
registration later calls `snd_soc_try_rebind_card` to retry the pending card.

The A9 module now emits `A9-CARD` info/error messages for entry/allocation,
DT parse result, every CPU/codec DAI, platform/aux component, backend callbacks,
card probe, jack setup, late probe, pin disable/sync, and registration result
including `instantiated`, runtime count and ALSA-card allocation. The DAI lookup
uses the exported mutex-taking API. Platform/aux lookup covers platform and I2C
devices; `MISSING/unresolved` also permits a different bus and is a diagnostic,
not grounds to drop the dependency. Internal ASoC retry behavior is retained.
No component/route is removed on a guess, and no output is enabled by diagnostics.

Build k4 explicitly enables `amplifier@58`: the base board had left it disabled,
which prevented the aux component's I2C client from existing and kept the card
on ASoC's internal pending list even though all seven links resolved. GPIO107
remains released from TLMM reservations and specified as the active-high amp
enable/reset line. The amp probe requests `GPIOD_OUT_LOW`; DAPM alone raises it
for playback, and power-down/failure/shutdown drives it low again.

### k4 registration failure: frontend component order

The extracted k4 DT orders links TX4, TX3, RX1, RX0, MM2, MM1, ESS. Within a
component probe priority, `soc_probe_link_components()` follows runtime/link
order. It therefore reaches q6routing before q6asm-dai creates its `MM_DL1..8`
and `MM_UL1..8` widgets. The saved log's 528 failed routes are 65 playback
mixers times eight missing MM_DL sources, plus eight missing MM_UL sinks.
`snd_soc_dapm_add_routes()` returns `-ENODEV`; `soc_probe_component()` propagates
it and `snd_soc_bind_card()` emits `failed to instantiate card -19`. This is
before aux component probing or DAI/link initialization. The amp ID read only
happens at playback, and ESS's successful I2C registration is not this failure.

The machine driver now orders dynamic frontends first, by ID, preserving backend
order and complete link structures. MM1 precedes MM2 for stable PCM numbering.
This requires only replacing `snd-soc-a9-audio.ko`, with the existing k4 DT and
codecs. Additional `A9-CARD` messages show component probe order, every link init
and aux init; optional amp/ESS replacements emit `A9-AMP`/`A9-ESS` I2C and
component/DAI probe results. For a coordinator-owned live replacement, remove the
machine module first; if replacing codecs too, load them before the machine.
Expect frontend order MM1/MM2, then aux/link init, late probe and
`snd_soc_register_card rc=0 instantiated=1`. A later error is still a separate
hardware finding; the host build does not prove card registration or playback.

### Silent MM2 capture and control diagnostics

An unrouted playback FE rejects even an open-only `probe` with `EINVAL` and
`no backend DAIs enabled`; the probe intentionally does not create a route.
Card registration and an unrouted PCM open are separate checks.

The mic helper makes ten writes, printing the control name, requested value,
and before/verified-after readbacks for each. Its route is Main Mic → AMIC1 →
ADC1/ADC1 REQ/ADC1_MIXER → ADC1_OUTPUT → TX SWR_INPUT0 → TX SMIC MUX2
(SWR_MIC0) → TX DEC2 MUX (SWR_MIC) → TX_AIF1_CAP Mixer DEC2 → TX_AIF1 Capture
→ TX_CODEC_DMA_TX_3 → MultiMedia2 Mixer → MM_UL2. The DT supplies Main Mic from
MIC BIAS1; the macro graph supplies DEC2 from TX_MCLK and SMIC2 from TX_SWR_CLK.
These supply widgets power with the capture route, rather than extra mixer
clock writes. SoundWire's master takes its port/channel masks from the WCD
slave's selected ADC1 port; those masks are distinct from codec DMA masks.

The original machine code did not transfer the TX macro's selected decimator
mask to the CPU DAI. For mono DEC2, the macro reports mask `0x4`, but Q6AFE's
implicit mono mask is `0x1` (DEC0). The machine now reads the SM6115 TX macro
DAI's map during hw_params and calls the CPU's set_channel_map. A second fix
in `poc-kernel/q6afe-dma-mask.patch` copies an explicit mask into the Q6AFE wire
config: the original packer handled only the implicit-mask case and otherwise
left the old/zero payload value. Both module fixes are needed. This is a source
defect consistent with capture making no progress; a new hardware run must
still verify that it explains the saved timeout.

`a9ml-audio.sh dump` prints every supported readable mixer control's values;
`non-default` prints nonzero booleans/integers and enum indices other than zero,
including every channel and enum labels. ALSA does not expose hardware reset
defaults, so this is explicitly a zero/index-zero filter, not a claim about
reset values. The mic helper prints this dump after setup, and capture-levels
prints it before and after capture, retaining a capture failure's exit status.
No tinymix installation is needed: the rebuilt a9-audio-ctl is static Android
arm64. Output commands retain their attendance requirement.

PCM diagnostics print HW_PARAMS, SW_PARAMS, PREPARE and START results. Transfer
timeout reports completed frames and ALSA state/hw_ptr/appl_ptr/avail/delay; its
five-second capture deadline starts after prepare/start, so setup time cannot
consume the transfer budget. Backend logs show startup, the macro and CPU DMA
mask, SoundWire prepare/enable and whether the stream became prepared. Q6AFE
logs the packed DMA mask and device-start result. Expected DEC2 capture is
count=1/mask=0x4, SoundWire prepared=1, DMA start rc=0, then 144000 frames.

The control/PCM tools and shell helper can be replaced live on k4. The module
pair also matches k4's ABI, but Q6AFE is shared with q6afe-dai/q6afe-clocks;
its users must be unloaded before it can be replaced. If the current graph
keeps those clock providers busy, collect the tool diagnostics with the working
f1b3f9d module and carry the pair in the next vendor. No DT/k5 boot is required
for these changes. A fresh build-a5.py module build includes the patched Q6AFE;
package-only continues to reuse the caller's module cache.

The coordinator's first live Q6AFE reload on k4 failed clock votes for hardware
block4, then LPASS pinctrl/VA macro clock enable timed out and card parsing
deferred. The DMA patch does not change vote/unvote request packing, callback,
APR send helper, sysclk or probe initialization. A host comparison against the
Q6AFE module extracted from vendor k also found those six native functions
unchanged after normalizing relocated branch addresses. This does not prove
live reload is impossible or identify the DSP state failure. Test the same
module pair from cold boot in vendor k5 with boot k4 to separate that question;
retain the clock-vote failure evidence if it persists.

### k5 running capture with near-zero levels

The k5 result completed all setup/start stages and 144000 captured frames, but
reported peak1 even with desk vibration. Its readback shows the mic helper
writing `TX_DEC2 Volume=0`, while the unused decimators retain84. This is an
ALSA-index error: `lpass-tx-macro.c` declares `SOC_SINGLE_S8_TLV` with signed
hardware range -84..40 dB. `soc_mixer_ctl_to_reg()` adds that minimum to the
user value, so ALSA0 writes -84 dB and ALSA84 writes 0 dB. The driver's TLV
also marks its minimum as a mute step. The helper was attenuating the active
decimator by84 dB, consistent with the observed near-zero samples.

The mic helper now sets84 (unity input gain). This is a script-only live fix:
with capture stopped, the coordinator can use the existing control tool to set
`TX_DEC2 Volume` to84, then rerun capture-levels. Replace the helper so `mic`
does not restore0. Boot k4, vendor k5, modules and other controls stay the same;
no vendor k6 is needed for this correction. A new silent capture must still
confirm microphone response; low levels alone did not establish micbias or
SoundWire-port failure. No output route or playback is involved.
