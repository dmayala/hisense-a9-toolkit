"""Offline transport/state rehearsals for the real matched-source core.

Every subprocess call is intercepted. The fake executable is never launched;
its command allowlist rejects settings/property mutations and unrelated paths.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "eink-probe" / "sessions"))
import ghost_matched as M
import ghost_stock as S

try:
    import cv2
except ImportError:
    cv2 = None

OLD_HOME = "example.old/example.old.Home"
NEW_HOME = "com.android.launcher3/com.android.launcher3.Launcher"
VIEWER = ["am", "start", "-n", "verified.viewer/verified.viewer.Image", "-d", "{uri}", "-t", "image/png"]


def arguments(directory):
    return argparse.Namespace(matched_source=True, launcher_component=NEW_HOME,
                              source_viewer=json.dumps(VIEWER), source_roi="32,100,792,1560",
                              wallpaper_roi="32,100,792,1560", settings_start="warm",
                              ghost_reps=6, source_wait_ms=4000, recovery_marker=str(Path(directory)/"pending.json"))


class Transport:
    """Stateful fake Android shell, with partial-failure injection."""
    def __init__(self, out, home=OLD_HOME):
        self.out, self.home = Path(out), home
        self.files = {"/data/local/tmp/keep-unrelated.png": b"keep"}
        self.calls, self.sets, self.deleted = [], [], []
        self.fail_push = self.fail_set_after_change = self.restore_mismatch = False
        self.fail_remove = False
        self.interrupt_restore = False
        self.receipt_before_first_set = None
        self.foreground = VIEWER[3]

    def __call__(self, argv, **_kw):
        argv = list(argv)
        if argv[0] != "fake-matched-transport":
            raise AssertionError("unexpected executable")
        args = argv[1:]
        self.calls.append(args)
        stdout, rc = "", 0
        if args and args[0] == "push":
            if len(args) != 3 or not args[2].startswith("/data/local/tmp/ghost-matched-"):
                raise AssertionError("unexpected push path")
            data = Path(args[1]).read_bytes()
            self.files[args[2]] = data[:20] if self.fail_push else data
            rc = 7 if self.fail_push else 0
        elif args and args[0] == "shell":
            command = args[1] if len(args) == 2 else shlex.join(args[1:])
            words = shlex.split(command)
            if command == "getprop ro.boot.slot_suffix; getprop sys.boot_completed":
                stdout = "_a\n1"
            elif command == M.HOME_QUERY:
                stdout = self.home + "\n"
            elif command == "dumpsys activity activities | grep -E 'topResumedActivity|mResumedActivity' | tail -n 1":
                stdout = "mResumedActivity: ActivityRecord{test u0 " + self.foreground + " t1}"
            elif words[:3] == ["cmd", "package", "set-home-activity"] and len(words) == 4:
                requested = words[3]
                receipt = json.loads((self.out / "matched-source.json").read_text())
                if not self.sets:
                    self.receipt_before_first_set = receipt
                if not receipt["home_change_armed"] or receipt["original_home"] != OLD_HOME:
                    raise AssertionError("rollback was not persisted before setter")
                self.sets.append(requested)
                if self.interrupt_restore and requested == OLD_HOME:
                    for signum in (signal.SIGINT, signal.SIGTERM):
                        if signal.getsignal(signum) != signal.SIG_IGN:
                            raise AssertionError("cleanup did not mask interruption")
                        signal.raise_signal(signum)
                if not (self.restore_mismatch and requested == OLD_HOME):
                    self.home = requested
                if self.fail_set_after_change and requested == NEW_HOME:
                    rc = 7
            elif words[:2] == ["chmod", "0644"] and len(words) == 3:
                if words[2] not in self.files:
                    raise AssertionError("chmod of unowned file")
            elif words[:1] == ["sha256sum"] and len(words) == 2:
                stdout = hashlib.sha256(self.files[words[1]]).hexdigest() + "  " + words[1]
            elif words[:3] == ["rm", "-f", "--"] and command.startswith("rm -f -- "):
                path = words[3].rstrip(";")
                if path not in self.files or not path.startswith("/data/local/tmp/ghost-matched-"):
                    raise AssertionError("cleanup attempted an unowned path")
                self.deleted.append(path)
                if not self.fail_remove:
                    del self.files[path]
                stdout = "PRESENT" if path in self.files else "REMOVED"
            elif words[:3] == ["if", "[", "-e"]:
                path = words[3]
                stdout = ("EXISTS" if path in self.files else "ABSENT") if "echo ABSENT" in command else (
                    "PRESENT" if path in self.files else "REMOVED")
            elif words == ["input", "keyevent", "KEYCODE_HOME"]:
                pass
            else:
                raise AssertionError("command outside test allowlist: " + command)
        else:
            raise AssertionError("unexpected transport operation")
        return subprocess.CompletedProcess(argv, rc, stdout, "injected failure" if rc else "")


class Lifecycle(unittest.TestCase):
    def make(self, directory, stock=True, home=OLD_HOME):
        transport = Transport(directory, home)
        logs = []
        run = M.MatchedRun(arguments(directory), ["fake-matched-transport"], directory, logs.append, stock=stock)
        return run, transport, logs

    def receipt(self, directory):
        return json.loads(Path(directory, "matched-source.json").read_text())

    def test_stock_success_restores_home_and_exact_files_only(self):
        with tempfile.TemporaryDirectory() as d:
            run, transport, logs = self.make(d)
            with mock.patch.object(M.subprocess, "run", side_effect=transport):
                run.start()
                self.assertEqual(transport.home, NEW_HOME)
                self.assertEqual(set(run.paths), {"dark", "white"})
                for sid, path in run.paths.items():
                    self.assertEqual(hashlib.sha256(transport.files[path]).hexdigest(), run.images[sid]["sha256"])
                self.assertEqual(run.close(), [])
                calls = len(transport.calls)
                self.assertEqual(run.close(), [])
                self.assertEqual(len(transport.calls), calls)
            self.assertEqual(transport.home, OLD_HOME)
            self.assertEqual(transport.sets, [NEW_HOME, OLD_HOME])
            self.assertEqual(set(transport.deleted), set(run.owned))
            self.assertEqual(transport.files, {"/data/local/tmp/keep-unrelated.png": b"keep"})
            self.assertTrue(transport.receipt_before_first_set["home_change_armed"])
            self.assertTrue(self.receipt(d)["home_restored"])
            self.assertIn("home restored: yes", logs)

    def test_partial_push_failure_removes_pending_file_and_restores_home(self):
        with tempfile.TemporaryDirectory() as d:
            run, transport, _logs = self.make(d)
            transport.fail_push = True
            with mock.patch.object(M.subprocess, "run", side_effect=transport):
                with self.assertRaisesRegex(RuntimeError, "transport command failed"):
                    run.start()
                self.assertEqual(len(run.owned), 1)
                self.assertEqual(len(transport.files[run.owned[0]]), 20)
                self.assertEqual(run.close(), [])
            self.assertEqual(transport.home, OLD_HOME)
            self.assertEqual(transport.deleted, run.owned)
            self.assertEqual(transport.files, {"/data/local/tmp/keep-unrelated.png": b"keep"})
            self.assertTrue(self.receipt(d)["home_restored"])

    def test_setter_failure_after_mutation_still_restores(self):
        with tempfile.TemporaryDirectory() as d:
            run, transport, _logs = self.make(d)
            transport.fail_set_after_change = True
            with mock.patch.object(M.subprocess, "run", side_effect=transport):
                with self.assertRaisesRegex(RuntimeError, "transport command failed"):
                    run.start()
                self.assertEqual(transport.home, NEW_HOME)
                self.assertEqual(run.close(), [])
            self.assertEqual(transport.home, OLD_HOME)
            self.assertEqual(transport.sets, [NEW_HOME, OLD_HOME])
            self.assertEqual(transport.deleted, [])

    def test_restore_readback_mismatch_is_recorded_and_reported(self):
        with tempfile.TemporaryDirectory() as d:
            run, transport, logs = self.make(d)
            with mock.patch.object(M.subprocess, "run", side_effect=transport):
                run.start()
                transport.restore_mismatch = True
                failures = run.close()
            self.assertTrue(any("HOME verification mismatch" in f for f in failures))
            self.assertIn("home restored: no", logs)
            self.assertFalse(self.receipt(d)["home_restored"])
            self.assertEqual(self.receipt(d)["cleanup_failures"], failures)
            self.assertEqual(set(transport.deleted), set(run.owned))

    def test_cleanup_failure_checks_existence_even_when_shell_returns_success(self):
        with tempfile.TemporaryDirectory() as d:
            run, transport, _logs = self.make(d)
            with mock.patch.object(M.subprocess, "run", side_effect=transport):
                run.start()
                transport.fail_remove = True
                failures = run.close()
            self.assertEqual(len(failures), 2)
            self.assertTrue(all("temporary source still present" in f for f in failures))
            self.assertTrue(self.receipt(d)["home_restored"])

    def test_mainline_asserts_existing_home_and_never_sets_it(self):
        with tempfile.TemporaryDirectory() as d:
            run, transport, _logs = self.make(d, stock=False, home=NEW_HOME)
            with mock.patch.object(M.subprocess, "run", side_effect=transport):
                run.start()
                self.assertEqual(run.close(), [])
            self.assertEqual(transport.sets, [])
            self.assertEqual(transport.home, NEW_HOME)

    def test_mainline_wrong_home_refuses_before_staging_and_without_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            run, transport, _logs = self.make(d, stock=False)
            with mock.patch.object(M.subprocess, "run", side_effect=transport):
                with self.assertRaisesRegex(RuntimeError, "not the requested"):
                    run.start()
                self.assertEqual(run.close(), [])
            self.assertEqual(transport.sets, [])
            self.assertEqual(run.owned, [])

    def test_existing_remote_path_is_never_claimed_or_deleted(self):
        with tempfile.TemporaryDirectory() as d:
            run, transport, _logs = self.make(d)
            path = "/data/local/tmp/ghost-matched-%s-dark.png" % run.token
            transport.files[path] = b"preexisting file"
            with mock.patch.object(M.subprocess, "run", side_effect=transport):
                with self.assertRaisesRegex(RuntimeError, "already exists"):
                    run.start()
                self.assertEqual(run.close(), [])
            self.assertEqual(run.owned, [])
            self.assertEqual(transport.deleted, [])
            self.assertEqual(transport.files[path], b"preexisting file")
            self.assertEqual(transport.home, OLD_HOME)

    def test_mainline_accepts_equivalent_short_component_without_setter(self):
        with tempfile.TemporaryDirectory() as d:
            run, transport, _logs = self.make(d, stock=False, home="com.android.launcher3/.Launcher")
            with mock.patch.object(M.subprocess, "run", side_effect=transport):
                run.start()
                self.assertEqual(run.close(), [])
            self.assertEqual(transport.sets, [])

    def test_cleanup_masks_both_signals_and_restores_original_handlers(self):
        with tempfile.TemporaryDirectory() as d:
            run, transport, _logs = self.make(d)
            previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
            handler = lambda _signum, _frame: self.fail("signal escaped cleanup")
            try:
                for signum in previous:
                    signal.signal(signum, handler)
                with mock.patch.object(M.subprocess, "run", side_effect=transport):
                    run.start()
                    transport.interrupt_restore = True
                    self.assertEqual(run.close(), [])
                for signum in previous:
                    self.assertIs(signal.getsignal(signum), handler)
            finally:
                for signum, old in previous.items():
                    signal.signal(signum, old)

    def test_stock_main_propagates_restore_mismatch_to_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as d:
            transport = Transport(d)
            def run_session(session):
                self.addCleanup(session.logf.close)
                session.matched.start()
                transport.restore_mismatch = True
            args = ["--adb", "fake-matched-transport", "--out", d, "--calib", "unused", "--dry-run",
                    "--matched-source", "--launcher-component", NEW_HOME, "--source-viewer", json.dumps(VIEWER),
                    "--ghost-reps", "6", "--recovery-marker", str(Path(d)/"pending.json")]
            with mock.patch.object(M.subprocess, "run", side_effect=transport), \
                    mock.patch.object(S.Stock, "preflight", return_value=None), \
                    mock.patch.object(S.Stock, "run", run_session):
                self.assertEqual(S.main(args), 3)
            self.assertFalse(self.receipt(d)["home_restored"])
            self.assertIn("home restored: no", Path(d, "session.log").read_text())

    def test_actual_stock_signal_handlers_restore_core_state_and_prior_handlers(self):
        for stop_signal in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=stop_signal), tempfile.TemporaryDirectory() as d:
                transport = Transport(d)
                previous = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
                def run_session(session):
                    self.addCleanup(session.logf.close)
                    session.matched.start()
                    transport.interrupt_restore = True
                    signal.raise_signal(stop_signal)
                    self.fail("stock handler did not stop")
                args = ["--adb", "fake-matched-transport", "--out", d, "--calib", "unused", "--dry-run",
                        "--matched-source", "--launcher-component", NEW_HOME, "--source-viewer", json.dumps(VIEWER),
                        "--ghost-reps", "6", "--recovery-marker", str(Path(d)/"pending.json")]
                with mock.patch.object(M.subprocess, "run", side_effect=transport), \
                        mock.patch.object(S.Stock, "preflight", return_value=None), \
                        mock.patch.object(S.Stock, "run", run_session):
                    self.assertEqual(S.main(args), 3)
                self.assertEqual(transport.home, OLD_HOME)
                self.assertEqual(transport.files, {"/data/local/tmp/keep-unrelated.png": b"keep"})
                self.assertTrue(self.receipt(d)["home_restored"])
                self.assertIn("STOP: signal " + signal.Signals(stop_signal).name,
                              Path(d, "session.log").read_text())
                for signum, handler in previous.items():
                    self.assertEqual(signal.getsignal(signum), handler)


@unittest.skipUnless(cv2 is not None, "OpenCV optional; run in cached Nix Python for pixel checks")
class Capture(unittest.TestCase):
    def test_exact_fixture_raster_passes_and_mismatched_raster_fails(self):
        with tempfile.TemporaryDirectory() as d:
            transport, logs = Transport(d), []
            run = M.MatchedRun(arguments(d), ["fake-matched-transport"], d, logs.append, stock=True)
            with mock.patch.object(M.subprocess, "run", side_effect=transport):
                try:
                    run.start()
                    screenshot_dir = Path(d, "runs", "01-A", "probe", "screencap")
                    screenshot_dir.mkdir(parents=True)
                    screenshot = screenshot_dir / "01-source.png"
                    screenshot.write_bytes(Path(run.images["dark"]["path"]).read_bytes())
                    result = run.capture_source("01-A", "dark")
                    self.assertTrue(result["verified"])
                    self.assertEqual(result["pixel_mismatch_fraction"], 0.0)
                    screenshot.write_bytes(Path(run.images["white"]["path"]).read_bytes())
                    with self.assertRaisesRegex(RuntimeError, "not displayed 1:1"):
                        run.capture_source("01-A", "dark")
                finally:
                    self.assertEqual(run.close(), [])

    def test_foreground_mismatch_rejected_for_launcher_and_both_fixtures(self):
        with tempfile.TemporaryDirectory() as d:
            transport = Transport(d)
            run = M.MatchedRun(arguments(d), ["fake-matched-transport"], d, lambda _line: None, stock=True)
            with mock.patch.object(M.subprocess, "run", side_effect=transport):
                try:
                    run.start()
                    screenshot_dir = Path(d, "runs", "01-A", "probe", "screencap")
                    screenshot_dir.mkdir(parents=True)
                    screenshot = screenshot_dir / "01-source.png"
                    transport.foreground = "wrong.app/wrong.app.Error"
                    for sid in ("launcher", "dark", "white"):
                        screenshot.write_bytes(Path(run.images["dark" if sid == "launcher" else sid]["path"]).read_bytes())
                        with self.subTest(source=sid), self.assertRaisesRegex(RuntimeError, "did not resume"):
                            run.capture_source("01-A", sid)
                finally:
                    self.assertEqual(run.close(), [])

    def test_wrong_viewport_rejected_before_pixel_scoring(self):
        with tempfile.TemporaryDirectory() as d:
            run = M.MatchedRun(arguments(d), ["fake-matched-transport"], d, lambda _line: None, stock=False)
            image_dir = Path(d, "runs", "01-A", "probe", "screencap")
            image_dir.mkdir(parents=True)
            original = Path(M.F.write_source(Path(d, "sources"), "dark")["path"])
            image = cv2.imread(str(original))
            cv2.imwrite(str(image_dir / "01-source.png"), cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE))
            with self.assertRaisesRegex(RuntimeError, "not 824x1648"):
                run.capture_source("01-A", "dark")


if __name__ == "__main__":
    unittest.main()
