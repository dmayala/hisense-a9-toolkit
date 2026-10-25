#!/usr/bin/env python3
"""health-gate.py -- the review-24 gates for the A9_HEALTH=real supervised test.
Offline evaluator: it never talks to the phone. Main captures the inputs with
the read-only commands printed by `health-gate.py commands` and passes the
files here. Exit 0 = gate passed, 1 = STOP (reason printed), 2 = usage.

  health-gate.py commands
      print the exact read-only capture commands (for the phone operator)
  health-gate.py artifact --vendor V.img --boot B.img --snapshot SNAPDIR
                          [--record OUT.txt] [--commit SHA]
                          [--thermal-kernel | --thermal-hal [--thermal-conf F]]
      gate 1+2: the final images. Vendor: patched health binary sha256, one
      IHealth/default (v4), rc line, SELinux label, cf.health APEX absent,
      health.storage present, example thermal APEX present, no A9 thermal HAL.
      Boot/kernel: no thermal DT strings in the boot image, the snapshot is not
      a thermal one, the snapshot's Image.gz bytes are inside the boot image.
      --thermal-kernel (android-thermal, 2026-10-16): instead REQUIRE the gated
      thermal kernel proven by thermal step 2 (0043-0046): the three gate
      options, a *-thermal.dtb snapshot, the thermal DT strings in the boot
      DTB; the vendor still has no A9 thermal HAL.
      --thermal-hal: --thermal-kernel plus the A9 thermal HAL in the vendor:
      binary + label, rc, conf == --thermal-conf (default: the repo's),
      exactly one IThermal (v3) fragment, example thermal APEX absent, pairing
      file /etc/thermal-a9.kernel-image.sha256 == the snapshot's Image.gz.
      Writes a provenance record (hashes, flags) with --record.
  health-gate.py telemetry CAPTURE [CAPTURE...]
      gate 3: fresh repeated baseline sysfs telemetry (STOP on any error,
      stale/missing value, absent battery, low margin, heat).
  health-gate.py compare --dumpsys DUMPSYS.txt --sysfs CAPTURE
      gate 4: Android's BatteryService values against fresh sysfs values.
"""
import argparse, hashlib, os, re, subprocess, sys, tempfile

PATCHED_SHA = "13e186d508283150b0c9c4908114181478dec7cb46db66da60c51dd514653908"
HEALTH_BIN = "/bin/hw/android.hardware.health-service.cuttlefish"
HEALTH_RC = "/etc/init/android.hardware.health-service.a9.rc"
HEALTH_XML = "/etc/vintf/manifest/android.hardware.health-service.a9.xml"
RC_LINE = "service vendor.health-a9 /vendor/bin/hw/android.hardware.health-service.cuttlefish"
THERMAL_DT_STRINGS = (b"quiet-therm-thermal", b"thermal-sensor-quiet-therm", b"generic-adc-thermal")
THERMAL_GATES = ("CONFIG_QCOM_TSENS_REQUIRE_VALID=y", "CONFIG_THERMAL_CRITICAL_CONFIRM_SAMPLES=3",
                 "CONFIG_THERMAL_EMERGENCY_POWEROFF_DELAY_MS=100")
THERMAL_BIN = "/bin/hw/android.hardware.thermal-service.a9"
THERMAL_RC = "/etc/init/android.hardware.thermal-service.a9.rc"
THERMAL_CONF = "/etc/thermal-a9.conf"
THERMAL_PAIR = "/etc/thermal-a9.kernel-image.sha256"
REPO_THERMAL_CONF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "thermal-hal", "thermal-a9.conf")

# gate-3 limits (supervised first test; deliberately conservative)
MIN_SAMPLES, MIN_SPAN_S = 5, 8.0
VBAT_MIN_UV, VBAT_MAX_UV = 3_800_000, 4_480_000     # >= 3.8 V margin; <= FV 4.45 V + 30 mV
TEMP_MIN_DC, TEMP_MAX_DC = 100, 400                 # 10.0 .. 40.0 C ("cool device")
IBAT_MAX_UA = 3_000_000
SYSFS_KEYS = ("battery.present", "battery.status", "battery.health", "battery.voltage_now",
              "battery.current_now", "battery.temp", "battery.capacity", "usb.present", "usb.online")

CAP = (r"""adb shell 'echo "boot_id=$(cat /proc/sys/kernel/random/boot_id)"; for i in 1 2 3 4 5 6; do echo "t=$(cut -d" " -f1 /proc/uptime)"; echo "epoch=$(date +%s)"; """
       r"""for f in present status health voltage_now current_now temp capacity; do echo "battery.$f=$(cat /sys/class/power_supply/battery/$f 2>&1)"; done; """
       r"""for f in present online; do echo "usb.$f=$(cat /sys/class/power_supply/a9-usb/$f 2>&1)"; done; sleep 2; done'""")
G = "python3 -I scripts/mainline/android/health/health-gate.py"
COMMANDS = f"""# Gate 3 (before the enabled boot, on the known-good baseline image; read-only):
{CAP} > baseline-telemetry.txt
{G} telemetry baseline-telemetry.txt
# Gate 4 (on the A9_HEALTH=real image; capture both right after each other, repeat):
{CAP} > real-telemetry.txt
adb shell 'echo "boot_id=$(cat /proc/sys/kernel/random/boot_id)"; dumpsys battery' > real-dumpsys-battery.txt
{G} telemetry real-telemetry.txt
{G} compare --dumpsys real-dumpsys-battery.txt --sysfs real-telemetry.txt
# A PASS does not replace the review-24 manual, ABL/header and kernel/vendor checks.
"""



class Stop(Exception):
    pass


def debugfs(img, cmd):
    r = subprocess.run(["debugfs", "-R", cmd, img], capture_output=True, text=True)
    return r.stdout


def read_file(img, path):
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "f")
        subprocess.run(["debugfs", "-R", f"dump {path} {out}", img], capture_output=True)
        if not os.path.exists(out):
            return None
        return open(out, "rb").read()


def ls(img, path):
    out = debugfs(img, f"ls -p {path}")
    return [l.split("/")[5] for l in out.splitlines() if l.count("/") >= 6 and l.split("/")[5] not in (".", "..")]


def boot_fields(data):
    """Android boot image header v2 (the A9 ABL's format, CLAUDE.md): returns
    (header_version, page_size, {kernel, ramdisk, second, recovery_dtbo, dtb})."""
    import struct
    if data[:8] != b"ANDROID!":
        raise Stop("boot image: no ANDROID! magic")
    ks, _, rs, _, ss, _, _, page, ver = struct.unpack_from("<9I", data, 8)
    if ver != 2:
        raise Stop(f"boot image header version {ver} (the A9 uses 2)")
    rdo_size, = struct.unpack_from("<I", data, 1632)
    hsize, dtb_size = struct.unpack_from("<II", data, 1644)
    pad = lambda n: (n + page - 1) // page * page
    off, out = pad(hsize), {}
    for name, size in (("kernel", ks), ("ramdisk", rs), ("second", ss), ("recovery_dtbo", rdo_size), ("dtb", dtb_size)):
        out[name] = data[off:off + size]
        if len(out[name]) != size:
            raise Stop(f"boot image truncated in {name}")
        off += pad(size)
    return ver, page, out


def sha(b):
    return hashlib.sha256(b).hexdigest()


def artifact(a):
    errs, rec = [], []
    v, b = a.vendor, a.boot
    for p in (v, b):
        if not os.path.isfile(p):
            raise Stop(f"missing {p}")
    thal = bool(getattr(a, "thermal_hal", False))
    tk = thal or bool(getattr(a, "thermal_kernel", False))
    rec += [f"commit {a.commit or '(not given)'}",
            f"flags A9_HEALTH=real A9_THERMAL={int(tk)} A9_THERMAL_HAL={int(thal)}",
            f"vendor {hashlib.sha256(open(v, 'rb').read()).hexdigest()} {v}",
            f"boot   {hashlib.sha256(open(b, 'rb').read()).hexdigest()} {b}"]
    hb = read_file(v, HEALTH_BIN)
    if hb is None or hashlib.sha256(hb).hexdigest() != PATCHED_SHA:
        errs.append(f"{HEALTH_BIN}: not the patched binary {PATCHED_SHA[:16]}")
    lab = debugfs(v, f"ea_get {HEALTH_BIN} security.selinux")
    if "hal_health_default_exec" not in lab:
        errs.append("health binary label is not hal_health_default_exec")
    rc = read_file(v, HEALTH_RC)
    if rc is None or RC_LINE not in rc.decode(errors="replace").splitlines():
        errs.append(f"{HEALTH_RC}: missing or wrong service line")
    ihealth, ithermal = [], []
    for d in ("/etc/vintf/manifest", "/etc/vintf"):
        for n in ls(v, d):
            if not n.endswith(".xml"):
                continue
            x = (read_file(v, f"{d}/{n}") or b"").decode(errors="replace")
            for m in re.finditer(r"<hal[^>]*>(.*?)</hal>", x, re.S):
                h = m.group(1)
                if "<name>android.hardware.health</name>" in h and "IHealth/default" in h:
                    ihealth.append((f"{d}/{n}", re.search(r"<version>(\d+)</version>", h)))
                if "<name>android.hardware.thermal</name>" in h:
                    ithermal.append((f"{d}/{n}", re.search(r"<version>(\d+)</version>", h)))
    if len(ihealth) != 1 or not ihealth[0][1] or ihealth[0][1].group(1) != "4":
        errs.append(f"IHealth/default declarations in /vendor: {[(p, m and m.group(1)) for p, m in ihealth]} (want exactly one, v4)")
    if not thal and ithermal:
        errs.append(f"a vendor-partition IThermal fragment exists ({[p for p, _ in ithermal]}); the baseline thermal is the example APEX")
    if thal and (len(ithermal) != 1 or not ithermal[0][1] or ithermal[0][1].group(1) != "3"):
        errs.append(f"IThermal declarations in /vendor: {[(p, m and m.group(1)) for p, m in ithermal]} (want exactly one, v3)")
    apex = ls(v, "/apex")
    for want, present in (("com.google.cf.health.apex", False), ("com.google.cf.health.storage.apex", True),
                          ("com.android.hardware.thermal.apex", not thal)):
        if (want in apex) != present:
            errs.append(f"/apex/{want} {'missing' if present else 'present'}")
    snap = a.snapshot
    try:
        img = open(os.path.join(snap, "Image.gz"), "rb").read()
        cfg = open(os.path.join(snap, "config")).read()
        dtbs = sorted(n for n in os.listdir(snap) if re.fullmatch(r"sm6115-hisense-hlte556n-android[a-z0-9-]*\.dtb", n))
        if len(dtbs) != 1:
            raise Stop(f"snapshot {snap}: want exactly one sm6115-hisense-hlte556n-android*.dtb, found {dtbs}")
        sdtb = open(os.path.join(snap, dtbs[0]), "rb").read()
        sums = open(os.path.join(snap, "SHA256SUMS")).read().split("\n")
        dsrc = open(os.path.join(snap, "dtb.source")).read() if os.path.exists(os.path.join(snap, "dtb.source")) else ""
    except OSError as e:
        raise Stop(f"snapshot {snap}: {e}")
    thermal_files = (THERMAL_BIN, THERMAL_CONF, THERMAL_RC)
    if not thal:
        for p in thermal_files:
            if read_file(v, p) is not None:
                errs.append(f"A9 thermal HAL file present: {p}")
    else:
        for p in thermal_files + (THERMAL_PAIR,):
            if read_file(v, p) is None:
                errs.append(f"A9 thermal HAL file missing: {p}")
        if "hal_thermal_default_exec" not in debugfs(v, f"ea_get {THERMAL_BIN} security.selinux"):
            errs.append("thermal HAL binary label is not hal_thermal_default_exec")
        rcb = (read_file(v, THERMAL_RC) or b"").decode(errors="replace").splitlines()
        if "service vendor.thermal-a9 /vendor/bin/hw/android.hardware.thermal-service.a9" not in rcb or "    user system" not in rcb:
            errs.append(f"{THERMAL_RC}: missing service line or not user system")
        want_conf = open(getattr(a, "thermal_conf", None) or REPO_THERMAL_CONF, "rb").read()
        if read_file(v, THERMAL_CONF) != want_conf:
            errs.append(f"{THERMAL_CONF} differs from the reviewed config")
        pair = (read_file(v, THERMAL_PAIR) or b"").decode().strip()
        if pair != sha(img):
            errs.append(f"{THERMAL_PAIR} ({pair[:12]}) is not the snapshot Image.gz ({sha(img)[:12]})")
        rec.append(f"thermal HAL {sha(read_file(v, THERMAL_BIN) or b'')} conf {sha(want_conf)} paired with {sha(img)[:16]}")
    boot = open(b, "rb").read()
    _, page, f = boot_fields(boot)
    for s in THERMAL_DT_STRINGS:
        if not tk and (s in f["dtb"] or s in f["kernel"]):
            errs.append(f"boot image DTB/kernel contains thermal DT string {s.decode()} (thermal variant selected)")
        if tk and s not in f["dtb"] and s not in f["kernel"]:
            errs.append(f"boot image DTB lacks thermal DT string {s.decode()} (--thermal-kernel needs the thermal DTB)")
    for line in filter(None, sums):
        h, n = line.split(None, 1)
        q = os.path.join(snap, n.strip().lstrip("*"))
        if not os.path.isfile(q) or sha(open(q, "rb").read()) != h:
            errs.append(f"snapshot SHA256SUMS mismatch: {n.strip()}")
    # the kernel FIELD of the header, not a substring search
    if f["kernel"] == img:
        rec.append("boot kernel field == snapshot Image.gz (exact)")
    elif f["kernel"].startswith(img) and f["kernel"][len(img):].startswith(b"\xd0\x0d\xfe\xed"):
        rec.append("boot kernel field == snapshot Image.gz + appended DTB")
        if f["kernel"][len(img):] != sdtb and not f["dtb"]:
            errs.append("appended DTB differs from the snapshot DTB")
    else:
        errs.append("boot kernel field is not the snapshot's Image.gz (kernel/modules provenance mismatch)")
    if f["dtb"]:
        if f["dtb"] != sdtb:
            errs.append("boot DTB field differs from the snapshot DTB")
        else:
            rec.append("boot dtb field == snapshot DTB")
    # vendor side of the same kernel: E Ink module closure and its kernel record
    kis = read_file(v, "/lib/modules/kernel-image.sha256")
    if kis is not None:
        if kis.decode().strip() != sha(img):
            errs.append("vendor lib/modules/kernel-image.sha256 is not the snapshot Image.gz")
        for line in filter(None, sums):
            h, n = line.split(None, 1); n = n.strip().lstrip("*")
            if n.startswith("modules/") or n.startswith("./modules/"):
                m = os.path.basename(n)
                vb = read_file(v, f"/lib/modules/{m}")
                if vb is None or sha(vb) != h:
                    errs.append(f"vendor module {m} differs from the snapshot")
        rec.append("vendor modules == snapshot modules (SHA256SUMS)")
    else:
        rec.append("vendor has no lib/modules (non-eink build): module check not applicable")
    if not tk:
        if re.search(r"^CONFIG_THERMAL_CRITICAL_CONFIRM_SAMPLES=([2-9]|10)$", cfg, re.M) or "-thermal" in dsrc:
            errs.append("snapshot is a thermal (A9_THERMAL=1) build")
        if re.search(r"^CONFIG_THERMAL_EMERGENCY_POWEROFF_DELAY_MS=[1-9]", cfg, re.M):
            errs.append("snapshot config has the thermal forced power-off delay (thermal variant)")
    else:
        for c in THERMAL_GATES:
            if not re.search(rf"^{re.escape(c)}$", cfg, re.M):
                errs.append(f"--thermal-kernel: snapshot config lacks {c} (not the gated thermal kernel)")
        if not re.search(r"-thermal(-adsp(-d2)?)?\.dtb\s*$", dsrc):
            errs.append("--thermal-kernel: snapshot dtb.source is not a *-thermal.dtb")
        rec.append("thermal kernel: gate options + thermal DTB present (thermal step 2 kernel family)")
    rec += [f"snapshot {snap} Image.gz {hashlib.sha256(img).hexdigest()}",
            f"health binary {PATCHED_SHA}", f"IHealth fragment {ihealth[0][0] if ihealth else '-'}"]
    if a.record:
        rp = os.path.realpath(a.record)
        if "a9-assets" in rp or rp.startswith("/share/"):
            raise Stop("refusing to write the record under ~/a9-assets")
        if os.path.exists(rp) and not a.overwrite:
            raise Stop(f"{rp} exists (use --overwrite)")
        rec.append("note: commit/flag strings are operator-supplied labels, not authenticated provenance")
        open(a.record, "w").write("\n".join(rec + [f"result {'PASS' if not errs else 'STOP'}"] + errs) + "\n")
    if errs:
        raise Stop("; ".join(errs))
    print("\n".join(rec))


def capture_boot_id(path):
    for line in open(path, errors="replace"):
        if line.startswith("boot_id="):
            return line.strip()[8:]
    return None


def parse_capture(path):
    samples, cur = [], None
    for line in open(path, errors="replace"):
        line = line.strip()
        if line.startswith("boot_id="):
            continue
        if line.startswith("t="):
            cur = {"t": line[2:]}
            samples.append(cur)
        elif "=" in line and cur is not None:
            k, v = line.split("=", 1)
            cur[k] = v
    return samples


def as_int(v):
    return int(v) if re.fullmatch(r"-?\d+", v or "") else None


MAX_GAP_S, MAX_AGE_S = 10.0, 900
SYSFS_STATUS = ("Charging", "Discharging", "Not charging", "Full")


def check_telemetry(samples, where, now=None, max_age=MAX_AGE_S):
    import math, time
    errs = []
    if len(samples) < MIN_SAMPLES:
        errs.append(f"{where}: {len(samples)} samples (< {MIN_SAMPLES})")
    ts, eps = [], []
    for i, s in enumerate(samples):
        try:
            t = float(s["t"])
            if not math.isfinite(t) or t < 0:
                raise ValueError
            ts.append(t)
        except ValueError:
            errs.append(f"{where}#{i}: bad uptime {s['t']!r} (must be finite, >= 0)")
        e = as_int(s.get("epoch"))
        if e is None:
            errs.append(f"{where}#{i}: no epoch (capture with `health-gate.py commands`)")
        else:
            eps.append(e)
        if s.get("battery.status") not in SYSFS_STATUS:
            errs.append(f"{where}#{i}: battery.status {s.get('battery.status')!r} not in {SYSFS_STATUS}")
        if s.get("usb.online") == "1" and s.get("usb.present") != "1":
            errs.append(f"{where}#{i}: usb online without present (driver invariant)")
        if s.get("battery.status") in ("Charging", "Full") and s.get("usb.online") != "1":
            errs.append(f"{where}#{i}: status {s.get('battery.status')} with USB offline")
        for k in SYSFS_KEYS:
            v = s.get(k)
            if v is None or v == "" or re.search(r"No data|No such|Invalid|error|denied|not found", v, re.I):
                errs.append(f"{where}#{i}: {k} unreadable ({v!r}) -- ENODATA = error/stale telemetry")
        if s.get("battery.present") != "1":
            errs.append(f"{where}#{i}: battery not present ({s.get('battery.present')!r})")
        if s.get("battery.health") not in ("Good",):
            errs.append(f"{where}#{i}: battery health {s.get('battery.health')!r}")
        uv, dc, ua, cap = (as_int(s.get(k)) for k in ("battery.voltage_now", "battery.temp", "battery.current_now", "battery.capacity"))
        if uv is None or not VBAT_MIN_UV <= uv <= VBAT_MAX_UV:
            errs.append(f"{where}#{i}: voltage {uv} uV outside {VBAT_MIN_UV}..{VBAT_MAX_UV}")
        if dc is None or not TEMP_MIN_DC <= dc <= TEMP_MAX_DC:
            errs.append(f"{where}#{i}: temperature {dc} deci-C outside {TEMP_MIN_DC}..{TEMP_MAX_DC}")
        if ua is None or abs(ua) > IBAT_MAX_UA:
            errs.append(f"{where}#{i}: current {ua} uA implausible")
        if cap is None or not 1 <= cap <= 100:
            errs.append(f"{where}#{i}: capacity {cap} (0 or invalid)")
        if s.get("usb.online") not in ("0", "1") or s.get("usb.present") not in ("0", "1"):
            errs.append(f"{where}#{i}: usb present/online {s.get('usb.present')!r}/{s.get('usb.online')!r}")
    if len(ts) == len(samples) and len(ts) > 1:
        if any(b <= a for a, b in zip(ts, ts[1:])):
            errs.append(f"{where}: uptime not strictly increasing")
        elif ts[-1] - ts[0] < MIN_SPAN_S:
            errs.append(f"{where}: samples span {ts[-1] - ts[0]:.1f} s (< {MIN_SPAN_S})")
        elif max(b - a for a, b in zip(ts, ts[1:])) > MAX_GAP_S:
            errs.append(f"{where}: a gap above {MAX_GAP_S} s between samples")
    if eps and len(eps) == len(samples):
        if any(b < a for a, b in zip(eps, eps[1:])):
            errs.append(f"{where}: epoch going backwards")
        now = time.time() if now is None else now
        if now - eps[-1] > max_age or eps[-1] - now > 120:
            errs.append(f"{where}: capture is {now - eps[-1]:.0f} s old by the host clock (max {max_age}): not fresh")
    online = {s.get("usb.online") for s in samples}
    if len(online) > 1:
        errs.append(f"{where}: usb.online changed during the capture {sorted(online)} (re-capture undisturbed)")
    return errs


def telemetry(a):
    errs = []
    for p in a.captures:
        if not capture_boot_id(p):
            errs.append(f"{os.path.basename(p)}: no boot_id header")
        errs += check_telemetry(parse_capture(p), os.path.basename(p), a.now, a.max_age)
    if errs:
        raise Stop("; ".join(errs[:12]) + (f" (+{len(errs) - 12} more)" if len(errs) > 12 else ""))
    print(f"telemetry PASS: {sum(len(parse_capture(p)) for p in a.captures)} samples within {a.max_age} s of the host clock")


CURRENT_NOISE_UA = 50_000   # documented tolerance: 50 mA between two capture instants
STATUS = {"Unknown": 1, "Charging": 2, "Discharging": 3, "Not charging": 4, "Full": 5}


def parse_dumpsys(path):
    d = {}
    for line in open(path, errors="replace"):
        m = re.match(r"\s+([A-Za-z ]+):\s*(.*)$", line)
        if m:
            d.setdefault(m.group(1).strip(), m.group(2).strip())
    return d


def compare(a):
    d = parse_dumpsys(a.dumpsys)
    samples = parse_capture(a.sysfs)
    errs = check_telemetry(samples, os.path.basename(a.sysfs), a.now, a.max_age)
    b1, b2 = capture_boot_id(a.dumpsys), capture_boot_id(a.sysfs)
    if not b1 or b1 != b2:
        errs.append(f"dumpsys boot_id {b1} vs sysfs capture boot_id {b2}: not the same boot")
    if not samples:
        raise Stop("no sysfs samples")
    need = ("level", "voltage", "temperature", "status", "present", "USB powered", "AC powered")
    miss = [k for k in need if k not in d]
    if miss:
        raise Stop(f"dumpsys battery lacks {miss}")
    def rng(k, f=lambda x: x):
        vals = [f(as_int(s.get(k))) for s in samples if as_int(s.get(k)) is not None]
        return (min(vals), max(vals)) if vals else (None, None)
    lv = as_int(d["level"])
    lo, hi = rng("battery.capacity")
    if lv is None or lo is None or not lo - 1 <= lv <= hi + 1:
        errs.append(f"level {lv} vs sysfs capacity {lo}..{hi}")
    if lv == 85 and d.get("temperature") == "250" and d.get("voltage") == "3600":
        errs.append("dumpsys shows the Cuttlefish fake values (85 %, 3600 mV, 25.0 C): the real HAL is NOT running")
    mv = as_int(d["voltage"]); lo, hi = rng("battery.voltage_now", lambda x: x // 1000)
    if mv is None or lo is None or not lo - 30 <= mv <= hi + 30:
        errs.append(f"voltage {mv} mV vs sysfs {lo}..{hi} mV (uV/1000)")
    t = as_int(d["temperature"]); lo, hi = rng("battery.temp")
    if t is None or lo is None or not lo - 10 <= t <= hi + 10:
        errs.append(f"temperature {t} (deci-C) vs sysfs {lo}..{hi}")
    st = {STATUS.get(s.get("battery.status")) for s in samples}
    if as_int(d["status"]) not in st:
        errs.append(f"status {d['status']} vs sysfs {sorted(x for x in st if x)}")
    if d["present"] != "true":
        errs.append(f"present {d['present']}")
    if d.get("health") != "2":   # BatteryManager.BATTERY_HEALTH_GOOD (sysfs Good is gated above)
        errs.append(f"Android health {d.get('health')!r} (want 2 = GOOD)")
    for k in ("AC powered", "USB powered", "Wireless powered", "present"):
        if k in d and d[k] not in ("true", "false"):
            errs.append(f"{k} {d[k]!r} not a boolean")
    usb = {s.get("usb.online") for s in samples}
    if (d["USB powered"] == "true") != ("1" in usb) or len(usb) != 1:
        errs.append(f"USB powered {d['USB powered']} vs usb.online {sorted(usb)}")
    if d["AC powered"] != "false":
        errs.append(f"AC powered {d['AC powered']} (no AC supply exists on this driver)")
    # current: Android convention + = into the battery. The sysfs sign must agree
    # with the sysfs status, and, when dumpsys prints it, Android's value must
    # have the same sign and be within CURRENT_NOISE_UA of the sysfs range.
    lo, hi = rng("battery.current_now")
    for s in samples:
        ua = as_int(s.get("battery.current_now"))
        if ua is None:
            continue
        if s.get("battery.status") == "Discharging" and ua > CURRENT_NOISE_UA:
            errs.append(f"sysfs current {ua} uA positive while Discharging (sign)")
        if s.get("battery.status") == "Charging" and ua < -CURRENT_NOISE_UA:
            errs.append(f"sysfs current {ua} uA negative while Charging (sign)")
    cur_key = next((k for k in ("current now", "current_now", "Current now") if k in d), None)
    if cur_key is None:
        print("note: dumpsys battery prints no current; current validated from sysfs only (sign vs status)")
    else:
        cur = as_int(d[cur_key])
        if cur is None or lo is None or not lo - CURRENT_NOISE_UA <= cur <= hi + CURRENT_NOISE_UA:
            errs.append(f"{cur_key} {cur} uA vs sysfs {lo}..{hi} (+-{CURRENT_NOISE_UA})")
        elif abs(cur) > CURRENT_NOISE_UA and lo is not None and (cur > 0) != (((lo + hi) / 2) > 0):
            errs.append(f"{cur_key} {cur} uA sign differs from sysfs {lo}..{hi}")
    if errs:
        raise Stop("; ".join(errs))
    print("compare PASS: Android BatteryService matches fresh sysfs (level, mV, deci-C, status, presence, USB)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("commands")
    x = sp.add_parser("artifact")
    x.add_argument("--vendor", required=True); x.add_argument("--boot", required=True)
    x.add_argument("--snapshot", required=True); x.add_argument("--record"); x.add_argument("--commit")
    x.add_argument("--overwrite", action="store_true")
    x.add_argument("--thermal-kernel", action="store_true"); x.add_argument("--thermal-hal", action="store_true")
    x.add_argument("--thermal-conf")
    x = sp.add_parser("telemetry"); x.add_argument("captures", nargs="+")
    x.add_argument("--max-age", type=int, default=MAX_AGE_S); x.add_argument("--now", type=float, help=argparse.SUPPRESS)
    x = sp.add_parser("compare"); x.add_argument("--dumpsys", required=True); x.add_argument("--sysfs", required=True)
    x.add_argument("--max-age", type=int, default=MAX_AGE_S); x.add_argument("--now", type=float, help=argparse.SUPPRESS)
    a = ap.parse_args()
    try:
        if a.cmd == "commands":
            print(COMMANDS, end="")
        else:
            {"artifact": artifact, "telemetry": telemetry, "compare": compare}[a.cmd](a)
    except Stop as e:
        print(f"STOP: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
