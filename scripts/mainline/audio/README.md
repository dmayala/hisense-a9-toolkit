# A9 offline audio integration

All outputs remain development artifacts. No device or network access is needed
or performed by these tools. Audio is default off (`A9_AUDIO` unset/0). Wi-Fi
owns QRTR and the mapper lifecycle; this folder installs no daemon or automatic
route, remoteproc-start, or module-load service. The ADSP remoteproc node is
`disabled` in every audio DT unless `--enable-adsp` is given (review 33 F2): the
Wi-Fi build loads the same `qcom_q6v5_pas` module for MPSS, and PAS boots every
enabled PAS node on load. Only an `--enable-adsp` DTB lets PAS start ADSP.

The audio DT shrinks `pil_adsp_mem` to the stock 0x1e00000 but keeps
0x55600000-0x56000000 no-map (stock IPA fw/GSI and GPU zap regions plus a tail,
identical to the gpu branch's nodes; review 33 F1). Every build runs
`dt-reserved-audit.py` on its DTB against the stock base DTS (`--stock-dts`,
default `~/A9-build/mainline/sol-pmic/evidence/base_0.dts`) and the plain Android
DTB of the same tree, and fails if a stock reservation is uncovered, regions
overlap, a baseline reservation is released or the ADSP status is not the one
requested. `tests/test-dt.py` runs the same audit on every DT variant plus a
negative control (the pre-review-33 DT must fail).

`A9_AUDIO=1 python3 scripts/mainline/audio/build-kernel.py --es9218` builds the
experimental codec/card with disabled ESS nodes. Add `--enable-es9218` only to
produce an explicit probe-enabled DT for a later attended session. The tool
archives the local v7.2.10 tag, applies this checkout's numbered kernel patches,
and modifies only its private `--work` tree. It never fetches. Set the local
cross compiler in PATH and CROSS_COMPILE if the default is unavailable. The
standalone builder leaves global display/vendor/Wi-Fi builders untouched.
`--macros` produces an experimental macro-probe DT; it does not enable the
unresolved WCD/SoundWire board path. No audio feature is selected in normal builds.

Default work: `~/A9-build/mainline/sol-audio/build/mainline`. Completed builds
record source/config/DT/Image hashes, every built module hash and Module.symvers.
`--configure-only` cannot produce a releasable snapshot. The ESS backend is
48kHz stereo S32_LE; MM1 permits S16/S24/S32 frontends, with DSP conversion still
requiring phone verification. Read [README-es9218.md](README-es9218.md) for the
recovered sequence, strict errors and unresolved hardware facts.

After a completed ESS build, `snapshot.py --build WORK --dtb COMPILED_DTB
--out PRIVATE_SNAPSHOT` freezes its kernel, configuration, DT and dependency
closure. `stage-vendor.py --snapshot PRIVATE_SNAPSHOT --assets PRIVATE_STOCK
--binaries PRIVATE_TOOL_BUILD --stage PRIVATE_VENDOR_ADDON` preflights and stages
only that audio add-on, stock split ADSP firmware and the static control utility.
Both require `A9_AUDIO=1`; all binary/firmware outputs must be outside Git checkouts,
a9-assets and shared storage. Empty .git placeholders are not repositories;
nonempty metadata, pointer files and bare repositories are rejected. Existing
payloads must match bytes and modes. An existing display modules.load, policy,
APEX, init and properties remain untouched. No stock module is installed.

`modules.audio` is an explicit dependency-ordered list with PAS last. It is
separate from display modules.load. Install the selected mapper first and mount
vendor firmware before any later attended module loading. Mapper mode defaults
to kernel; use `A9_WIFI_PD_MAPPER=userspace` consistently for audio and Wi-Fi to
omit the kernel mapper. Never run both. Combined display/Wi-Fi/audio delivery
requires one common kernel/config/modules build; do not mix same-release modules
from different snapshots. The add-on is not a packed vendor image or Android HAL.

Offline tests: `make all android test`, `python3 tests/policy_test.py`,
`python3 tests/firmware_test.py`, `python3 test_snapshot.py`, `python3 test_builder.py`, the sequence C harness,
production callback extraction and DT variants under tests/. All fixtures are
host-only; none opens a sound device. Keep logs and build outputs outside Git.
The YAML binding is a local draft; full upstream dt-schema validation is pending.

# A9 offline audio userspace staging

This is an opt-in bring-up foundation, not a working audio claim. Kernel ADSP,
SoundWire/WCD937x, backend paths, amp handling and attended tests must succeed
before an audio card or runtime routing service is enabled. The control utility starts no
ADSP, owns no QRTR/pd-mapper service and stages no firmware; the explicit private
packaging helpers above copy firmware without installing runtime actions. Wi-Fi owns shared
QRTR/pd-mapper integration.

## Implemented and built offline

`a9-audio-ctl.c` directly uses the standard ALSA control ioctl ABI, the same ABI
that tinyalsa uses. It avoids a dependency on Cuttlefish's APEX-private
`libtinyalsav2.so`; the checked Cuttlefish VENDOR tree has no libaudioroute.
`make android` builds a static aarch64 Android API30 executable with cached NDK27
and emits `$(OUT)/SHA256SUMS` containing only the Android binary receipt.
`make all test` compiles the host utility and runs an injected-ioctl fixture:
missing/ambiguous/disappeared controls, rejected ranges/enums/implicit multivalue
writes, explicit stereo validation, partial write failure, mismatched second
channel readback, identity changes and extreme signed integer ranges. Tests never open a sound device.
`python3 tests/policy_test.py` checks the restricted candidate policy.

The utility accepts an explicit card index and requires exact card-name identity
for every operation. `list`/`get` open the control read-only. `set` changes exactly
one unambiguous scalar control and immediately reads it back. Explicit
`set-stereo CONTROL LEFT RIGHT` accepts only a two-element INTEGER mixer control,
validates both channel values before any write, then writes once and verifies both
values and full control identity. It is suitable for the two-value
`ES9218 Digital Volume` control; raw values must follow the driver semantics,
without assuming a dB mapping. Scalar `set` still rejects multi-element controls. A nonzero exit means
a requested value did not verify. It does not implement an atomic route, restore
an earlier value after failure, or guarantee mute: a failed set can have changed
the hardware. It is intended only for a later attended session, after exact
mainline names are captured. No runtime commands were executed for this task.

## Candidate policy and HAL source work

`primary_audio_policy_configuration.a9-candidate.xml` restricts normal PCM to
48kHz stereo playback and 16/48kHz mono capture. It removes Cuttlefish offload,
MMAP, FM and telephony declarations from this prospective primary module. It is
NOT installed automatically: declaring a policy route cannot create a DSP path.
It leaves r_submix/Bluetooth modules in the surrounding Cuttlefish policy alone.
Do not claim wired-jack support until the actual AIDL primary module accepts
analog external device connections and Android jack detection is wired.

`hal-patches/*.patch.in` are source proposals adapted from the A6L reference:
input PCM property `ro.vendor.a9ml.audio.capture_pcm` (candidate value 1 ONLY once
MM2 is verified at PCM1) and analog headset/headphone/mic connection acceptance.
They are intentionally NOT ready patches: the cached A9 filesystem contains no
`hardware/interfaces/audio/aidl/default` source checkout, and the external-jack
hunk was based on an already modified A6L tree. Rebase the small changes against
the exact Android16 source revision and build the complete matching audio APEX,
including AIDL V4 generated interfaces and its dependency closure. Never drop an
A6L binary into the Android16 Cuttlefish APEX: old interfaces/APEX signing are not
ABI provenance. Existing service/effects should remain together.

A real routing daemon should use verified A9 controls and own the full state:
headphones/earpiece on RX codec DMA0; loudspeaker on RX codec DMA1 into WCD AUX and
the AW analogue PA; mics through WCD ADC/SoundWire into the appropriate macro.
The stock XML controls use downstream TX names and are NOT an installable
mainline mixer profile. No profile is staged until VA/TX wiring is settled.
Read back every required control, rebuild cached state after failed libaudioroute
writes, and reopen/reprepare a running PCM when changing its DSP backend. A6L
observed both write errors and silent output when it switched an active backend
without reopening. Avoid an unconditional default-speaker route on headphone
unplug: honor AudioService BECOMING_NOISY and recheck active streams first.

ESS Hi-Fi has priority: build and validate its separately gated secondary-MI2S
path first, resolving the measured selector ambiguity and unknown oscillator
frequency before claiming audible Hi-Fi. WCD headphones and bottom mic are the
next endpoint path. Add the AW speaker only after its exact variant/sequence
and shutdown control are understood. Do not carry A6L gains/calibration/profile
names to these different chips. Cellular voice, Bluetooth and acoustic echo/noise
processing remain separate work.
