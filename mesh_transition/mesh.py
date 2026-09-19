# -*- coding: utf-8 -*-
"""A 2D quadrilateral mesh and the topology checks a transition template has to pass.

The checks are the acceptance list of the plan (sections 25 and 34): no hanging node, no duplicate node inside an
element, no zero area, every shared edge shared by exactly two elements, no orphan node, consistent orientation.
They are deliberately geometric as well as topological: a node that sits on another element's edge without being one
of its corners is a hanging node even when the connectivity looks tidy."""
import math

# Node welding tolerance, in model units (mm). Two nodes closer than this are the same node. 1e-7 mm is far below
# any real feature and well above what accumulates when two parts of a long strip reach the same point by different
# sums: over a few thousand millimetres that difference reaches 1e-10, and a tighter tolerance leaves a pair of
# coincident unwelded nodes that no local check sees but the Euler relation does.
WELD_TOL = 1e-7


class Mesh(object):
    """Nodes numbered from 1, quadrilaterals numbered from 1, counter-clockwise."""

    def __init__(self):
        self.nodes = {}                     # id -> (x, y)
        self.quads = {}                     # id -> (n1, n2, n3, n4)
        self._grid = {}                     # bucket -> node ids, so tiled templates weld on contact

    # ---------------------------------------------------------------- building
    def node(self, x, y):
        """Node id at (x, y); any existing node within WELD_TOL is reused. This is how templates weld.

        The neighbouring buckets are searched too. Quantising the coordinate into one bucket is not a tolerance: two
        points a nanometre apart that straddle a bucket edge would land in different buckets and never weld, which
        leaves a slit that every local check passes and only the Euler relation notices."""
        x, y = float(x), float(y)
        gx, gy = int(math.floor(x / WELD_TOL)), int(math.floor(y / WELD_TOL))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in self._grid.get((gx + dx, gy + dy), ()):
                    px, py = self.nodes[i]
                    if abs(px - x) <= WELD_TOL and abs(py - y) <= WELD_TOL:
                        return i
        i = len(self.nodes) + 1
        self.nodes[i] = (x, y)
        self._grid.setdefault((gx, gy), []).append(i)
        return i

    def quad(self, a, b, c, d):
        i = len(self.quads) + 1
        self.quads[i] = (a, b, c, d)
        return i

    def offset(self, other, dx=0.0, dy=0.0):
        """Copy another mesh into this one, shifted; nodes at the same point are welded, not duplicated."""
        remap = {i: self.node(x + dx, y + dy) for i, (x, y) in other.nodes.items()}
        for ids in other.quads.values():
            self.quad(*[remap[i] for i in ids])
        return remap

    # ---------------------------------------------------------------- geometry
    def xy(self, i):
        return self.nodes[i]

    def corners(self, q):
        return [self.nodes[i] for i in self.quads[q]]

    def area(self, q):
        """Signed area (shoelace); positive means counter-clockwise."""
        p = self.corners(q)
        return 0.5 * sum(p[k][0] * p[(k + 1) % 4][1] - p[(k + 1) % 4][0] * p[k][1] for k in range(4))

    def edges(self, q):
        ids = self.quads[q]
        return [(ids[k], ids[(k + 1) % 4]) for k in range(4)]

    def edge_use(self):
        """Undirected edge -> list of quads using it."""
        use = {}
        for q in self.quads:
            for a, b in self.edges(q):
                use.setdefault((min(a, b), max(a, b)), []).append(q)
        return use


# ---------------------------------------------------------------- checks
def duplicate_node_elements(m):
    """Elements that name the same node twice: a collapsed quad, however tidy the card looks."""
    return [q for q, ids in m.quads.items() if len(set(ids)) != 4]


def zero_area_elements(m, tol=1e-12):
    return [q for q in m.quads if abs(m.area(q)) <= tol]


def inverted_elements(m):
    return [q for q in m.quads if m.area(q) < 0]


def non_manifold_edges(m):
    """Edges used by more than two elements."""
    return {e: qs for e, qs in m.edge_use().items() if len(qs) > 2}


def boundary_edges(m):
    return sorted(e for e, qs in m.edge_use().items() if len(qs) == 1)


def orphan_nodes(m):
    used = {i for ids in m.quads.values() for i in ids}
    return sorted(set(m.nodes) - used)


def node_quads(m):
    """Node -> the elements that have it as a corner."""
    d = {i: [] for i in m.nodes}
    for q, ids in m.quads.items():
        for i in ids:
            d[i].append(q)
    return d


def hanging_nodes(m, tol=1e-7):
    """Nodes lying strictly inside an element edge without being a corner of that element.

    This is the check that catches a transition that only *looks* conformal: the fine side's extra node resting on
    the coarse side's edge. Geometric, not topological, because that node may be perfectly well connected on its own
    side of the interface.

    Nodes are bucketed on a grid first, so only the few near an edge are tested; testing every node against every
    edge is quadratic and unusable on a real mesh."""
    use = m.edge_use()
    if not use:
        return []
    lengths = [math.hypot(m.nodes[b][0] - m.nodes[a][0], m.nodes[b][1] - m.nodes[a][1]) for a, b in use]
    cell = max(sorted(lengths)[len(lengths) // 2], 1e-9)
    grid = {}
    for i, (x, y) in m.nodes.items():
        grid.setdefault((int(math.floor(x / cell)), int(math.floor(y / cell))), []).append(i)
    out = []
    for (a, b), qs in use.items():
        (xa, ya), (xb, yb) = m.nodes[a], m.nodes[b]
        ex, ey = xb - xa, yb - ya
        L2 = ex * ex + ey * ey
        if L2 <= 0:
            continue
        gx0, gx1 = sorted((int(math.floor(xa / cell)), int(math.floor(xb / cell))))
        gy0, gy1 = sorted((int(math.floor(ya / cell)), int(math.floor(yb / cell))))
        for gx in range(gx0 - 1, gx1 + 2):
            for gy in range(gy0 - 1, gy1 + 2):
                for i in grid.get((gx, gy), ()):
                    if i in (a, b):
                        continue
                    x, y = m.nodes[i]
                    t = ((x - xa) * ex + (y - ya) * ey) / L2
                    if not (tol < t < 1.0 - tol):
                        continue
                    if abs((x - xa) * ey - (y - ya) * ex) / math.sqrt(L2) <= tol:
                        out.append({"node": i, "on_edge": (a, b), "elements": sorted(qs), "t": round(t, 6)})
    return out


def flat_corners(m, tol_deg=1.0):
    """Corners whose interior angle is 180 degrees or more: the element has a node that is not really a corner."""
    from .quality import corner_values
    out = []
    for q in m.quads:
        for k, (_, ang) in enumerate(corner_values(m.corners(q))):
            if ang >= 180.0 - tol_deg:
                out.append({"element": q, "node": m.quads[q][k], "angle_deg": round(ang, 3)})
    return out


def check_all(m):
    """Every topology check of the plan in one dict; `ok` is true only when all of them are empty."""
    r = {
        "nodes": len(m.nodes), "elements": len(m.quads),
        "duplicate_node_elements": duplicate_node_elements(m),
        "zero_area_elements": zero_area_elements(m),
        "inverted_elements": inverted_elements(m),
        "non_manifold_edges": sorted(non_manifold_edges(m)),
        "orphan_nodes": orphan_nodes(m),
        "hanging_nodes": hanging_nodes(m),
        "flat_corners": flat_corners(m),
        "boundary_edges": len(boundary_edges(m)),
    }
    nq = node_quads(m)
    r["min_node_elements"] = min((len(v) for v in nq.values()), default=0)
    r["max_node_elements"] = max((len(v) for v in nq.values()), default=0)
    bnodes = {i for e in boundary_edges(m) for i in e}
    r["interior_nodes"] = len(m.nodes) - len(bnodes)
    r["interior_node_min_elements"] = min((len(nq[i]) for i in m.nodes if i not in bnodes), default=None)
    # Euler for a simply connected quad mesh: elements = boundary_edges / 2 + interior_nodes - 1
    r["euler_elements_expected"] = r["boundary_edges"] // 2 + r["interior_nodes"] - 1
    r["euler_ok"] = r["euler_elements_expected"] == len(m.quads)
    r["ok"] = (not r["duplicate_node_elements"] and not r["zero_area_elements"] and not r["inverted_elements"]
               and not r["non_manifold_edges"] and not r["orphan_nodes"] and not r["hanging_nodes"]
               and not r["flat_corners"] and r["euler_ok"])
    return r
