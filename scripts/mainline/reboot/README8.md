# Integ8 diagnostic image — 6r3 + FP8 + FBE evidence

The user authorized building this diagnostic image to capture the missing
evidence on the next owned boot. It uses the immutable 6r3 image pair, the
source-merged FP8 fingerprint init change, and the `02d38b6` resume logger as
sidecar assets. The FBE fix and zero-FAIL result remain unproven. No device
commands were run during preparation or assembly.

## What changed between the retained boots

Integ4 boot55 (`eink-quality-20261023/mainline4`) creates fresh metadata and
filesystem keys. Kernel lines999–1026 show the normal rollback-resistant
generation refusal followed by successful ordinary hardware-backed generation
and `vold` return0. Lines1042/1045 show a68-byte ephemeral wrapped key and native
AES-XTS. This proves fresh provisioning worked; it does not prove reopening an
existing key after an A-slot boot.

Integ5 boots63/72/73 use an existing metadata key. Their retained early logcat
shows TA `Begin -62`, then upgrade `-8`; the Keystore2 stack explicitly says
`Upgrade failed`. The original README's description is supported for these
boots. After correcting the vendor patch properties to2022-04-05, 6r2 boot81
instead shows a direct `Begin -8`, with the generic upgrade wrapper but no
upgrade call. Integ7 boot86 fails before the real mount, and the saved6r3
acceptance still reports `data_rc=25`, `data_tmpfs=1`. These are distinct
phases. Matching the patch level removed the observed upgrade requirement but
did not repair decryption.

Integ4 and6r3 contain byte-identical stock Keymaster service, QSEECom API,
Keymaster implementation/device utility/parameter libraries, QCBOR and real
wrapped fstab. The failing TA reports API4.1, version4.551. Its stock client
selects the modern protocol for that version. The saved sweeps do not contain
the failing key's authorized block modes or a current metadata snapshot. An
older golden metadata wrapper has a clear public characteristics field before
its encrypted material; [the later bounded reader](FBE12.md) can inspect that
field without TA validation. Those older characteristics cannot establish
the current key's authorizations.
No keys, identifiers or encrypted payloads are included in this report.

Stock and current Android both request AES-GCM for the outer key-storage
wrapper. This differs from the68-byte ephemeral ICE key used by the kernel.
The error occurs before that kernel key-programming path, so changing ICE,
fscrypt policy or native-XTS flags cannot address this observed failure.
[Current KeyStorage](https://raw.githubusercontent.com/LineageOS/android_system_vold/lineage-23.2/KeyStorage.cpp)
and [Keystore compatibility](https://raw.githubusercontent.com/LineageOS/android_system_security/lineage-23.2/keystore2/src/km_compat/km_compat.cpp)
provide the source comparison. The actual stock client functions were also
examined with [REA](/home/user/.agents/skills/reverse-engineer-anything/SKILL.md).

## Prepared diagnostic

`fbe-trace.c` interposes the stock low-level Begin call. It always calls the
original once with unchanged arguments, and returns its exact status. Only a
failed DECRYPT Begin(-8/-62), within120 boot seconds and at most twice, reads
the existing key's characteristics through that same stock backend. It logs
only GCM requested/authorized, AES/decrypt authorization and public OS/vendor
patch fields. Flags are meaningful only when `chars_rc=0`. Blob bytes/hashes,
app IDs/data, nonces, authentication tokens, secure IDs and names are never
logged. There is no retry, upgrade, alternate cipher, file/key mutation,
software provider or format added.

The existing wrapped-start script copies only this fixed numeric record to
the persistent kernel logger immediately after failed `vdc`. It preserves the
original exit status and leaves `data.ready=0`. This prevents the long sweep
from losing the relevant early evidence to logcat rotation.

Five actual Android ARM64/QEMU groups cover Begin-8, characteristic failure,
Begin-62, successful Begin and an unrelated error. They verify one original
call per attempt, preserved arguments/output/handle/status, the two-record cap
and exclusion of synthetic secrets. An actual Android mksh fixture confirms
the durable collector accepts only the fixed public record. Logs and native
library are under `A9-build/mainline/integ8-fbe-host`.

`python3 -B scripts/mainline/reboot/build8.py --stage-only` checks the exact6r3
base, tested native library, source pins and original resume logger pins.
Without `--stage-only`, it assembles the diagnostic candidate, adds FP8's
init-only change, preserves all other6r3 regular files and all boot/kernel/DT
bytes, checks ext4 and init, and copies matching acceptance and sleep logger
assets. Default reserve is
15GiB. A14GiB exception requires explicit user authorization; no exception has
been assumed. The assembly result explicitly records `fbe_fixed=false`.

## Next owned run and saved evidence

Run `/home/user/A9-build/mainline/integ-20261024-8/run-fbe.sh` only as the
authorized phone owner. It invokes the existing b-try lifecycle with a120-second
hold and the frozen short read-only hook. The normal return-to-A path is
unchanged. Preparing or host-testing the launcher does not execute it.

The hook saves `${A9_BTRY_LOGS}/fbe-evidence.log` (mode0600), also echoed into
`onb.log`. It selects only fixed numeric `A9_FBE_KEY`, `A9_FBE_SOURCE`,
`A9_FBE_STATE` and `A9_FBE_CAPTURE` records. It reads the current durable logger
through its private process-root mount, checks the public header and privately
compares the boot ID, then falls back to dmesg/logcat. Raw native/helper output
never enters the evidence file. Missing records remain `key_records=0`.

Five host fixture groups check capture, duplicate removal, private/malformed
record rejection, absence, failure/timeout and context refusal. An actual
Android mksh fixture verifies current-log selection, stale-header rejection
and numeric-only output. The hook does not start fingerprint, radio or media
actions, change keys, or deliver credentials.

## Remaining work

Use an already saved failing-key characteristics capture if available. If
none exists, only the authorized phone owner can obtain this missing runtime
fact using the prepared diagnostic in an owned session. This host-only worker
must not perform that boot. Determine whether the hardware key permits GCM and
decrypt, whether characteristics retrieval succeeds, and which public patch
levels it binds. Then repair the evidenced mismatch and rerun a real-data
mount plus B-to-B reopen. Preserve shared keys/metadata until the owner makes
an explicit, appropriately scoped fresh-data decision. No automatic reset is
justified by the current evidence. The requested FBE repair and zero sweep
FAILs remain open.
