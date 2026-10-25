#!/usr/bin/env python3
"""check-conf-vs-dtb.py -- the HAL's Android thresholds against the kernel's own trips.

Usage: check-conf-vs-dtb.py <thermal-a9.conf> <thermal DTB>   (offline; reads two files)

The DTB is the one the boot image carries (a kernel snapshot's
sm6115-hisense-hlte556n-android*.dtb whose dtb.source is a *-thermal.dtb).
For every HAL sensor whose source is a thermal zone with trips:
  - CPU/GPU/SKIN sensors sit on zones with passive trips AND cooling-maps (the kernel
    mitigates); zones without cooling-maps are informational and carry no thresholds;
  - LIGHT    == the zone's lowest passive trip (Android follows the first kernel stage);
  - MODERATE == the second passive trip, if the zone has one;
  - SHUTDOWN <= the zone's critical trip - 5 C (Android's clean shutdown comes first);
  - no Android threshold is set on a zone the kernel does not mitigate unless it is
    the battery (power_supply, BatteryService/ThermalManagerService only).
Also: every CPU zone of the DTB (cpu*-thermal) is a HAL sensor. Exit 0 = PASS.
"""
import struct
import sys

FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9


def parse_fdt(data):
    magic, total, off_struct, off_strings = struct.unpack(">IIII", data[:16])
    if magic != 0xD00DFEED:
        raise SystemExit("not a flattened device tree")
    strings = data[off_strings:]
    root = None
    stack, p = [], off_struct
    while True:
        tok = struct.unpack(">I", data[p:p + 4])[0]
        p += 4
        if tok == FDT_BEGIN_NODE:
            end = data.index(b"\0", p)
            n = {"name": data[p:end].decode(), "props": {}, "children": []}
            p = (end + 4) & ~3
            if stack:
                stack[-1]["children"].append(n)
            else:
                root = n
            stack.append(n)
        elif tok == FDT_END_NODE:
            stack.pop()
        elif tok == FDT_PROP:
            ln, nameoff = struct.unpack(">II", data[p:p + 8])
            p += 8
            pname = strings[nameoff:strings.index(b"\0", nameoff)].decode()
            stack[-1]["props"][pname] = data[p:p + ln]
            p = (p + ln + 3) & ~3
        elif tok == FDT_NOP:
            continue
        elif tok == FDT_END:
            return root
        else:
            raise SystemExit(f"bad FDT token {tok}")


def child(n, name):
    for c in n["children"]:
        if c["name"] == name:
            return c
    return None


def zones(dtb):
    tz = child(parse_fdt(dtb), "thermal-zones")
    if not tz:
        raise SystemExit("DTB has no /thermal-zones")
    out = {}
    for z in tz["children"]:
        trips = []
        t = child(z, "trips")
        for tr in (t["children"] if t else []):
            temp = struct.unpack(">i", tr["props"]["temperature"][:4])[0] / 1000.0
            typ = tr["props"]["type"].rstrip(b"\0").decode()
            trips.append((temp, typ))
        out[z["name"]] = {"trips": sorted(trips), "maps": bool(child(z, "cooling-maps") and child(z, "cooling-maps")["children"])}
    return out


def conf(path):
    rows = []
    for line in open(path):
        line = line.split("#", 1)[0].split()
        if not line:
            continue
        hot = [None if v == "-" else float(v) for v in line[4:10]]
        srcs = [s.split("@")[0] for s in line[2].split("|")]
        rows.append((line[0], line[1], srcs, hot))
    return rows


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    z = zones(open(sys.argv[2], "rb").read())
    bad, ok, sensors_on_zones = [], 0, set()
    for name, typ, srcs, hot in conf(sys.argv[1]):
        zs = [s.split(":", 1)[1] for s in srcs if s.split(":", 1)[0] in ("zone", "adczone")]
        for zn in zs:
            sensors_on_zones.add(zn)
            if zn not in z:
                bad.append(f"{name}: zone {zn} not in the DTB")
                continue
            passive = [t for t, k in z[zn]["trips"] if k == "passive"]
            crit = [t for t, k in z[zn]["trips"] if k == "critical"]
            if not (passive and z[zn]["maps"]):
                # informational zone (upstream hot alerts at 115/125 C have no cooling-maps)
                if any(h is not None for h in hot) and typ != "BATTERY":
                    bad.append(f"{name}: Android thresholds on {zn}, which the kernel does not mitigate")
                if typ in ("CPU", "GPU", "SKIN"):
                    bad.append(f"{name}: {typ} sensor on {zn}, which has no cooling-maps")
                continue
            if hot[0] != passive[0]:
                bad.append(f"{name}: LIGHT {hot[0]} != first passive trip {passive[0]} of {zn}")
            if len(passive) > 1 and hot[1] != passive[1]:
                bad.append(f"{name}: MODERATE {hot[1]} != second passive trip {passive[1]} of {zn}")
            if crit and (hot[5] is None or hot[5] > crit[0] - 5):
                bad.append(f"{name}: SHUTDOWN {hot[5]} not >= 5 C under the critical trip {crit[0]} of {zn}")
            ok += 1
            print(f"ok   {name:8s} {zn:20s} passive {passive} critical {crit} -> LIGHT {hot[0]} MODERATE {hot[1]} SHUTDOWN {hot[5]}")
    for zn in z:
        if zn.startswith("cpu") and zn not in sensors_on_zones:
            bad.append(f"CPU zone {zn} of the DTB is not a HAL sensor")
    for b in bad:
        print("FAIL", b)
    print(f"{'PASS' if not bad and ok else 'FAIL'}: {ok} mitigated zones checked, {len(bad)} problems")
    sys.exit(1 if bad or not ok else 0)


if __name__ == "__main__":
    main()
