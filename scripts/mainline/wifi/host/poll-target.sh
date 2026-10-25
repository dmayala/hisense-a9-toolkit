# Read-only state poller for one Wi-Fi active trial. NOT pushed to the phone:
# capture-run.sh sends this text over the root channel (adb shell 'nc 127.0.0.1 2324' < this),
# and the listener's root /system/bin/sh (init.hlte556n.rc a9ml_rootsh) runs it from the socket.
# It never creates, writes or removes files. Nothing in it reads stdin (stdin is the script).
# First line is this shell's PID, so stop can kill exactly this process (never by name).
# ~250 ms period; builtins where possible (review 42 B8).
echo "POLLPID $$"
D=/mnt/a9-wifi/trial
B=/sys/class/power_supply/battery
n=0; last=
while :; do
  n=$((n + 1))
  read up idle < /proc/uptime
  echo "POLL $up"
  for r in /sys/class/remoteproc/remoteproc*; do
    [ -r "$r/state" ] || continue
    nm=; st=; rc=; read nm < "$r/name"; read st < "$r/state"
    [ -r "$r/recovery" ] && read rc < "$r/recovery"
    echo "RP ${r##*/} $nm $st $rc"
  done
  t=; sg=
  for f in "$D"/*; do [ -e "$f" ] && t="$t ${f##*/}"; done
  [ -r "$D/stage" ] && read sg < "$D/stage"
  echo "TRIAL stage=$sg files=$t"
  grep -iE 'q6v5|mpss|modem|wdog|fatal|handover' /proc/interrupts
  # fault text and watcher log tails, only when the trial dir listing changed (or every ~5 s)
  cur=$(ls -l "$D" 2>/dev/null)
  if [ "$cur" != "$last" ] || [ $((n % 20)) = 0 ]; then
    last=$cur
    [ -r "$D/fault" ] && head -c 2048 "$D/fault" | while IFS= read -r x; do echo "FAULT $x"; done
    for l in watchdog.log guardian.log guardian-stop.log; do
      [ -r "$D/$l" ] && tail -n 5 "$D/$l" 2>/dev/null | head -c 2048 | while IFS= read -r x; do echo "LOG $l $x"; done
    done
  fi
  # temperature and power every 5th poll
  if [ $((n % 5)) = 0 ]; then
    s=
    for z in /sys/class/thermal/thermal_zone*; do
      [ -r "$z/temp" ] || continue
      ty=; tp=; read ty < "$z/type"; read tp 2>/dev/null < "$z/temp"
      s="$s ${ty}=${tp}"
    done
    echo "THERM$s"
    bt=; bv=; bc=; bs=; bp=
    [ -r "$B/temp" ] && read bt < "$B/temp"; [ -r "$B/voltage_now" ] && read bv < "$B/voltage_now"
    [ -r "$B/current_now" ] && read bc < "$B/current_now"; [ -r "$B/status" ] && read bs < "$B/status"
    [ -r "$B/capacity" ] && read bp < "$B/capacity"
    echo "BATT temp=$bt uv=$bv ua=$bc status=$bs cap=$bp"
  fi
  # dead-man stay file age every ~5 s (two forks)
  [ $((n % 20)) = 1 ] && echo "STAY now=$(date +%s) mtime=$(stat -c %Y /data/local/tmp/a9-stay 2>/dev/null)"
  sleep 0.25
done
