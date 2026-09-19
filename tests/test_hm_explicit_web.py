# -*- coding: utf-8 -*-
"""hm explicit-web backend on synthetic STEP files written here: supported plate with a notch, a non-planar face and
an oblique plane that must be refused, parameter validation, and the row-count hysteresis. No CAD, no HyperMesh."""
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests._paths import TOOL
from hm import explicit_web as ew
from mesh_transition import web


class Step(object):
    """A minimal AP203 writer: planar faces bounded by straight edges, enough for the web mesher."""

    def __init__(self):
        self.lines, self.n = [], 0

    def add(self, text):
        self.n += 1
        self.lines.append("#%d = %s ;" % (self.n, text))
        return self.n

    def pt(self, p):
        return self.add("CARTESIAN_POINT ( 'NONE', ( %r, %r, %r ) )" % tuple(float(v) for v in p))

    def dirn(self, d):
        return self.add("DIRECTION ( 'NONE', ( %r, %r, %r ) )" % tuple(float(v) for v in d))

    def plane_face(self, loop_pts, origin, normal, ref):
        verts = [self.add("VERTEX_POINT ( 'NONE', #%d )" % self.pt(p)) for p in loop_pts]
        oes = []
        for i, p in enumerate(loop_pts):
            q = loop_pts[(i + 1) % len(loop_pts)]
            L = math.dist(p, q)
            d = self.dirn([(q[k] - p[k]) / L for k in range(3)])
            vec = self.add("VECTOR ( 'NONE', #%d, %r )" % (d, L))
            line = self.add("LINE ( 'NONE', #%d, #%d )" % (self.pt(p), vec))
            ec = self.add("EDGE_CURVE ( 'NONE', #%d, #%d, #%d, .T. )" % (verts[i], verts[(i + 1) % len(verts)], line))
            oes.append(self.add("ORIENTED_EDGE ( 'NONE', *, *, #%d, .T. )" % ec))
        loop = self.add("EDGE_LOOP ( 'NONE', ( %s ) )" % ", ".join("#%d" % o for o in oes))
        bound = self.add("FACE_OUTER_BOUND ( 'NONE', #%d, .T. )" % loop)
        ax = self.add("AXIS2_PLACEMENT_3D ( 'NONE', #%d, #%d, #%d )" % (self.pt(origin), self.dirn(normal), self.dirn(ref)))
        surf = self.add("PLANE ( 'NONE', #%d )" % ax)
        return self.add("ADVANCED_FACE ( 'NONE', ( #%d ), #%d, .T. )" % (bound, surf))

    def write(self, path):
        io.open(path, "w", encoding="latin-1", newline="\n").write(
            "ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION (( 'STEP AP203' ), '1' );\nENDSEC;\n\nDATA;\n"
            + "\n".join(self.lines) + "\nENDSEC;\nEND-ISO-10303-21;\n")


def notched_plate(x, oblique=False):
    """400 mm root along z, 200 mm tall along y, a 40 x 40 mm rectangular notch cut down from the top edge."""
    pts = [(0, 0, 0), (0, 0, 400), (0, 200, 400), (0, 200, 220), (0, 160, 220), (0, 160, 180), (0, 200, 180),
           (0, 200, 0)]
    if oblique:
        return [(0.2 * p[2], p[1], p[2]) for p in pts], (0, 0, 0), (1 / math.sqrt(1.04), 0, -0.2 / math.sqrt(1.04))
    return [(x, p[1], p[2]) for p in pts], (x, 0, 0), (1, 0, 0)


def write_plate(path, oblique=False):
    s = Step()
    loop, o, n = notched_plate(0.0, oblique)
    web_face = s.plane_face(loop, o, n, (0, 1, 0))
    loop2, o2, n2 = notched_plate(6.0, oblique)
    s.plane_face(loop2, o2, n2, (0, 1, 0))
    s.write(path)
    return web_face


class TestSupport(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_planar_notched_plate_is_recognised(self):
        p = os.path.join(self.d, "plate.stp")
        face = write_plate(p)
        ok, reasons, summary = ew.support(p, face)
        self.assertTrue(ok, reasons)
        self.assertEqual(summary["notches"], 1)
        self.assertAlmostEqual(summary["thickness_mm"], 6.0)

    def test_oblique_plane_is_unsupported_not_attempted(self):
        p = os.path.join(self.d, "oblique.stp")
        face = write_plate(p, oblique=True)
        ok, reasons, _ = ew.support(p, face)
        self.assertFalse(ok)
        self.assertIn("oblique", reasons[0])

    def test_non_planar_face_is_unsupported(self):
        p = os.path.join(self.d, "curved.stp")
        face = write_plate(p)
        text = io.open(p, encoding="latin-1").read().replace("PLANE ( 'NONE',", "CYLINDRICAL_SURFACE ( 'NONE',")
        text = text.replace("CYLINDRICAL_SURFACE ( 'NONE', #", "CYLINDRICAL_SURFACE ( 'NONE', #", 1)
        io.open(p, "w", encoding="latin-1", newline="\n").write(text)
        ok, reasons, _ = ew.support(p, face)
        self.assertFalse(ok)
        self.assertIn("non-planar", reasons[0])

    def test_missing_file_and_face(self):
        self.assertFalse(ew.support(os.path.join(self.d, "none.stp"), 1)[0])
        p = os.path.join(self.d, "plate.stp")
        write_plate(p)
        self.assertFalse(ew.support(p, 99999)[0])


class TestParams(unittest.TestCase):
    def test_required_and_ranges(self):
        with self.assertRaises(ew.ExplicitWebError):
            ew.normalize({"face": 1})
        with self.assertRaises(ew.ExplicitWebError):
            ew.normalize({"step": "a.stp", "face": 1, "fine_mm": 5, "coarse_mm": 20})      # ratio 4: one row cannot
        with self.assertRaises(ew.ExplicitWebError):
            ew.normalize({"step": "a.stp", "face": 1, "nonsense": 1})
        with self.assertRaises(ew.ExplicitWebError):
            ew.normalize({"step": "a.stp", "face": 1, "fallback": "maybe"})
        p = ew.normalize({"step": "a.stp", "face": 1, "deck": {"node_base": 27501}})
        self.assertEqual(p["deck"]["node_base"], 27501)
        self.assertEqual(p["deck"]["element_base"], 1)


class TestRun(unittest.TestCase):
    """The backend end to end on the synthetic plate: every acceptance check, and an R1 deck the pipeline accepts."""

    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        cls.step = os.path.join(cls.d, "plate.stp")
        cls.face = write_plate(cls.step)
        cls.out = os.path.join(cls.d, "00_hm")
        os.makedirs(cls.out)
        cls.res = ew.run_explicit({"step": "plate.stp", "face": cls.face, "smooth_passes": 5,
                                   "deck": {"node_base": 27501, "element_base": 20613}},
                                  cls.d, cls.out, log=lambda s: None)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_all_checks_pass(self):
        self.assertTrue(self.res["supported"], self.res.get("unsupported_reasons"))
        self.assertTrue(self.res["ok"], {k: v for k, v in self.res["checks"].items() if not v})

    def test_all_hex_and_on_the_boundary(self):
        s = self.res["report"]["solid"]
        self.assertEqual(s["penta6"], 0)
        self.assertEqual(s["hex8"], s["elements"])
        self.assertLess(self.res["boundary"]["max_deviation_mm"], 1e-6)
        self.assertAlmostEqual(s["sweep"]["thickness_mm"], 6.0)

    def test_decks(self):
        from common.bdf import check_r1
        ok, st = check_r1(os.path.join(self.out, "base_web.bdf"))
        self.assertTrue(ok, st)
        self.assertEqual(self.res["hm_deck"]["node_id_range"][0], 27501)
        self.assertEqual(self.res["hm_deck"]["element_id_range"][0], 20613)


class TestUnsupportedFallsBack(unittest.TestCase):
    """weldsim hm with an unsupported face: fallback 'fail' stops the step; 'automesh' without a base_mesh block
    also stops it, saying why. (With base_mesh and hmbatch configured it would run the automesh backend.)"""

    def run_hm(self, fallback):
        d = tempfile.mkdtemp()
        try:
            face = write_plate(os.path.join(d, "oblique.stp"), oblique=True)
            case = {"case_name": "t", "geometry_family": "fillet_web_on_plate", "output_dir": "out",
                    "explicit_web": {"step": "oblique.stp", "face": face, "fallback": fallback}}
            cp = os.path.join(d, "case.json")
            io.open(cp, "w", encoding="utf-8").write(json.dumps(case))
            r = subprocess.run([sys.executable, os.path.join(TOOL, "weldsim.py"), "hm", cp,
                                "--config", os.path.join(TOOL, "config.example.yaml")],
                               capture_output=True, text=True, cwd=TOOL)
            man = json.load(io.open(os.path.join(d, "out", "00_hm", "manifest.json"), encoding="utf-8"))
            return r, man
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_fail_and_missing_fallback(self):
        for fb in ("fail", "automesh"):
            r, man = self.run_hm(fb)
            self.assertNotEqual(r.returncode, 0)
            self.assertEqual(man["exit_code"], 1)
            self.assertFalse(man["explicit_web"]["supported"])
            self.assertIn("unsupported", r.stdout + r.stderr)


class TestRowCounts(unittest.TestCase):
    def test_a_height_on_a_threshold_does_not_flicker(self):
        """91 mm is 7 rows of 13.0 mm or 5 of 18.2: noise around it made 7 T3 cells in 8 columns on a real part."""
        h = [91.0 + (1.5 if k % 3 else -1.5) for k in range(60)]
        n = web.row_counts(h, 14.5, 13.0)
        steps = sum(1 for a, b in zip(n, n[1:]) if a != b)
        self.assertLessEqual(steps, 2)


def write_web_on_plate(path):
    """A notched web (faces x = 57 and 63) standing on a plate extruded along x from 0 to 120, profile 400 x 10."""
    s = Step()
    web = [(57.0, p[1], p[2]) for p in notched_plate(0.0)[0]]
    web_face = s.plane_face(web, (57, 0, 0), (1, 0, 0), (0, 1, 0))
    s.plane_face([(63.0, p[1], p[2]) for p in notched_plate(0.0)[0]], (63, 0, 0), (1, 0, 0), (0, 1, 0))
    prof = [(0.0, 0.0, 0.0), (0.0, -10.0, 0.0), (0.0, -10.0, 400.0), (0.0, 0.0, 400.0)]
    plate_face = s.plane_face(prof, (0, 0, 0), (1, 0, 0), (0, 1, 0))
    s.plane_face([(120.0, p[1], p[2]) for p in prof], (120, 0, 0), (1, 0, 0), (0, 1, 0))
    s.write(path)
    return web_face, plate_face


class TestHardPoints(unittest.TestCase):
    def grid(self):
        from mesh_transition.block import tfi
        from mesh_transition.mesh import Mesh
        m = Mesh()
        b = [m.node(10.0 * i, 0.0) for i in range(5)]
        t = [m.node(10.0 * i, 30.0) for i in range(5)]
        l = [m.node(0.0, 10.0 * j) for j in range(4)]
        r = [m.node(40.0, 10.0 * j) for j in range(4)]
        tfi(m, b, t, l, r)
        return m, [[(0.0, 0.0), (40.0, 0.0)], [(0.0, 30.0), (40.0, 30.0)], [(0.0, 0.0), (0.0, 30.0)], [(40.0, 0.0), (40.0, 30.0)]]

    def test_interior_point_becomes_a_node(self):
        from mesh_transition.hardpoints import relocate
        m, curves = self.grid()
        r = relocate(m, [(12.0, 13.0)], curves)
        self.assertIn((12.0, 13.0), m.nodes.values())
        self.assertEqual(r[0]["how"], "moved")

    def test_boundary_point_slides_along_its_own_edge(self):
        from mesh_transition.hardpoints import relocate
        m, curves = self.grid()
        relocate(m, [(23.0, 30.0)], curves)
        self.assertIn((23.0, 30.0), m.nodes.values())
        self.assertFalse(any(abs(y - 30.0) < 1e-9 and abs(x - 20.0) < 1e-9 for x, y in m.nodes.values()))

    def test_a_point_off_the_part_is_refused(self):
        from mesh_transition.hardpoints import HardPointError, relocate
        m, curves = self.grid()
        with self.assertRaises(HardPointError):
            relocate(m, [(20.0, 31.0)], curves)             # nearest node is on the top edge; the point is not

    def test_a_point_too_far_from_any_node_is_refused(self):
        from mesh_transition.hardpoints import HardPointError, relocate
        m, curves = self.grid()
        with self.assertRaises(HardPointError):
            relocate(m, [(15.0, 15.0)], curves, max_move_fraction=0.3)


class TestGradedLevels(unittest.TestCase):
    def test_fine_zone_growth_and_required_levels(self):
        from mesh_transition.plate import graded_levels
        lv = graded_levels(0.0, 120.0, ((40.0, 80.0),), 5.0, 16.0, 1.3, keep=(57.0, 63.0, 10.0))
        w = [b - a for a, b in zip(lv, lv[1:])]
        for v in (0.0, 10.0, 57.0, 63.0, 120.0):
            self.assertTrue(any(abs(x - v) < 1e-9 for x in lv), v)
        inner = [b - a for a, b in zip(lv, lv[1:]) if a >= 40.0 - 1e-9 and b <= 80.0 + 1e-9]
        self.assertTrue(all(4.0 <= x <= 6.5 for x in inner), inner)
        self.assertLessEqual(max(w), 16.0 + 1e-6)
        self.assertGreater(min(w), 1.0)


class TestWebAndPlate(unittest.TestCase):
    """The formal backend on a web standing on an extruded plate, with constraints given as positions."""

    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp()
        web, plate = write_web_on_plate(os.path.join(cls.d, "wp.stp"))
        cls.out = os.path.join(cls.d, "00_hm")
        os.makedirs(cls.out)
        cls.cons = [{"name": "top", "body": "web", "dirs": "x", "point_mm": [60.0, 200.0, 97.0]},
                    {"name": "under", "body": "plate", "dirs": "y", "point_mm": [12.0, -10.0, 203.0]},
                    {"name": "end", "body": "plate", "dirs": "xyz", "point_mm": [0.0, -5.0, 0.0]}]
        cls.res = ew.run_explicit({"step": "wp.stp", "face": web, "smooth_passes": 5, "constraints": cls.cons,
                                   "plate": {"face": plate, "layers": 2}}, cls.d, cls.out, log=lambda s: None)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_all_checks(self):
        self.assertTrue(self.res["ok"], {k: v for k, v in self.res["checks"].items() if not v})

    def test_constraints_are_nodes_exactly(self):
        self.assertLess(self.res["constraints"]["max_distance_mm"], 1e-3)
        rows = [l.strip().split(";") for l in io.open(os.path.join(self.out, "fixed_nodes.csv"), encoding="utf-8")]
        self.assertEqual(rows[0], ["name", "dirs", "target", "x", "y", "z"])
        self.assertEqual([r[0] for r in rows[1:]], ["top", "under", "end"])

    def test_plate_fine_zone_is_around_the_web(self):
        a, b = self.res["plate"]["fine_zone"]
        self.assertAlmostEqual(a, 57.0 - 16.0)
        self.assertAlmostEqual(b, 63.0 + 16.0)
        s = self.res["plate"]["report"]["solid"]
        self.assertEqual(s["penta6"], 0)
        self.assertTrue(s["ok"])

    def test_a_constraint_on_an_unmeshed_plate_is_refused(self):
        with self.assertRaises(ew.ExplicitWebError):
            ew.normalize({"step": "a", "face": 1, "constraints": [{"name": "n", "body": "plate", "dirs": "y",
                                                                   "point_mm": [0, 0, 0]}]})


class TestPlateEndProfiles(unittest.TestCase):
    def test_a_plate_whose_width_changes_is_refused(self):
        """Two end faces with different outlines are not an extrusion; the mesher must say so, not make a prism."""
        from mesh_transition.plate import PlateParams, build as build_plate
        d = tempfile.mkdtemp()
        try:
            s = Step()
            a = s.plane_face([(0.0, 0.0, 0.0), (0.0, -10.0, 0.0), (0.0, -10.0, 400.0), (0.0, 0.0, 400.0)],
                             (0, 0, 0), (1, 0, 0), (0, 1, 0))
            s.plane_face([(120.0, 0.0, 0.0), (120.0, -10.0, 0.0), (120.0, -10.0, 440.0), (120.0, 0.0, 440.0)],
                         (120, 0, 0), (1, 0, 0), (0, 1, 0))
            p = os.path.join(d, "taper.stp")
            s.write(p)
            with self.assertRaises(ValueError) as cm:
                build_plate(PlateParams(step_path=p, face=a, layers=2))
            self.assertIn("not a straight extrusion", str(cm.exception))
        finally:
            shutil.rmtree(d, ignore_errors=True)
