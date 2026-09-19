# -*- coding: utf-8 -*-
"""build step (runs under Simufact runscript). Copied from P0_A4 build_fullscript_v2.py; all inputs come from a params
JSON written by weldsim.py (argv[1]).
Rules enforced: R6' calculate_all_orientations once per trajectory on a NEW project only (refuses existing project),
no calculate_all_projections / delete_geometry / split_at; R14 imported weld lines are the outer-surface lines;
R16 heat source entity name checked; R17 search_radius from params (5 mm); R18 weld line set to local-vector;
hard rule 9 units mm. Saves, decodes robots_properties.xml, requires trajectory_modification blocks."""
import io
import json
import os
import re
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from common.qxml import robot_blobs  # noqa: E402
from simufact import UnitValueLength  # noqa: E402
from simufact.welding import new_project  # noqa: E402

P = json.load(io.open(sys.argv[1], encoding="utf-8"))
OUT = P["out_dir"]
R = {"params": sys.argv[1], "steps": []}


def log(k, v):
    R[k] = v
    io.open(os.path.join(OUT, "build_result.json"), "w", encoding="utf-8").write(json.dumps(R, ensure_ascii=False, indent=2) + "\n")


def step(s):
    R["steps"].append(s)
    log("steps", R["steps"])


def geom(proj, path, unit="mm"):
    kwargs = {"as_single_body": True}
    if unit:
        kwargs["unit"] = unit
    g = proj.import_geometry(path, **kwargs)
    if isinstance(g, (tuple, list)):
        g = [x for x in g if hasattr(x, "name")]
        assert len(g) == 1, path
        g = g[0]
    return g


def read_fixed(path):
    rows = []
    for l in io.open(path, encoding="utf-8"):
        s = l.strip()
        if not s or s.startswith("#") or s.lower().startswith("name;"):
            continue
        name, dirs, target, x, y, z = s.split(";")
        rows.append((name, tuple(dirs), target, (float(x), float(y), float(z))))
    return rows


def main():
    swproj = os.path.join(OUT, P["project_name"], P["project_name"] + ".swproj")
    if os.path.exists(swproj):
        raise RuntimeError("R6': project exists, calculate_all_orientations only allowed on a new project: " + swproj)
    proj = new_project(P["project_name"], OUT)
    proc = proj.new_process(name=P["process_name"], process_type="arc-welding", gravity=P["gravity"])
    t = P["temperature"]
    temp = proj.new_temperature(name=t["name"], initial_temperature=t["initial_C"], convective_htc=t["convective_htc"],
                                contact_htc=t["contact_htc"], emission_coefficient=t["emission"])
    m_base = proj.import_material(P["material_base"])
    m_fill = {s["label"]: proj.import_material(P["material_filler"]) for s in P["sides"]}
    log("materials", [str(m_base.name)] + [str(m.name) for m in m_fill.values()])
    base_geoms = {}
    for role in P.get("base_order", ["web", "plate"]):
        base_geoms[role] = geom(proj, P[role + "_bdf"])
    g_web, g_plate = base_geoms["web"], base_geoms["plate"]
    g_bead = {s["label"]: geom(proj, s["bead_bdf"]) for s in P["sides"]}
    for role in P.get("base_order", ["web", "plate"]):
        proc.new_component(name=P[role + "_name"], geometry=base_geoms[role], temperature=temp, material=m_base)
    log("geometries", [str(g.name) for g in [g_web, g_plate] + list(g_bead.values())])
    hs = proj.import_heat_source_parameters(P["heat_source_xml"])
    log("heat_source_name", str(hs.name))
    if str(hs.name) != P["heat_source_entity"]:
        raise RuntimeError("R16: heat source entity %s != %s" % (hs.name, P["heat_source_entity"]))
    robs = {}
    for s in P["sides"]:
        lab = s["label"]
        wl = proj.new_weld_line(name=s["weld_line_name"])
        wl.import_points(s["outer_line_csv"], field_separator=";", length_unit="mm")
        wl.orientation = "local-vector"                                   # R18
        rob = proc.new_robot(s["robot_name"])
        rob.material = m_fill[lab]
        rob.temperature = temp
        tr = rob.new_trajectory(wl)
        tr.new_geometry(g_bead[lab])
        rob.assign_heat_source_parameter(hs, True)
        step(lab + " calculate_all_orientations")
        tr.calculate_all_orientations(mode="component-center",                 # R6' once, R17 search radius
                                      search_radius=UnitValueLength(P["orientation_search_radius_mm"], "mm"))
        step(lab + " done")
        robs[lab] = {"length": str(tr.original_length), "beads": [str(g.name) for g in tr.all_geometries],
                     "heat_source": str(tr.heat_source_parameters.name) if tr.heat_source_parameters else None}
    log("robots", robs)
    counts = {}
    constraints = P.get("constraints", {"mode": "fixed_nodes"})
    if constraints.get("mode") == "fixed_nodes":
        for i, (name, dirs, target, xyz) in enumerate(read_fixed(P["fixed_nodes_csv"])):
            g = g_web if target == "web" else g_plate
            csv = os.path.join(OUT, "fixed_%02d_%s.csv" % (i, name))
            with io.open(csv, "w", encoding="ascii", newline="") as f:
                f.write("%s;%.4f;%.4f;%.4f\n" % ((str(g.name),) + xyz))                   # hard rule 8
            fn = proc.new_fixed_nodes(name=name, fixed_directions=dirs)
            fn.import_nodes(csv, decimal_separator=".", field_separator=";", unit="mm", search_radius=P["fixed_search_radius_mm"])
            counts[name] = len(list(fn.nodes))
    else:
        tooling = []
        for item in constraints.get("items", []):
            g = geom(proj, item["geometry"], item.get("unit"))
            if item["kind"] == "bearing":
                bc = proc.new_bearing(name=item["name"], geometry=g)
            else:
                bc = proc.new_clamping(name=item["name"], geometry=g)
                bc.definition = item.get("definition", "stiffness")
                bc.trans_stiffness = tuple(item.get("trans_stiffness", [1000.0, 1000.0]))
                bc.rot_stiffness = tuple(item.get("rot_stiffness", [0.0, 0.0]))
                if "direction" in item:
                    bc.direction = tuple(item["direction"])
                    bc.use_normalized_vector = item.get("use_normalized_vector", True)
            tooling.append(item["name"])
        log("tooling_count", len(tooling))
        log("tooling", tooling)
    log("fixed_node_counts", counts)
    if constraints.get("mode") == "fixed_nodes" and not all(v == 1 for v in counts.values()):
        raise RuntimeError("fixed node sets not exactly 1 node: %s" % counts)
    pp = proc.process_parameters
    pp.import_all_settings(P["process_parameters_xml"])
    pp.time_control.analysis_end_time = P["end_time_s"]
    pp.parallelization.on = True
    pp.parallelization.num_domains = P["solver_domains"]
    pp.parallelization.num_cores_per_domain = P["solver_threads_per_domain"]
    log("end_time", str(pp.time_control.analysis_end_time))
    proj.save()
    proc_dir = str(proc.path)
    log("process_path", proc_dir)
    parts = robot_blobs(os.path.join(proc_dir, "robots_properties.xml"))
    io.open(os.path.join(OUT, "robots_decoded_after_build.xml"), "w", encoding="utf-8").write("\n<!-- robot -->\n".join(parts))
    has = ["<trajectory_modification>" in x for x in parts]
    log("trajectory_modification_present", has)
    if len(parts) != len(P["sides"]) or not all(has):
        raise RuntimeError("trajectory_modification missing: %s" % has)
    log("phase", "saved")


try:
    main()
except Exception:
    log("phase", "failed")
    log("error", traceback.format_exc())
    raise
