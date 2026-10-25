#!/usr/bin/env python3
"""Extract actual worker and idle-timeout helpers for a deterministic host test.

Usage: idle-policy.py a9_eink_drv.c idle-policy.inc
The C harness supplies time, wakeups and board calls; worker policy is verbatim.
"""
import pathlib
import re
import sys

source = pathlib.Path(sys.argv[1]).read_text()


def function(name, required=True):
    match = re.search(r"^static [^\n]+\b" + name + r"\(", source, re.M)
    if not match:
        if required:
            raise SystemExit("missing production function: " + name)
        return ""
    start = source.index("{", match.start())
    depth, end = 1, start + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[match.start():end]


parts = [function("a9_hold_ms"), function("a9_idle_clip", False), function("a9_session_hold", False), function("a9_idle_timeout", False), function("a9_thread")]
pathlib.Path(sys.argv[2]).write_text("/* Verbatim production worker policy. */\n" +
    "\n\n".join(p for p in parts if p) + "\n")
