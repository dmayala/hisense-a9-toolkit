# Capture B while it is still hanging — agent only

Do not wait for `boot_completed` or for the persistence checker. Capture while
slot B is alive, before returning A. No capture command below changes SELinux,
clears logs, restarts a service, formats a filesystem or writes a phone file.
Host outputs are private and must stay out of git.

R2 starts the existing `a9ml_rootsh` in late-fs **before** the mapper. The later
boot-completed start remains harmless/idempotent. This is still the existing
loopback2324 development shell/domain, not a new listener or permission mode.
The mapper now writes phases/errors directly to kmsg, without depending on
`/data` or logd. A failure before adb/USB is available cannot be collected over
adb; do not infer absence of an AVC from an empty/denied capture.

Immediately after the owner reboots B, run on the host (bash):

```bash
set -o pipefail
O=/home/user/A9-build/mainline/storage-row29-data-r2-20261021
E=/home/user/A9-build/mainline/vendor-test-20261020/evidence-row29-data-r2-$(date -u +%Y%m%dT%H%M%SZ)
mkdir -m 700 "$E"
# Wait only for B's adb shell, at most120s; no boot_completed prerequisite.
for i in $(seq 1 24); do
  slot=$(timeout 3 adb shell -T getprop ro.boot.slot_suffix 2>/dev/null | tr -d '\r')
  [ "$slot" = _b ] && break
  sleep 2
done
test "$slot" = _b || { echo 'No B adb shell: save bootloader/USB status; no B log capture yet'; exit 1; }
# Start the log stream early; bound only this client to600s/16MiB (bash limits).
(ulimit -c 0; ulimit -f 16384; exec timeout 600 adb logcat -b all -v threadtime \
  >"$E/logcat-live.txt" 2>"$E/logcat-live.err") &
LOGCLIENT=$!
```

Take an immediate snapshot, then repeat while the boot is stuck (for example
at30,120,300 and540 seconds). Each snapshot has a host deadline. `adb shell`
may already be root; collect that route even if nc is not ready:

```bash
T=$(date -u +%H%M%S)
timeout 20 adb shell -T 'id; getenforce; getprop ro.boot.slot_suffix; getprop sys.boot_completed; dmesg' \
  >"$E/direct-$T.txt" 2>"$E/direct-$T.err"
echo "direct rc=$?" >>"$E/status.txt"
timeout 35 adb shell -T 'timeout 30 nc 127.0.0.1 2324' \
  <"$O/capture-phone.sh" >"$E/root-$T.txt" 2>"$E/root-$T.err"
echo "root rc=$?" >>"$E/status.txt"
timeout 15 adb logcat -b all -d -v threadtime -t 3000 \
  >"$E/logcat-$T.txt" 2>"$E/logcat-$T.err"
echo "logcat rc=$?" >>"$E/status.txt"
```

If nc times out, keep direct adb/logcat results and retry a snapshot; do not
discard stderr or create a marker. Require `ROW29_CAPTURE_BEGIN/END` to call a
root snapshot complete. The snapshot includes context/boot-ID/cmdline/enforce,
mapper/rootsh/logd/vold/zygote init states, actual node/root inode labels,
mountinfo/table, complete dmesg and recent logcat. It can fail or end partially
without `/data`; that itself is evidence. Do not rerun the mapping helper or
`mount_all` during collection: those would change the state being diagnosed.

Inspect saved host files without throwing away full originals:

```bash
rg -n -i 'avc:|ROW29|a9ml_row29|cannot exec|cannot enter|setexeccon|mount_all|fs_mgr|f2fs|failed to mount|service.*exited|init.*fatal' "$E"/*.txt >"$E/focus.txt" || :
# Before the owner's normal return/undo, end only our logcat client if needed:
kill "$LOGCLIENT" 2>/dev/null || :
wait "$LOGCLIENT" 2>/dev/null || :
```

Avoid `setenforce`, `auditctl`, `dmesg -c`, `logcat -c` or a service restart in
this capture. No new boot/flash is hidden in these commands. Once on A,
`dmesg` is A's current kernel, not the lost B boot. If needed, inspect retained
pstore through the owner's established read-only root route on A:
`ls -l /sys/fs/pstore; cat /sys/fs/pstore/*` (private host redirection only).
Empty pstore does not prove the failed boot had no kernel/SELinux error.
