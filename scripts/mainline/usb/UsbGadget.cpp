// SPDX-License-Identifier: Apache-2.0
// A9 adaptation of PaddleStroke/hisense-a6l-lineageos USB gadget HAL.
// V1 AIDL matches the frozen Android 16 interface; configfs is vendor-owned.
#define LOG_TAG "android.hardware.usb.gadget-service.a9"

#include "UsbGadget.h"

#include <android/log.h>
#include <sys/system_properties.h>
#include <unistd.h>

namespace aidl {
namespace android {
namespace hardware {
namespace usb {
namespace gadget {

using ::a9::usb::Controller;
using ::a9::usb::Gadget;
using ::a9::usb::Paths;

namespace {
void halLog(int prio, const std::string& msg) {
    __android_log_print(prio >= 2 ? ANDROID_LOG_ERROR : prio == 1 ? ANDROID_LOG_WARN : ANDROID_LOG_INFO,
                        LOG_TAG, "%s", msg.c_str());
}

Status toStatus(Controller::Result r) {
    switch (r) {
        case Controller::Result::kSuccess:
            return Status::SUCCESS;
        case Controller::Result::kNotSupported:
            return Status::CONFIGURATION_NOT_SUPPORTED;
        default:
            return Status::ERROR;
    }
}
}  // namespace

UsbGadget::UsbGadget() {
    mGadget = std::make_unique<Gadget>(
            Paths(), [] { char value[PROP_VALUE_MAX]{}; __system_property_get("sys.usb.controller", value); return std::string(value); }, halLog);
    mController = std::make_unique<Controller>(mGadget.get(), halLog);
    mController->start();
    // The boot helper starts us after R4 has enumerated. Adopt its live ADB
    // (+ACM/NCM) before publishing the HAL; do not tear down early transport.
    if (mGadget->matches(::a9::usb::compose(::a9::usb::kAdb))) {
        mTookOver = mController->setFunctions(::a9::usb::kAdb, 0) == Controller::Result::kSuccess;
    }
}

UsbGadget::~UsbGadget() {
    mController->stop();
}

::ndk::ScopedAStatus UsbGadget::setCurrentUsbFunctions(int64_t functions,
                                                       const std::shared_ptr<IUsbGadgetCallback>& callback,
                                                       int64_t timeoutMs, int64_t transactionId) {
    halLog(0, "setCurrentUsbFunctions " + std::to_string(functions));
    const Controller::Result r = mController->setFunctions(static_cast<uint64_t>(functions), timeoutMs);
    if (r == Controller::Result::kSuccess) mTookOver = true;
    Status st = toStatus(r);
    if (callback) {
        auto ret = callback->setCurrentUsbFunctionsCb(functions, st, transactionId);
        if (!ret.isOk()) halLog(2, "setCurrentUsbFunctionsCb: " + ret.getDescription());
    }
    if (st == Status::SUCCESS) return ::ndk::ScopedAStatus::ok();
    return ::ndk::ScopedAStatus::fromServiceSpecificErrorWithMessage(static_cast<int32_t>(st),
                                                                     "setCurrentUsbFunctions failed");
}

::ndk::ScopedAStatus UsbGadget::getCurrentUsbFunctions(const std::shared_ptr<IUsbGadgetCallback>& callback,
                                                       int64_t transactionId) {
    if (callback == nullptr) return ::ndk::ScopedAStatus::fromExceptionCode(EX_NULL_POINTER);
    auto ret = callback->getCurrentUsbFunctionsCb(
            static_cast<int64_t>(mController->currentFunctions()),
            mController->applied() ? Status::FUNCTIONS_APPLIED : Status::FUNCTIONS_NOT_APPLIED, transactionId);
    if (!ret.isOk()) halLog(2, "getCurrentUsbFunctionsCb: " + ret.getDescription());
    return ::ndk::ScopedAStatus::ok();
}

::ndk::ScopedAStatus UsbGadget::getUsbSpeed(const std::shared_ptr<IUsbGadgetCallback>& callback,
                                            int64_t transactionId) {
    const std::string s = mGadget->currentSpeed();
    UsbSpeed speed = UsbSpeed::UNKNOWN;
    if (s == "low-speed")
        speed = UsbSpeed::LOWSPEED;
    else if (s == "full-speed")
        speed = UsbSpeed::FULLSPEED;
    else if (s == "high-speed")
        speed = UsbSpeed::HIGHSPEED;
    else if (s == "super-speed")
        speed = UsbSpeed::SUPERSPEED;
    else if (s == "super-speed-plus")
        speed = UsbSpeed::SUPERSPEED_10Gb;
    if (callback) {
        auto ret = callback->getUsbSpeedCb(speed, transactionId);
        if (!ret.isOk()) halLog(2, "getUsbSpeedCb: " + ret.getDescription());
    }
    return ::ndk::ScopedAStatus::ok();
}

::ndk::ScopedAStatus UsbGadget::reset(const std::shared_ptr<IUsbGadgetCallback>& callback, int64_t transactionId) {
    const bool ok = mController->reset();
    if (callback) {
        auto ret = callback->resetCb(ok ? Status::SUCCESS : Status::ERROR, transactionId);
        if (!ret.isOk()) halLog(2, "resetCb: " + ret.getDescription());
    }
    if (ok) return ::ndk::ScopedAStatus::ok();
    return ::ndk::ScopedAStatus::fromServiceSpecificErrorWithMessage(-1, "reset: no applied configuration");
}

binder_status_t UsbGadget::dump(int fd, const char** /*args*/, uint32_t /*numArgs*/) {
    char buffer[512];
    snprintf(buffer, sizeof(buffer),
            "a9 usb gadget: functions=0x%llx applied=%d bound=%d udc=%s speed=%s binds=%u adopts=%u owner=%s\n",
            static_cast<unsigned long long>(mController->currentFunctions()), mController->applied(),
            mGadget->bound(), mGadget->udc().c_str(), mGadget->currentSpeed().c_str(), mController->bindCount(),
            mController->adoptCount(), mTookOver ? "hal" : "init");
    const std::string s(buffer);
    ssize_t r = write(fd, s.data(), s.size());
    (void)r;
    return STATUS_OK;
}

}  // namespace gadget
}  // namespace usb
}  // namespace hardware
}  // namespace android
}  // namespace aidl
