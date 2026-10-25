#!/usr/bin/env python3
"""Registry of Hisense A9 phones seen by this machine: local/device.json.

Device identifiers stay out of git (local/ is gitignored; format in
data/device.example.json). Most people have one A9 and never need to touch
this: the first scan records it and makes it the default. A developer with
several A9s gives them names and picks one with `use`, or per command with
A9_DEVICE=<name|serial> or A9_SERIAL=<serial>.

  a9_devices.py scan            detect attached A9s (adb + fastboot), record them
  a9_devices.py list            known phones; * = default, + = attached now
  a9_devices.py use <name|serial>        make it the default
  a9_devices.py name <serial|name> <new> rename (names: letters, digits, - _)
  a9_devices.py forget <name|serial>     drop it from the registry
  a9_devices.py resolve [<name|serial>]  print the serial to target (empty = none)

An A9 is a device reporting device:/product:HLTE556N over adb, or
getvar product HLTE556N in fastboot. EDL (9008) exposes no serial, so EDL
scripts still refuse when more than one 9008 device is attached.
"""
import json, os, re, subprocess, sys, time

MODEL = "HLTE556N"
HOME = os.environ.get("A9_HOME") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.environ.get("A9_DEVICE_JSON") or os.path.join(HOME, "local", "device.json")
ADB = os.environ.get("ADB", "adb")
FB = os.environ.get("FB", "fastboot")


def run(cmd, timeout=8):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return r.stdout + r.stderr
    except (OSError, subprocess.TimeoutExpired):
        return ""


def load():
    try:
        d = json.load(open(PATH))
    except (OSError, ValueError):
        d = {}
    if "adb_serial" in d and "devices" not in d:          # 2026-10-14 single-serial format
        s = d.pop("adb_serial")
        d = {"default": s, "devices": {s: {"name": "a9-1"}}} if s else {}
    d.setdefault("devices", {})
    d.setdefault("default", "")
    return d


def save(d):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, PATH)


def attached():
    """serial -> mode for every A9 visible right now."""
    found = {}
    for line in run([ADB, "devices", "-l"]).splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and re.search(rf"\b(device|product|model):{MODEL}\b", line):
            found[parts[0]] = parts[1]                   # device / recovery / sideload
    for line in run([FB, "devices"]).splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "fastboot" and parts[0] not in found:
            prod = run([FB, "-s", parts[0], "getvar", "product"])
            if MODEL in prod:
                found[parts[0]] = "fastboot"
    return found


def find(d, key):
    if key in d["devices"]:
        return key
    for s, info in d["devices"].items():
        if info.get("name") == key:
            return s
    return None


def scan(d, quiet=False):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    seen = attached()
    for s, mode in seen.items():
        info = d["devices"].setdefault(s, {})
        if "name" not in info:
            used = {i.get("name") for i in d["devices"].values()}
            n = 1
            while f"a9-{n}" in used:
                n += 1
            info["name"] = f"a9-{n}"
            info["first_seen"] = now
            if not quiet:
                print(f"new A9 {info['name']} recorded", file=sys.stderr)
        info["last_seen"] = now
        info["last_mode"] = mode
    if not d["default"] and len(d["devices"]) == 1:
        d["default"] = next(iter(d["devices"]))
    if seen:
        save(d)
    return seen


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "list"
    d = load()
    if cmd == "scan":
        seen = scan(d)
        print(f"{len(seen)} A9 attached, {len(d['devices'])} known")
        cmd = "list"
    if cmd == "list":
        now = attached()
        if not d["devices"]:
            print("no A9 known yet: attach one and run scan")
        for s, info in sorted(d["devices"].items(), key=lambda x: x[1].get("name", "")):
            mark = ("*" if s == d["default"] else " ") + ("+" if s in now else " ")
            print(f"{mark} {info.get('name', '?'):12} {s:16} last seen {info.get('last_seen', '-')} ({info.get('last_mode', '-')})")
        return 0
    if cmd == "use" and len(argv) == 3:
        s = find(d, argv[2])
        if not s:
            print(f"unknown A9 '{argv[2]}' (run scan first)", file=sys.stderr); return 1
        d["default"] = s; save(d); print(f"default A9: {d['devices'][s]['name']}"); return 0
    if cmd == "name" and len(argv) == 4:
        s = find(d, argv[2])
        if not s or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", argv[3]) or find(d, argv[3]):
            print("usage: name <serial|name> <new-unique-name>", file=sys.stderr); return 1
        d["devices"][s]["name"] = argv[3]; save(d); return 0
    if cmd == "forget" and len(argv) == 3:
        s = find(d, argv[2])
        if not s:
            return 1
        del d["devices"][s]
        if d["default"] == s:
            d["default"] = next(iter(d["devices"])) if len(d["devices"]) == 1 else ""
        save(d); return 0
    if cmd == "resolve":
        # Used by a9-env.sh: never prints anything but the serial (or nothing).
        key = argv[2] if len(argv) > 2 else ""
        if key:
            s = find(d, key)
            if not s:
                print(f"A9_DEVICE='{key}' is not a known A9 (scripts/a9-devices.sh list)", file=sys.stderr)
                return 1
            print(s); return 0
        if not d["default"]:
            scan(d, quiet=True)                          # first run: record what is attached
        print(d["default"]); return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
