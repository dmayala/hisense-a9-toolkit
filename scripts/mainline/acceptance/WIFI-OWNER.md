# Integ19 Wi-Fi owner and cleanup

Host-only comparison against saved integ18/19; base main33a5ef6. No device
commands, live sweep worktree changes, image build, owner retry, deadline
extension, controller or firmware changes.

## What the retained evidence establishes

Integ18 reports connected/validated Wi-Fi and all cleanup subchecks pass.
Integ19 reports controller_rc1 and service_checked0: no credential delivery
or Wi-Fi row enable attempt occurred. Its failed native script's line13 is
the default START case. That proves a state outside serving/armed/starting
and the explicit empty/awaiting-audit request path. It does **not** identify
ended versus failed versus ending. The new11/12 exit distinction and
owner_exit from79177af were not in this image; they do not retrospectively
recover its property value or failure reason.

Read-only debugfs comparison verifies11 nonempty members identical between
the actual integ18 and19 vendor images: controller, starter, firmware helper,
sandbox, common/session/Android init, modules list, Wi-Fi HAL, QRTR module and
session.conf. Both session lengths are3600s. A228s measurement window does
not demonstrate normal expiration of that session. Neither the old firmware
path defect (already repaired in both) nor a new controller binary is an
established explanation. The saved early log shows supplicant binder and
STA connectivity setup around16:02:20; it does not prove lifetime through
the later row. The startup supplicant timeout therefore cannot simply be
asserted as the observed failure.

Neither output retains session/prepare.out, session/run.out, trial/fault or
trial/session-end. Integ18's tar is empty;19's is truncated, with logs ending
before the failed row. No existing coordinator checkpoint supplied the
missing reason. The exact early terminal cause remains **unknown**. Changing
watchdogs, increasing session length or restarting a consumed owner would
be an unsupported fix, so no such change is made.

The terminal START branch now exports fixed numeric owner_state values:
0empty,1armed,2starting,3serving,4ending,5ended,6failed,7awaiting-audit,8other.
Before refusing a terminal owner, it reads existing root-owned boot-local
files: owner_end_reason0unknown/1requested/2evidence-RAM-low/3session-length,
start-attempted/fault booleans and a fixed STOP category from bounded log
tails. Categories1–12 identify companion startup, offline audit/gate, MPSS
start, release, Android readiness, identity, MAC, regulatory, companion,
monitor, module or firmware failures;0 remains unknown. No raw reason, log,
identifier, path or credential is exported. The sweep saves these facts on
failure and still refuses to restart the terminal one-attempt owner. This
repairs loss of diagnostic evidence; it does not claim to fix its unknown
historical exit cause.

## Cleanup repair

Integ19's sole failed cleanup subcheck is wifi_disabled0; apps, credential
removal applicability, Bluetooth and E Ink subchecks all pass. The old result
means the disable command failed; stderr and subsequent status were not
saved. The later79177af fallback checks status only once and accepts a
successful command without confirming actual completion. It can still fail
an asynchronous disable or an already-disabled service that rejects the
request, and it can pass an accepted request that never completes.

Both acceptance and Play cleanup now request disable once and poll bounded
positive framework readback. Both `Wifi is disabled` and `Wi-Fi is disabled`
are recognized as whole lines. Command failure can pass only if readback
proves off; successful request with enabled/unreadable status still fails.
Acceptance respects its existing command deadline and uses at most15s;
Play uses its existing20s window. The sweep retains numeric request/status
RCs and poll count. Existing targeted forget, app/BT/E Ink cleanup, final
restore ownership and every owner guard remain. This is a source-level
cleanup correctness repair, not a claimed measured recovery of integ19.

## Host results and remaining intake

Five focused groups PASS: real cached Android mksh/Toybox/awk numeric owner
reasons and no terminal restart; known/unknown orderly end reasons with
privacy checks; accepted request that stays enabled; already-off and delayed
disable; actual sweep failure evidence plus cleanup verdict. Five existing
acceptance groups and five Play smoke groups also PASS. Native fixtures
touch only synthetic host files and mocked properties; no phone lifecycle.

Artifacts: `A9-build/mainline/wifiowner-host-20261024/{checks.json,
image-comparison.json}`. Changed Python files are consumed by the existing
runtime bundler; no APK or policy rebuild is needed for this change.

To repair the owner's actual cause, supply any **existing** integ19
prepare.out/run.out or fault/session-end export. Otherwise the next
coordinator-owned run must consume the revised runtime and retain the
numeric terminal facts. Host-only evidence cannot establish a new Wi-Fi or
cleanup runtime PASS. **CLEANUP HOST FIX DONE — OWNER CAUSE OPEN.**
