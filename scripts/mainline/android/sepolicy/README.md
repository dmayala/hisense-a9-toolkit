# B4 enforcing round9 — radio link control and AOD setup

[R9 commands/pass list](ROUND9.md) supersede R8. R8 phone qualification confirms
audit rate0/backlog8192, both camera JPEGs, Codec2 paths, microphone capture and
working named-domain settings replies. R9 adds radio interface-control socket
permissions and AOD input-service lookup. Setup now prints prefs metadata before
its first command and after editing. Session stage/STOP messages also go to
logcat, since the supplied R8 captures did not retain native controller lines.
Pinned radio controller/tool files, admission and factory-MAC check stay intact.

# B4 enforcing round8 — main6cebb58 and primary audit switch

[R8 commands/pass list](ROUND8.md) supersede R7. The base is now the full
camera-preview vendor, preserving the new RGBA HAL/minigbm. R8 adds observed
radio QRTR/MPSS/strace permissions and Binder replies to inherited console FDs.
R7's qualification arguments were in ignored extra_cmdline. R8 preserves the
unrelated primary arguments and puts enforcing=1 plus androidboot.a9audit=1 in
the primary field. A static helper in the existing auditctl enforcing domain
sets rate0/backlog8192 and logs AUDIT_GET readback to kmsg. It changes no daemon
PID, enabled state, rules or loss counters; normal builds have no automatic
audit action. All functional domains remain enforcing. Phone testing pending.

# B4 enforcing round7 — observed R6 failures (superseded audit packing)

[R7 build, qualification commands and pass list](ROUND7.md) supersede R6.
The feature payload/base is unchanged. R7 covers the radio launch/read-only
seed/umount denials, libcamera IPA user/network namespaces, framework replies
to the AOD command pipe, ART/prefs traversal and manual audio's DAC capability.
Exact vendor service-status properties now have a dedicated read-only type for
shell diagnostics. `--qualification-audit` opts into a boot flag, 8192-record
backlog, the existing confined auditctl `-r 0` and a `secilc -D` binary. The init
actions reapply after the GSI's boot-completion rate reset without writing a
persistent property. Normal builds retain the default audit behavior. Only the
already authorized root console is permissive; all functional domains enforce.

# B4 enforcing round6 — current main6e2d925

[R6 instructions and pass list](ROUND6.md) supersede the stale-j round5 image.
The base is the tested cam-aod-surf vendor (today's audio modules, camera FPS,
AOD DPU, Codec2 RGBA, GNSS time, GLES), plus the unchanged m boot with enforcing
header flags. `source-round6.cil` adds the standalone allocator's targeted init
noatsecure/private-library policy, enforcing manual audio/camcap/key-AOD domains,
GNSS command-pipe and observed Wi-Fi metadata permissions. Existing RAM-only
format/fsck permissions are retained. The new GLES
service maps to a9ml_gpu. No functional service becomes permissive; only the
existing coordinator root console remains exempt. Host-only qualification does
not establish an enforcing runtime pass. Fresh cas AVCs are a rate-limited lower
bound, and many vectors are already covered by rounds3–5.

# B4 enforcing round 5 — Wi-Fi launcher environment, 2026-10-20

`source-round5.cil` preserves init's configured `LD_LIBRARY_PATH` on its three
framework Wi-Fi HAL/supplicant/hostapd transitions. Each service starts a shell
wrapper in its HAL domain, then execs the native binary in that same domain.
AT_SECURE would otherwise strip the variable before the wrapper can pass it on.
Camera and Codec2 already have their init noatsecure rules in `avc-h.cil`.

The [A6L modem lead](https://github.com/PaddleStroke/hisense-a6l-lineageos/blob/main/docs/port-status.md)
does not directly explain A9's offline MPSS: our confined rmtfs, tqftpserv,
pd-mapper and diag-router are static, with no interpreter/DT_NEEDED or private
library environment. The session/controller also uses no LD_LIBRARY_PATH.
No radio-domain exception, sandbox/UID change or physical-store grant is added.
The existing E Ink gpu_device directory-search rule already covers that A6L
lead; its mirror's composer-fd sharing rule is not a demonstrated A9 failure.
The existing service inventory is retained; no new service-check harness is
introduced. This is a source-supported launcher fix, not proof MPSS or camera
enumeration recovered. Coordinator qualification remains required.

# B4 enforcing round 4 — complete AVC capture, 2026-10-20

Round 3 booted enforcing and passed MTP, raw camera, Venus, BT and sensors.
`avc-enf3.cil` addresses the retained full capture. The current candidate uses
integrated j, including its working permissive Camera2 JPEG and USB fixes.

The camera module-loader shell runs as vendor_init; its vendor-toolbox `tr`
command was denied execute_no_trans, so set -e could stop module preparation
before the provider starts. That command is now allowed. The capture also
shows init successfully starting camera/logd and later reporting them already
running, with no captured exits. Their missing ordinary-shell getprop values
are private init-property reads, not proof those processes failed. The policy
allows status reads and targeted camera/Codec2 lshal queries; the permissive
root diagnostic channel remains available for full inspection.

Launcher/GMS native crashes are in EGL display initialization; Mesa's DRM
enumeration hits denied uevent reads. App/framework reads are now permitted
on the dedicated DRM metadata type, including the faux-bus vkms and SM6115 GPU
device uevents. Generic sysfs reads are not granted to all apps. This is the
supported dependency fix; crash recovery needs a phone result.

Wi-Fi gets missing mnt_vendor traversal/mountpoint access and the observed
toybox mount cleanup LOOP_CLR_FD probe (0x4c01); no physical block write or
discard is added. Sensor registry RAM-file rename, MPSS status enumeration,
GNSS RAM-log operations, system_suspend status reads, E Ink metadata/fault
reads and the observed composer ioctl are covered. ttyGS nodes now get their
existing tty-device rules rather than falling back to generic device.
Root-domain permissive AVCs, unrelated hwservice probes, and phhsu physical
block ROSET/discard are not turned into extra grants. GNSS/radio still need
the real Wi-Fi/MPSS session; no SIM/SV state is fabricated.

Host checks establish compilation/labels only. Qualify boot, Launcher/GMS
crash count, firmware/E Ink, Wi-Fi serving/MPSS, GNSS/radio, camera/JPEG,
Codec2 and logd using the coordinator's existing procedure. Capture AVCs
before USB changes. Only a9ml_rootsh remains intentionally permissive;
default integrated builds stay permissive.

# B4 enforcing round 3 — service recovery, 2026-10-20

Round 2 now has a coordinator-confirmed enforcing boot_completed, BT ON,
5 sensors, vibration, Venus decoding and battery. Its remaining feature
failures have no retained AVC export, so `source-round3.cil` supplies the
missing permissions from the actual service sources. The next run must export
AVCs before testing USB. Host linking is not evidence those features recovered.

The coordinator explicitly permits the existing root test tool to remain
permissive-equivalent. **Only a9ml_rootsh is now a permissive domain**; the
kernel and functional service domains stay enforcing. This lets firmware
installation, media tools and lshal diagnostics use the existing loopback root
channel without changing their labels or making ordinary shell permissive.
Private compiler copies still remove the GSI's three permissive domains. This
supersedes the zero-permissive-input statement in the historical round-1 section.

Explicit media/video/v4l-subdev contexts cover multi-digit device names;
camera and Codec2 receive V4L2/media ioctls and camera DMA-heap access. Canonical
MPSS sysfs has its own type, with controller-only writes and shell status reads;
ADSP enumeration remains read-only for the Wi-Fi controller. The session gets
its missing tmpfs/root-detach, read-only physical seed and persisted-file reads.
Physical block writes are not added, and the native seccomp/UID drop and all
existing session admissions remain unchanged. GNSS/radio depend on that MPSS
session; no fabricated SIM or SV state is added.

USB rules cover the native gadget core's configfs unlink/link/write sequence,
FunctionFS endpoint checks/watches, UDC soft_connect and monitor eventfd.
No controller algorithm, timeout or ADB retention behavior changes. The logd
overlay adds post-fs-data and boot-completed fallback triggers, each requiring
the system logd to be stopped, so an early missed property event cannot leave
the existing vendor fallback unstarted. The exact cause of the reported empty
service state remains unproven without a new capture.

The incremental enforcing stage copies raw ext4 with host sparse-file support
to avoid allocating all free blocks on the shared low-disk host. Its byte
format stays raw ext4 and every installed payload/label is still read back.
Default integrated builds remain permissive. The same matching-GSI selection
and development-policy qualification below apply.

# B4 enforcing round 2 — integrated h, 2026-10-20

The h-enf capture boots the kernel and keeps system_server alive, but the
framework boot-completed path waits in `UsbService$Lifecycle.onBootPhase` on
its asynchronous initialization future. The captured USB handoff is still
`sys.usb.configfs=1`: the existing helper cannot read `sys.usb.ffs.ready` or
`sys.usb.configfs`, and therefore cannot advance to 2 to start the declared
gadget HAL. `avc-h.cil` restores those property reads and legacy android_usb
state access. This is the supported boot-blocker diagnosis from the captured
wait, property state and source dependency; a new phone boot must confirm it.
The system_server/zygote additions are read-only boot/diagnostic resources.

Camera and Codec2 already package the reported missing libraries in private
directories and set `LD_LIBRARY_PATH` in their root-owned init definitions.
SELinux's SID transition sets AT_SECURE when noatsecure is denied, and bionic
then ignores that environment variable. The baseline dontaudit hides those
two denials. The round-2 policy permits noatsecure only for init's transitions
to hal_camera_default and mediacodec, preserving these two configured paths.
Private library directories have dedicated vendor labels and read/map/execute
rules for the corresponding native HAL, plus an exact Codec2 executable label.
No namespace-wide or application-domain permission is added. See
[bionic linker_main.cpp](https://android.googlesource.com/platform/bionic/+/refs/heads/main/linker/linker_main.cpp)
and the cached kernel's security/selinux/hooks.c secureexec calculation.

Other selected fixes cover thermal battery reads and existing helper DAC,
sensor and module-loader operations. Captured crash_dump ptrace to keystore/vold,
generic default hwservice lookup, shell readiness-property setters and physical
block ioctls are deliberately not granted without a functional requirement.
The opt-in flag, matching-GSI requirements and permissive default below remain.
These corrections are host-built; boot completion and HAL recovery need the
coordinator's next enforcing test.

# B4 opt-in enforcing candidate — integrated f, 2026-10-20

Status: **host-built, linked and inode-labelled; enforcing phone boot and
functional parity remain unverified**. The earlier draft below is retained as
historical context; this section supersedes its image-integration limitations.

```sh
scripts/mainline/build-integrated.sh --enforcing --audio --cellular \
  --kernel-build ~/A9-build/mainline/integration-build-20261020/suspend \
  --out ~/A9-build/mainline/integrated-enforcing-20261020
```

Default builds stay permissive. An existing integrated pair can be supplied
with `--base-build DIR`; omit feature rebuilds with the existing `--without`
flags, retaining the base payloads. The enforcing stage runs last, after all
feature overlays. No built-in kernel rebuild is needed: it changes only the
header cmdline to `enforcing=1 selinux_force_permissive=0
androidboot.selinux=enforcing`, preserving every segment and the snapshot.
The force-permissive patch in R4 accepts0 and then honours ordinary enforcement.

`avc-f.cil` contains fixed source/AVC-derived rules for the working f composition
(371 distinct capture vectors), including RAM formatting, kernel firmware/node
management, QRTR HALs, module loaders, sensors/FastRPC, USB/configfs, graphics,
thermal/health and helper logs/properties. It does not blindly grant every
captured denial: diagnostic enumeration and failed attempts to create sysfs
files are not feature requirements. Wi-Fi's existing process-ownership scan
gets process metadata reads without cross-domain process ptrace. Physical
block writes are not added; writable block access in new helper rules is scoped
to ram_device. The development root console remains a privileged, explicit
a9ml_rootsh domain for the existing coordinator commands, with Binder control
and RAM tooling. This is a POC policy, not a release confinement claim.

The loader also gives the video starter its own domain and rewrites every
functional vendor service formerly using phhsu_daemon into a named domain.
Native HAL domains and service triggers/argv/UIDs/capability lists are retained.
A real libselinux regex lookup assigns every vendor inode's security.selinux
attribute; copying file_contexts alone would leave old xattrs active. Module,
firmware and helper labels are read back. File-context patterns use explicit
/vendor stems so old generic vendor patterns do not outrank them.

The supplied GSI declares phhsu_daemon, tkcore and ueventd permissive. For this
opt-in candidate the compiler uses private copies of the matching GSI CIL,
removes those declarations, and supplies precompiled_sepolicy plus all three
matching platform/system_ext/product hash sidecars. The shared policy cache and
system image are unchanged. **The cache must match the actual GSI**; select it
with --enforcing-platform-cache. A different system hash causes Android to
compile its original split policy, whose inherited permissive declarations
would return. Check active domains/policy as well as getenforce on the phone.

Linking uses Android init's existing `secilc -m -M true -G -N -c 30` path. The
inherited GSI/CF policy and current system-shell/property bridge do not pass
strict Treble neverallows. The compiled input has no permissive declarations;
-N does not disable runtime enforcement. Host legacy libsepol3.8 inspection
cannot validate Android16's newer policy capabilities, so the authoritative
host link here uses the cached Android compiler. No CTS/release compliance or
hardware pass is claimed.

Coordinator qualification: within the existing exclusive slot-B owner, boot the
new matching pair/snapshot and preserve private radio handling. Check
getenforce=Enforcing and /sys/fs/selinux/enforce=1, verify the A9 service process
labels (no functional service in a permissive domain), then normal E Ink/Wi-Fi,
BT, sensors, GNSS, vibrator, USB and camera/Codec2 registration. Keep the existing
recovery owner and dead-man; no automatic retries or audio playback. Capture
new AVCs privately and sanitize them with sanitize-avc.py. Newly separated
source domains can expose permissions hidden by the original phhsu grants;
return the first failed service and sanitized vectors rather than declaring
this image qualified because it links.

---

# B4 SELinux first pass — 2026-10-20

Status: **static draft; permissive collection next; enforcing parity unproved**.
No phone access was used to prepare it. Branch `fix/enforcing`, base `07b9875`.
The coordinator owns all phone commands below. Do not flip enforcing on the
current vendor merely because this policy links.

`a9ml.cil` and `integrated.cil` reuse the 2026-10-19 enforcing-integrated source
(with its original evidence annotations). `services.cil` adds current-service
rules inferred from source. No new domain is permissive. The old permissive
`a9ml_debug` policy and the old runner/harness are deliberately not imported.
This first pass keeps the current scripts, startup triggers, module order,
firmware bridge, Wi-Fi manifest hashes and shutdown code intact.

## Collect now on the current permissive image

Save raw logs locally, mode 0600, and keep them with the coordinator. They may
contain credentials, addresses, identifiers and locations. Share only the
sanitizer's JSON with this worker. The commands use the existing root channel;
run them from the coordinator's already-owned phone session. No reboot, log
clear, service restart or setenforce is part of collection.

```sh
cd /home/user/A9-wt/fix-enforcing
umask 077
capture=/home/user/A9-build/mainline/enforcing-20261020/avc-current
mkdir -p "$capture"
printf 'getenforce; cat /sys/fs/selinux/enforce\n' |
  adb shell 'timeout 15 nc 127.0.0.1 2324' > "$capture/mode.txt"
printf 'dmesg | grep -E "avc:|SELinux:"\n' |
  adb shell 'timeout 20 nc 127.0.0.1 2324' > "$capture/dmesg-avc.txt"
printf 'logcat -b all -d -v threadtime\n' |
  adb shell 'timeout 30 nc 127.0.0.1 2324' > "$capture/logcat-all.txt"
printf 'ps -AZ; ls -lZ /dev/dri /dev/input /dev/fastrpc-adsp /dev/qcom_rmtfs_mem1; ls -ldZ /mnt/a9-wifi /mnt/vendor/persist /data/vendor\n' |
  adb shell 'timeout 15 nc 127.0.0.1 2324' > "$capture/contexts.txt"
cat "$capture/dmesg-avc.txt" "$capture/logcat-all.txt" |
  python3 -I scripts/mainline/android/sepolicy/sanitize-avc.py > "$capture/vectors.json"
```

Repeat into a different directory after the normal integrated E Ink redraw,
Wi-Fi session/HTTP, BT enable, sensor enumeration, vibrator and GNSS exercises.
Use the coordinator's existing feature-test commands: this draft starts no
subsystem automatically and adds no separate test harness. Collect thermal and
health activity during that same session. Do not play audio for this task.
Kernel audit and logcat copies are deduplicated by vector; counts are log-line
counts, not unique events. Empty vectors do not prove absence of denials:
permissive domains and audit rate limits may hide requests. The coordinator
should report read failures or audit-loss messages separately, without raw data.

## Build a permissive collection overlay

This produces a small `overlay/`, not a complete flashable vendor image. It
reads the selected integrated vendor and cached platform policies, replaces the
vendor policy, builds `precompiled_sepolicy`, and changes init domain selection.
It does not touch either input or any live script. Run against the newest
merged vendor so concurrent parity fixes are retained:

```sh
python3 -I scripts/mainline/android/sepolicy/build-draft.py \
  --vendor-image /home/user/A9-build/mainline/vendor-main2-20261020/vendor.img \
  --platform-cache /home/user/A9-build/mainline/android/cache/gsi-sepolicy/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v8-3569860608-1792259254 \
  --out /home/user/A9-build/mainline/enforcing-20261020/draft-next
```

The platform cache must match the actual system image. The current command
uses the existing cached LOS v8 policy selected by the earlier enforcing work;
there is no claim that it matches any future system build. Runtime compilation
uses Android init's `secilc -m -M true -G -N -c 30`. The resulting binary replaces
an old precompiled policy as well as updating CIL; otherwise unchanged platform
hash sidecars could select the old vendor policy. Inherited platform hash
sidecars are retained: a platform hash mismatch makes init use split-policy
compilation. `compile-command.json` records every compiler input.

**Image integration:** merge the overlay into a staging copy of the current
integrated vendor and repack using `pack-vendor.sh` with its merged
`vendor_file_contexts`, preserving the integrated fs_config and file owners.
The ordinary `build-vendor.sh --wifi` cannot recreate this integrated variant.
`--refresh-integrated --overlay` copies policy/init bytes but preserves existing
file xattrs; by itself it does **not** apply the new helper, module and firmware
labels. Do not call that a fully labelled enforcing image. No candidate image
is supplied in this first delivery. The coordinator can collect current-image
AVCs immediately, then choose the existing staging/repack path for the next
permissive collection with the new domains.

Keep the present permissive boot for collection. Capture again after booting
the labelled draft: source contexts must be `a9ml_*` / `a9_wifi_radio` /
`hal_*_default`, rather than `phhsu_daemon`. This separates each helper's
requests. Existing kernel forced-permissive mode makes this possible without
adding permissive policy domains.

## Static service/script coverage

The image-specific `service-inventory.json` lists every extracted vendor init
service and its old/new domain. The first tested source image has 30 services;
B1 vibrator is covered below but absent from that image. Native APEX services
keep the Cuttlefish platform/vendor HAL policy already shipped in the image.

| Service/script | Domain and source-based resources | Remaining evidence/work |
|---|---|---|
| `a9ml-eink.sh` setup/open/watch/close/drmdebug-off | `a9ml_eink`; modules, DRM, specific E Ink/gate/parameter sysfs, firmware reads, scheduling, properties, fault dmesg | Current all-process scheduling scan differs from the older narrowed helper; do not grant ptrace across all Android domains. Verify sysfs canonical labels and fault-log writes. |
| `a9-eink-compat` | `a9ml_compat`; app socket, E Ink sysfs, frontlight, legacy framework properties | Existing core-property bridge violates Treble neverallows. Optional `settings`/`service` execution remains denied. |
| `a9ml-gpu.sh` setup/select | `a9ml_gpu`; firmware, DRM, DMA heap, module parameters, EGL properties | Current script sets core EGL properties directly; move these setters to init as the earlier enforcing implementation did. Retired GPU-guard services are not present in the selected image. |
| `a9ml-bluetooth.sh`, `a9-bt-identity` | `a9ml_bt`; firmware bridge, module load, HCI management socket/ioctl, private vendor identity read | Confirm integrated bridge traversal labels and logd access; no identity is read or emitted here. |
| BT HCI_USER HAL | `hal_bluetooth_default`; existing HAL policy + HCI ioctl 0x48d3 | Fresh BT enable AVCs. |
| `a9ml-wifi-firmware.sh` | `a9ml_fw`; firmware symlinks, configs and parameter reads only | Current display target remains `/data/local/tmp/a9fw`; no module/mount/radio capability granted to this domain. |
| Wi-Fi session/controller, module loader and native sandbox/servers | `a9_wifi_radio`; RAM mounts, native/tool execution, same-domain tracing, QRTR/netlink, RAM rmtfs device, module load | Physical-store **read** target labels, mountpoint/remoteproc sysfs, securityfs and native sandbox transitions need AVCs. No physical block-device write grant is added. Current system-shell and shell-data paths are cross-Treble collection exceptions. |
| Wi-Fi HAL/supplicant/hostapd launcher | Existing three Wi-Fi HAL domains; current shell wrapper entrypoint + property reads | Collect against installed launchers, not ad hoc root invocation. Binder/netlink/socket rules remain inherited HAL policy. |
| `a9-sensors.sh`, `s1-listener` | `a9ml_sensors`; RAM registry/tmpfs, verified firmware, module load, remoteproc, chroot/UID drop and fastrpc listener | Canonical ADSP sysfs/fastrpc/mount labels await AVCs. Generic sysfs writes and arbitrary device ioctls are not granted. |
| Sensors HAL | `hal_sensors_default`; existing binder service, QRTR create/bind/read/write, sensor-ready props | Enumerate and stream motion/proximity; binder publication alone is insufficient. |
| Thermal HAL | `hal_thermal_default`; read-only thermal/IIO/sysfs traversal | Canonical per-zone labels need measurement; no thermal writes added. |
| Health HAL | `hal_health_default`; existing binder policy and PMI632 power-supply labels reused from prior work | Validate real battery/USB paths and system_suspend wakeup labels. |
| B1 vibrator HAL (separate worker) | `hal_vibrator_default`; input enumeration, EVIOCGNAME, EVIOCSFF, EVIOCRMFF and EV_FF writes | Needs merged B1 executable and actual evdev labels; module load remains init's action. |
| GNSS AIDL + `a9-gnss-loc` | `hal_gnss_default`; binder, child pipes, UID/capability drop, QRTR | Needs MPSS supplied by current Wi-Fi session; test location start/stop, retaining raw location logs privately. |
| `a9ml-deadman.sh` | `a9ml_deadman`; current hold file read, uptime/cmdline, beacon, powerctl and sysrq fallback | Fresh autonomous expiry proof required before relying on it under enforcing. No expiry is run by this worker. |
| `a9ml-usb.sh`, inherited `a9ml-ctl.sh` | `a9ml_usb`; UDC discovery, USB fixup, ACM/NCM, route netlink and diagnostics | Confinement incomplete: configfs, tty, logd, controller properties and command transport need AVCs. |
| Development root shell + settings housekeeping | `a9ml_rootsh`; loopback listener and current shell entrypoint | Confinement intentionally incomplete. Arbitrary root tooling is not a release feature; no blanket device/binder grants. |
| Hardware init / `vendor_init` | Existing vendor_init plus specific gates, beacon, RAM format ioctls and custom property setters | Current key-copy, tmpfs metadata flag, APEX-selection defaults and housekeeping carry earlier known denials. Keep coordinator recovery operational while fixing these. |
| BoringSSL self tests; composer/allocator, keymint/gatekeeper/authgraph, USB, memtrack, audio, authsecret, stub power and other retained APEX HALs | Existing executable transitions and Cuttlefish policy unchanged | Included baseline is retained; current boot AVCs determine any mainline-specific additions. Stub HALs are not hardware parity claims. |

## What blocks enforcing qualification

The draft links with `-N`; a strict compile fails. Inherited baseline policy
already violates neverallows; the draft also has deliberate current-shell,
shell-data and legacy-property exceptions. No release compliance is claimed.
No auto-generated audit2allow policy is used.

The GSI's `system_ext_sepolicy.cil` itself declares `phhsu_daemon`, `tkcore`, and
`ueventd` permissive. A vendor-only overlay cannot remove those declarations.
The draft makes **no A9 service use them** and adds no vendor permissive domain.
A literal zero-permissive-domains release requires changing that system policy
as well; it is not solved by checking only `/sys/fs/selinux/enforce`.

After AVC-driven fixes and a labelled permissive functional pass, the coordinator
can prepare the enforcing boot. The existing 0014 kernel patch is opt-in:
remove `selinux_force_permissive=1` (or set it to 0), remove `enforcing=0`, and
set `enforcing=1` in the boot header command line. Preserve every other boot
input. No kernel code or rebuild is inherently required. Do not rely on
`setenforce 1` while the forced-permissive flag is set: that patch ignores it.

Qualification then needs `getenforce=Enforcing`, enforce=1, no functional
service in a permissive domain, the installed-domain identities, normal
E Ink+Wi-Fi+BT+sensors+vibrator+GNSS+thermal+health exercises, and the existing
return/restore path working. This worker supplies no hardware pass claim.
