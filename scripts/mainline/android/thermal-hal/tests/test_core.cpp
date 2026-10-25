// Host tests of the A9 thermal HAL core against a fake sysfs tree.
// Built by tests/run-tests.sh with -fsanitize=address,undefined.
#include <sys/stat.h>
#include <unistd.h>

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <string>

#include "../thermal_core.h"

using namespace a9thermal;
static int fails, checks;
#define CHECK(c, ...) do { checks++; if (!(c)) { fails++; printf("FAIL %s:%d: ", __FILE__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

static std::string root;
static void mkdirs(const std::string& p) {
    std::string cur;
    std::stringstream ss(p);
    for (std::string part; std::getline(ss, part, '/');) {
        if (part.empty()) continue;
        cur += "/" + part;
        mkdir((root + cur).c_str(), 0755);
    }
}
static void put(const std::string& p, const std::string& v) {
    mkdirs(p.substr(0, p.rfind('/')));
    std::ofstream(root + p) << v << "\n";
}
static void rm(const std::string& p) { unlink((root + p).c_str()); }
static std::string slurp(const char* p) { std::ifstream f(p); std::stringstream s; s << f.rdbuf(); return s.str(); }

int main(int argc, char** argv) {
    char tmpl[] = "/tmp/a9thermal-XXXXXX";
    const char* t = getenv("A9_TEST_TMP");
    std::string base = t ? std::string(t) + "/a9thermal-XXXXXX" : tmpl;
    std::vector<char> b(base.begin(), base.end()); b.push_back(0);
    root = mkdtemp(b.data());

    // --- parser
    std::vector<SensorCfg> cfg; std::string err;
    CHECK(parseConfig("a CPU zone:x 0.001 - - 95 100 103 105 5\n", &cfg, &err) && cfg.size() == 1, "basic %s", err.c_str());
    CHECK(std::isnan(cfg[0].hot[1]) && cfg[0].hot[3] == 95 && cfg[0].hot[6] == 105 && cfg[0].intervalS == 5, "thresholds");
    CHECK(!parseConfig("a CPU zone:x 0.001 - - 95 90 103 105\n", &cfg, &err), "decreasing refused");
    CHECK(!parseConfig("a XYZ zone:x 0.001 - - - - - -\n", &cfg, &err), "type refused");
    CHECK(!parseConfig("a CPU zone:x 0.001 - - - - -\n", &cfg, &err), "field count");
    CHECK(!parseConfig("a CPU file:rel 1 - - - - - -\n", &cfg, &err), "relative file refused");
    CHECK(!parseConfig("a CPU file:/sys/../etc 1 - - - - - -\n", &cfg, &err), "dotdot refused");
    CHECK(!parseConfig("a CPU zone:x 1 - - - - - -\na GPU zone:y 1 - - - - - -\n", &cfg, &err), "dup refused");
    CHECK(!parseConfig("a CPU zone:x 1 - - - - - - 0\n", &cfg, &err), "interval 0 refused");
    CHECK(parseConfig("s SKIN adczone:q|iio:quiet_therm|file:/sys/class/power_supply/battery/temp@0.1 0.001 45 48 50 52 55 58 10 # c\n", &cfg, &err), "alts %s", err.c_str());
    CHECK(cfg[0].sources.size() == 3 && cfg[0].sources[2].scale == 0.1 && cfg[0].sources[1].scale == 0.001 &&
          cfg[0].sources[2].arg == "/sys/class/power_supply/battery/temp", "alt scales");
    CHECK(parseConfig("p SOC file:/sys/devices/pmic@2/temp 0.001 - - - - - -\n", &cfg, &err) &&
          cfg[0].sources[0].arg == "/sys/devices/pmic@2/temp" && cfg[0].sources[0].scale == 0.001, "'@' in path kept");
    // the shipped config parses
    std::string shipped = slurp(argc > 1 ? argv[1] : "../thermal-a9.conf");
    CHECK(!shipped.empty() && parseConfig(shipped, &cfg, &err), "shipped config: %s", err.c_str());
    int shut = 0;
    for (auto& c : cfg) {
        if (c.type == T_CPU || c.type == T_GPU) {
            CHECK(c.hot[6] < 110, "%s SHUTDOWN below the kernel critical 110 C", c.name.c_str());
            shut++;
        }
        if (c.type == T_SKIN || c.type == T_BATTERY) {
            CHECK(!std::isnan(c.hot[6]), "%s has SHUTDOWN", c.name.c_str());
            shut++;
        }
    }
    CHECK(shut >= 10, "CPU/GPU/SKIN/BATTERY sensors present (%d)", shut);
    int skins = 0; for (auto& c : cfg) skins += c.type == T_SKIN; CHECK(skins == 1, "one SKIN sensor");

    // --- severity with hysteresis
    float hot[7] = {NAN, 45, 48, 50, 52, 55, 58};
    CHECK(severityFor(hot, 30, 0, 2) == 0, "cool");
    CHECK(severityFor(hot, 45, 0, 2) == 1, "light at 45");
    CHECK(severityFor(hot, 58.5f, 1, 2) == 6, "shutdown");
    CHECK(severityFor(hot, 44, 1, 2) == 1, "hysteresis holds LIGHT at 44");
    CHECK(severityFor(hot, 42.9f, 1, 2) == 0, "falls below 43");
    CHECK(severityFor(hot, 49, 3, 2) == 3, "SEVERE held at 49 (50-2)");
    CHECK(severityFor(hot, 47.9f, 3, 2) == 2, "falls to MODERATE");
    float sparse[7] = {NAN, NAN, NAN, 95, 100, 103, 105};
    CHECK(severityFor(sparse, 94, 0, 2) == 0 && severityFor(sparse, 101, 0, 2) == 4, "sparse thresholds");
    CHECK(severityFor(sparse, 93.5f, 3, 2) == 3 && severityFor(sparse, 92.9f, 3, 2) == 0, "sparse hysteresis");

    // --- fake sysfs: zones, iio, power_supply, cooling devices
    put("/sys/class/thermal/thermal_zone0/type", "cpu4-thermal");
    put("/sys/class/thermal/thermal_zone0/temp", "41000");
    put("/sys/class/thermal/thermal_zone1/type", "gpu");          // name without -thermal
    put("/sys/class/thermal/thermal_zone1/temp", "39000");
    put("/sys/bus/iio/devices/iio:device0/in_temp_xo_therm_input", "27500");
    put("/sys/bus/iio/devices/iio:device1/in_temp3_label", "pa_therm0");
    put("/sys/bus/iio/devices/iio:device1/in_temp3_input", "28000");
    put("/sys/class/power_supply/battery/temp", "310");
    put("/sys/class/thermal/cooling_device0/type", "cpufreq-cpu0");
    put("/sys/class/thermal/cooling_device0/cur_state", "0");
    put("/sys/class/thermal/cooling_device0/max_state", "14");
    put("/sys/class/thermal/cooling_device1/type", "cpufreq-cpu4");
    put("/sys/class/thermal/cooling_device1/cur_state", "0");
    put("/sys/class/thermal/cooling_device1/max_state", "15");
    Sysfs fs(root);
    CHECK(fs.resolve({"zone", "cpu4-thermal", 1}) == "/sys/class/thermal/thermal_zone0/temp", "zone exact");
    CHECK(fs.resolve({"zone", "gpu-thermal", 1}) == "/sys/class/thermal/thermal_zone1/temp", "zone without suffix");
    CHECK(fs.resolve({"zone", "nope", 1}).empty(), "absent zone");
    CHECK(fs.resolve({"iio", "xo_therm", 1}) == "/sys/bus/iio/devices/iio:device0/in_temp_xo_therm_input", "iio extend_name");
    CHECK(fs.resolve({"iio", "pa_therm0", 1}) == "/sys/bus/iio/devices/iio:device1/in_temp3_input", "iio label file");

    CHECK(parseConfig(shipped, &cfg, &err), "reparse");
    {   // no skin source may be a different physical sensor (review 24 item 3)
        for (auto& c : cfg)
            if (c.type == T_SKIN)
                for (auto& src : c.sources)
                    CHECK(src.arg.find("quiet") != std::string::npos, "skin source %s is quiet_therm", src.arg.c_str());
    }
    Core core(cfg, fs);
    auto find = [&](const char* n) -> const SensorState& { for (auto& s : core.sensors()) if (s.cfg.name == n) return s; static SensorState none; return none; };
    auto pubt = [&](const char* n, float* v, int* sev) { for (auto& p : core.temperatures(-2)) if (p.name == n) { *v = p.value; *sev = p.severity; return true; } return false; };
    auto ch = core.poll(0);
    CHECK(ch.empty(), "nothing throttling at boot");
    CHECK(find("cpu4").last.valid && std::fabs(find("cpu4").last.celsius - 41) < 0.01, "cpu4 41 C");
    CHECK(find("gpu").last.valid, "gpu via suffix-less zone");
    CHECK(!find("cpu5").last.valid && !find("cpu5").published, "cpu5 absent, not listed");
    CHECK(!find("skin").last.valid && !find("skin").published, "skin absent without quiet_therm (no battery fallback)");
    CHECK(find("battery").last.valid && std::fabs(find("battery").last.celsius - 31) < 0.01, "battery 31 C");
    CHECK(find("xo-therm").last.valid && std::fabs(find("xo-therm").last.celsius - 27.5f) < 0.01, "xo 27.5 C");
    size_t nlisted = core.temperatures(-2).size(), nthr = core.thresholds(-2).size();
    CHECK(nlisted == core.thresholds(-2).size() && nlisted >= 5, "temperatures and thresholds list the same sensors");

    // quiet_therm appears later: skin is listed from its first valid read (10 s interval)
    put("/sys/bus/iio/devices/iio:device0/in_temp_quiet_therm_input", "46000");
    core.poll(5000);
    CHECK(!find("skin").published, "skin not read before its interval");
    ch = core.poll(10000);
    CHECK(find("skin").resolvedFrom == "iio:quiet_therm" && find("skin").severity == 0 && find("skin").pending.size() == 1,
          "skin 46 C: LIGHT pending (1/3), not committed");
    core.poll(10500);
    CHECK(find("skin").pending.size() == 1, "confirm re-read waits 1 s");
    core.poll(11000);
    ch = core.poll(12000);
    CHECK(find("skin").severity == 1 && find("skin").pending.empty(), "LIGHT after 3 consecutive samples");
    CHECK(ch.size() == 1 && core.sensors()[ch[0]].cfg.name == "skin", "one change reported");
    CHECK(core.temperatures(-2).size() == nlisted + 1, "skin added to the list");
    // the zone wins over the IIO channel once present; 44.5 C keeps LIGHT (hysteresis)
    put("/sys/class/thermal/thermal_zone2/type", "quiet-therm-thermal");
    put("/sys/class/thermal/thermal_zone2/temp", "44500");
    core.poll(22000);
    CHECK(find("skin").resolvedFrom == "adczone:quiet-therm-thermal" && find("skin").severity == 1, "zone first, LIGHT kept");
    // ADC clamp: the zone reads the table end 125 C -> FAULT, never SHUTDOWN (review 24 item 1)
    put("/sys/class/thermal/thermal_zone2/temp", "125000");
    ch = core.poll(32000);
    float v; int sev;
    CHECK(find("skin").fault && find("skin").severity == 0 && pubt("skin", &v, &sev) && std::isnan(v) && sev == 0,
          "skin 125 C (ADC clamp) = fault: listed NaN / NONE");
    CHECK(ch.size() == 1, "fault edge notified (LIGHT -> NONE)");
    CHECK(core.thresholds(-2).size() == nlisted + 1, "thresholds keep the faulty sensor (static)");
    put("/sys/bus/iio/devices/iio:device0/in_temp_quiet_therm_input", "125000");
    rm("/sys/class/thermal/thermal_zone2/temp"); rm("/sys/class/thermal/thermal_zone2/type");
    core.poll(42000);
    CHECK(find("skin").fault, "IIO channel at 125 C is a fault too");
    put("/sys/bus/iio/devices/iio:device0/in_temp_quiet_therm_input", "-40000");
    core.poll(52000);
    CHECK(find("skin").fault, "-40 C table end is a fault");
    put("/sys/bus/iio/devices/iio:device0/in_temp_quiet_therm_input", "30000");
    ch = core.poll(62000);
    CHECK(!find("skin").fault && pubt("skin", &v, &sev) && std::fabs(v - 30) < 0.01 && ch.size() == 1, "recovery notified with the value");

    // CPU: ONE bad successful 125 C sample never reaches SHUTDOWN (review 24 item 1)
    put("/sys/class/thermal/thermal_zone0/temp", "125000");
    core.poll(65000);
    put("/sys/class/thermal/thermal_zone0/temp", "41000");
    core.poll(66000);
    CHECK(find("cpu4").severity == 0 && find("cpu4").pending.empty(), "single 125 C tsens sample discarded");
    // two high, one normal, two high: never confirmed
    for (int t : {106000, 106000, 41000, 106000, 106000}) { put("/sys/class/thermal/thermal_zone0/temp", std::to_string(t)); core.poll(core.sensors()[0].lastReadMs + 5000); }
    CHECK(find("cpu4").severity == 0, "interrupted run not confirmed");
    // genuine heat: rising 101, 104, 106, 106 -> committed level = lowest of the last 3
    put("/sys/class/thermal/thermal_zone0/temp", "41000"); core.poll(150000);
    CHECK(find("cpu4").pending.empty() && find("cpu4").severity == 0, "a normal sample clears the pending run");
    int64_t tms = 200000;
    for (int t : {101000, 104000, 106000}) { put("/sys/class/thermal/thermal_zone0/temp", std::to_string(t)); core.poll(tms); tms += 1000; }
    CHECK(find("cpu4").severity == 4, "101/104/106 -> CRITICAL (lowest of three)");
    for (int t : {106000, 106500, 107000}) { put("/sys/class/thermal/thermal_zone0/temp", std::to_string(t)); core.poll(tms); if (getenv("A9_DEBUG")) printf("t=%d sev=%d pend=%zu last=%lld\n", t, find("cpu4").severity, find("cpu4").pending.size(), (long long)find("cpu4").lastReadMs); tms += 1000; }
    CHECK(find("cpu4").severity == 6, "sustained >= 105 C -> SHUTDOWN after confirmation (got %d, pending %zu, valid %d %.1f)", find("cpu4").severity, find("cpu4").pending.size(), find("cpu4").last.valid, find("cpu4").last.celsius);
    // read failure while at SHUTDOWN: fault, NONE, NaN, still listed
    put("/sys/class/thermal/thermal_zone0/temp", "abc");
    ch = core.poll(tms + 5000);
    CHECK(find("cpu4").fault && find("cpu4").severity == 0 && pubt("cpu4", &v, &sev) && std::isnan(v) && ch.size() == 1,
          "read failure: NaN / NONE, notified, listed");
    // zone disappears entirely: still listed
    rm("/sys/class/thermal/thermal_zone0/temp"); rm("/sys/class/thermal/thermal_zone0/type");
    core.poll(tms + 10000);
    CHECK(pubt("cpu4", &v, &sev) && std::isnan(v), "vanished zone stays listed");
    CHECK(core.temperatures(-2).size() >= nlisted && core.thresholds(-2).size() == core.temperatures(-2).size(), "lists never shrink");
    CHECK(core.temperatures(T_CPU).size() >= 1 && core.temperatures(T_SKIN).size() == 1 && core.temperatures(T_GPU).size() == 1, "type filters");
    (void)nthr;
    // battery: 30 s interval, out-of-range is a fault
    put("/sys/class/power_supply/battery/temp", "455");
    core.poll(400000); core.poll(401000); core.poll(402000);
    CHECK(std::fabs(find("battery").last.celsius - 45.5f) < 0.01 && find("battery").severity == 3, "battery 45.5 C SEVERE after confirmation");
    rm("/sys/class/power_supply/battery/temp");
    core.poll(500000);
    CHECK(find("battery").fault && find("battery").severity == 0, "battery gone -> fault, NONE");

    {   // review 28: a sensor PRESENT at startup but faulty is listed (NaN / NONE, thresholds), notified,
        // and its first valid value (recovery) is notified too
        put("/sys/class/thermal/thermal_zone7/type", "cpu6-thermal");
        put("/sys/class/thermal/thermal_zone7/temp", "200000");      // > tsens range: fault
        std::vector<SensorCfg> c2; std::string e2;
        CHECK(parseConfig("cpu6 CPU zone:cpu6-thermal 0.001 - - 95 100 103 105 5\n", &c2, &e2), "cfg");
        Core k(c2, fs);
        auto chg = k.poll(0);
        float v2; int s2; bool listed = false;
        for (auto& p : k.temperatures(-2)) if (p.name == "cpu6") { listed = true; v2 = p.value; s2 = p.severity; }
        CHECK(listed && std::isnan(v2) && s2 == 0, "initially faulty present sensor listed NaN/NONE");
        CHECK(k.thresholds(-2).size() == 1, "and its static thresholds");
        CHECK(chg.size() == 1, "initial fault notified");
        put("/sys/class/thermal/thermal_zone7/temp", "30000");
        chg = k.poll(5000);
        CHECK(chg.size() == 1 && !k.sensors()[0].fault, "first valid value (recovery) notified");
        put("/sys/class/thermal/thermal_zone7/temp", "125000");     // plausible for tsens: valid, needs confirmation
        k.poll(10000);
        CHECK(k.sensors()[0].severity == 0 && k.sensors()[0].pending.size() == 1, "125 C tsens: pending, not committed");
        rm("/sys/class/thermal/thermal_zone7/temp"); rm("/sys/class/thermal/thermal_zone7/type");
        k.poll(11000);
        CHECK(k.temperatures(-2).size() == 1 && k.sensors()[0].fault && k.sensors()[0].pending.empty(), "vanished: listed, pending cleared");
        CHECK(!find("cpu5").published, "a never-present sensor is not listed");
    }

    // --- cooling devices: persistent list
    auto cc = core.pollCooling();
    CHECK(cc.empty() && core.cooling().size() == 2, "two cooling devices, first scan reports nothing");
    CHECK(core.cooling()[0].type == C_CPU && core.cooling()[1].max == 15, "cpu cooling types");
    put("/sys/class/thermal/cooling_device1/cur_state", "3");
    cc = core.pollCooling();
    CHECK(cc.size() == 1 && cc[0] == "cpufreq-cpu4", "cur_state change reported");
    put("/sys/class/thermal/cooling_device2/type", "cpufreq-cpu4");
    put("/sys/class/thermal/cooling_device2/cur_state", "0");
    cc = core.pollCooling();
    CHECK(core.cooling().size() == 3 && core.cooling()[2].name == "cpufreq-cpu4#1" && cc.size() == 1, "new device appended and reported");
    rm("/sys/class/thermal/cooling_device0/type");
    core.pollCooling();
    CHECK(core.coolingDevices(-2).size() == 3 && !core.cooling()[0].present && core.cooling()[0].name == "cpufreq-cpu0",
          "vanished cooling device stays listed in place");
    CHECK(core.coolingDevices(C_CPU).size() == 3 && core.coolingDevices(C_GPU).empty(), "cooling filter");
    CHECK(coolingTypeFor("thermal-cpufreq-0") == C_CPU && coolingTypeFor("5900000.gpu") == C_GPU &&
          coolingTypeFor("fan0") == C_COMPONENT, "type mapping");

    std::string cmd = "rm -rf '" + root + "'";
    if (system(cmd.c_str())) {}
    printf("%s: %d checks, %d failures\n", fails ? "FAIL" : "PASS", checks, fails);
    return fails ? 1 : 0;
}
