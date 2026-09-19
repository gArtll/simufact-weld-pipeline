# -*- coding: utf-8 -*-
"""A plate whose section changes along the sweep axis: end profiles of one topology, mapped station by station.

`plate.py` sweeps one profile and refuses a plate whose two end profiles differ (a plate longer at one edge than at
the other is not an extrusion). This backend meshes that plate instead of refusing it, as long as
both end faces are four-sided profiles with the same edges per side:

1. both end profiles are cut into their four sides and matched side to side (loop sense and start found from the
   geometry, not assumed);
2. every edge gets one station count, the larger of what the two ends need, so station i of one end corresponds to
   station i of the other at the same fraction of arc length;
3. the node levels along the sweep axis are graded exactly as in `plate.py` (fine around the weld, `keep_levels`
   exact);
4. on each level the profile boundary is the blend of the two end profiles, then every boundary node is projected,
   within that level's plane, onto the CAD face it belongs to -- so a lateral face that is not ruled (a B-spline
   plate edge, a curved top) is followed, not approximated by a straight line between the ends;
5. the interior of each level is the transfinite map of its projected boundary; all levels share one topology, and
   neighbouring levels are joined into hexahedra.

What is not handled is refused with the reason: end faces with different side or edge structure, a side whose
edges cannot be assigned to a CAD face, a projection that does not converge onto its face."""
import math

import numpy as np

from .block import arc_lengths, discretize, tfi
from .hardpoints import HardPointError
from .mesh import Mesh, check_all
from .plate import PlateParams, graded_levels, profile_sides, _d
from .solid import Solid, check_solid, volume
from .step import face_edges, faces, parse
from .web import AXES, plane_of, stats2d

PROJECT_TOL = 1e-3            # a projected boundary node must end this close to its CAD face (mm)
PROJECT_ITER = 30


class MappedPlateError(ValueError):
    pass


# ---------------------------------------------------------------- CAD faces
def _lateral_faces(step_path, ents, face_a, face_b):
    """The faces of the plate body other than its two end profiles, as capability.geometry Face objects."""
    from capability.geometry import Face, bodies
    _, bs = bodies(step_path)
    body = next((b for b in bs if face_a in {f.id for f in b.faces}), None)
    if body is not None:
        return [f for f in body.faces if f.id not in (face_a, face_b)]
    # a file of loose faces (no solid): every non-parallel face whose extent reaches the profile
    k = plane_of(ents, face_a)[0]
    out = []
    for f in faces(ents):
        if f["face"] in (face_a, face_b):
            continue
        try:
            if f["surface_kind"] == "PLANE" and plane_of(ents, f["face"])[0] == k:
                continue
        except ValueError:
            pass
        out.append(Face(ents, f["face"]))
    return out


def closest_on_surface(surface, q):
    """The point of an (untrimmed) surface closest to q: exact for planes and cylinders, on the sampling
    triangulation for B-spline surfaces (its chord error is far below PROJECT_TOL, see capability/geometry.py)."""
    q = np.asarray(q, float)
    if surface.kind == "PLANE":
        return q - surface.normal * float((q - surface.origin) @ surface.normal)
    if surface.kind == "CYLINDRICAL_SURFACE":
        w = q - surface.origin
        along = surface.axis * float(w @ surface.axis)
        radial = w - along
        n = float(np.linalg.norm(radial))
        if n == 0.0:
            raise MappedPlateError("point on the cylinder axis")
        return surface.origin + along + radial * (surface.radius / n)
    if surface.kind == "B_SPLINE_SURFACE_WITH_KNOTS":
        _, k = surface.tree.query(q)
        j, i = divmod(int(k), surface.nu)
        best, bp = None, None
        g = surface.grid
        for jj in (j - 1, j):
            for ii in (i - 1, i):
                if 0 <= jj < surface.nv - 1 and 0 <= ii < surface.nu - 1:
                    p0, p1, p2, p3 = g[jj, ii], g[jj, ii + 1], g[jj + 1, ii + 1], g[jj + 1, ii]
                    for tri in ((p0, p1, p2), (p0, p2, p3)):
                        c = _closest_on_triangle(q, *tri)
                        d = float(np.linalg.norm(c - q))
                        if best is None or d < best:
                            best, bp = d, c
        return bp
    raise MappedPlateError("cannot project onto a %s face" % surface.kind)


def _closest_on_triangle(p, a, b, c):
    """Ericson, Real-Time Collision Detection 5.1.5."""
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = ab @ ap, ac @ ap
    if d1 <= 0 and d2 <= 0:
        return a
    bp = p - b
    d3, d4 = ab @ bp, ac @ bp
    if d3 >= 0 and d4 <= d3:
        return b
    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        return a + ab * (d1 / (d1 - d3))
    cp = p - c
    d5, d6 = ab @ cp, ac @ cp
    if d6 >= 0 and d5 <= d6:
        return c
    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        return a + ac * (d2 / (d2 - d6))
    va = d3 * d6 - d5 * d4
    if va <= 0 and d4 - d3 >= 0 and d5 - d6 >= 0:
        return b + (c - b) * ((d4 - d3) / ((d4 - d3) + (d5 - d6)))
    den = 1.0 / (va + vb + vc)
    return a + ab * (vb * den) + ac * (vc * den)


def project_in_level(q, surfaces, k, level):
    """q moved onto the intersection of its face(s) with the level plane (coordinate k = level): alternate
    projections onto each surface and back onto the plane until they agree. Two surfaces = a node on an edge."""
    q = np.asarray(q, float).copy()
    q[k] = level
    for _ in range(PROJECT_ITER):
        prev = q.copy()
        for s in surfaces:
            q = closest_on_surface(s, q)
            q[k] = level
        if float(np.linalg.norm(q - prev)) < 1e-9:
            break
    dev = max(float(s.distance(q[None, :])[0]) for s in surfaces)
    return q, dev


# ---------------------------------------------------------------- profiles
def _mid(side):
    pts = [p for e in side for p in e]
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def match_sides(sa, sb):
    """sb re-ordered (loop sense and start) so that sb[i] corresponds to sa[i], every edge running the same way."""
    rev = [[e[::-1] for e in s[::-1]] for s in sb[::-1]]
    best = None
    for cand in (sb, rev):
        for r in range(4):
            c = cand[r:] + cand[:r]
            score = sum(_d(_mid(a), _mid(b)) for a, b in zip(sa, c))
            if best is None or score < best[0]:
                best = (score, c)
    sb = best[1]
    for i, (a, b) in enumerate(zip(sa, sb)):
        if len(a) != len(b):
            raise MappedPlateError("side %d has %d edges at one end and %d at the other: the end profiles are not one "
                                   "topology" % (i, len(a), len(b)))
    return sb


def _assign(edge3d, lateral):
    """The lateral face an edge (3D points) lies on."""
    best = min(lateral, key=lambda f: float(np.median(f.surface.distance(edge3d))))
    d = float(np.median(best.surface.distance(edge3d)))
    if d > 0.05:
        raise MappedPlateError("no CAD face under a profile edge (nearest %.3f mm away)" % d)
    return best


def build(p):
    """(solid, face mesh of the first end, report). Same parameters as plate.build."""
    ents = parse(p.step_path)
    k, uv, off_a, sides_a = profile_sides(ents, p.face)
    # the other end: parallel, same edge count, farthest away
    cands = []
    for f in faces(ents):
        if f["face"] == p.face or f["surface_kind"] != "PLANE":
            continue
        try:
            k2, _, off2 = plane_of(ents, f["face"])
        except ValueError:
            continue
        if k2 == k and len(face_edges(ents, f["face"])) == len(face_edges(ents, p.face)):
            cands.append((abs(off2 - off_a), off2, f["face"]))
    if not cands:
        raise MappedPlateError("no end face parallel to #%d with the same number of edges" % p.face)
    _, off_b, face_b = max(cands)
    _, uv_b, _, sides_b = profile_sides(ents, face_b)
    if tuple(uv_b) != tuple(uv):
        raise MappedPlateError("end faces use different in-plane axes")
    sides_b = match_sides(sides_a, sides_b)

    lens = [sum(arc_lengths(e)[-1] for e in s) for s in sides_a]
    longs = sorted(range(4), key=lambda i: -lens[i])[:2]
    if p.weld_point is not None:
        longs.sort(key=lambda i: min(_d(q, p.weld_point) for e in sides_a[i] for q in e))
    weld, opp = longs
    if (weld + 2) % 4 != opp:
        raise MappedPlateError("the two long sides of the profile are not opposite each other")
    left_i, right_i = (opp + 1) % 4, (weld + 1) % 4

    def to3(q2, level):
        q = [0.0, 0.0, 0.0]
        q[uv[0]], q[uv[1]], q[k] = q2[0], q2[1], level
        return np.array(q)

    lateral = _lateral_faces(p.step_path, ents, p.face, face_b)
    if not lateral:
        raise MappedPlateError("no lateral CAD faces found for the plate")

    # stations per edge -- one count per edge index, shared by both ends and by the weld and opposite sides, as in
    # the sweep -- and the CAD face(s) of every boundary station
    flip_side = lambda sd: [e[::-1] for e in sd[::-1]]
    sides4 = (sides_a[weld], sides_b[weld], flip_side(sides_a[opp]), flip_side(sides_b[opp]))
    if len({len(sd) for sd in sides4}) != 1:
        raise MappedPlateError("weld side and opposite side have different edge counts %s: not a simple profile"
                               % [len(sd) for sd in sides4])
    counts = [max(1, int(round(max(arc_lengths(sd[i])[-1] for sd in sides4) / p.along)))
              for i in range(len(sides4[0]))]

    def stations(side_a, side_b, flip):
        ea = flip_side(side_a) if flip else side_a
        eb = flip_side(side_b) if flip else side_b
        pa, pb, owner = [], [], []
        for n, e_a, e_b in zip(counts, ea, eb):
            face = _assign(np.array([to3(q, off_a) for q in e_a[:: max(1, len(e_a) // 40)]]), lateral)
            sa, sb = discretize(e_a, n), discretize(e_b, n)
            if pa:
                if face is not owner[-1][0]:
                    owner[-1] = owner[-1] + [face]       # a junction between two faces lies on both
                sa, sb = sa[1:], sb[1:]
            pa += sa
            pb += sb
            owner += [[face] for _ in sa]
        return pa, pb, owner

    top_a, top_b, top_own = stations(sides_a[weld], sides_b[weld], False)
    bot_a, bot_b, bot_own = stations(sides_a[opp], sides_b[opp], True)
    if len(top_a) != len(bot_a):
        raise MappedPlateError("weld side has %d stations, opposite side %d: edge structure differs"
                               % (len(top_a), len(bot_a)))
    # ends: straight in the profile, `layers` cells; their faces
    lf = _assign(np.array([to3(q, off_a) for e in sides_a[left_i] for q in e]), lateral)
    rf = _assign(np.array([to3(q, off_a) for e in sides_a[right_i] for q in e]), lateral)
    top_own[0], top_own[-1] = top_own[0] + [lf], top_own[-1] + [rf]
    bot_own[0], bot_own[-1] = bot_own[0] + [lf], bot_own[-1] + [rf]

    lo, hi = sorted((off_a, off_b))
    levels = graded_levels(lo, hi, p.fine_intervals, p.fine, p.far, p.growth,
                           tuple(p.keep_levels) + tuple(q[k] for q in p.hard_points))
    worst = [0.0, 0.0]                  # distance of a projected node to its face, move off the linear blend

    def level_mesh(x):
        s = (x - off_a) / (off_b - off_a)
        bl = lambda A, B: [((1 - s) * a[0] + s * b[0], (1 - s) * a[1] + s * b[1]) for a, b in zip(A, B)]
        top, bot = bl(top_a, top_b), bl(bot_a, bot_b)

        def proj(pts, own):
            out = []
            for q2, fs in zip(pts, own):
                q3, dev = project_in_level(to3(q2, x), [f.surface for f in fs], k, x)
                if dev > PROJECT_TOL:
                    raise MappedPlateError("a boundary node did not converge onto its CAD face at level %.3f "
                                           "(%.4f mm off)" % (x, dev))
                worst[0] = max(worst[0], dev)
                worst[1] = max(worst[1], _d(q2, (q3[uv[0]], q3[uv[1]])))
                out.append((float(q3[uv[0]]), float(q3[uv[1]])))
            return out
        top, bot = proj(top, top_own), proj(bot, bot_own)
        left = [(bot[0][0] + (top[0][0] - bot[0][0]) * j / p.layers, bot[0][1] + (top[0][1] - bot[0][1]) * j / p.layers)
                for j in range(p.layers + 1)]
        right = [(bot[-1][0] + (top[-1][0] - bot[-1][0]) * j / p.layers,
                  bot[-1][1] + (top[-1][1] - bot[-1][1]) * j / p.layers) for j in range(p.layers + 1)]
        # the end sides follow their faces too (a slanted or curved plate end)
        left = [left[0]] + proj(left[1:-1], [[lf]] * (p.layers - 1)) + [left[-1]]
        right = [right[0]] + proj(right[1:-1], [[rf]] * (p.layers - 1)) + [right[-1]]
        m = Mesh()
        T = [m.node(*q) for q in top]
        B = [m.node(*q) for q in bot]
        L = [m.node(*q) for q in left]
        R = [m.node(*q) for q in right]
        tfi(m, B, T, L, R)
        return m

    meshes = [level_mesh(x) for x in levels]
    m0 = meshes[0]
    ids2d = sorted(m0.nodes)
    for m in meshes[1:]:
        if sorted(m.nodes) != ids2d or m.quads.keys() != m0.quads.keys():
            raise MappedPlateError("the level meshes do not share one topology")
    c2 = check_all(m0)
    if c2["inverted_elements"]:                        # wound the other way in (u, v): turn every quad round
        for m in meshes:
            m.quads = {q: v[::-1] for q, v in m.quads.items()}
        c2 = check_all(m0)

    s3 = Solid()
    grid = {}
    for j, (x, m) in enumerate(zip(levels, meshes)):
        for i in ids2d:
            q = [0.0, 0.0, 0.0]
            q[uv[0]], q[uv[1]], q[k] = m.nodes[i][0], m.nodes[i][1], x
            grid[(i, j)] = s3.add_node(*q)
    quads = list(m0.quads.values())
    for j in range(len(levels) - 1):
        for quad in quads:
            s3.elements.append(("hex8", [grid[(i, j)] for i in quad] + [grid[(i, j + 1)] for i in quad]))
    if s3.elements and volume(s3.xyz(s3.elements[0][1])) < 0:
        s3.elements = [(kd, v[4:] + v[:4]) for kd, v in s3.elements]

    # hard points: on their level, the nearest node is moved onto them (never more than half a cell)
    moves = []
    if p.hard_points:
        for q in p.hard_points:
            j = min(range(len(levels)), key=lambda i: abs(levels[i] - q[k]))
            if abs(levels[j] - q[k]) > 1e-3:                # graded_levels takes a point this close to an end
                raise HardPointError("hard point %r is not on a node level" % (tuple(q),))   # face as that face
            cand = [grid[(i, j)] for i in ids2d]
            nid = min(cand, key=lambda n: math.dist(s3.nodes[n], q))
            d = math.dist(s3.nodes[nid], q)
            if d > 0.5 * p.along:
                raise HardPointError("hard point %r is %.3f mm from the nearest node of its level (more than half a "
                                     "cell): it is not on this plate" % (tuple(q), d))
            s3.nodes[nid] = tuple(float(v) for v in q)
            moves.append({"point": list(q), "node": nid, "move_mm": round(d, 4)})
    c3 = check_solid(s3, len(levels) - 1, len(ids2d))
    widths = [levels[i + 1] - levels[i] for i in range(len(levels) - 1)]
    if min(widths) < 0.2 * p.fine:
        raise MappedPlateError("sweep layer of %.4f mm: two required levels are too close together" % min(widths))
    gap = max(_d(a, b) for a, b in zip(top_a + bot_a, top_b + bot_b))
    rep = {"backend": "mapped_plate",
           "profile": {"face": p.face, "other_end_face": face_b, "normal_axis": AXES[k],
                       "weld_side_mm": [round(lens[weld], 2), round(sum(arc_lengths(e)[-1] for e in sides_b[weld]), 2)],
                       "cells_along": len(top_a) - 1, "layers": p.layers, **stats2d(m0), "topology_ok": c2["ok"],
                       "max_station_shift_between_ends_mm": round(gap, 3)},
           "projection": {"lateral_faces": [f.id for f in lateral], "max_distance_to_face_mm": round(worst[0], 6),
                          "max_move_off_linear_blend_mm": round(worst[1], 4)},
           "extrusion": {"from": lo, "to": hi, "levels": [round(v, 4) for v in levels],
                         "widths": [round(w, 3) for w in widths]},
           "solid": {kk: (len(v) if isinstance(v, list) else v) for kk, v in c3.items()},
           "hard_points": moves}
    return s3, m0, rep
