# Integ26 PIN: missing RPMB destination evidence

2026-10-25, host only. Release work is parked. No phone, TA execution, storage
query, RPMB operation or physical persist write was performed.

**This change adds diagnostics, not a PIN fix or write permission.** The saved
records cannot identify the two rejected writes' exact partition/address.
Counts2/34 alone do not prove per-entry KMTB access. Existing GK and per-entry
KMTB guards are unchanged; unknown maps, KMTB bulk clears, programming and other
forbidden writes remain blocked.

## Retained evidence

`scripts/mainline/reboot/credential-summary.py` on
`A9-build/mainline/sweep-integ26-f1-eos-y-20261024/` confirms PIN set0 and the
two/34-frame refusals. Neither boot127 nor128 contains any map/KMTB record.
The actual integ26 kernel contains `A9_RPMB_MAP` and all three KMTB write
strings, so the code was included.

Boot128 `kernel.log:1020..1029` has successful type4 read submissions around
21.38s without a map record. Lines6054..6070, around435.92s, show three34-frame
refusals within one write callback, then a counter read. Legacy listener
operation status0 after those refusals is not authenticated-write success.
The guard returns DATA_PROTECT before UFS submission; see [R6](mainline-fbe-rr6.md).

No destination, PTBL contents, raw request or top-level TA command was retained.
Therefore no exact absolute or partition-relative offset can be recovered, and
an exact private packet replay is unavailable. Missing KMTB records also do not
prove absence of KMTB attempts: candidate classification needs a learned map.

## Stock comparison

TA SHA256 `11c120cabd09e9caa48e85f6f7ce307ff7fe5f3a2b82a465f2afeaf4313bec43`;
TA addresses use REA base0x100000. TZ addresses0x100a... are secure VAs.

| Stock path | Proven behavior and implication |
| --- | --- |
| TZ PTBL loader `0x100a1da0` | Cached `DAT_10156a38` returns before table I/O. Cold path reads physical1/8 frames at address0. Evidence `ev_e824d3dd304b985c5088c92e080c99e5c7879addc847f39b2ccc9ad5a768ba55`. A passive observer cannot assume repeated PTBL reads. |
| TZ reader `0x100a0a94`, builder `0x100a3ff0` | READ input header24, offset+12=24, UFS type4/address0/positive counts; response frames at20. Evidence `ev_983c4fd0b9619e38b7a576c3da830305175dc5a87f6e8a3ce2293206ddd3e43a`, `ev_30533baef48cce6d3832c0abbbee24e7f44d1cb38a25afcc3b7336bcf76c875f`. Current dynamic-offset parser accepts this; older20-byte request fixtures alone are not the cause. |
| Counter refresh `0x100a0494` | Outer command0x102 can contain inner type2 rather than type4, including after failed writes. Evidence `ev_4d355cc12b0b78afe378bb63d505a915a63c5740d6d1d59656c66feec271b237`. Callback counts do not prove PTBL reads. |
| RR add/remove `0x115670` / `0x115864` | One logical512-byte sector inside KM-relative `[0x2000,0x5000)`, two physical payload frames. See [proposal](mainline-fbe-rollback-proposal.md) for entry formulas/evidence. Two frames fit RR but do not identify it. |
| GK header init `0x117efc`; writer `0x117dac` | GK header can write one512-byte sector at relative0. Error site `0x117df4..0x117e10` is `gk_write_platform:87`, its RPMB branch. The retained `:87:-2` supports GK RPMB failure but is not tied to an individual SCSI request. |
| GK delete-all `0x104760` → `0x112ae0` | TA command0x21004 clears0x2e00 bytes at GK-relative0x200:46 physical payload frames overall. This is not a per-entry KM mutation. |
| KM delete-all `0x1159a4` | Clears0x3000 bytes at KM-relative0x2000:48 physical payload frames. It remains forbidden. |

TZ writer `0x100a0f70` derives an even chunk capacity34 from mapped listener
length0x7000. With UFS reliable limit at least34, GK's46-frame clear splits34+12;
KM's48-frame clear can split34+14. Stock `rpmb_ufs_write`, REA0x107a28,
submits the selected group count (instructions0x107a60..0x107a80 and
0x107e90..0x107e98). Writer evidence:
`ev_ba9fa135550a2b6ebef96398693edae641099c2cf85c4e45d1f144a0ead3d931`.
Neither the reliable limit nor destination was retained, so these explain
possible size shapes, not actual attribution.

[AOSP gatekeeperd](https://android.googlesource.com/platform/system/core/+/refs/heads/main/gatekeeperd/gatekeeperd.cpp)
has `clear_state_if_needed()` emit the exact cold-boot message then call HAL
`deleteAllUsers()`; enrollment calls that helper first. Stock QTI selects
0x21004 at REA0x1051e0..0x1051ec, matching TA record0x132800. This strengthens
the GK bulk-clear hypothesis for PIN-stage34. Checked AOSP main is not proof
of exact deployed A16 binary equivalence or an unlogged physical destination,
and does not authorize unblocking a shared bulk clear.

No fixed base, new metadata probe, empty-table fallback or cache flush was
added. TZ reset `0x100a2160` is destructive: it zero-writes allocated storage
and metadata. It must not be used to refresh the map.

## Diagnostic records and privacy

The new records reach kmsg and the rebuilt strict logcat bridge. They include
public16-bit physical frame addresses to answer the requested offset question.
They never log key IDs, data, MAC, nonce, counter, pointer, TA name or opaque
PTBL context. All values are numeric/bounded; arbitrary suffixes are rejected.

```
A9_RPMB_REQUEST_TRACE command=3 shape=1 reason=0 request=3 frames=34 input_frames=34 offset=24 first=2050 count=34 map_valid=0 context=10 table_candidate=0 table_valid=0
A9_RPMB_WRITE_TRACE allowed=0 map_valid=0 groups_valid=1 partition=0 first=2050 offset=0 frames=34 context=10
```

**These are synthetic examples, not device addresses.**

REQUEST_TRACE snapshots before listener wakeup. `frames` is the outer response/
write count; `input_frames` counts submitted descriptors. Its `offset` is the
shared-buffer offset, not a partition offset. Inner `request`:2 counter read,
4 data read,3 data write. Counter requests print no unused address/count bytes.
`shape=0` reports the failing check:1 short,2 command,3 outer count,4 input
length,5 bounds,6 unexpected type;0 means valid. Unknown header/control values
are redacted. `table_candidate/valid` independently project the actual existing
PTBL request parser, even if stricter diagnostic checks fail. Neither flag
certifies response success or secure authentication.

WRITE_TRACE observes every bounded SCSI write attempt after unlocking.
`groups_valid` checks all reliable-group controls. `first` is a physical
256-byte address. Its `offset` is relative256-byte blocks only for a known
GK/KM extent; otherwise0 means unavailable. `partition`:0 unknown map/shape,
1 GK,2 RR window,3 other KM bytes,4 outside both extents,5 crossing a known
boundary. Classification grants nothing; `allowed` reports the existing guard.

`context` whitelists known Keymaster-app operations:0 unknown/non-Keymaster;
1 generate,2 import,3 upgrade,4 wrapped import,5 per-key delete,6 KM delete-all,
7 GK enroll,8 verify,9 delete-user,10 delete-all-users. It is set/cleared under
existing transaction/map locks. Caller context is not a partition label or
a permission bypass.

## Integration and host verification

Integ27 needs both the new QSEE driver/header and a freshly built
`a9-rpmb-diag`; the old logcat filter drops these tags. Existing
`qseecom/apply.sh` copies `driver/*`, and credentials packaging builds the
daemon from current sources. The extractor/credential summary accept both
tags with C/Python bounds agreement.

Next coordinator-owned capture must retain ordered records through PIN set:
counter versus data requests; absent PTBL versus malformed candidate/failed
publication; TA context and actual first address of each two/34-frame write;
and GK versus RR/prefix when a map exists. Unknown maps must keep denying
writes; neither first address nor count alone establishes a partition.

Native/ARM64 fixtures replay both retained **public shapes** with synthetic
addresses/payloads, reproducing unknown-map denial and no KMTB candidate.
They show the same sizes in different partitions,24-byte request layout,
private-byte independence, precise reasons and the reviewed table-flag case.
They are not exact private captures. Existing add/remove/delete-all/read-fail,
GK, listener, ABI, bounds and ring suites remain passing.

Production QSEE compiles with integ26 flags without warnings. The bridge passes
64 actual ARM64 datagrams, C/Python schema agreement, delayed logd/backpressure,
parser/queue and main-loop tests. Extractor and five numeric-summary groups
pass. Independent scoped review gives GO for diagnostics only. No full image,
hardware PIN success, authenticated live map or actual destination is claimed.

Evidence: `A9-build/mainline/rpmb-pin26-20261025/`, including `stock.md`, retained
disassembly/provenance, `review.md`, `kernel-compile/`, `fixtures-final/`,
`bridge-final-r2/` and `daemon/build.json`.

## Read-only map bootstrap for a cached secure PTBL (follow-up)

The follow-up removes the dependency on seeing a secure-world PTBL read in the
current Linux boot. Before delivering a pending RPMB write, `finish_call` now
loads an unknown map through the **same registered stock RPMB listener**. The
existing transaction mutex serializes the original TA call and every synthetic
read. No nested I/O or allocation runs in the UFS queue hook.

The driver saves the whole mapped listener allocation, including the original
signed request, and sends only outer `0x102` / inner `READ_DATA=4` callbacks.
It reads physical frame0; format `0x10100` needs that frame, while `0x10200`
needs frames0..7. The existing strict PTBL parser validates format, counts,
partition IDs, lengths, non-overlap and totals before publishing dynamic GK/KM
extents. There is one map-bootstrap attempt per Linux boot, success or read
failure. A published map is cached; existing explicit invalidation still
revokes it and does not grant another bootstrap attempt. Allocation/bounds
failure before any exchange performs no I/O and grants no permission.

Stock TZ loader `0x100a1da0` can skip reads using cached state at
`0x10156a38`; its ordinary reader is `0x100a0a94`, builder `0x100a3ff0`.
Stock `librpmb.so` dispatcher `0x105cd4..0x105db0` uses input frame offset24
and reply frame offset20. Importantly, `rpmb_ufs_read` at `0x1074dc` groups
requests by its private cached capability (`DAT_0010ad30`), rather than by
our chosen descriptor count. Therefore each synthetic callback requests
**exactly one physical frame** with block_count1. This works for every positive
stock cached limit, without a new capability/init command. A v2 map takes nine
such callbacks (frame0 detection, then frames0..7). The read builder validates
reply command/status/length/offset, response type/result and a fresh nonce per
callback. It neither queries nor increments the write counter.

If a pending, aligned two-frame RR update has a known per-key add/remove
context but no valid before-image, the driver also reads all48 RR frames,
again one callback per physical frame. Only a complete successful read can
populate the existing private mirror. This avoids treating a missing mirror as
an empty table when secure world cached the map. Before-image reads may recur
when that mirror is invalidated; the once-per-boot limit applies to the PTBL
bootstrap. A failed or partial read leaves the KMTB write blocked. The signed
original request is restored byte for byte before the stock write callback,
and scratch is erased. No synthetic reply is sent to secure world. On listener
abort an unreplied read still owns the mapping: the driver does not restore
or deliver the original write and instead fails the pending secure call.

Write predicates are unchanged: normal TA traffic confined to the discovered
GK partition, or an existing known-KM-context, unique single8-byte-ID transition
in one512-byte RR sector. KMTB delete-all, multi-entry changes, prefix/other
partition writes and RPMB key programming remain blocked. No map guesses,
metadata writes, signed-frame edits, secure-cache resets, persist writes or
new bypass are introduced.

The bootstrap trusts the stock listener/device as the existing accidental-write
confinement design does. **Normal world cannot verify RPMB HMACs.** In contrast
to passively observed reads followed by a successful secure continuation, these
new synthetic replies are not passed through the TZ verifier at `0x100a456c`.
Nonce/type/result/structural checks do not provide cryptographic authenticity
against a compromised listener/device; this is not a new authenticated-map or
malicious-normal-world security claim.

`A9_RPMB_BOOTSTRAP phase=N rc=R` is retained in kernel logs, the closed logcat
bridge, evidence extraction and numeric credential summary:

| Phase | Meaning |
|---|---|
|1|Map bootstrap begins|
|2|Initial physical-frame0 read completed|
|3|All eight v2 PTBL frames read (not emitted for legacy)|
|4|Full48-frame RR before-image read completed, when required|
|5|Final map-bootstrap result, including parser failure|
|6|Pre-I/O allocation or buffer-bounds refusal|

For the next run, an initial write trace may show map_valid0; it should then
show phases1/2[/3]/5 with rc0 and `A9_RPMB_MAP phase=2 valid=1`, followed by
`A9_RPMB_WRITE_TRACE map_valid=1`. GK destinations should classify partition1;
RR writes additionally need phase4 rc0 / `A9_KMTB_READ phase=3 valid=1` if the
mirror was missing, and the existing `A9_KMTB_WRITE` checks must pass. A missing
map no longer depends on a TA PTBL callback. This addresses that code-path gap;
the lost integ26 destinations and actual PIN outcome still require hardware
evidence. Build both the kernel and updated `a9-rpmb-diag` schema for integ27.

Host validation: `qseecom/tests/bootstrap.py` compiles production map publication,
exchange and preflight with a recording synthetic listener. It covers no-prior-
PTBL writes with2/34 frames, both table formats, all positive cached limits1..49,
cache reuse/revocation, read/type/result/nonce/length/PTBL failures, partial RR
failure, abort ownership, full mapped-byte preservation and scratch erasure.
Existing GK/KMTB tests preserve per-entry restrictions; native execution and
ARM64 compilation, the actual kernel driver object compile, logcat bridge64-
datagram replay and evidence/credential-summary tests passed. The fixture
stubs kernel scheduling and does not establish phone runtime/PIN success.
