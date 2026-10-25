#!/usr/bin/env python3
"""Offline regression of the actual Android setup script's scheduling policy.

All paths/commands are fake. If CAP_SYS_NICE is usable, repeat with real
scheduler calls on two disposable children pinned to one CPU; never change
the scheduler/affinity of the caller or any existing system task.
"""
import json
import multiprocessing as mp
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

SCRIPT = Path(__file__).resolve().parents[1] / "vendor/bin/a9ml-eink.sh"


def tool():
    name, *args = sys.argv[2:]
    state_path = Path(os.environ["A9_TEST_STATE"])
    state = json.loads(state_path.read_text())
    pid = args[-1]
    entry = state[pid]  # reject every PID not explicitly owned by this test
    if name == "chrt":
        assert len(args) == 4 and args[0] in ("-o", "-f") and args[1] == "-p"
        assert args[0] != "-o" or args[2] == "0"
        policy = os.SCHED_OTHER if args[0] == "-o" else os.SCHED_FIFO
        priority = int(args[2])
        if os.environ.get("A9_TEST_REAL") == "1":
            os.sched_setscheduler(int(pid), policy, os.sched_param(priority))
        entry.update(policy=policy, priority=priority)
    else:
        assert name == "renice" and args[:3] == ["-n", "-10", "-p"]
        if os.environ.get("A9_TEST_REAL") == "1":
            os.setpriority(os.PRIO_PROCESS, int(pid), -10)
        entry["nice"] = -10
    state_path.write_text(json.dumps(state))
    with open(os.environ["A9_TEST_CALLS"], "a") as calls:
        calls.write(name + " " + " ".join(args) + "\n")


def setup_policy(names, real=False):
    with tempfile.TemporaryDirectory(prefix="a9-priority-") as tmp:
        root = Path(tmp)
        for directory in ("bin", "proc", "dev/dri", "mods", "sys/class/drm",
                          "sys/devices/platform/eink-display/drm/card7"):
            (root / directory).mkdir(parents=True, exist_ok=True)
        (root / "sys/class/drm/card7").symlink_to(
            root / "sys/devices/platform/eink-display/drm/card7")
        (root / "dev/dri/card7").touch()
        (root / "mods/modules.load").touch()  # never load a module
        state = {}
        for pid, name in names.items():
            directory = root / "proc" / str(pid)
            directory.mkdir()
            (directory / "comm").write_text(name + "\n")
            # Reproduce the previous wildcard's FIFO producer state.
            state[str(pid)] = dict(policy=os.SCHED_FIFO, priority=50, nice=0)
        (root / "state").write_text(json.dumps(state))
        for name in ("chrt", "renice"):
            stub = root / "bin" / name
            stub.write_text('#!/bin/sh\nexec "$A9_TEST_PYTHON" "$A9_TEST_FILE" --tool '
                            + name + ' "$@"\n')
            stub.chmod(0o755)
        for name, body in {
            "getprop": '[ "$1" = ro.boot.a9ml.eink ] && echo 1',
            "setprop": "exit 0", "chown": "exit 0", "chmod": "exit 0",
            "insmod": "exit 99",  # must not be reached
        }.items():
            stub = root / "bin" / name
            stub.write_text("#!/bin/sh\n" + body + "\n")
            stub.chmod(0o755)
        env = dict(os.environ, PATH=str(root / "bin") + ":" + os.environ["PATH"],
                   A9ML_SYS=str(root / "sys"), A9ML_DEV=str(root / "dev"),
                   A9ML_PROC=str(root / "proc"), A9ML_KMSG=str(root / "kmsg"),
                   A9ML_MODDIR=str(root / "mods"), A9ML_WAIT="1",
                   A9_TEST_STATE=str(root / "state"), A9_TEST_CALLS=str(root / "calls"),
                   A9_TEST_PYTHON=sys.executable, A9_TEST_FILE=str(Path(__file__).resolve()),
                   A9_TEST_REAL="1" if real else "0")
        subprocess.run(["sh", str(SCRIPT), "setup"], env=env, check=True, timeout=10)
        log = (root / "kmsg").read_text()
        assert "WARNING cannot set" not in log, log
        return json.loads((root / "state").read_text()), (root / "calls").read_text()


def fake_policy():
    names = {101: "a9_eink_r", 102: "a9_eink", 103: "a9_eink_extra",
             104: "crtc_commit0", 105: "crtc_event0", 106: "msm_xcommit0",
             107: "unrelated"}
    state, calls = setup_policy(names)
    assert state["101"] == dict(policy=os.SCHED_OTHER, priority=0, nice=-10)
    assert state["102"] == dict(policy=os.SCHED_FIFO, priority=50, nice=0)
    for pid in (104, 105, 106):
        assert state[str(pid)]["priority"] == 49
    for pid in (103, 107):
        assert state[str(pid)] == dict(policy=os.SCHED_FIFO, priority=50, nice=0)
        assert not any(line.endswith(" " + str(pid)) for line in calls.splitlines())
    assert calls.splitlines() == [
        "chrt -o -p 0 101", "renice -n -10 -p 101", "chrt -f -p 50 102",
        "chrt -f -p 49 104", "chrt -f -p 49 105", "chrt -f -p 49 106"]
    print("PASS: actual setup script restores producer OTHER/-10; only consumer FIFO50")


def worker(connection, cpu, producer):
    os.sched_setaffinity(0, {cpu})
    connection.send("ready")
    connection.recv()
    if producer:
        connection.send(time.monotonic())
        end = time.monotonic() + 0.4
        while time.monotonic() < end:
            pass
    connection.send(time.monotonic())


def same_cpu():
    # Probe only a disposable child; nice -10/FIFO need host privileges.
    probe = subprocess.run([sys.executable, "-c", """
import os
try:
    os.setpriority(os.PRIO_PROCESS, 0, -10)
    os.sched_setscheduler(0, os.SCHED_FIFO, os.sched_param(50))
    os.sched_setscheduler(0, os.SCHED_OTHER, os.sched_param(0))
except PermissionError:
    raise SystemExit(77)
"""], check=False)
    if probe.returncode == 77:
        print("SKIP: real same-CPU FIFO50/OTHER nice -10 contention (CAP_SYS_NICE unavailable)")
        return
    assert probe.returncode == 0
    cpu = min(os.sched_getaffinity(0))
    pipes, children = [], []
    try:
        for producer in (True, False):
            parent, child = mp.Pipe()
            process = mp.Process(target=worker, args=(child, cpu, producer))
            process.start()
            pipes.append(parent)
            children.append(process)
            assert parent.poll(5) and parent.recv() == "ready"
        setup_policy({children[0].pid: "a9_eink_r", children[1].pid: "a9_eink"}, True)
        assert os.sched_getscheduler(children[0].pid) == os.SCHED_OTHER
        assert os.getpriority(os.PRIO_PROCESS, children[0].pid) == -10
        assert os.sched_getscheduler(children[1].pid) == os.SCHED_FIFO
        assert os.sched_getparam(children[1].pid).sched_priority == 50
        pipes[0].send("start")
        assert pipes[0].poll(5)
        started = pipes[0].recv()
        pipes[1].send("wake")
        assert pipes[1].poll(5)
        consumed = pipes[1].recv()
        assert pipes[0].poll(5)
        finished = pipes[0].recv()
        assert started <= consumed < finished, (started, consumed, finished)
        print("PASS: FIFO50 consumer preempts OTHER/-10 producer on CPU", cpu)
    finally:
        for child in children:
            child.join(2)
            if child.is_alive():
                child.terminate()
                child.join()


if __name__ == "__main__":
    if sys.argv[1:] and sys.argv[1] == "--tool":
        tool()
    else:
        fake_policy()
        same_cpu()
