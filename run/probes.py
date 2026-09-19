# -*- coding: utf-8 -*-
"""Probes anchored to a physical coordinate, and the metrics of one probe's thermal cycle.

Why a coordinate and not a node number: two meshes of the same part have different node numbering, so a node number
compares nothing across meshes. A probe is a point in the model coordinate system (mm); it is resolved once, on the
anchor increment (the undeformed mesh), to the nearest node of the named body, and that node index is then followed
through the increments. Resolution is reported with its distance, and a probe whose nearest node is further away than
`max_distance_mm` fails: on a coarse mesh the nearest node can be several millimetres from the intended point, and a
comparison of two such probes is not a comparison at the same physical location.

t8/5 is read off the cooling branch by linear interpolation between the samples that bracket 1073.15 K and 773.15 K.
It is only as good as the result output interval: `samples_in_window` and `max_gap_in_window_s` are reported with it,
and no value is invented when the cycle does not cross both temperatures."""
import numpy as np

T_HI = 1073.15                    # 800 degC
T_LO = 773.15                     # 500 degC
T_RATE = 973.15                   # 700 degC, reference temperature of the reported cooling rate


def resolve(coords_mm, probes):
    """Nearest node of `coords_mm` (n x 3, mm) for every probe {name, point_mm, max_distance_mm}."""
    xyz = np.asarray(coords_mm, dtype=float)
    out = []
    for p in probes:
        pt = np.asarray(p["point_mm"], dtype=float)
        d = np.linalg.norm(xyz - pt, axis=1)
        k = int(np.argmin(d))
        lim = float(p.get("max_distance_mm", 2.0))
        within = bool(d[k] <= lim)
        out.append({"name": p["name"], "body": p.get("body"), "point_mm": [float(v) for v in pt],
                    "node_index": k, "node_mm": [float(v) for v in xyz[k]], "distance_mm": float(d[k]),
                    "max_distance_mm": lim, "within": within,
                    # the verdict is about the mesh, not about the probe: a value is still produced at the nearest
                    # node, but it belongs to that node's position, not to the point that was asked for
                    "represents_point": within,
                    "comparable_across_meshes": within,
                    "verdict": "ok" if within else
                               "this mesh cannot reliably represent this point (nearest node %.3f mm away, limit %.3f mm); "
                               "the values below are the nearest node's own history, for reference only, and must not be "
                               "compared with another mesh's value at this point" % (d[k], lim)})
    return out


def _cross(times, temps, k, level):
    """Time at which the cooling branch passes `level` between samples k-1 and k (temps[k] <= level < temps[k-1])."""
    t0, t1, a, b = times[k - 1], times[k], temps[k - 1], temps[k]
    if a == b:
        return float(t1)
    return float(t0 + (a - level) * (t1 - t0) / (a - b))


def metrics(times_s, temps_K, t_hi=T_HI, t_lo=T_LO, t_rate=T_RATE):
    """Peak, t8/5 and cooling rate of one thermal cycle. Samples are sorted by time; equal times are kept."""
    o = np.argsort(np.asarray(times_s, dtype=float), kind="stable")
    t = np.asarray(times_s, dtype=float)[o]
    T = np.asarray(temps_K, dtype=float)[o]
    r = {"samples": int(len(t)), "peak_K": None, "time_at_peak_s": None, "final_K": None,
         "t85_s": None, "t85_start_s": None, "t85_end_s": None, "samples_in_window": None,
         "max_gap_in_window_s": None, "cooling_rate_K_per_s": None, "status": "no samples"}
    if not len(t):
        return r
    pk = int(np.argmax(T))
    r.update({"peak_K": float(T[pk]), "time_at_peak_s": float(t[pk]), "final_K": float(T[-1])})
    if T[pk] < t_hi:
        r["status"] = "peak below %.2f K: no t8/5" % t_hi
        return r
    k_hi = next((i for i in range(pk + 1, len(T)) if T[i] <= t_hi), None)
    k_lo = next((i for i in range(pk + 1, len(T)) if T[i] <= t_lo), None)
    if k_hi is None or k_lo is None:
        r["status"] = "cooling branch does not reach %.2f K: no t8/5" % (t_hi if k_hi is None else t_lo)
        return r
    a, b = _cross(t, T, k_hi, t_hi), _cross(t, T, k_lo, t_lo)
    win = [i for i in range(pk + 1, k_lo + 1) if t_lo <= T[i] <= t_hi]
    gaps = np.diff(t[win]) if len(win) > 1 else np.array([b - a])
    r.update({"t85_s": b - a, "t85_start_s": a, "t85_end_s": b, "samples_in_window": len(win),
              "max_gap_in_window_s": float(gaps.max()), "status": "ok"})
    k_r = next((i for i in range(pk + 1, len(T)) if T[i] <= t_rate), None)
    if k_r is not None and t[k_r] > t[k_r - 1]:
        r["cooling_rate_K_per_s"] = float((T[k_r - 1] - T[k_r]) / (t[k_r] - t[k_r - 1]))
    return r


def format_probe(name, res, met):
    """One line per probe, plus a second line when the mesh cannot represent the point."""
    line = ("%s%-16s node %-8d at %.3f mm from the point   peak %s K   t8/5 %s s (%s samples, max gap %s s)   "
            "cooling rate %s K/s   %s" % (
                "" if res["within"] else "[REFERENCE ONLY] ",
                name, res["node_index"], res["distance_mm"],
                "%.1f" % met["peak_K"] if met["peak_K"] is not None else "-",
                "%.2f" % met["t85_s"] if met["t85_s"] is not None else "-",
                met["samples_in_window"] if met["samples_in_window"] is not None else "-",
                "%.2f" % met["max_gap_in_window_s"] if met["max_gap_in_window_s"] is not None else "-",
                "%.2f" % met["cooling_rate_K_per_s"] if met["cooling_rate_K_per_s"] is not None else "-",
                met["status"] if met["status"] != "ok" else ""))
    return line if res["within"] else line + "\n" + " " * 17 + res["verdict"]
