# FRP and foreground scheduling parity (integ23 boot123)

Two independent missing configurations are repaired for the next integrated
image. The changes are host-tested; no phone operation or real FRP read/write
was performed. Hardware service publication and scheduling remain to be checked
in the coordinator's next owned boot.

## Persistent data block

Stock `android/ref/vendor_a.img` has
`ro.frp.pst=/dev/block/bootdevice/by-name/frp` (`build.prop:49`), FRP ownership
`0600 system system` (`ueventd.rc:348`), and `frp_block_device` on the old
`soc/4804000.ufshc` path (`vendor_file_contexts:450`). Its `fstab.default` and
`fstab.emmc` contain no FRP mount row: this is a raw block, not a filesystem.
The early diskless mainline builder deliberately strips `ro.frp.pst`
(`android/build-vendor.sh:312,426`); the real-data integrated image inherited
that omission. SystemServer starts PersistentDataBlockService only when the
property is nonempty. Integ23 `logcat.log:24642–24676` shows the missing service
through PackageInstallerSession and `am_wtf`; LockSettingsStorage also consumes
this same service for FRP checks.

The integrated builder now restores the stock property, reuses the existing
bootdevice/by-name discovery, waits for FRP during `fs`, and adds ueventd rules
and SELinux contexts for global, legacy-alias and mainline `soc@0` names.
No fstab mount, format, erase or partition-content operation is added to our
scripts. The raw node matches stock: **`0600 system system`**, labelled
`frp_block_device`. System_server therefore retains normal
PersistentDataBlockService reads and writes, including FRP credential updates
when a PIN is set or cleared. Framework checksum repair/migration behavior is
unchanged; our boot, provisioning and sweep scripts never wipe or repair FRP.

The stock vendor's compiled policy and the current mainline compiled policy
both allow system_server `read`, `write`, `open`, `getattr`, `ioctl`, `lock`,
`append` and `map` on `frp_block_device`; both deny `create`, `setattr` and
`unlink`. No new allow/permissive rule or capability is needed. Ueventd assigns
ownership and label when creating the real node; vendor_init gains no raw FRP
access. Host tests compare these decisions against both binary policies and
verify that upgrading the earlier read-only ueventd rules restores stock mode.

Sources: [SystemServer](https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/android-16.0.0_r1/services/java/com/android/server/SystemServer.java),
[PersistentDataBlockService startup/checksum/format](https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/android-16.0.0_r1/services/core/java/com/android/server/pdb/PersistentDataBlockService.java).

## Foreground scheduling

Integ23 `logcat.log.prev:600` reports the unavailable schedtune controller;
`logcat.log:8611–8616` shows missing `/dev/stune/top-app/cgroup.procs` and
`AddTidToCgroup` EACCES after opening other control files. `first_api_level=30`
selects `/system/etc/task_profiles/task_profiles_30.json`, replacing modern CPU
profiles with schedtune definitions. No vendor override was installed.

The new vendor profiles override all eleven API30 compatibility definitions
with current AOSP CPU/uclamp profiles. Normal/system, foreground, top-app,
background, camera, NNAPI and dex2oat use their actual cpuctl groups. CPUSET and
scheduler aggregate profiles retain AOSP membership and timing actions. The
upstream-missing `cpu.uclamp.latency_sensitive` hint is optional. The vendor
controller descriptors keep CPU, cpuset and blkio ownership explicit. Init
sets `0664 system system` on membership files, normal/top-app shares1024 and
background/system-background/dex2oat shares52, with uclamp min0/maxmax. No new
CPU-frequency boost, core mask or realtime allocation is introduced. The
optional inherited API30 schedtune mount may still log an unavailable-controller
warning; no active performance profile depends on it and no fake control files
are created.

The write EACCES also needs a kernel correction: mainline cgroup-v1 permits
cross-UID migration only for root. Android common 6.6 additionally permits
`ns_capable(tcred->user_ns, CAP_SYS_NICE)`. Patch0064 ports exactly that condition.
AOSP SystemServer uses SYS_NICE and the existing policy permits it. DAC, SELinux, UID checks
for unprivileged callers and namespace capability checks remain. No new
capability or special UID1000 bypass is granted. The incremental kernel recipe
recompiles `kernel/cgroup/cgroup-v1.o`; image assembly requires its current patch
hash, so a vendor-only rebuild cannot silently omit the fix. Exported types and
kernel configuration are unchanged; the normal full-link export audit remains.

Sources: [AOSP profile load order and actions](https://raw.githubusercontent.com/aosp-mirror/platform_system_core/android-16.0.0_r1/libprocessgroup/task_profiles.cpp),
[Android common 6.6 permission predicate](https://android.googlesource.com/kernel/common/+/refs/heads/android15-6.6/kernel/cgroup/cgroup-v1.c).

## Host validation

Run `python3 -B scripts/mainline/android/boot-parity/check-host.py --out NEW_DIR`.
It compiles the full patched ARM64 cgroup object using retained native flags
and private output/source aliases, then runs four focused groups: effective
API30/vendor profile resolution and group permissions; actual kernel predicate
(root/self/SYS_NICE/unauthorized/wrong namespace, with old code failing the
regression); real libselinux labels and existing-policy decisions plus stock read/write
FRP configuration; real ext4 payload installation/metadata and init verifier.
Only a synthetic 16MiB filesystem is written, never an integrated image or FRP.
The host policy query uses the existing bit12 compatibility adapter on a private
copy; shipped policy is untouched. No real cgroup/mount or target entry runs.

Evidence for the FRP read/write correction:
`A9-build/mainline/frp-stock-rw-host-20261024/`, including
`checks.json`, `tests.log` and `stock-policy.log`. The correction runs the two
FRP and installation/init groups; unchanged cgroup tests and ARM64 compile evidence remains in `frp-cgroup-host-20261024/ready/`. The next integrated
image must rebuild its kernel and vendor from this commit. Neither actual
PersistentDataBlockService publication nor UI smoothness is claimed from host
checks.
