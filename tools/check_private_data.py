# -*- coding: utf-8 -*-
"""Refuse private data, credentials and unregistered data files in the tracked tree (and in commit messages).

What `check_no_absolute_paths.py` cannot see is the data itself. This check looks for four things:

  secret         private keys, access tokens, credential assignments
  private        internal case codes and markers of unpublished work (generic patterns below), plus the names of
                 unpublished models from a denylist kept outside the repository (private_denylist)
  personal       chat-app ids and paths
  unregistered   a data file (STEP, BDF, XML, CSV, solver logs, project exports, ...) that is not registered in
                 tools/public_data_allowlist.yaml as synthetic or public

Commit messages are checked for the first three with `--commits <rev-range>` (CI: the pushed commits).

The marker patterns are assembled from fragments so that this file does not match itself; the regex literals
elsewhere in the code (e.g. a guard pattern written with escapes) do not match either.

Usage: python tools/check_private_data.py [--commits RANGE] [--list]"""
import fnmatch
import io
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWLIST = os.path.join(ROOT, "tools", "public_data_allowlist.yaml")
DATA_EXT = (".stp", ".step", ".igs", ".iges", ".stl", ".x_t", ".xmt", ".bdf", ".nas", ".fem", ".inp", ".xml", ".csv",
            ".sts", ".out", ".dat", ".log", ".arc", ".swproj", ".jmt", ".json", ".png", ".jpg", ".jpeg", ".tcl",
            ".zip", ".7z", ".rar", ".h5", ".vtk", ".vtu", ".odb", ".t16", ".t19")
# never allowed, whatever the allowlist says: solver input decks, result files, tooling geometry, material cards and
# Simufact project files (licensed or project data)
NEVER_EXT = (".dat", ".arc", ".xmt", ".swproj", ".jmt", ".t16", ".t19")
BINARY = (".png", ".jpg", ".jpeg", ".zip", ".7z", ".rar", ".h5", ".odb", ".t16", ".t19", ".pyc")
SELF = "tools/check_private_data.py"


def _j(*parts):
    return "".join(parts)


PATTERNS = {
    "secret": [
        (_j("-----BEGIN [A-Z ]*PRIV", "ATE KEY-----"), "private key block"),
        (_j(r"\bgh[pousr]_", r"[A-Za-z0-9]{30,}"), "GitHub token"),
        (_j(r"\bAK", r"IA[0-9A-Z]{16}\b"), "AWS access key id"),
        (_j(r"\bsk-", r"[A-Za-z0-9_-]{24,}"), "API secret key"),
        (_j(r"(?i)\b(pass", r"word|passwd|secret|api[_-]?key|access[_-]?token)\s*[:=]\s*['\"][^'\"\s]{6,}['\"]"),
         "credential assignment"),
    ],
    "private": [
        (_j(r"\bS", r"0\d\d\b"), "internal case code S0xx"),
        (_j(r"[(（]priv", r"ate[)）]"), "'(private)' marker on a value"),
        (_j(r"\b(runs|cases)_priv", r"ate\b|\bprivate form", r"al\b"), "private work directory"),
    ],
    "personal": [
        (_j(r"\bwx", r"id_[a-z0-9]{6,}"), "WeChat id"),
        (_j(r"xwechat_", r"files|Tencent ", r"Files"), "chat application path"),
    ],
}


def private_denylist():
    """Names of unpublished models, meshes, CAD files and work directories. They are exactly what may not be in this
    repository, so they are not written here either: one regex per line, from the file named by
    WELDSIM_PRIVATE_DENYLIST and/or the variable WELDSIM_PRIVATE_DENYLIST_PATTERNS (CI: an Actions secret)."""
    lines = []
    f = os.environ.get("WELDSIM_PRIVATE_DENYLIST", "")
    if f and os.path.isfile(f):
        lines += io.open(f, encoding="utf-8").read().splitlines()
    lines += os.environ.get("WELDSIM_PRIVATE_DENYLIST_PATTERNS", "").splitlines()
    return [(l.strip(), "private denylist entry") for l in lines if l.strip() and not l.strip().startswith("#")]


def compile_patterns():
    pats = dict(PATTERNS, private=PATTERNS["private"] + private_denylist())
    return {k: [(re.compile(p), why) for p, why in v] for k, v in pats.items()}


COMPILED = compile_patterns()


def tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout
    return [n for n in out.decode("utf-8").split("\0") if n]


def scan_text(text, categories=("secret", "private", "personal")):
    """[(category, why, match)] for one text."""
    hits = []
    for cat in categories:
        for rx, why in COMPILED[cat]:
            m = rx.search(text)
            if m:
                hits.append((cat, why, m.group(0)))
    return hits


def load_allowlist(path=ALLOWLIST):
    return [e["glob"] for e in (yaml.safe_load(io.open(path, encoding="utf-8")) or {}).get("entries", [])]


def unregistered(files, globs):
    return [f for f in files if f.lower().endswith(NEVER_EXT)
            or (f.lower().endswith(DATA_EXT) and not any(fnmatch.fnmatch(f, g) for g in globs))]


def scan_tree(files=None):
    files = tracked_files() if files is None else files
    hits = []
    for rel in files:
        if rel == SELF or rel.lower().endswith(BINARY):
            continue
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            continue
        for k, line in enumerate(io.open(path, encoding="utf-8", errors="replace"), 1):
            for cat, why, m in scan_text(line):
                hits.append((rel, k, cat, why, m))
    for rel in unregistered(files, load_allowlist()):
        hits.append((rel, 0, "unregistered", "data file not in tools/public_data_allowlist.yaml", ""))
    return hits


def scan_commits(rev_range):
    out = subprocess.run(["git", "log", "--format=%H%x00%B%x01", rev_range], cwd=ROOT, capture_output=True,
                         check=True).stdout.decode("utf-8", "replace")
    hits = []
    for rec in out.split("\x01"):
        if "\x00" not in rec:
            continue
        h, body = rec.strip().split("\x00", 1)
        for cat, why, m in scan_text(body):
            hits.append(("commit " + h[:7], 0, cat, why, m))
    return hits


def main(argv):
    hits = scan_tree()
    if "--commits" in argv:
        hits += scan_commits(argv[argv.index("--commits") + 1])
    n = len(private_denylist())
    print("private denylist: %d pattern(s)%s" % (n, "" if n else " -- set WELDSIM_PRIVATE_DENYLIST(_PATTERNS) to check "
                                                              "the names of unpublished models too"))
    for rel, k, cat, why, m in hits:
        # a denylist hit is reported without the matched text: the log must not repeat what it found
        shown = "<denylisted>" if why == "private denylist entry" else repr(m[:60])
        print("%s:%d  [%s] %s  %s" % (rel, k, cat, why, shown))
    print("%d file(s) scanned, %d finding(s)" % (len(tracked_files()), len(hits)))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
