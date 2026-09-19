# -*- coding: utf-8 -*-
"""hm: parameters -> Tcl text (TCL1-TCL4, all parameters present, no absolute paths), BDF layers and contact gap on
a hand-built 20-element plate, raw dump conversion. Integration with HyperMesh only when env WELDSIM_HMBATCH is set."""
import hashlib
import io
import json
import os
import re
import tempfile
import unittest

from tests._paths import TOOL
from common.bdf import write_bdf, check_r1
from hm import meshio, tcl_gen
from hm.params import ParamError, normalize, surface_height, zones, WEB_FIRST_ROW_MIN_MM

CASE = os.path.join(TOOL, "cases", "example_synthetic.json")
HMBATCH = os.environ.get("WELDSIM_HMBATCH", "")


def block():
    return json.load(io.open(CASE, encoding="utf-8"))["base_mesh"]


class TestParams(unittest.TestCase):
    def test_defaults_and_validation(self):
        p = normalize(block())
        self.assertEqual(p["mesh"]["plate_layers"], 3)
        b = block()
        b["web"]["length_mm"] = 200.0
        with self.assertRaises(ParamError):
            normalize(b)
        b = block()
        del b["plate"]["width_mm"]
        with self.assertRaises(ParamError):
            normalize(b)

    def test_zones(self):
        p = normalize(block())
        z = zones(p, 2.0)
        self.assertEqual(z["plate_planes"], [-15.0, -9.0, -3.0, 3.0, 9.0, 15.0])
        self.assertGreaterEqual(z["web_first_row_mm"], WEB_FIRST_ROW_MIN_MM)
        sizes = [s for _, s in z["plate_zones"]]
        self.assertEqual(sizes, sorted(sizes))

    def test_curvature_surface(self):
        p = normalize(block())
        self.assertEqual(surface_height(p, 30.0), 0.0)
        b = block()
        b["plate"]["curvature_radius_mm"] = 500.0
        p = normalize(b)
        self.assertAlmostEqual(surface_height(p, 50.0), 0.0)
        self.assertLess(surface_height(p, 0.0), 0.0)


class TestTcl(unittest.TestCase):
    def setUp(self):
        self.p = normalize(block())
        self.texts = {"plate": tcl_gen.plate_tcl(self.p, 2.0), "web": tcl_gen.web_tcl(self.p, 2.0)}

    def test_rules_pass(self):
        for role, text in self.texts.items():
            for name, res in tcl_gen.check_all(text).items():
                self.assertTrue(res["ok"], "%s %s %s" % (role, name, res["detail"]))

    def test_all_parameters_present(self):
        for text in self.texts.values():
            for g in ("plate", "web", "mesh", "quality"):
                for k, v in self.p[g].items():
                    self.assertIn("# PARAM %s.%s=%s" % (g, k, v), text)
        self.assertIn("*meshdragelements 1 1 6.0 3 0 0", self.texts["plate"])
        self.assertIn("*meshdragelements 1 1 6.0 2 0 0", self.texts["web"])

    def test_no_paths_or_private_names(self):
        for text in self.texts.values():
            self.assertIsNone(re.search(r"[A-Za-z]:[\\/]", text))
            self.assertNotIn("S0" + "26", text)
            self.assertNotIn("task" + "1", text)
            self.assertNotIn("*feoutput", text)

    def test_rule_violations_detected(self):
        t = self.texts["plate"]
        self.assertFalse(tcl_gen.check_tcl1(re.sub(r"^set ZONES \{.*\}$", "set ZONES {{3.0 10.0} {25.0 2.0}}", t, flags=re.M))[0])
        self.assertFalse(tcl_gen.check_tcl2(t + '\n*createmark nodes 3 "all"\n')[0])
        self.assertFalse(tcl_gen.check_tcl3(t + '\n*writefile "x.hm" 1\n')[0])
        self.assertTrue(tcl_gen.check_tcl3(t + '\nhm_answernext yes\n*writefile "x.hm" 1\n')[0])
        self.assertFalse(tcl_gen.check_tcl4(t + "\n*surfacemarksplitwithlines 1 2 0 13 0\n")[0])
        self.assertFalse(tcl_gen.check_tcl4(t + "\n*solid_split_by_tool 1 tool_type=line\n")[0])


def slab(nx, nz, layers, dx, dz, thick, y_top, x0=0.0):
    """nx x nz x layers hexahedra, top face at y_top, thickness downward."""
    idx, nodes = {}, []
    for k in range(layers + 1):
        for j in range(nz + 1):
            for i in range(nx + 1):
                idx[(i, j, k)] = len(nodes) + 1
                nodes.append((x0 + dx * i, y_top - thick * k / layers, dz * j))
    hexas = []
    for k in range(layers):
        for j in range(nz):
            for i in range(nx):
                hexas.append([idx[(i, j, k)], idx[(i + 1, j, k)], idx[(i + 1, j + 1, k)], idx[(i, j + 1, k)],
                              idx[(i, j, k + 1)], idx[(i + 1, j, k + 1)], idx[(i + 1, j + 1, k + 1)], idx[(i, j + 1, k + 1)]])
    return nodes, hexas


class TestMeshio(unittest.TestCase):
    def test_layers_and_gap_on_20_elements(self):
        flat = lambda z: 0.0
        with tempfile.TemporaryDirectory() as d:
            plate = os.path.join(d, "plate.bdf")
            nodes, hexas = slab(5, 2, 2, 10.0, 10.0, 6.0, 0.0)
            self.assertEqual(len(hexas), 20)
            write_bdf(plate, nodes, hexas)
            self.assertEqual(meshio.layers(plate), (2, 2))
            web = os.path.join(d, "web.bdf")
            wn, wh = slab(2, 2, 1, 3.0, 10.0, -10.0, 0.0, x0=-3.0)        # 4 elements from y=0 up to y=10
            write_bdf(web, wn, wh)
            g = meshio.contact_gap(web, plate, flat)
            self.assertEqual(g["gap_mm"], 0.0)
            self.assertFalse(g["penetration"])
            wn2 = [(x, y + 5e-4, z) for x, y, z in wn]
            write_bdf(web, wn2, wh)
            self.assertGreater(meshio.contact_gap(web, plate, flat)["gap_mm"], 1e-6)

    def test_raw_dump_to_r1_bdf(self):
        with tempfile.TemporaryDirectory() as d:
            raw = os.path.join(d, "raw.txt")
            nodes, hexas = slab(2, 1, 1, 2.0, 2.0, 2.0, 0.0)
            lines = ["N %d %r %r %r" % ((k + 1,) + tuple(v + 1e-14 for v in xyz)) for k, xyz in enumerate(nodes)]
            lines += ["E %d 208 %s" % (100 + k, " ".join(str(i) for i in (h[4:] + h[:4]))) for k, h in enumerate(hexas)]   # inverted winding
            io.open(raw, "w").write("\n".join(lines) + "\n")
            n, e = meshio.parse_raw(raw)
            coords, mesh = meshio.build(n, e)
            q = meshio.quality(coords, mesh)
            self.assertEqual((q["hex8"], q["penta6"], q["penta_fraction"]), (2, 0, 0.0))
            self.assertAlmostEqual(q["min_scaled_jacobian"], 1.0)
            out = os.path.join(d, "o.bdf")
            meshio.write_bdf(out, coords, mesh, "t")
            ok, st = check_r1(out)
            self.assertTrue(ok, st)
            self.assertIn("0.000000000e+00", io.open(out).read())


@unittest.skipUnless(HMBATCH and os.path.isfile(HMBATCH), "HyperMesh not configured (env WELDSIM_HMBATCH)")
class TestHmIntegration(unittest.TestCase):
    def test_synthetic_twice_identical(self):
        from hm.pipeline import run_hm
        shas = []
        for _ in range(2):
            with tempfile.TemporaryDirectory(prefix="hm_") as d:
                res = run_hm(block(), d, HMBATCH, log=lambda s: None)
                self.assertIsNotNone(res["final_attempt"], res["attempts"])
                self.assertGreaterEqual(res["plate_layers"], 3)
                shas.append(tuple(hashlib.sha256(io.open(os.path.join(d, "base_%s.bdf" % r), "rb").read()).hexdigest() for r in ("web", "plate")))
        self.assertEqual(shas[0], shas[1])


if __name__ == "__main__":
    unittest.main()
