# -*- coding: utf-8 -*-
"""00_hm runner: generate Tcl -> hmbatch (plate, web) -> R1 BDF -> quality; rerun while the thresholds fail (at most
max_retries reruns). Every attempt is kept in attempt_<k>/ with the mesh values it used.
Retry by failure type (parameter sweep on the synthetic case: a smaller weld band size lowers the minimum scaled
Jacobian, a lower transition ratio with a wider weld band raises it):
  penta fraction above the limit     -> weld band size - shrink_step_mm
  scaled Jacobian below the limit    -> transition_ratio - 0.5 (not below 1.0); weld band width x 2 once"""
import copy
import hashlib
import io
import os
import shutil
import subprocess
import time

from hm import meshio, tcl_gen
from hm.params import normalize, surface_height, zones


def _sha(path):
    return hashlib.sha256(io.open(path, "rb").read()).hexdigest()


def _hmbatch(hmbatch, tcl, cwd):
    log = os.path.splitext(tcl)[0] + ".log"
    with io.open(log, "w", encoding="utf-8") as f:
        f.write("CMD %s -tcl %s\n" % (os.path.basename(hmbatch), os.path.basename(tcl)))
        f.flush()
        rc = subprocess.run([hmbatch, "-tcl", tcl], stdout=f, stderr=subprocess.STDOUT, cwd=cwd).returncode
    return rc, log


def run_hm(block, out_dir, hmbatch, log=print):
    p = normalize(block)
    q = p["quality"]
    surf = lambda z: surface_height(p, z)
    work = copy.deepcopy(p)
    band = float(work["mesh"]["weld_band_size_mm"])
    widened = False
    attempts, final = [], None
    for k in range(int(q["max_retries"]) + 1):
        ad = os.path.join(out_dir, "attempt_%d" % k)
        os.makedirs(ad, exist_ok=True)
        z = zones(work, band)
        rec = {"attempt": k, "weld_band_size_mm": band, "transition_ratio": float(work["mesh"]["transition_ratio"]),
               "weld_band_width_mm": float(work["mesh"]["weld_band_width_mm"]), "web_first_row_mm": z["web_first_row_mm"],
               "transition_size_mm": z["transition_size_mm"], "plate_split_planes_x": z["plate_planes"], "web_split_planes_y": z["web_planes"]}
        ok_all = True
        for role, gen in (("plate", tcl_gen.plate_tcl), ("web", tcl_gen.web_tcl)):
            tcl = os.path.join(ad, role + ".tcl")
            text = gen(work, band)
            io.open(tcl, "w", encoding="ascii", newline="\n").write(text)
            rules = tcl_gen.check_all(text)
            t0 = time.time()
            rc, hlog = _hmbatch(hmbatch, tcl, ad)
            raw = os.path.join(ad, role + "_raw.txt")
            r = {"tcl_sha256": _sha(tcl), "tcl_rules": {n: v["ok"] for n, v in rules.items()}, "hmbatch_exit": rc,
                 "hmbatch_s": round(time.time() - t0, 2), "log": os.path.relpath(hlog, out_dir)}
            if rc != 0 or not os.path.isfile(raw) or not all(v["ok"] for v in rules.values()):
                r["error"] = "hmbatch exit %d, raw dump %s, tcl rules %s" % (rc, os.path.isfile(raw), r["tcl_rules"])
                rec[role] = r
                ok_all = False
                continue
            nodes, elems = meshio.parse_raw(raw)
            coords, mesh = meshio.build(nodes, elems, surf if float(p["plate"]["curvature_radius_mm"]) > 0 else None)
            bdf = os.path.join(ad, "base_%s.bdf" % role)
            meshio.write_bdf(bdf, coords, mesh, "%s, attempt %d" % (role, k))
            qs = meshio.quality(coords, mesh)
            lo, hi = meshio.layers(bdf)
            r.update(qs, nodes=len(coords), layers_min=lo, layers_max=hi, bdf_sha256=_sha(bdf))
            r["quality_ok"] = qs["tetra4"] == 0 and qs["penta_fraction"] <= float(q["max_penta_fraction"]) and \
                qs["min_scaled_jacobian"] is not None and qs["min_scaled_jacobian"] >= float(q["min_scaled_jacobian"])
            ok_all &= r["quality_ok"]
            rec[role] = r
        if ok_all:
            gap = meshio.contact_gap(os.path.join(ad, "base_web.bdf"), os.path.join(ad, "base_plate.bdf"), surf)
            rec["contact"] = gap
            ok_all = gap["gap_mm"] is not None and gap["gap_mm"] < 1e-6 and not gap["penetration"]
        rec["ok"] = bool(ok_all)
        attempts.append(rec)
        log("HM attempt %d band %.4g mm -> %s" % (k, band, "ok" if ok_all else "retry"))
        if ok_all:
            final = ad
            break
        if any(rec.get(role, {}).get("error") for role in ("plate", "web")):
            break                                           # hmbatch / rule failure: a parameter change does not help
        rs = [rec[role] for role in ("plate", "web") if role in rec]
        penta_fail = any(r["penta_fraction"] > float(q["max_penta_fraction"]) for r in rs)
        sj_fail = any(r["min_scaled_jacobian"] is None or r["min_scaled_jacobian"] < float(q["min_scaled_jacobian"]) for r in rs)
        changed = []
        if penta_fail:
            nb = round(band - float(q["shrink_step_mm"]), 9)
            if nb > 0:
                band = nb
                changed.append("weld_band_size_mm")
        if sj_fail:
            ratio = float(work["mesh"]["transition_ratio"])
            if ratio > 1.0:
                work["mesh"]["transition_ratio"] = max(1.0, round(ratio - 0.5, 9))
                changed.append("transition_ratio")
            if not widened:
                work["mesh"]["weld_band_width_mm"] = round(float(work["mesh"]["weld_band_width_mm"]) * 2.0, 9)
                widened = True
                changed.append("weld_band_width_mm")
        rec["next_change"] = changed
        if not changed:
            break
    res = {"params": p, "attempts": attempts, "final_attempt": None}
    if final:
        for role in ("plate", "web"):
            shutil.copyfile(os.path.join(final, "base_%s.bdf" % role), os.path.join(out_dir, "base_%s.bdf" % role))
        last = attempts[-1]
        res.update(final_attempt=last["attempt"], elements={"plate": last["plate"]["elements"], "web": last["web"]["elements"]},
                   plate_layers=last["plate"]["layers_min"], web_layers=last["web"]["layers_min"],
                   bdf_sha256={role: last[role]["bdf_sha256"] for role in ("plate", "web")},
                   tcl_sha256={role: last[role]["tcl_sha256"] for role in ("plate", "web")},
                   hmbatch_exit={role: last[role]["hmbatch_exit"] for role in ("plate", "web")})
    return res
