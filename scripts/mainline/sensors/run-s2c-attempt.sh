set -euo pipefail
# S2c attempt (runs/sensors-s2c-prep.md): slot B boots the S2c TEST vendor (S2a vendor + hash-pinned
# sensors payload, persist.a9.sensors=1 in its build.prop) with a RAM /data. The vendor's own boot service
# (init.a9-sensors.rc, post-fs-data) brings the ADSP sensors PD up; the AIDL HAL registers; this script only
# OBSERVES it, reads the light sensor for 60 s through the public NDK (a9-sensor-read, shell user), collects
# the AVC denials, then CONTAINS (ADSP stop, listener gone, seed unchanged). Section 5 (run-s2c-sec5.sh)
# always follows and returns the phone to slot A + the golden restore.
#   bash run-s2c-attempt.sh            the attempt
#   bash run-s2c-attempt.sh --host-only   the host-only input checks, nothing else (no adb, no wrapper)
# No fd 9 from the caller may reach adb/wrapper children (review 79k section 7): closed here first. The
# wrapper's own lock (main's a9_usb_lock keeper, 966c853) starts the adb server before it locks.
exec 9>&-
HOST_ONLY=0; [ "${1:-}" = --host-only ] && HOST_ONLY=1
export NIX_CONFIG=$'substitute = false\nsubstituters =\nbuilders =\n'
export COMMIT=${S2C_COMMIT:?set S2C_COMMIT to the reviewed sensors-s2c-run commit}
export FROZEN=/home/user/A9-wt/sensors-s2c-run
test "$(git -C "$FROZEN" rev-parse HEAD)" = "$COMMIT"
test -z "$(git -C "$FROZEN" status --porcelain)"
test "$(df --output=avail -B1 / | tail -1)" -ge 15000000000
# the lock fix must be the one on main (a2d9563 line): a9-env.sh byte-identical to main's 966c853
cmp -s "$FROZEN/scripts/a9-env.sh" <(git -C /home/user/A9 show 966c853:scripts/a9-env.sh)
grep -q 'a9-usb-lock-keeper' "$FROZEN/scripts/a9-env.sh"

export A9_HOME=/home/user/A9
export A9_DEVICE_JSON=/home/user/A9/local/device.json
export ADB=/nix/store/q17av8m8k96y1yqqssbbpi9r8fnbyhp2-android-tools-35.0.2/bin/adb
export FB=/nix/store/q17av8m8k96y1yqqssbbpi9r8fnbyhp2-android-tools-35.0.2/bin/fastboot
test -x "$ADB" && test -x "$FB"

export W=/home/user/A9-build/mainline/sensors-s1
export ARTIFACTS="$W/build-20261018-s2c"
export S2A="$W/build-20261018-s2a"
export BUNDLE="$S2A/bundle"                     # S2a bundle: s1-host.py Transport (root loopback) only
export TUPLE=c0a4ad5aa8aeefd0749aef40b5041971f4a9dab4c29c4e504ccdd34e38ce41ae
export BOOT="$ARTIFACTS/android/out/a9-android-eink-boot-gpu.img"
export VENDOR="$ARTIFACTS/android/out/vendor_b-eink-gpu-bt-health-thermal-sensors.img"
export DTBO="$ARTIFACTS/snapshot/recovery_dtbo.img"
export CLIENT=/home/user/A9-build/mainline/sensors-s2c/client/a9-sensor-read
export STOCK_DUMP=/home/user/A9/runs/stock-dumpsys-sensorservice.txt
REVIEW=${S2C_REVIEW:-}
[ "$HOST_ONLY" = 1 ] || [ -n "$REVIEW" ] || { echo 'set S2C_REVIEW to the S2c review report (it must carry the line S2C-REVIEW-VERDICT: GO)' >&2; exit 2; }

# --- host-only input checks (computed, never typed): every pin of the S2c tuple
python3 -I - "$REVIEW" <<'PY'
from pathlib import Path
import os,json,hashlib,sys
def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''): h.update(b)
    return h.hexdigest()
e=os.environ; A=Path(e['ARTIFACTS']); B=Path(e['BUNDLE'])
if sys.argv[1]:
    # review 79l B2: an exact verdict line, exactly once; any NO-GO verdict line refuses
    lines=[l.rstrip('\r') for l in Path(sys.argv[1]).read_text().splitlines()]
    verdicts=[l for l in lines if l.startswith('S2C-REVIEW-VERDICT:')]
    assert verdicts==['S2C-REVIEW-VERDICT: GO'], ('review verdict lines', verdicts)
assert sha(e['BOOT'])=='35d0c7d47eb65cbd5e32cfe1d5826ee68a6a39e479ccf3f4041c87f7193cc595'
assert sha(e['DTBO'])=='cf2fe1f2b10d73f5f99d2972e9fecb87fd281c33d92cf305f5b4f41b55ec01d0'
vs=sha(e['VENDOR']); assert vs==Path(e['VENDOR']+'.sha256').read_text().split()[0]==os.environ.get('S2C_VENDOR_SHA256',vs)
r=json.loads(Path(e['VENDOR']+'.receipt.json').read_text())
assert r['status']=='PASS-OFFLINE-S2C-VENDOR' and r['sha256']==vs and r['test_switch'] is True
assert r['base']['sha256']=='4b08eadff33f64e749e01f6e24aa4c9224ee10b4e1567e86b04933929a39cf53'
assert r['boot']['sha256']==sha(e['BOOT']) and r['pins']['bundle_tuple']==e['TUPLE']
assert r['stage_json_sha256']==sha(A/'stage/stage.json')
assert r['pins']['hal']==sha(A/'stage/tree/bin/hw/android.hardware.sensors-service.a9')
# review 79l B1: the staged probe is this tree's build (receipt), its --wait mode ran under qemu (rc 1), not the S2a probe
st=json.loads((A/'stage/stage.json').read_text()); probe=A/'probe/s2-sns-probe'
pr=json.loads((A/'probe/s2-sns-probe.build.json').read_text())
assert sha(probe)==pr['output_sha256']==st['pins']['probe']==r['pins']['probe']==sha(A/'stage/tree/bin/s2-sns-probe')
assert st['pins']['probe']!='3bc3c6e65f2979b4d3830feb408a569d6129d82bdb4f5b84fabf2596cd8f11ca'
assert st['probe_exec']['wait_400_1']=={'rc':1,'stdout':'S2-WAIT-FAIL no-unique-sns-400'} and st['probe_exec']['usage_rc']==2
assert st['pins']['listener']=='21ec2234b54668bd652522592498f21e7cf991150ce9818cdd12c3972b2e8460' 
assert sha(B/'SHA256SUMS')==e['TUPLE']
assert sha(B/'s1-host.py')=='a2cdb353d9650c1dcaa76bedf2f7c5db65e9c504e6380c1587ea4e3df1c7c487'
c=sha(e['CLIENT']); assert c==Path(e['CLIENT']+'.sha256').read_text().split()[0]
assert Path(e['STOCK_DUMP']).is_file()
print('PASS S2c inputs: vendor',vs[:16],'HAL',r['pins']['hal'][:16],'probe',st['pins']['probe'][:16],'client',c[:16],'stage commit',r['pins']['repo_commit'][:12])
PY
# the stage (and so the vendor's repo files) came from this frozen tree's history
git -C "$FROZEN" merge-base --is-ancestor "$(python3 -I -c 'import json,sys;print(json.load(open(sys.argv[1]))["pins"]["repo_commit"])' "$VENDOR.receipt.json")" "$COMMIT"
for f in scripts/mainline/android/vendor/bin/a9-sensors.sh scripts/mainline/android/vendor/etc/init/init.a9-sensors.rc \
         scripts/mainline/android/sensors-hal/android.hardware.sensors-service.a9.rc scripts/mainline/android/sensors-hal/android.hardware.sensors-service.a9.xml; do
  cmp -s "$FROZEN/$f" "$ARTIFACTS/stage/tree/$(case $f in */a9-sensors.sh) echo bin/a9-sensors.sh;; */init.a9-sensors.rc) echo etc/init/init.a9-sensors.rc;; *.rc) echo etc/init/android.hardware.sensors-service.a9.rc;; *.xml) echo etc/vintf/manifest/android.hardware.sensors-service.a9.xml;; esac)"
done
if [ "$HOST_ONLY" = 1 ]; then echo 'HOST-ONLY CHECKS PASS (no adb, no wrapper)'; exit 0; fi

source "$FROZEN/scripts/a9-env.sh"
test -n "$A9_SERIAL"
case "$A9_SERIAL" in unknown-A9_DEVICE-*) exit 1;; esac
export A9_SERIAL ANDROID_SERIAL="$A9_SERIAL"
export SESSION
SESSION=$(mktemp -d "$W/s2c-session-XXXXXXXX")
printf '%s\n' "$SESSION" > "$W/current-session-s2c"
on_exit() {
  rc=$?
  [ "$rc" = 0 ] || echo "S2C SECTIONS 1-4 STOPPED rc=$rc: the boot wrapper may still hold slot B. Run run-s2c-sec5.sh now (SESSION=$SESSION)."
  printf '%s\n' "$rc" > "$SESSION/script-exit-status"
}
trap on_exit EXIT
sha256sum "$REVIEW" > "$SESSION/review.sha256"

mkdir -p "$SESSION/wrapper-android" "$SESSION/wrapper-mainline/out-android"
ln -s /home/user/A9-build/mainline/android/golden "$SESSION/wrapper-android/golden"
ln -s "$ARTIFACTS/snapshot" "$SESSION/wrapper-android/kernel-snap"
ln -s "$DTBO" "$SESSION/wrapper-mainline/out-android/recovery_dtbo.img"
export A9_ANDROID="$SESSION/wrapper-android"
export A9_MAINLINE="$SESSION/wrapper-mainline"
export A9_BASE_ANDROID_DTB=/home/user/A9-build/mainline/adsp/dt-ky9UCr0D/base.dtb
export A9_STOCK_BASE_DTS=/home/user/A9-build/mainline/sol-pmic/evidence/base_0.dts
export A9_GOLDEN=/home/user/a9-assets/Backups/edl_2026-10-17_102417
export GOLDEN="$A9_GOLDEN"
export BOOT_SH="$FROZEN/scripts/mainline/android/android-boot.sh"
BOOT_ARGS=(--eink --boot "$BOOT" --vendor "$VENDOR" --watch 600 --hold 3000 --no-restore)
declare -p NIX_CONFIG COMMIT FROZEN A9_HOME A9_DEVICE_JSON ADB FB A9_SERIAL ANDROID_SERIAL W \
  ARTIFACTS S2A BUNDLE TUPLE SESSION BOOT VENDOR DTBO CLIENT \
  A9_ANDROID A9_MAINLINE A9_BASE_ANDROID_DTB A9_STOCK_BASE_DTS A9_GOLDEN GOLDEN BOOT_SH \
  > "$SESSION/operator.env"
printf 'Retain this recovery environment: source %q\n' "$SESSION/operator.env"

# Host-only wrapper checks (incl. its protected-partition scan of the new vendor); golden data read only.
bash "$BOOT_SH" --offline "${BOOT_ARGS[@]}" | tee "$SESSION/offline-check.log"
grep -q '^OFFLINE CHECK: PASS' "$SESSION/offline-check.log"
( unset A9_ANDROID A9_MAINLINE A9_BASE_ANDROID_DTB A9_STOCK_BASE_DTS A9_GOLDEN GOLDEN BOOT_SH
  cd /home/user/A9 && bash scripts/restore-golden.sh ) | tee "$SESSION/golden-check.log"
bash /home/user/A9/scripts/a9-health.sh --json | tee "$SESSION/health-before.json"
bash "$BOOT_SH" "${BOOT_ARGS[@]}" | tee "$SESSION/boot-check.log"
CODE=$(sed -n 's/^CHECK: PASS. Approval code for exactly these images: \([0-9a-f]\{8\}\)$/\1/p' "$SESSION/boot-check.log")
test "${#CODE}" = 8
"$ADB" start-server </dev/null >/dev/null 2>&1

setsid bash "$BOOT_SH" --write --code "$CODE" "${BOOT_ARGS[@]}" > "$SESSION/boot-write.log" 2>&1 < /dev/null &
BOOT_PID=$!
printf '%s\n' "$BOOT_PID" > "$SESSION/boot-holder.pid"
printf 'Boot log: tail -F %q\n' "$SESSION/boot-write.log"
WAIT_END=$((SECONDS + 2400))
until grep -q '^-- HOLD: the phone stays on the test image' "$SESSION/boot-write.log"; do
  kill -0 "$BOOT_PID" || { cat "$SESSION/boot-write.log"; exit 1; }
  test "$SECONDS" -lt "$WAIT_END" || { echo 'Boot did not reach hold; use run-s2c-sec5.sh'; exit 1; }
  sleep 2
done
HOLD_SEEN=$SECONDS
grep -E 'boot_completed=|root:|HOLD:|gates stay CLOSED' "$SESSION/boot-write.log"
timeout 10 "$ADB" devices -l > "$SESSION/transports.txt" </dev/null
export A9_TRANSPORT_ID
A9_TRANSPORT_ID=$(python3 -I - "$SESSION/transports.txt" "$A9_SERIAL" <<'PYID'
from pathlib import Path
import re,sys
rows=[l.split() for l in Path(sys.argv[1]).read_text().splitlines() if l.split() and l.split()[0]==sys.argv[2]]
assert len(rows)==1 and rows[0][1]=='device'
ids=[v.removeprefix('transport_id:') for v in rows[0] if v.startswith('transport_id:')]
assert len(ids)==1 and re.fullmatch('[1-9][0-9]*',ids[0])
print(ids[0])
PYID
)
test "$(timeout 10 "$ADB" -t "$A9_TRANSPORT_ID" get-serialno </dev/null | tr -d '\r')" = "$A9_SERIAL"
export S1_BOOT_ID
S1_BOOT_ID=$(timeout 10 "$ADB" -t "$A9_TRANSPORT_ID" shell -T 'cat /proc/sys/kernel/random/boot_id' </dev/null | tr -d '\r')
python3 -I -c 'import re,sys; assert re.fullmatch("[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}",sys.argv[1])' "$S1_BOOT_ID"
declare -p A9_TRANSPORT_ID S1_BOOT_ID >> "$SESSION/operator.env"

# Checked root calls (same loopback Transport as S1/S2a: UID 0 and boot id verified, rc by nonce).
R() {
  local log
  log=$(mktemp "$SESSION/root-step-XXXXXXXX.log") || return $?
  python3 -I - "$BUNDLE" "$ADB" "$A9_TRANSPORT_ID" "$A9_SERIAL" "$S1_BOOT_ID" "$log" "$1" <<'PYROOT' || return $?
import importlib.util,sys
sys.dont_write_bytecode=True
from pathlib import Path
spec=importlib.util.spec_from_file_location('s1_host',Path(sys.argv[1])/'s1-host.py')
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
t=mod.Transport(Path(sys.argv[2]),sys.argv[3],sys.argv[4],'loopback',sys.argv[5])
sys.exit(t.execute(['/system/bin/sh','-c',sys.argv[7]],120,Path(sys.argv[6])))
PYROOT
  cat "$log"
}
S() { timeout "${2:-30}" "$ADB" -t "$A9_TRANSPORT_ID" shell -T "$1" </dev/null | tr -d '\r'; }   # shell user
note() { printf '%s\n' "$*" | tee -a "$SESSION/checks.txt"; }
# From here on a failing observation must never skip the containment (step 9) or section 5: errors are
# recorded by check/note, not by set -e.
set +e
FAILS=()
check() { local name=$1; shift; if "$@"; then note "ok   $name"; else note "FAIL $name"; FAILS+=("$name"); fi; }
: > "$SESSION/checks.txt"
printf 'RUNNING\n' > "$SESSION/result"

# 1. the boot: slot B, booted, the S1/S2a kernel, RAM /data, the S2c vendor's switch
check slot-b-booted test "$(S 'getprop ro.boot.slot_suffix; getprop sys.boot_completed' | tr '\n' ' ')" = '_b 1 '
check kernel test "$(S 'uname -r')" = "$(cat "$BUNDLE/kernel.release")"
R 'awk '\''$2=="/data" {print $1,$3}'\'' /proc/mounts' | tee "$SESSION/data-mount.txt"
check data-ram0 grep -Eqx '/dev/(block/)?ram0 ext4' "$SESSION/data-mount.txt"
check switch test "$(S 'getprop ro.vendor.a9ml.sensors; getprop persist.a9.sensors' | tr '\n' ' ')" = '1 1 '

# 2. the boot service: wait for up (or a terminal state), at most 180 s after HOLD
end=$((SECONDS + 180)); st=
while [ "$SECONDS" -lt "$end" ]; do
  st=$(S 'getprop vendor.a9.sensors.state')
  case "$st" in up|fail:*|down:*|off) break;; esac
  sleep 3
done
S 'getprop | grep -E "a9\.sensors|ro\.boottime\.vendor\.(a9-sensors|sensors-a9)|init\.svc\.vendor\.(a9-sensors|sensors-a9)|init\.svc_debug_pid\.vendor\.(a9-sensors|sensors-a9)"' \
  | tee "$SESSION/sensor-props.txt"
check state-up test "$st" = up
check ready-1 test "$(S 'getprop vendor.a9.sensors.ready')" = 1

# 3. timings + the HAL registration (N5): init's service start stamps, the kernel log of a9-sensors.sh,
#    the HAL's own log lines (logcat, monotonic), sensorservice's start
R 'cat /proc/uptime; dmesg | grep -E "a9-sensors:|remoteproc|fastrpc|qcom_q6v5_pas" ' > "$SESSION/kmsg-sensors.txt" 2>&1 || true
S 'logcat -d -b all -v monotonic,uid' 120 > "$SESSION/logcat-all.txt" || true
grep -E 'sensors-a9|sensors-service\.a9|android\.hardware\.sensors|SensorService|SensorDevice|a9-sensors' "$SESSION/logcat-all.txt" > "$SESSION/logcat-sensors.txt" || true
# S2c result1: the HAL's own liblog lines (tag sensors-a9) never reached logcat in attempt 1 -- nor did the
# thermal HAL's (thermal-a9) -- so registration and discovery are judged from sources that do not depend
# on them (sensors-s2c-result1.md):
#  * registration: servicemanager's kernel-log line for an addService by the HAL's own pid in
#    hal_sensors_default (Caller(pid=<init.svc_debug_pid.vendor.sensors-a9>, ...) Found ...ISensors/default),
#    exactly one such caller line (no second registration), `service check` finds it, and the HAL pid is
#    unchanged at the end (no restart);
#  * discovery: dumpsys "Total 1 h/w sensors" (the dumpsys-light check compares the line with stock).
#  The HAL's own lines are counted and reported, not judged.
HALPID=$(S 'getprop init.svc_debug_pid.vendor.sensors-a9')
grep -E "servicemanager: Caller\(pid=[0-9]+,uid=[0-9]+,sid=u:r:hal_sensors_default:s0\) Found android\.hardware\.sensors\.ISensors/default" \
  "$SESSION/logcat-all.txt" | tee "$SESSION/hal-registration.txt"
S 'service check android.hardware.sensors.ISensors/default' | tee -a "$SESSION/hal-registration.txt"
check hal-registered test "$(grep -c "Caller(pid=$HALPID,uid=1000,sid=u:r:hal_sensors_default:s0) Found" "$SESSION/hal-registration.txt"):$(grep -c 'sid=u:r:hal_sensors_default:s0' "$SESSION/hal-registration.txt"):$(grep -c ': found' "$SESSION/hal-registration.txt")" = 1:1:1
note "HAL pid $HALPID; own log lines (tag sensors-a9): $(grep -c ' sensors-a9 *:' "$SESSION/logcat-all.txt") (reported, not judged)"
note "timing (boottime s): a9-sensors start $(S 'getprop ro.boottime.vendor.a9-sensors' | awk '{printf "%.2f", $1/1e9}'), HAL start $(S 'getprop ro.boottime.vendor.sensors-a9' | awk '{printf "%.2f", $1/1e9}'); state-up and the addService line are in kmsg-sensors.txt / hal-registration.txt"

# 4. the first tmpfs persist (size=8m, mode=0700, nosuid,nodev,noexec), nothing else at or under it
R 'awk '\''$2 ~ /^\/mnt\/vendor\/persist/ {print $1,$2,$3,$4}'\'' /proc/mounts' | tee "$SESSION/persist-mounts.txt"
check tmpfs-persist python3 -I - "$SESSION/persist-mounts.txt" <<'PY'
import sys
rows=[l.split() for l in open(sys.argv[1]) if l.strip()]
assert len(rows)==1, rows
dev,pt,fs,opts=rows[0]; o=opts.split(',')
assert (dev,pt,fs)==('tmpfs','/mnt/vendor/persist','tmpfs'), rows
assert 'size=8192k' in o and 'mode=700' in o and {'nosuid','nodev','noexec'} <= set(o), o
PY
R '! grep -qE "by-name/persist|/dev/block/[^ ]*persist" /proc/mounts' > /dev/null && note 'ok   no real persist mounted' || { note 'FAIL real-persist'; FAILS+=(real-persist); }

# 5. dumpsys sensorservice: exactly one light sensor, the stock name/vendor/handle/version/flags (C4), no '?'
S 'dumpsys sensorservice' 60 > "$SESSION/dumpsys-sensorservice.txt" || true
check hal-discovered-1 grep -qx 'Total 1 h/w sensors, [0-9]* running [0-9]* disabled clients:' "$SESSION/dumpsys-sensorservice.txt"
check dumpsys-light python3 -I - "$SESSION/dumpsys-sensorservice.txt" "$STOCK_DUMP" <<'PY'
import re,sys
cur=open(sys.argv[1]).read().splitlines(); stock=open(sys.argv[2]).read().splitlines()
def lights(lines):
    return [(l, lines[i+1] if i+1<len(lines) else '') for i,l in enumerate(lines) if re.match(r'0x[0-9a-f]{8}\) ',l) and 'type: android.sensor.light(5)' in l]
mine=lights(cur)
print('mainline light sensors:', mine)
assert len(mine)==1, 'want exactly one light sensor'
l,detail=mine[0]
want=[s for s in lights(stock) if 'Non-wakeup' in s[0]][0]
norm=lambda s: re.sub(r'\s+',' ',s).strip()
print('stock:', want)
assert '?' not in l, 'a ? in name/vendor (F1)'
assert norm(l)==norm(want[0]), ('header differs from stock', norm(l), norm(want[0]))
assert norm(detail)==norm(want[1]), ('detail differs from stock', norm(detail), norm(want[1]))
PY

# 6. the 60 s light read through the NDK, as the shell user
timeout 30 "$ADB" -t "$A9_TRANSPORT_ID" push "$CLIENT" /data/local/tmp/a9-sensor-read </dev/null
check client-sha test "$(S 'sha256sum /data/local/tmp/a9-sensor-read' | cut -d' ' -f1)" = "$(cut -d' ' -f1 "$CLIENT.sha256")"
S 'chmod 0755 /data/local/tmp/a9-sensor-read; /data/local/tmp/a9-sensor-read 60; echo "client-rc=$?"' 120 | tee "$SESSION/light-read.txt"
check light-read-pass grep -qx 'S2C-READ-RESULT PASS' "$SESSION/light-read.txt"
check light-default-is-ours grep -q '^S2C-LIGHT-DEFAULT handle=0x33 name="stk_stk3b6x Ambient Light Sensor Non-wakeup"' "$SESSION/light-read.txt"
RATE=$(sed -n 's/^S2C-READ .* rate_hz=\([0-9.]*\) .*/\1/p' "$SESSION/light-read.txt")
note "light rate ${RATE:-none} Hz (S2a measured ~8 Hz on-change; 4-12 Hz expected, reported not judged)"

check hal-not-restarted test "$(S 'getprop init.svc_debug_pid.vendor.sensors-a9')" = "$HALPID"

# 7. AVC denials (C3, permissive): all, and the HAL/service ones (QRTR socket as uid system, the
#    vendor.a9.sensors.* properties, servicemanager/vintf)
R 'dmesg | grep "avc: " ; true' > "$SESSION/avc-kmsg.txt" 2>&1 || true
grep -h 'avc: ' "$SESSION/logcat-all.txt" > "$SESSION/avc-logcat.txt" || true
# classified (hal / service / firmware / lookups / other), not a broad grep: result1 counted the harness's
# own root-shell (phhsu_daemon) service lookups as "sensors" denials
python3 -I "$FROZEN/scripts/mainline/sensors/s2c-avc.py" "$SESSION/avc-kmsg.txt" "$SESSION/avc-logcat.txt" > "$SESSION/avc-sensors-summary.txt" 2>&1 || true
note "AVC: $(grep -E '^(unique|== )' "$SESSION/avc-sensors-summary.txt" | tr '\n' ';')"

# 8. the service's own evidence
R 'ls -l /data/vendor/a9-sensors; for f in /data/vendor/a9-sensors/*; do echo "== $f"; tail -n 80 "$f"; done; true' > "$SESSION/a9-sensors-logs.txt" 2>&1 || true

# 9. containment: stop the ADSP (the supervisor then reports down:adsp-offline and exits), kill the
#    listener by exact pid, verify: remoteproc offline, no s1-listener, the RAM seed still matches the
#    vendor's registry.sha256, the vendor seed intact. Never touches anything outside the tmpfs.
R 'set -u
rp=; for p in /sys/class/remoteproc/remoteproc*; do [ "$(basename "$(readlink -f "$p/device/of_node")")" = remoteproc@a400000 ] && rp=$p; done
[ -n "$rp" ] || { echo "CONTAIN no-a400000"; exit 1; }
echo "before: $(cat "$rp/state")"
[ "$(cat "$rp/state")" = offline ] || echo stop > "$rp/state"
i=0; while [ "$(cat "$rp/state")" != offline ] && [ $i -lt 50 ]; do sleep 0.2; i=$((i+1)); done
echo "adsp: $(cat "$rp/state")"
for p in $(pgrep -f "^/vendor/bin/s1-listener --service "); do kill "$p"; done
sleep 1
for p in $(pgrep -f "^/vendor/bin/s1-listener --service "); do kill -9 "$p"; done
sleep 0.5
echo "listener: $(pgrep -f "^/vendor/bin/s1-listener" | wc -l)"
(cd / && sed "s#  /mnt/vendor/persist/#  mnt/vendor/persist/#" /vendor/etc/a9-sensors/registry.sha256 | sha256sum -c >/dev/null) && echo "seed: ram-copy-ok" || echo "seed: ram-copy-CHANGED"
(cd /vendor/etc/a9-sensors && sha256sum -c SHA256SUMS >/dev/null) && echo "vendor-seed: ok" || echo "vendor-seed: CHANGED"
sleep 6; echo "state: $(getprop vendor.a9.sensors.state) ready: $(getprop vendor.a9.sensors.ready)"' | tee "$SESSION/containment.txt"
CONTAINED=0
grep -qx 'adsp: offline' "$SESSION/containment.txt" && grep -qx 'listener: 0' "$SESSION/containment.txt" \
  && grep -qx 'seed: ram-copy-ok' "$SESSION/containment.txt" && grep -qx 'vendor-seed: ok' "$SESSION/containment.txt" && CONTAINED=1
check containment test "$CONTAINED" = 1

kill -0 "$BOOT_PID"
if [ "$CONTAINED" != 1 ]; then RESULT=CONTAINMENT-UNVERIFIED
elif [ "${#FAILS[@]}" = 0 ]; then RESULT=PASS-S2C
else RESULT="FAIL-S2C ${FAILS[*]}"; fi
printf '%s\n' "$RESULT" | tee "$SESSION/result"
printf 'S2c attempt finished: %s. Now run run-s2c-sec5.sh (always).\n' "$RESULT"
