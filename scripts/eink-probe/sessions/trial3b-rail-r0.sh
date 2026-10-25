#!/usr/bin/env bash
# trial3b-rail-r0 session (see runs/eink-rail-diag.md). Python stdlib only; the probe wrapper brings numpy/cv2.
exec python3 "$(cd "$(dirname "$0")" && pwd)/trial3b_rail_r0.py" "$@"
