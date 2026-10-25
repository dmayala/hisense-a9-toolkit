# RPMB R4: registration succeeds; localize the remaining read failure

Follow-up: new builds use [read-only physical persist](mainline-persist-readonly.md)
and per-open SFS root/errno records. The original isolated-store description is
historical; the RPMB read guard and the registration/read distinction remain.

Host-only analysis of the private integ20 evidence. No phone access or actual
RPMB request was performed. The existing read-only guard and isolated TEE store
remain in place. Never write/create KMTB or program RPMB while reads fail.

## What integ20 establishes

The second saved boot positively records request25600/mapped28672,
transport0/status_valid1/secure_status0/mapped_rc0 and registration phases4/5/6
rc0. This resolves the earlier registration -22 in the retained evidence.
Both saved TA logs still report qsee_stor_read_sectors failure followed by
km_add_key_id and -67. Registration success does not establish an authenticated
read or a rollback-resistant key.

The first kernel stream ends around5s; the second extends beyond300s. Both
contain exactly ten discovery guard records. The old shared rate-limit budget
could hide later OUT/IN/refusal records. Their absence cannot establish absent
SG traffic. Integ20 source9c48b6f also predates bridge26acc22: its vendor lacks
that service. Missing bridge messages are expected for this image.

## Stock backend selection and UFS path

REA of stock librpmb.so, SHA256
364139c927b091df2a2c9b3b6ff02330be60ba3fdb17be9f88c3b8a66632b02b:

| Function/address | Relevant behavior | REA evidence |
| --- | --- | --- |
| rpmb_init 0x1066e4 | stat/readlink of bootdevice; recognizes ufshc/sdhci. On failure/unrecognized target it can cache backend0x7fffffff and initialized, yet return0. | ev_2ab06868f4c11bfcd7fda3eaf411f5d55c9b4940e6fedb6259eb0045ab657c68 |
| rpmb_read 0x1068d0 | Dispatches backend9 to UFS and3 to MMC; invalid backend returns-1. | ev_bf8fd920184bc049130ad7328203e8412c6c9db987bf0bc801220c87263b2068 |
| rpmb_ufs_init 0x106e68 | Enumerates SG devices, matches GET_IDLUN, then queries descriptor IDNs0/7/2. | ev_fa8d95d18fd9d90937fa7dafe2eaf7d2c5eb51909aa708c68ae20ad44ee34b65 |
| rpmb_ufs_read 0x1074dc | Allocation, open or SG_IO can fail before a request reaches the queue. | ev_7847f779197d138c52ed7595cc9a7bf2a52f7c0420efc26ebda430911c7ba9d5 |

The existing descriptor-query ABI and stock read CDB sequence match our source
interfaces. Actual request-frame type and backend selection are unknown from
integ20. No guard or query-argument change is justified by these logs.
The four complete original REA responses are retained at
`A9-build/mainline/fbe-rpmb-r4-20261024/librpmb-focused-REA.json`, SHA256
53186286f0ee1481eeac38fc8e6f2a2ad2f99800bf5fae93b482f316ab4e7a8b.
The similarly named post-close export is empty and is not evidence.

## Next build's milestones

The qseecomd preload observes only actual stat/readlink calls for the exact
bootdevice path. It issues no probes, retries or redirected calls, and preserves
arguments, output buffers, result and errno. It emits:

```
A9_RPMB_BACKEND op=N ok=B errno=N ufs=B mmc=B truncated=B
A9_RPMB_BACKEND_LIMIT op=N limit=7 dropped=1
```

Op1 is stat; op2 is readlink. Only successful readlink output is scanned, bounded
by returned bytes, caller capacity and256. UFS/MMC booleans identify only public
ufshc/sdhci substrings. Truncated is conservative when capacity/scan bounds are
reached; it does not prove the original call truncated. No path, target, device
identity or stat data is logged. Each op permits seven events and one limit
marker. Existing attestation-open observation has independent budgets.

Kernel records, retained directly in kmsg and forwarded by the independent
logcat bridge when available:

```
A9_RPMB_CALLBACK phase=1 command=N size_valid=B
A9_RPMB_CALLBACK phase=2 command=N rc=I status_valid=B qsee_result=S
A9_RPMB_QUERY idn=N rpmb_lun=B rc=I length_valid=B
```

Only RPMB listener0x2000 emits callback records. Command enums are unknown0,
init1, read2, write3, mapped from the first four LE bytes with a DMA barrier and
size check. No other packet fields are logged. Phase1 follows receive delivery;
phase2 follows the existing reply SCM call. qsee_result is QSEE result0, valid
only when transport rc0, not raw SMC a0 or an authenticated-read result.
Query IDNs are0/2/7; rc includes copy-out failure. Length_valid describes only
the existing returned-length allocation bound, not descriptor contents or a
new minimum-length check.

Guard discovery, outbound read requests, inbound responses and refusals now
have separate rate-limit budgets. Decisions and read-policy bytes are unchanged.
Allowed means submission permitted, not successful I/O. Each category can still
drop records; liblog submission/retention is also unacknowledged. Missing
records cannot establish success or absence of an operation. Backend hooks go
directly to logcat; kernel milestones use kmsg plus the bridge. The public
extractor accepts only the defined numeric forms and strips private payloads.

First inspect backend recognition, then callback delivery/reply, descriptor
query and independent OUT/IN/refusal milestones alongside the TA failure.
A successful stat/readlink is not proof that librpmb accepted its backend or
that UFS initialization completed. If the first missing stage remains ambiguous,
capture its actual return next; do not relax the guard or provision storage.
The separate attestation -10003 remains an SFS-open failure requiring its own
observed errno/backing path; IRPC absence alone remains expected on legacy KM4.

## Host validation

Production UFS/query fixtures and host/ARM64 command decoding pass. Both changed
ARM64 kernel objects, a9-qseecom.o and ufshcd.o, compile with the existing integ20
native flags; no full kernel/image build or hardware success is claimed. The
normal build already forces rebuild of both objects.

The bridge passes production-parser/queue/backoff checks and64 actual ARM64
datagrams with delayed logd/backpressure. C/Python agree on17 kernel fixtures;
userspace observer records remain excluded from the kernel bridge input.
Backend forwarding/redaction/caps and combined attestation-forwarding tests
pass natively and on Android ARM64 QEMU, with synthetic ext4 packaging and
unchanged policy compilation. The combined library SHA256 is
c9d6d2f2b0735cdffbdd2556b2fd3ff6728c2e4b23865651f9328c74f0e73fa9.
Results live under `A9-build/mainline/fbe-rpmb-r4-20261024/checks.json`,
`fbe-rpmb-r4-host/{bridge-tests-final,combined-attest-tests}/tests.json` and
`fbe-rpmb-backend-observer-host/tests/tests.json` in the same mainline directory.
