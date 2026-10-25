---
name: a9-device
description: Core measured facts about the Hisense A9 (HLTE556N) — partition layout, boot image geometry, A/B and dynamic partition setup, bootloader capabilities and limits, and the build-host environment for working with it. Load this first when starting any A9 work.
---

# Hisense A9 (HLTE556N) — measured device facts

Everything here was read out of the device or its firmware, not from forum
posts. Full derivations in `~/A9/docs/twrp-port-findings.md`.

## Identity

| Property | Value |
|---|---|
| Model / codename | Hisense A9 / `HLTE556N` |
| SoC / platform | Snapdragon 662, SM6115, `bengal` |
| Stock Android | 11, `RKQ1.210107.001` / `L2037.6.01.01` |
| Panel | E Ink Carta 1200, 824×1648 (recovery UI assets are 720×1280). **DRM/KMS only — `/dev/dri/card0` exists, `/dev/graphics/fb0` and `/dev/fb*` do NOT** |
| Storage | **UFS, 4096-byte sectors, six LUNs, one GPT per LUN** |
| Slotting | **Virtual A/B** + dynamic partitions |
| Recovery | **Dedicated `recovery_a`/`recovery_b`, 96 MiB each** — not recovery-in-boot |
| Encryption | FBE + `dm-default-key` metadata + hardware-wrapped keys |
| Frontlight | `/sys/class/backlight/panel0-backlight/brightness` |

## Boot image geometry (from stock `recovery.img`)

```
header_version 2      page_size 4096       base   0x00000000
kernel  0x00008000    ramdisk 0x01000000   tags   0x00000100
```

## Bootloader capabilities — verified, not assumed

| Capability | Status |
|---|---|
| `fastboot boot <img>` | **UNSUPPORTED** — `remote: 'unknown command'`. No RAM-boot rescue path exists. |
| `fastboot fetch` | **UNSUPPORTED** — cannot read partitions over fastboot |
| fastbootd / userspace | **NONE** (`is-userspace: no`) — logical partitions in `super` cannot be flashed from fastboot |
| `fastboot flash` / `erase` / `set_active` | Work |
| `fastboot oem device-info` | Works |
| `abl.elf` strings | **LZMA-compressed, not encrypted** (corrected 2026-10-13). UEFI FV at 0x3000, LZMA GUID section; Python `lzma` (FORMAT_ALONE) gives a 491,720-byte PE (LinuxLoader) with readable strings. The high entropy was compression. The phone's `abl_a`/`abl_b` (built Dec 28 2022) differ from the package `abl.elf` (Jul 13 2022): the phone runs a newer boot chain than `HLTE556N/`. See `docs/a6l-harvest-2026-10-13.md` §1. |
| ABL fastboot commands (from strings) | `flash erase set_active getvar download continue reboot-recovery reboot-fastboot reboot-bootloader snapshot-update`, `flashing get_unlock_ability/unlock/lock/unlock_critical/lock_critical`, `oem enable-charger-screen/disable-charger-screen/off-mode-charge/select-display-panel/device-info/serial`, `Hisense`. **No `boot`, no `fetch`, no `oem edl`.** `getvar snapshot-update-status` is read-only VAB merge state. |
| `Hisense` sub-commands (disassembled, `abl_a` RVA 0x34dac) | exact match on `unlock`, `lock`, `unlockallow`. `unlock`/`lock` write devinfo bytes 13+14 (unlocked, critical unlocked) to 1/0 directly: **no prompt, no wipe**. `fastboot Hisense lock` is a NEVER (CLAUDE.md rule 14). |

The custom Hisense fastboot is on the share (`$A9_DATA/tools/fastboot-hisense/fastboot`, asset `fastboot-hisense`). It adds the `Hisense` verb; its help lists only `unlockallow`, but the ABL also accepts the undocumented `unlock` (what this phone was unlocked with) and `lock`. The bootloader is
already unlocked, so stock `fastboot` covers everything else. Never send `Hisense lock`.

Unlock procedure, boot image repack, Magisk patching and GSI flashing notes (ported from A9Toolkit 2026-08-13, not re-verified): `~/A9/docs/unlock-and-bootimg.md`. Asset origins: `docs/asset-sources.md`.

## Two traps that cost hours

**`ro.vendor.build.version.incremental` is not a build identifier here.** It
reports `L2037.6.01.01`, matching the stock package — but the device's kernel
is **15,047,394 bytes** while the package's is **15,050,789**. Always compare
actual image bytes, never the prop.

**The two firehose loaders differ.** `prog_firehose_ddr.elf` and
`prog_firehose_ddr_001360E1.elf` are both 638,972 bytes but diverge from byte
4161. Use **`_001360E1`**.

## A/B and `super`

- `super` is **10 GiB exactly** (2621440 sectors × 4096), LP group
  `qti_dynamic_partitions_a/_b`.
- Since 2026-10-10 (`scripts/sync-slot-a.sh`) LP metadata holds system,
  system_ext, product and vendor for **both** slots (the orphaned `-cow`
  partitions are gone), and both slots share firmware/vendor/dtbo/vbmeta.
  **Since 2026-10-11 slot A is MAIN (active, daily) and slot B is TEST**:
  flash new images to B, promote passing ones to A (`system` + `boot` only;
  `~/A9/docs/slots-and-promotion.md`). `/data` is shared, so a platform-key
  change needs `twrp format data`. `super` is nearly full with a GApps system
  on both slots (~80 MB free). Before 2026-10-10, `system_a` was absent.
- **Slot state lives in GPT attribute bits on LUN4**, not in `misc`. See the
  `a9-edl` skill.
- `misc` carries a `misc_virtual_ab_message` at offset 32768. **Never
  `fastboot erase misc`.**

## Environment (build-host, a Proxmox VM)

- **Repo `~/A9`, data `$A9_DATA`** (→ `/srv/nas/A9`, NAS NFSv4).
  Every script sources `scripts/a9-env.sh`; override `A9_DATA`, `ADB`, `FB`,
  `LOADER`, `A9_SERIAL` there or in the environment.
- **adb, fastboot, python3, lsusb are on `PATH`** (native android-tools).
  `edl` (bkerler edlclient) is system-wide from `~/host-config/a9-usb.nix`,
  which also adds the udev rules: Hisense `109b:*` and Qualcomm `05c6:9008` /
  `900e` → group `adbusers`, and ModemManager ignores 9008. Without that rule
  adb reports `Access denied (insufficient permissions)` — android-udev-rules
  ships the Hisense line commented out.
- **The VM owns a real USB controller.** Proxmox passes the Alder Lake PCH
  xHCI (`00:14.0`, in the guest `06:10.0 [8086:51ed]`) through over PCI, so
  every USB identity of the phone (Android, EDL, fastboot, recovery) lands in
  the VM. QEMU usb-host passthrough broke EDL (no Sahara hello); see the
  a9-edl skill, trap 3.
- **`A9_SERIAL`** (resolved from the registry `local/device.json`, gitignored; `scripts/a9-devices.sh`; `A9_DEVICE=<name|serial>` per command) pins adb/fastboot to this phone when another
  Android device (the Thor) is attached.
- **zsh does not word-split unquoted variables** — `$FB $cmd` passes one
  argument. Use arrays or `${=var}`.
- **The phone's shell (mksh) does 32-bit arithmetic.** `$((a + b))` on byte
  sizes overflows past 2 GiB (a 3.5 GB system partition) with no error. Print
  the numbers and sum them on the host.
- **toybox `sync` has no `-f`**: it is always a global sync. For a log that must
  survive a hard reset, mount its filesystem `-o sync` instead
  (`magisk/a9-bootlog`).
- `pkill -f` / `pgrep -f` match their own command line; an `adb shell` inside a
  piped `bash -s` eats the script's stdin (give it `</dev/null`).
- **Flashing is scripted (2026-10-13):** `flash-test-b.sh`, `promote-b-to-a.sh`,
  `a9-compat.sh` (platform key / SDK / patch vs `/data`), `image-diff.sh`,
  `a9-readback.sh`, `gsi-check.sh`; offline tests `tests/run-tests.sh` (fake
  phone). `scripts/edl` refuses writes to persist/modem/devinfo/diag/boot chain,
  raw sectors, whole-disk and erase without an explicit `A9_EDL_ALLOW_*`.
  `A9_SERIAL` comes from `local/device.json` (gitignored, see `data/device.example.json`); `A9_SERIAL=` (empty) means any device.
- **Don't build AOSP locally.** Use the GitHub Actions builder; see
  `~/A9/docs/plans/twrp-build-plan.md`.
- Legacy: Windows (retired 2026-10): the old WSL/adb.exe notes are history; see
  `docs/linux-native-migration.md`.

## Current state — as of 2026-08-09

**Recovered and working.** Stock Android 11 on slot A, IMEI intact, `/data`
erased, GSI gone (re-flash a GSI from the Windows PC copies if wanted; `14/` was deleted from $A9_DATA 2026-10-10). Slot B has no `system_b`.

**TWRP boots** (`product:twrp_HLTE556N`, root adb shell). See the `a9-twrp`
skill for the two build bugs that had to be fixed and the safe test harness.

**Read `~/A9/docs/session-2026-08-08-postmortem.md` §0 before any low-level
work.** Short version: never hand-patch the GPT — use `fastboot set_active`.
Doing so bricked this device for hours and cost `/data` unnecessarily.
