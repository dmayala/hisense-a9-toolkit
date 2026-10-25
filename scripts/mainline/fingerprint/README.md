# Row 30: stock Sunwave fingerprint on mainline

For the later user enrollment session, see the [HAT, HMAC agreement and enrollment
preflight](../../../docs/mainline-fp-enroll.md). The row30 hook remains a sensor
initialization check and does not establish enrollment or authentication.

Host-prepared `integ-20261023-fp7` extends the combined `integ-20261023-7` (7.2.10+,
LOS23 GSI) with the stock Sunwave control ABI, DT and vendor HAL/TA integration.
FP7 supplies the saved-device TA signing variant matching the saved-device TZ.
It retains FP6's kernel transport and all integrated fixes.
Registration and chip-ID observation require a later coordinator/user B run.
Enrollment and authentication are not called by the hook.

```
/home/user/A9/scripts/mainline/fingerprint/run.sh
```

The existing `boot-deadline/b-try.sh` owns the boot and return to A. The hook has
240 seconds, waits for actual QSEECom listener readiness, requests the HAL through
init, requires the HIDL 2.1 listing and the TA-backed nonzero chip ID plus sensor
ready message from the same HAL PID, then observes its PID/starttime for 30s and
checks the crash buffer. A service listing with a null legacy device fails.
It stops its HAL request on exit. No enrollment, challenge, authentication,
template enumeration, removal or user/group activation is requested by the hook.
Its sanitized result is `fingerprint-result.json` in the existing B-try log dir.
The HAL itself retains stock initialization and Keymaster interactions.

## Shipped evidence and the missing .ko

The acceptance README's stock init evidence is correct: `init.sunwave_hs.rc`
contains `insmod /vendor/lib/modules/hs_sunwave_fp.ko`, and the service rc starts
`vendor.fps_hal` as system, with system/input/uhid groups.
However **neither the saved vendor image nor the original firmware-package vendor
contains that .ko**. Their `/lib/modules` inventories contain audio modules and
`qca_cld3_wlan.ko`. Do not manufacture module evidence from a stale init command.
The saved stock boot kernel contains `sf_ctl_ioctl`, `sf_probe`, `sf_platform_init`
and `__initcall_sf_ctl_driver_init6`: the actual shipped implementation is built
in. REA/Ghidra inspected that code instead, together with both HAL ELF files.

Original REA evidence: `/home/user/A9-build/mainline/integ-20261023-fp1/` (FP2 receipts are in the sibling `integ-20261023-fp2/`):

- `rea-*.json.zst`: complete REA records, compressed and round-trip hash
  verified (`rea-compression.json`); names are hal, service, kernel,
  kernel-relocated and hal-service-init.
- `stock/focus.json`, `stock/relocations.json`, `focus-stock.py`: address-preserving
  kernel excerpt and explicit RELATIVE relocations used to recover pointer data.
- `stock-assets.json`: selected file provenance and SHA256, including all TA files.
- `extract-stock.py`: original package LP/ext4 absence checks. The checked-in
  `extract-assets.py`/`stock-package.py` reproduce selected asset extraction.

| Artifact | SHA256 |
|---|---|
| Saved stock boot kernel Image, from `integ-20261023-5/stock-evidence/Image.raw` | `2f0edd65ddb2bfb743727560e740448ae931e0b31972ac4c4e0c063a88f3ceb1` |
| Stock `lib64/hw/fingerprint.default.so`, saved vendor and package agree | `5e2bbcf3458a251786a14ba3b01c17c8f6c79017c0eec0071a574f55719ed193` |
| Stock fingerprint 2.1 service, saved vendor and package agree | `014d6c4ee5129db573c6dd5dfc7c8db2cfcd02c3f787f9ec51ca71ea41c81f6c` |
| RELA-resolved fingerprint kernel excerpt | `0771bb57f34e84bd8f2aa3968975624d74d0afb97170e944b620cf52bbae3abb` |
| Signed `sw_fp.mdt` from package `NON-HLOS.bin:/IMAGE` | `60d047ae849627d018339d87e763b2a52c1e45abbdc9092c2edbe016099f039c` |

Kernel addresses below are stock link VAs. HAL/service analysis addresses are ELF
VAs plus Ghidra's `0x100000` base. Reconstructed kallsyms supplies kernel symbols;
external routines are unmapped in the small excerpt. Initial decompilation showed
zero data pointers because the kernel applies RELA at boot. The later excerpt
resolves only selected data slots from actual `R_AARCH64_RELATIVE` addends, with
zero load slide; code bytes remain unchanged. Indirect platform callbacks are
assigned at runtime by `sf_platform_init`; a decompiler's null callback is not
stock execution. These are static observations, not device test results.

## Stock chain

1. HIDL service `getInstance`, analysis **0x102678**, calls
   `hw_get_module("fingerprint")`, opens its legacy device, requires API `0x201`
   and registers the legacy notify callback. `main.cfi` **0x104100** registers
   `IBiometricsFingerprint/default` even if legacy device opening failed.
   `setNotify` **0x102b28** returns the legacy device pointer, not a chip ID.
   REA `ev_942671432b81a672e0697bc697f011ace0daaaf1b2d3b739022fa84f8ebf05bb`
   and `ev_138d1c11d02f009d4ca7092d018e7d409baca693f6579e7ce67a87c0508806c2`.
2. HAL `sf_trustlet_client_open_session`, **0x170f2c**, locates `sw_fp.b00` and
   calls `QSEECom_start_app(..., "sw_fp", 0x100000)`. It sends initialization
   command `0x1000`, opens `keymaster` with `keymaster64` fallback at **0x171ff4**,
   obtains a target-bound authentication-key capsule with command **0x205**,
   subcommand **2** at **0x171b48**, then sends it to `sw_fp` with command **0x1012**. It uses
   direct libQSEEComAPI -> `/dev/qseecom` calls, not a replacement fingerprint TA.
   REA `ev_4e12f4f69c1b1972d42c6f41bff951ab60b5fa5a4284297862f3b9adec5fadaf`
   and `ev_b1a269307c9c3e70a4f8da1b39fddd2c3e71a1855fae4e858e2c07502819f893`.
3. HAL SPI `write_then_read`, **0x142630**, packs TX/RX lengths and bytes into
   `sw_fp` command **0x100b**, calls the trustlet exchange and copies back the TA
   response. Linux does not read sensor frames through spidev or this char
   device. Stock `sw_config.xml` says `Environment="TEE"`, QSEE SPI device **6**,
   sensitivity for chip **0x8395**, key/navigation and image dumping disabled.
   REA `ev_34caa659ca7dcba84c58f41413295164b628984c23f06aa858119ce27f400349`.
   This establishes the normal-world TEE route; the signed TA's internal SPI
   implementation and per-listener call sequence have not been decompiled.
4. HAL pre-init **0x14d6c8** opens `/dev/sunwave_fp`, asks the driver version and
   starts a `NETLINK_KOBJECT_UEVENT` receiver at **0x14fc70**. It signals its IRQ
   semaphore on **`SPI_STATE=finger`**. Stage1 **0x14d03c** probes/logs the device
   ID; stage2 **0x14f7d0** accepts `0x8395`, initializes its IC implementation and
   logs `fingerprint device is ready.`. These two log facts form the hook's
   chip-ID predicate, without adding a fabricated kernel sensor-ID attribute.
   REA `ev_81afcf5a9c4b9ccec41c9509ef24958981d08ec99ac66fe6c6ab0b379ff054e8`,
   `ev_d80446cb99e653e23eb2a69f5c80e42d1b2a5b09576b3e6769dab723aefc4dde`,
   `ev_aee6f159e7a4451e100acbc8d2f803f6e2c8a3a4ac39d2b104e09259cfb73e94`.
5. HAL `sf_service_init` **0x16b4fc** starts **0x16b6a0**, which also registers
   stock `vendor.sw.swfingerprint@2.0::ISwfingerprint/default`.
   Both stock interfaces are declared in the vendor manifest.
   REA `ev_191df8e22d56902791cc7b229fd0c6ab8d15355fd10eb9b2cbbf3d0470fbf9ba`.

`qseecomd` supplies registered secure listeners; this image retains integ5's
stock daemon, listener workers, QSEE/ION transport, Keymaster, startup and actual
`vendor.sys.listeners.registered=true` readiness. The fingerprint init action
waits for that property. HAL -> QSEE is direct, so the daemon is a listener
prerequisite, not the SPI transport. Exact `sw_fp` listener IDs and its secure
storage behavior remain unobserved; no new listener, RPMB provisioning or secure
storage implementation is introduced here. Existing Keymaster/listener evidence
is in `scripts/mainline/qseecom/README.md`.

## Driver ABI and board controls

Stock **0xffffff80089a6fc8** (`sf_ctl_ioctl`), REA
`ev_a187d4c54ec82b03c01d7eeb6812c8b1735aebfcc80bd6a1b113d151b71ad362`:

| Request | Stock behavior recreated |
|---|---|
| `7300`, `7301` | Init/deinit return 0; initialization occurs in probe |
| `7302` | Reset high 1ms, low 10ms, high |
| `7303`, `7304`, `7321` | IRQ enable/disable/request return 0; probe owns IRQs |
| `7305`, `7306` | SPI clock requests return 0, no clock operation |
| `7307`, `7308` | Power GPIO100 high +10ms / low |
| `40087309` | Copy 8-byte signed key/value pair; stock key map and input sync |
| `c008730a`, `730b` | Speed/config requests return 0, no data transfer |
| `730d` | Copy stock attribute word 0 (not a sensor ID) |
| `80087320` | Copy 32-byte `v2.2.10-2018-03-05` including zero padding; encoded size is pointer size 8 |
| Other, including HAL's `7330` version report | `EINVAL`, as shipped |

Native ARM64 is the stock HAL ABI. Compat forwards pointer-free requests and
native command values, like the stock compat wrapper at **0xffffff80089a73f8**;
no separate 32-bit HAL claim is made. There are no read/write/mmap sensor-data
operations. The misc node starts mode0600; ueventd assigns stock system:system
0660. Kernel file references survive removal safely; IRQ/work are drained before
freeing state. Unknown ioctls fail.

Stock `sf_platform_init` **0xffffff80089a7dd8** installs GPIO power/reset callbacks.
GPIO init **0xffffff80089a7e78** looks up seven stock pinctrl states, powers high
and waits 10ms. Reset is **0xffffff80089a81f8**; power **0xffffff80089a8170**.
**`sf_spi_clock_enable` at 0xffffff80089a81f0 is simply return 0.** No regulator
or clock handle appears in this fingerprint driver/DT chain. Its board supply
switch is GPIO100; unknown upstream rail ownership is not guessed.
REA `ev_7c73972f04344c4d6479e3acb10a255b9f4bb51310ae72f1c8129678591cf1b3`,
`ev_2d095d68de4147d68524dc954ce6aee8b65d20b8e42d2ff8eb400f6e1e47a410`,
`ev_7b6d513e3c5f95b704e0ae127a3477c9e98be2a01a25882c4f42242817d79423`.

The stock merged DT has IRQ **97**, reset **19**, power **100**, logic **70**;
all muxed GPIO with 2mA drive. Reset uses pull-up; others bias-disable; power/reset
are high states, IRQ is input. Stock logic high/low states have identical bias
and mux settings, not driven output levels. The mainline driver uses GPIO70 as
input, matching stock's `gpio_get_value`. Probe and rising logic events sample
it twice 300ms apart and power off if both samples are high, as stock does at
**0xffffff80089a7a00**. IRQ97 uses falling edge and a 500ms wake event; logic70
uses rising edge and NO_SUSPEND. Both request IRQ wake (logging unavailable wake support without failing probe), as stock probe does
at **0xffffff80089a7410**. IRQ work emits the misc-device uevent from
**0xffffff80089a7900**, confirmed after RELA resolution in REA
`ev_5120d53a2051e905cc8a3952198951011cc9112148afd8178c7a24655175f0a1`.

The DT overlay removes only 19/70/97/100 from the incoming reserved GPIO set.
It preserves secure QUP SE5 pins14..17 and leaves its Linux SPI controller off.
The source Android DTS includes the same node/states; its default reserved set
reopens these controls. Other composed builds must apply the final reservation
subtraction as `build.py` does. Display-notifier integration and screen-off
fingerprint wake qualification are outside this initial registration proof;
stock config disables screen-off algorithm gating, key emulation and navigation.

## Host build and verification

```
python3 scripts/mainline/fingerprint/build-fp2.py
python3 scripts/mainline/fingerprint/smoke.py
python3 scripts/mainline/fingerprint/policy-smoke.py
```

The build uses the existing FIX11 compiler command cache and integ5 export table,
then native modpost/link. It copies the existing vendor sparsely and replaces
only explicit overlay members with debugfs, retaining original inode metadata
for replacements. No shared source/object cache changes, kernel rebuild, system
image change or storage format is needed. The original 100 modules remain byte
identical; the new module is loaded by its dedicated post-fs-data init action.
All firmware, TA files and HAL blobs stay in the local build directory.

Validation receipts are local: `module-imports.txt`, `module-commands.json`,
`dt-delta.json`, `abl-check.log`, `policy-build.log`, `link-symbol-audit.json`,
`module-audit-fp2.json`, `vendor-fsck.log`, `integrated-build.json`, `SHA256SUMS`.
The symbol audit uses saved GSI Bionic/linker exports; it proves static global
symbol coverage, not runtime namespace or TA behavior. SELinux retains the
incoming permissive/enforcing domain decisions and adds no permissive domain.
Only the fingerprint HAL gets its device, QSEE/ION, uevent and private hwservice
rules, with init noatsecure for its image-defined stock library path.

`smoke.py` exercises five host-only groups: real ID + service + stable PID;
listed wrapper with null/failed sensor; process restart; crash record; invalid
IDs/ready messages and missing hook context. No phone/target executable runs.
Kernel checkpatch has no errors; warnings retain stock's short `msleep` calls
and the required mutable uevent string-array API. Hardware registration remains
unproven until the provided B-try command is run by its authorized owner.

## FP2: saved fp1 failure and repair

Saved evidence is `fp1-run-20261023/onb.log` and
`boot-0000000067/{kernel.log,logcat.log.prev}` under the mainline build root.
No phone command was run while preparing this repair.

- The actual module AVC is **vendor_init -> vendor_file, system:module_load**,
  not init -> kernel. Vendor rc builtins run in vendor_init. The policy now
  permits that exact caller/target operation; module bytes and GPIO behavior
  remain identical to fp1.
- The listener check's ordinary `getprop` emitted **Access denied finding
  property "vendor.sys.listeners.registered"** in the saved previous logcat.
  This is an unreadable private property, not evidence that the daemon was
  stopped or its listeners absent. Its property label is
  `a9ml_tee_listener_prop`, readable by tee/Keymaster but not ordinary shell.
  The hook now reads it through the existing native root channel, along with
  actual `init.svc.vendor.qseecomd` and `init.svc.vendor.keymaster-4-1` states.
  It allows 30s for readiness and reports the three bounded states on failure.
  It never sets listener readiness or restarts either daemon. Shell still has
  no permission to read the private property.
- `mkdir /data/vendor_de/0/fpdata` also failed. FP1 overrode the platform's
  vendor-DE path with `fingerprintd_data_file` (the system-user-directory type).
  FP2 uses LOS23's actual **fingerprint_vendor_data_file** for that path and
  HAL data permissions. Vendor init can create it; framework gets only directory
  search/getattr so it can inspect the precreated path, not template-file writes.

The SCM messages are about separate interfaces. In the saved FIX11 kernel
source, `qcom_scm_qseecom_init` (qcom_scm.c:2429) logs "untested machine" when
it skips the upstream **kernel QSEECom client** platform-device registration.
The QTEE skip (line2642) skips **smcinvoke**, not Android QSEECom. The private
`qcom_scm_qseecom_android_call` (line2228) directly calls `qcom_scm_call` under
its transaction mutex, independently of both checks. `a9-qseecom.c` registers
`/dev/qseecom` and `/dev/ion` separately, and the saved boot explicitly reports
**Android transport ready (native arm64, whitelist 1)**. These existing kernel
settings remain unchanged.

Row29's inherited hardware init mounts metadata on `fs`, starts stock qseecomd
then stock Keymaster, and only invokes its wrapped-vold worker on `late-fs`.
The saved run reached that worker, establishing ordering, not daemon success.
Saved logs do not retain an authenticated readback of the daemon/listener states;
FP2 measures them natively before fingerprint initialization. The stock HAL's
QSEECom_start_app/load/query/send path and `sw_fp` TA use this same existing
Android frontend and listener continuation implementation; they do not require
enabling the unrelated kernel-client or QTEE probes. Actual `sw_fp` loading
remains a device result to collect with the provided command.

FP2 rebuild uses the frozen fp1 compiler policy inputs retained in its own output,
rather than the older wrapped-build directory removed by shared host cleanup.
The boot, DT and Sunwave module are hash-pinned and reused, as is the passing ABL
receipt. The full new vendor is read back for payloads and SELinux labels, checked
with e2fsck, and audited against all 101 modules. Its original 100 module payloads
must equal integ5. No kernel build or protected storage change is introduced.

`policy-smoke.py` queries actual compiled binaries with a host libsepol helper:
it reproduces fp1's module-load denial and requires fp2's allowance, vendor init
fpdata creation, framework directory search, HAL directory write, and continued
shell-property denial. Like the existing acceptance sweep it clears only the
unsupported functionfs_seclabel capability bit12 in **disposable host parsing
copies** for libsepol3.8. Full delivered policy bytes and capability bits are
unchanged by that query adaptation. `policy-smoke.json` records both hashes.
`smoke.py` also covers delayed/private listener readback without any phone/target
execution. A successful host rebuild does not establish hardware registration.

## FP3: missing platform device in the applied DT

The saved FP2 run is `/home/user/A9-build/mainline/fp2-run-20261023`.
`onb.log` passes the native listener/daemon/Keymaster check and fails the
combined node/file check. Kernel line 1193 records the Sunwave module loading,
but there is no probe/ready message. `fdtget` on the actual FP2 `integ.dtb`
returns `FDT_ERR_NOTFOUND` for `/fingerprint`. The platform device was absent,
so the loaded module could not create `/dev/sunwave_fp`.

The overlay source used a direct `/ { fingerprint {...}; };` block. With
`/plugin/`, dtc left this as a bare node in the overlay, while fdtoverlay applied
only the fragments generated for `&tlmm`. Change it to `&{/}` to generate an
explicit root-target fragment. The inline SM6115 Android tree uses the same
form. The boot builder now requires the merged device's compatible, four exact
GPIO references, four pinctrl references and wakeup property; its old
allowed-additions inventory accepted an empty set of device properties.
This assertion replaces that incomplete coverage. The old FP2 DT fails it.

The saved HAL log also establishes a distinct diagnostic boundary: stock
`libQSEEComAPI` finds `sw_fp.mdt` and eight segments (`app_arch=1`,
`total_files=9`), then its native LOAD_APP request returns `EINVAL`. This was
not a missing firmware path. Retained REA for the identical stock library
(SHA256 `2f70ad2dd4d3137f18df34022021bc3bf9533ac2161de0e1afc6e7d338d82094`),
function `__QSEECom_load_image` at analysis VA `10561c` (ELF `561c`), shows the
84-byte request, architecture at byte 76 and app ID at byte 80. Stock SCM
dispatch at `ffffff80087b5848`, case 1, uses argument info 3, matching the
current all-value load call. The stock DT sets `qcom,appsbl-qseecom-support`
and `qcom,commonlib64-loaded-by-uefi`; the downstream driver skips app-region
notification in that case. FP3 does not change that transport or speculate
about SCM argument types/region initialization. Restoring the stock sensor
power/reset/probe is the confirmed fix; whether the TA loads after it is a
runtime result, not a host assertion.

Evidence reused from `qseecom-transport-20261021.tar` is saved under
`integ-20261023-fp3/evidence/`: full client/SCM REA records and their original
carriers, plus the matching reference source. The reference loader and SCM
parameter definitions are from [Qualcomm's downstream kernel](https://android.googlesource.com/kernel/msm/+/refs/heads/android-msm-redbull-4.19-android11-qpr3/drivers/misc/qseecom.c).

The hook replaces the combined check with named node, driver-binding and
nine-file checks and prints every prerequisite before refusing. It separately
captures the HAL PID's Sunwave/QSEE messages in `fingerprint-hal.log`, even
while waiting for HIDL registration. Sunwave embeds PID/TID in its Android
log tag; the old literal `fsfp-hal-device` filter missed those messages.
Chip ID, ready message, HIDL listing, unchanged process and crash checks still
apply. No enrollment or authentication is attempted.

Build on the host with `python3 -I scripts/mainline/fingerprint/build-fp3.py`.
This rebuilds the boot DT over integ5, preserves kernel/ramdisk/cmdline, and
reruns the existing ABL check. Vendor, policy, HAL and module are byte-identical
hard links to the checked FP2 artifacts; their existing fsck, metadata, policy
and 101-module ABI results apply unchanged. The user/coordinator command
remains `scripts/mainline/fingerprint/run.sh`, now selecting
`/home/user/A9-build/mainline/integ-20261023-fp3/{boot,vendor}.img`.

## FP4: restore QSEE resource votes and observe actual HAL state

Saved FP3 evidence: `fp3-run-20261023/boot-0000000074/`. Kernel line 1245
confirms the Sunwave probe. All hook prerequisite nodes/files/binding pass.
`logcat.log` lines 1696–1906 show that `sw_fp` is **not loaded**: the stock
library assembles nine files, submits LOAD_APP, receives `EINVAL`, and detaches.
The preceding log contains repeated failures and SIGSEGV at line 53311 in
`sw_v2_service`; kernel init messages show changing HAL PIDs. The previous
hook's `HAL_NOT_RESTARTING` was insufficient: an empty ordinary-shell read of
the private vendor service property satisfied `!= restarting`, and no HAL log
was collected. FP4 reads state and PID/birth through the existing native helper,
requires a recognized state, and retains its process/crash checks. It captures
filtered LOAD diagnostics in `fingerprint-kernel.log` on either success/failure.
No new shell property access or permissive policy is added.

The observed failure precedes normal-world sensor initialization. Stock HAL
REA at `170f2c` loads `sw_fp` before commands; `142630` transports SPI through
TA command `100b`, with QSEE SPI device 6. There is no evidence of a successful
SPI exchange in this run. Linux QUP SE5 remains disabled, and GPIO14–17 remain
reserved to TZ. Enabling a Linux SPI controller would compete with the stock
route. The confirmed stock control sequence remains power GPIO100 high plus
10ms, reset GPIO19 high 1ms/low 10ms/high, IRQ97 falling edge, and GPIO70's
stock two-sample/300ms logic check. Stock reset `ffffff80089a81f8`, power
`ffffff80089a8170`, and clock no-op `ffffff80089a81f0` remain the basis for the
unchanged Sunwave module. There are no new reset pulses or SPI pinmux writes.

A missing stock dependency was found in the QSEE transport. Stock
`qsee_vote_for_clock` at **ffffff80087b6f18** votes the QSEE CE clock and bus;
`__qseecom_enable_clk` at **ffffff80087b64d8** enables the clock handles.
REA evidence `ev_bb0dd29ccb57a69c7269c07ec7eec5e6ca2ee68ce13d10d6d48d7718c7d6e415`
and `ev_abdc3b2298a93a2f5197d09ed5889c743fb1d43f65dc7d578f19fca223b8c810`
is retained in `integ-20261023-fp4/evidence/rea-clocks.json`, with its small
address-preserving carrier and extraction script. External data/callees remain
unmapped; this establishes static control flow, not current clock state.

The stock DT uses RPM `QSEECOM_CE1_CLK` for all four clock names and a
192 MHz operating rate. Qualcomm's [Bengal DT](https://android.googlesource.com/kernel/msm-extra/devicetree/+/refs/tags/android-s-v2-beta-1_r0.5/qcom/bengal.dtsi)
and [RPM clock driver](https://android.googlesource.com/kernel/msm/+/03ce931ca7831ca627405a8d231ea15cd306a72d/drivers/clk/qcom/clk-smd-rpm.c)
identify that voter as a child of CE1, RPM CE resource 0. Mainline exposes the
same resource as `RPM_SMD_CE1_CLK` (binding ID70). FP4 adds a separate SCM
`qsee` clock at the stock rate and brackets Android OS/app SCM calls with its
clock vote and existing SCM crypto interconnect vote. Each vote unwinds on
errors. SIP feature probes retain their earlier path. The dedicated clock
avoids the generic SCM core-clock probe's `INT_MAX` rate request. No clocks
are held permanently, and the SPI controller remains under TZ ownership.

This restores a stock resource requirement missing from the raw Android hook;
the saved logs alone cannot identify which LOAD validation/SCM/continuation
branch returned `EINVAL`. The frontend now logs those boundaries separately,
including initial SCM status and secure result/type/data and final completion.
It logs no payload buffers, physical addresses, keys or device identifiers.
TA load and real chip-ID success still require the next authorized device run.

Host build: `python3 -I scripts/mainline/fingerprint/build-fp4.py` invokes the
small native kernel replay if needed. It reuses frozen objects, recompiles
SCM/QSEE plus integ5's built-in statistics and embedded config, and runs native
genksyms/modpost, export-object compilation, linker/kallsyms and exception-table
sorting. Its generated 13,195-export CRC table matches integ5 exactly. It never
edits the shared cache or manufactures CRC rows. The actual embedded config is
checked against integ5. The 101 modules, policy and stock HAL remain unchanged;
only the vendor kernel hash marker is updated with metadata preserved. Host
checks require the DT delta to contain only the SCM clock properties, the
complete fingerprint node, matching native CRCs, matching marker, ABL PASS and
vendor fsck PASS. Hardware behavior and any later power/wake qualification are
not host acceptance claims. No independent runtime review was performed.

## FP5: shared memory for dynamic TA loading

FP4's saved kernel LOAD diagnostics show `arch=1 mdt=7260 image=3894274
scm=-22 result=18446744073709551615 type=0 data=0`. The request reaches the
initial SCM call: neither ioctl validation nor listener continuation caused
that error. The userspace transport remains independent of the skipped upstream
QSEE client registration and SMCI probe. Wrapped-key row29 uses SIP storage
commands via the legacy ICE descriptors, not QSEE LOAD_APP. The retained
Keymaster R3 evidence uses preloaded `keymaster64` lookup/GET_VERSION and does
not establish dynamic loading through this frontend.

Stock split files and request geometry are intact. `audit-load-image.py`
verifies every saved stock digest, ELF32/eight program headers, each segment
size, MDT = header plus hash segment, and the exact 3,894,274-byte buffer the
stock library submits (MDT plus b00..b07). The load segments fit in the stock
68 MiB secure-app region. `load-layout.json` retains that host evidence.
Stock LOAD_APP in `qseecom_ioctl` at `ffffff80087aebd0` submits the same native
84-byte ioctl request and owner50/service1/command1, argument info3. There is
no new filename, replacement TA, repacking or altered app-start ABI.

The actual kernel command line includes ABL's `androidboot.keymaster=1`.
Stock `qseecom_probe` at **ffffff80087b9ec0** skips region notification for
this protected-region handoff. Its fallback notification is visible near
`ffffff80087bae90..87bb0e0`, and stock SCM dispatch case0xd uses argument
info0x22. The existing `60000000..65c00000` reservation covers the stock
`61800000..65c00000` secure-app region. FP5 preserves that handoff and does
not force another region notification. An initial host-only region prototype
was withdrawn after checking the appended command line; it is retained under
`integ-20261023-fp5-region-initial`, without a READY marker.

The missing stock memory path is SHM bridge setup and buffer registration:

- Stock **qtee_shmbridge_init `ffffff8009c90f3c`** invokes SIP2/service0xc/
  command0x1c to enable bridge support. REA
  `ev_5ac6367c2d7157b00c99814855d5bfe4a47938e20ccfb5b619051648b0c9496a`.
- Stock **qseecom_register_heap_shmbridge `ffffff80087bc5d8`** registers the
  ION backing heaps with HLOS VMID3 and read/write permissions6 for HLOS and
  TZ. Probe registers TA heap19, QSEE heap27 and user-contiguous heap26.
  REA `ev_80f06208f84bcb7c76d3ce9e5fcb36f2a9a0afe8f60221d55e69fcd36727d0e5`.
- The previous port selected generic TZ memory and exported ordinary coherent
  ION buffers, without registering their backing memory. Restoring clock votes
  alone left this difference in place.

FP5 selects upstream `QCOM_TZMEM_MODE_SHMBRIDGE` while retaining
`qcom_scm.skip_qtee=1`. The upstream allocator registers SCM scratch/extended
argument memory. The Android frontend registers each page-aligned ION backing
allocation, lookup-name buffer and whitelist table with the same HLOS/TZ
permissions. These registrations last through their existing DMA-buffer/file
references. Teardown precedes returning pages to Linux; a failed teardown
quarantines the allocation. Unsupported bridge firmware retains upstream's
normal fallback, and LOAD logs report `shared=0` or `shared=1`. The actual
firmware result still belongs to the next device run; host checks do not prove
that this resolves the secure loader's failure.

New complete REA records/carriers are in `integ-20261023-fp5/evidence/`:
`rea-loader-memory.json` and `rea-stock-probe.json`. Probe evidence
`ev_16afa7741906c0230ff2e96c4aac0f7b585dfc62c002508331a6e3e944912910`
retains the region and heap-registration branches. The small carriers preserve
original addresses; external globals/callees remain unmapped. This corrects
the earlier *inferred* bring-up claim that stock never enables SHM bridge.

Build with `python3 -I scripts/mainline/fingerprint/build-fp5.py`. Native
modpost produces 13,197 kernel exports: two new bridge APIs and two changed,
unused device-argument API CRCs after the configuration/header change. No
shipped module imports either changed API. All 101 actual vendor modules pass
the new-kernel audit: 6,243 imports, zero CRC/vermagic failures. CRCs are generated
natively; none are replaced to match the old table. The DT, Sunwave module,
HAL, policy and protected-region/SMCI choices remain as in FP4. Vendor changes
only its kernel hash marker, with metadata preserved. ABL, embedded config,
marker/readback, fsck and the existing hook smoke are checked on the host.

## FP6: match the TZ loader's validated extent

FP5's saved `fp5-run-20261023/fingerprint-kernel.log` records `shared=1
scm=-22 result=18446744073709551569` (signed **-47**) on initial APP_START.
The error occurs before TA initialization or SPI access. It does not identify
an app-name, signature or sensor fault.

New REA of the shipped `HLTE556N/tz.mbn` (SHA256
`abadf826998127933189347819f1c4622faa0ef8d8718fcc6d96834aabb23fc3`)
resolves this result. Full records are in
`integ-20261023-fp6/evidence/rea-stock-tz.json`:

- TZ dispatcher **1006ffa4**, case **32000101**, selects **100cdc14** with
  argument-info **3**. This confirms APP_START's owner50/service1/command1
  descriptor and three value arguments. Evidence
  `ev_408f120e969845b4547b39ec38f7762e74ea8d7cc0cf768f74c4797abcb426b0`.
- At **100cdc14**, the loader checks length addition and calls **10059884**
  on `(physical_address, mdt_len + img_len)`. Failure returns **-0x2f** at
  **100cdd28**, before any image-loader call. Evidence
  `ev_997bdc157bec8bca19a4e273f3b1d68a801267f44b8309e1e426e6691b2ea4f4`.
- **10059884 -> 100597ec -> 100b4638 -> 100b3910** checks the entire rounded
  range and read/write ownership. The registry path **100af938** looks up
  range coverage in **10169938/10169930**; an uncovered range fails. Evidence
  `ev_ed82587c97219d1b4a68f6529acf96db907caf2cca612b939ca2edff6327c8a9`,
  `ev_81968c5192d46fffdcf271e98ceac6633c7abe8aef93acba59757ae544edd9ae`,
  `ev_59e3351063c70343d86e289800908da97f5a2e5d405affc1f54dedcd6ccb0813`,
  `ev_c970c2d7d11cc16aee29243f0ce5ee323f17346a0a9c500e2b21306bad4c3fa7`.

The stock library at **10561c** sums MDT and all b00..b07 sizes, allocates that
sum and sets `img_len` to that sum; `mdt_len` remains 7,260. The earlier
`rea-client-full.json` contains those stores. Stock kernel ioctl LOAD at
**ffffff80087b183c..87b39d8** preserves both lengths, imports the contiguous
ION buffer and cleans its caches. Its packed 64-bit internal request places
command/MDT/IMG at offsets 0/4/8, the 64-bit PA at offset12 and **64-byte**
app-name at offset20. The old 32-bit request has PA at12 and app-name at16.
`qseecom_scm_call` **ffffff80087b5848** extracts only MDT/IMG/PA into the three
SCM registers; the packed structure and app-name are not passed as a buffer.
The app's ELF32 architecture does not select the SCM calling convention.

Stock shares the whole **16 MiB**, 4 MiB-aligned, below-4-GiB dynamic
`qseecom_ta_region` CMA heap, not only the current ION allocation. That heap
is the source staging memory; **61800000 + 4400000** is the separate secure
app destination. No new hyp assignment appears in stock's LOAD path. The
protected ABL handoff, preloaded common libraries, region reservation and
notification skip from FP5 remain applicable.

For this request, the populated image is **3,894,274 bytes**. FP5 registered
**3,895,296 bytes** after page alignment, but TZ validates **3,901,534 bytes**
(MDT + IMG), exceeding that registration by **6,238 bytes**. This is the
statically identified mismatch corresponding to the observed -47 branch.

FP6 copies the unchanged stock concatenation into a separate coherent,
physically contiguous, zero-filled staging allocation, capped at 64 MiB and
registered with the existing HLOS/TZ read/write SHM bridge. It covers the
entire validated extent (**3,903,488 bytes** after alignment), including its
zero-owned tail. The SCM command, lengths, original bytes and app-name stay
unchanged. The staging allocation stays alive through listener completion;
bridge teardown precedes freeing it, retaining FP5's quarantine on teardown
failure. No adjacent buffer is exposed, and no extra region notification or
secure-memory assignment is sent. Coherent CPU/user mappings plus the existing
DMA write barrier order the copy before SCM; cached ION maintenance is not
needed for this coherent staging allocation.

Build: `python3 -I scripts/mainline/fingerprint/build-fp6.py`.
`audit-load-image.py --out /home/user/A9-build/mainline/integ-20261023-fp6`
checks stock digests, concatenation, the old uncovered range and new covered
zero tail. Native bounds smoke covers the real geometry, exact cap and
malformed/overflow rejection. Reuse FP5's native SHM configuration, resource
votes, module ABI checks, DT/policy/HAL and host packaging checks. The existing
hook still requires actual TA-backed sensor ID/ready from a stable HAL process;
no success criterion was weakened. Static evidence and host checks do not prove
the next runtime TA-load or sensor result.

FP6 host result: native export CRCs, embedded config and DT are byte-identical
to FP5; all 101 packaged vendor modules pass 6,243 import checks with zero
CRC/vermagic failures. ABL, fsck, kernel/DT/config readback, vendor marker and
metadata checks pass. The five existing hook fixture groups pass. The kernel
replay's inherited FP4 summary line says the entire table matches integ5;
`abi.json` is authoritative: FP5's two new bridge APIs and two changed, unused
API CRCs are retained, generated by native genksyms/modpost. No CRC is forged.

## FP7: use the TA signed for the saved device firmware

FP6's `fp6-run-20261023` reaches the later generic secure result **-1** after
covering the loader's physical range. The saved logs show only `sw_fp` LOADs;
keymaster's running service is not evidence of a dynamic keymaster LOAD.
Prior preloaded `keymaster64` lookup/GET_VERSION remains the available control.
No new app load or storage operation is requested to manufacture a control.

The concrete mismatch is the TA signing envelope. Earlier extraction used the
QFIL package's `HLTE556N/NON-HLOS.bin`. Both saved daily-device modem slots in
`Backups/edl_2026-10-14_110759/lun4/` instead contain a different, internally
consistent signed variant. The executable/data files b00 and b02..b07 and
384-byte segment hash table are identical. Only b01 and its embedded copy in
MDT differ: OEM metadata, RSA signature and certificate chain. Both variants
pass RSA-PSS-SHA256 signature verification (32-byte salt) and full certificate
chain verification with current dates. Their root certificates differ:

| Signing source | Root certificate SHA256 |
|---|---|
| QFIL package TA and package TZ | `f8ab20526358c4fa4cef96d78c45180dc3db75e8f24051ad624448c134b4e861` |
| Saved device TA and saved device TZ, both slots | `ef4c82e0f428942aaca170f90a1fb1b3b4bc783ae19d8dd1de05ce29c1f0fa3c` |

The saved TZ executable segments are byte-identical to the package TZ used for
REA; its signing envelope differs too. This establishes the source mismatch
without reading the phone or its fuses. It makes authentication a concrete
candidate for the later generic failure, while the log alone does not uniquely
identify that branch. Qualcomm's [signed-image format and authentication
reference](https://www.qualcomm.com/content/dam/qcomm-martech/dm-assets/documents/secure-boot-and-image-authentication.pdf)
explains the metadata constraints and root-certificate checks (sections4–6).

Stock kernel REA still supports the retained transport: `qseecom_ioctl` LOAD
at **ffffff80087b183c..87b39d8** imports the contiguous ION image, cleans its
cache and preserves the request lengths. `qseecom_scm_call`
**ffffff80087b5848** extracts MDT/IMG/PA into owner50/service1/command1,
argument-info3. `__scm_call2` **ffffff80085eeb88** selects the standard ARM64
SMC convention; the observed -47 branch already established entry into the
expected TZ APP_START handler. There is no hyp assignment in this LOAD path.
`qseecom_probe` **ffffff80087b9ec0**, protected/appsbl skip branch
**ffffff80087bae90**, omits region notification for the existing ABL handoff.
Stock DT declares phase2 reentrancy and preloaded common libraries. Its CMA
source region and secure-app destination are separate. These retained facts
do not justify changing the calling convention, forcing region notification,
assigning Linux pages away, or claiming the secure SPI controller.

New full static REA is retained in
`integ-20261023-fp7/evidence/rea-stock-tz-loader.json`:
**100c9c6c -> 100d5a3c** enters the ELF/authentication loader; **10052d60**
invokes its metadata/hash verification helpers, and **10052f1c** performs
further image verification. **100cd990/100cd9e4** translate multiple later
failures to generic -1. Dossiers include
`ev_fcb9740de9f1e225a55e708a839d8fcea057c198ee9d918bb379e6b4502c531f`,
`ev_79cc17fdf9516c5b1df657edba74434e36983ad3f1896587ee1352854c9cebe4`,
`ev_19058c313691d18427e077489965c8c742223f00af1c274672d6ea805e6726b8`,
`ev_8e9f44fcfdeb84d83365b815fca74eff8834089ee948245187eddcac7ccc7651`
and `ev_519addc4bdcecb370b1a2239e7d8fde1d37c160f7084d5fe58cd46235e162579`.
These are static observations, not a trace of the failing hardware call.

`audit-signed-ta.py` checks both saved slots agree, matches the TA root to both
saved TZ roots, verifies both reference signatures/chains, and selects the
complete **unmodified** golden nine-file set. The extractor now uses that saved
modem by default. No blob is edited or re-signed. Selected MDT SHA256 is
`08ab4ca7a2eaf58d6d583e8afd5453e5fe6c8d322b05300df2c5a355129cd933`;
b01 SHA256 is `82657aa5d982eecf25bca6359b0666c2fcefc02073d943e51c21bfc98e50ea02`.

Build on current main with
`python3 -I scripts/mainline/fingerprint/build-fp7.py`. It requires the
published **integ-20261023-7** baseline, keeps its boot/kernel/DT byte-for-byte
and replaces only the two differing TA files in its full integrated vendor.
All other vendor regular files must match, with replacement metadata preserved.
Thus the combined GPIO11 wake fix, media/sensor/TEE, GNSS, radio, modules and
HAL/policy integration are carried forward. ABL and vendor fsck are checked;
unchanged kernel/module hashes retain the baseline's101-module/6,243-import
ABI proof. The existing hook still requires a real nonzero chip ID and stable
HAL process. Hardware TA-load and sensor success remain pending the user run.

## FP8: capture chip initialization before the startup logs roll over

The supplied `fp7-6r3-20261024` run loads `sw_fp` successfully (app ID3).
Its retained HAL log contains only periodic `sf_get_device_state state:0001`
and `healthMonitoringThread state:0001` messages from one process. It contains
neither a failed initialization nor its earlier chip-ID/ready messages. The
old hook repeatedly overwrote `fingerprint-hal.log` with the current logd tail,
including after the30-second stability window. That loses one-time startup
proof once the busy boot log rolls over.

Focused stock REA establishes the post-load sequence:

| Stock function, REA address | Relevant behavior |
|---|---|
| HAL `sf_log_printf`, `142168` | Level2 maps to Android INFO, so the INFO filter was correct. |
| HAL init helper, `15c0d0` | Sends config `1007`, probes the actual chip through stage1 `14d03c`, initializes it through stage2 `14f7d0`, then sends TA init `1006` with that chip ID. Failures stop initialization. |
| HAL `Initialize`, `15b884` | Creates the health thread only after that helper returns success. |
| HAL health thread, `15362c` | With initialization stage4, sends `1017` and logs the returned state. |
| HAL exchange, `171668` | Uses `QSEECom_send_cmd` and forwards the TA's log messages. |
| TA `sf_device_init`, `148d0` | Logs the passed chip ID, accepts `8395`, initializes the IC and logs `fingerprint device is initialized.` only on success. |
| TA handler, `14f18`, case `1017` near `15870` | Returns its cached state; this command does not read a fresh chip ID. State1 alone is insufficient acceptance evidence. |
| TA SPI open, `127cc`; transfer, `12d8c` | Configured QSEE SPI ID6 becomes secure API enum5; secure full-duplex transfers retry by closing/reopening on failure. |

HAL addresses use the existing Ghidra image base (ELF VA +`100000`); TA
addresses use ELF VA +`10000`. The analysis carrier reconstructs unmodified
bXX segments at their program-header file offsets; SHA256
`263b872cd3ec8a275bfdaf2738825d24511c4f46f189bf051e0cd3b3c6d659fa`.
Full static records are in `integ-20261024-fp8/evidence/rea-hal-postload.json`
and `rea-ta-postload.json`. Key Evidence IDs are
`ev_eb41bc8c95f67ea09d48f4815a851df7d086cc6bc0b3a07c70ce9c75d553ec98`
(init helper), `ev_8290977caf53be95944262d220189f63f6d6c4e2c9fa9c691e098d5bf2c01778`
(thread creation), `ev_36047421425ce26dbfcb07fba42afa9194d7ad685432e53ca22199bb913cd305`
(health), `ev_6b88147e89258595bdd383b83467d0c575bbdb50bc6c8c826553ba01d80f4787`
(TA init), `ev_6692826e65eb92aeddf38e77a03f58e9aabb2dd7f2bea0b7c577b831bbb6476b`
(TA commands), and `ev_7abbfd36dd58a1cfb588934225cf892b125e26adc0d41ea78a73ed2ed5425857`
(secure SPI open). Static control flow supports lost startup observation as
the diagnosis; the saved run still lacks direct nonzero-ID proof.

Stock `sf_spi_clock_enable` at `ffffff80089a81f0` remains a no-op, and the
control-driver reset/power/IRQ ABI remains unchanged. No Linux clock vote or
SPI claim is added. The inherited DT leaves both SE5 SPI/I2C controllers
disabled and GPIO14..17 reserved to the secure path; GPIO19 reset,100 power,
97 IRQ and70 logic remain available to the control driver.

FP8 removes the init `interface` lazy-start entry so Android cannot start the
HAL before the existing listener+enable property gate. The stock HAL binary,
VINTF registration and policy remain unchanged. Before setting enable1, the
hook attaches a live main-buffer logcat stream and waits for its unique marker
to be observed. It retains bounded, deduplicated, PID-specific safe facts;
it never saves raw startup serial, key, calibration or template logs. A lost
capture fails explicitly and its host child is always stopped in cleanup.
A real nonzero chip ID plus the HAL ready phrase or the equivalent TA initialized
phrase is still mandatory, together with HIDL registration, unchanged PID/birth,
30-second stability and a readable crash buffer with no HAL crash. A health
state alone cannot pass. No enrollment or authentication is requested.

Build `python3 -I scripts/mainline/fingerprint/build-fp8.py` on merged main.
It derives **integ-20261024-fp8** from the complete **integ-20261023-6r3** image,
changes only `etc/init/init.a9-fingerprint.rc`, preserves its metadata and
verifies every other vendor regular file is identical. Native init verification,
ABL, fsck, DT control pins/secure SPI ownership and the five hook fixture groups
are checked. Byte-identical kernel, export tables and all vendor modules retain
6r3's native101-module/6,243-import ABI proof, including FIX3 audio. The fixtures
also exercise a real host pipe/thread with a synthetic producer, rollover,
wrong-PID evidence, capture failure, cleanup, restart/crash and private-log
filtering. Host checks do not qualify sensor hardware; that needs the user run.
Run `scripts/mainline/fingerprint/run.sh` for the prepared B-try hook.

## FP9: restore interface startup and observe registration through the native channel

The supplied `fp8-run-20261024` proves actual chip8395 detection, both ready and
TA-initialized messages, a successful TA load and a stable HAL. Its diagnostic
files also contain missing init-interface mapping messages and shell denials
for fingerprint hwservice discovery and binder calls. Thus the shell `lshal`
failure did not establish that the stock process failed to register.

FP9 restores the init line
`interface android.hardware.biometrics.fingerprint@2.1::IBiometricsFingerprint default`.
The existing vendor manifest fragment already declares that exact version,
interface and default instance with hwbinder transport; the build verifies it
in the actual parent image. The saved stock service rc under
`vibration-f0-20261020/stock-init/init/` runs the same executable eagerly in
`class hal`, without `disabled` or an interface declaration. Our explicitly
controlled service needs the interface mapping for hwservicemanager lazy start.
The driver, HAL binaries, manifest, policy and secure SPI ownership remain
unchanged. No shell HAL permissions are added.

Registration now uses the existing native B root channel:
`lshal list --neat --types=b -i -p --service-status`. The registered binderized
section must contain the exact default interface with `alive` status and the
same server PID as the measured HAL. A manifest-only declaration, missing row,
wrong server or unresponsive service cannot pass. An unrelated HAL's table
error does not invalidate a positive exact live row; command timeout or failed
transport still fails. These output options and binderized-table semantics are
specified by [AOSP ListCommand.cpp](https://android.googlesource.com/platform/frameworks/native/+/refs/heads/main/cmds/lshal/ListCommand.cpp).

Restored lazy startup can precede hook attachment. If an existing HAL has no
retained initialization proof, the hook requests one controlled `ctl.restart`
after arming capture and before fixing the measured PID/birth. The new process
must appear within20seconds. There is no retry of a measured crash or restart.
The real chip-ID/initialized, registration and30-second stability requirements
remain. Enrollment and authentication are still not requested.

Build `python3 -I scripts/mainline/fingerprint/build-fp9.py` from merged main.
**integ-20261024-fp9** derives from current integrated **integ-20261024-8**, not
an older fingerprint side image. Only the fingerprint init rc changes in
vendor; all other regular files and replacement metadata are checked. Image8's
FBE trace library/startup diagnostics, FIX3 audio, sleep logger and acceptance
assets are retained. FBE remains unresolved. Native init/ABL/fsck and the
unchanged native101-module/6,243-import ABI evidence are retained or checked.
The five focused hook groups cover native registration filtering with an
actual host shell/stub, missing/dead/wrong-process rows, preexisting HAL capture,
real-ID evidence, restart/crash and stream cleanup. A frozen
`run-fingerprint.sh` is delivered beside boot.img/vendor.img. Host checks do
not qualify registration on hardware; that remains for the user run.

## FP10: repair enforcing-console observation permissions

The supplied `fp9-run2-20261024` stops at prerequisites, before arming HAL
capture. It reports **all three** device nodes missing while the driver is
bound, firmware is readable, listeners/keymaster are running and the saved
kernel log shows a successful `sw_fp` LOAD. The failing run's actual linked
policy (`vendor-overlay/precompiled_sepolicy`) denies `a9ml_rootsh` **getattr**
on the fingerprint, TEE and ION character-device labels. Host libsepol queries
reproduce those exact three denials. `test -c` returns false when stat is denied;
this was an observation failure, not evidence of an absent driver or early
uevent. The missing HAL log follows directly from stopping before capture.

The same enforcing policy also blocks the HAL process directory/file reads
needed by the existing `pidof` and `/proc/PID/stat` identity checks. FP10 adds
only three character-device `getattr` permissions and HAL-domain directory
`getattr/search` plus file `getattr/open/read`. Device open/read/write/ioctl,
process ptrace and block writes remain denied to the diagnostic console. No
permissive type, driver timing change, node creation or HAL I/O permission is
added. The unrelated logger's init-process traversal denial is not used to
justify wider process access.

The hook now stats each device before testing its character-device type.
An access error is `unreadable`, an absent path is `missing`, and an existing
wrong type is `not_character`. An unreadable prerequisite fails explicitly;
it cannot be mistaken for a valid node or a probe that has not run yet. The
actual chip-ID/initialized, protected native HIDL query and stable-process
requirements from FP9 remain unchanged.

Stage and test the compiled policy with:
`python3 -I scripts/mainline/fingerprint/policy-fp10.py --base /home/user/A9-build/mainline/integ-20261024-10/vendor.img --out /home/user/A9-build/mainline/fp10-host/policy-main`.
It reproduces five denied metadata/proc operations, checks eight required
permissions after repair, checks16 I/O/write/ptrace controls remain denied,
and reads the binary permissive map to require zero permissive domains.
The functionfs capability adapter changes only throwaway host query copies.
The shipped policy is the native secilc output. Exact failing-run evidence is
retained separately in `fp10-host/fp9-observation.json`.

Build `python3 -I scripts/mainline/fingerprint/build-fp10.py` from merged main.
**integ-20261024-fp10** derives from the latest integrated **integ-20261024-10**,
which includes integ9, `9988071`'s stock boot identity/FBE runtime delivery and
`44cd6fb`'s finalized image digest. Only vendor CIL and its linked binary change;
all other regular files and replacement metadata must match. Boot, kernel, DT,
HAL, signed TA, manifest, audio/radio, assets and module ABI remain unchanged.
The complete frozen integrated10 runtime and launchers are retained, with the
updated fingerprint hook. Native ABL/fsck, policy decisions, artifact/source
pins and the five existing hook fixture groups are checked. Hardware success
and real FBE reopen remain for owner runs. The fingerprint launcher is
`/home/user/A9-build/mainline/integ-20261024-fp10/run-fingerprint.sh`.

FP10 also carries concurrent main `501bb3e`: the enforcing generic-netlink
cap socket rule and updated Wi-Fi collectors are linked/copied into the same
image/runtime. Host policy queries check those socket permissions too.

## FP12: accept the stock service's hidden debug PID

The supplied `fp11-run-20261024` proves actual sensor initialization and stable
HAL identity, but its native listing check fails with query status0. Stock
registration is present in the shipped service: `main.cfi` at **104100** builds
`default`, calls `IBiometricsFingerprint::registerAsService` at **1041d0**,
exits1 on a nonzero return and joins the RPC pool on success. Its SHA256 is
`014d6c4ee5129db573c6dd5dfc7c8db2cfcd02c3f787f9ec51ca71ea41c81f6c`.
The standard interface is already labeled by the platform
`plat_hwservice_contexts` as `hal_fingerprint_hwservice`; vendor contexts label
the separate Sunwave interface. The run's policy permits the stock HAL's add
and find operations for both labels. No service, manifest, context or policy
change is warranted by this failure.

The concrete hook bug is mandatory numeric debug PID matching. The exact
`lib64/a9-fingerprint/libhidlbase.so` in integrated11, SHA256
`151f1926871e843b8fedec754ba165637245744770e4bfee69afbf8c467c868e`,
implements `details::getPidIfSharable` at **14ab08** as only
`mov w0, #0xffffffff; ret`. Its `IBase::getDebugInfo` at **17c364** also supplies
PID-1 in the callback record. This is intentional stock behavior, not a dead
server. [AOSP TableEntry.cpp](https://android.googlesource.com/platform/frameworks/native/+/7b6a56ef2a/cmds/lshal/TableEntry.cpp)
formats NO_PID as **N/A**, independently of the service's alive status.
FP9's numeric-only parser rejects that valid registered-row format and reports
`missing_or_unresponsive`.

REA addresses above are ELF VAs +100000. Full static records are retained at
`/home/user/A9-build/mainline/fp12-host/rea-service-registration.json`,
`rea-hal-open-registration.json` and `rea-stock-hidden-pid.json`. Evidence IDs:
`ev_138d1c11d02f009d4ca7092d018e7d409baca693f6579e7ce67a87c0508806c2`
(service main), `ev_065d16607eae2dc865895969872e1f22a932c93edbf797bb8a48188d66922967`
(hidden PID getter), and
`ev_feb7d441ab7a5aa1140c2dd41f060f928853d17208858c196271d8fa78ba1c38`
(debug callback). `registration-policy.log` contains the four positive compiled
policy decisions. The run overlay receipt changes no policy files, so those
queries use its unchanged integrated11 policy. Static analysis does not itself
prove a live registration during that run.

FP12 still requires exactly one alive registered binderized default-interface
row. If a numeric server PID is published, it must match the observed HAL.
N/A (or the underlying-1 representation) is accepted as hidden debug metadata;
it never substitutes a fabricated PID. The hook records row status, parsed
row count, parse errors and whether the PID is hidden, matched or different.
Missing, dead, malformed, duplicate, declared-only and conflicting numeric-PID
rows fail. Real sensor proof, the HAL's separate PID/birth identity,30-second
stability and crash checks remain mandatory. No enrollment or authentication
is requested.

The five existing host fixture groups now replay the actual filtered query
with a stock-format N/A/alive row and cover hidden/dead, malformed, duplicate
and conflicting numeric-PID cases. Full fixtures and syntax checks pass. This
is a hook-only fix on main; no new image is built. The next integrated build
must copy the updated `fingerprint/onb.py`. Hardware qualification remains for
the user's integrated run.


## FP13: permit enumeration and capture the registration path

The supplied `fp12-run-20261024` contains **44 enforcing AVCs** denying
`a9ml_rootsh -> hwservicemanager : hwservice_manager list`. This is the
concrete explanation for query status0 with zero rows; it does not prove that
the HAL failed to register. The existing `service_manager list` permission
covers another manager/class. [AOSP AccessControl.cpp](https://android.googlesource.com/platform/system/hwservicemanager/+/refs/heads/main/AccessControl.cpp)
checks `list` against the hardware manager's process context. When denied,
[ServiceManager::list](https://android.googlesource.com/platform/system/hwservicemanager/+/2aefd64c7371cb52ea654ada5d511cc7c36f42f6%5E%21/)
returns an empty callback and successful binder transport. FP13 grants this
single enumeration permission in `rootsh-enforcing.cil`. Interface-specific
HAL add/find permissions already pass; the run supplies no fingerprint add
AVC requiring another HAL permission.

The stock registration trace was rechecked against byte-identical service,
HAL and libhidlbase files extracted from integrated12; hashes are recorded in
`/home/user/A9-build/mainline/fp13-host/stock-artifacts.json`. Addresses below
are REA/Ghidra ELF VAs +100000:

- Service `main.cfi` **104100** calls the standard V2_1 default registration
  at **1041d0**, exits on error and joins its RPC pool on success. Generated
  `IBiometricsFingerprint::registerAsService` **117980** directly calls
  `details::registerAsServiceInternal` **14e9a4**. That implementation obtains
  the manager, checks the manifest transport, requests `interfaceChain`, and
  its callback **15e960** submits the service/name/chain to the manager. Missing
  manifest transport emits `HidlServiceManagement` and returns an error. There
  is no V2_2 or custom-name substitution. The extra `vendor.sw.swfingerprint@2.0` service starts in a
  separate HAL thread (**16b4fc -> 16b6a0**).
- `getInstance` **102678** reads `/proc/fp_vendor_check`; an absent or unreadable
  file selects the default `fingerprint` module. It opens the legacy module
  and sets its notify callback. A failed legacy open still creates the HIDL
  wrapper, which is why real chip-ID proof remains mandatory.
- Legacy `fingerprint_open` **166d9c** selects API201 for the current Android
  release, obtains the instance (**16602c**) and initializes under its mutex.
  Initialize **15b884**, helper **15c0d0**, covers TA load, authentication,
  configuration, real sensor probe and initialization. It starts vendor
  threads, performs a bounded semaphore wait and TA CANCEL2004, then sets
  its initialized flag and returns. Health monitoring **15362c** takes the
  same mutex. Notify **1673f0 -> 15f3d0** is a mutex-protected callback setter.
  The observed real-ID/initialized logs support this sensor path; they alone
  do not establish the later manager registration.

The existing exact-artifact records remain in `fp12-host/rea-service-registration.json`,
`rea-hal-open-registration.json`, `rea-stock-hidden-pid.json`, and
`integ-20261024-fp8/evidence/rea-hal-postload.json`. The generated interface
record is `fp13-host/rea-interface-registration.json`, Evidence
`ev_1ac7eb96e60306fcf9c7e42c64044837f4c9bba831dade3e71dd05168635a5fa`.
`fp13-host/rea-registration-internal.json` retains the libhidl gate and add
callback, Evidence `ev_ab3b3195ce12e4c3aad829d3dd2f6abece2f2d527929a5c2160707fbd4493a00`
and `ev_81773cc5433a26a9ad70c42410cee918764d575a1f4fc4c59aa9dd688402d1d2`.
This static trace provides no reason to spoof properties, manufacture a
`/proc` node, change the service name or bypass a sensor/TA response.

The hook now retains **all emitted tags** from each positively observed HAL
PID in `fingerprint-hal-full.log`, including libhidl registration messages.
`fingerprint-service-managers.log` retains manager/VINTF history before the
capture marker and live manager-process messages, including not-allowed and
manifest errors. The live reader selects all buffers and verbose records;
it does not change producer log levels. Startup anchors preserve HAL output
before the first identity poll. Both files redact identifier, key/token and
payload fields, use mode0600, and are flushed on failure/cleanup. Buffers keep
128 initial plus4096 recent records per selected PID/manager stream, cap each
line at2048 characters, and report omitted middle records. Unemitted or already
overwritten logd records cannot be recovered. `fingerprint-hal.log` remains
the separate strict real-ID proof; diagnostic text cannot satisfy pass checks.

Native secilc compilation of the integrated12 policy passes. Host libsepol
queries reproduce enumeration denied before and allowed after, preserve HAL
add/find for both fingerprint labels and root-console add denial, and require
zero permissive domains. Receipts are `fp13-host/policy-tests.json` and
`policy-decisions.log`; only throwaway host-query copies receive the existing
functionfs capability adapter. The five focused host fixture groups cover
all-tag retention, manager history, redaction, bounded truncation, file modes,
stream cleanup and all existing registration/sensor/stability failures.
Run `python3 -I scripts/mainline/fingerprint/smoke.py` on the host. The next
integrated build must link current `rootsh-enforcing.cil` and copy current
`fingerprint/onb.py`. This change has no new standalone image. Actual live
registration remains to be qualified in the user's next integrated run.


## FP14: require one framework fingerprint sensor; retire lshal

In `fp13-run-20261024`, the PID-filtered HAL capture explicitly reports both
Sunwave2.0 and standard fingerprint2.1/default **Registered**. The failing query
returns139: the listing process crashes. This establishes a harness failure;
it is not evidence that the stock service omitted registration.

FP14 queries only `dumpsys -t 8 fingerprint` through the ordinary shell, with a
12-second host timeout. It never invokes `lshal`. Android16's
[FingerprintService dump](https://github.com/aosp-mirror/platform_frameworks_base/blob/android16-release/services/core/java/com/android/server/biometrics/sensors/fingerprint/FingerprintService.java)
emits `Dumping for sensorId: ..., provider: ...` while iterating its registered
providers' sensor properties. The same header is present in the local GSI
`services.jar`. A feature declaration, manifest entry or HAL log alone cannot
satisfy this framework check.

`FP_REGISTRATION` now requires exactly **one** well-formed framework sensor,
plus the real TA-backed chip-ID/initialized proof and the same HAL PID/birth
for30seconds with no crash. The same logical framework sensor/provider must
remain visible at the end. Logical sensor index0 is valid. Empty, duplicate,
multiple or malformed rows, permission/transport errors, dump timeouts and
nonzero command status all fail, including partial dumps that contain one
valid header before an error. Late framework initialization is polled within
the existing startup window. No enrollment/authentication call is added.

`fingerprint-framework.json` and the result JSON retain only query status,
error category, sensor count and parse state. Logical sensor IDs and raw dumps
are not exported: provider dumps may contain per-user/authenticator details.
The summary is mode0600. Existing redacted HAL and manager diagnostics remain.
The compiled integrated13 policy already permits the shell's fingerprint
service lookup, system-server Binder call/return and executable access; no
policy expansion is needed. Host evidence is under
`/home/user/A9-build/mainline/fp14-host/`.

The reported keymaster firmware-path error, ION ioctl error and efuse default
appear in the successful FP13 initialization capture. They are not treated as
registration failures. Eight saved stock kernel logs were checked, but contain
no HAL logcat tags or matching messages; this does **not** establish whether
stock HAL startup emits the same warnings. The stock-runtime comparison
remains unknown (`stock-warning-comparison.json`); no warning suppression or
speculative transport/TA/efuse change is made.

Run `python3 -I scripts/mainline/fingerprint/smoke.py` on the host. Five focused
fixture groups cover the new framework gate, command status/timeout behavior,
logical0, late visibility, changed sensor, privacy and all existing sensor,
process, crash and capture-cleanup checks. The next integrated build must copy
the updated `fingerprint/onb.py`; no APK or image change is needed for this
framework dump path. Hardware qualification remains for the user's next run.

## Display setup for fingerprint sessions (integ27)

The integrated fingerprint launcher now stages the two display firmware assets
and opens E Ink through the same vendor admission as the acceptance sweep.
`with-display.py` keeps that admission open around the entire child hook, then
closes it on return, failure or interruption. The default child remains the
registration-only `onb.py`. No PIN or enrollment operation is added.

For an interactive enrollment session, select the **outer** executable hook
with `A9_FINGERPRINT_ONB=/absolute/path/to/enrollment-hook` and set
`A9_BTRY_HOLD` to the coordinator's session budget before invoking the new
`run-fingerprint.sh`. The hold defaults to 240 seconds. The child inherits the
original B-try context, absolute owner deadline and timeout process group;
the existing owner retains cleanup and return to A. Opening only during the
registration child closes the display too early for later enrollment screens.

Integ27 fingerprint boot 131 never loaded the control template or opened any
display gate. The composer submitted frames, but the kernel rejected the
missing template 1,293 times. Sweep boot 129 staged both files and opened the
gates, producing 13 waveform-end PG telemetry records with no latch. The
same-image rerun boot 132 also loaded both files and opened the gates,
producing 14 PG observations while the user saw the acceptance app updating.
The earlier warm reboot 130 was a closed-gate health check. See
`runs/integ27-eink-20261025.md`. A new frozen runtime/launcher is required;
existing image directories and running sessions are not updated in place.

Host check: `python3 -B scripts/mainline/fingerprint/test-display.py`.
