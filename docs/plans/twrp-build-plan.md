# TWRP Build Plan — Hisense A9 (HLTE556N)

**Created:** 2026-08-08 · **Revised:** 2026-08-08 (CI-first restructure)
**Status:** Phases 0–2 complete. **TWRP 3.7.1_12-0 built and verified.**
Phase 3 (bring-up) blocked on the pre-flash backup.

**Repos:**
- Builder fork — https://github.com/dmayala/Action-TWRP-Builder
- Device tree — https://github.com/dmayala/android_device_hisense_HLTE556N (`android-12.1`)
- Local working copy — `~/A9/device_hisense_HLTE556N`
**Findings this builds on:** [`../twrp-port-findings.md`](../twrp-port-findings.md)

Goal, in order of decreasing certainty:

1. A TWRP image that boots on `recovery_a` and is fully controllable over
   `adb shell` — **useful on its own**, even with a dead screen.
2. Readable, usable rendering on the E Ink panel.
3. Working backup/restore to SD card.
4. `/data` decryption — stretch goal that probably fails.

Each phase has a **Done when** gate. Don't start the next until it passes.

---

## Build strategy: CI-first

Builds run on **GitHub Actions** via a fork of
[`azwhikaru/Action-TWRP-Builder`](https://github.com/azwhikaru/Action-TWRP-Builder),
not on this machine.

**Why.** The original plan's largest risk was the build host: this box is
NixOS under WSL2, and AOSP's build system plus its hundreds of prebuilt
binaries assume an FHS layout (`/lib64/ld-linux-x86-64.so.2`, standard glibc
paths) that NixOS does not provide. That failure mode surfaces hours into a
multi-hour build. The Action runs on `ubuntu-24.04` with OpenJDK 11, so the
problem simply does not exist.

**What the workflow gives us** (verified by reading
`.github/workflows/Recovery Build.yml`):

- `runs-on: ubuntu-24.04`, OpenJDK 11 (Zulu)
- Defaults to the TWRP minimal manifest, branch `twrp-12.1` — what we wanted
- `slimhub_actions` workspace cleanup to reclaim runner disk; 12 GB swap
- Device-dependency sync, TWRP-vs-Omni build-tree detection
- Uploads the built image to GitHub Releases

Repo health as of 2026-08-08: 357 stars, 2,717 forks, not archived, last push
2024-12-16, **no license file**. The maintainer states they won't debug
source-specific build errors — those are ours regardless.

**What it does not solve.**

- The **device tree is still the work**, and `DEVICE_TREE_URL` is a clone
  target, so it must live in a git repo. Phase 1 is unchanged in substance.
- **Slower iteration.** Each tweak is a full CI run (sync + build, est.
  1.5–3 h), with no ccache across runs. Fine for Phases 2–3; potentially
  painful in Phase 4 where display work may need many cycles. See
  [Fallback](#fallback-local-build-environment).

**Two runner limits to verify empirically on the first run**, not assume:
the 6-hour per-job ceiling, and actual free disk after the cleanup step.

### Workflow input mapping

| Input | Value |
|---|---|
| `MANIFEST_URL` | *(default — TWRP minimal manifest)* |
| `MANIFEST_BRANCH` | `twrp-12.1` |
| `DEVICE_TREE_URL` | our device tree repo (Phase 1) |
| `DEVICE_TREE_BRANCH` | `android-12.1` |
| `DEVICE_PATH` | `device/hisense/HLTE556N` |
| `COMMON_TREE_URL` | *(empty — no common tree)* |
| `COMMON_PATH` | *(empty)* |
| `DEVICE_NAME` | `HLTE556N` |
| `MAKEFILE_NAME` | `twrp_HLTE556N` |
| `BUILD_TARGET` | `recovery` |

`BUILD_TARGET=recovery` is correct and load-bearing — this device has a
**dedicated 96 MiB `recovery_a`/`recovery_b`**, so it is neither `boot` nor
`vendorboot`.

---

## Phase 0 — Local tooling and GitHub setup

Small now. Nothing AOSP-related runs locally.

### 0.1 — Tools

`git` and `gh` are installed **globally** via
`host-config/nixos/configuration.nix` → `environment.systemPackages`
(gh 2.63.0, added 2026-08-08). No nix-shell needed for either.

`twrpdtgen` still needs Python 3, which is not on the default `PATH`:

```bash
nix-shell -p python313
```

Note `pip install --user` is awkward on NixOS — prefer a venv inside the
nix-shell, and expect to sort this out when Phase 1 actually starts.

### 0.2 — GitHub

1. `gh auth login`
2. **Fork** `azwhikaru/Action-TWRP-Builder`. Read the workflow before running
   it — it is third-party and pulls third-party actions (`slimhub_actions`).
3. Create a **public** device-tree repo, e.g.
   `android_device_hisense_HLTE556N`, branch `android-12.1`. Public keeps
   Actions minutes free and avoids needing SSH deploy keys for the clone step.

> **Done when:** `gh auth status` is clean, both repos exist, and
> `twrpdtgen --help` runs.

---

## Phase 1 — Device tree (the real work)

### 1.1 — Generate the skeleton

```bash
twrpdtgen "$A9_FW/recovery.img" -o ./out
# -> out/hisense/HLTE556N/
```

### 1.2 — Correct `BoardConfig.mk`

`twrpdtgen` guesses. These values are **measured** from the stock images and
override whatever it produces:

```makefile
TARGET_ARCH                := arm64
TARGET_BOARD_PLATFORM      := bengal

BOARD_KERNEL_BASE          := 0x00000000
BOARD_KERNEL_PAGESIZE      := 4096
BOARD_KERNEL_OFFSET        := 0x00008000
BOARD_RAMDISK_OFFSET       := 0x01000000
BOARD_KERNEL_TAGS_OFFSET   := 0x00000100
BOARD_BOOT_HEADER_VERSION  := 2

BOARD_RECOVERYIMAGE_PARTITION_SIZE := 100663296   # 96 MiB, measured

# Dedicated recovery partition exists — must stay false
BOARD_USES_RECOVERY_AS_BOOT := false

BOARD_USES_METADATA_PARTITION := true
BOARD_SUPER_PARTITION_GROUPS  := qti_dynamic_partitions
AB_OTA_UPDATER                := true
TW_INCLUDE_REPACKTOOLS        := true
TW_INCLUDE_CRYPTO             := true    # will likely not work; see Phase 5
```

Use the **stock kernel as a prebuilt** — extract from `recovery.img`
(pages 1..3675 at 4096 B/page, 15,050,789 bytes) and commit it. `boot.img`
and `recovery.img` carry the identical kernel, so no kernel source is needed.

### 1.3 — Resolve the Virtual A/B flags properly

Published guides are stale on this. Read the flag names out of the actual
`twrp-12.1` tree rather than copying from a blog — either clone
`bootable/recovery` standalone, or read it on GitHub:

```bash
git clone --depth 1 -b twrp-12.1 https://github.com/TeamWin/android_bootable_recovery
grep -rn "VIRTUAL_AB\|virtual_ab\|snapuserd" android_bootable_recovery/ | grep -i "ifeq\|TW_\|BOARD_"
```

### 1.4 — Cross-check against a sibling device

Closest published `bengal` + Virtual A/B trees:

- Moto G Power 2021 — `borneo`
- Redmi 9T / Poco M3 — `juice`
- Moto E7 Plus

Diff their `BoardConfig.mk` against ours and account for every difference.
Anything they set that we don't should be understood, not blindly copied.

### 1.5 — fstab

Base on stock `system/etc/recovery.fstab` (already extracted). Key entries:
logical `system`/`system_ext`/`product`/`vendor` from `super` with
`slotselect`, `/data` f2fs, `/metadata` ext4, SD card at
`/dev/block/mmcblk1p1`.

### 1.6 — Push

```bash
git init -b android-12.1 && git add -A && git commit -m "HLTE556N: initial device tree"
gh repo create android_device_hisense_HLTE556N --public --source=. --push
```

> **Done when:** the tree is pushed on branch `android-12.1` and contains
> `BoardConfig.mk`, `twrp_HLTE556N.mk`, `recovery.fstab`, and the prebuilt
> kernel.

---

## Phase 2 — First CI build

Dispatch the workflow from the fork with the inputs in the mapping table
above (`gh workflow run`, or the Actions tab).

Expect several failed runs. Typical first-build failures are missing
`TARGET_RECOVERY_*` vars, fstab syntax, and SELinux policy — all cheap to fix
once the log names them. Read the failing step's log, patch the device tree,
push, re-dispatch.

Sanity-check the release artifact before flashing anything:

```bash
gh release download -R <fork> -p 'recovery*.img'
ls -l recovery.img                                    # must be < 100663296
dd if=recovery.img bs=1 skip=8 count=40 | od -A none -t u4
```

The header dump must show page size **4096** and header version **2**,
matching stock. A mismatch means it will not boot — fix before flashing.

> **Done when:** a `recovery.img` is published to Releases, is under 96 MiB,
> and its header geometry matches stock.

---

## Phase 3 — Bring-up (the real milestone)

**Take a fresh full EDL dump first.** The `Stock/Backup-2023-09-11-173822` (deleted from $A9_DATA 2026-10-10; original on the Windows PC)
dump predates the GSI install. Verify the restore path *works* before relying
on it.

**Flash the ACTIVE slot's recovery.** Verified 2026-08-08:
`fastboot getvar current-slot` → **`b`**. Flashing `recovery_a` would write to
the inactive slot, `adb reboot recovery` would still boot stock `recovery_b`,
and we would wrongly conclude the build was broken.

```bash
adb reboot bootloader
fastboot getvar current-slot           # re-check every time — do not assume
fastboot flash recovery_b recovery.img # ← active slot
fastboot reboot
adb reboot recovery
```

**Slot switching is not a fallback on this device.** `fastboot set_active a`
switches the whole system to slot A, whose `system` may be stale or empty
since the GSI was flashed to the active slot. The real fallback is re-flashing
stock `recovery.img` to `recovery_b`.

`fastboot fetch` is also unsupported (`is-userspace: no`), so partitions
cannot be read back over fastboot — backups must go through adb+root or EDL.

**`fastboot boot` does not work on this device — CONFIRMED 2026-08-08, not
assumed.** The image uploads fine and the bootloader then rejects the command:

```
Sending 'boot.img' (98304 KB)    OKAY [ 2.076s]
Booting                          FAILED (remote: 'unknown command')
```

Nothing is written when this fails, so it is harmless to try — but it is not
an available test path. Custom recovery must be flashed to `recovery_a`.

Then, regardless of what the screen shows:

```bash
adb devices          # TWRP's adbd starts early
adb shell
adb pull /tmp/recovery.log
```

`/tmp/recovery.log` is the primary debugging artifact from here on.

While in there, settle the open display question:

```bash
adb shell cat /sys/class/graphics/fb0/modes
adb shell cat /sys/class/graphics/fb0/virtual_size
adb shell cat /sys/class/graphics/fb0/bits_per_pixel
```

> **Done when:** `adb shell` connects from recovery and `/tmp/recovery.log`
> pulls. **This is goal #1 achieved** — TWRP's CLI (`twrp install`,
> `twrp backup`, `twrp wipe`) is fully usable from here even with a blank
> screen. Consider banking this before touching display code.

**If it does not boot:** flash stock `recovery.img` back to `recovery_a`,
confirm stock recovery works, iterate on header/fstab. If the device won't
boot at all, EDL restore.

---

## Phase 4 — E Ink display

Only start once Phase 3 passes. **Reassess the CI loop here** — if this needs
more than a handful of rebuilds, stand up the local fallback environment.

Evidence from the findings doc suggests this may be less work than feared:
stock `/system/bin/recovery` references **no** EPD/waveform sysfs nodes, only
battery and thermal. That implies the kernel panel driver auto-refreshes on
framebuffer writes, so TWRP's stock `minui` fbdev backend may work unmodified.

1. **Set resolution and pixel format** from the Phase 3 `fb0` readings. The
   unresolved conflict: stock recovery UI assets are 720×1280 but the panel is
   824×1648 native. **The runtime `fb0` values are authoritative.** Set
   `TARGET_RECOVERY_PIXEL_FORMAT` and `TW_THEME` accordingly.
2. **Build, flash, look at the screen.** Three outcomes:
   - *Renders correctly* → step 4.
   - *Ghosted/garbled* → refresh or pixel-format problem → step 3.
   - *Blank* → driver is not auto-refreshing → step 3.
3. **Reverse the refresh path.** Disassemble stock `system/bin/recovery`'s
   framebuffer ioctls (`FBIOPAN_DISPLAY`, custom `FBIO*`), or look for a
   kernel-exposed sysfs node (`ro.hmct.epd.type=2` hints at a Hisense EPD
   framework). Patch `minui`'s `gr_flip()` to issue the same call.
4. **Theme for E Ink.** TWRP's default dark theme will be unreadable and ghost
   badly. Build high-contrast black-on-white, heavy line weight, large hit
   targets. Consider forcing a full-panel refresh per screen change.

> **Done when:** the TWRP UI is readable and touch-navigable on the panel.

---

## Phase 5 — Polish and stretch goals

1. **Mirror to `recovery_b`** once `recovery_a` is proven.
2. **Validate backup/restore to SD card** (`/dev/block/mmcblk1p1`). Back up
   `boot`, restore it, confirm the device still boots. An untested backup is
   not a backup.
3. **Validate zip flashing** — a Magisk zip is the smoke test.
4. **`/data` decryption — expect failure.** Wrapped-key FBE +
   `dm-default-key`, and the stock recovery ramdisk contains no keymaster or
   gatekeeper binaries to lift. An attempt means pulling the keymaster HAL out
   of `vendor` in `super` and wiring it into the recovery ramdisk. Time-box
   it; several SD662 devices never achieved this. The SD card makes TWRP fully
   useful without it.
5. **Publish** the device tree — nobody else has one for this device.

---

## Fallback: local build environment

Only if CI iteration becomes the bottleneck (realistically, Phase 4).

Requirements: ~250 GB free on a **case-sensitive Linux filesystem** (never
the NFS share `$A9_DATA` — case handling and speed make it impractical;
do not build on NixOS bare metal either), 16 GB RAM, JDK 11.

Options, in order:

1. **Ubuntu 22.04/24.04 container** (Docker/Podman) — every AOSP guide's
   `apt install` works verbatim.
2. **A separate Ubuntu VM/LXC** on the Proxmox host. Simple, but a second
   source tree. (Legacy, Windows: this was a WSL2 distro.)
3. **`nix-shell` + `buildFHSUserEnv`** — native, but needs hand-tuning and
   fails deepest into the build. **Do not try this first.**

Notes: size the VM's RAM/disk explicitly. Set `USE_CCACHE=1` and `ccache -M 50G`.

---

## Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| ~~NixOS/WSL2 can't build AOSP~~ | **Eliminated** | Moved to GitHub Actions |
| CI job hits 6 h ceiling or runs out of disk | Medium | Verify on first run; fallback env documented |
| Slow CI iteration stalls display work | Medium | Reassess at Phase 4; add ccache to fork or go local |
| Wrong VAB flags → no boot | Medium | Read flags from the tree (1.3), sibling diff (1.4) |
| E Ink won't render | Medium | Phase 3 headless milestone is valuable alone |
| `/data` decryption | **Expected to fail** | Design around SD card |
| Third-party workflow/actions | Low | Fork and read before running |
| Hard brick | Low | EDL + firehose + `gpt_*` backups, verified in Phase 3 |

## Open questions

- Recovery-mode framebuffer geometry — 720×1280 or 824×1648? *(Phase 3)*
- Correct Virtual A/B flag names in `twrp-12.1` *(Phase 1.3)*
- Free disk and wall-clock of a full CI run *(Phase 2)*
- ~~Which Lineage GSI build is running~~ **RESOLVED 2026-08-08:**
  `lineage_arm64_bgN-userdebug 14 UQ1A.240205.004 eng.crossg.20240217.123224`
  — the 2024-02-17 LineageOS 21 build from `14/`.
- ~~Does the prebuilt kernel match the device's firmware?~~ **RESOLVED
  2026-08-08: YES.** `ro.vendor.build.version.incremental` = **`L2037.6.01.01`**,
  identical to the `HLTE556N/` package the prebuilt kernel was extracted from.
  No kernel/vendor mismatch, and **no rebuild is needed** before flashing.
  (The GSI is "latest"; the underlying Hisense vendor firmware is not — those
  are independent, and it is the vendor firmware that matters here.)
- Active slot confirmed `_b` from Android (`ro.boot.slot_suffix`), matching
  `fastboot getvar current-slot`.

## Progress log

| Date | Phase | Notes |
|---|---|---|
| 2026-08-08 | — | Plan created; recon complete; nothing built yet |
| 2026-08-08 | — | Restructured CI-first on `azwhikaru/Action-TWRP-Builder`; old Phase 0 (host setup) demoted to fallback |
| 2026-08-08 | 0 ✅ | `gh` 2.63.0 global via host-config; authed as `dmayala`; twrpdtgen 3.0.0 in `A9/.venv`; builder forked |
| 2026-08-08 | 1 ✅ | Device tree generated + corrected, pushed to `dmayala/android_device_hisense_HLTE556N@android-12.1` |
| 2026-08-08 | 2 ✗ | Run `31238102457` failed at 9m31s — `PRODUCT_STATIC_BOOT_CONTROL_HAL` obsolete in Android 12 |
| 2026-08-08 | 2 ✅ | Run `31238547821` **succeeded in 57m19s**. TWRP 3.7.1_12-0, verified against stock |

### Phase 2 result

Artifact: `HLTE556N-31238547821` →
`$A9_DATA/twrp/twrp-HLTE556N-31238547821.img` (deleted from $A9_DATA 2026-10-10; original on the Windows PC; the kept build is `recovery/twrp-HLTE556N-31434216263.img`)
sha256 `7f9a7f28871ccdb2cc989c839cf44195236d67fb77b7f1b6806e81b467a640ca`

Header verified byte-for-byte against the stock `recovery.img`:

| Field | Built | Stock | |
|---|---|---|---|
| magic | `ANDROID!` | `ANDROID!` | ✅ |
| kernel_size | 15050789 | 15050789 | ✅ exact — prebuilt kernel intact |
| kernel_addr | 0x8000 | 0x8000 | ✅ |
| ramdisk_addr | 0x1000000 | 0x1000000 | ✅ |
| tags_addr | 0x100 | 0x100 | ✅ |
| page_size | 4096 | 4096 | ✅ |
| header_version | 2 | 2 | ✅ |
| ramdisk_size | 25671917 | 11147216 | expected — TWRP ramdisk is larger |
| os_version | 16.1.0 / 2099-12 | 11.0.0 / 2022-04 | expected — anti-rollback hack |
| file size | 100663296 | 100663296 | ✅ exactly the partition size |

Ramdisk unpacked and inspected: `system/bin/recovery` (2.5 MB), `twres/`
theme resources, **and our custom `recovery.fstab` is present verbatim** —
confirming the device tree actually took effect rather than a default.

Note for Phase 5: the ramdisk contains only *soft/portable* keymaster libs
(`libpuresoftkeymasterdevice.so`, `libsoftkeymasterdevice.so`), not the QTI
vendor HAL — consistent with the prediction that wrapped-key FBE decryption
will not work.

**Build failure fixed between runs:** `twrpdtgen` emitted
`PRODUCT_STATIC_BOOT_CONTROL_HAL`, removed in Android 12 and now a hard error.
Replaced with the HAL module plus its `.recovery` variant.

### twrpdtgen corrections applied (Phase 1)

Five errors in the generated skeleton, all caught before the first build:

1. **`BOARD_USES_RECOVERY_AS_BOOT := true` → `false`.** The generator assumes
   A/B implies recovery-in-boot. This device has real 96 MiB
   `recovery_a`/`recovery_b`. Would have built the wrong image entirely.
2. **`omni_HLTE556N.mk` → `twrp_HLTE556N.mk`.** It emitted an Omni-style
   makefile inheriting `vendor/omni/config/common.mk`; the twrp-12.1 minimal
   manifest ships `vendor/twrp`, not `vendor/omni`. Confirmed by reading
   `twrp-default.xml`. Would have failed at `lunch`.
3. **`hisense_dynamic_partitions` → `qti_dynamic_partitions`.** Group name was
   guessed from the brand; LP metadata in `super_1.img` says `qti_`.
4. **`BOARD_SUPER_PARTITION_SIZE`** — generator emitted `9126805504` with a
   `TODO: Fix hardcoded value`. Real value from `COM4_PartitionsList.xml`:
   2621440 sectors × 4096 = **10737418240** (exactly 10 GiB).
5. **fstab rewritten** — added slotted `boot`/`recovery`/`dtbo`/`vbmeta`/
   `modem`, whole-`super` for imaging, and the SD card as `/external_sd` with
   `storage;removable` (the intended working medium given decryption is
   expected to fail).

Also added: `BOARD_USES_METADATA_PARTITION`, explicit kernel base/offsets, and
`TW_BRIGHTNESS_PATH := /sys/class/backlight/panel0-backlight/brightness`
(measured — stock `init.recovery.qcom.rc` writes 200 to it on init).
