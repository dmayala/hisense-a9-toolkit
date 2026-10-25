#!/system/bin/sh
# Manual B5 POC commands, run by the coordinator on the mainline test vendor.
# setup selects a9service's real SharedPreferences mode and framework settings.
# No auto-start, evdev reader, panel gate writes or compositor restart.
set -eu
PKG=com.lmqr.ha9_comp_service
SVC=$PKG/.A9AccessibilityService
PREFDIR=/data/user/0/$PKG/shared_prefs
PREFS=$PREFDIR/${PKG}_preferences.xml

prefs_metadata() {
    # Metadata only: never print app preferences content. Run before any setup
    # command can fail, and again after the edit, so absent and denied differ.
    echo "AOD prefs metadata: domain=$(id -Z)"
    for path in "/data/user/0/$PKG" "$PREFDIR" "$PREFS" "$PREFS.bak"; do
        # Android ls -Z can report EINVAL for an absent backup. Keep access
        # failures visible through ordinary ls, without a misleading xattr error.
        if [ -e "$path" ] || [ -L "$path" ]; then
            ls -Zd "$path" 2>&1 || :
        else
            ls -d "$path" 2>&1 || :
        fi
    done
}

setup_closed() {
    # RRO changes and accessibility restarts can cause a burst of composition.
    # Configure before the coordinator opens the panel, never during a waveform.
    for gate in a9_eink/enable fp9929a/allow_power_on panel_hisense_hlte556n_eink/enable; do
        path=/sys/module/${gate%/*}/parameters/${gate#*/}
        value=$(cat "$path" 2>/dev/null) || {
            echo "AOD setup: cannot read $path; load the vendor first" >&2; return 1;
        }
        case "$value" in
            N|n|0) ;;
            *) echo "AOD setup requires closed E Ink gates: $gate=$value; configure before opening on the next boot" >&2; return 1 ;;
        esac
    done
}

pref_source() {
    # SharedPreferencesImpl recovers .bak before reading the main file.
    if [ -f "$PREFS.bak" ]; then echo "$PREFS.bak"; else echo "$PREFS"; fi
}

pref() {
    CLASSPATH=/vendor/etc/a9-aod/prefs.jar app_process /system/bin a9.aod.Preferences "$@"
}

restore_service() {
    cmd package unstop --user 0 "$PKG" >/dev/null
    settings put secure enabled_accessibility_services "$target_services"
    settings put secure accessibility_enabled 1
}

set_overlay_mode() {
    # Preserve other services, detach A9, then stop its process so its cached
    # SharedPreferences cannot overwrite the disk edit when it later exits.
    remaining= saved_ifs=$IFS
    IFS=:
    set -f
    for component in $target_services; do
        case "$component" in
            "$SVC"|"$PKG/$PKG.A9AccessibilityService"|null|'') ;;
            *) remaining=${remaining:+$remaining:}$component ;;
        esac
    done
    set +f
    IFS=$saved_ifs
    target_services=${remaining:+$remaining:}$SVC
    settings put secure enabled_accessibility_services "$remaining"
    # Restore binding even if an edit command fails; no other app is stopped.
    trap 'set +e; rm -f "$PREFS.a9-aod-new"; restore_service' EXIT
    am force-stop --user 0 "$PKG"
    owner=$(stat -c '%u:%g' "/data/user/0/$PKG")
    if [ ! -d "$PREFDIR" ]; then
        mkdir "$PREFDIR"
        chown "$owner" "$PREFDIR"
        chmod 0700 "$PREFDIR"
    fi
    source=$(pref_source)
    umask 077
    pref overlay "$source" > "$PREFS.a9-aod-new"
    if [ -f "$source" ] && [ ! -e "$PREFS.a9-aod-before" ]; then
        cp -p "$source" "$PREFS.a9-aod-before"
    fi
    chown "$owner" "$PREFS.a9-aod-new"
    chmod 0600 "$PREFS.a9-aod-new"
    mv -f "$PREFS.a9-aod-new" "$PREFS"
    # A stale .bak would cause Android to undo the new value on service start.
    rm -f "$PREFS.bak"
    restorecon -RF "$PREFDIR"
    [ "$(pref get "$PREFS")" = overlay ]
    restore_service
    trap - EXIT
}

state() {
    echo "uptime=$(cat /proc/uptime)"
    source=$(pref_source)
    if [ -r "$source" ]; then
        # This app writes three ASCII enum values in ordinary Android XML.
        # Keep state cheap while waveforms run: never start ART just to read it.
        # Android's BRE implementation does not support GNU's \| extension.
        # Capture the value with portable BRE; the case below validates the enum.
        mode=$(sed -n 's@.*<string name="aod_mode">\([^<]*\)</string>.*@\1@p' "$source")
        case "$mode" in
            overlay|static|stock) echo "aod_mode(app SharedPreferences)=$mode" ;;
            *) echo 'aod_mode(app SharedPreferences)=unrecognized XML; verify with setup while gates are closed' ;;
        esac
    else
        echo 'aod_mode(app SharedPreferences)=unreadable or absent (app default overlay)'
    fi
    for key in doze_always_on doze_enabled accessibility_enabled enabled_accessibility_services; do
        echo "$key=$(settings get secure "$key")"
    done
    echo "animator_duration_scale=$(settings get global animator_duration_scale)"
    echo "stay_on_while_plugged_in=$(settings get global stay_on_while_plugged_in)"
    cmd overlay list --user 0 | grep -F me.phh.treble.overlay.misc.aod || true
    echo "sys.linevibrator_type=$(getprop sys.linevibrator_type)"
    echo "debug.a9.colorfade=$(getprop debug.a9.colorfade)"
    echo "doze.display.supported=$(getprop doze.display.supported)"
    # Dumpsys field names differ between GSI revisions. A missing field must
    # not end this diagnostic early under set -e and hide the remaining state.
    dumpsys power | grep -E 'mWakefulness=|mHoldingDisplaySuspendBlocker=' || true
    dumpsys display | grep -E 'mScreenState=|mColorFadeEnabled=' || true
    dumpsys accessibility | grep -F com.lmqr.ha9_comp_service || true
    # Read-only panel-path evidence: an awake HOME redraw must work before AOD
    # can be assessed. Never reopen or clear a fault here.
    for key in vendor.a9ml.eink.gate vendor.a9ml.eink.fault vendor.a9ml.eink.ready vendor.a9ml.eink.card; do
        echo "$key=$(getprop "$key")"
    done
    for status in /sys/class/drm/card*-*/eink/status; do
        [ ! -r "$status" ] || cat "$status"
    done
    for gate in a9_eink/enable fp9929a/allow_power_on panel_hisense_hlte556n_eink/enable; do
        path=/sys/module/${gate%/*}/parameters/${gate#*/}
        echo "$gate=$(cat "$path" 2>/dev/null || echo unreadable)"
    done
}

press() {
    # Android input keyevent synthesizes scanCode=0. Inject EV_KEY instead so
    # a9service's scanCode==766 handler is exercised with the shipped .kl.
    event=
    for name in /sys/class/input/event*/device/name; do
        if [ "$(cat "$name")" = gpio-keys ]; then
            event=${name%/device/name}
            event=/dev/input/${event##*/}
            break
        fi
    done
    [ -n "$event" ] || { echo 'gpio-keys input device not found' >&2; return 1; }
    sendevent "$event" 1 766 1
    sendevent "$event" 0 0 0
    sleep 0.05
    sendevent "$event" 1 766 0
    sendevent "$event" 0 0 0
}

case "${1:-state}" in
setup)
    prefs_metadata
    setup_closed
    # End any existing doze dream before changing the capability overlay, so
    # the next sleep creates its DozeMachine adapters with the new resources.
    echo 'AOD setup: input wake'
    input keyevent KEYCODE_WAKEUP
    echo 'AOD setup: overlay and accessibility configuration'
    appops set com.lmqr.ha9_comp_service SYSTEM_ALERT_WINDOW allow
    # Framework availability is already working on this GSI (k5 reaches Dozing).
    # Its separate .aod RRO is static/property-gated; do not toggle it at runtime.
    # drm_hwcomposer lacks native doze/self-refresh. Keep SystemUI's base false
    # capability values so its adapters request ON and SF keeps compositing.
    cmd overlay disable --user 0 me.phh.treble.overlay.misc.aod_systemui
    target_services=$(settings get secure enabled_accessibility_services)
    echo 'AOD setup: SharedPreferences edit'
    set_overlay_mode
    prefs_metadata
    settings put secure doze_always_on 1
    settings put secure doze_enabled 1
    settings put global animator_duration_scale 1
    # Same sentinel as a9service's stl96 command. Apply after the ROM's boot
    # hook, which initializes this property to 47. No panel/sysfs power writes.
    setprop sys.linevibrator_type 96
    echo 'Overlay AOD configured: app aod_mode=overlay; continuous composition (no DOZE_SUSPEND).'
    ;;
single) press ;;
double) press; sleep 0.10; press ;;
sleep) input keyevent KEYCODE_SLEEP ;;
wake) input keyevent KEYCODE_WAKEUP ;;
state) state ;;
*) echo 'usage: a9ml-key-aod.sh setup|single|double|sleep|wake|state' >&2; exit 2 ;;
esac
