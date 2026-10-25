# Offline ADSP D0 preparation

These tools prepare one private E Ink + GPU + Bluetooth + thermal + ADSP tuple.
They do not authorize deployment. D0 remains attended and requires independent
review of the **final** tuple, service dispositions and harness. No PCM route,
APR, FastRPC, LPASS LPI, MPSS/CDSP or Wi-Fi start is admitted.

## Reproduce without network or phone access

Use branch `adsp` (which merges main at `db8bad50a35dd63ffe4538eeb025e987e7d381f3`).
The base kernel is the local composed source at
`~/A9-build/mainline/linux-7.2.10-gpu`, commit
`ab3d710164c746028ff2045e01e2e7b9d610f5ab`. The reviewed PAS patch stays outside
the default patch series. All build outputs must be a new direct child of
`~/A9-build/mainline/adsp`; never choose `$A9_DATA`. The recipe refuses a fresh
build below 12,000,000,000 available bytes on `/`. No downloads are needed.

From this worktree, with an unused output name:

```sh
OUT=$HOME/A9-build/mainline/adsp/d0-20261017
nix-shell --option substitute false --option builders '' --option substituters '' \
  scripts/mainline/kernel/shell.nix --run \
  "python3 -I scripts/mainline/adsp/build-d0.py '$OUT' --jobs 8"
python3 -I scripts/mainline/adsp/stage-firmware.py \
  "$OUT/firmware/qcom/sm6115/hisense/hlte556n" \
  --mcopy /nix/store/ldv4awl1v5kk1q4dafm1mq1bmr1fmsj1-mtools-4.0.49/bin/mcopy
nix-shell --option substitute false --option builders '' --option substituters '' \
  scripts/mainline/kernel/shell.nix --run \
  "python3 -I scripts/mainline/adsp/freeze-d0.py '$OUT'"
python3 -I scripts/mainline/adsp/verify-packaged.py "$OUT"
mkdir -p "$OUT/audit/system-image"
debugfs -R "rdump / $OUT/audit/system-image" \
  "$A9_DATA/roms/a16/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v7.img"
python3 -I scripts/mainline/adsp/audit-services.py \
  --system "$OUT/audit/system-image" \
  --vendor "$OUT/android/work/vendor-stage-bt" \
  --ramdisk "$OUT/android/work/ramdisk/root" --output "$OUT/audit/final"
# Review every finding and write a private JSON map keyed by file, each with
# the exact audit sha256 and an evidence-based reason. Unknown hashes refuse.
python3 -I scripts/mainline/adsp/admit-services.py \
  --audit "$OUT/audit/final/services.json" --dispositions "$OUT/dispositions.json" \
  --snapshot "$OUT/snapshot" --output "$OUT/service-admission.json"
nix-shell --option substitute false --option builders '' --option substituters '' \
  scripts/mainline/kernel/shell.nix --run \
  "python3 -I scripts/mainline/adsp/make-bundle.py '$OUT' --services '$OUT/audit/final/services.json'"
```

The recipe uses only cached Nix/NDK/AIDL/Cuttlefish/Mesa/container inputs. The
thermal headers/libraries, GPU staging/probe and frontlight module have private
outputs. The GSI and golden FAT modem image are opened read-only, without mounts.
Firmware extraction verifies the golden modem hash and all 39 unchanged files
against retained provenance, stages lowercase request paths, and writes a private
size/hash manifest. No firmware, compiled DT, Bluetooth address, host key or
private audit payload belongs in Git.

`build.json` binds all private kernel source files, config, Image, symvers and
modules. `module-crcs.json` verifies **every imported symbol CRC**, including the
external frontlight, against the common Module.symvers; vermagic is supplementary.
`snapshot/SHA256SUMS` binds the selected closure. Both baseline and ADSP DTs are
built by the common kernel build. The five-property delta and stock reservations
are checked before image packing. The real kernel boot image passes ABL emulation.
The snapshot keeps PAS and its providers in `d0/modules`, outside the normal
vendor module directory. Boot, vendor and system carry no PAS module, D0 init
service or automatic PAS loader. `modules.d0` is a separate explicit sidecar
list. Default aliases/softdeps remain empty.
The MDT provider is shared with the GPU closure: the harness reuses only a
provider already in the exact admitted baseline instead of inserting it twice.
PAS itself must always be absent before D0 begins.

## Service and protection-domain admission

The only proposed owner is the kernel `qcom_pd_mapper`, with QRTR, QRTR-SMD,
QMI and PDR providers loaded before PAS. Its SM6115 table maps `avs/audio` to
`msm/adsp/audio_pd`, instance 74. The mapper auxiliary device is created by the
remoteproc prepare callback, and its successful driver binding establishes that
the local QMI server was registered. No userspace pd-mapper service is installed.
The JSONs remain provenance, rather than a second mapper's configuration.
The Wi-Fi coordinator must reuse that same owner if Wi-Fi is added later;
that would be a different tuple and needs a new admission. Wi-Fi is absent here.

The static audit inventories every regular file and symlink in the actual
ramdisk, system, system_ext, product, vendor and odm namespaces. It also extracts
and inventories **every packaged system/vendor APEX payload**, preserving import
lines and hashes for all findings. Updated APEXes/overlays, Magisk modules, a real
persistent `/data`, extra modules and arbitrary privileged tools invalidate the
admitted scope. This tuple uses the existing RAM-backed data boot recipe.

Admission must disposition every finding by exact file hash, resolve init import
properties from this tuple, inspect module lists/options/aliases/softdeps and
all start/recovery/PDR callers, and verify the selected module closure's undefined
symbol references. Generic init/toybox module-loading functionality alone is not
an automatic loader. String searches are conservative evidence, not a proof of
arbitrary native program behavior. An unreviewed finding or extraction failure
is NO-GO. `admission.pending.json` is deliberately rejected by the host.

## Harness contract and time limits

`d0-host.py` has no default adb, device discovery, push, reboot or flash action.
A future coordinator must explicitly select a transport/serial, pre-stage the
reviewed bundle and unchanged firmware and supply an independently accepted
receipt, including named fallback ownership. Do not run it during offline prep.
The receipt must explicitly admit `root_transport: "loopback"` (or `"adbd"`
for a separately reviewed root-adbd image). There is no automatic fallback or
`adb root`. Existing mainline measurements show adbd UID 2000 despite the v7
debug properties; the debug-only vendor already provides `a9ml_rootsh` on
127.0.0.1:2324 after boot completion. No production image/property changes are
needed. The old review-63b receipt does not admit this adapter.

Supply `--root-transport loopback --transport-id ID --serial SERIAL --boot-id UUID`
along with the existing arguments. Select ID from the coordinator's exact
serial's current `adb devices -l` entry after boot, then keep it fixed through
staging and D0. Every call uses `adb -t ID`, checks `get-serialno`, and sends a
non-PTY script through `adb shell -T 'nc 127.0.0.1 2324'`. Inside that root
shell, UID must be 0 and boot ID must match the original staging boot before
the target command executes. Target stdin is `/dev/null`. A fresh random
protocol marker carries the target's actual exit code; nc/adb success alone
cannot certify completion. Unexpected prefix/trailer output or absent status
fails closed. Raw, streaming evidence is in `*.transport.log`; verified target
output is in the ordinary `*.log` after completion. All serial checks fit
inside the existing per-call deadlines. Disconnect/reset never selects a new
transport, boot or root channel. Containment uses the same pinned adapter.

For coordinator staging only, the adapter's `Transport.execute(argv, seconds,
log)` runs an explicitly supplied command through those same checks. It does
not stage files from the harness CLI. The coordinator runbook is
`/home/user/A9/runs/adsp-d0-runbook.md`; its transport revision remains pending
independent re-review before any device action.
The host passes its original boot ID to run; the target requires that ID before
binding/starting. Both consume a durable boot ledger before binding/starting;
changing tuples does not permit another attempt in the same boot. A failed or
ambiguous load/start consumes the attempt. No reload, recover or retry path exists.

The target requires a matching manifest, actual live tuple hashes, RAM-backed
data with no updated APEX/Magisk modules, exact baseline
loaded-module list, no competing mapper/rmtfs/FastRPC service, no PAS module and
no previously published remoteproc. PAS is inserted last with
`a9_adsp_manual_start=1`. Identity comes from the OF node/compatible, with exactly
one remoteproc accepted. Successful publication and driver binding, effective
read-only parameter, one diagnostic and initially disabled recovery/coredump must
all agree before the explicit start. Incorrect initial recovery is refused before
any corrective write. Firmware absence is never a start gate.

| Operation | Bound |
|---|---:|
| Host boot identity read | 5 s |
| Each target insmod completion wait | 8 s |
| Log marker acknowledgement / diagnostic | 5 s each |
| Explicit start completion wait | 12 s, one write only |
| ADSP GLINK/rpmsg readiness | 10 s |
| Running observation after readiness | 180 s |
| Explicit stop completion wait | 12 s |
| OFFLINE/no-restart observation | 10 s |
| Independent target dead-man from before module loading | 250 s |
| Host transaction deadline | 290 s |
| Host containment after failure | 18 s, one call |
| Host child terminate/kill grace | 2 s each |
| Guardian wait for executor seal and writer completion after abort | 14 s |
| Executor wait for guardian result | 16 s |

All fault paths stop observation. Continuous marked kernel logs must stay live;
loss/overflow, reset/crash/SSR/auth/SMEM/SMMU/thermal/display fault, changed boot ID,
unexpected state, lost rpmsg, enabled recovery/coredump, or dead-man activation
fails the run. E Ink must remain closed; any measured thermal zone >=70 C aborts.
The marker and full baseline are retained privately. Production dwell is fixed;
only the explicit offline rehearsal branch shortens it to 2 s plus 1 s OFFLINE.
Observation uses monotonic uptime, with one extra second to compensate for
integer flooring. A 15 C rise from baseline also aborts; display counters and
all three E Ink gates must remain healthy/closed.

HUP/INT/TERM latch abort and exit with status 129/130/143. EXIT cleanup seals
all further dispatch and explicitly preserves the status (phone mksh can turn
an implicit EXIT-trap return into zero). Cleanup is idempotent and cannot resume
module insertion or start. It kills only external log readers with KILL. No
forked phone shell is sent TERM: Android mksh can re-raise it to its process group,
as reproduced in `runs/thermal-rc143-fix.md`.

Each load/start/stop operation gets a journal directory before launch. A detached
writer records completion atomically only after its command returns. Time limits
bound the executor's wait; they never kill a writer or infer cancellation. The
run executor is the sole dispatcher. Containment latches a request and waits;
it cannot perform its own competing stop or certify an OFFLINE sample. The
original executor must seal dispatch and **every** registered writer must return
before the guardian can inspect state and conditionally stop a running processor.
This prevents containment from finishing between pre-start health and a later
start, including the interval when the kernel still reports OFFLINE during boot.
No redundant unconditional shutdown or power-reference decrement is introduced.

A fresh-session guardian, with stdin closed and file-only output, starts before
module insertion. Guardian and writers ignore HUP/INT/TERM, including transport
process-group signals. The guardian never depends on the transport, a killed PID,
or a timeout as proof of operation completion. Its 250 s dead-man requests abort.
After 14 s without a seal and complete writers, or a failed/bounded stop, it
records `CONTAINMENT-UNVERIFIED` and an **unacknowledged** attended PON2/Power and
golden-restore request. The host records the named fallback owner. No physical
fallback is claimed executed. The guardian stays active on this unacknowledged
request; a later sealed/completed operation can receive one conditional stop and
separate late-result evidence, never upgrade the original failure to success.
An executor killed before sealing cannot be certified by a later OFFLINE read.
A stop writer already pending is observed to completion, never retried.

A timeout or killed process does not prove SCM/TZ or a D-state syscall stopped.
Without completed operations and verified OFFLINE the result remains
`CONTAINMENT-UNVERIFIED`; all attempts are consumed. The named attended coordinator
owns PON2/Power fallback and golden restore. The harness never automatically
reboots, reloads PAS or invokes recovery. The receipt stays PENDING until a new
independent review accepts the corrected exact bundle and fallback ownership.

## Offline rehearsals

```sh
python3 -I tests/mainline-adsp/test-host.py
python3 -I tests/mainline-adsp/test-transport.py --sysroot "$A9_PHONE_SYSROOT" \
  --qemu /nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64
python3 -I tests/mainline-adsp/test-phone.py --sysroot "$A9_PHONE_SYSROOT" \
  --qemu /nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64
python3 -I scripts/mainline/adsp/test-manual-start.py \
  --cc /nix/store/95k9rsn1zsw1yvir8mj824ldhf90i4qw-gcc-wrapper-14.3.0/bin/gcc
```

Phone rehearsal uses the unchanged extracted `/system/bin/sh`, toybox and linker
under qemu-user. Applet wrappers re-enter qemu because no ARM binfmt handler is
installed. Invoking the phone linker directly with the copied phone libraries
avoids qemu `-L` redirecting `readlinkat`'s root directory into the library sysroot.
Only hardware/transport effects are simulated; no fixture establishes real PAS
authentication, IRQ concurrency, GLINK service health or reliable hardware stop.

The phone suite includes actual mksh process-group TERM and returning-trap
negative controls, terminal HUP/INT/TERM at four execution phases, a concurrent
pre-start containment request, delayed start past the guardian deadline, executor
death with a surviving writer, and transport process-group HUP/TERM. Pending
writers deliberately leave the synthetic state OFFLINE. Late completions cannot
produce successful containment or a successful run.
