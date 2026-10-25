#!/usr/bin/env python3
"""ghost-stock: the stock reference for the ghost A/B validation, on the DAILY slot A (stock vendor composer), run
BEFORE the mainline session and separately from it. Read-only apart from transient actions: HOME / Settings activity
starts and the stock composer's own mode-dependent clear (`epd_force_clear`, root). No
Settings, property or partition write; nothing is flashed or rebooted. Opt-in matched-source mode may temporarily
select the explicitly requested default HOME, recording and restoring/verifying its prior component in cleanup;
it also stages and removes only its own temporary source PNG files. No wallpaper or other setting is changed.

Same repetition as ghost_ab.py, same probe/camera layout (runs/NN-A, NN-B, NN-C, stock-reps.json), so
ghost_ab_analyze.py analyzes both:
  A  HOME + epd_force_clear; camera clean check against the baseline (00-A, cleared twice), up to --clean-retries.
  B  am start Settings (warm by default, review 89i C1; --settings-start cold adds -S), wait --post-s (optionally
     epd-snap after it: the composer's software target, read-only). Settings is primed once before the baseline.
  C  epd_force_clear on Settings.
Not held fixed by this script (it must not change the daily settings): the frontlight. It is RECORDED
(sys.linevibrator_short, screen_brightness, mode) before and after every repetition; a repetition whose frontlight
changed is marked and excluded from the comparison by the analysis. The panel temperature (epd_thermal) is recorded.

Awake and unlocked (review 89i C2): the preflight requires mWakefulness=Awake and no keyguard; before every
repetition (and before the preflight check) a transient KEYCODE_WAKEUP and `wm dismiss-keyguard` (the slot-A
lockscreen is swipe-only) are sent and both are checked again. A keyguard still showing (a secure one) stops the
session (rc 3, "unlock the phone"). The legacy session changes no settings (no stay-awake or lock-setting change);
matched mode changes only the explicitly consented preferred HOME and restores it.
Stops: STOP file, --budget-s, two consecutive adb failures, the phone not on slot _a / not booted, asleep or locked.
Exit 0 = ran to the end (or the budget), 3 = stopped.
"""
import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time

import ghost_matched as M

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE = os.path.normpath(os.path.join(HERE, "..", "eink-probe"))
HELPER = os.path.normpath(os.path.join(HERE, "..", "ghost-analyze"))
FC = "/sys/devices/platform/soc/soc:qcom,dsi-display-primary/epd_force_clear"
THERM = "/sys/devices/platform/soc/soc:qcom,dsi-display-primary/epd_thermal"


class Stop(Exception):
    pass


class Stock:
    def __init__(self, a):
        self.a = a
        self.t0 = time.monotonic()
        self.out = os.path.abspath(a.out)
        os.makedirs(os.path.join(self.out, "runs"), exist_ok=True)
        self.logf = open(os.path.join(self.out, "session.log"), "a")
        self.adb = shlex.split(a.adb)
        self.fails = 0
        self.reps = []
        self.ref = None
        self.source_refs = {}
        self.source_baselines = {}
        self.matched = M.MatchedRun(a, self.adb, self.out, self.log, stock=True) if getattr(a, "matched_source", False) else None

    def log(self, m):
        line = "[%6.1f] %s" % (time.monotonic() - self.t0, m)
        print(line, flush=True)
        self.logf.write(line + "\n")
        self.logf.flush()

    def sh(self, cmd, timeout=30):
        try:
            r = subprocess.run(self.adb + ["shell", cmd], capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            r = None
        if r is None or r.returncode != 0:
            self.fails += 1
            if self.fails >= 2:
                raise Stop("adb failed twice in a row (%r)" % cmd[:60])
            return ""
        self.fails = 0
        return r.stdout.strip()

    def check_stop(self):
        if os.path.exists(self.a.stop_file):
            raise Stop("STOP file %s" % self.a.stop_file)
        if time.monotonic() - self.t0 > self.a.budget_s:
            raise Stop("BUDGET")

    def state(self):
        out = self.sh("echo \"fl=$(getprop sys.linevibrator_short) b=$(settings get system screen_brightness) "
                      "m=$(settings get system screen_brightness_mode) th=$(su -c 'cat %s' 2>/dev/null)\"" % self.a.therm_path)
        return dict(kv.split("=", 1) for kv in out.split() if "=" in kv)

    def wake(self):
        """transient: wake the screen and dismiss a swipe-only keyguard (no setting changed; a secure keyguard stays,
        and the check after it stops the session)"""
        self.sh("input keyevent KEYCODE_WAKEUP; sleep 0.5; wm dismiss-keyguard 2>/dev/null; sleep 1; true")

    def awake_unlocked(self, where):
        """review 89i C2: screen awake and no keyguard; Stop (rc 3) otherwise"""
        out = self.sh("echo \"awake=$(dumpsys power | grep -c 'mWakefulness=Awake') "
                      "kg=$(dumpsys window | grep -cE 'mDreamingLockscreen=true|isKeyguardShowing=true|mShowingLockscreen=true')\"")
        kv = dict(x.split("=", 1) for x in out.split() if "=" in x)
        if kv.get("awake", "0") == "0" or kv.get("kg", "1") != "0":
            raise Stop("unlock the phone: screen not awake or keyguard showing at %s (%s)" % (where, out))

    def preflight(self):
        st = self.sh("echo \"slot=$(getprop ro.boot.slot_suffix) bc=$(getprop sys.boot_completed) "
                     "root=$(su -c id 2>/dev/null | grep -c 'uid=0') fc=$(su -c 'ls %s' 2>/dev/null | grep -c force_clear)\"" % self.a.fc_path)
        kv = dict(x.split("=", 1) for x in st.split() if "=" in x)
        if kv.get("slot") != "_a" or kv.get("bc") != "1":
            raise Stop("preflight: not slot _a booted (%s)" % st)
        if kv.get("root") != "1" or kv.get("fc") != "1":
            raise Stop("preflight: no root or no %s (%s)" % (self.a.fc_path, st))
        self.wake()
        self.awake_unlocked("preflight")
        self.s0 = self.state()
        json.dump({"preflight": kv, "state": self.s0, "settings_start": self.a.settings_start},
                  open(os.path.join(self.out, "orig.json"), "w"), indent=1)
        if self.a.epd_snap:          # epd-snap's once-per-boot buffer discovery pulls the shade: do it here, before the baseline
            p = os.path.join(self.out, "epd-snap-discovery.png")
            r = subprocess.run(shlex.split(self.a.epd_snap) + [p], capture_output=True, text=True, timeout=180)
            self.log("epd-snap discovery rc %d" % r.returncode)
        self.log("preflight OK: slot _a, root, epd_force_clear present; frontlight %s brightness %s mode %s thermal %s" % (
            self.s0.get("fl"), self.s0.get("b"), self.s0.get("m"), self.s0.get("th")))

    def clear_step(self, name):
        # adb joins remote argv with spaces. Preserve the root command as one
        # shell word, so the redirection happens inside su rather than as shell.
        command="echo 1 > "+shlex.quote(self.a.fc_path)
        return {"name": name, "adb": ["shell", "su -c "+shlex.quote(command)], "min_wait_ms": 2500, "quiet_ms": 800, "max_wait_ms": 6000}

    def phase(self, tag, steps):
        self.check_stop()
        rundir = os.path.join(self.out, "runs", tag)
        os.makedirs(rundir, exist_ok=True)
        scp = os.path.join(rundir, "scenario.json")
        json.dump({"name": "ghost-stock-" + tag, "min_wait_ms": 1500, "quiet_ms": 600, "max_wait_ms": 6000, "steps": steps}, open(scp, "w"), indent=1)
        cmd = shlex.split(self.a.probe) + ["act", "--adb", self.a.adb, "--scenario", scp, "--out", os.path.join(rundir, "probe"),
                                           "--preroll-ms", str(self.a.preroll_ms), "--url", self.a.url, "--calib", self.a.calib,
                                           "--stale-reconnect-s", "3"]
        with open(os.path.join(rundir, "act.log"), "w") as lf:
            rc = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, timeout=self.a.run_timeout_s).returncode
        if rc not in (0, 4):                 # 4 = camera warning (stale), as in the R0 sessions: not a stop
            raise Stop("probe act %s rc %d" % (tag, rc))
        # The probe's process rc can be zero even when individual actions fail.
        # Neither a failed viewer nor a failed clear is a valid source baseline.
        path=os.path.join(rundir,"probe","session.json")
        try:
            actions=json.load(open(path))["actions"]
            expected=[step for step in steps if "adb" in step]
            if len(actions)!=len(expected):raise ValueError()
            for action,step in zip(actions,expected):
                if action.get("name")!=step["name"] or type(action.get("rc")) is not int:raise ValueError()
                if action["rc"]:
                    raise Stop("probe action %s/%s failed rc=%d; evidence=%s" % (tag,step["name"],action["rc"],path))
        except (OSError,ValueError,KeyError,TypeError):
            raise Stop("probe action results missing or invalid for %s; evidence=%s" % (tag,path))
        return rc

    def check_clean(self, tag):
        if self.ref is None:
            return {"score": 0.0, "clean": True, "ref": True}
        try:
            r = subprocess.run([self.a.ghost_helper, "check-clean", "--calib", self.a.calib, "--ref", self.ref,
                                "--probe", os.path.join(self.out, "runs", tag, "probe"), "--threshold", str(self.a.clean_threshold)],
                               capture_output=True, text=True, timeout=300)
            return json.loads(r.stdout.strip().splitlines()[-1])
        except Exception as e:  # noqa: BLE001
            return {"score": None, "clean": False, "error": repr(e)[:200]}

    def run(self):
        a = self.a
        if self.matched:
            self.matched.start()
            self.matched.probe_viewer()
        # review 89i C1: prime Settings once (fresh activity on its top page), then HOME, before the baseline
        self.sh("am start -S -a android.settings.SETTINGS >/dev/null 2>&1; sleep 3; input keyevent KEYCODE_HOME", 40)
        self.log("primed Settings once (am start -S, 3 s, HOME); phase B starts are %s" % a.settings_start)
        if self.matched:
            for sid in self.matched.sources:
                tag = self.matched.baseline_tag(sid)
                self.phase(tag, [self.matched.source_step(sid), self.clear_step("clear_source"), self.clear_step("clear_source2")])
                self.source_baselines[sid] = self.matched.capture_source(tag, sid)
                self.source_refs[sid] = os.path.join(self.out, "runs", tag, "probe")
            with open(os.path.join(self.out, "source-baselines.json"), "w") as baseline_file:
                json.dump(self.source_baselines, baseline_file, indent=1)
            sequence = self.matched.sequence(a.ghost_reps)
        else:
            self.phase("00-A", [{"name": "home", "adb": ["shell", "input", "keyevent", "KEYCODE_HOME"]},
                                self.clear_step("clear_home"), self.clear_step("clear_home2")])
            self.ref = os.path.join(self.out, "runs", "00-A", "probe")
            sequence = [None] * a.ghost_reps
        for k in range(1, a.ghost_reps + 1):
            if a.budget_s - (time.monotonic() - self.t0) < a.rep_est_s:
                self.log("repetitions end early: budget")
                break
            self.wake()                                    # transient (review 89i C2 + swipe keyguard), then recheck
            self.awake_unlocked("rep %d" % k)
            s_before = self.state()
            rep = {"rep": k, "arm": "stock", "dip": None, "settled": None, "clean_tries": 0, "settings_start": a.settings_start}
            sid = sequence[k - 1]
            if self.matched:
                rep["source_id"] = sid
                self.ref = self.source_refs[sid]
            for t in range(a.clean_retries + 1):
                tag = "%02d-A%s" % (k, "" if t == 0 else str(t))
                show = self.matched.source_step(sid) if self.matched else {"name": "home", "adb": ["shell", "input", "keyevent", "KEYCODE_HOME"]}
                self.phase(tag, ([show] if t == 0 else []) + [self.clear_step("clear_source" if self.matched else "clear_home")])
                rep["clean_tries"] = t + 1
                rep["A"] = {"tag": tag, "camera": self.check_clean(tag)}
                if rep["A"]["camera"].get("clean"):
                    break
            rep["clean"] = bool(rep["A"]["camera"].get("clean"))
            if self.matched:
                rep["source"] = self.matched.capture_source(tag, sid)
            if rep["clean"]:
                self.phase("%02d-B" % k, [{"name": "settings_main", "adb": ["shell", "am", "start"] + (["-S"] if a.settings_start == "cold" else [])
                                           + ["-a", "android.settings.SETTINGS"],
                                           "min_wait_ms": 1500, "quiet_ms": 600, "max_wait_ms": 6000},
                                          {"wait_ms": int(a.post_s * 1000)}])
                if a.epd_snap:
                    p = os.path.join(self.out, "runs", "%02d-B" % k, "epd-snap.png")
                    r = subprocess.run(shlex.split(a.epd_snap) + [p], capture_output=True, text=True, timeout=120)
                    rep["epd_snap"] = {"rc": r.returncode, "png": os.path.relpath(p, self.out) if os.path.exists(p) else None}
                self.phase("%02d-C" % k, [self.clear_step("clear_settings")])
            s_after = self.state()
            rep["frontlight"] = [s_before.get("fl"), s_after.get("fl")]
            rep["frontlight_changed"] = s_before.get("fl") != s_after.get("fl") or s_before.get("b") != s_after.get("b")
            rep["thermal"] = [s_before.get("th"), s_after.get("th")]
            if rep["frontlight_changed"]:
                rep["clean"] = False                  # excluded: the frontlight moved during the repetition
            self.reps.append(rep)
            with open(os.path.join(self.out, "stock-reps.json"), "w") as rep_file:
                json.dump({"reps": self.reps}, rep_file, indent=1)
            self.log("stock rep %d: clean %s after %d, frontlight %s, thermal %s" % (k, rep["clean"], rep["clean_tries"], rep["frontlight"], rep["thermal"]))

    def finish(self):
        failures = []
        if self.matched:
            try:
                failures.extend(self.matched.close())
            except Exception as exc:  # noqa: BLE001
                failures.append("matched-source cleanup failed: %s" % exc)
                self.log("home restored: no (cleanup exception)")
        try:
            subprocess.run(self.adb + ["shell", "input", "keyevent", "KEYCODE_HOME"], timeout=20, capture_output=True)
        except Exception:  # noqa: BLE001
            pass
        with open(os.path.join(self.out, "stock-reps.json"), "w") as rep_file:
            json.dump({"reps": self.reps}, rep_file, indent=1)
        return failures


def main(argv=None):
    ap = argparse.ArgumentParser(description="ghost-stock: stock reference on slot A (read-only apart from transient actions)")
    ap.add_argument("--adb", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--calib", required=True)
    ap.add_argument("--url", default="http://127.0.0.1:8093/stream")
    ap.add_argument("--probe", default=PROBE)
    ap.add_argument("--ghost-helper", default=HELPER)
    ap.add_argument("--ghost-reps", type=int, default=12)
    ap.add_argument("--post-s", type=float, default=3.5)
    ap.add_argument("--settings-start", choices=("warm", "cold"), default="warm",
                    help="phase B: warm `am start` (default, review 89i C1) or cold `am start -S`")
    ap.add_argument("--clean-retries", type=int, default=2)
    ap.add_argument("--clean-threshold", type=float, default=0.02)
    ap.add_argument("--rep-est-s", type=float, default=45.0)
    ap.add_argument("--budget-s", type=float, default=900.0)
    ap.add_argument("--preroll-ms", type=int, default=700)
    ap.add_argument("--run-timeout-s", type=float, default=120.0)
    ap.add_argument("--epd-snap", help="command taking an output PNG path (scripts/epd-snap.sh), run after each B phase")
    ap.add_argument("--stop-file", default="/tmp/ghost-stock.STOP")
    ap.add_argument("--fc-path", default=FC, help="the stock composer's clear node (default: the real one; tests use a fake)")
    ap.add_argument("--therm-path", default=THERM)
    ap.add_argument("--go", action="store_true", help="required unless --dry-run")
    ap.add_argument("--dry-run", action="store_true", help="refuse a real adb binary (offline tests)")
    ap.add_argument("--plan", action="store_true", help="print matched protocol without running any commands")
    M.add_arguments(ap)
    a = ap.parse_args(argv)
    try:
        M.validate(a)
    except ValueError as exc:
        print("refusing: %s" % exc, file=sys.stderr)
        return 2
    if a.plan:
        print("ghost-stock plan: no commands executed")
        if a.matched_source:
            print("record preferred HOME -> set requested Launcher3/Trebuchet -> launcher/dark/white reps via HOME/viewer")
            print("independent double-clear source baselines; warm Settings B; unchanged C and metrics; decoded source + luminance receipts")
            print("finally/signal: restore original HOME, verify and log home restored: yes/no; remove owned PNGs; change no other settings")
        else:
            print("legacy HOME -> warm Settings; stock mode-dependent clear references; no settings changes")
        return 0
    exe = os.path.basename(shlex.split(a.adb)[0])
    if a.dry_run and exe in ("adb", "adb.exe"):
        print("refusing: --dry-run with a real adb", file=sys.stderr)
        return 2
    if not a.dry_run and not a.go:
        print("refusing: pass --go (or --dry-run with a fake adb)", file=sys.stderr)
        return 2
    recovery = M.Recovery.HomeRecovery(a.recovery_marker, shlex.split(a.adb))
    try:
        with recovery:
            if recovery.recover_pending():
                return 3
            a._home_recovery = recovery
            return _run_session(a)
    except Exception as exc:
        print('RECOVERY/SESSION FAILED: %s' % exc, file=sys.stderr)
        return 3


def _run_session(a):
    s = Stock(a)
    rc = 0
    original_handlers = {}
    if s.matched:
        def stop_signal(signum, _frame):
            raise Stop("signal %s" % signal.Signals(signum).name)
        for signum in (signal.SIGINT, signal.SIGTERM):
            original_handlers[signum] = signal.signal(signum, stop_signal)
    try:
        s.preflight()
        s.run()
    except (Stop, RuntimeError, ValueError) as e:
        s.log("STOP: %s" % e)
        rc = 0 if str(e) == "BUDGET" else 3
    finally:
        # Repeated interrupt/termination must not interrupt HOME restoration.
        # SIGKILL or lost transport cannot be repaired here; close reports the
        # verified outcome instead of claiming restoration merely was attempted.
        if s.matched:
            for signum in original_handlers:
                signal.signal(signum, signal.SIG_IGN)
        try:
            failures = s.finish() or []
            if failures:
                for failure in failures:
                    s.log("STOP: %s" % failure)
                rc = 3
        finally:
            for signum, handler in original_handlers.items():
                signal.signal(signum, handler)
    s.log("done: %d repetitions; analyze with ghost-analyze analyze <mainline out> --stock %s" % (len(s.reps), s.out))
    return rc


if __name__ == "__main__":
    sys.exit(main())
