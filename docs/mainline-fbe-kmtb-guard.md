# Stock per-entry KMTB allowance

2026-10-24. The user approved stock Keymaster's per-entry KMTB add/remove
writes. This implementation narrows the existing RPMB guard to that mutation
shape; it does not create entries, choose IDs, issue extra storage requests or
rewrite authenticated frames itself. Host-only work: no phone, TA execution,
RPMB I/O or persist writes were performed.

Integ26 still failed PIN set without any map/KMTB records. The
[PIN26 follow-up](mainline-fbe-pin26.md) records the cached-map limitation,
ambiguous two/34-frame writes and additional diagnostics. It grants no
additional write permission.

[The proposal](mainline-fbe-rollback-proposal.md) records stock layout, function
addresses, failure behavior and shared-A/B limitations. This implements the
approved shared per-key semantics, including the fact that the format has no
boot-slot ownership field. It does not claim that a valid A blob presented to B
cannot be deleted, or that shared capacity/irreversible deletion disappears.

## Scope and enforcement

PTBL observation still requires the existing validated stock table read and
recognized SCM continuation. The map now retains the KMTB partition as well as
GK. KMTB's numeric partition ID is `0x4b4d5442`; its physical base comes from
PTBL, never a fixed device address. Only its relative byte window
`[0x2000,0x5000)` is eligible: 24 logical 512-byte sectors, 1,536 eight-byte IDs.
One logical sector is two physical 256-byte payloads in 512-byte RPMB frames.
The metadata prefix, KMTB sectors 0–15, non-RR KMTB bytes, partition creation,
RPMB key programming and previously forbidden partitions remain blocked.
The existing independently scoped GK allowance is preserved.

The new policy has four boundaries:

1. **Before-image from existing reads.** Retain a private 12 KiB RAM mirror
   only from the stock TA's complete RR-table read. Validate every request
   descriptor, bounds, exact coverage, response type/result and listener
   length/status. Stage the reply before SCM can reuse its buffer, then publish
   only on a recognized successful SCM continuation. A partial/malformed KM
   read, failed storage operation, fatal/unknown continuation, map epoch change,
   Keymaster unload or uncertain write invalidates permission. A TA error after
   a read also invalidates the mirror; successful transport alone is insufficient.
2. **Known per-key transaction.** App identity is the stock `keymaster64`
   query/load name. Request command is LE32 at offset 0. Explicit known generate,
   import, modern upgrade/wrapped-import commands select add; explicit per-key
   delete commands select remove. Legacy/modern security-level aliases are
   enumerated, rather than admitted by a bit mask. Unknown commands and all
   delete-all aliases select neither. Therefore a sparse bulk clear cannot
   masquerade as one permitted removal.
3. **Exactly one entry.** Before listener wakeup, require one aligned logical
   sector (two signed physical frames), both within the RR window. Compare its
   512 payload bytes with the mirror. Exactly one aligned eight-byte slot must
   change: zero to nonzero for add, or nonzero to zero for remove. All other
   bytes must match. Reject no-op new mutations, replacements, multiple changes,
   duplicate insertions and ambiguous removals. The complete mirror checks ID
   uniqueness; IDs never leave private kernel RAM through this mechanism.
4. **Unmodified transport and observed result.** Stage both complete signed
   frames, including opaque MAC bytes. Every SCSI allowance must match an exact
   staged reliable group, with stock address/count/type/result/counter rules and
   the strict original region-0 SECURITY PROTOCOL OUT CDB. Exact stock SCSI
   retransmissions can pass; no new host retry is added. Commit the mirror only
   after both frames were admitted, a bounded successful `0x0300` write result
   reports the expected advanced counter, and SCM continues successfully.
   Missing/failed/uncertain completion invalidates the mirror. A high-level
   algorithm failure after a committed write does not restore the old sector.

Delete-all is blocked both by command classification and transfer size. The
stock bulk helper clears 12 KiB, whereas the allowed update is one logical
sector. Multi-entry zeroing within even that one sector is also blocked.
The guard never synthesizes MACs, changes a counter, splits/trims a transaction,
substitutes zero data for failed reads, or initializes a table to recover.

Normal-world parsing cannot verify RPMB HMACs. This remains accidental-write
confinement under the existing unmodified stock TA/listener trust assumption;
secure-world nonce/MAC verification remains in stock. A failed read cannot
be treated as an empty table. If the TA retains a stale lazy cache after failure,
new writes remain denied until a supported fresh table observation is available.
No automatic TA restart or recovery operation was added.

## Stock ABI evidence

The request/status offsets were checked independently against the saved stock
HAL and its actual transport dependency:

| Artifact / function | Observation |
| --- | --- |
| `libkeymasterdeviceutils.so`, SHA256 `1231b993bde4c65982a7c8a22233e7063152596580e5b7d7f318917e8262fffd`, REA `KeymasterUtils::init` at `0x10280c` | Writes command LE32 at request +0, payload at +4. Evidence `ev_29855a3de560dfed3ee7b5776098e13f246095d1cdc1eadb02a222bf48d73a28`. |
| Same library, `sendCmd` at `0x10285c` | Response status signed32 at +0, response length at +4, payload at +8. Evidence `ev_a2be3485044d224fec5c31337db65f13bf93db5fd938685997de25a362f775dc`. |
| Stock `libqtikeymaster4.so`, SHA256 `beb923d3e09579bbf54360a55fa19af3b4a8ee5be9446d9798199a909d46b274` | Generate `0x111600`: `0x2108/0x2508`; import `0x1118d4`: `0x210b/0x250b`; legacy import `0x114748`: `0x10b/0x50b`. Per-key versus bulk-delete IDs are separately proven in the proposal. |
| Secure writer TZ VA `0x100a0f70` | Listener `0x103`, status at +4, returned length +8 exactly 512, frame offset +12, then secure response verification. Evidence `ev_ba9fa135550a2b6ebef96398693edae641099c2cf85c4e45d1f144a0ead3d931`. |

The write request has a **24-byte header**: command +0, total frame count +4,
input byte count +8, input offset +12 (=24), fixed stock field +16 (=256),
reliable-group count +20, then signed frames +24. The listener writes its result
at shared-buffer +20 and changes the returned offset at +12 to 20. These input
and output layouts differ. The guard snapshots input before wakeup and validates
the returned layout separately; its fixtures exercise both offsets and malformed
header/count variants. Stock writer stores are at TZ `0x100a112c..0x100a1240`;
stock listener dispatch at REA `0x105cd4..0x105da0` passes result buffer base+20
and stores the returned offset. Saved instruction/provenance evidence is
`stock-writer-layout.dis` and the review's listener-layout follow-up.

Library REA addresses use its analysis mapping; TZ addresses are secure virtual
addresses. Full exports and the ABI report are retained under
`A9-build/mainline/rpmb-kmtb-20261024/`, including `abi-facts.md`. They contain
static code evidence, not live IDs or table contents.

## Integ25 refusal: what is established

The retained log's one unique refusal form is:

```
A9_RPMB_REJECT reason=3 opcode=2 direction=2 bytes_valid=1 length_match=1 frames=2 frame_valid=1 request=3
```

This proves an otherwise well-shaped two-frame DATA_WRITE was blocked before
UFS hardware submission. Two frames carry one logical 512-byte sector, matching
stock per-entry KMTB writes. There are 30 retained copies on the first complete
boot and 60 on the second; the listener has stock retries. The secure log also
contains four `km_add_key_id` / `addkeyId` reports of -56 per boot. Stock's
inspected -56 path denotes a full cached table. This does not establish the
current physical table's occupancy, or prove that capacity will cease to be a
blocker after the guard change. No table contents were read by this task.

**The old redacted record cannot conclusively identify KMTB**: it retains no
destination or partition classification. A rejected GK group can share that
public signature. The new classifier closes that evidence gap without logging
addresses/IDs; an unsupported attribution is not promoted to proof here.

## Diagnostics and next build

Every classifiable KMTB write is logged without rate limiting at preparation,
each SCSI submission/retry, and reply processing:

```
A9_KMTB_READ phase=2 valid=1
A9_KMTB_WRITE phase=1 allowed=1 operation=1 reason=0 changed=1
A9_KMTB_WRITE phase=2 allowed=1 operation=1 reason=0 changed=1
A9_KMTB_WRITE phase=3 allowed=1 operation=1 reason=0 changed=1
```

`operation`: 0 unclassified, 1 add, 2 remove. `phase`: 1 candidate, 2 SCSI
submission, 3 observed completion. `changed` counts eight-byte slots, not IDs.
`reason`: 0 accepted, 1 unsuitable map, 2 missing before-image, 3 unapproved
command, 4 shape/range/group, 5 entry difference/uniqueness/context mismatch,
6 completion/staged-frame mismatch. The phase distinguishes submission
permission from completion. Unknown maps grant no writes; only the ordinary
map/guard diagnostics can classify an attempt when its partition is unknown.

The existing kmsg-to-logcat daemon's strict whole-message allowlist accepts
these numeric records and rejects appended ID/pointer/payload fields. The
extractor and credential-summary command retain them too. Kernel emission is
per attempt; the bridge's existing overrun/drop counters still matter, and
summary deduplication is not a count of physical writes.

The coordinator must build the kernel with **both** changed QSEE and UFS objects
and rebuild the diagnostics daemon/acceptance assets. The native builder already
copies all QSEE headers and recompiles the two objects; its manifest now names
the single-entry scope. Preserve physical persist RO/noload and existing image
custody. No old/frozen image was changed.

Expected next-run evidence is a valid full-table read, an admitted one-slot add,
unchanged stock write/result flow, and no RR -67 fallback if no other error or
capacity limit remains. A per-key remove should show operation 2. Delete-all,
multi-entry changes, programming and other forbidden destinations must retain
refusals. Check the complete PIN lifecycle and fingerprint enrollment separately;
host tests do not establish either result.

## Host tests and independent review

Native and ARM64 fixtures pass for add/remove, sparse and full delete-all,
multi-entry zeroing, replacement/no-op/duplicates, malformed/partial reads,
strict signed groups, unchanged frame bytes, retries, counter mismatch, ranges
and uncertain completion. AddressSanitizer/UBSan pass. Existing QSEE/UFS/ABI
fixtures remain passing. The Android ARM64 QEMU bridge passes its parser/queue
checks and 64 real logdw datagrams, including the new tags. Extractor/redaction
and all five credential-summary groups pass.

Both production `a9-qseecom.o` and `ufshcd.o` compile with the selected integ25
ARM64 flags/config. Their paired new built-in symbol resolves between the two
objects. This is object compilation, not a full kernel link or device result.
A single independent reviewer inspected the new allowance, ABI, fixtures and
compiled-source bindings; its final verdict and exact source hashes are in
`A9-build/mainline/rpmb-kmtb-20261024/review.md`. Test results, compilation method,
object/source hashes and logs are in that same private host artifact directory.
