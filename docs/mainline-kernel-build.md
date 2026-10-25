# Mainline kernel image for the A9: build, first-boot incident, fail-safe entry

**Date:** 2026-10-15. **Status:** image rebuilt and checked offline (both ABL routes PASS); the first-boot
procedure (`scripts/mainline/first-boot.sh`) is written but **not run**. Nothing in this revision touched the
phone.

**Boot 1 of this design ran 2026-10-15 03:47 UTC (main, user-approved; `runs/20261015T034755Z-mainline-first-boot`):**
gadget `1d6b:0104` up at t+9 s (cdc_acm + cdc_ncm on the host), dead-man → bootloader fastboot at t+135 s, restore
MATCH, slot a, Android up. **No log captured:** `ttyACM0` appeared ~15 ms after the script looked for it, and the user
is not in `dialout`. Fixed for boot 2: section 7.

**Boot 2 (2026-10-15 04:15 UTC, `runs/20261015T041539Z-mainline-first-boot`):** capture over NCM worked (released, fastboot
at t+134 s, restore MATCH). Beacon armed (previous reason 0, so the boot chain had left 0 after `fastboot reboot`).
Stage-A verdict: [mainline-eink-steps.md](mainline-eink-steps.md). Changes since: second (late) capture, `--e1`, E1
gaps G1-G4/G7, i2c2 FIFO, PMI632 telemetry built in (patches 0007-0009, sol-pmic `419666a`): OBSERVE mode, no charger/QG
writes except one readback-verified stock FV write (0x1070 := 0x55 = 4.450 V, only from 0x5a/0x55, never retried);
firmware charging continues; `pmi632_a9.fv_stock=0` makes it zero-write (`scripts/mainline/pmic/`, docs/mainline-pmic.md), patch-id based patch application in `build.sh`.

**Boot 3 (2026-10-15 05:29 UTC, `runs/20261015T052915Z-mainline-first-boot`):** late capture and E1 (display modules
loaded, no power), no reset in 383 s; verdict in [mainline-eink-steps.md](mainline-eink-steps.md). Image since: `a9-ctl
i2c-probe` (E2a), `drm_connectors.txt`, `first-boot.sh --e2`.

**Boot 4 (2026-10-15 05:55 UTC):** E2 safe, but the TC358767 NACKs with the DSI host stopped; see
mainline-eink-steps.md "Boot 4 verdict". Since then: L9 feeds the bridge VDDC, patch 0015, a pre-E1 I2C probe.

**Boot 5 (2026-10-15 06:18 UTC):** the bridge answers before E1 (live bootloader DSI) and not after; E1 then lost USB.
Since then: kmsg stream on tcp 2324, IRQ storm monitor, `--e2pre` opt-in, liveness checks, patch 0016 (G9).

**Current image:** `~/A9-build/mainline/out/a9-mainline-recovery.img`, 13,418,496 bytes, sha256
`69fd4fa6f9f7886df52770715a3bed73907d5b20985b0c836bc1b3be513f12aa` (linux 7.2.10 + patches 0001-0016 (0016 FP9929A panic notifier) (0014 selinux_force_permissive from the Android work, 0015 tc358767 ID at probe): beacon, scm, FP9929A, PMI632 observe telemetry, a9_eink DRM driver + enable order + underflow colour; initramfs with `a9-ctl`, dispcc, a9_eink not auto-loaded, no waveform). For
`dtbo_b`: `out/recovery_dtbo.img` (null overlay, 391 bytes, `.sha256` next to it). `build.sh check`: abl-check
recovery PASS, abl-check boot + that dtbo PASS, cmdline gates PASS. Panel and FP9929A stay unpowered by default. Labels: **MEASURED** = read from a file, a disassembly or an emulation (command in the appendix);
**INFERRED** = deduced, not observed.

Build files: [`scripts/mainline/kernel/`](../scripts/mainline/kernel/README.md). Plan and background:
[mainline-plan.md](mainline-plan.md). ABL emulator: [abl-check.md](abl-check.md).

## 1. The incident (2026-10-15, 02:32-02:57 UTC)

Facts as recorded in [state-log.md](state-log.md) (MEASURED by main):

- An agent flashed the first image (sha256 of that build not kept) to `recovery_b` from bootloader fastboot, ran
  `fastboot set_active b`, then `fastboot reboot recovery`.
- The ABL accepted the image (the kernel was started from `recovery_b`). The E Ink kept the bootloader logo, as
  expected: the image never powers the panel.
- The host saw **no USB device** from it. The phone reset repeatedly (cause unknown, no logs) and every reset
  started the mainline image again. The 900 s dead-man never fired.
- Recovery needed the EDL cable: TWRP written back to `recovery_b` over EDL.

Why every reset came back (MEASURED, ABL disassembly, section 4):

1. `fastboot reboot recovery` in this ABL writes `boot-recovery` into the misc BCB (`0x346fc` ->
   `0x1ccb0`), then resets with PON reason 0. `RecoveryInit` (`0x1ce50`) reads the BCB at **every** boot and sets
   BootIntoRecovery for `boot-recovery` or `boot-fastboot`. Nothing clears it except the recovery itself. A
   mainline image that never touches storage cannot clear it, so each reset re-entered mainline.
2. Recovery boots never decrement the A/B retry count (`0x1350` returns "no" when ToRecovery is set). The
   slot-fallback safety net therefore never triggers on a recovery loop.
3. The keys were no way out: the Hisense boot-flow stage (`0x366b8`) reads the key only when the power-on reason
   is KEYPAD (0x80) or CABLE (0x40). The resets in the loop are PS_HOLD resets with PON reason HRST (0x01), where
   no key is looked at. From power-off, Vol Down + Power (key code 8) selects **recovery**, i.e. the mainline
   image again (code MEASURED; "Vol Down = code 8" INFERRED from the a9-recover skill's "Vol Down + Power →
   Recovery"). And a phone in a reset loop cannot be powered off: holding Power is an S2 reset, also HRST.

Why the dead-man never fired: the resets came earlier than 900 s. That puts them before or early in userspace.

## 2. Likely reset causes, ranked (all offline; no log exists)

| # | Cause | Evidence | Status | Fix in this image |
|---|---|---|---|---|
| 1 | **TLMM read of a TZ-owned GPIO** at TLMM registration (gpiolib reads the direction of every valid line). XPU violation = immediate reset, at core initcall, before any USB | gpiolib/pinctrl-msm code (MEASURED); which pins TZ owns: unknown. Upstream SM6115 boards reserve 14-17, fxtec also 0-3; stock DT reserves none | INFERRED likely class; pins unknown | DTS reserves **every** pin the image does not use (93 of 114). Kept: 0 1 6 7 12 13 2 21 25 26 31 33 34 36 71 80 81 82 84 86 108 |
| 2 | **PSCI idle / OSI cluster states, secondary CPU bring-up** before anything is logged | upstream `sm6115.dtsi` idle states with psci domains; untested firmware here | INFERRED | `cpuidle.off=1 maxcpus=1 cpufreq.off=1`, `CONFIG_ARM_PSCI_CPUIDLE` off (it sets OSI mode regardless) |
| 3 | **A rail or clock the bootloader left on gets dropped** (late cleanup, sync_state) | `clk_ignore_unused`/`pd_ignore_unused` already set; RPM regulators send nothing until enabled (`qcom_smd-regulator.c` MEASURED); fw_devlink sync_state is strict in 7.2 (MEASURED), MDSS (enabled, driver not loaded) blocks rpmpd/icc sync_state (INFERRED) | INFERRED, lower | add `regulator_ignore_unused`; keep `&mdss` enabled (DTS comment) |
| 4 | **Memory map**: stock reserves 0x60000000+0x5c00000 ("removed"), upstream only 0x3900000 | stock base DTB vs `sm6115.dtsi` (MEASURED) | was fixed in the first image (`&removed_mem` override) | unchanged; every other stock region is covered by an upstream one (subagent table: section 6) |
| 4b | **QTEE smcinvoke probe** with null buffers; SHM bridge was initially grouped with it | SCM code; stock REA corrected the SHM assumption in FP5 | QTEE effect INFERRED; stock SHM enable now MEASURED | Keep `qcom_scm.skip_qtee=1`; FP5 restores SHM bridge and QSEE buffer registration (fingerprint README) |
| 5 | **SMMU takeover** of streams the bootloader left running (display) | `qcom_smmu_cfg_probe` keeps every valid bootloader SMR as bypass (MEASURED code) | INFERRED handled | none needed |
| 6 | **Watchdog** armed by the boot chain, bite before `qcom-wdt` probes | ABL calls `SetWatchdogTimer` only in fastboot; UEFI-watchdog backing unknown | INFERRED, low | `qcom-wdt` built in pets a running WDT (`WATCHDOG_HANDLE_BOOT_ENABLED`). 32 s × 32764 Hz = 1,048,448 < 0xFFFFF: no counter overflow (MEASURED arithmetic) |
| 7 | **panic → `panic=30` → reboot → BCB → mainline** (the loop period) | cmdline of the first image | MEASURED mechanism | `panic=10`, and every post-probe reset now lands in fastboot (section 4) |

What does **not** explain it: header/DT selection (abl-check PASS, and the phone did start the kernel), earlycon on
`0x4a90000` (stock's own cmdline has `earlycon=msm_geni_serial,0x4a90000`, MEASURED from the stock boot logs).

## 3. USB (why no gadget)

The USB description matches the upstream boards that enumerate in device mode (sm4250-oneplus-billie2,
sm6115p-lenovo-j606f): legacy glue `qcom,dwc3` (dwc3-qcom-legacy.c, built), `dr_mode = "peripheral"`, vbus
override set by the glue in peripheral mode (MEASURED code, `dwc3-qcom-legacy.c:806-810`), HS PHY only, role switch
removed. **INFERRED:** the gadget was missing because the kernel reset before init reached
it, not because of the USB description. Changes: init now fails fast (no UDC after 40 s → reboot to the bootloader),
which turns "USB broken" into a distinct, visible outcome. Two fixes from the review (section 6): QUSB2 rails pinned
to stock voltages (L4 would otherwise be enabled at 488 mV), and `qcom,select-utmi-as-pipe-clk`.

## 4. Fail-safe entry (the important part)

### 4.1 How the ABL picks what to boot (MEASURED, LinuxLoader `1dd55dcb…`, entry `0x13e4`)

Order in `LinuxLoaderEntry`:

1. `boot_flow_check_event_stage` (`0x366b8`, Hisense): PON reason (KEYPAD/CABLE/HRST/…), ConIn key scan code,
   charger, warm reset, `diag` tags → may set ToFastboot (CABLE + key 1), ToRecovery (KEYPAD + key 8), AlarmBoot.
2. `GetRebootReason` (`0x193c`): the reset-reason protocol `a022155a-…`, i.e. the reason the previous OS wrote into
   PON `SOFT_RB_SPARE`. Jump table at `0x44f88`: **1 → ToRecovery, 2 → ToFastboot**, 3 alarm, 4/5 dm-verity,
   6 keys clear; 0x28 → mode 2, 0x29 → 3, 0x2a → 1 (Android's own reboot code), 0x2c → silence; anything else
   "not handled, defaulting to Normal Boot". **This ABL never calls ClearResetReason** (only `GetResetReason`
   at `[proto+8]`).
3. `RecoveryInit` (`0x1ce50`): misc BCB `boot-recovery` or `boot-fastboot` → ToRecovery.
   (`bootonce-bootloader` is ignored.)
4. **If ToFastboot: fastboot. Checked first**, so reason 2 wins over a `boot-recovery` BCB.
5. Else `LoadImageAndAuth` → `FindBootableSlot` (`0x235d8`): for a slot that is not successful, retry count--
   **unless** ToRecovery/ToFastboot, or (cable present AND charger screen enabled). On this phone the charger
   screen is off (golden `devinfo` byte 15 = 0, MEASURED). Retry 0 → `HandleActiveSlotUnbootable` (`0x232f8`):
   switch to the other slot if it is successful and reboot.

How the reasons get written (MEASURED): `fastboot reboot` and `reboot-recovery` → `RebootDevice(0)` (`0xfe68`:
`ResetSystem` with `RESET_PARAM` = 0); `reboot-bootloader` → `RebootDevice(2)`; upstream Linux `qcom,pm8998-pon`
writes `mode << 1` under mask 0xfe, and the ABL's own `EPD_SetResetReason` writes 4 under 0xfe for reason 2, so the
encodings agree. `mode-bootloader = <2>`, `mode-recovery = <1>` in `pm6125.dtsi`.

**Open (could not be settled offline):** whether XBL's `GetResetReason` clears the register (one-shot). The ABL
re-arming reason 2 when it enters fastboot hints that it does (INFERRED). The design below does not depend on it.
The new kernel logs `beacon: reboot reason was X, now 2`, which measures it at the next boot.

### 4.2 The beacon (patch 0004)

`qcom_pon.beacon=2` (in the image cmdline) writes PON reason 2 when the PON device probes;
`/sys/module/qcom_pon/parameters/beacon` sets/reads it later (read = from the PMIC over SPMI). init re-asserts 2
before anything else. Result: **after the PON probe, any reset - panic, watchdog bite, plain `reboot` - lands in
bootloader fastboot**, whatever the BCB says and whichever partition the image is in, and it costs no A/B retry.
The register is the one channel that survives this phone's hard PS_HOLD reset (DDR does not; section 5).

### 4.3 Route: boot_b + dtbo_b, entered with a plain reboot (recommended)

| | boot_b + dtbo_b, slot B active (chosen) | recovery_b, slot B active | recovery_a, slot A active |
|---|---|---|---|
| Entry | `fastboot reboot` (reason 0, BCB untouched) | reason 1 by raw `reboot(2)` from Android B, BCB empty | same, from A |
| Reset before PON probe | normal boot of B again; retry 7 → 0, then the ABL boots **slot A Android by itself** (bounded, MEASURED code) | reason 1 if not one-shot → mainline again, **unbounded** | same, on the main slot |
| Reset after PON probe | fastboot | fastboot | fastboot |
| Writes | boot_b, dtbo_b (golden copies exist) | recovery_b (TWRP gone from B) | recovery_a (TWRP gone from main) |
| Key escape | none needed | none (section 1) | none |

Only the first route has a measured termination bound for an early crash. Its cost is slot B's Android until the
restore, and at most 6 more mainline boots (each identical) before the fallback. The recovery route becomes
acceptable once a boot has proven that the kernel reaches the PON probe (log line above).

`scripts/mainline/first-boot.sh` (CHECK-ONLY by default; `--offline` is host-only; `--write` needs a TTY,
`A9_MAINLINE_HUMAN=yes` and a typed code):
1. checks: image sha256, abl-check boot+null dtbo and recovery, cmdline has the beacon, golden `boot_b`/`dtbo_b`
   match `SHA256SUMS`; phone in Android on slot a; battery ≥ 60 %; `boot_b`/`dtbo_b` on the phone = golden
   (readback); misc BCB not `boot-recovery`/`boot-fastboot`;
2. `adb reboot bootloader`; in the bootloader: current-slot a, `slot-successful:a` yes, `slot-unbootable:a` no,
   no snapshot update, charger screen disabled (else nothing is written);
3. `flash boot_b <image>`, `flash dtbo_b recovery_dtbo.img`, `set_active b`, `fastboot reboot`;
4. watch USB for up to 15 min and record the timeline;
5. always restore: golden `boot_b` + `dtbo_b`, `set_active a`, reboot, readback, `a9-health.sh`.

### 4.4 Reading the outcome without a log

| Seen | Meaning |
|---|---|
| gadget `1d6b:0104`, then fastboot ~2.5 min after the reboot | healthy; dead-man (120 s) sent it to fastboot. ACM log grabbed if the tty is writable |
| fastboot ~1 min after the reboot, no gadget | init ran; no UDC within 40 s (fail-fast) |
| fastboot at another time | reset after the PON probe (panic/watchdog) |
| Android on slot a after a few minutes | reset before the PON probe, 7 times; ABL fell back |
| nothing for 15 min | hang without the watchdog (not expected: the beacon makes a held-Power S2 reset land in fastboot, INFERRED) |

## 5. Persistent logs

- ramoops at `0xb0580000`, 1 MiB, record/console/pmsg 0x40000, no ECC: identical to stock (MEASURED, base DTB), and
  the zone order is the same in 4.19 and 7.2. Compression is now off (`CONFIG_PSTORE_COMPRESS` not set) so the stock
  kernel could read the records.
- **But it only helps across a warm reset.** Every stock boot log says `HARD_RESET by PS_HOLD`, `Warm reset: 0`,
  stock pstore is empty in practice and Hisense compiled warm reset out (`# CONFIG_SUPPORT_WARM_RESET is not set`)
  (MEASURED, stock-boot-logs.md, a6l-harvest 1.7). A hard reset drops DDR. `reboot=warm` (PSCI SYSTEM_RESET2) is the
  experiment for a later boot (`A9_CMDLINE_EXTRA="reboot=warm"`); untested XBL warm-boot path, not default.
- The reliable channel is the PON reason (section 4.2) plus the outcome timing (4.4), and the ACM log when USB works.

## 6. Offline reviews (2026-10-15, four parallel reviews, no phone access)

**Memory map (MEASURED, decompiled ABL-merged stock DTB vs compiled mainline DTB, interval coverage):** every fixed
stock reservation is covered by a mainline reservation (all no-map except ramoops, mapped identically in both).
Stock ADSP 0x1e00000 lies inside upstream's 0x2800000; stock IPA/GSI/GPU-zap at 0x55600000-0x55617000 lie inside
upstream ADSP; `removed_region` override verified in the compiled DTB; the stock dtbo adds no reservations. Mainline-
only reservations (ADSP tail, upstream IPA/zap at 0x56100000, rmtfs 0x89b01000) only take RAM away. No enabled node
uses a differing region. **No gap, no fix.** Later, enabling the GPU: the zap region differs from stock (0x56115000 vs
0x55615000), probably harmless.

**Clocks, regulators, power domains, SMMU, TZ calls (code MEASURED, effects INFERRED):** no built-in driver clearly
resets the SoC. Applied the cheap mitigations:
- Historical bring-up disabled SHM bridge together with the QTEE smcinvoke probe. FP5 stock REA proves that
  `qtee_shmbridge_init` does enable SHM bridge and QSEE registers its ION heaps; FP5 restores that memory path.
  The QTEE probe remains skipped (`qcom_scm.skip_qtee=1`). SCM also disables SDI, so a watchdog
  bite becomes a plain hard reset (fine with the beacon).
- `cpuidle-psci-domain` switches PSCI to OS-initiated mode even with `cpuidle.off=1`: `CONFIG_ARM_PSCI_CPUIDLE` off.
- `cpufreq.off=1`: no DCVS perf-state writes.
- crypto engine, its BAM and the PRNG disabled in the DTS (shared with QSEE, unused).
- icc-rpm enables then disables the NoC AXI clocks of UFS/USB at probe (clk_ignore_unused does not cover an
  explicit disable). Not changed; if a log ever shows snoc/cnoc failing, SCM -> SMMU -> USB would all defer.
- rpmpd sync_state would vote corner 0 for every domain; confirmed held back by the never-probing MDSS consumer.
  **Never add `fw_devlink.sync_state=timeout`.**
- tsens on sm6115 needs no software calibration; critical trips are a power-off, not a loop.

**USB device mode (code + DTB MEASURED):** glue, VBUS override, clocks/resets/GDSC/interconnect, IOMMU stream 0x120
(= stock), fw_devlink (the link to the disabled QMP PHY is not created), config and the configfs sequence are all fine.
Two INFERRED fixes applied:
- **QUSB2 rails at stock voltages.** The PHY driver only enables its supplies, so the regulator core would start L4 at
  its 488 mV minimum (stock PHY needs 925-970 mV, `qcom,vdd-voltage-level`), L12 at 1.624 V, L15 at 2.92 V; the RPM
  driver sends that voltage on enable. Now L4 928-968 mV, L12 1.8 V, L15 3.08-3.2 V. (Upstream boards use the wide
  ranges and enumerate, probably because the RPM aggregates other votes; not proven here.)
- `qcom,select-utmi-as-pipe-clk` on the glue (no SS PHY).
- init logs the deferred-probe list and USB dmesg lines to kmsg before failing fast.
PHY tuning (TUNE2/TUNE4) differs from stock; signal quality only.

**QEMU smoke test of the initramfs (MEASURED, `qemu-system-aarch64 -M virt`, the built kernel, an initramfs built the
same way as the image's):** fail-fast (`a9.udcwait=5`) → `reboot: Restarting system with command 'bootloader'` at
6 s; dead-man (20 s) fires; `/tmp/stay` stops it; with `dummy_hcd` the gadget binds, `usb0` 172.16.42.1, telnetd and
udhcpd run, ACM `ttyGS0` exists. Bugs found and fixed: the beacon line claimed success when the parameter only
stored the value (no PON device): init now checks the PMIC readback and logs `NOT ARMED`; `a9.udcwait=0` blocked the
shells for 10 min: the wait now runs in the background in that case.

## 7. Log capture (boot 2 on)

- **Phone:** init starts `tcpsvd -E 0 2323 /bin/a9-ctl` on the NCM link (172.16.42.1). `a9-ctl` takes one command
  per connection: `ping`, `stay <s>`, `release`, `capture` (tar.gz: dmesg, `/tmp/a9-progress`, beacon readback,
  `devices_deferred`, `/proc/interrupts`, drivers/devices binding lists, modules, cmdline, iomem, pstore listing, ps),
  `capture-dbg` (`regulator_summary`, `clk_summary`, fetched separately because they read every clock/regulator),
  `bootloader`, and `eink-seq <verb>` (the E3-E5 sequencer `/bin/a9-eink-seq`: pre, e3, e5a, verdict, e5b, e5c,
  safe, status, fetch; docs/mainline-eink-steps.md "E3-E5 sequencer"). The dead-man and `bootloader` run
  `eink-seq safe` first when the sequencer was used. The dead-man now treats `/tmp/stay` as a HOLD: it fires as soon
  as the file is gone. init also logs
  the kernel's `beacon: reboot reason was X, now 2` line.
- **Host** (`first-boot.sh`, `capture_mainline`): find the NCM interface by MAC `02:a9:00:00:00:02` (≤10 s), `sudo -n ip
  addr replace 172.16.42.2/24`, ping a9-ctl (≤20 s), `stay <--stay, default 300>`, `capture`, `capture-dbg`, extract
  into `$A9_RUN/capture/`, `analyze-capture.py` → `summary.txt`, then `release` (dead-man fires → fastboot → the
  unchanged restore). Fallback: wait ≤10 s for `/dev/ttyACM*`, same commands via `sudo -n python3
  a9-capture-host.py acm` (marker + base64 framing). Capture failures are never fatal.
- **Tested offline (MEASURED):** QEMU virt with the real initramfs (virtio-net renamed to `usb0`, port forward):
  ping at 4 s, `stay 40`, both bundles (17 files), dead-man held until `stay (40 s) ended` at 43.4 s then fired at
  44.0 s (`Restarting system with command 'bootloader'`); `analyze-capture.py` on that bundle. ACM path: host pty with a
  CRLF-echoing shell and a fake a9-ctl, capture and stay decoded correctly.
- `scripts/mainline/analyze-capture.py <dir> [--dtb <dtb>]`: kernel/cmdline, beacon (flags a non-zero previous reason),
  progress, probe failures and dummy regulators, deferred list, unbound devices vs enabled DT nodes, USB and E Ink chain
  status, QUSB2/DSI rails, enabled clocks at rate 0, top interrupts, WARN/Oops. Tested on synthetic captures only.

## 8. Opt-in Android kernel (`A9_ANDROID=1`)

`A9_ANDROID=1 scripts/mainline/kernel/build.sh all` adds `scripts/mainline/kernel/a9-android.config` (binder/binderfs,
SELinux, dm-verity/snapshot, ext4/erofs/f2fs, fscrypt + inline crypt, FUSE, overlayfs, PSI, cgroups v1/v2, BPF for
NetBpfLoad/netd, iptables-legacy, FunctionFS, dma-buf heaps, a size diet) and builds into `build-7.2.10-android` /
`out-android`. MEASURED: builds; Image `image_size` 30,015,488 (< 36,024,320); 262 of 279 kernel configs of the GSI's
`compatibility_matrix.202504.xml` (6.12) met. Missing, not in mainline 7.2: ASHMEM, DM_DEFAULT_KEY (so the stock
metadata-encrypted `/data` cannot be mounted: bring-up needs a RAM/zram `/data`), QUOTA2, CPU_FREQ_TIMES,
UID_SYS_STATS; Clang-only items (GCC build); sound left off. NetBpfLoad's hard gates pass (>= 5.10, 64-bit; non-LTS
only warns). Not boot-tested. The Android vendor agent's own fragment (`scripts/mainline/android/kernel-android.config`)
overlaps; merge the two into one.

## 9. Next: E Ink (default OFF)

Staged plan, pass criteria for the boot-2 capture, gaps G1-G16 and the enable-flag procedure:
[mainline-eink-steps.md](mainline-eink-steps.md). Do **not** boot with `a9.eink=1` on the current image: it loads
tc358767 without dispcc, which toggles the bridge VDDC/reset on every deferred-probe retry (G2), and loading msm lets
rpmpd sync_state drop the CX/MX votes (G3).

## Appendix: reproduction (offline)

```sh
S=/tmp/claude-1000/-home-user-A9/138276b1-32cf-47e9-a654-4be339175015/scratchpad
scripts/mainline/kernel/build.sh all          # kernel + image + check (both abl-check routes, cmdline)
scripts/mainline/first-boot.sh --offline      # host-only preflight
# ABL: LinuxLoader.efi from golden abl_a (abl-check.py regenerates it), disassembled with capstone
#   (scratchpad ablchk/dis.py -> abl.dis). Key sites: 0x1608 reason jump table (bytes at 0x44f88/0x44f8f),
#   0x193c GetRebootReason, 0x1ce50 RecoveryInit, 0x366b8 boot_flow_check_event_stage (key table 0x55015),
#   0x235d8 FindBootableSlot, 0x1350 decrement predicate, 0x232f8 HandleActiveSlotUnbootable,
#   0x346fc/0x34754/0x348f4 fastboot reboot-recovery/-fastboot/-bootloader, 0xfe68 RebootDevice.
od -A x -t x1 -N 16 $A9_DATA/Backups/edl_2026-10-14_110759/lun4/devinfo.bin   # byte 15 = charger screen
# stock kernel pinctrl soc data (reserved_gpios = NULL): boot_a kernel, VA 0xffffff800968d058, RELA entries
```
