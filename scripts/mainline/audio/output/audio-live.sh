#!/usr/bin/env bash
# Host launcher for agent's attended slot-B audio diagnostic. Never flashes.
# Hold while the user is AFK; run only after they return.
set -eu
umask 077
image_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [ "${1:-}" = --image-dir ]; then
    image_dir=$2
    shift 2
fi
output=${1:-$image_dir/audio-live-$(date -u +%Y%m%dT%H%M%SZ).txt}
mkdir -p "$(dirname "$output")"
rc=0
adb shell -T 'timeout 90 nc 127.0.0.1 2324' >"$output" 2>&1 <<'A9_ROOT_SHELL' || rc=$?
/system/bin/sh <<'A9_AUDIO_CAPTURE'
# This block runs only when the coordinator invokes the host launcher.
umask 077
work=/data/local/tmp/a9-audio-live-$$
mkdir "$work" || exit 1
trace_pid= pcm_pid= state_pid= hal_pid=
player_started=0
cleanup() {
    [ "$player_started" = 0 ] || am force-stop org.lineageos.twelve >/dev/null 2>&1
    for pid in "$pcm_pid" "$state_pid" "$trace_pid" "$hal_pid"; do
        [ -z "$pid" ] || kill "$pid" 2>/dev/null
    done
    for pid in "$pcm_pid" "$state_pid" "$trace_pid" "$hal_pid"; do
        [ -z "$pid" ] || wait "$pid" 2>/dev/null
    done
    # Only this invocation's named temporary files; no recursive removal.
    rm -f "$work/pcm" "$work/states" "$work/hal" "$work/trace.log" \
        "$work/frontend.trace" "$work/am" "$work/kernel-before"
    rmdir "$work" 2>/dev/null
}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP
stamp() { printf '\nUPTIME '; cat /proc/uptime; }
pcm_snapshot() {
    stamp
    for sub in /proc/asound/card0/pcm*/sub0; do
        [ -d "$sub" ] || continue
        for name in status hw_params sw_params; do
            printf '\n-- %s/%s\n' "$sub" "$name"
            cat "$sub/$name" 2>&1
        done
    done
}
state_snapshot() {
    stamp
    printf '\n== MIXER VALUES (dump, not list)\n'
    timeout 5 /vendor/bin/a9-audio-ctl 0 --expect-card Hisense-A9-Audio dump 2>&1
    printf 'mixer_dump_rc=%s\n' "$?"
    printf '\n== DAPM WIDGET STATES\n'
    find /sys/kernel/debug/asoc -type f -path '*/dapm/*' 2>&1 |
        while IFS= read -r file; do
            [ -f "$file" ] || { printf '%s\n' "$file"; continue; }
            printf '\nDAPM %s\n' "$file"
            cat "$file" 2>&1
        done
    printf '\n== AW87519 DEVICE / RUNTIME STATE\n'
    for dev in /sys/bus/i2c/devices/*-0058; do
        [ -d "$dev" ] || continue
        printf '\nAMP %s\n' "$dev"
        cat "$dev/name" "$dev/power/runtime_status" 2>&1
        ls -l "$dev/driver" 2>&1
    done
    printf '\n== GPIO READBACK (TLMM local pin107 is amp reset)\n'
    cat /sys/kernel/debug/gpio 2>&1
    for pins in /sys/kernel/debug/pinctrl/*/pins \
        /sys/kernel/debug/pinctrl/*/pinconf-pins \
        /sys/kernel/debug/pinctrl/*/pinmux-pins; do
        [ -r "$pins" ] || continue
        printf '\nPINCTRL %s\n' "$pins"
        grep -E 'gpio107|pin 107[ :()]|aw87519|0058' "$pins" 2>&1
    done
    printf '\n== AW87519 REGISTERS (read-only regmap; never enable amp for reads)\n'
    found=0
    for map in /sys/kernel/debug/regmap/*; do
        case "$map" in
            *0058*|*aw87519*)
                [ -r "$map/registers" ] || continue
                found=1
                printf '\nREGMAP %s\n' "$map"
                timeout 2 cat "$map/registers" 2>&1
                printf 'regmap_read_rc=%s\n' "$?"
                ;;
        esac
    done
    [ "$found" = 1 ] || echo 'AW87519 regmap debugfs unavailable; not proof amp is off'
}
printf 'A9 AUDIO LIVE BEGIN\n'
stamp
id
cat /proc/asound/cards
getprop init.svc.vendor.audio-hal-aidl
getprop init.svc.audioserver
printf '\n== INPUT / ADAPTER IDENTITY\n'
sha256sum /vendor/lib64/a9-audio/liba9audioroute.so
cp /system/product/media/audio/ringtones/Andromeda.ogg /data/local/tmp/t.ogg || exit 1
chmod 0644 /data/local/tmp/t.ogg
dmesg >"$work/kernel-before" 2>&1
logcat -b main -b system -v threadtime -T 1 \
    'A9AudioRoute:V' 'AHAL_StreamAlsa:V' 'AHAL_Stream:V' \
    'AHAL_StreamPrimary:V' 'AHAL_ModulePrimary:V' 'AHAL_Module:V' \
    'AudioFlinger:I' 'AudioTrack:I' 'APM_AudioPolicyManager:I' '*:S' \
    >"$work/hal" 2>&1 &
hal_pid=$!
# Existing helper owns its private trace instance/probes and removes them.
/vendor/bin/a9-audio-fe-trace.sh "$work" >"$work/trace.log" 2>&1 &
trace_pid=$!
i=0
while [ "$i" -lt 15 ]; do
    grep -q 'Capturing FE events' "$work/trace.log" && break
    kill -0 "$trace_pid" 2>/dev/null || break
    sleep 0.1
    i=$((i + 1))
done
printf '\n== PLAYBACK START\n'
stamp
# Start sampling before am, so even the first short-lived PCM open is included.
(
    i=0
    while [ "$i" -lt 48 ]; do
        pcm_snapshot
        sleep 0.25
        i=$((i + 1))
    done
) >"$work/pcm" 2>&1 &
pcm_pid=$!
# Set ownership first so interrupted/failed am calls still stop this test player.
player_started=1
timeout 10 am start -a android.intent.action.VIEW -t audio/ogg -d file:///data/local/tmp/t.ogg \
    -n org.lineageos.twelve/.ViewActivity >"$work/am" 2>&1
am_rc=$?
cat "$work/am"
printf 'am_start_rc=%s\n' "$am_rc"
(
    sleep 0.5
    state_snapshot
    sleep 2
    state_snapshot
) >"$work/states" 2>&1 &
state_pid=$!
wait "$pcm_pid"; pcm_rc=$?; pcm_pid=
wait "$state_pid"; state_rc=$?; state_pid=
wait "$trace_pid"; trace_rc=$?; trace_pid=
# Capture ends before stopping the owned test player.
printf '\n== PLAYBACK CAPTURE END\n'
stamp
kill "$hal_pid" 2>/dev/null
wait "$hal_pid" 2>/dev/null
hal_pid=
printf '\n== PCM TIMELINE (status / hw_params / sw_params) rc=%s\n' "$pcm_rc"
cat "$work/pcm"
printf '\n== LIVE MIXER / DAPM / AMP SNAPSHOTS rc=%s\n' "$state_rc"
cat "$work/states"
printf '\n== FE TRACE SETUP / RESULT rc=%s\n' "$trace_rc"
cat "$work/trace.log"
printf '\n== FE TRACE\n'
cat "$work/frontend.trace" 2>&1
printf '\n== DMESG BEFORE\n'
cat "$work/kernel-before"
printf '\n== DMESG AFTER\n'
dmesg
printf '\n== HAL LOG (includes A9AudioRoute first returned error)\n'
cat "$work/hal"
printf '\n== PLAYBACK STOP\n'
am force-stop org.lineageos.twelve
player_started=0
stamp
printf 'A9 AUDIO LIVE END am_rc=%s trace_rc=%s\n' "$am_rc" "$trace_rc"
A9_AUDIO_CAPTURE
exit
A9_ROOT_SHELL
printf 'Audio capture saved: %s (adb rc=%s)\n' "$output" "$rc"
exit "$rc"
