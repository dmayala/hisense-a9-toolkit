#!/usr/bin/env bash
# eink-fw-install.sh <waveform> [<template>] -- install the user's E Ink blobs
# on the running mainline Android AS ROOT, into a root-owned directory:
#   /data/local/tmp/a9fw            root:root 0755  (firmware_class.path)
#   /data/local/tmp/a9fw/hisense    root:root 0755
#   .../a9-waveform.bin, .../a9-ctrl-template.bin   root:root 0644
# Called by android-boot.sh (eink_session). runs/gpu-g2a-pushfix.md.
#
# Why: G2a 2026-10-16 (gpu 859fd31). The GPU vendor's a9ml-gpu.sh setup runs
# as root at post-fs-data and creates /data/local/tmp/a9fw for the GPU
# firmware, so adbd (uid 2000) can no longer mkdir hisense/ or push into it.
# On the E Ink/Wi-Fi vendors nothing created a9fw before the push, so adb shell
# created it as shell. A shell-writable firmware dir is a TOCTOU risk
# (review 43), so the blobs now go: adb push (shell) -> private staging dir ->
# root cp into the root-owned tree -> sha256 of the INSTALLED file == host.
#
# Root: adbd as root, else the vendor's loopback root shell (nc 127.0.0.1
# 2324, started at post-fs-data); waited for up to A9M_ROOT_WAIT s (60).
# Exit 0 installed and verified; 2 no root shell; 3 staging push failed;
# 4 install/verify failed. Every failure prints one "!! E Ink blob install
# FAILED: ..." line. Nothing here opens a gate or touches the driver.
set -uo pipefail
ADB=${ADB:-adb}
WF="${1:-}" CT="${2:-}"
[ -n "$WF" ] && [ -f "$WF" ] || { echo "!! E Ink blob install FAILED: no waveform file '${WF}'"; exit 4; }
[ -z "$CT" ] || [ -f "$CT" ] || { echo "!! E Ink blob install FAILED: no template file '$CT'"; exit 4; }
FWD=/data/local/tmp/a9fw H=/data/local/tmp/a9fw/hisense STG=/data/local/tmp/a9-stage-$$
die() { echo "!! E Ink blob install FAILED: $2"; timeout 10 "$ADB" shell rm -rf "$STG" </dev/null >/dev/null 2>&1; exit "$1"; }

# ---- root channel
ROOT="" end=$(( $(date +%s) + ${A9M_ROOT_WAIT:-60} ))
while :; do
  u=$(timeout 10 "$ADB" shell id -u </dev/null 2>/dev/null | tr -d '\r')
  if [ "$u" = 0 ]; then ROOT=adbd; break; fi
  r=$(timeout 10 "$ADB" shell "echo id -u | nc -w 3 127.0.0.1 2324" </dev/null 2>/dev/null | tr -d '\r' | tail -n1)
  if [ "$r" = 0 ]; then ROOT=nc; break; fi
  [ "$(date +%s)" -lt "$end" ] || break
  sleep 3
done
[ -n "$ROOT" ] || die 2 "no root shell after ${A9M_ROOT_WAIT:-60} s (adbd uid ${u:-?}, loopback 2324 silent); blobs NOT installed"
# rsh CMD: run CMD as root (CMD must not contain single quotes)
rsh() { if [ "$ROOT" = adbd ]; then timeout 60 "$ADB" shell "$1" </dev/null 2>&1
        else timeout 60 "$ADB" shell "echo '$1' | nc -w 30 127.0.0.1 2324" </dev/null 2>&1; fi; }

# ---- stage as shell (a fresh private dir), then install as root
timeout 10 "$ADB" shell "umask 077; rm -rf $STG; mkdir $STG" </dev/null >/dev/null 2>&1 || die 3 "cannot create staging dir $STG"
timeout 60 "$ADB" push "$WF" "$STG/a9-waveform.bin" </dev/null >/dev/null 2>&1 || die 3 "adb push of the waveform to $STG failed"
if [ -n "$CT" ]; then timeout 60 "$ADB" push "$CT" "$STG/a9-ctrl-template.bin" </dev/null >/dev/null 2>&1 || die 3 "adb push of the template to $STG failed"; fi
names="a9-waveform.bin"; [ -n "$CT" ] && names="$names a9-ctrl-template.bin"
cmd="umask 022; mkdir -p $H && chown root:root $FWD $H && chmod 0755 $FWD $H || echo A9FW-DIR-FAIL;"
for n in $names; do
  cmd="$cmd cp $STG/$n $H/.$n.new && chown root:root $H/.$n.new && chmod 0644 $H/.$n.new && mv -f $H/.$n.new $H/$n || echo A9FW-CP-FAIL $n;"
done
cmd="$cmd rm -rf $STG; for f in $FWD $H; do echo A9FW-STAT \$(stat -c %U:%G:%a \$f) \$f; done; for n in $names; do echo A9FW-STAT \$(stat -c %U:%G:%a $H/\$n) $H/\$n; echo A9FW-SHA \$(sha256sum $H/\$n); done"
out=$(rsh "$cmd" | tr -d '\r')
printf '%s\n' "$out" | grep -q 'A9FW-.*-FAIL' && die 4 "root install step failed: $(printf '%s' "$out" | grep 'A9FW-.*-FAIL' | tr '\n' ' ')"
# owner/mode of the tree as installed
for p in "$FWD:755" "$H:755"; do
  printf '%s\n' "$out" | grep -qx "A9FW-STAT root:root:${p##*:} ${p%:*}" || die 4 "${p%:*} is not root:root ${p##*:} after install ($(printf '%s\n' "$out" | grep " ${p%:*}\$" | head -n1))"
done
sums=""
for n in $names; do
  src=$WF; [ "$n" = a9-ctrl-template.bin ] && src=$CT
  want=$(sha256sum "$src" | cut -c1-64)
  printf '%s\n' "$out" | grep -qx "A9FW-STAT root:root:644 $H/$n" || die 4 "$H/$n is not root:root 644"
  got=$(printf '%s\n' "$out" | sed -n "s|^A9FW-SHA \([0-9a-f]\{64\}\) *$H/$n\$|\1|p")
  [ "$got" = "$want" ] || die 4 "$H/$n sha256 ${got:-<none>} != host ${want:0:16}..."
  sums="$sums ${n%.bin} ${want:0:16}"
done
echo "   E Ink blobs installed as root (via $ROOT), verified on the phone:$sums ($FWD + hisense root:root 755, files 644)"
exit 0
