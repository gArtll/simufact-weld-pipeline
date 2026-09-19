# -*- coding: utf-8 -*-
"""run / compare: .sts exit parsing (hard rule 6), end time / cooling step handling (hard rule 7), ArcTool CSV reader,
plus static rule checks on the package source (R6', no hard-coded absolute paths)."""
import io
import os
import re
import sys
import tempfile
import unittest

from tests._paths import TOOL
import weldsim

sys.path.insert(0, os.path.join(TOOL, "run"))
import compare_temperature  # noqa: E402


class TestRun(unittest.TestCase):
    def test_sts_exit(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.sts")
            io.open(p, "w").write("inc ...\nSimufact Solver 2024.2 based on MARC 2023.4:\n          job ends with exit number     3004\n total wall time: 1.0\n")
            self.assertEqual(weldsim.read_sts_exit(p), (3, 3004))

    def test_end_time_and_cooling_rule(self):
        src = os.path.join(TOOL, "cases", "example_synthetic", "inputs", "process_parameters.xml")
        ok, n = weldsim.check_cooling_step(src)
        self.assertTrue(ok)
        self.assertGreater(n, 0)
        with tempfile.TemporaryDirectory() as d:
            dst = weldsim.set_end_time(src, os.path.join(d, "p.xml"), 20.0)
            self.assertRegex(io.open(dst, encoding="utf-8").read(), r'<end_time\b[^>]*value="20.0"')


class TestCompare(unittest.TestCase):
    def test_arctool_csv_reader(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.csv")
            lines = ["Header;x", "Coordinates (nb);3", "1;0.001;0.0;0.0", "2;0.002;0.0;0.0", "3;0.003;0.0;0.0",
                     "Post value (name, number);TEMPTURE;1", "1", "2", "3", "4", "300.0", "400.0", "500.0"]
            io.open(p, "w", encoding="utf-8").write("\n".join(lines) + "\n")
            xyz, t = compare_temperature.read_csv(p)
            self.assertAlmostEqual(xyz[2][0], 3.0)
            self.assertEqual(list(t), [300.0, 400.0, 500.0])


class TestStaticRules(unittest.TestCase):
    CODE_DIRS = ("common", "prep", "build", "gate", "run", "library", "ui", "tools", "cases")

    def code_files(self):
        out = [os.path.join(TOOL, "weldsim.py")]
        for d in self.CODE_DIRS:
            for root, _, files in os.walk(os.path.join(TOOL, d)):
                if "regression" in root:
                    continue
                out += [os.path.join(root, f) for f in files if f.endswith((".py", ".tcl"))]
        return out

    def test_no_forbidden_simufact_calls(self):
        """R6': calculate_all_projections, delete_geometry, split_at are never called."""
        pat = re.compile(r"\.(calculate_all_projections|delete_geometry|split_at)\s*\(")
        hits = [f for f in self.code_files() if pat.search(io.open(f, encoding="utf-8").read())]
        self.assertEqual(hits, [])

    def test_orientation_called_once_with_radius(self):
        src = io.open(os.path.join(TOOL, "build", "build_project.py"), encoding="utf-8").read()
        self.assertEqual(len(re.findall(r"\.calculate_all_orientations\(", src)), 1)
        self.assertIn("search_radius=", src)
        self.assertIn('wl.orientation = "local-vector"', src)

    def test_no_absolute_paths_in_code(self):
        pat = re.compile(r"""["'{][A-Za-z]:[\\/]""")
        hits = []
        for f in self.code_files():
            for k, line in enumerate(io.open(f, encoding="utf-8"), 1):
                if pat.search(line):
                    hits.append("%s:%d %s" % (os.path.relpath(f, TOOL), k, line.strip()))
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
