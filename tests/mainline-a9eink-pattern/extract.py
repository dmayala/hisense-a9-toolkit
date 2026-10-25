#!/usr/bin/env python3
"""Extract exact production functions; only kernel/DRM infrastructure is mocked."""
import pathlib
import re
import sys

source = pathlib.Path(sys.argv[1]).read_text()
names = ("a9_fmt", "a9_plane_atomic_update", "a9_ctl_store",
         "test_pattern_store", "a9_take_job")
parts = []
for name in names:
    match = re.search(r"^static [^\n]+\b" + name + r"\(", source, re.M)
    if not match:
        raise SystemExit("missing production function: " + name)
    start = source.index("{", match.start())
    depth, end = 1, start + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    parts.append(source[match.start():end])
job = re.search(r"^struct a9_job \{.*?^\};", source, re.M | re.S)
if not job:
    raise SystemExit("missing production a9_job")
pathlib.Path(sys.argv[2]).write_text("/* Verbatim production source. */\n" +
    job.group() + "\n\n" + "\n\n".join(parts) + "\n")
