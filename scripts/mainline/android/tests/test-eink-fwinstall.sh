#!/usr/bin/env bash
# test-eink-fwinstall.sh -- offline tests for eink-fw-install.sh (G2a pushfix,
# runs/gpu-g2a-pushfix.md). adb is a FAKE that maps /data into a temp root:
# as "shell" it may only write outside a9fw (the GPU vendor's root-owned
# a9fw, the 2026-10-16 G2a failure), the "root" channels run the command
# locally with chown/stat stubbed. Nothing touches USB.
set -uo pipefail
HERE=$(cd "$(dirname "$0")/.." && pwd)
INST=$HERE/eink-fw-install.sh
fails=0
ok() { echo "  ok   $1"; }
bad() { echo "  FAIL $1"; fails=$((fails+1)); }
setup() {   # setup <root mode: nc|adbd|none> [fault]
  T=$(mktemp -d); R=$T/r; mkdir -p "$R/data/local/tmp/a9fw/qcom" "$T/bin"
  echo fw > "$R/data/local/tmp/a9fw/qcom/a610_zap.mbn"   # created by a9ml-gpu.sh setup (root)
  printf 'A9WF-test-waveform' > "$T/wf.bin"; printf 'A9CT-test-template' > "$T/ct.bin"
  # stubs the "root" shell sees: chown records, stat reports root ownership
  printf '#!/bin/sh\necho "chown $*" >> "%s/chown.log"\n' "$T" > "$T/bin/chown"
  printf '#!/bin/sh\n# stat -c FMT path\np=$3; m=$(%s -c %%a "$p") || exit 1; [ -f "%s/badmode" ] && m=777; echo "root:root:$m"\n' "$(command -v stat)" "$T" > "$T/bin/stat"
  cat > "$T/adb" <<S
#!/usr/bin/env bash
echo "adb \$*" >> "$T/calls"
R="$R"; mode="$1"; fault="${2:-}"
map() { sed "s|/data/|\$R/data/|g"; }
rootrun() { local c; c=\$(printf '%s' "\$1" | map)
  [ "\$fault" = corrupt ] && c=\$(printf '%s' "\$c" | sed 's|mv -f \([^ ]*\) |printf X >> \1; mv -f \1 |')
  PATH="$T/bin:\$PATH" sh -c "\$c" 2>&1 | sed "s|\$R/data/|/data/|g"; }
[ "\$1" = shell ] || [ "\$1" = push ] || exit 0
if [ "\$1" = push ]; then
  dst=\$(printf '%s' "\$3" | map)
  case "\$3" in /data/local/tmp/a9fw/*) echo "adb: error: stat failed when trying to push to \$3: Permission denied" >&2; exit 1 ;; esac
  [ "\$fault" = pushfail ] && exit 1
  cp "\$2" "\$dst" || exit 1; exit 0
fi
shift; c="\$*"
case "\$c" in
  "id -u") [ "\$mode" = adbd ] && echo 0 || echo 2000 ;;
  "echo id -u | nc -w 3 127.0.0.1 2324") [ "\$mode" = nc ] && echo 0 ;;
  "echo '"*"' | nc -w 30 127.0.0.1 2324") [ "\$mode" = nc ] || exit 1; c=\${c#echo \'}; c=\${c%\' | nc -w 30 127.0.0.1 2324}; rootrun "\$c" ;;
  mkdir*|*a9fw*) if [ "\$mode" = adbd ]; then rootrun "\$c"; else
                   # as shell: nothing under a9fw (root-owned), staging is fine
                   case "\$c" in *a9fw*) echo "mkdir: Permission denied"; exit 1 ;; esac; sh -c "\$(printf '%s' "\$c" | map)"; fi ;;
  *) sh -c "\$(printf '%s' "\$c" | map)" ;;
esac
S
  chmod +x "$T/adb" "$T/bin/chown" "$T/bin/stat"
}
run() { ADB="$T/adb" A9M_ROOT_WAIT=${RW:-2} "$INST" "$T/wf.bin" ${CTARG-"$T/ct.bin"} > "$T/out" 2>&1; }
H() { echo "$R/data/local/tmp/a9fw/hisense/$1"; }
done_() { rm -rf "$T"; }

setup nc; run; rc=$?
if [ $rc = 0 ] && cmp -s "$T/wf.bin" "$(H a9-waveform.bin)" && cmp -s "$T/ct.bin" "$(H a9-ctrl-template.bin)" \
   && grep -q 'E Ink blobs installed as root (via nc)' "$T/out" && grep -q 'chown root:root .*/a9fw .*/a9fw/hisense' "$T/chown.log" \
   && ! ls -d "$R"/data/local/tmp/a9-stage-* >/dev/null 2>&1 && [ -f "$R/data/local/tmp/a9fw/qcom/a610_zap.mbn" ]
then ok "root-owned a9fw (the G2a case): installed via the loopback root shell, chowned root, staging removed, GPU firmware kept"
else bad "nc install (rc $rc)"; sed 's/^/     /' "$T/out"; fi
[ "$(command stat -c %a "$(H a9-waveform.bin)" 2>/dev/null)" = 644 ] && ok "installed files are 0644" || bad "file mode $(command stat -c %a "$(H a9-waveform.bin)" 2>/dev/null)"
grep -q 'push .* /data/local/tmp/a9fw' "$T/calls" && bad "pushed straight into a9fw as shell" || ok "adb push only into the private staging dir, never into a9fw"
done_
setup adbd; run; rc=$?
[ $rc = 0 ] && cmp -s "$T/wf.bin" "$(H a9-waveform.bin)" && grep -q 'via adbd' "$T/out" && ok "adbd as root: installed" || { bad "adbd install (rc $rc)"; sed 's/^/     /' "$T/out"; }
done_
setup nc; CTARG="" run; rc=$?
[ $rc = 0 ] && [ -f "$(H a9-waveform.bin)" ] && [ ! -e "$(H a9-ctrl-template.bin)" ] && ok "waveform only (no --template)" || { bad "waveform-only (rc $rc)"; sed 's/^/     /' "$T/out"; }
done_
setup none; t0=$(date +%s); RW=3 run; rc=$?
[ $rc = 2 ] && grep -q '!! E Ink blob install FAILED: no root shell after 3 s' "$T/out" && [ ! -e "$(H a9-waveform.bin)" ] && [ $(( $(date +%s) - t0 )) -ge 3 ] \
  && ok "no root shell: waited A9M_ROOT_WAIT, rc 2, clear FAILED line, nothing installed" || { bad "no-root (rc $rc)"; sed 's/^/     /' "$T/out"; }
done_
setup nc pushfail; run; rc=$?
[ $rc = 3 ] && grep -q 'FAILED: adb push of the waveform' "$T/out" && ok "staging push fails -> rc 3" || { bad "pushfail (rc $rc)"; sed 's/^/     /' "$T/out"; }
done_
setup nc corrupt; run; rc=$?
[ $rc = 4 ] && grep -q 'FAILED: .*a9-waveform.bin sha256 .* != host' "$T/out" && ok "installed copy differs from the host blob -> rc 4" || { bad "corrupt (rc $rc)"; sed 's/^/     /' "$T/out"; }
done_
setup nc; touch "$T/badmode"; run; rc=$?
[ $rc = 4 ] && grep -q 'is not root:root 755' "$T/out" && ok "tree not root:root 755 after install -> rc 4" || { bad "badmode (rc $rc)"; sed 's/^/     /' "$T/out"; }
done_
setup nc; ADB="$T/adb" "$INST" "$T/missing.bin" > "$T/out" 2>&1; rc=$?
[ $rc = 4 ] && grep -q 'no waveform file' "$T/out" && ! [ -s "$T/calls" ] && ok "missing host blob -> rc 4 before any adb call" || bad "missing blob (rc $rc)"
done_
[ $fails = 0 ] && echo "test-eink-fwinstall: PASS" || { echo "test-eink-fwinstall: $fails FAILED"; exit 1; }
