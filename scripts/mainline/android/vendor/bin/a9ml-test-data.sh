#!/system/bin/sh
# Shared read-only /data admission for integrated test services. Source this file.
# Accept brd RAM ext4 or the checked ROW29 f2fs mapper; never daily userdata.
a9ml_test_data() (
    proc=${1:-/proc}; sys=${2:-/sys}; data=${3:-/data}
    record=$(awk -v point="$data" '
      $5==point {
        n++
        for(i=7;i<=NF;i++) if($i=="-") {
          if($4=="/" && $6 ~ /(^|,)rw(,|$)/) {
            ok++; record=$3 " " $(i+1) " " $(i+2)
          }
        }
      }
      END {if(n==1 && ok==1) print record; else exit 1}
    ' "$proc/self/mountinfo") || return 1
    set -- $record
    [ "$#" = 3 ] || return 1
    device=$1; fs=$2; source=$3
    case "$device:$fs:$source" in
        1:0:ext4:/dev/block/ram0|1:0:ext4:/dev/ram0) return 0 ;;
    esac
    [ "$fs" = f2fs ] || return 1
    case "$source" in
        /dev/block/mapper/a9-row29-data) ;;
        /dev/block/dm-*)
            index=${source#/dev/block/dm-}
            case "$index" in ''|*[!0-9]*) return 1;; esac ;;
        *) return 1 ;;
    esac
    block=$sys/dev/block/$device
    [ "$(cat "$block/dm/name" 2>/dev/null)" = a9-row29-data ] &&
    [ "$(cat "$block/dm/uuid" 2>/dev/null)" = A9-ROW29-DATA-20261021 ] &&
    [ "$(cat "$block/size" 2>/dev/null)" = 6191304 ] || return 1
    # The boot mapper already checks GPT identity and the exact linear table.
    # Verify the mounted device is that mapping, with its one fixed parent.
    set -- "$block"/slaves/*
    [ "$#" = 1 ] && [ "${1##*/}" = sde ] || return 1
    parent=$(cat "$sys/class/block/sde/dev" 2>/dev/null) || return 1
    [ -n "$parent" ] && [ "$(cat "$1/dev" 2>/dev/null)" = "$parent" ]
)
