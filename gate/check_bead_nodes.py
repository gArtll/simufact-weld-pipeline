# -*- coding: utf-8 -*-
"""Distance of bead nodes to the union of the base meshes (R3 / R20).
Bands: exact < 1e-6 mm; danger 1e-6..0.1 mm; separate > 0.1 mm. Histogram bins include the 10 um sub-band
([1e-6,1e-4) + [1e-4,1e-3) + [1e-3,1e-2)).
NOTE: contact_boundary / interior classification uses local index (id-1) % 7 and is only meaningful for beads with
bead-local node numbering from 1 (prep output). For beads extracted from a .dat (global numbers) it is NOT valid;
check_short_v3 uses only all_nodes and the histogram.
Usage: python check_bead_nodes.py --web W --plate P --bead B [--bead B2] [--trajectory CSV ...] --out-dir DIR
Exit 0 always when the check ran (it is a report); callers judge the counts."""
import argparse
import csv
import json
import math
import os
import sys

import numpy as np
from scipy.spatial import cKDTree

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from common.bdf import read_bdf, read_weldline_csv  # noqa: E402

EDGES = [0.0, 1e-6, 1e-4, 1e-3, 1e-2, 0.1, 1.0, 5.0, 10.0, math.inf]


def classify(d):
    return {"exact_lt_1e-6_mm": int(np.count_nonzero(d < 1e-6)),
            "danger_1e-6_to_0.1_mm": int(np.count_nonzero((d >= 1e-6) & (d <= 0.1))),
            "separate_gt_0.1_mm": int(np.count_nonzero(d > 0.1))}


def histogram(d):
    rows = []
    for lo, hi in zip(EDGES[:-1], EDGES[1:]):
        if math.isinf(hi):
            rows.append({"bin_mm": ">=%g" % lo, "count": int(np.count_nonzero(d >= lo))})
        else:
            rows.append({"bin_mm": "[%g,%g)" % (lo, hi), "count": int(np.count_nonzero((d >= lo) & (d < hi)))})
    return rows


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--web", required=True)
    p.add_argument("--plate", required=True)
    p.add_argument("--bead", action="append", required=True)
    p.add_argument("--trajectory", action="append", default=[])
    p.add_argument("--out-dir", required=True)
    a = p.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    W, _, _ = read_bdf(a.web)
    Pl, _, _ = read_bdf(a.plate)
    tree = cKDTree(np.vstack((np.array(list(W.values())), np.array(list(Pl.values())))))
    result = {"thresholds_mm": {"exact": 1e-6, "danger_upper": 0.1}, "beads": [], "trajectory_roots": []}
    hist = []
    for bead in a.bead:
        nodes, _, _ = read_bdf(bead)
        ids = np.array(list(nodes.keys()))
        d, _ = tree.query(np.array(list(nodes.values())), k=1)
        boundary = np.isin((ids - 1) % 7, [0, 1, 2, 5, 6])
        result["beads"].append({"file": bead, "node_count": int(len(d)), "all_nodes": classify(d),
                                "contact_boundary_nodes_bead_local_numbering_only": classify(d[boundary]),
                                "minimum_mm": float(d.min()), "maximum_mm": float(d.max())})
        hist += [{"bead": os.path.basename(bead), **r} for r in histogram(d)]
    for t in a.trajectory:
        d, _ = tree.query(np.array(read_weldline_csv(t)), k=1)
        result["trajectory_roots"].append({"file": t, "point_count": int(len(d)), "exact_count": int(np.count_nonzero(d < 1e-6)),
                                           "minimum_mm": float(d.min()), "maximum_mm": float(d.max())})
    json.dump(result, open(os.path.join(a.out_dir, "bead_node_check.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    with open(os.path.join(a.out_dir, "bead_node_histogram.csv"), "w", newline="", encoding="utf-8-sig") as h:
        w = csv.DictWriter(h, fieldnames=["bead", "bin_mm", "count"])
        w.writeheader()
        w.writerows(hist)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
