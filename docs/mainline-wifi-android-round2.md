# Android Wi-Fi first-run payload

2026-10-16. Offline implementation; no Android ARM64 binary was executed and
no device was accessed. This is a staged experiment using the user's requested
matched Android 16 Cuttlefish prebuilts, not a claim of A9 framework Wi-Fi parity.

`A9_WIFI_ANDROID=1 scripts/mainline/android/build-vendor.sh --wifi --eink`
adds a separate `-wifi-android` vendor image. The ordinary build and radio-only
`--wifi` image remain unchanged by default. No Android source tree or network
fetch is required. The extracted target-files cache must be the exact CI build
15581820 recorded in `scripts/mainline/wifi/android/prebuilts.lock.json`.

The stage helper verifies every selected ELF's SHA256/size and ARM64 ELF layout,
then checks its complete `DT_NEEDED` closure against the process-local libraries
and six existing Android vendor-public libraries. It copies:

- `com.android.hardware.wifi/root/bin/hw/android.hardware.wifi-service`, its
  `libwifi-hal.so`, `libnl.so`, AIDL libraries and other private dependencies;
- `com.google.cf.wpa_supplicant/root/bin/hw/{wpa_supplicant,hostapd}`, their
  AIDL, crypto, keystore and other private dependencies;
- the actual packaged VINTF fragments: IWifi/default version 3,
  ISupplicant/default version 4 and IHostapd/default version 3. Their backing
  library filenames V4/V5/V3 are deliberately preserved; a filename is not a
  replacement for the published manifest version.

The `com.google.cf.wifi` setup package is excluded. Its `setup_wifi`,
`init.wifi`, interface renamer, `mac80211_create_radios` and Cuttlefish-specific
libraries are not needed on ath10k and are not staged. No Wi-Fi APEX is activated:
the binaries are installed at standard `/vendor/bin/hw` paths, which already have
the appropriate HAL exec labels in the Cuttlefish vendor contexts. Each service
sets `LD_LIBRARY_PATH` to its own `/vendor/lib64/a9-wifi/{hal,supplicant}` directory
and `/vendor/lib64`. This preserves process-local library versions and avoids
overwriting the base vendor's different `libcrypto.so`. Actual target linker
acceptance remains a coordinator stop condition; host ELF closure is not a
runtime linker test.

The actual supplicant's configuration-path behavior was also checked statically.
`ensureConfigFileExists` at `0x431ed0` resolves the template suffix
`/etc/wifi/wpa_supplicant.conf` through `resolveVendorConfPath` at `0x432200`.
That helper reads GetExecutablePath, takes the prefix through the first slash
after the six-character `/apex/` prefix length, and appends the configuration
suffix. For the staged `/vendor/bin/hw/wpa_supplicant`, this yields
`/vendor/etc/wifi/wpa_supplicant.conf`. Thus flattening does not strand the
template at an absent APEX path. Supplicant SHA256 is
`0fd935d19269bdbfd4c494742e63a79c8a6f39b44dee39c7d7fbd03c9e2e5d9f`;
REA evidence `ev_cbf2071642564d71f0b504462b21fd8985dec4219061875fd6651ba51023266c`
and `ev_166dc5042b2c4e1435424e613ab2333d768a9bb9b5252816e6ed70fa6335d171`.

The HAL, supplicant and hostapd have the standard
`hal_wifi_default`, `hal_wifi_supplicant_default` and
`hal_wifi_hostapd_default` domains. They have NET_ADMIN/NET_RAW, no SYS_MODULE,
and no partition handles. All three are disabled/oneshot. Lazy AIDL interface
starts go through a fixed-role shell entry wrapper, which refuses unless the
Android build flag, nonpersistent ready gate and wlan0 phy are present. Hostapd
also requires its separate AP gate. No caller-selected executable/path/arguments
are accepted. This permits normal framework interface stop/start after the
coordinator opens the gate. After direct driver/scan checks, the coordinator
sets nonpersistent `vendor.a9ml.wifi.android.ready=1`, which starts
`vendor.a9ml_wifi_hal` and `wpa_supplicant`. Setting it to zero stops hostapd,
supplicant, then the HAL before MPSS shutdown. Hostapd additionally needs the
separate `vendor.a9ml.wifi.android.ap=1` gate; the first STA run leaves it unset.
There is no automatic MPSS start or restart.

`-Dnl80211` selects the supplicant driver. Templates disable P2P and scan
offload; no QCA daemon, cnss daemon, firmware-path driver command or proprietary
qcacld driver is introduced. The A6L's disconnect/magic_pkt WoWLAN settings are
retained as an experiment because its WLAN PD crashed through its no-WoWLAN
suspend path. A9 suspend tolerance is unmeasured. The AP template is explanatory
and never passed by init: the AIDL hostapd service receives framework-generated
configuration. There are no committed credentials or country assumptions.

The existing factory-MAC route remains private generated DT
`local-mac-address` on `&wifi`. The source is the verified cached persist copy;
neither persist nor any real EFS partition is opened by Android provisioning.
The generated include/DTB/image are private build artifacts. Android's normal
per-network randomized MAC policy is separate from provisioning the factory
address; this change does not disable that policy. Wi-Fi Direct feature XML is
still present solely for the existing GSI DisplayManager workaround and is not
evidence that P2P works.

## What static analysis actually establishes

The shipped HAL service SHA256
`73c37d8e5ffeb468fcea4410d21b5137e13321b7815d9b81dee293301e7215a1`
contains the vendor-HAL descriptor directory and initializer symbol
(REA `ev_e87d2345d68f157860c01c6918ead28eac35c2c152cf47379709f8ba66a534f0`).
However the bundled `libwifi-hal.so`, SHA256
`761f5c072df9d70334be6f6538e4c9bd5f5a1207c0dd8d66c1831ec6e25fc0fd`,
initializes the emulator function table successfully; it is not the fallback
used by the A6L's dynamic adapter
(`ev_51feebc6826bdea3afb7b99bcc391b555ee3a2668ede8ca5ec9a7f3555664b26`).
No A6L legacy HAL with an unverified function-table ABI is injected.

REA/Ghidra 12.1.4 inspected only immutable host files. Relevant observations:

| Function | Static result | REA evidence |
|---|---|---|
| Info constructor | Tests `/sys/class/net/` plus fixed names including `wlan0`; builds interface list | `ev_8466da3b1b4a8fed2b122fcf98062955d2a95d333dc7490800e7d3fd19bbcd7a` |
| Interface::init | Uses `if_nametoindex`; no hwsim device check in this function | `ev_9159d36d5e34712767a0ca31763fee7d5b0cd917a09bb0ddc3c3b4da645471c7` |
| Netlink::init | Opens/binds AF_NETLINK, SOCK_DGRAM, NETLINK_ROUTE | `ev_e310e355cde2eaf4daa832a91f598af0ed01c7cdf00536b42c46e7088c6a6a37` |
| wifi_load_driver | Sets an internal flag; no module/sysfs IO | `ev_01c6c2a870aa47dda88c997e7c78aab485c716106598438abbc8267ca34d1480` |
| getSupportedFeatureSet | Reports zero | `ev_1961437b4c589018b7a6f45fcd69a92e791c273d598645edd8b1ec81e6442c08` |
| setCountryCode | Returns success without applying a regulatory hint | `ev_d7d6e61678ef6822ebde94948249891281ca02964fa7c5f30353f2574bd23563` |
| getPacketFilterCapabilities | Reports APF 6000 / 4096 bytes; this is emulator capability, not ath10k offload | `ev_0791fd6c10e3b97e4937afc14164b19c2342a272f05835cffe00b2b3e22e6ea4` |
| getValidChannels | BG only; reports an entry count but copies that many bytes rather than integers | `ev_267439bb5ef934adf11b506e006bd8298dd63ccacd297ded102ef81d3f10eeed` |

These observations support an attended, gated framework bring-up experiment
after direct `iw` scanning. They do not establish a complete ath10k HAL. The
country no-op, APF report and channel-list bug remain limitations of the user's
preferred unchanged Cuttlefish binary. Kernel cfg80211/signed regulatory.db must
enforce the world/current regulatory domain; never infer legal channels from
this HAL's return value. A connected, suspend or AP experiment needs separate
validation of those limitations. A proper later source-built plain-nl80211
adapter remains a route to removing them.

## Labels and offline checks

Every radio-only or Android-enabled Wi-Fi image adds dedicated
`a9_wifi_radio`, `a9_wifi_radio_exec`, `a9_wifi_private_file`,
`a9_wifi_ram_file` and `a9_wifi_rmtfs_device` labels. The radio helpers no longer
borrow the unrelated phhsu domain. RAM file/dir type transitions and standard
supplicant/hostapd data labels are retained. These are correct identities in the
existing permissive build; an enforcing policy is not claimed, and the native
mount/FD/seccomp boundary remains mandatory.

`python3 -I tests/mainline-wifi/android.py` passes ten host checks using actual
cached prebuilts: SHA/ELF/dependency closure, VINTF versions, private libcrypto
separation, tamper rejection before writes, output symlink/hardlink rejection,
source/golden output refusal, and disabled init gating. The actual wrapper runs
on the host with only its fixed absolute device paths replaced by synthetic
getprop/phy/executable fixtures: missing/wrong flags, missing phy, AP closed and
unknown/extra roles reject without exec; valid roles pass fixed arguments.
No target ARM64 binary is executed. The actual GSI platform,
system_ext/product mappings and Cuttlefish vendor CIL plus these labels compile
with the cached AOSP secilc, policy version 30 (815712 bytes). This does not test
target service execution, binder registration, scans, association or suspend.

Credit: the read-only Hisense A6L mainline project
[PaddleStroke/hisense-a6l-lineageos](https://github.com/PaddleStroke/hisense-a6l-lineageos),
commit `ede802bcb12d3c8854b8ed0e147d6477d0ea0f9d`, supplied the plain-nl80211
integration model, init/data layout and suspend considerations. Its source-built
HAL adaptation differs from the unchanged Cuttlefish first-run route chosen
here. No online page was fetched for this work.
