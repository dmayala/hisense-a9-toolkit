#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
# Offline only. Never loads a module, accesses USB, or builds a boot image.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../../.." && pwd)
MAINLINE=${A9_MAINLINE:-$HOME/A9-build/mainline}
SRC=$MAINLINE/linux-7.2.10
SHARED=$MAINLINE/build-7.2.10
WORK=$MAINLINE/sol-pmic
OBJ=$WORK/build-7.2.10
SHELL_NIX=$REPO/scripts/mainline/kernel/shell.nix
[ -f "$SHARED/Module.symvers" ] || {
    echo "Need the existing 7.2.10 build's Module.symvers (read only)." >&2
    exit 1
}
mkdir -p "$OBJ" "$WORK/module" "$WORK/adc"
cp "$SHARED/.config" "$OBJ/.config"
# Coordinator now builds observe in. Keep our control artifact independent;
# these symbols are disabled ONLY in this private O= configuration.
"$SRC/scripts/config" --file "$OBJ/.config" \
    --disable CHARGER_PMI632_A9_TELEMETRY_ONLY \
    --disable CHARGER_PMI632_A9_OBSERVE --disable CHARGER_PMI632_A9_FV_STOCK
cp "$HERE/Makefile" "$HERE/pmi632-a9.c" "$HERE/pmi632-policy.h" "$HERE/pmi632-fv.h" "$WORK/module/"
# Prepare private O= headers. No shared .config or build output is changed.
nix-shell "$SHELL_NIX" --run "make -s -C '$SRC' O='$OBJ' ARCH=arm64 CROSS_COMPILE=aarch64-unknown-linux-gnu- olddefconfig modules_prepare"
cp "$SHARED/Module.symvers" "$OBJ/Module.symvers"
python3 -I - "$SRC" "$HERE" "$WORK" <<'PY'
import pathlib, subprocess, sys
src, here, work = map(pathlib.Path, sys.argv[1:])
# Apply only inside an isolated directory containing the single source file.
rel = pathlib.Path('drivers/iio/adc/qcom-spmi-adc5.c')
dst = work / 'adc-copy' / rel
dst.parent.mkdir(parents=True, exist_ok=True)
dst.write_bytes((src / rel).read_bytes())
patch = here / 'patches/0001-iio-adc5-battery-thermistor-voltage-PROPOSAL.patch'
applied = subprocess.run(['git', '-C', str(src), 'apply', '--check', '-R', str(patch)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
if not applied:
    subprocess.run(['patch', '-s', '-p1', '-d', str(work / 'adc-copy'), '-i', str(patch)], check=True)
(work / 'adc/qcom-spmi-adc5.c').write_bytes(dst.read_bytes())
(work / 'adc/Makefile').write_text('obj-m += qcom-spmi-adc5.o\n')
PY
nix-shell "$SHELL_NIX" --run "make -C '$SRC' O='$OBJ' ARCH=arm64 CROSS_COMPILE=aarch64-unknown-linux-gnu- M='$WORK/module' W=1 modules"
nix-shell "$SHELL_NIX" --run "make -C '$SRC' O='$OBJ' ARCH=arm64 CROSS_COMPILE=aarch64-unknown-linux-gnu- M='$WORK/adc' W=1 modules"
mkdir -p "$WORK/module-telemetry"
cp "$HERE/Makefile" "$HERE/pmi632-a9.c" "$HERE/pmi632-policy.h" "$HERE/pmi632-fv.h" "$WORK/module-telemetry/"
# Compile the observe variant too, without changing the shared kernel config.
nix-shell "$SHELL_NIX" --run "make -C '$SRC' O='$OBJ' ARCH=arm64 CROSS_COMPILE=aarch64-unknown-linux-gnu- M='$WORK/module-telemetry' KCFLAGS='-DCONFIG_CHARGER_PMI632_A9_TELEMETRY_ONLY=1 -DCONFIG_CHARGER_PMI632_A9_OBSERVE=1 -DCONFIG_CHARGER_PMI632_A9_FV_STOCK=1' W=1 modules"
nix-shell "$SHELL_NIX" --run "aarch64-unknown-linux-gnu-nm -u '$WORK/module-telemetry/pmi632-a9.o'" > "$WORK/observe-imports.txt"
if rg -q 'regmap_(raw_write|bulk_write|update_bits)' "$WORK/observe-imports.txt"; then
    echo "observe object references a forbidden register mutation API" >&2
    exit 1
fi
nix-shell "$SHELL_NIX" --run "aarch64-unknown-linux-gnu-objdump -dr '$WORK/module-telemetry/pmi632-a9.o'" > "$WORK/observe-disassembly.txt"
python3 -I - "$WORK/observe-disassembly.txt" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
assert len(re.findall(r'R_AARCH64_(?:CALL26|JUMP26)\s+regmap_write\b', text)) == 1
print('PASS: observe has exactly one regmap_write call site, no bulk-write/RMW imports')
PY
nix-shell "$SHELL_NIX" --run "cc -std=c11 -Wall -Wextra -Werror -fsanitize=undefined,address -g '$HERE/tests/policy.c' -o '$WORK/policy-test' && '$WORK/policy-test'"
nix-shell "$SHELL_NIX" --run "cc -std=c11 -Wall -Wextra -Werror -fsanitize=undefined,address -g '$HERE/tests/fv.c' -o '$WORK/fv-test' && '$WORK/fv-test'"
echo "Offline modules: $WORK/module/pmi632-a9.ko, $WORK/adc/qcom-spmi-adc5.ko"
