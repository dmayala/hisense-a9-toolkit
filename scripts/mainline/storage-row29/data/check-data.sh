#!/system/bin/sh
# Invoke via a9ml_rootsh. Public marker only; no partition writes or formatting.
set -eu
case "${1-}" in create|verify) mode=$1 ;; *) echo 'Usage: check-data.sh create|verify' >&2; exit 2 ;; esac
[ "$#" = 1 ] && [ "$(id -u)" = 0 ] && [ "$(getprop ro.boot.slot_suffix)" = _b ]
[ "$(getprop sys.boot_completed)" = 1 ]
node=/dev/block/mapper/a9-row29-data
[ -b "$node" ]
hex=$(stat -L -c '%t:%T' "$node")
maj=$((0x${hex%:*})); min=$((0x${hex#*:}))
dev=$maj:$min
[ "$(cat /sys/dev/block/$dev/dm/name)" = a9-row29-data ]
[ "$(cat /sys/dev/block/$dev/dm/uuid)" = A9-ROW29-DATA-20261021 ]
[ "$(cat /sys/dev/block/$dev/size)" = 6191304 ]
[ "$(cat /sys/dev/block/$dev/slaves/sde/dev)" = "$(cat /sys/class/block/sde/dev)" ]
[ "$(ls /sys/dev/block/$dev/slaves | wc -w)" -eq 1 ]
awk -v dev="$dev" '$5=="/data" {n++; for(i=1;i<=NF;i++) if($i=="-") {if($3==dev && $4=="/" && $(i+1)=="f2fs" && $6 ~ /(^|,)rw(,|$)/) ok++}} END {exit !(n==1 && ok==1)}' /proc/self/mountinfo
table=$(dmctl table a9-row29-data)
parent=$(cat /sys/class/block/sde/dev)
printf '%s\n' "$table" | awk -v parent="$parent" '
 NF && !/^Targets/ {lines++}
 /linear/ {n++; gsub(/[:,]/," ",$0); if($1=="0-6191304" && $2=="linear" && $3==substr(parent,1,index(parent,":")-1) && $4==substr(parent,index(parent,":")+1) && $5==2197264 && NF==5) ok++}
 END {exit !(lines==1 && n==1 && ok==1)}'
marker=/data/local/tmp/row29-persistent-proof
text='ROW29 persistent data proof 20261021'
want=$(printf '%s\n' "$text" | sha256sum); want=${want%% *}
if [ "$mode" = create ]; then
    [ ! -e "$marker" ] && [ ! -L "$marker" ]
    (set -C; printf '%s\n' "$text" > "$marker")
    sync
fi
[ -f "$marker" ] && [ ! -L "$marker" ]
got=$(sha256sum "$marker"); got=${got%% *}
[ "$got" = "$want" ]
echo "ROW29 DATA CHECK PASS mode=$mode marker_sha256=$got"
printf 'BOOT_ID='; cat /proc/sys/kernel/random/boot_id
printf '%s\n' "$table"
df -k /data
