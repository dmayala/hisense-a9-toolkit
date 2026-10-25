# Integ27 host rerun

The retained `sweep-integ27-f1-20261025/onb.log` showed four identical encoder
skips with remaining_ms=144999, skip_reason=3, budget_ok=0, slot_b=0 and staged=0.
`variants.py` passed `now+145` to a qualifier requiring at least145 seconds;
the next clock read made admission impossible. Slot0 meant it never queried the
slot. The wrapper now passes the real owner deadline minus its existing reset
reserve. The qualifier still caps each attempt at140 seconds; the original
owner hold, native alarm and cleanup limits remain intact.

Drain failed before its watchdog/charge pause: the retained failed root script
contains `head -c 262145 /sys/kernel/debug/wakeup_sources`. Its transport exception
was swallowed into stage_failed=1, attempted=0. No watchdog or wakeup-before file
was produced. Retained evidence does not distinguish a missing mount from a read
denial. Wakeup attribution now falls back to `/sys/class/wakeup`; if both reads
fail, the hour still runs and records wakeup_sources_recorded=0. Charging,
battery floor, residency, coverage and restoration checks remain mandatory.
Failures after admission now include a numeric failed_phase.

`test-integ27-replay.py` replays the exact retained numeric sequence: drain
failure, cleanup PASS, four skips and PIN PASS. Frozen eda8a0d code reproduces
the original drain result and all four complete skip records byte for byte.
Fixed code attempts the simulated hour and all four real qualifier paths.
Fake codecs time out, so this test supplies no hardware success. It also checks
the actual Android shell's sysfs fallback and interruption propagation.

Prepare a separate host runtime; do not edit the running/frozen image directory:

```sh
python3 -B scripts/mainline/acceptance/prepare-integ27-rerun.py \
  --image /home/user/A9-build/mainline/integ-20261024-27 \
  --out /home/user/A9-build/mainline/integ27-harness-rerun-20261025
```

The preparer verifies original image/runtime hashes, copies the frozen assets,
and overlays current host onb.py, drain.py and variants.py. It updates only the
qualification manifest's host variants.py pin; all native binary/media and
encoder receipt hashes remain unchanged. It verifies the copied manifests and
creates a new launcher/hook. No image assembly or USB command runs during prep.
The hook includes main's USB/restart row independence fix too.

After the coordinator's current session ends, use the usual FORMAT1/HOLD7200,
fresh-disposable-formatted-b PIN, HVENC/variants and DRAIN1 flags with the new
`run.sh`. It uses the original integ27 boot/vendor images under the same b-try
owner. This is a new sweep, not an injection into a running hook.
