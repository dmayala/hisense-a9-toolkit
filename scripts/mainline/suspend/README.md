# A9 s2idle POC (B3, 2026-10-20)

Status: the coordinator's k4-observable boot delivered the flags live
(`ramoops`, `console_suspend=N`, watchdog guard enabled), but `rtc` had no
completed result and reset to bootloader within about30s. Booting the same
candidate immediately yielded **empty pstore**, including no console record.
There is no usable ramoops retention across this observed reset path. Suspend
entry and the reset cause remain unknown. Use the returning tests below next.
The coordinator runs phone commands. This does not enable automatic suspend by
default. It prepares a bounded test of the earlier USB-loss failure on the
integrated E Ink + Wi-Fi image.

## Returning pm_test bisect: host BEGIN/END, no partition writes

`phone-test.sh pmtest <level>` adds a single returning debug test. **k4 cannot
run it yet:** its config has `# CONFIG_PM_DEBUG is not set`, so it has no
`/sys/power/pm_test`. The helper refuses that condition before stopping
watchdogd or arming the watchdog. An opt-in debug kernel is necessary; adding
the helper alone cannot enable this sysfs interface.

The conservative-to-less-conservative order is **freezer → devices → platform**.
Linux7.2.10 explicitly rejects `processors` and `core` for s2idle in
`kernel/power/suspend.c:enter_state()`. They are deeper suspend-to-RAM test
boundaries, not extra s2idle stages. The existing A9 watchdog guard also
forbids deeper suspend. Both helper and host refuse these levels before a test;
do not switch to `mem`/deep sleep or relax the guard to obtain a five-stage list.

| Level | Returning boundary on this s2idle path |
| --- | --- |
| freezer | Freeze/thaw tasks; no device suspend callbacks |
| devices | Normal device suspend callbacks, then device resume |
| platform | Also late/noirq device/platform preparation, then resume; stops before `s2idle_loop()` |
| processors/core | Unsupported for s2idle; no test or deeper sleep attempted |

No existing partition has been approved for durable markers. In particular,
`kdebuginfo` is not a spare merely because it sounds like a logging target.
No userdata/persist/modem/misc/GPT or other block device is opened or written.
Phone `/data/local/tmp` holds **volatile** per-run logs/control files only.
`pmtest-host.py` exclusively creates a private local HOST log, flushes/fsyncs
each record and fsyncs its directory. Its protocol is:

1. The helper takes a RAM-file flock, requires `[none]` in `pm_test` and the
   default test delay in1..5s, opens its kernel-log reader and performs the
   existing watchdogd handoff/30s watchdog arm. It sends a fresh random token.
   It does not arm/change RTC, feed the watchdog again or release the awake lock.
2. The host commits **BEGIN** before sending the token-matched **GO**. Only
   then does the helper select/read back the level and write `freeze`.
3. On return, the helper checks for the kernel's `suspend debug: Waiting for`
   message in records acquired after GO, restores/readbacks `pm_test=none` and
   reasserts the awake inhibitor. A successful write alone is insufficient.
   Lost/incomplete kernel records or a failed write yields a failed test.
4. The host commits **END** with that result before sending a fresh matching
   **ACK**. Only this ACK permits watchdog disarm/magic-close and watchdogd
   restart. A returned-but-failed test may ACK safe cleanup, then exits nonzero.
   Unrestored/uncertain tests receive no cleanup ACK. Missing/wrong GO or ACK
   leaves the watchdog deadline intact. No launch/GO/ACK is resubmitted.

The host compares boot identity in memory without logging the raw ID. A new
boot, terminal failure or timeout exits nonzero; the runner never reboots,
restores, retries or advances a stage. A durable BEGIN without END identifies
the last **admitted** stage, not proof that the kernel reached its boundary.
Retain host logs and ordinary private kernel diagnostics between returned
stages. Empty pstore is not an alternate stage marker.

### Host preparation (no phone access)

Build just the new static helper:

```sh
python3 -I scripts/mainline/suspend/build.py \
  --out ~/A9-build/mainline/suspend-a6l-20261020/pmtest/final helper
```

For the coordinator's next image, prepare an isolated debug kernel with the
existing builder; budget remains15GiB free plus2GiB when reusing source:

```sh
python3 -I scripts/mainline/suspend/build.py --pm-debug \
  --source ~/A9-build/mainline/video-20261020/power-kernel/kernel \
  --out ~/A9-build/mainline/suspend-pmtest-kernel-20261020 kernel
python3 -I scripts/mainline/suspend/build.py --no-vendor \
  --out ~/A9-build/mainline/suspend-pmtest-kernel-20261020 package
scripts/mainline/build-integrated.sh --audio --cellular --suspend-observable \
  --kernel-build ~/A9-build/mainline/suspend-pmtest-kernel-20261020 \
  --out ~/A9-build/mainline/integrated-pmtest-20261020
```

`--pm-debug` enables only `PM_DEBUG` and its dependent `PM_SLEEP_DEBUG`;
`pmtest.config` records the profile. Automatic boot-time suspend, advanced PM
debug and the panic-based DPM watchdog stay disabled. The builder still
requires identical exported module CRCs and kernel release; a mismatch stops
packaging and needs matching modules, not an assertion bypass. The snapshot
now records the actual debug config. Kernel/image/module-hash overlay and helper
are paired through the normal integration. Do not substitute a new kernel into
k4 while retaining stale kernel-hash metadata. The worker built the helper and
resolved the config, not a full kernel/integrated pair or module-ABI proof.

The `observable-boot --image PATH --boot INPUT --config CONFIG` host-only step
can also insert a separately validated kernel while preserving INPUT's newer
audio DT and ramdisk; matching module/hash/vendor/snapshot packaging is still
the coordinator's responsibility. It does not establish ABI compatibility.

### Coordinator sequence, stopping on the first failure/reset

Boot the debug/helper pair through the original exclusive slot-B owner, with
the same watchdog guard and observable flags. Keep its awake inhibitor held.
Require `/sys/power/pm_test` contains `[none]`, and
`/sys/module/suspend/parameters/pm_test_delay` is1..5 (default5). Pause other
keeper/adb polling for each test; this host runner supplies its own protocol.
The existing watchdog qualifications and restore owner remain in force.

Use a fresh HOST directory on local disk (these are coordinator commands):

```sh
umask 077
EVID="$HOME/A9-build/mainline/suspend-a6l-20261020/pmtest-phone-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$EVID"
bash scripts/mainline/suspend/phone-test.sh pmtest freezer --log "$EVID/freezer.log" &&
bash scripts/mainline/suspend/phone-test.sh pmtest devices --log "$EVID/devices.log" &&
bash scripts/mainline/suspend/phone-test.sh pmtest platform --log "$EVID/platform.log"
```

The `&&` chain stops at any nonzero return, including the first reset/timeout.
No stage is run twice. Do not append a real `rtc`/sleep automatically after all
three. `processors`/`core` are not runnable next steps on this guarded s2idle
kernel. On failure use only the original owner's recovery route and return
the last host BEGIN/END, helper failure marker and callback category. Once
freezer passes but devices resets, for example, device callbacks become the
next localization target; it is not proof of one specific driver.

If the updated helper is instead staged as the POC executable
`/data/local/tmp/a9ml-suspend-pmtest`, add
`--helper /data/local/tmp/a9ml-suspend-pmtest` to each command. The coordinator
must restage/chmod it after any reboot because phone `/data` is RAM. This still
requires a PM_DEBUG kernel and suitable execution policy. No phone binary is
installed by the host builder.

## Observable integrated retry

The stock DT already reserves **1 MiB at `0xb0580000`** for ramoops, with
`record-size`, `console-size` and `pmsg-size` each `0x40000`. R4 and the delivered
B3 kernel already have `CONFIG_PSTORE`, `CONFIG_PSTORE_RAM`,
`CONFIG_PSTORE_CONSOLE` and `CONFIG_PSTORE_PMSG` built in. Do not allocate a
second region or rebuild that kernel merely to enable them.

The follow-up explicitly enables that same DT node, retaining its allocation
and record offsets, and selects its existing write-combined mapping. The boot
adds `no_console_suspend`, `pstore.backend=ramoops`, `pstore.kmsg_bytes=262144`
and `loglevel=7`. The console ring records normal kernel messages continuously;
it does not depend on a panic dump to capture the last suspend stage. The
kernel's `console_suspend_all()` normally suspends this console too, so
`no_console_suspend` matters for a hang after userspace has frozen. This is a
diagnostic boot setting, with a possible effect on suspend timing.

From an existing integrated pair (the example retains the AW8695 fix):

```sh
scripts/mainline/build-integrated.sh \
  --update-suspend ~/A9-build/mainline/integration-aw8695-20261020 \
  --out ~/A9-build/mainline/integration-suspend-observable-20261020
```

This compiles only the static helper and repackages boot DT/cmdline and the
vendor helper. Kernel, ramdisk, module ABI, all other vendor payloads and the
matching snapshot's kernel/modules remain unchanged. Use a fresh output path.
The normal integrated build also applies these settings, including when
`--kernel-build` reuses an older B3 build.

### Round 7: primary cmdline and A6L leads

Normal integrated builds now finalize the cumulative boot after all feature
packaging, before updating its snapshot. `--suspend-observable` is enabled by
default; `--no-suspend-observable` skips that final step, and `--without suspend`
also skips it. Skipping the step does not remove settings inherited from an
input boot or from B3 packaging. The explicit `--update-suspend` route continues
to apply observability and update the helper. No full kernel rebuild is needed
when reusing `video-20261020/power-kernel`.

The stored j boot **already contains** all five flags above, but
`no_console_suspend`, `pstore.backend`, `pstore.kmsg_bytes` and `loglevel` are in
the boot header's `extra_cmdline`, after its primary494-byte string. The helper's
reported refusal is consistent with that overflow not reaching the kernel;
this is not proof of the exact ABL loss mechanism. The repacker now places all
five flags, all Android storage/slot/recovery arguments and the other critical
settings in the primary512-byte field. Only `log_buf_len`, `printk.devkmsg` and
an override identical to the kernel's built-in schedutil default may overflow.
They remain present for readers using both arrays. A different governor remains
in the primary field. This fixes the actual layout rather than appending the
same flags to an already overflowing string. The coordinator must still verify
`console_suspend=N` and backend `ramoops` at runtime before `rtc`.

For a boot-only host check, without rebuilding the helper/vendor/integrated
composition:

```sh
python3 -I scripts/mainline/suspend/build.py \
  --boot ~/A9-build/mainline/integrated-20261020j/boot.img \
  --config ~/A9-build/mainline/video-20261020/power-kernel/snapshot/config \
  --out ~/A9-build/mainline/suspend-a6l-20261020/j-observable observable-boot
```

A6L references were read as data through `gh api`, pinned to
`5d9ae7ed60a544d159ed78483ca67fd51673b569`:

- [CCI async-suspend finding](https://github.com/PaddleStroke/hisense-a6l-lineageos/blob/5d9ae7ed60a544d159ed78483ca67fd51673b569/docs/sleep-async-workaround-20261006.md):
  SDM660 CCI must retain its MMSS NoC/SMMU clock votes while its GT9769 actuator
  parks. [Its overlay](https://github.com/PaddleStroke/hisense-a6l-lineageos/blob/5d9ae7ed60a544d159ed78483ca67fd51673b569/device/hisense/a6l/kernel/a6l-camera-v75.dtso)
  explicitly consumes `mnoc_ahb`, `smmu_ahb` and `smmu_axi` before CCI clocks.
- [CPU-idle row](https://github.com/PaddleStroke/hisense-a6l-lineageos/blob/5d9ae7ed60a544d159ed78483ca67fd51673b569/docs/port-status.md)
  and [policy](https://github.com/PaddleStroke/hisense-a6l-lineageos/blob/5d9ae7ed60a544d159ed78483ca67fd51673b569/device/hisense/a6l/rom/init/init.a6l.cpuidle.rc):
  its TZ rejects retention/cluster encodings from mainline sdm630.dtsi; disabling
  those states avoids the s2idle retry loop. A6L sysfs state numbers are not
  portable to A9.

Neither finding justifies the same writes on the present A9 boot:

| Lead | A9 evidence and action |
| --- | --- |
| CCI clocks | Stock `/soc/qcom,cci0` at `0x5c1b000` lists only `cci_0_clk`, `cci_0_clk_src`. Stock `/soc/apps-smmu@0xc600000` is a different SMMUv500 topology. Current `cameras/a9-cameras.dtso` supplies CCI's `GCC_CAMSS_TOP_AHB_CLK` and `GCC_CAMSS_CCI_0_CLK` and its TOP GDSC; it has no lens actuator or lens-park callback. The A6L `mmcc` clocks do not exist in this SM6115 clock provider. Keep the A9 graph and async policy; no copied clocks or global `pm_async=0` workaround. |
| CPU states | Stock `/soc/qcom,lpm-levels/qcom,pm-cluster@0` has `pwr` and `perf` clusters; both CPU children offer WFI mode1 and PC mode3, while L2 modes2/4 and system mode3 need separate coordination. Current j/power-kernel has `cpuidle.off=1` and **ARM_PSCI_CPUIDLE disabled**, so its s2idle cannot select the A6L rejected PSCI states. It uses architectural WFI. The existing `smp/a9-smp-c1.dtsi` already implements the CPU-only next step by removing cluster domain-idle-state lists while keeping `0x40000003` CPU PC. Reuse it with a separately enabled/qualified PSCI-idle kernel; do not apply A6L state1/3/4/5 disables. |

The stock evidence is local
`~/A9-build/mainline/sol-pmic/evidence/base_0.{dtb,dts}` (CCI lines14841ff,
SMMU8718ff, LPM8817ff). Applying the stored `dtbo_0.dtb` to that base leaves
these CCI clock and CPU-mode properties unchanged. Mainline CPU/cluster
encodings are in cached `demo-integrated-20261019/kernel/arch/arm64/boot/dts/qcom/sm6115.dtsi`.
Stock DT describes the requested modes; it cannot prove which composite
arguments the current TZ accepts. The earlier `PSCI_SET_SUSPEND_MODE(PC)` DENIED
discussed in [mainline-smp.md](../../../docs/mainline-smp.md) concerns mode
selection, not rejection of CPU PC. A9's matching deep-state rejection remains
**unestablished**. No additional CPU-idle or camera DT change is made here.

After a returned test the coordinator can read only each CPU's cpuidle state
`name`, `usage`, `time`, `rejected`, `disable` when present. On this kernel those
PSCI states are absent; do not call that proof of CPU power collapse. A future
C1 run needs successful `psci_idle` initialization and increasing PC residency
on all eight CPUs with no growing rejections before evaluating deeper states.

`a9ml-suspend` mirrors its `PM ...` stdout protocol to `/dev/kmsg`, prefixed with
`a9ml-suspend:` and kernel timestamps. It records startup, ramoops availability,
watchdogd stop/ownership, RTC arming, the freeze write, return duration, RTC
status, host ACK, watchdog release, and watchdogd restart. It refuses a new
blind attempt if kmsg cannot be opened or the ramoops backend/live console is
missing. The bound-driver watchdog discovery incorporates main `e724352`.

The helper uses Android's `ctl.stop watchdogd-gsi` itself and waits for
`init.svc.watchdogd-gsi=stopped` before opening the hardware watchdog. If that
service was active, ordinary pre-suspend failure and successful cleanup restore
it with `ctl.start` and wait for `running`. Successful RTC/key cleanup still
requires the existing host ACK before releasing the helper's watchdog. The
`watchdog=disarmed` marker concerns the helper's ownership; watchdogd then
rearms its normal watchdog. An initially stopped/absent service stays stopped.
Deliberate watchdog-expiry modes keep the original reset behavior; a failed
resume cannot restart userspace services, and init starts its service on the
next boot.

After an unexpected reset, the coordinator should boot **this candidate again
through the existing owner**, then copy `/sys/fs/pstore/console-ramoops-0`
(or `console-ramoops`), `dmesg-ramoops-*` and `pmsg-ramoops-*` into private host
evidence immediately, before another reset or a different OS/recovery boot.
The supplied GSI init already mounts pstore. If it is unmounted, mount it with
`mount -t pstore pstore /sys/fs/pstore`. Read/copy records; do not unlink them.
Do not rely on `/data` for the previous boot. A file read during the test is
previous-boot data, not a live view of the current console ring.

Before the retry, confirm `ramoops` in
`/sys/module/pstore/parameters/backend`, `N` in
`/sys/module/printk/parameters/console_suspend`, and ramoops registration in
private dmesg. Use the existing `phone-test.sh rtc` within the exclusive phone
session; no separate manual watchdogd stop is needed. On return verify the
service is running if it was running before. On reset retain full pstore logs
privately; return only PM markers and the failing kernel stage to the worker.

`PM entering=s2idle` proves the helper reached the freeze write, not that the
kernel finished entering sleep. Kernel PM messages and `A9 PM guard: watchdog
retained in s2idle` narrow the boundary. `PM returned=s2idle` establishes a
returned syscall; the existing duration/RTC/host ACK checks still decide the
test result. A hardware bite need not emit a panic or `dmesg-ramoops` record,
which is why the continuous console is needed. Persistence across this exact
reset/bootloader path remains **hardware-unverified**; empty records alone do
not prove where the failed attempt stopped. Avoid a cold power cycle before
retrieval.

Sources: local stock DT `sol-pmic/evidence/base_0.dts` at the ramoops node;
matching cached kernel `fs/pstore/{ram,platform}.c` and
`kernel/printk/printk.c`; [kernel ramoops documentation](https://docs.kernel.org/admin-guide/ramoops.html)
and [kernel command-line parameters](https://docs.kernel.org/admin-guide/kernel-parameters.html).

## Source findings

- `vendor/etc/init/hw/init.hlte556n.rc` holds `a9ml_test_awake` from boot and
  sets `stay_on_while_plugged_in=7`. These deliberately block normal screen-off
  suspend after the earlier screen-timeout attempt lost USB.
- `a9ml-deadman.sh` is a userspace sleep loop. The freezer stops it during
  suspend. Its uptime/freshness checks catch expiry **after a successful thaw**;
  it cannot reset a phone stuck asleep. `poc/keeper.sh` touching `a9-stay` over
  adb does not change that. Pause the coordinator's keeper/other adb polling
  during the actual sleep interval to avoid USB traffic causing early wakes.
- The unmodified `qcom-wdt.c` stops an active watchdog in its late suspend
  callback and starts it in early resume. This is another gap in recovery.
  The existing `parity-next/wake/qcom-wdt-a9-s2idle-guard.patch` is reused
  byte-for-byte here. With the A9-only boot flag `qcom_wdt.a9_guard=1`, it
  retains the armed watchdog through s2idle, does not refresh it at resume,
  and refuses deeper suspend or suspend without an armed watchdog.
- SM6115 KPSS uses a 20-bit watchdog counter at the board's 32764 Hz sleep
  clock: the driver maximum is about **32 s**, so a 60 s RTC alarm cannot be
  protected by this simple watchdog path. This POC uses RTC **+10 s**, WDT
  **30 s**, no feeding after arming. No timeout extension worker is added.
- R4 already enables the PM8xxx RTC and its driver installs an alarm wake IRQ.
  The PM8941 power-key driver enables its IRQ for wake by default. No DT
  change is indicated by these sources. Hardware alarm/key wake is unproven.
- E Ink already has a PM notifier that drains/parks the worker and turns off
  HV before the freezer, then unparks after resume. DWC3/QCOM and ath10k have
  PM callbacks. Their presence does not prove working resume. The retained
  R4 config has `CONFIG_ARM_PSCI_CPUIDLE` off; do not apply the separate
  October 18 CPU-collapse result to this boot or call s2idle deep sleep.
- A6L reference `README.md` explicitly lists adb dropping at every suspend.
  Its USB documentation and PM diagnostics concern a different SoC/kernel
  composition and include failed USB/GPU resume attempts. They provide no
  demonstrated A9 USB fix to transplant.

The direct `freeze` write intentionally keeps the Android awake inhibitor
held, bypassing the Android wakeup-count handshake for exactly one suspend.
This prevents unplanned automatic repeats after resume. It is a kernel/driver
POC, not proof that Android autosuspend policy works. Neither the shell
dead-man nor a successful offline build establishes autonomous recovery.

## Host build

From `fix/suspend`, based on `07b9875`:

```sh
python3 -I scripts/mainline/suspend/build.py kernel
python3 -I scripts/mainline/suspend/build.py package
```

Outputs: `~/A9-build/mainline/suspend-20261020/boot.img`,
`vendor/vendor.img`, SHA256 sidecars, `artifacts.json`, `kernel-command.json`
and logs. Images stay local; vendor contains the original private modem seeds.

The builder reuses the integrated kernel recipe and cached toolchain from
`demo-integrated-20261019` / `parity-next/wake/build.py` in new, independent
source links and independent object copies. Patched watchdog and GCC files are
private regular files; cached sources remain read-only. The GCC patch enables
VCODEC0 hardware control for Venus, matching stock and upstream SM6115. Config,
release and symbol CRCs must match the integrated base. Boot packaging replaces
the kernel, appends the guard flag, recalculates header SHA1/offsets, and keeps
R4's ramdisk/recovery DTBO bytes. It also applies the observable ramoops DT
and boot parameters described above. No Wi-Fi MAC is added to the DT.

Vendor is built by the main checkout's current
`scripts/mainline/android/build-vendor.sh --refresh-integrated --out ... --overlay ...`.
This retains the integrated composition and incorporates the merged runtime
MAC/ADSP/GPU fixes. The overlay installs `bin/a9ml-suspend` and updates the
module/thermal kernel hash pair. The exact make argv and artifact hashes are
saved with the output. No shared kernel, image, snapshot or phone owner file
is edited.

## Coordinator phone test

Use the existing exclusive slot-B owner and normal restore route. Boot the
candidate boot/vendor pair through that owner; keep its matching integrated
snapshot (new `Image.gz` and module kernel-hash file). The two image paths above
are the only image substitutions. Leave E Ink and Wi-Fi configured through
the current installed services; do not stop sensors as a suspend workaround.

First qualify RTC and fallback separately, with E Ink gates closed:

1. `bash scripts/mainline/suspend/phone-test.sh watchdog-awake` deliberately
   expires the hardware watchdog. Expect **bootloader within 30–40 s**, then
   use the original owner's restore/reboot path. A timeout without observed
   bootloader is a failed qualification; do not advance on the assumption
   that the watchdog works.
2. On the candidate again,
   `bash scripts/mainline/suspend/phone-test.sh rtc-awake` should finish in
   about 10 s with `PM rtc_event=alarm`, `PM cleanup=...watchdog-disarmed`,
   `PM_RC=0`. It refuses an already enabled RTC alarm rather than replacing
   another owner's alarm. The alarm is based on RTC time, not host Unix time.
3. `bash scripts/mainline/suspend/phone-test.sh watchdog-s2idle` deliberately
   enters s2idle with a **15 s watchdog and +20 s RTC backup**. Expect the
   watchdog to reset before the RTC deadline. This is the first suspended
   watchdog qualification, so hardware recovery cannot be promised beforehand.
   If RTC or an unrelated IRQ wakes the phone first, its log says so; a later
   awake reset alone does not prove the timer ran while asleep. Likewise, an
   expiry during slow suspend preparation does not prove asleep counting.
   Inspect the private PM log and elapsed timing; never call ambiguity a pass.

Then open E Ink normally, connect Wi-Fi, and run:

```sh
bash scripts/mainline/suspend/phone-test.sh rtc
```

Expected: screen off; `PM entering=s2idle`; roughly 10 s asleep;
`PM rtc_event=alarm`; adb reconnects; the host writes an ACK; only then does
the helper restore the awake lock/alarm and disarm the watchdog. Success is
`PM result=returned-and-host-acknowledged` plus `PM_RC=0`. Early/non-RTC wake
is reported as failure. The host stays quiet for 12 s, then polls for at most
25 s (roughly 40 s total including launch). ADB can be set via
the `ADB` environment variable; it must be the coordinator's existing route.

Power-key test (requires a person): same command with `key`; press the physical
key about 3 s after suspend entry. RTC +10 remains the backup. A key-mode
result with an RTC event fails the key-specific test; a non-RTC wake still
needs the private IRQ/wakeup log to attribute it to the physical key.

After every returned test: wake the UI (`input keyevent KEYCODE_WAKEUP` via
the existing root shell), force a shade expand/collapse, verify frames advance
and `rail_fault=0`/`stall_fault=0`; check Wi-Fi reports only the extracted
`Supplicant state: COMPLETED`, and run the existing sanitized HTTP 204 check.
Observe for at least 60 s for delayed USB/GPU failure. Retain raw kernel logs
privately and return only the failing PM callback/IRQ category to the worker.
Do not print full Wi-Fi status or device identifiers.

## Recovery and remaining work

If adb does not return, **do not ACK**: the hardware watchdog remains armed.
After the separately verified timeout, expect bootloader and run the existing
owner's `android-boot.sh --restore` route to slot A. The software dead-man may
also fire after thaw once the keeper freshness/hold expires, but it provides
no deadline while frozen. This branch does not change the dead-man.

If the first watchdog qualification fails, there is **no proven unattended
fallback**. Stop suspend testing. Power held 10–15 s is the documented manual
recovery for a phone invisible in all USB modes; the coordinator follows the
existing recovery ladder. Do not promise that any untested candidate cannot
strand the phone.

Next changes depend on actual results: localize a failed callback or USB
resume path from the same boot; fix it; then test ordinary Android screen-off
autosuspend by releasing `a9ml_test_awake` and changing plugged-in stay-on
policy. This guarded boot deliberately refuses automatic suspend once the
helper disarms WDT, so it is not a production suspend policy. Full screen-off,
power-key/RTC, E Ink, Wi-Fi and USB parity remains pending phone evidence.
