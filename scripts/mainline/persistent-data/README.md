# Persistent /data POC (round 7)

Host-built candidate only. **No current partition has been established as unused.**
The coordinator decides whether to perform the reads and writes below. First
qualification is on the existing permissive integrated j pair, slot B. Nothing
in this directory flashes, formats or opens a phone device.

The design mounts a separate, preformatted ext4 filesystem as `/data` in B's
vendor fstab. Daily A keeps its existing encrypted `userdata` and metadata.
The filesystem starts empty, as the RAM data does today. Accounts, settings and
files subsequently created on B should survive B reboots. There is no encryption
in this POC; private test data remains on the candidate until it is restored.

## Candidate and evidence

`scripts/partition-map.txt` gives this range (units are 4096 bytes):

| Field | LUN4 `last_parti` |
| --- | --- |
| Start | 274658 sectors = 1124999168 bytes |
| Length | 773913 sectors = 3169947648 bytes |
| GPT entry | **59**, from the stock GPT template |
| Image length | 2816 MiB = 2952790016 bytes |
| Remaining partition tail | 217157632 bytes, left untouched by image installation |

The stored stock `HLTE556N/rawprogram4.xml` and
`rawprogram4_001360E1.xml` assign this entry an empty payload filename and zero
programmed sectors. `gpt_main4.bin` identifies entry59/start274658;
its end is an unpatched zero-length placeholder. `patch4.xml` expands that
entry to the LUN's final usable sector. The map supplies the measured length,
**not that template**. Counting lines in the abbreviated map gives the wrong
partition number. These files are host reference evidence, not a live GPT read.

This suggests a spare, but does not prove current use or stock runtime behavior.
Only consider it after the coordinator confirms the exact live range, a wholly
zero backup and absence of users. A nonzero payload or different geometry stops
this plan; do not erase something because its filesystem is unrecognized.
Other reserved/log partitions have firmware purposes. No GPT change, partition
resize, `super` allocation or shared `userdata` use is proposed.

There are multiple partitions named `last_parti` on different LUNs. **Never use
the global `by-name/last_parti` symlink.** Linux disk letters must be measured.
The mainline SD controller is disabled in
`android/sm6115-hisense-hlte556n-android.dts` (GPIO88 reserved); a microSD-backed
file would require separate DT/pin work. An image file on RAM data would not
persist. Those are not implemented alternatives here.

## Host build

From this worktree, using local e2fsprogs:

```sh
python3 -I scripts/mainline/persistent-data/build.py \
  --out /home/user/A9-build/mainline/persistent-data-20261020/image
```

Outputs: sparse **raw ext4** `data.img` (not Android sparse format), its hash,
`build.json`, a deliberately incomplete `fstab.hlte556n.in`, and build/fsck/root
metadata/marker-readback logs. The root has mode0771, system:system and
`system_data_root_file`; Android's existing post-fs-data creates and labels its
normal subdirectories. The image has a 64 MiB journal and a public marker
`/data/a9-persistent-data-poc.txt`. There is no copied RAM data or device identity.
The 2816 MiB capacity may limit apps; free space is a phone acceptance check.

The fstab keeps the existing system/vendor logical mounts, replacing only the
RAM data row. The new row has `wait,latemount`, without `formattable`, `check`,
`discard`, encryption, a metadata entry or a misc entry. A missing/bad target
must fail the mount; it must not silently initialize a partition or fall back
to RAM and appear to pass persistence. Recovery from a failed mount uses the
previous vendor image and the coordinator's existing slot-B recovery path.

## Coordinator: establish a target before any write

Keep the current RAM-data B image for this stage. Use the coordinator's existing
root transport; the following are **phone root-shell commands**, not host paths.
For the example `sdd59`, verify all of:

```sh
readlink -f /sys/class/block/sdd/device
cat /sys/class/block/sdd/queue/logical_block_size
cat /sys/class/block/sdd59/partition
cat /sys/class/block/sdd59/start
cat /sys/class/block/sdd59/size
sed -n 's/^PARTNAME=//p' /sys/class/block/sdd59/uevent
ls -A /sys/class/block/sdd59/holders
awk '$2 == "/data" || $1 == "/dev/block/sdd59" { print $1, $2, $3 }' /proc/mounts
```

Require parent SCSI **LUN4** (the last component of the SCSI address is `:4`),
logical sector4096, partition59, name `last_parti`, start**2197264** and
size**6191304** in sysfs's **512-byte** units. No holders, no candidate mount,
and `/data` still on RAM are required. Check live GPT and the coordinator's
stock/current fstab and device-mapper inventories for the same exact range and
absence of any consumer, including daily A. Do not print whole uevents, GPT
UUIDs, mount inventories or device identifiers into the report. If the Linux
letter differs, substitute the positively identified disk throughout.

Read the **entire 3169947648-byte partition** through that root transport to a
host backup, preferably streaming through zstd to avoid consuming 3 GiB of the
tight local disk. Check the source/transport/compressor return statuses, exact
decompressed length, and hash. Require the decompressed bytes to be wholly
zero, compared with a same-length zero stream. Retain and verify that backup
before writing. Zero contents plus no users and the stock no-payload recipe
are the proposed evidence for the coordinator's spare decision; unknown stock
use remains a reason to decline. The worker has not performed these checks.

## Coordinator: prepare and install if the spare is accepted

After verification, generate a concrete overlay on the host (example only):

```sh
python3 -I scripts/mainline/persistent-data/build.py \
  --overlay-only --block-device /dev/block/sdd59 \
  --out /home/user/A9-build/mainline/persistent-data-20261020/selected
bash scripts/mainline/android/build-vendor.sh --refresh-integrated \
  --base-vendor /path/to/the/current-j/vendor.img \
  --overlay /home/user/A9-build/mainline/persistent-data-20261020/selected/vendor-overlay \
  --out /home/user/A9-build/mainline/persistent-data-20261020/vendor
```

The chosen node in an overlay is a coordinator input, not evidence of a verified
target. Use the existing vendor refresh's readback/fsck and inspect its changed
file list. Keep the original j vendor hash/path and matching boot/snapshot.
No new boot image or DT is needed. Do not reapply the original RAM-data fstab
over this overlay in a subsequent rebuild.

The proposed phone writes are exactly:

1. While on RAM-data B and with candidate unmounted, stream `image/data.img`
   from the host into the verified candidate node. Root-shell sink, after the
   verified device is substituted: `dd of=/dev/block/sdd59 bs=1048576` followed
   by `sync`. The input must be binary transport without a terminal or shell
   framing; check sender and sink statuses. This writes the first **2952790016
   bytes only**, not a whole UFS LUN and not a GPT. Do not stage the image under
   phone `/data` (it is RAM). Read back precisely that prefix (2816 blocks of
   1048576 bytes) and require its SHA256 to equal `image/data.img.sha256`.
   The remaining tail must match the original backup.
2. Install the new **vendor_b only** through the coordinator's existing logical
   partition write/readback procedure. Retain the same known j boot_b and
   snapshot; use the existing boot/keeper/restore owner. Do not write vendor_a,
   boot_a, userdata, metadata, persist, modem*, fsg, misc, GPT or recovery.
3. Subsequent B filesystem activity writes within the candidate. The default
   RAM image is unchanged in source; the candidate overlay is strictly opt-in.

## Coordinator: two-boot acceptance and undo

On the first B boot require boot completion, `/data` mounted ext4 from the exact
candidate (not ram0), no mount/fs errors, and adequate `df -k /data` space.
Read `/data/a9-persistent-data-poc.txt`; create
`/data/local/tmp/a9-persistence-proof` containing a public test string and `sync`.
Set one harmless Settings preference. Reboot B through the same owned path,
**without rewriting data.img** or formatting anything. Require a new boot,
the same candidate mount, the proof file/content and preference preserved, and
normal E Ink/UI/Wi-Fi. In both boots use the coordinator's existing radio/session
setup; persistence alone does not resolve any scripts that assume fresh RAM data.
Daily A must still boot its own original userdata, with its original apps/data.
No persistence or parity-row29 pass is claimed before this actual result.

To undo, first restore the previous j **vendor_b** and boot B with RAM data;
confirm the candidate is unmounted and has no holders. Restore the **entire
original 3169947648-byte backup** to that same verified candidate, sync, and
read back/hash the full range against the backup. The raw backup is complete
even though the image install touched only a prefix. Restore via the same
binary root transport; never a `by-name/last_parti` alias. Returning to daily A
uses the existing coordinator path. Reverting vendor alone stops using the
candidate but leaves test data there; full undo includes restoring its backup.

No independent review agents or phone rehearsals were run: this deliverable is
a host filesystem image and plan, with no new phone-side executable/harness.
Mount behavior, actual spare status, SELinux enforcing and persistence remain
unqualified until the coordinator's device work.
