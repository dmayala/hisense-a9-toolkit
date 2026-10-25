/* SPDX-License-Identifier: LGPL-2.1-or-later */
#include <atomic>
#include <cassert>
#include <cstdarg>
#include <cmath>
#include <condition_variable>
#include <fcntl.h>
#include <functional>
#include <mutex>
#include <string>
#include <thread>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <linux/videodev2.h>
#include <hardware/camera_common.h>
#include <system/camera_metadata.h>
#include <libcamera/camera.h>
#include <libcamera/camera_manager.h>
#include <libcamera/property_ids.h>
#include <libcamera/internal/software_isp/swstats_cpu.h>
#include <libcamera/internal/software_isp/swisp_stats.h>
#include <libcamera/formats.h>
#include <libcamera/stream.h>
#include "src/ipa/simple/algorithms/af_search.h"

static std::atomic<int> mode{0}, ioError{0}, closed{0};
static int fakeOpen(const char *, int, ...) { return 100; }
static int fakeClose(int) { closed++; mode = 0; return 0; }
static int fakeIoctl(int, unsigned long operation, ...)
{
    if (ioError) { errno = EIO; return -1; }
    va_list args; va_start(args, operation); auto *ctrl = va_arg(args, v4l2_control *); va_end(args);
    if (operation == VIDIOC_S_CTRL) mode = ctrl->value;
    else if (operation == VIDIOC_G_CTRL) ctrl->value = mode;
    else { errno = EINVAL; return -1; }
    return 0;
}
// Exercise the production HAL helper against an in-memory V4L2 device. Only
// private descriptor injection is used; no production hardware path is opened.
#define private public
#define open fakeOpen
#define close fakeClose
#define ioctl fakeIoctl
#include "src/android/camera_torch.h"
#undef ioctl
#undef close
#undef open
#undef private

static void torchTest()
{
    A9Torch torch; torch.fd_ = 100;
    std::atomic<int> notified{-1};
    assert(torch.setModule(nullptr, true, [&](int status) { notified = status; }) == 0);
    assert(notified == TORCH_MODE_STATUS_AVAILABLE_ON && mode == V4L2_FLASH_LED_MODE_TORCH);
    mode = V4L2_FLASH_LED_MODE_NONE; // kernel deadline/fault auto-off
    for (int i = 0; i < 50 && notified != TORCH_MODE_STATUS_AVAILABLE_OFF; i++)
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    assert(notified == TORCH_MODE_STATUS_AVAILABLE_OFF);
    torch.cameraOpen_ = true;
    assert(torch.setModule(nullptr, true, [](int) {}) == -EBUSY);
    assert(torch.setCapture(ANDROID_FLASH_MODE_TORCH) == 1);
    assert(torch.setCapture(ANDROID_FLASH_MODE_OFF) == 0 && mode == 0);
    assert(torch.setCapture(ANDROID_FLASH_MODE_SINGLE) == -EINVAL && mode == 0);
    assert(torch.setCapture(ANDROID_FLASH_MODE_TORCH) == 1);
    { A9Torch::RequestGuard guard(torch); } // queue/control validation error
    assert(mode == 0);
    assert(torch.setCapture(ANDROID_FLASH_MODE_TORCH) == 1);
    torch.releaseCamera(); assert(mode == 0 && !torch.cameraOpen_);
    torch.cameraOpen_ = true; ioError = 1;
    assert(torch.setCapture(ANDROID_FLASH_MODE_TORCH) == -EIO && closed > 0);
    ioError = 0;
    assert(torch.fd_ == -1);
}

static void statsTest()
{
    using namespace libcamera;
    CameraManager manager;
    SwStatsCpu stats(manager); assert(stats.isValid());
    StreamConfiguration config; config.pixelFormat = formats::SGRBG10;
    config.size = Size(64,64); config.stride = 128;
    assert(stats.configure(config) == 0); stats.setWindow(Rectangle(config.size));
    auto *shared = static_cast<SwIspStats *>(mmap(nullptr, sizeof(SwIspStats), PROT_READ, MAP_SHARED, stats.getStatsFD().get(), 0));
    assert(shared != MAP_FAILED);
    for (int textured = 0; textured < 2; textured++) {
        uint16_t rows[3][64];
        for (int y = 0; y < 3; y++) for (int x = 0; x < 64; x++)
            rows[y][x] = 400 + (textured && (x & 4) ? 200 : 0);
        const uint8_t *src[] = {reinterpret_cast<uint8_t *>(rows[0]), reinterpret_cast<uint8_t *>(rows[1]), reinterpret_cast<uint8_t *>(rows[2])};
        stats.startFrame(0);
        for (unsigned y = 0; y < 64; y += 2) stats.processLine0(0,y,src);
        stats.finishFrame(0,0);
        assert(shared->sharpnessCount > 0);
        assert(textured ? shared->sharpness > 0 : shared->sharpness == 0);
    }
    munmap(shared,sizeof(SwIspStats));
}

static void searchTest()
{
    a6l_af::Tuning tuning;
    tuning.dacMin = tuning.infinityDac = 116;
    tuning.dacMax = tuning.macroDac = 515;
    tuning.infinityMargin = tuning.macroMargin = 0;
    tuning.coarseSteps = 8; tuning.fineSteps = 2;
    a6l_af::Search search; search.configure(tuning);
    for (int textured = 0; textured < 2; textured++) {
        search.start(); int steps = 0;
        while (search.scanning() && steps++ < 40) {
            assert(search.target() >= 116 && search.target() <= 515);
            double distance = (search.target()-310)/100.0;
            search.measure(textured ? std::exp(-distance*distance) : 0);
        }
        assert(steps < 40);
        assert(search.state() == (textured ? a6l_af::State::Focused : a6l_af::State::Failed));
        if (textured) assert(std::abs(search.target()-310) < 25);
    }
}
int main()
{
    statsTest(); searchTest(); torchTest();
    puts("PASS: expanded RAW10 statistics, A9 focus search, V4L2 torch lifecycle");
}
