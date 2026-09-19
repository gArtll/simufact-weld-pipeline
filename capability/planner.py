# -*- coding: utf-8 -*-
"""Joint features -> a plan, or capability gaps.

The planner looks each classified joint up in capability/matrix.yaml by its features. A supported combination comes
back with the backends to use and the list of inputs the user has to supply for this part (tooling, process); an
unsupported one comes back as a gap, with what is missing and the official references that show the method. There is
no fallback to "the closest existing case": a gap stops the pipeline."""
import io
import os

import numpy as np
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MATRIX = os.path.join(HERE, "matrix.yaml")
REFERENCES = os.path.join(ROOT, "official", "references.yaml")


def load_matrix(path=MATRIX):
    return yaml.safe_load(io.open(path, encoding="utf-8"))


def load_references(path=REFERENCES):
    return yaml.safe_load(io.open(path, encoding="utf-8"))


def joint_features(features, joint):
    bodies = {b["body"]: b for b in features["bodies"]}
    st, base = bodies[joint["standing"]["body"]], bodies[joint["base"]["body"]]
    paths = sorted({s["path"] for s in joint["seams"]}) or ["unknown"]
    return {"joint": joint["type"], "standing_kind": st["kind"], "base_kind": base["kind"],
            "standing_surface": joint["standing_surface"], "seam_path": paths[0] if len(paths) == 1 else "mixed",
            "groove": "prepared" if joint.get("groove") else "none",
            "parts": "two" if features["counts"]["bodies"] == 2 else "many"}


def _matches(when, f):
    for k, v in when.items():
        want = v if isinstance(v, list) else [v]
        if f.get(k) not in want:
            return False
    return True


def base_backend(st, base, joint):
    """Mesh backend of the base plate, from its sections along the standing part's thickness axis.

      equal section     explicit_plate_sweep   one profile swept, graded away from the weld (mesh_transition/plate.py)
      variable section  mapped_plate           end profiles of the same topology mapped station by station, levels
                                               projected onto the real faces (mesh_transition/mapped_plate.py)
      anything else     none                   BLOCK: no backend follows it

    Also names the faces the backends start from, so the hm block of a case can be filled from the plan."""
    axis = st.get("normal_axis")
    sec = (base.get("sections") or {}).get(axis) if axis else None
    out = {"standing_face": None, "root_geometry_ids": [], "base_profile_face": None}
    seams = sorted(joint["seams"], key=lambda s: s["on_face"])
    if axis and seams:
        k = "xyz".index(axis)
        lowest = min(seams, key=lambda s: sum(p[k] for p in s["points_mm"]) / len(s["points_mm"]))
        out.update(standing_face=lowest["on_face"], root_geometry_ids=lowest["geometry_ids"])
    if sec is None:
        out.update(base="none", base_reason="base part has no pair of end faces normal to the standing part's "
                                            "thickness axis %s: it is not a sweep of one profile along it" % axis)
    elif sec["section"] == "equal":
        out.update(base="explicit_plate_sweep", base_profile_face=sec["end_faces"][0],
                   base_reason="equal section along %s" % axis)
    else:
        out.update(base="mapped_plate", base_profile_face=sec["end_faces"][0],
                   base_reason="variable section along %s (end profiles differ by up to %.3f mm)"
                               % (axis, sec["profile_gap_mm"]))
    return out


def plan(features, matrix=None, refs=None):
    matrix = matrix or load_matrix()
    refs = refs or load_references()
    bodies = {b["body"]: b for b in features["bodies"]}
    out = {"step": features.get("step"), "joints": [], "gaps": [], "status": "supported"}
    if not features["joints"]:
        out["gaps"].append({"feature": "no joint found", "detail": "no face of one body lies on another body"})
    for j in features["joints"]:
        f = joint_features(features, j)
        hit = next((c for c in matrix["capabilities"] if _matches(c["when"], f)), None)
        # recognising a joint is not being able to build it: `supported` is set below from the matrix status only
        entry = {"detected_joint": f, "detected": True, "seams": j["seams"], "sides_possible": j["sides_possible"],
                 "frame": j.get("frame")}
        if hit is None:
            entry.update(capability=None, status="gap",
                         unsupported_features=["no capability for %s" % f])
        else:
            entry.update(capability=hit["id"], status=hit["status"],
                         official_references=[dict(refs["references"][r], id=r)
                                              for r in hit.get("official_references", []) if r in refs["references"]])
            if hit["status"] == "gap":
                entry["unsupported_features"] = hit.get("missing", [])
            else:
                unsupported = []
                if f["seam_path"] not in hit.get("seam_paths", [f["seam_path"]]):
                    unsupported.append("seam path %s not in %s" % (f["seam_path"], hit["seam_paths"]))
                mb = dict(hit["mesh_backend"])
                if mb.get("base") == "plate_by_section":
                    mb.update(base_backend(bodies[j["standing"]["body"]], bodies[j["base"]["body"]], j))
                    if mb["base"] == "none":
                        unsupported.append("base part mesh: %s" % mb["base_reason"])
                ver = (hit.get("backend_verification") or {}).get(mb.get("base"))
                if ver and ver.get("status") != "verified":
                    entry["not_verified"] = ["%s: %s" % (mb["base"], ver.get("missing", "not run end to end"))]
                entry.update(required_mesh_backend=mb, bead_strategy=hit["bead_strategy"],
                             contact_strategy=hit["contact"], evidence=hit.get("evidence"),
                             tooling_inputs_required=hit["tooling_inputs_required"],
                             process_inputs_required=hit["process_inputs_required"],
                             unsupported_features=unsupported)
                if unsupported:
                    entry["status"] = "gap"
                elif entry.get("not_verified"):
                    entry["status"] = "unverified"
        entry["supported"] = entry["status"] == "supported"
        out["joints"].append(entry)
        if entry["status"] == "gap":
            out["gaps"].append({"joint": f, "capability": entry.get("capability"),
                                "missing": entry.get("unsupported_features")})
    if len(features["joints"]) > 1:
        out["gaps"].append({"feature": "more than one joint", "detail": "joint graph and weld sequence planning "
                                                                        "are not implemented (capability multi_part_assembly)"})
    if out["gaps"]:
        out["status"] = "gap"
    elif any(e["status"] == "unverified" for e in out["joints"]):
        out["status"] = "unverified"
    elif any(e["status"] == "partial" for e in out["joints"]):
        out["status"] = "partial"
    return out
