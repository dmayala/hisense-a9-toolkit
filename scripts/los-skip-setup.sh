#!/usr/bin/env bash
#
# los-skip-setup.sh — get a freshly wiped LineageOS GSI past its setup wizard
# from adb, without touching the panel.
#
# What the wizard leaves behind when it finishes normally is two settings plus
# itself disabled. This sets the same three things directly:
#   global device_provisioned=1
#   secure user_setup_complete=1
#   pm disable-user org.lineageos.setupwizard
# then sends HOME. No root needed — the adb shell uid may write these.
# Verified 2026-10-10 on LineageOS 23.2 (A16 GSI): launcher focused, nav bar
# working, StatusBarManager disable flags 0x0.
#
# Requires: booted Android with adb authorised. After a /data wipe that means
# an image built with the debug props (adb on, ro.adb.secure=0); for a release
# image, enable USB debugging on the panel first.
#
# Root manager. After a /data wipe the Magisk daemon in the boot image installs
# only a *stub* manager. The stub pops "Please connect to the Internet!
# Upgrading to full Magisk is required." over the home screen at boot, and `su`
# is denied because no full manager exists to approve it. The phone has no
# network, so install the full manager APK from $A9_DATA, then grant root to
# the adb shell by tapping GRANT on the Superuser prompt. The dialog
# auto-denies after ~5 s, and with "Forever" selected a timeout is saved as a
# PERMANENT deny (fix: manager app > Superuser > Shell). Never tap OK on the
# app's "Requires Additional Setup" dialog: it re-flashes the boot partition.
# Official Magisk v30.7 only (both slots since 2026-10-11): com.topjohnwu.magisk,
#   MAGISK_APK=<apk>, or the newest Magisk-v*.apk under $A9_DATA. (Kitsune Mask
#   handling was removed 2026-10-11; see docs/root-magisk.md.)
# Without a full APK -> force-stop + `pm disable-user` the stub (popup gone; root
# stays unusable, as it already was). `pm enable` reverts.
#
# Usage:
#   ./los-skip-setup.sh            # skip if not yet provisioned, fix the root manager, verify
#   ./los-skip-setup.sh root       # only the root-manager stub fix (`kitsune` still accepted as an alias)
#   ./los-skip-setup.sh status     # show state only
#   ./los-skip-setup.sh undo       # re-enable the wizard and clear the flags
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=a9-env.sh
. "$HERE/a9-env.sh"
WIZ=org.lineageos.setupwizard
GWIZ=com.google.android.setupwizard   # GApps builds (Doze-off GAPPS)
MAGISK=com.topjohnwu.magisk
SHOTS="${SHOTS:-${TMPDIR:-/tmp}}"

[ "$(a9_adb_state)" = "device" ] || { echo "ERROR: need booted Android on adb (state='$(a9_adb_state)')." >&2; exit 1; }

status() {
  "$ADB" shell "
    echo \"lineage            \$(getprop ro.lineage.version)\"
    echo \"device_provisioned \$(settings get global device_provisioned)\"
    echo \"user_setup_complete \$(settings get secure user_setup_complete)\"
    echo \"wizard             \$(pm list packages -d | grep -q $WIZ && echo disabled || echo enabled)\"
    pm path $GWIZ >/dev/null 2>&1 && echo \"google wizard      \$(pm list packages -d | grep -q $GWIZ && echo disabled || echo enabled)\"
    echo \"shell root         \$(su -c id 2>/dev/null | grep -q uid=0 && echo yes || echo no)\"
    for p in $MAGISK; do pm path \$p >/dev/null 2>&1 && echo \"root manager       \$p \$(pm list packages -d | grep -q \$p && echo disabled || echo enabled)\"; done
    echo \"wakefulness        \$(dumpsys power | sed -n 's/.*mWakefulness=//p' | head -1)\"
    dumpsys window | grep -m1 mCurrentFocus | sed 's/^ */focus              /'
    dumpsys statusbar | grep -E 'mDisabled[12]=' | tr -d ' ' | tr '\n' ' '; echo
  " 2>/dev/null | tr -d '\r'
}

# Full root manager if we have one; otherwise silence the stub.
fix_root_manager() {
  local pkg apk vc found=""
  for pkg in "$MAGISK"; do
    "$ADB" shell pm path "$pkg" >/dev/null 2>&1 || continue
    found=1
    vc=$("$ADB" shell dumpsys package "$pkg" 2>/dev/null | sed -n 's/.*versionCode=\([0-9]*\).*/\1/p' | head -1)
    # Magisk's stub has a small versionCode (< 100); the full manager is 30700 (v30.7).
    if [ "${vc:-0}" -gt 1000 ]; then echo "root manager       $pkg full (versionCode $vc)"; grant_shell_root; return; fi
    apk="${MAGISK_APK:-$(find "$A9_DATA/" -maxdepth 4 -type f -name 'Magisk-v*.apk' 2>/dev/null | sort -V | tail -1)}"
    if [ -n "$apk" ] && [ -f "$apk" ]; then
      echo "root manager       $pkg stub -> installing full manager: $apk"
      "$ADB" shell pm enable "$pkg" >/dev/null 2>&1
      "$ADB" install -r "$apk" 2>&1 | tail -1
      "$ADB" shell am force-stop "$pkg" >/dev/null 2>&1
      grant_shell_root
    else
      echo "root manager       $pkg stub only, no full APK -> disabling stub"
      "$ADB" shell "am force-stop $pkg; pm disable-user --user 0 $pkg" >/dev/null 2>&1
    fi
    return
  done
  [ -n "$found" ] || echo "root manager       none installed"
}

# Turn on "[SharedUID] Shell" in Magisk > Superuser through the UI (uiautomator).
# Used when Magisk holds a stored deny for uid 2000. Needs the full manager.
magisk_allow_shell_ui() {
  local xml sw bounds
  dump() { "$ADB" shell 'uiautomator dump /sdcard/.a9ui.xml >/dev/null 2>&1; cat /sdcard/.a9ui.xml; rm -f /sdcard/.a9ui.xml' 2>/dev/null | tr '>' '\n'; }
  center() { set -- $(printf '%s' "$1" | sed -n 's/.*bounds="\[\([0-9]*\),\([0-9]*\)\]\[\([0-9]*\),\([0-9]*\)\]".*/\1 \2 \3 \4/p'); echo "$(( ($1+$3)/2 )) $(( ($2+$4)/2 ))"; }
  "$ADB" shell 'input keyevent KEYCODE_WAKEUP; wm dismiss-keyguard; am force-stop com.topjohnwu.magisk; monkey -p com.topjohnwu.magisk -c android.intent.category.LAUNCHER 1' >/dev/null 2>&1
  sleep 4
  xml=$(dump); sw=$(printf '%s\n' "$xml" | grep -m1 'text="Superuser"')
  [ -n "$sw" ] || { echo "shell root         Magisk UI: no Superuser tab found" >&2; return 1; }
  "$ADB" shell input tap $(center "$sw"); sleep 3
  xml=$(dump)
  # the switch after the "[SharedUID] Shell" row
  bounds=$(printf '%s\n' "$xml" | sed -n '/Shell"/,$p' | grep -m1 'android.widget.Switch')
  [ -n "$bounds" ] || { echo "shell root         Magisk UI: no Shell entry in Superuser" >&2; return 1; }
  if printf '%s' "$bounds" | grep -q 'checked="false"'; then
    "$ADB" shell input tap $(center "$bounds"); sleep 2
    echo "shell root         flipped Magisk > Superuser > Shell to allowed"
  fi
  "$ADB" shell input keyevent KEYCODE_HOME >/dev/null 2>&1
  return 0
}

# Grant root to the adb shell (uid 2000) via the Superuser prompt.
# ONE su call does both jobs: with a stored policy it returns at once (granted
# or denied); with none it raises the prompt, which we tap. A separate
# "is it granted?" probe would itself raise a prompt that then times out into a
# permanent deny, so there is deliberately none.
grant_shell_root() {
  local out tmp; tmp=$(mktemp)
  "$ADB" shell 'input keyevent KEYCODE_WAKEUP; wm dismiss-keyguard' >/dev/null 2>&1
  ( timeout 40 "$ADB" shell 'su -c id' > "$tmp" 2>&1 ) & local pid=$!
  local tapped=""
  for _ in $(seq 1 60); do
    kill -0 "$pid" 2>/dev/null || break                        # su answered on its own
    if "$ADB" shell dumpsys window 2>/dev/null | grep -m1 mCurrentFocus | grep -q SuRequestActivity; then
      # Find GRANT on screen; fall back to where it sits on the 824x1648 panel.
      xy=$("$ADB" shell 'uiautomator dump /sdcard/.a9ui.xml >/dev/null 2>&1; cat /sdcard/.a9ui.xml; rm -f /sdcard/.a9ui.xml' 2>/dev/null \
           | tr '>' '\n' | grep -i 'text="GRANT"' | sed -n 's/.*bounds="\[\([0-9]*\),\([0-9]*\)\]\[\([0-9]*\),\([0-9]*\)\]".*/\1 \2 \3 \4/p' | head -1)
      if [ -n "$xy" ]; then set -- $xy; x=$(( ($1+$3)/2 )); y=$(( ($2+$4)/2 )); else x=574; y=1060; fi
      "$ADB" shell input tap "$x" "$y"; tapped=" (tapped GRANT at $x,$y)"
      break
    fi
    sleep 0.25
  done
  wait "$pid" 2>/dev/null; out=$(cat "$tmp"); rm -f "$tmp"
  if printf '%s' "$out" | grep -q 'uid=0'; then echo "shell root         granted$tapped"; return 0; fi
  # A stored deny answers at once with "Permission denied" (Magisk logs
  # "su: request rejected (2000)"). It happens when su was tried while a setup
  # wizard was in front. Flip the Shell switch in Magisk > Superuser, once.
  if printf '%s' "$out" | grep -q 'Permission denied' && [ -z "${_A9_ALLOWED:-}" ]; then
    _A9_ALLOWED=1
    magisk_allow_shell_ui && { grant_shell_root; return; }
  fi
  if printf '%s' "$out" | grep -q 'inaccessible or not found'; then
    echo "shell root         no su at all: Magisk's /data/adb/magisk is empty after a wipe. Run ./magisk-env.sh --apply" >&2
    return 1
  fi
  echo "shell root         NOT granted ($out). If it was denied 'Forever': Magisk app > Superuser > enable Shell." >&2
  return 1
}

case "${1:-skip}" in
  root|kitsune) fix_root_manager ;;

  status) status ;;

  undo)
    "$ADB" shell "pm enable --user 0 $WIZ; settings put secure user_setup_complete 0; settings put global device_provisioned 0" 2>&1 | tr -d '\r'
    echo "Wizard re-enabled. It runs on the next boot (or: adb shell am start -n $WIZ/.SetupWizardActivity)."
    ;;

  skip)
    echo "-- before"; status
    # GApps builds run Google's wizard. It is where the Google account is added,
    # so it is not skipped by default: finish it on the panel, then run
    # `./los-skip-setup.sh root`. SKIP_GOOGLE=1 disables it like the LOS one.
    if "$ADB" shell pm path "$GWIZ" >/dev/null 2>&1 && [ "${SKIP_GOOGLE:-0}" != 1 ]; then
      if "$ADB" shell dumpsys window 2>/dev/null | grep -m1 mCurrentFocus | grep -q "$GWIZ"; then
        echo "Google setup wizard is on screen: finish it on the phone (Google sign-in), then run: $0 root"
        echo "(SKIP_GOOGLE=1 $0 skips it instead.)"
        exit 0
      fi
    fi
    # The screen is usually off by the time anyone looks; a screencap of an
    # asleep panel is solid black and proves nothing. Wake it first.
    "$ADB" shell input keyevent KEYCODE_WAKEUP
    "$ADB" shell "
      settings put global device_provisioned 1
      settings put secure user_setup_complete 1
      pm disable-user --user 0 $WIZ >/dev/null
      [ \"${SKIP_GOOGLE:-0}\" = 1 ] && pm disable-user --user 0 $GWIZ >/dev/null 2>&1
      am start -W -a android.intent.action.MAIN -c android.intent.category.HOME >/dev/null
    " 2>&1 | tr -d '\r'
    fix_root_manager
    sleep 3
    echo "-- after"; status
    out="$SHOTS/a9-after-setup-skip.png"
    "$ADB" exec-out screencap -p > "$out" && echo "screenshot: $out"
    if "$ADB" shell dumpsys window 2>/dev/null | grep -m1 mCurrentFocus | grep -q "$WIZ"; then
      echo "WARNING: the wizard still has focus." >&2; exit 1
    fi
    echo "Setup skipped. After a /data wipe also run ./a9-aod-setup.sh (AOD settings)."
    ;;

  *) echo "usage: $0 [skip|status|undo|root]" >&2; exit 2 ;;
esac
