// SPDX-License-Identifier: LGPL-2.1-or-later
// Exercise actual IPA algorithm objects under bionic, with a simulated sensor.
#include "algorithms/agc.h"
#include "algorithms/awb.h"
#include "algorithms/blc.h"
#include "algorithms/ccm.h"
#include "libcamera/internal/yaml_parser.h"
#include "libcamera/internal/software_isp/stats_ownership.h"
#include "libcamera/internal/a6l_sensor_control_guard.h"
#include "libcamera/internal/a9_frame_timing.h"
#include "libipa/camera_sensor_helper.h"
#include <libcamera/base/file.h>
#include <libcamera/control_ids.h>
#include <cassert>
#include <algorithm>
#include <cmath>
#include <cstdio>
using namespace libcamera;
using namespace libcamera::ipa::soft;

int main(int argc, char **argv)
{
    assert(argc == 2);
    File tuning(argv[1]); assert(tuning.open(File::OpenModeFlag::ReadOnly));
    auto yaml = YamlParser::parse(tuning); assert(yaml);
    const ValueNode &tuningAlgorithms = (*yaml)["algorithms"];
    IPAContext context(16);
    IPAConfigInfo config;
    algorithms::Agc agc;
    context.configuration.agc = {6, 3252, 1, 16, 1, .15, 10.2 * std::chrono::microseconds(1)};
    context.activeState.blc.level = 16;
    assert(agc.configure(context, config) == 0);
    int exposure = 1000; double gain = 1;
    int settled = -1;
    for (unsigned frame = 0; frame < 100; ++frame) {
        IPAFrameContext f{}; f.sensor = {exposure, gain};
        ControlList request(controls::controls), metadata(controls::controls);
        agc.queueRequest(context, frame, f, request);
        SwIspStats stats{}; stats.valid = frame % 4 == 0;
        double mean = std::min(.98, .02 * exposure / 1000 * gain);
        unsigned bin = std::min(63u, unsigned((16 + 239 * mean) / 4));
        stats.yHistogram[bin] = 10000;
        agc.process(context, frame, f, &stats, metadata);
        assert(f.sensor.exposure >= 6 && f.sensor.exposure <= 3252);
        assert(f.sensor.gain >= 1 && f.sensor.gain <= 16);
        assert(f.sensor.exposure <= std::max(exposure + 1, int(exposure * 1.5 + 1)));
        assert(f.sensor.gain <= gain * 1.5 + .15);
        if (metadata.get(controls::AeState) == controls::AeStateConverged) { settled = frame; break; }
        exposure = f.sensor.exposure; gain = f.sensor.gain;
    }
    assert(settled >= 0 && settled < 80 && exposure > 1000 && gain > 1);
    printf("PASS bounded AGC dark startup converges in %d frames, exposure=%d gain=%.3f\n", settled, exposure, gain);
    // Replay the two production gain helpers, including integer-code readback.
    // Both must raise gain with exposure capped, and can reach CONVERGED.
    for (const char *name : {"s5k3l6", "hi556"}) {
        auto helper = ipa::CameraSensorHelperFactoryBase::create(name); assert(helper);
        IPAContext sensorContext(16);
        const bool front = std::string(name) == "hi556";
        const int maxExposure = front ? 1980 : 3260;
        const unsigned minCode = front ? 0 : 32;
        const unsigned maxCode = front ? 240 : 512;
        sensorContext.configuration.agc = {6, maxExposure, 1, 16, 1, .15, 10.2 * std::chrono::microseconds(1)};
        sensorContext.activeState.blc.level = 16;
        algorithms::Agc quantized; assert(quantized.configure(sensorContext, config) == 0);
        unsigned code = minCode; bool raised = false, converged = false;
        for (unsigned frame = 0; frame < 100; ++frame) {
            IPAFrameContext q{}; q.sensor = {maxExposure, helper->gain(code)};
            ControlList request(controls::controls), qmeta(controls::controls);
            quantized.queueRequest(sensorContext, frame, q, request);
            SwIspStats s{}; s.valid = frame % 4 == 0;
            double mean = std::min(.98, .03 * q.sensor.gain);
            s.yHistogram[std::min(63u, unsigned((16 + 239 * mean) / 4))] = 10000;
            quantized.process(sensorContext, frame, q, &s, qmeta);
            code = helper->gainCode(q.sensor.gain);
            assert(q.sensor.exposure == maxExposure && code <= maxCode);
            raised |= code > minCode;
            if (qmeta.get(controls::AeState) == controls::AeStateConverged) { converged = true; break; }
        }
        assert(raised && converged);
        printf("PASS %s at exposure cap: quantized gain rises %u->%u, AE converges\n", name, minCode, code);
    }
    // At the sensor limits a black scene must not claim convergence.
    algorithms::Agc limited; assert(limited.configure(context, config) == 0);
    IPAFrameContext dark{}; dark.sensor = {3252, 16};
    SwIspStats blackStats{}; blackStats.valid = true; blackStats.yHistogram[4] = 10000;
    ControlList darkMeta(controls::controls);
    limited.process(context, 0, dark, &blackStats, darkMeta);
    assert(dark.sensor.exposure == 3252 && dark.sensor.gain == 16);
    assert(darkMeta.get(controls::AeState) == controls::AeStateSearching);
    IPAFrameContext bright{}; bright.sensor = {3252, 16};
    SwIspStats brightStats{}; brightStats.valid = true; brightStats.yHistogram[63] = 10000;
    limited.process(context, 4, bright, &brightStats, darkMeta);
    assert(bright.sensor.exposure == 3252 && bright.sensor.gain < 16);
    // Last AE decision must remain available on the other three frames.
    IPAFrameContext cached{}; cached.sensor = {exposure, gain};
    SwIspStats invalid{}; ControlList meta(controls::controls);
    agc.process(context, 1, cached, &invalid, meta);
    assert(meta.get(controls::AeState) == controls::AeStateConverged);
    ControlList ev(controls::controls); ev.set(controls::ExposureValue, -1.0f);
    agc.queueRequest(context, 2, cached, ev); agc.process(context, 2, cached, &invalid, meta);
    assert(meta.get(controls::AeState) == controls::AeStateSearching);
    puts("PASS cached AE state and changed-EV SEARCHING state");

    algorithms::BlackLevel black;
    algorithms::Awb awb;
    algorithms::Ccm ccm;
    assert(black.init(context, tuningAlgorithms[0]["BlackLevel"]) == 0);
    assert(black.configure(context, config) == 0 && context.activeState.blc.level == 16);
    assert(awb.configure(context, config) == 0);
    assert(ccm.init(context, tuningAlgorithms[2]["Ccm"]) == 0 && context.ccmEnabled);
    SwIspStats colour{}; colour.valid = true; colour.yHistogram[20] = 1000;
    colour.sum_ = RGB<uint64_t>({76000,116000,66000});
    IPAFrameContext f{}; DebayerParams params{};
    awb.process(context, 0, f, &colour, meta); awb.prepare(context, 1, f, &params);
    assert(std::abs(params.gains.r() - 100.0 / 60) < 1e-5);
    assert(std::abs(params.gains.b() - 2) < 1e-5);
    // Every extracted matrix preserves neutral grey; exercise interpolated CCT too.
    for (unsigned ct : {2000u,2275u,3025u,4250u,5150u,6350u,7500u}) {
        context.activeState.awb.temperatureK = ct;
        context.activeState.combinedMatrix = Matrix<float,3,3>::identity();
        ccm.prepare(context, 1, f, &params);
        for (unsigned row = 0; row < 3; ++row) {
            float sum = 0; for (unsigned col = 0; col < 3; ++col) sum += f.ccm[row][col];
            assert(std::abs(sum - 1) < 1e-5);
        }
    }
    puts("PASS RAW64 black level, green-cast AWB gains, parsed stock CCMs and neutral preservation");

    StatsOwnership owner; owner.start();
    auto *output = reinterpret_cast<FrameBuffer *>(uintptr_t(1));
    assert(owner.begin(0, output)); assert(!owner.begin(1, output));
    assert(owner.publish(0)); assert(!owner.acknowledge(1)); assert(owner.acknowledge(0));
    assert(!owner.acknowledge(0)); assert(owner.begin(1, output));
    owner.halt(); assert(!owner.publish(1)); owner.clearAfterDrain();
    ControlList sensor;
    assert(!a6lSensorExposureGain(sensor));
    sensor.set(V4L2_CID_EXPOSURE, int32_t(1000)); sensor.set(V4L2_CID_ANALOGUE_GAIN, int32_t(32));
    assert(a6lSensorExposureGain(sensor)->exposure == 1000);
    puts("PASS statistics ownership ACK ordering/stop and sensor-control guard");

    // POC low-light timing, using the measured front mean at33.5ms/16x.
    IPAContext fpsContext(16);
    fpsContext.configuration.agc = {6, 3932, 1, 16, 1, .15, (2816.0 / 166400000) * std::chrono::seconds(1)};
    fpsContext.configuration.agc.minFrameLines = 1988;
    fpsContext.configuration.agc.maxFrameLines = 3940;
    fpsContext.configuration.agc.exposureMargin = 8;
    fpsContext.activeState.blc.level = 16;
    algorithms::Agc fpsAgc; assert(fpsAgc.configure(fpsContext, config) == 0);
    int exp = 1980, kernelExposure = 1980, kernelLines = 1988, currentLines = 1988;
    double again = 16; bool reached = false;
    for (unsigned frame = 0; frame < 100; ++frame) {
        IPAFrameContext q{}; q.sensor = {exp, again};
        ControlList request(controls::controls), qmeta(controls::controls);
        const std::array<int64_t, 2> range{33334, 66667};
        request.set(controls::FrameDurationLimits, range);
        fpsAgc.queueRequest(fpsContext, frame, q, request);
        SwIspStats s{}; s.valid = frame % 4 == 0;
        double mean = std::min(.98, .2523 * exp / 1980 * again / 16);
        s.yHistogram[std::min(63u, unsigned((16 + 239 * mean) / 4))] = 10000;
        fpsAgc.process(fpsContext, frame, q, &s, qmeta);
        int desired = std::clamp(q.sensor.exposure + 8, q.minFrameLines, q.exposureMax + 8);
        assert(a9ApplyFrameTiming(currentLines, desired,
            [&](int lines) { assert(kernelExposure <= lines - 8); kernelLines = lines; return 0; },
            [&]() { assert(q.sensor.exposure <= kernelLines - 8); kernelExposure = q.sensor.exposure; return 0; }) == 0);
        exp = q.sensor.exposure; again = q.sensor.gain;
        assert(currentLines >= 1988 && currentLines <= 3940 && again <= 16);
        if (qmeta.get(controls::AeState) == controls::AeStateConverged) { reached = true; break; }
    }
    assert(reached && exp > 2700 && exp <= 3932);
    printf("PASS 15-30fps measured-front model converges: exposure=%d frame-lines=%d gain=%.2f\n", exp, currentLines, again);
    // Bright light reduces gain, then exposure/frame time back to nominal30fps.
    for (unsigned frame = 100; frame < 300 && currentLines > 1988; ++frame) {
        IPAFrameContext q{}; q.sensor = {exp, again};
        ControlList request(controls::controls), qmeta(controls::controls);
        fpsAgc.queueRequest(fpsContext, frame, q, request);
        SwIspStats s{}; s.valid = frame % 4 == 0; s.yHistogram[63] = 10000;
        fpsAgc.process(fpsContext, frame, q, &s, qmeta);
        int desired = std::clamp(q.sensor.exposure + 8, q.minFrameLines, q.exposureMax + 8);
        assert(a9ApplyFrameTiming(currentLines, desired,
            [&](int lines) { assert(kernelExposure <= lines - 8); kernelLines = lines; return 0; },
            [&]() { assert(q.sensor.exposure <= kernelLines - 8); kernelExposure = q.sensor.exposure; return 0; }) == 0);
        exp = q.sensor.exposure; again = q.sensor.gain;
    }
    assert(currentLines == 1988);
    IPAFrameContext fixed{}; fixed.sensor = {3932,16};
    ControlList fixedReq(controls::controls), fixedMeta(controls::controls);
    const std::array<int64_t,2> fixedRange{33334,33334};
    fixedReq.set(controls::FrameDurationLimits, fixedRange);
    fpsAgc.queueRequest(fpsContext, 301, fixed, fixedReq);
    fpsAgc.process(fpsContext, 301, fixed, &invalid, fixedMeta);
    assert(fixed.exposureMax == 1980 && fixed.sensor.exposure <= 1980);
    int touched = 0, oldLines = 1988;
    assert(a9ApplyFrameTiming(oldLines, 3940, [&](int) { return -1; }, [&]() { touched++; return 0; }) == -1);
    assert(oldLines == 1988 && !touched);
    oldLines = 3940;
    assert(a9ApplyFrameTiming(oldLines, 1988, [&](int) { touched++; return 0; }, [&]() { return -1; }) == -1);
    assert(oldLines == 3940 && !touched);
    puts("PASS bright return, fixed30fps invalid-stats clamp and failed-write ordering");
}
