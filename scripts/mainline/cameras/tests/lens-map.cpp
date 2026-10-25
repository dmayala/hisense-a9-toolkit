// SPDX-License-Identifier: LGPL-2.1-or-later
// The runner includes the production IPA implementation with private test access.
#include <cassert>
#include <cstdio>
#include "libcamera/internal/byte_stream_buffer.h"
#include "libcamera/internal/control_serializer.h"
#include "libcamera/internal/ipa_data_serializer.h"

using namespace libcamera;
using namespace libcamera::ipa::soft;

static ControlInfoMap transfer(ControlSerializer &from, ControlSerializer &to,
                               const ControlInfoMap &map)
{
    std::vector<uint8_t> bytes(ControlSerializer::binarySize(map));
    ByteStreamBuffer output(bytes.data(), bytes.size());
    assert(from.serialize(map, output) == 0 && !output.overflow());
    ByteStreamBuffer input(static_cast<const uint8_t *>(bytes.data()), bytes.size());
    auto result = to.deserialize<ControlInfoMap>(input);
    assert(!input.overflow());
    return result;
}

int main()
{
    const Control<int32_t> focus(V4L2_CID_FOCUS_ABSOLUTE, "Focus", "v4l2", ControlId::Direction::In);
    const Control<int32_t> exposure(V4L2_CID_EXPOSURE, "Exposure", "v4l2", ControlId::Direction::In);
    const Control<int32_t> gain(V4L2_CID_ANALOGUE_GAIN, "Gain", "v4l2", ControlId::Direction::In);
    const Control<bool> flip(V4L2_CID_VFLIP, "VFlip", "v4l2", ControlId::Direction::In);
    const ControlIdMap lensIds{{focus.id(), &focus}};
    const ControlIdMap sensorIds{{flip.id(), &flip}, {exposure.id(), &exposure}, {gain.id(), &gain}};
    const ControlInfoMap lens{{{&focus, ControlInfo(0, 1023, 116)}}, lensIds};
    const ControlInfoMap sensor{{{&flip, ControlInfo(false, true, false)},
                                {&exposure, ControlInfo(6, 3260, 1000)},
                                {&gain, ControlInfo(32, 512, 32)}}, sensorIds};
    ControlSerializer proxy(ControlSerializer::Role::Proxy), worker(ControlSerializer::Role::Worker);
    IPASoftSimple ipa;
    ipa.params_ = nullptr; ipa.stats_ = nullptr;
    // Model the map formerly retained at init. configure invalidates these IDs.
    ipa.lensInfoMap_ = transfer(proxy, worker, lens);
    for (bool rear : {false, true, true}) {
        proxy.reset(); worker.reset();
        IPAConfigInfo config;
        config.sensorInfo.minLineLength = 1000;
        config.sensorInfo.pixelRate = 100000000;
        config.sensorControls = transfer(proxy, worker, sensor);
        config.lensControls = rear ? transfer(proxy, worker, lens) : ControlInfoMap();
        assert(ipa.configure(config) == 0);
        assert(ipa.lensInfoMap_.empty() == !rear);
        if (!rear) continue;
        // Both keys and range values must belong to the current generation.
        assert(&ipa.lensInfoMap_.idmap() == &config.lensControls.idmap());
        assert(ipa.lensInfoMap_.find(focus.id())->first->type() == ControlTypeInteger32);
        assert(ipa.lensInfoMap_.at(focus.id()).min().get<int32_t>() == 0);
        assert(ipa.sensorInfoMap_.find(flip.id())->first->type() == ControlTypeBool);
        assert(ipa.sensorInfoMap_.at(flip.id()).min().type() == ControlTypeBool);
        ControlList requested(ipa.lensInfoMap_);
        requested.set(focus.id(), int32_t(400));
        auto wire = IPADataSerializer<ControlList>::serialize(requested, &worker);
        auto received = IPADataSerializer<ControlList>::deserialize(std::get<0>(wire), &proxy);
        assert(received.get(focus.id()).get<int32_t>() == 400);
        assert(!received.contains(flip.id()));
    }
    std::puts("PASS real IPA configure + serializer resets: front, rear, reconfigure; bool VFLIP and int32 focus roundtrip");
}
