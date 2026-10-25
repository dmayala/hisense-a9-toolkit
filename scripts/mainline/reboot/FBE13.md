# INTEG12 FBE: Begin failure confirmed; initial capture repaired

Host-only analysis of `sweep-integ12-20261024/mainline.tar`, boot100. No phone
operation or change to the delivered image/run. `fbe-extract.log` already says
`key_records=1 config_records=1`; `fbe-evidence-saved.log` contains KEY, CONFIG
and LOCAL. The initial hook's zero count is not absence of the native trace.

The archived logcat.prev lines952–968 establish the call chain:

- QCOM reports response status-8 for the existing Begin, then-33 for our
  diagnostic get-key-characteristics call.
- Keystore2 `createOperation` fails with `INCOMPATIBLE_BLOCK_MODE` (-8).
  Its stack includes `upgrade_keyblob_if_required_with`, but this log does not
  establish an actual upgrade invocation; there is no requires-upgrade-62 here.
- Vold `decryptWithKeystoreKey`, then `read_key` in mountFstab fail; vdc exits25.
  This happens before a metadata-encryption mapper/data filesystem can mount.

The new LOCAL record DOES exist (logcat.prev955 and kernel993): local_rc0,
verified0, hardware AES/GCM/decrypt all1, OS160000/system202605,
vendor20220405/boot20220405. CONFIGrc0 has the same OS/system/vendor values;
it has no boot-patch parameter. This resolves the previously missing current
key's clear public authorization evidence. No raw key blob is needed to repeat
that inspection. Local clear metadata remains unverified because TA
characteristics validation fails; its GCM flag cannot establish the secure
world's accepted key or root/boot context. The header's patch month and userspace
properties still do not measure the actual ABL-to-TA boot-patch value.

The initial capture bug is reproduced on the cached actual Android Toybox sed:
BRE escaped alternation (`\|`) exits0 with zero extracted records. GNU sed
extracts all three from the same synthetic input. All three hook paths now use
a portable marker expression; the existing Python full-match numeric whitelist
still rejects unknown tags and any extra/private fields. An explicit Toybox
regression exercises the fixed expression and redaction, with the old expression
as a failing control. Post-pull extraction remains separate from initial STATE.

This commit repairs collection, not hardware-key decryption. No cipher/provider,
TA boot identity, key bytes, metadata, secure storage, partitions or retry
behavior is changed. The response -8 despite a locally declared GCM key plus
TA characteristics-33 does not justify changing GCM or spoofing secure boot.

Next exact evidence needed from the phone owner: on a stock boot retaining the
SAME current metadata key/secdiscardable, does that key reopen, and what are
Begin/get-key-characteristics return codes? Only numeric status/public mode and
patch tags; no blob/nonce/identifier. A successful same-key stock reopen would
isolate the remaining comparison to TEE boot/backing context; failure there
would instead show that this key/state already fails outside the mainline boot.
If available, also compare the actual Keymaster TA boot-patch and lock/verified
state parameters; `ro.boot.*` and configure's absent boot tag are not that proof.
Do not wipe or regenerate before this comparison and call it an existing-key
repair. Fresh-key provisioning in integ4boot55 was already successful but is
not an existing-key reopen control.

Host evidence: `A9-build/mainline/fbe13-host/`. Main already has a028120 and
f98cb9f; INTEG12 source includes f98cb9f (the native LOCAL trace was rebuilt).
The collector fix and this report are the new handoff for the INTEG13 builder.
