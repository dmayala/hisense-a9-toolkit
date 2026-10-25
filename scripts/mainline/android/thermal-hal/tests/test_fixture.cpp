// Host test of the A9 thermal HAL core against the phone's MEASURED sysfs
// (tests/fixtures/phone-step1b-20261016.txt) and the shipped thermal-a9.conf:
// what the HAL would publish on the step-2-proven kernel at idle, and its
// replay of the two supervised emulations (cpu4 91 C, quiet-therm 46 C).
// Built by tests/run-tests.sh with -fsanitize=address,undefined.
//   test_fixture <thermal-a9.conf> <fixture>
#include <sys/stat.h>
#include <unistd.h>

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <map>
#include <set>
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
static std::string slurp(const char* p) { std::ifstream f(p); std::stringstream s; s << f.rdbuf(); return s.str(); }
// captures in the format of thermal-gate.py's read-only commands, for its host test
static void capture(const std::string& dir, const std::string& tag, const Core& core) {
    if (dir.empty()) return;
    std::ofstream(dir + "/" + tag + "-dump.txt") << core.dumpText() << "callbacks: 0 thermal, 0 cooling\n";
    std::ofstream z(dir + "/" + tag + "-zones.txt");
    Sysfs fs(root);
    for (auto& d : fs.listDir("/sys/class/thermal")) {
        std::string t, v, m;
        std::string p = "/sys/class/thermal/" + d;
        if (d.compare(0, 12, "thermal_zone") == 0 && fs.readString(p + "/type", &t) && fs.readString(p + "/temp", &v))
            z << "zone " << t << " " << v << "\n";
    }
    for (auto& d : fs.listDir("/sys/class/thermal")) {
        std::string t, v, m;
        std::string p = "/sys/class/thermal/" + d;
        if (d.compare(0, 14, "cooling_device") == 0 && fs.readString(p + "/type", &t) && fs.readString(p + "/cur_state", &v)) {
            if (!fs.readString(p + "/max_state", &m)) m = "0";
            z << "cdev " << t << " " << v << " " << m << "\n";
        }
    }
    std::string bt;
    if (fs.readString("/sys/class/power_supply/battery/temp", &bt)) z << "battery.temp " << bt << "\n";
}

int main(int argc, char** argv) {
    if (argc != 3) { fprintf(stderr, "usage: %s <thermal-a9.conf> <fixture>\n", argv[0]); return 2; }
    const char* t = getenv("A9_TEST_TMP");
    std::string base = std::string(t ? t : "/tmp") + "/a9fixture-XXXXXX";
    std::vector<char> b(base.begin(), base.end()); b.push_back(0);
    root = mkdtemp(b.data());

    // --- build the fake sysfs from the measured fixture
    std::map<std::string, std::string> zoneDir;   // zone type -> /sys/class/thermal/thermal_zoneN
    std::map<std::string, double> zoneC;          // zone type -> measured degC
    std::map<std::string, std::string> cdevDir;
    std::istringstream in(slurp(argv[2]));
    int nz = 0, nc = 0, np = 0;
    for (std::string line; std::getline(in, line);) {
        if (line.empty() || line[0] == '#') continue;
        std::istringstream ls(line);
        std::string kind; ls >> kind;
        if (kind == "zone") {
            std::string idx, type; long long mc; ls >> idx >> type >> mc;
            std::string d = "/sys/class/thermal/thermal_zone" + idx;
            put(d + "/type", type); put(d + "/temp", std::to_string(mc)); put(d + "/mode", "enabled");
            zoneDir[type] = d; zoneC[type] = mc / 1000.0; nz++;
        } else if (kind == "cdev") {
            std::string idx, type, cur, max; ls >> idx >> type >> cur >> max;
            std::string d = "/sys/class/thermal/cooling_device" + idx;
            put(d + "/type", type); put(d + "/cur_state", cur);
            if (max != "-") put(d + "/max_state", max);
            cdevDir[type] = d; nc++;
        } else if (kind == "psy") {
            std::string a, v; ls >> a; std::getline(ls >> std::ws, v);
            put("/sys/class/power_supply/" + a, v); np++;
        }
    }
    CHECK(nz == 18 && nc == 2 && np >= 10, "fixture parsed: %d zones, %d cooling devices, %d psy attrs", nz, nc, np);

    std::vector<SensorCfg> cfg; std::string err;
    CHECK(parseConfig(slurp(argv[1]), &cfg, &err), "shipped config: %s", err.c_str());

    // --- every configured zone/adczone source names a zone the phone has (no typo can hide a sensor)
    std::set<std::string> zoneSensors;
    for (auto& c : cfg)
        for (auto& s : c.sources)
            if (s.kind == "zone" || s.kind == "adczone") {
                CHECK(zoneDir.count(s.arg), "%s: zone %s exists on the phone", c.name.c_str(), s.arg.c_str());
                zoneSensors.insert(c.name);
            }
    // and every measured tsens/ADC zone is reported (the battery zone is the power_supply's own,
    // reported through the file source)
    for (auto& z : zoneDir) {
        if (z.first == "battery") continue;
        bool used = false;
        for (auto& c : cfg) for (auto& s : c.sources) used |= s.arg == z.first;
        CHECK(used, "measured zone %s is in the config", z.first.c_str());
    }

    Sysfs fs(root);
    Core core(cfg, fs);
    auto find = [&](const char* n) -> const SensorState& { for (auto& s : core.sensors()) if (s.cfg.name == n) return s; static SensorState none; return none; };
    auto ch = core.poll(0);
    core.pollCooling();
    CHECK(ch.empty(), "idle phone: no severity change, no fault (got %zu)", ch.size());
    const char* go = getenv("A9_GATE_OUT");
    std::string gateOut = go ? go : "";
    capture(gateOut, "idle", core);

    // --- idle: what Android would get
    for (auto& s : core.sensors()) {
        bool zone = zoneSensors.count(s.cfg.name);
        if (zone) {
            CHECK(s.published && s.last.valid && !s.fault, "%s listed with a valid value", s.cfg.name.c_str());
            double want = zoneC[s.cfg.sources[0].arg];
            CHECK(std::fabs(s.last.celsius - want) < 0.001, "%s = %.3f C (measured %.3f)", s.cfg.name.c_str(), s.last.celsius, want);
            CHECK(s.severity == 0, "%s NONE at idle", s.cfg.name.c_str());
        }
        if (s.cfg.sources[0].kind == "iio")
            CHECK(!s.published, "%s: no IIO channel in the fixture -> not listed", s.cfg.name.c_str());
    }
    CHECK(find("skin").resolvedFrom == "adczone:quiet-therm-thermal", "skin = the quiet-therm zone (%s)", find("skin").resolvedFrom.c_str());
    CHECK(find("battery").last.valid && std::fabs(find("battery").last.celsius - 31.9f) < 0.001 && find("battery").severity == 0,
          "battery 31.9 C from power_supply/battery/temp (0.1 C)");
    CHECK(core.temperatures(T_SKIN).size() == 1 && core.temperatures(T_CPU).size() == 7 && core.temperatures(T_GPU).size() == 1 &&
          core.temperatures(T_BATTERY).size() == 1, "published: 1 skin, 7 CPU, 1 GPU, 1 battery");
    CHECK(core.thresholds(-2).size() == core.temperatures(-2).size(), "a threshold entry per listed sensor");
    auto cds = core.coolingDevices(-2);
    CHECK(cds.size() == 2 && cds[0].name == "cpufreq-cpu0" && cds[1].name == "cpufreq-cpu4" && cds[0].type == C_CPU &&
          cds[1].type == C_CPU && cds[1].max == 8 && cds[0].cur == 0 && cds[1].cur == 0,
          "cooling: cpufreq-cpu0 / cpufreq-cpu4 (CPU, 0, max 8)");

    // --- soak peak (cpu6 49.3 C, quiet-therm 37.0 C, battery 32.2 C): still NONE everywhere
    put(zoneDir["cpu6-thermal"] + "/temp", "49300");
    put(zoneDir["quiet-therm-thermal"] + "/temp", "37000");
    put("/sys/class/power_supply/battery/temp", "322");
    ch = core.poll(30000);
    CHECK(ch.empty() && find("cpu6").severity == 0 && find("skin").severity == 0, "soak peak: nothing throttling");

    // --- replay of step 2 (c): cpu4-thermal emulated 91 C for 5 s, kernel cooling state 1
    int64_t ms = 40000;
    put(zoneDir["cpu4-thermal"] + "/temp", "91000");
    put(cdevDir["cpufreq-cpu4"] + "/cur_state", "1");
    core.poll(ms);
    CHECK(find("cpu4").severity == 0 && find("cpu4").pending.size() == 1, "91 C: LIGHT pending, not yet committed");
    auto cc = core.pollCooling();
    CHECK(cc.size() == 1 && cc[0] == "cpufreq-cpu4" && core.coolingDevices(C_CPU)[1].cur == 1, "cooling change cpufreq-cpu4 -> 1 reported");
    ms += 1000; core.poll(ms);
    ms += 1000; ch = core.poll(ms);
    CHECK(find("cpu4").severity == 1 && ch.size() == 1, "3 samples at 91 C (>= first passive trip 90): cpu4 LIGHT");
    capture(gateOut, "hot", core);
    CHECK(find("cpu5").severity == 0 && find("skin").severity == 0, "other sensors untouched");
    // the emulation is cleared after 5 s: real 32.8 C, cooling 0
    put(zoneDir["cpu4-thermal"] + "/temp", "32800");
    put(cdevDir["cpufreq-cpu4"] + "/cur_state", "0");
    ms += 5000; ch = core.poll(ms);
    CHECK(find("cpu4").severity == 0 && ch.size() == 1, "cleared: NONE at once (decrease)");
    cc = core.pollCooling();
    CHECK(cc.size() == 1 && core.coolingDevices(C_CPU)[1].cur == 0, "cooling back to 0 reported");

    // --- replay of step 2 (b): quiet-therm emulated 46 C (kernel gold trip 45), cleared to 37 C
    put(zoneDir["quiet-therm-thermal"] + "/temp", "46000");
    ms += 10000; core.poll(ms);
    ms += 1000; core.poll(ms);
    ms += 1000; ch = core.poll(ms);
    CHECK(find("skin").severity == 1 && ch.size() == 1, "skin 46 C x3: LIGHT (Android thermal status 1)");
    put(zoneDir["quiet-therm-thermal"] + "/temp", "37000");
    ms += 10000; ch = core.poll(ms);
    CHECK(find("skin").severity == 0 && ch.size() == 1, "skin cleared: NONE");

    // --- the HAL never wrote anything: every fixture file still holds what the test put there
    {
        std::string v;
        CHECK(fs.readString(zoneDir["cpu4-thermal"] + "/temp", &v) && v == "32800", "zone temp untouched");
        CHECK(access((root + zoneDir["cpu4-thermal"] + "/emul_temp").c_str(), F_OK) != 0, "no emul_temp created");
        CHECK(access((root + zoneDir["cpu4-thermal"] + "/trip_point_0_temp").c_str(), F_OK) != 0, "no trip file created");
    }

    std::string cmd = "rm -rf '" + root + "'";
    if (system(cmd.c_str())) {}
    printf("%s: %d checks, %d failures (fixture)\n", fails ? "FAIL" : "PASS", checks, fails);
    return fails ? 1 : 0;
}
