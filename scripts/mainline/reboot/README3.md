# 6r3: integ7 + FP7 + audio repair and acceptance corrections

Host-only delivery. No device commands, USB enumeration, playback, credentials,
key deletion, formatting or cipher substitution were performed. This candidate
does **not** establish that wrapped data or framework mic capture works.

Base: the latest main's `integ-20261023-fp7`, which itself preserves agent's
combined `integ-20261023-7`. `build3.py` preserves its complete boot/kernel/DT,
firmware, radio, fingerprint, init and policy closure. It restores the merged
FIX3 tinyalsa negotiation and Q6ASM PREPARE fixes. The two Q6 modules are freshly
built for this kernel; the old audio build's module CRCs were incompatible.
Acceptance, GNSS and fingerprint launchers select the same candidate and accept
an explicit image override. No task branch was created.

## Findings and changes

Evidence root: `/home/user/A9-build/mainline/sweep-integ6r2-2-20261023`.
The sweep measured boot80, then restarted. Only boot81 is retained in its
directory and mainline.tar; do not attribute later reboot counts to boot80.

1. **Real wrapped data remains blocked.** Boot81 `kernel.log:993–996` and
   `logcat.log.prev:1022–1036` show Keymaster begin returning -8,
   `INCOMPATIBLE_BLOCK_MODE`, followed by decrypt/read-key failure and vold25.
   There is no preceding -62 or actual upgrade request in this failure chain.
   `upgrade_keyblob_if_required_with` is a generic wrapper name, not evidence
   that patch-level upgrade failed. Both
   [Android11 KeyStorage](https://android.googlesource.com/platform/system/vold/%2B/refs/heads/android11-d1-release/KeyStorage.cpp)
   and [Lineage23.2 KeyStorage](https://github.com/LineageOS/android_system_vold/blob/lineage-23.2/KeyStorage.cpp)
   use AES-GCM to protect the storage key. Stock mounts persist before Keymaster;
   our TEE uses the pre-existing isolated metadata-backed persist directory.
   Neither comparison identifies the failing key's authorizations or justifies
   altering its mode, replacing keys, or formatting. Those characteristics and
   boot80 logs are not in the saved sweep. Row29 now FAILs on RAM fallback when
   `real_data_required=1`; intentional RAM profiles retain N/A. This corrects
   classification, **not decryption**. The previous vendor-patch change did not
   solve this error. A stock-compatible unwrap fix remains required.

2. **Video was real, but too short.** Front: 281 capture callbacks, 276 encoded
   samples, 9853ms. Rear: 273/268/9936ms. The sole failed video predicate was
   encoded duration >=10000ms; wall windows were 10104/10103ms. Record 12100ms
   to accommodate pipeline startup while retaining the genuine 10s criterion.
   320x240 was explicitly requested by our `smallest()` selector, not a measured
   HAL maximum. Request the largest advertised size up to 1280x720 and report
   the advertised maximum. Neither larger-size success nor hardware encoding
   is claimed from the old recordings.

3. **Hardware encoder is not exposed.** The installed `media_codecs_c2.xml`
   declares AVC/HEVC decoders only; SM6115's current Venus resources have no
   encoder node. `hardware_available=0` is an accurate capability result.
   Adding an encoder XML name cannot supply a driver/Codec2 implementation.
   Camera MediaRecorder can use the software AVC encoder. HW encode stays open.

4. **Audio repair was omitted from integrated images.** Integ5, 6r2 and integ7
   all contain the original tinyalsa SHA `bc39aebf…` with the old instruction at
   file0xa698, although FIX3 is merged. Negotiated ring size and SW thresholds
   therefore still use different configurations. Restore the exact checked
   one-instruction repair and rebuild the merged Q6 queue/buffer reset patch
   against integ7's kernel ABI. Gain is unchanged: the working direct-capture
   helper and adapter both select MM2/PCM1, SWR_MIC0/DEC2 and gain84 (0dB).
   Saved `vendor-test-20261020/evidence-k5-audio/mic-gain.txt:1–2` records
   144000 frames with peaks63/65 after correcting the old gain0 (-84dB) bug.
   That was direct PCM capture, rather than the current AudioRecord path.
   The sweep returned **zero samples**, rather than samples with zero amplitude.
   Probe explicitly prefers the built-in mic, and records routed device,
   empty reads and read error. The stale audio closure is a confirmed packaging
   regression; its responsibility for this AudioRecord stall requires a rerun.

5. **Wi-Fi failure phase was ambiguous.** `cap_rc=1` is also synthesized if
   wlan0 never appears; it does not prove an iw cap command failed. START queues
   an asynchronous owner. Wait for its existing serving/Android-ready release
   before framework enable, and retain device_ready/cap_attempted in failures.
   The txpower cap and original owner/fault/stop/reboot behavior are unchanged.
   Retained boot81 eventually creates wlan0 (`logcat.log:20610–20841`), but this
   does not reconstruct the missing initial-boot failure.

6. **MTP host probe was skipped.** Functions were not MTP and no matching host
   MTP device existed; mtp_rc1 was the fallback result, not a failed libmtp
   exchange. USB now verifies minimum volume, wakes and dismisses a nonsecure
   keyguard again immediately before function selection after the long hold.
   Credential locks remain intact. Keep applied-state, same-device descriptor,
   real storage-session and post-session transport proofs; reset stays refused.

7. **SELinux remains a real FAIL.** The sweep counted 80 permissive AVCs and
   one permissive domain. The retained reboot contains 1281 permissive records,
   all `a9ml_rootsh`, in the 25 groups below. Initial80 records are unavailable,
   so this is not a fabricated enumeration of that exact window. No blanket
   allow or removal of the diagnostic domain's permissive flag was applied.

8. **ANRs are real; collector failure is separate.** Boot81 logcat contains 15
   explicit `am_anr` phone-startup records (sweep reported16 on boot80).
   `com.android.phone` blocks on HIDL IRadio1.4/slot1 (e.g. lines869,2079–2081)
   until stock RIL registers at22106–22108. The existing boot-completed ->
   owner-serving -> stock preparation dependency starts radio much later than
   persistent phone startup. No telephony disable or ANR suppression is used.
   Fixing that startup dependency without a second MPSS/store owner remains open.
   The collector previously copied the entire log ring into an external printf
   argument; an actual Android-shell replay with 300KB reproduces E2BIG. Its
   rc126 differs from the saved rc2, so it is not proved the sole saved cause.
   Now stream selected event lines to private host memory, count-check and hash
   there; retain nonzero collector exits and all startup ANRs as failures.

## Retained boot81 permissive AVCs

Every source is `a9ml_rootsh`. Target types/classes and permissions, with counts:

| Target | Class | Permissions | Count |
|---|---|---|---:|
| anr_data_file | dir | getattr | 20 |
| anr_data_file | dir | open | 13 |
| anr_data_file | dir | read | 19 |
| anr_data_file | dir | search | 4 |
| anr_data_file | file | getattr | 10 |
| logcat_exec | file | getattr | 1 |
| rootfs | dir | read | 302 |
| rootfs | dir | read open | 26 |
| tombstone_data_file | dir | getattr | 32 |
| tombstone_data_file | dir | open | 32 |
| tombstone_data_file | dir | read | 32 |
| tombstone_data_file | dir | search | 771 |
| unlabeled | dir | relabelfrom | 1 |
| unlabeled | dir | search | 6 |
| unlabeled | file | relabelfrom | 1 |
| vendor_data_file | dir | relabelfrom | 1 |
| vendor_data_file | dir | relabelto | 1 |
| vendor_data_file | file | relabelfrom | 1 |
| vendor_data_file | file | relabelto | 1 |
| vold_metadata_file | dir | getattr | 1 |
| vold_metadata_file | dir | search | 2 |
| a9ml_rootsh | process | setfscreate | 1 |
| kernel | dir | search | 1 |
| kernel | file | read | 1 |
| kernel | lnk_file | read | 1 |

Machine-readable listing: `A9-build/mainline/6r3-host/permissive-avcs.json`.
An additional160 enforcing shell/logcat denials concern reading the generated
runtime event-tag file; these are separate from permissive records.

## Host validation and reproduction

Use `python3 -B scripts/mainline/reboot/build3.py --stage-only` to compile the
two source-patched Q6 modules in an isolated output, then the same command
without `--stage-only` after merging tested sources. It requires the pinned
FP7/integ7 pair, preserves inode metadata and every other regular vendor file,
checks ext4, and audits all101 modules against the final kernel/provider CRCs.
No boot/kernel/DT change is introduced. Never replace a delivered candidate.

The actual Android donor library is exercised before/after in the existing
five configuration groups, for playback **and capture**: rounded/exact periods,
two-period ring, explicit thresholds and HW_PARAMS refusal. Android mksh with
300KB log input exercises the streaming collector. Five acceptance and five
SWEEP4 fake transport groups retain mute, real-data failure, MTP reset refusal,
cleanup and restart behavior. APK build/signature and source/asset pins must
match the delivery commit. No hardware acceptance credit follows from these
host checks. Logs and final packaging receipt live beside the new candidate.
