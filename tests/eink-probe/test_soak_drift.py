"""Offline tests for scripts/eink-probe/soak_drift.py (review 89f soak drift and ghost check). Synthetic camera frames
made by warping panel images into a camera view with the inverse of a calibration homography. No phone, no camera."""
import json
import os
import sys
import tempfile
import unittest

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "scripts", "eink-probe"))
import soak_drift as SD  # noqa: E402

# camera 1280x960, the panel occupies a 412x824 rectangle (like the real calib: x 2.05, y 2.02)
QUAD = [[700, 70], [1112, 70], [1112, 894], [700, 894]]


def calib(d):
    H = cv2.getPerspectiveTransform(np.float32(QUAD), np.float32([[0, 0], [823, 0], [823, 1647], [0, 1647]]))
    p = os.path.join(d, "calib.json")
    json.dump({"version": 1, "panel": [824, 1648], "H": H.tolist()}, open(p, "w"))
    return p, H


def panel(white=0.85, noise=0.02, ghost=False, seed=0):
    rng = np.random.default_rng(seed)
    img = np.full((1648, 824), white, np.float32)
    img[230:300, 60:480] = 0.15                                   # the first row's text
    img[0:40, :] = 0.3                                            # status bar
    if ghost:                                                     # a wallpaper ghost over the blank right side of the rows
        img[900:1300, 570:650] -= 0.10
    img += rng.normal(0, noise, img.shape).astype(np.float32)
    return np.clip(img, 0, 1)


def to_cam(pimg, H):
    cam = cv2.warpPerspective((pimg * 255).astype(np.uint8), np.linalg.inv(H), (1280, 960), flags=cv2.INTER_LINEAR)
    return cv2.cvtColor(cam, cv2.COLOR_GRAY2BGR)


def make_slice(run, tag, H, white, noise, t0, uptime_start, ghost_after=None, seed=0):
    sl = os.path.join(run, "runs", tag)
    probe = os.path.join(sl, "probe")
    os.makedirs(os.path.join(probe, "frames"))
    idx = []
    for i in range(40):                       # 4 s of frames at 10 fps
        mono = t0 + 0.1 * i
        g = ghost_after is not None and ghost_after <= mono <= ghost_after + 1.0
        cv2.imwrite(os.path.join(probe, "frames", "f%05d.jpg" % i), to_cam(panel(white, noise, ghost=g, seed=seed + i), H),
                    [cv2.IMWRITE_JPEG_QUALITY, 95])
        idx.append({"i": i, "mono": mono})
    with open(os.path.join(probe, "frames", "index.jsonl"), "w") as f:
        for e in idx:
            f.write(json.dumps(e) + "\n")
    json.dump({"actions": [{"n": 1, "name": "home", "rc": 0, "t_cap": t0 + 1.0},
                           {"n": 2, "name": "settings_main", "rc": 0, "t_cap": t0 + 3.05}]}, open(os.path.join(probe, "session.json"), "w"))
    json.dump({"tag": tag, "uptime_start": uptime_start}, open(os.path.join(sl, "rail-r0.json"), "w"))


def make_opening(series, n, H, slices, bright="64", dips=""):
    run = os.path.join(series, "opening-%d" % n, "run")
    os.makedirs(run)
    json.dump({"brightness": {"b": bright, "m": "1"}}, open(os.path.join(run, "orig.json"), "w"))
    for k, (tag, white, noise, ghost_after) in enumerate(slices):
        make_slice(run, tag, H, white, noise, t0=1000.0 + 10 * k, uptime_start=100.0 + 10 * k, ghost_after=ghost_after, seed=10 * k)
    open(os.path.join(run, "dips-dmesg.txt"), "w").write(dips)
    return run


class Drift(unittest.TestCase):
    def run_case(self, last_white, last_noise, bright2="64", dips="", ghost_after=None):
        d = tempfile.mkdtemp()
        cp, H = calib(d)
        s = os.path.join(d, "series")
        make_opening(s, 1, H, [("01-C", 0.85, 0.02, None), ("02-W", 0.80, 0.02, None)], dips=dips)
        make_opening(s, 2, H, [("01-C", 0.85, 0.02, ghost_after), ("02-W", 0.85, 0.02, None), ("03-C", last_white, last_noise, None)],
                     bright=bright2)
        out = os.path.join(d, "out")
        rc = SD.main([s, "--calib", cp, "--out", out, "--final"])
        return rc, json.load(open(os.path.join(out, "drift.json"))), open(os.path.join(out, "drift.md")).read(), out

    def test_same_panel_passes_and_measures_the_regions(self):
        rc, r, md, _ = self.run_case(0.85, 0.02)
        self.assertEqual(rc, 0, md)
        self.assertEqual(r["drift"]["verdict"], "PASS")
        self.assertEqual((r["drift"]["first"], r["drift"]["last"]), ("opening-1/01-C", "opening-2/03-C"))
        w = r["openings"][0]["slices"][0]["ref"]["regions"]
        self.assertAlmostEqual(w["white"]["mean"], 0.85, delta=0.03)
        self.assertLess(w["text"]["mean"], 0.6)
        self.assertAlmostEqual(w["status"]["mean"], 0.3, delta=0.04)
        self.assertIn("**Drift: PASS**", md)

    def test_mean_shift_over_2_percent_fails(self):
        rc, r, md, _ = self.run_case(0.85 * 0.97, 0.02)
        self.assertEqual(rc, 1)
        self.assertEqual(r["drift"]["verdict"], "FAIL")
        self.assertLess(r["drift"]["mean_rel"], -0.02)

    def test_mean_shift_under_2_percent_passes(self):
        rc, r, _, _ = self.run_case(0.85 * 0.99, 0.02)
        self.assertEqual(rc, 0, r["drift"])

    def test_speckle_rise_over_20_percent_fails(self):
        rc, r, _, _ = self.run_case(0.85, 0.03)
        self.assertEqual(rc, 1)
        self.assertGreater(r["drift"]["std_rise"], 0.2)

    def test_different_brightness_is_not_comparable(self):
        rc, r, _, _ = self.run_case(0.85, 0.02, bright2="40")
        self.assertEqual(rc, 1)
        self.assertTrue(r["drift"]["verdict"].startswith("NOT COMPARABLE"))

    def test_dip_strip_and_ghost_flag(self):
        # a dip in opening 2's first slice: dmesg 101.5 s = mono 1001.5 (offset frame0.mono - uptime_start = 900)
        dips = ("[  101.500000] a9_eink eink-display: pg-settle: PG low after a waveform came back in 37000 us from the first low reading"
                " (recovered dip 1 of 3 this session, 1 of 1000 this boot; not a fault)\n")
        d = tempfile.mkdtemp()
        cp, H = calib(d)
        s = os.path.join(d, "series")
        make_opening(s, 1, H, [("01-C", 0.85, 0.02, 1001.5), ("02-W", 0.85, 0.02, None)], dips=dips)
        out = os.path.join(d, "out")
        SD.main([s, "--calib", cp, "--out", out])
        r = json.load(open(os.path.join(out, "drift.json")))
        dp = r["openings"][0]["dips"]
        self.assertEqual(len(dp), 1)
        self.assertEqual(r["openings"][0]["uptime_to_mono"], 900.0)
        self.assertEqual(dp[0]["slice"], "01-C")
        self.assertTrue(os.path.exists(os.path.join(out, dp[0]["strip"])))
        self.assertEqual(cv2.imread(os.path.join(out, dp[0]["strip"])).shape[:2], (412, 6 * 206))
        self.assertTrue(dp[0]["flag"], dp[0])
        self.assertIn("LOOK", open(os.path.join(out, "drift.md")).read())

    def test_dark_page_after_a_dip_is_not_flagged(self):
        """the off-pattern case of review 89d: a dip on the dark home screen, no Settings frame in the window"""
        dips = ("[  101.500000] a9_eink eink-display: pg-settle: PG low after a waveform came back in 37000 us from the first low reading"
                " (recovered dip 1 of 3 this session, 1 of 1000 this boot; not a fault)\n")
        d = tempfile.mkdtemp()
        cp, H = calib(d)
        s = os.path.join(d, "series")
        run = make_opening(s, 1, H, [("01-C", 0.85, 0.02, None)], dips=dips)
        fr = os.path.join(run, "runs", "01-C", "probe", "frames")
        for i in range(15, 26):                    # 1001.5 .. 1002.5: a dark page
            cv2.imwrite(os.path.join(fr, "f%05d.jpg" % i), to_cam(panel(0.2, 0.05, seed=i), H))
        out = os.path.join(d, "out")
        SD.main([s, "--calib", cp, "--out", out])
        dp = json.load(open(os.path.join(out, "drift.json")))["openings"][0]["dips"][0]
        self.assertFalse(dp["flag"])
        self.assertEqual(dp["white_std_ratio"], "not a Settings page")

    def test_no_settings_frame_is_no_data(self):
        d = tempfile.mkdtemp()
        cp, _ = calib(d)
        s = os.path.join(d, "series")
        os.makedirs(os.path.join(s, "opening-1", "run", "runs"))
        self.assertEqual(SD.main([s, "--calib", cp, "--out", os.path.join(d, "o"), "--final"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
