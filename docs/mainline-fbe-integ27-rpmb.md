# Integ27: RR fallback and the remaining RPMB rejection

2026-10-25, host-only. **The retained two-frame rejection is outside both known
Gatekeeper and KMTB extents. It is not an approved single-entry KMTB add.** The
stock TA separately reports its **full cached RR-table** path before a write.
There is no justified classifier permission change from these observations.
Gatekeeper and single-entry KMTB permissions remain unchanged; delete-all,
programming, metadata and other-partition writes remain blocked.

The 1056-write ownership investigation is **parked**. The follow-up below
focuses on RR capacity, stale references and a per-entry-only recovery plan.

## The request that was rejected

The private evidence is
`/home/user/A9-build/mainline/sweep-integ27-f1-20261025/boot-0000000129/`
and adjacent boot130. The numeric destination record in both boots is:

```text
A9_RPMB_WRITE_TRACE allowed=0 map_valid=1 groups_valid=1 partition=4 first=1056 offset=0 frames=2 context=0
```

Here **partition4 is a diagnostic enum meaning outside both known extents**, not
PTBL partition ID4. `first` is a physical256-byte payload block, so this is the
512-byte range in blocks[1056,1058). `offset=0` means no known GK/KM-relative
coordinate is available; it does not mean a KMTB header or entry at offset0.
`context=0` is unknown/non-whitelisted caller context, not evidence of Generate.
The preceding request has outer command0x103, request3, input offset24 and two
signed data frames. The valid map already identifies GK and KMTB by their public
PTBL IDs; this request matches neither. No KMTB candidate/read/write record
classifies it as an RR table operation. Its rejection is the existing policy.

The earlier read6 frames at1056 followed by write2 is not unique to an RR-entry
update and is not a partition identifier. Existing diagnostics retain only known
GK/KM extents; they do not reveal the public PTBL ID, owner or relative offset of
this other range. Exact runtime ownership therefore remains unresolved from
these retained logs. Do not invent an extent or relabel partition4 as KMTB.

The first such rejected destination is at173.115532s on boot129 and129.503167s
on boot130. Boot129 retains96 identical WRITE_TRACE observations, boot13012;
these are retained diagnostic records, not independently counted completed
writes. The summary de-duplicates their one unique form. “One rejection” in the
summary does not establish one attempted transaction per boot.

## Bounded stock search for the other writer

A focused common-library32 REA found a separate TA/SFS version-metadata writer,
with merged entry0x2accc and storage call sites0x2ae7a(read),0x2aede/0x2b1e8(write).
These REA VAs map to original stock TZ VAs by adding0x102f1000. Its initializer
reads logicalsector0,count1 and can write one sector after checking formatbyte9;
its partition-add path requests four logical sectors. The identifier is loaded
from per-TA context+0x11b8, not a recovered fixed partition ID. This proves
another stock writer can have the same two-frame write size. Its direct read is
two physical frames, **not the observed six**, so it does not identify1056.
Selected Sunwave FTS writes instead route to filesystem listener0x7000, as traced
in [fingerprint preflight](mainline-fp-enroll.md); no FP RPMB allowance follows.

Source TZ SHA256 is
`13e664bf4a20d32cd8e5e14584b9a33ca891dc9398ef7dea7657dff7f1faaf7e`.
Static REA call evidence includes
`ev_8234d661671e82dcf65afeeac916606fe6380dd7b1b6b452c4e9cb81081b296b`
and `ev_493a601d780dfc9dba81c36a39f3be484b1b190bfea9263e6b61f4dabcefee7d`;
private `rpmb27-20261025/other-partition.md` indexes carrier identity,
ordinary-disassembly provenance and the bounded search gaps. All isolated REA
sessions exported and closed. No static candidate is presented as runtime
partition ownership, and no extra storage permission is introduced.

## Why rollback-resistant generation falls back

Boot129's kernel records fallback at21.846782s,25.445817s and28.291408s, before
the first request to1056. Its retained logcat summary counts two fallback lines;
that is a retention/counting difference, not proof of two complete TA calls.
Timing and the TA error path distinguish this fallback from the later denial.

Boot129's retained `qsee_log` has six occurrences each of
`km_add_key_id:122:-56` and its outer `addkeyId:219:-56`. These are two diagnostic
sites in one call chain, not twelve unique insertions. Boot130's retained TA log
has neither. Absence in a retained log does not establish a successful operation.

Stock Keymaster TA SHA256
`11c120cabd09e9caa48e85f6f7ce307ff7fe5f3a2b82a465f2afeaf4313bec43`:

| REA address (base0x100000) | Proven branch |
| --- | --- |
| `km_add_key_id@0x115670` | Calls lazy storage read, scans1536 uint64 slots for zero. If no zero is found, logs line0x7a=122 and returns-0x38=-56. **No ID mutation or sector write is issued by this full-scan branch.** |
| Same helper, empty-slot branch | Changes one cached ID and invokes raw writer0x117d68 for its512-byte sector. A write failure uses diagnostic line0x73=115, distinct from122. Initial-read failure logs line0x65=101. |
| `addkeyId@0x109128` | Logs the helper's error at219 and maps a nonzero insertion result to-67. |
| `storage_alloc_read@0x11602c` | Publishes its allocation pointer before reading12KiB at KM-relative0x2000. A failed initial read can leave a non-null cache pointer, so later calls can skip reloading. |

Evidence: R5 `ev_ef283e7db2680984cd9ff79b69b385fcbe06e5a03926b1281c6ac645d720ccab`
and wrapper `ev_589fd9927693386b2fd76af0677e62183b1f25d3e6dff75707b8345aa76f118d`;
original responses remain under `fbe-rpmb-r5-20261024/rea/`. The
[stock layout proposal](mainline-fbe-rollback-proposal.md) gives table units,
creation timing, deletion semantics and capacity consequences.

The precise supported diagnosis is **no empty slot in the TA's cached table**.
It is not proof that all physical entries are live valid keys: retained state,
failed initial reads and unexpected nonzero storage can also affect that cache.
No live table, ID, blob or PTBL content was read by this investigation. There is
no safe automatic reclamation rule: the table has no A/B ownership field, and
failed/abandoned creation or upgrades can retain slots. Allowing an unrelated
write or weakening the single-entry difference rule cannot make this full-scan
branch attempt an add. Bulk zeroing/reset and key programming are not recovery
options within the approved scope.

## Landed diagnostic correction and checks

`credential-summary.py` previously counted a TA key-ID error only when its line
contained the word “failed”; stock's function:line:rc records were silently
missed. It now recognizes the bounded negative-return syntax and emits numeric
`ta_key_id_full` separately. The observed boot129 result is
`ta_key_id_failed=6 ta_key_id_full=6`, replacing the misleading zero. Wrapper
lines do not count twice. Arbitrary suffixes/private text are not exported.

Host fixtures cover full-cache versus initial-read error, wrong function,
wrapper duplication and suffix rejection. An inert C fixture reproduces the
integ27 public request shape against explicitly synthetic extents consistent
with outside-both classification; it remains denied and is never a KMTB
candidate. It does not pretend to recover a device PTBL. Existing summary
fixtures pass and the observer fixture compiles/runs natively and compiles for
AArch64. No production guard or storage-writing code changes were made.

Artifacts: `/home/user/A9-build/mainline/rpmb27-20261025/` contains the numeric
summary, TA counts, public timeline/source hashes, stock helper excerpt and
fixture logs. The assigned checkpoint retains the host-only boundary. Further
exact attribution of1056 needs a public PTBL-entry match or equivalent retained
caller evidence; no extra phone operation was started by this worker.


## Follow-up: capacity, lifetime and orphaned entries

### Target identity and exact meaning of -56

`13e664bf4a20d32cd8e5e14584b9a33ca891dc9398ef7dea7657dff7f1faaf7e`
is the stock **TZ image**, including common-library/storage code. The separate
stock **Keymaster TA** whose table routines were analyzed has SHA256
`11c120cabd09e9caa48e85f6f7ce307ff7fe5f3a2b82a465f2afeaf4313bec43`.
These are different artifacts from the saved stock dump, not interchangeable
names for one binary. TA addresses below use REA base0x100000; subtract that
base for original ELF VAs. Common-library TZ addresses use their own mapping.

Capacity is **1,536 entries**:12,288 bytes, each entry an8-byte ID; zero means
free. The table occupies KMTB-relative bytes[0x2000,0x5000),24 logical512-byte
sectors,64 entries per sector. KMTB partition ID is0x4b4d5442; its physical base
is dynamic. `storage_alloc_read@0x11602c` reads this12KiB region;
`km_add_key_id@0x115670` scans exactly0x600 entries. `-56` is its local
**no zero found in the cached table** result, emitted at source-line122 before
an entry write. It is not a SCSI error, write-counter mismatch or guard-denial
code. `addkeyId@0x109128` maps this and other insertion failures to-67.

| Stock operation | When and what it removes |
| --- | --- |
| `deleteKey(blob)` | HAL0x112500 serializes the supplied blob and sends0x210d (alternate0x250d). TA handler0x1073fc calls decoder0x121ea4; after decoder success it tests decoded RR flag+0x140 and loads ID+0x10 at0x107478, calling remove0x115864 at0x10747c. The helper clears the first equal ID and writes its containing512-byte sector; missing ID returns-33. No owner/slot filter exists in the table. |
| `deleteAllKeys()` | HAL0x1125e4 sends0x210e without a blob or slot selector. TA handler0x1074cc reaches global helper0x1159a4 through0x1074d8. That helper zeroes/writes the entire12KiB region, even without first reading it. **Still blocked.** |
| Key upgrade | Actual upgrade branch0x1076ac checks the old RR ID via0x1068fc/0x1157a4, then calls addkeyId at0x1079a0 before serializing a replacement blob through0x120e0c. No old-ID removal or failure compensation appears in this body. It can consume an additional slot. A higher-level caller might later delete an old blob; this TA trace does not establish that it does. Deleting only the new blob does not automatically remove the old ID. |
| Generation/import failure after insertion | Shared finalizer0x10490c inserts before generation/import and blob serialization. A later failure can leave an occupied entry without a returned blob. The inspected failure cleanup frees the object, without an RR-remove call. |

A zero `deleteKey` return is **not** a reclamation receipt: the selected TA
handler sets return0 at0x1074b4 even after logging decode/removal failures.
The special legacy HAL64-byte FBE branch0x116bfc instead clears an ICE slot;
it is not a KMTB-entry deletion. Check the actual single-entry write and result.

The input flow proves that the audited stock API needs a **blob accepted by the
stock decoder, carrying its internal RR ID**. Complete cryptographic validation
of every decoder/callback branch was not established by this bounded audit.
It does not expose the internal helper as a supported HAL delete-by-ID API.
If all usable blobs are gone, this evidence supplies no stock-equivalent
blobless orphan-deletion procedure. Do not forge a blob from an ID or replace
stock deleteKey with a raw sector/index zeroing tool.

Evidence for add/read/wrapper is indexed above. Delete/all original code bytes:
`ev_245b5727bc46924b4d8d36d5c12e0cb25756ff1da62e582b64bf9c611ce4ed5f`;
HAL delete:
`ev_37e53aeef31a2932557481d19941cdf1e3832ff7a3f3152b1266658cccb6ea11`;
upgrade:
`ev_81cc475bf5988ec28bce47c49bbd9d19039622e088dd3f74dd537c855681d201`.
Newly decoded delete-caller instructions and hashes, separately labeled as
ordinary immutable-byte disassembly rather than new REA Evidence, are in
`rpmb27-20261025/orphan-delete-audit.md` and
`orphan-delete-static-manifest.json`. No target code was executed.

### Could our testing have exhausted it, including for stock A?

**Plausible, but unproved.** A raw shared-/data format can discard the keystore
DB, FBE/SP blobs and pending-delete records without issuing stock deleteKey for
each RR entry. Failed creations and repeated upgrades can also leak references.
The flat table has no A/B ownership field. If the physical table is truly full,
stock A using this layout/backend also cannot add another RR ID: the TA returns
-67 and a client may fall back or fail. Existing A keys can continue working;
booting A need not generate a new RR key, so absence of a boot fallback would
not prove spare capacity. A normal Android factory reset is not equated with a
raw format here; other reset hooks were not audited and delete-all remains out
of scope.

Boot count alone is insufficient. For example,3 committed unreclaimed entries
per format would need512 such cycles to fill an initially empty table;6 would
need256. Actual initial occupancy and per-test allocation counts are unknown.
Integ27 retains **zero A9_KMTB_WRITE records** in either kernel log and six
full-cache messages in boot129. It does not demonstrate that these tests made
1,536 successful physical additions. Earlier denied/failed writes cannot be
counted as committed entries just because the TA attempted a key creation.

Two stock cache behaviors are important alternatives:

- Add0x115670 changes the cached ID **before** calling writer0x117d68 and does
  not restore the cached slot after a failed write. Repeated failures can
  exhaust free slots in that loaded TA's RAM without committing those IDs.
- Read0x11602c publishes its allocation pointer before the initial read. If the
  read fails, a later call with that pointer can skip reloading; unexpected
  nonzero cached contents can take the same full-table branch.

Neither behavior proves the observed cache was polluted, and persistence of
this TA cache across ordinary warm reboots has not been established. A direct
cold boot removes the ambiguity of the previous loaded-TA instance. A healthy
fresh table read is still required; cold boot cannot repair genuine persistent
exhaustion, reconstruct lost blobs or validate every stored ID.

### Minimal recovery plan within the existing policy

This is a **plan**, not an implemented maintenance writer or permission change.
The existing single-entry difference guard stays intact. No table reset,
delete-all, multi-entry zeroing, RPMB key programming or persist write is added.

1. **Test a fresh TA instance first.** Direct cold boot of the already staged B
   image, preserving /data and metadata, then at most one ordinary existing RR
   creation attempt. Use the existing PIN-lifecycle probe only if its disposable
   data/no-current-credential prerequisites already hold; do not format data to
   make this test eligible. This may resolve a transient cache problem without
   deleting any old key. Preserve QSEE/kernel/logcat evidence of initial reads,
   the creation outcome and KMTB write phases. No event means inconclusive,
   not repaired; another full-cache result alone still does not prove physical
   live-key occupancy. A failed read stops any cleanup plan.
2. **Retain references before attempting reclamation.** On the correct OS/user
   state, unlock normally and allow the existing keystore's pending per-key GC
   to run. Inspect retained obsolete-test-key references in that keystore and
   available private, quiescent backups. Do not restore an old DB over the live
   keystore or upgrade keys merely to enumerate them. Keep blobs, binding
   material and IDs private/on-device; report only candidate/result counts.
3. **Prove each candidate is disposable.** It must have a retained blob and
   provenance tying it to an explicitly obsolete test key. Its absence from B
   is insufficient: shared A state, other users, system/FBE/SP components and
   retained backups may still depend on it. The RR table itself cannot prove
   orphanhood. Do not delete a live alias just to free capacity. A historical
   blob's presence is necessary for this plan, but does not by itself prove it
   is unused or that the current stock decoder will accept it.
4. **Delete one proven obsolete key through stock per-key APIs.** Prefer normal
   owning-keystore GC where its retained blob/backend association exists;
   otherwise a separately prepared/reviewed maintenance client would supply
   that exact blob to the corresponding stock KM4 HAL deleteKey. That client
   does not exist in this change. It must not synthesize an ID, call upgrade,
   allocate a replacement partition, or invoke deleteAllKeys. The existing
   guard must have a healthy before-image and admit only the one nonzero-to-zero
   ID transition, with all other bytes preserved. Require matching successful
   `A9_KMTB_WRITE phase=3 allowed=1 operation=2 reason=0 changed=1` evidence and
   normal secure completion; API return0 alone is insufficient. On an error or
   uncertain result, stop and reconcile through a fresh read, without a retry.
5. **Validate relief with one bounded create/delete pair.** After one confirmed
   removal, a stock RR probe can create one key, retain its returned blob, then
   delete that same key per-entry, checking add/remove outcomes. Preserve its
   blob until removal is confirmed. This probe is a later prepared step, not a
   command to improvise on this run. It restores the free slot and demonstrates
   capacity relief; one free slot may not cover all subsequent FBE creations.
   Reclaim further candidates only one at a time with the same provenance.

If all original/backup blobs for truly orphaned IDs are lost, **the approved
stock per-entry API does not provide a demonstrated way to reclaim them**.
There is no safe automatic “table minus current-B DB” cleanup: it cannot
separate A/backup references from orphans. Stop rather than reinterpret a full
cache as permission to clear arbitrary entries. That case needs a separate
recovery decision; this plan neither implements nor recommends bypassing the
current policy. Future disposable tests should finish and verify their normal
per-key teardown while their blobs still exist, before discarding /data.

### Exact next phone step requested from the owner

**No cleanup or deletion yet.** Prepare the next normal B image through the
existing coordinator, then arrange a **full shutdown followed by power-on
straight into that already staged B slot**, with no intermediate A boot and no
/data or metadata format/restore. A repeated warm reboot is not this test. Once
B is ready, run **one existing PIN-lifecycle check in that same owned boot**
only if the data is still disposable and no credential is installed. Use its
existing opt-in `A9_SWEEP_PIN_LIFECYCLE=fresh-disposable-formatted-b`; the flag
attests the existing state, it is not a request to format anything. If those
preconditions do not hold, collect the normal cold-boot logs only; do not
change a daily PIN or rerun the boot wrapper to manufacture a test.

Retain that boot's `qsee_log`, kernel.log and logcat using the existing logger,
and run this **host-only** summary after pulling them:

```sh
python3 -I scripts/mainline/reboot/credential-summary.py /path/to/cold-B-sweep
```

Return the PIN verdict, `ta_read_failed`, `ta_key_id_full`, rollback-fallback
count and any KMTB_READ/KMTB_WRITE records. We need to distinguish:
(a) a fresh healthy read followed by successful RR add, supporting cache-only
exhaustion; (b) another no-empty-slot result after a verified healthy fresh read,
supporting persistent exhaustion/unexpected contents; and (c) an initial read
failure, which leaves the cache diagnosis unresolved and forbids cleanup.
If no RR operation was attempted or retained, the result is inconclusive.
Neither PIN PASS nor absence of -56 alone certifies rollback resistance.
