# Play smoke test

`onb.py` is the passing row28 run5 host hook, promoted from the 2026-10-23
run. It runs after slot B reaches `boot_completed`, under the existing b-try
owner. It connects the test Wi-Fi, requires validated Wi-Fi internet, requests
one forced Google check-in, waits up to 900 seconds, and opens Play. It then
closes the E Ink gates, disconnects, disables Wi-Fi and forgets the exact test
network. The b-try owner retains session teardown and return-to-A ownership.

The hook prints nine fixed PASS/FAIL criteria. Play accepts either its signed-out
`SIGN_IN` screen or `HOME` (Games/Apps). Screen diagnostics contain only fixed
class/label enums; errors take precedence. UI XML and command output stay in
memory. The host desk camera saves `play.jpg` in `A9_BTRY_LOGS` while Play is open;
this private runtime evidence must not be committed.

Bluetooth is explicitly enabled before connecting Wi-Fi. The hook checks both
the adapter's enabled/ON state and a crash-free observation window, sampling
framework recovery counters and signatures of app/native crash events throughout
the waits and again before cleanup. The BT window lasts at least60 seconds.
A crash, log rollover, counter reset or failed observation stays failed.
`BLUETOOTH_ENABLED` and `BLUETOOTH_NO_CRASH` are independent verdicts.

Current system crash windows are checked even when UIAutomator cannot dump XML.
A detected dialog produces a fixed `WARN PLAY_SYSTEM_CRASH_DIALOG` label, up to
two Back dismissal attempts, and another dump attempt. Any observed dialog keeps
`NO_SYSTEM_CRASH_DIALOG` failed even if Play later reaches its sign-in page.
Neither raw window titles nor XML/account text are logged. Bluetooth crash
dialogs also fail the Bluetooth crash criterion.

## Prerequisites and use

Use the GApps LOS system with the integrated mainline Wi-Fi vendor and an
E Ink-enabled boot (`a9ml.eink=1`). GApps packages and permissions remain in the
system image; this vendor change does not install Google APKs. The builder
replaces AOSP's network-location exclusion with the checked-in empty
`aosp_excluded_hardware.xml`; `handheld_core_hardware.xml` already declares the
feature and GmsCore supplies its provider. Both fresh vendor builds and normal
integrated refreshes apply this replacement. An explicit `--overlay-only`
refresh still applies only its requested overlay.

The mainline GLES selector already reports ES 3.2 for admitted Mesa and ES 3.1
for ANGLE/SwiftShader. Its write-once property must be set after driver selection;
do not add an unconditional ES 3.2 property to the vendor image.

Host dependencies are Python 3, curl, the existing `onb-root.py` helper, and the
established Wi-Fi release tool pin at
`/home/user/A9-build/mainline/wifi-android/s2-87k/release.json` (`tools.op`).
Authentication uses `/run/agenix/op-token`. The hook reads only the network-name
and wireless-password fields under `op://vault/item/field`, at runtime.
Credentials travel through memory and shell stdin, never files, logs or host
arguments. The native root helper receives no credentials.

The established private E Ink firmware files must be available under
`/home/user/A9-build/mainline/firmware/hisense/`. The hook uses the vendor's
admission/watch path and the proven 100 ms settling interval. The host desk
camera service must be available for the optional visual evidence.

For the owning coordinator's next authorized run, from the repository root:

```sh
A9_BTRY_ONB="$PWD/scripts/mainline/play/onb.py" A9_BTRY_HOLD=2100 \
  /home/user/A9-build/mainline/b-try.sh BOOT.img VENDOR.img
```

b-try supplies `ADB`, `A9_BTRY_LOGS` and `A9_BTRY_IN_HOOK=1`. Direct execution
without that context refuses before phone access. Importing the module does
not run the hook. Choose the admitted boot/vendor pair through the usual
coordinator workflow; the hook does not build or flash images.

## Check-in evidence and host verification

Initial check-in before Wi-Fi can fail DNS and schedule a roughly two-hour
backoff. After validation the hook sends one targeted CHECKIN broadcast with
`checkin_source_force=true` and truthful shell attribution. Broadcast acceptance
or task scheduling does not count as completion. PASS requires an actual
check-in completion log or an existing nonzero GSF registration length read
inside native SQLite. No identifier value is returned. SQLite is optional;
the log predicate also works on images without it.

Row28 run5 passed all six criteria, including the Play sign-in page on the
glass. The additional BT/dialog checks address the later integrated-image
regression; runtime qualification of that correction remains separate. The
root-helper path resolves relative to this checkout. Source-only checks use
fake hook operations and synthetic UI/log text:

```sh
python3 -B tests/mainline-android/test-play-smoke.py
```
