# Offline vendor HAL dependency gate

INTEG21's stock Gatekeeper could not load android.hardware.gatekeeper@1.0.so.
The former package test found a copy under system/lib64 without establishing
vendor namespace visibility. The fixed package installs the hash-pinned VNDK30
interface at vendor/lib64. The gate must reject the unfixed image even though
its matching GSI contains a copy.

```
python3 -I scripts/mainline/android/check-hal-needed.py \
  --vendor-image /path/to/vendor.img --system-root /matching/GSI/system \
  --out /fresh/private/check-directory
```

It runs host readelf, never a target executable or a mount. Roots include native
init services, every vendor/bin/hw ELF, lib[64]/hw plugins, private libraries in
actual service LD_LIBRARY_PATH bundles, and vendor APEX HAL binaries/plugins.
It recursively checks DT_NEEDED using each service's init environment, ORIGIN
RUNPATH, vendor defaults and the matching GSI LLNDK exports. The two Bionic
runtime exports libdl_android.so and ld-android.so resolve to actual runtime
APEX objects; the latter is provided by linker/linker64 itself. Init's first
service definition wins unless a later definition explicitly says override.
Plugins and the three script-dispatched Wi-Fi services name their installed
init service context in ALIASES; changing those services requires updating that
binding, rather than searching every private library directory globally.

System-only private libraries cannot satisfy vendor imports. Each root/context
is checked independently, including cycles, symlinks and ELF class/machine.
Missing files, unsupported input/paths, malformed ELF/APEX or ABI mismatch fail
the build. The private JSON report records roots, dependencies, actual resolved
providers, failures and limitations. APEX payload images are temporary scratch;
source packages/images are read-only. No proprietary bytes enter the repository.

The build14 integrated recipe invokes this gate after credential packaging.
Any subsequent camera overlay must rerun it against the final vendor image;
integ22's assembler/finalizer enforce that second result. This replaces the old
filesystem-wide closure as the library availability gate. Credential tests
still check all three installed Gatekeeper objects' strong imports, hashes,
labels, policy, unique provider and unrelated vendor files.

This is static DT_NEEDED coverage. It does not qualify arbitrary future dlopen
names, symbol versions, every generated namespace rule, SELinux or secure-world
calls. The package tests and an actual coordinator-owned boot remain distinct.

Host smoke: `python3 -B scripts/mainline/android/test-hal-needed.py` exercises
five groups with real readelf/compiled synthetic ELFs and an offline APEX:
hidden system interface/vendor fix; transitive missing/ORIGIN; APEX inventory;
wrong ABI/symlink escape; cycles and independent service contexts/init ordering.

Android references:

- [Runtime linker namespace configuration](https://source.android.com/docs/core/architecture/vndk/linker-namespace)
- [Bionic linker: ld-android.so is added to all namespaces](https://android.googlesource.com/platform/bionic/+/3da2fec061833d3445990b4b891628dd4e757038/linker/linker.cpp)
- [APEX manifest fields](https://android.googlesource.com/platform/system/apex/+/refs/heads/main/proto/apex_manifest.proto)

INTEG22 follow-up: the Gatekeeper plugin must not inherit the service's private
stock-ABI bundle. That erroneous alias hid libkeymasterdeviceutils.so and
libqcbor.so; their recursive dependencies also require libQSEEComAPI.so and
libion.so in the normal vendor namespace. The package now installs these four
hash-pinned helpers in vendor/lib64 and removes Gatekeeper's private LD path.
The package symbol test likewise uses public vendor paths for this closure.

Every native vendor/bin/hw entry and every vendor/lib64/hw/*.so and
vendor/lib/hw/*.so is inventoried in the JSON, including ARM32 plugins.
An unmapped plugin always gets the normal vendor context. Camera/fingerprint
retain their explicit deployed private-ABI contexts, which contain different
versions of some HIDL libraries; these directories are never a global fallback
for another HAL. `--standalone-plugins` additionally reports all plugins in a
bare vendor context for diagnostic comparison; it does not assert that the
camera/fingerprint deployment uses that context. Both modes print every missing
edge. Standard vendor search includes lib/hw and lib/egl.

The selected ro.vndk.version is read from the actual vendor properties. A
nonempty version requires its exact system VNDK APEX and exported core/SP lists;
only those names may cross into it. The integrated VNDK-less vendor explicitly
sets an empty version, so an unrelated installed VNDK30 APEX cannot silently
satisfy a dependency. LLNDK comes from the matching GSI list and actual providers.

Seven host groups additionally cover all ARM32/ARM64 plugin inventory, no
service-environment leakage into the Gatekeeper plugin, and only selected
VNDK exports crossing the namespace.

Additional primary source: [vendor namespace paths and VNDK/LLNDK links](https://android.googlesource.com/platform/system/linkerconfig/+/c50534bdf5c154aed0fb1d720adddaa6c4e0cd12/contents/namespace/vendordefault.cc).
