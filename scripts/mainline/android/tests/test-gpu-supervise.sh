#!/usr/bin/env bash
# test-gpu-supervise.sh -- offline fault injection for gpu/gpu-supervise.sh
# (stages g1/g2a/g2b; review 27 item 1, review 31 items 2a-2c). android-boot.sh,
# a9-health.sh and adb are STUBS; nothing touches USB.
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
SUPV=$HERE/gpu/gpu-supervise.sh
fails=0
ok() { echo "  ok   $1"; }
bad() { echo "  FAIL $1"; fails=$((fails+1)); }
setup() {   # setup <write mode> <restore failures> [offline rc]
  T=$(mktemp -d); mkdir -p "$T/runs" "$T/out"
  for f in a9-android-boot-gpu.img vendor_b-gpu.img a9-android-eink-boot-gpu.img vendor_b-eink-gpu.img; do echo "$f" > "$T/out/$f"; done
  echo "${2}" > "$T/restore_fail"; echo a > "$T/state"
  cat > "$T/ab" <<S
#!/usr/bin/env bash
echo "\$*" >> "$T/calls"
LOCK="$T/a9-usb.lock"
case "\$1" in
  --offline) [ "${3:-0}" = 0 ] && echo "  ok   GPU: boot DTB + vendor are a GPU pair, ..." && exit 0; exit 1 ;;
  --restore) exec 9>"\$LOCK"; flock -n 9 || { echo "lock busy"; exit 3; }
             n=\$(cat "$T/restore_fail"); if [ "\$n" -gt 0 ]; then echo \$((n-1)) > "$T/restore_fail"; exit 1; fi; echo a > "$T/state"; exit 0 ;;
  --write)
    exec 9>"\$LOCK"; flock -n 9 || exit 3
    echo b > "$T/state"
    case "$1" in
      hang) sleep 300 ;;
      orphans) sleep 300 & setsid sleep 300 & echo \$! > "$T/escaped"; sleep 300 ;;
      timeout) echo bootloader > "$T/state"; echo "!! nothing decisive on USB"; exit 1 ;;
      *)
        RD="$T/runs/x-mainline-android/android"; mkdir -p "\$RD/eink"
        st=mesa; [ "$1" = angle ] && st="angle:FAIL:dmabuf(rc=1)"
        ek=0; case "\$*" in *--eink*) ek=1 ;; esac
        printf '[vendor.a9ml.gpu.state]: [%s]\n[vendor.a9ml.gpu.setup]: [ok]\n[ro.boot.a9ml.eink]: [%s]\n[sys.boot_completed]: [1]\n[vendor.a9ml.eink.ready]: [1]\n[vendor.a9ml.eink.card]: [card2]\n[vendor.hwc.drm.device]: [/dev/dri/card2]\n' "\$st" "\$ek" > "\$RD/getprop.txt"
        g='A9GPU shared %s, 8 rounds: cpu->gpu 0 wrong, gpu->cpu 0 wrong, padding: 512 px in the allowed 16x4 band (1 rounds), 0 px OUTSIDE it, fences 0 bad, glError 0x0\n'
        { echo 'A9GPU result PASS info'; printf "\$g" A B C; echo 'A9GPU result PASS dmabuf'; } > "\$RD/a9ml-gpu.log"
        printf 'msm 123 0 - Live\n' > "\$RD/proc_modules.txt"
        md=-; [ \$ek = 1 ] && md=msm-mdss
        { echo "[ 0.0] Linux version 7.2.10-a9+"; echo "[ 58.0] a9ml-gpu: bound: gpucc=sm6115-gpucc smmu=arm-smmu gpu=adreno mdss=\$md"
          echo "[ 60.0] a9ml-gpu: qualification: PASS"; } > "\$RD/dmesg.txt"
        [ "$1" = fault ] && echo "[ 9.0] arm-smmu 59a0000.iommu: Unhandled context fault: fsr=0x402" >> "\$RD/dmesg.txt"
        [ "$1" = nokmsg ] && echo "su: permission denied" > "\$RD/dmesg.txt"
        [ "$1" = rail ] && echo "[ 120.0] a9-eink eink-display: fault latched: rail" >> "\$RD/dmesg.txt"
        echo "GLES: freedreno, FD610, OpenGL ES 3.2 Mesa 26.2.5" > "\$RD/dumpsys_SurfaceFlinger_.txt"
        [ "$1" = nomesa ] && echo "GLES: ANGLE (SwiftShader)" > "\$RD/dumpsys_SurfaceFlinger_.txt"
        printf 'card1 -> ../../devices/platform/soc@0/5900000.gpu/drm/card1\ncard2 -> ../../devices/platform/eink-display/drm/card2\ncard3 -> ../../devices/platform/soc@0/5e00000.display-subsystem/5e01000.display-controller/drm/card3\n' > "\$RD/gpu-drm.txt"
        mod=0x0; [ "$1" = ubwc ] && mod=0x500000000000001
        pw='Y\nY\n'; [ "$1" = nomapwc ] && pw='N\nY\n'; printf "\$pw" > "\$RD/gpu-params.txt"
        # review 38 R1: the real debugfs layout, dri/<minor> symlink + dri/<unique> dir,
        # each with "name" = "<driver> dev=<dev> unique=<unique>"
        st='crtc[33]: crtc-0\n\tenable=1\nplane[31]: plane-0\n\tcrtc=crtc-0\n\tfb=40\n\t\tallocated by = surfaceflinger\n\t\tformat=AB24 little-endian (0x34324241)\n\t\tmodifier=%s\n'
        { [ "$1" = uniqueonly ] || { echo "== /sys/kernel/debug/dri/2 a9_eink dev=eink-display unique=eink-display"; printf "\$st" "\$mod"; }
          echo "== /sys/kernel/debug/dri/3 msm-kms dev=5e00000.display-subsystem unique=5e00000.display-subsystem"; printf "\$st" 0x0
          echo "== /sys/kernel/debug/dri/5e00000.display-subsystem msm-kms dev=5e00000.display-subsystem unique=5e00000.display-subsystem"; printf "\$st" 0x0
          echo "== /sys/kernel/debug/dri/eink-display a9_eink dev=eink-display unique=eink-display"; printf "\$st" "\$mod"; } > "\$RD/dri-states.txt"
        [ "$1" = nodebugfs ] && : > "\$RD/dri-states.txt"
        # status: transport is attached lazily at the first (dry-run) update (review 38 R2)
        s='enable %s waveform %s transport %s hv_on 0 stall_fault 0 template %s\nupdates %s aborts 0 frames 10 late 0 vbl_skips 0 commit_fail 0 max_gap_us 0 late_drive %s vbl_skips_drive 0 max_gap_drive_us %s\nrail_fault 0 rail_faults 0\nlast mode 0x203 pre 0x0 clear 1 type 2 bin 7 t1 25 t2 25 frames 40 steps 40 dry %s aborted 0\n'
        printf "\$s" 0 0 0 0 0 0 0 0 > "\$RD/eink/eink-status-before.txt"
        cp "\$RD/dmesg.txt" "\$RD/eink/dmesg-after.txt"
        tr=1; [ "$1" = notransport ] && tr=0
        [ \$tr = 1 ] && echo "[ 99.0] a9-eink eink-display: transport on msm-kms: 1648x... frame period 11765 us" >> "\$RD/eink/dmesg-after.txt"
        [ "$1" = latefault ] && echo "[ 130.0] msm 5900000.gpu: [drm:hangcheck_handler] *ERROR* 67.5.10.1: hangcheck detected gpu lockup rb 0!" >> "\$RD/eink/dmesg-after.txt"
        [ "$1" = nodmesgafter ] && rm -f "\$RD/eink/dmesg-after.txt"
        case "\$*" in *--waveform*)
          if [ "$1" = noblobs ]; then echo "!! E Ink blob install FAILED: no root shell after 60 s"; echo "   !! E Ink session stopped: waveform/template NOT installed"
          else echo "   E Ink blobs installed as root (via nc), verified on the phone: a9-waveform 0123"; fi ;; esac
        case "\$*" in
          *--open-gates*)
            ld=0; gap=15300; [ "$1" = late ] && ld=3; printf "\$s" 1 1 1 1 16 \$ld \$gap 0 > "\$RD/eink/eink-status-open.txt"
            printf "\$s" 0 1 1 1 16 \$ld \$gap 0 > "\$RD/eink/eink-status-after.txt"
            echo 0 > "\$RD/eink/compare.rc"; [ "$1" = nocompare ] && echo 3 > "\$RD/eink/compare.rc"
            echo "t pace ..." > "\$RD/eink/pace.log" ;;
          *--waveform*)
            gb='N N N '; [ "$1" = gateopen ] && gb='N Y N '
            printf 'gates before: %s\nkicked /sys/class/drm/card2-DPI-1/eink/force_clear\ngates after: %s\n' "\$gb" "\$gb" > "\$RD/eink/dryrun.txt"
            dr=1; [ "$1" = realupdate ] && dr=0
            printf "\$s" 0 1 \$tr 1 1 0 0 \$dr > "\$RD/eink/eink-status-after.txt" ;;
          *) printf "\$s" 0 0 0 0 0 0 0 0 > "\$RD/eink/eink-status-after.txt" ;;
        esac
        echo "   collected into \$RD"; echo a > "$T/state"; exit 0 ;;
    esac ;;
esac
exit 0
S
  cat > "$T/health" <<S
#!/bin/sh
[ "\$(cat "$T/state")" = a ] && { echo '{"mode":"android","slot":"_a","boot_completed":"1"}'; exit 0; }
echo '{"mode":"'"\$(cat "$T/state")"'"}'; exit 1
S
  printf '#!/bin/sh\necho "adb $*" >> "%s/calls"\n' "$T" > "$T/adb"
  chmod +x "$T/ab" "$T/health" "$T/adb"
}
sup() { A9_GPU_ATTENDED=${ATT:-yes} A9_SUP_ANDROID_BOOT="$T/ab" A9_SUP_HEALTH="$T/health" A9_SUP_RUNS="$T/runs" A9_SUP_OUT="$T/out" \
        A9_SUP_ADB="$T/adb" A9_SUP_LOCK="$T/a9-usb.lock" A9_SUP_POLL=1 A9_SUP_RETRY_SLEEP=1 A9_EINK_E5C_PASSED=${E5C:-yes} \
        "$SUPV" --code c "$@" > "$T/sup.out" 2>&1; }
log() { cat "$T"/runs/*-supervise/supervise.log 2>/dev/null; }
restores() { local n; n=$(grep -c '^--restore' "$T/calls" 2>/dev/null); echo "${n:-0}"; }
check() {  # check <name> <want rc> <got rc> <want state> <want restores or ->
  local st; st=$(cat "$T/state")
  if [ "$3" = "$2" ] && [ "$st" = "$4" ] && { [ "$5" = - ] || [ "$(restores)" = "$5" ]; }; then ok "$1 (rc $3, phone $st, restores $(restores))"
  else bad "$1: rc $3 want $2, phone $st want $4, restores $(restores) want $5"; { cat "$T/sup.out"; log; } | sed 's/^/     /' | tail -8; fi
  pkill -f "sleep 300" 2>/dev/null; rm -rf "$T"
}
echo "-- G1"
setup ok 0; sup --stage g1; check "G1 good run" 0 $? a 0
setup angle 0; sup --stage g1; check "G1: GPU fell back to ANGLE -> FAIL" 1 $? a 0
setup fault 0; sup --stage g1; check "G1: SMMU fault in kmsg -> FAIL" 1 $? a 0
setup nokmsg 0; sup --stage g1; check "2c: dmesg not real kmsg -> FAIL (no false PASS)" 1 $? a 0
setup nomesa 0; sup --stage g1; check "2c: SurfaceFlinger not on FD610 -> FAIL" 1 $? a 0
setup timeout 0; sup --stage g1; check "child USB timeout -> supervisor restores" 1 $? a 1
setup timeout 2; sup --stage g1; check "restore fails twice, third restores" 1 $? a 3
setup timeout 99; sup --stage g1 --recover-window 4; check "restore never succeeds -> exit 3" 3 $? bootloader -
setup hang 0; sup --stage g1 --max-run 3; check "child hangs -> overrun, group kill, restore" 1 $? a 1
setup orphans 0; sup --stage g1 --max-run 3; rc=$?
if grep -q 'USB lock still held by' "$T"/runs/*-supervise/supervise.log && ! kill -0 "$(cat "$T/escaped")" 2>/dev/null; then ok "2b: lock holder outside the child's group found and killed"; else bad "2b: escaped lock holder not handled"; fi
check "2b: orphans hold the USB lock -> killed, restore succeeds" 1 $rc a 1
# 2a: a hangup / group signal to the CALLER's group must not reach the supervisor
setup hang 0
( A9_GPU_ATTENDED=yes A9_SUP_ANDROID_BOOT="$T/ab" A9_SUP_HEALTH="$T/health" A9_SUP_RUNS="$T/runs" A9_SUP_OUT="$T/out" A9_SUP_ADB="$T/adb" \
  A9_SUP_LOCK="$T/a9-usb.lock" A9_SUP_POLL=1 A9_SUP_RETRY_SLEEP=1 setsid bash -c '"$0" --stage g1 --code c --max-run 6 & sleep 2; kill -HUP 0; kill -TERM 0' "$SUPV" ) >/dev/null 2>&1
for i in $(seq 1 90); do ls "$T"/runs/*-supervise/verdict.txt >/dev/null 2>&1 && break; sleep 1; done
grep -q 'overrun' "$T"/runs/*-supervise/supervise.log 2>/dev/null && [ "$(cat "$T/state")" = a ] \
  && ok "2a: HUP+TERM to the caller's process group: supervisor survived, ran to its own end and restored" || { bad "2a: supervisor did not survive the group hangup"; log | tail -5; }
pkill -f "sleep 300" 2>/dev/null; rm -rf "$T"
setup hang 0; sup --stage g1 & sleep 3
p=$(sed -n 's/^gpu-supervise\[\([0-9]*\)\] stage.*/\1/p' "$T"/runs/*-supervise/supervise.log | head -n1); kill -TERM "$p" 2>/dev/null; wait
grep -q 'interrupted (TERM)' "$T"/runs/*-supervise/supervise.log && check "TERM to the supervisor pid (from its log) -> group kill, restore" 1 1 a 1 || { bad "TERM by logged pid"; log | tail -4; rm -rf "$T"; }
setup ok 0; ATT=no sup --stage g1; rc=$?; ! grep -q -- '--write' "$T/calls" 2>/dev/null && check "unattended refused" 2 $rc a 0 || { bad "unattended wrote"; rm -rf "$T"; }
setup ok 0 1; sup --stage g1; rc=$?; ! grep -q -- '--write' "$T/calls" && check "offline check fails -> refused" 2 $rc a 0 || { bad "wrote after failed offline"; rm -rf "$T"; }
echo "-- G2a (gates closed)"
G2A=(--stage g2a --waveform w --template t)
setup ok 0; sup "${G2A[@]}"; check "G2a good run (transport attached by the dry run, after-session kmsg)" 0 $? a 0
setup ok 0; sup --stage g2a; check "G2a without --waveform/--template -> refused (R2 needs the dry run)" 2 $? a 0
setup ok 0; sup "${G2A[@]}"; rc=$?; grep -q -- '--write --code c --eink --waveform w --template t --boot .*a9-android-eink-boot-gpu.img --vendor .*vendor_b-eink-gpu.img' "$T/calls" && ! grep -q -- '--open-gates' "$T/calls" && check "G2a uses the E Ink pair, gates closed" 0 $rc a 0 || { bad "G2a args"; rm -rf "$T"; }
setup ubwc 0; sup "${G2A[@]}"; check "G2a: non-LINEAR a9_eink framebuffer -> FAIL" 1 $? a 0
setup rail 0; sup "${G2A[@]}"; check "G2a: E Ink fault latched in kmsg -> FAIL" 1 $? a 0
setup uniqueonly 0; sup "${G2A[@]}"; check "R1: a9_eink state only under dri/<unique> -> found by driver name, PASS" 0 $? a 0
setup nodebugfs 0; sup "${G2A[@]}"; check "R1: no DRM states (debugfs not mounted) -> FAIL" 1 $? a 0
setup notransport 0; sup "${G2A[@]}"; check "R2: transport still 0 after the dry run -> FAIL" 1 $? a 0
setup gateopen 0; sup "${G2A[@]}"; check "R2: a gate read open around the dry run -> FAIL" 1 $? a 0
setup realupdate 0; sup "${G2A[@]}"; check "R2: last update not dry with gates closed -> FAIL" 1 $? a 0
setup latefault 0; sup "${G2A[@]}"; check "R3: GPU hangcheck only in the after-session kmsg -> FAIL" 1 $? a 0
setup nodmesgafter 0; sup "${G2A[@]}"; check "R3: no after-session kmsg -> FAIL" 1 $? a 0
setup noblobs 0; sup "${G2A[@]}"; rc=$?; grep -q 'E Ink blobs NOT installed (E Ink blob install FAILED: no root shell' "$T"/runs/*-supervise/verdict.txt && check "pushfix: blobs not installed as root -> FAIL, named in the verdict" 1 $rc a 0 || { bad "pushfix: verdict does not name the install failure"; cat "$T"/runs/*-supervise/verdict.txt; rm -rf "$T"; }
setup nomapwc 0; sup "${G2A[@]}"; check "R6: vkms.map_wc not Y at collect -> FAIL" 1 $? a 0
setup nomapwc 0; sup --stage g1; check "R6 (G1): vkms.map_wc not Y at collect -> FAIL" 1 $? a 0
echo "-- G2b (gates open)"
setup ok 0; sup --stage g2b --after /nonexistent --waveform w --template t --bins 5-9; check "G2b without a g2a PASS -> refused" 2 $? a 0
mkg2a() { mkdir -p "$T/g2a"; echo "verdict: G2A PASS restored=yes" > "$T/g2a/verdict.txt"
          echo "gpu-supervise[1] stage g2a: boot $(sha256sum "$T/out/a9-android-eink-boot-gpu.img" | cut -c1-16) vendor $(sha256sum "$T/out/vendor_b-eink-gpu.img" | cut -c1-16) max-run" > "$T/g2a/supervise.log"; }
setup ok 0; mkg2a; E5C=no sup --stage g2b --after "$T/g2a" --waveform w --template t --bins 5-9; check "G2b without A9_EINK_E5C_PASSED -> refused" 2 $? a 0
setup ok 0; mkg2a; echo x >> "$T/out/vendor_b-eink-gpu.img"; sup --stage g2b --after "$T/g2a" --waveform w --template t --bins 5-9; check "G2b: g2a PASS was for other images -> refused" 2 $? a 0
setup ok 0; mkg2a; sup --stage g2b --after "$T/g2a" --waveform w --template t --bins 5-9 --gate-secs 60; rc=$?
grep -q -- '--open-gates --bins 5-9 --gate-secs 60' "$T/calls" && check "G2b good run (gates open, compare 0, pace)" 0 $rc a 0 || { bad "G2b args"; rm -rf "$T"; }
setup late 0; mkg2a; sup --stage g2b --after "$T/g2a" --waveform w --template t --bins 5-9; check "G2b: late_drive > 0 -> FAIL" 1 $? a 0
setup nocompare 0; mkg2a; sup --stage g2b --after "$T/g2a" --waveform w --template t --bins 5-9; check "G2b: eink-compare failed -> FAIL" 1 $? a 0
setup latefault 0; mkg2a; sup --stage g2b --after "$T/g2a" --waveform w --template t --bins 5-9; check "R3 (G2b): hangcheck inside the gates-open window -> FAIL" 1 $? a 0
setup hang 0; mkg2a; sup --stage g2b --after "$T/g2a" --waveform w --template t --bins 5-9 --max-run 3; rc=$?
if [ "$(grep -c '^adb shell setprop vendor.a9ml.eink.gate closed' "$T/calls")" = 2 ] \
   && [ "$(grep -n 'setprop vendor.a9ml.eink.gate closed\|^--restore' "$T/calls" | tail -n1 | grep -c restore)" = 1 ]; then ok "R4: g2b overrun -> gates closed (before + after the group kill) before the restore"
else bad "R4: gates not closed on a g2b stop"; cat "$T/calls" | sed 's/^/     /'; fi
check "R4: g2b overrun -> restore" 1 $rc a 1
setup hang 0; sup --stage g1 --max-run 3; rc=$?
grep -q 'eink.gate' "$T/calls" && bad "R4: g1 stop sent an E Ink gate prop" || ok "R4: g1 stop sends no E Ink gate prop"
check "g1 overrun -> restore" 1 $rc a 1
[ $fails = 0 ] && echo "test-gpu-supervise: PASS" || { echo "test-gpu-supervise: $fails FAILED"; exit 1; }
