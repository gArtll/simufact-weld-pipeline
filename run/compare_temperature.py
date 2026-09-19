# -*- coding: utf-8 -*-
"""compare step: same-section peak temperature, new run vs reference result increment (R20: diff < 5%).
Copied from P0_A4 temperature_a4.py (section-offset fix included). Changes: all paths / body names / speed from a
params JSON (argv[1]); the short-weld start section is the reference section whose outer-surface node (local index 4,
0-based) is nearest the window start point (R14/R19 definition), instead of a hard-coded root start point.
Judged section = start + speed * end_time / 2 (middle of the length heated by end_time)."""
import glob
import io
import json
import os
import subprocess
import sys

import numpy as np
from scipy.spatial import cKDTree

P = None
OUT = None


def export(arc, csv):
    r = subprocess.run([P["arctool"], "FileIn=" + arc, "FileOut=" + csv, "Format=4", "Post=TEMPTURE"], capture_output=True, text=True)
    if not os.path.exists(csv):
        raise RuntimeError("ArcToolCmd failed %s: %s %s" % (arc, r.stdout[-300:], r.stderr[-300:]))


def read_csv(path):
    lines = open(path, encoding="utf-8", errors="ignore").read().splitlines()
    ci = next(i for i, s in enumerate(lines) if s.startswith("Coordinates (nb)"))
    n = int(lines[ci].split(";")[1])
    xyz = np.array([[float(v) for v in lines[ci + 1 + k].split(";")[1:4]] for k in range(n)]) * 1000.0
    pi = next(i for i, s in enumerate(lines) if s.startswith("Post value (name, number);TEMPTURE"))
    vals = [float(v) for v in lines[pi + 1:] if v.strip() and ";" not in v]
    t = np.array(vals[4:4 + n])
    if len(t) != n:
        raise ValueError("%s: %d coords, %d temps" % (path, n, len(t)))
    return xyz, t


def main():
    global P, OUT
    P = json.load(io.open(sys.argv[1], encoding="utf-8"))
    OUT = P["out_dir"]
    target = P["weld_speed_mm_s"] * P["end_time_s"] / 2.0
    res_dir = os.path.join(P["new_proc_dir"], "_Results_")
    inc = sorted(d for d in os.listdir(res_dir) if d.isdigit())[-1]
    S = json.load(io.open(P["reference_sections_json"]))
    res = {"new_increment": inc, "target_ds_mm": target, "welds": {}, "tables": {}}
    for s in P["sides"]:
        lab = s["label"]
        pairs = {}
        new_arc = glob.glob(os.path.join(res_dir, inc, "*_FV_%s_%d.ARC" % (s["new_body"], int(inc))))
        if len(new_arc) != 1:
            raise RuntimeError("new ARC for %s: %r" % (s["new_body"], new_arc))
        for tag, arc in (("new", new_arc[0]), ("ref", s["reference_arc"])):
            csv = os.path.join(OUT, "temp_%s_%s.csv" % (tag, lab))
            export(arc, csv)
            pairs[tag] = read_csv(csv) + (arc,)
        sec = np.array([x["nodes_mm"] for x in S[s["reference_bead_key"]]])
        s_mm = np.array([x["s_mm"] for x in S[s["reference_bead_key"]]])
        tree = cKDTree(sec.reshape(-1, 3))
        per = {}
        for tag, (xyz, t, arc) in pairs.items():
            d, idx = tree.query(xyz)
            peak = {}
            for i, T, dd in zip(idx // 7, t, d):
                if dd < 1.0:
                    peak[int(i)] = max(peak.get(int(i), -1e9), float(T))
            per[tag] = {"arc": arc, "nodes": int(len(xyz)), "peak": peak}
        common = sorted(i for i in per["new"]["peak"] if i in per["ref"]["peak"] and per["new"]["peak"][i] > 294.15 and per["ref"]["peak"][i] > 294.15)
        w0 = np.array(s["window_start_point_mm"])
        od = np.linalg.norm(sec[:, 4, :] - w0, axis=1)
        start = int(np.argmin(od))
        ds = s_mm - s_mm[start]
        mid = min(common, key=lambda i: abs(ds[i] - target))
        a, b = per["new"]["peak"][mid], per["ref"]["peak"][mid]
        rows = []
        for i in sorted(set(per["new"]["peak"]) | set(per["ref"]["peak"])):
            nv, rv = per["new"]["peak"].get(i), per["ref"]["peak"].get(i)
            if (nv or 0) > 294.15 or (rv or 0) > 294.15:
                rows.append("%4d %10.2f %9s %9s %7s" % (i, ds[i], "%.1f" % nv if nv else "-", "%.1f" % rv if rv else "-",
                                                        "%.2f" % (abs(nv - rv) / rv * 100) if nv and rv else "-"))
        res["tables"][lab] = "## %s start section %d (outer node dist %.4f mm) target ds %.3f\n sec  ds_from_start    new_K     ref_K   diff%%\n%s" % (
            lab, start, od[start], target, "\n".join(rows))
        res["welds"][lab] = {"arc_new": per["new"]["arc"], "arc_ref": per["ref"]["arc"], "nodes_new_ref": [per["new"]["nodes"], per["ref"]["nodes"]],
                             "start_section_index": start, "start_outer_node_dist_mm": float(od[start]),
                             "mid_section_index": mid, "mid_section_ds_mm": float(ds[mid]),
                             "peak_new_K": a, "peak_ref_K": b, "diff_percent": abs(a - b) / b * 100,
                             "pass_lt_limit": bool(abs(a - b) / b * 100 < P["limit_percent"])}
    json.dump(res, open(os.path.join(OUT, "temperature_comparison.json"), "w"), indent=2)
    io.open(os.path.join(OUT, "temperature_section_table.txt"), "w", encoding="utf-8").write("\n\n".join(res["tables"].values()) + "\n")
    for lab, v in res["welds"].items():
        print("%s start %d mid %d ds %.2f mm new %.1f K ref %.1f K diff %.2f%% pass=%s" % (
            lab, v["start_section_index"], v["mid_section_index"], v["mid_section_ds_mm"], v["peak_new_K"], v["peak_ref_K"], v["diff_percent"], v["pass_lt_limit"]))
    sys.exit(0 if all(v["pass_lt_limit"] for v in res["welds"].values()) else 1)


if __name__ == "__main__":
    main()
