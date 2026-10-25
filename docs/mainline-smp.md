# Hisense A9: staged SMP, cpufreq and PSCI idle

**2026-10-15 — status: A/B0/B1/C1 images built and offline-validated; no phone test by
this agent.** Device operations remain the coordinator's responsibility.
Sources/tools: [scripts/mainline/smp](../scripts/mainline/smp/README.md).
No file in `scripts/mainline/kernel/` was edited by this agent. Captures and
device identifiers must remain outside git.

**MEASURED** means read from saved images/captures or local code/build output.
**INFERRED** identifies expected behavior requiring coordinator measurements.
The first change should be **A: eight CPUs, idle and cpufreq still off**.
CPU_ON is separate from CPU_SUSPEND and its OSI/PC coordination. The existing
warnings do not establish that either SMP or the cpufreq hardware is broken.

## 1. Evidence and comparison

Reference mainline source: Linux 7.2.10. The original boot-2 capture is
`runs/20261015T041539Z-mainline-first-boot/capture`; only derived findings are
recorded here. It reports PSCIv1.0 over SMC, standard v0.2 function IDs, OSI
support, denied initial PC mode (`-3`), **one** activated CPU, and failed
cpufreq registration. It contains no successful secondary CPU_ON trial or
enabled cpufreq policy. Do not infer hardware failure from either omission.

The saved stock boot log `kmonlog_2026_0811_105015` in golden kdebuginfo
was also read offline: it reports PSCIv1.0, standard v0.2 IDs, successful
CPU1–7 startup at those exact MPIDRs, and eight activated processors. This
proves this phone's firmware has supported CPU_ON under stock Linux; it does
not prove mainline startup or suspend behavior. No raw log was added to git.

Stock CPU/clock facts were checked against the phone's own golden
`lun4/boot_a.bin`/`dtbo_a.bin`, opened read-only. The extracted FDT blobs were
byte-compared to those images; the second embedded blob is RTIC metadata,
not another CPU/board tree. Numeric provenance and hashes are in
[`stock-evidence.json`](../scripts/mainline/smp/stock-evidence.json). Saved DTBO
was applied offline to the actual board base, confirming that CPU/PSCI/cpufreq/
LPM subtrees are unchanged. The
stock DT/BSP uses downstream bindings; copying it wholesale is inappropriate.

| Item | Stock | Upstream sm6115.dtsi | Consequence |
|---|---|---|---|
| CPU count / MPIDRs | 0,1,2,3,0x100,0x101,0x102,0x103 | identical | four little / four big CPUs, matching topology |
| Startup | `enable-method="psci"` | identical | use existing arm64 PSCI CPU_ON path; no spin-table/SCM boot rewrite |
| PSCI conduit | `arm,psci-1.0`, `smc` | identical | no reason to change to HVC or fake PSCI 0.2 |
| Capacity | little 1024, big 1638 | identical | scheduler capacity already describes heterogeneous cores |
| Frequency domains | little 0, big 1; downstream two argument cells | little 0, big 1; one argument cell | retain upstream binding; stock's extra cell is not an upstream CPU index |
| cpufreq MMIO | 0x0f521000/0x1000, 0x0f523000/0x1000 | identical | legacy CPUFREQ-HW layout, not EPSS |
| Clocks | `xo`, `alternate` | `rpmcc` XO / GCC GPLL0, same names | current upstream provider handles these inputs |
| CPU idle | downstream `qcom,lpm-levels` coordinator; CPU PC low mode 3 | CPU domain state 0x40000003 | matching low mode, whole composite state semantics still need testing |
| Cluster idle | mode bits at shift 4; system bits at shift 8 | GDHS 0x40000022/0x40000042, collapse 0x41000044 | higher-level coordination is a separate C2 experiment |
| LMh | two downstream limits nodes, shared SPI37 and control 0xf550800 | no LMh IRQs on this cpufreq node | not required to register cpufreq; host throttle accounting is not validated |

The stock symbolized Image confirms the standard-offset array at
`0xffffff8009706c70`: enable 0x000, frequency LUT 0x110, voltage LUT 0x114,
performance request 0x920, cycle counter 0x9c0 (16-bit entries). That matches
the relevant upstream `qcom_soc_data` legacy offsets. Upstream additionally
reads DCVS mode at 0xbc and can write per-core requests when firmware reports
that mode. It never initializes PLL/rail voltages itself: it requests indices
in the firmware-populated LUT. Hardware enable bit0 must already be set;
failure there is a stop/measurement result, not a reason to force-enable MMIO.

One important stock DT trap: it says `qcom,max-lut-entries = <12>`, but the
actual driver's property string is **`qcom,lut-max-entries`**. The driver's
initialized row size and maximum at `0xffffff800a071c58` are **32 and 40**.
Do not invent a 12-row mainline limit from the misspelled/unused property.
Upstream also defaults to 40 rows, 32-byte stride, and stops at repeated
frequency entries. Boot-2 with cpufreq disabled did not validate the live LUT.

The upstream binding explicitly identifies SM6115 as legacy CPUFREQ-HW:
[binding](https://kernel.googlesource.com/pub/scm/linux/kernel/git/stable/linux/+/e34881c84c255bc300f24d9fe685324be20da3d1/Documentation/devicetree/bindings/cpufreq/cpufreq-qcom-hw.yaml).
No B-stage compatible/reg patch is justified. In particular, do not force
`qcom,cpufreq-epss`: its LUT stride and performance-request offset differ.

## 2. What the warnings mean

**cpufreq:** `qcom_cpufreq_hw_driver_probe()` eventually calls
`cpufreq_register_driver()`. The latter immediately returns `-ENODEV` when
`cpufreq.off=1` is set, producing the captured generic registration error.
That is expected under the precautionary command line. The probe may have
mapped resources/registered its clock provider before that point; this is not
proof of live enable bits, valid LUTs or successful frequency transitions.
The existing config already has SMP, CPU_FREQ, ARM_QCOM_CPUFREQ_HW and the
userspace/schedutil governors built in. NR_CPUS=512 does not create 512 CPUs;
the DT describes eight, while `maxcpus=1` restricts startup.

**PSCI:** the captured `-3` is raw **PSCI_RET_DENIED** from an early
`SET_SUSPEND_MODE(PC)` request, not a CPU_ON return. Mainline's PSCI 1.0 init
logs and continues after that request. This is a known Qualcomm firmware
behavior, already deliberately logged at information level upstream:
[upstream discussion](https://lists.infradead.org/pipermail/linux-arm-kernel/2023-April/827423.html).
It does not justify hiding the error, pretending PC succeeded, or disabling
the advertised OSI feature. Firmware mode has no standard readback here.

[TF-A's PSCI description](https://trustedfirmware-a.readthedocs.io/en/latest/design_documents/psci_osi_mode.html)
distinguishes CPU_ON from suspend coordination and describes SET_SUSPEND_MODE
constraints. Qualcomm mobile firmware commonly implements vendor OSI policy.
The stock custom LPM tree is consistent with OS-coordinated composite states,
but neither that tree nor a denied PC request proves the current mode. **C1
must obtain a successful upstream OSI setup**, not merely suppress a warning.

The current a9.config disables ARM_PSCI_CPUIDLE. Changing cpuidle.off alone
cannot enable it. Conversely, compiling its domain driver in can request OSI
even if cpuidle.off=1; the command-line flag is not a complete domain-probe
barrier. Keep that config off in A/B.

## 3. Stages and pass criteria

### A — secondary CPU startup only

Replace `maxcpus=1` with **maxcpus=8**. Keep cpuidle.off=1, cpufreq.off=1,
ARM_PSCI_CPUIDLE disabled, and every unrelated guard/Android parameter.
No DT CPU-node change is needed. The stock and upstream CPU_ON ABI/IDs match;
this supports a controlled trial, **not a guarantee** of this TZ accepting
mainline's secondary entry or of all cores working correctly.

Pass: early and >=300-second-late captures enumerate processors 0–7,
online=0-7, eight IRQ columns with observed per-CPU IRQ/IPI activity, an
8-CPU startup log, same boot ID and advancing uptime, and no panic/SError/
CPU-start failure/reset. No cpufreq policies should be active. Observe Android
system_server start count/PIDs separately; a CPU gate pass is not proof that
all Android services are fixed. No CPU hotplug/offline test is included.

**Early-reset limit:** SMP startup occurs around 0.075 s, before the PON beacon
platform driver's probe. A CPU_ON fatal reset can therefore precede beacon
arming and userspace watchdog/dead-man execution. Retain the coordinator's
existing fail-safe entry/restoration procedure; do not claim the beacon
protects this earliest interval. This agent does not boot or prepare phone
state. A late reset after the beacon and an early reset are different hazards.

### B0 / B1 — hardware frequency registration, then transitions

After A passes, remove cpufreq.off; retain cpuidle.off=1. **B0** explicitly
selects `cpufreq.default_governor=userspace` to inspect the provider without
immediately adding a dynamic governor. Registration can itself validate/select
an initial LUT entry, so B0 is not a zero-write image. Do not write registers
or force hardware enable. Pass: policy0 related CPUs0–3, policy4 CPUs4–7,
driver `qcom-cpufreq-hw`, nonempty available-frequency lists, positive current
frequency within policy limits, expected governor, and A's continuity checks.

**B1** changes only the governor to schedutil. Pass additionally requires a
demonstrated transition (counter increase or observed rate change) without
resets/CPU faults. A stable number at two times is not proof of transitions.
`scaling_cur_freq` is a requested/LUT-derived rate; `cpuinfo_cur_freq`, when
provided, helps observe hardware throttling. BogoMIPS is not measured MHz.

LMh is optional in the upstream driver (`platform_get_irq_optional` permits
absence). This does **not** prove thermal safety. Do not transplant downstream
limits nodes or duplicate their shared IRQ onto the upstream provider. The
standalone qcom LMh driver has separate firmware/SCM initialization and is not
needed for this first cpufreq trial. Preserve firmware limits, keep initial
trials short, collect available TSENS temperatures, and stop the trial on
abnormal rising temperature/throttle faults. A conservative test ceiling is
80 C CPU-sensor temperature, not a claimed device specification. Do not run
unbounded performance stress while host/firmware thermal integration is unknown.
The capture proposals record thermal-zone types and temperatures. B/C gates
require the seven upstream CPU TSENS zones and readings below 80 C at both
observations; unavailable sensors are incomplete evidence. Two point samples
cannot exclude an intervening spike, so the coordinator must also watch these
read-only temperature files during the bounded trial.

### C1 — OSI with CPU idle only; C2 held back

After B1 passes, use the rebuilt C1 kernel: ARM_PSCI_CPUIDLE=y and
ARM_PSCI_CPUIDLE_DOMAIN=y, remove cpuidle.off, retain maxcpus=8 and schedutil.
The private C1 DT removes the two cluster domain-idle-state lists, retaining
CPU domains/states. The upstream domain driver requests OSI using the real
PSCI call and fails initialization if denied. It may still print the known
early PC warning. A passing C1 must log **Initialized CPU PM domain topology
using OSI mode**, expose `psci_idle`, and demonstrate state1 usage increases
and residency increases on every CPU with no rejected entries, while all eight
CPUs remain online over the continuity interval.

The domain setup is a `core_initcall`, before the PON platform driver's
device-init probe. A fatal OSI setup error can therefore share A's early-reset
gap; the beacon/userspace dead-man is not guaranteed to cover it.

No PSCI-core quirk is currently justified: the fix is to use the supported
OSI topology/configuration path and verify its result rather than force PC.
If OSI is denied or idle returns errors, stop at B1 and return the capture.
A PC-only fallback requires new evidence of PC acceptance; do not emulate
success or reinterpret DENIED as support. C1 has not proven that call succeeds.

**C2 is a later proposal only:** restore upstream cluster GDHS/collapse lists
one cluster/state at a time after CPU idle passes. Shared-cache/RPM coordination,
s2idle and suspend-to-RAM need separate wakeup/residency/current measurements.
Do not bundle them into the first Android performance test.

## 4. Artifacts, isolation and validation

The known Android boot-2 image is SHA256
`d719592576dcda102043490175e632428ed0185bee927628d1a114a401544520`.
The [README](../scripts/mainline/smp/README.md) gives exact offline commands,
whole command-line override files and build recipes. A/B variants change only
the header's command-line fields; kernel, ramdisk, DTB, overlay, Android slot
pinning and all unrelated parameters remain identical. No duplicate CPU
parameters are appended. A9_CMDLINE (kernel builder) and A9_ANDROID_CMDLINE
(Android ramdisk builder) are whole-string override interfaces if the owner
chooses normal integration instead of these prepacked variants.

C1 uses a private source archive of `bd6d5b239` plus the ashmem request patch,
the exact config extracted from the tested kernel, and only the PSCI-idle
config additions/dependencies. Its DTB is the tested DTB with two cluster
idle lists removed. The rebuild excludes subsequent rail/display changes to
avoid confounding the idle test. Source/config/build outputs are isolated under
`~/A9-build/mainline/sol-smp`; no shared build/source files or backup were changed.
Images/manifests/build logs are local artifacts; their existence is not a
successful firmware test. Verify the manifest and ABL checks before any trial.
The optional copied-cache build uses a private mount namespace with the pinned
source mounted read-only at its original path. Source bytes are compared first;
only identical files reuse original timestamps. Normal Kbuild dependency checks
apply, with no edits to `.cmd` metadata, shared outputs or parent mounts.

Offline verification includes stage-isolation/ambiguity tests, synthetic
capture gates rejecting resets/missing CPUs/OSI failure, real A/B byte comparison,
private C1 compilation, DT property checks, command-line/geometry/size checks
and the phone ABL's **offline emulator**. No image was flashed or booted here.
The checker rejects missing evidence instead of inferring a pass.

**MEASURED offline result:** all four images passed independent unpacking,
hash/component checks and ABL emulation, including inspection of the ABL-merged
DTB. C1 release is `7.2.10-a9+`, runtime `image_size` is 30,081,024 bytes
(the same size field as the baseline), and the linked symbol map contains the
PSCI idle/domain/OSI functions. Its image SHA256 is
`04b9b789ae0c47796bbede0a3a843abbc5f4aea6025abf234df19ed7dbd521e2`.
Artifacts and `manifest.json` are under `~/A9-build/mainline/sol-smp/images/`.
The private source/object trees were removed after validation; config, symbol
maps, source/patch provenance and logs remain under `sol-smp`. Disk use returned
to 72%. No firmware/runtime pass is claimed.

## 5. Coordinator-only read-only observations

The existing capture lacks cpuinfo, online masks, policy/idle data and boot ID.
`0002-capture-cpu-telemetry-PROPOSAL.patch` adds them to the current collector;
it is unapplied and belongs to the coordinator's kernel owner. Proposal 0003
adds matching fields to the Android vendor NCM collector; its current capture
also needs interrupts and uname/uptime. Neither owner file was edited.
All new fields
are normal read-only proc/sysfs reads. Captures contain identifiers and stay
outside git. Early/late pairs can then be checked entirely offline:

```sh
python3 -I scripts/mainline/smp/check-capture.py A /saved/early /saved/late
```

Until that proposal is integrated, the exact extra read-only observations to
run through the coordinator's established console/transport are the commands
in [capture-addition.sh](../scripts/mainline/smp/capture-addition.sh), with `d`
set to that run's capture directory. They read `/proc/cpuinfo`, `/proc/stat`,
CPU online/possible/present, boot ID, policy driver/governor/domain/frequency/
transition counters, idle state usage/time, and thermal zone types/temperatures. They do not online CPUs, set
governors, disable idle states, write MMIO, flash, reboot or contact a phone
from this agent. Existing dmesg/interrupts/uname/uptime captures remain required.

Android-only read observations, **not run by this agent**:

```sh
getprop sys.boot_completed
getprop sys.system_server.start_count
pidof system_server surfaceflinger
cat /proc/cpuinfo
cat /sys/devices/system/cpu/online
```

Compare service continuity and ART timeout logs against the one-core baseline.
The existing Android notes also identify independent ashmem and userspace
issues; SMP/DVFS are a performance hypothesis, not a complete diagnosis of
system_server restarts. Return A's measurements before proceeding to B/C.
