# Display investigation tools

**Date:** 2026-10-12. **Status:** accepted host tools assembled for source review;
no main promotion or runtime/physical reverse engineering approved by this task.

The four analyzers read user-supplied files without executing vendor code,
modifying inputs or contacting a device. Keep raw vendor binaries, logs, saves
and evidence-derived reports outside Git. Software counters and gray targets
remain separate from optical state, physical completion and optical FPS.

## Saved software logs

From the repository root, use Python 3.9+ and supply the year of the first dated
record, explicit bounds in the log's unspecified local wall-clock domain, and
the scope the original evidence actually supports:

```sh
python3 scripts/epd_log_report.py /path/outside-git/engine.log --year 2026 \
  --start 2026-10-12T04:00:00.750 --stop 2026-10-12T04:01:00.750 \
  --window-scope stimulus --output /path/outside-git/new-report.json
```

The window is `[start,stop)`. Use `acquisition` if it includes surrounding
capture work, or `unspecified` if its scope is unknown. Exact saved BEGIN/END
messages can replace timestamp bounds; see the [parser contract](epd-offline-log-report.md)
for marker selection, invalid-run declarations and separate kernel-uptime windows.
Experimental validity defaults to unknown; parsing cannot validate acquisition.

Only when justified by source evidence, append `--mode-code-mask 0xff` to declare
a comparison assumption for compact mode pairs versus full metadata fields.
The default compares exact values; the mask preserves original fields and does
not establish firmware semantics. Engine batch counts, reported waveform-frame
sums and lifecycle events have separate units. Quiet logs do not prove a quiet
panel; none of these counters measures optical FPS or completion.

## Known-binary stored-table inventory

```sh
python3 scripts/a9_composer_tables.py /path/outside-git/vendor.qti.hardware.display.composer-service \
  --output /path/outside-git/new-inventory.json
```

The [inventory contract](a9-composer-tables.md) pins one complete SHA256 and size,
checks ELF/table structure, and reports aggregate stored headers, payload-code
counts, zero tails and slot 256. Unknown binaries are refused; there is no
identity bypass. Stored lengths and byte counts do not prove runtime sequencing,
transition-axis meaning, polarity, temperature selection or physical behavior.
All four tools' `--output` options refuse existing destinations. A write failure
can leave a partial new file; ordinary shell stdout redirection can overwrite files.

The accepted [software-target notes](epd-panel-screenshot.md) distinguish composer
targets from pigment state. The corresponding `epd-snap.sh` header correction
preserves its executable body; this workflow requires no acquisition commands.

## Conditional request model and behavior notes

The [clear-request lifecycle note](a9-clear-request-lifecycle.md) separates
sysfs/event success, the userspace clear latch, handler admission and duplicate
image suppression. None alone proves physical clearing.

The [request model](a9-composer-requests.md) uses the same exact known binary
plus explicit synthetic state and execution assumptions. It combines decoded
numeric metadata with separately reviewed control assertions to report request
steps, a final pass and conditional nominal producer iterations. Unsupported
gate/counter, unstable-count, interrupted or suppressed states yield unknown
sequence/count predictions. Supplied state is not current device telemetry.

The request model's original gate-zero behavior remains unchanged. The separate
[gate-zero count model](a9-composer-startup.md) covers the newly accepted
conditional path using a supplied synthetic bin and explicit state, ring,
admission and completion assumptions:

```sh
python3 scripts/a9_composer_startup.py /path/outside-git/vendor.qti.hardware.display.composer-service \
  --temperature-bin 9 --gate-bit0 0 --initial-counter 0 \
  --stable-bin yes --no-intervening-writes yes --valid-ring yes \
  --admitted yes --ring-progress yes --completion uninterrupted \
  --output /path/outside-git/new-startup-count.json
```

This predicts 67 produced software frames only under all declared conditions.
Gate one, nonzero counter or any unsupported assumption yields unknown/null;
invalid or missing declarations yield exit 2 and no report. Headers are decoded
from the exact binary; control meaning comes from separate manual proof receipts.
No sequence payload, temperature acquisition or physical completion is predicted.

Keep three evidence layers separate: the inventory describes stored bytes;
the two count models predict nominal counts under declared conditions; the log
parser reports saved software observations without validating acquisition or
those conditions. None establishes optical completion or FPS.

The accepted [behavior notes](a9-composer-behavior.md) summarize the examined
binary's reviewed image conditioning, request construction and producer paths,
with their evidence boundaries and remaining questions. Regenerating numeric
metadata does not verify the manual control-flow assertions.

The [scheduling notes](a9-composer-scheduling.md) document reviewed working/pending
snapshot ownership, generated-subframe buffering and distinct software milestones.
The [dither stability study](a9-dither-stability.md) defines synthetic target-change
measurements, screen/world comparisons and their control/composition limits.
The [measurement boundaries](a9-display-measurement-boundaries.md) synthesize
accepted emitted-byte, submission and kernel-wait findings, with selected saved-log
fits and the distinct milestones that remain separate from unmeasured optical output.
The [temperature-input notes](a9-temperature-inputs.md) document reviewed cached
input ranges, error behavior and the conditional ADC integer scale, with their
freshness, calibration and physical-identity limits.

## Focused verification and sibling study

Run all four synthetic test modules in one host process:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=scripts/tests python3 -m unittest \
  test_epd_log_report test_a9_composer_tables test_a9_composer_requests \
  test_a9_composer_startup -v
```

See the [parser tests](../scripts/tests/test_epd_log_report.py),
[inventory tests](../scripts/tests/test_a9_composer_tables.py),
[request-model tests](../scripts/tests/test_a9_composer_requests.py) and
[gate-zero count tests](../scripts/tests/test_a9_composer_startup.py). Synthetic
fixtures need no private logs, binaries, ROMs, saves or device; they do not measure optics.
The sibling game's [source branch](https://github.com/dmayala/gen1recomp-a9/src/branch/a9-display-study)
assembles the reviewed [host harness](https://github.com/dmayala/gen1recomp-a9/src/commit/3d2685429a97d6a2deca087fb66a538405ae2aad/docs/a9-display-lab.md)
and [research design](https://github.com/dmayala/gen1recomp-a9/src/commit/aa1bc3273ca05399acb0d4fd60c97a926f5ca46a/docs/a9-display-research.md).
Their host workload metrics are a separate evidence layer from these saved-file
reports. The scheduling and dither documents add reviewed host findings;
they do not establish optical performance or change runtime behavior.

Accepted input heads: parser `a7cf57ba15280abf2d66597a467acc42980fd3fd`,
software-target docs/comments `5d67ac4f0272cd32f7551868db96cc3d21a46d4b`,
inventory `317692c19cb43b8b852fd25f12e56f0cc45cff8f`,
request model `d5bfefd61695f1eec4b1473be922de6f2068dbc9`,
behavior notes `fd1ddfe8d23308bb348e7a51daa0f908469e386c`.
