import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import synth  # noqa: E402
from synth import L  # noqa: E402

PROBE = os.path.join(HERE, "..", "..", "scripts", "eink-probe", "probe.py")


def make_calib(tmp):
    """Calibrate from synthetic white/black camera frames (different exposure)."""
    wd, bd = os.path.join(tmp, "w"), os.path.join(tmp, "b")
    os.makedirs(wd); os.makedirs(bd)
    for k in range(3):
        cv2.imwrite(os.path.join(wd, "%d.png" % k), synth.camera(synth.screen("white"), gain=1.0, seed=k))
        cv2.imwrite(os.path.join(bd, "%d.png" % k), synth.camera(synth.screen("black"), gain=0.9, seed=k + 9))
    cal = L.calibrate(wd, bd)
    p = os.path.join(tmp, "cal.json")
    cal.save(p)
    return cal, p


class Geometry(unittest.TestCase):
    def test_calibrate_synthetic(self):
        with tempfile.TemporaryDirectory() as t:
            cal, p = make_calib(t)
            err = np.abs(cal.quad - L.order_quad(synth.QUAD)).max()
            self.assertLess(err, 5.0, cal.quad)   # px of 1920; rounded by the 9 px open
            self.assertTrue(cal.info["aspect_ok"])
            cal2 = L.Calib.load(p)
            self.assertTrue(np.allclose(cal2.quad, cal.quad))
            self.assertIsNotNone(cal2.white)

    def test_warp_registers_checker(self):
        with tempfile.TemporaryDirectory() as t:
            cal, p = make_calib(t)
            pats = L.make_patterns(os.path.join(t, "pat"))
            chk = cv2.imread(os.path.join(t, "pat", "pattern-checker.png"))
            r = L.verify_registration(cal, synth.camera(chk), os.path.join(t, "pat", "pattern-checker.png"))
            self.assertGreater(r["ncc"], 0.8, r)
            self.assertLess(max(map(abs, r["shift_px_panel"])), 8, r)

    def test_manual_corners(self):
        with tempfile.TemporaryDirectory() as t:
            cal, _ = make_calib(t)
            wd = os.path.join(t, "w")
            c2 = L.calibrate(wd, os.path.join(t, "b"), corners=synth.QUAD[::-1])
            self.assertEqual(c2.info["method"], "manual")
            self.assertTrue(np.allclose(c2.quad, L.order_quad(synth.QUAD)))

    def test_real_fixture_pair(self):
        # Real (downscaled/cropped) frames: dark vs white setup screen.
        cal = L.calibrate(os.path.join(HERE, "fixtures", "cam-white.jpg"),
                          os.path.join(HERE, "fixtures", "cam-black.jpg"))
        self.assertTrue(cal.info["aspect_ok"], cal.info)
        q = cal.quad  # fixtures are half scale: screen ~ x 345-555, y 35-445
        self.assertTrue(330 < q[:, 0].min() and q[:, 0].max() < 570, q)
        self.assertTrue(25 < q[:, 1].min() and q[:, 1].max() < 460, q)
        self.assertGreater(cal.info["panel_contrast_michelson"], 0.3)

    def test_detect_failure_is_loud(self):
        a = np.full((200, 300, 3), 80, np.uint8)
        with self.assertRaises(ValueError):
            L.detect_quad_from_pair(a, a.copy())


class Mjpeg(unittest.TestCase):
    def frames(self):
        return [synth.jpeg(synth.camera(synth.screen("A"), seed=i)) for i in range(4)]

    def test_parser_chunked_with_headers(self):
        fr = self.frames()
        blob = b"".join(b"--b\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\nX-Timestamp: %d.5\r\n\r\n%s\r\n" %
                        (len(j), 100 + i, j) for i, j in enumerate(fr))
        p = L.MjpegParser(); out = []
        for i in range(0, len(blob), 777):
            out += p.feed(blob[i:i + 777])
        self.assertEqual([o[0] for o in out], fr)
        self.assertEqual([o[1] for o in out], [100.5, 101.5, 102.5, 103.5])

    def test_parser_no_length(self):
        fr = self.frames()
        blob = b"".join(b"--b\r\nContent-Type: image/jpeg\r\n\r\n" + j + b"\r\n" for j in fr)
        p = L.MjpegParser(); out = []
        for i in range(0, len(blob), 1000):
            out += p.feed(blob[i:i + 1000])
        self.assertEqual([o[0] for o in out], fr)

    def test_capture_fps(self):
        with tempfile.TemporaryDirectory() as t:
            sf = os.path.join(t, "state"); open(sf, "w").write("A")
            fr = {"A": self.frames()}
            with synth.CamServer(sf, fr, fps=25.0) as srv:
                out = os.path.join(t, "cap")
                r = subprocess.run([sys.executable, PROBE, "capture", "--url", srv.url, "--seconds", "2",
                                    "--out", out], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                st = json.load(open(os.path.join(out, "capture.json")))["stats"]
                self.assertGreater(st["frames"], 35)
                self.assertAlmostEqual(st["fps_cam"], 25.0, delta=2.0)
                self.assertAlmostEqual(st["resolution_floor_ms"], 40.0, delta=4.0)
                self.assertTrue(os.path.exists(os.path.join(out, "frames", "f00000.jpg")))


class Timing(unittest.TestCase):
    def test_action_timing_synthetic(self):
        rng = np.random.RandomState(1)
        fps = 30.0
        times = np.arange(0, 4.0, 1 / fps)
        base = rng.rand(206, 103).astype(np.float32)
        new = rng.rand(206, 103).astype(np.float32)
        thumbs = []
        for t in times:
            if t < 1.50:
                im = base
            elif t < 2.0:
                w = (t - 1.5) / 0.5
                im = base * (1 - w) + new * w     # E Ink style progressive change
            else:
                im = new
            thumbs.append(im + rng.normal(0, 0.004, im.shape).astype(np.float32))
        r = L.action_timing(times, thumbs, t_send=1.40, t_end=4.0, settle_ms=500)
        self.assertAlmostEqual(r["first_change_ms"], 100 + 2 * 33, delta=80)   # change starts at 1.5 s
        self.assertAlmostEqual(r["settle_ms"], 600, delta=100)               # ends at 2.0 s
        self.assertTrue(r["settled"])

    def test_no_change_reports_none(self):
        times = np.arange(0, 3.0, 1 / 30)
        thumbs = [np.full((206, 103), 0.5, np.float32) for _ in times]
        r = L.action_timing(times, thumbs, 1.0, 3.0)
        self.assertIsNone(r["first_change_ms"])

    def test_segments(self):
        times = np.arange(0, 6, 0.1)
        ser = [(0, 0.0)] * len(times)
        for i in list(range(10, 18)) + list(range(40, 50)):
            ser[i] = (0.1, 0.2)
        s = L.segments(times, ser, 0.01, quiet_ms=400)
        self.assertEqual(len(s), 2)
        self.assertAlmostEqual(s[0]["start"], 0.9, delta=0.11)


class Quality(unittest.TestCase):
    def setUp(self):
        self.S = L.to_gray_f(synth.screen("A"))
        self.P = L.to_gray_f(synth.screen("B"))

    def cam(self, **kw):
        S = synth.screen("A")
        with tempfile.TemporaryDirectory() as t:
            cal, _ = make_calib(t)
            img = synth.camera(S, **kw)
            return cal.norm(cal.warp_gray(img))

    def test_clean_vs_ghost(self):
        clean = L.quality_metrics(self.cam(), self.S, self.P)
        ghost = L.quality_metrics(self.cam(blend=(synth.screen("B"), 0.35)), self.S, self.P)
        self.assertLess(abs(clean["ghost_residual"]), 0.12, clean["ghost_residual"])
        self.assertAlmostEqual(ghost["ghost_residual"], 0.35, delta=0.12)
        self.assertGreater(clean["contrast_michelson"], 0.7)
        self.assertGreater(clean["ncc_vs_screencap"], 0.9)

    def test_speckle_detects_noise(self):
        quiet = L.quality_metrics(self.cam(noise=0.5), self.S)
        loud = L.quality_metrics(self.cam(noise=8.0), self.S)
        self.assertGreater(loud["speckle_std"], 2 * quiet["speckle_std"])

    def test_low_contrast_and_blur(self):
        flat = self.cam()
        low = 0.5 + 0.3 * (flat - 0.5)
        blur = cv2.GaussianBlur(flat, (0, 0), 3)
        ql, qb, qc = (L.quality_metrics(x, self.S) for x in (low, blur, flat))
        self.assertLess(ql["contrast_michelson"], qc["contrast_michelson"] - 0.1)
        self.assertLess(qb["edge_sharpness_ratio"], qc["edge_sharpness_ratio"] - 0.1)


class EndToEnd(unittest.TestCase):
    def test_act_with_fake_adb(self):
        fake = os.path.join(HERE, "fake-adb.sh")
        with tempfile.TemporaryDirectory() as t:
            cal, calp = make_calib(os.path.join(t, "c")) if os.makedirs(os.path.join(t, "c")) is None else None
            fd = os.path.join(t, "fake"); os.makedirs(os.path.join(fd, "screens"))
            for n in ("A", "B", "C", "mid"):
                cv2.imwrite(os.path.join(fd, "screens", n + ".png"), synth.screen(n))
            open(os.path.join(fd, "logical"), "w").write("A")
            sf = os.path.join(fd, "state"); open(sf, "w").write("A")
            frames = {n: [synth.jpeg(synth.camera(synth.screen(n), seed=i)) for i in range(5)]
                      for n in ("A", "B", "C", "mid")}
            sc = {"name": "fake", "min_wait_ms": 1200, "quiet_ms": 500, "max_wait_ms": 6000,
                  "steps": [{"name": "to_b", "adb": ["shell", "input", "tap", "1", "1", "SCREEN=B"]},
                            {"name": "to_c", "adb": ["shell", "input", "tap", "1", "1", "SCREEN=C"]}]}
            scp = os.path.join(t, "sc.json"); json.dump(sc, open(scp, "w"))
            env = dict(os.environ, FAKE_DIR=fd)
            with synth.CamServer(sf, frames, fps=30.0) as srv:
                out = os.path.join(t, "sess")
                r = subprocess.run([sys.executable, PROBE, "act", "--adb", "bash " + fake, "--scenario", scp,
                                    "--url", srv.url, "--calib", calp, "--out", out, "--preroll-ms", "800"],
                                   capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            rep = json.load(open(os.path.join(out, "report.json")))
            self.assertEqual(len(rep["actions"]), 2)
            for a in rep["actions"]:
                tm = a["timing"]
                self.assertIsNotNone(tm["first_change_ms"], a)
                self.assertTrue(250 < tm["first_change_ms"] < 520, tm)   # fake: change at 300 ms
                self.assertTrue(500 < tm["settle_ms"] < 900, tm)         # fake: final at 600 ms
                self.assertTrue(tm["settled"])
                self.assertEqual(a["wait_ended"], "quiet")
                self.assertGreater(a["quality"]["ncc_vs_screencap"], 0.85, a["quality"])
                self.assertTrue(os.path.exists(os.path.join(out, a["sheet"])))
            self.assertIn("| to_b |", open(os.path.join(out, "report.md")).read())
            # fake adb saw exactly: initial screencap, then input+screencap per step; nothing else
            calls = open(os.path.join(fd, "calls.log")).read().splitlines()
            self.assertEqual(len(calls), 5, calls)

    def test_watch_timeline(self):
        with tempfile.TemporaryDirectory() as t:
            sf = os.path.join(t, "state"); open(sf, "w").write("A")
            frames = {n: [synth.jpeg(synth.camera(synth.screen(n), seed=i)) for i in range(5)] for n in ("A", "B")}
            import threading
            def flip():
                time.sleep(1.5); open(sf, "w").write("B"); time.sleep(1.5); open(sf, "w").write("A")
            threading.Thread(target=flip, daemon=True).start()
            with synth.CamServer(sf, frames) as srv:
                out = os.path.join(t, "w")
                r = subprocess.run([sys.executable, PROBE, "watch", "--url", srv.url, "--seconds", "4.5",
                                    "--out", out], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            w = json.load(open(os.path.join(out, "watch.json")))
            self.assertEqual(len(w["segments"]), 2, w["segments"])
            # capture starts a little after the flip timer: check the spacing, not the origin
            self.assertAlmostEqual(w["segments"][1]["start"] - w["segments"][0]["start"], 1.5, delta=0.25)
            self.assertTrue(0.8 < w["segments"][0]["start"] < 1.7)
            self.assertTrue(os.path.exists(os.path.join(out, "activity.png")))


class SafetyStatic(unittest.TestCase):
    def test_no_phone_tools_hardcoded(self):
        """The library never spawns anything; probe.py reaches adb only through --adb."""
        import re
        base = os.path.join(HERE, "..", "..", "scripts", "eink-probe")
        lib = open(os.path.join(base, "probe_lib.py")).read()
        self.assertNotIn("subprocess", lib)
        for f in ("probe_lib.py", "probe.py", "eink-probe"):
            txt = open(os.path.join(base, f)).read()
            self.assertIsNone(re.search(r"\b(fastboot|edl|nc)\b", txt), f)
        self.assertIn('default="adb"', open(PROBE).read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
