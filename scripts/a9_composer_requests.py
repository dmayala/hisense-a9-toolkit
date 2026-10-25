#!/usr/bin/env python3
"""Host-only analytical request model for one exact private A9 composer ELF."""

import argparse
import hashlib
from pathlib import Path
import struct
import sys

import a9_composer_tables as tables


MODES = (1, 2, 3, 6, 8, 9)
REVIEW_SHA256 = "fe1eb05737d04f9d4d24e6f80896aee0e2b0d167c192c3a33f2db7e90d9e3030"
MODEL_ID = "a9-composer-requests-v1"
# Numeric vector locations; these contain count followed by type numbers.
VECTOR_WORDS = {
    0x2A850: 4, 0x2A8D0: 4, 0x2A8E0: 4, 0x2A920: 4,
    0x2A950: 4, 0x2A970: 4, 0x2A980: 4, 0x2A9F0: 4,
    0x2AA20: 4, 0x2AA40: 4, 0x2AA60: 2, 0x2AA88: 2,
    0x2AAC8: 2, 0x2AAD0: 2,
}
# Manually reviewed control assertions, NOT decoded from the data vectors.
# pair -> (N, flag, vector address, immediate type tail)
TRANSITIONS = {
    (2, 1): (0, 1, None, ()), (3, 1): (0, 1, None, ()),
    (1, 3): (0, 0, None, ()),
    (6, 1): (2, 0, 0x2AA60, ()), (6, 3): (2, 0, 0x2AA60, ()),
    (9, 1): (3, 0, 0x2AAC8, ()), (9, 3): (3, 0, 0x2AAC8, ()),
    (1, 2): (9, 0, 0x2AA88, (1,)),
    (6, 2): (7, 0, 0x2A980, (6,)),
    (9, 2): (8, 0, 0x2AA40, (9,)),
    (2, 6): (1, 0, 0x2AA20, (6, 6)),
    (3, 6): (1, 0, 0x2AA20, (6, 6)),
    (1, 6): (6, 0, 0x2A9F0, (6, 6)),
    (9, 6): (5, 0, 0x2A850, (6, 6)),
    (2, 9): (11, 0, 0x2A8D0, (9, 9)),
    (3, 9): (11, 0, 0x2A8D0, (9, 9)),
    (1, 9): (12, 0, 0x2A970, (9, 9)),
    (6, 9): (4, 0, 0x2A950, (9, 9)),
}
CLEAR_VECTORS = {3: (0x2AAD0, ()), 6: (0x2A920, (6, 6)),
                 9: (0x2A8E0, (9, 9))}
CONTROL_ASSERTIONS = {
    "evidence": "Independent composer producer / counter proof review, 2026-10-12",
    "review_report_sha256": REVIEW_SHA256,
    "status": "manually reviewed control flow; this tool does not decode instructions",
    "request_layout_offsets": {"kind": 0, "flag": 4, "N": 8, "count": 12,
                               "step_types": [16, 20, 24, 28, 32]},
    "reset": "kind, flag, N, count reset before same-mode and transition dispatch",
    "kind1_clear": "external nonzero clear retains kind 1 active steps",
    "forced_clear": "prior 6/9 to current 1/3 forces clear_out=1",
    "header_only": "2/3 to 1 and 1 to 3 have N=0, count=0",
    "final_type": "current low byte, except 1/3 with clear_out nonzero gives 2",
    "ordering": "every active step completes, then a separate final pass completes",
    "temperature": "bit 7 zeroes each byte before 2:1 weighting; each term capped at 50; "
                   "sum<3 gives 0, sum>152 gives 50, otherwise integer sum/3",
    "lookup_paths": "type 1 flag 0: first lookup, L-3 cache copies, two last-buffer copies; "
                    "type 1 flag 1 and types 2/3: full lookup; 6/8/9: first lookup, L-2 cache copies, "
                    "one last-buffer copy",
    "type8_family": "type 8 uses the Smooth stored family",
    "regular_preconditions": "lookup gate bit 0=1, initial counter=0, well-formed count, "
                             "stable state, uninterrupted completion, no suppression",
}
LIMITS = [
    "Analytical source-derived model; vendor firmware is not executed.",
    "Supplied state is synthetic, not verified current device telemetry.",
    "Counts are nominal producer iterations, not distinct table-column reads or optical refreshes.",
    "Gate 0 lifecycle, concurrent mutation, suppression and interrupted batches are unmodeled.",
    "No payload, polarity, gray levels, ghosting, FPS or safe-voltage prediction.",
    "Enumerating accepted types does not recommend activating unused modes.",
]
STATE_FIELDS = {"pre_mode", "current_mode", "clear_in", "temperature_format",
                "temperatures", "lookup_gate", "initial_counter", "well_formed_count",
                "completion", "suppressed"}


def integer(value, low, high, name):
    tables.require(type(value) is int and low <= value <= high,
                   f"{name} must be an integer in [{low}, {high}]")
    return value


def decode_numbers(data):
    """Decode numeric metadata only. Caller must establish full binary identity."""
    temperature = tables.inventory_temperature(data)
    lengths = {}
    sources = {}
    type_ids = {"type3": 3, "type2": 2, "balanced_stored": 1,
                "smooth_stored": 6, "speed_stored": 9}
    for spec in tables.TABLES:
        values = []
        for bin_index in range(14):
            start = spec.offset + bin_index * 257 * spec.stride
            rows = [tables.bounded_slice(data, start + slot * spec.stride, 1)[0]
                    for slot in range(256)]
            tables.require(all(n == spec.lengths[bin_index] for n in rows),
                           "unexpected nominal length headers")
            values.append(rows[0])
        key = str(type_ids[spec.name])
        lengths[key] = values
        sources[key] = {"file_offset": spec.offset, "bin_stride": 257 * spec.stride,
                        "slot_stride": spec.stride, "checked_regular_slots": 256}
    # Type8's use of the Smooth family is a reviewed control assertion.
    lengths["8"] = list(lengths["6"])
    vectors = {str(offset): list(struct.unpack("<" + "I" * words,
               tables.bounded_slice(data, offset, 4 * words)))
               for offset, words in VECTOR_WORDS.items()}
    return {"lengths_by_type": lengths, "length_sources": sources,
            "type8_family_alias": {"source_type": 6, "provenance": "reviewed control assertion"},
            "temperature_index_map": list(tables.bounded_slice(data, tables.TEMPERATURE_OFFSET, 51)),
            "temperature_map_metadata": temperature,
            "request_vectors": vectors,
            "vector_layout": "little-endian uint32 count followed by type numbers; "
                             "remaining immediate tails are reviewed assertions"}


def load_numbers(path):
    data = tables.read_input(path)
    digest = hashlib.sha256(data).hexdigest()
    tables.require(digest == tables.SUPPORTED_SHA256, "unsupported or corrupt input SHA256")
    elf = tables.validate_elf(data)
    return {"binary_sha256": digest, "binary_size_bytes": len(data), "elf": elf,
            "validation": "full SHA256 and accepted a9_composer_tables ELF/layout validation",
            "numbers": decode_numbers(data)}


def temperature_index(first, second, domain, mapping):
    tables.require(domain in ("raw-byte", "signed-byte"), "unsupported temperature format")
    low, high = (0, 255) if domain == "raw-byte" else (-128, 127)
    readings = [integer(t, low, high, "temperature") for t in (first, second)]
    raw = [t & 255 for t in readings]
    terms = [0 if t & 128 else min(t, 50) for t in raw]
    total = 2 * terms[0] + terms[1]
    index = 0 if total < 3 else 50 if total > 152 else total // 3
    tables.require(len(mapping) == 51 and 0 <= mapping[index] < 14,
                   "invalid decoded temperature mapping")
    return {"raw_bytes": raw, "effective_terms": terms, "weighted_sum": total,
            "index": index, "bin": mapping[index]}


def active_vector(numbers, offset, tail):
    if offset is None:
        return []
    vector = numbers["request_vectors"][str(offset)]
    count, *types = vector
    steps = types + list(tail)
    tables.require(count == len(steps) and 0 <= count <= 5 and all(t in MODES for t in steps),
                   "decoded vector disagrees with reviewed count/type contract")
    return steps


def request_fields(numbers, pre_mode, current_mode, clear_in, prior_fields=None):
    """Construct fresh fields; prior fields are reset and cannot supply N/count."""
    pre = integer(pre_mode, 0, 0xFFFFFFFF, "pre_mode") & 255
    cur = integer(current_mode, 0, 0xFFFFFFFF, "current_mode") & 255
    integer(clear_in, 0, 0xFFFFFFFF, "clear_in")
    tables.require(pre in MODES and cur in MODES, "unsupported mode low byte")
    if prior_fields is not None:
        tables.require(isinstance(prior_fields, (tuple, list)) and len(prior_fields) == 4,
                       "prior_fields must contain kind, flag, N, count")
        for value in prior_fields:
            integer(value, 0, 0xFFFFFFFF, "prior field")
    kind = flag = n = 0
    steps = []
    clear_out = clear_in
    transition = TRANSITIONS.get((pre, cur))
    if transition is not None:
        kind = 1
        n, flag, offset, tail = transition
        steps = active_vector(numbers, offset, tail)
        if pre in (6, 9) and cur in (1, 3):
            clear_out = 1
    elif clear_in:
        kind = 2
        if cur in CLEAR_VECTORS:
            offset, tail = CLEAR_VECTORS[cur]
            steps = active_vector(numbers, offset, tail)
    final = 2 if cur in (1, 3) and clear_out else cur
    return {"kind": kind, "flag": flag, "N": n, "count": len(steps),
            "active_types": steps, "clear_in": clear_in, "clear_out": clear_out,
            "final_type": final, "pre_low_byte": pre, "current_low_byte": cur}


def lookup_path(typ, flag, length):
    """Reviewed call classifications; no table-column content is reconstructed."""
    tables.require(typ in MODES and flag in (0, 1) and length >= 3,
                   "unsupported lookup classification")
    family = {1: "balanced_stored", 2: "type2", 3: "type3",
              6: "smooth_stored", 8: "smooth_stored", 9: "speed_stored"}[typ]
    if typ in (2, 3) or (typ == 1 and flag):
        phases = [{"operation": "table_lookup", "iterations": length}]
    else:
        last = 2 if typ == 1 else 1
        phases = [{"operation": "first_table_lookup", "iterations": 1},
                  {"operation": "cached_middle_copy", "iterations": length - 1 - last},
                  {"operation": "last_buffer_copy", "iterations": last}]
    return {"family": family, "nominal_iterations": length, "phases": phases,
            "table_lookup_iterations": sum(p["iterations"] for p in phases
                                           if "lookup" in p["operation"]),
            "terminal_counter": 0}


def validate_state(state):
    tables.require(isinstance(state, dict) and STATE_FIELDS <= state.keys()
                   and not state.keys() - STATE_FIELDS - {"prior_fields"},
                   "state requires the documented explicit fields")
    integer(state["lookup_gate"], 0, 1, "lookup_gate bit 0")
    integer(state["initial_counter"], 0, 0xFFFFFFFF, "initial_counter")
    for field in ("well_formed_count", "suppressed"):
        tables.require(type(state[field]) is bool, f"{field} must be boolean")
    tables.require(state["completion"] in ("uninterrupted", "interrupted"),
                   "unsupported completion assumption")
    tables.require(isinstance(state["temperatures"], (list, tuple))
                   and len(state["temperatures"]) == 2, "two temperature readings required")


def predict(decoded, state):
    """Report a synthetic request and conditionally its nominal producer count."""
    validate_state(state)
    numbers = decoded["numbers"]
    request = request_fields(numbers, state["pre_mode"], state["current_mode"],
                             state["clear_in"], state.get("prior_fields"))
    temp = temperature_index(*state["temperatures"], state["temperature_format"],
                             numbers["temperature_index_map"])
    reasons = []
    if state["lookup_gate"] != 1:
        reasons.append("lookup gate bit 0=0 uses an unreviewed alternate sequence")
    if state["initial_counter"] != 0:
        reasons.append("nonzero initial frame counter is outside the reviewed sum rule")
    if not state["well_formed_count"]:
        reasons.append("well-formed count and stable state are not assumed")
    if state["completion"] != "uninterrupted":
        reasons.append("interrupted batch has unknown iteration count")
    if state["suppressed"]:
        reasons.append("suppressed update is outside regular producer sequencing")
    # Request structure is resolved before downstream suppression; execution
    # sequence and all numeric predictions remain unknown for unsupported states.
    sequence = None
    count = None
    if not reasons:
        sequence = []
        types = request["active_types"] + [request["final_type"]]
        for index, typ in enumerate(types):
            length = numbers["lengths_by_type"][str(typ)][temp["bin"]]
            sequence.append({"role": "step" if index < request["count"] else "final",
                             "step_index": index if index < request["count"] else None,
                             "type": typ, "lookup": lookup_path(typ, request["flag"], length)})
        count = sum(p["lookup"]["nominal_iterations"] for p in sequence)
    return {"schema_version": 1, "model": MODEL_ID,
            "provenance": {"decoded_numbers": decoded,
                           "reviewed_control_assertions": CONTROL_ASSERTIONS,
                           "supplied_state_assumptions": state},
            "request": request, "temperature": temp,
            "prediction": {"status": "unknown" if reasons else "nominal",
                           "reasons": reasons, "sequence": sequence,
                           "nominal_producer_iterations": count},
            "semantic_limits": LIMITS}


def cli_integer(value):
    try:
        return int(value, 10)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected a decimal integer") from error


class BoundedParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message[:400]}\n")


def main(argv=None):
    parser = BoundedParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("binary", type=Path, help="exact supported private ELF, read only")
    parser.add_argument("--pre-mode", required=True, type=cli_integer, help="uint32, accepted low byte")
    parser.add_argument("--current-mode", required=True, type=cli_integer, help="uint32, accepted low byte")
    parser.add_argument("--clear-in", required=True, type=cli_integer, help="uint32, zero or nonzero")
    parser.add_argument("--temperature-format", required=True, choices=("raw-byte", "signed-byte"))
    parser.add_argument("--temperatures", required=True, nargs=2, type=cli_integer, metavar=("T1", "T2"))
    parser.add_argument("--lookup-gate", required=True, type=cli_integer, help="gate bit 0 (0 or 1)")
    parser.add_argument("--initial-counter", required=True, type=cli_integer, help="uint32 frame counter")
    parser.add_argument("--well-formed-count", required=True, choices=("yes", "no"),
                        help="assume producer count 0..5 and stable state without concurrent mutation")
    parser.add_argument("--completion", required=True, choices=("uninterrupted", "interrupted"))
    parser.add_argument("--suppressed", required=True, choices=("yes", "no"))
    parser.add_argument("--prior-fields", nargs=4, type=cli_integer,
                        metavar=("KIND", "FLAG", "N", "COUNT"), help="optional old uint32 fields, reset on entry")
    parser.add_argument("--output", type=Path, help="new exclusive JSON file (default: stdout, redirect outside Git)")
    args = parser.parse_args(argv)
    state = {key: getattr(args, key) for key in STATE_FIELDS}
    state["well_formed_count"] = args.well_formed_count == "yes"
    state["suppressed"] = args.suppressed == "yes"
    if args.prior_fields is not None:
        state["prior_fields"] = args.prior_fields
    try:
        result = predict(load_numbers(args.binary), state)
        tables.write_output(args.output, result)
    except (tables.AuditError, OSError, ValueError) as error:
        # Keep error text bounded, including supplied filesystem paths.
        print(f"a9_composer_requests: {str(error)[:400]}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
