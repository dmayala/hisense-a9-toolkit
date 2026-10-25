#!/system/bin/sh
# Optional coordinator diagnostic. No playback, routing, gain or service changes.
set -efu
[ "$(id -u)" = 0 ] || { echo 'run from existing root shell' >&2; exit 1; }
root=/sys/kernel/tracing
[ -w "$root/kprobe_events" ] || root=/sys/kernel/debug/tracing
[ -w "$root/kprobe_events" ] || { echo 'writable tracefs/kprobes unavailable' >&2; exit 1; }
group=a9_audio_diag
instance=$root/instances/$group
[ ! -e "$instance" ] && [ ! -e "$root/events/$group" ] || {
    echo 'audio diagnostic trace already exists; leave its owner in control' >&2; exit 1;
}
umask 077
out=${1:-/data/local/tmp/a9-audio-fe-$(date +%Y%m%dT%H%M%S)}
mkdir -p "$out"
chmod 0700 "$out"
mkdir "$instance"
events=
cleanup() {
    echo 0 > "$instance/tracing_on" 2>/dev/null || :
    [ ! -e "$instance/events/$group/enable" ] || echo 0 > "$instance/events/$group/enable" || :
    for event in $events; do echo "-:$event" >> "$root/kprobe_events" || :; done
    rmdir "$instance" 2>/dev/null || :
}
trap cleanup EXIT
trap 'exit 130' INT TERM
while IFS= read -r probe; do
    echo "$probe" >> "$root/kprobe_events"
    event=${probe%% *}
    events="$events ${event#*:}"
done <<'PROBES'
p:a9_audio_diag/prepare q6asm_dai:q6asm_dai_prepare
r:a9_audio_diag/prepare_ret q6asm_dai:q6asm_dai_prepare rc=$retval:s32
p:a9_audio_diag/trigger q6asm_dai:q6asm_dai_trigger cmd=%x2:s32
p:a9_audio_diag/open q6asm:q6asm_open_write stream=%x1:u32
r:a9_audio_diag/open_ret q6asm:q6asm_open_write rc=$retval:s32
p:a9_audio_diag/route q6routing:q6routing_stream_open fe=%x0:s32 session=%x2:s32 direction=%x3:s32
r:a9_audio_diag/route_ret q6routing:q6routing_stream_open rc=$retval:s32
p:a9_audio_diag/matrix q6adm:q6adm_matrix_map
r:a9_audio_diag/matrix_ret q6adm:q6adm_matrix_map rc=$retval:s32
p:a9_audio_diag/run q6asm:q6asm_run_nowait stream=%x1:u32
r:a9_audio_diag/run_ret q6asm:q6asm_run_nowait rc=$retval:s32
p:a9_audio_diag/write q6asm:q6asm_write_async stream=%x1:u32 bytes=%x2:u32
r:a9_audio_diag/write_ret q6asm:q6asm_write_async rc=$retval:s32
p:a9_audio_diag/event q6asm_dai:event_handler opcode=%x0:u32 token=%x1:u32
PROBES
echo 64 > "$instance/buffer_size_kb"
echo 1 > "$instance/events/$group/enable"
echo 1 > "$instance/tracing_on"
echo 'Capturing FE events for 8 seconds; playback is not invoked.'
sleep 8
echo 0 > "$instance/tracing_on"
cat "$instance/trace" > "$out/frontend.trace"
echo "Private FE trace: $out/frontend.trace"
