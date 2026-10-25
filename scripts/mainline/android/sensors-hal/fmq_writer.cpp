// See fmq_writer.h. Checked against system/libfmq android16-release MessageQueueBase.h
// (beginWrite/commitWrite/availableToReadBytes, grantor order in base/fmq/MQDescriptorBase.h)
// and EventFlag.cpp (wake: fetch_or, FUTEX_WAKE_BITSET only for newly set bits).
#include "fmq_writer.h"

#include <climits>
#include <cstring>
#include <linux/futex.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <unistd.h>

namespace a9sensors {

namespace {
constexpr size_t kRead = 0, kWrite = 1, kData = 2, kFlag = 3;
}

FmqWriter::~FmqWriter() { reset(); }

void FmqWriter::reset() {
    for (auto& m : maps_)
        if (m.base) munmap(m.base, m.len);
    maps_.clear();
    readPtr_ = writePtr_ = nullptr;
    data_ = nullptr;
    efWord_ = nullptr;
    size_ = quantum_ = 0;
}

void* FmqWriter::mapGrantor(const std::vector<int>& fds, const Grantor& g) {
    if (g.fdIndex < 0 || size_t(g.fdIndex) >= fds.size() || g.offset < 0 || g.extent <= 0) return nullptr;
    const long page = sysconf(_SC_PAGESIZE);
    const size_t mapOffset = size_t(g.offset) / size_t(page) * size_t(page);
    const size_t len = size_t(g.offset) - mapOffset + size_t(g.extent);
    void* base = mmap(nullptr, len, PROT_READ | PROT_WRITE, MAP_SHARED, fds[size_t(g.fdIndex)], off_t(mapOffset));
    if (base == MAP_FAILED) return nullptr;
    maps_.push_back({base, len});
    return static_cast<uint8_t*>(base) + (size_t(g.offset) - mapOffset);
}

int FmqWriter::init(const std::vector<int>& fds, const std::vector<Grantor>& g, int32_t quantum, size_t elementSize) {
    reset();
    if (g.size() < 3 || quantum <= 0 || size_t(quantum) != elementSize) return -1;
    if (g[kRead].extent < int64_t(sizeof(uint64_t)) || g[kWrite].extent < int64_t(sizeof(uint64_t))) return -1;
    if (g[kData].extent <= 0 || g[kData].extent % quantum) return -1;
    readPtr_ = static_cast<uint64_t*>(mapGrantor(fds, g[kRead]));
    writePtr_ = static_cast<uint64_t*>(mapGrantor(fds, g[kWrite]));
    data_ = static_cast<uint8_t*>(mapGrantor(fds, g[kData]));
    if (g.size() > kFlag) efWord_ = static_cast<uint32_t*>(mapGrantor(fds, g[kFlag]));
    if (!readPtr_ || !writePtr_ || !data_ || (g.size() > kFlag && !efWord_) ||
        (reinterpret_cast<uintptr_t>(readPtr_) | reinterpret_cast<uintptr_t>(writePtr_)) % alignof(uint64_t)) {
        reset();
        return -1;
    }
    size_ = size_t(g[kData].extent);
    quantum_ = size_t(quantum);
    return 0;
}

int FmqWriter::write(const void* elements, size_t n) {
    if (!data_ || !n) return -1;
    const size_t bytes = n * quantum_;
    const uint64_t w = __atomic_load_n(writePtr_, __ATOMIC_RELAXED);
    const uint64_t r = __atomic_load_n(readPtr_, __ATOMIC_ACQUIRE);
    if (w < r || w - r > size_ || w % quantum_) return -1;   // corrupt or misaligned pointers: refuse (as libfmq)
    if (size_ - (w - r) < bytes) return -1;  // SynchronizedReadWrite: never overwrite unread data
    const size_t off = size_t(w % size_);
    const size_t first = bytes < size_ - off ? bytes : size_ - off;
    std::memcpy(data_ + off, elements, first);
    if (bytes > first) std::memcpy(data_, static_cast<const uint8_t*>(elements) + first, bytes - first);
    __atomic_store_n(writePtr_, w + bytes, __ATOMIC_RELEASE);
    return 0;
}

void FmqWriter::wake(uint32_t bits) {
    if (!efWord_ || !bits) return;
    const uint32_t old = __atomic_fetch_or(efWord_, bits, __ATOMIC_SEQ_CST);
    if (~old & bits) syscall(SYS_futex, efWord_, FUTEX_WAKE_BITSET, INT_MAX, nullptr, nullptr, bits);
}

}  // namespace a9sensors
