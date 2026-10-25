# Staging probe (review 63c): proves the loopback root shell can do the three
# writes the D0 target needs BEFORE the one-attempt ledger exists. Not part of
# the frozen bundle; it touches no ledger, remoteproc or module. Passed to the
# checked root runner as one script string. PROBE_ROOT is for offline tests only.
set -eu
P=${PROBE_ROOT:-}
d=$P/data/local/tmp/adsp-d0-probe.$$
mkdir "$d"
trap 'rm -rf "$d"' EXIT
printf '<6>d0-root-probe %s\n' "$$" > "$P/dev/kmsg"
setsid sh -c 'echo guardian-ok > "$1/g"' sh "$d" </dev/null >/dev/null 2>&1
n=0
while [ ! -s "$d/g" ]; do
    n=$((n+1)); [ "$n" -lt 50 ] || { echo 'probe: setsid writer did not run'; exit 3; }
    sleep 0.1 2>/dev/null || sleep 1
done
[ "$(cat "$d/g")" = guardian-ok ]
echo 'D0-ROOT-PROBE PASS'
