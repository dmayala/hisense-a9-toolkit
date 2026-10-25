#!/usr/bin/env python3
"""Host-only replay of saved-kmsg analysis and production shell diagnostics."""
import argparse
import importlib.util
import os
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parents[1] / "android/vendor/bin/a9ml-eink.sh"
spec = importlib.util.spec_from_file_location("boot_analysis", HERE / "analyze.py")
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


def analyzer():
    msgs = ["Kernel command line: private-placeholder bias fault -107",
            "a9ml-eink: MSM_INSMOD begin",
            "A9_MSM_TRACE phase=gpu.request_irq.begin rc=0"]
    def log(messages):
        return "\n".join(f"6,{i},{i*1000},-;{s}" for i, s in enumerate(messages)).encode()
    result = analysis.summarize(log(msgs))
    assert result["insertion_pending_at_capture_end"]
    assert result["pending_phases"] == ["gpu.request_irq"]
    assert not any(result["counts"].values())
    assert "private-placeholder" not in str(result)
    msgs += ["A9_MSM_TRACE phase=gpu.request_irq.end rc=0",
             "A9_MSM_TRACE phase=dpu.clocks.begin rc=0",
             "A9_MSM_TRACE phase=dpu.clocks.end rc=6",
             "a9ml-eink: MSM_INSMOD end rc=0",
             "a9-eink eink-display: update refused: rail fault latched",
             "a9-eink eink-display: FP9929A dropped out while the pipeline was on: rail fault latched",
             "a9ml-eink: fault: kmsg: a9-eink eink-display: FP9929A dropped out while the pipeline was on: rail fault latched",
             "tc358767 0-000f: i2c read failed -107"]
    result = analysis.summarize(log(msgs))
    assert not result["insertion_pending_at_capture_end"]
    assert not result["pending_phases"] and not result["negative_probe_results"]
    assert result["counts"]["rail_latch"] == result["counts"]["rail_refusal"] == 1
    assert result["counts"]["i2c_minus107"] == 1
    assert result["phase_durations_ms"]["dpu.clocks"] == 1
    print("PASS saved-kmsg pending/completed phases, positive clock count, privacy, distinct faults")


def shell_diag(shell):
    # Source only the production function definitions; never dispatch setup/open.
    definitions = SCRIPT.read_text().split("# Boot-only DRM diagnosis")[0]
    with tempfile.TemporaryDirectory(prefix="a9-msm-diag-") as name:
        root = Path(name)
        task = root / "proc/123"
        task.mkdir(parents=True)
        # Include spaces and ')' in comm to exercise Linux stat field parsing.
        (task / "stat").write_text("123 (insmod ) test) " + " ".join(
            ["D"] + ["0"] * 18 + ["456"] + ["0"] * 5) + "\n")
        (task / "status").write_text("State:\tD (disk sleep)\n")
        (task / "wchan").write_text("wait_for_completion\n")
        (task / "stack").write_text("".join(
            "[<ffff123456789abc>] wait_for_completion+0x2/0x8\n" for _ in range(15))
            + "untrusted payload\n")
        env = dict(os.environ, A9ML_PROC=str(root / "proc"), A9ML_KMSG=str(root / "kmsg"))
        def run(body):
            (root / "kmsg").write_text("")
            subprocess.run([shell, "-c", definitions + "\n" + body], env=env,
                           check=True, timeout=15)
            return (root / "kmsg").read_text()
        out = run('test "$(msm_task_start 123)" = 456; msm_slow_diag 123 456')
        assert "state=D wchan=wait_for_completion" in out
        assert out.count("stack function=") == 12
        assert "ffff" not in out and "0x" not in out and "payload" not in out
        assert run("msm_slow_diag 123 999") == ""  # PID reuse
        (task / "stack").unlink()
        (task / "wchan").write_text("bad symbol /identifier\n")
        out = run("msm_slow_diag 123 456")
        assert "wchan=unavailable" in out and "stack unavailable" in out
        (task / "status").write_text("State:\tZ (zombie)\n")
        assert run("msm_slow_diag 123 456") == ""
        print("PASS production shell: bounded symbols, PID identity, unavailable data, exited child")
        # Exercise the real async insertion/wait path. A disposable host process
        # stands in for insmod; /proc reads are limited to that exact child.
        (root / "bin").mkdir()
        stub = root / "bin/insmod"
        stub.write_text('#!/bin/sh\n[ "$#" = 2 ] && [ "$1" = module.ko ] && '
                        '[ "$2" = separate_gpu_kms=1 ] || exit 91\nsleep 6\nexit 23\n')
        stub.chmod(0o755)
        env.update(A9ML_PROC="/proc", PATH=str(root / "bin") + ":" + env["PATH"])
        out = run("msm_insert module.ko separate_gpu_kms=1; test $? -eq 23; wait")
        assert "MSM_INSMOD slow after_ms=5000" in out, out
        assert out.index("MSM_INSMOD slow") < out.index("MSM_INSMOD end rc=23")
        print("PASS slow insertion: five-second capture precedes unchanged return code/arguments")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shell", default="sh", help="host shell or Android mksh/QEMU wrapper")
    args = ap.parse_args()
    analyzer()
    shell_diag(args.shell)


if __name__ == "__main__":
    main()
