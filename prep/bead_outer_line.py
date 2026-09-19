# -*- coding: utf-8 -*-
"""Bead outer-surface weld line (R14) from a scripted bead bdf.
Section convention (7 nodes per section, ids 7*s+1..7*s+7): 1-based local 5 = hypotenuse midpoint = outer surface.
Window (R19): first/last active point of --clip-csv projected onto the outer polyline; --n points resampled by arc
length between the projections. No snapping to base nodes.
Self-checks: CSV ends with newline; optional --expect-len/--len-tol; optional --check-csv point-wise tolerance."""
import argparse
import io
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from common.bdf import read_bdf, read_weldline_csv, csv_ends_with_newline  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--bead", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--clip-csv", required=True)
    p.add_argument("--header-csv", required=True)
    p.add_argument("--check-csv", default=None)
    p.add_argument("--n", type=int, default=23)
    p.add_argument("--tol", type=float, default=0.01)
    p.add_argument("--local", type=int, default=5)
    p.add_argument("--expect-len", type=float, default=None)
    p.add_argument("--len-tol", type=float, default=0.01)
    p.add_argument("--json", default=None)
    p.add_argument("--closed", action="store_true")
    a = p.parse_args()

    G, _, _ = read_bdf(a.bead)
    nsec, rem = divmod(len(G), 7)
    if rem or sorted(G) != list(range(1, len(G) + 1)):
        sys.exit("!! bead node ids are not 1..7*nsec (count %d)" % len(G))
    P = np.array([G[7 * s + a.local] for s in range(nsec)])
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(seg)])

    def project(q):
        q = np.asarray(q)
        best = (1e30, 0.0)
        for i in range(len(P) - 1):
            ab = P[i + 1] - P[i]
            den = np.dot(ab, ab)
            t = float(np.clip(np.dot(q - P[i], ab) / den, 0, 1)) if den else 0.0
            d = float(np.linalg.norm(q - (P[i] + t * ab)))
            if d < best[0]:
                best = (d, cum[i] + t * seg[i])
        return best

    def at(s):
        i = int(np.clip(np.searchsorted(cum, s, side="right") - 1, 0, len(seg) - 1))
        t = (s - cum[i]) / seg[i] if seg[i] else 0.0
        return P[i] + t * (P[i + 1] - P[i])

    clip = read_weldline_csv(a.clip_csv)
    d0, s0 = project(clip[0])
    d1, s1 = project(clip[-1])
    if a.closed and np.linalg.norm(np.asarray(clip[0]) - np.asarray(clip[-1])) < a.tol:
        s1 = float(cum[-1])
    pts = np.array([at(s0 + (s1 - s0) * k / (a.n - 1)) for k in range(a.n)])
    head = []
    for l in io.open(a.header_csv, encoding="utf-8", errors="ignore"):
        s = l.rstrip("\r\n")
        if len(s.split(";")) == 5 and s.split(";")[1] == "true":
            break
        head.append(s)
    with io.open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("# weldsim prep bead_outer_line: local node %d of %d sections, %d points\n" % (a.local, nsec, a.n))
        for s in head:
            f.write(s + "\n")
        for k, q in enumerate(pts, 1):
            f.write("%d;true;%.6f;%.6f;%.6f\n" % (k, q[0], q[1], q[2]))
    nl = csv_ends_with_newline(a.out)
    res = {"bead": a.bead, "sections": nsec, "local_index": a.local, "outer_polyline_len_mm": float(cum[-1]),
           "clip_start_dist_mm": d0, "clip_end_dist_mm": d1, "line_len_mm": float(s1 - s0), "points": a.n,
           "csv": a.out, "ends_with_newline": bool(nl)}
    ok = nl
    if a.expect_len is not None:
        res["expect_len_mm"] = a.expect_len
        res["R19_len_diff_mm"] = float(s1 - s0) - a.expect_len
        ok = ok and abs(res["R19_len_diff_mm"]) < a.len_tol
    if a.check_csv:
        ref = np.array(read_weldline_csv(a.check_csv))
        if len(ref) != a.n:
            ok = False
            res["check"] = "point count %d vs %d" % (a.n, len(ref))
        else:
            dev = np.linalg.norm(pts - ref, axis=1)
            res["max_dev_mm"] = float(dev.max())
            ok = ok and dev.max() < a.tol
    res["self_check_ok"] = bool(ok)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if a.json:
        io.open(a.json, "w", encoding="utf-8").write(json.dumps(res, ensure_ascii=False, indent=1) + "\n")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
