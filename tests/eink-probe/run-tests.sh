#!/usr/bin/env bash
# Offline tests for scripts/eink-probe (fake camera + fake adb) and scripts/eink-probe/sessions (fake phone).
# No phone, no webcam. Optional first arg: a unittest selector, e.g. test_session or test_probe.
D=$(cd "$(dirname "$0")" && pwd)
mods=${*:-test_probe test_session}
if python3 -c 'import numpy, cv2' 2>/dev/null; then cd "$D" && exec python3 -m unittest -v $mods; fi
exec nix-shell -p 'python3.withPackages(p:[p.numpy p.opencv4 p.pillow])' --run "cd $D && python3 -m unittest -v $mods"
