# FBE-RR2: listener registration extent

Host-only analysis of retained INTEG17 boots114/115. No device operation,
RPMB command or image build was performed. The fix is source/host tested;
registration and rollback-resistant key generation still need phone evidence.

The concrete stock mismatch is the third registration SCM argument. Our
frontend passed the logical listener request length0x6400. Stock registers the
mapped ION scatterlist extent0x7000. Both drivers allocate page-rounded memory,
but our registration discarded that distinction. The fix uses `f->buf->len`
for registration and keeps `f->size` unchanged for userspace/reply bounds.
The registered pages are the existing zeroed, contiguous, SHM-bridged buffer.
No new allocation, permissions, secure-memory mapping or write operation is
introduced. Other sub-page listeners receive the same stock-equivalent fix.

This explains the retained pattern: RPMB0x6400, secure UI0x10a8,
QISL0x1f4 and OPS0x6400 fail; SSD/time/filesystem/request-cancel0x5000 and
GPFS0x7e000 start successfully. A general missing SCM convention, CE clock
or unavailable QSEE endpoint would not explain that size correlation. It is
strong evidence for the extent bug, not a measured secure-firmware verdict.

The old `phase=4 rc=-22` does **not** expose TZ's raw return status or its
internal failed check. It establishes that request checks and DMA-buffer
binding passed and `qsee_register_listener_scm` failed before `finish_call`.
The retained kernel logs positively report SMC ARM64. Thus the earlier report's
0x32000206 is the stock descriptor ID before convention bits, not proof of
the actual wire ID. Mainline uses0x72000206 in ARM64 mode. Do not force ARM32
or add an `-EINVAL` legacy fallback on that basis; successful page-sized
listeners already use the same transport.

## Stock evidence

- qseecomd `main.cfi`0x102334 and `start_listener`0x10206c separate init from
  start and do not make the readiness property proof of RPMB availability.
  Existing REA IDs `ev_d7dbe901a59da80cdc12e2259c4683f3d0fc272985f98303b26fe686ed2a9885`
  and `ev_c1e3ebd60b14e283b7c611062516a803a76046a7aa70133e20da9decfa56b9e9`.
- librpmb `rpmb_start`0x105af8 calls `QSEECom_register_listener(id,0x6400,0)`
  before starting dispatch. REA
  `ev_c777624ffbb76a5aa5e191194360b2a80abd232450fe0419e508ed76d26a5287`.
- Stock libQSEEComAPI.so SHA256
  `2f70ad2dd4d3137f18df34022021bc3bf9533ac2161de0e1afc6e7d338d82094`:
  `QSEECom_register_listener`0x1064b0 preserves logical0x6400 in
  ioctl0xc0189701. `__QSEECom_ION_memalloc`0x105d24 computes
  `(length+0xfff)&0xfffff000`, allocates/maps0x7000 from heap27, and retains
  the original requested length in its handle. REA
  `ev_68ca41e99faa91b3c5f5294a8218eda9c491e88bc399f2395a2e6e0e22a5b899`
  and `ev_dbdde82fdaa43560582e184192de4a31ba810c0df2fd5f19477b43513efb3152`.
- The retained actual stock-kernel disassembly calls `qseecom_vaddr_map`
  at0xffffff80087b2eb4, with the size-output pointer in x5. It then loads
  that output into x10 at0xffffff80087b2ecc/2eec and packs the SCM request
  size at0xffffff80087b2edc/2efc. Command6 and the -EIO-only command1 fallback
  appear at0xffffff80087b6090..60d0. The matching downstream source's
  `qseecom_vaddr_map` assigns `new_sgt->sgl->length`; `__qseecom_set_sb_memory`
  copies that mapped length into `req[_64bit].sb_len`. This source was recovered
  read-only from the existing qseecom-transport archive, not fetched remotely.
  The focused stock ELF omits `qseecom_vaddr_map`; its attempted REA decompile
  failed and is not claimed as additional evidence.

REA pseudocode is recovered code, not runtime execution. Saved complete
library responses and earlier qseecomd/librpmb evidence are retained under
`A9-build/mainline/fbe-rr2-20261024/` and `fbe-rr-20261024/`.

## Every reachable -EINVAL source

| Site | Condition | Relation to INTEG17 phase4 |
| --- | --- | --- |
| Register ioctl precheck | already registered/app-owned, ID0, or aborted | phase1; ruled out |
| `qsee_bind` | base0, length0, address wrap, existing binding | phase3; ruled out |
| `qsee_get_buf` | imported DMA-buf has different ops | phase3; ruled out |
| `qsee_bind` imported bounds | length exceeds allocation or `access_ok` fails | phase3; ruled out |
| frontend `scm` | arg count exceeds descriptor capacity10 | fixed count3; cannot trigger |
| Android SCM wrapper | CE-clock or bandwidth helper propagates error | previously unlabelled; now `check=ce_clock/bandwidth` |
| `qcom_scm_call` | unknown convention | retained ARM64 rules it out; now `check=unknown_convention` |
| `__scm_smc_call` | extended args needed but TZ pool missing | count3 fits4 registers; cannot trigger; now `check=missing_ext_pool` |
| waitqueue completion | secure WAITQ context >= configured count | possible propagated transport error; `transport!=0 status_valid=0` distinguishes it from remapping |
| `qcom_scm_remap_error` | secure status-2 invalid argument, -3 invalid address, or unrecognized nonzero status | previously collapsed to-22; now raw signed status and mapped errno logged |

ION heap/query/alloc has other -EINVAL checks, but those precede a successful
register ioctl binding and cannot produce its observed phase4 record.
`finish_call` runs only after phase4 rc0 and has no direct -EINVAL return;
phase5/6 are absent. The proprietary secure check itself cannot be recovered
from the existing phase-only log. This patch does not label an unobserved
secure status as fact.

## Next owned phone run

Rebuild all three changed built-ins (`a9-qseecom.o`, `qcom_scm.o`,
`qcom_scm-smc.o`) and the ramdisk logger. Both the normal apply path and native
fingerprint/integrated kernel recipe include the new diagnostic patch and
recompile the SMC transport, preventing stale-object reuse. No integ17
worktree or existing image was modified.

Expect `A9_RPMB_REGISTER request=25600 mapped=28672 ... cmd=6 arginfo=0x83`,
CE-clock/bandwidth rc0, an `A9_RPMB_SCM check=invoke` record with nargs3,
size28672 and both page-alignment flags1, followed by a `check=smc_return`
record. A successful transport has transport0/status_valid1/secure_status0;
registration must then reach phase4/5/6 rc0 and rpmb_start0. These markers are
immediately fsynced into kdebuginfo by the logger. Failure records distinguish
clock/bandwidth/local transport errors from secure -2/-3/default remapping,
without exposing physical addresses, user pointers, device identifiers or buffer contents.

Then require authenticated RPMB read completion and TA/KMTB validation before
claiming rollback resistance. `stor_read_failed=0` while registration fails
means no reported storage read, not a successful read. Never create/write KMTB,
program an RPMB key/counter or permit authenticated writes to overcome failed
reads. The UFS read-first guard, userspace KMTB logic and fallback are unchanged;
this patch can fix registration while RR generation still fails because
programming is deliberately denied. Do not treat wrapped mount success as RR.

Host validation: clean three-patch replay; five production listener SCM groups
including logical0x6400/mapped0x7000 and sub-page allocation, error propagation
and the unchanged -EIO-only fallback; compilation of all three actual changed
ARM64 sources with retained native kernel flags; regular-file logger tests for
immediate marker sync; and diff checks. No phone or TA execution supplies a
hardware PASS.
