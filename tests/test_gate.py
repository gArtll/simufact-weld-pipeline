# -*- coding: utf-8 -*-
"""gate: .dat block parsing on the reference input, gate CLI usage error, self .dat from a prep run."""
import os
import subprocess
import sys
import unittest

from tests._paths import TOOL, CASE_DIR, RC, needs_private, needs_rc, private_expect
from common import dat as D


@needs_private
class TestDatBlocks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.A = D.load(os.path.join(CASE_DIR, "reference", "dat_ref.dat"))

    def test_ndsq(self):
        sets = D.ndsq_sets(self.A)
        self.assertEqual([len(ids) for _, ids in sets], [23, 23])

    def test_orientation_tables(self):
        tabs = D.tables(self.A, "table_nodal_connection")
        self.assertEqual(len(tabs), 6)
        rows = [D.fixed20(l) for l in D.nocomment(tabs[0])[3:]]
        self.assertEqual(len(rows), 23)
        self.assertTrue(all(len(r) == 2 for r in rows))

    def test_contact_and_autostep(self):
        self.assertEqual(len(D.section(self.A, "CONTACT TABLE")), 4)
        self.assertEqual(len(D.section(self.A, "AUTO STEP")), 3)

    def test_bead_sets_and_coords(self):
        sets = D.weld_fill_sets(self.A)
        self.assertEqual([len(D.element_set(self.A, n)) for n in sets], private_expect(self, "bead_set_elements"))
        C = D.coords(self.A)
        self.assertEqual(len(C), private_expect(self, "dat_nodes"))
        conn = D.connectivity(self.A)
        self.assertEqual(len(conn), private_expect(self, "dat_elements"))


class TestGateCli(unittest.TestCase):
    def test_missing_reference_is_usage_error(self):
        r = subprocess.run([sys.executable, os.path.join(TOOL, "gate", "check_short_v3.py"), os.path.join(TOOL, "weldsim.py")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 2)

    @needs_rc
    def test_regression_case_inputs_present(self):
        sys.path.insert(0, os.path.join(TOOL, "gate"))
        import run_regressions
        for name, _, args, *_ in run_regressions.CASES:
            if name.startswith("tube_on_plate"):
                continue
            for a in args:
                if a.endswith(".dat") or a.endswith("Proc"):
                    self.assertTrue(os.path.exists(a), "%s: %s" % (name, a))


if __name__ == "__main__":
    unittest.main()
