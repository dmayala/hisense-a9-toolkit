# Bootloader unlock, boot image work, rooting, GSI flashing

**Status:** ported from A9Toolkit (`cmd/unlock`, `cmd/bootimg`, `cmd/gsi`,
`docs/workflows.md`, `docs/safety.md`, `docs/device.md`) on 2026-08-13, **not
re-verified on build-host**. Written 2026-10-11 to keep only what `~/A9`
(CLAUDE.md, the `a9-*` skills) lacked. Where `~/A9` measured something later,
that wins; the conflicts are listed at the end. Safety rules live in CLAUDE.md
and are not repeated.

The phone today is already unlocked, rooted (official Magisk v30.7 on boot_b, see [root-magisk.md](root-magisk.md)) and on LOS 23.2;
this page is for starting over on another handset or after a stock reflash.

## Bootloader unlock (wipes /data)

Platform-tools `fastboot` **cannot** unlock this device: `flashing unlock` is
rejected with a generic failure that reads like the device refusing. Only
Hisense's own fastboot build (Denzil Ferreira's Linux package, asset
`fastboot-hisense`, at `$A9_DATA/tools/fastboot-hisense/fastboot`) is accepted.
The stock bootloader exposes `unlocked: yes|no` in `fastboot getvar unlocked`.

1. Back up first (`scripts/backup-edl.sh`, a9-backup skill). Unlock wipes `/data`.
2. Settings > About > tap **Build number** 7 times.
3. Developer options: **OEM unlocking** on, **USB debugging** on.
4. `adb reboot bootloader`
5. Check state, then unlock with the Hisense binary (copy it off NFS first if
   the exec bit is not honoured there):

```bash
fastboot getvar unlocked                     # "unlocked: no"
$A9_DATA/tools/fastboot-hisense/fastboot flashing unlock
```

6. Confirm on the phone with the volume keys, then `fastboot getvar unlocked`
   should read `yes`. Everything after that works with stock `fastboot`
   (a9-device skill).

## Boot image geometry, unpack and repack

Offsets (same as the a9-device skill): header v2, page 4096, base
`0x00000000`, kernel `0x00008000`, ramdisk `0x01000000`, tags `0x00000100`.
Take the exact `mkbootimg` arguments from `unpack_bootimg --format=mkbootimg`
(a9-twrp skill, "Repacking the stock recovery"); the A9Toolkit wrapper simply
hard-coded the values above:

```bash
unpack_bootimg --boot_img IMG --out DIR            # kernel, ramdisk, dtb, recovery_dtbo, ...
mkbootimg --header_version 2 --pagesize 4096 --base 0x00000000 \
  --kernel_offset 0x00008000 --ramdisk_offset 0x01000000 --tags_offset 0x00000100 \
  --kernel DIR/kernel --ramdisk DIR/ramdisk --dtb DIR/dtb \
  [--recovery_dtbo DIR/recovery_dtbo] --cmdline "$(cat DIR/cmdline)" --output OUT.img
```

Pitfalls (the recovery_dtbo one is already in CLAUDE.md; the rest is new):

- **Check a recovery image carries `recovery_dtbo`** before flashing:
  `unpack_bootimg` output must show a non-empty `recovery_dtbo` section
  (stock: 88,722 bytes). Absent means unbootable and silent. Fine for
  `boot.img`, fatal for a recovery image.
- Ramdisk compression varies: sniff with `file`, then `gzip -dc`, `lz4 -dc` or
  `xz -dc` piped into `cpio -idm`. Rebuild with `cpio -o -H newc --owner=0:0`.
- `unpack_bootimg` is also the quick way to compare a device-pulled image with
  the package's (kernel size 15,047,394 vs 15,050,789, CLAUDE.md fact 2).

## Rooting with Magisk (stock boot)

Assets `boot-vbmeta` (stock `boot.img` + `vbmeta` for `.04.06.00` and
`.08.01.00`) and `magisk` (now v30.7). Any other build: pull `boot.img` from the
phone.

1. Copy the stock `boot.img` to the phone, patch it in the Magisk app, pull
   the `magisk_patched-*.img`.
2. From bootloader fastboot (not fastbootd), flash to the **active** slot and
   disable verity:

```bash
adb reboot bootloader
fastboot getvar current-slot                       # e.g. b
fastboot flash boot_<slot> magisk_patched.img      # the active slot
# disable verity/verification using the stock package's vbmeta (A9_FW):
fastboot --disable-verity --disable-verification flash vbmeta_<slot> $A9_DATA/HLTE556N/vbmeta.img
fastboot reboot
```

(These are the exact commands A9Toolkit's `a9 flash boot` / `a9 flash
disable-verity` ran, including a size check of the image against the
partition. The golden phone already has AVB disabled.)

Today's setup: boot_b was **Kitsune**-patched until 2026-10-11 and was migrated to
official Magisk v30.7 by re-patching the Kitsune image with Magisk's own
`boot_patch.sh`. See [root-magisk.md](root-magisk.md). Slot A and the golden dump also boot Magisk v30.7 now (the Kitsune files were deleted 2026-10-11).

## GSI flashing and preparation

- `system` is a logical partition inside `super`: only **fastbootd** can write
  it. Sequence: `adb reboot fastboot`, confirm `fastboot getvar is-userspace`
  prints `yes`, `fastboot flash system <gsi>.img`, `fastboot reboot`.
  Bootloader fastboot refuses it. (Opposite of recovery, which is
  bootloader-only. Older a9-device notes claim there is no fastbootd; CLAUDE.md's
  2026-08-09 measurement that fastbootd works wins.)
- `.img.xz` GSIs must be decompressed first (`xz -dk -T0`).
- Hand-editing an image requires the unshare sequence (CLAUDE.md rule 9). A9Toolkit
  grew the filesystem by 512 MB first: `resize2fs img <size+512M>`.
  `e2fsck` exit 1 or 2 means "errors fixed" and is fine.
- A9 E Ink patches come from the patcher (`work-gsi/A9PatchingService`), not
  loose scripts.

## OTA `L2037.6.08.01.00` and anti-rollback

The stock package in `$A9_DATA/HLTE556N/` is `L2037.6.04.06.00` (2022-07-12).
The **latest OTA is `L2037.6.08.01.00`**, reachable via system update on a stock
phone. A full factory flash writes the bootloader chain (xbl, tz, abl): on a
phone that has taken `.08.01.00` it is a **downgrade** and can trip
anti-rollback into a hard brick beyond EDL's reach. Prefer a `super`-only
restore (`scripts/flash-stock-super.sh`, `scripts/restore-golden.sh`). A9Toolkit
required typing `FLASH STOCK` before any full flash. **Answered 2026-10-13: the
phone's bootloader chain is newer than the `.04.06.00` package.** Golden
`abl_a`/`abl_b` differ from `HLTE556N/abl.elf` (ABL build Dec 28 2022 vs Jul 13
2022; xbl, tz, devcfg, imagefv, xbl_config also differ). That fits `.08.01.00`
(inferred, not proven). So a full package flash IS a downgrade: never do it
(CLAUDE.md rule 13). (The anti-rollback behaviour itself is from A9Toolkit's
notes, not re-verified.) See [a6l-harvest-2026-10-13.md](a6l-harvest-2026-10-13.md) §1.

## Other small facts

- Backup tiers (A9Toolkit `data/partitions.tsv`): `critical` 18 partitions
  (IMEI, calibration, keymaster, `metadata`; unrecoverable), `state` 13
  (boot, recovery, dtbo, vbmeta, logo, misc), `bulk` 7 (`super`, modem, dsp,
  mdtp), `other` 53. Full table was derived from `COM4_PartitionsList.xml`
  (see `scripts/partition-map.txt`).
- Check the BCB before restoring any `misc` backup: one taken after
  `adb reboot bootloader` contains `bootonce-bootloader` and boots straight to
  fastboot forever (`~/A9` already prefers `misc-clean.bin`).
- Recovery framebuffer geometry is 448x829 on both stock and TWRP
  (`/sys/class/drm/card0-DSI-1/modes`); the panel expands it to 824x1648.
- EDL: `fastboot oem edl` from the bootloader and volume up + down while
  plugging in USB are A9Toolkit's EDL-entry routes. **`fastboot oem edl` does not
  exist:** the decompressed ABL has no `edl` command string at all (2026-10-13).
  There is no route from bootloader fastboot to EDL.
- The Sahara hello is sent once per EDL entry; `Received 0 bytes` means
  re-enter EDL. Prefer one `edl rl` over looping per partition. A `lun6` error
  during a dump is expected (only LUNs 0-5 carry partitions).
- `edl wl` of `rawprogram`-style GPT restores must apply `patch4.xml` (already in
  the a9-edl skill).

## Not ported

Windows/WSL2/QFIL/QPST history, the `a9` dispatcher UX (typed confirmations,
tier classification), and the nix dev shell: superseded by `~/A9/scripts`.
