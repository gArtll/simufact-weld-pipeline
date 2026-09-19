# -*- coding: utf-8 -*-
"""Round 1 of the plan: the five minimum cases, each with its topology checks, quality table and drawing.

  P0-1  reference T2_pair   4 fine -> 2 coarse
  P0-2  reference T3        3 fine -> 1 coarse
  P0-3  T2_pair tiled
  P0-4  T3 tiled
  P0-5  one mixed case      10 fine -> 4 coarse  (n2 = 2, n3 = 2, plan section 15)

Usage: python -m mesh_transition.round1 [out_dir]"""
import io
import json
import os
import sys

from .mesh import check_all, node_quads, boundary_edges
from .quality import quality_table, summary, summary_table, worst_element, metrics
from .svg import draw
from .templates import can_build, strip, t2_pair, t3

CASES = [
    ("P0-1_T2_pair", "reference T2_pair: 4 fine -> 2 coarse", lambda: (t2_pair(), None)),
    ("P0-2_T3", "reference T3: 3 fine -> 1 coarse", lambda: (t3(), None)),
    ("P0-3_T2_pair_tiled", "T2_pair tiled x3: 12 fine -> 6 coarse", lambda: strip([2, 2, 2, 2, 2, 2])),
    ("P0-4_T3_tiled", "T3 tiled x3: 9 fine -> 3 coarse", lambda: strip([3, 3, 3])),
    ("P0-5_mixed", "mixed: 10 fine -> 4 coarse (n2 = 2, n3 = 2)", lambda: strip([2, 2, 3, 3])),
]


def describe(name, title, m, zones):
    chk = check_all(m)
    nq = node_quads(m)
    bnodes = {i for e in boundary_edges(m) for i in e}
    lines = ["=" * 100, "%s  --  %s" % (name, title), "=" * 100, "",
             "nodes %d, elements %d, boundary edges %d, interior nodes %d"
             % (chk["nodes"], chk["elements"], chk["boundary_edges"], chk["interior_nodes"]), ""]
    lines.append("coordinates and node degree")
    lines.append("%5s %10s %10s %10s %10s" % ("node", "x", "y", "elements", "kind"))
    for i in sorted(m.nodes):
        x, y = m.nodes[i]
        lines.append("%5d %10.4f %10.4f %10d %10s" % (i, x, y, len(nq[i]), "boundary" if i in bnodes else "interior"))
    lines += ["", "QUAD connectivity (counter-clockwise)", "%5s  %s" % ("elem", "n1 n2 n3 n4")]
    for q in sorted(m.quads):
        lines.append("%5d  %s%s" % (q, " ".join("%4d" % i for i in m.quads[q]),
                                    ("   [%s]" % zones[q]) if zones else ""))
    lines += ["", "topology checks"]
    for k in ("duplicate_node_elements", "zero_area_elements", "inverted_elements", "non_manifold_edges",
              "orphan_nodes", "hanging_nodes", "flat_corners"):
        lines.append("  %-26s %s" % (k, chk[k] if chk[k] else "none"))
    lines.append("  %-26s %s (expected %d from boundary_edges/2 + interior_nodes - 1)"
                 % ("euler_ok", chk["euler_ok"], chk["euler_elements_expected"]))
    lines.append("  %-26s %s" % ("interior node min elements", chk["interior_node_min_elements"]))
    lines.append("  %-26s %s" % ("ALL TOPOLOGY CHECKS", "PASS" if chk["ok"] else "FAIL"))
    lines += ["", "quality (this project's own metrics, see quality.py; not HyperMesh's)", quality_table(m, zones), ""]
    lines.append(summary_table(summary(m)))
    w = worst_element(m)
    lines += ["", "worst element E%d: %s" % (w, {k: round(v, 4) for k, v in metrics(m.corners(w)).items()})]
    return "\n".join(lines), chk


def main(argv):
    out = argv[0] if argv else os.path.join(os.path.dirname(os.path.abspath(__file__)), "round1_out")
    os.makedirs(out, exist_ok=True)
    text, index = [], {}
    for name, title, build in CASES:
        m, zones = build()
        body, chk = describe(name, title, m, zones)
        text.append(body)
        draw(m, os.path.join(out, name + ".svg"), title="%s  %s" % (name, title), zones=zones)
        index[name] = {"title": title, "nodes": chk["nodes"], "elements": chk["elements"],
                       "topology_ok": chk["ok"], **{k: v for k, v in summary(m).items()}}
    # which sequences the reference templates can and cannot build
    text.append("=" * 100)
    text.append("which runs of cells close, and with what")
    text.append("=" * 100)
    text.append("%-22s %-8s %-8s %-6s  %s" % ("cells (fine per coarse)", "Ncoarse", "Nfine", "b", "result"))
    for cells in ([2], [3], [2, 2], [2, 3], [3, 3], [2, 3, 2], [2, 2, 3], [3, 2, 2, 3], [2, 2, 3, 3]):
        ok, res = can_build(cells)
        text.append("%-22s %-8d %-8d %-6d  %s" % (cells, len(cells), sum(cells), len(cells) + sum(cells) + 2,
                                                  (" + ".join(res) if ok else "NO: " + res)))
    report = "\n\n".join(text) + "\n"
    io.open(os.path.join(out, "round1_report.txt"), "w", encoding="utf-8", newline="\n").write(report)
    json.dump(index, io.open(os.path.join(out, "round1_summary.json"), "w", encoding="utf-8"), indent=1)
    print(report)
    return 0 if all(v["topology_ok"] for v in index.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
