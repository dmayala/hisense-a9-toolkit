# Wi-Fi cap and MTP follow-up — 24 October

Host-only changes on main; **no image was built**. Source evidence is
`A9-build/mainline/sweep-integ8-r2-20261024/{onb.log,onb-root-failed-command.sh}`
and its saved `vendor-overlay/precompiled_sepolicy`. At the time of this work
the sweep was still collecting stability evidence and had no host-side boot
kernel/logcat files. No phone, USB probe or credential lookup was performed.

## Wi-Fi

onb.log25–26 records native step8, remote rc1 at line7. The saved failed
command's line7 is:

```sh
timeout 3 /mnt/a9-wifi/tools/iw dev wlan0 set txpower limit 2000
```

This is the **20 dBm transmit-power cap**, not packet capture or connection.
It runs before the credential function and before `connect-network`; therefore
this failure did not look up or submit Wi-Fi credentials. Its old redirection
discarded both stdout and stderr, so the error text cannot be recovered from
onb.log. Device/service readiness now succeed independently.

Policy queries against the actual saved sweep policy deny rootsh's
`netlink_generic_socket` create/bind/read/write/getattr/getopt/setopt, while
NET_ADMIN is already allowed. That prevents libnl/nl80211 initialization even
with an existing wlan0. The new explicit self-socket rule repairs this missing
enforcing-policy closure; it adds no new capability, owner control, permissive
domain or cap bypass. The existing three-second timeout and command failure
remain fatal before credentials.

The cap command now reports only its actual return code and numeric netlink,
permission and unsupported-operation error classifications. Raw tool output is
discarded after classification. The failed check includes these fields, keeping
transport failure distinct from a missing tool, timeout, or rejected cap.

Actual Android shell replay: five groups PASS for success, netlink failure,
permission failure, unsupported operation, and timeout/missing-tool statuses.
The fixed argv is checked and a private-text marker never appears in output.
Compiled policy queries allow all seven socket operations and retain two tested
shell privilege denials. Five acceptance and five Play fixture groups PASS.

## MTP

onb.log30 shows `set_rc=255`, followed by ADB-only framework/request state and
zero MTP descriptors; the MTP session helper was therefore **not invoked**.
`screen_locked=0`; this is not evidence of a keyguard suppression. Getter stderr
is already captured by the preceding fix. An initiating adb shell can disconnect
during a real function switch, so rc255 alone cannot establish setter rejection
or justify replaying the mutation. The existing post-switch state, descriptor,
same-device session and transport checks remain the acceptance criteria.

A separate concrete HAL bug was reproduced: `Controller::setFunctions` capped
every caller's wait at2000ms. [Android16's UsbDeviceManager](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/android16-release/services/usb/java/com/android/server/usb/UsbDeviceManager.java)
passes2500ms (its3000ms timeout minus500ms callback leeway). Our earlier cutoff
could restore ADB while a valid MTP descriptor was still within that budget.
The HAL now honors that caller budget, with a finite5000ms ceiling for other
callers. Missing descriptors still trigger the existing bounded ADB fallback.

The real controller fixture delays the final MTP descriptor beyond two seconds
but inside2500ms. The old production code fails that case; the fixed code passes
all five controller groups, including missing/unsupported function fallback and
ADB restart recovery. This proves the timing bug and fix on the host. Without
the current boot's USB logcat, it does **not** prove that the cutoff was the sole
cause of the observed sweep failure.

The hook now snapshots numeric USB error counts before and after the request:
descriptor timeouts, bind/apply errors, failed MTP control opens, framework
default fallbacks and MTP service binding errors. It reports unreadable/rotated
logs explicitly and retains setter request/permission classifications. The
counts are saved before a warm reboot can lose the first boot's trace. Raw
logcat, USB identifiers and object names never enter the report. Actual Android
shell replay verifies all six counters without leaking a private-text marker.
An acceptance fixture also proves rc255 is tolerated only when the later full
state/session proof succeeds. Five acceptance groups PASS after rebasing onto
the concurrent FP10 commits.

Local host evidence: `A9-build/mainline/wifi-mtp-fixes-20261024/` contains the
policy compile/decision logs, before/after USB test logs and receipts. Commits:
`501bb3e` (Wi-Fi cap closure/diagnostics), `5197158` (USB timing/MTP evidence).
The next explicitly requested integrated build must rebuild the USB HAL and
compile current main's policy; changing only the host hook cannot install these
two binary-policy/HAL fixes. Existing images and queued phone sweeps are intact.

## Integ11 cap permission follow-up

The integ11 hook reports device/service/owner readiness, followed by
`cap_command_rc=243`, `cap_permission_error=1`, `cap_netlink_error=0`.
MTP and microphone now pass. At diagnosis time the ongoing sweep had not yet
saved its boot kernel/logcat, so this fix queries the policy extracted from
the integ11 vendor image rather than claiming a recovered AVC.

The earlier generic-netlink grants are present. The remaining policy gap is
`SIOCGIFINDEX` (`0x8933`) on rootsh's own Unix datagram socket. The ordinary
`ioctl` permission is allowed, but Android's default Unix-socket extended
allowlist contains terminal operations and excludes this interface lookup.
The musl `iw` uses that socket class for `if_nametoindex`, which must succeed
before sending the transmit-power request. EACCES is error13; `iw` returning
minus13 yields shell status243, matching the saved cap result.

Android's `unpriv_sock_ioctls` grants `SIOCGIFINDEX` on IP sockets; the actual
policy allows it for `hal_wifi_default`'s UDP socket. Our Wi-Fi owner already
has the exact Unix-datagram exception in `avc-f.cil`. The fix gives rootsh that
same single-command extended permission. It adds no interface-changing ioctl,
capability or permissive domain. The fixed20dBm cap still must succeed before
credential lookup or connection.

The compiled before/after policy check passes: the effective rootsh Unix
datagram ioctl bitmap gains **only0x8933**, removes nothing, and still denies
setting interface flags, address and MTU. Required base socket/NET_ADMIN
permissions are allowed before and after, and permissive domains remain zero.
The owner/Android Wi-Fi lookup comparisons pass. Host-only evidence and query
source: `A9-build/mainline/wifi-cap-fix-20261024/{tests.json,decisions.log,query-xperms.c}`.
No phone access, credentials or image build. The next integrated image must
compile current main's policy; the queued integ11 payload is untouched.

## MTP2 — INTEG14 function-switch follow-up

Host-only comparison of the delivered INTEG13/14 artifacts and
`sweep-integ13b-20261024` / `sweep-integ14-20261024`:

| Evidence | Finding |
|---|---|
| Vendor gadget HAL, USB helper, compatibility module and both USB init files | Byte-identical between images. |
| Sweep hook | Byte-identical. The launcher changes add optional reopen preparation and inherited USB locking; the ordinary sweep supplies no reopen hook. |
| Vendor policy CIL differential | Only two added metadata grants for rootsh. No USB or ordinary-shell permission change. |
| INTEG13b onb.log45 | Setter transport rc255, request marker1, subsequent framework MTP, same-device descriptors and MTP session all pass. |
| INTEG14 onb.log44 | Setter transport rc255, request marker0, no exception/permission text, subsequent ADB-only state. Six USB error counters remain zero. |
| INTEG14 saved archive | Only boot107 retained after the warm reboot. It cannot establish the earlier setter's AVC/HAL history. Its shell AVCs concern logcat mapping event tags, not a USB setter denial. |

There is no demonstrated USB payload or policy regression. Status255 is the
ADB client's status; it does not identify a Java exception or establish that
the setter ran. The hook discarded host stderr, and its only setter evidence
depended on the connection being replaced. The historical failure boundary is
therefore an unconfirmed request on a lost command channel; its exact initiating
cause is not recoverable from these saved files. A new permissive rule or HAL
change would be speculative.

The hook now launches **one** ordinary-shell `svc usb setFunctions mtp` worker
under `nohup`/`setsid`, with all ADB descriptors closed. It waits for a worker
start marker before acknowledging launch so that normal launching-shell exit
cannot race detachment. The setter has a20-second timeout and2-second kill
grace, inside a25-second worker bound with2-second grace. A fresh shell-owned
directory prevents stale result reuse. The worker keeps reply text only in
memory and atomically saves numeric return/exception/request/permission fields.
The host separately classifies its launch-channel stderr without saving raw
text. It polls the receipt after disconnect, never repeats the mutation, and
saves `usb-set-evidence.json` before the warm reboot. Completed receipts are
removed; an uncertain worker's tiny directory is retained rather than raced.
Framework state, matching host descriptors, real MTP session and restored
transport remain the acceptance criteria. A launch acknowledgment alone passes
nothing. No SELinux, HAL, init, launcher, credential or image changes are needed.

Host validation: five sweep fixture groups pass, including a launch disconnect
with successful readback and a missing receipt that still fails without retry.
Three Android ARM64 mksh/toybox replays pass with a fake `svc`: destruction of
the launching process group, permission refusal, and timeout. Each executes
the setter exactly once and exports no private-text marker. The initial replay
fixture had a missing separator in its cached shell path; corrected fixture
results pass. These are host tests, not evidence that INTEG14's historical
disconnect was caused by that process-group race. Private artifact comparison
and test receipts: `A9-build/mainline/mtp2-host-20261024/`.

RADIO2 `639358d` is on main and absent from INTEG14. Its direct post-fs-data
owner start removes the demonstrated property-action queue delay; asynchronous
stock-profile copying removes the blocking copy before framework startup.
INTEG14's saved trace again records slot1 waits at16:02:20.544 and registrations
at16:02:52.042 /16:02:52.715. The stability summary contains only
`com.android.phone`, with radio-wait/startup-timeout evidence. RADIO2 should
remove this identified cause of the remaining startup ANRs, subject to the
next integrated sweep confirming both slots are ready in time. A zero-ANR
runtime result has not yet been established.
