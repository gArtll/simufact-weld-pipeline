# -*- coding: utf-8 -*-
"""Weld root line from the web weld edge of the base mesh (R8: points lie on the web face).
weld edge = boundary edges of the web face X=--face-x whose two nodes are both within --edge-dist of a plate node;
longest chain, ordered by increasing z.
  --out-full  : whole weld-edge node polyline (input for mesh_bead.tcl)
  --out-short : --n points resampled by arc length between the edge nodes nearest the first/last active point of
                --window-csv (root line of the welded window; informational, the heat path uses the outer line, R14)
Self-check: |x - face| < 1e-6 mm, short points on the edge polyline < 1e-6 mm, CSVs end with newline (hard rule 8)."""
import argparse
import io
import json
import math
import os
import sys

import numpy as np
from scipy.spatial import cKDTree

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from common.bdf import read_bdf, read_weldline_csv, csv_ends_with_newline  # noqa: E402
from prep.wl_geom import plane_quads_dir, plane_quads_axis, boundary_chain, chain_order, resample  # noqa: E402
from prep.frame import LEGACY_ALONG, LEGACY_NORMAL, check_frame, coord, fmt, parse_vec  # noqa: E402

TOL = 1e-6


def seg_dist(q, A, B):
    q, A, B = map(np.asarray, (q, A, B))
    ab = B - A
    t = 0.0 if not ab.dot(ab) else min(1.0, max(0.0, (q - A).dot(ab) / ab.dot(ab)))
    return float(np.linalg.norm(q - (A + t * ab)))


def write_csv(path, pts, title):
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("# %s\n# Length unit: Millimeter [mm]\n#\n" % title)
        f.write("# Orientation: global orientation;x-coordinate;y-coordinate;z-coordinate\n0;1;0;0\n#\n")
        f.write("# order;activity;x-coordinate;y-coordinate;z-coordinate\n")
        for k, q in enumerate(pts, 1):
            f.write("%d;true;%.9f;%.9f;%.9f\n" % (k, q[0], q[1], q[2]))
    return csv_ends_with_newline(path)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--web", required=True)
    p.add_argument("--plate", required=True)
    p.add_argument("--face-x", type=float, required=True, help="web face position along --normal (named x for the "
                                                                "legacy frame, where normal = +x)")
    p.add_argument("--normal", default=fmt(LEGACY_NORMAL), help="web normal of the local joint frame (prep/frame.py)")
    p.add_argument("--along", default=fmt(LEGACY_ALONG), help="weld direction of the local joint frame")
    p.add_argument("--edge-mode", choices=("planar", "closed_circular"), default="planar")
    p.add_argument("--axis", choices=("x", "y", "z"), default="z")
    p.add_argument("--axis-value", type=float, default=0.0)
    p.add_argument("--center", default="0,0,0")
    p.add_argument("--radius", type=float)
    p.add_argument("--closed", action="store_true")
    p.add_argument("--label", required=True)
    p.add_argument("--window-csv", required=True)
    p.add_argument("--out-full", required=True)
    p.add_argument("--out-short", required=True)
    p.add_argument("--json", required=True)
    p.add_argument("--n", type=int, default=23)
    p.add_argument("--edge-dist", type=float, default=5.0)
    a = p.parse_args()

    G, E, _ = read_bdf(a.web)
    GP, _, _ = read_bdf(a.plate)
    tree_pl = cKDTree(np.array(list(GP.values())))
    X = a.face_x
    normal, along = parse_vec(a.normal), parse_vec(a.along)
    if a.edge_mode == "planar":
        check_frame(normal, along)
        quads = plane_quads_dir(G, E, normal, X, tol=TOL)
    else:
        quads = plane_quads_axis(G, E, a.axis, a.axis_value, tol=TOL)
    bnd = boundary_chain(quads)
    if a.edge_mode == "planar":
        near = {n for e in bnd for n in e if tree_pl.query(G[n])[0] < a.edge_dist}
        edge = [e for e in bnd if e[0] in near and e[1] in near]
        chains = sorted(chain_order(edge), key=len, reverse=True)
    else:
        center = tuple(float(x) for x in a.center.split(","))
        ai = {"x": 0, "y": 1, "z": 2}[a.axis]
        radial = lambda q: math.sqrt(sum((q[k] - center[k]) ** 2 for k in range(3) if k != ai))
        chains = chain_order(bnd, close=True)
        chains.sort(key=lambda ch_: abs(sum(radial(G[n]) for n in ch_[:-1]) / max(1, len(ch_) - 1) - a.radius))
    if not chains:
        sys.exit("!! %s: no weld-edge chain on X=%s" % (a.label, X))
    ch = chains[0]
    if a.edge_mode == "planar" and coord(G[ch[0]], along) > coord(G[ch[-1]], along):
        ch = ch[::-1]
    full = [G[n] for n in ch]
    full_len = sum(math.dist(full[k], full[k + 1]) for k in range(len(full) - 1))
    win = read_weldline_csv(a.window_csv)
    tch = cKDTree(np.array(full))
    i0, i1 = int(tch.query(win[0])[1]), int(tch.query(win[-1])[1])
    if a.closed:
        ring = full[:-1] if math.dist(full[0], full[-1]) < TOL else full
        i0 %= len(ring); i1 %= len(ring)
        if len(win) > 1:
            j = int(cKDTree(np.array(ring)).query(win[1])[1])
            forward = (j - i0) % len(ring) <= (i0 - j) % len(ring)
        else:
            forward = True
        seq = ring if forward else ring[::-1]
        start = int(cKDTree(np.array(seq)).query(win[0])[1])
        seq = seq[start:] + seq[:start]
        end = int(cKDTree(np.array(seq)).query(win[-1])[1])
        if math.dist(win[0], win[-1]) < a.edge_dist:
            end = len(seq)
        poly = seq[:end + 1]
        if math.dist(poly[0], poly[-1]) >= TOL and end == len(seq):
            poly.append(poly[0])
        full = seq + [seq[0]]
        full_len = sum(math.dist(full[k], full[k + 1]) for k in range(len(full) - 1))
    else:
        poly = full[i0:i1 + 1]
    short, short_len = resample(poly, a.n)
    if a.edge_mode == "planar":
        surface_dev = max(abs(coord(q, normal) - X) for q in list(full) + list(short))
    else:
        center = tuple(float(x) for x in a.center.split(",")); ai = {"x": 0, "y": 1, "z": 2}[a.axis]
        radial_deviation = max(abs(math.sqrt(sum((q[k] - center[k]) ** 2 for k in range(3) if k != ai)) - a.radius) for q in list(full) + list(short))
        surface_dev = max(abs(q[ai] - a.axis_value) for q in list(full) + list(short))
    pdev = max(min(seg_dist(q, poly[k], poly[k + 1]) for k in range(len(poly) - 1)) for q in short)
    nl1 = write_csv(a.out_full, full, "weldsim prep wl_v2 %s full weld-edge polyline, web face X=%.4f, %d nodes, %.4f mm" % (a.label, X, len(full), full_len))
    nl2 = write_csv(a.out_short, short, "weldsim prep wl_v2 %s root line of window, %d points" % (a.label, a.n))
    ok = surface_dev < TOL and pdev < TOL and nl1 and nl2
    res = {"label": a.label, "edge_mode": a.edge_mode, "closed": a.closed, "face_x_mm": X, "edge_dist_mm": a.edge_dist, "chains_found": [len(c) for c in chains],
           "full_nodes": len(full), "full_len_mm": full_len, "short_len_mm": short_len, "short_points": a.n,
           "R8_max_surface_deviation_mm": surface_dev, "R8_max_abs_x_minus_face_mm": surface_dev, "max_short_dist_to_edge_mm": pdev, "csv_newline": bool(nl1 and nl2),
           "self_check_ok": bool(ok)}
    if a.edge_mode == "closed_circular":
        res["radius_mesh_deviation_mm"] = radial_deviation
    else:
        res["frame"] = {"normal": list(normal), "along": list(along)}
    io.open(a.json, "w", encoding="utf-8").write(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
