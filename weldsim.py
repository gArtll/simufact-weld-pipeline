# -*- coding: utf-8 -*-
"""weldsim_tool command line.

  python weldsim.py suggest --joint fillet --process FCAW --material Q420 --thickness 6,10 [--n 3] [--emit-case out.json]
  python weldsim.py new      <case.json> --step part.stp   blank case from common/case_template.json (never from a case)
  python weldsim.py inspect  <case.json>   inspect   STEP -> bodies, contacts, joint features (capability/classify.py)
  python weldsim.py plan     <case.json>   plan      features -> capability plan or gaps (capability/matrix.yaml)
  python weldsim.py provenance <case.json> provenance every critical parameter has a source; nothing silently borrowed
  python weldsim.py review   <case.json>   dashboard the review page <out>/dashboard/index.html (fixed stage)
  python weldsim.py status   <case.json>             lifecycle of the case, and whether a formal run may start
  python weldsim.py preflight <case.json>          read-only rule checks (common/rules.yaml), non-zero exit on any [block]
  python weldsim.py hm       <case.json>   00_hm     base meshes from the STEP by the plan's backends (explicit web;
                                                     plate swept or mapped); records mesh_gate. Regression cases:
                                                     explicit_web block as given, or base_mesh -> hmbatch
  python weldsim.py prep     <case.json>   01_prep   root line, bead mesh in the joint frame, outer-surface line, checks
  python weldsim.py build    <case.json>   02_build   Simufact project from bdf/csv, trajectory patch, contact table
  python weldsim.py gate     <case.json>   03_gate    write .dat at gate end time on a copy, check_short_v3 vs reference
  python weldsim.py gate     <case.json> --self       03_gate from 01_prep only (no Simufact): self .dat, check_short_v3 new = ref
  python weldsim.py run      <case.json>   04_run     write .dat at run end time on a copy, gate --allow AUTO_STEP, solve
  python weldsim.py compare  <case.json>   05_compare same-section peak temperature vs reference result
  python weldsim.py post     <case.json>   07_post   probes by physical coordinate: thermal cycle, t8/5, final values (post block)
  python weldsim.py regress                          gate/run_regressions.py
  python weldsim.py test                             unit tests (no Simufact)
  python weldsim.py all      <case.json>   preflight prep build gate run compare [post] regress test
build and all run preflight first; --skip-preflight skips it and records that in 02_build/manifest.json.

Every step writes <output_dir>/<step>/manifest.json (input sha256, outputs, duration, exit code, checks).
Any failure exits non-zero and prints the last 20 lines of the relevant log.
Machine paths: config.yaml next to this file (or --config; falls back to config.example.yaml). Case paths are relative
to the case file."""
import argparse
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import time

import yaml

TOOL = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, TOOL)
from common.manifest import Manifest, tail  # noqa: E402
from common.qxml import robot_blobs  # noqa: E402
from common.bdf import read_weldline_csv, check_r1  # noqa: E402
from common.preflight import run_preflight, format_results, summarize  # noqa: E402

LIFE_STEPS = {"inspect": "inspect", "plan": "plan", "provenance": "provenance", "review": "dashboard"}
STEPS = {"hm": "00_hm", "prep": "01_prep", "build": "02_build", "gate": "03_gate", "run": "04_run", "compare": "05_compare",
         "post": "07_post"}   # 06_selfcheck is regress/test, not a pipeline step


class StepFailed(Exception):
    def __init__(self, msg, log=None):
        super().__init__(msg)
        self.log = log


# ---------------------------------------------------------------- context
class Ctx:
    def __init__(self, case_path, config_path):
        self.cfg = yaml.safe_load(io.open(config_path, encoding="utf-8"))
        self.case_path = os.path.abspath(case_path)
        self.base = os.path.dirname(self.case_path)
        self.case = json.load(io.open(self.case_path, encoding="utf-8"))
        c = self.case
        if not c.get("case_name"):
            raise StepFailed("case.json has no case_name")
        # No gate on a case family here: what the tool can build is decided by the capability plan of the
        # geometry (inspect -> plan), not by which of the existing cases a new one resembles. geometry_family is a
        # label the regression cases still carry; no decision reads it.
        self.out = self.p(c["output_dir"]) if c.get("output_dir") else os.path.join(TOOL, self.cfg["work_root"], c["case_name"])
        self.python = self.cfg.get("python") or sys.executable
        self.ref = c.get("reference")
        self.preflight = None

    def p(self, rel):
        return os.path.normpath(os.path.join(self.base, rel))

    def step_dir(self, step):
        return os.path.join(self.out, STEPS.get(step) or LIFE_STEPS[step])


def ascii_paths(paths):
    """Simufact hard rule 3: no non-ASCII characters in any path handed to the script runner."""
    bad = [p for p in paths if p and any(ord(ch) > 127 for ch in p)]
    if bad:
        raise StepFailed("non-ASCII path(s) not allowed (hard rule 3): %s" % bad)


def run_cmd(cmd, log, cwd=None, env=None):
    with io.open(log, "w", encoding="utf-8") as f:
        f.write("CMD %s\n" % cmd)
        f.flush()
        r = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, cwd=cwd, env=env)
    return r.returncode


def runscript(ctx, script, params, step_dir, name):
    pj = os.path.join(step_dir, name + "_params.json")
    io.open(pj, "w", encoding="utf-8").write(json.dumps(params, ensure_ascii=False, indent=1) + "\n")
    ascii_paths([script, pj, step_dir])
    log = os.path.join(step_dir, name + "_console.log")
    rc = run_cmd([ctx.cfg["runscript"], script, pj], log, cwd=step_dir)
    return rc, log, pj


def set_end_time(src, dst, end_time):
    t = io.open(src, encoding="utf-8").read()
    t, n = re.subn(r'(<end_time\b[^>]*\bvalue=")[^"]*(")', r"\g<1>%r\g<2>" % float(end_time), t, count=1)
    if n != 1:
        raise StepFailed("process parameters: no unique end_time in %s" % src)
    io.open(dst, "w", encoding="utf-8").write(t)
    return dst


def check_cooling_step(xml_path):
    """Simufact hard rule 7: cooling load cases must have a non-automatic maximum time step."""
    t = io.open(xml_path, encoding="utf-8").read()
    blocks = re.findall(r'<step_size_control loadcase_type="2"[^>]*>(.*?)</step_size_control>', t, re.S)
    auto = [re.search(r'<maximum_time_step\b[^>]*\bis_automatic="(\w+)"', b) for b in blocks]
    return bool(blocks) and all(m and m.group(1) == "false" for m in auto), len(blocks)


def find_proc_dir(project_dir):
    hits = [d for d in glob.glob(os.path.join(project_dir, "*")) if os.path.isfile(os.path.join(d, "robots_properties.xml"))]
    if len(hits) != 1:
        raise StepFailed("expected one process directory in %s, found %s" % (project_dir, hits))
    return hits[0]


def read_sts_exit(sts_path):
    """Return (line number, exit number) of the last 'job ends with exit number' line, or None."""
    last = None
    for k, s in enumerate(io.open(sts_path, encoding="latin-1"), 1):
        m = re.search(r"exit number\s+(\d+)", s)
        if m:
            last = (k, int(m.group(1)))
    return last


def list_outputs(d):
    return [os.path.relpath(os.path.join(r, f), d) for r, _, fs in os.walk(d) for f in fs if f != "manifest.json"]


def copy_project(src_project, dst_project):
    if os.path.exists(dst_project):
        raise StepFailed("refusing to overwrite %s (hard rule 1: disposable copies only, remove the step directory)" % dst_project)
    shutil.copytree(src_project, dst_project, ignore=shutil.ignore_patterns("_Run_", "_log", "_particles", "_Results_", "_watch_tmp",
                                                                            "project.lock", "*.bak_before_*"))
    return dst_project


def fresh_step(ctx, step):
    d = ctx.step_dir(step)
    if os.path.exists(d) and os.listdir(d):
        raise StepFailed("step directory %s is not empty; delete it to rerun this step" % d)
    os.makedirs(d, exist_ok=True)
    return d


def require_step_ok(ctx, step):
    mp = os.path.join(ctx.step_dir(step), "manifest.json")
    if not os.path.isfile(mp):
        raise StepFailed("step %s has not been run (%s missing)" % (step, mp))
    m = json.load(io.open(mp, encoding="utf-8"))
    if m.get("exit_code") != 0:
        raise StepFailed("step %s did not succeed (exit_code %s)" % (step, m.get("exit_code")))
    return m


def base_bdfs(ctx):
    """(web bdf, plate bdf, source): 00_hm output when the case has base_mesh or explicit_web and 00_hm succeeded, else
    case.json paths. The explicit-web backend meshes the web only; its plate is whatever it was handed, or the case's."""
    c = ctx.case
    mp = os.path.join(ctx.step_dir("hm"), "manifest.json")
    from_cad = c.get("role") != "regression" and (c.get("geometry") or {}).get("step")
    if (c.get("base_mesh") or c.get("explicit_web") or from_cad) and os.path.isfile(mp):
        m = json.load(io.open(mp, encoding="utf-8"))
        if m.get("exit_code") == 0 and not m.get("skipped"):
            d = ctx.step_dir("hm")
            plate = os.path.join(d, "base_plate.bdf")
            if not os.path.isfile(plate):
                plate = ctx.p(c["base"]["plate_bdf"])
            return os.path.join(d, "base_web.bdf"), plate, "00_hm/%s" % m.get("mesher", "automesh")
    return ctx.p(c["base"]["web_bdf"]), ctx.p(c["base"]["plate_bdf"]), "case"


# ---------------------------------------------------------------- steps
def hm_block_from_plan(ctx):
    """The explicit_web block of a project case, completed from the capability plan: the web face, its root edges,
    the plate profile face and the plate backend come from the geometry (inspect -> plan). Anything the case gives
    itself is kept. Sizes left out take the mesher's algorithm defaults, which describe the tool, not the part.
    Returns (block, notes); raises StepFailed when the plan has no mesh backend for the base part."""
    c = ctx.case
    blk = dict(c.get("explicit_web") or {})
    pj = os.path.join(ctx.step_dir("plan"), "plan.json")
    if c.get("role") == "regression" or not os.path.isfile(pj):
        return (blk or None), []
    joints = json.load(io.open(pj, encoding="utf-8")).get("joints") or []
    mb = (joints[0].get("required_mesh_backend") or {}) if joints else {}
    if mb.get("standing") != "explicit_web":
        raise StepFailed("plan: standing part backend is %r; hm meshes a web from CAD only" % mb.get("standing"))
    if mb.get("base") == "none":
        raise StepFailed("plan: no mesh backend for the base part (%s)" % mb.get("base_reason"))
    notes = []
    blk.setdefault("step", (c.get("geometry") or {}).get("step"))
    if blk.get("face") is None:
        blk["face"], notes = mb["standing_face"], notes + ["web face from plan"]
        if not blk.get("root_geometry_ids"):
            blk["root_geometry_ids"] = mb["root_geometry_ids"]
    plate = dict(blk.get("plate") or {})
    if mb.get("base") in ("explicit_plate_sweep", "mapped_plate") and not blk.get("plate_bdf"):
        if plate.get("face") is None:
            plate["face"], notes = mb["base_profile_face"], notes + ["plate profile face from plan"]
        plate.setdefault("backend", mb["base"])
        blk["plate"] = plate
    return blk, notes


def step_hm(ctx):
    """00_hm: base meshes. From CAD by the plan's backends (explicit web; plate swept or mapped), or -- regression
    cases only -- the parametric HyperMesh automesh of the base_mesh block."""
    c = ctx.case
    d = fresh_step(ctx, "hm")
    m = Manifest(d, "hm")
    try:
        blk, notes = hm_block_from_plan(ctx)
    except StepFailed:
        m.finish(1, [])
        raise
    if notes:
        m.data["from_plan"] = notes
    if blk:
        from hm.explicit_web import ExplicitWebError, run_explicit
        try:
            res = run_explicit(blk, ctx.base, d, log=lambda s: print(s, flush=True))
        except ExplicitWebError as exc:
            m.finish(1, [])
            raise StepFailed(str(exc))
        m.data["explicit_web"] = res
        if res["supported"]:
            m.data["mesher"] = "explicit_web"
            ok = True
            for name, v in res["checks"].items():
                ok &= m.check("explicit_web " + name, v, None)
            m.finish(0 if ok else 1, list_outputs(d))
            if not ok:
                # a supported geometry that fails its checks is a bug to look at, not a reason to swap meshers
                raise StepFailed("explicit_web: checks failed: %s" % [k for k, v in res["checks"].items() if not v])
            return
        if res["params"]["fallback"] != "automesh" or not c.get("base_mesh"):
            m.finish(1, [])
            raise StepFailed("explicit_web unsupported (%s) and no automesh fallback (fallback=%r, base_mesh %s)"
                             % ("; ".join(res["unsupported_reasons"]), res["params"]["fallback"],
                                "present" if c.get("base_mesh") else "absent"))
        m.data["mesher"] = "automesh (fallback)"
        m.data["fallback_reason"] = res["unsupported_reasons"]
        print("explicit_web unsupported -> automesh fallback", flush=True)
    else:
        m.data["mesher"] = "automesh"
    if not c.get("base_mesh"):
        m.data["skipped"] = "case.json has no base_mesh block"
        print("case.json 无 base_mesh，跳过 hm 步骤", flush=True)
        m.finish(0, [])
        return
    if not ctx.cfg.get("hmbatch"):
        m.data["skipped"] = "hmbatch not configured"
        print("未配置 HyperMesh，跳过 hm 步骤", flush=True)
        m.finish(0, [])
        return
    if c.get("role") != "regression":
        # base_mesh describes a web on a flat or bent plate by dimensions in a fixed frame; it does not read the
        # part's CAD, so it is kept only to reproduce the regression cases built with it
        m.finish(1, [])
        raise StepFailed("automesh (base_mesh) is the parametric mesher of the regression cases; a project case is "
                         "meshed from its CAD by the plan's backend (explicit_web)")
    from hm.params import ParamError
    from hm.pipeline import run_hm
    ascii_paths([d, ctx.cfg["hmbatch"]])
    try:
        res = run_hm(c["base_mesh"], d, ctx.cfg["hmbatch"], log=lambda s: print(s, flush=True))
    except ParamError as exc:
        m.finish(1, [])
        raise StepFailed(str(exc))
    m.data.update(res)
    last = res["attempts"][-1] if res["attempts"] else {}
    ok = res["final_attempt"] is not None
    for role in ("plate", "web"):
        r = last.get(role, {})
        ok &= m.check("hm %s Tcl rules TCL1-TCL4" % role, bool(r.get("tcl_rules")) and all(r["tcl_rules"].values()), r.get("tcl_rules"))
        ok &= m.check("hm %s hmbatch exit 0" % role, r.get("hmbatch_exit") == 0, r.get("hmbatch_exit"))
        ok &= m.check("hm %s quality (penta fraction, scaled Jacobian)" % role, r.get("quality_ok") is True,
                      {k: r.get(k) for k in ("hex8", "penta6", "penta_fraction", "min_scaled_jacobian")})
    need = int(res["params"]["mesh"]["plate_layers"])
    ok &= m.check("hm plate layers >= %d" % need, (res.get("plate_layers") or 0) >= need, res.get("plate_layers"))
    gap = last.get("contact") or {}
    ok &= m.check("hm web bottom on plate top (gap < 1e-6 mm)", gap.get("gap_mm") is not None and gap["gap_mm"] < 1e-6 and not gap.get("penetration"), gap)
    if ok:
        for role in ("web", "plate"):
            r1, st = check_r1(os.path.join(d, "base_%s.bdf" % role))
            ok &= m.check("R1 base_%s.bdf" % role, r1, st)
    m.finish(0 if ok else 1, list_outputs(d))
    if not ok:
        raise StepFailed("hm: thresholds not met after %d attempt(s)" % len(res["attempts"]),
                         os.path.join(d, last["plate"]["log"]) if last.get("plate", {}).get("log") else None)


# ---------------------------------------------------------------- lifecycle stages
def _life():
    from common import lifecycle
    return lifecycle


def step_inspect(ctx):
    """Classify the geometry: bodies, contacts, joint features. Regression cases built from meshes skip it."""
    c = ctx.case
    step = (c.get("geometry") or {}).get("step")
    d = ctx.step_dir("inspect")
    os.makedirs(d, exist_ok=True)
    if not step:
        if c.get("role") == "regression":
            return "SKIP", "regression case from supplied meshes"
        raise StepFailed("case has no geometry.step; a new case starts from its CAD (weldsim.py new --step)")
    from capability.classify import classify
    f = classify(ctx.p(step))
    io.open(os.path.join(d, "features.json"), "w", encoding="utf-8").write(json.dumps(f, ensure_ascii=False, indent=1))
    js = ", ".join("%s (%s on %s)" % (j["type"], j["standing"]["kind"], j["base"]["kind"]) for j in f["joints"])
    if not f["joints"]:
        return "BLOCK", "%d bodies, no joint found" % f["counts"]["bodies"]
    return "PASS", "%d bodies; %s" % (f["counts"]["bodies"], js)


def step_plan(ctx):
    d = ctx.step_dir("plan")
    fp = os.path.join(ctx.step_dir("inspect"), "features.json")
    if not os.path.isfile(fp):
        if ctx.case.get("role") == "regression" and not (ctx.case.get("geometry") or {}).get("step"):
            return "SKIP", "regression case from supplied meshes"
        raise StepFailed("no inspect result: run weldsim.py inspect first")
    from capability.planner import plan
    pl = plan(json.load(io.open(fp, encoding="utf-8")))
    os.makedirs(d, exist_ok=True)
    io.open(os.path.join(d, "plan.json"), "w", encoding="utf-8").write(json.dumps(pl, ensure_ascii=False, indent=1))
    caps = ", ".join("%s:%s" % (j.get("capability"), j["status"]) for j in pl["joints"])
    status = {"supported": "PASS", "partial": "WARN"}.get(pl["status"], "BLOCK")
    return status, caps + (("; gaps: %s" % json.dumps(pl["gaps"], ensure_ascii=False)) if pl["gaps"] else "")


def step_provenance(ctx):
    from common.preflight import check_provenance, Case
    d = ctx.step_dir("provenance")
    m = Manifest(d, "provenance")
    res = check_provenance(Case(ctx.case_path), ctx.cfg.get("regression_case_dirs"))
    for r in res:
        m.check("%s %s" % (r.rule, r.message[:150]), r.level != "block", r.value)
        print("[%s] %s %s" % (r.level, r.rule, r.message), flush=True)
    blocks = [r for r in res if r.level == "block"]
    m.finish(1 if blocks else 0, [])
    if blocks:
        return "BLOCK", "; ".join("%s %s" % (r.rule, r.message) for r in blocks)[:500]
    return ("WARN" if any(r.level == "warn" for r in res) else "PASS"), "sources complete"


def step_review(ctx):
    """Probe the machine, then write the review page. The verdict on it is the verdict of this stage."""
    from common import machine, provenance
    import ui.report as report
    d = ctx.step_dir("review")
    mdir = os.path.join(ctx.out, "machine")
    os.makedirs(mdir, exist_ok=True)
    mp = machine.probe(ctx.cfg)
    mp["recommendation"] = machine.recommend(mp)
    io.open(os.path.join(mdir, "machine.json"), "w", encoding="utf-8").write(json.dumps(mp, ensure_ascii=False, indent=1))
    lc = _life()
    r = report.build(ctx.case_path, ctx.out, lc, provenance)
    print("review page: %s" % r["path"], flush=True)
    others = [b for b in r["blockers"] if not b.startswith("stage review")]
    reasons = others + ["parameter %s" % k for k in r["open_parameters"]] + ["gap %s" % g for g in r["gaps"]]
    return ("BLOCK" if reasons else "PASS"), ("page %s; %s" % (r["path"], "; ".join(reasons) if reasons else "nothing blocks"))[:800]


def record_mesh_gate(ctx, lc):
    """The mesh quality gate stage: the acceptance checks of 00_hm (element kinds, Jacobians, CAD boundary, hard
    points), recorded as their own lifecycle stage so the page and the run gate show them apart from 'hm ran'."""
    mp = os.path.join(ctx.step_dir("hm"), "manifest.json")
    m = json.load(io.open(mp, encoding="utf-8")) if os.path.isfile(mp) else {}
    checks = m.get("checks") or {}
    if m.get("skipped") or not checks:
        lc.record(ctx.out, ctx.case_path, "mesh_gate", "SKIP", m.get("skipped") or "no mesh checks (meshes supplied)")
        return
    bad = [k for k, v in checks.items() if not (v.get("ok") if isinstance(v, dict) else v)]
    lc.record(ctx.out, ctx.case_path, "mesh_gate", "BLOCK" if bad else "PASS",
              ("failed: %s" % "; ".join(bad))[:500] if bad else "%d mesh checks passed (%s)"
              % (len(checks), m.get("mesher")))


def refresh_review(ctx):
    """Regenerate the page after any stage, without changing the review stage record."""
    try:
        from common import provenance
        import ui.report as report
        report.build(ctx.case_path, ctx.out, _life(), provenance)
    except Exception as exc:                                 # the page must never break a stage
        print("review page not refreshed: %s" % exc, flush=True)


def cmd_new(case_path, step, name):
    """A blank case from the template. Refuses to overwrite, and has no way to start from an existing case."""
    if os.path.exists(case_path):
        raise StepFailed("%s exists; `new` never overwrites and never starts from another case" % case_path)
    if not step or not os.path.isfile(step):
        raise StepFailed("--step must name the STEP file of the new part")
    from common import machine
    tpl = json.load(io.open(os.path.join(TOOL, "common", "case_template.json"), encoding="utf-8"))
    base = os.path.dirname(os.path.abspath(case_path))
    tpl["case_name"] = name or os.path.splitext(os.path.basename(case_path))[0]
    tpl["output_dir"] = os.path.relpath(os.path.join(base, "runs", tpl["case_name"]), base).replace("\\", "/")
    tpl["geometry"]["step"] = os.path.relpath(os.path.abspath(step), base).replace("\\", "/")
    rec = machine.recommend(machine.probe())
    tpl["solver"] = {"domains": rec["domains"], "threads_per_domain": rec["threads_per_domain"]}
    tpl["provenance"]["solver"] = {"source": "machine_probe", "ref": "common/machine.py", "note": rec["basis"]}
    os.makedirs(base, exist_ok=True)
    io.open(case_path, "w", encoding="utf-8").write(json.dumps(tpl, ensure_ascii=False, indent=1) + "\n")
    print("new case %s (blank; solver from this machine: %s)" % (case_path, tpl["solver"]), flush=True)
    return case_path


def prep_frame(ctx):
    """(normal, along, source) of the local joint frame prep builds the bead sections in (prep/frame.py).

    From the case (`bead.frame`), else from the capability plan (the joint frame `inspect` measured on the CAD), else --
    for a regression case built from supplied meshes -- the legacy +x / +z frame those meshes were made in. A project
    case never falls back to the legacy frame: its STEP may sit in any orientation."""
    c = ctx.case
    fr = (c.get("bead") or {}).get("frame")
    if fr:
        return fr["normal"], fr["along"], "case bead.frame"
    pj = os.path.join(ctx.step_dir("plan"), "plan.json")
    if os.path.isfile(pj):
        joints = json.load(io.open(pj, encoding="utf-8")).get("joints") or []
        f = (joints[0].get("frame") if joints else None) or {}
        if f.get("transverse") and f.get("tangent"):
            return f["transverse"], f["tangent"], "inspect joint frame (plan/plan.json)"
    if c.get("role") == "regression" and not (c.get("geometry") or {}).get("step"):
        return [1.0, 0.0, 0.0], [0.0, 0.0, 1.0], "legacy frame of the regression meshes"
    raise StepFailed("no local joint frame: run inspect and plan (or give bead.frame {normal, along})")


def step_prep(ctx):
    c = ctx.case
    d = fresh_step(ctx, "prep")
    m = Manifest(d, "prep")
    web, plate, src = base_bdfs(ctx)
    m.data["base_mesh_source"] = src
    windows = {s["label"]: ctx.p(s["window_csv"]) for s in c["sides"]}
    m.inputs([web, plate] + list(windows.values()))
    ascii_paths([web, plate, d] + list(windows.values()))
    cmd = [ctx.python, os.path.join(TOOL, "prep", "weld_prep.py"), "--web", web, "--plate", plate,
           "--leg", repr(c["bead"]["leg_mm"]), "--spacing", repr(c["bead"]["section_spacing_mm"]),
           "--snap-tol", repr(c["bead"].get("micro_snap_mm", 0.01)), "--points", str(c["bead"].get("points", 23)),
           "--out", d, "--hmbatch", ctx.cfg["hmbatch"]]
    edge = c.get("weld_edge", {})
    cmd += ["--edge-mode", edge.get("mode", "planar"), "--edge-axis", edge.get("axis", "z"),
            "--edge-axis-value", repr(edge.get("axis_value_mm", 0.0)), "--edge-center", ",".join(str(x) for x in edge.get("center_mm", [0, 0, 0])),
            "--section-frame", c.get("bead", {}).get("section_frame", "web_plane")]
    if c.get("bead", {}).get("section_frame", "web_plane") == "web_plane":
        normal, along, fsrc = prep_frame(ctx)
        m.data["frame"] = {"normal": normal, "along": along, "source": fsrc}
        if [float(v) for v in normal] != [1.0, 0.0, 0.0] or [float(v) for v in along] != [0.0, 0.0, 1.0]:
            # "--x=value": a direction may start with a minus sign, which argparse would read as an option
            cmd += ["--normal=" + ",".join(repr(float(v) + 0.0) for v in normal),
                    "--along=" + ",".join(repr(float(v) + 0.0) for v in along)]
    if edge.get("radius_mm") is not None:
        cmd += ["--edge-radius", repr(edge["radius_mm"])]
    if edge.get("closed"):
        cmd.append("--closed")
    for s in c["sides"]:
        cmd += ["--side", "%s:%s" % (s["label"], s["side"]), "--window", "%s=%s" % (s["label"], windows[s["label"]])]
    if c.get("window_expected_length_mm") is not None:
        sources = {s.get("line_source", "outer") for s in c["sides"]}
        cmd += ["--expect-len", repr(c["window_expected_length_mm"]), "--expected-line", sources.pop() if len(sources) == 1 else "outer"]
    log = os.path.join(d, "weld_prep_console.log")
    rc = run_cmd(cmd, log)
    m.command(cmd, rc, log)
    summ = json.load(io.open(os.path.join(d, "weld_prep_summary.json"), encoding="utf-8")) if os.path.isfile(os.path.join(d, "weld_prep_summary.json")) else {}
    for k, v in summ.get("checks", {}).items():
        m.check(k, v["ok"], v["detail"])
    m.finish(rc, list_outputs(d))
    if rc:
        raise StepFailed("prep failed (exit %d)" % rc, log)


def step_build(ctx):
    c = ctx.case
    prep = require_step_ok(ctx, "prep")
    d = fresh_step(ctx, "build")
    m = Manifest(d, "build")
    m.data["preflight"] = ctx.preflight
    m.save()
    P = ctx.step_dir("prep")
    labels = [s["label"] for s in c["sides"]]
    beads = {l: os.path.join(P, "bead_%s.bdf" % l) for l in labels}
    side_by_label = {s["label"]: s for s in c["sides"]}
    lines = {l: os.path.join(P, ("wl_root_short_%s.csv" if side_by_label[l].get("line_source", "outer") == "root" else "wl_outer_%s.csv") % l) for l in labels}
    # R13: bead + weld line must be the ones produced by this case's prep run (same hashes as recorded there)
    for f in list(beads.values()) + list(lines.values()):
        rel = os.path.relpath(f, P)
        if rel not in prep["outputs"]:
            raise StepFailed("R13: %s is not an output of 01_prep" % f)
    pxml = ctx.p(c["process"]["parameters_xml"])
    ok7, nblk = check_cooling_step(pxml)
    m.check("hard rule 7 cooling max step not automatic", ok7, {"cooling_blocks": nblk})
    if not ok7:
        m.finish(1, [])
        raise StepFailed("hard rule 7: cooling maximum time step is automatic in %s" % pxml)
    constraints = c.get("constraints", {"mode": "fixed_nodes", "fixed_nodes_csv": c.get("fixed_nodes_csv")})
    constraint_files = []
    if constraints.get("mode") == "fixed_nodes":
        constraint_files = [ctx.p(constraints.get("fixed_nodes_csv") or c["fixed_nodes_csv"])]
    else:
        constraint_files = [ctx.p(x["geometry"]) for x in constraints.get("items", [])]
    web_bdf, plate_bdf, _ = base_bdfs(ctx)
    inputs = [web_bdf, plate_bdf, ctx.p(c["materials"]["base_xmt"]), ctx.p(c["materials"]["filler_xmt"]),
              ctx.p(c["heat_source"]["xml"]), pxml] + constraint_files + list(beads.values()) + list(lines.values())
    m.inputs(inputs)
    params_xml = set_end_time(pxml, os.path.join(d, "process_parameters_build.xml"), c["run"]["end_time_s"])
    params = {
        "out_dir": d, "project_name": c["case_name"], "process_name": "Proc-" + c["case_name"], "gravity": c.get("gravity", [0, -9.80665, 0]),
        "temperature": c["temperature"], "material_base": ctx.p(c["materials"]["base_xmt"]), "material_filler": ctx.p(c["materials"]["filler_xmt"]),
        "web_bdf": web_bdf, "plate_bdf": plate_bdf, "web_name": c["base"]["web_name"], "plate_name": c["base"]["plate_name"],
        "base_order": c["base"].get("order", ["web", "plate"]),
        "heat_source_xml": ctx.p(c["heat_source"]["xml"]), "heat_source_entity": c["heat_source"]["entity_name"],
        "sides": [{"label": l, "bead_bdf": beads[l], "outer_line_csv": lines[l], "robot_name": "Rob-" + l, "weld_line_name": "wl_outer_" + l} for l in labels],
        "orientation_search_radius_mm": c["process"].get("orientation_search_radius_mm", 5.0),
        "constraints": constraints, "fixed_nodes_csv": constraint_files[0] if constraints.get("mode") == "fixed_nodes" else None,
        "fixed_search_radius_mm": c["process"].get("fixed_search_radius_mm", 0.01),
        "process_parameters_xml": params_xml, "end_time_s": c["run"]["end_time_s"],
        "solver_domains": c.get("solver", {}).get("domains", ctx.cfg["solver_domains"]),
        "solver_threads_per_domain": c.get("solver", {}).get("threads_per_domain", ctx.cfg["solver_threads_per_domain"]),
    }
    if constraints.get("mode") != "fixed_nodes":
        params["constraints"] = dict(constraints, items=[dict(x, geometry=ctx.p(x["geometry"])) for x in constraints.get("items", [])])
    ascii_paths(inputs + [d])
    rc, log, pj = runscript(ctx, os.path.join(TOOL, "build", "build_project.py"), params, d, "build_project")
    m.command([ctx.cfg["runscript"], "build_project.py", pj], rc, log)
    res_path = os.path.join(d, "build_result.json")
    res = json.load(io.open(res_path, encoding="utf-8")) if os.path.isfile(res_path) else {}
    if res.get("phase") != "saved":
        m.finish(1, list_outputs(d))
        raise StepFailed("build_project did not save: %s" % (res.get("error") or "no build_result.json"), log if not res else res_path)
    m.check("R6'/R17/R18 trajectory_modification blocks", all(res["trajectory_modification_present"]), res["trajectory_modification_present"])
    counts = res.get("fixed_node_counts", {})
    m.check("constraints created", (all(v == 1 for v in counts.values()) if constraints.get("mode") == "fixed_nodes" else res.get("tooling_count") == len(constraints.get("items", []))),
            counts if constraints.get("mode") == "fixed_nodes" else res.get("tooling_count"))
    m.check("R16 heat source entity", res["heat_source_name"] == c["heat_source"]["entity_name"], res["heat_source_name"])
    project = os.path.join(d, c["case_name"])
    proc = find_proc_dir(project)
    # patch trajectory settings (R4 connect_to_nodes etc.)
    sys.path.insert(0, os.path.join(TOOL, "build"))
    import patch_trajectory
    plog = os.path.join(d, "patch_trajectory.log")
    lines_out = []
    try:
        after = patch_trajectory.patch(proc, {"Rob-" + s["label"]: s["trajectory"] for s in c["sides"]}, log=lines_out.append)
        io.open(os.path.join(d, "robots_decoded_after_patch.xml"), "w", encoding="utf-8").write("\n<!-- robot -->\n".join(after))
        prc = 0
    except Exception as exc:
        lines_out.append("!! %r" % exc)
        prc = 1
    io.open(plog, "w", encoding="utf-8").write("\n".join(lines_out) + "\n")
    m.command(["patch_trajectory.patch", proc], prc, plog)
    if prc:
        m.finish(1, list_outputs(d))
        raise StepFailed("trajectory patch failed", plog)
    # contact table from rule set (R22)
    props = [f for f in os.listdir(proc) if f.endswith("_properties.xml") and f != "robots_properties.xml"][0]
    ptxt = io.open(os.path.join(proc, props), encoding="utf-8").read()
    comps = dict(re.findall(r'<component>\s*<name display_name="([^"]*)" internal_name="([^"]*)"/>', ptxt))
    robots = [re.search(r'<name display_name="([^"]*)" internal_name="([^"]*)"/>', x).groups() for x in robot_blobs(os.path.join(proc, "robots_properties.xml"))]
    from common import contact
    try:
        cdec = contact.resolve(c, contact.load_plan(ctx.out))
    except ValueError as exc:
        m.finish(1, list_outputs(d))
        raise StepFailed("contact: %s" % exc)
    m.data["contact"] = cdec
    cmd = [ctx.python, os.path.join(TOOL, "build", "contact_table_gen.py"), proc, "--ruleset", contact.ruleset_path(cdec["ruleset"]),
           "--web", "%s|%s" % (c["base"]["web_name"], comps[c["base"]["web_name"]]), "--plate", "%s|%s" % (c["base"]["plate_name"], comps[c["base"]["plate_name"]])]
    for dn, inn in robots:
        cmd += ["--torch", "%s|%s" % (dn, inn)]
    if ctx.ref and ctx.ref.get("contact_table_xml"):
        cmd += ["--compare", ctx.p(ctx.ref["contact_table_xml"])]
        for k, v in ctx.ref["contact_name_map"].items():
            cmd += ["--map", "%s=%s" % (k, v)]
    clog = os.path.join(d, "contact_table_gen.log")
    crc = run_cmd(cmd, clog)
    m.command(cmd, crc, clog)
    ctext = io.open(clog, encoding="utf-8", errors="replace").read()
    contact_ok = crc == 0 and ("user_defined=true" in ctext or "process_default=true" in ctext)
    m.check("R22 contact ruleset applied", contact_ok,
            [l for l in ctext.splitlines() if "differences" in l or "text diff" in l])
    ok = contact_ok
    m.finish(0 if ok else 1, list_outputs(d))
    if not ok:
        raise StepFailed("contact table generation failed", clog)


def write_dat(ctx, step, d, m, end_time, tag):
    c = ctx.case
    src = os.path.join(ctx.step_dir("build"), c["case_name"])
    copy = copy_project(src, os.path.join(d, "copy_" + tag))
    pxml = set_end_time(ctx.p(c["process"]["parameters_xml"]), os.path.join(d, "process_parameters_%s.xml" % tag), end_time)
    dat = os.path.join(d, "dat_%s.dat" % tag)
    res_json = os.path.join(d, "write_input_%s.json" % tag)
    params = {"copy_dir": copy, "process_parameters_xml": pxml, "dat_out": dat, "result_json": res_json,
              "solver_domains": c.get("solver", {}).get("domains", ctx.cfg["solver_domains"]),
              "solver_threads_per_domain": c.get("solver", {}).get("threads_per_domain", ctx.cfg["solver_threads_per_domain"])}
    rc, log, pj = runscript(ctx, os.path.join(TOOL, "build", "write_input.py"), params, d, "write_input_" + tag)
    m.command([ctx.cfg["runscript"], "write_input.py", pj], rc, log)
    res = json.load(io.open(res_json, encoding="utf-8")) if os.path.isfile(res_json) else {}
    if res.get("phase") != "done" or not os.path.isfile(dat):
        m.finish(1, list_outputs(d))
        raise StepFailed("write_program_input failed (%s)" % tag, log if not res else res_json)
    m.data["check_model_%s" % tag] = res.get("check_model")
    return copy, dat


def gate_dat(ctx, d, m, dat, copy, tag, allow=None):
    if not ctx.ref:
        m.check("gate %s" % tag, True, "no reference in case.json, gate comparison skipped")
        return 0, None
    c = ctx.case
    out = os.path.join(d, "gate_%s.txt" % tag)
    cmd = [ctx.python, os.path.join(TOOL, "gate", "check_short_v3.py"), dat, ctx.p(ctx.ref["dat"]),
           "--proc", find_proc_dir(copy), "--proc-ref", ctx.p(ctx.ref["proc_dir"]), "--work", os.path.join(d, "work_" + tag)]
    if c["gate"].get("independent_bead"):
        cmd.append("--independent-bead")
    if allow:
        cmd += ["--allow", allow]
    rc = run_cmd(cmd, out)
    with io.open(out, "a", encoding="utf-8") as f:
        f.write("exit=%d\n" % rc)
    concl = [l for l in io.open(out, encoding="utf-8", errors="replace").read().splitlines() if l.startswith("结论")]
    m.command(cmd, rc, out)
    m.check("gate %s (R11/R15/R20/R23)" % tag, rc == 0, concl[-1] if concl else None)
    return rc, out


def step_gate(ctx):
    require_step_ok(ctx, "build")
    d = fresh_step(ctx, "gate")
    m = Manifest(d, "gate")
    m.inputs([ctx.p(ctx.ref["dat"]), ctx.p(ctx.ref["proc_dir"])] if ctx.ref else [])
    copy, dat = write_dat(ctx, "gate", d, m, ctx.case["gate"]["end_time_s"], "%gs" % ctx.case["gate"]["end_time_s"])
    rc, out = gate_dat(ctx, d, m, dat, copy, "%gs" % ctx.case["gate"]["end_time_s"])
    m.finish(rc, list_outputs(d))
    if rc:
        raise StepFailed("gate failed (exit %d)" % rc, out)


def step_gate_self(ctx):
    """03_gate without Simufact: .dat assembled from 01_prep (common/selfdat.py), check_short_v3 against itself."""
    from common.selfdat import write_self_dat
    require_step_ok(ctx, "prep")
    c = ctx.case
    d = fresh_step(ctx, "gate")
    m = Manifest(d, "gate")
    P = ctx.step_dir("prep")
    labels = [s["label"] for s in c["sides"]]
    beads = [(l, os.path.join(P, "bead_%s.bdf" % l)) for l in labels]
    lines = {l: os.path.join(P, "wl_outer_%s.csv" % l) for l in labels}
    base = list(base_bdfs(ctx)[:2])
    m.inputs(base + [b for _, b in beads] + list(lines.values()))
    dat = os.path.join(d, "dat_self.dat")
    m.data["self_dat"] = write_self_dat(dat, base, beads, lines, c["gate"]["end_time_s"], c["gate"].get("weld_step_s", 0.4))
    out = os.path.join(d, "gate_self.txt")
    cmd = [ctx.python, os.path.join(TOOL, "gate", "check_short_v3.py"), dat, dat, "--work", os.path.join(d, "work_self")]
    if c["gate"].get("independent_bead"):
        cmd.append("--independent-bead")
    rc = run_cmd(cmd, out)
    with io.open(out, "a", encoding="utf-8") as f:
        f.write("exit=%d\n" % rc)
    concl = [l for l in io.open(out, encoding="utf-8", errors="replace").read().splitlines() if l.startswith("结论")]
    m.command(cmd, rc, out)
    m.check("gate self (check_short_v3, new = ref)", rc == 0, concl[-1] if concl else None)
    m.finish(rc, list_outputs(d))
    if rc:
        raise StepFailed("gate --self failed (exit %d)" % rc, out)


def run_gate(ctx):
    """LC1: regenerate the review, then refuse a formal run while anything on it blocks."""
    lc = _life()
    status, summary = step_review(ctx)
    lc.record(ctx.out, ctx.case_path, "review", status, summary)
    blockers = lc.run_blockers(ctx.out, ctx.case_path)
    if blockers:
        raise StepFailed("formal run refused (LC1); see the review page %s: %s"
                         % (os.path.join(ctx.out, "dashboard", "index.html"), "; ".join(blockers)))


def step_run(ctx):
    run_gate(ctx)
    require_step_ok(ctx, "gate")
    c = ctx.case
    d = fresh_step(ctx, "run")
    m = Manifest(d, "run")
    tag = "%gs" % c["run"]["end_time_s"]
    copy, dat = write_dat(ctx, "run", d, m, c["run"]["end_time_s"], tag)
    rc, out = gate_dat(ctx, d, m, dat, copy, tag, allow="AUTO_STEP")      # R15 short run
    if rc:
        m.finish(rc, list_outputs(d))
        raise StepFailed("run gate (--allow AUTO_STEP) failed", out)
    res_json = os.path.join(d, "start_analysis.json")
    params = {"copy_dir": copy, "gated_dat": dat, "result_json": res_json, "backup_dir": os.path.join(d, "backup_before_start")}
    t0 = time.time()
    rc, log, pj = runscript(ctx, os.path.join(TOOL, "run", "start_analysis.py"), params, d, "start_analysis")
    m.command([ctx.cfg["runscript"], "start_analysis.py", pj], rc, log)
    res = json.load(io.open(res_json, encoding="utf-8")) if os.path.isfile(res_json) else {}
    proc = find_proc_dir(copy)
    sts = glob.glob(os.path.join(proc, "_Run_", "*.sts"))
    ex = read_sts_exit(sts[0]) if sts else None                     # hard rule 6: judge by .sts only
    if sts:
        sys.path.insert(0, os.path.join(TOOL, "run"))
        import solver_stats
        outs = sorted(glob.glob(os.path.join(proc, "_Run_", "*.out")))    # one per solver domain, all scanned
        st = solver_stats.stats(sts[0], outs)
        m.data["solver_stats"] = st
        io.open(os.path.join(d, "solver_stats.txt"), "w", encoding="utf-8").write(solver_stats.format_stats(st) + "\n")
        m.check("all load cases finished (progress 100%)", (st.get("final_progress_percent") or 0) >= 99.99,
                {k: st.get(k) for k in ("final_progress_percent", "final_time_s", "increments", "retried_increments")})
        # warning, not a failure: a solver that recovered from these still finished, and the step must not reject a
        # run Simufact considers complete (docs/validation.md)
        m.check("solver ran clean (no inverted element, no retried increment)", bool(st.get("clean")),
                {"warnings": st.get("warnings"), "inverted_element_ids": st.get("inverted_element_ids"), "out": st.get("out")})
    m.data["solver"] = {"sts": sts[0] if sts else None, "exit": ex, "wall_s": round(time.time() - t0, 1), "proc_dir": proc,
                        "run_dat_equals_gated_dat_except_date": res.get("run_dat_equals_gated_dat_except_date")}
    ok = bool(ex) and ex[1] == 3004
    m.check("hard rule 6 .sts exit number 3004", ok, ex)
    m.check("solver used the gated .dat", res.get("run_dat_equals_gated_dat_except_date") is True, res.get("run_dat_equals_gated_dat_except_date"))
    st = m.data.get("solver_stats") or {}
    ok = ok and res.get("run_dat_equals_gated_dat_except_date") is True and \
        (not st or (st.get("final_progress_percent") or 0) >= 99.99)
    m.finish(0 if ok else 1, [o for o in list_outputs(d) if "_Results_" not in o])
    if not ok:
        raise StepFailed("solver did not end with exit 3004: %s" % (ex,), sts[0] if sts else log)


def step_compare(ctx):
    run = require_step_ok(ctx, "run")
    c = ctx.case
    if not ctx.ref:
        raise StepFailed("compare needs reference results in case.json")
    d = fresh_step(ctx, "compare")
    m = Manifest(d, "compare")
    sides = []
    for s in c["sides"]:
        r = ctx.ref["results"][s["label"]]
        sides.append({"label": s["label"], "new_body": "Rob-" + s["label"], "reference_arc": ctx.p(r["arc"]), "reference_bead_key": r["bead_key"],
                      "window_start_point_mm": list(read_weldline_csv(ctx.p(s["window_csv"]))[0])})
    params = {"out_dir": d, "arctool": ctx.cfg["arctool"], "new_proc_dir": run["solver"]["proc_dir"], "reference_sections_json": ctx.p(ctx.ref["bead_sections_json"]),
              "weld_speed_mm_s": c["process"]["weld_speed_mm_s"], "end_time_s": c["run"]["end_time_s"],
              "limit_percent": c["run"]["temperature_limit_percent"], "sides": sides}
    m.inputs([ctx.p(ctx.ref["bead_sections_json"])] + [x["reference_arc"] for x in sides])
    pj = os.path.join(d, "compare_params.json")
    io.open(pj, "w", encoding="utf-8").write(json.dumps(params, indent=1) + "\n")
    log = os.path.join(d, "compare_temperature.log")
    rc = run_cmd([ctx.python, os.path.join(TOOL, "run", "compare_temperature.py"), pj], log)
    m.command(["compare_temperature.py", pj], rc, log)
    tj = os.path.join(d, "temperature_comparison.json")
    if os.path.isfile(tj):
        for lab, v in json.load(io.open(tj)).get("welds", {}).items():
            m.check("R20 peak temperature diff < %g%% %s" % (c["run"]["temperature_limit_percent"], lab), v["pass_lt_limit"],
                    {"section": v["mid_section_index"], "ds_mm": v["mid_section_ds_mm"], "new_K": v["peak_new_K"], "ref_K": v["peak_ref_K"], "diff_percent": v["diff_percent"]})
    m.finish(rc, list_outputs(d))
    if rc:
        raise StepFailed("temperature comparison failed", log)


def step_post(ctx):
    """Result extraction for this model alone: probe thermal cycles by coordinate, t8/5, final mechanical values."""
    run = require_step_ok(ctx, "run")
    c = ctx.case
    if not (c.get("post") or {}).get("probes"):
        raise StepFailed("post needs post.probes in case.json (probes are physical coordinates, see case_schema.md)")
    d = fresh_step(ctx, "post")
    m = Manifest(d, "post")
    proc = run["solver"]["proc_dir"]
    outs = sorted(glob.glob(os.path.join(proc, "_Run_", "*.out")))
    params = {"out_dir": d, "arctool": ctx.cfg["arctool"], "proc_dir": proc, "sts": run["solver"]["sts"],
              "out_files": outs, "probes": c["post"]["probes"],
              "stride": c["post"].get("stride", 1), "increments": c["post"].get("increments"),
              "final_values": c["post"].get("final_values"),
              "keep_csv": bool(c["post"].get("keep_csv"))}
    pj = os.path.join(d, "post_params.json")
    io.open(pj, "w", encoding="utf-8").write(json.dumps(params, indent=1) + "\n")
    log = os.path.join(d, "post.log")
    rc = run_cmd([ctx.python, os.path.join(TOOL, "run", "post.py"), pj], log)
    m.command(["post.py", pj], rc, log)
    pjson = os.path.join(d, "post.json")
    if os.path.isfile(pjson):
        data = json.load(io.open(pjson, encoding="utf-8"))
        m.data["post"] = {"increments_exported": len(data["increments_exported"]), "time_source": data["time_source"],
                          "points_not_represented": data.get("points_not_represented", []),
                          "probes": {p["resolution"]["name"]: {"distance_mm": p["resolution"]["distance_mm"],
                                                               "comparable_across_meshes": p["resolution"]["comparable_across_meshes"],
                                                               "peak_K": p["metrics"].get("peak_K"),
                                                               "t85_s": p["metrics"].get("t85_s"),
                                                               "status": p["metrics"].get("status")} for p in data["probes"]}}
        for p in data["probes"]:
            r = p["resolution"]
            m.check("mesh represents the point of probe %s (nearest node within %g mm)" % (r["name"], r["max_distance_mm"]),
                    r["within"], {"distance_mm": r["distance_mm"], "node_index": r["node_index"], "node_mm": r["node_mm"],
                                  "verdict": r["verdict"]})
        m.check("increment times available (.sts)", data["time_source"] == "sts", data["time_source"])
    m.finish(rc, list_outputs(d))
    if rc:
        raise StepFailed("post failed", log)


def update_selfcheck_manifest(out_dir, name, rc, log, duration_s):
    """Maintain the sixth-step manifest while regress/test run independently."""
    path = os.path.join(out_dir, "manifest.json")
    if os.path.isfile(path):
        data = json.load(io.open(path, encoding="utf-8"))
    else:
        data = {"step": "selfcheck", "started": time.strftime("%Y-%m-%d %H:%M:%S"), "commands": [], "checks": {}}
    data["commands"] = [x for x in data.get("commands", []) if x.get("name") != name]
    data["commands"].append({"name": name, "exit_code": rc, "log": log})
    data.setdefault("checks", {})[name] = {"ok": rc == 0, "detail": log}
    data.setdefault("durations", {})[name] = round(duration_s, 2)
    data["duration_s"] = round(sum(data["durations"].values()), 2)
    data["exit_code"] = 0 if data["checks"] and all(x.get("ok") for x in data["checks"].values()) else 1
    data["outputs"] = list_outputs(out_dir)
    data["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    io.open(path, "w", encoding="utf-8", newline="\n").write(json.dumps(data, ensure_ascii=False, indent=1) + "\n")


def step_regress(out_dir):
    log = os.path.join(out_dir, "run_regressions.log")
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    rc = run_cmd([sys.executable, os.path.join(TOOL, "gate", "run_regressions.py"), "--out", os.path.join(out_dir, "regressions")], log)
    update_selfcheck_manifest(out_dir, "regress", rc, log, time.time() - t0)
    if rc:
        raise StepFailed("regressions not consistent (exit %d)" % rc, log)


def step_test(out_dir):
    log = os.path.join(out_dir, "unit_tests.log")
    os.makedirs(out_dir, exist_ok=True)
    t0 = time.time()
    rc = run_cmd([sys.executable, "-m", "unittest", "discover", "-s", os.path.join(TOOL, "tests"), "-t", TOOL, "-v"], log, cwd=TOOL)
    update_selfcheck_manifest(out_dir, "test", rc, log, time.time() - t0)
    if rc:
        raise StepFailed("unit tests failed (exit %d)" % rc, log)


def preflight(case_path, config_path):
    cfg = yaml.safe_load(io.open(config_path, encoding="utf-8"))
    results = run_preflight(case_path, work_root=cfg.get("work_root"))
    print(format_results(results, case_path), flush=True)
    return results


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "suggest":
        from library.suggest import run as suggest_run
        return suggest_run(sys.argv[2:])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["new", "status", "preflight"] + list(LIFE_STEPS) + list(STEPS)
                    + ["regress", "test", "all"])
    ap.add_argument("case", nargs="?")
    cfg_default = os.path.join(TOOL, "config.yaml")
    ap.add_argument("--config", default=cfg_default if os.path.isfile(cfg_default) else os.path.join(TOOL, "config.example.yaml"))
    ap.add_argument("--skip-preflight", action="store_true", help="build/all: do not run preflight (recorded in 02_build/manifest.json)")
    ap.add_argument("--self", dest="self_check", action="store_true", help="gate: check a .dat assembled from 01_prep against itself (no Simufact)")
    ap.add_argument("--step", help="new: STEP file of the part")
    ap.add_argument("--name", help="new: case name (default: file name)")
    a = ap.parse_args()
    try:
        if a.command == "new":
            if not a.case:
                ap.error("case.json required")
            cmd_new(a.case, a.step, a.name)
            return 0
        if a.command == "preflight":
            if not a.case:
                ap.error("case.json required")
            return 1 if summarize(preflight(a.case, a.config))["block"] else 0
        if a.command in ("regress", "test") and not a.case:
            out = os.path.join(TOOL, "selfcheck_out")
            (step_regress if a.command == "regress" else step_test)(out)
            print("%s: OK" % a.command)
            return 0
        if not a.case:
            ap.error("case.json required")
        ctx = Ctx(a.case, a.config)
        if a.command in ("all", "build"):
            if a.skip_preflight:
                ctx.preflight = {"skipped": True, "reason": "--skip-preflight", "time": time.strftime("%Y-%m-%d %H:%M:%S")}
                print("== preflight SKIPPED (--skip-preflight)", flush=True)
            else:
                print("== preflight ...", flush=True)
                res = preflight(a.case, a.config)
                n = summarize(res)
                ctx.preflight = {"skipped": False, "summary": n, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                                 "results": [dict(r._asdict(), value=None if r.value is None else str(r.value)) for r in res]}
                if n["block"]:
                    raise StepFailed("preflight: %d block item(s); nothing was built" % n["block"])
                print("== preflight OK", flush=True)
        ascii_paths([ctx.out])
        if a.command == "status":
            lc = _life()
            st = lc.load(ctx.out)
            for name in lc.STAGES:
                e = st["stages"].get(name)
                print("%-11s %-7s %s" % (name, e["status"] if e else "-", (e or {}).get("summary") or ""))
            b = lc.run_blockers(ctx.out, ctx.case_path)
            print("formal run: %s" % ("allowed" if not b else "BLOCKED: " + "; ".join(b)))
            return 0 if not b else 1
        if a.command in LIFE_STEPS:
            lc = _life()
            fn = {"inspect": step_inspect, "plan": step_plan, "provenance": step_provenance,
                  "review": step_review}[a.command]
            status, summary = fn(ctx)
            lc.record(ctx.out, ctx.case_path, a.command, status, summary)
            if a.command != "review":
                refresh_review(ctx)
            print("== %s %s: %s" % (a.command, status, summary), flush=True)
            return 1 if status == "BLOCK" else 0
        fns = {"hm": step_hm, "prep": step_prep, "build": step_build, "gate": step_gate_self if a.self_check else step_gate,
               "run": step_run, "compare": step_compare, "post": step_post}
        # all: hm and post only when the case carries the block they need (step_hm also skips without hmbatch)
        optional = {"hm": ("base_mesh", "explicit_web"), "post": ("post",)}
        if a.command == "all" and not ctx.ref:
            # all ends in the input check against a reference case and the temperature comparison with it. Without a
            # reference there is nothing to compare against, and quietly running gate --self instead would look like
            # a pass while the case is only compared with itself.
            raise StepFailed("all needs a reference case (case.json `reference`): gate and compare have nothing to "
                             "compare against. Without one, run the steps that do not need it: prep, gate --self, "
                             "build, run, post.")
        cad = ctx.case.get("role") != "regression" and (ctx.case.get("geometry") or {}).get("step")
        order = [s for s in STEPS if s not in optional or any(ctx.case.get(k) for k in optional[s])
                 or (s == "hm" and cad)] if a.command == "all" else \
            [a.command] if a.command in fns else []
        lc = _life()
        if a.command == "all":
            for st in ("inspect", "plan", "provenance", "review"):
                status, summary = {"inspect": step_inspect, "plan": step_plan, "provenance": step_provenance,
                                   "review": step_review}[st](ctx)
                lc.record(ctx.out, ctx.case_path, st, status, summary)
                print("== %s %s: %s" % (st, status, summary), flush=True)
                if status == "BLOCK":
                    raise StepFailed("%s is BLOCK; see %s" % (st, os.path.join(ctx.out, "dashboard", "index.html")))
        for s in order:
            t0 = time.time()
            print("== %s ..." % s, flush=True)
            try:
                fns[s](ctx)
            except StepFailed as exc:
                if s in lc.STAGES:
                    lc.record(ctx.out, ctx.case_path, s, "BLOCK", str(exc)[:500])
                if s == "hm":
                    record_mesh_gate(ctx, lc)
                refresh_review(ctx)
                raise
            if s in lc.STAGES:
                lc.record(ctx.out, ctx.case_path, s, "PASS", "%.0f s" % (time.time() - t0))
            if s == "hm":
                record_mesh_gate(ctx, lc)
            refresh_review(ctx)
            print("== %s OK (%.0f s)" % (s, time.time() - t0), flush=True)
        if a.command in ("all", "regress"):
            print("== regress ...", flush=True)
            step_regress(os.path.join(ctx.out, "06_selfcheck"))
            print("== regress OK", flush=True)
        if a.command in ("all", "test"):
            print("== test ...", flush=True)
            step_test(os.path.join(ctx.out, "06_selfcheck"))
            print("== test OK", flush=True)
        return 0
    except StepFailed as e:
        print("!! FAILED: %s" % e, file=sys.stderr)
        if e.log:
            print("---- last 20 lines of %s ----" % e.log, file=sys.stderr)
            print(tail(e.log, 20), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
