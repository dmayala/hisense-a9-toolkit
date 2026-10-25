# Encoder port for the next integrated phone check

The user requested proceeding beyond the [inventory audit](ENCODER-BLOCKER.md).
This work restores a testable encoder path using the accepted stock firmware;
host compilation is distinct from a successful hardware encode.

`git log -S 'It cannot encode'` identifies `1320ab8`: the public6.0.55
authentication failure motivated a conservative decoder-only SM6115 resource
while the signed6.0.39 decoder was brought up. It records no attempted A9
encoder session with that payload. `git log -S 'builder.encoder'` identifies
`ca634ca`, which packaged a decoder-only Codec2 store. Those exclusions are
being removed for an owned phone check; the upstream old-firmware warning
remains a compatibility risk, not an observed A9 encoder error.

The SM6115 resource now creates both dynamic children with the existing HFI4
AR50_LITE implementation. The QCM2290 minimum6.0.55, A9 minimum6.0.39, firmware
bytes, PAS authentication, DT resources and working decoder EOS quirk are
unchanged. The isolated host build compiles all three Venus modules and checks
that the actual `sm6115_res` ELF relocations contain both `video-decoder` and
`video-encoder`. Creating a node does not prove firmware session compatibility.

Stock package XML declares AVC/HEVC encoders at128x128–1920x1088, up to60Mbps,
and the conservative base file has244800 macroblocks/s. The new provider will
stay within those limits and expose no secure encoder or10-bit input. Stock
`libOmxVenc.so` REA at `venc_dev::venc_open` confirms a separate downstream
encoder device, NV12 input, H264/HEVC capture formats and V4L2 format/buffer
queries. It does not establish the kernel-to-firmware HFI packet sequence.
Evidence `ev_7561b5156425bbdc1d426c88d870e420ec65cc1fd6da3413b24f2260b7e80f5b`
is static Ghidra decompilation of SHA256
`6d7847173f2652a253d5bb0fe4392e693da59d4dc90c8164ac7631e2b007be42`.

Codec2 patch0011 restores device/profile-gated AVC and HEVC names using the
existing HIDL service. HEVC Main is limited to8-bit input; the exposed stock
envelope is128x128–1920x1088,60Mbps and244800 macroblocks/s, with one instance.
Encoder input is normalized through the existing format converter even when
already NV12, so its stride/chroma offset match Venus's coded allocation.
Compressed output uses V4L2 MMAP and a checked copy into client linear blocks;
it no longer imports the client's linear allocator into Venus. HEVC gets
profile/header controls and VPS/SPS/PPS CSD extraction; AVC CSD stays covered.

The complete service, codec and plugin compile/link against the matching GSI.
Packaged QEMU link and three production-code Android ARM64 groups pass: output
copy bounds, AVC/HEVC CSD and real encoder interfaces using synthetic caps.
Two shell/XML groups pass, including both nodes, a late encoder and a missing
encoder without loss of the decoder. Complete vendor init verification passes;
existing mediacodec V4L2 policy grants cover this path. No permissive rule was
added. All eleven patches replay cleanly and match the compiled source.

Old AR50 encoder drain now uses the same nonzero dummy EOS allocation as the
working decoder quirk. Numeric HFI session errors and the timed-out command are
retained in kernel messages. This is a compatibility candidate for the signed
6.0.39 payload, with no firmware modification or unproved hardware success.

The incremental integrated builder now requires a checked `--encoder-stage`
(default `A9-build/mainline/encoder-host-20261024`). It replaces the three
Venus modules, two private Codec2 libraries, service, XML, init and startup
worker together. Its receipt pins source and payload hashes, checks every
module import against the integrated kernel exports, and requires the other83
private dependencies to match the image baseline. Existing vendor destinations
retain their labels/modes; startup uses the existing enforcing `a9ml_video`
domain directly. No new SELinux grant or permissive domain is introduced.

Local evidence is in `A9-build/mainline/encoder-host-20261024/`:
`encoder-stage.json`, `packaging-checks.json`, `kernel/build.log`, Codec2 build
logs, production-code test logs and `vendor-overlay`. The nine payload targets
exist in the integrated baseline, all358 imported symbol CRCs match, and the
other83 private libraries are byte-identical. The host smoke check also rejects
changed payload bytes and an incorrect import CRC. The checked init tree is
byte-identical to the new source. No image was built by this worker.

The image bundle also receives `encoder-probes/codec2-encode`, its short
instructions and the source/payload receipt. This exact-name NDK probe supplies
30 synthetic320x240 frames separately to `c2.v4l2.avc.encoder` and
`c2.v4l2.hevc.encoder`, counting encoded buffers and EOS without camera/audio
or media export. It is compiled with warnings as errors; it has not run against
hardware. The ordinary framework shell cannot execute `vendor_file`, so the
existing phone owner stages this adjacent artifact through its normal
shell-owned data path. Do not add an execution policy exception.

INTEG16 runs both native cases automatically in sweep row18, under a30-second
outer timeout each, and requires both to pass. Missing hardware or input now
fails this required parity row. The owned phone check must also show the named
Venus encoder and both Codec2 names. Require
`status=0 inputs=30 outputs>0 bytes>0 eos=1` for both codecs, plus the existing
row18 AVC test and row17 decoder60/60 regression check. AVC success alone does
not qualify HEVC. `ENCODE_STAGE` marks create/configure/start/drain boundaries;
if a case fails, retain its final numeric result, the HFI command/error and
mediacodec AVCs. Those results determine whether a deeper old-firmware HFI port
is needed. No phone operation is performed by this worker.
