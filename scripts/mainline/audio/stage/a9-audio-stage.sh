#!/system/bin/sh
# a9-audio-stage.sh -- PHONE side of the attended ES9218 Hi-Fi DAC bring-up
# (runs/audio-stage-plan.md). Runs as root on the mainline audio stage image,
# pushed and started by audio-stage.sh on the host. Never started by init.
#
#   a9-audio-stage.sh pre|A|P1|P2|C|cleanup [module_dir]
#     pre      read-only baseline: image/DT/ADSP/frontlight/GPIO state; no writes
#     A        PROBE / STANDBY ONLY (review 51 R51-1): load the sound core and the
#              codec with bringup=0 (no self test possible), check the bind and
#              that 65/66/68 stay low and 69 high for a 30 s observation window,
#              frontlight (AW99703 @0x36, the enabled i2c1 LED) before/after.
#              The codec does no I2C and never raises PA68/OSC66 in this stage.
#     P1       three powered ID reads, masked ID stable (review 57)
#     P2       one powered muted init + exact dump, after P1 PASS this boot
#              Empty jack required; review 57: P2 attended unless S0 proves init
#     C        sound: NOGO on this image (needs the ADSP)
#     cleanup  remove the codec module and VERIFY Standby
#
# Bounds (R51-5): a phone-side watchdog ends every stage after $DEADLINE s;
# every kernel-touching operation (insmod, rmmod, sysfs writes) runs in a child
# that the main shell polls, so a blocked kernel call cannot block the shell.
# Every exit path (verdict, error, TERM/INT/HUP, watchdog) runs containment:
# rmmod the codec and verify that the module is gone and 65/66/68 read low.
# If that cannot be verified, the verdict is CONTAINMENT-UNVERIFIED (exit 6):
# the attended fallback is to hold Power 10-15 s (PON reason 2 -> bootloader
# fastboot), then `android-boot.sh --restore`.
# Latch (R51-6): $MD/state records per boot_id + tuple every verdict. Any FAIL
# in this boot refuses every stage but cleanup; A/P1/P2 run once per boot.
# Final line: "AUDIO-STAGE-<X> PASS|FAIL|STOP|NOGO|CONTAINMENT-UNVERIFIED <detail>";
# exit 0 PASS, 1 FAIL, 2 STOP, 3 NOGO, 6 CONTAINMENT-UNVERIFIED.
#
# Test hooks (host tests only; unset on the phone): A9_AUDIO_ROOT prefixes
# every /sys /proc /dev path; A9_AUDIO_TEST_HOOK is run after each sysfs write
# and module load/unload so a fake kernel can react; A9_AUDIO_DEADLINE,
# A9_AUDIO_OBSERVE shorten the timers.
R=${A9_AUDIO_ROOT:-}
STAGE=${1:-}
MD=${2:-/data/local/tmp/a9audio}
HOOK=${A9_AUDIO_TEST_HOOK:-}
DEADLINE=${A9_AUDIO_DEADLINE:-90}
OBSERVE=${A9_AUDIO_OBSERVE:-30}
DRV=$R/sys/bus/i2c/drivers/a9-es9218
LED=$R/sys/class/leds/a9:warm:center      # AW99703 @0x36 on i2c1 (the only enabled i2c1 LED)
GPIO=$R/sys/kernel/debug/gpio
DTB=$R/sys/firmware/devicetree/base
STATE=$MD/state
DONE=0 WD="" TOUCHED=0

say() { echo "[$STAGE] $*"; }
hook() { [ -z "$HOOK" ] || "$HOOK" "$@"; }
loaded() { grep -q "^$1 " "$R/proc/modules" 2>/dev/null; }
codec_dev() { for d in "$DRV"/*-0048; do [ -d "$d" ] && { echo "$d"; return 0; }; done; return 1; }
boot_id() { cat "$R/proc/sys/kernel/random/boot_id" 2>/dev/null; }
tuple() { echo "$(cat "$MD/kernel-image.sha256" 2>/dev/null | cut -c1-16)/$(sha256sum "$0" 2>/dev/null | cut -c1-16)"; }

# kio <secs> <cmd...>: run a kernel-touching command in a child, poll it; 0 ok, 1 failed, 124 still running
kio() {
  s=$1; shift
  o=$MD/kio.$$
  "$@" > "$o" 2>&1 < /dev/null & p=$!   # never inherits the session's stdout: a hung child cannot hold adb open
  n=0
  while kill -0 "$p" 2>/dev/null; do
    [ "$n" -ge "$s" ] && { say "kernel I/O still running after ${s}s: $*"; return 124; }
    sleep 1; n=$((n + 1))
  done
  wait "$p"; r=$?; [ -s "$o" ] && sed "s/^/[$STAGE]   /" "$o"; rm -f "$o"; return $r
}
read_selftest() { cat "$1" > "$2"; }
swrite() { printf '%s\n' "$2" > "$1" || return; hook write "$1" "$2"; }

# msm pinctrl dbg_show line: " gpio65  : out high func0 2mA pull down" -> "out high"
gpio_line() { sed -n "s/^ *gpio$1 *: *\([a-z]*\) *\([a-z]*\).*/\1 \2/p" "$GPIO" 2>/dev/null | head -n 1; }
pins() { echo "65 $(gpio_line 65), 66 $(gpio_line 66), 68 $(gpio_line 68), 69 $(gpio_line 69)"; }
standby_ok() {
  [ "$(gpio_line 65)" = "out low" ] && [ "$(gpio_line 66)" = "out low" ] &&
  [ "$(gpio_line 68)" = "out low" ] && [ "$(gpio_line 69)" = "out high" ]
}
power_off_ok() { for g in 65 66 68; do case "$(gpio_line $g)" in *low) ;; *) return 1 ;; esac; done; return 0; }

latch() { mkdir -p "$STATE" 2>/dev/null; echo "$(boot_id) $(tuple) $STAGE $1" >> "$STATE/verdicts" 2>/dev/null; }
attempted() { grep -q "^$(boot_id) [^ ]* $1 START\$" "$STATE/verdicts" 2>/dev/null; }
latched() { grep -q "^$(boot_id) $(tuple) $1 $2\$" "$STATE/verdicts" 2>/dev/null; }
any_fail() { grep -qE "^$(boot_id) [^ ]+ [^ ]+ (FAIL|CONTAINMENT-UNVERIFIED)\$" "$STATE/verdicts" 2>/dev/null; }

codec_ever() { grep -q "^$(boot_id) codec-insmod" "$STATE/verdicts" 2>/dev/null; }
contain() {   # 0 = module gone and 65/66/68 low (verified), or the codec never loaded this boot
  if ! loaded snd_soc_es9218_a9 && ! codec_ever; then
    say "codec never loaded this boot: pins not owned by the codec ($(pins))"; return 0
  fi
  if loaded snd_soc_es9218_a9; then
    say "containment: rmmod snd_soc_es9218_a9 (remove forces Standby)"
    kio 10 rmmod snd_soc_es9218_a9; hook rmmod snd_soc_es9218_a9
  fi
  say "gpio: $(pins)"
  ! loaded snd_soc_es9218_a9 && power_off_ok
}
finish() {   # finish <verdict> <detail>  (the only exit path)
  v=$1 d=$2
  [ -n "$WD" ] && kill "$WD" 2>/dev/null
  DONE=1
  if { [ "$v" != PASS ] || [ "$STAGE" = P1 ] || [ "$STAGE" = P2 ]; } && [ "$v" != NOGO ] && [ "$STAGE" != pre ] && { [ "$TOUCHED" = 1 ] || loaded snd_soc_es9218_a9; }; then
    if ! contain; then
      v=CONTAINMENT-UNVERIFIED
      d="$d; codec removal/power-off NOT verified: hold Power 10-15 s now (PON 2 -> fastboot), then android-boot.sh --restore"
    fi
  fi
  case $STAGE in pre|C) ;; *) [ "$v" = STOP ] || latch "$v" ;; esac
  echo "AUDIO-STAGE-$STAGE $v $d"
  case $v in PASS) exit 0;; FAIL) exit 1;; STOP) exit 2;; NOGO) exit 3;; *) exit 6;; esac
}
fail() { say "FAIL: $*"; kdump; finish FAIL "$*"; }
stop() { say "STOP: $*"; finish STOP "$*"; }
on_signal() { [ "$DONE" = 1 ] && exit 6; fail "interrupted or stage deadline (${DEADLINE}s) reached"; }
trap on_signal TERM INT HUP

kmark() {
  echo "A9AUDIO $STAGE $1 $$" > "$R/dev/kmsg" 2>/dev/null || fail "cannot write the kernel log marker"
  hook kmsg "A9AUDIO $STAGE $1 $$"
  dmesg 2>/dev/null | grep -q "A9AUDIO $STAGE $1 $$" || fail "kernel log marker not readable back (dmesg)"
}
kdelta() { dmesg 2>/dev/null | sed -n "/A9AUDIO $STAGE begin $$/,\$p"; }
kdump() { say "kernel log since stage start:"; kdelta | tail -n 60; }
# Any of these in the stage's kernel log is a stop condition (review 33 general stops).
KBAD='i2c[^ ]* 4a84000|4a84000\.i2c|geni.*(timeout|timed out|error)|i2c.*(timed out|arbitration|-110|-121|NACK)|aw99703.*(err|fail)|a9-frontlight.*(err|fail)|fp9929a.*fault|a9_eink.*fault|a9-es9218.*(fail|error|incorrect|missing|selftest.*ret=-)|Oops|BUG:|WARNING:|Call trace'
kcheck() {
  k=$(kdelta)
  [ -n "$k" ] || fail "stage kernel log unavailable (marker lost): monitoring would be blind"
  b=$(echo "$k" | grep -E -i "$KBAD" | head -n 5); [ -z "$b" ] || fail "kernel log: $b"
}

# DT nodes resolved by unit-address chain, not by an assumed parent (R51-3)
dt_find() { find "$DTB" -type d -path "*/$1" 2>/dev/null | head -n 1; }
dt_status() { cat "$1/status" 2>/dev/null | tr -d '\0'; }
dt_compat() { tr '\0' ' ' < "$1/compatible" 2>/dev/null; }

fl_test() {   # frontlight on i2c1: one step, read back, restore (attended: a visible blink)
  [ -w "$LED/brightness" ] || fail "$1: frontlight $LED not writable"
  b=$(cat "$LED/brightness") || fail "$1: frontlight read"
  m=$(cat "$LED/max_brightness" 2>/dev/null || echo 1)
  if [ "$b" -gt 0 ]; then t=$((b - 1)); else t=1; fi
  [ "$t" -le "$m" ] || t=$m
  kio 5 swrite "$LED/brightness" "$t" || fail "$1: frontlight write $t refused or blocked"
  [ "$(cat "$LED/brightness")" = "$t" ] || fail "$1: frontlight readback $(cat "$LED/brightness") != $t"
  kio 5 swrite "$LED/brightness" "$b" || fail "$1: frontlight restore $b refused or blocked"
  [ "$(cat "$LED/brightness")" = "$b" ] || fail "$1: frontlight restore readback"
  say "frontlight i2c1 0x36 $1: $b -> $t -> $b OK"
}

preconditions() {
  [ "$(id -u)" = 0 ] || stop "not root"
  [ "$(getprop ro.a9ml.vendor 2>/dev/null)" = 1 ] || stop "not the mainline vendor (ro.a9ml.vendor != 1)"
  [ -n "$(boot_id)" ] || stop "no boot_id"
  c=$(dt_find 'i2c@4a84000/audio-codec@48')
  [ -n "$c" ] || stop "DT: no i2c@4a84000/audio-codec@48 node"
  case "$(dt_compat "$c")" in *hisense,a9-es9218*) ;; *) stop "DT: $c is not hisense,a9-es9218";; esac
  [ "$(dt_status "$c")" = okay ] || stop "DT: audio-codec@48 is not okay (not an --enable-es9218 image)"
  l=$(dt_find 'i2c@4a84000/backlight@36')
  [ -n "$l" ] && [ "$(dt_status "$l")" != disabled ] || stop "DT: AW99703 backlight@36 on i2c1 is not enabled"
  a=$(dt_find 'remoteproc@a400000')
  [ -n "$a" ] || stop "DT: no remoteproc@a400000 node"
  [ "$(dt_status "$a")" = disabled ] || stop "DT: ADSP remoteproc is '$(dt_status "$a")', stage A requires it disabled"
  for m in snd_soc_a9_es9218 qcom_q6v5_pas q6afe apr; do loaded $m && stop "module $m is loaded"; done
  for p in "$R"/sys/class/remoteproc/*; do
    [ -f "$p/name" ] || continue
    case "$(cat "$p/name")" in *adsp*|*a400000*) [ "$(cat "$p/state" 2>/dev/null)" = running ] && stop "ADSP remoteproc is running";; esac
  done
  [ -r "$GPIO" ] || { [ -z "$R" ] && mount -t debugfs debugfs /sys/kernel/debug 2>/dev/null; }
  [ -r "$GPIO" ] || stop "$GPIO unreadable (debugfs not mounted)"
}
check_bundle() {
  [ -f "$MD/modules.order" ] && [ -f "$MD/SHA256SUMS" ] || stop "no module bundle in $MD (audio-stage.sh pushes it)"
  (cd "$MD" && sha256sum -c SHA256SUMS >/dev/null 2>&1) || stop "module bundle hash mismatch in $MD"
  kr=$(uname -r); want=$(cat "$MD/kernel.release" 2>/dev/null)
  [ "$kr" = "$want" ] || stop "bundle built for '$want', running '$kr'"
}
load_codec() {
  bringup=${1:-0}
  case $bringup in 0) param=N;; 1) param=Y;; esac
  TOUCHED=1
  for k in $(cat "$MD/modules.order"); do
    n=$(echo "${k%.ko}" | tr - _)
    loaded "$n" && continue
    case $k in snd-soc-es9218-a9.ko) args=bringup=$bringup
      mkdir -p "$STATE" && echo "$(boot_id) codec-insmod $$" >> "$STATE/verdicts" || fail "cannot record the codec load";;
    *) args=;; esac
    say "insmod $k $args"
    kio 8 insmod "$MD/$k" $args || fail "insmod $k failed or blocked"
    hook insmod "$k" $args
  done
  i=0; while [ $i -lt 5 ]; do codec_dev >/dev/null && break; sleep 1; i=$((i + 1)); done
  d=$(codec_dev) || fail "codec did not bind to i2c 0x48 within 5 s"
  [ "$(cat "$R/sys/module/snd_soc_es9218_a9/parameters/bringup" 2>/dev/null)" = "$param" ] || fail "bringup parameter is not $param"
  kdelta | grep -q "a9-es9218.*standby, no I2C at probe (bringup=$bringup)" || fail "no standby probe line (bringup=$bringup) from the codec"
  say "bound: ${d##*/}"
}

[ "$STAGE" = cleanup ] || [ "$STAGE" = pre ] || [ "$STAGE" = C ] || [ "$STAGE" = P1 ] || [ "$STAGE" = P2 ] || [ "$STAGE" = A ] \
  || { echo "usage: $0 pre|A|P1|P2|C|cleanup [module_dir]" >&2; exit 64; }
( sleep "$DEADLINE"; kill -TERM $$ ) > /dev/null 2>&1 < /dev/null & WD=$!

case $STAGE in
pre)
  preconditions
  say "kernel $(uname -r); codec DT okay; AW99703 DT enabled; ADSP DT disabled; no audio module loaded"
  say "gpio before the codec owns them: $(pins)"
  say "frontlight warm center $(cat "$LED/brightness" 2>/dev/null)/$(cat "$LED/max_brightness" 2>/dev/null)"
  finish PASS "baseline recorded"
  ;;
A)
  preconditions
  any_fail && stop "a failure is latched for this boot: no further stage; cleanup + restore"
  latched A PASS && stop "stage A already passed this boot (one run per boot)"
  loaded snd_soc_es9218_a9 && stop "codec already loaded (run cleanup, or reboot)"
  check_bundle
  kmark begin
  fl_test before
  load_codec
  standby_ok || fail "after probe: GPIO not in Standby ($(pins))"
  kcheck
  n=0; while [ $n -lt "$OBSERVE" ]; do
    standby_ok || fail "Standby lost after ${n}s ($(pins))"
    sleep 1; n=$((n + 1))
  done
  kcheck
  fl_test after
  standby_ok || fail "Standby lost at the end ($(pins))"
  kcheck
  kmark end
  finish PASS "probe/Standby only: bound bringup=0, 65/66/68 low 69 high for ${OBSERVE}s, frontlight ok, no PA/OSC raised, no codec I2C"
  ;;
P1|P2)
  preconditions
  any_fail && stop "a failure is latched for this boot: no further stage; cleanup + restore"
  attempted "$STAGE" && stop "stage $STAGE already attempted this boot (one run per boot)"
  [ "$STAGE" != P2 ] || latched P1 PASS || stop "P2 requires P1 PASS for this tuple in this boot"
  check_bundle
  # START is recorded before the first mutation; even a lost shell cannot retry.
  latch START
  attempted "$STAGE" || fail "cannot record the once-per-boot latch"
  kmark begin
  say "empty jack required (review 57); P2 attended unless stock S0 proves chip_init completion"
  fl_test before
  # Reload even if A left the codec bound; bringup is a read-only module parameter.
  TOUCHED=1
  contain || fail "cannot verify containment before bringup=1 reload"
  load_codec 1
  standby_ok || fail "after probe: GPIO not in Standby ($(pins))"
  kcheck
  if [ "$STAGE" = P1 ]; then level=id; count=3; else level=init; count=1; fi
  iteration=1; ids=""
  while [ "$iteration" -le "$count" ]; do
    standby_ok || fail "before $level $iteration: GPIO not in Standby ($(pins))"
    # Do not signal a kio child: Android mksh can re-raise TERM to our group.
    kio 8 swrite "$d/selftest" "$level"; store_rc=$?
    [ "$store_rc" = 124 ] && fail "$level $iteration selftest store timed out (124)"
    # show also takes the codec mutex: bound this read, retaining its raw evidence.
    kio 8 read_selftest "$d/selftest" "$MD/selftest-result.$$" || fail "$level $iteration selftest read failed or blocked"
    result=$(cat "$MD/selftest-result.$$")
    echo "$result"
    [ "$store_rc" = 0 ] || fail "$level $iteration selftest store failed ($store_rc)"
    status=$(echo "$result" | head -n 1)
    echo "$status" | grep -qE "^level=$level ret=0 id=0x[0-9a-fA-F]+ match=1 mode=0$" || fail "$level $iteration bad selftest status: $status"
    raw_id=$(echo "$status" | sed -n 's/.* id=\(0x[0-9a-fA-F]*\) .*/\1/p')
    [ $((raw_id & 252)) = 192 ] || fail "$level $iteration wrong masked ID: $raw_id"
    ids="$ids $raw_id"
    standby_ok || fail "after $level $iteration: GPIO not in Standby ($(pins))"
    if [ "$STAGE" = P2 ]; then
      [ "$result" = "$status
regs=00:00 01:80 02:34 03:70 04:00 05:68 06:07 07:41 08:dd 09:00 0a:02 0b:00 0c:77 0d:00 0e:25 0f:ff 10:ff 11:ff 12:ff 13:ff 14:62 15:00 16:90 17:00 18:36 19:00 1a:62 1b:c4 1c:f0 1d:05 1e:00 1f:00 20:03 21:3c 22:00 23:00 24:00 25:00 26:00 27:00 28:00 29:00 2a:00 2b:00 2c:00 2d:00 2e:00" ] || fail "init dump differs from the expected muted stock table"
    fi
    kcheck
    iteration=$((iteration + 1))
  done
  fl_test after
  standby_ok || fail "Standby lost at the end ($(pins))"
  kcheck
  # finish verifies removal even on PASS; a failure there must latch, not PASS.
  contain || fail "cannot verify final codec removal/power-off"
  kcheck
  kmark end
  finish PASS "$count $level selftest(s), ret=0 match=1 mode=0, masked IDs=0xc0 raw:$ids; frontlight ok; codec removed, 65/66/68 low"
  ;;
C)
  finish NOGO "sound needs the ADSP (q6afe SECONDARY_MI2S_RX clocks/data); not in this image"
  ;;
cleanup)
  if contain; then finish PASS "codec removed, 65/66/68 low"; fi
  [ -n "$WD" ] && kill "$WD" 2>/dev/null; DONE=1
  echo "AUDIO-STAGE-cleanup CONTAINMENT-UNVERIFIED codec removal/power-off NOT verified: hold Power 10-15 s now, then android-boot.sh --restore"
  latch CONTAINMENT-UNVERIFIED; exit 6
  ;;
esac
