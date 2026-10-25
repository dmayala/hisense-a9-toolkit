// Production C2 interface/usage query under Android QEMU; no GPU/Binder/video device.
#include <v4l2_codec2/components/EncodeInterface.h>
#include <v4l2_codec2/v4l2/V4L2ComponentCommon.h>
#include <android/hardware/graphics/common/1.0/types.h>
#include <cutils/properties.h>
#include <C2AllocatorGralloc.h>
#include <cassert>
#include <cstdio>
#include <cstring>
using namespace android;
using android::hardware::graphics::common::V1_0::BufferUsage;
static int choice = -1;
static unsigned reads;
extern "C" int8_t property_get_bool(const char* key, int8_t fallback) {
    if (!strcmp(key,"debug.stagefright.a9.enc_cpu_read")) {++reads;return choice < 0 ? fallback : choice;}
    return fallback;
}
int main() {
    for (const int setting : {1,0,-1,-1}) {
        choice=setting;
        const bool readable = setting != 0;
        for (const auto codec : {VideoCodec::H264,VideoCodec::HEVC}) {
            const bool hevc=codec==VideoCodec::HEVC;
            SupportedCapabilities caps;caps.codec=codec;
            caps.supportedProfiles.push_back({hevc?C2Config::PROFILE_HEVC_MAIN:C2Config::PROFILE_AVC_BASELINE,
                    ui::Size(128,128),ui::Size(1920,1088),30,1,false});
            EncodeInterface interface(hevc?V4L2ComponentName::kHEVCEncoder:V4L2ComponentName::kH264Encoder,
                    std::make_shared<C2ReflectorHelper>(),caps);
            assert(interface.status()==C2_OK);
            C2StreamUsageTuning::input usage(0u,0);
            std::vector<std::unique_ptr<C2Param>> heap;
            assert(interface.query({&usage},{},C2_DONT_BLOCK,&heap)==C2_OK);
            const uint64_t video=static_cast<uint64_t>(BufferUsage::VIDEO_ENCODER);
            // Exercise the actual framework conversion into producer allocation usage.
            // Raw gralloc CPU_READ_OFTEN is 3; C2's 0x2 means READ_PROTECTED.
            C2AndroidMemoryUsage memory{C2MemoryUsage(usage.value)};
            const uint64_t gralloc=memory.asGrallocUsage();
            const uint64_t cpu=static_cast<uint64_t>(BufferUsage::CPU_READ_OFTEN);
            assert(gralloc==(video|(readable?cpu:0)));
            assert(!(gralloc&static_cast<uint64_t>(BufferUsage::PROTECTED)));
            assert(!(usage.value&(C2MemoryUsage::READ_PROTECTED|C2MemoryUsage::WRITE_PROTECTED)));
            assert(!(usage.value&C2MemoryUsage::CPU_WRITE));
            // Same reverse conversion CCodec/allocator use; catches mixed enum spaces.
            assert(C2AndroidMemoryUsage::FromGrallocUsage(gralloc).expected==usage.value);
        }
    }
    assert(reads==8);
    puts("INPUT_USAGE_TEST AVC/HEVC actual C2 query and gralloc translation: default CPU-read, off/on/reset, unprotected PASS");
}
