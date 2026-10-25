#!/usr/bin/env bash
# Offline tests for scripts/mainline/bluetooth/bt-pair.sh against a fake adb
# (fake-bt-adb.py). No device access. About 2 min.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
S=$REPO/scripts/mainline/bluetooth/bt-pair.sh
PARSE=$REPO/scripts/mainline/bluetooth/bt-pair-parse.py
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
printf 'AA:BB:CC:DD:EE:01\n' > "$T/bdaddr.txt"
printf 'yes\n' > "$T/enter"; printf 'no\n' > "$T/answer-no"
pass=0 fail=0
ok() { pass=$((pass + 1)); echo "ok   $1"; }
no() { fail=$((fail + 1)); echo "FAIL $1"; }
FORMS='AA:BB:CC:DD:EE:01|aa:bb:cc:dd:ee:01|AABBCCDDEE01|01:EE:DD:CC:BB:AA|11:22:33:44:55:66|22:33:44:55:66:77|XX:XX:XX:XX:55:66|neighbour-0|Fake Peer'

# sc <name> <flags> <expected rc> [extra args...]: one full `run` against the fake
sc() {
  local name=$1 flags=$2 want=$3; shift 3
  local d=$T/$name; mkdir -p "$d"
  ( export FAKE_BT_STATE=$d/state.json FAKE_BT_LOG=$d/calls.log FAKE_BT_SCENARIO=$flags \
      A9_BT_ADB=$HERE/fake-bt-adb.py A9_BT_SLEEP=0 A9_BT_PRIVATE_ADDR=$T/bdaddr.txt \
      A9_BT_OPERATOR_IN=${OPF:-$T/enter} A9_BT_ATTENDED=${ATT-yes}
    "$S" run --run "$d/run" --peer-name 'Fake Peer' "$@" > "$d/out.txt" 2>&1 )
  RC=$?; D=$d
  [ "$RC" = "$want" ] && ok "$name: rc $RC" || { no "$name: rc $RC, want $want"; tail -n 15 "$d/out.txt"; }
  # value-free: no address/neighbour/peer-name in stdout or any non-private file
  if cat "$d/out.txt" $(find "$d/run" -path "$d/run/private" -prune -o -type f -print 2>/dev/null) 2>/dev/null | grep -qE "$FORMS"; then
    no "$name: address or name leaked"; grep -hE "$FORMS" "$d/out.txt" | head -3
  else ok "$name: nothing leaked"; fi
  grep -q '^UNKNOWN\|^NON-ROOT' "$d/calls.log" 2>/dev/null && { no "$name: unknown or non-root command"; grep '^UNKNOWN\|^NON-ROOT' "$d/calls.log" | head -3; }
  [ ! -d "$d/run/private" ] || [ "$(stat -c %a "$d/run/private")" = 700 ] || no "$name: private dir not 0700"
  return 0
}
has() { grep -q -- "$2" "$D/$1" && ok "$3" || no "$3"; }
hasnt() { grep -q -- "$2" "$D/$1" && no "$3" || ok "$3"; }

# --- parser units
r=$(printf 'a 11:22:33:44:55:66 b XX:XX:XX:XX:55:66 c 1122334455AB d 112233-44-55-66\n' | python3 -I "$PARSE" redact)
echo "$r" | grep -qE '([0-9A-F]{2}:){5}|XX:XX|1122334455AB' && no "redact: $r" || ok "redact masks full, anonymised and compact addresses"
printf '[ 1.000000] Bluetooth: hci0: setting up wcn399x\n[ 1.200000] Bluetooth: hci0: Frame reassembly failed (-84)\n[ 1.300000] Bluetooth: hci0: QCA Product ID   :0x0000000a\n' > "$T/d1"
python3 -I "$PARSE" dmesg "$T/d1" | grep -q '^bad=' && ok "dmesg: -84 200 ms after the marker is a stop" || no "dmesg: late -84 admitted"
printf '[ 1.000000] Bluetooth: hci0: setting up wcn399x\n[ 1.080000] Bluetooth: hci0: Frame reassembly failed (-84)\n[ 1.090000] Bluetooth: hci0: Frame reassembly failed (-84)\n' > "$T/d2"
python3 -I "$PARSE" dmesg "$T/d2" | grep -q '^bad=' && ok "dmesg: a second -84 in one window is a stop" || no "dmesg: double -84 admitted"
printf '[ 1.000000] Bluetooth: hci0: setting up wcn399x\n[ 1.080000] Bluetooth: hci0: Frame reassembly failed (-84)\n[ 1.100000] Bluetooth: hci0: QCA Product ID   :0x0000000a\n[ 2.0] Bluetooth: hci0: QCA setup on UART is completed\n' > "$T/d3"
python3 -I "$PARSE" dmesg "$T/d3" | grep -q '^bad=' && no "dmesg: admitted -84 flagged" || ok "dmesg: one -84 at +80 ms before Product ID is admitted"
printf '[ 5.0] Bluetooth: hci0: Retry BT power ON:0\n' > "$T/d4"
python3 -I "$PARSE" dmesg "$T/d4" | grep -q '^bad=' && ok "dmesg: Retry BT power ON is a stop" || no "dmesg: retry admitted"
printf '[ 5.0] init: starting service vendor.thermal-example...\n[ 6.0] qcom-rpm-smd-regulator: l16a: over-current\n' > "$T/d5"
python3 -I "$PARSE" dmesg "$T/d5" | grep -c '^bad=' | grep -qx 1 && ok "dmesg: rail error is a stop, init lines are not" || no "dmesg: rail/init classification"
printf 'x 01:EE:DD:CC:BB:AA y\n' > "$T/leak1"; python3 -I "$PARSE" leak "$T/bdaddr.txt" "$T/leak1" > /dev/null && no "leak: reversed private address missed" || ok "leak: reversed private address found"
printf 'x 01EEDDCCBBAA y\n' > "$T/leak2"; python3 -I "$PARSE" leak "$T/bdaddr.txt" "$T/leak2" > /dev/null && no "leak: compact reversed private address missed" || ok "leak: compact reversed private address found"

# --- refusals (nothing sent to the phone)
ATT=no sc not-attended '' 2; [ ! -s "$D/calls.log" ] && ok "not attended: no phone command" || no "not attended: phone was contacted"
sc realdata realdata 2; hasnt calls.log 'settings put' "real /data: no settings write"; hasnt calls.log 'am start' "real /data: no pairing UI"
d=$T/badpin; mkdir -p "$d"; A9_BT_ATTENDED=yes "$S" run --run "$d/r" --peer-name x --pin 12 > /dev/null 2>&1; [ $? = 2 ] && ok "bad --pin refused" || no "bad --pin accepted"
d=$T/quote; mkdir -p "$d"; A9_BT_ATTENDED=yes "$S" run --run "$d/r" --peer-name "a'b" > /dev/null 2>&1; [ $? = 2 ] && ok "quote in --peer-name refused" || no "quote accepted"

# --- happy paths
sc hid '' 0
has run/verdict.txt 'verdict: PASS kind=hid' "hid: PASS verdict"
has out.txt 'OPERATOR: type 123456 on the KEYBOARD' "hid: passkey relayed to the operator"
has out.txt 'BOND: bonded devices 0 -> 1' "hid: bond recorded"
has out.txt 'EV_KEY 10' "hid: getevent EV_KEY counted"
has out.txt 'UNBOND: bonded devices 1 -> 0' "hid: forgotten through the UI"
has out.txt 'BT disabled: STATE_OFF' "hid: BT off at the end"
has out.txt 'Android address hash = private input hash' "hid: address hash compared, value withheld"
has calls.log 'cmd bluetooth_manager disable' "hid: disable sent"
sc audio audio 0
has run/verdict.txt 'verdict: PASS kind=audio' "audio: PASS verdict"
has out.txt 'A2DP connected 1' "audio: A2DP connection state recorded"
hasnt calls.log 'keyevent' "audio: no key events (no media keys)"
hasnt calls.log 'media_session dispatch' "audio: no media dispatch"
hasnt calls.log 'btsnoop' "audio: btsnoop never touched"
hasnt calls.log 'getevent' "audio: no getevent for an audio sink"
sc pin pin 0 --pin 0000
has calls.log 'input text 0000' "pin: --pin entered in the PIN dialog"

# --- stop conditions (exit 4, disable + gate close, return path printed)
for f in storm late84 crash pid hot rail inode regulator modem; do
  sc "stop-$f" "$f" 4
  has calls.log 'setprop vendor.a9.bt.ready 0' "stop-$f: HAL gate closed"
  has out.txt 'RETURN PATH' "stop-$f: return path printed"
  has run/verdict.txt 'verdict: STOP' "stop-$f: STOP verdict"
done
D=$T/stop-storm; has out.txt 're-setup storm' "storm: classified as re-setup storm"
D=$T/stop-late84; has out.txt 'framing error outside' "late84: classified as framing error"
A9_BT_DEADLINE=-1 sc deadline '' 4; has out.txt 'session deadline' "deadline: stop on a passed session deadline"

# --- failures that are not stops (exit 1, post still runs)
sc notfound notfound 1; has run/verdict.txt 'peer not found' "notfound: FAIL verdict"; has out.txt 'BT disabled: STATE_OFF' "notfound: post ran"
sc noforget noforget 1; has out.txt 'UNPAIR UI FAILED: the bond lives only in RAM' "noforget: RAM-only note"
sc noevents noevents 1; has run/verdict.txt 'no input events' "noevents: FAIL verdict"
sc wrongkind audio 1 --kind hid; has run/verdict.txt 'profile hid not connected' "kind mismatch: FAIL"

# --- review 55 B1: no input unless a Settings Bluetooth window has focus; Settings nodes only; tap cap
taps() { grep -c '^input tap\|^input text' "$D/calls.log"; }
sc fgother fgother 1; [ "$(taps)" = 0 ] && ok "fgother: zero taps (Settings never came to the front)" || no "fgother: $(taps) taps"
has run/verdict.txt 'peer not found' "fgother: launcher node with the peer name ignored"
sc fgflip fgflip 1; [ "$(taps)" = 0 ] && ok "fgflip: zero taps (focus changed after the dump)" || no "fgflip: $(taps) taps"
has run/verdict.txt 'foreground not Settings/BT' "fgflip: FAIL foreground not Settings/BT"
[ "$(grep -c '^input keyevent BACK' "$D/calls.log")" = 1 ] && ok "fgflip: exactly one BACK" || no "fgflip: BACK count"
hasnt calls.log 'keyevent [^B]' "fgflip: no other keyevent"
has out.txt 'BT disabled: STATE_OFF' "fgflip: post ran"
sc otherpkg otherpkg 1; [ "$(taps)" = 0 ] && ok "otherpkg: zero taps (peer name only in another package)" || no "otherpkg: $(taps) taps"
sc stuck audio,stuck 1 --kind audio; [ "$(taps)" = 12 ] && ok "stuck: input capped at 12" || no "stuck: $(taps) inputs"
has run/verdict.txt 'tap cap 12 reached' "stuck: FAIL tap cap"
D=$T/hid; n=$(grep -c '^dumpsys window' "$D/calls.log"); t=$(taps); [ "$n" -ge "$t" ] && [ "$t" -gt 0 ] && ok "hid: a focus read for every input ($n reads, $t inputs)" || no "hid: focus reads $n < inputs $t"
awk '/^dumpsys window/{f=1;next} /^input (tap|text)/{if(!f)bad=1; f=0} END{exit bad}' "$D/calls.log" && ok "hid: every input directly preceded by a focus read" || no "hid: an input without a fresh focus read"
D=$T/hid; [ "$(grep -c '^am start -a android.settings.BLUETOOTH_SETTINGS' "$D/calls.log")" -ge 1 ] && ok "hid: unpair starts from a fresh Settings launch" || no "hid: no fresh launch"
sc noforget2 noforget 1; [ "$(grep -c '^am start -a android.settings.BLUETOOTH_SETTINGS' "$D/calls.log")" = 3 ] && ok "noforget: a fresh launch before each of 3 unpair tries" || no "noforget: launches $(grep -c '^am start -a android.settings.BLUETOOTH_SETTINGS' "$D/calls.log")"
# --- review 55 S1/S2
sc nopid nopid 4; has out.txt 'not all four pids readable' "nopid: STOP when a pid is unreadable"; [ "$(taps)" = 0 ] && ok "nopid: zero taps" || no "nopid: taps"
OPF=$T/answer-no sc noyes '' 1; has run/verdict.txt 'peer not confirmed' "noyes: FAIL without a typed yes"; [ "$(taps)" = 0 ] && ok "noyes: zero taps" || no "noyes: taps"
hasnt calls.log 'settings put' "noyes: no settings write before the confirmation"

echo "test-bt-pair: $pass passed, $fail failed"
[ "$fail" = 0 ]
