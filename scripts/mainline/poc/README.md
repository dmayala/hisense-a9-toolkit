# E Ink + Wi-Fi proof of concept (2026-10-20)

Status: **POC, not reviewed.** Built after the user waived reviews and the host safety harness
("no safety checks or review agents, review after a working poc"). Paths are absolute to this
host (`~/A9-build/mainline/...`); the scripts were run from `~/A9-build/mainline/{wifi-poc,eink-wifi-poc}`.

## Result (measured)

- Wi-Fi only (87k image: `wifi-android/out/boot-demo-wifi.img` + `s2-87d` vendor): framework
  Wi-Fi COMPLETED on 2432 MHz, VALIDATED, HTTP 204; 23/23 periodic checks.
- E Ink + Wi-Fi on the integrated image (R4 `boot.img` 02571e36 / `vendor.img` 785ab053):
  **30-minute joint soak 65/65** (HTTP 204 + forced redraw every 30 s, 6305 frames,
  rail_fault 0, bins 9-10 with 0-13 admitted, pg_settle_ms 100).
- Unmodified integrated boot (no MAC in the DT) with the runtime factory MAC
  (`../wifi/session/a9ml-wifi-session.sh` reads persist `wlan_mac.bin` Intf0 read-only;
  `../wifi/coordinator-target.sh` applies and proves it): connected, wlan0 = factory MAC.
- The 87k kernel has no PG settle: rail fault latched after ~4 min and again on reopen.

## What the scripts do

| Script | Role |
|---|---|
| `run.py` | integrated images via the R4 probe environment: wrapper only flashes/boots (gates closed), is then stopped; `keeper.sh` feeds the vendor dead-man; gates, ADSP stop, Wi-Fi, connect |
| `chain-int-mac.sh` | slot A -> check -> `run.py --write` -> `soak2.sh` |
| `poc-generic.sh` | flash the unmodified integrated `boot_b` from the bootloader and test the runtime MAC with the repo starter/controller loaded from `/data/local/tmp` |
| `dev-open.sh` | root: vendor E Ink opener copy, bins 0-13, pg_settle 100 |
| `dev-gates-direct.sh` | legacy POC direct-gate opener; refreshed vendors use `a9ml-eink.sh open` |
| `start.sh` / `connect.py` / `status.sh` | Wi-Fi session start + 20 dBm cap + framework enable; join a network saved in Settings, or on a fresh RAM /data inject the test network from 1Password (stdin, never host argv); sanitized status |
| `soak.sh` / `soak2.sh` | periodic HTTP 204 (Wi-Fi) / HTTP + redraw progress + fault latches (joint) |
| `boot-87k-*.sh`, `run87k.sh`, `dev-reopen.sh`, `dev-wifi-*.sh` | 87k-image runs and the integrated-image diagnosis steps |

## Known gaps (review backlog)

- Integrated Wi-Fi blockers worked around, not fixed: the controller refuses a non-MPSS remote
  QRTR node (ADSP/sensors) -> sensors/adaptive frontlight are stopped for Wi-Fi. The controller
  source now accepts the identified ADSP transport; installed-image testing remains pending.
- The GPU guard/admission layer was removed by user decision. Mesa/freedreno rendering and
  the kernel E Ink protections remain; test the refreshed vendor through the normal opener.
- The runtime MAC sources were tested by loading them at runtime. To include the current
  checkout's starter/controller in the integrated vendor, run the host-only rebuild below;
  the rebuilt image still needs a phone test using its installed copies.
- The `images-mac/` DT-MAC boot image is private and must never be committed.

## Rebuild the integrated vendor (host only)

From the checkout containing the merged fixes:

```sh
bash scripts/mainline/android/build-vendor.sh --refresh-integrated
```

Output: `~/A9-build/mainline/vendor-rebuild-20261020/vendor.img`, its SHA256 sidecar,
`build-result.json`, and debugfs/fsck logs. `--out DIR` selects another output directory.
This reuses the sealed R4 vendor (785ab053) and the original R4 debugfs replacement method.
R4 combined GPU/BT/health/thermal/sensors with the s2-87d Wi-Fi vendor; the ordinary
builder's mutually exclusive Wi-Fi/GPU/BT flags cannot reproduce that composition.
The refresh preserves its modules, labels, session settings and S2 start gate.
It updates the starter/controller, tool inventory and session manifest together, refreshes the
E Ink opener/GPU init, and removes the obsolete GPU guard script and services.
The native tool lock and kernel `wifi-manifest.json` need no new pins when their
payloads do not change; `wifi/verify-images.py` has no starter/controller hash constant.

The refresh also restores the normal system `logd` service through a vendor init
override. The selected diagnostic system image instead launches the retired
`a9-logd-factory` EGL-monitor wrapper (exit 125 on refusal, one-shot, no init-created
`logdw` socket). The ordinary `/system/bin/logd` binary remains intact. Check on
the refreshed image: `getprop init.svc.logd; logcat -d | wc -l` (expect `running`
and a nonzero count). This still needs installed-image testing by the coordinator.

The existing root shell on `127.0.0.1:2324` now starts at `post-fs-data`, after the
RAM data mount and before framework boot completion. It remains available during
a `system_server` boot loop; read tombstones through it, for example from the host:
`printf 'cat /data/tombstones/tombstone_00\n' | adb shell 'timeout 15 nc 127.0.0.1 2324'`.
Choose the latest text tombstone (`tombstone_*` without `.pb`) and save its output
privately; `/data` and the tombstones disappear at reboot. Normal tombstone ownership
and permissions are retained.

The same command includes the current controller and guard-free opener/init. It preserves
R4's Wi-Fi firmware-before-GPU ordering. For separately built replacements, add `--overlay DIR`, with
files at vendor-relative paths. New bin/config payloads receive the standard vendor labels.
Add `--gnss` to build and include the standalone AIDL GNSS service, LOC helper,
init/VINTF/feature declarations and file contexts; see [the GNSS README](../android/gnss-hal/README.md).

Use the unmodified R4 `images/boot.img` (02571e36), with no device MAC in its DT.
Start Wi-Fi through the installed session service (`vendor.a9ml.wifi.s2.start=1`),
without pushing scripts or supplying a factory MAC. Check that the state becomes
`serving` and compare `session/factory-mac` to wlan0 internally, printing only match
or mismatch. Test the installed ADSP-compatible controller with sensors left running.
Build outputs retain R4's private modem seeds and must stay local.
