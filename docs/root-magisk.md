# Root: migrating from Kitsune Mask to official Magisk

**Date:** 2026-10-11
**Status:** Done (Magisk v30.7, verified on slot b 2026-10-11). Later the same day
slot A and the golden EDL dump (`edl_2026-10-11_102717`, since superseded by `edl_2026-10-11_132444` and deleted) were brought to the same
Magisk v30.7 boot (sha256 `679e95b8…`). Nothing on the phone uses Kitsune any more.
The Kitsune APK, the Kitsune-patched boot image, `kitsune-search.md` and the old
golden dump `edl_2026-10-10_224821` were deleted from `$A9_DATA` on 2026-10-11, so
the file names below are historical.

## 1. Why

The phone was rooted with **Kitsune Mask** (HuskyDG's Magisk fork, build 27001,
daemon `R6687BB53-kitsune`). Its developer says his GitHub account was hijacked
and not to use it any more (reported by the user, 2026-10-11). Upstream
(`HuskyDG/magisk-files`) is gone. The only manager we had came from an archive.org
mirror. Official Magisk can't simply take over: the daemon only accepts a manager
signed with its own key, so official Magisk v27 showed "Installed: N/A" and `su`
hung (`$A9_DATA/root/README.md`). The boot image itself has to be re-patched.

## 2. Result (MEASURED 2026-10-11)

| | Before | After |
|---|---|---|
| `magisk -v` | `R6687BB53-kitsune:MAGISK:R` (27001) | `30.7:MAGISK:R` (30700) |
| `boot_b` sha256 | `fe308380…` (= `root/magisk_patched-27001_7a6jR.img`) | `679e95b8…` (= `root/magisk_patched-30700_boot_b.img`) |
| manager | `io.github.huskydg.magisk` 27001 | `com.topjohnwu.magisk` 30700 |
| ramdisk stub signer | `CN=Nguyen Hoang The Vi, OU=HuskyDG` `15e145f1…` | `CN=John Wu` `b4cb83b4…` |
| kernel | | **byte-identical** (15,047,394 bytes) |
| config | `KEEPVERITY=true KEEPFORCEENCRYPT=true PREINITDEVICE=metadata` | same |

- Magisk app home: `Installed 30.7 (30700)`, `Ramdisk Yes`, `Zygisk No`, no
  "Requires additional setup". ("Latest N/A" is only the missing network.)
- The adb shell's root grant **carried over** without a prompt: `magisk.db`
  `policies` row `uid=2000|policy=2` (allow).
- No modules were installed before or after.
- The E Ink HWC fix still held after the reboot (`mDebugDisableHWC=1`).

## 3. Procedure (what was run)

1. **Get Magisk** from the official release only, and check the signer:
   `Magisk-v30.7.apk`, sha256 `e0d32d21…`, `apksigner verify --print-certs` →
   `CN=John Wu`, cert SHA-256 `b4cb83b4dad99f997dbe872f013aa16c14eec41d167021f371f7e1330f273ee6`
   (the same certificate as the v27 APK recorded in `root/README.md`).
2. **Patch on the phone, as root, with the APK's own `boot_patch.sh`**, using a copy
   of the current Kitsune `boot_b` image as input. There's no need to find the stock
   boot: Kitsune images are Magisk-format, so the script detects "Magisk patched
   boot image detected", restores the original ramdisk from the image's
   `.backup/`, and re-patches. That's the path the official app's Direct Install
   takes on a rooted phone. Staging dir `/data/local/tmp/mnew`:
   `magiskboot magiskinit magisk magiskpolicy init-ld busybox` (from
   `lib/arm64-v8a/lib*.so`), `boot_patch.sh util_functions.sh stub.apk` (from `assets/`).
   ```sh
   cd /data/local/tmp/mnew
   export KEEPVERITY=true KEEPFORCEENCRYPT=true RECOVERYMODE=false ASH_STANDALONE=1
   ./busybox sh boot_patch.sh /data/local/tmp/mnew/kitsune-boot_b.img   # -> new-boot.img
   ```
   (The Kitsune image recorded a stock SHA1 `fc5e4d12…` that matches no boot image
   on the share. This method doesn't need it.)
3. **Check the output on the host** with the APK's `lib/x86_64/libmagiskboot.so`:
   `cpio test` = 1 (Magisk), `.backup/.magisk` flags unchanged, the
   `overlay.d/sbin/stub.xz` signer is John Wu, and the kernel is identical.
4. **Replace `/data/adb/magisk`** with the v30.7 files (Direct Install's step).
   The Kitsune directory held almost nothing (a 1.5 KB tar).
5. **Swap managers before flashing:** `adb install Magisk-v30.7.apk`,
   `pm uninstall io.github.huskydg.magisk`.
6. **Flash from bootloader fastboot** (not fastbootd), slot b only:
   `adb reboot bootloader`, `fastboot flash boot_b magisk_patched-30700_boot_b.img`,
   `fastboot reboot`. Run `a9-health.sh` before and after.
7. Grant Magisk its notification permission
   (`pm grant com.topjohnwu.magisk android.permission.POST_NOTIFICATIONS`).

Rollback: **Kitsune rollback is no longer possible.** The Kitsune APK and patched
boot image were deleted on 2026-10-11, and the golden dump now carries Magisk
v30.7 (`edl_2026-10-11_102717`, since superseded by `edl_2026-10-11_132444` and deleted). To undo a bad Magisk boot, restore the golden dump
or re-flash `magisk_patched-30700_boot_b.img`.

## 4. After a /data wipe

The daemon installs a **stub** manager that wants the network.
`scripts/los-skip-setup.sh` (or `los-skip-setup.sh root`) installs the newest
`Magisk-v*.apk` from `$A9_DATA` and grants root to the shell (official Magisk only;
the Kitsune handling was removed 2026-10-11). Never tap OK on "Requires Additional Setup": it
re-flashes the boot partition.

## 5. Not done

- Nothing outstanding for slot A or the golden dump: both carry Magisk v30.7
  since 2026-10-11 (the Kitsune boot images are gone).
- Patcher images don't include root; nothing to change there.
