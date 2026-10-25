# Physical persist: read-only mount and per-open SFS diagnostics

The previous mainline path did not mount physical persist for qseecomd. It bound
the writable `/metadata/a9-tee-persist` directory over `/mnt/vendor/persist`
and labeled that isolated namespace tee_data_file. This follow-up replaces that
bind with the user-authorized physical read-only view. Old metadata-store files
are preserved in place; they are not copied, merged, deleted or relabeled.

## Mount and labels

After mounting real metadata, init waits for a separate bounded30s worker before
requesting qseecomd/Keymaster startup. The worker:

- Requires the persist block node, with no stacked or preexisting mount of that
  device and no nonempty underlying mountpoint. If the mountpoint is absent,
  it creates an empty directory only after proving `/mnt` is tmpfs with no
  intervening mount or symlink. This directory is prepared in RAM before
  attaching physical persist, not on its filesystem.
- Sets and verifies the block-device read-only flag. Android Toybox's setro and
  getro commands open the node O_RDONLY; no raw block write permission is added.
- Mounts `/dev/block/by-name/persist` at `/mnt/vendor/persist` as ext4 with
  **`ro,noload,nosuid,nodev,noexec`**, then verifies VFS flags, ext4 superblock RO,
  displayed noload/norecovery and the block RO flag again.

There is no writable fallback, overlay, repair, journal replay or context mount
override. No mkdir/chown/chmod/restorecon runs on physical persist. The block RO
flag stays set throughout the boot; the worker never clears it. The GSI's later
legacy `/persist` bind alias therefore also reaches the read-only superblock.
The kernel documents why ro alone does not prevent journal writes, and why
[ro,noload is required](https://www.kernel.org/doc/html/latest/admin-guide/ext4.html#options).
An unreplayed dirty filesystem may expose inconsistent state; no repair follows.

Existing inode xattrs are preserved. Root mnt_vendor_file is already known;
the missing stock vendor_persist_data_file type is declared with only TEE
directory search/getattr/open/read and file getattr/open/read. Directory access
is required by libdrmfs's parent opendir checks. No stock write/create grants,
vendor_persist_type attribute family or unlabeled fallback is imported. Unknown
labels remain denied. File-context expectations now describe the stock root/data
types, but no restorecon is performed on the filesystem.

The worker adds only BLKROSET0x125d/BLKROGET0x125e to rootsh's block ioctl grants.
Bundled platform policy already grants domain/dev_type0x1268/0x1272, and rootsh
is a domain while block_device is dev_type. The new bits extend the existing
0x12 ioctl bitmap; they do not remove previously granted ioctls. Base block
write permission is unchanged and not added.

```
A9_PERSIST_RO phase=3 rc=0
```

This success marker means the worker verified its mount namespace. Failure
records use phase1 for preflight/block RO, phase2 for mount, phase3 for final
verification; rc is command exit status, not errno. Records go to kmsg and
direct logcat, with the existing bridge also retaining the numeric kernel form.
`exec_start` waits but does not gate subsequent service starts on exit0. A failed
worker leaves no factory mount, or a block-RO mounted view that failed final
qualification. Require the success marker before claiming verified exposure;
SFS open results must establish the listener's actual access.

## Write/create failures

### INTEG24 sensor and microphone regression

Boot `boot-0000000125` in `sweep-integ24-f1-20261024` confirms the physical
mount succeeded: `kernel.log:906` has `A9_PERSIST_RO phase=3 rc=0` at8.88s.
At31.38s (`kernel.log:1267`), the sensor starter exits with
`a9-sensors: state fail:real-persist-mounted`. Its old global mount-device
veto fired before staging the private registry or starting the listener.
This is a startup refusal, not an observed registry EROFS or SELinux denial.
The retained logcat files show no sensor registry or ACDB write attempt.

The mainline path already has the necessary writable copy. `a9-sensors.sh`
unpacks the hash-pinned stock registry seed from vendor into private tmpfs at
`/mnt/vendor/a9-sensors` and stages the listener's root there. The broker maps
DSP `/mnt/vendor/persist/sensors/...` requests to that RAM root;
`s1-registry-fs.c` checks tmpfs/device identity and uses openat2 beneath it.
The fix removes only the unrelated physical-persist mount veto. It retains
the checks on mounts at/below the private registry, payload hashes, the test
data gate, broker confinement and ADSP ownership. Incremental `build14.py`
now refreshes the sensor script from current source, with installed-byte and
metadata readback, rather than inheriting the old baseline script.

Audio startup is downstream: `audio/output/init.a9-audio-output.rc` starts
`vendor.a9-audio-load` only when `vendor.a9.sensors.ready=1` and audio ready=0.
That loader registers the ALSA card/modules and sets audio ready; the sensor
refusal prevents this trigger. This explains the blocked microphone path and
the restart row's sensor/audio readiness failures even when ADSP later runs.
The integrated AOSP AIDL/tinyalsa adapter does not load the stock ACDB stack.
Microphone samples and restart health still require the next owned device run.

Stock comparison (saved text only, no device access):

- `vibration-f0-20261020/stock-init/init/vendor.sensors.sscrpcd.rc` starts
  `/vendor/bin/sscrpcd sensorspd` as system in `early_hal`.
- `android/ref/vendor_selinux/vendor_file_contexts:419` labels
  `/mnt/vendor/persist/sensors` as `vendor_persist_sensors_file`; stock
  `vendor_sepolicy.cil:2728-2729,3450-3451,4706-4707` grants registry
  write/create/rename/unlink operations to the RPC/sensor domains.
- Stock contexts:400 labels persist/audio as `vendor_persist_audio_file`.
  Stock audio HAL rules:3052-3054 grant reads there. `init.qcom.rc:288-293`
  creates writable audio and ACDB delta directories under `/data/vendor/audio`,
  not persist. Stock policy permission is not evidence of an attempted write
  in this mainline boot.

Host checks in `integ24-persist-sensors-host/` pass sensor setup with physical
RO persist plus `/persist` alias, a RAM registry update with unchanged factory
canary/mount entries, the historical metadata TEE bind, and existing startup
and supervision failures. The real broker syscall test on private tmpfs also
passes. No physical persist overlay, permission or SFS/RPMB change is needed.

### SFS write/create failures

The kernel RO mount and block flag enforce refusal; no userspace success/error
spoof or retry layer is added. Stock libdrmfs `mkdir_h`0x106d6c returns immediately
when mkdir fails with anything except EEXIST. `dirExists`0x106ed4 performs bounded
allocation/opendir/close. `file_open`0x1065f8 propagates open or parent-creation
failure; `gpfile_readwrite_helper`0x1070cc and dispatch0x105b80 return failures
through the existing response path. These paths contain no EROFS retry loop.
REA ev_1d0b063d33db97d35b9099596713c073dac5b514aa522c467dc59d98b34bf77c,
ev_575bb646d79b8c291cef9c74595581fae09cf3b127cc672644805ca248dcb6c9,
plus the earlier [listener path evidence](mainline-qsee-sfs-paths.md).

Writes may return EROFS30 or an earlier SELinux/DAC EACCES13. An intended read
that actually requests O_RDWR can also fail; do not reopen RW or broaden policy
to accommodate it. The mount does not establish factory-object validity,
authenticated RPMB reads or attestation success. KMTB writes and RPMB programming
remain forbidden while reads fail; the existing UFS RPMB guard is unchanged.

## Next-build diagnostics

The qseecomd-only preload now attempts one logcat WARN record for every in-scope
outer open/openat/creat call, replacing capped A9_ATTEST_FS emission:

```
A9_SFS_OPEN root=1 op=1 access=0 create=0 errno=0 result=1
A9_SFS_OPEN root=1 op=1 access=1 create=1 errno=30 result=0
A9_SFS_OPEN root=2 op=2 access=0 create=0 errno=2 result=0
```

Root1 covers `/mnt/vendor/persist` and `/persist`; root2 covers
`/data/vendor/tzstorage`. Op1/2/3 means open/openat/creat, access is O_ACCMODE,
create identifies creat/O_CREAT/O_TMPFILE, errno0 means success and result is a
success boolean. No pathname, basename, FD, identity or contents are logged.
Repeated slash separators are handled lexically for classification, including
the stock GP rewrite's `/mnt/vendor//persist/...`; the actual call is unchanged.

Arguments, mode, dirfd, result and post-call errno remain intact; no extra
filesystem lookup, retry, denial or redirect occurs. There is no artificial SFS
event cap or private queue/file. Existing liblog/ring retention is unacknowledged.
Relative, overlong, dot/dotdot and EFAULT paths are excluded. A preceding
mkdir/opendir failure can prevent an open and is not an observed open event.

The offline extractor retains repeated SFS outcomes from logcat rather than
deduplicating identical calls; kernel mirrors are not a second SFS source.
Rotated-log overlap cannot establish event uniqueness. Historical ATTEST_FS
parsing remains available for older boots. The new library SHA256 is
6430df2974c8e25fb4cacc0fee95b56cd21250c11e26f5f426274c09f4e7d86e.

## Host validation and build intake

- A private host mount namespace used a fresh synthetic32MiB ext4 image and
  read-only loop device. Reads and a stock-style inode label survived; write,
  O_RDWR, create, mkdir, relabel and bind-alias create all returned EROFS30 in
  at most0.000283s. Before/after image SHA256 matched; mounts/loop were cleaned.
- Actual Android mksh control flow passes success, missing RAM mountpoint, occupied mount, setro failure,
  mount failure and missing-option cases; inherited init transformation/order,
  idempotence and host_init_verifier pass. These use mocked command interfaces.
- Current combined policy compiles with no diagnostics. Android Toybox traces
  confirm O_RDONLY node opens for both RO ioctls.
- Native and Android ARM64 observer tests pass, including320 EROFS calls producing
  320 records without retry/cap, aliases, mode/errno preservation and packaging.
- The bridge/extractor pass numeric-schema agreement, duplicate-call retention
  and64 actual ARM64 datagrams with delayed logd/backpressure.

Artifacts: `A9-build/mainline/fbe-persist-ro-20261024/` (shell-tests-final,
real-ext4-test, policy, bridge-tests, blockdev traces, libdrmfs-ro-error-REA.json),
and `fbe-sfs-open-ro-host/tests/`. All tests use synthetic storage; no phone,
physical persist or factory-key content was accessed. The independent review
accepts this scope; it does not claim the listener shares the qualified namespace
or that the original attestation/RPMB failure is repaired.

Build from latest main with the normal builder. The changed wrapped-start script
is pinned by the existing FBE trace test, so regenerate that small host artifact
before assembly. Credentials preparation installs the matching script and RC
together, including standalone use. Already frozen images retain their source
identity; the new behavior applies to builds consuming these commits.
