# -*- coding: utf-8 -*-
"""Shape metrics for 2D quadrilaterals.

**These are this project's own definitions.** They are NOT HyperMesh's, and none of them is called a HyperMesh
metric until the definitions have been compared against HyperMesh's documented ones on the same element. In
particular `corner_jacobian` below is the 2D form of the metric already used in `hm/meshio.py`; whether it agrees
with what HyperMesh calls Jacobian, or with what it calls scaled Jacobian, is an open question, so the name says
what it computes and nothing more.

corner_jacobian   at each corner, the determinant of the two unit edge vectors leaving it, i.e. the sine of the
                  corner angle; the element value is the minimum over its four corners. 1 for a square, 0 for a
                  corner folded flat, negative for a corner turned inside out.
skew_deg          the largest deviation of a corner angle from 90 degrees.
aspect_ratio      longest edge / shortest edge.
min_edge          shortest edge length.
area              signed area; negative means the element is wound the wrong way."""
import math


def _unit(dx, dy):
    L = math.hypot(dx, dy)
    return (0.0, 0.0) if L == 0 else (dx / L, dy / L)


def corner_values(p):
    """Per corner of a counter-clockwise element: (sine of the interior angle, interior angle in degrees).

    At corner k the interior angle is swept from the next corner to the previous one, counter-clockwise. Its sine is
    the determinant of the two unit edge vectors in that order: positive for a convex corner, 0 for a flat one,
    negative once the corner has turned past 180 degrees."""
    out = []
    for k in range(4):
        ax, ay = p[(k + 1) % 4][0] - p[k][0], p[(k + 1) % 4][1] - p[k][1]    # towards the next corner
        bx, by = p[(k - 1) % 4][0] - p[k][0], p[(k - 1) % 4][1] - p[k][1]    # towards the previous corner
        (ux, uy), (vx, vy) = _unit(ax, ay), _unit(bx, by)
        cross, dot = ux * vy - uy * vx, ux * vx + uy * vy
        ang = math.degrees(math.atan2(cross, dot))
        out.append((cross, ang + 360.0 if ang < 0 else ang))
    return out


def metrics(p):
    """All metrics of one quadrilateral given its four corner coordinates, counter-clockwise."""
    cv = corner_values(p)
    edges = [math.hypot(p[(k + 1) % 4][0] - p[k][0], p[(k + 1) % 4][1] - p[k][1]) for k in range(4)]
    area = 0.5 * sum(p[k][0] * p[(k + 1) % 4][1] - p[(k + 1) % 4][0] * p[k][1] for k in range(4))
    return {"corner_jacobian": min(s for s, _ in cv),
            "skew_deg": max(abs(90.0 - a) for _, a in cv),
            "max_angle_deg": max(a for _, a in cv),
            "min_angle_deg": min(a for _, a in cv),
            "aspect_ratio": (max(edges) / min(edges)) if min(edges) > 0 else float("inf"),
            "min_edge": min(edges), "area": area}


def quality_rows(m, zone=None):
    """One row per element: (element id, zone, metrics)."""
    return [(q, (zone or {}).get(q, ""), metrics(m.corners(q))) for q in sorted(m.quads)]


def summary(m, elements=None):
    rows = [metrics(m.corners(q)) for q in (elements if elements is not None else sorted(m.quads))]
    if not rows:
        return {}
    cj = [r["corner_jacobian"] for r in rows]
    return {"elements": len(rows),
            "min_corner_jacobian": min(cj), "mean_corner_jacobian": sum(cj) / len(cj),
            "cj_below_0.7": sum(1 for v in cj if v < 0.7), "cj_below_0.5": sum(1 for v in cj if v < 0.5),
            "max_skew_deg": max(r["skew_deg"] for r in rows),
            "mean_skew_deg": sum(r["skew_deg"] for r in rows) / len(rows),
            "max_aspect_ratio": max(r["aspect_ratio"] for r in rows),
            "min_edge": min(r["min_edge"] for r in rows),
            "min_area": min(r["area"] for r in rows),
            "zero_or_negative_area": sum(1 for r in rows if r["area"] <= 1e-12)}


def worst_element(m):
    """The element with the smallest corner_jacobian; the one the drawing marks."""
    return min(sorted(m.quads), key=lambda q: metrics(m.corners(q))["corner_jacobian"])


def quality_table(m, zone=None):
    """Per-element table as text, in the order the plan asks for."""
    head = "%4s %-12s %10s %8s %8s %9s %10s" % ("elem", "zone", "cornerJac", "skew", "aspect", "minEdge", "area")
    lines = [head, "-" * len(head)]
    for q, z, r in quality_rows(m, zone):
        lines.append("%4d %-12s %10.4f %8.2f %8.3f %9.4f %10.4f" % (
            q, z, r["corner_jacobian"], r["skew_deg"], r["aspect_ratio"], r["min_edge"], r["area"]))
    return "\n".join(lines)


def summary_table(s):
    keys = ["elements", "min_corner_jacobian", "mean_corner_jacobian", "cj_below_0.7", "cj_below_0.5",
            "max_skew_deg", "mean_skew_deg", "max_aspect_ratio", "min_edge", "min_area", "zero_or_negative_area"]
    return "\n".join("%-22s %s" % (k, ("%.4f" % s[k]) if isinstance(s[k], float) else s[k]) for k in keys if k in s)
