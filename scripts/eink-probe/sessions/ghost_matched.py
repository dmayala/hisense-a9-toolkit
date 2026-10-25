"""Opt-in matched histories. Construction/--plan never contacts a device.

Only start() stages fixtures and, on stock, temporarily changes preferred HOME.
The owning session MUST close() in its error/signal cleanup path. An interrupted
transport or SIGKILL cannot guarantee restoration: original HOME is durable on
the host before the change, and a failed verification is reported as a failure.
"""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import matched_source as F
import ghost_recovery as Recovery
import ghost_viewer as Viewer
from probe_output import public_output

HOME_QUERY = "cmd package resolve-activity --brief -c android.intent.category.HOME -a android.intent.action.MAIN"
COMPONENT = re.compile(r"[A-Za-z][A-Za-z0-9_.]*/[A-Za-z0-9_.$]+\Z")


def canonical_component(component):
    package, activity = component.split("/", 1)
    return package + "/" + (package + activity if activity.startswith(".") else activity)


def add_arguments(ap):
    ap.add_argument("--matched-source", action="store_true", help="launcher/dark/white matched histories; opt-in")
    ap.add_argument("--launcher-component", help="same explicit Trebuchet/Launcher3 component on both sides")
    ap.add_argument("--source-viewer", help="JSON am start argv with explicit -n and {path}/{uri}; no implicit viewer")
    ap.add_argument("--source-roi", default="32,100,792,1560", help="identical portrait pixel-validation ROI on both sides")
    ap.add_argument("--wallpaper-roi", default="32,100,792,1560", help="predeclared wallpaper/source luminance ROI; no segmentation")
    ap.add_argument("--source-wait-ms", type=int, default=4000)
    ap.add_argument("--recovery-marker", default=Recovery.DEFAULT_MARKER,
                    help="persistent stock HOME/owned-file recovery journal; shared across fresh output directories")
    ap.add_argument('--expected-stock-home-package',help='sealed stock session requires the original HOME package (Niagara)')


def roi(text):
    try:
        x1, y1, x2, y2 = map(int, text.split(","))
    except (ValueError, AttributeError):
        raise ValueError("ROI must be x1,y1,x2,y2")
    if not (0 <= x1 < x2 <= F.WIDTH and 0 <= y1 < y2 <= F.HEIGHT):
        raise ValueError("ROI is outside the portrait panel")
    return [x1, y1, x2, y2]


def validate(a):
    if not a.matched_source:
        return
    c = a.launcher_component or ""
    if not COMPONENT.fullmatch(c) or not c.startswith(("com.android.launcher3/", "org.lineageos.trebuchet/")):
        raise ValueError("--launcher-component must explicitly name Trebuchet/Launcher3")
    viewer = F.validate_viewer_template(a.source_viewer)
    if viewer[:2] != ["am", "start"] or "-n" not in viewer:
        raise ValueError("--source-viewer must be explicit am start -n argv")
    n = viewer.index("-n")
    if n + 1 >= len(viewer) or not COMPONENT.fullmatch(viewer[n + 1]):
        raise ValueError("viewer component is invalid")
    if a.settings_start != "warm":
        raise ValueError("matched histories require warm Settings starts")
    if a.ghost_reps < 6 or a.ghost_reps % 6:
        raise ValueError("matched --ghost-reps must be a positive multiple of 6 (12 recommended)")
    if not 3500 <= a.source_wait_ms <= 10000:
        raise ValueError("--source-wait-ms must be 3500..10000")
    roi(a.source_roi); roi(a.wallpaper_roi)
    if hasattr(a, "ghost_arms") and (len(a.ghost_arms.split(",")) != 1 or int(a.ghost_arms) != a.pg_settle_ms):
        raise ValueError("matched histories require one fixed --ghost-arms equal to --pg-settle-ms")


@contextlib.contextmanager
def cleanup_signals():
    old = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        for s in old:
            signal.signal(s, signal.SIG_IGN)
        yield
    finally:
        for s, handler in old.items():
            signal.signal(s, handler)


class MatchedRun:
    sources = ("launcher", "dark", "white")

    def __init__(self, a, adb, out, log, stock=False):
        validate(a)
        self.a, self.adb, self.out, self.log, self.stock = a, list(adb), Path(out), log, stock
        self.images, self.paths, self.owned = {}, {}, []
        self.original_home, self.home_touched, self.closed = None, False, False
        self.viewer = F.validate_viewer_template(a.source_viewer)
        self.token = uuid.uuid4().hex
        self.own_journal = stock and getattr(a, '_home_recovery', None) is None
        self.journal = (getattr(a, '_home_recovery', None) or
                        Recovery.HomeRecovery(getattr(a, 'recovery_marker', Recovery.DEFAULT_MARKER), self.adb, log)) if stock else None
        self.cleanup_failures = []
        if self.journal:
            self.journal.log = log

    def command(self, args, timeout=30):
        try:
            p = subprocess.run(self.adb + args, capture_output=True, text=True, timeout=timeout,
                               stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise RuntimeError('matched transport command timed out') from None
        if p.returncode:
            raise RuntimeError("matched transport command failed (rc %d)" % p.returncode)
        return p.stdout.strip()

    def shell(self, text):
        return self.command(["shell", text])

    def resolve_home(self):
        text = self.shell(HOME_QUERY)
        lines = [line.strip() for line in text.splitlines() if COMPONENT.fullmatch(line.strip())]
        if len(lines) != 1:
            raise RuntimeError("preferred HOME did not resolve to exactly one component")
        return lines[0]

    def receipt(self, **extra):
        record = {"protocol": "matched-source-v1", "original_home": self.original_home,
                  "requested_home": self.a.launcher_component, "home_change_armed": self.home_touched,
                  "token": self.token, "owned_device_paths": self.owned,
                  "recovery_marker": str(self.journal.marker) if self.journal else None,
                  "sources": self.images, "viewer_profile": self.viewer,
                  "source_roi": roi(self.a.source_roi), "wallpaper_roi": roi(self.a.wallpaper_roi), **extra}
        self.out.mkdir(parents=True, exist_ok=True)
        Recovery.atomic_json(self.out / "matched-source.json", record)

    def start(self):
        if self.journal and self.journal.recover_pending():
            raise RuntimeError('pending HOME recovery blocks new session')
        if self.viewer[self.viewer.index('-n') + 1] == Viewer.COMPONENT:
            reply = self.shell('cmd package resolve-activity --brief -a android.intent.action.VIEW -t image/png -n ' + Viewer.COMPONENT)
            resolved = [line.strip() for line in reply.splitlines() if COMPONENT.fullmatch(line.strip())]
            if len(resolved) != 1 or canonical_component(resolved[0]) != Viewer.COMPONENT:
                raise RuntimeError('Glimpse VIEW activity is unavailable; no HOME change or staging attempted')
        self.original_home = self.resolve_home()
        expected = getattr(self.a,'expected_stock_home_package',None)
        if self.stock and expected and self.original_home.split('/',1)[0] != expected:
            raise RuntimeError('original stock HOME is not the expected package; no HOME change attempted')
        self.receipt(home_restored=None)
        if self.stock:
            # Arm and persist rollback BEFORE the command: a failed command can
            # still have changed HOME before the transport failure was noticed.
            self.home_touched = True
            self.receipt(home_restored=None)
            self.journal.begin(self.original_home, self.a.launcher_component, self.token)
            self.shell("cmd package set-home-activity " + shlex.quote(self.a.launcher_component))
        if canonical_component(self.resolve_home()) != canonical_component(self.a.launcher_component):
            raise RuntimeError("preferred HOME is not the requested Launcher3/Trebuchet")
        for sid in ("dark", "white"):
            meta = F.write_source(str(self.out / "sources"), sid)
            path = "/data/local/tmp/ghost-matched-%s-%s.png" % (self.token, sid)
            F.validate_device_path(path)
            # No clobber and no broad cleanup. The pending path is remembered
            # before push, so partial transfers are removed on error as well.
            if self.shell("if [ -e %s ]; then echo EXISTS; else echo ABSENT; fi" % shlex.quote(path)) != "ABSENT":
                raise RuntimeError("temporary source path already exists or was not checked")
            self.owned.append(path)
            if self.journal:
                self.journal.remember(path)
            self.receipt(home_restored=None)
            self.command(["push", meta["path"], path], timeout=60)
            self.shell("chmod 0644 " + shlex.quote(path))
            check = self.shell("sha256sum " + shlex.quote(path)).split()
            if not check or check[0] != meta["sha256"]:
                raise RuntimeError("temporary source SHA256 readback mismatch")
            self.images[sid], self.paths[sid] = meta, path
            if self.viewer[self.viewer.index('-n') + 1] == Viewer.COMPONENT:
                private = Viewer.CACHE + '/ghost-matched-%s-%s.png' % (self.token, sid)
                if self.shell(Viewer.absent(private)) != 'ABSENT':
                    raise RuntimeError('viewer cache path exists; no clobber attempted')
                # Arm recovery before copying a byte into the app sandbox.
                self.owned.append(private)
                if self.journal:
                    self.journal.remember(private)
                self.receipt(home_restored=None)
                check = self.shell(Viewer.stage(path, private)).split()
                if not check or check[0] != meta['sha256']:
                    raise RuntimeError('viewer cache SHA256/owner/context verification failed')
                self.paths[sid] = private
        self.receipt(home_restored=None)

    def probe_viewer(self):
        """Launch both fixtures and verify actual pixels before any repetition.

        No optical camera, force-clear, settings/permission mutation or fallback
        activity. Exact am output is retained after device-identifier redaction.
        """
        for sid in ('dark', 'white'):
            tag = 'probe-viewer-' + sid
            directory = self.out / 'runs' / tag / 'probe' / 'screencap'
            directory.mkdir(parents=True, exist_ok=True)
            evidence = directory.parent / 'launch.json'
            record = dict(source=sid, verified=False, stdout='', stderr='', rc=None)
            try:
                try:
                    p = subprocess.run(self.adb + self.source_step(sid)['adb'], capture_output=True,
                                       timeout=60, stdin=subprocess.DEVNULL)
                except subprocess.TimeoutExpired as e:
                    record.update(stdout=public_output(e.stdout, self.adb), stderr=public_output(e.stderr, self.adb), timed_out=True)
                    raise RuntimeError('viewer launch timed out') from None
                record.update(rc=p.returncode, stdout=public_output(p.stdout, self.adb), stderr=public_output(p.stderr, self.adb))
                if p.returncode or re.search(r'(?mi)^\s*(?:Error(?: type \d+)?:|Exception|SecurityException)', record['stdout'] + '\n' + record['stderr']):
                    raise RuntimeError('viewer launch failed rc=%d' % p.returncode)
                time.sleep(self.a.source_wait_ms / 1000)
                p = subprocess.run(self.adb + ['exec-out', 'screencap', '-p'], capture_output=True,
                                   timeout=30, stdin=subprocess.DEVNULL)
                if p.returncode or not p.stdout.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise RuntimeError('viewer probe screencap failed')
                (directory / '00-viewer.png').write_bytes(p.stdout)
                record['source'] = self.capture_source(tag, sid)
                record['verified'] = True
            except Exception as error:
                record['error'] = public_output(str(error), self.adb)
                raise RuntimeError('viewer probe failed: %s; evidence=%s' % (record['error'], evidence)) from None
            finally:
                Recovery.atomic_json(evidence, record)
            self.log('viewer probe %s verified; evidence=%s' % (sid, evidence))

    def sequence(self, n):
        block = ["launcher", "dark", "white", "white", "dark", "launcher"]
        return (block * ((n + 5) // 6))[:n]

    def baseline_tag(self, sid):
        return "00-%s-A" % sid

    def source_step(self, sid):
        if sid == "launcher":
            args = ["shell", "input", "keyevent", "KEYCODE_HOME"]
        else:
            args = F.source_args(self.viewer, self.paths[sid])
        return {"name": "source_" + sid, "adb": args, "min_wait_ms": self.a.source_wait_ms,
                "quiet_ms": 800, "max_wait_ms": 12000}

    def capture_source(self, tag, sid):
        import cv2
        import numpy as np
        screenshots = sorted((self.out / "runs" / tag / "probe" / "screencap").glob("*.png"))
        if not screenshots:
            raise RuntimeError("matched source has no decoded screencap")
        path = screenshots[-1]
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None or image.shape[:2] != (F.HEIGHT, F.WIDTH):
            raise RuntimeError("source viewport is not 824x1648 portrait")
        grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
        x1, y1, x2, y2 = roi(self.a.source_roi)
        wx1, wy1, wx2, wy2 = roi(self.a.wallpaper_roi)
        patch = grey[y1:y2, x1:x2]
        mismatch, foreground = None, None
        text = self.shell("dumpsys activity activities | grep -E 'topResumedActivity|mResumedActivity' | tail -n 1")
        components = re.findall(r"[A-Za-z][A-Za-z0-9_.]*/[A-Za-z0-9_.$]+", text)
        foreground = components[-1] if components else None
        expected_component = self.a.launcher_component if sid == "launcher" else self.viewer[self.viewer.index("-n") + 1]
        if foreground is None or canonical_component(foreground) != canonical_component(expected_component):
            raise RuntimeError("source did not resume the requested launcher/viewer activity")
        if sid != "launcher":
            expected = cv2.imread(self.images[sid]["path"], cv2.IMREAD_GRAYSCALE).astype(np.float32) / 255.0
            mismatch = float((np.abs(patch - expected[y1:y2, x1:x2]) > 1.0 / 255).mean())
            if mismatch > 0.001:
                raise RuntimeError("source image not displayed 1:1 in validation ROI (mismatch %.4f)" % mismatch)
        return {"id": sid, "fixture_sha256": self.images[sid]["sha256"] if sid != "launcher" else None,
                "launcher_component": self.a.launcher_component,
                "viewer_profile": self.viewer if sid != "launcher" else None,
                "roi": roi(self.a.source_roi), "wallpaper_roi": roi(self.a.wallpaper_roi),
                "source_frame_mean_luma": float(grey[60:1600].mean()),
                "wallpaper_mean_luma": float(grey[wy1:wy2, wx1:wx2].mean()),
                "wallpaper_measure": "digital source-frame ROI proxy; not segmented wallpaper",
                "dark_fraction": float((patch <= 64 / 255).mean()),
                "white_fraction": float((patch >= 240 / 255).mean()),
                "pixel_mismatch_fraction": mismatch, "verified": True,
                "verification": "decoded fixture ROI" if sid != "launcher" else "resumed Launcher3/Trebuchet; wallpaper unmatched",
                "foreground_component": foreground,
                "screencap": str(path.relative_to(self.out)), "screencap_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def close(self):
        if self.closed:
            return self.cleanup_failures
        self.closed = True
        if self.journal:
            failures = []
            try:
                failures = self.journal.recover_pending()
                restored = self.journal.last_home_restored if self.home_touched else True
                self.receipt(home_restored=restored, cleanup_failures=failures)
            except Exception as e:
                failures.append('persistent HOME recovery: '+str(e))
                self.log('home restored: no; RECOVERY PENDING')
            finally:
                if self.own_journal:
                    self.journal.release()
            self.cleanup_failures = failures
            return failures
        failures, restored = [], not self.home_touched
        with cleanup_signals():
            if self.original_home is not None:
                try:
                    if self.home_touched:
                        self.shell("cmd package set-home-activity " + shlex.quote(self.original_home))
                    restored = canonical_component(self.resolve_home()) == canonical_component(self.original_home)
                    if not restored:
                        failures.append("preferred HOME verification mismatch")
                except Exception as e:
                    restored = False; failures.append("preferred HOME restore: %s" % e)
            self.log("home restored: " + ("yes" if restored else "no"))
            for path in self.owned:
                try:
                    if path.startswith(Viewer.CACHE + '/'):
                        self.shell(Viewer.remove(path))
                        if self.shell(Viewer.absent(path, 'REMOVED', 'PRESENT')) != 'REMOVED':
                            raise RuntimeError('temporary viewer cache source still present')
                        continue
                    self.shell("rm -f -- %s; if [ -e %s ]; then echo PRESENT; else echo REMOVED; fi" %
                               (shlex.quote(path), shlex.quote(path)))
                    # Check the outcome independently; rm can fail while the
                    # shell still returns success from its final echo.
                    if self.shell("if [ -e %s ]; then echo PRESENT; else echo REMOVED; fi" % shlex.quote(path)) != "REMOVED":
                        raise RuntimeError("temporary source still present")
                except Exception as e:
                    failures.append("source cleanup: %s" % e)
            self.receipt(home_restored=restored, cleanup_failures=failures)
        self.cleanup_failures = failures
        return failures
