# A9 stock RPMB transport compatibility

Apply `apply.sh PRIVATE_R1_KERNEL_TREE` after the QSEECom transport patch at
`54d4304`, then enable `ufs-rpmb.config` and run olddefconfig. This adds
`CONFIG_CHR_DEV_SG=y` and `CONFIG_SCSI_UFSHCD_A9_QUERY=y`. Both are built in;
there is no sg.ko or extra module to load. Existing QSEECom/ION, Stage2 ICE and
Stage3 userspace remain unchanged.

The host callback accepts only ioctl `0x5388`, READ_DESC opcode1, IDNs device0,
geometry7 and unit2. It preserves the downstream inline buffer ABI:

| Field | Offset | Size |
| --- | --- | --- |
| opcode | 0 | 4 |
| idn | 4 | 1 |
| padding | 5 | 1 |
| buf_size | 6 | 2 |
| descriptor | 8 | buf_size |

The same callback serves native and compat callers; no userspace pointer is
embedded. Device/geometry index0, unit index converted from the actual SCSI
LUN, selector0. Requested length is capped to255; the returned length and
descriptor bytes are copied out as in downstream. Invalid opcode/IDN/length
does not issue a query. The device WLUN is resumed using mainline runtime PM,
then `ufshcd_query_descriptor_retry` supplies its clock, management-command
and scaling locks. Memory, resume and user-copy failures propagate errno.

The existing `ufshcd_scsi_add_wlus` creates RPMB UPIU LUN0xc4 as SCSI
LUN0xc144. The SCSI generic class interface attaches to that device without
the SCSI upper-driver WLUN filter. GET_IDLUN0x5382 therefore exposes low LUN
byte0x44 as librpmb expects. Existing SG_IO0x2285 carries frames to that same device. With the A9 query
configuration enabled, the queue guard admits discovery, RPMB read-counter,
authenticated-read and result requests, and response IN. With the built-in
QSEE transport it also permits authenticated data-write groups wholly inside
the existing GK extent learned from a validated stock PTBL read. Unknown maps,
KMTB, other partitions, allocation metadata and key programming stay protected.
See [the scoped policy and trust boundary](../../../docs/mainline-fbe-rr6.md).
Raw/advanced RPMB bypass routes are denied. There is no runtime
write-enable or automatic unlock after a read. `CONFIG_RPMB` remains unset.
See [READ-FIRST.md](READ-FIRST.md) for observed listener failures and the staged
stock-equivalent plan. An allowed request is not proof that its I/O/MAC passed.

## Provenance and stock addresses

GPL sources: Qualcomm msm-4.19
[ufshcd.c](https://android.googlesource.com/kernel/msm/+/refs/heads/android-msm-redbull-4.19-android11-qpr3/drivers/scsi/ufs/ufshcd.c)
(`ufshcd_query_ioctl`, `ufshcd_ioctl`) and
[UAPI ioctl.h](https://android.googlesource.com/kernel/msm/+/refs/heads/android-msm-redbull-4.19-android11-qpr3/include/uapi/scsi/ufs/ioctl.h).
The ABI definition is copied with a flexible array in place of buffer[0];
the handler adapts only the three required descriptor reads to mainline.

Stock package kernel ELF SHA256
`0fd46f80770193f08633e9c5d31a6137800be0436524db743bd7f66db8154b01`:
`ufshcd_ioctl` at `ffffff80088337e0..ffffff8008833e58`, descriptor branch
`ffffff8008833910..ffffff80088339b0`, reply-size/copy path
`ffffff8008833b60..ffffff8008833d20`. REA/Ghidra evidence
`ev_030387baab2c4c3bfca2737a16b8210c8505966c514695cdd55387fc7d89616c`
confirms header8, length at6, data at8, 255-byte cap and RPMB index0xc4.
`ufshcd_query_descriptor_retry` is at `ffffff80088244d8`. The address-preserving
carrier includes exact code bytes and the ioctl jump tables; external callees
and unrelated kernel data remain unmapped. Full inline evidence and carrier
provenance are retained in the R2 build output, with these limitations.

Stock `librpmb.so` SHA256
`364139c927b091df2a2c9b3b6ff02330be60ba3fdb17be9f88c3b8a66632b02b`:
`rpmb_ufs_init` ELF0x6e68 (Ghidra0x106e68), agent REA
`ev_fa8d95d18fd9d90937fa7dafe2eaf7d2c5eb51909aa708c68ae20ad44ee34b65`.
It scans sg0..15, opens O_RDWR|O_NONBLOCK, GET_IDLUN checks byte1=0x44,
then issues three 40-byte requests with opcode1, IDNs0/7/2 and length32.
It reads device payload4, geometry payload23 and unit payload15, then uses
normal SG_IO. These offsets agree with the eight-byte kernel header.

## Integration and validation

Use the R2 report's exact boot/vendor pair and existing neutral DTBO selection.
Keep the R1 QSEECom/ION ueventd rules. Stock librpmb chowns its selected SG node
to system:system; retain stock qseecomd startup privileges, SG labels/grants
and normal SG_IO capability policy. No module-load rc change is required.
agent's R2 userspace already includes SG label/access rules; rebase its
kernel/image pins and remove its missing-SG/query prerequisite once this host
deliverable is imported. Do not declare listener initialization or wrapped-FBE
activation from host evidence.

`tests/run.sh OUTPUT` checks the production read-only frame policy/wrapper and
runs the actual production descriptor handler with a fake UFS
transport: three literal stock requests, short descriptors, index/selector,
read-only rejection, size limits, query/user-copy/PM/allocation failures and
balanced releases. It also compiles the same assertions independently for
ARM64. The build report records rc0, 100-module CRC/vermagic/dependency audit,
and ABL boot and recovery checks. These are host gates, not device results.
