// Host tests of sensors_core (no phone, no NDK): a fake SSC behind the s2_link transport.
// The wire encoders/decoders themselves are pinned byte-exactly by tests/mainline-sensors/test-s2-sns.py;
// here the fake SSC parses the requests with its own minimal protobuf reader.
#include <cassert>
#include <cerrno>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <deque>
#include <map>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "sensors_core.h"

using namespace a9sensors;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #x); std::exit(1); } } while (0)

namespace {
using Bytes = std::vector<uint8_t>;
// ---- tiny independent protobuf helpers for the fake SSC ----
void varint(Bytes& b, uint64_t v) { do { uint8_t c = v & 0x7f; v >>= 7; b.push_back(c | (v ? 0x80 : 0)); } while (v); }
void key(Bytes& b, unsigned f, unsigned w) { varint(b, (uint64_t(f) << 3) | w); }
void ld(Bytes& b, unsigned f, const Bytes& v) { key(b, f, 2); varint(b, v.size()); b.insert(b.end(), v.begin(), v.end()); }
void ld(Bytes& b, unsigned f, const std::string& s) { ld(b, f, Bytes(s.begin(), s.end())); }
void fx32(Bytes& b, unsigned f, uint32_t v) { key(b, f, 5); for (int i = 0; i < 4; i++) b.push_back(uint8_t(v >> (8 * i))); }
void fx64(Bytes& b, unsigned f, uint64_t v) { key(b, f, 1); for (int i = 0; i < 8; i++) b.push_back(uint8_t(v >> (8 * i))); }
void flt(Bytes& b, unsigned f, float v) { uint32_t u; memcpy(&u, &v, 4); fx32(b, f, u); }
void vint(Bytes& b, unsigned f, uint64_t v) { key(b, f, 0); varint(b, v); }
struct Field { unsigned f, w; uint64_t v; Bytes d; };
std::vector<Field> parse(const uint8_t* p, size_t n) {
    std::vector<Field> out; size_t i = 0;
    auto rv = [&](uint64_t& v) { v = 0; for (unsigned s = 0; s < 64; s += 7) { CHECK(i < n); uint8_t c = p[i++]; v |= uint64_t(c & 0x7f) << s; if (!(c & 0x80)) return; } CHECK(false); };
    while (i < n) {
        uint64_t k; rv(k); Field fd{unsigned(k >> 3), unsigned(k & 7), 0, {}};
        if (fd.w == 0) rv(fd.v);
        else if (fd.w == 1) { CHECK(n - i >= 8); for (int j = 0; j < 8; j++) fd.v |= uint64_t(p[i + j]) << (8 * j); i += 8; }
        else if (fd.w == 5) { CHECK(n - i >= 4); for (int j = 0; j < 4; j++) fd.v |= uint64_t(p[i + j]) << (8 * j); i += 4; }
        else if (fd.w == 2) { uint64_t l; rv(l); CHECK(l <= n - i); fd.d.assign(p + i, p + i + l); i += l; }
        else CHECK(false);
        out.push_back(fd);
    }
    return out;
}
const Field* get(const std::vector<Field>& v, unsigned f) { for (auto& x : v) if (x.f == f) return &x; return nullptr; }
Bytes tlv(uint8_t t, const Bytes& v) { Bytes b{t, uint8_t(v.size()), uint8_t(v.size() >> 8)}; b.insert(b.end(), v.begin(), v.end()); return b; }
Bytes qmi(uint8_t kind, uint16_t txn, uint16_t msg, const Bytes& body) {
    Bytes b{kind, uint8_t(txn), uint8_t(txn >> 8), uint8_t(msg), uint8_t(msg >> 8), uint8_t(body.size()), uint8_t(body.size() >> 8)};
    b.insert(b.end(), body.begin(), body.end()); return b;
}
Bytes suidMsg(uint64_t lo, uint64_t hi) { Bytes b; fx64(b, 1, lo); fx64(b, 2, hi); return b; }

constexpr uint64_t ALS_LO = 0x1111, ALS_HI = 0x2222;

// ---- the fake SSC ----
struct FakeSsc {
    std::mutex m; std::condition_variable cv;
    std::map<int, std::deque<Bytes>> inbox;   // replies per connection handle
    int next = 0;
    std::vector<std::pair<int, uint32_t>> requests;   // (handle, msg_id)
    // F1: the SSC sends attribute strings NUL-terminated (S2a printed "stk_stk3b6x?" before the fix)
    std::string alsName = std::string("stk_stk3b6x\0", 12);
    bool alsAvailable = true, failConfig = false, motion = false;
    std::map<uint64_t, std::string> names{{ALS_LO, "ambient_light"}, {0x3333, "accel"}, {0x4444, "mag"}, {0x5555, "proximity"}};
    std::map<std::string, int> streams;
    std::vector<float> rates;
    int streamHandle = -1; uint64_t clientBase = 0x100;

    void push(int h, Bytes b) { inbox[h].push_back(std::move(b)); cv.notify_all(); }
    Bytes ind(int h, const Bytes& eventMsg) {
        Bytes body = tlv(1, {uint8_t(clientBase + h), 0, 0, 0, 0, 0, 0, 0});
        Bytes arr{uint8_t(eventMsg.size()), uint8_t(eventMsg.size() >> 8)}; arr.insert(arr.end(), eventMsg.begin(), eventMsg.end());
        Bytes t2 = tlv(2, arr); body.insert(body.end(), t2.begin(), t2.end());
        return qmi(4, 0, 0x21, body);
    }
    Bytes resp(int h, uint16_t txn, uint16_t result = 0) {
        Bytes body = tlv(2, {uint8_t(result), 0, 0, 0});
        Bytes cid = tlv(0x10, {uint8_t(clientBase + h), 0, 0, 0, 0, 0, 0, 0}); body.insert(body.end(), cid.begin(), cid.end());
        return qmi(2, txn, 0x20, body);
    }
    Bytes event(uint64_t lo, uint64_t hi, uint32_t msgId, uint64_t ts, const Bytes& payload) {
        Bytes ev; fx32(ev, 1, msgId); fx64(ev, 2, ts); ld(ev, 3, payload);
        Bytes m; ld(m, 1, suidMsg(lo, hi)); ld(m, 2, ev); return m;
    }
    void onSend(int h, const uint8_t* b, size_t n) {
        CHECK(n >= 7 && b[0] == 0 && b[3] == 0x20);
        uint16_t txn = uint16_t(b[1] | b[2] << 8);
        CHECK(b[7] == 0x01);
        size_t len = size_t(b[10] | b[11] << 8);
        auto msg = parse(b + 12, len);
        uint32_t id = uint32_t(get(msg, 2)->v);
        auto uid = parse(get(msg, 1)->d.data(), get(msg, 1)->d.size());
        uint64_t lo = get(uid, 1)->v, hi = get(uid, 2)->v;
        const std::string dtForSuid = names.count(lo) ? names.at(lo) : "";
        std::lock_guard<std::mutex> l(m);
        requests.push_back({h, id});
        if (id == 512) {   // SUID lookup
            auto r = parse(get(msg, 4)->d.data(), get(msg, 4)->d.size());
            auto q = parse(get(r, 2)->d.data(), get(r, 2)->d.size());
            std::string dt(get(q, 1)->d.begin(), get(q, 1)->d.end());
            push(h, resp(h, txn));
            Bytes se; ld(se, 1, dt);
            if (dt == "ambient_light") ld(se, 2, suidMsg(ALS_LO, ALS_HI));
            else if (motion) for (const auto& [id, name] : names) if (dt == name) ld(se, 2, suidMsg(id, ALS_HI));
            push(h, ind(h, event(0xababababababababull, 0xababababababababull, 768, 1, se)));
        } else if (id == 1) {   // attributes
            push(h, resp(h, txn));
            auto attr = [](int aid, const Bytes& data) { Bytes v; ld(v, 1, data); Bytes a; vint(a, 1, aid); ld(a, 2, v); Bytes o; ld(o, 1, a); return o; };
            Bytes ev, d;
            const std::string name = dtForSuid == "accel" ? "KXTJ3" : dtForSuid == "mag" ? "ak0991x" : alsName;
            d.clear(); ld(d, 2, name); { auto x = attr(0, d); ev.insert(ev.end(), x.begin(), x.end()); }
            d.clear(); ld(d, 2, std::string("sensortek\0", 10)); { auto x = attr(1, d); ev.insert(ev.end(), x.begin(), x.end()); }
            d.clear(); vint(d, 5, alsAvailable ? 1 : 0); { auto x = attr(3, d); ev.insert(ev.end(), x.begin(), x.end()); }
            d.clear(); key(d, 4, 1); for (int i = 0; i < 8; i++) d.push_back(i == 0 && (dtForSuid == "ambient_light" || dtForSuid == "proximity") ? 1 : 0); { auto x = attr(16, d); ev.insert(ev.end(), x.begin(), x.end()); }
            push(h, ind(h, event(lo, hi, 128, 2, ev)));
        } else if (id == 514 || id == 513) {
            push(h, resp(h, txn, failConfig ? 1 : 0));
            if (!failConfig) {
                if (dtForSuid == "ambient_light") streamHandle = h;
                streams[dtForSuid] = h;
                if (id == 513) {
                    auto req = parse(get(msg, 4)->d.data(), get(msg, 4)->d.size());
                    auto cfg = parse(get(req, 2)->d.data(), get(req, 2)->d.size());
                    uint32_t bits = uint32_t(get(cfg, 1)->v); float rate; memcpy(&rate, &bits, 4);
                    rates.push_back(rate);
                }
            }
        } else if (id == 2) {
            push(h, resp(h, txn));
            push(h, ind(h, event(lo, hi, 129, 0, {})));
        } else if (id == 10) {
            push(h, resp(h, txn));
            streamHandle = -1;
        }
    }
    void emitLux(float lux, uint64_t ticks) {
        std::lock_guard<std::mutex> l(m);
        CHECK(streamHandle >= 0);
        Bytes p; flt(p, 1, lux); vint(p, 2, 3);
        push(streamHandle, ind(streamHandle, event(ALS_LO, ALS_HI, 1025, ticks, p)));
    }
    void emit(const std::string& dt, std::initializer_list<float> values, uint64_t ticks) {
        std::lock_guard<std::mutex> l(m);
        int h = streams.at(dt);
        uint64_t lo = 0; for (const auto& [id, name] : names) if (dt == name) lo = id;
        Bytes p;
        uint32_t msgId = 1025;
        if (dt == "proximity") { vint(p, 1, uint64_t(*values.begin())); vint(p, 2, 123); vint(p, 3, 3); msgId = 769; }
        else { for (float v : values) flt(p, 1, v); vint(p, 2, 3); }
        push(h, ind(h, event(lo, ALS_HI, msgId, ticks, p)));
    }
    size_t count(uint32_t id) { std::lock_guard<std::mutex> l(m); size_t n = 0; for (auto& r : requests) if (r.second == id) n++; return n; }
};
FakeSsc* g;

int64_t monoMs(void*) { return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
int fOpen(void*) { std::lock_guard<std::mutex> l(g->m); return g->next++; }
int fSend(void*, int h, const uint8_t* b, size_t n) { g->onSend(h, b, n); return 0; }
int fRecv(void*, int h, uint8_t* b, size_t cap, int timeoutMs) {
    std::unique_lock<std::mutex> l(g->m);
    if (!g->cv.wait_for(l, std::chrono::milliseconds(timeoutMs), [&] { return !g->inbox[h].empty(); })) return 0;
    Bytes x = std::move(g->inbox[h].front()); g->inbox[h].pop_front();
    CHECK(x.size() <= cap); memcpy(b, x.data(), x.size()); return int(x.size());
}
void fClose(void*, int) {}

struct FakeTime : TimeBase {
    int64_t boot = 1'000'000'000'000LL; uint64_t t = 19'200'000ULL * 100;   // 100 s of ticks
    int64_t boottimeNs() override { return boot; }
    uint64_t ticks() override { return t; }
    uint64_t hz() override { return 19'200'000ULL; }
};

struct Sink {
    std::mutex m; std::condition_variable cv; std::vector<Ev> got;
    void operator()(const std::vector<Ev>& e) { std::lock_guard<std::mutex> l(m); got.insert(got.end(), e.begin(), e.end()); cv.notify_all(); }
    bool waitFor(size_t n) { std::unique_lock<std::mutex> l(m); return cv.wait_for(l, std::chrono::seconds(3), [&] { return got.size() >= n; }); }
};

void testTimestamps() {
    FakeTime tb; TimestampMapper m;
    // a sample 19.2e6 ticks (1 s) old maps to now - 1 s
    CHECK(m.map(tb, tb.t - 19'200'000ULL) == tb.boot - 1'000'000'000LL);
    // an older sample than the last mapped one is clamped forward (monotonic)
    int64_t a = m.map(tb, tb.t - 2 * 19'200'000ULL);
    CHECK(a == tb.boot - 1'000'000'000LL + 1);
    // a tick from the future -> receive time, counted as fallback
    tb.boot += 5'000'000'000LL; tb.t += 5ULL * 19'200'000ULL;
    CHECK(m.map(tb, tb.t + 1000) == tb.boot && m.fallbacks() == 1);
    // an age beyond 10 s -> receive time (+1 for monotonicity), fallback
    tb.boot += 1; CHECK(m.map(tb, tb.t - 11ULL * 19'200'000ULL) == tb.boot && m.fallbacks() == 2);
    // never in the future
    for (int i = 0; i < 5; i++) CHECK(m.map(tb, tb.t) <= tb.boot + 5);
    puts("PASS timestamps: age mapping, monotonic clamp, future/old fallback");
}
}  // namespace

int main() {
    testTimestamps();
    s2_link L{fOpen, fSend, fRecv, fClose, monoMs, nullptr};
    FakeTime tb;
    FILE* log = stdout;
    {   // discovery + stream + flush + stop
        FakeSsc ssc; g = &ssc; Sink sink;
        Core core(&L, &tb, [&](const std::vector<Ev>& e) { sink(e); }, log);
        CHECK(core.discover(500, 500) == 1);
        auto s = core.sensors();
        CHECK(s.size() == 1 && s[0].def.handle == kLightHandle && s[0].def.version == 213 && s[0].def.maxDelayUs == 0 && s[0].def.fifoMax == 0 && s[0].def.type == kTypeLight && s[0].onChangeSsc);
        CHECK(std::string(s[0].attrs.vendor) == "sensortek");     // F1: one trailing NUL dropped, no '?'
        CHECK(std::string(s[0].attrs.name) == "stk_stk3b6x");
        CHECK(core.flush(kLightHandle) == -EINVAL);                       // not active
        CHECK(core.activate(7, true) == -EINVAL);              // unknown handle
        CHECK(core.activate(kLightHandle, true) == 0 && ssc.count(514) == 1 && ssc.count(513) == 0);
        CHECK(core.activate(kLightHandle, true) == 0 && ssc.count(514) == 1);   // idempotent
        ssc.emitLux(123.5f, tb.t - 19'200'000ULL);
        ssc.emitLux(-1.0f, tb.t);                              // dropped (negative lux)
        ssc.emitLux(NAN, tb.t);                                // dropped (not finite)
        ssc.emitLux(80.0f, tb.t);
        CHECK(sink.waitFor(2));
        std::this_thread::sleep_for(std::chrono::milliseconds(300));
        {
            std::lock_guard<std::mutex> l(sink.m);
            CHECK(sink.got.size() == 2);
            CHECK(sink.got[0].handle == kLightHandle && sink.got[0].type == kTypeLight && sink.got[0].v[0] == 123.5f);
            CHECK(sink.got[0].timestampNs == tb.boot - 1'000'000'000LL);
            CHECK(sink.got[1].v[0] == 80.0f && sink.got[1].timestampNs > sink.got[0].timestampNs);
        }
        CHECK(core.flush(kLightHandle) == 0 && sink.waitFor(3));
        { std::lock_guard<std::mutex> l(sink.m); CHECK(sink.got[2].flushComplete && sink.got[2].handle == kLightHandle); }
        CHECK(core.batch(kLightHandle, 200'000'000, 0) == 0 && core.batch(kLightHandle, -1, 0) == -EINVAL);
        CHECK(core.activate(kLightHandle, false) == 0 && ssc.count(10) == 1 && !core.active(kLightHandle));
        CHECK(core.flush(kLightHandle) == -EINVAL);
        puts("PASS core: discover, on-change config, lux conversion and filtering, flush, batch, disable");
    }
    {   // N1: an unexpected part is never exposed
        FakeSsc ssc; g = &ssc; ssc.alsName = "tsl2772"; Sink sink;
        Core core(&L, &tb, [&](const std::vector<Ev>& e) { sink(e); }, log);
        CHECK(core.discover(500, 500) == 0 && core.sensors().empty() && ssc.count(514) == 0);
        puts("PASS core: unexpected part refused (review 79j N1)");
    }
    {   // unavailable sensor, and a refused config
        FakeSsc ssc; g = &ssc; ssc.alsAvailable = false; Sink sink;
        Core core(&L, &tb, [&](const std::vector<Ev>& e) { sink(e); }, log);
        CHECK(core.discover(500, 500) == 0);
        FakeSsc ssc2; g = &ssc2; ssc2.failConfig = true;
        Core core2(&L, &tb, [&](const std::vector<Ev>& e) { sink(e); }, log);
        CHECK(core2.discover(500, 500) == 1 && core2.activate(kLightHandle, true) == -EIO && !core2.active(kLightHandle));
        puts("PASS core: unavailable not exposed; SSC-refused config -> -EIO, stays inactive");
    }
    {   // destruction with an active stream stops it (disable sent)
        FakeSsc ssc; g = &ssc; Sink sink;
        {
            Core core(&L, &tb, [&](const std::vector<Ev>& e) { sink(e); }, log);
            CHECK(core.discover(500, 500) == 1 && core.activate(kLightHandle, true) == 0);
        }
        CHECK(ssc.count(10) == 1);
        puts("PASS core: destructor disables active streams");
    }
    {   // B2: all four discovered parts, real 3-axis payloads, near/far, rate update and SSC flush.
        FakeSsc ssc; g = &ssc; ssc.motion = true; Sink sink;
        Core core(&L, &tb, [&](const std::vector<Ev>& e) { sink(e); }, log);
        CHECK(core.discover(100, 100) == 4);
        CHECK(core.batch(kAccelHandle, 20'000'000, 0) == 0);
        CHECK(core.activate(kAccelHandle, true) == 0);
        CHECK(core.activate(kMagHandle, true) == 0);
        CHECK(core.activate(kProximityHandle, true) == 0);
        ssc.emit("accel", {0.1f, -0.2f, 9.8f}, tb.t);
        ssc.emit("accel", {0.1f, 0.2f}, tb.t); // incomplete XYZ must be ignored
        ssc.emit("accel", {NAN, 0, 1}, tb.t);
        ssc.emit("mag", {12, 25, -36}, tb.t);
        ssc.emit("proximity", {0}, tb.t);  // far
        ssc.emit("proximity", {0}, tb.t);  // duplicate on-change suppressed
        ssc.emit("proximity", {1}, tb.t);  // near
        CHECK(sink.waitFor(4));
        CHECK(core.flush(kAccelHandle) == 0 && sink.waitFor(5));
        CHECK(core.batch(kAccelHandle, 10'000'000, 0) == 0);
        std::this_thread::sleep_for(std::chrono::milliseconds(300));
        core.stopAll();
        {
            std::lock_guard<std::mutex> l(sink.m);
            CHECK(sink.got.size() == 5);
            int accel = 0, mag = 0, near = 0, far = 0, flush = 0;
            bool sawAccel = false;
            for (const auto& e : sink.got) {
                if (e.flushComplete) { CHECK(sawAccel && e.handle == kAccelHandle); ++flush; }
                else if (e.type == kTypeAccelerometer) {
                    CHECK(e.n == 3 && e.v[0] == 0.1f && e.v[1] == -0.2f && e.v[2] == 9.8f && e.status == 3);
                    ++accel; sawAccel = true;
                } else if (e.type == kTypeMagneticField) {
                    CHECK(e.n == 3 && e.v[0] == 12 && e.v[1] == 25 && e.v[2] == -36 && e.status == 0); ++mag;
                } else if (e.type == kTypeProximity) { if (e.v[0] == 0) ++near; else if (e.v[0] == 5) ++far; }
            }
            CHECK(accel == 1 && mag == 1 && near == 1 && far == 1 && flush == 1);
        }
        CHECK(ssc.count(2) == 1 && ssc.count(513) == 3);
        CHECK(ssc.rates.size() == 3 && ssc.rates[0] == 50 && ssc.rates[1] == 20 && ssc.rates[2] == 100);
        puts("PASS B2: four parts, Android XYZ/units/status, near/far, filtering, live rate update and ordered SSC flush");
    }
    puts("SENSORS-HAL CORE TESTS PASS");
    return 0;
}
