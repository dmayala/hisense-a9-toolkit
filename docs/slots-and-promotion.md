# Slots: A is main, B is test — and how a build gets promoted

**Date:** 2026-10-11
**Status:** In force. Supersedes "slot B is golden, slot A is the fallback copy"
(2026-10-10).

## Roles

| Slot | Role | What runs there |
|---|---|---|
| **A** | **Main / daily driver, active.** Only builds that passed on B. | the user's phone |
| **B** | **Test.** Every new image goes here first. | whatever is under test |

`/data` (and `/metadata`) are **shared** by both slots. That is the one thing
that couples them; see "Platform keys" below.

## Test a build on B

**Scripted (2026-10-13):** `scripts/flash-test-b.sh <img> [--boot <boot.img>] [--write] [--back]`
does steps 1-5 with checks and readback; `scripts/promote-b-to-a.sh <img> [--write]` does the
promotion below. Since 2026-10-13 the phone is in daily use, so step 4 (a key-changing wipe) is
refused by `a9-compat.sh`. The manual steps stay here as the reference for what the scripts do.

1. `scripts/a9-health.sh` (Android, slot `_a`).
2. `adb reboot bootloader; fastboot set_active b`
3. `fastboot reboot fastboot` → `fastboot getvar is-userspace` = `yes`,
   `current-slot` = `b` → `fastboot flash system <image>` (= `system_b`).
   `boot_b` only if the test is about boot (it is Magisk-patched; see
   [root-magisk.md](root-magisk.md)).
4. If the image's platform key differs from the one that set up `/data`:
   `fastboot reboot recovery`, wait for a **stable** TWRP
   (`a9_wait_stable recovery` in `a9-env.sh`; TWRP re-enumerates USB once),
   `adb shell twrp format data`, `adb reboot`. Never `fastboot -w` (CLAUDE.md
   rule 10).
5. Boot, `a9-health.sh`, then the checks that matter for E Ink:
   `scripts/epd-trace.sh` (frames reach the panel, `mDebugDisableHWC=1`),
   `scripts/epd-snap.sh` (what the glass shows).
6. After a wipe: Google's setup wizard (GApps builds) on the panel, or
   `SKIP_GOOGLE=1 scripts/los-skip-setup.sh`; then `scripts/magisk-env.sh --apply`
   if `su` is missing, then `scripts/los-skip-setup.sh root` (it also clears a
   stored Magisk deny for the shell). Check the A9 Accessibility Service:
   `adb shell settings get secure enabled_accessibility_services` must list
   `com.lmqr.ha9_comp_service/.A9AccessibilityService`. On 2026-10-11 it read
   `null` once after the first slot-A boot (cause not reproducible); images
   built after `a16-*-hwc-guard` 2026-10-11 re-check it for 3 minutes at boot and
   log `a9-firstboot: ... re-enabling` to `dmesg` if they had to.

Back to main: `fastboot set_active a` — **only if `/data` is still compatible
with A's build** (same platform key). After a key-changing wipe, A cannot use
`/data` until it is promoted.

## Promote B → A

Slot A's firmware, `vendor`, `dtbo`, `vbmeta` are byte copies of B's (the
2026-10-10 `sync-slot-a.sh` run; re-verified 2026-10-11: `dtbo_a`==`dtbo_b`,
`vbmeta_a`==`vbmeta_b`). So promotion is two partitions:

```sh
adb reboot bootloader
fastboot set_active a
fastboot flash boot_a <the boot image B runs>      # bootloader fastboot
fastboot reboot fastboot                           # fastbootd, current-slot a
fastboot flash system <the image B passed with>    # = system_a
fastboot reboot                                    # then a9-health: slot _a
```

If A fails to boot, the bootloader falls back to B after its retries; B is
known good at that point. `sync-slot-a.sh` (whole `super` + per-slot firmware
over EDL) is only needed if firmware/vendor ever change.

## `super` space (MEASURED 2026-10-11)

`super` is 10,737,418,240 B and holds system, system_ext, product and vendor of
**both** slots. Per slot today: vendor 1,154,674,688 + product 190,722,048 +
system_ext 413,605,888 = 1,759,002,624.

| system on A / B | free |
|---|---|
| vanilla 3,145,728,000 / vanilla 3,145,728,000 | 927,956,992 |
| vanilla / GApps unshrunk 3,636,461,568 | 437,223,424 |
| GApps shrunk 3,569,737,728 on both | **≈ 80 MB** |

So a GApps image must be shrunk to its filesystem minimum (`finalize-image.sh`
does this since 2026-10-11 for any image above 3000M), or promotion fails for
lack of space. If a future base is bigger still, the test slot has to carry a
smaller system (or nothing) while A gets the big one.

## Platform keys

| Base | Platform key | Patcher branch |
|---|---|---|
| mochibuilds LOS 23.2 (testkey) | AOSP testkey `c8a2e9bc…` | tag `archive/a16-hwc-guard` (branch deleted 2026-10-14) |
| Doze-off LOS 23.2 GAPPS/VANILLA | crDroid `a82af580…` | `a16-port` (main line) |

Moving between these needs `twrp format data` (shared `/data`).
