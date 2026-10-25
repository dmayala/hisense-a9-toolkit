#!/usr/bin/env bash
# test-gpu-setup.sh -- host test of the GPU-mode vendor scripts against fake
# roots (runs/gpu.md, review 27). Must hold (FAIL CLOSED):
#  - a9ml-gpu.sh is inert without ro.vendor.a9ml.gpu=1;
#  - setup verifies every prerequisite by readback (firmware path, firmware
#    sha256, vkms.map_wc=Y, vkms image: GPU modules + separate_gpu_kms=Y, no
#    a9_eink module) and only then qualifies (info, render, dmabuf);
#  - select sets persist.graphics.egl=mesa only on a full PASS and reads it
#    back; on ANY failure it writes angle explicitly (also over a stale mesa);
#  - a hung probe is killed by the step timeout, and the deadline guard
#    triggers the sysrq reboot;
#  - a9ml-eink.sh passes modules.options to insmod (msm separate_gpu_kms=1) and
#    leaves the GPU's own DRM card (adreno, 5900000.gpu) as ueventd made it.
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
G=$HERE/vendor/bin/a9ml-gpu.sh E=$HERE/vendor/bin/a9ml-eink.sh
fails=0
ok() { echo "  ok   $1"; }
bad() { echo "  FAIL $1"; fails=$((fails+1)); }
# mkroot <eink 0|1> <probe spec: e.g. "info=0 render=0 dmabuf=0" (exit codes; "hang" sleeps)>
mkroot() {
  R=$(mktemp -d)
  mkdir -p "$R/stubs" "$R/sys/module/firmware_class/parameters" "$R/sys/module/vkms/parameters" \
    "$R/vendor/lib/modules" "$R/vendor/firmware/a9gpu/qcom/sm6115/hisense/hlte556n" "$R/vendor/bin" "$R/data/local/tmp" "$R/dev/dri" \
    "$R/sys/devices/platform/soc@0/5900000.gpu/drm/renderD128" "$R/sys/class/drm" "$R/proc"
  : > "$R/sys/module/firmware_class/parameters/path"; echo N > "$R/sys/module/vkms/parameters/map_wc"; : > "$R/proc/sysrq-trigger"
  # platform devices: gpucc + GPU SMMU bound at boot (built in), GPU and MDSS bound by msm.ko
  PDV="$R/sys/bus/platform/devices"; mkdir -p "$R/sys/bus/platform/drivers/gpucc-sm6115" "$R/sys/bus/platform/drivers/arm-smmu" \
    "$R/sys/bus/platform/drivers/adreno" "$R/sys/bus/platform/drivers/msm-mdss"
  for d in 5990000.clock-controller 59a0000.iommu 5900000.gpu 5e00000.display-subsystem; do mkdir -p "$PDV/$d"; : > "$PDV/$d/driver_override"; done
  ln -s ../../drivers/gpucc-sm6115 "$PDV/5990000.clock-controller/driver"; ln -s ../../drivers/arm-smmu "$PDV/59a0000.iommu/driver"
  echo sqe > "$R/vendor/firmware/a9gpu/qcom/a630_sqe.fw"; echo zap > "$R/vendor/firmware/a9gpu/qcom/sm6115/hisense/hlte556n/a610_zap.mbn"
  (cd "$R/vendor/firmware/a9gpu" && find . -type f | sort | xargs sha256sum > ../a9gpu.SHA256SUMS)
  printf 'gpucc-sm6115.ko\nmdt_loader.ko\nmsm.ko\n' > "$R/vendor/lib/modules/modules.gpu"
  printf 'msm.ko separate_gpu_kms=1\n' > "$R/vendor/lib/modules/modules.options"
  ln -s ../.. "$R/sys/devices/platform/soc@0/5900000.gpu/drm/renderD128/device"
  ln -s ../../devices/platform/soc@0/5900000.gpu/drm/renderD128 "$R/sys/class/drm/renderD128"; : > "$R/dev/dri/renderD128"
  printf 'ro.vendor.a9ml.gpu=1\nro.vendor.a9ml.gpu.egl=mesa\nro.boot.a9ml.eink=%s\nro.vendor.a9ml.gpu.app_egl=mesa\n' "$1" > "$R/props"
  cat > "$R/stubs/getprop" <<S
#!/bin/sh
sed -n "s/^\$1=//p" "$R/props" | tail -n1
S
  cat > "$R/stubs/setprop" <<S
#!/bin/sh
echo "\$1 \$2" >> "$R/setprop.log"
[ -e "$R/setprop.ignore.\$1=\$2" ] && exit 0
case "\$1" in ro.*) grep -q "^\$1=" "$R/props" && exit 1 ;; esac   # ro. is write-once (init refuses a second write)
echo "\$1=\$2" >> "$R/props"
S
  # insmod: "loads" by creating /sys/module/<name> (+ the msm parameter from its args)
  cat > "$R/stubs/insmod" <<S
#!/bin/sh
echo "\${1##*/} \${2:-}" >> "$R/insmod.log"
m=\$(basename "\$1" .ko | tr - _); mkdir -p "$R/sys/module/\$m/parameters"
[ "\$m" = msm ] && { case "\${2:-}" in separate_gpu_kms=1) echo Y ;; *) echo N ;; esac; } > "$R/sys/module/msm/parameters/separate_gpu_kms"
if [ "\$m" = msm ]; then
  P="$R/sys/bus/platform/devices"
  [ -e "$R/nogpu" ] || ln -s ../../drivers/adreno "\$P/5900000.gpu/driver"
  [ "\$(cat "\$P/5e00000.display-subsystem/driver_override")" = none ] || ln -s ../../drivers/msm-mdss "\$P/5e00000.display-subsystem/driver"
fi
exit 0
S
  local spec="$2"
  cat > "$R/vendor/bin/a9-gpu-probe" <<S
#!/bin/sh
for kv in $spec; do
  [ "\${kv%%=*}" = "\$1" ] || continue
  v=\${kv#*=}
  [ "\$v" = hang ] && sleep 30
  [ "\$v" = 0 ] && echo "A9GPU result PASS \$1" || echo "A9GPU result FAIL \$1"
  exit \$v
done
exit 9
S
  chmod +x "$R"/stubs/* "$R/vendor/bin/a9-gpu-probe"
}
run() { PATH="$R/stubs:$PATH" A9ML_SYS="$R/sys" A9ML_DEV="$R/dev" A9ML_KMSG="$R/kmsg" A9ML_VENDOR="$R/vendor" A9ML_DATA="$R/data" \
        A9ML_SYSRQ="$R/proc/sysrq-trigger" A9ML_GPUWAIT=3 A9ML_GPU_STEP=${STEP:-10} A9ML_GPU_DEADLINE=${DL:-60} sh "$G" "$@"; }
prop() { sed -n "s/^$1=//p" "$R/props" | tail -n1; }
ALLOK="info=0 render=0 dmabuf=0"

mkroot 0 "$ALLOK"; sed -i 's/^ro.vendor.a9ml.gpu=1/ro.vendor.a9ml.gpu=0/' "$R/props"; run setup; run select
[ ! -e "$R/insmod.log" ] && [ ! -e "$R/setprop.log" ] && [ "$(cat "$R/sys/module/vkms/parameters/map_wc")" = N ] && ok "inert without ro.vendor.a9ml.gpu=1" || bad "not inert"
rm -rf "$R"

mkroot 0 "$ALLOK"; run setup
[ "$(cat "$R/sys/module/firmware_class/parameters/path")" = "$R/data/local/tmp/a9fw" ] && ok "firmware_class.path set when empty" || bad "firmware path"
[ -f "$R/data/local/tmp/a9fw/qcom/sm6115/hisense/hlte556n/a610_zap.mbn" ] && ok "firmware copied" || bad "firmware not copied"
[ "$(cat "$R/sys/module/vkms/parameters/map_wc")" = 1 ] && ok "vkms.map_wc written" || bad "map_wc"
[ "$(tr '\n' '|' < "$R/insmod.log")" = "gpucc-sm6115.ko |mdt_loader.ko |msm.ko separate_gpu_kms=1|" ] && ok "vkms image: GPU modules in order, msm separate_gpu_kms=1" || bad "insmod: $(tr '\n' '|' < "$R/insmod.log")"
[ "$(prop vendor.a9ml.gpu.setup)" = ok ] && [ "$(cat "$R/data/local/tmp/a9ml-gpu.qual")" = PASS ] && ok "setup ok, qualified in setup (vkms image)" || bad "setup: $(prop vendor.a9ml.gpu.setup) / $(cat "$R/data/local/tmp/a9ml-gpu.qual" 2>&1)"
run select
[ "$(prop persist.graphics.egl)" = mesa ] && [ "$(prop vendor.a9ml.gpu.state)" = mesa ] && ok "select: full PASS -> mesa (read back)" || bad "select pass: $(cat "$R/setprop.log")"
[ "$(prop ro.hardware.egl)" = mesa ] && [ "$(prop vendor.a9ml.gpu.apps_egl)" = mesa ] && ok "select: full PASS -> ro.hardware.egl=mesa for apps (no ANGLE for every app)" || bad "apps egl: '$(prop ro.hardware.egl)'"
rm -rf "$R"

case_fail() {  # case_fail <name> <eink> <spec> <prep cmd> <want state glob>
  mkroot "$2" "$3"; echo persist.graphics.egl=mesa >> "$R/props"   # a stale mesa must be replaced
  eval "$4"; run setup 2>/dev/null; run select
  local st; st=$(prop vendor.a9ml.gpu.state)
  # shellcheck disable=SC2254
  case "$st" in $5) if [ "$(prop persist.graphics.egl)" = angle ] && [ "$(prop ro.hardware.egl)" = angle ]; then ok "$1 -> $st, persist.graphics.egl=angle, ro.hardware.egl=angle"; else bad "$1: persist $(prop persist.graphics.egl) ro.hardware.egl $(prop ro.hardware.egl)"; fi ;;
    *) bad "$1: state '$st' (want $5)"; sed 's/^/     kmsg: /' "$R/kmsg" | tail -5 ;; esac
  rm -rf "$R"
}
case_fail "map_wc cannot be set (old kernel)" 0 "$ALLOK" 'rm -rf "$R/sys/module/vkms"' 'angle:FAIL:setup(*vkms.map_wc*'
case_fail "firmware hash mismatch" 0 "$ALLOK" 'echo x >> "$R/vendor/firmware/a9gpu/qcom/a630_sqe.fw"' 'angle:FAIL:setup(*firmware-copy/verify*'
case_fail "a9_eink loaded in the vkms image" 0 "$ALLOK" 'mkdir -p "$R/sys/module/a9_eink"' 'angle:FAIL:setup(*a9_eink-loaded*'
case_fail "msm without separate_gpu_kms" 0 "$ALLOK" 'sed -i "s/separate_gpu_kms=1/foo=1/" "$R/vendor/lib/modules/modules.options"' 'angle:FAIL:setup(*msm-separate_gpu_kms*'
case_fail "GPU not loaded (info)" 0 "info=1 render=0 dmabuf=0" ':' 'angle:FAIL:info(rc=1)'
case_fail "render readback wrong" 0 "info=0 render=1 dmabuf=0" ':' 'angle:FAIL:render(rc=1)'
case_fail "shared dma-buf readback wrong" 0 "info=0 render=0 dmabuf=1" ':' 'angle:FAIL:dmabuf(rc=1)'
case_fail "mesa selection does not read back" 0 "$ALLOK" 'sed -i "/^persist.graphics.egl=/d" "$R/props"; : > "$R/setprop.ignore.persist.graphics.egl=mesa"' 'angle:readback(*'
case_fail "build-time opt-out" 0 "$ALLOK" 'echo ro.vendor.a9ml.gpu.egl=angle >> "$R/props"' 'angle:forced'
# build option: Mesa for SurfaceFlinger, apps stay on ANGLE
mkroot 0 "$ALLOK"; echo ro.vendor.a9ml.gpu.app_egl=angle >> "$R/props"; run setup; run select
[ "$(prop persist.graphics.egl)" = mesa ] && [ "$(prop ro.hardware.egl)" = angle ] && [ "$(prop vendor.a9ml.gpu.state)" = mesa ] && ok "app_egl=angle: SF mesa, apps angle" || bad "app_egl=angle: $(prop ro.hardware.egl)"
rm -rf "$R"
# ro.hardware.egl refused (denied): state stays mesa for SF, apps_egl reads empty (init then sets angle)
mkroot 0 "$ALLOK"; : > "$R/setprop.ignore.ro.hardware.egl=mesa"; run setup; run select
[ "$(prop vendor.a9ml.gpu.state)" = mesa ] && [ -z "$(prop ro.hardware.egl)" ] && [ -z "$(prop vendor.a9ml.gpu.apps_egl)" ] && grep -q "did not read back" "$R/kmsg" && ok "ro.hardware.egl write refused: logged, apps_egl empty, SF unaffected" || bad "refused write: $(prop ro.hardware.egl)"
rm -rf "$R"
# write-once: a stale ro.hardware.egl=angle (old build.prop) stays; select cannot flip it
mkroot 0 "$ALLOK"; echo ro.hardware.egl=angle >> "$R/props"; run setup; run select
[ "$(prop ro.hardware.egl)" = angle ] && ok "legacy build.prop with ro.hardware.egl=angle: stays angle (why the line must be absent)" || bad "write-once"
rm -rf "$R"
# review 74: failed qualification + stale persist=mesa + refused ANGLE write must NOT give apps Mesa
mkroot 0 "info=1 render=0 dmabuf=0"; echo persist.graphics.egl=mesa >> "$R/props"; : > "$R/setprop.ignore.persist.graphics.egl=angle"; run setup 2>/dev/null; run select
[ "$(prop vendor.a9ml.gpu.state)" = "angle:readback(mesa)" ] && [ "$(prop ro.hardware.egl)" = angle ] && [ "$(prop vendor.a9ml.gpu.apps_egl)" = angle ] && ok "failed qualification + stale persist=mesa + refused angle write: apps still angle (state $(prop vendor.a9ml.gpu.state))" || bad "stale-mesa case: state $(prop vendor.a9ml.gpu.state) ro.hardware.egl '$(prop ro.hardware.egl)'"
rm -rf "$R"
# app_egl must read exactly mesa (unset/garbage -> angle)
mkroot 0 "$ALLOK"; sed -i 's/^ro.vendor.a9ml.gpu.app_egl=.*//' "$R/props"; run setup; run select
[ "$(prop ro.hardware.egl)" = angle ] && [ "$(prop vendor.a9ml.gpu.state)" = mesa ] && ok "app_egl property absent: SF mesa, apps angle (fail closed)" || bad "app_egl absent: '$(prop ro.hardware.egl)'"
rm -rf "$R"
# init.rc: the fallback setprop follows the select, before anything else
python3 -I - "$HERE/vendor/etc/init/init.a9ml-gpu.rc" <<'PY' && ok "init.a9ml-gpu.rc: 'setprop ro.hardware.egl angle' directly follows exec_start a9ml_gpu_select" || bad "rc fallback missing"
import sys,re
t=[l.strip() for l in open(sys.argv[1]) if l.strip() and not l.strip().startswith('#')]
i=t.index('exec_start a9ml_gpu_select'); sys.exit(0 if t[i+1]=='setprop ro.hardware.egl angle' else 1)
PY
STEP=1 case_fail "GPU SMMU unbound (G1 attempt 1 replay)" 0 "$ALLOK" 'rm "$R/sys/bus/platform/devices/59a0000.iommu/driver"' 'angle:FAIL:setup(*59a0000.iommu-unbound*'
case_fail "adreno did not bind" 0 "$ALLOK" ': > "$R/nogpu"' 'angle:FAIL:setup(*5900000.gpu-unbound*'
case_fail "MDSS bound in the vkms image (override ignored)" 0 "$ALLOK" 'chmod 0444 "$R/sys/bus/platform/devices/5e00000.display-subsystem/driver_override"' 'angle:FAIL:setup(*mdss-driver_override*'
STEP=1 case_fail "hung probe killed by the step timeout" 0 "info=0 render=hang dmabuf=0" ':' 'angle:FAIL:render(rc=137)'

# deadline guard: steps longer than the deadline -> sysrq b
mkroot 0 "info=hang render=0 dmabuf=0"; STEP=5 DL=1 run setup; sleep 1
grep -qx b "$R/proc/sysrq-trigger" && grep -q 'sysrq reboot' "$R/kmsg" && ok "deadline guard: sysrq b when the qualification overruns" || bad "deadline guard did not fire"
rm -rf "$R"
mkroot 0 "$ALLOK"; DL=2 run setup; sleep 3
[ ! -s "$R/proc/sysrq-trigger" ] && ok "deadline guard silent after a finished qualification" || bad "guard fired after success"
rm -rf "$R"

# E Ink image: setup does not load modules; select qualifies once a9ml-eink.sh loaded msm with the option
mkroot 1 "$ALLOK"; echo -n "$R/cmdline-fw" > "$R/sys/module/firmware_class/parameters/path"; run setup
[ ! -e "$R/insmod.log" ] && [ ! -e "$R/data/local/tmp/a9ml-gpu.qual" ] && ok "eink image: setup loads nothing and does not qualify yet" || bad "eink setup did too much"
[ -f "$R/cmdline-fw/qcom/a630_sqe.fw" ] && ok "eink image: cmdline firmware path kept and filled" || bad "eink firmware"
eink_msm() {  # what a9ml-eink.sh's insmod of msm.ko <opt> does in the fake root
  mkdir -p "$R/sys/module/msm/parameters"; echo "$1" > "$R/sys/module/msm/parameters/separate_gpu_kms"
  ln -s ../../drivers/adreno "$R/sys/bus/platform/devices/5900000.gpu/driver"
  [ "$2" = mdss ] && ln -s ../../drivers/msm-mdss "$R/sys/bus/platform/devices/5e00000.display-subsystem/driver"; :; }
eink_msm Y mdss; run select
[ "$(prop persist.graphics.egl)" = mesa ] && ok "eink image: select qualifies and selects mesa" || bad "eink select: $(prop vendor.a9ml.gpu.state)"
rm -rf "$R"
mkroot 1 "$ALLOK"; run setup; eink_msm N mdss; run select
[ "$(prop persist.graphics.egl)" = angle ] && ok "eink image: msm loaded without separate_gpu_kms -> angle" || bad "eink no-separate: $(prop vendor.a9ml.gpu.state)"
rm -rf "$R"
mkroot 1 "$ALLOK"; run setup; eink_msm Y nomdss; run select
[ "$(prop vendor.a9ml.gpu.state)" = "angle:FAIL:mdss-not-bound" ] && grep -q 'STOP: 5e00000.display-subsystem not bound' "$R/kmsg" && ok "eink image: msm-mdss not bound -> HARD STOP (angle:FAIL:mdss-not-bound)" || bad "eink mdss: $(prop vendor.a9ml.gpu.state)"
rm -rf "$R"

# a9ml-eink.sh: options + the GPU's own card
R=$(mktemp -d)
mkdir -p "$R/stubs" "$R/proc" "$R/dev/dri" "$R/mods" "$R/sys/class/drm" "$R/sys/bus/platform/drivers/adreno" "$R/sys/bus/platform/drivers/a9-eink"
printf 'gpucc-sm6115.ko\ndispcc-sm6115.ko\nmsm.ko\na9_eink.ko\n' > "$R/mods/modules.load"
printf 'msm.ko separate_gpu_kms=1\n' > "$R/mods/modules.options"
printf '#!/bin/sh\n[ "$1" = ro.boot.a9ml.eink ] && echo 1\n' > "$R/stubs/getprop"
printf '#!/bin/sh\necho "$@" >> %s/setprop.log\n' "$R" > "$R/stubs/setprop"
printf '#!/bin/sh\necho "chmod $@" >> %s/perm.log\n' "$R" > "$R/stubs/chmod"
printf '#!/bin/sh\necho "chown $@" >> %s/perm.log\n' "$R" > "$R/stubs/chown"
printf '#!/bin/sh\necho "${1##*/} ${2:-}" >> %s/insmod.log\n' "$R" > "$R/stubs/insmod"
chmod +x "$R"/stubs/*
for c in card0:devices/faux/vkms card1:devices/platform/soc@0/5900000.gpu card2:devices/platform/eink-display; do
  n=${c%%:*} d=${c#*:}; mkdir -p "$R/sys/$d/drm/$n"; ln -s ../.. "$R/sys/$d/drm/$n/device"; ln -s "../../$d/drm/$n" "$R/sys/class/drm/$n"; : > "$R/dev/dri/$n"
done
ln -s ../../../../bus/platform/drivers/adreno "$R/sys/devices/platform/soc@0/5900000.gpu/driver"
ln -s ../../../bus/platform/drivers/a9-eink "$R/sys/devices/platform/eink-display/driver"
mkdir -p "$R/sys/devices/platform/eink-display/drm/card2/card2-DSI-1/eink"; : > "$R/sys/devices/platform/eink-display/drm/card2/card2-DSI-1/eink/mode"
ln -s ../../devices/platform/eink-display/drm/card2/card2-DSI-1 "$R/sys/class/drm/card2-DSI-1"
PATH="$R/stubs:$PATH" A9ML_SYS="$R/sys" A9ML_DEV="$R/dev" A9ML_PROC="$R/proc" A9ML_KMSG="$R/kmsg" A9ML_MODDIR="$R/mods" A9ML_WAIT=3 A9ML_MSMWAIT=2 sh "$E" setup
grep -qx 'msm.ko separate_gpu_kms=1' "$R/insmod.log" && grep -qx 'gpucc-sm6115.ko ' "$R/insmod.log" && ok "a9ml-eink.sh: insmod msm.ko separate_gpu_kms=1" || bad "eink insmod: $(tr '\n' '|' < "$R/insmod.log")"
! grep -q 'card1' "$R/perm.log" 2>/dev/null && ok "a9ml-eink.sh: GPU card (adreno) left as is" || bad "GPU card touched: $(cat "$R/perm.log")"
grep -q 'vendor.hwc.drm.device .*card2' "$R/setprop.log" && ok "a9ml-eink.sh: composer still on the a9_eink card" || bad "composer: $(cat "$R/setprop.log")"
rm -rf "$R"
[ $fails = 0 ] && echo "test-gpu-setup: PASS" || { echo "test-gpu-setup: $fails FAILED"; exit 1; }
