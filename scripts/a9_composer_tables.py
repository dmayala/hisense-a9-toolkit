#!/usr/bin/env python3
"""Host-only stored-table inventory for one exact privately supplied composer."""

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys


SUPPORTED_SHA256 = "8c89557bf47b57b1687b9fbcc3e55bedbee175d20f80d14bc5317fcf893df5ed"
SUPPORTED_SIZE = 2049912
ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
PROGRAM_HEADER = struct.Struct("<IIQQQQQQ")
SECTION_HEADER = struct.Struct("<IIQQQQIIQQ")
EXPECTED_HEADER = (
    b"\x7fELF\x02\x01\x01" + bytes(9),
    3, 183, 1, 1429504, 64, 2048120, 0, 64, 56, 12, 64, 28, 26,
)
EXPECTED_LOADS = (
    (1, 4, 0, 0, 0, 1428636, 1428636, 4096),
    (1, 5, 1429504, 1429504, 1429504, 568720, 568720, 4096),
    (1, 6, 1998848, 1998848, 1998848, 24088, 24088, 4096),
    (1, 6, 2022936, 2027032, 2027032, 36, 2990968, 4096),
)
RODATA = (249, 1, 50, 132784, 132784, 1240148, 0, 0, 16, 0)


class AuditError(ValueError):
    """Input or output does not meet the audit contract."""


def require(condition, message):
    if not condition:
        raise AuditError(message)


def bounded_slice(data, offset, size):
    require(offset >= 0 and size >= 0 and offset + size <= len(data),
            "region outside input bounds")
    return data[offset:offset + size]


def read_input(path):
    # Nonblocking open prevents a FIFO from hanging before fstat rejects it.
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        require(stat.S_ISREG(info.st_mode), "input must be a regular file, not a symlink")
        require(info.st_size == SUPPORTED_SIZE, "unsupported input size")
        data = source.read(SUPPORTED_SIZE + 1)
    require(len(data) == SUPPORTED_SIZE, "input changed or was truncated while reading")
    return data


def validate_elf(data):
    header = ELF_HEADER.unpack(bounded_slice(data, 0, ELF_HEADER.size))
    require(header == EXPECTED_HEADER, "unsupported ELF header/layout")
    programs = [PROGRAM_HEADER.unpack(bounded_slice(data, header[5] + i * header[9], 56))
                for i in range(header[10])]
    for program in programs:
        bounded_slice(data, program[2], program[5])
        require(program[5] <= program[6], "ELF file extent exceeds memory extent")
    require(tuple(p for p in programs if p[0] == 1) == EXPECTED_LOADS,
            "unsupported ELF load segments")
    sections = [SECTION_HEADER.unpack(bounded_slice(data, header[6] + i * header[11], 64))
                for i in range(header[12])]
    strings = sections[header[13]]
    require(strings[1] == 3, "ELF section name table is not STRTAB")
    names = bounded_slice(data, strings[4], strings[5])
    rodata = []
    for section in sections:
        if section[1] != 8:  # SHT_NOBITS has no file bytes.
            bounded_slice(data, section[4], section[5])
        require(section[0] < len(names), "ELF section name outside string table")
        end = names.find(b"\0", section[0])
        require(end != -1, "unterminated ELF section name")
        if names[section[0]:end] == b".rodata":
            rodata.append(section)
    require(rodata == [RODATA], "unsupported ELF rodata layout")
    return {"class": "ELF64", "byte_order": "little", "machine": "AArch64",
            "type": "ET_DYN", "rodata_file_offset": RODATA[4],
            "rodata_virtual_address": RODATA[3], "rodata_size": RODATA[5]}


@dataclass(frozen=True)
class TableSpec:
    name: str
    offset: int
    stride: int
    lengths: tuple
    extra_slot_has_length: bool = False

    @property
    def end(self):
        return self.offset + len(self.lengths) * 257 * self.stride


# Canonical length-byte addresses cross-checked with the independent researcher
# on 2026-10-12. These are +1 from the superseded [zero][length] probe view.
LONG_LENGTHS = (104, 88, 76, 66, 57, 48, 40, 35, 30, 30, 30, 30, 30, 30)
TABLES = (
    TableSpec("type3", 0x2B793, 106, LONG_LENGTHS, True),
    TableSpec("type2", 0x8895F, 106, LONG_LENGTHS),
    TableSpec("balanced_stored", 0xE5B2B, 56, (54, 46, 40, 35, 30, 26, 22, 19, 17, 15, 14, 13, 12, 12)),
    TableSpec("smooth_stored", 0x116E3B, 30, (28, 25, 23, 20, 17, 14, 12, 10, 8, 8, 8, 8, 8, 8)),
    TableSpec("speed_stored", 0x1313DF, 30, (28, 25, 23, 20, 17, 14, 12, 10, 7, 7, 7, 7, 7, 7)),
)
TEMPERATURE_OFFSET = 0x2B0D0
TEMPERATURE_RANGES = tuple((i * 3, i * 3 + 2) for i in range(11)) + ((33, 37), (38, 43), (44, 50))


def inventory_family(data, spec):
    require(spec.stride >= 2 and all(0 < n <= spec.stride - 2 for n in spec.lengths),
            "invalid table specification")
    bounded_slice(data, spec.offset, spec.end - spec.offset)
    bins = []
    for bin_index, expected_length in enumerate(spec.lengths):
        headers = Counter()
        alphabet = Counter()
        active = 0
        padding_bytes = 0
        extra = None
        for slot in range(257):
            start = spec.offset + (bin_index * 257 + slot) * spec.stride
            row = bounded_slice(data, start, spec.stride)
            length = row[0]
            expected = expected_length if slot < 256 or spec.extra_slot_has_length else 0
            context = f"{spec.name} bin {bin_index} slot {slot}"
            require(length == expected, context + ": unexpected length header")
            payload = row[1:1 + length]
            padding = row[1 + length:]
            require(all(code in (0, 1, 2) for code in payload), context + ": invalid payload code")
            require(not any(padding), context + ": nonzero padding")
            nonzero = sum(code != 0 for code in payload)
            active += int(nonzero > 0)
            headers[length] += 1
            alphabet.update(payload)
            padding_bytes += len(padding)
            if slot == 256:
                extra = {"length_header": length, "nonzero_payload_bytes": nonzero}
        bins.append({"index": bin_index, "file_offset": spec.offset + bin_index * 257 * spec.stride,
                     "length_header_counts": {str(k): v for k, v in sorted(headers.items())},
                     "declared_payload_code_counts": {str(k): alphabet[k] for k in (0, 1, 2)},
                     "slots_with_nonzero_payload": active, "zero_padding_bytes": padding_bytes,
                     "slot_256": extra})
    return {"id": spec.name, "file_offset": spec.offset, "end_exclusive": spec.end,
            "bin_count": len(spec.lengths), "slots_per_bin": 257,
            "slot_stride": spec.stride, "bin_stride": 257 * spec.stride, "bins": bins}


def inventory_temperature(data):
    mapping = bounded_slice(data, TEMPERATURE_OFFSET, 51)
    ranges = []
    for bin_index, (low, high) in enumerate(TEMPERATURE_RANGES):
        require(all(value == bin_index for value in mapping[low:high + 1]),
                "unexpected stored temperature index mapping")
        ranges.append({"bin": bin_index, "input_index_min": low, "input_index_max": high})
    return {"file_offset": TEMPERATURE_OFFSET, "entry_count": 51,
            "entry_width_bytes": 1, "bin_input_index_ranges": ranges,
            "semantic_status": "stored index mapping; runtime temperature weighting is outside this audit"}


def audit(data):
    require(len(data) == SUPPORTED_SIZE, "unsupported input size")
    digest = hashlib.sha256(data).hexdigest()
    require(digest == SUPPORTED_SHA256, "unsupported or corrupt input SHA256")
    elf = validate_elf(data)
    for index, spec in enumerate(TABLES):
        require(RODATA[4] <= spec.offset and spec.end <= RODATA[4] + RODATA[5],
                "table outside rodata")
        if index:
            require(TABLES[index - 1].end == spec.offset, "table regions are not contiguous")
    return {
        "schema_version": 1,
        "provenance": {"binary_sha256": digest, "binary_size_bytes": len(data),
                       "specification": "a9-composer-tables-v1, cross-checked 2026-10-12",
                       "evidence": "STATIC-RE-COMPOSER-20261012.md UPDATE 3 byte-boundary correction; independent layout review"},
        "elf": elf,
        "record_layout": {"header_width_bytes": 1, "header_type": "unsigned length byte",
                          "declared_payload_offset": 1, "payload_alphabet": [0, 1, 2],
                          "padding": "all bytes after declared payload through slot end are zero"},
        "semantic_limits": [
            "Family identifiers describe stored regions, not the entire behavior of a display mode.",
            "Slot 256 is an inventoried slot, not generic padding; caller selection is outside this audit.",
            "Transition-axis orientation, drive polarity, and type9 runtime path selection are unproven here.",
            "Length headers and nonzero code counts do not establish optical quality, DC balance, safe drive constants, or measured device FPS.",
        ],
        "temperature_index_map": inventory_temperature(data),
        "families": [inventory_family(data, spec) for spec in TABLES],
    }


def write_output(path, result):
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
    if path is None:
        sys.stdout.write(encoded)
        return
    # O_EXCL refuses existing regular files, symlinks, and the input itself.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        output.write(encoded)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path, help="exact supported private composer ELF (never modified)")
    parser.add_argument("--output", type=Path, help="new JSON file; existing paths are refused (default: stdout)")
    args = parser.parse_args(argv)
    try:
        result = audit(read_input(args.binary))
        write_output(args.output, result)
    except (AuditError, OSError) as error:
        print(f"a9_composer_tables: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
