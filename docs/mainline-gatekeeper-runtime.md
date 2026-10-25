# Stock Gatekeeper runtime preflight after INTEG22

Host-only comparison of stock A9 binaries/configuration with the shipped
INTEG22 vendor. Gatekeeper's observed `libkeymasterdeviceutils.so` namespace
failure is agent's library-closure fix. This audit finds no additional
demonstrated QSEE, firmware, DAC, property or SELinux gap requiring a runtime
change. Host checks do not establish successful PIN authentication.

## Trusted application and initialization

There is no separate Gatekeeper TA on the selected stock HIDL constructor path.
`android.hardware.gatekeeper@1.0-impl-qti.so`, SHA256
`7cce2abe8ba7908d9ee66546885a5b4989cfb929b399bba0a7deb4565265f27c`,
calls `KeymasterUtils(1)` from `HIDL_FETCH_IGatekeeper` at **0x105e14**.
In stock `libkeymasterdeviceutils.so`, SHA256
`1231b993bde4c65982a7c8a22233e7063152596580e5b7d7f318917e8262fffd`,
the constructor at **0x1022d8**:

1. Reads `vendor.sys.listeners.registered`, sleeping100ms between checks until
   its value is exactly `true`. This is the real qseecomd property; no substitute
   readiness property or forced value is needed. It has no constructor timeout.
2. Calls `QSEECom_start_app` with directory `/vendor/firmware_mnt/image`, name
   `keymaster64`, and shared-buffer size0xa000; on failure it tries `keymaster`
   in the same directory. Both failures cause a stock assertion.
3. Sends GET_VERSION0x200 and negotiates the API version with0x207. Transport
   failure or nonzero TA status causes a stock assertion before registration.

The exported alternative `gk_initialize` at0x1028e4 starts `keymaster`, but is
not called by the inspected HIDL factory. It must not replace the constructor
trace. `spu_init` at0x102a28 returns0; this QSEE path needs no speculative SPU
device permissions. Modern enroll/verify use0x21001/0x21002 through the same
Keymaster TA. The auth-token HMAC comes from the TA response; this HAL needs no
new userspace HMAC secret, shared-secret daemon or key-copy step.

These stock addresses include Ghidra image base0x100000. Earlier complete REA
records and Evidence IDs are linked from [the PIN investigation](mainline-fbe-pin.md).

## QSEE app lookup precedes firmware reads

Stock `libQSEEComAPI.so`, SHA256
`2f70ad2dd4d3137f18df34022021bc3bf9533ac2161de0e1afc6e7d338d82094`,
uses the following path:

| Address | Operation | REA evidence |
| --- | --- | --- |
| 0x105014 | Opens `/dev/qseecom` RDWR, obtains QSEOS version, queries the named app, allocates the shared buffer and sets its memory parameters. An existing app skips disk loading. | ev_0502848f2a53348501a8bc844daeef8410b66e1d13926f8307cda9442d7d1472 |
| 0x1054b8 | App query ioctl0xc048970f: errno EEXIST means already loaded; a successful ioctl reporting absent selects the disk-load path. | ev_6b04600c9e6596ab924c1120fa9b6491faf73c2c50f15cc18c53ccebcb2e3b0e |
| 0x10561c | Reads the supplied directory's `NAME.mdt` and `NAME.bNN`, then submits LOAD_APP. No `/vendor/firmware` fallback appears in this routine. | ev_865c0ab8fba683f201b58fd8e6b01b07d441e0cca4f4bde9ee4e999fba0e73b4 |
| 0x105d24 | Allocates ION heap27 for the client buffer, maps its returned DMA fd RW and issues DMA_BUF_IOCTL_SYNC0x40086200. | ev_2965ce1ccba7f6fed74c56e392eba28d569e7f7ac3f653f5ce68dab0c661e70a |

The frontend's APP_LOADED_QUERY implements stock's EEXIST convention. Its
`app_attach` tracks shared references, allowing Gatekeeper and Keymaster to
hold the same TA concurrently. These normal opens require no added service
capability. Earlier saved Keymaster lookup/GET_VERSION established a preloaded
`keymaster64` control; the new Gatekeeper handshake still needs phone evidence.

Stock fstab mounts the modem FAT filesystem read-only at
`/vendor/firmware_mnt`, with context `firmware_file`. Mainline has no such mount
or directory. However, format-aware read-only inventories of both the saved
device modem image and stock `NON-HLOS_001360E1.bin` found237 entries under
`image/` and **no Keymaster/Gatekeeper filenames in either**. Stock's core TA
path therefore relies on the preloaded app, rather than those missing split
files. Creating a firmware mount or copying an unrelated TA would not supply
the absent stock files. No mount, TA bundle or firmware change is added here.
The literal stock `firmware_file` type is also undefined in this GSI policy;
its mount context must not be copied blindly.

## Effective service access

The stock service uses `early_hal`, user/group `system`. Mainline retains that
ordering and identity, uses enforcing `hal_gatekeeper_default`, and supplies
the stock HIDL1.0 default provider. INTEG22 already finds impl-qti before its
dependency load fails; no new HAL-selection property is indicated.

| Runtime prerequisite | INTEG22 configuration / effective access |
| --- | --- |
| QSEE device | `/dev/qseecom`,0660 system:drmrpc, `tee_device`; Gatekeeper uid system has DAC access. Effective open/read/write/getattr/ioctl/map allowed; source ioctl range0x9700–0x972c includes version/query/load/set-buffer/send. |
| ION device | `/dev/ion`,0664 system:system, `ion_device`; effective open/read/write/getattr/ioctl/map allowed, source xperm0x4900–0x490a. |
| Listener property | Exact bool property `vendor.sys.listeners.registered`, label `a9ml_tee_listener_prop`; effective read/open/getattr/map allowed. qseecomd owns its real value. `true` alone does not prove RPMB health. |
| Firmware | Existing `/vendor/firmware` directories0755 and files0644 use `a9ml_firmware_file`; Gatekeeper has directory search/read and file read/map. This is the actual installed type, not a missing `vendor_firmware_file` grant. |
| Executable/private libraries | Service0755 `hal_gatekeeper_default_exec`; libraries0644 and private directory0755 `vendor_file`; effective entrypoint and library read/map/execute allowed. Filesystem existence does not prove linker namespace resolution. |
| HIDL registration | Effective hwbinder access, binder call/transfer to hwservicemanager, and add/find of `hal_gatekeeper_hwservice` allowed. |

No direct HAL write access to persist/tzstorage is needed by the inspected
constructor: secure-storage requests are handled by qseecomd's existing
listeners. Neither a broad firmware grant, extra SPU access nor a permissive
Gatekeeper domain is introduced.

DMA sync0x6200 acts on the returned DMA buffer, not the ION device. The selected
frontend exports it directly in the caller's allocation context, without a
userspace `/dev/dma_heap` open. The retained matching Linux source creates an
S_PRIVATE anonymous inode; SELinux bypasses its inode ioctl checks after the
fd-creator check. Effective own-fd use is allowed. Adding ION0x6200, kernel-fd
use or DMA-heap access is not supported by this path.

## Host validation and next run

The actual combined INTEG22 policy was reconstructed from its recorded platform
and mapping inputs plus shipped vendor/public CIL. Its SHA256
`defff5315a70d46dfc425fc71ae3b1b5f5360fac970baa3a4fcc10435384b13b`
matches the installed binary exactly. Effective access queries passed for the
table above, including attributes, TE/RBAC/MLS. The older host libsepol cannot
read `functionfs_seclabel`; a query-only copy omits that single capability.
Production policy is unchanged. Extended ioctl ranges were checked in source,
not through binary xperm queries; live inode labels and AVCs remain unobserved.
Installed metadata, device/property configuration and both stock FAT inventories
were checked read-only. No image, physical partition or secure-storage write
was performed.

After agent's closure fix, require these distinct milestones:

1. The implementation and private dependencies load without a linker error.
2. The real listener property becomes `true`; otherwise the stock constructor
   can remain in its wait loop.
3. Existing-app lookup succeeds for `keymaster64` or `keymaster`, followed by
   KeymasterUtils TA API/TA version diagnostics without a start/version assertion.
   A missing app selects unavailable disk fallback; a firmware open error at that
   point does not establish a SELinux denial.
4. HIDL Gatekeeper default registers, then `29_pin_lifecycle` passes set,
   verify, lock/unlock and removal/cleanup. Registration alone is insufficient.

TA storage failure remains a separate possible blocker. Keep the RPMB
read-first guard and persist read-only: a failed read or a denied stock TA write
does not authorize table creation, RPMB programming or permission widening.

Host evidence: `A9-build/mainline/gatekeeper-runtime-20261024/` contains complete
original successful QSEE REA responses, FAT inventories and retained-log numeric
counts. `gatekeeper-runtime-preflight-host/` contains effective-access queries,
policy reconstructions, installed metadata and DMA source-identity checks.
