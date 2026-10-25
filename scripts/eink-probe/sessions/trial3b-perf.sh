#!/usr/bin/env bash
# trial3b-perf session (see runs/eink-perf-session.md). Python stdlib only; the probe wrapper brings numpy/cv2.
exec python3 "$(cd "$(dirname "$0")" && pwd)/trial3b_perf.py" "$@"
