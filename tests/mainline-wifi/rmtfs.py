#!/usr/bin/env python3
"""Offline tests of the actual patched A9 rmtfs backend; no QRTR/device opens."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--sources", type=Path, default=Path.home() / "A9-build/mainline/sol-wifi/src")
parser.add_argument("--no-sanitize", action="store_true")
args = parser.parse_args()
here = Path(__file__).resolve().parent
repo = here.parent.parent
cc = os.environ.get("CC") or shutil.which("cc")
if not cc:
    raise SystemExit("Set CC to a local C compiler; this test never downloads one")
qrtr = args.sources / "qrtr"
for name, pin in (("rmtfs", "b30a3eb38f9af283f18dbd3c7755653efc52c094"),
                  ("qrtr", "29e36ae164389580a0f8ea7a7fdb728140ae978d")):
    source = args.sources / name
    actual = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                            check=True, capture_output=True, text=True).stdout.strip()
    if actual != pin:
        raise SystemExit("Wrong source pin for " + name)
    subprocess.run(["git", "-C", str(source), "diff", "--quiet", "HEAD", "--", "."], check=True)
# LeakSanitizer cannot enumerate threads under the managed execution sandbox.
# Address/undefined-behavior checks remain active; no leak-proof claim is made.
os.environ["ASAN_OPTIONS"] = os.environ.get("ASAN_OPTIONS", "") + ":detect_leaks=0:abort_on_error=1"
os.environ["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
with tempfile.TemporaryDirectory(prefix="a9-rmtfs-tests-") as tmp:
    work = Path(tmp)
    patched = work / "rmtfs"
    shutil.copytree(args.sources / "rmtfs", patched, ignore=shutil.ignore_patterns(".git"))
    patch = repo / "scripts/mainline/wifi/patches/rmtfs.patch"
    subprocess.run(["patch", "--batch", "-p1", "-i", str(patch)], cwd=patched, check=True)
    flags = ["-D_GNU_SOURCE", "-DA9_RMTFS_SAFE", "-O1", "-g", "-Wall", "-Wextra",
             "-Wno-sign-compare", "-Wno-unused-parameter", "-Wno-cpp",
             "-I" + str(patched), "-I" + str(qrtr / "include")]
    san = [] if args.no_sanitize else ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
    def build(name, files, extra=()):
        output = work / name
        subprocess.run([cc, *flags, *san, *map(str, files), *extra, "-o", str(output)], check=True)
        return output
    backend = build("backend", [here / "rmtfs-backend.c", patched / "a9-storage.c"], ["-Wl,--wrap=fstat"])
    transport = build("transport", [here / "rmtfs-sharedmem.c"])
    wire = build("wire", [here / "rmtfs-wire.c", patched / "qmi_rmtfs.c", qrtr / "lib/qmi.c", qrtr / "lib/logging.c"])
    faults = build("faults", [here / "rmtfs-fault.c", patched / "qmi_rmtfs.c"] +
                   [qrtr / "lib" / name for name in ("qrtr.c", "qmi.c", "logging.c")])
    diagnostics = build("diagnostics", [here / "rmtfs-diagnostics.c", patched / "qmi_rmtfs.c"] +
                        [qrtr / "lib" / name for name in ("qrtr.c", "qmi.c", "logging.c")])
    multiport = build("multiport", [here / "rmtfs-multiport.c", patched / "a9-storage.c", patched / "qmi_rmtfs.c"] +
                     [qrtr / "lib" / name for name in ("qrtr.c", "qmi.c", "logging.c")])
    admission = build("admission", [here / "rmtfs-admission.c", patched / "a9-storage.c", patched / "qmi_rmtfs.c"] +
                     [qrtr / "lib" / name for name in ("qrtr.c", "qmi.c", "logging.c")],
                     ["-Wl,--wrap=pread,--wrap=pwrite,--wrap=fdatasync", "-pthread"])
    sandbox_admission = build("sandbox-admission", [here / "sandbox-admission.c"],
                              ["-I" + str(repo / "scripts/mainline/wifi"), "-Wl,--wrap=open,--wrap=fstat"])
    for binary in (backend, transport, wire, faults, diagnostics, multiport, admission, sandbox_admission):
        subprocess.run([str(binary)], check=True)
    daemon = build("daemon", [patched / name for name in ("rmtfs.c", "qmi_rmtfs.c", "a9-storage.c", "a9-sharedmem.c", "util.c")] +
                   [qrtr / "lib" / name for name in ("qrtr.c", "qmi.c", "logging.c")])
    # All arguments abort before FD validation or QRTR operations.
    for option in ("-r", "-s", "-P", "-o", "-S", "--help"):
        result = subprocess.run([str(daemon), option], capture_output=True, text=True)
        if result.returncode != 1 or "accepts no options" not in result.stderr:
            raise SystemExit("A9 mode failed to reject " + option)
    symbols = subprocess.run([os.environ.get("NM", "nm"), str(daemon)], capture_output=True, text=True, check=True).stdout
    for name in ("rproc_start", "rproc_stop", "rproc_init", "storage_populate_shadow_buf", "rmtfs_mem_enumerate"):
        if any(line.split()[-1:] == [name] for line in symbols.splitlines()):
            raise SystemExit("Forbidden legacy symbol in A9 daemon: " + name)
    print("rmtfs daemon: PASS (no options, no rproc/fallback symbols; never started)")
