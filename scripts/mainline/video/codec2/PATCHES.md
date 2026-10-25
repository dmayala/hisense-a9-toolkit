# Android 16 Venus decoder patches

Apply `patches/*.patch` in lexical order to the Android 16 `external/v4l2_codec2`
source fetched by the parent build. These are decoder-only adaptations of the
Hisense A6L patches 0001 and 0005–0010, with no service transport replacement.

1. `0001`: discover video nodes using sysfs names matching `venus` by default;
   avoid opening unrelated camera nodes and do not cache empty discovery.
2. `0002`: advertise nonsecure H.264, VP8, VP9 and HEVC only when both the
   `ro.vendor.v4l2_codec2.decoder.supported.<codec>` property and real device
   capabilities permit it. All properties default false. No encoder or secure
   decoder is advertised by this POC store.
3. `0003`: parse H.264/HEVC SPS crop; use it when driver crop is the full coded
   frame and dimensions agree within the reference patch's alignment allowance.
4. `0004`: tolerate CAPTURE G_FMT refusal before stream headers; defer CAPTURE
   allocation to SOURCE_CHANGE. This retains the reference's single G_FMT probe.
5. `0005`: nonsecure MMAP bitstream input, eight buffers, up to 4 MiB each;
   merge parameter-set-only H.264/HEVC input into the next access unit.
   `vendor.a9.c2dec.input_mmap` and `vendor.a9.c2dec.merge_csd` default true.
   Unlike the reference, pending CSD is capacity-bounded, oversize input fails
   instead of truncating, flush clears pending CSD, and secure input stays DMABUF.
6. `0006`: recycle ByteBuffer graphic allocations through C2PooledBlockPool
   using explicit VER_HIDL. `vendor.a9.c2dec.bytebuffer_pool` defaults true.
   Check null pool data before dereferencing it. No bufferpool2/IGBA APIs used.
7. `0007`: keep retry counters per pool and retry transient allocation/transport
   errors with a slow backoff, from A6L 0010. Invalid pools retain upstream stop
   behavior. The HIDL BUFFERQUEUE path already uses dmabuf identity; retain its
   existing buffer migration/recycling and bounded CAPTURE-slot ownership.
8. `0008`: use the module foundation header path for MediaDefs; add an early
   service `--link-check` that returns before normal Android runtime operations.
9. `0009`: size single-memory-plane NV12 graphic allocations using CAPTURE
   `bytesperline` as the allocation width, retaining the driver's scanline count
   and deriving visible crop from the original picture dimensions. Venus needs
   stride 384 for a 320-wide picture; a 320-wide minigbm BO is too small and has
   the wrong chroma offset. Allow the driver's 32-line height alignment when
   using SPS crop. On QBUF failure log errno, plane geometry and dma-buf extent.
   This addresses the v3 ByteBuffer CAPTURE failure; the separate YUV Surface
   usage rejection in minigbm remains unresolved.

Initially skipped: A6L 0002–0004 encoder changes. A9 patch0011 now implements
device-backed AVC/HEVC encoder registration, MMAP bitstream output with bounded
copies into client linear blocks, HEVC Main interface/CSD and NV12 input
normalization to the driver's coded geometry. Stock limits cap the exposed
profiles, resolution and rate; discovery failure exposes no encoder. This is
an encoder candidate for phone qualification, not a hardware encode result.
Three production-code Android ARM64 test groups cover output bounds, AVC/HEVC
parameter sets and interfaces; two additional shell/XML groups cover startup
with a late/missing encoder and the stock envelope. See [the port](../ENCODER-PORT.md).

Still skipped: AIDL IGBA identity, new CAPTURE slots, DPB-age guesses and
starvation-driven slot reassignment in 0010. Those AIDL surface heuristics are
not equivalent to the Android 16 HIDL1.2 client ownership model.

API evidence: fetched Android 16 `media/codec2/vndk/include/C2BufferPriv.h`
declares `C2PooledBlockPool(allocator, localId, BufferPoolVer = VER_HIDL)` and
`VER_HIDL = 0`. The source's existing `_C2BlockFactory::GetBufferPoolData` and
`hardware::media::bufferpool::BufferPoolData` remain in use. Parent compilation
must verify linkage for C2PooledBlockPool/C2BufferPriv and libcutils properties.

Validation: the first seven patches applied sequentially without rejects to an
independent clean extracted-source copy; replay matched the working source byte
for byte. No compile or phone qualification is claimed by this patch task.
Parent validation: all eight patches replayed and the complete target service,
plugin and codec library compiled/linked against the matching GSI ABI. QEMU
loaded the private library closure and returned successfully from --link-check.
The coordinator owns device tests,
including ByteBuffer decode, Surface decode, flush/seek and output-Surface switch.


A9 Surface R2 patch0010 is a distinct RGB presentation POC, not the A6L AIDL
slot-ownership patch. The hardware decoder uses a private HIDL GRALLOC capture
pool, maps the decoded YUV420 planes and converts with libyuv to RGBA_8888 in the
client BufferQueue. FIFO retries preserve output order; flush/stop invalidates
pending copies; allocation/map/layout errors return codec failure without YUV
fallback. CPU maps are released before sharing RGB. Protected output is refused.
ByteBuffer BASIC_GRAPHIC behavior is retained. Color is limited-range BT.601
matching the accepted test fixture; HDR/general color management/performance and
Surface switching are not qualified. See [SURFACE-R2.md](SURFACE-R2.md).


0012 adds encoder stall boundaries around the shared INTEG16 ByteBuffer and
INTEG17 Surface first-frame path: output pool, C2/GraphicBuffer mapping,
converter allocation and V4L2 QBUF/STREAMON. Invalid maps return errors before
layout/data access. A9_ENC_TRACE markers are immediately persisted by the
updated ramdisk logger. No hardware success, power change or packet change.

0013 uses the negotiated single-memory-plane `sizeimage` for encoder input
QBUF length/bytesused. Visible-width arithmetic omitted stride and tail padding:
NV12 320x256 with stride384 requires147456 bytes, not122880. The existing
allocation already uses the driver's allocated geometry; its storage is not
enlarged by this change. Offsets and the multi-memory-plane path are retained.
`tests/input-queue.py` extracts the actual enqueue method and links the production
format/allocation helpers under Android ARM64 QEMU. Five cases cover aligned,
stride-padded, tail-padded, offset and multi-plane layouts using a synthetic vb2
length contract. The old method fails the stride-padded case. This proves the
sizing repair, not firmware execution or resolution of the historical phone hang.
