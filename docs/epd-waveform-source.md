# Where the A9's E Ink waveform comes from

**Date:** 2026-10-13. **Status:** answered offline (static analysis only, no phone access). A read-only
live check that would confirm it is in §6. Measured facts say so; inferences are labelled.

## Answer

**The waveform is compiled into the userspace binaries. It is not read from the panel, a partition or a file.**

- **Android:** `vendor.qti.hardware.display.composer-service` carries temperature-binned LUTs in its
  `.rodata` (14 temperature bins, one table per waveform type). `lookUpTable()` indexes them by a temperature
  bin computed from two sysfs thermistors.
- **Stock recovery:** `librecovery_ui.so` carries two fixed 256×38 LUTs in `.rodata`, with no temperature
  compensation.
- **`/dev/epd_flash` is vestigial.** No A9 kernel has a driver that creates it. The DT has no NOR node for the
  panel. The live composer never names it. In recovery, `ReadEpdFlash()` exists but nothing calls it.
- **`UseDefaultFlg=0` tells us nothing.** It is a `.bss` int that nothing ever writes.

This differs from the Hisense A6L, where the HWC reads 0x70080 bytes from the panel's SPI NOR. The A9 recovery
library still has the A6L-style plumbing (a 0x70080 buffer and the `info` struct). But its `Init_Eink_SWTcon`
reads only three arguments and ignores that buffer.

---

## 1. Stock recovery: `librecovery_ui.so`

The file is `work-epd/librecovery_ui.so`, sha256 `2b187fca…9cdb`. It is byte-identical to
`system/lib64/librecovery_ui.so` in the device's own `recovery_b`, taken from
`$A9_DATA/Backups/2026-08-08_055553/recovery_b.img`.

Tools: capstone and pyelftools disassembly, with `llvm-readelf -r` for the APS2-packed GOT relocations.
Scripts are in `scratchpad/wftools/` (`dis.py`, `lowent.py`). Addresses are vaddrs.

### 1.1 The `/dev/epd_flash` reader is dead code (measured)

- `ReadEpdFlash(void*, unsigned long)` at `0x39bd4` does `open("/dev/epd_flash")` and then
  `__read_chk(buf, len)`. On failure it returns 0.
- Across the whole `.text` (`0x28000-0x3d104`) the only reference to the string is that function. **Nothing
  calls `ReadEpdFlash`:** there is no `bl`, PLT or `.cfi` reference.
- `system/bin/recovery` in the same ramdisk imports **no** EPD symbols. Its `nm -D --undefined-only` shows no
  `ReadEpdFlash`, `GetEpdTopTemp`, `SetEpdVcom`, `SaveEpdInfo` or `InitEpd`. `SetEpdVcom`, `GetEpdTopTemp`,
  `GetEpdBottomTemp` and `SaveEpdInfo` are also never called inside the library.

### 1.2 The A6L-style `Init_Eink_SWTcon` call, and why it is a no-op here (measured)

`EinkSwTconInit()` at `0x3a168` does the following:

```
0x3a264  malloc(0x70080)                ; same size as the A6L SPI-NOR waveform window
0x3a280  memset(buf, 0, 0x70080)        ; zero-filled, nothing ever writes it
0x3a288  x5 = &epd_flash_info           ; 196-byte .bss struct
0x3a2a4  Init_Eink_SWTcon(x0=DPI_UpdateBuffer, w1=8, x2=&cfg{d0@0x1a7b0, 0x32},
                          x3=buf, w4=0x70080, x5=&epd_flash_info)
```

So the caller still uses the A6L's 6-argument ABI. But `Init_Eink_SWTcon` at `0x3c2f4-0x3c854` reads only
`x0`, `w1` and `x2`. A register scan of its body finds **no** reads of `x3`/`w3`, `x4`/`w4` or `x5`/`w5`.
This explains the "3 args vs 6" note in `a6l-harvest-2026-10-13.md` §3.

Inside, it zeroes the six `.bss` buffers `LONG_WF_BUF`, `_DU2`, `_Video`, `_GLD16`, `_A2_Flash` and `_Initial`,
each `0x960000` bytes. Nothing in the library ever writes them afterwards. **Inferred:** they are leftovers of
the A6L-style "generate tables from the flash file" design.

### 1.3 `UseDefaultFlg` (measured)

- `EpdUnblank` at `0x3b44c` prints `UseDefaultFlg` from `epd_flash_info+0xc0`.
- `epd_flash_info` has three references: `0x3a0ac` (SaveEpdInfo, a load), `0x3a288` (passed to Init, ignored)
  and `0x3b440` (a load). None of them stores to it, so the value is always the `.bss` zero.
- `SaveEpdInfo` (`0x3a01c`) would write `waveform=%d\nversion…\nvcom…` to `/sys/devices/virtual/graphics/fb0/epd_info`,
  with `waveform = (UseDefaultFlg == 0)`. It is never called.
- The kernel's `epd_info` node shows `waveform=0`, which is a static string in the kernel (§3).

### 1.4 The built-in waveform (measured)

`.rodata` (`0x17680-0x1f854`) holds a region whose bytes are only `0`, `1` and `2`. It runs from `0x1a89c` to
`0x1f49c` and is two tables of exactly 256 × 38 bytes (`0x2600`) each:

| Table | Range | Rows with any drive | Meaning (row = `old<<4 \| new`, 38 frames, code 0 = none, 1 / 2 = the two polarities) |
|---|---|---|---|
| A | `0x1a89c-0x1ce9c` | 256/256 | Every transition is driven, including old == new. **Inferred:** a full or flashing GC16 |
| B | `0x1ce9c-0x1f49c` | 240/256 | The 16 rows with old == new are blank. **Inferred:** partial GC16 |

`Update_lookUp_table` (`0x3c8fc`) reads them through `adrp 0x1a000 + 0x874` / `+0x89c` and `adrp 0x1c000 + 0xe9c`.
`Update_epd_Image` (`0x3d3ec`) tail-calls it with the transport geometry (`0x1c0`, `0x33d`) = 448 × 829.
The last driven frame index is 35 in both tables. That fits the `epd SwTconThread out frame: 38` lines in the
stock recovery log.

**Inferred:** the first `162` frames are the built-in init/clear sequence. As on the A6L, that sequence does not
depend on the waveform. There is one table set and no temperature input: none of the temperature getters are
called.

## 2. Android: the composer HAL

| Copy | sha256 | Size | `.rodata` |
|---|---|---|---|
| Stock package `HLTE556N/super_5.img` = `vendor_a` (verified from the LP metadata in `super_1.img`: `vendor_a` extent at super byte 4,880,072,704, 1,154,555,904 bytes = exactly `super_5.img`; read with `debugfs -c`) | `17f9cf41…` | 1,943,592 | `0x114684` |
| Live device copy (`~/source/a9-display-evidence-20261012/vendor/`, recorded as equal to `/vendor` on the phone) | `8c89557b…` | 2,049,912 | `0x12ec54` |

The two copies differ, the same pattern as the kernel in CLAUDE.md: the device runs a newer vendor than the
package.

**Both copies (measured):**

- Neither contains the string `epd_flash`, `.wbf`, `waveform` or any E Ink panel or waveform ID. A regex for
  `E[CDS]nnn…`, `AFDnnn` or `_Rnnn_` found nothing; on the A6L the flash file yields `ED058TC7U2` and
  `320_R301_AFD521_…`.
- The `.gnu_debugdata` symbols contain no `ReadEpdFlash` and no `Init_Eink_SWTcon`. The TCON lives inside
  `sdm::HWCDisplay::EinkSwTconThreadHandler` and `lookUpTable` (live `0x1d1690`, package `0x1b7648`).
- The only EPD inputs the binaries name are sysfs nodes: `…/in_temp_epd_top_therm_input` (the PMI632 ADC
  thermistor), `…/dsi-display-primary/epd_thermal` (kernel; the strings point to the fp9929a PMIC's
  temperature register), `epd_info` and `epd_xon_en`.

**The tables (measured):** an earlier static RE pass (`STATIC-RE-COMPOSER-20261012.md`, live copy) found
`lookUpTable` reading tables in `.rodata`. The layout is `[len][frames…]` per row, 257 rows per bin and
14 temperature bins. The bin is `byte_table@0x2b0d0[(2*t1+t2)/3]`, where t1 and t2 are the two temperatures.
That pass checked the frame lengths against 310/310 logged steady-state updates.

Today's comparison of the two copies:

| Type | Live base | Bin stride | Row-0x0f length, bins 0..13 | Present in the package copy? |
|---|---|---|---|---|
| 3 (Clear) | `0x2b793` | `0x6a6a` | 104 88 76 66 57 48 40 35 30 30 30 30 30 30 | Yes, all 14 bins byte-identical, at `0x2b763` |
| 2 | `0x8895f` | `0x6a6a` | same as type 3 | Yes, identical, at `0x8892f` |
| 1 (Balanced) | `0xe5b2b` | `0x3838` | 54 46 40 35 30 26 22 19 17 15 14 13 12 12 | Yes, identical, at `0xe5afb` |
| 6/8 (Smooth) | `0x116e3b` | `0x1e1e` | 28 25 23 20 17 14 12 10 8 8 8 8 8 8 | Yes, identical, at `0x116e0b` |
| 9 (Speed) | `0x1313df` | `0x1e1e` | 28 … 10 7 7 7 7 7 7 | **No.** 14 × `0x1e1e` = 107,940 B, against a `.rodata` growth of 0x1a5d0 = 107,984 B |

So a vendor update carried a new waveform (Speed) **inside the composer binary**, which is direct evidence
that the binary is where the waveform ships. **Inferred:** the recovery tables are a separate set (38 frames,
no length bytes). Only 6 of their 512 rows occur verbatim in the composer.

**Vendor init and sepolicy (measured):**

- `vendor/etc/init/hw/init.target.rc:78,100` chmods and chowns `/dev/epd_flash`.
- `allow hal_graphics_composer_default epd_flash_device_30_0` is in `vendor_sepolicy.cil`.
- The type is declared in `plat_pub_versioned.cil`, and the `/dev/epd_flash` label is in the recovery ramdisk's
  `system_ext_file_contexts:50`, i.e. in the Hisense *platform* policy shared across their e-ink line.
- `vendor_file_contexts` does not label `/dev/epd_flash` at all.

**Inferred:** these are BSP-wide leftovers that suit the A6L design. On the A9 an `init` `chmod` on a missing
node fails quietly.

## 3. Kernel and DT

Sources: `boot.img` (package) and the device's `recovery_b` (2026-08-08 backup). Both are boot header v2. The
kernels are gzip images (15,050,789 and 15,047,394 bytes); both decompress to 34,476,544 bytes. The DTB and the
recovery_dtbo were split with `scratchpad/wftools/{bootv2.py,fdt.py}`; there was no `dtc`, so a small FDT
walker was used.

**Kernel strings (measured):**

- **0** occurrences of `epd_flash` in either Image, so there is no misc or char device of that name.
- The EPD-related strings are the `msm_drm` panel GPIOs (`epd_xon/xoe/mode/power_on`), the `epd_*` sysfs
  attributes, `fiti,fp9929a` (the PMIC, with temperature and VCOM), `dz,tc358767` and a static `waveform=0`
  (the `epd_info` default).
- There is no SPI-NOR framework string. The only JEDEC and flash table (`CTS-Flash`, GD25/MX25R/W25Q) is
  the Chipone touch-controller firmware updater.
- `miss ram get f0 waveform!` belongs to the Awinic haptic driver.

**DT and DTBO (measured):**

- Sources: `dtbo_b` (backup), `HLTE556N/dtbo.img` and the 88,722-byte `recovery_dtbo`. Their epd and spi
  properties are identical.
- The panel node `qcom,mdss_dsi_epd_eink_fhdhs_video` ("Eink epd 824x1648", 448×829 at 85 Hz, `bl_ctrl_external`)
  has GPIOs only. Peripherals: `fp9929a@48`, `tc358767@0F` and the thermistor `epd_top_therm` (ADC reg `0x54`).
- The only SPI child anywhere is `sunwave,fingerprint`. No node matches `nor`, `mx25`, `w25`, `gd25`, `jedec`,
  `spidev` or `flash` apart from the camera and LED flash.

**Why the node does not appear (inferred from the above):** no driver creates `/dev/epd_flash`, and the DT
describes no panel NOR for it to bind to. The node is not on-demand ueventd behaviour and not a probe failure.
Whether the Carta 1200 FPC even has a NOR chip cannot be settled offline. Even if it has one, nothing reads it.

## 4. Partitions

Scanned in `$A9_DATA/Backups/edl_2026-10-11_132444/` (read-only). The scan looked for 4 KiB blocks made only of
bytes 0, 1 and 2, panel or waveform ID strings, and `.wbf`.

| Partition | Finding |
|---|---|
| `tpf_a`/`tpf_b` (8 MiB, LUN0) | = `HLTE556N/tpf.bin` (51,974 B, byte-identical). 8051-family code (`02 xx xx` LJMP vector table, `32` RETI). **Inferred:** touch-panel firmware, not a waveform |
| `prospecfg`, `reserve`, `ssd`, `dip`, `spunvm`, `splash`, `toolsfv` | All zero |
| `flag` (7 non-zero bytes), `diag` (852), `devinfo` (`ANDROID-BOOT!` header) | No LUT content |
| `databackup` | 3 near-empty blocks, no waveform |
| `persist` | `debugfs ls`: `display/` empty, no EPD files |

No partition holds a waveform. userdata was not touched.

## 5. Consequences

- For QEMU or offline TCON work on the A9 (a6l-harvest §3.6), you need **no waveform capture**. The tables are
  in the binaries you already have.
  - For a recovery-lib emulator, pass anything as the 0x70080 buffer; it is ignored.
  - For composer-faithful output, take the tables from the live composer `.rodata` and feed the temperature bin.
- To change the waveform (for example a faster mode), patch composer `.rodata` or the bin selection. There is
  nothing to flash to a partition or a NOR chip.
- `UseDefaultFlg=0` and `epd_info: waveform=0` say nothing about which waveform is in use.

## 5b. Addendum (2026-10-13): a `.wbf` file does exist in the vendor

Found by the stock-boot-log analysis ([stock-boot-logs.md](stock-boot-logs.md)):
`/vendor/firmware/wf_6_13inch.bin` (209,043 B) has a standard `.wbf` header with
the ID `400_B033_HC2201_ED061KC1U2_VB3300-FGA_TC` (the 6.1" ED061KC1 panel).
Nothing in the composer, `librecovery_ui.so` or the kernel references it, and it
shares no data with the composer's tables, so it is most likely the vendor's
source waveform from which the compiled-in tables were generated, unused at
runtime (inferred). It is the input to use for any offline TCON/QEMU work.

## 6. Live check (read-only) — RUN 2026-10-13, confirms §1 and §3

Result on the phone (A16 v6, slot a): `/dev/epd_flash` absent; nothing EPD in `/proc/misc` or
`/proc/devices`; `/sys/bus/spi/devices/` is **empty** (not even the fingerprint sensor is a
spidev here); composer sha256 `8c89557bf47b57b1…` = the analysed binary; its open fds and
non-`.so` mappings show no waveform file and no flash device (only dmabufs, ashmem, binder,
`/dev/dri/card0`, `/dev/kgsl-3d0`, the backlight `brightness` node); VADC
`in_temp_epd_top_therm_input` = 31819 (31.8 °C) matching the composer's
`epd:: clear=0,temp=27,32,mode=0x206`. `epd_thermal` was deliberately NOT read (reading the
PMIC temperature powers it up, epd-panel-screenshot.md §7).

### Commands used

Run these on the phone as it is (A16, stock vendor, Magisk). Nothing here writes.

```bash
# 1. Node and driver really absent
adb shell 'ls -l /dev/epd_flash; cat /proc/misc | grep -i epd; grep -i epd /proc/devices; ls /sys/bus/spi/devices/; for d in /sys/bus/spi/devices/*; do echo $d $(cat $d/modalias); done'
# 2. Live composer is the analysed binary
adb shell su -c 'sha256sum /vendor/bin/hw/vendor.qti.hardware.display.composer-service'   # expect 8c89557b...
# 3. Composer has no waveform file or device open
adb shell su -c 'p=$(pidof vendor.qti.hardware.display.composer-service); ls -l /proc/$p/fd | grep -v -E "socket|pipe|anon_inode|/dev/(binder|hwbinder|vndbinder|dri|ion|dma_heap|kgsl)"; grep -v "\.so" /proc/$p/maps | grep -E "/(dev|data|vendor|mnt)/" | sort -u'
# 4. The temperature inputs the composer actually uses (reads only)
adb shell 'cat /sys/devices/platform/soc/1c40000.qcom,spmi/spmi-0/spmi0-02/1c40000.qcom,spmi:qcom,pmi632@2:vadc@3100/iio:device0/in_temp_epd_top_therm_input; cat /sys/devices/platform/soc/soc:qcom,dsi-display-primary/epd_thermal'
adb logcat -d | grep -m5 'clear=.*temp='
```

Expected if this doc is right:

1. `No such file`, nothing in `misc` or `devices`, and only the fingerprint SPI device.
2. `8c89557b…`.
3. No `epd_flash` and no `.wbf`/`waveform` path.
4. Two temperatures that match the `temp=t1,t2` logcat line.

Do not write `epd_xon_en` or any other `epd_*` node (a6l-harvest §3.10).

## Reproduction

The scripts live in `scratchpad/wftools/`, which is not durable. Re-create them from this description if
needed.

- `dis.py <so> <sym|0xaddr:0xsize>…`, with `REL=<llvm-readelf -rW output>`: capstone disassembly with
  string, PLT and GOT annotation.
- `lp.py super_1.img`: dumps the LP extents.
- `bootv2.py <img> <out>`: splits the kernel, ramdisk, recovery_dtbo and dtb.
- `fdt.py <blob> <regex>`: FDT property grep.
- `lowent.py <file> lo hi`: finds `{0,1,2}` runs.

Low-entropy map of the recovery `.rodata` (one line of Python):

```python
d=open('librecovery_ui.so','rb').read()
for a in range(0x17680,0x1f854,0x200): print(hex(a), sum(b<3 for b in d[a:a+0x200]))
```

Vendor extraction: `debugfs -c -R 'rdump / vend' $A9_DATA/HLTE556N/super_5.img`.
