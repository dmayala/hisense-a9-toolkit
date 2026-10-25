# Golden TWRP backup: back up and restore WITH your data

**Status (2026-10-14): PROVEN on the phone.** `golden-20261014` was taken with
`scripts/twrp-backup.sh` and restored straight away with `scripts/twrp-restore.sh
--write`: Android booted and packages (254, versions), user apps (22), accounts,
home app and app-data counts all matched the backup's reference. User check of
signed-in apps: see the end of this file.

**2026-10-14/15: PROVEN ACROSS A FORMAT.** `pre-format-20261014` was taken, then
`twrp format data` (new FBE and metadata keys, internal storage erased), one boot
into Android on the empty `/data` (setup wizard pending: that boot creates the new
keys), then `twrp-restore.sh pre-format-20261014 --write --storage`. Android booted
provisioned; packages, user apps, accounts, home app and app-data counts all matched;
Magisk root and modules (a9-bootlog, zygisk_vector) back. Restore 245 s.

This complements the EDL golden (`edl_2026-10-14_110759`), which excludes userdata.
EDL golden = firmware/system state, no data; TWRP golden = your data on the same
ROM line.

## Use

```sh
scripts/twrp-backup.sh [name] [--no-storage] [--stay]   # Android -> TWRP -> backup -> NAS -> Android
scripts/twrp-restore.sh <name>                          # check only
scripts/twrp-restore.sh <name> --write [--storage]      # restore Data + Boot, compare
```

Backups land in `$A9_BACKUPS/twrp/<name>/`: `twrp/` (TWRP's archives + `.sha2`
digests), `raw/metadata.img` + `raw/misc.img`, `internal/` (internal storage,
which TWRP's Data backup excludes), `reference/` (build, platform key, packages,
accounts with names redacted, home app, app-data counts), `manifest.txt`.
golden-20261014: 2.4 GB (Data 4 archives + Boot), backup 289 s, restore 193 s.

## Rules (why it works, and when it would not)

- **Same phone, same ROM line (platform key).** `twrp-restore.sh` refuses a
  platform-key mismatch (with root via mac_permissions, without root via the
  framework-res cert).
- **After a format, boot Android once before restoring.** On FBE, TWRP leaves the
  key material out of a Data backup on purpose (`partition.cpp` `Decrypt_FBE_DE`:
  `unencrypted/`, `misc/vold/user_keys`, `misc/keystore`, gatekeeper, `locksettings.db`,
  `system_de/0/spblob`, `drm/kek.dat`), so the restore lands on the keys that first
  boot created. metadata/misc are kept raw for the record but NEVER written (the
  new metadata key must stay).
- **What a restore across a format cannot bring back:** anything sealed in the
  Android keystore (excluded above): app logins/tokens kept there may need signing
  in again. User check: see the end of this file.
- A restore returns the phone to the backup's moment; newer data is lost.
- No lock-screen PIN was set (TWRP decrypts unattended). With a PIN, TWRP needs it.

## Traps found while building it

- The OpenRecoveryScript `backup`/`restore` letters cover only S D C R B A E
  (+O compression, M skip digest): metadata/misc cannot be selected
  (`openrecoveryscript.cpp`), hence the raw copies.
- **Internal storage goes back from Android via `/sdcard`**, per entry, skipping
  MediaProvider's caches (`.transforms`, `.picker_transcoded`): adb cannot create
  them through FUSE ("Machine is not on the network") and one failure aborted the
  whole-folder push on the first run.
- **TWRP starts MTP a few seconds after it decrypts /data, which re-enumerates USB
  and drops adb.** The first attempt's `twrp backup` hit "device not found". The
  scripts wait for `MTP Enabled` in `/tmp/recovery.log`, then for stable adb, and
  retry a TWRP command once if adb dropped before it started.

## Evidence

Runs: `runs/20261014T215643Z-twrp-backup`, `runs/20261014T220513Z-twrp-restore`;
across a format: `runs/20261015T000640Z-twrp-backup`, `runs/20261015T001749Z-twrp-restore`
(after/ comparison files, health-after.json).

## User check after the restore across a format (2026-10-15)

Google Play sign-in survived, and the user reported everything else intact too
(apps, logins, settings). The keystore exclusion cost nothing visible here.
