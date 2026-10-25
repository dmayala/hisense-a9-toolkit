#!/usr/bin/env bash
# test-sensors-setup.sh -- host test of vendor/bin/a9-sensors.sh against fake roots (S2b).
# Must hold (FAIL CLOSED, NEVER REBOOT):
#  - inert without ro.vendor.a9ml.sensors=1; kill switch off (persist.a9.sensors != 1) -> state off,
#    no mount, no insmod, no ADSP write;
#  - pass beside physical persist RO and its alias: manifest, private tmpfs + seed hash,
#    listener writes only the RAM registry, firmware verified, modules with
#    options and read-back gates, the single a400000 ADSP with recovery/coredump disabled, the
#    listener started with --service <boot> <manifest token>, SNS wait -> ready=1, state up;
#  - supervision: ADSP crash -> ready 0, down:adsp-crashed (no restart); listener death -> ADSP
#    stopped, down:listener-exited;
#  - refusals: /data is neither RAM ext4 nor checked ROW29 f2fs (nothing touched, not even
#    /data/vendor), private registry already mounted, seed tamper, manifest tamper,
#    ADSP not offline, wrong remoteproc, fastrpc gate N, listener never ready (ADSP stopped),
#    no SNS (listener killed, ADSP stopped).
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
SCRIPT=$HERE/vendor/bin/a9-sensors.sh
fails=0
ok() { echo "  ok   $1"; }
bad() { echo "  FAIL $1"; fails=$((fails+1)); }
BG=()
cleanup() { for p in "${BG[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null; done; }
trap cleanup EXIT

# mkroot <case>
mkroot() {
  local c=$1
  R=$(mktemp -d)
  mkdir -p "$R/stubs" "$R/sys/module/firmware_class/parameters" "$R/vendor/lib/modules" "$R/vendor/bin" \
    "$R/vendor/etc/a9-sensors/soc0" "$R/vendor/etc/sensors/config" "$R/vendor/firmware/a9adsp/qcom/sm6115/hisense/hlte556n" \
    "$R/data" "$R/dev" "$R/proc/sys/kernel/random" "$R/mnt/vendor/a9-sensors" \
    "$R/sys/firmware/devicetree/base/soc@0/remoteproc@a400000" "$R/sys/devices/rp/remoteproc0" "$R/sys/class/remoteproc"
  : > "$R/sys/module/firmware_class/parameters/path"; : > "$R/proc/sys/kernel/modprobe"
  echo 11111111-2222-3333-4444-555555555555 > "$R/proc/sys/kernel/random/boot_id"
  echo "1.00 1.00" > "$R/proc/uptime"
  echo "/dev/block/dm-0 /vendor ext4 ro 0 0" > "$R/proc/mounts"
  mkdir -p "$R/proc/self"
  cp "$HERE/vendor/bin/a9ml-test-data.sh" "$HERE/vendor/bin/a9ml-module-load.sh" "$R/vendor/bin/"
  # C2.3: /data must be exactly one /dev/block/ram0 ext4 mount
  case "$c" in
    nonram-data) echo "/dev/block/dm-9 $R/data f2fs rw 0 0" >> "$R/proc/mounts" ;;
    data-twice) printf '/dev/block/ram0 %s ext4 rw 0 0\n/dev/block/ram0 %s ext4 rw 0 0\n' "$R/data" "$R/data" >> "$R/proc/mounts" ;;
    no-data) ;;
    *) echo "/dev/block/ram0 $R/data ext4 rw 0 0" >> "$R/proc/mounts" ;;
  esac
  # The service now validates the mounted device/root through mountinfo.
  awk -v point="$R/data" '$2==point {dev=($1=="/dev/block/ram0"?"1:0":"253:9"); print "32 1",dev,"/",$2,$4,"-",$3,$1,$4}' \
    "$R/proc/mounts" > "$R/proc/self/mountinfo"
  [ "$c" = persist-mounted ] && echo "tmpfs $R/mnt/vendor/a9-sensors tmpfs rw 0 0" >> "$R/proc/mounts"
  # Reproduce integ24's physical RO mount, including the GSI /persist alias.
  # Sensors must neither reject nor modify it. Legacy metadata bind also works.
  mkdir -p "$R/mnt/vendor/persist/sensors/registry/registry"
  echo factory-canary > "$R/mnt/vendor/persist/sensors/registry/registry/stk3b6x_0"
  if [ "$c" = legacy-tee ]; then
    echo "/dev/block/dm-1 $R/mnt/vendor/persist ext4 rw 0 0" > "$R/persist.mounts"
  else
    printf '/dev/block/by-name/persist %s ext4 ro,nosuid,nodev,noexec,norecovery 0 0\n' \
      "$R/mnt/vendor/persist" "$R/persist" > "$R/persist.mounts"
  fi
  cat "$R/persist.mounts" >> "$R/proc/mounts"
  (cd "$R/mnt/vendor/persist" && find . -type f -exec sha256sum {} +) > "$R/persist.before"
  # remoteproc
  local RPD=$R/sys/devices/rp/remoteproc0 OF=$R/sys/firmware/devicetree/base/soc@0/remoteproc@a400000
  [ "$c" = wrong-node ] && { mkdir -p "$R/sys/firmware/devicetree/base/soc@0/remoteproc@b300000"; OF=$R/sys/firmware/devicetree/base/soc@0/remoteproc@b300000; }
  printf 'qcom,sm6115-adsp-pas\0' > "$OF/compatible"
  mkdir -p "$RPD/device"; ln -s "$OF" "$RPD/device/of_node"
  ln -s ../../devices/rp/remoteproc0 "$R/sys/class/remoteproc/remoteproc0"
  # The integrated PAS module exposes the running modem alongside ADSP.
  # Sensor startup must select ADSP without counting or modifying MPSS.
  mkdir -p "$R/sys/devices/rp/modem/device" "$R/sys/firmware/devicetree/base/soc@0/remoteproc@6080000"
  printf 'qcom,sm6115-mpss-pas\0' > "$R/sys/firmware/devicetree/base/soc@0/remoteproc@6080000/compatible"
  ln -s "$R/sys/firmware/devicetree/base/soc@0/remoteproc@6080000" "$R/sys/devices/rp/modem/device/of_node"
  echo running > "$R/sys/devices/rp/modem/state"
  ln -s ../../devices/rp/modem "$R/sys/class/remoteproc/remoteproc1"
  echo "$([ "$c" = not-offline ] && echo running || echo offline)" > "$RPD/state"
  echo enabled > "$RPD/recovery"; echo enabled > "$RPD/coredump"
  # vendor payload: stock config, soc0, seed (absolute /mnt/vendor/persist paths), firmware, modules
  echo version=1 > "$R/vendor/etc/sensors/sns_reg_config"; echo '{}' > "$R/vendor/etc/sensors/config/a.json"
  for n in hw_platform platform_subtype platform_subtype_id platform_version revision soc_id; do echo x > "$R/vendor/etc/a9-sensors/soc0/$n"; done
  local SEED; SEED=$(mktemp -d); mkdir -p "$SEED/sensors/registry/registry"
  echo version=1 > "$SEED/sensors/registry/sns_reg_version"; echo '{"a":1}' > "$SEED/sensors/registry/registry/stk3b6x_0"
  (cd "$SEED" && tar -cf "$R/vendor/etc/a9-sensors/registry-seed.tar" sensors)
  (cd "$SEED" && find sensors -type f | sort | while read -r f; do echo "$(sha256sum "$f" | cut -d ' ' -f1)  /mnt/vendor/persist/$f"; done) > "$R/vendor/etc/a9-sensors/registry.sha256"
  rm -rf "$SEED"
  [ "$c" = seed-tamper ] && sed -i '1s/^./0/' "$R/vendor/etc/a9-sensors/registry.sha256"
  (cd "$R/vendor/etc/a9-sensors" && sha256sum registry-seed.tar registry.sha256 soc0/* > SHA256SUMS)
  [ "$c" = manifest-tamper ] && echo y >> "$R/vendor/etc/a9-sensors/soc0/soc_id"
  echo fw > "$R/vendor/firmware/a9adsp/qcom/sm6115/hisense/hlte556n/adsp.mdt"
  (cd "$R/vendor/firmware/a9adsp" && find . -type f | sort | xargs sha256sum > ../a9adsp.SHA256SUMS)
  printf 'qcom_q6v5_pas.ko\nfastrpc.ko\n' > "$R/vendor/lib/modules/modules.sensors"
  printf 'qcom_q6v5_pas.ko a9_adsp_manual_start=1\nfastrpc.ko a9_sensor_fastrpc=1\n' > "$R/vendor/lib/modules/modules.options"
  # props
  if [ "$c" != inert ]; then echo ro.vendor.a9ml.sensors=1 > "$R/props"; else : > "$R/props"; fi
  [ "$c" != killswitch-off ] && echo persist.a9.sensors=1 >> "$R/props"
  cat > "$R/stubs/getprop" <<S
#!/bin/sh
sed -n "s/^\$1=//p" "$R/props" | tail -n1
S
  cat > "$R/stubs/setprop" <<S
#!/bin/sh
echo "\$1 \$2" >> "$R/setprop.log"; echo "\$1=\$2" >> "$R/props"
S
  cat > "$R/stubs/mount" <<S
#!/bin/sh
echo "mount \$*" >> "$R/actions.log"; eval last=\\\${\$#}; echo "tmpfs \$last tmpfs rw 0 0" >> "$R/proc/mounts"
S
  cat > "$R/stubs/insmod" <<S
#!/bin/sh
echo "\${1##*/} \${2:-}" >> "$R/insmod.log"
m=\$(basename "\$1" .ko | tr - _); mkdir -p "$R/sys/module/\$m/parameters"
echo live > "$R/sys/module/\$m/initstate"
case "\$m:\${2:-}" in
  qcom_q6v5_pas:a9_adsp_manual_start=1) echo Y > "$R/sys/module/\$m/parameters/a9_adsp_manual_start" ;;
  fastrpc:a9_sensor_fastrpc=1) echo $([ "$c" = fastrpc-gate ] && echo N || echo Y) > "$R/sys/module/\$m/parameters/a9_sensor_fastrpc" ;;
esac
exit 0
S
  # the listener: READY unless the case says otherwise; lives until killed (or dies in listener-dies)
  cat > "$R/vendor/bin/s1-listener" <<S
#!/bin/sh
echo "\$*" > "$R/listener.args"
case "$c" in listener-silent) while :; do sleep 1; done ;; esac
# Model the registry update that the real broker confines with openat2 to tmpfs.
echo runtime-update > "$R/mnt/vendor/a9-sensors/s1-listener-root/persist/sensors/registry/registry/stk3b6x_0"
echo S1-LISTENER-READY
case "$c" in listener-dies) sleep 1; exit 0 ;; esac
while :; do sleep 1; done
S
  cat > "$R/vendor/bin/s2-sns-probe" <<S
#!/bin/sh
echo "\$*" > "$R/probe.args"
[ "$c" = no-sns ] && { echo S2-WAIT-FAIL; exit 1; }
echo "S2-SNS node=5 port=14"
S
  # review 79l B1: the stub hid that the staged probe had no --wait. real-probe runs the REAL staged
  # binary (A9S_TEST_PROBE) under qemu with exactly the arguments a9-sensors.sh passes.
  if [ "$c" = real-probe ]; then
    printf '#!/bin/sh\necho "$*" > "%s/probe.args"\nexec "%s" "%s" "$@"\n' "$R" "$A9S_TEST_QEMU" "$A9S_TEST_PROBE" > "$R/vendor/bin/s2-sns-probe"
  fi
  chmod +x "$R"/stubs/* "$R"/vendor/bin/*
  # kernel stand-ins: uptime ticks, ADSP state machine (start -> running / stop -> offline), fastrpc node
  ( s=0; while :; do s=$((s+1)); echo "$((1+s/5)).00 1.00" > "$R/proc/uptime"
      st=$(cat "$RPD/state" 2>/dev/null)
      case "$st" in start) echo running > "$RPD/state"; [ "$c" = no-fastrpc ] || : > "$R/dev/fastrpc-adsp" ;;
                    stop) echo offline > "$RPD/state" ;; esac
      [ -e "$R/crash-now" ] && { echo crashed > "$RPD/state"; rm -f "$R/crash-now"; }
      sleep 0.2; done ) &
  BG+=($!)
  KPID=$!
}
run() {   # runs the script; supervision ends by itself in the crash/death cases
  env PATH="$R/stubs:$PATH" A9S_SYS="$R/sys" A9S_DEV="$R/dev" A9S_VENDOR="$R/vendor" A9S_PROC="$R/proc" \
      A9S_MNT="$R/mnt/vendor/a9-sensors" A9S_DATA="$R/data" A9S_KMSG="$R/kmsg" A9S_POLL=0.3 A9S_START_S=4 A9S_DEV_S=3 \
      A9S_LISTENER_S=3 A9S_SNS_MS=1000 sh "$SCRIPT" start &
  SPID=$!; BG+=($SPID)
}
prop() { sed -n "s/^$1=//p" "$R/props" | tail -n1; }
waitprop() { for _ in $(seq 1 100); do [ "$(prop "$1")" = "$2" ] && return 0; sleep 0.1; done; return 1; }
finish() { kill "$KPID" 2>/dev/null; pkill -f "$R/vendor/bin/s1-listener" 2>/dev/null; wait "$SPID" 2>/dev/null; rm -rf "$R"; }

echo "== a9-sensors.sh"
# inert
mkroot inert; run; wait "$SPID"
[ ! -e "$R/setprop.log" ] && [ ! -e "$R/insmod.log" ] && ok "inert without ro.vendor.a9ml.sensors=1" || bad inert
finish
# kill switch off
mkroot killswitch-off; run; wait "$SPID"
[ "$(prop vendor.a9.sensors.state)" = off ] && [ "$(prop vendor.a9.sensors.ready)" = 0 ] && [ ! -e "$R/insmod.log" ] && [ ! -e "$R/actions.log" ] \
  && [ "$(cat "$R/sys/devices/rp/remoteproc0/state")" = offline ] && ok "kill switch off: state off, nothing mounted/loaded/started" || bad killswitch
finish
# pass + ADSP crash supervision
mkroot pass; run
if waitprop vendor.a9.sensors.ready 1 && [ "$(prop vendor.a9.sensors.state)" = up ]; then
  TOKEN=$(sha256sum "$R/vendor/etc/a9-sensors/SHA256SUMS" | cut -d ' ' -f1)
  [ "$(cat "$R/listener.args")" = "--service 11111111-2222-3333-4444-555555555555 $TOKEN" ] && ok "listener: --service <boot> <manifest token>" || bad "listener args $(cat "$R/listener.args")"
  [ "$(cat "$R/insmod.log")" = "$(printf 'qcom_q6v5_pas.ko a9_adsp_manual_start=1\nfastrpc.ko a9_sensor_fastrpc=1')" ] && ok "modules in order with options" || bad "insmod $(cat "$R/insmod.log")"
  [ "$(cat "$R/sys/devices/rp/remoteproc0/recovery") $(cat "$R/sys/devices/rp/remoteproc0/coredump")" = "disabled disabled" ] && ok "recovery/coredump disabled before start" || bad recovery
  [ -f "$R/mnt/vendor/a9-sensors/s1-listener-root/persist/sensors/registry/registry/stk3b6x_0" ] && [ -f "$R/mnt/vendor/a9-sensors/s1-listener-root/vendor/etc/sensors/sns_reg_config" ] \
    && [ -f "$R/mnt/vendor/a9-sensors/s1-listener-root/sys/devices/soc0/soc_id" ] && grep -q "tmpfs $R/mnt/vendor/a9-sensors tmpfs" "$R/proc/mounts" \
    && ok "tmpfs persist, seed, listener root (registry, config, soc0)" || bad root
  [ "$(cat "$R/data/local/tmp/a9fw/qcom/sm6115/hisense/hlte556n/adsp.mdt")" = fw ] && [ "$(cat "$R/sys/module/firmware_class/parameters/path")" = "$R/data/local/tmp/a9fw" ] \
    && ok "firmware copied and verified, firmware_class.path set" || bad firmware
  [ "$(cat "$R/probe.args")" = "--wait 400 1000" ] && ok "SNS wait" || bad probe
  [ "$(cat "$R/mnt/vendor/a9-sensors/s1-listener-root/persist/sensors/registry/registry/stk3b6x_0")" = runtime-update ] \
    && (cd "$R/mnt/vendor/persist" && sha256sum -c "$R/persist.before" >/dev/null) \
    && [ "$(grep 'persist ' "$R/proc/mounts")" = "$(cat "$R/persist.mounts")" ] \
    && ok "registry update in private RAM; physical persist RO and alias unchanged" || bad persist-isolation
  touch "$R/crash-now"
  waitprop vendor.a9.sensors.state down:adsp-crashed && [ "$(prop vendor.a9.sensors.ready)" = 0 ] && ok "ADSP crash: ready 0, down:adsp-crashed, no restart" || bad "crash state $(prop vendor.a9.sensors.state)"
  wait "$SPID"; [ "$(cat "$R/sys/devices/rp/remoteproc0/state")" = crashed ] && ok "no restart after the crash" || bad restart
else bad "pass: state $(prop vendor.a9.sensors.state)"; fi
finish
# Historical metadata-backed TEE namespace remains independent as well.
mkroot legacy-tee; run
waitprop vendor.a9.sensors.ready 1 && [ "$(prop vendor.a9.sensors.state)" = up ] \
  && (cd "$R/mnt/vendor/persist" && sha256sum -c "$R/persist.before" >/dev/null) \
  && ok "legacy TEE bind coexists with writable private sensor registry" || bad legacy-tee
touch "$R/crash-now"; wait "$SPID"
finish
# listener death -> ADSP stopped
mkroot listener-dies; run
waitprop vendor.a9.sensors.state down:listener-exited && sleep 0.5 && [ "$(cat "$R/sys/devices/rp/remoteproc0/state")" = offline ] \
  && ok "listener death: ADSP stopped, down:listener-exited" || bad "listener death $(prop vendor.a9.sensors.state)"
finish
# refusals
for c in nonram-data:unsupported-data data-twice:unsupported-data no-data:unsupported-data persist-mounted:persist-already-mounted seed-tamper:seed-hash manifest-tamper:manifest \
         not-offline:state-running wrong-node:wrong-node fastrpc-gate:fastrpc-gate listener-silent:listener-not-ready no-sns:no-sns no-fastrpc:no-fastrpc-device; do
  name=${c%%:*} why=${c#*:}
  mkroot "$name"; run; wait "$SPID"
  st=$(prop vendor.a9.sensors.state); rp=$(cat "$R/sys/devices/rp/remoteproc0/state")
  sleep 0.5; rp=$(cat "$R/sys/devices/rp/remoteproc0/state")
  case "$name" in
    nonram-data|data-twice|no-data) [ ! -e "$R/insmod.log" ] && [ ! -e "$R/actions.log" ] && [ "$rp" = offline ] \
       && [ ! -e "$R/data/vendor" ] || st="$st+touched" ;;
    persist-mounted|seed-tamper|manifest-tamper) [ ! -e "$R/insmod.log" ] && [ "$rp" = offline ] || st="$st+touched-kernel" ;;
    listener-silent|no-sns|no-fastrpc) [ "$rp" = offline ] || st="$st+adsp-$rp"
       ! pgrep -f "$R/vendor/bin/s1-listener" >/dev/null || st="$st+listener-alive" ;;
  esac
  [ "$st" = "fail:$why" ] && [ "$(prop vendor.a9.sensors.ready)" = 0 ] && ok "refused: $name -> fail:$why" || bad "$name: state=$st"
  finish
done
# the real staged probe (review 79l B1): on the host there is no SNS 400, so the REAL binary must run
# its --wait mode and fail with S2-WAIT-FAIL (rc 1), giving fail:no-sns with that line in sns.log; a probe
# without --wait would exit 2 silently (empty sns.log)
if [ -n "${A9S_TEST_PROBE:-}" ] && [ -x "${A9S_TEST_QEMU:-}" ]; then
  mkroot real-probe; run; wait "$SPID"; sleep 0.5
  [ "$(prop vendor.a9.sensors.state)" = fail:no-sns ] && [ "$(cat "$R/probe.args")" = "--wait 400 1000" ] \
    && [ "$(cat "$R/data/vendor/a9-sensors/sns.log")" = "S2-WAIT-FAIL no-unique-sns-400" ] \
    && [ "$(cat "$R/sys/devices/rp/remoteproc0/state")" = offline ] \
    && ok "real staged probe ($(sha256sum "$A9S_TEST_PROBE" | cut -c1-12)) under qemu: --wait mode ran, S2-WAIT-FAIL -> fail:no-sns, ADSP stopped" \
    || bad "real probe: state=$(prop vendor.a9.sensors.state) args='$(cat "$R/probe.args")' rp=$(cat "$R/sys/devices/rp/remoteproc0/state") sns.log='$(cat "$R/data/vendor/a9-sensors/sns.log" 2>/dev/null)'"
  finish
else
  echo "  SKIP real staged probe (set A9S_TEST_PROBE=<staged s2-sns-probe> A9S_TEST_QEMU=<qemu-aarch64>)"
  [ "${A9S_REQUIRE_REAL_PROBE:-0}" = 1 ] && bad "real probe required but not given"
fi
echo "== result: $fails failure(s)"
[ "$fails" = 0 ] && echo "A9-SENSORS SETUP HOST TEST PASS"
exit $((fails > 0))
