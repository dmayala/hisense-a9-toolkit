# Row29 step4: vold-managed software FBE on the bounded f2fs

Original design plan; the first software FBE POC is now implemented as an
opt-in profile in [fbe/README.md](fbe/README.md), with separate RAM-B reformat
and kernel-policy/Android-user boot checks. Plain persistent `/data` remains
the integration default. Host preparation does not execute a phone run.
Kernel fscrypt-v2/software DE/CE operations passed r4, and plain f2fs persistence
passed R2 across reboot. Those results do not yet prove vold/Keystore key
provisioning or credential-encrypted Android boot.

## Proposed profile

Keep the same checked B-only dm-linear parent/range, boot b4936f2f initially,
and software filesystem encryption (AES-XTS contents, AES-CTS filenames, v2):

```text
/dev/block/mapper/a9-row29-data /data f2fs noatime,nosuid,nodev,nodiscard wait,latemount,fileencryption=aes-256-xts:aes-256-cts:v2
```

This is an opt-in future FBE vendor. The explicit v2 format and omission of
inlinecrypt select the ordinary fscrypt crypto-API path; don't copy hardware
wrapped-key/inline optimization flags from stock. See [AOSP FBE format and
setup](https://source.android.com/docs/security/features/encryption/file-based).
Keep metadata encryption/dm-default-key/ICE as a separate later milestone.
No real userdata, metadata, misc or stock key-directory fstab row is added.

Use the selected GSI's existing **system vold**, Keystore2 and its Cuttlefish
software/nonsecure KeyMint/Gatekeeper providers. Do not start the stock QSEE,
Keymaster or vendor vold stack. Software kernel keys and Keystore-wrapped
on-disk key blobs are different layers: kernel success does not establish that
the software provider exposes the security level/services vold requests or
keeps its wrapping state across reboot. This is a functional software FBE POC,
not hardware-backed key isolation or rollback resistance.

## Concrete work before a write run

1. Freeze the passing plain vendor/boot, candidate geometry/GPT pins and current
   test-data backup/retention choice. Existing `/data` is nonempty (~327MiB), so
   do not just flip fstab flags over it. The proposed FBE run starts with a fresh
   filesystem in the **same bounded mapper**, using the explicit RAM-B one-shot
   format path; no automatic format on boot. This task does not execute that
   write or erase current test data. Keep original candidate.bin untouched.
2. Inspect the actual selected GSI init post-fs-data/installkey/init_user0 and
   encryption triggers, vold version/build, service manifests and software
   KeyMint/Gatekeeper state paths. Verify their early startup, Binder security
   level/instance and restart persistence. Any persistent provider state must
   be on the approved candidate, not ephemeral `/metadata` or a daily partition.
   Reuse the already booting providers; address a concrete service failure from
   logs before introducing any replacement. No raw key, PIN or auth-token dump.
3. Build an FBE-only mapper variant that prepares root metadata but **does not
   traverse `/data/local/tmp/a9-stay` before vold loads the DE keys**. The current
   pre-vold hold clearing can fail with ENOKEY on an encrypted directory. Move
   the dead-man hold to a volatile `/dev` path and update the owner keeper and
   its narrow SELinux permissions together, retaining freshness/cap/return
   behavior. Do not weaken GPT/table checks or the RAM-only diagnostic guards.
4. Freeze the fstab line above and verify the actual GSI startup path initializes
   systemwide keys before user0/app directories. Check `ro.crypto.state` and
   `ro.crypto.type` from real boot; setting properties alone is not evidence.
   Add only the needed policy for system vold/Keystore and fscrypt ioctls on
   this f2fs mount. Keep the mapper domain enforcing and linked policy/labels
   consistent, as in R2. Preserve early root capture to collect failures.

The current AOSP reference stores systemwide key material under
`/data/unencrypted/key` and user keys under `/data/misc/vold/user_keys/{de,ce}`,
and installs separate device/user policies. Use this as a source baseline,
then verify the selected GSI rather than assuming binary equivalence.
[Android16 QPR2 FsCrypt.cpp](https://android.googlesource.com/platform/system/vold/+/refs/heads/android16-qpr2-release/FsCrypt.cpp)
(reviewed blob9666b6e4db82908501d5e0274cac8581e6a9b51a).
KeyStorage uses Keystore wrapping for relevant authentication modes and can
fall back from rollback-resistant key generation; it is not a reason to replace
Android key management with a fixed test key.
[KeyStorage.cpp](https://android.googlesource.com/platform/system/vold/+/refs/heads/android16-qpr2-release/KeyStorage.cpp).

## One short initial phone run, then reboot acceptance

First FBE boot: leave the disposable user without a credential initially. Let
normal init/vold provision it; do not add manual fscrypt keys with the r4 tool.
Capture boot/enforce/mount/table, vold/Keystore errors and fscrypt policies.
Require v2 policies and different DE/CE identifiers, with ordinary software
mode and no wrapped-key/inline flags. Store a public DE marker and a CE marker
through a small test app using device-/credential-protected contexts, fsync,
then reboot B without formatting or restoring directories. Require both
markers/hash values and Android user state to recover. Keep logs and policy
IDs private, and never log key blobs. Crypto properties alone cannot pass it.

Then establish Direct Boot with a disposable credential entered through the
normal Android UI. Before first unlock after another reboot, require DE marker
access, CE unavailability/ciphertext names, and no CE key installed; after
correct unlock require plaintext CE names and the original marker hash. A
wrong credential must not expose CE data. Merely locking the screen after an
unlock is not a key-eviction test; use a cold user/reboot state and the selected
framework/vold lifecycle. Do not forcibly evict keys beneath live framework
file handles. A read-only policy/status helper can inspect kernel state without
exporting keys. The r4 wrong-key/removal test remains separate kernel evidence.

Stop on first concrete service/key/policy failure and capture dmesg AVCs,
init/vold/Keystore logs before the owner's usual return. No unattended PIN
handling or credentials in argv, scripts or logs. The acceptance milestone is
Android/vold provisioning + DE/CE boot/unlock behavior + reboot persistence
under enforcing, not just mkfs encrypt-feature presence.

## Undo

Restore the plain R2 vendor or explicit RAM-data vendor through the existing
owner B-only workflow. Vendor rollback does not decrypt FBE data, and losing
software-provider state may make it unreadable. To return to a fresh plain
test filesystem, preserve any wanted test data first and use the bounded RAM-B
manual reformat; restore the plain vendor and rerun its two-boot marker test.
Original-zero restoration remains the full exact-range restore from RAM-B,
with original hash/GPT/boundary verification. No GPT restore, daily-data wipe
or real metadata/key deletion is part of this plan.
