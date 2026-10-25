# PLAIN INTEG READY

Audio update 2026-10-22: FIX3 speaker ringtone confirmed "clear as day".
The package recipe now builds/stages the current audio closure, including
tinyalsa/Q6 recovery fixes and the pending standby-pop correction. It accepts
--out and --reuse-boot for a new vendor without overwriting this original pair.
See [audio follow-up](audio/output/STOP-AND-JACK.md) for the delivered merged
vendor and one-command attended earpiece/jack check. Historical original
composition and hashes follow.

1. Proven R22c kernel lineage, VT_CONSOLE=n, quiet console, guard=0; no reliability init/logger/deadline package.
2. R22d stock USB ONLINE and generation-deduplicated USB state notifications; QSCRATCH resume restore retained.
3. R22e stock full-charge STATUS, cached SOC and suspend-safe/freezable polling without periodic wake events.
4. FastRPC interrupted-context restoration and O3 sensors/ADSP firmware bridge.
5. QSEECom R2 native Android/ION transport plus SG and legacy UFS RPMB descriptor-query ABI.
6. Row27 normal watchdog: bark 11s, bite 14s, minimum pet spacing 9360ms, watchdogd 9+5; no test dead-man.
7. P GNSS HAL with framework UTC injection and per-satellite signal diagnostics.
8. P GLES capability property, GPU/minigbm, camera and Codec2 composition.
9. P Wi-Fi, Bluetooth, cellular, audio and E Ink (same-target skip/prewarm controls present; defaults unchanged).
10. Enforcing O3-FBE row29 data, mapper fix692a9f2 and unconditional sys.usb.configfs=2 HAL handoff.

The vendor starts from the phone-proven plain-QSEE/R22e composition, which carries P's feature payloads.
The only new kernel source is the exact row27 watchdog driver; config and existing export CRCs match QSEE.
The boot's ramdisk, second, DTB, embedded recovery DTBO and both cmdline fields match R22c exactly.
No external DTBO is written; boot routing/beacon is unchanged. There is no promise of automatic A rollback.
The old vendor test dead-man is removed; watchdogd-gsi is the single owner (vendor override sets9+5 on either GSI).
Existing development root shell and test awake-lock/stayon defaults remain; no keeper/ACK/bootstrap lease is added.
No system image change: use the currently installed, already boot-proven system_b (row27 TEST companion is compatible).
This smoke checks presence and boot; it does not play audio, start a TA, exercise RPMB or qualify GNSS/optical output.

```sh
P=/home/user/A9-build/mainline; "$P/plain-b.sh" "$P/plain-integ-20261022/boot.img" "$P/plain-integ-20261022/vendor.img" && cat "$P/plain-integ-20261022/presence.sh" | adb shell -T 'timeout 60 nc 127.0.0.1 2324'
```

Return separately: /home/user/A9-build/mainline/plain-a.sh

Host build/package rc0; module exports unchanged, vendor readback/metadata/fsck rc0 and ABL boot PASS.
One packed-image smoke only; no phone operation, fake-phone suite, new harness/gates/pins/leases or review agent.
Actual combined phone smoke is agent's next operation. No running worker jobs/locks/cleanup.

boot.img SHA256 657c0fe8d947765cdeda0560f8774fb51eefe36d04803ae6b092e08eb8916577
vendor.img SHA256 8aa652cae9b511d8e5d55eba1edf79ea3f10828bd6cd2c94ab6e92de20bddc76
Image.gz SHA256 f6e5ff62ac01971e6482857561254f8845b1b9789933a3bd65c25fa06b825a5d

Free after package: 2960457728 bytes. Build recipe: scripts/mainline/plain-integ-20261022.py.
Uses the worker-owned existing incremental cache (historical directory name boot-reliability/cache); source is plain QSEE, not the parked reliability kernel.
