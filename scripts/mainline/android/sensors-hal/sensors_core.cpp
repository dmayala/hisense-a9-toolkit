// A9 sensors HAL core. See sensors_core.h.
#include "sensors_core.h"

#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstring>
#include <ctime>

namespace a9sensors {

const std::vector<Def>& knownSensors() {
    // Light, from the stock dumpsys sensorservice (runs/stock-dumpsys-sensorservice.txt, C4,
    // MEASURED on slot A 2026-10-18): handle 0x33, "stk_stk3b6x Ambient Light Sensor Non-wakeup",
    // vendor sensortek (we pass the SSC's own attribute, F1), ver 213, type light(5), flags 0x2
    // (on-change, non-wakeup), minDelay 0 us, maxDelay 0 us, FIFO (10000, 0).
    // maxRange / resolution / power are NOT in that dump: they stay INFERRED placeholders
    // (30000 lux, 1.0 lux, 0.1 mA) until read from the stock HAL's getSensorList.
    // Stock non-wakeup names/handles/versions/rate limits. FIFO=0: this HAL delivers
    // immediately, including when a client allows latency. Range/resolution/power for
    // the newly exposed sensors are nominal chip values, pending stock getSensorList.
    static const std::vector<Def> defs = {
        {"accel", kAccelHandle, kTypeAccelerometer, "KXTJ3 Accelerometer Non-wakeup", "Kionix",
         false, false, 131072, 156.9064f, 0.00958f, 0.145f, 2500, 1000000, 0, 0, 50.0f},
        {"mag", kMagHandle, kTypeMagneticField, "ak0991x Magnetometer Non-wakeup", "akm",
         false, false, 146971, 4912.0f, 0.15f, 1.0f, 10000, 1000000, 0, 0, 20.0f},
        {"proximity", kProximityHandle, kTypeProximity, "stk_stk3b6x Proximity Sensor Non-wakeup", "sensortek",
         true, false, 213, 5.0f, 5.0f, 0.1f, 0, 0, 0, 0, 5.0f},
        {"ambient_light", kLightHandle, kTypeLight, "stk_stk3b6x Ambient Light Sensor Non-wakeup", "sensortek",
         /*onChange*/ true, /*wakeUp*/ false, /*version*/ 213, 30000.0f, 1.0f, 0.1f, 0, 0, 0, 0, 5.0f},
    };
    return defs;
}

namespace {
class RealTimeBase : public TimeBase {
  public:
    int64_t boottimeNs() override {
        timespec t{};
        clock_gettime(CLOCK_BOOTTIME, &t);
        return int64_t(t.tv_sec) * 1000000000LL + t.tv_nsec;
    }
    uint64_t ticks() override {
#if defined(__aarch64__)
        uint64_t v;
        asm volatile("isb; mrs %0, cntvct_el0" : "=r"(v));
        return v;
#else
        timespec t{};
        clock_gettime(CLOCK_BOOTTIME, &t);
        return (uint64_t(t.tv_sec) * 1000000000ULL + t.tv_nsec) / 52;   // host stand-in, ~19.2 MHz
#endif
    }
    uint64_t hz() override {
#if defined(__aarch64__)
        uint64_t v;
        asm volatile("mrs %0, cntfrq_el0" : "=r"(v));
        return v ? v : 19200000ULL;
#else
        return 1000000000ULL / 52;
#endif
    }
};
}  // namespace

std::unique_ptr<TimeBase> realTimeBase() { return std::make_unique<RealTimeBase>(); }

int64_t TimestampMapper::map(TimeBase& tb, uint64_t eventTicks) {
    const int64_t now = tb.boottimeNs();
    const uint64_t t = tb.ticks(), hz = tb.hz();
    int64_t ts;
    if (hz && eventTicks <= t) {
        const uint64_t ageTicks = t - eventTicks;
        const long double ageNs = (long double)ageTicks * 1e9L / (long double)hz;
        if (ageNs <= (long double)kMaxAgeNs) {
            ts = now - (int64_t)ageNs;
        } else {
            ts = now;
            ++fallbacks_;
        }
    } else {
        ts = now;   // a tick from the future: the base is not what we think; receive time
        ++fallbacks_;
    }
    if (ts <= last_) ts = last_ + 1;
    if (ts > now) ts = now > last_ ? now : last_ + 1;
    last_ = ts;
    return ts;
}

Core::Core(const s2_link* link, TimeBase* tb, Sink sink, FILE* log)
    : link_(link), tb_(tb), sink_(std::move(sink)), log_(log) {}

Core::~Core() { stopAll(); }

size_t Core::discover(int lookupTimeoutMs, int attrTimeoutMs) {
    std::vector<std::unique_ptr<Slot>> found;
    for (const Def& d : knownSensors()) {
        s2_suid suid{0, 0};
        if (s2_lookup(link_, d.dataType, &suid, lookupTimeoutMs, log_) != 1) {
            fprintf(log_, "S2B-SENSOR dt=%s state=absent\n", d.dataType);
            continue;
        }
        s2_attrs a;
        std::memset(&a, 0, sizeof(a));
        if (s2_attributes(link_, d.dataType, suid, &a, attrTimeoutMs, log_)) {
            fprintf(log_, "S2B-SENSOR dt=%s state=no-attributes\n", d.dataType);
            continue;
        }
        // Review 79j N1: never expose a part we do not expect on this phone.
        if (s2_part_expected(d.dataType, a.name) != 1) {
            fprintf(log_, "S2B-SENSOR dt=%s state=unexpected-part name=\"%s\" expected=%s\n", d.dataType, a.name,
                    s2_part_tokens(d.dataType));
            continue;
        }
        if (a.has_available && !a.available) {
            fprintf(log_, "S2B-SENSOR dt=%s state=unavailable\n", d.dataType);
            continue;
        }
        auto slot = std::make_unique<Slot>();
        slot->s.def = d;
        slot->s.suid = suid;
        slot->s.attrs = a;
        slot->s.onChangeSsc = a.has_stream_type && a.stream_type == 1;
        fprintf(log_, "S2B-SENSOR dt=%s state=exposed handle=%d name=\"%s\" ssc_mode=%s\n", d.dataType, d.handle, a.name,
                slot->s.onChangeSsc ? "on-change" : "streaming");
        found.push_back(std::move(slot));
    }
    std::lock_guard<std::mutex> l(m_);
    for (auto& s : slots_) stopLocked(s.get());
    slots_ = std::move(found);
    return slots_.size();
}

std::vector<Core::Sensor> Core::sensors() const {
    std::lock_guard<std::mutex> l(m_);
    std::vector<Sensor> out;
    for (auto& s : slots_) out.push_back(s->s);
    return out;
}

Core::Slot* Core::find(int handle) {
    for (auto& s : slots_)
        if (s->s.def.handle == handle) return s.get();
    return nullptr;
}
const Core::Slot* Core::find(int handle) const {
    for (auto& s : slots_)
        if (s->s.def.handle == handle) return s.get();
    return nullptr;
}

bool Core::active(int handle) const {
    std::lock_guard<std::mutex> l(m_);
    const Slot* s = find(handle);
    return s && s->active;
}

// Only the stream reader sends control messages on an active connection. It keeps
// receiving queued samples and emits flush metadata only at SSC FLUSH_EVENT, after
// all preceding samples. Responses are consumed by s2_next_events.
int Core::sendControl(Slot* slot, uint32_t msg, const uint8_t* payload, size_t len, bool batching) {
    uint8_t pb[256], frame[288];
    size_t n = s2_client_request(pb, sizeof(pb), slot->s.suid, msg, S2_DELIVERY_NO_WAKEUP,
                                 payload, len, batching, 0);
    n = n ? s2_qmi_request(frame, sizeof(frame), ++slot->conn->txn, pb, n) : 0;
    return n ? link_->send(link_->ctx, slot->conn->h, frame, n) : -1;
}

void Core::readerLoop(Slot* slot) {
    while (!slot->stop.load()) {
        const int64_t period = slot->reconfigureNs.exchange(-1);
        if (period > 0 && !slot->s.onChangeSsc) {
            uint8_t cfg[16];
            size_t n = s2_sensor_config(cfg, sizeof(cfg), float(1e9 / double(period)));
            if (!n || sendControl(slot, S2_MSGID_SENSOR_CONFIG, cfg, n, true)) {
                fprintf(log_, "S2B-STREAM dt=%s state=reconfigure-error\n", slot->s.def.dataType);
                return;
            }
        }
        if (slot->flushPending.load()) {
            if (sendControl(slot, S2_MSGID_FLUSH_REQ, nullptr, 0, false)) {
                fprintf(log_, "S2B-STREAM dt=%s state=flush-send-error\n", slot->s.def.dataType);
                return;
            }
            --slot->flushPending;
            ++slot->flushInFlight;
        }
        s2_event_msg* m = nullptr;
        const int64_t end = link_->now_ms(link_->ctx) + 200;
        const int rc = s2_next_events(link_, slot->conn, end, &m);
        if (rc > 0) continue;
        if (rc < 0) {
            fprintf(log_, "S2B-STREAM dt=%s state=transport-error (sensor stays silent until re-activated)\n",
                    slot->s.def.dataType);
            return;
        }
        std::vector<Ev> out;
        for (size_t i = 0; i < m->n; ++i) {
            const s2_event& e = m->ev[i];
            if (e.msg_id == S2_MSGID_ERROR_EVENT) {
                uint32_t err = 0;
                s2_decode_error_event(e.payload, e.payload_len, &err);
                fprintf(log_, "S2B-STREAM dt=%s sns-error=%u\n", slot->s.def.dataType, err);
                continue;
            }
            Ev ev;
            ev.handle = slot->s.def.handle;
            ev.type = slot->s.def.type;
            if (e.msg_id == S2_MSGID_FLUSH_EVENT && slot->flushInFlight) {
                --slot->flushInFlight;
                ev.flushComplete = true;
                out.push_back(ev);
                continue;
            }
            if (ev.type == kTypeProximity) {
                if (e.msg_id != S2_MSGID_PROXIMITY_EVENT && e.msg_id != S2_MSGID_PROXIMITY_RECURRENT) continue;
                int near; uint32_t status;
                if (s2_decode_proximity_event(e.payload, e.payload_len, &near, &status)) continue;
                ev.n = 1;
                ev.v[0] = near ? 0.0f : slot->s.def.maxRange; // Android binary proximity in cm
                ev.status = int(status);
            } else {
                if (e.msg_id != S2_MSGID_SENSOR_EVENT) continue;
                s2_sensor_event se;
                if (s2_decode_sensor_event(e.payload, e.payload_len, &se)) continue;
                ev.n = ev.type == kTypeLight ? 1 : 3;
                if (se.n < size_t(ev.n)) continue;
                bool ok = true;
                for (int k = 0; k < ev.n; ++k) {
                    if (!std::isfinite(se.data[k])) ok = false;
                    ev.v[k] = se.data[k];
                }
                if (!ok || (ev.type == kTypeLight && ev.v[0] < 0)) continue;
                // sns_accel.proto/sns_mag.proto: SSC output is already Android XYZ,
                // accel m/s^2, mag microtesla. The registry orientation is applied in DSP.
                ev.status = se.has_status && se.status <= 3 ? int(se.status) : 0;
                // mag is factory-calibrated only; no live hard-iron mag_cal correction yet.
                if (ev.type == kTypeMagneticField) ev.status = 0;
            }
            if (slot->s.def.onChange) {
                if (slot->hasLastScalar && slot->lastScalar == ev.v[0]) continue;
                slot->hasLastScalar = true;
                slot->lastScalar = ev.v[0];
            }
            ev.timestampNs = slot->ts.map(*tb_, e.timestamp);
            out.push_back(ev);
        }
        if (!out.empty()) sink_(out);
    }
}

void Core::stopLocked(Slot* slot) {
    if (!slot->active) return;
    slot->stop.store(true);
    if (slot->reader.joinable()) slot->reader.join();
    s2_stream_stop(link_, slot->conn, slot->s.suid, log_);
    slot->conn = nullptr;
    slot->active = false;
}

int Core::activate(int handle, bool on) {
    std::lock_guard<std::mutex> l(m_);
    Slot* s = find(handle);
    if (!s) return -EINVAL;
    if (!on) {
        stopLocked(s);
        return 0;
    }
    if (s->active) return 0;
    const float hz = s->periodNs > 0 ? float(1e9 / double(s->periodNs)) : s->s.def.streamRateHz;
    s2_conn* c = s2_stream_start(link_, s->s.def.dataType, s->s.suid, s->s.onChangeSsc ? 1 : 0, hz, log_);
    if (!c) return -EIO;
    s->conn = c;
    s->stop.store(false);
    s->ts.reset();
    s->reconfigureNs.store(-1);
    s->flushPending.store(0);
    s->flushInFlight = 0;
    s->hasLastScalar = false;
    s->active = true;
    s->reader = std::thread([this, s] { readerLoop(s); });
    return 0;
}

int Core::batch(int handle, int64_t periodNs, int64_t latencyNs) {
    std::lock_guard<std::mutex> l(m_);
    Slot* s = find(handle);
    if (!s || periodNs < 0 || latencyNs < 0) return -EINVAL;
    if (!s->s.def.onChange) {
        periodNs = std::clamp(periodNs, int64_t(s->s.def.minDelayUs) * 1000,
                              int64_t(s->s.def.maxDelayUs) * 1000);
        s->periodNs = periodNs;
        if (s->active) s->reconfigureNs.store(periodNs);
    }
    // FIFO=0: requested maximum latency is satisfied by immediate delivery.
    return 0;
}

int Core::flush(int handle) {
    std::lock_guard<std::mutex> l(m_);
    Slot* s = find(handle);
    if (!s || !s->active) return -EINVAL;
    ++s->flushPending;
    return 0;
}

void Core::stopAll() {
    std::lock_guard<std::mutex> l(m_);
    for (auto& s : slots_) stopLocked(s.get());
}

}  // namespace a9sensors
