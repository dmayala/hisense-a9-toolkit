#!/usr/bin/env bash
# Offline tests of the a9_eink engine (the same C the kernel module builds):
#  1. selftest: framebuffer -> grey for all formats and rects, damage, mask
#  2. engine frames == a9tcon CLI frames (the CLI is checked byte for byte
#     against the stock composer under Unicorn by tcon/test-tcon-c.py), over
#     modes x previous modes x clear x temperatures x inputs, sha256 of all
#     frames of each update
#  3. chained updates: the engine's persistent state (old levels, previous mode,
#     step arrays) vs fresh CLI replays fed with the engine's own levels
#  4. apitest: a9e_ct_check refuses corrupted templates; the split API
#     (a9e_assume_white/a9e_prepare/a9e_init_end/a9e_start) == a9e_begin
#  5. bench: host timings (report only)
# All with the control template: every output buffer and the hold cache start
# as the template, frames are compared as whole 32-bit words (engine vs
# `a9tcon --template`), and every engine frame is checked: bytes 1..3 == the
# template, null frames == the template.
# Usage: run-tests.sh COMPOSER [TMPDIR]   (no phone access)
#   A9_CT_TEMPLATE  control template (default ~/A9-build/mainline/firmware/hisense/a9-ctrl-template.bin;
#                   a raw 1,485,568-byte template works too)
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
C=${1:?composer binary (vendor.qti.hardware.display.composer-service, sha256 8c89557b...)}
CT=${A9_CT_TEMPLATE:-$HOME/A9-build/mainline/firmware/hisense/a9-ctrl-template.bin}
[[ -f "$CT" ]] || { echo "control template missing: $CT (set A9_CT_TEMPLATE)"; exit 2; }
T=${2:-$(mktemp -d)}
mkdir -p "$T"
nix-shell -p gcc gnumake --run "make -s -C '$HERE' && make -s -C '$HERE/../tcon' build/a9tcon" >/dev/null
CLI=$HERE/../tcon/build/a9tcon
ENG=$HERE/build/a9e-test
"$HERE/build/a9wf-extract" "$C" "$T/wf.bin" >/dev/null
"$ENG" "$T/wf.bin" selftest --template "$CT"
"$ENG" "$T/wf.bin" apitest --template "$CT"

python3 -I - "$T" <<'EOF'
import os, sys, random
t = sys.argv[1]; W, H = 1648, 824
random.seed(7)
open(f"{t}/g_rand", "wb").write(bytes(random.getrandbits(8) for _ in range(W * H)))
open(f"{t}/g_grad", "wb").write(bytes(((x * 255) // (W - 1) if y < H // 2 else (16 * ((x * 16) // W)) % 256) for y in range(H) for x in range(W)))
open(f"{t}/o_rand", "wb").write(bytes(random.getrandbits(4) for _ in range(W * H)))
EOF

pass=0 fail=0
cmp_case() { # engine-args cli-args (both with the template)
	local e c
	e=$("$ENG" "$T/wf.bin" update $1 --template "$CT" | tail -1) || e="engine exit $? ($e)"
	c=$("$CLI" "$C" --template "$CT" $2 | tail -1)
	if [[ "$e" == "$c" ]]; then pass=$((pass + 1)); else fail=$((fail + 1)); echo "FAIL [$1] engine: $e / cli: $c"; fi
}

for g in g_rand g_grad; do
	for old in "" "--old $T/o_rand"; do
		for mode in 0x201 0x203 0x206 0x209 0x401 0x303 0x202 0x208; do
			for pre in "$mode" 0x201 0x203 0x206 0x209; do
				for clr in 0 1; do
					for tt in "--t1 25 --t2 25" "--t1 3 --t2 48"; do
						a="--grey $T/$g $old --mode $mode --pre $pre --clear $clr $tt"
						cmp_case "$a" "$a"
					done
				done
			done
		done
	done
done
# init flash at three bins
for tt in "--t1 0 --t2 0" "--t1 25 --t2 25" "--t1 50 --t2 50"; do
	e=$("$ENG" "$T/wf.bin" init $tt --template "$CT" | tail -1) || e="engine exit $?"
	c=$("$CLI" "$C" --init $tt --template "$CT" | tail -1)
	if [[ "$e" == "$c" ]]; then pass=$((pass + 1)); else fail=$((fail + 1)); echo "FAIL init [$tt]"; fi
done
# without a template both sides keep bytes 1..3 zero: still equal
for a in "--init --t1 25 --t2 25" "--grey $T/g_grad --mode 0x203 --pre 0x203 --clear 1 --t1 30 --t2 30"; do
	if [[ $a == --init* ]]; then e=$("$ENG" "$T/wf.bin" init ${a#--init } | tail -1); else e=$("$ENG" "$T/wf.bin" update $a | tail -1); fi
	c=$("$CLI" "$C" $a | tail -1)
	if [[ "$e" == "$c" ]]; then pass=$((pass + 1)); else fail=$((fail + 1)); echo "FAIL no-template [$a]"; fi
done
# chained: engine levels of update n are the CLI's --old of update n+1
prev=0x201 old=""
for mode in 0x203 0x206 0x206 0x209 0x201 0x203 0x201; do
	a="--grey $T/g_rand $old --mode $mode --pre $prev --clear 0"
	"$ENG" "$T/wf.bin" update $a --levels "$T/lv_next" >/dev/null
	cmp_case "$a" "$a"
	mv "$T/lv_next" "$T/lv_cur"; old="--old $T/lv_cur"; prev=$mode
done
"$ENG" "$T/wf.bin" update --grey "$T/g_rand" --mode 0x201 --time --template "$CT" | grep time
"$ENG" "$T/wf.bin" bench --template "$CT"
echo "engine vs CLI: $pass pass, $fail fail"
[[ $fail -eq 0 ]]
