# Offline Wi-Fi draft integration

Status (2026-10-16): Wi-Fi remains a default-off build option. The user has
authorized staged coordinator phone trials, subject to the RAM-only storage
and explicit-start gates. This round performs no device access. See
`runs/sol-wifi-plan.md` for the earlier evidence and firmware/secure-world
limits. The original diskless and Android/E Ink builds retain their defaults.

With reviewed sources already present locally:

```sh
scripts/mainline/android/build-kernel.sh --wifi kernel
scripts/mainline/android/snapshot-kernel.sh --wifi
scripts/mainline/android/build-vendor.sh --wifi --eink
scripts/mainline/android/pack-boot.sh --wifi -r /path/to/reviewed-ramdisk.cpio.gz -c /path/to/reviewed-cmdline
```

These commands only build offline artifacts. The staged coordinator procedure
is documented in the round-two test guide; do not treat a successful build
as a modem or framework test. The ordinary deployment path retains its
protected-partition guard; only the explicitly selected Wi-Fi path may accept
reviewed confined companion services.

`A9_WIFI=1` is equivalent to `--wifi` in the Android wrapper, snapshot, vendor
and boot packers. The lower-level kernel builder accepts `A9_WIFI=1` only with
`A9_ANDROID=1`. The Android wrapper refuses to fetch missing kernel sources.
Wi-Fi kernel builds use `build-7.2.10-android-wifi` and `out-android-wifi`
(or `out-android-eink-wifi` with E Ink recovery assets); snapshots default to
`android/kernel-snap-wifi`. The snapshot accepts `A9_DTBO` to select a reviewed
null overlay, otherwise reads the existing unchanged `out-android/recovery_dtbo.img`.

Vendor outputs are `vendor_b-wifi.img` or `vendor_b-eink-wifi.img`; boot packing
defaults to `boot-wifi.img` and refuses an explicit output without `-wifi.img`.
An explicit Wi-Fi snapshot destination must end in `-wifi`. Vendor staging,
fsconfig and packing directories also use the suffix. `A9_WIFI_SNAPSHOT`
selects a different checked snapshot for boot packing. Explicit kernel/DTB
inputs must still match its Wi-Fi manifest.

The generated Wi-Fi DTS includes the ordinary Android board, `a9-wifi.dtsi`,
then an absolute native-DTC include of the private MAC DTS in the kernel object
directory. `wifi/mac-dtsi.py` reads copied assets at `A9_WIFI_ASSETS` (default
`~/A9-build/mainline/sol-wifi`). No real MAC value belongs in source control.
The generated private include and final DTB/boot image contain that identity
and must remain private. The board keeps the existing memory and TLMM
reservations, adds four rail nodes and correct split `modem.mdt` naming, and
uses the isolated ath10k BDF directory `hisense/hlte556n`.

The pinned candidate voltages are representable by the mainline PM6125
descriptors: L8/L17 use the 320000 + N*8000 range, and L16/L23 use
1504000 + N*8000. L8 640000 and L17 1304000 have stock request evidence;
L16 1800000 and L23 3304000 are choices inside the measured stock limits,
including the ICNSS/regulator L23 intersection. The static shared-rail consumer review below is complete; actual operating
votes and undocumented physical sharing remain target checks. No clocks, Bluetooth pins or remoteproc memory
allocations are invented.

`A9_WIFI_PD_MAPPER=kernel` is the default for every stage. To prepare the
optional userspace alternative, set `A9_WIFI_PD_MAPPER=userspace` consistently
for kernel, snapshot, vendor and boot packing. The kernel builder merges an
explicit disable override for `QCOM_PD_MAPPER`. The snapshot rejects the
userspace mode if the kernel mapper remains enabled, records `wifi-mode` and
`wifi-manifest.json`, and the vendor/boot stages verify mode consistency.
Never run both mappers.

The snapshot copies the full Wi-Fi/MPSS/QRTR/RMTFS module dependency closure,
including `rmtfs_mem.ko` (its actual module name), into the same module directory.
It writes their topological load order to `modules.wifi`, separately from the
unchanged display `modules.load`. Thus the display init does not load or start
Wi-Fi. MPSS itself has `auto_boot=false` in the inspected SM6115 PAS descriptor;
no unsupported DT auto-boot property or automatic sysfs start is added.

With Wi-Fi enabled, `build-vendor.sh` invokes `wifi/stage-vendor.py` using the
copied asset directory and `A9_WIFI_BINARIES` (default
`~/A9-build/mainline/sol-wifi/build/android`). The stager owns exact firmware,
RAM-storage helpers and optional mapper payloads. The protected block-path
guard still runs after staging. Optional `A9_WIFI_ANDROID=1` stages the matched cached A16 stack described
in `docs/mainline-wifi-android-round2.md`; target operation is unmeasured.

## A6L reference and DIAG

The user's local read-only A6L port at
`~/A9-build/ref/hisense-a6l-lineageos` provides successful related-device
evidence. `device/hisense/a6l/hals/rootdir/etc/init/a6l-radio.rc` records the
23 September test: writable RAM copies of all four EFS stores, tqftpserv and
a QRTR DIAG router kept MPSS stable for 300 seconds and reached a Wi-Fi scan.
`tools/build-radio-userspace.sh` pins rmtfs/tqftpserv/qrtr commits; it builds
only and does not itself stage or run a modem. This is useful evidence that
RAM EFS can work on a Qualcomm modem, not proof of A9 firmware compatibility.

Its `docs/archive/radio-diag-fix-20260923.md` explains a material prerequisite:
the A6L modem starved its DIAG task without the host publishing QRTR service
4097 (0x1001). The router publishes instances 0/2/4 and discovers CMD instance
1, then performs the normal DIAG control handshake. Earlier attempts to open
unannounced DIAG RPMSG channels were wrong and triggered a modem assertion.
The related init file consequently requires a live DIAG router before MPSS
start. `docs/hals-20260924.md` credits the successful in-kernel PD mapper and
QRTR nameservice combination used here.

The A9 uses SM6115 `qcom_q6v5_pas`, not the A6L's SDM660 `qcom_q6v5_mss`.
Neither the inspected SM6115 DT nor its GLINK driver defines a dedicated
DIAG endpoint; that does not establish that A9 firmware needs no DIAG
service, because QRTR services do not require a DT node. Round two adds a separately audited, confined DIAG companion as a precaution
before MPSS start. Static evidence and related-board success cannot establish
whether the A9 firmware would crash without DIAG. Read the DIAG audit and
coordinator guide for the exact pinned source, protocol restrictions and
readiness checks; do not substitute an arbitrary DIAG prebuilt or a stub
advertisement for the control protocol. A9 firmware behavior remains a
measurement for the coordinator trial.

The later A6L `device/hisense/a6l/rom/init/init.qcom.rc` instead describes an
installed-ROM real-partition/read-only mode with an optional write-through
property. That mode is not imported: the A9 requirement continues to forbid
access to the real stores even if another device accepts such a policy.

Offline checks:

```sh
python3 tests/mainline-wifi/integration.py
```

The tests exercise mode exclusion, missing modules, separate gated load lists,
artifact collision refusal, offline missing-source behavior and the disabled
baseline. Set `A9_WIFI_TEST_KERNEL`, `A9_WIFI_TEST_CPP` and `A9_WIFI_TEST_DTC`
to existing local tools for the optional real DT compilation check. This check
uses synthetic files only and invokes no device tool. A local real Kconfig
`olddefconfig` check also retained the Wi-Fi fragment and userspace disable
override; no full kernel rebuild was performed for the integration draft.

## Private factory MAC: kernel route verified from source

The build helper `scripts/mainline/wifi/mac-dtsi.py` reads only the verified
private cached persist extraction, `private/wlan_mac.bin`, and its manifest.
It requires exactly one valid unicast `Intf0MacAddress`, emits a mode-0600
generated include outside all source checkouts, and adds
`local-mac-address` to `&wifi`. Neither persist nor an nvmem provider referring
to a real partition is mounted or opened. The MAC bytes must remain private
in the generated DTB/boot artifact; they never belong in a report or commit.

The cached Linux 7.2.10 source establishes the consumer chain:

- `drivers/net/wireless/ath/ath10k/core.c`, `ath10k_core_probe_fw()` calls
  `device_get_mac_address(ar->dev, ar->mac_addr)` before the core starts.
- `net/ethernet/eth.c`, `device_get_mac_address()` first reads the firmware
  node. `fwnode_get_mac_address()` accepts `local-mac-address` before falling
  back to nvmem. A valid generated DT address therefore needs no nvmem read.
- `drivers/net/wireless/ath/ath10k/wmi.c`, `ath10k_wmi_event_ready()` copies the
  firmware-provided address only if `ar->mac_addr` is zero.
- `ath10k_core_start()` sends the selected address with
  `ath10k_wmi_pdev_set_base_macaddr()`. Unsupported firmware handling remains
  a runtime check; this source audit does not prove RF operation.

The coordinator must compare the final wlan interface address privately with
the DT value and record only equality/failure, never the address itself.
Android may later use per-network randomized addresses; that does not change
the private factory-address provisioning route or permit live persist access.

## Review 23: direct firmware lookup and lifecycle

The kernel direct loader does not search `/vendor/firmware` by default. Merely
setting ueventd firmware directories cannot fix requests when forced userspace
fallback is disabled. Wi-Fi boot packing therefore requires one effective
`firmware_class.path=/mnt/a9-wifi/firmware` token and a command line no longer
than 511 bytes. The firmware-only prep helper runs synchronously during Wi-Fi post-fs-data,
before early-boot E Ink setup, and again **before the first Wi-Fi module**.
It starts no modules, companion servers or MPSS and checks that exact sysfs value. It refuses a missing, writable, wrong-owner
or symlinked anchor or an unreviewed existing bridge target.

The root-owned `/mnt/a9-wifi/firmware` contains fixed links for `qcom`, `ath10k`
and signed `regulatory.db`/`.p7s` to the immutable staged vendor tree. The
`hisense` link points to `/data/local/tmp/a9fw/hisense`, preserving the existing
E Ink waveform/control-template push and gate checks. This avoids using the
shell-owned `/data/local/tmp` parent as the anchor for radio firmware. The
ordinary E Ink build retains its original lookup path and behavior. Synthetic
host tests exercise actual helper idempotence, request-name resolution for both
consumers, refusal paths and the boot packer's actual effective-cmdline guard.

Companion init declarations remain disabled/oneshot, with no persistent
auto-start triggers. The staged coordinator owns one traced preparation and
one explicit MPSS start. It must verify production confinement while offline,
disable/read back remoteproc recovery, observe DIAG publication then the actual
post-start control handshake, enforce all deadlines and stop MPSS before
companion teardown. Request failures now cause daemon failure; TFTP uses real
monotonic session expiry. Neither storage daemon gets remoteproc privileges.

The coordinator accepted opaque modem/TZ storage access as a residual risk
under the user's authorization and golden backups. This is a policy decision,
not a hardware-isolation proof. Only the coordinator test script may open the
four real stores read-only for pre/post hashing on noswap RAM. A difference is
a hard failure, never permission to restore/repair a store or use it as a
server backing file. Linux process confinement remains unchanged.

## Static shared-supply review

A bounded scan of every `*-supply` reference in the cached golden
`stock-merged.dts` found these consumers of the four rail phandles:

| PM6125 rail / stock phandle | Stock consumers | Selected request | Stock compatibility |
|---|---|---|---|
| L8 / 0x75 | ICNSS CX/MX only | 640000uV | Exact ICNSS 640000uV request, within rail400000-728000 |
| L16 / 0x15 | ICNSS XO and BT XTAL | 1800000uV | Within regulator1704000-1904000 and BT1700000-1900000 |
| L17 / 0x13 | ICNSS RFA and BT core | 1304000uV | Exact BT core request, within regulator1152000-1384000 |
| L23 / 0x14 | ICNSS CH0 and BT PA | 3304000uV | Within regulator3200000-3400000 and ICNSS/BT3000000-3312000 intersection |

Reproduction uses the cached read-only DT only: locate `regulator-name` and
`phandle` on regulator-l8/l16/l17/l23, then search supply-property references to
those four exact cells. ICNSS is at lines5633-5649; BT supplies/constraints at
1109-1123; regulator constraints at1815/1983/2004/2130. No stock E Ink or UFS
supply property uses these four phandles. The opt-in mainline overlay attaches
only Wi-Fi; this round does not enable the BT controller or its UART/pins.
This clears the static DT consumer-intersection check, not a measurement of
actual operating votes or undocumented physical sharing. Continue target
thermal/power monitoring and stop on any anomaly.
