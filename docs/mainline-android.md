# Android on the mainline kernel: vendor side and first boot

**Date:** 2026-10-15. **Status:** built and checked offline; **not run on the phone**. No agent in this work
ran adb, fastboot or edl; `$A9_DATA` was only read. The first boot is `scripts/mainline/android/android-boot.sh`
(check-only by default), run by main.

Labels: **MEASURED** = read from a file, image or binary (command or path given); **INFERRED** = deduced,
not observed. Related: [mainline-kernel-build.md](mainline-kernel-build.md) (kernel, ABL, fail-safe route),
[mainline-plan.md](mainline-plan.md), [mainline-eink-compat.md](mainline-eink-compat.md) (E Ink ABI),
[kernel-re-assessment.md](kernel-re-assessment.md) §(c.1).

## Bottom line

- **Goal of boot 1:** the v7 GSI (`system_b`, unchanged) boots on linux 7.2.10 with a new `vendor_b`; success =
  adb up from mainline Android, `sys.boot_completed=1`, SurfaceFlinger running on a DRM device. Phase 1 uses a
  **virtual KMS display (vkms)** so it does not wait for the E Ink DRM driver; phase 2 swaps in the E Ink card
  (section 7).
- **Vendor = the Android 16 QPR2 Cuttlefish arm64 vendor**, CI build 15581820, unmodified prebuilt APEXes,
  minus everything that talks to a host or writes storage, plus our own A9 init/fstab/props. No AOSP build: the
  image is assembled and packed on this host in seconds. 104 MB (stock `vendor_b` is 1.15 GB).
- **Boot ramdisk = stock Android 11 first-stage init** (the one the GSI boots on today) with our fstab.
- **/data = 3 GiB brd RAM disk**, formatted by fs_mgr at every boot. userdata, metadata, misc, persist, modem,
  frp are named nowhere. RAM is 7.9 GiB (MEASURED, `Total DDR Size: 0x1FBB00000`, stock kmon log).
- **SELinux:** the GSI's init is a **user** build (MEASURED: `ro.system.build.type=user`, no
  `androidboot.selinux` string in `/system/bin/init`), so `androidboot.selinux=permissive` is ignored. Permissive
  comes from a kernel patch (`selinux_force_permissive=1`, section 4). The vendor policy also **compiles** against
  the v7 platform policy (MEASURED, secilc android-16.0.0_r4: PASS, 814,417 bytes).
- **Fail-safe** is the existing one (boot_b + dtbo_b, PON beacon 2, slot-B retry bound) plus an Android dead-man
  and `androidboot.init_fatal_reboot_target=bootloader`. The script restores golden `boot_b`, `dtbo_b`,
  `vendor_b` and slot a by itself.

## Current artifacts (`~/A9-build/mainline/android/out/`, MEASURED 2026-10-15)

| File | Bytes | sha256 |
|---|---|---|
| `a9-android-boot.img` (boot_b) | 13,029,376 | `cadcf3aba0c3e48506d50d2362d8f41178f1ffd10799cc122c269f697218ea62` |
| `vendor_b.img` (raw ext4) | 103,829,504 | `1969bb7ad32c93796a7c28e6e9782fb4b2e169c50ccc98d4e7842cd234544b06` |
| kernel `Image.gz` (`A9_ANDROID=1 build.sh`, `out-android/`) | 11,785,606 | `71f86d4f…06eb2b`; runtime image_size 30,015,488 <= 36,024,320 (build.sh check PASS) |
| golden `vendor_b.img` (restore source, `golden/`) | 1,154,674,688 | `2a7d5d86…` (lpunpack of the golden super.bin, whose SHA256SUMS entry was verified) |

`android-boot.sh --offline`: PASS (abl-check boot + null dtbo, cmdline gates, raw ext4, no protected partition in
the vendor fstab, no bootctl/rmtfs, golden copies, super fit).

## 1. Architecture decision

| Option | Verdict |
|---|---|
| Full AOSP device build (lunch target) | No: ~100 GB checkout, hours of build; NixOS has no FHS; GitHub free runners are too small |
| Keep the stock A11 vendor, override pieces | No: its gralloc (ION), composer (SDE/KGSL), keymaster (QSEE) and most services need the downstream kernel |
| GloDroid prebuilt | No: newest release v0.8.2 (2023, Android 13, VNDK), board images only (MEASURED, release assets) |
| Waydroid vendor | Not as a whole: Android 13 / VNDK 33 / HIDL, Wayland HWC. Kept as a **Mesa 26 donor** (freedreno FD610, turnip, llvmpipe) for later |
| Linaro db845c AOSP | Gone (snapshots.linaro.org/android redirects away) |
| **Cuttlefish arm64 vendor, A16 QPR2** | **Chosen.** Software, in-guest HALs packed as vendor APEXes with their libraries; vendor API 202504 = the GSI's newest level; drm_hwcomposer + minigbm work on any KMS device |

Cuttlefish source (MEASURED, downloaded 2026-10-15): branch `aosp-android-latest-release`, target
`aosp_cf_arm64_only_phone-userdebug`, build **15581820** (`BP4A.251205.006`, SDK 36, the last Android 16 build
before the branch moved to 17), `aosp_cf_arm64_only_phone-target_files-15581820.zip` sha256 `c39a1a06…43cb`.
URL pattern `https://ci.android.com/builds/submitted/<bid>/<target>/latest/raw/<name>`; the v3 build API now
answers 403, the v4 RPC lister is in `~/A9-build/mainline/android/tools/ci_list.py`. Licences: Apache-2.0 (AOSP,
drm_hwcomposer, KeyMint, SwiftShader), BSD-3 (minigbm); CI artifacts are test-keys userdebug builds. Artifacts
stay out of git (`~/A9-build/mainline/android/dl/`, `INVENTORY.tsv` there).

### What the GSI needs from a vendor (MEASURED in the v7 image with debugfs)

- **VINTF:** matrices 5, 6, 7, 8, 202404, 202504; **no HAL is `optional="false"`** at any level. The vendor
  manifest declares `target-level=202504`, `sepolicy 202504`. servicemanager refuses AIDL services the manifest does
  not declare (each APEX ships its own fragment). Kernel 7.2 matches no `<kernel>` block (the newest is 6.12): that
  is the "internal problem with your device" dialog, not a boot failure (INFERRED from source).
- **Needed in practice** (INFERRED from rc files): KeyMint (`keystore2` is `critical window=0`: without it a reboot
  loop), Gatekeeper, allocator + mapper 5, composer3, health.
- **VNDK:** APEXes v28-v34 in `/system/system_ext/apex`; a 202504 vendor uses none. The GSI's `vndk.rc` would
  `setprop ro.vndk.version 36` (vndk-detect falls back to the vendor SDK), so the vendor sets an **empty**
  `ro.vndk.version=` first (ro. is write-once).
- **Sepolicy mappings:** 29.0-34.0, 202404, 202504 (`/system/etc/selinux/mapping/`).
- **Graphics in /system:** ANGLE (`libEGL_angle` etc.), `libvulkan`; no SwiftShader, no Mesa. So
  `ro.hardware.egl=angle` over `ro.hardware.vulkan=pastel` (SwiftShader from the vendor APEX).
- **phh scripts:** `rw-system.sh` resets `ro.debuggable=0`/`ro.secure=1` unless `/metadata/securize_disable`
  exists (vendor rc writes it; `/metadata` is the GSI's own tmpfs, `00-fix-metadata.rc`); branches keyed on
  `ro.hardware=qcom` are avoided with `androidboot.hardware=hlte556n`. The A9 additions write stock sysfs nodes and
  fail harmlessly.
- **adb:** adbd (APEX 361099999) never reads `ro.adb.secure`; keys are always needed, and `/adb_keys` points to a
  file that does not exist. The vendor rc copies main's `~/.android/adbkey.pub` (packed into the image at build
  time, never committed) to `/data/misc/adb/adb_keys`.

## 2. The vendor image (`build-vendor.sh`)

Kept from Cuttlefish (vendor APEXes, unmodified and still signed):

| Role | APEX | Notes |
|---|---|---|
| Composer | `com.android.hardware.graphics.composer.drm_hwcomposer` | HWC3 v4; any atomic KMS device; `vendor.hwc.drm.device=/dev/dri/card0` |
| Allocator + mapper 5 | `com.google.cf.gralloc` (+ `/vendor/lib64/hw/mapper.minigbm.so`) | minigbm generic: **vkms** is in its dumb-driver list; **msm is not** (needs DRV_MSM or Mesa gbm, phase 2) |
| Vulkan (software) | `com.google.cf.vulkan` (`vulkan.pastel.so` = SwiftShader) | `vulkan.ranchu` in the same APEX is never selected |
| KeyMint | `com.android.hardware.keymint.rust_nonsecure` | Rust TA in-process, + SharedSecret, SecureClock |
| Gatekeeper | `com.android.hardware.gatekeeper.nonsecure` | software |
| Health | `com.google.cf.health`, `.health.storage` | reads `/sys/class/power_supply`, fake battery otherwise; the PMI632 driver in `scripts/mainline/pmic/` feeds it later |
| Stubs | usb, vibrator, power, memtrack, thermal, authsecret, authgraph | example HALs |

Removed: **boot control** (writes slot metadata and the VAB merge status into misc), rebootescrow (pmem), every
remote/trusty/vsock/hvc HAL (keymint/gatekeeper `cf_remote`, light, oemlock, identity, confirmationui), bt, rild,
wifi, gnss, nfc, uwb, cameras, the ranchu composer (its client mode only opens virtio-gpu), `libEGL_emulation`,
Cuttlefish init rc/fstab/ueventd/overlays/priv-apps, virtio modules, and props `ro.frp.pst` (PersistentDataBlock
would write the **frp** partition), `ro.virtual_ab.*`, pmem/hvc props.

Added (A9 files, `scripts/mainline/android/vendor/`): `etc/fstab.hlte556n`, `etc/init/hw/init.hlte556n.rc`,
`etc/ueventd.rc`, `bin/a9ml-deadman.sh`, `bin/a9ml-usb.sh`, `bin/a9ml-ctl.sh`, the E Ink compat shim
(`bin/a9-eink-compat` + `etc/init/a9-eink-compat.rc`, overrides the ROM's `a9_eink_server`; fails soft until the
E Ink sysfs exists), `etc/adb_keys`, build.prop additions (`ro.zygote=zygote64`, graphics props, APEX selection,
`ro.product.first_api_level=30`, `ro.board.platform=bengal`).

Packed by `pack-vendor.sh`: ext4 like stock (no journal, 4K, stock feature list), fs_config owners/modes, labels
from the GSI `plat_file_contexts` + Cuttlefish `vendor_file_contexts`; every inode verified; the sepolicy compile
check runs AOSP secilc in the `a9-android-tools` podman image (only tool not in nixpkgs). Fits super easily:
`super-fit.py` (MEASURED from the golden `lpdump`: device free space for `vendor_b` 1,233,596,416 bytes).

## 3. Boot image (`A9_ANDROID=1 kernel/build.sh`, `mkramdisk.sh`)

- **Kernel:** one config path since 2026-10-15 (merged with the kernel team's): `A9_ANDROID=1
  scripts/mainline/kernel/build.sh` = arm64 defconfig + `a9.config` + `a9-android.config` (binder/binderfs, SELinux
  in `LSM`, DM + verity built in, PSI, cgroup v1 cpusets/memcg, BPF + netfilter legacy tables for
  netd/netbpfload, vkms, UFS QCOM + QMP PHY + ICE built in; brd added by this work) + patch 0014 (force-permissive),
  own build/out dirs (`build-7.2.10-android`, `out-android`). With `A9_ANDROID=1` it builds the DTS
  `scripts/mainline/android/sm6115-hisense-hlte556n-android.dts` = the board DTS unchanged plus UFS (stock rails)
  for **read-only** use. The earlier separate `build-kernel.sh` tree is retired (its fragment without a size
  diet was 37.8 MB, over the ABL window).
- **Ramdisk:** stock `HLTE556N/boot.img` ramdisk (A11 first-stage `/init`, sha256-checked), `fstab.default` and
  `fstab.emmc` removed (they name userdata/metadata/misc/persist as formattable), `/fstab.hlte556n` added:
  only `system` and `vendor`, `slotselect,logical,first_stage_mount`, read-only, no avb/check/discard.
- **Cmdline:** our header items come before the ABL's (MEASURED order: DT bootargs, header, ABL items) and Android
  takes the **first** occurrence of a key (MEASURED in AOSP 11 and 16 source, fs_mgr_boot_config / GetKernelCmdline).
  Pinned: `androidboot.slot_suffix=_b` (only system_b/vendor_b can be mapped), `hardware`/`fstab_suffix=hlte556n`,
  `boot_devices=soc@0/4804000.ufshc`, `init_fatal_reboot_target=bootloader`, vendor APEX selection,
  `enforcing=0 selinux_force_permissive=1`, `brd.rd_nr=1 brd.rd_size=3145728`, plus every safety item of the
  diskless image (`qcom_pon.beacon=2`, `*_ignore_unused`, `cpuidle.off=1 maxcpus=1 cpufreq.off=1`,
  `qcom_scm.skip_qtee=1`, `panic=10`).
- `abl-check.sh <img> boot --dtbo recovery_dtbo.img` must PASS (the ABL emulator; `mkramdisk.sh --check`).
- **UFS rails** (MEASURED, stock golden `dtbo_a` decompiled, fragments 34/35 + `__fixups__`): vcc = L24A
  (2.95-2.96 V, pinned 2.952 V), vccq2 = L11A (pinned 1.8 V, INFERRED: stock gives no level), phy vdda-phy = L4A,
  vdda-pll = L12A, ref clock L18A 1.232 V as `vdd-hba-supply`; no `reset-gpios` (TLMM 113 stays reserved).
  DTB node `/soc@0/ufshc@4804000` -> platform path `soc@0/4804000.ufshc` (MEASURED in the built DTB), so the
  ABL's `soc/4804000.ufshc` (the 4.19 name) would create no by-name links; ours comes first and wins.
- The kernel carries the kernel team's PMI632 observe driver, which makes **one PMIC write at probe** (charger float voltage `0x1070 := 0x55`, the stock 4.45 V, only if
  it differs; `pmi632_a9.fv_stock=0` disables it). Their design choice; flagged here.
- Modules (msm, tc358767, panel, a9_eink) are built for phase 2; boot 1 loads none.

## 4. Storage safety (what can write UFS)

| Path | Prevention |
|---|---|
| userdata, metadata, persist, modem*, fsg, frp, devinfo, diag | named in no fstab, rc or prop; `build-vendor.sh` greps the vendor for them; frp prop removed |
| misc (BCB, VAB header) | **no `/misc` fstab entry**: libbootloader_message finds misc only through the fstab, so every BCB write fails (MEASURED in source); no boot HAL |
| super | first stage maps system_b/vendor_b read-only (both `readonly` in the metadata, MEASURED); never `adb remount`, `disable-verity`, `reboot fastboot` from this Android |
| SD card | no voldmanaged entry; sdhc_2 stays disabled |
| ext4 journal replay | system/vendor images have no journal / are clean ro mounts |
| rmtfs / qseecomd / modem | not in the vendor; remoteprocs not enabled |

UFS initialisation itself writes device *attributes/flags* (not data): `fDeviceInit`, `bActiveICCLevel`,
exception-event control, auto-BKOPS, WriteBooster flags if supported (MEASURED in the 7.2 ufshcd source; stock does
the same class of writes, INFERRED). The ICE probe makes TZ availability queries stock may not make.

Open (kernel team): an `sd_mod.a9_force_ro=1`-style `set_disk_ro()` on every UFS LU would make this absolute.

## 5. Test procedure (main runs it; check mode first)

Prerequisites, offline (in this order):
```sh
A9_ANDROID=1 scripts/mainline/kernel/build.sh all  # Android kernel (one config path) -> ~/A9-build/mainline/out-android
scripts/mainline/android/mkramdisk.sh --check       # ramdisk + boot image -> out/a9-android-boot.img, abl-check PASS
scripts/mainline/android/build-vendor.sh            # vendor -> out/vendor_b.img (+ sepolicy check)
scripts/mainline/android/extract-golden-vendor.sh   # restore source: golden vendor_b.img + super lpdump (done once)
scripts/mainline/android/android-boot.sh --offline  # all host checks
```
On the phone (Android slot a, battery >= 60 %). First, once, put `vendor_b` back to golden (state after the
failed 2026-10-15 staging: 103.8 MB, truncated); the check prints the code, `--write` resizes in fastbootd and
writes:
```sh
scripts/mainline/android/lp-write.sh ~/A9-build/mainline/android/golden/vendor_b.img
scripts/mainline/android/lp-write.sh ~/A9-build/mainline/android/golden/vendor_b.img --write --code <code>
```
Then:
```sh
scripts/mainline/android/android-boot.sh                       # CHECK: read-only phone checks, prints the code
scripts/mainline/android/android-boot.sh --write --code <code> # the test, always ends with the restore
scripts/mainline/android/android-boot.sh --restore             # only if a run was interrupted
```
What `--write` does: `lp-write.sh` puts the vendor image into `vendor_b` from Android slot a (section 5.1) ->
readback of `vendor_b` from super through the `lpdump` extents (works for the inactive slot) -> bootloader checks (slot a successful, charger screen off) ->
`flash boot_b`, `flash dtbo_b` (null overlay), `set_active b`, `fastboot reboot` -> watch up to 45 min ->
on adb from a 7.x kernel: hold the dead-man, wait for `sys.boot_completed`, collect (getprop, dmesg, logcat,
SurfaceFlinger, service list, DRM state, screencap) into `runs/<UTC>-mainline-android/android/`, then
`adb reboot bootloader` -> restore: golden `boot_b`/`dtbo_b`, `set_active a`, Android a (it waits for the phone first), golden `vendor_b` through
`lp-write.sh` if it differs, readbacks, `a9-health.sh`. Slot A is never written. `/data` (userdata) is never mounted.

### 5.1 Staging failure 2026-10-15 and the lp-write route

**MEASURED (main, first `--write`, 2026-10-15):** fastbootd entered from Android slot a printed
`Resizing 'vendor_b' OKAY`, `Sending 'vendor_b' (101396 KB) OKAY`, then `fastboot: error: Command failed`. Afterwards
`vendor_b` was 202,792 sectors (the resize was committed) and its content was the **truncated old golden vendor**
(`head -c 103829504` sha256 `a0a54320…`, ext4 magic present): fastbootd wrote nothing. Android slot a maps
`vendor_b` read-only at boot (`dmctl info`: access ro; `dmctl table vendor_b` = `0-202792: linear, 259:3
16910336`); both vendor partitions carry the `readonly` attribute in the metadata. The script's restore then timed
out waiting for bootloader fastboot while the phone was still coming back from fastbootd (race, fixed).

**Why (INFERRED, not settled offline):** a resize only rewrites the super metadata, but a flash must map the
partition writable. The most likely failure is that `vendor_b` was **already mapped** (read-only, as Android a does
at every boot) when fastbootd tried to create its own writable mapping of the same name, so the map/open for write
failed. The empty error text fits a FAIL without a message from TWRP's fastbootd. The earlier proven fastbootd
flashes were of the *current* slot's system (`flash-test-b.sh` sets slot b first), which this route must not do.
Not ruled out: a TWRP-fastbootd-specific write path. Neither matters for the new route.

**Route now (`lp-write.sh`, used by `android-boot.sh` for both the test image and the golden restore):**
fastbootd only for `resize-logical-partition` (metadata, the step that worked; needed only when `vendor_b` is
smaller than the image, i.e. to grow it back to 1,154,674,688 for the golden copy). The data is written from
Android slot a as root: `lpdump -s 0` and `-s 1` must give identical `vendor_b` extents with no overlap with any
other partition and equal to the kernel's own `dmctl table vendor_b`; then a separate RW device-mapper device
`a9_lpw` is created over exactly those extents (`dmctl create a9_lpw linear <lstart> <n> /dev/block/by-name/super
<pstart> ...`), the image (pushed to `/data/local/tmp`, sha256 checked) is `dd`'d with fsync, caches dropped,
read back through `a9_lpw`, the device deleted, and read back again from super through the extents. Never a raw dd
to super. The test image (104 MB) goes into the start of the existing `vendor_b` without a resize; ext4 only uses
its own size.

### Reading the outcome

| Seen | Meaning |
|---|---|
| adb from kernel 7.2.x, `sys.boot_completed=1`, surfaceflinger pid | **success** (phase 1) |
| adb 7.2.x, no boot_completed | userspace runs; logs collected tell where it stops |
| `unauthorized` | adbd up, key not installed (check `/data/misc/adb`) |
| Android gadget (18d1:4e..) without adb, or `02:a9:00:00:00:02` NCM | adbd failed; the a9ml-usb fallback binds ACM + NCM after 120 s (root shell on ttyGS0, a9ml-ctl on 172.16.42.1:2323) |
| bootloader fastboot early | reset after the PON probe (panic, init fatal -> bootloader, kernel watchdog) |
| bootloader fastboot after ~40 min | dead-man: boot_completed never came |
| Android on kernel 4.19 slot a | resets before the PON probe exhausted slot B's 7 retries; the ABL fell back |

## 6. Risks and open points

- **Unverified on the device:** UFS on mainline (rails, `soc@0/4804000.ufshc` name), the UDC name `4e00000.usb`
  (the USB helper corrects it at runtime), brd + formattable /data, unencrypted /data on A16 (INFERRED fine: the
  A6L LOS port runs unencrypted ext4), apexd multi-install selection from `ro.boot.vendor.apex.*`.
- `maxcpus=1` and SwiftShader: the first boot will be slow (minutes). /data is RAM: the setup wizard runs every boot.
- drm_hwcomposer's rc has `onrestart restart surfaceflinger`. Harmless on vkms; on the E Ink card rule 11's
  backlight hazard belongs to the stock panel driver and has to be re-assessed for the mainline driver.
- VINTF kernel-version dialog (cosmetic).

## 7. Phase 2: E Ink

The E Ink DRM driver contract ([mainline-eink-compat.md](mainline-eink-compat.md)) is a normal KMS connector with
824x1648 logical pixels and the TCON below it. Then: load the msm/bridge/panel modules from `/vendor/lib/modules`
(an rc step, gated), point `vendor.hwc.drm.device` at that card, give minigbm a matching backend (rebuild with
`DRV_MSM`, or keep allocating on vkms/dumb and import; to be decided with the driver owners), `ro.sf.lcd_density`
~280, and the compat shim's sysfs labels. Later GPU: Mesa freedreno (Waydroid's Mesa 26 is a donor, ABI risk) +
the a610 zap firmware from the stock vendor.

## 8. First mainline Android boot, 2026-10-15 (run `runs/20261015T060540Z-mainline-android`, not in git)

**MEASURED (main ran it; logs read offline):** `lp-write` staged `vendor_b` (MATCH from super). Kernel 7.2.10-a9+,
slot `_b`; Android USB gadget 18d1:4ee7 and adb up at t+90 s; surfaceflinger, `vendor.hwcomposer-3`
(drm_hwcomposer on vkms), keystore2, vold, zygote running; `sys.boot_completed=0` at 521 s,
`sys.system_server.start_count=27`. Restore worked (golden `vendor_b` MATCH, slot a, Android up).

| Symptom | Cause (MEASURED in logcat / source) | Fix |
|---|---|---|
| system_server dies at start, 27 times | `RuntimeException: Failed to create ashmem: No such file or directory` in `ApplicationSharedMemory.nativeCreate` (`SystemServer.run:1024`). A16 libcutils (`ashmem-dev.cpp`, android16-qpr2-release) uses memfd only if `sys.use_memfd=true` **and** the kernel answers `ASHMEM_GET_SIZE` on a memfd (an ACK-only shim); otherwise it opens `/dev/ashmem`, which mainline has not had since 5.18 | **kernel:** the ACK legacy C driver (android-mainline `ashmem-legacy.c`) as `drivers/misc/ashmem`, `CONFIG_ASHMEM=y`; request patch `scripts/mainline/android/kernel-requests/0001-misc-ashmem-…patch` (compiles against 7.2.10, built in) |
| audioserver SIGSEGV loop | `AudioFlinger::onFirstRef()+684` null deref; servicemanager: no `android.hardware.audio.core.IModule/default` / `audio.effect.IFactory/default` in the VINTF manifest (the vendor had no audio HAL) | vendor ships `com.android.hardware.audio` (AIDL example HAL) |
| `/dev/stune/*` missing, task profile warnings | system task profiles name the v1 schedtune cgroup that mainline lacks | harmless |
| (artifacts for boot 2) | boot `a9-android-boot.img` sha256 `d719592576dcda102043490175e632428ed0185bee927628d1a114a401544520` (kernel = kernel team tree `bd6d5b239` + the ashmem request patch, their `build-7.2.10-android` config + `CONFIG_ASHMEM=y`, built in a scratch clone, staged in `~/A9-build/mainline/android/kernel-ashmem/` with `SOURCE.txt`; runtime image_size 30,081,024; abl-check PASS), vendor `ac2fb893b885322d7e3a2f8fb7acae81eeaa6d97d94fb39790ad4e135333d8fc` | `A9_ANDROID_KERNEL=~/A9-build/mainline/android/kernel-ashmem mkramdisk.sh --check` until the kernel team carries the patch |
| watch ended at 546 s | the script waited at most 420 s for boot_completed after adb (by design, then collect) | now up to 1200 s (`A9M_BC_WAIT`), stops early on a system_server crash loop (start_count >= 15) |

### Boot 2 (run `runs/20261015T065459Z-mainline-android`, ashmem test kernel + vendor `ac2fb893`)

**MEASURED:** adb at t+96 s, surfaceflinger up, past ashmem; `boot_completed=0` after 1339 s, system_server
started 10 times. Two independent killers, both in logcat:

| Symptom | Cause | Fix |
|---|---|---|
| `FATAL EXCEPTION IN SYSTEM PROCESS: android.display`, 19 times | `RuntimeException: WiFi display was requested, but there is no WiFi Direct feature` (`WifiDisplayAdapter.<init>`): the GSI's resources enable Wi-Fi display, and the new vendor dropped the `android.hardware.wifi(.direct)` feature XMLs (the stock vendor declares them) | vendor keeps `android.hardware.wifi.xml` and `android.hardware.wifi.direct.xml` (no Wi-Fi HAL yet: WifiService runs without one) |
| zygote64 SIGABRT, ~8 times (takes system_server with it) | `Caused HeapTaskDaemon failure : SuspendAll timeout`, a JIT worker thread `Runnable` never reached a suspend point within ART's timeout: one core (`maxcpus=1`) at the bootloader's frequency (`cpufreq.off=1`) | vendor `dalvik.vm.extra-opts=-XX:ThreadSuspendTimeout=60000` (option string MEASURED in the GSI's libart.so); kernel request: more CPUs |
| keystore2 Watchdog reports | only `await_boot_completed` overdue (a symptom; KeyMint is not slow) | none |
| `No service published for: dropbox`, blocking GC | symptoms of the restarts and the single core | none |

New vendor `vendor_b.img` sha256 `ddbd8771ea372e71cfabff3359c28b8049795c1a4002ca30598ab847ccd83e82`.

**Request for the kernel agent (through main):** raise `maxcpus=1` (at least 4, ideally all 8 = 4x A73 + 4x A53)
once the secondary-CPU bring-up via PSCI CPU_ON is judged safe, and consider `cpufreq` (qcom-cpufreq-hw) for the big
cluster; `cpuidle.off=1` can stay. The Android cmdline lives in `mkramdisk.sh` (`A9_CMDLINE_EXTRA="maxcpus=8"` works
for a test, since the later `maxcpus=` wins), but the decision is theirs.

### Boot 3 (run `runs/20261015T072227Z-mainline-android`): `sys.boot_completed=1` at t+601 s

**MEASURED (main):** Android 16 v7 fully booted on 7.2.10 (`boot_completed=1`, surfaceflinger pid 931). Then every
collect command answered `adb: device '<serial>' not found` (empty bundle), `adb reboot bootloader` failed the same
way, the gadget 18d1:4ee7 stayed enumerated for a while with no adb transport, then the phone left USB. The serial
was not the problem: adb had answered on that same serial from t+90 s to t+601 s.

**Cause (INFERRED from the code; no logs survived):**
1. `collect()` began with `adb root`. That restarts adbd; its functionfs closes, which unbinds the UDC
   (`sys.usb.ffs.ready` drops to 0).
2. `a9ml-usb.sh`'s fallback ("UDC unbound, adbd not ready, t >= 120 s") then bound the gadget with ACM+NCM only.
   With the gadget bound, init's own symlink/UDC writes for the restarted adbd failed (configfs refuses link changes
   on a bound gadget), so adb never came back. A later unbind (init/UsbDeviceManager) left the phone invisible.
3. Nobody asked for a reboot (the adb command failed). The phone was most likely **alive**, held by the dead-man's
   stay file (touched at t+90 s) until the 14,400 s uptime cap, i.e. around 11:20 UTC, then reboot,bootloader ->
   PON 2 -> fastboot. There is no evidence of a reboot hang. A held Power button (S2 reset, beacon 2) also lands in
   fastboot.

**Fixes:**
- `a9ml-usb.sh`: the fallback acts only while adbd has never been ready and before boot_completed. After that it
  never writes UDC.
- `a9ml-deadman.sh`: the stay file holds only while fresh (touched within 600 s, `a9ml.stayfresh`); uptime cap
  7,200 s. A host that loses the phone gets it back in fastboot within ~10-20 min.
- `android-boot.sh`: no `adb root`; a mini bundle (getprop, dmesg, logcat) first; the stay file refreshed while
  waiting and collecting; logs the adb transport (`adb devices -l`); if `adb reboot` fails, waits up to 30 min for
  the dead-man's fastboot; the restore also waits 30 min for the phone.
- Still open for robustness: a kernel-side log channel across the hard reset (ramoops is lost on PS_HOLD resets,
  mainline-kernel-build.md section 5). The NCM a9ml-ctl channel (172.16.42.1:2323) works while USB is up.

New vendor `vendor_b.img` sha256 `509cafa10869714f03cbccc0f7c7aba764fc5be433f23af81aa98daa7c2a5e67`.

### Boot 4 (run `runs/20261015T114047Z-mainline-android`): **full boot**

**MEASURED (main):** vendor `509cafa1` + sol-smp stage A boot image (the ashmem kernel with `maxcpus=8`): adb
t+98 s, `sys.boot_completed=1` at t+257 s, system_server started once, `a9_eink_server` (the compat shim) running,
screencap = setup wizard "Hi there" on vkms 1024x768. Since then the normal boot image has `maxcpus=8` (no `--boot`
needed) and uses a frozen kernel snapshot (next section).

### Boot-4 bundle review (offline, a subagent; evidence in that run's logcat/dmesg)

| Finding | Fix (applied unless marked) |
|---|---|
| **drm_hwcomposer ran headless** (`- Display on: null-display`; composer strings "DRM/KMS master access required", "Creating null-display for headless mode"): minigbm opens the vkms *card* node (vkms has no render node, `cgd.cc` "Try card nodes... for vkms mostly"), and the first opener of a primary node becomes DRM master (INFERRED) | `init.hlte556n.rc`: `on early-boot` `start vendor.hwcomposer-3`, before `class_start hal` starts the allocator. Phase 2: the a9_eink card has no minigbm user, so no race there |
| Cuttlefish fence workarounds in build.prop (`running_without_sync_framework`, `present_fence_not_reliable`, `vsync_reactor_ignore_present_fences`) | dropped |
| traced crash loop (34x, `vsock://-1:2010`, no VSOCKETS) -> `updatable_crashing` + flags_health_check flood | `persist.traced.enable=0` (kernel `CONFIG_VSOCKETS` would be the alternative) |
| `a9ml-usb.sh` logcat mirror (sh `read` loop) cost ~50 % of a core | off by default, `sed -u` when on |
| Bluetooth HAL-less crash loop (handheld_core_hardware declares it) | `a9ml-unavailable-features.xml`: bluetooth, bluetooth_le unavailable |
| early logs lost (1 MB logd, kernel buffer) | `ro.logd.size=16M`, cmdline `log_buf_len=4M` |
| **compat shim never ran**: vendor `override` of the system `a9_eink_server` is rejected across the Treble boundary; the ROM's server ran (one ENOENT on the stock sysfs, failed soft) | own `a9-eink-compat.rc`: separate service `a9_eink_compat`; on `init.svc.a9_eink_server=running` stop it, start the shim (untested). Better long term: ROM-side launch (patcher, request) |
| vendor_init set `ro.boot.vendor.apex.*` -> `apexd_select_prop` denial (would break enforcing) | removed from build.prop; the cmdline carries them |
| dexopt 89 s, setup wizard every boot | inherent to the RAM `/data` (every boot is a first boot); not changed |
| cgroup v1 moves refused (160x, euid check) | kernel request: the ACK CAP_SYS_NICE exemption in `__cgroup1_procs_write` (not done) |
| SELinux before enforcing (not done): `/dev/dri/card*` -> `gpu_device`; health sysfs genfscon (`.../charger@1000/power_supply`); adb_keys copy and `/metadata/securize_disable` out of vendor_init; `/cache` tmpfs with `context=`; a vendor_default_prop label | listed for later |

Artifacts as of the boot-4 review (superseded, see section 9.1 for the current ones): `a9-android-boot.img` `0bf963659594…`, `vendor_b.img` `1a920a54e017…`; phase 2 `a9-android-eink-boot.img` `d3cae2de4cb3…`,
`vendor_b-eink.img` `9c73de9ccb5e…`. All `--offline` PASS. Boot images: kernel snapshot `38edc98a…` (kernel agent's
`A9_ANDROID=1` build incl. ashmem 0020), `maxcpus=8`, `log_buf_len=4M`.

## 9. Phase 2: Android on the a9_eink card (prepared offline; NOT run)

Preconditions (main): E5c of [mainline-eink-driver.md](mainline-eink-driver.md) §6 passed on the **diskless** image.
Until then only the gates-closed variant may run, and even that loads msm/tc358767/panel on the phone.

**Kernel = one frozen snapshot.** `snapshot-kernel.sh` copies the kernel agent's `A9_ANDROID=1` build
(`build-7.2.10-android`: Image.gz, Android DTB, ashmem patch 0020, a9_eink as a module) plus the null-overlay
DTBO and the module closure of `a9_eink msm tc358767 panel-hisense-hlte556n-eink` (14 modules, one vermagic
`7.2.10-a9+ SMP preempt mod_unload modversions aarch64`, refused if any module is newer than Image.gz) to
`~/A9-build/mainline/android/kernel-snap/` with a flat `modules.dep`/`modules.load` and `SHA256SUMS`. Both boot
images use it; the eink vendor records the kernel's sha256 in `/vendor/lib/modules/kernel-image.sha256`, and
`android-boot.sh --eink --offline` refuses a boot image whose kernel differs.

| Piece | Phase 2 (`--eink`) |
|---|---|
| boot | `out/a9-android-eink-boot.img`: same ramdisk, cmdline + `androidboot.a9ml.eink=1 firmware_class.path=/data/local/tmp/a9fw` |
| vendor | `out/vendor_b-eink.img`: + `/vendor/lib/modules` (snapshot), `ro.sf.lcd_density=280` |
| setup | `init.a9ml-eink.rc` (in every vendor, inert without the cmdline flag): `on early-boot` (before `class_start hal`) `exec_start a9ml_eink_setup` -> `a9ml-eink.sh setup`: insmod in `modules.load` order, find the card **by platform path** `/sys/devices/platform/eink-display/drm/card*` (not card0: vkms is built in and registers first), make every other non-vkms KMS node root-only 0600 (nobody may become DRM master on the msm card: the driver commits there), `setprop vendor.hwc.drm.device /dev/dri/cardN`, `vendor.a9ml.eink.ready=1`. Failure leaves drm_hwcomposer on vkms |
| buffers | minigbm keeps allocating on **vkms** (it has no `a9_eink` backend; its generic build knows vkms); drm_hwcomposer imports those dma-bufs into a9_eink (`DRM_GEM_SHMEM_DRIVER_OPS`: PRIME import + vmap; INFERRED to work, both are shmem GEM) |
| waveform | vendor data: `android-boot.sh --waveform <blob>` pushes the user's own `a9wf-extract` output (checked: `A9WF` magic, outside the repo) to `/data/local/tmp/a9fw/hisense/a9-waveform.bin` after adb is up; the driver retries `request_firmware` at every update (`a9_load_waveform`), so the first update after the push loads it. Never in git, never in an image |
| gates | closed. `--open-gates` (needs `--eink --waveform` and `A9_EINK_E5C_PASSED=yes`) sets `vendor.a9ml.eink.gate=open` after boot_completed -> `a9ml-eink.sh open`: refuses without the blob; `hv_hold_ms=0`, `init_flash=1`, then panel gate, FP9929A gate, `a9_eink.enable`; after `--gate-secs` (120) `gate=closed` -> enable 0 first, then the gates. Status before/open/after and E Ink dmesg go to `runs/.../android/eink/` |

Open for phase 2 (requests through main):
- **compat shim card path:** `eink-compat.c` hardcodes `/sys/class/drm/card0-DSI-1/eink/`; with vkms built in, the
  a9_eink card is never card0. It should resolve `/sys/devices/platform/eink-display/drm/card*/card*-DSI-1/eink/`
  (driver doc §2 deviation 1).
- drm_hwcomposer on a 60 Hz vblank-timer card with SF frames at E Ink rates: SF's present fences and frame pacing
  are untested; the driver coalesces updates.

Commands (main), once E5c passed:
```sh
scripts/mainline/android/snapshot-kernel.sh                 # only after a kernel rebuild; then rebuild both images:
scripts/mainline/android/mkramdisk.sh --check --eink && scripts/mainline/android/build-vendor.sh --eink
scripts/mainline/android/android-boot.sh --eink --waveform ~/A9-build/mainline/firmware/a9-waveform.bin          # check, gates closed
scripts/mainline/android/android-boot.sh --eink --waveform <blob> --write --code <code>
A9_EINK_E5C_PASSED=yes scripts/mainline/android/android-boot.sh --eink --waveform <blob> --template <ct> --open-gates --bins <E5C_BINS>   # check, first glass
```

### 9.1 Phase-2 safety integration (2026-10-15, after the shim review and kernel patches 0021-0026)

- **Compat shim:** `build-vendor.sh` always runs the shim's `make android` (freshness by make) and refuses a binary
  older than its sources; it logs the shim's sha256 and git commit (now `a7166f2`, card-number independent).
  Handoff (`vendor/etc/init/a9-eink-compat.rc`): the shim starts at `on boot`, long before the ROM starts
  `a9_eink_server` at boot_completed, so it already owns the abstract socket when a9service connects and no
  write-only command can reach the stock server. The stock server is then stopped once; the shim is (re)started
  only after the stock service has reached `stopped` (guarded by `vendor.a9ml.shim.handoff`).
- **Kernel caps are the driver defaults, checked not changed:** `fp9929a.max_on_ms` must read 20000 (the binding
  per-session cap; normal Android updates power down ~2.75 s after the queue empties, like stock).
  `a9_eink.hv_max_ms` is set to 15000 (below it, as the diskless sequencer does).
- **Bin admission:** `--open-gates` requires `--bins <lo-hi>`, the `E5C_BINS` range the E5c run admitted. The
  vendor writes `a9_eink.bin_min/bin_max`, reads them back, and resets them to 0-13 on close.
- **Fail closed on an old kernel:** the vendor's `open` refuses unless `fp9929a/{max_on_ms,min_off_ms,pg_timeout_ms}`
  and `a9_eink/{bin_min,bin_max,hv_max_ms}` exist; `android-boot.sh --offline --eink` checks the same strings in the
  snapshot kernel and module (WARN with gates closed, FAIL with `--open-gates`).
- **Faults end the run (patch 0026 and the fp9929a latch):** the vendor's `open` refuses if a fault was latched this
  boot (`vendor.a9ml.eink.fault`), if `eink/status` shows `stall_fault` != 0, or if the kernel log already holds an
  E Ink fault. While the gates are open, `a9ml-eink.sh watch` checks every second for `stall_fault`, any err-or-worse
  message from fp9929a / a9-eink, or the literal fault strings (`max_on_ms N expired`, `emergency off`,
  `fault latched`, `frame-progress fault`, `update refused`, `power-up aborted`). On a hit it closes the gates
  (enable 0 first), latches the reason and sets `gate=faulted`; nothing ever writes `clear_fault`. `android-boot.sh`
  polls the gate every 5 s, stops the session on `faulted`/`refused`, records `eink=FAULT` in the outcome, collects
  and restores.
- **Snapshot refreshed 2026-10-15 with 0021-0026** (cleared by main): `A9_ANDROID=1 build.sh all` CHECK PASS (runtime
  image_size 30,081,024), snapshot kernel `56324397…`; `--offline --eink`: the safety params are present, no
  warning.
- **Before any `--eink` run:** once the sequencer's patch 0026 is committed, rebuild `A9_ANDROID=1
  scripts/mainline/kernel/build.sh` (kernel agent), then `snapshot-kernel.sh`, `mkramdisk.sh --check --eink`,
  `build-vendor.sh --eink`, so the Android E Ink image carries every safety patch. Even with gates closed the eink
  flavour loads msm/tc358767/panel on the phone, so an old snapshot should not be run either.

Current artifacts (`out/`, 2026-10-15 after patches 0001-0030: `A9_ANDROID=1 build.sh all` CHECK PASS, image_size
30,015,488; kernel `Image.gz` `bd12d1e6…` is unchanged because 0028-0030 touch only the a9_eink module, which the
snapshot now carries with the control template and drive counters; frontlight block in the Android DTS; `maxcpus=8`):

| Image | sha256 |
|---|---|
| `a9-android-boot.img` | `79ac4338256c30b1f57ae17915d4bd514c2404780500036a302d15cdf13bf392` |
| `vendor_b.img` | `f3f2efed28023b3c34cee993957ced0fdc88b2ba0ddf2b1d3659ba10450472a0` (no kernel modules) |
| `a9-android-eink-boot.img` | `94c11fb352ef7195e67c7ac2b03ed195ae032765d837f88f659f08beec88e8a0` |
| `vendor_b-eink.img` | `6730a81a30a4973024afb78a78a02abe1233bc9558f8224db5cc7e85386af790` (15 modules incl. a9-frontlight) |

`--offline`, `--offline --eink --waveform <blob> --template <ct>` and the same with `A9_EINK_E5C_PASSED=yes
--open-gates --bins 5-7`: PASS, no safety-patch warning. `snapshot-kernel.sh` now judges module freshness against
`Module.symvers` (+300 s for the .ko links after modpost) and refuses while a make runs on the build dir: a
module-only change legitimately leaves Image.gz older than the modules.

**Frontlight (Android build only, 2026-10-15):** the frontlight LED driver (`scripts/mainline/frontlight/`, `781df25`)
is built as an external module against the Android kernel build (`build-frontlight-module.sh`, same vermagic,
checked by `snapshot-kernel.sh`) and loaded last from `modules.load` in the eink flavour. Its DT fragment is inlined
verbatim at the end of `sm6115-hisense-hlte556n-android.dts` (kernel/build.sh copies only that file; sha256 of the
source dtsi recorded in the block and checked by `snapshot-kernel.sh`): i2c0/i2c1 enabled, the four LED nodes
`a9:{warm,white}:{center,edge}`, `gpio-reserved-ranges` reopening exactly TLMM 4, 5, 83, 85, 88, 101 against the
current board (computed: nothing newly reserved), `sdhc_2` disabled. The diskless E3/E5 image and
`scripts/mainline/kernel/` are unchanged. The compat shim's four default paths are
`/sys/class/leds/a9:{warm,white}:{center,edge}/brightness` (MEASURED in `eink-compat.c`); its largest output is
2200 x 81 % = 1782 (center) and 2047 x 78 % = 1596 (edge), within `max_brightness` 2047. Note: the frontlight is
not behind the E Ink gates; in the eink flavour a9service's brightness reaches the LEDs as soon as the shim runs.

### 9.2 Control template (kernel patches 0028-0030, 2026-10-15)

The glass only changes when bytes 1..3 of every transport word carry the stock control template (MEASURED on the
diskless image; patch 0028 loads `hisense/a9-ctrl-template.bin`, checks its payload sha256 against
`A9E_CT_SHA256` = `a88ae8ef…` and refuses to drive without it). For the Android eink flavour:

- `android-boot.sh --template <a9-ctrl-template.bin>` (made by `scripts/mainline/eink/tools/a9ct-extract.py` from
  the user's own composer; required with `--open-gates`) is pushed next to the waveform to
  `/data/local/tmp/a9fw/hisense/` (same `firmware_class.path`). Offline: `A9CT` magic, version 1, payload size and
  sha256 equal to the kernel constant (read from `scripts/mainline/eink/driver/a9_eink_engine.h`), file outside the
  repo. Neither blob is ever committed or put in an image.
- Vendor `open` pre-checks, in addition to 9.1: both blobs present; the status attribute has the 0026-0030 fields
  (`template`, `stall_fault`, `late_drive`, `max_gap_drive_us`, `commit_fail`); with `enable` still 0 it requests a
  dry-run update (`force_clear`, computed and hashed, nothing committed, no HV) so the driver loads both blobs, then
  requires `waveform 1`, `template 1` and the `control template loaded` kmsg line; `stall_fault`, `commit_fail` and
  `aborts` must be 0. The status line is logged before opening, and the session's kmsg window starts there.
- Faults during the session: the 9.1 rules plus `commit_fail`/`aborts` becoming non-zero and a rejected
  template/waveform; generic err-level E Ink messages only count inside the session window (the driver logs one
  benign `no valid control template (-2)` before the push).
- `--offline --eink` also requires the 0028-0030 strings (`a9-ctrl-template.bin`, `late_drive`, `max_gap_drive_us`)
  in the snapshot's a9_eink module: the current snapshot `bd12d1e6` (0001-0027) WARNs (gates closed) / FAILs
  (`--open-gates`) until it is rebuilt after the template agent's final kernel commit.

### 9.3 `--hold`: leave the phone on the test image (2026-10-15, after E5c passed)

E5c passed on the diskless image (main: image `98bb6ee6`, kernel 0001-0030, control template; HV ~1579 ms, no
late frames; `E5C_BINS` 8-10, E5a bin 9, ~26 C). For the user to look at Android on the glass, `android-boot.sh
--hold [s]` (default 14,400 s, max 21,600) skips the restore after boot_completed and the collection:

- the host touches the dead-man's stay file every 60 s and raises its uptime cap through `vendor.a9ml.maxstay`
  (uptime + hold + 600 s; the vendor clamps it to 6 h). Every 5 min it saves the E Ink status and E Ink/frontlight
  kmsg lines to `runs/.../android/eink/hold/` (or `android/hold/` without gates).
- with `--open-gates` the gates stay open for the whole hold. The session keeps every kernel cap (fp9929a.max_on_ms
  per session, min_off_ms, the frame-progress interlock, bins 8-10, hv_max_ms 15000) and the vendor's 1 s fault
  watch and latch; `hv_hold_ms` stays at the driver default (stock-like power-down ~3 s after the queue empties)
  instead of 0 (`vendor.a9ml.eink.hv_hold=default`).
- the hold ends at the timeout, on Ctrl-C/TERM, or when adb has been gone for 5 min: the gates are closed and the
  script exits printing `android-boot.sh --restore`; with no host the stay file goes stale and the dead-man sends
  the phone to fastboot within ~10-20 min. **A fault ends the hold and restores at once** (gates already closed by
  the vendor, latched for that boot).
- Note: a temperature drift out of bins 8-10 makes the driver refuse updates (`update refused`), which counts as a
  fault and ends the hold. The frontlight is outside the gates.

### 9.4 The ABL passes only 511 bytes of cmdline (MEASURED 2026-10-15, eink `--hold` run)

The `--eink --open-gates --hold` run booted mainline Android (adb t+96 s) with **only vkms**: no E Ink modules,
`vendor.hwc.drm.device=/dev/dri/card0`. dmesg `Kernel command line:` showed our part cut after
`androidboot.usbcontroller=4e00000.usb androidboo`, followed by the ABL's own items: the ABL uses only the 512-byte
header `cmdline` field and drops mkbootimg's `extra_cmdline` spill-over (the earlier 876-char cmdlines were cut the
same way: `androidboot.init_fatal_reboot_target`, `androidboot.selinux` and the `androidboot.vendor.apex.*` items
never reached the kernel in boots 1-4; the APEX selection worked through build.prop). The eink flag and
`firmware_class.path` were lost, so `init.a9ml-eink.rc` stayed inert and the gates were refused (safe).

Fix: the cmdline is now 397 bytes (eink 462), everything in the first field: dropped `console`/`earlycon` (no UART
access), `rdinit=/init` (default), `brd.rd_nr` (`CONFIG_BLK_DEV_RAM_COUNT=1`), `androidboot.fstab_suffix` (falls back
to `hardware`), `androidboot.usbcontroller` (runtime correction + rc default), `androidboot.selinux` (ignored by the
user-build init); `androidboot.vendor.apex.*` are back in the vendor build.prop (an `apexd_select_prop` denial,
fine while permissive). `mkramdisk.sh` refuses more than 511 bytes; `android-boot.sh --offline` now reads the
EFFECTIVE cmdline (the 512-byte field only), fails if `extra_cmdline` is non-empty, and requires beacon, forced
permissive, slot `_b`, hardware, boot_devices, init_fatal_reboot_target, `brd.rd_size`, `maxcpus=8`, and for
`--eink` `androidboot.a9ml.eink=1` + `firmware_class.path`.

Images: `a9-android-boot.img` `d4e0be0056a9b67ae32ca56b20ede737fe63323c9e6979c40db644b0c21e6d55`,
`vendor_b.img` `0704753af8889439004631c26c996e9257c3d0b60c2821d9a295d0db69665bed`,
`a9-android-eink-boot.img` `719f1e01bb8055330cc2ed89440e0d671cbdb560900e479c8ceeba3ab4925a5a`,
`vendor_b-eink.img` `59a28c7bd452647e80a863d31b75c63c03745d0881cf920979caf37c13412ab1`.

### 9.5 Eink run 2: the wrong KMS node was locked; development root shell (2026-10-15)

**MEASURED (main, eink run 2, images 719f1e01/59a28c7b):** the cmdline fix worked (`[drm] Initialized a9_eink 1.0.0
for eink-display on minor 1`, `vendor.hwc.drm.device=/dev/dri/card1`), but boot never completed:
`vendor.graphics.allocator` (minigbm) crash-looped (`Failed to initialize driver`), SurfaceFlinger restarted
forever, 0 E Ink updates. `/dev/dri/card0` (vkms) was `crw------- root`, card1 (a9_eink) `0666`: minigbm could not
open vkms, opened card1 instead, and has no backend for a9_eink.

**Cause:** `a9ml-eink.sh setup` classified cards by `DRIVER=` in `device/uevent`; in 7.2 vkms is a **faux device**
(`drivers/gpu/drm/vkms/vkms_drv.c`: `faux_device_create`) with no driver line, so it was taken for "not vkms" and
made root-only. **Fix:** `card_kind` classifies by the sysfs device path and the bound driver (`/devices/faux/vkms/`
= vkms, `/devices/platform/eink-display/` = a9_eink, driver `msm*` or a display-controller path = msm, anything else
left alone); only the msm card is made root-only. Host test `tests/test-eink-setup.sh` (fake sysfs trees: the
eink-2 layout, msm before/after a9_eink, an unknown card) runs in `android-boot.sh --offline --eink`.

**Composer ordering:** the vkms image now carries `androidboot.a9ml.eink=0` (cmdline 421 bytes) and only there
`init.hlte556n.rc` starts the composer at early-boot; in the eink image `init.a9ml-eink.rc` starts it right after the
setup `exec_start`, so it never opens vkms first.

**Development root shell (test vendor only, user-approved):** primary route is adbd as root. The v7 system
build.prop has `ro.secure=0`, `ro.debuggable=1` (MEASURED); phh's `rw-system.sh` would reset them at post-fs, but
the vendor rc writes `/metadata/securize_disable`, and the GSI plat policy has the `su` domain. adbd (APEX
361099999) decides from `ro.secure` and `service.adb.root` (strings MEASURED in the binary), so `adb shell id`
should be uid 0, or `adb root` works (INFERRED; run 3 MEASURED adbd uid 2000, so the loopback shell is the working route). Fallback: service `a9ml_rootsh`,
`nc -L` root shell on 127.0.0.1:2324 (phhsu_daemon), started at boot_completed (`vendor.a9ml.rootsh=0/1` stops /
starts): `adb shell 'echo CMD | nc 127.0.0.1 2324'` or `adb forward tcp:2324 tcp:2324`. `android-boot.sh` checks both
right after adb comes up (non-fatal warning) and uses the working one for root reads in the collection. Only in
the mainline test vendor (slot B, RAM /data); the daily slot-A ROM uses the stock vendor, unchanged.

Images: `a9-android-boot.img` `8407c16c22c4025609f99520a247e2b3ca1db427ff7218dd9c00270a7218e40b`, `vendor_b.img`
`3600f95100f584591f40c2067fcad56afa9d33191df72e0e6bd968147a05a11b`, `a9-android-eink-boot.img`
`b832f9839537a249ceed398d79bf4ac15a0a34bed23a2b503307510444df79a7`, `vendor_b-eink.img`
`fcbb244adfcbb0af7961018d9e0364c8f14b2c54cae22cfd5d3c9e1f8c656c77`.

### 9.6 Eink run 3: Android up on the a9_eink card; the gate open read kernel bools wrong (2026-10-15)

**MEASURED (main, run 3, images 8407c16c/3600f951/b832f983/fcbb244a):** the card fix worked (both `/dev/dri` nodes
0666, allocator running, SurfaceFlinger up, `vendor.hwc.drm.device=/dev/dri/card1`, `sys.boot_completed=1` at
t+287 s, a9_eink card ready, waveform and template pushed, `[a9_eink]` kthread and a9service running). The frontlight
driver lit the frontlight at boot (LED values 0 and 596). The loopback root shell works; adbd stays uid 2000 (so
`ro.secure=0` alone does not make it root). The gates did not open: `a9ml-eink: open refused: a9_eink.enable
already 1`, while all three gate parameters read `N`: kernel `bool` parameters print `Y`/`N`, and the script
compared with `0`/`1`. That refusal was safe (fail closed).

Fix: `pbool` reads `Y`/`N`/`1`/`0`; writes stay `1`/`0`. `open` requires all three gates to read closed, verifies
each gate (and `init_flash`) reads back `1` after its write, and resolves the `force_clear` path before writing it
(the dry-run update makes the worker load both blobs: `a9_load_waveform` runs at every kicked update until both
load; the pre-push `Direct firmware load ... -2` is expected). `close` reads the three gates back and logs a warning
unless they are all `0`. New host test `tests/test-eink-gates.sh` (fake sysfs with `Y`/`N` params, status, blobs,
getprop/setprop/dmesg stubs): open from N/N/N, refusal with enable `Y`, refusal without a loaded template, close
from Y/Y/Y with read-back; it runs in `android-boot.sh --offline --eink`.

Vendors: `vendor_b.img` `f1c89f24d41efe9554b5c43d5aaf92340a8e75221e2ed5f8c91b55ba6c5a0f0f`, `vendor_b-eink.img`
`58c80d579140738f8898a189dfd9dc5b27f45d6f95d33c4324cf848c7c8f743f`; boot images unchanged (`8407c16c`, `b832f983`).

### 9.7 Eink run 4: gates opened, but msm never bound (no dispcc) (2026-10-15)

**MEASURED (main, run 4):** the Y/N fix worked (`a9ml-eink: gates OPEN`), template and waveform loaded (sha
`a88ae8ef` / composer `8c89557b`), SurfaceFlinger's primary display was 824x1648 on the a9_eink card, frontlight on.
No frame reached the panel: the first forced update logged `update dropped: pipeline not available (-11)`, the
vendor fault watch closed the gates and latched (`aborts 1`), as designed. Cause: `msm-mdss
5e00000.display-subsystem: deferred probe timeout, ignoring dependency` then `probe with driver msm-mdss failed with
error -110`; `lsmod` had msm, tc358767, panel and a9_eink but **no `dispcc-sm6115`**, so no msm DRM card existed
(only vkms card0 and a9_eink card1). The diskless loader (`a9-eink load core|bridge`) loads dispcc first.

Fix: `snapshot-kernel.sh` now builds the module list in load order: `dispcc-sm6115` (=m in the Android config),
msm and its dependencies, panel, tc358767, a9_eink, then a9-frontlight. `a9ml-eink.sh setup` waits up to 30 s,
before insmod of a9_eink, for a card classified msm and logs whether it appeared (a missing msm card is a WARNING;
the gates then refuse because updates are dropped). Loading dispcc before msm also avoids the fw_devlink
deferred-probe timeout path (msm-mdss probing without its clock supplier). `android-boot.sh --offline --eink`
checks that the vendor's `modules.load` has dispcc-sm6115, msm, panel, tc358767, a9_eink in that order; the setup
host test checks the insmod order and the msm wait. Images: `a9-android-boot.img` `a4353de9…`,
`a9-android-eink-boot.img` `3bbf111c…`, `vendor_b.img` `b2d1325c…`, `vendor_b-eink.img` `6f17e19b…` (full hashes in
the `.sha256` files). Open: an SELinux `getattr` denial from `sh` on the eink status (permissive, fine for now).

### 9.8 Iterating without the golden restore (`--no-restore`, `--to-slot-a`; 2026-10-15, user request)

- `--write --no-restore`: before, `boot_b`/`dtbo_b`/`vendor_b` may hold a previous test build; instead of requiring
  golden, the check notes them and the run records their hashes (`runs/.../prior-state.txt`). Slot a must still be
  the booted, successful slot; every other check is unchanged (abl-check, the 512-byte cmdline, gates, bins,
  waveform/template, approval code, which now also covers the flag). After the run: gates closed as before, slot B
  left as written, and the phone is switched to slot a with **no partition writes** (bootloader `set_active a`,
  reboot, health). On a fault the same happens instead of the restore. `--hold` still keeps the phone on b.
- `--to-slot-a`: only that switch, from mainline Android on b, the bootloader or fastbootd.
- `--restore` stays the final golden restore (boot_b, dtbo_b, vendor_b through lp-write with a resize if needed,
  slot a) and works from any of these states.

### 9.9 Eink run 5: first Android frame on the glass (2026-10-15)

**MEASURED (main, run 5: eink boot `3bbf111c`, vendor `6f17e19b`, `--no-restore --hold`):** `[drm] Initialized msm
1.13.0 for 5e01000.display-controller`, `msm DRM card card1-DPI-1 present after 0 s`, a9_eink = card2,
`sys.boot_completed=1` at t+273 s, gates OPEN, `fp9929a on: VCOM 2730 mV, temp 29 C, PG 1 after 36 ms`; a forced
Clear through the root shell ran a **real update** (dry 0, 123 frames, aborts 0) and the user saw the glass turn
black: Android's actual frame (screencap black with the battery icon; the setup wizard not yet drawn).

| Problem | Change |
|---|---|
| timing: late 59, vbl_skips 59, late_drive 11, vbl_skips_drive 12, max_gap_drive 25.5 ms (no fault, extra drive pulses), 8 CPUs at the boot frequency, SwiftShader/SF on the same cores | setup puts the `[a9_eink]` TCON kthread on SCHED_FIFO 50 and msm's `crtc_commit*/crtc_event*` workers on FIFO 49 (`chrt`); optional sol-smp stage B1 image `a9-android-eink-boot-b1.img` (`A9_CPUFREQ=b1 mkramdisk.sh --eink`: `cpufreq.default_governor=schedutil` instead of `cpufreq.off=1`; not boot-tested, B0/B1 gates in scripts/mainline/smp/README.md) |
| ~5 min after boot the screen timeout let the kernel **suspend**; USB never returned | test vendor holds a kernel wakelock from `on boot` (`/sys/power/wake_lock a9ml_test_awake`) and sets `stay_on_while_plugged_in 7` at boot_completed. Gadget survival across suspend/resume: open |
| frontlight came up at maximum | at boot_completed: `screen_brightness_mode 0`, `screen_brightness 40` (a9service maps it to the frontlight properties the shim applies) |
| healthd `battery ... h=5` (OVER_VOLTAGE) at 4450 mV | the stock-like OV qualification (FV + 40 mV) is only a PROPOSAL (`scripts/mainline/pmic/kernel-telemetry-PROPOSAL.patch`, 2b763b9) and is **not** in the kernel tree (no match in `drivers/power/supply/pmi632-a9.c`): **request to the kernel agent** to integrate it as the next free patch (0031) |
| after a long-press reboot: Hisense logo, no USB for minutes, then EDL with firehose disconnects; the user reached fastboot; main set slot a | not reproduced offline; note for the recovery skill |

Images: `vendor_b.img` `dab845493c4f989f36e17a736c07310e82bbcff146ab05b1fa62265b7227e77e`, `vendor_b-eink.img`
`5fadbe4c948b4e886b0b361236d30edecec09ac2d7f4b93f48c5c4592d248007`, eink boot unchanged `3bbf111c…`, B1 variant
`b35a746e0412a101ac594b0ec1067090ba9facddc4e2dcd5914b89c1c2b45035`.

### 9.10 Run 6 diagnosis and run-7 images (2026-10-15)

**MEASURED live in run 6 (root shell, read-only plus a short DRM debug window, one SF repaint and one sleep/wake):**
every drm_hwcomposer commit to the a9_eink card failed `EINVAL` (122x `Failed to commit pset ret=-22`); DRM atomic
debug: `[CRTC:37:crtc-0] active without enabled` / `atomic core check failed` - the composer sets `ACTIVE=1` and the
plane's FB but no `MODE_ID`, while the CRTC holds no mode (`dri/2/state`: crtc `enable=0 active=0`, plane `fb=0`).
The imported framebuffers were fine (AB24 824x1648, pitch 3328, `imported=yes`). So no plane update ever ran
(`updates 2`: dry run + forced Clear), the grey shadow stayed zero and the forced Clear drew it: solid black. Also
`bad framebuffer height 49, should be >= 1648 && <= 1648` (mode_config min = max). INFERRED from drm_hwcomposer
android16-qpr2 `HwcDisplay`: it only sends a mode when a config is staged; after a teardown/re-init (or a failed
first blocking modeset) it keeps committing without one.

**Fix (kernel, by the template agent):** patch 0032, fixed-mode CRTC enabled with the 824x1648 mode from reset,
connector routed, disabling commits kept inactive with mode and link; mode_config 1x1..4096x4096. Patch 0031: PMI632
OV health qualified like stock. **Vendor (this work):** in the eink image DRM atomic debug (`drm.debug=0x10`) is
switched on just before the composer starts and off again after 60 s (`a9ml.drmdebug_s`), so the first commits are in
kmsg.

Run-7 images (kernel snapshot `b237078d…`, patches 0001-0032): `a9-android-eink-boot.img`
`a936671ecdf38337c0e04904709f0325fab1c89bdf9efd47f48c5ff3fa0ec969`, `vendor_b-eink.img`
`c93951f69950f7266b4f44686e82671aed50ed7cd951911ae7e96922a677f53c`, `a9-android-boot.img`
`51536ac9c5a22c3ad058602a52743117b0cf4d171f590c3a02b61fdd2955b2e1`, `vendor_b.img`
`ed10ab38d67de147983d975de2fa387ef65c9632a216dd63e7d6e140863e9b96`.

**Revised 0032 (a8c493a), 2026-10-15:** re-snapshot (Image.gz `b237078d…` unchanged: the revision touches the a9_eink
module only, `a9_eink.ko` `19363dc1…`), images rebuilt: `a9-android-eink-boot.img`
`d48c76ed594decc124787fc4a07f542d44f2bab0a3fe48d8da91bb72433d2f5b`, `vendor_b-eink.img`
`057016010cb13a8f6f773950140216e106a5d0e846977360afc4555ba69e461e`, `a9-android-boot.img`
`484ac031ebae1e7bc8ec0360cfef57586172a51e2902e1eba72a866f884d5c81`, `vendor_b.img` `ed10ab38…` (unchanged).

**`eink-compare.sh [outdir]`** (host; read-only on the phone except two temp files in /data/local/tmp): through the
loopback root shell it mounts debugfs if needed, copies `/sys/kernel/debug/a9_eink/grey` (1648x824, the last
update's grey) and the eink status, takes `screencap -p`, pulls both, and runs `eink-compare.py`, which puts the
screencap through the driver's own grey stage (`a9e_fb_to_grey`: `(76R + 150G + 30B) >> 8`, 90 degrees
counter-clockwise, `grey[(823 - c) * 1648 + r]`) and prints exact-match share, mean |d|, PSNR and the share of
non-black screencap pixels; exit 3 if the frames differ (PSNR < 30 dB) or the driver grey is all zero against a
non-black frame. PNGs (`ref-grey`, `driver-grey`, `diff` x4, `side-by-side`) go into the newest run dir's
`android/compare-<UTC>/`. Tested offline on the run-6 screencap (an independent rotation: 100 %; an all-zero grey:
exit 3). The grey is the last update's input, so the screencap must be taken after the frame settles.

### 9.11 Run 7 and the run-8 preparation (2026-10-15)

**MEASURED (main, run 7):** composer commits work (0 pset failures, CRTC active; `eink-compare` 100 % while the
frame was black), but the first real update (the setup wizard) hit `a9_eink stall_fault 1` mid-waveform; the
fault was latched and the run ended on slot a. Frame pacing is the blocker (runs 5/6: late ~60, max_gap_drive
~25 ms); a pacing patch 0033 is in progress (template agent).

Prepared here:
- **Stage-B1 eink boot image** on the current snapshot (0001-0032, `a9_eink.ko` `19363dc1…`): `A9_CPUFREQ=b1
  mkramdisk.sh --check --eink` -> `a9-android-eink-boot-b1.img`
  `828d168aa5afe0150a3037c4f552fb48843c275039c035f0df21bb8c4fc07452`, cmdline 483 bytes in the 512-byte field (no
  `cpufreq.off`, `cpufreq.default_governor=schedutil`, eink flag and firmware path present, extra empty), abl-check
  PASS. Stage B1 itself is not boot-tested (sol-smp gates: two cpufreq policies, a demonstrated transition).
- **Hold evidence:** the loop runs every 10 s; every 30 s the E Ink/msm/fp9929a kmsg tail and the eink status go to
  `android/eink/hold/latest-*` (a copy kept every 5 min). On a vendor fault the host grabs the full dmesg, the
  status, the vendor's own `/data/local/tmp/a9ml-fault-dmesg.txt` (written by the fault watch the moment it trips)
  and a logcat tail into `hold/fault-<UTC>-*` before anything else.
- Vendors: `vendor_b-eink.img` `cf92528ee020e80c05e17dee3901aa3277689b165a8a8f054fce2525fabe8386`, `vendor_b.img`
  `bce5468f982e26f702303b4540e5c869e3ba71117bfb3c300ca420d1af22b81b`.

### 9.12 Run 8: the goal, and run-9 images (2026-10-15)

**MEASURED (main, run 8: B1 eink boot `828d168a`, vendor `cf92528e`):** `eink-compare` **100.00 % exact** on the setup
wizard ("Hi there"): the glass holds Android's own frame. A forced Clear was a real update (dry 0, 123 frames,
aborts 0, no stall); late_drive 27, max_gap_drive 23.6 ms. adb t+66 s, `sys.boot_completed=1` at t+123 s, cpufreq
1.80/2.02 GHz with schedutil. The phone was held on slot B for the user.

Since then: **B1 is the default for the eink boot image** (`mkramdisk.sh --eink`; `A9_CPUFREQ=off` builds
`a9-android-eink-boot-nocpufreq.img`; the vkms image keeps `cpufreq.off=1`, `A9_CPUFREQ=b1` gives `-b1`). Snapshot on
patches 0001-0033 (0033: render-ahead ring + pace diagnostics on status line 4; Image.gz `a96e5d0e…`, `a9_eink.ko`
`292ad856…`). The hold appends status line 4 (`pace`) with a timestamp to `android/eink/hold/pace.log` every 30 s
and prints the last samples when it ends.

Run-9 images: `a9-android-eink-boot.img` `bf5099711186aecffaefc623007a1688a91e87607b5caadbbb776866cb8db499` (B1,
cmdline 483 bytes), `vendor_b-eink.img` `5cd1f56bd6669f51d669ee2c77d89d115278e4109ad71703ed8f5b39a6b0dff2`,
`a9-android-boot.img` `47b8fc1388ce8791135563525622494b216a44f8cec64f4a0606245b8ed5ef86`, `vendor_b.img`
`bce5468f…` (unchanged).

## Appendix: files

`scripts/mainline/android/`: `android-boot.sh` (test + restore), `lp-write.sh` (vendor_b writer),
`sm6115-hisense-hlte556n-android.dts`, `mkramdisk.sh`, `ramdisk/fstab.hlte556n`, `build-vendor.sh`,
`vendor/` (A9 vendor files), `pack-vendor.sh`, `pack-boot.sh`, `Containerfile` (secilc), `super-fit.py`,
`extract-golden-vendor.sh`. Kernel: `scripts/mainline/kernel/build.sh` (`A9_ANDROID=1`), `a9-android.config`,
`patches/0014-selinux-…`. Outputs: `~/A9-build/mainline/android/out/` (each with `.sha256`).
