#!/usr/bin/env bash
# Run by the coordinator on an already booted B. No lifecycle or flashing.
set -euo pipefail
umask 077
here=$(cd -- "$(dirname -- "$0")" && pwd)
. "$here/common.sh"
image=${1:?usage: hvenc.sh IMAGE_DIR [OUTPUT_DIR]}
out=${2:-"$PWD/manual-hvenc-$(date -u +%Y%m%dT%H%M%SZ)"}
selection=${3:-NY}
case $selection in N) cases=(N);; Y) cases=(Y);; NY) cases=(N Y);;
    *) echo 'usage: hvenc.sh IMAGE_DIR [OUTPUT_DIR] [N|Y|NY]' >&2; exit 2;; esac
kernel_reader= logcat_reader=
finish_readers() {
    local rc
    if [[ -n $kernel_reader ]]; then
        if wait "$kernel_reader"; then rc=0; else rc=$?; fi
        printf '%s\n' "$rc" > "$out/$bidir-kernel-live.rc"
        kernel_reader=
    fi
    if [[ -n $logcat_reader ]]; then
        if wait "$logcat_reader"; then rc=0; else rc=$?; fi
        printf '%s\n' "$rc" > "$out/$bidir-logcat-live.rc"
        logcat_reader=
    fi
}
mkdir -p "$out"
[[ $(shell 'getprop ro.boot.slot_suffix' | tr -d '\r') == _b ]]
original=$(shell 'getprop debug.stagefright.a9.enc_cpu_read' | tr -d '\r')
printf -v original_q '%q' "$original"
cleanup() {
    local rc=$?
    # Root nc is serial: let its bounded reader release the endpoint before
    # restoring parameters. Readers have their own remote and host timeouts.
    finish_readers
    root 'p=/sys/module/venus_enc/parameters; rc=0
for n in a9_mapped_eos a9_bidir_buffers a9_output_size a9_stock_work_mode; do
    printf "N\n" > "$p/$n" || rc=1
done
exit "$rc"' > "$out/reset.log" 2>&1 || rc=1
    shell "setprop debug.stagefright.a9.enc_cpu_read $original_q" >> "$out/reset.log" 2>&1 || rc=1
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
# Property service belongs to shell, not the confined native root domain.
shell 'set -e; setprop debug.stagefright.a9.enc_cpu_read 1; getprop debug.stagefright.a9.enc_cpu_read' > "$out/cpu-read.txt"
remote=$(shell 'mktemp -d /data/local/tmp/manual-hvenc.XXXXXX' | tr -d '\r')
printf '%s\n' "$remote" > "$out/remote.txt"
"$ADB" push "$image/hvenc-qualification/qualify" "$image/hvenc-qualification/avc.mp4" "$image/hvenc-qualification/hevc.mp4" "$remote/"
shell "chmod 755 $remote/qualify"
failed=0
for bidir in "${cases[@]}"; do
    root "p=/sys/module/venus_enc/parameters
printf 'Y\n' > \"\$p/a9_mapped_eos\"
printf '$bidir\n' > \"\$p/a9_bidir_buffers\"
printf 'N\n' > \"\$p/a9_output_size\"
printf 'N\n' > \"\$p/a9_stock_work_mode\"
for n in a9_mapped_eos a9_bidir_buffers a9_output_size a9_stock_work_mode; do printf '%s ' \"\$n\"; cat \"\$p/\$n\"; done" > "$out/$bidir-parameters.txt"
    shell "mkdir $remote/$bidir; cp $remote/avc.mp4 $remote/hevc.mp4 $remote/$bidir/"
    # Stream directly to host files: root() buffers its reply and would lose a
    # partial tail on transport death. No parameter writes use this nc endpoint
    # until the bounded kernel reader has returned.
    (printf 'timeout 60 dmesg -w; r=$?; printf "\\nHVENC_KERNEL_RC=%%s\\n" "$r"; exit\n' |
        timeout --kill-after=2 65 "$ADB" shell -T 'timeout 62 nc 127.0.0.1 2324') > "$out/$bidir-kernel-live.log" 2>&1 &
    kernel_reader=$!
    timeout --kill-after=2 65 "$ADB" shell -T 'timeout 60 logcat -b all -v threadtime -T 100' > "$out/$bidir-logcat-live.log" 2>&1 &
    logcat_reader=$!
    # A separate invocation creates fresh codec instances. Y-only avoids
    # repeating an N case that has already reset the whole phone.
    if shell "timeout -k 2 55 $remote/qualify $remote/$bidir 2>&1" > "$out/$bidir.log"; then rc=0; else rc=$?; failed=1; fi
    printf '%s\n' "$rc" > "$out/$bidir.rc"
    finish_readers
    # A transport loss ends this script; it is not a clean codec failure on
    # which to schedule the other mapping case automatically.
    if [[ $rc == 255 ]]; then
        echo "Transport lost in $bidir; collect saved boot logs before another attempt." >&2
        exit "$rc"
    fi
    "$ADB" pull "$remote/$bidir/output.mp4" "$out/$bidir.mp4" || true
    root dmesg > "$out/$bidir-dmesg.log" || true
    printf 'bidir=%s rc=%s output=%s\n' "$bidir" "$rc" "$out"
done
exit "$failed"
