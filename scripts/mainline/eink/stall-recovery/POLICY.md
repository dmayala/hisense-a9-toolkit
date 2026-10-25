# N-e frame-progress policy

The user reported the same phase5 freeze on N-c with launcher swipes and no
stream/video. Audio is not required. N-d adds the existing flush observation
polling patch. N-e retains that module and all audio/volume/vibrator/policy fixes,
then changes only the E Ink module and its recovery helper.

User-selected thresholds are100ms soft and500ms hard, measured from the start
of the first drive commit or the last successful drive return. A soft warning
counts once per progress interval and does not cut HV, change gates, set fault
or snapshot, or consume a recovery grant. Its timer continues to the SAME
absolute500ms deadline. Successful drive progress re-arms; confirmed null
cancels. A short raw-spin critical section serializes progress/null against the
hard timer. At500ms, the existing starvation shutdown/snapshot/recovery path is
used. A later raw success cannot resurrect a cut-off drive.

A real DRM error, including the existing void-wait MSM error latch, now forces
emergency-off as soon as the call returns and before producer cleanup. It stays
hard and cannot qualify for automatic timing recovery. Rail/PG, temperature/bin,
PMIC max-on, session HV limits and all electrical latches are unchanged. Hard
faults dominate a racing timer. Recovery still requires neutral/off, PG low,
closed gates, no pending commit and no hard latch; full Clear is owed afterwards.

There are two timing recovery grants, refilled after600000ms of clean operation
between successful non-dry physical updates. Idle time between them counts;
a stalled/faulted system cannot refill itself merely by waiting. A soft/hard
counter change restarts the clean interval; faults or an aborted update clear
it. This accounting is serialized with the timer. Backoffs remain5s/30s. Budget
refill never resets the separate lifetime `grants` receipt or hides an electrical
fault. Userspace validates that receipt rather than the resettable `retries`.
Read `stall_recovery` for thresholds, soft_events, grants and current budget.

These are explicit engineering thresholds, not measured stock panel hold
limits. REA establishes that stock has no equivalent software frame-progress
cutoff; it does not establish harmless500ms drive holds. Physical wear, waveform
quality and watchdog effectiveness under real IRQ scheduling remain unqualified.
No phone/video/webcam access occurs in the host build or tests.

`test-policy.py` compiles the actual timer/arm/disarm, immediate-error block and
clean-refill functions with a controlled clock. `test-recovery.py` executes the
actual worker/PMIC recovery and refill, including receipt2->3 after reset.
`test-reopen.py` runs the full helper against fake sysfs through host sh and
Android mksh, including a refilled budget, hard error and explicit cancellation.
These test production logic and boundaries, not a physical hold duration or a
real SMP/IRQ scheduler. Build checks real kernel types and every module CRC.
