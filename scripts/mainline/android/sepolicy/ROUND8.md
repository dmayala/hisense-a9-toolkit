# Enforcing R8 — main6cebb58

R7 phone evidence confirms both Camera2 JPEG paths, readable service states,
named-domain mic capture and retained E Ink/GLES/BT/sensors/vibrator/Codec2.
R8 targets the remaining radio, inherited Binder descriptor and audit failures.
No functional permissive domain is added. Only the previously authorized
diagnostic a9ml_rootsh remains permissive. Worker is host-only; agent owns tests.

## What changed

Base vendor is `camera-preview-20261020/vendor.img`, SHA256
8d924f61156a08141b249223ac22a78c742786d1e62d4c6d00d435a8ce4b5d4a, corresponding
to main6cebb58. Its RGBA camera HAL6b67befe and minigbm91f4d0c replace the earlier
cam-aod-surf pair; all current feature payloads remain intact. Boot input is m,
with the same kernel/ramdisk/DT. No fresh kernel build/tree.

Radio adds observed self QRTR connect for confined diag-router/tqftpserv,
MPSS directory write/access check, strace pid_max reads and pipe TIOCGWINSZ.
Existing physical seed read-only and RAM-only write rules remain. No new block
write/ioctl, identity override or admission bypass. Earlier mnt_vendor_file
read/sys_rawio vectors are absent from R7, but audit loss prevents claiming
completeness. Factory MAC comparison stays mandatory: its application occurs
later in the controller, so an earlier session abort can leave a mismatch.

R7 AOD evidence has system_server denied writing an **a9ml_rootsh** inherited
pipe and checking a passed **shell** socket's options. R7 already covered the
helper's own pipe. R8 adds the inherited reply pipe and getsockopt; no socket
create/connect/listen or extra data permission. Existing named helper/app-data
policy is retained. There is no captured a9ml_key_aod-source denial; unreadable
or absent prefs on RAM /data still needs existence/label inspection and closed
setup, rather than granting reads of all system data.

## Audit root cause and replacement

R7's primary field was500 bytes with **none** of its mode/audit flags; all
were in extra_cmdline. This ABL ignores that field, as measured in prior suspend
work. R7 enforced because removing the old permissive arguments restored kernel
defaults; it did not prove the appended arguments were delivered.

R8 keeps every unrelated primary argument and puts **enforcing=1** and short
**androidboot.a9audit=1** in the primary field (499 bytes). The old custom
selinux_force_permissive=1 is removed: its actual kernel variable is a zero-init
static bool, so omission restores false. Android's absent selinux property
already produced enforcing R6/R7 boots. No existing primary argument is spilled
to make room; overflow is an error. Original diagnostic extras stay extras.

Rather than relying on further kernel parameters, `/vendor/bin/a9ml-audit`
uses AUDIT_GET, AUDIT_SET with **only RATE_LIMIT|BACKLOG_LIMIT**, then AUDIT_GET.
It requests rate0/backlog8192, verifies both and logs before/after limits and
unchanged loss counters to kmsg. It never changes enabled/failure mode, auditd
PID, rules or resets lost counts. It works even if audit_enabled=0 suppresses
CONFIG_CHANGE records; SELinux AVC collection is separate. Receive timeout is3s.
Errors log FAILED and exit nonzero. `--status` only queries/logs; `--help` opens
no device or socket and is suitable for host QEMU smoke.

The helper runs in the existing **auditctl** enforcing domain. New vendor
entrypoint, kmsg write and AUDIT_GET nlmsg/xperm are added; AUDIT_SET was already
allowed. No functional domain gets audit_control. Service name remains
**vendor.a9ml_auditctl**, root UID with only AUDIT_CONTROL capability. Every
automatic trigger requires **ro.boot.a9audit=1**. It runs post-fs-data (and
early-init if already parsed), then after GSI logd-auditctl stops, countering
its boot-completion rate5 reset. No persistent property writes. Qualification
still compiles with -D; ordinary no-flag policy retains dontaudit and does not
automatically run the helper. Return to an ordinary policy+boot after diagnosis.

## Host rebuild

```sh
R=/home/user/A9-build/mainline/enforcing-round8-20261020
python3 -I scripts/mainline/android/sepolicy/build-enforcing.py \
  --vendor /home/user/A9-build/mainline/camera-preview-20261020/vendor.img \
  --boot /home/user/A9-build/mainline/integrated-20261020m/boot.img \
  --min-free-gib 8 --qualification-audit --out "$R"
sha256sum "$R/boot.img" "$R/vendor.img"
```

Use a fresh reproduction output directory. Default disk reserve stays15GiB;
explicit8GiB is the user-authorized shared-disk floor. Sparse copies and small
NDK static build only. Both output images are needed with the current GSI.

## Coordinator commands — worker executes none

Use the existing exclusive slot-B lifecycle, current admission CODE and original
dead-man/restore procedure. Do not just toggle mode on the previous image.

```sh
R=/home/user/A9-build/mainline/enforcing-round8-20261020
/home/user/A9-build/mainline/vendor-test-20261020/direct-b.sh \
  "$R/boot.img" "$R/vendor.img" "$CODE"
adb shell getenforce
adb shell cat /sys/fs/selinux/enforce
adb shell getprop sys.boot_completed
adb shell getprop ro.boot.a9audit
adb shell getprop init.svc.vendor.a9ml_auditctl
adb shell getprop init.svc.vendor.graphics.allocator
adb shell getprop init.svc.vendor.camera-provider-a9
adb shell getprop init.svc.vendor.a9ml_logd
adb shell ps -AZ
adb shell pidof surfaceflinger system_server
printf '%s\n' 'dmesg | grep -E "a9ml-audit:|audit_lost|audit_rate_limit|audit_backlog"' |
  adb shell 'timeout 30 nc 127.0.0.1 2324'
```

Expect ro.boot.a9audit=1; the helper's **stopped** state is normal after oneshot
exit0. Require a final `a9ml-audit: after ... rate_limit=0 backlog_limit=8192`
**after** the GSI's boot-completion reset. A flag or exit alone is not proof.
If needed in the same owned qualification session, invoke the existing oneshot
again and inspect its readback, without changing properties persistently:

```sh
printf '%s\n' 'setprop ctl.start vendor.a9ml_auditctl' |
  adb shell 'timeout 30 nc 127.0.0.1 2324'
```

Wi-Fi uses the existing session/HTTP procedure with its original admissions and
factory-MAC check. Preserve the session's native prepare/run failure reason,
not just session=failed. Do not print the MAC or seed content. Compare only
equal/not-equal after reaching the normal MAC-application stage.

Before normal E Ink opening, run named-domain AOD setup at the existing closed
setup point. If direct-b.sh has already opened the panel, run only state now
and schedule setup at that closed point next time; do not bypass its gate check.

```sh
printf '%s\n' 'ls -Zd /data/user/0/com.lmqr.ha9_comp_service/shared_prefs /data/user/0/com.lmqr.ha9_comp_service/shared_prefs/com.lmqr.ha9_comp_service_preferences.xml' |
  adb shell 'timeout 30 nc 127.0.0.1 2324'
printf '%s\n' 'runcon u:r:a9ml_key_aod:s0 /system/bin/sh -c "id -Z; exec /vendor/bin/a9ml-key-aod.sh setup"' |
  adb shell 'timeout 60 nc 127.0.0.1 2324'
printf '%s\n' 'runcon u:r:a9ml_key_aod:s0 /system/bin/sh -c "id -Z; exec /vendor/bin/a9ml-key-aod.sh state"' |
  adb shell 'timeout 30 nc 127.0.0.1 2324'
```

Retain R7's named-domain mic capture, both Camera2 JPEG and both Codec2 tests;
their exact commands are in [ROUND7](ROUND7.md). Also test Aperture's new RGBA
preview/shutter from [the current handoff](../../../../runs/camera-preview-20261020.md).
Do not clear logs as that older preview recipe suggests during this enforcing
qualification; keep initial and final full captures instead. Preserve images
privately. GNSS counters require an actual working Wi-Fi/MPSS session.

Capture before USB changes/teardown, including helper readback rather than only
AVC lines. Do not clear logs/reset audit_lost or expose raw private logs in Git:

```sh
printf '%s\n' 'dmesg | grep -E "a9ml-audit:|avc:|audit_lost|audit_rate_limit|audit_backlog|callbacks suppressed"' |
  adb shell 'timeout 30 nc 127.0.0.1 2324' > dmesg-avc-r8-private.txt
adb logcat -b all -d > logcat-r8-private.txt
cat dmesg-avc-r8-private.txt logcat-r8-private.txt |
  python3 -I scripts/mainline/android/sepolicy/sanitize-avc.py > vectors-r8.json
```

## Required enforcing pass list

| Check | Required result |
|---|---|
| Mode | Enforcing/enforce1/boot_completed1; only diagnostic console exempt |
| Audit | primary short gate arrives, final helper readback rate0/backlog8192 after boot completion; audit_lost delta retained/reported |
| Radio | session succeeds, MPSS running, factory MAC equality at proper stage, Wi-Fi connection/HTTP204; real GNSS time/SV counters when available |
| AOD | named-domain settings replies succeed; closed setup writes/reads overlay prefs, then clock/minute update and wake/redraw |
| Current preview | both Aperture RGBA previews update, fresh JPEGs, no allocation/import failures or SF restart |
| Retained parity | both Camera2 JPEG paths; named-domain mic capture; stable SF/system_server/logd, GLES196610, E Ink redraw, BT ON, five sensors, vibration/battery; Codec2 Surface60queued/60callbacks/EOS/sameSFpid and ByteBuffer60/60 |
| Evidence | four service states truthful/readable; first failed feature's full chain and sanitized AVC vectors; missing/lost evidence stays pending |

Host checks prove packing, policy linking, labels and payload preservation, not
phone success. No functional permissive relaxation to complete a pass row.
