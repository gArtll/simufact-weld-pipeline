# -*- coding: utf-8 -*-
"""tools/check_private_data.py: internal model codes, private markers, credentials and unregistered data files are
caught; ordinary code and docs are not. Sample strings are assembled at run time so this file stays clean."""
import os
import sys
import unittest

TOOL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(TOOL, "tools"))
import check_private_data as cpd  # noqa: E402


def j(*p):
    return "".join(p)


class PrivateDataScanner(unittest.TestCase):
    def cats(self, text):
        return {c for c, _, _ in cpd.scan_text(text)}

    def test_internal_codes_and_markers_caught(self):
        for text in (j("model S", "099 formal mesh"), j("the S", "011 review"), j("leg 5.4（priv", "ate）"),
                     j("value (priv", "ate)"), j("work/runs_priv", "ate/x"), j("the private form", "al mesh")):
            self.assertIn("private", self.cats(text), text)

    def test_denylist_from_the_environment(self):
        """Names of unpublished models are never written into the repository: they come from outside."""
        import importlib
        text = j("the ACME", "-7Q plate model")
        self.assertNotIn("private", self.cats(text))
        os.environ["WELDSIM_PRIVATE_DENYLIST_PATTERNS"] = j("ACME", r"-7\w")
        try:
            importlib.reload(cpd)
            self.assertIn("private", {c for c, _, _ in cpd.scan_text(text)})
        finally:
            del os.environ["WELDSIM_PRIVATE_DENYLIST_PATTERNS"]
            importlib.reload(cpd)

    def test_credentials_and_personal_caught(self):
        self.assertIn("secret", self.cats(j("-----BEGIN RSA PRIV", "ATE KEY-----")))
        self.assertIn("secret", self.cats(j("gh", "p_", "a" * 36)))
        self.assertIn("secret", self.cats(j('pass', 'word = "hunter22"')))
        self.assertIn("personal", self.cats(j("files/wx", "id_abcdef123456/msg")))

    def test_ordinary_code_is_not_flagged(self):
        for text in ('PRIVATE_CASE = os.environ.get("WELDSIM_PRIVATE_CASE", "")',
                     "cases/private/", "needs_private = unittest.skipUnless(HAVE_PRIVATE, ...)",
                     r'pat = re.compile(r"\bS0\d\d\b|" + names)', "GRID*   1173   2.526316000e+02",
                     "an unpublished T-joint model", "token = tokens[0]", "api_key = cfg.get('api_key')",
                     "S0 = 1.0; S02 = 2.0", "0.26 mm", "version 2.26.1"):
            self.assertEqual(self.cats(text), set(), text)

    def test_unregistered_data_file(self):
        globs = ["cases/example_synthetic/inputs/*.bdf"]
        self.assertEqual(cpd.unregistered(["cases/example_synthetic/inputs/a.bdf", "weldsim.py", "x/model.stp",
                                           "runs/r/job.sts", "docs/a.md"], globs), ["x/model.stp", "runs/r/job.sts"])
        # solver decks, results, tooling and material cards are refused even when an entry would match them
        self.assertEqual(cpd.unregistered(["cases/c/inputs/clamp.arc", "cases/c/ref.dat", "m/steel.xmt"], ["cases/*", "m/*"]),
                         ["cases/c/inputs/clamp.arc", "cases/c/ref.dat", "m/steel.xmt"])

    def test_current_tree_is_clean(self):
        hits = cpd.scan_tree()
        self.assertEqual(hits, [], hits[:10])

    def test_every_allowlist_entry_matches_a_file(self):
        """A stale entry would silently allow a future file of the same name."""
        import fnmatch
        files = cpd.tracked_files()
        for g in cpd.load_allowlist():
            self.assertTrue(any(fnmatch.fnmatch(f, g) for f in files), g)


if __name__ == "__main__":
    unittest.main()
