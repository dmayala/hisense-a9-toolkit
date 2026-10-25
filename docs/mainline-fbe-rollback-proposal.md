# Proposal: scoped stock KMTB rollback-resistance writes

**Subsequent decision, 2026-10-24:** the user approved shared stock per-entry
add/remove, with delete-all/multi-entry changes, programming and other forbidden
partitions still blocked. See [implementation and verification](mainline-fbe-kmtb-guard.md).
The text below preserves the earlier proposal and its evidence/limitations.

**Analysis and decision proposal only. Not implemented or authorized for a
phone run.** This change adds documentation only. No KMTB-writing code, guard
allowance, storage request, TA execution, provisioning or secure-state mutation
was created or performed. The existing GK-only policy and physical persist RO
remain unchanged.

Stock per-key allocation normally preserves occupied entries, but the code
does **not** prove unconditional isolation from stock slot A. Both systems use
one flat table, with no slot ownership field, unchecked random64-bit IDs,
shared capacity and sector-sized read/modify/write. Global delete-all is
destructive across slots. A range-only KMTB allowance would be insufficient.

## Exact stock layout and writes

Actual stock Keymaster TA SHA256:
`11c120cabd09e9caa48e85f6f7ce307ff7fe5f3a2b82a465f2afeaf4313bec43`.
Keymaster addresses below use REA base0x100000; subtract it for original ELF
VAs. Secure-core addresses beginning0x100a are actual TZ VAs, not that base.
No live table, key ID, blob or physical extent was read for this proposal.

| Object | Proven layout |
| --- | --- |
| Partition | ID0x4b4d5442, conventionally KMTB/KM. Stock setup0x1183cc opens it; its allocation request is40 logical512-byte sectors. PTBL determines its physical base dynamically. |
| RR key-ID table | KMTB-relative bytes[0x2000,0x5000), logical sectors[16,40):12,288 bytes. Sectors0–15 are outside this proposal, not assumed unused. |
| Entry | Eight-byte little-endian ID;1,536 entries. Zero is the empty marker. No per-entry owner/slot, bitmap, count or table-magic check appears in the inspected table routines. The separate PTBL directory has its own format. |
| Write unit | One logical512-byte sector contains64 IDs and becomes two physical256-byte RPMB data frames. It is not an eight-byte hardware write. |

If `B` is the dynamic KM physical256-byte start, the entire RR table is
`[B+32,B+80)`. Entry index `i` is in logical sector
`16+floor(i/64)`, physical blocks
`[B+32+2*floor(i/64), B+34+2*floor(i/64))`.
These are relative formulas, never fixed device addresses.

The following traces identify the direct RR-ID mutations. They do not grant
access to other private TA storage effects; all writes outside this proposal's
window and operation scope would remain denied.

| Operation | Stock mutation and timing |
| --- | --- |
| Generate | Modern TA command0x2108, handler0x104a54, calls shared finalizer0x10490c and addkeyId0x109128 before the algorithm-generation callback at0x104b58 and blob serialization at0x104ba8. With RR requested, it inserts one ID and writes its containing512-byte sector first. |
| Import | Command0x210b, handler0x105b90: same insertion at0x105c74, then import callback0x105cb8 and serialization0x105d08. |
| Wrapped/secure import | Command0x2117, handler0x10e154: same insertion at0x10e1f4, then import callback0x10e298 and serialization0x10e2e8. |
| Upgrade | Command0x2114, handler0x1076ac: checks the old RR ID, updates boot/patch fields, then creates a new random ID and serializes. No old-ID removal appears in the inspected upgrade body. It can consume another slot. |
| Per-key delete | Table helper0x115864 finds the first equal ID, sets that cached uint64 to zero, and writes exactly its containing512-byte sector. Missing ID returns-33. There is no slot filter or duplicate sweep. |
| Delete-all | Helper0x1159a4 zeros all12KiB in RAM and writes all24 logical sectors. It need not first read the existing table. It is excluded from the proposal. |

`addkeyId` requests eight bytes from `qsee_prng_getdata` once. Failure to obtain
eight returns-49. With RR false it skips table insertion. With RR true,
`km_add_key_id`0x115670 lazy-loads the table, chooses its first zero, changes
cached RAM, and writes one containing sector. Full returns-56 internally;
the outer wrapper maps every nonzero insertion result to-67. Therefore -67
does not distinguish full capacity, read failure, guard refusal or write failure.

Initial read0x11602c allocates0x3000 bytes and reads at offset0x2000.
Raw read0x117cd8 and write0x117d68 use the KM handle, translating bytes into
512-byte sectors; sector-size getter0x117cd0 returns0x200. A nonzero storage
API result becomes-8. Table bytes pass to the existing secure RPMB frame builder;
no new normal-world key generation or MAC computation is proposed.

Check/remove/delete-all can return0 without mutation when their storage-ready
test is false. The legacy HAL also treats a64-byte FBE blob specially: it
issues clear-key ioctl0xc048972c and returns0 even after logged ioctl/open
failure, instead of calling the ordinary KMTB delete path. Therefore generic
deleteKey success is not an unconditional secure-entry reclamation receipt.

Insertion precedes algorithm/output success. Failure cleanup in the inspected
generate/import paths frees the object without removing the inserted entry.
The HAL can also fail allocating the returned blob after TA success. Failed
creation is consequently not evidence of an unchanged table, and retries can
consume additional slots. Upgrade's old/new-ID retention has the same capacity
implication. This trace does not establish every later cleanup path.

## What can be proved about stock-A coexistence

For a successful ordinary add with a current, correctly authenticated table
image, the chosen entry was zero. The sector image changes that entry and
preserves its other63 IDs. Thus distinct nonzero A/B key IDs can coexist, even
within one physical write sector. There is no intentional replacement of an
occupied ID in the inspected add routine.

That conditional statement cannot be promoted to an unconditional A-safety
guarantee:

- **No slot namespace.** The flat table cannot identify which OS, Android user
  or app owns an entry. An Android boot-slot suffix does not partition RPMB.
- **Collision handling is absent in the inspected path.** Neither the wrapper
  nor insertion rejects a zero/new duplicate ID or retries on collision.
  Deletion removes the first equal ID; check0x1157a4 succeeds on any equal ID.
  A surviving duplicate can keep a deleted blob's ID recognized, and deletion
  can clear the other key's first matching entry. Check(0) can match an empty
  slot. No such collision or zero-ID key was observed on this device.
- **Random is not a proof of uniqueness.** Assuming uniform independent64-bit
  PRNG output and `n` occupied IDs, collision probability for one add is at most
  `n/2^64`, plus the separate zero-sentinel probability. At full capacity,
  `1536/2^64` is approximately8.3e-17. This is conditional arithmetic, not an
  entropy audit or a no-collision guarantee.
- **Deletion has no provenance filter.** Stock modern deleteKey sends the
  supplied blob via0x210d; deleteAllKeys0x210e carries no app/slot selector.
  If B has an A blob through shared/restored userdata, the table alone provides
  no reason to refuse deletion of its ID. Bulk reset affects all entries.
- **Capacity and write failures are shared.** B can consume free slots needed
  by A. Each update also rewrites neighboring IDs from cached RAM. Failure and
  stale-cache behavior must be considered, not just the changed entry's size.

Consequently, **the “per-key, therefore independent” hypothesis is only partly
proved**. Normal distinct-ID additions preserve existing entries; absolute
collision freedom and slot ownership are not implemented by this stock format.

The shared RPMB write counter is transport replay state. The inspected key-ID
lookup compares table IDs, not a per-key saved RPMB counter, so counter advance
alone is not an ID deletion. Each boot still needs valid stock authenticated
I/O; this is not a broader proof of cross-version keyblob/boot-policy compatibility.

Filesystem backup/restore cannot undo an RPMB deletion. Raw userdata format
does not issue per-key secure deletes and may destroy the blobs needed to
reclaim their entries. Reference Keystore GC even postpones irreversible
deleteKey until boot completion because filesystem checkpoint rollback cannot
undo it. Its cleanup can drop the database blob after a failed hardware delete.
See [GC implementation](https://android.googlesource.com/platform/system/security/+/ce2aa0ef49158d0d45fb133fd42ab1deff210f8c/keystore2/src/gc.rs#113).
No factory-reset path is proposed as recovery.

## Failure modes and recovery limits

| Failure | Stock behavior established from code | Recovery implication |
| --- | --- | --- |
| Table full | All1,536 slots nonzero; insertion returns-56, exposed as-67. | Reclaim only entries through known valid key blobs and successful per-key deletion. Lost/orphaned IDs cannot be safely attributed to B from table contents. Never evict an arbitrary/A entry, delete-all or initialize the table to make room. |
| Read/authentication failure | Read error becomes-8/-67. Cache pointer can remain published after a failed initial read; later calls may skip reload. | Keep KMTB writes denied. A later cached path is not a fresh authenticated-read receipt. No zero-filled substitute or “empty table” inference. |
| Entry write failure | RAM entry changes before I/O, with no cache restoration in the inspected add/remove helper. | Do not flush that cache or blindly repeat creation/deletion. Physical state may differ from RAM. Stop mutations and require supported fresh secure-state/table reload and reconciliation; a normal-world daemon restart is not proven to reload the preloaded TA. |
| Counter/result mismatch | Secure writer validates the write response, then attempts an authenticated counter refresh on failure. | Successful refresh supplies the next counter; it does not undo data or make the failed operation successful. Failed refresh establishes no resynchronization guarantee. Never guess/decrement/program a counter. |
| Lost result, timeout, interrupted batch | Stock listener may resend already-prepared frames; earlier groups may already have committed. | Treat completion as uncertain. Do not add host retries, rewrite frames or restore an old table image. A fresh authenticated read can reconcile state; it cannot resurrect a deleted RR ID. |
| Failure after successful insertion | Algorithm, serialization or HAL output-allocation can fail later. | An orphan may remain without a returned blob. Repeated attempts risk capacity leakage. Neither final error nor non-RR fallback proves rollback of the first attempt. |
| Duplicate/zero ID or unexpected table data | No table-header recovery protocol exists in this flat region. | Refuse a new colliding/zero insertion in a proposed guard; ambiguous deletion needs a decision, not automatic duplicate cleanup. Never normalize unknown entries to zero. |

The secure writer0x100a0f70 builds stock frames with0x100a41c8, invokes listener
0x2000 and validates the response with0x100a4a3c. That verifier checks successful
write-result type0x0300, result, MAC and the expected advanced counter. Counter
mismatch returns-10; MAC mismatch returns-9. The builder advances its cached
counter during preparation, before I/O, once per reliable-write group.

The failure epilogue attempts0x100a0494: an authenticated counter read, with
nonce/MAC verification by0x100a456c, then setter0x100a4d1c updates the cache.
Refresh failure is ignored while the original write error is returned. This
resynchronizes future preparation when successful; it is not table rollback
or automatic replay of the application mutation.

Stock librpmb's three-attempt SCSI retry uses the already-prepared frames;
it does not recompute their counter/HMAC. A counter can already have been
consumed when a result transfer is lost. No whole-table or whole-operation
power-loss atomicity was established. Even a one-sector update consists of
two physical data frames, whose grouping depends on reliable-write capability.
Do not infer measured device atomicity from the eight-byte entry size.

## Smallest proposed guard extension

**Destination-only widening is too broad.** Allowing all of KMTB, or even only
its RR24-sector window, also admits delete-all and arbitrary replacement of
neighbors. A literal per-entry add/remove allowance requires the following
state in addition to the existing group/CDB checks:

1. Retain the KM extent from the existing validated stock PTBL observation.
   Require an existing sufficiently large partition and checked unit/range
   conversion. Derive only its RR window; never admit partition creation,
   metadata writes, KM sectors0–15, other partitions, PROGRAM_KEY or bypasses.
2. Observe the stock TA's existing RR-table reads and retain a private RAM
   before-image, with request/response bounds and all descriptors validated.
   Full lookup/collision checking needs12KiB, not just one sector. Do not issue
   extra storage requests or log IDs/table bytes. Cache validity must fail closed
   across failed reads, unknown TA/session state or uncertain completion.
3. Bind the candidate to a positively identified stock Keymaster per-key
   transaction. Proposed initial scope is generate/import plus deleteKey.
   Explicitly reject deleteAll/reset/reinitialization and unknown commands.
   Wrapped import and upgrade can be separately included after their multi-step
   effects are accepted; they are not silently covered by a region whitelist.
4. Accept exactly one aligned logical512-byte sector update changing exactly
   one eight-byte slot: zero to a nonzero ID absent from the complete table, or
   one uniquely present nonzero ID to zero. Every other byte must equal the
   before-image. Reject nonzero-to-different-nonzero, multiple changes, unknown
   or partial images, and bulk operations even if a sparse table makes a bulk
   clear superficially resemble one deletion.
5. Validate both physical frames and every reliable-write group using the
   existing strict CDB, region0, length, repeated-header and range rules.
   Preserve stock authenticated frames unchanged, including counters/MACs and
   RESULT_READ. Never synthesize, split, trim or repair the signed transaction.
6. Track pending writes through the stock result path; distinguish exact stock
   transport retransmission from a new mutation. Do not advance the before-image
   merely because OUT was submitted or callback operation_rc is0. Unknown
   completion invalidates write permission pending supported secure reload.
   A later high-level failure can still leave an entry committed.

These are proposed semantics, not an implementation or a claim that all
necessary integration is already proven. Exact top-level command/result
observation and cache lifecycle need implementation review before any approval
could become a phone run. In particular, stock loads the ID table lazily;
requiring an extra physical read for every add would change stock behavior,
while treating a cached pointer as successful read validation would be wrong.
The duplicate/zero rejection is deliberately narrower than stock's unchecked
edge cases: refuse the request without changing its ID or authenticated bytes.

As with the GK map, normal-world code does not possess the RPMB HMAC secret.
The proposal relies on the unmodified stock TA and listener, with secure
verification retained. Parsing listener bytes cannot authenticate a malicious
before-image. SCM0, callback0 and a library operation0 are separate from the
TA's successful nonce/MAC verification. This is an accidental-write confinement
proposal under that trust assumption, not a new secure-world enforcement layer.

## Additional policy needed to protect stock A

The preceding minimum restricts mutation shape, but **cannot infer ownership**.
There are two concrete decision scopes:

| Scope | What it allows and preserves | Limit |
| --- | --- | --- |
| Shared stock per-key semantics | Unique-ID additions and targeted removals through the stock TA, with single-entry diff enforcement and bulk reset blocked. | It cannot guarantee that a valid blob presented by B does not refer to A's entry. Shared capacity and normal secure-deletion irreversibility remain. |
| Conservative bounded B trial | At the first healthy observation, protect every occupied slot. Permit additions into empty slots and removals only of entries newly added by that admitted trial. Preserve all baseline IDs byte-for-byte. | Baseline includes any older B entries too. Volatile provenance is lost after reboot; this does not support arbitrary later cleanup or repeated userdata formats without leakage. |

**Recommendation for an initial decision:** if preserving every existing A
record is required, choose the bounded-trial scope, with a small agreed entry
budget and cleanup limited to its known newly created keys. Do not approve an
address-only allowance as “A-safe.” Durable B ownership across boots/formats
would require an additional trustworthy lifecycle/provenance design; a missing
normal-world journal cannot be interpreted as permission to delete old IDs.
No such journal or ownership mechanism is implemented here.

The decision must also accept that failed creation can consume a slot before
a blob exists, and that an uncertain physical write cannot be undone from a
userdata backup. Keep the existing non-RR fallback when the narrowed operation
cannot be admitted. KM4 requires an unsupported RR request to fail with-67;
the framework may explicitly retry without RR, retaining its other auth policy.
See [KM4 contract](https://android.googlesource.com/platform/hardware/interfaces/+/3fe3ba022e4da87110ca194b3ce6ad03f3c86e23/keymaster/4.0/types.hal)
and [SP fallback](https://android.googlesource.com/platform/frameworks/base/+/android-16.0.0_r1/services/core/java/com/android/server/locksettings/SyntheticPasswordCrypto.java#217).

## Evidence and review status

Static analysis only, including ordinary disassembly backed by exact REA code
bytes where Ghidra omitted function records. No runtime content was inspected.

| Claim | Address / primary evidence |
| --- | --- |
| First-empty allocation / random ID | 0x115670 /0x109128; R5 `ev_ef283e7db2680984cd9ff79b69b385fcbe06e5a03926b1281c6ac645d720ccab`, `ev_589fd9927693386b2fd76af0677e62183b1f25d3e6dff75707b8345aa76f118d` |
| Create and imports precede algorithm/output | `ev_e4208111c490c540d7e4305bed87513b945842afe6f8265f16248abf7743d778`, `ev_f0fffd28c70faf8ff5ffd7b5a6676af7f8c855c27ab1a919301318bba2a8e601`, `ev_ef76d3dd36aa882c4a416203e6117f4bea7041f1dff58b5a16c92a0bdeb21fde`; modern handler records `ev_9a821c729f9f0a52cacb5d5acb931e6bb395a68b6d56c22f1e3b537dffafc9be` |
| Single delete / global clear | 0x115864 /0x1159a4; exact code `ev_245b5727bc46924b4d8d36d5c12e0cb25756ff1da62e582b64bf9c611ce4ed5f`, write ranges `ev_b31a9b69585e5fb62722ba5e0fd24dcd88b0013a72a24fb974ae8bfc2305b7ec` |
| Raw write translation | 0x117d68; `ev_8a78e70dd99c01bc0ad9ea96b2b7985aff55ee791bb00a1a5824b6dfe82f4a95` |
| HAL per-key / unfiltered bulk delete | 0x112500 /0x1125e4; `ev_37e53aeef31a2932557481d19941cdf1e3832ff7a3f3152b1266658cccb6ea11`, `ev_52c416ebb46c97fbf89c329aaee47e43f257f9416bbbcfd0067e3e84712667d1` |
| Write-result verification / counter refresh | TZ0x100a4a3c /0x100a0494; `ev_ac2943c939208c895f05555a37d964b6a2eafca439cb830b6a8cde9a01e97cc6`, `ev_4d355cc12b0b78afe378bb63d505a915a63c5740d6d1d59656c66feec271b237` |

Full reports, immutable-target identities and original ledgers:
`A9-build/mainline/rollback-proposal-20261024/{km-table-report.md,
hal-contract-report.md,rpmb-failure-report.md,km-rea/,km-rea-creation/,
hal-rea/,rpmb-rea/}`. Target-scoped exports are authoritative; an early shared
output-name collision is documented in their recovery manifests. All sessions
exported and closed. Prior layout/guard evidence: [R5](mainline-fbe-rr5.md),
[R6](mainline-fbe-rr6.md). No implementation or hardware qualification is claimed.
