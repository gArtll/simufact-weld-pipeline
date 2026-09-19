# -*- coding: utf-8 -*-
"""MARC .dat block parsing (Simufact write_program_input output). Replaces the parsers duplicated in check_short_v3,
compare_scriptbead, bead_comparison and the probes. Coordinates in the .dat are in m; coords() returns mm."""
import io
import re

KEY = re.compile(r"^[A-Z][A-Z]")


def load(path):
    return io.open(path, encoding="latin-1").read().split("\n")


def nocomment(lines):
    return [s for s in lines if not s.startswith("$")]


def block(A, k):
    """Card at line k plus its data lines, stopping at a '$' line or the next keyword."""
    j = k + 1
    while j < len(A) and not A[j].startswith("$") and not KEY.match(A[j]):
        j += 1
    return A[k:j]


def section(A, head):
    """All occurrences of a keyword section (comments included) up to the next keyword."""
    res = []
    for k, s in enumerate(A):
        if s.startswith(head):
            j = k + 1
            while j < len(A) and not KEY.match(A[j]):
                j += 1
            res.append(A[k:j])
    return res


def tables(A, pattern):
    return [block(A, k) for k, s in enumerate(A) if re.match(r"TABLE\s+\d+_" + pattern, s)]


def fixed20(line):
    line = line.rstrip()
    return [float(line[i:i + 20]) for i in range(0, len(line), 20) if line[i:i + 20].strip()]


def coords(A):
    res, on = {}, False
    for s in A:
        if s.startswith("COORDINATES"):
            on = True
            continue
        if on and KEY.match(s):
            break
        if on and not s.startswith("$") and len(s) >= 70:
            try:
                res[int(s[:10])] = tuple(float(s[10 + 20 * i:30 + 20 * i]) * 1000.0 for i in range(3))
            except ValueError:
                pass
    return res


def connectivity(A):
    res, on = {}, False
    for s in A:
        if s.startswith("CONNECTIVITY"):
            on = True
            continue
        if on and KEY.match(s):
            break
        if on and not s.startswith("$"):
            t = s.split()
            if len(t) == 10:
                res[int(t[0])] = [int(v) for v in t[2:]]
    return res


def ndsq_sets(A):
    """[(set name, [node ids])] for trajectory NDSQ heat-source paths."""
    res = []
    for k, s in enumerate(A):
        if s.startswith("DEFINE") and "NDSQ" in s and "trajectory_nodes_" in s:
            ids, j = [], k + 1
            while j < len(A):
                t = A[j].rstrip()
                if t.startswith("$") or KEY.match(t):
                    break
                ids += [int(v) for v in t.split() if v.isdigit()]
                j += 1
            res.append((s.split()[-1], ids))
    return res


def element_set(A, name):
    for k, s in enumerate(A):
        if s.startswith("DEFINE") and "ELEMENT" in s and s.split()[-1] == name:
            ids = []
            for t in block(A, k)[1:]:
                toks = t.rstrip().rstrip("C").split()
                i = 0
                while i < len(toks):
                    if i + 2 < len(toks) and toks[i + 1] == "TO":
                        ids += list(range(int(toks[i]), int(toks[i + 2]) + 1))
                        i += 3
                    else:
                        if toks[i].isdigit():
                            ids.append(int(toks[i]))
                        i += 1
            return ids
    return []


def weld_fill_sets(A):
    k = next((i for i, s in enumerate(A) if s.startswith("WELD FILL")), None)
    res = []
    if k is None:
        return res
    for s in A[k + 1:]:
        if s.startswith("$") and res:
            break
        if s.strip().endswith("_el_set"):
            res.append(s.strip())
    return res
