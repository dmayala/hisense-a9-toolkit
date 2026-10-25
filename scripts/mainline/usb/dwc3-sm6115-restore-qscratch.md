# SM6115 legacy DWC3 QSCRATCH restoration

Apply dwc3-sm6115-restore-qscratch.patch with `git apply --unidiff-zero`
to the pinned kernel before building Image.gz. The change is built in;
replacing a vendor module alone does not apply it. Preserve boot payload/DTBO
rules and run scripts/abl-check.sh on the rebuilt boot.

Save the legacy glue's owned QSCRATCH UTMI clock mux and session overrides
before suspend; restore after clocks/ICC and before the child DWC3 resumes.
Restrict to qcom,sm6115-dwc3 with qcom,select-utmi-as-pipe-clk. Restore exact
prior fields, preserve unrelated bits, and gate/delay the mux only on change.
No forced VBUS attachment, charge-current vote, PM bypass or always-on domain.

Phone R20 PASS supplied by agent, rtc-r20-phone-20261021T173537Z.log:
general0x4->0xd, HS0x10000000->0x10100000, SS0->0x1000000. Context loss is
measured. USB/adb returned on slotB, charging within10s; RTC/ADSP/capacity
and60s health passed. Residual one request-not-queued EP0 message at+0.25s
and pre-sleep HS-PHY-not-L2 remain; do not claim all USB PM issues resolved.

This integration patch removes the R20 printk and its two diagnostic-only
reads; restore behavior matches the tested candidate. W=1 compile against the
noVT SDK rc0/no warnings. No R21 instrumentation, observer, FastRPC/battery
change, DT/cmdline change or binary is part of this commit. The clean kernel
has not itself been booted; R21 intentionally reuses the measured R20 boot.
Stock-first exact package addresses/REA evidence are in
../suspend/devices/usb-r20.md and the local N/usb-r20 evidence bundle.
