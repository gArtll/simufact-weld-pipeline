# -*- coding: utf-8 -*-
"""Read a boundary curve out of a STEP file instead of off an existing mesh.

A weld root read from a mesh boundary carries that mesh's node spacing and its kinks. The same curve read from the
CAD geometry is the thing the mesh was supposed to approximate. This module reads enough of AP203 / AP214 to get
there: the entity graph, B-spline curves and surfaces, lines and circles, and the edges bounding a face.

It is a reader, not a kernel: no booleans, no surface intersection, no tolerance healing. What it gives back is
control points, knots and sampled curves."""
import io
import math
import re

NUM = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


# ---------------------------------------------------------------- parsing
def _split_args(text):
    """Split one entity's argument list on commas that are not inside brackets or quotes."""
    out, depth, quoted, cur = [], 0, False, []
    for ch in text:
        if quoted:
            cur.append(ch)
            if ch == "'":
                quoted = False
            continue
        if ch == "'":
            quoted = True
            cur.append(ch)
        elif ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def _value(tok):
    """A STEP argument: reference, number, string, enumeration, or a nested list of the same."""
    tok = tok.strip()
    if tok.startswith("#"):
        return int(tok[1:])
    if tok.startswith("("):
        return [_value(t) for t in _split_args(tok[1:-1])]
    if tok.startswith("'"):
        return tok.strip("'")
    if NUM.match(tok):
        return float(tok)
    return tok


def parse(path):
    """{id: (TYPE, [arguments])} for the DATA section. Bracket-aware, so nested lists survive."""
    text = io.open(path, encoding="latin-1", errors="ignore").read()
    data = text.split("DATA;", 1)[1]
    ents, i, n = {}, 0, len(data)
    while True:
        h = data.find("#", i)
        if h < 0:
            break
        m = re.match(r"#(\d+)\s*=\s*([A-Z_0-9]+)\s*\(", data[h:h + 200])
        if not m:
            i = h + 1
            continue
        eid, kind = int(m.group(1)), m.group(2)
        j = h + m.end()                       # just after the opening bracket
        depth, quoted, k = 1, False, j
        while k < n and depth:
            ch = data[k]
            if quoted:
                quoted = ch != "'"
            elif ch == "'":
                quoted = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            k += 1
        ents[eid] = (kind, [_value(t) for t in _split_args(data[j:k - 1])])
        i = k
    return ents


# ---------------------------------------------------------------- geometry
def point(ents, ref):
    kind, a = ents[ref]
    if kind == "CARTESIAN_POINT":
        return tuple(float(v) for v in a[1])
    if kind == "VERTEX_POINT":
        return point(ents, a[1])
    raise TypeError("#%d is %s, not a point" % (ref, kind))


def direction(ents, ref):
    return tuple(float(v) for v in ents[ref][1][1])


def knot_vector(knots, mult):
    out = []
    for u, m in zip(knots, mult):
        out += [float(u)] * int(m)
    return out


def de_boor(degree, ctrl, knots, t):
    """Curve point at parameter t (de Boor). `ctrl` are 3D points, `knots` the expanded knot vector."""
    n = len(ctrl)
    t = min(max(t, knots[degree]), knots[n])
    k = degree
    while k < n - 1 and t >= knots[k + 1]:
        k += 1
    d = [list(ctrl[j + k - degree]) for j in range(degree + 1)]
    for r in range(1, degree + 1):
        for j in range(degree, r - 1, -1):
            i = j + k - degree
            lo, hi = knots[i], knots[i + degree - r + 1]
            a = 0.0 if hi == lo else (t - lo) / (hi - lo)
            d[j] = [(1.0 - a) * d[j - 1][c] + a * d[j][c] for c in range(3)]
    return tuple(d[degree])


def curve(ents, ref):
    """(kind, sampler) for the geometry of an edge: B-spline, line or circle."""
    kind, a = ents[ref][0], ents[ref][1]
    if kind == "B_SPLINE_CURVE_WITH_KNOTS":
        degree = int(a[1])
        ctrl = [point(ents, r) for r in a[2]]
        mult = [int(v) for v in a[6]]
        knots = knot_vector([float(v) for v in a[7]], mult)
        lo, hi = knots[degree], knots[len(ctrl)]
        return kind, (lambda s: de_boor(degree, ctrl, knots, lo + (hi - lo) * s)), {"degree": degree,
                                                                                    "control_points": len(ctrl)}
    if kind == "LINE":
        p0 = point(ents, a[1])
        vec = ents[a[2]][1]                                   # VECTOR('', direction, magnitude)
        d = direction(ents, vec[1])
        mag = float(vec[2])
        return kind, (lambda s: tuple(p0[c] + d[c] * mag * s for c in range(3))), {"magnitude": mag}
    if kind == "CIRCLE":
        ax = ents[a[1]][1]
        c = point(ents, ax[1])
        z = direction(ents, ax[2])
        x = direction(ents, ax[3])
        y = tuple(z[(i + 1) % 3] * x[(i + 2) % 3] - z[(i + 2) % 3] * x[(i + 1) % 3] for i in range(3))
        r = float(a[2])
        return kind, (lambda s: tuple(c[i] + r * (math.cos(2 * math.pi * s) * x[i] + math.sin(2 * math.pi * s) * y[i])
                                      for i in range(3))), {"radius": r}
    raise TypeError("#%d is %s: not a curve this reader handles" % (ref, kind))


def face_edges(ents, face_id):
    """The edge curves bounding one ADVANCED_FACE, with their end points."""
    kind, a = ents[face_id]
    if kind != "ADVANCED_FACE":
        raise TypeError("#%d is %s" % (face_id, kind))
    out = []
    for bound in a[1]:
        loop = ents[ents[bound][1][1]]
        for oe in loop[1][1]:
            ec = ents[oe][1][3]
            v1, v2, geom = ents[ec][1][1], ents[ec][1][2], ents[ec][1][3]
            out.append({"edge": ec, "geometry": geom, "kind": ents[geom][0],
                        "p1": point(ents, v1), "p2": point(ents, v2)})
    return out


def faces(ents):
    """Every ADVANCED_FACE with the type of its underlying surface."""
    return [{"face": i, "surface": a[2], "surface_kind": ents[a[2]][0], "bounds": a[1]}
            for i, (k, a) in ents.items() if k == "ADVANCED_FACE"]


def sample(ents, geom_ref, n=200):
    """A curve as a polyline of n+1 points."""
    _, f, _ = curve(ents, geom_ref)
    return [f(k / float(n)) for k in range(n + 1)]


def sample_edge(ents, edge, n=200):
    """One bounded edge as a polyline from its first vertex to its second.

    A STEP curve is unbounded in parameter: a CIRCLE entity is the whole circle, a LINE the whole line. The edge is
    the piece between its two vertices, so each kind is clipped to them. For a circle the minor arc is taken, which
    is what a fillet or a blend between straight runs always is; a major arc would need the edge orientation flag
    and is refused rather than guessed."""
    kind = ents[edge["geometry"]][0]
    p1, p2 = edge["p1"], edge["p2"]
    if kind == "LINE":
        return [tuple(p1[c] + (p2[c] - p1[c]) * k / float(n) for c in range(3)) for k in range(n + 1)]
    if kind == "CIRCLE":
        a = ents[edge["geometry"]][1]
        ax = ents[a[1]][1]
        c = point(ents, ax[1])
        z = direction(ents, ax[2])
        x = direction(ents, ax[3])
        y = tuple(z[(i + 1) % 3] * x[(i + 2) % 3] - z[(i + 2) % 3] * x[(i + 1) % 3] for i in range(3))
        r = float(a[2])

        def angle_of(p):
            v = [p[i] - c[i] for i in range(3)]
            return math.atan2(sum(v[i] * y[i] for i in range(3)), sum(v[i] * x[i] for i in range(3)))
        t1, t2 = angle_of(p1), angle_of(p2)
        d = (t2 - t1 + math.pi) % (2 * math.pi) - math.pi          # the minor arc
        if abs(d) > math.pi * 0.999:
            raise ValueError("edge on circle #%d spans a half turn or more; orientation would have to be guessed"
                             % edge["geometry"])
        return [tuple(c[i] + r * (math.cos(t1 + d * k / float(n)) * x[i] + math.sin(t1 + d * k / float(n)) * y[i])
                      for i in range(3)) for k in range(n + 1)]
    if kind == "B_SPLINE_CURVE_WITH_KNOTS":
        _, f, _ = curve(ents, edge["geometry"])
        pts = [f(k / float(n)) for k in range(n + 1)]
        d1 = sum((pts[0][i] - p1[i]) ** 2 for i in range(3))
        d2 = sum((pts[0][i] - p2[i]) ** 2 for i in range(3))
        return pts if d1 <= d2 else pts[::-1]
    raise TypeError("edge geometry #%d is %s" % (edge["geometry"], kind))


def chain_edges(edges, tol=1e-6):
    """Order a set of edges into chains by matching end points. Returns [[edge, ...], ...]."""
    def key(p):
        return tuple(round(v / tol) for v in p)
    ends = {}
    for e in edges:
        ends.setdefault(key(e["p1"]), []).append(e)
        ends.setdefault(key(e["p2"]), []).append(e)
    used, chains = set(), []
    for e0 in edges:
        if id(e0) in used:
            continue
        chain, used_here = [e0], {id(e0)}
        used.add(id(e0))
        for which in ("p2", "p1"):
            cur, at = e0, e0[which]
            while True:
                nxt = [e for e in ends.get(key(at), []) if id(e) not in used]
                if not nxt:
                    break
                e = nxt[0]
                used.add(id(e))
                at = e["p2"] if key(e["p1"]) == key(at) else e["p1"]
                chain = chain + [e] if which == "p2" else [e] + chain
        chains.append(chain)
    return chains


def tangent_at(ents, edge, end, eps=1e-6):
    """Unit tangent of one bounded edge at one of its two vertices, pointing along p1 -> p2.

    Taken from the entity's own definition rather than from a sampled polyline. A polyline reports whatever angle
    its sampling happens to spread a corner over -- the same 64 degree corner reads as 20 degrees at 2 mm sampling
    and as 84 degrees off a 5 mm mesh -- so the angle between two edges at a shared vertex has to come from the
    geometry. LINE and CIRCLE are differentiated exactly; a B-spline is smooth inside one edge, so a difference
    quotient on its own sampler is accurate there."""
    if end not in ("p1", "p2"):
        raise ValueError("end must be 'p1' or 'p2'")
    kind = ents[edge["geometry"]][0]
    p1, p2 = edge["p1"], edge["p2"]
    if kind == "LINE":
        v = [p2[i] - p1[i] for i in range(3)]
    elif kind == "CIRCLE":
        a = ents[edge["geometry"]][1]
        ax = ents[a[1]][1]
        c, z, x = point(ents, ax[1]), direction(ents, ax[2]), direction(ents, ax[3])
        y = tuple(z[(i + 1) % 3] * x[(i + 2) % 3] - z[(i + 2) % 3] * x[(i + 1) % 3] for i in range(3))

        def ang(p):
            w = [p[i] - c[i] for i in range(3)]
            return math.atan2(sum(w[i] * y[i] for i in range(3)), sum(w[i] * x[i] for i in range(3)))
        t1, t2 = ang(p1), ang(p2)
        d = (t2 - t1 + math.pi) % (2 * math.pi) - math.pi
        t = t1 if end == "p1" else t2
        s = 1.0 if d >= 0 else -1.0
        v = [s * (-math.sin(t) * x[i] + math.cos(t) * y[i]) for i in range(3)]
    else:
        pts = sample_edge(ents, edge, 200)
        a, b = (pts[0], pts[1]) if end == "p1" else (pts[-2], pts[-1])
        v = [b[i] - a[i] for i in range(3)]
    L = math.sqrt(sum(c * c for c in v))
    return tuple(c / L for c in v)


def g1_break(ents, edge_a, edge_b, tol=1e-6):
    """The angle in degrees between two edges meeting at a shared vertex, from both entities' analytic tangents.

    Returns (turn_degrees, vertex). The edges are taken in traversal order: edge_a ends where edge_b begins."""
    ends = [(ka, kb) for ka in ("p1", "p2") for kb in ("p1", "p2")
            if max(abs(edge_a[ka][i] - edge_b[kb][i]) for i in range(3)) < tol]
    if not ends:
        raise ValueError("edges #%d and #%d do not share a vertex" % (edge_a["edge"], edge_b["edge"]))
    ka, kb = ends[0]
    ta = tangent_at(ents, edge_a, ka)
    tb = tangent_at(ents, edge_b, kb)
    if ka == "p1":
        ta = tuple(-c for c in ta)                 # always leaving edge_a
    if kb == "p2":
        tb = tuple(-c for c in tb)                 # always entering edge_b
    d = max(-1.0, min(1.0, sum(ta[i] * tb[i] for i in range(3))))
    return math.degrees(math.acos(d)), edge_a[ka]
