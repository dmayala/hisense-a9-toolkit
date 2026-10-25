#!/system/bin/sh
# Manual POC routing. Never invokes playback, never chooses an output at boot.
set -eu
CARD=Hisense-A9-Audio
raw_ctl() { /vendor/bin/a9-audio-ctl 0 --expect-card "$CARD" "$@"; }
ctl() {
 case $1 in
  set|set-stereo)
   printf 'CONTROL <%s> before: ' "$2"
   raw_ctl get "$2"
   printf 'CONTROL request:'
   for arg in "$@"; do printf ' <%s>' "$arg"; done
   printf '\n'
   raw_ctl "$@"
   printf 'CONTROL <%s> verified: ' "$2"
   raw_ctl get "$2"
   ;;
  *) raw_ctl "$@" ;;
 esac
}
output_off() {
 ctl set 'aw87xxx_mode_switch_a' Off
 for pin in Speaker Earpiece Headphones 'HiFi Headphones'; do ctl set "$pin Switch" 0; done
 for be in RX_CODEC_DMA_RX_0 RX_CODEC_DMA_RX_1 SEC_MI2S_RX; do ctl set "$be Audio Mixer MultiMedia1" 0; done
 ctl set 'ES9218 Bypass Switch' 0
 # WCD port controls survive endpoint/BE disable. Clear the old port set
 # before the next startup snapshots it into the SoundWire stream.
 for name in 'AUX_RDAC Switch' 'LO Switch' 'HPHL Switch' 'HPHR Switch' \
   'HPHL_RDAC Switch' 'HPHR_RDAC Switch' 'EAR_RDAC Switch' \
   'CLSH PA Switch' 'RX_EAR Mode Switch'; do ctl set "$name" 0; done
 for n in 0 1 2 3 4 5; do ctl set "RX_MACRO RX$n MUX" ZERO; done
}
case ${1:-} in
 load)
  # Existing sensor starter owns ADSP/firmware/mapper; never start it here.
  # Builder emits the full modinfo dependency order, including slimbus and
  # regmap-sdw before their SoundWire controller/codec users (insmod needs it).
  while read -r ko; do
   [ -n "$ko" ] || continue
   name=$(echo "${ko%.ko}" | tr - _)
   if [ ! -d "/sys/module/$name" ]; then
    if [ "$ko" = snd-soc-a9-audio.ko ]; then
     # Opt-in boot comparison only; parameter is immutable after module load.
     if [ "$(getprop ro.boot.a9_audio_clock_hold)" = 1 ]; then
      insmod "/vendor/lib/modules/$ko" lpass_clock_hold=1
     else
      insmod "/vendor/lib/modules/$ko"
     fi
    else
     insmod "/vendor/lib/modules/$ko"
    fi
   fi
  done < /vendor/lib/modules/modules.audio-poc
  ;;
 inventory)
  cat /proc/asound/cards /proc/asound/pcm
  ctl list
  ctl dump
  ;;
 dump) ctl dump ;;
 non-default) ctl non-default ;;
 probe) /vendor/bin/a9-audio-pcm probe 0 "$CARD" ;;
 off)
  output_off
  ctl set 'Main Mic Switch' 0
  ctl set 'Headset Mic Switch' 0
  ctl set 'MultiMedia2 Mixer TX_CODEC_DMA_TX_3' 0
  ctl set 'MultiMedia2 Mixer TX_CODEC_DMA_TX_4' 0
  ;;
 mic)
  ctl set 'Headset Mic Switch' 0
  ctl set 'MultiMedia2 Mixer TX_CODEC_DMA_TX_4' 0
  ctl set 'TX DEC2 MUX' SWR_MIC
  ctl set 'TX SMIC MUX2' SWR_MIC0
  ctl set 'TX_AIF1_CAP Mixer DEC2' 1
  ctl set 'ADC1 Switch' 1
  ctl set 'ADC1_MIXER Switch' 1
  # SOC_SINGLE_S8_TLV(-84, 40) uses ALSA indices: 84 = 0 dB,
  # while index 0 writes -84 dB (the TLV minimum/mute step).
  ctl set 'TX_DEC2 Volume' 84
  ctl set 'MultiMedia2 Mixer TX_CODEC_DMA_TX_3' 1
  ctl set 'Main Mic Switch' 1
  ctl non-default
  ;;
 capture-levels)
  echo 'Mixer controls before capture:'
  ctl non-default
  rc=0
  /vendor/bin/a9-audio-pcm capture-levels 1 "$CARD" || rc=$?
  echo 'Mixer controls after capture:'
  ctl non-default
  exit "$rc"
  ;;
 speaker|earpiece|headphone|headphone-wcd)
  [ "${2:-}" = --user-present ] || { echo 'output routing requires --user-present' >&2; exit 2; }
  output_off
  # First framework playback at minimum volume, before enabling a route.
  # Direct PCM bypasses framework volume and the caller temporarily stops
  # audioserver. Keep framework commands out of that direct check.
  [ "${3:-}" = --direct ] || cmd media_session volume --stream 3 --set 1
  case $1 in
   headphone)
    ctl set-stereo 'ES9218 Digital Volume' 0 0
    ctl set 'SEC_MI2S_RX Audio Mixer MultiMedia1' 1
    ctl set 'HiFi Headphones Switch' 1
    ;;
   speaker)
    ctl set 'aw87xxx_mode_switch_a' Music
    ctl set 'RX_MACRO RX2 MUX' AIF2_PB
    ctl set 'RX INT2_1 MIX1 INP0' RX2
    ctl set 'RX_RX2 Digital Volume' 75
    ctl set 'AUX_RDAC Switch' 1
    ctl set 'LO Switch' 1
    ctl set 'CLSH PA Switch' 1
    ctl set 'RX_CODEC_DMA_RX_1 Audio Mixer MultiMedia1' 1
    ctl set 'Speaker Switch' 1
    ;;
   earpiece)
    ctl set 'RX_MACRO RX0 MUX' AIF1_PB
    ctl set 'RX INT0_1 MIX1 INP0' RX0
    ctl set 'RX INT0 DEM MUX' CLSH_DSM_OUT
    ctl set 'RX_EAR Mode Switch' 1
    ctl set 'RDAC3_MUX' RX1
    ctl set 'EAR_RDAC Switch' 1
    ctl set 'HPHL Switch' 1
    ctl set 'CLSH PA Switch' 1
    ctl set 'RX HPH Mode' CLS_AB
    ctl set 'EAR_PA Volume' 16
    ctl set 'RX_RX0 Digital Volume' 54
    ctl set 'RX_CODEC_DMA_RX_0 Audio Mixer MultiMedia1' 1
    ctl set 'Earpiece Switch' 1
    ;;
   headphone-wcd)
    ctl set 'ES9218 Bypass Switch' 1
    ctl set 'RX_MACRO RX0 MUX' AIF1_PB
    ctl set 'RX_MACRO RX1 MUX' AIF1_PB
    ctl set 'RX INT0_1 MIX1 INP0' RX0
    ctl set 'RX INT1_1 MIX1 INP0' RX1
    ctl set 'RX INT0 DEM MUX' CLSH_DSM_OUT
    ctl set 'RX INT1 DEM MUX' CLSH_DSM_OUT
    ctl set 'RX_RX0 Digital Volume' 54
    ctl set 'RX_RX1 Digital Volume' 54
    ctl set 'HPHL Switch' 1
    ctl set 'HPHR Switch' 1
    ctl set 'CLSH PA Switch' 1
    ctl set 'HPHL_RDAC Switch' 1
    ctl set 'HPHR_RDAC Switch' 1
    ctl set 'RX_CODEC_DMA_RX_0 Audio Mixer MultiMedia1' 1
    ctl set 'Headphones Switch' 1
    ;;
  esac
  ;;
 *) echo 'usage: a9ml-audio.sh load|inventory|dump|non-default|probe|off|mic|capture-levels|speaker|earpiece|headphone|headphone-wcd [--user-present]' >&2; exit 2 ;;
esac
