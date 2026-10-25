# Rear AF and torch — host implementation

CAM-AF-TORCH adds the rear DW9714P lens, PMI632 channel-0 torch, Android
controls and independent acceptance measurements. Host-built; **no phone
access or hardware qualification**. The HI556 front still has neither control.
Exposure-synchronised flash is outside this change; SINGLE is rejected and
AE auto-flash modes are not advertised.

## Stock evidence and licensing

Only existing A9 blobs were analysed. No proprietary source was downloaded or
copied into the implementation. Downloaded reference code is GPL kernel code
from android.googlesource.com. Proprietary inputs/excerpts stay in the local
build directory and are not part of this repository.

Rear module SHA256:
`c1b737cabb0d629b54573fc26de77417d7611f0cb1962176ddb0d5e486424d03`.
The Parameter Parser V2 records use little-endian 64-bit slots. Addresses below
are file offsets, also REA addresses when the data blob is mapped at zero.

| Location | Observation | Implementation |
|---|---|---|
| `0x172d8`, actuatorDriver | slave `0x18`, 10-bit DAC, initial code116; region reference | 7-bit CCI address `0x0c`, same shared GPIO93 2.8V supply |
| `0x21870`, registerParam | `[1,2,3,0,1,0,0,0,0]` | register `0x03`, big-endian two-byte DAC; **not** upstream DW9714's raw two-byte command |
| `0x218b8`, six init records | ED=AB;02=01;02=00 then100µs;06=84;07=01;08=71 | exact writes and wait on runtime resume |
| `0x21af8`, region |400 steps, start0, float code increment1.0, auxiliary q-value128 | bounded stock fallback codes116..515 |
| `0x21b28` / `0x21b50` | scenario records `[511,7000,7]` / `[511,4500,7]` | use the slower7ms settling interval; park in smaller32-code steps, then standby |
| `0x17df0`, module configuration | double0.1 | nominal10-dioptre scan scale; **uncalibrated**, no per-unit EEPROM endpoints read |
| `0x17c88`, flashDriverData | rear flash `back_sensor_flash_single` | rear only; front never shares it |

REA module read evidence:
`ev_adb0d6f9ba9a91b02e2110f69b87c00bfe127db5c3af257d3f15068a1c4deaa0`.

Stock `camera.qcom.so` SHA256:
`35cd15607bfc8746be70f3e48342fab0f3d0839dd9b6e1fa483509cee9d4698d`.
Whole-library Ghidra analysis exceeded the tool deadline. A bounded instruction
excerpt, bytes/VA `0x83a27c..0x83a3af`, was analysed instead, retaining the
original addresses (ELF file offset equals VA in this load segment). Excerpt
SHA256 `53e058a4db758ebc506f2d6083c56ddbc1b6e5e9c624bdf31921ad7406c37a75`;
REA instruction evidence
`ev_20f1e6e59b853a146b6e4d0056a4050c6f5df72b3f842c287565194ae17aaf2f`.

The loop loads the initial signed DAC at `0x83a2b0`, stores it at `0x83a390`,
loads the region's float increment at `0x83a398`, adds it at `0x83a364`, and
iterates while the count is below the region endpoint at `0x83a374`. With the
module fallback, 400 entries are116..515, not116..516 and not a second division
by128. This is an interpretation of a **partial instruction excerpt**, not
complete function decompilation or observed stock motion. Per-unit EEPROM can
change stock endpoints. The proposed fallback scan and linear dioptre mapping
need near/far optical verification; Android reports UNCALIBRATED.

Reproduce the module decode/excerpt without network or a device:

```sh
python3 -I scripts/mainline/cameras/extract-af-torch.py \
  --rear /home/user/A9-build/mainline/cameras-20261020/rear.bin \
  --camera-hal /home/user/A9-build/mainline/cam-af-torch/camera.qcom.so \
  --out /home/user/A9-build/mainline/cam-af-torch/evidence
```

The existing merged stock DT identifies USID3/peripheral `0xd300`, rear
flash0/torch0, switch0 mask`0x03`, current100mA for torch0, short/open/VPH-droop
detection and thermal derating. Switch0's mask does **not** imply both LEDs
should light: downstream `qpnp_flash_led_switch_set()` intersects the mask with
active `fnode[].led_on` before writing the channel-enable register. Rear torch0
populates channel0; the implementation enables bit0 only.

Reference kernel sources:

- [GPL flash-v2 driver, pinned commit](https://android.googlesource.com/kernel/msm/+/e1e85fa160463d8c5e55c58c1806668e9740a117/drivers/leds/leds-qpnp-flash-v2.c): register map, current encoding, switch selection, PMI632 VREG polling. Decoded source SHA256 `8b6a4d23a5b33b282fc9182d9cec7095d8a5a6f5121cc8c6fb7c1e80e74a43af`.
- [GPL SCHGM flash implementation](https://android.googlesource.com/kernel/msm/+/refs/heads/android-msm-coral-4.14-android13/drivers/power/supply/qcom/schgm-flash.c) and [header](https://android.googlesource.com/kernel/msm/+/refs/heads/android-msm-coral-4.14-android13/drivers/power/supply/qcom/schgm-flash.h): USID2 `0xa607` bit4 is VREG_OK; `0xa608[2:0]==7` is flash error. Downloaded implementation SHA256 `0fbf862bce5dee7ff8a3a9e5feece99e7375e729b3f54a3a1270ba39a4d9c9e0`, header `5174a405a0008af202fdae193a929b6e1b217eb20b0822ff3a75790783b033ee`.
- [GPL SMB5 power-supply handler](https://android.googlesource.com/kernel/msm/+/refs/heads/android-msm-coral-4.14-android13/drivers/power/supply/qcom/qpnp-smb5.c): FLASH_ACTIVE imposes a5V input restriction; it is not a hidden boost-enable bit. Downloaded SHA256 `7b2a62ab8c0221fbe20de2de90f56c8be303306f6b2bb8ed1949fa4661fcd738`.

The integrated A9 charger already disables HVDCP and uses5V only. This change
makes **no charger register writes**. The boost phandle supplies a read-only
SPMI regmap. The generic7.2.10 `leds-qcom-flash.c` lacks PMI632-specific boost
readiness and explicit PMI632 support, so it is not bound to this peripheral.
The dedicated driver checks flash type`0x18` and logs the actual subtype; no
unmeasured subtype is asserted as a hardware observation.

## INTEG25 dedicated-probe repair

The retained INTEG25 rear-control result had33 ACTIVE_SCAN results,35 moving
results and a10000-millidioptre span, but no focused result; capture torch had
no FIRED or READY result despite CameraManager on/off callbacks. The later
rear video, in the same boot, returned293 focused results and46 FIRED results.
These summaries prove that the video path reports both controls. They do not
say whether the dedicated AF scan ended unsuccessfully or its results arrived
outside the probe window: the old probe did not retain that distinction.

The old control sequence began with PREVIEW/AF_OFF, changed to AUTO, waited
up to35 seconds for AF success, and only then requested torch. Video used
RECORD/AUTO from startup, an AF trigger and torch together. The control probe
now uses that working request sequence with a YUV sink, independent of codecs.
Its capture torch request lasts two seconds, then OFF is requested before
waiting up to eight seconds for delayed OFF metadata. Kernel fault/close and
five-second torch limits are unchanged. Both torch interfaces still turn off
in finally. This is a probe repair, not a new lens/LED/HAL implementation.

The comparison is against the stock Android Camera2 reporting contract, not
an unretained stock callback trace. AUTO uses one START request followed by
IDLE; FOCUSED_LOCKED means successful lock, while NOT_FOCUSED_LOCKED means
terminal failure. Android explicitly permits a direct INACTIVE-to-focused
result without reporting the transient scan. Therefore scan count, lens-motion
count and position span remain diagnostic fields, not additional conditions
for accepting a genuine focused lock. See [AOSP 3A state semantics](https://source.android.com/docs/core/camera/camera3_3Amodes)
and [CaptureResult AF transitions](https://developer.android.com/reference/android/hardware/camera2/CaptureResult#CONTROL_AF_STATE).

CameraManager torch callbacks describe the module path. Capture proof still
requires actual result FLASH_MODE_TORCH/FLASH_STATE_FIRED followed by
FLASH_MODE_OFF/FLASH_STATE_READY, plus both manager callbacks; callback success
alone cannot pass. These are separate [Camera2 flash metadata fields](https://developer.android.com/reference/android/hardware/camera2/CaptureResult#FLASH_STATE).
Immutable request tags associate baseline/on/off results with the correct
phase even when callbacks arrive late. New af_unfocused and torch_on_frames/
torch_off_frames counters distinguish focus failure from missing capture
metadata. No capture evidence is copied from video into the dedicated rows.

One host regression compiles the full Probe.java with the cached Android SDK,
replays its actual classifier on a JVM, and checks independent row verdicts:
`python3 -I scripts/mainline/acceptance/test-camera-controls.py`.
It covers skipped intermediate AF states, unsuccessful lock, stale baseline
metadata, wrong flash mode, missing FIRED/OFF proof and incomplete probes.
The fixed dedicated requests still need an owned phone sweep and optical
confirmation; no new physical PASS is claimed by host replay.

## Implemented path

- Lens: register-addressed DW9714P module, sensor `lens-focus`, shared regulator,
  stock init, bounded controls and ramp/standby on last close/runtime suspend.
  CameraLens reopens when moving and closes on pipeline stop. Repeated focus
  writes execute after resume even if the cached V4L2 control value matches.
- AF: stock fallback range, three-frame settling, existing contrast search,
  expanded RAW10 centre-window sharpness, and failed AF results after a lens
  I2C error. Flat/no-statistic input cannot produce a focused result. Colour
  packaging retains the AF block from `rear-af.yaml`.
- Torch: sensor `flash-leds` ancillary, V4L2 NONE/TORCH at100mA, one opener,
  channel0 only, boost-ready wait and register readback. Short/open/droop
  detection and thermal ramp backup are enabled; thermal thresholds remain
  unchanged. Any thermal warning/fault stops the torch. Close, suspend and
  provider death switch it off. A five-second absolute deadline is not extended
  by repeated TORCH requests; a timeout requires OFF before another activation.
- Android: simple pipeline discovers the linked torch device; the HAL gates
  FLASH_INFO_AVAILABLE on that device/control. CameraManager setTorchMode,
  availability/on/off callbacks, capture-request OFF/TORCH, FIRED/READY result
  metadata and error/flush/close shutdown use that same exclusive V4L2 device.
  FIRED means the channel-enable readback succeeded, not an optical measurement.

## Build and host evidence

A normal fresh camera build includes both modules. For an existing media build,
use `build-af-torch.py` with the **next image's** `--base-obj` configuration and
export table. The media exports are separate; pass `--media-symvers` if the
camera build keeps them elsewhere. Do not substitute an older kernel's CRCs.

```sh
# In the existing Nix kernel compiler environment, including bc:
python3 -I scripts/mainline/cameras/build-af-torch.py \
  --kernel-source /home/user/A9-build/mainline/linux-7.2.10 \
  --base-obj /home/user/A9-build/mainline/integ19-host/kernel/obj \
  --camera-build /home/user/A9-build/mainline/cameras-20261020 \
  --out /home/user/A9-build/mainline/cam-af-torch/kernel-integ19
```

Build/package a clean libcamera tree with the complete patch set using existing
`android/build.sh` and `android/package.py`; changing `.patched` on an old tree
is not a patch application procedure. This worker's independent Android build
is `/home/user/A9-build/mainline/cam-af-torch/android`. The integrated builder
accepts `--camera-af-torch-build .../kernel-integ19` and
`--camera-android-build .../android`; it stages the DT and both modules together.
Rebuild acceptance assets for the new `camera_controls` probe.

Local evidence root: `/home/user/A9-build/mainline/cam-af-torch/`.
Host checks cover module/HAL/APK compilation, dual DT overlay merge, packaged
colour+AF profile, production statistics/search/torch replay under Android
QEMU, and acceptance verdicts. Commands:

```sh
python3 -I scripts/mainline/cameras/tests/af-torch.py --build /home/user/A9-build/mainline/cam-af-torch/android
python3 -I scripts/mainline/acceptance/test-camera-controls.py
python3 -I scripts/mainline/acceptance/selftest.py
python3 -I scripts/mainline/acceptance/test-sweep17.py
```

## Next coordinator phone run

No phone command has been run by this worker. Include the new modules, HAL,
tuning, DT and acceptance APK in the next owned mainline image. Expect:

1. Rear graph has `dw9714p-a9 ...-000c` lens and `a9-rear-torch` ancillary;
   front has neither. Kernel logs the measured flash type/subtype. Lens init
   failures must leave AF unavailable; torch failures must not claim FIRED.
2. With a textured near/far target, inspect manual focus at both endpoints,
   visible focus change, AF scanning and genuine focused/not-focused results.
   `19_rear_af` requires a genuine focused lock; scan, lens motion and position
   span are diagnostic observations. Flat targets may correctly fail to focus.
   Verify parking on close.
3. Rear LED visibly lights at100mA. CameraManager on/off callbacks and capture
   TORCH/FIRED then OFF/READY must agree. `19_rear_torch` requires both paths.
   Confirm actual light and off on close/error/provider exit, plus the5s bound.
   Probe luma summaries aid diagnosis but do not replace looking at the LED.
4. Front AF/torch remain NOT_RUN with `not_applicable=1`. The separate control
   measurements for both cameras run before either video and can pass even if
   video encoding fails. Integ24's rear controls ran after a front shutdown
   stall and were rejected by CameraService; zeros were not a lens/torch
   hardware diagnosis. Summaries now retain the camera stage and error.

Optical focus calibration, real LED current/light and real fault/cleanup
behaviour remain phone qualifications. No runtime PASS is claimed here.
