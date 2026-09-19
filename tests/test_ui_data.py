# -*- coding: utf-8 -*-
import os
import unittest

from tests._paths import TOOL
from ui.data import STEP_DIRS, assemble_dashboard, scan_cases, scan_runs

# a finished full run (prep ... post) to render: runs/ is not in the repository, so the case name comes from outside
RUN_CASE = os.environ.get("WELDSIM_UI_RUN_CASE", "")
RUN = os.path.join(TOOL, "runs", RUN_CASE) if RUN_CASE else ""


class DashboardDataTests(unittest.TestCase):
    @unittest.skipUnless(RUN and os.path.isdir(RUN), "WELDSIM_UI_RUN_CASE not set to a finished run under runs/")
    def test_render_existing_manifests(self):
        view = assemble_dashboard(RUN_CASE, RUN)
        self.assertEqual([s["key"] for s in view["steps"]], [k for k, _ in STEP_DIRS])
        ran = [s for s in view["steps"] if os.path.isdir(os.path.join(RUN, s["directory"]))]
        self.assertGreaterEqual(len(ran), 6)
        self.assertEqual([s["status"] for s in ran], ["ok"] * len(ran))        # optional steps only when their directory is there
        self.assertTrue(all(3 <= len(s["metrics"]) <= 5 for s in ran))
        self.assertTrue(all(s["logs"] for s in ran))
        compare = next(s for s in view["steps"] if s["key"] == "compare")
        self.assertTrue(any(k == "最大温差" and str(v).endswith("%") for k, v in compare["metrics"]))
        run = next(s for s in view["steps"] if s["key"] == "run")
        self.assertIn(("求解退出码", 3004), run["metrics"])

    def test_sidebar_scans(self):
        names = {x["name"] for x in scan_cases()}
        self.assertTrue({"example_tube_synthetic", "example_synthetic"}.issubset(names))
        self.assertIsInstance(scan_runs("example_tube_synthetic"), list)


if __name__ == "__main__":
    unittest.main()
