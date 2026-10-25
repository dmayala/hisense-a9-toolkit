# TWRP E Ink UX: E Ink-key full refresh, power-off image, no fast mode

**Status (2026-10-14): TESTED ON THE PHONE and in use.** Build 38624343750 on both recovery slots via `flash-recovery.sh` (boot-verified, hashes match). E Ink button: 0x2fe in TWRP, one 38-frame full refresh (log + user). Power off: the image stays on the glass with USB unplugged (user). With USB attached, charger mode replaces it (as under Android). Feature (C) skipped by design (see below).
Labels:
**measured** means read from a file or binary with the tool named; **inferred** means deduced and not
checked on the device.

| Feature | Result |
|---|---|
| (A) Power-off image | Implemented. Reboot > Power Off and `twrp reboot poweroff` draw the stock "DEVICE POWERED OFF" screen with the full waveform. They wait until the software TCON is idle and only then set `sys.powerctl`. Reboots are untouched |
| (B) E Ink button | Implemented. Key `0x2fe` (gpio-keys) does one full GC16 redraw of the current screen. TWRP consumes the key |
| (C) Faster display | **Skipped, by design.** The library has no usable fast mode (§4). Ordinary updates stay on the stock 38-frame partial GC16 |

## 1. Branches, commits, image

| Repo (GitHub, public) | Branch | Commit | Change |
|---|---|---|---|
| `dmayala/android_bootable_recovery` | `eink-ux` (from `android-12.1` `fe367c19`) | `0540ac3c` | `minuitwrp/graphics_hmct_epd.cpp`, `minuitwrp/include/minuitwrp/minui.h`, `gui/gui.cpp`, `twrp-functions.cpp` |
| `dmayala/android_device_hisense_HLTE556N` | `eink-ux` (from `android-12.1` `894a8da`) | `cd79b04` | `recovery/root/system/etc/hmct_epd_poweroff.png`, README |
| `dmayala/platform_manifest_twrp_aosp` | `eink-ux` (from `twrp-12.1` `0aa4f29`) | `2840b84` | `bootable/recovery` revision `android-12.1` -> `eink-ux` |

The manifest needs its own branch: the recovery fork is selected only through the manifest, and the
builder has no input for it. `android-12.1` / `twrp-12.1` are unchanged, so build 31434216263 can still
be reproduced exactly.

Build: `dmayala/Action-TWRP-Builder`, workflow "Recovery Build". The inputs are those of 31434216263,
except `MANIFEST_BRANCH=eink-ux` and `DEVICE_TREE_BRANCH=eink-ux`:

```bash
gh workflow run "Recovery Build" -R dmayala/Action-TWRP-Builder \
  -f MANIFEST_URL=https://github.com/dmayala/platform_manifest_twrp_aosp -f MANIFEST_BRANCH=eink-ux \
  -f DEVICE_TREE_URL=https://github.com/dmayala/android_device_hisense_HLTE556N -f DEVICE_TREE_BRANCH=eink-ux \
  -f DEVICE_PATH=device/hisense/HLTE556N -f DEVICE_NAME=HLTE556N -f MAKEFILE_NAME=twrp_HLTE556N -f BUILD_TARGET=recovery
```

(With a manifest branch other than `twrp-11`/`twrp-12.1`, the workflow's "Check Build Tree" picks
`omni.dependencies` instead of `twrp.dependencies`. The device tree has neither file, and that step is
`continue-on-error`, so the result is the same as before.)

Image: `$A9_DATA/recovery/twrp-HLTE556N-38624343750.img`, sha256 `56cfb62a4b34d12170d1221dfc31f7b2e43fc2c8538608687c1519195670464c` (also in the `.sha256` next to it).

## 2. (B) E Ink button: full refresh

**What the user sees (inferred, not yet observed):** press the E Ink key in TWRP, and the current screen
redraws once with the full waveform: one black/white flash, about 0.45 s (38 frames at 85 Hz). Ghosting
is gone afterwards. Nothing else happens: the key is not passed to the theme.

**How:**

- `gui/gui.cpp` `InputHandler::process_EV_KEY`: `EV_KEY` code `0x2fe` (`KEY_HMCT_EINK`) goes to
  `gr_epd_full_refresh()` on key down (value 1). Repeats (2) and the release are swallowed. This applies
  only while the hmct backend is active (`gr_epd_active()`). On any other display the key takes the
  normal path.
- The code is **measured**: `getevent -lt` on the phone (Android, 2026-10-14) shows
  `/dev/input/event2 (gpio-keys): EV_KEY 02fe DOWN/UP` (a16 findings §13). **Inferred**: recovery sees
  the same code. Recovery runs the device's own kernel and `recovery_dtbo`. The DTBO is byte-identical
  to `prebuilt/dtbo.img`, and its epd/spi properties match the Android `dtbo` (epd-waveform-source §3).
  The gpio-keys node was not compared separately. minuitwrp opens every `/dev/input/event*`, and only
  `hbtp_vm` is blacklisted.
- `gr_epd_full_refresh()` (in `graphics_hmct_epd.cpp`) calls `ForceClearGhosting(1)` and then
  `drawImage()` on the current frame buffer, under the backend's draw mutex. Input and rendering share
  one thread in TWRP (`runPages` -> `loopTimer` -> `processInput`, then `Render`/`flip`). So at key time
  the buffer holds the last complete frame.

**Why that is a full refresh (measured, disassembly of `libhmct_epd.so` = stock `librecovery_ui.so`,
sha256 `2b187fca…9cdb`, §5):**

1. `ForceClearGhosting(int)` (`0x39950`, 5 instructions) only stores its argument in `force_clear_flag`.
2. The TCON thread (`0x3b164`) calls `WaitForNextImage()`, which reads `force_clear_flag` at `0x3bb40`
   and passes it as `force` to `ModeDecision_MirrorMode(img, handle, 18, 18, force, 3)` (`0x3bb70`).
   When `force` is set it also sets `epd_clear_revert`, and it skips the "image is repeat" `memcmp`
   (`0x3bbb8`), so an unchanged screen is still drawn.
3. `ModeDecision_MirrorMode` (`0x3c8d4`, 8 instructions) is a stub. It stores `mode` (`0x6ae98`) and
   `force != 0` (`0x4d63c94`), and returns 1.
4. `Update_lookUp_table` (`0x3c8fc`) computes `m = force ? 2 : mode`. Mode 2 reads the 256 × 38 table at
   `0x1a89c`, in which every transition is driven, including old == new (the doc's "table A", full
   GC16). Mode 3 reads `0x1ce9c` ("table B", where unchanged pixels are not driven).
5. After that update, `epd_clear_revert` makes `WaitForNextImage` zero `force_clear_flag` again
   (`0x3bff8-0x3c004`). The force is one-shot.

So this is the same "clear" as Android's `epd_force_clear` (a6l-harvest §3.2): a forced GC16 redraw of
the current picture. It is **not** the 162-frame init flash. That flash runs only while the init-done
flag at `0x4d63c98` is 0, which is once after `InitEpd`. Nothing exported re-arms it, and it was not
touched.

## 3. (A) Power-off image

**What the user should see (inferred):** Reboot > Power Off. The TWRP screen changes to the Hisense
logo with "⏻ DEVICE POWERED OFF" (one full-waveform update, about 0.5 s), and then the phone switches
off. The glass keeps the image. **With USB connected** the phone goes into off-mode charging, and the
bootloader's charging splash can replace the image. This was measured on Android, 2026-10-14
(poweroff-image.md). Judge it with the cable unplugged.

**How:**

- `TWFunc::tw_reboot(rb_poweroff)` (twrp-functions.cpp): after the optional `poweroff.sh`, and before
  `property_set("sys.powerctl", "shutdown,")`, it calls
  `gr_epd_show_image("/system/etc/hmct_epd_poweroff.png", 4000)`, if `gr_epd_active()`. All other
  `RebootCommand`s (system, recovery, bootloader, edl, fastboot, download) are untouched, so **a reboot
  never shows the image.**
- Paths covered: GUI Reboot > Power Off (`GUIAction::reboot` -> `twrp.cpp reboot()` -> `tw_reboot`), and
  ORS `reboot poweroff` (`adb shell twrp reboot poweroff`, openrecoveryscript.cpp). Those are the only
  two `rb_poweroff` callers (grep).
- **Not covered:** `adb reboot -p` / `reboot -p` from the TWRP shell. adbd sets `sys.powerctl` itself,
  and TWRP's code never runs. Property triggers do not help either. AOSP init clears its action queue
  when it handles a shutdown, so an `on property:sys.powerctl=…` rule would not run (inferred from AOSP
  init source, not tested). The phone then keeps the last TWRP screen, as before.
- `gr_epd_show_image()`:
  1. It loads the PNG with minuitwrp's own loader (`res_create_surface`; 8-bit grey is expanded to RGBX).
  2. It centres the image on a white 824 × 1648 canvas, clipping if needed. Our image is exactly
     824 × 1648.
  3. Under the draw mutex it sets `frozen`, so later `gr_flip()`s from the GUI thread no longer reach
     the panel. Then it calls `ForceClearGhosting(1)` (full waveform, so no TWRP ghost remains) and
     `drawImage()` on its own surface.
  4. It waits until the TCON is idle. It polls every 20 ms and needs 3 idle readings in a row, with a
     4 s timeout. Then it sleeps another 200 ms for the consumer thread's last flip.
  5. It logs `hmct_epd: still image … on the panel after ~N ms`, or `no idle confirmation within 4000 ms`.
  6. Shutdown proceeds either way. A missing or bad PNG logs `cannot load …` and powers off as before.
- **Idle test (measured from the disassembly):** `frame_is_end == 1` (set by the producer after the
  last drive frame, `0x3b2a8`), `image_update == 0` (reset only when the producer next waits for an
  image, `0x3b7a4`), `frame_lose == 0` (no queued "lost" image) and `Que[0] == Que[1]` (the 8-slot
  drive-frame ring is empty; the consumer thread `0x3adb4` advances `Que[0]`). All four are exported
  symbols, resolved by `dlsym`. If any is missing, the backend still renders, the init log says
  `idle wait no`, and power-off falls back to a fixed 4 s sleep.
- Turning it off: delete `recovery/root/system/etc/hmct_epd_poweroff.png` from the device tree (no
  file, no still). The image path is `GR_EPD_POWEROFF_IMAGE` in `minui.h`.
- The image: `$A9_DATA/assets/stock-aod/edpd-bitmap-low-poweroff.png` (sha256 `6d4db879…`, RGB,
  decoded from stock's RGB565 `bitmap_low.raw`, see poweroff-image.md) was converted with
  `magick … -colorspace Gray -depth 8 -strip` to `hmct_epd_poweroff.png` (14,679 B, sha256
  `2470a419…`, grey, IHDR/IDAT/IEND only). Its maximum chroma was 0.0031, so it was already grey.
  4,668 pixels (0.34 %) changed by rounding. The TCON keeps only the top 4 bits per pixel anyway
  (`lsr #4` in `WaitForNextImage`), giving 16 levels.

**Why the image should stay after power is cut (inferred, strong):** the same kernel (stock
`recovery_b`'s) is in use, and on Android the image stayed on the glass through a real power-off with
USB unplugged (poweroff-image.md, user, 2026-10-14). TWRP does not blank the panel at exit: `hmct_blank`
ignores blank requests, and nothing calls `EpdBlank`/`DeInitEpd` on this path.

## 4. (C) Faster updates: why it was skipped

The question was whether a DU/A2-like mode with fewer frames can be chosen per update from the backend.
**It cannot, safely.** Measured from the disassembly (§5):

1. **The mode is not an input.** `WaitForNextImage` hard-codes `mode = 3` and both temperatures `= 18`
   in its call to `ModeDecision_MirrorMode` (`mov w5,#3` / `mov w2,#0x12` / `mov w3,#0x12` at
   `0x3bb60-0x3bb6c`). It does this for every image, and that call overwrites the mode global
   (`0x6ae98`) each time. No exported function sets the mode. The only per-update input is `force`,
   which selects the *slower* full table. Changing the mode would mean racing a write into a private
   global between `ModeDecision` and `Update_lookUp_table`, or patching the library's code. Both are
   hacks.
2. **The only short path is broken.** `Update_lookUp_table` sends modes 1, 6, 8 and 9 (bitmask `0x342`)
   to a 10-frame path (`0x3c94c`, counter wraps at 9). It reads a table at `0x1a874` with a row stride
   of 10 and indexes it with the full 8-bit transition `old<<4 | new`. What is at `0x1a874`:
   - The data there is only 40 bytes, a 4 × 10 table. Row 0: none; row 1: `2` × 9; row 2: `1` × 9;
     row 3: none. That is a 2-level DU for a 2-bit index.
   - The 256 × 38 GC16 table starts directly after it, at `0x1a89c`. The bytes before `0x1a874` are
     not waveform codes (`03 06 09 …`).
   - With 16 grey levels, every transition except black -> black (row 0) reads GC16 bytes at the wrong
     stride. For example, white -> white is row 255, at `0x1a874 + 2550`. The drive would be wrong and
     possibly DC-unbalanced on the glass. **Inferred:** it is a leftover of a DU mode whose diff stage
     was never wired up, and stock never runs it, because the mode is always 3.
3. **There is no other table.** The library has exactly the two 38-frame tables, plus the 40-byte stub
   (epd-waveform-source §1.4) and the built-in 162-frame init sequence. The six `LONG_WF_BUF_*` buffers
   are zero-filled and never written. The faster Android tables (Speed: 7 frames in warm bins) live in
   the vendor composer, keyed by temperature bin (epd-waveform-source §2). Using them would mean
   reimplementing the TCON's frame generation in TWRP, which is out of scope and well beyond a
   "small, clean code path".
4. **Already in place:** ordinary updates use table B, which does not drive unchanged pixels. The TCON
   merges bursts on its own: `drawImage` during an update overwrites a single "lost image" slot, so the
   latest frame wins. The DSI mode is fixed at 85 Hz, so 38 frames is about 0.45 s per update. The
   periodic or on-demand full clear that (C) would have needed is provided on demand by (B).

## 5. Library internals used (measured)

`prebuilt/libhmct_epd.so` = `work-epd/librecovery_ui.so`, sha256
`2b187fca7e8ddfa470b2dbe04749216105edda7612f4b494cbd43f1683cd9e9c`. Addresses are vaddrs.

| Symbol | Addr | Role here |
|---|---|---|
| `_Z18ForceClearGhostingi` | `0x39950` | `force_clear_flag = arg; return 0` |
| `_Z9drawImageP9GRSurface` | `0x395cc` | If `frame_is_end && !is_bitmap_mode && !image_update`: copy to `start_frame_buf`, set `image_update = 1`, signal. Otherwise copy to `lost_frame_buf`, set `frame_lose = 1`. Copies `height * row_bytes` from vtable slot 2 synchronously |
| TCON producer thread | `0x3b164` | Loop: `WaitForNextImage`, then `Update_epd_Image` into `DPI_UpdateBuffer[(w+1)%8]` until it returns 0, then `frame_is_end = 1` and log `out frame: N` |
| Consumer thread | `0x3adb4` | `Que[0] = (Que[0]+1)%8`, swizzle the slot into `gr_get_drv_buffer()`, `gr_flip()` |
| `_Z16WaitForNextImagev` | `0x3b974` | See §2. Builds 4-bit grey (`lsr #4`) and the `old<<4\|new` diff frame |
| `_Z19NormalModeWaitImagev` | `0x3b5ac` | Takes a lost image if `frame_lose`, else `image_update = 0` and waits |
| `ModeDecision_MirrorMode` | `0x3c8d4` | Stub: store mode, `force != 0`; return 1 |
| `Update_lookUp_table` | `0x3c8fc` | Table select (§2, §4). Init sequence while `0x4d63c98 == 0` (162 frames) |
| `frame_is_end` (D), `image_update` (D), `frame_lose` (B), `Que` (B, 2 × int) | `0x6ae91`, `0x6ae90`, `0x6b370`, `0x6b2b8` | Idle test |

## 6. Phone test checklist (main session)

The phone is the user's daily phone: no `/data` wipe, and reboots batched and announced. TWRP is a
recovery image. It touches no system partition, so no `/data` key questions arise. Rule numbers refer
to CLAUDE.md.

1. **Before:** `scripts/a9-health.sh` must exit 0, with Android booted on slot a.
2. **Check-only:** `scripts/flash-recovery.sh $A9_DATA/recovery/twrp-HLTE556N-38624343750.img`. It checks
   the sha256, header v2, page 4096, recovery_dtbo and size, and needs no phone writes.
3. **Flash:** `scripts/flash-recovery.sh $A9_DATA/recovery/twrp-HLTE556N-38624343750.img --write`. From
   bootloader fastboot, and only if `is-userspace` = `no` (rule 3), it flashes `recovery_a` and
   `recovery_b` (rule 4). The active slot is untouched. It then boots TWRP, requires an *authorized*
   adb recovery, hashes both slots, and exits to Android. If anything fails it rolls back to
   `twrp-HLTE556N-31434216263.img` by itself. (Manual equivalent: §7 with the new image.)
4. **Enter TWRP:** `scripts/twrp.sh enter`. Then check:
   - `adb shell 'grep -a -E "Using hmct epd graphics|hmct_epd:" /tmp/recovery.log'` must show
     `Using hmct epd graphics.` and `hmct_epd: using Hisense E Ink backend, 824x1648 (full refresh yes, idle wait yes)`.
     If it says `no`, a symbol did not resolve. Report this; it is not a display failure.
   - Regression: the GUI renders full-screen, touch works,
     `adb shell 'df -h /data; getprop twrp.all.users.decrypted'` shows about 223 G and `true`.
   - `epd-snap.sh` does not apply in TWRP (it reads the Android HWC). Judge by eye or camera.
5. **(B) E Ink key:** go to a page with text (for example Settings), then press the E Ink key once.
   - Expected: one black/white flash and the same page redrawn, without ghosting.
   - `adb shell 'tail -20 /tmp/recovery.log'` must show `E Ink key: full refresh`, then
     `hmct_epd: full refresh`, then `epd SwTconThread out frame: 38`.
   - Press it 3 times quickly: still one redraw at a time and no hang (`ps -A | grep recovery`
     should show `do_sys_poll`, not `futex_wait`).
   - Volume keys and touch must still work afterwards.
6. **Reboot does not show the image:** Reboot > System (or `scripts/twrp.sh exit`). The phone must
   reboot straight to Android with no "DEVICE POWERED OFF" screen. Then run `scripts/a9-health.sh`.
7. **(A) Power-off, log run (USB attached, automatable):** enter TWRP again. In one terminal run
   `adb shell 'tail -f /tmp/recovery.log'`, and in another `adb shell twrp reboot poweroff`.
   - Expected in the tail before the connection drops: `hmct_epd: still image
     /system/etc/hmct_epd_poweroff.png on the panel after ~NNN ms` (NNN about 400-700).
   - The glass shows the Hisense "DEVICE POWERED OFF" screen. With USB attached, the off-mode charging
     splash may replace it afterwards. That is expected, not a failure.
   - `no idle confirmation within 4000 ms` means the idle test never matched. The image may still have
     been drawn; report it.
   - Then the phone is off or charging. Power it on (hold Power, or unplug and replug, as for any
     off-mode charge). It boots Android on the active slot a. Run `scripts/a9-health.sh`.
8. **(A) Power-off, real test (needs the user, USB unplugged):** in TWRP, Reboot > Power Off with the
   cable unplugged. The glass must keep the "DEVICE POWERED OFF" image. The user powers the phone on
   and reconnects USB, then run `scripts/a9-health.sh`.
9. **Not covered, for the record:** `adb reboot -p` from TWRP leaves the last TWRP screen (§3).
10. **After:** `scripts/a9-health.sh` must exit 0. The golden dump keeps the old TWRP on both recovery
    slots. Note in `docs/state-log.md` which TWRP the phone now runs. If it is kept, `data/golden.tsv`
    lags for `recovery_a`/`recovery_b`.

## 7. Rollback to the current good TWRP

`$A9_DATA/recovery/twrp-HLTE556N-31434216263.img` (sha256 `077cac63…061b`):

```bash
scripts/flash-recovery.sh $A9_DATA/recovery/twrp-HLTE556N-31434216263.img --write
# manual equivalent:
adb reboot bootloader
fastboot getvar is-userspace          # MUST be "no" (rule 3)
fastboot flash recovery_a $A9_DATA/recovery/twrp-HLTE556N-31434216263.img
fastboot flash recovery_b $A9_DATA/recovery/twrp-HLTE556N-31434216263.img
fastboot reboot recovery              # TWRP comes up AUTHORIZED over adb
scripts/twrp.sh exit                  # clears the BCB, back to Android
```

A recovery that does not boot does not consume the slot retry budget, and Android on slot a is not
affected (a9-twrp skill). If the phone is stuck anyway, follow the `a9-recover` skill.

## 8. Offline verification of the image (measured, 2026-10-14)

Image `twrp-HLTE556N-38624343750.img` (100,663,296 B, release asset `recovery.img` of run 38624343750),
compared with the good build `31434216263` and the device's stock `recovery_b`
(`$A9_DATA/Backups/2026-08-08_055553/recovery_b.img`):

| Check | New image | 31434216263 | Stock recovery_b |
|---|---|---|---|
| Boot header version / page size | 2 / 4096 | 2 / 4096 | 2 / 4096 |
| kernel / ramdisk / tags addr | `0x8000` / `0x1000000` / `0x100` | same | same |
| Kernel | 15,047,394 B, byte-identical to `prebuilt/kernel` | same | same bytes |
| `recovery_dtbo` | **88,722 B** at 41,082,880, byte-identical to `prebuilt/dtbo.img` | 88,722 B at 41,062,400 | 88,722 B at 26,202,112 |
| DTB | 327,956 B, = `prebuilt/dtb.img` | same | same |
| `scripts/abl-check.py --target recovery` (the phone's own ABL, emulated in Unicorn; not a boot proof) | PASS (header, dtbo, dtb overlay) | PASS | n/a |

Ramdisk (gzip cpio), new versus 31434216263, file by file sha256:

- Added: `system/etc/hmct_epd_poweroff.png` (sha256 `2470a419…`, the device-tree file).
- Changed: `system/bin/recovery` (contains `E Ink key: full refresh` and the PNG path),
  `system/lib64/libminuitwrp.so` (contains the `hmct_epd: full refresh` / `still image` / `idle wait`
  strings). `prop.default` changed only in its build timestamps, and `ramdisk-files.*` are the build's
  own listings. `system/etc/twrp.fstab` changed in comments only: device-tree commit `4e09410` landed
  after build 31434216263.
- Unchanged: everything else, including `system/lib64/libhmct_epd.so` (`2b187fca…`, = stock
  `librecovery_ui.so`).

Not checkable offline: that the kernel boots, that the GUI renders, the key, and the power-off behaviour.
Those are covered in §6.

## 9. Reproduction

```bash
# disassembly (capstone + pyelftools); dis.py from epd-waveform-source "Reproduction", renamed:
# a file called dis.py shadows the stdlib module and python then fails at import
nix-shell -p llvmPackages.bintools-unwrapped --run 'llvm-readelf -rW work-epd/librecovery_ui.so > rel.txt'
REL=rel.txt nix-shell -p python3Packages.capstone python3Packages.pyelftools --run \
  'python3 -P edis.py work-epd/librecovery_ui.so _Z18ForceClearGhostingi ModeDecision_MirrorMode \
     _Z16WaitForNextImagev Update_lookUp_table _Z9drawImageP9GRSurface _Z19NormalModeWaitImagev \
     0x3b164:0x174 0x3adb4:0x3b0'
# the 40-byte table in front of GC16
python3 -I -c "d=open('work-epd/librecovery_ui.so','rb').read(); print(d[0x1a860:0x1a8a0].hex(' '))"
# image header / kernel / recovery_dtbo check (the script in §8)
```
