#!/usr/bin/env python3
"""Replay actual probe transition methods on a host JVM, with fake callbacks/time.

This checks deadline/pairing logic, not Android Camera2 or hardware delivery.
"""
from pathlib import Path
import subprocess
import tempfile

here = Path(__file__).resolve().parent
source = (here / 'CaptureActivity.java').read_text()
java = Path('/nix/store/fnshd55b29s6z88mfl3ilmdqfb7l3dqi-openjdk-21.0.7+6/bin')

def method(name):
    start = source.index('    private void ' + name + '(')
    pos = source.index('{', start)
    depth = 1
    end = pos + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]

harness = r'''
import java.util.*;
public class WarmupReplay {
    static final String TAG = "test";
    static class Log { static void i(String t, String m) {} }
    static class SystemClock { static long now; static long elapsedRealtime() { return now; } }
    static class Handler {
        ArrayDeque<Runnable> posts = new ArrayDeque<>();
        long deadline;
        void post(Runnable r) { posts.add(r); }
        void postDelayed(Runnable r, long ms) { deadline = ms; }
        void removeCallbacks(Runnable r) { deadline = 0; }
        void drain() { while (!posts.isEmpty()) posts.remove().run(); }
    }
    final Handler handler = new Handler();
    final Runnable timeout = this::onTimeout;
    boolean done, warming = true, warmImage, warmResult, warmupTimedOut;
    boolean imageSaved, captureCompleted, passed;
    int warmFrames, stableAeFrames;
    long warmStartMs;
    long captureStartMs;
    String stage = "warming_ae", reason;
    Map<String,Object> result = new HashMap<>();
    ArrayList<Boolean> requests = new ArrayList<>();
    void put(String k, Object v) { result.put(k, v); }
    void submitFrame(boolean preview) { requests.add(preview); warming = preview; stage = preview ? "warming_ae" : "capturing"; }
    void fail(String r) { done = true; reason = r; }
    void finishResult(boolean p, String r) { done = true; passed = p; reason = r; }
    static WarmupReplay frame() {
        WarmupReplay p = new WarmupReplay(); p.warmFrames = 665;
        SystemClock.now = 60000; return p;
    }
    static void check(boolean v) { if (!v) throw new AssertionError(); }
    public static void main(String[] args) {
        WarmupReplay p = frame(); p.warmFrames = 16; p.stableAeFrames = 8;
        p.warmImage = p.warmResult = true; SystemClock.now = 4000;
        p.finishWarmFrame(); p.finishWarmFrame(); p.handler.drain();
        check(p.requests.equals(Arrays.asList(false)) && !p.warmupTimedOut);
        check(Boolean.TRUE.equals(p.result.get("ae_converged")) && p.handler.deadline == 15000);
        System.out.println("PASS converged preview pair submits exactly one still");
        for (boolean imageFirst : new boolean[]{true, false}) {
            p = frame(); p.warmImage = imageFirst; p.warmResult = !imageFirst;
            p.onTimeout(); p.handler.drain();
            check(p.warmupTimedOut && p.requests.isEmpty() && p.handler.deadline == 15000);
            p.warmImage = p.warmResult = true; p.finishWarmFrame();
            p.finishWarmFrame(); p.handler.drain();
            check(p.requests.equals(Arrays.asList(false)));
            check(Boolean.FALSE.equals(p.result.get("ae_converged")));
            p.imageSaved = true; p.captureCompleted = true; p.finishIfReady();
            check(p.passed && "capture_after_ae_timeout".equals(p.reason));
            System.out.println("PASS timeout drains " + (imageFirst ? "image-first" : "result-first") + " preview and keeps unconverged still");
        }
        p = frame(); p.onTimeout(); p.onTimeout();
        check(p.done && "timeout_warming_ae".equals(p.reason) && p.requests.isEmpty());
        System.out.println("PASS stalled callback drain has bounded failure");
        p = frame(); p.stage = "opening"; p.onTimeout();
        check(p.done && "timeout_opening".equals(p.reason));
        p = frame(); p.warmFrames = 15; p.stableAeFrames = 7;
        p.warmImage = p.warmResult = true; p.finishWarmFrame(); p.handler.drain();
        check(p.requests.equals(Arrays.asList(true)));
        System.out.println("PASS setup failure and incomplete convergence retain prior behavior");
        p = frame(); SystemClock.now = 0; p.armWarmupTimeout();
        check(p.handler.deadline == 60000);
        SystemClock.now = 40000; p.armWarmupTimeout();
        check(p.handler.deadline == 20000);
        SystemClock.now = 60000; p.onTimeout();
        check(p.handler.deadline == 15000);
        SystemClock.now = 75000; p.onTimeout();
        check(p.done && !p.passed);
        System.out.println("PASS slow setup cannot extend capture past the host deadline");
    }
}
'''
harness = harness.replace('    public static void main(', '\n'.join(method(n) for n in
    ['onTimeout', 'finishWarmFrame', 'finishIfReady', 'armWarmupTimeout']) + '\n    public static void main(')
with tempfile.TemporaryDirectory(prefix='a9-warmup-replay-') as work:
    target = Path(work) / 'WarmupReplay.java'
    target.write_text(harness)
    subprocess.run([java / 'javac', '--release', '8', target], check=True)
    subprocess.run([java / 'java', '-cp', work, 'WarmupReplay'], check=True)
