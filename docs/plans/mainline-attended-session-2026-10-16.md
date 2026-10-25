# Attended session plan — 2026-10-16 (morning)

Status: plan, not run yet. Written overnight by the coordinator. These first runs all need a person at the
phone, as their reviews require. Run them in this order, **one first experiment per boot**, and go back to
slot A between them.

## 0. Before anything

1. `scripts/a9-health.sh`: slot A, Android booted.
2. The phone's current state: slot A is the daily ROM. Slot B holds the run-14 mainline E Ink build
   (`3df20ef`, kernel 0001-0042). The golden restore is `android-boot.sh --restore`.
3. Keep the phone on the desk, plugged in, where the person can hold **Power for 10-15 s**. That is the backstop for
   every run below; with PON reason 2 it lands in bootloader fastboot.

## 1. Reset backstop proof (no new feature): required before G1

Source: `runs/review-31-gpu.md` §4.
- Boot the default mainline pair and trigger a panic as root (`echo c > /proc/sysrq-trigger`).
- It must land in bootloader fastboot, and `android-boot.sh --restore` must bring back slot A.
- If not: stop, and G1 stays NO-GO.

## 2. Bluetooth only (branch `bt`): conditional GO

Source: `runs/sol-review-26.md`.
- **Inputs:** BT-only image and the private BD address `~/A9-build/mainline/sol-bt/private/bdaddr.txt` (valid; never print it).
- **Checks:** `a9_qca_firmware_required=1` in the copied hci_uart metadata, the firmware identity, and exactly one HCI controller.
- **Stop on any of:**
  - missing/unknown firmware or a ROM fallback;
  - a timeout or an identity mismatch;
  - an extra or wrong controller;
  - serial framing/IBS errors;
  - no command or scan response.
- Not combined with Wi-Fi, enforcing or audio.

## 3. GPU G1, vkms only, no E Ink (branch `gpu` at `9a72e97`): GO, attended once

Source: `runs/review-31-gpu.md` §4.
- **Pinned hashes:** boot `0b8ae05f…6835`, vendor `a5c9720d…08f5`; run `--offline` first.
- **Launch:** `setsid nohup env A9_G1_ATTENDED=yes …/g1-supervise.sh --code <c> </dev/null >log 2>&1 &`. Abort only with `kill -TERM <pid>`.
- **Stop:** hold Power, with no retry, if any of these happen:
  - no adb and no fastboot within 300 s;
  - USB shows `05c6:900e` or `9008`;
  - the phone is invisible for more than 5 min;
  - the phone gets hot;
  - the supervisor exits 3.
- **PASS needs all of:**
  - `G1 PASS restored=yes`;
  - a real kmsg with `qualification: PASS`;
  - dma-buf test 0 errors;
  - both GPU params `Y`;
  - SurfaceFlinger FD610;
  - slot A confirmed.

  Then G2 needs a new review.

## 4. Battery health only (`A9_HEALTH=real`, branch `sensors`): conditional GO

Source: `runs/sol-review-24.md` and `runs/sol-review-28.md`. The thermal flags are still NO-GO.
- **Steps:** `health-gate.py artifact`, then baseline `telemetry`, then a bounded attended run with `telemetry` and `compare`.
- **Stop on any of:** stale data or read errors, an absent battery, implausible values, unit/charging-state mismatch.

## 5. Wi-Fi (branch `wifi` at `19b70b9`): **user decision needed**

Source: `runs/sol-review-29.md`.
- All software findings (#2-#6) are fixed.
- Review 29 still says NO-GO, only because modem/TZ-side storage isolation cannot be proven offline. Stock and the A6L carry the same exposure.
- **Coordinator mitigation:** the test script reads modemst1/2, fsg and fsc read-only and hashes them before and after. Any change is a hard stop.
- Golden backups exist: `edl_2026-10-14_110759`, `nv/00000000.qcn`.
- **If the user accepts the residual risk,** run it supervised with review 29's gates and limits (30 s per stage, at least 180 s observation).

## 6. Audio / ES9218 Hi-Fi DAC (branch `audio`)

Source: `runs/review-33-audio.md`, a GO after fixes F1/F2.
- **F1:** keep the stock 0x55600000-0x56100000 reservation.
- **F2:** an independent ADSP switch, so the Wi-Fi build cannot start the ADSP. **Fixed in `830e288`** (DTB audit added). Use the new `out-audio-ess*` outputs, not the old `out-audio/`.
- **Supervised stages, each with stop conditions:**
  1. read-only stock `dmesg`;
  2. a boot with the audio DT disabled;
  3. ADSP;
  4. codec-only probe;
  5. **silence with an empty jack** (replaces the ID-read-only stage);
  6. a -40 dBFS tone with the headphones not worn.
- **To verify on the phone:** chip ID and board variant (stock enables Hi-Fi only for `board_uid==2`), selector pin, oscillator, volume direction, pops.
- Not combined with Wi-Fi in the first audio image.

## Not tonight

- SELinux enforcing: NO-GO until runtime recovery is qualified (`runs/sol-review-25.md`).
- Thermal kernel/HAL: NO-GO (`runs/sol-review-28.md`; fixes in progress).
- Hardware video: blocked on firmware 6.0.39 vs 6.0.55.
- CPU idle: the C1 variant (`cpuidle` `bd4d025`) is safe but saves nothing (idle is already WFI). Real savings need core power collapse plus an a9_eink latency request: a later experiment (`runs/sol-cpuidle.md`).
- Cellular, cameras, FBE: days of work (`runs/parity-matrix.md`).
