// SPDX-License-Identifier: Apache-2.0
// Host/QEMU tests of production encoder helpers/interfaces. No Binder or video devices.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <memory>
#include <vector>
#include <v4l2_codec2/common/EncodeHelpers.h>
#include <v4l2_codec2/components/EncodeInterface.h>
#include <v4l2_codec2/v4l2/EncoderOutput.h>
#include <v4l2_codec2/v4l2/V4L2ComponentCommon.h>

using namespace android;
static void check(bool condition) { if (!condition) std::abort(); }

int main() {
    unsigned char src[32], dst[32];
    for (size_t i = 0; i < sizeof(src); ++i) src[i] = static_cast<unsigned char>(i);
    memset(dst, 0xee, sizeof(dst));
    check(copyEncoderOutput(dst, 16, src, 32, 7, 23));
    check(memcmp(dst, src + 7, 16) == 0 && dst[16] == 0xee);
    check(!copyEncoderOutput(dst, 15, src, 32, 7, 23));
    check(!copyEncoderOutput(dst, 32, src, 32, 24, 23));
    check(!copyEncoderOutput(dst, 32, src, 32, 0, 33));
    check(!copyEncoderOutput(dst, 32, nullptr, 32, 0, 1));
    check(!copyEncoderOutput(nullptr, 32, src, 32, 0, 1));
    check(copyEncoderOutput(nullptr, 0, nullptr, 0, 0, 0));
    check(!copyEncoderOutput(dst, 32, src, 32, 0, std::numeric_limits<size_t>::max()));
    puts("ENCODER_TEST mapped_output_bounds PASS");

    const uint8_t hevc[] = {0,0,0,1,0x40,1,0xaa, 0,0,1,0x42,1,0xbb,
                            0,0,0,1,0x44,1,0xcc, 0,0,1,0x26,1,0xdd};
    std::unique_ptr<C2StreamInitDataInfo::output> csd;
    check(extractCSDInfo(&csd, hevc, sizeof(hevc), true));
    const uint8_t expected[] = {0,0,0,1,0x40,1,0xaa, 0,0,0,1,0x42,1,0xbb,
                               0,0,0,1,0x44,1,0xcc};
    check(memcmp(csd->m.value, expected, sizeof(expected)) == 0);
    check(!extractCSDInfo(&csd, hevc, 13, true)); // Missing PPS.
    const uint8_t avc[] = {0,0,0,1,0x67,0xaa, 0,0,0,1,0x68,0xbb};
    check(extractCSDInfo(&csd, avc, sizeof(avc))); // Existing AVC path retained.
    puts("ENCODER_TEST parameter_sets PASS");

    auto reflector = std::make_shared<C2ReflectorHelper>();
    for (const auto codec : {VideoCodec::H264, VideoCodec::HEVC}) {
        const bool hevcCodec = codec == VideoCodec::HEVC;
        const auto name = hevcCodec ? V4L2ComponentName::kHEVCEncoder : V4L2ComponentName::kH264Encoder;
        check(V4L2ComponentName::isEncoder(name));
        check(!V4L2ComponentName::isDecoder(name));
        check(V4L2ComponentName::getCodec(name) == codec);
        SupportedCapabilities caps;
        caps.codec = codec;
        caps.supportedProfiles.push_back({hevcCodec ? C2Config::PROFILE_HEVC_MAIN : C2Config::PROFILE_AVC_BASELINE,
                                          ui::Size(128,128), ui::Size(1920,1088), 30, 1, false});
        EncodeInterface interface(name, reflector, caps);
        check(interface.status() == C2_OK);
        check(strcmp(interface.getOutputMediaType(), hevcCodec ? "video/hevc" : "video/avc") == 0);
        caps.supportedProfiles.clear();
        EncodeInterface absent(name, reflector, caps);
        check(absent.status() != C2_OK);
    }
    puts("ENCODER_TEST interfaces PASS");
    return 0;
}
