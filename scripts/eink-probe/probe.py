#!/usr/bin/env python3
"""eink-probe CLI: compare adb screencap with the E Ink glass seen by a webcam.

Never contacts the phone by itself. `act` runs the adb command prefix YOU pass
with --adb (default "adb"); point it at a fake for dry runs.
Subcommands: patterns calibrate verify warp capture watch act report
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_lib as L  # noqa: E402
from probe_output import public_output

DEFAULT_URL = "http://127.0.0.1:8093/stream"
HERE = os.path.dirname(os.path.abspath(__file__))


def jdump(obj, path):
    with open(path, "w") as f:
        json.dump(obj, f, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o))


# ------------------------------------------------------------------- simple


def cmd_patterns(a):
    for n in L.make_patterns(a.out):
        print(os.path.join(a.out, n))
    print("Push with adb (coordinator): adb push <png> /sdcard/Pictures/  then view with\n"
          "  adb shell am start -a android.intent.action.VIEW -d file:///sdcard/Pictures/pattern-white.png -t image/png\n"
          "(any viewer that shows the image edge to edge; hide system bars if the viewer allows)")


def cmd_calibrate(a):
    cal = L.calibrate(a.white, a.black, L.parse_corners(a.corners) if a.corners else None,
                      use_refs=not a.no_refs)
    cal.save(a.out)
    print("quad (TL,TR,BR,BL):", np.round(cal.quad, 1).tolist())
    print("info:", json.dumps(cal.info))
    if cal.info.get("aspect_ok") is False:
        print("WARNING: quad aspect %.2f vs expected %.2f; check or pass --corners" %
              (cal.info["aspect_h_over_w"], cal.info["aspect_expected"]), file=sys.stderr)
        return 2
    # preview: quad drawn on the white frame
    if a.preview:
        im = L.load_images(a.white)[0].copy()
        cv2.polylines(im, [cal.quad.astype(np.int32)], True, (0, 255, 0), 3)
        cv2.imwrite(a.preview, im)
        print("preview:", a.preview)


def cmd_verify(a):
    cal = L.Calib.load(a.calib)
    r = L.verify_registration(cal, L.load_images(a.image)[0], a.pattern)
    print(json.dumps(r, indent=1))
    ok = r["ncc"] > 0.5 and max(map(abs, r["shift_px_panel"])) < 12
    print("registration", "OK" if ok else "POOR")
    return 0 if ok else 3


def cmd_warp(a):
    cal = L.Calib.load(a.calib)
    os.makedirs(a.out, exist_ok=True)
    srcs = [a.src] if not os.path.isdir(a.src) else \
        [os.path.join(a.src, f) for f in sorted(os.listdir(a.src)) if f.lower().endswith((".jpg", ".png"))]
    for s in srcs:
        g = cal.warp_gray(cv2.imread(s))
        if a.norm:
            g = cal.norm(g)
        cv2.imwrite(os.path.join(a.out, os.path.basename(s).rsplit(".", 1)[0] + ".png"),
                    np.clip(g * 255, 0, 255).astype(np.uint8))
    print("wrote %d image(s) to %s" % (len(srcs), a.out))


def cmd_capture(a):
    os.makedirs(a.out, exist_ok=True)
    cap = L.Capture(a.url, os.path.join(a.out, "frames"), max_seconds=a.seconds).start()
    if not cap.wait_first(8):
        print("no frames from", a.url, cap.error, file=sys.stderr)
        cap.stop(); return 4
    time.sleep(a.seconds)
    idx = cap.stop()
    st = L.fps_stats(idx)
    jdump({"url": a.url, "seconds": a.seconds, "stats": st}, os.path.join(a.out, "capture.json"))
    print(json.dumps(st, indent=1))
    return 0 if idx else 4


# ----------------------------------------------------------------- analysis


def load_capture(framesdir):
    idx = [json.loads(l) for l in open(os.path.join(framesdir, "index.jsonl"))]
    return idx


def read_frame(framesdir, i):
    with open(os.path.join(framesdir, "f%05d.jpg" % i), "rb") as f:
        return f.read()


def analyse_watch(framesdir, cal, quiet_ms=400.0, thr=None):
    idx = load_capture(framesdir)
    times = L.frame_times(idx)
    t0 = times[0]
    thumbs = [L.thumb(cal, read_frame(framesdir, r["i"])) for r in idx]
    ser = L.activity_series(thumbs)
    # noise floor from the quietest 20% of frames (a human is active only part of the time)
    fr = np.array([s[1] for s in ser[1:]])
    base = np.sort(fr)[: max(5, len(fr) // 5)] if len(fr) else np.array([0.0])
    thr_frac = thr or float(max(np.median(base) + 6 * 1.4826 * np.median(np.abs(base - np.median(base))), 0.002))
    segs = L.segments(times, ser, thr_frac, quiet_ms)
    for s in segs:
        s["start"] -= t0; s["end"] -= t0
    return {"frames": len(idx), "fps": L.fps_stats(idx), "threshold_frac": thr_frac,
            "segments": segs, "series": [{"t": float(t - t0), "mean": s[0], "frac": s[1]}
                                         for t, s in zip(times, ser)]}


def plot_timeline(res, path):
    W, H = 1200, 260
    img = np.full((H, W, 3), 255, np.uint8)
    ser = res["series"]
    if not ser:
        return
    T = max(ser[-1]["t"], 1e-6)
    mx = max(max(s["frac"] for s in ser), res["threshold_frac"] * 2, 1e-3)
    for s in res["segments"]:
        x0, x1 = int(s["start"] / T * (W - 1)), int(s["end"] / T * (W - 1))
        cv2.rectangle(img, (x0, 0), (x1, H - 20), (235, 225, 200), -1)
    pts = np.array([[int(s["t"] / T * (W - 1)), int(H - 20 - s["frac"] / mx * (H - 40))] for s in ser], np.int32)
    cv2.polylines(img, [pts], False, (0, 0, 160), 1)
    y = int(H - 20 - res["threshold_frac"] / mx * (H - 40))
    cv2.line(img, (0, y), (W, y), (0, 140, 0), 1)
    cv2.putText(img, "frac changed px (max %.3f), thr %.4f, %.1fs" % (mx, res["threshold_frac"], T),
                (6, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 0), 1)
    cv2.imwrite(path, img)


def cmd_watch(a):
    os.makedirs(a.out, exist_ok=True)
    fdir = os.path.join(a.out, "frames")
    if not a.analyse_only:
        cap = L.Capture(a.url, fdir, max_seconds=a.seconds).start()
        if not cap.wait_first(8):
            print("no frames:", cap.error, file=sys.stderr); cap.stop(); return 4
        print("capturing %.0f s; use the phone now ..." % a.seconds, file=sys.stderr)
        time.sleep(a.seconds)
        cap.stop()
    cal = L.Calib.load(a.calib) if a.calib else None
    res = analyse_watch(fdir, cal, a.quiet_ms, a.threshold)
    plot_timeline(res, os.path.join(a.out, "activity.png"))
    jdump(res, os.path.join(a.out, "watch.json"))
    md = ["# eink-probe watch", "",
          "frames %d, fps %.1f (camera), calibrated: %s" % (res["frames"], res["fps"].get("fps", 0), bool(cal)),
          "activity threshold: %.4f of panel pixels changed between consecutive frames" % res["threshold_frac"], "",
          "| # | start s | end s | duration s | peak frac |", "|---|---|---|---|---|"]
    for i, s in enumerate(res["segments"]):
        md.append("| %d | %.2f | %.2f | %.2f | %.3f |" % (i, s["start"], s["end"], s["duration_s"], s["peak_frac"]))
    md += ["", "Activity duration = first to last frame with change; includes E Ink waveform time.",
           "![timeline](activity.png)"]
    open(os.path.join(a.out, "watch.md"), "w").write("\n".join(md) + "\n")
    print("\n".join(md[:8 + len(res["segments"])]))
    return 0


# ---------------------------------------------------------------------- act


def run_adb(prefix, args, timeout=60, binary=False):
    t = time.monotonic()
    p = subprocess.run(prefix + args, capture_output=True, timeout=timeout)
    return p, t, time.monotonic()


def wait_quiet(cap, cal, since_i, t_ret, min_ms, quiet_ms, max_ms, thr_frac, lag_ms=250.0):
    """Block until the glass has been still for quiet_ms (and min_ms elapsed
    since t_ret), or max_ms elapsed. A frame counts as active when it differs
    from the frame ~lag_ms earlier (slow E Ink changes are tiny frame to frame).
    Returns 'quiet' or 'max_wait'."""
    last_active = t_ret
    hist = []          # (mono, thumb)
    nxt = since_i
    while True:
        now = time.monotonic()
        el = (now - t_ret) * 1000
        # never fall behind real time: look at the newest frame only
        if len(cap.index) > nxt:
            r = cap.index[-1]
            nxt = len(cap.index)
            j = cap.jpegs.get(r["i"])
            if j is not None:
                th = L.thumb(cal, j)
                old = [h for h in hist if (r["mono"] - h[0]) * 1000 >= lag_ms]
                if old and L.diff_stats(th, old[-1][1])[1] > thr_frac:
                    last_active = max(last_active, r["mono"])
                hist.append((r["mono"], th))
                hist = hist[-40:]
        if el >= max_ms:
            return "max_wait"
        if el >= min_ms and (now - last_active) * 1000 >= quiet_ms:
            return "quiet"
        time.sleep(0.04)


def screencap(a, out_png):
    p, t0, t1 = run_adb(shlex.split(a.adb), shlex.split(a.screencap_cmd), timeout=30)
    data = p.stdout
    if not data.startswith(b"\x89PNG"):
        # some adb builds mangle exec-out via CRLF; try to repair
        data = data.replace(b"\r\n", b"\n")
    if not data.startswith(b"\x89PNG"):
        raise RuntimeError("screencap did not return a PNG (rc=%d, %d bytes, stderr=%r)" %
                           (p.returncode, len(p.stdout), p.stderr[:200]))
    open(out_png, "wb").write(data)
    return t0, t1


def cmd_act(a):
    sc = json.load(open(a.scenario))
    os.makedirs(os.path.join(a.out, "screencap"), exist_ok=True)
    fdir = os.path.join(a.out, "frames")
    cal = L.Calib.load(a.calib) if a.calib else None
    prefix = shlex.split(a.adb)
    cap = L.Capture(a.url, fdir, stale_reconnect_s=a.stale_reconnect_s).start()
    if not cap.wait_first(8):
        print("no camera frames from", a.url, cap.error, file=sys.stderr); cap.stop(); return 4
    time.sleep(a.preroll_ms / 1000)
    thr_frac = max(L.noise_floor(L.activity_series(
        [L.thumb(cal, cap.jpegs[r["i"]]) for r in cap.index[-40:] if r["i"] in cap.jpegs])), 0.003)
    meta = {"scenario": sc.get("name"), "adb_prefix": a.adb, "url": a.url, "calib": a.calib,
            "preroll_ms": a.preroll_ms, "threshold_frac_online": thr_frac, "actions": [],
            "cam_latency_ms_assumed": a.cam_latency_ms}
    try:
        n = 0
        t0, t1 = screencap(a, os.path.join(a.out, "screencap", "00-initial.png"))
        meta["initial"] = {"name": "initial", "screencap": "screencap/00-initial.png", "t_cap": t1}
        for st in sc["steps"]:
            n += 1
            name = st.get("name", "step%d" % n)
            rec = {"n": n, "name": name}
            if "wait_ms" in st and "adb" not in st:
                time.sleep(st["wait_ms"] / 1000.0)
                continue
            since = len(cap.index)
            p, ts, tr = run_adb(prefix, st["adb"], timeout=st.get("timeout_s", 60))
            rec.update({"adb": st["adb"], "t_send": ts, "t_ret": tr, "rc": p.returncode,
                        "stdout": public_output(p.stdout, prefix),
                        "stderr": public_output(p.stderr, prefix)})
            if p.returncode != 0:
                rec["error"] = "adb rc=%d" % p.returncode
            why = wait_quiet(cap, cal, since, tr, st.get("min_wait_ms", sc.get("min_wait_ms", 1500)),
                             st.get("quiet_ms", sc.get("quiet_ms", 700)),
                             st.get("max_wait_ms", sc.get("max_wait_ms", 10000)), thr_frac)
            rec["wait_ended"] = why
            rec["t_quiet"] = time.monotonic()
            png = "screencap/%02d-%s.png" % (n, name)
            c0, c1 = screencap(a, os.path.join(a.out, png))
            rec.update({"screencap": png, "t_cap": c1})
            meta["actions"].append(rec)
            print("  %-16s rc=%d %s" % (name, p.returncode, why), file=sys.stderr)
            if p.returncode != 0 and a.stop_on_error:
                break
    finally:
        idx = cap.stop()
    meta["capture_stats"] = L.fps_stats(idx)
    meta["camera_error"] = cap.error
    # camera health: stream reopens and frame gaps; every action whose window overlaps one has INVALID optical data
    gaps = cap.stale_gaps()
    meta["camera_reconnects"] = cap.reconnects
    meta["camera_stale_gaps"] = [{"from": g0, "to": g1} for g0, g1 in gaps]
    bad = [(g0, g1) for g0, g1 in gaps] + [(r["mono"] - r["gap_s"], r["mono"]) for r in cap.reconnects]
    for rec in meta["actions"]:
        t0, t1 = rec.get("t_send"), rec.get("t_quiet")
        if t0 is not None and t1 is not None and any(g0 < t1 and g1 > t0 for g0, g1 in bad):
            rec["optical_invalid"] = "camera stream gap/reconnect during this action"
    jdump(meta, os.path.join(a.out, "session.json"))
    if a.no_report:
        return 0
    return report_session(a.out, cal, a.cam_latency_ms)


# ------------------------------------------------------------------- report


def contact_sheet(panels, path, scale=0.6):
    """panels: list of (label, grey float 0..1 half-res)."""
    tiles = []
    for label, g in panels:
        im = cv2.cvtColor(np.clip(g * 255, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
        im = cv2.resize(im, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        cv2.rectangle(im, (0, 0), (im.shape[1], 18), (255, 255, 255), -1)
        cv2.putText(im, label, (4, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 1)
        cv2.rectangle(im, (0, 0), (im.shape[1] - 1, im.shape[0] - 1), (128, 128, 128), 1)
        tiles.append(im)
    cv2.imwrite(path, np.hstack(tiles))


def report_session(sdir, cal, cam_latency_ms=0.0, quiet_ms=500.0):
    meta = json.load(open(os.path.join(sdir, "session.json")))
    fdir = os.path.join(sdir, "frames")
    idx = load_capture(fdir)
    if not idx:
        print("no frames", file=sys.stderr); return 4
    times = L.frame_times(idx)
    # frame_times() is offset-corrected camera time on the host monotonic clock
    thumbs = [L.thumb(cal, read_frame(fdir, r["i"])) for r in idx]
    os.makedirs(os.path.join(sdir, "sheets"), exist_ok=True)
    res = {"calibrated": bool(cal), "normalised_with_refs": bool(cal and cal.white is not None),
           "camera": L.fps_stats(idx), "cam_latency_ms_subtracted": cam_latency_ms, "actions": []}
    prev_png = L.png_to_gray(os.path.join(sdir, meta["initial"]["screencap"]))
    acts = meta["actions"]
    for k, rec in enumerate(acts):
        t_end = acts[k + 1]["t_send"] if k + 1 < len(acts) else times[-1]
        tm = L.action_timing(times, thumbs, rec["t_send"], t_end, settle_ms=quiet_ms)
        tm["input_return_ms"] = (rec["t_ret"] - rec["t_send"]) * 1000
        for key in ("first_change_ms", "settle_ms"):
            if tm.get(key) is not None:
                tm[key + "_since_adb_return"] = tm[key] - tm["input_return_ms"]
                tm[key] = max(tm[key] - cam_latency_ms, 0.0)
        # settled frames: last <=0.5 s before the screencap, after the action began
        win = [r["i"] for r, t in zip(idx, times) if rec["t_quiet"] - 0.5 <= t <= rec["t_quiet"] + 0.05]
        win = win[-6:] or [idx[-1]["i"]]
        row = {"n": rec["n"], "name": rec["name"], "adb_rc": rec["rc"], "wait_ended": rec["wait_ended"],
               "timing": tm, "settled_frames": win}
        try:
            S = L.png_to_gray(os.path.join(sdir, rec["screencap"]))
            C = L.settled_camera(cal, [read_frame(fdir, i) for i in win]) if cal else None
            if C is None:
                C = np.mean([L.thumb(None, read_frame(fdir, i)) for i in win], axis=0)
                C = cv2.resize(C, (L.PANEL_W, L.PANEL_H))
            q = L.quality_metrics(C, S, prev_png)
            arr = q.pop("_arrays")
            row["quality"] = q
            panels = [("prev screencap", L._half(prev_png)), ("screencap (SF)", arr["s"]),
                      ("glass (norm)", arr["c"]),
                      ("|glass-SF| x2", np.clip(np.abs(arr["c"] - arr["s"]) * 2, 0, 1))]
            contact_sheet(panels, os.path.join(sdir, "sheets", "%02d-%s.png" % (rec["n"], rec["name"])))
            row["sheet"] = "sheets/%02d-%s.png" % (rec["n"], rec["name"])
            prev_png = S
        except Exception as e:  # keep going
            row["error"] = repr(e)
        res["actions"].append(row)
    jdump(res, os.path.join(sdir, "report.json"))
    write_md(res, os.path.join(sdir, "report.md"), meta)
    print(open(os.path.join(sdir, "report.md")).read())
    return 0


def fmt(v, f="%.2f"):
    return "-" if v is None else f % v


def write_md(res, path, meta):
    cam = res["camera"]
    md = ["# eink-probe report", "",
          "scenario `%s`, camera %.1f fps (resolution floor %.0f ms), refs-normalised: %s" %
          (meta.get("scenario"), cam.get("fps", 0), cam.get("resolution_floor_ms", 0), res["normalised_with_refs"]),
          "", "Latency columns are host time from the adb command start to the first/last camera-visible change",
          "(includes adb + `input` startup and camera pipeline latency; see docs/eink-probe.md).", "",
          "| # | action | first change ms | settle ms | settled | contrast (Mich.) | white | black | edge sharp | ghost | speckle | NCC |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in res["actions"]:
        t, q = r["timing"], r.get("quality", {})
        md.append("| %d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["n"], r["name"], fmt(t.get("first_change_ms"), "%.0f"), fmt(t.get("settle_ms"), "%.0f"),
            "yes" if t.get("settled") else "NO", fmt(q.get("contrast_michelson")), fmt(q.get("white_level")),
            fmt(q.get("black_level")), fmt(q.get("edge_sharpness_ratio")), fmt(q.get("ghost_residual")),
            fmt(q.get("speckle_std"), "%.3f"), fmt(q.get("ncc_vs_screencap"))))
    md += ["", "Sheets (prev screencap | screencap | glass | diff):", ""]
    md += ["- %s" % r["sheet"] for r in res["actions"] if r.get("sheet")]
    errs = [(r["n"], r["error"]) for r in res["actions"] if r.get("error")]
    if errs:
        md += ["", "Errors: %s" % errs]
    open(path, "w").write("\n".join(md) + "\n")


def cmd_report(a):
    cal = L.Calib.load(a.calib) if a.calib else None
    return report_session(a.session, cal, a.cam_latency_ms)


def main():
    ap = argparse.ArgumentParser(prog="eink-probe", description=__doc__.split("\n")[1])
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("patterns", help="write calibration PNGs (white/black/checker, 824x1648)")
    p.add_argument("--out", required=True); p.set_defaults(f=cmd_patterns)
    p = sp.add_parser("calibrate", help="screen quad from a white and a black camera capture")
    p.add_argument("--white", required=True, help="image or dir of frames with the white pattern")
    p.add_argument("--black", required=True)
    p.add_argument("--corners", help="manual: x,y of TL,TR,BR,BL (8 numbers), skips detection")
    p.add_argument("--no-refs", action="store_true", help="geometry only, no flat-field refs")
    p.add_argument("--preview"); p.add_argument("--out", required=True); p.set_defaults(f=cmd_calibrate)
    p = sp.add_parser("verify", help="check registration on a camera image of the checker pattern")
    p.add_argument("--calib", required=True); p.add_argument("--image", required=True)
    p.add_argument("--pattern", required=True); p.set_defaults(f=cmd_verify)
    p = sp.add_parser("warp", help="warp camera image(s) to panel space")
    p.add_argument("--calib", required=True); p.add_argument("--norm", action="store_true")
    p.add_argument("src"); p.add_argument("--out", required=True); p.set_defaults(f=cmd_warp)
    p = sp.add_parser("capture", help="record MJPEG frames and measure fps")
    p.add_argument("--url", default=DEFAULT_URL); p.add_argument("--seconds", type=float, default=5)
    p.add_argument("--out", required=True); p.set_defaults(f=cmd_capture)
    p = sp.add_parser("watch", help="capture N s while a human uses the phone; activity timeline")
    p.add_argument("--url", default=DEFAULT_URL); p.add_argument("--seconds", type=float, default=30)
    p.add_argument("--calib"); p.add_argument("--out", required=True)
    p.add_argument("--quiet-ms", type=float, default=400); p.add_argument("--threshold", type=float)
    p.add_argument("--analyse-only", action="store_true", help="re-analyse existing OUT/frames")
    p.set_defaults(f=cmd_watch)
    p = sp.add_parser("act", help="run a scripted scenario with camera capture and measure")
    p.add_argument("--adb", default="adb", help="adb command prefix, e.g. 'adb -s SERIAL' or a fake")
    p.add_argument("--scenario", default=os.path.join(HERE, "scenarios", "default.json"))
    p.add_argument("--screencap-cmd", default="exec-out screencap -p")
    p.add_argument("--url", default=DEFAULT_URL); p.add_argument("--calib")
    p.add_argument("--stale-reconnect-s", type=float, default=3.0,
                   help="reopen the MJPEG stream when no frame arrived for this long (0 = never); recorded in session.json")
    p.add_argument("--out", required=True); p.add_argument("--preroll-ms", type=float, default=1500)
    p.add_argument("--cam-latency-ms", type=float, default=0.0,
                   help="known camera pipeline latency to subtract (default 0; unmeasured)")
    p.add_argument("--stop-on-error", action="store_true"); p.add_argument("--no-report", action="store_true")
    p.set_defaults(f=cmd_act)
    p = sp.add_parser("report", help="(re)analyse a session directory written by `act`")
    p.add_argument("session"); p.add_argument("--calib"); p.add_argument("--cam-latency-ms", type=float, default=0.0)
    p.set_defaults(f=cmd_report)
    a = ap.parse_args()
    return a.f(a) or 0


if __name__ == "__main__":
    sys.exit(main())
