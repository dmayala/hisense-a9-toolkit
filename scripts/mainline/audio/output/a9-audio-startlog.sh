#!/system/bin/sh
# Coordinator-invoked, one-shot private capture; never plays or selects audio.
set -eu
[ "$(id -u)" = 0 ] || { echo 'run from the existing root shell' >&2; exit 1; }
umask 077
out=${1:-/data/local/tmp/a9-audio-start-$(date +%Y%m%dT%H%M%S)}
mkdir -p "$out"
chmod 0700 "$out"
for tag in AudioPolicyManagerCustom APM_AudioPolicyManager AudioPolicyService APM_Config APM_EngineLoader DeviceHalAidl Hal2AidlMapper AHAL_Main AHAL_Config AHAL_Module AHAL_ModulePrimary AHAL_Stream AHAL_StreamAlsa AHAL_StreamPrimary FMQ ashmem; do
    setprop "log.tag.$tag" V
done
# Keep all tags: Android logcat tag filters don't expand an AHAL_* wildcard.
logcat -b main -b system -c
logcat -b main -b system -v threadtime -f "$out/startup.log" -r 2048 -n 2 '*:V' &
cap=$!
stopped=0
cleanup() {
    [ "$stopped" = 0 ] || start audioserver
    kill "$cap" 2>/dev/null || :
    wait "$cap" 2>/dev/null || :
}
trap cleanup EXIT
trap 'exit 130' INT TERM
sleep 1
stopped=1
stop audioserver
sleep 1
start audioserver
stopped=0
sleep 15
getprop sys.boot_completed > "$out/boot-completed.txt"
getprop vendor.a9.audio.ready > "$out/audio-ready.txt"
getprop sys.use_memfd > "$out/use-memfd.txt"
ls -l /dev/ashmem > "$out/ashmem-node.txt" 2>&1 || :
pidof audioserver > "$out/audioserver-pid.txt" || :
dumpsys media.audio_policy > "$out/audio-policy.txt"
dumpsys media.audio_flinger > "$out/audio-flinger.txt"
echo "Private startup logs and dumps: $out"
