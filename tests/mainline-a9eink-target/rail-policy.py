#!/usr/bin/env python3
"""Extract complete production rail/update/selector/reset functions verbatim.

Usage: rail-policy.py DRIVER GUARD OUT_DIRECTORY
Optional A9_FP_STATUS_HEADER supplies the real status schema; otherwise the
fixture's documented mock structure supplies the hardware interface fields.
No production statement is rewritten or replaced by this extractor.
"""
import os
from pathlib import Path
import re
import sys

def function(source, name):
    match = re.search(r"^static (?:void|int|bool|u64) " + name + r"\([^;{]*\)\s*\{", source, re.M)
    if not match:
        raise SystemExit("missing production function: " + name)
    start = source.index("{", match.start())
    depth, end = 1, start + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]

driver = Path(sys.argv[1]).read_text()
guard = Path(sys.argv[2]).read_text()
out = Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
job = re.search(r"^struct a9_job \{.*?^\};", driver, re.M | re.S)
clear = re.search(r"^#define A9_MODE_CLEAR .+$", driver, re.M)
if not job or not clear:
    raise SystemExit("missing production job or Clear definition")
names = ("a9_session_frames", "a9_temps", "a9_hv_sample", "a9_hv_good", "a9_hv_is_on",
         "a9_hv_maybe_on", "a9_force_hv_off", "a9_power_off", "a9_rail_fault",
         "a9_check_live_rails", "a9_power_on", "a9_unprepare", "__a9_update", "a9_take_job",
         "a9_pg_wf_end_low", "a9_pg_wf_end")
# Include any shared rail-sample validator added by the integration fix.
extras = [name for name in re.findall(r"^static (?:void|int|bool) (a9_\w*rail\w*)\(", driver, re.M)
          if name not in names]
names = (*names, *extras)
parts = [function(driver, name) for name in names]
parts.append(function(guard, "a9_clear_fault_set"))
# 0042: the production session-counter type (a9_eink.h next to the driver).
stats = re.search(r"^struct a9_session_stats \{.*?^\};",
                  (Path(sys.argv[1]).parent / "a9_eink.h").read_text(), re.M | re.S)
if not stats:
    raise SystemExit("missing struct a9_session_stats (patch 0042)")
(out / "rail-policy-session.inc").write_text(stats.group() + "\n")
(out / "rail-policy-production.inc").write_text("/* Verbatim production functions. */\n" +
    clear.group() + "\n" + job.group() + "\n\n" +
    "\n".join(part[:part.index("{")].rstrip() + ";" for part in parts) +
    "\n\n" + "\n\n".join(parts) + "\n")
if header := os.environ.get("A9_FP_STATUS_HEADER"):
    schema = re.search(r"^struct fp9929a_status \{.*?^\};", Path(header).read_text(), re.M | re.S)
    if not schema:
        raise SystemExit("missing fp9929a_status in supplied production header")
    hdr = Path(header).read_text()
    nested = [m.group() for n in ("fp9929a_pg_point", "fp9929a_pd_trace")
              if (m := re.search(r"^struct " + n + r" \{.*?^\};", hdr, re.M | re.S))]
    (out / "rail-policy-status.inc").write_text("\n".join(nested + [schema.group()]) + "\n")
    print("-DA9_TEST_FP_STATUS_EXTRACTED=1")
