# Stock QTI gatekeeper vendor packaging

The integrated Cuttlefish vendor selected the nonsecure Gatekeeper APEX. Its
software authentication token cannot authenticate a PIN operation to stock QTI
Keymaster. This host-only derivative removes that selection and APEX and installs
the device's stock QTI HIDL 1.0 gatekeeper service and implementation.

```
python3 -I scripts/mainline/credentials/prepare-vendor.py \
  --base-vendor /path/to/vendor.img --out /fresh/output-directory \
  --stock-ref /home/user/A9-build/mainline/android/ref/vendor_a.img
```

The importable `prepare(source, target, out, stock_ref=...)` API writes a fresh
image and returns `result.json` data. Source and stock reference remain immutable.
The incoming vendor must already contain the hash-pinned QTI Keymaster private
ABI at `/vendor/lib64/a9-wrapped-stock`, listener property/policy, firmware labels,
and the normal vendor directories. `assets.json` records hashes only; no binary
assets are stored in git. The HIDL `android.hardware.gatekeeper@1.0.so` interface is extracted from the
matching GSI's hash-pinned VNDK v30 APEX and installed at `/vendor/lib64`. The
stock service's vendor namespace can resolve that path. INTEG21 crashed before
boot completion because the former check accepted a system-only copy that the
vendor linker could not find. No unrelated vendor interface is substituted.

The stock service starts in `early_hal`, as in the device's stock init file,
using enforcing `hal_gatekeeper_default`. The stock implementation's
`KeymasterUtils(1)` constructor waits for the actual
`vendor.sys.listeners.registered=true` property set by existing qseecomd. It then
uses the same keymaster64/fallback keymaster TA as Keymaster. No software HMAC
copy, readiness override, preload, service restart or new TA is introduced.

Packaging adds a HIDL hwbinder `@1.0::IGatekeeper/default` manifest, init service,
file and hwservice contexts, and explicit enforcing access for QSEE, ION,
listener-property reads and firmware/private libraries. Matching public platform
CIL is expanded from the existing boot-deadline gzip bundle and hash-checked.
Existing policy identity files are retained. The code reuses the existing ext4
refresh metadata helpers/replacement recipe without its unrelated subsystem
refresh or image-growth heuristic. New files get explicit modes and labels;
replaced files retain owner/mode/label/timestamps. No image resize is performed.

`result.json` separates existing-file `changed`, new-file `added`, and `removed`
paths, with source/stock/output pins, init/manifest paths and policy files. The
module checks payload and metadata readback, init syntax, policy compilation,
filesystem consistency and immutable input hashes. It needs debugfs/e2fsck and
the existing Android host secilc/host_init_verifier tools. Keep outputs local:
the base vendor contains private runtime payloads.

For the five focused host groups (including all unrelated regular files and
recursive DT_NEEDED plus new-object strong-import resolution):

```
python3 -I scripts/mainline/credentials/test-package.py \
  --out /prepared/output-directory --system-root /matching/extracted/system
```

The dependency test uses that GSI's own runtime APEX for Bionic. Its symbol check
covers the newly added proprietary service/implementation; it does not claim a
full Android linker-namespace or runtime credential test. Package success does
not prove PIN enrollment, authentication, reboot unlock or retention. Those are
coordinator-owned device checks.

The QTI-stack package also installs the independent read-only kernel-status
bridge from `../qseecom/diagnostics`. It has a static Android executable, dedicated
enforcing domain/init service, explicit label and a startup request before
qseecomd. Its strict numeric allowlist and logdw queue/retry behavior are described
in that directory's README. It does not use or weaken credential-domain policy.
`result.json.diagnostics` records its executable, init, domain and transport.


`../android/check-hal-needed.py` is the build-failing library-availability gate.
It covers vendor HAL executables, init services, HAL plugins/private bundles and
vendor APEX HALs, using installed service LD_LIBRARY_PATH and target GSI LLNDK
exports. An arbitrary `/system/lib64` copy cannot satisfy a vendor-private import.
This replaces the older global-filesystem closure as availability assurance;
the five package groups still check strong symbols, labels and unrelated files.
The integrated recipe runs the gate after Gatekeeper packaging and after any
additional camera packaging. Static closure is not full Android linker/runtime,
dlopened-plugin, SELinux or secure-world qualification.

New builds also install the matching wrapped-start script, physical persist RO
init service and stock-label read permissions together. The mount uses
`ro,noload,nosuid,nodev,noexec` plus a verified block RO flag; it replaces the
old writable metadata bind and never relabels or repairs physical persist.
`A9_SFS_OPEN` records each in-scope open outcome separately for persist and
tzstorage. See [mount behavior, diagnostics and host evidence](../../../docs/mainline-persist-readonly.md).

Before qseecomd starts, packaging also restores stock librpmb's discovery link:
`/dev/block/bootdevice` points to `/dev/block/platform/soc@0/4804000.ufshc`.
The latter is this board's mainline controller path. The bounded init wait and
symlink operate in `/dev`; they mount nothing and issue no storage request.
The RPMB programming/write guard remains unchanged. See the
[TA read failure and backend diagnosis](../../../docs/mainline-fbe-rr5.md).

Check this startup transform against a saved vendor without creating an image:

```
python3 -I scripts/mainline/credentials/test-rpmb-bootdevice.py \
  --vendor /saved/vendor.img --out /fresh/host-results
```
Boot122 follow-up: passthrough loading did not find the private Keymaster
helpers despite the init LD_LIBRARY_PATH. The service now uses the normal
vendor namespace. Stock libkeymasterdeviceutils.so, libqcbor.so and
libQSEEComAPI.so, plus the pinned matching libion.so from the existing stock-ABI
bundle, are installed at /vendor/lib64. This closes the complete recursively
missing Gatekeeper NEEDED set. Existing private copies remain for their other
users. Packaging tests check the real public closure and strong imports of
all added objects; a service-private search cannot make this test pass.

Optional host runtime link preflight (Android Bionic, no phone facilities):

```
python3 -I scripts/mainline/credentials/test-link-qemu.py \
  --vendor /prepared/vendor.img --system-root /matching/GSI/system \
  --out /fresh/qemu-results
```

This extracts only the real public service/plugin NEEDED closure, runs an
Android RTLD_NOW dlopen/factory-symbol probe, and starts the actual service
under QEMU with a five-second bound. It records whether the service itself
reached plugin loading. Missing host Android properties/Binder are not TA,
registration or PIN success. No private LD path or target device is supplied.
