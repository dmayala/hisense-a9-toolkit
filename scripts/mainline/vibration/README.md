# A9 vibrator POC

Latest user result (2026-10-21): `integrated-20261021n` is **stronger than stock**.
The [stock steady default](stock-equivalent.md) uses peak78, RAM_LOOP bypass
and stock software battery compensation; the earlier boosted peak126 output
remains available via `stock_profile=0`. F0 measured236.5 Hz on k4. Earlier
bring-up findings are in [stock-strength.md](stock-strength.md) and
[amplitude.md](amplitude.md). The stock DT puts `awinic,haptic_hv` at I²C0
`0x5a`, reset TLMM67 active-low, IRQ TLMM28 falling. Bootloader logs say
`aw8624_device_init`, but the stock kernel has AW869x driver symbols and the DT
has AW869x settings. Those names do not identify the fitted chip.

`a9-aw86xx` reads register 0 before any register-layout-specific write: ID `0x24`
selects AW8624, `0x95` selects AW8695, `0x97` selects AW8697. Any other ID is logged and probe fails.
The driver initially generates a 102-sample signed sine in SRAM (24 kHz / 102 =
235.29 Hz, from the stock nominal F0 of 235 Hz). AW8695 then measures resonance
once before input registration and replaces it with an eight-cycle loop at the
measured frequency. This causes a brief calibration buzz at driver load;
`auto_f0=0` retains nominal playback until an explicit idle measurement.
See [resonance.md](resonance.md) for stock REA evidence, failure behavior and a
two-minute tactile test. It exposes `FF_RUMBLE`; the input memless
core handles duration/off. AW8695 defaults to a generated ±78 sine and battery
bypass. It reads the stock reset gain and applies stock RAM_LOOP compensation
`min(base_gain*4200/VBAT_mV,179)`. Its live0..255 `gain` knob adjusts that
baseline; `stock_profile` selects the stock steady profile or the earlier
boosted ±126 profile. The8500 mV boost setpoint is retained but inactive in
stock mode. RAM data mode remains1x. AW8624/AW8697 retain the original ±64 sine
and battery bypass. Physical strength needs the one-minute user comparison in
the stock-default document. No saved calibration, efuse, persistent data or
vendor waveform is used.

The Android V3 AIDL HAL supports on/off, with the kernel's 1..65535 ms duration
range. Capabilities are zero: no callback, amplitude, canned effects, composition
or frequency-control promises. Android can fall back to on/off for effects.
The example vibrator APEX (including its example manager) is removed and one
physical `IVibrator/default` is declared. This targets the existing permissive
integrated image; SELinux enforcing parity is a separate task.

Build from a worktree containing the integrated refresh recipe (a6ca611 or later):

```sh
python3 -I scripts/mainline/vibration/build-images.py
```

`A9_VIBRATION_OUT` can override the default
`~/A9-build/mainline/vibration-20261020`. NDK and binder dependencies reuse the
integrated thermal HAL's cache under `~/A9-build/mainline/sensors`. The first HAL
build downloads the frozen Android V3 AIDL API and one platform import; subsequent
builds are offline. `a9-vibration.config` records existing R4 dependencies.

The recipe builds only the external module using a private copy of R4's object
tree, preserves the R4 kernel image and module symbol versions, changes
the haptics node in R4's DTB and releases only TLMM67 from reserved GPIOs, runs `build-vendor.sh --refresh-integrated`, then
adds the B1 module/service/manifest. Existing integrated modules remain intact.
Outputs: `out/{boot.img,vendor.img,a9-aw86xx.ko,android.hardware.vibrator-service.a9}`,
`out/SHA256SUMS`, and `artifacts.json`. Vendor images retain private modem seeds;
keep them local. Never use the private `images-mac` boot as an input.

Coordinator phone test, in the coordinator's next owned integrated slot-B boot:

1. Use the generated boot and vendor pair with the existing POC runner. To retain
   Bluetooth's private DT address, overlay **only the haptics node's compatible
   and status**, plus release reset pin 67 from the TLMM reserved ranges, into the current integrated DT; the supplied boot is derived
   from the public R4 boot and does not carry a private Wi-Fi MAC.
2. Check `dmesg | grep -E 'a9-aw86xx|haptics'`; expected A9 chip ID `0x95` (AW8695); `0x24` and `0x97` remain supported.
   Unsupported ID is a useful result: report that exact non-private byte rather
   than enabling a guessed register layout.
3. Check `getprop init.svc.vendor.vibrator-a9` and `dumpsys input` for
   `a9-aw86xx-haptics`. Check `service list | grep vibrator` for the physical
   `android.hardware.vibrator.IVibrator/default` instance.
4. `cmd vibrator_manager synced oneshot 200`; confirm the actual buzz stops after
   about 200 ms. Repeat 50 ms and 500 ms; check `cmd vibrator_manager cancel`
   during a longer oneshot. Check touch/keyboard haptics with Android settings
   enabled. Do not infer physical vibration from a successful command alone.

Reference register sources: [Awinic AW8624](https://github.com/awinic-driver/aw8624)
and the [AW8697 input driver posting](https://lists.openwall.net/linux-kernel/2026/09/30/2189).
The REA stock-ELF function query timed out; no decompiled result is claimed.

The coordinator observed `reset GPIO: -EINVAL` with the first candidate. R4's
`gpio-reserved-ranges` contains `<37 34>` (37..70), including reset pin 67.
The builder now splits that range around 67; every other reserved pin stays
reserved. No reset timing or register sequence changes. GNSS is now opt-in
(`--gnss`) because its framework crash is unresolved.

For full composition use `scripts/mainline/build-integrated.sh`. This builder
also accepts `--boot PATH`, `--vendor PATH`, and `--kernel-build DIR` to preserve
earlier feature steps and reuse the same private kernel build.

AW8695 uses AW869x gain/DATDBG `0x39` and global-state `0x46`, verified
against the [AW8695 driver posting](https://lists.openwall.net/linux-kernel/2026/10/01/1353)
and the cached stock symbolized kernel: `aw869x_set_gain`
(`0xffffff80087be8d8`) passes `0x39` to `haptic_hv_i2c_writes`;
`aw869x_get_glb_state` (`0xffffff80087c0208`) passes `0x46` to
`haptic_hv_i2c_reads`. RAM/SYSCTRL/GO/wave-sequence/PWM offsets match the
existing AW8697 path. The original ID-support change shared that path. The strength candidate now adds the AW8695-only stock drive setup described above; reset timing and calibration remain unchanged. The current public
`awinic-driver` repository index exposes no AW8695/AW869x repository; the
AW8695 posting retains its Awinic vendor-driver attribution.

Incremental rebuild preserves the tested pair and all unrelated payloads:

```sh
scripts/mainline/build-integrated.sh \
  --update-vibration ~/A9-build/mainline/integration-build-20261020 \
  --out ~/A9-build/mainline/integration-aw8695-20261020
```

This compiles only the external vibration module, reuses the existing HAL and
updates its snapshot entry. Outputs go to a separate directory.
