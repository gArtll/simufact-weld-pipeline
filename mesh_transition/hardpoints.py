# -*- coding: utf-8 -*-
"""Hard points: physical positions that must be mesh nodes, whatever the mesh.

A boundary condition written as a node id belongs to one mesh. Remeshing moves every id, and "the node nearest to
where the old one was" drifts by up to half a cell -- millimetres on a real part. A hard point is the position itself.
The mesher is asked to put a node there, and it does so by moving the node that is already closest:

* an interior node moves freely to the point;
* a node on the boundary moves only along its own boundary curve, and only if the point lies on that curve, so the
  outline of the part does not change;

and after every move the elements around the node are checked again. A move that would leave an element below the
quality limit is refused, loudly: the caller learns which point could not be honoured and why, instead of getting a
constraint that quietly sits somewhere else."""
import math

from .block import project_point
from .mesh import boundary_edges
from .quality import metrics


def _d(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


class HardPointError(ValueError):
    pass


def relocate(m, targets, boundary_curves=(), tol=1e-6, min_cj=0.3, max_move_fraction=0.75, on_curve_tol=0.01):
    """Move one node onto each target (u, v). Returns one record per target.

    `boundary_curves` are the polylines the boundary nodes lie on; a boundary node may only slide along the curve it
    is on. `max_move_fraction` bounds the move by the shortest edge at the node, so a point is never honoured by
    dragging a node across its neighbours. A point within `on_curve_tol` of the boundary curve counts as on it and the
    node goes to its projection onto the curve, so the outline stays the CAD outline; 0.01 mm covers a position
    written in an eight-character Nastran field."""
    ring, nbr = {}, {}
    for q, ids in m.quads.items():
        for k, i in enumerate(ids):
            ring.setdefault(i, []).append(q)
            nbr.setdefault(i, set()).update((ids[(k + 1) % 4], ids[(k + 3) % 4]))
    bnd = {i for a, b in boundary_edges(m) for i in (a, b)}
    taken, out = set(), []
    grid = {}
    cell = 20.0
    for i, p in m.nodes.items():
        grid.setdefault((int(p[0] // cell), int(p[1] // cell)), []).append(i)

    def near(t, r=2):
        gx, gy = int(t[0] // cell), int(t[1] // cell)
        best = None
        for dx in range(-r, r + 1):
            for dy in range(-r, r + 1):
                for i in grid.get((gx + dx, gy + dy), ()):
                    d = _d(m.nodes[i], t)
                    if i not in taken and (best is None or d < best[0]):
                        best = (d, i)
        return best

    for t in targets:
        t = (float(t[0]), float(t[1]))
        hit = near(t)
        if hit is None:
            hit = near(t, 10)
        if hit is None:
            raise HardPointError("no mesh node near %r" % (t,))
        d, i = hit
        rec = {"target": t, "node": i, "from": m.nodes[i], "move_mm": round(d, 6)}
        if d <= tol:
            taken.add(i)
            out.append(dict(rec, how="already a node"))
            continue
        if i in bnd:
            on = [c for c in boundary_curves if _d(project_point(c, m.nodes[i]), m.nodes[i]) <= 1e-6]
            if not on:
                raise HardPointError("node %d nearest to %r is on the boundary but on no given curve" % (i, t))
            q = project_point(on[0], t)
            if _d(q, t) > on_curve_tol:
                raise HardPointError("point %r is %.4f mm off the boundary curve its nearest node lies on; it is "
                                     "not on the part surface this body was meshed from" % (t, _d(q, t)))
            how = "slid along the boundary"
            rec["off_curve_mm"] = round(_d(q, t), 6)
            t = q
        else:
            how = "moved"
        short = min(_d(m.nodes[i], m.nodes[j]) for j in nbr[i])
        d = _d(m.nodes[i], t)
        if d > max_move_fraction * short:
            raise HardPointError("honouring %r would move node %d by %.3f mm, more than %.2f of its shortest edge "
                                 "(%.3f mm); the mesh is too coarse here to hold this point" % (t, i, d, max_move_fraction, short))
        old = m.nodes[i]
        m.nodes[i] = t
        worst = min(metrics(m.corners(q))["corner_jacobian"] for q in ring[i])
        if worst < min_cj:
            m.nodes[i] = old
            raise HardPointError("moving node %d onto %r leaves an element at corner Jacobian %.3f (< %.2f)"
                                 % (i, t, worst, min_cj))
        taken.add(i)
        out.append(dict(rec, how=how, worst_cj_after=round(worst, 4)))
    return out
