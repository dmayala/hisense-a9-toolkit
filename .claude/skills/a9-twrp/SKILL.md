---
name: a9-twrp
description: Build, flash and debug custom recovery (TWRP) on the Hisense A9 (HLTE556N) — the device tree, the two build bugs that must be fixed, the safe test harness, and how to repack the stock recovery with adb. Use for any custom-recovery work on this device.
---

# Custom recovery on the Hisense A9

**Status: FULLY WORKING as of 2026-08-09.** TWRP 3.7.1_12-0 boots, the GUI
renders crisp at the full **824x1648**, **touch works**, root adb shell, and
the `twrp` CLI drives backup/restore/install headlessly.

**The display fix:** this panel is *not* driven by DRM. Stock recovery's own
atomic commits fail exactly as TWRP's did; stock renders through a Hisense
**software TCON** that streams E Ink waveform frames. That whole implementation
is *exported* from stock's `librecovery_ui.so`, so minuitwrp gained a
`graphics_hmct_epd` backend which ships it as a prebuilt (`libhmct_epd.so`),
`dlopen`s it, and calls
`gr_init -> InitEpd -> EpdUnblank -> drawImage(824x1648 RGBA)`.
Chosen by **runtime probe**, so no build flag and no `vendor/twrp` dependency.

Confirm which backend ran — this line decides everything:
```
Using hmct epd graphics.   <- correct; panel renders
Using drm graphics.        <- prebuilt missing at /system/lib64/libhmct_epd.so
                              -> stripes over part of the panel
```

Four bugs had to be fixed to get here (three build bugs below, plus the
display). Rotation, ADF and the entire EPD sysfs layer were tested and excluded
along the way — see *The panel is not the problem*.

**`/data` decrypts and mounts** (re-verified 2026-08-11: 223 G, `df` shows
`/dev/block/dm-8` on `/sdcard`, `twrp.all.users.decrypted=true`). An earlier
version of this skill said it never mounts — that was true only before the
`ae36df8` ramdisk work; see *"/data decryption — SOLVED"* below.

**The prebuilt ABI is frozen.** The A9 is end-of-life; Hisense ships no further
firmware, so `librecovery_ui.so` will never change. The symbol addresses, the
`GRSurface` layout and the call sequence recorded below are permanent for this
device — the usual "a vendor update could break this shim" caveat does not
apply. The prebuilt lives in the device tree; if it is ever lost, re-extract it
from stock `recovery_b` (`system/lib64/librecovery_ui.so` in the ramdisk).

Device tree: https://github.com/dmayala/android_device_hisense_HLTE556N (`android-12.1`)
Builder: https://github.com/dmayala/Action-TWRP-Builder (fork of azwhikaru)

**Flashing (2026-10-14): use `scripts/flash-recovery.sh <img> [--write] [--rollback <img>]`.** It
checks header v2 / page 4096 / `recovery_dtbo` present / size / `.sha256` (and `scripts/abl-check.sh`
if present), refuses unless `is-userspace` = `no`, writes `recovery_a` and `recovery_b`, boots the new
recovery and requires an AUTHORIZED adb recovery, hashes both slots from the TWRP shell, clears the BCB
and returns to Android. On failure it re-flashes the rollback image (default: build 31434216263); if the
phone is unreachable it asks the human for Power + Volume Down and continues.

## FIRST: a build flag only works if Soong exports it

**Check this before trusting any build that "tests" a flag.** `TW_*` and
`RECOVERY_*` flags reach the compiler only through two allowlists in
`vendor/twrp/config/BoardConfigSoong.mk`:

- `EXPORT_TO_SOONG` — read by `getMakeVars(ctx, "NAME")`
- `SOONG_CONFIG_twrpGlobalVars` — the lowercase `tw_*` / `recovery_*` names

A flag absent from both is **silently discarded**: no warning, no error, and
the build succeeds having compiled nothing.

```bash
git clone --depth 1 -b android-12.1 \
  https://github.com/TeamWin/android_vendor_twrp /tmp/vt
grep -n "YOUR_FLAG" /tmp/vt/config/BoardConfigSoong.mk   # no hit -> no effect
```

This burned a full 50-minute build on 2026-08-09: `TW_DRM_LEGACY_MODESET` was
added to a forked `bootable/recovery`, the fork was confirmed present in the
build log, and the flag still did nothing because no `vendor/twrp` entry
existed. Proof it was inactive is in the recovery log — `drm_init()` has
exactly one atomic commit, in the `#else` branch:

```
Atomic Commit succeed        <- the #else branch ran; flag was NOT compiled in
Using drm graphics.
```

Adding a new flag therefore also needs a `vendor/twrp` fork. Prefer an
already-exported flag where one exists.

## The three bugs — all silent, all cost hours

### 1. `recovery_dtbo` must be embedded

Stock `recovery_b` carries **88,722 bytes** of recovery DTBO at offset
26202112. Builds without it have `recovery_dtbo: size=0` and **do not boot at
all** — no splash, no adb, straight back to fastboot.

`BOARD_PREBUILT_DTBOIMAGE` alone is **not enough**; it builds `dtbo.img` as a
standalone artifact and never hands it to `mkbootimg`. Required:

```makefile
BOARD_MKBOOTIMG_ARGS += --recovery_dtbo $(BOARD_PREBUILT_DTBOIMAGE)
```

Verify before flashing — this must print `88722`, not `0`:

```bash
dd if=recovery.img bs=1 skip=1632 count=4 2>/dev/null | od -A none -t u4
```

### 2. `TW_INCLUDE_LIBRESETPROP` must be set explicitly

`bootable/recovery/Android.mk` gates linking and installing separately:

```makefile
ifneq ($(TW_INCLUDE_LIBRESETPROP),)      # non-empty -> LINKS the binary
ifeq  ($(TW_INCLUDE_LIBRESETPROP), true) # exactly "true" -> INSTALLS the module
```

`TW_INCLUDE_CRYPTO` enables the first but not the second, producing a recovery
that **boots and answers adb but whose GUI never starts**:

```
CANNOT LINK EXECUTABLE "/system/bin/recovery":
library "libresetprop.so" not found: needed by main executable
```

On an E Ink device this presents as "TWRP boots but the screen is blank" and
will send you into display debugging that cannot possibly work. It is a
dynamic-linker error. Set both:

```makefile
TW_INCLUDE_LIBRESETPROP := true
TW_INCLUDE_RESETPROP := true
```

**Diagnose it by running the binary by hand from a root adb shell**, not by
staring at the screen:

```bash
adb shell /system/bin/recovery      # prints the linker error immediately
adb shell 'ps -A | grep recovery'   # GUI running at all?
```

### 3. `TW_SCREEN_BLANK_ON_BOOT` blanks the frontlight

`twrpdtgen` sets it. TWRP then sets brightness 200 and immediately 0, so a
working GUI looks like a dead screen on E Ink. See the Display section.

## Verify every image before flashing

Compare against the device's own stock `recovery_b`. All three must match:

```bash
for f in new.img stock_recovery_b.img; do
  printf "%-16s " "$f"
  printf "rdtbo="; dd if=$f bs=1 skip=1632 count=4 2>/dev/null | od -An -tu4 | tr -d ' \n'
  printf "  kernel="; dd if=$f bs=1 skip=8 count=4 2>/dev/null | od -An -tu4 | tr -d ' \n'
  printf "  dtb=";  dd if=$f bs=1 skip=1648 count=4 2>/dev/null | od -An -tu4 | tr -d ' \n'
  echo
done
```

Expected: `rdtbo=88722  kernel=15047394  dtb=327956`.

## The safe test harness — set this up first

**Both slots must have a bootable system before testing recovery.** The
2026-08-08 brick happened because a failed recovery boot triggered the A/B
fallback onto a slot with no `system_a`.

Best configuration, proven working:

- **TWRP on BOTH `recovery_a` and `recovery_b`**, slot A left active
- Then `adb reboot recovery` boots TWRP, `adb reboot` returns to Android —
  **no slot switching at all**, and a bad recovery cannot strand you

```bash
fastboot getvar is-userspace   # MUST print "no" -- see the trap below
fastboot flash recovery_a twrp.img
fastboot flash recovery_b twrp.img
# do NOT change the active slot
adb reboot recovery     # -> TWRP
adb reboot              # -> Android
```

### TRAP: fastbootd reports OKAY and writes NOTHING

**Flash recovery only from bootloader fastboot, never from fastbootd.**
Measured 2026-08-11: from userspace fastboot (`is-userspace: yes`), both

```
Sending 'recovery_a' (98304 KB)   OKAY [ 2.191s]
Writing 'recovery_a'              OKAY [ 0.504s]
```

completed "successfully" for `recovery_a` and `recovery_b`, and the device then
booted **stock recovery**. The identical commands from bootloader fastboot
(`is-userspace: no`) worked first time. There is no error, no warning, and no
non-zero exit — the only symptom is that the old recovery is still there.

Check before flashing; it is one command:

```bash
fastboot getvar is-userspace     # no  -> bootloader fastboot, safe to flash
                                 # yes -> fastbootd, reboot to bootloader first
```

`fastboot reboot bootloader` gets you there. If you are stuck in *stock*
recovery its adbd is unauthorized (no shell, so `adb reboot bootloader` is
unavailable) — use the recovery menu's "Reboot to bootloader", or power off and
hold **Volume Down + Power**.

**Never trust `OKAY` as proof of a flash on this device.** Verify by booting
the recovery and checking `adb devices`: TWRP ships `ro.adb.secure=0` and comes
up **authorized** (`<serial> recovery`), while stock recovery comes up
**`unauthorized`**. That one word is the fastest discriminator there is.

**Recovery boot failures do NOT consume the slot retry budget** (verified
repeatedly: `slot-retry-count` stayed at 7). Testing recovery is cheap. What is
expensive is *system* boot attempts on a slot with no system.

Abort path: `scripts/abort-to-slot-a.sh` — **check the slot layout before
running it.** As of 2026-08-11 slot A has no system at all (`system_a`:
`Could not open partition`) and slot B is the live one, so switching to A would
create exactly the empty-slot boot that bricked this device on 2026-08-08.
Confirm with `fastboot getvar partition-size:system_a` first.

## Sticky BCB — use `scripts/twrp.sh`

Rebooting to recovery on an A/B device writes `boot-recovery` into the BCB (the
first bytes of `misc`). A working recovery clears it at startup; **builds before
`42220a1` did not**, so the *next* boot went back into recovery instead of
Android, and the BCB had to be cleared by hand after every session.

**Current builds clear it** — `docs/release-readiness.md` records "returns to
Android cleanly, no sticky BCB", verified from both recovery and fastbootd.
Consistent with 2026-08-11: `misc` read all-zero from inside TWRP. Note that
observation is *weak* evidence on its own — that session entered recovery via
`fastboot reboot recovery` from the bootloader, which need not write a BCB at
all. Keep using the helper; it costs nothing and still catches the old failure.

Wrapped up in a helper:

```bash
cd ~/A9/scripts
./twrp.sh enter     # Android -> TWRP
./twrp.sh exit      # clears the BCB, then reboots to Android
./twrp.sh status    # where the device is + what the BCB says
./twrp.sh bcb       # show and clear the BCB, no reboot
```

`exit` is the one that matters — forgetting it is what leaves the device
booting into recovery.

Manual equivalent, from the TWRP root shell:

```bash
dd if=/dev/zero of=/dev/block/bootdevice/by-name/misc bs=1 count=2048 conv=notrunc
```

**Never `fastboot erase misc`** — that also destroys the Virtual A/B header at
offset 32768 (magic `0x56740AB0`). Zero only the first 2048 bytes; the header
must read `02 b0 0a 74 56 00 01 00` before and after.

Worth fixing properly at some point: find why TWRP is not clearing the BCB
itself. Until then the helper is the workaround.

## Repacking the stock recovery (excellent diagnostic)

A minimally-modified stock recovery is far more likely to boot than TWRP, and
proves whether the recovery partition is signature-checked. **It is not** — a
repacked stock image boots fine on this device.

Tools in `~/A9/tools/` (AOSP `unpack_bootimg.py` / `mkbootimg.py`; the latter
needs `gki/generate_gki_certificate.py` and `gki/__init__.py` beside it).
Working tree in `~/A9/work-recovery/`.

```bash
python unpack_bootimg.py --boot_img stock.img --out unpacked --format=mkbootimg
# ^ prints the exact mkbootimg args -- reuse them verbatim
gzip -dc unpacked/ramdisk | (cd rd && cpio -idm)
# ... edit rd/ ...
(cd rd && find . | sort | cpio -o -H newc --owner=0:0 --quiet) | gzip -9 > unpacked/ramdisk-new
python mkbootimg.py <args from unpack> --ramdisk unpacked/ramdisk-new -o out.img
```

`--owner=0:0` is required — files must be root-owned.

To add unauthenticated adb, edit `prop.default` (`default.prop` is a symlink to
it): `ro.adb.secure=0`, `ro.debuggable=1`,
`persist.sys.usb.config=adb`, plus an `on boot: setprop sys.usb.config adb`
trigger in `init.recovery.qcom.rc` — stock never fires
`on property:sys.usb.config=adb`, so adbd stays `disabled`.

**Caveat:** stock recovery's sepolicy has no `su` domain and denies
`process:dyntransition`, so adbd aborts on shell spawn
(`Could not set SELinux context for subprocess`). `androidboot.selinux=permissive`
is ignored on this `user` build. You get **root `adb pull`/`push` but no shell**
— still enough to image partitions. TWRP has its own sepolicy with a `su`
domain and gives a full root shell.

## Display — the panel works; the RESOLUTION is the open problem

**Settled 2026-08-09: E Ink rendering is NOT the blocker.** TWRP's `minui` DRM
backend drives this panel:

```
setting DRM_FORMAT_XBGR8888 and GGL_PIXEL_FORMAT_RGBA_8888
number of lms in topology 1
Atomic Commit succeed
Using drm graphics.
I:Loading package: splash (/twres/splash.xml)
```

Frontlight control also works, via the path measured from the stock
`init.recovery.qcom.rc`:

```
I:Found brightness file at '/sys/class/backlight/panel0-backlight/brightness'
I:TWFunc::Set_Brightness: Setting brightness control to 200
```

### Remove `TW_SCREEN_BLANK_ON_BOOT`

`twrpdtgen` sets it. On this device TWRP immediately follows its own
`Set_Brightness 200` with `Set_Brightness 0`, killing the frontlight. On an
E Ink panel in a dim room that is indistinguishable from a dead display and
sent this investigation down a blind alley. Force it back with:

```bash
adb shell 'echo 200 > /sys/class/backlight/panel0-backlight/brightness'
```

### The actual open problem: TWRP's DRM backend vs AOSP's

**Both stock recovery and TWRP use DRM.** An earlier conclusion that stock used
ADF was WRONG -- it was inferred from `adf_*` symbols in
`librecovery_ui.so`, but AOSP's minui compiles the ADF backend in
unconditionally and falls back at runtime. This kernel has **no ADF at all**:

```
/dev/adf*        -> No such file or directory
/sys/class/adf   -> No such file or directory
/proc/devices    -> no adf ;  dmesg -> no adf
```

Stock's `librecovery_ui.so` contains the full DRM atomic API
(`drmModeAtomicCommit`, `drmModeAddFB2WithModifiers`, …) with libdrm statically
linked and no `libadf` dependency. **Do not port ADF -- it would do nothing.**

So: same backend, same single mode, different result.

| | Implementation | Result |
|---|---|---|
| Stock recovery | AOSP `minui/graphics_drm.cpp` (android-11) — simple | full screen, crisp |
| TWRP 3.7.1_12-0 | `minuitwrp/graphics_drm.cpp` — heavily Qualcomm-customised (atomic + layer mixers + separate `draw_buf`) | scattered striped bands |

The kernel exposes exactly one mode:

```
/sys/class/drm/card0-DSI-1/modes  ->  448x829x85x40079vid
```

against an 824x1648 panel. TWRP's mode *selection* is correct (DSI connector,
only mode available). The fault is in how its DRM backend drives that mode.

**Observed on the panel:** the previous splash still visible underneath, with
TWRP's output as scattered bands of fine vertical stripes plus grey blocks. So
TWRP writes real pixels, the EPD refreshes the regions that change, but the
buffer→panel mapping is wrong.

### Stock recovery contains NO E Ink code — verified

Searched the entire stock recovery ramdisk. The only `eink|epd|waveform|hmct`
hits are `prop.default` (`ro.hmct.epd.type=2`, an app-level setting) and
SELinux context files. `librecovery_ui_ext.so` — the obvious place for a vendor
hack — is **10 KB with no display, EPD, DRM or framebuffer strings at all**.

Stock recovery is plain AOSP recovery. All E Ink handling lives in the kernel:
the `msm_drm` panel driver, the `tc358767` DSI-to-EPD bridge and the `fp9929a`
power IC. **A correct plain DRM client just works on this panel** — there is
nothing E Ink-specific to write, port, or reverse-engineer.

### ROOT CAUSE: stock does not use DRM to draw. It uses a software TCON.

**Settled 2026-08-09 from stock recovery's own log.** Stock's DRM atomic commits
*fail*, exactly like TWRP's, and it then draws by a completely different route:

```
Atomic Commit failed in DisableNonMainCrtcs
Atomic Commit failed, rc = 0            <- DRM is a dead end in stock too
InitEpd start / InitEpd end
EpdUnblank version:2.0,UseDefaultFlg=0
hmct orientation: 0 framebuffer_y:1648, framebuffer_x:824, offset_y:184
icon load... iconWidth:720 iconHeight:1280 row_bytes:2880 pixel_bytes:4
epd SwTconThread out frame: 162    (then 38, 38 ...)
```

So the real framebuffer is **824x1648** — the panel's true size — and the
720x1280 assets are centred with `offset_x=52, offset_y=184`. E Ink needs many
drive frames per update (162, then 38...), which is why the DSI link runs at
85 Hz.

**The 448x829 DRM surface is a transport for waveform data, not a picture.**
TWRP writes an ordinary RGB image into it, which is meaningless to the panel —
that is the stripes. No DRM flag, pixel format, stride or buffering change can
fix this, because DRM is not the drawing path.

Everything lives in stock's `librecovery_ui.so`, and the key functions are
**exported**, so they can be called from a prebuilt:

```
T Init_Eink_SWTcon    T Release_Eink_SWTcon    T Reset_SWTcon_Database
T ReportEinkSWTconLibVersion
B epd_image_buf  B epd_wf_frame  B epd_is_unblank  B epd_init_state
  /dev/epd_flash        /data/vendor/display/epd_image_%d.raw
```

`epd_commit_bitmap` is only a trigger (accepts `"1"`), not a pixel path.

#### "Stuck at splash" = startup stalled, NOT a rendering bug

Diagnose by PROCESS STATE, not by the screen:

```bash
adb shell 'ps -A | grep " recovery$"'
#   futex_wait_queue_me  -> blocked, startup never finished
#   do_sys_poll          -> healthy event loop
adb shell 'tail -5 /tmp/recovery.log'   # last line = where it stalled
```

On 2026-08-10 TWRP sat on the splash across three builds. The cause was
`fscrypt_mount_metadata_encrypted()` doing a HIDL `getService()` for a keymaster
HAL that never registers in recovery -- **the call never returns**, so TWRP's
startup stalls before the GUI loads:

```
Using additional fstab for decryption /etc/additional.fstab   <- last log line
recovery 459 futex_wait_queue_me
```

It reached that point because `fsflags=ro` made /vendor mountable, TWRP copied
`/vendor/etc/fstab.default` to `/etc/additional.fstab`, and that vendor fstab
carries `keydirectory=`. Fixed with `TW_SKIP_ADDITIONAL_FSTAB := true` plus
removing the crypto flags from `/data`.

**This cost three wrong diagnoses** -- the theme, TCON serialisation, and
crash-looping HALs -- all treated as display faults, two of them reverted for
no reason. If the splash is on screen the panel is working; look at what
startup is blocked on.

#### /data decryption — SOLVED

Status 2026-08-11: **working**, and re-verified on a fresh flash of build
`31434216263` — 223 G mounted, `twrp.all.users.decrypted=true`. Confirm it the
only way that counts:

```bash
adb shell 'df -h /data; getprop twrp.all.users.decrypted'
```

Note `df` reports the mount as **`/sdcard`** backed by `/dev/block/dm-8`, not as
`/data` — `df -h /data` still resolves it, but grepping the mount table for the
literal string `/data` can make a working decrypt look broken.

**Do not unmount `/data`.** fscrypt keys are per-superblock and installed only
during startup decrypt, so a remount silently returns ciphertext. Reboot
recovery instead.

The two fixes that actually did it, after the false trail below:

1. The vendor keymaster HAL parked forever in
   `WaitForPropertyCreation("vendor.sys.listeners.registered")` *before*
   `registerAsService()`. That property comes from `/vendor/bin/qseecomd`,
   which TWRP never started. Wrapped keys were never the blocker.
2. FBE additionally needs an `fscrypt` session keyring, which AOSP init creates
   and TWRP's does not.

Everything up to the HAL was solved earlier and is still required:

| step | how it was fixed |
|---|---|
| /vendor would not mount | `fsflags=ro` -- vendor is ext4 with shared_blocks and cannot mount rw ("Failed to mount '/vendor' (Permission denied)") |
| TWRP took the legacy FDE path | fstab `keydirectory=` -- partition.cpp branches on `Key_Directory.empty()`; without it TWRP hunts a crypto footer that cannot exist on FBE |
| vold got no fs_mgr entry | with /vendor mounted TWRP finds /vendor/etc/fstab.default and writes /etc/additional.fstab |
| keymaster version unknown | `setprop keymaster_ver` in init.recovery.qcom.rc |
| HALs would not start | `libion` (see below); before that init.svc=restarting |

**The false trail (kept so it is not re-run): "the vendor keymaster HAL never
registers with hwservicemanager."** The symptom was real, the diagnosis was
not — the HAL was alive but blocked in `WaitForPropertyCreation` waiting on
`qseecomd`, so it never reached `registerAsService()`. Starting `qseecomd`
fixed it; none of the sepolicy/VINTF work below was needed.

Both services ran (`km=running gk=running`,
`keystore2=running`) but every client lookup fails, forever:

```
init: Control message: Could not find
  'android.hardware.keymaster@4.0::IKeymasterDevice/default'
  for ctl.interface_start from pid: 455 (/system/bin/hwservicemanager)
```

Ruled out by direct test, do not repeat:
- **wrong HIDL version** -- started the 4.0 service too, and set
  `keymaster_ver=4.1`; lookups still fail
- **SELinux** -- `setenforce 0` at runtime changes nothing (though note
  `SELinux: Context u:object_r:vendor_hal_keymaster_qti_exec:s0 is not valid
  (left unmapped)` -- TWRP's policy has no vendor keymaster types)
- **stale hwservicemanager** -- restarting it with /vendor mounted, then the
  HALs, changes nothing

The conclusion drawn at the time — "fixing this means teaching TWRP's sepolicy
the vendor keymaster domain and getting the VINTF manifest visible to
hwservicemanager, a real project" — was **wrong**, and expensive. The lesson
generalises: a HAL that is `running` but unreachable is more often *blocked* in
its own startup than mis-registered. Check what it is waiting on
(`ps -A -o PID,STAT,WCHAN,NAME`, `/proc/<pid>/stack`) before touching sepolicy.

`/external_sd` is still in the fstab and remains a fine target for backups and
zip/image installs when you would rather not touch `/data`.

#### A crash-looping init service breaks E Ink rendering

Bisected 2026-08-10. Two builds rendered a broken/striped UI; the known-good
image (build 31329801630) reflashed cleanly and rendered fine, so the fault came
from the device tree, not the display code -- `minuitwrp/` and `gui/theme/`
were byte-identical between them.

The difference: those builds declared the vendor keymaster + gatekeeper
services in init.recovery.qcom.rc while **libion was missing from the ramdisk**,
so init respawned both HALs continuously:

    km=restarting  gk=restarting

A full E Ink refresh streams ~160 waveform frames from a userspace thread. Two
processes respawning in a tight loop starve it, and frames get dropped
mid-update ("epd send lost image" / "epd acquire a lost image").

Lesson: **never ship an init service whose dependencies are not in the image.**
A failing service is not inert here -- it degrades the display. If a service
cannot be satisfied, do not declare it.

Note also that "epd send lost image" appears in healthy runs too -- it is not by
itself evidence of a problem. Two wrong diagnoses came from reading it that way.

#### SOLVED: full-panel 824x1648 rendering from TWRP, 2026-08-09

**Shipped and confirmed working end-to-end**: the TWRP GUI renders crisp at
824x1648 and **touch works**. Backend: `minuitwrp/graphics_hmct_epd.cpp`
(bootable/recovery fork) + `libhmct_epd.so` prebuilt in the device tree.
Look for `Using hmct epd graphics.` in the log; `Using drm graphics.` means the
prebuilt was not found at /system/lib64/libhmct_epd.so and you will get stripes.


Verified by photograph: four equal grey bands, edge to edge, correct order.
**Do not pass `gr_get_drv_buffer()` to `drawImage`** — that is the 448x829
*output transport* and produces a squashed image in the top ~28% of the panel.
Fabricate your own 824x1648 surface instead:

```c
// 0. release DRM master first, or the commit fails with -13 (EACCES)
//      adb shell setprop ctl.stop recovery
void *L = dlopen("/tmp/librecovery_ui.so", RTLD_NOW | RTLD_GLOBAL);
((int(*)(void))dlsym(L,"_Z7gr_initv"))();          // 824x1648
((void(*)(void))dlsym(L,"_Z7InitEpdv"))();
((void(*)(void))dlsym(L,"_Z10EpdUnblankv"))();

// GRSurface: [vptr][size_t width][size_t height][size_t row_bytes]
// vtable slot 2 (byte offset +0x10) must return the pixel pointer.
static unsigned char *px;                  // 824*1648*4 = 5,431,808 bytes
static unsigned char *ret_px(void *self) { return px; }
static void *vt[8]; vt[2] = (void*)ret_px;
struct { void *vt; size_t w, h, rb; } s = { vt, 824, 1648, 824*4 /*3296*/ };

// fill px as RGBA 824x1648, then:
((void(*)(void*))dlsym(L,"_Z9drawImageP9GRSurface"))(&s);
sleep(...);   // the SW TCON runs in THIS process; exiting kills the update
```

Success log (identical to stock): `epd drawImage`, then
`epd SwTconThread out frame: 162` (full refresh) and `38` (update).

Working probe: `~/A9/work-epd/epdtest.c`, cross-built with
`nix-shell -p pkgsCross.aarch64-android-prebuilt.stdenv.cc`.

#### Earlier milestone: the panel was driven from inside TWRP

Proven with `~/A9/work-epd/epdtest.c` (aarch64 probe, `dlopen`s stock's
`librecovery_ui.so` from a TWRP root shell). **The panel visibly changed.**

```c
// 1. TWRP holds DRM master -- it MUST be released first, or gr_flip/commit
//    fails with "Atomic commit failed ret=-13" (EACCES):
//        adb shell setprop ctl.stop recovery
dlopen("/tmp/librecovery_ui.so", RTLD_NOW | RTLD_GLOBAL);   // works in TWRP
gr_init();                       // _Z7gr_initv -> 0; gr_fb_width/height = 824x1648
InitEpd();                       // _Z7InitEpdv
EpdUnblank();                    // _Z10EpdUnblankv
GRSurface *s = gr_get_drv_buffer();          // _Z17gr_get_drv_bufferv
uint8_t *px = (*(uint8_t*(**)(void*))(*(void***)s + 2))(s);  // vtable slot +0x10
// surface fields at s+8: width=448 height=829 row_bytes=1792  -> 448*829*4
memset(px, ...);                 // fill 1,485,568 bytes ONLY (see below)
drawImage(s);                    // _Z9drawImageP9GRSurface  <- THE draw call
// 2. DO NOT EXIT IMMEDIATELY. The SW TCON runs in a thread of THIS process;
//    exiting kills it before it drives the panel. Hold >= a few seconds.
sleep(15);
```

Success looks like these lines, matching stock's log exactly:
```
epd drawImage / epd send lost image
epd SwTconThread out frame: 38      <- the panel is physically being driven
```

**Traps that each cost a run:**
- `DrawEpd(int)` is NOT the draw call. Its arg is a **buffer index** into a
  pointer array (`arg << 4`), and it needs `gr_init()` first — calling
  `DrawEpd(0)` before that segfaults at `0x0`. It never enqueues an image.
- `gr_get_drv_buffer()` returns the **448x829** DRM transport surface, even
  though `gr_fb_width/height` report 824x1648. Filling 824*1648*4 overruns it
  and segfaults (fault addr - data = 0x16b000 = 448*829*4 page-rounded).
  Read the geometry from the surface fields at `s+8`; never assume.
- Exiting straight after `drawImage` produces the log lines but **no visible
  update** — the TCON thread dies first.

#### The whole EPD API is EXPORTED — no reimplementation needed

`librecovery_ui.so` exports the complete E Ink interface, so a shim can
`dlopen` it and call these directly. All `DT_NEEDED` deps (`libpng libz liblog
libcutils libutils libbase libc++ libc libm libdl`) already ship in TWRP.

| mangled symbol | signature | addr |
|---|---|---|
| `_Z7InitEpdv` | `InitEpd()` | 0x3aa78 |
| `_Z9DeInitEpdv` | `DeInitEpd()` | 0x3abd0 |
| `_Z10EpdUnblankv` | `EpdUnblank()` | 0x3b2d8 |
| `_Z8EpdBlankv` | `EpdBlank()` | 0x3b47c |
| `_Z7DrawEpdi` | **`DrawEpd(int)`** | 0x3a97c |
| `_Z9WakeUpEpdv` | `WakeUpEpd()` | 0x3a918 |
| `_Z12WaitEpd2Idlev` | `WaitEpd2Idle()` | 0x3a840 |
| `_Z12EpdPanelOpenii` | `EpdPanelOpen(int,int)` | 0x3a54c |
| `_Z13EpdPanelClosev` | `EpdPanelClose()` | 0x3a770 |
| `_Z10SetEpdVcomj` | `SetEpdVcom(unsigned)` | 0x39f18 |
| `_Z15ActiveEpdThreadv` | `ActiveEpdThread()` | 0x39814 |
| `_Z14EinkSwTconInitv` | `EinkSwTconInit()` | 0x3a168 |
| `Init_Eink_SWTcon` | `(void*, int, void*)` | 0x3c2f4 |
| `Update_epd_Image` | — | 0x3d3ec |

Data symbols (**8 bytes each = POINTERS, not the buffers**):
`epd_image_buf` 0x6b340, `epd_wf_frame` 0x6b478, `pSWTconhandle` 0x6b368;
`swtcon_idle` (1 byte flag) 0x6b378.

`InitEpd()` does: 7x `pthread_mutex_init`, 5x `pthread_cond_init`,
`EinkSwTconInit()`, then sets two flags=1 and two ints=0x1c (28).

`DrawEpd(int)`'s inner loop swizzles channels
(`lsl #8` / `and #0xff0000` / `bfi #24,#8`) with a NEON fast path — it repacks
source words into the panel format.

**Iterate with a standalone aarch64 test harness, NOT TWRP builds.** Push a
small `dlopen` binary to TWRP's root shell and try call sequences — seconds per
attempt instead of a ~50 minute build. Only fold into a minui backend once a
sequence is proven to paint the panel.

#### SW TCON ABI — recovered from disassembly 2026-08-09

`Init_Eink_SWTcon` keeps three args (`x0->x20`, `w1->w21`, `x2->x23`), so:

```c
void Init_Eink_SWTcon(void *a, int b, void *c);
```

Its `memset` sizes give the exact buffer geometry — every one is 824x1648:

| hex | bytes | meaning |
|---|---|---|
| `0x52e200` | 5,431,808 | **824 x 1648 x 4** — RGBA input framebuffer |
| `0x297100` | 2,715,904 | 824 x 1648 x 2 |
| `0x14b880` | 1,357,952 | **824 x 1648 x 1** — 8-bit greyscale |
| `0x960000` | 9,830,400 | waveform LUT |
| `0x102e8`  | 66,280 | handle/state struct |

Pipeline: **RGBA 824x1648 -> 8bpp grey -> waveform frames -> the 448x829 DRM
buffer.** TWRP already renders RGBA, just at 448x829 straight into the DRM
buffer — the wrong end of the pipe.

`epd_image_buf` is an exported `B` symbol, so a shim can `dlsym` the buffer
address directly and let the TCON thread (`fb_sw_tcon_thread_`) consume it,
rather than reimplementing waveform generation.

Measured for comparison — TWRP's own mappings:
```
1486848  rw-s  /dev/dri/card0     (448x829x4 = 1485568, page-rounded, x2 = double buffered)
```

**Two viable routes:**

1. **Headless TWRP (available now).** TWRP's adb shell and `twrp` CLI work
   fine; only the screen is wrong. Backup/restore/flash can be driven entirely
   over adb without ever looking at the panel.
2. **A `graphics_hmct_epd` minui backend** that ships stock's
   `librecovery_ui.so` in the ramdisk, `dlopen`s it, calls `Init_Eink_SWTcon`,
   and renders at 824x1648. Signatures still need reversing.

### The panel is not the problem — measured against live stock, 2026-08-09

Flash `$A9_DATA/recovery/recovery-adb-permissive.img` (repacked stock + adb) and read the
same nodes stock uses while it renders **full panel, crisp**. Everything
matches TWRP:

| node | stock (works) | TWRP (broken) |
|---|---|---|
| `/sys/class/drm/card0-DSI-1/modes` | `448x829x85x40079vid` | identical |
| `epd_display_mode` | `3` | `3` |
| `epd_connect` | `0` | `0` |
| `epd_info` | `waveform=0` | `waveform=0` |
| `tc358767_send_init_cmd` | `ret=-107` | `ret=-107` |

**Android logs that same `-107` bridge error on every display power-on** while
rendering a perfect 824x1648 image, so it is benign noise. `/dev/epd_flash`
exists on neither. The panel expands a 448x829 surface to the full 824x1648 by
itself, in recovery, with no waveform load and no bridge fix.

**Therefore: never again spend time on `epd_*` sysfs, the waveform, the
TC358767 bridge, `epd_display_mode` values, or `epd_connect`.** For reference
only, from damianmqr/A9PatchingService and denzilferreira/vendor_hisense,
`epd_display_mode` is `0x200 | waveform`: CLEAR 515, BALANCED 513, SMOOTH 518,
FAST 521. It sets refresh quality, not geometry.

### TWRP blanks the panel and hides its own state

Measured with `TW_SCREEN_BLANK_ON_BOOT` (twrpdtgen's default):

```
t=08s..56s   enabled=enabled   brightness=200
t=64s        enabled=disabled  brightness=0
```

Stock holds `enabled`/200 indefinitely. **On E Ink the last frame persists
after the CRTC is disabled**, so a blanked TWRP is visually identical to a
running one — this repeatedly derailed diagnosis. Fixed with
`TW_NO_SCREEN_BLANK` + `TW_NO_SCREEN_TIMEOUT` (both exported to Soong).
To re-enable the display without rebooting: `adb shell killall recovery`
(init restarts it, DRM re-initialises).

### Checked and EXCLUDED — do not re-investigate

| Hypothesis | How excluded |
|---|---|
| E Ink refresh/waveform | Panel refreshes fine; stock renders crisply; TWRP's writes do trigger updates |
| Rotation / transpose | `persist.twrp.rotation` 0/90/270 all wrong; **90 and 270 identical** |
| Wrong DRM mode chosen | Only one mode exists; TWRP picks it correctly |
| `row_bytes` / pitch mismatch | TWRP sets `row_bytes = create_dumb.pitch`, same as AOSP |
| `draw_buf` stride mismatch | `draw_buf` is memcpy'd from the DRM surface, inherits identical stride |
| Layer-mixer division (`width/number_of_lms`) | Log reports `number of lms in topology 1`, so it is a no-op |
| ADF backend missing | Kernel has no ADF; stock does not use it |
| minui properties | Stock uses the same `ro.minui.pixel_format=RGBX_8888` |
| A build flag for a simpler DRM path | None exists; only pixel format and rotation are exposed |

### Rotation IS runtime-tunable — no rebuild needed

`minuitwrp/graphics.cpp`:

```c
property_get("persist.twrp.rotation", gr_rotation_string, default_rotation);
```

`TW_ROTATION` is only the compiled-in *default*, and the `recovery` service is
not `oneshot`, so init restarts it:

```bash
adb shell 'setprop persist.twrp.rotation 90; pkill -f "bin/recovery"'
adb shell 'grep -a "Scaling theme" /tmp/recovery.log | tail -2'   # confirms new geometry
```

Pixel format is **not** runtime-tunable — `libminuitwrp_defaults.go` bakes it in
as `-DRECOVERY_RGBX`, and minuitwrp ignores `ro.minui.pixel_format` (AOSP minui
reads it; minuitwrp does not). Testing formats costs a build each.

### What would actually fix it

Replace or repair minuitwrp's DRM backend using AOSP's as the reference — it is
known to work on this exact panel. Both are available for diffing:

* TWRP: `git clone -b android-12.1 https://github.com/TeamWin/android_bootable_recovery`
  -> `minuitwrp/graphics_drm.cpp`
* AOSP: `curl "https://android.googlesource.com/platform/bootable/recovery/+/refs/heads/android11-release/minui/graphics_drm.cpp?format=TEXT" | base64 -d`
  (412 lines, much simpler)

Prime remaining suspect: the atomic **plane property** setup
(`SRC_W/SRC_H/CRTC_W/CRTC_H`, plane selection, `update_plane_fb`). AOSP uses a
far simpler path.

**Build workflow is unchanged** — the same `Action-TWRP-Builder` fork is used.
Only the manifest changes:

1. Fork `TeamWin/android_bootable_recovery`, patch `minuitwrp/graphics_drm.cpp`
2. Fork `minimal-manifest-twrp/platform_manifest_twrp_aosp`, edit
   `twrp-default.xml` so `bootable/recovery` points at your fork
3. Set `MANIFEST_URL` in the Action to the forked manifest; leave every other
   input the same

### Debugging displays on this device

`/tmp/recovery.log` is the artifact; it only exists once the GUI runs.

**Never run `/system/bin/recovery` by hand while TWRP is already running.** The
second instance cannot become DRM master and produces
`Atomic Commit failed, rc = -13` (`-EACCES`), corrupting both the display and
the log. This happened during this investigation and briefly misled it.
Check first:

```bash
adb shell 'ps -A -o PID,NAME | grep -E "recovery$"'   # expect exactly one
```
