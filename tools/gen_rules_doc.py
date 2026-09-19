# -*- coding: utf-8 -*-
"""Generate docs/rules.md from common/rules.yaml. Usage: python tools/gen_rules_doc.py
tests/test_preflight.py checks that docs/rules.md equals render()."""
import io
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(ROOT, "common", "rules.yaml")
OUT = os.path.join(ROOT, "docs", "rules.md")
GROUPS = (("R", "Simulation rules"), ("S", "Simufact hard rules"), ("TCL", "HyperMesh Tcl hard rules"), ("PF", "Preflight consistency checks"))


def _group(rule_id):
    for prefix, _ in sorted(GROUPS, key=lambda g: -len(g[0])):
        if rule_id.startswith(prefix) and rule_id[len(prefix):].isdigit():
            return prefix
    return "R"


def _cell(v):
    return str(v).replace("|", "\\|")


def render():
    rules = yaml.safe_load(io.open(RULES, encoding="utf-8"))["rules"]
    out = ["# Rules", "", "Generated from `common/rules.yaml` by `tools/gen_rules_doc.py`. Do not edit by hand.", "",
           "| Field | Values |", "|---|---|", "| stage | prep / build / gate / run / none |", "| severity | block / warn |",
           "| enforced by | `file:function` / manual |", "| source | 内部算例 (an unpublished development model) / Simufact 硬规则 / Tcl 硬规则 / E2 任务书 / F1 |", ""]
    for prefix, title in GROUPS:
        sel = [r for r in rules if _group(r["id"]) == prefix]
        out += ["## %s (%d)" % (title, len(sel)), "", "| ID | Rule | Stage | Severity | Enforced by | Source |", "|---|---|---|---|---|---|"]
        for r in sel:
            enf = r["enforced_by"] if r["enforced_by"] == "manual" else "`%s`" % r["enforced_by"]
            out.append("| %s | %s | %s | %s | %s | %s |" % (r["id"], _cell(r["title"]), r["stage"], r["severity"], enf, _cell(r["source"])))
        out.append("")
    return "\n".join(out)


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, "w", encoding="utf-8", newline="\n").write(render())
    print("wrote", os.path.relpath(OUT, ROOT))


if __name__ == "__main__":
    main()
