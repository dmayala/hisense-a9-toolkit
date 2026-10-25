# Integ19 rear-video readback repair

Host-only work from main7b12f77. No phone commands, image build, live-sweep
changes, camera driver/HAL changes, encoder implementation or logger changes.

## Evidence and cause boundary

`A9-build/mainline/sweep-integ19-f1-20261024/acceptance.json` and
`onb.log:26,29` retain:

| Measurement | Front | Rear |
|---|---:|---:|
| Software encoder selected |1|1|
| Encoder EOS observed |1|1|
| Encoded samples |263|184|
| Camera callback frames |262|184|
| Wall-clock recording window, ms |12162|12181|
| Parsed duration, ms |11921|absent|
| Demuxed video samples |263|absent|
| Final camera stage |5|4|
| Completion / error kind |1 / absent|0 /2|

The same `Probe.camera()` method handles both facings; only camera selection,
supported recording size and delivered frames differ. No audio track is
created for either facing: this is intentional silent video, not a missing
audio-track setup. Software selection still requires a named software AVC
encoder with a surface input; no hardware fallback is added.

EOS and encoded-sample evidence is saved after the entire drain loop. Every
accepted rear sample had already passed `writeSampleData`; the result does
not support blaming an earlier rejected sample timestamp or failure to add a
video track. The next operations stop/release the encoder/muxer, open the
MP4 with MediaMetadataRetriever, parse its duration string, and only then
inspect its samples. Stage4 covers all those operations. Integ19 did not
retain an exception line/stack or the temporary MP4. Its saved logcat ends
before the probe, and its tar is truncated. A bounded search of available
`runs/` Markdown found no additional integ19 record. Thus the exact historical
throwing call and MP4 validity cannot be established from retained evidence.

There is a concrete unchecked readback defect:
`Long.parseLong(metadata.extractMetadata(METADATA_KEY_DURATION))` assumes an
optional metadata string is present and numeric. A null value reproducibly
throws NumberFormatException, which the probe records as IllegalArgumentException
(`error_kind=2`). Retriever data-source failure is another possible kind2
path. The front's populated duration does not validate that assumption for
the rear. Neither possibility is relabeled as a recovered stack trace.

## Repair

Remove the optional retriever dependency and inspect the finalized MP4 with
the already-used MediaExtractor. Require a real AVC track and actual nonempty
samples. Count samples only when their demuxed timestamp and size exist;
the former do/while counted one sample even for an empty track.

Use the track's positive duration in microseconds. If absent, null or
nonpositive, use `max(sample PTS)-min(sample PTS)` from that MP4. This span
does not include an assumed final-frame duration, an absolute timestamp
origin, the recording wall clock or invented/clamped timestamps. Presentation
reordering is allowed. A positive short track duration remains short even
if the sample span or wall clock is longer. No video, no samples, unreadable
container or unmeasurable duration still fails. The host's existing >=10000ms,
EOS/software/sample/frame requirements are unchanged.

Numeric evidence now separates recording1, EOS/drain2, encoder/muxer
finalization3, extractor open4, sample scan5, duration read6 and done7 in
`video_phase`. `muxer_stopped=1` proves stop/release completed;
`video_tracks`, `sample_span_ms` and `duration_source` (1=track,2=PTS span)
describe actual readback. Dimensions and file size are retained before
readback, and the current exception-line diagnostic remains. Temporary
videos are deleted through the existing finally path; no media is exported.

## Host validation and coordinator consumption

`python3 -I scripts/mainline/acceptance/test-recording.py`: five groups PASS.
The actual Java inspection method is compiled unchanged into the existing
JVM-replay style, with fake Android media responses. Cases reproduce the
old null-duration exception; compare263-sample front and184-sample rear;
check absent/null/zero durations, absolute/reordered PTS, missing/empty video,
unreadable sources, short duration and resource release. These are algorithm
checks, not a real Android extractor/container or hardware replay.

The five existing `test-sweep17.py` acceptance groups also PASS. The offline
asset builder compiles the complete modified Probe against SDK36 with min
API30, builds/signs all three APKs, and verifies signatures. All three APK
alignments, seven asset hashes, seven source hashes and diff checks PASS.
Artifacts: `A9-build/mainline/rearvideo-host-20261024/`, including the rebuilt
`assets/assets.json`. Proprietary firmware and APKs are not committed.

The coordinator must consume the rebuilt asset bundle after merging and
qualify both facings on its next owned run. If finalization/container parsing
still fails, retain video_phase/error_line and the numeric readback evidence
before attributing a timestamp, codec or muxer defect. A runtime rear-video
PASS is not established by host checks. **REARVIDEO HOST FIX DONE.**
