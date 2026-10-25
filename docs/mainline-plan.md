# Mainline Linux for the A9: plan and offline groundwork

**Update 2026-10-15:** a mainline image was booted once without approval and reset-looped (BCB `boot-recovery`,
no USB, recovered over EDL). Cause analysis, the PON-reason beacon, the hardened image and the fail-safe first-boot
route (boot_b + dtbo_b, bounded by the ABL's A/B retry count) are in [mainline-kernel-build.md](mainline-kernel-build.md).
The "first on-phone step" in §5 and the `recovery_b` row in §6 are superseded by that doc's section 4.

**Date:** 2026-10-14. **Status:** offline research and prototypes, done; nothing booted. The phone was not
touched (no adb, fastboot or edl), and `$A9_DATA` was only read. This doc follows
[kernel-re-assessment.md](kernel-re-assessment.md) §2(c)/(c.1), which it corrects and extends.

The goal is the user's choice: a **mainline kernel with a new vendor side** (the A6L route). That means a different
ROM and, later, a `/data` wipe (a TWRP data backup exists: `Backups/twrp/golden-20261014`).

Labels:
- **MEASURED**: read from a file, a disassembly or an emulation, with the command in the appendix.
- **INFERRED**: deduced.

Session scratch: `scratchpad/mainline/` (not durable). Reproduction scripts: `scripts/mainline/`.

## Bottom line

1. **The E Ink half is no longer the unknown it was.** A standalone software TCON (`scripts/mainline/a9tcon.py`,
   about 300 lines) reproduces the stock composer's drive frames **byte for byte**. The comparison ran the stock
   ARM64 code in Unicorn and covered 34/34 checks per input image, on two images. Those checks include all four
   UI waveform families, 7 of the 14 temperature bins (0, 1, 3, 6, 9, 10, 13), the clear and sentinel paths, the 16-level dither and the
   one-time init flash (§4).
   **C port (2026-10-14, §4.5):** `scripts/mainline/tcon/` (freestanding core, needs only `memcpy`/`memset`)
   matches the stock code in 174/174 checks per image, 5,648 frames byte for byte. It adds the mode-switch step
   sequences, the RGBA -> grey + rotation stage and all 14 temperature bins.
2. **The ABL accepts a mainline device tree** under two conditions (MEASURED with the phone's own ABL code, §3):
   - `qcom,msm-id` must include `<0x1bc 0x10000>`;
   - the image needs its own board overlay. The stock DTBO cannot be applied to a mainline base.
   A recovery-partition image with an embedded `recovery_dtbo` passes all ABL stages without touching the `dtbo`
   partition.
3. **The kernel-side E Ink sequences are extracted** (§2): the bridge init table, FP9929A power-up/down, VCOM and
   thermistor, the panel GPIO sequence, and the frontlight init and brightness for all four chips. They are small
   enough for one mainline panel/regulator driver plus two backlight drivers.
4. **The biggest non-E Ink gap is charging.** Mainline has no driver for the PMI632 charger or its QG fuel gauge,
   and nothing is in review (§1). The A6L could charge because its PM660 is supported by `qcom_smbx`; the A9 has no
   such shortcut. Camera has a real upstream path through the QCM2290 (Agatti) CAMSS support.

---

## 1. Assessment refresh (corrects kernel-re-assessment §2(c))

### 1.1 Correction: the A6L cameras work

The earlier study said A6L camera problems "were still open". **That was wrong.**
- The A6L README §1 (repo HEAD `ede802b`, 2026-10-13) lists under **Works**: "all 3 cameras (photo, video,
  autofocus, flash/torch). Image quality is a little below stock".
- Its real open items are stability (hard resets under load), no `/data` encryption (plain ext4, no FBE),
  software KeyMint, and VoLTE.

Its camera stack (all MEASURED in the repo):

| Layer | A6L (SDM660) | Paths |
|---|---|---|
| Kernel driver | mainline `qcom-camss`, CAMSS_660 (CSIPHY 3-phase 1.0, CSID 4.7, ISPIF, VFE 4.8), plus a 684-line out-of-tree fix patch; later a VFE 4.8 hardware-ISP path (`a6l_pix=1`) | `device/hisense/a6l/kernel/camera/patches/camss-sdm660-rom1.patch`, `rom/bin/a6l-modules.sh:45-50`, `docs/upstreaming.md:134` |
| Bus, sensors, focus motor | `i2c-qcom-cci`. IMX576 rear main and S5K3T1 front (both out-of-tree drivers, register tables replayed from stock), Hi-846 rear wide (mainline + 1 patch), GT9769 focus motor | `rom/modules/camera.txt`, `kernel/camera/*.c`, `kernel/a6l-camera-v75.dtso:106-228` |
| Userspace | libcamera v0.7.2, simple pipeline with soft-ISP (29 patches), later an A6L `DebayerHwIsp` on the VFE | `camera/libcamera/tools/build-libcamera-a6l.sh:81,140`, `prebuilt/BUILD-INFO.txt` |
| Android HAL | libcamera's own Android HAL (`camera.libcamera.so`), loaded by an A6L fork of the LineageOS AIDL camera provider; `camera_hal.yaml`; tuning YAMLs per sensor | `libcamera-vendor.mk:13-28`, `camera.mk:8-9`, `prebuilt/share/libcamera/ipa/simple/` |

**What transfers to SM6115.**
- **Not the kernel side** (INFERRED). The A9's camera block is a different generation:
  - stock DT: `qcom,tfe530`, `csid530`, `csiphy-v2.0.1`, `cci-v1.2` and an OPE (`base_0.dts:14775-15407`);
  - A6L: VFE 4.8 + ISPIF, so its camss fixes and its VFE 4.8 ISP do not apply.
- **SM6115 is the QCM2290 ("Agatti") camera IP** (register map MEASURED identical): CSIPHY0 0x5c52000, TFE0/CSID0
  0x5c6e000, TFE1 0x5c75000, CCI 0x5c1b000, OPE 0x5c42000. SM6115 adds CSIPHY2 at 0x5c54000 and TFE2 at 0x5c7c000.
- **What carries over (INFERRED):** the userspace pattern. That means libcamera's Android HAL and most of the A6L's
  HAL patches, the provider fork, `camera_hal.yaml`, and the soft-IPA autofocus work.

**A9 sensors** (MEASURED: module file names in the stock `super_*.img`):
- rear Samsung **S5K3L6** (13 MP, `com.qti.sensormodule.s5k3l6_bnd32b_shine.bin`), with focus motor, EEPROM and
  PMI632 flash, on CSIPHY0;
- front Hynix **Hi-556** (5 MP), on **CSIPHY2** (`dtbo_0.dts:640-840`).

### 1.2 Upstream state per item (torvalds master `0fcb05b72344` = 7.3-rc7, linux-next `next-20261014`, Mesa main `25219134d356`, all fetched 2026-10-14)

lore.kernel.org search returned 403 to scripted access. In-flight series were found through the
patchwork.kernel.org API, and their cover letters were read from lore `/raw`.

| Item | Upstream status | M/I |
|---|---|---|
| Base SoC: DPU/MDSS, DSI + 14 nm PHY, UFS, SDHCI, USB, remoteprocs (ADSP/CDSP/MPSS), WCN3990, tsens, cpufreq-hw, interconnect, SMMU, gcc/dispcc/gpucc/lpasscc | In `sm6115.dtsi` and drivers | M |
| **GPU** Adreno 610 | Kernel: `a6xx_catalog.c:682-712` (`0x06010000`, gmu-wrapper, `a610_zap`); `sm6115.dtsi` gpu@5900000, disabled until a board enables it. Mesa: `GPUId(610)` in `freedreno_devices.py:300` (`a6xx_gen1_low`); Turnip shares the table; no a610 CI job | M (support), I (Turnip quality) |
| **CAMSS QCM2290** | In master: `qcom,qcm2290-camss` (camss.c:5747, `csid/vfe_ops_340`), binding `qcom,qcm2290-camss.yaml`, cci+camss nodes in `agatti.dtsi` | M |
| **CAMSS SM6115** | **Absent** in master and next; no binding; no camss/cci node in `sm6115.dtsi`; no series on patchwork (267 SM6115-related titles searched). The A9 front camera on CSIPHY2 needs a 3-PHY / 3-TFE resource table. The camera clocks are already in `gcc-sm6115.c`; no camcc needed | M |
| CAMSS OPE (hardware ISP) | "CAMSS Offline Processing Engine" v10, 2026-09-25, Qualcomm; Agatti `isp@5c42000` (same address as the A9's OPE); not in next. Userspace: Hans de Goede's libcamera CAMSS pipeline | M |
| Sensors | S5K3L6: RFC driver 2026-06-26, state "new". Hi-556: `hi556.c` in master but ACPI-only (needs an OF match) | M |
| **PMI632 charger** | **None.** `qcom_smbx.c` matches pmi8998/pm660 only (lines 1040-1041). The SMB5 series v4 (2026-08-20) covers PM8150B/PM7250B, is not in next, and has no PMI632 | M |
| **PMI632 fuel gauge (QG)** | **None**, nothing on patchwork. Stock uses `qcom,qpnp-smb5` + `qcom,qpnp-qg` (`base_0.dts:4368,4421`) | M |
| `pmi632.dtsi` | vbus-reg, typec, temp-alarm, adc5, adc-tm5, pbs, sdam, gpio, lpg, vib. No charger/gauge | M |
| SMB1355 parallel charger (stock, i2c1 0x0c) | not checked upstream | – |
| TC358767 bridge | In master, DSI->DPI supported (`mode_dsi_to_dpi`) | M |
| FP9929A E Ink PMIC | No driver. `drivers/regulator/fp9931.c` (`fitipower,fp9931`) is the nearest relative | M (absence), I (base) |
| AW99703 frontlight | No driver; `aw99706.c` in master (register compatibility not checked) | M / I |
| KTD3137 frontlight | No driver; KTD3136 RFC v1 2026-09-11 | M |
| Atmel maXTouch | `atmel_mxt_ts.c` in master | M |
| Haptics (stock `haptic_hv`, aw869x) | `aw86927.c` in master; AW8697 v3 (2026-10-13) and AW8695 v2 in review; A9 chip variant unknown | M / I |
| Speaker PA AW87xxx | `aw87390.c` in master; A9 chip variant unknown | M / I |
| ES9218 Hi-Fi DAC | No driver, nothing in flight | M |
| Venus/Iris video | `qcom,sm6115-venus`; iris-sm6115 v2 mainlined 2026-05-15 | M |
| ADSP audio | `sm8250.c` has `qcom,qrb4210-rb2-sndcard`; `lpasscc-sm6115.c`; WCD/WSA board series stuck at changes-requested (2025-05). The A9 codec is `qcom,wcd937x` (dtbo fragment 1) | M |
| Modem | `qcom,sm6115-mpss-pas` + smp2p; the RIL / QMI side is userspace | M / I |
| Boards in master | `sm6115-fxtec-pro1x`, `sm6115p-lenovo-j606f`, `sm4250-oneplus-billie2`, `qrb4210-rb2` (+ `agatti.dtsi` with `qrb2210-rb1`, `qrb2210-arduino-imola`) | M |

**Changed since the earlier study:**
- Camera moved from "no CAMSS node" to "the same IP is upstream as QCM2290, and the hardware ISP is in review".
- Charging is unchanged and remains the biggest functional gap.

---

## 2. E Ink hardware sequences from our kernel

Source: the device's own `boot_a` kernel (golden dump), `4.19.157-perf`, symbolized with vmlinux-to-elf
(`kre/img/vmlinux_boota.elf`, base `0xffffff8008080000`). All addresses below are kernel VAs. DT facts come from
`lun4/dtbo_a.bin`; fixups were resolved with `scripts/mainline/dtbo-resolve.py`. All MEASURED unless marked.

### 2.0 Where the chips sit

| Device | Bus (stock label = mainline) | Addr | GPIOs (TLMM) | Stock compatible |
|---|---|---|---|---|
| TC358767 DSI->DPI | `qupv3_se0_i2c` `i2c@4a80000` = `&i2c0` | 0x0f | reset 26, VDDC-en 31 | `dz,tc358767` |
| FP9929A E Ink PMIC | i2c0 | 0x48 | pwrup 86, pwrcom 36, state 82 (input) | `fiti,fp9929a` |
| AW99703 #2 ("center") | i2c0 | 0x36 | hwen 85 | `awinic,aw99703-bl-2` |
| KTD3137 #4 ("edge") | i2c0 | 0x35 | hwen 101 | `ktd,ktd3137-4` |
| AW99703 #1 | `qupv3_se1_i2c` `i2c@4a84000` = `&i2c1` | 0x36 | hwen 83 | `awinic,aw99703-bl-1` |
| KTD3137 #3 | i2c1 | 0x35 | hwen 88 | `ktd,ktd3137-3` |
| Panel lines | – | – | xon 25, epd-power-on 33, xoe 34, mode 81, i2c-mux-sel 2 | in `qcom,mdss_dsi_epd_eink_fhdhs_video` |
| E Ink thermistor | PMI632 VADC | ch 0x54 = `ADC5_GPIO3_100K_PU` (PMI632 GPIO4), ratiometric, settle 200 us | – | `epd_top_therm` |

Also on i2c0: `haptic_hv` at 0x5a. Also on i2c1: the `aw87xxx` amplifier at 0x58, `es9218` at 0x48 and `smb1355` at 0x0c.
`CONFIG_HZ=100`.

### 2.1 TC358767 bridge init (`dsi2dpi_init` 0xffffff8008f1dbe8, table `tc358767_cmd` 0xffffff800a0eab18)

**How it is sent:**
- 25 entries of 6 bytes each: a big-endian u16 register, then a little-endian u32 value. Each entry is one
  `i2c_transfer` write to 0x0f.
- Errors are ORed into the return value and never retried. The log line is `tc358767_send_init_cmd, epd ret=%d`.
- The probe reads no chip ID. UEFI logs `ChipID=0x6601`, which mainline accepts.

Decoded with `scripts/mainline/decode-tc358767-table.py` (mainline register names):

| # | Reg | Name | Value | Meaning | Mainline `tc_dpi_stream_enable` |
|---|---|---|---|---|---|
| 0 | 0x448 | POCTRL | 0x86 | S2P, VS_POL, HS_POL | S2P + polarity from mode flags |
| 1 | 0x6a0 | DP0_SRCCTRL | 0x3080 | SCRMBLDIS, EN810B, LANESKEW | not written in DPI mode |
| 2 | 0x914 | PXL_PLLPARAM | 0x118301 | pre 1, post 1, **IN_SEL=2**, DIV 3, MUL 1 | computed from refclk, IN_SEL_REFCLK (0) |
| 3 | 0x904 | DP1_PLLCTRL | 0x0, then *1 ms* | off | |
| 4 | 0x904 | DP1_PLLCTRL | 0x4 | PLLUPDATE | |
| 5 | 0x908 | PXL_PLLCTRL | 0x1, then *1 ms* | PLLEN | PLLBYP\|PLLEN first |
| 6 | 0x908 | PXL_PLLCTRL | 0x5 | PLLUPDATE\|PLLEN | same |
| 7 | 0x918 | SYS_PLLPARAM | 0x110 | REF_FREQ 19.2 MHz, SYSCLK_SEL 1 | from the refclk rate, SYSCLK_SEL 0 |
| 8, 9 | 0x800 | DP_PHY_CTRL | 0x1100 ×2 | DP PHYs held in reset | – |
| 10 | 0x13c | PPI_TX_RX_TA | 0x30005 | | 0x40006 |
| 11 | 0x114 | PPI_LPTXTIMECNT | 3 | | 3 |
| 12-15 | 0x164-0x170 | PPI_D0S..D3S_CLRSIPOCOUNT | 4 each | | 5 |
| 16, 17 | 0x134 / 0x210 | PPI_/DSI_LANEENABLE | 7 | clock + 2 data lanes | same |
| 18, 19 | 0x104 / 0x204 | PPI_STARTPPI / DSI_STARTDSI | 1 | | same, but last in mainline |
| 20 | 0x450 | VPCTRL0 | 0x03f00100 | VSDELAY 63, RGB888, **FRMSYNC off** | VSDELAY = hfp+10, FRMSYNC on |
| 21 | 0x454 | HTIM01 | 0x1d0003 | HPW 3, HBPR 29 | 4, 30 |
| 22 | 0x45c | VTIM01 | 0x40002 | VSPR 2, VBPR 4 | same |
| 23 | 0x464 | VFUEN0 | 1 | | same |
| 24 | 0x510 | SYSCTRL | 1 | VIDSRC_DSI_RX | same |

**The DPI timing it serves.** HTIM02/VTIM02 are never written.

| | Stock DT `timing@0` | Bridge registers |
|---|---|---|
| Active | 448×829 | not written |
| Horizontal | hfp 80, hbp 30, hpw 4 | HPW and HBPR = DT value − 1 |
| Vertical | vfp 4, vbp 4, vpw 2 | VSPR/VBPR = DT values |
| Rate | 85 Hz, 2 DSI lanes, 24 bpp, video mode, non-burst sync event | – |
| Totals | htotal 562, vtotal 839, pixel clock **40.08 MHz** (computed) | – |

**INFERRED:**
- With FRMSYNC off, the DPI side follows the DSI syncs.
- PXL_PLLPARAM IN_SEL=2 is not the RefClk input. On a DSI byte clock of about 60 MHz, ÷3 gives about 20 MHz, half
  the pixel clock. That may tie in with S2P (open).

**Call order (`dsi_panel_enable` 0xffffff8008f0fb80):** `fp9929a_active_mode`, then `dsi2dpi_init`, then the
(empty) DSI ON command set, then `ts_resume`.

**The `-107` on every power-on.** At least one write NACKs on every power-on. UEFI logs "Retry reg = 13C", which
suggests entry 10 is the one (INFERRED). A mainline driver should retry it.

**For mainline:**
- Mainline `toshiba,tc358767` needs a `ref` clock of 13, 19.2, 26 or 38.4 MHz. The stock DT names none; REF_FREQ
  19.2 MHz implies one exists (INFERRED).
- Two options: small driver changes (a non-refclk PLL input, FRMSYNC off, the PPI values), or replaying this table
  the way the A6L replayed its TC358762's.

### 2.2 FP9929A E Ink PMIC

**Registers** (8-bit register, 8-bit data; the driver writes nothing else):

| Reg | Use | Detail |
|---|---|---|
| 0x00 | temperature | write [0x00], then read 1 byte, unsigned °C (`fp9929a_read_temp`) |
| 0x02 | VCOM | `fp9929a_set_register` 0xffffff8008f1eb78, up to 5 attempts. `code = 0x74 + trunc((mV - 2500) / 22)`. Valid 605 ≤ mV < 5002, otherwise 2650 mV (codes 30..229) |

**Where VCOM comes from:**
- `eink_vcom_setup` 0xffffff8009c8fb48 parses `androidboot.eink_vcom` (4 ASCII digits). If that gives 0, the
  probe uses 2650 mV.
- On this phone `diag`+0x12B0 = "2730", so code **126 (0x7e)**. The UEFI log line `vcom_reg_value= 126` agrees
  independently.
- The sysfs `vcom` store only caches the value; it is applied at the next power-up.

**Power-up (`fp9929a_active_mode` 0xffffff8008f1ed50):**
```
gpio[fp+8] = 1          # field never parsed from the DT -> global GPIO 0 (a stock bug; leave out)
PWRUP (gpio86) = 1
usleep 5000-5100
i2c write reg 0x02 = vcom_code
i2c read  reg 0x00 -> cached temperature   # the only sample of epd_thermal
usleep 20000-20100
PWRCOM (gpio36) = 1     # VCOM output on (inferred)
active = 1; log PG = gpio82                 # 0 when on -> active-low power-good (inferred)
```

**Power-down (`fp9929a_sleep_mode` 0xffffff8008f1ef00):**
```
if !active: return
usleep 1000-1100
PWRCOM (gpio36) = 0
usleep 50000-50100
gpio[fp+8] = 0
PWRUP (gpio86) = 0
active = 0
```

Reading the `temperature` sysfs attribute while the chip is off runs a full active/sleep cycle. That is why reading
`epd_thermal` powers the panel ([epd-panel-screenshot.md](epd-panel-screenshot.md) §7).

### 2.3 Panel rails and GPIOs (`dsi_panel_power_on` 0xffffff8008f0e928, from `pre_prepare`)

**On.** Skipped if the touch driver asks for a power reset hold; the pinctrl is selected first unless continuous
splash is active:
```
dsi2dpi_reset(26)=0, epd_power_on(33)=1, xoe(34)=1, mode(81)=1, xon(25)=1,
dsi2dpi_vdcc_en(31)=1, i2c_mux_sel(2)=1, usleep 10000-10010, dsi2dpi_reset(26)=1   # "epd power on"
```

**Off, `post_unprepare` 0xffffff8008f0ff20:**
```
dsi2dpi_reset(26)=0, fp9929a_sleep_mode(), i2c_mux_sel(2)=0
```

**The 1.8 V rails stay up after that.** They drop only after userspace writes 16 to `epd_xon_en`:
- `epd_panel_xon_off` queues delayed work 60 jiffies (about 600 ms) later;
- the work, `dsi_epd_panel_pwroff` 0xffffff8008f0cf20, sets vdcc_en, power_on, xoe, mode and xon to 0 with no
  delays between them, then selects the sleep pinctrl.

This matches the A6L warning not to write `epd_xon_en`.

**Per-update rhythm (INFERRED from the code + dmesg).** Every E Ink update is a full DRM enable, then a disable a
few seconds later: PMIC on, bridge init, frames, PMIC off. A mainline panel/bridge driver sees the same
enable/disable rhythm.

### 2.4 Frontlight

**AW99703** (driver copies `_1` 0xffffff8008f1fae8.., `_2` 0xffffff8008f20ed8.., V1.0.4; 8-bit read-modify-write,
writes retried 5 times):

*Probe:* hwen high, wait 3.5-4 ms, then check **reg 0x00 == 0x03** (4 tries, 2 ms apart).

*Init (`aw99703_backlight_init` 0xffffff8008f1fcb0), with the DT values:*
```
R02 |= 0x10 (pwm-mode 0);  R02 |= 0x04
R04 = (R04 & 0xE3) | 0x10;  R04 |= 0x20;  R04 |= 0x03
R05 |= 0x40
R03 = (R03 & 0x07) | (bl_fscal_led << 3)        # DT 0
R03 = (R03 & ~7) | 1                            # bl-channel 1 (2 -> |3, 3 -> |7)
R09 = 0x44                                      # turn-on/off ramp 4/4
R0A = (R0A & 0x80)                              # pwm/i2c trans-dim 0/0
```

*Brightness:*
- The first non-zero value sets R02 = (R02 & ~3) | 1.
- With `bl-map 3` (the DT value) the value is used unchanged, then scaled by **0.81 (chip 1) or 0.75 (chip 2)**.
- It is written as 11 bits: **R06 = b & 7, R07 = b >> 3**.
- 0 sets R06 = R07 = 0 and R02 &= ~3; the next non-zero value re-runs init.

**KTD3137** (`_3` 0xffffff8008f226a8, `_4` 0xffffff8008f23e90; SMBus byte access):

*Probe:* hwen high with no delay. The "ID check" is only a successful read of reg 0x02.

*Init with the DT values:*
```
R03 = 0x62   # OVP 32 V (0x20) | fsw 1000 kHz (0x40) | linear (0x02); inductor 1800 mA, so no 0x08
R06 |= 0x80  # PWM off
R07 = 0x00   # ramps 0/0
R08 = 0x00   # trans-dim 0/0
R02 = 0x01   # bl-fscal-led raw
```

*Brightness (`ktd3137_update_brightness` 0xffffff8008f234c8 / 0xffffff8008f24ca8):*
- R02 bit 0 is set on 0 -> non-zero and cleared on 0.
- The value is clamped to the maximum (0x7FF with using-lsb, INFERRED), then scaled by **0.78 (chip 3) or 0.72
  (chip 4)**.
- With using-lsb: R04[2:0] = b & 7, R05 = b >> 3.
- Chip 4 skips the write if the value is unchanged.

**Off-mode charging.** `dsi_panel_power_on` reads `/kdebuginfo/edpd/backlight_flag`. With the flag 0 all four chips
get 0 ("BL SILENCE"). Otherwise AW#2 = 0x38 ("center") and KTD#4 = 0x36 ("edge").

So, by the strings (INFERRED meaning): **the AW99703 pair drives "center" and the KTD3137 pair drives "edge"**,
one chip of each pair per I2C bus.

**Mainline mapping.**
- **FP9929A:** a regulator driver modelled on `fp9931.c`, owning the GPIO sequence above, with VCOM in mV from DT
  or the bootloader parameter.
- **TC358767:** the existing bridge, adjusted as in §2.1.
- **Panel lines:** an E Ink "panel" driver for xon/xoe/mode/power-on and the 448×829 transport timing.
- **Frontlight:** an `aw99703` backlight driver (check `aw99706.c` first) and a `ktd3137` driver (check the KTD3136
  RFC first). Both are small register-level drivers.

---

## 3. Device tree outline and the ABL

**Files:**
- [`docs/mainline/sm6115-hisense-hlte556n.dts`](mainline/sm6115-hisense-hlte556n.dts): the outline, about 450 lines,
  every value cited to the stock DT;
- [`docs/mainline/hlte556n-null-overlay.dts`](mainline/hlte556n-null-overlay.dts): the minimal board overlay.

`scripts/mainline/dt-abl-check.sh <workdir>` fetches `sm6115.dtsi`, `pm6125.dtsi`, `pmi632.dtsi` and the
dt-bindings headers from torvalds master, compiles both files with `dtc -@`, and runs the phone's ABL on them. Since 2026-10-16 it copies the
local include `scripts/mainline/pmic/a9-pmi632.dtsi`, deletes every earlier output first and fails
when the compile fails (before, a failed compile silently reused a stale `a9.dtb`). Test:
`tests/dt-abl-check-tests.sh` (about 5 s).

### 3.1 Stock -> upstream mapping (MEASURED from the stock DT; upstream labels from `sm6115.dtsi`)

| Function | Stock | Outline (upstream node) | Upstream binding? |
|---|---|---|---|
| Debug UART | `qupv3_se4_2uart` | `&uart4` | yes |
| E Ink bus | `qupv3_se0_i2c` 4a80000 | `&i2c0` | yes |
| Frontlight 1 / audio bus | `qupv3_se1_i2c` 4a84000 | `&i2c1` | yes |
| Touch bus | `qupv3_se2_i2c` 4a88000 | `&i2c2` | yes |
| Touch | `atmel_mxt_ts_336u@4b`, irq tlmm 80 (flags 0x2008 = level-low, oneshot), reset 71, vccio gpio 21, vdd gpio 108, fw `TY001_10AA` | `atmel,maxtouch` + two GPIO `regulator-fixed` | yes |
| Volume up | `gpio_keys/vol_up`, `pm6125_gpios` 5, active-low | `gpio-keys` | yes |
| **E Ink key** | `gpio_keys/key_ai`, tlmm **84**, active-low, **linux,code 0x2fe** | `gpio-keys` `key-eink` | yes (code 0x2fe has no name) |
| Volume down / power | `qcom,pon_2` resin `linux,code 0x72`; kpdpwr | `&pon_resin`, `&pon_pwrkey` | yes |
| DSI | `mdss_dsi0`, 2 lanes, video mode | `&mdss_dsi0`, `data-lanes = <0 1>` | yes |
| Bridge | `dz,tc358767@0f` | `toshiba,tc358767` port@0 DSI in, port@1 DPI out | yes; refclk not described in stock |
| Transport panel | `qcom,mdss_dsi_epd_eink_fhdhs_video`, 448×829 at 85 Hz, `bl_ctrl_external` | `panel-dpi`, 40.078 MHz | yes, as a virtual DPI panel |
| E Ink PMIC | `fiti,fp9929a@48` | proposed `fitipower,fp9929a` | **no** |
| Frontlight ×4 | `awinic,aw99703-bl-1/2@36`, `ktd,ktd3137-3/4@35` | proposed `awinic,aw99703`, `kinetic,ktd3137` | **no** |
| E Ink thermistor | PMI632 VADC 0x54 | `&pmi632_adc` channel@54 `ADC5_GPIO3_100K_PU` | yes |
| Panel GPIOs xon/xoe/mode/power-on, i2c-mux-sel | panel node | comment only, no owner | **no** (needs an E Ink panel driver) |
| UFS | `ufshc@4804000`, vcc L24A, vccq2 L11A | `&ufs_mem_hc` | yes |
| SD card | `sdhci@4784000` **status "disabled"**, cd tlmm 88 (= the KTD3137-3 hwen pin) | `&sdhc_2`, disabled | yes; see open questions |
| GPU | – | `&gpu`, zap firmware from the phone's vendor | yes |
| Charger, fuel gauge, SMB1355 | `qcom,qpnp-smb5`, `qcom,qpnp-qg` (4000 mAh Hisense profile), `qcom,smb1355@0c` | comment only | **no driver** |
| Haptics | `awinic,haptic_hv@5a`, irq 28, reset 67 | placeholder `awinic,aw86927`, disabled | partly (chip unknown) |
| Speaker PA / DAC | `awinic,aw87xxx_pa_58@58` reset 107; `ess,hifi-es9218@48` reset 65, sel 69, osc 66, pa-pwr 68 | placeholder `awinic,aw87390`, disabled / comment | partly / **no** |
| Fingerprint | `sunwave,fingerprint` platform node: reset 19, irq 97, pwr 100, logic 70 | comment only | **no** (and needs a TEE TA) |
| Camera | `cam-sensor0` rear (CSIPHY0), `cam-sensor1` front (CSIPHY2) | not in the outline | **no SM6115 CAMSS** (§1.2) |

**Regulators.** In the outline, as PM6125 RPM LDOs with the stock ranges:
- L5 1.648-3.056 V, L7 1.2-1.304 V, L9 1.8-2.0 V (DSI vddio, touch), L11 1.704-1.952 V, L18 1.104-1.312 V,
  L22 2.952-3.304 V, L24 2.704-3.6 V.
- The panel's only listed supply is vddio = L9 (`dsi_panel_pwr_supply_no_labibb`), and the PMI632 LCDB is disabled.
  The E Ink high voltages come from the FP9929A.

**Memory map differences** (MEASURED: stock base DTB `reserved-memory` vs `sm6115.dtsi`). These must be overridden
before any remoteproc is enabled:

| Region | Stock | Upstream `sm6115.dtsi` |
|---|---|---|
| ADSP | 0x53800000, 0x1e00000 | 0x53800000, **0x2800000** |
| IPA fw / IPA GSI / GPU zap | 0x55600000 / 0x55610000 / 0x55615000 | 0x56100000 / 0x56110000 / 0x56115000 |
| "removed" | 0x60000000, **0x5c00000** | 0x60000000, 0x3900000 |
| Hisense debug (boot_log, subsys_trap, rs_recorder, ramoops) | 0xb0000000-0xb0680000 | – |
| Upstream-only | – | 0x89b01000 (0x200000), check whether stock reserves it elsewhere |

### 3.2 What the ABL needs (MEASURED: `scripts/abl-check.sh`, the phone's own LinuxLoader `1dd55dcb…`)

1. **SoC id.** The phone reports SoC id **0x1bc** rev 0x10000 (`Boardsocid = 1bc`, BoardVariant 0xb = QRD,
   subtype 0, foundry 0x20000). `qcom,msm-id` must contain `<0x1bc 0x10000>`. A DTB with only `<0x1a1 0x10000>`
   fails: "No match found for Soc Dtb type". The outline keeps both pairs, as the stock base DTB does.
2. **Board overlay.** The ABL **always** applies a board overlay matching board-id `0x1000b`, even when the SoC DTB
   carries a board-id (`0x1000b` and `0xb` were both tried; "Dtbo count = 0 LocalBoardDtMatch = 1c001400", the
   DTBO-not-needed flag stays false):
   - **the stock DTBO fails on a mainline base:** "Couldn't find 'thermal_zones' symbol in main dtb";
   - **an overlay without `__fixups__` fails:** "Bad overlay_fixups";
   - **what passes:** an overlay that changes nothing but has one fixup to a label the mainline DTB exports
     (`hlte556n-null-overlay.dts`, target `&tlmm`), with the base compiled with `dtc -@`.
3. **Delivery.** Both routes pass all three ABL stages (header, DTBO, DT selection + overlay; merged DTB 81,403
   bytes, model "Hisense A9 (HLTE556N)"):
   - as a **recovery image** with the overlay as `--recovery_dtbo`, which needs no `dtbo` partition write;
   - as a **boot image** with the overlay flashed as the `dtbo` partition.
   - With the stock `dtbo` partition, a mainline boot image fails.

Not checked by the emulator: AVB (orange, flags 3), the ABL's DT fixups (memory, cmdline) and the kernel itself
([abl-check.md](abl-check.md)).

### 3.3 Open DT questions (cheap, read-only on the phone)

- **SD card.** CLAUDE.md lists an SD card at `mmcblk1p1`, but `dtbo_a` disables `sdhc_2`, and its `cd-gpios`
  (tlmm 88) is also the KTD3137-3 hwen pin. Read `/proc/device-tree` and `/sys/class/mmc_host` to see which is
  live.
- **`i2c-mux-sel` (tlmm 2).** Its purpose is unknown; it is set high during panel power-on and low after.
- **TC358767 refclk.** The table says 19.2 MHz, but no clock is described anywhere.
- **TZ-reserved GPIOs.** The stock DT has no `gpio-reserved-ranges`. Upstream boards reserve some pins: a wrong
  TLMM access to a TZ-owned pin resets the SoC. Check with `/sys/kernel/debug/gpio` before the first mainline boot.

---

## 4. The crucial unknown: a standalone software TCON

### 4.1 Which route: (a) call the composer's code, or (b) lift the tables and reimplement

| | (a) Call the stock code in isolation (the A6L method) | (b) Lift the tables, reimplement the frame generation |
|---|---|---|
| A9 situation | The full TCON is **not a library**. It is internal to the composer executable (`EinkSwTconThreadHandler` 0x1a5acc, `lookUpTable` 0x1d1690, `DitherSStyle16` 0x1a8300; names only from `.gnu_debugdata`). It links SDM/Android vendor libraries, and its state lives in globals and in a 27 MB `HWCDisplay` object | Tables are plain `.rodata` arrays with a mapped layout ([epd-waveform-source.md](epd-waveform-source.md)). The logic around them is small, integer-only and deterministic |
| On a mainline vendor | Would need a custom loader that maps a PIE executable and calls internal addresses. Fragile, and tied to one binary | Plain code; the tables are read from the user's own composer at install time (not redistributed) |
| Fallback | `librecovery_ui.so` **is** an A6L-style standalone TCON (`Init_Eink_SWTcon`, `Update_epd_Image`; TWRP drives it today), but it has 2 fixed 38-frame tables and no temperature compensation | – |

**Choice: (b).** Route (a) was still used, under Unicorn only, as the oracle that (b) is checked against.

### 4.2 What the stock TCON does (MEASURED from the disassembly, then confirmed by the emulation)

Composer = live device copy, sha256 `8c89557b…` (= `/vendor` on the phone, epd-waveform-source.md §6). It builds on
the earlier static RE in `~/source/a9-display-evidence-20261012/STATIC-RE-COMPOSER-20261012.md` and that work's
Unicorn harness for the grey builders, which this one re-verifies.

1. **Grey input.** `0x1a5e1c` converts SurfaceFlinger's RGBA to grey: luma `(76·c0 + 150·c1 + 30·c2) >> 8` with
   NEON, then 8×8 `trn` transposes. These rotate the portrait buffer (832-byte stride) into the TCON's landscape
   **1648×824** grey buffer `[this+0x7e8]`. *Not emulated*; the rotation direction is INFERRED.
2. **Conditioning** (`0x1a7338`, jump table 0x2ac3c, on the mode's low byte `lo` and high byte `hi`):

   | Mode (lo) | Grey table | Dither |
   |---|---|---|
   | 1 Balanced, 6 Smooth | g2 (`this+0x19e7214`, built from black/white/contrast) | `hi & 4`: 32×32 ordered (matrix at 0x14b983, plus white above 0xef); otherwise 1-bit serpentine error diffusion (5/16 ahead, 7/16 below, 1/8 diagonal alternating with column parity) |
   | 9 Speed | g2 | ordered |
   | 3 Clear | g16 (`this+0x19e7314`) | `hi & 1`: `DitherSStyle16` (16-level serpentine diffusion over 3 rows with an x%3 phase) |
   | 2 | g16 | `DitherSStyle16` always |
   | 0, 4, 5, 7, 8 | none | none |

   Every mode then shifts the result `>> 4`, giving target levels 0..15.
3. **Pack.** Each pixel becomes the uint16 `old<<4 | new`. For mode 3 with clear=0 (0x1a7e64), each pixel going
   from non-white to white (`old != 15`, `new == 15`) marks its first steady-white neighbour as **256**: left, then
   right, up, down. Then old := new.
4. **Type and temperature bin.**
   - The final waveform type is the mode's low byte. It becomes 2 when the type is 1 or 3 and the clear flag is set.
   - The bin comes from `avg = (2·t1 + t2) / 3`, with each t clamped to 50 and forced to 0 when bit 7 is set (a
     quirk), looked up in the 51-byte table at 0x2b0d0 to give bins 0..13.
5. **Frames (`lookUpTable`).** Per frame it fills the 257-entry scratch LUT `0x4c86d9` with the drive code (0, 1
   or 2) of every transition at frame index c, from the type's table and bin.

   **The output frame** is **448×829 words of 4 bytes**:
   - rows 0..4 are blank;
   - in rows 5..828, words 0..411 carry **4 pixels in byte 0** (`lut[p0]<<6 | lut[p1]<<4 | lut[p2]<<2 | lut[p3]`);
   - words 412..447 are 0;
   - bytes 1..3 are never written **by lookUpTable**. They are NOT zero on the panel: they hold the **control
     template** (byte 1 gate clocks and per-word source strobes, byte 2 0x00, byte 3 0xff) that
     `HWCDisplay::SetPowerMode` (0x19eed0) builds into the frame buffers once at TCON start and nothing ever
     overwrites; null frames clear byte 0 only, so stock's idle frame IS the template. (Corrected 2026-10-15
     after boot 12: this section used to say only byte 0 matters; our transport zeroed bytes 1..3 and the panel
     was never clocked. MEASURED on 16 live stock ring buffers, all 371,392 words.)

   So one 824-row × 1648-pixel picture rides in the **first byte of a 448×829 XRGB8888 frame**, on top of the
   template in bytes 1..3. That matches the DSI mode exactly.

   **Two frame disciplines:**
   - *Table* (types 2, 3/4/5/7, and Balanced when request `S+4` = 1): every frame is read from the table, and the
     update has `len` frames.
   - *Hold* (Smooth 6/8, Speed 9, Balanced with `S+4` = 0): frame 0 comes from the table and is cached, the next
     frames repeat it, and the update ends with null frames (all zero). Balanced emits two null frames, the others
     one.

   **Every update's last frame is all zero** (no drive).
6. **Init flash.** After TCON start, a one-time global flash (`0x1a7d04`) sends frames whose every pixel carries
   the same code. They come from a table at 0x2b103 (stride 0x78, 109 frames at bin 0 down to 51 at bin 13).

Waveform tables (row = transition `old<<4|new`, code 1 drives toward black, 2 toward white (INFERRED from rows
0x0f/0xf0), 0 = no drive):

| Type | Table | Row stride | Slots | Frames per bin 0..13 |
|---|---|---|---|---|
| 3,4,5,7 (Clear) | 0x2b793 | 0x6a | 257 | 104 … 30 |
| 2 (clear-flag) | 0x8895f | 0x6a | 257 | 104 … 30 |
| 1 (Balanced) | 0xe5b2b | 0x38 | 256 | 54 … 12 |
| 6, 8 (Smooth) | 0x116e3b | 0x1e | 256 | 28 … 8 |
| 9 (Speed) | 0x1313df | 0x1e | 257 | 28 … 7 |

Slot 256 of the Clear table is the "sentinel" cleanup waveform (`…121212122…` at bin 10).

### 4.3 The prototype and the comparison

- **`scripts/mainline/a9tcon.py`** is the model. It contains no vendor data: it reads tables, bin map, init table
  and dither matrix from the composer passed in, and checks its sha256.
- **`scripts/mainline/tcon-oracle.py`** loads the same composer into Unicorn and runs the stock code on the same
  input. That covers the grey builders, the conditioning-to-bin block 0x1a7338..0x1a7b30, `lookUpTable` until it
  returns 0, and the init-flash block.
- Only memcpy, memset and the logger are stubbed; any other call faults. It then compares the levels, the packed
  indices, the type, the bin and **every byte of every frame**.

Results (MEASURED, 2026-10-14). Inputs: the stock AOD cat picture (`$A9_DATA/assets/stock-aod/eink-cat.png`,
sha256 `097edf77…`, letterboxed) and a synthetic chart (16 grey steps, a full ramp and a checker strip). Old state:
white, or the previous update's levels, or their inverse.

| Check | Result (each image) |
|---|---|
| g2/g16 builders, 3 knob settings incl. out-of-range | match |
| temperature -> bin, 73 × 8 pairs incl. negative, 128, 200, 255 | match |
| init flash, bins 0 / 10 / 13 (109 / 63 / 51 frames) | match |
| 12 updates: 0x201 (white and grey old; `S+4` = 0 and 1 give different frames, both match), 0x206, 0x209, 0x203 clear 0/1, 0x201 clear 1 (type 2, bin 0, 104 frames), 0x401 (ordered), 0x303 (DitherSStyle16 + **3,722 sentinels** on the synthetic chart, 93 on the cat), 0x202, 0x208 (noise, no conditioning) | levels, pairs, type, bin and all frames match |
| **Total** | **34/34 checks, 0 failures; 397 update frames + 223 init frames, each 1,485,568 bytes, byte-identical** (byte 0 only in effect: both sides had bytes 1..3 zero; the control template was not part of the comparison, see the 2026-10-15 correction in item 5) |

**Answer to the crucial unknown: yes.** The stock frame generation can be reproduced exactly from the composer's
tables, so the E Ink half of a mainline port needs no vendor code at run time.

**Not covered yet** (all bounded; the mode-switch and grey/rotation items are now covered by the C port, §4.5):
- **Mode-switch requests.** The multi-step sequences, e.g. Balanced -> Smooth = Balanced + 4 Smooth steps, are
  decoded in the earlier static RE (UPDATE 8), but their per-step index arrays (`S+0x30..`) are not traced. The
  model only does steady updates; a mode switch can fall back to a clear (type 2) update.
- The RGBA -> grey + rotation stage (above), and the frame pacing and DSI commit timing (85 Hz, one frame per
  vsync, INFERRED).
- Optical correctness. Byte equality with stock is proven; what the glass shows still needs the phone.

### 4.4 How a mainline E Ink stack would use it (plan, INFERRED)

- **The A6L ran its TCON beside an LCD.** It used a daemon, a DRM lease and a mirror. On the A9 the E Ink is the
  **only** display, so the TCON becomes the primary display path.
- **Option 1, a userspace compositor backend.** drm_hwcomposer or a small custom HWC hands SurfaceFlinger's
  composed buffer (HWC composition is already off on this phone) to the TCON. The TCON then page-flips the
  448×829 drive frames to the DSI connector (`panel-dpi` behind the TC358767).
- **Option 2, a kernel DRM driver.** It does the same in-kernel, as a "virtual 824×1648 grey plane in, drive frames
  out" encoder.
- **Recommendation: start with option 1.** The model is Python today; a C port is mechanical.


### 4.5 C port of the TCON, plus mode switches and the grey stage (2026-10-14)

**Status:** done offline. The phone was not touched. Every item below was checked against the stock code running
under Unicorn, except where a line says otherwise.

**Files:**
- `scripts/mainline/tcon/a9tcon.h`, `a9tcon.c`: the core. C11, integer only, no allocation. The caller passes
  every buffer in. It needs nothing beyond `memcpy` and `memset`: `make freestanding` compiles it with
  `-ffreestanding -nostdlib`, and `nm -u` lists only those two (MEASURED). So the same code can go into a DRM
  driver or a userspace daemon.
- `a9tcon-load.c`: the table loader. It checks the composer's sha256 with its own small SHA-256, then copies the
  tables from the offsets of §4.2.
  - The tables are contiguous in `.rodata`: init flash 0x2b103, Clear 0x2b793, type 2 0x8895f, Balanced 0xe5b2b,
    Smooth 0x116e3b, Speed 0x1313df, and the dither matrix at 0x14b983. Each one ends where the next one starts
    (MEASURED from the sizes).
  - The copy totals about 1.2 MB. No vendor bytes are in the repo.
- `a9tcon-cli.c`: the CLI, `build/a9tcon COMPOSER`. It takes raw grey or RGBA, old levels, mode, clear flag,
  temperatures, an optional `--pre` mode for a mode switch, and `--init` for the init flash. It writes the frames
  (`--frames`) and the new levels, and prints a sha256 for every frame and for all of them together.
- `Makefile`: builds into `build/`, which is gitignored.
- `test-tcon-c.py`: the test. It drives `liba9tcon.so` through ctypes and runs the CLI. It compares both with
  `tcon-oracle.py`'s `Stock` and with `a9tcon.py`.
- `tcon-oracle.py` gained five more stock entry points, listed in the next subsection.

**New stock code under emulation** (only memcpy, memset and the logger are stubbed, as before):

| Range | What |
|---|---|
| `0x1a5acc..0x1a5c80` | Handler prologue. Copies the rodata step vectors to the stack and sets v8 = (6,6), v15 = (9,9), the luma weights v12..v14 and x27 |
| `0x1a6604..0x1a6ea0` | Mode-switch request builder: request S, step index arrays, old := last level, mode word, clear flags |
| `0x1a7c78..0x1a7df4` | Per-frame dispatch: step n with `[S+0x10+4n]` / `[S+0x30+8n]`, then the final pass |
| `0x1a61b0..0x1a62fc` | RGBA -> grey + rotation. The copy at 0x1a5e0c is identical code |

**Results** (MEASURED 2026-10-14). Inputs:
- the stock AOD cat, sha256 `097edf77…`;
- the synthetic chart;
- random RGBA and random old levels, numpy seed 7.

**174/174 checks match on each image, 0 failures.** 5,648 frames of 1,485,568 bytes are byte-identical with stock,
and 1,471 of them also with `a9tcon.py`. Runtime: 739 s and 773 s. **Scope (corrected 2026-10-15):** both sides
started from zeroed buffers, so this proved byte 0 (the drive data) only; bytes 1..3 (the control template) were
zero on both sides and the port's null frames (`memset`) differed from stock's (byte 0 cleared only) in a way
the test could not see. The whole-word re-run with the template is in docs/mainline-eink-steps.md, top section.

| Section | Checks (each image) | Notes |
|---|---|---|
| A grey builders | 4 | 4 knob settings, incl. out of range; C = stock = model |
| B temperature -> bin | 2 | 657 (t1, t2) pairs incl. -100000, -129, 128, 255; all 14 bins occur in the map |
| C init flash | 14 | **every bin 0..13** (109 … 51 frames) |
| D steady updates | 27 | the 12 cases of §4.3 (prepare + frames, Balanced S+4 = 0 and 1); C = stock = model |
| E lookUpTable sweep | 102 | 3,145 frames. Types 1 (S+4 = 0 and 1), 2, 3, 6 and 9 at **every bin**, plus types 0, 4, 5, 7, 8, 10, 255, and 6/9 with S+4 = 1, at bins 3 and 12. The pairs array contains all 257 indices |
| F request builder | 1 | **264 cases**: pre 0..10 × cur 0..10 × clear 0/1, plus `this+0x6c7` = 0 for pre 1/6. Compares kind, S+4, code, count, step types and levels, the mode word, the clear flag and its reset, old := level, and **all five persistent step arrays** after every case |
| G mode-switch updates | 16 | builder + prepare + dispatch, every frame (14 … 188 frames each) |
| H RGBA -> grey | 3 | random RGBA, and the test picture as an 832-stride portrait buffer; closed-form rotation checked |
| I CLI | 5 | steady 0x201 and the init flash vs `a9tcon.py`; three switches vs stock (whole-update sha256) |

**What the new stock paths do** (MEASURED: disassembly and emulation agree):

1. **Grey + rotation.**
   - SurfaceFlinger's buffer is **1648 rows of 832 RGBA pixels**. Only columns 0..823 are used.
   - Grey is `(76·b0 + 150·b1 + 30·b2) >> 8`, where b0..b2 are bytes 0..2 of each pixel. They are R, G, B if the
     buffer is RGBA8888 (INFERRED).
   - The TCON buffer is `grey[y][x] = luma(src row x, column 823 − y)` (x < 1648, y < 824).
2. **Request builder**, run before every update with the previous mode's low byte `pre` and the new mode `cur`.
   - It first zeroes S+0..+0xf.
   - It does nothing more when `pre == cur` and clear = 0.
   - Otherwise the steps are (step types; the final pass follows):

   | cur \ pre | 1 | 2 or 3 | 6 | 9 |
   |---|---|---|---|---|
   | 1 Balanced | (clear request) | none, but **S+4 = 1**: the Balanced pass uses the full table, not hold | [6], sets clear | [9], sets clear |
   | 2 | [1, 1] | (clear request) | [6, 6, 6, 6] | [9, 9, 9, 9] |
   | 3 Clear | none (kind 1; no clear request even with clear = 1) | (clear request) | [6], sets clear | [9], sets clear |
   | 6 Smooth | [1, 6, 6, 6, 6] | [3, 6, 6, 6, 6] | (clear request) | [9, 6, 6, 6, 6] |
   | 9 Speed | [1, 9, 9, 9, 9] | [3, 9, 9, 9, 9] | [6, 9, 9, 9, 9] | (clear request) |

   - **Clear request** (kind 2): any other pair, when clear = 1. cur 3 gives [3], cur 6 gives [6 × 5], cur 9 gives
     [9 × 5], and every other cur gives no steps.
   - When clear is set after the builder, it is reset after the update (`this+0x6e0`).
   - The mode word gains 0x100 when byte `this+0x6c7` is 0.
   - "Sets clear" means the final pass of 6->1, 9->1, 6->3 and 9->3 runs the **type-2** table.
   - **Correction:** UPDATE 8 of the earlier static RE gives cur 1, pre 2/3 as [3, 6, 6, 6, 6]. That is wrong;
     those steps belong to cur 6.
3. **Step index arrays.**
   - Each step uses a uniform transition, from the request's levels `S+0x24..0x28`: step 0 is old -> l0 (0xf for a
     single step); step k is l(k−1) -> l(k).
   - Afterwards old := the last level, and the final pass packs from that.
4. **A stock bug, reproduced as is.**
   - In 5-step requests the builder writes the step-3 array (`S+0x48`) twice: l2->l3, then l3->l4.
   - It never writes the step-4 array (`S+0x50`). That array keeps the zeros from the TCON start (memset at
     0x19f514; a search of `.text` finds no other writer).
   - So 1->6 really runs:
     - Balanced, old -> white;
     - Smooth, white -> black;
     - Smooth, black -> black;
     - Smooth, black -> white;
     - Smooth on index 0 (black -> black);
     - the final Smooth pass, white -> new.
   - The C port keeps all five arrays persistent and leaves `S+0x50` alone, so its output matches stock.
5. **Dispatch.**
   - A step whose lookUpTable pass ends still returns "more frames". Its last, all-zero frame is sent, then the
     next step starts.
   - Total = Σ len(step type, bin) + len(final type, bin), e.g. 1->6 at bin 10: 14 + 4×8 + 8 = 54 frames (logged
     54 on the phone, UPDATE 5).
   - Each step's hold/table discipline is that of its type, with S+4 from the request.
6. **Temperatures** (read from the disassembly at 0x1a6334..0x1a65a4; not emulated):
   - t1 comes from `/sys/devices/platform/soc/soc:qcom,dsi-display-primary/epd_thermal`, in °C. A value above 85
     reads as 0.
   - t2 comes from PMI632 `…vadc@3100/iio:device0/in_temp_epd_top_therm_input`, in m°C, divided by 1000: below
     −999 it reads as 0, above 50999 as 50.
   - The composer reads both on every update (MEASURED from the code). §6 warns that reading `epd_thermal` by
     hand power-cycles the PMIC; how that squares with the composer reading it on every update is not explained
     (open).
7. **Overflow.** Stock traps (`b.vs` -> abort) when `2·t1 + t2` overflows int32. The C port computes that sum in
   int64, so the stock trap has no counterpart.

**Speed** on the host, i5-1235U, `-O2`, one thread, with two oracle runs in parallel (MEASURED). Per update:

| Stage | Time |
|---|---|
| RGBA -> grey + rotation | 5.4 ms |
| Conditioning, error diffusion (0x201) | 16 ms |
| Conditioning, ordered dither (0x401, 0x209) | 7.5 ms |
| Conditioning, DitherSStyle16 (0x303, 0x202) | 27 ms |
| Conditioning, plain (0x203, 0x208) | 1 ms |
| Pack with sentinels | 2.8 ms |
| Table frame | 0.8 ms |

At 85 Hz (INFERRED) one frame has 11.8 ms. The phone's Cortex-A73 cores will be slower, by a factor not measured.
The per-frame part fits the budget comfortably. The per-update conditioning is the part to watch (or to vectorise).

**Still open:**
- **Not validated:**
  - frame pacing and the DSI commit, with its vsync timing;
  - optical correctness (needs the phone);
  - the second grey source at 0x1a5fc0: a 16-bit buffer at `this+0x6a0` with its own rotation (not ported);
  - who sets `this+0x6c7` and the pre/cur mode fields (`this+0x6e4`/`0x6e8`). The model takes them as inputs.
  - what triggers the init flash (bit 0 of `0x4c7fb8`). The model exposes it as `a9t_init_frame`.
- **Not reproducible:** the stock overflow trap in the temperature sum.
- **Assumed:** pairs values ≤ 256. Stock indexes its 257-byte LUT without a bound, and so does the port.

**Reproduce:**
```sh
nix-shell -p gcc gnumake 'python3.withPackages(p:[p.unicorn p.numpy p.pillow])' --run \
  "python3 -I scripts/mainline/tcon/test-tcon-c.py $C $A9_DATA/assets/stock-aod/eink-cat.png --tmp $S/tcon-cli"
# without the image argument: the synthetic chart; --quick: 64 checks in about 2 minutes
nix-shell -p gcc gnumake binutils --run 'make -C scripts/mainline/tcon freestanding'   # nm -u: memcpy, memset
```

---

## 5. Recommended next step

Do two offline tasks first:
1. ~~**C port of `a9tcon.py`** (with the same oracle as its test), plus decoding the mode-switch index arrays.~~
   **Done 2026-10-14** (§4.5).
2. **The mainline kernel config and patch set for a diskless boot:**
   - `sm6115-hisense-hlte556n.dts`;
   - an FP9929A regulator driver;
   - a minimal E Ink panel driver (GPIOs + 448×829 `panel-dpi` timing);
   - the TC358767 tweaks.
   Build them on GitHub Actions or in an Ubuntu container, never on this box. Check each image with `abl-check.sh`
   (the recovery route of §3.2).

**Then the first on-phone step** (superseded 2026-10-15: use `boot_b` + `dtbo_b` and
`scripts/mainline/first-boot.sh`, [mainline-kernel-build.md](mainline-kernel-build.md) §4; a `recovery_b` entry
through `fastboot reboot recovery` loops on the BCB): a diskless mainline boot from **`recovery_b`**:
- with an embedded `recovery_dtbo`;
- a ramdisk that never mounts `/data`;
- serial or USB gadget logging, or `pstore` on the ramoops region.

It answers four questions in one boot: does the kernel start, do the DSI and the bridge come up, does the FP9929A
answer, and does a drive frame reach the glass?

## 6. What needs the phone, and the risk of each step

| Step | Needs | Risk |
|---|---|---|
| Read-only checks of §3.3 (sdhc_2, i2c-mux-sel, TZ GPIOs, refclk hints in `/sys/kernel/debug/clk`) | adb shell, root for debugfs | **None** to the device. Do not read `epd_thermal` (it power-cycles the PMIC) |
| Optical check of the TCON's output: what the glass shows | Phone with stock vendor; compare `epd-snap` with the model's frames | None (read-only) |
| Diskless mainline boot (2026-10-15: from `boot_b` + `dtbo_b` via `scripts/mainline/first-boot.sh`, human-run only; see mainline-kernel-build.md §4) | ~~`flash-recovery.sh`-style write of recovery_b only~~ | **Measured 2026-10-15: the recovery route loops on the BCB and needed EDL.** Original estimate: **Low for the slot and `/data`**: recovery failures do not consume slot retries (CLAUDE.md rule 4), TWRP stays on `recovery_a`, nothing mounts `/data`. **Medium for the panel:** a wrong FP9929A sequence or VCOM could damage the E Ink panel (INFERRED; the sequence in §2.2 lowers this). **Display blank until reboot** is likely while the driver is wrong. TZ-owned GPIO access resets the SoC (harmless, reboots) |
| Charging under mainline | A PMI632 charger/QG driver (does not exist) | **High:** without it the battery is neither charged nor protected by mainline. Keep mainline sessions short and on a charged battery; return to stock to charge |
| Boot image on slot B (`boot_b` + `dtbo_b`) | Writing `dtbo_b` with the null overlay | Medium: breaks slot B's stock Android until `dtbo_b` is restored (golden copy exists). Slot A untouched |
| A full mainline ROM | New vendor, `/data` **wipe** (`twrp format data`), different key | High, accepted by the user: TWRP backup `golden-20261014` exists, but it restores only to a crDroid-keyed stock-kernel ROM. Hardware-wrapped-key FBE will not be available on mainline at first (the A6L runs without encryption) |
| Never | `devinfo`, `diag`, the stock boot chain, `fastboot -w`, relocking | CLAUDE.md rules 10, 13, 14 apply unchanged |

---

## Appendix: reproduction (offline)

```sh
S=/tmp/claude-1000/-home-user-A9/138276b1-32cf-47e9-a654-4be339175015/scratchpad   # session scratch
PY="nix-shell -p 'python3.withPackages(p:[p.capstone p.pyelftools p.unicorn p.numpy p.pillow])' --run"

# §4 TCON: model vs stock under Unicorn (about 50 s per image)
C=~/source/a9-display-evidence-20261012/vendor/vendor.qti.hardware.display.composer-service   # sha256 8c89557b...
eval $PY "'python3 -I scripts/mainline/tcon-oracle.py $C $A9_DATA/assets/stock-aod/eink-cat.png'"
eval $PY "'python3 -I scripts/mainline/tcon-oracle.py $C'"            # synthetic chart
# §4.5 C port vs stock and vs a9tcon.py (about 12 min per image; --quick about 2 min)
nix-shell -p gcc gnumake 'python3.withPackages(p:[p.unicorn p.numpy p.pillow])' --run \
  "python3 -I scripts/mainline/tcon/test-tcon-c.py $C $A9_DATA/assets/stock-aod/eink-cat.png --tmp $S/tcon-cli"
# disassembly with .gnu_debugdata symbols (dbg.elf from tools-host/extract-debugdata.py)
eval $PY "'python3 -I scripts/mainline/elfdis.py $C dbg.elf 0x1d1690 0x1d1d58'"      # lookUpTable

# §2 kernel sequences (vmlinux from kernel-re-assessment appendix)
eval $PY "'python3 -I scripts/mainline/decode-tc358767-table.py $S/kre/img/vmlinux_boota.elf'"
eval $PY "'python3 -I scripts/mainline/kern-dis.py $S/kre/img/vmlinux_boota.elf dsi2dpi_init dsi_panel_power_on \
  dsi_panel_post_unprepare dsi_epd_panel_pwroff fp9929a_active_mode fp9929a_sleep_mode fp9929a_set_register \
  fp9929a_read_temp eink_vcom_setup aw99703_backlight_init aw99703_set_brightness ktd3137_probe ktd3137_update_brightness'"
eval $PY "'python3 -I scripts/mainline/kern-dis.py $S/kre/img/vmlinux_boota.elf --hex 0xffffff8009bb7130:8'"  # 0.81 scale

# §3 DT: stock fixups resolved, then the outline through the phone's ABL
python3 -I scripts/mainline/dtbo-resolve.py $S/kre/dt/dtbo_0.dts 'fragment@20' 'fragment@31' 'fragment@29'
scripts/mainline/dt-abl-check.sh $S/mainline/dtcheck     # recovery PASS, boot+overlay PASS, boot+stock dtbo FAIL
```

Upstream sources (fetched 2026-10-14): git.kernel.org `torvalds/linux.git/plain/…` (`sm6115.dtsi`, `pmi632.dtsi`,
`pm6125.dtsi`, `camss.c`, `qcom_smbx.c`, `a6xx_catalog.c`, `tc358767.c`, `gcc-sm6115.c`, `aw99706.c`, `fp9931.c`,
dt-bindings headers), patchwork.kernel.org API (SM6115/QCM2290/PMI632/QG/KTD3136/aw86xx series), lore `/raw` for
cover letters, gitlab.freedesktop.org Mesa `freedreno_devices.py`.
