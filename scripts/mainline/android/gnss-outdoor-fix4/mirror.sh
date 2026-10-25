#!/system/bin/sh
# Root-only bounded mirror of the probe's public numeric summaries. No raw copy.
session=$1; duration=$2
case "$session" in ''|*[!a-f0-9]*) exit 2;; esac
[ "${#session}" = 16 ] || exit 2
case "$duration" in ''|*[!0-9]*) exit 2;; esac
[ "$duration" -le 1000 ] || exit 2
[ "$(id -u)" = 0 ] && [ "$(getprop ro.boot.slot_suffix)" = _b ] || exit 2
# logcat -T 100 follows from now; launch before the single app start. Timeout
# terminates the complete pipe at the app deadline; no reader survives sleep.
timeout -s KILL "$duration" logcat -b main -v raw -T 100 -s A9GNSS:I '*:S' 2>/dev/null |
awk -v session="$session" '
$1 == "A9GNSS" {
    delete row
    for (i=2;i<=NF;i++) {
        n=split($i,p,"="); if(n==2 && !(p[1] in row)) row[p[1]]=p[2]
    }
    if(row["session"]!=session || row["kind"]!~/^(sample|final)$/ ||
       row["fix"]!~/^(yes|no)$/ || row["elapsed_s"]!~/^[0-9]+$/) next
    # Samples at most once per 30 elapsed seconds; exactly one final.
    if(row["kind"]=="final") {if(final++) next}
    else {if(seen && row["elapsed_s"]+0<last+30) next; seen=1;last=row["elapsed_s"]+0}
    out="A9GNSS session=" session " kind=" row["kind"] " fix=" row["fix"]
    keys="ttff_ms accuracy_m sats_used satellites cn0_max_dbhz elapsed_s"
    count=split(keys,k," ")
    for(i=1;i<=count;i++) if(row[k[i]]~/^-?[0-9]+([.][0-9]+)?$/ && length(row[k[i]])<=20)
        out=out " " k[i] "=" row[k[i]]
    print out > "/dev/kmsg"; close("/dev/kmsg")
}'
