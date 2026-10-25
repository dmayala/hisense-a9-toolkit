#!/system/bin/sh
# Phone-side commands for the coordinator. No automatic service or playback.
set -eu
CTL=/vendor/bin/a9-audio-ctl
PCM=/vendor/bin/a9-audio-pcm
CARD=${2:-Hisense-A9-HiFi}
ctl() { "$CTL" 0 --expect-card "$CARD" "$@"; }
case ${1:-} in
 inventory)
  cat /proc/asound/cards /proc/asound/pcm
  ctl list
  if [ "$CARD" = Hisense-A9-HiFi ]; then
   ctl get 'ES9218 Digital Volume'
   ctl get 'ES9218 Bypass Switch'
  fi
  ;;
 probe)
  # Open and close only. No HW_PARAMS/PREPARE: those can power a codec.
  "$PCM" probe 0 "$CARD"
  ;;
 tone)
  [ "${2:-}" = --user-present ] || { echo 'tone requires --user-present' >&2; exit 2; }
  CARD=Hisense-A9-HiFi
  # Raw 0 is stock's initialized register value. Source PCM sets the low
  # initial amplitude; no unverified attenuation/dB mapping is assumed.
  cleanup() {
   ctl set 'SEC_MI2S_RX Audio Mixer MultiMedia1' 0
   ctl set-stereo 'ES9218 Digital Volume' 255 255
  }
  trap cleanup EXIT
  ctl set 'ES9218 Bypass Switch' 0
  ctl set-stereo 'ES9218 Digital Volume' 0 0
  ctl set 'SEC_MI2S_RX Audio Mixer MultiMedia1' 1
  "$PCM" tone 0 "$CARD" --user-present
  ;;
 *) echo 'usage: a9-audio-poc.sh inventory|probe [CARD_NAME]; tone --user-present' >&2; exit 2 ;;
esac
