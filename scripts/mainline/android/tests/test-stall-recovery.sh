#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
# Reuse the normal opener fixture; all commands target fake sysfs.
eval "$(sed '/^R=$(mktemp/,$d' "$HERE/tests/test-eink-gates.sh")"
finish() { rm -rf "$R"; }
trap finish EXIT
# Model synchronous sysfs grant acknowledgement and driven progress. The old
# fixture left kind1/3 after a successful write and only checked enable=1.
receipt_fixture() {
 echo 'dry 0 aborted 0' >> "$E/status"
 realcat=$(command -v cat)
 cat > "$R/stubs/cat" <<S
#!/bin/sh
if [ "\${1##*/}" = stall_recovery ] && [ -f "$E/recover_stall" ] && [ "\$("$realcat" "$E/recover_stall")" = 1 ]; then
 sed -i 's/kind [13]/kind 0/; s/retries 0/retries 1/; s/grants 0/grants 1/' "$E/stall_recovery"
fi
exec "$realcat" "\$@"
S
 cat > "$R/stubs/sleep" <<S
#!/bin/sh
if [ "\$1" = 0.5 ] && [ "\$(cat "$M/a9_eink/parameters/enable")" = 1 ]; then
 sed -i 's/updates 2/updates 3/; s/frames 0/frames 1/; s/aborted 1/aborted 0/' "$E/status"
fi
S
 chmod +x "$R/stubs/cat" "$R/stubs/sleep"
}
for scenario in recovered inflight rail hard writefail cancelbackoff canceladmission; do
 R=$(mktemp -d); mk "$R" N; M=$R/sys/module
 E=$R/sys/devices/platform/eink-display/drm/card1/card1-DSI-1/eink
 echo 'kind 1 retries 0 limit 2 backoff_ms 5000 result 0 pmic_hard 0 grants 0' > "$E/stall_recovery"
 : > "$E/recover_stall"
 receipt_fixture
 sed -i 's/aborts 0/aborts 12/' "$E/status"
 cat > "$R/stubs/dmesg" <<'S'
#!/bin/sh
printf '%s\n' '[ 1.0] a9-eink: control template loaded' '[ 2.0] a9-eink: frame-progress fault latched' '[ 3.0] a9-eink: starvation recovery granted'
S
 chmod +x "$R/stubs/dmesg"
 case "$scenario" in
 inflight) sed -i 's/kind 1/kind 3/' "$E/stall_recovery" ;;
 rail) sed -i 's/rail_fault 0/rail_fault 1/' "$E/status" ;;
 hard) sed -i 's/kind 1/kind 2/' "$E/stall_recovery" ;;
 cancelbackoff)
  printf '#!/bin/sh\necho vendor.a9ml.eink.gate=closed >> "%s/props"\n' "$R" > "$R/stubs/sleep" ;;
 canceladmission)
  sed -i 's/template 1/template 0/' "$E/status"
  cat > "$R/stubs/sleep" <<S
#!/bin/sh
if [ "\$1" = 0.5 ]; then
 sed -i 's/template 0/template 1/' "$E/status"
 echo vendor.a9ml.eink.gate=closed >> "$R/props"
fi
S
  ;;
 writefail) rm "$E/recover_stall"; mkdir "$E/recover_stall" ;;
 esac
 if run "$R" recover; then rc=0; else rc=$?; fi
 gates=$(cat "$M/a9_eink/parameters/enable")$(cat "$M/fp9929a/parameters/allow_power_on")$(cat "$M/panel_hisense_hlte556n_eink/parameters/enable")
 if [ "$scenario" = recovered ] || [ "$scenario" = inflight ]; then
  [ "$rc" = 0 ] && [ "$gates" = 111 ]
  grep -q 'vendor.a9ml.eink.aborts_base=12' "$R/props"
  grep -q 'vendor.a9ml.eink.gate=open' "$R/props"
  echo 'PASS manual recovery after a9 clear: historical logs/counters retained, gates reopen'
  # A subsequent error after the floor must still close the watcher.
  echo 'kind 0 retries 1 limit 2 backoff_ms 30000 result 0 pmic_hard 0 grants 1' > "$E/stall_recovery"
  printf '\necho "[ 4.0] a9-eink: power-up aborted"\n' >> "$R/stubs/dmesg"
  if run "$R" watch; then exit 1; fi
  [ "$(cat "$M/a9_eink/parameters/enable")" = 0 ]
  echo 'PASS new error after recovery is still a hard stop'
  bundle=$(echo "$R"/faults/*)
  [ -s "$bundle/status.txt" ] && [ -s "$bundle/state.txt" ]
  grep -q 'power-up aborted' "$bundle/dmesg.txt"
  [ "$(stat -c %a "$bundle/dmesg.txt")" = 600 ]
  echo 'PASS full fault dmesg/status captured privately before recovery handling'
 else
  [ "$rc" != 0 ] && [ "$gates" = 000 ]
  echo "PASS $scenario refuses and leaves gates closed"
 fi
 rm -rf "$R"
done

# Automatic in-flight recovery must capture the fault BEFORE the first sleep.
R=$(mktemp -d); mk "$R" N; M=$R/sys/module
E=$R/sys/devices/platform/eink-display/drm/card1/card1-DSI-1/eink
echo vendor.a9ml.eink.gate=open >> "$R/props"
echo 'kind 3 retries 0 limit 2 backoff_ms 5000 result 0 pmic_hard 0 transport_hard 0 grants 0' > "$E/stall_recovery"
echo 'valid 1 phase 5 in_commit 1 raw_ret 0' > "$E/stall_snapshot"
sed -i 's/stall_fault 0/stall_fault 1/' "$E/status"
: > "$E/recover_stall"
receipt_fixture
cat > "$R/stubs/sleep" <<S
#!/bin/sh
if [ "\$1" = 5 ]; then
 bundle=\$(echo "$R"/faults/*)
 test -s "\$bundle/dmesg.txt" || exit 9
 grep -q 'stall_fault 1' "\$bundle/status.txt" || exit 10
 echo snapshot-before-backoff > "$R/order"
 sed -i 's/stall_fault 1/stall_fault 0/' "$E/status"
 echo 'kind 0 retries 1 limit 2 backoff_ms 30000 result 0 pmic_hard 0 transport_hard 0 grants 1' > "$E/stall_recovery"
elif [ "\$1" = 0.5 ]; then
 sed -i 's/updates 2/updates 3/; s/frames 0/frames 1/; s/aborted 1/aborted 0/' "$E/status"
else
 echo vendor.a9ml.eink.gate=closed >> "$R/props"
fi
S
chmod +x "$R/stubs/sleep"
run "$R" watch
[ "$(cat "$R/order")" = snapshot-before-backoff ]
[ "$(cat "$M/a9_eink/parameters/enable")" = 1 ]
echo 'PASS automatic kind3: snapshot first, backoff, normal reopen; later close cancels watcher'
rm -rf "$R"
