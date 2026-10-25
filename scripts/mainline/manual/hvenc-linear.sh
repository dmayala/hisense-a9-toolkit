#!/usr/bin/env bash
# User-run single attempt on an already booted B. No reboot or policy changes.
set -euo pipefail
umask 077
here=$(cd -- "$(dirname -- "$0")" && pwd)
. "$here/common.sh"
bundle=${1:?usage: hvenc-linear.sh BUNDLE_DIR NEW_OUTPUT_DIR [mmap|system]}
out=${2:?usage: hvenc-linear.sh BUNDLE_DIR NEW_OUTPUT_DIR [mmap|system]}
mode=${3:-mmap}
[[ $mode == mmap || $mode == system ]] || exit 2
[[ -f $bundle/linear ]]
mkdir "$out"
[[ $(shell 'getprop ro.boot.slot_suffix' | tr -d '\r') == _b ]]
original=$(root 'p=/sys/module/venus_enc/parameters
for n in a9_mapped_eos a9_bidir_buffers a9_output_size a9_stock_work_mode; do
    printf "%s %s\n" "$n" "$(cat "$p/$n")"
done')
printf '%s\n' "$original" > "$out/original-parameters.txt"
restore='p=/sys/module/venus_enc/parameters'
while read -r name value; do
    [[ $name =~ ^a9_(mapped_eos|bidir_buffers|output_size|stock_work_mode)$ && $value =~ ^[YN]$ ]] || exit 1
    restore+=$'\n'"printf '$value\\n' > \"\$p/$name\""
done <<< "$original"
reader= transport_lost=0
cleanup() {
    local rc=$?
    if [[ -n $reader ]]; then wait "$reader" || true; reader=; fi
    if [[ $transport_lost == 0 ]]; then
        root "$restore" > "$out/reset.log" 2>&1 || rc=1
    else
        echo 'Transport lost; original-boot parameter restoration unconfirmed. No follow-on attempt.' > "$out/reset.log"
    fi
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
remote=$(shell 'mktemp -d /data/local/tmp/hvenc-linear.XXXXXX' | tr -d '\r')
[[ $remote =~ ^/data/local/tmp/hvenc-linear\.[A-Za-z0-9]+$ ]]
printf '%s\n' "$remote" > "$out/remote.txt"
"$ADB" push "$bundle/linear" "$remote/linear" > "$out/push.log" 2>&1
shell "chmod 755 $remote/linear"
root 'p=/sys/module/venus_enc/parameters
printf "Y\n" > "$p/a9_mapped_eos"
printf "Y\n" > "$p/a9_bidir_buffers"
printf "N\n" > "$p/a9_output_size"
printf "N\n" > "$p/a9_stock_work_mode"
for n in a9_mapped_eos a9_bidir_buffers a9_output_size a9_stock_work_mode; do printf "%s %s\n" "$n" "$(cat "$p/$n")"; done' > "$out/parameters.txt"

# A single nc connection owns both the native attempt and its kernel reader.
# Root nc is serial; a second root connection would wait behind the reader.
payload="timeout 40 dmesg -w &
k=\$!
timeout -k 2 32 $remote/linear $mode $remote/output.h264
rc=\$?
wait \"\$k\"; kr=\$?
printf '\\nHVENC_LINEAR_READER_RC=%s\\nHVENC_LINEAR_COMMAND_RC=%s\\n' \"\$kr\" \"\$rc\"
exit"
printf -v quoted '%q' "$payload"
timeout --kill-after=2 47 "$ADB" shell -T 'timeout 42 logcat -b all -v threadtime -T 100' > "$out/logcat-live.log" 2>&1 &
reader=$!
if printf 'sh -c %s; exit\n' "$quoted" |
    timeout --kill-after=2 47 "$ADB" shell -T 'timeout 45 nc 127.0.0.1 2324' > "$out/attempt.log" 2>&1; then
    transport_rc=0
else transport_rc=$?; fi
printf '%s\n' "$transport_rc" > "$out/transport.rc"
if wait "$reader"; then logcat_rc=0; else logcat_rc=$?; fi
reader=
printf '%s\n' "$logcat_rc" > "$out/logcat-live.rc"
sed -n '/^\[ *[0-9]/p' "$out/attempt.log" > "$out/kernel-live.log"
command_rc=$(sed -n 's/^HVENC_LINEAR_COMMAND_RC=//p' "$out/attempt.log" | tr -d '\r')
if [[ $transport_rc != 0 || ! $command_rc =~ ^[0-9]+$ ]]; then
    transport_lost=1
    echo 'Incomplete transport; collect saved boot logs. No retry was started.' >&2
    exit 1
fi
printf '%s\n' "$command_rc" > "$out/native.rc"
[[ $command_rc == 0 ]] || exit 1
root "base64 $remote/output.h264" > "$out/output.b64"
base64 --decode "$out/output.b64" > "$out/output.h264"
rm "$out/output.b64"
# Independently decode the synthetic access unit on the host; a VCL marker
# alone is not proof that firmware produced a valid frame.
ffmpeg=/nix/store/j1b3q6akcmxjamapv9qac6ibpd4g1d8b-ffmpeg-headless-7.1.1-bin/bin/ffmpeg
[[ -x $ffmpeg ]] || ffmpeg=ffmpeg
"$ffmpeg" -nostdin -v error -xerror -i "$out/output.h264" -f rawvideo -pix_fmt yuv420p "$out/decoded.yuv" > "$out/decode.log" 2>&1
[[ $(stat -c %s "$out/decoded.yuv") == 115200 ]]
echo "HVENC_LINEAR_HOST_DECODE_PASS mode=$mode output=$out"
