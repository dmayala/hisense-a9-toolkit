// SPDX-License-Identifier: Apache-2.0
// Inject the production enqueueInputBuffer method below. Only the queue/frame transport is fake;
// V4L2 format parsing, allocation geometry and pixel-format functions come from the built library.
// No video nodes, Binder, allocator or firmware operations are executed.
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <optional>
#include <vector>
#include <log/log.h>
#include <base/numerics/safe_conversions.h>
#include <base/time/time.h>
#include <v4l2_codec2/v4l2/V4L2Device.h>

using namespace android;
namespace replay {
static void check(bool condition) { if (!condition) std::abort(); }
struct InputFrame {
    VideoFrameLayout layout;
    VideoPixelFormat pixelFormat() const { return layout.mFormat; }
    const std::vector<VideoFramePlane>& planes() const { return layout.mPlanes; }
    int64_t index() const { return 7; }
    int64_t timestamp() const { return 1234567; }
    std::vector<int> fds() const { return std::vector<int>(layout.mPlanes.size(), 123); }
};
struct TaskRunner { bool RunsTasksInCurrentSequence() const { return true; } };
struct Queue {
    std::vector<v4l2_plane> planes;
    std::vector<size_t> minimum, backing;
    timeval time{};
    bool queued = false;
    size_t freeBuffersCount() const { return 1; }
    std::optional<Queue> getFreeBuffer() const { return *this; }
    Queue* recording = nullptr;
    void setTimeStamp(timeval value) { time = value; }
    size_t bufferId() const { return 0; }
    void setPlaneDataOffset(size_t i, size_t v) { planes.at(i).data_offset = v; }
    void setPlaneSize(size_t i, size_t v) { planes.at(i).length = v; }
    void setPlaneBytesUsed(size_t i, size_t v) { planes.at(i).bytesused = v; }
    bool queueDMABuf(const std::vector<int>& fds) && {
        // vb2 __prepare_dmabuf rejects a nonzero length below min_length or above dma_buf->size.
        check(fds.size() == planes.size());
        *recording = *this;
        for (size_t i = 0; i < planes.size(); ++i) {
            if (planes[i].length < minimum[i] || planes[i].length > backing[i] ||
                planes[i].bytesused > planes[i].length ||
                planes[i].data_offset > planes[i].bytesused) return false;
        }
        recording->queued = true;
        return true;
    }
};
class InputQueueReplay {
public:
    enum class State { ENCODING };
    TaskRunner runner;
    TaskRunner* mTaskRunner = &runner;
    Queue queue;
    Queue* mInputQueue = &queue;
    State mState = State::ENCODING;
    std::optional<VideoFrameLayout> mInputLayout;
    std::unique_ptr<InputFrame> mInputBuffers[1];
    bool error = false;
    void onError() { error = true; }
    bool enqueueInputBuffer(std::unique_ptr<InputFrame> frame);
};

// PRODUCTION_METHOD

static void exercise(const char* name, uint32_t width, uint32_t stride, uint32_t size,
                     size_t offset = 0, bool multi = false) {
    v4l2_format fmt{};
    fmt.type = V4L2_BUF_TYPE_VIDEO_OUTPUT_MPLANE;
    auto& pix = fmt.fmt.pix_mp;
    pix.width = width; pix.height = 256;
    pix.pixelformat = multi ? V4L2_PIX_FMT_NV12M : V4L2_PIX_FMT_NV12;
    pix.num_planes = multi ? 2 : 1;
    pix.plane_fmt[0].bytesperline = stride;
    pix.plane_fmt[0].sizeimage = multi ? stride * 256 : size;
    if (multi) {
        pix.plane_fmt[1].bytesperline = stride;
        pix.plane_fmt[1].sizeimage = stride * 128;
    }
    InputQueueReplay enc;
    enc.mInputLayout = V4L2Device::v4L2FormatToVideoFrameLayout(fmt);
    check(enc.mInputLayout.has_value());
    const auto allocation = V4L2Device::allocatedSizeFromV4L2Format(fmt);
    const size_t extent = allocationSize(enc.mInputLayout->mFormat, allocation);
    check(extent >= size); // This fix does not enlarge or fake the backing allocation.
    enc.queue.planes.resize(pix.num_planes);
    for (size_t i = 0; i < pix.num_planes; ++i) {
        enc.queue.minimum.push_back(pix.plane_fmt[i].sizeimage);
        enc.queue.backing.push_back((multi ? pix.plane_fmt[i].sizeimage : extent) + offset);
    }
    enc.queue.recording = &enc.queue;
    auto frame = std::make_unique<InputFrame>();
    frame->layout = *enc.mInputLayout;
    for (auto& plane : frame->layout.mPlanes) plane.mOffset += offset;
    check(enc.enqueueInputBuffer(std::move(frame)));
    check(enc.queue.queued && !enc.error && enc.mInputBuffers[0]);
    check(enc.queue.time.tv_sec == 1 && enc.queue.time.tv_usec == 234567);
    for (size_t i = 0; i < pix.num_planes; ++i) {
        const auto& plane = enc.queue.planes[i];
        check(plane.length == pix.plane_fmt[i].sizeimage + offset);
        check(plane.data_offset == offset);
        if (!multi) check(plane.bytesused == plane.length);
        else check(plane.bytesused == static_cast<size_t>(getArea(
            planeSize(enc.mInputLayout->mFormat, i, enc.mInputLayout->mCodedSize)).value()) + offset);
    }
    printf("INPUT_QUEUE %s PASS length=%u sizeimage=%u\n", name,
           enc.queue.planes[0].length, pix.plane_fmt[0].sizeimage);
}
} // namespace replay
int main() {
    setvbuf(stdout, nullptr, _IOLBF, 0);
    replay::exercise("aligned", 384, 384, 147456);
    replay::exercise("stride_padding", 320, 384, 147456);
    replay::exercise("tail_padding", 320, 384, 151552);
    replay::exercise("offset", 320, 384, 147456, 4096);
    replay::exercise("multiplanar", 320, 384, 147456, 0, true);
}
