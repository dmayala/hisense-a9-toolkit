# Android Bluetooth HAL for the A9 mainline vendor

Use the real standalone Linux HCI_USER implementation in [native/](native/README.md).
Round 2 fetched immutable Android16 AOSP source, generated the official frozen
AIDL V1 NDK implementation and built a real arm64 service. It needs only the
existing platform Binder/LLNDK libraries; no synthetic dependency or virtual
serial transport is used. Hardware runtime remains untested.

Build offline with `bash scripts/mainline/bluetooth/android/native/build.sh`.
The source lock verifies every upstream file before compilation. The resulting
ignored `native/bundle/` includes a freshly generated manifest and actual binary
SHA256. Explicit source reacquisition is `native/fetch.py`, limited to the locked
commit URLs/hashes; build never fetches. `verify-abi.py` statically compares
versioned Binder imports against a separately extracted matching GSI library.

Pass the built bundle with `A9_BT_HAL_BUNDLE=/absolute/path/to/native/bundle`
to `A9_BLUETOOTH=1 build-vendor.sh --eink`. The pinned original-address firmware
input and strict-QCA kernel snapshot are still required. No authentic local BT
address was recovered from golden persist; `wlan_mac.bin` is WLAN-only.

The service UID/GID is Bluetooth1002 with CAP_NET_ADMIN only. The private expected
image file is root:bluetooth0640; its source remains private0600. Root setup
compares it to DT. The HAL repeats UART/actual-address checks before and after
USER binding. Source policy files still require a real enforcing-policy rebuild;
they are not inert runtime `.te` payloads. Only IBluetoothHci/default is declared.

The cached Cuttlefish H4 serial HAL does not implement QCA power/patch/NVM/IBS.
Its APEX also brings unrelated finder/ranging/socket/LMP services; do not install
it for this route. The A6L used Linux USER channel too. The retained A6L startup
patch is historical source reference, not a required patch for the new native
wrapper. Native README documents its exact code derivation and lifetime limits.

`stage-hal.py` validates real ELF64/AArch64 SHA256 and transitive dependencies,
rejects the cached serial HAL and refuses replacing different vendor libraries.
Its source-build manifest is provenance, not a binary equivalence proof or a
runtime test. `test-stage-hal.py` uses synthetic ELF only to test that parser.

Profile properties preserve A6L's software paths and disable LE Audio/DSP offload.
They do not create missing audio ports/providers or a cellular-call bridge.

## Integrated e startup ordering fix (2026-10-20)

The framework waited for `android.hardware.bluetooth.IBluetoothHci/default`
and eventually aborted because the ready-gated HAL never started. The e boot
log shows Bluetooth setup starting at32.79s and failing at33.35s, before the
first firmware bridge preparation at34.38s. The new synchronous camera module
load occupied init between Bluetooth's background start and the sensors/GPU
bridge actions. Build d happened to prepare the bridge immediately after the
Bluetooth launch and completed setup. Both images have identical BT services,
HAL, firmware manifest and firmware bytes.

The Bluetooth init file now runs an earlier, integrated-only post-fs-data
action: create the existing bridge directories and `exec_start` its existing
firmware-only service. The ordinary Bluetooth action then starts setup after
that service returns. Standalone BT builds retain their original staging path.
The HAL's identity/firmware/controller checks and ready gate are unchanged;
this does not lazily start the HAL before the controller is ready. The existing
vendor refresh maps the changed init file into both full and incremental builds.
