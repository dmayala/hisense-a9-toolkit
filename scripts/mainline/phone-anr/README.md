# Phone startup ANRs

The integ 26/integ 27r replacement phone process inherits the dead persistent
instance's pending bind timeout. Cancel messages 82/83 before restarting the
reused record, while keeping the successor's ordinary startup deadline and
provider timeout cleanup. This does not fix or suppress the initial
SafetyCenter broadcast ANR; its Binder server-side owner is not retained.

The source patch shows the Android 16 change. `PatchServices.java` applies those
two calls to the pinned shipped v8 framework using dexlib2, preserving branch
labels and exception ranges. Unknown or already patched framework inputs fail.
No TeleService APK, radio/IMS policy, SIM checks or ANR timeout value changes.

Host-only standalone build:

```sh
python3 -B scripts/mainline/phone-anr/build.py \
  --system /path/to/current-v8-system.img --out /path/to/fresh-output
python3 -B scripts/mainline/phone-anr/test.py /path/to/fresh-output
python3 -B scripts/mainline/phone-anr/test-integration.py
```

Both integrated builders accept `--integ-system /path/to/current-v8-system.img`
(alias `--phone-anr-system`), or `A9_INTEG_SYSTEM=/path/to/current-v8-system.img`.
Use the original v8 input, not an already patched output. This adds the fixed
`system.img`, its SHA sidecar and manifest digest to the output. Release builds
must select the same input with `--release-system`; the fixed copy then goes
through the existing release finalizer. Default boot/vendor builds omit SYSTEM.

For the incremental integ builder, generated `run.sh` and `run-fingerprint.sh`
automatically pass the built SYSTEM to B-try. The generic acceptance launcher
uses `integrated-build.json`; both clear an inherited SYSTEM when their package
omits it. Direct B-try accepts `boot.img vendor.img system.img` or
`A9_BTRY_SYSTEM=/path/to/system.img`. Existing boot/vendor-only invocations work.

B-try runs the existing `flash-test-b.sh` check-only path: SHA, raw ext4,
platform certificate/SDK/patch compatibility and super space. These checks also
apply with RAM or explicitly fresh B data. The integ package freezes those
check scripts with its runtime. B-try uses slot A fastbootd to flash the explicit
`system_b` target, then verifies its image-length SHA in recovery before vendor,
any existing requested data format, or B boot. Slot A image partitions are not
written. Shared `/data` still follows the existing B-try format/return-A policy.
The new transport path has host fake-phone coverage; physical execution remains
for the owning coordinator. Enable `A9_INTEG_SYSTEM` on the normal integ build,
then use that output's launcher; no separate manual SYSTEM flash is needed.

No source image is edited. The private copy retains the jar's mode, ownership,
SELinux label and nanosecond timestamps, removes its stale preopt companions,
verifies jar readback and runs read-only e2fsck. A 15 GiB free-space floor remains.

Evidence, exact offsets and remaining gap: `runs/phone-anr-integ 26-27-20261025.md`.
