#!/usr/bin/env python3
"""ghost-ab: controlled launcher -> Settings ghosting validation on mainline (review 89h follow-up, "Minimal next
controlled validation"). One gates-open session, N repetitions of the same transition, each from a verified clean
glass, interleaved pg_settle_ms arms. Built on the R0 session (trial3b_rail_r0.RailSession): the same preflight,
telemetry, safety stops (rail fault, settle stops S1/S3/S5/S7, gate/GPU/temperature/bins/parameter drift, adb loss,
STOP file, 25-minute cap), writes and teardown reverts.

One repetition (each phase is one eink-probe `act` run, so the camera frames and the kernel counters of each phase are
separate):
  A  HOME, then force_clear (one GC16 redraw of the home screen), wait.  Kernel check: a logged update with clear 1,
     not aborted, after the write. Camera check: the home frame against the session's reference home frame (the
     baseline's), via the calibration homography and an off-glass exposure patch (ghost_ab_analyze.py check-clean).
     Not clean: one more force_clear (up to --clean-retries), else the repetition is marked unclean and skipped.
  B  `am start -a android.settings.SETTINGS` (review 89i C1: WARM by default, as in the soak; `--settings-start cold`
     adds -S), wait --post-s. Settings is primed once before the baseline (`am start -S`, 3 s, HOME); C leaves it
     on its top page and nothing scrolls, so every warm start shows the same page. This is the measured Smooth transition. Its diag deltas say whether a PG low happened at a
     waveform end (end.low), whether it settled (settle.ok) or was already high on re-read (reread_high).
  C  force_clear on the Settings page (the clean reference of the same page), wait.
The per-repetition ghost score (B's settled frames against C's clean frames, exposure-normalised) is computed
offline by ghost_ab_analyze.py.

Arms: pg_settle_ms values in an ABBA pattern over the repetitions (default "100,0"). 0 is NOT stock's ignore-PG
policy: it is the 0052 behaviour (no settle; a low still present at the next sample latches a rail fault, which
stops the session through the existing stop). Writes, all reverted at teardown: a9_eink.pg_settle_ms,
a9_eink.log_updates, Settings screen_brightness_mode (manual, same level: the frontlight is held fixed) and the
force_clear events. Nothing else.
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import trial3b_rail_r0 as R  # noqa: E402
import ghost_matched as M  # noqa: E402
P = R.P

# one line (the probe hands it to `adb shell` / the persistent injector shell as one argument)
FC_SH = ('S=${S:-/sys}; c=$(getprop vendor.a9ml.eink.card); for f in $S/class/drm/$c-*/eink/force_clear; do '
         '[ -e "$f" ] && { echo 1 > "$f" && echo FC_OK || echo FC_FAIL; }; break; done')
UPD_RE = re.compile(r"^\[\s*(\d+\.\d+)\].*update done: mode (0x[0-9a-f]+) pre (0x[0-9a-f]+|0) clear (\d+) steps (-?\d+) bin (-?\d+) init (\d+) "
                    r"rect (-?\d+),(-?\d+)-(-?\d+),(-?\d+) sent (\d+)", re.M)


def settings_argv(start):
    """phase B's Settings start: warm (default, review 89i C1) or cold (-S, force-stop first)"""
    return ["shell", "am", "start"] + (["-S"] if start == "cold" else []) + ["-a", "android.settings.SETTINGS"]


def arm_sequence(n, arms):
    """ABBA over the arm values: 100,0,0,100,100,0,0,100,..."""
    out = []
    k = 0
    while len(out) < n:
        blk = arms if k % 2 == 0 else list(reversed(arms))
        out.extend(blk)
        k += 1
    return out[:n]


def parse_updates(text, since):
    out = []
    for m in UPD_RE.finditer(text):
        t = float(m.group(1))
        if t >= since:
            out.append({"t": t, "mode": m.group(2), "pre_mode": m.group(3), "init": int(m.group(7)), "clear": int(m.group(4)), "steps": int(m.group(5)), "bin": int(m.group(6)),
                        "rect": "%s,%s-%s,%s" % m.group(8, 9, 10, 11), "sent": int(m.group(12))})
    return out


class GhostSession(R.RailSession):
    def __init__(self, a):
        super().__init__(a)
        self.reps = []
        self.ref_home = None
        self.source_refs = {}
        self.matched = M.MatchedRun(a, self.plain.prefix, self.out, self.log) if getattr(a, "matched_source", False) else None

    def teardown(self):
        failures = []
        if self.matched:
            try:
                failures = self.matched.close()
            except Exception as exc:
                failures = ["matched cleanup failed: %s" % exc]
        inherited, final = super().teardown()
        return failures + inherited, final

    # ---- the frontlight is held fixed for the whole session (manual mode, same level); restored at teardown
    def enable_instruments(self):
        super().enable_instruments()
        self.wake_armed = True
        if not self.bright_set(int(self.bright["b"]), 0):
            raise P.Stop("could not hold the frontlight (manual brightness at the current level)")
        self.log("frontlight held: screen_brightness %s, manual mode (restored at teardown)" % self.bright["b"])

    def set_settle(self, v):
        ok, out = self.write_check("echo %d > $M/a9_eink/parameters/pg_settle_ms; echo \"RB=$(P a9_eink/pg_settle_ms)\"" % v,
                                   "RB=%d" % v, "pg_settle_ms %d" % v)
        if not ok:
            raise P.Stop("pg_settle_ms %d not confirmed by readback (%s)" % (v, out.strip()[:80]))

    def scenario(self, name, steps):
        sc = {"name": name, "min_wait_ms": 1500, "quiet_ms": 600, "max_wait_ms": 6000, "steps": steps}
        return sc

    def clear_step(self, name):
        return {"name": name, "adb": ["shell", FC_SH], "min_wait_ms": 2500, "quiet_ms": 800, "max_wait_ms": 6000}

    def phase(self, rep, tag, steps):
        rundir = os.path.join(self.out, "runs", tag)
        os.makedirs(rundir, exist_ok=True)
        scp = os.path.join(rundir, "scenario.json")
        json.dump(self.scenario("ghost-" + tag, steps), open(scp, "w"), indent=1)
        self.a.scenario = scp
        before = self.diag_read()
        why = self.diag_safety(before, "before " + tag)
        if why:
            raise P.Stop(why)
        clock0 = time.monotonic()
        up0 = self.uptime_s()
        clock1 = time.monotonic()
        self.run_probe(tag, "ghost-" + tag.split("-")[-1], "fast")
        after = self.diag_read()
        rc, dm = self.plain.sh("dmesg 2>/dev/null | grep -E 'update done|pg-settle|rail-diag: PG low' | tail -n 80", 40)
        e0, e1 = before["end"], after["end"]
        s0, s1 = before.get("settle") or {}, after.get("settle") or {}
        rec = {"tag": tag, "uptime_start": up0,
               "clock_anchor": {"device_uptime": up0, "host_mono_before": clock0, "host_mono_after": clock1},
               "waveforms": e1.get("n", 0) - e0.get("n", 0),
               "wf_end_low": e1.get("low", 0) - e0.get("low", 0),
               "settle_ok": s1.get("ok", 0) - s0.get("ok", 0), "settle_timeout": s1.get("timeout", 0) - s0.get("timeout", 0),
               "reread_high": s1.get("reread_high", 0) - s0.get("reread_high", 0),
               "rail_faults": (after["rail_faults"] or 0) - (before["rail_faults"] or 0),
               "updates": parse_updates(dm, up0 or 0.0)}
        json.dump(rec, open(os.path.join(rundir, "phase.json"), "w"), indent=1)
        why = self.diag_safety(after, "after " + tag)
        if why:
            raise P.Stop(why)
        return rec

    def check_clean(self, tag):
        """camera check of phase A's home frame against the reference (offline helper with numpy/cv2)"""
        if self.ref_home is None:
            return {"score": 0.0, "clean": True, "ref": True}
        cmd = [self.a.ghost_helper, "check-clean", "--calib", self.a.calib, "--ref", self.ref_home,
               "--probe", os.path.join(self.out, "runs", tag, "probe"), "--threshold", str(self.a.clean_threshold)]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            j = json.loads(r.stdout.strip().splitlines()[-1])
        except Exception as e:  # noqa: BLE001
            return {"score": None, "clean": False, "error": repr(e)[:200]}
        return j

    def run_all(self):
        a = self.a
        seq = arm_sequence(a.ghost_reps, [int(x) for x in a.ghost_arms.split(",")])
        source_seq = self.matched.sequence(a.ghost_reps) if self.matched else []
        if self.matched:
            self.matched.start()
            self.matched.probe_viewer()
        # review 89i C1: prime Settings once (fresh activity on its top page), then HOME, before the baseline
        self.plain.sh("am start -S -a android.settings.SETTINGS >/dev/null 2>&1; sleep 3; input keyevent KEYCODE_HOME", 40)
        self.log("primed Settings once (am start -S, 3 s, HOME); phase B starts are %s" % a.settings_start)
        # baseline: home, two clears; its last home frame is the clean reference
        self.set_settle(seq[0])
        if self.matched:
            baselines = {}
            for sid in self.matched.sources:
                tag = self.matched.baseline_tag(sid)
                self.phase(0, tag, [self.matched.source_step(sid), self.clear_step("clear_source"), self.clear_step("clear_source2")])
                baselines[sid] = self.matched.capture_source(tag, sid)
                self.source_refs[sid] = os.path.join(self.out, "runs", tag, "probe")
            json.dump(baselines, open(os.path.join(self.out, "source-baselines.json"), "w"), indent=1)
            self.log("baseline: each matched source cleared twice; independent references")
        else:
            self.phase(0, "00-A", [{"name": "home", "adb": ["shell", "input", "keyevent", "KEYCODE_HOME"]},
                                  self.clear_step("clear_home"), self.clear_step("clear_home2")])
            self.ref_home = os.path.join(self.out, "runs", "00-A", "probe")
            self.log("baseline: home cleared twice; reference = runs/00-A")
        for k, arm in enumerate(seq, 1):
            if self.time_left() < a.rep_est_s + 60:
                self.log("repetitions end early: %.0f s of session left" % self.time_left())
                break
            self.set_settle(arm)
            sid = source_seq[k - 1] if self.matched else None
            if self.matched:
                self.ref_home = self.source_refs[sid]
            rep = {"rep": k, "arm": arm, "clean_tries": 0, "settings_start": a.settings_start, "A_lows": 0}
            if self.matched:
                rep["source_id"] = sid
            for t in range(a.clean_retries + 1):
                tag = "%02d-A%s" % (k, "" if t == 0 else str(t))
                source = self.matched.source_step(sid) if self.matched else {"name": "home", "adb": ["shell", "input", "keyevent", "KEYCODE_HOME"]}
                steps = ([source] if t == 0 else []) + [self.clear_step("clear_home")]
                ph = self.phase(k, tag, steps)
                rep["clean_tries"] = t + 1
                clr = [u for u in ph["updates"] if u["clear"] == 1]
                chk = self.check_clean(tag)
                rep["A_lows"] += ph["wf_end_low"]          # a low in a clean-up clear: the rep is excluded from P2 (89i C3)
                rep["A"] = {"tag": tag, "kernel_clear": bool(clr), "clear_update": clr[-1] if clr else None, "camera": chk,
                            "wf_end_low": ph["wf_end_low"]}
                if clr and chk.get("clean"):
                    break
            rep["clean"] = bool(rep["A"]["kernel_clear"] and rep["A"]["camera"].get("clean"))
            if not rep["clean"]:
                self.log("rep %d: glass not verified clean after %d clear(s) (kernel %s, camera %s): skipped" % (
                    k, rep["clean_tries"], rep["A"]["kernel_clear"], rep["A"]["camera"].get("score")))
                self.reps.append(rep)
                continue
            if self.matched:
                rep["source"] = self.matched.capture_source(rep["A"]["tag"], sid)
            b = self.phase(k, "%02d-B" % k, [{"name": "settings_main", "adb": settings_argv(a.settings_start),
                                              "min_wait_ms": 1500, "quiet_ms": 600, "max_wait_ms": 6000},
                                             {"wait_ms": int(a.post_s * 1000)}])
            c = self.phase(k, "%02d-C" % k, [self.clear_step("clear_settings")])
            rep.update({"B": b, "C": c, "dip": b["wf_end_low"] > 0, "settled": b["settle_ok"] > 0, "C_lows": c["wf_end_low"],
                        "bin": [u["bin"] for u in b["updates"]], "modes": [u["mode"] for u in b["updates"]]})
            self.reps.append(rep)
            json.dump(rep, open(os.path.join(self.out, "runs", "%02d-B" % k, "rep.json"), "w"), indent=1)
            self.log("rep %d arm %d: clean after %d, transition lows %d settled %d reread_high %d, updates %s" % (
                k, arm, rep["clean_tries"], b["wf_end_low"], b["settle_ok"], b["reread_high"],
                ",".join("%s/%s" % (u["mode"], u["rect"]) for u in b["updates"])))

    def summary(self):
        super().summary()
        json.dump({"reps": self.reps, "ref_home": self.ref_home}, open(os.path.join(self.out, "ghost-reps.json"), "w"), indent=1)
        L = ["", "## ghost A/B repetitions", "", "| rep | arm | clean (tries) | lows | settled | reread_high | bins | modes |", "|---|---|---|---|---|---|---|---|"]
        for r in self.reps:
            b = r.get("B") or {}
            L.append("| %d | %d | %s (%d) | %s | %s | %s | %s | %s |" % (r["rep"], r["arm"], r["clean"], r["clean_tries"], b.get("wf_end_low", "-"),
                     b.get("settle_ok", "-"), b.get("reread_high", "-"), r.get("bin", "-"), r.get("modes", "-")))
        L += ["", "Ghost scores: ghost_ab_analyze.py analyze %s (offline)." % self.out]
        open(os.path.join(self.out, "summary.md"), "a").write("\n".join(L) + "\n")


def plan_text(a):
    seq = arm_sequence(a.ghost_reps, [int(x) for x in a.ghost_arms.split(",")])
    return "\n".join([
        "MATCHED sources: launcher/dark/white; source-specific double-clear baselines; fixed settle; source PNG staging+cleanup; HOME verified, not changed on mainline" if a.matched_source else "Legacy launcher history (not an exact source match)",
        "ghost-ab plan (nothing is sent to the phone by --plan)",
        " prime       am start -S Settings, 3 s, HOME (once, before the baseline)",
        (" baseline    00-<launcher|dark|white>-A: show source, force_clear x2; per-source camera + decoded-pixel validation"
         if a.matched_source else " baseline    00-A: HOME, force_clear x2 -> the reference home frame"),
        (" per rep     A: matched source + force_clear (kernel + source-specific camera + decoded pixels, up to %d retries) | B: am start %sSettings (%s), %g s |"
         if a.matched_source else " per rep     A: HOME + force_clear (kernel + camera clean check, up to %d retries) | B: am start %sSettings (%s), %g s |") % (a.clean_retries, "-S " if a.settings_start == "cold" else "", a.settings_start, a.post_s),
        " C           force_clear on Settings (unchanged reference operation)",
        " reps        %d, pg_settle_ms arms %s" % (len(seq), seq),
        " writes      a9_eink.pg_settle_ms (arm values; reverted), a9_eink.log_updates (reverted), Settings brightness mode manual at the"
        " same level (restored), force_clear events; never a gate",
        " stops       rail fault/timeout, S1 %g ms, S3, S5, S7, gate/GPU/temp/bins/param drift, adb, STOP file, %d s budget, 25 min cap" % (
            a.s1_dip_ms, a.budget_s)])


def main(argv=None):
    ap = R.build_parser()
    ap.add_argument("--ghost-reps", type=int, default=24, help="repetitions of the launcher -> Settings transition")
    ap.add_argument("--ghost-arms", default="100,0", help="pg_settle_ms values, interleaved ABBA")
    ap.add_argument("--post-s", type=float, default=3.5, help="wait after the Settings transition (persistent residual)")
    ap.add_argument("--settings-start", choices=("warm", "cold"), default="warm",
                    help="phase B: warm `am start` (default, as in the soak; review 89i C1) or cold `am start -S`")
    ap.add_argument("--clean-retries", type=int, default=2)
    ap.add_argument("--clean-threshold", type=float, default=0.02, help="camera clean check: mean |home - reference| (normalised)")
    ap.add_argument("--rep-est-s", type=float, default=45.0, help="time one repetition takes; no repetition starts without it left")
    ap.add_argument("--ghost-helper", default=os.path.join(HERE, "..", "ghost-analyze"))
    M.add_arguments(ap)
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--pg-settle-ms" not in argv:
        argv += ["--pg-settle-ms", "100"]          # settle stops on; the arm writes follow
    try:
        checked = ap.parse_args(argv)
        M.validate(checked)
        if not checked.plan and os.path.exists(checked.recovery_marker):
            ap.error('pending stock HOME recovery: use the host wrapper on slot A before mainline')
    except ValueError as exc:
        ap.error(str(exc))
    return R.main(argv, session_cls=GhostSession, parser=ap, plan=plan_text)


if __name__ == "__main__":
    sys.exit(main())
