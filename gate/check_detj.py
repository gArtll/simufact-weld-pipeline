# -*- coding: utf-8 -*-
"""2x2x2 Gauss-point detJ check for CHEXA beads (R2: non-positive count must be 0).
Usage: python check_detj.py <bead.bdf> [...] --json <out.json>"""
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from common.bdf import read_bdf  # noqa: E402

g = 1 / np.sqrt(3)
NAT = np.array([(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1), (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)], float)
DN = []
for xi in (-g, g):
    for et in (-g, g):
        for ze in (-g, g):
            DN.append(np.array([[n[0] * (1 + n[1] * et) * (1 + n[2] * ze), n[1] * (1 + n[0] * xi) * (1 + n[2] * ze),
                                 n[2] * (1 + n[0] * xi) * (1 + n[1] * et)] for n in NAT]) / 8)
DN = np.array(DN)


def detj(nodes, elems):
    X = np.array([[nodes[k] for k in e] for e in elems])
    return np.linalg.det(np.einsum("gni,enj->egij", DN, X))


def main(argv):
    if "--json" not in argv:
        sys.exit("usage: check_detj.py <bead.bdf> [...] --json <out.json>")
    k = argv.index("--json")
    out_json = argv[k + 1]
    paths = argv[:k] + argv[k + 2:]
    res = {}
    for path in paths:
        nodes, elems, _ = read_bdf(path)
        d = detj(nodes, elems)
        xyz = np.array(list(nodes.values()))
        dup = len(xyz) - len(np.unique(np.round(xyz, 9), axis=0))
        res[path] = {"elements": len(elems), "nodes": len(nodes), "gauss_points": int(d.size), "min_detJ": float(d.min()),
                     "max_detJ": float(d.max()), "nonpositive": int((d <= 0).sum()),
                     "elements_with_nonpositive": int((d <= 0).any(1).sum()), "coincident_node_duplicates": int(dup)}
        print("%s elements=%d gauss_points=%d min_detJ=%.6g max_detJ=%.6g nonpositive=%d duplicate_nodes=%d"
              % (path, len(elems), d.size, d.min(), d.max(), (d <= 0).sum(), dup))
    json.dump(res, open(out_json, "w"), indent=2)


if __name__ == "__main__":
    main(sys.argv[1:])
