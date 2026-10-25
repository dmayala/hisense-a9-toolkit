# AW8695 strength candidate — 2026-10-20

This file describes the historical 43eb28f candidate (peak78). See [amplitude.md](amplitude.md) for the newer peak126/gain/data-rate changes.

The coordinator reports working but faint vibration with the original ±64
235 Hz RAM sine in battery bypass. This candidate selects **boosted RAM
playback for AW8695 only**, using the stock A9 boost setup and a generated
±78 sine. It needs a physical comparison; host analysis cannot establish stock
strength. In particular, this is **not an exact reconstruction of stock long
effects**: stock distinguishes boosted RAM from its bypass RAM_LOOP mode.

## Stock evidence and offsets

All offsets below are hexadecimal **file offsets**, not register addresses or
virtual addresses. Local evidence stays under
`~/A9-build/mainline/vibration-strength-20261020/`; no firmware is committed.

- **DT**: `~/A9-build/mainline/sol-audio/stock/merged.dtb`, SHA256
  `92cfd52ec25a36dd92eb47b3bf99278bb67b7f05298398ad2b635afad2fd8862`.
  These offsets address property data in the stock `haptic_hv@5A` node.
- **ELF**: `~/A9-build/mainline/sol-pmic/evidence/vmlinux.elf`, SHA256
  `258f157ce827c8997be7e358c06b91fdcb0032d423e978c248d7f5fc858291b5`.
  The `.kernel` mapping is VA `0xffffff8008080000` at file `0x1c0`;
  `file_offset = VA - 0xffffff8008080000 + 0x1c0`. Saved symbol names and
  `aarch64-unknown-linux-gnu-objdump` disassembly identify the functions below.
- **RAM**: stock vendor image
  `~/A9-build/mainline/android/ref/vendor_a.img:/firmware/haptic_ram.bin`,
  extracted into the evidence directory. SHA256
  `dfbb039adedfc85d797645fc3652ee1631899d2aeb15db51b1f8c4d7c332929f`.
  The relevant cache has `haptic_ram.bin`, rather than the brief's suggested
  `aw8695_haptic.bin`. Its checksum is `0xdacb` at `0x0`, base address
  `0x1000` at `0x2`, and table marker `0x55` at `0x4`.

### Frequency, waveform and stock modes

| Value | Evidence |
| --- | --- |
| Nominal F0 235.0 Hz, not 170 Hz | DT `f0_pre=2350` at `0x30408`, coefficient `260` at `0x30388`; ELF `aw869x_misc_para_init`, `0x73fb18..0x73fb5c`, computes the F0 register from the product. |
| 24 kHz RAM sample rate | ELF probe passes PWM enum 1 at `0x74e2e0..0x74e2f0`; `aw869x_set_pwm`, `0x73ea70..0x73ea80`, maps it to register `0x2e`, bits 6:5=`2` (`0x40`). `aw869x_get_theory_time`, `0x740564..0x740568`, recognizes that field as 24 kHz. Stock `/firmware/haptic_rtp_osc_24K_5s.bin` is 120000 bytes. |
| Steady wave 4: 102 samples, peak ±78 | RAM table at `0x11`: SRAM `0x1606..0x166b`; sample bytes `0x60a..0x66f`. This implies `24000/102 = 235.294 Hz`. Generated samples use this count/peak; they do not copy the firmware. Its selection by the shipped Android HAL has not been established. |
| Other stock waves | RAM table `0x5`: wave 1, 663 samples at `0x19..0x2af`, peak ±126. Table `0x9`: wave 2, 510 samples at `0x2b0..0x4ad`, peak ±126. These transient envelopes are not used by this candidate. |
| Default mode is RAM_LOOP 5 | DT `mode=5` at `0x303e8`; ELF `vibrator_work_routine`, `0x74ea24..0x74ea54`, dispatches mode 4 to CONT, mode 5 to RAM VBAT compensation and RAM_LOOP. |
| RAM_LOOP uses bypass | ELF `aw869x_play_mode`, `0x73ee78..0x73ee90`, writes mode 5, clears SYSCTRL `0x04` bits 3:1 (value zero), then activates. It does **not** take the boosted RAM branch. |
| RAM mode 1 uses boost | ELF switch table `0x164eed0..0x164eed5` selects VA `0xffffff80087be9e0` for case 1; `0x73eba0..0x73ebc4` branches into shared RAM setup. `0x73ed68..0x73ed74` sets SYSCTRL bit 1, then `0x73ed78..0x73ed80` waits 2000..2500 microseconds. This candidate follows that branch while retaining the existing repeating sequence. |

The correct frequency is already present. Missing boost alone cannot explain
stock's long-effect behavior, since that path also bypasses it. The candidate
uses stock's **other**, boosted RAM path as a direct strength experiment.
The larger sine and boosted supply should increase electrical drive; actual
acceleration and perceived strength remain unmeasured.

### Every new drive setting

| Driver setting | Stock value and register mapping |
| --- | --- |
| BSTDBG registers `0x31..0x33 = 30 eb d4` | DT `0x30324..0x30326`; ELF `aw869x_misc_para_init`, `0x73faa0..0x73fab0`, writes the first three bytes. The remaining three DT bytes are not written. |
| TSET `0x4d = 0x12` | DT `0x30348`; ELF `0x73fab4..0x73fac4`. |
| R_SPARE `0x5d = 0x68` | DT `0x30338`; ELF `0x73fac8..0x73fad8`. |
| BEMF `0x74..0x77 = 10 08 03 f8` | DT `0x30358..0x3035b`; ELF `0x73fb04..0x73fb14`. |
| ANADBG `0x35`, mask/value `0x0c` | ELF `0x73fadc..0x73faec`: clear bits 3:2 and write `0x0c`. |
| ANACTRL `0x36`, bit 5 set | ELF `0x73faf0..0x73fb00`: mask `0x20`, value `0x20`. |
| BSTCFG `0x34`, bits 2:0 = 1 | AW8695 probe sets enum zero: ELF `0x74ddc4..0x74ddd4`; `aw869x_set_bst_peak_cur`, `0x73e88c..0x73e8fc`, maps zero to code 1. AW8697 uses another enum/code. The AW8695 vendor input-driver posting names code 1 as 2 A: cached `integration-aw8695-20261020/references/aw8695-v2.txt`, `0x1dbe`, `AW8695_BSTCFG_PEAKCUR_2A`. |
| BSTDBG4 `0x3a`, bits 5:1 = `0x22` | DT requests 8500 mV at `0x302f8`. ELF `aw869x_set_bst_vol`, `0x73efe8..0x73f028`, clamps 6000..10500 mV and computes `floor((mV-6000)/142)<<1`. For 8500 this is `0x22`, a nominal quantized 8414 mV; no voltage measurement is claimed. |
| ADCTEST `0x66`, bit 6 clear | ELF RAM common tail `0x73ef14..0x73ef24`. CONT sets this bit instead. The candidate keeps its fixed software gain; it does not apply battery-based gain compensation to boosted playback. |
| SYSCTRL `0x04`, bit 1 set after activation; 2000..2500 µs settle | Boosted RAM branch above. Off clears GO and explicitly clears this boost bit while forcing standby. Other chip IDs keep their original bypass behavior. |

The cached primary posting is
[AW8695 input driver](https://lists.openwall.net/linux-kernel/2026/10/01/1353),
SHA256 `f8c66ee3ed689aa86ed63b07e4f53047c8f45ed5b1dde1baf897f73f472e2323`.
The current web fetch failed; the cached posting and stock ELF were inspected
locally. Ordinary binutils output is not a REA decompilation. The attempted REA
Ghidra `analyze_function(aw869x_cont_config)` timed out after 300 seconds and
returned no decompilation/Evidence records; the owned target was closed.

### Values investigated but deliberately not enabled

CONT settings are DT `cont_drv_lev=0x7d` at `0x303d8`, overdrive `0x9b` at
`0x303c8`, time `0x006c` at `0x303b8`, zero-cross threshold `0x0ff1` at
`0x303a8`, brake count 3 at `0x30398`. ELF `aw869x_cont_config` maps these to
registers `0x7b/0x7c` (`0x73e608..0x73e624`), `0x4b/0x4c`
(`0x73e584..0x73e5a0`), `0x72/0x73` (`0x73e5bc..0x73e5d8`), and
`0x78` low nibble (`0x73e5e4..0x73e5ec`). CONT additionally writes register
`0x48=0x89` at `0x73e56c..0x73e57c`. These do not affect the chosen RAM mode.
No claim of F0 tracking or measured calibration is made.

Stock RAM-loop software compensation multiplies its saved gain by `4200/VBAT`
(ELF `ram_vbat_comp`, `0x751760..0x751770`) and caps it at 179 (`0x751788`).
The initial gain is read from register `0x39` (`aw869x_misc_para_init`,
`0x73fa58..0x73fa80`), so the ELF does not establish that the stock runtime gain
is always 128. Our existing FF gain cap 128 is retained. The A9 AIDL HAL sends
full FF magnitude for on/off and does not advertise amplitude control; a
successful `-a 255` request is not a measurement of stock drive strength.

## Build and coordinator test

Cherry-pick the strength commit onto the integrating branch and use the normal
`scripts/mainline/build-integrated.sh` vibration step. Its existing recipe
copies `a9-aw86xx.c` and `sine.h`, so no extra firmware, DT overlay or builder
change is needed. A vendor-only `--without vibration` refresh retains the old
module and will **not** include this correction. No full image was built for
this task.

The external module was compiled with `W=1` against the existing
`~/A9-build/mainline/video-20261020/power-kernel/{kernel,obj}`. Output:
`~/A9-build/mainline/vibration-strength-20261020/a9-aw86xx.ko`.
`module-command.json`, `module-build.log` and `host-verification.json` record the
command, module hash, import CRCs, vermagic and unchanged borrowed kernel files.

In the coordinator's next owned test boot, run through its existing root shell:

```sh
dmesg | grep -E 'a9-aw86xx|haptics'
getprop init.svc.vendor.vibrator-a9
cmd vibrator_manager synced oneshot -a 100 255
cmd vibrator_manager synced oneshot -a 500 255
cmd vibrator_manager synced oneshot -a 3000 255
# During a long effect, from the coordinator's existing second shell:
cmd vibrator_manager cancel
dmesg | grep -E 'a9-aw86xx|standby wait|FF playback'
```

Expected initialization line:

```text
chip ID 0x95; RAM sine 235 Hz, peak 78, gain max 128; stock boost 8500 mV (reg 0x22), peak-current code 1
```

That line reports successfully programmed settings, not measured voltage or
movement. Confirm a distinctly stronger buzz, duration/off/cancel and keyboard
haptics by touch; record whether long playback remains steady. There should be
no `AW8695 stock drive init`, `standby wait failed`, or `FF playback failed`
errors. A faint result despite the new initialization line leaves boost/output
behavior or waveform/closed-loop tuning unresolved; capture the exact result
rather than declaring strength parity. Thermal/overcurrent shutdown is not
disabled by this change. No calibration, trim/efuse or persistent storage writes.
