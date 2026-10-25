/*
 * Copyright 2026 A9 mainline contributors
 * SPDX-License-Identifier: Apache-2.0
 *
 * Linux-only NDK AIDL V1 transport. Management constants/discovery derive from
 * AOSP hardware/interfaces Bluetooth default at commit
 * 5688f7eb1e117ed26e642a695de300b7683acb87 (Apache-2.0).
 * HCI_USER sockets are packet-oriented: no emulator UART, Rust LE offload or
 * H4 serial-stream parser is needed. Kernel QCA owns firmware and power.
 */
#include <aidl/android/hardware/bluetooth/BnBluetoothHci.h>
#include <aidl/android/hardware/bluetooth/IBluetoothHciCallbacks.h>
#include <android/binder_manager.h>
#include <android/binder_process.h>
#include <android/log.h>
#include <sys/eventfd.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/uio.h>
#include <poll.h>
#include <fcntl.h>
#include <unistd.h>
#include <array>
#include <algorithm>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <cstring>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>
#include "hci_packets.h"

namespace bt = aidl::android::hardware::bluetooth;
using ndk::ScopedAStatus;
using Clock = std::chrono::steady_clock;
static void note(const char* message) { __android_log_write(ANDROID_LOG_INFO, "a9-bt-hal", message); }
struct SockAddrHci { sa_family_t family; uint16_t index, channel; };
static int remaining(Clock::time_point deadline) {
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now()).count();
    return ms > 0 ? int(ms) : 0;
}
static uint16_t le16(const uint8_t* p) { return uint16_t(p[0] | (unsigned(p[1]) << 8)); }

// Read-only configured-controller discovery, maximum five wall-clock seconds.
// Accept exactly hci0; never use the upstream fallback to a higher controller.
static bool configured_hci0() {
    int fd = socket(AF_BLUETOOTH, SOCK_RAW | SOCK_CLOEXEC | SOCK_NONBLOCK, 1);
    if (fd < 0) return false;
    SockAddrHci a{AF_BLUETOOTH, 0xffff, 3};
    bool success = false;
    if (bind(fd, reinterpret_cast<sockaddr*>(&a), sizeof(a)) == 0) {
        const uint8_t query[] = {3, 0, 255, 255, 0, 0}; // READ_INDEX_LIST
        auto deadline = Clock::now() + std::chrono::seconds(5);
        if (write(fd, query, sizeof(query)) == sizeof(query)) {
            while (remaining(deadline)) {
                pollfd p{fd, POLLIN, 0};
                int rc = poll(&p, 1, remaining(deadline));
                if (rc < 0 && errno == EINTR) continue;
                if (rc <= 0 || (p.revents & (POLLERR | POLLHUP | POLLNVAL))) break;
                if (!(p.revents & POLLIN)) continue;
                std::array<uint8_t, 4096> ev{};
                ssize_t n = recv(fd, ev.data(), ev.size(), MSG_TRUNC | MSG_DONTWAIT);
                if (n < 0 && (errno == EAGAIN || errno == EINTR)) continue;
                if (n < 6 || size_t(n) > ev.size() || le16(ev.data()+4) != n-6) break;
                // Parse only the matching completed READ_INDEX_LIST command.
                if (le16(ev.data()) != 1 || le16(ev.data()+2) != 0xffff) continue;
                if (n < 11 || le16(ev.data()+6) != 3 || ev[8] != 0) break;
                unsigned count = le16(ev.data()+9);
                if (n != 11 + 2*count) break;
                success = count == 1 && le16(ev.data()+11) == 0;
                break;
            }
        }
    }
    close(fd);
    return success;
}

struct HciInfo {
    uint16_t id; char name[8]; uint8_t address[6]; uint32_t flags;
    uint8_t type, features[8]; uint32_t packet_type, policy, mode;
    uint16_t acl_mtu, acl_packets, sco_mtu, sco_packets; uint32_t stats[10];
};
static_assert(sizeof(HciInfo) == 92 && offsetof(HciInfo, address) == 10);
static bool read_address(const char* path, std::array<uint8_t,6>& out) {
    int fd = open(path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return false;
    std::array<uint8_t,7> data{};
    ssize_t n = read(fd, data.data(), data.size());
    close(fd);
    bool zero = true, ones = true;
    for (unsigned i=0; i<6; ++i) { zero &= data[i] == 0; ones &= data[i] == 255; }
    if (n != 6 || zero || ones || (data[5] & 1)) return false;
    std::copy_n(data.begin(), 6, out.begin());
    return true;
}
static bool identity_hci0(int socket_fd, bool allow_up = false) {
    char physical[4096];
    ssize_t n = readlink("/sys/class/bluetooth/hci0", physical, sizeof(physical)-1);
    if (n <= 0 || size_t(n) >= sizeof(physical)-1) return false;
    physical[n] = 0;
    if (!strstr(physical, "/4a8c000.serial/")) return false;
    // Root's readiness gate checks expected versus DT bytes. Repeat the
    // private expected-versus-live-controller check on every HAL initialize.
    std::array<uint8_t,6> expected{};
    if (!read_address("/vendor/etc/bluetooth/local-bd-address", expected)) return false;
    HciInfo info{};
    if (ioctl(socket_fd, _IOR('H', 211, int), &info) < 0) return false;
    // AUTO_OFF masks UP in this read-only ioctl, as allowed by USER binding.
    uint32_t forbidden = (1u<<1) | (1u<<8) | (allow_up ? 0 : (1u<<0));
    return !memcmp(info.name, "hci0\0", 5) && info.type == 3 &&
           !(info.flags & forbidden) &&
           !memcmp(info.address, expected.data(), 6);
}
static int open_hci0() {
    if (!configured_hci0()) return -1;
    int fd = socket(AF_BLUETOOTH, SOCK_RAW | SOCK_CLOEXEC | SOCK_NONBLOCK, 1);
    if (fd < 0) return -1;
    SockAddrHci a{AF_BLUETOOTH, 0, 1};
    if (!identity_hci0(fd) || bind(fd, reinterpret_cast<sockaddr*>(&a), sizeof(a)) < 0) {
        close(fd); return -1;
    }
    // USER binding may reopen/power-cycle a NON_PERSISTENT_SETUP controller.
    // Check its actual post-open identity with a separate read-only raw fd.
    int verify = socket(AF_BLUETOOTH, SOCK_RAW | SOCK_CLOEXEC, 1);
    bool correct = verify >= 0 && identity_hci0(verify, true);
    if (verify >= 0) close(verify);
    if (!correct) { close(fd); return -1; }
    return fd;
}

class Session : public std::enable_shared_from_this<Session> {
    std::mutex io_;
    int fd_, wake_;
    std::atomic<bool> stopped_{false};
    std::shared_ptr<bt::IBluetoothHciCallbacks> cb_;
    AIBinder_DeathRecipient* death_ = nullptr;
    struct Cookie { std::weak_ptr<Session> session; };
 public:
    Session(int fd, std::shared_ptr<bt::IBluetoothHciCallbacks> cb)
        : fd_(fd), wake_(eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK)), cb_(std::move(cb)) {}
    ~Session() {
        stop();
        if (death_) AIBinder_DeathRecipient_delete(death_);
        if (wake_ >= 0) ::close(wake_);
    }
    bool link_death() {
        if (wake_ < 0) return false;
        death_ = AIBinder_DeathRecipient_new([](void* ptr) {
            if (auto session = static_cast<Cookie*>(ptr)->session.lock()) session->stop();
        });
        if (!death_) return false;
        AIBinder_DeathRecipient_setOnUnlinked(death_, [](void* ptr) { delete static_cast<Cookie*>(ptr); });
        // onUnlinked owns cookie cleanup even when linkToDeath fails (API33).
        return AIBinder_linkToDeath(cb_->asBinder().get(), death_, new Cookie{weak_from_this()}) == STATUS_OK;
    }
    bool stopped() const { return stopped_.load(); }
    void stop() {
        std::lock_guard<std::mutex> guard(io_);
        if (stopped_.exchange(true)) return;
        if (fd_ >= 0) { ::close(fd_); fd_ = -1; }
        if (wake_ >= 0) { uint64_t one=1; (void)write(wake_, &one, sizeof(one)); }
    }
    ScopedAStatus send(uint8_t type, const std::vector<uint8_t>& bytes) {
        if (!valid_hci_packet(type, bytes.data(), bytes.size()))
            return ScopedAStatus::fromExceptionCode(EX_ILLEGAL_ARGUMENT);
        std::lock_guard<std::mutex> guard(io_);
        if (stopped_ || fd_ < 0) return ScopedAStatus::fromServiceSpecificError(1);
        iovec parts[] = {{&type, 1}, {const_cast<uint8_t*>(bytes.data()), bytes.size()}};
        msghdr message{}; message.msg_iov=parts; message.msg_iovlen=2;
        auto deadline = Clock::now() + std::chrono::milliseconds(250);
        while (remaining(deadline)) {
            ssize_t sent = sendmsg(fd_, &message, MSG_DONTWAIT | MSG_NOSIGNAL);
            if (sent == ssize_t(bytes.size()+1)) return ScopedAStatus::ok();
            if (sent >= 0 || (errno != EAGAIN && errno != EINTR)) break;
            pollfd p{fd_, POLLOUT, 0};
            if (poll(&p, 1, remaining(deadline)) < 0 && errno != EINTR) break;
        }
        return ScopedAStatus::fromServiceSpecificError(2);
    }
    void start() {
        auto self = shared_from_this();
        std::thread([self] { self->receive(); }).detach();
    }
 private:
    void receive() {
        std::array<uint8_t,65540> bytes{};
        while (!stopped_) {
            int fd;
            { std::lock_guard<std::mutex> guard(io_); fd = fd_; }
            if (fd < 0) break;
            pollfd p[] = {{fd, POLLIN, 0}, {wake_, POLLIN, 0}};
            int rc = poll(p, 2, 1000);
            if (rc < 0 && errno == EINTR) continue;
            if (rc < 0 || stopped_ || p[1].revents ||
                (p[0].revents & (POLLERR | POLLHUP | POLLNVAL))) break;
            if (!(p[0].revents & POLLIN)) continue;
            ssize_t n;
            { std::lock_guard<std::mutex> guard(io_);
              if (stopped_ || fd_ != fd) break;
              n = recv(fd_, bytes.data(), bytes.size(), MSG_TRUNC | MSG_DONTWAIT); }
            if (n < 0 && (errno == EAGAIN || errno == EINTR)) continue;
            if (n < 1 || size_t(n) > bytes.size() ||
                !valid_hci_packet(bytes[0], bytes.data()+1, size_t(n)-1)) break;
            if (stopped_) break;
            std::vector<uint8_t> packet(bytes.begin()+1, bytes.begin()+n);
            ScopedAStatus status;
            switch (bytes[0]) {
            case 2: status=cb_->aclDataReceived(packet); break;
            case 3: status=cb_->scoDataReceived(packet); break;
            case 4: status=cb_->hciEventReceived(packet); break;
            case 5: status=cb_->isoDataReceived(packet); break;
            default: stop(); return;
            }
            if (!status.isOk()) break;
        }
        stop();
        note("HCI receive session stopped");
    }
};

class BluetoothHal final : public bt::BnBluetoothHci {
    std::mutex state_;
    std::shared_ptr<Session> session_;
    std::weak_ptr<Session> retiring_;
    bool initializing_ = false;
    uint64_t generation_ = 0;
 public:
    ScopedAStatus initialize(const std::shared_ptr<bt::IBluetoothHciCallbacks>& cb) override {
        if (!cb) return ScopedAStatus::fromExceptionCode(EX_ILLEGAL_ARGUMENT);
        uint64_t generation;
        bool occupied;
        { std::lock_guard<std::mutex> guard(state_);
          if (session_ && session_->stopped()) { retiring_=session_; session_.reset(); }
          occupied = initializing_ || bool(session_) || !retiring_.expired();
          generation = generation_;
          if (!occupied) initializing_ = true; }
        if (occupied) { (void)cb->initializationComplete(bt::Status::ALREADY_INITIALIZED); return ScopedAStatus::ok(); }
        int fd = open_hci0();
        std::shared_ptr<Session> session;
        if (fd >= 0) {
            session = std::make_shared<Session>(fd, cb);
            if (!session->link_death()) { session->stop(); session.reset(); }
        }
        { std::lock_guard<std::mutex> guard(state_);
          if (generation != generation_) { if (session) session->stop(); session.reset(); }
          initializing_ = false;
          if (session) session_ = session; }
        if (!session) {
            note("HCI initialization unavailable; no fallback transport");
            (void)cb->initializationComplete(bt::Status::UNABLE_TO_OPEN_INTERFACE);
            return ScopedAStatus::ok();
        }
        // Match AOSP ordering: RX must run before SUCCESS. A client may send
        // commands and wait for their replies reentrantly in that callback.
        try { session->start(); }
        catch (const std::exception&) {
            session->stop(); (void)close();
            (void)cb->initializationComplete(bt::Status::UNABLE_TO_OPEN_INTERFACE);
            return ScopedAStatus::ok();
        }
        auto status = cb->initializationComplete(bt::Status::SUCCESS);
        if (!status.isOk()) { session->stop(); (void)close(); }
        return ScopedAStatus::ok();
    }
    ScopedAStatus close() override {
        std::shared_ptr<Session> old;
        { std::lock_guard<std::mutex> guard(state_);
          ++generation_; old=std::move(session_); if (old) retiring_=old; }
        if (old) old->stop();
        return ScopedAStatus::ok();
    }
    ScopedAStatus transmit(uint8_t type, const std::vector<uint8_t>& packet) {
        std::shared_ptr<Session> session;
        { std::lock_guard<std::mutex> guard(state_); session=session_; }
        return session ? session->send(type,packet) : ScopedAStatus::fromServiceSpecificError(1);
    }
    ScopedAStatus sendHciCommand(const std::vector<uint8_t>& p) override { return transmit(1,p); }
    ScopedAStatus sendAclData(const std::vector<uint8_t>& p) override { return transmit(2,p); }
    ScopedAStatus sendScoData(const std::vector<uint8_t>& p) override { return transmit(3,p); }
    ScopedAStatus sendIsoData(const std::vector<uint8_t>& p) override { return transmit(5,p); }
};

int main() {
    // Multiple Binder threads allow close/death while a callback is outstanding.
    // Detached RX sessions keep their own lifetime; close never joins a blocked
    // remote callback. A retiring session prevents unbounded retry threads.
    if (!ABinderProcess_setThreadPoolMaxThreadCount(3)) return 1;
    auto service = ndk::SharedRefBase::make<BluetoothHal>();
    const char* instance = "android.hardware.bluetooth.IBluetoothHci/default";
    if (AServiceManager_addService(service->asBinder().get(), instance) != STATUS_OK) return 1;
    ABinderProcess_startThreadPool();
    note("Linux HCI_USER Bluetooth HAL registered");
    ABinderProcess_joinThreadPool();
    return 1;
}
