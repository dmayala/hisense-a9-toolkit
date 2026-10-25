#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Compile only, with private output. Never accesses a device or creates a boot image."""
import argparse
from pathlib import Path
import shutil
import subprocess


def main():
    here = Path(__file__).resolve().parent
    base = Path.home() / "A9-build/mainline"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=base / "linux-7.2.10")
    parser.add_argument("--reference", type=Path, default=base / "build-7.2.10-android")
    parser.add_argument("--work", type=Path, default=base / "sol-frontlight-7.2.10")
    args = parser.parse_args()
    src, ref, work = (p.resolve() for p in (args.source, args.reference, args.work))
    if not work.name.startswith("sol-frontlight-"):
        parser.error("private --work basename must start with sol-frontlight-")
    if any(work == p or work.is_relative_to(p) or p.is_relative_to(work) for p in (src, ref)):
        parser.error("private output must not overlap source or reference")
    for name in (".config", "Module.symvers"):
        if not (ref / name).is_file():
            parser.error(f"missing read-only reference {ref / name}")
    obj, mod = work / "kernel", work / "module"
    for p in (obj, mod):
        if p.is_symlink():
            parser.error(f"refusing symlink output {p}")
        p.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ref / ".config", obj / ".config")
    for name in ("Makefile", "a9-frontlight-core.c", "a9-frontlight-core.h", "leds-a9-frontlight.c"):
        shutil.copyfile(here / "driver" / name, mod / name)
    common = ["make", "-C", str(src), f"O={obj}", "ARCH=arm64",
              "CROSS_COMPILE=aarch64-unknown-linux-gnu-", "HOSTCC=gcc"]
    subprocess.run([*common, "olddefconfig"], check=True)
    subprocess.run([*common, "-j2", "modules_prepare"], check=True)
    shutil.copyfile(ref / "Module.symvers", obj / "Module.symvers")
    subprocess.run([*common, f"M={mod}", "CONFIG_LEDS_A9_FRONTLIGHT=m", "W=1", "-j2", "modules"], check=True)
    print(f"Compiled offline only: {mod / 'a9-frontlight.ko'}")


if __name__ == "__main__":
    main()
