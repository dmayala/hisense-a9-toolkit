#!/usr/bin/env bash
# stage-firmware.sh -- stage the Adreno 610 firmware for the A9 mainline GPU
# vendor (build-vendor.sh --gpu). Offline; reads only local copies; output is
# LOCAL and NEVER committed (Qualcomm firmware). runs/gpu.md.
#
#   scripts/mainline/android/gpu/stage-firmware.sh [golden_vendor_img]
#
# Sources:
#   zap: the stock vendor partition, /firmware/a610_zap.{mdt,b00,b01,b02,elf},
#        read with debugfs from the verified local lpunpack of the golden dump
#        (~/A9-build/mainline/android/golden/vendor_b.img, extract-golden-
#        vendor.sh; never from ~/a9-assets directly). TZ authenticates it (PAS 13).
#   SQE: linux-firmware 20250917 qcom/a630_sqe.fw (version 0x207; the stock
#        vendor's copy is 0x190, kept as a630_sqe.fw.stock for A/B tests).
# Output tree ($GW/fw/stage, mirrored to /vendor/firmware/a9gpu and copied at
# boot into firmware_class.path):
#   qcom/a630_sqe.fw
#   qcom/sm6115/hisense/hlte556n/a610_zap.mbn   (= stock a610_zap.elf, squashed)
#   qcom/sm6115/hisense/hlte556n/a610_zap.{mdt,b00,b01,b02}  (split, for reference)
set -euo pipefail
command -v zstd >/dev/null || exec nix-shell -p zstd --run "$(printf '%q ' "$0" "$@")"
GW=${A9_GPU_WORK:-$HOME/A9-build/mainline/gpu}
G=${1:-$HOME/A9-build/mainline/android/golden/vendor_b.img}
LF=${A9_LINUX_FIRMWARE:-/nix/store/7nm4vfj0j5q7w2pn46alb8d0ajr1h1xp-linux-firmware-20250917-zstd/lib/firmware}
case "$GW" in *a9-assets*) echo "refusing to write under a9-assets" >&2; exit 2 ;; esac
[ -f "$G" ] || { echo "missing $G (scripts/mainline/android/extract-golden-vendor.sh)" >&2; exit 1; }
S=$GW/fw/stage; T=$GW/fw/tmp
rm -rf "$S" "$T"; mkdir -p "$S/qcom/sm6115/hisense/hlte556n" "$T"
for f in a610_zap.mdt a610_zap.b00 a610_zap.b01 a610_zap.b02 a610_zap.elf a630_sqe.fw; do
  debugfs -R "dump /firmware/$f $T/$f" "$G" >/dev/null 2>&1
  [ -s "$T/$f" ] || { echo "cannot read /firmware/$f from $G" >&2; exit 1; }
done
# MEASURED 2026-10-15 (golden vendor_b, sha256 2a7d5d86...)
(cd "$T" && sha256sum -c --quiet) <<'EOF'
deec821d90753f647ba5309f511b23c9a17bd5a5eeb3515031f37d1a168196ab  a610_zap.mdt
422ed7993d7cd924ba3200b1acd18c120b0a40f4357b55362eddcbe4a0c8e019  a610_zap.b00
50648cfaceec3fcd7aa4ae9468aaec1c3c846da5c0356ac25696620656305665  a610_zap.b01
84c657996f3ec60a90d0c7223faad9b0700074fe00dc55596e0d3ba69b914d33  a610_zap.b02
73303ddb5bdc60a503732aa0dda57f95dce021aa326e580e2d245bede761c543  a610_zap.elf
a0e1b583f620fabe32729ce367959d1960638663244d7d0cfc21b9a5215a018b  a630_sqe.fw
EOF
# the squashed .elf must be the split image (ELF header + hash + load segment)
python3 -I - "$T" <<'PY'
import struct, sys, os
d = sys.argv[1]
elf = open(os.path.join(d, 'a610_zap.elf'), 'rb').read()
mdt = open(os.path.join(d, 'a610_zap.mdt'), 'rb').read()
phoff = struct.unpack_from('<I', elf, 0x1c)[0]; n = struct.unpack_from('<H', elf, 0x2c)[0]
assert elf[:4] == b'\x7fELF' and elf[4] == 1, 'not ELF32'
for i in range(n):
    t, off, va, pa, fs, ms, fl, al = struct.unpack_from('<8I', elf, phoff + 32 * i)
    if fs == 0: continue
    seg = open(os.path.join(d, 'a610_zap.b%02d' % i), 'rb').read()
    assert elf[off:off + fs] == seg[:fs], f'segment {i} differs from b{i:02d}'
    if t == 1: print(f'  zap load segment {i}: paddr {pa:#x} memsz {ms:#x} relocatable {bool(fl >> 27 & 1)}')
assert elf[:0x34] == mdt[:0x34], 'ELF header differs from .mdt'
PY
zstd -dqf "$LF/qcom/a630_sqe.fw.zst" -o "$S/qcom/a630_sqe.fw"
echo "1c21b527d9183487cc550dabbb3f43e555df5a977a461934fc61f0635a9aa90c  $S/qcom/a630_sqe.fw" | sha256sum -c --quiet
cp "$T/a630_sqe.fw" "$S/qcom/a630_sqe.fw.stock"
Z=$S/qcom/sm6115/hisense/hlte556n
cp "$T/a610_zap.elf" "$Z/a610_zap.mbn"
for f in mdt b00 b01 b02; do cp "$T/a610_zap.$f" "$Z/"; done
rm -rf "$T"
(cd "$S" && find . -type f | LC_ALL=C sort | xargs sha256sum > "$GW/fw/stage.SHA256SUMS")
echo "staged GPU firmware (LOCAL ONLY, never commit): $S"; cat "$GW/fw/stage.SHA256SUMS"
