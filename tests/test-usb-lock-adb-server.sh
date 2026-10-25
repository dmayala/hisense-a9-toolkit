#!/usr/bin/env bash
# Offline regression: an adb server spawned by an adb call must not keep the USB
# lock after the script exits. Fake adb: `start-server`, or any command with no
# server running, forks a daemon that inherits every open fd (like the real one).
set -u
R=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d); trap 'kill $(cat "$T/pid" 2>/dev/null) 2>/dev/null; rm -rf "$T"' EXIT
mkdir "$T/bin"
cat > "$T/bin/adb" <<EOF
#!/usr/bin/env bash
if [ ! -e "$T/pid" ]; then
  ( setsid sleep 30 </dev/null >/dev/null 2>&1 & echo \$! > "$T/pid" )   # inherits fd 9 if open
  sleep 0.2
fi
exit 0
EOF
chmod +x "$T/bin/adb"
fail=0
export PATH="$T/bin:$PATH" XDG_RUNTIME_DIR="$T"
# script that locks, then runs adb (as android-boot.sh does), then exits
bash -c ". '$R/scripts/a9-env.sh'; a9_usb_lock; adb devices" >/dev/null 2>&1
if [ -e "$T/pid" ] && kill -0 "$(cat "$T/pid")" 2>/dev/null; then
  if ( exec 8>"$T/a9-usb.lock"; flock -n 8 ); then echo "PASS lock free after exit, adb server still alive"
  else echo "FAIL adb server keeps the USB lock"; fail=1; fi
else echo "FAIL fake adb server not running"; fail=1; fi
exit $fail
