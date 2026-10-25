# Current: eager radio publication independent of WLAN and packet data

## INTEG24: the remaining packet-data module veto

Boot125 retains the initializing phone main-thread chain in
`crash-evidence.log:115-135` and `anrs.jsonl`:

```
HwBinder.getService -> android.hardware.radio.V1_4.IRadio.getService
 -> RIL.getRadioProxy -> RIL.<init> -> PhoneFactory.makeDefaultPhone
 -> PhoneGlobals.onCreate -> PhoneApp.onCreate -> ActivityThread.main
```

The trace collector strips native thread names, but the application-onCreate
and ActivityThread.main frames establish the thread. TelephonyRegistry/metrics
frames appear on another waiting path; they do not replace this startup block.
Logcat repeatedly reports IRadio1.4/slot1 unregistered and unsuccessful lazy
start requests. The declared stock QCRIL services are eager, not lazy services.
Neither IMS nor satellite appears in the captured blocking startup chain.

`kernel.log:1286-1289` records legacy qcom_common/IPA unresolved symbols at
36.06-36.18s. The packaged stock-radio helper still required both
`/sys/module/ipa` and `/sys/module/rmnet` before verifying/copying ordinary
radio files. Thus failed packet-data module loading withheld both QCRIL
instances even after58a6303 removed their WLAN dependency. The retained logs
do not include the helper's stderr or a module-directory/property snapshot;
the exact helper refusal is reproduced on the host, not quoted as a device log.

The helper now verifies/prepares the stock profiles/database without requiring
IPA/rmnet. Init still attempts its existing module preparation, and continues
to file preparation after an exec-service error. Ready is published only after
the existing opt-in/B-slot, source/destination checksums, path/symlink checks,
socket directories and labels pass. Both declared QCRIL slots can then start
without SIMs or a usable packet-data stack, as stock class-main startup does.
`stock.netmgr=blocked-kernel-abi` and disabled netmgr remain; this does not repair
the data-plane ABI or grant an RF/modem/store operation. No framework patch,
fake HAL, ANR suppression or hidden Telephony feature is introduced.

Host evidence: `A9-build/mainline/integ24-phone-anr-host/`. The new empty-SIM,
missing-IPA/rmnet case fails the old helper with `phase=modules`, then passes
after removing that veto. Six actual Android-shell preparation groups and
the five existing init-queue plus five integration groups pass. Hardware HAL
registration timing and absence of subsequent ANRs require the next owned run.

## Earlier WLAN lifecycle repair (STAB2)

Integ19's28 retained phone ANRs block in IRadio1.4.getService during PhoneApp
creation. The stock radio VINTF declarations were already present, but every
QCRIL start required WLAN serving/Android-ready; owner failure also cancelled
preparation or stopped QCRIL. This turns an unavailable WLAN/modem session into
a permanently absent, declared Telephony HAL. No SIM is required for Telephony
to construct both DSDS RIL instances and make these blocking HIDL requests.

Stock's qcom-c_main-sh (init.qcom.rc:516) runs init.class_main.sh in class main.
That script starts vendor.qcrild (line94) and vendor.qcrild2 for dsds/dsda
(line118), subject to its platform/no-RIL selection, with no SIM or WLAN test.
The retained stock qcrild.rc uses class main/disabled and normal init restart;
it contains no interface or oneshot directive: explicit eager startup, not a
lazy HIDL contract. A third QCRIL is declared but only selected for tsts; A9's
stock vendor property persist.radio.multisim.config=dsds selects two.

Exact stock configuration was reopened read-only from
a9-assets/mainline-archive/modem-finished-candidates2-7-evidence-20261023.tar,
members evidence/stock/{etc/init/qcrild.rc,etc/vintf/manifest.xml,
bin/init.class_main.sh,build.prop}. Private retained copies and member hashes
are in A9-build/mainline/stab2-host/radio-startup/stock-archive-members.json.
The android.hardware.radio and radio.config XML subtrees are identical to the
packaged fragment: hwbinder IRadio1.4/slot1+slot2, ISap1.2/slot1+slot2, and
IRadioConfig1.1/default. No AIDL radio replacement is started. Stock vendor has
ro.telephony.iwlan_operation_mode=legacy, dsds and the Qualcomm RIL library
path; no ro.radio.noril or ro.telephony.default_network entry occurs in that
vendor file. Absence here says nothing about its system partition defaults.
Mainline already removes Cuttlefish's ro.radio.noril override and retains DSDS,
two explicit slot selectors and its network-mode defaults. No property or
manifest alteration is needed to repair this lifecycle mismatch. Removing
radio declarations or disabling telephony for an empty SIM would hide hardware.

REA stock QCRIL main.cfi0x101084 (ELF VA1084) calls RIL_Init then RIL_register;
evidence ev_7ea4144728e7d38efd5aac20d9ddd002185068fef9298a06962c993fa4d1195d
is retained at stab2-host/qcrild-main.json. This establishes the unmodified
stock initialization path, not its future completion time. A further large
library Ghidra query timed out300s and adds no usable claim. No target executed.

The repair puts module/profile/database preparation in the common firmware RC
before queued zygote-start, after firmware and the existing owner start; import
order cannot load IPA before its firmware bridge. Both QCRIL instances start
on prepared-ready/opt-in, independently of WLAN state, and remain framework
endpoints across owner failure/end. IMS keeps its owner conditions. Explicit
opt-out/shutdown still stops QCRIL. An explicit re-enable from stopped requests
preparation; boot failure has no implicit retry. Preparation never starts MPSS,
changes its attempt accounting or grants store access. Existing source/dest
checksums, directory/symlink checks and180s helper timeout remain. The deliberate
boot cost is finishing ordinary profile copying before framework startup.

This fixes the source-level service-lifetime defect that can leave getService
waiting forever. Completion of stock RIL initialization and a zero-ANR
A_stability run still require coordinator qualification; host results do not
establish hardware timing or guarantee all causes of ANRs are eliminated.

Host checks: five actual Android mksh preparation cases (including failed-WLAN
owner success, corrupt/symlink/opt-out refusal), five init FIFO/import/packaging
cases, five source integration cases, full host_init_verifier including existing
power services, and exact archived stock radio/config VINTF comparison. Artifact
root: A9-build/mainline/stab2-host/radio-startup/. No phone operation or image
build is performed by this task. Earlier scheduling experiments follow as
historical evidence; their serving-owner QCRIL dependency is superseded above.

## Historical early-radio preparation


The full integ12 sweep's retained boot100 registers both radio instances:
logcat37686 registers IRadio1.4/slot1 at16:04:11.600, and37797 registers slot2
at16:04:12.162. Kernel3429 shows successful preparation at249.646s, followed
by qcrild startup at249.700s. All14 phone ANRs in this boot precede those
registrations; the last phone ANR records are logcat36767/36788. The696
process observations aggregate repeated reads of retained traces and do not
mean696 new ANRs or prove continued radio failure after registration.

Our startup dependency was wrong: the S2 init trigger waited for
`sys.boot_completed=1`, and stock-client preparation waited for owner release
and boot completion. Telephony starts before boot completion and waits for
IRadio. This pushed registration to roughly250s. The qualified stock-radio
candidate8's22 no-SIM checks begin after readiness and cover a later211s
window; they did not qualify the preceding framework startup or zero ANRs.
Stock prepares its radio data during initialization, and the imported qcrild
definitions are explicitly started disabled services, without lazy interface
lines. VINTF and both slots were already correct in integ12.

The opt-in image now requests the same one-shot owner at post-fs-data, after
the existing data/firmware actions. Module and ordinary filesystem preparation
run alongside it. No helper starts MPSS, QCRIL or IMS. Source checksums,
symlink refusal, copy verification and the board's two-slot selector remain.
Prepared-ready indicates verified data, while **every** radio/IMS start or
restart action additionally requires cellular opt-in and a serving,
Android-ready owner. Initial Android-ready0 does not cancel file preparation;
after owner release, revocation still requests client stop. Owner ending,
ended and failed always revoke opt-in, including before initial release.

This changes scheduling, not the controller's protected-store, companion,
one-attempt, timeout, fault-stop or offline-teardown checks. No new lazy-start
route, modem restart, RF command, capability or policy bypass is introduced.
The file helpers remain confined to packaged sources and ordinary runtime
directories; protected modem stores remain owned by the existing controller.

Five actual Android mksh host cases pass for pre-boot-completed preparation,
gated client release, corrupt-source refusal, symlink refusal, opt-out and
failed-owner refusal (the first case includes release/revocation transitions).
Five source-integration groups and the complete host init verifier pass.
The next integrated run must establish the earlier registration time and
absence of startup ANRs; no hardware success is inferred from these host tests.


## RADIO2: execute the early start, rather than queuing a late request

INTEG13b boot 104 (`sweep-integ13b-20261024/mainline.tar`) contains the same
kernel/logcat files as the extracted boot directory. The first retained phone
IRadio wait is at 16:02:18.755 (logcat.log.prev:5599). Slot1 registers at
16:03:03.986 (38694); slot2 at 16:03:04.467 (38810): 45.231/45.712 seconds later.
The original phone fork is outside the retained log window, which starts at
16:02:16.105; it must precede the wait. Retained starts below are ANR restarts,
not an invented timestamp for the original fork.

| Event | Log wall time | logcat.log.prev line |
|---|---|---|
| First retained phone IRadio wait | 16:02:18.755 | 5599 |
| First phone startup ANR | 16:02:28.227 | 17854 |
| First retained phone restart | 16:02:28.526 | 18140 |
| IRadio 1.4 slot1 registered | 16:03:03.986 | 38694 |
| IRadio 1.4 slot2 registered | 16:03:04.467 | 38810 |
| Last phone startup ANR | 16:03:04.563 | 38893 |
| Last retained phone restart | 16:03:04.640 | 38977 |

All six ANRs were incurred during initialization. The last ANR report follows
slot2 registration by 96 ms, after the startup deadline had already elapsed.
The 289 observations reread retained traces; they are not 289 separate ANRs.
The later IRadio waits during owner shutdown do not establish another initial
startup failure. Wall clock dates are the log's clock, not a corrected date.

Kernel line 2503 records stock-file preparation exit at 105.274459s after a
29.000999 s `exec_start` wait. The owner only reaches preparation at 106.934358s
(2575), run at 130.884396s (2672), and modem-up at 171.983001s (2964).
The earlier patch moved **setting** the request into post-fs-data, but its
property action remained behind already queued zygote-start/early-boot/boot
and the blocking profile copy. It did not execute the owner start there.
Android 16 queues ordinary property changes and suspends action execution for
an exec service; this differs from executing `start` in the current event.
[AOSP init source](https://raw.githubusercontent.com/aosp-mirror/platform_system_core/android16-release/init/init.cpp).
The retained platform init.rc late-init sequence explicitly queues
post-fs-data, zygote-start, early-boot and boot in that order.

Stock's retained `etc/init/hw/init.qcom.rc:516` defines qcom-c_main-sh as
class main/oneshot, running `bin/init.class_main.sh`. That script explicitly
starts vendor.qcrild at line 94 and vendor.qcrild2 at line 118 (the latter selected by
stock's multisim configuration). qcrild.rc itself remains class main/disabled.
The script does not wait for boot-completed. These sources do not demonstrate
an additional stock TeleService startup hold; no such gate is assumed here.
Our explicit board two-slot selector remains; it is not reverted to a missing
or persisted framework preference. Cached source: storage-row29-lun4-verdict-
20261021/configs/daily-vendor-reference/. No binary or identifier is copied.

The common init.a9ml-wifi.rc now directly starts the preauthorised one-shot
owner **in post-fs-data, after its firmware preparation in the same file**.
This also prevents filename/import order from starting it before its mount
and firmware setup. The S2 file retains only its explicit manual request path;
the ordinary session file no longer repeats automatic startup at boot complete.
The cellular action still synchronously loads IPA/rmnet prerequisites, then
starts the verified profile-copy service asynchronously. Its ready proof,
source/copy/symlink checks, 180 s timeout and failed/opt-out handling are unchanged.
Init can process HAL/client release while the copy is running; clients still
require all of verified preparation, cellular opt-in, serving owner and
Android-ready. Owner release/revocation and every stop/shutdown path are kept.
No new interface lazy-start bypass, RF operation, protected-store access,
framework ANR suppression, nor fake registration proof is introduced.

Host checks: 15 groups PASS (five FIFO/import-order/opt-in/packaging regressions,
five source-integration groups, five actual Android mksh file-preparation/
release/refusal groups); full vendor host_init_verifier PASS for S2 and ordinary
session configurations. The FIFO test first reproduces the old late-property
start, then checks firmware-before-owner-before-zygote across all six RC import
orders. Host tests establish ordering and preserved gates, not a measured
future registration time or zero hardware ANRs. The next owned boot must
confirm registration before phone startup. Existing INTEG14 remains immutable
and does not contain RADIO2; no image is built by this task. Incremental
packaging now refreshes both common Wi-Fi and session RCs so the next candidate
cannot silently omit the direct-start change.
