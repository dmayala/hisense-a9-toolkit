# Manual slot-B measurements

Run these only after the coordinator releases the phone, with B already booted
and its existing root nc endpoint at 127.0.0.1:2324. These scripts never flash,
reboot, enroll fingerprints or set a PIN. `ADB` and `ANDROID_SERIAL` select the
existing transport. Output defaults to a new directory in the current folder.

```sh
scripts/mainline/manual/hvenc.sh /home/user/A9-build/mainline/integ-20261024-28
scripts/mainline/manual/drain.sh /home/user/A9-build/mainline/integ-20261024-28
```

HVENC stages the shipped synthetic `qualify` test and its AVC/HEVC inputs.
CPU_READ is set to 1 through ordinary shell. Mapped EOS stays on, with
bidirectional buffers N then Y, earlier output/work options off. Each invocation
creates new codecs and retains its stdout, rc, MP4 if produced, and dmesg.
Bounded dmesg/logcat readers stream to host files during the attempt, retaining
partial output if USB dies. They finish before nc is reused for native controls.
A clean codec failure of N does not skip Y; transport loss ends the script.
The third argument selects `N`, `Y`, or the default `NY`. For the next owner-run
comparison, after preserving the reset logs, use **Y only** on the existing image:

```sh
bash scripts/mainline/manual/hvenc.sh /home/user/A9-build/mainline/integ-20261024-28 /home/user/A9-build/mainline/manual-hvenc-integ28-Y Y
```

That selects the already shipped stock-style bidirectional buffer mappings
before creating new codecs. It is a candidate fix requiring a device result.
EXIT restores the CPU property and all four
kernel options; the remote synthetic files remain for inspection.

Drain enables Wi-Fi (connect it beforehand if association is required), disables
USB stay-awake and sleeps the screen. It measures 60 minutes using the
suspend-safe `drain_snapshot` and BOOTTIME/MONOTONIC clock pair. Raw samples,
wakeup sources, framework state, charge delta and suspend residency are saved.
A capacity at/below 35% stops the measurement, leaving margin above 30%.
Charging is restored first on EXIT/INT/TERM. A detached host watchdog restores
it after parent death or 65 minutes, including repeated writes covering a late
bounded pause command. The existing kernel 35%/70-minute restoration remains.
Do not run another charging-control test concurrently. Host rehearsal uses fake adb and Android mksh under qemu, shortening only the
fixture measurement interval; it never contacts a phone. Physical calibration and transport behavior still need the owned run.

## Pull after the manual encoder reset

While B is still up, run from the main checkout:

```sh
bash scripts/mainline/manual/hvenc-pull.sh /home/user/A9-build/mainline/hvenc-reset-integ28-pull
```

This tries the early logger's **existing private mount** through
`/proc/<pid>/root/kdebuginfo/mainline`; it does not mount sda9 or change policy.
Actual access on enforcing integ28 is unverified. If access is denied, the script
prints the recovery command and exits. After the current phone owner releases
the device and you enter TWRP, use a fresh output directory:

```sh
bash scripts/mainline/manual/hvenc-pull.sh /home/user/A9-build/mainline/hvenc-reset-integ28-recovery --recovery
```

Recovery mode uses the same ext4 journal-replaying mount as b-try's pull.
The script never reboots. `mainline.tar` contains the last three saved boots'
`info.txt`, `kernel.log`/`.prev`, `kernel.early.log`, `logcat.log`/`.prev`, and
`pstore.previous.log`. The third boot covers a possible intermediate restart.
Use `mainline-files.txt` to see exact boot numbers; preserve the crashing boot
and its successor, whose `pstore.previous.log` describes its predecessor.
`pstore.tar` contains the current raw `/sys/fs/pstore` directory when readable;
the B root domain may deny it even if saved logs were retrieved successfully.
It includes `console-ramoops-*`, `dmesg-ramoops-*`, and `pmsg-ramoops-*` when
present. No records are erased. `current-dmesg.log` and `current-boot.txt` describe
the running successor and must not be mistaken for the crashed boot.

The old `scripts/a9-bootlog.sh pull` reads Magisk's `/a9` directory, not this
early logger's `/mainline` directory. Neither an empty pstore nor boot reason
`reboot` identifies the initiating fault. A reset followed by B returning about
a minute later and the deadline disarming at 106s is compatible with an ordinary
new boot; the disarm is not evidence that the boot deadline caused the reset.
Keep raw archives private; extract to a separate local directory for analysis.

## Why integ28 never attempted encoding/drain

The retained failed encoder script exits at line 7, setting
`debug.stagefright.a9.enc_cpu_read` from `a9ml_rootsh`. The compiled integ28 policy
denies that domain `debug_prop:property_service set`, while allowing `shell`.
All five variants therefore stop before staging; their parameter writes and
slot/budget checks passed. The drain's failed_phase=4 script exits at
`svc power stayon false`; `svc` launches app_process, whose `zygote_exec:file
execute` permission is denied to `a9ml_rootsh` and allowed to `shell`.
These are framework/property commands routed to the native root context.
The scripts above reserve nc for native controls and use ordinary shell for
framework calls, without widening policy or changing the sweep harness.

The front photo has a separate retained boundary: 515 warm-up frames, phase 4,
no JPEG and a timeout; rear still and both videos passed. No retained evidence
connects that camera timeout to the two context failures.
