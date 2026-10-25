"""Offline tests for scripts/eink-probe/sessions (trial3b-perf, fastshell). Fake phone + fake camera.
No adb, no phone, no webcam: a PATH shim named `adb` records any accidental use and fails the test."""
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import synth  # noqa: E402
from test_probe import make_calib  # noqa: E402

SESS = os.path.join(HERE, "..", "..", "scripts", "eink-probe", "sessions")
sys.path.insert(0, SESS)
import trial3b_perf as T  # noqa: E402

PERF = os.path.join(SESS, "trial3b_perf.py")
FASTSHELL = os.path.join(SESS, "fastshell.py")
FAKE = os.path.join(HERE, "fake-phone.sh")
PROBE = os.path.join(HERE, "..", "..", "scripts", "eink-probe", "probe.py")

GFX = """Applications Graphics Acceleration Info:
** Graphics info for pid 3126 [com.android.launcher3] **
Total frames rendered: 47
Janky frames: 46 (97.87%)
50th percentile: 250ms
90th percentile: 250ms
99th percentile: 400ms
Number Slow UI thread: 12
Number Slow issue draw commands: 43
50th gpu percentile: 22ms
90th gpu percentile: 4950ms

---PROFILEDATA---
Flags,IntendedVsync,Vsync,IssueDrawCommandsStart,SwapBuffers,FrameCompleted,DequeueBufferDuration,QueueBufferDuration,GpuCompleted,SwapBuffersCompleted,
0,1000000000,1000000000,1010000000,1030000000,1260000000,200000000,1000000,1050000000,1255000000,
0,2000000000,2000000000,2010000000,2030000000,2100000000,10000000,1000000,9223372036854775807,2090000000,
1,3000000000,3000000000,3010000000,3030000000,3500000000,10000000,1000000,3040000000,3490000000,
---PROFILEDATA---
View hierarchy:
"""
TRANS = """     From  :   To
           :  200000000  400000000  800000000   time(ms)
*  200000000:          0          3          0      %d
   400000000:          2          0          1      %d
   800000000:          0          1          0      %d
Total transition : %d
"""


def build_phone(fd, share_top=0.3):
    """Fake /sys, /proc, shims and canned outputs under fd/phone and fd/bin."""
    ph = os.path.join(fd, "phone")

    def w(rel, txt):
        p = os.path.join(ph, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w").write(txt + "\n")
    m = "sys/module/"
    for k, v in (("enable", "Y"), ("bin_min", "0"), ("bin_max", "13"), ("hv_max_ms", "15000"), ("hv_hold_ms", "3000"),
                 ("hv_hold_interactive_ms", "0"), ("input_diagnostics", "N"), ("init_flash", "Y")):
        w(m + "a9_eink/parameters/" + k, v)
    for k, v in (("allow_power_on", "Y"), ("max_on_ms", "20000"), ("min_off_ms", "500"), ("pg_timeout_ms", "100")):
        w(m + "fp9929a/parameters/" + k, v)
    w(m + "panel_hisense_hlte556n_eink/parameters/enable", "Y")
    w("sys/class/drm/card3-DSI-1/eink/status", "updates 85 aborts 0 frames 845 commit_fail 0 stall_fault 0\nrail_fault 0 rail_faults 0 commit_max_us 310251 commit_slow 4 underruns 0 late_drive 0")
    w("sys/class/drm/card3-DSI-1/eink/input_stats", "enabled 0 limit 4096 samples 0 failures 0 pixels 0 full 0 partial 0 unchanged 0\n"
      "cpu_begin_total_ns 0 cpu_begin_max_ns 0\nshadow_wait_total_ns 0 shadow_wait_max_ns 0\nconvert_total_ns 0 convert_max_ns 0\ncpu_end_total_ns 0 cpu_end_max_ns 0\nfences 0 fence_total_ns 0 fence_max_ns 0")
    g = "sys/devices/platform/soc@0/5900000.gpu/"
    w(g + "power/control", "auto"); w(g + "power/autosuspend_delay_ms", "100"); w(g + "power/runtime_status", "suspended")
    w(g + "power/runtime_active_time", "1000"); w(g + "power/runtime_suspended_time", "5000")
    d = "sys/class/devfreq/5900000.gpu/"
    w(d + "governor", "simple_ondemand"); w(d + "cur_freq", "200000000"); w(d + "min_freq", "200000000"); w(d + "max_freq", "800000000")
    w(d + "polling_interval", "50"); w(d + "available_frequencies", "200000000 400000000 800000000")
    w(d + "trans_stat", (TRANS % (1000, 500, int(2000 * share_top / (1 - share_top)) if share_top < 1 else 9999, 6)).rstrip())
    os.symlink("../../../devices/platform/soc@0/5900000.gpu", os.path.join(ph, d, "device"))
    w("sys/class/thermal/thermal_zone0/type", "cpu0-thermal"); w("sys/class/thermal/thermal_zone0/temp", "41000")
    w("proc/sys/kernel/printk", "6\t4\t1\t7"); w("proc/uptime", "700.50 1000.00"); w("proc/loadavg", "0.5 0.4 0.3 1/200 99")
    w("proc/pressure/cpu", "some avg10=0.00 avg60=0.00 avg300=0.00 total=1")
    w("dmesg.txt", "[  100.000000] a9ml-eink: pre-open\n[  520.250000] a9ml-eink: gates OPEN (panel, fp9929a, a9_eink.enable), hv_hold_ms 3000")
    for k, v in (("vendor.a9ml.eink.gate", "open"), ("vendor.a9ml.eink.card", "card3")):
        w("props/" + k, v)
    w("gfx.txt", GFX)
    os.makedirs(os.path.join(ph, "data"), exist_ok=True)
    os.makedirs(os.path.join(fd, "screens"), exist_ok=True)
    for n in ("A", "B", "C", "mid"):
        cv2.imwrite(os.path.join(fd, "screens", n + ".png"), synth.screen(n))
    open(os.path.join(fd, "logical"), "w").write("A")
    # shims
    b = os.path.join(fd, "bin")
    os.makedirs(b, exist_ok=True)

    def shim(name, body):
        p = os.path.join(b, name)
        open(p, "w").write("#!/usr/bin/env bash\n" + body + "\n")
        os.chmod(p, 0o755)
    shim("getprop", 'cat "$FAKE_DIR/phone/props/$1" 2>/dev/null; exit 0')
    shim("id", "echo 0")
    shim("dmesg", 'cat "$FAKE_DIR/phone/dmesg.txt"')
    shim("pidof", 'if [ "$1" = perfetto ]; then [ -e "$FAKE_DIR/phone/perfetto.on" ] && echo 4242; exit 0; fi; echo 1; exit 0')
    shim("input", 'bash "$(dirname "$0")/../../fake-adb-link.sh" shell input "$@"')
    shim("am", 'bash "$(dirname "$0")/../../fake-adb-link.sh" shell am "$@"')
    shim("top", "echo 'top: fake'")
    shim("cmd", 'echo "com.android.launcher3/.Launcher"')
    shim("killall", "exit 0")
    shim("settings", 'f="$FAKE_DIR/phone/settings-$3"; case "$1" in get) [ -e "$f" ] && cat "$f" || echo null;; '
         'put) echo "$4" > "$f";; delete) rm -f "$f";; esac')
    shim("dumpsys", 'case "$2" in reset) ;; *) case "$1" in gfxinfo) cat "$FAKE_DIR/phone/gfx.txt";; SurfaceFlinger) echo "refresh rate 60 composition client";; esac;; esac')
    shim("perfetto", 'o=""; while [ $# -gt 0 ]; do [ "$1" = -o ] && o=$2; shift; done; echo trace > "$o"')


def rd(fd, rel):
    return open(os.path.join(fd, "phone", rel)).read().strip()


class Env:
    """temp dir with fake phone, fake camera, calibration, scenario, a PATH with a tripwire adb."""

    stall = None            # (after_s, for_s) camera hiccup, see synth.CamServer

    def __enter__(self):
        self.t = tempfile.TemporaryDirectory()
        t = self.t.name
        self.fd = os.path.join(t, "fake")
        os.makedirs(self.fd)
        # shims call fake-adb through a link next to FAKE_DIR so $0-relative paths work
        os.symlink(os.path.join(HERE, "fake-adb.sh"), os.path.join(t, "fake-adb-link.sh"))
        build_phone(self.fd)
        os.makedirs(os.path.join(t, "c"))
        _, self.calp = make_calib(os.path.join(t, "c"))
        sc = {"name": "fake", "min_wait_ms": 1000, "quiet_ms": 400, "max_wait_ms": 5000,
              "steps": [{"name": "home", "adb": ["shell", "input", "keyevent", "KEYCODE_HOME", "SCREEN=A"]},
                        {"name": "to_b", "adb": ["shell", "input", "tap", "1", "1", "SCREEN=B"]},
                        {"name": "to_c", "adb": ["shell", "input", "tap", "1", "1", "SCREEN=C"]}]}
        self.scp = os.path.join(t, "sc.json")
        json.dump(sc, open(self.scp, "w"))
        self.sf = os.path.join(self.fd, "state")
        open(self.sf, "w").write("A")
        frames = {n: [synth.jpeg(synth.camera(synth.screen(n), seed=i)) for i in range(5)] for n in ("A", "B", "C", "mid")}
        self.cam = synth.CamServer(self.sf, frames, fps=30.0, stall=self.stall)
        self.cam.__enter__()
        # tripwire: a real adb on PATH must never be used
        tw = os.path.join(t, "tw")
        os.makedirs(tw)
        open(os.path.join(tw, "adb"), "w").write("#!/bin/sh\necho CALLED >> %s/adb-called\nexit 99\n" % t)
        os.chmod(os.path.join(tw, "adb"), 0o755)
        self.env = dict(os.environ, FAKE_DIR=self.fd, PATH=tw + ":" + os.environ["PATH"])
        self.out = os.path.join(t, "out")
        return self

    def __exit__(self, *a):
        self.cam.__exit__()
        self.t.cleanup()

    def args(self, *extra):
        return [sys.executable, PERF, "--dry-run", "--adb", "bash " + FAKE, "--out", self.out, "--calib", self.calp,
                "--scenario", self.scp, "--url", self.cam.url, "--probe", "%s %s" % (sys.executable, PROBE),
                "--preroll-ms", "700", "--poll-s", "0.3", "--max-frames-stale-s", "3", "--trace-s", "1"] + list(extra)

    def run(self, *extra, timeout=600):
        return subprocess.run(self.args(*extra), capture_output=True, text=True, env=self.env, timeout=timeout)

    def tripwire_clean(self):
        return not os.path.exists(os.path.join(self.t.name, "adb-called"))


class Parsers(unittest.TestCase):
    def test_gfx_framestats(self):
        g = T.parse_gfx(GFX)
        self.assertEqual((g["frames"], g["p50"], g["slow_issue"], g["gpu_p90"]), (47, 250, 43, 4950))
        fs = g["fs"]
        self.assertEqual((fs["rows"], fs["used"], fs["gpu_completed_int64max"]), (3, 2, 1))   # flag-1 row excluded
        self.assertAlmostEqual(fs["dequeue_p90"], 200.0)
        self.assertAlmostEqual(fs["issue_p90"], 20.0)

    def test_trans_stat_and_input_stats(self):
        t, n = T.parse_trans_stat(TRANS % (1, 2, 3, 6))
        self.assertEqual((t[200000000], t[400000000], t[800000000], n), (1, 2, 3, 6))
        d, share = T.diff_times(t, {200000000: 11, 400000000: 2, 800000000: 103})
        self.assertAlmostEqual(share, 100 / 110.0)
        self.assertEqual(T.tokens("samples 5 full 2\nx 1")["samples"], "5")
        self.assertEqual(T.parse_trans_stat("garbage"), (None, None))

    def test_cmp_metric(self):
        self.assertEqual(T.cmp_metric([100, 110], [400, 420])["verdict"], "faster")
        self.assertEqual(T.cmp_metric([400, 410], [405, 395])["verdict"], "within noise")
        self.assertEqual(T.cmp_metric([900], [400, 420])["verdict"], "SLOWER")
        self.assertEqual(T.cmp_metric([], [1])["verdict"], "no data")

    def test_static_no_phone_tools(self):
        for f in ("trial3b_perf.py", "fastshell.py", "trial3b-perf.sh"):
            txt = open(os.path.join(SESS, f)).read()
            self.assertIsNone(re.search(r"\b(fastboot|edl|nc)\b", txt), f)
        src = open(PERF).read()
        # the knob writes: never a gate, bin, hv_max/hold or fp9929a parameter
        writes = re.findall(r"echo [^>\n]*> *\S+", src)
        for w in writes:
            for bad in ("a9_eink/parameters/enable", "allow_power_on", "bin_min", "bin_max", "hv_max_ms", "hv_hold_ms",
                        "max_on_ms", "min_off_ms", "pg_timeout_ms", "panel_hisense", "init_flash"):
                self.assertNotIn(bad, w, w)
            if "/parameters/" in w:
                self.assertTrue("input_diagnostics" in w or "hv_hold_interactive_ms" in w, w)


class FastShell(unittest.TestCase):
    def test_persistent_shell_protocol(self):
        with tempfile.TemporaryDirectory() as t:
            fd = os.path.join(t, "f"); os.makedirs(fd)
            build_phone(fd)
            env = dict(os.environ, FAKE_DIR=fd)
            sock, real = os.path.join(t, "s.sock"), "bash " + FAKE
            d = subprocess.Popen([sys.executable, FASTSHELL, "serve", "--sock", sock, "--real", real, "--ready-file", sock + ".r"], env=env)
            try:
                for _ in range(100):
                    if os.path.exists(sock + ".r"):
                        break
                    time.sleep(0.05)
                def run(*a):
                    return subprocess.run([sys.executable, FASTSHELL, "run", "--sock", sock, "--real", real, "--"] + list(a),
                                          capture_output=True, text=True, env=env, timeout=20)
                r = run("shell", "echo hello; echo err 1>&2; false")
                self.assertEqual((r.returncode, r.stdout), (1, "hello\nerr\n"))
                r = run("shell", "exit 7")                       # cannot kill the persistent shell
                self.assertEqual(r.returncode, 7)
                r = run("shell", "echo -n nonl")
                self.assertEqual((r.returncode, r.stdout), (0, "nonl"))
                r = subprocess.run([sys.executable, FASTSHELL, "run", "--sock", sock, "--real", real, "--", "exec-out", "screencap", "-p"],
                                   capture_output=True, env=env, timeout=20)     # pass-through reaches the real (fake) adb
                self.assertEqual((r.returncode, r.stdout[:4]), (0, b"\x89PNG"))
                subprocess.run([sys.executable, FASTSHELL, "stop", "--sock", sock], env=env, timeout=10)
                d.wait(timeout=10)
                r = run("shell", "echo x")
                self.assertEqual(r.returncode, 255)
                self.assertIn("not reachable", r.stderr)
            finally:
                if d.poll() is None:
                    d.kill()


class Refusals(unittest.TestCase):
    def test_needs_go_or_dry_run_and_plan_sends_nothing(self):
        r = subprocess.run([sys.executable, PERF, "--adb", "adb -s X"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        r = subprocess.run([sys.executable, PERF, "--dry-run", "--adb", "adb -s X"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)           # --dry-run refuses a real adb binary name
        r = subprocess.run([sys.executable, PERF, "--plan"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("never written", r.stdout)

    def test_preflight_refusals(self):
        for what, rel, val, msg in (("gate closed", "props/vendor.a9ml.eink.gate", "closed", "not open"),
                                    ("bins", "sys/module/a9_eink/parameters/bin_max", "12", "bins"),
                                    ("gate readback", "sys/module/fp9929a/parameters/allow_power_on", "N", "gate readback"),
                                    ("rail fault", "sys/class/drm/card3-DSI-1/eink/status", "rail_fault 1 updates 3", "rail_fault")):
            with self.subTest(what), Env() as e:
                open(os.path.join(e.fd, "phone", rel), "w").write(val + "\n")
                r = e.run("--arms", "f3", timeout=120)
                self.assertEqual(r.returncode, 3, r.stdout + r.stderr)
                self.assertIn(msg, r.stdout)
                self.assertFalse(os.path.exists(os.path.join(e.out, "runs", "01-B0-plain")))
                self.assertEqual(rd(e.fd, "sys/module/a9_eink/parameters/input_diagnostics"), "N")   # nothing was written
                self.assertTrue(e.tripwire_clean())


class EndToEnd(unittest.TestCase):
    def test_full_session_dry_run(self):
        with Env() as e:
            gates = {k: rd(e.fd, "sys/module/%s" % k) for k in ("a9_eink/parameters/enable", "fp9929a/parameters/allow_power_on",
                                                                  "a9_eink/parameters/bin_min", "a9_eink/parameters/hv_hold_ms")}
            # read-only gate files: a gate write would fail loudly
            for k in gates:
                os.chmod(os.path.join(e.fd, "phone", "sys/module", k), 0o444)
            r = e.run("--arms", "f3,f5,f2m,f2a,hvi", "--reps", "1", "--b0-fast-runs", "1", "--hvi-ms", "8000")
            self.assertEqual(r.returncode, 0, r.stdout[-3000:] + r.stderr[-2000:])
            out = e.out
            summ = json.load(open(os.path.join(out, "summary.json")))
            tags = [x["tag"] for x in summ["runs"]]
            self.assertIn("01-B0-plain", tags)
            self.assertTrue(any("T1-trace" in x for x in tags))
            self.assertTrue(any(x.endswith("f3-1") for x in tags) and any(x.endswith("f2m-1") for x in tags))
            self.assertFalse(any("f2a" in x for x in tags), tags)            # suspended 0 ms in the fake: skipped
            ev = [json.loads(l) for l in open(os.path.join(out, "events.jsonl"))]
            self.assertTrue(any(x["event"] == "arm_skipped" and x["arm"] == "f2a" for x in ev))
            # every knob is back at the original value
            self.assertEqual(rd(e.fd, "proc/sys/kernel/printk").split(), ["6", "4", "1", "7"])
            self.assertEqual(rd(e.fd, "sys/class/devfreq/5900000.gpu/min_freq"), "200000000")
            self.assertEqual(rd(e.fd, "sys/module/a9_eink/parameters/hv_hold_interactive_ms"), "0")
            self.assertIn(rd(e.fd, "sys/module/a9_eink/parameters/input_diagnostics"), ("N", "0"))   # real bool reads N after a 0 write
            self.assertFalse(os.path.exists(os.path.join(e.fd, "phone", "settings-window_animation_scale")))
            self.assertEqual({k: rd(e.fd, "sys/module/%s" % k) for k in gates}, gates)
            # applied values were seen during the arms
            kn = [x for x in ev if x["event"] == "knob"]
            self.assertTrue(any("RB=3" in x["out"] for x in kn))
            self.assertTrue(any("RB=0,0,null" in x["out"] for x in kn))
            self.assertTrue(any("min_freq 400000000" in x["what"] for x in kn))      # mid OPP of 3
            self.assertTrue(any(x["what"].startswith("hv_hold_interactive_ms 8000") for x in kn))
            # artefacts
            run1 = os.path.join(out, "runs", "02-B0-fast")
            for f in ("perf.json", "pre.txt", "post.txt", "gfxinfo-launcher.txt", "probe/report.json"):
                self.assertTrue(os.path.exists(os.path.join(run1, f)), f)
            self.assertTrue(os.path.exists(os.path.join(out, "runs", [x for x in tags if "T1-trace" in x][0], "eink-perf.pftrace")))
            self.assertTrue(os.path.exists(os.path.join(out, "runs", [x for x in tags if "T1-trace" in x][0], "top-0.txt")))
            md = open(os.path.join(out, "summary.md")).read()
            self.assertIn("A/B", md); self.assertIn("F7", md)
            fast = [x for x in summ["runs"] if x["injector"] == "fast"][0]
            self.assertIsNotNone(fast["fc_ret_med"])
            self.assertEqual(fast["gfx"]["launcher"]["p50"], 250)
            self.assertTrue(r.stdout.count("COORDINATOR: close the gates") == 1)
            self.assertTrue(e.tripwire_clean(), "a real adb binary was invoked")
            # no phone-side write left a gate param modified; the fake never saw a gate write command
            log = open(os.path.join(e.fd, "phone.log")).read()
            self.assertNotIn("allow_power_on", log.replace("P fp9929a/allow_power_on", ""))  # reads only via the snippet

    def test_stop_on_eink_fault_reverts_knob(self):
        with Env() as e:
            def trip():
                evf = os.path.join(e.out, "events.jsonl")
                for _ in range(2400):
                    time.sleep(0.25)
                    try:
                        txt = open(evf).read()
                    except OSError:
                        continue
                    if '"event": "apply"' in txt:
                        time.sleep(0.5)
                        open(os.path.join(e.fd, "phone", "props", "vendor.a9ml.eink.fault"), "w").write("rail fault\n")
                        return
            th = threading.Thread(target=trip, daemon=True); th.start()
            r = e.run("--arms", "f3,f5", "--reps", "1", "--b0-fast-runs", "1", "--no-trace")
            self.assertEqual(r.returncode, 3, r.stdout[-2500:])
            self.assertIn("E Ink fault latched", r.stdout)
            self.assertEqual(rd(e.fd, "proc/sys/kernel/printk").split()[0], "6")       # f3 was reverted
            md = open(os.path.join(e.out, "summary.md")).read()
            self.assertIn("STOPPED", md)
            self.assertTrue(e.tripwire_clean())

    def test_revert_only(self):
        with Env() as e:
            os.makedirs(e.out)
            subprocess.run(["bash", "-c", "echo '3 4 1 7' > %s/phone/proc/sys/kernel/printk" % e.fd])
            orig = {"printk": ["6", "4", "1", "7"], "printk_present": True, "anim": {"window_animation_scale": "null", "transition_animation_scale": "null", "animator_duration_scale": "null"},
                    "devfreq": {"min_freq": "200000000", "max_freq": "800000000", "governor": "x", "cur_freq": "1"}, "devfreq_present": True, "pm": {"control": "auto", "autosuspend_delay_ms": "100"},
                    "pm_present": True, "input_diagnostics": "N", "hv_hold_interactive_ms": "0", "freqs": [200000000, 400000000, 800000000]}
            json.dump(orig, open(os.path.join(e.out, "orig.json"), "w"))
            r = subprocess.run([sys.executable, PERF, "--dry-run", "--adb", "bash " + FAKE, "--revert-only", e.out], capture_output=True, text=True, env=e.env, timeout=60)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertEqual(rd(e.fd, "proc/sys/kernel/printk").split()[0], "6")


class CaptureReconnect(unittest.TestCase):
    """probe_lib.Capture reopens a stream that goes silent, records it, and keeps the old first-connect behaviour."""

    def setUp(self):
        sys.path.insert(0, os.path.join(HERE, "..", "..", "scripts", "eink-probe"))
        import probe_lib as L
        self.L = L

    def server(self, stall_first=None):
        sf = tempfile.NamedTemporaryFile("w", delete=False); sf.write("A"); sf.close()
        frames = {"A": [synth.jpeg(synth.camera(synth.screen("A"), seed=i)) for i in range(3)]}
        return synth.CamServer(sf.name, frames, fps=30.0, stall=stall_first)

    def test_reconnect_after_a_silent_stream(self):
        with self.server(stall_first=(1.0, 6.0)) as cam, tempfile.TemporaryDirectory() as d:
            cap = self.L.Capture(cam.url, d, stale_reconnect_s=1.5).start()
            self.assertTrue(cap.wait_first(5))
            t0 = time.monotonic()
            while time.monotonic() - t0 < 6 and not cap.reconnects:
                time.sleep(0.1)
            n1 = len(cap.index)
            time.sleep(1.0)
            idx = cap.stop()
            self.assertGreaterEqual(len(cap.reconnects), 1, "no reconnect")
            self.assertIsNone(cap.error)
            self.assertGreater(len(idx), n1, "no frames after the reconnect")
            gaps = cap.stale_gaps(1.4)
            self.assertTrue(gaps and gaps[0][1] - gaps[0][0] >= 1.5, gaps)
            self.assertGreaterEqual(cap.reconnects[0]["gap_s"], 1.4)
            self.assertTrue(os.path.exists(os.path.join(d, "index.jsonl")))

    def test_no_reconnect_when_disabled_or_never_connected(self):
        with tempfile.TemporaryDirectory() as d:
            cap = self.L.Capture("http://127.0.0.1:9/stream", d, stale_reconnect_s=1.0).start()
            self.assertFalse(cap.wait_first(3))
            time.sleep(0.3)
            self.assertTrue(cap.error)                     # as before: the first connection failing is an error at once
            self.assertEqual(cap.reconnects, [])
            cap.stop()
        with self.server(stall_first=(0.5, 3.0)) as cam, tempfile.TemporaryDirectory() as d:
            cap = self.L.Capture(cam.url, d, stale_reconnect_s=0).start()      # 0 = never reconnect
            self.assertTrue(cap.wait_first(5))
            time.sleep(4.5)
            cap.stop()
            self.assertEqual(cap.reconnects, [])
            self.assertEqual(cap.stale_gaps(1.0)[0][1] - cap.stale_gaps(1.0)[0][0] > 2.5, True)


class CameraStaleStop(unittest.TestCase):
    """USER DECISION 2026-10-17: a stale/frozen camera is a warning (run's optical metrics INVALID, stream reconnected); the old stop is opt-in."""

    def run_stalled(self, *extra):
        class E(Env):
            stall = (1.0, 7.0)
        with E() as e:
            r = e.run("--arms", "f3", "--reps", "1", "--b0-fast-runs", "1", "--no-trace", "--max-frames-stale-s", "2",
                      "--stale-reconnect-s", "2.5", *extra)
            summ = None
            if os.path.exists(os.path.join(e.out, "summary.json")):
                summ = json.load(open(os.path.join(e.out, "summary.json")))
            ev = [json.loads(l) for l in open(os.path.join(e.out, "events.jsonl"))] if os.path.exists(os.path.join(e.out, "events.jsonl")) else []
            md = open(os.path.join(e.out, "summary.md")).read() if os.path.exists(os.path.join(e.out, "summary.md")) else ""
            return r, summ, ev, md, e.tripwire_clean()

    def test_default_warns_marks_invalid_reconnects_and_goes_on(self):
        r, summ, ev, md, tw = self.run_stalled()
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])
        self.assertIn("WARNING (camera, not stopping)", r.stdout)
        self.assertNotIn("STOP CONDITION", r.stdout)
        self.assertTrue(any(x["event"] == "camera_stale_warning" for x in ev))
        runs = summ["runs"]
        first = runs[0]
        self.assertTrue(first.get("camera_invalid"), first.get("camera_notes"))
        self.assertTrue(first.get("optical_invalid"))
        self.assertIsNone(first["fc_med"])                                  # no number is published from an invalid run
        self.assertIsNotNone(first["optical_raw"])                           # but kept for forensics
        self.assertGreaterEqual(first["camera"]["reconnects"], 1)
        self.assertGreater(len(runs), 1)                                     # the session went on
        later = [x for x in runs[1:] if not x.get("camera_invalid")]
        self.assertTrue(later and later[0]["fc_med"] is not None, "later runs lost their optical metrics")
        self.assertTrue(tw)

    def test_opt_in_flag_restores_the_old_stop(self):
        r, summ, ev, md, tw = self.run_stalled("--stop-on-camera-stale")
        self.assertEqual(r.returncode, 3, r.stdout[-3000:])
        self.assertIn("STOP CONDITION (monitor): camera stale: no new frame for", r.stdout)
        self.assertIn("STOPPED", md)
        self.assertFalse(any(x["event"] == "camera_stale_warning" for x in ev))
        self.assertTrue(tw)

    def test_kernel_side_stops_are_unchanged_while_the_camera_is_stale(self):
        """a rail fault during a stalled-camera run still stops the session"""
        class E(Env):
            stall = (1.0, 7.0)
        with E() as e:
            def trip():
                time.sleep(3.5)
                p = os.path.join(e.fd, "phone", "sys/class/drm/card3-DSI-1/eink/status")
                txt = open(p).read()
                open(p, "w").write(txt.replace("rail_fault 0", "rail_fault 1"))
            th = threading.Thread(target=trip, daemon=True); th.start()
            r = e.run("--arms", "f3", "--reps", "1", "--b0-fast-runs", "1", "--no-trace", "--max-frames-stale-s", "2",
                      "--stale-reconnect-s", "2.5")
            th.join()
            self.assertEqual(r.returncode, 3, r.stdout[-2000:])
            self.assertIn("rail_fault", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
