#!/usr/bin/env bash
# ghost-ab session (see the module docstring). Python stdlib only; the probe and ghost-analyze wrappers bring numpy/cv2.
exec python3 "$(cd "$(dirname "$0")" && pwd)/ghost_ab.py" "$@"
