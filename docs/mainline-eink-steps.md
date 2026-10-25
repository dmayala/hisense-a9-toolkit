# Mainline E Ink: staged enable plan (default OFF)

## HANDOFF: current state (2026-10-15 evening; read this first)

**Where it stands.** Mainline 7.2.10 boots on the phone diskless (boot_b + null dtbo_b, dead-man -> fastboot ->
golden restore; `scripts/mainline/first-boot.sh`). The E Ink power path is proven on the phone: E3 (boots 9-11), PG
active-high, VCOM 2730 mV, bridge init table, kernel caps (patches 0021-0027, nine independent reviews in
`runs/sol-e3-review-*.md`, local). **Boot 12 (E5c, first real drive, image `333374a2`): every machine criterion
except timing passed, and the glass did not change at all, not even the init flash** (user watched).

**Root cause (MEASURED offline).** Each 32-bit word the panel scans is byte 0 = 2-bit drive data **plus bytes 1..3 =
a fixed CONTROL TEMPLATE** (byte 1: row 1 0x08/0x0a, rows 2-4 gate clocks 0x02, row 5 and rows 6-828 per-word
strobes; byte 2 0x00; byte 3 0xff; byte 0 0), built once at TCON start by the composer (`HWCDisplay::SetPowerMode`,
emulated range 0x19ee98..0x19f054) and by recovery (`Init_Eink_SWTcon` 0x3c4c0..0x3c6e0: byte-identical template) and
never overwritten; stock's null frames are a memcpy of it (lookUpTable 0x1d1b54). All 16 live stock ring buffers
(`~/A9-build/mainline/b-diag/live-dumps/`, local) match it in bytes 1..3 on all 371,392 words; two equal it
entirely (the idle frame). Our transport zero-filled bytes 1..3, so the gate/source drivers were never clocked.
**Superseded claims:** "bytes 1..3 are zero / never written" (they are never written *after TCON start*), "try
XB24 if nothing changes" (XR24 is MEASURED correct: stock BA24 = data on B, control on G), and every
"byte-identical to stock" TCON result before 2026-10-15 (they compared byte 0 only; re-run on whole words below).
Also MEASURED fine at boot 12: DSI timing, panel GPIOs, DPU scanout (vsync +146 for 135 frames, underrun 0), all 25
bridge init writes.

**What changed (offline, NOT run on the phone yet):**
- Template provisioning: `scripts/mainline/eink/tools/a9ct-extract.py <composer>` (Unicorn emulation of the
  composer's builder; refuses any composer but `8c89557b…`; checks byte invariants and the payload sha256
  `a88ae8ef…`; `--cross-recovery`, `--cross-dumps`) writes
  `~/A9-build/mainline/firmware/hisense/a9-ctrl-template.bin` (80-byte `A9CT` header + payload, file sha256
  `dc9aa5ba…`). Vendor-derived: never commit it. `build.sh` with `A9_EINK_ASSETS=1` installs it and checks it.
- Patch 0028 (POWER/DRIVE PATH): a9_eink loads and validates the template (header, byte checks, payload sha256 ==
  header == compiled-in `A9E_CT_SHA256`); without it no update (dry or real) runs and the transport never powers
  up. Both transport buffers hold the template; each frame is composed in one pass as `(frame & byte-0 lanes) |
  template`, and the null decision is made on that byte 0; every "zero" frame (power-on idle refreshes,
  power-off, blank, zero_test) is the template itself. a9tcon's null frames clear byte 0 only. XR24 only.
  Status `template`.
- Patch 0030 (review 10): a slow incoming drive commit counts as drive time; E5c gates on the totals as well as
  the drive counters.
- Patch 0029 (frame pacing): the update's bin-independent set-up (`a9e_prepare`: request builder, step arrays,
  conditioning, pack) runs before power-up (boot 12's 47.8 ms gap sat between the init flash and the update,
  with HV on); refused updates restore the levels; real updates run at nice -10; drive-only counters
  `late_drive`, `vbl_skips_drive`, `max_gap_drive_us` (gaps with a drive frame on the glass); `prep_us`.
- Sequencer: new no-waveform step `e3t` (template frames, HV on 5 s, user watches; raw TC358767 status reads,
  clock rates, DPU state), template checks in `pre`, E5c gates on the drive counters, lighter samplers. Details:
  section "Control template (patches 0028/0029), `e3t`, session load" below.

**Tests (offline, 2026-10-15):** stall/transport harness 15/15 (new: no template -> no power-up; composition and
idle = template; drive counters); FP9929A fault tests unchanged and passing; sequencer simulator 271/271; engine
harness: engine == CLI on whole words with the template 652/652, split API == `a9e_begin` (96 scenarios), every
frame's bytes 1..3 == template; stock comparison under Unicorn on WHOLE words with the template 182/182 checks,
5,648 frames, 0 frames with bytes 1..3 != template on either side.

**Boot 13 (`--e3t` on `f37ba924`, run twice: `runs/20261015T193839Z-*` and the worktree run
`20261015T194701Z-*`, MEASURED):** pre PASS (template hash checked); **E3 A (HV ~3204 ms, 255 template frames) and B
(~5180 ms, 425) PASS: the first real gate clocking, and the user watched: the glass stayed perfectly still.**
e3t itself never started an update (updates +0, frames +0, HV 0, guardian at 15 s, mid reads empty). **Cause
(sequencer bug, from the driver code):** a9_eink only kicks an update for pixels that changed against its shadow
(`a9_plane_atomic_update` -> `a9e_fb_to_grey(..., &changed)`); e3t committed "white" right after e3b's "white", so
nothing changed. Fix: e3t uses "black"; the sequencer records what the shadow holds (`last_pattern`, white at
probe) and refuses to open any gate for a session whose pattern equals it (`<tag>.pattern` FAIL, latched); unread
registers now read `x` instead of empty. The simulator's fake client models the damage rule (`samefake`), and
`e3tsame` checks the guard; the old sequencer fails `e3t` in the simulator exactly as on the phone.

**Review 10 (`runs/sol-e3-review-10.md`):** `--e3t` GO; `--e5` GO-WITH-FIXES, both fixes in: E5c gates on the
total late/vbl_skips/max_gap again (in addition to the drive counters), and patch 0030 counts a slow incoming drive
commit as drive time (harness case 16, the reviewer's regression).

**Image (for review 11):** `~/A9-build/mainline/out-eink/a9-mainline-recovery.img`, sha256
`98bb6ee6a3a24f69dd14afb2fe7648f7163a4d4eb4ec476661628be3e9836594` (LOCAL ONLY: waveform + template inside; kernel
0001-0030), `build.sh check` PASS, `first-boot.sh --offline --e3t` and `--e5` PASS. Tests: sequencer simulator
285/285, transport harness 16/16, FP9929A all pass. (Previous image `f37ba924` = reviewed, boot 13.)

**Android pacing (patch 0033, 2026-10-15, offline):** runs 5-7 on mainline Android showed late drive frames
(late_drive 11-12, max_gap_drive 23.7-25.5 ms) and one frame-progress trip in run 7 (stall_fault, HV dropped;
the glass went logo -> black first). 0033: a producer thread renders a real update ahead into a 32-slot ring;
the worker starts the waveform only with the ring full (idle refreshes meanwhile) and then only composes byte 0
into the template buffer and commits. Status line 4 `pace commit_max_us commit_slow compose_max_us render_max_us
prefill_us ring_min underruns` tells on the phone whether the DRM/msm commit itself exceeds a period (then the
next step is a vblank-synced page-flip queue). Host harness (17)-(22): every frame sent once and equal to
byte 0 | template. Review 14 found 0033 **NO-GO**: missing job acquire, late-prefill admission, delayed emergency
before producer join, Android producer priority and unlatched starvation. **0035 supersedes its abort policy:**
unexpected errors latch the trial off; intentional cancellation may finish neutral without a latch; shutdown
precedes producer join and failed neutral removes HV immediately. See
[0035 ring safety and host validation](mainline-eink-ring-safety.md). Kernel patches 0032 (fixed-mode
CRTC for drm_hwcomposer, review 12 fixes, KUnit) and 0031 (PMIC OV) are in the Android build; the reviewed
diskless E Ink image `98bb6ee6` has 0001-0030 only.

**Next (each needs the reviewer first, then main runs it; the user watches the glass):**
1. `A9_MAINLINE_HUMAN=yes scripts/mainline/first-boot.sh --write --e3t` again on `98bb6ee6` (boot 13's e3t never
   ran): expected glass UNCHANGED; `capture/eink-seq/phone/e3t/regs.txt` has the bridge status.
2. Then `--e5`: expected the init flash, then 16 grey bands (black top, white bottom).
3. Open: per-frame cost on the phone at boot frequency is known only from E5a's dry run (~3.3 ms/frame); `prep_us`
   and the drive counters will measure it. The Android snapshot (`scripts/mainline/android`) needs 0028/0029 and
   the template pushed next to the waveform before its E Ink path can drive.

**Date:** 2026-10-15. **Status:** plan only, written offline (no phone access) from the code, the compiled DTB, the
stock DT and stock boot logs. **Nothing here is enabled in the default image.** The default image never loads the
display modules and never powers the FP9929A or the panel. Stage A is boot 2 itself (capture only); E1-E4 each need
the image changes listed (G1-G16) and an explicit, human-approved run. Labels: MEASURED / INFERRED as in
[mainline-kernel-build.md](mainline-kernel-build.md). The E Ink DRM/engine work of the parallel agent lives in
`scripts/mainline/eink/` and is integrated into the kernel tree only through reviewed patches.

Enable-flag procedure, summarised: the two power gates (`fp9929a.allow_power_on`,
`panel_hisense_hlte556n_eink.enable`) are **never** put on the command line; they are opened at runtime with
`a9-eink allow` only for the duration of one `a9-kms` run and closed with `a9-eink deny`. Module loading is staged
(`core` = dispcc + msm, `bridge` = + panel + tc358767) after G2. `build.sh check` must FAIL if a gate appears in the
cmdline (to add with E3).

## Boot 2 verdict against stage A (2026-10-15, MEASURED from `runs/20261015T041539Z-mainline-first-boot/capture`)

The capture was taken at uptime 3.3 s (dmesg ends at 3.56 s), so everything that only shows after the 10 s
deferred-probe timeout (sync_state lines) is missing; GPIO, genpd, `state_synced` and iio were not in the capture set.
Fixed for boot 3: a second capture `--late` 60 s later, and those reads added to `capture-dbg`.

| # | Criterion | Result |
|---|---|---|
| 1 | cmdline: beacon, `androidboot.eink_vcom=2730`, no gates | **PASS** (also `androidboot.boardid=2`; no `fp9929a.`/`panel_hisense`/`a9.eink`) |
| 2 | FP9929A probe | **PASS**: `at probe: PWRUP=1 VCOM_EN=1 PG=1` → `bootloader left the E Ink rails up: powering down` → `VCOM 2730 mV -> code 0x7e`. **Open:** PG read 1 with the rails up, while stock logs `state=0` when on; polarity not settled (G8: log PG again after the power-down) |
| 3 | GPIO state | **NOT CAPTURED** (now in `capture-dbg`) |
| 4 | i2c0 bound, IRQ count 0, no I2C errors | **PASS** (`4a80000.i2c` → `geni_i2c`, IRQ 97 count 0) |
| 5 | no modules, no `/dev/dri` | **PASS** |
| 6 | deferred only `59a0000.iommu` | **FAIL (benign):** also `4a88000.i2c` (touch bus) `Failed to get tx DMA ch`; GPI DMA is a module. Fixed: `dmas` deleted on `&i2c2` (FIFO mode) |
| 7 | sync_state pending, nothing forced | **NOT VISIBLE** at 3.5 s (late capture). The DT hold node (G3) is now in the image |
| 8 | no `disp_cc_*`; `gcc_disp_*` state | **PASS**: no dispcc rows; `gcc_disp_ahb_clk`, `gcc_disp_hf_axi_clk` hardware-enabled (splash path live). SMMU "preserved 0 boot mappings" |
| 9 | `usb30_prim` on; MMU TBU votes | **NOT CAPTURED** (now in `capture-dbg`) |
| 10 | QUSB2 rails, `epd_vpanel` off | **PASS**: l4 928 mV, l12 1.8 V, l15 3.08 V in use; `epd_vpanel` off; `tc358767_vddc` use 0 |
| 11 | no SError/faults/WARN/storm, ≥10 min | **PASS for the 134 s session** (only `psci: failed to set PC mode: -3`, harmless; cpufreq-hw "failed" is `cpufreq.off=1`). 10 min not tested: use `--stay 600` |
| 12 | sensors readable | **NOT CAPTURED** (iio now in `capture-dbg`) |

Verdict: nothing argues against E1. Boot 3 = the same safe default plus `--late` (criteria 3, 6, 7, 9, 12) and,
human-approved, `--e1`.

## Boot 3 verdict: stage A late items and E1 (2026-10-15, MEASURED, `runs/20261015T052915Z-mainline-first-boot/capture`)

Image `74cb6f24…`, `--stay 480 --late 300 --e1`. No reset, no USB loss in 383 s; released → fastboot; restore MATCH.

- **Stage A late items:** GPIO (criterion 3): 2 25 26 31 33 34 81 out high (the bootloader leaves the panel lines
  up), 36 and 86 out low (FP9929A off), 82 in low. sync_state (7): pending for gcc and every interconnect (named
  causes include `sync-state-hold`, `5e00000.display-subsystem`, `4a88000.i2c`, `1b8e300.pmu`); **rpmpd had already
  synced** (below). Deferred (6): none at 300 s except the expected GPU SMMU `59a0000.iommu` (probe gave up, -110).
  i2c0 IRQ count 0 throughout. PMI632 telemetry: one verified FV write, then read only.
- **PG polarity settled (G8):** PG read 1 at probe with PWRUP=1 VCOM_EN=1 and 0 after the driver's power-down, so it is
  **active-high**; the DTS is right, and the G8 suggestion to make it active-low is withdrawn. The power-good check in
  `fp9929a_enable` is still to be added.
- **E1 PASS.** `eink-load core` loaded dispcc-sm6115 and msm. `5e00000.display-subsystem`→msm-mdss,
  `5e01000.display-controller`→msm_dpu, `5e94000.dsi`→msm_dsi, `5e94400.phy`→msm_dsi_phy bound. No `/dev/dri` (no
  bridge, as designed). `mdss_gdsc` off after autosuspend; CX `on` (DPU vote). The only new message:
  `msm_dsi: supply refgen not found, using dummy` (optional supply). No SMMU fault, no WARN, GPIOs and i2c0 unchanged.
- **G3 decision:** the hold node keeps gcc and the interconnects pending (logged), but **rpmpd synced at boot anyway**
  (`state_synced` 1 for cx/mx/lpi_*; MEASURED). fw_devlink names no consumer of it: in 7.2 power-domain consumers attach
  to the per-domain genpd provider devices, not to the rpmpd platform device, so nothing holds its sync_state
  (INFERRED). Result: Linux's MX vote is 0 and CX is voted only by the DPU. 380 s with USB, I2C, UART and E1 showed
  no effect, because other masters keep MX/CX up. **Not a blocker for E2** (I2C reads and a bridge bind, no DSI
  traffic). **Gate for E3:** before DSI video and HV, check that the DSI PHY/controller vote MX/CX through their
  `required-opps` (stock votes MX at SVS or higher for the PHY). If they don't, add an MX vote to the DSI PHY node.

## Boot 4 verdict: E2 (2026-10-15, MEASURED, `runs/20261015T055506Z-mainline-first-boot/capture`)

Image `5d00d850…`, `--e2`. Safe: fastboot at t+173 s, restore MATCH.

- **E2a (after E1):** GPIO 2 25 26 31 33 34 81 all **high** (stock's full panel power-on state), 36/86 low. FP9929A at
  0x48 answers: reg 0x02 = **0x7e** (the bootloader programmed VCOM 2730 mV), reg 0x00 = 0x00. **TC358767 at 0x0f NACKs**
  (`I2C_RDWR: No such device or address`). i2c0 IRQ count 8.
- **E2b:** panel `448x829@85; driving refused`; `tc358767 0-000f: can not read device ID: -6`; probe failed and released
  VDDC (GPIO 31 low afterwards); no `/dev/dri`.
- **Why (stock, MEASURED):** the stock tc358767 driver has no power code and no I2C at probe (`tc358767_probe` only
  stores the client). All bridge power is `dsi_panel_power_on`: vddio (L9 1.8 V, 62 mA, 20 ms post-on) → reset(26)=0,
  power(33), xoe(34), mode(81), xon(25), vdcc_en(31)=1, mux(2)=1, 10 ms, reset=1. The first bridge I2C (stock kernel
  and UEFI alike) comes only after the DSI host is enabled with `force-clock-lane-hs`, and after `fp9929a_active_mode`.
  UEFI: `MDPPlatform_TC358767_Init` → FP9929A init (VCOM, `Epd Pmic Status = 1`) → `ChipID= 0x6601`. Stock kernel:
  `dsi_panel_enable` → FP9929A active → `tc358767_send_init_cmd` (one write NACKs every time, ret -107).
- **INFERRED cause:** every line was in stock's "on" state, so power GPIOs are not missing. Two candidates remain:
  - the bridge needs the DSI clock (the PXL PLL input is the DSI byte clock, IN_SEL=2). In that case E1's MDSS reset,
    which stops the bootloader's DSI, also took the bridge offline;
  - L9 is off (Linux never enabled it).
- **Changes (image `a004dbd68cb69cfb…`):**
  - L9 pinned at 1.8 V and made the input of `bridge_vddc`, with stock's 20 ms delay before VDDC (G5);
  - patch 0015: in init-sequence mode tc358767 tolerates an unreadable ID at probe (as stock) and logs the ID again
    right before the init sequence, once DSI is up. The bridge binds and the msm DRM device appears; nothing modesets;
  - `--e2` probes I2C once **before** E1 (bootloader DSI still live) and once after: ACK before, NACK after = the DSI
    clock is needed.
- **Handoff check:** stock does not power the rails down at probe. It writes VCOM on the live rails at 1.48 s
  (cont_splash) and first runs `fp9929a_sleep_mode` at 19.6 s, when the display stack takes the splash over. Ours runs
  the same stock power-down sequence earlier, at probe. The end state is the same, and the panel keeps its image
  unpowered. Later, when a9_eink drives the splash takeover itself, the driver may leave the rails up until then.

## Boot 5 verdict (2026-10-15, MEASURED, `runs/20261015T061823Z-mainline-first-boot`)

Image `a004dbd6…`, `--e2` with the I2C probe before E1.

- **E2a-pre (before E1): the TC358767 answers.** Reg 0x0500 = `0x01 0x66 0x00 0x00` (ID 0x6601), FP9929A VCOM 0x7e. The
  same read NACKed after E1 in boot 4. So the bridge needs the DSI host running (the bootloader's DSI was still live
  before E1; MDSS's reset in E1 stops it). This matches stock, which only talks to the bridge after DSI is on.
- **Regression: E1 lost the phone.** After `loaded dispcc-sm6115` the host could not connect any more. The phone
  reached fastboot at t+569 s, about when the 480 s stay ended and the dead-man fired, not after a 32 s watchdog bite.
  So userspace and the kernel kept running and only USB/networking stopped answering (INFERRED). No log of that window.
  Boot 4 ran the same E1 without the pre-probe and without L9 feeding the bridge VDDC.
  - Suspects: an interrupt storm on the single CPU (maxcpus=1) after msm resets MDSS under a bridge we had just
    woken; or the new L9 enable. Not decidable offline.
- **Fixes for boot 6 (image `69fd4fa6f9f7886d…`):**
  - live kernel log: init serves `dmesg; cat /dev/kmsg` on tcp 2324 and the host streams it into
    `capture/kmsg-stream.txt` for the whole session (QEMU: the stream ran up to the line before the reboot);
  - `a9-irqmon` in init logs every IRQ line that grows by more than 2000 in 5 s to kmsg, so a storm appears in the stream;
  - the pre-E1 probe is opt-in (`--e2pre`). `--e2` now runs E1 exactly as in boot 4, then the probe after E1, then E2b;
  - the host pings after each E step and stops sending commands if the phone stops answering;
  - **G9 done:** patch 0016, a panic notifier in the FP9929A driver (VCOM-EN low, 50 ms, PWRUP low, no locks, no sleep).
  - Reading the result: E1 survives → the boot-5 loss came from the pre-probe plus the MDSS reset (L9 cleared); E1 dies
    again → L9; drop it next.

## Bridge path, now that it needs the live DSI clock

- Bridge I2C is possible only while the DSI host is running. So it belongs in the enable path, which patch 0012
  already orders as: DSI host on → panel prepare (lines + FP9929A, gated) → DPU video with a zero buffer → bridge
  pre-enable (ID check, now logged by 0015) → init table. No I2C to the bridge at probe, and no reads before E3.
- E2b on this image: tc358767 binds without the ID (0015), msm's DRM device should appear (`/dev/dri/card0`, a
  448x829 mode), and nothing modesets. Pass criteria as in the E2 procedure, minus the ID read.
- E3 then needs: G9 (done), the power gates opened by hand for one session, `oops=panic panic=1`, a9_eink driving zero
  frames (docs/mainline-eink-driver.md E5), and the MX/CX vote check from the boot-3 G3 decision.

## E3 preparation status (2026-10-15, offline; image `c80e23ad8f3e423e…` has NO E Ink assets)

In the default image now (nothing here powers the panel by itself):
- patch 0017: `fp9929a.max_on_ms` (default 20000): a delayed work forces the rails down if they stay up longer, and
  latches a fault (enable returns -EIO) until a normal disable or `clear_fault`. **Interaction:** a9_eink's own
  `hv_max_ms` defaults to 60000, so any E3/E5 run must set `a9_eink.hv_max_ms` below 20000 (and `hv_hold_ms` 0), or
  the FP9929A timer trips first;
- patch 0018: rpmpd keeps genpd's corner across sync_state, so a vote set before sync_state is not lost (code reading);
- DTS: DSI PHY votes MX at NOM while the link is up (stock `vdda-0p9` = rwmx at level 0x100; upstream agatti/sm6125).
  To verify during video: `pm_genpd_summary` shows mx at 256;
- patch 0019: `a9_eink.zero_test=N`. An update powers the pipeline up through the normal HV/PG check, sends N all-zero
  frames, and leaves the engine state unchanged. This is E3;
- `build.sh` opt-in `A9_EINK_ASSETS=1` adds the local waveform blob and `a9eink-pattern` to the initramfs and appends
  `oops=panic panic=1`. The resulting image contains vendor tables and stays local.

**Done since:** the on-phone E3 -> E5c sequencer, next section. **Open safety question:** PG polarity
(docs/mainline-fp9929a-pg.md: likely active-high, the opposite of a9_eink's raw-0 check). The sequencer measures it
in E3 and stops unless the data agree with a9_eink.

## E3-E5 sequencer (2026-10-15, offline; NOT run on the phone yet)

**Status:** written and tested offline only (host fake-hardware tests and QEMU end to end, below). An independent
review of `ba81506` (`runs/sol-e3-review.md`, not in git) found two blockers, both fixed since (section "Review
fixes" below): a latched abort could be followed by the runner reopening the gates, and the FP9929A driver's panic
backstop and gate missed a partial power-up. Files:
`scripts/mainline/kernel/initramfs/a9-eink-seq` (phone), `first-boot.sh --e3|--e5ab|--e5` (host),
`tests/mainline-eink-seq/` (tests). No kernel patch was changed for it. A PG-convention patch for the *next* image
is staged in `scripts/mainline/kernel/patches-pending/0022-*` (not applied by `build.sh`, see its README).

**Image:** `~/A9-build/mainline/out-eink/a9-mainline-recovery.img`, sha256
`333374a2b69999f55ee3748ca99f8dc38c4072a8fb1a8eba70d5e1fb7a65ebfb` (local only: vendor waveform inside; kernel with patches 0021-0027), `build.sh
check` PASS (both abl-check routes), `first-boot.sh --offline` PASS for `--e3`, `--e3full`, `--e5ab` and `--e5`.
Differs from the reviewed (GO) image `89c37fdc…` by the boot-7 path fix, the boot-8 watcher filter fix, the
boot-9 PG change (patch 0022 + the sequencer's verdict) and the review-3 fixes (patch 0023, the active HV cutoff,
the 120 ms verdict window, the phone-side `--e3full` latch) and the review-4 fixes (patch 0024: the kernel per-session
cap and the PG origin; the sequencer setting the cap), the boot-10 criterion fix and patch 0025 (signed TMST + cold
refusal, min_off_ms, VCOM readback), the review-7 fixes (host E5b allow-line, the corrected frame table, driver bin
admission, the frame-progress interlock, patch 0026) and the review-8 fixes (patch 0027): **0023-0027 and the cap/bin
handling change the power path**.

**Boot 7 (2026-10-15, `runs/20261015T140345Z-mainline-first-boot`, MEASURED):** E1/E2a/E2b ran, then `pre` FAILED
and aborted safely; the panel was never powered (`safe: verified PWRUP=0 VCOM_EN=0`), restore MATCH. Cause:
`/proc/device-tree` is a symlink and `find` did not descend into it, so `pre.pg_dt_flags` failed; `modprobe a9_eink`
is skipped by design once any earlier criterion failed, so the 8 a9_eink checks after it failed as a consequence.
Fix: `find "$R/proc/device-tree/"`; `pre` now says when modprobe was skipped; the simulator models
`/proc/device-tree` as a symlink (the old sequencer fails `pre` there, the fixed one passes).

**Boot 8 (2026-10-15, `runs/20261015T141343Z-mainline-first-boot`, MEASURED):** `pre` PASS (a9_eink loaded, every
parameter read back). Session A: the transport attached (`transport on msm: 448x829 XR24 ... frame period 11764 us`),
then the **kmsg watcher tripped falsely** at 163.82 on ` SUBSYSTEM=platform`, a /dev/kmsg dictionary continuation
line (no header, parsed as level 0). `safe_off` closed the gates, so 20 ms later the panel refused prepare (`refusing
to power the E Ink panel`), a9_eink found no HV (`FP9929A is not on after enable: no frames sent`) and sent nothing;
HV never rose, PWRUP/VCOM_EN verified low, measurement-only stop, restore MATCH. So the "pipeline not available" was
our own abort working, not a driver fault; the bridge answered (`ID read 0, ID 0x6601`). Two harmless once-per-boot
kernel warnings came with the first modeset and would have tripped the watcher next: `msm_dpu ...: no GPU device was
found` (adreno lookup on the first DRM open; the GPU is disabled in our DT) and `[drm:programmable_fetch_get_num_lines]
low vbp+vfp may lead to perf issues in some cases` (DPU programmable fetch, this mode's 4/4-line porches).
Fix (watcher filter only, no power path): continuation lines (leading space, no `;`) are skipped; the two messages are
added to BENIGN by exact text; E5b also allows `Skipping unprepare of already unprepared panel` (the refused prepare).
The host scan got the same filter. The simulator now emits these lines at the first modeset (the old sequencer fails
session A on it, the fixed one passes).

**Commands for main (after the delta review of 0022 + the sequencer change):**

```sh
A9_EINK_ASSETS=1 scripts/mainline/kernel/build.sh all          # -> ~/A9-build/mainline/out-eink/ (LOCAL ONLY)
scripts/mainline/first-boot.sh --offline --e3full              # (and --e5ab / --e5): must print OFFLINE CHECK: PASS
A9_MAINLINE_HUMAN=yes scripts/mainline/first-boot.sh --write --e3full   # then --e5ab, then --e5
```

`--e3` is **measurement only**: a latched stop after session A whatever its result (`RESULT e3 STOP-A ...`).
`--e3full` runs `e3 full` (sessions A and B) and stops; `--e5ab` adds E5a and E5b (no glass); `--e5` adds E5c. Without `--image` these flags use
`out-eink/`. The flash/restore path is the unchanged first-boot route (boot_b + null dtbo_b, slot b, dead-man ->
fastboot -> golden restore).

### PG polarity: measured (boot 9), drivers changed to match

**Boot 9 (2026-10-15, `runs/20261015T142350Z-mainline-first-boot`, image `e09ccc84`, MEASURED):** first rails-on.
HV on ~95 ms in total, PWRUP/VCOM_EN verified low after, restore MATCH. Samples (o36 i36 o86 i86 o82 i82):
PWRUP alone (VCOM_EN low) PG 0 (3 samples); VCOM_EN up at ~163.55 s: PG 0 on the first sample, then 1 from ~163.57 s;
`PG_VERDICT active-high steady_on=2/2 on_incl_edge=3/4 pwrup_only=0/3 before=0/39 after_settled=0/54`. Kernel log:
`fp9929a ... on: ..., temp 25 C, PG 0` at 163.830 s (the edge sample right after VCOM_EN), a9_eink's check at
163.890 s saw PG 1 and refused (`FP9929A is not on after enable`): its raw==0 test was inverted, as
docs/mainline-fp9929a-pg.md predicted. So PG is **active-high**, rises <= 60 ms after VCOM_EN (~20 ms in the
samples) and falls with the power-down.

**Changes since (image `8aed5c01e015be7825d563c8d7a02a88096a5bcd1f8d38884ed8069c470e31cb`):**
- `patches/0022` (moved from patches-pending, unchanged): fp9929a polls PG after VCOM_EN and needs two reads of 1
  within `pg_timeout_ms` = 100 ms (>= 1.7x the 60 ms bound, ~5x the sampled ~20 ms), else runs the normal power-down
  and returns -ETIMEDOUT (logs `PG not asserted within 100 ms`); the gate is re-checked before the rails are marked
  on; the `on:` line reports `PG 1 after N us`; `pg_good` = logical PG 1; a9_eink requires PG wired and good.
- DTS comment on `pg-gpios`: "1 = rails good".
- `a9-eink-seq`: the accepted verdict is now `active-high` = steady-on all 1 **and** >= 3 settled-off samples all 0
  (a toggle, as measured). `raw-low-when-on`, `high-no-toggle` (1 on and off), `low-no-toggle`, the `*-unclear`
  verdicts, `unstable` and `undetermined` all stop. Session B's mid-session pin check expects PG 1. The `on:` line's
  settle time is recorded (`pg_settle`). No new allow-list entry: the refusal and the PG timeout are errors (trip).
- `first-boot.sh`: `--e3full` = pre + `e3 full` (sessions A and B, zero frames only), then a latched stop; the host's
  E3 "expected refusal" exception is removed.

**Review 3 (`runs/sol-e3-review-3.md`, GO-WITH-FIXES on `d3851d9`) and the fixes:**
- **PG deadline (kernel, POWER PATH): new `patches/0023`.** 0022 accepted PG when the second good read arrived,
  before comparing with the deadline, so two good reads separated by a long preemption passed after
  `pg_timeout_ms` (reproduced on the host: second read 150 ms late -> enable 0, `PG 1 after 151227 us`). Now the
  deadline is checked after every read and before it is counted, and its origin is the VCOM-EN assertion (the clock
  is read right after raising the pin, under the gate lock); the reported settle time is measured from VCOM-EN.
  Fault tests: (i) second good read 150 ms late -> -ETIMEDOUT, normal power-down order, on/energized false, no `on:`
  line (failed before the fix, passes after); (j) 30 ms late -> success; (a), (g), (h) unchanged and passing.
- **Per-session HV cap (superseded by review 4, below):** the first version was a shell check in the session's
  final wait loop; review 4 showed it is blind while the runner waits for the pattern client, the I2C mid-reads or
  its own cleanup. It stays only as a best-effort backstop (cap + 1 s from the gate opening).
- **PG verdict window:** steady-on now starts 120 ms into a both-high run (past the driver's 100 ms PG limit), so
  a valid but slower PG rise never counts against it. Failed devmem reads become `x` samples and make the verdict
  `invalid-samples` (a stop) instead of turning into 0s.
- **`--e3full` latches on the phone:** it sends `e3 full stop`; after session B the phone itself runs `safe_off`
  with a latch and reports `RESULT e3 STOP-B (zero frames only; sessions A+B PASS|FAIL ...)` plus the `SAFE` line,
  so a lost host does not leave E3 un-latched. The host requires `STOP-B ... PASS` and now **checks** the final
  `safe` answer (`safe: verified PWRUP=0 VCOM_EN=0`), failing loudly otherwise.

**Review 4 (`runs/sol-e3-review-4.md`, NO-GO on `c11f44b`) and the fixes (patch 0024 + sequencer):**
- **The per-session HV limit is enforced in the kernel (POWER PATH).** Patch 0024 makes `fp9929a.max_on_ms` a
  validated per-session cap: 1..20000 accepted, 0 (no limit) and > 20000 refused (-EINVAL), a write while the rails
  are energized refused (-EBUSY; checked under the gate lock, under which energize reads the value), so a value only
  ever applies to the next power-up and can never extend a running one. The timer already started at energize
  (before PWRUP), drops VCOM_EN, 50 ms, PWRUP in the stock order also while another path holds the regulator lock
  (0021's fallback), and latches a fault until a normal disable or `clear_fault`; it now runs on `system_highpri_wq`.
  The sequencer writes the session's cap (A 4000, B 6000, E5c 3588 ms) under the abort lock **before** opening the
  FP9929A gate, reads it back (any mismatch stops the session), and restores 20000 once the rails are verified off.
  The kernel's `max_on_ms ... expired` line is a crit message, so the watcher also aborts the stage.
- **What the bound is, honestly:** PWRUP/VCOM_EN stay high at most `max_on_ms` after energize plus the ordered
  ~50 ms drop, **subject to kernel scheduling** of a nice -20 kworker (no hardware timer; a wedged kernel or a
  hard reset bypasses it; the panic notifier covers panics). Every shell-side check (the backstop at cap + 1 s from
  the gate opening, the 100 ms poll, the guardians, the post-session HV-time criteria) is best-effort on top.
  Nominal session time is about 3.2 s (A) and 5.2 s (B) after energize, so the caps leave ~0.8 s; late frames that
  stretch a session past its cap end it (fail closed).
- **PG origin (POWER PATH, 0024):** `vcom_on` is now read **before** the VCOM_EN write (under the gate lock), so a
  task switch after the write can only shorten the PG window. Fault case (l): a 150 ms stall right after the write
  -> -ETIMEDOUT, not on (it failed with 0023's order, passes now).
- **Regressions (simulator, which models 0024's timer from energize):** the pattern client blocked 20 s with the
  update hung (`capblock`), session B's I2C reads stalled 2.9 s each with B's update hung (`capi2c`), and software
  reporting off (`hv_on 0`) while the pins stay high (`capswoff`): in each the kernel timer drops the pins
  4000/6000 ms (+ <= 150 ms) after energize while the runner is blocked, the stage aborts, and `max_on_ms` is back at
  20000 afterwards; plus an abort injected right after the cap is written (`inject ... max_on`). Fault tests of the
  real driver: (k) parameter validation, refusal while energized, next-power-up-only; (l) above; (m) the cap fires
  while another thread holds the regulator lock.

**Boot 10 (`--e3full` on `e130fd5d`, `runs/20261015T155353Z-mainline-first-boot`, MEASURED):** session A ran for real:
255 all-zero frames, HV on ~3205 ms (cap 4000), PWRUP/VCOM_EN low after, `PG_VERDICT active-high steady_on=223/223
before=0/37 after_settled=0/52 bad=0`, `PG 1 after 42873 us` (the 0022 wait; 100 ms leaves 2.3x margin). The one
failed criterion was the sequencer's: `e3a.last` compared the status field `last mode` with the string `0`, but the
kernel prints `%#x` of 0 as `0x0` (lib/vsprintf.c adds the prefix whatever the value), and `0` is exactly what the
zero-test path sets. Fixed in the sequencer (numeric compare); the simulator now prints `0x0` as the kernel does
(the old sequencer fails `aonly` on it, the fixed one passes). Session B did not run; safe verified; restore MATCH.

**Next-image changes from the FP9929A datasheet (`runs/sol-fp9929a-research.md` §5), patch 0025 (POWER PATH):**
- **Signed TMST:** register 0 decoded as `s8` after the I2C error check (0xf6 = -10 C, 0xff = -1 C). Policy stays
  in the consumer: a9_eink refuses an update (no frames, pipeline down) when the FP9929A or the PMI632 thermistor
  reads below 0 C, instead of mapping it to 0 (the composer's bins start at 0 C; the panel's cold rating is not
  known). The sequencer's 5-45 C criteria are unchanged.
- **Minimum off time:** `fp9929a.min_off_ms` (500..10000, default 1000, 0 refused) from the last completed
  power-down (the driver's own, the probe's power-down of bootloader-left rails, and the max_on fallback) to the
  next energize; a power-up inside it waits, re-checking the gate every 20 ms (-EPERM if it closes), counted as in
  flight for `allow_power_on=0`. Datasheet fig. 5: VGL needs VLD >= 500 ms to discharge to -7.4 V. `pre` checks
  the value (1000); the shell backstop allows the extra delay (cap + 1 s + min_off from the gate opening). The
  kernel cap (`max_on_ms`) starts at energize, after the wait.
- **Stock comparison (MEASURED by main on the daily stock kernel, 2026-10-15, dmesg `fp9929a` lines during swipes):**
  stock keeps the rails up during continuous updates (one 13.5 s session), sleeps ~2.75 s after its queue empties
  (`active end` -> `sleep start` ~2.74 s; the sleep sequence ~52 ms), and powers up in ~20 ms (`write vcom` ->
  `active end`). When an update arrives just after a sleep, stock re-powers after only **257 / 277 / 616 ms** off
  (`sleep end` -> next `write vcom`): it does not keep the datasheet's 500 ms VLD. `min_off_ms` = 1000 is therefore
  stricter than stock (safe, adds up to 1 s latency); it stays 1000 for E3/E5. For Android daily use 500 (the
  datasheet minimum, still stricter than stock) is a possible later choice, not made now.
- **VCOM check:** after the VCOM write (PWRUP up), register 1 bit 0 must read 1 (VCOM from the I2C DAC; reset
  default, never written) and register 2 must read back the code (5 tries each), else the ordered power-down and
  -EIO.
- Fault cases (n) signed TMST, (o) min_off validation / >= 1000 ms re-energize / gate close during the wait,
  (p) readback mismatch, (p2) register 1 bit 0 = 0 (and no register but VCOM written), (p3) readback I2C error; each
  fails when its fix is removed (mutation check). Simulator: `cold` (-5 C -> refusal, stop).

**Boot 11 (`--e5ab` on `8a24079a`, `runs/20261015T162349Z-mainline-first-boot`, MEASURED):** E3 full PASS (A HV
~3204 ms, B ~5170 ms, total 8374 ms; PG active-high 217/217 steady, 0 before/after; both sessions verified off).
E5a DRY-OK and the host CLI agreed (frames 60, sha256 `6359db11…`). E5b failed on one kmsg line,
`tc358767 0-000f: main link stream start error: -6`; aborted, safe verified, restore MATCH; the user saw the boot
logo unchanged on the glass. **Cause (code + capture):** with the FP9929A gate closed, `fp9929a` refuses, the panel's
prepare fails and drops the I2C mux (GPIO 2, `panel-hisense-hlte556n-eink` prepare error path), so all 25 raw writes
of the bridge init table NACK (-6 = ENXIO, 3 tries each), `tc_dpi_bridge_atomic_enable` logs the stream error once
and calls `tc_main_link_disable` (its writes fail too); a9_eink then reports `FP9929A is not on after enable`, powers
the pipeline down and drops the update. **No power is involved and nothing retries:** tc358767's enable is void and
runs once per modeset, and a9_eink only starts another power-up on a new commit. The bridge is left unconfigured,
which the next real power-on (fresh prepare with the mux high, the whole table again) overwrites. **Fix (E5b only):**
that exact line is allow-listed, with new criteria: at most 25 init-seq failures (one pass), at most one stream
error, no `main link stream stop error`, no forced FP9929A power-down; a retry fails E5b (test `e5bretry`). Side
finding: `before init sequence: ID read 0, ID 0x6601` can be served from the regmap cache (`TC_IDREG` is not in
`tc_volatile_ranges`; it was, here, with the mux low), so it is not evidence that the bridge answered; the HV
stages' real evidence is zero raw-write `init seq #` failures, and they now also require no stream error.

### E5c (first glass): exactly what runs, for the delta review

**Review 7 (`runs/sol-e3-review-7.md`, NO-GO for `--e5` on `4913119`) corrected the frame envelope and asked for a
frame-progress interlock; this section is the corrected version.**

Runs only inside `--e5`, in the same boot after `pre`, `e3 full` (A + B), E5a with the host verdict and E5b all
passed, with PG verdict `active-high`, battery 3.8-4.5 V and `epd_top_therm` 5-45 C re-checked, and the a9_eink
parameters re-verified (`zero_test` 0, `hv_hold_ms` 0, `hv_max_ms` 15000, `init_flash` Y, `trace` N).

**Frames per temperature bin** (review 7, MEASURED with the real engine and the image's waveform blob; E5a's dry
run at bin 8 gave 60 Clear frames and the host CLI reproduced its hash, `6359db11…`). The first real update has
`force_clear` set, so the request is mode 0x203, clear 1: the stock builder adds a **uniform white type-3 pre-pass**
before the **type-2 pass of the image**; "steps 1" in `last` is that internal request step. The Clear column is the
complete request (both passes), not one table pass:

| Bin | Init flash | Clear request | Total | at 85 Hz |
|---:|---:|---:|---:|---:|
| 0 | 109 | 208 | 317 | 3.73 s |
| 1 | 93 | 176 | 269 | 3.16 s |
| 2 | 81 | 152 | 233 | 2.74 s |
| 3 | 71 | 132 | 203 | 2.39 s |
| 4 | 61 | 114 | 175 | 2.06 s |
| 5 | 111 | 96 | 207 | 2.44 s |
| 6 | 95 | 80 | 175 | 2.06 s |
| 7 | 83 | 70 | 153 | 1.80 s |
| 8 | 75 | 60 | 135 | 1.59 s |
| 9 | 67 | 60 | 127 | 1.49 s |
| 10 | 63 | 60 | 123 | 1.45 s |
| 11 | 59 | 60 | 119 | 1.40 s |
| 12 | 55 | 60 | 115 | 1.35 s |
| 13 | 51 | 60 | 111 | 1.31 s |

**Bin admission (enforced in the driver, patch 0026):** the sequencer writes `a9_eink.bin_min/bin_max` = E5a's bin
+-1 (boot 11: bin 8 -> 7..9, worst case 153 frames = 1.80 s), refuses E5c if any admitted bin's frames + 1 s setup
exceed the fixed 3588 ms cap (never raised), and restores 0/13 afterwards. a9_eink checks the bin before power-up
(previous TMST sample + fresh thermistor) and again with this power-up's TMST before any frame, and refuses outside
the window. The post-check then requires the exact counts of the bin that ran (`init_frames` = Init column,
`update_frames` = Clear column) and that bin inside the window.

**Review 8 (`runs/sol-e3-review-8.md`, NO-GO on `754c43f`) -> patch 0027 (POWER PATH):**
- **Emergency ordering:** `fp9929a_emerg_work` now always waits a full 50 ms after its OWN VCOM_EN-low write, under
  the gate lock (no power-up can raise VCOM_EN while it is held), before dropping PWRUP; the timestamp comparison,
  which a repeated `fp9929a_emergency_off()` could overwrite (reproduced: PWRUP 8.9 ms after the last VCOM_EN fall),
  is gone. Bound: VCOM_EN low at the call, PWRUP low ~100 ms after the first call (50 ms queue + 50 ms), plus work
  scheduling. Regression (r4): first emergency after the gate check, the authorized VCOM_EN assertion delayed 40 ms,
  a second emergency 4 ms after it -> PWRUP >= 50 ms after the last VCOM_EN fall (fails on 0026, passes on 0027).
- **Progress-only deadline:** a drive commit arms the interlock only if it is not armed and never postpones an
  armed deadline; a successful drive commit restarts it from its return (confirmed progress); a successful null
  commit cancels it; a failed commit leaves it alone. **Bound:** no drive frame stays on the panel longer than
  3 periods (35.3 ms) after the last drive commit that returned, or after the start of the first drive commit
  after a zero frame (it may latch, then block); then VCOM_EN at once, PWRUP ~100 ms later. Regressions: (11) an OK
  frame, then 18 ms later a drive commit that blocks -> emergency 35.37 ms after the OK frame (0026: ~54 ms);
  (12) drive commits failing every 8 ms after an OK frame -> emergency 35.37 ms after it (0026: postponed); all
  older cases re-measured from the last confirmed progress: 35.31-35.38 ms. As the review notes, a returned DRM
  commit is the latch boundary in the ordinary path, not an electrical proof of scanout.
- **Valid fresh temperatures:** a real waveform update needs a valid TMST sampled at THIS power-up (fp9929a clears
  it at every power-up start) and a thermistor reading; `temp_fallback` never admits drive frames; refused (power
  down, no frame) otherwise. The pre-power-up bin pre-filter needs the thermistor, uses the previous TMST only if
  there is one, and can only refuse. Simulator `tmstfail`; the `a9_update` call site itself is not host-harnessed.

**Frame-progress interlock (patch 0026, POWER PATH; deadline semantics as amended by 0027 above):** an hrtimer per
transport with a deadline of 3 frame periods (3 x 11764 us = 35.3 ms), cancelled only after a
successful all-zero commit. If it expires with the transport on (the worker stopped computing, a commit blocks
forever holding the transport/modeset locks, or a frame is simply late by more than 3 periods), its hardirq
callback calls the new `fp9929a_emergency_off()`: no lock, no sleep; latch, `allow_power_on` false, VCOM_EN low at
once (`gpiod_set_value`, TLMM does not sleep, as in the panic notifier), PWRUP low from a highpri work 50 ms later
(again >= 50 ms after VCOM_EN if a racing power-up raised it), refusing power-ups until `fp9929a.clear_fault`; it
also latches `a9_eink` `stall_fault` (status line 1; updates refused until `a9_eink.clear_fault`) and sets
`a9_eink.enable=0`. Why 3 periods: boot 11's zero-frame commits ran with a maximum gap of 13.2 ms, and the dry run
computed a frame in ~3.3 ms; a gap over 3 periods means at least 2 extra refreshes (drive pulses) of one frame
beyond the 1.5-period "late" threshold. Bound on a stuck drive frame: ~35 ms + 50 ms to PWRUP + timer and work
latency (VCOM_EN at ~35 ms); a total CPU/scheduler lockup is outside it (the panic drop remains). It removes HV, it
does not blank: blanking is only attempted where it cannot block (the normal power-off path). Also: a failed zero
commit at power-off now triggers the emergency drop and the latch before DPMS off (it used to be ignored), and the
second zero commit at power-on is checked. Tests (`tests/mainline-a9eink-stall`, the real transport against DRM
mocks): worker stall mid-waveform, a commit blocked forever with the lock held, a 60 ms late frame -> emergency at
35.3-35.4 ms; failing zero commit -> emergency before DPMS; a normal 135-frame waveform and zero-only frames with long
gaps -> no emergency; plus FP9929A cases (q)-(r3) for `fp9929a_emergency_off` (lock held elsewhere, during a stalled
power-up, during the PG wait, racing VCOM_EN). Not covered by a harness: the call sites in `a9_update`/`a9_power_on`
(fault refusal, the two bin checks), which are too tied to DRM; checked by build and reading.

1. Opening, every step under the abort lock: `zero_test=0` (read back), `fp9929a.max_on_ms=3588` (read back), then
   the gates `fp9929a.allow_power_on`, `panel_hisense_hlte556n_eink.enable`, `a9_eink.enable`, then
   `a9eink-pattern -d /dev/dri/card1 --hold 1 steps` (the a9_eink card only; the shadow holds E5b's checker, so the
   whole steps image is damage).
2. What the panel receives (a9_eink, one update): power-up (DSI on, panel lines, `min_off_ms` wait if needed,
   FP9929A energize -> PWRUP, VCOM write + register 1/2 readback, TMST, 20 ms, VCOM_EN, PG wait <= 100 ms, bridge
   init, two all-zero refreshes), the bin check, then the **stock init flash** once (never run in this boot: E3 was
   zero-test, E5a dry, E5b refused), then the **Clear request**: white type-3 pre-pass + type-2 steps pass. Every
   pass ends on an all-zero frame (stock tables; the driver blanks otherwise); the zero frame scans 2 refreshes,
   CRTC off, FP9929A power-down (hold 0).
3. Limits: kernel `max_on_ms` 3588 from energize (pins dropped VCOM_EN, 50 ms, PWRUP, fault latched); the
   frame-progress interlock (below); shell backstop at cap + 1 s + `min_off_ms` from the gate opening; guardian
   25 s; the 0021 panic drop.
4. Abort criteria (any one -> `safe_off`, latched): a kernel warning or worse in the session window (watcher; no
   E5c allow-list entry besides the global BENIGN pair), `fp9929a ... on:` without code 0x7e, any a9_eink refusal
   (bin, temperature, PG), `max_on_ms ... expired`, a frame-progress trip, an a9-irqmon storm, no end of update
   within 25 s, PWRUP/VCOM_EN high 2 s after the update. Post criteria: exactly one `on:` line with VCOM 2730/0x7e,
   TMST 5-45 C within 10 C of the thermistor, 0 bridge `init seq #` failures and no stream error, updates +1,
   aborts +0, late +0, vbl_skips +0, commit_fail +0, max_gap_us <= 17646, DPU underrun +0, vsync advanced >= the
   frame count, `last` = `mode 0x203 pre 0x203 clear 1 type 2 ... dry 0 aborted 0`, the bin inside the window and
   the exact frame counts of that bin, sampled HV time <= cap + 300 ms, pins low, boot HV total <= 30 s.
5. Not machine-checkable: the glass (expected: boot logo gone, 16 grey bands black at the top to white at the
   bottom). The frames are not hashed during E5c (`trace` 0, to keep the frame timing); their computation is the
   one E5a verified in dry run, at E5c's own temperature bin.

**Plan:** `--e3full` (A + B) -> `--e5ab` (A + B + E5a + E5b; no glass) -> `--e5` (+ E5c, first glass; the user looks).
Each run repeats the earlier stages in the same boot, because the sequencer's pass files live in tmpfs.

### Control template (patches 0028/0029), `e3t`, session load (2026-10-15, offline; NOT run on the phone yet)

**Why:** boot 12 (`runs/20261015T175533Z-mainline-first-boot`) ran E5c with every machine criterion but the timing
ones passing, and the glass did not change. Root cause (coordinator, SPEC): stock drive words carry 2-bit data in
byte 0 and a fixed **control template** in bytes 1-3 (gate clocks, strobes); our transport zero-filled bytes 1-3, so
the panel was never clocked. Patch 0028 fills every frame with the template (`/lib/firmware/hisense/a9-ctrl-template.bin`,
local only, never committed: 80-byte `A9CT` header + 1,485,568-byte payload, payload sha256 `a88ae8ef…`, host file
sha256 `dc9aa5ba…`); zero-test, power-on/off and blank frames are now the template (byte 0 = 0). Patch 0029 adds
drive-only timing counters.

**Sequencer changes (`a9-eink-seq`):**
- `pre [FW_SHA256 [CT_SHA256]]` (`-` = not given; the old one-argument form still works): the template file must
  exist, have the `A9CT` v1 header, 1,485,648 bytes, payload sha256 = `a88ae8ef…` (= the kernel's `A9_CT_SHA256`)
  and whole-file sha256 = the host's copy; the status must have the 0028/0029 fields (`template`, `late_drive`,
  `vbl_skips_drive`, `max_gap_drive_us`): **missing = kernel without 0028, pre FAIL**. `template` is 0 at pre (it
  loads with the waveform at the first update) and must be **1 after every HV session and E5a** (`<tag>.template`);
  the `control template loaded` line is recorded as info in E3 session A.
- The zero-test line must read `zero test: N/N template frames with HV on (0)`; the old `all-zero frames` text fails
  as "kernel without patch 0028". `no valid control template` is an error line: the watcher aborts.
- E3 A, B and e3t: the drive counters must not move (`late_drive` +0, `vbl_skips_drive` +0, `max_gap_drive_us` +0:
  no drive frame is ever sent there); the total late/vbl_skips/max_gap keep their old gating (A: info).
- **E5c:** `late_drive` +0, `vbl_skips_drive` +0, `max_gap_drive_us` <= 17646 are GATING, **and so are the totals**
  (late +0, vbl_skips +0, max_gap_us <= 17646). Review 10 (`runs/sol-e3-review-10.md`) found that 0029's
  attribution (previous front only) missed a slow first drive commit after idle; the totals catch it, so the
  "totals are info" exemption was removed (conservative: a harmless idle delay now refuses E5c). Patch 0030 also
  fixes the attribution: an interval is drive time if the front drove (whole gap) or, if the incoming frame
  drives, for the commit's own execution; host case (16) = the reviewer's regression. Sampled-HV limit = cap + 50 ms + one
  250 ms sample period + 100 ms (3988 ms). `prep_us` (status line 3; `a9e_prepare` now runs before power-up) is
  reported as info in E5a and E5c.
- **Session load (frame pacing):** only E3 session A samples densely (5 ms, nice 10; it measures PG and sends
  template frames only). Session B samples every 100 ms, e3t and E5c every 250 ms (was 200), all three at nice 19.
  The kmsg logger and watcher stay at the default priority (the watcher must stay responsive); the kernel's update
  path runs at nice -10.

**`e3t` (`first-boot.sh --e3t` = pre + `e3 full` (A + B) + `e3t stop`):** needs `pass.e3` (only `e3 full` writes it)
and PG `active-high`. One template-only HV session: 425 zero-test frames (5 s), kernel cap `fp9929a.max_on_ms` 6000,
the same opening under the abort lock, watcher, guardian (15 s), shell backstop, `safe_off`; then the phone latches
the stop itself (`RESULT e3t STOP-T (template frames only; e3t PASS|FAIL ...)`, `SAFE ...`). The phone logs
`e3t: HV ON (VCOM_EN high) at uptime ...: WATCH THE GLASS` and `e3t: HV OFF at uptime ...`; the host echoes both live
from the kmsg stream (`LIVE hh:mm:ss ...`). **Expected glass: unchanged** (template frames clock the gates and drive
no pixel); recorded as `e3t.glass` NOT machine-checkable: the user reports it.
- Gating (as session B): one `on:` line with VCOM 2730 mV/0x7e, TMST 5-45 C within 10 C of the thermistor, bridge
  ID line, 0 init-seq failures, no stream error, watcher clean, HV time <= 425/85 s + 2 s, pins low, boot budget
  30 s; `zero test: 425/425 template frames (0)`; updates +1, aborts +0, frames +425, `template 1`, commit_fail +0,
  underrun +0, vsync advanced; late +0, vbl_skips +0, max_gap_us <= 17646 **up to the DPU state read** (status
  snapshot `st.e3t.ps` taken right before it; the whole-session values are printed with them); drive counters +0
  over the whole session; at 1 s and 3 s after VCOM_EN: FP9929A reg 0x02 = 0x7e, reg 0x00 5-45 C, MX >= 256, pins
  `1 1 1 1 x 1`.
- Info only (`criteria.e3t`, raw lines in `e3t/regs.txt`, fetched into `capture/eink-seq/phone/e3t/`): raw TC358767
  reads at 1 s and 3 s, `i2ctransfer -f -y 0 w2@0x0f <hi> <lo> r4` (big-endian address, little-endian data; the
  regmap cache is bypassed): 0x0500 ID (expect 0x6601), 0x0510 SYSCTRL, 0x050c SYSRSTENB, 0x0108 PPI_BUSYPPI, 0x0208
  DSI_BUSYDSI, 0x0214/0x0218 lane status, 0x0220 DSI_INTSTATUS, 0x0564 INTSTS_G, table readbacks 0x0448 0x0450 0x0454
  0x045c 0x0464 0x0908 0x0914 (and 0x0510) compared with the DTS `toshiba,init-sequence` (match/MISMATCH; 0x0464
  VFUEN and 0x0908 PLLUPDATE are upload bits the driver treats as volatile, so a readback without them is expected),
  0x0300 DSIERRCNT (again 1 s after the first read: delta), and **0x0508 SYSSTAT last**. Side effects (read from
  `drivers/gpu/drm/bridge/tc358767.c`): SYSSTAT is the only register in `tc_precious_ranges` (read may clear it), so
  it is read last; INTSTS_G and DSI_INTSTATUS are cleared by writes; DSIERRCNT is volatile, not precious (clear-on-read
  not known: no datasheet here). A failed read is an `x` and info, never an abort. Clock rates from debugfs:
  `disp_cc_mdss_byte0_clk` (59.5-60.7 MHz) and `disp_cc_mdss_pclk0_clk` (39.68-40.48 MHz), info. **DPU state:**
  `dri/<msm>/state` once, at the 3 s point after the I2C reads, read in the background (`timeout 5`) so the runner
  never blocks; it takes the modeset locks and may delay a commit, which is why the timing gates end at the snapshot
  before it. Allowed only in e3t: every frame there is the template and the frame-progress interlock is never armed;
  never in E5c.

**E5a host (first-boot.sh):** the CLI call (and the offline known-case re-run) passes `--template <host template>`
(the dry-run frames are template-filled). The known-case hash changed and is pinned: `E5A_KNOWN` =
`1b76ea3d87d0b767eddbe2620772f839ac9a42957fb3b685ce3d7a483682be1f` (frames 60, 30/30 C, with the template; the
template-free value was `6661424b…`). `qemu-e2e.sh` computes its E5a fixture hash with the CLI at test time.

**Tests:** `run.sh` adds `e3t` (good path, STOP-T PASS, HV ON/OFF lines, regs/clock/state info, SYSSTAT last, timing
window), `e3tread` (one register NACKs: info, PASS), `e3tdrive` (late_drive +1 in e3t: FAIL), `e5ctpl` (E5c total
late +3, drive 0: PASS), `e5cdrive` (late_drive +1: FAIL), `notpl` (no template / host hash mismatch / wrong payload
hash: pre FAIL), `oldkernel` (status without 0028 fields: pre FAIL; `all-zero frames` text: e3 FAIL). The simulator
uses a FAKE template with the real header layout; its payload hash is passed as `A9_SEQ_CT_SHA` (honoured only with
the fake tree, like `A9_SEQ_SPEED`).

### Session parameters

| Parameter | Value | Why |
|---|---|---|
| `a9_eink.hv_hold_ms` | 0 | power down right after each update |
| `a9_eink.hv_max_ms` | 15000 | below `fp9929a.max_on_ms` (20000, checked). **Not a per-update cutoff:** a9_eink checks it only before taking the next job, so it never ends a running update (review); the per-session limits are the guardian and `fp9929a.max_on_ms` |
| `a9_eink.init_flash` / `trace` | 1 / 0 | stock start; no hashing during real drive (frame timing) |
| `a9_eink.zero_test` | E3 A: 255 (3 s), E3 B: 425 (5 s), e3t: 425 (5 s), E5: 0 | patch 0019; template frames since 0028 |
| `fp9929a.max_on_ms` | 20000 (read back, must equal) | patch 0017 backstop |
| HV-on budget | 30 s per boot (sum of all sessions) | |

All of them are set by `modprobe a9_eink ...` in `pre` (none on the kernel command line; `build.sh check` and the
offline check still refuse any gate or `a9_eink.` item there) and read back before every session.

### Stages and machine-checkable criteria

Every criterion is printed as `ok`/`FAIL <name>: <detail>` and stored in `/tmp/eink-seq/criteria.<stage>`; the
stage ends with `RESULT <stage> PASS|FAIL`. Nobody looks at the glass; "glass unchanged" (E3) and "16 bands" (E5c)
are recorded as NOT checked.

**pre** (no power): msm DRM bound (`[drm] Initialized msm`), tc358767 bound to `0-000f`; VCOM `2730 mV (from
cmdline) -> code 0x7e`; no `max_on_ms ... expired` and no `fp9929a ... on:` so far in this boot; `max_on_ms` 20000;
both power gates N; DT `pg-gpios` flags 0 (logical = raw); devmem self-test (below); GPIO 36/86 low; battery
3.8-4.5 V (`power_supply/battery/voltage_now`); `epd_top_therm` 5-45 C; waveform blob sha256 = the host's
`~/A9-build/mainline/firmware/hisense/a9-waveform.bin`; `a9eink-pattern` present; `modprobe a9_eink` with the
parameters above and every parameter read back; the a9_eink card found (`card*-DSI-1/eink`), status `enable 0 ...
hv_on 0`; dmesg `frames dry run`.

**e3, session A** (`black`, 255 zero frames, gates fp + panel + enable opened in that order): the sampler records
`uptime o36 i36 o86 i86 o82 i82` (TLMM output/input bits) every 5 ms (devmem) or 50 ms (debugfs fallback) from
0.3 s before the gates open until 0.6 s after the rails are verified down. Criteria:
- exactly one `fp9929a 0-0048: on:` line, with `VCOM 2730 mV (code 0x7e)`, TMST 5-45 C and within 10 C of
  `epd_top_therm`; its PG value is recorded but NOT used (an edge sample, like stock's logs);
- `tc358767 ... before init sequence: ID read 0, ID 0x6601`; zero `init seq #` lines (= init_errors 0: a failed
  write after its 3 tries; there is no init_errors attribute in the driver);
- a9_eink accepted the rails (no `FP9929A is not on after enable`); `zero test: 255/255 ... (0)`; status: updates +1,
  aborts +0, frames +255, commit_fail +0, `last mode 0 frames 255 aborted 0`, hv_on 0 afterwards; late/vbl_skips/
  max_gap reported but not gating in A (the 5 ms sampler shares the single CPU);
- DPU encoder debugfs (`dri/<msm>/encoder-*/status`): underrun +0, vsync advanced at least by the frame count
  (fallback: the `msm` IRQ line);
- the kmsg watcher never tripped; HV-on time (from the samples) <= 255/85 s + 2 s; PWRUP/VCOM_EN low (output and
  pad bits) at the end; boot total within budget;
- **PG verdict** from the samples (steady-on = both controls high and >= 120 ms into that run, past the driver's
  100 ms PG limit; off-settled = both low and >= 0.2 s after PWRUP fell; any failed read = `invalid-samples`):

  | verdict | samples | consequence |
  |---|---|---|
  | `active-high` | steady-on all 1 **and** >= 3 settled-off samples, all 0 | the drivers' convention (patch 0022; MEASURED boot 9): session B, then E5 |
  | `high-no-toggle` / `high-off-unclear` | 1 on and 1 (or mixed) off | PG carries no information: stop |
  | `raw-low-when-on` | steady-on all 0, settled-off all 1 | opposite of the drivers (fp9929a would time out): stop |
  | `low-no-toggle` / `low-off-unclear` / `unstable` / `undetermined` | 0 throughout, mixed, or < 2 steady-on samples | stop |

**e3, session B** (`white`, 425 zero frames; only with `e3 full`, after A passed with `active-high`): A's criteria with late +0,
vbl_skips +0 and max_gap_us <= 17646 (1.5 frame periods) gating; at 1 s and 3 s after VCOM_EN rose: FP9929A reg 0x02 =
0x7e and reg 0x00 = 5-45 C (`i2cget -f`, read-only), `pm_genpd_summary` mx >= 256 (the DSI PHY's MX vote during
video), pins still `1 1 1 1 x 0`; vsync rate between the two reads 76-94 /s.

**e5a** (dry run; all gates N, verified): pattern `steps`; updates +1, transport frames +0, never an `on:` line, GPIO
36/86 low in every sample (200 ms), no warning/error; `last` = `mode 0x203 pre 0x203 clear 1 type 2 ... dry 1
aborted 0` with a frames/sha256 line; `grey`, `levels_before` 1,357,952 bytes each. The phone prints `E5A_LAST`,
`E5A_FRAMES`, `E5A_SHA` and `RESULT e5a DRY-OK`. **Host:** fetches `e5a/`, checks the sha256s, `levels_before` = all
15, `grey` = the steps image through the stock luma + rotation (`17*((x*16)//1648)`), runs
`a9tcon <composer> --grey grey --old levels_before --mode 0x203 --pre <pre> --clear 1 --t1 <t1> --t2 <t2>` and
requires mode/clear/type/bin and frames + sha256 to agree. Then `verdict e5a <frames> <sha>`: the phone compares
with its own `last` and only then writes `pass.e5a` (a host bug cannot pass E5a by itself). MEASURED offline: the
engine's init flash followed by this update equals the fresh CLI update (3 temperature pairs); the offline check
re-runs the CLI on a known case (`frames 60 sha256 6661424b...` at 30/30 C).

**e5b** (panel gate + enable, FP9929A gate N, verified): pattern `checker 64`; `FP9929A is not on after enable: no
frames sent` and `fp9929a ... power-up refused` present; aborts >= +1, frames +0, updates +0, commit_fail +0;
GPIO 36/86 low in every sample (50 ms); no `on:`; no other warning/error (allowed only here: the refusal lines and
`tc358767 init seq #`, since the panel drops the mux after the refused prepare; recorded as info).

**e5c** (all gates; refused unless pre, e3, e5a (with host verdict) and e5b passed, PG verdict `raw-low-when-on`,
battery and temperature re-checked, parameters unchanged): pattern `steps` (the shadow holds the checker, so there
is damage); the HV criteria of E3 (one `on:` line, VCOM, TMST, bridge ID, init errors 0, watcher, HV-on <= 10 s, off
verified); updates +1, aborts +0, late +0, vbl_skips +0, commit_fail +0, max_gap <= 17646, underrun +0, vsync
advanced; `last` = `mode 0x203 pre 0x203 clear 1 type 2 ... dry 0 aborted 0`; Clear update frames 30-140; init flash
frames (frames delta - update frames) 40-120.

### Abort path (`safe_off`, the one function every failure goes through)

Triggered by: any FAIL criterion at the end of a stage; the **kmsg watcher** (live, blocking read of /dev/kmsg from
the session's open marker; trips on any kernel line at warning level or worse that is not on the stage's allow-list,
on `a9-irqmon:` storms, on an `on:` line without code 0x7e or in a no-HV stage, on a zero test ending with an error
code; the abort runs from inside awk, so it does not wait for the next kmsg record); the **guardian** (per-session
hard deadline, independent of the runner: N/85 + 10 s for E3, 25 s for E5c); the runner's own deadline; HV still up
2 s after the update; the host (`eink-seq safe "host: <reason>"`); the dead-man timer and `a9-ctl bootloader` (both
call `eink-seq safe` before rebooting when `/tmp/eink-seq` exists).

Steps, serialised with flock and idempotent:
1. `a9_eink.enable=0` (emergency stop: the update ends after the current frame, a zero frame is committed and the
   pipeline powers down, hold 0), `panel_hisense_hlte556n_eink.enable=0`, `fp9929a.allow_power_on=0`, kill
   `a9eink-pattern`;
2. wait up to 3 s for GPIO 36 (VCOM_EN) and 86 (PWRUP) low, output **and** pad bits;
3. still high: drive them low through the TLMM registers (`0x00d24004` bit 1, 50 ms, `0x00556004` bit 1: the stock
   order, as the G9 panic notifier does), **only** if the devmem self-test passed (below);
4. still high: wait up to 22 s for `fp9929a.max_on_ms` to force them down;
5. still high: `a9-reboot bootloader` at once (kernel shutdown runs the FP9929A/a9_eink power-down; PON reason 2 ->
   fastboot).
A latched abort writes `/tmp/eink-seq/ABORTED`; every later stage verb refuses. The host then sends a final
`eink-seq safe` (prints the verification), fetches `/tmp/eink-seq` into `capture/eink-seq/phone/`, and continues with
`release` -> dead-man -> fastboot -> golden restore, exactly as in boots 2-6.

**devmem self-test** (in `pre`): for GPIO 2, 31, 36, 82, 86 (two TLMM tiles, outputs and an input), the `ctl`
register's OE bit must match debugfs `in/out`, the `io` bit must match debugfs `low/high`, and 36/86 must be
GPIO-function outputs. Addresses from `pinctrl-sm6115.c` (WEST 0x500000, EAST 0xd00000, `0x1000*n`, io `+4`); the
PG register equals stock's (0x00552004 bit 0, docs/mainline-fp9929a-pg.md). If it fails, the sampler uses debugfs
and step 3 is skipped. `CONFIG_STRICT_DEVMEM=y` without `IO_STRICT_DEVMEM` allows MMIO reads (INFERRED from the
config; MEASURED only in QEMU, where devmem is a fake).

**Stage isolation.** `a9-ctl eink-seq <stage>` starts the stage with `setsid` (HUP/PIPE ignored, own log) and only
waits for it, so a lost USB connection never stops a stage half-way; the stage still closes the gates and verifies.
One stage at a time (lock directory). The order is enforced on the phone (`pass.<previous>` files), not only by the
host.

**Host second opinion.** After each stage the host scans its own copy of the live kmsg stream
(`capture/kmsg-stream.txt`) between the stage's `MARK <tag> open/close` lines for kernel errors and known bad
signatures; a hit stops the run even if the phone said PASS. The host also pings a9-ctl after each stage.

### Review fixes (2026-10-15, after `runs/sol-e3-review.md`)

1. **Abort vs. opening (sequencer).** Every step that can lead to power (writing `zero_test`, each gate, launching
   `a9eink-pattern`) now runs through `locked`: it takes the same flock `safe_off` holds for its whole run and refuses
   (status 3) once `ABORTED` is latched. So an abort and the opening are totally ordered: a step either completed
   before `safe_off` started (which then undoes it) or sees the latch and does nothing; the first refused or failed
   step ends the opening for good (nothing is retried or resumed). The pattern is launched detached from inside the
   lock with fd 9 closed, so it never holds the lock. Regression tests inject a trip at each opening step
   (baseline, zero_test, after each gate), synchronously and concurrently (a separate `_safe` process racing the
   runner): in all 10 cases no later opening step ran, nothing was launched, the FP9929A never rose, all gates closed.
2. **Partial power-up and in-flight enable (kernel).** New patch `0021-regulator-fp9929a-...` (in `patches/`,
   applied by `build.sh`): an "energized" state set before the first PWRUP assertion; the panic notifier drops the
   owned pins unconditionally; `allow_power_on` is checked again under a gate lock right before PWRUP and right before
   VCOM_EN, and a write of 0 waits until no enable is in flight, so after `echo 0 > allow_power_on` returns no enable
   can raise a pin; `max_on_ms` is armed at energize time, so it covers startup. Host fault tests of the real driver
   source: `tests/mainline-fp9929a/`. The former pending PG patch is now `patches-pending/0022` on top of it.
3. **`--e3` stops after session A unconditionally** (latched), as the reviewer proposed.
4. **MMIO checks in `safe_off`:** every TLMM read and write in the force-off is checked and logged; a failed read
   skips that pin's write; the output bit is read back after each write.
5. **HV time bounds, corrected:** `hv_max_ms` is not a per-update cutoff (above). Nominal layers with a responsive
   kernel: the update itself (~3 s of zero frames in A, or a short pulse if a9_eink refuses), the guardian at N/85 + 10
   + 1 s after the session opens (14 s for A), then `safe_off`'s 3 s poll and the TLMM fallback; `fp9929a.max_on_ms`
   20 s from energize (with 0021); if all of that fails, 22 s more polling, then reboot. None of this is a hardware
   timer; a wedged kernel or a hard reset bypasses the software paths, and the bootloader itself raises the rails at
   the next boot (the mainline probe powers them down again).
6. **Not changed:** an active-high board can pass a9_eink's immediate check if PG is still low then and rise later;
   the update then runs its ~3 s of zero frames before the active-high verdict stops the stage. Sparse samples give
   `undetermined`, which stays a stop.

### Tests (offline, MEASURED 2026-10-15)

- `tests/mainline-eink-seq/run.sh`: the real `a9-eink-seq` under the static busybox sh, against fake hardware
  (`sim-lib.sh`: module parameters, a9_eink status/debugfs, TLMM registers, debugfs gpio, pm_genpd, DPU encoder
  status, kmsg records, and the current drivers' behaviour including a9_eink's raw-PG==0 check). 9 scenarios, 59
  checks, all pass at moderate host load: `pg-high` (verdict active-high, E3 FAIL, latched, E5 refused, no session B), `pg-stuck0`
  (low-no-toggle, stop), `good` (full E3 -> E5c with order refusals at every step, HV total ~10 s), `dsi-error`
  (watcher trips mid-session, the update ends with -125), `hv-stuck` (power-down ignored: TLMM force-off, verified,
  no reboot), `hang` (deadline/guardian), `no-drm` (pre FAIL, HV never rises), bad host verdict (latched), bridge
  init error with HV up (trip).
- `tests/mainline-eink-seq/qemu-e2e.sh`: the E Ink image's own kernel and initramfs (busybox, init, tcpsvd, a9-ctl,
  a9-eink-seq) in `qemu-system-aarch64 -M virt`, fake hardware in an extra cpio, driven by the real host code
  (`first-boot.sh --sim`, which refuses 172.16.42.x) over forwarded ports. **Result (host load ~90, guest 10-20x
  slower than real time): the plumbing and every abort path worked, the timing-based passes did not.** Verified in
  QEMU: image's own busybox/init/tcpsvd/a9-ctl, `pre` PASS over the network, setsid-detached stages, kmsg stream on
  2324, the watcher tripping on a9_eink's refusal (pg-high) and on the DPU timeout (dsi-error), the guardian and
  deadline aborts, host `STOP` -> `safe "host: e3"` -> `safe: verified PWRUP=0 VCOM_EN=0`, `fetch`. Not reached in
  QEMU: e3 PASS and E5a-c (the emulated simulator was too slow for the 3 s session; the host gave up waiting on `e3`
  while the guest still finished its abort path correctly). The E5a host verification (python in `eink_e5a_verify`)
  was run separately on fixtures: agreeing case accepted, a one-digit sha256 change rejected.
- After the review fixes (2026-10-15, serial run): `run.sh` **127/127** (11 scenarios plus 10 abort-injection cases);
  `tests/mainline-fp9929a/run.sh` all cases pass (normal order and delays, panic right after PWRUP, enable stalled in
  I2C while the gate is closed and the pins dropped, gate closed between PWRUP and VCOM_EN, max_on during a stalled
  startup, VCOM write failure, and with `--pending` the 0022 PG wait); the harness compiles the real patched driver
  against a pthread shim, so it checks the logic, not the panic path or the electrics.
- Earlier `run.sh` under load ~92: 58/59 checks; the one failure is the simulator's vsync ticker lagging
  (`e3b.vsync_rate 75 /s`), with the expected knock-on refusals. Under lower load (load ~45) all 59 passed.
- Test hooks in `a9-eink-seq` are environment variables (`A9_SEQ_ROOT`, `_DIR`, `_KMSG_CMD`, `_KMSG_LOG`, `_FW`,
  `_REBOOT`, `_PREPATH`, `_NICE`, `_SPEED`, `_SLOW`, `_BOOTLOG`); init and a9-ctl set none of them. `A9_SEQ_SLOW`
  (QEMU: stretches every deadline 20x, since the emulated guest runs 10-20x slower than real time on this host) is
  ignored unless the fake tree `A9_SEQ_ROOT` is set, so it can never widen a limit on the phone; `A9_SEQ_SPEED` too.
- Not testable offline: the real drivers' timing, the PG settle time, whether devmem reads TLMM on the phone, msm
  debugfs file names (`encoder-*/status`, `name` starting with `msm `), the pm_genpd line format for `mx`.

### Uncertain for the panel (read before running)

- **PG polarity** (above). With the current image the expected and safe outcome is a stop after E3 session A.
- **The PG settle time** is unknown. If PG needs more than 20 ms after VCOM_EN, the verdict may read `unstable` and
  the run stops (fails closed).
- **HV with DSI video on the glass for up to ~150 ms (A refused) or 3 s + 5 s (A and B accepted)** with zero
  frames: "zero = no drive" comes from the stock code (every update ends on zero), not from a measurement on this
  panel.
- **i2cget during HV** (session B only) reads registers 0x02/0x00 on the bus the bridge also uses; reads only,
  serialised by the I2C core, as in E2a.
- **devmem on TLMM** is new on the phone; it is used for reads in the sampler and, only as a last resort, for the
  force-off write. Both are gated by the self-test.
- **Nothing reads the glass.** E5c's success criterion "16 bands" needs a human or a photo afterwards; the glass
  then keeps the image (E Ink), so a photo after the restore still shows it.

## Corrections from the E Ink driver work (2026-10-15, docs/mainline-eink-driver.md, MEASURED there)

- **G14 settled:** the drive buffer is XRGB8888 as is (stock byte 0 = blue = `XR24`), not XBGR.
- Panel power-down is **per session** (2-3 s after the update queue empties), not per update.
- Reading `epd_thermal` never touches the chip; only the FP9929A temperature file powers it.
- **G10/G11/G16** are carried as patches 0012 (panel `prepare_prev_first`, tc358767 `pre_enable_prev_first`, HSE, 40079 kHz)
  and 0013 (DPU underflow colour 0). The E3/E4 player is the in-kernel `a9_eink` driver (patch 0011, module in the
  initramfs, never auto-loaded; `a9_eink.enable` is refused on the cmdline by `build.sh check`) instead of a
  userspace `a9-kms`. Its first-enable procedure (E5) is in docs/mainline-eink-driver.md.
- **G9 done** (patch 0016): the FP9929A panic notifier drops VCOM-EN, waits 50 ms, then drops PWRUP.
- The waveform firmware (`a9-waveform.bin`, vendor data) is not in the image and must never be committed.

## E2 procedure (image `69fd4fa6f9f7886d…`)

`first-boot.sh --e2` (implies `--e1`): after E1, `a9-ctl i2c-probe` (E2a, read-only: `i2ctransfer -y 0 w2@0x0f 0x05
0x00 r4`, `i2cget -f -y 0 0x48 0x02 b` and `0x00 b`, plus GPIO lines and the i2c0 IRQ count) into `capture/e2a.txt`;
then `eink-load bridge` (E2b), 30 s, capture into `capture/e2/` (now also `drm_connectors.txt`: status/modes per
connector). Checked in the source: the panel probe takes its GPIOs `ASIS` and powers nothing; tc358767's probe enables
VDDC (already high), pulses reset (gpio26) and reads the ID; `prepare` stays gated; with fbdev off nothing does a
modeset. Pass: ID `0x01 0x66 0x00 0x00`, VCOM reg 0x7e (or NACK with PWRUP low), tc358767 bound, `/dev/dri/card0`,
a 448x829 mode, GPIO 36/86 still low, no `fp9929a ... on:`.

## Status of the image-side gaps (2026-10-15, image `69fd4fa6f9f7886d…`)

- **G1 done:** `dispcc-sm6115.ko` in the initramfs; `CONFIG_SM_DISPCC_6115=m` pinned; `build.sh check` fails without it.
- **G2 done:** `a9-eink load core|bridge`, plain `load` refused; init accepts `a9.eink=core|bridge` and refuses `=1`;
  `a9.eink_power` removed (gates by hand only); `a9-ctl eink-load <stage>`, `eink-status`; `check` fails if a gate
  appears on the command line.
- **G3 done:** driverless `sync-state-hold` node (rpmpd VDDCX, `GCC_DISP_HF_AXI_CLK`, bimc path).
- **G4 done:** L18 pinned at 1.232 V. **G5 done** (boot 4 follow-up): L9 1.8 V feeds `bridge_vddc`.
- **G7 done:** DT fallback 2730 mV; patch 0006 logs the source (`from cmdline|dt|default`) and refuses power-up on the
  built-in default.
- G15 partly: `capture-dbg` now adds gpio, `pm_genpd_summary`, every `state_synced`, iio, drm, deferred again.
- G6, G8-G14, G16: open (E3/E4 items; FP9929A sequencing belongs to the E Ink driver work in `scripts/mainline/eink/`).

---


1. **dispcc is missing from the initramfs.** `build.sh:96` copies only the msm, tc358767 and panel closure, so the display subsystem (MDSS) can never probe.
2. **`a9.eink=1` already powers the bridge.** `a9-eink load` loads msm, tc358767 and the panel module. The bridge probe then switches VDDC (GPIO 31) on, toggles reset (GPIO 26) and reads the chip ID over I2C. It then defers because no DSI host exists, which drives GPIO 31 low. The cycle repeats on every deferred-probe retry.
3. **Nothing holds rpmpd sync_state off once msm loads.** The only rpmpd consumers in the compiled DTB are the display controller and DSI. As soon as they bind, rpmpd votes corner 0 on all eight domains, CX and MX included. USB, the I2C/UART controllers and the NoCs then run with no CX vote from Linux.
4. **The DSI and panel rails differ from stock.** Stock pins L18 at 1.232 V and enables L9 (1.8 V) for the panel; the mainline DTS has a wide L18 range and no L9 user.
5. **The VCOM fallback in the DTS is 2650 mV.** This phone needs 2730 mV (code 0x7e).

Labels: MEASURED = read from code, the compiled DTB, the built tree or the stock boot logs. INFERRED = deduced.

## Gaps and fixes

| # | Where | Gap | Fix |
|---|---|---|---|
| G1 | `build.sh:96`; `a9.config` | `dispcc-sm6115.ko` (`CONFIG_SM_DISPCC_6115=m`) is not in the initramfs (MEASURED: 16 modules, no dispcc). MDSS needs it for its clocks, MDSS_GDSC and the CORE_BCR reset. gpucc is not needed. | Add `dispcc-sm6115` to the closure list. Pin `CONFIG_SM_DISPCC_6115=m` in `a9.config`. Make `check()` fail if `dispcc-sm6115.ko` is missing. Keep gpucc and bwmon out: they are what keeps gcc and icc sync_state pending (see G3). |
| G2 | `a9-eink:21-25`; `init:34-36,182-188` | `load` = all three modules (MEASURED code path, gap 2 above). The init comment says `fp9929a.allow_power_on=1` is needed as a kernel parameter, but `a9-eink allow` writes both gates at runtime. | Staged loads. `a9-eink load core` = dispcc-sm6115 + msm. `a9-eink load bridge` = core + panel + tc358767. Init accepts `a9.eink=core\|bridge` and refuses `=1` with a log line. Drop `a9.eink_power` from init: gates are opened only by hand. Add a9-ctl verbs `eink-load <stage>`, `eink-allow`, `eink-deny`, `kms-zero`, `kms-play` so `first-boot.sh` can script the steps. |
| G3 | DTS (new node) | rpmpd sync_state (gap 3 above). Checked: gcc sync_state is held by cpufreq@f521000 (probe fails with `cpufreq.off=1`), gpucc and the GPU SMMU (wait on gpucc). icc is held globally by bwmon `pmu@1b8e300` (module not loaded): `icc_sync_state` needs every provider. dispcc's own genpd sync_state fires when MDSS binds, which is fine. | A driverless consumer at the root: `sync-state-hold { compatible = "hisense,hlte556n-sync-hold"; power-domains = <&rpmpd SM6115_VDDCX>; clocks = <&gcc GCC_DISP_HF_AXI_CLK>; interconnects = <&bimc MASTER_AMPSS_M0 RPM_ALWAYS_TAG &bimc SLAVE_EBI_CH0 RPM_ALWAYS_TAG>; };`. With strict fw_devlink (the 7.2 default, `CONFIG_FW_DEVLINK_SYNC_STATE_TIMEOUT` unset) an unbound consumer keeps them pending for good. Never write any `state_synced` file. |
| G4 | DTS:291-294 | DSI vdda = L18 at 1104-1312 mV, so the core starts it at 1.104 V. Stock DSI ctrl `vdda-1p2` = L18 fixed at 1,232,000 µV, load 21.8 mA (MEASURED, `base_0.dts:14621-14637`). | `pm6125_l18` min = max = 1232000 (UFS is disabled). |
| G5 | DTS:132-146, 275-278 | Stock panel supply = `vddio` L9 fixed at 1.8 V, load 62 mA (MEASURED, `dtbo_0.dts:556-570`). Mainline has no L9 consumer, so L9 keeps whatever the ABL left. | Pin L9 to 1800000-1800000 and add `vin-supply = <&pm6125_l9>` to `bridge_vddc` (DT only: L9 then comes up before VDDC). Side effect: a boot-on regulator with a supply enables that supply at registration, so the default boot will send an L9 1.8 V enable. Alternative: a `vddio-supply` in patch 0003, enabled first in prepare and never dropped (stock keeps the 1.8 V rails up). |
| G6 | DTS:98-112 (comment) | `regulator-boot-on` on a fixed regulator requests the GPIO `GPIOD_OUT_HIGH` (MEASURED, `fixed.c:191,295`). GPIO 31 is driven high on every boot, not "kept as left". With `regulator_ignore_unused` on the command line there is no 30 s cleanup either. | Fix the comment. Behaviour probably matches the stock idle state (stock keeps vdcc_en high between updates, INFERRED). The bootloader's own level cannot be observed with this node. |
| G7 | DTS:499; patch 0001 at `+342` | The 2650 mV fallback would give code 0x74. The ABL passes `androidboot.eink_vcom=2730` to stock (MEASURED: stock kernel cmdline in `blpull/boot-00006/kmsg.txt:79`, and `diag`+0x12B0). Whether it appends that for our boot_b image is not yet measured. | DT `fitipower,vcom-millivolt = <2730>`. In the driver, log the VCOM source (cmdline/DT/default) and refuse power-up when the source is the built-in default. |
| G8 | DTS:498; patch 0001 at `+181..+219` | PG polarity is MEASURED from stock: `active end, state=0` / `enter sleep end, state=1`, so GPIO 82 is low when the rails are good. The driver logs PG but never checks it, and logs nothing at power-down. | `pg-gpios ... GPIO_ACTIVE_LOW`. In `fp9929a_enable`, if PG is not asserted after VCOM-EN, power down and return `-EIO`. Log `off` in `fp9929a_power_down` and `PG=%d` again 50 ms after the probe's power-down (that measures the polarity for free in boot 2). |
| G9 | patch 0001 | No backstop if the kernel dies with HV up. The display keeps scanning the last frame through `panic=10` or a 32 s watchdog, which holds the panel under DC bias (INFERRED damage path). | A panic notifier (drives VCOM-EN then PWRUP low, non-sleeping TLMM). A `max_on_ms` module parameter (default 5000 for tests) whose delayed work powers down. E3/E4 command line `oops=panic panic=1`. |
| G10 | patch 0003 at `+217` | `prepare` (HV on) runs before the DSI host and bridge pre_enable. Stock order (MEASURED, kmsg 6082-6093): DSI on, then panel lines, then FP9929A, then bridge init. | `a9->panel.prepare_prev_first = true;`. |
| G11 | patch 0002 at `+88..+100`; `tc358767.c:1625` | `atomic_enable` is void: a failed init sequence only logs, while HV stays up. | Count failures into a read-only sysfs attribute `init_errors`. a9-kms checks it after the modeset and aborts. Optional parity items: per-enable reset pulse on GPIO 26 (stock resets on every power-on); `MIPI_DSI_MODE_VIDEO_HSE` (stock `h-sync-pulse = 1`). |
| G12 | DTS (no pinctrl) | Stock `mdss_dsi_active` = GPIOs 2/25/26/31/33/34/81 as gpio, 8 mA, bias-disable. `fp9929a_active` = 36/86 at 8 mA, 82 at 2 mA, bias-disable (MEASURED, `dtbo_0.dts:1062-1150`). Mainline sets none. | Add default pinctrl states on the panel, bridge, `bridge_vddc` and FP9929A nodes. These are TLMM config writes only. |
| G13 | no tool exists | Nothing in the initramfs does a modeset (no fbdev, MEASURED), and there is no KMS client. | New static `a9-kms` using raw DRM ioctls: `zero <n>` and `play <file> --max N`. Zero-filled first buffer, page flip per vblank, hard `alarm()` cap, SIGTERM/SIGINT handler. It always ends with a zero frame and then a CRTC-off commit. It refuses unless both gates read 1 and the file's sha256 is on an allowlist. |
| G14 | E4 data path | Which DRM format puts the TCON's byte 0 on the same DPI lanes as stock is unresolved. `librecovery_ui.so` has a fourcc table RA24/XB24/AR24/BA24 at file offset 0x17750 (MEASURED); the selected entry is unknown. | Offline: find the code that indexes 0x17750 (and the composer's equivalent). E4 uses that fourcc (XBGR8888 if stock is RGBA_8888, INFERRED). Zero frames (E3) are the same in every format, so E3 does not depend on this. |
| G15 | `a9-ctl` capture sets | Missing reads: `/sys/kernel/debug/gpio`, `pm_genpd/pm_genpd_summary`, every `state_synced`, regulator `state`/`microvolts`, iio, `/sys/class/drm`. | Add them to `capture-dbg`. The debugfs gpio file reads only the valid (non-reserved) pins. |
| G16 | optional | DPU underflow colour is hard-coded `0xff` (`dpu_encoder_phys_vid.c:89`, MEASURED), so an underflow sends code 3 on every pixel. Stock is the same (`underflow-color 0xff`). | Optional: 0 for this panel. |

The GPIO reservations are fine for this path (MEASURED). Every pin the E Ink stack needs is still valid: 0/1 (i2c0), 2, 25, 26, 31, 33, 34, 36, 81, 82, 86. DSI needs no TLMM pins (video mode, no TE pin). The 14nm-2290 DSI PHY needs no regulator in mainline. The DSI controller wants `vdda` (L18) and an optional `refgen` (gets a dummy). The stock PHY also voted MX (`vdda-0p9` = rwmx, at least SVS); G3 keeps the boot vote.

**The bootloader leaves the display running (MEASURED, stock).** The stock kernel logs `cont_splash enabled in 1 of 1 display(s)`, its FP9929A probe succeeds in writing VCOM at 1.48 s, and it puts the FP9929A to sleep at 19.6 s. So at kernel entry MDSS and DSI are live and HV is likely up. Our FP9929A probe powers HV down at boot, which makes a later msm load safe for the glass: SMMU faults or an underflow while the splash is torn down drive nothing (INFERRED). Loading msm: dispcc reconfigures `disp_cc_pll0` on a live pipeline, MDSS probe resets the core (`msm_mdss_reset`, MEASURED), and a short burst of SMMU context faults on stream 0x420 between attach and reset is possible (INFERRED, non-fatal).

---

## (A) Boot 2: capture only, no E Ink step

**Image:** the current one (no G-fixes needed). `A9_CMDLINE_EXTRA=""`. Capture with a9-ctl `capture` and `capture-dbg`, plus the G15 reads done by hand over telnet, twice 60 s apart.

**Pass criteria (all must hold before any E step):**
1. **Command line:** has `qcom_pon.beacon=2` and **`androidboot.eink_vcom=2730`**. No `fp9929a.`, `panel_hisense`, `a9.eink` items.
2. **dmesg FP9929A:**
   - `fp9929a 0-0048: at probe: PWRUP=x VCOM_EN=y PG=z` is present. 1/1/0 is the expected stock-like state (INFERRED). If so, `bootloader left the E Ink rails up: powering down` must follow.
   - Then `VCOM 2730 mV -> code 0x7e; power-up refused (safe default)`. **2650/0x74 = fix G7 before E3.**
3. **GPIO state:** 36 and 86 out low; 82 input (records the PG off level); 31 out high (G6). Record 2, 25, 26, 33, 34, 81 (bootloader state; E2a needs them).
4. **i2c0:** bound to `geni_i2c` (`4a80000.i2c`), with `0-0048 -> fp9929a` and `0-000f` unbound. The **i2c0 IRQ count is exactly 0** in both `/proc/interrupts` reads (no I2C at probe, MEASURED code). No `geni`, `i2c` or `NACK` lines in dmesg.
5. **Modules:** `/proc/modules` empty (tc358767, msm and the panel not loaded). No `/dev/dri`.
6. **`devices_deferred`:** only `59a0000.iommu` (waits for gpucc) is expected (INFERRED). Any i2c, geni, qup, usb, spmi, pon, rpm or `c600000.iommu` entry is a FAIL.
7. **sync_state:**
   - There are `sync_state() pending due to ...` lines for the rpmpd power controller, gcc and the interconnects.
   - There is no `Timed out. Forcing sync_state()`.
   - `state_synced` = 0 for rpmpd, gcc and every icc provider.
8. **Clocks:** `clk_summary` has no `disp_cc_*` rows. Record the hardware-enable state of `gcc_disp_ahb_clk` and `gcc_disp_hf_axi_clk`; Y confirms the live splash.
9. **Power domains:** `pm_genpd_summary` shows `usb30_prim` on. Record `hlos1_vote_mm_snoc_mmu_tbu_rt` (gate for E3: it must be on).
10. **Regulators:** l4 928 mV, l12 1.8 V, l15 3.08 V in use; `epd_vpanel` disabled; `tc358767_vddc` enabled.
11. **No error signatures:** `SError`, `Internal error`, `Unhandled context fault`, `XPU`, `WARNING:`, `BUG` absent. Uptime keeps growing for at least 10 min with `stay`. No IRQ storm (no counter grows by more than about 1000 in 60 s, USB excepted).
12. **Sensors:** `epd_top_therm` (iio) and `vbat_sns` readable; record them.

---

## (B) E1: load display modules, no panel power

**Image changes:** G1, G2, G3, G15, ideally G4/G5. **Cmdline:** `A9_CMDLINE_EXTRA="oops=panic"`, the same image for E1-E3 where possible. Run `a9-ctl eink-load core` in the same session as A, after A's capture has been fetched and has passed (a cmdline `a9.eink=core` works too but needs its own image).

**What it does (MEASURED code):**
- dispcc probe: PLL0 configure, MDSS_GDSC registered.
- MDSS: clocks, GDSC on, BCR reset, interconnect votes, children populated.
- The display controller, DSI host and DSI PHY bind. There is no DRM device, because the bridge is absent.

**Must not happen:**
- GPIO 36/86 or 31/26 change.
- Any I2C on i2c0 (IRQ count stays 0).
- rpmpd `state_synced` becomes 1 (without G3 it **will** become 1; that is the risk event).
- More than about 10 `Unhandled context fault` lines, a reset, or USB loss in 5 min.
- `/dev/dri/card0` appears.

**Capture:** dmesg delta, modules, `devices.txt` (mdss, dpu, dsi and dsi-phy have drivers), `state_synced` (dispcc 1, others 0), `pm_genpd_summary` (`mdss_gdsc` goes off after autosuspend), `clk_summary` (`disp_cc_*` rows present), interrupts.

| Seen | Meaning | Next |
|---|---|---|
| All bound, criteria hold, 5 min stable | MDSS path fine | E2 |
| MDSS defers ("wait for supplier") | dispcc missing or a cycle in fw_devlink | fix the closure |
| Fastboot within seconds of modprobe | SoC reset (PLL/GDSC on a live pipeline, or sync_state) | check `state_synced`/G3 in the next capture; try loading msm without dispcc first |
| USB drops after 1-10 min | CX/MX vote dropped (G3 not effective) | do not proceed |

---

## (C) E2: bridge and FP9929A I2C, read-only

No code change is needed for the reads: `CONFIG_I2C_CHARDEV=y` and busybox has i2ctransfer/i2cget (MEASURED). Never use i2cdetect or i2cset on bus 0.

**E2a, raw reads, nothing loaded beyond core:**
- `i2ctransfer -y 0 w2@0x0f 0x05 0x00 r4` should return `0x01 0x66 0x00 0x00` (ID 0x6601, which mainline accepts).
- `i2cget -f -y 0 0x48 0x02 b` (VCOM register; 0x7e would show the ABL programmed it).
- `i2cget -f -y 0 0x48 0x00 b` (temperature).
- A NACK from 0x48 with PWRUP=0 is acceptable and is recorded. Stock wrote VCOM only while its rails were up.

**E2b:** `a9-ctl eink-load bridge`.
- The panel probe drives nothing (ASIS).
- The tc358767 probe: VDDC enable (already high), reset 26 low then high, regmap ID read, DSI attach. Then msm binds (`[drm] Initialized msm`), `/dev/dri/card0` appears and `card0-DPI-1/modes` = `448x829`.
- No modeset happens (fbdev emulation is off), so `prepare` is never called.

**Must not happen:** `fp9929a ... on:`; GPIO 36/86 high; `refusing to power` (that would mean a client tried a modeset); repeated `can not read device ID`.

| Seen | Meaning | Next |
|---|---|---|
| E2a ID 0x6601, E2b bound, card0 present | bridge reachable with lines as left | E3 |
| E2a NACK at 0x0f, E2b `can not read device ID: -6/-107` | bridge I/O unpowered or I2C routed away: GPIO 2 (`i2c-mux-sel`, stock sets it 1 before the init) or 33 (`epd-power-on`) low | code change: a panel-driver `lines` parameter that raises power/xoe/mode/xon plus mux without the FP9929A (stock idle state, INFERRED low risk), then rebind tc358767 |
| `invalid device ID` | a different chip, or address 0x0f reaches something else | stop, review |
| tc358767 defers forever | no DSI host registered | check the msm DSI host registration |

---

## (D) E3: first power-on (FP9929A plus panel), zero drive

**Image:** G4, G5, G7-G13. **Cmdline extra:** `oops=panic panic=1`. Gates are never on the command line; add a `check()` FAIL if `allow_power_on`, `hlte556n_eink.enable` or `eink_power` appears.

**Preconditions:** A, E1 and E2 pass; `vbat_sns` ≥ 3.8 V; `epd_top_therm` 5-45 °C; `hlos1_vote_mm_snoc_mmu_tbu_rt` on; GPIO 36/86 low; `init_errors` attribute present; USB stable.

**Step:** `a9-eink allow` → `a9-kms zero 85` (1 s at 85 Hz: modeset with an all-zero buffer, 85 flips, zero frame, CRTC off) → `a9-eink deny`.

**VCOM:** source is the cmdline 2730 mV (ABL, from `diag`), code 0x7e. The DT fallback is 2730 after G7. Never 2650.

**Expected order:**
- DSI on (`prepare_prev_first`) → lines → 10 ms → PWRUP → 5 ms → VCOM write (one NACK then success is normal: stock logs `-107` then `num=1`, MEASURED) → temperature read → 20 ms → VCOM-EN → PG asserted → bridge init (stock NACKs once at reg 0x08xx, MEASURED `buf[0]=0x8`; the table writes 0x800 twice, so a retry is harmless).
- `fp9929a ... on: VCOM 2730 mV (code 0x7e), temp T C, PG ok`, with T within ±10 °C of `epd_top_therm`.
- After CRTC off: `fp9929a ... off`, GPIO 36/86 low, PG back to the off level.

**Abort criteria:** any one triggers SIGTERM to a9-kms (zero frame, CRTC off, FP9929A off). The kernel backstops are `max_on_ms`, the panic notifier and `oops=panic panic=1`.
- VCOM code is not 0x7e.
- PG is not asserted.
- `init_errors` > 0.
- `main link stream start error`.
- DSI error, timeout or underrun lines.
- Any SMMU fault.
- Flip interval outside 11.76 ms ±5 % (expected 84.99 Hz from the mode).
- HV still up 200 ms after CRTC off.
- The glass changes at all. Zero means no drive: every stock update ends with an all-zero frame (MEASURED from the stock code).

| Seen | Meaning |
|---|---|
| on/off lines, PG ok, `init_errors` 0, glass unchanged | power path proven, go to E4 |
| VCOM write fails 5× | FP9929A not reachable with PWRUP high (mux/lines); the driver already powered down |
| PG not asserted | rails did not come up, or the PG polarity is wrong (compare with A, item 3) |
| init errors | bridge state with HV up; the kill path ran; revisit E2 |
| glass changes on zero frames | format, lane or underflow problem (G14/G16); stop before E4 |

**Panel damage risks (INFERRED):**
- HV up while a non-zero frame is held (panic, hang, stuck CRTC) → DC bias, ghosting, possibly permanent damage after minutes (G9 covers this).
- Wrong VCOM → DC offset, image retention (G7).
- HV before the link → the DPI lines idle under HV (G10).
- Underflow (code 3 everywhere) (G16).
- Wrong lane mapping → stray drive (G14, E4 only).
- Temperature bin wrong → poor image, not damage.
- The 1.8 V lines staying up is the stock behaviour and fine.

---

## (E) E4: first TCON frames

**Image:** E3 plus the G14 fourcc resolved. The frames are pre-rendered on the host: `scripts/mainline/tcon` CLI with the phone's own composer tables, temperatures t1 = FP9929A temp from E3 and t2 = `epd_top_therm`. Each frame is 448×829×4 = 1,485,568 bytes. Push them over NCM into `/tmp`, sha256 checked and allowlisted in a9-kms.

- **E4a:** the stock init flash only (`--init`, 51-109 frames by temperature bin, about 1.3 s). It is a global flash, every pixel the same code, and it is what stock runs at TCON start. `a9-eink allow; a9-kms play init.bin --max 120; a9-eink deny`.
- **E4b:** one full Clear update with the clear flag set (final pass type 2) to a test image: white with a black rectangle, old levels = white after the init flash (INFERRED). About 38 frames.

**Pass:**
- E3's power criteria again, and no late flips (at most 2 % repeated).
- The glass flashes (E4a) or shows the rectangle (E4b). This needs a human glance or a photo: nothing reads the glass under mainline, and that is a third reason to call a human beyond the two in CLAUDE.md. Raise it before E4.

| Seen | Meaning |
|---|---|
| expected image | pipeline done |
| nothing changes, logs clean | byte 0 is on the wrong lanes (G14) → try the other fourcc once |
| partial or noisy image | timing/pacing or bridge PPI values, or the temperature bin |
| image fades or ghosts after the CRTC is off | HV-off path or VCOM; stop |

Later, the in-progress `scripts/mainline/eink/driver/a9_eink_engine.h` (another agent's kernel engine) can replace the userspace player.

---

**Reproduction (offline):** `scratchpad/ml2/sub-eink/consumers.py <dtb> <provider-path-substrings>` lists enabled consumers per provider from the compiled DTB (rpmpd, gcc, icc, dispcc, gpucc). The stock supply, pinctrl and PG facts come from `scratchpad/kre/dt/{base_0,dtbo_0}.dts` and `scratchpad/blpull/boot-00006/kmsg.txt`. No phone access and no repo writes.
