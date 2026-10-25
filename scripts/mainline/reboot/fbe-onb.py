#!/usr/bin/env python3
"""Short, read-only FBE capture inside the existing owning b-try hook."""
import os
import argparse
from pathlib import Path
import re
import subprocess as sp
import sys

HERE = Path(__file__).resolve().parent
KEY = re.compile(
    r'A9_FBE_KEY begin_rc=-[0-9]{1,10} chars_rc=-?[0-9]{1,10} requested_gcm=[01] '
    r'hardware_aes=[01] hardware_gcm=[01] hardware_decrypt=[01] '
    r'os_version=[0-9]{1,10} os_patch=[0-9]{1,10} vendor_patch=[0-9]{1,10}')
CONFIG = re.compile(r'A9_FBE_CONFIG configure_rc=-?[0-9]{1,10} os_version=[0-9]{1,10} os_patch=[0-9]{1,10} vendor_patch=[0-9]{1,10} boot_patch_present=[01] boot_patch=[0-9]{1,10}')
LOCAL = re.compile(r'A9_FBE_LOCAL local_rc=-?[0-9]{1,10} verified=0 hardware_aes=[01] hardware_gcm=[01] hardware_decrypt=[01] os_version=[0-9]{1,10} os_patch=[0-9]{1,10} vendor_patch=[0-9]{1,10} boot_patch_present=[01] boot_patch=[0-9]{1,10}')
BOOT = re.compile(r'A9_FBE_BOOT keymaster_loaded=[01] verified_state=[0-4] flash_locked=[0-2] device_locked=[0-2]')
STATE = re.compile(r'A9_FBE_STATE data_rc=-?[0-9]{1,10} data_ready=[01] data_f2fs=[0-9]{1,5} data_tmpfs=[0-9]{1,5}')
SOURCE = re.compile(r'A9_FBE_SOURCE source=1')
READ = re.compile(r'A9_FBE_READ kernel_opened=[01] logcat_opened=[01]')
MOUNT = re.compile(r'A9_FBE_MOUNT bootstrap_detached=[01] bootstrap_restored=[01]')
CAPTURE = r'''
# Toybox BRE has no escaped alternation. Extract marker suffixes portably;
# records() below is the exact numeric whitelist, including rejection of extras.
# Read the current ramdisk logger's private mount, as in the resume logger.
# Match its public header and privately compare boot IDs; emit neither value.
for pid in $(timeout 2 pidof a9-mainline-logger 2>/dev/null); do
    case "$pid" in ''|*[!0-9]*) continue;; esac
    root=/proc/$pid/root/kdebuginfo/mainline
    [ -r "$root/counter" ] || continue
    IFS= read -r boot <"$root/counter" || continue
    case "$boot" in ''|*[!0-9]*) continue;; esac
    dir=$(printf '%s/boot-%010d' "$root" "$boot")
    [ -r "$dir/info.txt" ] || continue
    header=$(head -n 1 "$dir/info.txt")
    [ "$header" = "A9_MAINLINE_LOG boot=$(printf 'boot-%010d' "$boot") pid=$pid" ] || continue
    bid=$(cat /proc/sys/kernel/random/boot_id) || continue
    grep -qx "boot_id=$bid" "$dir/info.txt" || continue
    kernel_opened=0; logcat_opened=0
    for file in "$dir/kernel.log.prev" "$dir/kernel.log" "$dir/logcat.log.prev" "$dir/logcat.log"; do
        [ -r "$file" ] || continue
        if timeout 5 sed -n 's/^.*\(A9_FBE_[A-Z]* .*\)$/\1/p' "$file" 2>/dev/null; then
            case "$file" in *kernel.log*) kernel_opened=1;; *) logcat_opened=1;; esac
        fi
    done
    printf 'A9_FBE_READ kernel_opened=%s logcat_opened=%s\n' "$kernel_opened" "$logcat_opened"
    if [ "$kernel_opened" = 1 ] || [ "$logcat_opened" = 1 ]; then
        printf 'A9_FBE_SOURCE source=1\n'
        break
    fi
done
timeout 5 dmesg 2>/dev/null | sed -n 's/^.*\(A9_FBE_[A-Z]* .*\)$/\1/p' || :
timeout 5 logcat -b all -d -v raw -s A9FBE:I '*:S' 2>/dev/null |
    sed -n 's/^.*\(A9_FBE_[A-Z]* .*\)$/\1/p' || :
km=0; [ "$(getprop ro.boot.keymaster)" != 1 ] || km=1
case "$(getprop ro.boot.verifiedbootstate)" in green) vb=1;; yellow) vb=2;; orange) vb=3;; red) vb=4;; *) vb=0;; esac
case "$(getprop ro.boot.flash.locked)" in 0) fl=0;; 1) fl=1;; *) fl=2;; esac
case "$(getprop ro.boot.vbmeta.device_state)" in unlocked) dl=0;; locked) dl=1;; *) dl=2;; esac
printf 'A9_FBE_BOOT keymaster_loaded=%s verified_state=%s flash_locked=%s device_locked=%s\n' "$km" "$vb" "$fl" "$dl"
rc=$(getprop vendor.a9.trace.data_rc)
case "$rc" in ''|*[!0-9]*) rc=-1;; esac
ready=0; [ "$(getprop vendor.a9.data.ready)" != 1 ] || ready=1
printf 'A9_FBE_STATE data_rc=%s data_ready=%s ' "$rc" "$ready"
awk '$2=="/data" {if ($3=="f2fs") f++; if ($3=="tmpfs") r++}
END {printf "data_f2fs=%d data_tmpfs=%d\n", f+0, r+0}' /proc/mounts
'''


def records(text):
    """Never retain native/helper output except the fixed numeric protocol."""
    found = []
    for line in text.replace('\r', '').splitlines():
        if any(pattern.fullmatch(line) for pattern in (KEY, CONFIG, LOCAL, BOOT, STATE, SOURCE, READ, MOUNT)) and line not in found:
            found.append(line)
    return found


def saved_boot(boot, output):
    """Recover whitelisted records after b-try has pulled the retained boot.

    Keep this separate from the initial hook snapshot: a sweep can reboot,
    and post-return logs may belong to a different boot than its initial STATE.
    No keys, properties, boot IDs or raw log lines enter either output.
    """
    selected = []
    for name in ('kernel.log.prev', 'kernel.log', 'logcat.log.prev', 'logcat.log'):
        path = boot/name
        if not path.is_file() or path.is_symlink():
            continue
        with path.open(errors='replace') as source:
            for line in source:
                # Android prefixes vary between kmsg and saved logcat. Locate
                # the public marker, then require the whole suffix to match.
                start = line.find('A9_FBE_')
                if start < 0: continue
                suffix = line[start:].strip('\r\n')
                if not any(p.fullmatch(suffix) for p in (KEY, CONFIG, LOCAL)): continue
                if suffix not in selected: selected.append(suffix)
    keys = sum(KEY.fullmatch(s) is not None for s in selected)
    configs = sum(CONFIG.fullmatch(s) is not None for s in selected)
    selected.append(f'A9_FBE_SAVED key_records={keys} config_records={configs}')
    output.write_text('\n'.join(selected)+'\n'); output.chmod(0o600)
    print(selected[-1])
    return 0


def capture(out):
    try:
        result = sp.run([sys.executable, '-B', str(HERE.parent/'boot-deadline/onb-root.py')],
                        input=CAPTURE, text=True, errors='replace', stdout=sp.PIPE, stderr=sp.DEVNULL, timeout=80)
        rc, text = result.returncode, result.stdout
    except sp.TimeoutExpired:
        rc, text = 124, ''
    except OSError:
        rc, text = 127, ''
    selected = records(text)
    count = sum(KEY.fullmatch(line) is not None for line in selected)
    selected.append(f'A9_FBE_CAPTURE capture_rc={rc} key_records={count}')
    output = '\n'.join(selected)+'\n'
    path = out/'fbe-evidence.log'
    path.write_text(output); path.chmod(0o600)
    sys.stdout.write(output); sys.stdout.flush()
    # A missing record remains explicit evidence of absence, never crypto PASS.
    return int(rc != 0)


def main():
    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(description='Offline numeric FBE extraction; no device access')
        parser.add_argument('--saved-boot', type=Path, required=True)
        parser.add_argument('--out', type=Path, required=True)
        args = parser.parse_args()
        return saved_boot(args.saved_boot, args.out)
    if (os.environ.get('A9_BTRY_IN_HOOK') != '1' or not os.environ.get('ADB')
            or not os.environ.get('A9_BTRY_LOGS')):
        print('A9_FBE_CONTEXT owning_hook=0')
        return 2
    try:
        return capture(Path(os.environ['A9_BTRY_LOGS']))
    except OSError:
        print('A9_FBE_CAPTURE capture_rc=125 key_records=0')
        return 1


if __name__ == '__main__': sys.exit(main())
