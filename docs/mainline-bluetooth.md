# Mainline A9 Bluetooth

## Integrated firmware bridge and automatic refresh, 2026-10-20

The coordinator found the missing integration step on the phone: integrated
images use `firmware_class.path=/mnt/a9-wifi/firmware`, whereas the original
BT setup required `/data/local/tmp/a9fw`. Main `9402983` makes the Wi-Fi
bridge link `qca` to `/vendor/firmware/qca` and makes BT use that bridge.
The coordinator reports **BT ON and discovery alongside connected Wi-Fi and
streaming sensors** with this fix.

`build-vendor.sh --refresh-integrated` now discovers changed tracked files
under `scripts/mainline/{bluetooth,wifi}` since pre-POC source revision
`079d0884` (before the runtime factory-MAC change). It maps them onto existing
regular vendor files by basename, preferring matching vendor path suffixes
for duplicate names. The Wi-Fi stagers' `a9-*.rc` to `init.a9ml-*.rc` rename
is also recognized. Future script fixes need no per-file refresh list.
Unchanged R4 customizations, including session init and scan policy, remain
in place. The existing tool hashes/session manifest are regenerated after
refresh and explicit `--overlay` replacements still take precedence.

This picks up both `a9ml-bluetooth.sh` and `a9ml-wifi-firmware.sh`, alongside
the runtime-MAC and ADSP fixes. Build the new vendor with:

```sh
bash scripts/mainline/android/build-vendor.sh --refresh-integrated \
  --out /home/user/A9-build/mainline/bt-integrated-20261020/firmware-bridge
```

Use that directory's `vendor.img.sha256` and `build-result.json` for the latest
image identity. The R4 boot remains unchanged. The earlier image/hash below
predates this firmware-bridge fix and is retained as host-comparison history.

## Initial integrated R4 host comparison (before phone test)

Bluetooth ships enabled in R4, but its original firmware search-path assumption
prevented setup on the integrated image, as described above. The
October 17 Settings pairing/A2DP connection was on the `integ-1` combined image
(see `docs/state-log.md`, "BT phase 2"). Audio output was not verified.

Host inspection compared that image with R4 and the refreshed vendor:

- All 32 inspected BT payload files match byte for byte: HAL, identity tool,
  setup script, both init files, feature/VINTF XML, firmware manifest, private
  identity, module list and all 22 QCA firmware files. The HAL requires only
  platform LLNDK libraries. BT profile properties also match.
- R4 has `CONFIG_BT=m`, QCA/serdev HCI UART enabled, matching module vermagic
  and the required-firmware driver marker. Its BT node matches `integ-1`
  except for the node's numeric phandle. UART SE3 is enabled at 3.2 Mbaud.
- BT and Wi-Fi refer to the same L16/L17/L23 regulator nodes, voting
  1.8/1.304/3.304 V. BT I/O remains L9 at 1.8 V. No rail changes were made.
- `ro.vendor.a9.bluetooth=1` starts `vendor.a9-bt-setup` at `post-fs-data`.
  Setup loads `modules.bluetooth`, stages/verifies QCA firmware and verifies
  the controller identity. It then sets `vendor.a9.bt.ready=1`, starting
  `vendor.a9-bt-hal`. The services' `disabled` entries prevent class startup;
  these explicit init triggers start them. Bluetooth feature declarations
  are present and the default unavailable-feature file is absent.

The host-only image pair is under
`/home/user/A9-build/mainline/bt-integrated-20261020/`:

| File | SHA-256 |
|---|---|
| `boot.img` (unchanged R4) | `02571e365120f8b7fd290110304591fb225a82bffaba194dab6139db05e14dfd` |
| `vendor.img` (Wi-Fi MAC/ADSP transport fixes and GPU guard removal; BT retained) | `3c40594cc07df46677c59ae639c0c8ccb604994b1a4addba7a6c68f0c852efbf` |

The branch was rebased onto main `a6ca611` (fixes 1–3). The vendor was
built at `f3abb73` with the existing recipe; the final documentation commit
changes no build inputs:

```sh
bash scripts/mainline/android/build-vendor.sh --refresh-integrated \
  --out /home/user/A9-build/mainline/bt-integrated-20261020
```

`bt-comparison.json`, `build-result.json`, `fsck.log`, `host-inspect.py` and
`SHA256SUMS` accompany the images. Recipe readback, preserved file metadata
and `e2fsck -fn` passed. Keep the images local: they carry existing private
radio inputs. The earlier mutually exclusive `--wifi`/`A9_BLUETOOTH` flags
are not the path for this integrated image.

Coordinator phone test (not executed by this host-only worker): use the
existing POC loader with `POC_BOOT` and `POC_VENDOR` pointing to this pair,
and `POC_KEEP_ADSP=1` to retain sensors. After its usual E Ink/Wi-Fi setup,
run this through the POC's existing loopback root channel:

```sh
printf '%s\n' \
  'getprop ro.vendor.a9.bluetooth' \
  'getprop vendor.a9.bt.ready' \
  'getprop init.svc.vendor.a9-bt-setup' \
  'getprop init.svc.vendor.a9-bt-hal' \
  'cmd bluetooth_manager enable >/dev/null' \
  'cmd bluetooth_manager wait-for-state:STATE_ON >/dev/null && echo BT_ON' \
  'am start -a android.settings.BLUETOOTH_PAIRING_SETTINGS >/dev/null' \
  | adb shell 'timeout 100 nc 127.0.0.1 2324'
```

Expected initial properties: `1`, `1`, `stopped`, `running` (setup is
oneshot). The Settings pairing screen starts discovery. Put a nearby peer
in discovery mode and confirm it appears; leave Wi-Fi HTTP/redraw checks and
sensors running during discovery. Record only state/counts in the shared
report. This step needs no pairing or audio playback. `cmd bluetooth_manager
disable` ends the BT test; the existing POC owner retains restore ownership.

If setup never raised ready, capture the `a9-bt` setup reason and kernel/HAL
logs privately in that same boot and return sanitized errors. Do not rerun
the setup script blindly: it deliberately refuses a pre-existing staged
`a9fw/qca` directory. Boot-time setup completion, enable/scan and simultaneous
Wi-Fi/sensors operation on this pair remain unverified on hardware.

## Original implementation notes (2026-10-16)

2026-10-16. Default off. DT, configuration and host checks pass; no A9 Bluetooth
device operation has been performed. This is a controller/HAL bring-up path,
not a claim of tested pairing, audio or A6L feature parity.

Use `A9_BLUETOOTH=1` consistently for kernel, snapshot and vendor. Unset or `0`
keeps the existing build. Bluetooth outputs use `-bt` directories and filenames.
Code lives under `scripts/mainline/bluetooth/`. Round-two findings and corrections are
in `/home/user/A9/runs/sol-bt-round2.md` and private evidence staging under
`~/A9-build/mainline/sol-bt/`.

## Required private inputs

1. The copied golden `bluetooth_a.bin`. The stager pins its size/SHA and every
   extracted file, retaining all 22 stock AP/CR/CM files under lowercase `qca/`.
   Kernel QCA selects the revision after reading the controller, without aliases.
2. The original Bluetooth address, recovered read-only by the coordinator from
   the daily slot-A Android after run12, in exactly
   `/home/user/A9-build/mainline/sol-bt/private/bdaddr.txt`.
   It must contain one line of six colon-separated hex octets, with an optional
   final LF. Missing, malformed, zero, multicast and broadcast values fail.
   There is no environment/CLI override, random identity or WLAN-derived fallback.
   Keep this file and generated DT/image identities private and outside git.
3. The real standalone NDK HCI_USER HAL now builds from pinned Android16
   source under `scripts/mainline/bluetooth/android/native/`. It uses official
   frozen AIDL V1 generated code and actual platform Binder/LLNDK libraries.
   `native/build.sh` is offline and generates a complete `native/bundle/`.
   The checked local build is also staged outside git under
   `~/A9-build/mainline/sol-bt/r2/hal-bundle/`. The CF serial BT APEX remains
   unsuitable for direct QCA/serdev ownership. Runtime has not been tested.

A fixed-path offline attempt is available as `firmware.py import-persist
--image <golden-persist-copy> --output <new-private-LE-file>`. It verifies the
pinned image and reads only `/bluetooth/.bt_nv.bin`; the examined golden image
has no such record, so it fails without writing an address. It never chooses
one of the WLAN addresses. Historical log scans likewise found no authentic
local BT address.

Once the coordinator has populated that private file, stage into a new output:

```sh
python3 scripts/mainline/bluetooth/firmware.py stage \
  --image "$HOME/A9-build/mainline/sol-bt/bluetooth_a.bin" \
  --output "$HOME/A9-build/mainline/sol-bt/runtime"
```

Staging reads only the fixed `bdaddr.txt`; alternate address arguments are rejected.
It fails before creating output if the address is missing or malformed. All later
kernel, snapshot and vendor admission checks reread that same file and compare
it with the staged `address.dtsi`. Schema1 runtime directories from earlier
rounds must be regenerated; a stale or synthetic identity cannot pass real-image
admission. `A9_BT_FIRMWARE` selects the asset directory, never the identity source.
The output must be new/private and outside project worktrees and a9-assets.
Firmware/NVM bytes remain unchanged; DT contains the six little-endian address
bytes and vendor contains the matching private expected identity.

## Build integration

These are offline build commands for the coordinator, not deployment commands.
Use the already available kernel tree/toolchains; no source acquisition is
part of this implementation. Serialize kernel builds sharing the same source
tree: the existing build helper copies board DTS files into that tree.

```sh
bash scripts/mainline/bluetooth/android/native/build.sh
A9_ANDROID=1 A9_BLUETOOTH=1 scripts/mainline/kernel/build.sh all
scripts/mainline/android/build-frontlight-module.sh \
  "$HOME/A9-build/mainline/build-7.2.10-android-bt"
A9_BLUETOOTH=1 scripts/mainline/android/snapshot-kernel.sh
A9_BLUETOOTH=1 A9_BT_HAL_BUNDLE="$PWD/scripts/mainline/bluetooth/android/native/bundle" \
  scripts/mainline/android/build-vendor.sh --eink
```

`A9_BT_FIRMWARE` overrides `~/A9-build/mainline/sol-bt/runtime`. The kernel
build includes Bluetooth and the private address only for that flag. Kernel
and image creation, snapshot and vendor staging validate the config/DT/private
address together. Kernel outputs use `build-7.2.10-android-bt` and
`out-android-bt`; snapshot uses `android/kernel-snap-bt`; vendor output is
`vendor_b-eink-bt.img`. A custom `A9_EINK_ASSETS=1` DTBO location still needs the
existing explicit `A9_DTBO` override for snapshotting.

Do not export `A9_ANDROID=1` into vendor commands: kernel/build.sh uses it as a
boolean, while Android build helpers use `A9_ANDROID` as their workspace path.
Rebuild every external module against that same opted-in build before
snapshotting; the frontlight command above is the current external module.
The existing ramdisk builder must receive the Bluetooth snapshot explicitly
through `A9_ANDROID_KERNEL`; it has no new implicit Bluetooth default. Keep
boot, DTBO and vendor module snapshots matched using the existing offline
checks. This task creates no ready-to-flash image.

## Ownership and startup

Stock BT uses GENI SE3 `0x4a8c000`, GPIO8/9/10/11 = CTS/RTS/TX/RX. The opt-in
fragment reopens only those four pins. Stock GPIO87 is SW_CTRL, not proved
BT_EN, so it stays reserved. QCA drives the documented WCN3990 UART power
pulses and supply votes. No shell GPIO, UART attach program or second owner.

BT shares PM6125 L16/L17/L23 with Wi-Fi, using the Wi-Fi plan's labels and
values 1.8/1.304/3.304 V; L9 I/O is the existing 1.8-V rail. Wi-Fi must reuse
those same regulator nodes when merging. No CH1 rail is invented for a PMU
binding that would require it. BT does not enable MPSS, mount persist or
start modem storage services. Shared-rail/coexistence behavior still needs
coordinator validation.

The snapshot copies the entire actual `hci_uart` dependency closure into
`modules.bluetooth`, keeping it out of display `modules.load`. The BT setup
service loads that ordered list, then waits up to 30 boot-time seconds for
the SE3 `hci0`, a configured management index, the expected DT identity and
the matching HCI address. The management query is read-only and has a 100-ms
poll timeout. The polling deadline starts after module insertion; synchronous
kernel calls are not a cancellable userspace timeout. Setup refuses extra
controllers and prints no address. Linux initializes the transport/firmware
asynchronously when registering HCI, without requiring a preliminary BlueZ
power-on operation.

Firmware search path (fix after BT run 1, 2026-10-16). The kernel searches
only `firmware_class.path`, and the E Ink cmdline sets it to the RAM-`/data`
directory `/data/local/tmp/a9fw` (waveform/template in its `hisense/`). Run 1
measured `qca/crbtfw21.tlv` failing with -2 although `/vendor/firmware/qca`
held it. Before inserting any module, setup now requires that exact search
path, checks `/vendor/firmware/qca` against `/vendor/etc/bluetooth/firmware.sha256`
(the pinned `FILE_HASHES`, exactly the 22 files), copies the files into a new
`a9fw/qca` (refusing a pre-existing one), checks the copy the same way, and
checks it again after the controller came up, before opening the HAL gate.
`a9fw` is made `root:root 0755` (created when absent, re-owned when it
exists; a symlink is refused): since integ-1 (review 44) the E Ink blobs are
installed as root by `android-boot.sh` through `eink-fw-install.sh` (adb push
into a private staging dir, root copy, sha256 readback), never pushed into
`a9fw` as shell, and the GPU vendor's `a9ml-gpu.sh setup` uses the same
root-owned directory. The cmdline and kernel are unchanged.

Only then does `vendor.a9.bt.ready=1` start the AOSP Linux HAL. It runs as
Bluetooth UID with NET_ADMIN, disabled/oneshot, without a restart
loop. Android owns HCI packets through USER channel; Linux owns UART, IBS,
firmware and power. A mismatch or failed setup leaves HAL stopped. DT address
programming is conditional in btqca, so runtime identity verification is
required even though the private DT was validated offline.

Round 2 adds an A9-only QCA policy patch: missing patch/NVM errors must
propagate instead of silently selecting ROM firmware. The BT build applies
that feature-local patch, and kernel/image/snapshot/vendor checks require
its `a9_qca_firmware_required=1` module marker. Rebuild any round-one snapshot;
old modules are deliberately rejected. Other boards retain generic QCA ROM
fallback and normal default-off A9 builds do not enable BT.

The native HAL rechecks its private expected address against the actual UART
controller both before and after USER bind, because opening USER may re-run
QCA setup. Its image address file is root:bluetooth0640, readable by HAL UID1002;
private source inputs remain0600. WCN3990 uses `qca_set_bdaddr` (FC14 with a
callback byte swap), while only QCA_ROME uses `qca_set_bdaddr_rome`. DT input
remains six little-endian bytes. The management discovery timeout does not
bound synchronous kernel bind/close or remote Binder callbacks; the native
README documents those lifecycle limits.

The supplied enforcing-policy sources require a real policy rebuild; they are
not installed as inert `.te` files. The current bring-up vendor remains under
its existing permissive policy. Profile properties follow A6L software paths
and disable LE Audio/DSP offload. They do not create missing audio ports,
providers, a DSP link or a cellular-call bridge. Audio remains a separate
effective-policy/provider and runtime validation gate.

## Host checks

```sh
PYTHONDONTWRITEBYTECODE=1 python3 tests/mainline-bt/test-offline.py
PYTHONDONTWRITEBYTECODE=1 python3 scripts/mainline/bluetooth/android/test-stage-hal.py
```

The C identity gate also has `--self-test`, which exercises only synthetic
in-memory data and performs no socket/device access. Build it with the host
compiler for that check; `build-tools.sh` builds the Android arm64 executable
with the existing NDK and never executes it. Firmware/parser/DT and synthetic
HAL staging tests do not establish actual firmware acceptance or HAL behavior.
Full kernel/ABL checks and coordinator HCI/identity/scan/pair/audio/suspend
and Wi-Fi-coexistence tests remain outstanding. Never deploy the synthetic
test identity/HAL or isolated compile-check modules.
