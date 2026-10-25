# INTEG21 host delivery — 2026-10-24

Source main commit: `33a5ef640efac9daf5d35026f925d4f35dc58f07`.
Frozen detached worktree: `/home/user/A9-wt/integ21` (tracked clean).
Main checked before kernel build at `2026-10-24T17:37:38.574555+00:00` and
immediately before final assembly at `2026-10-24T17:44:06.373993+00:00`;
both selected this source. Later main changes, including `c4c2833` and
`9003fc6`, are outside this frozen build.

Includes `26acc22` RPMB logcat bridge, `83591e1` rear AF/torch, `f82078a`
acceptance crash names, `05c5f81` rootfs kmsg read/backoff, `028ff53` tagged
boot-status probe, `58a6303` QCRIL startup and `25e1c5e` MP4 sample readback.
Also includes current-at-freeze `0d5a330` RPMB guard/listener/query diagnostics,
`549c59b` passive backend observation and `33a5ef6` evidence parsing.

Uses the INTEG20 recipe: fresh native kernel, FBE/listener replay, encoder trace
stage, logger/policy, rebuilt acceptance assets and stock QTI Gatekeeper,
using INTEG17 as the immutable binary baseline. The PIN implementation and
Gatekeeper asset hashes are pinned to this source. Acceptance assets were
rebuilt again after selecting `25e1c5e`; the stale supplied bundle was unchanged.

AF/torch additionally packages the updated dual-camera DT, HAL, tuning and
loader. Existing integrated camera/media modules are retained. The two new
modules were freshly built from the frozen source using integ21's own config
and `Module.symvers`, rather than the old camera cache's export table. The
final 103-module audit checks 6,313 imports with zero CRC/vermagic mismatches.

Artifact directory: `/home/user/A9-build/mainline/integ-20261024-21`.

| Image | Bytes | SHA256 |
|---|---:|---|
| `boot.img` | 13996032 | `fcb85edb071c2381b0b241230cf1924c34c5bbc8e87bb6e93e9ffca2a017d1b8` |
| `vendor.img` | 847532032 | `d2d2b4a873addbf353d3bbf2ebcf2c1ae8e4c862442a3a10709294a73ab716d2` |

Adjacent `SHA256SUMS` verifies all eight kernel/image inputs. Records are
`integrated-build.json`, `host-checks.json` and `source-handoff.json`.
Drivers, build logs, return codes and retained failed-attempt evidence are in
`/home/user/A9-build/mainline/integ21-host`.

Final build/assembly/finalization rc=0; both image files exist and are nonempty.
Gatekeeper's five groups, enforcing policy/load replay, native/Android listener
and RPMB tests, passive observer tests, AF/torch production helper replay,
acceptance/PIN/recording tests, ABL/init/ext4 and source/runtime/asset pins pass.
The final image preserves one stock QTI Gatekeeper provider and its dependency
closure. Unchanged INTEG17 mounted-data handoff evidence is explicitly reused.
Finalizers and `sha256sum -c SHA256SUMS` pass. No device qualification is claimed.

Both launchers retain FORMAT=0 by default and accept FORMAT=1. Inert launcher
checks verify the requested FORMAT=1, HOLD=2400 and exact PIN opt-in, as well as
defaults and invalid-format refusal. The sweep consumes adjacent frozen runtime,
policy/compiler, images and assets. The command below also sources the outer
environment helpers from the frozen worktree, using the existing private
registry without copying identifiers into it.

## Exact sweep command (for the phone coordinator; not executed here)

```sh
cd /home/user/A9-wt/integ21
A9_HOME="$PWD" A9_DEVICE_JSON=/home/user/A9/local/device.json \
A9_BTRY_LOGS=/home/user/A9-build/mainline/sweep-integ21-f1-20261024 \
A9_BTRY_FORMAT=1 A9_BTRY_HOLD=2400 \
A9_SWEEP_PIN_LIFECYCLE=fresh-disposable-formatted-b \
/home/user/A9-build/mainline/integ-20261024-21/run.sh \
> /home/user/A9-build/mainline/sweep-integ21-f1-20261024.out 2>&1
```

Fingerprint launcher remains `run-fingerprint.sh`. Host only: no adb, fastboot,
EDL, USB or phone access. Earlier integrated images and all sweep trees/logs
are unchanged. Only worker-owned kernel intermediates were compacted with
copy/archive, full verification and removal; receipts are retained alongside
those kernel caches. Current-task temporary derivatives were cleaned separately.
