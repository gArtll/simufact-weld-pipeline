# -*- coding: utf-8 -*-
"""post step: result extraction for one model, without a reference case.

Reads the increments of `_Results_` through ArcToolCmd, follows the probes of `case.json` (`post.probes`, physical
coordinates -> `run/probes.py`) through time and writes their temperature history, peak, t8/5 and cooling rate, the
mechanical values of the last increment at the same probes, and the solver statistics of the run (`run/solver_stats.py`).

Increment -> time comes from the `.sts` table; the result increment numbers are a subset of the solver increments.
The probes are resolved on the first exported increment, which is the undeformed mesh, and then followed by node
index: the coordinates of a later increment are the deformed ones.

Usage: python run/post.py <params.json>"""
import glob
import io
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import arccsv                                                              # noqa: E402
import probes as probelib                                                  # noqa: E402
import solver_stats                                                        # noqa: E402

FINAL_VALUES = ("PKTEMP", "TOTDISP", "EFFPLS", "EFFSTS")


def increments(res_dir):
    return sorted((int(d) for d in os.listdir(res_dir) if d.isdigit()), key=int)


def select(incs, stride=1, explicit=None):
    """Increments to export: an explicit list, or every `stride`-th one with the first and last always kept."""
    if explicit:
        return [i for i in incs if i in set(explicit)]
    keep = incs[::max(1, int(stride))]
    for i in (incs[0], incs[-1]):
        if i not in keep:
            keep.append(i)
    return sorted(set(keep))


def find_arc(res_dir, inc, body):
    """Result file of one body in one increment. `body` is the body name or a part of it."""
    d = os.path.join(res_dir, "%05d" % inc)
    for pat in ("*_FV_%s_%d.ARC" % (body, inc), "*_FV_*%s*_%d.ARC" % (body, inc)):
        hit = sorted(glob.glob(os.path.join(d, pat)))
        if len(hit) == 1:
            return hit[0]
        if len(hit) > 1:
            raise RuntimeError("increment %d body %r matches %d files: %s" % (inc, body, len(hit), [os.path.basename(x) for x in hit]))
    raise RuntimeError("increment %d: no result file for body %r in %s" % (inc, body, d))


def increment_times(sts_path):
    """Increment number -> simulated time, from the last (accepted) row of each increment."""
    if not sts_path or not os.path.isfile(sts_path):
        return {}
    rows, _ = solver_stats.parse_sts(sts_path)
    return {r["inc"]: r["tot_time"] for r in rows if r["load_case"] not in ("Initializing", "Start simulation")}


def main():
    P = json.load(io.open(sys.argv[1], encoding="utf-8"))
    out = P["out_dir"]
    res_dir = os.path.join(P["proc_dir"], "_Results_")
    incs = increments(res_dir)
    if not incs:
        raise RuntimeError("no result increments in %s" % res_dir)
    names = [p["name"] for p in P["probes"]]
    if len(set(names)) != len(names):
        raise RuntimeError("probe names must be unique (they are the columns of probe_history.csv): %s" % names)
    want = select(incs, P.get("stride", 1), P.get("increments"))
    times = increment_times(P.get("sts"))
    bodies = sorted({p["body"] for p in P["probes"]})
    tmp = os.path.join(out, "arc_csv") if P.get("keep_csv") else tempfile.mkdtemp(prefix="arc_")
    os.makedirs(tmp, exist_ok=True)

    def csv_of(inc, body, posts):
        """Export, read, and delete again: a full run is hundreds of increments and the CSVs add up to gigabytes."""
        c = arccsv.export(P["arctool"], find_arc(res_dir, inc, body), os.path.join(tmp, "%s_%05d.csv" % (body, inc)), posts)
        try:
            return arccsv.read(c, want=posts)
        finally:
            if not P.get("keep_csv"):
                os.remove(c)

    try:
        res, hist = {}, {}
        for body in bodies:                                                # anchor: first exported increment
            xyz, _ = csv_of(want[0], body, ["TEMPTURE"])
            for r in probelib.resolve(xyz, [p for p in P["probes"] if p["body"] == body]):
                res[r["name"]] = r
                hist[r["name"]] = []
        rows = []
        for inc in want:
            vals = {}
            for body in bodies:
                _, v = csv_of(inc, body, ["TEMPTURE"])
                vals[body] = v["TEMPTURE"]
            t = times.get(inc)
            for name in names:
                r = res[name]
                T = float(vals[r["body"]][r["node_index"]])
                hist[name].append((t, T))
            rows.append("%d;%s;%s" % (inc, "" if t is None else "%.6f" % t,
                                      ";".join("%.4f" % hist[n][-1][1] for n in names)))
        finals = {}
        fv = list(P.get("final_values") or FINAL_VALUES)
        for body in bodies:
            _, v = csv_of(want[-1], body, fv)
            finals[body] = {k: v[k] for k in fv}
    finally:
        if not P.get("keep_csv"):
            shutil.rmtree(tmp, ignore_errors=True)

    data = {"proc_dir": P["proc_dir"], "increments_available": len(incs), "increments_exported": want,
            "time_source": "sts" if times else "none", "probes": [], "bodies": bodies,
            # probes this mesh cannot represent: a value is still produced at the nearest node, but it belongs to
            # that node, not to the point that was asked for, and must not be compared with another mesh
            "points_not_represented": [n for n in names if not res[n]["within"]]}
    for name in names:
        r = res[name]
        h = [x for x in hist[name] if x[0] is not None]
        met = probelib.metrics([x[0] for x in h], [x[1] for x in h])   # empty when the .sts gave no time for any increment
        if not h:
            met["status"] = "no increment times (.sts missing): the history has no time axis"
        f = {k: float(finals[r["body"]][k][r["node_index"]]) for k in fv}
        if "PKTEMP" in f and met.get("peak_K") is not None:
            met["solver_peak_K"] = f["PKTEMP"]                              # the solver's own per-node peak
            met["peak_missed_by_sampling_K"] = f["PKTEMP"] - met["peak_K"]  # what the export interval hides
        data["probes"].append({"resolution": r, "metrics": met, "final_values": f,
                               "history": [{"time_s": a, "T_K": b} for a, b in hist[name]]})
    if P.get("sts"):
        data["solver_stats"] = solver_stats.stats(P["sts"], P.get("out_files"))

    os.makedirs(out, exist_ok=True)
    io.open(os.path.join(out, "probe_history.csv"), "w", encoding="utf-8").write(
        "increment;time_s;%s\n%s\n" % (";".join(names), "\n".join(rows)))
    json.dump(data, io.open(os.path.join(out, "post.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    lines = ["increments %d of %d exported, time from %s; final values in solver units (SI: m, Pa, K)"
             % (len(want), len(incs), data["time_source"])]
    for p in data["probes"]:
        lines.append(probelib.format_probe(p["resolution"]["name"], p["resolution"], p["metrics"]))
        lines.append("%-16s %s" % ("", "  ".join("%s %.4g" % (k, v) for k, v in sorted(p["final_values"].items()))))
    if data.get("solver_stats"):
        lines.append("")
        lines.append(solver_stats.format_stats(data["solver_stats"]))
    text = "\n".join(lines) + "\n"
    io.open(os.path.join(out, "post_summary.txt"), "w", encoding="utf-8").write(text)
    print(text, end="")
    far = data["points_not_represented"]
    if far:
        print("this mesh cannot reliably represent these points: %s. Their nearest-node values are reference only "
              "and must not be compared with another mesh's value at the same point." % ", ".join(far), file=sys.stderr)
    return 1 if far else 0


if __name__ == "__main__":
    sys.exit(main())
