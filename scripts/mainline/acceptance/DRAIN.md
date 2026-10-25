# Optional one-hour standby drain

`A9_SWEEP_DRAIN=1` enables row `10_hourly_drain`; the default is0/NOT_RUN.
This is a later-sweep stage inside the existing b-try owner. It neither starts
a second phone session nor changes the original owner deadline. No phone
operation was performed to implement or test it.

## A9 measurement support

The updated PMI632 module provides a dedicated, relative QG hardware-batch
counter and `a9-usb/input_suspend` USER control. The stage recognizes
`drain_counter_supported=1`, reads a coherent `drain_snapshot` and uses fresh
`drain_capacity` for the independent watchdog. It requires99–101% hardware sample
coverage across the measured interval and records collection wakeups. Rates use
sampled time; unrepresented sleep intervals fail measurement and restore input.
See [stock addresses and driver behavior](../pmic/battery/drain-stock.md).

The old `A9-PMI632-sampled-CC-POC` module without these attributes remains
unsupported: its ordinary one-second counter is invalid across suspend and its
cached SOC cannot enforce the floor. Older images are refused before charging,
screen or Wi-Fi changes. Neither path fabricates zero or interpolates sleep.
The new control and gauge need physical qualification in an owned later sweep.

## Conditions and bound

The stage runs after normal Wi-Fi/check-in/media measurements and before
Wi-Fi cleanup, warm reboot, optional final HVENC and final PIN lifecycle.
It requires a passing Wi-Fi row and a live original b-try owner deadline.
An adequate hold reserves3600 seconds of measurement plus300 seconds for setup
and restoration. A short hold fails only this row with insufficient_time=1;
ordinary rows keep their budgets. Use `A9_BTRY_HOLD=7200` for a future combined
run, with the coordinator's usual image paths and other desired sweep flags.
The existing Wi-Fi owner's image-time `session.conf` bound must also cover
the measurement plus earlier feature work; its usual3600-second bound alone
cannot cover an hour after setup. Configure a later candidate through that
owner's existing session preparation. This stage never extends a live owner.

It admits only an initially normal charging control: either the standard
`battery/charge_behaviour` with auto and force-discharge support, or a dedicated
`a9-usb/input_suspend`, `usb/input_suspend`, or `battery/input_suspend` initially0. It selects no raw regmap
address, FCC limit, ICL vote, battery simulation or ambiguous charge-only knob.
Force-discharge/input suspend must remove USB power contribution to the cell;
merely inhibiting charging while USB powers the system would bias the result.
The stock PMI632 input suspend bit is documented in
[charging-control.md](../pmic/battery/charging-control.md): smblib_set_icl_current
uses USBIN0x1340 bit0 at <=25mA. The updated module implements this separate USER control; host source evidence
does not substitute for physical input-suspend qualification.

Battery capacity must start above35%. The main sampler checks every60 seconds
and restores at35% or below, leaving headroom above the requested30% hard floor.
The independent watchdog checks capacity every30 seconds too. Missing/invalid
readings, Wi-Fi loss, screen wake, resumed input, charging, counter reset,
reboot/slot change or transport failure prevent a PASS and invoke restoration.
A recorded below_floor flag is a failed measurement, never a successful test.
Physical floor protection depends on a working, live gauge and reachable
charging control; older sampled-only modules are refused for that reason.

The screen is put to sleep and USB stay-awake is temporarily disabled. Existing
suspend policy and wakelocks remain observable; there is no forced suspend,
forced Doze, IRQ masking, wakeup-source disabling or holding a measurement
wakelock. USB remains attached. Data enumeration while input is suspended is
a device observation. The monitoring/USB traffic can itself wake the phone;
reported residency includes that overhead. Zero residency is a valid measured
observation, not proof the phone entered suspend.

## Restoration

An independent host process is armed and acknowledges readiness before the
first charging mutation. It inherits no owner lock descriptors. Pipe EOF after
hook SIGKILL, a75-second missed heartbeat, low battery, failed health read or
its absolute time bound starts restoration. The parent also restores in its
finally block, including interruption. Both restore charging first, check the
control readback, then restore the original USB stay-awake and screen state.
Restoration retries are bounded. Transport commands and their entire owned
host process groups are timed out/reaped; no host child can extend the b-try
deadline. SlotB and original boot checks prevent a stale watchdog from writing
to slotA or another boot.
Root-helper process groups are registered, with process birth identity, before
their mutation scripts are sent. On parent death the watchdog stops any such
in-flight group before restoring charging; it does not leave a late pause
command running after restoration. If a leader has exited, a per-command inherited
marker and pidfds identify surviving descendants without trusting a reused PID.
Bounded cleanup that cannot confirm those descendants are gone fails cleanup.

Failure of independent restoration fails the sweep cleanup verdict and cannot
be hidden by a measurement PASS. Existing b-try retains return/recovery custody.
The watcher is a charging-restoration helper, not a reboot/flash/modem owner.

## Evidence and host validation

`drain-result.json` records elapsed time, charge-counter delta in uAh, normalized
uAh/hour, millipercent/hour against charge_full_design, start/end capacity,
suspend milliseconds and suspend permille. The static ARM64 clock probe reads
CLOCK_BOOTTIME and CLOCK_MONOTONIC; their interval difference measures system
suspend time, with a recorded clock-pair uncertainty bound. Wall time, cached
percentage and extrapolation from instantaneous current are not drain counters.

`drain-wakeup-before.txt` and `drain-wakeup-after.txt` retain bounded wakeup-source
tables; `drain-watchdog.json` records restoration and its trigger category.
Files are private0600. Raw transport errors and network/account labels are not
printed. No screen, audio or user data is exported.

Rebuild acceptance assets: the new `drain-clock` executable and source are
manifest-pinned. Incremental builds copy both new runtime sources, export the
flag default0, and refuse an old asset bundle lacking the clock probe.

`python3 -I scripts/mainline/acceptance/test-drain.py` runs five host-only groups:
simulated full-hour charge/residency; default/short-budget/unsupported-counter
refusal; low battery/read/Wi-Fi/input-override failures and interruption;
actual host-parent SIGKILL with independently reaped watchdog plus deadline/
heartbeat/floor restoration; and actual Android ARM64 clock/Android shell input
suspend readback on synthetic files. Existing acceptance and independent-row
tests remain passing. These are stage/control-flow tests, not physical charging,
QG-counter, floor, USB-enumeration or one-hour standby qualification.
