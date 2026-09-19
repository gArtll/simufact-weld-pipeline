# -*- coding: utf-8 -*-
"""Pull a 2D face mesh out of a 3D BDF, so an existing swept mesh can be compared with an explicit transition.

A web or plate meshed in 2D and dragged through its thickness has all of its topology on one face: the quads and
triangles of that face are what became hexahedra and wedges. Reading the face back turns the 3D mesh into the 2D
problem the templates solve, without any assumption about how it was made.

Generic: takes a file, a plane, and two in-plane axes. Nothing here knows about any particular model."""
import io
import math
import re

AXES = {"x": 0, "y": 1, "z": 2}
_EXP = re.compile(r"^([+-]?[\d.]+)([+-]\d+)$")


def _nf(s):
    """A NASTRAN small field: 1.23+4 means 1.23e4."""
    s = s.strip()
    if not s:
        return 0.0
    m = _EXP.match(s)
    return float(m.group(1) + "E" + m.group(2)) if m else float(s)


def read_solid_bdf(path):
    """(nodes {id: (x, y, z)}, elements [(kind, [ids])]) for GRID / GRID* with CHEXA and CPENTA."""
    lines = io.open(path, encoding="latin-1", errors="ignore").read().splitlines()
    nodes, elems, i = {}, [], 0
    while i < len(lines):
        l = lines[i]
        if l.startswith("GRID*"):
            nodes[int(l[8:24])] = (float(l[40:56]), float(l[56:72]), float(lines[i + 1][8:24]))
            i += 2
            continue
        if l.startswith("GRID"):
            nodes[int(l[8:16])] = (_nf(l[24:32]), _nf(l[32:40]), _nf(l[40:48]))
        elif l.startswith("CHEXA"):
            elems.append(("hex8", [int(l[k:k + 8]) for k in range(24, 72, 8)] +
                          [int(lines[i + 1][k:k + 8]) for k in (8, 16)]))
            i += 2
            continue
        elif l[:8].strip() == "CPENTA":
            elems.append(("penta6", [int(l[k:k + 8]) for k in range(24, 72, 8)]))
        i += 1
    return nodes, elems


def face_cells(nodes, elems, axis, value, tol=1e-3):
    """Cells of the face at `axis` = `value`: a hexahedron contributes 4 nodes there, a wedge 3.

    Returns (face node ids, [(3d kind, [face node ids])]). A wedge that shows 3 nodes on the face is a triangle of
    the original 2D mesh; a wedge lying the other way round shows 4 and is not counted as one."""
    k = AXES[axis]
    on = {i for i, p in nodes.items() if abs(p[k] - value) <= tol}
    cells = []
    for kind, ids in elems:
        f = [i for i in ids if i in on]
        if len(f) in (3, 4):
            cells.append((kind, f))
    return on, cells


def levels(nodes, axis, ndigits=3):
    """The distinct coordinates along one axis with their node counts: how a swept mesh shows its layers."""
    k, out = AXES[axis], {}
    for p in nodes.values():
        out[round(p[k], ndigits)] = out.get(round(p[k], ndigits), 0) + 1
    return sorted(out.items())


def order_ring(points):
    """Order the four (or three) face nodes of one cell around its perimeter, counter-clockwise."""
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    idx = sorted(range(len(points)), key=lambda i: math.atan2(points[i][1] - cy, points[i][0] - cx))
    return idx


def boundary_chain(cells_2d):
    """Edges used by a single cell: the outline of the face mesh."""
    use = {}
    for ids in cells_2d:
        for a, b in [(ids[i], ids[(i + 1) % len(ids)]) for i in range(len(ids))]:
            use[(min(a, b), max(a, b))] = use.get((min(a, b), max(a, b)), 0) + 1
    return [e for e, c in use.items() if c == 1]


def resample(poly, step):
    """A polyline re-cut at a uniform arc-length `step`, corners preserved by linear interpolation.

    A boundary read off an existing mesh inherits that mesh's node spacing, including any odd long segment. Mapping
    a template through such a polyline puts the distortion of that one segment straight into the elements sitting on
    it, which then looks like a fault of the template."""
    p = [tuple(map(float, q)) for q in poly]
    d = [0.0]
    for a, b in zip(p, p[1:]):
        d.append(d[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total = d[-1]
    n = max(2, int(round(total / float(step))) + 1)
    out, k = [], 0
    for j in range(n):
        s_at = total * j / (n - 1.0)
        while k + 2 < len(d) and d[k + 1] < s_at:
            k += 1
        span = d[k + 1] - d[k]
        t = 0.0 if span <= 0 else (s_at - d[k]) / span
        out.append((p[k][0] + t * (p[k + 1][0] - p[k][0]), p[k][1] + t * (p[k + 1][1] - p[k][1])))
    return out
