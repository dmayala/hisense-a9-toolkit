# Integ10: restore the stock boot identity and remove archived runtime inputs

Boot91's `chars_rc=-33` means characteristics retrieval failed. Its zero AES,
GCM and version fields are initialized placeholders, not key authorizations.
`begin_rc=-8` is real; neither the permitted modes nor the failing key's bound
patch levels were recovered. Existing shared-key reopen remains unqualified
until the phone owner runs this candidate. No worker device access was used.

The integrated boot header advertised16.1.0 /2099-12; both the stock package
and golden daily boot advertise11.0.0 /2022-04. Offline analysis of the pinned
stock LinuxLoader PE (SHA2561dd55dcbb57612f949a93e94c926b3a69924f5941037e0a350ce022e25bc4dab)
establishes the actual transfer:

- `LoadImageAndAuthVB2`, RVA0x4e24, reads header-v2 offset44, splits OS version
  and patch, optionally replaces the patch with AVB's
  `com.android.build.boot.security_patch`, then calls0x6bc4.
- `KeyMasterSetRotAndBootState`,0x6bc4, sends ROT command0x201 and boot-state
  command0x208. The latter includes the OS/patch words supplied by that caller.
- ABL adds `androidboot.keymaster=1` and the actual verified-boot state. These
  are observations of static code, not proof of a particular boot's TA state.
- Stock libqtikeymaster4.so's constructor0x10da24 separately configures actual
  system OS, system patch and vendor patch through0x111544 /command0x2116.
  Its configure call does not supply BOOT_PATCHLEVEL. Changing `ro.boot.*`
  strings in Android would not repair ABL's earlier secure transaction.

REA records are retained in `integ10-host/{abl,keymaster}-rea.json`.
Core evidence IDs: ABL caller `ev_fce82b642ef2d3e8daf7f8c653f5498330052fc360e161b5e11d052f26fa4353`,
boot-state sender `ev_b47752dcc32408e40035c6476c3b41800d62d4c34e8508552de4de832a75e136`,
HAL constructor `ev_3954c37e9d59c7e9de255b16dfbca1c79e744376862509a0f4e8b778434a3a0a`.
This follows Android's [version binding design](https://source.android.com/docs/security/features/keystore/version-binding).

`stock-boot-identity.py` restores the golden daily boot's four-byte version
word. Kernel, DT, ramdisk, command line and modules remain byte identical.
The normal packers now default to that same boot/TEE identity. Android system
and vendor properties retain their actual levels. Hardware-wrapped encryption,
existing metadata/keys, TA binaries and the existing mount/encrypt decision
remain intact. No added format, key deletion, upgrade retry or crypto fallback.
The confirmed mismatch is fixed; its causal role in Begin-8 still needs runtime
confirmation. The future2099 value was shared with earlier integ4, whose
boot55 generated fresh keys; that success never proved an existing-key reopen.

The preload also observes the stock configure call, once with the original
arguments and result. Numeric `A9_FBE_CONFIG` records report the supplied public
levels and return code. `boot_patch_present=0` means that tag was absent in the
configure input, not that the TA's boot patch is zero. `A9_FBE_BOOT` maps only
four public boot properties to numeric enums: verified_state0unknown/1green/
2yellow/3orange/4red, lock values0unlocked/1locked/2unknown. It captures no
hashes, identifiers, keys, app IDs, HATs, nonces or raw property dumps.
The wrapped startup copies whitelisted CONFIG/KEY records to the durable
logger after vold returns, including successful mounts. Both launchers capture
`${A9_BTRY_LOGS}/fbe-evidence.log` (0600); the full sweep also writes
`acceptance.json`. Characteristics flags are meaningful only with chars_rc0.

Runtime archival repair: `prepare-vendor.py` expands six pinned, compressed
public platform CIL inputs from the repository into its own output. Its helper
import is source-relative. No `plain-deadline-*` or `adsp/d2-*` input is opened.
This includes77b60ba's enforcing diagnostic policy. Persistent otatools remain
the compiler dependency. Acceptance takes an explicit image, uses that image's
assets/firmware, invokes the source-adjacent b-try, and resolves installed `op`
at the existing credential request instead of opening an old release receipt.
No credential transport or storage behavior changes. Historical build recipes
still name their explicit build-time bases; they are not imported by a run.

Build: `python3 -B scripts/mainline/reboot/build10.py` after the focused Android
trace/hook tests pass. Default base is integ9 (FP9, capture, radio and enforcing policy on integ8/6r3); latest-main runtime
sources, matching acceptance assets and02d38b6 sleep logger are frozen under the
new output. The builder links enforcing policy, verifies ext4/init/ABL and
inherits the exact unchanged101-module/6243-import ABI audit. It preserves15GiB
free and refuses to overwrite an existing candidate. `run.sh` runs the full
owning acceptance sweep; `run-fbe.sh` is the short120s alternative. Neither is
executed by the builder or this worker.
