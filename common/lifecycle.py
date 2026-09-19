# -*- coding: utf-8 -*-
"""The case lifecycle: fixed stages, one state file, and the gate in front of a long run.

    new -> inspect -> plan -> provenance -> review -> hm -> mesh_gate -> preflight -> prep -> build -> gate -> run
        -> post -> report

Every stage records PASS / WARN / BLOCK / SKIP in `<out>/lifecycle.json`, with the hash of the case file it saw. The
dashboard is the `review` stage, not a script someone has to remember: it is generated from these records, and a run
is refused unless a review exists for the current case file and nothing on it blocks. A case edited after its review
needs a new review before it can run, because the page the user looked at no longer describes it."""
import hashlib
import io
import json
import os
import time

STAGES = ["new", "inspect", "plan", "provenance", "review", "hm", "mesh_gate", "preflight", "prep", "build", "gate",
          "run", "post", "report"]
# stages that must have passed, for the current case file, before a formal long run
BEFORE_RUN = ["inspect", "plan", "provenance", "review"]
STATUSES = ("PASS", "WARN", "BLOCK", "SKIP", "RUNNING")


def case_hash(case_path):
    return hashlib.sha256(io.open(case_path, "rb").read()).hexdigest()


def path(out_dir):
    return os.path.join(out_dir, "lifecycle.json")


def load(out_dir):
    p = path(out_dir)
    if os.path.isfile(p):
        return json.load(io.open(p, encoding="utf-8"))
    return {"stages": {}, "history": []}


def record(out_dir, case_path, stage, status, summary=None, detail=None):
    if stage not in STAGES:
        raise ValueError("unknown stage %r" % stage)
    if status not in STATUSES:
        raise ValueError("unknown status %r" % status)
    os.makedirs(out_dir, exist_ok=True)
    st = load(out_dir)
    entry = {"status": status, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "case_sha256": case_hash(case_path),
             "summary": summary, "detail": detail}
    st["stages"][stage] = entry
    st["history"].append(dict(entry, stage=stage))
    st["case"] = os.path.abspath(case_path)
    io.open(path(out_dir), "w", encoding="utf-8").write(json.dumps(st, ensure_ascii=False, indent=1))
    return entry


def run_blockers(out_dir, case_path):
    """Reasons a formal run may not start. Empty list = allowed."""
    st, h, out = load(out_dir), case_hash(case_path), []
    for s in BEFORE_RUN:
        e = st["stages"].get(s)
        if not e:
            out.append("stage %s has not run" % s)
        elif e["status"] == "BLOCK":
            out.append("stage %s is BLOCK: %s" % (s, e.get("summary")))
        elif e["case_sha256"] != h:
            out.append("stage %s ran on a different version of the case file; run it again" % s)
    return out
