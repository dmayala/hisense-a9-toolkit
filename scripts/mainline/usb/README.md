# A9 mainline USB gadget POC (B7)

`build-integrated.sh` now builds and stages the USB gadget AIDL V1 HAL by
default; `--without usb` preserves the R4 USB setup. R4 already builds
FunctionFS, RNDIS and NCM into the kernel. A small external compatibility module
uses the existing R4 module ABI; the boot kernel does not need rebuilding.

The gadget core and adapter derive from PaddleStroke's
`hisense-a6l-lineageos/device/hisense/a6l/usb/gadget/` (Apache-2.0), reference
commit `ede802bcb12d3c8854b8ed0e147d6477d0ea0f9d`.
The A9 discovers its UDC at runtime, clears UDC with an empty name as upstream
configfs requires (the reference's `none` string fails on this kernel), uses the frozen Android 16 V1 AIDL
definitions included in `aidl/`, and builds against the existing Binder/NDK
cache. The official AIDL archive URL is
`https://android.googlesource.com/platform/hardware/interfaces/+archive/refs/heads/android16-release/usb/gadget/aidl.tar.gz`
(download SHA256 `c4852c19d22812c442f420b363e1503a5a7a77cb883863de7c2bb402b46c921f`).

Boot keeps R4's `sys.usb.configfs=1`, platform-init ADB binding and ACM/NCM
fallback, independently of the gadget HAL. Init also creates FunctionFS MTP/PTP
mounts. Once the existing helper sees ADB descriptors and the UDC's host state
`configured`, it changes `sys.usb.configfs` to `2`, which starts the HAL.
The HAL adopts the existing ADB+ACM/NCM binding before registering its service,
without writing UDC or deleting links. It then handles framework requests,
endpoint readiness and daemon restart rebinding. Every
request includes ADB, including NONE/charging-only. Unsupported requests,
missing kernel functions, apply failure and a readiness/bind timeout restore
ADB-only. The descriptor wait is capped at two seconds, followed by an ADB
restore wait of at most two seconds; its monitor keeps retrying ADB if the
daemon's endpoints are still absent. Composition changes briefly disconnect
and enumerate again; this does not provide uninterrupted ADB transport.

The development USB helper keeps its original UDC discovery and early fallback.
ACM/NCM debug links f8/f9 survive boot and adoption; a subsequent MTP/PTP/network
composition switch removes them normally. No custom ownership property gates
early ADB. The failed first candidate switched to configfs=2 at boot, removed
the f8/f9 backup links and disabled the old fallback before ADB was working;
its replacement early bind also depended on the undeclared
`sys.usb.a9ml.hal` property. Build c did not enumerate; the exact runtime
failure stage was not captured. This corrected flow requires coordinator testing.
Android 16 `UsbService` constructs its device manager only when
`/sys/class/android_usb` exists. Without it, a valid MTP request throws
`IllegalStateException` before calling the gadget HAL; Settings reaches the
same missing-manager check. `a9-usb-compat.ko`, loaded at `post-fs-data` before
system_server, exposes this legacy class and `android0/state`. The existing
helper forwards actual UDC state samples to it; the module emits the standard
`USB_STATE` uevent. It performs no binding, power or other hardware control.
Suspended or unreadable UDC state leaves the last state unchanged.
If polling observes a direct DISCONNECTED-to-CONFIGURED jump, the module emits
CONNECTED before CONFIGURED. Android's CONNECTED update cancels a queued
disconnect; CONFIGURED alone leaves that delayed update pending. Omitting the
intermediate event can make the framework report disconnected after the kernel
is configured, stop a working MTP selection and fall back to ADB.
The two-second helper can also miss an entire disconnect/reconnect, seeing
CONFIGURED both before and after a function switch. Android arms a fresh
five-second enumeration timeout for that switch and needs a fresh event to
cancel it. Both helper and module therefore forward confirmed CONFIGURED
samples even when unchanged. Other duplicate states remain suppressed;
repeating DISCONNECTED would keep postponing Android's debounced disconnect.
This refresh reports the real UDC state and does not treat a HAL bind as host
enumeration. Unreadable, suspended and unconfigured states cannot generate a
CONFIGURED event.

This replaces the A9Usb resource overlay and its idmap dependency. The old APK
is removed when refreshing a base vendor, including older integrated images.
The module and helper use the legacy interface already understood by the GSI,
so UDC state tracking does not depend on a resource override. A fresh boot is
required: loading the module after UsbService construction cannot populate an
already-null device manager. No Settings or `cmd` route bypasses that check.

The integrated step overlays R4's actual USB init/helper files, retaining its
other boot/session customizations. R4's original USB port example HAL remains;
there is no new Type-C/role-swap implementation. RNDIS/NCM gadget composition
is provided, but end-to-end tethering still needs the Android tethering service
and a coordinator test. MIDI/accessory/audio-source/UVC are unsupported.
This is a permissive development POC; enforcing policy is separate B4 work.

Build only USB against the integrated R4 baseline:

```sh
bash scripts/mainline/build-integrated.sh --out "$HOME/A9-build/mainline/usb-20261020/images" \
  --without sensors --without vibration --without suspend --without battery \
  --without cameras --without video --without gnss --without audio
```

This preserves baseline R4 sensors/BT/Wi-Fi/GPU; the options omit the new feature
rebuild steps. Output images contain the baseline private modem seeds and must
stay local. They do not contain the private runtime-MAC POC boot image; use the
coordinator's existing runtime identity setup for Wi-Fi.

Host check (five cases, no phone):

```sh
nix-shell -p gcc --run 'c++ -std=c++17 -Wall -Wextra -Werror -pthread scripts/mainline/usb/gadget_core.cpp scripts/mainline/usb/test_core.cpp -o /tmp/a9-usb-test'
/tmp/a9-usb-test "$(mktemp -d /tmp/a9-usb-test.XXXXXX)"
```

Coordinator phone acceptance, using the normal slot-B image path:

The framework request accepts a data function, not the final gadget composition.
Use `mtp`, `ptp`, `rndis`, `ncm`, or `none`; do not append `,adb` or request
`adb` directly through `svc usb setFunctions`. Android rejects these ADB masks
with `IllegalArgumentException` before the HAL is called. Keep USB debugging
enabled: the framework adds ADB separately, and this development HAL also
retains ADB in every applied composition. See Android 16
[argument validation](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android16-release/core/java/android/hardware/usb/UsbManager.java)
and [ADB application](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android16-release/services/usb/java/com/android/server/usb/UsbDeviceManager.java).

1. Boot with the new vendor and R4-compatible boot. Confirm ADB connects before
   and after framework boot. At first enumeration, R4's ACM/NCM interfaces
   should still be present. The helper then changes configfs from 1 to 2 and
   starts the HAL without another disconnect. Keep USB debugging enabled.
   Confirm `/sys/class/android_usb/android0/state` exists and follows host
   configuration, and `adb shell dumpsys usb` includes its device manager.
2. Unlock the screen. Select Settings > USB preferences > File transfer, or run
   `adb shell svc usb setFunctions mtp`. A brief disconnect is expected;
   `adb wait-for-device` must return. `adb shell 'getprop sys.usb.configfs'`
   should return `2`. Check the gadget service with
   `adb shell pidof android.hardware.usb.gadget-service.a9`.
   `adb shell svc usb getFunctions` reports the framework request; the HAL's
   `adb shell dumpsys android.hardware.usb.gadget.IUsbGadget/default` should
   show `functions=0x5 applied=1 bound=1` for MTP+ADB. Check the host interfaces
   below as well; `sys.usb.state` alone is not proof of the applied AIDL mode.
3. Host interface classes only (avoid printing USB serial descriptors):
   `lsusb -v -d 18d1:4ee2 2>/dev/null | sed -n '/bInterfaceClass/p; /bInterfaceSubClass/p; /bInterfaceProtocol/p'`.
   Expect MTP class 6/subclass 1/protocol 1 and ADB class 255/subclass 66/protocol 1.
   Open the phone in the host's MTP file manager, confirm Internal shared storage,
   and copy a small test file both directions. Confirm ADB still works.
4. Repeat for `ptp` and Settings > No data transfer (ADB stays available).
   Test `rndis` through USB tethering Settings and `ncm` if wanted.
   Restore `adb shell svc usb setFunctions none` between cases. Keep USB
   debugging enabled throughout, so every applied composition retains ADB.
5. For an observed failure, retain the coordinator's private logcat tagged
   `android.hardware.usb.gadget-service.a9`, `UsbDeviceManager`, `MtpServer`,
   and the UDC state/configfs link readbacks. Report sanitized errors only.

Host checks/builds do not establish device acceptance or guarantee recovery
from kernel/UDC faults. No worker phone operations have been performed.
