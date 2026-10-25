#!/usr/bin/env python3
"""Offline conditional gate-bit-zero count model for one exact private composer."""

import hashlib
from pathlib import Path
import sys

import a9_composer_requests as requests
import a9_composer_tables as tables


MODEL_ID = "a9-composer-startup-v1"
TABLE_OFFSET = 0x2B103
BIN_STRIDE = 120
LENGTHS = (109, 93, 81, 71, 61, 111, 95, 83, 75, 67, 63, 59, 55, 51)
REPORT_SHA256 = "e5d9fe6caf43c3a6dbdd630e9844690abda40ba79800cdc8c648e03a91a19c3b"
MANIFEST_SHA256 = "5f9f2974f1fe4336fb3052e1bfaa250e7dec076ae3a5eaf985ea9081a49cb8e4"
REVIEW_SHA256 = "33108aa446a7a62daaf6a70ba54f575868df98702e49dd4f9c672d9e764175b9"
BOOL_FIELDS = ("stable_bin", "no_intervening_writes", "valid_ring", "admitted", "ring_progress")
STATE_FIELDS = set(BOOL_FIELDS) | {"temperature_bin", "gate_bit0", "initial_counter", "completion"}
CONTROL_ASSERTIONS = {
    "trust_boundary": "Manually accepted gate-zero FINALrev4 and independent review; "
                      "decoding headers does not verify instructions or regenerate the proof.",
    "report_sha256": REPORT_SHA256,
    "manifest_sha256": MANIFEST_SHA256,
    "independent_review_sha256": REVIEW_SHA256,
    "review_scope": "Original instruction checks and all 14 headers; 67 full-bin9 frames "
                    "plus 10 selected frames, ring-full deferral and conditional handoff. "
                    "Author all-bin batches were not independently rerun.",
    "path": "Gate bit0 zero ignores request count, step types and final type; "
            "bin and length are reloaded each produced frame.",
    "completion": "From counter zero with the declared assumptions, produces L(bin) "
                  "software frames then writes whole gate byte 1 and counter 0.",
    "ring_full": "Wait/recheck passes defer production and count no frame.",
    "geometry": {"rows": 829, "words_per_row": 448, "word_bytes": 4,
                 "written_byte_lane": 0, "preserved_byte_lanes": [1, 2, 3]},
    "initialization_scope": "Zero BSS and one identified init reset establish possible "
                            "gate/counter zero states, not every wake or admission. "
                            "At-most-once init requires sequential calls, unchanged object/flag "
                            "and successful initialization; writer scans are not exhaustive.",
}
LIMITS = [
    "Offline analytical count model; no vendor code is executed.",
    "Supplied bin and state are synthetic declarations, not live telemetry or temperature acquisition.",
    "Count unit is produced software frames, not optical refreshes, distinct codes or physical FPS.",
    "Ring progress permits eventual production; no completion time or consumer/display completion is predicted.",
    "Gate bit0 one, nonzero initial counter and changing/interrupted state are unmodeled; no partial count or range.",
    "No sequence payload, full renderer simulation, polarity, voltage, gray level or physical waveform meaning.",
    "No universal writer inventory, initialization ordering, every-wake or automatic next-batch admission claim.",
]


def decode_headers(data):
    """Decode only numeric headers; caller must establish exact identity and ELF."""
    end = TABLE_OFFSET + len(LENGTHS) * BIN_STRIDE
    tables.require(tables.RODATA[4] <= TABLE_OFFSET
                   and end <= tables.RODATA[4] + tables.RODATA[5], "gate-zero table outside rodata")
    tables.bounded_slice(data, TABLE_OFFSET, end - TABLE_OFFSET)
    bins = []
    for index, expected in enumerate(LENGTHS):
        offset = TABLE_OFFSET + index * BIN_STRIDE
        length = tables.bounded_slice(data, offset, 1)[0]
        tables.require(0 < length < BIN_STRIDE and length == expected,
                       "unexpected gate-zero length header")
        bins.append({"bin": index, "header_file_offset": offset, "length_header": length})
    return {"table_file_offset": TABLE_OFFSET, "table_virtual_address": TABLE_OFFSET,
            "bin_stride_bytes": BIN_STRIDE, "bin_count": len(bins),
            "header_width_bytes": 1, "end_exclusive": end, "bins": bins}


def load_headers(path):
    data = tables.read_input(path)
    digest = hashlib.sha256(data).hexdigest()
    tables.require(digest == tables.SUPPORTED_SHA256, "unsupported or corrupt input SHA256")
    elf = tables.validate_elf(data)
    return {"binary_sha256": digest, "binary_size_bytes": len(data), "elf": elf,
            "validation": "full SHA256, accepted ELF/layout validation and all 14 length headers",
            "headers": decode_headers(data)}


def validate_state(state):
    tables.require(isinstance(state, dict) and state.keys() == STATE_FIELDS,
                   "state requires exactly the documented explicit fields")
    requests.integer(state["temperature_bin"], 0, 13, "temperature_bin")
    requests.integer(state["gate_bit0"], 0, 1, "gate_bit0")
    requests.integer(state["initial_counter"], 0, 0xFFFFFFFF, "initial_counter")
    for field in BOOL_FIELDS:
        tables.require(type(state[field]) is bool, f"{field} must be boolean")
    tables.require(state["completion"] in ("uninterrupted", "interrupted"),
                   "unsupported completion assumption")


def predict(decoded, state):
    validate_state(state)
    selected = decoded["headers"]["bins"][state["temperature_bin"]]
    reasons = []
    if state["gate_bit0"] != 0:
        reasons.append("gate bit0 one uses the regular path, outside this model")
    if state["initial_counter"] != 0:
        reasons.append("nonzero initial counter is outside the reviewed zero-counter count rule")
    for field, reason in (
        ("stable_bin", "bin stability across the entire batch is not assumed"),
        ("no_intervening_writes", "absence of intervening gate/counter/bin writes is not assumed"),
        ("valid_ring", "valid successfully initialized ring buffers and indices are not assumed"),
        ("admitted", "batch admission to production is not assumed"),
        ("ring_progress", "consumer progress providing eventual space for every frame is not assumed"),
    ):
        if not state[field]:
            reasons.append(reason)
    if state["completion"] != "uninterrupted":
        reasons.append("uninterrupted production through the terminal iteration is not assumed")
    return {"schema_version": 1, "model": MODEL_ID,
            "provenance": {"decoded_metadata": decoded,
                           "reviewed_control_assertions": CONTROL_ASSERTIONS,
                           "supplied_synthetic_assumptions": dict(state)},
            "selected_bin": dict(selected),
            "prediction": {"status": "unknown" if reasons else "nominal", "reasons": reasons,
                           "count_unit": "PRODUCEDSOFTWAREFRAMES",
                           "nominal_produced_software_frames": None if reasons else selected["length_header"],
                           "conditional_terminal_state": None if reasons else {"gate_byte": 1, "counter": 0}},
            "semantic_limits": LIMITS}


def main(argv=None):
    # Reuse the accepted bounded parser, decimal/domain helpers and audited I/O;
    # neither prior analyzer's API nor behavior is changed.
    parser = requests.BoundedParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("binary", type=Path, help="exact supported private ELF, read only")
    parser.add_argument("--temperature-bin", required=True, type=requests.cli_integer,
                        help="explicit synthetic bin 0..13; no temperature acquisition/recalculation")
    parser.add_argument("--gate-bit0", required=True, type=requests.cli_integer, help="0 or 1")
    parser.add_argument("--initial-counter", required=True, type=requests.cli_integer, help="uint32")
    for field, help_text in (
        ("stable_bin", "bin remains unchanged across the entire batch"),
        ("no_intervening_writes", "no external/intervening gate, counter or bin writes during the batch"),
        ("valid_ring", "successfully initialized valid buffers, pointers and ring indices"),
        ("admitted", "batch is admitted to the producer"),
        ("ring_progress", "consumer eventually provides ring space for every produced frame"),
    ):
        parser.add_argument("--" + field.replace("_", "-"), required=True,
                            choices=("yes", "no"), help="assume " + help_text)
    parser.add_argument("--completion", required=True, choices=("uninterrupted", "interrupted"),
                        help="production through completion without exit, abort or trap")
    parser.add_argument("--output", type=Path, help="new exclusive JSON file outside Git (default: stdout)")
    args = parser.parse_args(argv)
    state = {field: getattr(args, field) for field in STATE_FIELDS}
    for field in BOOL_FIELDS:
        state[field] = getattr(args, field) == "yes"
    try:
        validate_state(state)
        if args.output is not None:
            try:
                parent = args.output.parent.resolve()
            except RuntimeError as error:
                # Python 3.9-3.12 report resolution loops as RuntimeError.
                raise tables.AuditError("cannot resolve output parent (symlink loop)") from error
            tables.require(not any((p / ".git").exists() for p in (parent, *parent.parents)),
                           "output must be outside Git worktrees")
        result = predict(load_headers(args.binary), state)
        tables.write_output(args.output, result)
    except (tables.AuditError, OSError, ValueError) as error:
        print(f"a9_composer_startup: {str(error)[:400]}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
