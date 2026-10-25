# RPMB R6: INTEG23 guard refusal and scoped Gatekeeper writes

Host-only analysis of the saved INTEG23 boot. No phone, storage request,
provisioning, key programming or KMTB write was performed. Physical persist
remains read-only. Normal TA authenticated data I/O is the requested behavior,
subject to the user's continuing prohibition on KMTB writes and key programming.

## Cause established from code and retained records

The backend-link repair worked: stat/readlink recognize UFS, descriptor queries
0/7/2 return0, and listener registration phases4/5/6 and SCM succeed. The RPMB
W-LUN has an SG node. Stock librpmb's region0 SECURITY PROTOCOL IN/OUT CDBs,
GET_IDLUN selection, query ABI and the mainline W-LUN mapping agree. Neither
registration nor device discovery is the current blocker.

At boot148.799s a write callback is delivered; at148.812s the guard rejects a
request with reason3. Discovery/control requests and further refusals follow,
then the write callback replies. The pattern repeats, including the write
sequence around186.49s during the PIN attempt. Read/counter request OUT and
response IN submissions are also retained. Logcat wall-clock adjustment makes
global wall-time subtraction unreliable; kernel monotonic records and local
message order are the correlation basis.

The selected mainline ufshcd_queuecommand refusal path builds SCSI
**DATA_PROTECT, ASC0x27/ASCQ0 (WRITE PROTECTED)**, completes the command and
returns **before DMA or the UFS state switch**. It does not inject DID_ERROR.
Stock librpmb reports any nonzero SCSI status, host status or driver status as
`SCSI error occurred!!`, with retries and REQUEST SENSE. Thus this diagnostic
does not establish a physical UFS fault; the recorded refusals explain the
write-path errors. There is no supporting evidence for changing the CDB,
descriptor ABI, node permissions or W-LUN route.

Stock rpmb_ufs_write, ELF0x7a28, submits multi-frame OUT batches, then a
single-frame result request and IN response. The INTEG23 guard allowed only
single-frame read/counter OUT. Reason3 can mean a multi-frame OUT, a bad byte
count, a direction mismatch or an unavailable frame copy. The old record
cannot distinguish these subcases. Multi-frame write rejection fits the code
and sequence; the new metadata below will identify the exact subcase.

## Passive diagnostics now available

```
A9_RPMB_REJECT reason=N opcode=N direction=N bytes_valid=B length_match=B frames=N frame_valid=B request=N
A9_RPMB_RESULT command=N status_valid=B operation_rc=I
```

REJECT accompanies existing guard refusals. Opcode is other0/IN1/OUT2;
direction is none0/in1/out2/unsupported3. Bytes_valid means a nonzero multiple
of512; length_match compares the CDB's declared transfer with the buffer size,
including INC_512. Frames is the buffer frame count, or0 for invalid size.
Frame_valid means exactly512 bytes were copied for inspection. Request is the
first frame's known RPMB request type1..5, or0 for unknown/unavailable. Unknown
packet bytes are never printed as a number.

For a well-formed multi-frame data write, expect reason3/opcode2/direction2,
bytes_valid1/length_match1, frames greater than1, frame_valid1/request3. This
distinguishes that policy refusal from malformed lengths or failed inspection.
Only the first frame is inspected, the temporary copy is cleared, and no
address, sector, counter, nonce, MAC, payload or key is logged. The existing
read-policy decisions were unchanged in diagnostic commits3913437/9d6f049.
The subsequent scoped policy below replaces the blanket data-write refusal.

RESULT is the stock listener's signed LE32 operation status at response
offset4, sampled for read2/write3 after its reply and before the response SCM.
Short/null buffers produce status_valid0/operation_rc0; that is unknown, not
success. Aborted listener waits do not emit this record. A library operation
rc0 is distinct from QSEE callback acceptance and still does not establish TA
authentication of a returned frame. Stock dispatch evidence for this ABI is
`ev_bef6b1dd71df3dc936392f5d724403e7a7668f431bfbacd27d9325dde30b6bdc`
at Ghidra0x105bbc.

Both tags go to kmsg and the independent logcat bridge, and are accepted by
the strict offline extractor and numeric credential summary. Per-callsite
logging and transport retention remain bounded; absent records are not proof
of absent I/O. Fields, bounds and trailing-payload rejection are checked by
the C and Python parsers.

## Recovered dynamic partition format

Allowing every authenticated DATA_WRITE frame would remove the observed
refusal, but could also permit KMTB changes. A frame request type alone cannot
identify GK versus KM storage. The TA's partition IDs, allocation sizes and
handle-relative offsets do not establish physical RPMB ranges. In particular,
KM allocation size0x28 does **not** prove that the first40 physical blocks are
the protected region. A top-level Gatekeeper command is not a proven physical
partition label either.

The lower implementation was recovered from stock TZ, SHA256
`13e664bf4a20d32cd8e5e14584b9a33ca891dc9398ef7dea7657dff7f1faaf7e`.
Its embedded common library uses a CBOR container rather than a nested ELF.
An explicitly synthetic analysis carrier maps its unchanged code/data; it is
not presented as an original ELF. The standalone common-library dumps are
zero-filled and did not contain the implementation.

| Stock TZ address | Proven behavior | REA evidence |
| --- | --- | --- |
| `0x100a1da0` | Loads plaintext `PTBL`: LE32 magic0x4c425450, stable format0x10100/0x10200, allocation sum, record count. Records start at+16, stride16: LE32 partition ID, LE16 start/length, eight opaque context bytes. Legacy has15 records; v2 has127. | `ev_e824d3dd304b985c5088c92e080c99e5c7879addc847f39b2ccc9ad5a768ba55` |
| `0x100a146c` | Allocates dynamic extents after prior entries. Reserved logical prefix is1 sector for legacy,8 for v2. Allocation counts do not establish KM/GK bases. | `ev_d894212ae0ca39f6fde0ba3ea8155677943d09b8edd7b37d6d5bd22baa90e8d5` |
| `0x100a41c8` | Converts logical512-byte starts into physical256-byte addresses by multiplying by2. Each reliable-write group repeats its address/count/counter; the next group advances by its frame count. | `ev_b8e362438c45e46ef8a1e89c42a4984d485db6c72ec98d651b684c95bac3f9a7` |
| `0x100a3ff0` | For UFS kind9, builds type4 descriptors whose contiguous ranges and summed counts cover the requested response frames. | `ev_30533baef48cce6d3832c0abbbee24e7f44d1cb38a25afcc3b7336bcf76c875f` |
| `0x100a0a94` | After secure verification, copies each raw512-byte response's256 data bytes at+228 directly into the table buffer. No intervening decryption. | `ev_983c4fd0b9619e38b7a576c3da830305175dc5a87f6e8a3ce2293206ddd3e43a` |

The stock read listener at0x105bbc returns raw frames at shared-buffer+20,
signed operation status at+4, returned byte count at+8 and frame offset at+12.
The input header and request frames must be inspected before userspace reuses
that storage. Secure verifier0x100a456c checks nonce and HMAC. Its validation
is not visible to a normal-world parser and is not inferred from library rc0.

## Scoped policy

The driver observes **existing** table reads; it issues no extra reads or
writes. Before waking the listener it validates every bounded UFS type4 request
descriptor: starts must cover physical frames0..N-1 contiguously, counts must
sum to the outer1/8-frame count, and each descriptor must fit the shared buffer.
Beginning a new table read invalidates the old map. After the reply it checks
command0x102, library rc0, offset20, exact returned length, response types/results,
stable PTBL format, counts, allocation sum, unique live IDs, disjoint extents,
reserved-prefix exclusion and address overflow. Tombstones retain occupied
extents and may repeat IDffffffff. Both existing KM and GK entries are required.
Failed, partial or unrecognized maps grant no writes. Only GK's physical range
is retained; table contents, addresses and opaque context bytes are never logged.

The candidate range is published after the response SCM succeeds with a
recognized continuation status. Failed continuation, abort or fatal QSEE
status revokes it. This does **not** mean the TA authenticated the table.

SECURITY PROTOCOL OUT must retain stock's exact CDB shape, UFS RPMB protocol,
region0, direction and byte count. Every DATA_WRITE frame is inspected. A
reliable-write group must repeat its address/count/counter/type, fit completely
inside GK, and advance contiguously to the next group. The batch is capped at49
frames by the stock0x6400-byte listener capacity. Any mixed, short or overflowing
group is rejected. PROGRAM_KEY, KMTB, all other partitions and the entire
allocation-metadata prefix remain write-protected. RESULT_READ is admitted as
a nonmutating control needed to retrieve an allowed write's result. There is
no generic write-enable switch, partition creation, KMTB update or provisioning.

This is protection against accidental stock TA writes under the **stock TA
and stock listener assumption**. Normal-world code lacks the RPMB HMAC secret;
this mechanism does not authenticate a map supplied by a compromised listener.
The secure TA retains its own nonce/MAC checks. Hardware PIN success remains
unverified until the coordinator's next run.

The next run should retain `A9_RPMB_MAP phase=2 valid=1`, then
`A9_RPMB_GUARD mode=2 allowed=1 operation=5 reason=0` for GK writes, operation6
for result requests, and `A9_RPMB_RESULT command=3 status_valid=1 operation_rc=0`.
Map phase1 means invalidation; phase2 means reply processing, with valid0/1.
All these records reach kmsg, logcat and the numeric summary. No address or
payload is emitted. Confirm the complete PIN set/verify/lock/unlock/remove
lifecycle separately. Unknown maps should retain refusals. Rollback resistance
may still fall back because **KMTB writes remain forbidden**; that is not a
reason to widen this GK-only allowance.

## Host verification

Native and ARM64 fixtures pass for the existing guard, first-frame metadata,
length overflow, short copies, known-type redaction and unchanged request
buffers. New scoped fixtures cover dynamic legacy/v2 maps, tombstones,
overlap/malformed maps, every request descriptor, reply bounds/command/status,
SCM map revocation, stock write groups and forbidden KM/metadata/programming
ranges. Native and ARM64 compilers accept them; AddressSanitizer and UBSan pass.
Listener-result
fixtures cover signed32 extremes and short/null buffers. The production bridge
passes parser/queue/backoff tests, C/Python agreement and64 actual ARM64 logdw
datagrams including the new tags, with delayed logd and backpressure. Extractor
and five numeric-summary fixture groups pass.

Both a9-qseecom.o and ufshcd.o compile with selected INTEG23 native flags.
Its original object tree was pruned, so the check borrows prepared generic
generated headers and reconstructs autoconf from the exact selected config in
a private output directory. Frozen build sources remain unchanged. No full
kernel link, image build or hardware qualification is claimed.

Host artifacts are under `A9-build/mainline/rpmb-scsi-r6-20261024/`, including
stock transport and partition-protection reports, UFS fixtures, bridge tests,
kernel compile commands/method, and sanitized public events. Listener-result
fixtures are in `rpmb-scsi-r6-result-20261024/`. Scoped-policy fixtures, bridge
tests and repeated kernel object compiles are in `rpmb-scsi-r6-gk-20261024/`.
Independent review covers the changed NV allowance; original records and
closed-session exports retain address and Evidence-ID citations.
