#!/usr/bin/env bash
# Attended host launcher. Upload the body, then submit a short root command;
# adb stdin is /dev/null, so it cannot consume prompts or lose a script pipe.
set -eu
umask 077
usage() { echo 'usage: audio-jack-earpiece.sh --user-present [--phase earpiece|headset|unplug] [output.txt]' >&2; exit 2; }
[ "${1:-}" = --user-present ] || usage
shift
selected_phase=
if [ "${1:-}" = --phase ]; then
 [ "$#" -ge 2 ] || usage
 selected_phase=$2
 case $selected_phase in earpiece|headset|unplug) ;; *) usage ;; esac
 shift 2
fi
[ "$#" -le 1 ] || usage
output=${1:-$(dirname -- "${BASH_SOURCE[0]}")/audio-jack-earpiece-$(date -u +%Y%m%dT%H%M%SZ)-$$.txt}
mkdir -p "$(dirname -- "$output")"
printf 'A9 attended earpiece + headset check\nHOST_BEGIN phase=%s\n' "${selected_phase:-interactive}" >"$output"
printf 'Capture: %s\n' "$output"
if [ -z "$selected_phase" ] && [ ! -t 0 ]; then
 echo 'ATTENDANCE_INPUT_REQUIRED: interactive terminal unavailable; use --user-present --phase after confirming the physical step' | tee -a "$output" >&2
 exit 2
fi
work=$(mktemp -d)
remote=/data/local/tmp/a9-jack-earpiece-$(date -u +%Y%m%dT%H%M%SZ)-$$.sh
uploaded=0
cleanup_host() {
 rc=$?
 trap - EXIT
 if [ "$uploaded" = 1 ]; then
  adb shell -T "rm -f $remote" </dev/null >>"$output" 2>&1 || true
 fi
 rm -f -- "$work/check.sh"
 rmdir -- "$work"
 exit "$rc"
}
trap cleanup_host EXIT
trap 'exit 1' HUP INT TERM
cat >"$work/check.sh" <<'A9_JACK_CHECK'
set -eu
ctl() { /vendor/bin/a9-audio-ctl 0 --expect-card Hisense-A9-Audio "$@"; }
jack() { ctl get 'Headphone Jack'; }
echo "PHASE $1 uptime=$(cat /proc/uptime)"
echo "JACK $(jack)"
case $1 in
 earpiece) expected=0; route=earpiece ;;
 headset) expected=1; route=headphone-wcd ;;
 unplug) expected=0; route= ;;
 *) exit 2 ;;
esac
# Use the real MBHC/ALSA jack indication, never an injected framework event.
i=0
while [ "$(jack)" != "$expected" ] && [ "$i" -lt 20 ]; do sleep 0.25; i=$((i+1)); done
jack_rc=0
if [ "$(jack)" = "$expected" ]; then
 echo "JACK_RESULT phase=$1 expected=$expected actual=$(jack) PASS"
else
 echo "JACK_RESULT phase=$1 expected=$expected actual=$(jack) FAIL"
 # This is an explicitly attended physical check. Keep listening results
 # separate from MBHC failure; never inject or claim a detected jack.
 echo 'Physical step confirmed by operator; continuing tones, jack result remains FAIL'
 jack_rc=1
fi
[ -n "$route" ] || exit "$jack_rc"
was_running=$(getprop init.svc.audioserver)
saved_rx0=$(ctl get 'RX_RX0 Digital Volume')
saved_rx1=$(ctl get 'RX_RX1 Digital Volume')
cleanup() {
    rc=$?
    /vendor/bin/a9ml-audio.sh off || true
    ctl set 'RX_RX0 Digital Volume' "$saved_rx0" || true
    ctl set 'RX_RX1 Digital Volume' "$saved_rx1" || true
    [ "$was_running" != running ] || start audioserver
    return "$rc"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
# Keep the HAL process/provider alive. Direct PCM only needs audioserver stopped.
stop audioserver
sleep 0.5
rc=$jack_rc
if [ "$route" = earpiece ]; then tones='tone tone'; gain=74; else tones='tone-left tone-right tone'; gain=64; fi
for tone in $tones; do
 # Direct PCM leaves PA/RDAC off after every beep. Select it anew each time.
 /vendor/bin/a9ml-audio.sh "$route" --user-present --direct
 # Waveform peak is now -20dBFS. These attenuations reproduce the levels
 # already heard in the attended result: EAR -20-10-18=-48, HPH -20-20=-40.
 # Never inherit the manual +30/+20dB gains from that experiment.
 ctl set 'RX_RX0 Digital Volume' "$gain"
 [ "$route" != headphone-wcd ] || ctl set 'RX_RX1 Digital Volume' "$gain"
 echo "TONE BEGIN route=$route channel=$tone macro=$gain peak=-20dBFS"
 for name in 'Speaker Switch' 'Earpiece Switch' 'Headphones Switch' \
  'RX_MACRO RX0 MUX' 'RX_MACRO RX1 MUX' \
  'RX INT0_1 MIX1 INP0' 'RX INT1_1 MIX1 INP0' \
  'RX INT0 DEM MUX' 'RX INT1 DEM MUX' \
  'HPHL_RDAC Switch' 'HPHR_RDAC Switch' 'EAR_RDAC Switch' \
  'HPHL Switch' 'HPHR Switch' 'RX_RX0 Digital Volume' 'RX_RX1 Digital Volume' \
  'RX_CODEC_DMA_RX_0 Audio Mixer MultiMedia1' 'aw87xxx_mode_switch_a'; do
  printf '%s: ' "$name"; ctl get "$name"
 done
 tone_rc=0
 timeout 10 /vendor/bin/a9-audio-pcm "$tone" 0 Hisense-A9-Audio --user-present &
 tone_pid=$!
 sleep 0.2
 echo 'DURING TONE PCM / PA STATES'
 cat /proc/asound/card0/pcm0p/sub0/status /proc/asound/card0/pcm0p/sub0/hw_params || true
 find /sys/kernel/debug/asoc -type f -path '*/dapm/*' | while IFS= read -r file; do
  case "$file" in
   */'HPHL PGA'|*/'HPHR PGA'|*/'EAR PGA'|*/'RX INT0 DEM MUX'|*/'RX INT1 DEM MUX')
    printf '\nDAPM %s\n' "$file"; cat "$file" || true ;;
  esac
 done
 wait "$tone_pid" || tone_rc=$?
 echo "TONE END route=$route channel=$tone rc=$tone_rc"
 [ "$tone_rc" = 0 ] || { rc=$tone_rc; break; }
 sleep 0.5
done
echo "JACK AFTER $(jack)"
exit "$rc"
A9_JACK_CHECK
upload_rc=0
adb push "$work/check.sh" "$remote" </dev/null >>"$output" 2>&1 || upload_rc=$?
printf 'UPLOAD_RESULT rc=%s\n' "$upload_rc" >>"$output"
[ "$upload_rc" = 0 ] || exit "$upload_rc"
uploaded=1
phase() {
 printf 'HOST_DISPATCH phase=%s\n' "$1" | tee -a "$output"
 # Both tokens contain only fixed phase names and our generated ASCII path.
 # Quote the root command on the adb shell side so $? expands in rootsh.
 root_command="/system/bin/sh $remote $1; phase_rc=\$?; echo PHASE_RESULT $1 \$phase_rc; exit"
 transport_rc=0
 adb shell -T "printf '%s\n' '$root_command' | timeout 60 nc 127.0.0.1 2324" </dev/null >>"$output" 2>&1 || transport_rc=$?
 printf 'HOST_TRANSPORT phase=%s rc=%s\n' "$1" "$transport_rc" >>"$output"
 if ! grep -Fqx "PHASE_RESULT $1 0" "$output"; then
  printf 'HOST_PHASE_FAILED phase=%s: inspect device JACK_RESULT/TONE lines; missing PHASE_RESULT means transport failed\n' "$1" | tee -a "$output" >&2
  return 1
 fi
 [ "$transport_rc" = 0 ]
}
result=0
if [ -n "$selected_phase" ]; then
 # Selecting a phase with --user-present confirms its physical step; no stdin
 # prompts are required for agent's noninteractive attended command runner.
 phase "$selected_phase" || result=1
else
 printf 'Unplug the headset. Hold the earpiece to your ear for two tones, then press Enter: '
 if read -r _; then phase earpiece || result=1; else echo 'ATTENDANCE_INPUT_EOF phase=earpiece' >>"$output"; exit 2; fi
 printf 'Insert the headset and put it on: left, right, then both will play. Press Enter: '
 if read -r _; then phase headset || result=1; else echo 'ATTENDANCE_INPUT_EOF phase=headset' >>"$output"; exit 2; fi
 printf 'Unplug the headset, then press Enter: '
 if read -r _; then phase unplug || result=1; else echo 'ATTENDANCE_INPUT_EOF phase=unplug' >>"$output"; exit 2; fi
fi
printf 'HOST_END rc=%s\n' "$result" >>"$output"
printf 'Capture: %s\nReport earpiece twice, headset left / right / both, and any start/end pops.\n' "$output"
exit "$result"
