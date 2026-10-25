#!/usr/bin/env bash
# qemu-e2e.sh -- end to end: the E Ink image's real kernel + initramfs (busybox,
# init, tcpsvd, a9-ctl, a9-eink-seq) in qemu-system-aarch64 -M virt, with the
# fake hardware of sim-lib.sh in an extra cpio, driven by the real host side
# (first-boot.sh --sim, over a forwarded a9-ctl port). No phone. ~6 min.
#   tests/mainline-eink-seq/qemu-e2e.sh [image dir, default ~/A9-build/mainline/out-eink]
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
O=${1:-$HOME/A9-build/mainline/out-eink}
T=$(mktemp -d); trap 'qkill; rm -rf "$T"' EXIT
# qemu runs under nix-shell with exec; kill it by its unique initrd path
qkill() { pkill -f "[q]emu-system-aarch64 .*-initrd $T/rd" 2>/dev/null; }
QP=
mkdir -p "$T/ov/test/bin" "$T/ov/test/fix"
cp "$HERE/sim-lib.sh" "$HERE/sim-update.sh" "$T/ov/test/"; cp "$HERE"/bin/{devmem,i2cget,i2ctransfer,modprobe,a9eink-pattern} "$T/ov/test/bin/"
install -m 755 "$HERE/test-init" "$T/ov/test-init"
# the E5a fixture's frames/sha256 = what the host CLI computes for this case
# WITH the control template (patch 0028), so the host's verdict can agree
W=${A9_MAINLINE:-$HOME/A9-build/mainline}
E5A_SHA=$(t=$(mktemp -d); python3 -I -c "
import sys
g = bytearray(1648 * 824)
for y in range(824):
    for x in range(1648):
        g[y * 1648 + x] = 17 * ((x * 16) // 1648)
open(sys.argv[1] + '/grey', 'wb').write(g); open(sys.argv[1] + '/old', 'wb').write(bytes([15]) * (1648 * 824))" "$t"
	"${A9_TCON_CLI:-$REPO/scripts/mainline/tcon/build/a9tcon}" "${A9_COMPOSER:-$HOME/source/a9-display-evidence-20261012/vendor/vendor.qti.hardware.display.composer-service}" \
		--template "${A9_EINK_CT:-$W/firmware/hisense/a9-ctrl-template.bin}" --grey "$t/grey" --old "$t/old" --mode 0x203 --pre 0x203 --clear 1 --t1 30 --t2 30 2>/dev/null \
		| tail -n1 | sed -n 's/^frames 60 sha256 \([0-9a-f]\{64\}\)$/\1/p'; rm -rf "$t")
[ -n "$E5A_SHA" ] || { echo "no E5a fixture hash: the a9tcon CLI with --template, the composer and the control template are needed"; exit 2; }
python3 -I - "$T/ov/test/fix" "$E5A_SHA" <<'PY'
import sys
d = sys.argv[1]
g = bytearray(1648 * 824)
for y in range(824):
    for x in range(1648):
        g[y * 1648 + x] = 17 * ((x * 16) // 1648)
open(d + '/grey', 'wb').write(g)
open(d + '/levels_before', 'wb').write(bytes([15]) * (1648 * 824))
open(d + '/last', 'w').write('mode 0x203 pre 0x203 clear 1 type 2 bin 10 t1 30 t2 30 t1_raw 30 t2_raw 30343\n'
    'rect 0,0-1648,824 steps 1 dry 1 aborted 0\nframes 60 sha256 %s\n' % sys.argv[2])
PY
(cd "$T/ov" && find . | LC_ALL=C sort | cpio -o -H newc -R 0:0 --quiet | gzip -9n) > "$T/ov.cpio.gz"
cat "$O/initramfs.cpio.gz" "$T/ov.cpio.gz" > "$T/rd.cpio.gz"
pass=0 fail=0
run() { # run <scenario> <flags> <expect regex in output>...
	local sc=$1 fl=$2; shift 2
	local P=$(( 20000 + RANDOM % 20000 ))
	echo "== QEMU scenario $sc ($fl)"
	nix-shell -p qemu --run "exec qemu-system-aarch64 -M virt -cpu cortex-a57 -smp 1 -m 1024 -nographic \
		-kernel '$O/Image.gz' -initrd '$T/rd.cpio.gz' \
		-append 'console=ttyAMA0 rdinit=/test-init a9.deadman=3600 a9.udcwait=0 printk.devkmsg=on a9sim=$sc' \
		-netdev user,id=n0,net=172.16.42.0/24,host=172.16.42.2,hostfwd=tcp:127.0.0.1:$P-172.16.42.1:2323,hostfwd=tcp:127.0.0.1:$((P + 1))-172.16.42.1:2324 \
		-device virtio-net-device,netdev=n0" > "$T/console.$sc" 2>&1 &
	QP=$!
	local i=0
	until python3 -I "$REPO/scripts/mainline/a9-capture-host.py" net 127.0.0.1 $P ping --timeout 3 2>/dev/null | grep -q 'a9-ctl ok'; do
		i=$((i + 1)); [ $i -gt 120 ] && { echo "  FAIL no a9-ctl in QEMU"; tail -20 "$T/console.$sc"; fail=$((fail + 1)); qkill; return; }
		sleep 2
	done
	mkdir -p "$T/cap.$sc"
	A9_SIM_SLOW=20 A9_CTL_ADDR=127.0.0.1 A9_CTL_PORT=$P A9_SIM_CAP="$T/cap.$sc" "$REPO/scripts/mainline/first-boot.sh" --sim $fl > "$T/host.$sc" 2>&1
	for e in "$@"; do
		if grep -qE -- "$e" "$T/host.$sc"; then echo "  ok   $e"; pass=$((pass + 1)); else echo "  FAIL $e"; fail=$((fail + 1)); fi
	done
	[ -n "${KEEP:-}" ] && cp -r "$T/host.$sc" "$T/cap.$sc" "$T/console.$sc" "$KEEP/" 2>/dev/null
	qkill; wait $QP 2>/dev/null; QP=
}
run pg-good --e5 'RESULT pre PASS' 'RESULT e3 PASS' 'RESULT e5a DRY-OK' 'ok   frames \+ sha256 agree' 'RESULT verdict PASS' \
	'RESULT e5b PASS' 'RESULT e5c PASS' 'final: .*safe: verified PWRUP=0 VCOM_EN=0' 'SIM: eink_run rc 0'
run pg-inverted --e3 'RESULT pre PASS' 'PG_VERDICT (raw-low-when-on|undetermined)' 'RESULT e3 STOP-A' 'final: .*verified PWRUP=0 VCOM_EN=0'
run pg-good,dsi-error --e5ab 'RESULT e3 FAIL' 'ABORTED: .*frame done timeout' 'SIM: eink_run rc 1'
echo "qemu e2e: $pass passed, $fail failed"
[ $fail = 0 ]
