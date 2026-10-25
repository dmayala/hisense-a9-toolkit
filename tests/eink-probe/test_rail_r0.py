"""Offline tests for scripts/eink-probe/sessions/trial3b_rail_r0.py (R0 rail-fault protocol).
Fake phone + fake camera, like test_session.py. No adb, no phone, no webcam: the PATH tripwire fails the
test if a real adb is ever invoked."""
import json
import os
import re
import subprocess
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_session import Env, FAKE, PROBE, SESS, rd  # noqa: E402

sys.path.insert(0, SESS)
import trial3b_rail_r0 as R  # noqa: E402

R0 = os.path.join(SESS, "trial3b_rail_r0.py")
BASE_STATUS = "updates 85 aborts 0 frames 845 commit_fail 0 stall_fault 0\nrail_fault 0 rail_faults 0 commit_max_us 310251 commit_slow 4 underruns 0 late_drive 0"


def status(faults=0):
    return BASE_STATUS.replace("rail_fault 0 rail_faults 0", "rail_fault %d rail_faults %d" % (1 if faults else 0, faults)) + "\n"


def diag_text(n=0, full=0, low=0, settle=0, m4=False, hist=None, max_us=0, skip=0, cap_skip=0, reread=0, total_max=30,
              f6=False, vmis=0, verr=0, vreads=0, rskip=0):
    """the counters, as the rail_diag attribute prints them (before the ring). m4=True: the M4 module's appended fields."""
    h = hist or [settle, 0, 0, 0, 0]
    return ("diag_hist_n 4 3 2 1 0 diag_hist_low 0 0 0 0 0 (since_wf_ms buckets 0-3 4-15 16-63 64+ none)\n"
            "diag_pg_end n %d low %d err 0 skipped 1 last_pg 1 last_at_us 123 full_n %d full_low 0 first_low_ms -1 first_low_session 0 "
            "first_low_n 0 first_low_total_us 0 last_low_ms -1 diag_pd_total 2 diag_pd_missed 0 diag_pd_skipped_fault 0\n" % (n, low, full)
            + "diag_settle win_ms 0 budget 3 n %d ok %d timeout 0 skip %d ok_hist(<2<5<10<25>=25ms) %d %d %d %d %d max_us %d" % (
                settle, settle, skip, h[0], h[1], h[2], h[3], h[4], max_us)
            + (" total_max %d cap_skip %d reread_high %d clock first_low" % (total_max, cap_skip, reread) if m4 else "") + "\n"
            + ("diag_rate n 15 window_s 600 burst 6 skip %d\ndiag_vcom reads %d ok %d mismatch %d err %d last %d last_ms %d\n" % (
                rskip, vreads, max(vreads - vmis - verr, 0), vmis, verr, 0x74 if vmis else (0x7e if vreads else -1), 1000 if vreads else -1)
               if f6 else ""))


class StopArgs:
    s1_dip_ms, f2_offset_ms, s2_long_dips, s4_boot_dips, s5_low_rate, s5_min_waveforms = 60.0, 15.0, 6, 25, 0.03, 300


RING = ("pd_total 2 pd_missed 0 pd_skipped_fault 0 pd_seen_seq 2 (entries: ...)\n"
        "pd 0 seq 2 session 5 pg 1,1,0 dt_us 0,1100,51100 entry_at_us 1000\n"
        "pd 1 seq 1 session 4 pg 1,1,1 dt_us 0,1000,51000 entry_at_us 900\n")


class Pure(unittest.TestCase):
    def test_parse_diag(self):
        d = R.parse_diag(status() + diag_text(n=7, full=3, low=1))
        self.assertTrue(d["present"])
        self.assertEqual((d["end"]["n"], d["end"]["full_n"], d["end"]["low"], d["end"]["diag_pd_total"]), (7, 3, 1, 2))
        self.assertEqual(d["hist_n"], [4, 3, 2, 1, 0])
        self.assertEqual((d["rail_faults"], d["rail_fault"], d["updates"]), (0, 0, 85))
        old = R.parse_diag(status())
        self.assertFalse(old["present"])
        self.assertIsNone(old["hist_n"])

    def test_parse_settle(self):
        d = R.parse_diag(status() + diag_text(n=7, full=3, settle=2))
        self.assertEqual(d["settle"], {"win_ms": 0, "budget": 3, "n": 2, "ok": 2, "timeout": 0, "skip": 0,
                                       "hist": [2, 0, 0, 0, 0], "max_us": 0})
        self.assertIsNone(R.parse_diag(status() + diag_text().split("diag_settle")[0])["settle"])

    def test_parse_settle_m4_fields(self):
        d = R.parse_diag(status() + diag_text(settle=2, m4=True, reread=4, cap_skip=1, total_max=30))
        self.assertEqual((d["settle"]["total_max"], d["settle"]["cap_skip"], d["settle"]["reread_high"], d["settle"]["first_clock"]),
                         (30, 1, 4, True))
        self.assertNotIn("first_clock", R.parse_diag(status() + diag_text(settle=2))["settle"])

    def test_settle_stops_s1_to_s5(self):
        """review 89 section 7: S1 long dip, S2 count of >= 25 ms dips, S3 budget/cap skip, S4 dips per boot, S5 low rate;
        both the M4 clock (durations from the first low reading) and an old module (F2-corrected limits)."""
        a = StopArgs()
        P = lambda **k: R.parse_diag(status() + diag_text(**k))
        base = P(n=100, settle=2, m4=True, hist=[0, 0, 0, 2, 0], max_us=20000)
        self.assertIsNone(R.settle_stops(base, P(n=150, settle=3, m4=True, hist=[0, 0, 0, 2, 1], max_us=59999), a))
        self.assertRegex(R.settle_stops(base, P(n=150, settle=3, m4=True, hist=[0, 0, 0, 2, 1], max_us=60000), a), r"^S1: .*60000")
        # old module: limit 45 ms reported
        old0 = P(n=100, settle=1, hist=[0, 0, 0, 1, 0], max_us=20000)
        self.assertIsNone(R.settle_stops(old0, P(n=110, settle=2, hist=[0, 0, 0, 1, 1], max_us=44999), a))
        self.assertRegex(R.settle_stops(old0, P(n=110, settle=2, hist=[0, 0, 0, 1, 1], max_us=45000), a), r"^S1: .*old module")
        # a max that was already above the limit before this session does not stop by itself
        hi0 = P(n=100, settle=1, m4=True, hist=[0, 0, 0, 0, 1], max_us=70000)
        self.assertIsNone(R.settle_stops(hi0, P(n=120, settle=2, m4=True, hist=[0, 0, 1, 0, 1], max_us=70000), a))
        # S2: 6 dips >= 25 ms this session (M4: bucket 4; old module: buckets 3+4)
        self.assertIsNone(R.settle_stops(base, P(n=200, settle=7, m4=True, hist=[0, 0, 0, 2, 5], max_us=50000), a))
        self.assertRegex(R.settle_stops(base, P(n=200, settle=8, m4=True, hist=[0, 0, 0, 2, 6], max_us=50000), a), r"^S2: 6 ")
        self.assertRegex(R.settle_stops(old0, P(n=200, settle=7, hist=[0, 0, 0, 4, 3], max_us=30000), a), r"^S2: 6 .*approximated")
        # S3: budget skip, or the per-boot cap used up
        self.assertRegex(R.settle_stops(base, P(n=150, settle=2, m4=True, hist=[0, 0, 0, 2, 0], max_us=20000, skip=1), a), r"^S3: 1 budget")
        self.assertRegex(R.settle_stops(base, P(n=150, settle=2, m4=True, hist=[0, 0, 0, 2, 0], max_us=20000, cap_skip=1), a), r"^S3: .*cap")
        # S4: 25 recovered dips in the boot (absolute, includes earlier sessions)
        self.assertIsNone(R.settle_stops(base, P(n=150, settle=24, m4=True, hist=[22, 0, 0, 2, 0], max_us=20000), a))
        self.assertRegex(R.settle_stops(base, P(n=150, settle=25, m4=True, hist=[23, 0, 0, 2, 0], max_us=20000), a), r"^S4: 25 ")
        # S5: > 3 % lows after >= 300 waveforms this session
        self.assertIsNone(R.settle_stops(base, P(n=100 + 299, low=50, settle=2, m4=True, hist=[0, 0, 0, 2, 0], max_us=20000), a))
        self.assertIsNone(R.settle_stops(base, P(n=100 + 300, low=9, settle=2, m4=True, hist=[0, 0, 0, 2, 0], max_us=20000), a))
        self.assertRegex(R.settle_stops(base, P(n=100 + 300, low=10, settle=2, m4=True, hist=[0, 0, 0, 2, 0], max_us=20000), a), r"^S5: ")
        # no settle line: no decision here (the module checks refuse such a session earlier)
        self.assertIsNone(R.settle_stops(R.parse_diag(status()), R.parse_diag(status()), a))

    def test_parse_f6_lines_and_s3_rate_s7(self):
        """review 89d: diag_vcom / diag_rate parse; S3 on a rate-limit skip; S7 on a VCOM mismatch or a read error."""
        a = StopArgs()
        P = lambda **k: R.parse_diag(status() + diag_text(**k))
        d = P(settle=2, m4=True, f6=True, vreads=2)
        self.assertEqual(d["vcom"], {"reads": 2, "ok": 2, "mismatch": 0, "err": 0, "last": 0x7e, "last_ms": 1000})
        self.assertEqual(d["rate"], {"n": 15, "window_s": 600, "burst": 6, "skip": 0})
        self.assertIsNone(P(settle=2, m4=True)["vcom"])
        b = P(n=100, settle=2, m4=True, f6=True, vreads=2, hist=[0, 0, 0, 2, 0], max_us=20000)
        self.assertIsNone(R.settle_stops(b, P(n=110, settle=3, m4=True, f6=True, vreads=3, hist=[0, 0, 0, 3, 0], max_us=20000), a))
        self.assertRegex(R.settle_stops(b, P(n=110, settle=3, m4=True, f6=True, vreads=3, vmis=1, hist=[0, 0, 0, 3, 0], max_us=20000), a),
                         r"^S7: .*mismatch 0 -> 1.*0x74")
        self.assertRegex(R.settle_stops(b, P(n=110, settle=3, m4=True, f6=True, vreads=3, verr=1, hist=[0, 0, 0, 3, 0], max_us=20000), a),
                         r"^S7: .*err 0 -> 1")
        self.assertRegex(R.settle_stops(b, P(n=110, settle=2, m4=True, f6=True, vreads=2, rskip=1, hist=[0, 0, 0, 2, 0], max_us=20000), a),
                         r"^S3: 1 rate-limit skip")

    def test_parse_traces(self):
        t = ("[  941.100000] a9_eink eink-display: pg-settle trace: reads 21 bits 0000000000180000, vblank first 8000@941070000 rec 8002@941093528,"
             " update mode 0x206 rect 0,0-824,1648 clear 0, VCOM reg 0x7e (programmed 0x7e, r 0)\n"
             "[  950.000000] a9_eink eink-display: pg-settle trace: reads 3 bits 0000000000000006, vblank first 9@1 rec 0@0,"
             " update mode 0x203 rect 10,20-30,40 clear 1, VCOM reg 0x00 (programmed 0x00, r -61)\n")
        tr = R.parse_traces(t)
        self.assertEqual([(x["reads"], x["frames"], x["mode"], x["rect"], x["vcom"], x["r"]) for x in tr],
                         [(21, 2, "0x206", "0,0-824,1648", 0x7e, 0), (3, None, "0x203", "10,20-30,40", 0, -61)])

    def test_parse_dips(self):
        t = ("[  321.459000] a9_eink eink-display: rail-diag: PG low at waveform end (not a fault, not latched; the next update's sample decides): session 7 uptime_ms 321459 first 1\n"
             "[  321.486100] a9_eink eink-display: pg-settle: PG low after a waveform came back in 11200 us (recovered dip 1 of 3 this session; not a fault)\n"
             "[  400.000000] a9_eink eink-display: rail-diag: PG low at waveform end (not a fault, not latched; the next update's sample decides): session 9 uptime_ms 400000 first 0\n"
             "[  400.040000] a9_eink eink-display: pg-settle: PG low after a waveform came back in 39800 us from the first low reading (recovered dip 1 of 3 this session, 2 of 30 this boot; not a fault)\n")
        rows = R.parse_dips(t)
        self.assertEqual([(r[1], r[2], r[3]) for r in rows], [(11200, False, 7), (39800, True, 9)])
        self.assertAlmostEqual(rows[0][4], 27.1, places=1)

    def test_frame_stale_check(self):
        import tempfile
        d = tempfile.mkdtemp()
        for i in range(3):
            open(os.path.join(d, "f%05d.jpg" % i), "w").close()
        n, last, why = R.P.frame_stale_check(d, -1, 0.0, 100.0, 3.0)
        self.assertEqual((n, last, why), (3, 100.0, None))                       # first look: new frames
        self.assertEqual(R.P.frame_stale_check(d, 3, 100.0, 102.9, 3.0)[2], None)  # quiet but inside the limit
        why = R.P.frame_stale_check(d, 3, 100.0, 108.1, 3.0)[2]
        self.assertIn("camera stale: no new frame for 8.1 s", why)               # capture running, no frames: stale
        # the capture ended (Capture.stop() wrote index.jsonl): the analysis adds no frames, never stale
        open(os.path.join(d, "index.jsonl"), "w").close()
        self.assertIsNone(R.P.frame_stale_check(d, 3, 100.0, 400.0, 3.0)[2])
        e = tempfile.mkdtemp()                                                     # no frames yet: not stale (the probe handles rc 4)
        self.assertIsNone(R.P.frame_stale_check(e, 0, 0.0, 999.0, 3.0)[2])

    def test_opening_prefix(self):
        for g, want in (("N N N", True), ("N N Y", True), ("N Y Y", True), ("Y Y Y", False), ("Y N Y", False),
                        ("N ? Y", False), ("Y Y N", False), ("N Y", False), ("", False)):
            self.assertEqual(R.opening_prefix(g), want, g)

    def test_dense_steps_and_order(self):
        d = R.dense_steps(10, 3, 500)
        self.assertEqual(d["min_wait_ms"], 500)
        names = [x.get("name") for x in d["steps"][:-1]]
        self.assertEqual(len(names), 10)
        self.assertTrue(set(names) <= {"settings_main", "settings_display", "home"})      # page changes only, no scroll (partial)
        self.assertNotIn("scroll_down", names)
        self.assertEqual(d["steps"][-1], {"wait_ms": 3000})
        self.assertFalse(any("brightness" in " ".join(x.get("adb", [])) for x in d["steps"]))
        self.assertEqual([x["name"] for x in R.dense_steps(4, 1, 500, first=1)["steps"][:-1]],
                         ["settings_display", "home", "settings_main", "settings_display"])
        for o, want in (("DCW", "DCW"), ("cw", "CW"), ("D", "D"), ("WC", "WC")):
            self.assertEqual(R.parse_order(o), want)
        for bad in ("", "DD", "X", "CWX", "DCWD"):
            self.assertIsNone(R.parse_order(bad), bad)

    def test_parse_ring(self):
        r = R.parse_ring(RING)
        self.assertEqual(r[0], (2, 5, (1, 1, 0), (1100, 51100)))
        self.assertEqual(len(r), 2)

    def test_bound(self):
        self.assertAlmostEqual(R.upper_bound_95(600), 0.005)
        self.assertIsNone(R.upper_bound_95(0))

    def test_slice_steps(self):
        c = R.slice_steps(20, 10, False, 20, 64, 6)
        self.assertEqual(len(c["steps"]), 21)
        self.assertEqual(c["steps"][-1], {"wait_ms": 10000})
        self.assertGreaterEqual(c["min_wait_ms"], 2000)          # an interaction every >= 2 s
        self.assertFalse(any("brightness" in " ".join(s.get("adb", [])) for s in c["steps"]))
        w = R.slice_steps(20, 10, True, 20, 64, 6)
        names = [s.get("name") for s in w["steps"]]
        self.assertEqual(names[:2], ["fl_dip", None])
        self.assertEqual(w["steps"][1], {"wait_ms": 6000})
        self.assertEqual(w["steps"][2]["name"], "fl_wake")
        self.assertEqual(w["steps"][2]["adb"][-1], "64")          # written back to the original level
        self.assertLessEqual(w["steps"][2]["max_wait_ms"], 300)   # the first interaction follows within ~0.3 s
        self.assertEqual(len(w["steps"]), 24)


class REnv(Env):
    def __enter__(self):
        super().__enter__()
        ph = os.path.join(self.fd, "phone")
        self.status = os.path.join(ph, "sys/class/drm/card3-DSI-1/eink/status")
        self.ring = os.path.join(ph, "sys/class/drm/card3-DSI-1/eink/rail_diag")
        self.set_status(status())
        self.set_diag(diag_text() )
        # the fake glass only changes when a command carries SCREEN=<name>: cycle A, B, C on every input/am
        open(os.path.join(self.fd, "bin", "settings"), "a").write('case "$1" in put) echo "$*" >> "$FAKE_DIR/settings.log";; esac\n')
        for name in ("input", "am"):
            sh = os.path.join(self.fd, "bin", name)
            open(sh, "w").write('#!/usr/bin/env bash\nf="$FAKE_DIR/cycle"; n=$(cat "$f" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$f"\n'
                                'case $((n %% 3)) in 0) s=A;; 1) s=B;; *) s=C;; esac\n'
                                'bash "$(dirname "$0")/../../fake-adb-link.sh" shell %s "$@" SCREEN=$s\n' % name)
        for k, v in (("screen_brightness", "64"), ("screen_brightness_mode", "1")):
            open(os.path.join(ph, "settings-" + k), "w").write(v + "\n")
        return self

    def set_diag(self, txt):
        tmp = self.ring + ".tmp"
        open(tmp, "w").write(txt + RING)
        os.replace(tmp, self.ring)

    def set_status(self, txt):
        tmp = self.status + ".tmp"
        open(tmp, "w").write(txt)
        os.replace(tmp, self.status)

    def args(self, *extra):
        return [sys.executable, R0, "--dry-run", "--adb", "bash " + FAKE, "--out", self.out, "--calib", self.calp,
                "--url", self.cam.url, "--probe", "%s %s" % (sys.executable, PROBE),
                "--preroll-ms", "700", "--poll-s", "0.3", "--max-frames-stale-s", "12",
                "--block-s", "2", "--interactions", "3", "--idle-s", "0.5", "--dip-idle-s", "0.5"] + list(extra)

    def bump(self, stop, per=0.3, dn=5, dfull=3, extra=None):
        """a fake kernel: every `per` s, n += dn, full_n += dfull (until `stop` is set)."""
        n = full = 0
        while not stop.is_set():
            n += dn; full += dfull
            kw = dict(n=n, full=full)
            if extra:
                kw.update(extra(n, full))
            self.set_status(status(faults=kw.pop("faults", 0)))
            self.set_diag(diag_text(**kw))
            stop.wait(per)

    def scenarios(self):
        out = {}
        d = os.path.join(self.out, "runs")
        for t in sorted(os.listdir(d)):
            p = os.path.join(d, t, "scenario.json")
            if os.path.exists(p):
                out[t] = json.load(open(p))
        return out


class Refusals(unittest.TestCase):
    def test_go_plan_and_order(self):
        r = subprocess.run([sys.executable, R0, "--adb", "adb -s X"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        r = subprocess.run([sys.executable, R0, "--dry-run", "--adb", "adb -s X"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        r = subprocess.run([sys.executable, R0, "--plan"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("never a gate", r.stdout)
        r = subprocess.run([sys.executable, R0, "--plan", "--order", "XY"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    def test_preflight_refusals(self):
        for what, setup, msg in (("old module without diag", lambda e: os.unlink(e.ring), "diag_pg_end"),
                                 ("rail fault", lambda e: e.set_status(status(faults=1)), "rail_fault"),
                                 ("gate closed", lambda e: open(os.path.join(e.fd, "phone/props/vendor.a9ml.eink.gate"), "w").write("closed\n"), "not open"),
                                 ("no brightness", lambda e: os.unlink(os.path.join(e.fd, "phone/settings-screen_brightness")), "brightness")):
            with self.subTest(what), REnv() as e:
                setup(e)
                r = subprocess.run(e.args(), capture_output=True, text=True, env=e.env, timeout=120)
                self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
                self.assertIn(msg, r.stdout)
                self.assertFalse(os.path.exists(os.path.join(e.out, "runs", "01-C")))
                self.assertFalse(os.path.exists(os.path.join(e.fd, "settings.log")))       # nothing was written
                self.assertTrue(e.tripwire_clean())

    def gate_path(self, e):
        return os.path.join(e.fd, "phone/sys/module/a9_eink/parameters/enable")

    def test_gate_wait_while_the_opener_is_still_writing(self):
        """N Y Y mid-opening must wait for the final a9_eink gate write."""
        with REnv() as e:
            gp = self.gate_path(e)
            open(gp, "w").write("N\n")
            def later():
                time.sleep(2.5); open(gp, "w").write("Y\n")
            th = threading.Thread(target=later, daemon=True); th.start()
            r = subprocess.run(e.args("--gate-wait-s", "30", "--block-s", "0"), capture_output=True, text=True, env=e.env, timeout=300)
            th.join()
            self.assertIn("gates: 'N Y Y'", r.stdout)
            self.assertIn("preflight OK", r.stdout, r.stdout[-1500:])
            self.assertTrue(e.tripwire_clean())

    def test_gate_wait_is_bounded(self):
        with REnv() as e:
            open(self.gate_path(e), "w").write("N\n")
            t0 = time.monotonic()
            r = subprocess.run(e.args("--gate-wait-s", "3"), capture_output=True, text=True, env=e.env, timeout=120)
            self.assertEqual(r.returncode, 3, r.stdout)
            self.assertIn("gate readback changed: 'N Y Y'", r.stdout)
            self.assertGreaterEqual(time.monotonic() - t0, 3)

    def test_gate_wait_is_not_used_for_a_fault_or_a_closed_gate(self):
        def fault(e):                      # opening-looking gates (N Y Y) but the opener published a fault
            open(self.gate_path(e), "w").write("N\n")
            open(os.path.join(e.fd, "phone/props/vendor.a9ml.eink.fault"), "w").write("rail\n")

        def y_then_n(e):                   # Y N Y: a gate that was Y went N, not an opening
            open(os.path.join(e.fd, "phone/sys/module/fp9929a/parameters/allow_power_on"), "w").write("N\n")
        for what, setup in (("fault property", fault), ("gate y then n", y_then_n)):
            with self.subTest(what), REnv() as e:
                setup(e)
                t0 = time.monotonic()
                r = subprocess.run(e.args("--gate-wait-s", "60"), capture_output=True, text=True, env=e.env, timeout=120)
                self.assertEqual(r.returncode, 3, r.stdout)
                self.assertLess(time.monotonic() - t0, 40, "waited for a state that is not an opening")
                self.assertTrue(e.tripwire_clean())

    def test_module_sha_mismatch(self):
        with REnv() as e:
            vend = os.path.join(e.t.name, "vend/lib/modules")
            os.makedirs(vend)
            open(os.path.join(vend, "a9_eink.ko"), "wb").write(b"module")
            e.env["VENDR"] = os.path.join(e.t.name, "vend")
            r = subprocess.run(e.args("--expect-module-sha", "deadbeef"), capture_output=True, text=True, env=e.env, timeout=120)
            self.assertEqual(r.returncode, 3, r.stdout)
            self.assertIn("expected deadbeef", r.stdout)


class EndToEnd(unittest.TestCase):
    def read_only(self, e):
        # a gate/parameter write would fail loudly
        d = os.path.join(e.fd, "phone", "sys/module")
        for root, _, files in os.walk(d):
            for f in files:
                os.chmod(os.path.join(root, f), 0o444)

    def run_with(self, e, *extra, bumper=None, timeout=600):
        stop = threading.Event()
        th = threading.Thread(target=bumper or e.bump, args=(stop,), daemon=True)
        th.start()
        try:
            return subprocess.run(e.args(*extra), capture_output=True, text=True, env=e.env, timeout=timeout)
        finally:
            stop.set(); th.join()

    def test_full_run_counts_blocks_and_restores_brightness(self):
        with REnv() as e:
            self.read_only(e)
            r = self.run_with(e)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-2000:])
            sc = e.scenarios()
            c = [t for t in sc if t.endswith("-C")]
            w = [t for t in sc if t.endswith("-W")]
            self.assertTrue(c and w, sc.keys())
            self.assertLess(max(c), min(w))                       # --order CW: all C slices first
            for t in c:
                self.assertFalse(any("brightness" in " ".join(s.get("adb", [])) for s in sc[t]["steps"]), t)
            for t in w:
                self.assertEqual([s.get("name") for s in sc[t]["steps"][:3]], ["fl_dip", None, "fl_wake"])
            rec = json.load(open(os.path.join(e.out, "rail-r0.json")))
            tot = rec["totals"]
            self.assertEqual(tot["wakes"], len(w))
            self.assertGreater(tot["full"], 0)
            self.assertEqual(tot["rail_faults"], 0)
            self.assertTrue(all(s["wakes"] == 0 for s in rec["slices"] if s["block"] == "C"))
            md = open(os.path.join(e.out, "summary.md")).read()
            self.assertIn("0 rail faults in %d full-screen waveforms" % tot["full"], md)
            self.assertIn("Not a fix claim", md)
            self.assertIn("shutdown trace ring", md)
            # brightness and mode are back; the dip level was really written during the W block
            self.assertEqual(rd(e.fd, "settings-screen_brightness"), "64")
            self.assertEqual(rd(e.fd, "settings-screen_brightness_mode"), "1")
            log = open(os.path.join(e.fd, "settings.log")).read()
            self.assertEqual(len(re.findall(r"put system screen_brightness 20", log)), len(w))     # one dip per W slice, none in C
            self.assertTrue(os.path.exists(os.path.join(e.out, "stop-evidence", "rail_diag.txt")))
            self.assertEqual(r.stdout.count("COORDINATOR: close the gates"), 1)
            self.assertTrue(e.tripwire_clean(), "a real adb binary was invoked")

    def test_stops_on_first_rail_fault_and_restores(self):
        with REnv() as e:
            self.read_only(e)
            r = self.run_with(e, "--order", "WC", bumper=lambda stop: e.bump(stop, extra=lambda n, full: {"faults": 1} if n >= 20 else {}))
            self.assertEqual(r.returncode, 3, r.stdout[-3000:])
            self.assertRegex(r.stdout.lower(), r"rail.fault")
            self.assertIn("RAIL FAULT", open(os.path.join(e.out, "summary.md")).read())
            self.assertEqual(rd(e.fd, "settings-screen_brightness"), "64")        # stopped inside the W block, restored
            self.assertEqual(rd(e.fd, "settings-screen_brightness_mode"), "1")
            self.assertTrue(os.path.exists(os.path.join(e.out, "stop-evidence", "eink-status.txt")))
            self.assertFalse([t for t in e.scenarios() if t.endswith("-C")])     # stopped before the second block
            self.assertTrue(e.tripwire_clean())

    def test_stop_on_wf_end_low_and_continue_flag(self):
        for flag, rc in (([], 3), (["--no-stop-on-end-low"], 0)):
            with self.subTest(flag), REnv() as e:
                self.read_only(e)
                r = self.run_with(e, *flag, bumper=lambda stop: e.bump(stop, extra=lambda n, full: {"low": 1} if n >= 60 else {}))
                self.assertEqual(r.returncode, rc, r.stdout[-2500:])
                if rc:
                    self.assertIn("PG low at a waveform end", r.stdout)
                else:
                    self.assertEqual(json.load(open(os.path.join(e.out, "rail-r0.json")))["since_preflight"]["low"], 1)
                    self.assertIn("wf-end PG lows 1", open(os.path.join(e.out, "summary.md")).read())
                self.assertEqual(rd(e.fd, "settings-screen_brightness"), "64")

    def param(self, e, name="pg_settle_ms"):
        return os.path.join(e.fd, "phone", "sys/module/a9_eink/parameters", name)

    def read_only_except(self, e, keep):
        self.read_only(e)
        os.chmod(self.param(e, keep), 0o644)

    def test_pg_settle_written_logged_and_reverted(self):
        with REnv() as e:
            open(self.param(e), "w").write("0\n")
            self.read_only_except(e, "pg_settle_ms")
            seen = []
            stop = threading.Event()

            def bumper(stop):
                e.bump(stop, extra=lambda n, full: (seen.append(rd(e.fd, "sys/module/a9_eink/parameters/pg_settle_ms")) or
                                                    ({"low": 1, "settle": 1} if n >= 60 else {})))
            r = self.run_with(e, "--pg-settle-ms", "100", bumper=bumper)
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-1500:])
            self.assertIn("100", seen)                                           # written while the session ran
            self.assertEqual(rd(e.fd, "sys/module/a9_eink/parameters/pg_settle_ms"), "0")     # reverted
            md = open(os.path.join(e.out, "summary.md")).read()
            self.assertIn("pg-settle (a9_eink.pg_settle_ms=100)", md)
            self.assertIn("wf-end PG lows 1", md)                                # a low did not stop the run (implied flag)
            ev = open(os.path.join(e.out, "events.jsonl")).read()
            self.assertIn("revert pg_settle_ms", ev)
            self.assertTrue(e.tripwire_clean())

    def test_pg_settle_refused_without_the_parameter_or_with_a_bad_value(self):
        with REnv() as e:
            self.read_only(e)
            r = self.run_with(e, "--pg-settle-ms", "100")
            self.assertEqual(r.returncode, 3, r.stdout[-2000:])
            self.assertIn("pg_settle_ms parameter not present", r.stdout + open(os.path.join(e.out, "summary.md")).read())
        r = subprocess.run([sys.executable, R0, "--plan", "--pg-settle-ms", "500"], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("1..200", r.stderr)

    def test_pg_settle_revert_failure_is_reported(self):
        with REnv() as e:
            open(self.param(e), "w").write("0\n")
            self.read_only(e)                       # the write itself fails: readback does not confirm
            r = self.run_with(e, "--pg-settle-ms", "100")
            self.assertEqual(r.returncode, 3, r.stdout[-2000:])
            self.assertIn("pg_settle_ms write not confirmed", r.stdout)

    def test_camera_stale_is_a_warning_by_default_and_the_flag_restores_the_stop(self):
        """USER DECISION 2026-10-17: a stale camera does not stop an R0 session (optical metrics invalid, stream reconnected);
        --stop-on-camera-stale is the opt-in old stop. Kernel counters never depend on the camera."""
        class StallEnv(REnv):
            stall = (3.0, 8.0)
        for flag, rc in (([], 0), (["--stop-on-camera-stale"], 3)):
            with self.subTest(flag), StallEnv() as e:
                self.read_only(e)
                r = self.run_with(e, "--max-frames-stale-s", "2", "--stale-reconnect-s", "2.5", *flag)
                self.assertEqual(r.returncode, rc, r.stdout[-3000:])
                md = open(os.path.join(e.out, "summary.md")).read()
                if rc == 0:
                    self.assertIn("WARNING (camera, not stopping)", r.stdout)
                    self.assertIn("CAMERA WARNING", md)
                    self.assertIn("INVALID", r.stdout)
                    rec = json.load(open(os.path.join(e.out, "rail-r0.json")))
                    self.assertTrue(any(s.get("camera_invalid") for s in rec["slices"]))
                    self.assertGreater(rec["totals"]["waveforms"], 0)          # the kernel counters are unaffected
                else:
                    self.assertIn("camera stale: no new frame for", r.stdout)
                self.assertEqual(rd(e.fd, "settings-screen_brightness"), "64")
                self.assertTrue(e.tripwire_clean())

    def test_dense_block_goal_and_summary(self):
        """order DCW: the D slices carry the dense scenario, the summary has a D row; --stop-after-dips ends the run successfully (rc 0,
        GOAL REACHED) at a slice boundary once enough recovered dips were counted; without the flag the plan runs to its end."""
        for goal, rc_goal in ((0, False), (3, True)):
            with self.subTest(goal=goal), REnv() as e:
                open(self.param(e), "w").write("0\n")
                self.read_only_except(e, "pg_settle_ms")
                extra = ["--order", "DCW", "--dense-block-s", "2", "--dense-interactions", "6", "--dense-idle-s", "0.5",
                         "--pg-settle-ms", "100",
                         "--s4-boot-dips", "100000"]      # the fake kernel makes a dip every ~1.5 s: S4 (25) would end the plan
                if goal:
                    extra += ["--stop-after-dips", str(goal)]
                # the fake kernel: one recovered dip per 25 counter steps (about every 1.5 s)
                r = self.run_with(e, *extra, bumper=lambda stop: e.bump(stop, extra=lambda n, full: {"settle": n // 25}))
                self.assertEqual(r.returncode, 0, r.stdout[-3000:])
                sc = e.scenarios()
                dn = [t for t in sc if t.endswith("-D")]
                self.assertTrue(dn, sc.keys())
                for t in dn:
                    self.assertEqual(sc[t]["name"], "rail-r0-dense")
                    self.assertEqual(sc[t]["min_wait_ms"], 500)
                md = open(os.path.join(e.out, "summary.md")).read()
                self.assertRegex(md, r"\| D \| \d+ \|")
                self.assertRegex(md, r"pg-settle: \d+ recovered dips, 0 timeouts")
                if rc_goal:
                    self.assertIn("GOAL REACHED: collected", r.stdout)
                    self.assertIn("**GOAL REACHED", md)
                    self.assertFalse([t for t in sc if t.endswith("-W")], "ran on after the goal: %s" % list(sc))
                    self.assertEqual(rd(e.fd, "sys/module/a9_eink/parameters/pg_settle_ms"), "0")      # still reverted
                else:
                    self.assertNotIn("GOAL", md)
                    self.assertTrue([t for t in sc if t.endswith("-W")])
                self.assertEqual(rd(e.fd, "settings-screen_brightness"), "64")
                self.assertTrue(e.tripwire_clean())

    def test_goal_never_hides_a_rail_fault_and_needs_the_settle(self):
        with REnv() as e:
            open(self.param(e), "w").write("0\n")
            self.read_only_except(e, "pg_settle_ms")
            r = self.run_with(e, "--order", "D", "--dense-block-s", "2", "--dense-interactions", "4", "--pg-settle-ms", "100",
                              "--stop-after-dips", "1",
                              bumper=lambda stop: e.bump(stop, extra=lambda n, full: {"settle": 5, "faults": 1} if n >= 20 else {}))
            self.assertEqual(r.returncode, 3, r.stdout[-2000:])
            self.assertIn("RAIL FAULT", open(os.path.join(e.out, "summary.md")).read())
            self.assertNotIn("GOAL REACHED", r.stdout)
        r = subprocess.run([sys.executable, R0, "--plan", "--stop-after-dips", "5"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("--stop-after-dips needs --pg-settle-ms", r.stderr)

    def test_settle_stop_s3_mid_session_and_per_dip_table(self):
        """S3 (a budget skip) stops a settle-on session at a monitor poll with rc 3, pg_settle_ms is reverted, and the summary
        has the per-dip table from dmesg (M4 clock)."""
        with REnv() as e:
            open(self.param(e), "w").write("0\n")
            self.read_only_except(e, "pg_settle_ms")
            with open(os.path.join(e.fd, "phone", "dmesg.txt"), "a") as f:
                f.write("\n[  600.000000] a9_eink eink-display: rail-diag: PG low at waveform end (not a fault, not latched; the next update's sample decides): session 4 uptime_ms 600000 first 1\n"
                        "[  600.031000] a9_eink eink-display: pg-settle: PG low after a waveform came back in 31000 us from the first low reading (recovered dip 1 of 3 this session, 1 of 30 this boot; not a fault)\n")
            r = self.run_with(e, "--order", "D", "--dense-block-s", "30", "--dense-interactions", "4", "--pg-settle-ms", "100",
                              "--stop-after-dips", "20",
                              bumper=lambda stop: e.bump(stop, extra=lambda n, full: dict(m4=True, settle=1 if n >= 10 else 0,
                                                                                         skip=1 if n >= 25 else 0)))
            self.assertEqual(r.returncode, 3, r.stdout[-3000:])
            self.assertIn("S3: 1 budget skip", r.stdout)
            self.assertEqual(rd(e.fd, "sys/module/a9_eink/parameters/pg_settle_ms"), "0")
            md = open(os.path.join(e.out, "summary.md")).read()
            self.assertRegex(md, r"\| 1 \| 600\.031 \| 31000 \| 31\.0 \| first \| 4 \| 31\.0 \| YES \|")
            self.assertIn("wf-end lows already high on 0053's re-read", md)
            self.assertTrue(e.tripwire_clean())

    def test_log_updates_written_reverted_and_s7_stop_with_trace_columns(self):
        """--log-updates writes a9_eink.log_updates=1 for the session and reverts it; S7 (a VCOM mismatch) stops the session at a
        poll with rc 3; the per-dip table carries the F6 trace columns (VCOM, bits, frames, mode, rect)."""
        with REnv() as e:
            open(self.param(e), "w").write("0\n")
            open(self.param(e, "log_updates"), "w").write("N\n")
            self.read_only(e)
            os.chmod(self.param(e, "pg_settle_ms"), 0o644)
            os.chmod(self.param(e, "log_updates"), 0o644)
            with open(os.path.join(e.fd, "phone", "dmesg.txt"), "a") as f:
                f.write("\n[  600.000000] a9_eink eink-display: rail-diag: PG low at waveform end (not a fault, not latched; the next update's sample decides): session 4 uptime_ms 600000 first 1\n"
                        "[  600.038000] a9_eink eink-display: pg-settle: PG low after a waveform came back in 38000 us from the first low reading (recovered dip 1 of 3 this session, 1 of 1000 this boot; not a fault)\n"
                        "[  600.038100] a9_eink eink-display: pg-settle trace: reads 33 bits 0000000180000000, vblank first 51000@599990000 rec 51003@600025293,"
                        " update mode 0x206 rect 0,0-824,1648 clear 0, VCOM reg 0x74 (programmed 0x7e, r 0)\n")
            seen = []
            r = self.run_with(e, "--order", "D", "--dense-block-s", "30", "--dense-interactions", "4", "--pg-settle-ms", "100",
                              "--log-updates", "--stop-after-dips", "20",
                              bumper=lambda stop: e.bump(stop, extra=lambda n, full: (seen.append(rd(e.fd, "sys/module/a9_eink/parameters/log_updates")) or
                                                                                     dict(m4=True, f6=True, settle=1 if n >= 10 else 0,
                                                                                          vreads=1 if n >= 10 else 0, vmis=1 if n >= 25 else 0))))
            self.assertEqual(r.returncode, 3, r.stdout[-3000:])
            self.assertIn("S7: VCOM readback", r.stdout)
            self.assertIn("1", seen)                                                          # written during the session
            self.assertEqual(rd(e.fd, "sys/module/a9_eink/parameters/log_updates"), "0")      # reverted (to N)
            self.assertEqual(rd(e.fd, "sys/module/a9_eink/parameters/pg_settle_ms"), "0")
            md = open(os.path.join(e.out, "summary.md")).read()
            self.assertRegex(md, r"\| 1 \| 600\.038 \| 38000 \| 38\.0 \| first \| 4 \| 38\.0 \| YES \| 0x74/0x7e MISMATCH \| 0000000180000000 \(33\) \| 3 \| 0x206 \| 0,0-824,1648 \|")
            self.assertIn("VCOM readbacks this boot (F6): 1, ok 0, mismatch 1, err 0", md)
            self.assertEqual(json.load(open(os.path.join(e.out, "orig.json")))["log_updates"], "N")
            self.assertTrue(e.tripwire_clean())

    def test_dense_density_warning_is_only_a_warning(self):
        with REnv() as e:
            self.read_only(e)
            r = self.run_with(e, "--order", "D", "--dense-block-s", "2", "--dense-interactions", "4",
                              "--dense-target-full-per-min", "100000")
            self.assertEqual(r.returncode, 0, r.stdout[-2000:])
            self.assertIn("WARNING: dense slice", r.stdout)

    def test_revert_only_restores_brightness(self):
        with REnv() as e:
            os.makedirs(e.out)
            json.dump({"brightness": {"b": "64", "m": "1"}}, open(os.path.join(e.out, "orig.json"), "w"))
            subprocess.run(["bash", "-c", "echo 20 > %s/phone/settings-screen_brightness; echo 0 > %s/phone/settings-screen_brightness_mode" % (e.fd, e.fd)])
            r = subprocess.run([sys.executable, R0, "--dry-run", "--adb", "bash " + FAKE, "--revert-only", e.out], capture_output=True, text=True, env=e.env, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual((rd(e.fd, "settings-screen_brightness"), rd(e.fd, "settings-screen_brightness_mode")), ("64", "1"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
