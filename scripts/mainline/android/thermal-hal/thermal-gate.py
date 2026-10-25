#!/usr/bin/env python3
"""thermal-gate.py -- offline evaluator for the supervised A9 thermal HAL check.
It never talks to the phone: the operator captures with the read-only commands
printed by `thermal-gate.py commands` and passes the files here.
Exit 0 = PASS, 1 = STOP (reasons printed), 2 = usage.

  thermal-gate.py commands
  thermal-gate.py idle --hal HAL_DUMP --sysfs ZONES [--tol 3.0]
      HAL_DUMP = `dumpsys android.hardware.thermal.IThermal/default` (thermal-a9 dump)
      ZONES    = the zone/cooling/battery listing (commands below), taken right after
      PASS needs: every CPU/GPU/SKIN/BATTERY sensor of thermal-a9.conf listed with a valid
      value (no FAULT/absent), each zone sensor within --tol C of the sysfs zone, skin from
      the quiet-therm zone, battery from power_supply, every severity 0, every cooling
      device at 0, the HAL's cooling list == the sysfs cooling devices, and an idle phone
      (all tsens < 60 C, quiet-therm < 42 C, battery < 40 C).
  thermal-gate.py service --dumpsys THERMALSERVICE
      `dumpsys thermalservice`: HAL ready, Thermal Status 0, no override, the A9 sensor
      names present in the HAL temperature list.
"""
import argparse, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(HERE, "thermal-a9.conf")
MUST = ("CPU", "GPU", "SKIN", "BATTERY")
ZONES_CMD = (r"""adb shell 'for z in /sys/class/thermal/thermal_zone*; do echo "zone $(cat $z/type) $(cat $z/temp)"; done; """
             r"""for c in /sys/class/thermal/cooling_device*; do echo "cdev $(cat $c/type) $(cat $c/cur_state) $(cat $c/max_state)"; done; """
             r"""echo "battery.temp $(cat /sys/class/power_supply/battery/temp)"'""")
COMMANDS = f"""# all read-only; no override, no emulation, no setprop
adb shell dumpsys android.hardware.thermal.IThermal/default > hal-dump.txt
{ZONES_CMD} > zones.txt
adb shell dumpsys thermalservice > thermalservice.txt
adb shell 'logcat -d -s thermal-a9' > thermal-a9.log
python3 -I scripts/mainline/android/thermal-hal/thermal-gate.py idle --hal hal-dump.txt --sysfs zones.txt
python3 -I scripts/mainline/android/thermal-hal/thermal-gate.py service --dumpsys thermalservice.txt
"""


class Stop(Exception):
    pass


def conf():
    rows = []
    for line in open(CONF):
        f = line.split("#", 1)[0].split()
        if f:
            rows.append((f[0], f[1], f[2].split("|")[0]))
    return rows


def parse_hal(text):
    sensors, cooling, sec = {}, {}, None
    for line in text.splitlines():
        if line.startswith("sensors:"):
            sec = "s"; continue
        if line.startswith("cooling:"):
            sec = "c"; continue
        if line.startswith("callbacks:"):
            sec = None; continue
        f = line.split()
        if sec == "s" and len(f) >= 3:
            m = re.match(r"\s+(\S+) type (-?\d+) (\S+) C sev (\d+) from (\S+) \((\S+)\)", line)
            if m:
                sensors[m.group(1)] = {"valid": True, "c": float(m.group(3)), "sev": int(m.group(4)), "from": m.group(5)}
            else:
                sensors[f[0]] = {"valid": False, "why": " ".join(f[3:])}
        elif sec == "c":
            m = re.match(r"\s+(\S+) type (\d+) (-?\d+)/(-?\d+)( \(gone\))?", line)
            if m:
                cooling[m.group(1)] = {"cur": int(m.group(3)), "max": int(m.group(4)), "gone": bool(m.group(5))}
    return sensors, cooling


def parse_zones(text):
    zones, cdev, batt = {}, {}, None
    seen = {}
    for line in text.splitlines():
        f = line.split()
        if len(f) >= 3 and f[0] == "zone":
            try:
                zones[f[1]] = int(f[2]) / 1000.0
            except ValueError:
                raise Stop(f"unreadable zone line: {line}")
        elif len(f) >= 3 and f[0] == "cdev":
            n = f[1]
            k = seen.get(n, 0); seen[n] = k + 1
            cdev[n if k == 0 else f"{n}#{k}"] = int(f[2])
        elif len(f) == 2 and f[0] == "battery.temp":
            batt = int(f[1]) / 10.0
    return zones, cdev, batt


def idle(a):
    errs = []
    s, c = parse_hal(open(a.hal).read())
    z, cd, batt = parse_zones(open(a.sysfs).read())
    if not s:
        raise Stop("no thermal-a9 dump (HAL not running or wrong service?)")
    for name, typ, src in conf():
        kind, arg = src.split(":", 1)
        if typ in MUST:
            if name not in s or not s[name]["valid"]:
                errs.append(f"{name} ({typ}) not valid in the HAL: {s.get(name, {}).get('why', 'missing')}")
                continue
            if s[name]["sev"] != 0:
                errs.append(f"{name} severity {s[name]['sev']} at idle")
        if name in s and s[name]["valid"]:
            if kind in ("zone", "adczone") and s[name]["from"] == src:
                if arg not in z:
                    errs.append(f"{name}: zone {arg} not in the sysfs capture")
                elif abs(z[arg] - s[name]["c"]) > a.tol:
                    errs.append(f"{name}: HAL {s[name]['c']} C vs sysfs {z[arg]} C (> {a.tol} C)")
            if name == "battery" and batt is not None and abs(batt - s[name]["c"]) > a.tol:
                errs.append(f"battery: HAL {s[name]['c']} C vs sysfs {batt} C")
    if "skin" in s and s["skin"].get("from") != "adczone:quiet-therm-thermal":
        errs.append(f"skin resolved from {s['skin'].get('from')} (want the quiet-therm zone)")
    if "battery" in s and s["battery"].get("from") != "file:/sys/class/power_supply/battery/temp":
        errs.append(f"battery resolved from {s['battery'].get('from')}")
    if set(c) != set(cd):
        errs.append(f"HAL cooling devices {sorted(c)} != sysfs {sorted(cd)}")
    for n, v in c.items():
        if v["cur"] != 0 or v["gone"]:
            errs.append(f"cooling {n} state {v['cur']}{' gone' if v['gone'] else ''} at idle")
    for n, v in cd.items():
        if v != 0:
            errs.append(f"sysfs cooling {n} cur_state {v} at idle")
    for n, t in z.items():
        lim = 42.0 if "quiet" in n else 60.0
        if n != "battery" and t >= lim:
            errs.append(f"not idle: {n} {t} C >= {lim}")
    if batt is not None and batt >= 40.0:
        errs.append(f"not idle: battery {batt} C")
    if errs:
        raise Stop("; ".join(errs))
    print(f"idle PASS: {len(s)} HAL sensors, {len(c)} cooling devices, all NONE / 0, HAL = sysfs within {a.tol} C")


def service(a):
    t = open(a.dumpsys).read()
    errs = []
    if not re.search(r"HAL Ready:\s*true", t):
        errs.append("thermalservice: HAL Ready is not true")
    m = re.search(r"Thermal Status:\s*(\d+)", t)
    if not m or m.group(1) != "0":
        errs.append(f"thermalservice: Thermal Status {m.group(1) if m else '?'} (want 0)")
    if re.search(r"IsStatusOverride:\s*true", t):
        errs.append("thermalservice: status override active")
    hal = t.split("Current temperatures from HAL:", 1)
    for n in ("cpu4", "cpu0123", "gpu", "skin", "battery"):
        if len(hal) < 2 or f"mName={n}," not in hal[1]:
            errs.append(f"thermalservice: {n} missing from 'Current temperatures from HAL'")
    if errs:
        raise Stop("; ".join(errs))
    print("service PASS: HAL ready, status 0, no override, A9 sensors listed")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("commands")
    x = sp.add_parser("idle"); x.add_argument("--hal", required=True); x.add_argument("--sysfs", required=True)
    x.add_argument("--tol", type=float, default=3.0)
    x = sp.add_parser("service"); x.add_argument("--dumpsys", required=True)
    a = ap.parse_args()
    try:
        if a.cmd == "commands":
            print(COMMANDS, end="")
        else:
            {"idle": idle, "service": service}[a.cmd](a)
    except (Stop, OSError) as e:
        print(f"STOP: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
