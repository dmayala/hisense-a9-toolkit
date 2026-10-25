# STAB: incident attribution and service/evidence repairs

Host-only work against saved sweeps dated 20261024. No phone commands, new
image, runtime qualification or changes to the live integ18 sweep. Initial
base main68ac010; rebased again before final host validation and ff-only merge.
`A_stability` remains **unproven** until an owned coordinator run passes it.

## INTEG26 follow-up

[Attribution and fixes](../../../runs/integ26-stability-20261025.md): two real
phone ANRs in framework Binder startup, zero online observation loss, separate
logger exec127/incomplete capture gaps, and nonfatal camera SIG35 diagnostics
in the older retained boot. Remove the blanket cleanup/MTP verdict vetoes on
independent restart/USB rows. Check Android shell interpreter readiness before
crash capture and retain numeric exec errno. No causal repair or PASS is claimed
for the two ANRs: the remote server-side owner is absent from retained traces.

[Integ26/27 phone follow-up](../../../runs/phone-anr-integ26-27-20261025.md)
identifies the replacement's premature startup ANR: persistent restart reuses
the process record without cancelling the old soft/hard bind timers. A pinned
framework repair clears those timers before restart; a private SYSTEM image
passes host checks. Both builders accept `--phone-anr-system INPUT_GSI`.
The initial receiver-registration/DeviceConfig Binder cause is still open;
radio callbacks already precede the ANRs. Android16 omits server traces for
ANRs in its first ten minutes, even when the ANR queue latency is only18ms.
The lifecycle repair does not qualify stability or repair that first wait.

## Current integ24 attribution (STAB4)

[Ranked incident report](../../../runs/integ24-stab4-20261024.md):252 retained
phone ANRs (a2546a9 already on main/in25) and one camera watchdog cascade killing
the provider, cameraserver and two IPA workers (5ca3a30 shutdown repair). The
reported65 resident ANR files and118 crash/rotation observations are not unique
incident counts. Two additional provider-labelled SIG35 records are nonfatal
pre-exec child FD-cleanup diagnostics, now repaired to match Android11 bionic
fork behavior. SystemServer Memtrack startup waits receive an early existing-
service start; other dump sections are not invented crash processes. Fresh
camera HAL build and host checks are ready; physical A_stability PASS is unproven.

## Each retained incident

Sources below are `boot-*/logcat.log.prev` under
`A9-build/mainline/sweep-integ{13b,14,17-f1}-20261024`. Times are the saved log
clock; they are not a correction of the device date. Full trace payloads and
native tombstone files are absent from all three archives. The phone wait
attribution combines the named ActivityManager records, the same processes'
repeated HIDL waits, and the saved `radio_wait=1` summary; it is not a claim
that individual missing stack files were recovered.

| Run / boot | Log line / time | Process | Reported reason and supported cause |
|---|---|---|---|
|13b /104|17854 /16:02:28.227|com.android.phone|SafetySourceReceiver REFRESH_SAFETY_SOURCES broadcast timeout; its initializing main thread is waiting for IRadio/slot1.|
|13b /104|20911 /16:02:32.034|com.android.phone|Failed to complete startup; replacement process still waits for IRadio/slot1.|
|13b /104|28907 /16:02:44.898|com.android.phone|Failed to complete startup; IRadio/slot1 wait.|
|13b /104|31284 /16:02:48.566|com.android.phone|Failed to complete startup; IRadio/slot1 wait.|
|13b /104|37647 /16:03:01.044|com.android.phone|Failed to complete startup; IRadio/slot1 wait.|
|13b /104|38893 /16:03:04.563|com.android.phone|Failed to complete startup; slot1 then slot2 wait. Deadline elapsed before both registrations; last report is96ms after slot2 registration.|
|14 /107|17682 /16:02:28.261|com.android.phone|SafetySourceReceiver REFRESH_SAFETY_SOURCES broadcast timeout; IRadio/slot1 wait.|
|14 /107|19938 /16:02:32.126|com.android.phone|Failed to complete startup; IRadio/slot1 wait.|
|14 /107|28722 /16:02:44.692|com.android.phone|Failed to complete startup; IRadio/slot1 wait.|
|14 /107|30893 /16:02:48.332|com.android.phone|Failed to complete startup; IRadio/slot1 wait.|
|13b /104|11194 /16:02:20.852|com.google.android.tts|Java crash in application creation/StartupAfterPackageReplaced; nested RuntimeException: cannot access credential-protected data from direct boot.|
|13b /104|12239 /16:02:21.706|com.google.android.apps.wellbeing|Same direct-boot credential-protected-data exception during application creation.|
|14 /107|12249 /16:02:21.346|com.google.android.tts|Same direct-boot credential-protected-data exception.|
|14 /107|14386 /16:02:23.029|com.google.android.apps.wellbeing|Same direct-boot credential-protected-data exception.|
|17-f1 /114|stability-evidence.json: trace_files2|system_server|Two retained diagnostic files; both samples only identify this process. Blocked methods, filenames and subjects were discarded. Their individual causes and whether they are watchdog/pre-watchdog dumps or ANRs are unknown.|
|17-f1 /114|acceptance.json: crash_or_rotation_observations1|No crash process established|Exactly one ANR-file metadata signature change, zero event/tombstone changes. No saved am_crash, fatal death or native tombstone establishes a separate crash.|

Event records in13b/14 repeat at the sweep's reboot index; repeated retained
buffer snapshots and process-summary observations are not additional unique
incidents. Their original measurement boots are not retained separately in
these archives. In17-f1, boot115 is the A-return recovery boot, not boot114's
measurement; its phone ANRs must not be attributed to the feature sweep.
Boot114's IPA fdsan DEBUG record at logcat.log:5646 is debuggerd signal35, and
the same process continues afterward. It is not evidence of process death.

## Stock comparison and repairs already on main

Stock `etc/init/hw/init.qcom.rc:516` runs `qcom-c_main-sh` in class main.
`bin/init.class_main.sh:94,118` explicitly starts disabled qcrild services,
selecting the second from multisim configuration. It does not wait for boot
completion. Cached sources are under `storage-row29-lun4-verdict-20261021/configs/
daily-vendor-reference/`. No REA binary decompilation is needed for these init
facts. Stock's Android11 framework differs from this Android16 GSI; the source
comparison does not establish identical Telephony startup timing.

RADIO2 **639358d** already repairs the observed queueing defect in13b/14:
direct owner start follows firmware preparation in post-fs-data, and verified
profile copying is asynchronous. Both radio slots still require verified
preparation, cellular opt-in, serving owner and Android-ready. In13b they
registered45.231/45.712s after the first retained wait. In14 registrations are
at logcat33254/33607,16:02:52.042/.715, after its last timeout. Neither image
contains RADIO2. Boot114, which does contain it, has no retained phone ANR.
This is supporting evidence, not proof of all future startup ordering.

The Google exceptions are direct-boot app failures, not native crashes or
radio HAL deaths. Both older sweeps report failed real-data mount and tmpfs
fallback. In17-f1 row29 verifies real f2fs, file encryption and wrapped ICE;
its retained feature logs contain neither Google fatal exception. Existing
FBE handoff fix **1f7783e** and the integrated crypto work address that separate
path. This does not justify disabling apps, skipping their exceptions or
pretending that the older runs passed. Stock init supplies normal encrypted
user data; the tested tmpfs fallback is not evidence of stock's same behavior.

The enforcing shell `runtime_event_log_tags_file:file map` denial in17-f1
kernel3036/3947 is already fixed by **7a71799**. The current read/open/getattr/map
grant is compiled and checked again, including write denial. Read/open alone
was insufficient. Twelve lost observations include native7/events5/window5;
these categories overlap. The camera's unintended hardware-encoder dispatch
and transport failure are addressed separately by7a71799/4e868cf. No claim is
made that the map grant alone repairs all twelve losses.

## New service-order repair

All three saved kernels show the declared AIDL
`android.hardware.power.stats.IPowerStats/default` unavailable during early
system_server startup, followed by repeated failed ctl.interface_start calls.
For17-f1 these span kernel1552–1699, about62.1–77.6s. The packaged power APEX
contains the statistics binary and manifest, but its class-hal service
`vendor.power.stats-default` has no lazy-start interface. System init starts
zygote before the boot event's class_start hal, allowing the framework to
request it first. Stock's power HAL is likewise a class-hal service but does
not ship this Cuttlefish AIDL statistics declaration.

`android/early-stats.rc` starts that existing statistics service directly in
post-fs-data, after platform APEX-config parsing and before queued zygote-start.
It adds no replacement service, fake registration or power-control command.
Full and incremental vendor builds consume the action. Incremental packaging
preserves the base hardware RC's data, USB, domain and deadline customizations.
This repairs an observed service-readiness gap; the missing system_server
traces do **not** prove it caused those two diagnostic files. No ANR timeout,
watchdog setting or framework error handling is relaxed.

## Evidence retained by the next sweep

`stability-traces.jsonl` now retains typed summaries from ANR and text native
tombstone files: per-sample file ordinals, fixed diagnostic filename category,
byte size, completeness/read status, process sections, Java method names,
fixed thread categories and simple native library/symbol names. It also records
radio/startup/powerstats/direct-boot hints. A hint anywhere in a multi-process
trace is not proof that the first process was blocked there. Neither file
ordinals nor deduplicated summaries are unique-incident counts.

Paths, filenames, command arguments, timestamps, PIDs/UIDs, source line numbers,
addresses, broadcast extras, exception messages and arbitrary thread names are
not exported. Native protobufs and symlinks are skipped. Each file prefix is
bounded to256KiB,128 frame symbols and128files per capture; prefix/frame/file
limits are explicit. All process headers in the prefix are retained. Partial
summaries survive read failures, and diagnostic failures fail the observation.
The8192-summary cap is explicit in stability-evidence.json. Data stays in0600
files in the existing private owning output directory. The policy adds only
getattr/open/read on text tombstones, retaining write denial.

Fixed event cause codes preserve startup1, SafetySource broadcast2 and
direct-boot exception3;0 means unclassified. Missing or invalid snapshot fields
now identify an incomplete observation even if both commands returned0. USB
readback failure has its own loss category. Initial retained ANR/tombstone
artifacts cannot pass merely because an event read returns no records.
The existing crash/rotation metric remains conservatively failing; it is not
renamed into a count of proved crashes.

Validation artifacts: `A9-build/mainline/stab-host-20261024/`. Five focused
STAB groups cover cached Android shell/toybox/awk execution and host decoding,
watchdog-vs-ANR categories, multiple process sections, tombstone symbols,
symlink/protobuf skips, truncation/privacy, partial reads, empty-event startup
artifacts, incomplete fields and pre-zygote statistics ordering/packaging.
Final validation also runs the existing five stability and five acceptance
groups, the full host init verifier including the existing power APEX services,
and a compiled-policy allow/deny check. No device lifecycle is started.

Next coordinator consumption: rebuild current policy and acceptance source
assets, include the early statistics action through the updated vendor recipe,
and collect the next owned run. An actual A_stability PASS and the two missing
system_server causes remain open. Existing sweep evidence is immutable.

STAB HOST DONE — runtime A_stability PASS pending coordinator evidence.

## Integ18 follow-up

The saved `stability-events.jsonl` contains two distinct retained `am_anr`
records, both naming **com.android.phone**. It contains no crash event.
`stability-evidence.json` aggregates ten reads naming that process with
`startup_timeout=1, radio_wait=0`; ten reads are not ten ANRs. The old collector
did not retain the events' reasons or per-file blocked stacks, so neither
individual ANR can yet be assigned an exact blocked method. A zero aggregate
radio hint does not prove the radio was ready before phone startup. RADIO2 is
present in integ18, so its earlier repair alone cannot close this new result.

The reported `crash_or_rotation_observations=2` counts an initial nonempty
baseline plus one ANR-file signature change. The saved signature-change
counters are `anrs=1, crash_events=0, tombstones=0`. This does **not** establish
two crashes. Eleven lost samples include records whose native/events readers
both returned0, but the old report omitted the missing/invalid-field category.
The end of `onb.log` also records actual offline native-push failures after
hardware AVC qualification. No successful final snapshot exists (`anrs=-1`,
`tombstones=-1`). It is not possible to attribute all losses to the event-map
denial, nor to pass the run by removing the failed samples.

The new typed trace summaries and incomplete/diagnostic loss categories from
**8802f17** were not in integ18. Its init statistics repair was also absent.
Reuse the passing host policy/init/collector evidence in
`stab-host-20261024/checks.json`; that commit still needs actual consumption
and runtime qualification. Missing old system_server payloads from integ17
cannot be reconstructed by the new collector.

The user-prioritized integ18 work was merged first:

| Item | Commit | Established result |
|---|---|---|
| Display admission |29136ac|Restore omitted E Ink firmware in rebuilt, hash-pinned acceptance bundles. No gate/power change.|
| MTP |1fa8f14|Measure USB before disruptive hardware encoder qualification; distinguish a failed mute prerequisite from an attempted MTP session.|
| Rear still |da1422d|Repair the app/host deadline mismatch and retain atomic partial camera-stage evidence. Exact runtime cause remains open.|
| Rear AF/torch |72a365d|Stock-backed implementation scope; pre-existing driver/graph/HAL gaps, still unimplemented.|

Details and integ12/17 comparison:
[INTEG18.md](INTEG18.md). Host artifacts and rebuilt APK/firmware bundle:
`A9-build/mainline/integ18-followup-host/`. Five acceptance groups, five
partial/result groups, actual JVM timeout/callback replay, Android-shell USB
request tests, APK signature/alignment and final asset/source pin checks pass.
No phone operation, new image, encoder implementation or qseecom/FBE change was
made by this worker.

The integ18 `mainline.tar` path appeared during investigation but was still
zero bytes at this report milestone. Preserve the owner's output and inspect
the completed archive when available for the two phone startup ANRs and rear
still timeout. Do not infer an exact runtime cause from an empty archive.
`A_stability` remains **FAIL/unqualified**, with no host-only path to claim a
new measured PASS.

## STAB2: integ19 evidence and HIDL crash-name repair

The immutable `sweep-integ19-f1-20261024` output retains28 `am_anr` events,
all `com.android.phone`: one SafetySource refresh broadcast and27 startup
timeouts. Thirty repeated trace summaries identify its main thread in
`HwBinder.getService -> android.hardware.radio.V1_4.IRadio.getService ->
RIL.getRadioProxy -> RIL.<init> -> PhoneFactory.makeDefaultPhones ->
PhoneApp.onCreate`. These are repeated reads, not30 unique ANRs. Unlike
integ18, this run establishes the blocked method. RADIO2 is present, so its
earlier queue repair alone does not close this case. The same run's Wi-Fi
owner check fails (`controller_rc=1, service_checked=0`); its exact failed
phase is absent from the saved output. Do not bypass owner release or start
another one-shot session to resolve the missing evidence.

The run reports no lost samples. Its15 crash/rotation observations include
14 ANR signature changes,14 event signature changes and3 tombstone changes;
they are not15 separately attributed native crashes. Nine repeated native
summaries (three text-file sizes93252,93292,93844) have empty process sections.
The final tombstone file count of six is not six established distinct native
crashes: that counter includes any protobuf siblings, while trace summaries
skip them. The missing originals prevent verifying each text/protobuf pairing.

The trace sanitizer and host decoder both rejected `@` in HIDL executable
names. A normal `android.hardware.media.c2@1.2-service-v4l2-64` header thus
lost its process and frames despite a successful reader. The repair accepts
numeric HIDL versions in host process tokens and `@` in device process
tokens, retaining the separate symbol validator, argument removal, limits,
0600 output and failure verdicts. The actual Android-shell/host round-trip
test now uses this HIDL name and verifies native frames plus rejected argv,
path and malformed-version tokens. Five focused STAB groups pass. This is a
repair to attribution, not evidence that integ19's missing native headers
belonged to that codec or that any native crash is fixed. Encoder and logger
implementation are unchanged.

Feature boot118 is bound to slotB by the saved info. Its retained kernel
export stops about5.09s after boot; logcat ends around16:02:46, before the
later sweep failures. The tar is truncated (`Unexpected EOF`), and integ18's
tar remains empty. These existing outputs are unchanged. The actual integ19
vendor includes RADIO2 and the real-data compatibility helper, so neither
is claimed missing. Exact owner failure and native-crash attribution require
the owner's existing later session/tombstone evidence. No runtime PASS is
claimed from host tests.

Stock QCRIL comparison: the retained candidate8 `bin/hw/qcrild` has SHA256
`0fe5f5714ae16f6b7c55c034a9332da4db6f81e52d0e23cc9997d432a936567b`.
REA/Ghidra12.1.4 decompilation of `main.cfi` at **0x101084** (ELF VA0x1084)
shows `RIL_startEventLoop`, `RIL_Init`, then `RIL_register`, then
`rilc_thread_pool`. Evidence
`ev_7ea4144728e7d38efd5aac20d9ddd002185068fef9298a06962c993fa4d1195d`
is saved in `stab2-host/qcrild-main.json`. The existing stock init sources
above establish class-main startup. Neither fact proves that QCRIL can
register before its nested RIL initialization completes or while this
mainline owner has failed. The large-library REA query produced no usable
result; no earlier client release or relaxation of owner gates follows from
it. All REA targets are closed; no target was executed.

Per-item disposition after STAB2's host work:

| Incident | Repair/commit | Remaining qualification |
|---|---|---|
|13b/14 early phone ANRs|RADIO2 `639358d`, already on main|Integ19's blocked startup persists after RADIO2; exact failed owner phase is missing. No new causal fix claimed.|
|13b/14 Google direct-boot Java crashes|Existing FBE handoff `1f7783e` and crypto work, owned by agent|No matching retained Google fatal exception in17 or19; no FBE changes in STAB2.|
|17 system_server diagnostic files|Early power statistics `8802f17`, already on main|Original blocked stacks absent; power readiness repair is not a proved ANR-cause fix.|
|18 two phone startup ANRs|Integ19 corroborates the same class of blocked RIL initialization|Integ18 has no recoverable individual stacks; do not assign19's stack to18 as measured fact.|
|19 28 phone ANRs|Blocked `IRadio1.4.getService` established; owner check failed|Need existing session `prepare.out`/`run.out` or later owner logs to fix the exact failed path.|
|19 empty native crash summaries|`f82078a` fixes HIDL-name loss, five host groups PASS|Native process/cause remains unassigned without original headers. Encoder/logger belong to agent.|

`A_stability` is still **FAIL/unqualified**. Next host intake needs only the
existing later session failure and native headers/frames, bound to boot118;
it does not require a new phone attempt from this worker. Host artifact root:
`A9-build/mainline/stab2-host/`. Live integ20 and all sweep worktrees remain
untouched. **STAB2 HOST MILESTONE DONE — causal repairs remain open.**

## STAB2 radio startup repair

The follow-up compares the archived stock service definitions, class-main
script, vendor properties and complete radio/config VINTF subtrees. Stock
starts two QCRIL instances for DSDS without waiting for WLAN or SIM presence;
its QCRIL services are eager, with no lazy interface directives. The existing
mainline VINTF already matches stock IRadio1.4/slot1+slot2, ISap1.2/slot1+slot2
and IRadioConfig1.1/default. Its noril override is already removed and its
two-slot/DSDS properties already enable both slots. No declaration/property
change or disabled Telephony is needed.

The defect is the service lifecycle: mainline withheld QCRIL until the Wi-Fi
owner was serving, then stopped it on owner failure/end. That leaves the
declared IRadio endpoint absent for PhoneApp's blocking getService call,
independently of the underlying owner-failure phase. The repair completes
ordinary radio-data preparation before queued framework startup and starts
both QCRIL instances on verified data plus cellular opt-in. QCRIL persists
across WLAN/owner unavailability; IMS companions retain serving-owner release
and stop conditions. Explicit cellular opt-out and shutdown still stop all
clients. MPSS start/attempt/store handling and runtime source checks are
unchanged. A data-preparation failure still prevents QCRIL startup and fails
qualification; it is not converted into a fabricated HAL result.

Details, stock source lines and REA address0x101084:
[cellular startup comparison](../cellular/stock/STARTUP.md). Five actual
Android-shell preparation groups, five FIFO/import/build cases, five source
integration groups, full host init verification and exact stock radio/config
XML comparison pass. Both full and incremental builds consume the changed
common init action and stock helper/RC files. This repairs the source defect;
stock RIL initialization completion and **zero phone ANRs** remain runtime
qualification for the coordinator. Host artifacts:
`A9-build/mainline/stab2-host/radio-startup/`. No phone operation, image build,
encoder/logger or qseecom/FBE/keymaster/gatekeeper change was made.

## STAB3: native evidence retention and system_server crash loops

The previous captures only summarized `/data` traces once the acceptance hook
ran. b-try later pulled the persistent logger boot directory, which did not
contain those trace files. A system_server loop can prevent the hook entirely;
RAM `/data`, a reboot or later formatting can also remove the original files.
STAB3 closes this collection gap without another transport or phone owner.

### Crash infrastructure checked on the host

The selected GSI contains `/system/bin/debuggerd`, `/system/bin/tombstoned` and
`/apex/com.android.runtime/bin/crash_dump64`. The last is inside the runtime
APEX, not missing from `/system/bin`. `android/check-crash-runtime.py` checks
all three ARM64 executables and their recursive DT_NEEDED closure:18 objects,
none missing or wrong ABI. It also checks the shipped init directory actions,
tombstoned service and three socket definitions. The selected system build.prop
does not disable debuggerd. The incremental image builder now runs this check
against its selected system root. This is static dependency validation, not
a relocated-symbol, namespace, ptrace or actual crash-handler qualification.

Platform post-fs-data creates `/data/anr` and `/data/tombstones` as0775
system:system with encryption=Require, then starts tombstoned. `/data/system/dropbox`
is0700 system:system. These actions are retained; no directory chmod, encryption
fallback or replacement debuggerd service is introduced. Runtime snapshots
record whether the trace directories exist, tombstoned reports running and
crash_dump64 is executable. Missing capture/health is visible in the summary.

The complete enforcing policy compiles. Its host libsepol replay verifies
crash_dump's tombstoned connection/socket write, tombstoned's tombstone creation
and write and ANR append, and the collector's shell/toolbox execution and
property read. Rootsh's existing ANR/tombstone reads remain. The only added
data access is read-only dropbox directory/files and parent-directory search;
dropbox file write remains denied. Existing protected vold/keystore ptrace
restrictions remain; this task changes no FBE, credential, HAL or QSEE behavior.

### Evidence kept before `/data` can disappear

The existing asynchronous logger launches one bounded scrubber child in
Android's root, using its existing diagnostic domain. The child exports only
allowlisted records to root0600 `crash-evidence.log` in the **current logger
boot directory**. It reads text tombstones, `/data/anr` traces and only dropbox
system_server_crash/data_app_crash/system_app_crash/app_crash text entries,
including gzip. Symlinks and native protobufs are skipped. No raw trace or
dropbox body is copied to the persistent partition.

Kept fields are source kind, byte size, truncation/completion status, process
basenames including HIDL versions, Java/native symbols, exception class,
numeric signal/code and fixed abort categories (unclassified/fdsan/FORTIFY/
assertion/Scudo). Paths, file timestamps/names, argv, PIDs/UIDs, UUIDs, registers,
fault/PC addresses, memory dumps, arbitrary abort/exception text and thread names
are omitted. Like STAB2, these are diagnostic summaries, not complete raw traces
or distinct-incident counts. Identical summaries across snapshots deduplicate.
Completion means the reader finished its prefix, not that a concurrent crash
writer had finished the original file.

Each capture examines at most64 files, a64KiB decompressed prefix per file,
16 process sections and32 frames. Prefix, frame and file limits and read errors
are explicit. One child is allowed at a time; its8-second deadline kills its
owned process group, with direct-child fallback. Exit/fork failures have
numeric status. Another snapshot starts15 seconds after completion. The logger
syncs partial output on its two-second cadence and on completion, and applies
the existing8MiB rotation/previous-file and50MiB total retention. No operation
runs in PID1 or delays the kernel's boot deadline. A very short-lived file or
storage/SoC hang can still escape a snapshot; actual persistence is unqualified.

b-try's existing return/pull collects both retained boot directories, including
this stream. The new offline `acceptance/crash-evidence.py` validates its fixed
protocol again and creates0600 per-boot `tombstones.jsonl`, `anrs.jsonl`,
`dropbox-crashes.jsonl` and `crash-summary.json`. It reports missing captures,
health, errors, limits and interrupted captures rather than interpreting absent
data as a clean native-crash result. Extraction failure is explicit and cannot
leave an otherwise successful sweep result marked PASS.

### INTEG21 summary lesson

The post-pull extractor counts **FATAL EXCEPTION IN SYSTEM PROCESS** markers
in both logcat rings, deduplicating exact overlapping records in private memory.
It prints fixed numeric `system_server_fatals` and `system_server_crash_loop`
fields even when the B hook never started. Two or more retained markers flag a
loop and make the final sweep HANG after the original return to A. Existing EDL
and failed-restoration precedence is retained. Marker counts are not inferred
from service death, bootloader destination, ANR file count or native file count.
The online acceptance stability row also counts these markers, saves a typed
system_server event and refuses PASS for any observed system_server fatal.

### Validation and build consumption

Host artifacts: `/home/user/A9-build/mainline/stab3-host-20261024/`.
Five Android mksh/Toybox-AWK and host groups pass for all three trace sources,
gzip, signal/stack/class retention, redaction, symlink/protobuf skips, partial
captures, limits, online/offline loop counts and overlapping rings. The native
controller fixture uses fake child/time syscalls and real temporary files:
deadline/group kill, reap, cadence, exit and fork failure pass without spawning
a child. Existing logger and four compiled-policy/handoff groups pass; the
complete static ARM64 logger compiles with warnings as errors. Existing five
STAB and five acceptance groups pass. Two isolated fake b-try lifecycles prove
normal success and post-pull crash-loop failure while both restore fake A.

Build14 recompiles the logger with the source-pinned embedded scrubber, links
the updated read-only policy, stages the offline extractor in frozen runtime
and runs the native crash dependency gate. Existing images/live sweeps are
untouched. The next coordinator-owned build/run must verify observed healthy
runtime status and a real retained tombstone/ANR/dropbox sample; this host-only
task performs no deliberate crash, device access or image build. A_stability
PASS and complete physical crash-handler operation remain runtime qualification.

STAB3 HOST DONE — scrubbed per-boot capture and explicit crash-loop summary ready.
