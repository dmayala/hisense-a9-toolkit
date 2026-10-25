# Integrated9 host delivery

`build9.py` assembles `/home/user/A9-build/mainline/integ-20261024-9` on
agent's image8 through its FP9 derivative. The same image retains image8's
FBE trace and sleep logger, FP9's init interface discovery, all 101 kernel
modules, and the existing boot/kernel/DT. No phone operation is part of this
build. A future owning session can use the image's frozen `run-sweep.sh`.

The user goal of zero FAILs is **not yet demonstrated**. This is a host-tested
candidate, not a new acceptance result. Real wrapped FBE remains agent's
01_boot/01_restart/29 task; image8 explicitly contains diagnostics rather than
a proven repair. This delivery does not reinterpret the real-data failure.

Changes merged individually after host tests:

| Area | Change and evidence | Limit |
|---|---|---|
| Wi-Fi | Read native `/sys/class/net/wlan0/ifindex` instead of making shell rtnetlink access a prerequisite. Owner readiness and the real 20 dBm cap remain required before credential use. | The retained reboot89 log has a working `wlan0` and scans, whereas the initial feature boot88 is absent. This establishes a bad inference in the old gate, not a proven initial-boot kernel failure. Actual Android `ip` accepts the old command syntax. |
| Mic | Map the AOSP primary HAL's default attached input address PCM0 to MM2/PCM1 only for 48 kHz mono S16. Reject unsupported SCO rates/formats. Keep the existing main-mic route and gain84. Log the first read entry/return without samples. | REA of the shipped HAL confirms its missing/unparsed-address fallback is card0/device0, and the adapter previously routed only input device1. Initial boot88's selected address/read boundary was not retained, so this is a candidate fix, not a demonstrated root cause of the899 empty reads. |
| MTP | Capture normal `svc usb getFunctions` output from stderr, preserve setter status/exception and lock state. | [AOSP UsbCommand](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android16-release/cmds/svc/src/com/android/commands/svc/UsbCommand.java) prints the getter on System.err. The old hook discarded it. The actual same-device MTP descriptors/session, applied state, ADB health and reset refusal are still mandatory; fixing the getter alone does not establish why the earlier device count was0. |
| Stability | Compare each valid snapshot with the preceding snapshot; count actual signature transitions once and report which of tombstones/ANRs/crash events changed. Keep startup ANRs as failures. | The old238 observations recounted a persistent difference from the first baseline on every poll. They do not mean238 crashes. Saved boot89 has11 explicit phone `am_anr` records in logcat.prev,87 slot1 getService waits there, and16 slot2 waits in the later ring. Missing earlier rings prevent reconstructing all14 ANRs or exact old signature transitions. |
| Radio | Start declared QCRIL slot2 using `ro.vendor.a6l.ril.slots=2`, matching board/VINTF configuration. Retain stock-ready, cellular opt-in, boot-completed and existing-owner-serving gates and all teardown actions. | Boot89 never starts qcrild2 and later phone logs still wait for slot2. Slot1 also waits until qcrild starts at kernel3908–3910 (~242s); the existing owner's boot-completed admission is retained. Startup ANRs may remain until that earlier dependency is resolved. |
| SELinux | Add explicit target/class permissions for the observed root-console closure and remove its permissive exception. Link vendor CIL, binary and matching platform hash sidecars together. | Actual host policy queries:67 intended permissions allowed,65 denied in the old policy; shell dynamic transition into rootsh and reading its protected TEE property remain denied. New compiled permissive map is empty. Inherited `-N` development/Treble exceptions remain; this is not release-policy certification. |
| Camera applicability | [Stock modules and DT](../cameras/STOCK-CAPABILITIES.md) prove the front lacks an actuator/flash; report front N/A only when its camera exists and those capabilities are absent. | Rear has DW9714P and a flash. Its missing mainline controls remain NOT_RUN with `stock_supported=1, implementation_missing=1`; never N/A or PASS. |
| Checkin/Play | The existing real Wi-Fi validation then checkin/install/launch path runs after the corrected netdevice gate. | Not executed on the host. No fabricated network, checkin or Play success. |

## Encoder limitation

The current encoder is not simply omitted from XML. The SM6115 resources
deliberately have no encoder child and the Codec2 store's existing patch
advertises device-backed decoders only. The A9's accepted OEM-signed firmware
is6.0.39. Its older HFI compatibility path was decode-only; the upstream
[firmware discussion](https://lkml.iu.edu/hypermail/linux/kernel/2508.0/05851.html)
explicitly discusses that limitation. Upstream selected newer firmware rather
than retaining this path. This device rejected the newer public6.0.55 signing
metadata in the earlier Venus bring-up; see [video evidence](../video/README.md).

There is no evidence-backed working mainline encoder to expose yet. Advertising
`c2.v4l2.avc.encoder` in XML alone would invent availability. Row18 remains
NOT_RUN with `hardware_available=0`; it is not hardware N/A. A compatible signed
firmware or an implemented old-firmware encoder protocol remains required.

## Host checks and local evidence

The build checks exact image8/FP9 ancestry, image/source hashes, capture tests,
policy binary/tests, and all acceptance APK/source pins before packaging. It
preserves every vendor file outside the explicit overlay, checks inode modes,
ownership, timestamps and SELinux labels, runs ext4 and complete init validation
and the ABL image check, and retains the byte-identical module/CRC audit.

Local evidence: `A9-build/mainline/integ9-host/` contains the compiled policy,
67 positive/2 negative policy decisions, audio Android/QEMU route tests, and
REA snapshots of the shipped primary HAL and adapter. `integ9-mtp-test.log`,
`integ9-acceptance-test.log` and the final candidate's build logs record host
checks. Firmware, stock camera module payloads, keys, credentials, identifiers,
photos and audio are not committed or exported by these changes.

Five acceptance fixture groups include secret-free output, MTP setter/getter
behavior, real-data failure retention, unsupported front versus missing rear
features, startup ANR retention, and no recounting of a stable changed signature.
Five actual Android arm64 adapter fixture groups cover input alias/format/SCO
refusal, read errno preservation, concurrent playback, unchanged route/gain,
output cleanup and control/open failures. Five stock-radio groups preserve
source/hash/account/policy checks and the second-slot owner's readiness gates.
