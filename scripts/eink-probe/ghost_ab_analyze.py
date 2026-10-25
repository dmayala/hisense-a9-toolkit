#!/usr/bin/env python3
"""ghost_ab_analyze.py: offline analysis for the ghost A/B validation (ghost_ab.py on mainline, ghost_stock.py on stock).

  ghost_ab_analyze.py check-clean --calib C --ref PROBE_DIR --probe PROBE_DIR [--threshold 0.02]
      used in-session by ghost_ab.py: the last 0.6 s of camera frames of a probe run (phase A: home after a
      force_clear) against the reference run's (the baseline's double-cleared home). Prints one JSON line
      {"score": s, "clean": s <= threshold}. score = mean |frame - ref| / ref white level, both exposure-normalised.
  ghost_ab_analyze.py analyze --calib C SESSION_DIR [--stock STOCK_DIR] [--out DIR]
      per-repetition ghost scores and the pre-registered comparisons (below). Writes DIR/ghost.json, ghost.md.

Exposure normalisation (camera exposure / white balance are not locked): every camera frame is divided by the mean of a
fixed OFF-GLASS patch (camera coordinates: the white mount left of the phone, DEFAULT_OFFGLASS; --offglass overrides),
then warped to panel coordinates with the calibration homography. Frames are median-stacked over the last 0.6 s of a
phase (the settled state; >= 3 frames at 15 fps).

Per repetition (phases B = after the launcher -> Settings transition, C = after force_clear on the same page):
  mask   blank pixels of C: within 6 % of C's white level (90th percentile), eroded 7 px, y 60-1600 (no status/nav bar)
  G      mean |B - C| / white over the mask            (any residual, both signs)
  Gd     mean max(C - B, 0) / white over the mask      (darkening residual = ghost of darker previous content)
  Groi   Gd in x 560-740, y 1300-1560                  (the Storage/Battery area of the soak finding)
  Gp99   99th percentile of |B - C| / white over the mask
Camera saturation: the panel fraction of clipped pixels (>= 254) is reported per repetition ("saturated"); above
about 0.01 the exposure normalisation is not valid for that repetition (the camera, not the glass).
Pre-registered comparisons and decision table: review 89i (C3), verbatim in PREREG below and in every ghost.md.
Visual check: the per-rep B/C crops are written as one sheet per rep (ghost-sheets/) for review; numbers order them.
"""
import argparse
import glob
import json
import math
import os
import re
import statistics
import sys

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probe_lib as PL  # noqa: E402

ROI = (560, 1300, 740, 1560)
SAT_LIMIT = 0.01

# Review 89i section 1, item 1 (C3): fixed before capture, quoted verbatim; the code below implements it.
PREREG = """Pre-registered (review 89i, C3), verbatim:
- **P1 (mainline no-dip vs stock) becomes descriptive.** The stock run cannot reproduce the mainline history:
  - slot A's launcher is the user's Niagara, not Launcher3;
  - Settings carries the user's data;
  - the per-app stock E Ink mode is whatever a9service / EpdManager selects for these apps;
  - stock's `epd_force_clear` is a mode-dependent manual clear (Smooth same-mode: five type-6 passes plus the final
    pass, `runs/eink-stock-policy.md`), not a GC16;
  - the frontlight is not held.

  A "stock-like" or "worse than stock" verdict over unmatched history would be over-interpretation. This is the
  follow-up's own caveat ("otherwise label the control mainline Smooth"). Report the stock median and IQR of Gd
  and Groi, plus every stock sheet, labelled "stock, own launcher and policy". That still answers the useful
  question: does stock also leave launcher remnants on Settings?
- **P2 (dip vs no-dip on mainline): agreed, as the deciding comparison.** The rule is "dip-related worsening" if
  median Gd(dip) > median Gd(no-dip) + max(2 x MAD(no-dip), 0.003), with these refinements:
  - **"No dip-related worsening shown" needs at least 3 dip repetitions.** With exactly 2 the result is
    "inconclusive", and one more identical session may be pooled (it needs no new review if nothing changes).
  - Groi (the soak ROI) is reported alongside, and the same rule is applied to it as a secondary check.
  - **Visual rule:** if any dip sheet shows coherent launcher shapes clearly stronger than every no-dip sheet, the
    result is "dip-related worsening" whatever the numbers say. Every sheet is viewed: dip, no-dip and stock.
  - **"Dip" = a wf-end low during phase B only** (settled, re-read-high, or unsettled in arm 0). Repetitions with
    a low in phase A or C are listed separately and excluded from both groups, because a low during the clean-up
    clears could contaminate the reference.
- **P3 (settle 100 vs 0): descriptive.** Agreed (section 1, item 4).
- **Decision table, fixed now:**
  - P2 "no worsening" (>= 3 dips): the soak STOP's ghost is ordinary mainline Smooth history, not settle- or
    dip-related. The settle default review may proceed. The ghost becomes a separate image-quality item, compared
    descriptively with stock.
  - P2 "worsening": no default. The dip mechanism needs work.
  - "Inconclusive": pool one more session."""


def load_calib(p):
    c = json.load(open(p))
    H = np.asarray(c["H"], np.float64)
    q = np.asarray(c.get("quad_tl_tr_br_bl") or [[0, 0], [1, 0], [1, 1], [0, 1]], np.float64)
    return H, q


# The off-glass patch of THIS camera rig (1920x1080 frames, calib quad x 704-1105): the white mount left of the phone,
# x 605-630, y 300-700 (MEASURED on soak/F6 frames: uniform 121-145 grey, std < 3 across the column; x 614-674 would
# straddle the mount edge). Re-check it when the rig or the calibration changes (--offglass).
DEFAULT_OFFGLASS = (605, 300, 630, 700)


def offglass_box(q, override=None):
    if override:
        return tuple(int(v) for v in override.split(","))
    return DEFAULT_OFFGLASS


def index(probe):
    p = os.path.join(probe, "frames", "index.jsonl")
    out = []
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def settled_stack(probe, H, box, last_s=0.6):
    """median of the exposure-normalised, warped frames of the last last_s seconds of a probe run"""
    idx = index(probe)
    if not idx:
        return None, 0
    t1 = idx[-1]["mono"]
    sel = [f for f in idx if f["mono"] >= t1 - last_s]
    imgs = []
    sat = []
    for f in sel:
        img = cv2.imread(os.path.join(probe, "frames", "f%05d.jpg" % f["i"]))
        if img is None:
            continue
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
        sat.append(float((PL.warp(g, H) >= 254).mean()))      # clipped camera pixels break the normalisation
        x1, y1, x2, y2 = box
        pm = float(g[y1:y2, x1:x2].mean())
        if pm <= 1.0:
            pm = 1.0
        imgs.append(PL.warp(g / pm, H))
    if not imgs:
        return None, 0
    settled_stack.last_sat = max(sat) if sat else 0.0
    return np.median(np.stack(imgs), axis=0), len(imgs)


def white_level(img):
    return float(np.percentile(img[60:1600, :], 90))


def blank_mask(clean):
    w = white_level(clean)
    m = (np.abs(clean - w) <= 0.06 * w).astype(np.uint8)
    m[:60, :] = 0
    m[1600:, :] = 0
    m = cv2.erode(m, np.ones((15, 15), np.uint8))
    return m.astype(bool), w


def ghost_scores(post, clean):
    m, w = blank_mask(clean)
    if not m.any() or w <= 0:
        return None
    d = (post - clean) / w
    dark = np.maximum(-d, 0)
    signed = -d[m]
    offset = float(np.median(signed))
    x1, y1, x2, y2 = ROI
    rm = m[y1:y2, x1:x2]
    return {"G": float(np.abs(d)[m].mean()), "Gd": float(dark[m].mean()),
            "Groi": float(dark[y1:y2, x1:x2][rm].mean()) if rm.any() else None,
            "Gp99": float(np.percentile(np.abs(d)[m], 99)), "mask_px": int(m.sum()), "white": w,
            "SignedOffset": offset, "CenteredL1": float(np.abs(signed - offset).mean())}


def cmd_check_clean(a):
    H, q = load_calib(a.calib)
    box = offglass_box(q, a.offglass)
    ref, nr = settled_stack(a.ref, H, box)
    cur, nc = settled_stack(a.probe, H, box)
    if ref is None or cur is None:
        print(json.dumps({"score": None, "clean": False, "error": "no frames (ref %d, run %d)" % (nr, nc)}))
        return 0
    w = white_level(ref) or 1.0
    s = float(np.abs(cur - ref).mean() / w)
    print(json.dumps({"score": round(s, 5), "clean": s <= a.threshold, "frames": [nr, nc]}))
    return 0


def mad(xs):
    if not xs:
        return 0.0
    m = statistics.median(xs)
    return statistics.median([abs(x - m) for x in xs])


def iqr(xs):
    if len(xs) < 2:
        return 0.0
    q = np.percentile(xs, [25, 75])
    return float(q[1] - q[0])


def analyse_dir(d, H, box, sheets, label):
    reps = []
    meta = None
    for name in ("ghost-reps.json", "stock-reps.json"):
        p = os.path.join(d, name)
        if os.path.exists(p):
            meta = json.load(open(p))
            break
    for r in (meta or {}).get("reps", []):
        k = r["rep"]
        rec = {"src": label, "rep": k, "arm": r.get("arm"), "clean": r.get("clean"), "dip": r.get("dip"), "settled": r.get("settled"),
               "frontlight": r.get("frontlight"), "bin": r.get("bin"), "A_lows": r.get("A_lows", 0) or 0, "C_lows": r.get("C_lows", 0) or 0}
        for key in ("source_id", "source"):
            if key in r:
                rec[key] = r[key]
        if r.get("clean"):
            post, n1 = settled_stack(os.path.join(d, "runs", "%02d-B" % k, "probe"), H, box)
            s1 = settled_stack.last_sat
            clean, n2 = settled_stack(os.path.join(d, "runs", "%02d-C" % k, "probe"), H, box)
            rec["saturated"] = round(max(s1, settled_stack.last_sat), 4)
            if post is not None and clean is not None:
                rec.update(ghost_scores(post, clean) or {})
                rec["frames"] = [n1, n2]
                if sheets:
                    os.makedirs(sheets, exist_ok=True)
                    w = white_level(clean) or 1.0
                    to8 = lambda x: np.clip(x / w * 230, 0, 255).astype(np.uint8)  # noqa: E731
                    diff = np.clip(128 + (post - clean) / w * 1280, 0, 255).astype(np.uint8)
                    sheet = np.hstack([cv2.resize(i, (412, 824)) for i in (to8(post), to8(clean), diff)])
                    cv2.imwrite(os.path.join(sheets, "%s-rep%02d.png" % (label, k)), sheet)
                    rec["sheet"] = "%s-rep%02d.png" % (label, k)
        reps.append(rec)
    return reps


def scored(r):
    """valid for scoring: clean start, frames, camera not saturated (review 89i section 3)"""
    return r.get("Gd") is not None and (r.get("saturated") or 0) <= SAT_LIMIT


def excluded_ac(r):
    """a low in phase A or C (a clean-up clear): excluded from both P2 groups (review 89i C3)"""
    return r["src"] == "mainline" and (r.get("A_lows", 0) > 0 or r.get("C_lows", 0) > 0)


def group(reps, cond, key="Gd"):
    return [r[key] for r in reps if scored(r) and r.get(key) is not None and cond(r)]


def p2_rule(dip, nodip, stats_nodip_median, stats_nodip_mad):
    """review 89i C3: worsening if median(dip) > median(no-dip) + max(2 MAD, 0.003) (needs >= 2 dips);
    "no dip-related worsening shown" needs >= 3 dips; otherwise inconclusive"""
    if len(dip) < 2 or not nodip:
        return {"result": "inconclusive", "why": "%d dip repetitions, %d no-dip (worsening needs >= 2, no worsening >= 3)" % (len(dip), len(nodip))}
    lim = stats_nodip_median + max(2 * stats_nodip_mad, 0.003)
    md = statistics.median(dip)
    if md > lim:
        return {"result": "dip-related worsening", "dip_median": md, "limit": lim, "n_dip": len(dip)}
    if len(dip) >= 3:
        return {"result": "no dip-related worsening shown", "dip_median": md, "limit": lim, "n_dip": len(dip)}
    return {"result": "inconclusive", "dip_median": md, "limit": lim, "n_dip": len(dip),
            "why": "exactly 2 dip repetitions: pool one more identical session"}


MATCHED_SOURCES = ("launcher", "dark", "white")
MATCHED_METRICS = ("G", "Gd", "Groi", "Gp99", "SignedOffset", "CenteredL1")


def matched_source_id(r):
    source = r.get("source")
    return r.get("source_id") or (source.get("id") if isinstance(source, dict) else None)


def matched_source_errors(r):
    """Fail closed on missing or contradictory source evidence; never infer a match."""
    source = r.get("source")
    if not isinstance(source, dict):
        return ["missing source metadata"]
    sid = matched_source_id(r)
    errors = []
    if sid not in MATCHED_SOURCES or source.get("id") != sid or r.get("source_id") != sid:
        errors.append("missing or inconsistent source_id/source.id")
    if source.get("verified") is not True:
        errors.append("source was not verified")
    component = source.get("launcher_component")
    if not isinstance(component, str) or not component.strip():
        errors.append("missing explicit launcher_component")
    roi = source.get("roi")
    if (not isinstance(roi, list) or len(roi) != 4 or
            not all(type(v) is int for v in roi) or
            not (0 <= roi[0] < roi[2] <= 824 and 0 <= roi[1] < roi[3] <= 1648)):
        errors.append("invalid source ROI")
    for key in ("source_frame_mean_luma", "wallpaper_mean_luma"):
        value = source.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            errors.append("missing or invalid " + key)
    profile = source.get("viewer_profile")
    if "viewer_profile" not in source:
        errors.append("missing explicit viewer_profile")
    if sid in ("dark", "white"):
        sha = source.get("fixture_sha256")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", sha):
            errors.append("missing or invalid fixture_sha256")
        if not isinstance(profile, list) or not profile or not all(isinstance(v, str) and v for v in profile):
            errors.append("missing explicit viewer_profile argv")
    elif profile is not None:
        errors.append("launcher viewer_profile must be null")
    return errors


def matched_analysis(reps, box, out):
    """Separate histories, retaining descriptive rows even when match evidence fails."""
    histories = {sid: {} for sid in MATCHED_SOURCES}
    comparisons = {}
    validation = []
    for r in reps:
        if "source" in r or "source_id" in r:
            validation.append({"src": r["src"], "rep": r["rep"], "source_id": matched_source_id(r),
                               "errors": matched_source_errors(r)})

    def usable(r):
        return r.get("clean") is True and scored(r) and not excluded_ac(r)

    def stats(rows):
        result = {"n": len(rows)}
        for key in MATCHED_METRICS:
            values = [r[key] for r in rows if type(r.get(key)) in (int, float) and math.isfinite(r[key])]
            result[key] = {"n": len(values), "median": statistics.median(values) if values else None,
                           "iqr": iqr(values), "mad": mad(values)}
        return result

    for sid in MATCHED_SOURCES:
        rr = [r for r in reps if matched_source_id(r) == sid and usable(r)]
        for platform in ("mainline", "stock"):
            histories[sid][platform] = stats([r for r in rr if r["src"] == platform])
        reasons = ["%s rep%s: %s" % (r["src"], r["rep"], error)
                   for r in rr for error in matched_source_errors(r)]
        # Compare every usable repetition, not a representative that could hide drift.
        keys = ["launcher_component", "roi", "viewer_profile"]
        if sid in ("dark", "white"):
            keys.append("fixture_sha256")
        if rr and not reasons:
            for key in keys:
                values = [r["source"][key] for r in rr]
                if any(value != values[0] for value in values[1:]):
                    reasons.append("source %s mismatch within/across platforms" % key)
        missing = [platform for platform in ("mainline", "stock") if not histories[sid][platform]["n"]]
        result = "refused" if reasons else ("reference pending" if missing else "descriptive")
        comparisons[sid] = {"result": result, "reasons": reasons, "missing_platforms": missing,
                            "matched_fixed_source": result == "descriptive" and sid != "launcher",
                            "pixel_match_established": False,
                            "label": ("launcher wallpaper histories remain unmatched; stratify by recorded luma; "
                                      "no exact pixel-match claim" if sid == "launcher" else
                                      "verified fixed-source metadata match; target/reference waveform histories remain descriptive")}
    verdict = {"P1": {"result": "descriptive by source only", "comparisons": comparisons},
               "P2": {"result": "inconclusive", "why": "matched-source quality trial: separate source histories; legacy pooled dip rule is not applied"},
               "decision": "collect and inspect matched results by source before any policy/driver fix proposal",
               "excluded_low_in_A_or_C": [{"src": r["src"], "rep": r["rep"]} for r in reps if excluded_ac(r)]}
    result = {"protocol": "matched-source-quality-v1", "reps": reps, "groups_by_source": histories,
              "source_validation": validation, "legacy_records_not_pooled": [
                  {"src": r["src"], "rep": r["rep"]} for r in reps if "source" not in r and "source_id" not in r],
              "verdict": verdict, "offglass_box": box, "roi": ROI,
              "prereg": PREREG, "prereg_applies": False}
    with open(os.path.join(out, "ghost.json"), "w") as f:
        json.dump(result, f, indent=1)
    fmt = lambda x: "-" if x is None else ("%.6f" % x if isinstance(x, float) else str(x))  # noqa: E731
    lines = ["# Ghost quality trial: source histories kept separate", "",
             "P1 is descriptive by source only. P2 is inconclusive: the legacy pooled dip decision does not apply.",
             "No policy/driver fix is justified before matched results. Legacy records are retained but never pooled into these groups.",
             "SignedOffset = median((C-B)/white) on the original C blank mask (positive darkening); CenteredL1 = mean absolute residual after subtracting that offset.", "",
             "| source | platform | n | median G | median Gd | median Groi | median Gp99 | median SignedOffset | median CenteredL1 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for sid, platforms in histories.items():
        for platform, st in platforms.items():
            lines.append("| %s | %s | %d | %s |" % (sid, platform, st["n"], " | ".join(fmt(st[k]["median"]) for k in MATCHED_METRICS)))
    lines += ["", "| source | comparison | missing platform | refusal reasons | scope |",
              "|---|---|---|---|---|"]
    for sid, comparison in comparisons.items():
        lines.append("| %s | %s | %s | %s | %s |" % (sid, comparison["result"], ", ".join(comparison["missing_platforms"]),
                     "; ".join(comparison["reasons"]).replace("|", "\\|"), comparison["label"]))
    lines += ["", "Every repetition retains its source metadata and luminance; launcher rows must remain stratified by their wallpaper/source luma.", "",
              "| platform | rep | source | clean | Gd | SignedOffset | CenteredL1 | source luma | wallpaper ROI luma | fixture SHA256 | sheet |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in reps:
        source = r.get("source") if isinstance(r.get("source"), dict) else {}
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["src"], r["rep"], matched_source_id(r) or "legacy", r.get("clean"), fmt(r.get("Gd")),
            fmt(r.get("SignedOffset")), fmt(r.get("CenteredL1")), fmt(source.get("source_frame_mean_luma")),
            fmt(source.get("wallpaper_mean_luma")), source.get("fixture_sha256") or "-", r.get("sheet", "-")))
    lines += ["", "Source validation (all rows, including unscored):", "", "```json", json.dumps(validation, indent=1), "```", "",
              "Historical review 89i preregistration, verbatim; NOT applicable to this matched-source quality trial:", "", PREREG]
    with open(os.path.join(out, "ghost.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("ghost quality analysis: %s" % os.path.join(out, "ghost.md"))
    return 0


def cmd_analyze(a):
    H, q = load_calib(a.calib)
    box = offglass_box(q, a.offglass)
    out = a.out or os.path.join(a.session, "ghost-analysis")
    os.makedirs(out, exist_ok=True)
    sheets = os.path.join(out, "ghost-sheets")
    reps = analyse_dir(a.session, H, box, sheets, "mainline")
    if a.stock:
        reps += analyse_dir(a.stock, H, box, sheets, "stock")
    if any("source_id" in r or "source" in r for r in reps):
        return matched_analysis(reps, box, out)
    ml = lambda r: r["src"] == "mainline" and not excluded_ac(r)  # noqa: E731
    conds = {"stock": lambda r: r["src"] == "stock",
             "mainline_nodip": lambda r: ml(r) and not r.get("dip"),
             "mainline_dip": lambda r: ml(r) and r.get("dip"),
             "s100_nodip": lambda r: ml(r) and r.get("arm") == 100 and not r.get("dip"),
             "s100_dip": lambda r: ml(r) and r.get("arm") == 100 and r.get("dip"),
             "s0_nodip": lambda r: ml(r) and r.get("arm") == 0 and not r.get("dip"),
             "s0_dip": lambda r: ml(r) and r.get("arm") == 0 and r.get("dip")}
    st = lambda v: {"n": len(v), "median": statistics.median(v) if v else None, "iqr": iqr(v), "mad": mad(v)}  # noqa: E731
    stats = {k: st(group(reps, c)) for k, c in conds.items()}
    stats_roi = {k: st(group(reps, c, "Groi")) for k, c in conds.items()}
    g = {k: group(reps, c) for k, c in conds.items()}
    gr = {k: group(reps, c, "Groi") for k, c in conds.items()}
    verdict = {
        "P1": {"result": "descriptive" if a.stock else "stock reference pending", "label": "stock, own launcher and policy",
               "stock_Gd": stats["stock"], "stock_Groi": stats_roi["stock"],
               "mainline_nodip_Gd": stats["mainline_nodip"], "mainline_nodip_Groi": stats_roi["mainline_nodip"]},
        "P2": p2_rule(g["mainline_dip"], g["mainline_nodip"], stats["mainline_nodip"]["median"] or 0.0, stats["mainline_nodip"]["mad"]),
        "P2_Groi_secondary": p2_rule(gr["mainline_dip"], gr["mainline_nodip"], stats_roi["mainline_nodip"]["median"] or 0.0,
                                     stats_roi["mainline_nodip"]["mad"]),
        "P2_visual_rule": "PENDING: view every sheet (dip, no-dip, stock); coherent launcher shapes in any dip sheet clearly stronger "
                          "than in every no-dip sheet make P2 'dip-related worsening' whatever the numbers say",
        "P3": {"result": "descriptive", "s100_dip": stats["s100_dip"], "s0_dip": stats["s0_dip"],
               "s100_nodip": stats["s100_nodip"], "s0_nodip": stats["s0_nodip"]},
        "excluded_low_in_A_or_C": [r["rep"] for r in reps if excluded_ac(r)],
        "invalid_not_scored": [r["rep"] for r in reps if r["src"] == "mainline" and r.get("clean") and not scored(r)],
    }
    res_p2 = verdict["P2"]["result"]
    verdict["decision"] = {"no dip-related worsening shown": "the soak STOP's ghost is ordinary mainline Smooth history, not settle- or dip-related; "
                                                             "the settle default review may proceed; the ghost becomes a separate image-quality item, "
                                                             "compared descriptively with stock",
                           "dip-related worsening": "no default; the dip mechanism needs work",
                           "inconclusive": "pool one more session"}[res_p2] + " (subject to the visual rule)"
    res = {"reps": reps, "groups": stats, "groups_roi": stats_roi, "verdict": verdict, "offglass_box": box, "roi": ROI, "prereg": PREREG}
    json.dump(res, open(os.path.join(out, "ghost.json"), "w"), indent=1)
    L = ["# ghost A/B analysis (exposure-normalised against an off-glass patch; Gd = darkening residual of B vs the cleared C)", "",
         "| src | rep | arm | clean | dip | settled | G | Gd | Groi | Gp99 | saturated | frames | sheet |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    f = lambda x: "-" if x is None else ("%.4f" % x if isinstance(x, float) else str(x))  # noqa: E731
    for r in reps:
        L.append("| %s | %d | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (r["src"], r["rep"], r.get("arm"), r.get("clean"), r.get("dip"),
                 r.get("settled"), f(r.get("G")), f(r.get("Gd")), f(r.get("Groi")), f(r.get("Gp99")), r.get("saturated", "-"), r.get("frames", "-"),
                 r.get("sheet", "-")))
    L += ["", "| group | n | median Gd | IQR | MAD | median Groi | IQR Groi |", "|---|---|---|---|---|---|---|"]
    for k, s in stats.items():
        sr = stats_roi[k]
        L.append("| %s | %d | %s | %s | %s | %s | %s |" % (k, s["n"], f(s["median"]), f(s["iqr"]), f(s["mad"]), f(sr["median"]), f(sr["iqr"])))
    L += ["", "**P2 (deciding): %s** %s" % (verdict["P2"]["result"], {k: v for k, v in verdict["P2"].items() if k != "result"}),
          "P2 secondary (Groi): %s" % verdict["P2_Groi_secondary"],
          "P2 visual rule: %s" % verdict["P2_visual_rule"],
          ("P1 (descriptive, \"stock, own launcher and policy\"): stock Gd %s, Groi %s; mainline no-dip Gd %s" % (
              verdict["P1"]["stock_Gd"], verdict["P1"]["stock_Groi"], verdict["P1"]["mainline_nodip_Gd"]) if a.stock else
           "P1: stock reference pending (no --stock; run ghost_stock.py later by hand and re-run this analysis with --stock); "
           "mainline no-dip Gd %s" % verdict["P1"]["mainline_nodip_Gd"]),
          "P3 (descriptive): settle 100 dips %s, settle 0 dips %s" % (stats["s100_dip"], stats["s0_dip"]),
          "Excluded (a low in phase A or C): %s; clean but not scored (stale/saturated/no frames): %s" % (
              verdict["excluded_low_in_A_or_C"], verdict["invalid_not_scored"]),
          "**Decision (table below, before the visual rule): %s**" % verdict["decision"],
          "", PREREG]
    open(os.path.join(out, "ghost.md"), "w").write("\n".join(L) + "\n")
    print("ghost analysis: %s" % os.path.join(out, "ghost.md"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="ghost A/B analysis (offline)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check-clean")
    c.add_argument("--calib", required=True)
    c.add_argument("--ref", required=True)
    c.add_argument("--probe", required=True)
    c.add_argument("--threshold", type=float, default=0.02)
    c.add_argument("--offglass")
    n = sub.add_parser("analyze")
    n.add_argument("session")
    n.add_argument("--calib", required=True)
    n.add_argument("--stock")
    n.add_argument("--out")
    n.add_argument("--offglass")
    a = ap.parse_args(argv)
    return cmd_check_clean(a) if a.cmd == "check-clean" else cmd_analyze(a)


if __name__ == "__main__":
    sys.exit(main())
