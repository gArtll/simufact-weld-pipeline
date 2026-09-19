# -*- coding: utf-8 -*-
"""Contact strategy planner, capability evidence, and the local joint frame of prep.

Contact: there is no repository-wide contact default. A regression case keeps the rule set it was solved with; a
project case gets the capability plan's default (process default where nothing more specific is known), and an
explicit alternative only with a qualifying source -- never because it would make a run converge.

Evidence: a capability marked supported/partial names the tests and solver runs that show its chain works; a joint the
classifier recognises but the tool cannot build stays detected=true, supported=false."""
import io
import json
import os
import re
import shutil
import tempfile
import unittest

import yaml

from tests._paths import TOOL
from tests import _step_boxes as boxes
from common import contact
from common.preflight import Result
from capability.classify import classify
from capability.planner import load_matrix, plan


def _t_plan():
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, "t.stp")
        boxes.t_joint(p)
        return plan(classify(p))
    finally:
        shutil.rmtree(d, ignore_errors=True)


class TestContactPlanner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = _t_plan()

    def _case(self, ruleset, source=None, role="project"):
        c = {"role": role, "contact_ruleset": ruleset, "provenance": {}}
        if source:
            c["provenance"]["contact_ruleset"] = {"source": source, "ref": "x"}
        return c

    def test_plan_names_default_basis_and_options(self):
        cs = self.plan["joints"][0]["contact_strategy"]
        self.assertEqual(cs["default"], "process_default")
        self.assertTrue(cs["basis"])
        self.assertIn("fillet", [v["ruleset"] for v in cs["explicit"].values()])

    def test_project_default(self):
        r = contact.resolve(self._case("process_default", "simufact_official_example"), self.plan)
        self.assertEqual((r["ruleset"], r["mode"]), ("process_default", "process_default"))
        self.assertIn("plan default", r["decided_by"])
        self.assertEqual(contact.check(self._case("process_default", "derived"), self.plan, Result)[0].level, "ok")

    def test_explicit_option_needs_a_qualifying_source(self):
        for src in ("derived", "estimated", "borrowed_case", None):
            res = contact.check(self._case("fillet", src), self.plan, Result)
            self.assertEqual(res[0].level, "block", (src, res))
        for src in ("simufact_official_example", "user_confirmed", "process_document"):
            res = contact.check(self._case("fillet", src), self.plan, Result)
            self.assertEqual(res[0].level, "ok", (src, res))
            self.assertIn("explicit capability", contact.resolve(self._case("fillet", src), self.plan)["decided_by"])

    def test_ruleset_outside_the_capability_is_refused(self):
        res = contact.check(self._case("fillet_tube", "user_confirmed"), self.plan, Result)
        self.assertEqual(res[0].level, "block")

    def test_project_without_plan_is_refused(self):
        res = contact.check(self._case("process_default", "user_confirmed"), None, Result)
        self.assertEqual(res[0].level, "block")

    def test_regression_cases_keep_their_baseline(self):
        """Whatever the plan default is, a regression case builds with the rule set it was solved with."""
        for f, want, mode in (("example_synthetic.json", "fillet", "user_defined"),
                              ("example_tube_synthetic.json", "fillet_tube", "process_default")):
            c = json.load(io.open(os.path.join(TOOL, "cases", f), encoding="utf-8"))
            r = contact.resolve(c, self.plan)
            self.assertEqual((r["ruleset"], r["mode"], r["decided_by"]), (want, mode, "regression baseline"), f)
            self.assertEqual(contact.check(c, None, Result)[0].level, "ok", f)


class TestCapabilityEvidence(unittest.TestCase):
    def test_supported_needs_evidence_that_exists(self):
        ci = yaml.safe_load(io.open(os.path.join(TOOL, ".github", "workflows", "ci.yml"), encoding="utf-8"))
        steps = {s.get("name") for job in ci["jobs"].values() for s in job["steps"]}
        for cap in load_matrix()["capabilities"]:
            ev = cap.get("evidence") or {}
            if cap["status"] == "gap":
                self.assertFalse(ev, "%s is a gap and claims evidence" % cap["id"])
                continue
            self.assertTrue(ev.get("ci_tests"), "%s is %s without CI tests" % (cap["id"], cap["status"]))
            self.assertTrue(ev.get("solver_runs"), "%s is %s without a solver run" % (cap["id"], cap["status"]))
            for t in ev["ci_tests"]:
                parts = t.split("::")
                src = io.open(os.path.join(TOOL, parts[0]), encoding="utf-8").read()
                for name in parts[1:]:
                    self.assertTrue(re.search(r"^\s*(class|def) %s\b" % re.escape(name), src, re.M), t)
            for s in ev.get("ci_workflow_steps", []):
                self.assertIn(s, steps)

    def test_every_capability_has_a_contact_block_unless_gap(self):
        for cap in load_matrix()["capabilities"]:
            if cap["status"] != "gap":
                self.assertIn(cap["contact"]["default"], ("process_default",) + tuple(
                    v["ruleset"] for v in cap["contact"]["explicit"].values()))
                self.assertTrue(os.path.isfile(contact.ruleset_path(cap["contact"]["default"])))
                for v in cap["contact"]["explicit"].values():
                    self.assertTrue(os.path.isfile(contact.ruleset_path(v["ruleset"])))
                    self.assertTrue(v["requires_source"])
                    self.assertNotIn("derived", v["requires_source"])
                    self.assertNotIn("estimated", v["requires_source"])


def _rot(p):
    """A proper rotation that is not the identity and keeps boxes axis-aligned: (x, y, z) -> (z, x, y).
    The web normal x becomes y, the plate normal y becomes z, the weld direction z becomes x."""
    return (p[2], p[0], p[1])


def _t_joint_rotated(path, rot):
    s = boxes.StepWriter()
    for name, lo, hi in (("plate", (0, -10, 0), (200, 0, 400)), ("web", (97, 0, 0), (103, 100, 400))):
        a, b = rot(lo), rot(hi)
        s.box(name, tuple(min(u, v) for u, v in zip(a, b)), tuple(max(u, v) for u, v in zip(a, b)))
    s.write(path)


def _window(path, pts):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# window\n# Length unit: Millimeter [mm]\n#\n# Orientation: global orientation;x;y;z\n0;1;0;0\n#\n"
                "# order;activity;x-coordinate;y-coordinate;z-coordinate\n")
        for k, q in enumerate(pts, 1):
            f.write("%d;true;%.9f;%.9f;%.9f\n" % ((k,) + tuple(q)))


class TestFrameArguments(unittest.TestCase):
    def test_negative_leading_component_reaches_prep(self):
        """A frame whose first component is -0.0 or negative must not be read as a new command-line option."""
        import argparse
        from prep.frame import fmt, parse_vec
        p = argparse.ArgumentParser()
        p.add_argument("--normal")
        for v in ((-0.0, 1.0, -0.0), (-1.0, 0.0, 0.0)):
            self.assertEqual(parse_vec(p.parse_args(["--normal=" + fmt(v)]).normal), tuple(x + 0.0 for x in v))
        self.assertNotIn("-0.0", fmt((-0.0, 1.0, -0.0)))


class TestTJointChain(unittest.TestCase):
    """STEP -> inspect -> plan -> explicit hm (web + plate) -> prep, in the repository's legacy orientation and in a
    rotated one. prep must take the joint frame from inspect, not assume a web normal along x; the rotated bead must be
    the original bead rotated. This is the CI evidence of capability fillet_t_planar_web."""

    @classmethod
    def setUpClass(cls):
        import subprocess
        import sys
        from hm import explicit_web as ew
        cls.d = tempfile.mkdtemp()
        cls.res = {}
        for tag, rot in (("legacy", lambda p: tuple(p)), ("rotated", _rot)):
            d = os.path.join(cls.d, tag)
            os.makedirs(os.path.join(d, "00_hm"))
            step = os.path.join(d, "t.stp")
            _t_joint_rotated(step, rot)
            pl = plan(classify(step))
            j = pl["joints"][0]
            mb = j["required_mesh_backend"]
            hm = ew.run_explicit({"step": "t.stp", "face": mb["standing_face"], "root_geometry_ids": mb["root_geometry_ids"],
                                  "smooth_passes": 5, "plate": {"face": mb["base_profile_face"], "layers": 2}},
                                 d, os.path.join(d, "00_hm"), log=lambda s: None)
            wins = []
            for lab, x in (("w1", 97.0), ("w2", 103.0)):
                w = os.path.join(d, "win_%s.csv" % lab)
                _window(w, [rot((x, 0.0, 20.0)), rot((x, 0.0, 380.0))])
                wins += ["--side", "%s:%s" % (lab, "neg" if x < 100 else "pos"), "--window", "%s=%s" % (lab, w)]
            fr = j["frame"]
            out = os.path.join(d, "01_prep")
            cmd = [sys.executable, os.path.join(TOOL, "prep", "weld_prep.py"), "--web",
                   os.path.join(d, "00_hm", "base_web.bdf"), "--plate", os.path.join(d, "00_hm", "base_plate.bdf"),
                   "--leg", "6", "--spacing", "5", "--out", out,
                   "--normal=" + ",".join(map(repr, fr["transverse"])), "--along=" + ",".join(map(repr, fr["tangent"]))] + wins
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            cls.res[tag] = {"plan": pl, "hm": hm, "prep_rc": r.returncode, "prep_out": r.stdout + r.stderr,
                            "dir": out, "rot": rot}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_step_to_bead_axis_frames(self):
        for tag, r in self.res.items():
            j = r["plan"]["joints"][0]
            self.assertEqual(r["plan"]["status"], "supported", tag)
            self.assertEqual(j["required_mesh_backend"]["base"], "explicit_plate_sweep", tag)
            self.assertTrue(r["hm"]["ok"], (tag, {k: v for k, v in r["hm"]["checks"].items() if not v}))
            self.assertEqual(r["prep_rc"], 0, (tag, r["prep_out"][-3000:]))
            summ = json.load(io.open(os.path.join(r["dir"], "weld_prep_summary.json"), encoding="utf-8"))
            self.assertTrue(all(v["ok"] for v in summ["checks"].values()), (tag, summ["checks"]))
        fr = self.res["rotated"]["plan"]["joints"][0]["frame"]
        self.assertEqual([round(v, 9) for v in fr["transverse"]], [0.0, 1.0, 0.0])
        self.assertEqual([round(v, 9) for v in fr["tangent"]], [1.0, 0.0, 0.0])
        self.assertEqual([round(v, 9) for v in fr["normal"]], [0.0, 0.0, 1.0])

    def test_rotated_bead_is_the_bead_rotated(self):
        import numpy as np
        from scipy.spatial import cKDTree
        from common.bdf import read_bdf
        for lab in ("w1", "w2"):
            a = read_bdf(os.path.join(self.res["legacy"]["dir"], "bead_%s.bdf" % lab))[0]
            b = read_bdf(os.path.join(self.res["rotated"]["dir"], "bead_%s.bdf" % lab))[0]
            A = np.array([_rot(p) for p in a.values()])
            B = np.array(list(b.values()))
            self.assertEqual(A.shape, B.shape, lab)
            self.assertLess(float(cKDTree(B).query(A)[0].max()), 1e-6, lab)

    def test_legacy_orientation_is_legacy_frame(self):
        summ = json.load(io.open(os.path.join(self.res["legacy"]["dir"], "weld_prep_summary.json"), encoding="utf-8"))
        self.assertTrue(summ["frame"]["legacy"])
        summ = json.load(io.open(os.path.join(self.res["rotated"]["dir"], "weld_prep_summary.json"), encoding="utf-8"))
        self.assertFalse(summ["frame"]["legacy"])


class TestMappedPlateChain(unittest.TestCase):
    """A plate whose section changes across its width (400 mm long at one edge, 440 mm at the other): the sweep
    refuses it, the plan names mapped_plate, hm meshes it onto the real faces, prep builds the beads on it. The
    joint stays detected / not supported until build and a smoke run have been done with this backend."""

    @classmethod
    def setUpClass(cls):
        import subprocess
        import sys
        from hm import explicit_web as ew
        cls.d = tempfile.mkdtemp()
        step = os.path.join(cls.d, "t.stp")
        boxes.tapered_t_joint(step)
        cls.plan = plan(classify(step))
        mb = cls.plan["joints"][0]["required_mesh_backend"]
        os.makedirs(os.path.join(cls.d, "00_hm"))
        cls.hm = ew.run_explicit({"step": "t.stp", "face": mb["standing_face"], "root_geometry_ids": mb["root_geometry_ids"],
                                  "smooth_passes": 5, "plate": {"face": mb["base_profile_face"], "layers": 2},
                                  "constraints": [{"name": "c", "body": "plate", "dirs": "xyz",
                                                   "point_mm": [150.0, -10.0, 200.0]}]},
                                 cls.d, os.path.join(cls.d, "00_hm"), log=lambda s: None)
        wins = []
        for lab, x in (("w1", 97.0), ("w2", 103.0)):
            w = os.path.join(cls.d, "win_%s.csv" % lab)
            _window(w, [(x, 0.0, 20.0), (x, 0.0, 380.0)])
            wins += ["--side", "%s:%s" % (lab, "neg" if x < 100 else "pos"), "--window", "%s=%s" % (lab, w)]
        fr = cls.plan["joints"][0]["frame"]
        cls.out = os.path.join(cls.d, "01_prep")
        cmd = [sys.executable, os.path.join(TOOL, "prep", "weld_prep.py"), "--web",
               os.path.join(cls.d, "00_hm", "base_web.bdf"), "--plate", os.path.join(cls.d, "00_hm", "base_plate.bdf"),
               "--leg", "6", "--spacing", "5", "--out", cls.out,
               "--normal=" + ",".join(map(repr, fr["transverse"])), "--along=" + ",".join(map(repr, fr["tangent"]))] + wins
        cls.prep = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_plan_names_mapped_plate_and_does_not_claim_support(self):
        j = self.plan["joints"][0]
        self.assertEqual(j["required_mesh_backend"]["base"], "mapped_plate")
        self.assertTrue(j["detected"])
        self.assertFalse(j["supported"])
        self.assertEqual((j["status"], self.plan["status"]), ("unverified", "unverified"))
        self.assertTrue(j["not_verified"])

    def test_sweep_refuses_mapped_meshes(self):
        from mesh_transition.plate import PlateParams, build as sweep
        mb = self.plan["joints"][0]["required_mesh_backend"]
        with self.assertRaises(ValueError):
            sweep(PlateParams(step_path=os.path.join(self.d, "t.stp"), face=mb["base_profile_face"], layers=2))
        self.assertEqual(self.hm["plate"]["backend"], "mapped_plate")
        self.assertTrue(self.hm["ok"], {k: v for k, v in self.hm["checks"].items() if not v})
        rep = self.hm["plate"]["report"]
        self.assertEqual(rep["profile"]["weld_side_mm"], [400.0, 440.0])
        self.assertLessEqual(rep["projection"]["max_distance_to_face_mm"], 1e-3)
        self.assertEqual(rep["solid"]["penta6"], 0)
        self.assertTrue(rep["solid"]["ok"])

    def test_plate_follows_the_oblique_ends(self):
        """Every node of the plate lies inside the tapered outline, and its end nodes on the oblique end faces."""
        from mesh_transition.face import read_solid_bdf
        nodes = read_solid_bdf(os.path.join(self.d, "00_hm", "base_plate.bdf"))[0]
        zmin = lambda x: -0.1 * x
        zmax = lambda x: 400.0 + 0.1 * x
        for x, y, z in nodes.values():
            self.assertGreaterEqual(z, zmin(x) - 1e-6)
            self.assertLessEqual(z, zmax(x) + 1e-6)
        ends = [z for x, y, z in nodes.values() if abs(z - zmin(x)) < 1e-6 or abs(z - zmax(x)) < 1e-6]
        self.assertGreater(len(ends), 100)

    def test_prep_on_the_mapped_plate(self):
        self.assertEqual(self.prep.returncode, 0, self.prep.stdout[-3000:] + self.prep.stderr[-2000:])
        summ = json.load(io.open(os.path.join(self.out, "weld_prep_summary.json"), encoding="utf-8"))
        self.assertTrue(all(v["ok"] for v in summ["checks"].values()), summ["checks"])


class TestCliPlanDrivenChain(unittest.TestCase):
    """weldsim.py new -> inspect -> plan -> hm -> prep on a rotated T joint. The case says nothing about faces or
    axes: hm takes the web face, root and plate profile from the plan, prep takes the joint frame from inspect."""

    @classmethod
    def setUpClass(cls):
        import subprocess
        import sys
        cls.d = tempfile.mkdtemp()
        step = os.path.join(cls.d, "part.stp")
        _t_joint_rotated(step, _rot)
        cfg = os.path.join(TOOL, "config.example.yaml")
        run = lambda *a: subprocess.run([sys.executable, os.path.join(TOOL, "weldsim.py")] + list(a) + ["--config", cfg],
                                        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cls.d)
        cls.case = os.path.join(cls.d, "part.json")
        cls.r = {"new": run("new", cls.case, "--step", step)}
        c = json.load(io.open(cls.case, encoding="utf-8"))
        # what the user supplies for this part (a test part: the values are confirmed for it by the test itself)
        c["bead"] = {"leg_mm": 6.0, "section_spacing_mm": 5.0}
        c["sides"] = []
        for lab, x in (("w1", 97.0), ("w2", 103.0)):
            _window(os.path.join(cls.d, "win_%s.csv" % lab), [_rot((x, 0.0, 20.0)), _rot((x, 0.0, 380.0))])
            c["sides"].append({"label": lab, "side": "neg" if x < 100 else "pos", "window_csv": "win_%s.csv" % lab})
        c["explicit_web"] = {"smooth_passes": 5}
        io.open(cls.case, "w", encoding="utf-8").write(json.dumps(c, indent=1))
        for s in ("inspect", "plan", "hm", "prep"):
            cls.r[s] = run(s, cls.case)
        cls.out = os.path.join(cls.d, "runs", "part")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_every_step_passes(self):
        for s, r in self.r.items():
            self.assertEqual(r.returncode, 0, (s, r.stdout[-2500:], r.stderr[-2500:]))

    def test_hm_filled_from_plan_and_prep_in_the_joint_frame(self):
        hm = json.load(io.open(os.path.join(self.out, "00_hm", "manifest.json"), encoding="utf-8"))
        self.assertIn("web face from plan", hm["from_plan"])
        self.assertEqual(hm["explicit_web"]["plate"]["backend"], "explicit_plate_sweep")
        prep = json.load(io.open(os.path.join(self.out, "01_prep", "manifest.json"), encoding="utf-8"))
        self.assertEqual(prep["frame"]["source"], "inspect joint frame (plan/plan.json)")
        self.assertEqual([round(v, 9) for v in prep["frame"]["normal"]], [0.0, 1.0, 0.0])
        self.assertEqual(prep["base_mesh_source"], "00_hm/explicit_web")

    def test_mesh_gate_is_its_own_stage_and_the_page_follows(self):
        from common import lifecycle
        st = lifecycle.load(self.out)["stages"]
        self.assertEqual(st["hm"]["status"], "PASS")
        self.assertEqual(st["mesh_gate"]["status"], "PASS")
        self.assertIn("mesh checks passed", st["mesh_gate"]["summary"])
        page = io.open(os.path.join(self.out, "dashboard", "index.html"), encoding="utf-8").read()
        for text in ("mesh_gate", "Contact", "process_default", "local frame", "detected / supported"):
            self.assertIn(text, page)


if __name__ == "__main__":
    unittest.main()
