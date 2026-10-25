# INTEG23 host delivery — 2026-10-24

Source main commit: `ee79d2f0534e384907bd30535df5368a2f331d4e`.
Frozen detached worktree: `/home/user/A9-wt/integ23` (tracked clean).
Main checked before kernel build at `2026-10-24T19:35:13.137478+00:00` and
immediately before assembly at `2026-10-24T19:40:15.314304+00:00`;
both selected this source.

Includes `7cede33` crash evidence, `e63767c` UFS discovery link before qseecomd,
`f9a91f8` runtime preflight report and `ee79d2f` complete missing Gatekeeper
NEEDED closure. All earlier integ22 changes, including `94f70e7` encoder sizing,
SFS diagnostics, read-only persist, PIN assets and Gatekeeper interface remain.

## Gatekeeper correction and negative control

Fix commit: `ee79d2f0534e384907bd30535df5368a2f331d4e` (ff-only main).
The earlier gate incorrectly assigned the plugin the service's private LD path.
Boot122 demonstrated that default-namespace dlopen did not find those libraries.
The corrected gate against unmodified integ22 exits1 and prints all missing edges:

- libkeymasterdeviceutils.so from android.hardware.gatekeeper@1.0-impl-qti.so
- libqcbor.so from android.hardware.gatekeeper@1.0-impl-qti.so

Recursive inspection adds missing libQSEEComAPI.so and libion.so. The package
installs all four in /vendor/lib64, alongside the existing pinned interface.
The first three are exact stock vendor bytes; libion is the pinned matching
VNDK helper from the existing stock-ABI bundle. Private copies remain for other
users. Gatekeeper's private LD_LIBRARY_PATH is removed. No firmware mount,
TA bundle or new QSEE/ION/property permission is added by this fix.

The gate inventories every native vendor/bin/hw binary and every lib64/hw/*.so
and lib/hw/*.so. This image contains19 native bin/hw roots and10 lib64 plugins;
ARM32 inventory is additionally exercised with a real ARM32 fixture. Init
services, declared private bundles and vendor APEX HALs are recursive roots.
Actual selected VNDK exports and GSI LLNDK providers are checked. An unrelated
installed VNDK APEX or a different HAL's private directory is not a fallback.
Both image gates pass505 deployed contexts /646 ELF objects /0 missing.
Seven real-ELF synthetic groups pass. The five Gatekeeper packaging groups
also verify public-path strong imports, labels, policy, unchanged files and ext4.

Full investigation, missing lists and the separate bare-context diagnostic:
[runs/integ22-gatekeeper-closure-20261024.md](../../../runs/integ22-gatekeeper-closure-20261024.md).
The camera/fingerprint private ABI contexts remain separate; the report lists
all33 additional bare-context comparison edges and explains their scope.
Read-only negative-control logs: /home/user/A9-build/mainline/integ23-gates-host/integ22-final-audit.log.
Full large JSON reports were losslessly gzipped with compressed-reports.json receipts.

## Host Android linker execution

The actual final vendor's QTI implementation loads with RTLD_NOW under
qemu-aarch64 and the real Android Bionic linker; HIDL_FETCH_IGatekeeper resolves.
Only public vendor/system/runtime paths are supplied, with no private stock
library path. The real service's initial ELF also links and runs, reaching the
five-second host bound (rc124) while Android facilities/properties are absent.
Its trace does not show plugin opening; the separate RTLD_NOW probe proves
that loading/relocation step. TA handshake, HIDL registration and PIN success
remain coordinator-owned runtime checks, as described in
[the preflight report](../../../docs/mainline-gatekeeper-runtime.md).
Evidence: /home/user/A9-build/mainline/integ23-host/qemu-link/tests.json, dlopen.log, service.log.

## Build and verification

Same integrated recipe as integ22, with immutable INTEG17 binary baseline,
fresh native kernel, logger/policy, acceptance assets, FBE/listener replay and
current encoder trace. Fresh AF/torch modules use this kernel's config and
Module.symvers; final103 modules /6,313 imports have zero CRC/vermagic failures.
Current crash capture, scrubber, loop detection and runtime tool closure are
physically included and checked. New UFS setup, unchanged read-only persist,
PIN environment, both launchers, current source/runtime/assets and ABI pins pass.
No storage/TA/hardware entry point was exercised by the host tests.

The new64-file QEMU crash fixture initially hit its25-second host timeout under
parallel build load. The unchanged case passed alone, and the full source suite
passed on a serial rerun. Original failure logs are retained; no production
or fixture deadline was weakened. Crash controller synthetic timeout/reap/
cadence tests pass. Only verified worker-owned kernel intermediates were
archived/removed for disk space; old images and sweep trees were preserved.

Artifact directory: `/home/user/A9-build/mainline/integ-20261024-23`.

| Image | Bytes | SHA256 |
|---|---:|---|
| `boot.img` | 14000128 | `2fffa17ab44230236a143ba0bf0021b59763d22bcf35eb479c33533e077b9e87` |
| `vendor.img` | 847532032 | `d32b237c400adb782f0722a80c13e2c4ec8c6688d33587b8443edb41d21825e9` |

Final build/assembly/finalization rc=0. Both image files exist and are nonempty.
All eight adjacent SHA256SUMS entries pass. ABL/init/ext4, final HAL gate,
Gatekeeper five groups, final-vendor QEMU loading, source and runtime checks pass.
Records: integrated-build.json, host-checks.json, source-handoff.json.
Drivers, logs and return codes: `/home/user/A9-build/mainline/integ23-host`.

Both launchers default FORMAT=0 and honor FORMAT=1. Inert checks verify
run.sh HOLD=2400 and the exact PIN lifecycle environment opt-in.

## Exact sweep command (phone coordinator only; not executed here)

```sh
cd /home/user/A9-wt/integ23
A9_HOME="$PWD" A9_DEVICE_JSON=/home/user/A9/local/device.json \
A9_BTRY_LOGS=/home/user/A9-build/mainline/sweep-integ23-f1-20261024 \
A9_BTRY_FORMAT=1 A9_BTRY_HOLD=2400 \
A9_SWEEP_PIN_LIFECYCLE=fresh-disposable-formatted-b \
/home/user/A9-build/mainline/integ-20261024-23/run.sh \
> /home/user/A9-build/mainline/sweep-integ23-f1-20261024.out 2>&1
```

Fingerprint launcher remains run-fingerprint.sh. Host only: no adb, fastboot,
EDL, USB or phone access.
