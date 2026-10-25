// A9 sensors HAL core: SNS (QMI 400) client side, independent of binder/AIDL so it
// is host-testable. Discovery (SUID lookup + attributes + expected-part check),
// per-sensor stream threads, event conversion, flush, timestamp mapping.
// Sensor set: light, accelerometer, magnetic field and proximity. Wire protocol: scripts/mainline/sensors/s2-*.
#pragma once
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "s2-client.h"

namespace a9sensors {

// android.hardware.sensors SensorType values used here
constexpr int kTypeAccelerometer = 1;
constexpr int kTypeMagneticField = 2;
constexpr int kTypeLight = 5;
constexpr int kTypeProximity = 8;
// Handles as the stock HAL numbers them (runs/stock-dumpsys-sensorservice.txt, slot A, C4):
// light non-wakeup 0x33. Keeping stock's handles keeps per-handle tooling/logs comparable.
constexpr int kAccelHandle = 0x0b;
constexpr int kMagHandle = 0x15;
constexpr int kLightHandle = 0x33;
constexpr int kProximityHandle = 0x51;
// SensorInfo flag bits
constexpr int kFlagWakeUp = 1;
constexpr int kFlagOnChange = 2;   // ON_CHANGE reporting mode (1 << SHIFT_REPORTING_MODE)

struct Def {
    const char* dataType;   // SNS data type ("ambient_light")
    int handle;
    int type;
    const char* name;       // stock sensors.ssc name, so overlays and dumps read the same
    const char* vendor;
    bool onChange;
    bool wakeUp;
    int32_t version;        // stock SensorInfo.version
    float maxRange, resolution, powerMa;
    int32_t minDelayUs, maxDelayUs;
    int32_t fifoMax, fifoReserved;
    float streamRateHz;     // used when the SSC reports the sensor as streaming
};
// Expose only sensors discovered on the SSC; handles match the stock non-wakeup sensors.
const std::vector<Def>& knownSensors();

struct Ev {
    int64_t timestampNs = 0;
    int handle = 0;
    int type = 0;
    int n = 0;
    float v[4] = {0, 0, 0, 0};
    int status = 0;               // SNS and Android accuracy enums both use 0..3
    bool flushComplete = false;   // META_DATA FLUSH_COMPLETE for `handle`
};

class TimeBase {
  public:
    virtual ~TimeBase() = default;
    virtual int64_t boottimeNs() = 0;
    virtual uint64_t ticks() = 0;   // the DSP timestamp base (QTimer / arch counter)
    virtual uint64_t hz() = 0;
};
std::unique_ptr<TimeBase> realTimeBase();   // CLOCK_BOOTTIME + cntvct_el0/cntfrq_el0 (aarch64)

// DSP-tick -> BOOTTIME mapping by sample AGE, never by an absolute anchor (A6L lesson: a
// backlog must not be anchored as "now"). Monotonic per sensor, never in the future.
// Ages beyond kMaxAgeNs or ticks in the future fall back to the receive time.
class TimestampMapper {
  public:
    static constexpr int64_t kMaxAgeNs = 10'000'000'000LL;
    int64_t map(TimeBase& tb, uint64_t eventTicks);
    void reset() { last_ = 0; }
    int64_t fallbacks() const { return fallbacks_; }
  private:
    int64_t last_ = 0;
    int64_t fallbacks_ = 0;
};

class Core {
  public:
    using Sink = std::function<void(const std::vector<Ev>&)>;
    Core(const s2_link* link, TimeBase* tb, Sink sink, FILE* log);
    ~Core();
    Core(const Core&) = delete;
    Core& operator=(const Core&) = delete;

    // SUID lookup + attributes + expected-part check for every known sensor. Returns the count exposed.
    size_t discover(int lookupTimeoutMs, int attrTimeoutMs);
    struct Sensor { Def def; s2_suid suid; s2_attrs attrs; bool onChangeSsc; };
    std::vector<Sensor> sensors() const;

    int activate(int handle, bool on);              // 0, -EINVAL (unknown), -EIO (SSC refused)
    int batch(int handle, int64_t periodNs, int64_t latencyNs);
    int flush(int handle);                          // -EINVAL if unknown or not active
    void stopAll();
    bool active(int handle) const;

  private:
    struct Slot {
        Sensor s;
        bool active = false;
        int64_t periodNs = 0;
        s2_conn* conn = nullptr;
        std::thread reader;
        std::atomic<bool> stop{false};
        TimestampMapper ts;
        std::atomic<int64_t> reconfigureNs{-1};
        std::atomic<unsigned> flushPending{0};
        unsigned flushInFlight = 0;   // reader thread only
        float lastScalar = 0;
        bool hasLastScalar = false;
    };
    Slot* find(int handle);
    const Slot* find(int handle) const;
    void readerLoop(Slot* slot);
    void stopLocked(Slot* slot);
    int sendControl(Slot* slot, uint32_t msg, const uint8_t* payload, size_t len, bool batching);

    const s2_link* link_;
    TimeBase* tb_;
    Sink sink_;
    FILE* log_;
    mutable std::mutex m_;   // guards slots_ structure and activation state
    std::vector<std::unique_ptr<Slot>> slots_;
};

}  // namespace a9sensors
