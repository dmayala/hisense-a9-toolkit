# A9 waveform-bin temperature inputs

**Date:** 2026-10-12. **Status:** docs synthesis of two independently accepted
private revision-4 reports; this source change awaits independent review.

The composer reads two inputs into cached int32 fields before waveform-bin
selection. The accepted acquisition paths establish stored numeric ranges and
local error behavior. They do not establish fresh physical temperatures,
sensor location, calibration or the running kernel/overlay. These are waveform
inputs; no examined path connects them to the user's Night Light color setting,
and these findings provide no adjustment recommendation.

## Composer acquisition and stored domains

| Input | HWCDisplay field / weight | Read source | Identified acquisition store |
| --- | --- | --- | --- |
| t1, logged as `GetEpdBottomTemp` | `this+0x6d8`, weight 2 | `/sys/devices/platform/soc/soc:qcom,dsi-display-primary/epd_thermal` | `atoi` result 0..85 retained; other int32 results become 0 |
| t2, logged as `GetEpdTopTemp` | `this+0x6d4`, weight 1 | IIO path below | Signed division by 1000 toward zero, bounded to 0..50 |

The exact t2 path is
`/sys/devices/platform/soc/1c40000.qcom,spmi/spmi-0/spmi0-02/1c40000.qcom,spmi:qcom,pmi632@2:vadc@3100/iio:device0/in_temp_epd_top_therm_input`.
Both blocks use open flags 0 (`O_RDONLY`), a zeroed 64-byte buffer and
`pread` of at most 63 bytes at offset 0, then `atoi`. For t2, results below
−999 store 0; −999..999 divide to 0; 1000..50999 divide toward zero;
results above 50999 store 50. For t1, 51..85 are stored as supplied and
subsequently capped to 50 by the consumer; 86 and above become 0 at acquisition.

Failed open or nonpositive `pread` retains the affected field's prior value.
A positive read of nonnumeric text parses to 0 and replaces it. A successful
open, including fd 0, closes on the admitted normal path even after nonpositive
`pread`. These are local store/retention facts, not lifecycle or freshness
guarantees. Mocked out-of-int32 text results reflect a chosen `atoi` mock;
they establish neither device-libc overflow behavior nor C semantics.

The constructor directly initializes only `+0x6d8` to 0. A shown guarded
`SetPowerMode` initialization writes 28 to both fields. Preinitialization of
`+0x6d4`, thread-start versus initialization ordering and additional alias
writers remain unresolved. The ranges above apply to identified stores,
not every possible object state. On examined work paths reaching acquisition,
both reads precede request/mode selection and weighting; no universal sampling
rate or exhaustive work-trigger claim follows.

The consumer loads int32 values and tests bit 7 of the low byte after its
signed cap comparison. Bit 7 is clear for values from these identified stores.
The [request model's](a9-composer-requests.md) explicit raw-byte 0..255 and
signed-byte −128..127 domains include values these acquisition stores do not
write. Its results remain valid within those declared synthetic domains;
they are not observations of current input values or complete lifecycle states.

## Kernel source and freshness limits

In the exact saved Image, `epd_manual_temp` starts at −1. `epd_thermal` show
returns the manual integer when nonnegative, otherwise the FP9929A cached
value at `fp9929a_data+0x14`; that show path performs no I2C transfer.
Nonnegative stores update the manual value and emit `EPD_TEMP`; negative
writes are ignored and cannot restore the initial automatic selection.
The composer path proven here opens this attribute read-only. Composer write
handling and the manual-value writer remain unestablished; missing direct path
references or an `EPD_TEMP` string do not prove no writer/parser exists.

The FP9929A read helper copies one unsigned byte only when its register-0
I2C transfer succeeds. Identified probe, active-mode and `show_thermal` paths
update the cache on successful reads. Panel enable calls active mode.
The distinct diagnostic `show_temperature` path can ignore a failed read and
replace the cache with 0; it is not the composer's `epd_thermal` attribute.
These examined refresh paths are not an exhaustive caller inventory.
A successful composer read may therefore return an unchanged manual or cached
value. Local retention on read failure does not prove physical freshness.
The names “bottom” and “top” do not establish physical sensor locations.

## Conditional ADC scale and numeric output

The second accepted report resolves the earlier report's open scale question
for the saved Image, base DTB and candidate channel configuration. Active
kernel, DT and applied overlay fixups remain unproven. Conditional on that
chain, the `pmi632` node's `qcom,spmi-adc5-lite` match selects channel table
`adc_chans_pmic5`; channel `0x54` is named `gpio3_pu2`, an IIO TEMP channel
whose default scale is 6. The inspected base tree and candidate child provide
no scale override. The overlay label is `epd_top_therm`.

Processed reads pass the converted 16-bit code to `qcom_vadc_hw_scale`.
Scale 6 selects the 34-pair `adcmap_100k_104ef_104fb_1875_vref` table branch:
`c = max(0, signed16(code))`, then integer intermediate
`v = c * 1875000000 / (28900 * 1000)`. It clamps table endpoints or interpolates
between descending signed x entries using signed integer division toward zero.
The returned value is the raw interpolated y integer, unchanged, in
−40000..125000; sysfs formats that integer without dividing it. The
microvolts-equivalent intermediate is not the output. One table anchor is
`x=937000, y=25000`; a synthetic call with code 14442 returns 25000.

Prescale and LUT-index arguments are unused in this branch. Calibration and
the separate `0x8000` handling before this call remain unresolved. Conversion
errors return before scaling; this scale-6 branch itself returns success and
clamps silently. Endpoint outputs do not diagnose physical open/short faults.
The table and composer's division by 1000 agree with thousandths of Celsius-like
degrees. This is software consistency evidence, not proof of physical Celsius,
sensor identity, the fitted part or calibration validity. Driver `gpio3_pu2`
versus candidate pinctrl `gpio4` mapping remains unresolved.

## Exact identities and accepted receipts

Composer SHA256:
`8c89557bf47b57b1687b9fbcc3e55bedbee175d20f80d14bc5317fcf893df5ed`.
Saved Image SHA256:
`2f0edd65ddb2bfb743727560e740448ae931e0b31972ac4c4e0c063a88f3ceb1`.
Host base DTB SHA256:
`a00f6103d3c307e5a02da0003aeba4e1a10fd9af1df7c87afb8e466ff2539a22`.
The saved Image is not proven running; the overlay is only a candidate.

Composer anchors are ELF virtual addresses: acquisition
`0x1a632c..0x1a65d4`, constructor store `0x198238`, guarded 28/28 store
`0x19f568`, consumer loads `0x1a7a5c/0x1a7a60` and weighting
`0x1a7a94..0x1a7b0c`. Kernel linked VAs: `adc_read_raw`
`0xffffff8008c7b580`, `qcom_vadc_hw_scale` `0xffffff8008c7c160`, scale-6
branch `0xffffff8008c7c18c`, table `0xffffff8009730cb4` and output store
`0xffffff8008c7c8fc`. Image file offsets equal linked VA minus
`0xffffff8008080000`. These are saved-binary anchors, not live addresses.

Private evidence root: `/home/user/source/a9-display-evidence-20261012`.
Paths below are beneath its `evidence/`; all hashes are SHA256.

| Investigation | Revision-4 report / hash | Manifest hash | Final independent receipt / hash |
| --- | --- | --- | --- |
| Temperature acquisition | `temperature-inputs-20261012/REPORT.txt` — `766745b72d5d0886ad625c9529cda661ef96edbe2d582ecc3d8c802cfe77b8fc` | `temperature-inputs-20261012/SHA256SUMS` — `5140e5a14bf647c92f5e945263e735f20b8eeb9510f971d3c5dd333e38ecdc85` | `derived/temperature-inputs-independent-review/final-review.txt` — `c301282fb4c198d6e2b13cfd2418f8a9da0398577de593c29f2009e1fc2504a5` |
| ADC scale | `adc-scale-20261012/REPORT.txt` — `e82a4f7d840de14b2093b5848b31a1c6efd5f9412ae6f5271461101e67483b3a` | `adc-scale-20261012/SHA256SUMS` — `d85ad56b09b811a5e87756d262f4153fbf9c2c3e482a59e318cc1775f386b3a8` | `derived/adc-scale-independent-review/REPORT.txt` — `0f0ad36ee55e09ef844c3613054fc6249a64bec59ed538f76f0c82b2193cef75` |

The matching `/tmp/` receipts are
`a9-temperature-inputs-independent-review-20261012.txt` and
`a9-adc-scale-independent-review-20261012.txt`. Temperature review covers 28
original-byte synthetic cases plus bounded kernel static/relocation checks.
ADC review covers 39 instruction assertions, four positive RELA records,
candidate DT fixups, 19 selected codes and four supplemental variations.
The author's 65536-code sweep and Beta fit were not independently recertified.
This docs synthesis reruns no emulation, code tests or device work. Raw vendor
bytes, disassembly, complete LUT and logs remain outside Git. See also the
[measurement boundaries](a9-display-measurement-boundaries.md) for the distinct
software and optical milestones.
