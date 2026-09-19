# -*- coding: utf-8 -*-
"""Sweep a 2D mesh into solids, and check what came out.

A face mesh swept through a thickness gives one solid per 2D cell per layer: a quadrilateral becomes a hexahedron,
a triangle becomes a wedge. That is the whole point of getting the 2D topology right first -- the wedge count of the
3D mesh is decided entirely in 2D.

Metric definition. `corner_scaled_jacobian` is the determinant of the three unit edge vectors at each corner, taken
as the minimum over the element's corners. It is the same definition already used in `hm/meshio.py` for this
project's base meshes, so the two are comparable with each other. Whether it agrees with what HyperMesh reports as
Jacobian, or as scaled Jacobian, has not been checked, so it is not given either of those names.

Each element type is measured with its own corner list and its own ideal value, and the raw determinant is divided
by that ideal so that a perfectly shaped element of either type scores 1.0:

    CHEXA  -> HEX_CORNERS,   ideal 1.0            the three edges at a cube corner are orthogonal
    CPENTA -> PENTA_CORNERS, ideal sqrt(3)/2      two of the three edges at a wedge corner lie in the triangular
                                                  face, 60 degrees apart, so an ideal wedge cannot reach 1.0

Without that second normalisation every wedge in a mesh reads about 13 percent worse than it is, and a hexahedral
mesh looks better than a wedge mesh of exactly the same quality. A CHEXA that has been collapsed -- the same node
id used twice, which is how a wedge or a pyramid is written in hexahedral form -- is not measured by either
evaluator. It is counted separately as `collapsed_elements`, because a degenerate element has no corner list that
means anything."""
import math

HEX_CORNERS = [(0, 1, 3, 4), (1, 2, 0, 5), (2, 3, 1, 6), (3, 0, 2, 7),
               (4, 7, 5, 0), (5, 4, 6, 1), (6, 5, 7, 2), (7, 6, 4, 3)]
PENTA_CORNERS = [(0, 1, 2, 3), (1, 2, 0, 4), (2, 0, 1, 5), (3, 5, 4, 0), (4, 3, 5, 1), (5, 4, 3, 2)]
# outward-oriented faces: the first four nodes of a CHEXA wind towards the second four, so the bottom face has to
# be reversed to point out of the element
HEX_FACES = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
PENTA_FACES = [(0, 2, 1), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5)]
AXES = {"x": 0, "y": 1, "z": 2}


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _triple(u, v, w):
    return (u[0] * (v[1] * w[2] - v[2] * w[1]) - u[1] * (v[0] * w[2] - v[2] * w[0])
            + u[2] * (v[0] * w[1] - v[1] * w[0]))


def _unit(v):
    L = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / L, v[1] / L, v[2] / L) if L > 0 else (0.0, 0.0, 0.0)


HEX_IDEAL = 1.0
PENTA_IDEAL = math.sqrt(3.0) / 2.0


def corner_scaled_jacobian(xyz, corners, ideal=HEX_IDEAL):
    """Minimum over the corners of the determinant of the three unit edge vectors, divided by `ideal`.

    `ideal` is what a perfectly shaped element of this type reaches: 1.0 for a hexahedron, sqrt(3)/2 for a wedge,
    whose two in-face edges meet at 60 degrees. Passing the wrong one makes the two element types incomparable."""
    return min(_triple(_unit(_sub(xyz[b], xyz[a])), _unit(_sub(xyz[c], xyz[a])), _unit(_sub(xyz[d], xyz[a])))
               for a, b, c, d in corners) / ideal


def volume(xyz):
    """Signed volume, faces split into triangles through the element centroid: works for warped faces too."""
    c = tuple(sum(p[k] for p in xyz) / len(xyz) for k in range(3))
    faces = HEX_FACES if len(xyz) == 8 else PENTA_FACES
    v = 0.0
    for f in faces:
        q = [xyz[i] for i in f]
        fc = tuple(sum(p[k] for p in q) / len(q) for k in range(3))
        for k in range(len(q)):
            a, b = q[k], q[(k + 1) % len(q)]
            v += _triple(_sub(a, c), _sub(b, c), _sub(fc, c)) / 6.0
    return v


def edge_lengths(xyz):
    pairs = ([(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]
             if len(xyz) == 8 else
             [(0, 1), (1, 2), (2, 0), (3, 4), (4, 5), (5, 3), (0, 3), (1, 4), (2, 5)])
    return [math.dist(xyz[a], xyz[b]) for a, b in pairs]


class Solid(object):
    """Nodes {id: (x, y, z)} and elements [(kind, [ids])] with kind 'hex8' or 'penta6'."""

    def __init__(self):
        self.nodes = {}
        self.elements = []

    def add_node(self, x, y, z):
        i = len(self.nodes) + 1
        self.nodes[i] = (float(x), float(y), float(z))
        return i

    def xyz(self, ids):
        return [self.nodes[i] for i in ids]


def sweep(mesh2d, plane, axis, start, thickness, layers, cells=None):
    """Sweep a 2D mesh into solids along `axis`, `layers` layers of equal thickness.

    `plane` names the two in-plane axes, in the order the 2D coordinates are given, e.g. ("z", "y"). `cells` is an
    optional list of node id tuples to sweep instead of mesh2d.quads, so a face mesh holding triangles as well can
    be swept: 4 nodes give a hexahedron, 3 give a wedge.

    The layer order is flipped when it would produce negative volume, so the result is right-handed whichever way
    the 2D mesh was wound relative to the sweep direction. `flipped` is reported rather than hidden."""
    a0, a1, ax = AXES[plane[0]], AXES[plane[1]], AXES[axis]
    if len({a0, a1, ax}) != 3:
        raise ValueError("plane %r and axis %r must be three different axes" % (plane, axis))
    cells = list(mesh2d.quads.values()) if cells is None else [tuple(c) for c in cells]
    ids2d = sorted(mesh2d.nodes)
    s = Solid()
    grid = {}
    for k in range(layers + 1):
        off = start + thickness * k / float(layers)
        for i in ids2d:
            p = [0.0, 0.0, 0.0]
            p[a0], p[a1], p[ax] = mesh2d.nodes[i][0], mesh2d.nodes[i][1], off
            grid[(i, k)] = s.add_node(*p)
    for k in range(layers):
        for c in cells:
            lo = [grid[(i, k)] for i in c]
            hi = [grid[(i, k + 1)] for i in c]
            s.elements.append(("hex8" if len(c) == 4 else "penta6", lo + hi))
    flipped = False
    if s.elements:
        kind, ids = s.elements[0]
        if volume(s.xyz(ids)) < 0:
            flipped = True
            s.elements = [(kd, v[len(v) // 2:] + v[:len(v) // 2]) for kd, v in s.elements]
    return s, {"flipped_for_positive_volume": flipped, "layers": layers,
               "nodes_per_layer": len(ids2d), "cells_per_layer": len(cells)}


def faces_of(kind, ids):
    if kind == "hex8":
        return [tuple(ids[i] for i in f) for f in HEX_FACES]
    return [tuple(ids[i] for i in f) for f in PENTA_FACES]


def check_solid(s, layers=None, nodes_per_layer=None):
    """Every 3D check of the plan, section 26, plus the two sweep-specific ones."""
    kinds = {}
    dup, zero, neg_vol, neg_jac, sj, ar, vols = [], [], [], [], [], [], []
    by_kind = {}
    for n, (kind, ids) in enumerate(s.elements, 1):
        kinds[kind] = kinds.get(kind, 0) + 1
        if len(set(ids)) != len(ids):
            dup.append(n)
            continue
        xyz = s.xyz(ids)
        v = volume(xyz)
        vols.append(v)
        if abs(v) <= 1e-12:
            zero.append(n)
        if v < 0:
            neg_vol.append(n)
        j = (corner_scaled_jacobian(xyz, HEX_CORNERS, HEX_IDEAL) if kind == "hex8"
             else corner_scaled_jacobian(xyz, PENTA_CORNERS, PENTA_IDEAL))
        sj.append(j)
        by_kind.setdefault(kind, []).append(j)
        if j <= 0:
            neg_jac.append(n)
        e = edge_lengths(xyz)
        ar.append(max(e) / min(e) if min(e) > 0 else float("inf"))
    use = {}
    for n, (kind, ids) in enumerate(s.elements, 1):
        for f in faces_of(kind, ids):
            use.setdefault(tuple(sorted(f)), []).append(n)
    over = {f: e for f, e in use.items() if len(e) > 2}
    r = {"elements": len(s.elements), "nodes": len(s.nodes),
         "hex8": kinds.get("hex8", 0), "penta6": kinds.get("penta6", 0),
         "other_kinds": {k: v for k, v in kinds.items() if k not in ("hex8", "penta6")},
         "collapsed_elements": dup, "duplicate_node_elements": dup,
         "scaled_jacobian_by_kind": {k: {"count": len(v), "min": min(v), "mean": sum(v) / len(v),
                                         "below_0.7": sum(1 for x in v if x < 0.7),
                                         "below_0.5": sum(1 for x in v if x < 0.5)} for k, v in by_kind.items()}, "zero_volume_elements": zero, "negative_volume_elements": neg_vol,
         "non_positive_jacobian_elements": neg_jac,
         "faces_shared_by_more_than_two": sorted(over)[:10], "faces_shared_by_more_than_two_count": len(over),
         "internal_faces": sum(1 for e in use.values() if len(e) == 2),
         "surface_faces": sum(1 for e in use.values() if len(e) == 1),
         "min_scaled_jacobian": min(sj) if sj else None, "mean_scaled_jacobian": (sum(sj) / len(sj)) if sj else None,
         "sj_below_0.7": sum(1 for v in sj if v < 0.7), "sj_below_0.5": sum(1 for v in sj if v < 0.5),
         "max_aspect_ratio": max(ar) if ar else None, "mean_aspect_ratio": (sum(ar) / len(ar)) if ar else None,
         "min_volume": min(vols) if vols else None, "total_volume": sum(vols) if vols else None}
    if layers is not None and nodes_per_layer is not None:
        r["layer_nodes_expected"] = nodes_per_layer * (layers + 1)
        r["layer_nodes_ok"] = r["layer_nodes_expected"] == len(s.nodes)
        r["elements_expected"] = layers * (len(s.elements) // layers if layers else 0)
    r["ok"] = (not dup and not zero and not neg_vol and not neg_jac and not over
               and not r["other_kinds"] and r.get("layer_nodes_ok", True))
    return r


def write_bdf(s, path, title="mesh_transition"):
    """R1 form: large-field GRID*, fixed-field CHEXA / CPENTA, millimetres."""
    import io as _io
    with _io.open(path, "w", encoding="ascii", newline="\r\n") as f:
        f.write("$ %s\n$ Units: mm\nSOL 101\nCEND\nBEGIN BULK\n" % title)
        for i in sorted(s.nodes):
            x, y, z = s.nodes[i]
            f.write("%-8s%16d%16s%16.9e%16.9e%-8s\n" % ("GRID*", i, "", x, y, "*"))
            f.write("%-8s%16.9e\n" % ("*", z))
        for n, (kind, ids) in enumerate(s.elements, 1):
            if kind == "hex8":
                f.write("%-8s%8d%8d%8d%8d%8d%8d%8d%8d%-8s\n" % (("CHEXA", n, 1) + tuple(ids[:6]) + ("+",)))
                f.write("%-8s%8d%8d\n" % ("+", ids[6], ids[7]))
            else:
                f.write("%-8s%8d%8d%8d%8d%8d%8d%8d%8d\n" % (("CPENTA", n, 1) + tuple(ids)))
        f.write("ENDDATA\n")
    return path
