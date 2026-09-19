# -*- coding: utf-8 -*-
"""HyperMesh raw dump -> R1 BDF (GRID* large field + CHEXA / CPENTA fixed field, mm), quality statistics, thickness
layers and web-plate contact gap. Pure Python, deterministic: coordinates rounded to 1e-9 mm, ids renumbered in
HyperMesh id order, element winding normalised to positive volume."""
import io
import math

from common.bdf import read_bdf
from common.preflight import thickness_layers

CONFIG = {"208": "hex8", "206": "penta6", "204": "tetra4"}
HEX_CORNERS = [(0, 1, 3, 4), (1, 2, 0, 5), (2, 3, 1, 6), (3, 0, 2, 7), (4, 7, 5, 0), (5, 4, 6, 1), (6, 5, 7, 2), (7, 6, 4, 3)]
PENTA_CORNERS = [(0, 1, 2, 3), (1, 2, 0, 4), (2, 0, 1, 5), (3, 5, 4, 0), (4, 3, 5, 1), (5, 4, 3, 2)]


def parse_raw(path):
    nodes, elems = {}, []
    for line in io.open(path, encoding="utf-8", errors="replace"):
        t = line.split()
        if not t:
            continue
        if t[0] == "N":
            nodes[int(t[1])] = tuple(round(float(v), 9) + 0.0 for v in t[2:5])
        elif t[0] == "E":
            elems.append((int(t[1]), CONFIG.get(t[2], t[2]), [int(v) for v in t[3:]]))
    return nodes, sorted(elems)


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _triple(u, v, w):
    return u[0] * (v[1] * w[2] - v[2] * w[1]) - u[1] * (v[0] * w[2] - v[2] * w[0]) + u[2] * (v[0] * w[1] - v[1] * w[0])


def _unit(v):
    l = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / l, v[1] / l, v[2] / l) if l > 0 else (0.0, 0.0, 0.0)


def scaled_jacobian(xyz, corners):
    return min(_triple(_unit(_sub(xyz[b], xyz[a])), _unit(_sub(xyz[c], xyz[a])), _unit(_sub(xyz[d], xyz[a]))) for a, b, c, d in corners)


def oriented(kind, ids, coords):
    """Winding with positive corner Jacobians (bottom and top faces swapped when inverted)."""
    xyz = [coords[i] for i in ids]
    if kind == "hex8":
        if scaled_jacobian(xyz, HEX_CORNERS[:1]) < 0:
            ids = ids[4:8] + ids[0:4]
    elif kind == "penta6":
        if scaled_jacobian(xyz, PENTA_CORNERS[:1]) < 0:
            ids = ids[3:6] + ids[0:3]
    return ids


def build(nodes, elems, surface_height=None):
    """Renumbered, curvature-mapped, oriented mesh: (coords list, [(kind, ids 1-based)])."""
    order = sorted(nodes)
    new = {old: k + 1 for k, old in enumerate(order)}
    coords = {}
    for old in order:
        x, y, z = nodes[old]
        if surface_height is not None:
            y = round(y + surface_height(z), 9) + 0.0
        coords[new[old]] = (x, y, z)
    out = [(kind, oriented(kind, [new[i] for i in ids], coords)) for _, kind, ids in elems]
    return coords, out


def write_bdf(path, coords, elems, title):
    with io.open(path, "w", encoding="ascii", newline="\r\n") as f:
        f.write("$ simufact-weld-pipeline hm: %s\n$ Units: mm\nSOL 101\nCEND\nBEGIN BULK\n" % title)
        for nid in sorted(coords):
            x, y, z = coords[nid]
            f.write("%-8s%16d%16s%16.9e%16.9e%-8s\n" % ("GRID*", nid, "", x, y, "*"))
            f.write("%-8s%16.9e\n" % ("*", z))
        for eid, (kind, ids) in enumerate(elems, 1):
            if kind == "hex8":
                f.write("%-8s%8d%8d%8d%8d%8d%8d%8d%8d%-8s\n" % (("CHEXA", eid, 1) + tuple(ids[:6]) + ("+",)))
                f.write("%-8s%8d%8d\n" % ("+", ids[6], ids[7]))
            elif kind == "penta6":
                f.write("%-8s%8d%8d%8d%8d%8d%8d%8d%8d\n" % (("CPENTA", eid, 1) + tuple(ids)))
            else:
                raise ValueError("unsupported element %s" % kind)
        f.write("ENDDATA\n")


def quality(coords, elems):
    n = len(elems)
    kinds = {k: sum(1 for kk, _ in elems if kk == k) for k in ("hex8", "penta6", "tetra4")}
    sj = [scaled_jacobian([coords[i] for i in ids], HEX_CORNERS if k == "hex8" else PENTA_CORNERS) for k, ids in elems if k in ("hex8", "penta6")]
    # penta_fraction = CPENTA count / element count (not a wedge aspect ratio)
    return {"elements": n, **kinds, "penta_fraction": (kinds["penta6"] / n) if n else 1.0,
            "min_scaled_jacobian": round(min(sj), 6) if sj else None}


def layers(path):
    """Element layers through the thinnest direction (hexahedra of an R1 BDF): (min, max)."""
    nodes, hexas, _ = read_bdf(path)
    lo, hi, _, _ = thickness_layers(nodes, hexas)
    return lo, hi


def contact_gap(web_path, plate_path, surface_height, band=1e-3):
    """Max distance of web bottom-face nodes and plate top-face nodes from the analytic plate top surface."""
    web, _, _ = read_bdf(web_path)
    plate, _, _ = read_bdf(plate_path)
    wh = [y - surface_height(z) for x, y, z in web.values()]
    ph = [y - surface_height(z) for x, y, z in plate.values()]
    wb = [abs(h) for h in wh if h < band]
    pt = [abs(h) for h in ph if h > -band]
    return {"web_bottom_nodes": len(wb), "plate_top_nodes": len(pt),
            "gap_mm": max(wb + pt) if wb and pt else None, "penetration": min(wh) < -band}
