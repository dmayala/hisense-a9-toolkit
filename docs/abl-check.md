# abl-check: the phone's own bootloader checks, offline

Dated 2026-10-14. **Status: working.** `scripts/abl-check.sh` runs three stages of the phone's ABL
(LinuxLoader) image handling in Unicorn: header validation, DTBO location/validation, and DT
selection + overlay. All 9 real images pass; all 19 negative controls fail at the expected stage with
the ABL's own error message. No phone access was used or is needed.
Labels: **MEASURED** = from the files and the emulation, with the command; **INFERRED** = deduced.

## Use

```
scripts/abl-check.sh <image> recovery|boot [--dtbo dtbo.img] [--json] [--quiet] [--save-dtb merged.dtb]
```

Exit 0 = all stages PASS, 1 = a stage FAILED, 2 = usage or harness error (missing file, ABL is not the
pinned build, a call outside the reviewed code). About 2 s per image plus nix-shell start-up.
`scripts/flash-recovery.sh` runs it automatically before writing a recovery.

Defaults: ABL = golden `lun4/abl_a.bin` (`edl_2026-10-14_110759`); for `boot`, the dtbo partition =
golden `lun4/dtbo_a.bin`. `--save-dtb` writes the DTB the ABL would hand the kernel.

Example (the current TWRP):

```
  0 file     sections inside the file (host)       PASS
  1 header   CheckImageHeader 0x132a0              PASS    rc=0x0 EFI_SUCCESS, ImageSizeActual=41816064, PageSize=4096
  2 dtbo     LoadAndValidateDtboImg 0x23d78        PASS    rc=1, image+0x2729000, 1 entry
  3 dtb      GetSocDtb/GetBoardDtb/ufdt overlay    PASS    merged 416441 bytes, model "Qualcomm Technologies, Inc. Bengal QRD"
  RESULT: PASS
```

Tests: `tests/abl-check-tests.sh` (about 25 s, needs nix-shell for unicorn; reads about 400 MB of
images from `$A9_DATA`, writes nothing there). It is separate from `tests/run-tests.sh`, which stubs
abl-check through `A9_ABL_CHECK`.

## What a PASS means, and what it does not

PASS means the ABL code that would have rejected TWRP builds 1-2 accepts the image. The checked code
covers the header limits, recovery_dtbo presence, location and size, the DTBO table, and that the SoC
DTB and board overlay select and merge for this board. It does **not** prove that the kernel boots.
Not emulated:

* AVB / libavb (`LoadImageAndAuthVB2` around it). On the phone, AVB is in the orange state with
  vbmeta flags = 3 (MEASURED: golden `lun4/vbmeta_a.bin` offset 120; kmonlog "VB2: boot state:
  orange").
* Partition reads. The image is placed in a buffer the size of the partition (96 MiB), padded with
  zeros. **INFERRED:** with verification disabled, libavb loads the whole partition, so the ABL's
  ImageSize = partition size. The host-side stage 0 adds the stricter check that every section lies
  inside the file. Otherwise the partition tail, stale bytes from an earlier flash, is what gets read.
* The BootLinux glue arithmetic that computes the DTB offsets (redone on the host from the
  disassembly, see below). Also hypervisor DTBO overlays: none on this phone (kmonlog "VM Hyp calls
  not present").
* Kernel decompression, DT fixups (memory, cmdline, serial), cmdline building, and the kernel itself.
* The TrustZone call in the RTIC handler (0x25488; see host fixtures).
* Header v3/v4 (vendor_boot). Only the rejection of a v3 header without vendor_boot is tested.

## The ABL being run

MEASURED. Golden `lun4/abl_a.bin`: ELF with one UEFI FV at file offset 0x3000 (`_FVH` at 0x3028). It
holds an LZMA section at 0x3078 (`lzma.FORMAT_ALONE`) that decompresses to 491,720 bytes, with a PE32+
(AArch64) at 0xb8 of that: **LinuxLoader, 491,536 bytes, sha256 `1dd55dcb…6ab4dab`**. The script
regenerates it every run and refuses any other hash, because the RVAs below belong to this build.
The package `HLTE556N/abl.elf` is an older build (Jul 2022). It is refused, and that refusal is one
of the tests.

PE layout: ImageBase 0, `.text` 0x1000-0x5a000, `.data` 0x5a000-0x77000, `.reloc` 0x77000 (699 DIR64
fixups). The harness relocates it to 0x40000000, so page 0 stays unmapped and a NULL dereference faults.

## Routines emulated and how they were found

All found by capstone disassembly of the PE and by string cross-references (`adrp`+`add` pairs).
The strings come from Qualcomm's QcomModulePkg (BootLib/BootLinux.c, UpdateDeviceTree.c, Board.c),
whose CAF source this build follows closely.

| Stage | RVA | Function | Found by |
|---|---|---|---|
| 1 | 0x132a0 | CheckImageHeader(hdr, 0x1000, vendor_hdr, vendor_size, &ImageSizeActual, &PageSize, BootIntoRecovery) | `"ANDROID!"`, "Invalid boot image header", "Recovery Dtbo Size too big %x, Allowed size %x"; caller 0x60e8 in LoadImageAndAuthVB2 passes `w1=0x1000`, `w6=Info->BootIntoRecovery` |
| 2 | 0x23d78 | LoadAndValidateDtboImg(Info, BootParamlist) -> BOOLEAN | "Invalid recovery dtbo: RecoveryDtboOffset=%u…", "Dtbo hdr magic mismatch"; called at 0x12674 |
| 2 | 0x131a8 | GetImage(Info, &buf, &size, name) | "GetImage: invalid parameters"; looked up "dtbo" for boot images |
| 3 | 0x156b8 | GetSocDtb(ImageBuffer, DtbOffset+dtb_size, DtbOffset, …) | "No match found for Soc Dtb type", "Exact DTB match found. DTBO search is not required" |
| 3 | 0x15950 | ReadDtbFindMatch (qcom,msm-id / board-id / pmic-id) | "qcom,msm-id" |
| 3 | 0x16678 | GetBoardDtb(Info, DtboImgBuffer) | "Dtbo Img buffer is NULL", "Dtbo count = %u LocalBoardDtMatch = %x" |
| 3 | 0x17228 / 0x172d8 | AppendToDtList / DeleteDtList | "DTs list: NULL" |
| 3 | 0x2cc30 | ufdt arena init (AllocateZeroPool 0x500000) | called at 0x127b0 before the overlay |
| 3 | 0x28260 | ufdt_install_blob | string "ufdt_install_blob" |
| 3 | 0x28bd8 | ufdt_apply_multioverlay(blob, size, list) | "ufdt_apply_multi_overlay" |
| 3 (no DTBO) | 0x140d8 | DeviceTreeAppended(ImageBuffer, end, DtbOffset, DtLoadAddr) | "DTB offset is incorrect, kernel image does not have appended DTB"; called at 0x1283c when stage 2 returns FALSE |
| 3 (no DTBO) | 0x40560 | fdt_check_header, on ImageBuffer + PageSize + DtbOffset (BootLinux 0x128fc) | last resort before "Error: Device Tree blob not found" (0x12944) |

Structure offsets used (MEASURED from the disassembly):

* **BootInfo:** +0 MultiSlotBoot, +1 BootIntoRecovery, +0x128 loaded-image table of
  {name*, buffer, size} (count at +0x428; GetImage), +0x458 HeaderVersion.
* **BootParamlist:** +8 ImageBuffer, +0x10 ImageSize, +0x18 DtboImgBuffer (out), +0x5c PageSize.
* **Globals:** gBS 0x5fb98, BoardInfo 0x5fbd4, the "DTBO not needed" flag 0x5fc28, SafeStack
  0x6ecb0/0x6ecb8, stack cookie 0x5a0c0.

The BootLinux glue that the host redoes, from 0x11a20-0x11ae0 and 0x12500-0x12574 (MEASURED):
locals [x23-0x98] = ImageBuffer, [x23-0x44] = page_size, [x23-0x40/-0x3c/-0x38] = kernel, second
and ramdisk sizes. For v2, DtbOffset = page × (1 + pages(kernel) + pages(ramdisk) + pages(second) +
pages(recovery_dtbo)), and end = DtbOffset + dtb_size.

## Harness design ("fail loudly")

* **Only `.text` is executable.** Headers, `.data`, the stacks, the heap and the image buffers are
  mapped read/write without execute. Page 0 is unmapped.
* **Firmware = gBS only.** Every one of the 64 EFI_BOOT_SERVICES slots points at its own trap.
  AllocatePool (+0x40), FreePool (+0x48), CopyMem (+0x160) and SetMem (+0x168) are implemented. Any
  other slot stops the run, which is how LocateProtocol (+0x140) in the RTIC handler was found.
* **Reviewed code allowlist.** Function ranges are [BL target, next BL target), plus the boundaries of
  allowlist entries. Code that is not on the reviewed list (`ALLOWED_TEXT` in the script, 151
  entries out of the 1,011 BL targets, each named) carries a block hook that stops the run. Allowed code runs without Python
  callbacks: 2 s per image, against 108 s with a hook on every block.
  * Each entry was checked in the disassembly for `blr`/`br`, `svc`/`hvc`/`smc`, `msr`/`mrs` and
    loads of firmware tables. Entries are BaseLib, BaseMemoryLib, SafeString, libfdt, libufdt,
    MemoryAllocationLib (firmware reached only through the trapped gBS), Board.c getters (they read
    the BoardInfo fixture), and the routines in the table above.
  * The `blr` sites inside allowed code are gBS CopyMem/AllocatePool/FreePool (trapped) and qsort
    0x29100. qsort calls libufdt comparators inside `.text`.
  * CpuDeadLoop (0x2058) stays forbidden. A hidden `--discover` mode lists unreviewed ranges
    instead of stopping; that is how the list was built.
* **Host fixtures** (the hooked entries; their bodies stay forbidden):

| RVA | Routine | Fixture |
|---|---|---|
| 0x1b50 | DebugPrint | formatted on the host (EDK2 `%a %s %d %u %x %r %p %g`) into the report. Debug output is compiled in: PcdDebugPropertyMask 0x2f, level mask 0xffffffff |
| 0x290a0 | dto_print (libufdt) | captured. **On the phone it is a stub returning -1**, so libufdt errors are silent there; the fixture returns -1 too |
| 0x1e98 / 0xfa48 | DebugAssert / `__stack_chk_fail` | stop the run |
| 0x10a00 / 0x10c18 | BoardPmicModel / BoardPmicTarget | PM6125 v1.1 (45, 0x1012d), PMI632 v2.0 (37, 0x20025). **Never queried** for these images (no `qcom,pmic-id` in any DTB) |
| 0x1a3e8 | GetTimerCountms | 0 (only used for timing prints) |
| 0x25488 | RTIC MP_DATA -> TrustZone | returns 1. Every path past the property reads returns 1; the value only picks a debug line in GetSocDtb (0x1584c), and selection continues either way |

**BoardInfo fixture** (MEASURED, stock boot log `kmonlog_2026_0811_093640`): kernel `socinfo_print:
id=444, ver=1.0, hw_plat=11, hw_plat_ver=65536, hw_plat_subtype=0, foundry_id=2`; XBL "Platform
ID:11, Major ID:1, Minor ID:0, Subtype:0"; UEFI "Platform: QRD". The PMIC revisions come from the
kernel lines "PM6125 v1.1" and "PMI632 v2.0". The Model/Target packing follows CAF Board.c
(**INFERRED**; unused here, see above).

## Results (MEASURED, `tests/abl-check-tests.sh`, 2026-10-14)

Positive controls: all three stages PASS, merged DTB model "Qualcomm Technologies, Inc. Bengal QRD".
That is the stock kernel's own `Machine model:` line.

| Image | Target | ImageSizeActual | DTBO source | Merged DTB |
|---|---|---|---|---|
| `HLTE556N/recovery.img` (package) | recovery | 26,959,872 | image+0x18fe000 (88,706 B) | 416,425 B |
| `Backups/2026-08-08_055553/recovery_b.img` (stock, device) | recovery | 26,955,776 | image+0x18fd000 (88,722 B) | 416,441 B |
| golden `lun0/recovery_a.bin`, `recovery_b.bin` | recovery | 41,816,064 | image+0x2729000 | 416,441 B |
| `recovery/twrp-HLTE556N-31434216263.img` | recovery | 41,816,064 | image+0x2729000 | 416,441 B |
| `recovery/recovery-adb-permissive.img` | recovery | 26,906,624 | image+0x18f1000 | 416,441 B |
| golden `lun4/boot_a.bin`, `root/magisk_patched-30700_boot_b.img` | boot | 16,703,488 | dtbo partition | 416,441 B |
| `HLTE556N/boot.img` (package) | boot | 16,539,648 | dtbo partition | 416,441 B |

Side finding: golden `recovery_a.bin` and `recovery_b.bin` have the same sha256 as the TWRP image
(`077cac63…`). The flashed recoveries are byte-identical to it, including the 96 MiB padding.

Negative controls (built in memory from the TWRP image unless noted). Each must FAIL at the stated
stage with the ABL's message:

| Control | Fails at | ABL says (rc) |
|---|---|---|
| **built without `--recovery_dtbo`** (no dtbo pages, DTB right after the ramdisk; the TWRP builds 1-2 layout) | 2 dtbo, then fallback | "Dtbo hdr magic mismatch 52444e41" (the "DTBO" is the boot header itself), then DeviceTreeAppended: "kernel image does not have appended DTB", then raw check FDT_ERR_BADMAGIC (-9) |
| recovery_dtbo size 0 and offset 0 | 2 dtbo | same magic mismatch |
| recovery_dtbo size 0, offset kept | 2 dtbo | "DTB header is corrupted, DtEntryCount…" (rounded size 0) |
| recovery_dtbo bytes zeroed | 2 dtbo | magic mismatch |
| recovery_dtbo size 0x1800001 | 1 header | "Recovery Dtbo Size too big 1801000, Allowed size 1800000" (EFI_BAD_BUFFER_SIZE) |
| recovery_dtbo offset past the partition end | 2 dtbo | "Invalid recovery dtbo: …ImageSize=100663296" |
| magic "ANDROID?" | 1 header | "Invalid boot image header" (EFI_NO_MEDIA) |
| header version 3 | 1 header | "Invalid vendor_boot image header" (EFI_NO_MEDIA) |
| header version 1 with a v2 header_size | 1 header | "Invalid boot image header: 1660" (EFI_BAD_BUFFER_SIZE) |
| v2 header_size 1648 | 1 header | "Invalid boot image header: 1648" |
| page size 8192 | 1 header | "Invalid image pagesize" |
| kernel size 0 | 1 header | "Invalid image Sizes" |
| DTBO table header_size 0x24 | 2 dtbo | "Dtbo Table HeaderSize got corrupted" |
| overlay board-id 0x1000c (platform 12) | 3 GetBoardDtb | "Dtbo count = 0" → "Error: Board Dtbo blob not found" |
| SoC DTB msm-id 0x1bc→0x1bd | 3 GetSocDtb | "qcom,msm-id does not match" → no SoC DTB |
| overlay `__fixups__` label renamed | 3 ufdt_apply_multioverlay | "Couldn't find 'Zhermal_zones' symbol in main dtb" / "failed to perform fixups in overlay" |
| image file truncated inside the DTB | 0 file (host) | n/a |
| boot_a.bin as recovery | 2 dtbo, then fallback | magic mismatch, then the fallback fails as above |
| boot with a zeroed dtbo partition magic | 2 dtbo | "Dtbo hdr magic mismatch 0" |

Command-line checks: the positional target exits 0; a `recovery_dtbo_size`=0 file exits 1 (JSON stage
dtbo FAIL); a missing file exits 2; the package `abl.elf` is refused with exit 2.

**Cross-check of stage 3 (MEASURED).** `--save-dtb` output for the TWRP image vs `fdtoverlay -i soc.dtb
-o ref.dtb overlay.dtbo` (dtc), both decompiled with `dtc -s`. With `__symbols__` left out, both have
17,220 lines. They differ in 52 lines, of two kinds only:
* The overlay's root properties (`model`, `compatible`, `qcom,board-id`). libufdt applies them and
  fdtoverlay ignores them. The ABL's result is the one the stock kernel printed.
* A consistent renumbering of 10 phandles and the `pinctrl-0/1` references to them.

## ABL facts learned (MEASURED unless marked)

1. **CheckImageHeader does not require a recovery_dtbo.** For v1/v2 with BootIntoRecovery it checks
   the following, but a size of 0 passes:
   * page-rounded recovery_dtbo size ≤ 0x1800000 (24 MiB);
   * offset + size does not overflow 64 bits;
   * the size is added to ImageSizeActual.

   Other header rules:
   * v1 needs header_size 0x670 (1648); v2 needs header_size 0x67c (1660) and dtb_size ≠ 0
     ("DTB Image not present").
   * Page size must equal 4096 or be ≤ 4096; kernel size and page size must be non-zero.
   * Version > 2 takes the vendor_boot path.
2. **LoadAndValidateDtboImg takes the DTBO from the recovery image.** That happens for a recovery
   boot with header v1/v2 (IsDynamicPartitionSupport, 0x13290, is hard-wired to 1). The DTBO is
   ImageBuffer + recovery_dtbo_offset, size rounded to the page, and offset + size must be ≤
   ImageSize. Then the table is checked:
   * magic `d7b7ab1e`, size ≤ 24 MiB, TotalSize non-zero and ≤ 24 MiB;
   * header size 0x20, entry size 0x20, DtEntryOffset ≤ 24 MiB;
   * count × entry size, and offset + count × entry size, both ≤ the DTBO size.

   A normal boot reads the `dtbo` partition instead. With size and offset 0 the "DTBO" is the boot
   header itself, hence `magic mismatch 52444e41` ("ANDR").
3. **Without a valid DTBO the ABL does not boot this image.** BootLinux (0x12834) falls back to
   DeviceTreeAppended, the legacy appended-DTB matcher.
   * That matcher compares the platform id inside msm-id: DT `<0x1bc 0x0 0x10000 0x0>` vs board
     `<0x1bc 0xb 0x10000 0x0>`. It rejects the SoC DTB, which carries the platform only in board-id.
   * The last resort checks ImageBuffer + PageSize + DtbOffset (0x128fc). That is the v0/v1
     kernel-relative layout, one page past a v2 image's DTB, so it gets FDT_ERR_BADMAGIC.
   * The ABL then prints "Error: Device Tree blob not found" and BootLinux fails before the kernel
     starts.
   * **INFERRED:** this is what happened to TWRP builds 1-2. The emulated analog of their layout
     ends here. The original images are not on the share, and what the ABL does after BootLinux
     returns (fastboot or a reboot) is not emulated.
4. **DT selection for this board.** SoC DTB = entry 0 of the image's DTB section, 327,783 bytes,
   msm-id 0x1a1 and 0x1bc at v1.0, board-id 0/0. 0x1bc matches, Bestmatch 0x2c00cc00. The match is
   not exact, so the DTBO search runs: board overlay = DTBO entry 0, 88,658 bytes, board-id
   0x1000b, LocalBoardDtMatch 0x1c001400. No DTB has qcom,pmic-id, so PMIC revisions play no part.
5. **The DTB section ends with a 173-byte RTIC DTB.** It has no msm-id, and GetSocDtb hands it to
   0x25488. That routine reads `qcom,rtic-id`/`MP_DATA` and passes them to TrustZone (LocateProtocol
   SCM + ScmSipSysCall).
6. libufdt error messages are compiled out (dto_print 0x290a0 is `return -1`). EDK2 debug output is
   fully on (the ABL logs every level), which fits the verbose kmonlog.
7. The ABL's merged DTB for every image here is 416,441 bytes (416,425 with the package recovery's
   88,706-byte DTBO).

## Reproduction

```
# PE extraction and pin (also done by the script on every run)
python3 -c "import lzma,hashlib;d=open('$A9_DATA/Backups/edl_2026-10-14_110759/lun4/abl_a.bin','rb').read();x=lzma.decompress(d[0x3078:],format=lzma.FORMAT_ALONE);pe=x[x.find(b'MZ'):];print(hex(x.find(b'MZ')),len(pe),hashlib.sha256(pe).hexdigest())"
# disassembly with string annotations (scratch): capstone over .text, skipdata=True
nix-shell -p python3Packages.capstone --run 'python3 -P dis.py'   # writes abl.dis
grep -n '"Invalid recovery dtbo\|"Recovery Dtbo Size too big\|"ANDROID!' abl.dis
# one image, all stages; JSON report with the ABL's log
scripts/abl-check.sh $A9_DATA/recovery/twrp-HLTE556N-31434216263.img recovery --json
# stage-3 cross-check against dtc
scripts/abl-check.sh <img> recovery --save-dtb abl.dtb
nix-shell -p dtc --run 'fdtoverlay -i soc.dtb -o ref.dtb overlay.dtbo; dtc -I dtb -O dts -s abl.dtb -o a.dts; dtc -I dtb -O dts -s ref.dtb -o r.dts'
# full suite
tests/abl-check-tests.sh
# review of unreviewed code reached (development only)
nix-shell -p python3Packages.unicorn --run 'python3 -P scripts/abl-check.py <img> recovery --discover'
```

Stock boot-log fixture sources: `kmonlog_2026_0811_093640.txt` (session scratchpad `kdebug/txt/`,
from golden `lun0/kdebuginfo.bin`, see [stock-boot-logs.md](stock-boot-logs.md)).
Lines: `CDT Version:3,Platform ID:11,Major ID:1,Minor ID:0,Subtype:0`, `socinfo_print: … id=444,
ver=1.0, hw_plat=11 …`, `Machine model: Qualcomm Technologies, Inc. Bengal QRD`, `PMIC@SID0: PM6125
v1.1`, `PMIC@SID2: PMI632 v2.0`, `VM Hyp calls not present`.

If the ABL is ever updated (an OTA, or a new golden dump with a different `abl_a`), the pin fails
and every RVA here has to be re-derived. The A6L method is the model for that:
`a6l/repo/tools/Test-CapturedAbl*.py` in the session scratchpad; [a6l-harvest](a6l-harvest-2026-10-13.md) §4.11.
