# Hardware encoder discovery — INTEG14

Historical inventory before the port. The implementation and qualification
status are now tracked in [ENCODER-PORT.md](ENCODER-PORT.md).

The missing encoder is an implementation gap in the existing mainline stack.
It is not hardware N/A, and adding stock codec names to XML cannot repair it.
The current SM6115 Venus resource deliberately creates no encoder device; the
Codec2 store independently removes encoder registration. The accepted A9
firmware has a decoder compatibility path, but no corresponding encoder port.

Host-only audit of latest main, delivered INTEG14, the final INTEG14 sweep and
the read-only stock `HLTE556N/super_5.img`. No image, device operation or stock
library patch was made. This report records a blocker rather than claiming an
encoder fix or hardware qualification.

| Layer | Stock / working decoder comparison | INTEG14 encoder finding |
|---|---|---|
| Acceptance | Row17 decoded/drew60/60 with hardware1/software0. | Row18 completed enumeration at stage2, hardware_available0. It never reaches encoder configure/start, so this is not a buffer-input timeout. |
| Stock XML | `media_codecs.xml` and vendor variants declare `OMX.qcom.video.encoder.avc`, `.hevc` and `.hevc.cq`. | No vendor hardware encoder declaration. The audited stock package uses OMX names here; no `c2.qti.*.encoder` declaration was found in its media XML. Software Google/Android AVC/HEVC entries and performance overrides do not establish hardware availability. |
| Provider | Stock has `libOmxVenc.so` and `libOmxCore.so`. | Neither stock OMX library is installed. The active provider is the rebuilt HIDL1.2 V4L2 Codec2 service with private `lib64/a9-video/libv4l2_codec2.so`. Copying OMX XML would refer to a missing provider and a different downstream driver ABI. |
| Firmware | Packaged firmware is the accepted OEM-signed `VIDEO.VE.6.0-00039-PROD-1`. | SHA256 `44c39a0ff5a25433626ae4b21e3267b0713cdf4c5338cdf88763489d72a3da05` matches the locked joined stock payload. Successful decode does not establish compatibility with upstream's encoder commands. |
| Driver selection | Actual DT selects `qcom,sm6115-venus`, with QCM2290 fallback; its only static child is `opp-table`. | No explicit `video-encoder` child overrides the resource. `sm6115-stock-firmware.patch` leaves `sm6115_res.enc_nodename` NULL. `venus_add_video_core()` returns without creating a device for a NULL name. It is not a failed probe of a known `/dev/video1`. |
| Modules | Both `venus-dec.ko` and `venus-enc.ko` are packaged, and the loader requests both. | Loading the encoder module only registers its driver. It cannot bind when the parent deliberately creates no encoder child. Device indices also vary with CAMSS; video1 is not a safe assumption. |
| Firmware minimum | Existing ELF inspector verifies actual packaged SM6115 minimum6.0.39 and QCM2290 minimum6.0.55. The SM6115 patch restores an old-firmware decoder EOS quirk. | The minimum was lowered for the decoder only. No old-firmware encoder compatibility changes were added to `venc.c`. |
| Capability tables | The HFI4 implementation contains static AR50_LITE AVC/HEVC encoder capability tables for its supported driver/firmware path. | Their existence is not a measured successful encode with A9 firmware6.0.39. Exposing a node may expose those tables before a session actually works. |
| Codec2 discovery | Patch0001 safely discovers Venus sysfs names rather than opening arbitrary camera nodes. Patch0002 advertises enabled, device-backed decoders. | Patch0002 explicitly removes all three upstream `builder.encoder(...)` registrations. No encoder can be listed even if a device is added. Encoder buffer/profile adaptations were explicitly skipped in `codec2/PATCHES.md`. |
| XML / init | Actual `media_codecs_c2.xml` matches main and declares only `c2.v4l2.avc.decoder` and `.hevc.decoder`. Init enables only decoder properties and waits for a decoder. | No encoder policy or component is configured. The startup worker grants DAC ownership only to the decoder, another change required when an encoder device becomes real. |
| SELinux | Codec2 runs as `mediacodec`; `/dev/video[0-9]+` is `video_device`. Existing policy grants V4L2 ioctl range0x5600–0x56ff. | Six basic video-device policy decisions allow open/read/write/getattr/ioctl/map. No mediacodec AVC was found in the retained boot107 logcat rings. Missing node/registration precede access checks, so no observed denial explains this inventory result. Future encoder-specific access still needs verification once implemented. |

The firmware limit is specific to the upstream Venus combination, not proof
that stock firmware cannot encode. The author's
[AR50_LITE v7 series](https://patchew.org/linux/20250715204749.2189875-1-jorge.ramirez%40oss.qualcomm.com/)
places encoding support at6.0.54 and later; the eventual QCM2290 driver requires
6.0.55. Our earlier bring-up records rejection of the newer public signing
metadata; this audit verifies the currently accepted stock payload, not a new
authentication attempt. Stock's working downstream implementation is a reason
to investigate compatibility, not to claim that a version-number change fixes
it.

There is also a newer, distinct
[SM6115 Iris/HFI-Gen1 implementation](https://www.spinics.net/lists/kernel/msg6190926.html).
Its author reports an encoder compliance failure on RB2 and says the Gen2
firmware path does not yet work there. Our cached Linux7.2.10 Iris source lacks
AR50_LITE/SM6115 platform support, and INTEG14 uses Venus. Switching the DT
compatible or driver name would not install that implementation. This is a
possible porting direction, not an established A9 replacement.

The blocking work is an encoder-compatible A9 firmware/driver path: port the
downstream old-firmware protocol (or a suitable Iris Gen1 implementation), or
obtain a compatible OEM-authenticated newer firmware. Then adapt the existing
V4L2 Codec2 encoder buffers/profile defaults, restore device-backed registration,
grant its discovered node DAC access and add matching XML. Native encode with
real output and EOS must precede claiming MediaCodec hardware availability.
No claim is made here about the exact old-firmware command or property that
would fail first: the current image never creates or opens that encoder, and
the saved sweep contains no attempted encoder HFI transaction.

Host checks passed for actual XML/provider comparison, DT selection/children,
module firmware minima, firmware hash/version, six packaged-policy access
decisions and saved sweep results. Evidence is under
`A9-build/mainline/encoder-host-20261024/`: stock/mainline XML excerpts,
`module-firmware-check.log`, `acceptance-media.json` and `evidence.json`.
The policy queries reuse INTEG14's existing host parsing copy, which removes
only the unsupported FunctionFS policy-capability bit; it is not loaded into
any kernel. All existing images and the working decoder are unchanged.
