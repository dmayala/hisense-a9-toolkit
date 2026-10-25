# INTEG24 host delivery — 2026-10-24

Source main commit: `1b87b7cc044d7d3663c66ce4314de42aa2c6dd25`.
Frozen detached worktree: `/home/user/A9-wt/integ24` (tracked clean).
Main checked before kernel build at `2026-10-24T20:23:45.317991+00:00` and
immediately before assembly at `2026-10-24T20:28:20.719184+00:00`;
both selected this source. Assembly started before `39a52f2` landed at20:29:57Z.
Per the user's subsequent instruction, this frozen build was completed.
`39a52f2` FRP/cgroup changes and later `3913437` / `9d6f049` RPMB diagnostics
are outside this image, as are any subsequent FRP write-access changes.

Includes `d1988d0` independent rows/final PIN, `1b87b7c` recovery-stable log pull,
`f77f751` bounded Surface HVENC/decode qualification, and all earlier changes
through the selected commit (including the working Gatekeeper closureee79d2f,
crash capture7cede33, UFS discoverye63767c, runtime preflightf9a91f8, input sizing
94f70e7, read-only persist and PIN assets). This is the requested run candidate
before resolving the remaining PIN/RPMB runtime failure; PIN cannot abort the
other feature rows because it now executes last with its own clear/readback.

## HVENC payload and environment proof

Freshly builds the native qualification executable and two synthetic AVC/HEVC
MP4 inputs. Both reference clips pass host frame/container/codec validation.
The manifest pins the executable, media, current C2/HFI/firmware sources, actual
encoder library and Venus modules. All source pins are copied into frozen
runtime and verified there; no mutable source checkout is a runtime dependency.
Hardware encode/decode behavior remains a coordinator-owned phone observation.

A9_SWEEP_HVENC_QUALIFY defaults0 and honors1 in the generated launchers.
The requested FORMAT1/HOLD2400/PIN opt-in/HVENC1 combination is confirmed by:

- Copied actual run.sh -> deliberately inert b-try -> copied actual Python
  onb-integ24.py -> inert FBE and acceptance scripts, each asserting the flags.
- The production b-try under fake transport, whose hook asserts HVENC1 and the
  inherited original owner deadline. Saved final-boot/native-completion and
  failure cases pass. No real device tool is reached.
- Current qualification host tests and PIN-last/order/isolation regressions.

Proof: `/home/user/A9-build/mainline/integ24-host/hook-env.json`, production-hook-env.log/rc and final-checks.log.
The main optional codec stage runs after normal rows and before the final PIN
window. Owner time limits and PIN clear/readback remain bounded.

## Build checks

Same integ23 recipe, immutable INTEG17 binary baseline; fresh native kernel,
logger/policy, acceptance assets, encoder stage, FBE/listener replay and current
credentials package. Both HAL gates pass505 deployed contexts /646 ELF objects
with0 missing dependencies. Fresh AF/torch modules use target config/exports;
final103 modules /6,313 imports have0 CRC/vermagic mismatches. ABL/init/ext4,
crash runtime tools, source/runtime/asset pins, logger follow, row isolation,
PIN cleanup/refusal, recovery pull, data handoff, UFS setup and persist checks pass.
All source tests passed; the 64-file crash fixture ran serially without changing
its timeout. Current bridge schema18/64 synthetic datagrams and passive observer
native/ARM64 tests pass.

The final vendor also passes five Gatekeeper packaging groups and actual Android
RTLD_NOW plugin/factory lookup under qemu-aarch64 with public libraries only.
The real service starts then reaches its five-second host bound while Android
facilities are absent; this is not TA/HIDL/PIN qualification.

Artifact directory: `/home/user/A9-build/mainline/integ-20261024-24`.

| Image | Bytes | SHA256 |
|---|---:|---|
| `boot.img` | 14000128 | `4a9148a47706850f2199edf7abb2a32837337b1d3eb540d894fbfb1e501e3a57` |
| `vendor.img` | 847532032 | `83d3d4585478121aaed1c6ea351da8ad26683092ebdf85fcfc078f6f1fc1f48c` |

Final assembly, final checks and finalization rc=0. Boot/vendor files exist and
are nonempty. All8 adjacent SHA256SUMS entries pass. Default FORMAT0 and override1
are verified. Metadata: integrated-build.json, host-checks.json, source-handoff.json.
Drivers/logs/return codes: `/home/user/A9-build/mainline/integ24-host`.
Only verified worker-owned kernel intermediates were compacted/relocated to
recover disk budget. Older images and sweep trees were unchanged.

## Exact sweep command (phone coordinator only; not executed here)

```sh
cd /home/user/A9-wt/integ24
A9_HOME="$PWD" A9_DEVICE_JSON=/home/user/A9/local/device.json \
A9_BTRY_LOGS=/home/user/A9-build/mainline/sweep-integ24-f1-20261024 \
A9_BTRY_FORMAT=1 A9_BTRY_HOLD=2400 \
A9_SWEEP_PIN_LIFECYCLE=fresh-disposable-formatted-b \
A9_SWEEP_HVENC_QUALIFY=1 \
/home/user/A9-build/mainline/integ-20261024-24/run.sh \
> /home/user/A9-build/mainline/sweep-integ24-f1-20261024.out 2>&1
```

Fingerprint launcher remains run-fingerprint.sh. Host only: no adb, fastboot,
EDL, USB or phone access. No phone attempt was consumed by this worker.
