# TWRP for Hisense A9 (HLTE556N) — release readiness

**Status: functionally complete, reproducible, documented.** Updated
2026-08-11.

Goal: a TWRP build that ordinary A9 owners can flash and use, with no adb
scripting, no hand-patched images, and no tribal knowledge.

All functional blockers are closed, everything ships from the device tree (B0
is done and byte-verified), and the README is written for release.

**The only open item is testing on a second device**, which may never happen —
this is a niche phone and one unit is all there is. That is a caveat to state
in the release notes, not a blocker to hold the release for. Everything here is
verified; it is verified *once*, on one A9.

---

## Ships today (verified on-device)

| Capability | Evidence |
|---|---|
| Boots on both recovery slots | repeatedly |
| **E Ink display, full 824x1648** | photographed; `Using hmct epd graphics.` |
| Touch input | confirmed by user |
| Root adb shell | `uid=0`, `u:r:su:s0` |
| `twrp` CLI (backup/restore/install/sideload) | `/system/bin/twrp` |
| Raw partition imaging | byte-exact, size-verified |
| `Wipe → Format Data` | works (no decryption needed) |
| Panel never blanks | `enabled`/200 held past 96s |
| **Reproducible from source** | build `31428884226` byte-matches the proven image |
| **MTP two-way transfer** | `18d1:4ee2`; md5-verified pull, verified push |
| **Returns to Android cleanly** | no sticky BCB; verified from both recovery and fastbootd |
| **`/data` decrypts and mounts** | 223G mounted; 289 dirs in `/data/data`; `twrp.all.users.decrypted=true` |
| **fastbootd entry + slot resolution** | `is-userspace yes`, `slot-count 2`, `current-slot a`, `is-logical:system_a yes`, `partition-size:system_a 0xFEB59000` |

## Release blockers

### B0 — port into the device tree  ✅ DONE AND VERIFIED
Committed as `42220a1` "HLTE556N: working fastbootd and MTP", pushed to
`android-12.1`, built as **run `31428884226`**
(`recovery.img`, sha256 `eba744dd318b336924405e79ca237be113090973b4f3e56691d95bf217f44c3f`).

**The build reproduces the hand-patched image exactly.** Unpacked and diffed
against `twrp-HLTE556N-decrypt.img` (deleted from $A9_DATA 2026-10-10; original on the Windows PC):

- 3305 files in both; **zero** missing, **zero** extra
- all 8 ported files byte-identical
- only 4 content differences, all build stamps: `prop.default` (timestamps and
  `ro.build.version.incremental` only), `ramdisk-files.txt`,
  `ramdisk-files.sha256sum`, `system/bin/recovery`
- cmdline carries `androidboot.dynamic_partitions=true`
- `recovery dtbo size: 88722` — matches stock, so it will boot

Method worth reusing: unpack **both** images and diff the ramdisks by content
hash, rather than trusting the package list or the build log.

### B4 — MTP  ✅ DONE
Enumerates as `18d1:4ee2` (MTP+adb); Windows shows A9 → Internal Storage; files
transfer **both ways** with matching md5; adb stays up alongside it; survives a
cold boot. Root cause was three missing ramdisk pieces, not a TWRP fault — no
`ffs.mtp` function, no `/dev/usb-ffs/mtp` functionfs mount, and no configfs
handler for `mtp,adb` (the only handler wrote to the legacy
`/sys/class/android_usb` nodes, an inert stub on this device).

### B5 — the `fastboot flash` write path  ✅ DONE
Proven by a real 3.81 GB write: `Writing 'system_a' OKAY`, host resolving
`system` → `system_a` unaided. LineageOS 21 (Android 14, SDK 34) then booted
from it with `/data` intact and no wipe.

---

## Restore: what we actually observed  (NO general rule yet)

Restore failed in **two different ways** on this device, and only one is
explained. Two tempting rules were each written down and then disproved by the
next test — do not replace this table with a rule until more cases exist.

| # | system restored | `/data` | `misc` in backup | Extra | Result |
|---|---|---|---|---|---|
| 1 | Android 11, whole set | Android 11, same backup | no | — | **boot loop** |
| 2 | Android 11, whole set | same | no | **+ Format Data** | **boots** |
| 3 | Lineage 21, `super` only | left as Android 11 | n/a | — | **boot loop** |
| 4 | Lineage 21, whole set | Lineage 21, same backup | yes | — | **boots** |
| 5 | Android 11, whole set | Android 11, same backup | **yes** | — | **boot loop** |
| 6 | Android 11, whole set | same | yes | **+ Format Data** | **boots** |

**The observed pattern: restoring the stock Android 11 `/data` never boots,
with or without `misc`. Restoring the LineageOS 21 `/data` boots fine.
Format Data always fixes it.**

- #4 disproves "never restore `/data`".
- #1 disproves "always restore the whole set".
- #5 disproves "the missing `misc` explains it" — restored *with* `misc` and it
  still looped. Both backups' `misc` images are byte-identical in the BCB and
  Virtual A/B regions anyway.
- **Format Data is the only action that has worked across every failure.**

### What the bootloader does (measured, case 5)

`slot-retry-count:a` went 6 → **0** across the failing boots, so the bootloader
*is* attempting slot A and Android never marks it successful. But after
`fastboot set_active a` reset the budget to 7, the device still landed in
recovery having consumed only **one** try (7 → 6) — with 6 remaining. So the
bootloader is not choosing recovery through retry exhaustion; something wrote
`boot-recovery` into the BCB while retries were still available. The bootloader
reports the failure, it does not decide it.

Android gets at least as far as keystore (`/data/misc/keystore/.metadata` is
rewritten) but never far enough to start adbd, so no logcat is obtainable.

Note `Restore_Tar()` wipes `/data` before extracting, so a `data` restore does
not leave stale files behind — but skipping `data` (case 3) silently keeps the
*previous ROM's* `/data`.

### Case 3 — explained and measured

A system/data version mismatch. APEX data dirs are labelled by the *running*
platform's policy: under Android 11
`/data/misc/apexdata/com.android.tethering` is `apex_module_data_file`, but
Android 14 requires `apex_system_server_data_file`:

APEX data directories are labelled by the *running* platform's policy. Under
Android 11 `/data/misc/apexdata/com.android.tethering` is
`apex_module_data_file`; Android 14 requires `apex_system_server_data_file`:

```
/data/misc/apexdata/com\.android\.tethering(/.*)?  u:object_r:apex_system_server_data_file:s0
```

`NetworkStatsService.getOrCreateStatsDir()` then fails `canWrite()` and throws,
which kills `system_server`, which kills zygote — an endless restart loop:

```
java.lang.IllegalStateException: Cannot write into stats data directory:
    /data/misc/apexdata/com.android.tethering/netstats
E/Zygote: Exit zygote because system server (pid ...) has terminated
```

**TWRP's Advanced → Fix Contexts does NOT fix this.** It relabels from the
*recovery ramdisk's* `file_contexts`, which has no APEX-specific rules, so it
reapplies the generic `/data/misc/apexdata` rule and the wrong label survives.
Confirmed by inspecting the label after running it.

The only reliable repair, short of formatting, is to **delete** the offending
directory so `apexd` recreates it from the system's own policy. That fixed the
tethering crash — but the same mislabelling applies to every APEX that takes
`apex_system_server_data_file` (`permission`, `uwb`, `wifi`, `scheduling`), so
it is whack-a-mole. Format Data is the supported answer.

### Case 1 — UNEXPLAINED

A whole-set Android 11 restore that still would not boot. It failed
*differently* from case 3: no zygote crash loop. Android reached `system_server`
(it wrote `/data/misc/keystore/persistent.sqlite` and
`/data/system/locksettings.db`) and then requested recovery through the BCB —
`boot command: boot-recovery`, with no `--wipe_data` argument. Ruled out by
measurement: bootloader slot flags (`successful=1`, `unbootable=0`,
`tries=6`), AVB (disabled), `boot_a` (loop persisted with pristine stock
`boot.img`), Virtual A/B merge (`/metadata/ota/snapshots/` empty), and `misc`
(byte-identical to a known-good image).

Four hypotheses were tested and **all four are dead**. Recorded so they are not
retried:

1. *Brand mismatch* (Crosscall system vs Hisense vendor/data) — no; that backup
   came from a working Android 11, so the Crosscall-branded system is simply
   this device's ODM base.
2. *`/metadata` key mismatch* — no; **no TWRP backup contains `/metadata`**, so
   it is always regenerated relative to restored `/data`. If fatal, case 4 would
   also have failed.
3. *Missing `misc`* — no; case 5 restored *with* `misc` and still looped, and
   both backups' `misc` images are byte-identical in the BCB and Virtual A/B
   regions.
4. *Bootloader decides* — only partly; see the measurement above. It attempts
   and reports, but recovery was entered with retries still available.

Two more, tested against the backup tar itself (read-only, on the PC):

5. *Magisk / Xposed modules* — no. `/data/adb/` exists in the Android 11 backup
   but is **empty**; no magisk, xposed, lsposed, riru or zygisk entries at all.
6. *Missing CE (credential-encrypted) data* — no. TWRP excludes
   `/data/{system_ce,misc_ce,user,media}/<userId>` for users it cannot decrypt
   (`partition.cpp` ~2748), but both backups carry comparable CE content:
   Android 11 has 49 `system_ce` / 31 `misc_ce` / 988 `user_de` entries,
   Lineage 125 / 53 / 1026.

**CONCLUSION: unexplained, and deliberately left so.** The answer lives in
Android's log during the failed boot, and adb never starts on those cycles;
`pstore` and `last_kmsg` are empty and there is no serial console. Obtaining it
would require modifying the boot ramdisk to start adbd earlier or to dump logs
to a partition — disproportionate for a ROM-restore quirk that is not a TWRP
defect.

**Ship this as guidance instead:** *after restoring a stock Android 11 backup,
run Wipe → Format Data.* That is honest, actionable, and always works.

### Why this is nearly invisible

- The only symptom is a frozen boot animation. Nothing on screen indicates a
  crash loop.
- On this E Ink panel it is worse: the display driver logs
  `CheckImageIsBlack: epd:: is black image, not display`, so the boot animation
  never paints at all — the screen just sits there.
- `adb logcat` is the *only* way to see it. Diagnose with:
  `adb logcat -d -b crash | grep -E "FATAL|Exit zygote"`

## Backup and restore — the rule that matters

**Back up and restore `misc` together with `super` and `data`.** Verified
2026-08-10: restoring `super` without a matching `misc` leaves the device
unbootable, and the only recovery is Format Data — the backup cannot be
restored without destroying the data it existed to protect.

`misc` holds the Virtual A/B message at offset 32768 (magic `0x56740AB0`),
which carries snapshot/merge state. If that disagrees with the LP metadata in
the restored `super`, boot fails and `/data` will not decrypt.

Until `9eb5fbf`, `/misc` was the only fstab entry without `backup=1`, so TWRP
never offered it — meaning **every backup taken before that commit is not
restorable without a wipe.** Re-take backups on the new build.

Restoring `misc` has a second, milder trap: its first 2048 bytes are the BCB,
rewritten on every reboot-to-recovery, so a backup may carry a stale
`boot-recovery` and send the device back into recovery on the next boot. Fix by
zeroing only the first 2048 bytes, never the whole partition.

## Known limitations — must appear in the release notes

- **Do not unmount `/data` if you want decrypted access — reboot recovery.**
  fscrypt keys are held per-superblock and TWRP only installs them during
  startup decrypt, so unmount evicts them and the remount silently returns
  ciphertext. A backup taken after an unmount would contain encrypted junk with
  no error shown. Pre-existing TWRP behaviour, not introduced here.
- MTP may report a stale 0-byte size for a file created behind its back while
  it is running. Affects adb-shell-created test files, not normal use.
- `/vendor`, `/system`, `/system_ext`, `/product` mount **read-only**
  (`fsflags=ro`) — ext4 `shared_blocks` cannot mount rw.

---

## Solved — root causes worth keeping

- **Keymaster never registered** because `libkeymasterdeviceutils.so` calls
  `WaitForPropertyCreation("vendor.sys.listeners.registered")` with no timeout,
  *before* `registerAsService()`. That property comes from `/vendor/bin/qseecomd`,
  which TWRP never started. The `nanosleep` was that wait. Not SELinux, not HIDL
  versions, not a missing trustlet.
- **Hardware-wrapped keys were never the problem.** The FBE failure was a missing
  `fscrypt` session keyring (AOSP init creates it; TWRP's does not).
- **Sticky BCB, empty `Startup Commands`, ignored `--fastboot` were one bug:**
  `libfs_mgr` cannot parse TWRP-format `recovery.fstab`, so `ReadDefaultFstab()`
  failed and `get_args()` could neither read nor write the BCB. The theory that
  "TWRP reads argv, not the BCB" was wrong.
- **fastbootd fell back to bootloader** because `ro.boot.dynamic_partitions` is
  empty — the bootloader does not pass it on the recovery cmdline.
- **`slot-count 0`** because lazy HAL start always loses the race: fastbootd
  caches `IBootControl` in its constructor.

## Before publishing

- [x] B0 — ported, pushed (`42220a1`), built and **verified byte-for-byte**
- [x] B4 — MTP working, two-way transfer confirmed
- [x] B5 — `fastboot flash` write path confirmed (3.81 GB to `system_a`)
- [x] **Flashed `31428884226` to both recovery slots and re-verified on-device**
- [x] **`/data` decryption confirmed against LineageOS 21** — `twrp.all.users
      .decrypted=true`, 223 G mounted, 269 plaintext dirs in `/data/data`
      (`android`, `com.UCMobile`, …), `/data/media/0` browsable. Lineage's FBE
      policy needs no extra work.
- [x] **Backup + restore cycle through the GUI confirmed, with one caveat.**
      Backup works (two full sets, 8/8 files verified byte-perfect against their
      `.sha2`, including 10 GiB `super` each). **Raw-partition restore works**
      (`super`, `boot`, `misc`). **File-level `/data` restore does NOT** — see
      the section above; restore `super` and Format Data instead.
- [x] **`misc` confirmed in the Backup list on a built image (2026-08-11)** —
      not just the hand-patched one. Build `31434216263`, device-tree HEAD
      `4e09410`, checked on-device. This closes the trap that every backup taken
      before `9eb5fbf` is non-restorable without a wipe.
- [ ] Test on a **second device** if at all possible
- [x] **README rewritten for release (2026-08-11).** The old one was the
      pre-port skeleton and actively dangerous: it said decryption was expected
      to fail, said E Ink rendering was unproven, claimed the device kernel
      matched the package's (15,050,789), and told users to flash `recovery_a`
      only and prove it before touching `recovery_b`. Now carries the four
      flashing rules, the prebuilt re-extraction warning, the
      `libhmct_epd.so` provenance note, the forks needed to build (upstream
      minimal-manifest-twrp produces a non-rendering image), the pre-flash
      image verification steps, the restore policy below, and the known
      limitations.
- [x] **Restore policy decided (2026-08-11): `super` + `boot`, then Format
      Data.** File-level `/data` restore is not offered — case 1 stays
      unexplained and Format Data is the only action that has recovered every
      observed failure. `misc` needs no special handling *because* Format Data
      is the prescribed final step; the README explains why rather than adding
      a fourth partition to the procedure. The README states plainly that this
      protects the ROM and not the user's data, and to copy data off first.

## Repos

- device tree: `dmayala/android_device_hisense_HLTE556N` (`android-12.1`)
- recovery fork: `dmayala/android_bootable_recovery` (`android-12.1`)
- manifest fork: `dmayala/platform_manifest_twrp_aosp` (`twrp-12.1`)
- builder: `dmayala/Action-TWRP-Builder`

## Hard-won rules (full detail in the a9-twrp skill)

1. **Verify by unpacking the built image**, never by the package list. Four
   mechanisms failed to install one 11 KB library; only `recovery/root/` works.
2. **A build flag does nothing unless `vendor/twrp` exports it to Soong.**
   Silent no-op otherwise.
3. **"Stuck at splash" = startup stalled, not a rendering fault.** Check
   `ps -A | grep " recovery$"`: `futex_wait_queue_me` = blocked,
   `do_sys_poll` = healthy.
4. **Never ship an init service whose dependencies are missing** — the respawn
   loop starves the E Ink TCON and corrupts the display.
5. **Iterate by hand-patching the ramdisk (~3 min), not by building (~55 min).**
   But hand-patching is a diagnostic tool, not a deliverable — see B0.
6. Flash **both** recovery slots. Never `fastboot erase misc`.
7. **AOSP code paths need an AOSP-format fstab.** `libfs_mgr` cannot parse
   TWRP's. Anything reaching `ReadDefaultFstab()` — BCB reads and writes
   included — fails silently until `/etc/recovery.fstab` is AOSP-format.
8. **Lazy-started HALs lose races against callers that cache the service
   handle.** Start them at `on post-fs`, not on demand.
9. **Prefer one root cause to several plausible ones.** Three separate-looking
   fastbootd symptoms were one fstab bug; three "display faults" were one
   blocking `getService()`. Both times the multi-cause reading cost more than
   the fix.
10. **Builder `BUILD_TARGET` is `recovery`, not `recovery.img`.** The workflow
    runs `make ${BUILD_TARGET}image`, but its "Display Run Parameters" step
    echoes `${BUILD_TARGET}.img`. Reading the parameter back out of an old run's
    log therefore gives the wrong value and fails with
    `ninja: unknown target 'recovery.imgimage'`. Read the workflow YAML, not the
    log.
