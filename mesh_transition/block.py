# -*- coding: utf-8 -*-
"""Mapped quadrilateral blocks, and the pieces needed to lay a web out in them.

A transition band follows one boundary. The rest of a part does not: it is a region with several boundaries, and the
regular way to fill it with quadrilaterals is to cut it into four-sided blocks and map a grid onto each. Blocks that
share a side share its nodes, so the result has no hanging node anywhere and needs no unstructured filler.

The map is the Coons patch (transfinite interpolation): the interior of a block is decided entirely by its four
boundary curves, which is what makes two blocks meeting on a curve agree along it without any further work."""
import math

from .mesh import Mesh


def arc_lengths(poly):
    d = [0.0]
    for i in range(len(poly) - 1):
        d.append(d[-1] + math.hypot(poly[i + 1][0] - poly[i][0], poly[i + 1][1] - poly[i][1]))
    return d


def point_at(poly, s):
    """The point at arc length `s` along a polyline, clamped to its ends."""
    d = arc_lengths(poly)
    s = min(max(s, 0.0), d[-1])
    k = 0
    while k < len(d) - 2 and d[k + 1] < s:
        k += 1
    span = d[k + 1] - d[k]
    t = 0.0 if span <= 0 else (s - d[k]) / span
    return (poly[k][0] + (poly[k + 1][0] - poly[k][0]) * t,
            poly[k][1] + (poly[k + 1][1] - poly[k][1]) * t)


def discretize(poly, n):
    """`n + 1` points at equal arc length along a polyline, ends exact."""
    d = arc_lengths(poly)
    out = [poly[0]] + [point_at(poly, d[-1] * k / float(n)) for k in range(1, n)] + [poly[-1]]
    return out


def slice_arc(poly, s0, s1):
    """The piece of a polyline between two arc positions, both ends interpolated exactly."""
    d = arc_lengths(poly)
    out = [point_at(poly, s0)]
    out += [p for p, dd in zip(poly, d) if s0 < dd < s1]
    out.append(point_at(poly, s1))
    return out


def nearest_arc(poly, p):
    """Arc position of the polyline point closest to `p`, to vertex resolution."""
    d = arc_lengths(poly)
    k = min(range(len(poly)), key=lambda i: (poly[i][0] - p[0]) ** 2 + (poly[i][1] - p[1]) ** 2)
    return d[k], k


def join(*polys):
    """Concatenate polylines end to end, dropping the repeated joint points."""
    out = list(polys[0])
    for q in polys[1:]:
        out += list(q)[1:] if out and _same(out[-1], q[0]) else list(q)
    return out


def _same(a, b, tol=1e-6):
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def coons(bottom, top, left, right, i, j, n, m):
    """The Coons point at grid index (i, j) of an n by m block from its four boundary point lists."""
    u, v = i / float(n), j / float(m)
    bx, by = bottom[i]
    tx, ty = top[i]
    lx, ly = left[j]
    rx, ry = right[j]
    c00, c10, c01, c11 = bottom[0], bottom[n], top[0], top[n]
    x = ((1 - v) * bx + v * tx + (1 - u) * lx + u * rx
         - ((1 - u) * (1 - v) * c00[0] + u * (1 - v) * c10[0] + (1 - u) * v * c01[0] + u * v * c11[0]))
    y = ((1 - v) * by + v * ty + (1 - u) * ly + u * ry
         - ((1 - u) * (1 - v) * c00[1] + u * (1 - v) * c10[1] + (1 - u) * v * c01[1] + u * v * c11[1]))
    return x, y


def tfi(mesh, bottom, top, left, right, zone=None, zones=None):
    """Fill one four-sided block with a mapped grid.

    Each side is given as node ids already in the mesh: `bottom` and `top` left to right, `left` and `right` bottom
    to top. Opposite sides must have equal counts, and the four corners must be the shared ids -- those two rules are
    what make neighbouring blocks conform, so they are checked rather than assumed. Returns the element ids."""
    n, m = len(bottom) - 1, len(left) - 1
    if len(top) != n + 1 or len(right) != m + 1:
        raise ValueError("opposite sides differ: bottom %d top %d, left %d right %d"
                         % (len(bottom), len(top), len(left), len(right)))
    for a, b, what in ((bottom[0], left[0], "bottom-left"), (bottom[-1], right[0], "bottom-right"),
                       (top[0], left[-1], "top-left"), (top[-1], right[-1], "top-right")):
        if a != b:
            raise ValueError("%s corner is two different nodes (%d, %d)" % (what, a, b))
    P = mesh.nodes
    bp, tp = [P[i] for i in bottom], [P[i] for i in top]
    lp, rp = [P[i] for i in left], [P[i] for i in right]
    grid = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        grid[i][0], grid[i][m] = bottom[i], top[i]
    for j in range(m + 1):
        grid[0][j], grid[n][j] = left[j], right[j]
    for i in range(1, n):
        for j in range(1, m):
            grid[i][j] = mesh.node(*coons(bp, tp, lp, rp, i, j, n, m))
    made = []
    for i in range(n):
        for j in range(m):
            q = mesh.quad(grid[i][j], grid[i + 1][j], grid[i + 1][j + 1], grid[i][j + 1])
            made.append(q)
            if zones is not None:
                zones[q] = zone
    return made


def row_ids(mesh, pts):
    """Node ids for a list of coordinates, welding onto whatever is already there."""
    return [mesh.node(*p) for p in pts]


def project_point(poly, p):
    """The closest point on a polyline to `p`, to segment resolution."""
    best = None
    for a, b in zip(poly, poly[1:]):
        ex, ey = b[0] - a[0], b[1] - a[1]
        L2 = ex * ex + ey * ey
        t = 0.0 if L2 <= 0 else max(0.0, min(1.0, ((p[0] - a[0]) * ex + (p[1] - a[1]) * ey) / L2))
        q = (a[0] + ex * t, a[1] + ey * t)
        d = (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2
        if best is None or d < best[0]:
            best = (d, q)
    return best[1]
