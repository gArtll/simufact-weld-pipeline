# -*- coding: utf-8 -*-
"""BDF read/write shared by prep, gate and tests (replaces the three copies in wl_v2 / check_detj / check_bead_nodes /
bead_outer_line). R1: Simufact imports only large-field GRID* + fixed-field CHEXA; write_bdf emits exactly that.
Pure Python (runs under the Simufact Python 3.10 as well)."""
import io
import re

_NASTRAN_EXP = re.compile(r"^([+-]?[\d.]+)([+-]\d+)$")


def _nf(text):
    text = text.strip()
    if not text:
        return 0.0
    m = _NASTRAN_EXP.match(text)
    return float(m.group(1) + "e" + m.group(2)) if m else float(text)


def read_bdf(path):
    """Return (nodes {id: (x,y,z)}, elements [ [8 node ids] ], stats dict).
    Supports GRID* (large field), GRID (small field), GRID, (free); CHEXA with '+' continuation; CPENTA counted."""
    lines = io.open(path, errors="ignore").read().splitlines()
    nodes, elems = {}, []
    stats = {"grid_star": 0, "grid_small": 0, "grid_free": 0, "chexa": 0, "cpenta": 0, "other_elements": 0}
    i = 0
    while i < len(lines):
        l = lines[i]
        if l.startswith("GRID*"):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            nodes[int(l[8:24])] = (float(l[40:56]), float(l[56:72]), float(nxt[8:24]))
            stats["grid_star"] += 1
            i += 1
        elif l.startswith("GRID,"):
            f = [v.strip() for v in l.split(",")]
            nodes[int(f[1])] = tuple(map(float, f[3:6]))
            stats["grid_free"] += 1
        elif l.startswith("GRID"):
            nodes[int(l[8:16])] = (_nf(l[24:32]), _nf(l[32:40]), _nf(l[40:48]))
            stats["grid_small"] += 1
        elif l.startswith("CHEXA"):
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            ids = [int(l[k:k + 8]) for k in range(24, 72, 8)] + [int(nxt[k:k + 8]) for k in (8, 16)]
            elems.append(ids)
            stats["chexa"] += 1
            i += 1
        elif l[:8].strip() == "CPENTA":
            stats["cpenta"] += 1
        i += 1
    return nodes, elems, stats


def check_r1(path):
    """R1 self-check: only GRID* and CHEXA (CPENTA tolerated for base meshes, reported). Returns (ok, stats)."""
    _, elems, st = read_bdf(path)
    ok = st["grid_star"] > 0 and st["grid_small"] == 0 and st["grid_free"] == 0 and st["chexa"] == len(elems) > 0
    return ok, st


def write_bdf(path, sections_or_nodes, hexas, title="weldsim_tool"):
    """nodes: list of (x,y,z) numbered 1..N in order; hexas: list of 8-id lists."""
    with io.open(path, "w", encoding="ascii", newline="\r\n") as f:
        f.write("$ %s\n$ Units: mm\nSOL 101\nCEND\nBEGIN BULK\n" % title)
        for nid, (x, y, z) in enumerate(sections_or_nodes, 1):
            f.write("%-8s%16d%16s%16.9e%16.9e%-8s\n" % ("GRID*", nid, "", x, y, "*"))
            f.write("%-8s%16.9e\n" % ("*", z))
        for eid, ids in enumerate(hexas, 1):
            f.write("%-8s%8d%8d%8d%8d%8d%8d%8d%8d%-8s\n" % (("CHEXA", eid, 1) + tuple(ids[:6]) + ("+",)))
            f.write("%-8s%8d%8d\n" % ("+", ids[6], ids[7]))
        f.write("ENDDATA\n")


def read_weldline_csv(path):
    """Active points of a Simufact weld line CSV 'order;activity;x;y;z' (mm)."""
    pts = []
    for l in io.open(path, encoding="utf-8", errors="ignore"):
        s = l.strip().split(";")
        if len(s) == 5 and s[1] == "true":
            pts.append(tuple(map(float, s[2:])))
    return pts


def csv_ends_with_newline(path):
    """Simufact hard rule 8."""
    return io.open(path, "rb").read().endswith(b"\n")
