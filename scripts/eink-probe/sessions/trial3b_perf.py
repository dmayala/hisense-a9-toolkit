#!/usr/bin/env python3
"""trial3b-perf: coordinator session script, ONE attended gates-open trial3b session that measures
and A/B-tests LOW-RISK runtime knobs for the HWUI/SurfaceFlinger lag (runs/eink-gpu-perf.md).

Plan and rationale: /home/user/A9/runs/eink-perf-session.md. Run it through trial3b-perf.sh.

What it does (nothing else):
  * reads telemetry (gates, E Ink status, params, temperatures) before, during and after
    every probe run, and stops on the first stop condition;
  * F1 instrumentation: gfxinfo framestats, a9_eink input_diagnostics/input_stats, GPU devfreq
    trans_stat/cur_freq/governor, runtime-PM counters, PSI, optional short perfetto trace;
  * A/B arms, one knob at a time, each bracketed by baseline runs of the SAME probe scenario:
      f3  console printk level 3          f5  window/transition animation scale 0
      f2a GPU keep-awake (power/control)  f2m GPU devfreq min_freq = mid OPP
      f2t GPU devfreq min_freq = top OPP  f2c top OPP + keep-awake
      hvi (optional, off unless asked) hv_hold_interactive_ms 8000, the one reviewed HV write
  * reverts every knob at the end or on any stop (and `--revert-only DIR` replays orig.json).
It NEVER writes a gate (a9_eink.enable, fp9929a.allow_power_on, panel enable), a bin, hv_max_ms,
hv_hold_ms or any FP9929A parameter, never closes the gates itself (the coordinator does), and
never touches partitions, modules or services. Phone access is only through the --adb prefix.
"""
import argparse
import datetime
import glob
import json
import os
import re
import shlex
import shutil
import signal
import statistics
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROBE_DIR = os.path.normpath(os.path.join(HERE, ".."))
FASTSHELL = os.path.join(HERE, "fastshell.py")
INT64_MAX = 9223372036854775807
SESSION_CAP_S = 1500          # review 75c: approximately 25 minutes from the opening
KNOWN_ARMS = ["f3", "f5", "f2a", "f2m", "f2t", "f2c", "hvi"]
DEFAULT_ARMS = ["f2m", "f2t", "f3", "f5", "f2a", "f2c"]   # order = value if the budget cuts the tail


class Stop(Exception):
    """A stop condition: revert every knob, tell the coordinator, exit 3."""


# ------------------------------------------------------------------ phone-side snippets
# All reads/writes are POSIX sh (mksh on the phone). `S`/`PROCR` default to the real roots; the
# fake phone of the offline tests points them at a temp tree.
PRE = r"""
S=${S:-/sys}; M=$S/module; R=${PROCR:-/proc}
rd() { v=""; read -r v < "$1" 2>/dev/null && echo "$v" || echo "?"; }
P() { rd $M/${1%%/*}/parameters/${1#*/}; }
"""

TELEM_SH = PRE + r"""
c=$(getprop vendor.a9ml.eink.card)
echo "gate=$(getprop vendor.a9ml.eink.gate)"
echo "fault=$(getprop vendor.a9ml.eink.fault)"
echo "gates=$(P a9_eink/enable) $(P fp9929a/allow_power_on) $(P panel_hisense_hlte556n_eink/enable)"
echo "bins=$(P a9_eink/bin_min)-$(P a9_eink/bin_max)"
for p in a9_eink/hv_max_ms a9_eink/hv_hold_ms a9_eink/hv_hold_interactive_ms a9_eink/init_flash \
         fp9929a/max_on_ms fp9929a/min_off_ms fp9929a/pg_timeout_ms; do
	echo "param.${p#*/}=$(P $p)"
done
st=""; for f in $S/class/drm/$c-*/eink/status; do [ -r "$f" ] || continue
	while read -r l; do st="$st $l"; done < "$f"; break; done
echo "status=$st"
echo "uptime=$(rd $R/uptime)"
"""

COLLECT_SH = PRE + r"""
echo '##id'; id -u
D=""; for d in $S/class/devfreq/*gpu* $S/class/devfreq/*5900000*; do [ -d "$d" ] && { D=$d; break; }; done
G=""; [ -n "$D" ] && G=$(readlink -f $D/device 2>/dev/null)
[ -d "$G/power" ] || G=$S/devices/platform/soc@0/5900000.gpu
echo '##devfreq'
for f in governor cur_freq min_freq max_freq polling_interval available_frequencies; do echo "$f=$(rd $D/$f)"; done
echo '##trans_stat'; cat $D/trans_stat 2>/dev/null
echo '##runtime_pm'
for f in control runtime_status runtime_active_time runtime_suspended_time autosuspend_delay_ms; do echo "$f=$(rd $G/power/$f)"; done
echo '##input_stats'
for f in $S/class/drm/$(getprop vendor.a9ml.eink.card)-*/eink/input_stats; do cat "$f" 2>/dev/null; break; done
echo '##status'
for f in $S/class/drm/$(getprop vendor.a9ml.eink.card)-*/eink/status; do cat "$f" 2>/dev/null; break; done
echo '##psi'
for f in cpu memory io; do echo "$f: $(cat $R/pressure/$f 2>/dev/null | tr '\n' ' ')"; done
echo "loadavg=$(rd $R/loadavg)"
echo '##temps'
for z in $S/class/thermal/thermal_zone*; do [ -r $z/temp ] && echo "temp.${z##*thermal_zone}.$(rd $z/type)=$(rd $z/temp)"; done
echo '##printk'; cat $R/sys/kernel/printk 2>/dev/null
echo '##anim'
for k in window_animation_scale transition_animation_scale animator_duration_scale; do echo "$k=$(settings get global $k 2>/dev/null)"; done
echo '##dmesg_counts'
dm=$(dmesg 2>/dev/null)
echo "eink_fault=$(echo "$dm" | grep -cE 'max_on_ms [0-9]+ expired|emergency off|fault latched|frame-progress fault|dropped out while the pipeline|power-up aborted|a9ml-eink: FAULT')"
echo "gpu_fault=$(echo "$dm" | grep -ciE 'hangcheck|a6xx.*(fault|error)|drm_sched.*(timeout|fail)|iommu.*fault|msm.*gpu.*(hang|fault)|\*ERROR\* .*(gpu|a6xx|adreno)')"
echo "gpu_dmesg=$(echo "$dm" | grep -ciE 'adreno|a6xx|msm_gpu')"
"""

LAUNCHER_SH = r"""
l=$(getprop vendor.a9ml.gpu.launcher)
[ -n "$l" ] || l=$(cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME 2>/dev/null | tail -n1)
l=${l%%/*}
echo "${l:-com.android.launcher3}"
"""

OPEN_AGE_SH = r"""
echo "open=$(dmesg 2>/dev/null | grep 'a9ml-eink: gates OPEN' | tail -n1)"
echo "up=$(read -r u _ < ${PROCR:-/proc}/uptime; echo $u)"
"""

PERFETTO_CFG = """buffers { size_kb: 32768 fill_policy: RING_BUFFER }
duration_ms: %d
data_sources { config { name: "linux.ftrace" ftrace_config {
  ftrace_events: "sched/sched_switch"
  ftrace_events: "sched/sched_wakeup"
  ftrace_events: "sched/sched_waking"
  ftrace_events: "power/cpu_frequency"
  ftrace_events: "power/gpu_frequency"
  ftrace_events: "dma_fence/dma_fence_init"
  ftrace_events: "dma_fence/dma_fence_signaled"
  ftrace_events: "dma_fence/dma_fence_wait_start"
  ftrace_events: "dma_fence/dma_fence_wait_end"
  ftrace_events: "drm/drm_vblank_event"
  ftrace_events: "drm/drm_vblank_event_queued"
  atrace_categories: "gfx"
  atrace_categories: "view"
  atrace_categories: "input"
  atrace_categories: "sm"
  atrace_categories: "wm"
} } }
data_sources { config { name: "linux.process_stats" process_stats_config { scan_all_processes_on_start: true } } }
"""


# ------------------------------------------------------------------ parsers (pure, tested)
def frame_stale_check(frames_dir, last_n, last_new, now, limit):
    """Camera-stale decision of the monitor: (last_n, last_new, reason or None).

    The probe writes frames/index.jsonl exactly once, in Capture.stop(), when the scenario's capture ENDS; the
    report/analysis that follows (contact sheets, tens of seconds for a long slice) adds no frames by design. Once
    that file exists the camera is no longer expected to deliver anything, so the stale clause is off from then on
    (r0-settle100 stopped 8.1 s after a normal capture end, with 1310 frames at a steady 14.97 fps and a 101 ms
    maximum gap). While the capture runs the check is unchanged: every frame is a new file, and no new file for
    `limit` seconds is a stale camera. The rest of the monitor (gates, faults, telemetry) keeps running."""
    if os.path.exists(os.path.join(frames_dir, "index.jsonl")):
        return last_n, now, None
    n = len(os.listdir(frames_dir))
    if n != last_n:
        return n, now, None
    if n > 0 and now - last_new > limit:
        return last_n, last_new, "camera stale: no new frame for %.1f s" % (now - last_new)
    return last_n, last_new, None


def parse_kv(text):
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def parse_sections(text):
    secs, cur = {}, None
    for line in text.splitlines():
        if line.startswith("##"):
            cur = line[2:].strip()
            secs[cur] = []
        elif cur is not None:
            secs[cur].append(line)
    return {k: "\n".join(v) for k, v in secs.items()}


def tokens(text):
    """whitespace-separated key value pairs ("a 1 b 2" over any number of lines) -> dict (status / input_stats)."""
    t = text.split()
    return {t[i]: t[i + 1] for i in range(0, len(t) - 1, 2)}


def num(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def parse_trans_stat(text):
    """devfreq trans_stat -> ({freq_hz: time_ms}, total_transitions) or (None, None)."""
    times, total = {}, None
    for line in text.splitlines():
        s = line.strip().lstrip("*").strip()
        m = re.match(r"^(\d+):(.*)$", s)
        if m:
            vals = m.group(2).split()
            if vals and re.fullmatch(r"\d+", vals[-1]):
                times[int(m.group(1))] = int(vals[-1])
        m = re.match(r"Total transition\s*:\s*(\d+)", s)
        if m:
            total = int(m.group(1))
    return (times or None), total


def pct(vals, p):
    if not vals:
        return None
    v = sorted(vals)
    return v[min(len(v) - 1, int(round(p / 100.0 * (len(v) - 1))))]


def med(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def parse_gfx(text):
    """dumpsys gfxinfo <pkg> [framestats] -> summary dict incl. per-frame derived stats."""
    r = {}
    for key, pat in (("frames", r"Total frames rendered: (\d+)"), ("janky", r"Janky frames: (\d+)"),
                     ("slow_issue", r"Number Slow issue draw commands: (\d+)"),
                     ("slow_ui", r"Number Slow UI thread: (\d+)")):
        m = re.search(pat, text)
        r[key] = int(m.group(1)) if m else None
    for key, pat in (("p50", r"50th percentile: (\d+)ms"), ("p90", r"90th percentile: (\d+)ms"),
                     ("p99", r"99th percentile: (\d+)ms"), ("gpu_p50", r"50th gpu percentile: (\d+)ms"),
                     ("gpu_p90", r"90th gpu percentile: (\d+)ms")):
        m = re.search(pat, text)
        r[key] = int(m.group(1)) if m else None
    rows = []
    m = re.search(r"---PROFILEDATA---\n(.*?)\n---PROFILEDATA---", text, re.S)
    if m:
        lines = [x for x in m.group(1).splitlines() if x.strip()]
        if lines:
            hdr = [h.strip() for h in lines[0].split(",") if h.strip()]
            for ln in lines[1:]:
                f = [x.strip() for x in ln.split(",")][:len(hdr)]
                try:
                    rows.append(dict(zip(hdr, [int(x) for x in f])))
                except ValueError:
                    pass
    if rows:
        ok = [x for x in rows if x.get("Flags", 0) == 0] or rows
        def ms(a, b, x):
            return (x[a] - x[b]) / 1e6 if a in x and b in x and x[a] < INT64_MAX and x[b] < INT64_MAX else None
        tot = [ms("FrameCompleted", "IntendedVsync", x) for x in ok]
        deq = [x["DequeueBufferDuration"] / 1e6 for x in ok if "DequeueBufferDuration" in x]
        iss = [ms("SwapBuffers", "IssueDrawCommandsStart", x) for x in ok]
        swq = [ms("SwapBuffersCompleted", "SwapBuffers", x) for x in ok]
        gpu = [ms("GpuCompleted", "SwapBuffers", x) for x in ok]
        r["fs"] = {"rows": len(rows), "used": len(ok),
                   "gpu_completed_int64max": sum(1 for x in ok if x.get("GpuCompleted") == INT64_MAX),
                   "total_p50": pct([v for v in tot if v is not None], 50), "total_p90": pct([v for v in tot if v is not None], 90),
                   "dequeue_p50": pct(deq, 50), "dequeue_p90": pct(deq, 90),
                   "issue_p50": pct([v for v in iss if v is not None], 50), "issue_p90": pct([v for v in iss if v is not None], 90),
                   "swapq_p50": pct([v for v in swq if v is not None], 50), "swapq_p90": pct([v for v in swq if v is not None], 90),
                   "gpu_after_swap_p50": pct([v for v in gpu if v is not None], 50)}
    return r


def diff_times(a, b):
    """per-frequency delta of two parsed trans_stat time maps -> (delta_ms by freq, share at top)."""
    if not a or not b:
        return None, None
    d = {f: b.get(f, 0) - a.get(f, 0) for f in b}
    tot = sum(d.values())
    top = max(d)
    return d, (d[top] / tot if tot > 0 else None)


def cmp_metric(knob_vals, base_vals, noise_floor_ms=50.0):
    """Compare lists of per-run medians (ms). Returns a dict with a plain-words verdict."""
    k, b = [v for v in knob_vals if v is not None], [v for v in base_vals if v is not None]
    if not k or not b:
        return {"verdict": "no data"}
    kb, bb = statistics.mean(k), statistics.mean(b)
    noise = max(max(b) - min(b), noise_floor_ms)
    d = kb - bb
    v = "within noise" if abs(d) <= noise else ("faster" if d < 0 else "SLOWER")
    return {"knob_ms": round(kb, 1), "base_ms": round(bb, 1), "delta_ms": round(d, 1),
            "delta_pct": round(100.0 * d / bb, 1) if bb else None, "noise_ms": round(noise, 1), "verdict": v,
            "n_knob": len(k), "n_base": len(b)}


# ------------------------------------------------------------------ session
class Phone:
    """adb access. `prefix` is the command prefix (list); every call is `prefix + ['shell', script]`."""

    def __init__(self, prefix, name):
        self.prefix, self.name = prefix, name

    def sh(self, script, timeout=40):
        try:
            p = subprocess.run(self.prefix + ["shell", script], capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "timeout"
        return p.returncode, p.stdout.decode(errors="replace") + p.stderr.decode(errors="replace")


class Session:
    def __init__(self, a):
        self.a = a
        self.t0 = time.monotonic()
        self.out = a.out
        os.makedirs(os.path.join(self.out, "runs"), exist_ok=True)
        self.logf = open(os.path.join(self.out, "session.log"), "a")
        self.evf = open(os.path.join(self.out, "events.jsonl"), "a")
        self.real = a.adb
        self.plain = Phone(shlex.split(a.adb), "plain")
        self.socks = {n: os.path.join(self.out, "fs-%s.sock" % n) for n in ("inj", "mon")}
        self.daemons = {}
        self.orig = {}
        self.expect = {}
        self.applied = {}            # arm -> True while its knob is applied
        self.runs = []               # run records (dicts)
        self.run_est = 75.0
        self.cap_deadline = self.t0 + SESSION_CAP_S
        self.freqs = []
        self.stop_reason = None
        self.has_perfetto = False
        self.gpu_profile = {}
        self.launcher = "com.android.launcher3"
        self.cam_frames_dir = None
        self.poll_log = open(os.path.join(self.out, "telemetry-poll.jsonl"), "a")

    # ---- logging
    def log(self, msg):
        line = "[%6.1f] %s" % (time.monotonic() - self.t0, msg)
        print(line, flush=True)
        self.logf.write(line + "\n"); self.logf.flush()

    def event(self, ev, **kw):
        kw.update({"t": round(time.monotonic() - self.t0, 2), "event": ev})
        self.evf.write(json.dumps(kw) + "\n"); self.evf.flush()

    # ---- time budget
    def time_left(self):
        return min(self.t0 + self.a.budget_s, self.cap_deadline) - time.monotonic()

    # ---- daemons (persistent shells)
    def mkphone(self, name):
        pre = [sys.executable, FASTSHELL, "run", "--sock", self.socks[name], "--real", self.real, "--"]
        return Phone(pre, name)

    def start_daemons(self):
        for n, sock in self.socks.items():
            ready = sock + ".ready"
            if os.path.exists(ready):
                os.unlink(ready)
            self.daemons[n] = subprocess.Popen([sys.executable, FASTSHELL, "serve", "--sock", sock,
                                                "--real", self.real, "--ready-file", ready],
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            end = time.monotonic() + 15
            while not os.path.exists(ready) and time.monotonic() < end and self.daemons[n].poll() is None:
                time.sleep(0.05)
            if not os.path.exists(ready):
                raise Stop("persistent shell daemon %s did not start" % n)
        self.inj, self.mon = self.mkphone("inj"), self.mkphone("mon")
        rc, out = self.inj.sh("echo inj-ok", timeout=15)
        rc2, out2 = self.mon.sh("echo mon-ok", timeout=15)
        if rc or rc2 or "inj-ok" not in out or "mon-ok" not in out2:
            raise Stop("persistent shells do not answer (%r / %r)" % (out, out2))
        self.log("persistent shells up (injector + monitor)")

    def stop_daemons(self):
        for n, sock in self.socks.items():
            subprocess.run([sys.executable, FASTSHELL, "stop", "--sock", sock], capture_output=True, timeout=15)
        for d in self.daemons.values():
            try:
                d.wait(timeout=5)
            except Exception:
                d.kill()

    # ---- telemetry
    def telemetry(self, phone=None):
        rc, out = (phone or self.plain).sh(TELEM_SH, timeout=20)
        if rc != 0 and not out.strip():
            return None
        t = parse_kv(out)
        t["_status"] = tokens(t.get("status", ""))
        return t

    def safety(self, t, where):
        """Return a stop reason or None. t is a telemetry dict."""
        if t is None:
            return "telemetry unreadable (adb lost?) at %s" % where
        e = self.expect
        if t.get("gate") != "open":
            return "gate property is '%s', not open (%s)" % (t.get("gate"), where)
        if t.get("fault"):
            return "E Ink fault latched: %s" % t["fault"]
        g = t.get("gates", "").split()
        if g != ["Y", "Y", "Y"] and g != ["1", "1", "1"]:
            return "gate readback changed: '%s' (want Y Y Y)" % t.get("gates")
        for k in ("bins", "param.hv_max_ms", "param.hv_hold_ms", "param.max_on_ms", "param.min_off_ms",
                  "param.pg_timeout_ms", "param.hv_hold_interactive_ms"):
            if k in e and t.get(k) != e[k]:
                return "E Ink parameter %s changed: %s -> %s" % (k, e[k], t.get(k))
        st = t["_status"]
        for k in ("rail_fault", "stall_fault", "commit_fail", "aborts"):
            if st.get(k) not in (None, "0"):
                return "a9_eink %s = %s" % (k, st.get(k))
        if "rail_faults" in e and st.get("rail_faults") not in (None, e["rail_faults"]):
            return "a9_eink rail_faults increased: %s -> %s" % (e["rail_faults"], st.get("rail_faults"))
        if os.path.exists(self.a.stop_file):
            return "operator STOP file %s present" % self.a.stop_file
        return None

    def temps_over(self, text):
        for line in text.splitlines():
            m = re.match(r"temp\.(\S+)=(-?\d+)$", line.strip())
            if m and int(m.group(2)) >= self.a.max_temp_c * 1000:
                return "temperature zone %s = %.1f C >= %s C" % (m.group(1), int(m.group(2)) / 1000.0, self.a.max_temp_c)
        return None

    def collect(self):
        rc, out = self.plain.sh(COLLECT_SH, timeout=60)
        if rc != 0 and "##id" not in out:
            raise Stop("collect failed (rc %s): %s" % (rc, out[:200]))
        secs = parse_sections(out)
        secs["_raw"] = out
        return secs

    # ---- preflight
    def preflight(self):
        a = self.a
        self.log("preflight (read-only)")
        t = self.telemetry()
        why = self.safety(t, "preflight")
        if why:
            raise Stop("preflight: " + why)
        if t.get("bins") != "0-13":
            raise Stop("preflight: bins are %s, this session is reviewed for 0-13" % t.get("bins"))
        if t.get("param.hv_max_ms") != "15000" or t.get("param.max_on_ms") != "20000":
            raise Stop("preflight: hv_max_ms/max_on_ms are not the reviewed 15000/20000")
        self.expect = {k: t[k] for k in ("bins", "param.hv_max_ms", "param.hv_hold_ms", "param.max_on_ms",
                                         "param.min_off_ms", "param.pg_timeout_ms", "param.hv_hold_interactive_ms") if k in t}
        self.expect["rail_faults"] = t["_status"].get("rail_faults", "0")
        self.event("preflight", telemetry={k: v for k, v in t.items() if not k.startswith("_")})
        c = self.collect()
        if c["id"].strip() != "0":
            raise Stop("preflight: the phone shell is uid %s, not root; sysfs writes would fail" % c["id"].strip())
        over = self.temps_over(c.get("temps", ""))
        if over:
            raise Stop("preflight: " + over)
        dev = parse_kv(c.get("devfreq", ""))
        pm = parse_kv(c.get("runtime_pm", ""))
        anim = parse_kv(c.get("anim", ""))
        printk = c.get("printk", "").split()
        o = self.orig
        o["printk"] = printk
        o["anim"] = {k: anim.get(k, "null") for k in ("window_animation_scale", "transition_animation_scale", "animator_duration_scale")}
        o["devfreq"] = {k: dev.get(k) for k in ("governor", "min_freq", "max_freq", "cur_freq")}
        o["pm"] = {k: pm.get(k) for k in ("control", "autosuspend_delay_ms")}
        o["input_diagnostics"] = None
        rc, out = self.plain.sh(PRE + 'echo "$(P a9_eink/input_diagnostics)"; echo "$(P a9_eink/hv_hold_interactive_ms)"', 20)
        lines = out.split()
        o["input_diagnostics"] = lines[0] if lines else "?"
        o["hv_hold_interactive_ms"] = lines[1] if len(lines) > 1 else "?"
        self.freqs = sorted(int(x) for x in dev.get("available_frequencies", "").split() if x.isdigit())
        o["freqs"] = self.freqs
        o["devfreq_present"] = bool(dev.get("governor") and dev.get("governor") != "?")
        o["pm_present"] = pm.get("control") not in (None, "?")
        o["printk_present"] = len(printk) == 4
        if o["anim"]["animator_duration_scale"] in ("0", "0.0"):
            self.log("WARNING animator_duration_scale is 0 (breaks AOD); f5 will not touch it")
        # perfetto / atrace availability
        rc, out = self.plain.sh("command -v perfetto; command -v atrace; pidof traced traced_probes", 20)
        self.has_perfetto = "perfetto" in out.split("\n")[0] if out.strip() else False
        o["tooling"] = out.strip()
        # launcher package and the session's age (review 75c: ~25 min from the opening)
        self.launcher = (self.plain.sh(LAUNCHER_SH, 30)[1].strip().splitlines() or [self.launcher])[-1]
        kv = parse_kv(self.plain.sh(OPEN_AGE_SH, 30)[1])
        m = re.search(r"\[\s*(\d+\.\d+)\]", kv.get("open", ""))
        up = num(kv.get("up"))
        if m and up:
            age = up - float(m.group(1))
            self.cap_deadline = time.monotonic() + SESSION_CAP_S - max(age, 0)
            self.log("gates were opened %.0f s ago; %.0f s left of the %d s cap" % (age, self.cap_deadline - time.monotonic(), SESSION_CAP_S))
        else:
            self.log("WARNING: could not read when the gates opened; only --budget-s %d bounds the session" % self.a.budget_s)
        if self.time_left() < 240:
            raise Stop("preflight: only %.0f s of session left; not enough for a useful A/B" % self.time_left())
        json.dump(o, open(os.path.join(self.out, "orig.json"), "w"), indent=1)   # BEFORE any write
        self.log("preflight OK: gates Y Y Y, bins 0-13, root, launcher=%s, devfreq=%s pm=%s perfetto=%s opps=%s" % (
            self.launcher, o["devfreq_present"], o["pm_present"], self.has_perfetto, self.freqs))

    # ---- knobs
    def write_check(self, script, want, what):
        rc, out = self.plain.sh(PRE + script, 30)
        ok = rc == 0 and want in out
        self.event("knob", what=what, rc=rc, out=out.strip()[:300], ok=ok)
        return ok, out

    def apply_arm(self, arm):
        o = self.orig
        if arm == "f3":
            if not o["printk_present"]:
                return False
            self.applied[arm] = True
            ok, _ = self.write_check('read a b c d < $R/sys/kernel/printk; echo "3 $b $c $d" > $R/sys/kernel/printk; read a _ < $R/sys/kernel/printk; echo "RB=$a"', "RB=3", "printk 3")
        elif arm == "f5":
            self.applied[arm] = True
            ok, _ = self.write_check("settings put global window_animation_scale 0; settings put global transition_animation_scale 0; "
                                     'echo "RB=$(settings get global window_animation_scale),$(settings get global transition_animation_scale),$(settings get global animator_duration_scale)"',
                                     "RB=0,0,%s" % self.anim_norm(o["anim"]["animator_duration_scale"]), "animation scales 0 (animator untouched)")
        elif arm in ("f2a", "f2m", "f2t", "f2c"):
            ok = self.apply_gpu(arm)
        elif arm == "hvi":
            if o["hv_hold_interactive_ms"] in ("?", None):
                self.log("hvi skipped: hv_hold_interactive_ms parameter not present in this kernel")
                return False
            if o["hv_hold_interactive_ms"] != "0":
                self.log("hvi skipped: hv_hold_interactive_ms is already %s (baseline already has it)" % o["hv_hold_interactive_ms"])
                return False
            v = str(self.a.hvi_ms)
            self.expect["param.hv_hold_interactive_ms"] = v      # the one expected, reviewed change
            self.applied[arm] = True
            ok, _ = self.write_check("echo %s > $M/a9_eink/parameters/hv_hold_interactive_ms; echo \"RB=$(P a9_eink/hv_hold_interactive_ms)\"" % v,
                                     "RB=" + v, "hv_hold_interactive_ms " + v)
            if not ok:
                self.expect["param.hv_hold_interactive_ms"] = o["hv_hold_interactive_ms"]
        else:
            return False
        return ok

    @staticmethod
    def anim_norm(v):
        return "null" if v in (None, "null", "") else v

    def gpu_sh(self, body):
        return ('D=""; for d in $S/class/devfreq/*gpu* $S/class/devfreq/*5900000*; do [ -d "$d" ] && { D=$d; break; }; done\n'
                'G=$(readlink -f $D/device 2>/dev/null); [ -d "$G/power" ] || G=$S/devices/platform/soc@0/5900000.gpu\n' + body)

    def apply_gpu(self, arm):
        o = self.orig
        awake = arm in ("f2a", "f2c")
        clamp = {"f2m": self.mid_freq(), "f2t": self.top_freq(), "f2c": self.top_freq()}.get(arm)
        ok = True
        if awake:
            if not o["pm_present"]:
                self.log("%s: no runtime-PM control file found" % arm); return False
            self.applied[arm] = True
            r, _ = self.write_check(self.gpu_sh('echo on > $G/power/control; echo "RB=$(rd $G/power/control)"'), "RB=on", "gpu power/control on")
            ok = ok and r
        if clamp:
            if not o["devfreq_present"]:
                self.log("%s: no devfreq node found" % arm); return False
            self.applied[arm] = True
            r, out = self.write_check(self.gpu_sh('echo %d > $D/min_freq; echo "RB=$(rd $D/min_freq)"' % clamp), "RB=%d" % clamp, "gpu min_freq %d" % clamp)
            ok = ok and r
        return ok

    def mid_freq(self):
        f = self.freqs
        return f[len(f) // 2] if f else None

    def top_freq(self):
        f = [x for x in self.freqs if x <= int(self.orig["devfreq"]["max_freq"] or 0)] or self.freqs
        return f[-1] if f else None

    def revert_arm(self, arm):
        o = self.orig
        res = True
        if arm == "f3" and o.get("printk_present"):
            res, _ = self.write_check('read a b c d < $R/sys/kernel/printk; echo "%s $b $c $d" > $R/sys/kernel/printk; read a _ < $R/sys/kernel/printk; echo "RB=$a"' % o["printk"][0],
                                      "RB=" + o["printk"][0], "revert printk")
        elif arm == "f5":
            cmds = []
            for k in ("window_animation_scale", "transition_animation_scale"):
                v = o["anim"][k]
                cmds.append("settings delete global %s" % k if v in ("null", "", None) else "settings put global %s %s" % (k, v))
            res, _ = self.write_check("; ".join(cmds) + '; echo "RB=$(settings get global window_animation_scale),$(settings get global transition_animation_scale)"',
                                      "RB=%s,%s" % (self.anim_norm(o["anim"]["window_animation_scale"]), self.anim_norm(o["anim"]["transition_animation_scale"])), "revert animation scales")
        elif arm in ("f2a", "f2m", "f2t", "f2c"):
            if arm in ("f2a", "f2c") and o["pm_present"]:
                r, _ = self.write_check(self.gpu_sh('echo %s > $G/power/control; echo "RB=$(rd $G/power/control)"' % o["pm"]["control"]), "RB=" + o["pm"]["control"], "revert power/control")
                res = res and r
            if arm in ("f2m", "f2t", "f2c") and o["devfreq_present"]:
                r, _ = self.write_check(self.gpu_sh('echo %s > $D/min_freq; echo "RB=$(rd $D/min_freq)"' % o["devfreq"]["min_freq"]), "RB=" + o["devfreq"]["min_freq"], "revert min_freq")
                res = res and r
        elif arm == "hvi":
            v = o["hv_hold_interactive_ms"]
            res, _ = self.write_check("echo %s > $M/a9_eink/parameters/hv_hold_interactive_ms; echo \"RB=$(P a9_eink/hv_hold_interactive_ms)\"" % v, "RB=" + v, "revert hv_hold_interactive_ms")
            self.expect["param.hv_hold_interactive_ms"] = v
        if res:
            self.applied.pop(arm, None)
        return res

    # ---- one probe run
    def run_probe(self, tag, kind, injector, extras=False, trace=False):
        a = self.a
        rundir = os.path.join(self.out, "runs", tag)
        os.makedirs(rundir, exist_ok=True)
        t_start = time.monotonic()
        why = self.safety(self.telemetry(), "before " + tag)
        if why:
            raise Stop(why)
        pre = self.collect()
        self.plain.sh("dumpsys gfxinfo %s reset; dumpsys gfxinfo com.android.settings reset" % self.launcher, 30)
        if trace:
            self.start_trace()
        prefix = "%s %s run --sock %s --real %s --" % (shlex.quote(sys.executable), shlex.quote(FASTSHELL), shlex.quote(self.socks["inj"]), shlex.quote(self.real)) if injector == "fast" else self.real
        cmd = shlex.split(a.probe) + ["act", "--adb", prefix, "--scenario", a.scenario, "--out", os.path.join(rundir, "probe"),
                                      "--preroll-ms", str(a.preroll_ms), "--url", a.url,
                                      "--stale-reconnect-s", str(getattr(a, "stale_reconnect_s", 3.0))]
        if a.calib:
            cmd += ["--calib", a.calib]
        if a.cam_latency_ms:
            cmd += ["--cam-latency-ms", str(a.cam_latency_ms)]
        self.event("run_start", tag=tag, kind=kind, injector=injector)
        self.log("RUN %-14s kind=%-8s injector=%s" % (tag, kind, injector))
        logf = open(os.path.join(rundir, "act.log"), "w")
        proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT)
        trip = {"reason": None}
        self.cam_warn = []          # camera warnings of THIS run (the monitor appends; metrics() marks the optical data invalid)
        stop_ev = threading.Event()
        mon = threading.Thread(target=self.monitor, args=(proc, trip, stop_ev, os.path.join(rundir, "probe", "frames"), extras, rundir), daemon=True)
        mon.start()
        try:
            proc.wait(timeout=a.run_timeout_s)
        except subprocess.TimeoutExpired:
            trip["reason"] = trip["reason"] or "probe run exceeded %s s" % a.run_timeout_s
            proc.kill()
        stop_ev.set(); mon.join(timeout=10)
        logf.close()
        post = self.collect()
        post_t = self.telemetry()
        reason = trip["reason"] or self.safety(post_t, "after " + tag) or self.temps_over(post.get("temps", ""))
        if not reason:
            reason = self.dmesg_delta(pre, post)
        rec = self.metrics(tag, kind, injector, rundir, pre, post, proc.returncode, t_start)
        if trace:
            self.finish_trace(rundir)
        if extras:
            self.extras_after(rundir)
        json.dump(rec, open(os.path.join(rundir, "perf.json"), "w"), indent=1)
        self.runs.append(rec)
        self.run_est = max(self.run_est * 0.5, rec["duration_s"] * 1.15)
        self.log("   -> fc_since_return med %s ms | fc med %s ms | settle med %s ms | gfx p50 %s ms | frames %s | probe rc %s" % (
            rec["fc_ret_med"], rec["fc_med"], rec["settle_med"], rec["gfx"].get("launcher", {}).get("p50"),
            rec["gfx"].get("launcher", {}).get("frames"), proc.returncode))
        self.summary()
        if reason:
            raise Stop(reason)
        # Camera-derived conditions. USER DECISION (2026-10-17, overrides review 80/81 on this point): a stale or frozen camera
        # must not end an E Ink session. Default: warn, mark this run's optical metrics invalid, keep going (the probe reconnects
        # the stream itself). --stop-on-camera-stale restores the old stops. Kernel/telemetry stops (rail fault, PG low, gates,
        # thermal, session length, adb loss) are above and below and are NOT affected.
        cam_stop = getattr(a, "stop_on_camera_stale", False)
        if proc.returncode == 4:
            self.camera_problem(rec, "probe act returned 4 (no camera frames)", cam_stop)
        elif proc.returncode != 0:
            raise Stop("probe act failed rc %s (see %s)" % (proc.returncode, os.path.join(rundir, "act.log")))
        if rec["adb_rc_bad_consecutive"] >= 2:
            raise Stop("adb rc != 0 on two consecutive steps")
        if rec["camera"].get("error") or (rec["camera"].get("fps") or 99) < a.min_fps:
            self.camera_problem(rec, "camera unhealthy: %s" % rec["camera"], cam_stop)
        if rec["no_change_consecutive"] >= 2:
            # a frozen camera frame and a frozen glass look the same to the probe; with the user's decision this is a warning too
            self.camera_problem(rec, "no visible glass change for 2 consecutive actions although the phone did work (camera or panel dead)", cam_stop)

    def camera_problem(self, rec, what, stop):
        """Warn + mark the run's optical metrics invalid; raise only for the opt-in --stop-on-camera-stale."""
        if stop:
            raise Stop("camera stale/absent: " + what if what.startswith("probe act") else what)
        rec["camera_invalid"] = True
        rec.setdefault("camera_notes", []).append(what)
        self.log("   WARNING (camera, not stopping): %s; optical metrics of this run are INVALID" % what)
        self.event("camera_warning", tag=rec.get("tag"), what=what)

    def dmesg_delta(self, pre, post):
        a, b = parse_kv(pre.get("dmesg_counts", "")), parse_kv(post.get("dmesg_counts", ""))
        for k, what in (("eink_fault", "E Ink fault line in dmesg"), ("gpu_fault", "GPU fault line in dmesg")):
            if num(b.get(k), 0) > num(a.get(k), 0):
                return "%s (count %s -> %s)" % (what, a.get(k), b.get(k))
        return None

    def monitor(self, proc, trip, stop_ev, frames_dir, extras, rundir):
        a = self.a
        last_n, last_new, bad = -1, time.monotonic(), 0
        stale_open = False
        started = time.monotonic()
        top_i = 0
        while not stop_ev.is_set() and proc.poll() is None:
            t = self.telemetry(self.mon)
            self.poll_log.write(json.dumps({"t": round(time.monotonic() - self.t0, 2), "gate": t and t.get("gate"), "gates": t and t.get("gates"),
                                            "status": t and t["_status"].get("rail_fault")}) + "\n"); self.poll_log.flush()
            bad = bad + 1 if t is None else 0
            why = self.safety(t, "poll") if (t is not None or bad >= 2) else None
            if not why and os.path.isdir(frames_dir):
                n_before = last_n
                last_n, last_new, cam = frame_stale_check(frames_dir, last_n, last_new, time.monotonic(), a.max_frames_stale_s)
                if cam and getattr(a, "stop_on_camera_stale", False):
                    why = cam                                   # opt-in: the old stop
                elif cam:
                    if not stale_open:                          # one warning per stale episode
                        stale_open = True
                        self.cam_warn.append({"t": round(time.monotonic() - self.t0, 1), "what": cam})
                        self.log("WARNING (camera, not stopping): %s; the probe reconnects the stream, this run's optical metrics will be INVALID" % cam)
                        self.event("camera_stale_warning", what=cam)
                elif last_n != n_before:
                    stale_open = False
            if why:
                trip["reason"] = why
                self.log("STOP CONDITION (monitor): " + why)
                proc.terminate()
                return
            if extras and time.monotonic() - started > 6 + 8 * top_i and top_i < 3:
                rc, out = self.mon.sh("top -b -n1 -H -m 15 2>&1; cat ${PROCR:-/proc}/pressure/cpu ${PROCR:-/proc}/pressure/memory 2>&1", 30)
                open(os.path.join(rundir, "top-%d.txt" % top_i), "w").write(out)
                top_i += 1
            stop_ev.wait(a.poll_s)

    def metrics(self, tag, kind, injector, rundir, pre, post, rc, t_start):
        rec = {"tag": tag, "kind": kind, "injector": injector, "probe_rc": rc, "duration_s": round(time.monotonic() - t_start, 1),
               "fc_ret_med": None, "fc_med": None, "settle_med": None, "inject_ms_med": None, "actions": [], "gfx": {},
               "adb_rc_bad_consecutive": 0, "no_change_consecutive": 0, "camera": {}}
        try:
            rep = json.load(open(os.path.join(rundir, "probe", "report.json")))
            sess = json.load(open(os.path.join(rundir, "probe", "session.json")))
        except (OSError, ValueError):
            rep, sess = None, None
        if rep:
            rec["camera"] = {"fps": rep.get("camera", {}).get("fps"), "error": (sess or {}).get("camera_error"),
                             "reconnects": len((sess or {}).get("camera_reconnects") or []),
                             "stale_gaps": len((sess or {}).get("camera_stale_gaps") or [])}
            inv = {i for i, x in enumerate((sess or {}).get("actions") or []) if x.get("optical_invalid")}
            notes = []
            if rec["camera"]["reconnects"] or rec["camera"]["stale_gaps"]:
                notes.append("camera stream: %d reconnect(s), %d gap(s) > stale threshold" % (rec["camera"]["reconnects"], rec["camera"]["stale_gaps"]))
            if getattr(self, "cam_warn", None):
                notes.append("monitor: %d camera-stale warning(s)" % len(self.cam_warn))
            if notes:
                rec["camera_invalid"] = True
                rec.setdefault("camera_notes", []).extend(notes)
            acts = [x for x in rep["actions"] if x["name"] not in self.a.exclude_actions]
            fc_r, fc, st, inj, bad, nochg, run_bad, run_nochg = [], [], [], [], 0, 0, 0, 0
            for ai, x in enumerate(rep["actions"]):
                tm = x["timing"]
                run_bad = run_bad + 1 if x.get("adb_rc") else 0
                rec["adb_rc_bad_consecutive"] = max(rec["adb_rc_bad_consecutive"], run_bad)
                if x["name"] not in self.a.exclude_actions:
                    run_nochg = run_nochg + 1 if tm.get("first_change_ms") is None else 0
                    rec["no_change_consecutive"] = max(rec["no_change_consecutive"], run_nochg)
                rec["actions"].append({"name": x["name"], "fc_ret": tm.get("first_change_ms_since_adb_return"), "fc": tm.get("first_change_ms"),
                                       "settle": tm.get("settle_ms"), "inject_ms": tm.get("input_return_ms"), "settled": tm.get("settled"),
                                       "ghost": (x.get("quality") or {}).get("ghost_residual"),
                                       "contrast": (x.get("quality") or {}).get("contrast_michelson"),
                                       "optical_invalid": ai in inv})
            use = [x for x in rec["actions"] if x["name"] not in self.a.exclude_actions and not x["optical_invalid"]]
            rec["fc_ret_med"] = rnd(med([x["fc_ret"] for x in use]))
            rec["fc_med"] = rnd(med([x["fc"] for x in use]))
            rec["settle_med"] = rnd(med([x["settle"] for x in use]))
            rec["inject_ms_med"] = rnd(med([x["inject_ms"] for x in use]))
            rec["ghost_med"] = rnd(med([x["ghost"] for x in use]), 3)
            if rec.get("camera_invalid"):
                # the run's optical numbers cannot be trusted: keep them for forensics, publish none
                rec["optical_raw"] = {k: rec[k] for k in ("fc_ret_med", "fc_med", "settle_med", "inject_ms_med", "ghost_med")}
                for k in rec["optical_raw"]:
                    rec[k] = None
                rec["optical_invalid"] = True
        # gfxinfo + kernel-side counters (post only; gfxinfo was reset before the run)
        g_l = self.plain.sh("dumpsys gfxinfo %s framestats" % self.launcher, 40)[1]
        g_s = self.plain.sh("dumpsys gfxinfo com.android.settings framestats", 40)[1]
        open(os.path.join(rundir, "gfxinfo-launcher.txt"), "w").write(g_l)
        open(os.path.join(rundir, "gfxinfo-settings.txt"), "w").write(g_s)
        rec["gfx"] = {"launcher": parse_gfx(g_l), "settings": parse_gfx(g_s)}
        open(os.path.join(rundir, "pre.txt"), "w").write(pre["_raw"])
        open(os.path.join(rundir, "post.txt"), "w").write(post["_raw"])
        ta, tb = parse_trans_stat(pre.get("trans_stat", ""))[0], parse_trans_stat(post.get("trans_stat", ""))[0]
        d, share_top = diff_times(ta, tb)
        rec["gpu"] = {"cur_freq": parse_kv(post.get("devfreq", "")).get("cur_freq"), "gov": parse_kv(post.get("devfreq", "")).get("governor"),
                      "time_ms_by_freq": d, "share_top": share_top,
                      "transitions": (parse_trans_stat(post.get("trans_stat", ""))[1] or 0) - (parse_trans_stat(pre.get("trans_stat", ""))[1] or 0)}
        pa, pb = parse_kv(pre.get("runtime_pm", "")), parse_kv(post.get("runtime_pm", ""))
        rec["gpu"]["suspended_ms"] = diff_num(pa.get("runtime_suspended_time"), pb.get("runtime_suspended_time"))
        rec["gpu"]["active_ms"] = diff_num(pa.get("runtime_active_time"), pb.get("runtime_active_time"))
        ia, ib = tokens(pre.get("input_stats", "")), tokens(post.get("input_stats", ""))
        samples = (num(ib.get("samples"), 0) - num(ia.get("samples"), 0))
        ins = {"commits": samples, "full": diff_num(ia.get("full"), ib.get("full")), "partial": diff_num(ia.get("partial"), ib.get("partial")),
               "failures": diff_num(ia.get("failures"), ib.get("failures"))}
        for k in ("cpu_begin", "shadow_wait", "convert", "cpu_end"):
            dt = diff_num(ia.get(k + "_total_ns"), ib.get(k + "_total_ns"))
            ins[k + "_mean_ms"] = rnd(dt / samples / 1e6, 3) if dt is not None and samples else None
            ins[k + "_max_ms_cumulative"] = rnd(num(ib.get(k + "_max_ns"), 0) / 1e6, 3) if ib.get(k + "_max_ns") else None
        if num(ib.get("fences"), 0):
            ins["fence_mean_ms"] = rnd(diff_num(ia.get("fence_total_ns"), ib.get("fence_total_ns")) / max(num(ib.get("fences"), 0) - num(ia.get("fences"), 0), 1) / 1e6, 3)
        rec["input_stats"] = ins
        sa, sb = tokens(pre.get("status", "")), tokens(post.get("status", ""))
        rec["eink"] = {"updates": diff_num(sa.get("updates"), sb.get("updates")), "frames": diff_num(sa.get("frames"), sb.get("frames")),
                       "commit_max_us": sb.get("commit_max_us"), "commit_slow": diff_num(sa.get("commit_slow"), sb.get("commit_slow")),
                       "underruns": sb.get("underruns"), "late_drive": sb.get("late_drive"), "max_gap_drive_us": sb.get("max_gap_drive_us")}
        rec["psi"] = post.get("psi", "")
        rec["max_temp_c"] = max([int(v) / 1000.0 for k, v in parse_kv(post.get("temps", "")).items() if re.fullmatch(r"-?\d+", v)] or [None]) if post.get("temps") else None
        return rec

    # ---- perfetto + extras
    def start_trace(self):
        if not self.has_perfetto:
            self.log("perfetto not available on this image: trace run skipped (extras still collected)")
            self.tracing = False
            return
        cfg = PERFETTO_CFG % (self.a.trace_s * 1000)
        script = ("cat > /data/local/tmp/eink-perf.cfg <<'CFG'\n" + cfg + "CFG\n"
                  "rm -f /data/local/tmp/eink-perf.pftrace\n"
                  "(perfetto --txt -c /data/local/tmp/eink-perf.cfg -o /data/local/tmp/eink-perf.pftrace >/data/local/tmp/eink-perf.log 2>&1 &)\n"
                  "sleep 1; pidof perfetto || echo no-perfetto-process")
        rc, out = self.plain.sh(script, 40)
        self.tracing = rc == 0 and "no-perfetto-process" not in out
        self.event("trace_start", ok=self.tracing, out=out.strip()[:200])
        self.log("perfetto trace %s (%d s, 32 MB ring)" % ("started" if self.tracing else "did NOT start: " + out.strip()[:120], self.a.trace_s))

    def finish_trace(self, rundir):
        if not getattr(self, "tracing", False):
            return
        for _ in range(self.a.trace_s + 10):
            if not self.plain.sh("pidof perfetto", 15)[1].strip():
                break
            time.sleep(1)
        dst = os.path.join(rundir, "eink-perf.pftrace")
        p = subprocess.run(self.plain.prefix + ["pull", "/data/local/tmp/eink-perf.pftrace", dst], capture_output=True, timeout=120)
        self.plain.sh("rm -f /data/local/tmp/eink-perf.pftrace /data/local/tmp/eink-perf.cfg /data/local/tmp/eink-perf.log", 20)
        self.event("trace_done", pulled=os.path.exists(dst), size=os.path.getsize(dst) if os.path.exists(dst) else 0, rc=p.returncode)
        self.log("trace pulled to %s (%s bytes); phone copy removed" % (dst, os.path.getsize(dst) if os.path.exists(dst) else "none"))

    def extras_after(self, rundir):
        rc, out = self.plain.sh("dumpsys SurfaceFlinger 2>&1", 60)
        open(os.path.join(rundir, "sf-dump.txt"), "w").write(out)
        keep = "\n".join(l for l in out.splitlines() if re.search(r"refresh|vsync|composition|client|GPU|Buffer|latency", l, re.I))[:20000]
        open(os.path.join(rundir, "sf-dump-filtered.txt"), "w").write(keep)

    # ---- instrumentation switch
    def enable_instruments(self):
        o = self.orig
        if o.get("input_diagnostics") in ("N", "0", "n"):
            ok, out = self.write_check("echo 1 > $M/a9_eink/parameters/input_diagnostics; echo \"RB=$(P a9_eink/input_diagnostics)\"", "RB=", "input_diagnostics on")
            rb = out.strip().split("=")[-1]
            self.applied["instr"] = rb in ("Y", "1")
            self.log("a9_eink.input_diagnostics -> %s (budget: 4096 commits per device lifetime, no reset)" % rb)
        elif o.get("input_diagnostics") in ("Y", "1"):
            self.log("input_diagnostics already on")
        else:
            self.log("input_diagnostics parameter absent: no input_stats (kernel lacks patch 0047)")

    # ---- plan
    def run_all(self):
        a = self.a
        arms = [x for x in a.arms if x in KNOWN_ARMS]
        n = [0]

        def nm(prefix):
            n[0] += 1
            return "%02d-%s" % (n[0], prefix)

        def need(runs):
            return runs * self.run_est + 60
        # B0: injector comparison (F7) then the real baseline
        if self.time_left() < need(3):
            raise Stop("not enough session time for the first baselines")
        self.run_probe(nm("B0-plain"), "baseline", "plain")
        for _ in range(a.b0_fast_runs):
            self.run_probe(nm("B0-fast"), "baseline", "fast")
        self.profile_gpu()
        if not a.no_trace and self.time_left() > need(1) + 30:
            self.run_probe(nm("T1-trace"), "trace", "fast", extras=True, trace=True)
        last_was_base = False
        for arm in arms:
            skip = self.skip_reason(arm)
            if skip:
                self.log("ARM %s skipped: %s" % (arm, skip)); self.event("arm_skipped", arm=arm, why=skip); continue
            if self.time_left() < need(a.reps + 1):
                self.log("ARM %s skipped: %.0f s left" % (arm, self.time_left())); self.event("arm_skipped", arm=arm, why="budget"); continue
            self.log("ARM %s: apply" % arm)
            self.event("apply", arm=arm)
            if not self.apply_arm(arm):
                self.log("ARM %s: knob not applied/verified; reverting and skipping" % arm)
                if arm in self.applied and not self.revert_arm(arm):
                    raise Stop("revert of %s failed" % arm)
                continue
            try:
                for i in range(a.reps):
                    self.run_probe(nm("%s-%d" % (arm, i + 1)), "arm:" + arm, "fast")
            finally:
                self.event("revert", arm=arm)
                if not self.revert_arm(arm):
                    raise Stop("revert of %s FAILED (see events.jsonl); revert by hand from orig.json" % arm)
            self.log("ARM %s: reverted; baseline run" % arm)
            self.run_probe(nm("B-after-%s" % arm), "baseline", "fast")
            last_was_base = True
        if not last_was_base and self.time_left() > need(1):
            self.run_probe(nm("B-final"), "baseline", "fast")

    def profile_gpu(self):
        fast = [r for r in self.runs if r["kind"] == "baseline" and r["injector"] == "fast"]
        sh = [r["gpu"]["share_top"] for r in fast if r["gpu"].get("share_top") is not None]
        susp = [r["gpu"]["suspended_ms"] for r in fast if r["gpu"].get("suspended_ms") is not None]
        self.gpu_profile = {"share_top": statistics.mean(sh) if sh else None, "suspended_ms": sum(susp) if susp else None}
        self.log("GPU profile from baselines: share of time at top OPP %s, runtime-suspended %s ms" % (
            "n/a" if not sh else "%.0f%%" % (100 * self.gpu_profile["share_top"]), self.gpu_profile["suspended_ms"]))

    def skip_reason(self, arm):
        o, gp = self.orig, self.gpu_profile
        if arm in ("f2m", "f2t", "f2c") and not o["devfreq_present"]:
            return "no GPU devfreq node"
        if arm in ("f2a", "f2c") and not o["pm_present"]:
            return "no GPU runtime-PM node"
        if arm == "f2a" and gp.get("suspended_ms") is not None and gp["suspended_ms"] < 100:
            return "runtime PM never suspended the GPU during the baselines (keep-awake cannot help)"
        if arm in ("f2m", "f2t", "f2c") and gp.get("share_top") is not None and gp["share_top"] >= 0.9:
            return "GPU already ran >= 90%% of the time at its top OPP (clamp moot)"
        if arm == "f2m" and self.mid_freq() == self.top_freq():
            return "mid OPP equals top OPP"
        if arm == "f5" and o["anim"]["animator_duration_scale"] in ("0", "0.0"):
            return "animator_duration_scale is 0 (AOD hazard): refuse to run animation arm"
        return None

    # ---- summary
    def summary(self):
        runs = self.runs
        lines = ["# trial3b-perf summary (%s)" % datetime.datetime.now().isoformat(timespec="seconds"), "",
                 "Metric = median over the scenario actions (excluding %s) per run; ms. since-return = after the adb/shell command returned (F7 headline)." % ", ".join(self.a.exclude_actions), "",
                 "| run | kind | inj | first change since return | first change since send | settle | inject ms | gfx p50 | slow issue | dequeue p50 | GPU top share | GPU susp ms | convert ms/commit | updates |",
                 "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in runs:
            g = r["gfx"].get("launcher", {}); fs = g.get("fs", {}); ins = r.get("input_stats", {})
            lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                r["tag"], r["kind"], r["injector"], r["fc_ret_med"], r["fc_med"], r["settle_med"], r["inject_ms_med"], g.get("p50"), g.get("slow_issue"),
                rnd(fs.get("dequeue_p50")), fmtp(r["gpu"].get("share_top")), r["gpu"].get("suspended_ms"), ins.get("convert_mean_ms"), r["eink"].get("updates")))
        comps = self.compare()
        lines += ["", "## A/B (knob runs vs the neighbouring baseline runs, fast injector)", "",
                  "| arm | since-return ms (knob / base / delta) | verdict | settle ms (knob / base / delta) | verdict | gfx p50 (knob / base) |", "|---|---|---|---|---|---|"]
        for arm, c in comps.items():
            a, b, p = c["fc_ret"], c["settle"], c["gfx_p50"]
            lines.append("| %s | %s / %s / %s (%s%%) | %s | %s / %s / %s | %s | %s / %s |" % (
                arm, a.get("knob_ms"), a.get("base_ms"), a.get("delta_ms"), a.get("delta_pct"), a["verdict"],
                b.get("knob_ms"), b.get("base_ms"), b.get("delta_ms"), b["verdict"], p.get("knob_ms"), p.get("base_ms")))
        f7 = self.f7()
        if f7:
            lines += ["", "## F7: injector", "", "plain adb: first change since send %s ms, since return %s ms, inject %s ms;  persistent shell: since send %s ms, since return %s ms, inject %s ms" % f7]
        if self.stop_reason:
            lines += ["", "## STOPPED: %s" % self.stop_reason]
        txt = "\n".join(lines) + "\n"
        open(os.path.join(self.out, "summary.md"), "w").write(txt)
        json.dump({"runs": runs, "ab": comps, "stop_reason": self.stop_reason, "orig": self.orig, "gpu_profile": self.gpu_profile},
                  open(os.path.join(self.out, "summary.json"), "w"), indent=1, default=str)

    def compare(self):
        seq = [r for r in self.runs if r["injector"] == "fast" and r["kind"] != "trace"]
        arms = {}
        for i, r in enumerate(seq):
            if r["kind"].startswith("arm:"):
                arms.setdefault(r["kind"][4:], []).append(i)
        allbase = [r for r in seq if r["kind"] == "baseline"]
        res = {}
        for arm, idx in arms.items():
            before = [seq[j] for j in range(idx[0]) if seq[j]["kind"] == "baseline"][-1:]
            after = [seq[j] for j in range(idx[-1] + 1, len(seq)) if seq[j]["kind"] == "baseline"][:1]
            base, knob = before + after, [seq[j] for j in idx]

            def metric(getter, floor=50.0):
                c = cmp_metric([getter(x) for x in knob], [getter(x) for x in base], floor)
                nb = [getter(x) for x in allbase if getter(x) is not None]
                if "noise_ms" in c and nb:
                    c["noise_ms"] = round(max(c["noise_ms"], max(nb) - min(nb)), 1)
                    c["verdict"] = "within noise" if abs(c["delta_ms"]) <= c["noise_ms"] else ("faster" if c["delta_ms"] < 0 else "SLOWER")
                return c
            res[arm] = {"fc_ret": metric(lambda x: x["fc_ret_med"]), "settle": metric(lambda x: x["settle_med"]),
                        "gfx_p50": metric(lambda x: x["gfx"].get("launcher", {}).get("p50"), 5.0)}
        return res

    def f7(self):
        pl = [r for r in self.runs if r["injector"] == "plain" and r["kind"] == "baseline"]
        fa = [r for r in self.runs if r["injector"] == "fast" and r["kind"] == "baseline"][:2]
        if not pl or not fa:
            return None
        m = lambda rs, k: rnd(med([r[k] for r in rs]))
        return (m(pl, "fc_med"), m(pl, "fc_ret_med"), m(pl, "inject_ms_med"), m(fa, "fc_med"), m(fa, "fc_ret_med"), m(fa, "inject_ms_med"))

    # ---- teardown
    def teardown(self):
        self.log("TEARDOWN: reverting knobs (no gate is written; the coordinator closes the gates)")
        failed = []
        for arm in list(self.applied):
            if arm == "instr":
                continue
            try:
                if not self.revert_arm(arm):
                    failed.append(arm)
            except Exception as e:      # keep reverting the others
                failed.append("%s (%r)" % (arm, e))
        if self.applied.get("instr") or self.orig.get("input_diagnostics") in ("N", "0", "n"):
            try:
                self.write_check("echo 0 > $M/a9_eink/parameters/input_diagnostics; echo \"RB=$(P a9_eink/input_diagnostics)\"", "RB=", "input_diagnostics off")
            except Exception as e:
                failed.append("input_diagnostics (%r)" % e)
        try:
            self.plain.sh("killall perfetto 2>/dev/null; rm -f /data/local/tmp/eink-perf.pftrace /data/local/tmp/eink-perf.cfg /data/local/tmp/eink-perf.log", 20)
        except Exception:
            pass
        try:
            self.stop_daemons()
        except Exception:
            pass
        final = None
        try:
            final = self.telemetry()
            c = self.collect()
            json.dump({"telemetry": {k: v for k, v in (final or {}).items() if not k.startswith("_")},
                       "printk": c.get("printk"), "anim": c.get("anim"), "devfreq": c.get("devfreq"), "runtime_pm": c.get("runtime_pm")},
                      open(os.path.join(self.out, "final-state.json"), "w"), indent=1)
            now = parse_kv(c.get("devfreq", "")); pm = parse_kv(c.get("runtime_pm", ""))
            o = self.orig
            if o.get("devfreq_present") and now.get("min_freq") != o["devfreq"]["min_freq"]:
                failed.append("min_freq is %s, was %s" % (now.get("min_freq"), o["devfreq"]["min_freq"]))
            if o.get("pm_present") and pm.get("control") != o["pm"]["control"]:
                failed.append("power/control is %s, was %s" % (pm.get("control"), o["pm"]["control"]))
            if o.get("printk_present") and (c.get("printk", "").split() or ["?"])[0] != o["printk"][0]:
                failed.append("printk console level is %s, was %s" % (c.get("printk", "").split()[:1], o["printk"][0]))
        except Exception as e:
            failed.append("final state read failed: %r" % e)
        self.event("teardown", failed=failed, final_gates=(final or {}).get("gates"), final_gate=(final or {}).get("gate"))
        return failed, final


def rnd(v, n=0):
    return None if v is None else (round(v, n) if n else int(round(v)))


def fmtp(v):
    return "-" if v is None else "%.0f%%" % (100 * v)


def diff_num(a, b):
    a, b = num(a), num(b)
    return None if a is None or b is None else int(b - a)


def banner(lines):
    w = max(len(x) for x in lines) + 4
    print("\n" + "#" * w)
    for x in lines:
        print("# " + x.ljust(w - 4) + " #")
    print("#" * w + "\n", flush=True)


def revert_only(a):
    """Replay orig.json (a killed session). Only the same knobs; never a gate."""
    o = json.load(open(os.path.join(a.revert_only, "orig.json")))
    a.out = a.revert_only
    s = Session(a)
    s.orig = o
    s.freqs = o.get("freqs", [])
    for arm in ("f3", "f5", "f2c", "hvi"):
        s.applied[arm] = True
    ok = True
    for arm in list(s.applied):
        ok = s.revert_arm(arm) and ok
    if o.get("input_diagnostics") in ("N", "0", "n"):
        s.write_check("echo 0 > $M/a9_eink/parameters/input_diagnostics; echo \"RB=$(P a9_eink/input_diagnostics)\"", "RB=", "input_diagnostics off")
    s.log("revert-only %s" % ("OK" if ok else "FAILED: check by hand"))
    return 0 if ok else 3


def plan_text(a):
    arms = [x for x in a.arms if x in KNOWN_ARMS]
    return "\n".join(["trial3b-perf plan (nothing is sent to the phone by --plan)",
                      " B0-plain x1   baseline, plain `adb shell input` (F7 reference)",
                      " B0-fast  x2   baseline, persistent-shell injector (all later runs use it)",
                      " T1-trace x1   perfetto %d s + top x3 + SurfaceFlinger dump (excluded from A/B)" % a.trace_s,
                      " arms (each: %d runs, then 1 baseline run): %s" % (a.reps, ", ".join(arms)),
                      " budget %d s from start, hard cap %d s from the gate opening, scenario %s" % (a.budget_s, SESSION_CAP_S, a.scenario),
                      " never written: any gate, bins, hv_max_ms, hv_hold_ms, fp9929a.*, a9_eink rail/power; gates are left for the coordinator to close"])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--adb", help="adb command prefix, e.g. 'adb -s SERIAL' (a fake in --dry-run)")
    ap.add_argument("--out", default=os.path.expanduser("~/A9-build/mainline/eink-trial3b/perf-%s" % datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
    ap.add_argument("--calib", help="eink-probe calib.json (needed for real glass metrics)")
    ap.add_argument("--probe", default=os.path.join(PROBE_DIR, "eink-probe"), help="eink-probe command")
    ap.add_argument("--scenario", default=os.path.join(PROBE_DIR, "scenarios", "perf.json"))
    ap.add_argument("--url", default="http://127.0.0.1:8093/stream")
    ap.add_argument("--arms", type=lambda s: [x for x in s.split(",") if x], default=list(DEFAULT_ARMS), help="comma list of %s" % ",".join(KNOWN_ARMS))
    ap.add_argument("--reps", type=int, default=2, help="runs per knob arm")
    ap.add_argument("--budget-s", type=float, default=1320.0, help="session time bound from script start (default 22 min)")
    ap.add_argument("--preroll-ms", type=int, default=1500)
    ap.add_argument("--run-timeout-s", type=float, default=240.0)
    ap.add_argument("--poll-s", type=float, default=2.0)
    ap.add_argument("--max-frames-stale-s", type=float, default=3.0,
                    help="no new camera frame for this long = a stale camera: WARNING by default (see --stop-on-camera-stale)")
    ap.add_argument("--stop-on-camera-stale", action="store_true",
                    help="opt-in: the old behaviour, a stale/absent/unhealthy/frozen camera stops the session (default: warn, mark the run's optical metrics invalid, reconnect)")
    ap.add_argument("--stale-reconnect-s", type=float, default=3.0,
                    help="the probe reopens the camera stream when no frame arrived for this long (0 = never)")
    ap.add_argument("--min-fps", type=float, default=7.5)
    ap.add_argument("--max-temp-c", type=float, default=75.0)
    ap.add_argument("--hvi-ms", type=int, default=8000, help="hv_hold_interactive_ms for the optional hvi arm (reviewed value)")
    ap.add_argument("--trace-s", type=int, default=30)
    ap.add_argument("--b0-fast-runs", type=int, default=2, help="initial baseline runs with the persistent-shell injector")
    ap.add_argument("--no-trace", action="store_true", help="skip the T1 trace/extras run")
    ap.add_argument("--cam-latency-ms", type=float, default=0.0)
    ap.add_argument("--exclude-actions", type=lambda s: [x for x in s.split(",") if x], default=["home"])
    ap.add_argument("--stop-file", default="/tmp/trial3b-perf.STOP", help="touch this file to stop between polls")
    ap.add_argument("--go", action="store_true", help="required for a real phone (the coordinator is attending)")
    ap.add_argument("--dry-run", action="store_true", help="refuse a real adb binary; for the fake phone of the offline tests")
    ap.add_argument("--plan", action="store_true", help="print the plan and exit; sends nothing")
    ap.add_argument("--revert-only", metavar="DIR", help="replay DIR/orig.json to revert the knobs of a killed session")
    a = ap.parse_args()
    a.out = os.path.abspath(a.out)
    if a.plan:
        print(plan_text(a)); return 0
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
    s = Session(a)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(Stop("SIGTERM")))
    s.log("trial3b-perf: out=%s adb='%s' arms=%s reps=%d budget=%ds" % (a.out, a.adb, ",".join(a.arms), a.reps, a.budget_s))
    try:
        s.preflight()
        s.start_daemons()
        s.enable_instruments()
        s.run_all()
    except Stop as e:
        s.stop_reason = str(e)
    except KeyboardInterrupt:
        s.stop_reason = "operator interrupt"
    except Exception as e:                       # any bug must still revert
        import traceback
        s.stop_reason = "internal error: %r" % (e,)
        traceback.print_exc()
    if s.stop_reason:
        banner(["STOP: " + s.stop_reason, "Knobs are being reverted. Gates are NOT touched by this script.",
                "COORDINATOR: look at the glass and at gate/fault, then end the session (a9ml-eink.sh close)."])
    failed, final = s.teardown()
    s.summary()
    if failed:
        banner(["REVERT PROBLEM: " + "; ".join(failed), "Fix by hand from %s/orig.json or run --revert-only %s" % (a.out, a.out)])
    if final:
        s.log("final: gate=%s gates=%s fault=%r" % (final.get("gate"), final.get("gates"), final.get("fault")))
    s.log("COORDINATOR: close the gates now and verify 000 + HV/PWRUP/VCOM_EN down (review 75c). Results: %s/summary.md" % a.out)
    return 3 if (s.stop_reason or failed) else 0


if __name__ == "__main__":
    sys.exit(main())
