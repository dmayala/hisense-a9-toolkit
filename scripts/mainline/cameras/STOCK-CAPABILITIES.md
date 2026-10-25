# A9 autofocus and flash evidence

The rear camera has both an actuator and flash. Its missing mainline controls
are implementation gaps, not hardware N/A. The front camera has neither, so
its AF/torch rows are NOT_RUN with `not_applicable=1` when the camera exists
and the corresponding capability is absent. Missing camera/metadata remains
an unattempted prerequisite, not evidence of absent hardware.

Offline inputs extracted from the stock vendor camera directory:

| Module | SHA256 | Actuator driver | Flash driver |
|---|---|---|---|
| `com.qti.sensormodule.s5k3l6_bnd32b_shine.bin` | `c1b737cabb0d629b54573fc26de77417d7611f0cb1962176ddb0d5e486424d03` | `s5k3l6_dw9714p_bnd32b_shine` | `back_sensor_flash_single` |
| `com.qti.sensormodule.hlte556n_shine_hi556_front.bin` | `fb6e5f337a6649459549cd05d62f07eb0ec154c48d505c1b22ac04f69f16490a` | absent | absent |

The existing `extract-stock.py` Parameter Parser V2 reader finds a populated
240-byte `actuatorDriver` and 120-byte `flashDriverData` in the rear module.
The front's `actuatorName` and `flashName` records are empty and it has neither
driver record. The stock merged DT additionally has an enabled rear
`qcom,camera-flash@0`, linked flash/torch/switch sources, and enabled
`qcom,actuator0` on CCI master 0 with a 2.8 V supply. These are shipped artifact
configuration observations, not a new runtime stock CameraCharacteristics dump.

Local reproduction inputs are `A9-build/mainline/cameras-20261020/{rear,front}.bin`
and `A9-build/mainline/sol-wifi/{stock-overlay,stock-merged}.dts`. Proprietary
module payloads are not committed. The current mainline rear sensor has no
lens link, the kernel configuration disables VIDEO_DW9714, and the libcamera
Android HAL hardcodes FLASH_INFO_AVAILABLE false. Exposing metadata alone
would not implement either operation.

## Integ18 implementation scope

Integ12 and18 both measured rear `af_available=0, torch_available=0` in
completed videos. Integ17's absent video result never measured capabilities.
This is a pre-existing kernel/graph/HAL gap, not a new integ18 regression.
The rear still timeout is a separate measurement; neither AF nor flash is
requested by that probe.

| Layer | Autofocus work | Torch / flash work |
|---|---|---|
| Stock facts to preserve | DW9714P variant, CCI master0, shared GPIO93 camera LDO,2.8V cam_vaf; decode stock actuator address/init/parking and module calibration before selecting the upstream driver. | PMI632 USID3 peripheral0xd300; stock rear camera links flash0/torch0 and switch0. Resolve the switch's channel mask before mapping it to upstream LED channels. |
| Kernel / DT | Integ18 disables `CONFIG_VIDEO_DW9714`; add the matching driver/module and CCI lens node, correct shared supply, and rear sensor `lens-focus` link. Verify the DW9714P command format instead of assuming the upstream DW9714 initialization is identical. | `CONFIG_LEDS_QCOM_FLASH=m` is already present. The mainline PMI632 DTS has no flash node. Verify the existing generic driver's discovered type/subtype/register support, add the matching PMI632 binding/DT node, LED limits and camera `flash-leds` link. A Kconfig change alone cannot expose torch. |
| libcamera / Android | Existing A6L port already routes `V4L2_CID_FOCUS_ABSOLUTE` through CameraLens to the simple IPA and translates AfMode/AfState/LensPosition. Supply A9-specific DAC endpoints, dioptres and settling calibration; inherited A6L lens tuning is not an A9 calibration. Confirm AF statistics on the A9 processed RAW path. | Android `camera_capabilities.cpp` currently hardcodes FLASH_INFO_AVAILABLE false. Implement request/result controls and torch callbacks backed by the actual LED/V4L2 device, including off on close/error, before advertising availability. The simple pipeline also needs to discover/control the flash device. |
| Qualification | Prove lens enumeration, bounded motion/parking, manual near/far image focus, then an AF trigger with genuine focused/not-focused state and observable focus change. | Prove the LED's channel and current limits, bounded on/off including failure/close, then CameraManager torch and capture-request torch with actual light. Flash exposure synchronization is additional work beyond torch. |

The stock DT's rear torch0 default is100mA with a500mA maximum; flash0's
default is1000mA with1500mA maximum and1280ms timeout. Those are shipped
configuration values, not permission to copy the maxima into an unqualified
mainline driver. Keep thermal, short/open circuit and VPH-droop protections.
Do not alias rear controls to the front camera or turn availability metadata
on before the device/control path exists.

This scope changes no driver, DT, power setting or capability verdict. Cached
source references: `linux-7.2.10/drivers/media/i2c/dw9714.c`,
`drivers/leds/flash/leds-qcom-flash.c`, their binding YAMLs,
`cameras-20261020/android/libcamera/src/{android,ipa/simple,libcamera/pipeline/simple}`,
and the exact stock inputs listed above. Stock nodes and existing parameter
records suffice for this scope; address/variant/calibration decoding remains
part of implementation, so no speculative lens or LED writes are added.

## CAM-AF-TORCH implementation

The host implementation, decoded stock addresses, kernel/HAL path, build inputs
and next phone qualifications are in [AF-TORCH.md](AF-TORCH.md). Rear AF and torch
now have real control paths and independent acceptance checks; their hardware
qualification remains pending. Front capability handling is unchanged.
