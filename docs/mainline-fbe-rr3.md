# FBE-RR3: independent RPMB logcat diagnostics

Host-only continuation of [FBE-RR2](mainline-fbe-rr2.md). Integ19's retained
kernel stream ends around five seconds, before any retained RPMB registration
record. Its zero init/register counts are missing evidence. Vold's non-rollback
fallback does not establish whether registration, callback I/O or TA validation
failed. No phone operation or RPMB command was executed in this task.

## Two diagnostic paths

The kernel still emits the existing pointer-free `A9_RPMB_REGISTER`,
`A9_RPMB_SCM` and `A9_RPMB_GUARD` records into kmsg. A separate static Android
ARM64 service, `a9_rpmb_diag`, now reads its own non-destructive `/dev/kmsg`
cursor and submits only exact numeric diagnostic messages to main logcat under
`A9_RPMB_DIAG`. It starts independently at init and explicitly before qseecomd.
It does not depend on logger.c, kdebuginfo, debugfs, counters or mount success.
agent owns the separate logger.c repair; this change does not edit it.

The service has a dedicated enforcing domain with kmsg/SYSLOG read and logdw
submission permissions. It cannot open QSEE/UFS/RPMB devices or issue their
ioctls. It neither writes/clears/seeks kmsg nor changes a readiness property.
Whole-message schemas reject arbitrary suffixes, pointers, tokens, sectors,
TA contents and unknown call IDs. The raw public SCM function ID is allowed;
physical addresses are not. Secure status retains the full signed 64-bit value,
including a zero-extended 32-bit error such as 4294967294.

A bounded 64-record RAM queue retains messages while logd is unavailable.
Only successful datagram submission removes a queued record; failed sends
retry the diagnostic record, never the secure call. Public liblog's write API
does not expose its transport failure, so the service uses the checked Android logdw
11-byte packed header and checks actual sendmsg acceptance. Acceptance is not
logd persistence or reader acknowledgment. Polling/read-error backoff bounds
CPU use; kernel overruns, oversized records, queue drops, read errors and
submitted/queued counts appear in a numeric heartbeat. No finite buffer can
guarantee evidence across an unlimited logd outage or kernel-ring overrun.

The existing QTI-stack vendor preparation installs this service and policy for
both integrated builders. The integrated source manifest includes all bridge
sources. Existing kernel diagnostics, registration behavior and the immutable
UFS read-first guard stay unchanged. The evidence extractor now retains raw SCM,
request/mapped-length and heartbeat records from logcat or kernel.early.log as
well as normal kernel logs. Its private daemon extract recognizes underscore
tags; its public summary reports register/scm/bridge record counts explicitly.

## Registration comparison and remaining unknown

Independent source/retained-REA review found no additional supported registration
argument defect after 1f11850. Owner50/service2/command6, arginfo0x83
(VAL/RW/VAL), listener0x2000 and mapped extent0x7000 match stock. The stock
library keeps logical0x6400 for userspace and rounds its allocation to0x7000:
register0x1064b0, allocator0x105d24, REA
ev_68ca41e99faa91b3c5f5294a8218eda9c491e88bc399f2395a2e6e0e22a5b899
and ev_dbdde82fdaa43560582e184192de4a31ba810c0df2fd5f19477b43513efb3152.
Stock kernel disassembly sets arginfo at0xffffff80087b5b50, packs mapped length
at0xffffff80087b2edc/2efc and selects command6 with only an -EIO fallback to
command1 at0xffffff80087b60a8..60d0. Mainline's ARM64 wire ID0x72000206 is
consistent with the descriptor ID0x32000206 plus the convention bit.

The coherent-DMA/SHM backend differs from stock heap27 ION. Current source
establishes a 32-bit coherent allocation device without an IOMMU attachment and
bridges the whole zeroed allocation; no supported IOVA/physical-address defect
or ownership change follows from the available evidence. CE-clock and bandwidth
votes bracket the call. Do not force a calling convention, change secure-memory
ownership, or add an -EINVAL fallback without the actual discriminator.

A bounded new REA inventory also examined the selected stock TZ image
(SHA256 13e664bf4a20d32cd8e5e14584b9a33ca891dc9398ef7dea7657dff7f1faaf7e).
It is a readable, sectionless ARM64 ELF with 28 segments, but no justified
listener-register handler emerged from the name/string/dispatch inventory.
Generic gpGetListenerSize strings at0x1034f749/0x10415c5b do not locate that
handler. Evidence ev_352790d50c2d16104530c657a119393e82af173394140847a58cc43f30091916
and ev_f3d563ada134807f15fc793bb8efc9ad5d87a88430ff886f099f78d1fa5c7af0.
No handler was decompiled and no proprietary failed check is claimed. Complete
responses and limits are in `A9-build/mainline/fbe-rr3-secure-20261024`.

## Integ20 evidence

Require the independent bridge heartbeat and the following complete chain in
logcat, with matching kernel records when kernel persistence is available:

```
A9_RPMB_REGISTER request=25600 mapped=28672 ... cmd=6 arginfo=0x83
A9_RPMB_SCM check=ce_clock rc=...
A9_RPMB_SCM check=bandwidth rc=...
A9_RPMB_SCM check=invoke fn=0x72000206 arginfo=0x83 nargs=3 size=28672 ...
A9_RPMB_SCM check=smc_return transport=... status_valid=... secure_status=... mapped_rc=...
A9_RPMB_REGISTER phase=4 rc=...
```

A clock/bandwidth failure before invoke identifies a local resource path.
Transport nonzero with status_valid0 means secure_status is not valid. With
transport0/status_valid1, secure -2/-3 distinguish secure argument/address
rejection; other nonzero statuses can also map to -22. None alone names the
internal TZ check. Secure status0 permits examining phase5 and phase6, then
listener startup and authenticated read/TA validation. A heartbeat without
registration records is an evidence gap to investigate, not registration success.

Never create/write KMTB, program RPMB, or permit authenticated data writes to
overcome failed reads. Allowed read-request submission and phase6 registration
are not authenticated-read or rollback-resistance success. This instrumentation
does not qualify hardware registration, RPMB reads or rollback-resistant keys.

## Host validation and handoff

Five focused groups pass: production parser and redaction with C/Python schema
agreement (including signed64 boundaries); bounded queue/failure/order behavior;
64 actual ARM64 logdw datagrams replayed under QEMU with late socket creation
and backpressure; production main-loop EOF/EINVAL/EIO backoff; and actual copied
vendor policy/init/payload/metadata/filesystem/no-growth checks. The prepared
vendor and binary source pins were rechecked after integration. Root reviewed
the transport and packaging; a separate read-only reviewer found no issue in
the parser, read loop, init and enforcing policy. No hardware availability or
SELinux/logd runtime success is inferred from those checks.

Artifacts: `A9-build/mainline/fbe-rr3-logcat-host/{tests,package-final}`. The final
static bridge SHA256 is
4e6332c98fcbd03c7d42be53cb90a6fa5b4065a837149c6281bbc0e0e3ac4909;
the immutable-integ18 host vendor derivative is
c33fda669061fed005ddf92f4a80c89c938957781becb2a71734eb711f8cf07e.
Use the normal latest-main integ20 assembly, which recompiles and installs the
bridge through QTI-stack preparation, rather than flashing this test derivative.
Keep the independent heartbeat and full raw registration chain in the retained
logcat before drawing a new conclusion about the old -22.
