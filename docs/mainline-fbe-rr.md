# FBE-RR host findings, 2026-10-24

Host only: no device access. Source rebased onto main `1dfc181` before changes.
Evidence directory: `/home/user/A9-build/mainline/fbe-rr-20261024`.
Raw input logs are private; numeric/known-public matches alone were exported.

The coordinator's INTEG15 FORMAT=1 boot and plain B reboot establish persistent
B-generated hardware-wrapped keys. A-generated keys failing earlier does not
contradict that result. Both saved boots fail RPMB registration at SCM transport
phase4 with -22; neither reaches phase5/6. This is before RPMB listener I/O,
not a shared-buffer binding failure or evidence that RPMB reads succeeded.

Stock registration uses SMC `0x32000206`, arginfo0x83, listener ID0x2000 and
buffer0x6400. Its fallback to legacy `0x32000201` occurs only on transport -EIO.
Our driver forced that legacy command, which returns -EINVAL on the measured
firmware. The fix restores stock command selection. Blocked calls use command7
and resp_type's session ID in the newer mode, with stock's command4/app-ID
fallback on transport error. Receive/reply, transaction serialization and the
immutable UFS read-only guard remain in force. Registration success on hardware
is untested; the secure firmware's internal rejection reason is unavailable.

REA evidence:

- qseecomd main: `ev_d7dbe901a59da80cdc12e2259c4683f3d0fc272985f98303b26fe686ed2a9885`.
  It ignores each start_listener return during startup, then sets the readiness
  property. That property does not prove RPMB registration.
- start_listener: `ev_c1e3ebd60b14e283b7c611062516a803a76046a7aa70133e20da9decfa56b9e9`.
  RPMB init and start are separate calls; init0 is not a running listener.
- librpmb rpmb_start: `ev_c777624ffbb76a5aa5e191194360b2a80abd232450fe0419e508ed76d26a5287`.
  It registers before starting dispatch, with a 0x6400 shared buffer, flags0.
- librpmb dispatch: `ev_bef6b1dd71df3dc936392f5d724403e7a7668f431bfbacd27d9325dde30b6bdc`.
  Reads and writes are separate callbacks; registration itself does not prove
  either. Retained in `librpmb.rea.json`.
- Stock kernel: `ev_94141f65ce977c08bbb20b579ea09cb85bcfd4b893dea9cd71e660b5d2a86e5a`,
  retained in `stock-kernel-rea.json`. The focused ELF's incomplete call-target
  coverage truncates its pseudocode. The complete retained disassembly
  `storage-row29-wrapped-study-20261021/stock-qseecom.dis` at
  `0xffffff80087b6090..60d0` supplies the command6, -EIO comparison and command1
  fallback instructions; matching downstream source in
  `integ-20261023-fp3/evidence/drivers_misc_qseecom.c` lines700–728 corroborates
  them. Static evidence does not establish runtime command6 success.

The mount helper's status1 is benign for the already successful mount, but is
a real housekeeping failure. On the second boot its final restorecon is denied
`getattr` on system_data_file; mkdir/chown also encounter write denial. These
operations duplicate the existing init boot action's persistent ADB setup.
Readiness was published before them, so Android proceeds. Init's following
SIGKILL is normal process-group cleanup after the helper has exited, not the
75-second service timeout. The helper took 4.354 seconds on the fresh boot and
1.751 seconds on reuse. The fix removes that duplicate success-path credential
copy/relabel and leaves init as owner. Failure-only diagnostic capture no longer
scans logcat after successful vold IPC. All actual mount/readiness checks stay.

The real-wrapped boot marker runs in `on boot`, after post-fs-data and
early-boot, not at mount completion. Fresh boot vold rc0 is at 10:11:09.046;
helper exit is at 10:11:10.049; boot marker is at 10:12:03.166. Reuse vold rc0
is at 10:29:57.341; helper exit is at 10:29:58.233; marker is at 10:30:49.383.
Thus about 51–53 seconds after the helper is outside FBE mount IPC. Major
serial contributors recorded by init are:

| Service | Fresh boot | Reuse boot |
| --- | ---: | ---: |
| rw-system.sh, before mount helper | 10.635 s | 10.433 s |
| bpfloader | 13.902 s | 14.374 s |
| a9ml_eink_setup | 20.009 s | 18.977 s |

MSM insertion itself takes about0.43 seconds; a9_eink insertion finishes about
2.3 seconds into E Ink setup. Much of that setup cost follows module insertion.
Reducing vold's 60-second timeout cannot save these already completed seconds.
This patch saves the unnecessary successful log scan and failing duplicate
setup. Further large savings need bpfloader/E Ink setup profiling; it does not
skip BPF initialization, reorder compositor startup or lower display waits.

Validation: five production SCM helper groups pass natively and compile for
ARM64; the actual modified QSEE driver compiles with the retained kernel's
native ARM64 flags. Five actual Android mksh fake mount/vold/property groups
pass, including fallback and partial-mount refusal. Existing UFS policy files
are unchanged. No independent hardware qualification is claimed.

Next phone test is coordinator-owned: one FORMAT=1 B boot, registration through
phase6, authenticated RPMB read completion and TA validation, wrapped mount and
boot completion, plus helper exit0 and timing. Preserve all private logs. RR
generation can still fall back because this read-first build denies every RPMB
programming request. Never turn failed KMTB reads into table creation. A
FORMAT=0 test of the earlier B-keyed state is unavailable after the later A
format. No phone command or image build was run by this worker.
