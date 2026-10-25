# FBE after integ11: diagnostic repairs, decryption unresolved

No phone access was used. The active sweep and delivered integ11 remain
immutable. These changes repair observation; they do not yet fix the existing
metadata key's decryption or establish a real-data acceptance PASS.

The initial sweep hook has KEYrecords0. Its subsequently saved boot97 and the
separate saved integ11 fingerprint boot95 both
has CONFIGrc0 (OS160000, system patch202605, vendor patch20220405, no boot-patch
configure parameter), then Begin-8/characteristics-33 and vold25. A missing hook
record therefore does not establish that Keymaster never ran. The post-return
extractor recovers both records from the actual sweep without a device command;
`fbe12-host/sweep-fbe-evidence-saved.log` holds the numeric evidence.

Stock's saved kernel cmdline reports orange, keymaster1, no flash-lock property,
vbmeta device_state unlocked and veritymode enforcing. Integ4boot55 and integ11
boot95 both report orange/keymaster1 but no flash-lock or vbmeta-device-state
property. In our numeric protocol lock value2 is UNKNOWN, not unlocked. This
property difference is shared with successful fresh provisioning and is not
an evidenced decryption cause.

Integ4 and11 have identical real wrapped fstab, stock Keymaster service,
implementation/utilities/QSEE libraries and qseecomd; saved daily vendor agrees.
The wrapped-start script changes only diagnostics/readiness. Integ4boot55
generated fresh keys after the normal rollback-resistance refusal, then mounted
68-byte wrapped keys/native AES-XTS. It did not prove an existing-key reopen.
The current failure precedes the kernel receiving its wrapped key.

The interposer's characteristics structure was reversed. Stock's ABI places
hw_enforced first, sw_enforced second, as documented by
[AOSP](https://source.android.com/reference/hal/structkeymaster__key__characteristics__t)
and confirmed in the shipped deserializer. The corrected test backend writes
offsets0/16 independently, with deliberately different software attributes.
Actual Android ARM64 replay reproduces the old library's false hardware zeros
and checks the corrected values. Existing chars_rc-33 values still mean no
TA-validated authorizations were returned. Begin tracing now retains any early
negative DECRYPT status, preserving the original call/arguments/result and
two-record bound; previously only-8/-62 produced a record.

The hook also reads the current boot's retained logcat files and reports
whether retained kernel/logcat files actually opened. A valid info header alone
no longer implies successful log reading. After the original b-try owner
returns and pulls its boot logs, the source-adjacent offline extractor saves
`fbe-evidence-saved.log` (0600). It contains only whitelisted KEY/CONFIG/LOCAL records
and numeric counts. It stays separate from initial `fbe-evidence.log`, because
a full sweep can reboot and its final boot must not be mixed with initial STATE.

```
python3 -B scripts/mainline/reboot/fbe-onb.py --saved-boot HOST_BOOT_LOG_DIR --out HOST_OUTPUT
```

Stock QCOM's CBOR wrapper has a clear characteristics field before its opaque
encrypted material. `fbe-public-auth.py HOST_SAVED_BLOB` reads only exact public
AES/mode/purpose/patch tags. It emits no opaque integer, key ID, nonce, bytes,
hashes, unknown fields or input path, and marks `cryptographically_verified=0`:
local parsing is not TA acceptance. Software provider, malformed/truncated/
oversized input and ambiguous maps are rejected.
[Keystore compatibility](https://android.googlesource.com/platform/system/security/+/refs/heads/main/keystore2/src/km_compat/km_compat.cpp)
defines the outer provider marker; the stock parse/getCharacteristics routines
establish the inner format. An older golden metadata key declares hardware
AES256/GCM, encrypt/decrypt, OS160000, system202605, vendor/boot20220405. Those
old values cannot establish the current metadata key's authorizations.

The interposer now parses those exact public tags from the existing failed
Begin's key argument, without reading any new file or making another TA call.
The bounded, allocation-free parser rejects malformed/oversized/ambiguous
maps and skips opaque fields. `A9_FBE_LOCAL` reports only AES/GCM/decrypt flags
and OS/system/vendor/boot patch numbers, explicitly `verified=0`. Native Android
ARM64 fixtures check the format, hardware/software distinction, missing tags,
opaque-field exclusion and malformed refusal. Both durable startup logging
and the hook/post-pull extractors accept this numeric record. This observation
does not change the original operation, stored key or cryptographic policy.

No current metadata key blob was found in supplied host logs/images; its host
path has been requested. Actual current authorizations are needed before
choosing a mode/version/TEE-state repair. Boot-patch-day fallback and isolated
TEE backing state remain hypotheses. No vbmeta/secure-boot change, seed restore,
key deletion, format/upgrade retry or alternate provider/cipher was introduced.

Evidence: `A9-build/mainline/fbe12-host/` contains filtered comparisons, REA
records (session closed), native/raw-ABI control, public-auth fixtures, Android
shell read/error fixtures, and boot95's numeric saved evidence. Tests do not
qualify hardware decryption or zero sweep FAILs.
