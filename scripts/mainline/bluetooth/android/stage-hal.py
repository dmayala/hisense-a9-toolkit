#!/usr/bin/env python3
"""Offline, fail-closed staging of a separately built Linux HCI-user HAL.

Manifest attestation is not a binary equivalence proof. See README.md for the
required source review/build evidence. Never accepts the Cuttlefish UART HAL.
"""
import argparse
import hashlib
import json
import pathlib
import re
import struct
import sys

HAL = "bin/hw/android.hardware.bluetooth-service.default"
LLNDK = {"libc.so", "libm.so", "libdl.so", "liblog.so", "libbinder_ndk.so",
         "libvndksupport.so", "libnativewindow.so", "libsync.so",
         "libselinux.so"}


def fail(message):
    raise ValueError(message)


def elf_needed(data):
    if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01":
        fail("payload must be little-endian ELF64")
    if struct.unpack_from("<H", data, 18)[0] != 183:
        fail("payload must be AArch64")
    phoff = struct.unpack_from("<Q", data, 32)[0]
    phsize, count = struct.unpack_from("<HH", data, 54)
    if phsize < 56 or count > 1024 or phoff + phsize * count > len(data):
        fail("bad ELF program headers")
    loads, dynamic = [], None
    for i in range(count):
        kind, flags, off, va, pa, size, memsize, align = struct.unpack_from(
            "<IIQQQQQQ", data, phoff + i * phsize)
        if off + size > len(data):
            fail("ELF segment extends outside file")
        if kind == 1:
            loads.append((va, off, size))
        elif kind == 2:
            dynamic = off, size
    if dynamic is None:
        fail("expected dynamic Android ELF")
    strings, needed, string_size = None, [], None
    off, size = dynamic
    for pos in range(off, off + size - 15, 16):
        tag, value = struct.unpack_from("<qQ", data, pos)
        if tag == 0:
            break
        if tag == 1:
            needed.append(value)
        elif tag == 5:
            strings = value
        elif tag == 10:
            string_size = value
    if strings is None or string_size is None:
        fail("missing ELF dynamic string table")
    for va, start, length in loads:
        if va <= strings and strings + string_size <= va + length:
            table = data[start + strings - va:start + strings - va + string_size]
            break
    else:
        fail("ELF string table is not file-backed")
    result = []
    for offset in needed:
        end = table.find(b"\0", offset)
        if offset >= len(table) or end < 0:
            fail("invalid ELF dependency string")
        name = table[offset:end].decode("ascii")
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+\.so", name):
            fail("invalid ELF dependency name")
        result.append(name)
    return result


def inspect(bundle, stage):
    manifest_path = bundle / "manifest.json"
    if manifest_path.is_symlink():
        fail("manifest cannot be a symlink")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != 1 or manifest.get("transport") != "linux-hci-user":
        fail("bundle must attest schema 1 linux-hci-user transport")
    if manifest.get("android_release") != "16":
        fail("bundle must target Android 16 vendor APIs")
    evidence = manifest.get("source_build", {})
    for key in ("hardware_interfaces_revision", "build_command", "source_review"):
        if not isinstance(evidence.get(key), str) or not evidence[key].strip():
            fail("missing source build attestation: " + key)
    if evidence.get("transport_failure_returns") is not True:
        fail("source review must attest early return after transport failure")
    if evidence.get("uart_fallback_disabled") is not True:
        fail("source review must attest UART fallback disabled")
    entries = manifest.get("files", {})
    if not isinstance(entries, dict) or HAL not in entries:
        fail("bundle lacks the real AOSP default service")
    contents = {}
    for relative, digest in entries.items():
        if relative != HAL and not re.fullmatch(r"lib64/[A-Za-z0-9_.+-]+\.so", relative):
            fail("bundle path is outside the HAL/library allowlist")
        source = bundle / relative
        if any(p.is_symlink() for p in [source, *source.parents] if p != bundle.parent):
            fail("symlinks are forbidden in bundle paths")
        if not source.is_file() or not re.fullmatch(r"[0-9a-f]{64}", str(digest)):
            fail("missing file or invalid SHA-256")
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            fail("bundle hash mismatch: " + relative)
        existing = stage / relative
        if relative.startswith("lib64/") and existing.exists():
            if existing.is_symlink() or existing.read_bytes() != data:
                fail("bundle would replace a different shared vendor library: " + relative)
        contents[relative] = data
    # This exact cached CF service is a serial transport, despite its AIDL API.
    if hashlib.sha256(contents[HAL]).hexdigest() == "a19f9a85a860155254a3bc265fb51d1e6518aba11b37e2e7e73d0d2e1c25dac4":
        fail("Cuttlefish serial HAL cannot own a kernel HCI controller")
    visited = set()
    pending = list(contents)
    while pending:
        relative = pending.pop()
        if relative in visited:
            continue
        visited.add(relative)
        data = contents.get(relative)
        if data is None:
            source = stage / relative
            if not source.is_file() or source.is_symlink():
                fail("missing vendor dependency: " + relative)
            data = source.read_bytes()
        for name in elf_needed(data):
            if name not in LLNDK:
                dep = "lib64/" + name
                if dep not in visited:
                    pending.append(dep)
    return manifest, contents


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=pathlib.Path, required=True)
    parser.add_argument("--vendor-stage", type=pathlib.Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    bundle = args.bundle.resolve()
    stage = args.vendor_stage.resolve()
    if "a9-assets" in stage.parts or stage == pathlib.Path("/"):
        fail("unsafe vendor staging directory")
    manifest, contents = inspect(bundle, stage)
    # Validate the complete closure before making any change.
    if not args.dry_run:
        for relative, data in contents.items():
            target = stage / relative
            if target.is_symlink() or any(p.is_symlink() for p in target.parents):
                fail("unsafe symlink in vendor staging path")
        for relative, data in contents.items():
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o755 if relative == HAL else 0o644)
    print("BT HAL bundle verified: %d files; Linux transport is a reviewed source-build attestation" % len(contents))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, UnicodeError, struct.error, json.JSONDecodeError) as error:
        print("stage-hal: " + str(error), file=sys.stderr)
        sys.exit(2)
