# Integrated B `/data`: ROW29 by default, RAM explicitly

Software FBE is now an **opt-in** `--data-mode row29-fbe` profile; see the
[POC package and RAM-B one-shot format instructions](fbe/README.md).
Plain `row29` remains the default; existing data is never formatted at boot.

agent reports a phone PASS on integrated N boot b4936f2f + ROW29 R2 vendor
66e95390 under enforcing: Android boot completes, `/data` is writable f2fs on
the a9-row29-data mapping, and create/reboot/verify retains the public marker
SHA68150bef with different boot IDs. Private evidence remains at
`~/A9-build/mainline/vendor-test-20261020/evidence-row29-data-r3-170342`.
No raw logs, IDs, firmware or vendor images belong in this commit.

`check-data.sh` now tolerates dmctl's `Targets...` header while counting exactly
one actual target row. Extra/nonlinear targets, wrong offset/parent/size, a RAM
mount, wrong slot or marker mismatch still refuse. Use create on the first B
boot, verify on the second; do not recreate a marker to satisfy verification.

The integrated builder defaults to `--data-mode row29`. Its final vendor step
applies on the normal build and both incremental update paths, after other
features and any requested enforcing rebuild. It preserves every incoming boot
byte and does not rebuild or replace a kernel/module. A matching snapshot config
must have built-in DM, f2fs and f2fs security. The incoming kernel's enforcing
mode is retained; storage never rewrites its cmdline.

The final step builds only the mapper, patches the existing fstab /data row and
late-fs action, and links/installs the dedicated enforcing mapper policy and
actual executable inode label. It derives from the current vendor CIL and the
selected GSI cache rather than substituting the old n-e policy, preserving
other features' rules. As in the proven N policy, inherited permissive domains
are stripped except the existing development rootsh; existing POC -N/-D linking
is retained. The linked policy and matching platform hash sidecars are installed
together; no stale precompiled policy can bypass the new domain on that GSI.

The runtime mapper and its reused step3 identity/GPT/DM primitives are the
phone-proven source, unchanged. Its native ELF discards the older loop,
fscrypt-test and formatter entry points. It verifies B, controller4804000/LUN4
(frozen sde binding), saved GPT CRCs, unused entry59 and neighbor58, then maps
exactly 0+6191304 sectors from parent offset 2197264. The exclusive end 8388568
excludes the backup-GPT 20KiB tail. Existing f2fs is required. No mkfs, discard,
repair, ignore-CRC, arbitrary disk/range or automatic RAM fallback exists.
Stable node/UUID, dm_device labeling, Android root metadata, kmsg diagnostics,
early existing rootsh and stale hold clearing match the passed R2 behavior.

Keep the step last in custom integration recipes too:

```bash
python3 -I scripts/mainline/storage-row29/data/integrate.py \
  --base-vendor /path/to/current/vendor.img --out /path/to/new/data-mode \
  --kernel-config /path/to/matching/kernel.config
# Use data-mode/vendor.img as the final vendor; retain the incoming boot.
```

The output directory must be fresh; only regular host `.img` inputs are
accepted. The tool preserves image size and a 2GiB host reserve, does readback
and e2fsck-fn, and emits image SHA/build receipts. It does not install anything
on the phone. The fixed GPT pins intentionally refuse geometry changes; a
legitimate GPT update requires fresh read-only intake and deliberate re-pinning.
The custom integrated N package keeps its matching config as `kernel.config`,
while the standard builder uses `snapshot/config`. Pass the matching file in
custom recipes. Legacy `build-vendor.sh` creates the original RAM baseline;
custom recipes must apply this final step rather than deploy that baseline.

## RAM-data fallback and undo

For an integrated build choose **`--data-mode ram`** explicitly. The same final
step replaces only /data with the known brd ram0/ext4/formattable row and removes
the mapper service/invocation. It leaves other features and boot unchanged and
does not access or erase the candidate. RAM-only diagnostic predicates remain
unchanged; do not weaken them to admit persistent data. `--without` does not
silently disable persistent storage; data mode has its own explicit switch.

Standalone RAM variant from any supported current vendor:

```bash
python3 -I scripts/mainline/storage-row29/data/integrate.py \
  --base-vendor /path/to/current/vendor.img --out /path/to/new/ram-data \
  --data-mode ram
```

The known undo remains n-e RAM-data vendor
`~/A9-build/mainline/integrated-20261021n-e/vendor.img`, SHA256
3640432d0fb384c9762c1546348f20677565dd1020994d10728391789efcbbb8,
with the matching boot b4936f2f. Existing owner lp-write from healthy A writes
vendor_b only; no resize or GPT edit. Then boot B and verify RAM ext4 /data.
Restoring this vendor leaves the persistent f2fs contents intact. Original-zero
restoration is the separate bounded step3 tool from RAM-data B, not this build.

Do not merge this packet by replaying the entire storage worker history. It is
prepared as one clean integration-line commit. The checkpoint/report identifies
its parent and commit; the coordinator chooses the cherry-pick/merge into the
active integration checkout. No phone operation or active-checkout merge is
performed by the worker. FBE remains the separate [step-4 plan](STEP4-FBE.md).

Focused host regression command (cached Android mksh wrapper required):

```bash
python3 -I scripts/mainline/storage-row29/data/test-integration.py
```

The integration packet was also staged over the frozen n-e image in both modes,
read back and checked with e2fsck-fn. All 100 modules remained byte-identical;
the rebuilt mapper and linked policy matched the phone-passed R2 exactly.
No full kernel/integrated build is needed for this delta. The full builder's
existing disk floor is retained; use a fresh output and sufficient free space.
