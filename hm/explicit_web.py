# -*- coding: utf-8 -*-
"""00_hm explicit-web backend: CAD face -> recognised outline -> explicit transition mesh -> web BDF.

    case.json "explicit_web" block
      -> geometry recognition (mesh_transition.web.outline)
      -> supported?  yes: mesh_transition.web build / finish, checks, base_web.bdf
                     no : the reason is recorded; weldsim.step_hm falls back to the automesh backend (hm/pipeline.py)
                          when the case allows it

Scope of this first stage, and what is refused rather than attempted:

  supported    one planar face lying in a coordinate plane, one weld root, plate ends next to it, a far boundary
               that may be cut by notches; tangent breaks inside the root; thickness from a parallel face
  unsupported  a face that is not a plane (curved web), a plane that is not a coordinate plane (oblique web),
               an outline that does not split into root / two ends / far boundary, notch feet that cross

Nothing here is specific to a part. Every size comes from the case block."""
import copy
import io
import os

from common.bdf import check_r1, write_bdf

DEFAULTS = {
    "step": None,                   # STEP file, relative to the case file
    "face": None,                   # ADVANCED_FACE id of the web face to mesh
    "root_geometry_ids": [],        # weld root edges; [] = the longest side of the outline
    "fine_mm": 5.0,
    "coarse_mm": 13.0,
    "far_mm": 14.5,
    "buffer_rows": 2,
    "coarse_rows": 2,
    "layers": 2,
    "thickness_mm": None,           # None = from the parallel face
    "smooth_passes": 25,
    "fallback": "automesh",         # "automesh" or "fail" when the geometry is unsupported
    "plate_bdf": None,              # plate mesh handed on unchanged (relative to the case file); ignored with `plate`
    "plate": None,                  # mesh the plate too: see PLATE_DEFAULTS
    "constraints": [],              # [{"name", "body": "web"|"plate", "dirs": "xyz", "point_mm": [x, y, z]}]
    "deck": {"component_id": 1, "component_name": "web", "component_color": 36, "pid": 0,
             "node_base": 1, "element_base": 1},
    "acceptance": {"min_scaled_jacobian": 0.3, "outer_min_cell_mm": 11.0, "max_outer_flagged_run": 3,
                   "max_boundary_deviation_mm": 0.05},
}


PLATE_DEFAULTS = {
    "face": None,                   # profile face at one end of the extruded plate
    "along_mm": 5.0,                # cell length along the weld
    "layers": 3,                    # through the plate thickness
    "fine_mm": 5.0,                 # across the weld, inside the fine zone
    "far_mm": 16.0,                 # across the weld, at the plate edges
    "growth": 1.3,                  # largest ratio between neighbouring layers across the weld
    "weld_leg_mm": 6.0,             # the fine zone is the web thickness plus leg plus margin on each side
    "fine_margin_mm": 10.0,
    # explicit_plate_sweep: one profile swept (mesh_transition/plate.py; refuses a plate whose end profiles differ)
    # mapped_plate: end profiles of one topology mapped level by level onto the CAD faces (mapped_plate.py)
    # auto: the sweep when the end profiles agree, the mapped plate when they do not
    "backend": "auto",
    "deck": {"component_id": 2, "component_name": "plate", "component_color": 55, "pid": 0,
             "node_base": 1, "element_base": 1},
}


class ExplicitWebError(ValueError):
    pass


def normalize(block):
    p = copy.deepcopy(DEFAULTS)
    for k, v in (block or {}).items():
        if k not in p:
            raise ExplicitWebError("explicit_web: unknown field %r" % k)
        if isinstance(p[k], dict):
            for kk, vv in v.items():
                if kk not in p[k]:
                    raise ExplicitWebError("explicit_web.%s: unknown field %r" % (k, kk))
                p[k][kk] = vv
        else:
            p[k] = v
    for k in ("step", "face"):
        if p[k] is None:
            raise ExplicitWebError("explicit_web: missing %s" % k)
    for k in ("fine_mm", "coarse_mm", "far_mm"):
        if not float(p[k]) > 0:
            raise ExplicitWebError("explicit_web.%s must be > 0" % k)
    if not float(p["fine_mm"]) < float(p["coarse_mm"]) <= float(p["far_mm"]) * 1.5:
        raise ExplicitWebError("explicit_web: need fine_mm < coarse_mm, and far_mm not far below coarse_mm")
    r = float(p["coarse_mm"]) / float(p["fine_mm"])
    if not 2.0 <= r <= 3.0:
        raise ExplicitWebError("explicit_web: coarse_mm / fine_mm = %.3f; one T2/T3 row covers 2..3" % r)
    if int(p["buffer_rows"]) < 1 or int(p["coarse_rows"]) < 1 or int(p["layers"]) < 1:
        raise ExplicitWebError("explicit_web: buffer_rows, coarse_rows, layers >= 1")
    if p["plate"] is not None:
        q = copy.deepcopy(PLATE_DEFAULTS)
        for k, v in p["plate"].items():
            if k not in q:
                raise ExplicitWebError("explicit_web.plate: unknown field %r" % k)
            if isinstance(q[k], dict):
                q[k].update(v)
            else:
                q[k] = v
        if q["face"] is None:
            raise ExplicitWebError("explicit_web.plate: missing face")
        if q["backend"] not in ("auto", "explicit_plate_sweep", "mapped_plate"):
            raise ExplicitWebError("explicit_web.plate.backend must be auto, explicit_plate_sweep or mapped_plate")
        p["plate"] = q
    for c in p["constraints"]:
        missing = [k for k in ("name", "body", "dirs", "point_mm") if k not in c]
        if missing or c["body"] not in ("web", "plate") or not set(c["dirs"]) <= set("xyz") or len(c["point_mm"]) != 3:
            raise ExplicitWebError("explicit_web.constraints: bad entry %r" % (c,))
        if c["body"] == "plate" and p["plate"] is None:
            raise ExplicitWebError("explicit_web.constraints: %r is on the plate, but the plate is not meshed here"
                                   % c["name"])
    if p["fallback"] not in ("automesh", "fail"):
        raise ExplicitWebError("explicit_web.fallback must be 'automesh' or 'fail'")
    return p


def web_params(p, step_path):
    from mesh_transition.web import WebParams
    return WebParams(step_path=step_path, face=int(p["face"]), root=tuple(p["root_geometry_ids"]),
                     fine=float(p["fine_mm"]), coarse=float(p["coarse_mm"]), far=float(p["far_mm"]),
                     buffer_rows=int(p["buffer_rows"]), coarse_rows=int(p["coarse_rows"]), layers=int(p["layers"]),
                     thickness=None if p["thickness_mm"] is None else float(p["thickness_mm"]),
                     smooth_passes=int(p["smooth_passes"]),
                     hard_points=tuple(tuple(float(v) for v in c["point_mm"]) for c in p["constraints"]
                                       if c["body"] == "web"))


def support(step_path, face, root_ids=()):
    """(supported, reasons, summary). Only reads and recognises the geometry; builds nothing."""
    from mesh_transition.step import faces, parse
    from mesh_transition.web import SIDE_CORNER_DEG, outline, plane_of, thickness_of
    reasons, summary = [], {}
    if not os.path.isfile(step_path):
        return False, ["STEP file not found"], summary
    ents = parse(step_path)
    f = [x for x in faces(ents) if x["face"] == face]
    if not f:
        return False, ["face #%d is not an ADVANCED_FACE in the file" % face], summary
    kind = f[0]["surface_kind"]
    summary["surface_kind"] = kind
    if kind != "PLANE":
        return False, ["unsupported: non-planar face (%s)" % kind], summary
    try:
        k, uv, off = plane_of(ents, face)
    except ValueError as exc:
        return False, ["unsupported: oblique plane (%s)" % exc], summary
    try:
        summary["thickness_mm"] = abs(thickness_of(ents, face))
    except ValueError as exc:
        reasons.append("no parallel face for the thickness: %s" % exc)
    try:
        g = outline(ents, face, tuple(root_ids))
    except (ValueError, IndexError) as exc:
        return False, reasons + ["unsupported outline: %s" % exc], summary
    summary.update(edges=g["edge_count"], sides=g["side_count"], root_geometry_ids=g["root_ids"],
                   notches=len(g["notches"]), root_tangent_breaks=g["root_breaks"],
                   side_corner_threshold_deg=SIDE_CORNER_DEG)
    if g["side_count"] < 3:
        reasons.append("unsupported outline: %d sides, need root, two ends and a far boundary" % g["side_count"])
    big = [b for b in g["root_breaks"] if b["turn_deg"] >= 90.0]
    if big:
        reasons.append("unsupported root: tangent break of %.1f deg" % big[0]["turn_deg"])
    return not reasons, reasons, summary


def run_explicit(block, case_dir, out_dir, log=print):
    """Mesh the web. Returns a result dict; result["supported"] False means nothing was written."""
    from mesh_transition.nastran import write_deck
    from mesh_transition.step import parse
    from mesh_transition.web import boundary_deviation, build, finish, outward_cells
    p = normalize(block)
    step_path = os.path.normpath(os.path.join(case_dir, p["step"]))
    ok, reasons, summary = support(step_path, int(p["face"]), p["root_geometry_ids"])
    res = {"backend": "explicit_web", "params": p, "supported": ok, "unsupported_reasons": reasons,
           "geometry": summary}
    if not ok:
        log("explicit_web: unsupported (%s)" % "; ".join(reasons))
        return res
    wp = web_params(p, step_path)
    try:
        m, zones, rep, g = build(wp)
        solid, rep = finish(wp, m, zones, rep, g)
    except ValueError as exc:
        res.update(supported=False, unsupported_reasons=["mesher refused the geometry: %s" % exc])
        log("explicit_web: mesher refused (%s)" % exc)
        return res
    outer = outward_cells(m, zones, "coarse%d" % wp.coarse_rows, ("middle", "far_field", "under_notch"),
                          float(p["acceptance"]["outer_min_cell_mm"]))
    dev = boundary_deviation(parse(step_path), wp.face, m, wp.swap_uv)

    # R1 deck for the rest of the pipeline (GRID* large field, CHEXA only), and the HyperMesh-flavoured one to look at
    order = sorted(solid.nodes)
    idx = {n: i + 1 for i, n in enumerate(order)}
    r1 = os.path.join(out_dir, "base_web.bdf")
    write_bdf(r1, [solid.nodes[n] for n in order], [[idx[i] for i in ids] for kind, ids in solid.elements],
              "explicit_web %s face %d" % (os.path.basename(step_path), wp.face))
    r1_ok, r1_stats = check_r1(r1)
    d = p["deck"]
    hmdeck = write_deck(solid, os.path.join(out_dir, "base_web_hm.bdf"), comp_id=int(d["component_id"]),
                        comp_name=str(d["component_name"]), comp_color=int(d["component_color"]), pid=int(d["pid"]),
                        node_base=int(d["node_base"]), elem_base=int(d["element_base"]),
                        title="explicit_web face %d" % wp.face)
    f, s, acc = rep["face"], rep["solid"], p["acceptance"]
    checks = {
        "2D all quadrilateral": f["triangles"] == 0 and f["quads"] > 0,
        "2D topology (hanging, duplicate, zero area, inverted, non-manifold, orphan, Euler)": f["topology_ok"],
        "2D no self intersection": f["self_intersections"] == 0,
        "3D all CHEXA, no CPENTA": s["penta6"] == 0 and s["hex8"] == s["elements"] > 0 and not s["other_kinds"],
        "3D no collapsed / zero-volume / negative-volume / non-positive Jacobian": (
            s["collapsed_elements"] == 0 and s["zero_volume_elements"] == 0 and s["negative_volume_elements"] == 0
            and s["non_positive_jacobian_elements"] == 0),
        "3D faces shared by at most two elements, layer nodes consistent": (
            s["faces_shared_by_more_than_two_count"] == 0 and s["layer_nodes_ok"]),
        "3D min scaled Jacobian >= %.3g" % acc["min_scaled_jacobian"]: s["min_scaled_jacobian"] >= acc["min_scaled_jacobian"],
        "no continuous re-refinement beyond the band (run <= %d columns)" % acc["max_outer_flagged_run"]:
            outer["longest_flagged_run"] <= int(acc["max_outer_flagged_run"]),
        "boundary nodes on the CAD edges (<= %.3g mm)" % acc["max_boundary_deviation_mm"]:
            dev["max_deviation_mm"] <= float(acc["max_boundary_deviation_mm"]),
        "R1 base_web.bdf": r1_ok,
    }
    res.update(report=rep, outer_cells=outer, boundary=dev, r1=r1_stats, hm_deck=hmdeck, checks=checks,
               ok=all(checks.values()))
    if p["plate"] is not None:
        res["plate"] = run_plate(p, step_path, rep, g, out_dir)
        res["checks"].update(res["plate"]["checks"])
    if p["constraints"]:
        res["constraints"] = write_constraints(p, out_dir)
        res["checks"]["every constraint point is a node (<= 0.01 mm)"] = res["constraints"]["max_distance_mm"] <= 0.01
    res["ok"] = all(res["checks"].values())
    if p["plate_bdf"] and p["plate"] is None:
        import shutil
        shutil.copyfile(os.path.normpath(os.path.join(case_dir, p["plate_bdf"])), os.path.join(out_dir, "base_plate.bdf"))
        res["plate_bdf"] = "copied from case"
    log("explicit_web: %d CHEXA, %d CPENTA, min SJ %.4f, %s" % (s["hex8"], s["penta6"], s["min_scaled_jacobian"],
                                                                "ok" if res["ok"] else "FAILED"))
    return res


def run_plate(p, step_path, web_rep, g, out_dir):
    """Mesh the plate the web stands on: mapped profile, extrusion graded away from the weld."""
    from mesh_transition.nastran import write_deck
    from mesh_transition.plate import PlateParams, build as build_plate
    from mesh_transition.step import parse
    from mesh_transition.web import plane_of
    q = p["plate"]
    sw = web_rep["solid"]["sweep"]
    w0, w1 = sw["from"], sw["from"] + sw["thickness_mm"]
    reach = float(q["weld_leg_mm"]) + float(q["fine_margin_mm"])
    # the web's root start, from the web's in-plane coordinates to the plate profile's, through 3D: the two faces
    # need not use the same in-plane axis order
    ents = parse(step_path)
    root3 = [0.0, 0.0, 0.0]
    root3["xyz".index(g["uv_axes"][0])], root3["xyz".index(g["uv_axes"][1])] = g["root"][0]
    root3["xyz".index(sw["axis"])] = w0
    _, puv, _ = plane_of(ents, int(q["face"]))
    pp = PlateParams(step_path=step_path, face=int(q["face"]), weld_point=(root3[puv[0]], root3[puv[1]]),
                     along=float(q["along_mm"]), layers=int(q["layers"]), fine=float(q["fine_mm"]),
                     far=float(q["far_mm"]), growth=float(q["growth"]), fine_intervals=((w0 - reach, w1 + reach),),
                     keep_levels=(w0, w1),
                     hard_points=tuple(tuple(float(v) for v in c["point_mm"]) for c in p["constraints"]
                                       if c["body"] == "plate"))
    if sw["axis"] != "xyz"[plane_of(parse(step_path), pp.face)[0]]:
        raise ExplicitWebError("the plate is not extruded along the web's thickness axis %s" % sw["axis"])
    backend, why = q["backend"], None
    if backend in ("auto", "explicit_plate_sweep"):
        try:
            solid, face, rep = build_plate(pp)
            backend = "explicit_plate_sweep"
        except ValueError as exc:
            if backend != "auto" or "not a straight extrusion" not in str(exc):
                raise
            why = str(exc)
            backend = "mapped_plate"
    if backend == "mapped_plate":
        from mesh_transition.mapped_plate import build as build_mapped
        solid, face, rep = build_mapped(pp)
    order = sorted(solid.nodes)
    idx = {n: i + 1 for i, n in enumerate(order)}
    r1 = os.path.join(out_dir, "base_plate.bdf")
    write_bdf(r1, [solid.nodes[n] for n in order], [[idx[i] for i in ids] for _, ids in solid.elements],
              "explicit plate face %d" % pp.face)
    ok_r1, st = check_r1(r1)
    d = q["deck"]
    deck = write_deck(solid, os.path.join(out_dir, "base_plate_hm.bdf"), comp_id=int(d["component_id"]),
                      comp_name=str(d["component_name"]), comp_color=int(d["component_color"]), pid=int(d["pid"]),
                      node_base=int(d["node_base"]), elem_base=int(d["element_base"]), title="explicit plate")
    s = rep["solid"]
    checks = {
        "plate all CHEXA, no CPENTA": s["penta6"] == 0 and s["hex8"] == s["elements"] > 0,
        "plate no collapsed / zero-volume / negative-volume / non-positive Jacobian": (
            s["collapsed_elements"] == 0 and s["zero_volume_elements"] == 0 and s["negative_volume_elements"] == 0
            and s["non_positive_jacobian_elements"] == 0),
        "plate faces shared by at most two elements, layer nodes consistent": (
            s["faces_shared_by_more_than_two_count"] == 0 and s["layer_nodes_ok"]),
        "plate min scaled Jacobian >= %.3g" % p["acceptance"]["min_scaled_jacobian"]:
            s["min_scaled_jacobian"] >= p["acceptance"]["min_scaled_jacobian"],
        "R1 base_plate.bdf": ok_r1,
    }
    if backend == "mapped_plate":
        checks["plate boundary nodes on their CAD faces (<= 0.001 mm)"] = (
            rep["projection"]["max_distance_to_face_mm"] <= 1e-3)
    return {"backend": backend, "backend_reason": why, "report": rep, "r1": st, "hm_deck": deck, "checks": checks,
            "fine_zone": [round(w0 - reach, 4), round(w1 + reach, 4)]}


def write_constraints(p, out_dir):
    """The constraint points as the build step reads them (name;dirs;target;x;y;z), with the node each one is."""
    import math
    from mesh_transition.face import read_solid_bdf
    meshes = {b: read_solid_bdf(os.path.join(out_dir, "base_%s.bdf" % b))[0] for b in ("web", "plate")
              if os.path.isfile(os.path.join(out_dir, "base_%s.bdf" % b))}
    rows, worst = [], 0.0
    with io.open(os.path.join(out_dir, "fixed_nodes.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("name;dirs;target;x;y;z\n")
        for c in p["constraints"]:
            q = [float(v) for v in c["point_mm"]]
            nodes = meshes[c["body"]]
            nid, xyz = min(nodes.items(), key=lambda kv: math.dist(kv[1], q))
            d = math.dist(xyz, q)
            worst = max(worst, d)
            rows.append({"name": c["name"], "body": c["body"], "dirs": c["dirs"], "point_mm": q, "node": nid,
                         "node_mm": list(xyz), "distance_mm": round(d, 6)})
            f.write("%s;%s;%s;%.6f;%.6f;%.6f\n" % (c["name"], c["dirs"],
                                                    c["body"], xyz[0], xyz[1], xyz[2]))
    io.open(os.path.join(out_dir, "constraints_resolved.json"), "w", encoding="utf-8").write(
        __import__("json").dumps(rows, ensure_ascii=False, indent=1))
    return {"count": len(rows), "max_distance_mm": round(worst, 6), "file": "fixed_nodes.csv"}
