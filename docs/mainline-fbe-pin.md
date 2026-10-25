# PIN authentication: pair Gatekeeper with QTI Keymaster

For the INTEG22 dependency failure and the subsequent TA, firmware, property and
effective-policy prerequisites, see the [Gatekeeper runtime preflight](mainline-gatekeeper-runtime.md).

Host-only investigation of the retained integ18 image and the supplied private
PIN/fingerprint logs. No phone commands were run. Integ18's empty retained boot
archive is final; row29/RPMB qualification now awaits integ19. This investigation
does not change the RPMB read-first guard or authorize KMTB/RPMB writes.

The concrete configuration defect is a mixed authentication stack. The integ18
vendor selects `com.android.hardware.gatekeeper.nonsecure` in build.prop and
contains its software Gatekeeper APEX. Wrapped FBE separately installs the stock
QTI Keymaster service and libraries. Setting a PIN can create a software
Gatekeeper password handle, but its authentication token does not establish
authentication to the QTI trusted application. The supplied log has two sequences
of password-token submission followed by Keymaster status -26, keystore
`KEY_USER_NOT_AUTHENTICATED`, and SyntheticPasswordCrypto blob-decryption failure.
The retained provider mismatch explains that boundary; successful verification
after replacement still requires the owned phone run.

## Stock trusted-application path

Stock QTI Gatekeeper is not a second software HMAC implementation. REA of
`android.hardware.gatekeeper@1.0-impl-qti.so` shows `HIDL_FETCH_IGatekeeper` at
0x105e14 constructing `KeymasterUtils` with security level 1. The utility
constructor at 0x1022d8 in `libkeymasterdeviceutils.so` waits for the real QSEE
listener property, opens `keymaster64` with `keymaster` fallback, obtains the
version with command 0x200 and negotiates the version with 0x207. Gatekeeper
verification uses that same TA (modern command 0x21002, legacy 0x1002).
`GatekeeperSerialize::deserializeClientVerify` at 0x1055b8 returns a 69-byte hardware auth token,
including the TA-provided HMAC at byte offset 0x25. No userspace key-export or
HMAC-copy step was observed or is introduced by the fix.

Gatekeeper evidence: ev_09923a4a86419ed86ca499eb440423549f11f691d400819121a516131f8907a4
(factory), ev_09ee87fa062209a4d24d034c4ccf6ec6f8fa938c5a566b392a7d94ca2e2ea50f
(utility constructor), and
ev_7ff5b34e1255fa1a5f48323be64bbe112774908b3eb621a21d35accf892f8ae2
(token deserialization). Addresses are Ghidra image addresses. Recovered code
describes the shipped binary; it is not a trace of a successful PIN operation.

The stock QTI Keymaster library's `get_hmac_sharing_parameters` at 0x112b1c
and `compute_shared_hmac` at 0x112bec delegate to the TA with commands 0x220e
and 0x220f (legacy 0x20e and 0x20f). `begin_operation` at 0x112010 delegates
to command 0x210f. These are the provider's existing secure operations; adding
a host-generated shared secret would not repair the wrong Gatekeeper producer.
REA evidence ev_fc1f0ff733d002cf7e83d913a91f50a78802338dd3f0a0cd2fbdf4033bc69c90,
ev_2786568f1683ffdc8f597afd6782aa7ab03023b48d111f70974925421dfba275 and
ev_b05f91ef7c33840e974c3b3fca72a965e53d38e04d0f56adf565d56706e10cc4.
The analyzed Keymaster SHA256 is
beb923d3e09579bbf54360a55fa19af3b4a8ee5be9446d9798199a909d46b274.

Complete REA responses are retained locally under
`A9-build/mainline/fbe-pin-{gatekeeper,keymaster}-20261024`. Reports contain no
PINs, token contents, secure user IDs, device identifiers or key material.

## Verification boundaries

The separate `Km(-10003)` generation failures and missing IRPC implementation
are present in the log too. They do not establish why password authentication
fails; this change must not be reported as repairing either error without new
evidence. A legacy Keymaster device is not proof of remote-provisioning support.

The existing software Gatekeeper's password handles and synthetic-password
protectors are not portable to the stock TA. Qualification must use the user's
already authorized disposable fresh-format B flow. Installing the provider is
not a migration or a way to recover a protector made by the software provider.

The selected software implementation provides a useful direct comparison:
`SoftGateKeeper::GetAuthTokenKey` at 0x106f30 obtains its key from
`SoftSharedSecret::HmacKey` at 0x105e70; `computeSharedSecret` at 0x105610 derives
the key in userspace, and `ComputeSignature` at 0x107110 signs through userspace
HmacSha256. Evidence ev_6c7abc04ec35d5e93c9a94edc344807763faaad8670e2850dd857915e9ab5ed7,
ev_cccc4e9ab9ea4003afe0e13d02dde7f7633c7a87e450618a42f39076c13634f8 and
ev_62db8b1a78dfe776f53f1fdd8f37fa60c3eb72129d073c34b8d8fa8da8ae982d.
Pairing the stock providers restores the shipped TA route instead of making
the QTI provider trust software-generated tokens.

## Fix and host validation

`scripts/mainline/credentials/prepare-vendor.py` removes the nonsecure Gatekeeper
APEX and selection property, installs the hash-pinned stock service and
implementation, and selects one HIDL 1.0 default provider. It reuses the
validated private QTI library closure already installed for Keymaster. The
service runs in enforcing `hal_gatekeeper_default`, waits through the stock
constructor for the real listeners property, and receives explicit QSEE/ION,
property, firmware and library access. No TA, kernel, auth-check, secure secret,
RPMB guard or user credential is patched. Both integrated builders apply this
step before recording final vendor hashes; the generic builder retains its
software-only base when no QTI Keymaster is installed.

Actual immutable-integ18 vendor derivation passed five host groups: unique
provider/labels, 1,347 unrelated files with identical content and metadata,
21-object recursive library closure and both new binaries' strong imports,
policy/init compilation, and filesystem/no-growth checks. Its four added files
and one removed APEX are recorded separately from five existing-file changes.
The host-maintained GSI image SHA256
14525f4852a3c56f816a6de15fd45b93bdf390503b5c8e01b47b2ff6331ef793
supplies the tested HIDL interface and runtime APEX byte-for-byte. This is a host
image identity check, not phone readback. An independent medium source reviewer
approved this bounded provider/policy change; actual runtime linker discovery,
TA verification and credential success remain for the phone owner.

Acceptance commit `22f0080` adds `29_pin_lifecycle`. On disposable freshly
formatted B data, opt in with
`A9_SWEEP_PIN_LIFECYCLE=fresh-disposable-formatted-b`. The check refuses an
existing credential, requires wrapped-FBE PASS, sets a generated test PIN,
requires explicit successful `locksettings verify`, observes a secure locked
keyguard, authenticates with normal UI PIN input, removes the PIN in finally,
and verifies unsecured state. The PIN travels on stdin and is absent from
receipts. Failed or uncertain removal fails cleanup and stops later features.
The source-pinned probe APK and complete acceptance assets have been rebuilt
offline at `/home/user/A9-build/mainline/fbe-pin-assets-20261024` (including
DEX, alignment and signature verification). Supply that directory through the
integrated assembly's `--assets` option; inheriting an older bundle fails the
source-pin check.

Five focused fake-transport/actual-Android-mksh groups, the existing acceptance
self-test and API36 Java compilation pass. Source review and Python compilation
cover the build hooks. No host result is a successful PIN, fingerprint or
post-reboot CE-unlock result. On the next owned fresh-format run, require
`29_pin_lifecycle` PASS with set/verified/locked/unlocked/removed/finally_unsecured
and cleanup_ok all 1. Then test fingerprint enrollment and cold-boot PIN/CE
unlock separately before claiming credential-bound storage qualification.
