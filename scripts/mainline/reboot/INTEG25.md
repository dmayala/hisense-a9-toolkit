# INTEG25 host delivery — 2026-10-24

Source main commit: `c30728177aa8f098f34f8669e32e1bd517bed791`.
Frozen detached worktree: `/home/user/A9-wt/integ25` (tracked clean).
Main rechecked immediately before corrected assembly at
`2026-10-24T21:54:23.854838+00:00`. The kernel was freshly rebuilt from
the same commit; its configuration and export CRCs match the integrated base.

Includes requested d40c4c2 RPMB Gatekeeper partition writes,9d558d8 sensor
coexistence with read-only physical persist,39a52f2/40c30bc FRP+cgroup,
232d727/fa24e20/5ca3a30 HVENC/camera, and earlier PIN/Gatekeeper assets.
Also includes a2546a9 radio availability and c307281 FRP packaging correction.
The inherited FRP context has two spaces; c307281 accepts that equivalent
spacing, preserves its existing label, and still refuses a conflicting label.
The initial failed assembly is retained under `/home/user/A9-build/mainline/integ25-host/assembly-attempt1` with
its incomplete vendor image copy+SHA-verified in the archive, not a sweep image.

## Radio ANR repair included

INTEG24 boot125 logcat contains204 phone ANR reports (90 previous,114 current).
All168 retained phone-startup trace records show the same main-thread chain:
HwBinder.getService -> IRadio1.4.getService -> RIL.getRadioProxy -> RIL.<init>
-> PhoneFactory -> PhoneApp.onCreate -> ActivityThread.main. Repeated trace
records are not additional distinct ANRs.65 is the stability-window count.
Logcat shows slot1 unregistered; kernel records legacy qcom_common/IPA import
failures. The stock-file preparer still required IPA/rmnet, so unavailable
packet data could withhold both framework radio HALs despite58a6303's WLAN fix.

a2546a9 removes that packet-data veto from ordinary radio-file preparation.
Verified profiles/database, opt-in/B-slot, source/destination hashes, path and
label checks remain; netmgr remains blocked for its existing kernel ABI gap.
No fake HAL, disabled Telephony, framework patch, IMS release change, RF/modem
start or protected-store access is added. The missing-module/no-SIM host case
reproduces old `phase=modules` refusal and passes after the fix. Six Android-shell
plus five integration and five init-queue groups pass. Future HAL registration
timing and zero phone ANRs remain device observations for the coordinator.
Analysis: `scripts/mainline/cellular/stock/STARTUP.md` and
`A9-build/mainline/integ24-phone-anr-host/host-checks.json`.

## Build and verification

Same recipe as INTEG24, immutable INTEG17 binary baseline. Fresh native kernel,
logger/policy, encoder trace stage, qualification executable/reference clips,
acceptance/PIN/camera assets, credential package and target-export AF/torch
modules. Current sensors and radio preparation scripts are verified byte-for-byte
in the final vendor image. All bundled source/runtime/asset hashes pass.

Both vendor HAL NEEDED gates pass; final103 modules have zero CRC/vermagic
mismatches. ABL, merged init, ext4, crash tools, logger follow, row isolation,
PIN cleanup, persist RO, UFS discovery, new learned-GK-partition policy and
native/ARM64 diagnostic bridge tests pass. Full FRP/cgroup host checks pass.
The unchanged crash-controller fixture evidence is reused only after verifying
its exact test/header source bytes. Other recorded build phases ran freshly.
Gatekeeper's final-vendor Android RTLD_NOW plugin/factory lookup passes under
qemu-aarch64; the real service reaches its bounded host wait with Android
facilities absent. This proves linking, not TA/HIDL/PIN/device qualification.

RPMB guard mode is2: authenticated writes are scoped to the learned stock
Gatekeeper partition under d40c4c2. The image is not described as globally
read-only RPMB. Physical persist remains read-only. FRP retains stock
system_server read/write access and scheduler cgroups retain the merged fix.

Artifact directory: `/home/user/A9-build/mainline/integ-20261024-25`.

| Image | Bytes | SHA256 |
|---|---:|---|
| boot.img | 14004224 | `8a9d4c0110c1bab446bc337272ade02f4aaafcadc2ad391b12b570ac985eb4fd` |
| vendor.img | 847532032 | `b2f2753dccd63882510763783c625d09272fcb19d87b9116a87d3d2ec260da7b` |

Final assembly, final checks and finalization rc=0. Both image files exist and
are nonempty regular files. All8 SHA256SUMS entries pass. Metadata:
integrated-build.json, host-checks.json, source-handoff.json. Host evidence:
`/home/user/A9-build/mainline/integ25-host`. Old image directories and sweep artifacts were untouched. Three old
worker-owned host-test image copies were copied+SHA-verified into the archive
before local removal; receipts are in host-fixture-archives.json.

Default FORMAT=0 and override=1 remain. Requested FORMAT1/HOLD2400/PIN/HVENC1
flags pass the actual copied launcher -> inert b-try -> actual Python hook ->
inert FBE/acceptance chain; production b-try fake-transport hook also asserts
HVENC1 and the original owner deadline. Proof: hook-env.json and
production-hook-env.log/rc. PIN stays last, isolated, with clear/readback.

## Exact sweep command (phone coordinator only; not executed here)

```sh
cd /home/user/A9-wt/integ25
A9_HOME="$PWD" A9_DEVICE_JSON=/home/user/A9/local/device.json \
A9_BTRY_LOGS=/home/user/A9-build/mainline/sweep-integ25-f1-20261024 \
A9_BTRY_FORMAT=1 A9_BTRY_HOLD=2400 \
A9_SWEEP_PIN_LIFECYCLE=fresh-disposable-formatted-b \
A9_SWEEP_HVENC_QUALIFY=1 \
/home/user/A9-build/mainline/integ-20261024-25/run.sh \
> /home/user/A9-build/mainline/sweep-integ25-f1-20261024.out 2>&1
```

Host only throughout; no adb/fastboot/EDL/USB/phone access. The coordinator
retains the phone. No hardware success is inferred from host tests.
