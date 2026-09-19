# -*- coding: utf-8 -*-
"""A whole plate face meshed from its CAD outline: weld-root band, far field, notches, then a solid.

What this assumes about the part, and nothing more:

* the face is planar and lies in a coordinate plane (its normal is one of the axes), and a parallel face on the
  other side of the plate gives the thickness;
* one run of the outline is the weld root. Pass its geometry ids, or let the longest side be taken;
* the two sides next to the root are the plate ends; everything else is the far boundary, which may be cut by
  notches -- short runs of sides that dip into the material and come back out close to where they went in.

"Side" means a run of edges between corners of at least `SIDE_CORNER_DEG`. A tangent break inside the weld root
(tens of degrees on real parts) is smaller than that and stays inside the root, where the corner machinery of corner.py deals
with it; a notch corner (90 degrees) or a plate corner is larger and starts a new side.

Every size is a parameter. Nothing here knows about any particular part: the part-specific numbers live in the caller."""
import math
from dataclasses import dataclass, field

from .block import arc_lengths, discretize, join, point_at, project_point, slice_arc, tfi
from .corner import band, ladder, self_intersections, smooth
from .hardpoints import HardPointError, relocate
from .mesh import Mesh, check_all
from .quality import metrics
from .solid import check_solid, sweep
from .step import direction, face_edges, faces, g1_break, parse, point, sample_edge

AXES = "xyz"
SIDE_CORNER_DEG = 75.0


@dataclass
class WebParams:
    step_path: str
    face: int                                  # the ADVANCED_FACE to mesh
    root: tuple = ()                           # geometry ids of the weld root; () = the longest side
    fine: float = 5.0                          # weld-side cell size
    coarse: float = 13.0                       # size the transition row coarsens to
    far: float = 14.5                          # far-field cell height target
    buffer_rows: int = 2                       # fine rows on the root before the transition
    coarse_rows: int = 2                       # coarse rows after it
    layers: int = 2                            # through the thickness
    thickness: float = None                    # None = from the parallel face
    notch_chord_ratio: float = 0.35            # a run is a notch if mouth chord < ratio * run length
    smooth_passes: int = 25
    column_split: float = 0.5                  # where a side-on T3 puts its interior nodes, fine line -> coarse
    hard_points: tuple = ()                    # (x, y, z) positions that must be nodes: constraint and load points
    swap_uv: bool = False                      # in-plane axis order (plane_of); build() sets it from the root
    extra: dict = field(default_factory=dict)


# ---------------------------------------------------------------- geometry
def plane_of(ents, face_id, swap=False):
    """(normal axis index, in-plane (u, v) axis indices, offset) of a planar face lying in a coordinate plane.

    The default (u, v) order is the one the existing decks use (x-normal -> (z, y)). The web mesher needs the weld
    root along u; `swap=True` gives the other order, for a web whose root runs along the default v (web.build decides
    this from the root, so a web meshes the same in any orientation of the part)."""
    f = [f for f in faces(ents) if f["face"] == face_id][0]
    if f["surface_kind"] != "PLANE":
        raise ValueError("face #%d is %s; only planar faces are handled" % (face_id, f["surface_kind"]))
    ax = ents[ents[f["surface"]][1][1]][1]
    n = direction(ents, ax[2])
    k = max(range(3), key=lambda i: abs(n[i]))
    if abs(abs(n[k]) - 1.0) > 1e-6:
        raise ValueError("face #%d is not in a coordinate plane (normal %r)" % (face_id, n))
    o = point(ents, ax[1])
    u, v = [i for i in range(3) if i != k][::-1]            # x-normal -> (z, y), as the existing decks use
    if swap:
        u, v = v, u
    return k, (u, v), o[k]


def thickness_of(ents, face_id):
    """Distance to the nearest other face parallel to this one that overlaps it."""
    k, _, off = plane_of(ents, face_id)
    best = None
    for f in faces(ents):
        if f["face"] == face_id or f["surface_kind"] != "PLANE":
            continue
        try:
            k2, _, off2 = plane_of(ents, f["face"])
        except ValueError:
            continue
        if k2 == k and abs(off2 - off) > 1e-6 and (best is None or abs(off2 - off) < abs(best)):
            best = off2 - off
    if best is None:
        raise ValueError("no parallel face to take the thickness from")
    return best


def _poly(ents, e, uv, n=400):
    return [(p[uv[0]], p[uv[1]]) for p in sample_edge(ents, e, n)]


def _d(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _unit(v):
    L = math.hypot(v[0], v[1])
    return (v[0] / L, v[1] / L)


def outline(ents, face_id, root_ids=(), swap=False):
    """The face loop cut into sides and named: root, left end, right end, far boundary tops and notches.

    Returned polylines run left to right, where left is the start of the root and the face interior lies to the
    left of the root's direction of travel."""
    k, uv, off = plane_of(ents, face_id, swap)
    es = face_edges(ents, face_id)
    # orient the loop
    polys, at = [], None
    for e in es:
        P = _poly(ents, e, uv)
        if at is not None and _d(P[0], at) > _d(P[-1], at):
            P = P[::-1]
        polys.append(P)
        at = P[-1]
    if _d(polys[0][0], polys[-1][-1]) > 1e-4:
        P = polys[0][::-1]
        if _d(P[-1], polys[1][0]) < _d(polys[0][-1], polys[1][0]):
            polys[0] = P
    pts = [p for P in polys for p in P]
    area = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
               for i in range(len(pts))) / 2.0
    if area < 0:                                            # make it counter-clockwise
        es, polys = es[::-1], [P[::-1] for P in polys[::-1]]
    n = len(es)
    breaks = []
    for i in range(n):
        deg, _ = g1_break(ents, es[i], es[(i + 1) % n])
        breaks.append(deg)
    # sides: runs between corners >= SIDE_CORNER_DEG
    starts = [(i + 1) % n for i in range(n) if breaks[i] >= SIDE_CORNER_DEG]
    if not starts:
        raise ValueError("the outline has no corners; it cannot be split into root, ends and far boundary")
    sides = []
    for j, s in enumerate(starts):
        e_ = starts[(j + 1) % len(starts)]
        idx, i = [], s
        while True:
            idx.append(i)
            i = (i + 1) % n
            if i == e_:
                break
        sides.append(idx)
    geom = lambda i: es[i]["geometry"]
    length = lambda side: sum(arc_lengths(polys[i])[-1] for i in side)
    if root_ids:
        r = [j for j, sd in enumerate(sides) if set(geom(i) for i in sd) == set(root_ids)]
        if not r:
            raise ValueError("no side of the outline is exactly the root edges %r" % (root_ids,))
        r = r[0]
    else:
        r = max(range(len(sides)), key=lambda j: length(sides[j]))
    S = len(sides)
    rot = sides[r:] + sides[:r]                             # root, right end, far sides ..., left end
    mk = lambda side: join(*[polys[i] for i in side])
    root = mk(rot[0])
    right_end = mk(rot[1])
    left_end = mk(rot[-1])
    far = [mk(sd) for sd in rot[2:-1]]                      # from the right end back to the left end
    far_ids = [[geom(i) for i in sd] for sd in rot[2:-1]]
    far, far_ids = [P[::-1] for P in far[::-1]], far_ids[::-1]   # now left to right
    tops, notches, j = [], [], 0
    cur_top = []
    while j < len(far):
        found = None
        for w in (3, 4, 5):                                 # smallest notch first
            if j + w <= len(far):
                run = far[j:j + w]
                L = sum(arc_lengths(p)[-1] for p in run)
                if _d(run[0][0], run[-1][-1]) < 0.35 * L and _dips(run, root):
                    found = w
                    break
        if found and cur_top:
            tops.append(join(*cur_top))
            run = far[j:j + found]
            notches.append({"left_wall": run[0], "bottom": join(*run[1:-1]), "right_wall": run[-1],
                            "mouth_left": run[0][0], "mouth_right": run[-1][-1],
                            "bottom_left": run[0][-1], "bottom_right": run[-1][0],
                            "ids": far_ids[j:j + found]})
            cur_top = []
            j += found
        else:
            cur_top.append(far[j])
            j += 1
    tops.append(join(*cur_top))
    for s in notches:
        a = _unit((s["left_wall"][0][0] - s["left_wall"][-1][0], s["left_wall"][0][1] - s["left_wall"][-1][1]))
        b = _unit((s["right_wall"][-1][0] - s["right_wall"][0][0], s["right_wall"][-1][1] - s["right_wall"][0][1]))
        s["axis_down"] = _unit((-(a[0] + b[0]), -(a[1] + b[1])))
    root_breaks = []
    for a, b in zip(rot[0], rot[0][1:]):
        deg, v = g1_break(ents, es[a], es[b])
        root_breaks.append({"between": [geom(a), geom(b)], "turn_deg": round(deg, 4),
                            "at": (round(v[uv[0]], 3), round(v[uv[1]], 3))})
    return {"normal_axis": AXES[k], "uv_axes": (AXES[uv[0]], AXES[uv[1]]), "plane_offset": off,
            "root": root, "root_ids": [geom(i) for i in rot[0]], "left_end": left_end, "right_end": right_end,
            "tops": tops, "notches": notches, "root_breaks": root_breaks,
            "side_count": S, "edge_count": n, "loop_area": abs(area)}


def _dips(run, root):
    """A notch goes towards the root: its bottom is closer to the root than its mouth."""
    mouth = ((run[0][0][0] + run[-1][-1][0]) / 2.0, (run[0][0][1] + run[-1][-1][1]) / 2.0)
    bot = run[len(run) // 2][len(run[len(run) // 2]) // 2]
    return _d(project_point(root, bot), bot) < _d(project_point(root, mouth), mouth)


def root_nodes(ents, face_id, root_ids, uv, step):
    """Root nodes about `step` apart, an exact node on every entity junction."""
    es = {e["geometry"]: e for e in face_edges(ents, face_id)}
    pts = []
    for g in root_ids:
        P = _poly(ents, es[g], uv, 2000)
        if pts and _d(P[0], pts[-1]) > _d(P[-1], pts[-1]):
            P = P[::-1]
        L = arc_lengths(P)[-1]
        seg = discretize(P, max(1, int(round(L / step))))
        pts += seg if not pts else seg[1:]
    return pts


def offset_clip(T, d, left_end, right_end):
    """The far boundary pushed `d` into the material and cut off at the two plate ends."""
    from .corner import offset_row
    C = offset_row(T, d, side=-1.0)

    def cut(C, end, keep_after):
        a, b = end[0], end[-1]
        for i in (range(len(C) - 1) if keep_after else range(len(C) - 2, -1, -1)):
            p, q = C[i], C[i + 1]
            x = _seg_x(p, q, a, b)
            if x is not None:
                return ([x] + C[i + 1:]) if keep_after else (C[:i + 1] + [x])
        return C
    return cut(cut(C, left_end, True), right_end, False)


def _seg_x(p, q, a, b):
    """Intersection of segment pq with the infinite line ab."""
    ex, ey = b[0] - a[0], b[1] - a[1]
    s1 = ex * (p[1] - a[1]) - ey * (p[0] - a[0])
    s2 = ex * (q[1] - a[1]) - ey * (q[0] - a[0])
    if s1 == s2 or (s1 > 0) == (s2 > 0):
        return None
    t = s1 / (s1 - s2)
    return (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)


def ray_foot(C, p, d):
    """Arc position on C where the ray from p along d first meets it."""
    best, dd = None, arc_lengths(C)
    for k in range(len(C) - 1):
        ex, ey = C[k + 1][0] - C[k][0], C[k + 1][1] - C[k][1]
        den = d[0] * ey - d[1] * ex
        if abs(den) < 1e-12:
            continue
        wx, wy = C[k][0] - p[0], C[k][1] - p[1]
        t = (wx * ey - wy * ex) / den
        u = (wx * d[1] - wy * d[0]) / den
        if t > 1e-9 and -1e-9 <= u <= 1 + 1e-9 and (best is None or t < best[0]):
            best = (t, dd[k] + (dd[k + 1] - dd[k]) * min(max(u, 0.0), 1.0))
    if best is None:
        raise ValueError("ray from %r never reaches the inner curve" % (p,))
    return best[1]


# ---------------------------------------------------------------- counts
def allocate(lengths, total, target):
    n = [max(1, int(round(L / target))) for L in lengths]
    big = max(range(len(n)), key=lambda i: lengths[i])
    n[big] += total - sum(n)
    if n[big] < 1:
        raise ValueError("cannot fit %d cells into %d segments" % (total, len(lengths)))
    return n


def row_counts(h, target, min_cell, ends_fixed=3, min_run=8):
    """Rows per column line: near h/target, cells no shorter than `min_cell` where the height allows, one parity,
    neighbours differing by 0 or 2 (a change of one row has no single-cell quad filling)."""
    par = max(1, int(round(h[0] / target))) % 2
    n = []
    for x in h:
        c = max(1, int(round(x / target)))
        if c % 2 != par:
            c = c + 1 if (c - 1 < 1 or x / (c - 1) > 1.25 * target) else c - 1
        while c - 2 >= 1 and x / c < min_cell:
            c -= 2
        n.append(max(c, 1 if par else 2))
    # hysteresis: a height sitting on a threshold (91 mm over 7 rows is 13.0 mm) makes the count flicker 5, 7, 5, 7
    # from one column to the next, and every flicker is a side-on T3 with its half-height cells. A run of one count
    # shorter than `min_run` columns, between two runs that agree, takes their count.
    for _ in range(len(n)):
        runs, i = [], 0
        while i < len(n):
            j = i
            while j + 1 < len(n) and n[j + 1] == n[i]:
                j += 1
            runs.append((i, j))
            i = j + 1
        changed = False
        for r in range(1, len(runs) - 1):
            (a0, a1), (b0, b1), (c0, c1) = runs[r - 1], runs[r], runs[r + 1]
            if b1 - b0 + 1 < min_run and n[a0] == n[c0]:
                for k in range(b0, b1 + 1):
                    n[k] = n[a0]
                changed = True
                break
        if not changed:
            break
    # no row change in the columns at either plate end: those columns are the narrowest (their nodes slide along
    # the end during smoothing) and a side-on T3 there is the worst cell in the whole face
    keep = min(ends_fixed, len(n) // 2)
    for k in range(keep):
        n[k], n[-1 - k] = n[keep], n[-1 - keep]
    for _ in range(len(n)):
        changed = False
        for k in range(len(n) - 1):
            if n[k + 1] - n[k] > 2:
                n[k] += 2
                changed = True
            elif n[k] - n[k + 1] > 2:
                n[k + 1] += 2
                changed = True
        if not changed:
            break
    return n


def column_blocks(a, b):
    """Blocks joining a column line of `a` edges to one of `b`: plain rows and, where they differ, one T3 midway."""
    if a == b:
        return [(1, 1)] * a
    if b == a + 2:
        return [(1, 1)] * (a // 2) + [(1, 3)] + [(1, 1)] * (a - a // 2 - 1)
    if a == b + 2:
        return [(1, 1)] * (b // 2) + [(3, 1)] + [(1, 1)] * (b - b // 2 - 1)
    raise ValueError("column lines of %d and %d edges cannot be joined by one T3" % (a, b))


def root_along_v(root):
    """Whether the root's chord runs more along v than along u."""
    du, dv = abs(root[-1][0] - root[0][0]), abs(root[-1][1] - root[0][1])
    return dv > du


# ---------------------------------------------------------------- the mesh
def build(p):
    """Mesh the face. Returns (mesh, zones, report, geometry)."""
    ents = parse(p.step_path)
    g = outline(ents, p.face, p.root, p.swap_uv)
    if root_along_v(g["root"]):
        # the root runs along the default v axis (the part is not in the orientation of the existing decks):
        # take the other in-plane order, so the mesher sees the same picture in any orientation
        p.swap_uv = not p.swap_uv
        g = outline(ents, p.face, p.root, p.swap_uv)
    k, uv, off = plane_of(ents, p.face, p.swap_uv)
    rep = {"outline": {"edges": g["edge_count"], "sides": g["side_count"], "root_ids": g["root_ids"],
                       "root_mm": round(arc_lengths(g["root"])[-1], 2), "notches": len(g["notches"]),
                       "root_tangent_breaks": g["root_breaks"],
                       "notch_ids": [s["ids"] for s in g["notches"]]}}

    # the band, as verified
    rows = ([(p.fine, p.fine, "buffer%d" % (i + 1)) for i in range(p.buffer_rows)]
            + [(2 * p.fine, p.coarse, "transition")]
            + [(p.coarse, p.coarse, "coarse%d" % (i + 1)) for i in range(p.coarse_rows)])
    rp = root_nodes(ents, p.face, g["root_ids"], uv, p.fine)
    mb, zb, info = band(rp, rows, side=1.0, ends=(g["left_end"], g["right_end"]))
    m, zones = Mesh(), {}
    remap = {i: m.node(*mb.nodes[i]) for i in sorted(mb.nodes)}
    for q in sorted(mb.quads):
        zones[m.quad(*[remap[i] for i in mb.quads[q]])] = zb[q]
    band_top = [remap[i] for i in info["top_row"]]
    N = len(band_top) - 1
    rep["band"] = {"rows": info["rows"], "top_cells": N, "height_mm": sum(r[0] for r in rows)}

    # far boundary with each notch bridged, and the inner curve C below every notch bottom
    T = join(*sum([[g["tops"][i], [g["notches"][i]["mouth_left"], g["notches"][i]["mouth_right"]]]
                   for i in range(len(g["notches"]))], []) + [g["tops"][-1]])
    depth = 0.0
    for s in g["notches"]:
        for c in (s["bottom_left"], s["bottom_right"]):
            depth = max(depth, _d(project_point(T, c), c))
    D = depth + 2.0 * p.far if g["notches"] else 2.0 * p.far
    C = offset_clip(T, D, g["left_end"], g["right_end"])
    m_top = max(2, int(round(D / p.far)))

    feet = []
    for s in g["notches"]:
        feet += [ray_foot(C, s["bottom_left"], s["axis_down"]), ray_foot(C, s["bottom_right"], s["axis_down"])]
    cuts = [0.0] + feet + [arc_lengths(C)[-1]]
    if any(cuts[i + 1] <= cuts[i] for i in range(len(cuts) - 1)):
        raise ValueError("notch feet on the inner curve are out of order: %r" % cuts)
    lens = [cuts[i + 1] - cuts[i] for i in range(len(cuts) - 1)]
    ncell = allocate(lens, N, p.coarse)
    Cpts = []
    for i in range(len(lens)):
        piece = discretize(slice_arc(C, cuts[i], cuts[i + 1]), ncell[i])
        Cpts += piece if not Cpts else piece[1:]
    Cids = [m.node(*q) for q in Cpts]
    splits = [0]
    for n_ in ncell:
        splits.append(splits[-1] + n_)
    rep["inner_curve"] = {"offset_mm": round(D, 2), "deepest_notch_mm": round(depth, 2),
                          "segments": [{"length_mm": round(L, 1), "cells": c} for L, c in zip(lens, ncell)]}

    # middle: row count follows local height, side-on T3 where it changes
    P = m.nodes
    h = [_d(P[Cids[i]], P[band_top[i]]) for i in range(N + 1)]
    nrow = row_counts(h, p.far, p.coarse)
    lines = [[m.node(*q) for q in discretize([P[band_top[i]], P[Cids[i]]], nrow[i])] for i in range(N + 1)]
    for i in range(N):
        ladder(m, lines[i], lines[i + 1], column_blocks(nrow[i], nrow[i + 1]), p.column_split, "middle", zones,
               flip=True)
    rep["middle"] = {"rows_min": min(nrow), "rows_max": max(nrow),
                     "row_steps": sum(1 for i in range(N) if nrow[i] != nrow[i + 1]),
                     "height_min_mm": round(min(h), 1), "height_max_mm": round(max(h), 1),
                     "row_height_min_mm": round(min(h[i] / nrow[i] for i in range(N + 1)), 2),
                     "row_height_max_mm": round(max(h[i] / nrow[i] for i in range(N + 1)), 2)}

    # top strip: blocks between notches, and one under each notch bottom
    tops, notches = g["tops"], g["notches"]
    side_prev = [m.node(*q) for q in discretize([P[Cids[0]], tops[0][0]], m_top)]
    rep["top_strip"] = {"rows": m_top, "blocks": []}
    for t in range(len(tops)):
        a = splits[2 * t]
        b = splits[2 * t + 1] if t < len(notches) else splits[-1]
        top = [m.node(*q) for q in discretize(tops[t], b - a)]
        if t < len(notches):
            s = notches[t]
            ext_len = _d(P[Cids[b]], s["bottom_left"])
            k_ext = min(m_top - 1, max(1, int(round(ext_len / p.far))))
            side_next = ([m.node(*q) for q in discretize([P[Cids[b]], s["bottom_left"]], k_ext)]
                         + [m.node(*q) for q in discretize(s["left_wall"][::-1], m_top - k_ext)[1:]])
        else:
            side_next = [m.node(*q) for q in discretize([P[Cids[b]], tops[-1][-1]], m_top)]
        tfi(m, Cids[a:b + 1], top, side_prev, side_next, "far_field", zones)
        rep["top_strip"]["blocks"].append({"kind": "between", "columns": b - a, "rows": m_top})
        if t < len(notches):
            s = notches[t]
            a2, b2 = splits[2 * t + 1], splits[2 * t + 2]
            bottom_row = [m.node(*q) for q in discretize(s["bottom"], b2 - a2)]
            l2 = [m.node(*q) for q in discretize([P[Cids[a2]], s["bottom_left"]], k_ext)]
            ext_r = _d(P[Cids[b2]], s["bottom_right"])
            if int(round(ext_r / p.far)) != k_ext and abs(ext_r - ext_len) > p.far:
                raise ValueError("notch %d is tilted too far for one row count under its bottom" % t)
            r2 = [m.node(*q) for q in discretize([P[Cids[b2]], s["bottom_right"]], k_ext)]
            tfi(m, Cids[a2:b2 + 1], bottom_row, l2, r2, "under_notch", zones)
            rep["top_strip"]["blocks"].append({"kind": "under_notch", "columns": b2 - a2, "rows": k_ext})
            side_prev = r2 + [m.node(*q) for q in discretize(s["right_wall"], m_top - k_ext)[1:]]
    return m, zones, rep, g


def stats2d(m, which=None):
    q = [x for x in sorted(m.quads) if which is None or which(x)]
    rows = [metrics(m.corners(x)) for x in q]
    cj = [r["corner_jacobian"] for r in rows]
    return {"cells": len(rows), "min_corner_jacobian": round(min(cj), 4),
            "mean_corner_jacobian": round(sum(cj) / len(cj), 4),
            "cj<0.7": sum(1 for v in cj if v < 0.7), "cj<0.5": sum(1 for v in cj if v < 0.5),
            "cj<=0": sum(1 for v in cj if v <= 0),
            "max_aspect": round(max(r["aspect_ratio"] for r in rows), 3),
            "max_skew_deg": round(max(r["skew_deg"] for r in rows), 2),
            "min_edge_mm": round(min(r["min_edge"] for r in rows), 3)}


def finish(p, m, zones, rep, g):
    """Smooth, check the face, sweep it, check the solid. Returns (solid, report)."""
    ents = parse(p.step_path)
    rep["smoothing"] = smooth(m, passes=p.smooth_passes, slide=(g["left_end"], g["right_end"]))
    k, uv, off = plane_of(ents, p.face, p.swap_uv)
    t = p.thickness if p.thickness is not None else thickness_of(ents, p.face)
    start = off if t > 0 else off + t
    rep["hard_points"] = []
    if p.hard_points:
        # through the thickness a hard point has to sit on a sweep level; in the face it takes the nearest node
        levels = [start + abs(t) * j / float(p.layers) for j in range(p.layers + 1)]
        for q in p.hard_points:
            if min(abs(q[k] - v) for v in levels) > 1e-3:
                raise HardPointError("hard point %r is at %s = %.4f, between the %d sweep levels %s; change the layer "
                                     "count or the point" % (tuple(q), AXES[k], q[k], len(levels),
                                                             [round(v, 4) for v in levels]))
        curves = [g["root"], g["left_end"], g["right_end"]] + list(g["tops"]) +             [c for s_ in g["notches"] for c in (s_["left_wall"], s_["bottom"], s_["right_wall"])]
        rep["hard_points"] = relocate(m, [(q[uv[0]], q[uv[1]]) for q in p.hard_points], boundary_curves=curves)
    c = check_all(m)
    rep["face"] = {**stats2d(m), "nodes": len(m.nodes), "quads": len(m.quads), "triangles": 0,
                   "hanging_nodes": len(c["hanging_nodes"]),
                   "duplicate_node_elements": len(c["duplicate_node_elements"]),
                   "zero_area_elements": len(c["zero_area_elements"]),
                   "inverted_elements": len(c["inverted_elements"]),
                   "non_manifold_edges": len(c["non_manifold_edges"]), "orphan_nodes": len(c["orphan_nodes"]),
                   "self_intersections": len(self_intersections(m)), "euler_ok": c["euler_ok"],
                   "topology_ok": c["ok"]}
    rep["face_by_zone"] = {z: stats2d(m, lambda q, z=z: zones[q] == z) for z in sorted(set(zones.values()))}
    s, info = sweep(m, (AXES[uv[0]], AXES[uv[1]]), AXES[k], start, abs(t), p.layers)
    c3 = check_solid(s, info["layers"], info["nodes_per_layer"])
    rep["solid"] = {kk: (len(v) if isinstance(v, list) else v) for kk, v in c3.items()}
    rep["solid"]["sweep"] = {**info, "thickness_mm": abs(t), "from": start, "axis": AXES[k]}
    if p.hard_points:
        import numpy as _np
        from scipy.spatial import cKDTree as _T
        worst = max(float(_T(_np.array(list(s.nodes.values()))).query(_np.array(q))[0]) for q in p.hard_points)
        rep["hard_point_max_distance_mm"] = round(worst, 9)
        if worst > 0.01:
            raise HardPointError("a hard point ended %.6f mm from the nearest node" % worst)
    return s, rep


# ---------------------------------------------------------------- acceptance measures
def outward_cells(m, zones, start_zone, outer_zones, below_mm):
    """Follow every column outward from the last band row and flag cells shorter than `below_mm`.

    Returns {"paths", "paths_flagged", "cells_flagged", "longest_flagged_run", "worst_mm"}. `longest_flagged_run`
    counts adjacent columns in a row that all carry a flagged cell: a continuous band of re-refinement shows up as a
    long run, the half-height cells a side-on T3 makes show up as runs of one or two."""
    edge_q = {}
    for q, ids in m.quads.items():
        for k in range(4):
            edge_q.setdefault(tuple(sorted((ids[k], ids[(k + 1) % 4]))), []).append(q)
    P = m.nodes
    flags, cells, worst = [], set(), None
    for q in sorted(x for x in m.quads if zones[x] == start_zone):
        a, b, c, d = m.quads[q]
        top, cur, bad = (c, d), q, False
        for _ in range(10000):
            nxt = [x for x in edge_q.get(tuple(sorted(top)), []) if x != cur]
            if not nxt or zones[nxt[0]] not in outer_zones:
                break
            cur = nxt[0]
            ids = m.quads[cur]
            k = [i for i in range(4) if {ids[i], ids[(i + 1) % 4]} == set(top)]
            if not k:
                break
            k = k[0]
            h = (math.dist(P[ids[k]], P[ids[(k + 3) % 4]]) + math.dist(P[ids[(k + 1) % 4]], P[ids[(k + 2) % 4]])) / 2
            if h < below_mm:
                bad = True
                cells.add(cur)
                worst = h if worst is None else min(worst, h)
            top = (ids[(k + 2) % 4], ids[(k + 3) % 4])
        flags.append(bad)
    run = best = 0
    for f in flags:
        run = run + 1 if f else 0
        best = max(best, run)
    return {"paths": len(flags), "paths_flagged": sum(flags), "cells_flagged": len(cells),
            "longest_flagged_run": best, "worst_mm": None if worst is None else round(worst, 3)}


def boundary_deviation(ents, face_id, m, swap=False):
    """Largest distance from a mesh boundary node to the true CAD edges of the face, and the area difference."""
    from .mesh import boundary_edges
    _, uv, _ = plane_of(ents, face_id, swap)
    polys = [_poly(ents, e, uv, 2000) for e in face_edges(ents, face_id)]
    bn = {i for a, b in boundary_edges(m) for i in (a, b)}
    dev = max(min(_d(project_point(q, m.nodes[i]), m.nodes[i]) for q in polys) for i in bn)
    g = outline(ents, face_id, swap=swap)
    area = sum(abs(m.area(q)) for q in m.quads)
    return {"boundary_nodes": len(bn), "max_deviation_mm": round(dev, 5),
            "cad_area_mm2": round(g["loop_area"], 1), "mesh_area_mm2": round(area, 1),
            "area_diff_percent": round(100.0 * (area - g["loop_area"]) / g["loop_area"], 4)}
