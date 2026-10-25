#!/usr/bin/env bash
# Offline regression (2026-10-17): a killed script must not leave the USB lock
# held by its surviving children. android-boot.sh --hold left a subshell, tee
# (exec > >(tee ...)) and sleep running after its PID was killed, and they held
# /run/user/1000/a9-usb.lock because every child inherited the lock fd 9.
# Cases: (1) kill -9 of the script while children live -> lock free at once and
# the children are still alive (they are the reason it used to stick);
# (2) while the script lives the lock is refused (rc 3) -- children opened
# after the lock never hold it; (3) normal exit frees it.
set -u
R=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d)
trap 'kill $(cat "$T"/*.pid 2>/dev/null) 2>/dev/null; rm -rf "$T"' EXIT
mkdir "$T/bin"
printf '#!/bin/sh\nexit 0\n' > "$T/bin/adb"; chmod +x "$T/bin/adb"
export PATH="$T/bin:$PATH" XDG_RUNTIME_DIR="$T" A9_HOME="$R"
fail=0
ok()  { echo "PASS $*"; }
bad() { echo "FAIL $*"; fail=1; }
free() { ( exec 8>"$T/a9-usb.lock"; flock -n 8 ); }

# script: lock, then do what android-boot.sh does (tee on stdout, background
# subshell loop, foreground-style sleep), record pids, wait to be killed
cat > "$T/victim.sh" <<EOS
#!/usr/bin/env bash
. "$R/scripts/a9-env.sh"
a9_usb_lock
exec > >(tee -a "$T/log.txt") 2>&1
( while :; do sleep 5; done ) &
echo \$! > "$T/sub.pid"
sleep 600 &
echo \$! > "$T/sleep.pid"
echo \$\$ > "$T/parent.pid"
echo ready
wait
EOS
chmod +x "$T/victim.sh"

setsid "$T/victim.sh" >"$T/out.txt" 2>&1 </dev/null &
for _ in $(seq 50); do [ -s "$T/parent.pid" ] && grep -q ready "$T/out.txt" 2>/dev/null && break; sleep 0.1; done
P=$(cat "$T/parent.pid" 2>/dev/null)
[ -n "$P" ] || { echo "FAIL victim did not start"; exit 1; }

# (2) lock is held while the script lives
if free; then bad "lock free while the script is alive"; else ok "lock held while the script lives"; fi
bash -c ". '$R/scripts/a9-env.sh'; a9_usb_lock" >/dev/null 2>&1; rc=$?
[ $rc = 3 ] && ok "second a9_usb_lock refused (rc 3)" || bad "second a9_usb_lock rc $rc (want 3)"

# who holds the lock file open? Only the keeper may; never the children
holders=$(for p in /proc/[0-9]*; do ls -l "$p/fd" 2>/dev/null | grep -qF "$T/a9-usb.lock" && echo "${p#/proc/}"; done | tr '\n' ' ')
for c in "$(cat "$T/sub.pid")" "$(cat "$T/sleep.pid")"; do
  case " $holders" in *" $c "*) bad "child $c holds the lock fd";; *) ok "child $c does not hold the lock fd";; esac
done
case " $holders" in *" $P "*) bad "script itself still holds fd 9";; *) ok "script does not keep the lock fd";; esac

# (1) kill -9 the script: children survive, lock must be free (<= 2 s)
kill -9 "$P"
for _ in $(seq 20); do free && break; sleep 0.1; done
if free; then ok "lock free after kill -9 of the script"; else bad "lock still held after kill -9 (children: $holders)"; fi
kill -0 "$(cat "$T/sleep.pid")" 2>/dev/null && ok "the orphaned sleep is still alive (so only the keeper design frees the lock)" || bad "orphan sleep gone: test proves nothing"

# (3) normal exit
bash -c ". '$R/scripts/a9-env.sh'; a9_usb_lock; sleep 0.3" >/dev/null 2>&1
bash -c ". '$R/scripts/a9-env.sh'; a9_usb_lock; true" >/dev/null 2>&1 && ok "lock re-acquired right after a normal exit" || bad "lock not re-acquirable after a normal exit"
exit $fail
