// Minimal writer side of an AIDL fast message queue (android.hardware.common.fmq,
// SynchronizedReadWrite) without libfmq, which the mainline vendor does not ship.
// Layout as libfmq (system/libfmq MessageQueueBase): grantor 0 = read pointer, 1 = write
// pointer (std::atomic<uint64_t> byte counters), 2 = ring data, 3 = event-flag word
// (uint32 futex). Writer: refuse when the free space is short (never overwrite unread
// data), copy with wrap-around, publish the write pointer with release order, then
// EventFlag::wake(bits): fetch_or + FUTEX_WAKE_BITSET when a bit was newly set.
#pragma once
#include <cstddef>
#include <cstdint>
#include <vector>

namespace a9sensors {

struct Grantor { int fdIndex; int32_t offset; int64_t extent; };

class FmqWriter {
  public:
    FmqWriter() = default;
    ~FmqWriter();
    FmqWriter(const FmqWriter&) = delete;
    FmqWriter& operator=(const FmqWriter&) = delete;
    // fds are borrowed for mmap only. quantum must equal the element size. 0 ok, -1 refused.
    int init(const std::vector<int>& fds, const std::vector<Grantor>& grantors, int32_t quantum, size_t elementSize);
    // all-or-nothing write of n elements; 0 ok, -1 not enough room (nothing written)
    int write(const void* elements, size_t n);
    void wake(uint32_t bits);
    bool valid() const { return data_ != nullptr; }
    size_t capacityElements() const { return quantum_ ? size_ / quantum_ : 0; }
    void reset();

  private:
    struct Map { void* base = nullptr; size_t len = 0; };
    void* mapGrantor(const std::vector<int>& fds, const Grantor& g);
    std::vector<Map> maps_;
    uint64_t* readPtr_ = nullptr;
    uint64_t* writePtr_ = nullptr;
    uint8_t* data_ = nullptr;
    uint32_t* efWord_ = nullptr;
    size_t size_ = 0, quantum_ = 0;
};

}  // namespace a9sensors
