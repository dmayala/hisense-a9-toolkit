# Offline EPD software log reports

**Date:** 2026-10-12

**Status:** Host parser with synthetic contract tests. Saved logs remain outside Git.

`scripts/epd_log_report.py` turns saved Android EPD logcat records into a JSON
report without connecting to a phone. It counts software events and preserves
incomplete update records. A batch's `total frame num` reports waveform frames;
it does not count unique content images or establish physical completion.
PanelOpen/PanelClose and optional kernel PMIC events are separate counters.
Physical content updates, optical completion times and SurfaceFlinger submissions
are explicitly unavailable (`null`).

The parser uses Python 3.9 or newer and the standard library. It runs no ADB,
service, shell acquisition, install, setting or refresh command. Reading a saved
log cannot establish whether its original acquisition succeeded or whether a
stimulus was valid.

## Run a report

For a saved `logcat -v threadtime` file, supply the year of its **first dated
record**. The file itself supplies month/day, local wall time, PID and TID:

```sh
python3 scripts/epd_log_report.py /path/outside-git/engine.log --year 2026
```

JSON goes to stdout. `--output /tmp/new-report.json` exclusively creates a new
file and refuses to overwrite an existing file. Input paths and SHA256 digests
are recorded; original message text is omitted except for caller-selected marker
messages. Keep reports outside Git when they derive from private evidence.

Use full timestamps to select an inclusive start and exclusive stop:

```sh
python3 scripts/epd_log_report.py /path/outside-git/engine.log --year 2026 \
  --start 2026-10-12T04:00:00.750 --stop 2026-10-12T04:01:00.750 \
  --window-scope stimulus --output /tmp/new-stimulus-report.json
```

Fractions up to nine decimal places are retained as integer nanoseconds. This
representation does not increase the source clock's resolution. Timezone
suffixes are rejected: these bounds use the same unspecified local wall-clock
domain as logcat, rather than assuming UTC. A time-only or kernel-uptime file
cannot substitute for dated logcat input. Forward December-to-January order
infers a new year and records that inference. A later December after that
rollover is ambiguous; split such sessions rather than guessing its year.

## Marker windows and invalid experiments

A saved log may include a replayed buffer from before capture. If the operator
wrote exact begin/end marker messages, select them explicitly:

```sh
python3 scripts/epd_log_report.py /path/outside-git/engine.log --year 2026 \
  --begin-marker BEGIN_example --end-marker END_example \
  --window-scope acquisition --output /tmp/new-acquisition-report.json
```

Each complete message must match exactly once. Missing, duplicate, identical or
reversed markers fail. Marker and explicit timestamp bounds cannot be combined.
Events must be after the begin line, before the end line, and within the marker
timestamps' `[start,stop)` interval. A record sharing the end timestamp is
excluded even if it appears earlier in the file; the source timestamp cannot
resolve its sub-timestamp ordering. The report preserves both marker records.

`--window-scope acquisition|stimulus|unspecified` describes the operator's
window. Acquisition markers surrounding counters/screenshots as well as the
stimulus do not isolate the stimulus. Defaults are `unspecified` scope and
`unknown` experimental validity. The parser cannot certify a clean experiment.

Preserve a failed experiment's label while reporting its observed events:

```sh
python3 scripts/epd_log_report.py /path/outside-git/engine.log --year 2026 \
  --begin-marker BEGIN_failed --end-marker END_failed \
  --run-validity invalid --invalid-reason 'stimulus pulled notification shade' \
  --window-scope acquisition --output /tmp/new-invalid-report.json
```

An invalid declaration requires a reason. A valid declaration is caller-supplied
provenance, not validation by this parser. Neither a zero event count nor a
well-formed report establishes absence of physical refreshes.

## Update association and summaries

Metadata is recognized from `clear=...,temp=...,...mode=...`. A later
`total frame num=...` is associated only within the same PID/TID and source
order. There is no unique hardware update ID in these messages. Decimal and
hexadecimal modes normalize to hexadecimal; numeric suffixes such as `8BROKEN`
or `8.5` are rejected as malformed fields.

The report contains:

- Exact per-record source line, timestamp, PID/TID and software waveform count.
- Engine batch-event count and a waveform-frame sum, each with its own units.
- Batch-length and mode counts, metadata `clear=1` counts, mode requests and
  observed same-thread mode pairs.
- PanelOpen start/complete and PanelClose complete records, independently of
  engine batches. Event-count keys absent from the map have zero parsed events;
  `summary.engine_batch_events` is always present.
- Unpaired totals, replaced/unclosed metadata, parse gaps, malformed records,
  conflicting mode context, backward clocks and window-crossing records.
- Per-PID/TID inter-batch log-event gap statistics, with sample count, median,
  nearest-rank p95, minimum and maximum. No rate is labelled optical FPS.

Mode pairs are consumed by the next metadata record on their own PID/TID. A
cross-thread `set next_display_mode` request is retained separately and cannot
prove which batch switched. A conflicting mode pair versus metadata becomes
unknown context rather than qualifying a batch as steady. Successive complete
metadata modes can independently show a change.

Only fully in-window paired batches with known unchanged mode and `clear=0`
enter the steady-state count. Mode-switch batches, clear-one batches and other
or unknown batches have separate count maps. The first batch is unknown unless
consistent same-thread mode-pair evidence establishes its context. Steady gaps
require both consecutive total records to be eligible and share a mode; they
never bridge an intervening switch or unknown total record.

Malformed EPD fields invalidate that stream's pending metadata and mode context.
An unparseable line with unknown identity conservatively invalidates pending
contexts; an invalid dated record with identifiable PID/TID invalidates that
stream. Headers and blank lines do not. A backward timestamp invalidates the
matching stream's context and splits its gap sequence. Global inter-thread
logging jitter is surfaced as a diagnostic while retaining individually ordered
stream associations. Records are never silently sorted or deduplicated.

Association is performed on the complete source before window selection. If
metadata is before the start and its total record is in the window, the batch
count includes the total event and the record carries out-of-window context.
Metadata in the window with a total after the stop is retained as a boundary
record but adds no total-event count. Both are excluded from steady state.
Metadata-to-total intervals are **log-event gaps**, not measured drive duration.

`parse_status=partial_or_discontinuous` warns of malformed/unparsed input,
backward timestamps or conflicting mode context. An entirely unparseable/empty
source fails with exit 2; a valid dated log with no EPD events reports zero
software events. These are distinct outcomes. Truncation without an explicit
marker or incomplete record may remain undetectable; source completeness and
acquisition success require external provenance.

## Mode code domains

`Pre_Mode` and `cur_mode` may be compact mode codes while metadata's `mode` is a
bitfield. For example, saved A9 software logs contain `6 -> 1` alongside
`0x201` metadata. The default compares exact numerical identity and flags
mismatched context. Raw mode-pair changes remain counted separately regardless
of whether a batch can be attributed to them.

If evidence establishes a specific comparison mask for the source, declare it:

```sh
python3 scripts/epd_log_report.py /path/outside-git/engine.log --year 2026 \
  --mode-code-mask 0xff --output /tmp/new-declared-mask-report.json
```

The mask applies to previous/current pair values and the metadata mode for the
comparison; original values and full metadata modes remain in the output.
`mode_code_comparison` records this caller-declared assumption. A mask does not
prove firmware semantics. A matching pair is labelled
`consistent_with_declared_mask`; a mismatch remains conflicting and excluded
from steady state. No A9-specific mask or mode table is silently selected.

## Optional kernel lifecycle evidence

A separate saved dmesg file with bracketed uptime can accompany the logcat file:

```sh
python3 scripts/epd_log_report.py /path/outside-git/engine.log --year 2026 \
  --kernel-log /path/outside-git/kernel.log \
  --kernel-start 100.000 --kernel-stop 160.000 \
  --output /tmp/new-two-clock-report.json
```

Kernel bounds are their own `[start,stop)` uptime interval. Without kernel
bounds, the whole supplied kernel file is summarized. Logcat bounds never apply
to kernel evidence; no wall-clock/uptime conversion or latency join is inferred.
The kernel subsection has its own digest, parse counts, diagnostics and
`pmic_power_on`, `pmic_enter_sleep_end`, and `panel_1v8_power_off` events. An
absent kernel file yields `kernel=null`, rather than pretending zero PMIC events
were observed. Lifecycle counts do not count unique content refreshes or optical
completions. Several engine batches can occur during one powered interval.

## Verification

```sh
python3 -m unittest discover -s scripts/tests -p 'test_epd_log_report.py' -v
```

Synthetic tests cover precise start/stop bounds, stale dates, midnight/year
rollover, invalid dates, nanosecond representation, mixed PID/TID, missing or
replaced metadata, repeated totals, concurrent timestamp jitter, malformed
numeric tokens, conflicting mode context, parse gaps, marker ambiguity, invalid
experiment labels, separate kernel clocks and output-file preservation. No
phone, ROM, save, vendor binary, dependency download or private log is needed.

The accompanying measurement design separates host generation, update,
render submission, host swap, Android composition/engine and physical panel
measurements. This parser supplies only saved software-log observations; camera
or human panel observations require separate provenance.
