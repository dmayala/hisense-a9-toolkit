# Mainline E Ink driver (a9_eink): in-kernel software TCON for Android

**Date:** 2026-10-15. **Status:** written and checked offline only. The driver compiles against linux 7.2.10 (with
patches 0001-0005 and the new 0006-0009), and the host tests pass. **It has never run on the phone.** No adb,
fastboot or edl was used for this work. Sources and integration notes:
[`scripts/mainline/eink/`](../scripts/mainline/eink/README.md).

> **Correction 2026-10-15 (boot 12, MEASURED; read before anything below).** The first real drive (E5c) left
> the glass unchanged. Every 32-bit word the panel scans is byte 0 = drive data **plus bytes 1..3 = a fixed
> CONTROL TEMPLATE** (byte 1 gate clocks / source strobes, byte 2 0x00, byte 3 0xff) that stock builds once at TCON
> start and never overwrites. This driver used to zero bytes 1..3, so the panel was never clocked. Since patch
> 0028 the template is loaded from `/lib/firmware/hisense/a9-ctrl-template.bin` (made from the user's own composer
> by `scripts/mainline/eink/tools/a9ct-extract.py`, vendor data, never committed; one accepted sha256
> `a88ae8ef…`), both transport buffers hold it, every frame is composed as byte 0 | template, and every "zero"
> frame is the template itself. The XR24 lane mapping is MEASURED correct (stock BA24 = data on B, control on G):
> **do not try XB24** (the driver now refuses it). Earlier statements here that bytes 1..3 are zero, and every
> "byte-identical to stock" result, covered byte 0 only. Current state and handoff:
> [mainline-eink-steps.md](mainline-eink-steps.md), top section.

Related docs:
- [mainline-plan.md](mainline-plan.md): §2 has the stock sequences, §4/§4.5 the TCON.
- [mainline-eink-compat.md](mainline-eink-compat.md): the userspace contract this driver implements.
- [mainline-eink-steps.md](mainline-eink-steps.md): the staged enable plan E1-E4. This driver adds stage E5 to it.

Labels:
- **MEASURED**: read from a file, a disassembly, a compiler or a test run. The command is in the appendix.
- **INFERRED**: deduced, not observed.

## 1. What it is

```
SurfaceFlinger / drm_hwcomposer (or a9eink-pattern)
   | 824x1648 portrait, XR24/AR24/XB24/AB24/R8, damage clips
   v
a9_eink KMS card (shmem GEM, 60 Hz vblank timer)
   plane atomic_update: damaged area -> stock luma + rotation -> landscape grey shadow,
   recording only the pixels whose grey value changed
   v  kthread, one update at a time; the newest content is coalesced
engine (a9_eink_engine.c over the a9tcon core): mode-switch steps, conditioning,
   complete conditioned target -> target-difference bounds, pack, waveform frames
   v
transport: drm_client on the msm card, two 448x829 XR24 dumb buffers, one blocking
   atomic commit per frame (85 Hz) -> DSI0 -> TC358767 -> source drivers
   msm CRTC on = panel prepare (lines, FP9929A up) ... CRTC off = FP9929A down
```

- **The a9tcon core is used unchanged.** `scripts/mainline/tcon/a9tcon.c` is copied into the kernel. Its
  `<stdint.h>`/`<stddef.h>`/`<string.h>` map to kernel headers through `driver/kcompat/`. It needs only memcpy and
  memset, and it matches the stock composer in 174/174 checks per image (§4.5 of the plan).
- **Waveform tables come from a firmware blob.** `request_firmware("hisense/a9-waveform.bin")`, loaded at the
  first update. The blob is made by `tools/a9wf-extract` from the user's own composer, whose sha256 `8c89557b…`
  is checked.
  - Format: an 80-byte header (`"A9WF"`, version 1, the header and payload sizes, the composer's sha256, the
    payload's sha256), then the raw `struct a9t_tables`, 1,182,899 bytes.
  - The kernel checks the header, the payload sha256 and every table's length and code range (`a9e_wf_check`).
  - **The blob holds vendor data.** Never commit it. Keep it on the device or under `~/A9-build`.
- **Why it finds the msm card through the panel.** The TC358767 driver wraps the transport panel in a
  `drm_panel_bridge` whose `of_node` is the panel's node. After msm binds, that bridge's `->dev` is the msm
  `drm_device`. The driver retries at every update until the card is there, so module load order does not matter.
- **Memory:** about 26 MB of vmalloc.
  - Tables 1.2 MB.
  - Grey, levels and shadow 4 × 1.36 MB.
  - Pairs 2.7 MB, plus 5 step arrays of 2.7 MB each (persistent, as in stock).
  - Frame cache and staging 2 × 1.5 MB.
  - Two msm dumb buffers of 1.5 MB.

## 2. Userspace interface (aligned with mainline-eink-compat.md)

Sysfs group `eink` on the connector, `/sys/class/drm/cardN-DSI-1/eink/`:

| Attribute | Behaviour |
|---|---|
| `mode` | 0644. Accepts 513, 515, 518 and 521 (Balanced, Clear, Smooth, Speed); anything else returns EINVAL. Default 518. Takes effect at the next update through the stock transition builder. `a9_eink.any_mode=1` (testing) accepts any stock mode word with low byte 0..10. |
| `black_threshold`, `white_threshold`, `contrast` | 0644, 0..100, defaults 30/30/0. Applied to g2/g16 (`a9t_set_controls`). Since patch 0037 an actual value change queues a full Clear redraw, including a static UI; same-value writes do nothing. Defaults and stock arithmetic are unchanged. |
| `force_clear` | 0200, only `1`. Queues a full-screen Clear `0x203` update with clear = 1 (type-2 table, no mask) of the newest composed image, even when nothing changed. `mode` stays as set. The engine history keeps pre = 3, so the next steady update runs the stock 3 -> mode transition. Returns ENODEV without a worker. |
| `manual_clear` | 0200 root, only `1` (0041). Queues a full-screen, unmasked clear of the newest image in the configured steady mode when committed history is known and no initialization, recovery or mode entry is required. Smooth/Speed retain their full mode-specific clear steps, conditioned tone and committed mode. Unknown/initial/aborted state, rail recovery, mode-entry and existing forced Clear requests keep the conservative GC16 path. The compat socket's `r` writes only this attribute; it cannot override the kernel's decision. Returns ENODEV without a worker. |
| `test_pattern` | 0600 root diagnostic (0037). `contrast` holds black/white/ramp/grey-27 input through normal full Clear jobs; `off` restores newest UI shadow through full Clear. Never changes drive gates or fault state. See [depth analysis](mainline-eink-depth.md). |
| `status` | 0444, **not in the contract**. Gates, waveform, transport and HV state, counters, last update. |

**Deviations from the contract:**

1. **Card number.** The contract names `card0-DSI-1`. This card registers when its module probes. The msm card
   registers when msm binds. Which one gets `card0` depends on load order. The msm card also has a DSI connector,
   but it has no `eink` group. The daemon should find the path through
   `/sys/devices/platform/eink-display/drm/card*/card*-DSI-1/eink`, or the init must load `a9_eink` before msm.
   The exact platform path follows the DT node name `eink-display`.
2. **Clear-event coalescing.** Manual and forced stores coalesce until the worker takes a job.
   A stronger GC16 request can satisfy a pending manual request. Stores during an active job
   remain pending for a subsequent job. Stores reserve requests, without promising physical
   completion if admission refuses; the compat shim never replays `r`.
3. **Frontlight LED devices** (`a9:{warm,white}:{center,edge}`) are not part of this driver. They are separate
   AW99703/KTD3137 drivers, not written yet.
4. **E Ink key EV_KEY 766** is already in the DTS (`key-eink`, gpio-keys). It is not this driver's concern.
5. **ueventd permissions do not apply.** The `eink` group is created in the connector's `late_register`, after
   the add uevent. Set owner and mode from init.rc (`chown`/`chmod` on the real path). SELinux genfs labels are
   not affected.

**Contract behaviours:**
- **Draining.** Shutdown, suspend and remove wait (up to 15 s) for the queued and running updates to finish. A
  waveform is never truncated, except by the emergency stop below.
- **Display off keeps the image.** Disabling this card's CRTC (screen off, doze) neither draws nor clears.
- **No init flash on disable or clear.** The init flash runs once, before the first update after the tables load
  (the stock TCON start). `a9_eink.init_flash=0` turns it off.
- **The commit path does not block on the waveform.** Each commit's damage is copied into the grey shadow inside
  the commit, so the update keeps its input after userspace frees the buffer.

**Debugfs `a9_eink/`:**
- `grey`: the last update's 1648×824 grey.
- `levels_before`: the levels before it (dry run or `trace=1`).
- `levels`: the levels now on the glass (the real pipeline's model).
- `dry_levels`: the levels after the last dry-run update.
- `last`: mode, pre, clear, type, bin, t1/t2 (raw and used), rect, steps, and the frame count plus the sha256
  over all frames.

Use them to reproduce an on-phone update on the host:
```sh
a9tcon COMPOSER --grey grey --old levels_before --mode M --pre P --clear C --t1 T1 --t2 T2
```
The sha256 must be equal. A partial rect needs `a9e-test … --rect` instead.

**Module parameters** (all 0644 unless noted):

| Parameter | Meaning |
|---|---|
| `enable` (0) | Send frames. With 0 every update is computed and hashed but nothing is committed: a dry run. Clearing it during an update aborts after the current frame (the emergency stop). |
| `hv_hold_ms` (3000) | Idle time before the pipeline is powered down. Stock is 2-3 s (MEASURED, §5). With 0 it powers down right after each update. |
| `hv_max_ms` (60000) | Continuous HV-on cap, applied between updates. Stock has none; the longest seen was 33 s. |
| `prefill_frames` (12, patch 0038 / Review 19) | Initial ready-slot watermark, 2..32; writes outside range are refused. Snapshotted per posted job. Ring capacity stays 32; producer-done may admit shorter sequences. Empty-ring starvation still faults the trial; see [prefill design](mainline-eink-prefill.md). |
| `init_flash` (1) | Run the init flash once. |
| `trace` (0) | Hash the frames in enabled mode too. |
| `temp_fallback` (28) | Temperature used when a sensor reads nothing; 28 is the composer's start value. |
| `flag_6c7` (1) | Stock engine byte. |
| `mask_outside_damage` (0) | Not stock: drive nothing outside the damage. |
| `any_mode` (0) | Testing: let `mode` accept any stock mode word. |
| `transport_format` (XR24, 0444) | Fourcc of the transport buffers. XR24 only (patch 0028; XB24 refused: MEASURED lane mapping). |

**Review 22 correction (0040b).** A duplicate validates an already-on session
before skipping drive. Bad rail status from the temperature read is consumed
as the same observed sample; a later good PG cannot erase it. A fresh check
also precedes the skip. Off sessions stay off. Bad live samples retain 0039's
capture, latch, full recovery and ordered shutdown behavior.

The idle hold is now measured from the last successful real transport output.
Duplicates, rejected jobs and dry rendering do not restart it, including when
more duplicates are queued. Actual output renews the idle origin; `tx.on_since`
and FP max-on origins remain unchanged. This is a worker soft deadline:
blocked preparation/scheduling can delay its check, without granting a new
hold interval. With hold=0, actual output may reuse a queued successor; a nonphysical kick
powers down even when more work is queued.

## 3. Safety design

1. **Three gates, all closed by default.**
   - `a9_eink.enable` decides whether frames are sent.
   - `panel_hisense_hlte556n_eink.enable` decides whether the panel may be prepared.
   - `fp9929a.allow_power_on` decides whether the PMIC may raise the rails.
   - None of them goes on the command line. They are opened at runtime only (steps doc).
2. **HV is verified before the first frame.** After the CRTC is enabled, the driver reads `fp9929a_get_status()`
   (patch 0006). If the rails are not up, or PG is not at its "good" raw level 0, it sends no frame and powers
   the pipeline down. A gate refusal therefore never turns into frames on an unpowered panel.
   PG = 0 when on was MEASURED in 45/45 stock sessions.
3. **The power sequence matches stock** (MEASURED from the stock kernel, FP9929A agent):
   - Patch 0008 sets the order: DSI host on → panel lines → 10 ms → FP9929A (PWRUP, 5 ms, VCOM write with
     retries, TMST read, 20 ms, VCOM-EN) → DPU video on the idle buffer (the control template, byte 0 = 0; it
     was all-zero before patch 0028) → bridge init table → one more idle refresh → frames.
   - Stock initialises the bridge before its video engine; here the idle buffer covers the difference.
   - The FP9929A sequence itself is patch 0001, reviewed against the disassembly: no unsafe deviation.
4. **Every update ends on zero drive.**
   - Stock tables always end with a null frame (byte 0 zero everywhere; on the panel that is the template).
     The harness asserts it for every update it runs.
   - The transport additionally refuses to idle or power down on a frame with a non-zero byte 0: it commits
     the idle (template) frame first.
   - It lets the zero frame scan for at least 2 refreshes before CRTC off.
   - The panel stays off for at least 200 ms between sessions, as in stock `PanelOpen`.
5. **HV-off fallback.** If the power-down commit fails (for example -EBUSY because a userspace master grabbed the
   msm card) or the FP9929A still reports on, the driver calls `regulator_force_disable()` on its own `epd-supply`
   handle. That runs the stock power-down. Userspace must never be DRM master on the msm card.
6. **Underflow colour 0** (patch 0009, steps G16). With the stock value 0xff, an underflow would drive code 3 on
   whole lines.
7. **Aborts.** A failed commit or the emergency stop marks the glass unknown. The next update is a full clear.
8. **HV is re-checked at every update,** not only at enable. If the FP9929A dropped out, or PG is no longer good,
   while the pipeline was held on, the pipeline is powered down and the update is refused. After a forced
   power-down a fault latch refuses updates until a normal power-down succeeds. In that state the worker retries
   once a second; it never spins.
9. **Every pass ends blank.** If a waveform pass ever ended on a driving frame, a zero frame is committed at
   once, so a driving frame is never held through the hold time. The blob check also rejects tables whose last
   column (Clear, type 2, Balanced) or last init code drives. The user's composer passes (MEASURED).
10. **System sleep.** msm saves and restores its atomic state in `.prepare`. On resume it would re-enable a CRTC
    that was left on, and with it the rails. Two defences:
    - a PM notifier (`PM_SUSPEND_PREPARE`, before the freezer) drains the queue, parks the worker and powers the
      pipeline down;
    - the drm_client `.suspend` callback powers it down as well, before msm saves its state.
11. **Shutdown order.** A device link (this device → the msm device) makes this driver shut down and suspend
    before msm. The power-off image's clear therefore finishes before msm tears the CRTC down. If the drain times
    out, the FP9929A is forced down before `kthread_stop`.
12. **msm unbind** while the pipeline is on: the client's unregister callback runs the normal zero-frame
    power-down first. If that fails, the worker forces the FP9929A down.
13. **Dry runs never touch the real pipeline's state.** The init flash, the startup clear, the levels and the
    previous mode stay as they were. Enabling later still starts with the init flash and a full clear.
    `hv_hold_ms` is clamped to 10 s.
14. `depends on !DRM_FBDEV_EMULATION`: msm's fbdev client would otherwise scan console contents out to the panel.

**Not covered by this driver (steps doc G9):**
- a panic notifier that drops VCOM-EN/PWRUP when the kernel dies with HV up;
- a hard `max_on_ms` timer in fp9929a.

With HV on, a panic leaves the DPU scanning the last frame. That is normally the zero frame, but a panic in the
middle of an update leaves a drive frame on screen (INFERRED risk). This needs G9 in patch 0001.

## 4. Stock facts used (MEASURED by the two research agents unless marked)

- **Byte order.** The stock composer renders the TCON frame and shuffles it into a `BA24` dumb buffer, so TCON
  byte 0 → buffer byte 3 = the logical B channel. The stock kernel format table maps BA24 byte 3 to B. On
  mainline, XRGB8888 puts byte 0 in B. The stock DSI colour order `rgb_swap_rgb` equals mainline's `VID_CFG1 = 0`.
  - **Result: XR24 with the TCON frame as is**, which is the default here. This answers steps G14.
  - Do not use AR24/BA24 on mainline: with alpha 0, the premultiplied blend gives all zero.
- **DSI clock.** Stock bit rate = 85 × 24 × 562 × 839 = 961,896,720 b/s, so pclk = 40,079,030 Hz. With mode clock
  40079 kHz the byte clock is within 0.7 ppm (patch 0008). The bridge pixel PLL takes a DSI-derived input
  (IN_SEL=2, INFERRED), so this matters.
- **HSE.** Stock sets `h-sync-pulse = 1`, so patch 0008 adds `toshiba,dsi-hse` → `MIPI_DSI_MODE_VIDEO_HSE`.
- **Matching DSI settings.** Non-burst sync events, continuous clock, EoT, BLLP LP and lanes 0/1 all match. vdda
  = L18 at 1.232 V. Pinning it is steps G4.
- **Session rhythm.** The stock composer powers the panel down 2-3 s after its queue empties (a 3 s wall-clock
  timeout, measured 2.003-3.031 s). r001 had 165 updates in 17 sessions, r002 274 in 17. HV stays on between
  updates and scans the zero frame. There is no maximum HV time and no frame cap, and PG is only logged.
  - mainline-plan §2.3's "per-update enable/disable" is wrong. It is per session.
- **Temperatures.**
  - **t1** is the FP9929A TMST byte, sampled inside the power-up, about 7 ms after PWRUP. The composer uses the
    previous session's sample for the first update of a session. This driver powers up first and then reads, so
    its value is fresher than stock's. Values above 85 read as 0.
  - **t2** is PMI632 `epd_top_therm` in m°C, /1000, clamped 0..50. Mainline's 100k table is the same as stock's,
    but mainline interpolates in mV, not µV. That gives up to 73 m°C of difference, and a different integer for
    about 3% of ADC codes. An exact match needs a µV interpolation patch to `qcom-vadc-common` (open, small).
  - Stock `epd_thermal` never touches the chip. The FP9929A's own `temperature` attribute does (§7 of
    epd-panel-screenshot.md is partly wrong). Patch 0006 never does I2C.
- **VCOM.** The ABL passes `androidboot.eink_vcom=2730` to our mainline kernel too: MEASURED in the boot-2
  capture's cmdline (`runs/20261015T041539Z-mainline-first-boot`, not in git). That gives code 0x7e.

## 5. Offline results (MEASURED, 2026-10-15)

| Check | Result |
|---|---|
| `a9wf-extract` on the live composer copy | 1,182,979-byte blob, payload sha256 `109961e1…`. `a9e_wf_check`: all lengths fit, all drive codes ≤ 2, bin map < 14 |
| `a9e-test selftest` | RGBX full == `a9t_rgba_to_grey` (the stock-verified rotation); BGRX == RGBX of swapped bytes; R8 with an odd pitch; 50 random rects convert exactly their mapped landscape rect; changed-pixel bbox, and identical redraws give no damage; masked partial updates in 0x201/0x203/0x206/0x209 drive nothing outside and the same inside: 0 failures |
| engine vs a9tcon CLI (`run-tests.sh`) | **650/650 equal** (sha256 over all frames): 2 images × 2 old states × 8 modes × 5 previous modes × clear 0/1 × 2 temperature pairs, plus 3 init flashes and 7 chained updates that carry the engine's state |
| same, 2026-10-15, WHOLE words with the control template (`run-tests.sh`, template from `A9_CT_TEMPLATE` or `~/A9-build/mainline/firmware/hisense/a9-ctrl-template.bin`) | **652/652 equal**; plus `apitest` (a9e_ct_check rejects 11 corruptions + 3 lengths; `a9e_prepare`+`a9e_start` == `a9e_begin` in 96 scenarios with and without the init flash) and `bench` (host timings, report only); bytes 1..3 of every frame == template |
| kernel build (W=1) of `a9_eink`, `fp9929a`, `tc358767`, the panel and `dpu_encoder_phys_vid` | 0 warnings, 0 errors |
| link check instead of a full modpost | `nm -u a9_eink.o`: 157 undefined symbols. Every one except compiler/libc builtins is found as `EXPORT_SYMBOL*` in the tree (`drm_client_*` needs `CONFIG_DRM_CLIENT=y`, which the Kconfig selects), apart from 6 (`_dev_*`, `param_ops_*`) that are exported through macros. The a9tcon core object needs only `memcpy` and `memset`. Module text is 30 KB |
| `a9wf-extract` stricter check (no table pass may end on a driving column) | the user's composer passes |
| `a9eink-pattern` | static aarch64 ELF (musl), 81 KB. Not run: there is no DRM device on this host |
| host time per frame (i5-1235U, under a parallel kernel build) | 1.1-2.2 ms (Balanced/Clear/Smooth/Speed), 4.3 ms (0x303); conditioning per update 9-130 ms |

The transitive argument: the engine equals the CLI in every case above, and the CLI equals the stock code under
Unicorn (test-tcon-c.py, §4.5 of the plan, core unchanged since). So in these cases the kernel engine produces
the stock drive frames. Not covered: the kernel's own glue (commit pacing, transport, locking), which only the
phone can test.

## 6. First-enable procedure (for main; stage E5 of mainline-eink-steps.md)

Preconditions:
- E1-E3 of the steps doc passed.
- E3 proved the power path with zero frames: VCOM 0x7e, PG ok, `init_errors` 0, glass unchanged. (Those frames
  had bytes 1..3 zero too, so the panel was not clocked; since patch 0028 E3 sends template frames, and the
  no-waveform step `--e3t` checks a clocked, undriven panel with the user watching.)
- The image carries patches 0006-0009, the DTS patch, `CONFIG_DRM_A9_EINK=m`, `a9_eink.ko`, `a9eink-pattern`,
  and `/lib/firmware/hisense/a9-waveform.bin` from the user's composer.
- **The a9-kms player (G13) must not be running.** Its DRM master on the msm card would make this driver's
  commits fail.
- Battery ≥ 3.8 V, `epd_top_therm` 5-45 °C.

**E5a, dry run (no commit to msm, no HV):**
1. `modprobe a9_eink` (`enable` stays 0).
   - dmesg: `E Ink KMS 824x1648; frames dry run …`.
   - `/sys/class/drm/card*-DSI-1/eink/` exists on the a9_eink card.
2. `a9eink-pattern steps`.
   - dmesg: `waveform tables loaded (composer 8c89557b...)`, then
     `transport on msm_drm: 448x829 XR24 pitch 1792, frame period 11764 us`. This is the attach only; no commit.
3. `cat …/eink/status` and `/sys/kernel/debug/a9_eink/last`. Expect:
   - `updates 1`, `dry 1`;
   - the first update is `mode 0x203 clear 1 type 2`, after the init flash;
   - `bin` matches t1/t2.
4. Fetch `grey`, `levels_before` and `last` to the host. Run the CLI line from §2: the sha256 must match.
   - Proves on the phone: the A73 build of the engine, the grey/rotation of a real dumb buffer, and the firmware
     path.
5. `echo 518 > mode; a9eink-pattern box 100 200 300 400` → a partial update; reproduce it with `a9e-test --rect`.

**E5b, transport without HV (lines up, PMIC refused):**
1. Keep `fp9929a.allow_power_on=0`. Run `echo 1 > /sys/module/panel_hisense_hlte556n_eink/parameters/enable`, then
   `echo 1 > /sys/module/a9_eink/parameters/enable`.
2. `a9eink-pattern checker 64`. Expect `FP9929A is not on after enable: no frames sent`: the CRTC came up with
   zero buffers, then went down. Then `status`: `aborts 1`, `hv_on 0`.
3. Check that nothing else happened: no DSI or DPU errors, the bridge init logged at most the known single NACK,
   GPIO 36/86 low, glass unchanged.
   - Proves the drm_client modeset path on msm and the HV check, with no drive possible.

**E5c, first glass (all gates open, short sessions):**
1. Set `hv_hold_ms` to 0 (power down after every update) and keep `init_flash` at 1. Then open the gates:
   `a9-eink allow` and `echo 1 > /sys/module/a9_eink/parameters/enable`.
2. `a9eink-pattern steps`. Expected sequence:
   - `fp9929a … on: VCOM 2730 mV (code 0x7e), temp T C, PG 0`;
   - the init flash: 51-109 uniform frames, about 0.6-1.3 s, the whole glass flashing;
   - a Clear type-2 update, 30-104 frames by bin;
   - `fp9929a` power-down.
3. **On the glass** (human glance or photo; see the steps doc E4): the bootloader picture is gone and **16
   horizontal grey bands** are shown, black at the top and white at the bottom, sharp and with no stripes.
4. Read `status` and check:
   - `late 0` and `vbl_skips 0`. Any non-zero value means a frame was repeated (an extra drive pulse). Note it
     and stop before long sessions.
   - `max_gap_us` near 11764.
   - `aborted 0`.
5. Close the gates: `echo 0 > …/a9_eink/parameters/enable`, then `a9-eink deny`.
6. Then, one at a time:
   - `box` partial updates in 518 and 521;
   - `echo 1 > force_clear` (GC16 flash, same image);
   - mode switches 518 → 513 → 515 (the stock step sequences flash the whole screen).
   - `hv_hold_ms` back to 3000 only after all of these look right.

**What the glass tells you:**

| Seen | Meaning | Action |
|---|---|---|
| bands as expected | pipeline correct | continue E5c |
| nothing changes, logs clean, HV on | panel not clocked: control template missing or wrong (boot 12) | stop; `status` must show `template 1`; the lane mapping (XR24) is MEASURED correct, do NOT try XB24 |
| noise or garbage | bit order or DSI timing (HSE/clock) | stop |
| image inverted | drive codes 1/2 swapped | stop (would contradict stock) |
| stripes or one half wrong | pixel-word mapping or timing | stop |
| image fades or ghosts after power-down | HV-off path or VCOM | stop |
| grey wash, weak contrast | temperature bin or VCOM | check t1/t2 in `last` |

Emergency stop at any time: `echo 0 > /sys/module/a9_eink/parameters/enable`. The update aborts after the
current frame, a zero frame is committed and the pipeline powers down. `fp9929a.allow_power_on=0` makes the next
power-up fail too.

## 7. Risks (INFERRED unless marked)

**Panel safety:**
- **A repeated frame is an extra drive pulse.** It happens when a commit misses its refresh. Stock has the same
  exposure. It is counted (`late`, `vbl_skips`) but not prevented.
  - Host per-frame cost is 1-4 ms. The A73 cost is not measured. Plus a 1.5 MB write-combined copy per frame.
  - If counts appear: precompute frames before the session, or vectorise `render()`.
- **A panic or hang in the middle of an update holds a drive frame under HV.** Needs G9 (panic notifier in
  fp9929a).
- **`regulator_force_disable` fallback.** It leaves the regulator use count unbalanced (a warning at the next
  panel unprepare). That is accepted for an emergency.
- **Userspace master on the msm card.** It stops this driver's commits (they fail with -EBUSY). The fallback
  covers HV off, but Android policy must keep the msm card away from the composer.

**Correctness:**
- **First-update levels.** After the init flash the glass is taken as white (level 15); not verified. The first
  update is a full clear anyway.
- **Luma byte order.** Stock luma weights b0 = R for RGBA_8888 (INFERRED from the Android default). XR24 input is
  mapped so the weights stay on R/G/B.
- **t2 mismatch.** It can differ from stock by 1 °C for about 3% of ADC codes (MEASURED, see §4), which can
  change the bin at a bin edge.
- **Not ported:** the second stock grey source (16-bit `this+0x6a0`).
- **Unknown stock inputs:** who sets `this+0x6c7`. The default here is 1, as in the CLI.
- **Complete conditioned targets (0040).** Raw-luma damage still updates the full
  owned grey canvas, but no longer clips the conditioned target. The engine
  compares every conditioned nibble with committed history and derives its
  damage bounds from those differences. Diffusion can propagate beyond raw
  damage; local ordered/undithered transforms keep their existing arithmetic.
  Clear sentinel-256 neighbours are included in optional masked output bounds.
- **Redundant ordinary targets (0040).** A coalesced A→B→A or a raw-luma change
  that quantizes to the same final target returns before power-up. Eligibility
  requires known completed real history, normal flag_6c7 and unchanged mode,
  with no request
  steps/kind, clear, init, recovery, diagnostic or dry-run obligation. The
  low-level begin/frame API remains an unconditional renderer; the kernel
  caller uses prepare's `unchanged` result. Skips send no waveform, commit no
  history and renew no HV deadline/hold. This final-target equality is stronger
  than stock's observed raw-luma equality; it does not alter that raw predicate
  or claim optical state equality. After abort the existing forced-clear path
  prevents a skip. Controls still force their existing full Clear redraw.
- **Mode-switch steps and clears always run full screen.**

**Not validated:** frame pacing on msm (blocking commit = latch, INFERRED), drm_client behaviour across msm unbind,
suspend/resume, and Android composition on top (drm_hwcomposer + minigbm on this card).

## 8. Build status and review

- **Kernel tree:** `~/A9-build/mainline/eink-tree`, a git worktree of the integration tree at `a31646791`, branch
  `a9-eink`, holding 0006-0009.
- **Build directory:** `~/A9-build/mainline/build-eink`, the integration `.config` plus `CONFIG_DRM_A9_EINK=m`.
- **No full `Image modules` build.** The arm64 defconfig base builds thousands of modules. That ran for over an
  hour on a host shared with other agents' builds (load about 30) and was stopped. The changed objects were built
  with W=1 instead, and the module's symbols were checked by hand (§5). The integration build (build.sh) is the
  real link test.
- **Code review.** An independent read-only review found 8 bugs, all fixed before these patches were generated:
  - resume could re-enable the rails;
  - an HV-unverified hold after a forced power-down;
  - no blanking of a driving last frame;
  - a double-counted framebuffer offset;
  - a busy loop after a failed power-down;
  - shutdown order against msm;
  - msm unbind with the pipeline on;
  - dry runs consuming the init flash and the clear.
- **Low items still open:**
  - `fp9929a_get_status` holds no reference on the driver data. A concurrent unbind is prevented only by the
    `epd-supply` device link.
  - If DPU `wait_flush` times out, a commit returns 0 and the next frame could be written into the buffer being
    scanned (INFERRED). The `late`/`vbl_skips` counters would show it.

## Appendix: reproduction (offline)

```sh
cd /home/user/A9
C=~/source/a9-display-evidence-20261012/vendor/vendor.qti.hardware.display.composer-service
scripts/mainline/eink/test/run-tests.sh "$C"                     # selftest, apitest, bench + 652 engine-vs-CLI cases (~9 min; needs the template file)
nix-shell -p gcc gnumake --run 'make -C scripts/mainline/eink'   # extractor + harness
scripts/mainline/eink/build/a9wf-extract "$C" /tmp/a9-waveform.bin
# kernel: worktree of the integration tree, then the driver and patches
git -C ~/A9-build/mainline/linux-7.2.10 worktree add --detach ~/A9-build/mainline/eink-tree HEAD
(cd ~/A9-build/mainline/eink-tree && git am /home/user/A9/scripts/mainline/eink/patches/000[6-9]-*.patch)
cp ~/A9-build/mainline/build-7.2.10/.config ~/A9-build/mainline/build-eink/.config
echo CONFIG_DRM_A9_EINK=m >> ~/A9-build/mainline/build-eink/.config
cd ~/A9-build/mainline/eink-tree && nix-shell /home/user/A9/scripts/mainline/kernel/shell.nix --run \
  'make O=../build-eink ARCH=arm64 CROSS_COMPILE=aarch64-unknown-linux-gnu- olddefconfig && \
   make O=../build-eink ARCH=arm64 CROSS_COMPILE=aarch64-unknown-linux-gnu- -j8 W=1 drivers/gpu/drm/a9_eink/'
```
