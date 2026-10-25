#!/usr/bin/env python3
"""soak_drift.py: the review-89f real-use soak drift and ghosting check (host-only, offline, camera frames only).

usage: soak_drift.py SERIES_DIR [--calib calib.json] [--out DIR] [--final]
  SERIES_DIR holds opening-N/run/ (the trial3b-rail-r0 output of each opening).

1. Drift. For every slice, the LAST settings_main action's camera frame at its screencap time (the probe's t_cap;
   the newest frame with mono <= t_cap) is warped to panel coordinates with the calibration homography, and three
   fixed panel regions are measured (mean and standard deviation of the grey level, 0..1):
     white  x 560-740, y  820-1400  blank right part of the Settings rows (Modes .. Storage); x >= 760 is bezel in the calib
     text   x  40-500, y  230- 300  the "Network & internet" row's text
     status x   0-824, y    0-  40  the status bar
   (The review's "x 40-780, y 1450-1600, below the list" is the Battery row and the nav bar on this A16 Settings
   page, measured on series-b frames, so the white region is the blank right-hand side of the list instead.
   --regions overrides all three.)
   Pass (review 89f section 4): the white-background mean of the LAST C slice of the last opening is within 2 % of the
   FIRST C slice of opening 1, and its standard deviation is at most 20 % higher; both at the same Settings brightness
   (orig.json of each opening; otherwise "not comparable"). Camera exposure is not locked: the numbers are relative.
2. Ghosting after each dip. Every recovered dip in dips-dmesg.txt is mapped to host monotonic time with one offset per
   boot (min over slices of frame0.mono - uptime_start, review 89d), and the camera frames of the 1 s after it are
   warped and written as one strip image (strips/opening-N-dipK.png) for the visual check. A numeric flag (not a
   verdict): the white-region standard deviation of each strip frame against that slice's reference frame; > 1.5x
   and darker is listed as "look at this strip".

Writes OUT/drift.json and OUT/drift.md. Exit 0 = drift PASS (or not final), 1 = drift FAIL, 2 = not enough data.
Reads only files; never talks to the phone.
"""
import argparse
import glob
import json
import os
import re
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probe_lib as PL  # noqa: E402

REGIONS = {"white": (560, 820, 740, 1400), "text": (40, 230, 500, 300), "status": (0, 0, 824, 40)}
MEAN_TOL, STD_RISE = 0.02, 0.20
DIP_RE = re.compile(r"^\[\s*(\d+\.\d+)\].*pg-settle: PG low after a waveform came back in (\d+) us", re.M)


def region_stats(gray):
    out = {}
    for k, (x1, y1, x2, y2) in REGIONS.items():
        r = gray[y1:y2, x1:x2]
        out[k] = {"mean": float(r.mean()), "std": float(r.std())}
    return out


def load_index(probe):
    idx = []
    p = os.path.join(probe, "frames", "index.jsonl")
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line:
                idx.append(json.loads(line))
    return idx


def frame_at(probe, idx, t):
    """newest frame with mono <= t (the frame the camera showed when the screencap was taken)"""
    best = None
    for f in idx:
        if f["mono"] <= t and (best is None or f["mono"] > best["mono"]):
            best = f
    if best is None:
        return None, None
    return os.path.join(probe, "frames", "f%05d.jpg" % best["i"]), best


def warp_gray(path, H):
    img = cv2.imread(path)
    if img is None:
        return None
    return PL.to_gray_f(PL.warp(img, H))


def slice_ref(sl_dir, H):
    """the slice's last settings_main frame -> region stats"""
    probe = os.path.join(sl_dir, "probe")
    try:
        sess = json.load(open(os.path.join(probe, "session.json")))
    except (OSError, ValueError):
        return None
    acts = [a for a in sess.get("actions", []) if a.get("name") == "settings_main" and a.get("rc") == 0 and a.get("t_cap")]
    if not acts:
        return None
    a = acts[-1]
    idx = load_index(probe)
    path, f = frame_at(probe, idx, a["t_cap"])
    if not path:
        return None
    g = warp_gray(path, H)
    if g is None:
        return None
    return {"action": a["n"], "t_cap": a["t_cap"], "frame": os.path.relpath(path, sl_dir), "frame_mono": f["mono"],
            "regions": region_stats(g)}


def opening_dirs(series):
    return sorted(glob.glob(os.path.join(series, "opening-*", "run")), key=lambda p: int(re.search(r"opening-(\d+)", p).group(1)))


def analyse(series, calib, out):
    H = np.asarray(json.load(open(calib))["H"], np.float64)
    os.makedirs(os.path.join(out, "strips"), exist_ok=True)
    res = {"openings": [], "regions": REGIONS, "limits": {"mean_rel": MEAN_TOL, "std_rise": STD_RISE}}
    for run in opening_dirs(series):
        on = int(re.search(r"opening-(\d+)", run).group(1))
        try:
            bright = (json.load(open(os.path.join(run, "orig.json"))).get("brightness") or {}).get("b")
        except (OSError, ValueError):
            bright = None
        op = {"opening": on, "brightness": bright, "slices": [], "dips": []}
        offs = []
        refs = {}
        for sl in sorted(glob.glob(os.path.join(run, "runs", "*"))):
            tag = os.path.basename(sl)
            block = tag.rsplit("-", 1)[-1]
            r = slice_ref(sl, H)
            op["slices"].append({"tag": tag, "block": block, "ref": r})
            refs[tag] = r
            try:
                u0 = json.load(open(os.path.join(sl, "rail-r0.json")))["uptime_start"]
                idx = load_index(os.path.join(sl, "probe"))
                if idx and u0 is not None:
                    offs.append((idx[0]["mono"] - u0, sl))
            except (OSError, ValueError, KeyError):
                pass
        off = min(offs)[0] if offs else None
        op["uptime_to_mono"] = off
        txt = ""
        try:
            txt = open(os.path.join(run, "dips-dmesg.txt")).read()
        except OSError:
            pass
        for k, m in enumerate(DIP_RE.finditer(txt), 1):
            t, us = float(m.group(1)), int(m.group(2))
            d = {"n": k, "dmesg_s": t, "us": us, "strip": None, "flag": None}
            if off is not None:
                tm = t + off
                for _, sl in sorted(offs, key=lambda x: x[1]):
                    probe = os.path.join(sl, "probe")
                    idx = load_index(probe)
                    win = [f for f in idx if tm <= f["mono"] <= tm + 1.0]
                    if not win:
                        continue
                    pick = [win[int(i * (len(win) - 1) / 5)] for i in range(6)] if len(win) >= 6 else win
                    grays = [warp_gray(os.path.join(probe, "frames", "f%05d.jpg" % f["i"]), H) for f in pick]
                    grays = [g for g in grays if g is not None]
                    if not grays:
                        continue
                    strip = np.hstack([cv2.resize((g * 255).astype(np.uint8), (206, 412), interpolation=cv2.INTER_AREA) for g in grays])
                    sp = os.path.join(out, "strips", "opening-%d-dip%d.png" % (on, k))
                    cv2.imwrite(sp, strip)
                    d["strip"] = os.path.relpath(sp, out)
                    d["slice"] = os.path.basename(sl)
                    ref = refs.get(os.path.basename(sl))
                    if ref:
                        rw = ref["regions"]["white"]
                        st = [region_stats(g)["white"] for g in grays]
                        # only frames that look like the Settings page (white region within 15 % of the reference) can
                        # carry a wallpaper ghost on white; a dark page (home) is reported as such
                        sett = [x for x in st if rw["mean"] and abs(x["mean"] - rw["mean"]) / rw["mean"] < 0.15]
                        d["settings_frames"] = len(sett)
                        if sett:
                            worst = max(sett, key=lambda x: x["std"])
                            d["white_std_ratio"] = round(worst["std"] / rw["std"], 3) if rw["std"] > 0 else None
                            d["flag"] = bool(d["white_std_ratio"] and d["white_std_ratio"] > 1.5 and worst["mean"] < rw["mean"])
                        else:
                            d["white_std_ratio"] = "not a Settings page"
                            d["flag"] = False
                    break
            op["dips"].append(d)
        res["openings"].append(op)
    # drift: first C slice of the first opening vs last C slice of the last opening
    cs = [(op, s) for op in res["openings"] for s in op["slices"] if s["block"] == "C" and s["ref"]]
    v = {"verdict": "NO DATA"}
    if len(cs) >= 2:
        (o0, s0), (o1, s1) = cs[0], cs[-1]
        w0, w1 = s0["ref"]["regions"]["white"], s1["ref"]["regions"]["white"]
        rel = (w1["mean"] - w0["mean"]) / w0["mean"] if w0["mean"] else None
        rise = (w1["std"] - w0["std"]) / w0["std"] if w0["std"] else None
        v = {"first": "opening-%d/%s" % (o0["opening"], s0["tag"]), "last": "opening-%d/%s" % (o1["opening"], s1["tag"]),
             "white_mean_first": w0["mean"], "white_mean_last": w1["mean"], "mean_rel": rel,
             "white_std_first": w0["std"], "white_std_last": w1["std"], "std_rise": rise,
             "brightness_first": o0["brightness"], "brightness_last": o1["brightness"]}
        if o0["brightness"] != o1["brightness"]:
            v["verdict"] = "NOT COMPARABLE (Settings brightness %s vs %s)" % (o0["brightness"], o1["brightness"])
        else:
            ok = rel is not None and abs(rel) <= MEAN_TOL and rise is not None and rise <= STD_RISE
            v["verdict"] = "PASS" if ok else "FAIL"
    res["drift"] = v
    json.dump(res, open(os.path.join(out, "drift.json"), "w"), indent=1)
    L = ["# soak drift and ghosting (review 89f section 4; camera only, exposure not locked: relative numbers)", "",
         "| opening | slice | block | frame | white mean | white std | text mean | text std | status mean |", "|---|---|---|---|---|---|---|---|---|"]
    for op in res["openings"]:
        for s in op["slices"]:
            r = s["ref"]
            if not r:
                L.append("| %d | %s | %s | no settings_main frame | | | | | |" % (op["opening"], s["tag"], s["block"]))
                continue
            g = r["regions"]
            L.append("| %d | %s | %s | %s | %.4f | %.4f | %.4f | %.4f | %.4f |" % (op["opening"], s["tag"], s["block"], r["frame"],
                     g["white"]["mean"], g["white"]["std"], g["text"]["mean"], g["text"]["std"], g["status"]["mean"]))
    L += ["", "**Drift: %s**" % v["verdict"]]
    if "mean_rel" in v:
        L.append("first %s, last %s: white mean %+.2f %% (limit +-%d %%), std %+.1f %% (limit +%d %%), brightness %s / %s" % (
            v["first"], v["last"], 100 * (v["mean_rel"] or 0), 100 * MEAN_TOL, 100 * (v["std_rise"] or 0), 100 * STD_RISE,
            v["brightness_first"], v["brightness_last"]))
    L += ["", "| opening | dip | dmesg s | us | slice | strip | white std ratio | look |", "|---|---|---|---|---|---|---|---|"]
    for op in res["openings"]:
        for d in op["dips"]:
            L.append("| %d | %d | %.3f | %d | %s | %s | %s | %s |" % (op["opening"], d["n"], d["dmesg_s"], d["us"], d.get("slice", "-"),
                     d["strip"] or "no frames", d.get("white_std_ratio", "-"), "LOOK" if d["flag"] else ""))
    L += ["", "Ghosting is a visual check: open every strip (1 s after each dip, six frames); a home-wallpaper ghost on a "
          "Settings page is a finding. The ratio column only orders them."]
    open(os.path.join(out, "drift.md"), "w").write("\n".join(L) + "\n")
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description="review 89f soak drift/ghost check (offline)")
    ap.add_argument("series")
    ap.add_argument("--calib", default="/tmp/claude-1000/-home-user-A9/138276b1-32cf-47e9-a654-4be339175015/scratchpad/probe/calib.json")
    ap.add_argument("--out")
    ap.add_argument("--final", action="store_true", help="exit 1 on a drift FAIL, 2 on no data (after the last opening)")
    ap.add_argument("--regions", help='JSON {"white": [x1,y1,x2,y2], "text": [...], "status": [...]} in panel coordinates')
    a = ap.parse_args(argv)
    if a.regions:
        REGIONS.update({k: tuple(v) for k, v in json.loads(a.regions).items()})
    out = a.out or os.path.join(a.series, "drift")
    res = analyse(a.series, a.calib, out)
    v = res["drift"]["verdict"]
    print("drift: %s (%s)" % (v, os.path.join(out, "drift.md")))
    if not a.final:
        return 0
    return 0 if v == "PASS" else (2 if v == "NO DATA" else 1)


if __name__ == "__main__":
    sys.exit(main())
