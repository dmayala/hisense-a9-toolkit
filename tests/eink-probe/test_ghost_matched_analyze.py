"""Offline matched-history report checks; no capture pipeline or device commands."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/eink-probe"))
import ghost_ab_analyze as GA


def record(platform, sid, rep=1, gd=.1, dip=False):
    return {"src": platform, "rep": rep, "arm": 100, "clean": True, "dip": dip,
            "A_lows": 0, "C_lows": 0, "saturated": 0, "G": gd, "Gd": gd,
            "Groi": gd/2, "Gp99": gd*2, "SignedOffset": gd, "CenteredL1": .01,
            "source_id": sid, "source": {"id": sid, "verified": True,
                "fixture_sha256": None if sid == "launcher" else ("a" if sid == "dark" else "b")*64,
                "launcher_component": "com.android.launcher3/.Launcher",
                "viewer_profile": None if sid == "launcher" else ["viewer", "{path}"],
                "roi": [30, 60, 790, 1580], "source_frame_mean_luma": .3 if sid == "dark" else .9,
                "wallpaper_mean_luma": .25 if sid == "dark" else .95}}


class MatchedAnalysis(unittest.TestCase):
    def report(self, mainline, stock=None):
        with tempfile.TemporaryDirectory() as d:
            args = ["analyze", "mainline", "--calib", "unused", "--out", d]
            if stock is not None:
                args += ["--stock", "stock"]
            def analyze(path, H, box, sheets, label):
                return copy.deepcopy(mainline if label == "mainline" else stock)
            with mock.patch.object(GA, "load_calib", return_value=(np.eye(3), np.zeros((4,2)))), \
                    mock.patch.object(GA, "analyse_dir", side_effect=analyze):
                self.assertEqual(GA.main(args), 0)
            return json.loads((Path(d)/"ghost.json").read_text()), (Path(d)/"ghost.md").read_text()

    def test_three_sources_are_separate_and_legacy_dip_verdict_is_disabled(self):
        ml = [record("mainline", sid, i, gd=.1*(i+1), dip=True) for i,sid in enumerate(GA.MATCHED_SOURCES,1)]
        st = [record("stock", sid, i, gd=.01*i) for i,sid in enumerate(GA.MATCHED_SOURCES,1)]
        result, md = self.report(ml, st)
        self.assertEqual(set(result["groups_by_source"]), {"launcher", "dark", "white"})
        self.assertNotIn("groups", result)
        for original in ml+st:
            group = result["groups_by_source"][original["source_id"]][original["src"]]
            self.assertEqual(group["n"], 1)
            self.assertEqual(group["Gd"]["median"], original["Gd"])
            self.assertEqual(group["Gp99"]["median"], original["Gp99"])
        self.assertEqual(result["verdict"]["P2"]["result"], "inconclusive")
        self.assertFalse(result["prereg_applies"])
        comparisons = result["verdict"]["P1"]["comparisons"]
        self.assertTrue(comparisons["dark"]["matched_fixed_source"])
        self.assertTrue(comparisons["white"]["matched_fixed_source"])
        self.assertFalse(comparisons["launcher"]["matched_fixed_source"])
        self.assertIn("wallpaper histories remain unmatched", comparisons["launcher"]["label"])
        self.assertEqual(result["reps"], ml+st)
        self.assertIn("NOT applicable", md)
        self.assertIn(GA.PREREG, md)

    def test_mismatch_refuses_only_affected_source_keeps_descriptive_measurements(self):
        for key,value in [("fixture_sha256", "c"*64), ("viewer_profile", ["other", "{path}"]),
                          ("roi", [40, 60, 790, 1580]), ("launcher_component", "other/.Home")]:
            with self.subTest(key=key):
                ml=[record("mainline", sid, i) for i,sid in enumerate(("dark","white"),1)]
                st=[record("stock", sid, i,gd=.003) for i,sid in enumerate(("dark","white"),1)]
                st[0]["source"][key]=value
                result,_=self.report(ml,st)
                comparisons=result["verdict"]["P1"]["comparisons"]
                self.assertEqual(comparisons["dark"]["result"], "refused")
                self.assertIn(key, " ".join(comparisons["dark"]["reasons"]))
                self.assertEqual(comparisons["white"]["result"], "descriptive")
                self.assertEqual(result["groups_by_source"]["dark"]["stock"]["Gd"]["median"], .003)

    def test_within_platform_drift_cannot_hide_behind_first_record(self):
        ml=[record("mainline","dark",1),record("mainline","dark",2)]
        ml[1]["source"]["fixture_sha256"]="c"*64
        result,_=self.report(ml,[record("stock","dark")])
        self.assertEqual(result["verdict"]["P1"]["comparisons"]["dark"]["result"], "refused")

    def test_missing_unverified_or_inconsistent_metadata_fails_closed(self):
        changes=[("verified",False),("fixture_sha256",None),("viewer_profile",None),
                 ("source_frame_mean_luma",None),("wallpaper_mean_luma",float("nan")),
                 ("id","white"),("roi",[0,0,2000,2000])]
        for key,value in changes:
            with self.subTest(key=key):
                ml=record("mainline","dark")
                ml["source"][key]=value
                result,_=self.report([ml],[record("stock","dark")])
                self.assertEqual(result["verdict"]["P1"]["comparisons"]["dark"]["result"], "refused")
                self.assertTrue(result["source_validation"][0]["errors"])

    def test_no_stock_and_legacy_records_are_not_pooled(self):
        legacy=record("mainline","dark",9)
        del legacy["source"]
        del legacy["source_id"]
        result,_=self.report([record("mainline","dark"),legacy])
        self.assertEqual(result["groups_by_source"]["dark"]["mainline"]["n"],1)
        self.assertEqual(result["legacy_records_not_pooled"],[{"src":"mainline","rep":9}])
        self.assertEqual(result["verdict"]["P1"]["comparisons"]["dark"]["result"],"reference pending")

    def test_launcher_luma_retained_without_pixel_match_claim(self):
        ml=record("mainline","launcher")
        st=record("stock","launcher")
        ml["source"]["wallpaper_mean_luma"]=.2
        st["source"]["wallpaper_mean_luma"]=.99
        result,md=self.report([ml],[st])
        self.assertEqual([r["source"]["wallpaper_mean_luma"] for r in result["reps"]],[.2,.99])
        comparison=result["verdict"]["P1"]["comparisons"]["launcher"]
        self.assertFalse(comparison["pixel_match_established"])
        self.assertFalse(comparison["matched_fixed_source"])
        self.assertIn("0.200000",md)
        self.assertIn("0.990000",md)

    def test_saturation_and_cleanup_lows_do_not_enter_source_groups(self):
        ml=[record("mainline","dark",i) for i in (1,2,3)]
        ml[1]["saturated"]=.02
        ml[2]["A_lows"]=1
        result,_=self.report(ml,[record("stock","dark")])
        self.assertEqual(result["groups_by_source"]["dark"]["mainline"]["n"],1)

    def test_legacy_report_retains_original_p2_rule(self):
        ml=[record("mainline","dark",i,dip=i<=3) for i in range(1,6)]
        for r in ml:
            del r["source"]
            del r["source_id"]
        result,_=self.report(ml)
        self.assertEqual(result["verdict"]["P2"]["result"],"no dip-related worsening shown")
        self.assertNotIn("groups_by_source",result)

    def test_signed_offset_addition_preserves_original_score_definitions(self):
        clean=np.ones((1648,824),np.float32)
        post=clean-.125
        score=GA.ghost_scores(post,clean)
        self.assertEqual(score["G"],.125)
        self.assertEqual(score["Gd"],.125)
        self.assertEqual(score["Groi"],.125)
        self.assertEqual(score["Gp99"],.125)
        self.assertEqual(score["SignedOffset"],.125)
        self.assertEqual(score["CenteredL1"],0)

    def test_capture_metadata_is_preserved_into_analysis_rows(self):
        original=record("mainline","dark")
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/"ghost-reps.json").write_text(json.dumps({"reps":[original]}))
            with mock.patch.object(GA,"settled_stack",return_value=(np.ones((2,2)),9)) as stack, \
                    mock.patch.object(GA,"ghost_scores",return_value={"Gd":.125}):
                stack.last_sat=0
                rows=GA.analyse_dir(d,np.eye(3),GA.DEFAULT_OFFGLASS,None,"mainline")
        self.assertEqual(rows[0]["source_id"],"dark")
        self.assertEqual(rows[0]["source"],original["source"])


if __name__ == "__main__":
    unittest.main()
