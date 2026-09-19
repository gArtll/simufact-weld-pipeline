# -*- coding: utf-8 -*-
"""A plate that is a planar profile extruded along a coordinate axis: mapped profile, graded sweep.

When the weld runs along the whole extrusion profile, the mesh has to be fine along the weld everywhere, so the
fine-to-coarse step is only needed across the plate width -- the extrusion direction. That step is made with layer
thicknesses that grow away from the weld, which keeps every element a hexahedron and every node shared: no
transition template is needed at all. The cost is aspect ratio in the far part of the width, which is bounded by
`far / along`.

What is assumed, and checked:

* the profile face is planar, lies in a coordinate plane, and splits at corners of 75 degrees or more into exactly
  four sides: two long ones (the weld side and the opposite one) and two short ends;
* a parallel face with the same outline closes the extrusion; its distance is the extrusion length.

The weld side is the long side passing closest to a given point, or the first long side."""
import math
from dataclasses import dataclass

from .block import arc_lengths, discretize, join, project_point, tfi
from .hardpoints import HardPointError, relocate
from .mesh import Mesh, check_all
from .solid import Solid, check_solid
from .step import face_edges, faces, g1_break, parse, sample_edge
from .web import SIDE_CORNER_DEG, AXES, plane_of, stats2d

EDGE_SAMPLES = 2000


@dataclass
class PlateParams:
    step_path: str
    face: int                          # profile face at one end of the extrusion
    weld_point: tuple = None           # (u, v) near the weld side of the profile; None = first long side
    along: float = 5.0                 # cell length along the weld
    layers: int = 3                    # through the plate thickness
    fine_intervals: tuple = ()         # ((a, b), ...) along the extrusion axis that must be `fine`
    fine: float = 5.0
    far: float = 15.0
    growth: float = 1.25               # largest ratio between neighbouring layer thicknesses
    keep_levels: tuple = ()            # extrusion coordinates that must be node levels (e.g. web faces)
    hard_points: tuple = ()            # (x, y, z) positions that must be nodes: constraint and load points
    end_profile_tol: float = 0.05      # the two end profiles must agree to this (mm)


def _d(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def profile_sides(ents, face_id):
    """The four sides of the profile, each as a list of per-edge polylines in loop order."""
    k, uv, off = plane_of(ents, face_id)
    es = face_edges(ents, face_id)
    polys, at = [], None
    for e in es:
        P = [(p[uv[0]], p[uv[1]]) for p in sample_edge(ents, e, EDGE_SAMPLES)]
        if at is not None and _d(P[0], at) > _d(P[-1], at):
            P = P[::-1]
        polys.append(P)
        at = P[-1]
    if _d(polys[0][0], polys[-1][-1]) > 1e-4:                  # first edge came out backwards
        polys[0] = polys[0][::-1]
    n = len(es)
    starts = [(i + 1) % n for i in range(n) if g1_break(ents, es[i], es[(i + 1) % n])[0] >= SIDE_CORNER_DEG]
    if len(starts) != 4:
        raise ValueError("profile face #%d has %d sides, need 4" % (face_id, len(starts)))
    sides = []
    for j, s in enumerate(starts):
        idx, i = [], s
        while True:
            idx.append(i)
            i = (i + 1) % n
            if i == starts[(j + 1) % 4]:
                break
        sides.append([polys[i] for i in idx])
    return k, uv, off, sides


def graded_levels(lo, hi, fine_intervals, fine, far, growth, keep=()):
    """Node levels from lo to hi: about `fine` inside the fine intervals, growing by at most `growth` per layer away
    from them up to `far`, and every value in `keep` exactly a level.

    Inside a fine interval the count per piece is rounded, not rounded up: `keep` levels cut the interval into short
    pieces, and rounding each up made layers a third finer than asked. The growth outside starts from the width of
    the fine layer it touches, so the first step is also bounded by `growth`."""
    in_fine = lambda v: any(x0 - 1e-9 <= v <= x1 + 1e-9 for x0, x1 in fine_intervals)
    end_tol = 1e-3                                   # a required level this close to an end face is that face
    snap = sorted(v for v in keep if not in_fine(v) and lo + end_tol < v < hi - end_tol)
    keep = [v for v in keep if in_fine(v) and lo + end_tol < v < hi - end_tol]
    marks = sorted({lo, hi} | {v for a, b in fine_intervals for v in (a, b)} | set(keep))
    out, fine_w = [lo], {}
    pieces = list(zip(marks, marks[1:]))
    kinds = [any(x0 - 1e-9 <= a and b <= x1 + 1e-9 for x0, x1 in fine_intervals) for a, b in pieces]
    for (a, b), inside in zip(pieces, kinds):
        if inside:
            n = max(1, int(round((b - a) / fine)))
            out += [a + (b - a) * k / n for k in range(1, n + 1)]
            fine_w[a] = fine_w[b] = (b - a) / n
    for (a, b), inside in zip(pieces, kinds):
        if not inside:
            out += _grow(a, b, fine_w.get(a), fine_w.get(b), far, growth)
    out = sorted(set(round(v, 9) for v in out))
    # a level required outside the fine zone moves the nearest graded level onto it rather than cutting a gap
    # into the grading, which would leave a thin layer next to a thick one
    for v in snap:
        k = min(range(1, len(out) - 1), key=lambda i: abs(out[i] - v)) if len(out) > 2 else None
        if k is None or abs(out[k] - v) > 0.5 * min(out[k + 1] - out[k], out[k] - out[k - 1]):
            out.append(round(v, 9))
        else:
            out[k] = round(v, 9)
        out = sorted(set(out))
    return out


def _grow(a, b, start_left, start_right, far, growth):
    """Levels strictly inside (a, b], widths growing from the given start widths towards the middle."""
    L = b - a
    if start_left is None and start_right is None:
        n = max(1, int(math.ceil(L / far - 1e-9)))
        return [a + L * k / n for k in range(1, n + 1)]

    def seq(s0, budget):
        sizes, s, tot = [], s0, 0.0
        while tot < budget - 1e-9:
            s = min(s * growth, far)
            sizes.append(s)
            tot += s
        # one layer too many overshoots: drop it if the rest, stretched, still keeps within growth and far
        if len(sizes) > 1:
            k = budget / (tot - sizes[-1])
            if sizes[0] * k <= s0 * growth + 1e-9 and max(sizes[:-1]) * k <= far + 1e-9:
                return [x * k for x in sizes[:-1]]
        return [x * budget / tot for x in sizes]
    if start_left is not None and start_right is not None:
        sizes = seq(start_left, L / 2.0) + seq(start_right, L / 2.0)[::-1]
    elif start_left is not None:
        sizes = seq(start_left, L)
    else:
        sizes = seq(start_right, L)[::-1]
    out, x = [], a
    for w in sizes:
        x += w
        out.append(x)
    out[-1] = b
    return out


def build(p):
    """(solid, face mesh, report)."""
    ents = parse(p.step_path)
    k, uv, off, sides = profile_sides(ents, p.face)
    lens = [sum(arc_lengths(e)[-1] for e in s) for s in sides]
    longs = sorted(range(4), key=lambda i: -lens[i])[:2]
    if p.weld_point is not None:
        longs.sort(key=lambda i: min(_d(q, p.weld_point) for e in sides[i] for q in e))
    weld, opp = longs
    if (weld + 2) % 4 != opp:
        raise ValueError("the two long sides of the profile are not opposite each other")
    # weld side and opposite side, both running the same way, edge by edge
    top = sides[weld]
    bot = [e[::-1] for e in sides[opp][::-1]]
    if len(top) != len(bot):
        raise ValueError("weld side has %d edges, opposite side %d: not a simple extrusion profile"
                         % (len(top), len(bot)))
    m = Mesh()
    top_pts, bot_pts = [], []
    for et, eb in zip(top, bot):
        n = max(1, int(round(arc_lengths(et)[-1] / p.along)))
        st, sb = discretize(et, n), discretize(eb, n)
        top_pts += st if not top_pts else st[1:]
        bot_pts += sb if not bot_pts else sb[1:]
    hp2 = [((q[uv[0]], q[uv[1]]), q) for q in p.hard_points]
    moves, claimed = [], {}
    top_curve, bot_curve = join(*top), join(*bot)
    rest = []
    for t2, q3 in hp2:
        for name, curve, pts in (("weld side", top_curve, top_pts), ("opposite side", bot_curve, bot_pts)):
            on = project_point(curve, t2)
            if _d(on, t2) <= 1e-3:
                s_t = _arc_of(curve, on)
                sts = [_arc_of(curve, x) for x in pts]
                kk = min(range(len(pts)), key=lambda i: abs(sts[i] - s_t))
                if 0 < kk < len(pts) - 1 and not (sts[kk - 1] < s_t < sts[kk + 1]):
                    raise HardPointError("hard point %r cannot take station %d of the %s" % (q3, kk, name))
                prev = claimed.get((name, kk))
                if prev is not None and _d(prev, on) > 1e-6:
                    raise HardPointError(
                        "hard points %r and another both need station %d of the %s, %.4f mm apart: every station of an "
                        "extruded profile is shared by all extrusion levels, so two points on one station must have the "
                        "same profile position" % (q3, kk, name, _d(prev, on)))
                claimed[(name, kk)] = on
                moves.append({"point": q3, "on": name, "station": kk, "move_mm": round(abs(sts[kk] - s_t), 4)})
                pts[kk] = on
                break
        else:
            rest.append((t2, q3))
    N = len(top_pts) - 1
    T = [m.node(*q) for q in top_pts]
    B = [m.node(*q) for q in bot_pts]
    L = [m.node(*q) for q in discretize([bot_pts[0], top_pts[0]], p.layers)]
    R = [m.node(*q) for q in discretize([bot_pts[-1], top_pts[-1]], p.layers)]
    zones = {}
    tfi(m, B, T, L, R, "plate", zones)
    if rest:
        ends2 = [[bot_pts[0], top_pts[0]], [bot_pts[-1], top_pts[-1]]]
        for r in relocate(m, [t for t, _ in rest], boundary_curves=[top_curve, bot_curve] + ends2):
            moves.append({"point": [q for t, q in rest if t == r["target"]][0], "on": r["how"],
                          "move_mm": r["move_mm"]})
    c2 = check_all(m)
    if c2["inverted_elements"]:
        m.quads = {q: v[::-1] for q, v in m.quads.items()}
        c2 = check_all(m)

    # extrusion
    other = [f["face"] for f in faces(ents) if f["face"] != p.face and f["surface_kind"] == "PLANE"]
    ends = []
    for f in other:
        try:
            k2, _, off2 = plane_of(ents, f)
        except ValueError:
            continue
        if k2 == k and len(face_edges(ents, f)) == len(face_edges(ents, p.face)):
            ends.append((off2, f))
    if not ends:
        raise ValueError("no matching profile face closes the extrusion")
    far_end, other_face = min(ends, key=lambda v: -abs(v[0] - off))
    # the two end faces must be the same outline: a plate whose width changes along its length is not an extrusion,
    # and meshing it as one silently makes a different part (seen on a real part: ends tens of millimetres apart)
    k2, uv2, _, sides2 = profile_sides(ents, other_face)
    gap = max(_outline_gap(sides, sides2), _outline_gap(sides2, sides))
    if gap > p.end_profile_tol:
        raise ValueError("the end faces #%d and #%d differ by up to %.3f mm: the plate is not a straight extrusion "
                         "(end_profile_tol %.3f mm); mesh it with the mapped_plate backend (mapped_plate.py)"
                         % (p.face, other_face, gap, p.end_profile_tol))
    lo, hi = sorted((off, far_end))
    levels = graded_levels(lo, hi, p.fine_intervals, p.fine, p.far, p.growth,
                           tuple(p.keep_levels) + tuple(q[k] for q in p.hard_points))
    s = Solid()
    grid = {}
    ids2d = sorted(m.nodes)
    for j, x in enumerate(levels):
        for i in ids2d:
            q = [0.0, 0.0, 0.0]
            q[uv[0]], q[uv[1]], q[k] = m.nodes[i][0], m.nodes[i][1], x
            grid[(i, j)] = s.add_node(*q)
    for j in range(len(levels) - 1):
        for quad in m.quads.values():
            s.elements.append(("hex8", [grid[(i, j)] for i in quad] + [grid[(i, j + 1)] for i in quad]))
    from .solid import volume
    if s.elements and volume(s.xyz(s.elements[0][1])) < 0:
        s.elements = [(kd, v[4:] + v[:4]) for kd, v in s.elements]
    c3 = check_solid(s, len(levels) - 1, len(ids2d))
    widths = [levels[i + 1] - levels[i] for i in range(len(levels) - 1)]
    if min(widths) < 0.2 * p.fine:
        raise ValueError("extrusion layer of %.4f mm: two required levels are too close together" % min(widths))
    rep = {"profile": {"face": p.face, "normal_axis": AXES[k], "weld_side_mm": round(lens[weld], 2),
                       "opposite_side_mm": round(lens[opp], 2), "cells_along": N, "layers": p.layers,
                       **stats2d(m), "topology_ok": c2["ok"]},
           "extrusion": {"from": lo, "to": hi, "levels": [round(v, 4) for v in levels],
                         "widths": [round(w, 3) for w in widths], "max_growth": round(max(
                             max(widths[i + 1] / widths[i], widths[i] / widths[i + 1]) for i in range(len(widths) - 1)), 3)
                             if len(widths) > 1 else 1.0},
           "solid": {kk: (len(v) if isinstance(v, list) else v) for kk, v in c3.items()},
           "hard_points": moves}
    # every hard point is now a node of the solid, exactly
    if p.hard_points:
        import numpy as _np
        from scipy.spatial import cKDTree as _T
        tree = _T(_np.array(list(s.nodes.values())))
        worst = max(float(tree.query(_np.array(q))[0]) for q in p.hard_points)
        rep["hard_point_max_distance_mm"] = round(worst, 9)
        # a point given slightly off the CAD surface is placed on the surface, so allow what the on-curve test allows
        if worst > 1e-3:
            raise HardPointError("a hard point ended %.6f mm from the nearest node" % worst)
    return s, m, rep


def _outline_gap(sides_a, sides_b, per_edge=60):
    """Largest distance from points spread along outline a to the polylines of outline b (point to segment)."""
    import numpy as np
    segs = []
    for sd in sides_b:
        for e in sd:
            P = np.array(e)
            segs.append(np.stack([P[:-1], P[1:]], axis=1))
    S = np.concatenate(segs)                                       # (n, 2, 2)
    a0, d = S[:, 0], S[:, 1] - S[:, 0]
    L2 = np.maximum((d * d).sum(1), 1e-30)
    worst = 0.0
    for sd in sides_a:
        for e in sd:
            idx = np.linspace(0, len(e) - 1, min(per_edge, len(e))).astype(int)
            for q in np.array(e)[idx]:
                t = np.clip(((q - a0) * d).sum(1) / L2, 0.0, 1.0)
                worst = max(worst, float(np.min(np.linalg.norm(a0 + d * t[:, None] - q, axis=1))))
    return worst


def _arc_of(curve, q):
    """Arc position of a point lying on a polyline."""
    best, acc = None, 0.0
    for a, b in zip(curve, curve[1:]):
        L = _d(a, b)
        if L > 0:
            t = max(0.0, min(1.0, ((q[0] - a[0]) * (b[0] - a[0]) + (q[1] - a[1]) * (b[1] - a[1])) / (L * L)))
            d = _d((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), q)
            if best is None or d < best[0]:
                best = (d, acc + L * t)
        acc += L
    return best[1]
