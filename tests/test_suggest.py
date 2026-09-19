# -*- coding: utf-8 -*-
"""weldsim suggest: ranking of parameter-library rows. The public library file holds its header only (rows of real
studies stay outside the public repository), so the ranking is tested on made-up rows built here."""
import csv
import io
import re
import unittest

from library.suggest import DEFAULT_CSV, rank


def row(case_id, joint, process, material, thickness, confidence="估算", exit_no="3004", **extra):
    r = {"case_id": case_id, "joint_type": joint, "welding_process": process, "base_material": material,
         "thickness_mm": thickness, "confidence": confidence, "solver_exit": exit_no, "weld_length_mm": "100"}
    r.update(extra)
    return r


ROWS = [row("synthetic-ring-a", "环焊+T形（圆筒立于平板，角焊）", "FCAW", "Q420D", "平板=10；圆筒壁=5"),
        row("synthetic-ring-b", "环焊+T形（圆筒立于平板，角焊）", "FCAW", "Q420D", "平板=12；圆筒壁=6"),
        row("synthetic-t", "T形+双侧角焊（腹板立于平板）", "GMAW", "S355", "腹板=8；平板=12"),
        row("synthetic-t-fail", "T形+双侧角焊（腹板立于平板）", "GMAW", "S355", "腹板=8；平板=12", exit_no="3015")]


class SuggestTests(unittest.TestCase):
    def test_public_library_is_header_only(self):
        with io.open(DEFAULT_CSV, encoding="utf-8-sig", newline="") as f:
            rd = csv.DictReader(f)
            self.assertIn("case_id", rd.fieldnames)
            self.assertEqual(list(rd), [])

    def test_ring_query_ranks_the_closer_thickness_first(self):
        got = rank(ROWS, "ring", "FCAW", "Q420", [5, 10])
        self.assertEqual([r["case_id"] for r in got][:2], ["synthetic-ring-a", "synthetic-ring-b"])

    def test_failed_solve_ranks_below(self):
        got = [r["case_id"] for r in rank(ROWS, "fillet", "GMAW", "S355", [8, 12])]
        self.assertLess(got.index("synthetic-t"), got.index("synthetic-t-fail"))

    def test_no_matching_joint(self):
        self.assertEqual(rank(ROWS, "butt", "GMAW", "304", [2, 2]), [])

    def test_score_uses_only_scoring_fields(self):
        """Blanking every non-scoring field (geometry included) leaves order and scores unchanged."""
        keep = {"case_id", "joint_type", "welding_process", "base_material", "thickness_mm", "confidence", "solver_exit"}
        blank = [{k: (v if k in keep else "") for k, v in r.items()} for r in ROWS]
        for q in (("ring", "FCAW", "Q420", [5, 10]), ("fillet", "GMAW", "S355", [8, 12])):
            a = [(r["case_id"], r["_score"]) for r in rank(ROWS, *q)]
            b = [(r["case_id"], r["_score"]) for r in rank(blank, *q)]
            self.assertEqual(a, b, q)

    def test_no_absolute_paths_in_the_library(self):
        self.assertIsNone(re.search(r"[A-Za-z]:[\/]", io.open(DEFAULT_CSV, encoding="utf-8-sig").read()))


if __name__ == "__main__":
    unittest.main()
