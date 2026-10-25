#!/usr/bin/env bash
# test-s2c-vendor.sh -- s2c-stage.py + s2c-vendor.py against the real (local-only) S2a inputs.
# Skips when the S2a build is not on this machine. Offline; every output in a temp dir.
#  - pass twice -> the same image sha256 (deterministic: fixed timestamps, no host times);
#  - stage refusals: tampered stage file, bundle tuple mismatch, uncommitted repo files are
#    covered by s2c-stage.py's own checks (tuple mismatch tested here);
#  - vendor refusals: wrong base sha, tampered stage file, kernel pin mismatch, and the read
#    back catching a change outside the stage (S2C_TEST_FAULT: a base file removed, a base
#    file's mode changed, a stage file's label changed).
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
S1=${S2C_S1_BUILD:-$HOME/A9-build/mainline/sensors-s1}
A=$S1/build-20261018-s2a
TUPLE=c0a4ad5aa8aeefd0749aef40b5041971f4a9dab4c29c4e504ccdd34e38ce41ae
BASE=$A/android/out/vendor_b-eink-gpu-bt-health-thermal.img
BASE_SHA=4b08eadff33f64e749e01f6e24aa4c9224ee10b4e1567e86b04933929a39cf53
BOOT=$A/android/out/a9-android-eink-boot-gpu.img
HAL=${S2C_HAL:-$HOME/A9-build/mainline/sensors-s2c/out/android.hardware.sensors-service.a9}
PROBE=${S2C_PROBE:-$S1/build-20261018-s2c/probe/s2-sns-probe}     # s2-build-probe.py output (+ .build.json)
QEMU=${S2C_QEMU:-/nix/store/va22pj11gdvzz2vzb8410f62lj53mjq2-qemu-user-10.1.0/bin/qemu-aarch64}
[ -f "$BASE" ] && [ -f "$HAL" ] && [ -f "$PROBE" ] && [ -x "$QEMU" ] || { echo "SKIP: S2a inputs or HAL not on this machine"; exit 0; }
T=$(mktemp -d "${S2C_TMP:-${TMPDIR:-/tmp}}/s2c-vendor-test.XXXXXX"); trap 'rm -rf "$T"' EXIT
fails=0; ok() { echo "  ok   $1"; }; bad() { echo "  FAIL $1"; fails=$((fails+1)); }
HSHA=$(sha256sum "$HAL" | cut -d' ' -f1)
stage() { python3 -I "$ROOT/scripts/mainline/sensors/s2c-stage.py" --bundle "$A/bundle" --tuple "$1" --hal "$HAL" \
  --hal-sha256 "$HSHA" --repo "$ROOT" --out "$2" --test-switch --probe "${3:-$PROBE}" --qemu "$QEMU"; }
vendor() { python3 -I "$ROOT/scripts/mainline/sensors/s2c-vendor.py" --base "$BASE" --base-sha256 "${VB:-$BASE_SHA}" \
  --stage "$1" --boot "$BOOT" --out "$2" --work "$T"; }
expect_fail() {   # <name> <expected stderr fragment> <cmd...>
  local name=$1 want=$2; shift 2
  if out=$("$@" 2>&1); then bad "$name: accepted"; elif grep -q -- "$want" <<<"$out"; then ok "refused: $name"; else bad "$name: $out"; fi
}
echo "== s2c-stage / s2c-vendor"
stage "$TUPLE" "$T/st" >/dev/null && ok "stage" || bad stage
expect_fail "bundle tuple mismatch" "do not match --tuple" stage 0000 "$T/st-bad"
# review 79l B1: the probe must be this tree's build, have --wait, and its --wait mode must RUN
mkprobe() {   # <dir> <extra bytes>: the S2a probe (no --wait) with bytes appended, and a receipt that matches it
  mkdir -p "$1"; cp "$A/bundle/s2-sns-probe" "$1/s2-sns-probe"; chmod u+w "$1/s2-sns-probe"; printf '%s' "$2" >> "$1/s2-sns-probe"
  python3 -I - "$PROBE.build.json" "$1/s2-sns-probe" <<'PY'
import hashlib,json,sys
r=json.load(open(sys.argv[1])); r['output_sha256']=hashlib.sha256(open(sys.argv[2],'rb').read()).hexdigest()
open(sys.argv[2]+'.build.json','w').write(json.dumps(r))
PY
}
mkdir -p "$T/p-s2a"; cp "$A/bundle/s2-sns-probe" "$T/p-s2a/"; python3 -I -c 'import json,sys;r=json.load(open(sys.argv[1]));r["output_sha256"]="3bc3c6e65f2979b4d3830feb408a569d6129d82bdb4f5b84fabf2596cd8f11ca";open(sys.argv[2],"w").write(json.dumps(r))' "$PROBE.build.json" "$T/p-s2a/s2-sns-probe.build.json"
expect_fail "the S2a probe (no --wait)" "is the S2a probe without --wait" stage "$TUPLE" "$T/st-p1" "$T/p-s2a/s2-sns-probe"
mkprobe "$T/p-nowait" x
expect_fail "a probe without --wait" "probe has no --wait mode" stage "$TUPLE" "$T/st-p2" "$T/p-nowait/s2-sns-probe"
mkprobe "$T/p-fake" --wait
expect_fail "a probe whose --wait mode does not run (qemu rc 2)" "staged probe under qemu" stage "$TUPLE" "$T/st-p3" "$T/p-fake/s2-sns-probe"
cp "$PROBE" "$T/p-src"; cp "$PROBE.build.json" "$T/p-src.build.json"
python3 -I -c 'import json,sys;p=sys.argv[1];r=json.load(open(p));r["sources"]["s2-sns.c"]="0"*64;open(p,"w").write(json.dumps(r))' "$T/p-src.build.json"
expect_fail "a probe built from other sources" "other sources than this tree" stage "$TUPLE" "$T/st-p4" "$T/p-src"
vendor "$T/st" "$T/v1.img" > "$T/v1.log" 2>&1 && grep -q '^S2C-VENDOR PASS' "$T/v1.log" && ok "vendor 1" || { bad "vendor 1"; cat "$T/v1.log"; }
vendor "$T/st" "$T/v2.img" > "$T/v2.log" 2>&1 && cmp -s "$T/v1.img" "$T/v2.img" && ok "deterministic: same sha256 twice" || bad "not deterministic"
rm -f "$T/v2.img"*
# the probe INSIDE the built image: run it under qemu exactly as a9-sensors.sh does (and the whole
# a9-sensors.sh host test with that real binary instead of the stub)
debugfs -R "dump /bin/s2-sns-probe $T/img-probe" "$T/v1.img" >/dev/null 2>&1; chmod +x "$T/img-probe"
"$QEMU" "$T/img-probe" --wait 400 1 > "$T/img-probe.out" 2>&1; rc=$?
[ "$rc" = 1 ] && [ "$(cat "$T/img-probe.out")" = "S2-WAIT-FAIL no-unique-sns-400" ] && cmp -s "$T/img-probe" "$PROBE" \
  && ok "image /bin/s2-sns-probe = the built probe; --wait 400 1 -> rc 1 S2-WAIT-FAIL (never 2)" || bad "image probe rc=$rc $(cat "$T/img-probe.out")"
A9S_TEST_PROBE="$T/img-probe" A9S_TEST_QEMU="$QEMU" A9S_REQUIRE_REAL_PROBE=1 bash "$ROOT/scripts/mainline/android/tests/test-sensors-setup.sh" > "$T/setup-real.log" 2>&1 \
  && grep -q '^  ok   real staged probe' "$T/setup-real.log" && ok "a9-sensors.sh host test with the image's real probe" || { bad "setup test with the real probe"; tail -5 "$T/setup-real.log"; }
VB=0000 expect_fail "wrong base sha" "base vendor sha256" vendor "$T/st" "$T/x.img"
cp -a "$T/st" "$T/st-t"; echo x >> "$T/st-t/tree/bin/a9-sensors.sh"
expect_fail "tampered stage file" "stage file bin/a9-sensors.sh changed" vendor "$T/st-t" "$T/x.img"
rm -rf "$T/st-t"; cp -a "$T/st" "$T/st-k"
python3 -I - "$T/st-k/stage.json" <<'PY'
import json,sys; p=sys.argv[1]; d=json.load(open(p)); d['pins']['kernel_image_sha256']='0'*64; open(p,'w').write(json.dumps(d))
PY
expect_fail "kernel pin mismatch" "kernel pairing" vendor "$T/st-k" "$T/x.img"
rm -rf "$T/st-k"
S2C_TEST_FAULT='rm /etc/thermal-a9.conf' expect_fail "read back: base file removed" "path set" vendor "$T/st" "$T/x.img"
S2C_TEST_FAULT='sif /bin/a9ml-gpu.sh mode 0100777' expect_fail "read back: base mode changed" "metadata of base path bin/a9ml-gpu.sh" vendor "$T/st" "$T/x.img"
S2C_TEST_FAULT='ea_set /bin/s1-listener security.selinux u:object_r:system_file:s0' expect_fail "read back: stage label changed" "bin/s1-listener" vendor "$T/st" "$T/x.img"
[ ! -e "$T/x.img" ] && ok "no output left by any refusal" || bad "refusal left an image"
# build-vendor.sh --sensors path: s2c-install-stage.py into a fake vendor staging tree
INS="$ROOT/scripts/mainline/sensors/s2c-install-stage.py"
KI=$(python3 -I -c "import json,sys;print(json.load(open(sys.argv[1]))['pins']['kernel_image_sha256'])" "$T/st/stage.json")
mkst() { rm -rf "$T/vs"; mkdir -p "$T/vs/lib/modules" "$T/vs/etc/selinux" "$T/vs/bin/hw" "$T/vs/etc/init" "$T/vs/etc/vintf/manifest" "$T/vs/firmware"
  echo "$KI" > "$T/vs/lib/modules/kernel-image.sha256"; cp "$T/st/tree/lib/modules/mdt_loader.ko" "$T/vs/lib/modules/"
  echo 'msm.ko separate_gpu_kms=1' > "$T/vs/lib/modules/modules.options"; echo '/vendor(/.*)? u:object_r:vendor_file:s0' > "$T/vs/etc/selinux/vendor_file_contexts"
  echo 'ro.zygote=zygote64' > "$T/vs/build.prop"; : > "$T/fsc"; }
mkst
if python3 -I "$INS" --stage "$T/st" --vendor-stage "$T/vs" --kernel-image-sha256 "$KI" --fsconfig "$T/fsc" >/dev/null \
   && python3 -I "$INS" --stage "$T/st" --vendor-stage "$T/vs" --props-only >/dev/null; then
  d=$( (cd "$T/st/tree" && find . -type f | xargs sha256sum | LC_ALL=C sort) ); v=$( (cd "$T/vs" && find . -type f | xargs sha256sum | LC_ALL=C sort) )
  extra=$(LC_ALL=C comm -13 <(echo "$d") <(echo "$v") | awk '{print $2}' | LC_ALL=C sort | tr '\n' ' ')
  [ -z "$(LC_ALL=C comm -23 <(echo "$d") <(echo "$v"))" ] && [ "$extra" = "./build.prop ./etc/selinux/vendor_file_contexts ./lib/modules/kernel-image.sha256 ./lib/modules/modules.options " ] \
    && ok "install-stage: every stage file, nothing else (besides the 4 pre-existing files)" || bad "install-stage tree: extra=$extra"
  [ "$(cat "$T/vs/lib/modules/modules.options")" = "$(printf 'msm.ko separate_gpu_kms=1\nqcom_q6v5_pas.ko a9_adsp_manual_start=1\nfastrpc.ko a9_sensor_fastrpc=1')" ] \
    && grep -qx 'ro.vendor.a9ml.sensors=1' "$T/vs/build.prop" && grep -qx 'persist.a9.sensors=1' "$T/vs/build.prop" \
    && grep -q 'sensors-service\\.a9 u:object_r:hal_sensors_default_exec:s0' "$T/vs/etc/selinux/vendor_file_contexts" \
    && grep -qx 'bin/hw/android.hardware.sensors-service.a9 0 2000 0755' "$T/fsc" && ! grep -q '^lib/modules ' "$T/fsc" \
    && [ "$(stat -c %a "$T/vs/bin/a9-sensors.sh")" = 755 ] \
    && ok "install-stage: options, props (test switch), HAL label, fsconfig, modes" || bad "install-stage appends"
  expect_fail "install-stage: props twice" "already has sensors properties" python3 -I "$INS" --stage "$T/st" --vendor-stage "$T/vs" --props-only
  expect_fail "install-stage: into a tree that has them" "already exists" python3 -I "$INS" --stage "$T/st" --vendor-stage "$T/vs" --kernel-image-sha256 "$KI" --fsconfig "$T/fsc"
else bad "install-stage pass"; fi
mkst; expect_fail "install-stage: other kernel" "the vendor modules are for kernel" python3 -I "$INS" --stage "$T/st" --vendor-stage "$T/vs" --kernel-image-sha256 0000 --fsconfig "$T/fsc"
mkst; echo different > "$T/vs/lib/modules/mdt_loader.ko"
expect_fail "install-stage: different module of the same name" "lib/modules/mdt_loader.ko already exists" python3 -I "$INS" --stage "$T/st" --vendor-stage "$T/vs" --kernel-image-sha256 "$KI" --fsconfig "$T/fsc"
# build-vendor.sh: a9-sensors.sh never reaches a default vendor through the bin/*.sh loop
grep -q '\[ "${f##\*/}" = a9-sensors.sh \] && continue' "$ROOT/scripts/mainline/android/build-vendor.sh" \
  && ok "build-vendor.sh: a9-sensors.sh only via --sensors" || bad "build-vendor.sh loop installs a9-sensors.sh"
echo "== result: $fails failure(s)"
[ "$fails" = 0 ] && echo "S2C-VENDOR OFFLINE TEST PASS"
exit $((fails > 0))
