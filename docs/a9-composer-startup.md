# Conditional gate-zero software-frame count

`scripts/a9_composer_startup.py` is a fourth, separate offline analytical tool
for one exact privately supplied composer. It predicts only the conditional
gate-bit-zero batch count. It never executes, fetches or device-reads firmware.
The name describes a possible initialization path, not a claim about every wake.

The existing [request model](a9-composer-requests.md) keeps its original contract:
gate zero yields unknown there. This tool accepts a synthetic bin directly and
does not ask for prior/current mode, clear flag or request count: the reviewed
gate-zero branch ignores request steps and final type. Gate one belongs to the
regular request model and remains unknown here. No prior analyzer changes.

## CLI and explicit assumptions

From the repository root, with Python 3.9+:

```sh
python3 scripts/a9_composer_startup.py /path/outside-git/vendor.qti.hardware.display.composer-service \
  --temperature-bin 9 --gate-bit0 0 --initial-counter 0 \
  --stable-bin yes --no-intervening-writes yes --valid-ring yes \
  --admitted yes --ring-progress yes --completion uninterrupted \
  --output /path/outside-git/new-startup-count.json
```

This example conditionally predicts **67 produced software frames**. Every
state flag is required; there are no optimistic defaults. All numbers are
decimal integers, with booleans excluded from the Python state API.

| Flag | Domain and exact declaration |
|---|---|
| `--temperature-bin` | Synthetic integer 0..13; no acquisition, units, weighting or recalculation of temperatures. |
| `--gate-bit0` | Integer 0 or 1, the tested bit, not the whole gate byte. Nominal prediction requires 0. |
| `--initial-counter` | uint32 0..4294967295. Nominal prediction requires 0; even selected private nonzero cases do not support a general rule. |
| `--stable-bin` | `yes`/`no`: selected bin stays constant throughout the batch; the producer reloads it every frame. |
| `--no-intervening-writes` | `yes`/`no`: no external/intervening writes to gate, counter or bin during production, including resets/re-init. The sequence's own reviewed counter/gate stores are permitted. |
| `--valid-ring` | `yes`/`no`: successful initialization and valid buffers, pointers and ring indices for the entire batch. A zero gate alone establishes none of these. |
| `--admitted` | `yes`/`no`: the batch has been admitted to production; a zero gate alone does not establish admission. |
| `--ring-progress` | `yes`/`no`: consumer progress eventually provides space for every frame. Ring-full wait/recheck passes defer production and add no produced frames. |
| `--completion` | `uninterrupted`/`interrupted`: production continues through the terminal iteration without exit, abort or trap. This does not assert consumer, kernel or optical completion. |

These are user-supplied synthetic assumptions, never verified live facts.
Nominal count requires gate bit0 zero, counter zero, every `yes`, and
`uninterrupted`. Any well-formed unsupported state returns a report with status
`unknown`, null count, null terminal state and all applicable reasons. There is
no guessed partial count or range. Missing declarations or invalid domains,
unknown/corrupt binaries and I/O failures return exit 2 with bounded stderr and
no report. Successful nominal and unknown reports return exit 0.

`--output` uses the accepted exclusive writer: a new file with mode 0600;
existing files, symlinks (including dangling), hardlinks, input aliases and
special destinations are refused. This tool also refuses output under a Git
worktree (including worktrees with a `.git` file). Omit it for JSON on stdout;
keep shell redirection outside Git and remember it can overwrite files. A write
failure can leave a partial new file. The input reader requires a regular file
of the exact size and refuses symlink and special inputs without waiting on a FIFO.

## Decoded metadata and JSON v1

Exact binary identity is size **2049912** and SHA256
`8c89557bf47b57b1687b9fbcc3e55bedbee175d20f80d14bc5317fcf893df5ed`.
Full identity and the accepted ELF/load/rodata layout are validated before
decoding. There is no unsafe identity bypass. Only the 14 one-byte length
headers are decoded, at file/virtual address `0x2b103`, stride 120 bytes,
region ending at `0x2b793` exclusive. Each header is checked against the accepted
numeric expectation and row bounds. No sequence code or full payload is exported.

| Bin | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Header / conditional count | 109 | 93 | 81 | 71 | 61 | 111 | 95 | 83 | 75 | 67 | 63 | 59 | 55 | 51 |

The JSON object has `schema_version: 1` and model `a9-composer-startup-v1`:

- `provenance.decoded_metadata`: full binary identity, ELF metadata, validation
  description and header layout/offsets/lengths for all bins.
- `provenance.reviewed_control_assertions`: separate manual proof receipts,
  trust boundary, review scope, count/terminal assertions and aggregate geometry.
- `provenance.supplied_synthetic_assumptions`: all nine explicit state fields.
- `selected_bin`: selected bin, header file offset and decoded length. A stored
  length remains metadata even when the execution count is unknown.
- `prediction`: `status` (`nominal` or `unknown`), `reasons`, `count_unit`
  (`PRODUCEDSOFTWAREFRAMES`), `nominal_produced_software_frames` (integer or null),
  and `conditional_terminal_state` (`{"gate_byte":1,"counter":0}` only for
  nominal predictions, otherwise null). The terminal gate value is a whole byte.
- `semantic_limits`: execution, telemetry, timing, payload and lifecycle limits.

## Proof boundary

The accepted private FINALrev4 report SHA256 is
`e5d9fe6caf43c3a6dbdd630e9844690abda40ba79800cdc8c648e03a91a19c3b`,
its manifest SHA256 is
`5f9f2974f1fe4336fb3052e1bfaa250e7dec076ae3a5eaf985ea9081a49cb8e4`,
and the independent review receipt SHA256 is
`33108aa446a7a62daaf6a70ba54f575868df98702e49dd4f9c672d9e764175b9`.
The reviewer checked original instructions and all headers, executed 67 full-bin9
frames plus 10 selected frames, and checked routing, ring-full deferral and
conditional handoff. Author all-bin full batches were read, not independently
rerun. This analyzer does not repeat emulation or decode control instructions.
Reading the headers, or listing hardcoded expectations, does not regenerate or
independently establish the manual proof.

The reviewed slice writes byte lane 0 across 829 rows of 448 four-byte words;
lanes 1..3 preserve arbitrary prior contents. Aggregate geometry does not give
physical waveform meaning. Gate/counter zero in BSS and an identified init reset
do not prove valid initialization or production on every wake. At-most-once init
is qualified by sequential calls, unchanged object/flag and successful init;
writer scans do not establish universal completeness or all framework ordering.
Terminal gate one permits a later regular path only with further admission,
ring availability and no intervening writer; this tool predicts no next batch.
Counts establish no completion time, kernel milestone, optical refresh, FPS,
polarity, voltage, gray level or safe physical drive behavior.

## Focused verification and reused interfaces

The tool imports `read_input`, `validate_elf`, `bounded_slice`, `require`,
`AuditError` and `write_output` from `a9_composer_tables`, and `integer`,
`cli_integer` and `BoundedParser` from `a9_composer_requests`. These existing
implementation interfaces remain unchanged; no base API is expanded.

[Focused synthetic tests](../scripts/tests/test_a9_composer_startup.py) cover
fixed expectations for every bin, unsupported assumptions, invalid/missing
state, corrupt/truncated headers, exact identity/ELF refusal, bounded CLI errors,
private exclusive output and special-input handling through those interfaces.
Fixtures contain invented ELF layout and numeric headers only, no vendor code
or sequence payload. Run them with the existing three modules in one process
using the command in [the tools workflow](display-investigation-tools.md).
