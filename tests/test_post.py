# -*- coding: utf-8 -*-
"""post: ArcToolCmd CSV reader on a hand-written file, probes resolved by coordinate (distance and the too-far case),
t8/5 and cooling rate on a cycle whose crossings can be worked out by hand, and the increment / result file selection.
Nothing here needs Simufact: real result files are not in the repository."""
import io
import json
import os
import sys
import tempfile
import unittest

from tests._paths import TOOL

sys.path.insert(0, os.path.join(TOOL, "run"))
import arccsv        # noqa: E402
import post          # noqa: E402
import probes        # noqa: E402

CSV = """Connectivity (nb, type, nb of nodes);1;7;8
1;1;2;3;4;5;6;7;8
Coordinates (nb);3
0;0.0;0.0;0.0
1;0.010;0.0;0.0
2;0.020;0.0;0.0
Post value (name, number);TEMPTURE;7
293.15
1500.0
293.15
1500.0
300.0
800.0
1500.0
Post value (name, number);EFFPLS;7
0.0
0.5
0.0
0.5
0.0
0.25
0.5
"""


class TestArcCsv(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.p = os.path.join(self.d.name, "x.csv")
        io.open(self.p, "w", encoding="utf-8").write(CSV)

    def tearDown(self):
        self.d.cleanup()

    def test_read_all_blocks(self):
        xyz, v = arccsv.read(self.p)
        self.assertEqual([list(r) for r in xyz], [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [20.0, 0.0, 0.0]])   # m -> mm
        self.assertEqual(sorted(v), ["EFFPLS", "TEMPTURE"])
        self.assertEqual(list(v["TEMPTURE"]), [300.0, 800.0, 1500.0])                                     # 4 preamble values dropped
        self.assertEqual(list(v["EFFPLS"]), [0.0, 0.25, 0.5])

    def test_want_filters_and_reports_missing(self):
        _, v = arccsv.read(self.p, want=["EFFPLS"])
        self.assertEqual(list(v), ["EFFPLS"])
        with self.assertRaises(ValueError):
            arccsv.read(self.p, want=["TOTDISP"])

    def test_post_value_names_documented(self):
        for k in ("TEMPTURE", "PKTEMP", "TOTDISP", "EFFPLS", "EFFSTS"):
            self.assertIn(k, arccsv.POST_VALUES)


class TestProbes(unittest.TestCase):
    NODES = [[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [20.0, 0.0, 0.0], [10.0, 5.0, 0.0]]

    def test_resolve_distance_and_limit(self):
        r = probes.resolve(self.NODES, [{"name": "on_node", "point_mm": [10.0, 0.0, 0.0], "max_distance_mm": 1.0},
                                        {"name": "between", "point_mm": [14.0, 0.0, 0.0], "max_distance_mm": 1.0},
                                        {"name": "between_ok", "point_mm": [14.0, 0.0, 0.0], "max_distance_mm": 5.0}])
        self.assertEqual((r[0]["node_index"], r[0]["distance_mm"], r[0]["within"]), (1, 0.0, True))
        self.assertEqual((r[1]["node_index"], r[1]["distance_mm"], r[1]["within"]), (1, 4.0, False))
        self.assertTrue(r[2]["within"])                    # same node, the limit is what differs

    def test_too_far_is_a_verdict_about_the_mesh(self):
        """Over the limit: still a value, but flagged as this mesh's own node and not comparable with another mesh."""
        r = probes.resolve(self.NODES, [{"name": "far", "point_mm": [14.0, 0.0, 0.0], "max_distance_mm": 1.0}])[0]
        self.assertFalse(r["comparable_across_meshes"])
        self.assertFalse(r["represents_point"])
        self.assertIn("cannot reliably represent this point", r["verdict"])
        self.assertIn("reference only", r["verdict"])
        line = probes.format_probe("far", r, probes.metrics([0, 1, 2], [300, 1500, 400]))
        self.assertTrue(line.startswith("[REFERENCE ONLY]"))
        self.assertIn("cannot reliably represent", line)

    def test_t85_by_hand(self):
        """1500 K peak, then -300 K/s: 800 degC at t=2.4228 s, 500 degC at t=3.4228 s, so t8/5 = 1.0 s."""
        m = probes.metrics([0, 1, 2, 3, 4, 5], [300, 1500, 1200, 900, 600, 400])
        self.assertEqual((m["peak_K"], m["time_at_peak_s"]), (1500.0, 1.0))
        self.assertAlmostEqual(m["t85_start_s"], 2 + (1200 - probes.T_HI) / 300.0)
        self.assertAlmostEqual(m["t85_end_s"], 3 + (900 - probes.T_LO) / 300.0)
        self.assertAlmostEqual(m["t85_s"], 1.0)
        self.assertAlmostEqual(m["cooling_rate_K_per_s"], 300.0)
        self.assertEqual((m["samples_in_window"], m["status"]), (1, "ok"))     # one sample in 800-500: coarse, and said so

    def test_no_t85_when_peak_too_low(self):
        m = probes.metrics([0, 1, 2], [300, 900, 400])
        self.assertEqual((m["peak_K"], m["t85_s"]), (900.0, None))
        self.assertIn("peak below", m["status"])

    def test_no_t85_when_still_hot_at_the_end(self):
        m = probes.metrics([0, 1, 2], [300, 1500, 900])
        self.assertIsNone(m["t85_s"])
        self.assertIn("does not reach", m["status"])

    def test_unsorted_and_empty(self):
        m = probes.metrics([2, 0, 1], [1200, 300, 1500])
        self.assertEqual(m["time_at_peak_s"], 1.0)
        self.assertEqual(probes.metrics([], [])["samples"], 0)


class TestSelection(unittest.TestCase):
    def test_select_keeps_first_and_last(self):
        incs = [0, 1, 2, 4, 6, 8, 11]
        self.assertEqual(post.select(incs, 1), incs)
        self.assertEqual(post.select(incs, 3), [0, 4, 11])
        self.assertEqual(post.select(incs, 4), [0, 6, 11])
        self.assertEqual(post.select(incs, 1, explicit=[2, 6, 99]), [2, 6])

    def test_find_arc_by_name_part(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "00021"))
            for n in ("P_FV_Comp-1-plateXm2_21.ARC", "P_FV_Comp-2-tubeXm2_21.ARC", "P_FV_Rob-ring_21.ARC", "P_Comp-1-plateXm2_21.spr"):
                io.open(os.path.join(d, "00021", n), "w").write("")
            self.assertTrue(post.find_arc(d, 21, "Comp-2-tubeXm2").endswith("P_FV_Comp-2-tubeXm2_21.ARC"))
            self.assertTrue(post.find_arc(d, 21, "tube").endswith("P_FV_Comp-2-tubeXm2_21.ARC"))
            with self.assertRaises(RuntimeError):
                post.find_arc(d, 21, "Comp")                                   # two matches: name the body
            with self.assertRaises(RuntimeError):
                post.find_arc(d, 21, "flange")

    def test_increment_times_without_sts(self):
        self.assertEqual(post.increment_times(None), {})

    def test_format_probe_without_any_metric(self):
        """No .sts means no time axis: the summary line must still print, with the reason instead of numbers."""
        r = probes.resolve([[0.0, 0.0, 0.0]], [{"name": "p", "point_mm": [0.0, 0.0, 0.0]}])[0]
        line = probes.format_probe("p", r, probes.metrics([], []))
        self.assertIn("no samples", line)
        self.assertIn("peak -", line)


STS = """  inc     load_case_name   tot_time  cyc1  conv_r    conv_d  cyc2  conv_t   prg(%)   pst   rst  walltime  elm_cnt
     0  Start simulation  0.0000E+00   0 0.000E+00 0.000E+00   0 0.000E+00   0.00     0     0       1.00        2
     1         welding_1  1.0000E+00   3 1.000E-03 0.000E+00   3 1.000E-01  25.00     1     0       2.00        2
     2         welding_1  2.0000E+00   3 1.000E-03 0.000E+00   3 1.000E-01  50.00     2     0       3.00        2
     3         cooling_1  3.0000E+00   3 1.000E-03 0.000E+00   3 1.000E-01  75.00     3     0       4.00        2
     4         cooling_1  4.0000E+00   3 1.000E-03 0.000E+00   3 1.000E-01 100.00     4     0       5.00        2
          job ends with exit number     3004
          total wall time:       5.00
          total cpu  time:      10.00
"""
CYCLE = {0: 300.0, 1: 1500.0, 2: 1200.0, 3: 900.0, 4: 600.0}      # same cycle as test_t85_by_hand, one step later


def fake_export(_arctool, arc, csv, posts=None):
    """Stand-in for ArcToolCmd: writes the CSV that increment would contain for a two-node body."""
    inc = int(os.path.basename(arc).rsplit("_", 1)[1].split(".")[0])
    out = ["Coordinates (nb);2", "0;0.0;0.0;0.0", "1;0.010;0.0;0.0"]
    for name in (posts or ["TEMPTURE"]):
        v = CYCLE[inc] if name == "TEMPTURE" else (max(CYCLE.values()) if name == "PKTEMP" else 0.5)
        out += ["Post value (name, number);%s;6" % name, "0", "0", "0", "0", "%g" % (v - 100.0), "%g" % v]
    io.open(csv, "w", encoding="utf-8").write("\n".join(out) + "\n")
    return csv


class TestPostMain(unittest.TestCase):
    """post.main() end to end with ArcToolCmd replaced: exercises the export loop, the history and the output files."""

    def test_writes_history_and_metrics(self):
        with tempfile.TemporaryDirectory() as d:
            res = os.path.join(d, "Proc", "_Results_")
            for inc in CYCLE:
                os.makedirs(os.path.join(res, "%05d" % inc))
                io.open(os.path.join(res, "%05d" % inc, "P_FV_Comp-1-plate_%d.ARC" % inc), "w").write("")
            sts = os.path.join(d, "x.sts")
            io.open(sts, "w", encoding="latin-1", newline="\n").write(STS)
            params = {"out_dir": os.path.join(d, "out"), "arctool": "unused", "proc_dir": os.path.join(d, "Proc"),
                      "sts": sts, "probes": [{"name": "toe", "body": "plate", "point_mm": [10.0, 0.0, 0.0], "max_distance_mm": 1.0}]}
            pj = os.path.join(d, "p.json")
            io.open(pj, "w", encoding="utf-8").write(json.dumps(params))
            real, post.arccsv.export = post.arccsv.export, fake_export
            argv, sys.argv = sys.argv, ["post.py", pj]
            try:
                self.assertEqual(post.main(), 0)
            finally:
                post.arccsv.export, sys.argv = real, argv
            hist = io.open(os.path.join(params["out_dir"], "probe_history.csv"), encoding="utf-8").read().splitlines()
            self.assertEqual(hist[0], "increment;time_s;toe")
            self.assertEqual(hist[1], "0;;300.0000")                  # increment 0 has no time in the .sts
            self.assertEqual(hist[-1], "4;4.000000;600.0000")
            data = json.load(io.open(os.path.join(params["out_dir"], "post.json"), encoding="utf-8"))
            m = data["probes"][0]["metrics"]
            self.assertEqual((m["peak_K"], m["time_at_peak_s"]), (1500.0, 1.0))
            self.assertAlmostEqual(m["t85_s"], 1.0)                   # crossings at 2.4228 s and 3.4228 s
            self.assertEqual(m["solver_peak_K"], 1500.0)
            self.assertEqual(m["peak_missed_by_sampling_K"], 0.0)
            self.assertEqual(data["probes"][0]["resolution"]["distance_mm"], 0.0)
            self.assertIn("Solver status: PASS", io.open(os.path.join(params["out_dir"], "post_summary.txt"), encoding="utf-8").read())

    def test_probe_too_far_fails_the_step(self):
        with tempfile.TemporaryDirectory() as d:
            res = os.path.join(d, "Proc", "_Results_")
            os.makedirs(os.path.join(res, "00000"))
            io.open(os.path.join(res, "00000", "P_FV_Comp-1-plate_0.ARC"), "w").write("")
            params = {"out_dir": os.path.join(d, "out"), "arctool": "unused", "proc_dir": os.path.join(d, "Proc"),
                      "probes": [{"name": "toe", "body": "plate", "point_mm": [50.0, 0.0, 0.0], "max_distance_mm": 1.0}]}
            pj = os.path.join(d, "p.json")
            io.open(pj, "w", encoding="utf-8").write(json.dumps(params))
            real, post.arccsv.export = post.arccsv.export, fake_export
            argv, sys.argv = sys.argv, ["post.py", pj]
            try:
                self.assertEqual(post.main(), 1)                      # 40 mm from the nearest node
            finally:
                post.arccsv.export, sys.argv = real, argv
            data = json.load(io.open(os.path.join(params["out_dir"], "post.json"), encoding="utf-8"))
            self.assertEqual(data["points_not_represented"], ["toe"])
            self.assertFalse(data["probes"][0]["resolution"]["comparable_across_meshes"])
            self.assertIn("[REFERENCE ONLY]", io.open(os.path.join(params["out_dir"], "post_summary.txt"), encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
