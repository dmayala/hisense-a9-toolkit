// FMQ writer against a simulated libfmq SynchronizedReadWrite reader (memfd, both layouts:
// one fd for everything, and a separate data fd at fdIndex 1 as newer libfmq creates).
#include <cassert>
#include <climits>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <linux/futex.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <atomic>
#include <chrono>
#include <thread>
#include <vector>
#include <unistd.h>

#include "fmq_writer.h"

using namespace a9sensors;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #x); std::exit(1); } } while (0)

namespace {
struct Elem { int64_t ts; int32_t handle, type; float v[16]; };   // a fixed-size stand-in for Event
static_assert(sizeof(Elem) % 8 == 0, "");

struct Queue {
    int fd = -1, dataFd = -1; uint8_t* meta = nullptr; uint8_t* data = nullptr; size_t count;
    std::vector<Grantor> g; std::vector<int> fds;
    Queue(size_t n, bool splitData) : count(n) {
        const size_t page = size_t(sysconf(_SC_PAGESIZE));
        fd = int(syscall(SYS_memfd_create, "fmq", 0)); CHECK(fd >= 0);
        const size_t dataBytes = n * sizeof(Elem);
        if (splitData) {
            dataFd = int(syscall(SYS_memfd_create, "fmqdata", 0)); CHECK(dataFd >= 0);
            CHECK(!ftruncate(fd, long(page)) && !ftruncate(dataFd, long(dataBytes)));
            fds = {fd, dataFd};
            g = {{0, 0, 8}, {0, 64, 8}, {1, 0, int64_t(dataBytes)}, {0, 128, 4}};
            data = static_cast<uint8_t*>(mmap(nullptr, dataBytes, PROT_READ | PROT_WRITE, MAP_SHARED, dataFd, 0));
        } else {
            CHECK(!ftruncate(fd, long(2 * page + dataBytes)));
            fds = {fd};
            g = {{0, 0, 8}, {0, 64, 8}, {0, int32_t(page + 24), int64_t(dataBytes)}, {0, 128, 4}};   // data at an unaligned offset
        }
        meta = static_cast<uint8_t*>(mmap(nullptr, splitData ? page : 2 * page + dataBytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0));
        CHECK(meta != MAP_FAILED);
        if (!splitData) data = meta + page + 24;
        CHECK(data != MAP_FAILED);
    }
    uint64_t& rd() { return *reinterpret_cast<uint64_t*>(meta); }
    uint64_t& wr() { return *reinterpret_cast<uint64_t*>(meta + 64); }
    uint32_t& flag() { return *reinterpret_cast<uint32_t*>(meta + 128); }
    // libfmq reader: read k elements at the read pointer
    std::vector<Elem> read(size_t k) {
        std::vector<Elem> out(k);
        const size_t size = count * sizeof(Elem);
        for (size_t i = 0; i < k; i++) {
            const size_t off = (rd() + i * sizeof(Elem)) % size;
            memcpy(&out[i], data + off, sizeof(Elem));   // element-aligned: never split inside one element here
        }
        __atomic_store_n(&rd(), rd() + k * sizeof(Elem), __ATOMIC_RELEASE);
        return out;
    }
};
Elem mk(int i) { Elem e{}; e.ts = 1000 + i; e.handle = 1; e.type = 5; e.v[0] = float(i); return e; }

void run(bool split) {
    Queue q(4, split);
    FmqWriter w;
    CHECK(w.init(q.fds, q.g, int32_t(sizeof(Elem)), sizeof(Elem)) == 0 && w.valid() && w.capacityElements() == 4);
    Elem a[3] = {mk(0), mk(1), mk(2)};
    CHECK(w.write(a, 3) == 0 && q.wr() == 3 * sizeof(Elem));
    Elem b[2] = {mk(3), mk(4)};
    CHECK(w.write(b, 2) == -1 && q.wr() == 3 * sizeof(Elem));   // only 1 free: refused, nothing written
    auto r = q.read(2);
    CHECK(r[0].ts == 1000 && r[1].v[0] == 1.0f);
    CHECK(w.write(b, 2) == 0);                                    // wraps around the ring end
    CHECK(q.wr() == 5 * sizeof(Elem));
    r = q.read(3);
    CHECK(r[0].ts == 1002 && r[1].ts == 1003 && r[2].ts == 1004);
    // event flag: bits are or-ed in; a waiter on the bit is woken
    q.flag() = 0;
    std::atomic<int> woke{0};
    std::thread waiter([&] {
        long rc = syscall(SYS_futex, &q.flag(), FUTEX_WAIT_BITSET, 0u, nullptr, nullptr, 1u);
        (void)rc; woke = 1;
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    w.wake(1);
    waiter.join();
    CHECK(woke == 1 && (q.flag() & 1));
    // a misaligned write pointer is refused (libfmq beginWrite)
    { uint64_t keep = q.wr(); q.wr() = keep + 1; CHECK(w.write(a, 1) == -1); q.wr() = keep; }
    // corrupt pointers are refused, never written through
    q.wr() = q.rd() + 100 * sizeof(Elem);
    CHECK(w.write(a, 1) == -1);
}
}  // namespace

int main() {
    run(false);
    run(true);
    // descriptor refusals
    Queue q(4, false);
    FmqWriter w;
    CHECK(w.init(q.fds, q.g, int32_t(sizeof(Elem)) + 4, sizeof(Elem)) == -1);   // quantum != element size
    auto bad = q.g; bad[2].extent = int64_t(sizeof(Elem)) * 4 + 1;
    CHECK(w.init(q.fds, bad, int32_t(sizeof(Elem)), sizeof(Elem)) == -1);        // data not a multiple of quantum
    bad = q.g; bad[0].fdIndex = 3;
    CHECK(w.init(q.fds, bad, int32_t(sizeof(Elem)), sizeof(Elem)) == -1);        // fd index out of range
    CHECK(w.init(q.fds, {q.g[0], q.g[1]}, int32_t(sizeof(Elem)), sizeof(Elem)) == -1);   // too few grantors
    CHECK(!w.valid() && w.write(&q, 1) == -1);
    puts("PASS fmq: write/refuse-full/wrap/wake/corrupt-pointer, one-fd and split-data layouts, descriptor refusals");
    return 0;
}
