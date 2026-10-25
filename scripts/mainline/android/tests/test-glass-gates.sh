#!/usr/bin/env bash
set -euo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
eval "$(sed '/^R=$(mktemp/,$d' "$HERE/tests/test-eink-gates.sh")"
R=$(mktemp -d); trap 'rm -rf "$R"' EXIT
mk "$R" Y; M=$R/sys/module
for f in fp9929a/allow_power_on panel_hisense_hlte556n_eink/enable; do echo Y > "$M/${f%/*}/parameters/${f#*/}"; done
echo N > "$M/a9_eink/parameters/log_updates"
echo vendor.a9ml.eink.gate=refused >> "$R/props"
printf '#!/bin/sh\necho Enforcing\n' > "$R/stubs/getenforce"
cat > "$R/stubs/input" <<S
#!/bin/sh
touch "$R/redraw"
sed -i 's/updates 2/updates 3/' "$E/status"
S
cat > "$R/stubs/dmesg" <<S
#!/bin/sh
if [ -f "$R/redraw" ]; then echo '[ 2.0] a9-eink eink-display: update done: real'; fi
S
chmod +x "$R/stubs/getenforce" "$R/stubs/input" "$R/stubs/dmesg"
PATH="$R/stubs:$PATH" A9ML_SYS="$R/sys" sh "$HERE/sepolicy/glass-proof.sh" > "$R/proof.log"
grep -q 'GLASS DRIVER PASS' "$R/proof.log"
echo 'PASS stale gate property with open module gates and real driver deltas'
echo N > "$M/fp9929a/parameters/allow_power_on"
if PATH="$R/stubs:$PATH" A9ML_SYS="$R/sys" sh "$HERE/sepolicy/glass-proof.sh" > "$R/proof.log"; then exit 1; fi
grep -q 'closed gate fp9929a/allow_power_on' "$R/proof.log"
echo 'PASS closed module gate still refuses'
