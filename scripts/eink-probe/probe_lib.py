"""eink-probe core library (2026-10-17).

Host-side only. Compares what SurfaceFlinger rendered (adb screencap) with
what the E Ink glass shows (fixed webcam). Nothing in here talks to a phone:
`act` shells out to a configurable adb command prefix that the caller owns.

Conventions
  * panel space  = 824x1648 (portrait, as Android sees it)
  * metric space = 412x824 (half panel): the camera sees ~415 px across the
    panel, so finer comparison would measure nothing real.
  * "norm" images are float32 grey in 0..1, flat-field normalised with the
    white/black reference captures when calibrated with them.
"""
import json
import os
import re
import socket
import threading
import time
import urllib.request

import cv2
import numpy as np

PANEL_W, PANEL_H = 824, 1648
MET_W, MET_H = 412, 824

# ----------------------------------------------------------------- geometry


def order_quad(pts):
    """Return corners as TL, TR, BR, BL (float32 4x2)."""
    p = np.asarray(pts, np.float32).reshape(4, 2)
    s = p.sum(1)
    d = p[:, 0] - p[:, 1]
    tl, br = p[np.argmin(s)], p[np.argmax(s)]
    tr, bl = p[np.argmax(d)], p[np.argmin(d)]
    return np.array([tl, tr, br, bl], np.float32)


def homography_from_quad(quad, w=PANEL_W, h=PANEL_H):
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], np.float32)
    return cv2.getPerspectiveTransform(order_quad(quad), dst)


def warp(img, H, w=PANEL_W, h=PANEL_H):
    return cv2.warpPerspective(img, np.asarray(H, np.float64), (w, h),
                               flags=cv2.INTER_AREA)


def to_gray_f(img):
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img.astype(np.float32) / 255.0


def mean_frames(frames):
    return np.mean([f.astype(np.float32) for f in frames], axis=0)


def _gain_match(ref, img, iters=6):
    """Robustly fit img*g+o to ref (auto-exposure moves the whole frame between
    captures); the panel pixels are the outliers and get trimmed out."""
    x = img.ravel()[::7]; y = ref.ravel()[::7]
    keep = np.ones(x.shape, bool)
    g, o = 1.0, 0.0
    for _ in range(iters):
        if keep.sum() < 100:
            break
        A = np.vstack([x[keep], np.ones(keep.sum())]).T
        g, o = np.linalg.lstsq(A, y[keep], rcond=None)[0]
        r = np.abs(y - (g * x + o))
        keep = r < max(np.percentile(r, 60), 0.01)
    return img * g + o


def detect_quad_from_pair(white, black, min_area_frac=0.02):
    """Screen quad from a white-screen and a black-screen camera image.

    The only thing that changes between them is the glass, so the blurred
    absolute difference is bright exactly over the panel. Returns
    (quad 4x2, info dict). Raises ValueError when nothing plausible is found.
    """
    gw, gb = to_gray_f(white), to_gray_f(black)
    gb = _gain_match(gw, gb)
    d = cv2.GaussianBlur(np.abs(gw - gb), (0, 0), 3)
    d8 = np.clip(d / max(d.max(), 1e-6) * 255, 0, 255).astype(np.uint8)
    thr, mask = cv2.threshold(d8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # refine: half of the in-panel median is the true edge of a blurred step
    inner = d[mask > 0]
    if inner.size:
        mask = (d > 0.5 * np.median(inner)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        raise ValueError("no region differs between white and black frames "
                         "(is the pattern shown? frontlight off?)")
    c = max(cnts, key=cv2.contourArea)
    area = cv2.contourArea(c)
    if area < min_area_frac * mask.size:
        raise ValueError("changed region too small (%.4f of frame)" % (area / mask.size))
    hull = cv2.convexHull(c)
    quad = None
    peri = cv2.arcLength(hull, True)
    for eps in (0.01, 0.02, 0.03, 0.05, 0.08):
        ap = cv2.approxPolyDP(hull, eps * peri, True)
        if len(ap) == 4:
            quad = ap.reshape(4, 2)
            break
    method = "approxPolyDP"
    if quad is None:
        quad = cv2.boxPoints(cv2.minAreaRect(hull))
        method = "minAreaRect"
    quad = order_quad(quad)
    w = np.linalg.norm(quad[1] - quad[0]); h = np.linalg.norm(quad[3] - quad[0])
    ratio = (h / w) if w else 0
    return quad.astype(np.float32), {
        "method": method, "otsu": float(thr), "area_px": float(area),
        "aspect_h_over_w": float(ratio), "aspect_expected": PANEL_H / PANEL_W,
        "aspect_ok": bool(abs(ratio / (PANEL_H / PANEL_W) - 1) < 0.25)}


# --------------------------------------------------------------- calibration


class Calib:
    def __init__(self, quad, H, white=None, black=None, info=None):
        self.quad = np.asarray(quad, np.float32)
        self.H = np.asarray(H, np.float64)
        self.white, self.black = white, black   # warped float32 camera grey, panel space
        self.info = info or {}

    def warp_gray(self, img):
        return to_gray_f(warp(img, self.H))

    def norm(self, g):
        """Flat-field normalise a warped grey frame (float 0..1) to 0..1."""
        if self.white is not None and self.black is not None:
            span = np.maximum(self.white - self.black, 0.02)
            return np.clip((g - self.black) / span, 0, 1.2).astype(np.float32)
        lo, hi = np.percentile(g, 1), np.percentile(g, 99)
        return np.clip((g - lo) / max(hi - lo, 1e-3), 0, 1.2).astype(np.float32)

    def save(self, path):
        base = os.path.splitext(path)[0]
        d = {"version": 1, "panel": [PANEL_W, PANEL_H],
             "quad_tl_tr_br_bl": self.quad.tolist(), "H": self.H.tolist(),
             "info": self.info, "has_refs": self.white is not None}
        if self.white is not None:
            cv2.imwrite(base + ".white.png", np.clip(self.white * 65535, 0, 65535).astype(np.uint16))
            cv2.imwrite(base + ".black.png", np.clip(self.black * 65535, 0, 65535).astype(np.uint16))
            d["white_png"] = os.path.basename(base) + ".white.png"
            d["black_png"] = os.path.basename(base) + ".black.png"
        with open(path, "w") as f:
            json.dump(d, f, indent=1)

    @staticmethod
    def load(path):
        with open(path) as f:
            d = json.load(f)
        white = black = None
        if d.get("has_refs"):
            dd = os.path.dirname(path)
            white = cv2.imread(os.path.join(dd, d["white_png"]), cv2.IMREAD_UNCHANGED).astype(np.float32) / 65535
            black = cv2.imread(os.path.join(dd, d["black_png"]), cv2.IMREAD_UNCHANGED).astype(np.float32) / 65535
        return Calib(d["quad_tl_tr_br_bl"], d["H"], white, black, d.get("info"))


def load_images(src):
    """A file or a directory of jpg/png -> list of BGR images."""
    if os.path.isdir(src):
        files = sorted(f for f in os.listdir(src) if f.lower().endswith((".jpg", ".jpeg", ".png")))
        if not files:
            raise ValueError("no images in " + src)
        return [cv2.imread(os.path.join(src, f)) for f in files]
    im = cv2.imread(src)
    if im is None:
        raise ValueError("cannot read " + src)
    return [im]


def calibrate(white_src, black_src, corners=None, use_refs=True):
    W = mean_frames(load_images(white_src)); B = mean_frames(load_images(black_src))
    if corners is not None:
        quad = order_quad(corners); info = {"method": "manual"}
    else:
        quad, info = detect_quad_from_pair(W, B)
    H = homography_from_quad(quad)
    cal = Calib(quad, H, info=info)
    if use_refs:
        cal.white = to_gray_f(warp(W.astype(np.uint8), H))
        cal.black = to_gray_f(warp(B.astype(np.uint8), H))
        info["white_level"] = float(np.median(cal.white))
        info["black_level"] = float(np.median(cal.black))
        info["panel_contrast_michelson"] = float(
            (info["white_level"] - info["black_level"]) / max(info["white_level"] + info["black_level"], 1e-6))
    return cal


def parse_corners(s):
    v = [float(x) for x in re.split(r"[,\s]+", s.strip()) if x]
    if len(v) != 8:
        raise ValueError("--corners wants 8 numbers: x,y of 4 corners")
    return np.array(v, np.float32).reshape(4, 2)


# ------------------------------------------------------------ test patterns


def make_patterns(outdir):
    os.makedirs(outdir, exist_ok=True)
    white = np.full((PANEL_H, PANEL_W, 3), 255, np.uint8)
    black = np.zeros((PANEL_H, PANEL_W, 3), np.uint8)
    chk = np.full((PANEL_H, PANEL_W, 3), 255, np.uint8)
    s = 103
    for y in range(0, PANEL_H, s):
        for x in range(0, PANEL_W, s):
            if ((x // s) + (y // s)) % 2 == 0:
                chk[y:y + s, x:x + s] = 0
    for (cx, cy) in [(0, 0), (PANEL_W, 0), (PANEL_W, PANEL_H), (0, PANEL_H)]:
        cv2.circle(chk, (cx, cy), 90, (128, 128, 128), -1)
    for n, im in (("white", white), ("black", black), ("checker", chk)):
        cv2.imwrite(os.path.join(outdir, "pattern-%s.png" % n), im)
    return ["pattern-white.png", "pattern-black.png", "pattern-checker.png"]


def verify_registration(cal, cam_img, pattern_path):
    """Warp a camera image of the checker pattern and report residual shift/NCC."""
    pat = to_gray_f(cv2.imread(pattern_path))
    g = cal.norm(cal.warp_gray(cam_img))
    a = cv2.resize(pat, (MET_W, MET_H), interpolation=cv2.INTER_AREA)
    b = cv2.resize(g, (MET_W, MET_H), interpolation=cv2.INTER_AREA)
    a = cv2.GaussianBlur(a, (0, 0), 2); b = cv2.GaussianBlur(b, (0, 0), 2)
    (dx, dy), resp = cv2.phaseCorrelate(a.astype(np.float64), b.astype(np.float64))
    return {"shift_px_metric": [float(dx), float(dy)], "shift_px_panel": [2 * float(dx), 2 * float(dy)],
            "ncc": ncc(a, b), "phase_response": float(resp)}


def ncc(a, b):
    a = a - a.mean(); b = b - b.mean()
    den = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / den) if den > 0 else 0.0


# ------------------------------------------------------------------ capture

_HDR_LEN = re.compile(rb"content-length:\s*(\d+)", re.I)
_HDR_TS = re.compile(rb"x-timestamp:\s*([0-9.]+)", re.I)


class MjpegParser:
    """Incremental multipart/x-mixed-replace parser (boundary-agnostic).

    Frames are located by Content-Length when present, else by JPEG SOI/EOI.
    feed() returns [(jpeg_bytes, cam_ts_or_None)].
    """

    def __init__(self):
        self.buf = b""

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            hdr_end = self.buf.find(b"\r\n\r\n")
            soi = self.buf.find(b"\xff\xd8")
            if hdr_end == -1 and soi == -1:
                self.buf = self.buf[-8:] if len(self.buf) > 4096 else self.buf
                break
            if hdr_end != -1 and (soi == -1 or hdr_end < soi):
                head = self.buf[:hdr_end]
                m = _HDR_LEN.search(head)
                if m:
                    n = int(m.group(1)); start = hdr_end + 4
                    if len(self.buf) < start + n:
                        break
                    ts = _HDR_TS.search(head)
                    out.append((self.buf[start:start + n], float(ts.group(1)) if ts else None))
                    self.buf = self.buf[start + n:]
                    continue
                self.buf = self.buf[hdr_end + 4:]
                continue
            # no usable header: raw JPEG scan
            eoi = self.buf.find(b"\xff\xd9", soi + 2)
            if eoi == -1:
                break
            out.append((self.buf[soi:eoi + 2], None))
            self.buf = self.buf[eoi + 2:]
        return out


class Capture:
    """Background MJPEG recorder. Frames land in `outdir` as f%05d.jpg and
    index.jsonl lines {i, mono, wall, cam_ts, bytes} (host clocks at arrival)."""

    def __init__(self, url, outdir, max_seconds=None, keep_in_memory=2000, stale_reconnect_s=3.0, max_reconnects=60):
        self.url, self.outdir, self.max_seconds = url, outdir, max_seconds
        self.index = []          # list of dicts, appended in order
        self.jpegs = {}          # i -> bytes (bounded)
        self.keep = keep_in_memory
        self._stop = threading.Event()
        self._t = None
        self.error = None
        self.t0_mono = None
        # A stream that delivers nothing for stale_reconnect_s (0 = never) is closed and reopened; every reopen is
        # recorded in `reconnects` ({"mono", "n", "why", "gap_s"}) so callers can mark the optical data around it invalid.
        self.stale_reconnect_s = stale_reconnect_s
        self.max_reconnects = max_reconnects
        self.reconnects = []
        os.makedirs(outdir, exist_ok=True)

    def start(self):
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()
        return self

    def stop(self):
        self._stop.set()
        if self._t:
            self._t.join(timeout=5)
        with open(os.path.join(self.outdir, "index.jsonl"), "w") as f:
            for r in self.index:
                f.write(json.dumps(r) + "\n")
        return self.index

    def wait_first(self, timeout=5.0):
        t = time.monotonic()
        while not self.index and time.monotonic() - t < timeout and not self.error:
            time.sleep(0.02)
        return bool(self.index)

    def stale_gaps(self, min_gap_s=None):
        """[(mono_from, mono_to)] where consecutive frames arrived more than min_gap_s apart (default stale_reconnect_s or 3 s)."""
        lim = min_gap_s if min_gap_s is not None else (self.stale_reconnect_s or 3.0)
        out = []
        for a, b in zip(self.index, self.index[1:]):
            if b["mono"] - a["mono"] > lim:
                out.append((a["mono"], b["mono"]))
        return out

    def _read_stream(self):
        """One connection. Returns why it ended: 'stop', 'max_seconds', 'eof' or the exception text."""
        timeout = max(self.stale_reconnect_s, 1.0) if self.stale_reconnect_s else 10
        req = urllib.request.Request(self.url)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            p = MjpegParser()
            if self.t0_mono is None:
                self.t0_mono = time.monotonic()
            while not self._stop.is_set():
                if self.max_seconds and time.monotonic() - self.t0_mono > self.max_seconds:
                    return "max_seconds"
                chunk = r.read1(65536) if hasattr(r, "read1") else r.read(65536)
                if not chunk:
                    return "eof"
                now_m, now_w = time.monotonic(), time.time()
                for jpg, ts in p.feed(chunk):
                    i = len(self.index)
                    with open(os.path.join(self.outdir, "f%05d.jpg" % i), "wb") as f:
                        f.write(jpg)
                    self.index.append({"i": i, "mono": now_m, "wall": now_w,
                                       "cam_ts": ts, "bytes": len(jpg)})
                    self.jpegs[i] = jpg
                    if len(self.jpegs) > self.keep:
                        self.jpegs.pop(min(self.jpegs))
        return "stop"

    def _run(self):
        backoff = 0.3
        while not self._stop.is_set():
            n0 = len(self.index)
            try:
                why = self._read_stream()
            except Exception as e:  # noqa
                why = repr(e)
            if why in ("stop", "max_seconds") or self._stop.is_set():
                return
            # a stream that never produced a frame keeps the old behaviour: report the error and give up at once
            if not self.index or not self.stale_reconnect_s or len(self.reconnects) >= self.max_reconnects:
                self.error = why if why != "eof" else "stream ended"
                return
            last = self.index[-1]["mono"]
            self.reconnects.append({"mono": time.monotonic(), "n": len(self.reconnects) + 1, "why": why,
                                    "gap_s": round(time.monotonic() - last, 3)})
            self._stop.wait(backoff)
            backoff = 0.3 if len(self.index) > n0 else min(backoff * 2, 2.0)


def fps_stats(index):
    """Measured fps from camera timestamps (if present) and from host arrival."""
    res = {"frames": len(index)}
    if len(index) < 3:
        return res
    mono = np.array([r["mono"] for r in index])
    res["duration_s_host"] = float(mono[-1] - mono[0])
    # Arrival is bursty (socket reads); use whole-span fps, not per-frame gaps.
    res["fps_host"] = float((len(index) - 1) / max(mono[-1] - mono[0], 1e-9))
    ts = [r.get("cam_ts") for r in index]
    if all(t is not None for t in ts):
        ts = np.array(ts)
        dts = np.diff(ts)
        res["fps_cam"] = float((len(ts) - 1) / max(ts[-1] - ts[0], 1e-9))
        res["cam_dt_ms_median"] = float(np.median(dts) * 1000)
        res["cam_dt_ms_p95"] = float(np.percentile(dts, 95) * 1000)
        res["cam_dt_ms_max"] = float(dts.max() * 1000)
        res["dropped_gaps_gt_1_5x"] = int((dts > 1.5 * np.median(dts)).sum())
    res["fps"] = res.get("fps_cam", res["fps_host"])
    res["resolution_floor_ms"] = 1000.0 / res["fps"]
    return res


def frame_times(index):
    """Frame times on the host monotonic clock. Camera X-Timestamp (same box,
    wall clock) gives low-jitter spacing; its offset to host monotonic is the
    median over the clip. Falls back to arrival times."""
    mono = np.array([r["mono"] for r in index], float)
    ts = [r.get("cam_ts") for r in index]
    if len(index) and all(t is not None for t in ts):
        ts = np.array(ts, float)
        off = np.median(mono - ts)
        # cam_ts is wall-clock; the offset also absorbs wall-vs-mono.
        return ts + off
    return mono


# ---------------------------------------------------------------- activity


TH_W, TH_H = 103, 206


def _thumb_refs(cal):
    if getattr(cal, "_th_refs", None) is None:
        if cal.white is not None:
            cal._th_refs = (cv2.resize(cal.white, (TH_W, TH_H), interpolation=cv2.INTER_AREA),
                            cv2.resize(cal.black, (TH_W, TH_H), interpolation=cv2.INTER_AREA))
        else:
            cal._th_refs = (None, None)
    return cal._th_refs


def thumb(cal, jpg_or_img):
    """Small normalised grey thumbnail of the panel (103x206) for activity.
    From JPEG bytes this uses DCT-domain 1/4 decoding (fast enough for 30 fps)."""
    if isinstance(jpg_or_img, (bytes, bytearray)):
        g4 = cv2.imdecode(np.frombuffer(jpg_or_img, np.uint8), cv2.IMREAD_REDUCED_GRAYSCALE_4)
        scale_src = 4.0
        g = g4.astype(np.float32) / 255.0
    else:
        g = to_gray_f(jpg_or_img)
        scale_src = 1.0
    if cal is None:
        lo, hi = np.percentile(g, 1), np.percentile(g, 99)
        g = np.clip((g - lo) / max(hi - lo, 1e-3), 0, 1.2)
        return cv2.resize(g, (TH_W, TH_H), interpolation=cv2.INTER_AREA)
    # camera(1/scale_src) -> thumb(2x) with the full-res homography rescaled
    S_src = np.diag([1 / scale_src, 1 / scale_src, 1.0])
    S_dst = np.diag([2 * TH_W / PANEL_W, 2 * TH_H / PANEL_H, 1.0])
    Hs = S_dst @ cal.H @ np.linalg.inv(S_src)
    w2 = cv2.warpPerspective(g, Hs, (2 * TH_W, 2 * TH_H), flags=cv2.INTER_LINEAR)
    w = cv2.resize(w2, (TH_W, TH_H), interpolation=cv2.INTER_AREA)
    wr, br = _thumb_refs(cal)
    if wr is not None:
        span = np.maximum(wr - br, 0.02)
        return np.clip((w - br) / span, 0, 1.2).astype(np.float32)
    lo, hi = np.percentile(w, 1), np.percentile(w, 99)
    return np.clip((w - lo) / max(hi - lo, 1e-3), 0, 1.2).astype(np.float32)


def diff_stats(a, b, thr=0.08):
    """(mean abs diff, fraction of pixels > thr) after removing global gain offset
    (camera auto-exposure moves the whole frame)."""
    d = a - b
    d = d - np.median(d)
    ad = np.abs(d)
    return float(ad.mean()), float((ad > thr).mean())


def activity_series(thumbs, thr=0.08):
    """Per frame: consecutive diff (mean, frac)."""
    out = [(0.0, 0.0)]
    for i in range(1, len(thumbs)):
        out.append(diff_stats(thumbs[i], thumbs[i - 1], thr))
    return out


def noise_floor(series, n_ref=None):
    """frac threshold: robust (median + 6*MAD, at least 0.002)."""
    fr = np.array([s[1] for s in series[1:n_ref]] or [0.0])
    med = np.median(fr); mad = np.median(np.abs(fr - med))
    return float(max(med + 6 * 1.4826 * mad, 0.002))


def segments(times, series, frac_thr, quiet_ms=400.0, min_frames=1):
    """Activity segments with hysteresis: [{start, end, peak_frac, mean_frac}]
    (times in seconds). A segment ends after quiet_ms with no frame over thr."""
    segs, cur, last_act = [], None, None
    for i in range(1, len(series)):
        act = series[i][1] > frac_thr
        t = times[i]
        if act:
            if cur is None:
                cur = {"start_i": i, "start": float(times[i - 1]), "fr": []}
            cur["fr"].append(series[i][1]); last_act = (i, t)
        elif cur is not None and (t - last_act[1]) * 1000 >= quiet_ms:
            segs.append(_close(cur, last_act, times)); cur = None
    if cur is not None:
        segs.append(_close(cur, last_act, times))
    return [s for s in segs if s["n_active_frames"] >= min_frames]


def _close(cur, last_act, times):
    return {"start": cur["start"], "end": float(last_act[1]),
            "duration_s": float(last_act[1] - cur["start"]),
            "n_active_frames": len(cur["fr"]), "peak_frac": float(max(cur["fr"])),
            "mean_frac": float(np.mean(cur["fr"]))}


def action_timing(times, thumbs, t_send, t_end, thr_frac=None, settle_ms=500.0,
                  consec=2, baseline_n=3):
    """Latency numbers for one input at host-monotonic t_send.

    first_change: first frame (after t_send) whose diff vs the pre-input
    baseline stays over threshold for `consec` frames. Reported time is the
    frame timestamp: true change happened somewhere in the preceding frame
    interval, so uncertainty is [-1 frame, 0] plus the unknown camera
    pipeline latency (bias, not measured here).
    settle: time of the last frame that still differs from the final picture
    (mean of the last 3 frames in the window); settled=False if fewer than
    settle_ms of unchanged frames follow it (the window ended too early).
    """
    times = np.asarray(times, float)
    pre = [i for i in range(len(times)) if times[i] < t_send]
    post = [i for i in range(len(times)) if t_send <= times[i] <= t_end]
    res = {"first_change_ms": None, "settle_ms": None, "settled": False,
           "frames_pre": len(pre), "frames_post": len(post), "peak_frac": 0.0}
    if len(pre) < 1 or len(post) < 2:
        res["error"] = "not enough frames around the action"
        return res
    base_idx = pre[-baseline_n:]
    base = np.mean([thumbs[i] for i in base_idx], axis=0)
    if thr_frac is None:
        ser = activity_series([thumbs[i] for i in pre[-30:]])
        thr_frac = noise_floor(ser)
    res["threshold_frac"] = thr_frac
    over = []
    for i in post:
        m, fr = diff_stats(thumbs[i], base)
        over.append(fr > thr_frac)
        res["peak_frac"] = max(res["peak_frac"], fr)
    for k in range(len(post) - consec + 1):
        if all(over[k:k + consec]):
            res["first_change_ms"] = float((times[post[k]] - t_send) * 1000)
            res["first_change_frame"] = int(post[k])
            break
    if res["first_change_ms"] is None:
        return res
    # settle: distance to the final picture. A slow E Ink transition changes
    # only a little between consecutive frames, so compare against the end
    # state instead: settle = the last frame that still differs from it.
    final = np.mean([thumbs[i] for i in post[-3:]], axis=0)
    last = None
    for i in post[:-3]:
        if diff_stats(thumbs[i], final)[1] > thr_frac:
            last = i
    if last is not None:
        quiet = (times[post[-1]] - times[last]) * 1000
        res["settle_ms"] = float((times[last] - t_send) * 1000)
        res["settle_frame"] = int(last)
        res["settled"] = bool(quiet >= settle_ms)
        res["quiet_after_ms"] = float(quiet)
    return res


# ------------------------------------------------------------ image quality


def _half(g):
    return cv2.resize(g, (MET_W, MET_H), interpolation=cv2.INTER_AREA)


def png_to_gray(path_or_bytes):
    if isinstance(path_or_bytes, (bytes, bytearray)):
        im = cv2.imdecode(np.frombuffer(path_or_bytes, np.uint8), cv2.IMREAD_COLOR)
    else:
        im = cv2.imread(path_or_bytes)
    if im is None:
        raise ValueError("bad image")
    g = to_gray_f(im)
    if g.shape != (PANEL_H, PANEL_W):
        g = cv2.resize(g, (PANEL_W, PANEL_H), interpolation=cv2.INTER_AREA)
    return g


def quality_metrics(C, S, P=None):
    """C: settled camera, normalised, panel space (or half). S: screencap grey
    (0..1). P: previous screencap, optional. All compared at metric space after
    registering C to S (phase correlation, shift reported).

    Returns dict of floats (None where undefined) + the half-res aligned arrays
    under key '_arrays' for rendering.
    """
    c = _half(C); s = _half(S)
    p = _half(P) if P is not None else None
    # S rendered through the camera's own resolution limit
    s_cam = cv2.GaussianBlur(s, (0, 0), 1.0)
    c_s = cv2.GaussianBlur(c, (0, 0), 1.0)
    (dx, dy), resp = cv2.phaseCorrelate(s_cam.astype(np.float64), c_s.astype(np.float64))
    M = np.float32([[1, 0, -dx], [0, 1, -dy]])
    if abs(dx) < 8 and abs(dy) < 8:
        c = cv2.warpAffine(c, M, (MET_W, MET_H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    out = {"align_shift_px_metric": [float(dx), float(dy)], "align_response": float(resp)}

    # --- contrast: levels over regions the screencap says are black / white
    sb = cv2.GaussianBlur(s, (0, 0), 1.5)
    black_m = cv2.erode((sb < 0.12).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    white_m = cv2.erode((sb > 0.88).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    bl = float(np.median(c[black_m])) if black_m.sum() > 200 else None
    wl = float(np.median(c[white_m])) if white_m.sum() > 200 else None
    out["black_level"], out["white_level"] = bl, wl
    out["black_frac"], out["white_frac"] = float(black_m.mean()), float(white_m.mean())
    out["contrast_michelson"] = float((wl - bl) / (wl + bl)) if (bl is not None and wl is not None and wl + bl > 1e-6) else None
    out["contrast_range"] = float(wl - bl) if (bl is not None and wl is not None) else None

    # --- sharpness: gradient energy of camera vs screencap on edge pixels
    def grad(x):
        gx = cv2.Sobel(x, cv2.CV_32F, 1, 0, ksize=3); gy = cv2.Sobel(x, cv2.CV_32F, 0, 1, ksize=3)
        return np.sqrt(gx * gx + gy * gy)
    gs, gc = grad(s_cam), grad(cv2.GaussianBlur(c, (0, 0), 1.0))
    edge = cv2.dilate((gs > 0.25).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    out["edge_pixels"] = int(edge.sum())
    out["edge_sharpness_ratio"] = float(gc[edge].mean() / gs[edge].mean()) if edge.sum() > 100 else None
    out["ncc_vs_screencap"] = ncc(s_cam, cv2.GaussianBlur(c, (0, 0), 1.0))
    out["rmse_vs_screencap"] = float(np.sqrt(np.mean((s_cam - cv2.GaussianBlur(c, (0, 0), 1.0)) ** 2)))

    # --- speckle: high-passed residual std in flat regions of the screencap
    loc = cv2.GaussianBlur(s * s, (0, 0), 4) - cv2.GaussianBlur(s, (0, 0), 4) ** 2
    flat = cv2.erode((loc < 1e-4).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    hp = c - cv2.GaussianBlur(c, (0, 0), 6)
    for nm, m in (("white", flat & (sb > 0.8)), ("black", flat & (sb < 0.2))):
        out["speckle_std_" + nm] = float(hp[m].std()) if m.sum() > 500 else None
    both = flat & ((sb > 0.8) | (sb < 0.2))
    out["speckle_std"] = float(hp[both].std()) if both.sum() > 500 else None

    # --- ghost: projection of (C-S) on (P-S) where the screens differ
    if p is not None:
        dsp = cv2.GaussianBlur(p - s, (0, 0), 2)
        ghost_m = cv2.erode((np.abs(dsp) > 0.5).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        out["ghost_region_frac"] = float(ghost_m.mean())
        if ghost_m.sum() > 200:
            cs = cv2.GaussianBlur(c - s, (0, 0), 2)[ghost_m]; ps = dsp[ghost_m]
            out["ghost_residual"] = float(np.clip((cs * ps).sum() / (ps * ps).sum(), -1, 2))
        else:
            out["ghost_residual"] = None
        keep_m = cv2.erode((np.abs(dsp) < 0.05).astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
        out["unchanged_region_rmse"] = float(np.sqrt(np.mean(cv2.GaussianBlur(c - s, (0, 0), 2)[keep_m] ** 2))) if keep_m.sum() > 500 else None
    else:
        out["ghost_residual"] = None
    out["_arrays"] = {"c": c, "s": s}
    return out


def settled_camera(cal, jpgs):
    """Mean of the given jpeg frames, warped + normalised, panel space."""
    gs = []
    for j in jpgs:
        img = cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR) if isinstance(j, (bytes, bytearray)) else j
        gs.append(cal.warp_gray(img))
    return cal.norm(np.mean(gs, axis=0).astype(np.float32))
