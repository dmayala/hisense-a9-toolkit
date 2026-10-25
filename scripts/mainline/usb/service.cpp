// SPDX-License-Identifier: Apache-2.0
#include "UsbGadget.h"
#include <android/binder_manager.h>
#include <android/binder_process.h>
#include <android/log.h>
int main() {
    ABinderProcess_setThreadPoolMaxThreadCount(0);
    auto service = ndk::SharedRefBase::make<aidl::android::hardware::usb::gadget::UsbGadget>();
    std::string instance = std::string(service->descriptor) + "/default";
    if (AServiceManager_addService(service->asBinder().get(), instance.c_str()) != STATUS_OK) {
        __android_log_write(ANDROID_LOG_ERROR, "a9-usb", "gadget registration failed");
        return 1;
    }
    ABinderProcess_joinThreadPool();
    return 1;
}
