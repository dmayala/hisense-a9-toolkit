# Experimental A9 ESS HiFi draft (offline only)

This is a source draft recovered from the shipped A9 module, not a verified working audio path. No device was accessed. Import only the explicit files listed below; validation DTB/binaries/logs are scratch artifacts. No firmware is included.

Files to integrate:

- `es9218-a9.c`, `es9218-a9-seq.h` → `sound/soc/codecs/`.
- `a9-es9218-card.c` → `sound/soc/qcom/`.
- Append `Kconfig.codecs`/`Makefile.codecs`, `Kconfig.qcom`/`Makefile.qcom` to the respective kernel files.
- `hisense,a9-es9218.yaml` → `Documentation/devicetree/bindings/sound/`. Local draft binding lacks upstream maintainer metadata; no full dt-schema validation claimed.
- Apply `a9-es9218-capability.patch` for the explicit S32 backend capability and machine-card binding compatible. Clean `git apply --check` passed against source commit397f74b5a8905ec78c30a61da173c343df4da19e.
- Merge `a9-es9218.config` after the audio fragment only when the audio/ESS build feature is selected; both new modules remain modular.
- Include `a9-es9218.dtsi` after the complete Android board DT only when the audio build flag is selected. Its codec and sound-card nodes remain **disabled** unless a second `A9_AUDIO_ENABLE_ES9218` preprocessing gate is explicitly defined. The integrated builder exposes these as `--es9218` and `--enable-es9218`.

DT facts: stock I2C physical4a84000 is mainline `i2c1`; slave0x48. GPIO65 reset(active-low),69 selector(raw stock0/1),66 oscillator enable,68 headphone output power. Codec driver describes reset as active-low and converts the recovered physical-level sequence at its GPIO callback; others are active-high. Do not substitute TLMM with LPASS LPI pinctrl. Secondary MI2S is LPI GPIO10 clock,11 WS,12 SD0. Unit address`dai@12` matches backendID18 decimal. Existing q6asm frontend nodes0,1,2 remain in inherited order; FE MM1 uses argument0. ESS mode reopens ONLY65,66,68,69 in the Android-specific reservations, preserving frontlight/key pins4,5,83,84,85.

The source paths are separated from WCD/SoundWire: q6asm MM1 S24_LE frontend→APR ADM→Q6AFE SECONDARY_MI2S_RX **S32_LE backend**, 48kHz stereo→ESS input word32. Machine clocks secondary MI2S BCLK3.072MHz. This explicit backend avoids assuming that Q6AFE's bit_width24 implicitly selects32-bit serial slots: mainline passes params_width directly to its I2S DSP packet. A targeted format extension adds S32 only to SECONDARY_MI2S_RX and only while `CONFIG_SND_SOC_A9_ES9218` is enabled. The generic RB2/SM8250 machine's S16 fixup is not used. Frontend S16 input can still be converted by DSP to the fixed32-bit backend; this is not a claim that16-bit source material gains resolution. The intended first HiFi test is24-valid-bit source on the MM1 frontend with explicit32-bit backend transport. DSP firmware behavior remains a phone test.

Codec DAI formats S16_LE/S24_LE/S32_LE are accepted at48kHz stereo only. 16 bits select stock enum bit_16 (`reg1&c0=00`);24-valid-bit S24_LE in a32-bit container and32-bit S32_LE select bit_32 (`80`) per stock QRD24-bit routes. The standalone driver can be reused by another test machine, but only the dedicated S32 backend is currently staged. Other rates, DSD, master mode and oscillator frequency are not inferred from the unused legacy PLL functions. No fixed MCLK node or guessed frequency exists.

Stock sequence equivalence and explicit new policies:

- 47 register/value pairs are copied exactly from ELF `es9218_init_register`; source hash/symbol/bytes retained in `stock-init-table.json`. Power-up GPIO levels, ramp writes and delay ranges match complete stock HiFi mode2. Each table write is read back with one mismatch retry, matching stock `do_reg_write_check` behavior. The new I2C layer returns errors immediately rather than stock low-level repeated I/O attempts and ignored failures. Independent PCM/DAPM ownership outlives a physical fault, so a failed live volume/mute operation cannot make the still-owned stream eligible for manual Bypass power.
- Stock ASoC startup100–110ms and shutdown register sequence are retained. New DAPM event handling follows shutdown with proven stock GPIO power-off containment, so reset/oscillator/PA are not left powered after a closed stream or I2C failure.
- The actual stock ASoC mute callback is a NOOP. The legacy helper changing reg0e02/8a is unreferenced by active callbacks; it is **not** transplanted. New mute design uses only the proven reg7 bit0, explicitly labeled as a new policy. Reg0e remains stock active0x25 during mute/unmute. Startup remains muted until the ASoC prepare sequence unmutes it.
- I2C/ID/readback errors abort HiFi, assert reset and disable oscillator/PA. Wrong ID gets six stock-pattern reads and no speculative register writes. DAPM/mute failures latch a PCM START error because ASoC ignores those callback returns in parts of prepare. The next fresh successful power-up clears the latch. This is software containment evidence, not rail/electrical proof.
- No arbitrary regmap cache serves readbacks: `REGCACHE_NONE` keeps init verification real. Remove/shutdown cleanup is idempotent. Normal DAPM endpoint power-down retains negotiated sample width while a PCM remains open, so a legal pin/sync cycle can reapply it. PCM close and system suspend clear that configuration. System suspend explicitly runs shutdown and GPIO Standby, clears cached bypass and input-width state, and leaves a START fault for formerly active paths. Resume never reapplies power automatically; userspace must close/reopen/prepare a fresh PCM stream. This fail-closed suspend policy is a new design, not stock behavior; transparent pause/resume is not implemented. Probe disables acquired PA/oscillator outputs before reset/selector acquisition and validates GPIO polarity before driving outputs. No runtime firmware blob or proprietary stock module is installed.

Controls: `ES9218 Digital Volume` is a two-value raw0..255 register control for registers0f/10. Default255 matches the raw bytes written by stock shutdown; acoustic attenuation, direction and dB/TLV scale remain unverified. Setting it while off caches both values; HiFi power-up reapplies them after the stock table. `ES9218 Bypass Switch` invokes the measured bypass GPIO sequence only with no HiFi stream; mode conflicts returnEBUSY. Stream hardware parameters drive input word length automatically; master/divider remain stock initialized slave/div4 settings. There are **no** proved filter/oversampling controls in the stock five ALSA controls. Register7 default40 is known; the filter/OS meaning is not. No filter names, oversampling choices or recommended register values are invented.

Critical selector uncertainty: stock probe requests selector GPIO69LOW; stock standby/bypass/power-off writes itHIGH; **complete stock HiFi setter never changes it**. Thus HiFi retains whichever selector state preceded it. Our safe standby usesHIGH and HiFi preserves it exactly; no invented LOW transition is inserted. Whether this signal physically selects analog bypass, another path, or participates differently is unresolved. Driver/binding text therefore does not claim thatHIGH electrically meansbypass, and this ambiguity must be resolved before claiming audible HiFi. The disabled-default DT prevents accidental probe. Firmware/DSP/clock/enumeration and physical listening tests are not replaced by compile results.

Tests:

- `test-es9218-seq.c` compiles the actual production shared sequence header. GCC14 -Wall/-Wextra/-Werror passed388,153 assertions: nominal16/24/32; every63write/50read failure point; transient and persistent readback mismatch for all47 registers; wrong ID with no writes; permanent I2C failure; all shutdown writes; mute failure; duplicate/conflicting mode rejection; complete GPIO containment;24-valid-bit word32 policy; exact ramp/shutdown write order and preserved selector behavior.
- `test-es9218-controls.py --driver PATH --cc CC` extracts the actual production volume/bypass/mute/trigger/startup/shutdown/suspend and DAPM callback bodies, supplies kernel/I2C stubs and compiles them. PASS for left/right volume failures, PCM ownership, DAPM-only ownership, idle bypass, mute START fault, open/close exclusion, suspend containment negative/64-bit-overflow volume values, three DAPM off/on cycles at16/24/32bits, volume restoration, close/suspend invalidation and rejection without parameters. The DAPM regression fails against the old driver and passes with retained width. No duplicated control implementation is substituted.
- Same sequence tests passed ASan+UBSan. Leak discovery disabled because only a stack/static mock is used and prior sandbox introspection limitations are irrelevant to sequence memory checking. Logs `test-es9218-seq.log` and `test-es9218-seq-asan.log`.
- `test-dt.py` freshly preprocesses and compiles Android baseline, ESS-disabled and ESS-enabled variants, checks exact reservation difference{65,66,68,69}, no added reservations, all frontlight/key pins open, physicalI2C1@4a84000/slave48, FE child order0/1/2, FEargument0 and backend18decimal. Result`test-dt-result.json`; only existing DT warnings retained in variantlogs.
- Full kernel/module compilation is recorded separately in private build receipts; host and DT tests do not establish real kernel linking or physical audio behavior.

## Powered bring-up self test (stages P1/P2; NOT stage A)

`insmod snd-soc-es9218-a9.ko bringup=1` exposes a root-only
`/sys/bus/i2c/devices/<bus>-0048/selftest` (write `id` or `init`, read the
result). Without `bringup=1` a write returns `EPERM`; stage A loads the codec
with `bringup=0` and never uses it. It is refused while a PCM or DAPM path owns
the codec and always ends in Standby. Both words **raise PA68 and OSC66** (the
stock probe/HiFi power state for an ID read; stock never reads the ID with
PA68 low, review 51 R51-1), so they are powered stages that need their own
review. `id`: reads of `0x40` only, zero register writes. `init`: right after
the ID, `07=41` (mute) and `0f/10=ff` are written and read back, then the stock
table is replayed with those three entries kept muted, so no write clears mute
or lowers attenuation (R51-2, invariant tested on every failure path); dump
`0x00..0x2e`; stock DAI shutdown ramp; stock power-off. The mute bit, the
attenuation direction and the role of `0x20` are inferred: this lowers, but does
not prove away, a power-up click. Tests: `tests/test-es9218-seq.c`,
`tests/test-es9218-selftest-attr.py`. Stage scripts and fake-phone tests built
from the stage DTB: `stage/` (`stage/test-stage.py`).


Review 57 approves the kernel sequences with an empty jack. The stage harness
reloads the codec with `bringup=1`, verifies Standby before each operation,
runs P1 as three `id` stores (all raw IDs retained; each `id & 0xfc == 0xc0`),
and runs P2 as one `init` store after P1 PASS for the same tuple and boot.
P2 requires an exact match of all 47 muted dump values. Each stage checks the
frontlight before and after, kernel errors, and verified module removal even
on PASS. START records prevent retries in the same boot; any failure stops
further powered stages until reboot. The 90 s watchdog, bounded kernel calls
and host 60 s emergency cleanup remain active; no kio child is signalled.

Before a device run, independently review this harness delta and its offline
rehearsals. P1 is unattended-OK under review 57. P2 is attended by default;
unattended eligibility also needs stock S0 evidence that chip_init completed
on this unit. `stage/test-stage.py` covers the fake phone and fake adb;
`stage/test-android-sh.py --android-root <v7-extraction> --qemu <qemu-aarch64>`
runs the same suite under the actual Android sh and Toybox, including
session-leader watchdog cancellation. Neither test accesses a device.
