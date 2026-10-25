#!/usr/bin/env python3
"""Replay production fault snapshots on fake sysfs; no devices or gate writes."""
import argparse
import os
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "android/vendor/bin/a9ml-eink.sh"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shell", default="sh")
    args = ap.parse_args()
    # Definitions and test-root assignments only; never dispatch setup/open/watch.
    definitions = SCRIPT.read_text().split('\ncase "$1" in\nrecover)')[0]
    with tempfile.TemporaryDirectory(prefix="a9-rail-context-") as tmp:
        root = Path(tmp)
        sysfs = root / "sys"
        status = sysfs / "class/drm/card1-DSI-1/eink/status"
        status.parent.mkdir(parents=True)
        (root / "bin").mkdir()
        (root / "proc/pressure").mkdir(parents=True)
        (root / "count").write_text("0\n")
        props = {"vendor.a9ml.eink.card": "card1", "init.svc.a9ml_wifi_session": "running",
                 "init.svc.vendor.a9ml_wifi_hal": "stopped", "init.svc.wpa_supplicant": "restarting"}
        getprop = root / "bin/getprop"
        getprop.write_text('#!/bin/sh\ncase "$1" in\n' +
                          'vendor.a9ml.eink.snapshot_count) cat "$A9_TEST_ROOT/count" ;;\n' +
                          "\n".join(f'{k}) echo {v} ;;' for k, v in props.items()) + "\nesac\n")
        (root / "bin/setprop").write_text('#!/bin/sh\n[ "$1" != vendor.a9ml.eink.snapshot_count ] || '
                                         'echo "$2" > "$A9_TEST_ROOT/count"\n')
        (root / "bin/dmesg").write_text('#!/bin/sh\necho "[ 1.0] synthetic rail fault"\n')
        for p in (root / "bin").iterdir():
            p.chmod(0o755)
        for key, value in {"pg_dip_policy": 1, "hv_hold_ms": 3000, "hv_max_ms": 15000}.items():
            path = sysfs / "module/a9_eink/parameters" / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(value) + "\n")
        for channel in ("a9:warm:center", "a9:white:center", "a9:warm:edge", "a9:white:edge"):
            path = sysfs / "class/leds" / channel / "brightness"
            path.parent.mkdir(parents=True)
            path.write_text("123\n")
        env = dict(os.environ, PATH=str(root / "bin") + ":" + os.environ["PATH"],
                   A9ML_SYS=str(sysfs), A9ML_PROC=str(root / "proc"),
                   A9ML_KMSG=str(root / "kmsg"), A9ML_FAULT_DIR=str(root / "faults"),
                   A9_TEST_ROOT=str(root))
        def run():
            before = {str(p): p.read_bytes() for p in sysfs.rglob("*") if p.is_file()}
            subprocess.run([args.shell, "-c", definitions + "\nsnapshot_fault synthetic"],
                           env=env, check=True, timeout=20)
            assert before == {str(p): p.read_bytes() for p in sysfs.rglob("*") if p.is_file()}
            return (root / "kmsg").read_text()
        status.write_text("enable 0 waveform 1 rail_fault 1\nlast mode 0x206 pre 0x203 clear 0 "
                          "type 6 bin 8 t1 28 t2 28 frames 8 steps 1 dry 0 aborted 0 "
                          "rect 0,0-1648,824 begin_us 123 total_us 456 prep_us 0\n")
        out = run()
        assert "last_update: mode 0x206 pre 0x203 clear 0 type 6 bin 8 t1 28 t2 28" in out, out
        assert "pg_dip_policy 1" in out and "hv_hold_ms 3000" in out, out
        assert out.count("frontlight:") == 4 and "wpa_supplicant restarting" in out, out
        assert "vendor.a9ml_wifi_hal stopped" in out, out
        print("PASS exact last-update/bin/temperature, PG telemetry policy, four cached lights, service enums")
        status.write_text("enable 0 rail_fault 1\nlast mode PRIVATE_DECOY bin PRIVATE_DECOY\n")
        (sysfs / "class/leds/a9:warm:center/brightness").write_text("PRIVATE_DECOY\n")
        (sysfs / "module/a9_eink/parameters/pg_dip_policy").unlink()
        getprop.write_text(getprop.read_text().replace("echo restarting", "echo PRIVATE_DECOY"))
        out = run()
        assert "PRIVATE_DECOY" not in out, out
        assert "last_update: unavailable" in out and "pg_dip_policy unavailable" in out, out
        assert "a9:warm:center unavailable" in out and "wpa_supplicant unavailable" in out, out
        print("PASS unavailable/malformed data omitted; diagnostic reads leave every sysfs byte intact")
        run()
        before = (root / "kmsg").read_bytes()
        run()
        assert before == (root / "kmsg").read_bytes()
        assert (root / "count").read_text().strip() == "3"
        print("PASS production snapshot limit remains three per boot")


if __name__ == "__main__":
    main()
