"""Offline tests for the ghost A/B validation: ghost_ab_analyze.py (synthetic camera frames), ghost_ab.py (the R0 fake
phone + fake camera; a fake clean-check helper) and ghost_stock.py (a fake stock phone). No phone, no webcam: the PATH
tripwire fails a test if a real adb is ever invoked."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "..", "scripts", "eink-probe"))
import ghost_ab_analyze as GA  # noqa: E402
from test_session import FAKE, PROBE, SESS, rd  # noqa: E402
import test_rail_r0 as TR  # noqa: E402

GHOST = os.path.join(SESS, "ghost_ab.py")
STOCK = os.path.join(SESS, "ghost_stock.py")
QUAD = [[700, 70], [1112, 70], [1112, 894], [700, 894]]


def calib(d):
    H = cv2.getPerspectiveTransform(np.float32(QUAD), np.float32([[0, 0], [823, 0], [823, 1647], [0, 1647]]))
    p = os.path.join(d, "calib.json")
    json.dump({"version": 1, "panel": [824, 1648], "quad_tl_tr_br_bl": QUAD, "H": H.tolist()}, open(p, "w"))
    return p, H


def settings_panel(ghost=0.0, seed=0):
    rng = np.random.default_rng(seed)
    img = np.full((1648, 824), 0.85, np.float32)
    for y in range(230, 1560, 130):                 # list rows: text on the left
        img[y:y + 60, 60:480] = 0.2
    img[0:40, :] = 0.3
    if ghost:                                       # a launcher-icon ghost in the blank lower right
        img[1350:1450, 600:700] -= ghost
    img += rng.normal(0, 0.01, img.shape).astype(np.float32)
    return np.clip(img, 0, 1)


def to_cam(pimg, H, exposure=1.0):
    cam = np.full((960, 1280), 60.0, np.float32)    # the off-glass background
    w = cv2.warpPerspective((pimg * 255).astype(np.float32), np.linalg.inv(H), (1280, 960), flags=cv2.INTER_LINEAR)
    m = cv2.warpPerspective(np.ones((1648, 824), np.float32), np.linalg.inv(H), (1280, 960)) > 0.5
    cam[m] = w[m]
    return cv2.cvtColor(np.clip(cam * exposure, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)


def probe_dir(path, H, panel_fn, n=10, exposure=1.0):
    os.makedirs(os.path.join(path, "frames"))
    with open(os.path.join(path, "frames", "index.jsonl"), "w") as f:
        for i in range(n):
            cv2.imwrite(os.path.join(path, "frames", "f%05d.jpg" % i), to_cam(panel_fn(i), H, exposure), [cv2.IMWRITE_JPEG_QUALITY, 95])
            f.write(json.dumps({"i": i, "mono": 100.0 + 0.1 * i}) + "\n")


class Analyze(unittest.TestCase):
    def session(self, d, H, reps, label="ghost-reps.json"):
        out = os.path.join(d, label.split("-")[0])
        for r in reps:
            k = r["rep"]
            probe_dir(os.path.join(out, "runs", "%02d-B" % k, "probe"), H, lambda i, g=r.pop("_ghost"): settings_panel(g, i), exposure=r.pop("_expB", 1.0))
            probe_dir(os.path.join(out, "runs", "%02d-C" % k, "probe"), H, lambda i: settings_panel(0.0, 100 + i), exposure=r.pop("_expC", 1.0))
        json.dump({"reps": reps}, open(os.path.join(out, label), "w"))
        return out

    def test_scores_dip_worsening_and_exposure_normalisation(self):
        d = tempfile.mkdtemp()
        cp, H = calib(d)
        reps = [{"rep": k, "arm": 100 if k % 2 else 0, "clean": True, "dip": k in (5, 6), "settled": k == 5,
                 "_ghost": 0.25 if k in (5, 6) else 0.02, "_expB": 1.0 + 0.07 * (k % 3), "_expC": 1.0} for k in range(1, 9)]
        ml = self.session(d, H, reps)
        st = self.session(d, H, [{"rep": k, "arm": "stock", "clean": True, "_ghost": 0.02} for k in range(1, 5)], "stock-reps.json")
        out = os.path.join(d, "an")
        self.assertEqual(GA.main(["analyze", ml, "--calib", cp, "--stock", st, "--out", out]), 0)
        r = json.load(open(os.path.join(out, "ghost.json")))
        g = r["groups"]
        self.assertEqual((g["mainline_dip"]["n"], g["mainline_nodip"]["n"], g["stock"]["n"]), (2, 6, 4))
        self.assertGreater(g["mainline_dip"]["median"], 3 * g["mainline_nodip"]["median"])
        self.assertEqual(r["verdict"]["P2"]["result"], "dip-related worsening")          # worsening is decidable with 2 dips
        self.assertEqual(r["verdict"]["P2_Groi_secondary"]["result"], "dip-related worsening")
        self.assertTrue(r["verdict"]["decision"].startswith("no default; the dip mechanism needs work"))
        p1 = r["verdict"]["P1"]                                           # review 89i C3: P1 is descriptive, no verdict
        self.assertEqual((p1["result"], p1["label"]), ("descriptive", "stock, own launcher and policy"))
        self.assertNotIn("stock-like", json.dumps(r["verdict"]))
        # exposure 1.0-1.14 in B normalised away (unsaturated): stock and mainline no-dip medians agree
        self.assertAlmostEqual(p1["stock_Gd"]["median"], p1["mainline_nodip_Gd"]["median"], delta=0.003)
        md = open(os.path.join(out, "ghost.md")).read()
        self.assertIn("Pre-registered (review 89i, C3), verbatim:", md)
        self.assertIn("- **P2 (dip vs no-dip on mainline): agreed, as the deciding comparison.**", md)
        self.assertIn("  - \"Inconclusive\": pool one more session.", md)
        rois = [x["Groi"] for x in r["reps"] if x["src"] == "mainline" and x["dip"]]
        clean = [x["Groi"] for x in r["reps"] if x["src"] == "mainline" and not x["dip"]]
        self.assertGreater(min(rois), 5 * max(clean), (rois, clean))
        self.assertTrue(os.path.exists(os.path.join(out, "ghost-sheets", "mainline-rep05.png")))

    def run_p2(self, dips, extra=None):
        d = tempfile.mkdtemp()
        cp, H = calib(d)
        reps = [{"rep": k, "arm": 100, "clean": True, "dip": k <= dips, "_ghost": 0.02} for k in range(1, 9)]
        for k, v in (extra or {}).items():
            reps[k - 1].update(v)
        ml = self.session(d, H, reps)
        out = os.path.join(d, "an")
        GA.main(["analyze", ml, "--calib", cp, "--out", out])
        return json.load(open(os.path.join(out, "ghost.json")))

    def test_p2_inconclusive_with_two_dips_and_no_worsening_with_three(self):
        r = self.run_p2(2)
        self.assertEqual(r["verdict"]["P2"]["result"], "inconclusive")
        self.assertIn("exactly 2", r["verdict"]["P2"]["why"])
        self.assertTrue(r["verdict"]["decision"].startswith("pool one more session"))
        r = self.run_p2(3)
        self.assertEqual(r["verdict"]["P2"]["result"], "no dip-related worsening shown")
        self.assertTrue(r["verdict"]["decision"].startswith("the soak STOP's ghost is ordinary mainline Smooth history"))

    def test_p2_excludes_reps_with_a_low_in_phase_a_or_c(self):
        # rep 1 (a dip) has a low in A, rep 2 (a dip) one in C: both out of both groups, so only one dip is left
        r = self.run_p2(3, {1: {"A_lows": 1}, 2: {"C_lows": 1}})
        self.assertEqual(r["verdict"]["excluded_low_in_A_or_C"], [1, 2])
        self.assertEqual(r["groups"]["mainline_dip"]["n"], 1)
        self.assertEqual(r["verdict"]["P2"]["result"], "inconclusive")

    def test_no_worsening_and_unclean_reps_excluded(self):
        d = tempfile.mkdtemp()
        cp, H = calib(d)
        reps = [{"rep": k, "arm": 100, "clean": k != 3, "dip": k in (1, 2), "_ghost": 0.02} for k in range(1, 7)]
        ml = self.session(d, H, reps)
        out = os.path.join(d, "an")
        GA.main(["analyze", ml, "--calib", cp, "--out", out])
        r = json.load(open(os.path.join(out, "ghost.json")))
        self.assertEqual(r["verdict"]["P2"]["result"], "inconclusive")       # 2 dips, no worsening: needs 3 (89i C3)
        self.assertIsNone([x for x in r["reps"] if x["rep"] == 3][0].get("Gd"))
        self.assertEqual(r["verdict"]["P1"]["stock_Gd"]["n"], 0)
        self.assertEqual(r["verdict"]["P1"]["result"], "stock reference pending")        # analysed without --stock
        self.assertIn("P1: stock reference pending", open(os.path.join(d, "an", "ghost.md")).read())

    def test_check_clean(self):
        d = tempfile.mkdtemp()
        cp, H = calib(d)
        home = lambda g: (lambda i: np.clip(np.full((1648, 824), 0.3, np.float32) + g + np.random.default_rng(i).normal(0, 0.01, (1648, 824)).astype(np.float32), 0, 1))  # noqa: E731
        probe_dir(os.path.join(d, "ref"), H, home(0.0))
        probe_dir(os.path.join(d, "ok"), H, home(0.0), exposure=1.15)       # brighter camera, same glass
        probe_dir(os.path.join(d, "bad"), H, home(0.1))
        outs = []
        for p in ("ok", "bad"):
            r = subprocess.run([sys.executable, os.path.join(HERE, "..", "..", "scripts", "eink-probe", "ghost_ab_analyze.py"), "check-clean",
                                "--calib", cp, "--ref", os.path.join(d, "ref"), "--probe", os.path.join(d, p)], capture_output=True, text=True)
            outs.append(json.loads(r.stdout.strip().splitlines()[-1]))
        self.assertTrue(outs[0]["clean"], outs[0])
        self.assertFalse(outs[1]["clean"], outs[1])


class GhostSession(unittest.TestCase):
    def test_two_reps_on_the_fake_phone(self):
        """force_clear events written, pg_settle_ms follows the ABBA arms and is reverted, log_updates reverted, brightness
        restored, reps recorded with their phase diag deltas; the clean check goes through the helper."""
        with TR.REnv() as e:
            ph = os.path.join(e.fd, "phone")
            open(os.path.join(ph, "sys/module/a9_eink/parameters/pg_settle_ms"), "w").write("0\n")
            open(os.path.join(ph, "sys/module/a9_eink/parameters/log_updates"), "w").write("N\n")
            fc = os.path.join(ph, "sys/class/drm/card3-DSI-1/eink/force_clear")
            open(fc, "w").write("")
            helper = os.path.join(e.t.name, "helper.sh")
            open(helper, "w").write('#!/bin/sh\necho "$*" >> "%s/helper.log"\necho \'{"score": 0.001, "clean": true}\'\n' % e.t.name)
            os.chmod(helper, 0o755)
            seen = []
            with open(os.path.join(ph, "dmesg.txt"), "a") as f:
                f.write("\n[  701.000000] a9_eink eink-display: update done: mode 0x203 pre 0x206 clear 1 steps 3 bin 9 init 0 rect 0,0-1648,824 sent 40 late_drive +0\n")

            def bumper(stop):
                e.bump(stop, extra=lambda n, full: (seen.append(rd(e.fd, "sys/module/a9_eink/parameters/pg_settle_ms")) or dict(m4=True, f6=True)))
            stop = threading.Event()
            th = threading.Thread(target=bumper, args=(stop,), daemon=True)
            th.start()
            try:
                args = [sys.executable, GHOST, "--dry-run", "--adb", "bash " + FAKE, "--out", e.out, "--calib", e.calp, "--url", e.cam.url,
                        "--probe", "%s %s" % (sys.executable, PROBE), "--preroll-ms", "300", "--poll-s", "0.3", "--max-frames-stale-s", "12",
                        "--ghost-reps", "2", "--post-s", "0.5", "--ghost-helper", helper, "--log-updates", "--rep-est-s", "5"]
                r = subprocess.run(args, capture_output=True, text=True, env=e.env, timeout=900)
            finally:
                stop.set(); th.join()
            self.assertEqual(r.returncode, 0, r.stdout[-4000:] + r.stderr[-2000:])
            reps = json.load(open(os.path.join(e.out, "ghost-reps.json")))["reps"]
            self.assertEqual([x["arm"] for x in reps], [100, 0])
            self.assertTrue(all(x["clean"] for x in reps), reps)
            self.assertIn("0", seen)
            self.assertIn("100", seen)
            self.assertEqual(rd(e.fd, "sys/module/a9_eink/parameters/pg_settle_ms"), "0")     # reverted
            self.assertIn(rd(e.fd, "sys/module/a9_eink/parameters/log_updates"), ("0", "N"))
            self.assertEqual(rd(e.fd, "settings-screen_brightness_mode"), "1")              # restored (was 1)
            self.assertEqual(open(fc).read().strip(), "1")                                  # force_clear written
            for k in (1, 2):
                for p in ("A", "B", "C"):
                    self.assertTrue(os.path.exists(os.path.join(e.out, "runs", "%02d-%s" % (k, p), "phase.json")), (k, p))
            self.assertTrue(reps[0]["A"]["kernel_clear"])
            # review 89i C1: phase B is a warm start (no -S); exactly one primed -S, before the baseline
            for k in (1, 2):
                sc = json.load(open(os.path.join(e.out, "runs", "%02d-B" % k, "scenario.json")))
                argv = sc["steps"][0]["adb"]
                self.assertEqual(argv, ["shell", "am", "start", "-a", "android.settings.SETTINGS"], argv)
            self.assertTrue(all(x["settings_start"] == "warm" for x in reps))
            plog = open(os.path.join(e.fd, "phone.log")).read()
            self.assertEqual(plog.count("am start -S"), 1, plog[-2000:])
            slog = open(os.path.join(e.out, "session.log")).read()
            self.assertLess(slog.index("primed Settings once"), slog.index("baseline: home cleared twice"))
            self.assertIn("## ghost A/B repetitions", open(os.path.join(e.out, "summary.md")).read())
            self.assertGreaterEqual(open(os.path.join(e.t.name, "helper.log")).read().count("check-clean"), 2)
            self.assertTrue(e.tripwire_clean())


def stock_dumpsys(e, keyguard=False, awake=True, swipe=False):
    """fake stock phone: dumpsys power / window for the awake/keyguard checks (review 89i C2); `wm dismiss-keyguard`
    clears a swipe-only keyguard (swipe=True) and leaves a secure one"""
    # Stock.finish() sends HOME just before teardown. Wait for the fake glass writer
    # so it cannot recreate files while TemporaryDirectory removes the fake phone.
    link = os.path.join(e.t.name, "fake-adb-link.sh")
    body = open(link).read()
    os.unlink(link)
    open(link, "w").write(body.replace("    exit 0;;", "    wait; exit 0;;"))
    open(os.path.join(e.fd, "phone", "kg"), "w").write("isKeyguardShowing=%s\n" % ("true" if keyguard else "false"))
    w = os.path.join(e.fd, "bin", "wm")
    open(w, "w").write('#!/usr/bin/env bash\necho "wm $*" >> "$FAKE_DIR/wm.log"\n%s\n' % (
        'echo isKeyguardShowing=false > "$FAKE_DIR/phone/kg"' if swipe else ':'))
    os.chmod(w, 0o755)
    if swipe:
        # Re-lock on every wake, so the repetition must dismiss again after preflight.
        inp = os.path.join(e.fd, "bin", "input")
        body = open(inp).read()
        open(inp, "w").write(body.replace("#!/usr/bin/env bash\n", '#!/usr/bin/env bash\ncase "$*" in *KEYCODE_WAKEUP*) echo isKeyguardShowing=true > "$FAKE_DIR/phone/kg";; esac\n'))
    b = os.path.join(e.fd, "bin", "dumpsys")
    open(b, "w").write('#!/usr/bin/env bash\ncase "$1" in power) echo "mWakefulness=%s";; window) cat "$FAKE_DIR/phone/kg";; '
                       'gfxinfo) cat "$FAKE_DIR/phone/gfx.txt";; esac\n' % ("Awake" if awake else "Asleep"))
    os.chmod(b, 0o755)


def stock_phone(e):
    ph = os.path.join(e.fd, "phone")
    fc = os.path.join(ph, "epd_force_clear")
    th = os.path.join(ph, "epd_thermal")
    open(fc, "w").write("")
    open(th, "w").write("27\n")
    for k, v in (("ro.boot.slot_suffix", "_a"), ("sys.boot_completed", "1"), ("sys.linevibrator_short", "300")):
        open(os.path.join(ph, "props", k), "w").write(v + "\n")
    b = os.path.join(e.fd, "bin")
    open(os.path.join(b, "su"), "w").write('#!/usr/bin/env bash\n[ "$1" = -c ] && shift\nif [ "$*" = id ]; then echo "uid=0(root)"; else sh -c "$*"; fi\n')
    os.chmod(os.path.join(b, "su"), 0o755)
    return fc, th


class StockSession(unittest.TestCase):
    def test_swipe_keyguard_is_dismissed_and_the_run_proceeds(self):
        with TR.REnv() as e:
            fc, th = stock_phone(e)
            stock_dumpsys(e, keyguard=True, swipe=True)
            ph = os.path.join(e.fd, "phone")
            before = {f: open(os.path.join(ph, f)).read() for f in os.listdir(ph) if f.startswith("settings-")}
            helper = os.path.join(e.t.name, "helper.sh")
            open(helper, "w").write("#!/bin/sh\necho '{\"score\": 0.001, \"clean\": true}'\n")
            os.chmod(helper, 0o755)
            r = subprocess.run([sys.executable, STOCK, "--recovery-marker", os.path.join(e.out, "pending.json"), "--dry-run", "--adb", "bash " + FAKE, "--out", e.out, "--calib", e.calp,
                                "--url", e.cam.url, "--probe", "%s %s" % (sys.executable, PROBE), "--ghost-helper", helper, "--ghost-reps", "1",
                                "--post-s", "0.5", "--fc-path", fc, "--therm-path", th, "--preroll-ms", "300", "--rep-est-s", "5"],
                               capture_output=True, text=True, env=e.env, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout[-2000:])
            self.assertEqual(open(os.path.join(e.fd, "wm.log")).read().splitlines(), ["wm dismiss-keyguard"] * 2)
            plog = open(os.path.join(e.fd, "phone.log")).read().splitlines()
            wakes = [i for i, line in enumerate(plog) if "KEYCODE_WAKEUP" in line]
            self.assertEqual(len(wakes), 2)  # preflight and repetition
            for i in wakes:
                self.assertLess(plog[i].index("KEYCODE_WAKEUP"), plog[i].index("wm dismiss-keyguard"))
                self.assertIn("dumpsys power", plog[i + 1])
                self.assertIn("dumpsys window", plog[i + 1])
            self.assertEqual(before, {f: open(os.path.join(ph, f)).read() for f in os.listdir(ph) if f.startswith("settings-")})
            self.assertTrue(e.tripwire_clean())
            self.assertEqual(len(json.load(open(os.path.join(e.out, "stock-reps.json")))["reps"]), 1)

    def test_keyguard_stops_with_rc3_and_writes_nothing(self):
        """a secure keyguard (dismiss-keyguard does not clear it) or a screen that stays asleep: rc 3, nothing written"""
        for kg, awake in ((True, True), (False, False)):
            with self.subTest(keyguard=kg, awake=awake), TR.REnv() as e:
                fc, th = stock_phone(e)
                stock_dumpsys(e, keyguard=kg, awake=awake)
                ph = os.path.join(e.fd, "phone")
                before = {f: open(os.path.join(ph, f)).read() for f in os.listdir(ph) if f.startswith("settings-")}
                r = subprocess.run([sys.executable, STOCK, "--recovery-marker", os.path.join(e.out, "pending.json"), "--dry-run", "--adb", "bash " + FAKE, "--out", e.out, "--calib", e.calp,
                                    "--url", e.cam.url, "--probe", "%s %s" % (sys.executable, PROBE), "--ghost-reps", "2",
                                    "--fc-path", fc, "--therm-path", th], capture_output=True, text=True, env=e.env, timeout=300)
                self.assertEqual(r.returncode, 3, r.stdout[-2000:])
                self.assertIn("unlock the phone", r.stdout)
                self.assertEqual(open(fc).read(), "")                     # no clear written
                self.assertFalse(os.path.exists(os.path.join(e.out, "runs", "00-A")))
                self.assertEqual(before, {f: open(os.path.join(ph, f)).read() for f in os.listdir(ph) if f.startswith("settings-")})
                self.assertTrue(e.tripwire_clean())

    def test_stock_reps_read_only_apart_from_the_clear(self):
        with TR.REnv() as e:
            ph = os.path.join(e.fd, "phone")
            fc, th = stock_phone(e)
            helper = os.path.join(e.t.name, "helper.sh")
            open(helper, "w").write("#!/bin/sh\necho '{\"score\": 0.001, \"clean\": true}'\n")
            os.chmod(helper, 0o755)
            stock_dumpsys(e)
            snap = os.path.join(e.t.name, "snap.sh")
            open(snap, "w").write('#!/bin/sh\necho "$1" >> "%s/snap.log"\n: > "$1"\n' % e.t.name)
            os.chmod(snap, 0o755)
            before = {f: open(os.path.join(ph, f)).read() for f in os.listdir(ph) if f.startswith("settings-")}
            r = subprocess.run([sys.executable, STOCK, "--recovery-marker", os.path.join(e.out, "pending.json"), "--epd-snap", snap, "--dry-run", "--adb", "bash " + FAKE, "--out", e.out, "--calib", e.calp, "--url", e.cam.url,
                                "--probe", "%s %s" % (sys.executable, PROBE), "--ghost-helper", helper, "--ghost-reps", "2", "--post-s", "0.5",
                                "--fc-path", fc, "--therm-path", th, "--preroll-ms", "300", "--rep-est-s", "5"],
                               capture_output=True, text=True, env=e.env, timeout=600)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1500:])
            reps = json.load(open(os.path.join(e.out, "stock-reps.json")))["reps"]
            self.assertEqual(len(reps), 2)
            self.assertTrue(all(x["clean"] and not x["frontlight_changed"] for x in reps), reps)
            self.assertEqual(reps[0]["thermal"], ["27", "27"])
            self.assertEqual(open(fc).read().strip(), "1")
            after = {f: open(os.path.join(ph, f)).read() for f in os.listdir(ph) if f.startswith("settings-")}
            self.assertEqual(before, after)                          # no Settings write
            snaps = open(os.path.join(e.t.name, "snap.log")).read().split()
            self.assertTrue(snaps[0].endswith("epd-snap-discovery.png"), snaps)   # discovery before the baseline
            self.assertEqual(len(snaps), 3)                          # discovery + one per repetition
            self.assertTrue(all(x["epd_snap"]["png"] for x in reps), reps)
            # review 89i C1/C2: warm B, one primed -S; a wakeup before every repetition
            for k in (1, 2):
                argv = json.load(open(os.path.join(e.out, "runs", "%02d-B" % k, "scenario.json")))["steps"][0]["adb"]
                self.assertNotIn("-S", argv)
            plog = open(os.path.join(e.fd, "phone.log")).read()
            self.assertEqual(plog.count("am start -S"), 1)
            self.assertEqual(plog.count("KEYCODE_WAKEUP"), 3)                  # preflight + one per repetition
            self.assertEqual(plog.count("dismiss-keyguard"), 3)
            self.assertTrue(e.tripwire_clean())
        # refusals
        r = subprocess.run([sys.executable, STOCK, "--adb", "adb", "--out", "/tmp/x", "--calib", "/dev/null"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
