# INTEG22 host delivery — 2026-10-24

Source main commit: `9374b919fbc64833ee8e80c6259f7a1a44b148a4`.
Frozen detached worktree: `/home/user/A9-wt/integ22` (tracked clean).
Main checked before kernel build at `2026-10-24T18:45:20.248437+00:00` and
immediately before assembly at `2026-10-24T18:48:24.135113+00:00`;
both selected this source.

Includes `9003fc6` SFS routing, `c4c2833`, `94f70e7` Venus encoder input sizing,
`2310c1d` reboot diagnostics, and `06b45f6` SFS diagnostics with `0515aea` /
`9374b91` read-only physical persist and RAM mountpoint preparation. The
PIN/gatekeeper assets and all earlier integrated changes remain included.

Gatekeeper repair: `f9fde994b4703abe9c6be1ad18d940e75c4fb380` installs the pinned
matching GSI VNDK30 `android.hardware.gatekeeper@1.0.so` in `/vendor/lib64`.
Interface SHA256: `3e98ca4e46580cdde6d07f170522ad857664621008407a22f8b903ed1c73962a`.
The same commit adds the mandatory recursive readelf NEEDED build gate for
shipped vendor HAL binaries, init services, plugins and private service
bundles, including vendor APEX HALs. Both the base assembly and final camera
overlay pass: 505 contexts, 642 ELF objects, zero missing dependencies. The
unfixed integ21 negative control fails on exactly the two missing Gatekeeper
interface edges. Five synthetic gate groups also pass. This is static NEEDED
coverage; generated linker rules, future dlopen names and secure-world behavior
still require runtime evidence.

Retained-log archive retry: `b32d9318ce07d091b7d823042188e63c9df4668c` retries a
failed exec-out or invalid tar once after a 3-second wait and bounded device
wait. Each attempt overwrites a private partial file; only a validated archive
is published. Remote archives remain until successful local extraction. Five
fake-only retry groups and the production FORMAT=1 fake-transport case pass.

Uses the integ20/21 recipe with INTEG17 as the immutable binary baseline:
fresh native kernel, current FBE/listener replay, logger/policy, encoder trace
payload, acceptance assets and stock QTI Gatekeeper. Acceptance assets were
rebuilt from current Probe.java after checking the provided PIN bundle.
The encoder payload consumes the compiled sizing fix and its current replay.
The two AF/torch modules were freshly built against integ22's config and
Module.symvers; the final 103-module audit checks 6,313 imports with no CRC or
vermagic mismatches. Existing integrated camera/media modules are retained.

Persist packaging includes its bounded service before QSEE startup, block RO
set/query, and `ro,noload,nosuid,nodev,noexec`. There is no metadata overlay over
persist. Six synthetic Android-shell mount-flow groups, RAM mountpoint behavior,
init ordering/verifier, exact installed script/service and policy rules pass.
Fresh data-handoff and SFS routing checks pass. The current passive observer
passes native/ARM64 tests; the diagnostic bridge passes schema 18 and 64 actual
synthetic logdw datagrams. No physical filesystem was mounted by this worker.

Artifact directory: `/home/user/A9-build/mainline/integ-20261024-22`.

| Image | Bytes | SHA256 |
|---|---:|---|
| `boot.img` | 13996032 | `0d16a9417a0cf0643338921410975fdf1c4e921ada04e3a5b43978cd905b7c75` |
| `vendor.img` | 847532032 | `a03283634b9a15219bedd571c0c3a70ac728818d8b1d36f2d7c577661fba948b` |

Adjacent `SHA256SUMS` verifies all eight kernel/image inputs. Records are
`integrated-build.json`, `host-checks.json` and `source-handoff.json`.
Drivers, logs, return codes and retained reserve-guard failure evidence are in
`/home/user/A9-build/mainline/integ22-host`. Host packaging/negative-control evidence is in
`/home/user/A9-build/mainline/integ22-gates-host`; large host-test reports/logs
were losslessly compressed with verification receipts. Only worker-owned
temporary derivatives were reclaimed; earlier integrated images and sweeps
were unchanged.

Final assembly, Gatekeeper five groups, final checks and finalization rc=0.
Boot/vendor files exist and are nonempty; `sha256sum -c SHA256SUMS` passes.
ABL, init, ext4, 103-module ABI, source/runtime/asset pins, logger follow replay,
acceptance/PIN and recording checks pass. Both launchers default FORMAT=0 and
honor FORMAT=1. Inert launcher checks verify the exact PIN environment token
and run.sh HOLD=2400, plus invalid-format refusal.

## Exact sweep command (for the phone coordinator; not executed here)

```sh
cd /home/user/A9-wt/integ22
A9_HOME="$PWD" A9_DEVICE_JSON=/home/user/A9/local/device.json \
A9_BTRY_LOGS=/home/user/A9-build/mainline/sweep-integ22-f1-20261024 \
A9_BTRY_FORMAT=1 A9_BTRY_HOLD=2400 \
A9_SWEEP_PIN_LIFECYCLE=fresh-disposable-formatted-b \
/home/user/A9-build/mainline/integ-20261024-22/run.sh \
> /home/user/A9-build/mainline/sweep-integ22-f1-20261024.out 2>&1
```

Fingerprint launcher remains `run-fingerprint.sh`. Host only: no adb, fastboot,
EDL, USB or phone access, and no hardware qualification is claimed.
