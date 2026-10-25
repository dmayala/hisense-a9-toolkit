#!/usr/bin/env bash
#
# epd-snap.sh — decoded software gray targets from the E Ink composer.
#
# `screencap` reads SurfaceFlinger's composition; this reads the composer's
# transition buffer. A decoded target matched one panel photo, but these bytes
# do not measure physical pigment state, optical ghosting or update completion
# (docs/epd-panel-screenshot.md records the validation and its limits).
#
# How: Hisense's userspace E Ink engine (EinkSwTconInit; logs
# as "SDM : epd::") runs inside vendor.qti.hardware.display.composer-service and
# keeps software transition targets in a heap buffer: 824 rows x 1648 (panel-native,
# rotated), 16 bits per pixel, low byte = (old grey << 4) | new grey, data
# end-aligned in a 2,719,744-byte allocation. We read it as root from
# /proc/<pid>/mem. Composer memory is read-only; it is not stopped or traced.
#
# Output:
#   OUT.png       new software gray target (new nibble, scaled by 17)
#   OUT-prev.png  old software gray target (old nibble, scaled by 17)
# These are target codes, not measured optical gray levels on the glass.
#
# Finding the buffer: seven allocations have that size and only one is live. With
# no cached address (or --discover) the script wakes the screen, pulls the
# notification shade down and back up, and keeps the one buffer that changed. The
# address is cached per boot + composer pid, so discovery runs once per boot.
# DISCOVERY CHANGES THE SCREEN: run it while the display is known to work, not
# while a freeze you want to observe is on screen.
#
# Usage:
#   ./epd-snap.sh OUT.png               # snapshot (discovers on first use)
#   ./epd-snap.sh OUT.png --discover    # force rediscovery
#
# Needs: adb, root (su) on the phone, python3 on the host.
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"

OUT=${1:?usage: epd-snap.sh OUT.png [--discover]}; MODE=${2:-}
SIZE=2719744; PAGES=$((SIZE / 4096)); R=824; C=1648
CACHE=${XDG_CACHE_HOME:-$HOME/.cache}/epd-snap; mkdir -p "$CACHE"

su_sh() { "$ADB" shell su -c "'$1'" | tr -d '\r'; }

P=$(su_sh "pidof vendor.qti.hardware.display.composer-service")
[ -n "$P" ] || { echo "composer not running (is Android up? a9-health.sh)" >&2; exit 1; }
BOOT=$("$ADB" shell cat /proc/sys/kernel/random/boot_id | tr -d '\r')
KEY="$CACHE/$BOOT-$P"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT

# Candidate buffers: anonymous mappings of exactly $SIZE bytes in the composer.
# Addresses are computed here, on the host: the phone's sh does 32-bit $(( )).
candidates() {
  su_sh "cat /proc/$P/maps" | awk -v s=$SIZE '
    { split($1, a, "-"); if (strtonum("0x" a[2]) - strtonum("0x" a[1]) == s && $0 ~ /anon/) print a[1] }'
}

dump() {  # dump TAG ADDR... -> $T/TAG/ADDR.bin
  local tag=$1; shift; local cmd="mkdir -p /data/local/tmp/epdsnap"
  for a in "$@"; do
    cmd+="; dd if=/proc/$P/mem of=/data/local/tmp/epdsnap/$a.bin bs=4096 skip=$((0x$a / 4096)) count=$PAGES 2>/dev/null"
  done
  su_sh "$cmd; chmod 644 /data/local/tmp/epdsnap/*" >/dev/null
  mkdir -p "$T/$tag"
  for a in "$@"; do "$ADB" pull "/data/local/tmp/epdsnap/$a.bin" "$T/$tag/" >/dev/null 2>&1; done
  su_sh "rm -rf /data/local/tmp/epdsnap" >/dev/null
}

mapfile -t A < <(candidates)
[ ${#A[@]} -gt 0 ] || { echo "no ${SIZE}-byte heap buffers in composer pid $P" >&2; exit 1; }

ADDR=$(cat "$KEY" 2>/dev/null || true)
case " ${A[*]} " in *" $ADDR "*) ;; *) ADDR="" ;; esac   # cached address must still be mapped
if [ -z "$ADDR" ] || [ "$MODE" = --discover ]; then
  echo "discovering the live buffer (pulls the notification shade down and up)..." >&2
  "$ADB" shell input keyevent KEYCODE_WAKEUP; sleep 2
  dump before "${A[@]}"
  "$ADB" shell cmd statusbar expand-notifications; sleep 5
  dump after "${A[@]}"
  "$ADB" shell cmd statusbar collapse
  CH=(); for a in "${A[@]}"; do cmp -s "$T/before/$a.bin" "$T/after/$a.bin" || CH+=("$a"); done
  if [ ${#CH[@]} -ne 1 ]; then
    echo "discovery ambiguous: ${#CH[@]} buffers changed (${CH[*]:-none}). Is the screen on and unlocked?" >&2
    exit 1
  fi
  ADDR=${CH[0]}; echo "$ADDR" > "$KEY"
  echo "live buffer 0x$ADDR (composer pid $P), cached" >&2
  sleep 5   # let the collapse land on the panel
fi

UP=$("$ADB" shell cat /proc/uptime | awk '{print $1}')
dump now "$ADDR"
python3 -I - "$T/now/$ADDR.bin" "$OUT" $R $C <<'PY'
import sys, zlib, struct
src, dst, R, C = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
d = open(src, 'rb').read()
b = d[len(d) - 2 * R * C:][0::2]                    # low byte of each 16-bit pixel
chunk = lambda t, x: struct.pack('>I', len(x)) + t + x + struct.pack('>I', zlib.crc32(t + x) & 0xffffffff)

def write(path, lut):
    p = b.translate(bytes(lut)); out = bytearray(R * C)
    for r in range(R):                              # panel-native -> upright portrait
        out[R - 1 - r::R] = p[r * C:(r + 1) * C]
    rows = b''.join(b'\0' + bytes(out[y * R:(y + 1) * R]) for y in range(C))
    open(path, 'wb').write(b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', struct.pack('>IIBBBBB', R, C, 8, 0, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress(rows, 6)) + chunk(b'IEND', b''))
    return len(set(p))

prev = (dst[:-4] if dst.endswith('.png') else dst) + '-prev.png'
n = write(dst, [(v & 15) * 17 for v in range(256)])   # new grey level
write(prev, [(v >> 4) * 17 for v in range(256)])      # old grey level
print(f"{dst}: panel now ({R}x{C}, {n} grey levels)" + ("  WARNING: flat image" if n < 2 else ""))
print(f"{prev}: image before the last transition")
PY
echo "kernel uptime $UP, composer pid $P, buffer 0x$ADDR"
