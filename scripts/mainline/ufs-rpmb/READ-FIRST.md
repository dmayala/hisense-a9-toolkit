# Existing wrapped-key rejection: RPMB read-first investigation

Historical registration investigation below. INTEG23 demonstrated that the
blanket DATA_WRITE refusal also blocks Gatekeeper. The current policy permits
only existing GK extents learned from stock PTBL reads; KMTB, metadata and key
programming remain blocked. See [R6 diagnosis and policy](../../../docs/mainline-fbe-rr6.md).

Host-only; no device tools, key blobs, identifiers or RPMB sectors exported.
The old root/patch hypothesis is not proved. New TA REA identifies a separate
rollback-resistant key-id lookup: KMTB allocation/read failure can yield-8,
then a cached empty table can yield-33. That is a static explanation, not a
runtime trace of the current TA branch.

Saved INTEG12 boot100 logcat.prev confirms:

- rpmb_init_service returns0 (line705), which does not prove a running listener.
- QSEECom_register_listener8192 fails (line722), rpmb_start returns-1 (723–724).
- Keymaster Begin-8/diagnostic characteristics-33 follows (952–955).

INTEG13 also contains the RPMB registration/start failure. Retained logger files
have neither `qsee_stor_read_sectors() failed` nor `key id not present`; no TA
qsee/tz log was saved. Those missing strings cannot refute the static path.
`rpmb-evidence.py --saved-boot HOST_DIR --out HOST_FILE` exports fixed numeric
counts and new kernel markers only. B-try's post-pull extraction bundles it for
future integrated builds; an existing saved boot can be processed offline.

INTEG14 already enables CONFIG_CHR_DEV_SG and SCSI_UFSHCD_A9_QUERY. Do not add a
second descriptor ABI or guess that the query bridge is absent. Existing stock
librpmb discovers UFS RPMB via SG GET_IDLUN, reads device/geometry/unit descriptor
ioctl0x5388, and uses ordinary SG_IO. rpmb_init0 and register-1 put the first
observed failure at the QSEE listener boundary, before it can serve TA I/O.

New A9_RPMB_REGISTER markers distinguish the existing call's stages without
retrying, changing arguments, or dumping buffers/addresses/secure values:

| phase | Meaning |
| --- | --- |
| 1 | Invalid local registration state |
| 2 | Duplicate local listener |
| 3 | Shared-buffer binding |
| 4 | SCM registration transport status |
| 5 | Existing secure response/callback completion |
| 6 | Listener published successfully |

Read-first plan:

1. Keep the existing stock userspace listener, descriptor ABI, UFS well-known
   RPMB LU and SG route. Add a fail-closed, RPMB-LU-only transport guard before
   attempting to repair registration. It must cover queuecommand, not only the
   host ioctl callback (SG_IO is handled by the generic SCSI layer).
2. Allow ordinary read-only discovery and RPMB read-counter/authenticated-read
   request/response transactions. SECURITY_PROTOCOL_OUT carrying a READ request
   is part of a read, not a programming operation. Deny PROGRAM_KEY and
   authenticated DATA_WRITE, malformed/multi-frame OUT and unknown operations;
   provide no runtime write-enable or automatic unlock-on-first-read.
3. With that guard compiled in, use the numeric registration phase/errno to
   distinguish buffer/ABI failure from SCM transport/secure status. Do not
   blindly switch SCM commands, unregister an unknown secure owner, clear or
   initialize a table, replace secure state, or manufacture an empty success.
4. Verify descriptor reads, read-counter request/response, then an unchanged
   stock TA authenticated KMTB read and getKeyCharacteristics/Begin. Report only
   operation/status/nonce-match booleans, never key IDs, sectors, MACs, counters,
   nonces, device descriptors/identifiers or table contents. A host counter read
   without the secret key cannot verify its MAC; TA validation is required.
5. Programming stays blocked throughout diagnostics, even after reads work.
   Any later stock-equivalent write support is a separate reviewed change
   after authenticated reads and failure handling are established. Never let
   a failed read become a zero-table creation path.

The queued fresh FORMAT=1 test remains useful: a non-RR fallback can work while
RPMB is unavailable. A B-to-B reuse PASS would establish B-owned-data behavior;
it would not prove that rollback-resistant stock keys work. A failure after a
read-only write denial also must not be relabeled as a cipher incompatibility.

## FBE-RR: INTEG15 saved evidence and registration repair

The coordinator's fresh-format INTEG15 boot reached Android with real
hardware-wrapped userdata. Its plain B-to-B reboot reused those keys without
generateKey and completed boot in 102 seconds. B-generated key persistence is
established; stock/A-generated rollback-resistant key access is still unproved.
Both logs contain `A9_RPMB_REGISTER phase=4 rc=-22`, after successful buffer
binding and before secure response processing. qseecomd's readiness property is
insufficient: stock main ignores a failed rpmb_start and sets it regardless.

The registration ABI mismatch is now repaired. Stock's actual
`qseecom_scm_call` instructions at `0xffffff80087b6090..60d0` issue
`0x32000206` with arginfo0x83 and retry `0x32000201` only for `-EIO`. The
mainline frontend had deliberately forced the latter legacy command, which
the measured transport rejects with `-EINVAL`. The ID0x2000, 0x6400 buffer and
argument layout already match stock librpmb. The fix uses stock's command
selection and session-aware blocked continuation; it does not retry arbitrary
registration errors, bypass callbacks or change the UFS guard. Firmware's
internal reason for rejecting the old command cannot be observed from host
analysis; command6 success still needs a phone test.

Host evidence: `A9-build/mainline/fbe-rr-20261024/` contains the sanitized
timeline, production ARM64 kernel-object compilation and five production SCM
helper cases (new success, exact unsupported fallback, error propagation,
session/app continuation fallback and failure propagation). Stock REA evidence
IDs and coverage limits are in `docs/mainline-fbe-rr.md`.

Next coordinator test: a single B boot starting with FORMAT=1, because A was
reformatted after the successful B reuse experiment. Require phase4/5 rc0 and
phase6, then actual counter/authenticated-read request and response completion
and TA read validation. `allowed=1` alone is only submission. Retain private TA,
daemon and kernel logs. Expect programming denials if TA attempts new KMTB
allocation; RR generation may still return -67 under the intentionally
read-only guard. That is not permission to initialize or repair the table.
Any later write support needs successful authenticated-read evidence and a
separate explicit change; this patch leaves programming blocked even then.

Fresh INTEG14 partial evidence reports vold0/ready1/f2fs1 and tmpfs1. The shipped
hardware RC unconditionally creates a bootstrap /data tmpfs at early-fs, before
late-fs calls vold; this explains an underlay, not proof of a tmpfs overmount.
The wrapped worker now detaches only that sole bootstrap mount before vold,
restores RAM fallback only if vold fails with no real mount, and requires one
f2fs mount before readiness. It never covers a partial real mount on an error.
Its existing crypto-status updates get a narrow vold_status_prop grant and
must succeed before readiness. Acceptance reads crypto markers from the
boot-bound private logger, rather than an inaccessible /kdebuginfo path.
Five Android mksh fake mount/vold/property groups, three private-log lookup
controls and split-policy compilation pass. These are source/host repairs;
the partial log alone does not establish RR fallback or listener registration.

No image is built here; latest-main source commits and object-only host checks
are the handoff. Existing running/frozen images remain unchanged. The current
read-first work does not claim a stock-key reopen PASS.

## Implemented read-only guard

`0002-rpmb-readonly-guard.patch` applies after the descriptor-query patch. Its
RPMB-WLUN queue check covers native/compat SG_IO and legacy SCSI submissions.
Programming denial returns DATA_PROTECT/write-protected CHECK_CONDITION before
DMA or UFS submission; it does not inject a host error that could provoke a
controller reset. Other normal UFS LUNs are unchanged. Discovery/sense and LU
power control stay available. The pure policy checks the stock12-byte security
CDB, protocol/SPSpecific, direction, declared byte/512-unit length, and the one
outbound512-byte read frame. Counter-read2 and authenticated-read4 are allowed;
PROGRAM_KEY1, WRITE_DATA3, RESULT_READ5 and all other OUT forms are rejected.
The private frame copy is zeroed. No response data is replaced or synthesized.

Raw BSG UPIUs targeting RPMB and the separate advanced-RPMB handler cannot
bypass that policy: they return EACCES in this read-first build. Stock librpmb
uses the legacy SG route. The guard stays on regardless of read outcomes.

A9_RPMB_GUARD uses only fixed numbers: mode1/read-only; allowed submission0/1;
operation1discovery/control,2counter request,3authenticated-read request,
4read response,0denied/unknown. Reasons1unsupported command,2CDB/protocol,
3length/direction/frame-copy,4non-read request,6alternate route. `allowed=1`
means only that a read was submitted, never I/O completion or MAC validation.

`build14-kernel.py` now explicitly reconstructs the existing UFS query core,
applies both patches in its private source tree, and recompiles ufshcd.o.
Copying a new header while retaining the old linked UFS object would not enforce
the guard. Existing configuration/export checks remain in place. This task
built no kernel/image; production UFS and QSEE ARM64 objects and focused host
policy/ABI fixtures were compiled independently. Future image builders must
start from latest main and record that commit as usual.

FBE-RR2 corrects the stock listener registration extent (logical0x6400,
registered allocation0x7000), with raw SCM failure diagnostics. See
[the complete check map and next-run evidence](../../../docs/mainline-fbe-rr2.md).
This change does not enable KMTB writes, RPMB programming or authenticated writes.
