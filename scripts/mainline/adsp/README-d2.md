# D2 offline preparation and attended earpiece experiment

Read `runs/adsp-d2-prep.md`. This is a new stage over the measured D1; D1 files
remain unchanged. No build or init path invokes the tone. Live admission stays
PENDING until the coordinator independently reviews the exact images, module
closure, helper, target, manifest and fallback ownership.

The internal earpiece is the only selected endpoint. The custom A9 WCD profile
exposes only the EAR route, uses the three stock supplies, omits micbias/MBHC/
capture and other analog outputs, validates EFUSE, and supplies a latched reset
and fault path. Both SoundWire controllers remain needed for playback/CSR access.
The custom controller match reads HW0 before selecting an existing supported
layout; it refuses unrecognized versions. A refusal consumes the attempt.

The native helper has no variable card/PCM/format/level/frequency/duration input:
mono S16_LE48kHz,1000Hz,48000frames, peak1036,5ms fades. It uses480-frame periods,
checks the exact mixer ABI, refuses other routing users, issues one START and one
nonblocking DRAIN, checks submitted/hardware frame counts, then DROP/close and
unroute. Host and target require physical attendance; software success does not
mean the user heard a tone. The1.5s kernel PA work deadline is not hard real-time.

## Reproduce without a phone

All outputs belong under `~/A9-build/mainline/adsp`; firmware remains private.
The build uses only local sources/caches. Freeze committed kernel inputs first.
The build requires19GB free and monitors the floor live, stopping owned build
processes at16GB. Unchanged source files share input inodes read-only; every
source write is detached first. All object/image outputs are private.

```sh
export NIX_CONFIG='substitute = false
substituters =
builders ='
OUT=$HOME/A9-build/mainline/adsp/d2-20261017
export D2_DISK_LOG=$HOME/A9-build/mainline/adsp/d2-build-disk.jsonl
nix-shell --option substitute false --option substituters '' --option builders '' \
  scripts/mainline/kernel/shell.nix --run "python3 -I scripts/mainline/adsp/build-d2.py '$OUT' --jobs 8"
python3 -I scripts/mainline/adsp/build-tone.py --out "$OUT" \
  --rehearsal /tmp/d2-tone-rehearsal.json
# Firmware staging reads the golden image; it never writes it.
python3 -I scripts/mainline/adsp/stage-firmware.py \
  "$OUT/firmware/qcom/sm6115/hisense/hlte556n" --mcopy /path/to/mcopy
nix-shell --option substitute false --option substituters '' --option builders '' \
  scripts/mainline/kernel/shell.nix --run "python3 -I scripts/mainline/adsp/freeze-d2.py '$OUT'"
python3 -I scripts/mainline/adsp/verify-packaged.py "$OUT"
```

Before the service audit, bind the selected system image. The current D2 target
is v8, SHA256 `14525f4852a3c56f816a6de15fd45b93bdf390503b5c8e01b47b2ff6331ef793`.
The producer freshly walks every directory and reads every inode/file through
read-only debugfs, verifies the complete supplied manifest, and reuses only
verified identical private reference bytes by hardlink. It requires a fresh
output child and never modifies reference file contents.

```sh
SYS=$HOME/A9-build/mainline/adsp/d2-system-v8-reproduction
python3 -I scripts/mainline/adsp/prepare-system-d2.py \
  --image "$HOME/A9-build/roms/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v8.img" \
  --sha256 14525f4852a3c56f816a6de15fd45b93bdf390503b5c8e01b47b2ff6331ef793 \
  --manifest "$HOME/A9-build/roms/a9-los23.2-dozeoff-gapps-20260603-hwcguard-v8.img.manifest.tsv" \
  --reference "$HOME/A9-build/mainline/adsp/d0-20261017/audit/system-image" \
  --reference-audit "$HOME/A9-build/mainline/adsp/d1-20261017/audit/final/services.json" \
  --output "$SYS"
ln -s "$SYS/system-image.json" "$SYS/system-files.json" \
  "$SYS/system-image.manifest.tsv" "$OUT/"
```

Run the complete service audit using `--system "$SYS/system-image"` and
the selected vendor/ramdisk; bind every finding by hash in dispositions; run
`admit-services-d2.py`, then `make-bundle-d2.py --services <audit>`. Read back the
packed boot/vendor and run ABL boot+recovery checks and `android-boot.sh --offline`
on the actual images. Check the vendor policy against v8 with `A9_GSI_IMG` set
to the pinned image. The bundle now requires its audit to match the image-bound
system inventory exactly; its live hashes select v8 and refuse v7. No runtime
version fallback is provided. The report records the exact paths and hashes.
The generated `admission.pending.json` deliberately cannot execute the harness.

Rehearse the actual target with the phone's own mksh/Toybox under qemu-user,
and the host with an executable named `fake-adb`. Never replace the fake with a
real transport in these tests. Native helper tests mock the ioctl/sysfs effects
and also run as a static AArch64 binary under qemu. See the report for final
commands, coverage and receipts; tests alone do not establish hardware behavior.

## Later attended trial

The coordinator owns one frozen tuple, fresh boot/attempt ledgers, the reviewed
root transport, live staging checks and the fallback/restore plan. The receipt
must bind D1 PASS, exact manifest and images, attendance, fixed earpiece path,
tone tuple and independent review. No reconnect or retry is allowed. Explicit
`d2-host.py --stop` requests the original target guardian; it never dispatches a
new playback operation. Stop before start leaves a persistent veto. Ambiguous
pending kernel operations remain containment-unverified and require physical
Power/PON2 fallback; killing a host/phone process is not verified containment.

Keep the jack empty. The user listens near the top earpiece with the phone away
from the ear, expects one quiet short beep then silence, and reports location,
audibility and any unexpected sound. Silence does not permit another attempt.
An ES9218 jack test is a separate alternative plan: the coordinator first asks
whether wired off-ear equipment is available. It is never selected automatically.
