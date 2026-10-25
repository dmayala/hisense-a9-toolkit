#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Offline board/Android DTS checks: two available AW LEDs, optional KTD off."""
import argparse
import hashlib
from pathlib import Path
import subprocess


def main():
    here = Path(__file__).resolve().parents[1]
    repo = here.parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.home() / "A9-build/mainline/linux-7.2.10")
    args = parser.parse_args()
    out = here / "build/dt"
    out.mkdir(parents=True, exist_ok=True)
    includes = [repo / "docs/mainline", args.source / "arch/arm64/boot/dts/qcom",
                args.source / "include", here, repo / "scripts/mainline/pmic"]
    cpp = ["gcc", "-E", "-nostdinc", "-undef", "-D__DTS__", "-x", "assembler-with-cpp"]
    for p in includes:
        cpp += ["-I", str(p)]
    android = (repo / "scripts/mainline/android/sm6115-hisense-hlte556n-android.dts").read_text()
    fragment = (here / "a9-frontlight.dtsi").read_text()
    start = android.index("#include <dt-bindings/gpio/gpio.h>", android.index("/* ---- BEGIN frontlight"))
    end = android.index("/* ---- END frontlight */", start)
    assert android[start:end] == fragment[fragment.index("#include <dt-bindings/gpio/gpio.h>"):]
    assert f"sha256 {hashlib.sha256(fragment.encode()).hexdigest()}" in android
    for variant in ("baseline", "frontlight", "android"):
        text = '#include "sm6115-hisense-hlte556n.dts"\n'
        if variant == "frontlight":
            text += '#include "a9-frontlight.dtsi"\n'
        elif variant == "android":
            text = android
        (out / f"{variant}.dts").write_text(text)
        with (out / f"{variant}.pre.dts").open("w") as f:
            subprocess.run([*cpp, str(out / f"{variant}.dts")], stdout=f, check=True)
        with (out / f"{variant}.dtc.log").open("w") as f:
            subprocess.run(["dtc", "-I", "dts", "-O", "dtb", "-o", str(out / f"{variant}.dtb"),
                            str(out / f"{variant}.pre.dts")], stderr=f, check=True)

    def prop(variant, node, key, fmt="s"):
        return subprocess.check_output(["fdtget", "-t", fmt, str(out / f"{variant}.dtb"), node, key], text=True).strip()

    tlmm = "/soc@0/pinctrl@500000"
    for variant in ("frontlight", "android"):
        phandle = int(prop(variant, tlmm, "phandle", "u"))
        for bus, color, aw_gpio, ktd_gpio in ((0, "white", 85, 101), (1, "warm", 83, 88)):
            bus_path = f"/soc@0/geniqup@4ac0000/i2c@{0x4a80000 + bus * 0x4000:x}"
            assert prop(variant, bus_path, "status") == "okay"
            for addr, chip, loc, gpio in ((0x36, "aw99703", "center", aw_gpio), (0x35, "ktd3137", "edge", ktd_gpio)):
                path = f"{bus_path}/backlight@{addr:x}"
                assert prop(variant, path, "compatible") == f"hisense,a9-{chip}-frontlight"
                assert prop(variant, path, "label") == f"a9:{color}:{loc}"
                assert prop(variant, path, "default-state") == "keep"
                assert prop(variant, path, "status") == ("okay" if addr == 0x36 else "disabled")
                assert prop(variant, path, "enable-gpios", "u") == f"{phandle} {gpio} 0"

    def reserved(variant):
        vals = list(map(int, prop(variant, tlmm, "gpio-reserved-ranges", "u").split()))
        return {pin for start, count in zip(vals[::2], vals[1::2]) for pin in range(start, start + count)}

    before = reserved("baseline")
    for variant in ("frontlight", "android"):
        after = reserved(variant)
        assert before - after == {4, 5, 83, 85}
        assert not after - before
        assert {88, 101}.issubset(after)
        assert prop(variant, "/soc@0/mmc@4784000", "status") == "disabled"
    print("PASS: full board/Android DTS, AW enabled, KTD disabled, HWEN88/101 reserved, inline block synchronized")


if __name__ == "__main__":
    main()
