#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Generate review patches. Reads the kernel; writes only this directory's patches."""
import argparse
import difflib
from pathlib import Path


def diff(path, old, new):
    header = f"diff --git a/{path} b/{path}\n"
    if not old:
        header += "new file mode 100644\n"
    return header + "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{path}" if old else "/dev/null", tofile=f"b/{path}"))


def main():
    here = Path(__file__).resolve().parent
    repo = here.parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.home() / "A9-build/mainline/linux-7.2.10")
    args = parser.parse_args()
    driver = "From: GPT-6.1 Sol (Codex)\nSubject: [PROPOSAL 1/2] leds: A9 raw AW99703/KTD3137 frontlight\n\n"
    path = "drivers/leds/Kconfig"
    old = (args.source / path).read_text()
    new = old.replace("endif # NEW_LEDS", 'source "drivers/leds/a9-frontlight/Kconfig"\n\nendif # NEW_LEDS')
    assert old != new
    driver += diff(path, old, new)
    path = "drivers/leds/Makefile"
    old = (args.source / path).read_text()
    driver += diff(path, old, old + "\nobj-$(CONFIG_LEDS_A9_FRONTLIGHT) += a9-frontlight/\n")
    for name in ("Kconfig", "Makefile", "a9-frontlight-core.h", "a9-frontlight-core.c", "leds-a9-frontlight.c"):
        driver += diff(f"drivers/leds/a9-frontlight/{name}", "", (here / "driver" / name).read_text())
    driver += diff("Documentation/devicetree/bindings/leds/hisense,a9-frontlight.yaml", "",
                   (here / "bindings/hisense,a9-frontlight.yaml").read_text())
    (here / "patches/0001-leds-a9-frontlight-PROPOSAL.patch").write_text(driver)
    dts = "From: GPT-6.1 Sol (Codex)\nSubject: [PROPOSAL 2/2] arm64: dts: qcom: A9 frontlight\n\n"
    dts += diff("arch/arm64/boot/dts/qcom/a9-frontlight.dtsi", "", (here / "a9-frontlight.dtsi").read_text())
    path = "arch/arm64/boot/dts/qcom/sm6115-hisense-hlte556n.dts"
    old = (repo / "docs/mainline/sm6115-hisense-hlte556n.dts").read_text()
    dts += diff(path, old, old + '\n#include "a9-frontlight.dtsi"\n')
    (here / "patches/0002-arm64-a9-frontlight-PROPOSAL.patch").write_text(dts)


if __name__ == "__main__":
    main()
