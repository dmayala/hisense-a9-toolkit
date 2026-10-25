# Backing up the Hisense A9 before flashing

**Created:** 2026-08-08
**Status:** Not yet performed — do this before Phase 3 of the
[TWRP build plan](plans/twrp-build-plan.md).
**Updated 2026-10-10:** commands and paths now describe the native NixOS
toolchain (Proxmox VM, `~/A9`, data in `$A9_DATA`). The Windows/WSL
procedures (QFIL, QPST, usbipd, Zadig, `edl.exe`) are retired; see the
"Legacy" notes and [`linux-native-migration.md`](linux-native-migration.md).
Per-script detail: `.claude/skills/a9-edl/SKILL.md`, `a9-backup/SKILL.md`.

---

## The core insight

**Most of your phone is already backed up.** The stock firmware package at
`HLTE556N/` can restore every Qualcomm image, the bootloader chain, `super`,
and stock `boot`/`recovery`. The GSI images in `14/` can restore the ROM.

What that package **cannot** restore is anything unique to *your specific
handset*. That's the short list worth protecting:

| Partition | Size | What you lose |
|---|---|---|
| `persist` | 32 MiB | Sensor and touch calibration, WiFi/BT MAC |
| `modemst1` / `modemst2` | 2 MiB ea | Modem NV — **IMEI, radio calibration** |
| `fsg` / `fsc` | small | Modem filesystem |
| `keystore` | small | Keymaster keyblobs — **lose these and encrypted `/data` is gone forever** |
| `metadata` | 16 MiB | `dm-default-key` metadata that `/data` is encrypted against |
| `devinfo` | 64 MiB | Device info / unlock state |
| `apdp`, `dip`, `secdata`, `prospecfg`, `tpf_*` | small | Per-device provisioning |

A wiped `persist` means a phone whose auto-brightness and touch never work
right again. A wiped `modemst*` means no IMEI. Neither is downloadable.

---

## How much risk are you actually taking?

Worth being honest about this: **the Phase 3 flash is low-risk for your data.**

We write to `recovery_a` only. That partition is not involved in booting
Android — the worst realistic outcome is "recovery doesn't boot", fixed by
flashing the stock `recovery.img` back. `/data`, `super`, and the bootloader
chain are untouched.

The scenario that *does* cost you everything is an EDL restore, which
rewrites the device wholesale. That's the fallback if something goes badly
wrong — and it's precisely why the irreplaceable partitions above need to be
sitting on your PC first.

---

## Tier 1 — App and user data (do this first)

This is the part that actually hurts to lose, and it's the part no partition
image reliably gives back.

**Why a `dd` of `userdata` is not a real backup here.** It's 228 GiB, and it's
FBE-encrypted with **hardware-wrapped keys bound to the TEE**. The image only
restores to this exact handset with `keystore`, `metadata`, and TEE state all
intact. Any of those changing turns it into 228 GiB of noise.

Use an app-level backup instead — you're already rooted:

- **Neo Backup** (formerly OAndBackupX) — actively maintained, backs up APKs
  plus app data per-app. Recommended.
- **Swift Backup** — good alternative, nicer UI.
- Target the **SD card**, then copy the folder to your PC. Don't leave the
  only copy on the device you're about to flash.

Also grab manually:

- `DCIM/`, `Pictures/`, `Download/`, `Documents/`
- Signal / WhatsApp in-app backups (these do not survive a data restore)
- Your Magisk module list and settings
- 2FA seeds — **authenticator apps are the classic thing people lose here**

## Tier 2 — Partition images

Script: [`../scripts/backup-partitions.sh`](../scripts/backup-partitions.sh)

It streams each partition off the device with `adb exec-out ... dd`, so
nothing is written to the phone's own storage and it needs no free space
there. Output goes to `$A9_BACKUPS` (`$A9_DATA/Backups/<timestamp>/`, an NFS
share: written plainly, no chmod/chown). The images are written with `dd` to a
file on the device, then `adb pull`ed and size-verified; the old
`exec-out` CRLF corruption was a Windows `adb.exe` artifact, but the
dd-pull-verify rule stays.

**Prerequisites:** device booted into Android, USB debugging on, and root
granted to *Shell* in Magisk → Superuser.

```bash
cd ~/A9/scripts

./backup-partitions.sh critical   # ~150 MiB — the irreplaceable set only
./backup-partitions.sh            # ~800 MiB — adds boot/recovery/dtbo/vbmeta/logo
./backup-partitions.sh all        # ~11.5 GiB — adds super (10 GiB) + modem + dsp
```

Recommendation: run **`all`** once now, while the device is healthy. 11.5 GiB
is cheap, and `super` holds your current LineageOS GSI, which turns a rebuild
into a restore. Then run `critical` again any time before a risky operation.

The script writes a `manifest.txt` with sizes and SHA-256 for every image, and
records the running build's fingerprint and active slot.

**`userdata` is deliberately excluded** for the reasons above.

### adb vs EDL — which capture method?

**The images are the same bytes either way.** A `dd` of
`/dev/block/by-name/persist` and an EDL read of `persist` produce an identical
raw partition image. So anything captured by `backup-partitions.sh` is
**already EDL-flashable** — you can write it back with
`scripts/edl w persist persist.img` on a device that no
longer boots.

The difference is only in what each method *requires*:

| | `backup-partitions.sh` (adb) | `backup-edl.sh` (EDL) |
|---|---|---|
| Device must boot | **Yes** — booted Android + root | No — 9008 mode is enough |
| Setup needed | None | Phone in 9008 (`adb reboot edl`); the VM owns the xHCI, so nothing else |
| Captures every LUN incl. bootloader chain | Awkward | Yes, automatically |
| Emits flashable rawprogram XML | No | **Yes** (`--genxml`) |
| Speed | Faster | Slower |
| Proves the restore path works | No | **Yes** |

That last row is the real argument for doing an EDL capture at least once.
Running it end-to-end confirms three things you otherwise only *assume* on the
day you actually need them: that the device enters 9008, that the VM sees
05c6:9008 (udev rule from `a9-usb.nix`), and that the firehose loader is
accepted by this handset's bootloader. Discovering any of those is broken while holding a brick
is a bad time.

### The EDL backup

Script: [`../scripts/backup-edl.sh`](../scripts/backup-edl.sh), wrapping
**bkerler's edlclient** — the same tool the community guides for this exact
device use ([aimindseye/hisense-a9](https://github.com/aimindseye/hisense-a9)).

The whole thing is two commands:

```bash
# backup
edl rl <dir> --memory=ufs --loader=prog_firehose_ddr_001360E1.elf --skip=userdata --genxml
# restore
edl wl <dir> --memory=ufs --loader=prog_firehose_ddr_001360E1.elf
```

`--genxml` is what makes this the answer to "a backup I can flash from EDL":
it writes the rawprogram XML alongside the images, so the dump is
self-describing and restorable with `edl wl` or the rawprogram XML via
`edl qfil` (what `scripts/restore-factory-gpt-lun4.sh` does).

```bash
cd ~/A9/scripts

./backup-edl.sh gpt      # connection test — prints the partition table, writes nothing
./backup-edl.sh          # full backup, skipping userdata
./backup-edl.sh full     # includes userdata — 228 GiB, rarely what you want
```

**Start with `gpt`.** It loads the programmer and reads the partition table
without writing anything — a clean proof that the device enters 9008, that
passthrough works, and that this handset accepts this loader.

Each backup drops a `RESTORE.md` next to the images with the exact restore
command for that dump.

> A **lun6 error during restore is expected and should be ignored** — the
> device reports six LUNs but only 0–5 carry partitions. Documented upstream.

### Use the `_001360E1` loader

The firmware package ships two firehose loaders, `prog_firehose_ddr.elf` and
`prog_firehose_ddr_001360E1.elf`. They are the same size (638,972 bytes) but
are **not identical** — they diverge from byte 4161. Every community guide for
this device uses the `_001360E1` variant, and so do these scripts. The same
applies to `rawprogram*_001360E1.xml` vs the plain ones when restoring stock
(`rawprogram0` and `rawprogram4` differ between the two sets; `rawprogram5` is
identical).

### Entering EDL on this device

Per the community guide, the reliable sequence is: **reboot with the EDL cable
unplugged, then plug the cable in while holding Power + Volume Up + Volume
Down + the button on the EDL cable.** The host should enumerate USB
`05c6:9008` (Qualcomm QDLoader 9008; check with `lsusb`).

`adb reboot edl` is **verified working** while Android (or TWRP) boots and is
the normal route. The cable method is the one that works when nothing else
does — which is the case that matters.

### USB access (native)

The NixOS VM owns the host's Alder Lake xHCI via PCI passthrough, so the phone
is visible in every mode (Android, recovery, bootloader fastboot, EDL
05c6:9008) with no per-session attach step. udev access for 9008 comes from
`~/host-config/a9-usb.nix`, which also installs `edl` (nixpkgs; it finds
libusb itself). `scripts/edl` is a thin wrapper that adds `--loader=$LOADER`
and `--memory=ufs` (forgetting `--memory=ufs` makes edl assume eMMC and read
garbage from this 4096-byte-sector UFS). `scripts/edl-cli.sh` is the
higher-level front end.

> **Legacy (Windows, retired 2026-10):** edl ran in WSL2 with the 9008 device
> passed through using `usbipd bind` / `usbipd attach --wsl` plus
> `modprobe vhci-hcd`, because bkerler's edl on Windows needs Zadig to swap
> the QDLoader driver for libusb, which breaks QFIL/QPST. Also needed then: a
> `nix-shell -p libusb1 gcc` wrapper with `LD_LIBRARY_PATH` because pyusb
> reported `backend: None` on NixOS. None of this applies any more.

## Tier 3 — QCN modem backup

Separate from `modemst*`, and the more reliable route for IMEI restoration.
The `modemst1`/`modemst2`/`fsg`/`fsc` images from Tier 2 or the EDL dump are
the practical IMEI/NV backstop on Linux.

> **Legacy (Windows, retired 2026-10):** a `.qcn` was written with QPST 2.7.496
> (Software Download → Backup, device in diag mode), with
> `QualcommIMEIRebuilder/` (deleted from $A9_DATA 2026-10-10; on the Windows PC) and an existing `QCN/` folder (now `$A9_DATA/nv/`) as helpers. There is
> no native Linux equivalent set up; any existing `.qcn` files remain usable
> if Windows tooling is ever revived. Keep them next to the partition backups.

## Tier 4 — Full EDL readback (optional)

The gold standard, because it works even when the device won't boot at all.
`scripts/backup-edl.sh` (edlclient with the `_001360E1` loader) reads every
partition back.

Slow, and largely redundant with Tier 2 — but it captures the bootloader
chain and GPT exactly as they currently are. The existing dump at
`Stock/Backup-2023-09-11-173822` (deleted from $A9_DATA 2026-10-10; original on the Windows PC) predates your GSI install, so it does not
reflect the current device.

---

## Verify the backup

An unverified backup is not a backup.

1. Check `manifest.txt` — no `FAILED` lines, and sizes match the expected
   partition sizes (`persist` 33554432, `metadata` 16777216, `boot_*`
   100663296, `recovery_*` 100663296, `super` 10737418240).
2. Confirm the sizes and SHA-256 on the NFS share match what the script
   reported (`ls -l` / `sha256sum` against `manifest.txt`).
3. Copy the whole `Backups/<timestamp>/` folder somewhere off this machine —
   a second drive or cloud. A backup on the same disk as the original is one
   failure away from nothing.

---

# Restoring

## The one rule that matters

**`userdata`, `metadata`, and `keystore` are a single set.** Restore all three
together, or none of them.

`/data` is encrypted with keys derived from material in `keystore`, against
metadata stored in `metadata`. Mixing a fresh `/data` with an old `metadata`,
or an old `keystore` with a fresh `/data`, produces a partition the device
cannot mount and cannot repair. If `/data` has been wiped, leave all three
alone and restore your files from the Tier 1 app-level backup instead.

Two smaller groupings follow the same logic:

- **`modemst1` + `modemst2` + `fsg` + `fsc`** — the modem NV set. Restore
  together.
- **`persist`** — standalone and always safe to restore to this handset.

## Which situation are you in?

### A. Recovery doesn't boot, Android still does

The expected Phase 3 failure, and the cheapest. `recovery_a` is not in
Android's boot path, so nothing else is affected.

```bash
adb reboot bootloader
fastboot flash recovery_a "$A9_FW/recovery.img"   # A9_FW=$A9_DATA/HLTE556N
fastboot reboot
```

### B. Bootloop after flashing, fastboot still reachable

Get to fastboot with **Volume Down + Power** held ~10 s, releasing Power at
the Hisense logo, then pick "Enter fastboot".

```bash
fastboot getvar current-slot          # know which slot you are on
fastboot flash boot_a  Backups/<ts>/boot_a.img
fastboot flash dtbo_a  Backups/<ts>/dtbo_a.img
fastboot reboot
```

Slot suffixes are explicit on this device. `fastboot flash boot` targets only
the active slot — name `boot_a` / `boot_b` directly when you mean a specific
one.

If it still won't boot, restore `super` (10 GiB, slow but reliable):

```bash
fastboot flash super Backups/<ts>/super.img
```

### C. Hard brick — no boot, no fastboot

EDL. This is what the Tier 1 images exist for.

Entry: `adb reboot edl` if anything still responds, otherwise the EDL cable /
test point. Device shows up as a Qualcomm 9008 serial port.

1. **Full stock restore first.** Use `scripts/restore-factory-gpt-lun4.sh
   --write` (GPT) and `scripts/flash-stock-super.sh --write` (super), both via
   edlclient with the `_001360E1` loader and the `rawprogram*_001360E1.xml` +
   `patch*.xml` set from `$A9_FW` (`$A9_DATA/HLTE556N`). For a state that was
   known-good, `scripts/restore-golden.sh --write` is the lighter option. This
   rebuilds the GPT and partitions from the factory package — and wipes
   everything. (Legacy, Windows: this was done with QFIL.)
2. **Overlay your device-unique images.** This is the step that makes the
   phone *yours* again rather than a generic A9:

   ```
   scripts/edl w persist   Backups/<ts>/persist.img
   scripts/edl w modemst1  Backups/<ts>/modemst1.img
   scripts/edl w modemst2  Backups/<ts>/modemst2.img
   scripts/edl w fsg       Backups/<ts>/fsg.img
   scripts/edl w fsc       Backups/<ts>/fsc.img
   scripts/edl w devinfo   Backups/<ts>/devinfo.img
   scripts/edl w apdp      Backups/<ts>/apdp.img
   scripts/edl w dip       Backups/<ts>/dip.img
   scripts/edl w secdata   Backups/<ts>/secdata.img
   scripts/edl w prospecfg Backups/<ts>/prospecfg.img
   scripts/edl w tpf_a     Backups/<ts>/tpf_a.img
   scripts/edl w tpf_b     Backups/<ts>/tpf_b.img
   ```

   Skip `keystore` and `metadata` here — `/data` was just wiped, so per the
   rule above they no longer match anything.
3. **IMEI still wrong?** `modemst*` usually covers it (step 2). A `.qcn`
   restore via QPST was the Windows-era backstop (see Tier 3 legacy note).
4. **Boot stock once** and confirm radio, sensors, and touch work before
   layering anything else on. If something is broken it is much easier to
   diagnose now than after the GSI is back.
5. **Re-flash the GSI** from the GSI copies on the Windows PC (`14/` was deleted from $A9_DATA 2026-10-10), re-apply Magisk from `$A9_DATA/root/`.
6. **Restore app data** from Neo Backup.

### inkOS recovery menu (it's in Chinese)

Two screens. Volume keys navigate, power selects.

**Screen 1 — 您已启动Recovery模式** ("You have booted Recovery mode")

| Button | Meaning |
|---|---|
| 进入Recovery模式 | Enter Recovery mode |
| **正常启动系统** | **Boot system normally** ← use this to get back to Android |

**Screen 2 — 您当前处于Recovery模式** ("You are currently in Recovery mode")

| Button | Meaning |
|---|---|
| 重新启动 | Reboot |
| **擦除用户数据** | **Erase user data** — factory reset, wipes `/data` |
| *(greyed)* | Update, needs a package |

Body text: update via Settings → System Update; long-press power to shut down
from this mode.

Note this screen renders crisply on the E Ink panel in portrait — recovery
drives the display properly without Android running, which is a good sign for
custom recovery rendering.

### D. Everything works, you just lost your apps

Neo Backup → restore from the SD card copy. Reinstall Magisk modules by hand.
Re-enrol 2FA from your seeds.

## Verifying a restore

After any restore involving `persist` or the modem set, check:

```bash
adb shell getprop | grep -i imei     # or dial *#06#
adb shell dumpsys sensorservice | head -30
```

Touch responsiveness, auto-brightness, WiFi MAC (`Settings > About`), and
mobile data are the things a bad `persist` or `modemst*` restore breaks.
Confirm them before you consider the device recovered.
