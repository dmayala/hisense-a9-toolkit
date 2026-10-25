#!/usr/bin/env bash
# pack-boot.sh -- pack a boot image (header v2) for boot_X from a mainline
# kernel, its DTB and an (Android first-stage) ramdisk, then check it with the
# phone's own ABL code (scripts/abl-check.sh, offline emulator). No phone access.
#
#   pack-boot.sh -r <ramdisk> [-c <cmdline_file>] [options]
#       -r <ramdisk>       cpio, cpio.gz or cpio.lz4 (legacy -l lz4); used as is
#       -c <cmdline_file>  kernel command line (newlines -> spaces; '#' comments
#                          dropped). Default: the mainline build's CMDLINE
#                          (scripts/mainline/kernel/build.sh, incl. A9_CMDLINE).
#       -k <Image.gz>      default ~/A9-build/mainline/out/Image.gz
#       -d <dtb>           default ~/A9-build/mainline/out/sm6115-hisense-hlte556n.dtb
#       -D <dtbo.img>      recovery_dtbo section AND the dtbo_X the ABL check uses
#                          (default ~/A9-build/mainline/out/recovery_dtbo.img, the
#                          null overlay; flash the same file to dtbo_X)
#       -o <boot.img>      default ~/A9-build/mainline/android/out/boot.img
#       --os-version V     default 11.0.0 (stock boot/TEE identity)
#       --os-patch-level P default 2022-04 (stock boot/TEE identity).
#                          ABL sends this header to Keymaster before Android;
#                          keep it aligned with the shipped boot firmware.
#                          System/vendor properties retain their actual levels.
#       --wifi             opt-in draft; defaults to kernel-snap-wifi inputs,
#                          requires their checked manifest, output boot-wifi.img
#                          (A9_WIFI=1 is equivalent; no modem startup flag).
#
# Geometry = scripts/mainline/kernel/build.sh `image` step (= stock/TWRP):
# header v2, page 4096, base 0, kernel 0x8000, ramdisk 0x1000000, tags 0x100,
# second 0, dtb in the v2 DTB section at 0x1f00000 (not appended to the kernel;
# stock has no appended DTB), --recovery_dtbo carried too (build.sh does, and
# the ABL's boot route passes with it). Size limit: boot_a/_b = 100663296 bytes
# (data/golden.tsv).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
M=${A9_MAINLINE:-$HOME/A9-build/mainline}
KERNEL=$M/out/Image.gz DTB=$M/out/sm6115-hisense-hlte556n.dtb DTBO=$M/out/recovery_dtbo.img
OUTIMG=$M/android/out/boot.img RD= CMDF= OSV=11.0.0 OSPL=2022-04
WIFI=${A9_WIFI:-0} KSET=0 DSET=0 TSET=0 OSET=0
LIMIT=100663296
die() { echo "pack-boot: $*" >&2; exit 2; }
while [ $# -gt 0 ]; do
	case $1 in
		-r) RD=$2; shift 2;; -c) CMDF=$2; shift 2;; -k) KERNEL=$2; KSET=1; shift 2;;
		-d) DTB=$2; DSET=1; shift 2;; -D) DTBO=$2; TSET=1; shift 2;; -o) OUTIMG=$2; OSET=1; shift 2;;
		--wifi) WIFI=1; shift;;
		--os-version) OSV=$2; shift 2;; --os-patch-level) OSPL=$2; shift 2;;
		-h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0;;
		*) die "unknown argument $1 (-h for help)";;
	esac
done
case "$WIFI" in 0|1) ;; *) die "A9_WIFI must be 0 or 1" ;; esac
[ "$WIFI" = 0 ] || umask 077 # private MAC is embedded in the Wi-Fi DTB
if [ "$WIFI" = 1 ]; then
	SNAP=${A9_WIFI_SNAPSHOT:-$M/android/kernel-snap-wifi}
	[ "$KSET" = 1 ] || KERNEL=$SNAP/Image.gz
	[ "$DSET" = 1 ] || DTB=$SNAP/sm6115-hisense-hlte556n-android.dtb
	[ "$TSET" = 1 ] || DTBO=$SNAP/recovery_dtbo.img
	[ "$OSET" = 1 ] || OUTIMG=$M/android/out/boot-wifi.img
	[[ "$OUTIMG" = *-wifi.img ]] || die "Wi-Fi boot output must end in -wifi.img"
	[ -s "$SNAP/wifi-manifest.json" ] || die "Wi-Fi snapshot manifest missing"
	(cd "$SNAP" && sha256sum -c --quiet SHA256SUMS) || die "Wi-Fi snapshot hashes failed"
	python3 -I - "$SNAP" "$KERNEL" "$DTB" "${A9_WIFI_PD_MAPPER:-kernel}" <<'PY'
import hashlib, json, pathlib, sys
p = pathlib.Path(sys.argv[1]); manifest = json.loads((p / 'wifi-manifest.json').read_text())
assert manifest['version'] == 1 and manifest['pd_mapper'] == sys.argv[4], 'Wi-Fi PD mapper mode mismatch'
for file, key in [(sys.argv[2], 'Image.gz'), (sys.argv[3], 'sm6115-hisense-hlte556n-android.dtb')]:
    assert hashlib.sha256(pathlib.Path(file).read_bytes()).hexdigest() == manifest['sha256'][key], key
PY
fi
[ -n "$RD" ] || die "-r <ramdisk> is required (-h for help)"
for f in "$KERNEL" "$DTB" "$DTBO" "$RD"; do [ -s "$f" ] || die "missing or empty: $f"; done
command -v mkbootimg >/dev/null || die "mkbootimg missing"
p=$(realpath -m "$OUTIMG"); case "$p" in "$(realpath -m "$HOME/a9-assets")"*|/share/*|*a9-assets*) die "refusing to write under ~/a9-assets ($p)";; esac
mkdir -p "$(dirname "$OUTIMG")"

# command line
if [ -n "$CMDF" ]; then
	[ -f "$CMDF" ] || die "cmdline file not found: $CMDF"
	CMDLINE=$(sed 's/#.*//' "$CMDF" | tr '\n\t' '  ' | tr -s ' ' | sed 's/^ //; s/ $//')
else
	CMDLINE=$(A9_CMDLINE=${A9_CMDLINE:-} A9_CMDLINE_EXTRA=${A9_CMDLINE_EXTRA:-} bash -c \
		"$(grep -m1 '^CMDLINE=' "$REPO/scripts/mainline/kernel/build.sh")"'; printf %s "$CMDLINE"')
fi
[ -n "$CMDLINE" ] || die "empty command line"
if [ "$WIFI" = 1 ]; then
    # ABL uses only the first 512-byte field. Both radio and E Ink share the
    # fixed RAM lookup root; the module prep bridges immutable vendor radio
    # trees there without hiding the runtime display firmware directory.
    python3 -I - "$CMDLINE" <<'PYWIFI' || die "Wi-Fi requires one effective firmware_class.path=/mnt/a9-wifi/firmware and <=511-byte cmdline"
import sys
s=sys.argv[1]; args=s.split()
assert len(s.encode())<=511
assert [a for a in args if a.startswith('firmware_class.path=')]==['firmware_class.path=/mnt/a9-wifi/firmware']
PYWIFI
fi

[ ${#CMDLINE} -le 1535 ] || die "command line ${#CMDLINE} chars > 1535 (header v2 cmdline+extra_cmdline)"

# ramdisk format (informational; the kernel needs the matching CONFIG_RD_*)
RDFMT=$(python3 -I - "$RD" <<'PY'
import sys
b = open(sys.argv[1], 'rb').read(6)
print('gzip' if b[:2] == b'\x1f\x8b' else 'lz4-legacy' if b[:4] == b'\x02\x21\x4c\x18'
      else 'lz4-frame' if b[:4] == b'\x04\x22\x4d\x18' else 'cpio-newc' if b[:6] in (b'070701', b'070702')
      else 'unknown')
PY
)
[ "$RDFMT" != unknown ] || die "ramdisk is not cpio/gzip/lz4: $RD"

TMP=$OUTIMG.tmp.$$; trap 'rm -f "$TMP"' EXIT
mkbootimg --header_version 2 --pagesize 4096 --base 0 --kernel_offset 0x8000 \
	--ramdisk_offset 0x1000000 --tags_offset 0x100 --second_offset 0 \
	--dtb_offset 0x1f00000 --os_version "$OSV" --os_patch_level "$OSPL" \
	--kernel "$KERNEL" --ramdisk "$RD" \
	--dtb "$DTB" --recovery_dtbo "$DTBO" \
	--cmdline "$CMDLINE" -o "$TMP"

rc=0
python3 -I - "$TMP" "$KERNEL" "$RD" "$DTB" "$DTBO" "$LIMIT" <<'PY' || rc=1
import struct, sys, os
img, k, rd, dtb, dtbo, limit = sys.argv[1:7]
h = open(img, 'rb').read(4096)
u = lambda o: struct.unpack_from('<I', h, o)[0]
ok = lambda c, m: print(("  ok   " if c else "  FAIL ") + m) or c
r = [ok(h[:8] == b'ANDROID!', 'magic ANDROID!'), ok(u(40) == 2, 'header version 2'),
     ok(u(36) == 4096, 'page size 4096'), ok(u(12) == 0x8000, 'kernel addr 0x8000'),
     ok(u(20) == 0x1000000, 'ramdisk addr 0x1000000'), ok(u(32) == 0x100, 'tags addr 0x100'),
     ok(u(8) == os.path.getsize(k), f'kernel {u(8)} bytes'),
     ok(u(16) == os.path.getsize(rd), f'ramdisk {u(16)} bytes'),
     ok(u(1632) == os.path.getsize(dtbo), f'recovery_dtbo {u(1632)} bytes'),
     ok(u(1648) == os.path.getsize(dtb), f'dtb {u(1648)} bytes at 0x{struct.unpack_from("<Q", h, 1652)[0]:x}'),
     ok(os.path.getsize(img) <= int(limit), f'size {os.path.getsize(img)} <= {limit} (boot_a/_b)')]
sys.exit(0 if all(r) else 1)
PY
case " $CMDLINE " in *" qcom_pon.beacon=2 "*) ;; *) echo "  WARN cmdline lacks qcom_pon.beacon=2 (first-boot fail-safe: resets land in fastboot)";; esac
echo "  ramdisk: $RDFMT, $(stat -c %s "$RD") bytes"
"$REPO/scripts/abl-check.sh" "$TMP" boot --dtbo "$DTBO" --quiet || { echo "  FAIL abl-check boot"; rc=1; }
[ $rc = 0 ] || die "checks failed; nothing written to $OUTIMG"
mv -f "$TMP" "$OUTIMG"
(cd "$(dirname "$OUTIMG")" && sha256sum "$(basename "$OUTIMG")" > "$(basename "$OUTIMG").sha256")
echo "pack-boot: produced"
echo "  $OUTIMG  $(stat -c %s "$OUTIMG") bytes  sha256 $(cut -c1-16 "$OUTIMG.sha256")"
echo "  cmdline: $CMDLINE"
echo "  flash with dtbo_X = $DTBO"
