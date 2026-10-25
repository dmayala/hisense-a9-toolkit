# SM6115 Venus and Android16 Codec2 POC

Host-built; phone decode untested. Adds video DT, modular V4L2 media, firmware,
native decode probes and an Android16 HIDL1.2 Codec2 HAL to R4. Integrated d
reached TrustZone but its public 6.0.55 firmware metadata was rejected (-22).
Packaging now uses the A9's OEM-signed 6.0.39 firmware and decoder compatibility
described below. The earlier same-file Wi-Fi bridge copy failure remains fixed.

Linux 7.2.10 already implements AR50_LITE in Venus: HFI4, QCM2290 match, PAS **9**,
and the A9's protected address ranges. Its SM6115 binding permits the QCM2290
fallback. Our node follows [the upstream SM6115 node](https://lists.openwall.net/linux-kernel/2026/05/15/1156).
The existing 5 MiB `pil_video_mem`, SIDs 0x860/0x880 and IRQ 225 match stock;
no reservation is changed. The built-in GCC provider needs the power fix below.

Stock NON-HLOS contains `VIDEO.VE.6.0-00039-PROD-1`. The packager extracts its
`image/venus.*` files from `$A9_DATA/HLTE556N/NON-HLOS_001360E1.bin` when the local
`--stock-firmware-dir` cache is absent, verifies the existing stock hashes and
joins segments at their original ELF offsets. The signed metadata passed to PAS
is byte-identical to stock `venus.mdt`; firmware code and signatures are intact.
Joined SHA256: `44c39a0ff5a25433626ae4b21e3267b0713cdf4c5338cdf88763489d72a3da05`.
No firmware binaries are committed.

Use the board-specific stock variant: the generic `NON-HLOS.bin` has identical
Venus load segments but different signing metadata and was rejected on image e.
The board-specific Venus CA keys match those in e's working ADSP firmware; the
generic variant's do not. Cache it under `stock-firmware-a9` so the older generic
`stock-firmware` cache cannot be reused accidentally. To build just the firmware
vendor overlay without rebuilding any kernel or Codec2 binaries:

```sh
python3 -I scripts/mainline/video/build.py firmware \
  --out "$HOME/A9-build/mainline/video-20261020/qualified-firmware"
```

`sm6115-stock-firmware.patch` adds a native SM6115 decoder-only resource with
minimum6.0.39 and the older AR50_LITE one-byte EOS quirk from the
[upstream v7 implementation](https://patchew.org/linux/20250715204749.2189875-1-jorge.ramirez%40oss.qualcomm.com/20250715204749.2189875-4-jorge.ramirez%40oss.qualcomm.com/).
Upstream later dropped older firmware support rather than retaining the
[decoder-only compatibility path](https://lkml.indiana.edu/2508.0/05851.html).
QCM2290 retains its6.0.55 minimum; SM6115 has no encoder child. PAS9 initialization
and authentication are unchanged. Image f accepted the firmware, probed the
decoder and registered Codec2, but native H.264/HEVC STREAMON timed out.

`sm6115-vcodec-hw-trigger.patch` backports the
[upstream SM6115 VCODEC0 GDSC fix](https://www.spinics.net/lists/kernel/msg6190927.html).
Stock declares `qcom,support-hw-trigger` for this domain. R4 lacks the equivalent
`HW_CTRL_TRIGGER` flag, so `dev_pm_genpd_set_hwmode()` returns `-EOPNOTSUPP` and
Venus polls legacy wrapper power registers during core acquisition at STREAMON.
Session initialization and buffer-requirement replies already succeeded before
STREAMON, so IRQ/HFI transport worked at that point. The missing provider flag
is an evidenced candidate for the remaining timeout, not a proven phone fix.
Venus now logs legacy power-on failure and names any timed-out HFI session call.

The suspend kernel builder applies the GCC patch in private source files,
including when `--source` reuses the old cached watchdog tree. **Rebuild the
built-in kernel**; replacing Venus modules or reusing the old `--kernel-build`
does not install the provider fix. No Iris backport or DT change is required:

```sh
K=$HOME/A9-build/mainline/video-20261020/power-kernel
python3 -I scripts/mainline/suspend/build.py kernel --out "$K" \
  --source "$HOME/A9-build/mainline/suspend-20261020/kernel"
python3 -I scripts/mainline/suspend/build.py package --no-vendor --out "$K"
bash scripts/mainline/build-integrated.sh --audio --cellular \
  --kernel-build "$K" --out "$HOME/A9-build/mainline/integrated-20261020g"
```

Host build:

```sh
O=$HOME/A9-build/mainline/video-20261020
# For a new cache, build the media extension once. Package extracts stock FW.
python3 -I scripts/mainline/video/build.py kernel --out "$O"
# Populate the hash-pinned public Codec2 source cache on a fresh host:
python3 -I scripts/mainline/video/codec2/build.py --out "$O/codec2" \
  --overlay "$O/codec2/stage" --fetch
bash scripts/mainline/build-integrated.sh --video --video-build "$O" \
  --kernel-build "$K" --without battery --without sensors --without vibration \
  --without cameras --without gnss \
  --out "$O/integrated"
```

The reduced invocation isolates B10 on the corrected R4 derivative. `--video` is
enabled by default in the normal integrated
invocation for cumulative parity. DT packaging precedes vendor refresh and the
vibration DT step. Its media modules join the final snapshot. `--video-build`
selects staged R4-compatible common `built-modules/` and the stock firmware
cache. When cameras are included, the packager uses that media build's config
and symbol exports. It privately rebuilds only the three Venus modules with the
stock compatibility patch, preserving all common media/camera modules. The
recorded recipe and logs are under the output's `video/stock-compat/`.
`--video` also rebuilds/stages Codec2 from `$O/codec2`; add
`--video-kernel-only` to omit Android startup/HAL while diagnosing native V4L2.
Codec2's `--system` defaults to the cached matching LOS23 GSI system tree at
`~/A9-build/mainline/adsp/d2-v8-20261017/system-image/system`. It extracts the
swcodec APEX libraries and records their hashes; do not mix another GSI's libs.
The small host build uses the same cached Nix/NDK/otatools toolchain as R4.

The R4 source is read-only, with private directories for the external media
build. Compiler argv, config, symbol tables, staged modules and logs remain;
generated source links/intermediates are pruned. Use a fresh output for a new
kernel build. With Codec2 included, init runs `a9-video-start` at post-fs-data:
it loads media modules, waits up to five seconds for the Venus decoder device,
grants media access and starts the HAL only after success. The manual
`a9-video-load` remains available. It stages firmware only if absent from the
kernel's selected RAM firmware bridge.

Coordinator phone steps, **not run by this worker**:

1. Use the existing slot-B POC boot/keeper workflow with
   `$O/integrated/{boot.img,vendor.img}`,
   `A9_WIFI_SNAPSHOT=$O/integrated/snapshot`, and
   `A9_WIFI_BINARIES=$HOME/A9-build/mainline/sol-wifi/run14-fix/android`.
   The existing host Wi-Fi image verifier passes with those exact inputs.
2. In the existing root shell, run `/vendor/bin/a9-video-load`, then:

   ```sh
   ls -l /dev/video*
   for n in /sys/class/video4linux/video*; do echo "$n: $(cat "$n/name")"; done
   dmesg | grep -iE 'venus|video-codec|firmware|smmu|iommu'
   ```

   Select the node named `qcom-venus-decoder`. On that selected node run
   `/vendor/bin/v4l2-ctl -d /dev/videoN --all --list-formats-out`.
   Do not guess the index or open camera nodes.
3. Copy the local generated `test.h264` and `test.hevc` into RAM
   `/data/local/tmp`, then in the root shell, replacing `videoN`:

   ```sh
   /vendor/bin/venus-one-frame /dev/videoN h264 /data/local/tmp/test.h264 /data/local/tmp/h264.nv12
   sha256sum /data/local/tmp/h264.nv12
   /vendor/bin/venus-one-frame /dev/videoN hevc /data/local/tmp/test.hevc /data/local/tmp/hevc.nv12
   sha256sum /data/local/tmp/hevc.nv12
   ```

   Each should print `DECODED 1 frame 320x240 NV12` and write 115200 bytes.
   Expected SHA256: H.264
   `25dcf65aa2fbccf702c9066f5f275b185191769bcadb298807f7029d3b13d6e3`;
   HEVC `9fed1538de79414b7227cfbd4b5dedf8d0b9b17de2e68684de913ddc4e61c2a2`.

The tool submits one IDR access unit, handles source change, maps capture buffers,
drains and writes visible NV12 rows. No software decoder or screenrecord is used.
Its ioctl path is untested; timeouts/errors are not acceptance. Fixtures came from
cached FFmpeg 7.1.1: `testsrc2=size=320x240:rate=1`, one frame, yuv420p, libx264
baseline (`keyint=1:bframes=0`) or libx265
(`keyint=1:bframes=0:pools=1:frame-threads=1`). References decode those exact streams
to NV12 in FFmpeg. Fixture binaries stay in the local build directory.

Codec2 sources are pinned by content-tree SHA256 in `codec2/sources.lock.json`
(observed archive hashes are also recorded; Gitiles tar metadata can change), with
A6L decoder adaptations described in [PATCHES.md](codec2/PATCHES.md). The HAL
retains Android16 HIDL1.2, its base/extended seccomp policy and minijail startup.
Its 85 platform libraries are private to `/vendor/lib64/a9-video`; service init
sets that LD path. The vendor image retains its existing graphics libraries.
Init selects `media.c2.hal.selection=hidl`, including after the GSI's
prefer-hardware action (which otherwise selects AIDL). Software Codec2 remains
the GSI's matching HIDL path. `media_codecs_c2.xml` lists nonsecure AVC/HEVC
decoders at rank256. Until the encoder hang is fixed, AVC/HEVC hardware encoders
use rank4096, behind the software encoders (default rank512). Android 16 applies
the XML rank over Codec2 traits, so MIME selection prefers software while the
row 18 probe can still select hardware by name. Properties and real driver
capabilities also gate advertisement. Host regression:
`python3 -I scripts/mainline/video/codec2/tests/encoder-rank.py`; use `--xml` for
the generated vendor XML and `--software-xml` for the GSI swcodec APEX XML.

The service and private dependency closure passed a QEMU `--link-check` that
returns before properties, binder, devices or minijail. This proves host linkage,
not service registration, allocation or decoding. After native decode passes,
the coordinator can test the Android path in the existing root shell:

```sh
getprop vendor.a9.video.ready
getprop init.svc.android-hardware-media-c2-v4l2-hal-1-2
getprop media.c2.hal.selection
lshal | grep android.hardware.media.c2
/vendor/bin/codec2-one-frame h264 /data/local/tmp/test.h264
/vendor/bin/codec2-one-frame hevc /data/local/tmp/test.hevc
dumpsys media.codec
```

Expected ready1/running/hidl, an IComponentStore/default HIDL1.2 service, and one
ByteBuffer frame plus EOS for each fixture. The helper calls
`AMediaCodec_createCodecByName` for the respective `c2.v4l2.*.decoder`; failure
cannot fall back to software. For a manual retry after fixing a load error, run
`start a9ml_video_load` as root. Capture Venus/kernel and `CCodec`, `V4L2*`,
`android.hardware.media.c2*` logcat errors before changing the next approach.

Then test a real video in a Surface player and confirm its named component in
`dumpsys media.codec`; check crop, seek/flush and EOS. Surface playback, longer
clips, power/PAS authentication, suspend, performance and enforcing SELinux
remain unqualified. This adds no encoder or secure playback support.
