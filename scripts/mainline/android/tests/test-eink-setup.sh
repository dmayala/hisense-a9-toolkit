#!/usr/bin/env bash
# test-eink-setup.sh -- host test of `a9ml-eink.sh setup` against fake sysfs
# trees: the KMS cards are classified by sysfs device path / bound driver,
# never by number. Must hold: vkms (a faux device, no driver link; boot eink-2
# locked it and minigbm died) and unknown cards stay as ueventd made them, only
# the msm card is made root-only, and drm_hwcomposer is pointed at the a9_eink
# card wherever it is numbered. Offline; run by android-boot.sh --offline --eink.
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
SCRIPT=$HERE/vendor/bin/a9ml-eink.sh
fails=0
run_case() {   # run_case <name> <layout...>: entries "cardN:kind"
  local name=$1; shift
  local R; R=$(mktemp -d)
  mkdir -p "$R/stubs" "$R/proc" "$R/dev/dri" "$R/mods" "$R/sys/class/drm" "$R/sys/bus/platform/drivers/msm_dpu" "$R/sys/bus/platform/drivers/a9-eink"
  printf 'dispcc-sm6115.ko\nmsm.ko\npanel-hisense-hlte556n-eink.ko\ntc358767.ko\na9_eink.ko\n' > "$R/mods/modules.load"
  cat > "$R/stubs/getprop" <<'S'
#!/bin/sh
[ "$1" = ro.boot.a9ml.eink ] && echo 1
S
  printf '#!/bin/sh\necho "$@" >> %s/setprop.log\n' "$R" > "$R/stubs/setprop"
  printf '#!/bin/sh\necho "chmod $@" >> %s/perm.log\n' "$R" > "$R/stubs/chmod"
  printf '#!/bin/sh\necho "chown $@" >> %s/perm.log\n' "$R" > "$R/stubs/chown"
  printf '#!/bin/sh\necho "${1##*/}" >> %s/insmod.log\ncase "$1" in */msm.ko) exit "${A9ML_TEST_MSM_RC:-0}" ;; esac\n' "$R" > "$R/stubs/insmod"
  chmod +x "$R"/stubs/*
  local want_card="" want_locked=""
  for e in "$@"; do
    local c=${e%%:*} k=${e#*:} dev
    case "$k" in
      vkms) dev=devices/faux/vkms ;;
      a9) dev=devices/platform/eink-display; want_card=$c ;;
      msm) dev=devices/platform/soc@0/5e00000.display-subsystem/5e01000.display-controller; want_locked="$want_locked $c" ;;
      other) dev=devices/platform/soc@0/1234.other ;;
    esac
    mkdir -p "$R/sys/$dev/drm/$c"
    case "$k" in
      msm) ln -s ../../../../../bus/platform/drivers/msm_dpu "$R/sys/devices/platform/soc@0/5e00000.display-subsystem/5e01000.display-controller/driver" 2>/dev/null
           ln -s ../.. "$R/sys/$dev/drm/$c/device" ;;
      a9) ln -s ../../../../bus/platform/drivers/a9-eink "$R/sys/$dev/driver"; ln -s ../.. "$R/sys/$dev/drm/$c/device"
          mkdir -p "$R/sys/$dev/drm/$c/$c-DSI-1/eink"; : > "$R/sys/$dev/drm/$c/$c-DSI-1/eink/mode"
          : > "$R/sys/$dev/drm/$c/$c-DSI-1/eink/manual_clear"
          chmod 0200 "$R/sys/$dev/drm/$c/$c-DSI-1/eink/manual_clear"
          : > "$R/sys/$dev/drm/$c/$c-DSI-1/eink/test_pattern"
          chmod 0600 "$R/sys/$dev/drm/$c/$c-DSI-1/eink/test_pattern"
          ln -s "../../$dev/drm/$c/$c-DSI-1" "$R/sys/class/drm/$c-DSI-1" ;;
      *) ln -s ../.. "$R/sys/$dev/drm/$c/device" ;;
    esac
    ln -s "../../$dev/drm/$c" "$R/sys/class/drm/$c"
    : > "$R/dev/dri/$c"
  done
  PATH="$R/stubs:$PATH" A9ML_SYS="$R/sys" A9ML_DEV="$R/dev" A9ML_PROC="$R/proc" A9ML_KMSG="$R/kmsg" A9ML_MODDIR="$R/mods" A9ML_WAIT=3 A9ML_MSMWAIT=2 \
    "${A9ML_TEST_SHELL:-sh}" "$SCRIPT" setup
  local ok=1 got locked
  if grep -qE '/(test_pattern|manual_clear)$' "$R/perm.log"; then
    echo "  [$name] root diagnostic/manual-clear ownership changed"; ok=0
  fi
  grep -q "^chown system system .*/$want_card-DSI-1/eink/mode$" "$R/perm.log" || {
    echo "  [$name] ordinary E Ink attribute ownership missing"; ok=0;
  }
  got=$(sed -n 's/^vendor.hwc.drm.device //p' "$R/setprop.log" 2>/dev/null)
  [ "$got" = "$R/dev/dri/$want_card" ] || [ "$got" = "/dev/dri/$want_card" ] || { echo "  [$name] composer -> '$got', want $want_card"; ok=0; }
  locked=$(sed -n 's|^chmod 0600 .*/dri/\(card[0-9]*\)$|\1|p' "$R/perm.log" 2>/dev/null | sort | tr '\n' ' ')
  [ "$(echo $locked)" = "$(echo $want_locked | tr ' ' '\n' | sort | tr '\n' ' ' | xargs)" ] || { echo "  [$name] locked '$locked', want '$want_locked'"; ok=0; }
  [ "$(tr '\n' ' ' < "$R/insmod.log")" = "dispcc-sm6115.ko msm.ko panel-hisense-hlte556n-eink.ko tc358767.ko a9_eink.ko " ] || { echo "  [$name] insmod order: $(tr '\n' ' ' < "$R/insmod.log")"; ok=0; }
  [ "$(sed -n '/MSM_INSMOD/p' "$R/kmsg" | tr '\n' '|')" = "a9ml-eink: MSM_INSMOD begin|a9ml-eink: MSM_INSMOD end rc=${A9ML_TEST_MSM_RC:-0}|" ] || { echo "  [$name] missing paired MSM markers"; ok=0; }
  if [ -n "$want_locked" ]; then grep -q "msm DRM card .* present" "$R/kmsg" || { echo "  [$name] msm card not reported before a9_eink"; ok=0; }
  else grep -q "WARNING no msm DRM card" "$R/kmsg" || { echo "  [$name] missing msm warning"; ok=0; }; fi
  [ $ok = 1 ] && echo "  ok   $name: composer on $want_card, root-only: ${want_locked:- none}" || { fails=$((fails+1)); sed 's/^/     kmsg: /' "$R/kmsg" 2>/dev/null; }
  rm -rf "$R"
}
run_case "boot eink-2 layout (vkms card0, a9_eink card1)" card0:vkms card1:a9
run_case "msm present (card1 msm, card2 a9_eink)" card0:vkms card1:msm card2:a9
run_case "a9_eink before msm" card0:vkms card1:a9 card2:msm
run_case "unknown card left alone" card0:vkms card1:other card2:a9
A9ML_TEST_MSM_RC=23 run_case "MSM insertion fails: paired numeric rc" card0:vkms card1:msm card2:a9
[ $fails = 0 ] && echo "test-eink-setup: PASS" || { echo "test-eink-setup: $fails FAILED"; exit 1; }
