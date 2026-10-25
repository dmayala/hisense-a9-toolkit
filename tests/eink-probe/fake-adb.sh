#!/usr/bin/env bash
# Fake adb for eink-probe tests. Never touches a phone. Env: FAKE_DIR holds
#   logical  (name of the screen SurfaceFlinger "renders")
#   state    (name the fake camera shows; follows `logical` via mid)
#   screens/<name>.png
#   calls.log
echo "$*" >> "$FAKE_DIR/calls.log"
case "$*" in
  *"screencap -p"*) cat "$FAKE_DIR/screens/$(cat "$FAKE_DIR/logical").png"; exit 0;;
  *"input "*|*"am start"*)
    target=$(printf '%s\n' "$@" | grep '^SCREEN=' | tail -1 | cut -d= -f2)
    [ -z "$target" ] && target=A
    echo "$target" > "$FAKE_DIR/logical"
    ( sleep 0.3; echo mid > "$FAKE_DIR/state"; sleep 0.3; echo "$target" > "$FAKE_DIR/state" ) >/dev/null 2>&1 &
    exit 0;;
esac
exit 0
