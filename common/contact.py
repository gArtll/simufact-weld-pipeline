# -*- coding: utf-8 -*-
"""Which contact behaviour a case is built with, and why.

There is no single repository-wide contact default. The decision is made per case:

* a regression case keeps the rule set it was solved with (`contact_ruleset` in its json). That rule set is part of
  the finished study it reproduces; changing it to match a newer default would change the baseline.
* a project case follows the capability plan of its joint (capability/matrix.yaml `contact`): the plan names a
  default -- the process default where nothing more specific is known -- and the explicit alternatives the capability
  offers. The case may choose an alternative only with provenance from an official reference, the user or the part's
  process document. An alternative is never chosen by the tool to make a run converge, and a rule set outside the
  capability's list is refused.

`resolve` answers what build uses; `check` gives the preflight results (rule CT1). Both are also shown on the review
page, with the basis of the choice."""
import io
import json
import os

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULESETS = os.path.join(ROOT, "build", "rulesets")
PROCESS_DEFAULT = "process_default"


def ruleset_path(name):
    return os.path.join(RULESETS, name + ".yaml")


def load_ruleset(name):
    return yaml.safe_load(io.open(ruleset_path(name), encoding="utf-8"))


def mode(name):
    """'process_default' (contact left to Simufact) or 'user_defined' (a generated contact table)."""
    rs = load_ruleset(name)
    return PROCESS_DEFAULT if rs.get("mode") == PROCESS_DEFAULT else "user_defined"


def plan_contact(plan):
    """The contact block of the plan's joints: {default, basis, explicit} merged over joints (one joint today)."""
    for j in (plan or {}).get("joints", []):
        cs = j.get("contact_strategy")
        if isinstance(cs, dict):
            return cs
    return None


def load_plan(out_dir):
    p = os.path.join(out_dir, "plan", "plan.json")
    return json.load(io.open(p, encoding="utf-8")) if os.path.isfile(p) else None


def resolve(case, plan):
    """{ruleset, mode, decided_by, basis, source} for this case."""
    declared = case.get("contact_ruleset")
    src = ((case.get("provenance") or {}).get("contact_ruleset") or {}).get("source")
    if case.get("role") == "regression":
        if not declared:
            raise ValueError("regression case without contact_ruleset: its baseline contact is unknown")
        return {"ruleset": declared, "mode": mode(declared), "decided_by": "regression baseline",
                "basis": ["the rule set this finished study was solved with; kept fixed so the regression stays a "
                          "regression"], "source": src}
    pc = plan_contact(plan)
    if pc is None:
        raise ValueError("no capability plan with a contact strategy: run inspect and plan first")
    if not declared or declared == pc["default"]:
        return {"ruleset": pc["default"], "mode": mode(pc["default"]),
                "decided_by": "capability plan default" + ("" if declared else " (case leaves contact_ruleset empty)"),
                "basis": pc.get("basis", []), "source": src}
    opt = next((dict(v, id=k) for k, v in (pc.get("explicit") or {}).items() if v.get("ruleset") == declared), None)
    if opt is None:
        raise ValueError("contact_ruleset %r is neither the plan default %r nor one of its explicit options %s"
                         % (declared, pc["default"], sorted(v["ruleset"] for v in (pc.get("explicit") or {}).values())))
    return {"ruleset": declared, "mode": mode(declared), "decided_by": "explicit capability %s" % opt["id"],
            "basis": [opt.get("what", ""), opt.get("evidence", "")], "source": src,
            "requires_source": opt.get("requires_source", [])}


def check(case, plan, Result):
    """CT1: the contact rule set is the plan default, or an explicit option the case has a qualifying source for."""
    try:
        r = resolve(case, plan)
    except ValueError as exc:
        return [Result("block", "CT1", str(exc), case.get("contact_ruleset"))]
    if not os.path.isfile(ruleset_path(r["ruleset"])):
        return [Result("block", "CT1", "contact rule set file %s missing" % ruleset_path(r["ruleset"]), r["ruleset"])]
    need = r.get("requires_source")
    if need and r["source"] not in need:
        return [Result("block", "CT1", "contact_ruleset %r is an explicit capability (%s); it needs provenance from %s, "
                       "got %r. It is not switched on to make a run converge"
                       % (r["ruleset"], r["decided_by"], "/".join(need), r["source"]), r["ruleset"])]
    return [Result("ok", "CT1", "contact %s (%s, %s)" % (r["ruleset"], r["mode"], r["decided_by"]), r["ruleset"])]
