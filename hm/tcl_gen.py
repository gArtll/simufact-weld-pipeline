# -*- coding: utf-8 -*-
"""HyperMesh batch Tcl for the plate and the web, generated from base_mesh parameters, plus static checks of the
Tcl hard rules (common/rules.yaml TCL1-TCL4). Generated scripts use only relative file names (cwd = attempt dir),
write no HyperMesh model files and export by plain Tcl file I/O."""
import re

from hm.params import zones

HEADER = """# simufact-weld-pipeline hm: {role} base mesh (generated, do not edit)
{params}
proc P {{s}} {{ puts $s; flush stdout }}
proc ecount {{c}} {{ *createmark elements 1 "by config" $c; return [hm_marklength elements 1] }}
*collectorcreateonly components "{role}" "" 5
*currentcollector components "{role}"
"""

MESH_ZONES = """# TCL1: zones sorted by element size, finest first
set ZONES {{{zones}}}
*createmark surfaces 1 "all"
set SURFS [hm_getmark surfaces 1]
set DONE {{}}
foreach zone $ZONES {{
  foreach {{limit size}} $zone break
  foreach s $SURFS {{
    if {{[lsearch $DONE $s] >= 0}} {{ continue }}
    *createmark surfaces 2 $s
    foreach {{x0 y0 z0 x1 y1 z1}} [hm_getboundingbox surfaces 2 0 0] break
    set c [expr {{abs(({lo}+{hi})/2.0 - {origin})}}]
    if {{$c > $limit + 1e-6}} {{ continue }}
    *createmark surfaces 1 $s
    *interactiveremeshsurf 1 $size 1 1 2 1 1
    *set_meshfaceparams 0 1 1 0 0 1 0.5 1 1
    *automesh 0 1 1
    *storemeshtodatabase 1
    *ameshclearsurface
    lappend DONE $s
    P "MESH surf=$s size=$size quad4=[ecount quad4] tria3=[ecount tria3]"
  }}
}}
"""

DRAG_DUMP = """*createmark elements 1 "all"
*createvector 1 {vx} {vy} {vz}
*meshdragelements 1 1 {dist!r} {layers} 0 0
foreach c {{quad4 tria3}} {{ *createmark elements 1 "by config" $c; if {{[hm_marklength elements 1] > 0}} {{ *deletemark elements 1 }} }}
P "SOLID hex8=[ecount hex8] penta6=[ecount penta6] tetra4=[ecount tetra4]"
set f [open "{raw}" w]
*createmark nodes 1 "all"
foreach n [hm_getmark nodes 1] {{ puts $f "N $n [hm_getvalue nodes id=$n dataname=coordinates]" }}
*createmark elements 1 "all"
foreach e [hm_getmark elements 1] {{ puts $f "E $e [hm_getvalue elems id=$e dataname=config] [hm_getvalue elems id=$e dataname=nodes]" }}
close $f
P "DONE {raw}"
"""


def _fmt(v):
    return repr(round(float(v), 9))


def _params_comment(p, band_size, z):
    lines = ["# PARAM %s.%s=%s" % (g, k, v) for g in ("plate", "web", "mesh", "quality") for k, v in sorted(p[g].items())]
    lines.append("# PARAM attempt.weld_band_size_mm=%s" % _fmt(band_size))
    lines.append("# PARAM derived.web_first_row_mm=%s" % _fmt(z["web_first_row_mm"]))
    return "\n".join(lines)


def _loop(pts):
    out = []
    for a, b in zip(pts, pts[1:] + pts[:1]):
        out.append("*linecreatestraight %s %s %s %s %s %s" % tuple(_fmt(v) for v in a + b))
    out += ['*createmark lines 1 "all"', "*surfacesplineonlinesloop 1 1 0 67"]
    return "\n".join(out) + "\n"


def _planes(normal, values):
    out = ["# TCL4: surfaces split only by planes"]
    for v in values:
        base = [0.0, 0.0, 0.0]
        base["xyz".index(normal)] = v
        n = [1.0 if c == normal else 0.0 for c in "xyz"]
        out += ['*createmark surfaces 1 "all"',
                "*createplane 1 %s %s %s %s %s %s" % tuple(_fmt(x) for x in n + base),
                "*surfacemarksplitwithplane 1 1"]
    return "\n".join(out) + "\n"


def plate_tcl(p, band_size, raw="plate_raw.txt"):
    z = zones(p, band_size)
    L, W, t = (float(p["plate"][k]) for k in ("length_mm", "width_mm", "thickness_mm"))
    text = HEADER.format(role="plate", params=_params_comment(p, band_size, z))
    text += _loop([(-W / 2, 0.0, 0.0), (W / 2, 0.0, 0.0), (W / 2, 0.0, L), (-W / 2, 0.0, L)])
    text += _planes("x", z["plate_planes"])
    text += MESH_ZONES.format(zones=" ".join("{%s %s}" % (_fmt(lim), _fmt(s)) for lim, s in z["plate_zones"]),
                              lo="$x0", hi="$x1", origin="0.0")
    text += DRAG_DUMP.format(vx="0.0", vy="-1.0", vz="0.0", dist=round(t, 9), layers=int(p["mesh"]["plate_layers"]), raw=raw)
    return text


def web_tcl(p, band_size, raw="web_raw.txt"):
    z = zones(p, band_size)
    H, tw, z0, Lw = (float(p["web"][k]) for k in ("height_mm", "thickness_mm", "z_start_mm", "length_mm"))
    x = -tw / 2.0
    text = HEADER.format(role="web", params=_params_comment(p, band_size, z))
    text += _loop([(x, 0.0, z0), (x, 0.0, z0 + Lw), (x, H, z0 + Lw), (x, H, z0)])
    text += _planes("y", z["web_planes"])
    text += MESH_ZONES.format(zones=" ".join("{%s %s}" % (_fmt(lim), _fmt(s)) for lim, s in z["web_zones"]),
                              lo="$y0", hi="$y1", origin="0.0")
    text += DRAG_DUMP.format(vx="1.0", vy="0.0", vz="0.0", dist=round(tw, 9), layers=int(p["mesh"]["web_layers"]), raw=raw)
    return text


# ---------------------------------------------------------------- static checks (common/rules.yaml TCL1-TCL4)
def _code(text):
    return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]


def check_tcl1(text):
    """TCL1 fine before coarse: every ZONES list has non-decreasing element sizes; meshing happens only inside the
    ZONES loop."""
    lists = re.findall(r"^set ZONES \{(.*)\}$", text, re.M)
    sizes = [[float(s) for s in re.findall(r"\{\S+ (\S+)\}", z)] for z in lists]
    loose = [l for l in _code(text) if l.startswith("*interactiveremeshsurf") and "$size" not in l]
    ok = bool(lists) and all(s == sorted(s) and s for s in sizes) and not loose
    return ok, {"zone_sizes": sizes, "meshing_outside_zones": loose}


def check_tcl2(text):
    """TCL2 mark ids 1 and 2 only."""
    ids = re.findall(r"(?:\*createmark|hm_getmark|hm_marklength|\*deletemark|\*createlist)\s+\w+\s+(\d+)", text)
    ids += re.findall(r"\*(?:surfacemarksplitwithplane|interactiveremeshsurf|storemeshtodatabase|surfacesplineonlinesloop)\s+(\d+)", text)
    ids += re.findall(r"\*meshdragelements\s+(\d+)", text)
    bad = sorted({i for i in ids if i not in ("1", "2")})
    return not bad, {"mark_ids": sorted(set(ids)), "bad": bad}


def check_tcl3(text):
    """TCL3 hm_answernext yes on the line before every *readfile / *writefile."""
    code = _code(text)
    bad = [l for k, l in enumerate(code) if re.match(r"\*(readfile|writefile)\b", l) and (k == 0 or code[k - 1] != "hm_answernext yes")]
    n = sum(1 for l in code if re.match(r"\*(readfile|writefile)\b", l))
    return not bad, {"file_commands": n, "bad": bad}


def check_tcl4(text):
    """TCL4 splitting by planes only: no line/surface split tools, *solid_split_by_tool only with tool_type=plane."""
    code = _code(text)
    bad = [l for l in code if re.match(r"\*surfacemarksplitwith(?!plane\b)", l) or re.match(r"\*surfacesplitwith(lines|surface)", l)
           or (l.startswith("*solid_split_by_tool") and "tool_type=plane" not in l)]
    return not bad, {"plane_splits": sum(1 for l in code if l.startswith("*surfacemarksplitwithplane")), "bad": bad}


def check_all(text):
    return {name: dict(zip(("ok", "detail"), fn(text))) for name, fn in
            (("TCL1", check_tcl1), ("TCL2", check_tcl2), ("TCL3", check_tcl3), ("TCL4", check_tcl4))}
