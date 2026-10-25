"""Stock matched-source integration with fake phases/core only; no device I/O."""
import argparse
import json
import os
import pathlib
import signal
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts" / "eink-probe" / "sessions"))
import ghost_stock as G  # noqa: E402


class FakeMatched:
    sources = ["launcher", "dark", "white"]

    def __init__(self, _args, _adb, _out, _log, stock):
        assert stock
        self.started = self.closed = False
        self.captures = []
        self.cleanup_failures = []

    def start(self):
        self.started = True

    def probe_viewer(self):
        assert self.started
        self.viewer_probed = True

    def close(self):
        self.closed = True
        return self.cleanup_failures

    def baseline_tag(self, sid):
        return "00-" + sid + "-A"

    def sequence(self, n):
        return (self.sources + list(reversed(self.sources)))[:n]

    def source_step(self, sid):
        return {"name": "source-" + sid, "adb": ["shell", "fake-viewer " + sid]}

    def capture_source(self, tag, sid):
        self.captures.append((tag, sid))
        return {"id": sid, "verified": True, "fixture_sha256": None if sid == "launcher" else sid + "-fixture",
                "source_frame_mean_luma": 0.2 if sid == "dark" else 0.95,
                "wallpaper_mean_luma": 0.5, "roi": [0, 0, 824, 1648]}


class FakeStock(G.Stock):
    def __init__(self, args):
        super().__init__(args)
        self.phases, self.commands, self.checked_refs = [], [], []
        self.clean_results = []

    def sh(self, cmd, timeout=30):
        self.commands.append(cmd)
        return ""

    def phase(self, tag, steps):
        self.phases.append((tag, steps))
        return 0

    def wake(self):
        pass

    def awake_unlocked(self, _where):
        pass

    def state(self):
        return {"fl": "10", "b": "100", "th": "30", "m": "0"}

    def check_clean(self, tag):
        self.checked_refs.append((tag, self.ref))
        return {"clean": self.clean_results.pop(0) if self.clean_results else True, "score": 0}


def args(out, matched=True):
    return argparse.Namespace(out=out, adb="fake-never-executed", matched_source=matched,
                              ghost_reps=6, budget_s=900, rep_est_s=45, settings_start="warm",
                              clean_retries=2, post_s=3.5, epd_snap=None, fc_path="/fake/epd_force_clear")


class Integration(unittest.TestCase):
    def make(self, out, matched=True):
        with mock.patch.object(G.M, "MatchedRun", FakeMatched):
            session = FakeStock(args(out, matched))
        self.addCleanup(session.logf.close)
        return session

    def test_distinct_baselines_sources_metadata_and_warm_settings(self):
        with tempfile.TemporaryDirectory() as d:
            s = self.make(d)
            s.run()
            self.assertTrue(s.matched.started)
            self.assertEqual([tag for tag, _ in s.phases[:3]], ["00-launcher-A", "00-dark-A", "00-white-A"])
            self.assertTrue(all(len(steps) == 3 for _, steps in s.phases[:3]))
            self.assertEqual([r["source_id"] for r in s.reps], ["launcher", "dark", "white", "white", "dark", "launcher"])
            self.assertEqual([r["source"]["id"] for r in s.reps], ["launcher", "dark", "white", "white", "dark", "launcher"])
            self.assertTrue(all(r["source"]["verified"] for r in s.reps))
            self.assertEqual([pathlib.Path(p).parts[-2] for _, p in s.checked_refs],
                             ["00-launcher-A", "00-dark-A", "00-white-A", "00-white-A", "00-dark-A", "00-launcher-A"])
            # Prime the top page once, identically to mainline, before any
            # source baseline; the scored B transitions remain warm.
            self.assertIn("am start -S -a android.settings.SETTINGS", s.commands[0])
            for tag, steps in s.phases:
                if tag.endswith("-B"):
                    self.assertNotIn("-S", steps[0]["adb"])
            baseline = json.loads(pathlib.Path(d, "source-baselines.json").read_text())
            self.assertEqual(set(baseline), set(FakeMatched.sources))

    def test_retry_only_clears_without_relaunching_source(self):
        with tempfile.TemporaryDirectory() as d:
            s = self.make(d)
            s.clean_results = [False, True, True, True]
            s.run()
            phases = dict(s.phases)
            self.assertEqual(len(phases["01-A"]), 2)
            self.assertEqual(len(phases["01-A1"]), 1)
            self.assertEqual(phases["01-A1"][0]["name"], "clear_source")
            self.assertIn(("01-A1", "launcher"), s.matched.captures)
            self.assertEqual(s.reps[0]["A"]["tag"], "01-A1")

    def test_source_verification_error_is_not_silently_scored(self):
        with tempfile.TemporaryDirectory() as d:
            s = self.make(d)
            with mock.patch.object(s.matched, "capture_source", side_effect=RuntimeError("source verification failed")):
                with self.assertRaisesRegex(RuntimeError, "source verification failed"):
                    s.run()
            self.assertEqual(s.reps, [])
            with mock.patch.object(G.subprocess, "run"):
                self.assertEqual(s.finish(), [])
            self.assertTrue(s.matched.closed)

    def test_cleanup_failures_are_returned(self):
        with tempfile.TemporaryDirectory() as d:
            s = self.make(d)
            s.matched.cleanup_failures = ["home restored no"]
            events = []
            close = s.matched.close

            def record_close():
                events.append("restore")
                return close()

            def record_home(argv, **_kwargs):
                self.assertEqual(argv[-4:], ["shell", "input", "keyevent", "KEYCODE_HOME"])
                events.append("home")

            with mock.patch.object(s.matched, "close", side_effect=record_close), \
                    mock.patch.object(G.subprocess, "run", side_effect=record_home):
                self.assertEqual(s.finish(), ["home restored no"])
            self.assertTrue(s.matched.closed)
            self.assertEqual(events, ["restore", "home"])

    def test_legacy_baseline_and_cold_priming_are_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            s = self.make(d, matched=False)
            s.run()
            self.assertIsNone(s.matched)
            self.assertEqual(s.phases[0][0], "00-A")
            self.assertIn("am start -S", s.commands[0])
            self.assertTrue(all("source" not in r and "source_id" not in r for r in s.reps))
            self.assertFalse(pathlib.Path(d, "source-baselines.json").exists())


class MainCleanup(unittest.TestCase):
    def run_fake(self, out, run_behavior, failures=None, finish_error=False):
        originals = {signal.SIGINT: object(), signal.SIGTERM: object()}
        active = originals.copy()
        calls = []

        def set_signal(sig, handler):
            calls.append((sig, handler))
            old, active[sig] = active[sig], handler
            return old

        class Session:
            matched = object()
            reps = []
            finished = False

            def __init__(self, _args):
                self.out = out

            def preflight(self):
                pass

            def run(self):
                if run_behavior == "signal":
                    active[signal.SIGTERM](signal.SIGTERM, None)
                elif run_behavior == "error":
                    raise RuntimeError("source not full screen")

            def finish(self):
                self.finished = True
                if any(active[sig] != signal.SIG_IGN for sig in originals):
                    raise AssertionError("cleanup was interruptible")
                if finish_error:
                    raise RuntimeError("cleanup unexpected failure")
                return failures or []

            def log(self, _message):
                pass

        session = Session(None)

        def add_arguments(parser):
            parser.add_argument("--matched-source", action="store_true")
            parser.add_argument("--recovery-marker", default=str(pathlib.Path(out)/"pending.json"))

        argv = ["--adb", "fake-adb", "--out", out, "--calib", "fake-calib", "--dry-run", "--matched-source"]
        with mock.patch.object(G, "Stock", return_value=session), mock.patch.object(G.signal, "signal", side_effect=set_signal), \
                mock.patch.object(G.M, "add_arguments", side_effect=add_arguments), mock.patch.object(G.M, "validate"):
            if finish_error:
                self.assertEqual(G.main(argv), 3)
                rc = 3
            else:
                rc = G.main(argv)
        self.assertTrue(session.finished)
        self.assertEqual(active, originals)
        return rc, calls

    def test_signal_always_cleans_with_signals_masked_then_restores_handlers(self):
        with tempfile.TemporaryDirectory() as d:
            rc, calls = self.run_fake(d, "signal")
        self.assertEqual(rc, 3)
        self.assertEqual(sum(handler == signal.SIG_IGN for _, handler in calls), 2)

    def test_runtime_error_and_restore_failure_return_stop(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.run_fake(d, "error")[0], 3)
            self.assertEqual(self.run_fake(d, "ok", failures=["home restored no"])[0], 3)

    def test_handlers_restore_even_if_finish_raises(self):
        with tempfile.TemporaryDirectory() as d:
            self.run_fake(d, "error", finish_error=True)


if __name__ == "__main__":
    unittest.main()
