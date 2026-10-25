# Real standalone Linux HCI_USER Bluetooth AIDL HAL

This is a real arm64 Android executable, built from reviewed transport source
and the official frozen Bluetooth AIDL V1 generated NDK implementation. No
Binder, HCI, generated-method or dependency stub was substituted. It has **not
been run on a phone or under Android**. Static compile/link/symbol checks do
not establish pairing, BLE, audio or suspend operation.

`a9_bluetooth_hal.cpp` is a Linux-only adaptation of the AOSP default approach,
with a new direct NDK Binder/session wrapper. It is not the unmodified AOSP
default HAL. Android16's default target now depends on Rust
`libbluetooth_offload_hal`, `hal/ffi.h`, async/H4 transport libraries and HIDL
headers. Those dependencies are unnecessary for this port's disabled LE Audio
offload and packet-oriented kernel USER channel. The custom source uses the
same frozen HCI interface with its actual generated transaction dispatch and
callback proxy. C++ is statically linked; cross-partition dynamic calls use C
Binder/LLNDK APIs only. There is no Cuttlefish serial service, emulator tty,
Qualcomm userspace UART firmware loader or BlueZ owner.

Source provenance:

* `platform/hardware/interfaces` Android16r1 resolves to immutable commit
  `5688f7eb1e117ed26e642a695de300b7683acb87`. Default-HAL code and frozen AIDL
  files were fetched only from commit URLs on android.googlesource.com.
* `platform/frameworks/native` Android16r1 resolves to immutable commit
  `2827a4a16b0340ecd07c2d5a6c89991799b362bb`. Platform-only Binder
  manager/process/stability headers were fetched from that commit. NDK27
  provides the remaining genuine public Binder headers.
* `source-manifest.json` records URLs, revisions, sizes and per-file SHA-256.
  `fetch.py` is the reproducible explicitly-networked source acquisition step;
  `build.sh` itself performs no network access. Preserve AOSP license notices.

Build from any directory: `bash scripts/mainline/bluetooth/android/native/build.sh`. It verifies every pinned upstream file's SHA-256 and
the official frozen API hash before generating code. Requires the local NDK27, SDK35 aidl generator and the
cached CF Android16 real `libbinder_ndk.so` as a link input. Override paths
with `ANDROID_NDK_HOME`, `AIDL`, `A9_BINDER_NDK` as needed. SDK36 aidl emits
helpers requiring newer C++ Binder headers than NDK27; SDK35 matches NDK27
and emits the same official V1 wire API. Frozen source is unmodified and the
generation uses `--structured --stability=vintf --version=1`, official hash
`f0eb2eb6d3b970cef8fb1b6e22c4ec39a4da5119`, and min SDK33. The vendor build
defines `__ANDROID_VENDOR__` and `BINDER_STABILITY_SUPPORT`; generated Binder
creation marks VINTF stability. Debug path prefix mapping avoids embedding
the temporary build directory. The build uses PIE, RELRO, immediate binding,
stack protection and FORTIFY. Generated code stays a build intermediate. The build generates `bundle/manifest.json`
from the actual executable hash and records implementation, source-lock, tool
and link-input hashes. gen/, build/ and bundle/ are ignored, and no generated
executable is stored in source control.

`bundle/bin/hw/android.hardware.bluetooth-service.default` depends only on
`libbinder_ndk.so`, `liblog.so`, `libm.so`, `libdl.so`, `libc.so`. It needs no
new vendor library replacement or copied AIDL interface .so. All 42 required
Binder symbols **and symbol versions** were compared against the actual
GSI's `/system/lib64/libbinder_ndk.so`, extracted read-only into target-libs:
none are missing. `verify-abi.py --hal <binary> --binder <matching-GSI-library>`
repeats this host-only check without loading either ELF. Binder version requirements stop at LIBBINDER_NDK33. This
is static ABI evidence; target linker namespace and SELinux operation still
need coordinator validation.

Transport and lifecycle:

* A read-only CONTROL-channel READ_INDEX_LIST uses a five-second monotonic
  deadline and validates response header/opcode/index/status/length/count.
  Require exactly configured hci0; a higher index or extra configured
  controller fails. There is no infinite event wait and no UART fallback.
* Before every USER bind, read the private expected LE address file,
  `/vendor/etc/bluetooth/local-bd-address`, validate it without logging it,
  check `/sys/class/bluetooth/hci0` resolves under `/4a8c000.serial/`, and
  compare HCIGETDEVINFO's actual UART controller identity. Reject UP, INIT
  and RAW. Root's bring-up gate independently compares expected versus DT
  property bytes. The HAL does not read a DT property itself.
* After USER bind, the driver may have re-run QCA power/firmware setup. A
  separate ephemeral raw read-only ioctl socket checks the identity again,
  allowing UP but rejecting INIT/RAW/address/UART mismatches. On any error,
  close USER and return UNABLE_TO_OPEN_INTERFACE. No HCI commands are used
  for the identity check; no address is programmed by this process.
* Linux HCI_USER is SOCK_RAW with complete H4-prefixed **datagrams**, as shown
  by kernel `hci_sock.c:261`. One sendmsg contains type plus the complete
  body. Receive validates datagram size and HCI body length before callback.
  ACL, SCO, event and ISO callbacks are routed through actual NDK-generated
  proxies. Command/ACL/SCO/ISO sends check declared lengths and return errors
  on bad state/input rather than abort. Nonblocking send retries have a 250ms
  monotonic deadline; the syscall never loops over partial stream writes.
* A per-session death recipient closes the USER fd on client death. Its
  cookie is a weak reference, freed exactly through API33 onUnlinked; an old
  client's late death cannot close a later session. Close stops the session
  using an eventfd and closes USER without joining a potentially blocked
  remote Binder callback. A detached RX worker owns its session lifetime;
  a weak retiring-session guard prevents creating repeated blocked workers.
  A second close preserves that guard. Generation numbers cancel an
  initialization that overlapped close. No callback is made holding the
  global state or I/O lock. RX starts before the SUCCESS callback, matching
  AOSP's ordering so a reentrant client can send and receive HCI commands
  inside initializationComplete. Thread creation failure closes the session
  and reports initialization failure. Three Binder pool workers plus the join thread
  permit close/death while another Binder operation is outstanding.
* There is no RFkill write or open, no GPIO/UART/block-device access, no
  controller-public-address write, and no autonomous retry loop. The kernel
  setup must have reached a usable unblocked controller before the root
  readiness gate starts this service. Closing USER releases kernel HCI
  ownership; root init controls process startup with disabled/oneshot policy.

Bounds: five seconds covers userspace mgmt discovery only. USER bind/close
can run kernel QCA setup/teardown and are governed by the kernel's command
timeouts, firmware transfer and retry limits. They do not inherit that
five-second overall bound. A remote synchronous Binder callback cannot be
forcibly cancelled by this C++ transport; the retiring guard bounds worker
creation and close avoids joining it. Successful initialization callback and
later Binder traffic are still runtime test gates.

Required image integration: Bluetooth UID/GID 1002 needs read access to the
private image address file: root:bluetooth 0640. Its source input stays 0600 and
private staging stays 0700. HAL also needs readlink/sysfs HCI access, basic
Bluetooth socket permission, CAP_NET_ADMIN, Binder HAL service registration
and callback permission. No NET_RAW/SYS_ADMIN/root or RFkill write grant is
required by this implementation. Preserve root's serial-device/DT/private
identity gate, firmware-completeness gate and strict QCA error patch. Do not
install the CF BT APEX or duplicate IBluetoothHci/default init/VINTF owners.

Host-only checks: `test-packets.cpp` executes only this project's pure packet
length validator on the build host, with 169,856 swept boundary cases plus
explicit malformed/maximum-size/ISO cases. It never opens Bluetooth sockets
or executes vendor code. Android executable was only cross-compiled and
read with static ELF tools. Actual registration, toggling, death recovery,
packet traffic, pairing, LE, A2DP/SCO, suspend and SELinux remain untested.

The independently reviewed RX ordering starts its worker before the synchronous
SUCCESS callback. Close can return while an already admitted remote callback
is outstanding; a stalled callback can hold the retiring-session guard and
prevent reinitialization until it returns or the client dies. Unexpected RX
failure stops the session and later sends fail; it does not invent a second
initialization callback after SUCCESS. Android watchdog/recovery behavior
still needs runtime validation. No hard overall init/close deadline is claimed.
