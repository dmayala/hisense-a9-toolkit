---
name: a9-backup
description: Back up and restore Hisense A9 (HLTE556N) partitions and user data over adb or EDL, with size verification. Use before any flashing operation, or when restoring a device after a failed flash. Covers which partitions are irreplaceable, the never-exec-out rule, the NFS data share, and app/config capture.
---

# Backing up the Hisense A9

Scripts in `~/A9/scripts/`. Full reasoning in
`~/A9/docs/backup-and-recovery.md`.

## The trap that silently destroyed backups

**Never stream `dd` through `adb exec-out`.** It can CRLF-translate the byte stream (first seen with Windows adb) — every `0x0A` becomes `0x0D 0x0A`. Images come back
**corrupted and slightly oversized**, and nothing errors:

```
boot_a    100,712,136 bytes   (should be 100,663,296)  +48,840
frp           524,289 bytes   (should be 524,288)          +1
ssd             8,192 bytes   correct — all-zero, no 0x0A to translate
```

An all-zero partition comes back correct, which makes spot-checking one
partition dangerously misleading.

**Instead: `dd` to a temp file on the device, then `adb pull`** (the file
transfer protocol is binary-safe), and **size-verify every image** against
`scripts/partition-map.txt`. `backup-partitions.sh` does both and treats a size
mismatch as a hard failure.

Native Linux adb does not translate, but the scripts keep dd-to-file + `adb
pull` + size verification anyway: it is what proves an image is intact.
State checks go through `a9_adb_state` in `a9-env.sh`, which still strips CR.

**The write side has the opposite trap: never `adb exec-in 'dd of=...'`.**
toybox `dd` has no `iflag=fullblock`, so short pipe reads truncate the write
silently (the A6L port wrote 64 of 115 KiB this way; A6L
`docs/update-keepdata-20260929.md`). Push the file, check its sha256 on the
phone, then write it, then read it back.

**Proving a flash: `scripts/a9-readback.sh <partition> <image>`.** A booted
ext4 system partition is never byte-identical to its image: every mount
rewrites the superblock and inode-table blocks (measured 2026-10-13: 14 such
blocks plus directory slack in `/`, data blocks identical). The script checks
data blocks exactly and allows only those metadata blocks. `--golden` compares
slot A/B with `data/golden.tsv` and says when the golden dump lags main.

## Usage

```bash
cd ~/A9/scripts
./backup-partitions.sh all        # DO THIS — includes super (~11.5 GiB)
./backup-partitions.sh            # tiers 1+2 only, ~800 MiB, NO super
./backup-partitions.sh critical   # irreplaceable set only, ~150 MiB
```

**Always use `all`.** The default omits `super`, which holds the installed
system/GSI. Running the default before a flash is a documented mistake from
2026-08-08.

Requires: booted Android, USB debugging, and **root granted to `Shell` in
Magisk → Superuser** (an instant `Permission denied` means a remembered deny,
not a missing prompt — fix it in the Magisk UI).

Output goes to `$A9_BACKUPS/<timestamp>/` = `$A9_DATA/Backups/` (override
with `OUTBASE=`). Existing backup folders there are read-only reference.

### `$A9_DATA` is an NFS share — write to it plainly

`$A9_DATA` → `/srv/nas/A9`: NAS NFSv4 on ZFS with ACLs, **owner
squashed to root**. So:

- **No `chmod`/`chown`/`chgrp`**, and do not rely on permission bits sticking.
- **No temp-then-rename** writes. Dot-prefixed temp files are rejected. Write
  the final filename directly (the backup scripts' `adb pull <tmp-on-device>
  <final path>` already does).
- rsync onto it needs
  `-rt --inplace --no-perms --no-owner --no-group --omit-dir-times`
  (default rsync writes a dot-temp and renames it).

## What is actually irreplaceable

The stock `HLTE556N/` package restores every Qualcomm image, `super`, and stock
`boot`/`recovery`. It **cannot** restore anything unique to this handset:

| Partition | Loss if gone |
|---|---|
| `persist` | Sensor/touch calibration, WiFi/BT MAC |
| `modemst1`, `modemst2`, `fsg`, `fsc` | Modem NV — **IMEI** |
| `keystore` | Keymaster keyblobs — encrypted `/data` becomes unrecoverable |
| `metadata` | `dm-default-key` state that `/data` is encrypted against |
| `devinfo`, `apdp`, `dip`, `secdata`, `prospecfg`, `tpf_*` | Per-device provisioning |

A factory flash generally leaves modem NV and `persist` alone — verified
by experience on this device, IMEI survived. **Prefer not to restore them**
unless something is actually wrong; writing modem NV needlessly is a way to
*lose* an IMEI.

## `userdata` is not usefully imageable

228 GiB, FBE-encrypted with **hardware-wrapped TEE-bound keys**. An image only
restores to this exact handset with `keystore`, `metadata`, and TEE state all
intact. Use app-level backup (Neo Backup / Swift Backup) to the SD card, then
copy to the PC.

**Restore rule:** `userdata` + `metadata` + `keystore` are a **set** — restore
all three together or none. Mixing a fresh `/data` with old `metadata` yields a
partition the device can neither mount nor repair.

`modemst1` + `modemst2` + `fsg` + `fsc` are likewise a set. `persist` is
standalone and always safe.

## Capturing the setup, not just partitions

Equally important for rebuilding, and cheap:

```bash
ADB=adb
$ADB shell 'pm list packages -3 --show-versioncode'   # user apps + versions
$ADB shell 'pm list packages -d'                      # disabled
$ADB shell 'su -c "ls /data/adb/modules"'             # Magisk modules
$ADB pull /sdcard/Download  <dest>
$ADB pull "/sdcard/xposed-edge-pro-backup (3).bak" <dest>
```

Notes:
- **`find` does not work on `/sdcard`** (scoped storage) — it returns nothing
  even for files sitting at the top level. Use `ls`.
- LSPosed's enabled-module list is in `/data/adb/lspd/config/modules_config.db`
  (+ `-wal`, `-shm`). A live `cat` copy may be unreadable by sqlite; the loaded
  modules are easier to read from `/data/adb/lspd/log/modules_*.log`.
- Don't forget **2FA seeds** — an authenticator app's data is the classic
  irrecoverable loss.

## Verifying a backup

1. `manifest.txt` has no `FAILED`/`CORRUPT` lines — the script records size and
   SHA-256 per image.
2. Sizes match `partition-map.txt` exactly.
3. Cross-check something known: `recovery_b` should resemble stock. Note it
   will **not** be byte-identical to the package's `recovery.img` — the device
   runs a different build (kernel 15,047,394 vs the package's 15,050,789).
4. It already lives on the NAS; keep a second copy somewhere else as well.

## Restoring

```bash
fastboot flash <partition> <file>        # bootloader fastboot, physical partitions only
```

Logical partitions inside `super` (`system_b` etc.) **cannot** be flashed this
way — this bootloader has no fastbootd (`is-userspace: no`). They need EDL
(see the `a9-edl` skill) or a working Android.
