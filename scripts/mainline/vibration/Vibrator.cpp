// SPDX-License-Identifier: Apache-2.0
// Minimal Android vibrator V3 HAL backed by the A9 Linux FF_RUMBLE device.
#include <aidl/android/hardware/vibrator/BnVibrator.h>
#include <android/binder_manager.h>
#include <android/binder_process.h>
#include <android/log.h>
#include <linux/input.h>
#include <dirent.h>
#include <fcntl.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <cstring>
#include <mutex>

using namespace aidl::android::hardware::vibrator;
using ndk::ScopedAStatus;

class Vibrator : public BnVibrator {
    std::mutex mutex_;
    int fd_ = -1, effect_ = -1;
    static ScopedAStatus unsupported() {
        return ScopedAStatus::fromExceptionCode(EX_UNSUPPORTED_OPERATION);
    }
    static ScopedAStatus failed() {
        __android_log_print(ANDROID_LOG_ERROR, "vibrator-a9", "FF operation: %s", strerror(errno));
        return ScopedAStatus::fromExceptionCode(EX_ILLEGAL_STATE);
    }
    bool openInput() {
        if (fd_ >= 0) return true;
        DIR* dir = opendir("/dev/input");
        if (!dir) return false;
        while (dirent* ent = readdir(dir)) {
            if (strncmp(ent->d_name, "event", 5)) continue;
            std::string path = std::string("/dev/input/") + ent->d_name;
            int fd = open(path.c_str(), O_RDWR | O_CLOEXEC);
            if (fd < 0) continue;
            char name[128] = {};
            if (ioctl(fd, EVIOCGNAME(sizeof(name)), name) >= 0 &&
                !strcmp(name, "a9-aw86xx-haptics")) {
                fd_ = fd;
                break;
            }
            close(fd);
        }
        closedir(dir);
        return fd_ >= 0;
    }
    bool stop() {
        if (effect_ < 0) return true;
        input_event ev = {};
        ev.type = EV_FF;
        ev.code = effect_;
        ev.value = 0;
        bool ok = write(fd_, &ev, sizeof(ev)) == sizeof(ev);
        if (ioctl(fd_, EVIOCRMFF, effect_) < 0) ok = false;
        // Closing an evdev FD also flushes its effects after an I/O failure.
        if (!ok) { close(fd_); fd_ = -1; }
        effect_ = -1;
        return ok;
    }
  public:
    ~Vibrator() override { stop(); if (fd_ >= 0) close(fd_); }
    ScopedAStatus getCapabilities(int32_t* out) override { *out = 0; return ScopedAStatus::ok(); }
    ScopedAStatus off() override {
        std::lock_guard<std::mutex> lock(mutex_);
        return stop() ? ScopedAStatus::ok() : failed();
    }
    ScopedAStatus on(int32_t ms, const std::shared_ptr<IVibratorCallback>& cb) override {
        if (cb) return unsupported();
        if (ms <= 0 || ms > 65535) return ScopedAStatus::fromExceptionCode(EX_ILLEGAL_ARGUMENT);
        std::lock_guard<std::mutex> lock(mutex_);
        if (!stop() || !openInput()) return failed();
        ff_effect effect = {};
        effect.type = FF_RUMBLE;
        effect.id = -1;
        effect.replay.length = ms;
        effect.u.rumble.strong_magnitude = 0xffff;
        if (ioctl(fd_, EVIOCSFF, &effect) < 0) return failed();
        effect_ = effect.id;
        input_event ev = {};
        ev.type = EV_FF;
        ev.code = effect_;
        ev.value = 1;
        if (write(fd_, &ev, sizeof(ev)) != sizeof(ev)) { stop(); return failed(); }
        return ScopedAStatus::ok();
    }
    ScopedAStatus getSupportedEffects(std::vector<Effect>* out) override { out->clear(); return ScopedAStatus::ok(); }
    ScopedAStatus perform(Effect, EffectStrength, const std::shared_ptr<IVibratorCallback>&, int32_t*) override { return unsupported(); }
    ScopedAStatus setAmplitude(float) override { return unsupported(); }
    ScopedAStatus setExternalControl(bool) override { return unsupported(); }
    ScopedAStatus getCompositionDelayMax(int32_t*) override { return unsupported(); }
    ScopedAStatus getCompositionSizeMax(int32_t*) override { return unsupported(); }
    ScopedAStatus getSupportedPrimitives(std::vector<CompositePrimitive>* out) override { out->clear(); return ScopedAStatus::ok(); }
    ScopedAStatus getPrimitiveDuration(CompositePrimitive, int32_t*) override { return unsupported(); }
    ScopedAStatus compose(const std::vector<CompositeEffect>&, const std::shared_ptr<IVibratorCallback>&) override { return unsupported(); }
    ScopedAStatus getSupportedAlwaysOnEffects(std::vector<Effect>* out) override { out->clear(); return ScopedAStatus::ok(); }
    ScopedAStatus alwaysOnEnable(int32_t, Effect, EffectStrength) override { return unsupported(); }
    ScopedAStatus alwaysOnDisable(int32_t) override { return unsupported(); }
    ScopedAStatus getResonantFrequency(float*) override { return unsupported(); }
    ScopedAStatus getQFactor(float*) override { return unsupported(); }
    ScopedAStatus getFrequencyResolution(float*) override { return unsupported(); }
    ScopedAStatus getFrequencyMinimum(float*) override { return unsupported(); }
    ScopedAStatus getBandwidthAmplitudeMap(std::vector<float>*) override { return unsupported(); }
    ScopedAStatus getPwlePrimitiveDurationMax(int32_t*) override { return unsupported(); }
    ScopedAStatus getPwleCompositionSizeMax(int32_t*) override { return unsupported(); }
    ScopedAStatus getSupportedBraking(std::vector<Braking>*) override { return unsupported(); }
    ScopedAStatus composePwle(const std::vector<PrimitivePwle>&, const std::shared_ptr<IVibratorCallback>&) override { return unsupported(); }
    ScopedAStatus performVendorEffect(const VendorEffect&, const std::shared_ptr<IVibratorCallback>&) override { return unsupported(); }
    ScopedAStatus getFrequencyToOutputAccelerationMap(std::vector<FrequencyAccelerationMapEntry>*) override { return unsupported(); }
    ScopedAStatus getPwleV2PrimitiveDurationMaxMillis(int32_t*) override { return unsupported(); }
    ScopedAStatus getPwleV2CompositionSizeMax(int32_t*) override { return unsupported(); }
    ScopedAStatus getPwleV2PrimitiveDurationMinMillis(int32_t*) override { return unsupported(); }
    ScopedAStatus composePwleV2(const CompositePwleV2&, const std::shared_ptr<IVibratorCallback>&) override { return unsupported(); }
};

int main() {
    ABinderProcess_setThreadPoolMaxThreadCount(0);
    auto vibrator = ndk::SharedRefBase::make<Vibrator>();
    std::string instance = std::string(IVibrator::descriptor) + "/default";
    if (AServiceManager_addService(vibrator->asBinder().get(), instance.c_str()) != STATUS_OK) return 1;
    ABinderProcess_joinThreadPool();
    return 1;
}
