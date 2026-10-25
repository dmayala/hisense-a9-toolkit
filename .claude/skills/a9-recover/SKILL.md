---
name: a9-recover
description: Recovery ladder for the Hisense A9 (HLTE556N). Use when the phone is stuck, bootlooping, blank, or not booting after a flash; to "get back to a working state" or restore golden / recover the A9; and before and after any automated flashing of test firmware. A decision tree from cheapest to heaviest rung, no human needed except two named exits.
---

# a9-recover: recovery ladder

Goal: from ANY state, get back to **main**: the promoted LineageOS 23.2 (A16 GSI) build on slot **a** (active; slot b is the TEST slot since 2026-10-11, `docs/slots-and-promotion.md`), TWRP 3.7.1 on both recovery slots, set up, `a9-health.sh` exit 0.
Facts live in `~/A9/CLAUDE.md` (Safety rules 1-12), skills `a9-device`, `a9-edl`, `a9-twrp`, `a9-backup`, `a9-los-setup`. Scripts are in `~/A9/scripts/`.

**Step 0 of EVERY pass: run `scripts/a9-health.sh` (add `--json` to parse) and branch on `mode`.** Exit 0 = Android booted, 1 = other reachable mode, 2 = absent. (If it is ever missing, fall back to `adb devices`, `fastboot devices`, `lsusb -d 05c6:9008`, `scripts/twrp.sh status`.) Re-run it after every rung.

## Success = all of
`a9-health.sh` exit 0, `boot_completed=1`, slot suffix `_a`, provisioned (`los-skip-setup.sh status`), display awake. Anything less: keep climbing.

## Decision table

| mode | action | success check | on failure |
|---|---|---|---|
| android, healthy | nothing | exit 0, slot a (slot b only while a test is running) | n/a |
| android, not provisioned / AOD off | R1 `los-skip-setup.sh`, `a9-aod-setup.sh` | provisioned | R2 |
| android, broken (wrong build, stuck UI, bad test image) | R2 `twrp.sh enter`, then R4 if /data bad, else R5 | health 0 | R5 |
| recovery (TWRP) | R2: `twrp.sh exit` first (clears BCB, boots Android). Only if that bootloops: R4 (`twrp format data`). R5 if the system image itself is wrong | boots to Android | R4, then R5 |
| bootloader fastboot (`is-userspace: no`) | R3: verify slot a, `set_active a`, reboot | Android or TWRP appears | R5 |
| fastbootd (`is-userspace: yes`) | `fastboot reboot bootloader`, then bootloader row | mode=bootloader | R5 |
| edl (9008) | R5 immediately (do not leave it idle) | health 0 | R6, then human |
| diag / usb-only | `adb reboot edl` impossible; wait 60 s once, re-run health | mode changes | human (exit B) |
| absent | wait 60 s, re-run health twice; check `lsusb` | any mode | human (exit B) |
| bootloops (any) | go straight to R5; never wait out retries (see Traps) | health 0 | R4 then R6 |

## Rungs

**R1: runtime fix-ups (seconds).**
`scripts/los-skip-setup.sh` then `scripts/a9-aod-setup.sh`. Bound: 60 s each. Success: `los-skip-setup.sh status` shows provisioned, AOD settings applied. Needs booted Android with adb.

**R2: TWRP (about 1 min).**
`scripts/twrp.sh enter` (Android to TWRP, ~16 s, timeout 180 s; it waits for a STABLE adb connection, because TWRP re-enumerates USB once right after adbd starts). Check `adb shell getprop ro.build.version.release`/`twrp` responds. TWRP does NOT clear the BCB, so leave ONLY via `scripts/twrp.sh exit` (zeroes bytes 0-2047 of misc, reboots, waits up to ~250 s). If `exit` warns "back in recovery", run `twrp.sh bcb` and `exit` again once. Never `fastboot erase misc` (rule 8). Never unmount /data (rule 5).

**R3: bootloader fastboot (minutes).**
```
fastboot devices
fastboot getvar is-userspace          # must print "no"; if "yes" -> fastboot reboot bootloader first
fastboot getvar current-slot          # must be a (b only during a test); if b -> fastboot set_active a
fastboot reboot                       # then a9-health.sh, bounded 5 min
```
Recovery re-flash (only if TWRP is gone/stock; stock recovery shows `unauthorized`, TWRP shows authorized): from bootloader fastboot ONLY, flash BOTH `recovery_a` and `recovery_b` (rules 3-4), then `fastboot reboot recovery` or `adb reboot recovery` and confirm TWRP is authorized. Fastbootd writes nothing silently. `fastboot boot` does not exist (rule 2). Never `fastboot -w`/`format userdata` (rule 10).

**R3b: boot the other slot (minutes).** From bootloader fastboot:
```
fastboot set_active b; fastboot reboot     # if A (main) is broken; or set_active a if a test on B is broken
```
Works only if `/data` is compatible with that slot's build (same platform key; `docs/slots-and-promotion.md`). Otherwise the other slot bootloops too: R4 or re-flash. The bootloader also falls back by itself after repeated failures: `a9-health.sh` showing the unexpected slot means the expected one failed.

**R4: wipe /data and re-provision (about 3-5 min). STOP FIRST: since 2026-10-13 the phone is the user's daily phone. Never run R4 without the user's explicit OK in this conversation; ask, with what will be lost. Prefer returning to a slot whose build matches /data (R3) or R5 without a wipe.**
Use when the golden or flashed system boots badly because of /data (bootloop, "cannot decrypt", stale test-build data):
```
scripts/twrp.sh enter
adb shell twrp format data            # THE only /data wipe (f2fs + /metadata); never fastboot -w
scripts/twrp.sh exit                  # clears BCB, boots Android
# wait for boot_completed=1 (bound 5 min), then:
scripts/los-skip-setup.sh             # GApps build: finish Google's wizard on the panel first, then `los-skip-setup.sh root`
scripts/magisk-env.sh --apply         # only if `su` is "inaccessible or not found" (empty /data/adb/magisk after the wipe)
scripts/los-skip-setup.sh root        # full Magisk manager + shell root grant
scripts/a9-aod-setup.sh
scripts/epd-trace.sh                  # frames reach the panel, mDebugDisableHWC=1
scripts/a9-health.sh                  # exit 0, slot a
```
TWRP traps (`docs/twrp-port-findings.md`, Known issues): never `restorecon` app data under `/data/data` from TWRP (mislabels it); fix labels with `chcon`.
Needs a debug-props image so adb is authorised after the wipe (golden is). Bisecting images: wipe /data between every image.

**Before R4/R5 on a bootloop: read why it failed.** `kdebuginfo` is not encrypted, so from TWRP (`scripts/twrp.sh enter`, or Vol Down + Power → Recovery) `scripts/a9-bootlog.sh list` and `pull boot-NNNNN` give the failed boot's kernel log, bootloader log and logcat even while `/data` stays locked. Then `twrp.sh exit`.

**R5: golden restore over EDL (about 7 min, super is 10 GiB). Proven on hardware 2026-10-10.**
Check first (no phone contact, ~40 s): `scripts/restore-golden.sh` verifies sizes, SHA-256, and that misc-clean has no BCB. Then:
```
scripts/restore-golden.sh --write --enter     # phone in Android/TWRP: does adb reboot edl itself
scripts/restore-golden.sh --write             # phone ALREADY in EDL (9008)
```
Writes super, boot, dtbo, vbmeta, vbmeta_system, recovery (both slots) and misc-clean, then `edl reset`, then waits for Android and runs `los-skip-setup.sh status`. Bound the whole thing at 25 min. Golden dump: `$A9_DATA/Backups/edl_2026-10-14_110759` (taken 2026-10-14: both slots v7 Doze-off GAPPS + Magisk 30.7, active slot a, userdata not included; previous v6 golden `edl_2026-10-11_132444` kept). After restoring, `/data` is compatible if it is still crDroid-keyed (platform key `a82af580…`), so no wipe is needed; R4 (`twrp format data`) only if /data was changed to another key meanwhile.
- Treat a failure as a script/EDL problem, not a reason to improvise partition writes.
- If it fails midway: the phone STAYS in EDL ("Do NOT reset into a half-written state"). Read `runs/<UTC>-restore-golden/<part>.log` (one dir per run, never overwritten), then immediately re-run `restore-golden.sh --write` (no `--enter`). Re-running rewrites everything, so it is idempotent. Up to 3 attempts.
- Sahara hello is sent once at enumeration. Have `scripts/edl` ready and start the write immediately after entering EDL. Do not poke the phone with extra probes, and do not leave it in EDL across failed attempts: a phone that sits in EDL after repeated failures wedges, and only a human holding Power 10-15 s frees it (exit A).
- If it ends "Android did not finish booting" (bootloop): R4.
- From bootloader fastboot there is no route into EDL (`fastboot oem edl` does not exist in the ABL, checked 2026-10-13). `adb reboot edl` works from Android and TWRP only. From bootloader, try R3 to reach Android/TWRP first.

**R6: last resorts (heavy; use only after R5 failed 3 times or the golden dump itself is suspect).**
1. Full `edl wl` of the whole dump per `RESTORE.md` in the dump dir. It rewrites the bootloader chain, persist and modem NV, so it is last-resort only (rule 6). `scripts/edl` refuses `wl` unless `A9_EDL_ALLOW_WHOLE=1` is set for that one command (write guard, 2026-10-13); the dump holds the phone's own boot chain and devinfo, so this does not conflict with rule 13.
2. Stock factory backstop: `scripts/restore-factory-gpt-lun4.sh` (check mode first) and `scripts/flash-stock-super.sh` from `$A9_FW` (`$A9_DATA/HLTE556N`). `$A9_FW` is complete (`a9-assets.sh status`: stock-rom present, 141 files, 2026-10-13). Run the check-only modes first; if they report missing/short files, do not write, go to exit A. **Never write the package's xbl/abl/tz or `devinfo`/`diag`** (CLAUDE.md rule 13): the phone's boot chain is newer than the package. Factory GPT has all-zero slot attributes, so afterwards `fastboot set_active` is mandatory, and this path changes the system (GSI is lost; re-flash it, then re-run R5 to return to golden).
Never hand-patch the GPT (rule 7).

## Flash test firmware safely (checklist)
Scripted since 2026-10-13: `scripts/flash-test-b.sh <system.img> [--boot <boot.img>]` runs steps 1-5 as checks (default) and with `--write` flashes slot B, reboots, verifies by readback (`a9-readback.sh`) and switches back to A on its own if B does not boot; `--back` returns to A after a pass. It refuses any image `a9-compat.sh` says needs a /data wipe. Promotion: `scripts/promote-b-to-a.sh <img>`. Prefer the scripts; the manual steps below are what they do.
1. `a9-health.sh`: must be exit 0, slot a (main). If not, climb the ladder first.
2. Confirm the golden dump verifies: `scripts/restore-golden.sh` (check mode) passes. Do not flash without a working net (rule 1).
3. Flash ONLY slot b (the test slot) and only the partitions the test intends (e.g. `system_b` from fastbootd, `boot_b` from bootloader). Never flash untested firmware to `_a` (slot A is main; it only receives promotions, `docs/slots-and-promotion.md`). Do not touch GPT, misc, persist, modem partitions, xbl/abl. Flash recovery only from bootloader fastboot, both slots.
4. Before any reboot out of the bootloader during the test: `fastboot getvar current-slot` = b (`fastboot set_active b`). After the test: `set_active a`, unless a key-changing wipe made /data incompatible with A (then promote or re-flash A).
5. Reboot, then poll `a9-health.sh` every 10 s up to **5 min** for `boot_completed=1` (exit 0). TWRP/fastboot appearing instead counts as "not booted".
6. Not up at 5 min, or any bootloop sign: run the ladder, starting at R5 (do not retry the same image repeatedly).
7. After success, if /data was touched or wiped: R4 tail (`los-skip-setup.sh`, `a9-aod-setup.sh`), final `a9-health.sh`.

## Traps and hard rules (CLAUDE.md "Safety rules")
- **Slot A is main, slot B is test** (since 2026-10-11). Do not run `abort-to-slot-a.sh`: it re-flashes `recovery_b` with stock recovery and removes TWRP. Plain-reboot from the bootloader only after checking `current-slot`.
- `/data` is shared: a build with a different platform key needs `twrp format data`, which strands the other slot until it gets a compatible build. Check before switching slots (`scripts/a9-compat.sh <img>`). Since 2026-10-13 such builds are off the table: no wipes without the user's OK.
- Rule 3: recovery flashes from fastbootd write nothing, silently. Rule 4: flash both recovery slots. Rule 2: no `fastboot boot`.
- Rule 5: never unmount /data in TWRP. Rule 10: `twrp format data` only, never `fastboot -w`/`format userdata`.
- Rule 8: never `fastboot erase misc`; restore misc only from `misc-clean.bin`, never the raw `misc.bin` (stale BCB). TWRP leaves the BCB set: always leave via `twrp.sh exit`.
- Rule 6: never touch modemst1/2, fsg, persist. Rule 7: never hand-patch GPT. Rule 11: never restart SurfaceFlinger (blank panel until reboot; a reboot fixes it).
- EDL: the Sahara hello fires once at enumeration; have `edl` ready. Never leave the phone idle in EDL.
- Run one USB-touching command at a time; no parallel adb/fastboot/edl calls.
- After any /data wipe, re-run `a9-aod-setup.sh` (vndk.rc's one-shot resets doze).

## When to stop and ask a human (only these two)
- **A. Phone wedged in EDL**: EDL entered/left badly or left idle across failed attempts, and `a9-health.sh` keeps reporting edl/usb-only/absent after a re-run. Tell the human: "The A9 is wedged in EDL. Please hold Power for 10-15 s until it reboots (unplug USB if needed), then tell me." Include the last `a9-health.sh --json`, which rung and attempt failed, and the failing `runs/*-restore-golden/<part>.log` tail.
- **B. Total USB loss**: `lsusb` shows no phone (not 05c6:9008, not adb/fastboot IDs) for 2+ minutes and two health runs, with the host USB controller VM passthrough still present. Tell the human: "No A9 on USB at all. Please check the cable and battery, and try Power 10-15 s, then plug in." Include the health JSON and the last successful step.
- A failed R6 stock path or missing `$A9_FW` files is reported as part of A with the same detail, not as a third exit. Do not ask for anything else; keep climbing the ladder.
