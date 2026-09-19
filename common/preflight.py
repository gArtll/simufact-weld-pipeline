# -*- coding: utf-8 -*-
"""preflight: read-only checks on a case.json before any step runs. Starts no solver, writes no file.

Each check returns a list of Result(level, rule, message, value); level is block / warn / ok.
Rule ids and severities come from common/rules.yaml."""
import io
import json
import math
import os
import re
import xml.etree.ElementTree as ET
from collections import namedtuple

import yaml

from common.bdf import read_bdf, check_r1, read_weldline_csv, csv_ends_with_newline

HERE = os.path.dirname(os.path.abspath(__file__))
RULES_YAML = os.path.join(HERE, "rules.yaml")
Result = namedtuple("Result", "level rule message value")

# hexa faces (CHEXA node order) as opposite pairs
HEX_PAIRS = [((0, 1, 2, 3), (4, 5, 6, 7)), ((0, 1, 5, 4), (3, 2, 6, 7)), ((0, 3, 7, 4), (1, 2, 6, 5))]
MIN_LAYERS = 3
ADVANCE_RATIO = 0.5
COOLING_MAX_STEP_LIMIT_S = 25.0      # hard rule 7: the automatic default grows to 25 s


def load_rules(path=RULES_YAML):
    return {r["id"]: r for r in yaml.safe_load(io.open(path, encoding="utf-8"))["rules"]}


class Case:
    def __init__(self, case_path):
        self.path = os.path.abspath(case_path)
        self.base = os.path.dirname(self.path)
        self.c = json.load(io.open(self.path, encoding="utf-8"))

    def p(self, rel):
        return os.path.normpath(os.path.join(self.base, rel))

    def out_dir(self, work_root=None):
        if self.c.get("output_dir"):
            return self.p(self.c["output_dir"])
        return os.path.join(work_root or "", self.c.get("case_name", ""))

    def input_files(self):
        """(label, absolute path) of every input file named in case.json."""
        c, out = self.c, []

        def add(label, getter):
            try:
                v = getter()
            except (KeyError, TypeError, IndexError):
                out.append((label, None))
                return
            out.append((label, self.p(v) if v else None))

        if c.get("base"):                            # a CAD-based case meshes its own parts: no supplied meshes
            add("base.web_bdf", lambda: c["base"]["web_bdf"])
            add("base.plate_bdf", lambda: c["base"]["plate_bdf"])
        add("geometry.step", lambda: (c.get("geometry") or {})["step"])
        add("materials.base_xmt", lambda: c["materials"]["base_xmt"])
        add("materials.filler_xmt", lambda: c["materials"]["filler_xmt"])
        add("heat_source.xml", lambda: c["heat_source"]["xml"])
        add("process.parameters_xml", lambda: c["process"]["parameters_xml"])
        constraints = c.get("constraints") or {"mode": "fixed_nodes", "fixed_nodes_csv": c.get("fixed_nodes_csv")}
        if constraints.get("mode") == "fixed_nodes":
            add("fixed_nodes_csv", lambda: constraints.get("fixed_nodes_csv") or c["fixed_nodes_csv"])
        else:
            for k, item in enumerate(constraints.get("items", [])):
                add("constraints.items[%d].geometry" % k, lambda item=item: item["geometry"])
        for k, s in enumerate(c.get("sides", [])):
            add("sides[%d].window_csv" % k, lambda s=s: s["window_csv"])
        return out


# ---------------------------------------------------------------- checks
def check_paths(case, out_dir):
    """S3: no non-ASCII characters and no spaces in any path handed to the Simufact script runner."""
    res = []
    paths = [(l, p) for l, p in case.input_files() if p] + [("output_dir", out_dir), ("case file", case.path)]
    for label, p in paths:
        bad_ascii = [ch for ch in p if ord(ch) > 127]
        if bad_ascii:
            res.append(Result("block", "S3", "%s contains non-ASCII characters" % label, p))
        elif " " in p:
            res.append(Result("block", "S3", "%s contains a space" % label, p))
    if not res:
        res.append(Result("ok", "S3", "all %d paths ASCII, no spaces" % len(paths), None))
    return res


def check_inputs_exist(case):
    """PF1: every input file named in case.json exists."""
    files = case.input_files()
    miss = [(l, p) for l, p in files if not p or not os.path.isfile(p)]
    if miss:
        return [Result("block", "PF1", "input file missing: %s" % l, p) for l, p in miss]
    return [Result("ok", "PF1", "all %d input files exist" % len(files), None)]


def _parse_xml(path):
    return ET.parse(path).getroot()


def check_parse(case):
    """PF2: material xmt and heat source / process xml parse as XML with the expected root element."""
    c, res = case.c, []
    items = []
    for label, key, root in (("materials.base_xmt", ("materials", "base_xmt"), "sfMaterialData"),
                             ("materials.filler_xmt", ("materials", "filler_xmt"), "sfMaterialData"),
                             ("heat_source.xml", ("heat_source", "xml"), "wlWeldingParameter")):
        try:
            items.append((label, case.p(c[key[0]][key[1]]), root))
        except KeyError:
            continue
    for label, p, root in items:
        if not os.path.isfile(p):
            continue                                   # reported by PF1
        try:
            tag = _parse_xml(p).tag
        except ET.ParseError as exc:
            res.append(Result("block", "PF2", "%s is not valid XML" % label, "%s: %s" % (p, exc)))
            continue
        if tag != root:
            res.append(Result("block", "PF2", "%s root element is <%s>, expected <%s>" % (label, tag, root), p))
        else:
            res.append(Result("ok", "PF2", "%s parses, root <%s>" % (label, tag), os.path.basename(p)))
    return res


def heat_source_values(xml_path):
    r = _parse_xml(xml_path)
    name = r.find("name")
    cur = r.find("welding_parameter_data/current")
    vol = r.find("welding_parameter_data/voltage")
    vel = r.find("welding_parameter_data/velocity")
    return {"display_name": name.get("display_name") if name is not None else None,
            "current_A": float(cur.get("value")) if cur is not None else None,
            "voltage_V": float(vol.get("value")) if vol is not None else None,
            "velocity": float(vel.get("value")) if vel is not None else None}


def _numbers(text):
    """Current / voltage tokens in a name: '250A', '29V' -> {'A': [250.0], 'V': [29.0]}."""
    out = {"A": [], "V": []}
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([AaVv])(?![A-Za-z])", text):
        out[unit.upper()].append(float(num))
    return out


def check_heat_source(case):
    """R16: entity name == display name inside the xml. R12: entity name, file name and xml share the same A / V numbers."""
    c = case.c
    try:
        xml, entity = case.p(c["heat_source"]["xml"]), c["heat_source"]["entity_name"]
    except KeyError as exc:
        return [Result("block", "R16", "case.json heat_source field missing", str(exc))]
    if not os.path.isfile(xml):
        return []
    try:
        hv = heat_source_values(xml)
    except ET.ParseError:
        return []                                      # reported by PF2
    res = []
    if hv["display_name"] == entity:
        res.append(Result("ok", "R16", "heat source entity name matches xml display_name", entity))
    else:
        res.append(Result("block", "R16", "heat_source.entity_name differs from xml display_name",
                          "case=%r xml=%r" % (entity, hv["display_name"])))
    fname = os.path.splitext(os.path.basename(xml))[0]
    ne, nf = _numbers(entity), _numbers(fname)
    detail = "entity=%s file=%s xml=%gA/%gV" % (entity, fname, hv["current_A"] or 0, hv["voltage_V"] or 0)
    bad = []
    for unit, val in (("A", hv["current_A"]), ("V", hv["voltage_V"])):
        for label, nums in (("entity name", ne), ("file name", nf)):
            if nums[unit] != [val]:
                bad.append("%s %s=%s" % (label, unit, nums[unit] or "absent"))
    if bad:
        res.append(Result("block", "R12", "heat source current/voltage not identical in entity name, file name and xml (%s)" % ", ".join(bad), detail))
    else:
        res.append(Result("ok", "R12", "entity name, file name and xml carry the same current and voltage", detail))
    return res


def process_steps(xml_path):
    r = _parse_xml(xml_path)
    method = r.find(".//time_steps_method")
    fixed = r.find(".//fixed_time_step_value")
    cooling = []
    for b in r.iter("step_size_control"):
        if b.get("loadcase_type") != "2":
            continue
        m = b.find("maximum_time_step")
        cooling.append({"precision": b.get("precision_mode_type"), "solution": b.get("solver_solution_type"),
                        "is_automatic": None if m is None else m.get("is_automatic"),
                        "value": None if m is None else float(m.get("value"))})
    return {"time_steps_method": None if method is None else method.text.strip(),
            "fixed_time_step_s": None if fixed is None else float(fixed.get("value")), "cooling": cooling}


def check_process(case):
    """S7: every cooling step_size_control (loadcase_type 2) has is_automatic=false and 0 < value <= 25 s.
    PF3: welding stage uses fixed time steps (time_steps_method 0) with a positive fixed_time_step_value."""
    try:
        xml = case.p(case.c["process"]["parameters_xml"])
    except KeyError as exc:
        return [Result("block", "S7", "case.json process.parameters_xml missing", str(exc))]
    if not os.path.isfile(xml):
        return []
    try:
        st = process_steps(xml)
    except ET.ParseError as exc:
        return [Result("block", "PF2", "process.parameters_xml is not valid XML", str(exc))]
    res = []
    cool = st["cooling"]
    bad = [b for b in cool if b["is_automatic"] != "false" or b["value"] is None or not 0 < b["value"] <= COOLING_MAX_STEP_LIMIT_S]
    if not cool:
        res.append(Result("block", "S7", "no cooling step_size_control block (loadcase_type=2) in process xml", xml))
    elif bad:
        res.append(Result("block", "S7", "cooling maximum time step automatic or unbounded in %d/%d blocks" % (len(bad), len(cool)),
                          ["precision=%s solution=%s is_automatic=%s value=%s" % (b["precision"], b["solution"], b["is_automatic"], b["value"]) for b in bad]))
    else:
        res.append(Result("ok", "S7", "cooling max step fixed in %d/%d blocks" % (len(cool), len(cool)),
                          "max_time_step=%s s" % sorted({b["value"] for b in cool})))
    if st["time_steps_method"] != "0" or not st["fixed_time_step_s"] or st["fixed_time_step_s"] <= 0:
        res.append(Result("block", "PF3", "welding stage is not a fixed time step",
                          "time_steps_method=%s fixed_time_step_value=%s" % (st["time_steps_method"], st["fixed_time_step_s"])))
    else:
        res.append(Result("ok", "PF3", "welding stage fixed time step", "%g s" % st["fixed_time_step_s"]))
    return res, st["fixed_time_step_s"]


def check_advance(case, fixed_step_s):
    """R21: heat source advance per step = weld speed x fixed step <= section spacing x 0.5."""
    c = case.c
    try:
        v, h = float(c["process"]["weld_speed_mm_s"]), float(c["bead"]["section_spacing_mm"])
    except (KeyError, TypeError, ValueError) as exc:
        return [Result("block", "R21", "weld_speed_mm_s or bead.section_spacing_mm missing", str(exc))]
    if not fixed_step_s:
        return [Result("block", "R21", "advance per step not computable (no fixed welding step)", None)]
    adv, lim = v * fixed_step_s, h * ADVANCE_RATIO
    val = "%g mm/s x %g s = %.4f mm, limit %g x %g = %.4f mm" % (v, fixed_step_s, adv, h, ADVANCE_RATIO, lim)
    if adv > lim:
        return [Result("block", "R21", "heat source advance per step exceeds half the bead section spacing", val)]
    return [Result("ok", "R21", "heat source advance per step within half the bead section spacing", val)]


def check_speed_consistency(case):
    """PF5 (warn): heat source xml velocity equals process.weld_speed_mm_s."""
    try:
        xml, v = case.p(case.c["heat_source"]["xml"]), float(case.c["process"]["weld_speed_mm_s"])
        hv = heat_source_values(xml)
    except (KeyError, OSError, ET.ParseError, TypeError, ValueError):
        return []
    if hv["velocity"] is None or abs(hv["velocity"] - v) > 1e-6 * max(1.0, v):
        return [Result("warn", "PF5", "heat source xml velocity differs from process.weld_speed_mm_s", "xml=%s case=%s" % (hv["velocity"], v))]
    return [Result("ok", "PF5", "heat source xml velocity equals case weld speed", "%g mm/s" % v)]


def thickness_layers(nodes, elems, samples=400):
    """Element layers through the thinnest direction of a hexa body.
    For sampled elements walk the three opposite-face chains to the boundary; the chain with the smallest total length is
    the thickness direction. Returns (min layers, max layers, median thickness mm, sampled count)."""
    faces = {}
    for ei, e in enumerate(elems):
        for pair in HEX_PAIRS:
            for f in pair:
                faces.setdefault(frozenset(e[k] for k in f), []).append(ei)

    def centroid(ids):
        pts = [nodes[i] for i in ids]
        return tuple(sum(q[k] for q in pts) / len(pts) for k in range(3))

    def walk(ei, entry):
        """from element ei leaving through face `entry` (tuple of local indices): count elements and length"""
        n, length = 0, 0.0
        cur, out_face = ei, entry
        seen = set()
        while True:
            e = elems[cur]
            n += 1
            seen.add(cur)
            opp = next(b if a == out_face else a for a, b in HEX_PAIRS if out_face in (a, b))
            length += math.dist(centroid([e[k] for k in out_face]), centroid([e[k] for k in opp]))
            key = frozenset(e[k] for k in out_face)
            nb = [x for x in faces.get(key, []) if x != cur]
            if not nb or nb[0] in seen:
                return n, length
            cur = nb[0]
            ne = elems[cur]
            # face of the neighbour that is shared -> leave through its opposite
            shared = next(f for a, b in HEX_PAIRS for f in (a, b) if frozenset(ne[k] for k in f) == key)
            out_face = next(b if a == shared else a for a, b in HEX_PAIRS if shared in (a, b))

    if not elems:
        return 0, 0, 0.0, 0
    step = max(1, len(elems) // samples)
    layers, thick = [], []
    for ei in range(0, len(elems), step):
        best = None
        for a, b in HEX_PAIRS:
            n1, l1 = walk(ei, a)
            n2, l2 = walk(ei, b)
            n, l = n1 + n2 - 1, l1 + l2 - math.dist(centroid([elems[ei][k] for k in a]), centroid([elems[ei][k] for k in b]))
            if best is None or l < best[1]:
                best = (n, l)
        layers.append(best[0])
        thick.append(best[1])
    thick.sort()
    return min(layers), max(layers), thick[len(thick) // 2], len(layers)


def radial_thickness_layers(nodes, elems, center, axis="z", samples=400):
    """Topology walk in the locally radial opposite-face direction for a tube wall."""
    faces = {}
    for ei, e in enumerate(elems):
        for pair in HEX_PAIRS:
            for f in pair:
                faces.setdefault(frozenset(e[k] for k in f), []).append(ei)
    ai = {"x": 0, "y": 1, "z": 2}[axis]
    def centroid(e, face):
        return tuple(sum(nodes[e[k]][j] for k in face) / len(face) for j in range(3))
    def radius(q):
        return math.sqrt(sum((q[k] - center[k]) ** 2 for k in range(3) if k != ai))
    def walk(ei, entry):
        count, cur, out_face, seen = 0, ei, entry, set()
        while True:
            e = elems[cur]; count += 1; seen.add(cur)
            key = frozenset(e[k] for k in out_face)
            nb = [x for x in faces.get(key, []) if x != cur]
            if not nb or nb[0] in seen:
                return count
            cur = nb[0]; ne = elems[cur]
            shared = next(f for a, b in HEX_PAIRS for f in (a, b) if frozenset(ne[k] for k in f) == key)
            out_face = next(b if a == shared else a for a, b in HEX_PAIRS if shared in (a, b))
    vals = []
    step = max(1, len(elems) // samples)
    for ei in range(0, len(elems), step):
        e = elems[ei]
        pair = max(HEX_PAIRS, key=lambda ab: abs(radius(centroid(e, ab[0])) - radius(centroid(e, ab[1]))))
        vals.append(walk(ei, pair[0]) + walk(ei, pair[1]) - 1)
    vals.sort()
    radii = [radius(q) for q in nodes.values()]
    return min(vals), max(vals), vals[len(vals) // 2], max(radii) - min(radii), len(vals)


def check_base_mesh(case):
    """R1: base bdf is GRID* + CHEXA only. PF4: compare layers with the case threshold (default 3, warning only)."""
    res = []
    min_layers = int(case.c.get("preflight", {}).get("min_layers_through_thickness", MIN_LAYERS))
    if min_layers < 1:
        min_layers = MIN_LAYERS
    roles = case.c.get("base", {}).get("roles", {})
    for default_label, key in (("web", "web_bdf"), ("plate", "plate_bdf")):
        label = roles.get(default_label, default_label)
        try:
            p = case.p(case.c["base"][key])
        except KeyError:
            continue
        if not os.path.isfile(p):
            continue
        ok, st = check_r1(p)
        stat = "GRID*=%d GRID=%d GRID,=%d CHEXA=%d CPENTA=%d" % (st["grid_star"], st["grid_small"], st["grid_free"], st["chexa"], st["cpenta"])
        res.append(Result("ok" if ok else "block", "R1", "%s bdf %s" % (label, "is GRID* + CHEXA only" if ok else "has cards Simufact will not import"), stat))
        nodes, elems, _ = read_bdf(p)
        if not elems:
            res.append(Result("block", "PF4", "%s bdf has no CHEXA, thickness layers not countable" % label, p))
            continue
        rule = case.c.get("base", {}).get("thickness_rules", {}).get(default_label, {})
        if rule.get("mode") == "radial":
            lo, hi, med, t, n = radial_thickness_layers(nodes, elems, rule["center_mm"], rule.get("axis", "z"))
            val = "radial mode: min %d median %d max %d layers over %d samples, radial thickness %.3f mm" % (lo, med, hi, n, t)
            passes = med >= min_layers
        else:
            lo, hi, t, n = thickness_layers(nodes, elems)
            med = lo
            val = "min %d max %d layers over %d sampled elements, median thickness %.3f mm" % (lo, hi, n, t)
            passes = lo >= min_layers
        if not passes:
            res.append(Result("block", "PF4", "%s has fewer than %d element layers through the thickness" % (label, min_layers), val))
        else:
            res.append(Result("ok", "PF4", "%s thickness layers >= %d" % (label, min_layers), val))
    return res


def check_csv(case):
    """S8: window csv and fixed node csv end with a newline; window csv has >= 2 active points."""
    res = []
    files = [(l, p) for l, p in case.input_files() if l.endswith("window_csv") or l == "fixed_nodes_csv"]
    for label, p in files:
        if not p or not os.path.isfile(p):
            continue
        if not csv_ends_with_newline(p):
            res.append(Result("block", "S8", "%s last line has no newline" % label, p))
        else:
            res.append(Result("ok", "S8", "%s ends with newline" % label, os.path.basename(p)))
        if label.endswith("window_csv"):
            n = len(read_weldline_csv(p))
            if n < 2:
                res.append(Result("block", "S8", "%s has %d active points (order;true;x;y;z), need >= 2" % (label, n), p))
    return res


def check_output(case, out_dir):
    """S1: target project must not exist (scripts run on disposable new projects only).
    S2: no project.lock anywhere under the output directory (project open in Simufact / stale lock)."""
    res = []
    locks = []
    if os.path.isdir(out_dir):
        for root, _, fs in os.walk(out_dir):
            locks += [os.path.join(root, f) for f in fs if f.lower() == "project.lock"]
    if locks:
        res.append(Result("block", "S2", "project.lock present under output dir (%d)" % len(locks), locks[:5]))
    else:
        res.append(Result("ok", "S2", "no project.lock under output dir", out_dir))
    target = os.path.join(out_dir, "02_build", case.c.get("case_name", ""))
    if os.path.exists(target):
        res.append(Result("block", "S1", "target project already exists, build would reopen it", target))
    else:
        res.append(Result("ok", "S1", "target project does not exist yet", target))
    return res


def check_search_radius(case):
    r = case.c.get("process", {}).get("orientation_search_radius_mm", 5.0)
    if r != 5.0:
        return [Result("warn", "R17", "orientation search radius is not 5 mm", "%s mm" % r)]
    return [Result("ok", "R17", "orientation search radius 5 mm", "%s mm" % r)]


# ---------------------------------------------------------------- driver
CHECKS = ("paths", "exist", "parse", "heat_source", "process", "base_mesh", "csv", "output", "search_radius",
          "provenance", "capability", "contact")
REPO_CASES = os.path.join(os.path.dirname(HERE), "cases")


def check_provenance(case, case_dirs=None):
    """PV0-PV4: every critical parameter has a source; nothing borrowed from another case without approval."""
    from common import provenance
    dirs = [REPO_CASES] + list(case_dirs or [])
    return provenance.check(case.c, case.path, case.input_files(), dirs, lambda p: Case(p).input_files(), Result)


def check_capability(case, out_dir):
    """CAP1: a case built from CAD must have a plan, and the plan must have no capability gap.

    A regression case built from supplied meshes (no `geometry.step`) is exempt: it reproduces a finished study."""
    step = (case.c.get("geometry") or {}).get("step")
    if not step:
        if case.c.get("role") == "regression":
            return [Result("ok", "CAP1", "regression case from supplied meshes: no capability plan needed", None)]
        return [Result("block", "CAP1", "case has no geometry.step: a new case starts from the CAD, "
                       "run `weldsim.py new` / `inspect`", None)]
    pj = os.path.join(out_dir, "plan", "plan.json")
    if not os.path.isfile(pj):
        return [Result("block", "CAP1", "no capability plan yet: run `weldsim.py plan <case>`", pj)]
    pl = json.load(io.open(pj, encoding="utf-8"))
    if pl.get("status") == "gap":
        return [Result("block", "CAP1", "capability gap: %s" % json.dumps(pl.get("gaps"), ensure_ascii=False), pj)]
    if pl.get("status") == "unverified":
        nv = [x for j in pl.get("joints", []) for x in j.get("not_verified", [])]
        return [Result("block", "CAP1", "detected and meshable, but not supported for a formal run until verified "
                       "end to end: %s" % "; ".join(nv), pj)]
    return [Result("ok", "CAP1", "capability plan %s" % pl.get("status"), pj)]


def check_contact(case, out_dir):
    """CT1: contact from the capability plan (or the regression baseline), alternatives only with a source."""
    from common import contact
    if case.c.get("role") != "regression" and not case.c.get("contact_ruleset"):
        return []                                   # not chosen yet: PV1 already blocks, nothing to judge here
    return contact.check(case.c, contact.load_plan(out_dir), Result)


def run_preflight(case_path, work_root=None, only=None, case_dirs=None):
    case = Case(case_path)
    out_dir = case.out_dir(work_root)
    sel = set(only or CHECKS)
    res = []
    if "paths" in sel:
        res += check_paths(case, out_dir)
    if "exist" in sel:
        res += check_inputs_exist(case)
    if "parse" in sel:
        res += check_parse(case)
    if "heat_source" in sel:
        res += check_heat_source(case)
        res += check_speed_consistency(case)
    if "process" in sel:
        pr = check_process(case)
        if isinstance(pr, tuple):
            r, fixed = pr
            res += r
            res += check_advance(case, fixed)
        else:
            res += pr
    if "base_mesh" in sel:
        res += check_base_mesh(case)
    if "csv" in sel:
        res += check_csv(case)
    if "output" in sel:
        res += check_output(case, out_dir)
    if "search_radius" in sel:
        res += check_search_radius(case)
    if "provenance" in sel:
        res += check_provenance(case, case_dirs)
    if "capability" in sel:
        res += check_capability(case, out_dir)
    if "contact" in sel:
        res += check_contact(case, out_dir)
    rules = load_rules()
    # severity from the rule library: a 'warn' rule never blocks
    res = [r._replace(level="warn") if r.level == "block" and rules.get(r.rule, {}).get("severity") == "warn" else r for r in res]
    return res


def summarize(results):
    n = {k: sum(1 for r in results if r.level == k) for k in ("block", "warn", "ok")}
    return n


def format_results(results, case_path):
    lines = ["preflight %s" % os.path.abspath(case_path)]
    for r in results:
        s = "[%s] %s %s" % (r.level, r.rule, r.message)
        if r.value not in (None, ""):
            s += " | %s" % (r.value,)
        lines.append(s)
    n = summarize(results)
    lines.append("summary: block=%d warn=%d ok=%d -> %s" % (n["block"], n["warn"], n["ok"], "FAIL" if n["block"] else "PASS"))
    return "\n".join(lines)
