# RPMB R5: secure-storage read failure and legacy backend discovery

Host only. No device access, TA execution, RPMB requests or storage contents
were used. Physical persist remains read-only. Never create/write KMTB or
program/write RPMB while reads fail; the kernel read guard is unchanged.

The sweep summary command is now available:

```
python3 scripts/mainline/reboot/credential-summary.py /saved/sweep-directory
```

It emits fixed tags with integer field values only; missing PIN evidence is
unknown. See [field meanings and capture limits](../scripts/mainline/reboot/CREDENTIAL-SUMMARY.md).

## What produces the retained -67

REA of the actual stock Keymaster TA, SHA256
`11c120cabd09e9caa48e85f6f7ce307ff7fe5f3a2b82a465f2afeaf4313bec43`,
distinguishes these paths. Addresses below use Ghidra's load base `0x100000`;
subtract that base for ELF virtual addresses. Names are inferred from the TA's
diagnostic strings; static decompilation does not establish execution.

| Function / address | Behavior | REA evidence |
| --- | --- | --- |
| addkeyId / 0x109128 | Converts **every nonzero** km_add_key_id result to -67 when the rollback-resistant flag is set. | ev_589fd9927693386b2fd76af0677e62183b1f25d3e6dff75707b8345aa76f118d |
| km_add_key_id / 0x115670 | Readiness false returns -67 directly; readiness true calls storage_alloc_read and propagates its error through diagnostic line101. | ev_ef283e7db2680984cd9ff79b69b385fcbe06e5a03926b1281c6ac645d720ccab |
| storage_alloc_read / 0x11602c | Allocates 0x3000 bytes and reads at byte offset0x2000, equivalent to KM-partition sector16/count24. Read error is logged at line74. | ev_2bb9c03cbb5624ccadfb0774655e53c27ef7601e85a4909f13343b6330b1f12f |
| Raw read / 0x117cd8 | With readiness and handles present, calls qsee_stor_read_sectors; any nonzero API result becomes -8. | ev_2b13e9985ab4318b7273edbd4996091f7f203ea8a4267e705b193801b0449e30 |
| Storage setup / 0x1183cc | Opens KM partition ID0x4b4d5442 and GK partition ID0x474b3232 before setting readiness. Stock also contains partition-add branches; none were executed or enabled. | ev_e4427d447994423a3eb736068cb3930ad4242c48cda7710d74e97bc1357564c9 |

The retained `qsee_stor_read_sectors` failure, `storage_alloc_read:74` and
`km_add_key_id:101` chain therefore identifies an API read failure propagated
as -8 and then -67. It does not identify the physical RPMB frame result or
prove an unprogrammed device. The allocation pointer remains cached after a
failed first read in the inspected routine; later calls may skip that read.
That state is no reason to retry provisioning or trust cached table contents.

Integ21 also retains successful registration phases4/5/6 and read/write listener
callbacks. Stock librpmb dispatch at `0x105bbc` stores the operation return code
in shared response header offset4 before replying. QSEE reply result0 means
the callback response was accepted; it does not establish read success. A write
callback does not establish physical write submission or success. Its dispatch
evidence is `ev_bef6b1dd71df3dc936392f5d724403e7a7668f431bfbacd27d9325dde30b6bdc`.

## A concrete startup gap

Stock `/vendor/etc/init/hw/init.target.rc`, lines42–44, waits for its UFS
platform directory and creates `/dev/block/bootdevice` before qseecomd starts.
Stock librpmb `rpmb_init` at `0x1066e4` performs `stat` and `readlink` on that
exact legacy link and searches its target for `ufshc` or `sdhci`. A missing,
unreadable or unrecognized link can cache backend0x7fffffff while returning0.
Subsequent `rpmb_read` at `0x1068d0` then returns-1 without UFS I/O. See the
[R4 evidence IDs and passive backend records](mainline-fbe-rr4.md).

All 40 init/ueventd RC files in the saved integ21 vendor were audited; none
creates the legacy link. The selected GSI init RCs and tracked mainline startup
sources likewise lack its creation. The saved boot header supplies the plural
`androidboot.boot_devices=soc@0/4804000.ufshc`. That enables generic by-name
links; it does not supply stock's singular-property init action. Reference
[AOSP Android16 init/devices.cpp, GetBlockDeviceSymlinks](https://github.com/aosp-mirror/platform_system_core/blob/android-16.0.0_r1/init/devices.cpp#L487)
creates platform and generic by-name links, without the legacy directory alias.
This source reference is not an exact-revision claim about the running GSI.

Credential packaging now inserts a five-second bounded wait for the board's
mainline platform directory, followed by:

```
symlink /dev/block/platform/soc@0/4804000.ufshc /dev/block/bootdevice
```

Both precede qseecomd startup and thus its cached backend initialization. This
restores stock's virtual discovery setup using the mainline controller path;
it adds no mount, block open, ioctl, data migration, permissions or policy.
Existing source link conflicts fail host packaging. The RPMB guard continues
to deny programming and authenticated writes, including any stock TA attempt
to add a missing partition or persist a new key ID.

The source gap is established. Its causal role in the saved phone's -67 remains
unproved because no backend errno or descriptor-query records survived there.
Other pre-query/pre-queue failures and incomplete logging remain possible. The
read audit found no supported change to the descriptor ABI, GET_IDLUN match,
argument order or read CDB; no transport or guard relaxation was made.

## Host validation and next coordinator run

Three focused checks pass against the actual saved vendor RC: ordering,
idempotence and unrelated-byte preservation; ambiguous/conflicting startup
rejection; and the real Android host_init_verifier. No full image or phone
build is claimed. The numeric summary's five fixture groups also pass.

The next image built from this change should retain backend op1 `ok=1 errno=0`,
then op2 `ok=1 errno=0 ufs=1 mmc=0 truncated=0` if link setup and permissions
succeed. These prove only discovery observations. Next require descriptor
QUERY IDNs0/7/2 with rc0, allowed read-request OUT/response IN **and completion**,
then TA read validation without the storage-read failure chain. Registration
or `allowed=1` alone is insufficient. RR generation may still fail because
persisting a new key ID remains deliberately blocked; do not infer a cipher
failure or initialize storage from that outcome.

Retained host evidence is under
`A9-build/mainline/fbe-rpmb-r5-20261024/`: complete TA REA responses plus the
exported `rea/evidence-bundle.json`, `ta-report.md`, config hashes/copies in
`config-audit/`, and `alias-tests/tests.json`. The reference AOSP file has SHA256
`7b987069b5b1d03958a1c21b49b60970eab9c27dc418bb00d2d818e700b74231`.
No key, table, sector, MAC, counter, nonce or device identifier was exported.
