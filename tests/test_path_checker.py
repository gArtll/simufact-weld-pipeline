# -*- coding: utf-8 -*-
"""tools/check_no_absolute_paths.py: real drive/home paths are caught, regex/printf text that only looks like one is
not. Paths are assembled at run time so this file does not trip the checker itself."""
import os
import sys
import unittest

TOOL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(TOOL, "tools"))
from check_no_absolute_paths import scan_line, tracked_files  # noqa: E402

COLON = ":"
BS = "\\"


def drive(letter, rest):
    return letter + COLON + BS + rest


class PathChecker(unittest.TestCase):
    def test_real_paths_caught(self):
        for line in ['x = r"%s"' % drive("E", "Simufact Welding" + BS + "simufact"),
                     "x = '%s'" % drive("C", "Users" + BS + "me"),
                     'os.environ.get("HOME", r"%s")' % drive("E", "WeldSim"),
                     "copy it to %s first" % drive("D", "data" + BS + "case.json"),
                     'p = "%s"' % ("E" + COLON + "/WeldSim"),
                     "see /home" + "/someone/project"]:
            self.assertIsNotNone(scan_line(line), line)

    def test_printf_regex_not_a_drive(self):
        for line in [r'm = re.search(r"Users of %s:\s*\(Total of (\d+)" % re.escape(f), out)',
                     r'pat = r"%s:\d+"',
                     r'fmt = "%d:%s"']:
            self.assertIsNone(scan_line(line), line)

    def test_real_path_after_percent_word_still_caught(self):
        # the % exclusion applies only to the character right before the letter
        self.assertIsNotNone(scan_line("100% sure it is at " + drive("C", "tools")))

    def test_non_ascii_names_listed_unquoted(self):
        for name in tracked_files():
            self.assertFalse(name.startswith('"'), name)
            self.assertNotIn("\\3", name)


if __name__ == "__main__":
    unittest.main()
