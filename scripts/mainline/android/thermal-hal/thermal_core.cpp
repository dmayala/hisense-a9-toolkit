// A9 thermal HAL core (see thermal_core.h). Read-only on sysfs.
#include "thermal_core.h"

#include <dirent.h>

#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>

namespace a9thermal {

static const std::map<std::string, int> kTypes = {
    {"CPU", T_CPU}, {"GPU", T_GPU}, {"BATTERY", T_BATTERY}, {"SKIN", T_SKIN},
    {"USB_PORT", T_USB_PORT}, {"POWER_AMPLIFIER", T_POWER_AMPLIFIER}, {"DISPLAY", T_DISPLAY},
    {"MODEM", T_MODEM}, {"SOC", T_SOC}, {"CAMERA", T_CAMERA}, {"AMBIENT", T_AMBIENT},
    {"NPU", T_NPU}, {"WIFI", T_WIFI},
    {"UNKNOWN", T_UNKNOWN},
};

bool parseType(const std::string& s, int* out) {
    auto it = kTypes.find(s);
    if (it == kTypes.end()) return false;
    *out = it->second;
    return true;
}

static bool parseDouble(const std::string& s, double* v) {
    if (s.empty()) return false;
    char* end = nullptr;
    errno = 0;
    double d = strtod(s.c_str(), &end);
    if (errno || !end || *end || !std::isfinite(d)) return false;
    *v = d;
    return true;
}

bool parseConfig(const std::string& text, std::vector<SensorCfg>* out, std::string* err) {
    std::istringstream in(text);
    std::string line;
    int n = 0;
    std::vector<SensorCfg> res;
    auto fail = [&](const std::string& m) {
        *err = "line " + std::to_string(n) + ": " + m;
        return false;
    };
    while (std::getline(in, line)) {
        n++;
        auto hash = line.find('#');
        if (hash != std::string::npos) line.resize(hash);
        std::istringstream ls(line);
        std::vector<std::string> f;
        for (std::string w; ls >> w;) f.push_back(w);
        if (f.empty()) continue;
        if (f.size() != 10 && f.size() != 11) return fail("need 10 or 11 fields");
        SensorCfg c;
        c.name = f[0];
        for (auto& o : res)
            if (o.name == c.name) return fail("duplicate sensor " + c.name);
        if (!parseType(f[1], &c.type)) return fail("unknown type " + f[1]);
        double scale;
        if (!parseDouble(f[3], &scale) || scale <= 0) return fail("bad scale " + f[3]);
        std::string srcs = f[2];
        size_t p = 0;
        while (p <= srcs.size()) {
            size_t q = srcs.find('|', p);
            if (q == std::string::npos) q = srcs.size();
            std::string one = srcs.substr(p, q - p);
            p = q + 1;
            Source s;
            s.scale = scale;
            auto at = one.rfind('@');
            if (at != std::string::npos && one.compare(0, 5, "file:") != 0) {
                if (!parseDouble(one.substr(at + 1), &s.scale) || s.scale <= 0)
                    return fail("bad @scale in " + one);
                one.resize(at);
            } else if (at != std::string::npos) {
                // file paths may contain '@' (pmic@2): only a numeric tail counts
                double d;
                if (parseDouble(one.substr(at + 1), &d) && d > 0) {
                    s.scale = d;
                    one.resize(at);
                }
            }
            auto colon = one.find(':');
            if (colon == std::string::npos) return fail("source without kind: " + one);
            s.kind = one.substr(0, colon);
            s.arg = one.substr(colon + 1);
            if (s.kind != "zone" && s.kind != "adczone" && s.kind != "file" && s.kind != "iio")
                return fail("unknown source kind " + s.kind);
            if (s.arg.empty()) return fail("empty source argument");
            if (s.kind == "file" && s.arg[0] != '/') return fail("file source must be absolute");
            if (s.arg.find("..") != std::string::npos) return fail("'..' in source");
            c.sources.push_back(s);
        }
        c.hot[0] = NAN;
        float last = -INFINITY;
        for (int i = 1; i < kSeverities; i++) {
            const std::string& t = f[3 + i];
            if (t == "-") {
                c.hot[i] = NAN;
                continue;
            }
            double v;
            if (!parseDouble(t, &v)) return fail("bad threshold " + t);
            if (v <= last) return fail("thresholds must increase");
            c.hot[i] = static_cast<float>(v);
            last = c.hot[i];
        }
        if (f.size() == 11) {
            double iv;
            if (!parseDouble(f[10], &iv) || iv < 1 || iv > 3600) return fail("bad interval " + f[10]);
            c.intervalS = static_cast<int>(iv);
        }
        res.push_back(c);
    }
    *out = std::move(res);
    return true;
}

std::vector<std::string> Sysfs::listDir(const std::string& dir) const {
    std::vector<std::string> v;
    DIR* d = opendir((root_ + dir).c_str());
    if (!d) return v;
    while (struct dirent* e = readdir(d)) {
        if (e->d_name[0] == '.') continue;
        v.push_back(e->d_name);
    }
    closedir(d);
    std::sort(v.begin(), v.end());
    return v;
}

bool Sysfs::readString(const std::string& path, std::string* v) const {
    std::ifstream f(root_ + path);
    if (!f) return false;
    std::string s;
    if (!std::getline(f, s)) return false;
    while (!s.empty() && (s.back() == '\n' || s.back() == ' ' || s.back() == '\r')) s.pop_back();
    *v = s;
    return true;
}

bool Sysfs::readLong(const std::string& path, long long* v) const {
    std::string s;
    if (!readString(path, &s) || s.empty()) return false;
    char* end = nullptr;
    errno = 0;
    long long x = strtoll(s.c_str(), &end, 10);
    if (errno || !end || *end) return false;
    *v = x;
    return true;
}

std::string Sysfs::resolve(const Source& s) const {
    if (s.kind == "file") {
        std::string tmp;
        return readString(s.arg, &tmp) ? s.arg : "";
    }
    if (s.kind == "zone" || s.kind == "adczone") {
        // the kernel names DT zones "<node>" without "-thermal" since 6.x; accept both
        for (auto& z : listDir("/sys/class/thermal")) {
            if (z.compare(0, 12, "thermal_zone") != 0) continue;
            std::string t, dir = "/sys/class/thermal/" + z;
            if (!readString(dir + "/type", &t)) continue;
            if (t == s.arg || t + "-thermal" == s.arg || t == s.arg + "-thermal") return dir + "/temp";
        }
        return "";
    }
    if (s.kind == "iio") {
        for (auto& d : listDir("/sys/bus/iio/devices")) {
            std::string dir = "/sys/bus/iio/devices/" + d;
            std::string p = dir + "/in_temp_" + s.arg + "_input";   // adc5: extend_name = label
            std::string tmp;
            if (readString(p, &tmp)) return p;
            // indexed channels with a label file
            for (auto& f : listDir(dir)) {
                if (f.compare(0, 7, "in_temp") != 0 || f.size() < 13 || f.compare(f.size() - 6, 6, "_label") != 0)
                    continue;
                std::string lab;
                if (readString(dir + "/" + f, &lab) && lab == s.arg) {
                    std::string base = f.substr(0, f.size() - 6);
                    if (readString(dir + "/" + base + "_input", &tmp)) return dir + "/" + base + "_input";
                }
            }
        }
        return "";
    }
    return "";
}

void rangeFor(const std::string& kind, double* lo, double* hi, bool* exclusive) {
    *exclusive = false;
    if (kind == "zone") { *lo = -40; *hi = 150; return; }
    // ADC thermistors (IIO channel, or a generic-adc-thermal zone on one): the
    // vadc table ends are clamps, so a reading AT an end is an open/short
    if (kind == "iio" || kind == "adczone") { *lo = -40; *hi = 125; *exclusive = true; return; }
    *lo = -40; *hi = 125;
}

int severityFor(const float hot[kSeverities], float c, int prev, float hyst) {
    int s = 0;
    for (int i = kSeverities - 1; i >= 1; i--) {
        if (std::isnan(hot[i])) continue;
        if (c >= hot[i]) {
            s = i;
            break;
        }
    }
    if (s >= prev) return s;
    // falling: stay at a level until below its threshold minus hysteresis
    for (int i = prev; i > s; i--) {
        if (std::isnan(hot[i])) continue;
        if (c >= hot[i] - hyst) return i;
    }
    return s;
}

int coolingTypeFor(const std::string& t) {
    if (t.compare(0, 11, "cpufreq-cpu") == 0 || t.compare(0, 8, "thermal-") == 0) return C_CPU;
    if (t.find("gpu") != std::string::npos) return C_GPU;
    if (t.find("battery") != std::string::npos || t.find("charger") != std::string::npos) return C_BATTERY;
    if (t.find("modem") != std::string::npos) return C_MODEM;
    if (t.find("display") != std::string::npos || t.find("backlight") != std::string::npos) return C_DISPLAY;
    return C_COMPONENT;
}

Core::Core(std::vector<SensorCfg> cfg, Sysfs fs, float hystC) : fs_(std::move(fs)), hyst_(hystC) {
    for (auto& c : cfg) {
        SensorState st;
        st.cfg = c;
        s_.push_back(st);
    }
}

std::vector<size_t> Core::poll(int64_t nowMs) {
    std::vector<size_t> changed;
    for (size_t i = 0; i < s_.size(); i++) {
        auto& st = s_[i];
        int64_t period = st.pending.empty() ? int64_t(st.cfg.intervalS) * 1000 : kConfirmMs;
        if (st.lastReadMs >= 0 && nowMs - st.lastReadMs < period) continue;
        st.lastReadMs = nowMs;
        Reading r;
        std::string why = "no source present";
        // re-resolve every time: modules and IIO devices appear late, and the
        // first alternative must win as soon as it exists
        for (auto& src : st.cfg.sources) {
            std::string p = fs_.resolve(src);
            if (p.empty()) continue;
            // review 28: identity follows PRESENCE, not validity. A sensor
            // whose source exists is listed from then on, faulty or not.
            st.published = true;
            long long raw;
            if (!fs_.readLong(p, &raw)) {
                why = "unreadable " + p;
                break;   // the preferred present source failed: a fault, not a fallback
            }
            double c = raw * src.scale, lo, hi;
            bool excl;
            rangeFor(src.kind, &lo, &hi, &excl);
            if (excl ? (c <= lo || c >= hi) : (c < lo || c > hi)) {
                why = "out of range " + std::to_string(c) + " C from " + p;
                break;
            }
            r.valid = true;
            r.celsius = static_cast<float>(c);
            r.path = p;
            st.resolvedFrom = src.kind + ":" + src.arg;
            break;
        }
        st.last = r;
        int before = st.severity;
        if (!r.valid) {
            st.fault = true;
            st.faultWhy = why;
            st.pending.clear();
            st.severity = 0;   // a faulty sensor never holds a throttling level
        } else {
            st.fault = false;
            st.faultWhy.clear();
            int raw = severityFor(st.cfg.hot, r.celsius, st.severity, hyst_);
            if (raw <= st.severity) {
                st.severity = raw;   // decreases (and no change) apply at once
                st.pending.clear();
            } else {
                st.pending.push_back(raw);
                if (int(st.pending.size()) >= kConfirm) {
                    int m = raw;
                    for (size_t k = st.pending.size() - kConfirm; k < st.pending.size(); k++)
                        m = std::min(m, st.pending[k]);
                    st.severity = m;   // the level all of the last kConfirm samples reached
                    st.pending.clear();
                    if (raw > st.severity) st.pending.push_back(raw);   // still rising: keep confirming
                }
            }
        }
        // notify severity changes and fault edges of listed sensors: a present
        // sensor faulty at its first read is reported (NaN / NONE), and so is
        // its first valid value (recovery), even when severity stays NONE
        bool faultEdge = st.published && st.fault != st.faultNotified;
        if (st.published) st.faultNotified = st.fault;
        if (st.severity != before || faultEdge) changed.push_back(i);
    }
    return changed;
}

std::vector<std::string> Core::pollCooling() {
    std::vector<std::string> changed;
    std::vector<Cooling> now;
    for (auto& d : fs_.listDir("/sys/class/thermal")) {
        if (d.compare(0, 14, "cooling_device") != 0) continue;
        Cooling c;
        c.dir = "/sys/class/thermal/" + d;
        if (!fs_.readString(c.dir + "/type", &c.name)) continue;
        fs_.readLong(c.dir + "/cur_state", &c.cur);
        fs_.readLong(c.dir + "/max_state", &c.max);
        c.type = coolingTypeFor(c.name);
        now.push_back(c);
    }
    // names are not unique across kernels (several "thermal-cpufreq-N"): suffix duplicates
    std::map<std::string, int> seen;
    for (auto& c : now)
        if (seen[c.name]++) c.name += "#" + std::to_string(seen[c.name] - 1);
    // merge into the persistent list: order of first appearance, never removed
    for (auto& o : c_) o.present = false;
    for (auto& c : now) {
        Cooling* o = nullptr;
        for (auto& x : c_)
            if (x.name == c.name) o = &x;
        if (!o) {
            if (scannedOnce_) changed.push_back(c.name);
            c_.push_back(c);
            continue;
        }
        if (o->cur != c.cur) changed.push_back(c.name);
        *o = c;   // present = true
    }
    scannedOnce_ = true;
    return changed;
}

std::vector<PubTemp> Core::temperatures(int f) const {
    std::vector<PubTemp> v;
    for (auto& s : s_) {
        if (!s.published || (f >= -1 && s.cfg.type != f)) continue;
        v.push_back({s.cfg.name, s.cfg.type, s.last.valid ? s.last.celsius : NAN, s.severity});
    }
    return v;
}

std::vector<PubThreshold> Core::thresholds(int f) const {
    std::vector<PubThreshold> v;
    for (auto& s : s_) {
        if (!s.published || (f >= -1 && s.cfg.type != f)) continue;
        PubThreshold t;
        t.name = s.cfg.name;
        t.type = s.cfg.type;
        std::copy(s.cfg.hot, s.cfg.hot + kSeverities, t.hot);
        v.push_back(t);
    }
    return v;
}

std::vector<Cooling> Core::coolingDevices(int f) const {
    std::vector<Cooling> v;
    for (auto& c : c_)
        if (f < -1 || c.type == f) v.push_back(c);
    return v;
}

std::string Core::dumpText() const {
    std::ostringstream o;
    o << "thermal-a9 (A9 mainline, read-only)\nsensors:\n";
    for (auto& s : s_) {
        o << "  " << s.cfg.name << " type " << s.cfg.type << " ";
        if (s.last.valid)
            o << s.last.celsius << " C sev " << s.severity << " from " << s.resolvedFrom << " (" << s.last.path << ")";
        else
            o << (s.published ? "FAULT " : "absent ") << s.faultWhy;
        if (!s.pending.empty()) o << " [confirming " << s.pending.size() << "/" << kConfirm << "]";
        o << "\n";
    }
    o << "cooling:\n";
    for (auto& c : c_)
        o << "  " << c.name << " type " << c.type << " " << c.cur << "/" << c.max << (c.present ? "" : " (gone)") << "\n";
    return o.str();
}

}  // namespace a9thermal
