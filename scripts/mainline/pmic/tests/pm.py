#!/usr/bin/env python3
"""Offline regression of actual driver PM callbacks, poll and property readers.

Only the kernel services and bus reads are stubbed. Compile the selected C
functions verbatim, including the canonical policy header; no phone tools.
--driver accepts an older driver to reproduce the pre-fix failure.
"""
import argparse
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile


def definition(text, pattern):
    start = re.search(pattern, text, re.M)
    if start is None:
        raise ValueError(f"missing definition: {pattern}")
    opening = text.index("{", start.start())
    depth = 1
    end = opening + 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start.start():end]


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--driver", type=Path, default=here.parent / "pmi632-a9.c")
    args = ap.parse_args()
    text = args.driver.read_text()
    names = ("a9_is_observe", "a9_off", "a9_health", "a9_poll",
             "a9_battery_get", "a9_usb_get", "a9_suspend", "a9_resume")
    functions = "\n".join(definition(text, r"^static \w+ " + n + r"\(") for n in names)
    properties = sorted(set(re.findall(r"\bPOWER_SUPPLY_\w+", functions)))
    # The host ABI needs distinct symbolic constants, not Linux enum numbers.
    prelude = (here / "pm-stubs.inc").read_text()
    prelude = prelude.replace("/* PROPERTY_ENUM */", "enum power_supply_property {\n" +
                              ",\n".join(properties) + "\n};")
    prelude = prelude.replace("/* DRIVER_STRUCT */",
                              definition(text, r"^struct a9_pmic \{") + ";")
    constants = "\n".join(re.findall(r"^#define (?:CHG_\w+|USB_\w+|OTG_CMD|WD_PET|POLL_MS)\s+.*", text, re.M))
    code = prelude + "\n" + constants + "\n" + functions + "\n" + (here / "pm-cases.inc").read_text()
    # No invocation depends on the cwd or on a shared object directory.
    with tempfile.TemporaryDirectory(prefix="pmi632-pm-") as td:
        src = Path(td) / "pm.c"
        src.write_text(code)
        for mode in ("control", "observe", "telemetry"):
            binary = Path(td) / mode
            subprocess.run(shlex.split(os.environ.get("CC", "cc")) + [
                "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-g",
                "-fsanitize=undefined,address", "-fno-omit-frame-pointer",
                "-I", str(here.parent),
                f"-DCONFIG_CHARGER_PMI632_A9_OBSERVE={int(mode == 'telemetry')}",
                f"-DCONFIG_CHARGER_PMI632_A9_TELEMETRY_ONLY={int(mode == 'telemetry')}",
                f"-DTEST_OBSERVE={int(mode == 'observe')}",
                str(src), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
            print(f"PASS: {mode} PM callbacks/poll/getters", flush=True)


if __name__ == "__main__":
    main()
