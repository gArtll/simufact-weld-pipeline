# -*- coding: utf-8 -*-
"""Pure Python weld-bead mesh generator: same algorithm, same arithmetic order and same BDF text as prep/mesh_bead.tcl.
Used by weld_prep.py when config hmbatch is empty, so prep runs without HyperMesh.
Usage: python mesh_bead.py <mesh_bead_params.json>
params: web_bdf, plate_bdf, snap_tol, leg, spacing, section_frame (web_plane | tube_on_plate), edge_center [3],
        edge_axis, closed, beads [{label, side, face_x, root_full_csv, out_bdf}]
Sections at equal arc length along the root line; template of 7 nodes / 3 quads per section (Simufact quality 0);
node snapping of contact-face nodes (local 0, 1, 2, 5, 6) closer than snap_tol to a base node. Units mm.
Optional `frame: {normal, along}` (prep/frame.py): the web normal and the weld direction. Without it the legacy frame
normal = +x, along = +z applies, and the output is the same as the Tcl mesher's."""
import io
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prep.frame import LEGACY_ALONG, LEGACY_NORMAL, check_frame, coord  # noqa: E402

TEMPLATE = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (0.3943375, 0.3943375), (0.5, 0.5), (0.0, 0.4999992), (0.4999992, 0.0)]
QUADS = [(3, 4, 2, 5), (5, 0, 6, 3), (3, 6, 1, 4)]
BUCKET = 10.0


def P(s):
    print(s, flush=True)


def vsub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vadd(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vmul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def vdot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vcross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def vnorm(a):
    l = math.sqrt(vdot(a, a))
    if l == 0.0:
        raise ValueError("zero vector")
    return vmul(a, 1.0 / l)


def axis_vector(name):
    return {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0)}.get(name, (0.0, 0.0, 1.0))


def read_grid_star(path):
    nodes = []
    lines = io.open(path, errors="ignore").read().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line[:5] == "GRID*":
            cont = lines[i + 1] if i + 1 < len(lines) else ""
            nodes.append((float(line[40:56].strip()), float(line[56:72].strip()), float(cont[8:24].strip())))
            i += 1
        i += 1
    return nodes


def read_line_csv(path):
    pts = []
    for line in io.open(path, encoding="utf-8", errors="ignore"):
        q = line.strip().split(";")
        if len(q) != 5 or q[1] != "true":
            continue
        pts.append((float(q[2]), float(q[3]), float(q[4])))
    return pts


def build_index(pts, along=LEGACY_ALONG):
    idx = {}
    for p in pts:
        idx.setdefault(int(math.floor(coord(p, along) / BUCKET)), []).append(p)
    return idx


def at_s(pts, cum, s):
    n = len(pts)
    if s <= 0.0:
        return pts[0]
    if s >= cum[-1]:
        return pts[-1]
    lo, hi = 0, n - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if cum[mid] <= s:
            lo = mid
        else:
            hi = mid
    c0, c1 = cum[lo], cum[hi]
    u = (s - c0) / (c1 - c0) if c1 > c0 else 0.0
    return vadd(pts[lo], vmul(vsub(pts[hi], pts[lo]), u))


def into_web(root, t, idx, along=LEGACY_ALONG):
    b0 = int(math.floor(coord(root, along) / BUCKET))
    best, bd = 1.0e100, None
    for b in range(b0 - 2, b0 + 3):
        for q in idx.get(b, ()):
            d = vsub(q, root)
            l = math.sqrt(vdot(d, d))
            if l < 1.0 or l > 30.0:
                continue
            if abs(vdot(d, t)) > 0.5 * l:
                continue
            if l < best:
                best, bd = l, d
    return bd


def template_section(corners):
    (x0, y0, z0), (x1, y1, z1), (x2, y2, z2) = corners
    out = [(x0 + u * (x1 - x0) + v * (x2 - x0), y0 + u * (y1 - y0) + v * (y2 - y0), z0 + u * (z1 - z0) + v * (z2 - z0)) for u, v in TEMPLATE]
    return list(corners) + out[3:]


def hexa_det_sign(X):
    x0, y0, z0 = X[0]
    x1, y1, z1 = X[1]
    x3, y3, z3 = X[3]
    x4, y4, z4 = X[4]
    ax, ay, az = x1 - x0, y1 - y0, z1 - z0
    bx, by, bz = x3 - x0, y3 - y0, z3 - z0
    cx, cy, cz = x4 - x0, y4 - y0, z4 - z0
    return (ay * bz - az * by) * cx + (az * bx - ax * bz) * cy + (ax * by - ay * bx) * cz


def write_bead(path, label, sections, info, closed):
    with io.open(path, "w", encoding="ascii", newline="\r\n") as f:
        f.write("$ C4 weld_prep mesh_bead_c4.tcl scripted weld bead: %s\n" % label)
        f.write("$ Units: mm; %d sections; 7 nodes/section; 3 HEXA/span; %s\n" % (len(sections), info))
        f.write("SOL 101\nCEND\nBEGIN BULK\n")
        nid = 1
        for sec in sections:
            for x, y, z in sec:
                f.write("%-8s%16d%16s%16.9e%16.9e%-8s\n" % ("GRID*", nid, "", x, y, "*"))
                f.write("%-8s%16.9e\n" % ("*", z))
                nid += 1
        s0, s1 = sections[0], sections[1]
        X = [s0[k] for k in QUADS[1]] + [s1[k] for k in QUADS[1]]
        flip = 1 if hexa_det_sign(X) < 0.0 else 0
        eid = 1
        nspan = len(sections) if closed else len(sections) - 1
        for i in range(nspan):
            a = i * 7 + 1
            b = ((i + 1) % len(sections)) * 7 + 1
            for q in QUADS:
                ids = ([b + k for k in q] + [a + k for k in q]) if flip else ([a + k for k in q] + [b + k for k in q])
                f.write("%-8s%8d%8d%8d%8d%8d%8d%8d%8d%-8s\n" % (("CHEXA", eid, 1) + tuple(ids[:6]) + ("+",)))
                f.write("%-8s%8d%8d\n" % ("+", ids[6], ids[7]))
                eid += 1
        f.write("ENDDATA\n")
    P("WROTE %s: sections=%d nodes=%d CHEXA=%d winding_flip=%d" % (path, len(sections), nid - 1, eid - 1, flip))


def nearest_base(p, idx, along=LEGACY_ALONG):
    x, y, z = p
    b0 = int(math.floor(coord(p, along) / BUCKET))
    best, bp = 1.0e100, None
    for b in range(b0 - 1, b0 + 2):
        for q in idx.get(b, ()):
            a, c, d = q
            d2 = (x - a) * (x - a) + (y - c) * (y - c) + (z - d) * (z - d)
            if d2 < best:
                best, bp = d2, q
    return bp, math.sqrt(best)


def main(params_path):
    prm = json.load(io.open(params_path, encoding="utf-8"))
    LEG, SPACING, SNAP_TOL = float(prm["leg"]), float(prm["spacing"]), float(prm.get("snap_tol", 0.0))
    CLOSED, FRAME = bool(prm.get("closed")), prm.get("section_frame", "web_plane")
    EDGE_CENTER, EDGE_AXIS = tuple(float(v) for v in prm.get("edge_center", (0, 0, 0))), prm.get("edge_axis", "z")
    fr = prm.get("frame") or {}
    NORMAL = tuple(float(v) for v in fr.get("normal", LEGACY_NORMAL))
    ALONG = tuple(float(v) for v in fr.get("along", LEGACY_ALONG))
    if FRAME == "web_plane":
        check_frame(NORMAL, ALONG)
        if (NORMAL, ALONG) != (LEGACY_NORMAL, LEGACY_ALONG):
            P("frame normal=%r along=%r" % (NORMAL, ALONG))
    P("Reading web mesh %s ..." % prm["web_bdf"])
    web = read_grid_star(prm["web_bdf"])
    P("web nodes=%d leg=%r spacing=%r" % (len(web), LEG, SPACING))
    plate = read_grid_star(prm["plate_bdf"]) if prm.get("plate_bdf") else []
    bidx = build_index(web + plate, ALONG)
    P("base nodes for micro-snap: web=%d plate=%d SNAP_TOL=%r mm" % (len(web), len(plate), SNAP_TOL))
    for spec in prm["beads"]:
        label, side, facex = spec["label"], spec["side"], float(spec["face_x"])
        sgn = -1.0 if side == "neg" else 1.0
        pts = read_line_csv(spec["root_full_csv"])
        cum = [0.0]
        for k in range(1, len(pts)):
            d = vsub(pts[k], pts[k - 1])
            cum.append(cum[-1] + math.sqrt(vdot(d, d)))
        L = cum[-1]
        spans = int(math.floor(L / SPACING))
        ds = L / spans
        fidx = build_index([p for p in web if abs(coord(p, NORMAL) - facex) < 1.0e-6], ALONG)
        sections, nofallback, fallback, prevw, maxdev = [], 0, 0, None, 0.0
        laststation = spans - 1 if CLOSED else spans
        for i in range(laststation + 1):
            s = i * ds
            root = at_s(pts, cum, s)
            sa = 0.0 if s - 0.5 < 0.0 else s - 0.5
            sb = L if s + 0.5 > L else s + 0.5
            t = vnorm(vsub(at_s(pts, cum, sb), at_s(pts, cum, sa)))
            if FRAME == "tube_on_plate":
                w = axis_vector(EDGE_AXIS)
                radial = vsub(root, EDGE_CENTER)
                av = axis_vector(EDGE_AXIS)
                radial = vsub(radial, vmul(av, vdot(radial, av)))
                pl = vnorm(radial)
                nofallback += 1
            else:
                w = vnorm(vcross(NORMAL, t))
                dw = into_web(root, t, fidx, ALONG)
                if dw is not None:
                    if vdot(w, dw) < 0.0:
                        w = vmul(w, -1.0)
                    nofallback += 1
                else:
                    if prevw is not None and vdot(w, prevw) < 0.0:
                        w = vmul(w, -1.0)
                    fallback += 1
                pl = vnorm(vcross(t, w))
                if coord(pl, NORMAL) * sgn < 0.0:
                    pl = vmul(pl, -1.0)
            prevw = w
            webtoe = vadd(root, vmul(w, LEG))
            platetoe = vadd(root, vmul(pl, LEG))
            maxdev = max(maxdev, max(abs(coord(root, NORMAL) - facex), abs(coord(webtoe, NORMAL) - facex)))
            corners = [root, webtoe, platetoe] if side == "neg" else [root, platetoe, webtoe]
            sections.append(template_section(corners))
        nsnap, maxmove = 0, 0.0
        for i, sec in enumerate(sections):
            for k in (0, 1, 2, 5, 6):
                q, d = nearest_base(sec[k], bidx, ALONG)
                if 1.0e-9 < d < SNAP_TOL:
                    P("SNAPNODE %s section=%d local=%d z=%.3f move=%.6f mm" % (label, i, k, coord(sec[k], ALONG), d))
                    sec[k] = q
                    nsnap += 1
                    maxmove = max(maxmove, d)
        P("SNAP %s tol=%.4f mm snapped=%d max_move=%.6f mm" % (label, SNAP_TOL, nsnap, maxmove))
        P("BEAD %s side=%s face_x=%.4f root_len=%.4f mm spans=%d sections=%d spacing=%.6f mm web_dir_found=%d fallback=%d max|x-face| root/webtoe=%.3g"
          % (label, side, facex, L, spans, len(sections), ds, nofallback, fallback, maxdev))
        write_bead(spec["out_bdf"], label, sections, "root_len %.4f mm, spans %d, spacing %.6f mm, leg %.3f mm, side %s" % (L, spans, ds, LEG, side), CLOSED)
    P("DONE")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: mesh_bead.py <mesh_bead_params.json>")
    main(sys.argv[1])
