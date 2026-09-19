# -*- coding: utf-8 -*-
"""Refuse absolute paths anywhere in the tracked text of the repository.

Machine paths belong in `config.yaml`, which is not tracked. This is the check that runs before every commit and in
CI; `tests/test_run_compare.py` runs a narrower version of it over the code directories only.

Usage: python tools/check_no_absolute_paths.py [--list]"""
import io
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BINARY = (".png", ".jpg", ".jpeg", ".gif", ".pdf", ".xmt", ".arc", ".ARC", ".dat", ".swproj", ".pyc")
PATTERNS = [
    (re.compile(r"""["'{]\s?[A-Za-z]:[\\/]"""), "drive letter in a quoted string"),
    # `%` before the letter is a printf field, not a drive: `r"Users of %s:\s*"` is a regex, `x %s:\path` is not a path
    (re.compile(r"(?<![A-Za-z0-9_.%])[A-Za-z]:\\\\?[A-Za-z0-9_]"), "drive letter path"),
    (re.compile(r"/(?:home|Users)/[A-Za-z0-9_.-]+/"), "home directory path"),
]
# Lines that legitimately look like a path and are not one. Keep this list short and specific.
ALLOW = re.compile(r"https?://|<SIMUFACT_ROOT>|<HYPERMESH_ROOT>|C--Users|例如|for example")


def tracked_files():
    try:
        # -z: names come unquoted, so a non-ASCII file name is scanned instead of silently failing isfile()
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
        return [n for n in out.decode("utf-8").split("\0") if n]
    except Exception:                                      # no git: walk instead, skipping the usual noise
        files = []
        for base, dirs, names in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "runs", "__pycache__", ".pytest_cache")]
            files += [os.path.relpath(os.path.join(base, n), ROOT) for n in names]
        return files


def scan_line(line):
    """Why `line` holds an absolute path, or None."""
    if ALLOW.search(line):
        return None
    for pat, why in PATTERNS:
        if pat.search(line):
            return why
    return None


def main(argv):
    hits = []
    for rel in tracked_files():
        if rel.endswith(BINARY):
            continue
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            continue
        for k, line in enumerate(io.open(path, encoding="utf-8", errors="replace"), 1):
            why = scan_line(line)
            if why:
                hits.append((rel, k, why, line.strip()[:120]))
    for rel, k, why, text in hits:
        print("%s:%d  %s\n    %s" % (rel, k, why, text))
    print("%d file(s) scanned, %d absolute path(s)" % (len(tracked_files()), len(hits)))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
