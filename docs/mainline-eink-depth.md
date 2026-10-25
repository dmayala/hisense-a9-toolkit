# Mainline E Ink black depth: run 10 and patch 0037

2026-10-16 UTC. Offline only. **MEASURED** below means inspected file bytes,
stock instructions run under Unicorn, compiler output or host-test results.
**INFERRED** means an optical/electrical explanation not measured on the phone.
No device access or phone/sequencer script was performed.

## Result and evidence limits

**MEASURED:** the two captured inputs request **level 1**, not endpoint black
**level 0**, across the setup background. Mainline and actual stock composer
code agree for the same controls. There is no demonstrated digital inversion,
truncated toward-black pass, wrong bin or different configured VCOM. Stock
level 1 is a waveform endpoint, not a calibrated 1/15 optical reflectance.
The photograph cannot establish what optical density that endpoint should
have. Bezel and E Ink have different materials and illumination.

**INFERRED:** the lighter rectangle can persist as physical history because
steady Clear does not drive an unchanged 1→1 pixel. Full Clear actively
conditions it. Whether level 0 is sufficiently black, and whether level 1 is
abnormally light, needs the single pattern experiment below. Changing default
LUTs, tables, temperature bins or VCOM without that result is unsupported.

Sources: run-10 Android directory
`/home/user/A9-wt/run10/runs/20261016T005652Z-mainline-android/android/`,
`glass-photo-wifi-page.jpg`, `eink-status-photo.txt`, both `compare-*` directories;
run-9 `glass-photo-after-clear.jpg`. Photo status has 151 updates, 0 aborts,
late_drive=0, bin 10 with t1=29/t2=35, Clear 0x203/type3. The saved latest kmsg
continues to 226 completed records, after that status. Only two grey inputs
were saved, at status updates 6 and 50. **The exact 151-image live sequence
cannot be reconstructed** from these snapshots. The replay explicitly assumes
white history for the first captured full image and the preceding captured
image for the second; it does not invent missing intervening inputs or claim
that their full-frame hashes are the live partial-update hashes.

## Grey, controls and expected target per pixel

Stock composer SHA256:
`8c89557bf47b57b1687b9fbcc3e55bedbee175d20f80d14bc5317fcf893df5ed`.
Primary code: `calcG16Table` at 0x1a48c8 → object+0x19e7314;
`calcG2Table` at 0x1a49a4 → object+0x19e7214. These addresses were independently
verified by executing each original builder. The older `a9tcon.c` address
comment reverses their names; its arithmetic is correct and remains unchanged.
`a9tcon.c:a9t_set_controls/a9t_condition`, the existing stock oracle and the
new `scripts/mainline/eink/test/depth-replay.py` reproduce those instructions.

Mainline initializes controls to 30/30/0. Compat translates stb/stw/sco directly;
it adds no tone processing or automatic high contrast. These are mainline
and replay defaults, **not a measured live stock preference**. Clear 0x203
uses G16 without optional 16-level diffusion. At contrast0 G16 is identity:

`target(x,y) = driver_grey(x,y) >> 4` (0=black, 15=white).

Black/white thresholds affect G2 modes, not Clear. Therefore increasing black
threshold cannot fix this Clear background. At contrast C, for grey≤200:
`target = floor(grey*(200-C)/200) >> 4`.

| Input | Contrast0 target | Contrast100 target |
|---|---:|---:|
| True black 0 | 0 | 0 |
| Saved background 27 | 1 | 0 |
| Literal #1b1c20, stock luma28 | 1 | 0 |
| White255 | 15 | 15 |

The captured dominant RGB is actually **#1a1b20**, stock luma 27, in both images.
Literal #1b1c20 gives `(76*27+150*28+30*32)>>8 = 28`; this one-step distinction
does not change its contrast0 target. Grey27 first becomes target 0 at C82;
grey28 at C86. C100 is an unambiguous opt-in endpoint comparison, not a new
default. Raw driver-grey equals its PNG byte-for-byte. The independently
captured screencap differs at 2 pixels in the first comparison and 2160 in the
second (99.9998527%/99.8409369% exact); the background agrees exactly. This is
consistent with non-atomic captures of moving UI, not evidence of a systemic
background luma error. Replay uses the raw driver input.

| Captured full image | Grey27 pixels | Expected level 1 pixels | Level0 pixels | Expected target-array SHA256 |
|---|---:|---:|---:|---|
| compare-010047 | 1338796 | 1338912 | 0 | `5dacd82419e1b0196fba57dc60c7418e69defb6869098067d3605fcecf1fc37a` |
| compare-010110 | 1329336 | 1329606 | 0 | `9dc43735768e4e5faa7a9c26b08b77406efac9b6688fd40ea86a38435ea714ec` |

Every expected pixel is saved as an unsigned byte in the replay output
`compare-*-clear{0,1}-engine.lv` (1648×824 landscape). They are target arrays
for the captured full image at the stated controls. Actual historical partial
updates preserve previous targets outside damage; missing history cannot be
replaced with a claim of measured glass levels.

## Clear, init, phase counts and ghosting

The engine `a9e_prepare` preserves previous targets outside partial damage;
`a9e_end` copies new targets into software history. A steady Clear uses type3;
a forced Clear uses a stock transition pass plus type2 and the same target
levels. Full Clear changes conditioning, not the quantisation formula.

**MEASURED** from stock table offsets Clear3=0x2b793/type2=0x8895f,
row stride0x6a, bin 10, including the final neutral frame:

| Transition | Type3 total / code1 / code2 / neutral | Type2 total / code1 / code2 / neutral |
|---|---|---|
| 15→0 | 30 / 12 / 0 / 18 | 30 / 12 / 0 / 18 |
| 15→1 | 30 / 15 / 6 / 9 | 30 / 15 / 6 / 9 |
| 1→1 | 30 / 0 / 0 / 30 | 30 / 12 / 12 / 6 |

Code1 is the toward-black interpretation supported by endpoint transitions;
code2 is toward-white. Counts alone are not an optical model. Bin 10 init is
63 frames (24/24/15); initial init+Clear is 63+30+30=123, matching run10.
Steady updates send all 30, not a truncated toward-black subset. Both captured
snapshots replay byte-for-byte through actual stock dispatcher, CLI and engine,
including the real control template. Steady30-frame and forced-Clear60-frame
hashes match; complete hashes and per-level histograms are in the replay JSON.

An unchanged level 1 pixel has **zero drive for all 30 type3 frames**. Repeated
partial UI updates therefore cannot correct an existing physical level 1 error
or ghost. This supports a conditioning experiment; it does not prove the
source of the observed rectangular band.

## Temperature and VCOM

Stock average after its clamps is `floor((2*t1+t2)/3)`.
The actual bin map selects bin9 at average27..29, bin 10 at30..32.
28/28→bin9; 28/34→bin 10; photo29/35→31→bin 10. A single “about28 C” reading
cannot determine the stock bin without the second sensor. Bin9 and 10 both
have30-frame Clear tables. Mainline's documented thermistor interpolation
rounding is not evidence for this contrast failure.

Requested VCOM 2730 mV gives stock code `0x74+trunc((2730-2500)/22)=0x7e`.
Stock kernel `fp9929a_set_register` at 0xffffff8008f1eb78, golden diag+0x12b0,
UEFI `vcom_reg_value=126`, run10 cmdline and PMIC log all agree. See
[mainline-plan §2.2](mainline-plan.md). This verifies configuration, not the
physical analog voltage. No VCOM or other electrical setting is changed.

## Smallest supported fix and diagnostic hook

A concrete software defect exists when controls change: old `a9_ctl_store`
only marked controls dirty; it did not request or wake an update. A static UI
could remain unchanged indefinitely. At the next partial update, new controls
applied inside its rectangle while old target levels remained outside, mixing
tone mappings across the screen. Patch 0037 queues a full Clear job on an
**actual value change**, with the existing coherent control snapshot and
coalescing. Same-value writes do nothing. It changes neither defaults nor
stock tone arithmetic. This fixes control application; it is not a claim that
the photographed run had a control change or that a physical contrast defect
has been cured offline.

New connector sysfs `eink/test_pattern` is root-only 0600. The Android setup
companion excludes it from the blanket system-ownership assignment; ship that
companion change with 0037. Existing ROM attributes retain their ownership.
Behavior:

- Write `contrast`: hold a generated grey input and request a full Clear.
  Repeating it reconditions the same references. Every pattern job uses
  normal worker/producer/transport and normal temperature/bin/fault admission.
- Write `off`: request a full Clear of the newest normal UI shadow.
- Read: `contrast` or `off`. Invalid strings refuse; no worker gives ENODEV.
  Drive disabled remains dry. No drive gate, fault latch, caps, current or
  voltage is changed.

Android KMS commits keep updating the real UI shadow while the pattern is
held, but cannot kick normal UI over the diagnostic. The hook adds no app,
DRM master takeover or SurfaceFlinger restart. The older raw-DRM
`a9eink-pattern` tool already exists, but needs the virtual card's master and
cannot reliably coexist with Android's active compositor, hence this hook.

Portrait layout, half-open coordinates, at contrast0:

| Rows | Columns | Input / expected target |
|---|---|---|
| 0..824 | 0..412 / 412..824 | 0→0 / 255→15, large endpoint references |
| 824..1236 | 16 equal-width bands | grey17*n→level n, n=0..15 |
| 1236..1648 | 0..275 / 275..549 / 549..824 | 0→0 / 27→1 / 255→15 |

## One deciding app-free experiment — coordinator only, not executed

Run on the working 0037 build with the existing normal drive admission.
One experiment has two phases under the same lighting/frontlight and camera
exposure: C0 references, then C100 opt-in mapping. Do not clear any fault or
open any extra gate for the hook. A fault/abort invalidates the comparison.

In the coordinator's **on-phone root shell**, discover exactly one group:

```sh
set -- /sys/devices/platform/eink-display/drm/card*/card*-*/eink
[ "$#" -eq 1 ] && [ -w "$1/test_pattern" ] || exit 1
E=$1
old_contrast=$(cat "$E/contrast")
printf '0\n' > "$E/contrast"
printf 'contrast\n' > "$E/test_pattern"
sleep 5
cat "$E/contrast" "$E/test_pattern" "$E/status"
cat /sys/kernel/debug/a9_eink/last
cat /sys/kernel/debug/a9_eink/grey > /data/local/tmp/a9-depth-C0-grey.raw
cat /sys/kernel/debug/a9_eink/levels > /data/local/tmp/a9-depth-C0-levels.raw
```

Confirm a new completed full-screen Clear (`clear1`, type2, dry0, aborted0,
no added abort/fault/late_drive; 60 frames normally, 123 if init was pending).
At C0 the big left region and bottom-left patch must target 0, bottom-middle1,
right references15. Take the first photo. Then, as the second phase of the
**same experiment**, change only contrast:

```sh
printf '100\n' > "$E/contrast"
sleep 5
cat "$E/contrast" "$E/status"
cat /sys/kernel/debug/a9_eink/last
cat /sys/kernel/debug/a9_eink/levels > /data/local/tmp/a9-depth-C100-levels.raw
```

Take a second photo at the same exposure. The bottom-middle patch now targets 0
and should join the black references if quantisation explains the desired
appearance. This also directly exercises the static-input control-redraw fix.
Restore afterwards in the same shell (variables retained):

```sh
printf '%s\n' "$old_contrast" > "$E/contrast"
printf 'off\n' > "$E/test_pattern"
```

Decision: if endpoint0 itself remains mid-grey after clean full Clear, changing
quantisation/thresholds cannot supply deeper black; investigate physical drive
or analog response next. If 0 is dark and 1 markedly lighter, use an explicit
contrast preference with full redraw; C100 maps this background to 0. If the
original band disappears on the C0 full Clear while the background still
matches the level 1 patch, that supports physical partial-history ghosting.
Offline code cannot select between those outcomes. Preserve both model-level
dumps and photos rather than infer glass state from grey input alone.

## Reproduction and validation

New offline helper (never contacts a device):

```sh
nix-shell -p gcc 'python3.withPackages(p:[p.unicorn p.numpy p.pillow])' --run \
  'python3 -I scripts/mainline/eink/test/depth-replay.py \
  /home/user/source/a9-display-evidence-20261012/vendor/vendor.qti.hardware.display.composer-service \
  /home/user/A9-wt/run10/runs/20261016T005652Z-mainline-android/android \
  /home/user/A9-build/mainline/sol-fix-0037/depth-replay'
```

Derived tables/images remain outside Git. See the task report for final patch
hash, module path and test results. The pattern suite checks all pixels,
orientation, bounds and exact production sysfs/job/KMS functions; existing
41 stall/ring/interlock regressions remain mandatory.
