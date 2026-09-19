# -*- coding: utf-8 -*-
"""solver_stats: the .sts increment table and the .out scan.

Two kinds of input: hand-written logs for the edge cases (a missing .out, two solver domains, exit 3004 stopping
short), and the real log excerpts in tests/failure_logs -- one PASS, one PASS with warnings, two FAIL -- so the
verdict cannot drift on real files. Complete solver logs are not in the repository."""
import io
import os
import sys
import tempfile
import unittest

from tests._paths import TOOL

sys.path.insert(0, os.path.join(TOOL, "run"))
import solver_stats  # noqa: E402

HEAD = """Simufact Solver 2024.2 based on MARC 2023.4, build 943692 (2024/05/30)
job name : Proc-1-X

  inc     load_case_name   tot_time  cyc1  conv_r    conv_d  cyc2  conv_t   prg(%)   pst   rst  walltime  elm_cnt
-----------------------------------------------------------------------------------------------------------------
     0      Initializing  0.0000E+00   0 0.000E+00 0.000E+00   0 0.000E+00  10.00     0     0       3.11        0
     0  Start simulation  0.0000E+00   0 0.000E+00 0.000E+00   0 0.000E+00   0.00     0     0       3.97      100
"""

CLEAN = HEAD + """     1         welding_1  4.0000E-01   4 1.148E-02 0.000E+00   5 9.455E-01   0.04     1     0       9.65      100
     2         welding_1  8.0000E-01   7 6.156E-02 0.000E+00   5 4.696E-01  50.00     1     0      18.21      100
     3         cooling_1  2.0000E+00   5 5.604E-05 0.000E+00   2 3.834E-08 100.00     3     0      25.00      100
=======================================
Simufact Solver 2024.2 based on MARC 2023.4:
          job ends with exit number     3004
          total wall time:      25.00
          total cpu  time:     100.00
"""

FAILED = HEAD + """     1         welding_1  4.0000E-01   5 3.850E-02 0.000E+00  32 3.273E+00   0.04     1     0      32.43      100
     2         welding_1  8.0000E-01   9 8.509E-02 0.000E+00   4 5.181E-01   0.08     1     1      57.65      100
     2         welding_1  9.0000E-01  45 1.514E-02 0.000E+00  10 8.721E-01  85.29     1     4     215.23      100
          job ends with exit number     3015
          total wall time:     215.23
          total cpu  time:     900.00
"""

OUT_CLEAN = """             *** warning - Poisson's ratio is less than -1.0 or greater than .5, but table ID is given.
             *** warning - Search order is fixed due to glue control.
             Number of warning messages is 2
"""

OUT_FAILED = """             *** warning - Search order is fixed due to glue control.
             *** error - element inside out at element     27938 integration point     2
             *** error - element inside out at element     27939 integration point     2
             *** error - element inside out at element     27939 integration point     4
             Number of warning messages is 1
"""


def write(d, name, text):
    p = os.path.join(d, name)
    io.open(p, "w", encoding="latin-1", newline="\n").write(text)
    return p


class SolverStatsTests(unittest.TestCase):
    def test_clean_run(self):
        with tempfile.TemporaryDirectory() as d:
            r = solver_stats.stats(write(d, "a.sts", CLEAN), write(d, "a.out", OUT_CLEAN))
            self.assertEqual(r["exit_number"], 3004)
            self.assertEqual((r["increments"], r["increment_lines"], r["retried_increments"]), (3, 3, 0))
            self.assertEqual((r["post_file_writes"], r["restart_file_writes"]), (3, 0))
            self.assertEqual(r["load_cases"], ["cooling_1", "welding_1"])
            self.assertEqual((r["wall_time_s"], r["cpu_time_s"]), (25.0, 100.0))
            self.assertEqual((r["final_time_s"], r["final_progress_percent"], r["elements"]), (2.0, 100.0, 100))
            self.assertEqual((r["inverted_elements"], r["warning_lines"], r["reported_warning_count"]), (0, 2, 2))
            self.assertEqual(r["out"], ["a.out"])
            self.assertTrue(r["pass"] and r["clean"] and not r["warnings"])
            self.assertIn("Solver status: PASS", solver_stats.format_stats(r))

    def test_failed_run_cutbacks_and_inverted_elements(self):
        with tempfile.TemporaryDirectory() as d:
            r = solver_stats.stats(write(d, "b.sts", FAILED), write(d, "b.out", OUT_FAILED))
            self.assertEqual((r["exit_number"], r["status"]), (3015, "FAIL"))
            self.assertEqual((r["increments"], r["increment_lines"], r["retried_increments"]), (2, 3, 1))
            self.assertEqual(r["restart_file_writes"], 4)          # a file-write counter, not a measure of trouble
            self.assertEqual(r["inverted_elements"], 2)                      # two distinct element ids
            self.assertEqual(r["inverted_element_ids"], [27938, 27939])
            self.assertEqual(r["max_iterations"], 45)
            self.assertFalse(r["pass"])

    def test_exit_3004_but_incomplete_is_not_pass(self):
        with tempfile.TemporaryDirectory() as d:
            text = CLEAN.replace("100.00     3     0", " 85.00     3     0")
            r = solver_stats.stats(write(d, "c.sts", text), None)
            self.assertEqual(r["exit_number"], 3004)
            self.assertFalse(r["pass"])

    def test_out_missing(self):
        with tempfile.TemporaryDirectory() as d:
            r = solver_stats.stats(write(d, "d.sts", CLEAN), os.path.join(d, "nope.out"))
            self.assertIsNone(r["out"])
            self.assertTrue(r["pass"])                                        # the run finished; only the scan is missing
            self.assertFalse(r["clean"])
            self.assertEqual(r["warnings"], [".out not scanned: inverted elements unknown"])

    def test_two_solver_domains(self):
        """Two domains write one .out each; an element inside out in the second must not be missed."""
        with tempfile.TemporaryDirectory() as d:
            a, b = write(d, "1e.out", OUT_CLEAN), write(d, "2e.out", OUT_FAILED)
            r = solver_stats.stats(write(d, "e.sts", CLEAN), [a, b])
            self.assertEqual(r["inverted_elements"], 2)
            self.assertEqual(r["reported_warning_count"], 3)                  # 2 + 1, summed per file
            self.assertTrue(r["pass"])                                        # exit 3004 at 100 %: the run finished
            self.assertFalse(r["clean"])                                      # but it did not run clean
            self.assertEqual(r["status"], "PASS with warnings")
            self.assertIn("2 element(s) inside out", r["warnings"])


class RealLogTests(unittest.TestCase):
    """The logs in tests/failure_logs, every verdict: PASS, PASS with warnings and two FAIL runs, all synthetic
    (make_synthetic.py) in the Simufact / MARC line formats. No log of a real run is kept in the repository."""

    LOGS = os.path.join(TOOL, "tests", "failure_logs")
    SYNTHETIC = ("synthetic_exit3015_a", "synthetic_exit3015_b", "synthetic_pass", "synthetic_pass_with_warnings")
    EXPECTED = {
        "synthetic_pass": {
            "status": "PASS", "exit_number": 3004, "elements": 9000, "final_progress_percent": 100.0,
            "inverted_elements": 0, "error_lines": 0, "retried_increments": 0, "reported_warning_count": 120,
            "wall_time_s": 6120.4, "cpu_time_s": 30110.8},
        "synthetic_pass_with_warnings": {
            "status": "PASS with warnings", "exit_number": 3004, "elements": 16000, "final_progress_percent": 100.0,
            "inverted_elements": 1, "error_lines": 2, "retried_increments": 1, "reported_warning_count": 410,
            "wall_time_s": 14880.1, "cpu_time_s": 88020.55, "first_inverted": 12011},
        "synthetic_exit3015_a": {
            "status": "FAIL", "exit_number": 3015, "elements": 12000, "final_progress_percent": 62.4,
            "inverted_elements": 10, "error_lines": 23, "retried_increments": 0, "max_iterations": 88,
            "reported_warning_count": 640, "wall_time_s": 12510.25, "cpu_time_s": 61880.4, "first_inverted": 401},
        "synthetic_exit3015_b": {
            "status": "FAIL", "exit_number": 3015, "elements": 15500, "final_progress_percent": 49.5,
            "inverted_elements": 10, "error_lines": 20, "retried_increments": 4, "max_iterations": 96,
            "reported_warning_count": 910, "wall_time_s": 11890.75, "cpu_time_s": 58420.1, "first_inverted": 733},
    }

    def stats_of(self, name):
        return solver_stats.stats(os.path.join(self.LOGS, name + ".sts"), [os.path.join(self.LOGS, name + ".out")])

    def test_each_verdict_parses_to_what_was_recorded(self):
        for name, want in self.EXPECTED.items():
            r = self.stats_of(name)
            for k, v in want.items():
                if k == "first_inverted":
                    self.assertEqual(r["inverted_element_ids"][0], v, name)
                else:
                    self.assertEqual(r[k], v, "%s %s" % (name, k))
            self.assertEqual(r["pass"], want["status"] != "FAIL", name)

    def test_a_finished_run_with_an_inverted_element_is_not_a_failure(self):
        """Exit 3004 at 100 %, one element inside out, one retried increment: finished, not clean."""
        r = self.stats_of("synthetic_pass_with_warnings")
        self.assertTrue(r["pass"])
        self.assertFalse(r["clean"])
        self.assertEqual(sorted(r["warnings"]), ["1 element(s) inside out", "1 retried increment(s)"])

    def test_fail_fixtures_are_synthetic_and_regenerable(self):
        """The FAIL fixtures say what they are, and make_synthetic.py writes exactly them."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("mk", os.path.join(self.LOGS, "make_synthetic.py"))
        mk = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mk)
        keys = {v: k for k, v in mk.FILES.items()}
        for name in self.SYNTHETIC:
            key = keys[name]
            for ext, fn in ((".sts", mk.sts), (".out", mk.out)):
                text = io.open(os.path.join(self.LOGS, name + ext), encoding="latin-1").read()
                self.assertTrue(text.startswith("# SYNTHETIC"), name + ext)
                self.assertEqual(text, fn(mk.CASES[key]), name + ext)
            r = solver_stats.stats(os.path.join(self.LOGS, name + ".sts"))
            self.assertEqual(r["final_time_s"], 0.0, name)

    def test_no_excerpt_holds_coordinates(self):
        """The connectivity and nodal coordinate block that follows an inside-out error must not have been copied."""
        for name in self.EXPECTED:
            for ext in (".sts", ".out"):
                lines = io.open(os.path.join(self.LOGS, name + ext), encoding="latin-1").read().splitlines()
                body = "\n".join(l for l in lines if not l.startswith("#"))      # the header says what was removed
                self.assertNotIn("nodal coordinates", body)
                self.assertNotIn("element connectivity", body)


if __name__ == "__main__":
    unittest.main()
