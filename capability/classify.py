# -*- coding: utf-8 -*-
"""Joint features from the geometry alone.

For every body: what kind of part it is (plate, curved plate, tube, solid), its wall thickness, which of its faces
are the two large faces and which are the thin ones, and whether it is a straight extrusion. For every pair of
bodies: which faces of one lie on which faces of the other. The joint type follows from which kinds of faces touch:

    thin face of A on a large face of B            T joint (A stands on B) -> fillet welds along the two long edges
    thin face of A on a thin face of B             butt joint
    large face of A on a large face of B           lap joint
    end annulus of tube A on a large face of B     tube on plate -> closed circumferential fillet
    end of tube A on the wall of tube B            tube T joint

Nothing here is decided by a case name. Features the classifier is unsure of are reported as such, not guessed."""
import math

import numpy as np

from .geometry import bodies, lies_on

CONTACT_TOL_MM = 0.05          # a boundary point this close to another surface is on it
ON_FRACTION = 0.9              # share of a face's boundary points that must lie on the other face
THIN_RATIO = 0.25              # thickness / second dimension below this = a wall (plate, tube, profile)
SECTION_TOL_MM = 0.05          # end profiles closer than this are one extrusion (= plate.PlateParams.end_profile_tol)


def _pairs_distance(fa, fb):
    """Median distance from the boundary points of fa to the surface of fb."""
    return float(np.median(fb.surface.distance(fa.points)))


def body_features(b):
    dims = b.dims
    large = sorted(b.faces, key=lambda f: -f.extent)
    # thickness: the smallest median distance between two large faces that are not the same face
    cands = [f for f in large if f.extent > 0.3 * dims[-1]]
    t, pair = None, None
    for i in range(len(cands)):
        for j in range(i + 1, len(cands)):
            d = _pairs_distance(cands[i], cands[j])
            if d > 1e-3 and (t is None or d < t):
                t, pair = d, (cands[i], cands[j])
    kinds = {f.kind for f in b.faces}
    cyl_share = sum(f.extent for f in b.faces if f.kind == "CYLINDRICAL_SURFACE") / max(sum(f.extent for f in b.faces), 1e-9)
    if t is not None and t < THIN_RATIO * dims[1]:
        big = [pair[0].id, pair[1].id]
        planar = all(f.kind == "PLANE" for f in pair)
        if cyl_share > 0.5 and all(f.kind == "CYLINDRICAL_SURFACE" for f in pair):
            kind = "tube"
        elif planar:
            kind = "plate"
        else:
            kind = "curved_plate"
    else:
        kind, big = ("tube" if cyl_share > 0.5 else "solid"), []
    axis_aligned, normal_axis, normal = None, None, None
    if kind == "plate":
        n = pair[0].surface.normal
        normal = [float(v) for v in n]
        axis_aligned = bool(np.max(np.abs(n)) > 1 - 1e-6)
        normal_axis = "xyz"[int(np.argmax(np.abs(n)))] if axis_aligned else None
    if t is not None and kind != "solid":
        big = [f.id for f in b.faces if not thin_face(f, t)]
    secs = sections(b)
    return {"body": b.id, "name": b.name, "kind": kind, "thickness_mm": None if t is None else round(t, 4),
            "large_faces": big, "surface_kinds": sorted(kinds), "bbox_mm": [round(x, 3) for x in dims],
            "axis_aligned": axis_aligned, "normal_axis": normal_axis, "normal": normal,
            "extrusion_axis": next((a for a, v in secs.items() if v["section"] == "equal"), None),
            "sections": secs, "centroid_mm": [float(v) for v in (b.lo + b.hi) / 2.0]}


def thin_face(f, t, tol=0.2):
    """A face that spans the wall: two of its edges are as long as the wall is thick (an end or edge strip), or it is
    a flat annulus whose two circles differ in radius by the wall thickness (a tube end)."""
    lens = sorted(math.dist(e["p1"], e["p2"]) if e["kind"] == "LINE" else _edge_len(e) for e in f.edges)
    if sum(1 for L in lens if abs(L - t) <= tol * t) >= 2:
        return True
    if f.kind == "PLANE" and len(f.edges) == 2 and all(e["kind"] == "CIRCLE" for e in f.edges):
        c = f.points.mean(0)
        r = sorted(np.linalg.norm(f.points - c, axis=1))
        return abs((r[-1] - r[0]) - t) <= tol * t
    return False


def _edge_len(e):
    return math.dist(e["p1"], e["p2"]) if e["p1"] != e["p2"] else float("inf")


def extrusion_axis(b):
    """Axis ('x'/'y'/'z') along which the body is a straight extrusion of one planar profile, or None.

    Two parallel planar faces normal to a coordinate axis with the same boundary (shifted along the axis) are the two
    ends of an extrusion. Different end outlines -- a plate whose width varies along its length -- are not."""
    return next((a for a, v in sections(b).items() if v["section"] == "equal"), None)


def _outline_gap(fa, fb, k, shift):
    """Largest distance from the boundary points of fa, moved by `shift` along axis k, to the boundary polylines of
    fb (point to segment, so the sampling density does not show up as a gap)."""
    segs = np.concatenate([np.stack([P[:-1], P[1:]], axis=1) for P in fb.edge_polys if len(P) > 1])
    a0, d = segs[:, 0], segs[:, 1] - segs[:, 0]
    L2 = np.maximum((d * d).sum(1), 1e-30)
    q = fa.points.copy()
    q[:, k] += shift
    worst = 0.0
    for p in q[:: max(1, len(q) // 400)]:
        t = np.clip(((p - a0) * d).sum(1) / L2, 0.0, 1.0)
        worst = max(worst, float(np.min(np.linalg.norm(a0 + d * t[:, None] - p, axis=1))))
    return worst


def sections(b):
    """Per coordinate axis: the two end faces normal to it, and whether the body between them is an equal-section
    extrusion or a variable-section sweep of one profile topology.

    `equal`: the end outlines coincide after the shift along the axis. `variable`: the same number of boundary edges
    but different outlines (a plate whose length or edge shape changes across its width); a mapped mesher can follow
    it, a sweep cannot. Axes without such a pair of end faces are absent."""
    planes = [f for f in b.faces if f.kind == "PLANE" and np.max(np.abs(f.surface.normal)) > 1 - 1e-6]
    out = {}
    for i in range(len(planes)):
        for j in range(i + 1, len(planes)):
            a, c = planes[i], planes[j]
            k = int(np.argmax(np.abs(a.surface.normal)))
            if int(np.argmax(np.abs(c.surface.normal))) != k:
                continue
            ka, kc = float(a.points[:, k].mean()), float(c.points[:, k].mean())
            if abs(kc - ka) < 1e-6 or len(a.edges) != len(c.edges):
                continue
            # end faces bound the body along k: nothing of the body lies beyond them
            ends = (b.lo[k], b.hi[k])
            if min(abs(ka - e) for e in ends) > 1e-3 or min(abs(kc - e) for e in ends) > 1e-3:
                continue
            gap = max(_outline_gap(a, c, k, kc - ka), _outline_gap(c, a, k, ka - kc))
            kind = "equal" if gap <= SECTION_TOL_MM else "variable"
            prev = out.get("xyz"[k])
            if prev is None or (prev["section"] == "variable" and kind == "equal"):
                lo_f, hi_f = (a, c) if ka < kc else (c, a)
                out["xyz"[k]] = {"section": kind, "end_faces": [lo_f.id, hi_f.id], "profile_gap_mm": round(gap, 4)}
    return out


def contacts(bs, feats):
    """Face pairs where one body's face lies on another body's surface."""
    out = []
    for A in bs:
        for B in bs:
            if A is B:
                continue
            for fa in A.faces:
                for fb in B.faces:
                    if fb.kind.startswith("UNSUPPORTED"):
                        continue
                    # quick reject: bounding boxes apart
                    if np.any(fa.lo > fb.hi + 1.0) or np.any(fb.lo > fa.hi + 1.0):
                        continue
                    if _touch(fa, fb):
                        out.append({"from": A.id, "face": fa.id, "on": B.id, "on_face": fb.id})
    return out


def _touch(fa, fb, tol=CONTACT_TOL_MM, overlap_mm=1.0):
    """Whether face fa lies on face fb over an area, not just along a line.

    Two parallel planes: coplanar, and their extents overlap by more than `overlap_mm` in both in-plane directions.
    That separates a lap (an area in common) from two coplanar faces side by side, such as the top faces of two
    butted plates, which meet only along a line. Otherwise: fa's boundary lies on fb's surface and inside fb's
    extent -- a thin face resting on a larger, possibly curved one."""
    na, nb = fa.surface.normal, fb.surface.normal
    if na is not None and nb is not None and abs(abs(float(na @ nb)) - 1.0) < 1e-6:
        if float(np.max(fb.surface.distance(fa.points))) > tol:
            return False
        k = int(np.argmax(np.abs(na)))
        for i in range(3):
            if i == k and abs(abs(na[k]) - 1.0) < 1e-6:
                continue
            if min(fa.hi[i], fb.hi[i]) - max(fa.lo[i], fb.lo[i]) <= overlap_mm:
                return False
        return True
    inside = np.all((fa.points >= fb.lo - 0.5) & (fa.points <= fb.hi + 0.5), axis=1)
    return inside.mean() >= ON_FRACTION and lies_on(fa.points[inside], fb.surface, tol) >= ON_FRACTION


def joint_type(fa_role, fb_role, ka, kb):
    if ka == "tube" and fb_role == "large" and kb in ("plate", "curved_plate", "solid"):
        return "tube_on_plate"
    if ka == "tube" and kb == "tube":
        return "tube_t_joint"
    if fa_role == "thin" and fb_role == "large":
        return "t_joint"
    if fa_role == "thin" and fb_role == "thin":
        return "butt"
    if fa_role == "large" and fb_role == "large":
        return "lap"
    return "unknown"


def classify(step_path):
    ents, bs = bodies(step_path)
    feats = {b.id: body_features(b) for b in bs}
    cs = contacts(bs, feats)
    faces = {f.id: (b, f) for b in bs for f in b.faces}
    role = lambda fid, bid: "large" if fid in feats[bid]["large_faces"] else "thin"
    joints = {}
    for c in cs:
        a, b = c["from"], c["on"]
        key = tuple(sorted((a, b)))
        ra, rb = role(c["face"], a), role(c["on_face"], b)
        jt = joint_type(ra, rb, feats[a]["kind"], feats[b]["kind"])
        if jt == "unknown":
            jt = joint_type(rb, ra, feats[b]["kind"], feats[a]["kind"])
            if jt != "unknown":
                a, b = b, a
        j = joints.setdefault(key, {"type": jt, "standing": a, "base": b, "contact_faces": []})
        if j["type"] == "unknown" and jt != "unknown":
            j.update(type=jt, standing=a, base=b)
        j["contact_faces"].append([c["face"], c["on_face"]])
    out = []
    for key, j in joints.items():
        A = next(x for x in bs if x.id == j["standing"])
        cf = sorted({f for f, _ in j["contact_faces"] if faces[f][0] is A})
        seams = _seams(A, feats[A.id], cf)
        base = feats[j["base"]]
        stand = feats[j["standing"]]
        frame = joint_frame(stand, seams)
        surface = "planar" if (stand["kind"] == "plate" and stand["axis_aligned"]) else \
            ("oblique" if stand["kind"] == "plate" else "curved")
        out.append({"type": j["type"], "standing": {"body": A.id, "name": A.name, "kind": stand["kind"]},
                    "base": {"body": j["base"], "name": base["name"], "kind": base["kind"]},
                    "contact_faces": j["contact_faces"], "seams": seams,
                    "sides_possible": 2 if j["type"] in ("t_joint", "tube_t_joint") else 1,
                    "standing_surface": surface, "base_surface": "planar" if base["kind"] == "plate" else "curved",
                    "groove": _groove(A, feats[A.id], cf) if j["type"] == "butt" else False,
                    "frame": frame})
    return {"step": step_path, "bodies": list(feats.values()), "contacts": cs, "joints": out,
            "counts": {"bodies": len(bs), "joints": len(out)}}


def _seams(A, feat, contact_faces):
    """The seam lines: edges shared by a contact face and one of the standing part's large faces."""
    large = [f for f in A.faces if f.id in feat["large_faces"]]
    seams = []
    for lf in large:
        segs = [e for e in lf.edges if any(e["edge"] in {x["edge"] for x in f.edges} for f in A.faces
                                           if f.id in contact_faces)]
        if not segs:
            continue
        L = sum(math.dist(e["p1"], e["p2"]) for e in segs)
        closed = len(segs) > 1 and _is_closed(segs)
        kinds = sorted({e["kind"] for e in segs})
        seams.append({"on_face": lf.id, "edges": len(segs), "chord_mm": round(L, 2),
                      "path": "closed" if closed else ("straight" if kinds == ["LINE"] else "curved"),
                      "edge_kinds": kinds, "geometry_ids": [e["geometry"] for e in segs],
                      "points_mm": [list(e["p1"]) for e in segs] + [list(e["p2"]) for e in segs]})
    return seams


def _unit(v):
    v = np.asarray(v, float)
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-12 else None


def _canon(v):
    """Sign convention for a direction with no natural sense: its largest component positive."""
    return v if v[int(np.argmax(np.abs(v)))] > 0 else -v


def joint_frame(stand, seams):
    """The local frame of a joint, from the geometry, in model coordinates:

      transverse  the standing part's thickness direction; bead sides `neg` / `pos` are along it
      tangent     the main direction of the seam (open seams)
      normal      from the base part into the standing part, perpendicular to both

    Nothing downstream assumes the joint sits in a particular global orientation: prep builds its bead sections in
    this frame. A direction the geometry does not define (curved standing part, closed seam) is None."""
    out = {"transverse": None, "tangent": None, "normal": None, "origin": None,
           "basis": "standing large-face normal; principal direction of the seam points; base -> standing centroid"}
    pts = np.array([p for s in seams for p in s["points_mm"]], float) if seams else np.zeros((0, 3))
    n = None
    if stand.get("normal") is not None:
        n = _canon(np.asarray(stand["normal"], float))
        out["transverse"] = [round(float(v), 12) + 0.0 for v in n]
    if len(pts) >= 2 and not any(s["path"] == "closed" for s in seams):
        c = pts.mean(0)
        t = _unit(np.linalg.svd(pts - c)[2][0])
        if t is not None and n is not None:
            t = _unit(t - n * float(t @ n))
        if t is not None:
            t = _canon(t)
            out["tangent"] = [round(float(v), 12) + 0.0 for v in t]
            out["origin"] = [round(float(v), 6) for v in c]
            up = np.asarray(stand["centroid_mm"], float) - c
            for d in (t, n):
                if d is not None:
                    up = up - d * float(up @ d)
            u = _unit(up)
            out["normal"] = None if u is None else [round(float(v), 12) + 0.0 for v in u]
    return out


def _is_closed(segs):
    ends = {}
    for e in segs:
        for p in (e["p1"], e["p2"]):
            k = tuple(round(v, 3) for v in p)
            ends[k] = ends.get(k, 0) + 1
    return all(v % 2 == 0 for v in ends.values())


def _groove(A, feat, contact_faces):
    """A butt contact narrower than the wall means a prepared edge (groove), not a square butt."""
    t = feat["thickness_mm"] or 0
    for f in A.faces:
        if f.id in contact_faces:
            span = sorted((f.hi - f.lo).tolist())
            if t and span[1] < 0.9 * t:
                return True
    return False
