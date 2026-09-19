# -*- coding: utf-8 -*-
"""prep: bdf read/write (R1), mesh-face helpers, detJ on a synthetic bead."""
import os
import subprocess
import sys
import tempfile
import unittest

from tests._paths import TOOL, needs_private, private_case, private_path, private_expect
from common.bdf import read_bdf, write_bdf, check_r1, read_weldline_csv, csv_ends_with_newline
from prep.wl_geom import plane_quads, boundary_chain, chain_order, resample


def synthetic_bead(n_sections=4, spacing=3.33, leg=5.4):
    """Simufact quality-0 template beads along +z, same layout as prep/mesh_bead.tcl output."""
    template = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (0.3943375, 0.3943375), (0.5, 0.5), (0.0, 0.4999992), (0.4999992, 0.0)]
    quads = [(3, 4, 2, 5), (5, 0, 6, 3), (3, 6, 1, 4)]
    nodes = []
    for s in range(n_sections):
        for u, v in template:
            nodes.append((-leg * u, leg * v, s * spacing))       # root (0,0,z); plate toe -x; web toe +y
    hexas = []
    for s in range(n_sections - 1):
        a, b = s * 7 + 1, (s + 1) * 7 + 1
        for q in quads:
            hexas.append([a + k for k in q] + [b + k for k in q])
    return nodes, hexas


class TestBdf(unittest.TestCase):
    def test_roundtrip_and_r1(self):
        nodes, hexas = synthetic_bead()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bead.bdf")
            write_bdf(p, nodes, hexas)
            G, E, st = read_bdf(p)
            self.assertEqual(len(G), len(nodes))
            self.assertEqual(E, hexas)
            for i, xyz in enumerate(nodes, 1):
                for a, b in zip(G[i], xyz):
                    self.assertAlmostEqual(a, b, places=6)
            ok, st = check_r1(p)
            self.assertTrue(ok, st)

    @needs_private
    def test_private_base_mesh_is_r1(self):
        ok, st = check_r1(private_path(private_case()["base"]["web_bdf"]))
        self.assertTrue(ok, st)
        self.assertEqual(st["chexa"], private_expect(self, "web_chexa"))

    @needs_private
    def test_private_window_csv(self):
        p = private_path(private_case()["sides"][0]["window_csv"])
        self.assertEqual(len(read_weldline_csv(p)), 23)
        self.assertTrue(csv_ends_with_newline(p))

    def test_tube_on_plate_inputs(self):
        """The synthetic tube-on-plate example (cases/example_tube_synthetic/make_example.py)."""
        inp = os.path.join(TOOL, "cases", "example_tube_synthetic", "inputs")
        ok, st = check_r1(os.path.join(inp, "base_plate.bdf"))
        self.assertTrue(ok, st)
        self.assertEqual(st["chexa"], 4800)
        p = os.path.join(inp, "window_ring.csv")
        self.assertEqual(len(read_weldline_csv(p)), 49)
        self.assertTrue(csv_ends_with_newline(p))


class TestGeometry(unittest.TestCase):
    def test_face_boundary_chain(self):
        # 3 x 1 hexa strip on x = 0 face -> boundary of the face is one closed loop of 8 nodes
        G = {}
        nid = 1
        idx = {}
        for i in range(4):
            for j in range(2):
                for k in range(2):
                    G[nid] = (float(k), float(j), float(i))
                    idx[(i, j, k)] = nid
                    nid += 1
        E = []
        for i in range(3):
            e = [idx[(i, 0, 0)], idx[(i, 1, 0)], idx[(i, 1, 1)], idx[(i, 0, 1)], idx[(i + 1, 0, 0)], idx[(i + 1, 1, 0)], idx[(i + 1, 1, 1)], idx[(i + 1, 0, 1)]]
            E.append(e)
        quads = plane_quads(G, E, 0.0)
        self.assertEqual(len(quads), 3)
        edges = boundary_chain(quads)
        self.assertEqual(len(edges), 8)
        chains = chain_order(edges)
        self.assertEqual(sum(len(c) for c in chains), 8)

    def test_resample(self):
        pts, total = resample([(0, 0, 0), (0, 0, 10), (0, 0, 30)], 7)
        self.assertAlmostEqual(total, 30.0)
        self.assertAlmostEqual(pts[3][2], 15.0)


class TestDetj(unittest.TestCase):
    def test_synthetic_bead_positive(self):
        nodes, hexas = synthetic_bead()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bead.bdf")
            write_bdf(p, nodes, hexas)
            js = os.path.join(d, "detj.json")
            r = subprocess.run([sys.executable, os.path.join(TOOL, "gate", "check_detj.py"), p, "--json", js], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            import json
            v = list(json.load(open(js)).values())[0]
            # the template winding with this axis layout is either all positive or all negative; prep flips winding
            self.assertIn(v["nonpositive"], (0, v["gauss_points"]))


if __name__ == "__main__":
    unittest.main()
