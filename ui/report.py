# -*- coding: utf-8 -*-
"""The review page: one static HTML file per case, generated from the stage manifests, always the same layout.

This is a pipeline stage, not an optional viewer. `weldsim.py review` writes `<out>/dashboard/index.html`, every other
stage regenerates it when it finishes, and a formal run refuses to start unless a review exists for the current case
file. The page reads only what the stages wrote -- lifecycle.json, inspect/features.json, plan/plan.json,
provenance/manifest.json, machine/machine.json and the step manifests -- so every case, regression or new, gets the same
page. No server, no JavaScript dependency: it opens from the disk."""
import datetime
import glob
import html
import io
import json
import os

STAGE_DIRS = {"hm": "00_hm", "prep": "01_prep", "build": "02_build", "gate": "03_gate", "run": "04_run",
              "compare": "05_compare", "post": "07_post"}
COLOURS = {"PASS": "#047857", "WARN": "#b45309", "BLOCK": "#b91c1c", "SKIP": "#64748b", "RUNNING": "#1d4ed8",
           None: "#94a3b8"}


def _j(p):
    try:
        return json.load(io.open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _e(x):
    return html.escape("" if x is None else (x if isinstance(x, str) else json.dumps(x, ensure_ascii=False)))


def _table(head, rows):
    out = ["<table><tr>%s</tr>" % "".join("<th>%s</th>" % _e(h) for h in head)]
    for r in rows:
        out.append("<tr>%s</tr>" % "".join("<td>%s</td>" % c for c in r))
    out.append("</table>")
    return "".join(out)


def _badge(status):
    return '<span class="b" style="background:%s">%s</span>' % (COLOURS.get(status, "#94a3b8"), _e(status or "—"))


def build(case_path, out_dir, lifecycle_mod, provenance_mod, preflight_results=None):
    case = json.load(io.open(case_path, encoding="utf-8"))
    life = lifecycle_mod.load(out_dir)
    feats = _j(os.path.join(out_dir, "inspect", "features.json"))
    plan = _j(os.path.join(out_dir, "plan", "plan.json"))
    machine = _j(os.path.join(out_dir, "machine", "machine.json"))
    blockers = lifecycle_mod.run_blockers(out_dir, case_path)
    prov_rows = provenance_mod.summary(case)
    s = []
    s.append("<h1>%s</h1><p class='m'>%s · role <b>%s</b> · generated %s</p>" % (
        _e(case.get("case_name")), _e(os.path.abspath(case_path)), _e(case.get("role", "project")),
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    # verdict
    open_items = [r for r in prov_rows if r["used"] and (not r["source"] or (r["source"] == "borrowed_case"
                                                                              and not r["approved"]))]
    gaps = list((plan or {}).get("gaps") or [])
    gaps += ["not verified end to end: %s" % x for j in (plan or {}).get("joints", []) for x in j.get("not_verified", [])]
    verdict = "BLOCK" if (blockers or open_items or gaps) else "PASS"
    s.append("<div class='v' style='border-color:%s'><h2>Formal run: %s</h2>" % (COLOURS[verdict], _badge(verdict)))
    items = ["<li>%s</li>" % _e(b) for b in blockers]
    items += ["<li>parameter without an accepted source: <b>%s</b> (%s)</li>" % (_e(r["key"]), _e(r["source"] or
                                                                                               "none")) for r in open_items]
    items += ["<li>capability gap: %s</li>" % _e(g) for g in gaps]
    s.append("<ul>%s</ul></div>" % ("".join(items) or "<li>nothing blocks</li>"))
    # stages
    rows = []
    for st in lifecycle_mod.STAGES:
        e = life["stages"].get(st) or {}
        stale = e and e.get("case_sha256") != lifecycle_mod.case_hash(case_path)
        rows.append([_e(st), _badge(e.get("status")), _e(e.get("time")), _e(e.get("summary")) +
                     (" <i>(case changed since)</i>" if stale else "")])
    s.append("<h2>Stages</h2>" + _table(["stage", "status", "time", "summary"], rows))
    # geometry and plan
    s.append("<h2>Model and joint</h2><p>STEP: <code>%s</code></p>" % _e((case.get("geometry") or {}).get("step")))
    if feats:
        s.append(_table(["body", "kind", "thickness mm", "bbox mm", "extrusion"],
                        [[_e(b["name"]), _e(b["kind"]), _e(b["thickness_mm"]), _e(b["bbox_mm"]),
                          _e(b.get("extrusion_axis"))] for b in feats["bodies"]]))
    if plan:
        for j in plan["joints"]:
            s.append("<h3>%s — capability <code>%s</code> %s</h3>" % (_e(j["detected_joint"].get("joint")),
                                                                      _e(j.get("capability")), _badge(
                {"supported": "PASS", "partial": "WARN"}.get(j["status"], "BLOCK"))))
            s.append(_table(["item", "value"], [[_e(k), _e(v)] for k, v in j["detected_joint"].items()] +
                            [["detected / supported", "detected: <b>%s</b> · supported: <b>%s</b>" % (
                                _e("yes" if j.get("detected", True) else "no"),
                                _e("yes" if j.get("supported") else "NO (%s)" % j["status"]))],
                             ["local frame", _e(j.get("frame"))], ["evidence", _e(j.get("evidence"))],
                             ["seams", _e(j.get("seams"))], ["mesh backend", _e(j.get("required_mesh_backend"))],
                             ["bead strategy", _e(j.get("bead_strategy"))],
                             ["contact strategy", _e(j.get("contact_strategy"))],
                             ["tooling inputs required", _e(j.get("tooling_inputs_required"))],
                             ["process inputs required", _e(j.get("process_inputs_required"))],
                             ["unsupported", _e(j.get("unsupported_features"))],
                             ["not verified end to end (formal run blocked)", _e(j.get("not_verified"))],
                             ["official references (method only)",
                              _e([r["id"] for r in j.get("official_references", [])])]]))
    else:
        s.append("<p>No capability plan.</p>")
    # contact: what build will use and why
    from common import contact as contact_mod
    try:
        cd = contact_mod.resolve(case, plan)
        crow = [["rule set", "<code>%s</code> (%s)" % (_e(cd["ruleset"]), _e(cd["mode"]))],
                ["decided by", _e(cd["decided_by"])], ["basis", _e(cd["basis"])],
                ["provenance source", _e(cd["source"] or "—")]]
        if cd.get("requires_source"):
            crow.append(["explicit option needs a source from", _e(cd["requires_source"])])
    except (ValueError, OSError) as exc:
        crow = [["not decided", _e(str(exc))]]
    pc = contact_mod.plan_contact(plan)
    if pc:
        crow.append(["plan default / explicit options", _e("%s / %s" % (pc.get("default"), {
            k: v.get("ruleset") for k, v in (pc.get("explicit") or {}).items()}))])
    s.append("<h2>Contact</h2>" + _table(["item", "value"], crow))
    # parameters
    s.append("<h2>Parameters and their sources</h2>")
    rows = []
    for r in prov_rows:
        if not r["used"]:
            continue
        colour = "#b91c1c" if (not r["source"] or (r["source"] == "borrowed_case" and not r["approved"])) else (
            "#b45309" if r["source"] in ("estimated", "borrowed_case") else "#047857")
        rows.append([_e(r["key"]), _e(r["what"]), "<code>%s</code>" % _e(r["value"]),
                     "<b style='color:%s'>%s</b>" % (colour, _e(r["source"] or "MISSING")), _e(r["ref"]),
                     _e("approved" if r["approved"] else ("NOT approved" if r["approved"] is False else ""))])
    s.append(_table(["parameter", "what", "value", "source", "reference", "borrow approval"], rows))
    # tooling
    cons = (case.get("explicit_web") or {}).get("constraints") or case.get("constraints") or case.get("fixed_nodes_csv")
    s.append("<h2>Tooling and constraints</h2><pre>%s</pre>" % _e(json.dumps(cons, ensure_ascii=False, indent=1)
                                                                  if not isinstance(cons, str) else cons))
    # machine
    s.append("<h2>Machine and parallelism</h2>")
    s.append(_table(["item", "value"], [[_e(k), _e(v)] for k, v in (machine or {}).items()] +
                    [["solver (case)", _e(case.get("solver"))]]))
    # mesh and run
    hm = _j(os.path.join(out_dir, STAGE_DIRS["hm"], "manifest.json"))
    if hm and hm.get("explicit_web"):
        e = hm["explicit_web"]
        s.append("<h2>Mesh</h2>" + _table(["check", "ok"], [[_e(k), _badge("PASS" if v else "BLOCK")]
                                                            for k, v in (e.get("checks") or {}).items()]))
    run = _j(os.path.join(out_dir, STAGE_DIRS["run"], "manifest.json"))
    s.append("<h2>Run</h2><pre>%s</pre>" % _e(json.dumps((run or {}).get("solver_stats") or "not run",
                                                          ensure_ascii=False, indent=1)))
    post = os.path.join(out_dir, STAGE_DIRS["post"], "post_summary.txt")
    if os.path.isfile(post):
        s.append("<h2>Results</h2><pre>%s</pre>" % _e(io.open(post, encoding="utf-8").read()[:20000]))
    page = """<title>%s review</title><meta charset="utf-8"><style>
body{font:14px system-ui,sans-serif;margin:16px auto;max-width:1300px;padding:0 16px;color:#0f172a}
table{border-collapse:collapse;margin:6px 0 14px;width:100%%}td,th{border:1px solid #cbd5e1;padding:4px 6px;
vertical-align:top;text-align:left}th{background:#f1f5f9}.b{color:#fff;border-radius:4px;padding:1px 6px;
font-weight:700}.v{border:3px solid;border-radius:8px;padding:6px 14px;margin:10px 0}.m{color:#64748b}
code,pre{background:#f8fafc;font-size:12px}pre{padding:8px;overflow-x:auto}</style>%s""" % (
        _e(case.get("case_name")), "".join(s))
    d = os.path.join(out_dir, "dashboard")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "index.html")
    io.open(p, "w", encoding="utf-8").write(page)
    return {"path": p, "verdict": verdict, "blockers": blockers, "open_parameters": [r["key"] for r in open_items],
            "gaps": gaps}
