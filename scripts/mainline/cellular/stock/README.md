# Stock radio and IMS integration

The normal `scripts/mainline/build-integrated.sh` path includes this stack by
default. `--no-cellular` or `--without cellular` skips cellular rebuilding and
this final packaging step; as before, an inherited base can retain its features.
The existing cellular step supplies the custom AIDL binary as a disabled fallback
and builds the matching IPA/rmnet prerequisites. After enforcing/data-mode steps,
`stock/integrate.py` adds the stock userspace to a copy of that vendor. It keeps
the cumulative boot image, modules and unrelated vendor content.

The imported configuration is candidate8, whose 2026-10-23 phone run passed all
22 no-SIM checks over a 211-second observation window. It includes qcrild for
two slots, the private 159-file stock ELF closure, stock HIDL VINTF declarations,
four IMS daemons, 134 MCFG assets, data configuration and the QCRIL database.
`netmgrd` is retained disabled pending the downstream IPA/rmnet-control ABI work.
Voice-call audio routing remains separate. A SIM/carrier is still needed to
qualify registration, mobile data, SMS and VoLTE.

The sources retain each observed startup repair: batched profile metadata work,
180-second preparation limit, populated vendor account names without blank
records, private-library `noatsecure`, the RTP-only getline errno compatibility
shim, qcrild generic-netlink/database permissions and the stock
`/dev/socket/qmux_radio` parent (radio:radio,2770). No stock ELF is patched.
The custom AIDL executable stays in the image; its init and VINTF fragments are
suppressed together. Its original fragments remain under `../vendor` for an
explicit fallback change; do not start both stacks.

Proprietary firmware/libraries/database inputs stay local. `assets.json` pins
only the 306 required files and hashes, excluding device identifiers and EFS
seeds. Set `A9_STOCK_RADIO_ASSETS` to an unpacked vendor tree containing those
exact assets. The local default is the retained
`~/A9-build/mainline/modem-parity-20261023/candidate8/readback`. These are the
phone's golden vendor closure, stock platform libraries and stock MCFG assets;
no network fetch or device extraction occurs in this packager. The shim builds
from `compat/rtp-getline.c` using `ANDROID_NDK_HOME` (or `--ndk`). Policy is merged
into the incoming vendor and compiled against the integrated build's selected
GSI policy cache; unrelated policy additions and platform identity sidecars
are preserved. Reapplying known radio fragments does not duplicate declarations.

Runtime startup is requested by vendor init at boot instead of a host hook.
Firmware preparation, the existing one-shot MPSS owner start, and synchronous
ordinary radio-data preparation share the same post-fs-data file, in that order,
before queued zygote-start. Prepared-ready plus explicit cellular opt-in starts
both stock QCRIL instances; it does not depend on WLAN serving/Android-ready.
Stock starts QCRIL in class main even with no SIM, and its HIDL services are
not lazy. This keeps the declared Telephony endpoints present when WLAN or the
bounded modem session is unavailable. IMS companions retain serving/Android-ready
release and stop on owner end/failure/readiness revocation. Explicit cellular
opt-out and shutdown still stop all clients. See [startup evidence](STARTUP.md).

The preparation helper copies packaged assets only into ordinary `/data/vendor`
runtime directories, retaining checksum, symlink and module checks. It does not
touch modemst1, modemst2, fsg or persist. The existing owner alone manages MPSS
and RAM-copy rmtfs; QCRIL gains no protected-store or physical-block write
permissions. Runtime data-preparation failure still prevents HAL startup and
fails the boot qualification; no unavailable HAL is synthesized.

The reusable `onb.sh` and adjacent native/report files are an `A9_BTRY_ONB` hook
for an authorized b-try owner. The runner supplies `ADB` and `A9_BTRY_LOGS`;
native commands go through the repository's `boot-deadline/onb-root.py` using
stdin. Framework output is reduced on the device before reaching host logs.
Output is written under `modem-stock/`, with a final PASS/FAIL report even after
a failed stage. It requests the existing owner opt-in, enables Wi-Fi with the
existing cap, waits for preparation, then observes six clients for180 seconds.
It checks unchanged PID/birth and running state, both IRadio1.4 and IImsRadio
slots, unchanged crash counts, Wi-Fi enabled and a silent audio-output-open
check. Both `gsm.sim.state` and current indexed `dumpsys isub` SIM states must
be ABSENT in both slots. Telephony registry section booleans are diagnostic;
service/registration state never substitutes for SIM presence. Cleanup revokes
cellular opt-in and confirms clients stopped before returning to the runner.
The hook contains no call, dial or SMS operation and prints no network identity.

For a separately authorized future run, set
`A9_BTRY_ONB=/home/user/A9/scripts/mainline/cellular/stock/onb.sh` on the normal
b-try command using the matching deadline boot/vendor pair. This documentation
does not authorize or perform a phone run.

Source-only smoke check (no builds or device commands):

```sh
python3 -I tests/mainline-cellular/test-stock-integration.py
```

The integration commit was checked against the qualified candidate8 source and
local asset hashes. No new image, shim or policy was built, and no phone command
was executed during repository integration. A future integrated image still
needs its usual boot/run verification.
