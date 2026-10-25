#!/usr/bin/env python3
"""Summarize saved logger kernel.log files; never emit raw kernel messages."""
import argparse
import hashlib
import json
import re
from pathlib import Path

KMSG = re.compile(r"\d+,(\d+),(\d+),[^;]*;(.*)")
TRACE = re.compile(r"A9_MSM_TRACE phase=([a-z0-9_.]+) rc=(-?\d+)$")
MODULES = ("dispcc-sm6115", "ubwc_config", "msm", "panel-hisense-hlte556n-eink",
           "tc358767", "a9_eink")


def summarize(raw):
    records = []
    for line, text in enumerate(raw.decode(errors="replace").splitlines(), 1):
        m = KMSG.fullmatch(text)
        if m:
            records.append((line, int(m[1]), int(m[2]), m[3]))
    result = {"sha256": hashlib.sha256(raw).hexdigest(), "records": len(records),
              "milestones": {}, "module_order": [], "probe_phases": {},
              "negative_probe_results": [], "pending_phases": [], "phase_durations_ms": {},
              "counts": dict(i2c_minus107=0, minus107=0, deferred_probe=0,
                             sync_state_pending=0, gpucc_sync_pending=0,
                             dummy_supply=0, bridge_cold_id=0, rail_latch=0,
                             rail_refusal=0, pg_waveform_dip=0, bias_fault=0,
                             splat=0, slow_insert=0)}
    if not records:
        return result
    result["coverage"] = dict(first_us=records[0][2], last_us=records[-1][2],
                              sequence_gaps=sum(max(0, b[1]-a[1]-1)
                                                for a, b in zip(records, records[1:])))
    starts = {}
    def mark(name, line, us):
        result["milestones"].setdefault(name, dict(line=line, us=us))
    for line, _, us, msg in records:
        # Explicitly discard command lines: they can contain private identifiers
        # and unrelated strings (including display parameters and addresses).
        if msg.startswith(("Kernel command line:", "Command line:")):
            continue
        counts = result["counts"]
        if re.search(r"(?<!\d)-107(?!\d)", msg):
            counts["minus107"] += 1
            if re.search(r"i2c|fp9929a|tc358767", msg, re.I):
                counts["i2c_minus107"] += 1
        if re.search(r"deferred probe|probe deferr|defer(?:red)? (?:probing|supplier)|-517\b", msg, re.I):
            counts["deferred_probe"] += 1
        if "sync_state() pending due to " in msg:
            counts["sync_state_pending"] += 1
            if msg.startswith("sm6115-gpucc "):
                counts["gpucc_sync_pending"] += 1
        if "supply " in msg and "using dummy regulator" in msg:
            counts["dummy_supply"] += 1
        if msg.startswith("tc358767 ") and "device ID not readable at probe" in msg:
            counts["bridge_cold_id"] += 1
        if msg.startswith("a9-eink ") and "FP9929A dropped out while the pipeline was on: rail fault latched" in msg:
            counts["rail_latch"] += 1
            mark("rail_latch", line, us)
        if msg.startswith("a9-eink ") and "update refused: rail fault latched" in msg:
            counts["rail_refusal"] += 1
        if msg.startswith("a9-eink ") and "PG low at waveform end" in msg:
            counts["pg_waveform_dip"] += 1
            mark("waveform_end_evidence", line, us)
        if re.search(r"bias.*(?:fault|fail|error)|(?:fault|fail|error).*bias", msg, re.I):
            counts["bias_fault"] += 1
        if re.search(r"^(?:BUG:|WARNING:|Kernel panic|INFO: task .*blocked)|(?:soft lockup|hard LOCKUP)", msg):
            counts["splat"] += 1
        if msg.startswith("a9ml-eink: MSM_INSMOD slow "):
            counts["slow_insert"] += 1
        if msg == "a9ml-eink: MSM_INSMOD begin":
            mark("msm_begin", line, us)
        if re.fullmatch(r"a9ml-eink: MSM_INSMOD end rc=-?\d+", msg):
            mark("msm_end", line, us)
            result["msm_rc"] = int(msg.rsplit("=", 1)[1])
        for module in MODULES:
            if msg.startswith("a9ml-eink: insmod " + module + ".ko"):
                result["module_order"].append(module)
                mark("module_" + module, line, us)
        if "Initialized msm " in msg:
            mark("gpu_card", line, us)
        if "Initialized msm-kms " in msg:
            mark("display_card", line, us)
        if "Initialized a9_eink " in msg:
            mark("eink_card", line, us)
        if msg.startswith("a9ml-eink: gates OPEN"):
            mark("gates_open", line, us)
        if msg.startswith("fp9929a ") and ": on: VCOM " in msg:
            mark("first_panel_power", line, us)
        if msg.startswith("fp9929a ") and "at probe: " in msg:
            mark("pmic_probe", line, us)
        if msg.startswith("fp9929a ") and "bootloader left the E Ink rails up: powering down" in msg:
            mark("pmic_boot_power_down", line, us)
        if msg.startswith("fp9929a ") and "power-up refused (safe default)" in msg:
            mark("pmic_safe_default", line, us)
        m = TRACE.search(msg)
        if m:
            phase, rc = m[1], int(m[2])
            result["probe_phases"][phase] = dict(line=line, us=us, rc=rc)
            if phase.endswith(".begin"):
                starts[phase[:-6]] = us
            if phase.endswith(".end"):
                closing = [phase[:-4]]
                # These existing entry markers share their enclosing end.
                closing += {"adreno.probe.end": ["adreno.bind"],
                            "adreno.init.end": ["gpu.init"],
                            "dpu.probe.end": ["dpu.component"]}.get(phase, [])
                for name in closing:
                    begin = starts.pop(name, None)
                    if begin is not None:
                        result["phase_durations_ms"][name] = round((us-begin)/1000, 3)
            # dpu.clocks.end rc=6 is the successful clock count, not an errno.
            if rc < 0:
                result["negative_probe_results"].append(dict(phase=phase, rc=rc, line=line))
    result["pending_phases"] = sorted(starts)
    points = result["milestones"]
    if "msm_begin" in points:
        result["since_msm_begin_ms"] = {
            name: round((point["us"]-points["msm_begin"]["us"])/1000, 3)
            for name, point in points.items() if point["us"] >= points["msm_begin"]["us"]}
        result["insertion_pending_at_capture_end"] = "msm_end" not in points
    # Physical refresh is NOT the first dry-run atomic commit/card registration.
    # Power-on is a lower bound and a waveform-end PG sample is an upper bound
    # on the first completed drive. Neither alone supplies an exact refresh time.
    if "first_panel_power" in points and "waveform_end_evidence" in points:
        result["first_drive_evidence_us"] = [points["first_panel_power"]["us"],
                                             points["waveform_end_evidence"]["us"]]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path)
    args = parser.parse_args()
    by_hash = {}
    for path in args.logs:
        result = summarize(path.read_bytes())
        if result["sha256"] in by_hash:
            by_hash[result["sha256"]]["sources"].append(str(path))
        else:
            result["sources"] = [str(path)]
            by_hash[result["sha256"]] = result
    print(json.dumps(list(by_hash.values()), indent=2))


if __name__ == "__main__":
    main()
