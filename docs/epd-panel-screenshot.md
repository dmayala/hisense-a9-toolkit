# E Ink software target snapshots: reading the composer's transition buffer

**Date:** 2026-10-11
**Status:** Working; decoded image matched one photo of the panel (§5). Tools:
`scripts/epd-snap.sh` (snapshot) and `scripts/epd-watch.sh` (refresh timeline).
Measured on slot b, testkey LOS 23.2 (`BP4A.251205.006 test-keys`), stock vendor
and kernel (`4.19.157-perf`, built 2022-12-28).

`screencap` reads SurfaceFlinger's composition, not the panel ([a16 findings
§9](a16-composition-and-epd-findings.md#9-sensor-discipline)). This doc describes
a software snapshot: the old/new gray targets held in the vendor composer's
transition buffer. The decoded target matched the structural content of one
panel photo (§5). It does not read pigment state or measure optical gray levels,
residual ghosting, contrast, or completion of a physical update. A camera or
human observation is separate evidence about the glass.

Labels follow the repo convention: **MEASURED** (with the command) or
**INFERRED**.

---

## 1. TL;DR for agents

```bash
scripts/epd-snap.sh /tmp/panel.png        # new software target; panel-prev.png = old target
scripts/epd-watch.sh /tmp/watch 60        # PMIC sleep-event snapshots: target vs screencap
adb shell su -c "logcat -d" | grep 'epd::'  # the E Ink engine's own log
```

- The first `epd-snap.sh` run after each boot **pulls the notification shade down
  and back up** to find the buffer. Do that while the display works, before
  reproducing a freeze. Later runs read the buffer without UI discovery.
- Compare `panel.png` with `screencap`. **A difference is a software-target/
  SurfaceFlinger composition divergence**. Use separate optical evidence to
  establish what the glass displayed.
- When the display is off (`epd 1v8 power off`, `mWakefulness=Dozing`), the glass
  retains an image and `epd-snap.sh` can still read the last software target;
  `screencap` then returns black. Retained target bytes do not measure physical
  residue or guarantee that the panel completed the requested update.

## 2. Evidence for a userspace E Ink engine in the HWC

**MEASURED** — `logcat` from `vendor.qti.hardware.display.composer-service`
(pid 1614 on this boot), tag `SDM`, after a single swipe:

```
SDM : epd:: epdImageDispose
SDM : epd:: DitherSimpleStyle
SDM : epd:: clear=0,temp=26,27,mode=0x206
SDM : epd::PanelOpen start
SDM : Minui-DRM::AtomicPopulatePlane: AtomicPopulatePlane End
SDM : epd::PanelOpen complete i=4,wakelock ret=0
SDM : epd:: ------------------ total frame num=8
```

**MEASURED** — strings in the composer binary (pulled with
`adb shell su -c "cat /vendor/bin/hw/vendor.qti.hardware.display.composer-service"`):
`epd:: EinkSwTconInit start`, `epd:: update step1..5 image`, `epd:: DU to G16`,
`epd:: G16 to DU`, `epd:: DitherMatrixStyle` / `DitherSimpleStyle` /
`DitherSStyle16`, `epd::clear ghosting`, `epd:: CommitBitMap`,
`epd:: BitMapModeGetImage`, `EpdUpdateThreadHandler`, `GetEpdTargetBuffers`,
`epd:: wait next frame timeout`, `epd:: queue is empty`, and a whole `Minui-DRM` /
`Minui::gr_init` path (DRM dumb buffers, its own atomic commits).

So the pipeline is:

```
apps -> SurfaceFlinger -> vendor HWC composer
                           |- normal SDM path (layers, HWC/GPU composition)
                           '- epd engine (thread EpdUpdateThreadHandler):
                                take the composed frame -> threshold/dither (mode 0x206)
                                -> compute 8 waveform frames (temperature-dependent)
                                -> push them via Minui-DRM dumb buffers
                                   -> DSI -> tc358767 bridge -> panel source drivers
                                      (fp9929a PMIC powered only during the cycle)
```

**INFERRED** — "software TCON" (`EinkSwTconInit`) and the paths above indicate
userspace waveform processing. The examined sysfs nodes expose no pixel
readback, and this method reads the composer's software transition targets from
system RAM. These observations do not establish a complete hardware inventory
or make those targets a measurement of physical pigment state.

Related facts:

- **MEASURED** — `epd_display_mode` (`/sys/devices/platform/soc/soc:qcom,dsi-display-primary/`)
  reads `518` = `0x206`, the `mode=` the engine logs. The other nodes there
  (`epd_contrast`, `epd_black_threshold`, `epd_white_threshold`, `epd_force_clear`,
  `epd_commit_bitmap`, `epd_info` → `vcom: 0`, …) are knobs. None returns pixels.
  The composer itself reads and writes `epd_info` and `epd_xon_en`
  (paths in its strings). For `epd_thermal`, the reviewed acquisition path opens
  read-only; composer write handling is unestablished. Missing direct path/string
  references do not prove no writer exists; see the
  [temperature-input notes](a9-temperature-inputs.md).
- **MEASURED** — the engine logs `epd:: queue is empty` and
  `epd:: wait next frame timeout` / `continue wait next image` every ~3 s while
  idle. These are normal and are **not** a freeze signature by themselves.
- **MEASURED** — on screen-off the engine logs
  `HWCDisplay::SetPowerMode: epd:: power mode = Off`, `epd:: Blank---`,
  `epd:: enter idle mode`; after a refresh, `epd::PanelClose complete`.

## 3. Where the software transition targets live

All buffers are anonymous heap (`[anon:scudo:secondary]`) mappings in the composer.
Inventory on 2026-10-11, pid 1614 (addresses change per boot):

| Size (bytes) | Count | Content | Role |
|---|---|---|---|
| **2,719,744** | 7 | 16-bit/pixel, 824×1648 | **one is the live transition map (below)**; two are constant fills (`0x0F`, `0xF0`); one holds an older posterised image; the rest are zero. **MEASURED**: only the live one changed across two screen transitions |
| 1,359,872 | 3 | 8-bit, 824×1648 | engine work image. Held the dithered lock screen during an update, **all zero a few seconds later**. Transient. **MEASURED** |
| 1,372,160 | 1 | 8-bit, 832-stride | mostly zero. Role unknown |
| 1,486,848 | 8 | values 0/85/170/255 | **INFERRED**: the 8 waveform drive frames (`total frame num=8`) of the last update. They change on every update |
| DRM dumb buffers | 2 | `/dev/dri/card0` mappings | the frames sent to the panel. `/proc/pid/mem` returns EIO (PFN-mapped), so they cannot be read this way. **MEASURED** |

### The live buffer format

**MEASURED** (decoded and checked against a photo of the panel, §5):

- Allocation 2,719,744 bytes. Pixel data is **end-aligned**: the last
  `2 × 824 × 1648` bytes. A 3,840-byte allocator header precedes it.
- **Panel-native orientation**: 824 rows of 1648 pixels, row-major. Upright
  portrait pixel `(x, y)` = native `(row = 823 − x, col = y)`.
- 16 bits per pixel. The **high byte is always 0**. The **low byte packs the
  last transition**: `(old_grey << 4) | new_grey`, 4-bit grey levels.
  - `new = low & 0x0F` → **new software gray target**
  - `old = low >> 4` → old software gray target for that transition
  - Decoding maps each nibble to `level × 17` in a PNG. These are software gray
    codes, not measured optical luminance or residual pigment levels.
- In mode `0x206` the values seen are only `0x00`, `0x0F`, `0xF0`, `0xFF`
  (black/white, i.e. 1-bit dithered). Other modes presumably use more levels.

The first decoding attempt rendered the raw low byte and got old and new
superimposed. That is how the first "frozen shade" false alarm happened (§5).

### Finding the live buffer

There are seven allocations of the same size, so size alone doesn't identify it.
`epd-snap.sh` dumps all seven, causes one known visible change (shade down), dumps
again, and keeps the **one** that changed. The address is cached in
`~/.cache/epd-snap/<boot_id>-<pid>` and re-checked against `/proc/pid/maps` on
every run. Picking the candidate that best matches `screencap` would be circular:
in a freeze the two legitimately differ.

## 4. Methods tried that do NOT work (so nobody retries them)

| Method | Result |
|---|---|
| `screencap` | SurfaceFlinger's composition. Shows colour wallpaper, status and nav bars that the panel may not show (§5). **Not a panel sensor** |
| Qualcomm HWC frame dump with CWB output: `adb shell su -c "vndservice call display.qservice 21 i32 <count> i32 1 i32 2 [i32 <fmt> i32 <tap>]"` | Works mechanically (`HWCDisplay::DumpOutputBuffer: … is Successful`, files in `/data/vendor/display/frame_dump_disp_id_00_builtin/`), but **every frame is all zeros**, at RGB_888 and RGBA_8888, tap points LM and DSPP. **INFERRED**: the epd engine drives the panel through its own Minui-DRM path, so concurrent writeback sees nothing. Leftover dumps in `/data/vendor/display/` can be deleted |
| Kernel sysfs `epd_*` nodes | Settings only (§2). No pixel readback in the driver strings either |
| `/sys/kernel/debug/dri` | Not mounted (no debugfs on this kernel config) |
| Reading DRM dumb buffers via `/proc/<pid>/mem` | EIO (driver-mapped memory) |
| Camera | Separate optical evidence. The photo in §5 validates structural content once; controlled camera measurements are needed for gray accuracy, residual ghosting or update timing. Re-validate the decoder after vendor or firmware changes |

## 5. Verification and what the panel showed

**MEASURED** — 2026-10-11 ~08:10–08:14 (device time), Treble app
(`me.phh.treble.app/.SettingsActivity`) in front, screen dozing after 08:11:58:

- `epd-snap.sh` (new nibble) showed the Treble **Settings list with no status bar
  and no navigation buttons**, just the divider line at the bottom.
- A **photo of the panel** taken by the user at the same time shows exactly that:
  Settings list, blank status-bar strip, blank nav strip.
- `screencap` at the same moment showed Settings **with** the `8:10` status bar and
  icons **and** the nav buttons.
- The old nibble showed the expanded notification shade, which `epd-snap.sh`'s
  discovery had pulled down and back up at 08:10:14. That was the last update the
  engine processed (`logcat` `epd::` lines end at 08:10:14; `epd::PanelClose` at
  08:10:20).

So the panel and SurfaceFlinger **disagreed about the status and nav bars** after
the shade collapsed, and the panel kept that state. **Observed once, not yet
reproduced.** **INFERRED**: the engine updated only part of the frame (partial
update region) and never redrew those strips, or SurfaceFlinger redrew them in a
frame the engine never received. Either way this is a real panel/SurfaceFlinger
divergence of the kind the freeze is (see a16 findings §10), small enough to
investigate safely.

**Resolved the same day.** It was the freeze mechanism: HWC composition was on, and
layers composed in hardware never reach the E Ink engine (a16 findings §12). After
the fix, `epd-snap.sh` shows the status and nav bars in the software target.

Also **MEASURED** on this build: home screen and lock screen snapshots show the
colour wallpaper thresholded to black, a low-contrast or dithered clock, and
dithered icons. Several captures showed half of a notification card dithered and
half flat, i.e. partial-region processing.

False alarm, recorded so it is not repeated: an early decoder rendered the raw low
byte. That showed the shade "still on screen" 40 s after it had been collapsed,
and looked like a lost frame. The photo disproved it. The decoder now splits the
nibbles.

## 6. Panel power lifecycle timeline (kernel)

**MEASURED** — observed EPD power cycles produce, in `dmesg` (root):

```
[328:crtc_commit:72]epd power on
[328:crtc_commit:72]fp9929a_set_register: epd write vcom num=1, vcom =2730
[328:crtc_commit:72]fp9929a_active_mode: epd temperature = 26
[328:crtc_commit:72]epd fp9929a active end, state=0
[328:crtc_commit:72]tc358767_send_init_cmd, epd ret=-107
[328:crtc_commit:72]epd fp9929a_sleep_mode start            (~3-8 s later)
[328:crtc_commit:72]epd fp9929a enter sleep end, state=1
```

and on screen-off `epd epd_panel_xon_off  start delay work`, `epd 1v8 power off`.
`epd-watch.sh` follows this with `dmesg -w`.

- **MEASURED** — `tc358767_send_init_cmd, epd ret=-107` (ENOTCONN) appears on
  **every** power-on, in the good state too. Background noise, not a fault signal.
- **MEASURED** — several commits share one power cycle: the PMIC stays up a few
  seconds after the last commit.
- A PMIC power-on/sleep pair is not an engine target-update count, a waveform
  subframe count or an optical completion acknowledgement. Keep these units
  separate when reporting cadence; `epd-watch.sh` snapshots at PMIC sleep events
  can cover multiple engine updates.
- `sde-crtc-0/retire_frame_event` and `measured_fps` (`/sys/class/drm/sde-crtc-0/`)
  are readable without side effects and change when the display controller pushes
  frames. They're usable as a secondary counter, but the dmesg cycle is clearer.

## 7. Hazards found while doing this

- **Reading `/sys/bus/i2c/devices/0-0048/power_en` (fp9929a) logs
  `epd pmic_active = N` to the kernel log on every read.** Polling it floods
  `dmesg` and pushes out the real EPD events. **MEASURED.**
- **Reading `fp9929a` `temperature` or `thermal` powers the EPD PMIC up**
  (`mdss_epd_fp9929a_power_up, enable=1` from the reading process), even when
  the panel's 1.8 V rail is off. It then fails I2C with `-107` and
  `epd read TMST1 error, ret=-5`. **MEASURED** (`[7419:head]` in dmesg was the
  probe's own `head`). Nothing broke, but don't read these nodes.
- **The phone's `sh` does 32-bit `$(( ))` arithmetic.** `$((0x6f4c894000/4096))`
  evaluates to `313492`, so `dd skip=` reads the wrong address and
  `/proc/pid/mem` returns EIO. Compute offsets on the host. **MEASURED.**
- `adb shell su -c '…$(…)…'` expands `$(…)` as the **shell** user before `su`
  runs (`pidof` of a vendor process then comes back empty). Put multi-step root
  commands in a pushed script, or quote as the tools do.

## 8. Reproduction (no tools)

```bash
P=$(adb shell su -c pidof vendor.qti.hardware.display.composer-service | tr -d '\r')
adb shell su -c "cat /proc/$P/maps" | awk '{split($1,a,"-"); if (strtonum("0x"a[2])-strtonum("0x"a[1])==2719744) print a[1]}'
# dump one candidate (offset computed on the host):
A=6f32306000
adb shell su -c "'dd if=/proc/$P/mem of=/data/local/tmp/b.bin bs=4096 skip=$((0x$A/4096)) count=664; chmod 644 /data/local/tmp/b.bin'"
adb pull /data/local/tmp/b.bin
# decode: last 2*824*1648 bytes, take every even byte, new = b & 15, rotate (see epd-snap.sh)

# engine log, composer strings
adb shell su -c "logcat -d" | grep 'epd::'
adb shell su -c "cat /vendor/bin/hw/vendor.qti.hardware.display.composer-service" > hwc.bin
grep -a -o -E '[[:print:]]{5,}' hwc.bin | grep -E 'epd::|Minui|EinkSwTcon'
```

## 9. Open questions

- Which update modes (`epd_display_mode` values, G16 vs DU) the live buffer covers.
  Only `0x206` was observed. A G16 update should show 16 grey levels.
- Whether the status/nav-bar divergence (§5) reproduces, and whether the engine
  skips regions or SurfaceFlinger never sent them.
- Whether the composer's dithered input image could be read too: the transient
  8-bit buffer, to tell "engine got a frame but rendered it wrongly" from "engine
  never got the frame".

## References

- [a16 composition and EPD findings](a16-composition-and-epd-findings.md) — §8/§9
  instruments, §10 the open freeze question
- `scripts/epd-snap.sh`, `scripts/epd-watch.sh`
