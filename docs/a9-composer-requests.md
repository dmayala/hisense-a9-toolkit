# A9 composer request model

2026-10-12. Status: host-only analytical model from corrected, independently
reviewed control facts. No vendor firmware is executed. Supplied state is
synthetic; output is not verified current device telemetry or a physical
display simulation.

## Input and reproduction

Run with Python 3 and the standard library. The sole supported private binary
is `vendor.qti.hardware.display.composer-service`, 2,049,912 bytes, SHA256
`8c89557bf47b57b1687b9fbcc3e55bedbee175d20f80d14bc5317fcf893df5ed`.
The entire 256-bit digest must match. The tool reuses the accepted
[table audit](a9-composer-tables.md) reader and ELF header, load-segment and
rodata validation; no hash-prefix gate is used. Inputs must be regular files
and are opened read only. Symlinks and special files are refused.

```sh
python3 scripts/a9_composer_requests.py /private/path/to/composer \
  --pre-mode 1 --current-mode 6 --clear-in 0 \
  --temperature-format raw-byte --temperatures 28 28 \
  --lookup-gate 1 --initial-counter 0 --well-formed-count yes \
  --completion uninterrupted --suppressed no \
  --output /tmp/a9-request-example.json

python3 scripts/a9_composer_requests.py --help
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s scripts/tests -p 'test_a9_composer_requests.py' -v
```

The example yields kind 1, flag 0, N 6, count 5, active types `[1,6,6,6,6]`, then
final type 6, temperature index 28/bin 9 and **55 nominal producer iterations**.
The final pass is additional to the five steps. Counts do not measure
physical refreshes or distinct table-column reads.

Default output is a single JSON record on stdout; redirect outside Git.
`--output` creates a new file with mode 0600 and exclusive creation. Existing
files, hardlink aliases, symlinks (including dangling links), and input aliases
are refused. Inputs are never modified. No vendor bytes, opcodes, ELF,
disassembly, table payloads, or raw logs are shipped or emitted by this model.

## CLI state schema

All state flags except `--prior-fields` are required. Decimal integers are
used; booleans are the literal CLI choices `yes`/`no`.

| Argument | Domain and meaning |
|---|---|
| `binary` | Exact known private ELF |
| `--pre-mode`, `--current-mode` | uint32, low byte in `{1,2,3,6,8,9}`; upper bits ignored by this model |
| `--clear-in` | uint32; zero versus nonzero external clear input |
| `--temperature-format` | `raw-byte` or `signed-byte`, explicitly declared |
| `--temperatures T1 T2` | Two integers in the declared byte domain; first reading gets weight 2 |
| `--lookup-gate` | Reviewed gate **bit 0**, `0` or `1`; not a whole global word |
| `--initial-counter` | uint32 initial frame counter; prediction requires 0 |
| `--well-formed-count` | `yes` asserts a stable producer count 0..5 with no concurrent state mutation |
| `--completion` | `uninterrupted` or `interrupted` |
| `--suppressed` | `no` asserts downstream state allows the update; `yes` is unsupported for counts |
| `--prior-fields KIND FLAG N COUNT` | Optional four prior uint32 fields; all are reset on entry |
| `--output` | Optional new JSON path, preferably outside Git |

Invalid input or missing declarations exit 2 with bounded errors and create
no report. An unsupported but well-formed execution state exits 0 with a report
whose `prediction.status` is `unknown`, `sequence` and
`nominal_producer_iterations` are null, and `reasons` explains the refusal.
This includes gate 0, nonzero initial counter, interrupted/suppressed updates,
or absence of the well-formed/stable-count assumption. Structural request
fields remain available: they describe the reviewed producer construction
before downstream state can suppress execution.

## JSON schema and provenance

The version 1 output has these fields:

| Field | Meaning |
|---|---|
| `schema_version`, `model` | `1`, `a9-composer-requests-v1` |
| `provenance.decoded_numbers` | Full binary identity, ELF validation, numeric metadata with file offsets and strides |
| `provenance.reviewed_control_assertions` | Explicit manual-control trust boundary and corrected proof report SHA |
| `provenance.supplied_state_assumptions` | Exact supplied synthetic state, including optional prior fields |
| `request` | kind, flag, N, count, active types, clear input/output, final type and prior/current low bytes |
| `temperature` | Raw bytes, effective terms, weighted sum, final temperature index and bin |
| `prediction` | `nominal`/`unknown`, reasons, ordered step-plus-final records and nominal producer iteration total |
| `semantic_limits` | Scope limits for interpretation |

Every sequence record has `role` (`step` or `final`), `step_index` (zero-based
or null for final), `type`, and `lookup`. Lookup includes stored family,
nominal iterations, ordered path phases, table-lookup iteration count, and
terminal counter 0 under the declared regular assumptions. The report does
not describe actual cache contents or individual table columns read.

Numeric metadata is regenerated from the exact input: all 14 nominal lengths
per family (checked across 256 regular row headers), all 51 temperature-bin
entries, and 14 count/type vectors. Type 8's use of the Smooth family and the
vectors' immediate tails are explicitly identified as reviewed assertions.
The separate manual rules establish transition dispatch, reset, final
override, cache paths and gate/counter requirements. Regenerating numeric
data does not machine-verify these control rules.

The accepted corrected report is *Independent composer producer / counter
proof review — 2026-10-12 UTC*, SHA256
`fe1eb05737d04f9d4d24e6f80896aee0e2b0d167c192c3a33f2db7e90d9e3030`.
Its private limited instruction interpreter checked 72 producer cases and 98
lookup-counter cases. That interpreter is not committed or executed by this
tool. Review anchors include reset at 0x1a661c, transition stores in
0x1a6670..0x1a6ab0, forced clear at 0x1a696c, final selection at
0x1a7a74..0x1a7a8c, temperature processing at 0x1a7a94..0x1a7b0c,
gate branch at 0x1a7c74, and lookup counters at 0x1d1690..0x1d1d48.
Addresses are provenance numbers, not shipped instructions.

## Corrected request rules

kind/flag/N/count reset to 0 before dispatch. Retained inactive type slots
cannot become steps when count 0. The header-only transitions 2/3->1 give
kind 1/flag 1/N 0/count 0; 1->3 gives kind 1/flag 0/N 0/count 0. Mode 8 defaults
have zero steps. For clear input 0 the active types are:

| Prior / current | 1 | 2 | 3 | 6 | 8 | 9 |
|---|---|---|---|---|---|---|
| 1 | [] | [1,1] | [] | [1,6,6,6,6] | [] | [1,9,9,9,9] |
| 2 | [] | [] | [] | [3,6,6,6,6] | [] | [3,9,9,9,9] |
| 3 | [] | [] | [] | [3,6,6,6,6] | [] | [3,9,9,9,9] |
| 6 | [6] | [6,6,6,6] | [6] | [] | [] | [6,9,9,9,9] |
| 8 | [] | [] | [] | [] | [] | [] |
| 9 | [9] | [9,9,9,9] | [9] | [9,6,6,6,6] | [] | [] |

Kind 1 transitions retain these steps when the external clear input is
nonzero, including header-only transitions. Other nonzero-clear pairs give
kind 2/N 0/flag 0: current 3 adds `[3]`, current 6 adds five 6s, current 9 adds
five 9s, and current 1/2/8 add no steps. Prior 6/9->current 1/3 forces
clear output 1 even when input 0. Final is the current low byte except final 1/3
with nonzero clear output becomes type 2. It runs after the last active step.

Under regular preconditions, the nominal count is the sum of active-step
lengths plus final length. Gate 0 uses another sequence whose lifecycle is
unresolved; nonzero starting counters and exceptional or suppressed paths
cannot use that sum.

## Temperature and cache paths

`raw-byte` accepts 0..255; `signed-byte` accepts -128..127. Signed negatives
are converted to their byte representation. **Bit 7 zeroes a reading before
weighting**, then each remaining reading is capped at 50. Sum is twice the
first effective term plus the second. The final guards are sum < 3 -> index 0,
sum > 152 -> index 50, otherwise integer sum/3. Within either byte domain the
sum cannot exceed 150, but the reviewed upper guard remains expressed.

Signed `(-20,50)` gives effective terms `(0,50)`, index 16/bin 5.
Raw `(128,0)` gives `(0,0)`, index 0/bin 0. Neither follows a naive
minimum-only weighting rule. All 14 bins are decoded from the exact binary:
0..32 in groups of 3, then 33..37 -> 11, 38..43 -> 12, 44..50 -> 13.

For nominal length L, Smooth 6/8 and Speed 9 use one first table lookup,
L - 2 cached middle copies and one last-buffer copy. Speed uses its own stored
family. Balanced 1 with flag 0 uses one first lookup, L - 3 cached copies and
two last-buffer copies; flag 1 uses table lookup throughout. Types 2/3 also
use full lookup. These are control-path classifications, with no assertion
about copied pixel content.

Tests use independent fixed review matrices and invented ELF metadata,
including poisoned reset fields, all 72 pair/clear cases over 14 bins,
98 lookup path classes, all 65,536 raw byte pairs and corresponding signed
pairs, observed nominal totals, unsupported assumptions, exact-hash refusal,
ELF validation, exclusive output, aliases and bounded CLI failures.

No device access, mode activation, native build, firmware/driver patch,
backup changes, payload reconstruction, polarity, gray-level, ghosting,
FPS or safe-voltage claims are part of this model.
