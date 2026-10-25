# Stock boot logs from the `kdebuginfo` / `kdebug` partitions

**Date:** 2026-10-13. **Status:** offline analysis, done. No phone access. Inputs were read with `debugfs -c`
and Python; nothing in `$A9_DATA` was written except one PNG (§6). Each item is marked **measured** (read from a
file, with its location) or **inferred**.

**Inputs** (golden dump `$A9_DATA/Backups/edl_2026-10-11_132444/`):

| File | What it is |
|---|---|
| `lun0/kdebuginfo.bin` | ext4, label `kdebuginfo_data_`, 200 MiB, mounted 8 times. Last mounted 2026-08-11 02:50 UTC, the last stock boot |
| `lun0/kdebug.bin` | **raw** 10 MiB partition (not ext4), written by the kernel. **Still written under the GSI** (2026-10-11 entries) |
| `lun0/phonelog.bin` | ext4 with only `lost+found` (12 non-zero 4 KiB blocks). Empty |
| `lun4/boot_b.bin`, `lun0/recovery_b.bin` | Kernel: the same 15,047,394 B `4.19.157-perf`, built Dec 28 2022, on both. The GSI runs this kernel |
| `super.bin` → `vendor_a` (lpunpack) | Live stock vendor: init rc, sepolicy, `crash_mond` |
| `HLTE556N/super_2.img` (stock package system) | `kernel_mond`, `init.rc`, charger and framework strings |

Times: the kmonlog file names use CST (UTC+8). `power_reason.txt` and the ext4 times are UTC.

---

## 1. Boot timeline: 8 stock boots, all on 2026-08-11 UTC (an OTA chain)

All 8 logs come from **stock** Hisense Android 11. Every boot runs a Hisense kernel. Builds advance
Jul → Aug → Oct → Dec 2022, and the slot flips at each step. That is an A/B OTA chain from the factory flash up to
the current boot chain (inferred from the dates and slots). No custom ROM boot reached this partition, because a
GSI never mounts it (§4).

| # | kmonlog (CST) | XBL `SBL1 BUILD` / ABL `Loader Build Info` | Kernel build | Slot | vbmeta digest | Notes |
|---|---|---|---|---|---|---|
| 1 | `1970_0101_175433` | Jul 12 2022 / Jul 13 2022 | Jul 13 2022 `root@HISENSE` | `_a`, retry 6 | `1b102d2b…` | **First boot after the EDL package flash** (inferred): RTC unset, DDR training 7.40 s with `sbl1_save_ddr_training_data`, `Is Coldboot : 0`, ABL EBS 11.0 s |
| 2 | `2026_0811_093454` | Aug 26 / Aug 27 2022 | Aug 27 2022 | `_b`, retry 6 | `061e2a8d…` | First boot of the new slot. UEFI: `Backup GPT CRC does not match … Restore backup partition table success` (l.164) |
| 3 | `2026_0811_093640` | Aug 2022 | Aug 27 2022 | `_b` | `061e2a8d…` | Lasted about 32 s (mtime 01:37:12). No power_reason line |
| 4 | `2026_0811_093736` | Aug 2022 | Aug 27 2022 | `_b` | `061e2a8d…` | |
| 5 | `2026_0811_094912` | Oct 26 2022 | Oct 26 2022 `root@gfkfcmo` | `_a`, retry 6 | `3acc3ea4…` | `powerlog/cpuinfo.txt` 09:59 CST shows `update_engine` at 98 %: it installed the next OTA |
| 6 | `2026_0811_100601` | Dec 28 2022 | Dec 28 2022 `complie.r740xd…` | `_b`, retry 6 | `c0a88ef5…` | = the boot chain on the phone now (ABL Dec 28 2022, a6l-harvest 1.2) |
| 7 | `2026_0811_101910` | Dec 28 2022 | Dec 28 2022 | `_b` | `c0a88ef5…` | Log ends at 29 s. Next boot came 31 min later. No power_reason line |
| 8 | `2026_0811_105015` | Dec 28 2022 | Dec 28 2022 | `_b` | `c0a88ef5…` | **Rooted stock**: ABL `avb_slot_verify.c:263: ERROR: boot_b: Hash of data does not match`, and 9 `magiskinit` kmsg lines. Init verbose (1176 init lines) |

`Active Slot _x is bootable, retry count 6` appears only on the first boot of a newly switched slot (#1, 2, 5, 6).
Later boots print just `Booting from slot`. VB state is `orange(1)` and `androidboot.vbmeta.device_state=unlocked` on
every boot. The device state is read from RPMB (`VB: RWDeviceState: Succeed using rpmb!`).

**Boot-stage timings (measured, ms from PBL):**

| Build | XBL `SBL1, End` | `UEFI Start` | `POST Time … OS Loader` | `Exit EBS` | kernel `Run /init` |
|---|---|---|---|---|---|
| Aug/Oct 2022 | 904 | 1110 | 2025 | 3667–3689 | 1.80–1.83 s |
| Dec 2022 | 1814–1852 | 2017–2058 | 2931–2973 | 4597–4616 | 1.88–1.91 s |

The Dec 2022 XBL is about 0.9 s slower before `Is HardReset` (1.48 s against 0.57 s). The cause was not found.

### 1.1 Reset and power-on reasons (measured; XBL l.45-47, ABL ~l.285-292, kernel qpnp l.631-635)

| # | XBL `PM 0` (PM6125 SID0) | ABL `PON reg` | ABL decode | Kernel SID0 (PM6125) | Kernel SID2 (PMI632) | `Warm reset` | `Reboot reason` | `Charger In` | VBUS (XBL) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `0x8000028000000041` | 0x41 | CABLE + HRST | on: Hard Reset, 'cold'; off: PS_HOLD | on: CBL, 'cold'; off: SOFT | 0 | **0x0** | 1 | SDP |
| 2-5 | `0x8000028000000001` | 0x01 | HRST | same | same | 0 | **0x2A** | 0 | `fedl, vbus_low` |
| 6 | `…0001` | 0x01 | HRST | same | same | 0 | 0x2A | 1 | SDP |
| 7 | `…0041` | 0x41 | CABLE + HRST | same | same | 0 | 0x2A | 1 | SDP |
| 8 | `…0041` | 0x41 | CABLE + HRST | same | same | 0 | **0x0** | 1 | SDP |

All 8 boots: XBL `PM: HARD_RESET by PS_HOLD`, `PM 1=0x8000018000000040:0x800800` (the PMI632 CBL bit), `Is HardReset: 1`,
`Resin Pressed : 0`, `PwrKey Pressed: 0`, `TCSR reg value 0x0`, `restore magic_1/2: 0x0`.

- **Measured:** the low byte of `PM 0` is `PON_REASON1`. Bit 0 is HARD_RESET and bit 6 is CBL. **No boot has SMPL
  (bit 1) or KPDPWR (bit 7) set.** No boot here was started with the power key: all 8 are PS_HOLD software resets.
  Warm reset is never seen, which matches `CONFIG_SUPPORT_WARM_RESET` not set (a6l-harvest 1.7).
- **Measured:** PMI632 (SID2) reports `CBL` / `SOFT` on every boot, even when ABL says `Charger In: 0`.
  `Charger In` is cable presence at ABL time. The PON CBL bit records the cable at the power-on event.
- **Inferred:** `Reboot reason 0x2A` marks a reboot that Android requested (boots 2-7 follow OTA and user reboots).
  `0x0` follows a reset that did not come from Android: #1 after EDL, and #8 most likely after `fastboot reboot`
  once a Magisk-patched boot was flashed. The value was not decoded (the ABL just prints `Reboot reason is 0x%x`).
- The ABL turns PON into `powerup_reason=` (`HRST|KEYPAD|RTC|CABLE|SMPL|PON1|USB_CHG|WALL_CHG`). It also has a path
  `Crash reboot, PWR_ON_BY_SMPL` (string in `abl_a.efi` at 0x555e0). Every boot here took `PWR_ON_BY_HRST` →
  `powerup_reason=HRST` (cable bit ignored), and the kernel echoes `powerup_reason_setup: poweron_reason= HRST`.

---

## 2. What the ABL logs about itself (measured, boot #8 l.256-399; identical on all boots except where noted)

| Item | Logged value | Source read |
|---|---|---|
| Init stage | `IMemBaseAddr: 0xC125000`, `SMEMPonReason: 0x1/0x41`, `User Version: 1` | SMEM, IMEM |
| dbgflags / devflags | `dbgflags num 7`, `devflags num 3`; `flag` partition +0x600,+0x604,+0x608,+0x60C,+0x610 and +0x40C,+0x414,+0x41C all `0x0` → `Flag bitmap 0x0`, `Setted flags num is 0` | `flag` partition |
| **flag +0x40C today** | golden `lun0/flag.bin` +0x40C = `44 33 22 11` (0x11223344); the other seven are 0 | **Changed after 2026-08-11.** Meaning unknown. **Inferred:** a Hisense marker such as the kernel `rooted:` driver (`Mark root`, `Read fastboot flag`). The next ABL boot then has `Setted flags num ≥ 1` and adds `flag_cmdline=`. Read-only check: `/proc/cmdline` |
| Debug flag | `Read partition=diag Offset=0x450 Size=92` → `Debug flag In Diag: 0` | `diag` |
| MEID | `diag` +0x0 (256 B) → `MEID exsit in eMMC` → `androidboot.meid=1` (presence flag only) | `diag` |
| Arrest recovery | `diag` +0x119C → `0` → `androidboot.arrest_recovery=0` | `diag` (zeros) |
| Lock diag flag | `diag` +0x11F8 → `1` | bytes `lock\0` |
| Encrypt tag | `diag` +0xC38 → `1` → `androidboot.encrypt=1` | bytes `encrypt\0` |
| E Ink VCOM | `diag` +0x12B0 (8 B) → `2730` → `androidboot.eink_vcom=2730` | ASCII `2730` |
| Board ID | `board_id_adc=638/639` → `Board id = 2` → `androidboot.boardid=2` | PMIC ADC |
| Mode | `ToFastboot=0 ToRecovery=0 AlarmBoot=0` → `androidboot.mode=normal`, `diagfacmode=0`. Other modes in the binary: `charger`, `recovery recoverytype=`, `silence`, `factory2`, `tfupdate`, `alarmboot=true` | |
| Charger | `Battery voltage = 4365…4477 mV`, `BATT_ID_2 ≈ 98.8k`, `Charging source not detected` (boots 2-5), `Good To Boot To HLOS … gThresholdVbatt = 3450` | |
| Slot / images | `Multi Slot boot is supported`; loads `vbmeta_x`, `vbmeta_system_x`, `boot_x` (190 ms), `dtbo_x` (49 ms); `Apply Overlay 244 ms` | |
| AVB | `VB2: Authenticate complete! boot state is: orange`. Boot #8: boot hash mismatch, still booted (orange) | |
| Logo | `EPD_DisplayLogo index = 2`, `PanelWidth:448, PanelHeight:829`, backlight ICs `aw99703 bl-1/2` present, `ktd3133 bl-3/4 … not available` | `logo_x` (10 images, 0x16b000 B each from +0x1000. **Inferred:** index 2 = Hisense boot logo, one = "FastBoot Mode", others charging icons) |
| VAB / snapshot | No `MergeStatus` line was printed, though the binary has `read virtualab MergeStatus:%x`. Fastboot was not entered in any log | |
| Haptics | `aw8624_device_init done`, boot buzz `i2c_haptics_enable … ram mode` | |

**Cmdline the ABL adds** (`Add … to cmdline`): `androidboot.meid=1 powerup_reason=HRST androidboot.mode=normal
androidboot.encrypt=1 androidboot.arrest_recovery=0 androidboot.diagfacmode=0 androidboot.boardid=2
androidboot.eink_vcom=2730`. Plus an empty item (`Add   to cmdline`), the ABL's `flag_cmdline`, which is empty
because no flags are set.

**Kernel command line** (measured, l.479, cut off at 969 characters by printk): `rcupdate.rcu_expedited=1
rcu_nocbs=0-7 kpti=off console=ttyMSM0,115200n8 earlycon=msm_geni_serial,0x4a90000 androidboot.hardware=qcom
androidboot.console=ttyMSM0 androidboot.memcg=1 lpm_levels.sleep_disabled=1 video=vfb:640x400,bpp=32,memsize=3072000
msm_rtb.filter=0x237 service_locator.enable=1 swiotlb=2048 loop.max_part=7 buildvariant=user
androidboot.verifiedbootstate=orange androidboot.keymaster=1 androidboot.vbmeta.{device=PARTUUID=…,avb_version=1.0,
device_state=unlocked,hash_alg=sha256,size=7424,digest=…,invalidate_on_error=yes} androidboot.veritymode=enforcing
androidboot.bootdevice=4804000.ufshc androidboot.fstab_suffix=default androidboot.boot_devices=soc/4804000.ufshc
androidboot.serialno=<serial> androidboot.meid=1 powerup_reason=HRST an…`. vbmeta PARTUUID `9efc742b…` = slot a,
`94b58fa9…` = slot b.

---

## 3. E Ink bring-up as logged (measured)

| Stage | Lines |
|---|---|
| UEFI DisplayDxe (l.194-209) | `Resolution 448x829 (1 intf)`, `MDPPLATFOTM_CONFIG_EPD_TC358767_INIT`, `Read partition=diag Offset=0x12B0 Size=8` → `vcom 2730`, `[fp9929a] vcom_reg_value= 126`, `[fp9929a] TMST_VALUE= 29…31` (panel temperature, °C), `Epd Pmic Status = 1`, `[tc358767] ChipID= 0x6601`, `Retry reg = 13C` |
| UEFI VCOM write | **`[fp9929a] Failed to Write reg = 0x2` / `Failed to Write Vcom reg num=0, ret=22` on boots 1-7.** Not on boot 8. The kernel writes it successfully later |
| Kernel early | `eink_vcom_setup: Eink vcom from UEFI is 2730` (from the cmdline) |
| Kernel DSI (~1.44 s) | `[Eink epd 824x1648] fallback to default te-pin-select`, `failed get reset gpio, rc=-2`, `Successfully bind display panel 'qcom,mdss_dsi_epd_eink_fhdhs_video'`; later `hactive= 448,vactive= 829,fps=85` |
| Kernel PMIC (~1.47 s) | `tc358767_probe, epd`, `fp9929a_probe: get vcom value 2730`, `fp9929a_set_register: epd write vcom num=0, vcom =2730`, `fp9929a_probe: epd read temperature =30` |
| Panel off | `dsi_panel_pre_disable … power_off`, `epd fp9929a_sleep_mode start`, `enter sleep end, state=1` |

**Waveform:** no log line loads a waveform. The kernel has no waveform loader: its only flash-ID table belongs to the
Chipone touch `CTS-Flash` (Winbond/GD/MX parts), and `miss ram get f0 waveform` is the haptics driver. The kernel
creates no `/dev/epd_flash` and no vendor module provides it. This agrees with
[epd-waveform-source.md](epd-waveform-source.md).

**New finding: an E Ink WBF file ships in `/vendor/firmware`** (measured; live `vendor_a` and package
`super_5.img`):

- `/vendor/firmware/wf_6_13inch.bin`, inode 1030, **209,043 B**, sha256 `cb8e3cf8…c9f6`. The size field at +4
  (`0x00033093`) equals the file length, which is the standard `.wbf` header.
- Embedded ID string: **`400_B033_HC2201_ED061KC1U2_VB3300-FGA_TC`**. ED061KC1 is a 6.1" E Ink panel, which
  matches the A9.
- **No binary names it** (searched for the plain text `wf_6_13inch` and `wf_` in the kernel and the whole vendor image).
  It also shares no 16-byte window with the composer's tables.
- **Inferred:** this is the panel vendor's source waveform. Hisense's LUTs in the composer were probably generated
  from it offline, and the file is unused at runtime. It is still the best reference for decoding the composer
  tables or building our own.
- Copy kept in `scratchpad/kdebug/wf/`.

---

## 4. Hisense kernel logging facilities (measured, kernel strings + kmsg)

Reserved memory (kmsg l.461, 762-769, 1553, 571-577):

| Region | Base / size | Driver | Purpose |
|---|---|---|---|
| bootlog | `0xb0000000` / 0x80000 (2 × 0x40000) | `bootloader_log` → `/proc/debug_control/bootloader_log` | XBL log (first half) and UEFI/ABL log (second half). Reading it prints `bootlog: The First/second bootlog print complete` to kmsg |
| subsys_report | `0xb0080000` / 0x100000 | `subsys-err-report` | Panic and subsystem crash records → `kdebug` partition + `/sys/debug_control/abnormal_stats/{data_ready,last_abnormal,reboot_stats,clear_stats}` |
| rs_recorder | `0xb0180000` / 0x400000 | `drivers/soc/hisense/rs_recorder/` | On panic: dmesg, all-ps, irqs, slabinfo, zipped into `/kdebuginfo/rs_recorder/`; reboot reason → `/kdebuginfo/rs_recorder/reboot_reason` |
| ramoops | `0xb0580000` / 0x100000 | pstore | console 0x40000, pmsg 0x40000. Empty in practice (no warm reset) |

Other facilities: `his_register_sysfs_attr` registers `boot_time_stats`, `wlan_ftm`, `hs_cam_otp`,
`type_c_disable_otg`, `host_mode`, `show_active_ws`, `phone_call_stats`, `charge_time_recorder`, `pon_off_reason`,
`modem_restart` under `/sys/debug_control/`. `debug_flag_control_init` exposes `debug_flag` (`reset_level_debug_set`,
`hs_set_pm_pon_resin: set resin debug to 0`). `hisense_debug_ctrl_init` exposes `serial_console_ctrl`, `hs_disable_avb`,
`hs_disable_wp`, `hs_nonblock_bl`, `print_active_ws`, `reboot_time`. `rooted:` reads the unlock and fastboot flags.
`HSINFO:` / `HSERR:` are printk prefixes on Hisense-instrumented messages (PM, charger, backlight, Wi-Fi, ext4 errors).

**`/proc/kexmlog` (EX-MON, `exmlog_read`)** is a **blocking** reader that returns **only `HSINFO`/`HSERR` lines**.
Measured: in boot #2 every one of the 86 lines after the dmesg section carries one of these tags.

**Who writes `kmonlog`** (measured from stock `system`, `super_2.img` 0x30bab270-0x30bab650 and 0x39a7a01d):

```
service kernel_mond /system/bin/kernel_mond     # system-side, class main, user root, group system media_rw, oneshot
on fs: setprop vendor.kdebuginfo.mountfs 1      # system init.rc → vendor init.target.rc:107-118 mounts sda9 at /kdebuginfo
```

`kernel_mond` (log tag `KERNEL_MON`) waits for `/kdebuginfo` and picks the directory `kmon_log/kexmlog_0..9` by
bitmap, deleting the oldest when space runs out. It creates `kmonlog_%4d_%02d%02d_%02d%02d%02d` and runs
`cat /proc/debug_control/bootloader_log >>`, then `dmesg >>`, then `cat /proc/kexmlog >>` until shutdown. It also runs
`dumpsys cpuinfo >> /kdebuginfo/powerlog/cpuinfo.txt` and `dumpsys batterystats >> …/batterystat.txt`. This explains
the file layout:

| Lines | Content |
|---|---|
| 1-~400 | XBL log, then UEFI/ABL. **The XBL part is a wrapped ring:** it starts mid-stream at `QHEE Image Loaded, Delta`. Read from `PBL, End` / `SBL1, Start` onward first, then lines 1-14 |
| ~401-~2800 | dmesg up to about 15 s (the `cat` of the bootlog shows at 15.08-15.89 s) |
| rest | the `HSINFO`/`HSERR` stream until reboot |

The log length runs **29-745 s**, not a fixed ~100 s. It ends where the boot ended.

The vendor side also has `crash_mond` (`/vendor/bin/crash_mond`, late_start, oneshot). It waits for
`/sys/debug_control/abnormal_stats/data_ready` and saves `last_kmsg` and `console-ramoops-0` into
`/kdebuginfo/rs_recorder/<date>`. `rs_recorder/` and `haptic/` are empty in the dump.

**Under the GSI** (measured: the same Dec 2022 kernel in golden `boot_a`/`boot_b`, which contains `kexmlog` and
`/kdebuginfo/power_reason.txt`):

- The kernel facilities are all still present.
- `kdebug` (raw) is still written: §5.2 has 2026-10-11 records.
- Nothing mounts `kdebuginfo`, because `on fs setprop vendor.kdebuginfo.mountfs 1` lived in the stock system
  `init.rc`. So no kmonlog, `power_reason.txt` or rs_recorder output appears.

**Replacement logger design (inferred, untested):** a Magisk `service.d` script would:

1. mount `/dev/block/by-name/kdebuginfo` (ext4, never format it);
2. `cat /proc/debug_control/bootloader_log`, then `dmesg`, into a new per-boot file;
3. follow with `dmesg -w`, which is a superset of `cat /proc/kexmlog`; use kexmlog only if a lean log is wanted;
4. add `/proc/cmdline` and the PON lines, and rotate 10 files.

`power_reason.txt` and rs_recorder hard-code the path `/kdebuginfo`, which the GSI root does not have. They only
work if that path exists.

Read-only checks to run live first: `ls /proc/kexmlog /proc/debug_control/`, `ls /sys/debug_control/`.

---

## 5. `power_reason.txt` and `kdebug`

### 5.1 `power_reason.txt` (measured, 696 B, 6 lines)

Format, written by the kernel (`HS_PON`, format `"%s    OFF: %s    ON: %s"`, time `%d-%02d-%02d_%02d:%02d:%02d` UTC):

```
2022-07-12_15:31:11    OFF: Triggered from SOFT (Software)    ON: Triggered from SMPL (Sudden Momentary Power Loss)
2026-08-11_01:35:33    OFF: …SOFT…    ON: …SMPL…        (and 01:38:18, 01:49:48, 02:06:40, 02:50:55)
```

| power_reason line | kmonlog created | Δ | Boot |
|---|---|---|---|
| 2022-07-12_15:31:11 | 1970 (RTC unset; the ext4 root dir mtime is also 2022-07-12 15:31, the build-date clock) | | #1 |
| 01:35:33 | 01:34:54 | 39 s | #2 |
| (none) | 01:36:40, last write 01:37:12 | the boot lasted only about 32 s | #3 |
| 01:38:18 | 01:37:36 | 42 s | #4 |
| 01:49:48 | 01:49:12 | 36 s | #5 |
| 02:06:40 | 02:06:01 | 39 s | #6 |
| (none) | 02:19:10, last write 02:19:26 | short boot | #7 |
| 02:50:55 | 02:50:15 | 40 s | #8 |

The kernel writes one line about 36-42 s after kmonlog starts. Boots shorter than that leave no line.

**The SMPL claim holds** (measured): all six lines say `ON: SMPL`. For the same boots, XBL, ABL and the kernel's own
qpnp driver show HARD_RESET (SID0, 0x01/0x41) or CBL (SID2), with **no SMPL bit**. The strings are the qpnp-power-on
tables (index 0 = "Hard Reset", 1 = "SMPL"; POFF index 0 = "SOFT", 1 = "PS_HOLD").

**Inferred cause:** `HS_PON` stores a 1-based bit index (`ffs()`) and looks it up in the 0-based table. HARD_RESET
(bit 0 → 1) then prints as SMPL. The OFF column always shows table entry 0 (`SOFT`), which suggests 0, i.e. no reason
stored. The disassembly was not done. Use the kernel's `PMIC@SIDn Power-on reason` lines or `/sys/debug_control/pon_off_reason`
instead.

### 5.2 `kdebug` raw partition (measured, `lun0/kdebug.bin`)

| Offset | Content |
|---|---|
| 0x0-0x1000 | zero |
| 0x1000-0x500000 | `0xff` with single bits cleared (`7f`, `df`, `ef`, …), uniform density. Unknown; not text |
| 0x500000 | magic `FFOP` (u32 `0x504f4646`), u32 `0x00010003`, then sparse u32 counters: [3]=1, [4]=534, [8]=8, [11]=7, [12]=17, [36]=559. **Inferred:** lifetime PON/POFF stats (`PON Stats:`/`POFF Stats:`) |
| 0x500408 | ring index 9, then 10 × {u32 1, char[20] time}: 2026-10-11 17:06:24 … 20:00:01 UTC. **Written under the GSI on the day of the golden dump** |
| 0x800000 | exception records (`ac ac 53 53` "SS" magic + tag): `RESIN 1970-01-01_00:26:05 Abnormal resin or combo key occurs: status=0x42`; `kernel 1970-01-02_15:24:15 init(PID:1)Attempted to kill init! exitcode=0x00007f00` (a panic: init exited 127, probably an early port); `UNKNOWN 2026-08-13_22:30:13 Reset reason is unknown: status=0x242` (2026-08-13 was the bisect day) |

`kdebug` is the one persistent record that survives on the GSI with no setup. Decoding its ring is the cheapest way
to get boot history (the read path is an EDL dump or `dd` as root).

---

## 6. `/edpd/bitmap_low.raw` = stock "DEVICE POWERED OFF" screen (measured)

- 2,715,904 B = 824 × 1648 × 2. It decodes cleanly as **RGB565 little-endian** (109 distinct values, 98 % `0xFFFF`).
  It shows a white screen with the black **Hisense** logo centred and `⏻ DEVICE POWERED OFF` at the bottom. A few
  stray pixels sit in column 823.
- sha256 `3427d03a…a5b0`. Owner uid 1000, mode 0600. Last written 2026-08-11 02:05:32 UTC, **the same second** boot
  #5's kmonlog stopped, so it is written at shutdown.
- Writers and readers (measured strings, stock system): the framework
  (`new thread commitFbBufferLocked for bitmap_low.raw`, services string pool) and the offline charger
  (`/kdebuginfo/edpd/bitmap_low.raw` next to `charger: [%ld] shutting down`). The vendor rc creates `/kdebuginfo/edpd`
  plus `backlight_flag`. The composer has read access to `kdebuginfo_data_file` (`vendor_sepolicy.cil`).
- **Inferred:** system_server renders the power-off frame here, and it is pushed to the panel as the last image before
  power-off. The GSI does neither, so after a GSI shutdown the panel keeps whatever was last on it.

PNG: `$A9_DATA/assets/stock-aod/edpd-bitmap-low-poweroff.png` (18,824 B). Scratch copies of all four decodes are in
`scratchpad/kdebug/png/`. The asset folder's `README.md` was not edited.

---

## Reproduction

```sh
D=$A9_DATA/Backups/edl_2026-10-11_132444/lun0; S=<scratch>
debugfs -c -R "rdump / $S" $D/kdebuginfo.bin          # ownership warnings are harmless
TZ=UTC debugfs -c -R "stat /edpd/bitmap_low.raw" $D/kdebuginfo.bin
lpunpack -p vendor_a $D/super.bin $S/super             # vendor_a.img
debugfs -c -R "dump /firmware/wf_6_13inch.bin $S/wf.bin" $S/super/vendor_a.img
# kernel: gunzip the boot image's kernel (page 4096, header v2), then search strings for kexmlog/HS_PON/rs-recorder
```
