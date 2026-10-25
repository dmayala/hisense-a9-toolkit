# Mainline E Ink: Clear default and HV-hold latency

Status 2026-10-17 (fix round after review 66). Offline only; nothing here was run on a phone.

## 1. Clear as the mainline default (implemented, host-tested)
- Kernel default stays Smooth 518; the daily slot-A ROM (a9service) is untouched.
- `build-vendor.sh` writes `ro.vendor.a9ml.eink.default_mode=515` into the mainline
  vendor `build.prop`. `a9-eink-compat` reads it at start (`b/c/s/p` or 513/515/518/521)
  and writes `mode` through the normal deferred-retry path. Any later app request wins;
  an absent/invalid value writes nothing. Thresholds, clear and power are not touched.
- Revert: delete the property line or set 518.
- Open risk: if the mainline a9service sends `s` at its own start (refresh_setting
  default 2) it overrides this. Not measured; check the first boot's compat log.
- Persistence: the app's `refresh_setting` fallback "2" (Smooth) and its startup `applyMode()` can request Smooth after compat selects Clear. For a controlled Clear reference set `ro.vendor.a9ml.eink.lock_mode=1` (compat then ignores b/c/s/p requests; off by default; thresholds and clear stay live), or select Clear in the app (index 1). Compat restart reapplies the property.
- Clear costs more transport frames than Smooth (bin 10: 30 vs 8, about 353 vs 94 ms at 85 Hz); lower CPU conditioning does not prove lower end-to-end phone latency.
- Test: `test_default_mode_property`, `test_lock_mode_ignores_app_mode_requests` in `scripts/mainline/eink-compat/test_compat.py`.

## 2. HV-hold latency: POWER-PATH CANDIDATE (needs independent review)
Measured cause (demo boot): 8 power-ups each waited ~730 ms (`min_off_ms` 1000) because
taps arrived 3.0-4.0 s apart: just after `hv_hold_ms` (3000) powered the pipeline down.
Stock holds 2-3 s too (docs/mainline-eink-driver.md), so stock taps in that gap pay a
charge-up cost as well; this is a deliberate departure for responsiveness.

Candidate `scripts/mainline/kernel/candidates/0048-*.candidate` v2 (not in the series), OPT-IN
(`hv_hold_interactive_ms` default 0; set 8000 to enable): when a real update ends less than
`hv_interactive_window_ms` (10 s) after the previous real one, THAT session's idle hold
becomes max(`hv_hold_ms`, `hv_hold_interactive_ms`, clamp 10 s). `hv_hold_ms=0` is never extended.
Review 66 fixes:
- Budget: the idle wait is clipped to the remaining continuous-on time of
  min(`hv_max_ms`, 15000) (15 s when `hv_max_ms` is the 60000 default), so the worker
  closes the session orderly with at least 5 s margin before the unchanged FP9929A 20 s hard
  cap, without needing another kick. No cap raised, `min_off_ms` untouched.
- Session binding: the boost is recomputed at every real output and is valid only while
  the pipeline is on in the same session (`tx.on_since`); it is therefore dead after any
  power-down (idle, `hv_max_ms` shutdown, suspend, abort, fault, rollover). History
  is cleared on suspend and `hv_fault`. Defined: previous-output **time** may cross a
  session boundary (so the tap after an idle power-down can qualify, which is the point);
  the boost itself never does.
- The idle-expiry power-off does NOT sample live rails (review 66; the earlier text here
  was wrong). Only the expired-hold-plus-kick branch does. Longer HV idle is electrically unqualified.
Unchanged: between-job `hv_max_ms` check, `max_on_ms`, PG latch, VCOM, sequencing.
Tests: `bash tests/mainline-a9eink-target/run-boost.sh` (9 single-shutdown cases plus the
review-66 multi-session regressions: idle-budget, default-cap, rollover, cross-session, suspend; the
mock keeps running across shutdowns). Cost: HV stays up up to 5 s longer in bursts.
Needs another independent power review before any phone use.

## 2b. 0049b: FP9929A minimum off time 1000 -> 500 ms (POWER-PATH, unreviewed)
Supersedes the withdrawn 0049 (200 ms), rejected by review 67: stock's ~200 ms is a composer
guard, not a measured discharge, and the FP9929A datasheet (fig. 5, p. 11) gives VLD >= 500 ms.
`scripts/mainline/kernel/candidates/0049b-regulator-*.candidate`: `fp9929a.min_off_ms` default
1000 -> 500; range stays 500..10000 (0 and <500 refused). Default only; no new knob or code path.
- **Why 1000 originally:** patch 0025, 2x the datasheet's 500 ms with margin, chosen before stock was
  compared. The stock kernel was measured re-powering after 257/277/616 ms, so 500 is still stricter
  than stock. **Why 500:** exactly the datasheet minimum, no lower.
- **Expected gain:** a restart right after a 3 s idle power-down waits up to 500 ms instead of up to
  1000 ms; the measured ~730 ms waits become ~230 ms (about 500 ms saved per cold restart). Not
  measured on a phone.
- **Companion (must go with it):** `0049b-companion-a9-eink-seq.patch.candidate` sets `FP_MIN_OFF_MS=500`
  in `initramfs/a9-eink-seq` (exact-equality preflight at `pre.fp_min_off`, and the shell backstop
  `cap + 1000 + FP_MIN_OFF_MS` follows the constant) and the simulator fixture `sim-lib.sh`.
  Not applied to production: against the current 1000 kernel the preflight would (correctly) refuse.
- **Unchanged:** shutdown order, PG wait/latch, `max_on_ms` 20000, gate checks during the wait, VCOM
  readback, a9_eink admission.
- **Tests:** `A9_CAND=<kernel candidate> bash tests/mainline-fp9929a/run.sh` (case (o) default/floor,
  new (o2): wait about 500 ms not 1000, PG still required, 499 refused);
  `bash tests/mainline-eink-seq/run-0049b.sh` applies the companion to a temp copy and runs the full
  BusyBox simulation plus a 61-check preflight matrix (500 passes; 499, 1000, 250, 200, 0, malformed,
  absent fail closed: pre FAIL, abort latched, no modprobe, no pattern, later e3 refused).

## 2c. Clear default robustness
`lock_mode=1` covers every path that changes the mode: compat only writes `mode` from the default
property and from socket `b/c/s/p`, and lock ignores all socket requests, so a9service's startup
`applyMode()`, per-app fallback ("2") and focus restoration cannot override Clear
(`test_lock_mode_ignores_app_mode_requests`). It is opt-in (the in-app mode menu stops working).
The coherent alternative (report rec. 3) is changing the mainline a9service fallback and migration,
which is app work outside this branch.

## 3. Conversion cost
From eink-quality-2 (x86 host, loaded, not phone numbers): framebuffer-to-grey
12-33 ms per full frame (tiling gave no gain); conditioning Smooth 69 ms, Balanced
58 ms, Clear 5.5 ms. Clear is about 12x cheaper than Smooth in the CPU path, which
supports the Clear default for latency as well as quality. Phone A-series cores are
slower, so expect several times these figures; not measured.
