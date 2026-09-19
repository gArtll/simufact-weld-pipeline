# -*- coding: utf-8 -*-
"""gate/run steps (runs under Simufact runscript): open a DISPOSABLE copy (hard rule 1), import process parameters,
check_model, write_program_input, save, copy the .dat out. No start_analysis. Copied from P0_A4 write_input.py; change:
all paths from params JSON (argv[1]): copy_dir, process_parameters_xml, dat_out, result_json, solver settings."""
import glob
import io
import json
import os
import shutil
import sys
import traceback

from simufact.welding import open_project

P = json.load(io.open(sys.argv[1], encoding="utf-8"))
R = {"params": sys.argv[1]}


def log(k, v):
    R[k] = v
    io.open(P["result_json"], "w", encoding="utf-8").write(json.dumps(R, ensure_ascii=False, indent=2) + "\n")


try:
    for n in ("_Run_", "_log", "_particles", "_Results_", "_watch_tmp"):          # hard rule 2 / R9: copy must be clean
        hits = glob.glob(os.path.join(P["copy_dir"], "*", n))
        if hits:
            raise RuntimeError("copy is not clean, found %s" % hits)
    proj = open_project(glob.glob(os.path.join(P["copy_dir"], "*.swproj"))[0])
    proc = proj.all_processes[0]
    pp = proc.process_parameters
    pp.import_all_settings(P["process_parameters_xml"])
    pp.parallelization.on = True
    pp.parallelization.num_domains = P["solver_domains"]
    pp.parallelization.num_cores_per_domain = P["solver_threads_per_domain"]
    log("end_time", str(pp.time_control.analysis_end_time))
    log("check_model", str(proc.check_model()))          # R4 / R10: "nodal connection not verified" and fixed-step warnings are expected
    proc.write_program_input()
    proj.save()
    d = glob.glob(os.path.join(str(proc.path), "_Run_", "*.dat"))
    log("process_path", str(proc.path))
    shutil.copy2(d[0], P["dat_out"])
    log("phase", "done")
except Exception:
    log("phase", "failed")
    log("error", traceback.format_exc())
    raise
