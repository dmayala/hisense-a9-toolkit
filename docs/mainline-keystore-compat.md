# Legacy Keymaster: IRPC absence and attestation error -10003

Follow-up: [read-only physical persist and per-open SFS diagnostics](mainline-persist-readonly.md)
replace the original writable isolated namespace and capped observer described
below. The -10003 assignment remains proven; successful attestation remains unproven.

Host-only analysis of the supplied private integ18 log and the device's stock
Keymaster libraries/TA. No phone access, key-file contents, provisioning,
factory-store modification or RPMB operation was used. These two messages have
different meanings: IRPC absence is expected on this non-RKP legacy stack;
`-10003` is a real factory-attestation storage-open failure, not an expected
generic Keymaster4/keystore2 compatibility error.

## What the retained requests actually contain

All ten `Error::Km(-10003)` records have EC, KEY_SIZE256, PURPOSESIGN,
NO_AUTH_REQUIRED and an ATTESTATION_CHALLENGE plus ATTESTATION_APPLICATION_ID.
Five include unique-ID/reset-related tags and five omit them; both variants
fail. They are asymmetric attested-key requests, not plain AES-256 generation.
Only public tag names/counts and enum values were extracted; no challenge,
application ID, certificate subject, alias, user ID or token is reproduced.

The phrase about generating without a provided attestation key describes
`generateKey(params, None)`: the caller/RKP did not supply a separate signing
key. It does not remove the challenge from params. Android16's compatibility
layer first generates a legacy key, then calls legacy `attestKey` when the
challenge is present. A certificate/attestation failure deletes the newly
generated key and returns the failure as a failed KeyMint generate operation.
Thus a keystore2 generate failure need not mean the original KM4 generate
failed. See pinned Android16
[security_level.rs](https://android.googlesource.com/platform/system/security/+/ce2aa0ef49158d0d45fb133fd42ab1deff210f8c/keystore2/src/security_level.rs#664)
and [km_compat.cpp](https://android.googlesource.com/platform/system/security/+/ce2aa0ef49158d0d45fb133fd42ab1deff210f8c/keystore2/src/km_compat/km_compat.cpp#485).

## Why missing IRPC is expected

The stock Android11 Keymaster4 interface uses a securely provisioned factory
attestation key through a separate `attestKey` call. It does not implement RKP
or require IRemotelyProvisionedComponent. Android16 explicitly supports old
devices with no declared IRPC: the default hybrid path logs the lookup failure
and returns no RKP key, allowing factory attestation. Both RKP-only properties
default false. Device-unique attestation bypasses RKP selection directly.
The observed downstream call with no provided signing key is consistent with
that fallback, not evidence that the main TEE Keymaster is missing. Sources:
[Android11 IKeymasterDevice](https://android.googlesource.com/platform/hardware/interfaces/+/3fe3ba022e4da87110ca194b3ce6ad03f3c86e23/keymaster/4.0/IKeymasterDevice.hal#776),
[IRPC declaration lookup](https://android.googlesource.com/platform/system/security/+/ce2aa0ef49158d0d45fb133fd42ab1deff210f8c/keystore2/src/globals.rs#430),
[hybrid fallback](https://android.googlesource.com/platform/system/security/+/ce2aa0ef49158d0d45fb133fd42ab1deff210f8c/keystore2/src/remote_provisioning.rs#88),
[old-device comment](https://android.googlesource.com/platform/system/security/+/ce2aa0ef49158d0d45fb133fd42ab1deff210f8c/keystore2/src/remote_provisioning.rs#126),
and [attestation-key selection](https://android.googlesource.com/platform/system/security/+/ce2aa0ef49158d0d45fb133fd42ab1deff210f8c/keystore2/src/attestation_key_utils.rs#62).

The source commits are Android16_r1 system/security
ce2aa0ef49158d0d45fb133fd42ab1deff210f8c and Android11_r1 hardware/interfaces
3fe3ba022e4da87110ca194b3ce6ad03f3c86e23. They establish intended compatibility
behavior; the running LineageOS binary's exact source revision is not claimed.
No IRPC/StrongBox service should be fabricated, and no requested challenge or
legacy failure should be suppressed to silence the warning.

## What -10003 means in this stock implementation

Stock libqtikeymaster4.so has distinct HIDL generate and attest paths at
0x10e6ec and0x10fac4. `KeymasterHalDevice::attest_key` at0x111be0 sends modern
TEE command0x2115; its legacy counterpart at0x115218 sends0x115 and propagates
the TA's nonzero result. REA evidence
ev_cd837e02693accf8a1f26beb5600ef44e2c7eed6e469e57b97591a38d047e8c6,
ev_e0509a6c800aeccde148b897f8d8d8563cec51670a8efdb3dbc23aa24612f9a2,
ev_1f8ff26b28854480d11c0abc5ce62ea4caebda91aa460686fe04fd4b08c3daec
and ev_891ddf055bce6716e23166b8fb21392d4b8289b7690f615a17868692375c608a.

REA of the selected stock keymaster TA (SHA256
11c120cabd09e9caa48e85f6f7ce307ff7fe5f3a2b82a465f2afeaf4313bec43)
locates `km_read_attestation_key` at image0x113748, ELF VA0x13748. It calls
`qsee_sfs_open(filename, 0)` and branches on a null handle at VA0x1377c;
VA0x137c8 assigns -10003. Other checked errors differ: zero-size/short-read
returns -49 and allocation failure returns -41. The routine does not query
`qsee_sfs_error`, so this proves open failure, not specifically a missing file.
Evidence ev_0a4e0cf69bac166811355a2fbe4389ace4db27aeb45f166ece267034caf2700e.

`get_attestation_key` at image0x113fd8, ELF VA0x13fd8, tries logical
`attest_key`, then only on -10003 retries `/persist/data/rsa_attest_key` for
algorithm1 or `/persist/data/ecc_attest_key` for algorithm3. Another branch
uses `attest_key_sb`. Evidence
ev_99b61edc35f7e476ab38dade61d1b9c734338a8d21dc561053df661b09f99199.
The qsee_sfs_open PLT entry at image0x1001e0 is independently confirmed by
dynamic relocation and REA
ev_6d7de9df2c2e2ceac40ff061ce34d04fe5607759135c0d4124b91f2e44308b2b.
Ghidra uses image base0x100000; ordinary memory-only disassembly corroborated
the same original code bytes without modifying the artifact or reading keys.

This error assignment is not an unsupported-tag or unsupported-key-size return.
The same stock TA can produce it under Android11 when its attestation object
cannot be opened. It does not prove hardware damage, missing factory provision,
or that this phone succeeded at attestation on stock Android11.

## Mainline storage boundary and next discriminator

Mainline deliberately binds `/metadata/a9-tee-persist` over
`/mnt/vendor/persist`, retaining a separate TEE store. Stock mounts physical
persist. Stock libdrmfs contains the `/persist/data/` and
`/mnt/vendor/persist/data/` translations. The different backing namespace is a
real integration difference, but the log/TA return alone does not distinguish
missing objects from inaccessible, invalid or unavailable SFS backing state.
Factory credential contents/health and authenticated RPMB reads are unproven.

A full factory-lower/metadata-upper writable overlay was reviewed and rejected:
read-only physical persist would still allow logical secure-object/table
copy-up, creation and whiteouts in the upper while reads fail. It would also
merge unrelated factory state with an independently evolved store. The existing
UFS guard does not prevent ordinary filesystem table writes. No such overlay,
factory-store exposure, reprovisioning or write fallback is included.

Keep this failure open. Obtain narrowly scoped numeric filesystem-open results
for the known attestation/backing paths while preserving the original operation,
return and errno. A demonstrated missing object can then be assessed with its
actual SFS mapping/companions and write-fallback prevention. Never create/write
KMTB or program RPMB while reads fail. IRPC absence can be documented as expected;
attestation failure must not be relabeled expected or converted to a false pass.

Complete stock REA, pinned AOSP sources and sanitized findings are retained under
`A9-build/mainline/fbe-keystore-errors-20261024/{rea,ta,irpc}`. No factory key,
device identifier or private log payload is included in this report.

## Passive observation and integ20 follow-up

The next latest-main image installs `liba9-attest-fs.so` as a preload only for
qseecomd. It observes existing open/openat/creat calls under the exact absolute
`/mnt/vendor/persist/data` component prefix, with no path resolution, reads,
retries, redirection or extra storage operation. Calls, modes, flags, directory
FDs, results and post-call errno are preserved. It logs only object/op/access
enums, errno and a success boolean; known attestation basenames have separate
budgets from other store activity. Seven events and one exhaustion marker per
class bound output to 32 messages. No filename, FD, alias or key content is logged.

Native and actual Android ARM64 QEMU tests pass for forwarding/errno, modes and
O_TMPFILE, nested Bionic creat/open, EFAULT, prefix/traversal rejection, caps and
redaction. Packaging tests cover qseecomd-only/idempotent init, unexpected-preload
refusal and small ext4 readback; unchanged CIL also compiles. No mount, policy,
factory-store exposure or original operation is changed. This is diagnostic
instrumentation, not a repair of factory attestation. Relative paths or a
different backend path remain blind spots; public liblog has no delivery ack.
The combined observer, including the passive RPMB backend hooks described in
[RPMB R4](mainline-fbe-rr4.md), has SHA256
c9d6d2f2b0735cdffbdd2556b2fd3ff6728c2e4b23865651f9328c74f0e73fa9.

Integ20 returned during this investigation. Its second saved boot positively
registers RPMB with request25600/mapped28672, transport0/status_valid1,
secure_status0/mapped_rc0 and registration phases4/5/6 rc0. The old registration
-22 is resolved in this evidence. Both saved TA logs still contain a
qsee_stor_read_sectors failure followed by km_add_key_id and -67. The first
boot also retains 16 km_read_attestation_key -10003 records, consistent with
eight failed calls trying both attestation paths. No successful authenticated
RPMB read, RR key or factory attestation is established.

Integ20 was frozen at9c48b6f before the independent logcat bridge26acc22, so
its missing bridge heartbeat is expected. One saved kernel stream still ends
near5s; the second extends beyond300s. Both contain ten discovery guard records.
In that build discovery and protocol operations share one pr_info_ratelimited site, so absent
OUT/IN/denial records cannot establish absent SG traffic after burst exhaustion.
No guard change follows from that absence.

New stock librpmb REA also identifies a possible pre-read failure: rpmb_init
at0x1066e4 can return0 while caching an invalid backend if bootdevice stat,
readlink or target recognition fails. rpmb_read at0x1068d0 then returns-1.
That path is not diagnosed in these boots. Distinguish backend/SG discovery,
descriptor-query, callback delivery and SG completion numerically before
changing behavior. Registration success alone is not successful RPMB reads.
The next main build adds those passive milestones; see [RPMB R4](mainline-fbe-rr4.md).
