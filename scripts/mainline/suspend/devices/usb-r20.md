# R20 USB resume candidate

R19 real RTC20 +60s postwatch kept ADSP/sensors/capacity healthy, but USB never
returned; DWC3 reported failed to enable ep0out. R18 had the same USB error.
This is separate from the fixed FastRPC thaw crash and cached-SOC gap.
agent alone runs the phone. All work here is host-only, no phone or REA runtime.

## Stock first: exact package kernel

Source: A9-build/mainline/stock-adsp-suspend-20261021/package-vmlinux.elf,
SHA256 0fd46f80770193f08633e9c5d31a6137800be0436524db743bd7f66db8154b01.
This is the extracted package kernel, not an assumption about the golden phone
kernel. Exact bytes/function hashes/annotated assembly and Ghidra12.1.4 REA
snapshots: suspend-novtcon-20261021/usb-r20/{stock-usb.json,*.asm,rea-*-snapshot.json}.
No stock phone suspend trace was taken. External callees/data are unmapped in
the small ELF excerpt; call names are resolved from the full symbolized ELF.
Notifier decompilation cannot resolve its external jump table; its register
sequence below is instruction evidence, not reconstructed C. The table bytes
are decoded in notifier-switch.json: event2 ->0xffffff800890abc4. Stock
dwc3_core_init calls notify_event(event2) at0xffffff80088f0bb4..0xffffff80088f0bcc,
so that mux sequence runs on core initialization, including reinitialization.

| Stock function | Address | Observed behavior |
| --- | --- | --- |
| dwc3_msm_suspend | 0xffffff800890e7f0 | Checks link/L2, requests USB2 low-power state, invokes HS/SS PHY suspend before clock/bus votes are dropped; conditional controller GDSC collapse. |
| dwc3_msm_resume | 0xffffff800890bfb0 | Restores bus/XO; on POWER_COLLAPSE restores GDSC, asserts core_reset, waits1000..1200us, deasserts reset, then clocks; resumes PHYs, core reinitialization and IRQ/work handling. |
| dwc3_msm_pm_suspend/resume | 0xffffff800890e5d8 / 0xffffff800890e670 | System PM coordinates with the MSM runtime low-power state; not a blind clock gate/ungate. |
| dwc3_msm_notify_event | 0xffffff800890aaa8 | Controller glue notification switch. At0xffffff800890ac60..0xffffff800890ad60 it programs core_base+0xf8808: sets mux bits0/3, clears bit8, with delays; same QSCRATCH UTMI selection as mainline probe. |
| qusb_phy_set_suspend | 0xffffff80088e6a50 | Connected branch arms line-state wake/clamps, gates clocks; resume clocks and clears interrupt control. Disconnected branch can power down/up rails. Installed by qusb_phy_probe0 at0xffffff80088e5c64. |
| msm_ssphy_qmp_set_suspend | 0xffffff80088ebba8 | Programs autonomous/clamp state and clocks, conditional regulator handling. Compiled reference only: board SS QMP node is disabled. |
| dwc3_gadget_resume | 0xffffff80088fbb10 | Restarts control endpoints, checks start error before run/stop. |
| dwc3_msm_vbus_draw_work | 0xffffff800890a250 | Passes gadget current to USB power_supply policy; separate from PHY/controller clocks. |
| smblib_set_usb_suspend | 0xffffff8008a2c318 | Sets/clears USBIN_SUSPEND bit0 at0x1340, coordinates USB_SUSPEND_VOTER, propagates regmap error. |

Stock merged board DT uses qcom,dwc-usb3-msm, HS-only snps,dwc3, QUSB2 at
0x1613000 + a nop SS transceiver. The physical QMP SS node0x1615000 is disabled.
Stock owns a USB3_GDSC supply and core_reset. HS SUSPHY/ENBLSLPM quirks match ours.
Do not enable SS/QMP or copy unrelated stock charger limits as a USB repair.

REA evidence IDs: MSM suspend ev_5550416cfc2d1b2d4ede940d9c1e5f23926d85b620029a8306443435481c27e5;
resume ev_9cc79368e9211598465ff9ec617c4ca5a6eecbc034ed2b9c720e747907e1a2c5;
HS PHY ev_aec82125c8c707ce4a9328be7e713f8d6138ae72d4af5bc073db53bb3dd15499;
QMP ev_4735f78d0a3bcb0e9f3347ada68fdaea9890ff38c514f75f2e7074b35b4b476c;
charger ev_0f2272b89094bb7119b703fe4b6b07d4f2964a778b25df84387b53e84c6d05d8.
Full immutable inline records are retained in those snapshots.

## Pinned mainline comparison and smallest candidate

Actual bound path is drivers/usb/dwc3/dwc3-qcom-legacy.c, not the new flattened
qcom driver. Parent4ef8800.usb owns USB GDSC OFF_ON, clocks and QSCRATCH; child
4e00000.usb is HS-only/peripheral with only a QUSB2 provider. The legacy probe
sets UTMI-as-PIPE clock selection and software VBUS/session overrides. Its
resume enables clocks/ICC and clears L2 events, without restoring that state.
The child core reinitializes after the parent resumes. Mainline QUSB2 runtime
suspend similarly selects opposite DP/DM line triggers, optional PLL override
and autoresume, then gates clocks; runtime resume enables clocks before clearing
interrupt control/PLL override. Full device-mode core exit/init instead uses
PHY exit/init (reset/rails, tuning and checked PLL lock). R19 has no reported
QUSB PLL-lock failure. QMP is not a live provider on this HS-only path. Its gadget-start error
is currently ignored by soft_connect/resume_common, so PM success does not
prove EP0 was enabled. EP0 does no TXFIFO resize; the error is downstream in
endpoint config/resource setup, with its exact errno unrecorded by R19.

The real-sleep-only failure is consistent with GDSC context loss. R20 tests
that hypothesis by saving the three owned QSCRATCH field groups before clock
gating and restoring them before child resume. Scope is sm6115-dwc3 with
qcom,select-utmi-as-pipe-clk. Only clock-select/status/disconnect bits,
LANE0_PWR_PRESENT and UTMI VBUS_VALID/SW_SESSVLD_SEL are restored. Other bits
are preserved; the mux is gated/delayed only if its saved state differs.
This restores exact prior session policy, not a forced attached/charging state.
A9USB_R20 restore gen=before->after hs=before->after ss=before->after is emitted
once per resume to kmsg (quiet console level1 remains). No observer, IRQ/PM
bypass, always-on GDSC, PHY power patch, reset sequence or charging-limit change.

This is a candidate, not a proven root cause/fix. Stock also resets on collapse;
R20 deliberately tests the missing mux/session restoration first. If the three
field groups were retained and EP0 still fails, this candidate is falsified;
keep that register line and the exact DWC3 error for the next change.

The active battery module stop() clears CHG_ENABLE only, preserving USBIN.
Its resume restarts polling. Gadget vbus_draw feeds its SDP current vote;
<=25mA legitimately suspends USBIN, and an absent successful configuration can
leave Charging->Discharging. Do not bypass that vote to mask controller failure.
USB input/charging recovery must be measured separately from cached SOC100.
Stock symbol lookup found no named SMB5 system-suspend/resume hook; do not infer
that stock suspends charger input merely because system PM starts. The observed
0x1340 operation is a USB policy helper, not proof of a system-PM invocation.

Useful primary comparisons (not the exact shipped build):
- [Mainline legacy glue](https://github.com/torvalds/linux/blob/master/drivers/usb/dwc3/dwc3-qcom-legacy.c)
- [Upstream MSM8953 GDSC comment describing the same EP0 symptom](https://kernel.googlesource.com/pub/scm/linux/kernel/git/torvalds/linux/+/master/drivers/clk/qcom/gcc-msm8953.c)
The latter concerns a different SoC and is not evidence to force our GDSC on.

## Integration commits

69285d941a74e043a522b14fc182cb7a37c896f1 and
1a82b6e053a0e7a839b64b2393c5a5d5ffe55513 have valid signatures and clean scopes.
FastRPC is clean source patch+note only, no R17 diagnostics/cut/counters.
Apply AFTER S1 gate, explicitly rebuild and stage fastrpc.ko: cherry-picking
alone does not replace an old cached module. Battery is only driver/note/host
test. It retains seeded cached SOC and resets integration baseline; uncertain
CHARGE_COUNTER stays unavailable, no invented sleep charge or shutdown override.
R18/R19 real sleep exercised restoration, and R19 HEALTH_PASS validates the
20s+60s case. Long/repeated sleep/full stock FIFO/OCV accounting remains open.
R20 does not change either integration commit or promote the USB candidate.

## Built R20 pair and exact run

N=~/A9-build/mainline/suspend-novtcon-20261021. NEW boot:
N/built-rtc-r20/boot.img
SHA256 d0e9519f703f896e1a5f3c32f740c321245454818a210ebb86e32dbdeb29e0a4
UNCHANGED R19 vendor:
N/built-rtc-r20/vendor.img
SHA256 a9b59ac7e641c3849431cb9fecaf76e67bc0f81c8f3e0440ef6c9e0681d38442
The bundle uses relative symlinks to usb-r20/boot.img and built-rtc-r19 inputs;
preserve those targets. SHA256SUMS plus per-image.sha256 files are in the bundle.

Prepare/build/package rc0, offline scripts/abl-check.sh through its cached Unicorn environment PASS/rc0 on exact new boot
and embedded recovery_dtbo. Same config as noVT SDK, all13168 built-in exports
identical, FastRPC92 and battery38 import CRCs match; same7.2.10-a9+ release.
No module/helper rebuild required. Nonkernel payloads, primary+extra cmdline,
DTB, recovery_dtbo byte-identical to noVT boot84d3188b. Image.gz nowe8ad90bc....
Readback manifests/payload-check.json/abi.json/abl-boot.json and all step RCs
are under N/usb-r20; bundle build.json records the reused inputs and checks.
Patch applies with git apply --unidiff-zero to the pinned legacy source and
reproduces the built C byte-for-byte. Zero-context form keeps the committed
patch file whitespace-clean; source code and built bytes did not change.
Disk7.1GiB before,5.0GiB after private copy,4.2GiB after build;2GiB reserve and
step budgets checked. No source/object deletion or other-owner cleanup.

Fresh NEW R20 boot + SAME R19 vendor, under agent's existing owner/recovery
>=120s. Wi-Fi serving, HOST_IP set, hostUDP42326 open. Prepared root body
N/preflight-rtc-r20.sh: printk1, pm_debug_messages1, pm_test[none], no observer,
save+disable UART wake at4a8c000.serial, sensorsup/ready1, ADSP running, numeric
capacity and exact battery module1ee34db0. Same R19 preflight behavior.
Restore saved UART policy through the original root channel or fresh recovery.

```sh
cd /home/user/A9-wt/fix-suspend-devices
N="$HOME/A9-build/mainline/suspend-novtcon-20261021"
bash scripts/mainline/suspend/phone-test.sh rtc \
  --udp-host "$HOST_IP" --built "$N/built-rtc-r20" \
  --rtc-seconds 20 --fastrpc-restore --require-health --post-watch 60 \
  --log "$N/rtc-r20-phone-$(date -u +%Y%m%dT%H%M%SZ).log"
```

N/commands-rtc-r20.sh is the same exact command. Prepared shell syntax PASS;
worker did not execute any phone command. No pm_test/cut/bypass/observer flag.
WDT30s, one-shot post-return feed, END/ACK, cleanup and60s health postwatch are
unchanged. Original owner alone handles failure/recovery; no auto retry.

Expected R19 health proof repeats: RTC~20s/IRQ19/alarm, clean END/ACK/disarm,
ADSP running/sensors ready, FastRPC restored1 with no resend, numeric capacity
through POSTWATCH_COMPLETE60/HEALTH_PASS. Additional R20 requirement: kmsg has
A9USB_R20 restore before the child gadget restarts, no ep0out enable error,
host sees fresh USB enumeration and adb remains reachable after wake/postwatch.
Original owner checks actual USB input online/current and Charging/Full status;
a cached capacity100 alone does not qualify charger recovery. If health passes
but USB still fails, R20 fails its purpose. Preserve register before/after,
EP0 error and host transport result; no subsequent bisect auto-launch.
