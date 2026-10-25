// A9 thermal HAL core: config, sysfs sources, severities, cooling devices.
// No binder here, so the host tests (tests/run-tests.sh) exercise all of it
// against a fake sysfs tree. Design follows the Hisense A6L thermal HAL
// (device/hisense/a6l/rom/r6/thermal, docs/android-thermal-*.md): per-sensor
// sources with alternatives, hot thresholds per ThrottlingSeverity, cooling
// devices from /sys/class/thermal/cooling_device*. Unlike the A6L HAL this one
// never writes sysfs: mitigation is the kernel's (cooling-maps, a9-thermal.dtsi).
//
// Review 24 semantics (2026-10-16):
//  - a reading outside its source's physical range is a SENSOR FAULT, not a
//    temperature: tsens zone -40..150 C, ADC thermistor (iio) strictly inside
//    the qcom-vadc 100k table ends (-40, 125) C, because the table CLAMPS an
//    open/short input to its endpoint and reports success; files -40..125 C.
//  - a severity INCREASE needs kConfirm consecutive valid samples (re-read every
//    kConfirmMs while pending); the committed level is the lowest of them, so
//    one bad sample never raises (let alone reaches SHUTDOWN). Decreases apply
//    at once (with hysteresis).
//  - a fault clears the sensor to value NaN / severity NONE, notifies, and
//    keeps the sensor listed; a sensor once valid ("exists") is never removed,
//    nor are its static thresholds; cooling devices once seen stay listed.
//  - no source substitution: each sensor lists only sources measuring the
//    same physical point (skin no longer falls back to the battery).
#pragma once

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace a9thermal {

// ThrottlingSeverity NONE..SHUTDOWN (android.hardware.thermal V3)
constexpr int kSeverities = 7;
constexpr int kConfirm = 3;          // consecutive samples for any severity increase
constexpr int kConfirmMs = 1000;     // re-read period while an increase is pending
// TemperatureType values used here (android.hardware.thermal V3)
enum Type : int {
    T_UNKNOWN = -1, T_CPU = 0, T_GPU = 1, T_BATTERY = 2, T_SKIN = 3, T_USB_PORT = 4,
    T_POWER_AMPLIFIER = 5, T_NPU = 9, T_DISPLAY = 11, T_MODEM = 12, T_SOC = 13, T_WIFI = 14, T_CAMERA = 15,
    T_AMBIENT = 18,
};
// CoolingType values used here
enum CoolType : int { C_BATTERY = 1, C_CPU = 2, C_GPU = 3, C_MODEM = 4, C_COMPONENT = 6, C_DISPLAY = 9 };

bool parseType(const std::string& s, int* out);

struct Source {
    std::string kind;   // "zone", "adczone" (zone on an ADC thermistor), "file", "iio"
    std::string arg;    // zone type, absolute path (under root), or IIO channel label
    double scale = 1;   // raw -> degC
};

struct SensorCfg {
    std::string name;
    int type = T_UNKNOWN;
    std::vector<Source> sources;   // first one present wins
    float hot[kSeverities];        // NAN = unset; index 0 (NONE) always NAN
    int intervalS = 5;             // minimum seconds between reads
};

// "name type src[|src..] scale t1 t2 t3 t4 t5 t6 [interval_s]"; '#' comments.
// src = zone:<type> | file:<path> | iio:<label>, optional "@<scale>" suffix.
// Returns false and sets *err for a bad line (line number included).
bool parseConfig(const std::string& text, std::vector<SensorCfg>* out, std::string* err);

struct Reading {
    bool valid = false;
    float celsius = 0;
    std::string path;   // resolved sysfs path
};

class Sysfs {
  public:
    explicit Sysfs(std::string root = "") : root_(std::move(root)) {}
    // resolve a source to a readable path ("" if absent)
    std::string resolve(const Source& s) const;
    bool readLong(const std::string& path, long long* v) const;
    bool readString(const std::string& path, std::string* v) const;
    std::vector<std::string> listDir(const std::string& dir) const;   // names, sorted
    const std::string& root() const { return root_; }

  private:
    std::string root_;
};

// physical range of a source kind (degC); outside = sensor fault
void rangeFor(const std::string& kind, double* lo, double* hi, bool* exclusive);

// Severity with hysteresis: rising when value >= hot[s]; falling only when the
// value drops below hot[s] - hyst. `prev` is the previous severity.
int severityFor(const float hot[kSeverities], float celsius, int prev, float hyst);

struct SensorState {
    SensorCfg cfg;
    Reading last;
    int severity = 0;            // committed (published) severity
    int64_t lastReadMs = -1;
    std::string resolvedFrom;    // source spec that resolved
    bool published = false;      // was valid once: listed forever
    bool fault = false;          // last read failed or was out of range
    std::string faultWhy;
    std::vector<int> pending;    // raw severities above `severity`, newest last
    bool faultNotified = false;  // fault state last reported to callbacks (starts "valid")
};

struct PubTemp { std::string name; int type; float value; int severity; };
struct PubThreshold { std::string name; int type; float hot[kSeverities]; };

struct Cooling {
    std::string name;   // kernel type, e.g. cpufreq-cpu4
    int type = C_COMPONENT;
    long long cur = 0, max = 0;
    std::string dir;
    bool present = true;   // false: gone since; kept listed with its last value
};

int coolingTypeFor(const std::string& kernelType);

class Core {
  public:
    Core(std::vector<SensorCfg> cfg, Sysfs fs, float hystC = 2.0f);
    // read every due sensor (interval elapsed) at time nowMs; returns the
    // indices whose severity changed
    std::vector<size_t> poll(int64_t nowMs);
    // rescan cooling devices; returns names whose cur_state changed
    std::vector<std::string> pollCooling();
    const std::vector<SensorState>& sensors() const { return s_; }
    const std::vector<Cooling>& cooling() const { return c_; }
    // what the Binder getters publish (filter < -1 = all types)
    std::vector<PubTemp> temperatures(int typeFilter) const;
    std::vector<PubThreshold> thresholds(int typeFilter) const;
    std::vector<Cooling> coolingDevices(int typeFilter) const;
    // the dumpsys text (sensors + cooling); thermal-gate.py parses it
    std::string dumpText() const;

  private:
    std::vector<SensorState> s_;
    std::vector<Cooling> c_;
    Sysfs fs_;
    float hyst_;
    bool scannedOnce_ = false;
};

}  // namespace a9thermal
