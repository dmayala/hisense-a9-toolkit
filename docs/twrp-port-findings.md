# Porting TWRP to the Hisense A9 (HLTE556N) — Findings & Plan

**Date:** 2026-08-08
**Status:** Recon complete, no build attempted yet.

All facts below were extracted directly from the stock firmware package at
`$A9_DATA/HLTE556N` (originally `C:\Users\dmayala\Desktop\A9\HLTE556N`) (`recovery.img`, `boot.img`,
`rawprogram*.xml`, `super_1.img`) — not from forum posts. Where something is
inferred rather than measured, it says so.

## Known issues (to fix)

Running list of TWRP 3.7.1 misbehaviour seen in use. Add an entry whenever a
TWRP step needs a retry or a workaround.

| Date | Symptom | Workaround | Fix idea |
|---|---|---|---|
| 2026-10-11 | **USB re-enumerates just after boot.** After `fastboot reboot recovery`, `adb get-state` reports `recovery`, then the very next `adb shell` fails with `adb: no devices/emulators found`; the device is back a few seconds later (new adb `transport_id`). | `adb wait-for-recovery` again (or retry) before the first command. | Find what restarts the USB gadget after adbd first comes up (likely a second `sys.usb.config` write in init.recovery*.rc or TWRP's own MTP/adb toggle) and set the final config once; until then, scripts that drive TWRP should wait for a stable transport. |
| 2026-10-11 | `twrp format data` prints "TWRP will not recreate /data/media on an FBE device". | None needed: Android creates it on first boot. | Informational; worth a note in `a9-recover`. |
| 2026-10-11 | **`restorecon` mislabels app data.** In TWRP, `restorecon` on `/data/data/<pkg>/...` set `u:object_r:system_data_file:s0` instead of `system_app_data_file` (TWRP applies `file_contexts` only, not `seapp_contexts`), so the app can no longer read its own files. | `chcon u:object_r:system_app_data_file:s0 <file>` (for a system-uid app; other apps also carry an MLS category). `restorecon` is still right for `/data/adb` (`adb_data_file`). | Ship `seapp_contexts` handling (or a wrapper that refuses `restorecon` under `/data/data`) in the TWRP build; until then never `restorecon` app data from TWRP. |
| 2026-10-11 | `adb reboot` from TWRP went straight to Android twice, although `scripts/twrp.sh` says this TWRP leaves `boot-recovery` in the BCB. | none needed this time | Re-measure: if the BCB is cleared after all on 3.7.1_12-0, simplify `twrp.sh exit`. |

---

## 1. Device facts

| Property | Value | Source |
|---|---|---|
| Codename | `HLTE556N` | `prop.default` |
| SoC / platform | `bengal` — SM6115, Snapdragon 662 | `ro.product.board=bengal` |
| Android | 11, SDK 30, `ro.product.first_api_level=30` | `default.prop` |
| Build | `RKQ1.210107.001` / `L2037.6.01.01`, patch `2022-04-05` | `default.prop` |
| Fingerprint | `Hisense/HLTE556N/HLTE556N:11/RKQ1.210107.001/L2037.6.01.01:user/release-keys` | `default.prop` |
| Arch | `arm64-v8a` (32-bit `armeabi-v7a` present) | `default.prop` |
| Panel | E Ink Carta 1200, 824×1648 native | vendor specs |
| SD card | `/dev/block/mmcblk1p1` (vfat) | `recovery.fstab` |

### Boot image geometry (parsed from `recovery.img` / `boot.img` headers)

```
magic                ANDROID!
header_version       2
page_size            4096
kernel_size          15050789   (identical in boot.img and recovery.img)
kernel_addr          0x00008000
ramdisk_addr         0x01000000
tags_addr            0x00000100
second_size          0
os_version           11.0.0, security patch 2022-04
recovery ramdisk     11147216 bytes, gzip (1f 8b 08)
boot ramdisk          1150942 bytes
```

Addresses are stored base-relative, i.e. `BOARD_KERNEL_BASE := 0x00000000`
with the standard AOSP offsets above.

**`boot.img` and `recovery.img` carry the same kernel** (byte-identical size
15,050,789). This means a prebuilt stock kernel is sufficient — **no kernel
source is required** to get a first build.

### Partition layout

Two findings that dominate everything else:

**A. There is a dedicated recovery partition.**

```
recovery_a / recovery_b   24576 sectors × 4096 B = 100,663,296 B = 96 MiB each
boot_a     / boot_b       96 MiB
dtbo_a     / dtbo_b       24 MiB
vbmeta_a   / vbmeta_b     64 KiB
```

No recovery-in-boot, no `vendor_boot`. You build a recovery image and flash it
to `recovery_a` / `recovery_b`. 96 MiB is enormous — TWRP is ~35 MiB.

**B. Virtual A/B with dynamic partitions.**

```
ro.virtual_ab.enabled     = true
ro.boot.dynamic_partitions = true
super group               qti_dynamic_partitions_a / qti_dynamic_partitions_b
logical partitions        system_a/b, system_ext_a/b, product_a/b, vendor_a/b
```

`super` is a single unslotted physical partition; the slotting lives in the LP
metadata. `metadata` partition is present and required.

Full label list per LUN (from `rawprogram*.xml`):

- **LUN0:** `super`, `userdata`, `metadata`, `misc`, `persist`, `cache`,
  `recovery_a/b`, `logo_a/b`, `tpf_a/b`, `vbmeta_system_a/b`, `keystore`,
  `frp`, `ssd`, `diag`, `flag`, `databackup`, `kdebug`, `kdebuginfo`,
  `phonelog`, `prospecfg`, `reserve`, `grow`
- **LUN1/2:** `xbl_a/b`, `xbl_config_a/b`
- **LUN3:** `cdt`, `ddr`
- **LUN4:** `boot_a/b`, `vbmeta_a/b`, `dtbo_a/b`, `abl_a/b`, `tz_a/b`,
  `modem_a/b`, `dsp_a/b`, `keymaster_a/b`, `cmnlib_a/b`, `cmnlib64_a/b`,
  `hyp_a/b`, `rpm_a/b`, `devcfg_a/b`, `featenabler_a/b`, `imagefv_a/b`,
  `qupfw_a/b`, `uefisecapp_a/b`, `mdtp_a/b`, `mdtpsecapp_a/b`,
  `bluetooth_a/b`, `devinfo`, `dip`, `splash`, `logdump`, `logfs`, `limits`,
  `rawdump`, `secdata`, `spunvm`, `storsec`, `uefivarstore`, `toolsfv`,
  `catefv`, `catecontentfv`, `cateloader`, `multiimgoem`, `multiimgqti`, `apdp`
- **LUN5:** `modemst1`, `modemst2`, `fsc`, `fsg`

### Encryption

From `default.prop`:

```
ro.crypto.volume.metadata.method       = dm-default-key
ro.crypto.volume.filenames_mode        = aes-256-cts
ro.crypto.dm_default_key.options_format.version = 2
ro.crypto.allow_encrypt_override       = true
```

From `system/etc/recovery.fstab`:

```
/dev/block/bootdevice/by-name/metadata  /metadata  ext4  ...  wait,check,formattable,wrappedkey
/dev/block/bootdevice/by-name/userdata  /data      f2fs  ...  wait,check,encryptable=footer
```

FBE plus metadata encryption via `dm-default-key`, with **hardware-wrapped
keys** (`wrappedkey`). This is the worst-case configuration for TWRP
decryption. See §4B.

---

## 2. Prerequisites — already satisfied

Confirmed present in the toolkit data (originally `C:\Users\dmayala\Desktop\A9\`, now `$A9_DATA`):

- **Unlocked bootloader + AVB disabled** — implied by the working GSI installs
  in `14/` (LineageOS 21 `arm64_bgN`, AOSP 14 GAPPS) and the Magisk-patched
  boot images in `newroot/` (all deleted from $A9_DATA, the last one, `root/magisk_patched-27001_7a6jR.img`, on 2026-10-11; originals on the Windows PC).
- **Custom fastboot binaries** — `fastboot/linux-x86.zip`, `windows-x86.zip` (deleted from $A9_DATA 2026-10-10; original on the Windows PC)
- **Full EDL unbrick path** — `prog_firehose_ddr.elf`,
  `prog_firehose_ddr_001360E1.elf`, `gpt_main[0-5].bin` + `gpt_backup[0-5].bin`, and an existing partition dump
  at `Stock/Backup-2023-09-11-173822` (deleted from $A9_DATA 2026-10-10; original on the Windows PC).

**Before touching anything: take a fresh full EDL dump.** The 2023 backup
predates the current GSI install.

---

## 3. Build plan

### Host

- ~400 GB free disk, 16 GB RAM minimum (32 GB comfortable).
- **Never build on the NFS share (`$A9_DATA`) or on NixOS bare metal.** AOSP
  requires a case-sensitive local FS; builds run on GitHub Actions. (Legacy,
  Windows: the equivalent rule was to avoid WSL2 `/mnt/c`, whose DrvFs is
  case-insensitive and slow.)

### Source

```bash
repo init -u https://github.com/minimal-manifest-twrp/platform_manifest_twrp_aosp.git -b twrp-12.1
repo sync -c -j8 --force-sync --no-clone-bundle
```

Use `twrp-12.1` despite the device being Android 11 — it has the Virtual A/B
and dynamic-partition handling this device needs.

### Device tree

```bash
pip install twrpdtgen
twrpdtgen /path/to/HLTE556N/recovery.img -o device/
# -> device/hisense/HLTE556N/
```

Then correct `BoardConfig.mk` with the measured values:

```makefile
TARGET_ARCH                := arm64
TARGET_BOARD_PLATFORM      := bengal

BOARD_KERNEL_BASE          := 0x00000000
BOARD_KERNEL_PAGESIZE      := 4096
BOARD_KERNEL_OFFSET        := 0x00008000
BOARD_RAMDISK_OFFSET       := 0x01000000
BOARD_KERNEL_TAGS_OFFSET   := 0x00000100
BOARD_BOOT_HEADER_VERSION  := 2

BOARD_RECOVERYIMAGE_PARTITION_SIZE := 100663296   # 96 MiB

# Dedicated recovery partition exists — do NOT set recovery-as-boot
BOARD_USES_RECOVERY_AS_BOOT := false

BOARD_USES_METADATA_PARTITION := true
BOARD_SUPER_PARTITION_GROUPS  := qti_dynamic_partitions
AB_OTA_UPDATER                := true
TW_INCLUDE_REPACKTOOLS        := true
```

Use the stock kernel from `recovery.img` as a prebuilt rather than building
one.

**Verify the Virtual A/B flag names against `bootable/recovery` in the
twrp-12.1 tree itself** — they changed between branches and most published
guides are stale.

### Reference trees

Closest published `bengal` + Virtual A/B device trees to crib a working
BoardConfig from:

- Moto G Power 2021 — `borneo`
- Redmi 9T / Poco M3 — `juice`
- Moto E7 Plus

---

## 4. The two device-specific problems

### A. E Ink display

This is the reason no TWRP build exists for this device.

**Encouraging evidence:**

- Stock recovery ships **303 image assets** in `res/images/`, including a full
  set of dedicated `*_ink.png` variants (`black_vision_ink.png`,
  `erasing_en_ink.png`, `erasing_text_ink.png`, …). The panel is genuinely
  driven in recovery mode.
- Strings extracted from stock `/system/bin/recovery` (431,568 bytes, built
  from Hisense's `bootable/recovery/hmct_recovery.cpp`) contain **no EPD,
  waveform, or E Ink sysfs paths whatsoever** — the only sysfs nodes
  referenced are `/sys/class/power_supply/battery/{capacity,voltage_now}` and
  `/sys/class/thermal/`.

That absence is the key signal: the stock recovery does not manually trigger
panel refreshes, so the **kernel panel driver almost certainly auto-refreshes
on framebuffer writes**. TWRP's stock `minui` fbdev backend has a real chance
of working unmodified.

**Open question — resolution mismatch.** The recovery UI assets are
**720×1280**, not the panel's native 824×1648. Confirm the actual
recovery-mode framebuffer geometry at runtime before choosing `TW_THEME` and
`TARGET_RECOVERY_PIXEL_FORMAT`:

```bash
adb shell cat /sys/class/graphics/fb0/modes
adb shell cat /sys/class/graphics/fb0/virtual_size
adb shell cat /sys/class/graphics/fb0/bits_per_pixel
```

**Expect theming work regardless.** TWRP's default dark theme will be
unreadable and will ghost badly on E Ink. Plan on a high-contrast
(black-on-white, heavy line weight) theme and possibly forcing a full refresh
per frame.

**Fallback if rendering proves intractable:** TWRP exposes a full command line
(`twrp install`, `twrp backup`, `twrp wipe`) usable entirely over `adb shell`.
A headless TWRP is still a genuinely useful result and should be treated as
milestone 1.

### B. `/data` decryption — assume it will not work

`wrappedkey` + `dm-default-key` is the hardest FBE case to support. Worse, the
stock recovery ramdisk contains **no keymaster, gatekeeper, or qseecom
binaries at all** (`vendor/bin/hw` is empty; no `*keymaster*` / `*gatekeeper*`
anywhere in the ramdisk) — so there is nothing to lift from stock recovery.
They would have to be pulled out of `vendor` in `super` and wired up by hand,
and several SD662 devices never achieved this.

**Design around it.** The SD card at `/dev/block/mmcblk1p1` gives TWRP a
working medium. Flashing zips, imaging partitions, and restoring images all
work fine without ever decrypting `/data`.

---

## 5. Test loop

```bash
fastboot flash recovery_a twrp.img
fastboot flash recovery_b twrp.img
adb reboot recovery

adb shell                      # adbd starts early — works even with a dead screen
adb pull /tmp/recovery.log     # primary debugging artifact
```

Do **not** rely on `fastboot boot twrp.img` for testing — Hisense's custom
fastboot likely blocks it. Flashing `recovery_a` directly is acceptable given
the EDL restore path.

---

## 6. Effort estimate

| Milestone | Estimate | Risk |
|---|---|---|
| TWRP boots, responds over adb, `/tmp/recovery.log` readable | a weekend | low |
| Usable E Ink rendering | days if the driver auto-refreshes (likely); weeks if the refresh path must be reversed | medium |
| `/data` decryption | — | assume it won't happen |

---

## 7. Worth deciding first

What is TWRP actually for here?

- **Nandroid backups** — with an unlocked bootloader and root already in hand,
  `dd` over `adb root` to the SD card covers most of this today.
- **Flashing zips / swapping GSIs more comfortably** — TWRP is the right tool
  and worth the port.

---

## Appendix: commands used for this recon

```bash
# Partition sizes
grep -o '<program[^>]*label="recovery_a"[^>]*>' rawprogram_unsparse0.xml

# Boot header fields (offset 8, 10 × u32)
dd if=recovery.img bs=1 skip=8 count=40 | od -A none -t u4

# Recovery ramdisk (page_size 4096; kernel occupies pages 1..3675)
dd if=recovery.img bs=4096 skip=3676 count=2723 of=rec_ramdisk.bin
gzip -dc rec_ramdisk.bin | cpio -idm

# Logical partition names in super
tr -c '[:print:]' '\n' < super_1.img | grep -E '^(system|vendor|product|system_ext)(_a|_b)?$'
```

## References

- [aimindseye/hisense-a9 — bootloader unlock](https://github.com/aimindseye/hisense-a9/blob/main/unlockbootloader.md)
- [XDA — Hisense A9 Root (Snapdragon 662)](https://xdaforums.com/t/hisense-a9-root-snapdragon-662.4495809/)
- [Liliputing — A9 unofficial bootloader unlock and root](https://liliputing.com/hisense-a9-e-ink-smartphone-gets-unofficial-bootloader-unlock-and-root/)
- [minimal-manifest-twrp](https://github.com/minimal-manifest-twrp/platform_manifest_twrp_aosp)
- [twrpdtgen](https://github.com/twrpdtgen/twrpdtgen)
