#!/usr/bin/env bash
# Offline test of host/post-store-check.sh against a fake adb on PATH. Never touches a phone.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd); S=$HERE/../../scripts/mainline/wifi/host/post-store-check.sh
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT; mkdir -p "$T/bin"
cat > "$T/bin/adb" <<'FAKE'
#!/usr/bin/env bash
[ "${1:-}" = -s ] && shift 2
case "$1" in
  wait-for-device) exit 0;;
  shell) case "$2 ${3:-}" in
      "getprop sys.boot_completed") echo 1;;
      *) h=${FAKE_HASH:-aa}; printf 'POST modemst1 %s -\nPOST modemst2 %s -\nPOST fsg %s -\nPOST fsc %s -\n' $h $h $h $h;;
    esac;;
esac
FAKE
chmod +x "$T/bin/adb"; export PATH="$T/bin:$PATH"; export ADB=adb
fail=0; ok() { echo "  PASS  $1"; }; no() { echo "  FAIL  $1"; fail=1; }
printf 'modemst1 aa\nmodemst2 aa\nfsg aa\nfsc aa\n' > "$T/pre.cmp"
FAKE_HASH=aa "$S" "$T/pre.cmp" "$T/o1" > /dev/null && grep -q 'STORES SAME' "$T/o1/verdict.txt" && ok "same hashes -> SAME" || no "same"
FAKE_HASH=bb "$S" "$T/pre.cmp" "$T/o2" > /dev/null; [ $? = 1 ] && grep -q DIFFER "$T/o2/verdict.txt" && ok "different -> DIFFER, exit 1" || no "differ"
: > "$T/empty"; "$S" "$T/empty" "$T/o3" 2>/dev/null; [ $? = 2 ] && ok "missing PRE refused" || no "empty pre"
[ "$(stat -c %a "$T/o1")" = 700 ] && ok "private output dir" || no "mode"
grep -q 'wait-for-device\|sha256sum < /dev/block/by-name' "$S" && ok "read-only payload only" || no "payload"
! grep -nE 'dd of=|> */dev/block|fastboot|flash|erase' "$S" > /dev/null && ok "no write verbs" || no "write verb present"
exit $fail
