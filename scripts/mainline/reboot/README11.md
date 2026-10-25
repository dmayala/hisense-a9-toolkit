# Integrated11: one image and two launchers

`build11.py` combines current main (78a81f3 or newer) into one boot/vendor pair.
The explicit build-time base is FP10, which already contains integrated10's
stock boot identity/FBE trace, FP10's enforcing observation grants and501bb3e's
Wi-Fi cap socket rule. The builder replaces only the userspace USB gadget HAL
with a build of5197158's framework timeout correction. Its matching acceptance
hook includes the MTP diagnostics, latest cap error tags and FP10 hook.
All other regular vendor files, their metadata, kernel/DT/ramdisk/modules and
hardware-wrapped encryption are preserved. No device access is part of building.

The output is `/home/user/A9-build/mainline/integ-20261024-11`:

- `run.sh`: owning acceptance sweep, prefixed by numeric FBE capture into
  `${A9_BTRY_LOGS}/fbe-evidence.log` (0600); sweep results in `acceptance.json`.
- `run-fingerprint.sh`: separate FP10 observation hook, same boot/vendor pair;
  fixed/tagged evidence in `fingerprint-result.json`.
- `run-fbe.sh`: retained short FBE-only alternative.

Every launcher uses its adjacent frozen b-try/helper tree and image-local
assets. The runtime includes the latest acceptance/play/fingerprint/FBE
sources, matching APKs, E Ink firmware, sleep logger, compressed platform policy
inputs and secilc executable. `A9_SECILC` selects the bundled compiler; it links
installed Nix host libraries, with no dependency on a historical build cache.
The standalone Play helper also resolves image-local firmware now. Generic
host Python/shell/debugfs, the persistent repository's device environment and
USB locking, installed credential CLI/authentication and existing recovery
loader remain normal host configuration; device-specific configuration is not
copied or printed. No old image or policy build directory is opened by a run.

Build the HAL without the legacy USB image/kernel overlay stages:

```
python3 -B scripts/mainline/usb/build.py --hal-only --binder-cache BINDER_INPUTS --out USB_STAGE
python3 -B scripts/mainline/reboot/build11.py --usb USB_STAGE
```

`BINDER_INPUTS` is the existing Binder NDK header set plus target `gsi-libs`;
the native build receipt pins those build-only inputs and every USB source.
This delivery's headers are frozen in `integ11-host/usb-inputs`, and its target
Binder library is extracted offline from the FP10 image. They are not runtime
dependencies. The builder refuses existing outputs and retains15GiB free.

Host validation checks exact ancestry and source/image/assets/runtime pins,
the rebuilt USB executable and preserved inode metadata, all unchanged vendor
files, ext4, full init and the stock offline ABL routes. The exact unchanged
101-module/6243-import ABI audit is inherited. USB core's five fake filesystem
cases include delayed MTP endpoints within the framework's2500ms budget;
acceptance, cap redaction, fingerprint and FBE fixtures use fake transports.
The bundled policy compiler/inputs and both launchers are exercised offline.
Actual packaged policy queries retain FP10/Wi-Fi grants and the denied I/O,
ptrace and block-write controls, with no permissive domains. Hardware success,
existing-key FBE reopen and zero sweep FAILs still require the owning run.
