# -*- coding: utf-8 -*-
"""Parameter provenance: every value that depends on the part, the process or the machine says where it came from.

Why this exists. A new model was once prepared by taking the tooling, supports, heat source, material, weld speed, time
step, cooling time and solver parallelism of the cases already in this repository -- silently, because they were
there. Nothing in the tool could tell a value measured on the new part from one copied off an old one. Now every
critical parameter of a case carries a source, and preflight refuses to start a formal computation while any of them
is unexplained, or borrowed from another case without the user's explicit approval.

What may be inherited without a source: algorithm defaults (mesh templates, row counts, growth limits), quality
thresholds and numerical rules. They describe the tool, not the part, and are not in CRITICAL below.

Borrowing is detected by content as well as by label: an input file byte-identical to an input of a registered
regression case is borrowed, whatever the case says about it."""
import datetime
import glob
import hashlib
import io
import json
import os

SOURCES = {
    "cad_measured": "measured on the CAD of this part",
    "simufact_official_example": "method from an official Simufact example / InfoSheet (never its numbers for another part)",
    "user_confirmed": "the user confirmed this value for this part",
    "process_document": "welding procedure specification, drawing or process sheet of this part",
    "derived": "computed by the tool from other values of this case",
    "estimated": "an estimate: allowed with a warning, listed for review",
    "borrowed_case": "taken from another case: blocked unless the user approved it explicitly",
    "machine_probe": "hardware facts of the machine that will run the job",
    "benchmark_measured": "timing measured by a smoke run on the machine that will run the job",
}

# key -> (what it is, how to tell whether the case uses it)
CRITICAL = {
    "materials.base_xmt": "base material card",
    "materials.filler_xmt": "filler material card",
    "heat_source.xml": "heat source: current, voltage, efficiency, geometry",
    "process.weld_speed_mm_s": "weld speed",
    "process.parameters_xml": "welding time step, cooling time, step control",
    "bead.leg_mm": "weld leg / bead size",
    "bead.section_spacing_mm": "bead section spacing",
    "constraints": "clamping, bearings, fixings, fixed nodes",
    "contact_ruleset": "contact behaviour",
    "temperature": "initial temperature, convection, radiation, contact heat transfer",
    "gravity": "gravity direction",
    "gate.end_time_s": "simulated end time (welding + cooling)",
    "solver": "solver domains and threads",
}

MACHINE_SOURCES = {"machine_probe", "benchmark_measured", "user_confirmed"}


def get(case, dotted):
    cur = case
    for k in dotted.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def used(case, key):
    """Whether the case uses this parameter at all (an unused parameter needs no source)."""
    if key == "constraints":
        return bool(case.get("fixed_nodes_csv") or case.get("constraints")
                    or (case.get("explicit_web") or {}).get("constraints"))
    return get(case, key) not in (None, "", [], {})


def approved(entry):
    a = (entry or {}).get("approval") or {}
    return bool(a.get("by")) and bool(a.get("date"))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def regression_inputs(case_dirs, input_files_of, exclude_case=None):
    """{sha256: [(case name, label, path)]} over every input file of every registered regression case.

    `input_files_of(path)` returns (label, abs path) pairs of a case file. A case is a regression case when it says
    `"role": "regression"`; project cases are never a source of anything."""
    out = {}
    for d in case_dirs:
        for cp in sorted(glob.glob(os.path.join(d, "*.json"))):
            if exclude_case and os.path.abspath(cp) == os.path.abspath(exclude_case):
                continue
            try:
                c = json.load(io.open(cp, encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if not isinstance(c, dict) or c.get("role") != "regression":
                continue
            for label, p in input_files_of(cp):
                if p and os.path.isfile(p):
                    out.setdefault(sha256(p), []).append((c.get("case_name", os.path.basename(cp)), label, p))
    return out


def check(case, case_path, input_files, case_dirs, input_files_of, Result):
    """Preflight results for the provenance rules PV1-PV4. `input_files` are (label, abs path) of this case."""
    res = []
    prov = case.get("provenance") or {}
    role = case.get("role", "project")
    missing, borrowed_unapproved, borrowed_ok, estimated, unknown = [], [], [], [], []
    not_yet = []
    for key, what in CRITICAL.items():
        if not used(case, key):
            # a project case has to establish every one of them; leaving a value out is not a way round its source
            if role != "regression":
                not_yet.append("%s (%s)" % (key, what))
            continue
        e = prov.get(key)
        if not e or not e.get("source"):
            missing.append("%s (%s)" % (key, what))
            continue
        src = e["source"]
        if src not in SOURCES:
            unknown.append("%s: %r" % (key, src))
        elif src == "borrowed_case":
            (borrowed_ok if approved(e) else borrowed_unapproved).append("%s <- %s" % (key, e.get("ref", "?")))
        elif src == "estimated":
            estimated.append("%s (%s)" % (key, e.get("note", "")))
        if key == "solver" and src not in MACHINE_SOURCES and src != "borrowed_case":
            unknown.append("solver: source %r; domains/threads must come from the machine "
                           "(machine_probe, benchmark_measured) or the user" % src)
    if missing:
        res.append(Result("block", "PV1", "critical parameters without a source: %s" % "; ".join(missing), len(missing)))
    else:
        res.append(Result("ok", "PV1", "every critical parameter in use has a source", None))
    if unknown:
        res.append(Result("block", "PV1", "invalid provenance: %s" % "; ".join(unknown), None))
    if not_yet:
        res.append(Result("block", "PV1", "not yet established for this part: %s" % "; ".join(not_yet), len(not_yet)))
    if borrowed_unapproved:
        res.append(Result("block", "PV2", "parameters borrowed from another case without explicit approval: %s"
                          % "; ".join(borrowed_unapproved), len(borrowed_unapproved)))
    if borrowed_ok:
        res.append(Result("warn", "PV2", "borrowed with the user's approval (still listed): %s"
                          % "; ".join(borrowed_ok), len(borrowed_ok)))
    if not borrowed_unapproved and not borrowed_ok:
        res.append(Result("ok", "PV2", "nothing borrowed from another case", None))
    if estimated:
        res.append(Result("warn", "PV3", "estimated values: %s" % "; ".join(estimated), len(estimated)))
    # content-based detection: the label cannot hide a copied file. Regression cases are exempt among themselves:
    # two variants of one finished study legitimately share its inputs; the rule is about a new project case.
    reg = {} if role == "regression" else regression_inputs(case_dirs, input_files_of, exclude_case=case_path)
    silent = []
    declared = {e.get("ref", "") for e in prov.values() if isinstance(e, dict) and e.get("source") == "borrowed_case"
                and approved(e)}
    for label, p in input_files:
        if p and os.path.isfile(p):
            hits = reg.get(sha256(p))
            if hits and not any(h[0] in r or os.path.basename(h[2]) in r for h in hits for r in declared):
                silent.append("%s is identical to %s of regression case %s" % (label, hits[0][1], hits[0][0]))
    if silent:
        res.append(Result("block", "PV4", "input files copied from a regression case without an approved "
                          "borrowed_case entry: %s" % "; ".join(silent), len(silent)))
    else:
        res.append(Result("ok", "PV4", "no input file is a silent copy of a regression case input", None))
    if role == "regression":
        res.append(Result("ok", "PV0", "regression case: reproduces a finished study, not a template", None))
    return res


def summary(case):
    """One row per critical parameter, for the dashboard."""
    prov = case.get("provenance") or {}
    rows = []
    for key, what in CRITICAL.items():
        e = prov.get(key) or {}
        rows.append({"key": key, "what": what, "used": used(case, key) or case.get("role", "project") != "regression",
                     "value": _short(get(case, key) if key !=
                                                                                    "constraints" else _cons(case)),
                     "source": e.get("source"), "ref": e.get("ref"), "note": e.get("note"),
                     "approved": approved(e) if e.get("source") == "borrowed_case" else None})
    return rows


def _cons(case):
    return case.get("fixed_nodes_csv") or case.get("constraints") or (case.get("explicit_web") or {}).get("constraints")


def _short(v):
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= 120 else s[:117] + "..."


def approval(by, scope, date=None):
    return {"by": by, "scope": scope, "date": date or datetime.date.today().isoformat()}
