A9 plain boot deadline (2026-10-22)

WDT backstop variants (supersede software-only deadline for hang recovery):
plain-deadline-logfix1-wdt-20261022 and
plain-wrapped-deadline-logfix1-wdt-20261022 under A9-build/mainline.
The initial fix3 package is plain-wrapped-fix3-wdt-20261022/boot.img:
it retains agent's exact deferred ICE SCM-query and empty-slot UFS changes,
plus this backstop. Config/export ABI and every nonkernel boot payload/cmdline
match his plain-wrapped-fix3-20261022 boot. That comparison did not establish
plain-integ module compatibility: its struct-bio ABI mismatch was subsequently
confirmed. Fix4 below restores that ABI. No vendor image is bundled for fix3;
pass the known-good vendor to b-try for its existing private overlay.
Build recipe build-wdt.py; built-in source a9_boot_deadline-wdt.c and header.
The previous late_initcall timer can miss a hang in an earlier driver probe.

plain-wrapped-fix4-wdt-20261022 adds the same WDT/USB-gated disarm to agent's
fix4. build-fix4-wdt.py retains the exact three-file struct-bio ABI correction
and deferred ICE/UFS sources, compares every existing fix4 and plain-integ
export CRC, and audits actual module imports from both compatible vendors.
The fix4 parent boot's ramdisk, DT payloads and both cmdline fields remain
byte-identical. Its matching wrapped vendor is copied privately with updated
kernel digest markers and the existing logger/disarm overlay. No CRC editing,
forced module loading or modversions disabling is used.

The WDT variants map the existing APSS watchdog at0xf017000 at early_initcall,
using the board DT's fixed32764Hz clock; register layout matches qcom-wdt.
There are20 counter bits, so a single330-second hardware interval is invalid.
Instead the hardware interval is30seconds and only the deadline's exclusive
FIFO keeper pets every8seconds, strictly before the absolute boot deadline.
If the timer CPU, scheduler or keeper stops, hardware bites within30seconds
of the last pet. If the keeper keeps running while a different CPU/TZ wedges,
it stops by the absolute300-second cutoff: reset no later than deadline+30.
Normal qcom-wdt/core/user start, ping, timeout changes and stop cannot reset,
shorten or disable that interval. Boot PM cannot suspend it. No generic worker
can extend it. Only after boot_completed1 AND actual USB configuration, PON
is cleared/read back, the exclusive timer/keeper end and the configured normal
watchdog service resumes; normal
watchdogd/core behavior and suspend semantics apply after that handoff.

USB disarm correction: the existing init trigger's boot_completed write now
latches a request; it does not itself disarm. The built-in UDC core reports
USB_STATE_CONFIGURED directly from usb_gadget_set_state, under its state lock,
before the asynchronous sysfs notification. This is the actual state shown by
/sys/class/udc/*/state, independent of sys.usb.state or adbd service properties.
The deadline's existing dedicated keeper checks both current USB configuration
and boot completion under hw_lock before handing off. Either event may happen
first. A reset/disconnect before the handoff revokes USB readiness. If UDC
never binds or FunctionFS never configures, the original absolute deadline and
hardware watchdog remain armed even after sys.boot_completed=1. No new init
polling service, userspace readiness write, policy rule or generic workqueue
is needed. The sysfs boot_completed getter remains0 until actual disarm. Once
both prerequisites have caused handoff, later cable removal does not rearm it.
build-usb-disarm.py refreshes all three WDT packages in a staging directory;
config, existing export CRCs, ramdisk, device-tree and cmdlines are preserved.

SPMI is not available at early_initcall. The normal PON probe writes/readbacks
bootloader reason2 at PM6125 offset0x88f, mask0xfe (shift1), exactly as the
reboot-mode driver does, as soon as the ordinary PMIC regmap exists. SCM probe
returns EPROBE_DEFER before any SCM work until that write is acknowledged.
Thus the PON reason is armed before qcom-scm's convention/TZ/ICE/QSEECom calls.
This uses SPMI/regmap, never SCM. Disarm clears only the reason bits. A hang
before PMIC/SPMI initialization has hardware coverage but no proven fastboot
destination. Hardware/firmware may override a requested reset destination.

Expiry programs a2.25-second hardware bite BEFORE the emerg marker/flush.
The dedicated thread queues emergency_sync, waits2seconds for logger fsync,
then programs a128ms bite directly. If that software path stalls, the already
programmed hardware cap still applies. No kernel_restart, emergency_restart,
SCM, reboot notifier or PMIC write occurs on the expiry path. An armed-boot
panic programs a6-second hardware backstop for panic=5/dump completion.

DOWNLOAD/EDL LIMIT: download_mode is forcedOFF and cannot be enabled on A9,
but actually disabling firmware dload/SDI requires SCM on this DT. There is no
documented nonsecure direct disable register here. The WDT arms BEFORE those
secure calls, so if firmware is already in dload/SDI mode and its OFF call
hangs, the bite may enter ramdump/9008/EDL. This is explicitly allowed by the
user as preferable to a dead splash; FASTBOOT IS NOT GUARANTEED in that case.
The secure OFF request's rc is logged when it returns, and is not evidence of
an early direct-MMIO guarantee. The currently hung phone cannot receive this
new kernel without coordinator recovery; the worker performs no phone access.

The old parked package used OPEN_TIMEOUT180 + host ACK, synchronous UMH/storage
receipt waits and helper-error panics. Its exact fast-reset cause is unproved.
These variants keep OPEN_TIMEOUT0, no host ACK/receipt prerequisite, and the
bounded asynchronous logfix1 ramdisk. The8-second pet owner starts with the
early hardware arm, so healthy slow userspace/logging cannot starve it.
The old software-only explanation below remains historical implementation
detail for the non-WDT builds. WDT host build/ABI/policy/packed-image checks
do not establish physical reset destination or hardware timeout behavior.

Hard-hang selftest: plain-wrapped-fix3-wdt-selftest-20261022/boot.img, built by
build-wdt-selftest.py, keeps a9_deadline=300 and adds the primary-header opt-in
a9_deadline_selftest=hang. At late_initcall_sync, after checking the watchdog
and PON are armed, asynchronous IPIs spin every online CPU with IRQs disabled;
the initiating CPU also spins with IRQs disabled. The keeper, software timer,
init and restart paths cannot run. Expected hardware bite is within30seconds
of the last pet, rather than the300-second software deadline. The first and
all-CPUs-entered emerg markers distinguish injection from an earlier hang if
pstore survives firmware reset. This happens before PID1, so no new early
logger directory is expected; logs from older boots do not prove this test.
The normal WDT images remain unchanged; selftest code is inactive unless the
explicit cmdline opt-in is supplied. agent alone tests the hardware.
Reported by agent: the dedicated hard-hang selftest reached fastboot and the
runner returned to Android A. USB-gated disarm is a subsequent host-only change
and needs its own phone smoke; that earlier result does not prove this change.

b-try detects05c6:9008 with bounded lsusb and reports HANG detail=edl after B.
With exactly one9008 device it invokes only /home/user/A9/scripts/edl reset,
using the exact prog_firehose_ddr_001360E1.elf loader and a60s timeout/5s kill
cap, inheriting the runner USB lock. No EDL storage commands are issued.
At most one reset is attempted per run; its output/status are saved under
edl-reset.log/rc, then normal fastboot set_active a / reboot / Android-A check
continues. HANG detail=edl is retained if recovery fails, with slot_A=0 and an
explicit stderr failure. Multiple9008 devices refuse reset. Unknown registry
serial fallback also accepts a single9008 when no adb/fastboot is attached.

Source/build only; agent owns phone execution. No phone or fake-phone test is
performed by these build scripts. b-try.sh is installed at
/home/user/A9-build/mainline/b-try.sh for the coordinator's single smoke.

Kernel: init/a9_boot_deadline.c, obj-y, late_initcall. a9_deadline=N seconds
defaults to300;0 disables. Root must write exactly1 to
/sys/module/a9_boot_deadline/parameters/boot_completed. The shared vendor rc
does so only on sys.boot_completed=1. Atomic state prevents a late completion
from canceling expiry. Adbd visibility does not disarm it.

An hrtimer wakes a dedicated SCHED_FIFO kernel thread, independently of init,
watchdogd and shared workqueues. Expiry prints A9_BOOT_DEADLINE EXPIRED, queues
emergency_sync, gives the logger3seconds to fsync, then kernel_restart("bootloader").
A second hrtimer calls emergency_restart after15seconds if orderly device
shutdown wedges. The existing PON beacon2 covers that fallback and panic.
SCM download_mode starts0 and enabling dload is refused on this A9. Critical
deadline/panic5/dload0/PON2/wrapped-key arguments live in the primary boot-header
cmdline because this ABL can ignore extra_cmdline. Existing DT/DTBO unchanged.

The original parked package's exact fault is not proved. Coordinator bisect
isolated fast reset to the combined reliability package. Its kernel early
logger synchronously waited for storage and a UMH helper before late_initcalls,
panicked on any helper/receipt/logging error, and fsynced each initcall; it also
changed the hardware watchdog to a finite bootstrap lease. This implementation
uses none of those hooks, receipts, per-initcall writes or ACK/lease changes.
Plain deadline retains the proven plain-integ normal WDT. Wrapped deadline
retains wrapped-fix1's original WDT with guard0.

Persistent logs: a static PID1 shim forks the static logger, waits at most3s
for its mounted, fsynced log directory, and execs the byte-original Android
init. A storage hang cannot extend that wait; the independent logger keeps
running. No storage operation runs in PID1 or a kernel initcall. Logger mounts
sysfs/proc and finds the exact PARTNAME=kdebuginfo ext4 block device without
waiting for Android by-name links. It mounts it at /kdebuginfo in its private
root before metadata/vold/keystore/QSEECom userspace. The logger cannot capture
a kernel hang before PID1 starts. Existing reserved ramoops remains available
for that earlier phase, with retention still dependent on firmware.

Only mainline/counter and mainline/boot-NNNNNNNNNN/ are written/pruned. Stock
kmonlog_*, power_reason.txt, edpd and Magisk a9/ are untouched. Kernel stream
uses /dev/kmsg (equivalent live dmesg); logcat starts once Android logdr exists
and runs in Android's root. Files fsync at most every2seconds under light load
and immediately upon the deadline marker. Each stream rotates at8MiB, with
one previous file; older mainline boot directories are pruned at about50MiB
total or when the partition has less than21MiB free. Root0600 logs remain
private. Filesystem failure cannot block PID1 or the reboot thread. A stuck
storage path can still lose the last marker; restart never waits indefinitely
for logging. No fallback TEST extent is used.

After SELinux loads, the logger adopts the existing a9ml_rootsh diagnostic
domain. Companion vendor policy adds kernel setcurrent and the transition
to that existing domain, plus an exact sysfs label reusing vendor_init's
existing boot-control write permission for the completion node. It also permits
the inherited kernel-owned descriptors and the private tmpfs /kmsg reader and
rootfs /a9-log-kmsg writer, including the short kernel-domain policy handoff
window. SwitchRoot deletes the writer's pathname but leaves its open descriptor
and rootfs inode type intact. No new permissive domain or global enforcing
change. These explicit allows avoid recurring kmsg AVCs even when the existing
diagnostic domain is permissive, and preserve this access when it is enforcing.
Before policy load, the logger explicitly adopts the initial kernel SID and
reopens its context fd under that identity; otherwise a pre-policy exec's
SID_INIT becomes Android's unlabeled SID and cannot make the later transition.
The early ext4 mount uses no context= option: SELinux rejects such options
before policy initialization. After the domain transition only our mainline
files are labeled vendor_data_file, which has an existing filesystem associate
permission; fscreate labels future files. Stock labels remain untouched.
PID1/logger startup and failure markers go to kmsg for ramoops diagnosis.
prepare-vendor.py recompiles the exact input
vendor CIL with the cached matching platform inputs and preserves all labels,
owners, modes, timestamps and existing platform hash sidecars.

b-try.sh accepts any matching boot/vendor pair, including the agent fixed
wrapped vendor. Before staging it, prepare-vendor.py makes a private derivative
with the disarm trigger, logger transition, and current kernel digest markers.
It preserves the original vendor image, flashes only vendor_b from TWRP dd,
then boot_b and one B activation. Supplied vendors must already contain modules
compatible with the selected kernel. The coordinator's push-retry change and
explicit A9_BTRY_FORMAT=1 option are retained for the initial test-data format.
Without a vendor argument, an already prepared compatible vendor is required.

Observation ends at deadline+120s: B boot_completed=1 =>PASS, returned fastboot
or automatic A fallback =>HANG, no usable transport =>NOUSB. EXIT/INT/TERM all
attempt fastboot set_active a, reboot and wait for A boot_completed1. Logging
is collected after that return, with a temporary TWRP visit if A lacks root,
then another checked return to A. Stdout contains one result line; progress
and newest kernel/logcat tails go to stderr. Registry unknown-* falls back
only when exactly one adb/fastboot transport is attached. Every tool wait is
bounded. SIGKILL/host death and a fully dead kernel cannot be fixed by a host
script; deadline coverage requires the kernel timer to remain live.

Return A waits at most480s, ending early if A enters recovery (after a15s
reboot-disconnect grace). If this run successfully formatted data before B,
or A entered recovery, one repair is authorized: enter TWRP, require slot A,
format data, run rebootsystem.sh, adb reboot, then wait once more for A.
The repair is attempted at most once across all return/log-collection paths;
its command output is saved in return-a-repair.log. No format occurs solely
for an ordinary A timeout in a run without FORMAT. Bootloader recovery reboot
is best effort on this ABL; unavailable USB still returns slot_A=0. If A
remains unbootable but TWRP is available, collect kdebuginfo/pstore anyway.
Missing collected logs in an older return_A_failed run do not establish that
the early logger failed: the old runner skipped pull_logs when A failed.

A9_BTRY_HOLD=N keeps observing B for N additional seconds after completion,
then requires recent B completion evidence before PASS. Use N=360 for a
300-second deadline disarm check. Fastboot or A fallback during hold is HANG.
Pstore is mounted and archived separately during collection (including TWRP),
even if mainline/ is absent; no records are unlinked. Its tails go to stderr.
Both kernels have PSTORE, PSTORE_RAM, PSTORE_CONSOLE and PSTORE_PMSG built in;
DT reserves 1MiB at 0xb0580000 with 256KiB record/console/pmsg regions. The
primary header now includes pstore.kmsg_bytes=262144, including wrapped boots.
panic=5 overrides CONFIG_PANIC_TIMEOUT=0. Firmware hard resets can erase DDR,
and visiting Android A before TWRP adds another reboot; an empty pstore is not
proof that no panic occurred. No unproven warm-reset firmware path is enabled.

A9_BTRY_ONB=/absolute/executable runs a host hook once after B completion,
inside the hold. A positive A9_BTRY_HOLD is required; the hook consumes that
hold budget. Its stdout/stderr are saved to onb.log, status to onb.rc, under
the run logs directory. Hook failures/timeouts report HANG with onb_failed_rc
and retain boot_completed=1; a checked return to A is still attempted.
The hook gets ADB/FB, selected ANDROID_SERIAL/A9_SERIAL, A9_HOME,
A9_BTRY_IN_HOOK=1, A9_BTRY_LOGS (absolute), and A9_BTRY_BOOT. The timeout owns
the hook process group; signal cleanup stops it before starting the A return.
There is no runner USB polling during the hook, preserving natural idle.
After it exits, the runner checks B completion and observes any remaining hold.
Kernel boot deadline is already disarmed; this is a host test timeout.

plain-idle.sh [minutes, default10] and plain-eink-ab.sh select themselves as
hooks. Idle now defaults to idle2-20261022 + plain-integ vendor; E Ink retains
the phone-proven plain-deadline-logfix1 + row28 pair.
Unmodified r22e has neither deadline nor early logger. The selected plain-integ
lineage already contains the R22e battery, USB fixes and E Ink feature controls.
Override the pair via A9_PLAIN_BOOT/A9_PLAIN_VENDOR with compatible images.
Idle defaults to minutes*60+300 seconds hold; E Ink to1200. Evidence goes under
idle/ or eink-ab/ in the b-try logs directory. The optical camera endpoint and
photographs/contact-sheet behavior are retained; optical quality is unscored.
The native root helper defaults to the debug console nc127.0.0.1:2324. For B
with existing root, A9_BTRY_ROOT=su or adb selects those transports. Root native
commands verify uid0 and slotB and carry an explicit exit receipt. Framework
am/input/settings/svc/dumpsys calls use ordinary adb shell, never app_process
from the root console. A rootless A is handled by b-try's existing TWRP log
collection. Neither hook performs its own boot/flash/slot/return operations.

idle2-20261022 is the plain-integ fence-fix + logger/USB-gated WDT candidate
with PSCI CPU idle/domain enabled and cpuidle.off removed. Its DT changes only
the two cluster domain-idle-states selection lists and a chosen profile: WFI
and SM6115 CPU-PC0x40000003 remain, no shared cluster idle state is selected.
QCOM_STATS is built in. The newly enabled DT helper stays builtin-only, with
no new module export; no existing export is removed or CRC value edited.
The full kernel+modules build's actual modpost rows match the parent exactly;
the delivered Module.symvers uses the parent row order (the raw generated
table is retained). Both delivered symvers files are byte-identical to the
fence-fixed parent. Existing original and audio-merged vendor import audits
pass. This is host preparation, not measured power/PSCI acceptance.

Idle2 captures a45s awake control (nine signed current samples), then natural
idle with no host polling or sampler wake timer. Before/after snapshots record
actual qcom_stats/RPM/RPMh files, per-CPU state usage/time/rejections/s2idle,
power_supply current/charge_counter/status/USB presence, suspend/wake counters,
PSCI startup logs and CLOCK_BOOTTIME/MONOTONIC/CNTFRQ from a tiny static native
helper. Trace clock is boot. Summary separates firmware tick/count deltas,
kernel CPU accounting, timekeeping and logical s2idle. UART/printk and the
original stayon setting are restored on exit. Framework tools use adb shell.
No app_process runs on the root console. The current A9 PMI632 battery driver
has current_now but no charge_counter: if no other supply exposes one, phase
average current remains N/A. Awake endpoint samples never claim suspended
current; positive current means battery charging, and USB charging prevents
treating net battery flow as total phone power. Exact900s b-try command and
hook/assets are in /home/user/A9-build/mainline/idle2-20261022/README.txt.

Idle2 hook fix1 logs every stage to stderr, identifies/tails its failure file,
and executes the clock helper as a setup preflight. Native transport failures
retain captured output, timeout output and unique failed-command scripts;
cleanup cannot overwrite the first failure. The phone attempt on2026-10-23
did execute setup and awake sampling: its failure was adb rc255 at idle entry,
not helper execution. Baseline/trace setup now finishes before screen-off.
Only the final framework screen-off request tolerates adb disconnect255 or
timeout124; the quiet window continues and final same-boot snapshots/trace
must still validate. Other command failures remain fatal. No USB polling or
sampling wake timer is added to the idle window.

2026-10-22 logger correction artifacts are packaged separately as
plain-deadline-logfix1-20261022 and plain-wrapped-deadline-logfix1-20261022.
Their kernel Image.gz payloads are byte-identical to the previous deadline
builds. The earlier plain hardware boot passed completion but produced no
logs; wrapped returned fastboot at about60s, cause still unproved. The normal
timer is300s in both. These corrected packages need agent's hardware smoke.

Artifacts: plain-deadline-20261022/{boot.img,boot-hang.img,vendor.img}; wrapped
boot artifacts live in plain-wrapped-deadline-20261022. boot-hang.img uses60s
and intentionally holds PID1 after spawning the logger, so Android cannot
complete. Default boot.img uses300s. All have SHA256 sidecars. Do not confuse
the intentional hang image with the normal feature-test boot.

Builds use the original worker-owned incremental cache. build.py kernel starts
from the proven plain-integ cache; build-wrapped.py kernel then applies the
existing reviewed Stage2 ICE + Stage3 dm-default-key source patches and wrapped
configuration. Both preserve existing module export CRCs. Reconstructing the
cache to repeat that sequence is separate from merely repackaging ramdisks.
No fake-phone suite is needed. agent's one real smoke establishes actual reset
destination, marker persistence, Android completion disarm and A return.

idle-c2-20261023 is a DT-only cluster sleep candidate on the proven idle2
kernel. build-idle-c2.py restores both native SM6115 cluster domain-idle-states
lists from idle2's saved parent DT and updates the chosen profile. It preserves
the exact kernel/module ABI, cmdline, ramdisk/logger/WDT and FIX2 hook. The
early PC-mode DENIED message precedes successful OSI selection; C2 does not
force PC mode or copy stock system-pc bits into upstream PSCI parameters.
All ignore-unused flags remain for this isolated test. The native topology
has no separate stock system-pc/notify-rpm path; firmware/system votes remain
an open blocker if cluster states alone do not increase RPM counts.
Package REPORT.md has the inventory, exact900s b-try command, stock4.19
Magisk-root control commands and recovery limits after boot-deadline disarm.
Pass criterion: vmin OR vlow Count increases during idle and resume/A-return
succeed. Runner PASS or CPU-PC alone does not meet system-sleep acceptance.

C3 uses build-idle-c2.py --profile c3: C1 plus silver GDHS0x40000022 only,
no gold shared-state selection or either cluster-PC selection. Kernel/ABI,
cmdline and ramdisk are unchanged. Package idle-c3-20261023 has the boot and
exact manual-collection900s hold command, with A9_BTRY_ONB empty. No harness
changes. Its pull-c2-crash-log.txt documents b-try's TWRP archive transport
adapted to select the original C2 boot by exact boot_id instead of newest.
The original C2 test changed boot IDs before final collection; the missing
trace instance is not evidence that the original quiet window never suspended.
Stock's early-setup USB control also had zero vlow/vmin entries. An increment
remains positive system-mode evidence, not a mandatory parity gate for that
specific condition; zero counts alone do not demonstrate hardware collapse.

Retained-log pull after recovery MTP enumeration (integ23 follow-up):
Wait for adb get-state to report recovery continuously across ten seconds of
polling, resetting the window on disconnect, error or another state. Recheck
after creating the remote tar and before each exec-out cat. The existing
stock-A su collection path instead requires stable device state. Readiness
waits are bounded at120 seconds; each transfer is bounded at60 seconds.
Both pstore and kdebuginfo use at most three transfer attempts, ten seconds
between failures. Each attempt truncates its private partial file. A validated
tar is published atomically, and the remote tar is removed only after local
extraction succeeds. No reboot/flash/format/archive-creation retry is added.
Host fake-state/time regression: python3 -B tests/test-b-try-pull.py.
