# Reverse engineering the A9 kernel: feasibility assessment

**Date:** 2026-10-14. **Status:** research only, done. **Corrected 2026-10-14 (later the same day):** the A6L
cameras work (§2(c)), and the upstream state is refreshed in [mainline-plan.md](mainline-plan.md) §1, which also
carries the offline groundwork for route (c). The phone was not touched (no adb, fastboot or edl).
`$A9_DATA` was only read. Binaries were inspected offline in the session scratchpad (`kre/`). Each claim is
marked **measured** (with the file and the command) or **inferred**. Reproduction commands are in the appendix.

**Bottom line: don't rebuild the kernel.** It is technically feasible, and cheaper than it looks for the E Ink
part, because the Hisense E Ink kernel code is small. But a rebuilt 4.19 achieves none of our open goals.
The goals it seems to serve (LOS 24 / Android 17, BPF 5.10/5.15 gates, GKI) need a 5.10+ kernel. On SM6115 that
means mainline, and mainline means replacing the whole vendor side, the way the A6L did. That costs a `/data` wipe
and most of the phone's hardware, so it is off the table while the A9 is a daily phone. One cheap kernel capability
does exist and is worth knowing about: loading **our own modules on the stock kernel** (§4).

---

## 0. The kernel we have (measured)

| Fact | Value | Source |
|---|---|---|
| Kernel | `4.19.157-perf`, `#1 SMP PREEMPT Wed Dec 28 20:14:05 CST 2022`, `root@complie.r740xd…`, clang 10.0.7 (Android NDK), GNU ld 2.27 | `Linux version` string in the decompressed Image |
| Container | boot header v2, page 4096. Kernel is gzip `Image.gz`, 15,047,394 B → 34,476,544 B raw, **no appended DTB**. DTB section 327,956 B (2 base DTBs: "Bengal SoC", "Bengal QRD", `msm-id <0x1a1 0x10000 0x1bc 0x10000>`) | golden `edl_2026-10-14_110759/lun4/boot_a.bin`, `unpack.py` |
| Same kernel on boot_a and the Magisk boot_b image | sha256 `b1ac57e3…dc5` (gzip) for both | `magisk_patched-30700_boot_b.img`, `boot_a.bin` |
| Stock package kernel | Jul 13 2022 build, `root@HISENSE`, 15,050,789 B. A different build, but **`/proc/config.gz` content is byte-identical** to the phone's | `HLTE556N/boot.img` |
| **Embedded config** | `CONFIG_IKCONFIG=y`. Extracted offline: 5,617 lines, 150,264 B | `IKCFG_ST` … `IKCFG_ED` in the raw Image, gunzip |
| Symbols | `CONFIG_KALLSYMS_ALL=y`: vmlinux-to-elf recovered **112,033 symbols** (base `0xffffff8008080000`, 78,370 relocations applied) | vmlinux-to-elf (nix store) |
| Hardening that matters for patching | `CONFIG_LTO_NONE=y`, no CFI, no shadow call stack, `RANDOMIZE_BASE=y`, `STRICT_KERNEL_RWX=y`, `KEXEC` off, `KPROBES` off, `FUNCTION_TRACER` off, `DEVMEM` off | config |
| **Modules** | `MODULES=y`, `MODVERSIONS=y`, **`MODULE_SIG_FORCE=y`**, `MODULE_FORCE_LOAD` off. Only 4 `=m` in-tree (`MSM_RDBG`, `DVB_MPQ*`, `LCD_CLASS_DEVICE`) | config |
| Vendor modules | **30** in `vendor_a:/lib/modules`: 28 audio techpack (`audio_*.ko`, including Hisense-added `audio_aw87xxx.ko` and `audio_es9218.ko`) + `qca_cld3_wlan.ko`. All have vermagic `4.19.157-perf SMP preempt mod_unload modversions aarch64`, and all carry an appended signature | `lpunpack -p vendor_a` of golden `lun0/super.bin`, `debugfs` |
| BPF / Android bits | `BPF_SYSCALL`, `BPF_JIT`, `BPF_JIT_ALWAYS_ON`, `CGROUP_BPF`, `NET_CLS_BPF`, `XT_MATCH_BPF`, `XT_MATCH_QUOTA2`, `UID_SYS_STATS`, `PSI`, `ANDROID_BINDERFS`, `INCREMENTAL_FS`, `FS_VERITY`, `DM_DEFAULT_KEY`, `BLK_INLINE_ENCRYPTION`, `FS_ENCRYPTION_INLINE_CRYPT` all on. **Off:** `USERFAULTFD`, `DEBUG_INFO_BTF`, `UCLAMP_TASK`, `F2FS_FS_COMPRESSION`, `CRYPTO_ZSTD` | config |
| Warm reset | `# CONFIG_SUPPORT_WARM_RESET is not set`. It sits in the **Hisense** Kconfig menu, next to `# CONFIG_SUBSYS_ERR_WARM_RESET is not set`. `PSTORE_RAM=y` | config |

Everything Hisense put in is **built in**. The only Hisense-added loadable modules are two audio amp/DAC
drivers (§1.2).

---

## 1. What is Hisense-specific

Comparison baseline: Qualcomm's public msm-4.19 (bengal techpacks), for example Xiaomi `lime-q-oss`
(SM6115, `4.19.95`, with `arch/arm64/configs/vendor/bengal-perf_defconfig` and `techpack/display/msm/dsi/dsi_panel.c`,
**measured**: HTTP 200 on `raw.githubusercontent.com/MiCode/Xiaomi_Kernel_OpenSource/lime-q-oss/…`).

### 1.1 Built-in Hisense or Hisense-board drivers (measured: symbols, strings, initcalls, DTBO `compatible`s)

"Code" is the sum of function sizes, taken from gaps between recovered symbol addresses. It is a proxy for
reverse-engineering effort, not exact.

| Function | Kernel evidence | DT `compatible` | Code | Upstream equivalent? |
|---|---|---|---|---|
| **E Ink panel glue in the DSI driver** (`techpack/display`, built in) | `dsi_epd_panel_pwroff`, `epd_panel_xon_off`, `epd_panel_sysfs_init`, `mdss_epd_fp9929a_power_up`. 12 sysfs attrs `epd_{info,contrast,black/white_threshold,display_type,display_mode,force_clear,connect,commit_bitmap,xon_en,vcom,thermal}`. uevents `EPD_CONNECT/EPD_COMMIT/EPD_CLEAR/EPD_TEMP`. GPIOs `epd_{xon,power_on,xoe,mode}_gpio`, `epd_backlight_flag`, `__setup("eink_vcom")` (ABL `androidboot.eink_vcom`) | `qcom,mdss_dsi_epd_eink_fhdhs_video` ("Eink epd 824x1648"), `epd_top_therm` ADC | ~3.9 KB, 32 funcs | No. **Plus unknown edits inside CAF `dsi_*` code** (e.g. `PWROFF-CHG BL SILENCE`) |
| **FITI FP9929A EPD PMIC** | `fp9929a_{probe,read_temp,read_vcom,write_vcom,set_power_en,active_mode,sleep_mode,…}` | `fiti,fp9929a` @0x48 | ~4.3 KB, 18 funcs | No mainline driver |
| **Toshiba TC358767 DSI→DPI bridge** (Hisense i2c shim, not the DRM bridge driver) | `tc358767_probe/remove/cmd/i2c_client` | `dz,tc358767` @0x0F | ~0.2 KB + a register table | Mainline `drivers/gpu/drm/bridge/tc358767.c` **does DSI→DPI** (`mode_dsi_to_dpi`, measured on master) |
| **AW99703 ×2 + KTD3137 ×2 frontlight** | `aw99703_*` (incl. `…_for_pwroffchg_1/2`), `ktd3137_*`, i2c drivers `_1.._4` | `awinic,aw99703-bl-1/2`, `ktd,ktd3137-3/4` | ~5.3 KB + ~7.3 KB | No mainline driver for either (measured, `drivers/video/backlight` on master has `aw99706.c`, `ktd253`, `ktd2801`, `ktz8866`; `aw99706` is a sibling part and might be adaptable, inferred) |
| **Sunwave SW9261 fingerprint** (`sf_ctl`, `sf_hw`) | strings `sunwave-sf_ctl`, `sunwave fingerprint device control driver`, input dev `sf-keys` | `sunwave,fingerprint` | ~5.5 KB | No. GPIO/IRQ shim only; matching and enrolment are a TEE TA + vendor HAL |
| Awinic haptics `aw86xx` (`haptic_hv`) | 244 funcs, `CONFIG_AWINIC_HAPTIC_HV`, `AW86XX_SUPPORT_CALI` | `awinic,haptic_hv` @0x5A | ~70 KB | Awinic publishes this driver (inferred); not in our goals |
| Touch: Atmel maXTouch (live: `atmel308`), plus Chipone `cts_*` built in but unused | `mxt_*` (gesture, glove), `cts_*` | `atmel,mxt-ts` (+ `elan,ekth3500`, `focaltech,5x06` present) | ~50 KB / ~79 KB | `atmel_mxt_ts` is mainline (without the Hisense gesture/glove extras) |
| `rs_recorder` (panic/reboot log saver) | `drivers/soc/hisense/rs_recorder/rs_{allps_info,dump_dmesg,dump_irqs}.c`, `/kdebuginfo/rs_recorder/reboot_reason` | `rs-recorder` | ~16 KB | No |
| Hisense SoC misc (`drivers/soc/hisense/`, Kconfig menu "Hisense product Infomation") | `HISENSE_BOOT_FLOW/BOOT_INFO/PRODUCT_DEVINFO/DEBUG_CTRL/SILENCE_REBOOT/PM_DEBUG/QPNP_REG_DEBUG/SHRINK_MEMORY/USB_FEATURE/ADB_BACKDOOR/CHARGE_FG_FUNCTION/TYPEC_OTG_CONTROL_FUNCTION/BATTERY_THERM_NTC_10K/PANIC_HANDLE/FORCE_REBOOT/SERIAL_CONSOLE_CTRL/DEBUG_BOOT_TIME/BOOTLOADER_LOG/ROOT_DETECT/ACCESS_DIAG_PARTITION/RESTART_MODEM`, `HS_POWERON_REASON`, `SAVE_AWAKEN_EVENT`, `SUBSYS_ERR_REPORT`, `ABNORMAL_STATS`, `EXCEPTION_MONITOR`, `PROJECTNAME_REPORT`, `QCOM_CHIP_EFUSE` (symbols `hs_silence_reboot`, `hs_nonblock_bl`, `hs_disable_avb/wp`, `detect_root`, `FoundRootandMark`, `abnormal_reboot_stats`, `his_show_bootlog`, `poweron_reason`, `projectname`, …) | `debug,bootlog`, `subsys-err-report` | ~12 KB in the `hs_*/his_*` names alone, more spread inside CAF files | No. None of it is needed to run Android |
| Hisense edits inside CAF drivers | `CONFIG_HISENSE_CHARGE_FG_FUNCTION`, `BATTERY_THERM_NTC_10K`, `TYPEC_OTG_CONTROL_FUNCTION`, `HISENSE_SD_POWER_OFF_QUICK`; camera `hmct-cam-position`, `hmct_otp_valid`; battery profile `Hisense_HLTE556N_Highpower_4000mAH_PMI632` in the DTBO | n/a | **unknown** | Needs a function-by-function bindiff against a matching CAF tag |

Not kernel at all (measured from the config): **the STK3B6X ALS/proximity sensor is on the ADSP sensor hub**.
`CONFIG_HS_SNS_ADSP_ALSPS=y`, `ALSP_SENSOR_VENDOR="STK3B6X"`, `sensors_ssc`. The kernel only has a
`sensor_productinfo` reporter. The same goes for the accelerometer and magnetometer (`HS_SNS_ADSP_ACC/MAG`).

`kmonlog` and `/proc/debug_control/bootloader_log` come from `HISENSE_BOOTLOADER_LOG` (`his_show_bootlog`,
`bootlog_vddr`). The `kmonlog_*` files themselves were written by stock userspace (`kernel_mond`,
[stock-boot-logs.md](stock-boot-logs.md)).

**Side finding (measured strings, inferred meaning):** `CONFIG_HISENSE_ADB_BACKDOOR=y` has
`usb_android_backdoor_work`/`set_usb_backdoor_value` and emits uevents `USB_DOOR=BACKDOOR`, `…ADB_ONLY`, `…LOGD`
("usb_backdoor is active, load ADB_OK/DIAG_OK"). It is a factory-mode trigger that stock userspace acted on.
**Follow-up 2026-10-16:** [firmware verification](a9-hardware-key-combos.md)
decoded the volume sequences and traced stock vold/init to ordinary ADB or
DIAG + ADB. A separate signed-challenge factory-root path exists in stock adbd;
the traced button path does not enable it. Current GSI handling remains untested.

### 1.2 Loadable vendor modules (measured)

`audio_aw87xxx.ko` (Awinic AW87xxx speaker PA: aw87339/359/369/**519**/559, `awinic,aw87xxx_pa_58`),
`audio_es9218.ko` (ESS ES9218 Hi-Fi DAC, `ess,hifi-es9218`), the CAF audio techpack and `qca_cld3_wlan.ko`.
These are the **only** Hisense-specific things that are not in the Image. Everything else in §1.1 is built in.

### 1.3 What that means for effort (inferred from the sizes)

The E Ink-relevant kernel code (DSI EPD glue + FP9929A + TC358767 shim + both frontlight drivers) is about
**21 KB of arm64 code, roughly 1,500–2,500 lines of C**. Decompiling and rewriting it is days of work, not months.
**The kernel holds no E Ink intelligence:** waveforms, the software TCON and mode logic are in the vendor HWC
([epd-waveform-source.md](epd-waveform-source.md)). The kernel powers the rails and sets VCOM, reads the
thermistors, toggles GPIOs, and relays sysfs writes as uevents (A6L `kernel-investigation-20260914.md` decoded the
same pattern on 4.4). The open-ended part is the **unmarked Hisense edits inside CAF drivers**: charger/FG,
display power sequencing, camera.

---

## 2. Routes

### (a) Rebuild 4.19 from Qualcomm's public bengal tree, porting the Hisense drivers

- **Base:** a CLO/CodeLinaro msm-4.19 bengal tag near 4.19.157 (LA.UM.9.15.r1 era, inferred), or Xiaomi
  `lime` (4.19.95) / Motorola `capri` as a known-booting SM6115 reference.
- **Config:** we have the exact one (§0).
- **Vendor modules as-is:** feasible in principle (measured). The Image's own `__ksymtab`/`__kcrctab` yield all
  **11,515 export CRCs**, and all **563** symbol versions imported by `audio_aw87xxx`, `audio_es9218`,
  `audio_machine_bengal` and `qca_cld3_wlan` match them, including `module_layout`. A rebuilt kernel only accepts
  them if its CRCs come out identical (same struct layouts and config). Our kernel would use our own signing key,
  so the `.ko`s would have to be stripped and re-signed. Realistically we would rebuild the audio techpack and WLAN
  from CAF source anyway. AW87xxx and ES9218 have public vendor drivers (inferred).
- **Port:** decompile the §1.1 drivers (about 21 KB for E Ink and frontlight, plus fingerprint, haptics and touch
  extras), then bindiff every CAF function in `dsi_*`, `smb5`/`qg`, typec and camera against the base tag to find
  the in-line Hisense edits.
- **Effort (inferred):** 2–4 weeks to a kernel that boots Android with display, touch and frontlight. Charging,
  fuel gauge and display power sequencing then need weeks more of on-device validation.
- **Risks:**
  - Every test is a boot of `boot_b`, and `/data` is shared with slot A. A crypto/ICE/f2fs regression in a
    rebuilt kernel touches the daily-driver `/data`. `/data` uses hardware-wrapped keys through ICE.
  - A wrong FP9929A power sequence or VCOM write can harm the E Ink panel. Wrong charger/FG parameters can harm the
    battery. Both are inferred, and both are the class of failure rule 11 exists for.
- **Result:** still 4.19, so the LOS 24 gates are unchanged (§3).

### (b) Binary-patch the existing Image (measured: practical)

- No LTO, no CFI, no SCS. Symbols are complete, and the relocation table is intact. AVB is disabled, so a patched
  Image in `boot_b` boots without signing (the ABL checks only the header/DTB layout, [abl-check.md](abl-check.md)).
- Worked example: `is_module_sig_enforced()` is `adrp; ldrb w0,[x8,#0x158]; ret` reading `sig_enforce`. That is
  **one byte, `0x01`, at raw-Image offset `0x1f09158`**. Setting it to 0, then re-gzipping and repacking with the
  same header and DTB, turns off module signature enforcement. Combined with a synthesized `Module.symvers` (from
  the `__kcrctab`, above), the extracted config and CAF 4.19 headers, this lets us build **our own out-of-tree
  modules for the stock kernel**. Effort: 1–2 days, plus one slot-B try-boot through
  `flash-test-b.sh --boot`. Risk: low. It is the same kernel, so a bad patch fails to boot and B falls back.
  This was not done.
- Larger behaviour changes by patching code (e.g. turning on warm reset) means reverse engineering unknown
  Hisense logic. It is not worth it without a concrete target.

### (c) Mainline (the A6L route)

- **Measured upstream support (torvalds master, 7.3-rc7):** `sm6115.dtsi` has DPU/MDSS + DSI (14 nm PHY),
  Adreno 610 + GMU wrapper, Venus, UFS, SDHCI, USB3/QUSB2, ADSP/CDSP/MPSS remoteprocs, WCN3990 Wi-Fi, q6 APR audio,
  LPASS LPI pinctrl, cpufreq-hw, tsens and bwmon. Boards: `qrb4210-rb2`, `sm4250-oneplus-billie2`,
  `sm6115-fxtec-pro1x`, `sm6115p-lenovo-j606f`.
- **Missing for the A9:**
  - **PMI632 charger and fuel gauge.** `pmi632.dtsi` has only gpio, lpg, pbs, typec, vbus-reg, vib, adc5, adc-tm5,
    sdam and temp-alarm. `qcom_smbx` matches only pmi8998/pm660. There is no QG driver.
  - Camera: no CAMSS node in `sm6115.dtsi`. *Refreshed 2026-10-14:* the same camera IP is upstream as QCM2290
    (`qcom,qcm2290-camss`, Agatti), but SM6115 needs its own 3-PHY/3-TFE table and the A9 sensors (S5K3L6,
    Hi-556) need drivers or DT support ([mainline-plan.md](mainline-plan.md) §1.2).
  - FP9929A, aw99703/ktd3137, Sunwave, aw86xx and aw87xxx drivers. Atmel touch board glue. A RIL for the modem.
  - The **E Ink display stack**. Mainline `tc358767` already does DSI→DPI, so getting pixels out may be the easy
    part. The software TCON is the hard part: it lives in the vendor HWC, which needs downstream SDE DRM, KGSL and
    the stock SurfaceFlinger interfaces, so it **will not run on mainline**. We would need an A6L-style stack. One
    possible shortcut (inferred, untested): `librecovery_ui.so` carries a self-contained TCON that TWRP already
    drives.
  - The whole vendor partition: Mesa instead of KGSL blobs, own HALs, software or own KeyMint. That means a
    **`/data` wipe**.
- **Effort (measured from the A6L repo):** about 4 weeks of near-daily attended sessions, kernel candidates V1→V75,
  with a spare phone. The project is now paused.
  - **Correction (2026-10-14):** this line used to say camera and storage problems were still open. That was wrong.
    The A6L README §1 (HEAD `ede802b`) lists all 3 cameras as working (photo, video, autofocus, flash/torch,
    through mainline `qcom-camss` + libcamera 0.7.2 and libcamera's Android HAL).
  - Its open items are stability (hard resets under load), no `/data` encryption (plain ext4), software KeyMint
    and VoLTE.
  - None of its camera *kernel* work transfers: SDM660 is VFE 4.8 + ISPIF, while the A9 is TFE/CSID 530 =
    QCM2290 generation. The userspace pattern does transfer ([mainline-plan.md](mainline-plan.md) §1.1).
  - On the A9 there is no spare phone, and this is the daily driver.
- **Verdict:** it is the only route to a 5.10+/5.15 kernel. It is not compatible with "daily phone, never wipe
  `/data`".

#### (c.1) The E Ink half of a mainline port: the A6L design mapped to the A9 (plan, recorded 2026-10-14)

How the A6L did it (their docs `eink-swtcon-abi-20260919.md`, `eink-transport-tc358762-20260920.md`,
`eink-mirror-milestone-20260923.md`, `eink3-20260924.md`, `eink-runtime-fixes-20261001.md`): they kept only
Hisense's **software TCON** (`libtcon_eink.so`: imports only libc, `property_get` and logging; RGBA image +
waveform in, drive frames out; modes 0 auto, 1 DU, 2 GC16, 3-5 REGAL, >5 A2), `dlopen`ed it from their own
daemon and tested it in QEMU (byte-identical over 13 updates). Around it: a plain mainline DRM path (DSI ->
`toshiba,tc358762` -> `panel-dpi` at 384x725, three register values replayed from the stock bridge table),
mainline TPS65185 patches, a daemon that page-flips each drive frame through a DRM lease, a patched
drm_hwcomposer that hides the E Ink connector from SurfaceFlinger, and a controller that mirrors the LCD and
picks waveforms per 8x8 tile. Worked on a tester's phone; mode switches about 6-7 s; project paused.

| A6L piece | A9 equivalent | Difficulty |
|---|---|---|
| Standalone `libtcon_eink.so`, reused as-is | **None standalone.** The full TCON (14 temperature bins, four waveform families) is compiled into the vendor composer; the only standalone copy is `librecovery_ui.so` (two fixed tables, no temperature compensation) | **Harder:** accept recovery's limits, or lift the composer's tables (layout mapped in [epd-waveform-source.md](epd-waveform-source.md)) into our own TCON |
| TC358762 bridge | TC358767; mainline does DSI->DPI; init table in our kernel (`tc358767_cmd`) | Similar |
| TPS65185 power chip | FP9929A, no mainline driver, about 4 KB of code | Small |
| Frontlight | 2x AW99703 + 2x KTD3137, no mainline drivers (`aw99706.c` is a sibling) | Small to medium |
| Mirror daemon + composer hiding the panel | The E Ink is the **only** display: SurfaceFlinger's output itself must go through the TCON (a custom composer backend). HWC composition is already off here, so SF already hands over one composed buffer per frame | **Harder:** a primary-display E Ink compositor |
| Waveform policy, E Ink key, modes | a9service logic and the 513/515/518/521 map carry over | Similar |

The rest of route (c) is unchanged (Mesa, no PMI632 charger/gauge, camera, fingerprint, amp, RIL, own HALs,
`/data` wipe).

**Update 2026-10-14:** steps 1 and 2 of the plan below are done, offline, in [mainline-plan.md](mainline-plan.md):
- the bridge table and the FP9929A/frontlight sequences are extracted (§2);
- a standalone TCON reproduces the stock composer's drive frames byte for byte under Unicorn (§4);
- the ABL accepts a mainline DT with its own board overlay (§3.2).

**Plan (not started).** Preconditions for any on-phone step: a second A9 as a test unit, or the user's OK to
give up the stock vendor side and wipe `/data`.

1. *Offline:* extract the bridge init table and the FP9929A power-up/VCOM sequence from our kernel (symbols
   already recovered; a few hours).
2. *Offline, the crucial unknown:* a standalone TCON prototype from the composer's waveform tables, tested in
   QEMU against the stock composer's output (the A6L method). If it matches, the E Ink half is de-risked.
3. *Later, only with a spare phone or an explicit go:* a diskless mainline boot on slot B (ramdisk never mounts
   `/data`) to check display bring-up. Panel and charging risk remain. The ABL must accept a mainline DT
   (Qualcomm board-ID properties): check with `abl-check.sh` first.

### (d) GPL source release

Hisense has to provide it on request (GPLv2 §3). Asking costs nothing, and enforcement (SFC/gpl-violations) is
slow. The user excluded it earlier ([a6l-harvest](a6l-harvest-2026-10-13.md) §0). It is mentioned here only
because it would make route (a) a weekend instead of weeks.

---

## 3. What a kernel we can build would get us

| Goal | Rebuilt 4.19 (a) / patched stock (b) | Real or wishful |
|---|---|---|
| **LOS 24 / Android 17: NetBpfLoad gates** | No help. The gate wants **5.4 at 25Q2, 5.10 at 25Q4, 5.15 at 26Q4** (measured, `NetBpfLoad-lineage-24.0.cpp` l.1570-1588). Our 4.19 already fails the 25Q2 gate, and the GSI already patches it (`bpf.progs_loaded=1`). The 25Q2 JIT requirement is met (`BPF_JIT_ALWAYS_ON=y`). Programs with `min_kver` > 4.19 are skipped on any 4.19 | **Wishful.** Only mainline moves the version |
| LOS 24 blockers we actually have | VNDK 30 and FCM 5 are userspace and vendor-manifest problems ([a6l-harvest](a6l-harvest-2026-10-13.md) §2). A kernel changes neither | **Wishful** |
| GKI / KMI | GKI starts at 5.4/5.10. A 4.19 is never GKI | **Wishful** |
| Security patches | 4.19.157 (Nov 2020 sublevel) vs final 4.19.325 (Dec 2024, EOL). A rebuild could take about 170 sublevels of fixes, but the base stays EOL | **Real, but bounded**, at high risk for a daily phone |
| USERFAULTFD (ART CMC GC) | Could be switched on in 4.19, but ART's uffd GC wants newer kernel features (inferred). ART falls back to CC GC today and works | **Mostly wishful** |
| BTF | `DEBUG_INFO_BTF` could be built, but NetBpfLoad sanitises BTF below 5.10 anyway | **Wishful** |
| Warm reset / pstore | (a) could set `SUPPORT_WARM_RESET=y`. The Hisense code behind it is unknown, and XBL/PMIC behaviour on a warm reset is untested here. A module from (b) could in theory set the PON reset type | **Speculative**, debug-only value. `kdebug` and the bootlog module already cover most of it |
| E Ink control from the kernel | The kernel exposes only rails, VCOM, thermistors and GPIOs. Every user-visible E Ink feature (modes, clears, AOD, refresh) lives in the HWC and a9service, and we already patch those | **Wishful.** Rebuilding the kernel adds no E Ink feature |
| Battery / standby | Unknown. `lpm_levels` is already live-`N`. No drain data exists yet ([a6l-harvest](a6l-harvest-2026-10-13.md) §5). Measure before blaming the kernel | **Unproven** |
| Removing root needs | What needs root today: Magisk modules, Vector/LSPosed for Xposed Edge, root reads in our scripts. Framework hooks need Zygisk or patched jars, not a kernel. Sysfs permissions come from vendor init/sepolicy | **Wishful.** A custom kernel could carry KernelSU-style root, which is a different root, not none |
| Own kernel modules (e.g. a probe, a debug helper, a fixed driver) | **Real via (b)**: one byte plus a synthesized `Module.symvers` | Real, but there is no concrete use case today |

What we already have without touching the kernel: an Android 16 GSI on the stock kernel, with E Ink, both AODs,
adaptive brightness and the HWC guard working; BPF loaded through the patched gate; `/data` intact.

---

## 4. Recommendation

**Don't.** Neither (a) nor (c) serves a current goal, and (c) is incompatible with the phone being a daily
driver. The real kernel blocker for Android 17 is the version (5.10/5.15), and only mainline moves it, at the
price of the vendor stack and `/data`. Watch the LOS 24 GSI route instead (VNDK 30 / FCM 5). That is where the
evidence says the blockers are.

**Smallest worthwhile step, only when a concrete need appears:** route (b), "own modules on the stock kernel":

1. Patch `sig_enforce` (raw offset `0x1f09158`, `01`→`00`), re-gzip, and repack `boot_b` with the same header, DTB
   and `recovery_dtbo` layout. Check it offline with `scripts/abl-check.sh`.
2. Synthesize `Module.symvers` from the `__kcrctab` (appendix).
3. Build modules against a CAF 4.19 bengal tree with the extracted config.
4. Try-boot with `flash-test-b.sh --boot`.

The Magisk patch then has to be re-applied on top, because `boot_b` holds Magisk.

**Main risks of any kernel work here:**

- No `fastboot boot`: every test is a real flash, and only to slot B.
- `/data` is shared by both slots, uses wrapped keys and must not be wiped.
- `boot_b` carries Magisk.
- Display and frontlight power-sequencing faults leave a blank panel (rule 11).
- Wrong EPD PMIC or charger parameters may damage hardware (inferred).
- Never use the stock package's `boot.img` kernel: it is a different build (CLAUDE.md fact 2).

---

## Appendix: reproduction (offline, session scratchpad `kre/`)

```sh
# Kernel from the golden dump: header v2, gzip Image.gz, DTB section after the ramdisk
python3 -I unpack.py $A9_DATA/Backups/edl_2026-10-14_110759/lun4/boot_a.bin img/Image_boota
python3 -I -c "import zlib;d=open('img/Image_boota','rb').read();open('img/Image_boota.raw','wb').write(zlib.decompress(d,16+15))"
# IKCONFIG: bytes between b'IKCFG_ST' and b'IKCFG_ED' in the raw Image, gunzip -> 5,617 lines
# Symbols
/nix/store/jf7fb17d6lmxz90s6fc90nymj7lg57an-vmlinux-to-elf-0-unstable-2024-07-20/bin/vmlinux-to-elf img/Image_boota.raw img/vmlinux_boota.elf
# Export CRCs: __start___ksymtab{,_gpl} entries are 8 B PREL32 (value, name); parallel 4 B __kcrctab{,_gpl}
#   -> 11,515 exports; compared with each vendor .ko's __versions (64 B records): 563/563 match
# Vendor modules
lpunpack -p vendor_a $A9_DATA/Backups/edl_2026-10-14_110759/lun0/super.bin super/
debugfs -R "ls -l /lib/modules" super/vendor_a.img
# DT: split boot DTB section (2 FDTs); DTBO table in lun4/dtbo_a.bin (1 entry, 88,658 B); dtc -I dtb -O dts
nix-shell -p dtc --run 'dtc -I dtb -O dts -q -o dtbo_0.dts dtbo_0.dtb'
# sig_enforce: kallsyms 0xffffff8009f89158 - base 0xffffff8008080000 = raw offset 0x1f09158, value 0x01;
#   is_module_sig_enforced @0xffffff8008194040 disassembled with capstone
# Upstream (2026-10-14): GitHub contents API for arch/arm64/boot/dts/qcom; raw sm6115.dtsi, pmi632.dtsi,
#   drivers/gpu/drm/bridge/tc358767.c, drivers/power/supply/qcom_smbx.c on torvalds/linux master
```
