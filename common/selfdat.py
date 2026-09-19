# -*- coding: utf-8 -*-
"""Minimal MARC-style .dat built from a prep run, for `weldsim gate --self` without Simufact.
Contains exactly the blocks check_short_v3 reads (COORDINATES in m, CONNECTIVITY, bead element sets, WELD FILL,
trajectory NDSQ node sets, CONTACT TABLE, AUTO STEP) in the layout parsed by common/dat.py. It is not a solver deck."""
import io

from common.bdf import read_bdf, read_weldline_csv


def _nearest(nodes, p):
    best, bid = None, None
    for nid, q in nodes.items():
        d = (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 + (q[2] - p[2]) ** 2
        if best is None or d < best:
            best, bid = d, nid
    return bid


def _id_lines(ids, per_line=8):
    return ["".join("%10d" % v for v in ids[k:k + per_line]) for k in range(0, len(ids), per_line)]


def write_self_dat(path, base_bdfs, beads, outer_lines, end_time_s, weld_step_s):
    """base_bdfs: [bdf]; beads: [(label, bead bdf)]; outer_lines: {label: csv}. Returns counts."""
    coords, conn, sets, ndsq = [], [], [], []
    nid = eid = 0
    for path_bdf in base_bdfs:
        G, E, _ = read_bdf(path_bdf)
        off = {k: nid + i + 1 for i, k in enumerate(G)}
        coords += [G[k] for k in G]
        nid += len(G)
        for e in E:
            eid += 1
            conn.append((eid, [off[v] for v in e]))
    for k, (label, bdf) in enumerate(beads):
        G, E, _ = read_bdf(bdf)
        off = {n: nid + i + 1 for i, n in enumerate(G)}
        coords += [G[n] for n in G]
        nid += len(G)
        first = eid + 1
        for e in E:
            eid += 1
            conn.append((eid, [off[v] for v in e]))
        sets.append(("Geom-%d-beadX%s_el_set" % (k + 1, label), first, eid))
        local = {off[n]: G[n] for n in G}
        ndsq.append(("trajectory_nodes_%d" % (19000 + 3 * k), [_nearest(local, p) for p in read_weldline_csv(outer_lines[label])]))
    L = ["$ weldsim self .dat (not a solver deck)", "TITLE     weldsim_self", "$....",
         "COORDINATES", "%10d%10d" % (3, len(coords))]
    L += ["%10d%20.13e%20.13e%20.13e" % (i + 1, x / 1000.0, y / 1000.0, z / 1000.0) for i, (x, y, z) in enumerate(coords)]
    L += ["$....", "CONNECTIVITY", "%10d" % len(conn)]
    L += ["%10d%10d" % (e, 7) + "".join("%10d" % v for v in ids) for e, ids in conn]
    L.append("$....")
    for name, a, b in sets:
        L += ["DEFINE    ELEMENT   SET       %s" % name, "%10d        TO%10d" % (a, b), "$...."]
    for name, ids in ndsq:
        L += ["DEFINE    NDSQ      SET       %s" % name] + _id_lines(ids) + ["$...."]
    L += ["WELD FILL"] + [name for name, _, _ in sets] + ["$...."]
    L += ["CONTACT TABLE", "%10d" % len(beads), "$...."]
    L += ["AUTO STEP", "%20.13e%20.13e" % (weld_step_s, end_time_s), "$...."]
    L.append("END OPTION")
    with io.open(path, "w", encoding="latin-1", newline="\n") as f:
        f.write("\n".join(L) + "\n")
    return {"nodes": len(coords), "elements": len(conn), "bead_sets": len(sets), "ndsq_sets": len(ndsq)}
