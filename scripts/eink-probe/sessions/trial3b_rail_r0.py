#!/usr/bin/env python3
"""trial3b-rail-r0: coordinator session script, ONE attended gates-open session that exposes the
rail-fault conditions on purpose and counts the exposure (runs/eink-rail-fault-convert.md, R0).

Plan and decisions: /home/user/A9/runs/eink-rail-diag.md. Run it through trial3b-rail-r0.sh.

What it does (nothing else):
  * runs a SCRIPTED, camera-watched workload through the eink-probe `act` scenario machinery (the
    trial3b-perf session class: persistent shells, telemetry, safety and the 25-minute cap are reused):
    slices of 20 interactions (all-apps swipe, back, settings pages, scrolls, home), one interaction
    every >= 2 s, then a 10 s idle, so full-screen waveforms arrive at a KNOWN density;
  * two blocks of equal length, alternated by --order:
      W  every slice starts with a deliberate FRONTLIGHT EVENT: brightness written down, an idle longer
         than the HV hold (the E Ink session ends), brightness written back, and the first interaction
         follows within ~0.3 s (the fault runs began 0.26-0.67 s after a frontlight write);
      C  the same workload with the frontlight held constant (no brightness write);
  * counts, per slice and per block, from the new a9_eink diagnostics (patch 0052): waveforms
    (diag_pg_end n), full-screen waveforms (full_n), wakes, minutes; reads the since-waveform-end
    histogram, the wf-end PG lows and the shutdown-trace ring at the end;
  * STOPS on the first fault (rail_fault, a rail_faults increase, any safety condition of the
    trial3b-perf session) and, by default, on the first PG low seen at a waveform end (it is not
    a latch, but the next sample would be);
  * reports "0 faults in N full-screen waveforms: 95% upper bound 3/N" and never claims "fixed".
It NEVER writes a gate, a bin, hv_max_ms, hv_hold_ms, fp9929a.*, any a9_eink parameter, a partition,
a module or a service. The only writes are the Settings brightness (and brightness mode) in the W block,
restored (and read back) at the end or on any stop. Gates are left for the coordinator to close.
Phone access is only through the --adb prefix.
"""
import argparse
import datetime
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import trial3b_perf as P  # noqa: E402  (Session, Stop, parsers, telemetry; the probe machinery)

PROBE_DIR = os.path.normpath(os.path.join(HERE, ".."))
DIAG_SH = P.PRE + r"""
c=$(getprop vendor.a9ml.eink.card)
for f in $S/class/drm/$c-*/eink/status; do [ -r "$f" ] && { echo '##status'; cat "$f"; }; break; done
for f in $S/class/drm/$c-*/eink/rail_diag; do [ -r "$f" ] && { echo '##rail_diag'; cat "$f"; }; break; done
echo '##uptime'; rd $R/uptime
"""
BRIGHT_SH = r"""
echo "b=$(settings get system screen_brightness 2>/dev/null)"
echo "m=$(settings get system screen_brightness_mode 2>/dev/null)"
echo "p=$(getprop sys.linevibrator_short)"
"""
# One interaction every >= 2 s. The page changes (settings pages, drawer) are full-screen redraws.
INTERACTIONS = [
    ("drawer_up", ["shell", "input", "swipe", "412", "1500", "412", "500", "150"]),
    ("back", ["shell", "input", "keyevent", "KEYCODE_BACK"]),
    ("settings_main", ["shell", "am", "start", "-a", "android.settings.SETTINGS"]),
    ("settings_display", ["shell", "am", "start", "-a", "android.settings.DISPLAY_SETTINGS"]),
    ("scroll_down", ["shell", "input", "swipe", "412", "1300", "412", "400", "150"]),
    ("home", ["shell", "input", "keyevent", "KEYCODE_HOME"]),
]


# ------------------------------------------------------------------ pure helpers (tested)
def parse_diag(text):
    """eink/status text -> the rail-diag fields. Missing fields are None (an old module has none of them)."""
    d = {"present": False, "end": {}, "hist_n": None, "hist_low": None,
         "rail_faults": None, "rail_fault": None, "updates": None}
    for key in ("rail_faults", "rail_fault", "updates"):
        m = re.search(r"(?<![\w])%s (-?\d+)" % key, text)
        d[key] = int(m.group(1)) if m else None
    m = re.search(r"^diag_pg_end (.*)$", text, re.M)
    if m:
        t = m.group(1).split()
        d["end"] = {t[i]: int(t[i + 1]) for i in range(0, len(t) - 1, 2) if re.fullmatch(r"-?\d+", t[i + 1])}
        d["present"] = True
    m = re.search(r"^diag_settle win_ms (\d+) budget (\d+) n (\d+) ok (\d+) timeout (\d+) skip (\d+) ok_hist\([^)]*\) "
                  r"(\d+) (\d+) (\d+) (\d+) (\d+) max_us (\d+)"
                  r"(?: total_max (\d+) cap_skip (\d+) reread_high (\d+) clock (\w+))?", text, re.M)
    d["settle"] = None
    if m:
        g = [int(x) if x is not None and x.isdigit() else x for x in m.groups()]
        d["settle"] = {"win_ms": g[0], "budget": g[1], "n": g[2], "ok": g[3], "timeout": g[4], "skip": g[5],
                       "hist": g[6:11], "max_us": g[11]}
        if g[12] is not None:      # M4 module (review 89): per-boot cap, F1 counter, durations from the first low reading
            d["settle"].update({"total_max": g[12], "cap_skip": g[13], "reread_high": g[14], "first_clock": g[15] == "first_low"})
    m = re.search(r"^diag_vcom reads (\d+) ok (\d+) mismatch (\d+) err (\d+) last (-?\d+) last_ms (-?\d+)", text, re.M)
    d["vcom"] = dict(zip(("reads", "ok", "mismatch", "err", "last", "last_ms"), map(int, m.groups()))) if m else None
    m = re.search(r"^diag_rate n (\d+) window_s (\d+) burst (\d+) skip (\d+)", text, re.M)
    d["rate"] = dict(zip(("n", "window_s", "burst", "skip"), map(int, m.groups()))) if m else None
    m = re.search(r"diag_hist_n ((?:\d+ ){4}\d+) diag_hist_low ((?:\d+ ){4}\d+)", text)
    if m:
        d["hist_n"] = [int(x) for x in m.group(1).split()]
        d["hist_low"] = [int(x) for x in m.group(2).split()]
    return d


def settle_stops(d0, d, a):
    """Review 89 section 7 stops S1-S5 for a settle-on session, from two rail_diag readings (preflight d0, now d).
    Evaluated at every monitor poll and slice end. Returns a stop reason or None. Pure (tested offline).

    The M4 module ("clock first_low") reports durations from the first low reading; an older module reports them
    about 16 ms short (review 89 F2), so its S1 limit is lowered by --f2-offset-ms and its "long dip" (>= 25 ms from
    the first reading) is approximated by the >= 10 ms reported buckets."""
    s0, s = (d0 or {}).get("settle"), (d or {}).get("settle")
    if not s0 or not s:
        return None
    first = s.get("first_clock", False)
    s1_us = int((a.s1_dip_ms if first else a.s1_dip_ms - a.f2_offset_ms) * 1000)
    if s["max_us"] > s0["max_us"] and s["max_us"] >= s1_us:
        return "S1: a recovered dip lasted %d us (limit %d us, %s clock): review before any further settle-on session" % (
            s["max_us"], s1_us, "first-reading" if first else "settle-entry (old module, F2-corrected limit)")
    lng = (lambda h: h[4]) if first else (lambda h: h[3] + h[4])
    nl = lng(s["hist"]) - lng(s0["hist"])
    if nl >= a.s2_long_dips:
        return "S2: %d recovered dips >= 25 ms (first-reading clock%s) this session (limit %d)" % (
            nl, "" if first else ", approximated by >= 10 ms reported", a.s2_long_dips)
    if s["skip"] > s0["skip"]:
        return "S3: %d budget skip(s): %d recovered dips in one HV session (clustering)" % (s["skip"] - s0["skip"], s["budget"])
    if s.get("cap_skip", 0) > s0.get("cap_skip", 0):
        return "S3: the per-boot settle cap (pg_settle_total_max %s) was used up" % s.get("total_max")
    r0, r = (d0 or {}).get("rate"), (d or {}).get("rate")
    if r0 and r and r["skip"] > r0["skip"]:
        return "S3: %d rate-limit skip(s): more recovered dips than the HV-on token bucket allows (%d per %d s, burst %d)" % (
            r["skip"] - r0["skip"], r["n"], r["window_s"], r["burst"])
    v0, v = (d0 or {}).get("vcom"), (d or {}).get("vcom")
    if v0 and v and (v["mismatch"] > v0["mismatch"] or v["err"] > v0["err"]):
        return "S7: VCOM readback after a recovered dip: mismatch %d -> %d, err %d -> %d (last 0x%02x): the FP9929A may have reset" % (
            v0["mismatch"], v["mismatch"], v0["err"], v["err"], v["last"] & 0xff)
    if s["ok"] >= a.s4_boot_dips:
        return "S4: %d recovered dips this boot (limit %d)" % (s["ok"], a.s4_boot_dips)
    e0, e = (d0 or {}).get("end", {}), (d or {}).get("end", {})
    n, low = e.get("n", 0) - e0.get("n", 0), e.get("low", 0) - e0.get("low", 0)
    if n >= a.s5_min_waveforms and low > a.s5_low_rate * n:
        return "S5: wf-end PG low rate %.2f%% (%d of %d waveforms) above %.2f%% (R0-dense: 0.73%%)" % (
            100.0 * low / n, low, n, 100.0 * a.s5_low_rate)
    return None


TRACE_RE = re.compile(r"^\[\s*(\d+\.\d+)\].*pg-settle trace: reads (\d+) bits ([0-9a-f]+), vblank first (\d+)@(-?\d+) rec (\d+)@(-?\d+),"
                      r" update mode (0x[0-9a-f]+|0) rect (-?\d+),(-?\d+)-(-?\d+),(-?\d+) clear (\d+), VCOM reg 0x([0-9a-f]+) \(programmed 0x([0-9a-f]+), r (-?\d+)\)", re.M)


def parse_traces(text):
    """F6/F6b dmesg trace lines -> dicts (t, reads, bits, frames from the vblank pair, mode, rect, clear, vcom, prog, r)."""
    out = []
    for m in TRACE_RE.finditer(text):
        g = m.groups()
        vf, vr = int(g[3]), int(g[5])
        out.append({"t": float(g[0]), "reads": int(g[1]), "bits": g[2], "frames": (vr - vf) if vr else None,
                    "mode": g[7], "rect": "%s,%s-%s,%s" % g[8:12], "clear": int(g[12]),
                    "vcom": int(g[13], 16), "prog": int(g[14], 16), "r": int(g[15])})
    return out


DIP_RE = re.compile(r"^\[\s*(\d+\.\d+)\].*pg-settle: PG low after a waveform came back in (\d+) us( from the first low reading)?", re.M)
LOW_RE = re.compile(r"^\[\s*(\d+\.\d+)\].*rail-diag: PG low at waveform end.*?session (\d+)", re.M)


def parse_dips(text):
    """dmesg -> per-dip rows: (t_s, reported_us, first_clock, session or None, gap_ms from the 0052 low line or None)."""
    lows = [(float(t), int(sid)) for t, sid in LOW_RE.findall(text)]
    rows = []
    for m in DIP_RE.finditer(text):
        t, us, first = float(m.group(1)), int(m.group(2)), bool(m.group(3))
        prev = [l for l in lows if l[0] <= t]
        sid = prev[-1][1] if prev else None
        gap = round((t - prev[-1][0]) * 1000.0, 1) if prev else None
        rows.append((t, us, first, sid, gap))
    return rows


def parse_ring(text):
    """eink/rail_diag text -> list of (seq, session, (pg0, pg1, pg2), (dt1_us, dt2_us)), newest first."""
    out = []
    for m in re.finditer(r"^pd (\d+) seq (\d+) session (\d+) pg (-?\d+),(-?\d+),(-?\d+) dt_us 0,(-?\d+),(-?\d+)", text, re.M):
        g = [int(x) for x in m.groups()]
        out.append((g[1], g[2], (g[3], g[4], g[5]), (g[6], g[7])))
    return out


def opening_prefix(gates):
    """True when the three gate readbacks (a9_eink.enable, fp9929a.allow_power_on, panel enable) look like an opener part
    way through: some Y and the rest N, never an unknown ('?') value. The opener writes panel, fp9929a, then a9_eink.enable
    (dmesg 'gates OPEN (panel, fp9929a, a9_eink.enable)'), so the states seen are N N N, N N Y, N Y Y, Y Y Y."""
    g = gates.split()
    if len(g) != 3 or any(x not in ("Y", "N", "1", "0") for x in g):
        return False
    on = [x in ("Y", "1") for x in g]            # [enable, allow_power_on, panel]
    return on in ([False, False, False], [False, False, True], [False, True, True])


def upper_bound_95(n):
    """rule of three: with 0 events in n trials the 95% upper bound on the per-trial probability is ~3/n."""
    return None if not n else 3.0 / n


def slice_steps(n_inter, idle_s, wake, dip_level, restore_level, dip_idle_s, first=0):
    """The step list of one slice. Pure; the scenario defaults give >= 2 s between interactions."""
    steps = []
    if wake:
        steps += [{"name": "fl_dip", "adb": ["shell", "settings", "put", "system", "screen_brightness", str(dip_level)],
                   "min_wait_ms": 500, "quiet_ms": 200, "max_wait_ms": 1500},
                  {"wait_ms": int(dip_idle_s * 1000)},
                  # the write-back, followed within ~0.3 s by the first interaction
                  {"name": "fl_wake", "adb": ["shell", "settings", "put", "system", "screen_brightness", str(restore_level)],
                   "min_wait_ms": 0, "quiet_ms": 50, "max_wait_ms": 300}]
    for i in range(n_inter):
        name, argv = INTERACTIONS[(first + i) % len(INTERACTIONS)]
        steps.append({"name": name, "adb": list(argv)})
    steps.append({"wait_ms": int(idle_s * 1000)})
    return {"name": "rail-r0-slice", "min_wait_ms": 2000, "quiet_ms": 700, "max_wait_ms": 4000, "steps": steps}


# Dense block D: back-to-back page changes (each one a full-screen redraw), about one per second, so that 600-900
# full-screen waveforms fit in 12-18 min. Realised full-screen waveforms/min is measured from the kernel counters and
# logged per slice; a slice under --dense-target-full-per-min only logs a warning.
DENSE = [INTERACTIONS[2], INTERACTIONS[3], INTERACTIONS[5]]          # settings_main, settings_display, home


def dense_steps(n_inter, idle_s, min_wait_ms=500, first=0):
    """The step list of one dense slice. Pure. No brightness write, no scroll (a scroll is a partial update)."""
    steps = []
    for i in range(n_inter):
        name, argv = DENSE[(first + i) % len(DENSE)]
        steps.append({"name": name, "adb": list(argv)})
    steps.append({"wait_ms": int(idle_s * 1000)})
    return {"name": "rail-r0-dense", "min_wait_ms": int(min_wait_ms), "quiet_ms": 250, "max_wait_ms": 2500, "steps": steps}


def parse_order(text):
    """'DCW', 'CW', 'D' ... -> the letters, or None when invalid (unknown letter, repeat, empty)."""
    o = text.upper()
    if not o or any(c not in "DCW" for c in o) or len(set(o)) != len(o):
        return None
    return o


# ------------------------------------------------------------------ session
class RailSession(P.Session):
    def __init__(self, a):
        super().__init__(a)
        self.slices = []
        self.blocks = {}
        self.diag0 = None
        self.pg_settle_written = False
        self.log_updates_written = False
        self.bright = {}
        self.wake_armed = False
        self.evid = os.path.join(self.out, "stop-evidence")
        self.rotor = 0

    # ---- counters
    def diag_read(self, phone=None):
        rc, out = (phone or self.plain).sh(DIAG_SH, timeout=30)
        secs = P.parse_sections(out)
        # the counters live in eink/rail_diag (the status page is too full for them); rail_fault etc. stay in status
        d = parse_diag(secs.get("status", "") + "\n" + secs.get("rail_diag", ""))
        d["ring"] = parse_ring(secs.get("rail_diag", ""))
        d["_status"] = secs.get("status", "")
        d["_rail_diag"] = secs.get("rail_diag", "")
        return d

    def safety(self, t, where):
        why = super().safety(t, where)
        if why or self.diag0 is None or t is None or not where.startswith("poll"):
            return why
        # the monitor thread also watches the new counters (each poll: one more read on its own shell)
        return self.diag_safety(self.diag_read(self.mon), where)

    def diag_safety(self, d, where, goal=False):
        """Stop reasons from the new counters (polled with the telemetry). goal=True (slice boundaries only, never the
        monitor polls) also reports the collection goal: a reason starting 'GOAL:' is a successful stop, not a failure."""
        if d is None or not d["present"]:
            return "rail-diag counters unreadable at %s (module without patch 0052?)" % where
        e0, e = self.diag0["end"], d["end"]
        if d["rail_fault"] not in (None, 0) or (self.diag0["rail_faults"] is not None and d["rail_faults"] not in (None, self.diag0["rail_faults"])):
            return "RAIL FAULT: rail_fault=%s rail_faults %s -> %s (%s)" % (d["rail_fault"], self.diag0["rail_faults"], d["rail_faults"], where)
        if self.a.pg_settle_ms and not self.a.no_settle_stops:
            why = settle_stops(self.diag0, d, self.a)
            if why:
                return "%s (%s)" % (why, where)
        if self.a.stop_on_end_low and e.get("low", 0) > e0.get("low", 0):
            return "PG low at a waveform end (not latched; the next sample would be): low %s -> %s (%s)" % (e0.get("low"), e.get("low"), where)
        n = getattr(self.a, "stop_after_dips", 0)
        if goal and n and d.get("settle") and (self.diag0 or {}).get("settle"):
            got = d["settle"]["ok"] - self.diag0["settle"]["ok"]
            if got >= n:
                return "GOAL: collected %d recovered dips (target %d) after %.0f s (%s)" % (got, n, time.monotonic() - self.t0, where)
        return None

    # ---- brightness (the only write of the session)
    def bright_read(self):
        rc, out = self.plain.sh(BRIGHT_SH, 20)
        kv = P.parse_kv(out)
        return {"b": kv.get("b"), "m": kv.get("m"), "p": kv.get("p")}

    def bright_set(self, level, mode):
        script = "settings put system screen_brightness_mode %s; settings put system screen_brightness %s; " % (mode, level)
        rc, _ = self.plain.sh(script, 20)
        now = self.bright_read()
        ok = rc == 0 and now["b"] == str(level) and now["m"] == str(mode)
        self.event("brightness", level=level, mode=mode, ok=ok)
        return ok

    # ---- preflight
    def wait_gates(self):
        """Wait for the opener to finish the panel, FP and a9_eink gate sequence.
        Stop waiting on a fault, closing gate or non-opening readback."""
        end = time.monotonic() + self.a.gate_wait_s
        last = None
        while True:
            t = self.telemetry()
            if t is None:
                return
            gates = t.get("gates", "")
            if gates != last:
                if last is not None or gates.split() not in (["Y", "Y", "Y"], ["1", "1", "1"]):
                    self.log("gates: %r (gate=%r fault=%r)" % (gates, t.get("gate"), t.get("fault")))
                last = gates
            if gates.split() in (["Y", "Y", "Y"], ["1", "1", "1"]):
                return
            opening = (t.get("gate") == "open" and not t.get("fault")
                       and opening_prefix(gates))
            if not opening or time.monotonic() >= end:
                return
            time.sleep(1.0)

    def preflight(self):
        self.wait_gates()
        super().preflight()
        a = self.a
        d = self.diag_read()
        if not d["present"]:
            raise P.Stop("preflight: the eink status has no diag_pg_end line: not the rail-diag module (patch 0052)")
        if d["ring"] is None:
            raise P.Stop("preflight: eink/rail_diag unreadable")
        if a.expect_module_sha:
            rc, out = self.plain.sh("sha256sum ${VENDR:-/vendor}/lib/modules/a9_eink.ko", 30)
            got = out.split()[0] if out.split() else "?"
            if not got.startswith(a.expect_module_sha):
                raise P.Stop("preflight: /vendor/lib/modules/a9_eink.ko is %s, expected %s" % (got[:16], a.expect_module_sha[:16]))
        rc, out = self.plain.sh(P.PRE + 'echo "cb=$(P a9_eink/convert_bounce)"', 20)
        self.orig["convert_bounce"] = P.parse_kv(out).get("cb")
        rc, out = self.plain.sh(P.PRE + 'echo "ps=$(P a9_eink/pg_settle_ms)"', 20)
        self.orig["pg_settle_ms"] = P.parse_kv(out).get("ps")
        if a.pg_settle_ms:
            if self.orig["pg_settle_ms"] in (None, "?", ""):
                raise P.Stop("preflight: --pg-settle-ms needs the pg-settle module (a9_eink.pg_settle_ms parameter not present)")
            if d["settle"] is None:
                raise P.Stop("preflight: eink/rail_diag has no diag_settle line: not the pg-settle module (patch 0053)")
        self.orig["input_diagnostics"] = "?"          # never written by this session
        if a.log_updates:
            rc, out = self.plain.sh(P.PRE + 'echo "lu=$(P a9_eink/log_updates)"', 20)
            self.orig["log_updates"] = P.parse_kv(out).get("lu")
            self.orig["log_updates"] = {"1": "Y", "0": "N"}.get(self.orig["log_updates"], self.orig["log_updates"])
            if self.orig["log_updates"] not in ("Y", "N"):
                raise P.Stop("preflight: --log-updates needs a9_eink.log_updates (read %r)" % self.orig["log_updates"])
        self.diag0 = d
        self.bright = self.bright_read()
        self.orig["brightness"] = self.bright
        if self.bright["b"] in (None, "", "null") or self.bright["m"] in (None, "", "null"):
            raise P.Stop("preflight: cannot read the Settings brightness (%s); the W block needs it" % self.bright)
        json.dump(self.orig, open(os.path.join(self.out, "orig.json"), "w"), indent=1)   # BEFORE any write
        self.log("preflight (rail-r0): diag counters present (wf-end reads %s, full %s, lows %s), convert_bounce=%s, brightness=%s mode=%s" % (
            d["end"].get("n"), d["end"].get("full_n"), d["end"].get("low"), self.orig["convert_bounce"], self.bright["b"], self.bright["m"]))

    def enable_instruments(self):
        self.log("no instrumentation switch to write: the counters are always on (patch 0052)")
        if self.a.pg_settle_ms:
            v = str(int(self.a.pg_settle_ms))
            self.pg_settle_written = True        # revert even if the readback fails
            ok, out = self.write_check("echo %s > $M/a9_eink/parameters/pg_settle_ms; echo \"RB=$(P a9_eink/pg_settle_ms)\"" % v,
                                       "RB=" + v, "pg_settle_ms %s" % v)
            if not ok:
                raise P.Stop("pg_settle_ms write not confirmed by readback (%s)" % out.strip()[:80])
            self.log("pg_settle_ms=%s (was %s): reverted at teardown" % (v, self.orig["pg_settle_ms"]))
        if self.a.log_updates:
            self.log_updates_written = True
            ok, out = self.write_check("echo 1 > $M/a9_eink/parameters/log_updates; echo \"RB=$(P a9_eink/log_updates)\"",
                                       "RB=", "log_updates 1")
            ok = ok and bool(re.search(r"RB=(Y|1)\b", out))       # a bool parameter reads back Y
            if not ok:
                raise P.Stop("log_updates write not confirmed by readback (%s)" % out.strip()[:80])
            self.log("log_updates=Y (was %s): one dmesg line per real update (review 89d); reverted at teardown" % self.orig.get("log_updates"))

    # ---- one slice
    def run_slice(self, block, wake):
        a = self.a
        self.rotor += 1
        tag = "%02d-%s" % (len(self.slices) + 1, block)
        rundir = os.path.join(self.out, "runs", tag)
        os.makedirs(rundir, exist_ok=True)
        if block == "D":
            sc = dense_steps(a.dense_interactions, a.dense_idle_s, a.dense_min_wait_ms,
                             first=(self.rotor * a.dense_interactions) % len(DENSE))
        else:
            sc = slice_steps(a.interactions, a.idle_s, wake, a.dip_level, int(self.bright["b"]), a.dip_idle_s,
                             first=(self.rotor * a.interactions) % len(INTERACTIONS))
        scp = os.path.join(rundir, "scenario.json")
        json.dump(sc, open(scp, "w"), indent=1)
        a.scenario = scp
        before = self.diag_read()
        why = self.diag_safety(before, "before " + tag)
        if why:
            raise P.Stop(why)
        if wake:
            self.wake_armed = True
        t0 = time.monotonic()
        up0 = self.uptime_s()
        rec = {"tag": tag, "block": block, "wake": bool(wake), "t_start": round(t0 - self.t0, 1), "uptime_start": up0}
        err = None
        try:
            self.run_probe(tag, "rail-r0-" + block, "fast")
        except P.Stop as e:
            err = e
        after = self.diag_read()
        rec["duration_s"] = round(time.monotonic() - t0, 1)
        e0, e1 = before["end"], after["end"]
        rec["waveforms"] = e1.get("n", 0) - e0.get("n", 0)
        rec["full_waveforms"] = e1.get("full_n", 0) - e0.get("full_n", 0)
        rec["wf_end_low"] = e1.get("low", 0) - e0.get("low", 0)
        rec["updates"] = (after["updates"] or 0) - (before["updates"] or 0)
        rec["wakes"] = 1 if wake else 0
        last = self.runs[-1] if self.runs and self.runs[-1].get("tag") == tag else {}
        rec["camera_invalid"] = bool(last.get("camera_invalid"))     # optical metrics only; the kernel counters above are unaffected
        rec["rail_faults"] = (after["rail_faults"] or 0) - (before["rail_faults"] or 0)
        self.slices.append(rec)
        json.dump(rec, open(os.path.join(rundir, "rail-r0.json"), "w"), indent=1)
        self.log("   slice %s: %d waveforms (%d full), %d updates, wf-end lows %d, rail faults %d, %.0f s%s" % (
            tag, rec["waveforms"], rec["full_waveforms"], rec["updates"], rec["wf_end_low"], rec["rail_faults"], rec["duration_s"],
            "  [camera warning: this slice's optical metrics are INVALID]" if rec["camera_invalid"] else ""))
        if block == "D" and rec["duration_s"] > 0:
            rec["full_per_min"] = round(rec["full_waveforms"] / (rec["duration_s"] / 60.0), 1)
            if rec["full_per_min"] < a.dense_target_full_per_min:
                self.log("   WARNING: dense slice %s ran %.1f full-screen waveforms/min, below the %.0f target (the session goes on; the "
                         "waveform count in summary.md is what counts)" % (tag, rec["full_per_min"], a.dense_target_full_per_min))
        st = after.get("settle")
        if st and self.diag0 and self.diag0.get("settle"):
            self.log("   pg-settle so far: %d recovered dips, %d timeouts, %d skipped (target %s)" % (
                st["ok"] - self.diag0["settle"]["ok"], st["timeout"] - self.diag0["settle"]["timeout"],
                st["skip"] - self.diag0["settle"]["skip"], a.stop_after_dips or "none"))
        if err:
            raise err
        why = self.diag_safety(after, "after " + tag, goal=True)
        if why:
            raise P.Stop(why)

    def uptime_s(self):
        rc, out = self.plain.sh("read -r u _ < ${PROCR:-/proc}/uptime; echo $u", 15)
        return P.num(out.strip())

    # ---- plan
    def run_all(self):
        a = self.a
        order = parse_order(a.order)
        for blk in order:
            self.blocks[blk] = {"t0": time.monotonic(), "s": 0.0}
        for blk in order:
            wake = blk == "W"
            if wake:
                self.wake_armed = True
                if not self.bright_set(int(self.bright["b"]), 0):     # manual mode, same level, so the dip/wake writes take effect
                    raise P.Stop("could not enter manual brightness mode for the W block")
            blen = a.dense_block_s if (blk == "D" and a.dense_block_s is not None) else a.block_s
            what = {"W": "frontlight event every slice", "C": "frontlight constant",
                    "D": "dense full-screen page changes, frontlight constant"}[blk]
            self.log("BLOCK %s: %s for %.0f s" % (blk, what, blen))
            b0 = time.monotonic()
            self.blocks[blk]["t0"] = b0
            while time.monotonic() - b0 < blen:
                if self.time_left() < self.run_est + 90:
                    self.log("BLOCK %s ends early: %.0f s of session left" % (blk, self.time_left()))
                    break
                try:
                    self.run_slice(blk, wake)
                finally:
                    self.blocks[blk]["s"] = time.monotonic() - b0
            if wake and not self.bright_restore():
                raise P.Stop("brightness restore failed after the W block")

    def bright_restore(self):
        o = self.bright
        if not o:
            return True
        ok = self.bright_set(o["b"], o["m"])
        self.wake_armed = False
        return ok

    # ---- results
    def totals(self, block=None):
        sl = [s for s in self.slices if block in (None, s["block"])]
        t = {"slices": len(sl), "seconds": sum(s["duration_s"] for s in sl), "wakes": sum(s["wakes"] for s in sl),
             "waveforms": sum(s["waveforms"] for s in sl), "full": sum(s["full_waveforms"] for s in sl),
             "updates": sum(s["updates"] for s in sl), "wf_end_low": sum(s["wf_end_low"] for s in sl),
             "rail_faults": sum(s["rail_faults"] for s in sl)}
        t["minutes"] = round(t["seconds"] / 60.0, 1)
        t["full_per_min"] = round(t["full"] / (t["seconds"] / 60.0), 1) if t["seconds"] else None
        return t

    def summary(self):
        tot = self.totals()
        L = ["# trial3b-rail-r0 summary", "",
             "stop reason: %s" % (self.stop_reason or "none (ran to the end of the plan)"), "",
             "| block | slices | minutes | wakes | waveforms | full-screen | full/min | wf-end lows | rail faults |", "|---|---|---|---|---|---|---|---|---|"]
        for blk in ("D", "W", "C"):
            t = self.totals(blk)
            if t["slices"]:
                L.append("| %s | %d | %s | %d | %d | %d | %s | %d | %d |" % (blk, t["slices"], t["minutes"], t["wakes"], t["waveforms"], t["full"], t["full_per_min"], t["wf_end_low"], t["rail_faults"]))
        L.append("| all | %d | %s | %d | %d | %d | %s | %d | %d |" % (tot["slices"], tot["minutes"], tot["wakes"], tot["waveforms"], tot["full"], tot["full_per_min"], tot["wf_end_low"], tot["rail_faults"]))
        L.append("")
        cam = [s.get("camera_invalid") for s in self.slices]
        if any(cam):
            L += ["CAMERA WARNING: %d of %d slice(s) had a stale/interrupted camera stream; their optical metrics (latency, ghost, contrast) are INVALID "
                  "and are not in any median. The kernel counters above do not use the camera." % (sum(1 for c in cam if c), len(cam)), ""]
        goal = getattr(self, "goal", None)
        if goal:
            L += ["**%s**" % goal.replace("GOAL: ", "GOAL REACHED: "), ""]
        fs = (getattr(self, "final_diag", None) or {}).get("settle")
        s0 = (self.diag0 or {}).get("settle")
        if fs and s0 and (self.a.pg_settle_ms or fs["n"]):
            ok, to = fs["ok"] - s0["ok"], fs["timeout"] - s0["timeout"]
            L += ["pg-settle: %d recovered dips, %d timeouts, %d budget skips; recovered-dip duration histogram (<2 <5 <10 <25 >=25 ms) %s, longest %d us" % (
                  ok, to, fs["skip"] - s0["skip"], [a - b for a, b in zip(fs["hist"], s0["hist"])], fs["max_us"]),
                  ("0 timeouts in %d recovered dips: 95%% upper bound on the per-dip timeout probability about 3/d = %s. Not a fix claim." % (
                      ok, "n/a" if not ok else "%.0f%%" % (300.0 / ok))) if not to else
                  "%d TIMEOUT(S) in %d dips: PG did not come back inside the window (a rail fault was latched)." % (to, ok), ""]
        rows = parse_dips(getattr(self, "dips_text", "") or "")
        if rows or self.a.pg_settle_ms:
            L += ["per-dip table (whole boot, from dmesg; review 89 D3). 'reported' is the driver's duration; on a module without the "
                  "M4 clock it is short by the 0052-to-settle gap (F2, about 16 ms), so 'first-reading' adds --f2-offset-ms there. "
                  "HV session age, waveform kind, frontlight and temperatures: from telemetry-poll.jsonl at that time.", "",
                  "| # | dmesg s | reported us | first-reading ms | clock | session | 0052 low -> settle line ms | >= 25 ms | VCOM | trace bits (reads) | frames to recovery | update mode | rect |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
            traces = parse_traces(getattr(self, "dips_text", "") or "")
            for i, (t, us, first, sid, gap) in enumerate(rows, 1):
                fr = us / 1000.0 if first else us / 1000.0 + self.a.f2_offset_ms
                tr = next((x for x in traces if 0 <= x["t"] - t < 1.0), None)     # the trace line follows its dip line
                vc = "-" if tr is None else ("0x%02x/0x%02x%s" % (tr["vcom"], tr["prog"], " MISMATCH" if tr["vcom"] != tr["prog"] else "")
                                             if tr["r"] == 0 else "r %d" % tr["r"])
                L.append("| %d | %.3f | %d | %.1f | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                    i, t, us, fr, "first" if first else "entry+%g" % self.a.f2_offset_ms, sid if sid is not None else "?",
                    gap if gap is not None else "?", "YES" if fr >= 25 else "", vc,
                    "-" if tr is None else "%s (%d)" % (tr["bits"], tr["reads"]),
                    "-" if tr is None or tr["frames"] is None else tr["frames"],
                    "-" if tr is None else tr["mode"], "-" if tr is None else tr["rect"]))
            if not rows:
                L.append("| - | no recovered dip in dmesg | | | | | | | | | | | |")
            fv = (getattr(self, "final_diag", None) or {}).get("vcom")
            if fv:
                L += ["", "VCOM readbacks this boot (F6): %(reads)d, ok %(ok)d, mismatch %(mismatch)d, err %(err)d" % fv]
            if fs and fs.get("reread_high") is not None:
                L.append("")
                L.append("wf-end lows already high on 0053's re-read (F1; not settled, no budget): %d this boot" % fs["reread_high"])
            L.append("")
        ub = upper_bound_95(tot["full"])
        if tot["rail_faults"] == 0:
            L.append("0 rail faults in %d full-screen waveforms (%d waveforms, %d wakes): 95%% upper bound on the per-full-screen-waveform fault probability about 3/N = %s. Not a fix claim." % (
                tot["full"], tot["waveforms"], tot["wakes"], "n/a" if ub is None else "1/%d" % round(1 / ub)))
        else:
            L.append("RAIL FAULT(S): %d, after %d full-screen waveforms and %d wakes; see stop-evidence/." % (tot["rail_faults"], tot["full"], tot["wakes"]))
        if getattr(self, "final_diag", None) and self.diag0:
            d = self.final_diag
            since = {k: d["end"].get(k, 0) - self.diag0["end"].get(k, 0) for k in ("n", "full_n", "low", "err")}
            since["rail_faults"] = (d["rail_faults"] or 0) - (self.diag0["rail_faults"] or 0)
            self.since_preflight = since
            L += ["", "since the preflight reading (whole session, including time between slices): wf-end reads %d, full-screen %d, "
                  "wf-end PG lows %d, read errors %d, rail faults %d" % (since["n"], since["full_n"], since["low"], since["err"], since["rail_faults"])]
            L += ["", "since-waveform-end histogram of checked rail samples (buckets 0-3, 4-15, 16-63, 64+ ms, none): n %s, low %s" % (d["hist_n"], d["hist_low"]),
                  "wf-end PG reads: %s" % d["end"],
                  "pg-settle (a9_eink.pg_settle_ms=%s): %s" % (self.a.pg_settle_ms or "off", d.get("settle")),
                  "shutdown trace ring (newest first, pg entry/vcom/pwrup): %s" % [(r[0], r[2]) for r in d["ring"]]]
        open(os.path.join(self.out, "summary.md"), "w").write("\n".join(L) + "\n")
        json.dump({"slices": self.slices, "totals": tot, "since_preflight": getattr(self, "since_preflight", None)}, open(os.path.join(self.out, "rail-r0.json"), "w"), indent=1)

    def teardown(self):
        """Evidence first (read-only), then the brightness restore, then the shells. No gate, no parameter."""
        failed = []
        try:
            os.makedirs(self.evid, exist_ok=True)
            d = self.diag_read()
            self.final_diag = d
            open(os.path.join(self.evid, "eink-status.txt"), "w").write(d["_status"])
            open(os.path.join(self.evid, "rail_diag.txt"), "w").write(d["_rail_diag"])
            rc, out = self.plain.sh("dmesg 2>/dev/null | tail -n 400", 40)
            open(os.path.join(self.evid, "dmesg-tail.txt"), "w").write(out)
            # every dip of the boot, not only the last 400 lines (review 89 D3: one row per dip)
            rc, out = self.plain.sh("dmesg 2>/dev/null | grep -E 'pg-settle|rail-diag: PG low|rail fault latched|update done'", 40)
            self.dips_text = out
            open(os.path.join(self.out, "dips-dmesg.txt"), "w").write(out)
        except Exception as e:
            failed.append("evidence capture failed: %r" % e)
        try:
            if self.bright and (self.wake_armed or self.bright_read()["b"] != self.bright["b"] or self.bright_read()["m"] != self.bright["m"]):
                if not self.bright_restore():
                    failed.append("brightness is not back at %s (mode %s)" % (self.bright["b"], self.bright["m"]))
        except Exception as e:
            failed.append("brightness restore failed: %r" % e)
        if self.pg_settle_written:
            back = self.orig.get("pg_settle_ms") or "0"
            try:
                ok, out = self.write_check("echo %s > $M/a9_eink/parameters/pg_settle_ms; echo \"RB=$(P a9_eink/pg_settle_ms)\"" % back,
                                           "RB=" + back, "revert pg_settle_ms")
                if not ok:
                    failed.append("pg_settle_ms is not back at %s (%s)" % (back, out.strip()[:60]))
            except Exception as e:
                failed.append("pg_settle_ms revert failed: %r" % e)
        if self.log_updates_written:
            back = "1" if self.orig.get("log_updates") == "Y" else "0"
            try:
                ok, out = self.write_check("echo %s > $M/a9_eink/parameters/log_updates; echo \"RB=$(P a9_eink/log_updates)\"" % back,
                                           "RB=", "revert log_updates")
                ok = ok and bool(re.search(r"RB=(%s)\b" % ("Y|1" if back == "1" else "N|0"), out))
                if not ok:
                    failed.append("log_updates is not back at %s (%s)" % (back, out.strip()[:60]))
            except Exception as e:
                failed.append("log_updates revert failed: %r" % e)
        try:
            self.stop_daemons()
        except Exception:
            pass
        final = None
        try:
            final = self.telemetry()
            json.dump({"telemetry": {k: v for k, v in (final or {}).items() if not k.startswith("_")}, "brightness": self.bright_read()},
                      open(os.path.join(self.out, "final-state.json"), "w"), indent=1)
        except Exception as e:
            failed.append("final state read failed: %r" % e)
        self.event("teardown", failed=failed)
        return failed, final


# ------------------------------------------------------------------ CLI
def plan_text(a):
    return "\n".join([
        "trial3b-rail-r0 plan (nothing is sent to the phone by --plan)",
        " preflight   read-only: gates Y Y Y, bins 0-13, caps, root, rail-diag counters present, module sha (--expect-module-sha)",
        " blocks      %s, %.0f s each (D = dense full-screen page changes, %d per slice, %s s for D; W = frontlight event every slice, C = frontlight constant); %d interactions per slice," % (
            " then ".join(parse_order(a.order) or "?"), a.block_s, a.dense_interactions, "%.0f" % (a.dense_block_s if a.dense_block_s is not None else a.block_s), a.interactions),
        "             one every >= 2 s, then %.0f s idle; W slices start with: brightness -> %d, idle %.0f s (HV hold ends), brightness back, interaction within ~0.3 s" % (a.idle_s, a.dip_level, a.dip_idle_s),
        " goal        %s" % ("stop successfully after %d recovered dips (pg-settle), else the block lengths / session cap" % a.stop_after_dips if a.stop_after_dips else "none (blocks run to their length)"),
        " counters    waveforms / full-screen waveforms / wakes per slice from eink/status (diag_pg_end), faults, wf-end PG lows",
        " stops       first rail fault, first wf-end PG low (--no-stop-on-end-low to keep going; implied by --pg-settle-ms), any trial3b-perf safety condition, adb loss, STOP file; a stale/frozen camera is only a WARNING (user decision 2026-10-17; --stop-on-camera-stale restores the old stop)",
        " settle stops %s" % ("(review 89 S1-S5, every poll) S1 dip >= %g ms from the first low reading; S2 %d dips >= 25 ms; S3 any budget/cap/rate skip; S4 %d recovered dips this boot; S5 wf-end low rate > %g%% after %d waveforms; S7 any VCOM readback mismatch or error" % (
            a.s1_dip_ms, a.s2_long_dips, a.s4_boot_dips, 100 * a.s5_low_rate, a.s5_min_waveforms) if a.pg_settle_ms and not a.no_settle_stops else "off"),
        " budget      %d s from start, hard cap %d s from the gate opening; writes: Settings brightness (restored), only with --pg-settle-ms a9_eink.pg_settle_ms (reverted), only with --log-updates a9_eink.log_updates (reverted); never a gate" % (a.budget_s, P.SESSION_CAP_S),
        " result      summary.md: counts and the 95% upper bound 3/N (no fix claim); evidence in stop-evidence/"])


def revert_only(a):
    """Restore the brightness (and pg_settle_ms, if the session wrote it) recorded in DIR/orig.json. Only that."""
    o = json.load(open(os.path.join(a.revert_only, "orig.json")))
    a.out = a.revert_only
    s = RailSession(a)
    s.bright = o.get("brightness") or {}
    ok = s.bright_restore() if s.bright else True
    if o.get("pg_settle_ms") not in (None, "?", ""):
        back = str(o["pg_settle_ms"])
        r, _ = s.write_check("echo %s > $M/a9_eink/parameters/pg_settle_ms; echo \"RB=$(P a9_eink/pg_settle_ms)\"" % back, "RB=" + back, "revert pg_settle_ms")
        ok = ok and r
    if o.get("log_updates") in ("Y", "N"):
        back = "1" if o["log_updates"] == "Y" else "0"
        r, out = s.write_check("echo %s > $M/a9_eink/parameters/log_updates; echo \"RB=$(P a9_eink/log_updates)\"" % back, "RB=", "revert log_updates")
        ok = ok and r and bool(re.search(r"RB=(%s)\b" % ("Y|1" if back == "1" else "N|0"), out))
    s.log("revert-only %s" % ("OK" if ok else "FAILED: set screen_brightness / screen_brightness_mode by hand"))
    return 0 if ok else 3


def build_parser():
    """the R0 argument parser (also extended by the ghost A/B session, ghost_ab.py)"""
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--adb", help="adb command prefix, e.g. 'adb -s SERIAL' (a fake in --dry-run)")
    ap.add_argument("--out", default=os.path.expanduser("~/A9-build/mainline/eink-rail-diag/r0-%s" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
    ap.add_argument("--calib", help="eink-probe calib.json (camera calibration; needed for the camera-watched settle detection)")
    ap.add_argument("--probe", default=os.path.join(PROBE_DIR, "eink-probe"))
    ap.add_argument("--scenario", default=os.path.join(PROBE_DIR, "scenarios", "perf.json"), help="unused: each slice writes its own scenario")
    ap.add_argument("--url", default="http://127.0.0.1:8093/stream")
    ap.add_argument("--order", default="CW", help="block order: letters D (dense full-screen), C (frontlight constant), W (wakes), each at most once, e.g. DCW, CW, WC")
    ap.add_argument("--dense-block-s", type=float, default=None, help="length of the D block (default: --block-s)")
    ap.add_argument("--dense-interactions", type=int, default=80, help="page changes per D slice (about one per second; the probe analyses the slice afterwards)")
    ap.add_argument("--dense-idle-s", type=float, default=3.0, help="idle at the end of a D slice")
    ap.add_argument("--dense-min-wait-ms", type=int, default=500, help="minimum wait after each D interaction")
    ap.add_argument("--dense-target-full-per-min", type=float, default=40.0, help="a D slice below this only logs a warning")
    ap.add_argument("--stop-after-dips", type=int, default=0, metavar="N",
                    help="stop successfully (rc 0, 'GOAL REACHED') at the first slice boundary where N recovered dips (diag_settle ok) were collected this session; needs --pg-settle-ms. 0 = off")
    ap.add_argument("--block-s", type=float, default=480.0, help="length of each block (default 8 min: 2 blocks + preflight inside the 25 min cap)")
    ap.add_argument("--interactions", type=int, default=20)
    ap.add_argument("--idle-s", type=float, default=10.0)
    ap.add_argument("--dip-level", type=int, default=20, help="Settings screen_brightness written down in the W block (0-255)")
    ap.add_argument("--dip-idle-s", type=float, default=6.0, help="idle after the dip; must exceed hv_hold_ms (3 s) so the E Ink session ends")
    ap.add_argument("--no-stop-on-end-low", dest="stop_on_end_low", action="store_false")
    ap.add_argument("--pg-settle-ms", type=int, default=0, metavar="MS",
                    help="write a9_eink.pg_settle_ms (patch 0053 bounded PG settle, 1..200) at the start and revert it at teardown; "
                         "implies --no-stop-on-end-low (a recovered dip is the point of the run; a timeout latches and stops as a rail fault)")
    ap.add_argument("--log-updates", action="store_true",
                    help="write a9_eink.log_updates=1 for the session (one dmesg line per real update: mode, rect; review 89d) and revert it at teardown")
    ap.add_argument("--no-settle-stops", action="store_true", help="disable the review-89 settle stops S1-S5 (not for a real session)")
    ap.add_argument("--s1-dip-ms", type=float, default=60.0, help="S1: stop on a recovered dip this long from the first low reading")
    ap.add_argument("--f2-offset-ms", type=float, default=15.0,
                    help="old (pre-M4) module: its durations start ~16 ms late (review 89 F2); S1 uses s1-dip-ms minus this")
    ap.add_argument("--s2-long-dips", type=int, default=6, help="S2: stop when this many recovered dips >= 25 ms were seen this session")
    ap.add_argument("--s4-boot-dips", type=int, default=25, help="S4: stop when diag_settle ok (per boot) reaches this")
    ap.add_argument("--s5-low-rate", type=float, default=0.03, help="S5: stop when wf-end PG lows exceed this fraction of waveforms ...")
    ap.add_argument("--s5-min-waveforms", type=int, default=300, help="... after at least this many waveforms this session")
    ap.add_argument("--expect-module-sha", help="sha256 (prefix ok) the phone's /vendor/lib/modules/a9_eink.ko must have")
    ap.add_argument("--gate-wait-s", type=float, default=30.0,
                    help="how long preflight waits for the opener to finish writing the gates (N Y Y -> Y Y Y) while the gate property is open and no fault/stop is published")
    ap.add_argument("--budget-s", type=float, default=1320.0)
    ap.add_argument("--preroll-ms", type=int, default=1500)
    ap.add_argument("--run-timeout-s", type=float, default=240.0)
    ap.add_argument("--poll-s", type=float, default=2.0)
    ap.add_argument("--max-frames-stale-s", type=float, default=12.0,
                    help="no new camera frame for this long = a stale camera: WARNING by default (see --stop-on-camera-stale); 12 s "
                         "because the probe analyses the slice after its capture ends")
    ap.add_argument("--stop-on-camera-stale", action="store_true",
                    help="opt-in: the old behaviour, a stale/absent/unhealthy/frozen camera stops the session (default: warn, mark the run's optical metrics invalid, reconnect)")
    ap.add_argument("--stale-reconnect-s", type=float, default=3.0,
                    help="the probe reopens the camera stream when no frame arrived for this long (0 = never)")
    ap.add_argument("--min-fps", type=float, default=7.5)
    ap.add_argument("--max-temp-c", type=float, default=75.0)
    ap.add_argument("--cam-latency-ms", type=float, default=0.0)
    ap.add_argument("--exclude-actions", type=lambda s: [x for x in s.split(",") if x],
                    default=["home", "fl_dip", "fl_wake"], help="steps that may leave the glass unchanged (brightness writes change no pixel)")
    ap.add_argument("--stop-file", default="/tmp/trial3b-rail-r0.STOP")
    ap.add_argument("--go", action="store_true", help="required for a real phone (the coordinator is attending)")
    ap.add_argument("--dry-run", action="store_true", help="refuse a real adb binary; for the fake phone of the offline tests")
    ap.add_argument("--plan", action="store_true", help="print the plan and exit; sends nothing")
    ap.add_argument("--revert-only", metavar="DIR", help="restore the brightness recorded in DIR/orig.json")
    return ap


def main(argv=None, session_cls=None, parser=None, plan=None):
    ap = parser or build_parser()
    a = ap.parse_args(argv)
    a.out = os.path.abspath(a.out)
    a.trace_s, a.arms, a.reps, a.hvi_ms, a.b0_fast_runs, a.no_trace = 1, [], 0, 8000, 0, True
    if parse_order(a.order) is None:
        ap.error("--order must be distinct letters from D, C, W (e.g. DCW, CW, WC)")
    if a.stop_after_dips and not a.pg_settle_ms:
        ap.error("--stop-after-dips needs --pg-settle-ms (the dips are counted by the settle)")
    if a.pg_settle_ms and not 1 <= a.pg_settle_ms <= 200:
        ap.error("--pg-settle-ms must be 1..200")
    if a.pg_settle_ms:
        a.stop_on_end_low = False
    if a.plan:
        print((plan or plan_text)(a)); return 0
    if not a.adb:
        ap.error("--adb is required")
    if a.dry_run:
        exe = shutil.which(shlex.split(a.adb)[0]) or shlex.split(a.adb)[0]
        if os.path.basename(exe) in ("adb", "adb.exe"):
            print("refusing: --dry-run with a real adb binary", file=sys.stderr); return 2
    elif not a.go:
        print("refusing: this talks to the phone; pass --go (coordinator attending) or use --plan / --dry-run", file=sys.stderr)
        return 2
    if a.revert_only:
        return revert_only(a)
    s = (session_cls or RailSession)(a)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(P.Stop("SIGTERM")))
    s.log("trial3b-rail-r0: out=%s adb='%s' order=%s block=%ds" % (a.out, a.adb, parse_order(a.order), a.block_s))
    try:
        s.preflight()
        s.start_daemons()
        s.enable_instruments()
        s.run_all()
    except P.Stop as e:
        s.stop_reason = str(e)
        if s.stop_reason.startswith("GOAL:"):             # success: the dip target was collected
            s.goal, s.stop_reason = s.stop_reason, None
    except KeyboardInterrupt:
        s.stop_reason = "operator interrupt"
    except Exception as e:                       # any bug must still restore the brightness
        import traceback
        s.stop_reason = "internal error: %r" % (e,)
        traceback.print_exc()
    if getattr(s, "goal", None):
        P.banner([s.goal.replace("GOAL: ", "GOAL REACHED: "), "Brightness is being restored. Gates are NOT touched by this script.",
                  "COORDINATOR: close the gates now (a9ml-eink.sh close)."])
    if s.stop_reason:
        P.banner(["STOP: " + s.stop_reason, "Brightness is being restored. Gates are NOT touched by this script.",
                  "COORDINATOR: look at the glass and at gate/fault, then end the session (a9ml-eink.sh close)."])
    failed, final = s.teardown()
    s.summary()
    if failed:
        P.banner(["TEARDOWN PROBLEM: " + "; ".join(failed), "Fix by hand from %s/orig.json or run --revert-only %s" % (a.out, a.out)])
    s.log("COORDINATOR: close the gates now and verify 000 + HV/PWRUP/VCOM_EN down. Results: %s/summary.md" % a.out)
    return 3 if (s.stop_reason or failed) else 0


if __name__ == "__main__":
    sys.exit(main())
