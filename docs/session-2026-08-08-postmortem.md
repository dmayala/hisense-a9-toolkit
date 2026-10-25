# Session post-mortem — 2026-08-08: TWRP build, and bricking the A9

**Outcome:** TWRP **boots** on the Hisense A9 (confirmed 2026-08-09,
`product:twrp_HLTE556N`, root adb shell). Two real build bugs found and fixed.
Along the way the device was bricked for several hours and `/data` was lost --
**mostly avoidably**. Read §0 before anything else.

> **Note (2026-10-10):** this is a historical record. Paths and tools below
> (`adb.exe`, QPST/QFIL, `/mnt/c`, COM ports, usbipd) refer to the retired
> Windows/WSL setup. For today's equivalents see
> [`.claude/skills/a9-edl/SKILL.md`](../.claude/skills/a9-edl/SKILL.md) and
> [`linux-native-migration.md`](linux-native-migration.md).

---

## 0. The single most important lesson

**Do not hand-patch the GPT. Use `fastboot set_active`.**

The device entered a recoverable state: TWRP failed to boot, the A/B fallback
switched to slot A, and slot A had no `system_a` (the GSI install had deleted
it), so it bootlooped. That was fixable.

What made it *un*recoverable was patching the GPT's A/B slot attribute bits by
hand. Every available check said the patch was correct -- right bit values,
both CRC32s recomputed and verified, a byte-diff showing exactly the 10
intended bytes changed, read-back from the device confirming it, and the
bootloader's own `getvar` agreeing. It was still wrong, and the device began
reporting:

    fastboot continue -> FAILED (remote: 'Failed to load image from partition: Device Error')

on **both** slots, even with a pristine factory boot image.

Everything after that was chasing a symptom of that patch: erasing `misc`
(which also destroyed the Virtual A/B header at offset 32768), restoring
`metadata`, **wiping `/data`**, and flashing stock `super`. None of it helped.
Restoring the **factory** GPT fixed the device instantly.

**The `/data` wipe was almost certainly unnecessary.** It changed nothing --
the device still failed afterwards. And had the BCB been cleared properly and
`fastboot set_active b` used to return to the slot that *did* have a system
(the GSI), the device would plausibly have recovered with data intact. That
was the obvious first move and it was not taken.

This cannot be proven, because once the GPT was patched every subsequent test
was contaminated. Which is itself the lesson: **an unverifiable low-level write
poisons all downstream debugging.**

## Corrections to earlier conclusions

Recorded because I asserted several of these confidently and was wrong.

- **"My GPT patch bricked it" — unproven.** Restoring the factory GPT is what
  made the device load images again, but `fastboot continue` was never run
  *before* the patch, so there is no baseline. The owner reports this device
  has entered the same state on previous occasions from touching recovery
  alone. Attribution between the two is unresolved.
- **"`super` restored recovery" — false.** Stock `super` was flashed and the
  device still failed; so did a pristine factory `boot_a`. Only the factory GPT
  restore changed it. But `super` was still necessary: it provided the
  `system_a` that let Android boot afterwards.
- **"Boot system normally leaves it bricked" — false.** Tested directly on
  2026-08-09: with `successful=no` on a healthy slot A it boots fine. The
  earlier observation was from a different underlying state (no `system_a`, or
  the patched GPT).
- **Recovery boots do NOT consume the slot retry budget.** Confirmed three
  times: `slot-retry-count:b` stayed at 7 across failed TWRP boots. The retry
  exhaustion that started the brick came from *system* boot attempts, not from
  flashing or booting recovery. Recovery testing is therefore much cheaper than
  assumed.

---

## 0b. Why "enter recovery, then reboot" un-stuck the device

AOSP-derived recovery **clears the BCB when it runs** -- it reads the
`boot-recovery` command, acts on it, and wipes it so the device does not loop.
inkOS recovery does this. That single fact explains three observations that
otherwise look contradictory:

| Observation | Explanation |
|---|---|
| "Enter Recovery mode" then reboot -> boots Android | Recovery actually ran, so it cleared the BCB |
| "Boot system normally" (正常启动系统) -> loops back to recovery | Recovery never ran; BCB still set |
| Later testing: booted normally with no prompt | `misc` had already been erased, so there was no BCB left to clear |

**TWRP does not clear it while its GUI is broken.** With the `libresetprop`
bug the recovery process never starts, so nothing wipes the BCB and
`fastboot reboot recovery` becomes sticky -- the device returns to recovery on
every boot until you clear it manually:

```bash
# from a TWRP root shell -- zero ONLY the 2048-byte bootloader_message
dd if=/dev/zero of=/dev/block/bootdevice/by-name/misc bs=1 count=2048 conv=notrunc
```

**Never `fastboot erase misc`** -- that also destroys the Virtual A/B message
at offset 32768. Verified working: BCB zeroed, header `02 b0 0a 74 56 00 01 00`
intact before and after.

Credit: this mechanism was worked out by the device owner, not by me.

## 1. What worked

- **CI build via GitHub Actions** ([`plans/twrp-build-plan.md`](plans/twrp-build-plan.md))
  — completely sidestepped AOSP-on-NixOS. Two builds, 57 and 53 minutes.
- **Device tree** — `twrpdtgen` skeleton, heavily corrected. See the plan doc
  for the five errors it made.
- **Image verification** — header geometry compared byte-for-byte against
  stock before flashing. Caught nothing wrong, but was the right check.
- **The backup** — byte-verified images of 31 partitions saved the ability to
  restore `boot_b`, `recovery_b`, `misc`, `metadata` exactly. Without it we
  would have been guessing.

## 2. What went wrong, in order

1. TWRP flashed to `recovery_b` (the active slot at the time).
2. **TWRP failed to boot** -- cause later established: the image had
   `recovery_dtbo: size=0` (see §5b). Not a device restriction.
3. The A/B fallback decremented slot B's retry counter, marked `boot_b`
   **unbootable**, and switched the active slot to **A**.
4. **Slot A had no `system_a`** (the GSI install deleted it), so slot A
   bootloops by construction. This is what turned a failed recovery boot into
   an apparent brick.
5. A sticky BCB (`boot-recovery`) in `misc` kept forcing recovery boots.
6. **The GPT was hand-patched** to clear slot B's unbootable flag. From here on
   the device reported `Device Error` on both slots and every subsequent
   diagnosis was contaminated. See §0.
7. `misc` erased, `metadata` restored, `/data` wiped, stock `super` flashed --
   none of it helped.
8. **Factory GPT restored -> device booted immediately.** Stock `super` was
   also needed, but only to give slot A a `system_a` to boot once images could
   load again.

## 3. Mistakes I made

Recorded plainly so they are not repeated.

| Mistake | Consequence |
|---|---|
| Used `adb exec-out ... dd` for the first backup | **Silent corruption.** Windows adb CRLF-translates the stream; `boot_a` came back 48,840 bytes too large, `frp` +1. Caught only by size-checking. See [`a9-backup`](../.claude/skills/a9-backup/SKILL.md). |
| Ran `backup-partitions.sh` default, not `all` | `super` (the GSI) never backed up, despite my own recommendation to run `all`. |
| `fastboot erase misc` | Destroyed the **Virtual A/B message at offset 32768**, not just the BCB. `misc` is not only the BCB on VAB devices. |
| Nearly restored the backup `misc` wholesale | It contained `"bootonce-bootloader"` from an earlier `adb reboot bootloader` — would have re-caused boot-to-fastboot. Caught by reading the bytes. |
| Set `successful=1` on slot B to protect it | Removed the retry-counter diagnostic; could no longer tell whether a boot was attempted. |
| Called `QSaharaServer` before **every** fh_loader op | The device sends its Sahara hello **once per EDL entry**. Each call consumed it, so every operation after the first silently failed. This is the "must reboot to get it picked up again" behaviour. |
| Assumed `ro.vendor.build.version.incremental` identified the build | It reported `L2037.6.01.01` matching the stock package, but the device's kernel is 15,047,394 bytes vs the package's 15,050,789. **That prop is not a build identifier on this device.** |
| Introduced `usbipd bind` | Took the device away from Windows tooling across *all* its USB identities, breaking Temblast `edl.exe` and later leaving the fastboot interface with no driver. |

## 4. A/B slot state lives in the GPT -- read it, never write it

Not in `misc`, not in any partition. It is in the **GPT partition attribute
bits** of `boot_a`/`boot_b` on **LUN4**. Qualcomm bootctrl layout:

```
bit 48-49  priority      bit 50  active      bit 51-53  tries
bit 54     successful    bit 55  unbootable
```

Observed on the bricked device -- this diagnosis was correct and useful:

```
boot_a  0x7f000000000000  priority=3 active=1 tries=7 successful=1 unbootable=0
boot_b  0xfa000000000000  priority=2 active=0 tries=7 successful=1 unbootable=1
```

`boot_b` flagged unbootable, slot A active -- exactly the fallback.

**Reading is safe and valuable. Writing is not.** Hand-patching those bits is
what produced `Device Error` (§0). [`parse-slot-attrs.py`](../scripts/parse-slot-attrs.py)
is kept for reading; **[`patch-slot-gpt.py`](../scripts/patch-slot-gpt.py) is
kept only as a record of what was tried -- do not use it.**

To change slot state: **`fastboot set_active <a|b>`**. It works even on a slot
flagged unbootable, and it resets the retry count.
To repair a damaged table: **[`restore-factory-gpt-lun4.sh`](../scripts/restore-factory-gpt-lun4.sh)**,
which writes the factory `gpt_main4.bin`/`gpt_backup4.bin` *and applies
`patch4.xml`* -- 26 fixups computing last-usable-LBA and CRCs against the real
disk. A dump-edit-write cycle cannot reproduce those, which is the likeliest
reason the hand-patched table was wrong.

## 5. Facts established about the device

- **UFS with per-LUN GPTs.** Six LUNs, hence `gpt_main0..5.bin`. LUN4 holds
  `boot_*`/`vbmeta_*`/`dtbo_*`/`modem_*`; LUN0 holds `super`/`persist`/
  `recovery_*`/`misc`/`metadata`; LUN5 the modem NV. 4096-byte sectors.
- **`fastboot boot` is unsupported** — `remote: 'unknown command'`. There is no
  RAM-boot rescue path on this device. Confirmed, not assumed.
- **`fastboot fetch` unsupported** (`is-userspace: no`), so no partition
  read-back over fastboot, and **no fastbootd** — logical partitions inside
  `super` cannot be flashed from bootloader fastboot.
- **`abl.elf` is encrypted.** Every 4 KB block of its 147 KB payload segment
  measures entropy 7.95. Vendor fastboot commands cannot be recovered from it.
- **The two firehose loaders differ** — `prog_firehose_ddr.elf` and
  `prog_firehose_ddr_001360E1.elf` are both 638,972 bytes but diverge from byte
  4161. Use the **`_001360E1`** variant; it is what QFIL and the community
  guides use, and it is proven on this handset.
- **`super` contained Virtual A/B COW devices** (`system_b-cow` ×4 extents,
  etc.) and **no `system_a`** at all.

## 5b. Why TWRP would not boot — RESOLVED

Comparing header-v2 fields against the device's own stock recovery:

| Image | `recovery_dtbo` | dtb |
|---|---|---|
| Stock `recovery_b` (boots) | **88722** @ 26202112 | 327956 @ 0x1f00000 |
| TWRP build 1 (package kernel) | **0** | 327956 @ 0x1f00000 |
| TWRP build 2 (device kernel) | **0** | 327956 @ 0x1f00000 |

The dtb was always correct; the **DTBO overlays were absent**. Cause:
`BOARD_PREBUILT_DTBOIMAGE` builds `dtbo.img` as a standalone artifact and never
passes it to `mkbootimg`. Fixed by adding
`BOARD_MKBOOTIMG_ARGS += --recovery_dtbo $(BOARD_PREBUILT_DTBOIMAGE)`.
`prebuilt/dtbo.img` is 88,722 bytes — exactly matching stock.

A kernel handed a device tree without the hardware's overlays dying silently
with no splash and no adb is precisely the observed symptom. **Unproven** until
build 3 is tested.

Ruled out by direct test, not inference:

| Hypothesis | How eliminated |
|---|---|
| Kernel mismatch | Device-kernel build matches stock's 15,047,394 exactly |
| AVB / verification | `vbmeta_b` rewritten with `--disable-verification`, still failed |
| Slot damage from testing | Retry counter stayed at 7, `unbootable:no` |
| Image geometry | All other header fields match stock byte-for-byte |

## 6. Unresolved

- **Display geometry.** DRM offers one mode, 448x829, while the panel is
  824x1648 and stock recovery's assets are 720x1280. TWRP renders correctly but
  into a corner. E Ink refresh itself works — this is a geometry/bridge problem.
  See the `a9-twrp` skill.
- **Exactly what was wrong with the hand-patched GPT.** Every check passed; the
  factory table fixed it. The unverified backup-GPT write at LBA 1048571 remains
  the prime suspect, never confirmed.
- **Whether the `/data` wipe could have been avoided** — almost certainly yes
  (§0), but unprovable after the fact.

## 7. If attempting this again

1. **Back up `super`.** Run the backup with `all`, not the default.
2. **Consider not flashing at all** until there is a way to test a recovery
   image without committing it to flash. `fastboot boot` does not work here,
   which removes the safe path — that alone is a strong argument for treating
   this port as high-risk.
3. **Flash the inactive slot's recovery first** (`recovery_a` while running
   slot B). It cannot be booted without switching slots, but it also cannot
   trigger a fallback on the slot you depend on.
4. **Know the retry budget.** Each failed boot decrements it; at zero the slot
   is marked unbootable and the device switches slots. On this device the other
   slot has no system, so that switch is effectively a brick.
5. **Have the stock package and an EDL cable ready before starting**, not after.
