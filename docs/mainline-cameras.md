# A9 camera kernel POC (2026-10-20)

Status: **rear Camera2 JPEG works on integrated j** (coordinator round7 result).
The default host-built graph now includes rear S5K3L6 and front Hi556 together;
front probe/raw/Camera2 capture still needs the coordinator's device test.
`--camera-rear-only` retains the proved rear-only graph and `--camera-front-probe`
selects front alone. This worker is host-only. Earlier per-round sections below
record the history; the round7 section describes the current graph and probe.

## Stock mapping

Read-only inputs: the cached stock vendor extraction
`~/A9-build/mainline/android/ref/vendor_a.img`, and decoded stock base plus board
DTBO in `/tmp/rea-stock-{base0,overlay0,merged}.dts`. Their existing DT provenance
is in `/tmp/rea-stock-dt-notes.md`; underlying base DTB SHA256
`95e54a3f14e1dcbb4a42b5c090f3b34fd48120000d4ed09e7bb58cff6d5a74df`,
board DTBO SHA256 `74138d6535db5897db0c80ae093dc5840e8c51cf578b5d395499ded185069931`.
These are stock file observations, not measurements of a running sensor.

| Item | Rear | Front |
|---|---|---|
| Module | `s5k3l6_bnd32b_shine` | `hlte556n_shine_hi556_front` |
| Sensor | Samsung S5K3L6 | Hynix Hi556 |
| CCI controller/master | 0 at `0x05c1b000`, master 0 | same |
| Address, 7-bit (stock 8-bit) | `0x10` (`0x20`) | `0x20` (`0x40`) |
| ID register / expected value | `0x0000` / `0x30c6` | `0x0f16` / `0x0556` |
| CSI PHY | 0, `0x05c52000` | 2, `0x05c54000` |
| Data lanes / stock laneAssign | 4 / `0x3210` | 2 / `0x10` |
| MCLK mux / pin | MCLK0 / TLMM20 | MCLK2 / TLMM27 |
| MCLK in sensor power table | 24 MHz | 24 MHz |
| Reset | TLMM18, physical low asserted | TLMM24, physical low asserted |
| External rail enable | TLMM93, high enabled | TLMM106, high enabled |
| VIO / VANA / VDIG | GPIO93 bank / same / same | GPIO93 bank / GPIO106 bank / GPIO106 bank |
| Nominal DT rail values | 1.8 V / 2.8 V / 1.05 V | same |
| Selected stock mode | 2104×1560, RAW10, 30 fps | 2592×1944, RAW10, 30 fps |
| Line/frame lengths, VT clock | 4896 / 3260 / 480 MHz | 2816 / 1988 / 166.4 MHz |

CCI master 0 uses TLMM22/23 (`cci_i2c`); unused master 1 uses TLMM29/30.
The rear DT's fallback clock value is 19.2 MHz, but its sensor module explicitly
requests 24 MHz in the power sequence and programs `0x0136=0x1800`; this POC
uses that sensor module value. Generic front-reset pinctrl in the base DT says
GPIO19; the board sensor node actually supplies GPIO24. Use the board node.

Rear stock power-up tuples are reset-low/5 ms, VIO-on/1 ms,
MCLK-24 MHz/10 ms, reset-high/10 ms. Power-down is MCLK-off/1 ms,
reset-low/5 ms, VIO-off/1 ms. The driver reproduces those waits. All rear
rails are controlled by one fixed-regulator GPIO93 reference. GPIO106, the
front reset and MCLK2 are not touched by this rear-only candidate. Focus
actuator, EEPROM and PMI632 flash are not enabled.

The stock PHY analogue rail is PM6125 L18, requested at 1.2 V. The SM6115
resource table uses this supply alone; it does not invent a separate 1.8 V
PHY regulator from the QCM2290 resource template.

## Implementation and build

Linux 7.2.10 already has QCM2290 CSID/VFE 340 operations and CSIPHY register
programming. `camss-sm6115.patch` adds an SM6115 resource table with three
PHY/CSID/VFE instances, retains those operations and adds the
`qcom,sm6115-camss` match. The third blocks use stock PHY2 `0x05c54000`,
CSID2 `0x05c7c000` (IRQ77), VFE2 `0x05c7d000` (IRQ78). The CSID/VFE address
split follows the upstream QCM2290 layout within stock's combined TFE range.
Its applicability to SM6115 remains a hardware-test hypothesis.

`extract-stock.py` decodes the stock modules' named symbol records and
8-byte-aligned values, preserving register widths, order and per-write delays.
It writes local JSON and a rear C header. No vendor module, generated register
table, microcode or calibration blob is committed. Extracted inputs:

- Rear module SHA256 `c1b737cabb0d629b54573fc26de77417d7611f0cb1962176ddb0d5e486424d03`.
- Front module SHA256 `fb6e5f337a6649459549cd05d62f07eb0ec154c48d505c1b22ac04f69f16490a`; decoded JSON is retained in the build output.

The S5K3L6 V4L2 code adapts the GPL A6L `kernel/camera/s5k3t1.c` plumbing,
with A9 tables and power timing. It exposes one binned mode and the controls
needed by libcamera. Default GRBG Bayer order and visual orientation are provisional
until a captured scene verifies them. The
[published S5K3L6 RFC](https://www.spinics.net/lists/kernel/msg6283190.html)
targets a different 25 MHz/two-lane setup, so its mode tables are not used.

All media components are modules. The image keeps the **exact R4 kernel,
ramdisk, second stage and recovery DTBO**; only the board DTB changes. New
modules are built from R4's source and configuration plus the media fragment,
using R4's `Module.symvers`. Existing R4 modules are unchanged. This avoids a
full kernel rebuild and stays within the shared disk budget. `obj/.config`
describes the media build extension; `snapshot/config` describes the actual
unchanged R4 kernel. Module version/import checks establish a host compatibility
check, not proof that the modules load or work on the phone.

From `fix/cameras` (host only):

```sh
O=/home/user/A9-build/mainline/cameras-20261020
V=/home/user/A9-build/mainline/android/ref/vendor_a.img
debugfs -R "dump /lib64/camera/com.qti.sensormodule.s5k3l6_bnd32b_shine.bin $O/rear.bin" "$V"
debugfs -R "dump /lib64/camera/com.qti.sensormodule.hlte556n_shine_hi556_front.bin $O/front.bin" "$V"
python3 -I scripts/mainline/cameras/extract-stock.py "$O/rear.bin" --out "$O/rear"
python3 -I scripts/mainline/cameras/extract-stock.py "$O/front.bin" --out "$O/front"
python3 -I scripts/mainline/cameras/build.py kernel
python3 -I scripts/mainline/cameras/build.py package
```

The exact cross-toolchain invocation is in `$O/make-command.json`: standard
integrated `aarch64-unknown-linux-gnu-`, `ARCH=arm64`, `LOCALVERSION=+`, `-j8`;
steps `olddefconfig`, `modules_prepare`, then `M=drivers/media modules`.
Private source files are materialized before editing; symlink inputs stay
read-only. A kernel step needs a fresh output staging directory. Package uses
main's existing `build-vendor.sh --refresh-integrated --overlay ...`; its exact
main source commit and substitutions are recorded in `vendor/build-result.json`.
Those concurrent main changes are separate from this branch's camera delta.

## Coordinator phone test

Use the coordinator's existing slot-B boot path with these replacements:

- Boot: `~/A9-build/mainline/cameras-20261020/boot.img`.
- Vendor: `~/A9-build/mainline/cameras-20261020/vendor/vendor.img`.
- Matching snapshot: `~/A9-build/mainline/cameras-20261020/snapshot`.
- Keep the existing system image and the snapshot's null `recovery_dtbo.img`.

The existing wrapper takes `--boot`, `--vendor`, `--wifi --eink`,
`--hold 900 --no-restore`, with `A9_WIFI_SNAPSHOT` set to this snapshot and
`A9_WIFI_BINARIES=~/A9-build/mainline/sol-wifi/run14-fix/android` (the R4 binary
set; the default older `sol-wifi/build/android` is different). Retain the
coordinator's existing lifecycle/restore ownership. No new launch harness is
needed. The commands below run **inside the coordinator's existing root shell**
after boot; this worker has not run them:

```sh
/vendor/bin/sh /vendor/bin/a9-camera-load
dmesg | grep -E 's5k3l6|cci|camss|csiphy|csid|vfe' | tail -n 100
/vendor/bin/a9-camcap -l
/vendor/bin/a9-camcap -s s5k3l6 -p 0 -c 0 -W 2104 -H 1560 -n 4 -o /data/local/tmp/a9-rear.raw
```

The first success condition is `Samsung S5K3L6 chip id 0x30c6` in the kernel
log. Then the media graph should include the sensor, `msm_csiphy0`, `msm_csid0`,
`msm_vfe0_rdi0` and `msm_vfe0_video0`. The helper sets links/formats, captures
four settling frames then saves one RAW10 frame. A timeout returns failure.
Save its full output, relevant kernel errors and the raw file through the
coordinator's established channel. Report the actual stride, bytes and return
status; node presence alone is not a raw-frame pass. No provider/app test is
meaningful until this works.

## Runtime milestones remaining

Fix concrete CCI/sensor/CAMSS failures from the coordinator's result, then
validate rear app preview and JPEG with the provider prepared below. Front
probe preparation also continued in parallel by explicit coordinator request.
No A6L VFE4.8 hardware ISP backend is used on SM6115 TFE530. Autofocus, flash
and calibrated image quality follow the preview/photo POC.

## Full integrated composition

Use `scripts/mainline/build-integrated.sh` to combine this module extension
with the suspend kernel and battery/vibration DT changes. The camera package
step accepts `--boot PATH --stage-only` for cumulative DT/overlay assembly.
R4 reserves camera reset TLMM18 and rail-enable TLMM93; the builder now releases
the selected camera pins, including CCI and MCLK: the MSM pinmux request
also checks gpiochip validity. All unrelated reservations remain intact. Camera media config is kept separately from the
actual built-in kernel config. The original module/frame limitations still apply.

## Front-only kernel probe (host continuation)

The coordinator authorized preparation before the rear device result. The
Hi556 driver uses the stock 24 MHz init/mode tables generated locally by
`extract-stock.py`; its embedded sensor microcode remains outside git. It
exposes 2592×1944 GRBG10, exposure, analogue gain and frame length. GPIO24
asserts reset low; GPIO93 supplies I/O and GPIO106 switches analogue/core
rails together. Power-up follows the stock reset/rail/clock delays. Shared
GPIO93 uses the regulator framework's reference counting.

Run `python3 -I scripts/mainline/cameras/build.py front` after the rear module
build. This adds only the Hi556 module to the existing media object tree and
creates `front-probe/boot.img` and `front-probe/overlay`. It does not rebuild
the kernel or overwrite the earlier rear candidate. Front DT compile/apply
and module compilation passed. Front MIPI link frequency 416 MHz is derived
from the stock OP pixel rate and still needs hardware verification.

The front DT has **only** the Hi556 graph on CSIPHY2/CSID2/VFE2. Keeping it
separate means a failed front sensor cannot stall the rear async notifier.
The updated module loader chooses the sensor from the active DT. A vendor
containing both modules works with either boot image. Inside the coordinator's
owned root shell (not executed by this worker):

```sh
/vendor/bin/sh /vendor/bin/a9-camera-load
dmesg | grep -E 'hi556|cci|camss|csiphy|csid|vfe' | tail -n 100
/vendor/bin/a9-camcap -l
/vendor/bin/a9-camcap -s hi556 -p 2 -c 2 -W 2592 -H 1944 -n 4 -o /data/local/tmp/a9-front.raw
```

Require ID `0x0556`, then an actual nonempty captured frame. Bayer order,
rotation, timing and image quality remain unverified. There is no autofocus
or flash support in this probe.

## Android HAL and CPU SoftISP (host-built)

The Android bundle builds libcamera v0.7.2's simple pipeline, CPU SoftISP and
camera3 HAL for bionic/arm64 API30. Static libyaml/libyuv/libjpeg/libexif supply
configuration, conversion, JPEG and EXIF. The S5K3L6 gain helper matches the
kernel's gain code/32; Hi556 uses 1+code/16. Black level 64/1024, sensor pitch,
two-frame control delays, Bayer order and rotation are provisional. Tuning
uses uncalibrated AWB/AGC, without a measured color correction matrix.

The stock **standard HIDL2.4 legacy provider shim** loads `camera.default.so`
(the libcamera HAL). The stock CamX engine and `camera.qcom.so` are absent.
Provider dependencies live under `/vendor/lib64/a9-camera` and are selected
only through the camera service's `LD_LIBRARY_PATH`. Shared platform library
files are not replaced. The private closure combines VNDK30 camera interfaces
with current GSI binder/utils/C++ libraries for the LLNDK boundary. The host
ELF check resolved all strong symbols in 57 payload ELFs; actual service,
mapper/minigbm and app compatibility remain untested.

IPA uses libcamera's normal isolated `soft_ipa_proxy`, installed executable
under `/vendor/libexec/libcamera`. The build has no cross crypto dependency.
A camera-enabled vendor now starts the provider at `post-fs-data`, after the
module loader completes. `vendor.a9ml.camera=0/1` still stops/restarts it for
manual raw capture. Device permissions are
appended to existing ueventd rules. R4's permissive camera policy is the POC
baseline; enforcing policy has not been qualified.

Host commands, after the kernel steps above:

```sh
python3 -I scripts/mainline/cameras/android/fetch.py
nix-shell -p meson ninja cmake pkg-config python3Packages.pyyaml python3Packages.jinja2 python3Packages.ply --run 'bash scripts/mainline/cameras/android/build.sh'
python3 -I scripts/mainline/cameras/android/package.py
```

Sources, dependency pins, original A6L patch hashes and the consolidated patch
are in `scripts/mainline/cameras/android/`. Extracted stock/VNDK libraries,
sensor firmware tables and built binaries remain local. The bundle is about
14 MiB and the build is incremental; no full kernel or Android tree is built.

`stage.py --boot INPUT --out OUTPUT --overlay VENDOR_OVERLAY` applies camera
DT changes to the selected input boot, retaining its kernel/ramdisk/second/
recovery-DTBO and existing board changes. It adds the media modules and Android
bundle to the overlay. Add `--front-probe` for the separate front-only graph.
This is also the camera step used by the integrated build after integration.

Inside the coordinator's existing owned root shell, after booting that paired
boot/vendor (commands supplied, never executed by this worker):

```sh
setprop vendor.a9ml.camera 1
getprop init.svc.vendor.camera-provider-a9
dumpsys media.camera
# Open the existing camera app, confirm live rear preview and save a JPEG.
# Capture full provider/libcamera errors and kernel CCI/CAMSS chain on failure.
setprop vendor.a9ml.camera 0
```

A running provider or listed camera is not preview/JPEG success. B9 remains
unproven until actual frames and a saved photo work on the phone.

## Integrated probe failure and correction

The coordinator's `d534d9da` integrated boot produced no media/capture nodes.
CCI failed pin22 with `-EINVAL`; CAMSS failed CSID2 claiming `0x5c75000` with
`-EBUSY`. Host source/DT inspection found both causes:

- SM6115 correctly maps GPIO22/23 to `cci_i2c`, but R4 reserves those pins.
  `msm_pinmux_request()` rejects an invalid gpiochip line even for alternate
  functions. Rear packaging now releases only 18/20/22/23/93 (reset, MCLK0,
  CCI, rail bank); front releases 22/23/24/27/93/106.
- The CSID2 resource table mistakenly named `csid1` for memory and IRQ, so
  it claimed CSID1 twice. It now names `csid2`, matching DT address
  `0x5c7c000` and IRQ77. R4 has no other node owning `0x5c75000`.

The corrected CAMSS module was built incrementally in the worker's existing
media object tree. All 127 versioned imports match the tested integrated
media/kernel exports. The repair reuses every other common CAMSS/Venus/V4L2
module from that integrated image; it does not substitute a separate media
configuration or rebuild the kernel.

`build-integrated.sh --base-build DIR --camera-build DIR ...` supports this
incremental packaging. The base contributes boot/vendor/snapshot; `--without`
skips rebuilding those features while retaining the base's payload. The
camera step adds the Android bundle from `--camera-android-build` (default
B9 Android output). An existing camera graph is replaced when switching to
`--camera-front-probe`, so the notifier never waits for both sensors.

These corrections address the observed failure paths. Sensor ID, raw capture,
Android preview/JPEG and front operation still require the coordinator's test.

The corrected full integrated candidate is under
`~/A9-build/mainline/cameras-20261020/probe-fix/{boot.img,vendor.img,snapshot}`.
The front-only alternative is `probe-fix-front/{boot.img,snapshot}` with the
same `probe-fix/vendor.img`. Exact hashes and current evidence are in
[`runs/b9-cameras-20261020.md`](../runs/b9-cameras-20261020.md). Earlier standalone
rear/front candidates are superseded for the next integrated test.

## Integrated d: stream timeout and zero Android cameras

Image d (`44169941` boot, `eb280db2` vendor) successfully probed S5K3L6 and
configured the 2104×1560 RAW10 pipeline, but dequeued no frame. The stock lane
assignment is `0x3210`. Our generic CSI endpoint numbering `<1 2 3 4>` was
wrong for this CAMSS backend: `csid_get_lane_assign()` uses DT positions
without subtracting one, producing `0x4321`. `csiphy_lanes_enable()` selects
physical lane bits at `2 * position` in an eight-bit value, so the old mapping
wrote `0xd4`, omitting data lane0 and truncating the nonexistent lane4 bit.

Both ends of the rear graph now use data lanes `<0 1 2 3>` and dedicated
clock lane `<7>`, producing stock `0x3210` and PHY enable `0xd5`. The front
uses `<0 1>`/clock7 for the same reason. The sensor's stock stream-on table,
link frequency, power sequence and kernel modules are unchanged.

The d test started the raw module loader but never set the property required
to start the disabled libcamera provider. Counting framework camera services
therefore did not establish provider startup. The camera init file now runs
the module loader and starts `vendor.camera-provider-a9` at `post-fs-data`.
The stop/start property remains available for coordinator diagnosis.

The next rear candidate is `~/A9-build/mainline/cameras-20261020/d-fix/` with
boot/vendor/snapshot. It preserves d's kernel, ramdisk, second stage,
recovery-DTBO and all17 camera/media modules. The only changed vendor payload
is `etc/init/init.a9-camera.rc`. No kernel or HAL rebuild was needed.

See [the d-fix report](../runs/b9-cameras-d-fixes-20261020.md) for exact hashes,
build command and coordinator steps. Actual frame delivery and provider
registration need the next phone test; this worker remains host-only.

## Integrated-e provider registration fix

The coordinator's e capture saved four rear frames. The camera nodes are owned
by cameraserver:camera with mode0660, matching the provider's credentials.
The provider stays running but repeatedly waits for `hwservicemanager.ready`;
Android set `hwservicemanager.disabled=true` and stopped the manager. Thus
these logs do not reach libcamera enumeration or establish a pipeline failure.

The inherited VNDK-less device manifest declares target-level202504. The actual
GSI framework manifest declares `android.hidl.manager` and ashmem support with
`max-level="8"`, filtering them out for that target. AOSP's
[hwservicemanager startup](https://android.googlesource.com/platform/system/hwservicemanager/+/refs/heads/main/service.cpp)
disables the manager when its own HIDL transport is absent. Camera staging now
caps the device target at8, preserving all HAL entries and older targets.
It keeps the VNDK-less linker setup, provider libraries, init order, sensor
drivers and working rear DT unchanged. This is a POC compatibility declaration,
not a full FCM/VTS qualification. The next boot must prove manager readiness,
provider registration and then libcamera camera listing. The provider also
selects libcamera's syslog target so diagnostics reach Android logd instead
of init's discarded stderr.

The absence of Hi556 from e is expected: the default DT contains only rear@10
and CSIPHY0. The existing `--camera-front-probe` alternative replaces that graph
with front@20/CSIPHY2. Both modules can be packaged, but loading a module alone
does not instantiate a sensor missing from DT. Front probe/capture remains
unproven; enabling both together is deferred until the separate front probe
passes, because a missing sensor can stall the shared CAMSS async notifier.

## Integrated-g rear facing fix

The g static Camera2 metadata identifies device0 as FRONT with orientation90,
so the rear-only probe correctly returns `no_rear_camera` before opening it.
Our rear DT used orientation0, but V4L2 defines FRONT=0 and BACK=1. Libcamera
reads that control and gives it priority over camera_hal.yaml. The rear node
now uses orientation1, making the existing HAL map it to LENS_FACING_BACK.

Stock board DT cam-sensor0 (CSIPHY0/MCLK0/reset18) has
sensor-position-roll=270. Libcamera converts Linux's anticlockwise rotation
property to Android's clockwise orientation using `(360 - rotation) % 360`.
The rear DT now uses rotation90, yielding Android orientation270 and matching
the existing HAL YAML. Visual orientation still needs the captured scene.
No sensor registers, power sequence, lanes, clocks, kernel module or HAL binary
change. The existing Camera2 APK remains usable; require BACK/orientation270
in dumpsys before repeating its JPEG capture. The front graph stays deferred.

## Integrated-h buffer import and close fix

The clean h retry opens the rear camera and reaches libcamera's post-processing
callback, then rejects the destination minigbm plane. Before that, the stock
HandleImporter cannot find any graphics mapper. The installed minigbm binary
has both stable-C v5 and HIDL v4 factories, but only v5 was declared and its
filename did not match HIDL's `*-impl*.so` search. The camera overlay now adds
the v4 passthrough declaration and an alias of the same minigbm binary.

The old importer can leave a borrowed handle cached after import failure; its
close path unconditionally dereferences mapper-v2 when all mapper pointers are
null. That matches h's null-pointer crash; the trace does not prove a double
free. A camera-service-only import/free adapter delegates to the original
mapper, tracks successful imports and rejects releases of unowned/already freed
handles. A focused host replay covers failed imports, borrowed FD lifetime,
successful/duplicate close, null handles and cross-importer ownership.

Libcamera also returned metadata with partial_result0 when a buffer failed,
which Android rejected as malformed. The new patch sets partial_result1 whenever
metadata is present. Detailed plane validation errors remain available if the
import fix exposes another issue. No sensor/DT/kernel or APK change is needed.
Actual Android JPEG capture and provider close still need the coordinator test.

## Round7: integrated front and rear graph

Default build-integrated/stage now adds Hi556 alongside S5K3L6. Hi556 uses stock
CCI0 master0 address0x20, MCLK2/TLMM27 at24MHz, reset24, shared VIO enable93 and
analogue/core enable106, PHY2 data lanes0/1 and clock lane7. Stock tables are
regenerated locally with extract-stock.py; firmware/register payloads stay local.
The existing driver and SoftISP helper/tuning are reused. Hi556 orientation0
exports LENS_FACING_FRONT; Linux rotation90 gives Android270, matching stock roll.
Rear orientation1, rotation90, PHY0 lanes0/1/2/3 and all register tables stay the
same. Both sensor modules load before CAMSS. Both sensors must probe for the
shared CAMSS async notifier to complete; use an isolated graph to diagnose a
missing front rather than disturbing the rear sensor configuration.

The raw tool now pairs a direct CSID N with VFE N: TFE340 completion and register
update IRQs use matching hardware IDs. The old front instructions selected CSID2
but the tool hardcoded VFE0. For Hi556 use PHY2/CSID2/VFE2; rear stays0/0/0.
Stock DT specifies PHY2; the raw diagnostic's CSID2/VFE2 pair is the matching TFE
route, while libcamera may select another supported PHY-to-CSID route.

Coordinator only, inside the existing owned test boot, with the provider stopped
before raw capture (commands below were never run by this worker):

```sh
setprop vendor.a9ml.camera 0
/vendor/bin/a9-camera-load
/vendor/bin/a9-camcap -l
/vendor/bin/a9-camcap -s hi556 -p 2 -c 2 -W 2592 -H 1944 -n 4 -o /data/local/tmp/a9-front.raw
setprop vendor.a9ml.camera 1
am force-stop org.a9.cameraprobe
am start -W -n org.a9.cameraprobe/.CaptureActivity --es facing front --ei width 1280 --ei height 960
```

Install the rebuilt camera2-probe APK first, using its README. Require chip0556,
nonempty raw frame and a fresh Camera2 PASS, lens_facing0, completed capture and
an actual viewable front JPEG. Then repeat the activity with facing back and
require lens_facing1 and a correct rear JPEG. Keep raw capture and provider use
sequential; do not reload modules under an active provider. Sensor timing,
Bayer/color/orientation and combined graph registration need hardware validation.

## Rear 3A and colour after the lifted-room test

The lifted build-j JPEG contains a real scene but has mean luma10.65 and a
1.76 max/min RGB mean ratio over the whole frame. Part of that frame is occluded;
these are baseline measurements, not a neutral-target colour calibration. The
covered-lens black capture was expected and is not a sensor-exposure failure.

The verified A6L0016/0017/0019/0020/0021 common port now supplies bounded relative
AGC, real cached AE state, statistics ownership and terminal request drain. A9's
JPEG bounds/footer remain intact, optional0018 raw resampling is omitted, and
A9 does not enable Af. The A9-specific feedback fix snapshots sensor controls at
capture dequeue: event-driven pipelines use the sensor sequence in their delayed
ring; without frame-start events it reads the driver cache instead of consulting
an unadvanced ring. Cached controls do not prove exact frame-effective exposure.
The first240 frames can log requested/cached values using the port's diagnostics.

Sensor limits remain frame-length minus8 lines and analogue gain1..16 (code32..512).
Minimum exposure now matches stock minLineCount6. The stock sensor-module offsets
and colour-table provenance are recorded in the per-run handoff. Stock mode/init
register tables, clocks, power and lane settings are unchanged.

Package.py pins and decodes the A9 rear non-flash stock CC13 tuning into a local
profile with five CCMs and approximate CCT midpoint anchors. It retains fixed
black64/1024 (helper plus dark RAW), gray-world AWB and gamma2.2. This transfers
stock matrices; it does not reproduce CamX's entire colour/tone pipeline or claim
a chart calibration. Generated tuning/blobs stay in the build directory.

The updated Camera2 probe runs serial preview-intent requests on the same JPEG
output, draining each warm-up image. It waits for >=16frames, >=4seconds and eight
consecutive converged AE results before saving one still. After60s without
convergence it now drains the preview and captures a diagnostic still with
`ae_converged:false`; a separate15s deadline bounds drain/capture. JPEG completion
and AE convergence are separate results. This works within the HAL's single processed
stream support. See the probe README and run report for integration/test commands.

For a stationary, unobstructed, evenly lit neutral indoor card/wall filling the
central ROI, host `analyse-capture.py` requires mean JPEG luma60..180, maximum/
minimum RGB channel mean ratio<=1.25 and <5% black/clipped pixels. A coloured room
or a partly covered lens is not a valid neutral-card test. RAW10 statistics are
stride-aware and exclude line padding; RAW channel means need not be neutral.
Actual quality, convergence and capture after reopen require the coordinator's
phone test; host model convergence is not a hardware result.

The camera FPS POC adds a real nominal15–30fps AE range for A9. The relative
AGC uses the per-request maximum frame duration to bound exposure; sensor
VBLANK grows before exposure increases and shrinks after exposure decreases.
The ceiling is about66.7ms with the existing eight-line sensor margin and16x
analogue gain. Requests for fixed30 retain the short exposure ceiling. No
sensor clocks, stock mode/init tables, power sequencing or kernel modules change.
Frame-duration metadata describes the current cached timing; without frame-sync
events it does not establish exact hardware-frame association. The matching
probe requests15–30 explicitly, supports a fixed30 comparison, keeps its window
awake and retains a still if AE times out. Host simulation does not qualify the
long-exposure picture; see the per-run FPS handoff for artifacts and commands.
