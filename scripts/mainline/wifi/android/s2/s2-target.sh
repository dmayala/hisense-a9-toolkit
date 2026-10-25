#!/system/bin/sh
# Review candidate ONLY; no host transport, boot, mount, modem start, standalone WPA or DHCP.
# Coordinator installs the reviewed bundle/GO on RAM only AFTER independent review.
set -u
umask 077
B=/data/local/tmp/a9-wifi-s2
D=/mnt/a9-wifi/trial/android-s2
IW=/mnt/a9-wifi/tools/iw
AWK=/system/bin/awk
phase=entry
admitted=0
note() { echo "S2_GATE phase=$phase $*"; }
fail() { note 'result=failed'; exit 1; }
bridge() {
    # Constant argv + public classpath only; all JVM diagnostics go to /dev/null.
    CLASSPATH=$B/bridge.jar timeout -s KILL "$1" /system/bin/app_process /system/bin a9.wifi.s2.Bridge "$2" 2>/dev/null
    brc=$?
    note "command=bridge-$2 rc=$brc"
    return "$brc"
}
check_active() {
    [ "$(getprop vendor.a9ml.wifi.session.state)" = serving ] &&
    [ "$(getprop vendor.a9ml.wifi.session.stop)" != 1 ] &&
    [ "$(getprop init.svc.logd)" = stopped ]
}
cleanup() {
    trap - EXIT HUP INT TERM
    phase=cleanup
    ok=0
    if [ "$admitted" = 1 ]; then bridge 25 cleanup || ok=1; fi
    # Only fixed RAM filename, no configuration/store scraping, no directory traversal.
    [ ! -f "$D/input" ] || rm -f "$D/input" || ok=1
    note "framework_cleanup_rc=$ok"
    # Always revoke and use the existing ordered session stop; never restart MPSS.
    /vendor/bin/a9ml-wifi-session.sh stop >/dev/null 2>&1 || ok=1
    timeout -s KILL 3 ip link set wlan0 down >/dev/null 2>&1 || :
    [ "$ok" = 0 ] || note 'cleanup=unproved restore_required=yes'
    [ "$ok" = 0 ] || exit 1
}
radio() {
    phase=regulatory
    check_active || fail
    phy_path=$(readlink -f /sys/class/net/wlan0/phy80211) || fail
    phy=${phy_path##*/}
    case "$phy" in phy[0-9]*) case "${phy#phy}" in *[!0-9]*) fail;; esac;; *) fail;; esac
    rc=0; raw=$(timeout -s KILL 3 "$IW" reg get 2>/dev/null) || rc=$?
    note "iw_rc=$rc"; [ "$rc" = 0 ] || fail
    printf '%s\n' "$raw" | "$AWK" -v p="${phy#phy}" -f "$B/reg.awk" || fail
    phase=channel
    rc=0; raw=$(timeout -s KILL 3 "$IW" phy "$phy" info 2>/dev/null) || rc=$?
    note "iw_rc=$rc"; [ "$rc" = 0 ] || fail
    printf '%s\n' "$raw" | "$AWK" -f "$B/channel.awk" || fail
    unset raw
}
power() {
    phase=power-$1
    rc=0; raw=$(timeout -s KILL 3 "$IW" dev wlan0 info 2>/dev/null) || rc=$?
    printf '%s\n' "$raw" | "$AWK" -v rc="$rc" -v stage="$1" -f "$B/power.awk" || fail
    unset raw
}
power_limit() {
    phase=power-limit
    rc=0; timeout -s KILL 3 "$IW" dev wlan0 set txpower limit 2000 >/dev/null 2>&1 || rc=$?
    note "iw_rc=$rc"; [ "$rc" = 0 ] || fail
}
[ "$#" = 1 ] || fail
case "$1" in run|receive|probe) mode=$1;; *) fail;; esac
phase=admission
[ "$(id -u)" = 0 ] && [ "$(getprop ro.boot.slot_suffix)" = _b ] || fail
case "$(uname -r)" in 7.*) ;; *) fail;; esac
for p in ro.a9ml.vendor ro.a9ml.wifi ro.a9ml.wifi.android ro.a9ml.wifi.session; do [ "$(getprop "$p")" = 1 ] || fail; done
phase=awk-pin
awk_sha=$(sha256sum "$AWK"); awk_sha=${awk_sha%% *}
[ "$awk_sha" = 39d9a31c314cb3f7166d127b3fdc12e97b5f100308003a0869147a38ad11bf55 ] || fail
phase=ram-data
# Android binds /data/data at /data/user/0 on the same RAM filesystem.
# Only that observed alias is allowed; credential-bearing subtree substitutions
# and every other child mount still refuse. Never print raw mount rows/paths.
"$AWK" -v mode="$mode" '$2=="/data" {n++; if($1!="/dev/block/ram0" || $3!="ext4")root_bad=1}
 $2 ~ /^\/data\// {children++;
  if($2!="/data/user/0")unexpected=1;
  else {user0++;if($1!="/dev/block/ram0" || $3!="ext4")user_bad=1}}
 END {branch="accepted";
  if(n!=1)branch="root-count";else if(root_bad)branch="root-backing";
  else if(unexpected)branch="unexpected-submount";else if(user0>1)branch="user0-duplicate";
  else if(user_bad)branch="user0-backing";
  if(mode!="receive" || branch!="accepted")
   printf "S2_GATE phase=ram-data branch=%s roots=%d children=%d user0=%d\n",branch,n,children,user0;
  exit (branch!="accepted")}' /proc/mounts || fail
phase=ram-swap
"$AWK" 'NR>1 {bad=1} END {exit bad}' /proc/swaps || fail
phase=ram-evidence
"$AWK" '$2=="/mnt/a9-wifi/trial" {n++;split($4,a,",");for(i in a)o[a[i]]=1;
 if($3!="tmpfs" || !o["noswap"] || !o["nodev"] || !o["nosuid"] || !o["noexec"])bad=1}
 END {exit !(n==1 && !bad)}' /proc/mounts || fail
phase=private-paths
if [ "$mode" = receive ]; then
    [ ! -e "$D" ] && [ ! -L "$D" ] || fail
    mkdir -m 700 "$D" || fail
fi
for dir in /data/local /data/local/tmp "$B" /mnt/a9-wifi/trial; do
 [ -d "$dir" ] && [ ! -L "$dir" ] || fail
done
[ "$(stat -c '%u:%a' "$B")" = 0:700 ] || fail
if [ "$mode" = probe ]; then
    [ ! -e "$D" ] && [ ! -L "$D" ] || fail
else
    [ -d "$D" ] && [ ! -L "$D" ] && [ "$(stat -c '%u:%a' "$D")" = 0:700 ] || fail
fi
check_active || fail
trap cleanup EXIT
trap 'exit 1' HUP INT TERM
phase=review-seal
# Fixed-file manifest. No shell sourcing or untrusted filenames passed to sha256sum.
[ -f "$B/GO" ] && [ ! -L "$B/GO" ] && [ "$(stat -c '%u:%a:%h' "$B/GO")" = 0:400:1 ] || fail
"$AWK" 'NR==1 {if($0!="I_AUTHORIZE_REVIEWED_ANDROID_S2")bad=1}
 NR==2 {if($0!="country=US frequency=2427 persistent_userdata=no")bad=1}
 NR>2 {if(NF!=2 || length($1)!=64 || $1~/[^0-9a-f]/)bad=1;
 names[NR]=$2}
 END {exit !(NR==9 && !bad && names[3]=="bridge.jar" && names[4]=="s2-target.sh" &&
  names[5]=="reg.awk" && names[6]=="channel.awk" && names[7]=="power.awk" &&
  names[8]=="s2-encode.py" && names[9]=="provenance.json")}' "$B/GO" || fail
for f in bridge.jar s2-target.sh reg.awk channel.awk power.awk s2-encode.py provenance.json; do
 [ -f "$B/$f" ] && [ ! -L "$B/$f" ] && [ "$(stat -c '%u:%h' "$B/$f")" = 0:1 ] || fail
 expected=$("$AWK" -v name="$f" 'NR>2 && $2==name {print $1}' "$B/GO")
 actual=$(sha256sum "$B/$f"); actual=${actual%% *}
 [ "$actual" = "$expected" ] || fail
done
phase=iw-pin
# Pinned S1 iw; never substitute host iw or a differing command ABI.
iw_sha=$(sha256sum "$IW"); iw_sha=${iw_sha%% *}
[ "$iw_sha" = 6a6951202b0e1120e7ba05fc57d7faec60790ede5614b5477c79fa386002703b ] || fail
phase=remaining-budget
read -r total extra < /mnt/a9-wifi/trial/session-total || fail
[ -z "$extra" ] || fail
read -r started extra < /mnt/a9-wifi/trial/started.uptime || fail
[ -z "$extra" ] || fail
read -r up idle < /proc/uptime || fail
up=${up%%.*}
case "$total:$started:$up" in *[!0-9:]*|:*|*::*|*:) fail;; esac
[ "$total" -ge 600 ] && [ "$total" -le 14400 ] && [ "$up" -ge "$started" ] || fail
[ $((started+total-up)) -ge 450 ] || fail
admitted=1
if [ "$mode" = probe ]; then
    radio
    power_limit
    power before
    phase=probe
    bridge 25 probe || fail
    phase=public-scan
    bridge 25 scan || fail
    radio
    power before
    trap - EXIT HUP INT TERM
    note 'public_preflight=accepted'
    exit 0
fi
if [ "$mode" = receive ]; then
    phase=receive
    set -C
    head -c 513 > "$D/input" || fail
    set +C
    [ "$(stat -c '%u:%a:%h' "$D/input")" = 0:600:1 ] || fail
    size=$(stat -c '%s' "$D/input")
    [ "$size" -ge 1 ] && [ "$size" -le 512 ] || fail
    check_active || fail
    trap - EXIT HUP INT TERM
    note 'input=ready'
    exit 0
fi
# Permanent one-attempt latch in the already-reviewed session's RAM state.
mkdir -m 700 /mnt/a9-wifi/session/s2-attempted || fail
phase=probe
radio
power_limit
power before
bridge 25 probe || fail
phase=public-scan
bridge 25 scan || fail
phase=framework-connect
check_active || fail
bridge 110 connect || fail
radio
power after
phase=framework-dhcp-web
check_active || fail
bridge 100 web || fail
# Re-check after the web request too (country IE, disconnect, or power drift).
radio
power after
phase=done
note 'result=success'
exit 0
