# Same-current-key stock TEE reopen control

User runs ONE `run-reopen.sh`; preparation/build/tests use no phone. Start with
normal Android A booted, no other phone owner. The launcher holds the existing
USB lock until its B owner and stock-A probe have finished. It prints numeric
result lines only. `A9_REOPEN_LOGS=/new/host/directory` selects a fresh log dir.

The standalone prepared bundle uses INTEG12's exact mainline boot/kernel/DT and
vendor as its binary baseline. Its bundled `prepare-vendor.py` applies current
main policy (including read-only key-file access) to a private vendor derivative
before flashing. The ABL/header stock version correction is retained. When
installed into INTEG13, the SAME launcher instead uses INTEG13's adjacent
boot/vendor images. No image from an old build directory is read at runtime.
The integrated B image's Keymaster implementation is already the stock QCOM
provider; it is the mainline kernel/boot environment being compared.

The control stage boots the EXISTING daily slot-A image, requiring stock4.19
kernel, root and the stock libqtikeymaster4 implementation. It does not flash a
stock boot/vendor to B, replace an A image, restart a provider or spoof boot
properties. It calls A's already-running HIDL default Keymaster service and
requires TRUSTED_ENVIRONMENT. No software provider is accepted.

Sequence, normally 6–12 minutes including image upload and A return:

1. Existing b-try owner enters A recovery and writes/readbacks vendor_b as
   usual. A short setup hook mounts metadata at its own temporary mountpoint
   `ro,noload`, reads the existing key directory, then unmounts its own mount.
   No key creation or metadata formatting is requested. Absent/unsupported
   input fails the control rather than generating a substitute key.
2. b-try flashes/boots the adjacent integrated boot_b with its existing kernel
   deadline. After B boot_completed, the hook reads those same files again,
   requires byte equality with the pre-B snapshot and captures numeric FBE
   evidence. There is no acceptance/Wi-Fi/fingerprint sweep in this launcher.
3. Original b-try returns to A and collects retained B evidence. Its existing
   authorized A-data repair may run if A otherwise fails to boot; that makes
   this control UNTESTED, never a crypto FAIL/PASS for the original context.
4. If A booted without data repair and the inputs match, a small bundled HIDL
   client receives the retained input packet on stdin. It gets characteristics,
   performs AES-GCM DECRYPT Begin/Finish, then exports the recovered persistent
   storage-key blob to a native hardware-wrapped ephemeral key. PASS requires
   all return codes0, hardware TEE, and exported length68. No plaintext FBE key,
   raw key bytes, hashes, nonce, ID or appId is printed or logged.
5. Client staging files and the private host RAM snapshot are removed. The
   existing A remains active; there is no second B attempt or follow-up reboot.

The application binding follows vold's SHA512 of its 128-byte padded
personalization prefix plus the saved secdiscardable. The provider prefix is
stripped only for hardware blobs. The outer GCM decrypted result is a persistent
storage-key blob, not the ephemeral68-byte key; the client explicitly performs
TEE RAW export and rejects a32-byte plain result. Sources:
[AOSP KeyStorage](https://android.googlesource.com/platform/system/vold/+/refs/heads/main/KeyStorage.cpp),
[AOSP KeyMint compatibility](https://android.googlesource.com/platform/system/security/+/refs/heads/main/keystore2/src/km_compat/km_compat.cpp).

Writes and readbacks:

- Flashes only boot_b and vendor_b; reads back vendor_b's image digest using
  existing b-try. Boot_b has b-try's existing flash-status check, not an added
  raw partition readback. A images, system/product/dtbo/vbmeta/firmware are not
  flashed. Normal slot selection returns to A.
- B's normal vold may write metadata/userdata while attempting the existing
  key mount. The launcher never requests encryptFstab/format; its initial
  snapshot requires that a key already exists. A return may use existing
  b-try's authorized TWRP data repair (userdata, and possibly first4MiB of
  metadata if its mapping is in use). Test data can be lost. A repair causes
  `stock_control_tested=0`; it cannot validate the original key context.
- Normal B logger writes kdebuginfo. Recovery, A and B use temporary script/
  staging files; client staging on A is removed. The control itself does not
  mount userdata, import a kernel key, write metadata keys, or call generate,
  upgrade, delete or deleteAllKeys. Normal A boot/framework uses its filesystems.
- No command reads/writes modemst/fsg/persist, devinfo/diag, GPT or firmware
  boot-chain partitions. No relock. Stock-A Keymaster uses its normal existing
  backing context; the client does not copy/restore secure state. No rmtfs or
  modem operation is added; the integrated image's RAM-only policy remains.
- Private encrypted inputs/secdiscardable live only in a0700 host `/dev/shm`
  directory and process memory/stdin, never in host logs or persistent A files.
  No identifiers are printed. Raw standard b-try boot logs stay private0600.

Typical final lines (details in `stock-reopen.log` and `result.log`):

```
PASS slot_A=1
PASS same_key_inputs=1
A9_REOPEN_TEE tee=1 chars_rc=0 begin_rc=0 finish_rc=0 export_rc=0 wrapped_bytes=68
PASS stock_reopen=1 wrapped_bytes=68
```

`-1000` means not attempted or no validated transport result. A real TA failure retains exact negative statuses and `FAIL stock_reopen=0`.
Transport/privilege/missing-input failure uses `FAIL stock_transport=0`,
`FAIL stock_context=0`, or `FAIL stock_control_tested=0 repair_before_probe=1`;
these are NOT evidence that the key was cryptographically rejected. A failed
A return reports `FAIL slot_A=0`. Existing b-try owns all return/repair work;
never launch a competing recovery while it runs. Worst-case time can exceed
20minutes with its existing A boot/repair timeouts. Nothing can promise a
healthy A if USB/recovery itself fails; the result reports that explicitly.

INTEG13 builder: `build13.py` (and `build12.py`) install the control automatically from
`--reopen-stage /home/user/A9-build/mainline/reopen-host`. For another builder,
call `reopen/prepare.py`'s `install(image_dir, stage_dir)` while that new image
is still under construction, before its final manifest. It adds
`run-reopen.sh`, the native client+libraries, hooks and pinned runtime. Do not
edit an active run/delivered image. Native fixture and host fake-transport
checks are preparation evidence; no real stock reopen is claimed.
