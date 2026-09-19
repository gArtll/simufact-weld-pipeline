# -*- coding: utf-8 -*-
"""Test paths. Public tests use cases/ in the repository. Tests on private data run only when these are set:
  WELDSIM_PRIVATE_CASE       path to a private case json whose inputs/reference sit in <case dir>/<case name>/
  WELDSIM_REGRESSION_CASES   directory with the gate regression .dat copies (default gate/regression_cases)"""
import io
import json
import os
import sys
import unittest

TOOL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if TOOL not in sys.path:
    sys.path.insert(0, TOOL)
CASES = os.path.join(TOOL, "cases")
PRIVATE_CASE = os.environ.get("WELDSIM_PRIVATE_CASE", "")
HAVE_PRIVATE = os.path.isfile(PRIVATE_CASE)
CASE_DIR = os.path.join(os.path.dirname(PRIVATE_CASE), os.path.splitext(os.path.basename(PRIVATE_CASE))[0]) if HAVE_PRIVATE else ""
RC = os.environ.get("WELDSIM_REGRESSION_CASES") or os.path.join(TOOL, "gate", "regression_cases")
HAVE_RC = os.path.isdir(RC)

needs_private = unittest.skipUnless(HAVE_PRIVATE, "private case data not available (WELDSIM_PRIVATE_CASE)")
needs_rc = unittest.skipUnless(HAVE_RC, "regression .dat copies not available (WELDSIM_REGRESSION_CASES)")


def private_case():
    return json.load(io.open(PRIVATE_CASE, encoding="utf-8"))


def private_expect(test, key):
    """An expected value of a private-data test, from the private case file's `test_expect` block. Private models'
    counts and sizes are never written into this repository; without the value the test is skipped."""
    v = (private_case().get("test_expect") or {}).get(key)
    if v is None:
        test.skipTest("private case has no test_expect.%s" % key)
    return v


def private_path(rel):
    """Case-relative path of the private case -> absolute path."""
    return os.path.normpath(os.path.join(os.path.dirname(PRIVATE_CASE), rel))
