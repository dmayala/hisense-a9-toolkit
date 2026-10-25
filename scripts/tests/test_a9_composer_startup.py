"""Fixed accepted numeric expectations; invented ELF, no vendor code/payload."""

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import a9_composer_startup as model
import a9_composer_tables as tables


# Frozen proof expectations, independent of the implementation's constants.
EXPECTED_COUNTS = [109, 93, 81, 71, 61, 111, 95, 83, 75, 67, 63, 59, 55, 51]
BOOL_FIELDS = ("stable_bin", "no_intervening_writes", "valid_ring", "admitted", "ring_progress")


def state(**changes):
    result = {"temperature_bin": 9, "gate_bit0": 0, "initial_counter": 0,
              "stable_bin": True, "no_intervening_writes": True, "valid_ring": True,
              "admitted": True, "ring_progress": True, "completion": "uninterrupted"}
    result.update(changes)
    return result


def cli_args(binary, **changes):
    args = [str(binary)]
    for name, value in state(**changes).items():
        args.extend(["--" + name.replace("_", "-"),
                     ("yes" if value else "no") if type(value) is bool else str(value)])
    return args


def synthetic_elf():
    # Zero-filled invented data plus ELF layout and numeric length headers only.
    data = bytearray(2049912)
    tables.ELF_HEADER.pack_into(data, 0, *tables.EXPECTED_HEADER)
    for index, load in enumerate(tables.EXPECTED_LOADS):
        tables.PROGRAM_HEADER.pack_into(data, 64 + index * 56, *load)
    tables.SECTION_HEADER.pack_into(data, 2048120 + 64, *tables.RODATA)
    tables.SECTION_HEADER.pack_into(data, 2048120 + 26 * 64,
                                   0, 3, 0, 0, 2022972, 278, 0, 0, 1, 0)
    data[2023221:2023229] = b".rodata\0"
    for index, length in enumerate(EXPECTED_COUNTS):
        data[0x2B103 + 120 * index] = length
    return data


def decoded_fixture():
    return {"headers": model.decode_headers(synthetic_elf()),
            "validation": "invented test metadata; not a known vendor binary"}


class CountTests(unittest.TestCase):
    def test_fourteen_fixed_counts_and_selected_metadata(self):
        decoded = decoded_fixture()
        self.assertEqual(decoded["headers"]["table_file_offset"], 0x2B103)
        self.assertEqual(decoded["headers"]["bin_stride_bytes"], 120)
        self.assertEqual(decoded["headers"]["end_exclusive"], 0x2B793)
        self.assertEqual(decoded["headers"]["bin_count"], 14)
        for index, count in enumerate(EXPECTED_COUNTS):
            with self.subTest(bin=index):
                supplied = state(temperature_bin=index)
                report = model.predict(decoded, supplied)
                prediction = report["prediction"]
                self.assertEqual(prediction["status"], "nominal")
                self.assertEqual(prediction["nominal_produced_software_frames"], count)
                self.assertEqual(prediction["count_unit"], "PRODUCEDSOFTWAREFRAMES")
                self.assertEqual(prediction["conditional_terminal_state"], {"gate_byte": 1, "counter": 0})
                self.assertEqual(prediction["reasons"], [])
                self.assertEqual(report["selected_bin"], {"bin": index, "length_header": count,
                                                       "header_file_offset": 0x2B103 + 120 * index})
                self.assertEqual(report["provenance"]["supplied_synthetic_assumptions"], supplied)
                self.assertEqual(report["schema_version"], 1)
                self.assertEqual(report["model"], "a9-composer-startup-v1")
        assertions = report["provenance"]["reviewed_control_assertions"]
        self.assertEqual(assertions["report_sha256"],
                         "e5d9fe6caf43c3a6dbdd630e9844690abda40ba79800cdc8c648e03a91a19c3b")
        self.assertEqual(assertions["independent_review_sha256"],
                         "33108aa446a7a62daaf6a70ba54f575868df98702e49dd4f9c672d9e764175b9")

    def test_each_unsupported_assumption_and_combination_is_unknown(self):
        cases = [{"gate_bit0": 1}, {"initial_counter": 1}, {"initial_counter": 0xFFFFFFFF},
                 {"completion": "interrupted"}] + [{field: False} for field in BOOL_FIELDS]
        cases.append({"gate_bit0": 1, "initial_counter": 67, "completion": "interrupted",
                      **{field: False for field in BOOL_FIELDS}})
        for changes in cases:
            with self.subTest(changes=changes):
                prediction = model.predict(decoded_fixture(), state(**changes))["prediction"]
                self.assertEqual(prediction["status"], "unknown")
                self.assertIsNone(prediction["nominal_produced_software_frames"])
                self.assertIsNone(prediction["conditional_terminal_state"])
                self.assertTrue(prediction["reasons"])
        self.assertEqual(len(prediction["reasons"]), 8)

    def test_missing_extra_and_invalid_domains_are_errors(self):
        for field in state():
            incomplete = state()
            del incomplete[field]
            with self.subTest(missing=field), self.assertRaises(tables.AuditError):
                model.predict(decoded_fixture(), incomplete)
        cases = [("temperature_bin", v) for v in (-1, 14, True, 1.5, "9")]
        cases += [("gate_bit0", v) for v in (-1, 2, True, 0.0)]
        cases += [("initial_counter", v) for v in (-1, 0x100000000, False, 0.0)]
        cases += [(field, v) for field in BOOL_FIELDS for v in (1, "yes", None)]
        cases += [("completion", "maybe"), ("extra", True)]
        for field, value in cases:
            with self.subTest(field=field, value=value), self.assertRaises(tables.AuditError):
                model.predict(decoded_fixture(), state(**{field: value}))
        with self.assertRaises(tables.AuditError):
            model.validate_state(None)

    def test_corruption_at_each_header_and_truncated_extent(self):
        for index in range(14):
            for invalid in (0, 1, 120, 255):
                data = synthetic_elf()
                data[0x2B103 + 120 * index] = invalid
                with self.subTest(bin=index, invalid=invalid), self.assertRaises(tables.AuditError):
                    model.decode_headers(data)
        for size in (0, 0x2B103, 0x2B103 + 13 * 120, 0x2B793 - 1):
            with self.subTest(size=size), self.assertRaises(tables.AuditError):
                model.decode_headers(synthetic_elf()[:size])


class FileAndCliTests(unittest.TestCase):
    def test_exact_identity_and_elf_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "input"
            invented = synthetic_elf()
            binary.write_bytes(invented)
            with self.assertRaisesRegex(tables.AuditError, "SHA256"):
                model.load_headers(binary)
            with patch.object(model.hashlib, "sha256") as sha:
                sha.return_value.hexdigest.return_value = tables.SUPPORTED_SHA256[:8] + "0" * 56
                with self.assertRaisesRegex(tables.AuditError, "SHA256"):
                    model.load_headers(binary)
            with patch.object(tables, "SUPPORTED_SHA256", hashlib.sha256(invented).hexdigest()):
                decoded = model.load_headers(binary)
                self.assertEqual(decoded["binary_size_bytes"], 2049912)
                self.assertEqual(decoded["elf"]["machine"], "AArch64")
            invented[18] = 62
            binary.write_bytes(invented)
            with patch.object(tables, "SUPPORTED_SHA256", hashlib.sha256(invented).hexdigest()):
                with self.assertRaisesRegex(tables.AuditError, "ELF"):
                    model.load_headers(binary)
            binary.write_bytes(b"invented short input")
            with self.assertRaisesRegex(tables.AuditError, "size"):
                model.load_headers(binary)

    def test_cli_stdout_unknown_and_exclusive_private_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "input"
            invented = synthetic_elf()
            binary.write_bytes(invented)
            output = root / "new.json"
            with patch.object(tables, "SUPPORTED_SHA256", hashlib.sha256(invented).hexdigest()):
                self.assertEqual(model.main(cli_args(binary) + ["--output", str(output)]), 0)
                report = json.loads(output.read_text())
                self.assertEqual(report["prediction"]["nominal_produced_software_frames"], 67)
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                hardlink = root / "hardlink"
                os.link(binary, hardlink)
                symlink = root / "symlink"
                symlink.symlink_to(binary)
                dangling = root / "dangling"
                dangling.symlink_to(root / "absent")
                fifo = root / "fifo"
                os.mkfifo(fifo)
                for target in (binary, hardlink, symlink, dangling, output, fifo, root):
                    with self.subTest(target=target), contextlib.redirect_stderr(io.StringIO()), \
                            contextlib.redirect_stdout(io.StringIO()) as captured:
                        self.assertEqual(model.main(cli_args(binary) + ["--output", str(target)]), 2)
                        self.assertFalse(captured.getvalue())
                self.assertEqual(binary.read_bytes(), invented)
                self.assertEqual(json.loads(output.read_text()), report)
                with contextlib.redirect_stdout(io.StringIO()) as captured:
                    self.assertEqual(model.main(cli_args(binary, valid_ring=False)), 0)
                self.assertIsNone(json.loads(captured.getvalue())["prediction"]["nominal_produced_software_frames"])
                checkout = root / "checkout"
                checkout.mkdir()
                (checkout / ".git").write_text("gitdir: invented-worktree")
                forbidden = checkout / "report.json"
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(model.main(cli_args(binary) + ["--output", str(forbidden)]), 2)
                self.assertFalse(forbidden.exists())
                loop = root / "loop"
                loop.symlink_to("loop")
                with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()) as captured:
                    self.assertEqual(model.main(cli_args(binary) + ["--output", str(loop / "report.json")]), 2)
                self.assertFalse(captured.getvalue())
                for special_input in (symlink, fifo, root):
                    with self.subTest(input=special_input), contextlib.redirect_stderr(io.StringIO()), \
                            contextlib.redirect_stdout(io.StringIO()) as captured:
                        self.assertEqual(model.main(cli_args(special_input)), 2)
                        self.assertFalse(captured.getvalue())

    def test_legacy_output_resolution_loop_is_a_bounded_error(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "absent.json"
            with patch.object(model.Path, "resolve", side_effect=RuntimeError("loop" * 5000)), \
                    contextlib.redirect_stderr(io.StringIO()) as errors, \
                    contextlib.redirect_stdout(io.StringIO()) as captured:
                self.assertEqual(model.main(cli_args(Path(directory) / "input")
                                            + ["--output", str(output)]), 2)
            self.assertFalse(captured.getvalue())
            self.assertFalse(output.exists())
            self.assertIn("cannot resolve output parent", errors.getvalue())
            self.assertLess(len(errors.getvalue()), 500)
            self.assertNotIn("Traceback", errors.getvalue())

    def test_invalid_cli_no_report_and_bounded_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "input"
            binary.write_bytes(synthetic_elf())
            output = Path(directory) / "absent.json"
            for changes in ({}, {"temperature_bin": 14}, {"gate_bit0": 2},
                            {"initial_counter": -1}, {"initial_counter": 0x100000000}):
                with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()) as captured:
                    self.assertEqual(model.main(cli_args(binary, **changes) + ["--output", str(output)]), 2)
                self.assertFalse(output.exists())
                self.assertFalse(captured.getvalue())
            command = [sys.executable, str(Path(model.__file__))]
            for field in state():
                args = cli_args(binary)
                index = args.index("--" + field.replace("_", "-"))
                del args[index:index + 2]
                with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()) as captured:
                    with self.assertRaises(SystemExit) as error:
                        model.main(args + ["--output", str(output)])
                    self.assertEqual(error.exception.code, 2)
                self.assertFalse(output.exists())
                self.assertFalse(captured.getvalue())
            for bad in (["--completion", "x" * 20000], ["--gate-bit0", "x" * 20000],
                        [str(binary)], ["--junk", "x" * 20000]):
                result = subprocess.run(command + bad, capture_output=True, timeout=5, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(result.stdout)
                self.assertLess(len(result.stderr), 3000)
                self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
