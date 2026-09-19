# -*- coding: utf-8 -*-
"""Bodies, faces and surfaces of a STEP file, enough to tell what the parts are and where they touch.

A face is represented by points sampled on its boundary edges and by an evaluator of its underlying surface (plane,
cylinder, non-rational B-spline). Trimming is ignored when measuring the distance of a point to a surface: the
question asked here is always "does this point lie on that face's surface", and the points asked about come from the
boundary of a face that touches it."""
import math

import numpy as np

from mesh_transition.step import de_boor, direction, face_edges, knot_vector, parse, point, sample_edge

EDGE_SAMPLES = 60


def _unit(v):
    v = np.asarray(v, float)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


class Surface(object):
    """Distance from points to an unbounded surface, and a normal where it is meaningful."""

    def __init__(self, ents, ref):
        self.kind, a = ents[ref][0], ents[ref][1]
        self.normal = None
        if self.kind == "PLANE":
            ax = ents[a[1]][1]
            self.origin = np.array(point(ents, ax[1]))
            self.normal = _unit(direction(ents, ax[2]))
        elif self.kind == "CYLINDRICAL_SURFACE":
            ax = ents[a[1]][1]
            self.origin = np.array(point(ents, ax[1]))
            self.axis = _unit(direction(ents, ax[2]))
            self.radius = float(a[2])
        elif self.kind == "B_SPLINE_SURFACE_WITH_KNOTS":
            du, dv = int(a[1]), int(a[2])
            ctrl = [[point(ents, r) for r in row] for row in a[3]]
            ku = knot_vector([float(x) for x in a[10]], [int(x) for x in a[8]])
            kv = knot_vector([float(x) for x in a[11]], [int(x) for x in a[9]])
            # sample densely enough that the chord error stays far below the contact tolerance
            nu = max(48, 10 * len(ctrl))
            nv = max(4, 4 * len(ctrl[0]))
            us = np.linspace(ku[du], ku[len(ctrl)], nu)
            vs = np.linspace(kv[dv], kv[len(ctrl[0])], nv)
            cols = [[de_boor(dv, row, kv, v) for row in ctrl] for v in vs]      # per v, points along u-control
            grid = np.array([[de_boor(du, col, ku, u) for u in us] for col in cols])   # (nv, nu, 3)
            self.grid = grid
            from scipy.spatial import cKDTree
            self.nv, self.nu = nv, nu
            self.tree = cKDTree(grid.reshape(-1, 3))
        else:
            self.kind = "UNSUPPORTED:" + self.kind

    def distance(self, pts):
        pts = np.asarray(pts, float)
        if self.kind == "PLANE":
            return np.abs((pts - self.origin) @ self.normal)
        if self.kind == "CYLINDRICAL_SURFACE":
            w = pts - self.origin
            radial = w - np.outer(w @ self.axis, self.axis)
            return np.abs(np.linalg.norm(radial, axis=1) - self.radius)
        if self.kind == "B_SPLINE_SURFACE_WITH_KNOTS":
            # nearest grid vertex, then only the triangles of the cells around it
            _, idx = self.tree.query(pts)
            out = np.empty(len(pts))
            for n, (p, k) in enumerate(zip(pts, idx)):
                j, i = divmod(int(k), self.nu)
                tris = []
                for jj in (j - 1, j):
                    for ii in (i - 1, i):
                        if 0 <= jj < self.nv - 1 and 0 <= ii < self.nu - 1:
                            g = self.grid
                            p0, p1, p2, p3 = g[jj, ii], g[jj, ii + 1], g[jj + 1, ii + 1], g[jj + 1, ii]
                            tris += [(p0, p1, p2), (p0, p2, p3)]
                out[n] = _min_tri_dist(p, np.array(tris))
            return out
        return np.full(len(pts), np.inf)


def _min_tri_dist(p, tris):
    """Distance from a point to the nearest of many triangles (vectorised over the triangles)."""
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    ab, ac, ap = b - a, c - a, p - a
    d1, d2 = np.einsum("ij,ij->i", ab, ap), np.einsum("ij,ij->i", ac, ap)
    n = np.cross(ab, ac)
    nn = np.einsum("ij,ij->i", n, n)
    # barycentric projection onto the plane, clamped to the triangle by falling back to edge distances
    abab, acac, abac = np.einsum("ij,ij->i", ab, ab), np.einsum("ij,ij->i", ac, ac), np.einsum("ij,ij->i", ab, ac)
    den = abab * acac - abac ** 2
    den[den == 0] = 1e-30
    v = (acac * d1 - abac * d2) / den
    w = (abab * d2 - abac * d1) / den
    inside = (v >= 0) & (w >= 0) & (v + w <= 1)
    plane_d = np.where(nn > 0, np.abs(np.einsum("ij,ij->i", n, ap)) / np.sqrt(np.maximum(nn, 1e-30)), np.inf)
    best = np.min(np.where(inside, plane_d, np.inf)) if inside.any() else np.inf

    def seg(x, y):
        t = np.clip(np.einsum("ij,ij->i", p - x, y - x) / np.maximum(np.einsum("ij,ij->i", y - x, y - x), 1e-30), 0, 1)
        return np.linalg.norm(x + (y - x) * t[:, None] - p, axis=1)
    return float(min(best, seg(a, b).min(), seg(b, c).min(), seg(c, a).min()))


class Face(object):
    def __init__(self, ents, fid):
        self.id = fid
        a = ents[fid][1]
        self.surface = Surface(ents, a[2])
        self.edges = face_edges(ents, fid)
        pts, self.edge_polys = [], []
        for e in self.edges:
            try:
                poly = sample_edge(ents, e, EDGE_SAMPLES)
            except (ValueError, TypeError):
                poly = [e["p1"], e["p2"]]
            pts += poly
            self.edge_polys.append(np.array(poly, float))
        self.points = np.array(pts, float)
        self.lo, self.hi = self.points.min(0), self.points.max(0)
        self.extent = float(np.linalg.norm(self.hi - self.lo))
        self.edge_kinds = sorted({e["kind"] for e in self.edges})

    @property
    def kind(self):
        return self.surface.kind


class Body(object):
    def __init__(self, ents, bid, name, face_ids):
        self.id, self.name = bid, name
        self.faces = [Face(ents, f) for f in face_ids]
        pts = np.vstack([f.points for f in self.faces])
        self.lo, self.hi = pts.min(0), pts.max(0)
        self.dims = sorted((self.hi - self.lo).tolist())


def bodies(step_path):
    """(ents, [Body]) for every MANIFOLD_SOLID_BREP in the file."""
    ents = parse(step_path)
    out = []
    for i, (k, a) in sorted(ents.items()):
        if k == "MANIFOLD_SOLID_BREP":
            shell = ents[a[1]]
            out.append(Body(ents, i, a[0], shell[1][1]))
    return ents, out


def lies_on(points, surface, tol):
    """Fraction of points within `tol` of the surface."""
    if len(points) == 0:
        return 0.0
    d = surface.distance(points)
    return float(np.mean(d <= tol))
