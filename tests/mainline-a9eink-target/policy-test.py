#!/usr/bin/env python3
"""Extract production update policy verbatim for the host regression."""
import pathlib
import re
import sys

source = pathlib.Path(sys.argv[1]).read_text()
parts = []
for name in ("a9_unprepare", "__a9_update"):
    match = re.search(r"^static void " + name + r"\(", source, re.M)
    if not match:
        raise SystemExit("missing production function: " + name)
    start = source.index("{", match.start())
    level, end = 1, start + 1
    while level:
        level += (source[end] == "{") - (source[end] == "}")
        end += 1
    parts.append(source[match.start():end])
job = re.search(r"^struct a9_job \{.*?^\};", source, re.M | re.S)
if not job:
    raise SystemExit("missing production a9_job")
pathlib.Path(sys.argv[2]).write_text("/* Verbatim production update policy. */\n" + job.group() + "\n\n" + "\n\n".join(parts) + "\n")
