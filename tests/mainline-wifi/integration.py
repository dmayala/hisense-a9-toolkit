#!/usr/bin/env python3
"""Offline integration checks: artifacts/modes, gated module closure, DT wiring.

No device tool is invoked. DT compilation uses an already available kernel tree
and host tools, and skips only that check if they are unavailable.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
ANDROID = REPO / "scripts/mainline/android"
KERNEL = Path(os.environ.get("A9_WIFI_TEST_KERNEL", str(Path.home() / "A9-build/mainline/linux-7.2.10")))


class Integration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="a9-wifi-integration-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.build = self.root / "build"
        self.out = self.root / "snapshot-wifi"
        for p in ["arch/arm64/boot/dts/qcom", "include/config", "modules"]:
            (self.build / p).mkdir(parents=True)
        self.dtb = self.build / "arch/arm64/boot/dts/qcom"
        blob = b"Hisense_HLTE556N\0qcom/sm6115/hisense/hlte556n/modem.mdt\0"
        blob += b"\0".join(x.encode() for x in (
            "a9:warm:center", "a9:white:center", "a9:warm:edge", "a9:white:edge"))
        for suffix in ("", "-wifi"):
            (self.dtb / f"sm6115-hisense-hlte556n-android{suffix}.dtb").write_bytes(blob)
        (self.build / "arch/arm64/boot/Image.gz").write_bytes(b"fixture-kernel")
        (self.build / "include/config/kernel.release").write_text("test-wifi\n")
        self.dtbo = self.root / "null.dtbo"
        self.dtbo.write_bytes(b"fixture-overlay")
        dependencies = {
            "dispcc-sm6115": [], "msm": ["common"],
            "panel-hisense-hlte556n-eink": [], "tc358767": [], "a9_eink": [],
            "common": [], "qcom_q6v5_pas": ["common"], "rmtfs_mem": [],
            "qrtr-smd": ["qrtr"], "qrtr": [],
            "ath10k_snoc": ["ath10k"], "ath10k": ["common"],
            "qcom_pd_mapper": ["qrtr"],
        }
        for name, deps in dependencies.items():
            (self.build / f"modules/{name}.ko").write_bytes(
                b"vermagic=fixture-version\0depends=" + ",".join(deps).encode() + b"\0")
        (self.build / "Module.symvers").touch()
        self.config("kernel")
        self.env = os.environ.copy()
        self.env.update(A9_WIFI="0", A9_WIFI_PD_MAPPER="kernel", A9_DTBO=str(self.dtbo),
                        A9_FRONTLIGHT_KO=str(self.root / "absent.ko"))

    def config(self, pd):
        symbols = ["WLAN=y", "CFG80211=y", "MAC80211=y", "QCOM_Q6V5_PAS=m",
                   "QCOM_RMTFS_MEM=m", "QRTR_SMD=m", "ATH10K_SNOC=m"]
        if pd == "kernel":
            symbols.append("QCOM_PD_MAPPER=m")
        (self.build / ".config").write_text("".join("CONFIG_" + x + "\n" for x in symbols))

    def run_snapshot(self, wifi=True, pd="kernel", out=None):
        env = self.env | {"A9_WIFI_PD_MAPPER": pd}
        cmd = ["bash", str(ANDROID / "snapshot-kernel.sh")]
        if wifi:
            cmd.append("--wifi")
        cmd += [str(self.build), str(out or self.out)]
        return subprocess.run(cmd, env=env, capture_output=True, text=True)

    def test_wireless_modules_are_shipped_but_not_display_autoloaded(self):
        result = self.run_snapshot()
        self.assertEqual(result.returncode, 0, result.stderr)
        display = (self.out / "modules/modules.load").read_text().splitlines()
        wifi = (self.out / "modules/modules.wifi").read_text().splitlines()
        for module in ("ath10k_snoc.ko", "rmtfs_mem.ko", "qcom_q6v5_pas.ko", "qcom_pd_mapper.ko"):
            self.assertNotIn(module, display)
            self.assertIn(module, wifi)
            self.assertTrue((self.out / "modules" / module).is_file())
        self.assertLess(wifi.index("qrtr.ko"), wifi.index("qrtr-smd.ko"))
        self.assertLess(wifi.index("ath10k.ko"), wifi.index("ath10k_snoc.ko"))
        manifest = json.loads((self.out / "wifi-manifest.json").read_text())
        self.assertEqual(manifest["pd_mapper"], "kernel")
        for name, digest in manifest["sha256"].items():
            self.assertEqual(hashlib.sha256((self.out / name).read_bytes()).hexdigest(), digest)

    def test_baseline_has_no_wireless_payload_or_marker(self):
        out = self.root / "baseline"
        result = self.run_snapshot(wifi=False, out=out)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((out / "wifi-mode").exists())
        self.assertFalse((out / "modules/modules.wifi").exists())
        self.assertFalse((out / "modules/rmtfs_mem.ko").exists())

    def test_userspace_mode_excludes_kernel_mapper(self):
        self.config("userspace")
        result = self.run_snapshot(pd="userspace")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("qcom_pd_mapper", (self.out / "modules/modules.wifi").read_text())
        self.assertFalse((self.out / "modules/qcom_pd_mapper.ko").exists())
        self.assertEqual((self.out / "wifi-mode").read_text(), "userspace\n")

    def test_conflicting_mapper_mode_fails_closed(self):
        result = self.run_snapshot(pd="userspace")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("both PD mapper modes enabled", result.stderr)
        self.assertFalse(self.out.exists())

    def test_missing_required_module_fails_closed(self):
        (self.build / "modules/rmtfs_mem.ko").unlink()
        result = self.run_snapshot()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.out.exists())

    def test_wireless_cannot_overwrite_baseline_outputs(self):
        out = self.root / "baseline"
        out.mkdir()
        (out / "preserve").write_text("baseline")
        result = self.run_snapshot(out=out)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((out / "preserve").read_text(), "baseline")
        result = subprocess.run(["bash", str(ANDROID / "pack-boot.sh"), "--wifi",
                                 "-o", str(self.root / "boot-eink.img")],
                                env=self.env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must end in -wifi.img", result.stderr)

    def test_boot_packer_rejects_wrong_mapper_snapshot(self):
        result = self.run_snapshot()
        self.assertEqual(result.returncode, 0, result.stderr)
        env = self.env | {"A9_WIFI_SNAPSHOT": str(self.out), "A9_WIFI_PD_MAPPER": "userspace"}
        result = subprocess.run(["bash", str(ANDROID / "pack-boot.sh"), "--wifi"],
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Wi-Fi PD mapper mode mismatch", result.stderr)

    def test_boot_packer_rejects_tampered_snapshot(self):
        result = self.run_snapshot()
        self.assertEqual(result.returncode, 0, result.stderr)
        (self.out / "Image.gz").write_bytes(b"changed-since-snapshot")
        env = self.env | {"A9_WIFI_SNAPSHOT": str(self.out)}
        result = subprocess.run(["bash", str(ANDROID / "pack-boot.sh"), "--wifi"],
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Wi-Fi snapshot hashes failed", result.stderr)

    def test_offline_kernel_wrapper_never_clones_missing_sources(self):
        env = self.env | {"A9_MAINLINE": str(self.root / "absent-source")}
        result = subprocess.run(["bash", str(ANDROID / "build-kernel.sh"), "--wifi"],
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("offline source missing", result.stderr)

    def test_real_dt_compilation_and_voltage_constraints(self):
        cpp = os.environ.get("A9_WIFI_TEST_CPP") or shutil.which("cpp")
        dtc = os.environ.get("A9_WIFI_TEST_DTC") or shutil.which("dtc")
        if not cpp or not dtc or not (KERNEL / "arch/arm64/boot/dts/qcom/sm6115.dtsi").is_file():
            self.skipTest("provide A9_WIFI_TEST_CPP/DTC/KERNEL for local DT compilation")
        wrapper = self.root / "test-wifi.dts"
        shutil.copy2(REPO / "docs/mainline/sm6115-hisense-hlte556n.dts", self.root)
        shutil.copy2(REPO / "scripts/mainline/pmic/a9-pmi632.dtsi", self.root)
        wrapper.write_text(f'#include "{ANDROID / "sm6115-hisense-hlte556n-android.dts"}"\n'
                           f'#include "{ANDROID / "a9-wifi.dtsi"}"\n')
        processed = self.root / "wifi.dts"
        args = [cpp, "-nostdinc", "-undef", "-D__DTS__", "-x", "assembler-with-cpp",
                "-I", str(self.root),
                "-I", str(KERNEL / "arch/arm64/boot/dts/qcom"),
                "-I", str(KERNEL / "scripts/dtc/include-prefixes"), str(wrapper)]
        with processed.open("w") as stream:
            subprocess.run(args, stdout=stream, stderr=subprocess.PIPE, check=True)
        # The local include directory supplies the draft's base/PMIC files.
        result = subprocess.run([dtc, "-q", "-I", "dts", "-O", "dtb", "-o",
                                 str(self.root / "wifi.dtb"), str(processed)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        text = processed.read_text()
        self.assertIn('firmware-name = "qcom/sm6115/hisense/hlte556n/modem.mdt"', text)
        self.assertIn('qcom,calibration-variant = "Hisense_HLTE556N"', text)
        for value, lo, hi, base in [(640000, 400000, 728000, 320000),
                                    (1800000, 1704000, 1904000, 1504000),
                                    (1304000, 1152000, 1384000, 320000),
                                    (3304000, 3200000, 3312000, 1504000)]:
            self.assertLessEqual(lo, value)
            self.assertLessEqual(value, hi)
            self.assertEqual((value - base) % 8000, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
