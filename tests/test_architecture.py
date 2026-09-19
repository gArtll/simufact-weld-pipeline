# -*- coding: utf-8 -*-
"""Architecture guarantees introduced after the new-model review (docs/architecture_refactor.md):

1. a new case never inherits critical parameters of an existing case (bootstrap is blank; borrowing is detected by
   content and blocked unless approved);
2. the review page is a fixed stage: a formal run regenerates it and refuses to start while anything on it blocks;
3. what the tool builds is decided by joint features of the geometry, not by case names -- including joints that
   are neither of the two regression families (butt, lap), which come back as capability gaps with official
   references instead of being served by the nearest existing case."""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests._paths import TOOL
from tests import _step_boxes as boxes
from common import lifecycle, provenance
from common.preflight import Case, Result, check_provenance
from capability.classify import classify
from capability.planner import plan

PY = sys.executable
WELDSIM = os.path.join(TOOL, "weldsim.py")
CONFIG = os.path.join(TOOL, "config.example.yaml")


def weldsim(*args, cwd=None):
    return subprocess.run([PY, WELDSIM] + list(args) + ["--config", CONFIG], capture_output=True, text=True,
                          cwd=cwd or TOOL, encoding="utf-8", errors="replace")


class TestNoInheritance(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.step = os.path.join(self.d, "t.stp")
        boxes.t_joint(self.step)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_new_case_is_blank(self):
        cp = os.path.join(self.d, "part.json")
        r = weldsim("new", cp, "--step", self.step)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        c = json.load(io.open(cp, encoding="utf-8"))
        self.assertEqual(c["role"], "project")
        for key in ("materials.base_xmt", "materials.filler_xmt", "heat_source.xml", "process.weld_speed_mm_s",
                    "process.parameters_xml", "bead.leg_mm", "contact_ruleset", "temperature", "gravity",
                    "gate.end_time_s"):
            self.assertFalse(provenance.used(c, key), "%s must start empty, got %r" % (key, provenance.get(c, key)))
        self.assertFalse(provenance.used(c, "constraints"))
        # the one thing filled in is the solver, and it comes from this machine
        self.assertEqual(c["provenance"]["solver"]["source"], "machine_probe")
        self.assertEqual(set(c["provenance"]), {"solver"})

    def test_new_never_overwrites(self):
        cp = os.path.join(self.d, "part.json")
        weldsim("new", cp, "--step", self.step)
        r = weldsim("new", cp, "--step", self.step)
        self.assertNotEqual(r.returncode, 0)

    def _case(self, **over):
        c = {"role": "project", "case_name": "p", "output_dir": "out", "geometry": {"step": "t.stp"},
             "heat_source": {"xml": "hs.xml", "entity_name": "x"}, "provenance": {}}
        c.update(over)
        cp = os.path.join(self.d, "p.json")
        io.open(cp, "w", encoding="utf-8").write(json.dumps(c))
        return cp

    def test_missing_source_blocks(self):
        io.open(os.path.join(self.d, "hs.xml"), "w").write("<x/>")
        res = check_provenance(Case(self._case()))
        self.assertTrue(any(r.rule == "PV1" and r.level == "block" for r in res), res)

    def test_borrowed_needs_approval(self):
        io.open(os.path.join(self.d, "hs.xml"), "w").write("<x/>")
        prov = {"heat_source.xml": {"source": "borrowed_case", "ref": "cases/other"}}
        res = check_provenance(Case(self._case(provenance=prov)))
        self.assertTrue(any(r.rule == "PV2" and r.level == "block" for r in res), res)
        prov["heat_source.xml"]["approval"] = provenance.approval("user", "temporary")
        res = check_provenance(Case(self._case(provenance=prov)))
        self.assertFalse(any(r.rule == "PV2" and r.level == "block" for r in res), res)
        self.assertTrue(any(r.rule == "PV2" and r.level == "warn" for r in res))
        # the rest of this minimal case is simply not established yet, and that blocks too
        self.assertTrue(any(r.rule == "PV1" and "not yet established" in r.message for r in res))

    def test_a_copied_file_is_caught_whatever_its_label(self):
        """The pattern of that review: a regression case's heat source copied into a new case and labelled as if it were
        this part's own. The content gives it away."""
        src = os.path.join(TOOL, "cases", "example_synthetic", "inputs", "HS-SYN-180A22V.xml")
        if not os.path.isfile(src):
            self.skipTest("regression input not present")
        shutil.copyfile(src, os.path.join(self.d, "hs.xml"))
        prov = {"heat_source.xml": {"source": "process_document", "ref": "our WPS"}}
        res = check_provenance(Case(self._case(provenance=prov)))
        pv4 = [r for r in res if r.rule == "PV4"]
        self.assertEqual(pv4[0].level, "block", res)
        self.assertIn("regression case", pv4[0].message)

    def test_regression_cases_are_marked_and_complete(self):
        for f in ("example_synthetic.json", "example_tube_synthetic.json"):
            c = json.load(io.open(os.path.join(TOOL, "cases", f), encoding="utf-8"))
            self.assertEqual(c.get("role"), "regression", f)
            res = provenance.check(c, os.path.join(TOOL, "cases", f), [], [], lambda p: [], Result)
            self.assertFalse([r for r in res if r.level == "block"], (f, res))


class TestReviewIsAStage(unittest.TestCase):
    """End to end on a new part: new -> inspect -> plan -> provenance -> run. The run must regenerate the page and
    refuse, because the new case has no process parameters yet."""

    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.step = os.path.join(cls.d, "t.stp")
        boxes.t_joint(cls.step)
        cls.case = os.path.join(cls.d, "part.json")
        assert weldsim("new", cls.case, "--step", cls.step).returncode == 0
        cls.out = os.path.join(cls.d, "runs", "part")
        cls.r = {s: weldsim(s, cls.case) for s in ("inspect", "plan", "provenance")}
        cls.run_result = weldsim("run", cls.case)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_every_stage_refreshes_the_page(self):
        self.assertTrue(os.path.isfile(os.path.join(self.out, "dashboard", "index.html")))
        st = lifecycle.load(self.out)["stages"]
        self.assertEqual(st["inspect"]["status"], "PASS")
        self.assertEqual(st["plan"]["status"], "PASS")
        self.assertEqual(st["provenance"]["status"], "BLOCK")      # nothing filled in yet

    def test_run_regenerates_the_review_and_refuses(self):
        self.assertNotEqual(self.run_result.returncode, 0)
        self.assertIn("LC1", self.run_result.stdout + self.run_result.stderr)
        st = lifecycle.load(self.out)["stages"]
        self.assertIn("review", st)
        self.assertEqual(st["review"]["status"], "BLOCK")
        page = io.open(os.path.join(self.out, "dashboard", "index.html"), encoding="utf-8").read()
        for text in ("t_joint", "fillet_t_planar_web", "MISSING", "Formal run", "machine_probe"):
            self.assertIn(text, page)

    def test_a_changed_case_needs_a_new_review(self):
        c = json.load(io.open(self.case, encoding="utf-8"))
        c["_touched"] = True
        io.open(self.case, "w", encoding="utf-8").write(json.dumps(c))
        b = lifecycle.run_blockers(self.out, self.case)
        self.assertTrue(any("different version" in x for x in b), b)


class TestFeaturesNotCaseNames(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _plan(self, make):
        p = os.path.join(self.d, "g.stp")
        make(p)
        f = classify(p)
        return f, plan(f)

    def test_t_joint_is_planned(self):
        f, p = self._plan(boxes.t_joint)
        self.assertEqual([j["type"] for j in f["joints"]], ["t_joint"])
        self.assertEqual(len(f["joints"][0]["seams"]), 2)
        self.assertEqual(p["status"], "supported")
        self.assertEqual(p["joints"][0]["required_mesh_backend"]["standing"], "explicit_web")
        self.assertEqual(p["joints"][0]["contact_strategy"]["default"], "process_default")
        self.assertTrue(p["joints"][0]["detected"] and p["joints"][0]["supported"])

    def test_butt_joint_is_a_gap_with_official_references(self):
        """Neither regression family: must be recognised and refused, not fitted to fillet_web_on_plate."""
        f, p = self._plan(boxes.butt_joint)
        self.assertEqual([j["type"] for j in f["joints"]], ["butt"])
        self.assertEqual(p["status"], "gap")
        j = p["joints"][0]
        self.assertEqual(j["capability"], "butt_square_plates")
        self.assertTrue(j["unsupported_features"])
        self.assertIn("laser_beam_welding_laserwelding", [r["id"] for r in j["official_references"]])
        self.assertTrue(j["detected"])
        self.assertFalse(j["supported"])

    def test_lap_joint_is_a_gap(self):
        f, p = self._plan(boxes.lap_joint)
        self.assertEqual([j["type"] for j in f["joints"]], ["lap"])
        self.assertEqual(p["joints"][0]["capability"], "lap_plates")
        self.assertEqual(p["status"], "gap")
        # recognised, and still not supported
        self.assertTrue(p["joints"][0]["detected"])
        self.assertFalse(p["joints"][0]["supported"])

    def test_no_case_name_in_the_main_logic(self):
        """Case names -- the regression cases' and internal model codes -- stay out of the decision code."""
        import re
        names = [json.load(io.open(os.path.join(TOOL, "cases", f), encoding="utf-8"))["case_name"]
                 for f in os.listdir(os.path.join(TOOL, "cases")) if f.endswith(".json")]
        pat = re.compile(r"\bS0\d\d\b|" + "|".join(re.escape(n) for n in names), re.I)
        for d in ("capability", "common", "hm", "build", "mesh_transition"):
            for root, _, files in os.walk(os.path.join(TOOL, d)):
                for fn in files:
                    if fn.endswith(".py"):
                        text = io.open(os.path.join(root, fn), encoding="utf-8").read()
                        code = "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))
                        code = re.sub(r'"""[\s\S]*?"""', "", code)
                        self.assertIsNone(pat.search(code), "%s names a case: %s" % (fn, pat.search(code)))

    def test_official_reference_paths(self):
        from capability.planner import load_references
        refs = load_references()
        install = os.environ.get("SIMUFACT_WELDING_HOME", "")
        if not install or not os.path.isdir(install):
            self.skipTest("SIMUFACT_WELDING_HOME not set to a Simufact Welding installation")
        for rid, r in refs["references"].items():
            self.assertTrue(os.path.exists(os.path.join(install, r["path"])), rid)
