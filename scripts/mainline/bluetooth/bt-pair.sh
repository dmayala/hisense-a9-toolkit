#!/usr/bin/env bash
# bt-pair.sh -- host side of BT phase 2 (runs/bt-phase2-plan.md): ONE supervised
# pairing with one user-supplied device on the combined mainline image, while
# `android-boot.sh --write ... --hold` keeps the phone on slot B.
# ATTENDED only. Written and tested offline against a fake adb
# (tests/mainline-bt/test-bt-pair.sh); never run against the phone by its author.
#
#   A9_BT_ATTENDED=yes bt-pair.sh run --peer-name "<name the device advertises>" [--kind hid|audio|auto] [--pin NNNN]
#   bt-pair.sh pre|pair|profile|unpair|post|stopcheck --run <dir> [...]   (one phase; same --run dir)
#   bt-pair.sh leak --run <dir>          address check of everything outside <dir>/private
#
# Value-free: raw phone output (addresses, neighbour names) goes to <run>/private
# (0700/0600) only; the terminal and <run>/log.txt get counts, states and redacted
# text. The BD address and peer addresses are never printed. All phone commands
# go through the loopback root shell (adb shell 'echo CMD | nc 127.0.0.1 2324'),
# bounded by timeouts; no uid-shell writes (review 43 1.4).
# Never: audio playback or media keys, btsnoop, Wi-Fi, gate opening, any write
# outside RAM /data and BT settings, any partition access.
# UI input (review 55 B1): before EVERY `input tap`/`input text` the focused window is
# read and must be a com.android.settings Bluetooth window; nodes are taken from the
# Settings package only, inside the screen; at most A9_BT_MAX_TAPS (12) inputs per run;
# one `input keyevent BACK` per run may dismiss a stray window, no other keyevents.
# Exit: 0 PASS; 1 FAIL (no stop condition; continue to post/return); 2 refused
# (nothing done); 4 STOP (stop condition: BT disabled, HAL gate closed; now run
# the return path printed in the log); 5 LEAK in the run dir (not a phone stop).
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
ADB=${A9_BT_ADB:-adb}
P="python3 -I $HERE/bt-pair-parse.py"
SLEEP=${A9_BT_SLEEP:-5}          # seconds between polls (tests: 0)
STEP=5                            # nominal poll period: a T-second wait = T/STEP polls
PRIV_ADDR=${A9_BT_PRIVATE_ADDR:-$HOME/A9-build/mainline/sol-bt/private/bdaddr.txt}
DEADLINE=${A9_BT_DEADLINE:-1800}  # whole BT session, from `pre`
TMAX=${A9_BT_TEMP_MAX:-60000}     # any thermal zone (milli-C)
BMAX=${A9_BT_BATT_MAX:-42000}     # battery zone
OPIN=${A9_BT_OPERATOR_IN:-/dev/tty}
OPWAIT=${A9_BT_OPERATOR_WAIT:-300}
UIX=/data/local/tmp/a9bt-ui.xml
MAXTAPS=${A9_BT_MAX_TAPS:-12}
PHASE=${1:-}; [ $# -gt 0 ] && shift
RUN="" NAME="" KIND=auto PIN=""
while [ $# -gt 0 ]; do
  case "$1" in
    --run) RUN=${2:-}; shift ;;
    --peer-name) NAME=${2:-}; shift ;;
    --kind) KIND=${2:-}; shift ;;
    --pin) PIN=${2:-}; shift ;;
    *) sed -n '2,24p' "$0"; exit 2 ;;
  esac; shift
done
case "$PHASE" in run|pre|pair|profile|unpair|post|stopcheck|leak) ;; *) sed -n '2,24p' "$0"; exit 2 ;; esac
case "$KIND" in hid|audio|auto) ;; *) echo "refused: --kind hid|audio|auto"; exit 2 ;; esac
case "$PIN" in ''|[0-9][0-9][0-9][0-9]|[0-9][0-9][0-9][0-9][0-9][0-9]) ;; *) echo "refused: --pin must be 4 or 6 digits"; exit 2 ;; esac
case "$NAME" in *"'"*|*'"'*|*$'\n'*) echo "refused: quotes or newlines in --peer-name"; exit 2 ;; esac
if [ "$PHASE" = run ] && [ -z "$RUN" ]; then
  RUN=${A9_BT_RUNS:-$REPO/runs}/$(date -u +%Y%m%dT%H%M%SZ)-bt-pair
fi
[ -n "$RUN" ] || { echo "refused: --run <dir> (the dir of an earlier 'pre')"; exit 2; }
umask 077
mkdir -p "$RUN/private" && chmod 700 "$RUN/private" || { echo "refused: cannot create $RUN/private"; exit 2; }
LOG=$RUN/log.txt
redact() { $P redact; }
say() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | redact | tee -a "$LOG"; }
op() { say "OPERATOR: $*"; }
# root channel; refuses ' and newlines (they would escape the transport)
R() {
  local c=$1 t=${2:-30}
  case "$c" in *"'"*|*$'\n'*) echo "R-REFUSED"; return 9 ;; esac
  timeout "$((t + 5))" "$ADB" shell "echo '$c' | nc -w $t 127.0.0.1 2324" </dev/null 2>&1 | tr -d '\r'
}
keep() { cat > "$RUN/private/$1"; }       # raw capture, private
sget() { local v; v=$(sed -n "s/^$1=//p" "$RUN/state" 2>/dev/null | tail -n1); echo "${v:-${2:-}}"; }
sset() { echo "$1=$2" >> "$RUN/state"; }
kv() { sed -n "s/^$1=//p" | head -n1; }   # value of key from parser output
polls() { local n=$(( $1 / STEP )); [ "$n" -ge 1 ] || n=1; echo "$n"; }
nap() { [ "$SLEEP" = 0 ] || sleep "$SLEEP"; }
# capture file names: a counter in a file, so it also counts inside $(...)
next() { local n; n=$(( $(cat "$RUN/private/.n" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$RUN/private/.n"; printf '%03d-%s\n' "$n" "$1"; }

verdict() { echo "verdict: $1 ${2:-}" | redact > "$RUN/verdict.txt"; say "verdict: $1 ${2:-}"; }
leakcheck() {
  local files=()
  while IFS= read -r f; do files+=("$f"); done < <(find "$RUN" -path "$RUN/private" -prune -o -type f -print)
  $P leak "$PRIV_ADDR" "${files[@]}" > "$RUN/private/leak.txt" 2>&1; local rc=$?
  tail -n1 "$RUN/private/leak.txt" | sed 's/^/leakcheck: /'
  return $rc
}
stop_actions() {
  say "stop actions: BT disable (bounded 20 s), HAL gate closed (vendor.a9.bt.ready 0); no retry"
  R 'cmd bluetooth_manager disable' 20 > /dev/null
  R 'setprop vendor.a9.bt.ready 0' 10 > /dev/null
  say "RETURN PATH: end the android-boot.sh hold (kill -TERM <its pid> or Ctrl-C in its terminal), then"
  say "  scripts/mainline/android/android-boot.sh --restore ; scripts/a9-health.sh   (slot a booted = done)"
  say "  if adb is gone: the vendor dead-man sends the phone to fastboot (<= 1200 s after the last stay refresh); then --restore"
}
stop() {
  say "STOP: $*"; stop_actions; verdict STOP "$*"; leakcheck > /dev/null; exit 4
}
now() { echo $(( $(date +%s) + ${A9_BT_CLOCK_SKEW:-0} )); }
deadline_ok() {
  local t0; t0=$(sget t0); [ -n "$t0" ] || return 0
  [ $(( $(now) - t0 )) -le "$DEADLINE" ] || stop "session deadline ${DEADLINE} s passed"
}
need_attended() { [ "${A9_BT_ATTENDED:-}" = yes ] || { echo "refused: attended only (A9_BT_ATTENDED=yes, operator at the phone and the peer)"; exit 2; }; }

# ---------------------------------------------------------------- snapshots
btdump() {   # -> parser kv on stdout; raw kept
  local f; f=$(next btdump.txt); R 'dumpsys bluetooth_manager' 40 | keep "$f"
  $P btdump "$RUN/private/$f"
}
fw_identity() {   # review 43 1.4: inodes stable, 22 OK, 22 entries
  local f a b c ok n
  f=$(next fw-id.txt)
  { R 'ls -lid /data/local/tmp/a9fw /data/local/tmp/a9fw/qca'; } | keep "$f"
  a=$(awk '{print $1}' "$RUN/private/$f" | tr '\n' ' ')
  b=$(R 'cd /data/local/tmp/a9fw/qca && sha256sum -c /vendor/etc/bluetooth/firmware.sha256' 30)
  ok=$(printf '%s\n' "$b" | grep -c ': OK$'); c=$(printf '%s\n' "$b" | grep -vc ': OK$\|^$')
  n=$(R 'ls -A /data/local/tmp/a9fw/qca | wc -l' | tr -d ' ' | tail -n1)
  if [ -z "$(sget inodes)" ]; then sset inodes "$a"; fi
  say "firmware identity: inodes $( [ "$a" = "$(sget inodes)" ] && echo stable || echo CHANGED ) ok=$ok other=$c entries=$n"
  [ "$a" = "$(sget inodes)" ] && [ "$ok" = 22 ] && [ "$c" = 0 ] && [ "$n" = 22 ] \
    || stop "firmware staging identity changed or incomplete (review 43 1.4)"
}
# "name=pid" x4; the HAL name is longer than comm (15): fall back to its ps ARGS path
pids() {
  R 'for p in com.android.bluetooth android.hardware.bluetooth-service.default audioserver system_server; do q=$(pidof -s $p); [ -n "$q" ] || { set -- $(ps -A -o PID,ARGS | grep -F "/$p" | grep -v grep | head -n1); q=${1:-}; }; echo $p=$q; done' \
    | grep '=' | tr '\n' ' ' | sed 's/ $//'
}
pids_ok() { [ "$(printf '%s\n' "$1" | tr ' ' '\n' | grep -cE '^[a-z_.-]+=[0-9]+$')" = 4 ]; }

stopcheck() {   # every stop condition the host can see; STOP on the first
  deadline_ok
  local f d bad setups allowed out t z cs st gates rp ps cur crashed
  [ "$(timeout 10 "$ADB" get-state 2>/dev/null)" = device ] || stop "adb get-state is not device"
  [ "$(R 'getprop vendor.a9.bt.ready' | tail -n1)" = 1 ] || stop "vendor.a9.bt.ready is not 1 (setup or HAL gate closed)"
  f=$(next dmesg.txt); R 'dmesg' 40 | keep "$f"
  d=$($P dmesg "$RUN/private/$f")
  bad=$(printf '%s\n' "$d" | sed -n 's/^bad=//p')
  setups=$(printf '%s\n' "$d" | kv setups)
  [ -n "$(sget setups0)" ] || sset setups0 "$setups"
  allowed=$(( $(sget setups0 0) + $(sget enables 0) ))
  [ -z "$bad" ] || { printf '%s\n' "$bad" | head -n5 | while IFS= read -r l; do say "  kernel: $l"; done; stop "kernel BT/rail/serial/restart/modem line (see log)"; }
  [ "$setups" -le "$allowed" ] || stop "controller re-setup storm: $setups power-on sequences, $allowed allowed (HAL reopened without an enable)"
  t=$(R 'cat /sys/class/thermal/thermal_zone*/temp' | grep -E '^-?[0-9]+$' | sort -n | tail -n1)
  z=$(R 'for z in /sys/class/thermal/thermal_zone*; do echo $(cat $z/type) $(cat $z/temp); done' | awk '$1 ~ /batt/ {print $2}' | sort -n | tail -n1)
  cs=$(R 'cat /sys/class/thermal/cooling_device*/cur_state' | grep -E '^[0-9]+$' | sort -n | tail -n1)
  [ -n "$t" ] && [ "$t" -lt "$TMAX" ] || stop "thermal: hottest zone ${t:-unreadable} >= $TMAX"
  [ -z "$z" ] || [ "$z" -lt "$BMAX" ] || stop "thermal: battery $z >= $BMAX"
  [ "${cs:-0}" = 0 ] || stop "thermal: a cooling device is active (state $cs)"
  st=$(R 'cat /sys/class/drm/card*-*/eink/status' | tr ' ' '\n' | awk 'p=="rail_fault"{print; exit}{p=$0}')
  gates=$(R 'cat /sys/module/a9_eink/parameters/enable' | tail -n1)
  [ "${st:-0}" = 0 ] || stop "E Ink rail_fault $st"
  [ "$gates" = N ] || [ -z "$gates" ] || stop "E Ink gate a9_eink.enable=$gates (must stay closed)"
  rp=$(R 'cat /sys/class/remoteproc/remoteproc*/state 2>/dev/null' | grep -c '^running$')
  ps=$(R 'ps -A -o NAME' | grep -cE '^(rmtfs|tqftpserv|pd-mapper)')
  [ "$rp" = 0 ] && [ "$ps" = 0 ] || stop "modem/storage service activity (remoteproc running $rp, servers $ps)"
  cur=$(pids)
  pids_ok "$cur" || stop "restart check: not all four pids readable (BT app, BT HAL, audioserver, system_server): $(printf '%s\n' "$cur" | tr ' ' '\n' | grep -E '=$' | tr '\n' ' ')"
  if [ -z "$(sget pids)" ]; then sset pids "$cur"; fi
  [ "$cur" = "$(sget pids)" ] || stop "restart: BT app / BT HAL / audioserver / system_server pid changed"
  crashed=$(btdump | kv crashed)
  [ -n "$(sget crashed0)" ] || sset crashed0 "$crashed"
  [ "$crashed" = "$(sget crashed0)" ] || stop "Bluetooth stack crash count $(sget crashed0) -> $crashed"
  say "stopcheck OK: setups $setups/$allowed, hottest ${t} mC, battery ${z:-?}, cooling 0, rail_fault 0, gates closed, no modem, pids stable, crashes $crashed"
}

# --------------------------------------------------------------------- UI
ui() {   # dump the view tree (raw private); echo the private file name
  local f; f=$(next ui.xml)
  R "uiautomator dump $UIX >/dev/null 2>&1; cat $UIX" 40 | sed -n '/<?xml/,$p' | keep "$f"
  echo "$f"
}
focus_ok() {   # immediately before every input: the focused window must be a Settings BT window
  local f r pkg win
  f=$(next focus.txt); R 'dumpsys window | grep -E "mCurrentFocus|mFocusedApp"' 15 | keep "$f"
  r=$($P focus "$RUN/private/$f"); pkg=$(printf '%s\n' "$r" | kv focus_pkg); win=$(printf '%s\n' "$r" | kv focus_win)
  echo "$pkg" >> "$RUN/private/focus-pkgs.txt"
  [ "$(printf '%s\n' "$r" | kv focus_ok)" = 1 ] && return 0
  say "foreground is not a Settings Bluetooth window (package $pkg, window $win): no input"
  if [ "$(sget back 0)" = 0 ]; then
    sset back 1; say "one BACK to dismiss the stray window (the only keyevent this run sends)"
    R 'input keyevent BACK' 10 > /dev/null
  fi
  sset fgbad 1; sset greason "foreground not Settings/BT"; return 1
}
guard() {   # tap cap + focus, before every input tap / input text
  local n; n=$(sget taps 0)
  [ "$n" -lt "$MAXTAPS" ] || { say "tap cap $MAXTAPS reached: no more input"; sset fgbad 1; sset greason "tap cap $MAXTAPS reached"; return 1; }
  focus_ok || return 1
  sset taps $((n + 1))
}
tapxy() { local xy=$1; [ "$xy" != none ] && [ -n "$xy" ] || return 1; guard || return 2; R "input tap $xy" 10 > /dev/null; }
textin() { guard || return 2; R "input text $1" 10 > /dev/null; }
blocked() { [ "$(sget fgbad 0)" = 1 ] || return 1; verdict FAIL "$(sget greason)"; return 0; }
due() { [ "$(date +%s)" -ge "$1" ]; }   # wall-clock end of a wait (review 55 S3)

# ----------------------------------------------------------------- phases
ph_pre() {
  [ -z "$(sget t0)" ] && sset t0 "$(now)"
  say "== pre (run dir $RUN)"
  [ "$(timeout 10 "$ADB" get-state 2>/dev/null)" = device ] || { say "refused: no adb device"; exit 2; }
  [ "$(R 'id -u' 10 | tail -n1)" = 0 ] || { say "refused: no loopback root shell on 127.0.0.1:2324"; exit 2; }
  local slot boot bt ready hal m
  slot=$(R 'getprop ro.boot.slot_suffix' | tail -n1); boot=$(R 'getprop sys.boot_completed' | tail -n1)
  bt=$(R 'getprop ro.vendor.a9.bluetooth' | tail -n1); ready=$(R 'getprop vendor.a9.bt.ready' | tail -n1)
  hal=$(R 'getprop init.svc.vendor.a9-bt-hal' | tail -n1)
  say "slot $slot boot_completed $boot ro.vendor.a9.bluetooth $bt ready $ready hal $hal"
  [ "$slot" = _b ] && [ "$boot" = 1 ] && [ "$bt" = 1 ] || { say "refused: not the mainline BT image on slot b with boot completed"; exit 2; }
  # (3) the bond lands in RAM: /data must be the ram0 disk, no userdata mounted
  R 'cat /proc/mounts' | keep mounts.txt
  m=$(awk '$2=="/data"{print $1}' "$RUN/private/mounts.txt" | tail -n1)
  if [ "$m" != /dev/block/ram0 ] || grep -qiE 'userdata|dm-default-key' "$RUN/private/mounts.txt"; then
    say "refused: /data is '${m:-none}', not /dev/block/ram0 (or userdata mounted); nothing done"; exit 2
  fi
  say "/data = /dev/block/ram0 (RAM disk, discarded at restore); no userdata mount"
  R 'cat /sys/power/wake_lock' | grep -qw a9ml_test_awake || stop "test wakelock a9ml_test_awake not held (suspend would drop USB)"
  [ "$ready" = 1 ] && [ "$hal" = running ] || stop "BT setup verdict not ready=1 / HAL not running"
  [ "$(R 'ls /sys/class/bluetooth' | tr -s ' \n' ' ' | sed 's/ $//')" = hci0 ] || stop "not exactly one controller hci0"
  R 'readlink -f /sys/class/bluetooth/hci0' | grep -q '/4a8c000.serial/' || stop "hci0 is not the SE3 UART controller"
  R 'ls -l /dev/uhid' | grep -q '^c' && say "/dev/uhid present (HID host path)" || say "WARNING: /dev/uhid absent: HID cannot work (record, not a stop)"
  fw_identity
  local d; d=$(btdump)
  say "bluetooth_manager: state $(printf '%s\n' "$d" | kv state) bonded $(printf '%s\n' "$d" | kv bonded) crashed $(printf '%s\n' "$d" | kv crashed) profiles $(printf '%s\n' "$d" | kv profiles)"
  case ",$(printf '%s\n' "$d" | kv profiles)," in *,HID_HOST,*A2DP,*|*,A2DP,*HID_HOST,*) ;; *) say "WARNING: HID_HOST/A2DP not both in Enabled Profile Services (record; the profile check will tell)" ;; esac
  [ "$(printf '%s\n' "$d" | kv bonded)" = 0 ] || [ "$(printf '%s\n' "$d" | kv state)" != ON ] || stop "bonded list not empty at start on RAM /data"
  R 'cmd bluetooth_manager help' 15 | keep cmd-help.txt
  say "cmd bluetooth_manager help: $(grep -ciE 'bond|pair' "$RUN/private/cmd-help.txt") lines mention bond/pair (UI route is used regardless)"
  # Android-side address = private input, hashes only (run-1 plan 5.4)
  if [ -r "$PRIV_ADDR" ]; then
    local hp hh
    hp=$(R 'settings get secure bluetooth_address | tr -d "\n" | tr a-f A-F | sha256sum' | awk '{print $1}' | tail -n1)
    hh=$(tr -d '\n' < "$PRIV_ADDR" | tr a-f A-F | sha256sum | awk '{print $1}')
    [ "$hp" = "$hh" ] && say "Android address hash = private input hash (values withheld)" || stop "Android BD address hash differs from the private input"
  else say "private address file not readable here: Android hash check SKIPPED"; fi
  R 'dumpsys input' 30 | keep input-pre.txt
  sset btin0 "$($P input "$RUN/private/input-pre.txt" | kv bt_inputs)"
  stopcheck
  say "pre: PASS"
}

ph_pair() {
  need_attended; deadline_ok
  [ -n "$NAME" ] || { echo "refused: --peer-name"; exit 2; }
  say "== pair (peer name given by the operator; not logged)"
  blocked && return 1
  op "confirm the peer is NOT paired with the daily phone (slot A), or that you accept re-pairing it there: type yes + Enter"
  local ans=""; read -r -t "$OPWAIT" ans < "$OPIN"
  [ "$ans" = yes ] || { say "operator did not type yes: no pairing"; verdict FAIL "peer not confirmed as unpaired from slot A"; return 1; }
  say "operator confirmed: peer not paired with the daily phone, or re-pairing on slot A accepted"
  # RAM-only settings writes, after re-proving /data is ram0
  [ "$(R 'cat /proc/mounts' | awk '$2=="/data"{print $1}' | tail -n1)" = /dev/block/ram0 ] || stop "/data is no longer ram0"
  R 'settings put system sound_effects_enabled 0' > /dev/null
  R 'cmd media_session volume --stream 3 --set 0' 15 > /dev/null
  R 'settings put global device_provisioned 1' > /dev/null
  R 'settings put secure user_setup_complete 1' > /dev/null
  local st i f xy dlg pk shown="" end
  st=$(btdump | kv state)
  if [ "$st" != ON ]; then
    say "BT state $st: one enable"
    sset enables $(( $(sget enables 0) + 1 ))
    R 'cmd bluetooth_manager enable' 20 > /dev/null
    R 'cmd bluetooth_manager wait-for-state:STATE_ON' 65 | keep enable-wait.txt
    [ "$(btdump | kv state)" = ON ] || stop "enable did not reach STATE_ON within 60 s"
    sset pids ""   # our own enable starts the BT app: new pid baseline (review 55 S1)
  fi
  stopcheck
  op "put the peer device in PAIRING MODE now (within 1 m of the phone), then press Enter here (limit ${OPWAIT} s)"
  read -r -t "$OPWAIT" _ < "$OPIN" || { say "operator did not confirm pairing mode in ${OPWAIT} s"; verdict FAIL "no pairing mode"; return 1; }
  R 'am start -a android.settings.BLUETOOTH_PAIRING_SETTINGS' 20 | keep am-pairing.txt
  if grep -qiE 'error|unable' "$RUN/private/am-pairing.txt"; then
    R 'am start -a android.settings.BLUETOOTH_SETTINGS' 20 > /dev/null
    f=$(ui); tapxy "$($P ui "$RUN/private/$f" text 'pair new device' | kv tap)"; i=$?
    blocked && return 1
    [ "$i" = 0 ] || stop "no pairing screen (neither action nor 'Pair new device')"
  fi
  xy=none; end=$(( $(date +%s) + 120 ))
  for i in $(seq 1 "$(polls 120)"); do
    nap; due "$end" && break; f=$(ui); xy=$($P ui "$RUN/private/$f" find "$NAME" | kv tap)
    [ "$xy" != none ] && break
    [ "$i" = "$(polls 60)" ] && { say "peer not listed after ~60 s: re-open the pairing screen once (new discovery)"; R 'am start -a android.settings.BLUETOOTH_PAIRING_SETTINGS' 20 > /dev/null; }
    [ $((i % 6)) = 0 ] && stopcheck
  done
  [ "$xy" != none ] || { say "peer not found in discovery within ~120 s"; verdict FAIL "peer not found"; return 1; }
  say "peer listed; tapping it (createBond through Settings)"
  tapxy "$xy"; blocked && return 1
  local bonded=0; end=$(( $(date +%s) + 120 ))
  for i in $(seq 1 "$(polls 120)"); do
    nap; due "$end" && break
    [ "$(btdump | kv bonded)" = 1 ] && { bonded=1; break; }
    f=$(ui); dlg=$($P ui "$RUN/private/$f" dialog)
    case "$(printf '%s\n' "$dlg" | kv dialog)" in
      consent) say "pairing dialog: consent -> Pair"; tapxy "$(printf '%s\n' "$dlg" | kv tap)" ;;
      access) say "profile access request -> Don't allow"; tapxy "$(printf '%s\n' "$dlg" | kv tap)" ;;
      passkey) pk=$(printf '%s\n' "$dlg" | kv passkey)
               [ "$pk" = "$shown" ] || { op "type $pk on the KEYBOARD, then its Enter/Return key"; shown=$pk; } ;;
      pin) if [ -n "$PIN" ]; then say "PIN dialog: entering the --pin value"; tapxy "$(printf '%s\n' "$dlg" | kv edit)"
             blocked && return 1; textin "$PIN"; blocked && return 1; tapxy "$(printf '%s\n' "$dlg" | kv tap)"
           else say "PIN dialog and no --pin: cannot pair"; verdict FAIL "PIN required"; return 1; fi ;;
    esac
    blocked && return 1
    [ $((i % 6)) = 0 ] && stopcheck
  done
  [ "$bonded" = 1 ] || { say "no bond within ~120 s of the tap"; stopcheck; verdict FAIL "bond timeout"; return 1; }
  sset bonded 1
  say "BOND: bonded devices 0 -> 1 (dumpsys; address withheld)"
  fw_identity; stopcheck
  say "pair: PASS"
}

ph_profile() {
  need_attended; deadline_ok
  say "== profile (expected kind: $KIND)"
  [ "$(btdump | kv bonded)" = 1 ] || { say "no bond in this run: profile check skipped"; verdict FAIL "no bond for the profile check"; return 1; }
  local i a2=0 hid=0 b0 path ev k r fi fe
  b0=$(sget btin0 0); local end=$(( $(date +%s) + 60 ))
  for i in $(seq 1 "$(polls 60)"); do
    nap; due "$end" && [ -n "$fi" ] && break
    fi=$(next input.txt); R 'dumpsys input' 30 | keep "$fi"
    hid=$($P input "$RUN/private/$fi" | kv bt_inputs); hid=${hid:-0}
    a2=$(btdump | kv a2dp_connected); case "$a2" in ''|*[!0-9]*) a2=0 ;; esac
    { [ "$hid" -gt "$b0" ] || [ "$a2" -ge 1 ]; } && break
  done
  if [ "$hid" -gt "$b0" ]; then k=hid; elif [ "$a2" -ge 1 ]; then k=audio; else k=none; fi
  say "profile state: bluetooth input devices $b0 -> $hid, A2DP connected $a2, HFP connected $(btdump | kv hfp_connected)"
  sset kind "$k"
  [ "$KIND" = auto ] || [ "$KIND" = "$k" ] || { say "expected $KIND, saw $k"; stopcheck; verdict FAIL "profile $KIND not connected (saw $k)"; return 1; }
  case "$k" in
    hid)
      path=$($P input "$RUN/private/$fi" | sed -n 's/^bt_input=//p' | tail -n1)
      case "$path" in /dev/input/event[0-9]*) ;; *) stop "unexpected input path from dumpsys" ;; esac
      op "within 20 s: press SHIFT 5 times on the keyboard (mouse: move it). Do NOT type text."
      fe=$(next getevent.txt); R "timeout 20 getevent -c 60 $path" 30 | keep "$fe"
      ev=$($P getevent "$RUN/private/$fe")
      say "getevent on the BT input node: EV_KEY $(printf '%s\n' "$ev" | kv ev_key) EV_REL $(printf '%s\n' "$ev" | kv ev_rel) EV_ABS $(printf '%s\n' "$ev" | kv ev_abs) (codes not logged)"
      r=$(( $(printf '%s\n' "$ev" | kv ev_key) + $(printf '%s\n' "$ev" | kv ev_rel) ))
      stopcheck
      [ "$r" -ge 2 ] || { verdict FAIL "HID connected but no input events"; return 1; } ;;
    audio)
      say "A2DP connected: state recorded only; NO playback, no media keys (audio stack is not up)"
      stopcheck ;;
    none) stopcheck; verdict FAIL "bonded but no HID/A2DP connection in 60 s"; return 1 ;;
  esac
  say "profile: PASS ($k)"
}

ph_unpair() {
  need_attended
  say "== unpair (Settings > device details > Forget)"
  [ "$(btdump | kv bonded)" = 0 ] && { say "bonded devices 0: nothing to forget"; return 0; }
  [ -n "$NAME" ] || { echo "refused: --peer-name"; exit 2; }
  local f i ok=0 r end
  if [ "$(sget fgbad 0)" = 1 ]; then
    say "UI input is blocked in this run ($(sget greason)): no Forget taps"
  else
    for i in 1 2 3; do
      R 'am start -a android.settings.BLUETOOTH_SETTINGS' 20 > /dev/null   # fresh launch before every try
      nap; f=$(ui)
      tapxy "$($P ui "$RUN/private/$f" gear "$NAME" | kv tap)"; r=$?; [ "$r" = 2 ] && break; [ "$r" = 0 ] || continue
      nap; f=$(ui); tapxy "$($P ui "$RUN/private/$f" text 'forget' | kv tap)"; r=$?; [ "$r" = 2 ] && break; [ "$r" = 0 ] || continue
      nap; f=$(ui); tapxy "$($P ui "$RUN/private/$f" text 'forget device|forget' | kv tap)"; r=$?; [ "$r" = 2 ] && break; [ "$r" = 0 ] || continue
      break
    done
  fi
  end=$(( $(date +%s) + 60 ))
  for i in $(seq 1 "$(polls 60)"); do
    nap; [ "$(btdump | kv bonded)" = 0 ] && { ok=1; break; }; due "$end" && break
  done
  if [ "$ok" = 1 ]; then sset bonded 0; say "UNBOND: bonded devices 1 -> 0"
  else say "UNPAIR UI FAILED: the bond lives only in RAM /data (ram0) and is discarded at --restore"; fi
  op "remove this phone from the PEER's own pairing list now if it has one (its manual), or power the peer off; press Enter"
  read -r -t "$OPWAIT" _ < "$OPIN" || say "operator did not confirm the peer-side cleanup"
  stopcheck
  [ "$ok" = 1 ] || { grep -q '^verdict: FAIL' "$RUN/verdict.txt" 2>/dev/null || verdict FAIL "unpair through the UI failed (RAM-only bond)"; return 1; }
  say "unpair: PASS"
}

ph_post() {
  say "== post"
  local i st
  stopcheck
  fw_identity
  R 'cmd bluetooth_manager disable' 20 > /dev/null
  R 'cmd bluetooth_manager wait-for-state:STATE_OFF' 65 | keep disable-wait.txt
  local end=$(( $(date +%s) + 30 ))
  for i in $(seq 1 "$(polls 30)"); do st=$(btdump | kv state); [ "$st" = OFF ] && break; due "$end" && break; nap; done
  [ "$st" = OFF ] || stop "disable did not reach STATE_OFF"
  say "BT disabled: STATE_OFF"
  say "post: PASS"
}

case "$PHASE" in
  pre) ph_pre; rc=$? ;;
  pair) ph_pair; rc=$? ;;
  profile) ph_profile; rc=$? ;;
  unpair) ph_unpair; rc=$? ;;
  post) ph_post; rc=$? ;;
  stopcheck) stopcheck; rc=0 ;;
  leak) leakcheck; exit $? ;;
  run)
    need_attended
    [ -n "$NAME" ] || { echo "refused: --peer-name"; exit 2; }
    ph_pre
    rc=0; ph_pair || rc=1
    if [ "$rc" = 0 ]; then ph_profile || rc=1; fi
    ph_unpair || rc=1
    ph_post || rc=1
    if [ "$rc" = 0 ]; then verdict PASS "kind=$(sget kind) bonded->unbonded, BT off"; else
      grep -q '^verdict: FAIL' "$RUN/verdict.txt" 2>/dev/null || verdict FAIL "see log"; fi
    say "return path: end the android-boot.sh hold, then android-boot.sh --restore ; scripts/a9-health.sh" ;;
esac
[ -s "$RUN/private/focus-pkgs.txt" ] && say "UI: $(sget taps 0) inputs (cap $MAXTAPS); focused packages seen before inputs: $(sort -u "$RUN/private/focus-pkgs.txt" | tr '\n' ' ')"
leakcheck || { say "LEAK in the run dir (NOT a phone stop): do not share it; the phone side is unaffected"; exit 5; }
exit "${rc:-0}"
