"""Independent numeric expectations and invented ELF metadata; no vendor code."""

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import a9_composer_requests as model
import a9_composer_tables as tables


# Fixed metadata reviewed independently of this model. Bytes outside metadata
# are invented zeroes; no vendor instructions or table payloads are fixtures.
LENGTHS = {
    "1": [54, 46, 40, 35, 30, 26, 22, 19, 17, 15, 14, 13, 12, 12],
    "2": [104, 88, 76, 66, 57, 48, 40, 35, 30, 30, 30, 30, 30, 30],
    "3": [104, 88, 76, 66, 57, 48, 40, 35, 30, 30, 30, 30, 30, 30],
    "6": [28, 25, 23, 20, 17, 14, 12, 10, 8, 8, 8, 8, 8, 8],
    "8": [28, 25, 23, 20, 17, 14, 12, 10, 8, 8, 8, 8, 8, 8],
    "9": [28, 25, 23, 20, 17, 14, 12, 10, 7, 7, 7, 7, 7, 7],
}
VECTOR_FIXTURES = {
    0x2A850: [5, 9, 6, 6], 0x2A8D0: [5, 3, 9, 9],
    0x2A8E0: [5, 9, 9, 9], 0x2A920: [5, 6, 6, 6],
    0x2A950: [5, 6, 9, 9], 0x2A970: [5, 1, 9, 9],
    0x2A980: [4, 6, 6, 6], 0x2A9F0: [5, 1, 6, 6],
    0x2AA20: [5, 3, 6, 6], 0x2AA40: [4, 9, 9, 9],
    0x2AA60: [1, 6], 0x2AA88: [2, 1],
    0x2AAC8: [1, 9], 0x2AAD0: [1, 3],
}
TEMPERATURE_MAP = [i // 3 if i < 33 else 11 if i < 38 else 12 if i < 44 else 13
                   for i in range(51)]
MODES = (1, 2, 3, 6, 8, 9)
# Independent review matrix: cell = kind/flag/N/active type digits. Rows pre,
# columns current1,2,3,6,8,9. '-' means no active steps, not stale fields.
CLEAR0 = (
    "0/0/0/- 1/0/9/11 1/0/0/- 1/0/6/16666 0/0/0/- 1/0/12/19999",
    "1/1/0/- 0/0/0/- 0/0/0/- 1/0/1/36666 0/0/0/- 1/0/11/39999",
    "1/1/0/- 0/0/0/- 0/0/0/- 1/0/1/36666 0/0/0/- 1/0/11/39999",
    "1/0/2/6 1/0/7/6666 1/0/2/6 0/0/0/- 0/0/0/- 1/0/4/69999",
    "0/0/0/- 0/0/0/- 0/0/0/- 0/0/0/- 0/0/0/- 0/0/0/-",
    "1/0/3/9 1/0/8/9999 1/0/3/9 1/0/5/96666 0/0/0/- 0/0/0/-",
)
CLEAR1 = (
    "2/0/0/- 1/0/9/11 1/0/0/- 1/0/6/16666 2/0/0/- 1/0/12/19999",
    "1/1/0/- 2/0/0/- 2/0/0/3 1/0/1/36666 2/0/0/- 1/0/11/39999",
    "1/1/0/- 2/0/0/- 2/0/0/3 1/0/1/36666 2/0/0/- 1/0/11/39999",
    "1/0/2/6 1/0/7/6666 1/0/2/6 2/0/0/66666 2/0/0/- 1/0/4/69999",
    "2/0/0/- 2/0/0/- 2/0/0/3 2/0/0/66666 2/0/0/- 2/0/0/99999",
    "1/0/3/9 1/0/8/9999 1/0/3/9 1/0/5/96666 2/0/0/- 2/0/0/99999",
)


def numeric_fixture():
    return {"numbers": {"lengths_by_type": LENGTHS,
                        "temperature_index_map": TEMPERATURE_MAP,
                        "request_vectors": {str(k): v for k, v in VECTOR_FIXTURES.items()}},
            "validation": "synthetic unit-test metadata, not a known vendor ELF"}


def state(**changes):
    result = {"pre_mode": 1, "current_mode": 6, "clear_in": 0,
              "temperature_format": "raw-byte", "temperatures": [28, 28],
              "lookup_gate": 1, "initial_counter": 0, "well_formed_count": True,
              "completion": "uninterrupted", "suppressed": False}
    result.update(changes)
    return result


def cli_args(binary, **changes):
    values = state(**changes)
    args = [str(binary)]
    for name, value in values.items():
        args.append("--" + name.replace("_", "-"))
        if type(value) is bool:
            args.append("yes" if value else "no")
        elif isinstance(value, list):
            args.extend(str(v) for v in value)
        else:
            args.append(str(value))
    return args


def synthetic_elf():
    data = bytearray(2049912)
    tables.ELF_HEADER.pack_into(data, 0, *tables.EXPECTED_HEADER)
    for index, load in enumerate(tables.EXPECTED_LOADS):
        tables.PROGRAM_HEADER.pack_into(data, 64 + index * 56, *load)
    tables.SECTION_HEADER.pack_into(data, 2048120 + 64, *tables.RODATA)
    tables.SECTION_HEADER.pack_into(data, 2048120 + 26 * 64,
                                   0, 3, 0, 0, 2022972, 278, 0, 0, 1, 0)
    data[2023221:2023229] = b".rodata\0"
    for typ, offset, stride in ((1, 0xE5B2B, 56), (2, 0x8895F, 106),
                                (3, 0x2B793, 106), (6, 0x116E3B, 30), (9, 0x1313DF, 30)):
        for bin_index, length in enumerate(LENGTHS[str(typ)]):
            for slot in range(256):
                data[offset + (bin_index * 257 + slot) * stride] = length
    data[0x2B0D0:0x2B103] = bytes(TEMPERATURE_MAP)
    for offset, values in VECTOR_FIXTURES.items():
        struct.pack_into("<" + "I" * len(values), data, offset, *values)
    return data


class RequestTests(unittest.TestCase):
    def test_all_pairs_and_bins_against_review_matrix(self):
        # 72 reset-aware requests and 1008 bin totals. The expected types and
        # fields come from fixed review matrices, never implementation dispatch.
        temperatures = [0, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 38, 44]
        for clear, matrix in enumerate((CLEAR0, CLEAR1)):
            for row, pre in enumerate(MODES):
                for col, cur in enumerate(MODES):
                    kind, flag, n, digits = matrix[row].split()[col].split("/")
                    expected_steps = [] if digits == "-" else [int(d) for d in digits]
                    forced = pre in (6, 9) and cur in (1, 3)
                    expected_clear = 1 if forced else clear
                    final = 2 if cur in (1, 3) and expected_clear else cur
                    for bin_index, temperature in enumerate(temperatures):
                        with self.subTest(pre=pre, cur=cur, clear=clear, bin=bin_index):
                            result = model.predict(numeric_fixture(), state(
                                pre_mode=pre, current_mode=cur, clear_in=clear,
                                prior_fields=[0xFFFFFFFF] * 4,
                                temperatures=[temperature] * 2))
                            request = result["request"]
                            self.assertEqual([request[k] for k in ("kind", "flag", "N", "count")],
                                             [int(kind), int(flag), int(n), len(expected_steps)])
                            self.assertEqual(request["active_types"], expected_steps)
                            self.assertEqual(request["clear_out"], expected_clear)
                            self.assertEqual(request["final_type"], final)
                            sequence = result["prediction"]["sequence"]
                            self.assertEqual([p["type"] for p in sequence], expected_steps + [final])
                            self.assertEqual([p["role"] for p in sequence],
                                             ["step"] * len(expected_steps) + ["final"])
                            self.assertEqual([p["step_index"] for p in sequence],
                                             list(range(len(expected_steps))) + [None])
                            expected = sum(LENGTHS[str(t)][bin_index] for t in expected_steps + [final])
                            self.assertEqual(result["temperature"]["bin"], bin_index)
                            self.assertEqual(result["prediction"]["nominal_producer_iterations"], expected)

    def test_observed_counterexamples_and_final_ordering(self):
        for pre, cur, clear, temp, total in ((1, 6, 0, 28, 55), (1, 6, 0, 31, 54),
                                             (6, 1, 0, 25, 38), (6, 6, 1, 28, 48),
                                             (1, 1, 1, 28, 30)):
            result = model.predict(numeric_fixture(), state(pre_mode=pre, current_mode=cur,
                                   clear_in=clear, temperatures=[temp, temp]))
            self.assertEqual(result["prediction"]["nominal_producer_iterations"], total)
            self.assertEqual(result["prediction"]["sequence"][-1]["role"], "final")

    def test_upper_mode_bits_and_nonboolean_clear(self):
        base = model.request_fields(numeric_fixture()["numbers"], 1, 6, 7)
        changed = model.request_fields(numeric_fixture()["numbers"], 0x201, 0xFFFFFF06, 7)
        self.assertEqual(base, changed)
        self.assertEqual(base["kind"], 1)
        self.assertEqual(base["active_types"], [1, 6, 6, 6, 6])
        self.assertEqual(base["clear_out"], 7)
        forced = model.request_fields(numeric_fixture()["numbers"], 9, 3, 7)
        self.assertEqual(forced["clear_out"], 1)

    def test_poisoned_fields_never_supply_stale_N_or_count(self):
        for pre, cur in ((2, 1), (3, 1), (1, 3), (8, 6), (6, 6)):
            for poison in ([0, 0, 0, 0], [1, 1, 12, 5], [0xA5A5A5A5] * 4):
                request = model.request_fields(numeric_fixture()["numbers"], pre, cur, 0, poison)
                self.assertEqual(request["N"], 0)
                self.assertEqual(request["count"], 0)
                self.assertEqual(request["active_types"], [])

    def test_all_98_cache_classes_and_counts(self):
        for typ, flag in ((1, 0), (1, 1), (2, 0), (3, 0), (6, 0), (8, 0), (9, 0)):
            for bin_index in range(14):
                length = LENGTHS[str(typ)][bin_index]
                result = model.lookup_path(typ, flag, length)
                with self.subTest(typ=typ, flag=flag, bin=bin_index):
                    if typ in (2, 3) or (typ == 1 and flag):
                        expected = [("table_lookup", length)]
                    elif typ == 1:
                        expected = [("first_table_lookup", 1), ("cached_middle_copy", length - 3),
                                    ("last_buffer_copy", 2)]
                    else:
                        expected = [("first_table_lookup", 1), ("cached_middle_copy", length - 2),
                                    ("last_buffer_copy", 1)]
                    self.assertEqual([(p["operation"], p["iterations"]) for p in result["phases"]], expected)
                    self.assertEqual(sum(p["iterations"] for p in result["phases"]), length)
                    self.assertEqual(result["terminal_counter"], 0)
        speed = model.lookup_path(9, 0, 7)
        self.assertEqual(speed["family"], "speed_stored")
        self.assertEqual(speed["table_lookup_iterations"], 1)
        self.assertEqual(model.lookup_path(8, 0, 8)["family"], "smooth_stored")

    def test_temperature_signed_and_raw_counterexamples(self):
        examples = ((-20, 50, "signed-byte", 16, 5), (128, 0, "raw-byte", 0, 0),
                    (-128, 127, "signed-byte", 16, 5), (127, 127, "signed-byte", 50, 13),
                    (255, 50, "raw-byte", 16, 5), (50, 128, "raw-byte", 33, 11),
                    (0, 2, "raw-byte", 0, 0), (0, 3, "raw-byte", 1, 0))
        for first, second, domain, index, bin_index in examples:
            with self.subTest(first=first, second=second, domain=domain):
                result = model.temperature_index(first, second, domain, TEMPERATURE_MAP)
                self.assertEqual((result["index"], result["bin"]), (index, bin_index))
        # Exhaust all 65536 byte pairs against a signed-domain reference.
        # The independent oracle uses signed byte decoding and term replacement.
        for first in range(256):
            for second in range(256):
                signed = [v if v < 128 else v - 256 for v in (first, second)]
                terms = [max(0, min(50, v)) for v in signed]
                expected = (terms[0] * 2 + terms[1]) // 3
                self.assertEqual(model.temperature_index(first, second, "raw-byte", TEMPERATURE_MAP)["index"], expected)
                self.assertEqual(model.temperature_index(*signed, "signed-byte", TEMPERATURE_MAP)["index"], expected)

    def test_every_temperature_endpoint_and_invalid_domain(self):
        for temp in range(51):
            self.assertEqual(model.temperature_index(temp, temp, "raw-byte", TEMPERATURE_MAP)["bin"],
                             TEMPERATURE_MAP[temp])
        for first, second, domain in ((-1, 0, "raw-byte"), (256, 0, "raw-byte"),
                                     (-129, 0, "signed-byte"), (128, 0, "signed-byte"),
                                     (1.5, 0, "raw-byte"), (True, 0, "raw-byte"), (0, 0, "auto")):
            with self.assertRaises(tables.AuditError):
                model.temperature_index(first, second, domain, TEMPERATURE_MAP)

    def test_unsupported_preconditions_never_predict_counts_or_execution(self):
        for changes in ({"lookup_gate": 0}, {"initial_counter": 1}, {"well_formed_count": False},
                        {"completion": "interrupted"}, {"suppressed": True},
                        {"lookup_gate": 0, "initial_counter": 42, "suppressed": True}):
            result = model.predict(numeric_fixture(), state(**changes))
            self.assertEqual(result["prediction"]["status"], "unknown")
            self.assertIsNone(result["prediction"]["nominal_producer_iterations"])
            self.assertIsNone(result["prediction"]["sequence"])
            self.assertTrue(result["prediction"]["reasons"])
            self.assertEqual(result["request"]["active_types"], [1, 6, 6, 6, 6])
        for field in model.STATE_FIELDS:
            incomplete = state()
            del incomplete[field]
            with self.assertRaises(tables.AuditError):
                model.predict(numeric_fixture(), incomplete)

    def test_invalid_states_are_refused(self):
        for changes in ({"pre_mode": 0}, {"current_mode": 7}, {"current_mode": -1},
                        {"clear_in": -1}, {"lookup_gate": 2}, {"initial_counter": -1},
                        {"suppressed": "no"}, {"well_formed_count": 1},
                        {"completion": "maybe"}, {"temperatures": [0]},
                        {"prior_fields": [1, 2, 3]}, {"prior_fields": [0, -1, 0, 0]}):
            with self.subTest(changes=changes), self.assertRaises(tables.AuditError):
                model.predict(numeric_fixture(), state(**changes))

    def test_provenance_is_separated_and_supplied_state_preserved(self):
        supplied = state(prior_fields=[99, 98, 97, 96])
        result = model.predict(numeric_fixture(), supplied)
        self.assertEqual(set(result["provenance"]),
                         {"decoded_numbers", "reviewed_control_assertions", "supplied_state_assumptions"})
        self.assertEqual(result["provenance"]["supplied_state_assumptions"], supplied)
        self.assertEqual(supplied["prior_fields"], [99, 98, 97, 96])
        self.assertEqual(result["provenance"]["reviewed_control_assertions"]["review_report_sha256"],
                         "fe1eb05737d04f9d4d24e6f80896aee0e2b0d167c192c3a33f2db7e90d9e3030")


class FileAndCliTests(unittest.TestCase):
    def test_numeric_decoder_regenerates_fixed_metadata_and_refuses_bad_regions(self):
        data = synthetic_elf()
        numbers = model.decode_numbers(data)
        self.assertEqual(numbers["lengths_by_type"], LENGTHS)
        self.assertEqual(numbers["request_vectors"], numeric_fixture()["numbers"]["request_vectors"])
        self.assertEqual(numbers["temperature_index_map"], TEMPERATURE_MAP)
        for offset in (0xE5B2B, 0xE5B2B + (13 * 257 + 255) * 56, 0x2B102):
            corrupted = bytearray(data)
            corrupted[offset] = 255
            with self.assertRaises(tables.AuditError):
                model.decode_numbers(corrupted)
        with self.assertRaises(tables.AuditError):
            model.decode_numbers(data[:0x2B0D0])

    def test_exact_hash_unknown_binary_and_truncated_input(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "binary"
            binary.write_bytes(synthetic_elf())
            with self.assertRaisesRegex(tables.AuditError, "SHA256"):
                model.load_numbers(binary)
            # A 32-bit prefix collision is insufficient: the comparison checks
            # all 64 hex digits, independently of the fixture's actual digest.
            digest = tables.SUPPORTED_SHA256[:8] + "0" * 56
            with patch.object(model.hashlib, "sha256") as sha:
                sha.return_value.hexdigest.return_value = digest
                with self.assertRaisesRegex(tables.AuditError, "SHA256"):
                    model.load_numbers(binary)
            binary.write_bytes(b"truncated")
            with self.assertRaisesRegex(tables.AuditError, "size"):
                model.load_numbers(binary)

    def test_cli_success_and_exclusive_output_with_synthetic_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "input"
            invented = synthetic_elf()
            binary.write_bytes(invented)
            output = root / "new.json"
            # Test-only identity injection exercises real file/ELF/CLI handling;
            # production cannot bypass the fixed expected binary identity.
            with patch.object(tables, "SUPPORTED_SHA256", hashlib.sha256(invented).hexdigest()):
                self.assertEqual(model.main(cli_args(binary) + ["--output", str(output)]), 0)
                report = json.loads(output.read_text())
                self.assertEqual(report["prediction"]["nominal_producer_iterations"], 55)
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                hardlink = root / "hardlink"
                os.link(binary, hardlink)
                symlink = root / "symlink"
                symlink.symlink_to(binary)
                dangling = root / "dangling"
                dangling.symlink_to(root / "missing")
                for target in (binary, hardlink, symlink, dangling, output):
                    with self.subTest(target=target), contextlib.redirect_stderr(io.StringIO()):
                        self.assertEqual(model.main(cli_args(binary) + ["--output", str(target)]), 2)
                self.assertEqual(binary.read_bytes(), invented)
                self.assertEqual(json.loads(output.read_text()), report)
                with contextlib.redirect_stdout(io.StringIO()) as captured:
                    self.assertEqual(model.main(cli_args(binary, lookup_gate=0)), 0)
                self.assertIsNone(json.loads(captured.getvalue())["prediction"]["nominal_producer_iterations"])

    def test_cli_rejections_do_not_create_output_and_errors_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "input"
            binary.write_bytes(synthetic_elf())
            output = root / "absent.json"
            with contextlib.redirect_stderr(io.StringIO()) as captured:
                self.assertEqual(model.main(cli_args(binary) + ["--output", str(output)]), 2)
            self.assertIn("SHA256", captured.getvalue())
            self.assertFalse(output.exists())
            command = [sys.executable, str(Path(model.__file__))]
            for bad in (["--temperature-format", "x" * 20000],
                        ["--pre-mode", "x" * 20000], [str(binary)], ["--junk", "x" * 20000]):
                result = subprocess.run(command + bad, capture_output=True, timeout=5, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(result.stdout)
                self.assertLess(len(result.stderr), 3000)
                self.assertNotIn("Traceback", result.stderr)
            help_result = subprocess.run(command + ["--help"], capture_output=True, timeout=5, text=True)
            self.assertEqual(help_result.returncode, 0)
            self.assertIn("--well-formed-count", help_result.stdout)

    def test_readonly_regular_input_and_elf_validation_are_used(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "input"
            data = synthetic_elf()
            binary.write_bytes(data)
            with patch.object(tables, "SUPPORTED_SHA256", hashlib.sha256(data).hexdigest()):
                self.assertEqual(model.load_numbers(binary)["elf"]["machine"], "AArch64")
            data[18] = 62
            binary.write_bytes(data)
            with patch.object(tables, "SUPPORTED_SHA256", hashlib.sha256(data).hexdigest()):
                with self.assertRaisesRegex(tables.AuditError, "ELF"):
                    model.load_numbers(binary)
            symlink = root / "link"
            symlink.symlink_to(binary)
            with self.assertRaises(OSError):
                model.load_numbers(symlink)
            fifo = root / "fifo"
            os.mkfifo(fifo)
            with self.assertRaises(tables.AuditError):
                model.load_numbers(fifo)


if __name__ == "__main__":
    unittest.main()
