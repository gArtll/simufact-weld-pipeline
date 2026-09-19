# -*- coding: utf-8 -*-
"""Transition bands that survive a corner in the boundary they follow.

A band built by offsetting a curve normally cannot cross a tangent discontinuity. On the side the band opens, the
offset leaves a gap that grows with height; on the side it closes, neighbouring offset points cross and the row folds
over itself. Either way the cells next to the corner stop being usable long before the band reaches its full height.

The way out taken here is not to smooth the corner. The boundary is the part; it stays exactly as the geometry gives
it, corner included. What changes is the number of cells: each row is offset from the row below by a mitre, and
wherever the mitre has pulled the edges out of their size band the row gains or loses cells through the same
transition templates used on the straight runs. A row therefore need not have the same cell count as the row under
it, and the corner is absorbed by that count changing.

Blocks
------
A row is joined to the row above it by a list of blocks, each saying how many edges of the lower row face how many
of the upper:

    (1, 1)   plain quadrilateral
    (3, 1)   T3, coarsening upward             (1, 3)  T3 mirrored, refining upward
    (2, 1)   T2, only ever in adjacent pairs   (1, 2)  likewise

A block changes the boundary edge count of the region it fills by `n_lower + n_upper`, so (1,1), (3,1) and (1,3)
leave the parity of that count alone and (2,1) and (1,2) each flip it. That is the same parity rule the straight
runs obey, and it is why local corner corrections are made with T3 blocks: a T3 changes the cell count by two, which
is the smallest change a single block can make without owing a partner somewhere else in the row.
"""
import math

from .block import project_point
from .mesh import Mesh, boundary_edges
from .templates import can_build, layout


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _unit(v):
    L = math.hypot(v[0], v[1])
    return (v[0] / L, v[1] / L) if L > 0 else (0.0, 0.0)


def _lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def turn_angles(pts):
    """Signed turn at every node, in radians; zero at the two ends. Positive is a left turn."""
    out = [0.0]
    for i in range(1, len(pts) - 1):
        a, b = _unit(_sub(pts[i], pts[i - 1])), _unit(_sub(pts[i + 1], pts[i]))
        out.append(math.atan2(a[0] * b[1] - a[1] * b[0], a[0] * b[0] + a[1] * b[1]))
    out.append(0.0)
    return out


def offset_row(pts, dh, side=1.0, max_miter=6.0):
    """Offset every node of a polyline by `dh` along its angle bisector.

    A mitre, not a rounding: the offset of a corner node is the point where the offsets of its two edges meet, so a
    corner in the boundary stays a corner at every height instead of being blended away. The mitre length grows as
    1/cos(turn/2) and is capped, because at a turn approaching 180 degrees it goes to infinity."""
    n = len(pts)
    e = [_unit(_sub(pts[i + 1], pts[i])) for i in range(n - 1)]
    nor = [(side * -t[1], side * t[0]) for t in e]
    out = []
    for i in range(n):
        if i == 0:
            d, s = nor[0], 1.0
        elif i == n - 1:
            d, s = nor[-1], 1.0
        else:
            a, b = nor[i - 1], nor[i]
            m = (a[0] + b[0], a[1] + b[1])
            if math.hypot(*m) < 1e-12:
                raise ValueError("boundary reverses on itself at node %d; no mitre exists" % i)
            d = _unit(m)
            s = min(1.0 / max(d[0] * a[0] + d[1] * a[1], 1e-9), max_miter)
        out.append((pts[i][0] + dh * s * d[0], pts[i][1] + dh * s * d[1]))
    return out


# ---------------------------------------------------------------- planning one row
def plan_row(pts, target, lo=0.6, hi=1.7):
    """Which blocks join a row whose offset points are `pts` to the row above it.

    Edges that the mitre has squeezed below `lo * target` are merged three into one, edges it has stretched past
    `hi * target` are split one into three. Everywhere else the count is unchanged. Three, not two, because a single
    T2 block does not close on its own."""
    L = [math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1)]
    blocks, up, i = [], [pts[0]], 0
    while i < len(L):
        if L[i] < lo * target and i + 3 <= len(L):
            blocks.append((3, 1))
            up.append(pts[i + 3])
            i += 3
        elif L[i] > hi * target and L[i] > 2.2 * target:
            blocks.append((1, 3))
            up += [_lerp(pts[i], pts[i + 1], 1 / 3.0), _lerp(pts[i], pts[i + 1], 2 / 3.0), pts[i + 1]]
            i += 1
        else:
            blocks.append((1, 1))
            up.append(pts[i + 1])
            i += 1
    return blocks, up


def units_of(blocks):
    """Group a block list into buildable units, pairing the T2 blocks. Raises if one is left unpaired."""
    out, i = [], 0
    while i < len(blocks):
        b = blocks[i]
        if b == (2, 1) and i + 1 < len(blocks) and blocks[i + 1] == (2, 1):
            out.append("T2P")
            i += 2
        elif b == (1, 2) and i + 1 < len(blocks) and blocks[i + 1] == (1, 2):
            out.append("R2P")
            i += 2
        elif b == (1, 1):
            out.append("Q")
            i += 1
        elif b == (3, 1):
            out.append("T3")
            i += 1
        elif b == (1, 3):
            out.append("R3")
            i += 1
        else:
            raise ValueError("block %r at %d cannot be built: a 2->1 cell has no adjacent partner" % (b, i))
    return out


UNIT_SPAN = {"Q": (1, 1), "T3": (3, 1), "R3": (1, 3), "T2P": (4, 2), "R2P": (2, 4)}


def ladder(mesh, lower, upper, blocks, split=0.5, zone=None, zones=None, flip=False):
    """Fill the region between two node rows with quadrilaterals.

    `lower` and `upper` are node ids, already in the mesh, left to right. Returns the element ids created. The
    interior nodes a transition block needs are placed at `split` of the way from the fine row to the coarse one,
    under the fine node they belong to. `flip` reverses the winding, which is what offsetting to the other side of
    the boundary needs: the rows then run clockwise and every element would otherwise come out inverted."""
    P = mesh.nodes
    made, i, j = [], 0, 0

    def add(*ids):
        q = mesh.quad(*(ids[::-1] if flip else ids))
        made.append(q)
        if zones is not None:
            zones[q] = zone
        return q

    for u in units_of(blocks):
        nl, nu = UNIT_SPAN[u]
        if u == "Q":
            add(lower[i], lower[i + 1], upper[j + 1], upper[j])
        elif u in ("T3", "T2P"):                                   # fine below, coarse above
            c = nl
            mid = [mesh.node(*_lerp(P[lower[i + k]], _lerp(P[upper[j]], P[upper[j + nu]], k / float(c)), split))
                   if nu == 1 else
                   mesh.node(*_lerp(P[lower[i + k]], _on(P, upper[j:j + nu + 1], k / float(c)), split))
                   for k in range(1, c)]
            add(lower[i], lower[i + 1], mid[0], upper[j])
            for k in range(1, c - 1):
                add(lower[i + k], lower[i + k + 1], mid[k], mid[k - 1])
            add(lower[i + c - 1], lower[i + c], upper[j + nu], mid[-1])
            for k in range(nu):
                a = mid[0] if k == 0 else mid[2 * k - 1]
                b = mid[-1] if k == nu - 1 else mid[2 * k + 1]
                add(a, b, upper[j + k + 1], upper[j + k])
        else:                                                      # coarse below, fine above
            c = nu
            mid = [mesh.node(*_lerp(P[upper[j + k]], _on(P, lower[i:i + nl + 1], k / float(c)), split))
                   for k in range(1, c)]
            for k in range(nl):
                a = mid[0] if k == 0 else mid[2 * k - 1]
                b = mid[-1] if k == nl - 1 else mid[2 * k + 1]
                add(lower[i + k], lower[i + k + 1], b, a)
            add(lower[i], mid[0], upper[j + 1], upper[j])
            for k in range(1, c - 1):
                add(mid[k - 1], mid[k], upper[j + k + 1], upper[j + k])
            add(mid[-1], lower[i + nl], upper[j + c], upper[j + c - 1])
        i += nl
        j += nu
    if i != len(lower) - 1 or j != len(upper) - 1:
        raise ValueError("blocks span %d/%d lower and %d/%d upper edges" % (i, len(lower) - 1, j, len(upper) - 1))
    return made


def _on(P, ids, t):
    """The point a fraction `t` along the polyline through node ids, by index not by arc length."""
    n = len(ids) - 1
    k = min(int(t * n), n - 1)
    return _lerp(P[ids[k]], P[ids[k + 1]], t * n - k)


# ---------------------------------------------------------------- the band
def transition_blocks(n_fine, n_coarse):
    """The block list of a straight transition row, from the mixed T2/T3 solution."""
    cells = layout(3 * n_coarse - n_fine, n_fine - 2 * n_coarse, "interleaved")
    ok, why = can_build(cells)
    if not ok:
        raise ValueError(why)
    return [(c, 1) for c in cells]


def band(root, rows, side=1.0, split=0.5, ends=None):
    """Build a band of rows along `root`, corners and all.

    `rows` is a list of (height, target_cell_size, name) from the boundary outward. Every row is mitre-offset from
    the row below and then given the cells it needs for that size; where the mitre has changed the spacing, the
    count changes with it. The row named 'transition' is the one that carries the global fine-to-coarse step and is
    built from the mixed solution instead.

    `ends` is an optional pair of polylines, the boundaries the two ends of the band run along. Without it each row
    end is offset along its own end segment's normal, and a corner close to the end tilts that normal a little more
    on every row: on this part the first tangent break sits three cells from the end of the weld, and by the fifth
    row the band had walked 19 mm past the end of the plate. With `ends` the first and last node of every row are
    projected back onto those boundaries, so the band ends where the part does.

    Returns (mesh, zones, info)."""
    m, zones = Mesh(), {}
    cur = list(root)
    ids = [m.node(*p) for p in cur]
    info = {"rows": [], "root_nodes": len(ids), "root_row": list(ids), "row_ids": []}
    for dh, target, name in rows:
        q = offset_row(cur, dh, side)
        if ends is not None:
            q[0], q[-1] = project_point(ends[0], q[0]), project_point(ends[1], q[-1])
        if name == "transition":
            n_fine = len(q) - 1
            n_coarse = _coarse_count(n_fine, target, _arc(q))
            blocks = transition_blocks(n_fine, n_coarse)
            up = [q[k] for k in _cuts(blocks, len(q))]
        else:
            blocks, up = plan_row(q, target)
        up_ids = [m.node(*p) for p in up]
        ladder(m, ids, up_ids, blocks, split, name, zones, flip=side < 0)
        info["rows"].append({"name": name, "height_mm": dh, "target_mm": target,
                             "cells_below": len(ids) - 1, "cells_above": len(up_ids) - 1,
                             "blocks": {str(b): blocks.count(b) for b in set(blocks)}})
        cur, ids = up, up_ids
        info["row_ids"].append(list(ids))
    info["top_nodes"] = len(ids)
    info["top_row"] = list(ids)
    return m, zones, info


def _arc(pts):
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) for i in range(len(pts) - 1))


def _cuts(blocks, n):
    out, i = [0], 0
    for nl, _ in blocks:
        i += nl
        out.append(i)
    return out


def _coarse_count(n_fine, coarse_size, length):
    """How many coarse edges the transition row ends with: near the target size, and reachable in one row."""
    best = None
    for nc in range(max(1, int(n_fine / 3.0)), n_fine // 2 + 1):
        n2, n3 = 3 * nc - n_fine, n_fine - 2 * nc
        if n2 < 0 or n3 < 0 or n2 % 2:
            continue
        err = abs(length / float(nc) - coarse_size)
        if best is None or err < best[0]:
            best = (err, nc)
    if best is None:
        raise ValueError("%d fine edges cannot be coarsened in one row to about %.2f mm" % (n_fine, coarse_size))
    return best[1]


# ---------------------------------------------------------------- self intersection
def self_intersections(m):
    """Pairs of boundary edges that cross. A band that folded over a corner shows up here even when every element
    on its own is still valid, which is exactly what a normal offset across a corner does."""
    es = [(m.nodes[a], m.nodes[b], a, b) for a, b in boundary_edges(m)]
    if not es:
        return []
    cell = max(4.0 * max(math.hypot(p[0] - q[0], p[1] - q[1]) for p, q, _, _ in es), 1e-9)
    grid = {}
    for k, (p, q, _, _) in enumerate(es):
        for gx in range(int(min(p[0], q[0]) // cell), int(max(p[0], q[0]) // cell) + 1):
            for gy in range(int(min(p[1], q[1]) // cell), int(max(p[1], q[1]) // cell) + 1):
                grid.setdefault((gx, gy), []).append(k)
    out = set()
    for ks in grid.values():
        for x in range(len(ks)):
            for y in range(x + 1, len(ks)):
                a, b = es[ks[x]], es[ks[y]]
                if {a[2], a[3]} & {b[2], b[3]}:
                    continue
                if _crosses(a[0], a[1], b[0], b[1]):
                    out.add((min(ks[x], ks[y]), max(ks[x], ks[y])))
    return sorted(out)


def _side(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _crosses(p1, p2, p3, p4):
    d1, d2 = _side(p3, p4, p1), _side(p3, p4, p2)
    d3, d4 = _side(p1, p2, p3), _side(p1, p2, p4)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


# ---------------------------------------------------------------- smoothing
def smooth(m, passes=20, relax=0.5, slide=(), slide_tol=1e-6):
    """Laplacian smoothing of the interior nodes only, guarded.

    Every boundary node is pinned, so the weld root keeps the exact coordinates the geometry gave it and the outer
    edge of the band keeps meeting whatever is outside it. A node is moved only when the move does not lower the
    worst corner Jacobian among the elements around it, which makes the smoothing safe to run on a mesh that is
    already valid: it can improve the corner blocks and cannot spoil them.

    `slide` names boundary curves along which the nodes may move instead of being pinned. The node stays on the
    curve, so the shape of the part does not change; only the spacing along it does. That is what lets a row that
    had to be squeezed against the end of a plate spread itself out again. A node is allowed to slide only if it is
    already on the curve and is not one of its two ends, so a corner stays a corner."""
    from .quality import metrics
    ring = {}
    for q, ids in m.quads.items():
        for k, i in enumerate(ids):
            ring.setdefault(i, []).append(q)
    nbr = {}
    for ids in m.quads.values():
        for k in range(4):
            nbr.setdefault(ids[k], set()).add(ids[(k + 1) % 4])
            nbr.setdefault(ids[k], set()).add(ids[(k + 3) % 4])
    bnd = set()
    for a, b in boundary_edges(m):
        bnd.add(a)
        bnd.add(b)
    free = [i for i in m.nodes if i not in bnd]
    onto = {}
    for curve in slide:
        ends = (curve[0], curve[-1])
        for i in bnd:
            p = m.nodes[i]
            if any(abs(p[0] - e[0]) <= slide_tol and abs(p[1] - e[1]) <= slide_tol for e in ends):
                continue
            q = project_point(curve, p)
            if abs(q[0] - p[0]) <= slide_tol and abs(q[1] - p[1]) <= slide_tol:
                onto[i] = curve
    free = free + sorted(onto)
    moved = 0
    for _ in range(passes):
        for i in free:
            old = m.nodes[i]
            was = min(metrics(m.corners(q))["corner_jacobian"] for q in ring[i])
            cx = sum(m.nodes[j][0] for j in nbr[i]) / len(nbr[i])
            cy = sum(m.nodes[j][1] for j in nbr[i]) / len(nbr[i])
            target = (old[0] + relax * (cx - old[0]), old[1] + relax * (cy - old[1]))
            m.nodes[i] = project_point(onto[i], target) if i in onto else target
            now = min(metrics(m.corners(q))["corner_jacobian"] for q in ring[i])
            if now < was:
                m.nodes[i] = old
            else:
                moved += 1
    return {"free_nodes": len(free) - len(onto), "sliding_nodes": len(onto),
            "pinned_nodes": len(bnd) - len(onto), "accepted_moves": moved, "passes": passes}
