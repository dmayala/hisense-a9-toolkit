# Stock / INTEG14 hardware-wrapped FBE configuration audit

Host-only, 2026-10-24. The encrypted-data algorithms and key directory match
stock. The observed failure is a Keymaster DECRYPT rejection before the kernel
receives a wrapped key. No cipher substitution or trust-property spoofing is
justified by this audit. Two small configuration fixes are merged:
`05b2f8d` restores block-device waits and explicit metadata format 2;
`165d734` matches stock directory ownership/modes inside the **existing
metadata-backed** TEE namespace. Neither establishes an existing-key reopen.
INTEG14 and all queued images remain unchanged; no image was built here.

The current higher-priority lead is the rollback-resistant key-id table's RPMB
read path. The TA worker's handoff infers a failed read at TA `0x117d5c` can
produce `-8`, followed by an empty cached lookup and `-33`. This is a static
explanation, not a captured TA trace. agent owns the RPMB implementation.
See [the shared read-first investigation](../ufs-rpmb/READ-FIRST.md).

Inputs are the actual package `$A9_DATA/HLTE556N/super_5.img` (vendor),
`super_2.img` (system), `boot.img`, and eight saved stock logs in golden
`edl_2026-10-11_132444/lun0/kdebuginfo.bin`, read with debugfs without mounting
or writing them. INTEG14 boot/vendor are compared directly; runtime evidence
comes from `sweep-integ13b-20261024/mainline.tar`, boot 104. Its prepared hardware
RC is byte-identical to INTEG14's. The overlay changes only that RC and two
kernel-hash files. INTEG14 has no measured runtime in this audit. Stock printed
cmdlines are truncated; boot arguments absent from that prefix remain unknown.
No identifiers, vbmeta digest values, key material or RPMB contents are retained
in this report.

| Configuration / path | Stock | INTEG14 / observed INTEG13b | Effect on vold / TA and disposition |
|---|---|---|---|
| Active fstab selection | ABL `fstab_suffix=default`; vendor `fstab.default`; UFS boot device | ABL still supplies `default`, but that file is absent; hardware `hlte556n` selects `fstab.hlte556n` | Expected filename fallback. The observed RPC reaches the correct wrapped-key path, not a missing-fstab error. |
| `/data` block name | `/dev/block/bootdevice/by-name/userdata` | `/dev/block/by-name/userdata` | Named aliases for the same intended partition; no slot suffix. Names alone are not a measured major/minor proof. No change. |
| `/data` filesystem and mount options | F2FS; `noatime,nosuid,nodev,discard,reserve_root=32768,resgid=1065,fsync_mode=nobarrier,inlinecrypt` | Same | No algorithm/inline-crypto configuration difference. |
| File encryption | `aes-256-xts:aes-256-cts:v2+inlinecrypt_optimized+wrappedkey_v0` | Same | No basis to change mode or remove wrapping. |
| Metadata encryption | `aes-256-xts:wrappedkey_v0` | Same | Not evidence of an incompatible cipher request. The TA error name alone cannot identify the failing internal branch. |
| Key directory | `/metadata/vold/metadata_encryption` (files under `key/`) | Same | No keydirectory spelling/namespace difference in vold's request. Key contents were not read here. |
| Other `/data` fs_mgr flags | `latemount,wait,check,formattable,quota,reservedsize=128M,...,checkpoint=fs` | Same except missing `wait` | Device-availability handling can differ after key retrieval. **Fixed in 05b2f8d**; does not explain the already observed TA rejection. |
| eMMC alternative | `fstab.emmc` uses `emmc_optimized` | Effective UFS/default row uses `inlinecrypt_optimized` | Not a regression: the stock UFS row is the relevant comparator. Do not copy the eMMC variant onto UFS. |
| `/metadata` source, type and mount options | Named metadata partition, ext4, `noatime,nosuid,nodev,discard` | Same source/type/options | Actual real metadata mount succeeds in boot 104. |
| `/metadata` fs_mgr flags | `wait,check,formattable,first_stage_mount` | Declared `check,formattable`; a bounded worker mounts it directly in `fs` | Missing `wait` restored by **05b2f8d**. First-stage disposition remains intentionally different; it is not added blindly to a manual-mount boot. |
| Metadata filesystem checking / formatting | First-stage/fs_mgr flow evaluates its flags | Direct `mount` does not run fs_mgr's check/format handling | Can matter for an unavailable/corrupt filesystem. Current metadata is mounted before DECRYPT, so this is not the observed failing branch. No new fsck/format action. |
| First-stage ramdisk fstab | Contains real metadata with first-stage flag | Only system/vendor; metadata initially has GSI tmpfs handling, then real metadata is mounted | Early consumers can see a different namespace. Audited vold startup reads fstab and precreates a dm device; its key read occurs later on the mount RPC. Current key request follows the real mount. [M] |
| Existing vs fresh-data decision | fs_mgr/mount_all flow | Existing `key/` directory selects `mountFstab`; its absence selects the inherited fresh encrypt/format RPC | A missing directory is not proof that userdata is blank. Current failure uses existing-key retrieval. No format/deletion behavior changed; launcher/data-flow ownership remains separate. |
| Checkpoint and quota policy | Filesystem checkpoint, quota, 128M reserve | Same fstab flags; manual vdc mount path | No changed encryption parameters. Manual init event/class sequencing differs below. |
| `ro.product.first_api_level` | 30 | 30 | Important: preserves Android 11 launch-device defaults on Android 16. |
| `ro.crypto.dm_default_key.options_format.version` | Explicit 2 | Absent, but Android 16 selects 2 for first API >29 | Effective match. **05b2f8d** makes 2 explicit and rejects conflicting input; this is parity, not a TA-error repair. [M] |
| `ro.crypto.volume.filenames_mode` | Explicit `aes-256-cts` | Absent; API 30 uses the CTS default | These properties select adoptable-volume policy; real `/data` already specifies CTS explicitly. No real-data mismatch. [F] |
| `ro.crypto.volume.metadata.method` | `dm-default-key` | Absent | Legacy property is not read by the audited Android 16 metadata mounting code; that code uses the fstab options. No demonstrated real-data effect. [M] |
| `ro.crypto.allow_encrypt_override` | true | Absent | Not read by the audited vold files; no override is used by this explicit wrapped-FBE request. Not copied as a speculative fix. |
| `ro.crypto.metadata_init_delete_all_keys.enabled` | Not explicitly set in audited stock property files | Actual GSI product explicitly false; Android 16 default is also false | Different framework generation, with destructive initialization disabled. Keep false; do not delete keys to pursue parity. [M] |
| `ro.crypto.state`, `ro.crypto.type`, metadata-enabled state | Established by stock mount/init flow | Worker publishes encrypted/file only after successful real F2FS mount; vold publishes metadata-enabled on success | Android 16 accepts empty/encrypted state at metadata mount. Reaching the observed TA call excludes its earlier unexpected-state rejection. No fake encrypted/ready state added. [M] |
| `vold.*` boot transitions | Stock mount_all and `vold.decrypt` actions drive framework class release | Manual vdc RPC plus guarded boot/class actions; no extra static `vold.*` override found in audited property files | Different orchestration, not a changed GCM input. Real-data failure remains visible; no ANR/crypto failure suppression added. |
| System OS / system patch properties | Android 11 / SDK 30 / 2022-04-05 | Android 16 / SDK 36 / 2026-05-01; configure reports OS160000 / patch202605 | Genuine upgrade, not a value to spoof. TA worker associates patch/version checks with upgrade status `-62`, not the identified RPMB `-8` path. Binding still requires functional secure state. [K] |
| `ro.vendor.build.security_patch` | 2022-04-05 | 2022-04-05; configure reports 20220405 | Matched. Earlier wrong vendor level is already corrected. [K] |
| `ro.vendor.boot_security_patch` | Not set in package vendor properties | Explicit 2022-04-05 | Additional property, consistent with stock level. Current stock constructor's observed configure has no BOOT_PATCHLEVEL tag. No invented tag/value injected. |
| Boot header OS / patch word supplied to ABL | 11.0.0 / 2022-04 | Same | Earlier future/2099 header mismatch is already fixed. Android properties cannot repair an earlier secure ABL transaction; do not regress this word. |
| `ro.boot.verifiedbootstate` | Stock cmdline orange | Actual saved mainline cmdline orange | Match. Collector `verified_state=3` means **orange**, not the TA's FAILED enumeration. Generic helper's missing-value fallback is not used here. [K] |
| `ro.boot.keymaster` | 1 | 1 | Both boot headers lack this literal; ABL supplies it. It is not proof that every listener/RPMB transaction succeeds. |
| `ro.boot.vbmeta.device_state` | unlocked in retained stock prefix | Absent from complete mainline saved cmdline | Collector `device_locked=2` means unknown. Generic Keymaster helper defaults missing state to unlocked, but that does not prove the A9 TA's secure ROT state. No spoofed property. [K] |
| `ro.boot.flash.locked` | Not seen in truncated stock prefix; complete stock value unknown | Absent; collector `flash_locked=2` | Unknown is not locked. Not a direct input in the audited vold / generic Keymaster configuration code. No fabricated lock state. |
| `ro.boot.vbmeta.digest` | Present, value withheld | Absent from complete saved mainline cmdline | Real ABL-output difference. Generic helper has a zero-digest fallback; do not equate that software fallback with the hardware TA's ROT. Attestation / earlier ABL path can differ; no copied old digest. [K] |
| Remaining vbmeta properties | `device` present (value withheld), `avb_version=1.0`, `hash_alg=sha256`, `size=7424`, `invalidate_on_error=yes` | All absent from saved mainline cmdline | Confirms an AVB-output-path difference. No direct read of these fields in the audited vold key retrieval. Earlier secure boot-state transfer remains a separate question, not fixed by strings. |
| `ro.boot.veritymode` | enforcing | Absent | Verified filesystem/AVB reporting differs. No direct DECRYPT parameter in audited vold; do not mistake orange boot state for this property or synthesize enforcement. |
| Hardware / transport of boot properties | `androidboot.hardware=qcom`; stock 4.19 kernel cmdline | `hlte556n`; mainline kernel cmdline; CONFIG_BOOT_CONFIG unset | Hardware rename intentionally selects this vendor/init/fstab. Both CONFIG_CMDLINE strings are empty. No forced built-in cmdline or demonstrated bootconfig parsing loss; missing AVB fields are absent from full saved cmdline itself. |
| qseecomd / Keymaster binary closure | Package stock daemons and libraries | Both daemons, libqtikeymaster4, keymaster utilities and QSEEComAPI byte-identical; private library search path | No provider-version or binary-patch mismatch in these six files. Diagnostic preload adds observation; tests preserve call parameters/results. |
| qseecomd before Keymaster | qseecomd starts during init; actual 4.1 service also starts during init, waits for listener property; target RC's old 4.0 name is not the real 4.1 definition | Real metadata + TEE bind worker completes, then explicit qseecomd and 4.1 starts in fs | Correct relative order retained. A generic service-running flag is weaker than listener initialization or successful RPMB I/O. No start/restart bypass added. |
| vold startup vs metadata | early-fs start, with stock metadata first-stage mounted | early-fs start precedes real metadata worker in fs | Namespace timing differs. Actual boot 104 mounts real metadata at 8.596500 s; configure and failed key DECRYPT occur at 20.819626/20.848272 s. Current key retrieval is after the mount. [M] |
| Keymaster readiness / late-fs | `wait_for_keymaster` before `mount_all --late` | Bounded vdc RPC; no equivalent waiter service in this GSI; HAL waits for actual qseecomd listener property | Potential latency/timeout difference, but observed configure 0 and DECRYPT -8 demonstrate a responding provider. No artificial readiness bit or unrelated waiter added. |
| TEE persist root / secure-file namespace | Physical persist mounted at `/mnt/vendor/persist` | Persistent `/metadata/a9-tee-persist` bind-mounted there | **Substantive backing-state difference.** Can affect secure-file storage independently of fstab ciphers. No physical persist copying/rebinding or contents change in this task; do not call it a proven RPMB cause. |
| TEE root ownership / mode | root:system 0771 | INTEG14 system:system 0770 | **Matched by 165d734** inside the existing metadata-backed tree. No secure state migration. |
| TEE `data/` ownership / mode | system:system 0700 | INTEG14 system:system 0770 | **Matched by 165d734**; directory metadata only, existing files retained. May affect listener filesystem behavior, but not proved as the DECRYPT cause. |
| Kernel / Keymaster bridge | Stock downstream 4.19 QSEECom/ICE | Mainline7.2 with A9 QSEECom, legacy wrapped-key ICE and UFS-query compatibility | Major implementation difference, not absent userspace encryption flags. Current failing key retrieval precedes ICE key installation. RPMB owner investigates the listener boundary. |

| RPMB / secure-storage configuration | Stock | INTEG14 / saved evidence | Verdict and owner |
|---|---|---|---|
| Listener libraries | librpmb, libssd, libdrmfs | All three byte-identical to actual package, in the private closure | No library-version mismatch. Transport and registration can still fail. |
| RPMB / secure-file / SSD listeners | Stock qseecomd initializes RPMB/SSD and its ten listener entries, including secure-file services | Same daemon/closure, A9 listener register/receive/reply bridge | Registration and functional reads are separate proofs. Retained boot 100 / INTEG13 history identifies RPMB listener 8192 registration/start failure despite init 0; see read-first report. agent owns repair. |
| Current 13b early listener evidence | Not applicable | Early logcat was rotated; offline numeric extractor finds no retained init/register/start/TA-log records | Zero retained counts are **not success**. Do not attribute the same exact registration phase to this boot without retained evidence. New f36a62f diagnostics are not in INTEG14. |
| RPMB well-known LU | UFS UPIU LUN 0xc4; SCSI LUN 0xc144 (49476) | Saved boot 104 enumerates LUN 49476 attached to sg1; device/boot WLUNs also enumerated | RPMB device discovery support exists. Enumeration does not validate authenticated reads or the TA table. |
| SCSI generic support | CONFIG_CHR_DEV_SG=y | Same | Already built in; no missing sg module to add. |
| Legacy UFS descriptor query | Downstream ioctl 0x5388 | CONFIG_SCSI_UFSHCD_A9_QUERY=y; existing narrow READ_DESC compatibility handler | Already present; do not add a second guessed ABI. Existing host tests are not runtime RPMB authentication. |
| RPMB frame transport | Stock librpmb scans sg0..15, GET_IDLUN 0x5382, descriptor ioctl 0x5388, then SG_IO 0x2285 | Existing generic SG frame path, custom descriptor query only | SG_IO is handled separately from the descriptor callback. Read-first safeguards and any programming/write policy remain agent's work. |
| In-kernel RPMB framework | Stock userspace SG route | CONFIG_RPMB unset | Not itself proof of missing RPMB: the frozen library does not use the new in-kernel provider API. |
| UFS BSG | Stock CONFIG_BLK_DEV_BSG/BSGLIB unset; ueventd nevertheless contains old `/dev/0:0:0:49476` 0600 system:system rule | CONFIG_BLK_DEV_BSG/BSGLIB and SCSI_UFS_BSG enabled | Different/extra interface. The retained library uses SG, so an extra ufs-bsg node is not a substitute for repairing its path. Static stock rule is not proof that a stock BSG node existed. |
| BSG / ufs-bsg DAC and label | Stale direct-node rule above; no dedicated matching vendor label found | No dedicated ueventd or vendor context rule for `/dev/bsg/*`, `/dev/ufs-bsg*` or direct WLUN name; platform `/dev` fallback is `device` | Exact live node path/label not captured here. Such a consumer could lack access, but it is not the library's demonstrated SG route. Report only; no broadened grant. |
| SG node ownership / modes | Librpmb discovers/owns selected node; vendor labels SG nodes | ueventd `/dev/sg*` 0660 system:system; qseecomd starts as root with CHOWN/DAC_OVERRIDE/SYS_RAWIO among explicit capabilities | Suitable declared discovery/access setup. Actual selected-node I/O remains unqualified. |
| SG SELinux label | `vendor_sg_device` | `a9ml_qsee_rpmb_device` | Different name, explicit tee read/write/open/getattr/setattr/ioctl permission. Actual packaged-policy queries pass; not a generic missing open/read grant. |
| SG ioctl policy | Stock tee has SG ioctl permission | Explicit tee xperm set 0x2285/0x5382/0x5388 | Required operation names are permitted. This does not prove buffer ABI, SCM listener registration or a successful RPMB transaction. |
| GPT / SSD secure-storage access | `vendor_ssd_block_device` on named SSD partition | `a9ml_qsee_ssd_device` on named SSD aliases; fs action creates `/dev/disk/by-partlabel/ssd` before qseecomd | Declared path/label support exists. Ten packaged tee/node permission queries (SG+SSD) pass; live GPT/SSD backend operation is not verified. No GPT/SSD write introduced. |
| `/dev/qseecom` | 0660 system:drmrpc, `tee_device` | Same | Device node declaration matches; listener transport can still fail inside registration. |
| Rollback-resistant key behavior | TA-worker handoff: A-made keys use rollback resistance | Boot 55 fresh generation reported -67 then non-RR fallback | Fresh non-RR provisioning can work while old RR keys fail. It does not validate rollback table reads. No removal of RR tags, table initialization, zero-table success or key replacement is a parity fix. |

The generic Keymaster helpers read OS/system/vendor levels and boot-state
properties, but that does not establish which Android strings the stock QTI TA
trusts. The earlier ABL secure transaction and the stock HAL configure call are
distinct. Its observed configure has OS160000/system202605/vendor20220405 and
no boot-patch tag. The TA worker associates version mismatches with upgrade
status; the new RPMB explanation must be tested at the listener/TA boundary.
Missing lock-property observations must not be turned into fabricated trust
values. Error labels and initialized-zero authorization fields after a failed
characteristics call are not a cryptographically validated key policy.

The directory-mode fix and the fstab/property fix preserve keys, backing paths,
OS/patch properties and wrapping. Four config test groups, actual-INTEG14
field-by-field/idempotence/RAM-refusal controls, three Android mksh metadata
success/failure controls, five Android ARM64 native FBE trace groups plus the
shell trace fixture pass. The regenerated trace library is byte-identical to
the old library. Ten read-only packaged SELinux permission queries pass.
These are host results, not a hardware reopen or RPMB-read PASS.

The next integrated build needs the fresh trace receipt at
`/home/user/A9-build/mainline/fbecfg-host-20261024/trace` (pass it as `--fbe`, or
rerun `test-fbe-trace.py` into a fresh stage). The old `fbe12-host` receipt pins
the previous wrapper source and correctly refuses the changed source. No
existing stage/image was overwritten. The latest main also contains agent's
`f36a62f` numeric listener evidence; its RPMB fix/guard remains separate.

Detailed private host evidence:
`/home/user/A9-build/mainline/fbecfg-host-20261024/` contains
`config-parity.json`, `stock-boot-args-public.json`, `provider-parity.json`,
`runtime-overlay-check.json`, `parity-fix-checks.json`, `rpmb-config-parity.json`,
`rpmb-runtime-evidence.log`, public AOSP16 sources and trace test records.
The TA worker handoff is
`local/agent-continuity/checkpoints/claude-25d6365545040bb8c07d5968.md`.

[M]: https://android.googlesource.com/platform/system/vold/+/refs/heads/android16-release/MetadataCrypt.cpp
[F]: https://android.googlesource.com/platform/system/vold/+/refs/heads/android16-release/FsCrypt.cpp
[K]: https://android.googlesource.com/platform/system/keymaster/+/refs/heads/android16-release/android_keymaster/keymaster_configuration.cpp
