// SPDX-License-Identifier: Apache-2.0
// Five focused cases exercise real composition/controller code against fake files.
#include "gadget_core.h"
#include <cassert>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <future>
#include <iostream>
using namespace a9::usb;
namespace fs = std::filesystem;
void put(const fs::path& p, const char* value = "") {
    fs::create_directories(p.parent_path());
    std::ofstream(p) << value;
}
int main(int argc, char** argv) {
    assert(argc == 2);
    fs::path root = argv[1];
    Paths paths{(root/"g").string(), (root/"ffs").string(), (root/"udc").string()};
    auto log = [](int, const std::string&) {};
    for (auto name : {"UDC", "idVendor", "idProduct", "bDeviceClass", "bDeviceSubClass", "bDeviceProtocol",
                      "configs/b.1/strings/0x409/configuration"}) put(root/"g"/name);
    fs::create_directories(root/"udc/test-controller");
    for (auto fn : {"ffs.adb", "ffs.mtp", "ffs.ptp", "rndis.gs4", "ncm.gs8", "acm.gs9"})
        fs::create_directories(root/"g/functions"/fn);
    for (auto ep : {"ep1", "ep2"}) put(root/"ffs/adb"/ep);
    Gadget gadget(paths, [] { return "wrong-controller"; }, log);
    // R4 has already enumerated independently of the HAL, with f8/f9 intact.
    put(root/"g/UDC", "test-controller");
    put(root/"g/idProduct", "0x4ee7");
    for (auto [link, fn] : {std::pair{"f1", "ffs.adb"}, {"f8", "ncm.gs8"}, {"f9", "acm.gs9"}})
        fs::create_symlink(root/"g/functions"/fn, root/"g/configs/b.1"/link);
    Controller controller(&gadget, log, 1);
    controller.start();
    using R = Controller::Result;
    // 1: NONE/charging and requested functions all include ADB; discover UDC.
    for (uint64_t fn : {kNone, kAdb, kMtp, kPtp, kRndis, kNcm}) {
        auto c = compose(fn);
        assert(c.supported && c.functions.back() == "ffs.adb");
    }
    assert(controller.setFunctions(kNone, 50) == R::kSuccess);
    assert(controller.currentFunctions() == kAdb && gadget.bound());
    assert(gadget.udc() == "test-controller");
    assert(controller.adoptCount() == 1 && controller.bindCount() == 0);
    assert(fs::exists(root/"g/configs/b.1/f8") && fs::exists(root/"g/configs/b.1/f9"));
    std::cout << "PASS 1: R4 ADB/ACM/NCM adoption without rebinding\n";
    // 2: Don't bind MTP before all descriptors; bind once ep3 appears.
    auto pending = std::async(std::launch::async, [&] { return controller.setFunctions(kMtp, 2500); });
    std::this_thread::sleep_for(std::chrono::milliseconds(30));
    assert(!gadget.bound());
    put(root/"ffs/mtp/ep1"); put(root/"ffs/mtp/ep2");
    std::this_thread::sleep_for(std::chrono::milliseconds(30));
    assert(!gadget.bound());
    // A valid descriptor arriving inside Android's budget but beyond the old
    // private two-second clamp must not cause an ADB fallback.
    std::this_thread::sleep_for(std::chrono::milliseconds(2200));
    put(root/"ffs/mtp/ep3");
    assert(pending.get() == R::kSuccess);
    assert(gadget.matches(compose(kMtp)));
    assert(!fs::exists(root/"g/configs/b.1/f8") && !fs::exists(root/"g/configs/b.1/f9"));
    std::cout << "PASS 2: MTP endpoint readiness\n";
    // 3: Missing PTP endpoints cause bounded fallback to ADB-only.
    assert(controller.setFunctions(kPtp, 20) == R::kError);
    assert(controller.currentFunctions() == kAdb && gadget.matches(compose(kAdb)));
    std::cout << "PASS 3: descriptor timeout restores ADB\n";
    // 4: A missing kernel function and unsupported request restore ADB too.
    fs::remove(root/"g/functions/rndis.gs4");
    assert(controller.setFunctions(kRndis, 50) == R::kNotSupported);
    assert(controller.currentFunctions() == kAdb && gadget.matches(compose(kAdb)));
    assert(controller.setFunctions(kAccessory, 50) == R::kNotSupported);
    assert(gadget.matches(compose(kAdb)));
    std::cout << "PASS 4: unavailable/unsupported function restores ADB\n";
    // 5: Daemon unbind/restart is repaired without a new framework request.
    assert(gadget.unbind());
    fs::remove(root/"ffs/adb/ep2");
    std::this_thread::sleep_for(std::chrono::milliseconds(30));
    put(root/"ffs/adb/ep2");
    for (int i = 0; !gadget.bound() && i < 100; ++i)
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    assert(gadget.bound());
    controller.stop();
    std::cout << "PASS 5: ADB daemon restart rebind\n";
}
