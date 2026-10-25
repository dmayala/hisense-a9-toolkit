# INTEG27 restart: shared module loading

Host-only investigation of `sweep-integ27-f1-20261025`, boots129/130. The
ordinary reboot completed, but the second boot lost both sensor and audio
readiness. No phone operation or storage/firmware execution was performed.

## Failure boundary

| Boot | Retained kernel evidence |
| --- | --- |
|129|Sensors stages private registry and firmware, loads PAS with manual-start enabled, starts ADSP at42.97s, listener ready43.97s, sensors up44.14s. Five sensors and microphone samples pass.|
|130|Private registry32.63s and firmware verification32.84s succeed. Sensors exits `fail:insmod-qcom_common` at36.91s, before listener startup. Another loader registers PAS and automatically boots ADSP41.24s; DSP reports up41.56s. No sensor listener/readiness follows.|

The immediate failure is userspace module insertion. Its errno/stderr was not
retained, so EEXIST is an inferred interleaving, not a logged errno. No retained
ADSP boot timeout, SSR fatal event or crash explains that earlier script exit.
Both persist staging and firmware checks passed; no sensor configuration change
between these boots was demonstrated.

Three boot consumers load shared modules concurrently: sensors, the Wi-Fi
coordinator and the cellular preparation action. Previously they checked only
`/sys/module/<name>` existence and treated insertion failure as fatal. That
check races with another insertion, and the directory also appears before
`module_init` completes. The two Wi-Fi loaders additionally omitted
`modules.options`: if Wi-Fi won PAS insertion it bypassed
`a9_adsp_manual_start=1`. This matches boot130's missing manual-start marker and
subsequent automatic ADSP boot. The sensor owner requires manual start and the
FastRPC sensor option before starting its confined registry broker.

Audio is downstream: `init.a9-audio-output.rc` starts its loader only after
`vendor.a9.sensors.ready=1`. A sensor setup exit therefore leaves
`vendor.a9.audio.ready=0`, even when another loader has brought ADSP up.

Stock `etc/init/init.vendor.sensors.rc` requests `boot_slpi` in early-boot and
prepares sensor registry ownership; `vendor.sensors.qti.rc` and
`vendor.sensors.sscrpcd.rc` start the stock core/early_hal sensor services.
Those stock RCs do not implement our competing shell module insertion loops.
Their writable-persist commands are not reproduced: mainline keeps the private
RAM registry and physical persist read-only.

## Change and verification

All four relevant insertion paths now use one helper. It applies the existing
module options for whichever consumer wins insertion, then requires `initstate`
LIVE. A competing insertion failure is reusable only if a live winner appears;
COMING is bounded to5s. It never retries insertion, unloads a module, starts a
remote processor or restarts a service. Genuine failures remain failures with
a module-name/return-code diagnostic. Cellular preparation loads GLINK before
`qcom_common`, matching the latter's imported SSR helpers; boot129's earlier
unknown-symbol messages demonstrate the old ordering gap.

Host checks cover actual Android-shell losing-insertion/LIVE, genuine failure,
stuck COMING, GOING, existing LIVE and option propagation. Existing sensor
setup tests pass, including private persist isolation and no-restart failures.
Wi-Fi prepare/abort/cleanup fixtures pass after updating their stale inline
RAM-data fixture to the existing shared admission helper. Effective policy
already permits all three domains to read generic module sysfs attributes.
The actual build14 offline installer verifies the new helper's metadata,
updated files and fsck. No kernel or policy permission change is needed.

The next phone boot should show PAS's manual-start marker regardless of module
load order, then sensor-owned ADSP start, listener ready, sensors up and audio
ready. The restart row must observe positive sensor count and both ready
properties after the reboot. Host tests do not establish that runtime verdict.

## Recheck after worktree consolidation

The report and fix are both on main in `f46d495`; this is not a new failure
found after that commit. Integ27's frozen source is `eda8a0d`, which predates
the fix. A read-only `debugfs` inspection of its actual `vendor.img` confirms
that `/vendor/bin/a9-sensors.sh` still uses the directory-existence check and
fatal `insmod` loop described above. A host launcher rerun cannot replace
that installed boot script; the next vendor image must include the fix.

The current main module-load regression passed all three test groups using
Android mksh under qemu, including options, competing insertion and LIVE
readiness. The sensors setup suite passed with zero failures, including
private registry isolation and refusal/supervision cases. Its optional real
SNS probe was not exercised. No phone operation was performed, and a warm
reboot with a rebuilt image remains the runtime confirmation.
