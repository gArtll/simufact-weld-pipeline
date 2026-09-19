# -*- coding: utf-8 -*-
"""run step (runs under Simufact runscript). Copied from P0_A4 start_20s.py; change: paths from params JSON.
Starts the analysis on the gated copy without changing settings. R9: _Results_ / _watch_tmp moved to a backup first.
Hard rule 6: success is judged ONLY from the .sts exit number (recorded here, judged by weldsim.py)."""
import glob
import hashlib
import io
import json
import os
import shutil
import sys
import time
import traceback

from simufact.welding import open_project

P = json.load(io.open(sys.argv[1], encoding="utf-8"))
R = {"params": sys.argv[1]}


def log(k, v):
    R[k] = v
    io.open(P["result_json"], "w", encoding="utf-8").write(json.dumps(R, ensure_ascii=False, indent=2) + "\n")


def body_hash(p):
    L = io.open(p, encoding="latin-1").read().split("\n")
    return hashlib.sha256("\n".join(s for s in L if not s.startswith("$ Date of creation")).encode("latin-1")).hexdigest()


try:
    COPY = P["copy_dir"]
    proc_dir = [d for d in glob.glob(os.path.join(COPY, "*")) if os.path.isfile(os.path.join(d, "robots_properties.xml"))][0]
    moved = []
    for n in ("_Results_", "_watch_tmp"):
        if os.path.exists(os.path.join(proc_dir, n)):
            bk = os.path.join(P["backup_dir"], time.strftime("%H%M%S"))
            os.makedirs(bk, exist_ok=True)
            shutil.move(os.path.join(proc_dir, n), os.path.join(bk, n))
            moved.append(n)
    log("moved_to_backup", moved)
    log("gated_dat_body_sha256", body_hash(P["gated_dat"]))
    proj = open_project(glob.glob(os.path.join(COPY, "*.swproj"))[0])
    proc = proj.all_processes[0]
    log("end_time", str(proc.process_parameters.time_control.analysis_end_time))
    a = proc.start_analysis()
    log("started", repr(a))
    log("wait_return", repr(a.wait()))
    proj.save()
    run_dat = glob.glob(os.path.join(proc_dir, "_Run_", "*.dat"))[0]
    log("run_dat_equals_gated_dat_except_date", body_hash(run_dat) == body_hash(P["gated_dat"]))
    sts = glob.glob(os.path.join(proc_dir, "_Run_", "*.sts"))
    ex = [(k, s.strip()) for k, s in enumerate(io.open(sts[0], encoding="latin-1"), 1) if "exit number" in s] if sts else []
    log("sts", sts[0] if sts else None)
    log("sts_exit", ex[-1] if ex else None)
    log("proc_dir", proc_dir)
    log("phase", "wait_returned")
except Exception:
    log("phase", "failed")
    log("error", traceback.format_exc())
    raise
