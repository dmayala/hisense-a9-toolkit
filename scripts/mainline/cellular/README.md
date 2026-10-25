# A9 cellular POC

The current integrated build defaults to the [stock radio/IMS stack](stock/README.md).
Candidate8 passed all 22 no-SIM checks on 2026-10-23: both stock radio processes
and four IMS daemons were stable, both interface slots published, and the
framework reported ABSENT in both slots. There is no SIM/carrier available;
registration, data, SMS, VoLTE and voice audio remain unqualified.

The custom AIDL implementation below is retained as a disabled fallback in that
image. Its standalone `build-images.py` path and the older findings below describe
the earlier AIDL POC; they do not start alongside stock qcrild. The reusable
no-SIM b-try hook is `stock/onb.sh`.

## Earlier custom AIDL work

Status (2026-10-20, integrated build e): UIM answers with state2/error3 (no ATR)
and no applications in both slots. Its physical-slot status1 means ABSENT.
SIM/registration must
be repaired before data/SMS testing. An IPA4.2 module/DT/stock-firmware candidate
supplies the missing SM6115 data path; mobile data remains unproven. Calls and IMS/audio are deferred.

The AIDL V4 service is ported from the A6L reference at
`ede802bcb12d3c8854b8ed0e147d6477d0ea0f9d`,
`device/hisense/a6l/radio/{hal,minradio,qmi}`. The namespaces and `a6l` property
names remain to keep the port small. Apache-2.0 notices are retained.
`minradio` originated in AOSP hardware/interfaces/radio/aidl/minradio.

No A6L EFS setup, radio launcher, partition access, rmtfs, DIAG, QMI CLI, IMS
daemon or write-through option is imported. The service uses QMI over QRTR
with the existing integrated MPSS owner. Its modem-store writes are handled
by that owner's existing RAM-copy rmtfs. Stopping the HAL does not stop MPSS.
Only the original owner closes MPSS and discards RAM stores.

The service is disabled until `vendor.a9ml.cellular=1`. It then publishes
config/default plus data, messaging, modem, network, sim and voice/slot1+slot2.
It reports real QMI state and waits for the modem services. Voice interfaces
are included for framework compatibility; no calling capability is claimed.

For the SIM failure, `a9-uim-status` defaults to read-only and queries UIM Get
Card Status and Get Slot Status and emits only numeric card/error/app/mapping
states. It never reads SIM files or prints AIDs, ICCIDs, IMSIs, ATRs or raw TLVs:

```sh
python3 scripts/mainline/cellular/build.py --uim-status-only \
  --out ~/A9-build/mainline/cellular-20261020/uim-fix
```

The coordinator runs that static Android binary in the existing MPSS session.
`state=2` is the real QMI card-error state; `error=1` means powered down,
`error=3` means no ATR, and `error=4` means voltage mismatch. The original build-d
logs contain no QMI error reason, so they cannot establish the physical cause.
The existing card parser agrees with libqmi's
[card-status layout and power requests](https://github.com/linux-mobile-broadband/libqmi/blob/main/data/qmi-service-uim.json)
and [card error enum](https://github.com/linux-mobile-broadband/libqmi/blob/main/src/libqmi-glib/qmi-enums-uim.h).

The HAL now implements explicit framework `setSimCardPower` requests using UIM
Power On/Off SIM, addressed to the physical card selected by provisioning.
It refreshes both slot caches and rechecks provisioning after power-up;
pass-through mode remains unsupported. This repairs a missing API; build-d
logs show no power request, so it is not yet evidence for the CARD_IO_ERROR cause.
No automatic power cycle, PIN attempt or slot remapping is added. Narrow
`a9-uim` log messages now report UIM request/result/state/error numbers even
while inherited payload logging stays suppressed. After the diagnostic, use
the exact returned error to choose the next physical/power/mapping fix.

Stock REA now establishes that no-ATR/powered-down/possibly-removed cards map
to Android ABSENT, not CARD_IO_ERROR. Both radio SIM and radio config now use
that conversion, while diagnostics retain the raw QMI state. This corrects the
framework report; it does not claim that the inserted SIM has become readable.
Normal power-up now explicitly includes telecom mode0, matching stock's UIM
request encoding. The coordinator may run `a9-uim-status --power-up 1` (or2) in
the existing MPSS/RAM-copy session: it sends one power-up for that physical
slot, waits two seconds, and reports status again. No power-down or automatic
retry is performed. This option changes SIM power; invocation without it is
still read-only. Stock addresses, hashes and REA evidence are in
[uim-no-atr.md](uim-no-atr.md). Readiness/data/SMS remain unproven.

Changes from the reference:

- Native C++20 NDK build with generated frozen Android 16 AIDL V4 sources.
  It uses the platform libc++ ABI and existing Cuttlefish vendor libbase/libc++.
- Replace libutils-only includes with standard mutex and CLOCK_BOOTTIME.
- Suppress inherited verbose and QMI payload logs; redact fatal messages that
  could include cell identity or numeric SIM contents. HAL log severity is
  FATAL; framework logs/dumps can still contain private SIM/network data.
- A9 service/init/VINTF/feature declarations and opt-in startup.

Build on the host:

```sh
cd /home/user/A9-wt/fix-cellular
python3 scripts/mainline/cellular/build.py
python3 scripts/mainline/cellular/build-images.py --ipa --integration-repo /home/user/A9
```

Outputs default to `/home/user/A9-build/mainline/cellular-20261020`.
The image builder uses main's existing `build-vendor.sh --refresh-integrated
--overlay`, preserving its RAM-copy modem storage and module setup. It adds
the radio overlay and sets the new binary's SELinux exec label. `--ipa` builds only the small external IPA module against existing R4 objects
and replaces only the boot DTB. `images/boot.img` then selects that candidate;
omitting `--ipa` keeps the original R4 boot for SIM/SMS-only testing. The vendor
image contains private modem seeds and must stay local.
For the combined image, use `scripts/mainline/build-integrated.sh --cellular`.
That calls this builder with `--ipa --stage-only --boot <cumulative boot>
--overlay <shared overlay>`, builds no separate vendor image, and lets the
integrated recipe assemble the final boot/vendor/snapshot once.
`snapshot/` follows the existing integrated snapshot format, with the matching
DTB and IPA/rmnet modules. Use `A9_WIFI_SNAPSHOT=<output>/snapshot` with the
coordinator's existing recipe. The boot uses its null `recovery_dtbo.img`, as
does R4; the stock DTBO cannot be applied to either mainline DTB. This is the
public R4 DT base; any private Wi-Fi MAC injection remains with the coordinator.

Coordinator phone steps, inside the existing owned slot-B integrated session:

1. Use the existing integrated boot/flash recipe with the delivered vendor
   and its matching delivered boot. Do not replace an active owner's vendor image.
   Keep the proven RAM-copy rmtfs and MPSS startup path. This HAL does not
   create or own that session and supplies no modem-start command.
2. Once MPSS has started, run `setprop vendor.a9ml.cellular 1` in the existing
   root device shell. Check `getprop init.svc.vendor.radio-a9` and
   `getprop gsm.sim.state`. Expect `running` and a real SIM state such as
   `READY` or `PIN_REQUIRED`; do not script PIN/PUK guesses.
3. Inspect `dumpsys telephony.registry` privately. Success is a real card,
   SIM subscription loaded, and voice/data registration IN_SERVICE. A safe
   summary is `dumpsys telephony.registry | grep -oE
   'mVoiceRegState=[0-9]+|mDataRegState=[0-9]+|mDataConnectionState=[0-9]+'`.
   Return only sanitized service/data states; no raw SIM dumps or logs.
4. Send and receive a short test SMS through the Messaging app with the user
   choosing the recipient. Confirm delivery both ways without saving phone
   numbers or message bodies in this repository. LTE SMS may depend on
   carrier IMS support; IMS is not supplied here, so this is unproven.
5. With the `--ipa` boot/vendor, the opt-in prepare action loads IPA/rmnet.
   First check for `/sys/class/net/rmnet_ipa0` and the IPA driver bind; retain
   exact kernel errors privately if the parent is missing. Select the SIM/APN
   in Android, turn Wi-Fi off with
   `svc wifi disable`, and enable data with `svc data enable`. For the actual
   allocated `rmnet_data<N>` interface, run
   `ping -I rmnet_data0 -c 3 example.com >/dev/null 2>&1; echo $?`.
   Record only success/failure, then restore Wi-Fi as appropriate for the
   joint test. A modem-only WDS activation is not working mobile data.
6. `setprop vendor.a9ml.cellular 0` stops only this HAL. Finish with the
   existing owner cleanup/restore path, which discards modem RAM stores.

Kernel findings from the exact R4 snapshot:

- `CONFIG_QCOM_IPA=m`, `CONFIG_RMNET=m`, `CONFIG_QRTR=m`,
  `CONFIG_QRTR_SMD=m`, `CONFIG_QCOM_Q6V5_PAS=m`.
- `sm6115.dtsi` has reserved IPA firmware/GSI memory but no IPA device node.
- `drivers/net/ipa/ipa_main.c` has no SM6115 match. The v4.2 table serves
  sc7180; treating it as an A9 match without deriving the hardware resources,
  endpoint assignments, GSI channels and memory layout is unverified.
- The A6L used a separate IPA v2.6L driver. This candidate replaces its
  framing defaults with the MAPv4 settings required by mainline IPA4.2.

The optional IPA candidate now adds the missing node/match, stock firmware and
module closure, using the existing v4.2 table after comparing downstream
endpoints/resources/SRAM. It overrides the sc7180 IMEM address with the A9's
stock IMEM mapping and selects MAPv4 framing. Derivation and probe expectations
are in [kernel/README.md](kernel/README.md). First phone bind, firmware/PAS/QMI
compatibility and actual mobile data are still pending. No full B8 parity claim.
