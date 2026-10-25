# R19 cached SOC across suspend (separate POC)

Stock package qg_psy_get_property at0xffffff8008a45928 dispatches CAPACITY to
0xffffff8008a45b14. The normal branch returns maintained/cached SOC, including
the cached value at+0x6e8 loaded by0xffffff8008a45c48. Stock source provenance,
package ELF/disassembly and property decoding are in the local
~/A9-build/mainline/stock-adsp-suspend-20261021/battery-vendor-report.md and
report.md section4. This is a local PMI632 QG/SPMI path, independent of ADSP.

R19 keeps the last seeded SOC/telemetry through suspend, clears the transient
counter_gap on resume and resets last_ms so the first resumed sample rebaselines.
Suspend does not manufacture an ENODATA transport error. Preexisting real
sample/control errors and the charging fault latch remain. CAPACITY returns
the seeded estimate while fresh measurement is unavailable; other error/health
reporting continues to describe actual failure. An unseeded SOC is ENODATA.
No fixed percentage, Android shutdown override or initial_soc default change.

A separate sticky charge_unknown flag records every missed current interval
(suspend, >5s sample gap, failed sample). CHARGE_COUNTER remains ENODATA until
the existing measured full-charge anchor reconciles it. No current is integrated
over sleep, no OCV reseed under load, no QG/SDAM/FIFO writes or charging-limit/
JEITA/USB policy change. The existing full anchor may clear that uncertainty.

This implements stock's cached-SOC reporting principle for the short test;
it is NOT stock's complete FIFO/OCV/userspace/SDAM model and does not claim to
reconstruct sleep charge. Repeated/long sleeps can accumulate estimate error.
That limitation is separate from whether Android receives a valid cached SOC.

Host artifact: suspend-novtcon-20261021/battery-r19/pmi632-battery.ko, built
against the noVT module SDK W=1 rc0,38 import CRCs match. Not installed in the
R18 vendor; no live replacement, unload/reload, device or charging action.
R19 packaging/test admission awaits R18 evidence and agent's next instruction.
Actual-source host tests cover cached60% across PM, reference timestamp reset,
no fabricated sleep charge, independent charge uncertainty, preserved hardware
errors, unseeded refusal, measured full anchor and suspend failure recovery.
No hardware qualification or complete gauge-model parity claim.
